"""
Core obfuscation logic for CObfuscate.

This module transforms normal Python source code into a functionally equivalent
but extremely difficult to read and automatically reverse form.

The pipeline applies multiple layers of transformation:
  - Docstring removal
  - String literal encryption (Base64 + XOR, reinforced by a C backend)
  - Identifier renaming (scope‑aware, using highly confusable characters)
  - Constant obfuscation (simple literals -> complex arithmetic expressions)
  - Opaque predicate injection (always‑true conditions with dead code)
  - Control flow flattening (state machine with computed next‑state)
  - Junk code insertion (dead assignments / useless loops)
  - Anti‑debug tricks (sys.gettrace, __debug__ checks)
  - Operator obfuscation (rewriting binary ops as more complex forms)
"""

import os
import sys
import random
import builtins
import libcst as cst
from libcst import CSTNode, BaseExpression, SimpleString, Name, Arg, BinaryOperation
from pathlib import Path
from collections import defaultdict
from typing import Optional, List, Union, Dict, Tuple

# ----------------------------------------------------------------------
# Fallback pure‑Python string obfuscation (if C extension is unavailable)
# ----------------------------------------------------------------------
def _py_obfuscate_string_b64(input_str: str) -> Tuple[str, str]:
    """Pure‑Python equivalent of the C backend: XOR with random key + Base64."""
    import base64

    key_len = 16
    key = bytes(random.randint(0, 255) for _ in range(key_len))
    data = input_str.encode("utf-8")
    xor_data = bytes(b ^ key[i % key_len] for i, b in enumerate(data))
    enc_b64 = base64.b64encode(xor_data).decode("ascii")
    # Format key as a bytes literal that can be embedded directly in code
    key_repr = "b'" + "".join(f"\\x{b:02x}" for b in key) + "'"
    return enc_b64, key_repr

# Attempt to import the high‑performance C module
try:
    from .ext.obfuscate import obfuscate_string_b64
except ImportError:
    obfuscate_string_b64 = None

def _obfuscate_string(input_str: str) -> Tuple[str, str]:
    """Return (enc_b64, key_bytes_literal). Always succeeds (falls back to pure Python)."""
    if obfuscate_string_b64 is not None:
        try:
            return obfuscate_string_b64(input_str)
        except Exception:
            pass
    return _py_obfuscate_string_b64(input_str)


# ----------------------------------------------------------------------
# Decoder stub – will be obfuscated by later stages
# ----------------------------------------------------------------------
DECODER_FUNC_NAME = "_d"
DECODER_CODE = f"""
import base64 as __b64
def {DECODER_FUNC_NAME}(__e, __k):
    __d = __b64.b64decode(__e)
    return bytes(__d[__i] ^ __k[__i % len(__k)] for __i in range(len(__d))).decode('utf-8')
"""

# ----------------------------------------------------------------------
# Utility: generate visually confusing but valid Python identifiers
# ----------------------------------------------------------------------
def _mangled_name() -> str:
    """
    Returns a hard‑to‑read name using a wide mixture of ASCII letters,
    digits, underscore and Unicode homoglyphs. Always starts with a non‑digit.
    """
    # Extended pool: ASCII letters + Unicode letters (Greek, Cyrillic) + digits + underscore
    ascii_letters = [chr(c) for c in range(65, 91)] + [chr(c) for c in range(97, 123)]
    unicode_letters = [
        chr(0x03B1), chr(0x03B2), chr(0x03B3),  # α, β, γ
        chr(0x0430), chr(0x0431), chr(0x0432),  # а, б, в (Cyrillic)
        chr(0x03C0), chr(0x03C3),               # π, σ
    ]
    letters = ascii_letters + unicode_letters
    digits = [chr(c) for c in range(48, 58)]
    all_chars = letters + digits + ["_"]
    first_pool = letters + ["_"]
    first = random.choice(first_pool)
    length = random.randint(8, 24)
    return first + "".join(random.choices(all_chars, k=length - 1))


# ======================================================================
# Transformation stages (LibCST visitors)
# ======================================================================

