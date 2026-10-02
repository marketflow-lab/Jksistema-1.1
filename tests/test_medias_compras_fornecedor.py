import asyncio
import io
import json

import openpyxl
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.routers.medias_compras import create_medias_compras_router
from backend.schemas.medias_compras import ListaPedidoUpdateRequest
from backend.services import cadastro_fornecedores
from backend.services import medias_compras_common as common
from backend.services import medias_compras_importacao as importacao
from backend.services import medias_compras_listas as listas


@pytest.fixture
def cadastro(monkeypatch, tmp_path):
    root = tmp_path / "info"
    for client_id, fornecedores in (
        ("cliente-a", [{"id": "fornecedor-a", "nome_empresa": "Empresa A"}, {"id": "fornecedor-b", "nome_empresa": "Empresa B"}]),
        ("cliente-b", [{"id": "fornecedor-outro", "nome_empresa": "Empresa de outro cliente"}]),
    ):
        tenant = root / client_id
        tenant.mkdir(parents=True)
        (tenant / "cadastro_fornecedores.json").write_text(
            json.dumps({"schema": "jk.cadastro.fornecedores.v1", "fornecedores": fornecedores}),
            encoding="utf-8",
        )
    monkeypatch.setattr(common, "get_tenant_path", lambda client_id: str(root / client_id))
    monkeypatch.setattr(cadastro_fornecedores, "get_tenant_path", lambda client_id: str(root / client_id))
    monkeypatch.setattr(listas, "_limpar_cache_lista_pedido", lambda *_args, **_kwargs: None)
    return root


@pytest.fixture
def client(cadastro, monkeypatch):
    monkeypatch.setattr(importacao, "_resolver_escopo_loja_medias", lambda *_args, **_kwargs: {
        "loja": "Loja A", "store_id": "store-a", "scope": "store",
    })
    monkeypatch.setattr(importacao, "_recalcular_frete_internacional_itens_lista", lambda _client_id, itens, **_kwargs: itens)
    monkeypatch.setattr(importacao, "_limpar_cache_lista_pedido", lambda *_args, **_kwargs: None)
    app = FastAPI()
    app.include_router(create_medias_compras_router())
    app.dependency_overrides[common.get_tenant_id] = lambda: "cliente-a"
    with TestClient(app) as test_client:
        yield test_client


def _excel():
    workbook = openpyxl.Workbook()
    workbook.active.append(["SKU", "Quantidade", "Valor unidade"])
    workbook.active.append(["001", 3, 2.5])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _salvar_lista(root, **overrides):
    lista = {
        "id": "lista-1", "nome_lista": "Lista existente", "loja": "Loja A",
        "store_id": "store-a", "itens": [], "supplier": "Empresa A", "fornecedor_id": "fornecedor-a",
    }
    lista.update(overrides)
    path = root / "cliente-a" / "listas_pedidos.json"
    path.write_text(json.dumps([lista]), encoding="utf-8")
    return path


