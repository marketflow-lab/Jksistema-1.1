"""Surface Scanner component."""



from __future__ import annotations



import ast
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import (
    Any,
    Sequence,
)



from .contracts import (
    _INFRA_ROOTS,
    _INFRA_SUFFIXES,
)



from .normalization import (
    _sha256_bytes,
    _sha256_value,
    _slug,
    _normal_api_path,
    _domain,
)



from .security import (
    _read_bytes,
    _read_text,
    _safe_resolve,
    _relative_ref,
    _source_hash,
    _safe_summary,
)



from .entities import (
    _finding,
    _entity,
)



from .python_scanner import _path_is_excluded






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