class CommentRemover(cst.CSTTransformer):
    """
    Removes all comments from the code.
    This includes inline comments and standalone comment lines.
    The only comment that survives is the header added at the very end.
    """
    def on_leave(self, original_node: cst.CSTNode, updated_node: cst.CSTNode) -> cst.CSTNode:
        # Remove leading comments (including empty lines with comments)
        if hasattr(updated_node, 'leading_lines'):
            # Keep only lines that have no comment (i.e., pure whitespace or empty)
            new_lines = [line for line in updated_node.leading_lines if line.comment is None]
            updated_node = updated_node.with_changes(leading_lines=new_lines)
        return updated_node


class DocstringRemover(cst.CSTTransformer):
    """Removes all docstrings from functions, classes and the module itself."""
    @staticmethod
    def _remove_docstring(body: cst.IndentedBlock) -> cst.IndentedBlock:
        new_body = list(body.body)
        if new_body and isinstance(new_body[0], cst.SimpleStatementLine):
            stmt = new_body[0].body[0]
            if isinstance(stmt, cst.Expr) and isinstance(stmt.value, cst.SimpleString):
                new_body.pop(0)
        return body.with_changes(body=new_body)

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        return updated_node.with_changes(body=self._remove_docstring(updated_node.body))

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        return updated_node.with_changes(body=self._remove_docstring(updated_node.body))

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        return updated_node.with_changes(body=self._remove_docstring(cst.IndentedBlock(updated_node.body)).body)


class StringEncryptionTransformer(cst.CSTTransformer):
    """
    Replaces every non‑trivial string literal (>2 characters) with a call
    to the (later obfuscated) decoder function.

    Skips bytes literals and already obfuscated patterns.
    The decoder itself is prepended to the module only once.
    """
    def __init__(self):
        super().__init__()
        self._replaced = False
        self._decoder_inserted = False

    def leave_SimpleString(self, original_node: cst.SimpleString, updated_node: cst.SimpleString) -> cst.BaseExpression:
        # Skip bytes literals (e.g., b'...')
        val_str = original_node.value
        if val_str.startswith(('b', 'B')):
            return updated_node

        try:
            str_val = eval(val_str)
        except Exception:
            return updated_node

        # Skip short strings and empty ones
        if len(str_val) <= 2:
            return updated_node

        enc, key_repr = _obfuscate_string(str_val)
        call_src = f"{DECODER_FUNC_NAME}('{enc}', {key_repr})"
        self._replaced = True
        return cst.parse_expression(call_src)

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        # Do not add decoder if it already exists in the module
        has_decoder = False
        for stmt in updated_node.body:
            if isinstance(stmt, cst.FunctionDef) and stmt.name.value == DECODER_FUNC_NAME:
                has_decoder = True
                break
        if self._replaced and not has_decoder and not self._decoder_inserted:
            decoder_module = cst.parse_module(DECODER_CODE)
            self._decoder_inserted = True
            return updated_node.with_changes(body=[*decoder_module.body, *updated_node.body])
        return updated_node


