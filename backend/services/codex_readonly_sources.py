"""Read-only data source discovery and query helpers for Black Jhon."""

from __future__ import annotations

import csv
import concurrent.futures
import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional

from backend.services.runtime_bridge import bind_runtime_globals


READONLY_SOURCES_VERSION = "20260718-readonly-sources-v4-ml-post-sale"
MAX_DISCOVERY_FILES = int(os.getenv("JK_CODEX_READONLY_MAX_DISCOVERY_FILES") or "1200")
MAX_TEXT_BYTES = int(os.getenv("JK_CODEX_READONLY_MAX_TEXT_BYTES") or str(512 * 1024))
DEFAULT_LIMIT = 50
SEARCH_STOPWORDS = {
    "a", "o", "os", "as", "de", "do", "da", "dos", "das", "um", "uma", "para", "com", "por",
    "qual", "quais", "como", "quando", "onde", "dados", "dado", "fiscal", "fiscais", "produto",
    "produtos", "sku", "estoque", "vendas", "venda", "pergunta", "perguntas", "mercado", "livre",
    "bling", "local", "locais", "fonte", "fontes", "consulta", "consultar", "busca", "buscar",
}
SENSITIVE_KEY_RE = re.compile(
    r"(token|access_token|refresh_token|secret|client_secret|api_key|apikey|senha|password|cookie|authorization|jwt|"
    r"e-?mail|telefone|phone|celular|whatsapp|cpf|cnpj|endere[cç]o|address|buyer|comprador)",
    re.I,
)
PII_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)\b(?:access[_-]?token|refresh[_-]?token|client[_-]?secret|api[_-]?key|password|senha)\b\s*[:=]\s*[\"']?[^\s\"']{8,}"
    ),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(r"(?<!\d)(?:\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[-\s]?\d{4}(?!\d)"),
)
SYNC_HINT_RE = re.compile(r"(sync|sincron|shared|state|status|erro|error|log|auditoria|job|worker|progress)", re.I)
CACHE_HINT_RE = re.compile(r"(cache|historico|state|favoritos|ia_|web_cache|ml_|mercado|bling|integracoes)", re.I)
FISCAL_HINT_RE = re.compile(r"(imposto|fiscal|ncm|cest|tribut|aliquota|convenio|nf-e|nfe|\bnf\b)", re.I)
QUESTION_HINT_RE = re.compile(r"(pergunta|pos_venda|pos-venda|question|approval|aprovacao|comprador)", re.I)
QUESTION_ALL_STORES_RE = re.compile(
    r"\b(todas as lojas|todas lojas|todas as contas|cada loja|cada conta|por loja|por conta|"
    r"loja a loja|conta a conta|separad[oa]s? por loja|visao geral)\b",
    re.I,
)
QUESTION_ALLOWED_STATUSES = {
    "UNANSWERED",
    "ANSWERED",
    "CLOSED_UNANSWERED",
    "BANNED",
    "DELETED",
    "UNDER_REVIEW",
}

# Generic discovery is intentionally narrower than the dedicated, permission-aware
# business tools. These paths can contain credentials, buyer data, private AI
# conversations, or mutable operational state and must never become ad-hoc Codex
# sources merely because a supported extension was found.
DISCOVERY_BLOCKED_DIR_NAMES = {
    ".obsidian",
    "contextvault",
    "context_hub",
    "codex_assistant",
    "ia_conversas",
    "credenciais",
    "credentials",
    "secrets",
    "tokens",
}
DISCOVERY_BLOCKED_FILE_NAMES = {
    "ia_rag_local.db",
    "ia_rag_local.db-shm",
    "ia_rag_local.db-wal",
    "context_hub.db",
    "context_hub.sqlite",
    "lojas_config.json",
    "integracoes.json",
    "configuracoes_globais.json",
    "shared_sync_config.json",
}
DISCOVERY_SENSITIVE_NAME_RE = re.compile(
    r"(?:^|[_.-])(config|configuration|credential|credencial|secret|token|oauth|jwt|api[_-]?key|password|senha)(?:[_.-]|$)",
    re.I,
)
DISCOVERY_OPERATIONAL_DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def configure_codex_readonly_sources_runtime(runtime_module=None):
    return bind_runtime_globals(globals(), runtime_module)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _base_dir() -> str:
    base = str(globals().get("BASE_DIR") or os.getcwd()).strip()
    return os.path.abspath(base or os.getcwd())


def _info_root() -> Path:
    base_info = str(globals().get("PASTA_INFO") or os.path.join(_base_dir(), "info")).strip()
    if not os.path.isabs(base_info):
        base_info = os.path.join(_base_dir(), base_info)
    return Path(base_info).resolve()


def _tenant_dir(client_id: str) -> Path:
    client = str(client_id or "default").strip() or "default"
    path = (_info_root() / client).resolve()
    if not _is_inside(path, _info_root()):
        return (_info_root() / "default").resolve()
    return path


def _safe_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    try:
        text = text.encode("latin1").decode("utf-8")
    except Exception:
        pass
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9_.-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    return (text or "source")[:140]


def _norm(value: Any) -> str:
    text = str(value or "").lower()
    try:
        text = text.encode("latin1").decode("utf-8")
    except Exception:
        pass
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except Exception:
        return path.name


def _module_for_path(path: Path) -> str:
    text = _norm(str(path))
    if "vendas" in text or "pedido" in text:
        return "vendas"
    if "estoque" in text:
        return "estoque"
    if "cadastro" in text or "produto" in text or "sku" in text:
        return "cadastro"
    if "bling" in text:
        return "bling"
    if "mercado" in text or "ml_" in text or "mlb" in text or "anuncio" in text:
        return "mercado_livre"
    if "pergunta" in text or "pos_venda" in text or "question" in text:
        return "perguntas_pos_venda"
    if FISCAL_HINT_RE.search(str(path)):
        return "fiscal"
    if SYNC_HINT_RE.search(str(path)):
        return "integracoes"
    if "favoritos" in text:
        return "favoritos"
    if "full" in text:
        return "full"
    return "sistema"


