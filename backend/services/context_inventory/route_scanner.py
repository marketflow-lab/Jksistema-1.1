"""Route Scanner component."""



from __future__ import annotations



import ast
import re
from collections import defaultdict
from pathlib import Path
from typing import (
    Any,
    Mapping,
    Sequence,
)



from .contracts import _ROUTE_METHODS



from .normalization import (
    _canonical_json,
    _sha256_bytes,
    _sha256_value,
    _normal_api_path,
)



from .security import (
    _read_bytes,
    _relative_ref,
)



from .entities import (
    _finding,
    _entity,
)



from .python_scanner import (
    _literal_string,
    _annotation_name,
    _domain_from_source_path,
    _parse_python,
    _path_is_excluded,
)



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


def _route_files(base: Path) -> list[Path]:
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
    return files


def _effective_route_prefix(
    raw_path: str,
    prefix: str,
    declared_prefixes: Sequence[str],
) -> str:
    if prefix or not raw_path or raw_path.startswith(("/api/", "/auth/", "/webhooks/")):
        return prefix
    non_empty = [item for item in declared_prefixes if item]
    return non_empty[0] if len(set(non_empty)) == 1 else ""


def _endpoint_schema_ids(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    decorator: ast.Call,
    schema_lookup: Mapping[str, str],
) -> set[str]:
    schema_ids: set[str] = set()
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    annotations = [_annotation_name(argument.annotation) for argument in arguments]
    annotations.extend(
        _annotation_name(keyword.value)
        for keyword in decorator.keywords
        if keyword.arg == "response_model"
    )
    for annotation in annotations:
        for schema_name, schema_id in schema_lookup.items():
            if re.search(rf"\b{re.escape(schema_name)}\b", annotation):
                schema_ids.add(schema_id)
    return schema_ids


def _route_entity(
    *,
    method: str,
    route_path: str,
    endpoint: str,
    line: int,
    domain: str,
    surface: str,
    ref: str,
    file_hash: str,
    schema_ids: Sequence[str] = (),
    declaration: str = "",
    static_registration: bool = False,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "method": method,
        "path": route_path,
        "endpoint": endpoint,
        "line": line,
    }
    if declaration:
        metadata["declaration"] = declaration
    content = f"Rota {method} {route_path}, implementada por {endpoint}."
    if static_registration:
        content = (
            f"Rota {method} {route_path} registrada estaticamente "
            f"por {endpoint or 'endpoint'}."
        )
    return _entity(
        entity_id=_route_id(method, route_path),
        kind="api",
        domain=domain,
        title=f"{method} {route_path}",
        surface=surface,
        tenant_scope="request",
        sensitivity="internal",
        truth_class="source",
        source_refs=[ref],
        source_hash=_sha256_value(
            {
                "file": file_hash,
                "method": method,
                "path": route_path,
                "endpoint": endpoint,
            }
        ),
        relationships=[
            {"type": "accepts", "target_id": schema_id}
            for schema_id in sorted(schema_ids)
        ],
        metadata=metadata,
        content=content,
    )


