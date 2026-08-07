"""Performance primitives shared by Vendas read endpoints.

The helpers in this module deliberately keep the public API unchanged. They
provide bounded response/file caches, read-only SQLite connections, indexable
date predicates and one-time preparation of existing Vendas databases.
"""

from __future__ import annotations

import copy
import functools
import inspect
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from backend.services.sqlite_coordination import sqlite_lock_for_path

from .dependencies import get_tenant_path, get_vendas_dependencies


VENDAS_CACHE_TTL_SECONDS = 60.0
VENDAS_CACHE_MAX_ENTRIES = 128

_CACHE_LOCK = threading.RLock()
_RESPONSE_CACHE: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
_FILE_CACHE: OrderedDict[tuple, Any] = OrderedDict()
_SCHEMA_CACHE: OrderedDict[tuple, frozenset[str]] = OrderedDict()
_CACHE_GENERATIONS: dict[str, int] = {}
_CACHE_STATS = {"hits": 0, "misses": 0, "sets": 0, "invalidations": 0}

_PREPARE_LOCK = threading.RLock()
_PREPARE_THREAD_LOCK = threading.Lock()
_PREPARE_THREAD_STARTED = False
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    try:
        hash(value)
        return value
    except Exception:
        return repr(value)


def vendas_file_signature(path: str | os.PathLike[str] | None) -> tuple:
    if not path:
        return ("", None, None)
    absolute = os.path.abspath(os.fspath(path))
    try:
        stat = os.stat(absolute)
        return (absolute, int(stat.st_size), int(stat.st_mtime_ns))
    except OSError:
        return (absolute, None, None)


def vendas_sources_signature(paths: Iterable[str | os.PathLike[str]]) -> tuple:
    unique = {os.path.abspath(os.fspath(path)) for path in paths if path}
    return tuple(vendas_file_signature(path) for path in sorted(unique))


def vendas_source_paths(
    client_id: str,
    db_paths: Iterable[str] = (),
    *,
    include_products: bool = False,
    include_stock: bool = False,
) -> list[str]:
    paths = [str(path) for path in db_paths if path]
    try:
        tenant_path = get_tenant_path(client_id)
    except Exception:
        tenant_path = ""
    if tenant_path:
        paths.append(os.path.join(tenant_path, "lojas_virtuais_map.json"))
        if include_products:
            paths.append(os.path.join(tenant_path, "produtos_compilado.csv"))
        if include_stock:
            paths.append(os.path.join(tenant_path, "estoque_historico.db"))
            paths.append(os.path.join(tenant_path, "lojas_config.json"))
    return paths


def _cache_get(key: tuple) -> tuple[bool, Any]:
    now = time.monotonic()
    with _CACHE_LOCK:
        item = _RESPONSE_CACHE.get(key)
        if item is None:
            _CACHE_STATS["misses"] += 1
            return False, None
        expires_at, value = item
        if expires_at <= now:
            _RESPONSE_CACHE.pop(key, None)
            _CACHE_STATS["misses"] += 1
            return False, None
        _RESPONSE_CACHE.move_to_end(key)
        _CACHE_STATS["hits"] += 1
        return True, copy.deepcopy(value)


def _cache_set(key: tuple, value: Any, ttl_seconds: float) -> None:
    with _CACHE_LOCK:
        _RESPONSE_CACHE[key] = (time.monotonic() + max(1.0, ttl_seconds), copy.deepcopy(value))
        _RESPONSE_CACHE.move_to_end(key)
        while len(_RESPONSE_CACHE) > VENDAS_CACHE_MAX_ENTRIES:
            _RESPONSE_CACHE.popitem(last=False)
        _CACHE_STATS["sets"] += 1


def cache_vendas_response(
    namespace: str,
    paths_provider: Callable[[Mapping[str, Any]], Iterable[str]],
    *,
    ttl_seconds: float = VENDAS_CACHE_TTL_SECONDS,
):
    """Cache a synchronous endpoint using filters and source-file signatures."""

    def decorator(func):
        signature = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            bound = signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            client_id = bound.arguments.get("client_id")
            if not isinstance(client_id, str) or not client_id.strip():
                return func(*args, **kwargs)

            try:
                source_paths = list(paths_provider(bound.arguments) or ())
            except Exception:
                source_paths = []

            with _CACHE_LOCK:
                generation = _CACHE_GENERATIONS.get(client_id, 0)
            filters = tuple(
                sorted(
                    (name, _freeze(value))
                    for name, value in bound.arguments.items()
                    if name != "client_id"
                )
            )
            key = (
                str(namespace),
                client_id,
                generation,
                filters,
                vendas_sources_signature(source_paths),
            )
            found, cached = _cache_get(key)
            if found:
                return cached
            result = func(*args, **kwargs)
            _cache_set(key, result, ttl_seconds)
            return result

        return wrapper

    return decorator