@pytest.mark.parametrize("fornecedor_id", [None, "", "desconhecido", "fornecedor-outro"])
@pytest.mark.parametrize("origem", ["post", "get", "excel"])
def test_criacao_exige_fornecedor_do_cliente_sem_gravar_lista(client, cadastro, origem, fornecedor_id):
    payload = {"opcao": "media_6m", "nome_lista": "Nova lista", "store_id": "store-a"}
    if fornecedor_id is not None:
        payload["fornecedor_id"] = fornecedor_id
    if origem == "post":
        response = client.post("/api/medias-compras/gerar-lista-compra", json={**payload, "supplier": "Texto livre"})
    elif origem == "get":
        response = client.get("/api/medias-compras/gerar-lista-compra", params=payload)
    else:
        response = client.post(
            "/api/medias-compras/listas-pedidos/importar-excel", data=payload,
            files={"file": ("pedido.xlsx", _excel(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert response.status_code == 400, response.text
    assert "fornecedor" in response.json()["detail"].lower()
    assert not (cadastro / "cliente-a" / "listas_pedidos.json").exists()


def test_importacao_excel_salva_id_e_nome_do_cadastro(client, cadastro):
    response = client.post(
        "/api/medias-compras/listas-pedidos/importar-excel",
        data={"nome_lista": "Pedido", "store_id": "store-a", "fornecedor_id": "fornecedor-a", "supplier": "Nome adulterado"},
        files={"file": ("pedido.xlsx", _excel(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200, response.text
    salvo = json.loads((cadastro / "cliente-a" / "listas_pedidos.json").read_text(encoding="utf-8"))[0]
    assert salvo["fornecedor_id"] == response.json()["lista"]["fornecedor_id"] == "fornecedor-a"
    assert salvo["supplier"] == response.json()["lista"]["supplier"] == "Empresa A"
    assert salvo["store_id"] == "store-a"
    assert salvo["itens"][0]["Quantidade"] == 3


@pytest.mark.parametrize("payload", [
    {"fornecedor_id": ""}, {"fornecedor_id": None}, {"fornecedor_id": "desconhecido"},
    {"fornecedor_id": "fornecedor-outro"}, {"supplier": "Nome livre"}, {"supplier": ""}, {"supplier": None},
])
def test_edicao_nao_remove_nem_adultera_vinculo_e_preserva_arquivo(cadastro, payload):
    path = _salvar_lista(cadastro)
    antes = path.read_bytes()
    with pytest.raises(HTTPException) as error:
        asyncio.run(listas.api_medias_compras_lista_pedido_editar(
            "lista-1", ListaPedidoUpdateRequest(nome_lista="Nome que nao deve ser salvo", **payload), "cliente-a",
        ))
    assert error.value.status_code == 400
    assert path.read_bytes() == antes


def test_troca_do_fornecedor_usa_nome_cadastrado_em_vez_do_texto_recebido(cadastro):
    path = _salvar_lista(cadastro)
    response = asyncio.run(listas.api_medias_compras_lista_pedido_editar(
        "lista-1", ListaPedidoUpdateRequest(fornecedor_id="fornecedor-b", supplier="Nome adulterado"), "cliente-a",
    ))
    salvo = json.loads(path.read_text(encoding="utf-8"))[0]
    assert salvo["fornecedor_id"] == response["lista"]["fornecedor_id"] == "fornecedor-b"
    assert salvo["supplier"] == response["lista"]["supplier"] == "Empresa B"


def test_lista_antiga_continua_editavel_e_pode_ser_vinculada(cadastro):
    path = _salvar_lista(cadastro, fornecedor_id="", supplier="Fornecedor legado")
    antigo = asyncio.run(listas.api_medias_compras_lista_pedido_editar(
        "lista-1", ListaPedidoUpdateRequest(nome_lista="Lista antiga editada"), "cliente-a",
    ))
    assert antigo["lista"]["fornecedor_id"] == ""
    assert antigo["lista"]["supplier"] == "Fornecedor legado"
    response = asyncio.run(listas.api_medias_compras_lista_pedido_editar(
        "lista-1", ListaPedidoUpdateRequest(fornecedor_id="fornecedor-a"), "cliente-a",
    ))
    assert response["lista"]["supplier"] == "Empresa A"
    assert json.loads(path.read_text(encoding="utf-8"))[0]["fornecedor_id"] == "fornecedor-a"


def test_nome_historico_permite_editar_lista_apos_exclusao_do_cadastro(cadastro):
    path = _salvar_lista(cadastro)
    (cadastro / "cliente-a" / "cadastro_fornecedores.json").unlink()
    response = asyncio.run(listas.api_medias_compras_lista_pedido_editar(
        "lista-1", ListaPedidoUpdateRequest(numero_invoice="INV-123", supplier="Empresa A"), "cliente-a",
    ))
    assert response["lista"]["fornecedor_id"] == "fornecedor-a"
    assert response["lista"]["supplier"] == "Empresa A"
    assert json.loads(path.read_text(encoding="utf-8"))[0]["numero_invoice"] == "INV-123"


def test_cadastro_invalido_bloqueia_criacao_sem_gravar_lista(client, cadastro):
    (cadastro / "cliente-a" / "cadastro_fornecedores.json").write_text("json invalido", encoding="utf-8")
    response = client.post(
        "/api/medias-compras/gerar-lista-compra",
        json={"opcao": "media_6m", "nome_lista": "Pedido", "fornecedor_id": "fornecedor-a"},
    )
    assert response.status_code == 500
    assert not (cadastro / "cliente-a" / "listas_pedidos.json").exists()


@pytest.mark.parametrize("destino", ["lista", "fila"])
@pytest.mark.parametrize("fornecedor_id", [None, "desconhecido", "fornecedor-outro"])
def test_reposicao_black_jhon_valida_fornecedor_antes_de_persistir(cadastro, destino, fornecedor_id):
    from backend.services import codex_reports_advanced as reports

    action = {"action_type": "replenishment", "items": [{"sku": "001", "quantity": 3}], "fornecedor_id": fornecedor_id}
    with pytest.raises(HTTPException) as error:
        if destino == "lista":
            reports.create_replenishment_list(info_base=str(cadastro), client_id="cliente-a", action=action, username="compras")
        else:
            reports.create_queue_action(info_base=str(cadastro), client_id="cliente-a", payload=action, username="compras")
    assert error.value.status_code == 400
    assert not (cadastro / "cliente-a" / "listas_pedidos.json").exists()
    assert reports.queue_actions_list(str(cadastro), "cliente-a") == []


@pytest.mark.parametrize("fornecedor_id", [None, "fornecedor-outro"])
def test_executor_black_jhon_nao_usa_fornecedor_inferido_do_relatorio(cadastro, monkeypatch, fornecedor_id):
    from backend.services import codex_actions, codex_assistant_storage
    from backend.services import codex_reports_advanced as reports
    from backend.services.codex.assistant import runtime

    action = {"action_id": "action-1", "action_type": "replenishment", "queueable": True,
              "items": [{"sku": "001", "quantity": 3}], "fornecedor_id": "fornecedor-a"}
    codex_assistant_storage.codex_assistant_report_save(
        str(cadastro), "cliente-a", {"report_id": "report-1", "top_actions": [action]},
    )
    monkeypatch.setattr(runtime, "info_base", lambda: str(cadastro))
    proposal = {"client_id": "cliente-a", "action": {"id": "reports.queue_replenishment"},
                "params": {"report_id": "report-1", "report_action": action, "fornecedor_id": fornecedor_id}}
    with pytest.raises(HTTPException) as error:
        codex_actions._execute_internal_report_queue("run-1", proposal)
    assert error.value.status_code == 400
    assert reports.queue_actions_list(str(cadastro), "cliente-a") == []
    assert not (cadastro / "cliente-a" / "listas_pedidos.json").exists()
