"""Core obfuscation logic with variable renaming, junk code insertion, and docstring removal."""

import os
import random
import libcst as cst
from pathlib import Path
from typing import Sequence  # ИСПРАВЛЕНО: Добавлен импорт Sequence

HEADER_COMMENT = "# This file is obfuscated using CObfuscate\n"

# ----------------------------------------------------------------------
# Junk code generator
# ----------------------------------------------------------------------
JUNK_FUNCTIONS = [
    """def _junk_func_{}(x):
    return x * 42 + 7
""",
    """def _junk_func_{}(a, b):
    return (a ^ b) & 0xFF
""",
    """def _junk_func_{}(lst):
    return sum(i * i for i in lst)
""",
]

JUNK_STATEMENTS = [
    "x = 123; x = x ^ 0xDEADBEEF",
    "if 0: print('never')",
    "for _ in range(1): pass",
    "try: pass\nexcept: pass",
]


def _random_name() -> str:
    """Generate a random identifier."""
    return "_" + hex(random.getrandbits(32))[2:]


def _insert_junk_function(module: cst.Module) -> cst.Module:
    """Add a random junk function at the module level."""
    name = _random_name()
    idx = random.randint(0, len(JUNK_FUNCTIONS) - 1)
    func_code = JUNK_FUNCTIONS[idx].format(name)
    try:
        junk_func = cst.parse_module(func_code).body[0]
        new_body = [junk_func] + list(module.body)
        return module.with_changes(body=new_body)
    except Exception:
        return module


def _insert_junk_statements_in_block(body: Sequence[cst.BaseStatement]) -> Sequence[cst.BaseStatement]:
    """Insert random junk statements into a block of code."""
    # ИСПРАВЛЕНО: Убрано cst.Sequence, теперь используется Sequence из typing
    if len(body) < 2:
        return body
    
    new_body = []
    for stmt in body:
        new_body.append(stmt)
        if random.random() < 0.3:
            junk_stmt_str = JUNK_STATEMENTS[random.randint(0, len(JUNK_STATEMENTS) - 1)]
            try:
                junk_stmt = cst.parse_statement(junk_stmt_str)
                new_body.append(junk_stmt)
            except Exception:
                pass
    return new_body # В LibCST блоки обычно принимают обычные списки/итерируемые объекты


