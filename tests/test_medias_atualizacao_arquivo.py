"""Exercise file reconciliation against isolated storage and real XLSX bytes."""

import copy
import io
from pathlib import Path

import openpyxl
import pytest
from fastapi import HTTPException

from backend.services import medias_compras_common as common
from backend.services import medias_compras_importacao as imports
from backend.services import medias_compras_fiscal as fiscal


def workbook(rows, headers=("SKU", "Quantity", "Cost", "Subtotal", "Description")):
    book = openpyxl.Workbook()
    book.active.append(list(headers))
    for row in rows:
        book.active.append(list(row))
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    def location(client):
        return str(tmp_path / client / "listas_pedidos.json")

    monkeypatch.setattr(common, "_arquivo_listas_pedidos", location)
    monkeypatch.setattr(imports, "_carregar_listas_pedidos", common._carregar_listas_pedidos)
    monkeypatch.setattr(imports, "_limpar_cache_lista_pedido", lambda *_a, **_kw: None)
    monkeypatch.setattr(imports, "_resumo_lista_pedido", lambda row, **_kw: {
        "id": row["id"], "status": row["status"],
    })
    monkeypatch.setattr(imports, "_recalcular_frete_internacional_itens_lista",
                        lambda _client, items, **_kw: copy.deepcopy(items))
    items = [common._normalizar_item_lista_pedido(item) for item in [
        {"SKU": "001", "Quantidade": 2, "Valor unidade": 10, "Valor total": 20,
         common.TITULO_PRODUTO_INGLES_KEY: "Original", "Observacoes": "Preservar"},
        {"SKU": "002", "Quantidade": 3, "Valor unidade": 4, "Valor total": 12,
         common.TITULO_PRODUTO_INGLES_KEY: "Segundo"},
    ]]
    rows = [{"id": "a", "store_id": "store-a", "status": "Analisando orçamento",
             "updated_at": "2026-10-01T00:00:00", "itens": items},
            {"id": "b", "store_id": "store-b", "status": "Analisando orçamento",
             "itens": copy.deepcopy(items)}]
    for client in ("tenant-a", "tenant-b"):
        common._salvar_listas_pedidos(client, copy.deepcopy(rows))
    saves = []

    def save(client, current):
        saves.append(client)
        common._salvar_listas_pedidos(client, current)

    monkeypatch.setattr(imports, "_salvar_listas_pedidos", save)

    class Storage:
        def bytes(self, client="tenant-a"):
            return Path(location(client)).read_bytes()

        def rows(self, client="tenant-a"):
            return common._carregar_listas_pedidos(client)

        def change(self, update):
            current = self.rows()
            update(current)
            common._salvar_listas_pedidos("tenant-a", current)

    result = Storage()
    result.saves = saves
    return result


def call(contents, **kwargs):
    params = {"lista_id": "a", "conteudo": contents, "client_id": "tenant-a",
              "confirmar_inclusoes": "0", "previsualizar": "0",
              "sincronizar_arquivo": "0", "confirmar_exclusoes": "",
              "token_confirmacao": ""}
    params.update(kwargs)
    return imports._api_medias_compras_lista_pedido_importar_excel_precos_sync(**params)


def apply(contents, **kwargs):
    preview = call(contents, previsualizar="1", **kwargs)
    return call(contents, confirmar_inclusoes="1",
                token_confirmacao=preview["token_confirmacao"], **kwargs)


def test_summary_failure_does_not_leave_a_partially_saved_update(storage, monkeypatch):
    before = storage.bytes()
    contents = workbook([("001", 5, 6, None, "Original")])

    def failed_summary(*_args, **_kwargs):
        raise RuntimeError("synthetic summary failure")

    monkeypatch.setattr(imports, "_resumo_lista_pedido", failed_summary)
    with pytest.raises(RuntimeError, match="synthetic summary failure"):
        apply(contents)
    assert storage.bytes() == before
    assert storage.saves == []


def test_cache_cleanup_failure_keeps_the_successful_report(storage, monkeypatch):
    contents = workbook([("001", 5, 6, None, "Original")])

    def failed_cleanup(*_args, **_kwargs):
        raise PermissionError("synthetic cache failure")

    monkeypatch.setattr(imports, "_limpar_cache_lista_pedido", failed_cleanup)
    result = apply(contents)
    assert result["success"] is True
    assert storage.rows()[0]["itens"][0]["Quantidade"] == 5
    assert result["relatorio"]["atualizados"][0]["sku"] == "001"


