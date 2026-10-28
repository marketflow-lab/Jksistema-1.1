from __future__ import annotations

import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
import requests

from backend.modules.vendas import notes, return_reason_queue, return_reasons_bling, return_reasons_ml
from backend.modules.vendas.compositions import CompositionCatalog
from backend.services import bling_vendas


COLUMNS = {
    "id_unico": "TEXT", "store_id": "TEXT", "id_nota": "TEXT",
    "numero_nota": "TEXT", "origem_codigo": "TEXT", "data_emissao": "TEXT",
    "sku": "TEXT", "descricao": "TEXT", "quantidade": "REAL",
    "valor_unitario": "REAL", "valor_total": "REAL", "fornecedor": "TEXT",
    "unidade_negocio_virtual": "TEXT", "unidade_negocio": "TEXT",
    "natureza_operacao": "TEXT", "finalidade_operacao": "TEXT",
    "loja_conta": "TEXT", "devolucao": "INTEGER",
}
EMPTY = {"status": "not_identified", "descricao": "", "codigo": "", "fonte": "", "status_coleta": ""}


def returned(ident, **changes):
    row = dict(
        id_unico=ident, store_id="store-a", id_nota="note-" + ident,
        numero_nota=ident, origem_codigo="", data_emissao="2026-10-15",
        sku="200", descricao="Produto", quantidade=2, valor_unitario=10,
        valor_total=20, fornecedor="Comprador de exemplo",
        unidade_negocio_virtual="Canal A", unidade_negocio="Canal A",
        natureza_operacao="Devolucao", finalidade_operacao="4",
        loja_conta="Loja A", devolucao=1,
    )
    row.update(changes)
    return row


def reason(ident, **changes):
    row = dict(
        store_id="store-a", item_id_unico=ident, nota_id="note-" + ident,
        loja="Loja A", sku="200", reference_json="{}", fingerprint="fixture",
        status="done", result_status="identified", reason_id="PDD1",
        reason_description="Produto danificado", reason_source="mercado_livre",
        created_at=1, updated_at=2,
    )
    row.update(changes)
    return row


