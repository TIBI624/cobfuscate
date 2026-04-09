"""
Core obfuscation logic for CObfuscate.
Includes:
- Scope-aware identifier renaming
- Safe import aliasing for built-in/external modules
- Self-obfuscating string literal decoder
- Control flow flattening
- Docstring removal
"""

import os
import random
import builtins
import libcst as cst
from pathlib import Path
from collections import defaultdict

try:
    from .ext.obfuscate import obfuscate_string_b64
except ImportError:
    obfuscate_string_b64 = None

HEADER_COMMENT = "# Obfuscated with CObfuscate\n"

# The decoder is written as normal Python code. 
# The RenameTransformer and ControlFlowFlattener will chew it up into an unreadable mess!
DECODER_FUNC_NAME = "_d"
DECODER_CODE = f"""
import base64
def {DECODER_FUNC_NAME}(e, k):
    b = base64.b64decode(e)
    return "".join(chr(b[i] ^ k[i % len(k)]) for i in range(len(b)))
"""

def _random_mangled_name() -> str:
    """Generates a visually confusing but valid identifier."""
    prefix = "O"
    suffix = "".join(random.choice("O0") for _ in range(random.randint(5, 10)))
    return prefix + suffix

class RenameTransformer(cst.CSTTransformer):
    """
    Performs scope-aware renaming of variables, functions, and classes.
    Safely handles imports by aliasing them, and protects attributes/builtins.
    """
    def __init__(self):
        super().__init__()
        self.name_map = defaultdict(dict)
        self.scope_stack = ["global"]
        self.skip_names = set()
        self.import_map = {}  # Maps original import names to their new obfuscated aliases
        self.builtins = set(dir(builtins))
        self.inside_import = False  # Strict flag to prevent renaming inside import statements

    def _get_new_name(self, name: str) -> str:
        current_scope = self.scope_stack[-1]
        if name not in self.name_map[current_scope]:
            if name.startswith("__"):
                return name  # Protect dunders
            new_name = _random_mangled_name()
            while new_name in self.name_map[current_scope].values():
                new_name = _random_mangled_name()
            self.name_map[current_scope][name] = new_name
        return self.name_map[current_scope][name]

    def _alias_imports(self, names):
        """Helper to obfuscate import names by adding aliases."""
        new_names = []
        for import_alias in names:
            name_node = import_alias.name
            
            # Only safely alias simple names (e.g., 'import asyncio')
            # Skip complex imports like 'import os.path' to avoid breaking attribute access
            if not isinstance(name_node, cst.Name):
                new_names.append(import_alias)
                continue
                
            orig_name = name_node.value
            if orig_name == "*":
                new_names.append(import_alias)
                continue
            
            # If it already has an alias (e.g., import X as Y), rename Y
            if import_alias.asname:
                new_alias_name = self._get_new_name(import_alias.asname.name.value)
                self.import_map[new_alias_name] = new_alias_name 
            else:
                # Prevent duplicate aliases if the user imports the same module twice
                if orig_name in self.import_map:
                    new_alias_name = self.import_map[orig_name]
                else:
                    # Create a new alias for the module (e.g., import asyncio -> import asyncio as O0O0O)
                    new_alias_name = _random_mangled_name()
                    self.import_map[orig_name] = new_alias_name
            
            new_names.append(
                import_alias.with_changes(
                    asname=cst.AsName(name=cst.Name(new_alias_name))
                )
            )
        return new_names

    # --- Import Handlers ---
    def visit_Import(self, node: cst.Import) -> bool:
        self.inside_import = True
        return True

    def leave_Import(self, original_node: cst.Import, updated_node: cst.Import) -> cst.Import:
        self.inside_import = False
        new_names = self._alias_imports(updated_node.names)
        return updated_node.with_changes(names=new_names)

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        self.inside_import = True
        return True

    def leave_ImportFrom(self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom) -> cst.ImportFrom:
        self.inside_import = False
        new_names = self._alias_imports(updated_node.names)
        return updated_node.with_changes(names=new_names)

    # --- Protection Handlers ---
    def visit_Arg(self, node: cst.Arg) -> bool:
        """Protect keyword arguments in function calls (e.g., 'headers' in get(headers=X))"""
        if node.keyword is not None:
            self.skip_names.add(id(node.keyword))
        return True

    def visit_Attribute(self, node: cst.Attribute) -> bool:
        """Protect object attributes (e.g., 'version' in sys.version)"""
        if isinstance(node.attr, cst.Name):
            self.skip_names.add(id(node.attr))
        return True

    def leave_Attribute(self, original_node: cst.Attribute, updated_node: cst.Attribute) -> cst.BaseExpression:
        """Rename the object IF it's an imported module (e.g., asyncio.sleep -> O0O0.sleep)"""
        if isinstance(updated_node.value, cst.Name):
            orig_val = updated_node.value.value
            if orig_val in self.import_map:
                return updated_node.with_changes(value=cst.Name(self.import_map[orig_val]))
        return updated_node

    # --- Scope Handlers ---
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self.scope_stack.append(node.name.value)
        for param in node.params.params:
            self._get_new_name(param.name.value)
        return True

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
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

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        self.scope_stack.pop()
        new_name_str = self._get_new_name(original_node.name.value)
        return updated_node.with_changes(name=cst.Name(new_name_str))

    def leave_Assign(self, original_node: cst.Assign, updated_node: cst.Assign) -> cst.Assign:
        new_targets = []
        for target in updated_node.targets:
            if isinstance(target.target, cst.Name):
                new_name_str = self._get_new_name(target.target.value)
                new_targets.append(target.with_changes(target=cst.Name(new_name_str)))
            else:
                new_targets.append(target)
        return updated_node.with_changes(targets=new_targets)

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.BaseExpression:
        # 1. Skip if explicitly protected (like keywords/attributes)
        if id(original_node) in self.skip_names:
            return updated_node
        
        # 2. CRITICAL FIX: Do not rename anything inside an import statement
        if self.inside_import:
            return updated_node
            
        val = original_node.value
        
        # 3. Skip Python built-ins (print, len, True, None, exec, etc.)
        if val in self.builtins:
            return updated_node
            
        # 4. If it's an imported module/function, use its obfuscated alias
        if val in self.import_map:
            return cst.Name(self.import_map[val])
        
        # 5. Otherwise, apply standard scope-aware variable renaming
        for scope in reversed(self.scope_stack):
            if val in self.name_map[scope]:
                return updated_node.with_changes(value=self.name_map[scope][val])
                
        return updated_node