def test_preview_lists_all_changes_without_mutating_storage(storage):
    contents = workbook([("001", 5, 6, None, "Original"), ("003", 7, 2, None, "Novo")])
    before = storage.bytes()
    preview = call(contents, previsualizar="1", sincronizar_arquivo="1")
    assert preview["previsualizacao"] is True
    assert preview["success"] is False and preview["requer_confirmacao"] is True
    assert preview["token_confirmacao"]
    assert {(row["tipo"], row["sku"]) for row in preview["pendencias"]} >= {
        ("sku_novo", "003"), ("sku_ausente", "002")}
    assert storage.bytes() == before
    assert storage.saves == []


def test_mixed_file_never_partially_saves_before_inclusion_confirmation(storage):
    contents = workbook([("001", 5, 6), ("003", 7, 2)])
    before = storage.bytes()
    response = call(contents)
    assert response["requer_confirmacao"] is True
    assert response["success"] is False
    assert storage.bytes() == before and storage.saves == []


def test_confirmed_reconciliation_matches_file_and_reports_before_after(storage):
    contents = workbook([("001", 5, 6, None, "Original"), ("003", 7, 2, None, "Novo")])
    untouched_list = copy.deepcopy(storage.rows()[1])
    response = apply(contents, sincronizar_arquivo="1", confirmar_exclusoes="1")
    assert response["success"] is True
    current = storage.rows()
    assert [item["SKU"] for item in current[0]["itens"]] == ["001", "003"]
    assert current[0]["itens"][0]["Quantidade"] == 5
    assert current[0]["itens"][0]["Valor unidade"] == 6
    assert current[0]["itens"][0]["Valor total"] == 30
    assert current[0]["itens"][0]["Observacoes"] == "Preservar"
    assert current[1] == untouched_list
    assert storage.saves == ["tenant-a"]
    report = response["relatorio"]
    assert [item["sku"] for item in report["incluidos"]] == ["003"]
    assert [item["sku"] for item in report["excluidos"]] == ["002"]
    changes = {field["campo"]: (field["antes"], field["depois"])
               for item in report["atualizados"] if item["sku"] == "001"
               for field in item["campos"]}
    assert changes["Quantidade"] == (2, 5)
    assert changes["Valor unidade"] == (10, 6)
    assert changes["Valor total"] == (20, 30)
    assert response["total_skus_excluidos"] == 1


def test_declining_deletion_keeps_missing_items_and_reports_them(storage):
    contents = workbook([("001", 5, 6)])
    response = apply(contents, sincronizar_arquivo="1", confirmar_exclusoes="0")
    assert response["success"] is True
    assert [item["SKU"] for item in storage.rows()[0]["itens"]] == ["001", "002"]
    assert [item["sku"] for item in response["relatorio"]["mantidos"]] == ["002"]
    assert response["relatorio"]["excluidos"] == []


def test_additive_import_does_not_offer_or_perform_deletions(storage):
    contents = workbook([("001", 5, 6)])
    preview = call(contents, previsualizar="1", sincronizar_arquivo="0")
    assert not any(item["tipo"] == "sku_ausente" for item in preview["pendencias"])
    response = call(contents, sincronizar_arquivo="0", token_confirmacao=preview["token_confirmacao"])
    assert response["success"] is True
    assert {item["SKU"] for item in storage.rows()[0]["itens"]} == {"001", "002"}


@pytest.mark.parametrize("values,expected", [
    ((0, 0, 0), (0, 0, 0)),
    ((None, 3, None), (2, 3, 6)),
    ((4, None, None), (4, 10, 40)),
    ((4, None, 18), (4, 4.5, 18)),
    ((None, None, 9), (2, 4.5, 9)),
    ((4, 2, 7), (4, 2, 7)),
    ((2, 0, None), (2, 0, 0)),
    ((2, 1.23456, 2.46912), (2, 1.23456, 2.46912)),
    ((None, None, None), (2, 10, 20)),
])
def test_numeric_values_are_authoritative_and_blanks_are_not_zero(storage, values, expected):
    response = apply(workbook([("001", *values)]))
    assert response["success"] is True
    item = storage.rows()[0]["itens"][0]
    assert (item["Quantidade"], item["Valor unidade"], item["Valor total"]) == expected