class RenameTransformer(cst.CSTTransformer):
    """
    Scope‑aware renaming of all user‑defined names (variables, functions, classes)
    to confusable sequences. Imports are aliased to hide their real names, and
    built‑ins / dunders are left untouched.
    """
    def __init__(self):
        super().__init__()
        self.name_map: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.scope_stack = ["__module__"]
        self.skip_names = set()          # ids of Name nodes we must not rename
        self.import_map: Dict[str, str] = {}  # original module/import name -> obfuscated alias
        self.builtins = set(dir(builtins))
        self.inside_import = False
        self.method_global_map: Dict[str, str] = {}

    def _new_name(self, name: str) -> str:
        if name == DECODER_FUNC_NAME:
            return name
        current = self.scope_stack[-1]
        if name not in self.name_map[current]:
            if name.startswith("__"):
                return name
            new = _mangled_name()
            while new in self.name_map[current].values():
                new = _mangled_name()
            self.name_map[current][name] = new
        return self.name_map[current][name]

    def _alias_imports(self, imports):
        new_imports = []
        for alias in imports:
            if not isinstance(alias.name, cst.Name):
                new_imports.append(alias)
                continue
            original = alias.name.value
            if original == "*":
                new_imports.append(alias)
                continue

            if alias.asname:
                new_alias = self._new_name(alias.asname.name.value)
                self.import_map[new_alias] = new_alias
            else:
                if original in self.import_map:
                    new_alias = self.import_map[original]
                else:
                    new_alias = _mangled_name()
                    self.import_map[original] = new_alias
            new_imports.append(
                alias.with_changes(asname=cst.AsName(name=cst.Name(new_alias)))
            )
        return new_imports

    def _is_builtin(self, name: str) -> bool:
        return name in self.builtins

    # ---------- Import handling ----------
    def visit_Import(self, node: cst.Import) -> bool:
        self.inside_import = True
        return True
    def leave_Import(self, original_node: cst.Import, updated_node: cst.Import) -> cst.Import:
        self.inside_import = False
        return updated_node.with_changes(names=self._alias_imports(updated_node.names))
    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        self.inside_import = True
        return True
    def leave_ImportFrom(self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom) -> cst.ImportFrom:
        self.inside_import = False
        return updated_node.with_changes(names=self._alias_imports(updated_node.names))

    # ---------- Protection of special names ----------
    def visit_Arg(self, node: cst.Arg) -> bool:
        if node.keyword:
            self.skip_names.add(id(node.keyword))
        return True
    def visit_Attribute(self, node: cst.Attribute) -> bool:
        if isinstance(node.attr, cst.Name):
            self.skip_names.add(id(node.attr))
        return True
    def leave_Attribute(self, original_node: cst.Attribute, updated_node: cst.Attribute) -> cst.BaseExpression:
        if isinstance(updated_node.value, cst.Name):
            orig_val = updated_node.value.value
            if orig_val in self.import_map:
                updated_node = updated_node.with_changes(value=cst.Name(self.import_map[orig_val]))
        if isinstance(updated_node.attr, cst.Name):
            attr_name = updated_node.attr.value
            if attr_name in self.method_global_map:
                updated_node = updated_node.with_changes(attr=cst.Name(self.method_global_map[attr_name]))
        return updated_node

    # ---------- Scope tracking ----------
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self.scope_stack.append(node.name.value)
        for param in node.params.params:
            self._new_name(param.name.value)
        return True
    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        scope = self.scope_stack.pop()
        new_name = cst.Name(self._new_name(original_node.name.value))
        if self.scope_stack and self.scope_stack[-1] != "__module__":
            self.method_global_map[original_node.name.value] = new_name.value

        new_params = [
            p.with_changes(name=cst.Name(self.name_map[scope].get(p.name.value, p.name.value)))
            for p in updated_node.params.params
        ]
        return updated_node.with_changes(
            name=new_name,
            params=updated_node.params.with_changes(params=new_params)
        )

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self.scope_stack.append(node.name.value)
        return True
    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        self.scope_stack.pop()
        new_name = cst.Name(self._new_name(original_node.name.value))
        return updated_node.with_changes(name=new_name)

    def leave_Assign(self, original_node: cst.Assign, updated_node: cst.Assign) -> cst.Assign:
        new_targets = []
        for target in updated_node.targets:
            if isinstance(target.target, cst.Name):
                new_name = self._new_name(target.target.value)
                new_targets.append(target.with_changes(target=cst.Name(new_name)))
            else:
                new_targets.append(target)
        return updated_node.with_changes(targets=new_targets)

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.BaseExpression:
        if id(original_node) in self.skip_names:
            return updated_node
        if self.inside_import:
            return updated_node
        val = original_node.value
        if self._is_builtin(val):
            return updated_node
        if val in self.import_map:
            return cst.Name(self.import_map[val])
        for scope in reversed(self.scope_stack):
            if val in self.name_map[scope]:
                return updated_node.with_changes(value=self.name_map[scope][val])
        return updated_node