class ObfuscateTransformer(cst.CSTTransformer):
    """
    AST transformer that:
    - Renames variables, functions, classes, and parameters.
    - Removes docstrings.
    - Inserts junk statements inside functions.
    """

    def __init__(self):
        self.name_map = {}
        self.global_names = set()
        super().__init__()

    def _rename(self, name: str) -> str:
        if name in self.name_map:
            return self.name_map[name]
        if name in self.global_names or name in dir(__builtins__):
            return name
        new_name = _random_name()
        self.name_map[name] = new_name
        return new_name

    def visit_Module(self, node: cst.Module) -> None:
        self.scope = set()
        return super().visit_Module(node)

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        return _insert_junk_function(updated_node)

    def leave_Import(self, original_node: cst.Import, updated_node: cst.Import) -> cst.Import:
        for alias in updated_node.names:
            if alias.asname:
                self.global_names.add(alias.asname.name.value)
            else:
                self.global_names.add(alias.name.value)
        return updated_node

    def leave_ImportFrom(self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom) -> cst.ImportFrom:
        for alias in updated_node.names:
            if alias.asname:
                self.global_names.add(alias.asname.name.value)
            else:
                self.global_names.add(alias.name.value)
        return updated_node

    def leave_Assign(self, original_node: cst.Assign, updated_node: cst.Assign) -> cst.Assign:
        new_targets = []
        for target in updated_node.targets:
            if isinstance(target.target, cst.Name):
                new_name = self._rename(target.target.value)
                new_target = target.with_changes(target=cst.Name(new_name))
                new_targets.append(new_target)
            else:
                new_targets.append(target)
        return updated_node.with_changes(targets=new_targets)

    def leave_AnnAssign(self, original_node: cst.AnnAssign, updated_node: cst.AnnAssign) -> cst.AnnAssign:
        if isinstance(updated_node.target, cst.Name):
            new_name = self._rename(updated_node.target.value)
            new_target = cst.Name(new_name)
            return updated_node.with_changes(target=new_target)
        return updated_node

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:
        if updated_node.value not in self.global_names:
            new_name = self._rename(updated_node.value)
            return updated_node.with_changes(value=new_name)
        return updated_node

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        new_func_name = self._rename(updated_node.name.value)
        new_params = []
        for param in updated_node.params.params:
            new_param_name = self._rename(param.name.value)
            new_params.append(param.with_changes(name=cst.Name(new_param_name)))
        
        new_body = self._remove_docstring(updated_node.body)
        new_body = self._insert_junk_into_body(new_body)

        return updated_node.with_changes(
            name=cst.Name(new_func_name),
            params=updated_node.params.with_changes(params=new_params),
            body=new_body,
        )

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        new_class_name = self._rename(updated_node.name.value)
        new_body = self._remove_docstring(updated_node.body)
        return updated_node.with_changes(
            name=cst.Name(new_class_name),
            body=new_body,
        )

    def leave_Lambda(self, original_node: cst.Lambda, updated_node: cst.Lambda) -> cst.Lambda:
        new_params = []
        for param in updated_node.params.params:
            new_param_name = self._rename(param.name.value)
            new_params.append(param.with_changes(name=cst.Name(new_param_name)))
        return updated_node.with_changes(
            params=updated_node.params.with_changes(params=new_params)
        )

    def _remove_docstring(self, body: cst.IndentedBlock) -> cst.IndentedBlock:
        new_body = list(body.body)
        if new_body and isinstance(new_body[0], cst.SimpleStatementLine):
            stmt = new_body[0].body[0]
            if isinstance(stmt, cst.Expr) and isinstance(stmt.value, cst.SimpleString):
                new_body.pop(0)
        return body.with_changes(body=new_body)

    def _insert_junk_into_body(self, body: cst.IndentedBlock) -> cst.IndentedBlock:
        if not body.body:
            return body
        new_body = []
        for stmt in body.body:
            new_body.append(stmt)
            if isinstance(stmt, cst.SimpleStatementLine) and random.random() < 0.2:
                junk_stmt_str = JUNK_STATEMENTS[random.randint(0, len(JUNK_STATEMENTS) - 1)]
                try:
                    junk_stmt = cst.parse_statement(junk_stmt_str)
                    new_body.append(junk_stmt)
                except Exception:
                    pass
        return body.with_changes(body=new_body)

    def leave_For(self, original_node: cst.For, updated_node: cst.For) -> cst.For:
        if isinstance(updated_node.target, cst.Name):
            new_name = self._rename(updated_node.target.value)
            new_target = cst.Name(new_name)
            return updated_node.with_changes(target=new_target)
        return updated_node

    def leave_With(self, original_node: cst.With, updated_node: cst.With) -> cst.With:
        new_items = []
        for item in updated_node.items:
            if item.asvar and isinstance(item.asvar, cst.Name):
                new_name = self._rename(item.asvar.value)
                new_asvar = cst.Name(new_name)
                new_items.append(item.with_changes(asvar=new_asvar))
            else:
                new_items.append(item)
        return updated_node.with_changes(items=new_items)

    def leave_ExceptHandler(self, original_node: cst.ExceptHandler, updated_node: cst.ExceptHandler) -> cst.ExceptHandler:
        if updated_node.name and isinstance(updated_node.name, cst.Name):
            new_name = self._rename(updated_node.name.value)
            return updated_node.with_changes(name=cst.Name(new_name))
        return updated_node


def obfuscate_code(source: str) -> str:
    try:
        tree = cst.parse_module(source)
        transformer = ObfuscateTransformer()
        modified_tree = tree.visit(transformer)
        return HEADER_COMMENT + modified_tree.code
    except Exception:
        return HEADER_COMMENT + source


def obfuscate_file(input_file: str, output_file: str) -> None:
    with open(input_file, "r", encoding="utf-8") as f:
        source = f.read()
    obfuscated = obfuscate_code(source)
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(obfuscated)


def obfuscate_directory(input_dir: str, output_dir: str) -> None:
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    for py_file in input_path.rglob("*.py"):
        rel_path = py_file.relative_to(input_path)
        out_file = output_path / rel_path
        obfuscate_file(str(py_file), str(out_file))