def invalidate_vendas_cache(client_id: str | None = None) -> None:
    """Invalidate response caches after Vendas writes or mapping changes."""

    with _CACHE_LOCK:
        if client_id:
            _CACHE_GENERATIONS[client_id] = _CACHE_GENERATIONS.get(client_id, 0) + 1
            for key in [key for key in _RESPONSE_CACHE if len(key) > 1 and key[1] == client_id]:
                _RESPONSE_CACHE.pop(key, None)
        else:
            _RESPONSE_CACHE.clear()
            _FILE_CACHE.clear()
            _SCHEMA_CACHE.clear()
            _CACHE_GENERATIONS.clear()
        _CACHE_STATS["invalidations"] += 1


def vendas_cache_stats() -> dict[str, int]:
    with _CACHE_LOCK:
        return {
            **_CACHE_STATS,
            "response_entries": len(_RESPONSE_CACHE),
            "file_entries": len(_FILE_CACHE),
            "schema_entries": len(_SCHEMA_CACHE),
        }


def cached_file_value(namespace: str, path: str, loader: Callable[[str], Any], *, max_entries: int = 16) -> Any:
    """Cache immutable parsed file data by path, size and mtime."""

    key = (namespace, vendas_file_signature(path))
    with _CACHE_LOCK:
        if key in _FILE_CACHE:
            _FILE_CACHE.move_to_end(key)
            return _FILE_CACHE[key]
    value = loader(path)
    with _CACHE_LOCK:
        _FILE_CACHE[key] = value
        _FILE_CACHE.move_to_end(key)
        while len(_FILE_CACHE) > max_entries:
            _FILE_CACHE.popitem(last=False)
    return value


def open_vendas_readonly(path: str, *, row_factory=None) -> sqlite3.Connection:
    """Open a query-only SQLite connection without creating a missing file."""

    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    if row_factory is not None:
        conn.row_factory = row_factory
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def cached_table_columns(path: str, table: str, conn: sqlite3.Connection) -> frozenset[str]:
    if not _IDENTIFIER_RE.fullmatch(table):
        raise ValueError("Invalid SQLite table name")
    key = (vendas_file_signature(path), table)
    with _CACHE_LOCK:
        cached = _SCHEMA_CACHE.get(key)
        if cached is not None:
            _SCHEMA_CACHE.move_to_end(key)
            return cached
    columns = frozenset(str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall())
    with _CACHE_LOCK:
        _SCHEMA_CACHE[key] = columns
        _SCHEMA_CACHE.move_to_end(key)
        while len(_SCHEMA_CACHE) > 256:
            _SCHEMA_CACHE.popitem(last=False)
    return columns


def select_compatible_column(columns: set[str] | frozenset[str], name: str, default: str = "''") -> str:
    if not _IDENTIFIER_RE.fullmatch(name):
        raise ValueError("Invalid SQLite column name")
    return name if name in columns else f"{default} AS {name}"


def append_indexable_date_filter(
    conditions: list[str],
    params: list,
    column: str,
    start: str | None,
    end: str | None,
) -> None:
    """Add lexicographic ISO-date bounds without wrapping the indexed column."""

    if not _IDENTIFIER_RE.fullmatch(column):
        raise ValueError("Invalid SQLite column name")
    if start:
        conditions.append(f"{column} >= ?")
        params.append(start)
    if end:
        try:
            end_exclusive = (date.fromisoformat(str(end)[:10]) + timedelta(days=1)).isoformat()
            conditions.append(f"{column} < ?")
            params.append(end_exclusive)
        except ValueError:
            conditions.append(f"date({column}) <= ?")
            params.append(end)


_VENDAS_MIGRATIONS = {
    "devolucao": "INTEGER DEFAULT 0",
    "numero_nf": "TEXT",
    "comprador": "TEXT",
    "unidade_negocio": "TEXT",
    "nota_fiscal_id": "TEXT",
    "loja_id": "TEXT",
    "unidade_id": "TEXT",
    "intermediador_nome": "TEXT",
    "intermediador_cnpj": "TEXT",
}