def _source_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        return "sqlite"
    if suffix == ".csv":
        return "csv"
    if suffix in {".json", ".jsonl"}:
        if SYNC_HINT_RE.search(path.name):
            return "sync_log"
        if CACHE_HINT_RE.search(str(path)):
            return "cache"
        return "json"
    if suffix in {".txt", ".log", ".md"}:
        return "log" if SYNC_HINT_RE.search(str(path)) else "text"
    return "file"


def _risk_for_path(path: Path) -> str:
    text = str(path).lower()
    if SENSITIVE_KEY_RE.search(text) or "integracoes" in text or "lojas_config" in text:
        return "contains_sensitive_config_redacted"
    return "low"


def _redact(value: Any, key: str = "") -> Any:
    normalized_key = str(key or "").strip().lower()
    if normalized_key == "buyer_question_history" and isinstance(value, list):
        return [_redact(item) for item in value[:200]]
    if (
        normalized_key.endswith("_count")
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return value
    if key and SENSITIVE_KEY_RE.search(str(key)):
        text = str(value or "")
        if not text:
            return ""
        digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:10]
        return f"[redacted:{digest}]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value[:200]]
    if isinstance(value, str):
        text = value
        for pattern in PII_VALUE_PATTERNS:
            text = pattern.sub("[redacted:pii]", text)
        if len(text) > 4000:
            return text[:4000] + "...[truncated]"
        return text
    return value


def _generic_discovery_enabled() -> bool:
    return str(os.getenv("JK_CODEX_READONLY_GENERIC_DISCOVERY_ENABLED") or "").strip().lower() in {
        "1", "true", "yes", "sim", "on",
    }


def _generic_discovery_allowlist() -> tuple[str, ...]:
    raw = str(os.getenv("JK_CODEX_READONLY_DISCOVERY_ALLOWLIST") or "")
    patterns: list[str] = []
    for item in re.split(r"[;,\r\n]+", raw):
        pattern = str(item or "").strip().replace("\\", "/").lstrip("/")
        normalized = os.path.normpath(pattern).replace("\\", "/") if pattern else ""
        if (
            not normalized
            or normalized in {".", ".."}
            or normalized.startswith("../")
            or os.path.isabs(normalized)
        ):
            continue
        if normalized not in patterns:
            patterns.append(normalized)
    return tuple(patterns[:200])


def _generic_discovery_path_allowlisted(path: Path, root: Path) -> bool:
    patterns = _generic_discovery_allowlist()
    if not patterns:
        return False
    rel = _rel(path, root).replace("\\", "/").lower()
    return any(fnmatch.fnmatchcase(rel, pattern.lower()) for pattern in patterns)


def _safe_int(value: Any, default: int = DEFAULT_LIMIT, minimum: int = 1, maximum: int = 500) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(minimum, min(parsed, maximum))


def _search_terms(message: str, extra: Optional[list[str]] = None) -> list[str]:
    raw = [str(message or ""), *(extra or [])]
    terms: list[str] = []
    for text in raw:
        for match in re.findall(r"\bMLB\d+\b|\bSKU[:\s#-]*[A-Za-z0-9._/-]+\b|\b[A-Za-z0-9._/-]{2,}\b", str(text or ""), flags=re.I):
            term = re.sub(r"^sku[:\s#-]*", "", match, flags=re.I).strip()
            term_norm = _norm(term)
            if len(term_norm) < 3 and not term_norm.isdigit():
                continue
            if term_norm in SEARCH_STOPWORDS:
                continue
            if len(term) >= 2:
                if term not in terms:
                    terms.append(term)
    return terms[:12]


def _read_text(path: Path, max_bytes: int = MAX_TEXT_BYTES) -> str:
    data = path.read_bytes()[:max_bytes]
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return data.decode(enc, errors="replace")
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def _json_preview(path: Path) -> Any:
    text = _read_text(path)
    if path.suffix.lower() == ".jsonl":
        rows = []
        for line in text.splitlines()[:200]:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                rows.append({"line": line[:500]})
        return rows
    try:
        return json.loads(text)
    except Exception:
        return {"text": text[:3000]}


def _sqlite_schema(path: Path) -> dict[str, Any]:
    tables: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 80"):
            table = str(row["name"] or "")
            cols = []
            try:
                for col in conn.execute(f"PRAGMA table_info({_quote_ident(table)})"):
                    cols.append({"name": col[1], "type": col[2]})
            except Exception:
                pass
            tables.append({"name": table, "columns": cols[:80]})
        conn.close()
    except Exception as exc:
        return {"error": str(exc)[:300], "tables": []}
    return {"tables": tables}


def _csv_schema(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="", errors="replace") as fh:
            sample = fh.read(4096)
        dialect = csv.Sniffer().sniff(sample) if sample else csv.excel
        reader = csv.reader(sample.splitlines(), dialect)
        header = next(reader, [])
        return {"columns": [str(item or "") for item in header[:120]]}
    except Exception:
        try:
            text = _read_text(path, 4096)
            first = text.splitlines()[0] if text.splitlines() else ""
            return {"columns": [part.strip() for part in first.split(";")[:120]]}
        except Exception as exc:
            return {"error": str(exc)[:300], "columns": []}


def _discovery_path_forbidden(path: Path, root: Path) -> bool:
    try:
        resolved_root = root.resolve()
        resolved = path.resolve()
        if not _is_inside(resolved, resolved_root):
            return True
        relative = resolved.relative_to(resolved_root)
    except Exception:
        return True

    parts = [str(part or "").strip().lower() for part in relative.parts]
    if any(part in DISCOVERY_BLOCKED_DIR_NAMES for part in parts[:-1]):
        return True
    if any(part.startswith(".") for part in parts[:-1]):
        return True

    name = str(path.name or "").strip().lower()
    if name in DISCOVERY_BLOCKED_FILE_NAMES:
        return True
    if path.suffix.lower() in DISCOVERY_OPERATIONAL_DB_SUFFIXES:
        return True
    if name.endswith((".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm")):
        return True
    if DISCOVERY_SENSITIVE_NAME_RE.search(name):
        return True
    return False


