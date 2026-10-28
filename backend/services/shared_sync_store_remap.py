"""Pure, typed projection of validated incoming sources onto local store IDs.

The caller authorizes the directional mapping and validates the original bundle.
This module never rewrites transport manifests, hashes, receipts or OAuth data.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import unicodedata

from fastapi import HTTPException


_CODE = "shared_sync_store_remap_ambiguous"
_CSV_FILES = {
    "cadastro": {
        "cadastro_produtos.csv", "cadastro_produtos_lojas.csv",
        "cadastro_custos_lojas.csv", "produtos_compilado.csv",
    },
    "vendas": {"produtos_compilado.csv"},
}
_SALES_TABLES = {"vendas", "notas_entrada", "notas_entrada_itens"}
_STOCK_TABLES = {
    "estoque_historico", "estoque_sync_eventos", "estoque_sync_evento_itens",
    "estoque_historico_migracoes",
}
_NAME_FIELDS = {"loja_sync", "loja_conta"}
_PROTECTED_FILES = {
    "lojas_config.json", "integracoes.json", "lojas_sync_tombstones.json",
    "manifest.json",
}
_PHOTO = re.compile(r"^(cadastro_fotos/lojas/)(sid-[a-f0-9]{64})(/[^/\\]+)$")


def _fail(reason: str) -> None:
    # Neither payload values, credentials, account identities nor file paths
    # belong in this error. Reasons are fixed implementation-owned constants.
    raise HTTPException(409, detail={
        "code": _CODE,
        "message": "As referencias de loja recebidas nao podem ser conciliadas com seguranca.",
        "reason": reason,
    })


def _mapping(value: dict | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(target, str)
        or not key or not target or key != key.strip() or target != target.strip()
        for key, target in value.items()
    ):
        _fail("invalid_mapping")
    result = {key: target for key, target in value.items() if key != target}
    # A one-step projection must be idempotent, including on retries.
    if any(target in result for target in result.values()):
        _fail("mapping_chain")
    return result


def _segment(store_id: str) -> str:
    # Same pure contract as cadastro_fotos._cadastro_store_id_foto_segmento;
    # importing that module would initialize the application runtime.
    return "sid-" + hashlib.sha256(store_id.encode("utf-8")).hexdigest()


def _photo_path(value: str, segments: dict[str, str]) -> str:
    match = _PHOTO.fullmatch(value)
    if match:
        return match[1] + segments.get(match[2], match[2]) + match[3]
    return value


def _column(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.strip())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def _photo_column(value: str) -> bool:
    # Mirrors the official candidate-column predicate without runtime imports.
    return any(part in _column(value) for part in (
        "foto", "imagem", "image", "picture", "thumbnail", "thumb",
    ))


def _reference(value: str, segments: dict[str, str]) -> str:
    # Only canonical relative file references are translated. External URLs,
    # data URLs and arbitrary prose are not file references.
    return _photo_path(value, segments)


def _read_csv(data: bytes) -> tuple[list[list[str]], csv.Dialect, bool]:
    try:
        text = data.decode("utf-8-sig")
        sample = text[:65536]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return list(csv.reader(io.StringIO(text, newline=""), dialect, strict=True)), dialect, data.startswith(b"\xef\xbb\xbf")
    except (UnicodeError, csv.Error):
        _fail("invalid_csv")


def _csv(data: bytes, ids: dict, names: dict, segments: dict) -> bytes:
    rows, dialect, bom = _read_csv(data)
    if not rows:
        return data
    header = rows[0]
    normalized = [_column(col) for col in header]
    if len(set(normalized)) != len(normalized):
        _fail("ambiguous_csv_columns")
    if any(len(row) != len(header) for row in rows[1:]):
        _fail("invalid_csv_rows")
    store_idx = normalized.index("store_id") if "store_id" in normalized else None
    sku_idxs = [normalized.index(key) for key in ("sku", "sku_normalizado") if key in normalized]
    name_idxs = [idx for idx, key in enumerate(normalized) if key in _NAME_FIELDS]
    photo_idxs = [idx for idx, col in enumerate(header) if _photo_column(col)]
    changed = False
    projected = [header]
    identities: dict[tuple, tuple] = {}
    for original in rows[1:]:
        row = list(original)
        if store_idx is not None:
            row[store_idx] = ids.get(row[store_idx], row[store_idx])
        for idx in name_idxs:
            row[idx] = names.get(row[idx], row[idx])
        for idx in photo_idxs:
            row[idx] = _reference(row[idx], segments)
        identity_changed = any(row[idx] != original[idx] for idx in (
            ([store_idx] if store_idx is not None else []) + name_idxs
        ))
        if identity_changed and not sku_idxs:
            _fail("csv_identity_without_sku")
        if sku_idxs and (store_idx is not None or name_idxs):
            # SKU comparison agrees with the scoped product key; store IDs
            # remain exact and case-sensitive.
            identity_idx = store_idx if store_idx is not None and row[store_idx] else (
                name_idxs[0] if name_idxs else store_idx
            )
            sku = next((row[idx].strip().casefold() for idx in sku_idxs if row[idx].strip()), "")
            before = (original[identity_idx], sku)
            after = (row[identity_idx], sku)
            if after in identities and identities[after] != before:
                _fail("csv_row_collision")
            identities[after] = before
        changed |= row != original
        projected.append(row)
    if not changed:
        return data
    output = io.StringIO(newline="")
    line_end = "\r\n" if b"\r\n" in data else "\n"
    csv.writer(output, dialect, lineterminator=line_end).writerows(projected)
    encoded = output.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" if bom else b"") + encoded


def _json(data: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("duplicate_json_key")
            result[key] = value
        return result
    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique)
    except (UnicodeError, ValueError, RecursionError):
        _fail("invalid_json")


def _json_bytes(value, original: bytes) -> bytes:
    encoded = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    return (b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b"") + encoded


def _photo_config(data: bytes, ids: dict) -> bytes:
    config = _json(data)
    if (
        not isinstance(config, dict)
        or set(config) != {"schema", "strict_store_scope", "shared_groups"}
        or config["schema"] != "jk.cadastro.fotos.v1"
        or not isinstance(config["strict_store_scope"], bool)
        or not isinstance(config["shared_groups"], list)
    ):
        _fail("invalid_photo_config")
    groups = []
    original_ids: set[str] = set()
    projected_ids: set[str] = set()
    group_ids: set[str] = set()
    changed = False
    for group in config["shared_groups"]:
        if not isinstance(group, dict) or set(group) != {"group_id", "store_ids"}:
            _fail("invalid_photo_group")
        members = group["store_ids"]
        group_id = group["group_id"]
        if (
            not isinstance(group_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", group_id)
            or group_id in group_ids or not isinstance(members, list) or len(members) < 2
            or any(not isinstance(item, str) or not item or item != item.strip() for item in members)
            or len(set(members)) != len(members) or original_ids.intersection(members)
        ):
            _fail("invalid_photo_group")
        group_ids.add(group_id)
        original_ids.update(members)
        translated = list(dict.fromkeys(ids.get(item, item) for item in members))
        changed |= translated != members
        if projected_ids.intersection(translated):
            _fail("photo_group_overlap")
        projected_ids.update(translated)
        # Two or more proven equivalent identities no longer form a sharing
        # group. A singleton cannot be passed to the official strict parser.
        if len(translated) == 1:
            continue
        groups.append({**group, "store_ids": translated})
    if not changed:
        return data
    return _json_bytes({**config, "shared_groups": groups}, data)


def _has_unknown_reference(value, ids: dict, names: dict) -> bool:
    """Inspect typed keys only; never interpret free text as an identity."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "store_id" and isinstance(item, str) and item in ids:
                return True
            if key == "store_ids" and isinstance(item, list) and any(
                isinstance(member, str) and member in ids for member in item
            ):
                return True
            if key in _NAME_FIELDS | {"loja"} and isinstance(item, str) and item in names:
                return True
            if _photo_column(key) and isinstance(item, str) and ids:
                segments = {_segment(source): _segment(target) for source, target in ids.items()}
                if _reference(item, segments) != item:
                    return True
            if isinstance(item, (dict, list)) and _has_unknown_reference(item, ids, names):
                return True
    elif isinstance(value, list):
        return any(_has_unknown_reference(item, ids, names) for item in value)
    return False


