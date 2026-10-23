from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import sqlite3

import pytest

from backend.services import vendas_collection_checkpoint as checkpoint


DAY = "2026-10-21"


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


@pytest.fixture
def clock(monkeypatch):
    current = [stamp("2026-10-23T12:00:00-03:00")]
    monkeypatch.setattr(checkpoint, "_now", lambda: current[0])
    return current


def context(tmp_path, job="job-a", day=DAY, **kwargs):
    return checkpoint.collection_checkpoint("tenant-a", "store-a", job, day, tenant_path=tmp_path, **kwargs)


def entry(document="1", day=DAY, kind="pedido", revision=1):
    original = {"id": document, "data": day, "revision": revision}
    return {"document_id": document, "listing_hash": checkpoint.collection_listing_hash(original),
            "listing": {"id": document, "data" if kind == "pedido" else "data_emissao": day}}


def payload(document="1", day=DAY, value=10):
    return {"sales": [{"data": day, "origem_tipo": "pedido", "origem_id": document,
                       "item_id": "1", "quantidade": 1, "valor": value}], "notes": [], "items": []}


def save(document="1", day=DAY, value=10, revision=1):
    item = entry(document, day, revision=revision)
    checkpoint.save_collected_document("pedido", document, payload=payload(document, day, value),
                                       listing_hash=item["listing_hash"])
    return item


def test_incremental_generation_survives_changed_requested_job(tmp_path, clock):
    with context(tmp_path) as first:
        document = save()
        checkpoint.save_collection_manifest("pedido", [document])
    with context(tmp_path, "different-period") as second:
        assert second.cycle_id == first.cycle_id
        assert second.started_at == first.started_at
        assert checkpoint.load_prepared_source("pedido") == payload()
    for job in ("job-a", "different-period", first.cycle_id):
        summary = checkpoint.collection_day_summary("tenant-a", "store-a", job, DAY, tenant_path=tmp_path)
        assert summary["documentos_salvos"] == 1
        assert summary["fontes_completas"] == ["pedido"]


def test_explicit_refresh_is_fresh_once_and_invalidates_stale_incremental(tmp_path, clock):
    with context(tmp_path) as original:
        save(value=10)
    with context(tmp_path, "forced-job", force=True) as forced:
        assert checkpoint.load_collected_document("pedido", "1", listing_hash=entry()["listing_hash"]) is None
        save(value=20)
    with context(tmp_path, "forced-job", force=True) as resumed:
        assert resumed.cycle_id == forced.cycle_id
        assert checkpoint.load_collected_document("pedido", "1", listing_hash=entry()["listing_hash"]) == payload(value=20)
    with context(tmp_path, "another-forced-job", force=True) as other:
        assert other.cycle_id != forced.cycle_id
        assert checkpoint.load_collected_document("pedido", "1", listing_hash=entry()["listing_hash"]) is None
    with context(tmp_path, "forced-job", force=True):
        assert checkpoint.load_collected_document("pedido", "1", listing_hash=entry()["listing_hash"]) == payload(value=20)
    with context(tmp_path, "normal-after-refresh") as normal:
        assert normal.cycle_id != original.cycle_id
        assert checkpoint.load_collected_document("pedido", "1", listing_hash=entry()["listing_hash"]) is None


def test_open_day_refreshes_discovery_only_for_a_new_job(tmp_path, clock):
    clock[0] = stamp("2026-10-21T12:00:00-03:00")
    with context(tmp_path, refresh_discovery=True) as first:
        document = save()
        checkpoint.save_collection_manifest("pedido", [document])
    clock[0] += 60
    with context(tmp_path, refresh_discovery=True) as resumed:
        assert resumed.started_at == first.started_at
        assert checkpoint.load_prepared_source("pedido") == payload()
    with context(tmp_path, "next-poll", refresh_discovery=True) as refreshed:
        assert refreshed.cycle_id == first.cycle_id
        assert refreshed.started_at > first.started_at
        assert checkpoint.load_collection_manifest("pedido") is None
        assert checkpoint.load_collected_document("pedido", "1", listing_hash=document["listing_hash"]) == payload()