def _discover_files(client_id: str) -> list[Path]:
    if not _generic_discovery_enabled():
        return []
    root = _tenant_dir(client_id)
    if not root.exists():
        return []
    files: list[Path] = []
    allowed = {".csv", ".json", ".jsonl", ".txt", ".log", ".md"}
    for path in root.rglob("*"):
        if len(files) >= MAX_DISCOVERY_FILES:
            break
        try:
            if not path.is_file() or path.suffix.lower() not in allowed:
                continue
            if "__pycache__" in path.parts or _discovery_path_forbidden(path, root):
                continue
            resolved = path.resolve()
            if not _is_inside(resolved, root):
                continue
            if not _generic_discovery_path_allowlisted(resolved, root):
                continue
            files.append(resolved)
        except Exception:
            continue
    return files


def _source_payload(client_id: str, path: Path) -> dict[str, Any]:
    root = _tenant_dir(client_id)
    rel = _rel(path, root)
    stype = _source_type(path)
    schema: dict[str, Any] = {}
    if stype == "sqlite":
        schema = _sqlite_schema(path)
    elif stype == "csv":
        schema = _csv_schema(path)
    return {
        "source_id": _safe_id(rel),
        "type": stype,
        "module": _module_for_path(path),
        "path": rel,
        "extension": path.suffix.lower(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime)) if path.exists() else "",
        "schema": schema,
        "allowed_filters": ["message", "source_id", "module", "type", "limit", "sql"],
        "default_limit": DEFAULT_LIMIT,
        "sensitive_risk": _risk_for_path(path),
        "status": "available",
    }


def discover_data_sources(
    *,
    client_id: str,
    query: str = "",
    module: str = "",
    source_type: str = "",
    limit: int = 300,
) -> dict[str, Any]:
    sources = [_source_payload(client_id, path) for path in _discover_files(client_id)]
    query_norm = _norm(query)
    query_terms = [_norm(item) for item in _search_terms(query)]
    query_terms.extend(
        token for token in _norm(query).split()
        if len(token) >= 4 and token not in {"qual", "quais", "para", "como", "dados", "fonte", "fontes", "voce", "consegue"}
    )
    query_terms = list(dict.fromkeys([term for term in query_terms if term]))
    module_norm = _norm(module)
    type_norm = _norm(source_type)
    filtered = []
    for src in sources:
        hay = _norm(" ".join([src.get("source_id", ""), src.get("path", ""), src.get("module", ""), src.get("type", "")]))
        if query_norm:
            if len(query_norm.split()) <= 2:
                if query_norm not in hay:
                    continue
            elif query_terms and not any(term in hay for term in query_terms):
                continue
        if module_norm and module_norm != _norm(src.get("module")):
            continue
        if type_norm and type_norm != _norm(src.get("type")):
            continue
        filtered.append(src)
    limit_safe = _safe_int(limit, 300, 1, 2000)
    return {
        "success": True,
        "version": READONLY_SOURCES_VERSION,
        "generic_discovery_enabled": _generic_discovery_enabled(),
        "generic_discovery_allowlist_count": len(_generic_discovery_allowlist()),
        "client_id": str(client_id or ""),
        "sources": filtered[:limit_safe],
        "total_sources": len(sources),
        "filtered_count": len(filtered),
        "generated_at": _now(),
    }


def _find_source_path(client_id: str, source_id: str) -> Optional[Path]:
    wanted = _safe_id(source_id)
    for path in _discover_files(client_id):
        if _safe_id(_rel(path, _tenant_dir(client_id))) == wanted:
            return path
    return None


def _matching_paths(client_id: str, stypes: set[str], message: str, source_id: str = "", module: str = "", limit_sources: int = 30) -> list[Path]:
    if source_id:
        found = _find_source_path(client_id, source_id)
        return [found] if found and _source_type(found) in stypes else []
    terms = [_norm(item) for item in _search_terms(message)]
    module_norm = _norm(module)
    paths = []
    for path in _discover_files(client_id):
        if _source_type(path) not in stypes:
            continue
        if module_norm and _norm(_module_for_path(path)) != module_norm:
            continue
        hay = _norm(str(path))
        if terms and not any(term and term in hay for term in terms):
            if not any(hint in hay for hint in ("vendas", "estoque", "cadastro", "produto", "pergunta", "bling", "mercado", "sync", "imposto")):
                continue
        paths.append(path)
        if len(paths) >= limit_sources:
            break
    return paths


def _quote_ident(name: str) -> str:
    return '"' + str(name or "").replace('"', '""') + '"'


def _safe_sql(sql: str) -> tuple[bool, str]:
    text = str(sql or "").strip()
    if not text:
        return False, "SQL vazio."
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    if not (normalized.startswith("select ") or normalized.startswith("with ") or normalized.startswith("pragma table_info")):
        return False, "Somente SELECT/WITH/PRAGMA table_info sao permitidos."
    stripped = normalized.rstrip(";")
    if ";" in stripped:
        return False, "Apenas uma instrucao SQL e permitida."
    if re.search(r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|vacuum|reindex|pragma\s+writable_schema)\b", stripped):
        return False, "Comando mutavel bloqueado."
    return True, ""


def _rows_from_cursor(cur: sqlite3.Cursor, limit: int) -> list[dict[str, Any]]:
    cols = [desc[0] for desc in (cur.description or [])]
    rows = []
    for row in cur.fetchmany(limit):
        rows.append(_redact({cols[idx]: row[idx] for idx in range(min(len(cols), len(row)))}) if cols else {})
    return rows


