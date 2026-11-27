from datetime import datetime, timedelta, timezone

from google.api_core.exceptions import FailedPrecondition, ServiceUnavailable
from google.cloud.firestore_v1 import Client
import pytest

from cloud.shared_sync_expiration.expiration import expire


NOW = datetime(2026, 11, 27, 18, tzinfo=timezone.utc)
OLD = NOW - timedelta(hours=3)
CUTOFF = NOW - timedelta(hours=2, minutes=45)
METADATA = "jk_sistema_shared_sync"
CHUNKS = "jk_sistema_shared_sync_chunks"
POINTER = "a" * 64
AUTHORITY = POINTER + "__v2_authority"
FIRST = POINTER + "__" + "b" * 32
SECOND = POINTER + "__" + "c" * 32
SNAPSHOT_HASH = "d" * 64


class Snapshot:
    def __init__(self, reference, row, fields):
        self.reference = reference
        self.id = reference.path[-1]
        self.exists = row is not None
        if row is None:
            self.values = {}
            self.update_time = None
        else:
            values, self.update_time = row
            self.values = {field: values[field] for field in fields if field in values}

    def to_dict(self):
        return self.values.copy()

    def get(self, key):
        return self.values[key]


class Reference:
    def __init__(self, db, path):
        self.db = db
        self.path = path

    def get(self, *, field_paths):
        return Snapshot(self, self.db.rows.get(self.path), field_paths)

    def collection(self, name):
        return Collection(self.db, (*self.path, name))


class Collection:
    def __init__(self, db, path):
        self.db = db
        self.path = path

    def document(self, name):
        return Reference(self.db, (*self.path, name))

    def where(self, *, filter):
        self.filter = filter
        return self

    def select(self, fields):
        assert "data" not in fields
        self.fields = fields
        return self

    def stream(self):
        self.db.fail_query(self.path)
        for path, row in list(self.db.rows.items()):
            if path[:-1] != self.path:
                continue
            field = row[0].get(self.filter.field_path)
            if self.filter.op_string == "in":
                matched = field in self.filter.value
            else:
                assert self.filter.op_string == "=="
                matched = field == self.filter.value
            if matched:
                yield Snapshot(Reference(self.db, path), row, self.fields)


class WriteBatch:
    def __init__(self, db):
        self.db = db
        self.deletes = []

    def delete(self, reference, *, option):
        self.deletes.append((reference.path, option._last_update_time))

    def commit(self):
        assert 0 < len(self.deletes) <= 8
        self.db.before_commit(self.deletes)
        for path, expected in self.deletes:
            current = self.db.rows.get(path)
            if current is None or current[1] != expected:
                raise FailedPrecondition("changed")
        self.db.commits.append([path for path, _ in self.deletes])
        for path, _ in self.deletes:
            del self.db.rows[path]


class Firestore:
    def __init__(self):
        self.rows = {}
        self.commits = []
        self.before_commit = lambda _deletes: None
        self.fail_query = lambda _path: None

    def collection(self, name):
        return Collection(self, (name,))

    def batch(self):
        return WriteBatch(self)

    write_option = staticmethod(Client.write_option)

    def add(self, path, update_time=OLD, **fields):
        self.rows[path] = (fields, update_time)


@pytest.fixture
def db():
    return Firestore()


def add_bundle(
    db, *, version=2, pointer=POINTER, snapshot_id=FIRST, scope="cadastro",
    status="complete", metadata_time=OLD, chunks=1, client_id="tenant-a",
):
    is_v2 = version == 2
    identity = snapshot_id if is_v2 else pointer
    fields = dict(id=identity, client_id=client_id, scope=scope,
                  chunk_count=chunks, snapshot_hash=SNAPSHOT_HASH)
    if is_v2:
        fields.update(schema=2, encrypted=True, status=status,
                      snapshot_id=snapshot_id, pointer_id=pointer)
    elif version == 1:
        fields["schema"] = 1
    db.add((METADATA, identity), metadata_time, **fields)
    for index in range(chunks):
        fields = dict(bundle_id=identity, index=index, scope=scope, client_id=client_id, data="unused")
        if is_v2:
            fields["pointer_id"] = pointer
        db.add((CHUNKS, f"{identity}_{index:05}"), metadata_time - timedelta(seconds=1), **fields)
    return identity