@pytest.mark.parametrize("headers,values,expected", [
    (("SKU", "Quantidade"), ("001", 7), (7, 10, 70)),
    (("SKU", "Valor unidade"), ("001", 3), (2, 3, 6)),
    (("SKU", "Valor total"), ("001", 7), (2, 3.5, 7)),
])
def test_file_can_contain_only_quantity_or_only_price_columns(storage, headers, values, expected):
    response = apply(workbook([values], headers))
    assert response["success"] is True
    item = storage.rows()[0]["itens"][0]
    assert (item["Quantidade"], item["Valor unidade"], item["Valor total"]) == expected


@pytest.mark.parametrize("values", [("003", None, 3), ("003", 2, None), ("003", None, None)])
def test_new_items_require_quantity_and_price_instead_of_inventing_values(storage, values):
    before = storage.bytes()
    with pytest.raises(HTTPException) as error:
        call(workbook([values]), previsualizar="1", sincronizar_arquivo="1")
    assert error.value.status_code == 400
    assert storage.bytes() == before and storage.saves == []


@pytest.mark.parametrize("rows", [
    [], [("001", "abc", 5)], [("001", -2, 5)], [("001", 2.5, 5)],
    [("001", 2, -5)], [("001", 2, "inválido")], [("001", 2, 5, -3)],
    [("001", 2, 5), ("001", 3, 5)],
])
def test_invalid_or_ambiguous_file_cannot_trigger_mass_deletion(storage, rows):
    before = storage.bytes()
    with pytest.raises(HTTPException) as error:
        call(workbook(rows), previsualizar="1", sincronizar_arquivo="1")
    assert error.value.status_code in (400, 422)
    assert storage.bytes() == before and storage.saves == []


def test_delete_requires_token_for_the_displayed_state(storage):
    before = storage.bytes()
    response = call(workbook([("001", 5, 6)]), sincronizar_arquivo="1",
                    confirmar_inclusoes="1", confirmar_exclusoes="1")
    assert response["previsualizacao"] is True and response["success"] is False
    assert response["token_confirmacao"]
    assert storage.bytes() == before and storage.saves == []


@pytest.mark.parametrize("mismatch", ["new_item", "quantity", "file", "tenant", "list"])
def test_confirmation_cannot_apply_to_changed_state_file_or_scope(storage, mismatch):
    contents = workbook([("001", 5, 6)])
    preview = call(contents, previsualizar="1", sincronizar_arquivo="1")
    params = {"sincronizar_arquivo": "1", "confirmar_inclusoes": "1",
              "confirmar_exclusoes": "1", "token_confirmacao": preview["token_confirmacao"]}
    if mismatch == "new_item":
        storage.change(lambda rows: rows[0]["itens"].append({"SKU": "004", "Quantidade": 7}))
    elif mismatch == "quantity":
        storage.change(lambda rows: rows[0]["itens"][0].update({"Quantidade": 99}))
    elif mismatch == "file":
        contents = workbook([("001", 20, 6)])
    elif mismatch == "tenant":
        params["client_id"] = "tenant-b"
    else:
        params["lista_id"] = "b"
    before = {tenant: storage.bytes(tenant) for tenant in ("tenant-a", "tenant-b")}
    with pytest.raises(HTTPException) as error:
        call(contents, **params)
    assert error.value.status_code == 409
    assert {tenant: storage.bytes(tenant) for tenant in before} == before
    assert storage.saves == []


def test_approved_item_cannot_be_deleted_by_file_reconciliation(storage):
    storage.change(lambda rows: rows[0]["itens"][1].update({"compra_aprovada": True}))
    before = storage.bytes()
    with pytest.raises(HTTPException) as error:
        apply(workbook([("001", 5, 6)]), sincronizar_arquivo="1", confirmar_exclusoes="1")
    assert error.value.status_code == 409
    assert storage.bytes() == before and storage.saves == []


def test_identical_values_are_reported_as_unchanged(storage):
    response = apply(workbook([("001", 2, 10, 20, "Original"), ("002", 3, 4, 12, "Segundo")]))
    assert response["success"] is True
    assert response["relatorio"]["atualizados"] == []
    assert set(response["relatorio"]["inalterados"]) == {"001", "002"}