def local_database_query(
    *,
    client_id: str,
    message: str = "",
    source_id: str = "",
    sql: str = "",
    module: str = "",
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    limit_safe = _safe_int(limit)
    paths = _matching_paths(client_id, {"sqlite"}, message, source_id, module, limit_sources=20)
    records: list[dict[str, Any]] = []
    sources = []
    warnings = []
    terms = _search_terms(message)
    for path in paths:
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=4)
            conn.row_factory = sqlite3.Row
            if sql:
                ok, reason = _safe_sql(sql)
                if not ok:
                    warnings.append(reason)
                    continue
                cur = conn.execute(sql)
                rows = _rows_from_cursor(cur, max(1, limit_safe - len(records)))
                records.extend({"source": _rel(path, _tenant_dir(client_id)), **row} for row in rows)
                sources.append(_source_payload(client_id, path))
            else:
                for table_row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 80"):
                    table = str(table_row["name"] or "")
                    cols = []
                    try:
                        cols = [str(col[1] or "") for col in conn.execute(f"PRAGMA table_info({_quote_ident(table)})")]
                    except Exception:
                        continue
                    if not cols:
                        continue
                    text_cols = cols[:30]
                    where = ""
                    params: list[Any] = []
                    if terms:
                        pieces = []
                        preferred_cols = [
                            col for col in text_cols
                            if any(token in _norm(col) for token in ("sku", "codigo", "bling", "mlb", "item"))
                        ]
                        sku_like_terms = [term for term in terms[:6] if re.match(r"^(MLB\d+|[A-Za-z0-9._/-]{2,})$", str(term or ""), re.I)]
                        if sku_like_terms and preferred_cols:
                            for term in sku_like_terms:
                                for col in preferred_cols:
                                    pieces.append(f"TRIM(CAST({_quote_ident(col)} AS TEXT)) = ?")
                                    params.append(str(term).strip())
                        if not pieces:
                            for term in terms[:6]:
                                like = f"%{term}%"
                                for col in text_cols:
                                    pieces.append(f"CAST({_quote_ident(col)} AS TEXT) LIKE ?")
                                    params.append(like)
                        where = "WHERE " + " OR ".join(pieces)
                    query = f"SELECT * FROM {_quote_ident(table)} {where} LIMIT ?"
                    params.append(max(1, limit_safe - len(records)))
                    cur = conn.execute(query, params)
                    rows = _rows_from_cursor(cur, max(1, limit_safe - len(records)))
                    for row in rows:
                        records.append({"source": _rel(path, _tenant_dir(client_id)), "table": table, **row})
                        if len(records) >= limit_safe:
                            break
                    if len(records) >= limit_safe:
                        break
                sources.append(_source_payload(client_id, path))
            conn.close()
        except Exception as exc:
            warnings.append(f"{_rel(path, _tenant_dir(client_id))}: {str(exc)[:220]}")
        if len(records) >= limit_safe:
            break
    return _result("local_database_query", records, sources, {"message": message, "source_id": source_id, "sql": bool(sql), "module": module, "limit": limit_safe}, warnings)


def local_csv_query(
    *,
    client_id: str,
    message: str = "",
    source_id: str = "",
    module: str = "",
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    limit_safe = _safe_int(limit)
    paths = _matching_paths(client_id, {"csv"}, message, source_id, module, limit_sources=30)
    terms_norm = [_norm(item) for item in _search_terms(message)]
    records: list[dict[str, Any]] = []
    sources = []
    warnings = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8-sig", newline="", errors="replace") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    text = _norm(" ".join(str(v or "") for v in row.values()))
                    if terms_norm and not all(term in text for term in terms_norm[:3]):
                        continue
                    records.append({"source": _rel(path, _tenant_dir(client_id)), **(_redact(row) or {})})
                    if len(records) >= limit_safe:
                        break
            sources.append(_source_payload(client_id, path))
        except Exception as exc:
            warnings.append(f"{_rel(path, _tenant_dir(client_id))}: {str(exc)[:220]}")
        if len(records) >= limit_safe:
            break
    return _result("local_csv_query", records, sources, {"message": message, "source_id": source_id, "module": module, "limit": limit_safe}, warnings)


def _json_records_from_value(value: Any, path: Path, terms_norm: list[str], limit_safe: int) -> list[dict[str, Any]]:
    records = []
    if isinstance(value, dict):
        candidates = []
        for key, item in value.items():
            if isinstance(item, list):
                candidates.extend({"key": key, **row} if isinstance(row, dict) else {"key": key, "value": row} for row in item[:500])
            elif isinstance(item, dict):
                candidates.append({"key": key, **item})
        if not candidates:
            candidates = [{"key": key, "value": item} for key, item in list(value.items())[:200]]
    elif isinstance(value, list):
        candidates = [row if isinstance(row, dict) else {"value": row} for row in value[:500]]
    else:
        candidates = [{"value": value}]
    for item in candidates:
        redacted = _redact(item) or {}
        text = _norm(json.dumps(redacted, ensure_ascii=False, default=str))
        if terms_norm and not any(term in text for term in terms_norm):
            continue
        records.append({"source": str(path.name), **redacted})
        if len(records) >= limit_safe:
            break
    return records


def local_cache_query(
    *,
    client_id: str,
    message: str = "",
    source_id: str = "",
    module: str = "",
    limit: int = DEFAULT_LIMIT,
    sync_only: bool = False,
    fiscal_only: bool = False,
    questions_only: bool = False,
) -> dict[str, Any]:
    limit_safe = _safe_int(limit)
    stypes = {"json", "cache", "sync_log", "text", "log"}
    paths = _matching_paths(client_id, stypes, message, source_id, module, limit_sources=50)
    if sync_only:
        paths = [p for p in paths if SYNC_HINT_RE.search(str(p))]
    if fiscal_only:
        paths = [p for p in paths if FISCAL_HINT_RE.search(str(p))]
    if questions_only:
        paths = [p for p in paths if QUESTION_HINT_RE.search(str(p))]
    terms_norm = [_norm(item) for item in _search_terms(message)]
    records: list[dict[str, Any]] = []
    sources = []
    warnings = []
    for path in paths:
        try:
            if path.suffix.lower() in {".json", ".jsonl"}:
                value = _json_preview(path)
                rows = _json_records_from_value(value, path, terms_norm, max(1, limit_safe - len(records)))
                for row in rows:
                    row["source"] = _rel(path, _tenant_dir(client_id))
                records.extend(rows)
            else:
                text = _read_text(path)
                lines = []
                for line in text.splitlines()[:5000]:
                    if terms_norm and not any(term in _norm(line) for term in terms_norm):
                        continue
                    lines.append(line[:1200])
                    if len(lines) >= max(1, limit_safe - len(records)):
                        break
                records.extend({"source": _rel(path, _tenant_dir(client_id)), "line": line} for line in lines)
            sources.append(_source_payload(client_id, path))
        except Exception as exc:
            warnings.append(f"{_rel(path, _tenant_dir(client_id))}: {str(exc)[:220]}")
        if len(records) >= limit_safe:
            break
    return _result("sync_logs_query" if sync_only else "local_cache_query", records, sources, {"message": message, "source_id": source_id, "module": module, "limit": limit_safe}, warnings)


def sync_logs_query(**kwargs: Any) -> dict[str, Any]:
    kwargs["sync_only"] = True
    if _norm(kwargs.get("module")) in {"integracoes", "sync", "sincronizacao", "shared_sync"}:
        kwargs["module"] = ""
    return local_cache_query(**kwargs)


def _question_status_filter(message: str, status: Any = "") -> str:
    explicit = str(status or "").strip().upper().replace("-", "_").replace(" ", "_")
    if explicit in {"ALL", "TODAS", "TODOS"}:
        return ""
    if explicit in QUESTION_ALLOWED_STATUSES:
        return explicit
    text = _norm(message)
    if re.search(r"\b(fechad[ao]s?|encerrad[ao]s?)\b", text) and re.search(r"\b(sem resposta|nao respondid[ao]s?)\b", text):
        return "CLOSED_UNANSWERED"
    if re.search(r"\b(respondid[ao]s?|respondidas anteriormente|ja respondid[ao]s?)\b", text) and not re.search(
        r"\b(nao respondid[ao]s?|sem resposta)\b", text
    ):
        return "ANSWERED"
    if re.search(r"\b(todas as perguntas|todo o historico|historico completo)\b", text):
        return ""
    return "UNANSWERED"


def _question_store_catalog(client_id: str) -> tuple[list[dict[str, Any]], str]:
    try:
        from backend.services import perguntas_pos_venda_endpoints

        payload = perguntas_pos_venda_endpoints.ml_perguntas_listar_lojas(client_id)
        stores = payload.get("lojas") if isinstance(payload, dict) else []
        clean: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in stores or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("nome") or "").strip()
            key = _norm(name)
            if not name or not key or key in seen:
                continue
            seen.add(key)
            clean.append({
                "nome": name,
                "key": key,
                "connected": bool(item.get("mercadolivre_conectado")),
                "status": str(item.get("mercadolivre_status") or "").strip(),
                "reason": str(item.get("mercadolivre_motivo") or "").strip(),
            })
        return clean, ""
    except Exception as exc:
        detail = getattr(exc, "detail", exc)
        return [], str(detail or "Falha ao listar lojas do Mercado Livre.")[:400]