def _scan_decorated_routes(
    tree: ast.Module,
    *,
    constants: Mapping[str, str],
    routers: Mapping[str, str],
    declared_prefixes: Sequence[str],
    schema_lookup: Mapping[str, str],
    domain: str,
    surface: str,
    ref: str,
    file_hash: str,
    findings: list[dict[str, str]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.lower()
            if method not in _ROUTE_METHODS:
                continue
            raw_path = _literal_string(decorator.args[0], constants) if decorator.args else "/"
            owner = _annotation_name(decorator.func.value)
            prefix = _effective_route_prefix(raw_path, routers.get(owner, ""), declared_prefixes)
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
            result.append(
                _route_entity(
                    method=method.upper(),
                    route_path=route_path,
                    endpoint=node.name,
                    line=node.lineno,
                    domain=domain,
                    surface=surface,
                    ref=ref,
                    file_hash=file_hash,
                    schema_ids=sorted(_endpoint_schema_ids(node, decorator, schema_lookup)),
                )
            )
    return result


def _scan_legacy_route_specs(
    tree: ast.Module,
    *,
    constants: Mapping[str, str],
    declared_prefixes: Sequence[str],
    schema_lookup: Mapping[str, str],
    domain: str,
    surface: str,
    ref: str,
    file_hash: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    allowed_methods = {item.upper() for item in _ROUTE_METHODS}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _annotation_name(node.func).split(".")[-1] != "LegacyRouteSpec":
            continue
        if len(node.args) < 3:
            continue
        method = _literal_string(node.args[0], constants).upper()
        raw_path = _literal_string(node.args[1], constants)
        endpoint = _literal_string(node.args[2], constants)
        response_model = _literal_string(node.args[3], constants) if len(node.args) >= 4 else ""
        if method not in allowed_methods or not raw_path:
            continue
        prefix = _effective_route_prefix(raw_path, "", declared_prefixes)
        schema_id = schema_lookup.get(response_model, "")
        result.append(
            _route_entity(
                method=method,
                route_path=_normal_api_path(prefix + raw_path),
                endpoint=endpoint,
                line=node.lineno,
                domain=domain,
                surface=surface,
                ref=ref,
                file_hash=file_hash,
                schema_ids=[schema_id] if schema_id else [],
                declaration="LegacyRouteSpec",
            )
        )
    return result


def _add_api_route_methods(node: ast.Call, constants: Mapping[str, str]) -> list[str]:
    methods = ["GET"]
    allowed_methods = {method.upper() for method in _ROUTE_METHODS}
    for keyword in node.keywords:
        if keyword.arg == "methods" and isinstance(keyword.value, (ast.List, ast.Tuple, ast.Set)):
            parsed = [_literal_string(item, constants).upper() for item in keyword.value.elts]
            methods = [item for item in parsed if item in allowed_methods] or methods
    return methods


def _scan_added_api_routes(
    tree: ast.Module,
    *,
    constants: Mapping[str, str],
    routers: Mapping[str, str],
    declared_prefixes: Sequence[str],
    domain: str,
    surface: str,
    ref: str,
    file_hash: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_api_route" or not node.args:
            continue
        raw_path = _literal_string(node.args[0], constants)
        if not raw_path:
            continue
        owner = _annotation_name(node.func.value)
        prefix = _effective_route_prefix(raw_path, routers.get(owner, ""), declared_prefixes)
        route_path = _normal_api_path(prefix + raw_path)
        endpoint = ""
        if len(node.args) >= 2:
            endpoint = getattr(node.args[1], "attr", "") or getattr(node.args[1], "id", "") or ""
        for method in _add_api_route_methods(node, constants):
            result.append(
                _route_entity(
                    method=method,
                    route_path=route_path,
                    endpoint=endpoint,
                    line=node.lineno,
                    domain=domain,
                    surface=surface,
                    ref=ref,
                    file_hash=file_hash,
                    static_registration=True,
                )
            )
    return result


def _scan_routes(
    base: Path,
    surface: str,
    schema_lookup: Mapping[str, str],
    findings: list[dict[str, str]],
) -> list[dict[str, Any]]:
    routes: dict[tuple[str, str], dict[str, Any]] = {}
    for path in _route_files(base):
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
        decorated = _scan_decorated_routes(
            tree,
            constants=constants,
            routers=routers,
            declared_prefixes=declared_prefixes,
            schema_lookup=schema_lookup,
            domain=domain,
            surface=surface,
            ref=ref,
            file_hash=file_hash,
            findings=findings,
        )
        for route in decorated:
            metadata = route["metadata"]
            key = (str(metadata["method"]), str(metadata["path"]))
            existing = routes.get(key)
            if existing is None or ref < existing["source_refs"][0]:
                routes[key] = route
        declared = _scan_legacy_route_specs(
            tree,
            constants=constants,
            declared_prefixes=declared_prefixes,
            schema_lookup=schema_lookup,
            domain=domain,
            surface=surface,
            ref=ref,
            file_hash=file_hash,
        )
        added = _scan_added_api_routes(
            tree,
            constants=constants,
            routers=routers,
            declared_prefixes=declared_prefixes,
            domain=domain,
            surface=surface,
            ref=ref,
            file_hash=file_hash,
        )
        for route in [*declared, *added]:
            metadata = route["metadata"]
            routes.setdefault((str(metadata["method"]), str(metadata["path"])), route)
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