class ConstantObfuscator(cst.CSTTransformer):
    """
    Replaces integer, float, boolean, and None literals with equivalent
    arithmetic/logic expressions that are hard to reduce statically.
    """
    def _obfuscate_int(self, value: int) -> str:
        if value == 0:
            return "~1+1"
        if value == 1:
            return "int(True)"
        if value < 0:
            return f"(-{self._obfuscate_int(-value)})"

        choices = []
        # Addition/subtraction tricks
        a = random.randint(2, value + 1000)
        b = a - value
        choices.append(f"{a} - {b}")
        a = random.randint(1, value)
        b = value - a
        choices.append(f"{a} + {b}")
        # Multiplication/division
        if value != 0:
            a = random.randint(1, 20)
            if value % a == 0:
                choices.append(f"{a} * {value // a}")
        # Bitwise
        if value > 10:
            shift = random.randint(1, 5)
            base = value >> shift
            if base << shift == value:
                choices.append(f"{base} << {shift}")
        # Hexadecimal via int()
        choices.append(f"int('{hex(value)[2:]}', 16)")
        # Using len([0]*value)
        if value <= 1000:
            choices.append(f"len([0]*{value})")
        # Using eval with string
        choices.append(f"eval('{value}')")
        choices.append(hex(value))
        choices.append(oct(value))
        return random.choice(choices)

    def _obfuscate_float(self, value: float) -> str:
        if value == float('inf') or value == float('-inf') or value != value:
            return repr(value)
        int_part = int(value)
        frac_part = value - int_part
        # Sometimes encode as fraction
        if random.choice([True, False]):
            return f"({int_part} + {frac_part})"
        else:
            # Use division: 3.14 -> 314/100
            numerator = int(value * 1e6)
            denominator = 1000000
            return f"({numerator}/{denominator})"

    def _obfuscate_bool(self, value: bool) -> str:
        if value:
            return "(1 == 1)"
        else:
            return "(1 == 0)"

    def _obfuscate_none(self) -> str:
        return "(lambda: None)()"

    def leave_Integer(self, original_node: cst.Integer, updated_node: cst.Integer) -> cst.BaseExpression:
        val = eval(original_node.value)
        new_expr = self._obfuscate_int(val)
        return cst.parse_expression(new_expr)

    def leave_Float(self, original_node: cst.Float, updated_node: cst.Float) -> cst.BaseExpression:
        val = eval(original_node.value)
        new_expr = self._obfuscate_float(val)
        return cst.parse_expression(new_expr)

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.BaseExpression:
        # Replace True/False/None
        if updated_node.value == "True":
            return cst.parse_expression(self._obfuscate_bool(True))
        elif updated_node.value == "False":
            return cst.parse_expression(self._obfuscate_bool(False))
        elif updated_node.value == "None":
            return cst.parse_expression(self._obfuscate_none())
        return updated_node


class OpaquePredicateInjector(cst.CSTTransformer):
    """
    Wraps a random subset of simple statements inside an `if` with an
    always‑true condition.  The else branch contains dead code that
    mimics real functionality.
    """
    def __init__(self, probability: float = 0.35):
        super().__init__()
        self.prob = probability
        self._opaque_exprs = [
            "(7 & 3) == 3",
            "len(bin(42)) > 5",
            "bool('') == False",
            "int(True) == 1",
            "~-1 == 0",
            "3**2 - 9 == 0",
            "all([1, 2, 3])",
            "not not 1",
            "(lambda x: x)(True)",
            "__debug__ or not __debug__",
        ]

    def _random_opaque(self) -> str:
        return random.choice(self._opaque_exprs)

    def leave_IndentedBlock(self, original_node: cst.IndentedBlock,
                            updated_node: cst.IndentedBlock) -> cst.IndentedBlock:
        new_body = []
        for stmt in updated_node.body:
            if isinstance(stmt, cst.SimpleStatementLine) and random.random() < self.prob:
                opaque_test = self._random_opaque()
                # Create a dead else branch that looks real
                fake_stmt = cst.parse_statement(f"{_mangled_name()} = __import__('sys').argv")
                wrapped = cst.If(
                    test=cst.parse_expression(opaque_test),
                    body=cst.IndentedBlock(body=[stmt]),
                    orelse=cst.Else(body=cst.IndentedBlock(body=[fake_stmt]))
                )
                new_body.append(wrapped)
            else:
                new_body.append(stmt)
        return updated_node.with_changes(body=new_body)


