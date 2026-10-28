from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from backend.modules.vendas import return_reason_queue as queue


def note(note_id="101", returned=1):
    return ("header-" + note_id, note_id, "10", "2026-10-20", "Devolucao", "4",
            returned, 100, "supplier", "N:10", "Loja", "", "")


def item(item_id="item-a", note_id="101", returned=1, sku="200"):
    return (item_id, note_id, "10", "N:10", "2026-10-20", sku, "Produto",
            1, 100, 100, "Devolucao", "4", returned, "supplier", "Loja", "", "")


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "vendas_historico.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE notas_entrada_itens (
            id_unico TEXT,store_id TEXT,devolucao INTEGER,sku TEXT,
            PRIMARY KEY(id_unico,store_id))""")
        conn.execute("CREATE TABLE commercial (id TEXT PRIMARY KEY)")
    return path


def persist(conn, rows, store="store-a"):
    conn.executemany("INSERT OR REPLACE INTO notas_entrada_itens VALUES (?,?,?,?)",
                     [(row[0], store, row[12], row[5]) for row in rows])


def add(conn, rows=None, notes=None, store="store-a", now=100, refs=None):
    rows = [item()] if rows is None else rows
    notes = [note()] if notes is None else notes
    persist(conn, rows, store)
    return queue.enqueue(conn, store, "Loja " + store, notes, rows,
                         references_by_note=refs, now=now)


def record(conn, store="store-a", item_id="item-a"):
    cursor = conn.execute("SELECT * FROM devolucao_motivos WHERE store_id=? AND item_id_unico=?",
                          (store, item_id))
    return dict(zip((column[0] for column in cursor.description), cursor.fetchone()))


def identified(**kwargs):
    return {"result_status": "identified", "reason_id": "damaged",
            "reason_description": "Produto danificado", "reason_source": "mercado_livre", **kwargs}


def test_enqueue_shares_commercial_commit_and_rollback(database):
    conn = sqlite3.connect(database)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO commercial VALUES ('transaction')")
    add(conn)
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM commercial").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM notas_entrada_itens").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='devolucao_motivos'").fetchone()[0] == 0
    add(conn)
    assert conn.in_transaction
    conn.commit()
    assert record(conn)["status"] == "pending"
    conn.close()


def test_replay_does_not_reset_lease_or_retry_or_success(database):
    with sqlite3.connect(database) as conn:
        assert add(conn) == 1
        job = queue.claim(conn, "store-a", now=100)
        assert add(conn, now=101) == 0
        assert record(conn)["lease_token"] == job["lease_token"]
        assert queue.finish(conn, job, {"result_status": "unavailable", "retryable": True}, now=102)
        assert add(conn, now=103) == 0
        assert record(conn)["next_attempt_at"] == 132
        job = queue.claim(conn, "store-a", now=132)
        assert queue.finish(conn, job, identified(), now=133)
        assert add(conn, now=134) == 0
        assert record(conn)["reason_id"] == "damaged"
        assert record(conn)["next_attempt_at"] == 133 + queue.REFRESH_SECONDS


def test_two_workers_only_one_claims_and_expired_lease_resumes(database):
    with sqlite3.connect(database) as conn:
        add(conn)

    def take(_):
        with sqlite3.connect(database, timeout=10) as conn:
            return queue.claim(conn, "store-a", now=100, lease_seconds=10)

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [job for job in pool.map(take, range(4)) if job]
    assert len(jobs) == 1
    with sqlite3.connect(database) as conn:
        assert queue.claim(conn, "store-a", now=109) is None
        renewed = queue.claim(conn, "store-a", now=110)
        assert renewed["attempts"] == 2
        assert renewed["lease_token"] != jobs[0]["lease_token"]
        assert not queue.finish(conn, jobs[0], identified(), now=111)
        assert queue.finish(conn, renewed, identified(), now=111)


def test_new_fingerprint_reopens_and_rejects_completion_of_old_evidence(database):
    with sqlite3.connect(database) as conn:
        add(conn, refs={"101": {"order_id": "2001"}})
        old = queue.claim(conn, "store-a", now=100)
        assert add(conn, now=101, refs={"101": {"order_id": "2002"}}) == 1
        assert not queue.finish(conn, old, identified(), now=102)
        current = queue.claim(conn, "store-a", now=102)
        assert current["order_id"] == "2002"
        assert current["fingerprint"] != old["fingerprint"]
        assert queue.finish(conn, current, identified(order_id="2002"), now=103)
        assert record(conn)["order_id"] == "2002"


def test_expired_completion_without_reclaim_is_refused(database):
    with sqlite3.connect(database) as conn:
        add(conn)
        job = queue.claim(conn, "store-a", now=100, lease_seconds=5)
        assert not queue.finish(conn, job, identified(), now=105)
        assert record(conn)["result_status"] is None


@pytest.mark.parametrize("rows,notes", [
    ([], [note()]),
    ([item(returned=0)], [note(returned=0)]),
    ([item()], [note(returned=0)]),
])
def test_observed_removed_or_nonreturn_items_obsolete_old_job(database, rows, notes):
    with sqlite3.connect(database) as conn:
        add(conn)
        job = queue.claim(conn, "store-a", now=100)
        add(conn, rows=rows, notes=notes, now=101)
        assert record(conn)["status"] == "obsolete"
        assert not queue.finish(conn, job, identified(), now=102)
        assert queue.claim(conn, "store-a", now=103) is None


def test_obsolete_item_reappears_with_same_fingerprint(database):
    with sqlite3.connect(database) as conn:
        add(conn)
        add(conn, rows=[], now=101)
        assert add(conn, now=102) == 1
        assert queue.claim(conn, "store-a", now=102)


def test_store_isolation_and_unobserved_notes_are_preserved(database):
    with sqlite3.connect(database) as conn:
        add(conn)
        add(conn, rows=[item("item-b", "102")], notes=[note("102")])
        add(conn, store="store-b")
        add(conn, rows=[], now=101)
        assert record(conn)["status"] == "obsolete"
        assert record(conn, item_id="item-b")["status"] == "pending"
        assert record(conn, store="store-b")["status"] == "pending"
        job = queue.claim(conn, "store-b", now=102)
        assert job["store_id"] == "store-b"
        assert queue.claim(conn, "unknown-store", now=102) is None
        with pytest.raises(ValueError, match="store_id"):
            queue.claim(conn, "", now=102)


def test_claim_revalidates_source_item_in_exact_store(database):
    with sqlite3.connect(database) as conn:
        add(conn)
        conn.execute("UPDATE notas_entrada_itens SET store_id='store-b'")
        assert queue.claim(conn, "store-a", now=100) is None
        assert record(conn)["status"] == "obsolete"


def test_finish_revalidates_deleted_or_nonreturn_source(database):
    with sqlite3.connect(database) as conn:
        add(conn)
        job = queue.claim(conn, "store-a", now=100)
        conn.execute("UPDATE notas_entrada_itens SET devolucao=0")
        assert not queue.finish(conn, job, identified(), now=101)
        assert record(conn)["status"] == "obsolete"


def test_reference_whitelist_and_result_never_store_raw_sensitive_payload(database):
    forbidden = "DO-NOT-STORE"
    with sqlite3.connect(database) as conn:
        add(conn, refs={"101": {
            "order_id": "2001", "bling_order_id": "30", "original_invoice_id": "40",
            "claim_id": "50", "return_id": "60", "bling_order_number": "0071",
            "original_invoice_number": "0072", "explicit_bling_reason": "Produto danificado",
            "access_token": forbidden, "observacoes": forbidden, "email": forbidden,
        }})
        job = queue.claim(conn, "store-a", now=100)
        assert job["order_id"] == "2001"
        assert job["bling_order_number"] == "0071"
        assert job["references"]["explicit_bling_reason"] == "Produto danificado"
        assert "access_token" not in job
        assert queue.finish(conn, job, identified(access_token=forbidden, raw_payload=forbidden), now=101)
        assert forbidden not in repr(record(conn))
    assert forbidden.encode() not in database.read_bytes()


@pytest.mark.parametrize("description", [
    "Cliente pessoa@example.com", "CPF 123.456.789-01", "Telefone (11) 99999-9999",
    "Bearer ABCsecret", "access_token=ABCsecret", "https://example.com/private",
])
def test_sensitive_free_text_is_rejected(database, description):
    with sqlite3.connect(database) as conn:
        add(conn, refs={"101": {"explicit_bling_reason": description,
                                "bling_order_number": "V:100", "original_invoice_number": "N:100"}})
        job = queue.claim(conn, "store-a", now=100)
        assert "explicit_bling_reason" not in job
        assert "bling_order_number" not in job
        assert "original_invoice_number" not in job
        queue.finish(conn, job, identified(reason_description=description), now=101)
        assert record(conn)["reason_description"] is None


@pytest.mark.parametrize("status", ["identified", "not_linked", "not_informed", "ambiguous"])
def test_completed_results_refresh_after_24_hours(database, status):
    with sqlite3.connect(database) as conn:
        add(conn)
        job = queue.claim(conn, "store-a", now=100)
        assert queue.finish(conn, job, identified(result_status=status), now=101)
        assert queue.claim(conn, "store-a", now=101 + queue.REFRESH_SECONDS - 1) is None
        refreshed = queue.claim(conn, "store-a", now=101 + queue.REFRESH_SECONDS)
        assert refreshed
        assert refreshed["attempts"] == 1


def test_transient_backoff_retry_after_and_reconnection_are_bounded(database):
    with sqlite3.connect(database) as conn:
        add(conn)
        now = 100
        for delay in (30, 60, 120, 240, 480, 900, 900):
            job = queue.claim(conn, "store-a", now=now)
            assert queue.finish(conn, job, {"result_status": "unavailable", "retryable": True}, now=now)
            assert record(conn)["next_attempt_at"] == now + delay
            now += delay
        job = queue.claim(conn, "store-a", now=now)
        queue.finish(conn, job, {"result_status": "unavailable", "retryable": True,
                                 "retry_after_seconds": 10**9}, now=now)
        assert record(conn)["next_attempt_at"] == now + 900
        job = queue.claim(conn, "store-a", now=now + 900)
        queue.finish(conn, job, {"result_status": "reconnect_required", "error_code": "oauth_invalid",
                                 "access_token": "DO-NOT-STORE"}, now=now + 900)
        assert record(conn)["next_attempt_at"] == now + 1800


def test_failed_refresh_preserves_identified_reason(database):
    with sqlite3.connect(database) as conn:
        add(conn)
        job = queue.claim(conn, "store-a", now=100)
        queue.finish(conn, job, identified(), now=101)
        job = queue.claim(conn, "store-a", now=101 + queue.REFRESH_SECONDS)
        queue.finish(conn, job, {"result_status": "unavailable", "retryable": True,
                                 "error_code": "rate_limit"}, now=102 + queue.REFRESH_SECONDS)
        result = record(conn)
        assert result["status"] == "retry"
        assert result["result_status"] == "identified"
        assert result["reason_id"] == "damaged"
        assert result["completed_at"] == 101


def test_dispatcher_releases_transaction_before_resolver_and_touches_no_commercial_data(database):
    with sqlite3.connect(database) as conn:
        add(conn, now=0)
        before = conn.execute("SELECT * FROM notas_entrada_itens").fetchall()

    def resolve(job):
        with sqlite3.connect(database, timeout=0.01) as other:
            # BEGIN IMMEDIATE would fail if the dispatcher kept the write lock.
            other.execute("BEGIN IMMEDIATE")
            assert record(other)["status"] == "processing"
            assert job["store_id"] == "store-a"
        return identified()

    summary = queue.process_due(database, "store-a", resolve)
    assert summary["claimed"] == summary["finished"] == summary["identified"] == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT * FROM notas_entrada_itens").fetchall() == before


def test_dispatcher_exception_is_retryable_without_exception_text(database):
    with sqlite3.connect(database) as conn:
        add(conn, now=0)

    def resolve(_):
        raise RuntimeError("DO-NOT-STORE access_token person@example.com")

    summary = queue.process_due(database, "store-a", resolve)
    assert summary["retry"] == 1
    with sqlite3.connect(database) as conn:
        assert record(conn)["error_code"] == "resolver_exception"
        assert "DO-NOT-STORE" not in json.dumps(record(conn))


def test_dispatcher_cannot_finish_after_source_changes_during_lookup(database):
    with sqlite3.connect(database) as conn:
        add(conn, now=0)

    def resolve(_):
        with sqlite3.connect(database) as conn:
            add(conn, now=queue._time(), refs={"101": {"order_id": "2002"}})
        return identified(order_id="2001")

    summary = queue.process_due(database, "store-a", resolve, limit=1)
    assert summary["stale"] == 1
    with sqlite3.connect(database) as conn:
        assert record(conn)["status"] == "pending"
        assert record(conn)["reason_id"] is None


def test_dispatcher_limit_bounds_work_and_other_tenant_database_is_independent(database, tmp_path):
    with sqlite3.connect(database) as conn:
        add(conn, rows=[item("item-a"), item("item-b")], now=0)
    summary = queue.process_due(database, "store-a", lambda _: identified(), limit=1)
    assert summary["finished"] == 1
    other = tmp_path / "tenant-b.db"
    with sqlite3.connect(other) as conn:
        conn.execute("""CREATE TABLE notas_entrada_itens
            (id_unico TEXT,store_id TEXT,devolucao INTEGER,sku TEXT,PRIMARY KEY(id_unico,store_id))""")
        add(conn, now=0)
    assert queue.process_due(other, "store-a", lambda _: identified())["finished"] == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM devolucao_motivos WHERE status='pending'").fetchone()[0] == 1


def test_rule_revision_migration_keeps_old_success_and_only_claims_selected_history_item(database):
    with sqlite3.connect(database) as conn:
        add(conn, rows=[item("item-a"), item("item-b")])
        job = queue.claim(conn, "store-a", now=100, item_ids=["item-b"])
        assert job["item_id_unico"] == "item-b"
        queue.finish(conn, job, identified(), now=101)
        conn.execute("ALTER TABLE devolucao_motivos DROP COLUMN rule_version")
        queue.ensure_schema(conn)
        old = record(conn, item_id="item-b")
        assert old["rule_version"] == 1 and old["result_status"] == "identified"
        # Resolver version changes do not manufacture new commercial evidence.
        assert add(conn, rows=[item("item-a"), item("item-b")], now=102) == 0
        assert record(conn, item_id="item-b")["reason_id"] == "damaged"
        assert queue.claim(conn, "store-a", now=103, item_ids=["item-b"]) is None
        assert queue.claim(conn, "store-a", now=103, item_ids=[]) is None
        assert queue.claim(conn, "store-a", now=103, item_ids=["item-a"])["rule_version"] == queue.RULE_VERSION