def _question_requested_stores(
    catalog: list[dict[str, Any]],
    message: str,
    loja: str,
    all_stores: bool,
) -> list[dict[str, Any]]:
    store_text = str(loja or "").strip()
    store_key = _norm(store_text)
    message_key = _norm(message)
    all_requested = bool(all_stores) or bool(QUESTION_ALL_STORES_RE.search(message_key)) or store_key in {
        "todas",
        "todas as lojas",
        "todas as contas",
        "__todas",
    }
    if all_requested:
        return list(catalog)

    def matches(candidate_key: str, haystack: str) -> bool:
        return bool(candidate_key and re.search(rf"(?<![a-z0-9]){re.escape(candidate_key)}(?![a-z0-9])", haystack))

    if store_key:
        for item in catalog:
            if item.get("key") == store_key:
                return [item]
        return [{"nome": store_text, "key": store_key, "connected": True, "status": "", "reason": ""}]

    matched = [item for item in catalog if matches(str(item.get("key") or ""), message_key)]
    if matched:
        matched_keys = {str(item.get("key") or "") for item in matched}
        return [
            item
            for item in matched
            if not any(
                item.get("key") != other_key and matches(str(item.get("key") or ""), other_key)
                for other_key in matched_keys
            )
        ]
    return list(catalog)


