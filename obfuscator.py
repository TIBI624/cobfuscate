"""
Core obfuscation logic for CObfuscate.
Includes:
- Scope-aware identifier renaming
- String literal obfuscation via C backend
- Control flow flattening
- Docstring removal
"""

import os
import random
import libcst as cst
from pathlib import Path
from collections import defaultdict

# CORRECTED: Import from the 'ext' package as defined in pyproject.toml
try:
    from .ext.obfuscate import obfuscate_string_b64
except ImportError:
    # This will be printed if the C extension is not compiled
    obfuscate_string_b64 = None

HEADER_COMMENT = "# Obfuscated with CObfuscate\n"
DECODER_FUNC_NAME = "_d"
DECODER_CODE = f"""
import base64
def {DECODER_FUNC_NAME}(e, k):
    b=base64.b64decode(e)
    k_len=len(k)
    return "".join([chr(b[i]^k[i%k_len]) for i in range(len(b))])
"""

def _random_mangled_name() -> str:
    """
    Generates a visually confusing but valid identifier.
    Guarantees the first character is a letter to avoid 'invalid identifier' errors.
    """
    prefix = "O"
    suffix = "".join(random.choice("O0") for _ in range(random.randint(5, 10)))
    return prefix + suffix

class RenameTransformer(cst.CSTTransformer):
    """
    Performs scope-aware renaming of variables, functions, and classes.
    """
    def __init__(self):
        super().__init__()
        self.name_map = defaultdict(dict)
        self.scope_stack = ["global"]

    def _get_new_name(self, name: str) -> str:
        current_scope = self.scope_stack[-1]
        if name not in self.name_map[current_scope]:
            if name.startswith("__"):
                return name
            new_name = _random_mangled_name()
            while new_name in self.name_map[current_scope].values():
                new_name = _random_mangled_name()
            self.name_map[current_scope][name] = new_name
        return self.name_map[current_scope][name]

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self.scope_stack.append(node.name.value)
        for param in node.params.params:
            self._get_new_name(param.name.value)
        return True

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        scope_name = self.scope_stack.pop()
        new_name_str = self._get_new_name(original_node.name.value)
        new_name = cst.Name(new_name_str)
        new_params = []
        for param in updated_node.params.params:
            new_param_name = self.name_map[scope_name].get(param.name.value, param.name.value)
            new_params.append(param.with_changes(name=cst.Name(new_param_name)))
        
        return updated_node.with_changes(
            name=new_name, params=updated_node.params.with_changes(params=new_params)
        )

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self.scope_stack.append(node.name.value)
        return True

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        self.scope_stack.pop()
        new_name_str = self._get_new_name(original_node.name.value)
        return updated_node.with_changes(name=cst.Name(new_name_str))

    def leave_Assign(
        self, original_node: cst.Assign, updated_node: cst.Assign
    ) -> cst.Assign:
        new_targets = []
        for target in updated_node.targets:
            if isinstance(target.target, cst.Name):
                new_name_str = self._get_new_name(target.target.value)
                new_targets.append(
                    target.with_changes(target=cst.Name(new_name_str))
                )
            else:
                new_targets.append(target)
        return updated_node.with_changes(targets=new_targets)

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.BaseExpression:
        for scope in reversed(self.scope_stack):
            if original_node.value in self.name_map[scope]:
                return updated_node.with_changes(value=self.name_map[scope][original_node.value])
        return updated_node

class StringTransformer(cst.CSTTransformer):
    """Obfuscates string literals using the C backend and injects a decoder."""
    def leave_SimpleString(self, original_node: cst.SimpleString, updated_node: cst.SimpleString) -> cst.BaseExpression:
        if not obfuscate_string_b64:
            return updated_node
        try:
            str_val = eval(original_node.value)
            if len(str_val) > 2:
                encrypted_b64, key_bytes = obfuscate_string_b64(str_val)
                key_str = key_bytes.decode('utf-8')
                return cst.parse_expression(
                    f"{DECODER_FUNC_NAME}('{encrypted_b64}', b'{key_str}')"
                )
        except Exception:
            pass
        return updated_node

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        if obfuscate_string_b64:
            decoder_node = cst.parse_module(DECODER_CODE)
            return updated_node.with_changes(body=[*decoder_node.body, *updated_node.body])
        return updated_node