_NOTAS_MIGRATIONS = {
    "origem_codigo": "TEXT",
    "finalidade_operacao": "TEXT",
    "loja_conta": "TEXT",
    "unidade_negocio": "TEXT",
    "unidade_negocio_virtual": "TEXT",
}

_INDEX_SPECS = {
    "vendas": (
        ("idx_vendas_data", ("data",)),
        ("idx_vendas_sku_data", ("sku", "data")),
        ("idx_vendas_loja_data", ("loja_conta", "data")),
        ("idx_vendas_unidade_data", ("unidade_negocio", "data")),
    ),
    "notas_entrada": (
        ("idx_notas_entrada_loja_data", ("loja_conta", "data_emissao")),
    ),
    "notas_entrada_itens": (
        ("idx_notas_itens_sku_data", ("sku", "data_emissao")),
        ("idx_notas_itens_loja_data", ("loja_conta", "data_emissao")),
        ("idx_notas_itens_unidade_data", ("unidade_negocio_virtual", "data_emissao")),
    ),
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table,),
        ).fetchone()
    )


def prepare_vendas_database(path: str) -> bool:
    """Migrate/index an existing Vendas DB outside request read paths."""

    if not path or not os.path.isfile(path):
        return False
    with _PREPARE_LOCK, sqlite_lock_for_path(path):
        conn = sqlite3.connect(path, timeout=15)
        try:
            conn.execute("PRAGMA busy_timeout = 15000")
            for table, migrations in (
                ("vendas", _VENDAS_MIGRATIONS),
                ("notas_entrada", _NOTAS_MIGRATIONS),
                ("notas_entrada_itens", _NOTAS_MIGRATIONS),
            ):
                if not _table_exists(conn, table):
                    continue
                columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                for column, definition in migrations.items():
                    if column not in columns:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                        columns.add(column)
                for index_name, index_columns in _INDEX_SPECS.get(table, ()):
                    if all(column in columns for column in index_columns):
                        joined = ", ".join(index_columns)
                        conn.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table}({joined})")
            conn.commit()
            return True
        finally:
            conn.close()


def prepare_vendas_databases_in_root(info_root: str) -> dict[str, int]:
    """Prepare active root/tenant DBs while skipping backup trees."""

    result = {"found": 0, "prepared": 0, "errors": 0}
    root = os.path.abspath(info_root)
    candidate_dirs = [root]
    try:
        candidate_dirs.extend(
            entry.path
            for entry in os.scandir(root)
            if entry.is_dir() and not entry.name.startswith(("_", "."))
        )
    except OSError:
        return result

    for directory in candidate_dirs:
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file() or not entry.name.startswith("vendas_historico") or not entry.name.endswith(".db"):
                continue
            result["found"] += 1
            try:
                if prepare_vendas_database(entry.path):
                    result["prepared"] += 1
            except Exception as exc:
                result["errors"] += 1
                get_vendas_dependencies().logger.warning(
                    "[VENDAS] Falha ao preparar banco %s: %s", entry.path, exc
                )
    return result


def _vendas_preparar_bancos_background() -> None:
    """Start the one-time Vendas DB preparation worker at application startup."""

    global _PREPARE_THREAD_STARTED
    with _PREPARE_THREAD_LOCK:
        info_root = str(get_vendas_dependencies().paths.info_dir)
        if _PREPARE_THREAD_STARTED or not info_root:
            return
        _PREPARE_THREAD_STARTED = True

    def worker():
        started = time.perf_counter()
        result = prepare_vendas_databases_in_root(info_root)
        duration_ms = int((time.perf_counter() - started) * 1000)
        get_vendas_dependencies().logger.info(
            "[VENDAS] Preparacao de bancos: encontrados=%s preparados=%s erros=%s duracao_ms=%s",
            result["found"],
            result["prepared"],
            result["errors"],
            duration_ms,
        )

    threading.Thread(target=worker, name="vendas-db-prepare", daemon=True).start()


__all__ = [
    "VENDAS_CACHE_TTL_SECONDS",
    "VENDAS_CACHE_MAX_ENTRIES",
    "cache_vendas_response",
    "invalidate_vendas_cache",
    "vendas_cache_stats",
    "vendas_file_signature",
    "vendas_sources_signature",
    "vendas_source_paths",
    "cached_file_value",
    "open_vendas_readonly",
    "cached_table_columns",
    "select_compatible_column",
    "append_indexable_date_filter",
    "prepare_vendas_database",
    "prepare_vendas_databases_in_root",
    "_vendas_preparar_bancos_background",
]