def _planilha_key(name: str) -> str:
    value = unicodedata.normalize("NFKD", name)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _planilhas(data: bytes, ids: dict, names: dict) -> bytes:
    payload = _json(data)
    if not isinstance(payload, dict) or not isinstance(payload.get("planilhas"), dict):
        if _has_unknown_reference(payload, ids, names):
            _fail("unknown_json_schema")
        return data
    result = {}
    changed = False
    for key, item in payload["planilhas"].items():
        original_name = item.get("loja") or key if isinstance(item, dict) else key
        if not isinstance(original_name, str):
            _fail("invalid_planilha_identity")
        translated = names.get(original_name, original_name)
        if _has_unknown_reference(item, ids, {}):
            _fail("unknown_planilha_identity")
        new_key = key
        new_item = item
        if translated != original_name:
            new_key = _planilha_key(translated)
            if not new_key:
                _fail("invalid_planilha_identity")
            # The reader accepts historical key->URL entries too.
            new_item = {**item, "loja": translated} if isinstance(item, dict) else item
            changed = True
        if new_key in result:
            _fail("planilha_collision")
        result[new_key] = new_item
    if not changed:
        return data
    return _json_bytes({**payload, "planilhas": result}, data)


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sqlite(data: bytes, rel: str, ids: dict, names: dict, *, references_only=False) -> bytes | bool:
    database = sqlite3.connect(":memory:")
    try:
        database.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        database.execute("PRAGMA trusted_schema=OFF")
        database.execute("PRAGMA temp_store=MEMORY")
        database.deserialize(data)
        allowed = (
            _STOCK_TABLES if rel == "estoque_historico.db" else _SALES_TABLES
            if re.fullmatch(r"vendas_historico(?:_[^/\\]+)?\.db", rel) else set()
        )
        tables = database.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        plans = []
        for (table,) in tables:
            cols = [row[1] for row in database.execute(f"PRAGMA table_info({_quoted(table)})")]
            for column in ("store_id", "loja_conta", "loja_sync", "loja"):
                if column not in cols:
                    continue
                replacements = ids if column == "store_id" else names
                present = {
                    row[0] for row in database.execute(
                        f"SELECT DISTINCT {_quoted(column)} FROM {_quoted(table)}"
                    ) if isinstance(row[0], str)
                }
                relevant = {key: value for key, value in replacements.items() if key in present}
                if not relevant:
                    continue
                if references_only:
                    return True
                if table not in allowed:
                    _fail("unknown_sqlite_table")
                if column != "store_id":
                    # Sales id_unico and stock event IDs/payload hashes can be
                    # derived from the label. No proven key rewrite exists.
                    _fail("sqlite_name_identity_unverified")
                plans.append((table, column, relevant))
        if not plans:
            return False if references_only else data
        if database.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1"
        ).fetchone():
            _fail("sqlite_schema_unsupported")
        for (table,) in tables:
            changed_columns = {column for name, column, _ in plans if name == table}
            for fk in database.execute(f"PRAGMA foreign_key_list({_quoted(table)})"):
                referenced = {column for name, column, _ in plans if name == fk[2]}
                if fk[3] in changed_columns or fk[4] in referenced:
                    _fail("sqlite_identity_foreign_key")
        database.execute("BEGIN")
        for table, column, relevant in plans:
            # COLLATE BINARY is deliberate even when a legacy schema declared
            # NOCASE: durable store IDs must be matched exactly.
            for original, target in relevant.items():
                database.execute(
                    f"UPDATE {_quoted(table)} SET {_quoted(column)}=? "
                    f"WHERE {_quoted(column)} COLLATE BINARY=?",
                    (target, original),
                )
        if database.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            _fail("sqlite_integrity")
        database.commit()
        return database.serialize()
    except sqlite3.Error:
        _fail("sqlite_remap_failed")
    finally:
        database.close()