def add_pointer(db, document, snapshot_id=FIRST, *, scope="cadastro", client_id="tenant-a", update_time=OLD):
    db.add(
        (METADATA, document), update_time, id=POINTER, scope=scope, client_id=client_id,
        pointer_id=POINTER, snapshot_id=snapshot_id, schema=2, encrypted=True,
    )


def add_receipt(db, name, snapshot_id=FIRST, *, update_time=OLD, protocol=1, hash_value=SNAPSHOT_HASH):
    path = (METADATA, POINTER, "receipts", name)
    db.add(path, update_time, snapshot_id=snapshot_id, snapshot_hash=hash_value, protocol=protocol)
    return path


def assert_counts(result, *, snapshots=0, pointers=0, chunks=0, receipts=0, failed=0):
    assert result == dict(
        snapshots_deleted=snapshots, pointers_deleted=pointers, chunks_deleted=chunks,
        receipts_deleted=receipts, failed=failed,
    )


def test_expires_at_server_time_cutoff_and_keeps_new_receipts(db):
    add_bundle(db, metadata_time=CUTOFF, chunks=17)
    add_pointer(db, POINTER)
    add_pointer(db, AUTHORITY)
    obsolete_receipt = add_receipt(db, "old", update_time=CUTOFF)
    recent_receipt = add_receipt(db, "recent", update_time=NOW)
    receipt_for_new_snapshot = add_receipt(db, "next", snapshot_id=SECOND)
    bad_receipt = add_receipt(db, "other-hash", hash_value="e" * 64)

    result = expire(db, now=NOW)

    assert_counts(result, snapshots=1, pointers=2, chunks=17, receipts=1)
    assert db.commits[0] == [(METADATA, POINTER), (METADATA, AUTHORITY)]
    assert [len(batch) for batch in db.commits[1:4]] == [8, 8, 1]
    assert db.commits[-2] == [obsolete_receipt]
    assert db.commits[-1] == [(METADATA, FIRST)]
    assert set(db.rows) == {recent_receipt, receipt_for_new_snapshot, bad_receipt}


@pytest.mark.parametrize(
    "overrides",
    [
        {"scope": "lojas_integracoes"}, {"scope": "future_scope"},
        {"status": "pending"}, {"metadata_time": CUTOFF + timedelta(seconds=1)},
    ],
)
def test_preserves_excluded_unconfirmed_and_recent_bundles(db, overrides):
    identity = add_bundle(db, **overrides)
    fields, _ = db.rows[(METADATA, identity)]
    fields["updated_ts"] = 0
    before = db.rows.copy()

    assert_counts(expire(db, now=NOW))
    assert db.rows == before
    assert db.commits == []


@pytest.mark.parametrize(
    "scope",
    ["cadastro", "vendas", "favoritos_historico", "favoritos_planilhas", "anuncios_ml", "sku_campos_pesquisa"],
)
def test_six_supported_scopes(db, scope):
    add_bundle(db, scope=scope)
    assert_counts(expire(db, now=NOW), snapshots=1, chunks=1)


def test_preserves_pointer_that_already_advanced(db):
    add_bundle(db)
    add_pointer(db, POINTER, SECOND, update_time=NOW)

    assert_counts(expire(db, now=NOW), snapshots=1, chunks=1)
    assert set(db.rows) == {(METADATA, POINTER)}


def test_never_removes_package_using_pointer_from_another_tenant(db):
    add_bundle(db)
    add_pointer(db, POINTER, client_id="tenant-b")
    add_pointer(db, AUTHORITY)
    before = db.rows.copy()

    assert_counts(expire(db, now=NOW), failed=1)
    assert db.rows == before


