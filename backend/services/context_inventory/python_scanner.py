"""Python Scanner component."""



from __future__ import annotations



import ast
import re
from collections import Counter
from pathlib import Path
from typing import (
    Any,
    Mapping,
    Sequence,
)



from .contracts import (
    _PYTHON_SOURCE_ROOTS,
    _EXCLUDED_DIR_NAMES,
    _BLOCKED_FILE_NAME_PARTS,
)



from .normalization import (
    _sha256_bytes,
    _sha256_value,
    _slug,
    _domain,
)



from .security import (
    _read_bytes,
    _read_text,
    _relative_ref,
    _safe_summary,
)



from .entities import (
    _finding,
    _entity,
)



def _literal_string(node: ast.AST | None, constants: Mapping[str, str] | None = None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and constants and node.id in constants:
        return constants[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string(node.left, constants)
        right = _literal_string(node.right, constants)
        return left + right if left or right else ""
    if isinstance(node, ast.JoinedStr):
        chunks: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                chunks.append(value.value)
            else:
                chunks.append("{dynamic}")
        return "".join(chunks)
    return ""

def _annotation_name(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""

def _module_from_path(path: Path, base: Path, *, schema: bool = False) -> str:
    rel = path.relative_to(base).with_suffix("").as_posix()
    if schema and rel.startswith("backend/schemas/"):
        return rel[len("backend/schemas/") :].replace("/", ".")
    if rel.startswith("backend/services/"):
        return rel[len("backend/services/") :].replace("/", ".")
    if rel.startswith("backend/modules/"):
        return rel[len("backend/modules/") :].replace("/", ".")
    if rel == "backend/lifecycle":
        return "lifecycle"
    return rel.replace("/", ".")

def _domain_from_source_path(path: Path, base: Path) -> str:
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        return _domain(path.stem)
    if len(parts) >= 3 and parts[0] == "backend" and parts[1] == "modules":
        return _domain(parts[2])
    return _domain(path.stem)

def _parse_python(path: Path, findings: list[dict[str, str]], source_ref: str) -> ast.Module | None:
    try:
        return ast.parse(_read_text(path), filename=source_ref)
    except (OSError, SyntaxError):
        findings.append(
            _finding(
                "python_parse_error",
                "warning",
                source_ref,
                "Arquivo Python nao pode ser analisado estaticamente.",
            )
        )
        return None

def _python_files(base: Path) -> list[Path]:
    files: set[Path] = set()
    for rel_root in _PYTHON_SOURCE_ROOTS:
        root = base / rel_root
        if root.is_dir():
            files.update(path for path in root.rglob("*.py") if not _path_is_excluded(path, root))
    lifecycle = base / "backend" / "lifecycle.py"
    if lifecycle.is_file():
        files.add(lifecycle)
    return sorted(files, key=lambda path: path.relative_to(base).as_posix())

def _path_is_excluded(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    if any(part in _EXCLUDED_DIR_NAMES or part.startswith(".venv") for part in parts[:-1]):
        return True
    lowered = path.name.lower()
    return any(part in lowered for part in _BLOCKED_FILE_NAME_PARTS)

def _scan_schemas(
    base: Path,
    surface: str,
    findings: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    candidates: list[tuple[Path, ast.Module, str]] = []
    schema_names: set[str] = set()
    roots = [base / "backend" / "schemas", base / "backend" / "modules"]
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if _path_is_excluded(path, root):
                continue
            ref = _relative_ref(path, base)
            tree = _parse_python(path, findings, ref)
            if tree is None:
                continue
            candidates.append((path, tree, ref))
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and any(
                    _annotation_name(base_node).split(".")[-1] == "BaseModel" for base_node in node.bases
                ):
                    schema_names.add(node.name)

    changed = True
    while changed:
        changed = False
        for _path, tree, _ref in candidates:
            for node in tree.body:
                if not isinstance(node, ast.ClassDef) or node.name in schema_names:
                    continue
                if any(_annotation_name(base_node).split(".")[-1] in schema_names for base_node in node.bases):
                    schema_names.add(node.name)
                    changed = True

    entities: list[dict[str, Any]] = []
    lookup: dict[str, str] = {}
    for path, tree, ref in candidates:
        module = _module_from_path(path, base, schema=True)
        domain = _domain(module.split(".", 1)[0])
        file_hash = _sha256_bytes(_read_bytes(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or node.name not in schema_names:
                continue
            fields: list[dict[str, str]] = []
            for child in node.body:
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    fields.append({"name": child.target.id, "type": _annotation_name(child.annotation)})
            entity_id = f"jk:schema:{module}.{node.name}"
            lookup.setdefault(node.name, entity_id)
            content_lines = [f"Schema Pydantic {node.name}."]
            if fields:
                content_lines.append("Campos: " + ", ".join(f"{row['name']}: {row['type']}" for row in fields))
            entities.append(
                _entity(
                    entity_id=entity_id,
                    kind="schema",
                    domain=domain,
                    title=node.name,
                    surface=surface,
                    tenant_scope="system",
                    sensitivity="internal",
                    truth_class="source",
                    source_refs=[ref],
                    source_hash=_sha256_value({"file": file_hash, "class": node.name, "fields": fields}),
                    metadata={"module": module, "line": node.lineno, "fields": fields},
                    content="\n".join(content_lines),
                )
            )
    return entities, lookup

def _scan_services(
    base: Path,
    surface: str,
    findings: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[tuple[Path, str, ast.Module]]]:
    entities: list[dict[str, Any]] = []
    parsed_files: list[tuple[Path, str, ast.Module]] = []
    for path in _python_files(base):
        ref = _relative_ref(path, base)
        tree = _parse_python(path, findings, ref)
        if tree is None:
            continue
        parsed_files.append((path, ref, tree))
        module = _module_from_path(path, base)
        domain = _domain(module.split(".", 1)[0])
        file_hash = _sha256_bytes(_read_bytes(path))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            name = node.name
            if name.startswith("_") and not name.startswith("_ia_tool_"):
                continue
            kind = "service_class" if isinstance(node, ast.ClassDef) else "service_function"
            if path.name == "lifecycle.py" and kind == "service_function":
                kind = "lifecycle_job"
            doc = ast.get_docstring(node) or ""
            summary = _safe_summary(doc.splitlines()[0] if doc else name.replace("_", " "))
            params: list[str] = []
            is_async = isinstance(node, ast.AsyncFunctionDef)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                params = [arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]]
            entity_id = f"jk:service:{module}.{name}"
            entities.append(
                _entity(
                    entity_id=entity_id,
                    kind=kind,
                    domain=domain,
                    title=name,
                    surface=surface,
                    tenant_scope="system",
                    sensitivity="internal",
                    truth_class="source",
                    source_refs=[ref],
                    source_hash=_sha256_value({"file": file_hash, "symbol": name, "kind": kind}),
                    metadata={
                        "module": module,
                        "line": node.lineno,
                        "async": is_async,
                        "parameters": params,
                    },
                    content=f"{summary}\nModulo: {module}.",
                )
            )
    return entities, parsed_files

def _scan_sql_schemas(
    parsed_files: Sequence[tuple[Path, str, ast.Module]],
    base: Path,
    surface: str,
) -> list[dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"`\]]?\s*\((.*?)\)",
        re.I | re.S,
    )
    for path, ref, tree in parsed_files:
        try:
            text = _read_text(path)
        except OSError:
            continue
        for match in pattern.finditer(text):
            table = match.group(1)
            columns: list[str] = []
            for fragment in match.group(2).split(","):
                column_match = re.match(r"\s*[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)", fragment)
                if not column_match:
                    continue
                name = column_match.group(1)
                if name.upper() not in {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}:
                    columns.append(name)
            row = tables.setdefault(table, {"paths": [], "columns": set(), "domains": []})
            row["paths"].append(path)
            row["columns"].update(columns)
            row["domains"].append(_domain(_module_from_path(path, base).split(".", 1)[0]))
    entities: list[dict[str, Any]] = []
    for table, row in sorted(tables.items()):
        refs = [_relative_ref(path, base) for path in row["paths"]]
        columns = sorted(row["columns"])
        domain = Counter(row["domains"]).most_common(1)[0][0] if row["domains"] else "dados"
        entities.append(
            _entity(
                entity_id=f"jk:data:{_slug(table)}",
                kind="data_schema",
                domain=domain,
                title=f"Tabela {table}",
                surface=surface,
                tenant_scope="client",
                sensitivity="schema_only",
                truth_class="source",
                source_refs=refs,
                source_hash=_sha256_value({"table": table, "columns": columns, "sources": sorted(refs)}),
                metadata={"table": table, "columns": columns, "rows_scanned": False},
                content=f"Schema declarado para {table}. Colunas: {', '.join(columns) or 'nao identificadas'}.",
            )
        )
    return entities