def sources_reference_store(scope, sources, store_id, name):
    """Detect typed references even when an alias keeps the same display name.

    A legacy sale can belong to an alias without requiring any rewritten byte.
    This inspection shares the projection parsers and never opens local files.
    """
    ids = {store_id: store_id} if store_id else {}
    labels = {name: name} if name else {}
    for rel, data in sources:
        if not isinstance(rel, str) or not isinstance(data, bytes):
            _fail("invalid_source")
        if rel in _PROTECTED_FILES:
            continue
        if rel.endswith((".db", ".sqlite", ".sqlite3")):
            if _sqlite(data, rel, ids, labels, references_only=True):
                return True
        elif rel in _CSV_FILES.get(scope, set()):
            rows, _, _ = _read_csv(data)
            if not rows:
                continue
            columns = [_column(column) for column in rows[0]]
            if len(set(columns)) != len(columns) or any(len(row) != len(columns) for row in rows[1:]):
                _fail("invalid_csv_rows")
            for row in rows[1:]:
                if any((column == "store_id" and value in ids)
                       or (column in _NAME_FIELDS | {"loja"} and value in labels)
                       for column, value in zip(columns, row)):
                    return True
        elif rel.endswith(".json") and _has_unknown_reference(_json(data), ids, labels):
            return True
    return False