def test_pointer_republish_between_read_and_commit_preserves_new_publication(db):
    add_bundle(db)
    add_pointer(db, POINTER)
    add_pointer(db, AUTHORITY)

    def republish(deletes):
        if deletes[0][0] == (METADATA, POINTER):
            add_pointer(db, POINTER, SECOND, update_time=NOW)
            add_pointer(db, AUTHORITY, SECOND, update_time=NOW)

    db.before_commit = republish
    assert_counts(expire(db, now=NOW), failed=1)
    assert (METADATA, FIRST) in db.rows
    assert (CHUNKS, FIRST + "_00000") in db.rows
    assert db.commits == []

    db.before_commit = lambda _deletes: None
    assert_counts(expire(db, now=NOW), snapshots=1, chunks=1)
    assert set(db.rows) == {(METADATA, POINTER), (METADATA, AUTHORITY)}


def test_failed_chunk_batch_retries_without_losing_package_metadata(db):
    add_bundle(db, chunks=10)
    add_pointer(db, POINTER)

    def fail_second_chunk_batch(deletes):
        if deletes[0][0] == (CHUNKS, FIRST + "_00008"):
            raise ServiceUnavailable("test transient outage")

    db.before_commit = fail_second_chunk_batch
    assert_counts(expire(db, now=NOW), pointers=1, chunks=8, failed=1)
    assert (METADATA, FIRST) in db.rows
    assert sum(path[0] == CHUNKS for path in db.rows) == 2

    db.before_commit = lambda _deletes: None
    assert_counts(expire(db, now=NOW), snapshots=1, chunks=2)
    assert db.rows == {}


def test_never_deletes_a_reused_chunk_id_or_incompatible_scope(db):
    add_bundle(db, chunks=2)
    path = (CHUNKS, FIRST + "_00001")
    original = db.rows[path][0]

    def reuse(deletes):
        if deletes[0][0][0] == CHUNKS:
            db.add(path, NOW, **original)

    db.before_commit = reuse
    assert_counts(expire(db, now=NOW), failed=1)
    assert len(db.rows) == 3

    db.add(path, OLD, **{**original, "scope": "lojas_integracoes"})
    db.before_commit = lambda _deletes: None
    assert_counts(expire(db, now=NOW), failed=1)
    assert len(db.rows) == 3


def test_preserves_receipt_refreshed_between_read_and_commit(db):
    add_bundle(db)
    path = add_receipt(db, "receiver")

    def refresh_receipt(deletes):
        if deletes[0][0] == path:
            add_receipt(db, "receiver", SECOND, update_time=NOW)

    db.before_commit = refresh_receipt
    assert_counts(expire(db, now=NOW), chunks=1, failed=1)
    assert (METADATA, FIRST) in db.rows

    assert_counts(expire(db, now=NOW), snapshots=1)
    assert set(db.rows) == {path}


def test_legacy_uses_precondition_and_preserves_inflight_upload(db):
    identity = add_bundle(db, version=1, chunks=2, metadata_time=OLD)
    path = (CHUNKS, identity + "_00001")
    fields, committed_on = db.rows[path]
    db.add(path, NOW, **fields)

    assert_counts(expire(db, now=NOW), failed=1)
    assert len(db.rows) == 3

    db.add(path, committed_on, **fields)
    assert_counts(expire(db, now=NOW), snapshots=1, chunks=2)
    assert db.commits[-1] == [(METADATA, POINTER)]


def test_preserves_legacy_pointer_if_v2_authority_exists(db):
    add_bundle(db, version=1)
    add_pointer(db, AUTHORITY, SECOND)
    before = db.rows.copy()

    assert_counts(expire(db, now=NOW))
    assert db.rows == before


def test_inventory_failure_is_reported_for_retry(db):
    add_bundle(db)

    def fail(_path):
        raise ServiceUnavailable("test transient outage")

    db.fail_query = fail
    assert_counts(expire(db, now=NOW), failed=1)
    assert db.commits == []