def test_force_retry_discards_incremental_prepared_during_its_pause_today(tmp_path, clock):
    clock[0] = stamp("2026-10-21T12:00:00-03:00")
    with context(tmp_path, "forced-job", force=True, refresh_discovery=True) as forced:
        save("1", value=10)
        checkpoint.save_collection_manifest("pedido", [entry("1"), entry("2")])
    clock[0] += 60
    with context(tmp_path, "normal-job", refresh_discovery=True) as stale_normal:
        save("2", value=20)
        checkpoint.save_collection_manifest("pedido", [entry("1"), entry("2")])
    clock[0] += 60
    with context(tmp_path, "forced-job", force=True, refresh_discovery=True) as resumed:
        assert resumed.cycle_id == forced.cycle_id
        assert checkpoint.load_collection_manifest("pedido") is not None
        assert checkpoint.load_collected_document("pedido", "1", listing_hash=entry("1")["listing_hash"]) == payload("1", value=10)
        save("2", value=30)
        assert [item["valor"] for item in checkpoint.load_prepared_source("pedido")["sales"]] == [10, 30]
    clock[0] += 60
    with context(tmp_path, "normal-job", refresh_discovery=True) as new_normal:
        assert new_normal.cycle_id != stale_normal.cycle_id
        assert checkpoint.load_collection_manifest("pedido") is None
        assert checkpoint.load_collected_document("pedido", "2", listing_hash=entry("2")["listing_hash"]) is None


@pytest.mark.parametrize("force", [False, True])
def test_same_job_reopens_preliminary_discovery_once_at_sao_paulo_midnight(tmp_path, clock, force):
    clock[0] = stamp("2026-10-22T02:59:00+00:00")  # Still October 21 in Sao Paulo.
    with context(tmp_path, force=force) as first:
        document = save()
        checkpoint.save_collection_manifest("pedido", [document])
    clock[0] = stamp("2026-10-22T03:01:00+00:00")
    with context(tmp_path, force=force) as closed:
        assert closed.cycle_id == first.cycle_id
        assert closed.started_at > first.started_at
        assert checkpoint.load_collection_manifest("pedido") is None
        assert checkpoint.load_collected_document("pedido", "1", listing_hash=document["listing_hash"]) == payload()
        checkpoint.save_collection_manifest("pedido", [document])
    clock[0] += 60
    with context(tmp_path, force=force) as retry:
        assert retry.started_at == closed.started_at
        assert checkpoint.load_prepared_source("pedido") == payload()


def test_original_listing_digest_survives_normalized_manifest(tmp_path, clock):
    document = entry()
    assert checkpoint.collection_listing_hash(document["listing"]) != document["listing_hash"]
    with context(tmp_path):
        checkpoint.save_collection_manifest("pedido", [document])
        checkpoint.save_collected_document("pedido", "1", {"id": "1", "data": DAY, "revision": 1}, payload())
    with context(tmp_path, "another-job"):
        assert checkpoint.load_prepared_source("pedido") == payload()
        assert checkpoint.load_collected_document("pedido", "1", listing_hash=entry(revision=2)["listing_hash"]) is None


@pytest.mark.parametrize("mutate", [
    lambda rows: rows[0]["listing"].update(access_token="NEVER-PERSIST"),
    lambda rows: rows[0]["listing"].update(data={"raw": "response"}),
    lambda rows: rows[0].update(listing_hash="invalid"),
    lambda rows: rows[0].update(document_id="wrong"),
    lambda rows: rows.append(deepcopy(rows[0])),
    lambda rows: rows[0].update(response={"data": []}),
])
def test_manifest_rejects_raw_or_inconsistent_source_data(tmp_path, clock, mutate):
    rows = [entry()]
    mutate(rows)
    with context(tmp_path):
        with pytest.raises(ValueError):
            checkpoint.save_collection_manifest("pedido", rows)
        assert checkpoint.load_collection_manifest("pedido") is None
    assert b"NEVER-PERSIST" not in (tmp_path / "vendas_collection_checkpoint.db").read_bytes()


def test_discovery_is_not_source_completion_until_every_document_is_confirmed(tmp_path, clock):
    with context(tmp_path):
        checkpoint.save_collection_manifest("pedido", [entry("1"), entry("2")])
        save("1")
        assert not checkpoint.collected_source_complete("pedido")
        summary = checkpoint.collection_day_summary("tenant-a", "store-a", "job-a", DAY, tenant_path=tmp_path)
        assert summary["documentos_salvos"] == 1
        assert summary["documentos_pendentes"] == 1
        save("2")
        assert checkpoint.collected_source_complete("pedido")
        assert len(checkpoint.load_prepared_source("pedido")["sales"]) == 2
        save("orphan")
        assert len(checkpoint.load_prepared_source("pedido")["sales"]) == 2


def test_confirmed_empty_source_differs_from_missing_manifest(tmp_path, clock):
    with context(tmp_path):
        assert checkpoint.load_prepared_source("nfe_entrada") is None
        checkpoint.save_collection_manifest("nfe_entrada", [])
        assert checkpoint.load_prepared_source("nfe_entrada") == {"sales": [], "notes": [], "items": []}
        assert checkpoint.collected_source_complete("nfe_entrada")


