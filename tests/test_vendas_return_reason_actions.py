from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from backend.modules.vendas import return_reason_actions as actions
from backend.modules.vendas import return_reason_queue as queue
from backend.modules.vendas.errors import VendasDomainError


def returned(item="item-a", note="101", store="store-a", sku="200", name="Loja A", **changes):
    values = dict(item=item, note=note, store=store, sku=sku, returned=1, name=name,
                  value=123.45, quantity=2, number="00991")
    values.update(changes)
    return tuple(values[key] for key in ("item", "note", "store", "sku", "returned", "name", "value", "quantity", "number"))


def identified(_job=None):
    return {"result_status": "identified", "reason_id": "PDD1", "reason_description": "Produto diferente",
            "reason_source": "mercado_livre"}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    state = SimpleNamespace(clock=1000.0, remembered=[], calls=[], central=None,
                            stores={"tenant-a": [{"store_id": "store-a", "nome": "Loja A"},
                                                   {"store_id": "store-b", "nome": "Loja B"}],
                                    "tenant-b": [{"store_id": "store-a", "nome": "Loja A"}]})
    for tenant in state.stores:
        (tmp_path / tenant).mkdir()
    monkeypatch.setattr(actions, "get_tenant_path", lambda tenant: str(tmp_path / tenant))
    monkeypatch.setattr(actions, "read_stores", lambda tenant: state.stores[tenant])
    monkeypatch.setattr(actions, "current", lambda tenant: state.central)
    monkeypatch.setattr(actions, "_listar_bancos_vendas_tenant", lambda tenant: sorted((tmp_path / tenant).glob("vendas_*.db")))
    monkeypatch.setattr(actions, "_remember", lambda tenant, scope: state.remembered.append((tenant, scope)))
    monkeypatch.setattr(actions.time, "time", lambda: state.clock)
    monkeypatch.setattr(actions, "load_composition_catalog", lambda tenant: SimpleNamespace(
        _row_store=lambda row, _fallback: {"Loja A": "store-a", "Loja B": "store-b"}.get(row.get("loja_conta"))))

    def database(rows, filename="vendas_historico.db", tenant="tenant-a"):
        path = tmp_path / tenant / filename
        with sqlite3.connect(path) as conn:
            conn.execute("""CREATE TABLE notas_entrada_itens (id_unico TEXT,id_nota TEXT,store_id TEXT,
                sku TEXT,devolucao INTEGER,loja_conta TEXT,valor_total REAL,quantidade REAL,numero_nota TEXT)""")
            conn.executemany("INSERT INTO notas_entrada_itens VALUES (?,?,?,?,?,?,?,?,?)", rows)
        return path

    def resolve(job):
        state.calls.append((job["store_id"], job["item_id_unico"], job["nota_id"], job.get("references")))
        return identified()

    def step(resolver=resolve, tenant="tenant-a"):
        actions.advance_search(tenant, actions._scope(actions._stores(tenant)), resolver)
        return actions.search_status(tenant)

    def run(resolver=resolve, tenant="tenant-a", turns=100):
        for _ in range(turns):
            status = step(resolver, tenant)
            if not status["running"]:
                return status
        pytest.fail("Search did not finish within the synthetic turn limit")

    state.database, state.step, state.run = database, step, run
    state.path = lambda tenant="tenant-a": tmp_path / tenant / actions.FILENAME
    return state


def saved_reason(path, status="not_linked", *, item="item-a", store="store-a", note="101", sku="200", now=100):
    with sqlite3.connect(path) as conn:
        assert queue.prepare_saved_item(conn, store, "Loja A", item, note, sku, now=now)
        job = queue.claim(conn, store, now=now, item_ids=[item])
        assert queue.finish(conn, job, {**identified(), "result_status": status}, now=now + 1)