def questions_post_sale_query(
    *,
    client_id: str,
    message: str = "",
    loja: str = "",
    status: str = "",
    limit: int = DEFAULT_LIMIT,
    all_stores: bool = False,
    query_deadline_seconds: int = 15,
    **_: Any,
) -> dict[str, Any]:
    """Consulta a fila viva do Mercado Livre; caches historicos nunca provam fila vazia."""

    from backend.services import perguntas_pos_venda_endpoints

    status_filter = _question_status_filter(message, status)
    limit_safe = _safe_int(limit, DEFAULT_LIMIT, 1, 100)
    catalog, catalog_error = _question_store_catalog(client_id)
    requested = _question_requested_stores(catalog, message, loja, all_stores)
    records: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    warnings: list[str] = []
    checked: list[str] = []
    incomplete: list[str] = []
    errors_by_store: dict[str, str] = {}
    questions_by_store: dict[str, Optional[int]] = {}

    if catalog_error and not requested:
        warnings.append(f"Lojas do Mercado Livre: {catalog_error}")
    if not requested and not catalog_error:
        warnings.append("Nenhuma loja Mercado Livre vinculada foi encontrada para consultar perguntas.")

    connected_stores: list[dict[str, Any]] = []
    for store in requested:
        name = str(store.get("nome") or "").strip()
        if not name:
            continue
        if store.get("connected") is False:
            reason = str(store.get("reason") or store.get("status") or "Conta Mercado Livre desconectada.").strip()
            errors_by_store[name] = reason
            questions_by_store[name] = None
            warnings.append(f"{name}: {reason}")
            continue
        connected_stores.append(store)

    fast_summary = len(connected_stores) > 1

    def query_store(store: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        name = str(store.get("nome") or "").strip()
        payload = perguntas_pos_venda_endpoints.ml_listar_perguntas(
            loja=name,
            status=status_filter or None,
            offset=0,
            limit=limit_safe,
            carregar_todas=False,
            max_pages=1,
            modo_resumo_rapido=fast_summary,
            request_timeout=min(10, max(5, int(query_deadline_seconds or 15))),
            client_id=client_id,
        )
        return name, payload if isinstance(payload, dict) else {}

    deadline_safe = _safe_int(query_deadline_seconds, 15, 5, 60)
    executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
    futures: dict[concurrent.futures.Future[Any], str] = {}
    completed_names: set[str] = set()
    if connected_stores:
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=min(4, len(connected_stores)),
            thread_name_prefix="jk-ml-questions",
        )
        futures = {
            executor.submit(query_store, store): str(store.get("nome") or "").strip()
            for store in connected_stores
        }
    try:
        iterator = concurrent.futures.as_completed(futures, timeout=deadline_safe) if futures else []
        for future in iterator:
            expected_name = futures[future]
            completed_names.add(expected_name)
            try:
                name, payload = future.result()
            except Exception as exc:
                detail = getattr(exc, "detail", exc)
                error = str(detail or "Falha ao consultar perguntas.")[:400]
                errors_by_store[expected_name] = error
                questions_by_store[expected_name] = None
                warnings.append(f"{expected_name}: {error}")
                continue
            questions = payload.get("questions") if isinstance(payload, dict) else []
            questions = [item for item in (questions or []) if isinstance(item, dict)]
            if status_filter:
                questions = [
                    item for item in questions
                    if str(item.get("status") or "").strip().upper() == status_filter
                ]
            checked.append(name)
            try:
                total_store = int(payload.get("total") if payload.get("total") is not None else len(questions))
            except (TypeError, ValueError):
                total_store = len(questions)
            questions_by_store[name] = max(0, total_store)
            for question in questions:
                records.append({"record_type": "mercado_livre_question", "loja": name, **question})
            sources.append({
                "source_id": f"mercado_livre_questions_{_safe_id(name)}",
                "type": "external_api",
                "module": "perguntas_pos_venda",
                "path": f"Mercado Livre - perguntas atuais - {name}",
                "updated_at": _now(),
                "status": "available",
            })
            if bool(payload.get("interrompido")):
                incomplete.append(name)
                warnings.append(f"{name}: a API interrompeu a consulta antes de confirmar a fila.")
    except concurrent.futures.TimeoutError:
        pass
    finally:
        for future, name in futures.items():
            if name in completed_names:
                continue
            future.cancel()
            error = f"Consulta excedeu o limite rapido de {deadline_safe} segundos."
            errors_by_store[name] = error
            questions_by_store[name] = None
            incomplete.append(name)
            warnings.append(f"{name}: {error}")
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    requested_names = [str(item.get("nome") or "").strip() for item in requested if str(item.get("nome") or "").strip()]
    coverage_complete = bool(requested_names) and len(checked) == len(requested_names) and not errors_by_store and not incomplete
    result = _result(
        "questions_post_sale_query",
        records,
        sources,
        {
            "message": message,
            "loja": loja,
            "status": status_filter,
            "all_stores": bool(all_stores) or bool(QUESTION_ALL_STORES_RE.search(_norm(message))),
            "limit": limit_safe,
        },
        warnings,
    )
    result.update({
        "live_query": True,
        "source_freshness": "live",
        "status_filter": status_filter,
        "current_queue": status_filter == "UNANSWERED",
        "total_questions": sum(value for value in questions_by_store.values() if isinstance(value, int)),
        "total_pending": sum(value for value in questions_by_store.values() if isinstance(value, int)) if status_filter == "UNANSWERED" else None,
        "records_returned": len(records),
        "records_truncated": any(isinstance(value, int) and value > limit_safe for value in questions_by_store.values()),
        "questions_by_store": questions_by_store,
        "pending_by_store": questions_by_store if status_filter == "UNANSWERED" else {},
        "stores_requested": len(requested_names),
        "stores_checked": len(checked),
        "stores_failed": len(errors_by_store) + len(incomplete),
        "coverage_complete": coverage_complete,
        "zero_is_authoritative": coverage_complete and not records,
        "lojas_solicitadas_text": ", ".join(requested_names),
        "lojas_consultadas_text": ", ".join(checked),
        "lojas_incompletas_text": ", ".join(incomplete),
        "errors_by_store": errors_by_store,
    })
    return result


def fiscal_local_query(**kwargs: Any) -> dict[str, Any]:
    kwargs["fiscal_only"] = True
    result = local_cache_query(**kwargs)
    if not result.get("records"):
        base = {k: v for k, v in kwargs.items() if k in {"client_id", "message", "source_id", "limit"}}
        db_result = local_database_query(**base, module="fiscal")
        csv_result = local_csv_query(**base, module="cadastro")
        records = (db_result.get("records") or []) + (csv_result.get("records") or [])
        result["records"] = records[: _safe_int(kwargs.get("limit"))]
        result["rows"] = result["records"]
        result["top_rows"] = result["records"][:20]
        result["record_count"] = len(result["records"])
        result["sources"] = (result.get("sources") or []) + (db_result.get("sources") or []) + (csv_result.get("sources") or [])
        result["empty_reason"] = "" if result["records"] else result.get("empty_reason") or "Nenhum dado fiscal local encontrado."
    result["tool_id"] = "fiscal_local_query"
    return result


POST_SALE_TEXT_PII_PATTERNS = PII_VALUE_PATTERNS + (
    re.compile(r"(?<!\d)\d{5}-?\d{3}(?!\d)"),
)


def _post_sale_safe_text(value: Any, limit: int = 1200) -> str:
    text = str(value or "").strip()
    for pattern in POST_SALE_TEXT_PII_PATTERNS:
        text = pattern.sub("[dado pessoal ocultado]", text)
    return text[: max(1, int(limit or 1200))]


def _post_sale_safe_attachment(value: Any) -> Optional[dict[str, Any]]:
    attachment = value if isinstance(value, dict) else {}
    name = _post_sale_safe_text(
        attachment.get("name") or attachment.get("filename") or attachment.get("file_name") or "anexo",
        160,
    )
    mime = str(attachment.get("mime_type") or attachment.get("content_type") or "").strip()[:120]
    is_image = bool(attachment.get("is_image") or re.search(r"image|jpg|jpeg|png|webp|gif", f"{mime} {name}", re.I))
    if not name and not mime:
        return None
    # IDs e URLs de anexos sao omitidos de proposito: podem ser credenciais de
    # download ou permitir acesso lateral a midia privada do comprador.
    return {"name": name or "anexo", "mime_type": mime, "is_image": is_image, "available_in_app": True}


