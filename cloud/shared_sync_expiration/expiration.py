"""Expire completed shared-sync bundles without loading their contents."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from google.api_core.exceptions import GoogleAPICallError
from google.cloud.firestore_v1 import DocumentSnapshot, FieldFilter


_METADATA = "jk_sistema_shared_sync"
_CHUNKS = "jk_sistema_shared_sync_chunks"
_SCOPES = (
    "cadastro",
    "vendas",
    "favoritos_historico",
    "favoritos_planilhas",
    "anuncios_ml",
    "sku_campos_pesquisa",
)
_AGE = timedelta(hours=2, minutes=45)
_BATCH_SIZE = 8
_POINTER_ID = re.compile(r"[a-f0-9]{64}")
_SNAPSHOT_ID = re.compile(r"([a-f0-9]{64})__[a-f0-9]{32}")
_METADATA_FIELDS = (
    "id", "scope", "client_id", "schema", "encrypted", "status",
    "snapshot_id", "pointer_id", "snapshot_hash", "chunk_count",
)
_CHUNK_FIELDS = ("bundle_id", "pointer_id", "client_id", "scope", "index")
_RECEIPT_FIELDS = ("protocol", "snapshot_hash", "snapshot_id")


class _ConcurrentChange(Exception):
    """A document no longer belongs to the version being expired."""


@dataclass(frozen=True)
class _Bundle:
    snapshot: DocumentSnapshot
    pointer_id: str
    client_id: str
    scope: str
    chunk_count: int
    snapshot_hash: str
    legacy: bool

    @property
    def id(self):
        return self.snapshot.id


def _candidate(snapshot, cutoff):
    if snapshot.update_time is None or snapshot.update_time > cutoff:
        return None

    fields = snapshot.to_dict() or {}
    scope = fields.get("scope")
    client_id = fields.get("client_id")
    chunk_count = fields.get("chunk_count")
    if (
        scope not in _SCOPES
        or not isinstance(client_id, str)
        or not client_id.strip()
        or fields.get("id") != snapshot.id
        or type(chunk_count) is not int
        or not 1 <= chunk_count <= 100_000
    ):
        return None

    schema = fields.get("schema")
    encrypted = fields.get("encrypted")
    match = _SNAPSHOT_ID.fullmatch(snapshot.id)
    if (
        match
        and type(schema) is int and schema == 2
        and encrypted is True
        and fields.get("status") == "complete"
        and fields.get("snapshot_id") == snapshot.id
        and fields.get("pointer_id") == match[1]
    ):
        pointer_id, legacy = match[1], False
    elif (
        _POINTER_ID.fullmatch(snapshot.id)
        and (schema is None or (type(schema) is int and schema in (0, 1)))
        and (encrypted is None or encrypted is False)
        and fields.get("status") in (None, "", "complete")
        and fields.get("snapshot_id") in (None, "")
        and fields.get("pointer_id") in (None, "", snapshot.id)
    ):
        pointer_id, legacy = snapshot.id, True
    else:
        return None

    return _Bundle(
        snapshot, pointer_id, client_id, scope, chunk_count,
        fields.get("snapshot_hash") or "", legacy,
    )


def _delete(db, snapshots, totals, counter):
    for start in range(0, len(snapshots), _BATCH_SIZE):
        group = snapshots[start:start + _BATCH_SIZE]
        batch = db.batch()
        for snapshot in group:
            option = db.write_option(last_update_time=snapshot.update_time)
            batch.delete(snapshot.reference, option=option)
        batch.commit()
        totals[counter] += len(group)


def _chunks(db, bundle):
    query = (
        db.collection(_CHUNKS)
        .where(filter=FieldFilter("bundle_id", "==", bundle.id))
        .select(_CHUNK_FIELDS)
    )
    chunks = []
    for chunk in query.stream():
        fields = chunk.to_dict() or {}
        index = fields.get("index")
        pointer = fields.get("pointer_id")
        if (
            fields.get("bundle_id") != bundle.id
            or fields.get("client_id") != bundle.client_id
            or fields.get("scope") != bundle.scope
            or (pointer != bundle.pointer_id and not (bundle.legacy and not pointer))
            or type(index) is not int
            or not 0 <= index < bundle.chunk_count
            or chunk.id != f"{bundle.id}_{index:05d}"
            or chunk.update_time is None
            or chunk.update_time > bundle.snapshot.update_time
        ):
            # A legacy writer stages reused chunk IDs before committing
            # metadata. Leave the entire bundle alone while that upload is visible.
            raise _ConcurrentChange()
        chunks.append(chunk)
    return chunks


def _pointers(collection, bundle):
    pointers = []
    authority_id = bundle.pointer_id + "__v2_authority"
    for pointer_id in (bundle.pointer_id, authority_id):
        pointer = collection.document(pointer_id).get(field_paths=_METADATA_FIELDS)
        if not pointer.exists:
            continue
        fields = pointer.to_dict() or {}
        if fields.get("snapshot_id") != bundle.id:
            continue
        if (
            fields.get("id") != bundle.pointer_id
            or fields.get("pointer_id") != bundle.pointer_id
            or fields.get("client_id") != bundle.client_id
            or fields.get("scope") != bundle.scope
            or fields.get("schema") != 2
            or fields.get("encrypted") is not True
            or pointer.update_time is None
        ):
            # Erasing chunks would break a pointer we cannot safely detach.
            raise _ConcurrentChange()
        pointers.append(pointer)
    return pointers


def _receipts(collection, bundle, cutoff):
    if not bundle.snapshot_hash:
        return []
    query = (
        collection.document(bundle.pointer_id)
        .collection("receipts")
        .where(filter=FieldFilter("snapshot_id", "==", bundle.id))
        .select(_RECEIPT_FIELDS)
    )
    receipts = []
    for receipt in query.stream():
        fields = receipt.to_dict() or {}
        if (
            receipt.update_time is not None
            and receipt.update_time <= cutoff
            and fields.get("protocol") == 1
            and fields.get("snapshot_hash") == bundle.snapshot_hash
        ):
            receipts.append(receipt)
    return receipts


def _expire_bundle(db, collection, bundle, cutoff, totals):
    if bundle.legacy:
        authority = collection.document(bundle.pointer_id + "__v2_authority")
        if authority.get(field_paths=("schema",)).exists:
            return

    chunks = _chunks(db, bundle)
    current = bundle.snapshot.reference.get(field_paths=("schema",))
    if not current.exists or current.update_time != bundle.snapshot.update_time:
        raise _ConcurrentChange()

    if not bundle.legacy:
        # Readers resolve chunks through these pointers. Detach them first, in
        # one commit, so a republish cannot leave a pointer to deleted chunks.
        _delete(db, _pointers(collection, bundle), totals, "pointers_deleted")
    _delete(db, chunks, totals, "chunks_deleted")
    if not bundle.legacy:
        _delete(db, _receipts(collection, bundle, cutoff), totals, "receipts_deleted")

    # This document is the recovery anchor if an earlier batch fails.
    _delete(db, [bundle.snapshot], totals, "snapshots_deleted")


def expire(db, *, now=None):
    """Expire eligible bundles and report only aggregate completion counts."""
    now = now if now is not None else datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    cutoff = now - _AGE
    totals = dict(
        snapshots_deleted=0, pointers_deleted=0, chunks_deleted=0,
        receipts_deleted=0, failed=0,
    )

    collection = db.collection(_METADATA)
    query = collection.where(filter=FieldFilter("scope", "in", _SCOPES)).select(_METADATA_FIELDS)
    try:
        # Freeze the projected inventory before mutating the same collection.
        snapshots = list(query.stream())
    except GoogleAPICallError:
        totals["failed"] += 1
        return totals

    for snapshot in snapshots:
        bundle = _candidate(snapshot, cutoff)
        if bundle is None:
            continue
        try:
            _expire_bundle(db, collection, bundle, cutoff, totals)
        except (GoogleAPICallError, _ConcurrentChange):
            totals["failed"] += 1
    return totals