class DocstringRemover(cst.CSTTransformer):
    """Removes docstrings from functions and classes."""
    def _remove_docstring(self, body: cst.IndentedBlock) -> cst.IndentedBlock:
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

class ControlFlowFlattener(cst.CSTTransformer):
    """Flattens control flow within function bodies using a while loop and state machine."""
    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        body_stmts = updated_node.body.body
        if len(body_stmts) < 2:
            return updated_node

        state_var_name = _random_mangled_name()
        
        indices = list(range(len(body_stmts)))
        random.shuffle(indices)
        
        dispatcher_cases = []
        for new_idx, old_idx in enumerate(indices):
            stmt = body_stmts[old_idx]
            
            # Find the new shuffled index of the next statement in the original sequence
            # If this is the last statement, the next state is -1 (exit condition)
            next_state = -1
            try:
                next_original_idx = old_idx + 1
                next_state = indices.index(next_original_idx)
            except ValueError:
                pass # next_state remains -1

            case_body = [
                stmt,
                cst.parse_statement(f"{state_var_name} = {next_state}")
            ]
            if_case = cst.If(
                test=cst.parse_expression(f"{state_var_name} == {new_idx}"),
                body=cst.IndentedBlock(body=case_body)
            )
            dispatcher_cases.append(if_case)

        # Chain the If nodes together into a single if/elif/else block
        if len(dispatcher_cases) > 1:
            for i in range(len(dispatcher_cases) - 1, 0, -1):
                dispatcher_cases[i-1] = dispatcher_cases[i-1].with_changes(
                    orelse=cst.Else(body=cst.IndentedBlock(body=[dispatcher_cases[i]]))
                )
        
        # --- START OF BUG FIX ---
        # The original code was missing everything from here until the return statement.
        # It tried to use 'new_func_body' without ever defining it.

        # The root of our if/elif/else dispatcher chain
        dispatcher_root = dispatcher_cases[0]

        # The body of the main while loop
        loop_body = [
            dispatcher_root,
            # Add a final check to break the loop when state is -1
            cst.If(
                test=cst.parse_expression(f"{state_var_name} == -1"),
                body=cst.IndentedBlock(body=[cst.SimpleStatementLine(body=[cst.Break()])])
            )
        ]

        # Define the complete new function body, which was missing before
        new_func_body = [
            # 1. Initialize the state variable to start at the beginning of the original code
            cst.parse_statement(f"{state_var_name} = {indices.index(0)}"),
            # 2. Create the while loop that contains the dispatcher
            cst.While(
                test=cst.Name("True"),
                body=cst.IndentedBlock(body=loop_body)
            )
        ]
        # --- END OF BUG FIX ---
        
        # Replace the function's body with the newly constructed flattened flow
        return updated_node.with_changes(body=cst.IndentedBlock(body=new_func_body))

def obfuscate_code(source: str) -> str:
    """Run the full obfuscation pipeline on a source string."""
    if obfuscate_string_b64 is None:
        print("Warning: C backend not found or failed to import. String obfuscation will be disabled.")
    
    try:
        tree = cst.parse_module(source)
        
        # Pipeline of transformations
        tree = tree.visit(DocstringRemover())
        tree = tree.visit(StringTransformer())
        tree = tree.visit(RenameTransformer())
        tree = tree.visit(ControlFlowFlattener())

        return HEADER_COMMENT + tree.code
    except Exception as e:
        # This is where your error message came from
        return f"# OBFUSCATION FAILED: {e}\n" + source

def obfuscate_file(input_file: str, output_file: str) -> None:
    """Read a file, obfuscate it, and write to the output."""
    with open(input_file, "r", encoding="utf-8") as f:
        source = f.read()
    obfuscated = obfuscate_code(source)
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(obfuscated)

def obfuscate_directory(input_dir: str, output_dir: str) -> None:
    """Recursively obfuscate all .py files in a directory."""
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    for py_file in input_path.rglob("*.py"):
        rel_path = py_file.relative_to(input_path)
        out_file = output_path / rel_path
        obfuscate_file(str(py_file), str(out_file))