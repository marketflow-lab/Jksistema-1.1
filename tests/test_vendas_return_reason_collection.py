from __future__ import annotations

import json
import sqlite3

import pytest

from backend.modules.vendas import return_reason_queue as queue, return_reason_worker as worker, sync_period
from backend.modules.vendas.errors import VendasDomainError
from backend.schemas import VendasSyncRequest
from backend.services import vendas_collection_checkpoint as checkpoint
from backend.services.bling_return_references import reference_from_note, store_reference


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "remember_authorization", lambda *_: None)
    monkeypatch.setattr(worker, "wake", lambda *_: None)
    monkeypatch.setattr(sync_period, "_verificar_cancelamento", lambda *_: None)
    monkeypatch.setattr(sync_period, "_legado_loja_nao_ambiguo", lambda *_: True)
    req = VendasSyncRequest(loja="Loja A", store_id="store-a", data_inicio="2026-10-20", data_fim="2026-10-20")
    note = ("header-101", "101", "10", "2026-10-20", "Devolucao", "4", 1,
            100, "", "N 10", "Loja A", "", "")
    item = ("item-a", "101", "10", "N 10", "2026-10-20", "200", "Produto",
            1, 100, 100, "Devolucao", "4", 1, "", "Loja A", "", "")
    return tmp_path / "vendas_historico_loja_a.db", req, note, item


def test_collected_returns_and_reason_jobs_are_one_commit_with_unchanged_totals(source):
    path, req, note, item = source
    refs = {"101": {"order_id": "2000001234567", "raw_response": "never saved"}}
    counts = sync_period._persistir_periodo_atomico(str(path), req, [], [note], [item], "tenant-a", return_references=refs)
    assert counts == {"vendas": 0, "notas": 1, "itens": 1}
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT SUM(quantidade),SUM(valor_total) FROM notas_entrada_itens").fetchone() == (1, 100)
        record = db.execute("SELECT store_id,nota_id,sku,reference_json,status FROM devolucao_motivos").fetchone()
        assert record[:3] == ("store-a", "101", "200")
        assert json.loads(record[3]) == {"order_id": "2000001234567"}
        assert record[4] == "pending"
    original = path.read_bytes()
    summary = queue.process_due(str(path), "store-a", lambda job: {
        "result_status": "identified", "reason_id": "PDD1234", "reason_description": "Produto diferente",
        "reason_source": "mercado_livre", "order_id": job["order_id"],
    })
    assert summary["identified"] == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT quantidade,valor_total,devolucao FROM notas_entrada_itens").fetchone() == (1, 100, 1)
        assert db.execute("SELECT COUNT(*) FROM vendas").fetchone() == (0,)
    assert path.read_bytes() != original  # Only enrichment metadata is persisted.


def test_failed_enqueue_rolls_back_commercial_changes_together(source, monkeypatch):
    path, req, note, item = source
    sync_period._persistir_periodo_atomico(str(path), req, [], [note], [item], "tenant-a")
    def fail(conn, *_args, **_kwargs):
        conn.execute("UPDATE notas_entrada_itens SET quantidade=999")
        raise sqlite3.OperationalError("synthetic failure")
    monkeypatch.setattr(queue, "enqueue", fail)
    with pytest.raises(VendasDomainError):
        sync_period._persistir_periodo_atomico(str(path), req, [], [note], [item], "tenant-a")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT quantidade,valor_total FROM notas_entrada_itens").fetchone() == (1, 100)
        assert db.execute("SELECT COUNT(*) FROM devolucao_motivos").fetchone() == (1,)


def test_provider_outage_never_changes_saved_sales_or_returns(source):
    path, req, note, item = source
    sync_period._persistir_periodo_atomico(str(path), req, [], [note], [item], "tenant-a")
    def outage(_job):
        raise TimeoutError("not persisted")
    summary = queue.process_due(str(path), "store-a", outage)
    assert summary["retry"] == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT quantidade,valor_total FROM notas_entrada_itens").fetchone() == (1, 100)
        assert db.execute("SELECT status,error_code FROM devolucao_motivos").fetchone() == ("retry", "resolver_exception")


