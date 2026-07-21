"""Inventario semantico, puro e seguro para o Context Hub do JK Sistema.

O modulo deliberadamente nao importa ``backend_api`` e nao escreve no disco.
Ele inspeciona apenas codigo-fonte allowlisted, o registro secundario de
capacidades e os dossies canonicos de SKU do tenant solicitado.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


INVENTORY_SCHEMA_VERSION = 1
SKU_SCHEMA_VERSION = 2

ENTITY_KEYS = (
    "id",
    "kind",
    "domain",
    "title",
    "surface",
    "tenant_scope",
    "sensitivity",
    "truth_class",
    "source_refs",
    "source_hash",
    "relationships",
    "metadata",
    "content",
)

_PYTHON_SOURCE_ROOTS = (
    "backend/services",
    "backend/modules",
)
_INFRA_ROOTS: tuple[tuple[str, str, str], ...] = (
    ("electron", "Electron", "electron_app"),
    ("android", "Android", "android_app"),
    ("chrome-extensions", "Extensoes Chrome", "extensoes_chrome"),
    ("cloudflare", "Cloudflare", "cloudflare"),
    ("cloudflare-workers", "Cloudflare Workers", "cloudflare_workers"),
    ("infra", "Infraestrutura", "infra"),
)
_INFRA_SUFFIXES = {
    ".bat",
    ".css",
    ".gradle",
    ".html",
    ".java",
    ".js",
    ".json",
    ".kt",
    ".md",
    ".nsh",
    ".ps1",
    ".py",
    ".sql",
    ".toml",
    ".ts",
    ".yaml",
    ".yml",
}
_EXCLUDED_DIR_NAMES = {
    ".git",
    ".obsidian",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "dist-client-setup",
    "logs",
    "node_modules",
    "test-results",
    "context_hub",
    "ContextVault",
}
_BLOCKED_FILE_NAME_PARTS = (
    ".env",
    "credential",
    "credencial",
    "private-key",
    "private_key",
    "secret",
    "token",
)

_SKU_REQUIRED_KEYS = {
    "schema_version",
    "sku",
    "nome_produto",
    "o_que_e",
    "para_que_serve",
    "caracteristicas_tecnicas",
    "medidas_do_produto",
    "modo_de_funcionamento",
    "instalacao",
    "oem",
    "aplicacao",
    "revisao",
    "atualizado_em",
}
_SKU_FORBIDDEN_KEY_PARTS = {
    "access_token",
    "address",
    "buyer",
    "client_secret",
    "comprador",
    "cpf",
    "cnpj",
    "email",
    "endereco",
    "fonte",
    "jwt",
    "oauth",
    "password",
    "refresh_token",
    "senha",
    "telefone",
    "token",
    "url",
}

_SENSITIVE_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:access[_-]?token|refresh[_-]?token|client[_-]?secret|api[_-]?key|password|senha)\b\s*[:=]\s*[\"']?[^\s\"']{8,}"
        ),
    ),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("cpf", re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")),
)
_SKU_SENSITIVE_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    pattern for _code, pattern in _SENSITIVE_VALUE_PATTERNS
) + (
    re.compile(r"(?i)(?:\+?55\s*)?\([1-9]{2}\)\s*9?\d{4}[\s.-]*\d{4}"),
    re.compile(r"(?i)\b(?:telefone|whatsapp|celular)\b\s*[:=]\s*\+?[\d(). -]{8,}"),
    re.compile(r"(?<!\d)\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}(?!\d)"),
)

_ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
_DOMAIN_ALIASES = {
    "admin_usuarios": "admin",
    "auth": "admin",
    "codex": "ia",
    "codex_console": "ia",
    "configuracoes": "admin",
    "frontend": "admin",
    "ia": "ia",
    "importacoes": "medias-compras",
    "mercado_livre": "mercado-livre",
    "perguntas_pos_venda": "mercado-livre",
    "promocoes": "mercado-livre",
    "shared_sync": "shared-sync",
    "whatsapp": "whatsapp",
    "whatsapp_bridge": "whatsapp",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_value(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _read_text(path: Path) -> str:
    # ``py_compile`` aceita BOM UTF-8, mas ``ast.parse`` recebe o caractere se
    # o arquivo for aberto como UTF-8 simples. Remover somente o BOM inicial
    # mantem a leitura equivalente ao importador do Python.
    return path.read_text(encoding="utf-8", errors="strict").lstrip("\ufeff")


def _strip_accents(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char))


def _slug(value: Any, *, fallback: str = "item") -> str:
    text = _strip_accents(value).lower().strip()
    text = re.sub(r"[^a-z0-9._-]+", "-", text)
    text = re.sub(r"[-_.]{2,}", "-", text).strip("-._")
    return text or fallback


def _normal_key(value: Any) -> str:
    return _slug(value, fallback="").replace("-", "_")


def _normal_api_path(value: str) -> str:
    path = str(value or "").strip().split("?", 1)[0]
    path = re.sub(r"\$\{[^}]+\}", "{dynamic}", path)
    # Starlette accepts converters such as ``{filename:path}``, while the
    # generated OpenAPI contract exposes the same parameter as ``{filename}``.
    # Stable API IDs follow the public OpenAPI spelling.
    path = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*):[^{}]+\}", r"{\1}", path)
    path = re.sub(r"/+", "/", path)
    if path != "/":
        path = path.rstrip("/")
    return path


def _domain(value: Any) -> str:
    raw = _slug(value, fallback="sistema").replace("-py", "")
    raw_under = raw.replace("-", "_").replace(".", "_")
    if raw_under in _DOMAIN_ALIASES:
        return _DOMAIN_ALIASES[raw_under]
    searchable = raw.replace("_", "-").replace(".", "-")
    canonical = {
        "admin",
        "cadastro",
        "documentacao",
        "estoque",
        "etiquetas",
        "favoritos",
        "fiscal",
        "ia",
        "integracoes",
        "medias-compras",
        "mercado-livre",
        "plataforma",
        "renovacao",
        "reuniao",
        "shared-sync",
        "sistema",
        "vendas",
        "whatsapp",
    }
    if searchable in canonical:
        return searchable
    groups: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("whatsapp", ("whatsapp",)),
        ("shared-sync", ("shared-sync", "drive-sync")),
        ("ia", ("codex", "-ia-", "ia-", "ia-sidebar", "gemini", "openai", "assistant")),
        (
            "mercado-livre",
            (
                "mercado-livre",
                "mercadolivre",
                "anunciosml",
                "perguntas-pos-venda",
                "ml-questions",
                "ml-pos-venda",
                "ml-product",
                "promoc",
                "promo",
                "full",
                "pesquisa-ml",
            ),
        ),
        ("vendas", ("venda", "devolu", "notas-entrada", "unidades-negocios")),
        ("estoque", ("estoque", "stock")),
        ("favoritos", ("favorito", "avant")),
        ("medias-compras", ("medias-compras", "importacoes", "importar-colunas")),
        ("fiscal", ("imposto", "fiscal", "monofasico", "siscomex", "transito", "simulador")),
        ("renovacao", ("renovacao",)),
        ("etiquetas", ("etiqueta", "qrcode")),
        ("reuniao", ("sala-reuniao", "rustdesk", "daily")),
        ("cadastro", ("cadastro", "produto", "foto", "ncm", "sku", "catalog")),
        ("integracoes", ("integracoes", "bling", "lojas", "oauth")),
        (
            "plataforma",
            (
                "electron",
                "android",
                "chrome-extension",
                "cloudflare",
                "firebase",
                "infra",
                "runtime",
                "mobile-update",
                "app-update",
                "package",
                "provision-python",
            ),
        ),
        (
            "admin",
            (
                "admin",
                "auth",
                "configur",
                "permission",
                "presence",
                "client-id",
                "dashboard",
                "global-ui",
                "frontend-index",
            ),
        ),
        ("documentacao", ("document", "guide", "readme")),
    )
    padded = f"-{searchable}-"
    for target, markers in groups:
        if any(marker in searchable or marker in padded for marker in markers):
            return target
    return "sistema"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_resolve(path: Path, root: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not _is_relative_to(resolved, root_resolved):
        return None
    return resolved


def _relative_ref(path: Path, base: Path, *, fallback: str = "") -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except (OSError, ValueError):
        return fallback or path.name


def _source_hash(paths: Sequence[Path], base: Path) -> str:
    rows: list[dict[str, str]] = []
    for path in sorted(set(paths), key=lambda item: _relative_ref(item, base, fallback=item.name)):
        try:
            rows.append(
                {
                    "path": _relative_ref(path, base, fallback=path.name),
                    "sha256": _sha256_bytes(_read_bytes(path)),
                }
            )
        except OSError:
            rows.append({"path": _relative_ref(path, base, fallback=path.name), "sha256": "unreadable"})
    return _sha256_value(rows)


def _safe_summary(value: Any, *, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    for _code, pattern in _SENSITIVE_VALUE_PATTERNS:
        if pattern.search(text):
            return "Conteudo omitido pela politica de seguranca."
    return text[:limit]


def _safe_content(value: Any, *, limit: int = 100_000) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    for _code, pattern in _SENSITIVE_VALUE_PATTERNS:
        if pattern.search(text):
            return "Conteudo omitido pela politica de seguranca."
    return text[:limit]


def _finding(
    code: str,
    severity: str,
    source_ref: str,
    message: str,
    *,
    entity_id: str = "",
) -> dict[str, str]:
    result = {
        "code": _slug(code),
        "severity": str(severity),
        "source_ref": str(source_ref),
        "message": _safe_summary(message, limit=240),
    }
    if entity_id:
        result["entity_id"] = str(entity_id)
    return result


def _entity(
    *,
    entity_id: str,
    kind: str,
    domain: str,
    title: str,
    surface: str,
    tenant_scope: str,
    sensitivity: str,
    truth_class: str,
    source_refs: Sequence[str],
    source_hash: str,
    relationships: Sequence[Mapping[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
    content: str = "",
) -> dict[str, Any]:
    result = {
        "id": str(entity_id),
        "kind": str(kind),
        "domain": _domain(domain),
        "title": _safe_summary(title, limit=180) or str(entity_id),
        "surface": str(surface),
        "tenant_scope": str(tenant_scope),
        "sensitivity": str(sensitivity),
        "truth_class": str(truth_class),
        "source_refs": sorted({str(ref).replace("\\", "/") for ref in source_refs if str(ref).strip()}),
        "source_hash": str(source_hash),
        "relationships": sorted(
            [dict(item) for item in (relationships or [])],
            key=lambda item: (str(item.get("type") or ""), str(item.get("target_id") or "")),
        ),
        "metadata": dict(metadata or {}),
        "content": _safe_content(content),
    }
    return result


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


def _router_prefixes(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    constants: dict[str, str] = {}
    routers: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            for target in targets:
                if isinstance(target, ast.Name):
                    literal = _literal_string(value, constants)
                    if literal:
                        constants[target.id] = literal
            if isinstance(value, ast.Call):
                called = _annotation_name(value.func).split(".")[-1]
                if called == "APIRouter":
                    prefix = ""
                    for keyword in value.keywords:
                        if keyword.arg == "prefix":
                            prefix = _literal_string(keyword.value, constants)
                    for target in targets:
                        if isinstance(target, ast.Name):
                            routers[target.id] = prefix
    return constants, routers


def _all_router_prefixes(tree: ast.Module, constants: Mapping[str, str]) -> list[str]:
    prefixes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _annotation_name(node.func).split(".")[-1] != "APIRouter":
            continue
        for keyword in node.keywords:
            if keyword.arg == "prefix":
                prefix = _literal_string(keyword.value, constants)
                if prefix:
                    prefixes.add(_normal_api_path(prefix))
    return sorted(prefixes)


def _route_id(method: str, path: str) -> str:
    return f"jk:api:{method.upper()}:{path}"


def _scan_routes(
    base: Path,
    surface: str,
    schema_lookup: Mapping[str, str],
    findings: list[dict[str, str]],
) -> list[dict[str, Any]]:
    files: list[Path] = []
    backend_api = base / "backend_api.py"
    if backend_api.is_file():
        files.append(backend_api)
    router_root = base / "backend" / "routers"
    if router_root.is_dir():
        files.extend(sorted(router_root.rglob("*.py")))
    modules_root = base / "backend" / "modules"
    if modules_root.is_dir():
        files.extend(sorted(modules_root.rglob("router*.py")))

    routes: dict[tuple[str, str], dict[str, Any]] = {}
    for path in files:
        if _path_is_excluded(path, base):
            continue
        ref = _relative_ref(path, base)
        tree = _parse_python(path, findings, ref)
        if tree is None:
            continue
        constants, routers = _router_prefixes(tree)
        declared_prefixes = _all_router_prefixes(tree, constants)
        file_hash = _sha256_bytes(_read_bytes(path))
        domain = _domain_from_source_path(path, base)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                method = decorator.func.attr.lower()
                if method not in _ROUTE_METHODS:
                    continue
                owner = _annotation_name(decorator.func.value)
                prefix = routers.get(owner, "")
                raw_path = _literal_string(decorator.args[0], constants) if decorator.args else ""
                if not raw_path and method:
                    raw_path = "/"
                if not prefix and raw_path and not raw_path.startswith(("/api/", "/auth/", "/webhooks/")):
                    non_empty = [item for item in declared_prefixes if item]
                    if len(set(non_empty)) == 1:
                        prefix = non_empty[0]
                if "{dynamic}" in raw_path:
                    findings.append(
                        _finding(
                            "dynamic_route_path",
                            "warning",
                            ref,
                            "Rota dinamica requer revisao manual.",
                        )
                    )
                route_path = _normal_api_path(prefix + raw_path)
                if not route_path.startswith("/"):
                    continue
                schema_ids: set[str] = set()
                for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                    annotation = _annotation_name(arg.annotation)
                    for schema_name, schema_id in schema_lookup.items():
                        if re.search(rf"\b{re.escape(schema_name)}\b", annotation):
                            schema_ids.add(schema_id)
                for keyword in decorator.keywords:
                    if keyword.arg == "response_model":
                        annotation = _annotation_name(keyword.value)
                        for schema_name, schema_id in schema_lookup.items():
                            if re.search(rf"\b{re.escape(schema_name)}\b", annotation):
                                schema_ids.add(schema_id)
                key = (method.upper(), route_path)
                route = _entity(
                    entity_id=_route_id(*key),
                    kind="api",
                    domain=domain,
                    title=f"{key[0]} {route_path}",
                    surface=surface,
                    tenant_scope="request",
                    sensitivity="internal",
                    truth_class="source",
                    source_refs=[ref],
                    source_hash=_sha256_value(
                        {"file": file_hash, "method": key[0], "path": route_path, "endpoint": node.name}
                    ),
                    relationships=[{"type": "accepts", "target_id": value} for value in sorted(schema_ids)],
                    metadata={
                        "method": key[0],
                        "path": route_path,
                        "endpoint": node.name,
                        "line": node.lineno,
                    },
                    content=f"Rota {key[0]} {route_path}, implementada por {node.name}.",
                )
                existing = routes.get(key)
                if existing is None or ref < existing["source_refs"][0]:
                    routes[key] = route

        # Alguns routers migrados mantem a tabela declarativa como fonte da
        # verdade e registram ``spec.path``/``spec.method`` em um loop. Ler os
        # literais evita importar backend_api e cobre essas rotas por completo.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _annotation_name(node.func).split(".")[-1] != "LegacyRouteSpec":
                continue
            if len(node.args) < 3:
                continue
            method = _literal_string(node.args[0], constants).upper()
            raw_path = _literal_string(node.args[1], constants)
            endpoint = _literal_string(node.args[2], constants)
            response_model = _literal_string(node.args[3], constants) if len(node.args) >= 4 else ""
            if method not in {item.upper() for item in _ROUTE_METHODS} or not raw_path:
                continue
            prefix = ""
            if not raw_path.startswith("/api/") and not raw_path.startswith("/auth/") and not raw_path.startswith("/webhooks/"):
                non_empty = [item for item in declared_prefixes if item]
                if len(set(non_empty)) == 1:
                    prefix = non_empty[0]
            route_path = _normal_api_path(prefix + raw_path)
            schema_id = schema_lookup.get(response_model, "")
            key = (method, route_path)
            routes.setdefault(
                key,
                _entity(
                    entity_id=_route_id(*key),
                    kind="api",
                    domain=domain,
                    title=f"{method} {route_path}",
                    surface=surface,
                    tenant_scope="request",
                    sensitivity="internal",
                    truth_class="source",
                    source_refs=[ref],
                    source_hash=_sha256_value(
                        {"file": file_hash, "method": method, "path": route_path, "endpoint": endpoint}
                    ),
                    relationships=[{"type": "accepts", "target_id": schema_id}] if schema_id else [],
                    metadata={
                        "method": method,
                        "path": route_path,
                        "endpoint": endpoint,
                        "line": node.lineno,
                        "declaration": "LegacyRouteSpec",
                    },
                    content=f"Rota {method} {route_path}, implementada por {endpoint}.",
                ),
            )

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "add_api_route" or not node.args:
                continue
            raw_path = _literal_string(node.args[0], constants)
            if not raw_path:
                continue
            methods = ["GET"]
            for keyword in node.keywords:
                if keyword.arg == "methods" and isinstance(keyword.value, (ast.List, ast.Tuple, ast.Set)):
                    parsed = [_literal_string(item, constants).upper() for item in keyword.value.elts]
                    methods = [item for item in parsed if item in {method.upper() for method in _ROUTE_METHODS}] or methods
            owner = _annotation_name(node.func.value)
            prefix = routers.get(owner, "")
            if not prefix and not raw_path.startswith(("/api/", "/auth/", "/webhooks/")):
                non_empty = [item for item in declared_prefixes if item]
                if len(set(non_empty)) == 1:
                    prefix = non_empty[0]
            route_path = _normal_api_path(prefix + raw_path)
            endpoint = ""
            if len(node.args) >= 2:
                endpoint = getattr(node.args[1], "attr", "") or getattr(node.args[1], "id", "") or ""
            for method in methods:
                key = (method, route_path)
                routes.setdefault(
                    key,
                    _entity(
                        entity_id=_route_id(*key),
                        kind="api",
                        domain=domain,
                        title=f"{method} {route_path}",
                        surface=surface,
                        tenant_scope="request",
                        sensitivity="internal",
                        truth_class="source",
                        source_refs=[ref],
                        source_hash=_sha256_value(
                            {"file": file_hash, "method": method, "path": route_path, "endpoint": endpoint}
                        ),
                        metadata={"method": method, "path": route_path, "endpoint": endpoint, "line": node.lineno},
                        content=f"Rota {method} {route_path} registrada estaticamente por {endpoint or 'endpoint'}.",
                    ),
                )
    return sorted(routes.values(), key=lambda item: item["id"])


def _link_implementation_relationships(
    routes: list[dict[str, Any]],
    services: list[dict[str, Any]],
    data_schemas: list[dict[str, Any]],
) -> None:
    """Liga entidades por simbolo/arquivo sem executar codigo da aplicacao."""

    services_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    services_by_ref: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for service in services:
        services_by_name[str(service.get("title") or "")].append(service)
        for ref in service.get("source_refs", []):
            services_by_ref[str(ref)].append(service)
    for route in routes:
        endpoint = str(route.get("metadata", {}).get("endpoint") or "")
        candidates = services_by_name.get(endpoint, [])
        if len(candidates) != 1:
            continue
        service = candidates[0]
        route["relationships"].append({"type": "implemented_by", "target_id": service["id"]})
        service["relationships"].append({"type": "implements", "target_id": route["id"]})
    for data_schema in data_schemas:
        linked: dict[str, dict[str, Any]] = {}
        for ref in data_schema.get("source_refs", []):
            for service in services_by_ref.get(str(ref), []):
                linked[service["id"]] = service
        for service in linked.values():
            service["relationships"].append({"type": "accesses", "target_id": data_schema["id"]})
            data_schema["relationships"].append({"type": "declared_by", "target_id": service["id"]})
    for entity in [*routes, *services, *data_schemas]:
        entity["relationships"] = sorted(
            {_canonical_json(row): row for row in entity["relationships"]}.values(),
            key=lambda row: (str(row.get("type") or ""), str(row.get("target_id") or "")),
        )


def compare_context_inventory_openapi(
    inventory: Mapping[str, Any], openapi_schema: Mapping[str, Any]
) -> dict[str, Any]:
    """Compara o inventario AST com um OpenAPI fornecido pelo chamador.

    A funcao nao cria/importa a aplicacao. O processo que ja possui uma
    instancia FastAPI pode passar ``app.openapi()`` com seguranca.
    """

    ast_routes = {
        (
            str(entity.get("metadata", {}).get("method") or "").upper(),
            _normal_api_path(str(entity.get("metadata", {}).get("path") or "")),
        )
        for entity in inventory.get("entities", [])
        if isinstance(entity, dict) and entity.get("kind") == "api"
    }
    openapi_routes: set[tuple[str, str]] = set()
    paths = openapi_schema.get("paths") if isinstance(openapi_schema, Mapping) else {}
    if isinstance(paths, Mapping):
        for path, operations in paths.items():
            if not isinstance(operations, Mapping):
                continue
            for method in operations:
                method_upper = str(method).upper()
                if method_upper in {item.upper() for item in _ROUTE_METHODS}:
                    openapi_routes.add((method_upper, _normal_api_path(str(path))))
    missing_from_inventory = sorted(openapi_routes - ast_routes)
    static_only = sorted(ast_routes - openapi_routes)
    return {
        "inventory_routes": len(ast_routes),
        "openapi_routes": len(openapi_routes),
        "matched_routes": len(ast_routes & openapi_routes),
        "missing_from_inventory": [
            {"method": method, "path": path} for method, path in missing_from_inventory
        ],
        "static_only": [{"method": method, "path": path} for method, path in static_only],
        "complete": not missing_from_inventory,
    }


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


def _title_from_html(text: str, fallback: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if match:
        return _safe_summary(re.sub(r"<[^>]+>", " ", match.group(1)), limit=180) or fallback
    return fallback


def _script_refs(html_text: str) -> list[str]:
    return sorted(
        {
            match.group(1).strip()
            for match in re.finditer(r"<script[^>]+src=[\"']([^\"']+)[\"']", html_text, re.I)
            if match.group(1).strip()
        }
    )


def _resolve_static_script(static_root: Path, page: Path, source: str) -> Path | None:
    clean = source.split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
    if not clean or re.match(r"^[a-z]+://", clean, re.I) or clean.startswith("//"):
        return None
    if clean.startswith("/static/"):
        candidate = static_root / clean[len("/static/") :]
    elif clean.startswith("/"):
        candidate = static_root / clean.lstrip("/")
    else:
        candidate = page.parent / clean
    return _safe_resolve(candidate, static_root)


def _extract_api_literals(text: str) -> set[str]:
    values: set[str] = set()
    quoted = re.compile(r"(?P<quote>[\"'`])(?P<path>/api(?:/[^\"'`\s<>)]*)?)(?P=quote)")
    for match in quoted.finditer(text):
        value = match.group("path").rstrip(".,;:")
        if value:
            values.add(value)
    return values


def _method_near(text: str, value: str) -> str:
    index = text.find(value)
    if index < 0:
        return "UNKNOWN"
    before = text[max(0, index - 80) : index].lower()
    after = text[index : index + 240].lower()
    axios = re.search(r"(?:axios|api)\.(get|post|put|patch|delete)\s*\($", before)
    if axios:
        return axios.group(1).upper()
    option = re.search(r"\bmethod\s*:\s*[\"'](get|post|put|patch|delete)[\"']", after)
    if option:
        return option.group(1).upper()
    return "GET" if re.search(r"\bfetch\s*\($", before) else "UNKNOWN"


def _route_match(path: str, method: str, routes: Sequence[dict[str, Any]]) -> tuple[str, str]:
    normalized = _normal_api_path(path)
    if "{dynamic}" in normalized or "${" in path:
        return "dynamic", ""
    candidates = routes
    if method != "UNKNOWN":
        candidates = [item for item in routes if item.get("metadata", {}).get("method") == method]
    for route in candidates:
        route_path = str(route.get("metadata", {}).get("path") or "")
        if normalized == route_path:
            return "valid", str(route["id"])
    for route in candidates:
        route_path = str(route.get("metadata", {}).get("path") or "")
        regex = "^" + re.sub(r"\{[^}/]+\}", r"[^/]+", re.escape(route_path).replace(r"\{", "{").replace(r"\}", "}")) + "$"
        try:
            if re.match(regex, normalized):
                return "alias", str(route["id"])
        except re.error:
            continue
    return "orphan", ""


def _scan_frontend(
    base: Path,
    surface: str,
    routes: Sequence[dict[str, Any]],
    findings: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    static_root = base / "static"
    if not static_root.is_dir():
        return [], []
    screens: list[dict[str, Any]] = []
    source_to_screens: dict[Path, set[str]] = defaultdict(set)
    text_by_source: dict[Path, str] = {}
    for page in sorted(static_root.glob("*.html")):
        resolved = _safe_resolve(page, static_root)
        if resolved is None:
            findings.append(_finding("unsafe_static_path", "blocker", page.name, "Tela fora da raiz canonica."))
            continue
        text = _read_text(resolved)
        ref = _relative_ref(resolved, base)
        title = _title_from_html(text, page.stem.replace("_", " ").title())
        screen_id = f"jk:screen:{_slug(page.stem)}"
        scripts: list[str] = []
        source_to_screens[resolved].add(screen_id)
        text_by_source[resolved] = text
        for raw_script in _script_refs(text):
            script = _resolve_static_script(static_root, resolved, raw_script)
            if script is None or script.suffix.lower() != ".js":
                continue
            scripts.append(_relative_ref(script, base))
            source_to_screens[script].add(screen_id)
            text_by_source.setdefault(script, _read_text(script))
        screens.append(
            _entity(
                entity_id=screen_id,
                kind="screen",
                domain=_domain(page.stem),
                title=title,
                surface=surface,
                tenant_scope="system",
                sensitivity="internal",
                truth_class="source",
                source_refs=[ref, *scripts],
                source_hash=_source_hash([resolved, *[base / script for script in scripts]], base),
                metadata={"path": ref, "scripts": sorted(scripts)},
                content=f"Tela canonica {title}. Fonte: {ref}.",
            )
        )

    for script in sorted(static_root.rglob("*.js")):
        if _path_is_excluded(script, static_root):
            continue
        resolved = _safe_resolve(script, static_root)
        if resolved is not None:
            text_by_source.setdefault(resolved, _read_text(resolved))

    calls: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for source, text in sorted(text_by_source.items(), key=lambda row: _relative_ref(row[0], base)):
        ref = _relative_ref(source, base)
        for api_path in sorted(_extract_api_literals(text)):
            method = _method_near(text, api_path)
            key = (ref, method, api_path)
            if key in seen:
                continue
            seen.add(key)
            classification, route_id = _route_match(api_path, method, routes)
            digest = hashlib.sha256(f"{ref}|{method}|{api_path}".encode("utf-8")).hexdigest()[:16]
            relationships: list[dict[str, str]] = []
            for screen_id in sorted(source_to_screens.get(source, set())):
                relationships.append({"type": "called_by", "target_id": screen_id})
            if route_id:
                relationships.append({"type": "calls", "target_id": route_id})
            entity_id = f"jk:frontend-call:{digest}"
            calls.append(
                _entity(
                    entity_id=entity_id,
                    kind="frontend_api_call",
                    domain=_domain(ref),
                    title=f"{method} {_normal_api_path(api_path)}",
                    surface=surface,
                    tenant_scope="request",
                    sensitivity="internal",
                    truth_class="source",
                    source_refs=[ref],
                    source_hash=_sha256_value({"source": _sha256_bytes(_read_bytes(source)), "method": method, "path": api_path}),
                    relationships=relationships,
                    metadata={
                        "method": method,
                        "path": api_path,
                        "classification": classification,
                    },
                    content=f"Chamada frontend classificada como {classification}: {method} {_normal_api_path(api_path)}.",
                )
            )
            if classification == "orphan":
                findings.append(
                    _finding(
                        "frontend_api_orphan",
                        "warning",
                        ref,
                        "Referencia de API sem rota estatica correspondente.",
                        entity_id=entity_id,
                    )
                )
    return screens, calls


def _scan_tests(base: Path, surface: str) -> list[dict[str, Any]]:
    candidates: set[Path] = set()
    tests_root = base / "tests"
    if tests_root.is_dir():
        candidates.update(tests_root.rglob("test_*.py"))
        candidates.update(tests_root.rglob("*.test.js"))
        candidates.update(tests_root.rglob("*.spec.js"))
    for root_rel in ("scripts", "cloudflare"):
        root = base / root_rel
        if root.is_dir():
            candidates.update(path for path in root.rglob("test*.js") if not _path_is_excluded(path, root))
            candidates.update(path for path in root.rglob("*.test.js") if not _path_is_excluded(path, root))
    entities: list[dict[str, Any]] = []
    for path in sorted(candidates, key=lambda item: _relative_ref(item, base)):
        if _path_is_excluded(path, base):
            continue
        ref = _relative_ref(path, base)
        file_hash = _sha256_bytes(_read_bytes(path))
        names: list[tuple[str, int]] = []
        if path.suffix.lower() == ".py":
            try:
                tree = ast.parse(_read_text(path))
                names = [
                    (node.name, int(node.lineno))
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
                ]
            except SyntaxError:
                names = []
        else:
            text = _read_text(path)
            names = [
                (match.group(2), text.count("\n", 0, match.start()) + 1)
                for match in re.finditer(r"\b(test|it)\s*\(\s*[\"'`]([^\"'`]+)[\"'`]", text)
            ]
        if not names:
            names = [(path.stem, 1)]
        inferred = _domain(ref)
        for name, line in names:
            entity_id = f"jk:test:{ref}#{_slug(name)}"
            entities.append(
                _entity(
                    entity_id=entity_id,
                    kind="test",
                    domain=inferred,
                    title=name,
                    surface=surface,
                    tenant_scope="system",
                    sensitivity="internal",
                    truth_class="source",
                    source_refs=[ref],
                    source_hash=_sha256_value({"file": file_hash, "test": name}),
                    relationships=[{"type": "verifies", "target_id": f"jk:domain:{inferred}"}],
                    metadata={"line": line, "framework": "pytest" if path.suffix == ".py" else "node"},
                    content=f"Teste {name}, em {ref}.",
                )
            )
    return entities


def _iter_infra_files(root: Path) -> list[Path]:
    files: list[Path] = []
    if not root.is_dir():
        return files
    for path in root.rglob("*"):
        if not path.is_file() or _path_is_excluded(path, root):
            continue
        if path.suffix.lower() in _INFRA_SUFFIXES or path.name in {"Dockerfile", "package.json"}:
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _scan_integrations(base: Path, surface: str) -> list[dict[str, Any]]:
    specs: list[tuple[str, str, list[Path]]] = []
    for slug, title, root_rel in _INFRA_ROOTS:
        files = _iter_infra_files(base / root_rel)
        if files:
            specs.append((slug, title, files))
    fixed: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("mercado-livre", "Mercado Livre", ("backend/routers/mercado_livre.py",)),
        ("bling", "Bling", ("backend/routers/integracoes.py", "integracoes.py")),
        ("whatsapp", "WhatsApp", ("backend/routers/whatsapp_bridge.py", "backend/services/whatsapp")),
        ("ia-providers", "Provedores de IA", ("backend/services/ia_providers.py",)),
        ("daily-rustdesk", "Daily e RustDesk", ("backend/routers/sala_reuniao.py", "static/rustdesk.html")),
    )
    for slug, title, refs in fixed:
        files: list[Path] = []
        for rel in refs:
            path = base / rel
            if path.is_file() and not _path_is_excluded(path, base):
                files.append(path)
            elif path.is_dir():
                files.extend(
                    item
                    for item in path.rglob("*.py")
                    if item.is_file() and not _path_is_excluded(item, path)
                )
        if files:
            specs.append((slug, title, sorted(set(files))))

    entities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for slug, title, files in specs:
        if slug in seen:
            continue
        seen.add(slug)
        refs = [_relative_ref(path, base) for path in files]
        entities.append(
            _entity(
                entity_id=f"jk:integration:{slug}",
                kind="integration",
                domain=_domain(slug),
                title=title,
                surface=surface,
                tenant_scope="system",
                sensitivity="internal",
                truth_class="source",
                source_refs=refs[:250],
                source_hash=_source_hash(files, base),
                metadata={"source_count": len(files), "source_refs_truncated": len(refs) > 250},
                content=f"Superficie de integracao {title}, descrita por {len(files)} arquivos-fonte allowlisted.",
            )
        )
    return entities


def _scan_capabilities(
    base: Path,
    surface: str,
    client_id: str,
    routes: Sequence[dict[str, Any]],
    findings: list[dict[str, str]],
) -> list[dict[str, Any]]:
    path = base / "info" / "codex_console" / "capabilities.json"
    if not path.is_file() or path.is_symlink():
        return []
    ref = _relative_ref(path, base)
    try:
        payload = json.loads(_read_text(path))
    except (OSError, json.JSONDecodeError):
        findings.append(_finding("capabilities_invalid", "warning", ref, "Registro de capacidades invalido."))
        return []
    payload_client = str(payload.get("client_id") or "") if isinstance(payload, dict) else ""
    if payload_client and payload_client != client_id:
        findings.append(
            _finding(
                "capabilities_tenant_mismatch",
                "warning",
                ref,
                "Registro secundario pertence a outro tenant e foi ignorado.",
            )
        )
        return []
    rows = payload.get("capabilities") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    route_lookup = {
        (str(item.get("metadata", {}).get("method") or ""), str(item.get("metadata", {}).get("path") or "")): item["id"]
        for item in routes
    }
    file_hash = _sha256_bytes(_read_bytes(path))
    entities: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        capability_id = _slug(row.get("id"), fallback="")
        if not capability_id:
            continue
        method = str(row.get("method") or "").upper()
        route_path = _normal_api_path(str(row.get("route_path") or ""))
        relationships: list[dict[str, str]] = []
        route_id = route_lookup.get((method, route_path))
        if route_id:
            relationships.append({"type": "describes", "target_id": route_id})
        safe_row = {
            "id": str(row.get("id") or ""),
            "module": str(row.get("module") or "sistema"),
            "category": str(row.get("category") or ""),
            "title": str(row.get("title") or row.get("id") or ""),
            "route_path": route_path,
            "method": method,
            "read_only": bool(row.get("read_only")),
            "mutating": bool(row.get("mutating")),
        }
        entities.append(
            _entity(
                entity_id=f"jk:capability:{capability_id}",
                kind="capability",
                domain=_domain(safe_row["module"]),
                title=safe_row["title"],
                surface=surface,
                tenant_scope="client",
                sensitivity="internal",
                truth_class="generated_secondary",
                source_refs=[ref],
                source_hash=_sha256_value({"file": file_hash, "capability": safe_row}),
                relationships=relationships,
                metadata=safe_row,
                content=f"Capacidade {safe_row['title']} ({safe_row['category']}).",
            )
        )
    return entities


def _contains_forbidden_sku_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _normal_key(key)
            if any(part in normalized for part in _SKU_FORBIDDEN_KEY_PARTS):
                return True
            if _contains_forbidden_sku_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_sku_key(item) for item in value)
    return False


def _contains_sensitive_sku_value(value: Mapping[str, Any]) -> bool:
    serialized = _canonical_json(value)
    return any(pattern.search(serialized) for pattern in _SKU_SENSITIVE_VALUE_PATTERNS)


def _sku_section(data: Mapping[str, Any], normalized_name: str) -> Any:
    for key, value in data.items():
        if _normal_key(key) == normalized_name:
            return value
    return None


def _sku_top_keys(data: Mapping[str, Any]) -> set[str]:
    return {_normal_key(key) for key in data}


def _sku_items(section: Any, field: str = "itens") -> list[Any]:
    if not isinstance(section, dict):
        return []
    for key, value in section.items():
        if _normal_key(key) == field and isinstance(value, list):
            return value
    return []


def _sku_field(section: Any, field: str, default: Any = "") -> Any:
    if not isinstance(section, dict):
        return default
    for key, value in section.items():
        if _normal_key(key) == field:
            return value
    return default


def _sku_family(name: str, application_type: str) -> str:
    stop = {"a", "as", "com", "da", "das", "de", "do", "dos", "e", "em", "para", "por"}
    words = [word for word in _slug(name).split("-") if word and word not in stop]
    noun = "-".join(words[:2]) or "produto"
    return f"{_slug(application_type, fallback='geral')}:{noun}"


def _sku_content(data: Mapping[str, Any], sku: str, family: str) -> tuple[str, dict[str, Any]]:
    name = str(data.get("nome_produto") or "").strip()
    description = str(_sku_section(data, "o_que_e") or "").strip()
    application = _sku_section(data, "aplicacao")
    application_type = str(_sku_field(application, "tipo", "geral") or "geral")
    characteristics = [str(item) for item in _sku_items(_sku_section(data, "caracteristicas_tecnicas")) if isinstance(item, str)]
    uses = [str(item) for item in _sku_items(_sku_section(data, "para_que_serve")) if isinstance(item, str)]
    vehicles_section = _sku_field(application, "veiculos_compativeis", {})
    vehicles: list[str] = []
    for item in _sku_items(vehicles_section):
        if not isinstance(item, dict):
            continue
        brand = str(_sku_field(item, "marca", "") or "").strip()
        model = str(_sku_field(item, "modelo", "") or "").strip()
        years = _sku_field(item, "anos", [])
        years_text = ", ".join(str(year) for year in years) if isinstance(years, list) else ""
        label = " ".join(part for part in (brand, model) if part)
        if years_text:
            label += f" ({years_text})"
        if label:
            vehicles.append(label)
    equipment_section = _sku_field(application, "equipamentos_ou_aplicacoes_compativeis", {})
    equipment = [str(item) for item in _sku_items(equipment_section) if isinstance(item, str)]
    lines = [f"SKU: {sku}", f"Produto: {name}", f"Familia: {family}"]
    if description:
        lines.append(f"Descricao: {description}")
    if uses:
        lines.append("Uso: " + " ".join(uses))
    if characteristics:
        lines.append("Caracteristicas: " + " ".join(characteristics))
    if vehicles:
        lines.append("Veiculos compativeis: " + "; ".join(vehicles))
    if equipment:
        lines.append("Aplicacoes compativeis: " + "; ".join(equipment))
    allowed = {
        "schema_version": data.get("schema_version"),
        "sku": sku,
        "nome_produto": name,
        "o_que_e": description,
        "para_que_serve": uses,
        "caracteristicas_tecnicas": characteristics,
        "aplicacao_tipo": application_type,
        "veiculos": vehicles,
        "equipamentos": equipment,
        "revisao": _sku_section(data, "revisao"),
    }
    return "\n".join(lines), allowed


def _sku_dir(info_root: Path, client_id: str) -> Path:
    if info_root.name == client_id and (info_root / "SKU").exists():
        return info_root / "SKU"
    return info_root / client_id / "SKU"


def _scan_sku(
    base: Path,
    info_root: Path,
    client_id: str,
    surface: str,
    findings: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result_stats: dict[str, Any] = {
        "files": 0,
        "valid": 0,
        "invalid": 0,
        "pending": 0,
        "excluded": 0,
        "families": 0,
    }
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", client_id or "") or client_id in {".", ".."}:
        findings.append(_finding("invalid_client_id", "blocker", "info", "Identificador de tenant invalido."))
        return [], result_stats
    try:
        info_resolved = info_root.resolve(strict=True)
    except OSError:
        findings.append(_finding("info_root_missing", "blocker", "info", "Raiz info nao encontrada."))
        return [], result_stats
    sku_path = _sku_dir(info_resolved, client_id)
    safe_sku = _safe_resolve(sku_path, info_resolved)
    if safe_sku is None or not safe_sku.is_dir():
        findings.append(_finding("sku_root_missing", "blocker", f"info/{client_id}/SKU", "Catalogo SKU nao encontrado."))
        return [], result_stats
    index_path = safe_sku / "_INDICE.json"
    index: dict[str, Any] = {}
    index_ref = f"info/{client_id}/SKU/_INDICE.json"
    if not index_path.is_file() or index_path.is_symlink():
        findings.append(_finding("sku_index_missing", "blocker", index_ref, "Indice canonico SKU ausente."))
    else:
        try:
            loaded_index = json.loads(_read_text(index_path))
            index = loaded_index if isinstance(loaded_index, dict) else {}
        except (OSError, json.JSONDecodeError):
            findings.append(_finding("sku_index_invalid", "blocker", index_ref, "Indice canonico SKU invalido."))

    sku_entities: list[dict[str, Any]] = []
    families: dict[str, list[str]] = defaultdict(list)
    names: list[tuple[str, str]] = []
    files = sorted(path for path in safe_sku.glob("*.json") if path.name != "_INDICE.json")
    result_stats["files"] = len(files)
    for path in files:
        ref = f"info/{client_id}/SKU/{path.name}"
        if path.is_symlink() or _safe_resolve(path, safe_sku) is None:
            findings.append(_finding("unsafe_sku_path", "blocker", ref, "Dossie SKU fora da raiz canonica."))
            result_stats["invalid"] += 1
            continue
        try:
            data = json.loads(_read_text(path))
        except (OSError, json.JSONDecodeError):
            findings.append(_finding("sku_json_invalid", "blocker", ref, "Dossie SKU com JSON invalido."))
            result_stats["invalid"] += 1
            continue
        if not isinstance(data, dict):
            findings.append(_finding("sku_schema_invalid", "blocker", ref, "Dossie SKU nao e um objeto."))
            result_stats["invalid"] += 1
            continue
        normalized_keys = _sku_top_keys(data)
        if normalized_keys != _SKU_REQUIRED_KEYS or data.get("schema_version") != SKU_SCHEMA_VERSION:
            findings.append(_finding("sku_schema_invalid", "blocker", ref, "Dossie SKU usa schema inesperado."))
            result_stats["invalid"] += 1
            continue
        if _contains_forbidden_sku_key(data):
            findings.append(_finding("sku_forbidden_field", "blocker", ref, "Dossie SKU contem campo nao permitido."))
            result_stats["invalid"] += 1
            continue
        if _contains_sensitive_sku_value(data):
            findings.append(
                _finding("sku_sensitive_value", "blocker", ref, "Dossie SKU contem dado sensivel nao permitido.")
            )
            result_stats["invalid"] += 1
            continue
        sku = str(data.get("sku") or "").strip()
        normalized_sku = _slug(sku, fallback="")
        if not normalized_sku or path.stem.casefold() != sku.casefold():
            findings.append(_finding("sku_identity_mismatch", "blocker", ref, "Identidade do SKU diverge do nome do arquivo."))
            result_stats["invalid"] += 1
            continue
        revision = _sku_section(data, "revisao")
        pending = _sku_field(revision, "pendencias", [])
        status = _normal_key(_sku_field(revision, "status", ""))
        if status != "revisado" or not isinstance(pending, list) or pending:
            findings.append(_finding("sku_pending_review", "blocker", ref, "Dossie SKU possui revisao pendente."))
            result_stats["pending"] += 1
            result_stats["invalid"] += 1
            continue
        application = _sku_section(data, "aplicacao")
        application_type = str(_sku_field(application, "tipo", "geral") or "geral")
        family = _sku_family(str(data.get("nome_produto") or ""), application_type)
        content, allowed = _sku_content(data, sku, family)
        entity_id = f"jk:sku:{normalized_sku}"
        families[family].append(entity_id)
        names.append((sku, str(data.get("nome_produto") or "")))
        sku_entities.append(
            _entity(
                entity_id=entity_id,
                kind="sku",
                domain="cadastro",
                title=f"SKU {sku} - {data.get('nome_produto')}",
                surface=surface,
                tenant_scope="client",
                sensitivity="internal_catalog",
                truth_class="canonical",
                source_refs=[ref],
                source_hash=_sha256_value(allowed),
                relationships=[{"type": "member_of", "target_id": f"jk:sku-family:{_slug(family)}"}],
                metadata={
                    "sku": sku,
                    "family": family,
                    "application_type": application_type,
                    "updated_at": str(data.get("atualizado_em") or ""),
                    "render_individually": False,
                },
                content=content,
            )
        )
        result_stats["valid"] += 1

    excluded = index.get("skus_excluidos")
    if excluded is None:
        excluded = next((value for key, value in index.items() if _normal_key(key) == "skus_excluidos"), [])
    excluded = excluded if isinstance(excluded, list) else []
    result_stats["excluded"] = len(excluded)
    result_stats["families"] = len(families)
    index_total = index.get("total_skus")
    index_reviewed = index.get("arquivos_revisados")
    index_pending = next((value for key, value in index.items() if _normal_key(key) == "skus_com_pendencias"), [])
    if index.get("schema_version") != SKU_SCHEMA_VERSION:
        findings.append(_finding("sku_index_schema_invalid", "blocker", index_ref, "Indice SKU usa schema inesperado."))
    if index_total != len(files) or index_reviewed != len(files) or (isinstance(index_pending, list) and index_pending):
        findings.append(_finding("sku_index_inconsistent", "blocker", index_ref, "Contagens do indice SKU estao inconsistentes."))

    index_hash = _sha256_bytes(_read_bytes(index_path)) if index_path.is_file() else _sha256_value({})
    catalog_content = "\n".join(
        [f"Catalogo canonico de SKU: {len(names)} itens.", *[f"- {sku}: {name}" for sku, name in sorted(names)]]
    )
    coverage = index.get("cobertura") if isinstance(index.get("cobertura"), dict) else {}
    coverage_content = "Cobertura do catalogo SKU.\n" + "\n".join(
        f"- {_safe_summary(key)}: {_safe_summary(value)}" for key, value in sorted(coverage.items())
    )
    coverage_content += "\n- excluidos: " + (", ".join(str(item) for item in excluded) or "nenhum")
    family_content = "Familias derivadas do catalogo SKU.\n" + "\n".join(
        f"- {family}: {len(ids)} SKU(s)" for family, ids in sorted(families.items())
    )
    map_specs = (
        ("catalog", "Catalogo SKU", catalog_content, {"sku_count": len(names)}),
        ("coverage", "Cobertura SKU", coverage_content, {"coverage": coverage, "excluded": excluded}),
        (
            "families",
            "Familias SKU",
            family_content,
            {"families": {family: sorted(ids) for family, ids in sorted(families.items())}},
        ),
    )
    for map_id, title, content, metadata in map_specs:
        sku_entities.append(
            _entity(
                entity_id=f"jk:sku-map:{map_id}",
                kind="sku_map",
                domain="cadastro",
                title=title,
                surface=surface,
                tenant_scope="client",
                sensitivity="internal_catalog",
                truth_class="canonical",
                source_refs=[index_ref],
                source_hash=_sha256_value({"index": index_hash, "map": map_id, "metadata": metadata}),
                relationships=[],
                metadata={**metadata, "render_individually": True},
                content=content,
            )
        )
    return sku_entities, result_stats


def _scan_legacy_docs(base: Path, surface: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    findings: list[dict[str, str]] = []
    paths: set[Path] = set(base.glob("*.md"))
    docs = base / "docs"
    if docs.is_dir():
        paths.update(path for path in docs.rglob("*.md") if "knowledge" not in path.relative_to(docs).parts)
    entities: list[dict[str, Any]] = []
    for path in sorted(paths, key=lambda item: _relative_ref(item, base)):
        if _path_is_excluded(path, base):
            findings.append(
                _finding("legacy_doc_excluded", "info", _relative_ref(path, base), "Documento sensivel excluido do inventario.")
            )
            continue
        ref = _relative_ref(path, base)
        title = path.stem.replace("_", " ").replace("-", " ").title()
        entity_id = f"jk:document:{_slug(ref)}"
        entities.append(
            _entity(
                entity_id=entity_id,
                kind="legacy_document",
                domain="documentacao",
                title=title,
                surface=surface,
                tenant_scope="system",
                sensitivity="internal",
                truth_class="legacy_unverified",
                source_refs=[ref],
                source_hash=_sha256_bytes(_read_bytes(path)),
                metadata={"content_ingested": False},
                content=f"Documento legado {title}. Conteudo nao promovido automaticamente; requer revisao humana.",
            )
        )
    return entities, findings


def _add_domains(entities: list[dict[str, Any]], surface: str) -> list[dict[str, Any]]:
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entity in entities:
        if entity["kind"] != "domain":
            by_domain[str(entity["domain"])].append(entity)
    domains: list[dict[str, Any]] = []
    for domain, rows in sorted(by_domain.items()):
        counts = Counter(str(row["kind"]) for row in rows)
        sources = sorted({ref for row in rows for ref in row["source_refs"]})
        domains.append(
            _entity(
                entity_id=f"jk:domain:{domain}",
                kind="domain",
                domain=domain,
                title=domain.replace("-", " ").title(),
                surface=surface,
                tenant_scope="mixed",
                sensitivity="internal",
                truth_class="generated",
                source_refs=sources[:250],
                source_hash=_sha256_value([(row["id"], row["source_hash"]) for row in sorted(rows, key=lambda item: item["id"])]),
                relationships=[{"type": "contains", "target_id": row["id"]} for row in rows],
                metadata={"counts": dict(sorted(counts.items())), "entity_count": len(rows)},
                content=f"Dominio {domain}. Entidades: {len(rows)}. "
                + ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items())),
            )
        )
    return domains


def _deduplicate_entities(
    entities: Iterable[dict[str, Any]], findings: list[dict[str, str]]
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for entity in entities:
        entity_id = str(entity.get("id") or "")
        if not entity_id:
            continue
        existing = by_id.get(entity_id)
        if existing is None:
            by_id[entity_id] = entity
            continue
        if existing["source_hash"] != entity["source_hash"]:
            findings.append(
                _finding(
                    "duplicate_entity_id",
                    "blocker",
                    entity["source_refs"][0] if entity["source_refs"] else "inventory",
                    "Duas entidades diferentes produziram o mesmo identificador estavel.",
                    entity_id=entity_id,
                )
            )
    return sorted(by_id.values(), key=lambda item: item["id"])


def _source_version(base: Path) -> str:
    for candidate in (base / "package.json", base / "runtime-manifest.json"):
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(_read_text(candidate))
            version = str(payload.get("version") or "").strip() if isinstance(payload, dict) else ""
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,119}", version):
                return version
        except (OSError, json.JSONDecodeError):
            continue
    return "unknown"


def build_context_inventory(base_dir: str, info_root: str, client_id: str, surface: str) -> dict[str, Any]:
    """Constroi o inventario completo sem importar a aplicacao nem escrever arquivos.

    ``info_root`` deve apontar para a raiz ``info`` ou diretamente para a pasta
    do tenant. Fora de ``SKU`` nenhum dado do tenant e aberto.
    """

    base = Path(base_dir).expanduser().resolve(strict=True)
    info = Path(info_root).expanduser().resolve(strict=True)
    surface_value = _slug(surface, fallback="checkout")
    tenant = str(client_id or "").strip()
    findings: list[dict[str, str]] = []

    schemas, schema_lookup = _scan_schemas(base, surface_value, findings)
    routes = _scan_routes(base, surface_value, schema_lookup, findings)
    services, parsed_files = _scan_services(base, surface_value, findings)
    data_schemas = _scan_sql_schemas(parsed_files, base, surface_value)
    _link_implementation_relationships(routes, services, data_schemas)
    screens, frontend_calls = _scan_frontend(base, surface_value, routes, findings)
    tests = _scan_tests(base, surface_value)
    integrations = _scan_integrations(base, surface_value)
    capabilities = _scan_capabilities(base, surface_value, tenant, routes, findings)
    sku_entities, sku_stats = _scan_sku(base, info, tenant, surface_value, findings)
    legacy_docs, legacy_findings = _scan_legacy_docs(base, surface_value)
    findings.extend(legacy_findings)

    base_entities = _deduplicate_entities(
        [
            *schemas,
            *routes,
            *services,
            *data_schemas,
            *screens,
            *frontend_calls,
            *tests,
            *integrations,
            *capabilities,
            *sku_entities,
            *legacy_docs,
        ],
        findings,
    )
    domains = _add_domains(base_entities, surface_value)
    entities = _deduplicate_entities([*base_entities, *domains], findings)
    kind_counts = Counter(str(entity["kind"]) for entity in entities)
    domain_counts = Counter(str(entity["domain"]) for entity in entities)
    finding_counts = Counter(str(item["severity"]) for item in findings)
    route_keys = {
        (str(row["metadata"].get("method") or ""), str(row["metadata"].get("path") or ""))
        for row in routes
    }
    capability_route_keys = {
        (str(row["metadata"].get("method") or ""), str(row["metadata"].get("route_path") or ""))
        for row in capabilities
        if row["metadata"].get("method") and str(row["metadata"].get("route_path") or "").startswith("/")
    }
    stats = {
        "entities": len(entities),
        "by_kind": dict(sorted(kind_counts.items())),
        "by_domain": dict(sorted(domain_counts.items())),
        "findings": dict(sorted(finding_counts.items())),
        "sku": sku_stats,
        "canonical_screens": len(screens),
        "routes": len(routes),
        "schemas": len(schemas),
        "services": len(services),
        "tests": len(tests),
        "capabilities": len(capabilities),
        "route_crosscheck": {
            "ast_routes": len(route_keys),
            "capability_routes": len(capability_route_keys),
            "matched": len(route_keys & capability_route_keys),
            "capability_only": len(capability_route_keys - route_keys),
        },
    }
    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "source_version": _source_version(base),
        "surface": surface_value,
        "client_id": tenant,
        "entities": entities,
        "findings": sorted(
            findings,
            key=lambda item: (
                {"blocker": 0, "error": 1, "warning": 2, "info": 3}.get(item.get("severity", ""), 9),
                item.get("code", ""),
                item.get("source_ref", ""),
                item.get("entity_id", ""),
            ),
        ),
        "stats": stats,
    }


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (list, dict, int, float)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return json.dumps(str(value), ensure_ascii=False)


def _markdown_safe_text(value: Any) -> str:
    """Remove sequencias que podem ser telefone/PII do artefato publicado.

    Dossies SKU continuam canonicos e intocados. Numeros tecnicos longos ficam
    disponiveis somente na entidade estruturada/adaptador autorizado, nao no
    Markdown agregado aberto no Obsidian.
    """

    text = str(value or "")
    return re.sub(r"(?<!\d)\d{8,14}(?!\d)", "[codigo-numerico-protegido]", text)


def _obsidian_wikilink(path: str, label: str | None = None) -> str:
    """Cria um link interno portavel, sempre relativo a raiz do vault."""

    target = str(path or "").replace("\\", "/").strip().removesuffix(".md")
    target = target.replace("[[", "").replace("]]", "").replace("|", "-")
    safe_label = _markdown_safe_text(label or Path(target).name.replace("-", " "))
    safe_label = safe_label.replace("[[", "").replace("]]", "").replace("|", "-")
    return f"[[{target}|{safe_label}]]"


def _with_graph_navigation(
    content: str,
    links: Sequence[tuple[str, str]],
) -> str:
    """Acrescenta arestas reais ao grafo do Obsidian sem links duplicados."""

    unique: dict[str, str] = {}
    for path, label in links:
        normalized = str(path or "").replace("\\", "/").strip()
        if normalized:
            unique.setdefault(normalized, str(label or ""))
    if not unique:
        return content
    navigation = "\n".join(
        f"- {_obsidian_wikilink(path, label)}" for path, label in sorted(unique.items())
    )
    # A navegacao vem primeiro para continuar integra mesmo quando uma fonte
    # agregada atingir o limite defensivo de tamanho do corpo.
    body = content.strip()
    prefix = "## Navegacao no grafo\n\n" + navigation
    return prefix + ("\n\n" + body if body else "")


def _paginate_markdown_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    maximum_chars: int = 60_000,
) -> list[list[Mapping[str, Any]]]:
    """Divide mapas extensos sem cortar uma entidade ou um link Markdown."""

    pages: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    current_size = 0
    for row in rows:
        line_size = len(f"- `{row.get('id', '')}` - {row.get('title', '')}\n")
        if current and current_size + line_size > maximum_chars:
            pages.append(current)
            current = []
            current_size = 0
        current.append(row)
        current_size += line_size
    if current:
        pages.append(current)
    return pages


def render_context_entity_markdown(
    entity: Mapping[str, Any],
    *,
    source_version: str = "unknown",
    generated_at: str = "1970-01-01T00:00:00Z",
) -> str:
    """Renderiza uma entidade em Markdown gerenciado sem efeitos colaterais."""

    missing = [key for key in ENTITY_KEYS if key not in entity]
    if missing:
        raise ValueError("entidade incompleta: " + ", ".join(missing))
    metadata = entity.get("metadata") if isinstance(entity.get("metadata"), dict) else {}
    frontmatter = {
        "id": entity["id"],
        "type": entity["kind"],
        "managed": True,
        "status": "published",
        "ai_usage": "allowed",
        "tenant_scope": entity["tenant_scope"],
        "sensitivity": entity["sensitivity"],
        "truth_class": entity["truth_class"],
        "required_permissions": metadata.get("required_permissions", ["admin_full"]),
        "surface": entity["surface"],
        "source_version": source_version,
        "source_refs": list(entity["source_refs"]),
        "source_hash": entity["source_hash"],
        "generated_at": generated_at,
    }
    lines = ["---", *[f"{key}: {_yaml_scalar(value)}" for key, value in frontmatter.items()], "---", ""]
    lines.extend(
        [
            f"# {_markdown_safe_text(entity['title'])}",
            "",
            _markdown_safe_text(entity.get("content") or "").strip(),
            "",
        ]
    )
    relationships = entity.get("relationships")
    if isinstance(relationships, list) and relationships:
        lines.extend(["## Relacoes", ""])
        for row in relationships:
            if isinstance(row, dict):
                lines.append(
                    f"- {row.get('type', 'related_to')}: `{_markdown_safe_text(row.get('target_id', ''))}`"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _aggregate_entity(
    *,
    entity_id: str,
    kind: str,
    domain: str,
    title: str,
    rows: Sequence[Mapping[str, Any]],
    inventory: Mapping[str, Any],
    content: str,
) -> dict[str, Any]:
    refs = sorted({ref for row in rows for ref in row.get("source_refs", [])})
    return _entity(
        entity_id=entity_id,
        kind=kind,
        domain=domain,
        title=title,
        surface=str(inventory.get("surface") or "checkout"),
        tenant_scope="mixed",
        sensitivity="internal",
        truth_class="generated",
        source_refs=refs[:250],
        source_hash=_sha256_value([(row["id"], row["source_hash"]) for row in rows]),
        metadata={"required_permissions": ["admin_full"], "entity_count": len(rows)},
        content=content,
    )


def render_context_inventory_markdown(
    inventory: Mapping[str, Any],
    *,
    generated_at: str = "1970-01-01T00:00:00Z",
) -> dict[str, str]:
    """Renderiza uma colecao compacta; SKUs ficam agregados e nao viram 450 notas."""

    rows = [row for row in inventory.get("entities", []) if isinstance(row, dict)]
    source_version = str(inventory.get("source_version") or "unknown")
    output: dict[str, str] = {}

    index_path = "70_Gerado/Mapas/Inventario-do-Programa.md"
    areas_path = "70_Gerado/Mapas/Areas-do-Programa.md"
    domain_entities = [row for row in rows if row.get("kind") == "domain"]
    domain_paths = {
        str(row["domain"]): f"70_Gerado/Dominios/{_slug(str(row['domain']))}.md"
        for row in domain_entities
    }
    screen_entities = [row for row in rows if row.get("kind") == "screen"]
    screen_paths = {
        str(row["id"]): f"70_Gerado/Mapas/Telas/{_slug(str(row['id']).split(':')[-1])}.md"
        for row in screen_entities
    }
    grouped_notes = (
        ("api", "70_Gerado/Contratos/APIs.md", "jk:map:apis", "Mapa de APIs"),
        ("schema", "70_Gerado/Contratos/Schemas.md", "jk:map:schemas", "Mapa de Schemas"),
        ("data_schema", "70_Gerado/Contratos/Dados.md", "jk:map:data", "Mapa de Dados"),
        ("test", "70_Gerado/Operacao/Testes.md", "jk:map:tests", "Mapa de Testes"),
        ("capability", "70_Gerado/Operacao/Capacidades-Codex.md", "jk:map:capabilities", "Capacidades Codex"),
        ("integration", "70_Gerado/Operacao/Runtime-e-Integracoes.md", "jk:map:integrations", "Runtime e Integracoes"),
    )
    grouped_paths = {kind: (path, title) for kind, path, _entity_id, title in grouped_notes}
    api_entities = [row for row in rows if row.get("kind") == "api"]
    api_domains = sorted({str(row.get("domain") or "sistema") for row in api_entities})
    api_domain_paths = {
        domain: f"70_Gerado/Contratos/APIs/{_slug(domain)}.md"
        for domain in api_domains
    }
    sku_paths = {
        "jk:sku-map:catalog": "70_Gerado/Produtos/Catalogo-SKU.md",
        "jk:sku-map:coverage": "70_Gerado/Produtos/Cobertura-SKU.md",
        "jk:sku-map:families": "70_Gerado/Produtos/Familias-SKU.md",
    }
    sku_titles = {
        str(row.get("id") or ""): str(row.get("title") or "Mapa SKU")
        for row in rows
        if str(row.get("id") or "") in sku_paths
    }

    stats = inventory.get("stats") if isinstance(inventory.get("stats"), dict) else {}
    index_content = "Inventario semantico do JK Sistema.\n\n" + "\n".join(
        f"- {key}: {_canonical_json(value) if isinstance(value, (dict, list)) else value}"
        for key, value in sorted(stats.items())
    )
    index_links = [
        (path, str(row.get("title") or row.get("domain") or "Dominio"))
        for row in domain_entities
        if (path := domain_paths.get(str(row.get("domain") or "")))
    ]
    available_kinds = {str(row.get("kind") or "") for row in rows}
    represented_area_kinds = {
        kind for kind, _path, _entity_id, _title in grouped_notes if kind in available_kinds
    }
    represented_sku_ids = set(sku_titles)
    area_links = [
        (path, title)
        for kind, path, _entity_id, title in grouped_notes
        if kind in represented_area_kinds
    ]
    area_links.extend(
        (path, sku_titles[entity_id])
        for entity_id, path in sku_paths.items()
        if entity_id in sku_titles
    )
    if area_links:
        index_links.append((areas_path, "Areas do Programa"))
    index_content = _with_graph_navigation(index_content, index_links)
    index = _aggregate_entity(
        entity_id="jk:map:inventory",
        kind="map",
        domain="sistema",
        title="Inventario do Programa",
        rows=rows,
        inventory=inventory,
        content=index_content,
    )
    output[index_path] = render_context_entity_markdown(
        index, source_version=source_version, generated_at=generated_at
    )
    if area_links:
        areas_content = _with_graph_navigation(
            "Indices tecnicos e operacionais organizados por finalidade.",
            [(index_path, "Inventario do Programa"), *area_links],
        )
        areas = _aggregate_entity(
            entity_id="jk:map:areas",
            kind="map",
            domain="sistema",
            title="Areas do Programa",
            rows=[
                row
                for row in rows
                if str(row.get("kind") or "") in represented_area_kinds
                or str(row.get("id") or "") in represented_sku_ids
            ],
            inventory=inventory,
            content=areas_content,
        )
        output[areas_path] = render_context_entity_markdown(
            areas, source_version=source_version, generated_at=generated_at
        )

    for domain_entity in domain_entities:
        domain = str(domain_entity["domain"])
        members = [row for row in rows if row.get("domain") == domain and row.get("kind") != "domain"]
        content = str(domain_entity.get("content") or "") + "\n\n" + "\n".join(
            f"- `{row['id']}` - {row['title']}" for row in members
        )
        navigation = [(index_path, "Inventario do Programa")]
        navigation.extend(
            (screen_paths[str(row["id"])], str(row.get("title") or row["id"]))
            for row in members
            if row.get("kind") == "screen" and str(row.get("id") or "") in screen_paths
        )
        for kind in sorted({str(row.get("kind") or "") for row in members}):
            if kind == "api" and domain in api_domain_paths:
                navigation.append((api_domain_paths[domain], f"APIs do dominio {domain}"))
            elif kind in grouped_paths:
                navigation.append(grouped_paths[kind])
        content = _with_graph_navigation(content, navigation)
        aggregate = _aggregate_entity(
            entity_id=domain_entity["id"],
            kind="domain",
            domain=domain,
            title=domain_entity["title"],
            rows=members or [domain_entity],
            inventory=inventory,
            content=content,
        )
        output[domain_paths[domain]] = render_context_entity_markdown(
            aggregate, source_version=source_version, generated_at=generated_at
        )

    for screen in screen_entities:
        related_calls = [
            row
            for row in rows
            if row.get("kind") == "frontend_api_call"
            and any(rel.get("target_id") == screen["id"] for rel in row.get("relationships", []) if isinstance(rel, dict))
        ]
        content = str(screen.get("content") or "")
        if related_calls:
            content += "\n\nChamadas de API:\n" + "\n".join(
                f"- {row['title']} ({row.get('metadata', {}).get('classification', 'unknown')})" for row in related_calls
            )
        navigation: list[tuple[str, str]] = []
        domain_path = domain_paths.get(str(screen.get("domain") or ""))
        if domain_path:
            navigation.append((domain_path, f"Dominio {screen.get('domain')}"))
        else:
            # Folhas sem um dominio conhecido ainda precisam de uma ancora.
            # Quando o dominio existe, ele ja aponta para o inventario e evita
            # concentrar todas as telas diretamente no mesmo no global.
            navigation.append((index_path, "Inventario do Programa"))
        if related_calls:
            api_domain_path = api_domain_paths.get(str(screen.get("domain") or "sistema"))
            if api_domain_path:
                navigation.append(
                    (api_domain_path, f"APIs do dominio {screen.get('domain') or 'sistema'}")
                )
            else:
                navigation.append((grouped_paths["api"][0], grouped_paths["api"][1]))
        content = _with_graph_navigation(content, navigation)
        aggregate = dict(screen)
        aggregate["content"] = content
        output[screen_paths[str(screen["id"])]] = render_context_entity_markdown(
            aggregate, source_version=source_version, generated_at=generated_at
        )

    for kind, output_path, entity_id, title in grouped_notes:
        selected = [row for row in rows if row.get("kind") == kind]
        if not selected:
            continue
        navigation = [(areas_path, "Areas do Programa")]
        if kind == "api":
            navigation.extend(
                (api_domain_paths[domain], f"APIs do dominio {domain}")
                for domain in api_domains
            )
            for domain in api_domains:
                domain_rows = [
                    row for row in selected if str(row.get("domain") or "sistema") == domain
                ]
                domain_navigation = [(output_path, title)]
                if domain in domain_paths:
                    domain_navigation.append((domain_paths[domain], f"Dominio {domain}"))
                domain_content = _with_graph_navigation(
                    "\n".join(f"- `{row['id']}` - {row['title']}" for row in domain_rows),
                    domain_navigation,
                )
                domain_aggregate = _aggregate_entity(
                    entity_id=f"{entity_id}:{_slug(domain)}",
                    kind="map",
                    domain=domain,
                    title=f"APIs do dominio {domain}",
                    rows=domain_rows,
                    inventory=inventory,
                    content=domain_content,
                )
                output[api_domain_paths[domain]] = render_context_entity_markdown(
                    domain_aggregate,
                    source_version=source_version,
                    generated_at=generated_at,
                )
            content = (
                f"Mapa de {len(selected)} APIs organizado em "
                f"{len(api_domains)} dominios."
            )
        else:
            navigation.extend(
                (domain_paths[domain], f"Dominio {domain}")
                for domain in sorted({str(row.get("domain") or "") for row in selected})
                if domain in domain_paths
            )
        if kind == "test":
            pages = _paginate_markdown_rows(selected)
            page_links: list[tuple[str, str]] = []
            for page_number, page_rows in enumerate(pages, start=1):
                page_path = f"70_Gerado/Operacao/Testes/Parte-{page_number:03d}.md"
                page_title = f"Mapa de Testes - Parte {page_number:03d}"
                page_links.append((page_path, page_title))
                page_content = "\n".join(
                    f"- `{row['id']}` - {row['title']}" for row in page_rows
                )
                # A pagina e folha do mapa de testes. O mapa pai e os dominios
                # ja oferecem o caminho ate o inventario global; repetir esse
                # backlink em toda pagina transforma o grafo em um unico hub.
                page_navigation = [(output_path, "Mapa de Testes")]
                page_navigation.extend(
                    (domain_paths[domain], f"Dominio {domain}")
                    for domain in sorted({str(row.get("domain") or "") for row in page_rows})
                    if domain in domain_paths
                )
                page_content = _with_graph_navigation(page_content, page_navigation)
                page_aggregate = _aggregate_entity(
                    entity_id=f"{entity_id}:part-{page_number:03d}",
                    kind="map",
                    domain="sistema",
                    title=page_title,
                    rows=page_rows,
                    inventory=inventory,
                    content=page_content,
                )
                output[page_path] = render_context_entity_markdown(
                    page_aggregate,
                    source_version=source_version,
                    generated_at=generated_at,
                )
            navigation.extend(page_links)
            content = (
                f"Mapa paginado para preservar integralmente {len(selected)} testes.\n\n"
                f"- paginas: {len(pages)}"
            )
        elif kind != "api":
            content = "\n".join(f"- `{row['id']}` - {row['title']}" for row in selected)
        content = _with_graph_navigation(content, navigation)
        aggregate = _aggregate_entity(
            entity_id=entity_id,
            kind="map",
            domain="sistema",
            title=title,
            rows=selected,
            inventory=inventory,
            content=content,
        )
        output[output_path] = render_context_entity_markdown(
            aggregate, source_version=source_version, generated_at=generated_at
        )

    for row in rows:
        row_id = str(row.get("id") or "")
        output_path = sku_paths.get(row_id)
        if output_path:
            aggregate = dict(row)
            navigation = [(areas_path, "Areas do Programa")]
            cadastro_path = domain_paths.get("cadastro")
            if cadastro_path:
                navigation.append((cadastro_path, "Dominio Cadastro"))
            navigation.extend(
                (path, sku_titles[entity_id])
                for entity_id, path in sku_paths.items()
                if entity_id != row_id and entity_id in sku_titles
            )
            aggregate["content"] = _with_graph_navigation(str(row.get("content") or ""), navigation)
            output[output_path] = render_context_entity_markdown(
                aggregate, source_version=source_version, generated_at=generated_at
            )

    return dict(sorted(output.items()))


def build_context_bundle_manifest(knowledge_dir: str | Path, source_version: str) -> dict[str, Any]:
    """Retorna manifesto deterministico dos arquivos versionados de conhecimento."""

    root = Path(knowledge_dir).expanduser().resolve(strict=True)
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.name == "context-bundle-manifest.json":
            continue
        resolved = _safe_resolve(path, root)
        if resolved is None:
            raise ValueError(f"arquivo fora da raiz de conhecimento: {path.name}")
        data = _read_bytes(resolved)
        relative = resolved.relative_to(root).as_posix()
        files.append(
            {
                "path": f"docs/knowledge/{relative}",
                "sha256": _sha256_bytes(data),
                "size": len(data),
            }
        )
    return {
        "schema_version": 1,
        "source_version": str(source_version or "unknown"),
        "files": files,
    }


__all__ = [
    "ENTITY_KEYS",
    "INVENTORY_SCHEMA_VERSION",
    "build_context_bundle_manifest",
    "build_context_inventory",
    "compare_context_inventory_openapi",
    "render_context_entity_markdown",
    "render_context_inventory_markdown",
]