def test_report_describes_final_persisted_calculation(storage, monkeypatch):
    def recalculate(_client, items, **_kwargs):
        result = copy.deepcopy(items)
        result[0]["Frete Internacional"] = 2.75
        return result

    monkeypatch.setattr(imports, "_recalcular_frete_internacional_itens_lista", recalculate)
    response = apply(workbook([("001", 5, 6)]))
    persisted = storage.rows()[0]["itens"][0]
    changes = {field["campo"]: field for item in response["relatorio"]["atualizados"]
               if item["sku"] == "001" for field in item["campos"]}
    assert changes["Frete Internacional"]["depois"] == persisted["Frete Internacional"] == 2.75
    assert changes["Frete Internacional"]["antes"] == 0


def test_formula_sku_without_cached_value_cannot_become_a_deletion(storage):
    contents = workbook([("001", 5, 6), ('="002"', 3, 4)])
    before = storage.bytes()
    with pytest.raises(HTTPException) as error:
        call(contents, previsualizar="1", sincronizar_arquivo="1")
    assert error.value.status_code == 400
    assert storage.bytes() == before and storage.saves == []


@pytest.mark.parametrize("column", [1, 2, 3], ids=["quantity", "unit_price", "total_price"])
def test_excel_error_cell_is_never_interpreted_as_zero(storage, column):
    row = ["001", 2, 10, 20]
    row[column] = "#DIV/0!"
    contents = workbook([row])
    before = storage.bytes()
    with pytest.raises(HTTPException) as error:
        call(contents, previsualizar="1", sincronizar_arquivo="1")
    assert error.value.status_code == 400
    assert storage.bytes() == before and storage.saves == []


def test_explicit_zero_total_stays_zero_in_real_list_summary(storage):
    response = apply(workbook([("001", 2, 10, 0)]),
                     sincronizar_arquivo="1", confirmar_exclusoes="1")
    saved = storage.rows()[0]
    assert saved["itens"][0]["valor_total_informado"] is True
    assert common._resumo_lista_pedido(saved, m3_lookup={})["total_usd"] == 0
    fields = {field["campo"] for item in response["relatorio"]["atualizados"]
              for field in item["campos"]}
    assert "valor_total_informado" not in fields
    apply(workbook([("001", 2, 3, None)]))
    saved = storage.rows()[0]
    assert not saved["itens"][0].get("valor_total_informado")
    assert common._resumo_lista_pedido(saved, m3_lookup={})["total_usd"] == 6


def test_freight_calculation_uses_explicit_zero_total_without_legacy_fallback(monkeypatch):
    items = [
        {"SKU": "001", "Quantidade": 2, "Valor unidade": 10,
         "Valor total": 0, "valor_total_informado": True},
        {"SKU": "002", "Quantidade": 2, "Valor unidade": 10, "Valor total": 20},
    ]

    def enrich(_client, rows, **_kwargs):
        return [{**row, "M3": 1, "M3 individual": 0.5, "Cubagem status": "cadastro"}
                for row in rows]

    monkeypatch.setattr(fiscal, "_enriquecer_itens_lista_pedido_com_impostos", enrich)
    result = fiscal._recalcular_frete_internacional_itens_lista("tenant-a", items)
    assert [item["Frete Internacional"] for item in result] == [10, 10]
    assert result[0]["Valor total"] == 0


def test_concurrent_save_invalidates_confirmation_instead_of_deleting_new_items(storage, monkeypatch):
    contents = workbook([("001", 5, 6)])
    preview = call(contents, previsualizar="1", sincronizar_arquivo="1")
    attempts = []

    def raced_save(client, proposed):
        attempts.append(client)
        current = common._carregar_listas_pedidos(client)
        current[0]["itens"].append({"SKU": "004", "Quantidade": 9})
        common._salvar_listas_pedidos(client, current)
        # Exercise the real snapshot conflict and the endpoint's retry decorator.
        common._salvar_listas_pedidos(client, proposed)

    monkeypatch.setattr(imports, "_salvar_listas_pedidos", raced_save)
    with pytest.raises(HTTPException) as error:
        call(contents, sincronizar_arquivo="1", confirmar_inclusoes="1",
             confirmar_exclusoes="1", token_confirmacao=preview["token_confirmacao"])
    assert error.value.status_code == 409
    assert attempts == ["tenant-a"], 'retry must stop at stale confirmation before another save'
    saved = storage.rows()[0]
    assert [item["SKU"] for item in saved["itens"]] == ["001", "002", "004"]
    assert saved["itens"][0]["Quantidade"] == 2
    assert saved["itens"][0]["Valor unidade"] == 10
