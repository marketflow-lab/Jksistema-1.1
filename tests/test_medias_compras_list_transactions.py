import asyncio
import copy
import io
import json
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.services import medias_compras_common as common
from backend.services import medias_compras_listas as service


@pytest.fixture
def store(tmp_path, monkeypatch):
    def location(client_id):
        return str(tmp_path / client_id / "listas_pedidos.json")
    monkeypatch.setattr(common, "_arquivo_listas_pedidos", location)
    rows = [
        {"id": "a", "store_id": "store-a", "itens": [{"SKU": "001", "Quantidade": 2}]},
        {"id": "b", "store_id": "store-b", "itens": [{"SKU": "002", "Quantidade": 3}]},
    ]
    common._salvar_listas_pedidos("tenant-a", rows)
    return location, rows


def test_concurrent_changes_to_different_lists_are_merged(store):
    first = common._carregar_listas_pedidos("tenant-a")
    second = common._carregar_listas_pedidos("tenant-a")
    first[0]["itens"][0]["compra_aprovada"] = True
    second[1]["itens"][0]["Quantidade"] = 8
    common._salvar_listas_pedidos("tenant-a", first)
    common._salvar_listas_pedidos("tenant-a", second)
    current = common._carregar_listas_pedidos("tenant-a")
    assert current[0]["itens"][0]["compra_aprovada"] is True
    assert current[1]["itens"][0]["Quantidade"] == 8
    assert second == current


@pytest.mark.parametrize("change", ["approval", "store", "deletion"])
def test_stale_backfill_cannot_overwrite_target_changes(store, change):
    location, _ = store
    stale = common._carregar_listas_pedidos("tenant-a")
    edited = common._carregar_listas_pedidos("tenant-a")
    stale[0]["itens"][0]["frete_internacional"] = 4
    if change == "approval":
        edited[0]["itens"][0]["compra_aprovada"] = True
    elif change == "store":
        edited[0]["store_id"] = "replacement"
    else:
        edited.pop(0)
    common._salvar_listas_pedidos("tenant-a", edited)
    committed = Path(location("tenant-a")).read_bytes()
    with pytest.raises(common._ListaPedidoConflict) as error:
        common._salvar_listas_pedidos("tenant-a", stale)
    assert error.value.status_code == 409
    assert Path(location("tenant-a")).read_bytes() == committed


def test_insert_and_delete_preserve_other_writer(store):
    created = common._carregar_listas_pedidos("tenant-a")
    removed = common._carregar_listas_pedidos("tenant-a")
    created.insert(0, {"id": "new", "itens": []})
    removed.pop(1)
    common._salvar_listas_pedidos("tenant-a", created)
    common._salvar_listas_pedidos("tenant-a", removed)
    assert [row["id"] for row in common._carregar_listas_pedidos("tenant-a")] == ["new", "a"]


def test_snapshot_cannot_be_committed_into_another_tenant(store):
    snapshot = common._carregar_listas_pedidos("tenant-a")
    with pytest.raises(common._ListaPedidoConflict):
        common._salvar_listas_pedidos("tenant-b", snapshot)
    assert not Path(store[0]("tenant-b")).exists()


def test_equivalent_paths_share_commit_coordination(store, monkeypatch):
    location, _ = store
    first = common._carregar_listas_pedidos("tenant-a")
    path = Path(location("tenant-a"))
    monkeypatch.setattr(common, "_arquivo_listas_pedidos", lambda _client: str(path.parent / "." / path.name))
    second = common._carregar_listas_pedidos("tenant-a")
    first[0]["status"] = "first"
    second[1]["status"] = "second"
    common._salvar_listas_pedidos("tenant-a", first)
    common._salvar_listas_pedidos("tenant-a", second)
    assert [row["status"] for row in common._carregar_listas_pedidos("tenant-a")] == ["first", "second"]


def test_failed_replace_keeps_original_and_cleans_temporary(store, monkeypatch):
    location, _ = store
    path = Path(location("tenant-a"))
    original = path.read_bytes()
    snapshot = common._carregar_listas_pedidos("tenant-a")
    snapshot[0]["status"] = "new"
    def denied(*_args):
        raise PermissionError("simulated")
    monkeypatch.setattr(common.os, "replace", denied)
    with pytest.raises(PermissionError):
        common._salvar_listas_pedidos("tenant-a", snapshot)
    assert path.read_bytes() == original
    assert not list(path.parent.glob(".listas-pedidos-*.tmp"))


def test_readers_observe_complete_json_during_commit(store, monkeypatch):
    location, _ = store
    entered, release = threading.Event(), threading.Event()
    replace = common.os.replace
    errors = []
    def delayed(source, target):
        entered.set()
        assert release.wait(3)
        replace(source, target)
    monkeypatch.setattr(common.os, "replace", delayed)
    snapshot = common._carregar_listas_pedidos("tenant-a")
    snapshot[0]["status"] = "new"
    def write():
        try:
            common._salvar_listas_pedidos("tenant-a", snapshot)
        except Exception as error:
            errors.append(error)
    writer = threading.Thread(target=write)
    writer.start()
    try:
        assert entered.wait(3)
        for _ in range(50):
            assert json.loads(Path(location("tenant-a")).read_bytes())[0].get("status") is None
    finally:
        release.set()
        writer.join(3)
    assert not writer.is_alive()
    assert not errors
    assert common._carregar_listas_pedidos("tenant-a")[0]["status"] == "new"