def _post_sale_safe_message(value: Any) -> Optional[dict[str, Any]]:
    message = value if isinstance(value, dict) else {}
    text = _post_sale_safe_text(message.get("text"), 1200)
    attachments = []
    for raw_attachment in (message.get("attachments") or [])[:10]:
        attachment = _post_sale_safe_attachment(raw_attachment)
        if attachment:
            attachments.append(attachment)
    if not text and not attachments:
        return None
    role = str(message.get("from_role") or "").strip().lower()
    if role not in {"seller", "buyer"}:
        role = "unknown"
    return {
        "id": str(message.get("id") or "").strip()[:120],
        "date": str(message.get("date") or "").strip()[:64],
        "from_role": role,
        "text": text,
        "attachments": attachments,
        "status": str(message.get("status") or "").strip()[:80],
    }


def mercado_livre_post_sale_detail(
    *,
    client_id: str,
    message: str = "",
    loja: str = "",
    pack_id: str = "",
    order_id: str = "",
    limit: int = DEFAULT_LIMIT,
    **_: Any,
) -> dict[str, Any]:
    """Le uma conversa de pos-venda exata sem expor comprador ou URLs privadas."""

    from backend.services import ia_tools_marketplaces

    pack = str(pack_id or "").strip()
    order = str(order_id or "").strip()
    if not pack:
        match = re.search(r"\bpack(?:\s*(?:id|n[uú]mero|#))?\s*[:#-]?\s*(\d{5,})\b", str(message or ""), re.I)
        pack = match.group(1) if match else ""
    if not re.fullmatch(r"\d{5,30}", pack):
        result = _result(
            "mercado_livre_post_sale_detail", [], [],
            {"loja": loja, "pack_id": "", "order_id": order, "limit": limit},
            ["Informe o pack_id numerico exato da conversa."],
        )
        result.update({"success": False, "error": "pack_id_required", "coverage_complete": False, "zero_is_authoritative": False})
        return result
    if order and not re.fullmatch(r"\d{5,30}", order):
        result = _result(
            "mercado_livre_post_sale_detail", [], [],
            {"loja": loja, "pack_id": pack, "order_id": "", "limit": limit},
            ["O order_id informado nao e valido."],
        )
        result.update({"success": False, "error": "order_id_invalid", "coverage_complete": False, "zero_is_authoritative": False})
        return result

    exact_store, failure = ia_tools_marketplaces._ia_ml_resolver_loja_exata(client_id, loja)
    if not exact_store:
        result = _result(
            "mercado_livre_post_sale_detail", [], [],
            {"loja": loja, "pack_id": pack, "order_id": order, "limit": limit},
            [str((failure or {}).get("message") or "Loja Mercado Livre exata nao encontrada.")],
        )
        result.update({
            "success": False,
            "error": str((failure or {}).get("code") or "store_not_found"),
            "available_stores": list((failure or {}).get("available_stores") or []),
            "coverage_complete": False,
            "zero_is_authoritative": False,
        })
        return result

    try:
        from backend.services import perguntas_pos_venda_endpoints

        raw = perguntas_pos_venda_endpoints.ml_pos_venda_detalhe_conversa(
            loja=exact_store,
            pack_id=pack,
            order_id=order or None,
            client_id=client_id,
        )
    except Exception as exc:
        detail = str(getattr(exc, "detail", exc) or "Falha ao consultar a conversa.")[:300]
        result = _result(
            "mercado_livre_post_sale_detail", [], [],
            {"loja": exact_store, "pack_id": pack, "order_id": order, "limit": limit},
            [detail],
        )
        result.update({"success": False, "error": "integration_error", "coverage_complete": False, "zero_is_authoritative": False})
        return result

    conversation = raw.get("conversa") if isinstance(raw, dict) and isinstance(raw.get("conversa"), dict) else {}
    returned_pack = str(conversation.get("pack_id") or "").strip()
    returned_order = str(conversation.get("order_id") or "").strip()
    if (returned_pack and returned_pack != pack) or (order and returned_order and returned_order != order):
        result = _result(
            "mercado_livre_post_sale_detail", [], [],
            {"loja": exact_store, "pack_id": pack, "order_id": order, "limit": limit},
            ["A API retornou uma conversa fora do identificador exato solicitado; os dados foram bloqueados."],
        )
        result.update({"success": False, "error": "conversation_scope_mismatch", "coverage_complete": False, "zero_is_authoritative": False})
        return result
    messages_raw = conversation.get("messages") if isinstance(conversation.get("messages"), list) else (
        raw.get("mensagens") if isinstance(raw, dict) and isinstance(raw.get("mensagens"), list) else []
    )
    limit_safe = _safe_int(limit, 50, 1, 100)
    messages = []
    for raw_message in messages_raw[-limit_safe:]:
        safe_message = _post_sale_safe_message(raw_message)
        if safe_message:
            messages.append(safe_message)
    items = []
    for item in (conversation.get("items") or [])[:20]:
        if not isinstance(item, dict):
            continue
        items.append({
            "id": str(item.get("id") or "").strip()[:64],
            "sku": str(item.get("sku") or item.get("seller_sku") or "").strip()[:120],
            "title": _post_sale_safe_text(item.get("title"), 240),
            "quantity": item.get("quantity"),
            "permalink": str(item.get("permalink") or "").strip()[:500],
        })
    record = {
        "record_type": "mercado_livre_post_sale_conversation",
        "loja": exact_store,
        "pack_id": str(conversation.get("pack_id") or pack).strip(),
        "order_id": str(conversation.get("order_id") or order).strip(),
        "status": str(conversation.get("status") or "").strip()[:80],
        "date_created": str(conversation.get("date_created") or "").strip()[:64],
        "date_closed": str(conversation.get("date_closed") or "").strip()[:64],
        "last_message_date": str(conversation.get("last_message_date") or "").strip()[:64],
        "unread": bool(conversation.get("unread") or conversation.get("is_unread")),
        "unread_count": _safe_int(conversation.get("unread_count"), 0, 0, 100_000),
        "conversation_status": {
            key: str((conversation.get("conversation_status") or {}).get(key) or "").strip()[:100]
            for key in ("status", "substatus", "path")
        } if isinstance(conversation.get("conversation_status"), dict) else {},
        "items": items,
        "messages": messages,
        "messages_returned": len(messages),
        "messages_total": len(messages_raw),
        "messages_truncated": len(messages_raw) > limit_safe,
    }
    result = _result(
        "mercado_livre_post_sale_detail",
        [record],
        [{
            "source_id": f"mercado_livre_post_sale_{_safe_id(exact_store)}",
            "type": "external_api",
            "module": "perguntas_pos_venda",
            "path": f"Mercado Livre - conversa pos-venda - {exact_store}",
            "updated_at": _now(),
            "status": "available",
        }],
        {"loja": exact_store, "pack_id": pack, "order_id": order, "limit": limit_safe},
        ["Dados de comprador, remetente e URLs/IDs privados de anexos foram omitidos."],
    )
    result.update({
        "live_query": True,
        "source_freshness": "live",
        "coverage_complete": True,
        "zero_is_authoritative": False,
        "records_truncated": len(messages_raw) > limit_safe,
    })
    return result