def remap_store_sources(
    scope: str,
    sources: list[tuple[str, bytes]],
    mapping: dict[str, str],
    names: dict[str, str] | None = None,
) -> list[tuple[str, bytes]]:
    """Return new sources without changing input objects or touching files.

    Only typed identities in supported formats are rewritten. The caller must
    still run its normal scope, tenant, photo and row validators afterwards.
    """
    ids = _mapping(mapping)
    labels = _mapping(names)
    if not ids and not labels:
        return list(sources)
    segments = {_segment(source): _segment(target) for source, target in ids.items()}
    result = []
    targets: dict[str, str] = {}
    for rel, data in sources:
        if not isinstance(rel, str) or not isinstance(data, bytes):
            _fail("invalid_source")
        target = rel
        transformed = data
        if rel in _PROTECTED_FILES or scope == "lojas_integracoes":
            pass
        elif scope == "cadastro" and _PHOTO.fullmatch(rel):
            target = _photo_path(rel, segments)
        elif rel in _CSV_FILES.get(scope, set()):
            transformed = _csv(data, ids, labels, segments)
        elif scope == "cadastro" and rel == "cadastro_fotos_config.json":
            transformed = _photo_config(data, ids)
        elif scope == "favoritos_planilhas" and rel == "favoritos_planilhas_lojas.json":
            transformed = _planilhas(data, ids, labels)
        elif scope == "vendas" and (
            re.fullmatch(r"vendas_historico(?:_[^/\\]+)?\.db", rel)
            or rel == "estoque_historico.db"
        ):
            transformed = _sqlite(data, rel, ids, labels)
        elif rel.endswith((".db", ".sqlite", ".sqlite3")):
            # Unknown table contracts can only prove that no typed reference
            # needs changing; they never receive an UPDATE.
            transformed = _sqlite(data, "", ids, labels)
        elif rel.endswith(".json"):
            if _has_unknown_reference(_json(data), ids, labels):
                _fail("unknown_json_schema")
        elif scope == "cadastro" and rel.startswith(("cadastro_fotos/", "cadastro_produtos_fotos/")):
            # Legacy images have no durable store identity in their paths.
            if rel.startswith("cadastro_fotos/lojas/"):
                _fail("noncanonical_photo_path")
        else:
            _fail("unsupported_source")
        path_key = target.casefold()
        if path_key in targets and targets[path_key] != rel:
            _fail("photo_path_collision")
        targets[path_key] = rel
        result.append((target, transformed))
    return result