@pytest.mark.parametrize("corrupt", ["manifest", "timestamp", "document"])
def test_corrupted_preparation_cannot_attest_source_completion(tmp_path, clock, corrupt):
    with context(tmp_path):
        document = save()
        checkpoint.save_collection_manifest("pedido", [document])
        with sqlite3.connect(tmp_path / "vendas_collection_checkpoint.db") as conn:
            if corrupt == "manifest":
                conn.execute("UPDATE collection_manifests SET entries_hash='broken'")
            elif corrupt == "timestamp":
                conn.execute("UPDATE collection_manifests SET started_at=started_at-1")
            else:
                conn.execute("UPDATE collected_documents SET payload_hash='broken'")
        assert checkpoint.load_prepared_source("pedido") is None
        assert not checkpoint.collected_source_complete("pedido")


@pytest.mark.parametrize("mutation", [
    lambda data: data["sales"].clear(),
    lambda data: data["sales"].append(deepcopy(data["sales"][0])),
    lambda data: data["sales"][0].update(quantidade=True),
    lambda data: data["sales"][0].update(valor=float("nan")),
    lambda data: data["sales"][0].update(data="2026-10-20"),
])
def test_incomplete_or_ambiguous_document_never_becomes_confirmed(tmp_path, clock, mutation):
    data = payload()
    mutation(data)
    with context(tmp_path):
        with pytest.raises(ValueError):
            checkpoint.save_collected_document("pedido", "1", payload=data, listing_hash=entry()["listing_hash"])
        assert checkpoint.collection_day_summary("tenant-a", "store-a", "job-a", DAY,
                                                 tenant_path=tmp_path)["documentos_salvos"] == 0


def test_optional_invoice_enrichment_survives_replay_without_changing_listing_hash(tmp_path, clock):
    data = payload()
    data["sales"][0].update(nota_fiscal_id="90", numero_nf="")
    with context(tmp_path):
        checkpoint.save_collected_document("pedido", "1", payload=data, listing_hash=entry()["listing_hash"])
        checkpoint.save_collection_manifest("pedido", [entry()])
        checkpoint.save_collected_invoice_number("90", "0042")
        expected = deepcopy(data)
        expected["sales"][0]["numero_nf"] = "0042"
        assert checkpoint.load_prepared_source("pedido") == expected
        checkpoint.save_collected_invoice_number("90", "DIFFERENT")
        assert checkpoint.load_prepared_source("pedido") == expected


def test_guard_prevents_writes_after_worker_loses_authorization(tmp_path, clock):
    permitted = [True]
    def guard():
        if not permitted[0]:
            raise RuntimeError("lease lost")
    with context(tmp_path, on_write=guard):
        save()
        permitted[0] = False
        for operation in (lambda: save("2"), lambda: checkpoint.save_collection_manifest("pedido", [entry()]),
                          lambda: checkpoint.save_collected_invoice_number("90", "42"),
                          checkpoint.validate_collection_context):
            with pytest.raises(RuntimeError, match="lease lost"):
                operation()
    assert checkpoint.collection_day_summary("tenant-a", "store-a", "job-a", DAY,
                                             tenant_path=tmp_path)["documentos_salvos"] == 1
    with pytest.raises(RuntimeError, match="lease lost"):
        checkpoint.purge_store_collection("tenant-a", "store-a", tenant_path=tmp_path, on_write=guard)


def test_guard_runs_before_initial_schema_or_directory_mutation(tmp_path, clock):
    target = tmp_path / "never-created"
    def guard():
        raise RuntimeError("not authorized")
    with pytest.raises(RuntimeError, match="not authorized"):
        with context(target, on_write=guard):
            pytest.fail("must not enter")
    assert not target.exists()


def test_connection_replacement_invalidates_old_worker_without_persisting_identity(tmp_path, clock):
    with context(tmp_path, connection_identity="PRIVATE-CONNECTION-A"):
        save()
        with context(tmp_path, "job-b", connection_identity="PRIVATE-CONNECTION-B"):
            assert checkpoint.load_collected_document("pedido", "1", listing_hash=entry()["listing_hash"]) is None
            save("2")
        with pytest.raises(RuntimeError, match="substituida"):
            checkpoint.validate_collection_context()
        with pytest.raises(RuntimeError, match="substituida"):
            save("3")
    raw = (tmp_path / "vendas_collection_checkpoint.db").read_bytes()
    assert b"PRIVATE-CONNECTION-A" not in raw and b"PRIVATE-CONNECTION-B" not in raw