class ControlFlowFlattener(cst.CSTTransformer):
    """
    Transforms the body of each function into a while‑True state machine
    where each original statement becomes a state. The next‑state is
    computed through an opaque arithmetic operation.

    Now supports nested blocks and adds no‑op states.
    """
    def _random_opaque(self) -> str:
        return random.choice([
            "(712 & 319)",
            "int(bool(None == True))",
            "len(bin(2**4 - 1))",
            "(lambda: 42)() - 42",
        ])

    def leave_FunctionDef(self, original_node: cst.FunctionDef,
                          updated_node: cst.FunctionDef) -> cst.FunctionDef:
        body = updated_node.body.body
        if len(body) < 2:
            return updated_node

        # Collect all statements recursively (simple flattening)
        statements = []
        self._collect_statements(body, statements)
        if len(statements) < 2:
            return updated_node

        indices = list(range(len(statements)))
        random.shuffle(indices)
        state_var = _mangled_name()

        # Build state entries (index, statement(s), next_state)
        entries = []
        for new_idx, old_idx in enumerate(indices):
            stmt_list = statements[old_idx]
            next_old = old_idx + 1
            try:
                next_state = indices.index(next_old)
            except ValueError:
                next_state = -1
            entries.append((new_idx, stmt_list, next_state))

        # Insert some no‑op states to confuse
        for _ in range(random.randint(1, 3)):
            noop_idx = len(entries)
            entries.append((noop_idx, [cst.parse_statement(f"{_mangled_name()} = None")], random.choice([-1, random.randint(0, len(entries)-1)])))

        def opaque_assign(var: str, target: int) -> cst.BaseSmallStatement:
            if target == -1:
                expr = "-1"
            else:
                delta = random.randint(10, 100)
                base = target + delta
                expr = f"({base} - {delta})"
            return cst.parse_statement(f"{var} = {expr}")

        def build_chain(entries):
            if not entries:
                return None
            idx, stmts, next_st = entries[0]
            case_body = stmts + [opaque_assign(state_var, next_st)]
            if_node = cst.If(
                test=cst.parse_expression(f"{state_var} == {idx}"),
                body=cst.IndentedBlock(body=case_body)
            )
            if len(entries) > 1:
                rest_chain = build_chain(entries[1:])
                return if_node.with_changes(orelse=cst.Else(body=cst.IndentedBlock(body=[rest_chain])))
            return if_node

        if_chain = build_chain(entries)
        if if_chain is None:
            return updated_node

        loop_body = [
            if_chain,
            cst.If(
                test=cst.parse_expression(f"{state_var} == -1"),
                body=cst.IndentedBlock(body=[cst.SimpleStatementLine(body=[cst.Break()])])
            )
        ]

        new_func_body = [
            cst.parse_statement(f"{state_var} = {indices.index(0)}"),
            cst.While(
                test=cst.Name("True"),
                body=cst.IndentedBlock(body=loop_body)
            )
        ]

        return updated_node.with_changes(body=cst.IndentedBlock(body=new_func_body))

    def _collect_statements(self, body_nodes, out_list):
        """Recursively collect SimpleStatementLine and If/For/While bodies."""
        for node in body_nodes:
            if isinstance(node, cst.SimpleStatementLine):
                out_list.append([node])
            elif isinstance(node, (cst.If, cst.For, cst.While)):
                # For simplicity, treat the whole block as one statement
                out_list.append([node])
            elif isinstance(node, cst.IndentedBlock):
                self._collect_statements(node.body, out_list)


class JunkCodeInjector(cst.CSTTransformer):
    """Inserts dead assignments, useless loops, and no‑op computations."""
    def __init__(self, probability: float = 0.25):
        super().__init__()
        self.prob = probability

    def _junk_statement(self) -> cst.BaseSmallStatement:
        junk_type = random.choice(['assign', 'loop', 'compute', 'call'])
        if junk_type == 'assign':
            var = _mangled_name()
            val = random.randint(1, 10000)
            return cst.parse_statement(f"{var} = {val} ; {var} = None")
        elif junk_type == 'loop':
            return cst.parse_statement(f"for _ in range(1): pass")
        elif junk_type == 'compute':
            a, b = random.randint(1, 100), random.randint(1, 100)
            op = random.choice(['+', '-', '*', '//'])
            return cst.parse_statement(f"_ = {a} {op} {b}; del _")
        else:  # call
            return cst.parse_statement(f"__import__('sys').version_info")

    def leave_IndentedBlock(self, original_node: cst.IndentedBlock,
                            updated_node: cst.IndentedBlock) -> cst.IndentedBlock:
        new_body = []
        for stmt in updated_node.body:
            new_body.append(stmt)
            if random.random() < self.prob:
                new_body.append(self._junk_statement())
        return updated_node.with_changes(body=new_body)


class AntiDebugInjector(cst.CSTTransformer):
    """Inserts checks for debugger presence and __debug__ flag."""
    def __init__(self):
        super().__init__()

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        # Insert at the beginning of function body
        trace_check = cst.parse_statement(
            "if __import__('sys').gettrace() is not None: _ = 1"
        )
        debug_check = cst.parse_statement(
            "if not __debug__: __import__('sys').stdout.write('')"
        )
        new_body = [trace_check, debug_check] + list(updated_node.body.body)
        return updated_node.with_changes(body=cst.IndentedBlock(body=new_body))