def mercado_livre_readonly(
    *,
    client_id: str,
    message: str = "",
    loja: str = "",
    limit: int = DEFAULT_LIMIT,
    **_: Any,
) -> dict[str, Any]:
    if re.search(r"(pergunta|pos venda|pos-venda|question)", _norm(message)):
        result = questions_post_sale_query(client_id=client_id, message=message, loja=loja, limit=limit)
        result["tool_id"] = "mercado_livre_readonly"
        return result

    records = []
    warnings = []
    sources = []
    try:
        from backend.services import ia_tools_marketplaces

        raw = ia_tools_marketplaces._ia_tool_get_mercado_livre_listing(client_id, message or "listar anuncios ativos mercado livre", loja or None, None, _safe_int(limit, 20, 1, 100))
        result = (raw or {}).get("result") if isinstance(raw, dict) else {}
        matches = result.get("matches") if isinstance(result, dict) else []
        if isinstance(matches, list):
            records.extend(_redact(matches))
        sources.append({"source_id": "mercado_livre_api", "type": "external_api", "module": "mercado_livre", "path": "Mercado Livre read-only"})
    except Exception as exc:
        warnings.append(f"Mercado Livre anuncios: {str(exc)[:220]}")
    return _result("mercado_livre_readonly", records[: _safe_int(limit)], sources, {"message": message, "loja": loja, "limit": limit}, warnings)


def execute_readonly_source_tool(
    *,
    client_id: str,
    tool_id: str,
    message: str = "",
    loja: str = "",
    data_inicio: str = "",
    data_fim: str = "",
    limit: int = DEFAULT_LIMIT,
    query_deadline_seconds: int = 15,
    args: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    args = dict(args or {}) if isinstance(args, dict) else {}
    common = {
        "client_id": client_id,
        "message": message,
        "source_id": str(args.get("source_id") or args.get("fonte") or ""),
        "module": str(args.get("module") or args.get("modulo") or ""),
        "limit": limit,
    }
    if tool_id == "source_discovery":
        return discover_data_sources(client_id=client_id, query=message, module=common["module"], source_type=str(args.get("type") or args.get("tipo") or ""), limit=limit)
    if tool_id == "local_database_query":
        return local_database_query(**common, sql=str(args.get("sql") or ""))
    if tool_id == "local_csv_query":
        return local_csv_query(**common)
    if tool_id == "local_cache_query":
        return local_cache_query(**common)
    if tool_id == "sync_logs_query":
        return sync_logs_query(**common)
    if tool_id == "questions_post_sale_query":
        return questions_post_sale_query(
            client_id=client_id,
            message=message,
            loja=loja,
            status=str(args.get("status") or ""),
            limit=limit,
            all_stores=bool(args.get("all_stores") or args.get("todas_lojas") or args.get("separar_por_loja")),
            query_deadline_seconds=query_deadline_seconds,
        )
    if tool_id == "mercado_livre_post_sale_detail":
        return mercado_livre_post_sale_detail(
            client_id=client_id,
            message=message,
            loja=loja,
            pack_id=str(args.get("pack_id") or args.get("pack") or ""),
            order_id=str(args.get("order_id") or args.get("id_pedido") or ""),
            limit=limit,
        )
    if tool_id == "fiscal_local_query":
        return fiscal_local_query(**common)
    if tool_id == "mercado_livre_readonly":
        return mercado_livre_readonly(client_id=client_id, message=message, loja=loja, limit=limit)
    return _result(tool_id, [], [], {"message": message}, [f"Ferramenta read-only desconhecida: {tool_id}"])


def _result(tool_id: str, records: list[dict[str, Any]], sources: list[dict[str, Any]], filters: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    records = [_redact(item) for item in (records or []) if isinstance(item, dict)]
    compact_sources = []
    seen = set()
    for source in sources or []:
        sid = str((source or {}).get("source_id") or (source or {}).get("path") or "")
        if sid in seen:
            continue
        seen.add(sid)
        compact_sources.append(_redact(source))
    return {
        "success": True,
        "tool_id": tool_id,
        "records": records,
        "rows": records,
        "top_rows": records[:20],
        "sources": compact_sources[:30],
        "queried_at": _now(),
        "generated_at": _now(),
        "filters": _redact(filters),
        "record_count": len(records),
        "warnings": [str(item or "")[:400] for item in warnings[:20]],
        "empty_reason": "" if records else "Nenhum registro encontrado nas fontes read-only consultadas.",
        "fallbacks_attempted": [],
        "sensitive_fields_redacted": True,
    }


configure_codex_readonly_sources_runtime()


__all__ = [
    "READONLY_SOURCES_VERSION",
    "configure_codex_readonly_sources_runtime",
    "discover_data_sources",
    "execute_readonly_source_tool",
    "local_database_query",
    "local_csv_query",
    "local_cache_query",
    "sync_logs_query",
    "questions_post_sale_query",
    "mercado_livre_post_sale_detail",
    "fiscal_local_query",
    "mercado_livre_readonly",
]