def test_purge_and_finish_preserve_other_tenants_stores_and_force_generations(tmp_path, clock):
    for tenant, store in (("tenant-a", "store-a"), ("tenant-a", "store-b"), ("tenant-b", "store-a")):
        with checkpoint.collection_checkpoint(tenant, store, "job", DAY, tenant_path=tmp_path):
            save()
    with context(tmp_path, "force-a", force=True) as force_a:
        save("2")
    with context(tmp_path, "force-b", force=True):
        save("3")
    checkpoint.finish_collection_day("tenant-a", "store-a", force_a.cycle_id, DAY, tenant_path=tmp_path)
    assert checkpoint.collection_day_summary("tenant-a", "store-a", "force-b", DAY,
                                             tenant_path=tmp_path)["documentos_salvos"] == 1
    checkpoint.purge_store_collection("tenant-a", "store-a", tenant_path=tmp_path)
    assert checkpoint.collection_day_summary("tenant-a", "store-a", "force-b", DAY,
                                             tenant_path=tmp_path)["documentos_salvos"] == 0
    for tenant, store in (("tenant-a", "store-b"), ("tenant-b", "store-a")):
        assert checkpoint.collection_day_summary(tenant, store, "job", DAY,
                                                 tenant_path=tmp_path)["documentos_salvos"] == 1


def test_legacy_documents_are_migrated_for_exact_job_but_never_prove_enumeration(tmp_path, clock):
    with context(tmp_path, "bootstrap"):
        pass
    serialized = checkpoint._json(payload())
    with sqlite3.connect(tmp_path / "vendas_collection_checkpoint.db") as conn:
        conn.execute("DELETE FROM collection_generations")
        conn.execute("DELETE FROM collection_job_bindings")
        conn.execute("INSERT INTO collected_documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     ("tenant-a", "store-a", "legacy-job", DAY, "pedido", "1", entry()["listing_hash"],
                      serialized, checkpoint._hash(serialized), 1, "2026-10-21T15:00:00+00:00"))
    with context(tmp_path, "legacy-job"):
        assert checkpoint.load_collected_document("pedido", "1", listing_hash=entry()["listing_hash"]) == payload()
        assert checkpoint.load_collection_manifest("pedido") is None
        assert not checkpoint.collected_source_complete("pedido")


def test_purge_client_without_queue_preserves_other_client_in_all_tables(tmp_path, clock):
    for client in ("tenant-a", "tenant-b"):
        for store in ("store-a", "store-b"):
            with checkpoint.collection_checkpoint(client, store, "job", DAY, tenant_path=tmp_path):
                document = save()
                checkpoint.save_collection_manifest("pedido", [document])
    assert not (tmp_path / "vendas_sync_queue.db").exists()
    checkpoint.purge_client_collection("tenant-a", tenant_path=tmp_path)
    checkpoint.purge_client_collection("tenant-a", tenant_path=tmp_path)
    with sqlite3.connect(tmp_path / "vendas_collection_checkpoint.db") as conn:
        for table in ("collected_documents", "collection_manifests", "collection_generations",
                      "collection_job_bindings", "collection_store_connections"):
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE client_id='tenant-a'").fetchone()[0] == 0
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE client_id='tenant-b'").fetchone()[0] == 2
    with checkpoint.collection_checkpoint("tenant-b", "store-a", "job", DAY, tenant_path=tmp_path):
        assert checkpoint.load_prepared_source("pedido") == payload()


def test_purge_client_missing_database_does_not_create_files(tmp_path):
    target = tmp_path / "not-created"
    checkpoint.purge_client_collection("tenant-a", tenant_path=target)
    assert not target.exists()


def test_purge_client_rolls_back_if_guard_revoked_before_commit(tmp_path, clock):
    with context(tmp_path):
        save()
    checks = []
    def guard():
        checks.append(True)
        if len(checks) == 2:
            raise RuntimeError("lease lost before commit")
    with pytest.raises(RuntimeError, match="lease lost before commit"):
        checkpoint.purge_client_collection("tenant-a", tenant_path=tmp_path, on_write=guard)
    assert checkpoint.collection_day_summary("tenant-a", "store-a", "job-a", DAY,
                                             tenant_path=tmp_path)["documentos_salvos"] == 1


def test_helpers_without_context_preserve_legacy_no_op_behavior():
    assert checkpoint.current_collection_checkpoint() is None
    checkpoint.validate_collection_context()
    checkpoint.save_collected_document("pedido", "1", {}, payload())
    checkpoint.save_collection_manifest("pedido", [])
    checkpoint.save_collected_invoice_number("90", "42")
    assert checkpoint.load_collection_manifest("pedido") is None
    assert checkpoint.load_collected_document("pedido", "1", {}) is None