def commercial_rows(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT * FROM notas_entrada_itens ORDER BY rowid").fetchall()


def test_idle_and_post_never_scan_or_resolve_and_repeated_click_preserves_generation(environment, monkeypatch):
    state = environment
    path = state.database([returned()])
    assert actions.search_status("tenant-a")["phase"] == "idle"
    assert not state.path().exists()
    monkeypatch.setattr(actions, "_scan", lambda *args: pytest.fail("POST scanned source rows"))
    result = actions.start_search("tenant-a")
    assert result["phase"] == "preparing" and result["running"] and result["total"] == 0
    with sqlite3.connect(state.path()) as conn:
        before = conn.execute("SELECT generation,requested_at FROM operations").fetchone()
        conn.execute("UPDATE operations SET lease_token='live-worker',lease_until=1100")
    state.clock += 1
    actions.start_search("tenant-a")
    with sqlite3.connect(state.path()) as conn:
        assert conn.execute("SELECT generation,requested_at FROM operations").fetchone() == before
        assert conn.execute("SELECT lease_token,lease_until FROM operations").fetchone() == ("live-worker", 1100)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='devolucao_motivos'").fetchone()[0] == 0


def test_bounded_scan_resume_and_all_history_preserve_commercial_data(environment, monkeypatch):
    state = environment
    monkeypatch.setattr(actions, "SCAN_LIMIT", 3)
    monkeypatch.setattr(actions, "RESOLVE_LIMIT", 2)
    rows = [returned(item="item-" + str(i), note=str(100 + i), quantity=4) for i in range(7)]
    rows += [returned("other-store", "201", "store-b", name="Loja B")]
    path = state.database(rows)
    before = commercial_rows(path)
    actions.start_search("tenant-a")
    state.step()  # Bounded legacy-source pass.
    result = state.step()
    assert result["phase"] == "preparing" and result["total"] == 3 and not state.calls
    with sqlite3.connect(state.path()) as conn:
        cursor = conn.execute("SELECT cursor FROM sources WHERE store_id='store-a'").fetchone()[0]
    actions.start_search("tenant-a")  # Reopened HTTP request resumes the durable cursor.
    with sqlite3.connect(state.path()) as conn:
        assert conn.execute("SELECT cursor FROM sources WHERE store_id='store-a'").fetchone()[0] == cursor
    result = state.run()
    assert result["phase"] == "completed" and result["total"] == result["processed"] == result["identified"] == 8
    assert len(state.calls) == 8 and {row[0] for row in state.calls} == {"store-a", "store-b"}
    assert all(not row[3] for row in state.calls)  # NF number never becomes an order reference.
    assert commercial_rows(path) == before


def test_tenant_and_authorized_store_scope_do_not_follow_body_alias_or_foreign_paths(environment, monkeypatch):
    state = environment
    state.stores["tenant-a"] = [{"store_id": "store-a", "nome": "Loja A"}, {"nome": "Sem identidade"}]
    path = state.database([returned(), returned("denied", store="store-b", name="Loja B"),
                           returned("missing", store=None, name="Loja B")])
    foreign = state.database([returned("foreign")], tenant="tenant-b")
    monkeypatch.setattr(actions, "_listar_bancos_vendas_tenant", lambda tenant: [path, foreign, path])
    actions.start_search("tenant-a")
    result = state.run()
    assert result["total"] == result["identified"] == 1 and result["skipped"] == 0
    assert state.calls == [("store-a", "item-a", "101", {})]
    assert actions.search_status("tenant-b")["phase"] == "idle"
    assert not state.path("tenant-b").exists()
    state.stores["tenant-a"] = [{"store_id": "store-b", "nome": "Loja B"}]
    assert actions.search_status("tenant-a")["phase"] == "idle"


def test_mirrors_deduplicate_and_existing_identified_reason_needs_no_provider(environment):
    state = environment
    state.database([returned()], "vendas_a.db")
    mirror = state.database([returned()], "vendas_z.db")
    saved_reason(mirror, "identified")
    actions.start_search("tenant-a")
    result = state.run(lambda job: pytest.fail("Identified mirror was queried again"))
    assert result["identified"] == result["total"] == result["processed"] == 1


def test_conflicting_mirror_identity_is_ambiguous_and_not_queried(environment):
    state = environment
    state.database([returned()], "vendas_a.db")
    state.database([returned(note="102")], "vendas_b.db")
    actions.start_search("tenant-a")
    result = state.run(lambda job: pytest.fail("Conflicting source identity was queried"))
    assert result["total"] == result["ambiguous"] == result["processed"] == 1


def test_legacy_authorized_alias_is_resolved_without_commercial_backfill_and_mirrors_deduplicate(environment):
    state = environment
    legacy = state.database([returned("legacy", store=None), returned("item-a", store=""),
                             returned("unknown", store=None, name="Ambiguous old name")], "vendas_a.db")
    state.database([returned()], "vendas_b.db")
    before = commercial_rows(legacy)
    actions.start_search("tenant-a")
    result = state.run()
    assert (result["total"], result["processed"], result["identified"], result["skipped"]) == (2, 2, 2, 0)
    assert commercial_rows(legacy) == before
    with sqlite3.connect(legacy) as conn:
        assert conn.execute("SELECT item_id_unico,source_kind FROM devolucao_motivos").fetchall() == [("legacy", "legacy")]


@pytest.mark.parametrize("status", ["not_linked", "unavailable", "not_informed", "ambiguous"])
def test_explicit_click_reopens_old_unidentified_results_without_waiting_24_hours(environment, status):
    state = environment
    path = state.database([returned()])
    saved_reason(path, status)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT next_attempt_at FROM devolucao_motivos").fetchone()[0] > state.clock
        conn.execute("UPDATE devolucao_motivos SET rule_version=1")
    actions.start_search("tenant-a")
    result = state.run()
    assert result["identified"] == 1 and len(state.calls) == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT rule_version FROM devolucao_motivos").fetchone()[0] == queue.RULE_VERSION


def test_rate_limit_stays_pending_obeys_backoff_and_counts_later_identification(environment):
    state = environment
    path = state.database([returned()])
    calls = []
    def resolve(job):
        calls.append(job)
        if len(calls) == 1:
            return {"result_status": "unavailable", "retryable": True, "retry_after_seconds": 60,
                    "error_code": "rate_limited"}
        return identified()
    actions.start_search("tenant-a")
    for _ in range(4):
        result = state.step(resolve)
    assert (result["phase"], result["pending"], result["retry"], result["processed"]) == ("processing", 1, 1, 0)
    with sqlite3.connect(path) as conn:
        retry_at = conn.execute("SELECT next_attempt_at FROM devolucao_motivos").fetchone()[0]
    assert retry_at == 1060
    state.clock = 1059
    actions.start_search("tenant-a")
    for _ in range(3):
        state.step(resolve)
    assert len(calls) == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT next_attempt_at FROM devolucao_motivos").fetchone()[0] == retry_at
    state.clock = retry_at
    result = state.run(resolve)
    assert len(calls) == 2 and result["phase"] == "completed"
    assert result["processed"] == result["identified"] == 1 and result["pending"] == result["retry"] == 0


def test_reconnect_is_a_separate_blocked_state_and_explicit_retry_can_resume(environment):
    state = environment
    state.database([returned()])
    actions.start_search("tenant-a")
    result = state.run(lambda _: {"result_status": "reconnect_required", "error_code": "oauth_invalid"})
    assert result["phase"] == "blocked" and not result["running"]
    assert result["reconnect_required"] == result["processed"] == 1 and result["identified"] == 0
    state.clock += 1
    actions.start_search("tenant-a")
    assert state.run()["identified"] == 1


def test_live_item_lease_is_not_reset_and_expiration_resumes(environment):
    state = environment
    path = state.database([returned()])
    with sqlite3.connect(path) as conn:
        queue.prepare_saved_item(conn, "store-a", "Loja A", "item-a", "101", "200")
        job = queue.claim(conn, "store-a", lease_seconds=100)
    actions.start_search("tenant-a")
    for _ in range(5):
        result = state.step()
    assert result["pending"] == 1 and not state.calls
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT lease_token,lease_until FROM devolucao_motivos").fetchone() == (job["lease_token"], 1100)
    state.clock = 1100
    assert state.run()["identified"] == 1


def test_stale_operation_owner_cannot_update_progress_or_clear_successors_lease(environment):
    state = environment
    state.database([returned(), returned("item-b", note="102")])
    actions.start_search("tenant-a")
    calls = []
    def replace_owner(job):
        calls.append(job)
        with sqlite3.connect(state.path(), timeout=.1) as conn:
            conn.execute("BEGIN IMMEDIATE")  # Resolver runs outside progress/commercial locks.
            conn.execute("UPDATE operations SET generation='successor',lease_token='new-owner',lease_until=1200")
        return identified()
    for _ in range(4):
        state.step(replace_owner)
    assert len(calls) == 1
    with sqlite3.connect(state.path()) as conn:
        assert conn.execute("SELECT lease_token,phase FROM operations").fetchone() == ("new-owner", "processing")
        assert conn.execute("SELECT SUM(processed) FROM members").fetchone()[0] == 0
    state.clock = 1200
    result = state.run()
    assert result["identified"] == 2 and len(state.calls) == 1  # Accepted queue evidence is reused.


def test_no_authorized_stable_store_and_expired_or_changed_context_do_not_process(environment, monkeypatch):
    state = environment
    state.stores["tenant-a"] = [{"nome": "Loja sem identidade"}]
    with pytest.raises(VendasDomainError, match="Nenhuma loja"):
        actions.start_search("tenant-a")
    assert not state.path().exists()
    state.stores["tenant-a"] = [{"store_id": "store-a", "nome": "Loja A"}]
    state.database([returned()])
    state.central = object()
    actions.start_search("tenant-a")
    state.central = None
    for _ in range(3):
        assert state.step()["total"] == 0
    state.central = object()
    monkeypatch.setattr(actions, "read_stores", lambda _: (_ for _ in ()).throw(VendasDomainError(401, "Sessão expirada")))
    with pytest.raises(VendasDomainError):
        actions.advance_search("tenant-a", actions._scope(["store-a"]), identified)
    assert not state.calls


def test_error_status_contains_no_private_exception_and_cursor_can_resume(environment, monkeypatch):
    state = environment
    state.database([returned()])
    actions.start_search("tenant-a")
    original = actions._scan
    def fail(*_args):
        raise RuntimeError("DO-NOT-STORE token personal@example.com")
    monkeypatch.setattr(actions, "_scan", fail)
    with pytest.raises(RuntimeError):
        state.step()
    status = actions.search_status("tenant-a")
    assert status["error"] == "search_retry_pending" and status["running"]
    assert b"DO-NOT-STORE" not in state.path().read_bytes()
    monkeypatch.setattr(actions, "_scan", original)
    assert state.run()["error"] is None


def test_http_routes_take_tenant_only_from_dependency_and_never_run_provider_in_post(environment, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from backend.modules.vendas.router import create_vendas_router
    state = environment
    state.database([returned(), returned("store-b-item", store="store-b", name="Loja B")])
    authorized = [True]
    async def tenant_dependency():
        if not authorized[0]:
            raise HTTPException(401, "Authentication required")
        return "tenant-a"
    app = FastAPI()
    app.include_router(create_vendas_router(None, None, SimpleNamespace(get_tenant_id=tenant_dependency)))
    client = TestClient(app)
    monkeypatch.setattr(queue, "process_due", lambda *args, **kwargs: pytest.fail("HTTP invoked provider processing"))
    monkeypatch.setattr(actions, "_scan", lambda *args, **kwargs: pytest.fail("HTTP scanned source rows"))
    assert client.get("/api/notas-entrada/motivos/status").json()["phase"] == "idle"
    response = client.post("/api/notas-entrada/motivos/buscar?client_id=tenant-b&loja=store-b", json={
        "client_id": "tenant-b", "store_id": "store-b", "data_inicio": "2999-01-01"})
    assert response.status_code == 200 and response.json()["phase"] == "preparing"
    with sqlite3.connect(state.path()) as conn:
        assert {row[0] for row in conn.execute("SELECT DISTINCT store_id FROM sources")} == {"", "store-a", "store-b"}
    assert not state.path("tenant-b").exists()
    authorized[0] = False
    assert client.get("/api/notas-entrada/motivos/status").status_code == 401
    assert client.post("/api/notas-entrada/motivos/buscar", json={}).status_code == 401
    client.close()


@pytest.mark.parametrize("opaque_item", ["Loja antiga item 7", "Peças devolução ã ç", " quote' OR 1=1 -- "])
def test_legacy_opaque_item_identity_is_preserved_and_report_enrichment_matches(environment, opaque_item):
    from backend.modules.vendas import notes
    state = environment
    path = state.database([returned(opaque_item, store=None)])
    before = commercial_rows(path)
    actions.start_search("tenant-a")
    assert state.run()["identified"] == 1
    assert state.calls == [("store-a", opaque_item, "101", {})]
    assert commercial_rows(path) == before
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM devolucao_motivos").fetchone()
        assert row["item_id_unico"] == opaque_item and row["source_kind"] == "legacy"
        assert len(row["source_fingerprint"]) == 64
        source = [{"id_unico": opaque_item, "store_id": None, "loja_conta": "Loja A"}]
        notes._enrich_return_reasons(conn, path, source, "tenant-a", catalog=actions.load_composition_catalog("tenant-a"))
        assert source[0]["motivo_devolucao"]["status"] == "identified"


@pytest.mark.parametrize("mutation", ["owner", "quantity", "alias_proof"])
def test_legacy_source_and_owner_are_revalidated_before_accepting_provider_result(environment, monkeypatch, mutation):
    state = environment
    opaque_item = "NF devolução item ç"
    path = state.database([returned(opaque_item, store=None)])
    owner = actions.legacy_owner_resolver("tenant-a")
    with sqlite3.connect(path) as conn:
        assert queue.prepare_legacy_saved_item(conn, "store-a", "Loja A", opaque_item, "101", "200", owner)
        job = queue.claim(conn, "store-a", legacy_owner=owner)
        assert job and job["item_id_unico"] == opaque_item
        if mutation == "owner":
            conn.execute("UPDATE notas_entrada_itens SET loja_conta='Loja B'")
        elif mutation == "quantity":
            conn.execute("UPDATE notas_entrada_itens SET quantidade=9")
        else:
            monkeypatch.setattr(actions, "load_composition_catalog", lambda tenant: SimpleNamespace(_row_store=lambda *_: ""))
        assert not queue.finish(conn, job, identified(), legacy_owner=owner)
        assert conn.execute("SELECT status,reason_id FROM devolucao_motivos").fetchone() == ("obsolete", None)
        assert conn.execute("SELECT store_id FROM notas_entrada_itens").fetchone()[0] is None


def test_ambiguous_legacy_alias_or_invalid_document_id_never_reaches_provider(environment, monkeypatch):
    from backend.modules.vendas.compositions import CompositionCatalog
    state = environment
    path = state.database([returned("ambiguous", store=None, name="Same"), returned("invalid-note", note="NF 991", store=None)])
    catalog = CompositionCatalog(stores=[{"store_id": "store-a", "nome": "Loja A", "nomes_anteriores": ["Same"]},
                                         {"store_id": "store-b", "nome": "Loja B", "nomes_anteriores": ["Same"]}])
    monkeypatch.setattr(actions, "load_composition_catalog", lambda tenant: catalog)
    actions.start_search("tenant-a")
    result = state.run(lambda _: pytest.fail("Unproved legacy identity reached provider"))
    assert result["identified"] == 0 and result["skipped"] == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='devolucao_motivos'").fetchone()[0] == 0


@pytest.mark.parametrize("bad_id", ["", "  ", "item\nrow", "nul\x00id", "item\u202eid", "x" * 513])
def test_invalid_opaque_item_is_counted_without_creating_provider_job(environment, bad_id):
    state = environment
    state.database([returned(bad_id, store=None)])
    actions.start_search("tenant-a")
    result = state.run(lambda _: pytest.fail("Invalid item identity reached provider"))
    assert result["skipped"] == result["processed"] == result["total"] == 1