class OperatorObfuscator(cst.CSTTransformer):
    """
    Replaces binary operators with more complex equivalent expressions.
    Uses pure string‑based rewriting to avoid libcst API complexities.
    """
    def leave_BinaryOperation(self, original_node: cst.BinaryOperation, updated_node: cst.BinaryOperation) -> cst.BaseExpression:
        if random.random() > 0.4:
            return updated_node

        op = updated_node.operator
        left = updated_node.left
        right = updated_node.right

        # Convert nodes to source code strings for safe rewriting
        left_src = cst.Module(body=[cst.SimpleStatementLine(body=[cst.Expr(value=left)])]).code.strip()
        right_src = cst.Module(body=[cst.SimpleStatementLine(body=[cst.Expr(value=right)])]).code.strip()

        if isinstance(op, cst.Add):
            # a + b -> a - (-b)
            new_src = f"({left_src} - (-{right_src}))"
            return cst.parse_expression(new_src)
        elif isinstance(op, cst.Multiply):
            # a * b -> a / (1.0 / b)   (use float to avoid integer division issues)
            new_src = f"({left_src} / (1.0 / {right_src}))"
            return cst.parse_expression(new_src)
        elif isinstance(op, cst.Equal):
            # a == b -> not (a != b)
            new_src = f"(not ({left_src} != {right_src}))"
            return cst.parse_expression(new_src)
        elif isinstance(op, cst.NotEqual):
            # a != b -> not (a == b)
            new_src = f"(not ({left_src} == {right_src}))"
            return cst.parse_expression(new_src)
        elif isinstance(op, cst.And):
            # a and b -> (a if not a else b)
            new_src = f"({left_src} if not {left_src} else {right_src})"
            return cst.parse_expression(new_src)
        elif isinstance(op, cst.Or):
            # a or b -> (a if a else b)
            new_src = f"({left_src} if {left_src} else {right_src})"
            return cst.parse_expression(new_src)
        else:
            return updated_node


# ======================================================================
# Top‑level pipeline
# ======================================================================
HEADER_COMMENT = "# Obfuscated with CObfuscate\n"

def obfuscate_code(source: str) -> str:
    """
    Apply all obfuscation stages to the given source code and return the
    obfuscated version.
    """
    if obfuscate_string_b64 is None:
        print("Warning: C extension not available; using pure‑Python string obfuscation.")

    try:
        tree = cst.parse_module(source)

        # 0. Remove all comments (including inline and standalone)
        tree = tree.visit(CommentRemover())

        # 1. Remove docstrings
        tree = tree.visit(DocstringRemover())

        # 2. Encrypt string literals and inject decoder
        tree = tree.visit(StringEncryptionTransformer())

        # 3. Rename identifiers
        tree = tree.visit(RenameTransformer())

        # 4. Obfuscate numeric and boolean constants
        tree = tree.visit(ConstantObfuscator())

        # 5. Inject opaque predicates
        tree = tree.visit(OpaquePredicateInjector(probability=0.3))

        # 6. Flatten control flow
        tree = tree.visit(ControlFlowFlattener())

        # 7. Insert junk code
        tree = tree.visit(JunkCodeInjector(probability=0.2))

        # 8. Inject anti‑debug checks
        tree = tree.visit(AntiDebugInjector())

        # 9. Obfuscate operators
        tree = tree.visit(OperatorObfuscator())

        return HEADER_COMMENT + tree.code

    except Exception as e:
        return f"# OBFUSCATION FAILED: {e}\n{source}"


def obfuscate_file(input_file: str, output_file: str) -> None:
    """Read an input Python file, obfuscate it, and write the result."""
    with open(input_file, "r", encoding="utf-8") as f:
        source = f.read()
    obfuscated = obfuscate_code(source)
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(obfuscated)


def obfuscate_directory(input_dir: str, output_dir: str) -> None:
    """Recursively obfuscate all .py files in a directory tree."""
    in_path = Path(input_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    for py_file in in_path.rglob("*.py"):
        rel = py_file.relative_to(in_path)
        out_file = out_path / rel
        out_file.parent.mkdir(parents=True, exist_ok=True)
        obfuscate_file(str(py_file), str(out_file))