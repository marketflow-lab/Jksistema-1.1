"""Durable store cost projections, refreshed only when their sources change."""
from __future__ import annotations

from collections import OrderedDict
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import threading

from fastapi import HTTPException

from backend.services import cadastro_lojas_produtos as cadastro
from backend.services.retired_store_names import TOMBSTONES_FILE, parse_retired_store_names
from backend.services.source_file_revision import _RevisionBusy, _open_read, content_stamp


SCHEMA = 1
DIRECTORY = ".cadastro_custos_cache"
MAX_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 64
_LOCKS = tuple(threading.RLock() for _ in range(32))
_MEMORY_LOCK = threading.RLock()
_MEMORY: OrderedDict[str, dict] = OrderedDict()
_MEMORY_SIZES: dict[str, int] = {}
# These fields determine membership, legacy precedence and the final cost.
# Photos, balances, prices and timestamps do not change this projection.
_COST_FIELDS = frozenset({"sku", "sku_normalizado", "store_id", "loja", "loja_sync", "custo", "deleted_at_utc"})


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _linked(path):
    try:
        info = path.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def _probe(client_id, store_id, include_deleted):
    root = Path(os.path.realpath(cadastro.get_tenant_path(client_id)))
    current = cadastro._lojas_atuais(client_id)
    cadastro.resolver_loja_cadastro(client_id, store_id, _lojas_capturadas=current)
    stores = [{field: row[field] for field in ("store_id", "nome", "nomes_anteriores") if field in row}
              for row in current]
    names = (cadastro.CADASTRO_PRODUTOS_LOJAS_ARQUIVO, *cadastro._ARQUIVOS_LEGADOS.values(), TOMBSTONES_FILE)
    versions = []
    for name in names:
        try:
            versions.append((name, content_stamp(root / name)))
        except FileNotFoundError:
            versions.append((name, None))
        except _RevisionBusy:
            raise HTTPException(status_code=409, detail="As fontes do Cadastro estão mudando. Tente novamente.") from None
    identity = _digest([SCHEMA, os.path.normcase(str(root)), str(client_id), store_id, include_deleted])
    return root, stores, names, identity, _digest([stores, versions])


def _read_disk(root, identity):
    directory = root / DIRECTORY
    path = directory / (identity + ".json")
    try:
        if _linked(directory) or _linked(path):
            return None
        with _open_read(path) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return None
        envelope = json.loads(raw)
        record = envelope["record"]
        if (envelope.get("schema") != SCHEMA or envelope.get("identity") != identity
                or envelope.get("checksum") != _digest(record)
                or not isinstance(record.get("products"), list)
                or record.get("revision") != _digest(record["products"])
                or not all(isinstance(record.get(field), str) for field in ("physical", "semantic"))):
            return None
        return record
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return None


def _write_disk(root, identity, record):
    temporary = None
    try:
        directory = root / DIRECTORY
        if _linked(directory):
            return
        directory.mkdir(exist_ok=True)
        if _linked(directory):
            return
        content = _json({"schema": SCHEMA, "identity": identity, "record": record, "checksum": _digest(record)})
        if len(content) > MAX_BYTES:
            return
        fd, temporary = tempfile.mkstemp(prefix=".write-", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.replace(temporary, directory / (identity + ".json"))
        temporary = None
        # Retain one current version per store; never prune by elapsed time.
        files = sorted((path for path in directory.glob("*.json")
                        if len(path.stem) == 64 and all(c in "0123456789abcdef" for c in path.stem)
                        and not _linked(path)), key=lambda path: path.stat().st_mtime_ns)
        sizes = {path: path.stat().st_size for path in files}
        total = sum(sizes.values())
        while files and (len(files) > MAX_ENTRIES or total > MAX_BYTES):
            oldest = files.pop(0)
            oldest.unlink(missing_ok=True)
            total -= sizes[oldest]
    except (OSError, ValueError, TypeError):
        # Cache storage failure must not prevent reading canonical costs.
        pass
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def _remember(identity, record):
    with _MEMORY_LOCK:
        if _MEMORY.get(identity) is record:
            _MEMORY.move_to_end(identity)
            return
        _MEMORY[identity] = record
        _MEMORY_SIZES[identity] = len(_json(record))
        _MEMORY.move_to_end(identity)
        while len(_MEMORY) > MAX_ENTRIES or sum(_MEMORY_SIZES.get(key, 0) for key in _MEMORY) > MAX_BYTES:
            evicted, _ = _MEMORY.popitem(last=False)
            _MEMORY_SIZES.pop(evicted, None)


def _capture(root, stores, names):
    sources = {}
    for name in names:
        try:
            with _open_read(root / name) as stream:
                sources[name] = stream.read()
        except FileNotFoundError:
            sources[name] = b"[]" if name == TOMBSTONES_FILE else b""
    semantic = []
    parsed = {}
    for name, raw in sources.items():
        if name == TOMBSTONES_FILE:
            retired = parse_retired_store_names(raw)
            semantic.append((name, retired.names, retired.invalid))
        else:
            rows, _ = cadastro._ler_csv_capturado(raw)
            parsed[name] = rows
            semantic.append((name, [{key: value for key, value in row.items() if key in _COST_FIELDS}
                                    for row in rows]))
    records = cadastro._normalizar_registros_persistidos(parsed[cadastro.CADASTRO_PRODUTOS_LOJAS_ARQUIVO])
    return sources, records, _digest([stores, semantic])


def custos_loja_snapshot(client_id: str, store_id: str, *, include_deleted: bool = False):
    """Return (products, content revision) after checking current store access."""
    store_id = str(store_id or "").strip()
    initial = _probe(client_id, store_id, include_deleted)
    with _LOCKS[hash(initial[3]) % len(_LOCKS)]:
        for _attempt in range(3):
            root, stores, names, identity, physical = initial if _attempt == 0 else _probe(client_id, store_id, include_deleted)
            with _MEMORY_LOCK:
                cached = _MEMORY.get(identity)
            if cached is None:
                cached = _read_disk(root, identity)
            if cached is not None and cached["physical"] == physical:
                if _probe(client_id, store_id, include_deleted)[4] != physical:
                    continue
                _remember(identity, cached)
                return copy.deepcopy(cached["products"]), cached["revision"]

            sources, records, semantic = _capture(root, stores, names)
            if cached is not None and cached["semantic"] == semantic:
                products = cached["products"]
            else:
                full = cadastro._listar_produtos_loja_sync(
                    client_id, store_id, include_deleted=include_deleted,
                    _registros_persistidos=records, _lojas_capturadas=stores,
                    _fontes_capturadas=sources, _incluir_fotos=False,
                )
                products = [{"store_id": row["store_id"], "sku": row["sku"], "custo": row.get("custo")}
                            for row in full]
            if _probe(client_id, store_id, include_deleted)[4] != physical:
                continue
            record = {"physical": physical, "semantic": semantic, "products": products, "revision": _digest(products)}
            _remember(identity, record)
            _write_disk(root, identity, record)
            return copy.deepcopy(products), record["revision"]
    raise HTTPException(status_code=409, detail="O Cadastro mudou durante a consulta dos custos. Tente novamente.")