def test_detail_recalculates_on_approval_race(store, monkeypatch):
    monkeypatch.setattr(service, "_carregar_listas_pedidos", common._carregar_listas_pedidos)
    monkeypatch.setattr(service, "_salvar_listas_pedidos", common._salvar_listas_pedidos)
    monkeypatch.setattr(service, "_limpar_cache_lista_pedido", lambda *_args, **_kw: None)
    monkeypatch.setattr(service, "_resumo_lista_pedido", lambda row: {"id": row["id"]})
    from backend.services import cadastro_compatibilidade
    monkeypatch.setattr(cadastro_compatibilidade, "visao_produtos_cadastro_contexto_loja",
                        lambda *_args: {"scope": "resolved", "store_id": "store-a"})
    calls = []
    def recalculate(client, items, **_kwargs):
        calls.append(copy.deepcopy(items))
        if len(calls) == 1:
            edit = common._carregar_listas_pedidos(client)
            edit[0]["itens"][0]["compra_aprovada"] = True
            common._salvar_listas_pedidos(client, edit)
        result = copy.deepcopy(items)
        result[0]["frete_internacional"] = 4
        return result
    monkeypatch.setattr(service, "_recalcular_frete_internacional_itens_lista", recalculate)
    result = asyncio.run(service.api_medias_compras_lista_pedido_detalhe("a", client_id="tenant-a"))
    assert len(calls) == 2
    assert result["lista"]["itens"][0]["compra_aprovada"] is True
    assert common._carregar_listas_pedidos("tenant-a")[0]["itens"][0]["compra_aprovada"] is True


def test_retry_is_bounded_and_only_retries_snapshot_conflicts():
    calls = []
    @common._retry_listas_pedidos
    def contested():
        calls.append(1)
        raise common._ListaPedidoConflict()
    with pytest.raises(HTTPException) as error:
        contested()
    assert error.value.status_code == 409
    assert len(calls) == 2
    calls.clear()
    @common._retry_listas_pedidos
    async def missing():
        calls.append(1)
        raise HTTPException(404, "missing")
    with pytest.raises(HTTPException) as error:
        asyncio.run(missing())
    assert error.value.status_code == 404
    assert len(calls) == 1


def test_invalid_json_is_preserved(store):
    path = Path(store[0]("tenant-a"))
    path.write_bytes(b"{invalid")
    with pytest.raises(HTTPException):
        common._salvar_listas_pedidos("tenant-a", [])
    assert path.read_bytes() == b"{invalid"


def test_report_replenishment_uses_same_transaction_as_api_edits(store, monkeypatch):
    from backend.services import codex_reports_advanced as reports
    location, _ = store
    (Path(location("tenant-a")).parent / "cadastro_fornecedores.json").write_text(
        json.dumps({"schema": "jk.cadastro.fornecedores.v1", "fornecedores": [
            {"id": "supplier-a", "nome_empresa": "Synthetic supplier"}]}), encoding="utf-8")
    original_save = common._salvar_listas_pedidos_no_caminho

    def edit_before_report_commit(path, rows):
        edited = common._carregar_listas_pedidos("tenant-a")
        edited[0]["nome_lista"] = "concurrent-edit"
        original_save(path, edited)
        original_save(path, rows)

    monkeypatch.setattr(common, "_salvar_listas_pedidos_no_caminho", edit_before_report_commit)
    result = reports.create_replenishment_list(
        info_base=str(Path(location("tenant-a")).parent.parent), client_id="tenant-a",
        username="synthetic-user", action={"store": "store-a", "fornecedor_id": "supplier-a",
                                           "items": [{"sku": "003", "quantity": 6}]})
    current = common._carregar_listas_pedidos("tenant-a")
    assert current[0]["id"] == result["list_id"]
    assert current[0]["itens"][0]["Quantidade"] == 6
    assert current[1]["nome_lista"] == "concurrent-edit"
    assert [row["id"] for row in current[1:]] == ["a", "b"]