class StringTransformer(cst.CSTTransformer):
    """Obfuscates string literals using the C backend and injects a decoder to be flattened."""
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
            # Parse the decoder and prepend it. 
            # RenameTransformer and ControlFlowFlattener will turn it into unreadable spaghetti!
            decoder_module = cst.parse_module(DECODER_CODE)
            return updated_node.with_changes(body=[*decoder_module.body, *updated_node.body])
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
            
            next_state = -1
            try:
                next_original_idx = old_idx + 1
                next_state = indices.index(next_original_idx)
            except ValueError:
                pass

            case_body = [
                stmt,
                cst.parse_statement(f"{state_var_name} = {next_state}")
            ]
            if_case = cst.If(
                test=cst.parse_expression(f"{state_var_name} == {new_idx}"),
                body=cst.IndentedBlock(body=case_body)
            )
            dispatcher_cases.append(if_case)

        if len(dispatcher_cases) > 1:
            for i in range(len(dispatcher_cases) - 1, 0, -1):
                dispatcher_cases[i-1] = dispatcher_cases[i-1].with_changes(
                    orelse=cst.Else(body=cst.IndentedBlock(body=[dispatcher_cases[i]]))
                )
        
        dispatcher_root = dispatcher_cases[0]

        loop_body = [
            dispatcher_root,
            cst.If(
                test=cst.parse_expression(f"{state_var_name} == -1"),
                body=cst.IndentedBlock(body=[cst.SimpleStatementLine(body=[cst.Break()])])
            )
        ]

        new_func_body = [
            cst.parse_statement(f"{state_var_name} = {indices.index(0)}"),
            cst.While(
                test=cst.Name("True"),
                body=cst.IndentedBlock(body=loop_body)
            )
        ]
        
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