@pytest.fixture
def report(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Opening a return report must not query providers or run enrichment")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(return_reason_queue, "process_due", forbidden)
    monkeypatch.setattr(return_reasons_bling, "resolve_reference", forbidden)
    monkeypatch.setattr(return_reasons_ml, "resolve_reason", forbidden)
    monkeypatch.setattr(bling_vendas, "get_tenant_path", lambda client_id: str(tmp_path / client_id))
    monkeypatch.setattr(notes, "_listar_bancos_vendas_tenant", bling_vendas._listar_bancos_vendas_tenant)
    monkeypatch.setattr(notes, "_sql_filtro_loja_notas_entrada", bling_vendas._sql_filtro_loja_notas_entrada)
    monkeypatch.setattr(notes, "_sql_filtro_unidade_devolucao", bling_vendas._sql_filtro_unidade_devolucao)
    monkeypatch.setattr(notes, "_classificar_unidade_virtual_devolucao", bling_vendas._classificar_unidade_virtual_devolucao)
    state = SimpleNamespace(catalog=CompositionCatalog.from_products(
        [], stores=[{"store_id": "store-a", "nome": "Loja A"},
                    {"store_id": "store-b", "nome": "Loja B"}],
    ))
    monkeypatch.setattr(notes, "load_composition_catalog", lambda _client_id: state.catalog)

    def create(name="vendas_historico.db", rows=(), reasons=None, *, tenant="tenant-a", omit=()):
        folder = tmp_path / tenant
        folder.mkdir(exist_ok=True)
        path = folder / name
        with sqlite3.connect(path) as conn:
            columns = {key: kind for key, kind in COLUMNS.items() if key not in omit}
            conn.execute("CREATE TABLE notas_entrada_itens (" + ", ".join(
                f"{key} {kind}" for key, kind in columns.items()) + ")")
            for row in rows:
                item = {key: value for key, value in row.items() if key in columns}
                conn.execute("INSERT INTO notas_entrada_itens (" + ",".join(item) + ") VALUES (" +
                             ",".join("?" for _ in item) + ")", list(item.values()))
            if reasons is not None:
                return_reason_queue.ensure_schema(conn)
                for item in reasons:
                    conn.execute("INSERT INTO devolucao_motivos (" + ",".join(item) + ") VALUES (" +
                                 ",".join("?" for _ in item) + ")", list(item.values()))
        return path

    def read(mode="items", tenant="tenant-a", sku="200", **filters):
        if mode == "sku":
            return notes.listar_notas_entrada_por_sku(sku, tenant, **filters)
        return notes.listar_itens_devolucoes(tenant, **filters)

    def enrich_legacy(path, ident):
        owner = lambda source: state.catalog._row_store(source, None)
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            source = dict(conn.execute("SELECT * FROM notas_entrada_itens WHERE id_unico=?", (ident,)).fetchone())
            assert return_reason_queue.prepare_legacy_saved_item(
                conn, "store-a", "Loja A", ident, source["id_nota"], source["sku"], owner, now=1,
            )
            job = return_reason_queue.claim(conn, "store-a", now=2, legacy_owner=owner)
            assert job is not None
            assert return_reason_queue.finish(conn, job, {
                "result_status": "identified", "reason_id": "PDD1",
                "reason_description": "Produto danificado", "reason_source": "mercado_livre",
            }, now=3, legacy_owner=owner)

    return SimpleNamespace(create=create, read=read, enrich_legacy=enrich_legacy, state=state)


@pytest.mark.parametrize("mode", ["items", "sku"])
def test_report_joins_exact_store_and_item_without_changing_commercial_data(report, mode):
    rows = [returned("shared"), returned("shared", store_id="store-b", loja_conta="Loja B"),
            returned("missing")]
    path = report.create(rows=rows, reasons=[
        reason("shared"), reason("shared", store_id="store-b", loja="Loja B",
                                 reason_description="Compra por engano", reason_source="bling"),
    ])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = report.read(mode)
    assert len(result) == 3
    by_key = {(item["store_id"], item["id_unico"]): item for item in result}
    assert by_key[("store-a", "shared")]["motivo_devolucao"] == {
        "status": "identified", "descricao": "Produto danificado", "codigo": "PDD1",
        "fonte": "mercado_livre", "status_coleta": "done",
    }
    assert by_key[("store-b", "shared")]["motivo_devolucao"]["descricao"] == "Compra por engano"
    assert by_key[("store-a", "missing")]["motivo_devolucao"] == EMPTY
    assert sum(item["quantidade"] for item in result) == 6
    assert sum(item["valor_total"] for item in result) == 60
    assert all(item["fornecedor"] == "Comprador de exemplo" for item in result)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("mode", ["items", "sku"])
@pytest.mark.parametrize("queue_state,result_state", [
    ("pending", None), ("processing", None), ("done", "not_informed"),
    ("done", "not_linked"), ("done", "ambiguous"), ("retry", "unavailable"),
    ("retry", "reconnect_required"), ("retry", "identified"), ("processing", "identified"),
])
def test_saved_result_and_collection_status_remain_distinct(report, mode, queue_state, result_state):
    report.create(rows=[returned("item")], reasons=[
        reason("item", status=queue_state, result_status=result_state),
    ])
    item = report.read(mode)[0]["motivo_devolucao"]
    assert item["status"] == (result_state or "not_identified")
    assert item["status_coleta"] == queue_state
    assert item["descricao"] == ("Produto danificado" if result_state == "identified" else "")
    assert item["codigo"] == ("PDD1" if result_state == "identified" else "")


@pytest.mark.parametrize("mode", ["items", "sku"])
def test_obsolete_and_missing_metadata_never_become_identified(report, mode):
    report.create(rows=[returned("old"), returned("uncollected")],
                  reasons=[reason("old", status="obsolete")])
    assert [item["motivo_devolucao"] for item in report.read(mode)] == [EMPTY, EMPTY]


@pytest.mark.parametrize("mode", ["items", "sku"])
@pytest.mark.parametrize("omit", [(), ("store_id",), ("store_id", "id_unico")])
def test_legacy_database_without_reason_table_remains_readable_and_unchanged(report, mode, omit):
    path = report.create(rows=[returned("legacy")], omit=omit)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = report.read(mode)
    assert len(result) == 1 and result[0]["motivo_devolucao"] == EMPTY
    assert result[0]["quantidade"] == 2 and result[0]["valor_total"] == 20
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with sqlite3.connect(path) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='devolucao_motivos'").fetchone()


@pytest.mark.parametrize("mode", ["items", "sku"])
def test_legacy_owner_requires_canonical_unambiguous_store_and_never_overrides_explicit_id(report, mode):
    report.create(rows=[
        returned("legacy", store_id=""),
        returned("unknown", store_id="", loja_conta="Unknown"),
        returned("wrong", store_id="store-wrong"),
        returned("case", store_id="STORE-A"),
    ], reasons=[reason(ident) for ident in ["legacy", "unknown", "wrong", "case"]])
    result = {item["id_unico"]: item["motivo_devolucao"] for item in report.read(mode)}
    assert result["legacy"]["status"] == "identified"
    assert all(result[ident] == EMPTY for ident in ["unknown", "wrong", "case"])
    report.state.catalog = CompositionCatalog.from_products([], stores=[
        {"store_id": "store-a", "nome": "Loja A"},
        {"store_id": "store-b", "nome": "Loja A"},
    ])
    assert all(item["motivo_devolucao"] == EMPTY for item in report.read(mode))


@pytest.mark.parametrize("mode", ["items", "sku"])
def test_legacy_table_without_store_id_can_use_proven_local_owner(report, mode):
    report.create(rows=[returned("legacy")], reasons=[reason("legacy")], omit=("store_id",))
    item = report.read(mode)[0]
    assert item["store_id"] == ""
    assert item["motivo_devolucao"]["status"] == "identified"


@pytest.mark.parametrize("mode", ["items", "sku"])
@pytest.mark.parametrize("omit", [(), ("store_id",)])
def test_completed_legacy_enrichment_appears_with_current_local_proof_without_writing(report, mode, omit):
    ident = "  legado ' com acento \u00ed  "
    path = report.create(rows=[returned(ident, store_id="", id_nota="101")], omit=omit)
    assert report.read(mode)[0]["motivo_devolucao"] == EMPTY
    report.enrich_legacy(path, ident)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    item = report.read(mode)[0]
    assert item["id_unico"] == ident
    assert item["motivo_devolucao"] == {
        "status": "identified", "descricao": "Produto danificado", "codigo": "PDD1",
        "fonte": "mercado_livre", "status_coleta": "done",
    }
    assert item["quantidade"] == 2 and item["valor_total"] == 20
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("mode", ["items", "sku"])
@pytest.mark.parametrize("change", ["invoice", "sku", "owner", "owner_alias", "source_hash", "canonicalized", "missing_proof"])
def test_completed_legacy_reason_is_hidden_after_source_or_owner_changes(report, mode, change):
    path = report.create(rows=[returned("legacy", store_id="", id_nota="101")])
    report.enrich_legacy(path, "legacy")
    assert report.read(mode)[0]["motivo_devolucao"]["status"] == "identified"
    updates = {
        "invoice": ("id_nota", "102"), "sku": ("sku", "201"),
        "owner": ("loja_conta", "Loja B"), "source_hash": ("quantidade", 3),
        "canonicalized": ("store_id", "store-a"),
    }
    with sqlite3.connect(path) as conn:
        if change in updates:
            field, value = updates[change]
            conn.execute(f"UPDATE notas_entrada_itens SET {field}=? WHERE id_unico='legacy'", (value,))
        elif change == "missing_proof":
            conn.execute("UPDATE devolucao_motivos SET source_fingerprint=NULL")
        else:
            report.state.catalog = CompositionCatalog.from_products([], stores=[
                {"store_id": "store-a", "nome": "Loja A"},
                {"store_id": "store-b", "nome": "Loja A"},
            ])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    items = report.read(mode, sku="201" if change == "sku" else "200")
    assert len(items) == 1
    assert items[0]["motivo_devolucao"] == EMPTY
    assert items[0]["quantidade"] == (3 if change == "source_hash" else 2)
    assert items[0]["valor_total"] == 20
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT status,result_status FROM devolucao_motivos").fetchone() == ("done", "identified")


@pytest.mark.parametrize("mode", ["items", "sku"])
def test_tenant_store_channel_and_inclusive_dates_filter_before_reporting(report, mode):
    report.create(rows=[
        returned("start", data_emissao="2026-10-01 00:00:00"),
        returned("end", data_emissao="2026-10-31 23:59:59"),
        returned("before", data_emissao="2026-09-30 23:59:59"),
        returned("after", data_emissao="2026-11-01 00:00:00"),
        returned("other-channel", unidade_negocio_virtual="Canal B", unidade_negocio="Canal B"),
        returned("not-return", devolucao=0),
    ], reasons=[reason(ident) for ident in ["start", "end", "before", "after", "other-channel", "not-return"]])
    report.create("vendas_historico_loja_b.db", rows=[
        returned("other-store", store_id="store-b", loja_conta="Loja B"),
    ], reasons=[reason("other-store", store_id="store-b", loja="Loja B")])
    report.create(rows=[returned("start")], reasons=[
        reason("start", reason_description="Outro cliente"),
    ], tenant="tenant-b")
    result = report.read(mode, loja="Loja A", unidade_negocio="Canal A",
                         data_inicio="2026-10-01", data_fim="2026-10-31")
    assert [item["id_unico"] for item in result] == ["end", "start"]
    assert all(item["motivo_devolucao"]["descricao"] == "Produto danificado" for item in result)
    assert report.read(mode, loja="Inexistente") == []


def test_component_inherits_original_reason_without_extra_occurrences_or_money(report):
    report.state.catalog = CompositionCatalog.from_products([
        {"store_id": "store-a", "sku": "200", "id_bling": 101, "produto": "Base"},
        {"store_id": "store-a", "sku": "KIT-200", "id_bling": 102, "produto": "Kit",
         "formato_bling": "E", "componentes_bling_json": json.dumps([
             {"produto": {"id": 101}, "quantidade": 3},
         ])},
    ], stores=[{"store_id": "store-a", "nome": "Loja A"}])
    report.create(rows=[returned("kit", sku="KIT-200")],
                  reasons=[reason("kit", sku="KIT-200")])
    item = report.read("sku", loja="Loja A")[0]
    assert item["sku_original"] == "KIT-200" and item["quantidade"] == 6
    assert item["devolucoes_contribuicao"] == 0 and item["valor_total"] == 0
    assert item["motivo_devolucao"]["descricao"] == "Produto danificado"


@pytest.mark.parametrize("mode", ["items", "sku"])
def test_unavailable_optional_metadata_does_not_drop_returns(report, monkeypatch, mode):
    report.create(rows=[returned("item")], reasons=[reason("item")])
    original = notes.open_vendas_readonly

    class UnavailableMetadata:
        def __init__(self, conn):
            self.conn = conn
        def __getattr__(self, name):
            return getattr(self.conn, name)
        def execute(self, sql, *args):
            if "FROM devolucao_motivos" in sql:
                raise sqlite3.OperationalError("metadata temporarily unavailable")
            return self.conn.execute(sql, *args)

    monkeypatch.setattr(notes, "open_vendas_readonly",
                        lambda *args, **kwargs: UnavailableMetadata(original(*args, **kwargs)))
    result = report.read(mode)
    assert len(result) == 1 and result[0]["valor_total"] == 20
    assert result[0]["motivo_devolucao"] == EMPTY


def test_large_report_reads_saved_reasons_in_bounded_queries(report):
    count = 1005
    report.create(rows=[returned(str(index)) for index in range(count)],
                  reasons=[reason(str(index)) for index in range(count)])
    result = report.read()
    assert len(result) == count
    assert all(item["motivo_devolucao"]["status"] == "identified" for item in result)
    assert sum(item["quantidade"] for item in result) == count * 2
    assert sum(item["valor_total"] for item in result) == count * 20


@pytest.mark.parametrize("mode", ["items", "sku"])
def test_partial_metadata_schema_and_code_only_reason_are_supported(report, mode):
    path = report.create(rows=[returned("item")])
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE devolucao_motivos (
            store_id TEXT, item_id_unico TEXT, status TEXT, result_status TEXT, reason_id TEXT)""")
        conn.execute("INSERT INTO devolucao_motivos VALUES ('store-a','item','done','identified','CODE1')")
    metadata = report.read(mode)[0]["motivo_devolucao"]
    assert metadata == {"status": "identified", "descricao": "", "codigo": "CODE1",
                        "fonte": "", "status_coleta": "done"}


@pytest.mark.parametrize("mode", ["items", "sku"])
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_invalid_identified_metadata_does_not_claim_provider_omitted_reason(report, mode, blank):
    report.create(rows=[returned("invalid")], reasons=[
        reason("invalid", reason_id=blank, reason_description=blank),
    ])
    metadata = report.read(mode)[0]["motivo_devolucao"]
    assert metadata["status"] == "not_identified"
    assert metadata["descricao"] == metadata["codigo"] == ""
    assert metadata["status_coleta"] == "done"


def test_sku_fills_missing_reason_from_exact_mirror_preserving_first_commercial_row(report):
    first = returned("shared", descricao="Descricao original")
    report.create(rows=[first, returned("newer", data_emissao="2026-10-20")])
    report.create("vendas_historico_loja_a.db", rows=[
        {**first, "descricao": "Descricao do espelho", "fornecedor": "Outro comprador",
         "quantidade": 7, "valor_total": 70},
    ], reasons=[reason("shared")])
    result = report.read("sku")
    assert [item["id_unico"] for item in result] == ["newer", "shared"]
    selected = result[1]
    assert selected["descricao"] == "Descricao original"
    assert selected["fornecedor"] == first["fornecedor"]
    assert selected["quantidade"] == 2 and selected["valor_total"] == 20
    assert selected["motivo_devolucao"]["status"] == "identified"
    assert selected["motivo_devolucao"]["descricao"] == "Produto danificado"
    assert sum(item["quantidade"] for item in result) == 4
    assert sum(item["valor_total"] for item in result) == 40


@pytest.mark.parametrize("weak_row", [
    {"store_id": "STORE-A"},
    {"store_id": "", "loja_conta": "store-a"},
    {"id_unico": " shared "},
])
def test_sku_mirror_does_not_enrich_casefolded_or_fallback_identity(report, weak_row):
    first = returned("shared", **weak_row) if "id_unico" not in weak_row else {
        **returned("shared"), **weak_row,
    }
    report.create(rows=[first])
    report.create("vendas_historico_loja_a.db", rows=[returned("shared")],
                  reasons=[reason("shared")])
    result = report.read("sku")
    assert len(result) == 1
    assert result[0]["motivo_devolucao"] == EMPTY
    assert result[0]["store_id"] == first["store_id"]


def test_sku_mirror_does_not_cross_distinct_store_owners(report):
    report.create(rows=[returned("shared")])
    report.create("vendas_historico_loja_b.db", rows=[
        returned("shared", store_id="store-b", loja_conta="Loja B"),
    ], reasons=[reason("shared", store_id="store-b", loja="Loja B")])
    result = {item["store_id"]: item for item in report.read("sku")}
    assert len(result) == 2
    assert result["store-a"]["motivo_devolucao"] == EMPTY
    assert result["store-b"]["motivo_devolucao"]["status"] == "identified"


@pytest.mark.parametrize("status,result_status", [
    ("pending", None), ("processing", None), ("retry", None),
    ("done", "not_informed"), ("done", "ambiguous"), ("done", "identified"),
])
def test_sku_mirror_preserves_existing_evidence_or_pending_invalidation(report, status, result_status):
    report.create(rows=[returned("shared")], reasons=[
        reason("shared", status=status, result_status=result_status,
               reason_id="FIRST", reason_description="Motivo original"),
    ])
    report.create("vendas_historico_loja_a.db", rows=[returned("shared")],
                  reasons=[reason("shared")])
    metadata = report.read("sku")[0]["motivo_devolucao"]
    assert metadata["status"] == (result_status or "not_identified")
    assert metadata["status_coleta"] == status
    if result_status == "identified":
        assert metadata["descricao"] == "Motivo original"
        assert metadata["codigo"] == "FIRST"
    else:
        assert metadata["descricao"] == metadata["codigo"] == ""