def test_new_observation_clears_old_result_and_requeues_without_duplicating_item(source):
    path, req, note, item = source
    sync_period._persistir_periodo_atomico(str(path), req, [], [note], [item], "tenant-a",
                                         return_references={"101": {"order_id": "123"}})
    queue.process_due(str(path), "store-a", lambda _: {
        "result_status": "identified", "reason_id": "old", "reason_description": "Anterior", "reason_source": "bling",
    })
    sync_period._persistir_periodo_atomico(str(path), req, [], [note], [item], "tenant-a",
                                         return_references={"101": {"order_id": "456"}})
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM notas_entrada_itens").fetchone() == (1,)
        assert db.execute("SELECT status,reason_id FROM devolucao_motivos").fetchone() == ("pending", None)
        assert json.loads(db.execute("SELECT reference_json FROM devolucao_motivos").fetchone()[0]) == {"order_id": "456"}


def test_detail_normalization_retains_only_allowlisted_reference(source, monkeypatch):
    monkeypatch.setattr(sync_period, "_resolver_nome_loja_virtual", lambda *_args, **_kw: "Canal")
    monkeypatch.setattr(sync_period, "_normalizar_nome_loja_virtual_candidato", lambda value: str(value or ""))
    monkeypatch.setattr(sync_period, "_extrair_codigo_origem_nf", lambda _: "N 10")
    monkeypatch.setattr(sync_period, "_normalizar_unidade_devolucao_entrada", lambda *_: "Canal")
    monkeypatch.setattr(sync_period, "_eh_devolucao_nota_entrada", lambda *_: 1)
    monkeypatch.setattr(sync_period, "_eh_devolucao_por_cfop_itens", lambda *_: False)
    monkeypatch.setattr(sync_period, "_tipo_devolucao_cfop_full_estoque", lambda *_: "")
    note = {"id": 101, "data_emissao": "2026-10-20"}
    raw = {"id": 101, "numeroPedidoLoja": "2000001234567", "observacoes": "not copied",
           "itens": [{"codigo": "200", "quantidade": 1, "valor": 100}]}
    sync_period._aplicar_detalhe_nota(note, raw, {})
    assert reference_from_note(note) == {"order_id": "2000001234567"}
    assert "observacoes" not in note


def test_return_reference_survives_interrupted_collection_checkpoint(tmp_path):
    day = "2026-10-20"
    note = {"id": "101", "data_emissao": day, "_detalhe_validado": True}
    store_reference(note, {"numeroPedidoLoja": "2000001234567", "origem": "N 10"})
    payload = {"sales": [], "notes": [note], "items": [{
        "id_nota": "101", "item_id": "1", "data_emissao": day,
        "quantidade": 1, "valor_total": 100,
    }]}
    with checkpoint.collection_checkpoint("tenant-a", "store-a", "cycle", day, tenant_path=tmp_path):
        checkpoint.save_collected_document("nfe_entrada", "101", {"id": 101}, payload)
    with checkpoint.collection_checkpoint("tenant-a", "store-a", "cycle", day, tenant_path=tmp_path):
        saved = checkpoint.load_collected_document("nfe_entrada", "101", {"id": 101})
    assert reference_from_note(saved["notes"][0]) == {"order_id": "2000001234567", "original_invoice_number": "10"}


@pytest.mark.parametrize("value", ["access_token=secret", "123@invalid.test", {"order_id": "123"}, ["123"], True])
def test_checkpoint_rejects_untyped_or_sensitive_return_reference(value):
    day = "2026-10-20"
    payload = {"sales": [], "notes": [{"id": "101", "data_emissao": day, "_detalhe_validado": True,
                                       "_return_reason_order_id": value}], "items": [{
        "id_nota": "101", "item_id": "1", "data_emissao": day, "quantidade": 1, "valor_total": 100,
    }]}
    assert not checkpoint._valid_payload(payload, "nfe_entrada", "101", day)


def test_new_download_wakes_worker_only_after_committed_queue_and_never_after_rollback(source, monkeypatch):
    path, req, note, item = source
    calls = []
    monkeypatch.setattr(worker, "remember_authorization", lambda tenant, store: calls.append((tenant, store)))
    def wake(tenant):
        with sqlite3.connect(path, timeout=.1) as conn:
            conn.execute("BEGIN IMMEDIATE")
            assert conn.execute("SELECT store_id,status FROM devolucao_motivos").fetchall() == [("store-a", "pending")]
        calls.append((tenant, "wake"))
    monkeypatch.setattr(worker, "wake", wake)
    sync_period._persistir_periodo_atomico(str(path), req, [], [note], [item], "tenant-a")
    assert calls == [("tenant-a", "store-a"), ("tenant-a", "wake")]
    calls.clear()
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic rollback")
    monkeypatch.setattr(queue, "enqueue", fail)
    with pytest.raises(VendasDomainError):
        sync_period._persistir_periodo_atomico(str(path), req, [], [note], [item], "tenant-a")
    assert calls == []