@pytest.mark.parametrize("mutation", ["edit", "approve", "delete", "import"])
def test_backfill_preserves_concurrent_public_mutations(store, monkeypatch, mutation):
    from types import SimpleNamespace
    import openpyxl
    from fastapi import UploadFile
    from backend.services import cadastro_compatibilidade, medias_compras_importacao as imports

    for module in (service, imports):
        monkeypatch.setattr(module, "_carregar_listas_pedidos", common._carregar_listas_pedidos)
        monkeypatch.setattr(module, "_salvar_listas_pedidos", common._salvar_listas_pedidos)
        monkeypatch.setattr(module, "_limpar_cache_lista_pedido", lambda *_args, **_kw: None)
        monkeypatch.setattr(module, "_resumo_lista_pedido", lambda row: {"id": row["id"]})
    monkeypatch.setattr(cadastro_compatibilidade, "visao_produtos_cadastro_contexto_loja",
                        lambda *_args: {"scope": "resolved", "store_id": "store-a"})
    entered, release = threading.Event(), threading.Event()
    calls, errors = [], []

    def recalculate(_client, items, **_kwargs):
        calls.append(copy.deepcopy(items))
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        result = copy.deepcopy(items)
        result[0]["frete_internacional"] = 4
        return result

    monkeypatch.setattr(service, "_recalcular_frete_internacional_itens_lista", recalculate)
    monkeypatch.setattr(imports, "_recalcular_frete_internacional_itens_lista",
                        lambda _client, items, **_kw: copy.deepcopy(items))

    def backfill():
        try:
            asyncio.run(service.api_medias_compras_lista_pedido_detalhe("a", client_id="tenant-a"))
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=backfill)
    worker.start()
    try:
        assert entered.wait(5)
        if mutation == "edit":
            asyncio.run(service.api_medias_compras_lista_pedido_editar(
                "a", service.ListaPedidoUpdateRequest(nome_lista="edited"), client_id="tenant-a"))
        elif mutation == "approve":
            asyncio.run(service.api_medias_compras_lista_pedido_atualizar_aprovacao_sku(
                "a", "001", service.ListaPedidoSkuAprovacaoRequest(aprovada=True), client_id="tenant-a"))
        elif mutation == "delete":
            asyncio.run(service.api_medias_compras_lista_pedido_excluir(
                "a", SimpleNamespace(headers={}), client_id="tenant-a"))
        else:
            workbook = openpyxl.Workbook()
            workbook.active.append(["SKU", "Quantity", "Cost"])
            workbook.active.append(["001", 9, 7])
            contents = io.BytesIO()
            workbook.save(contents)
            contents.seek(0)
            upload = UploadFile(contents, filename="synthetic.xlsx")
            asyncio.run(imports.api_medias_compras_lista_pedido_importar_excel_precos(
                "a", upload, confirmar_inclusoes="0", client_id="tenant-a"))
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    current = common._carregar_listas_pedidos("tenant-a")
    if mutation == "delete":
        assert [row["id"] for row in current] == ["b"]
        assert len(errors) == 1 and errors[0].status_code == 404
    else:
        assert not errors
        assert len(calls) == 2
        assert current[0]["itens"][0]["frete_internacional"] == 4
        if mutation == "edit":
            assert current[0]["nome_lista"] == "edited"
        elif mutation == "approve":
            assert current[0]["itens"][0]["compra_aprovada"] is True
        else:
            assert current[0]["itens"][0]["Quantidade"] == 9
            assert current[0]["itens"][0]["Valor unidade"] == 7


def test_cancelled_request_finishes_atomic_json_commit(store, monkeypatch):
    import anyio
    from backend.services.blocking_workers import run_heavy

    entered, release = threading.Event(), threading.Event()
    replace = common.os.replace
    snapshot = common._carregar_listas_pedidos("tenant-a")
    snapshot[0]["nome_lista"] = "committed"
    original = Path(store[0]("tenant-a")).read_bytes()

    def delayed(source, target):
        entered.set()
        assert release.wait(5)
        replace(source, target)

    monkeypatch.setattr(common.os, "replace", delayed)

    async def scenario():
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(run_heavy, common._salvar_listas_pedidos, "tenant-a", snapshot)
            assert await anyio.to_thread.run_sync(entered.wait, 3)
            tasks.cancel_scope.cancel()
            assert Path(store[0]("tenant-a")).read_bytes() == original
            release.set()

    try:
        anyio.run(scenario)
    finally:
        release.set()
    assert common._carregar_listas_pedidos("tenant-a")[0]["nome_lista"] == "committed"
    assert not list(Path(store[0]("tenant-a")).parent.glob(".listas-pedidos-*.tmp"))


def test_xlsx_cache_replace_is_atomic_and_late_cleanup_preserves_new_version(store, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(common, "_pasta_cache_listas_pedidos", lambda _client: str(cache))
    snapshot = common._carregar_listas_pedidos("tenant-a")
    snapshot[0]["updated_at"] = "new"
    common._salvar_listas_pedidos("tenant-a", snapshot)
    path = common._salvar_bytes_cache_lista_pedido("tenant-a", "a", "new", b"old-complete-bytes")
    replace = common.os.replace

    def inspect_replace(source, target):
        assert Path(target).read_bytes() == b"old-complete-bytes"
        assert Path(source).read_bytes() == b"new-complete-bytes"
        replace(source, target)

    monkeypatch.setattr(common.os, "replace", inspect_replace)
    common._salvar_bytes_cache_lista_pedido("tenant-a", "a", "new", b"new-complete-bytes")
    monkeypatch.setattr(common.os, "replace", replace)
    common._salvar_bytes_cache_lista_pedido("tenant-a", "a", "old", b"stale-but-complete")
    common._limpar_cache_lista_pedido("tenant-a", "a", manter_versao="old")
    assert Path(path).read_bytes() == b"new-complete-bytes"
    assert not list(cache.glob(".lista-xlsx-*.tmp"))
