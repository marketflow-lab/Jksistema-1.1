import asyncio
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.routers.medias_compras import create_medias_compras_router
from backend.schemas.medias_compras import ListaPedidoUpdateRequest
from backend.services import cadastro_nacionalizadores as cadastro
from backend.services import medias_compras_common as common
from backend.services import medias_compras_listas as listas


def _empresa(identificador, nome):
    return {
        "id": identificador, "nome_empresa": nome, "cnpj": "", "endereco_empresa": "",
        "nome_contato": "", "telefone": "",
    }


@pytest.fixture
def registry(monkeypatch, tmp_path):
    root = tmp_path / "info"
    for client_id, registros in (
        ("tenant-a", [_empresa("shared-id", "Empresa Sintética A"), _empresa("second-id", "Segunda Empresa")]),
        ("tenant-b", [_empresa("shared-id", "Empresa Sintética B"), _empresa("foreign-only", "Empresa Estrangeira")]),
    ):
        tenant = root / client_id
        tenant.mkdir(parents=True)
        (tenant / "cadastro_nacionalizadores.json").write_text(
            json.dumps({"schema": "jk.cadastro.nacionalizadores.v1", "nacionalizadores": registros}), encoding="utf-8",
        )
        (tenant / "listas_pedidos.json").write_text(json.dumps([
            {"id": "list-a", "nome_lista": "Pedido A", "loja": "Loja A", "store_id": "store-a",
             "fornecedor_id": "supplier-a", "supplier": "Fornecedor Sintético", "itens": [], "status": "Lista gerada"},
            {"id": "list-b", "nome_lista": "Pedido B", "loja": "Loja B", "store_id": "store-b", "itens": []},
        ]), encoding="utf-8")
    monkeypatch.setattr(common, "get_tenant_path", lambda client_id: str(root / client_id))
    monkeypatch.setattr(cadastro, "get_tenant_path", lambda *_args: pytest.fail("O vínculo deve usar pasta_tenant canônica"))
    monkeypatch.setattr(listas, "_limpar_cache_lista_pedido", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(listas, "_construir_mapa_m3_sku", lambda *_args: {})
    return root


def _path(registry, client_id="tenant-a"):
    return registry / client_id / "listas_pedidos.json"


def _rows(registry, client_id="tenant-a"):
    return json.loads(_path(registry, client_id).read_text(encoding="utf-8"))


def _seed_link(registry, *, identificador="shared-id", nome="Nome Histórico", client_id="tenant-a"):
    rows = _rows(registry, client_id)
    rows[0].update(nacionalizador_id=identificador, nacionalizador=nome)
    _path(registry, client_id).write_text(json.dumps(rows), encoding="utf-8")
    return rows[0]


def _edit(payload, client_id="tenant-a", lista_id="list-a"):
    return asyncio.run(listas.api_medias_compras_lista_pedido_editar(
        lista_id, ListaPedidoUpdateRequest(**payload), client_id,
    ))


def _api(monkeypatch, provider=None):
    def authenticated(_request, authorization=None):
        if authorization not in {"Bearer tenant-a", "Bearer tenant-b"}:
            raise HTTPException(status_code=401, detail="Token inválido.")
        return authorization.removeprefix("Bearer ")

    monkeypatch.setattr(common, "_get_tenant_id_fn", provider or authenticated)
    app = FastAPI()
    app.include_router(create_medias_compras_router())
    return TestClient(app, raise_server_exceptions=False)


def test_vinculo_usa_id_nome_do_tenant_e_preserva_loja(monkeypatch, registry):
    antes_a, antes_b = _rows(registry), _rows(registry, "tenant-b")
    source_before = (registry / "tenant-a" / "cadastro_nacionalizadores.json").read_bytes()
    with _api(monkeypatch) as client:
        response = client.put(
            "/api/medias-compras/listas-pedidos/list-a",
            headers={"Authorization": "Bearer tenant-a"},
            json={"nacionalizador_id": "shared-id", "nacionalizador": "Nome adulterado", "client_id": "tenant-b"},
        )
    assert response.status_code == 200
    salvo = _rows(registry)[0]
    assert salvo["nacionalizador_id"] == response.json()["lista"]["nacionalizador_id"] == "shared-id"
    assert salvo["nacionalizador"] == response.json()["lista"]["nacionalizador"] == "Empresa Sintética A"
    for campo in ("loja", "store_id", "itens", "fornecedor_id", "supplier"):
        assert salvo[campo] == antes_a[0][campo]
    assert _rows(registry)[1] == antes_a[1]
    assert _rows(registry, "tenant-b") == antes_b
    assert (registry / "tenant-a" / "cadastro_nacionalizadores.json").read_bytes() == source_before


def test_ids_iguais_em_clientes_distintos_resolvem_nomes_distintos(registry):
    for client_id, nome in (("tenant-a", "Empresa Sintética A"), ("tenant-b", "Empresa Sintética B")):
        response = _edit({"nacionalizador_id": "shared-id"}, client_id)
        assert response["lista"]["nacionalizador"] == nome
    assert _rows(registry)[0]["nacionalizador"] != _rows(registry, "tenant-b")[0]["nacionalizador"]


def test_mesmo_cliente_compartilha_cadastro_e_preserva_vinculos_das_lojas(registry):
    _edit({"nacionalizador_id": "shared-id"})
    _edit({"nacionalizador_id": "second-id"}, lista_id="list-b")
    rows = _rows(registry)
    assert [(row["store_id"], row["nacionalizador"]) for row in rows] == [
        ("store-a", "Empresa Sintética A"), ("store-b", "Segunda Empresa"),
    ]


@pytest.mark.parametrize("identificador", ["unknown-id", "foreign-only"])
def test_id_invalido_ou_de_outro_cliente_nao_grava(registry, identificador):
    path = _path(registry)
    antes = path.read_bytes()
    with pytest.raises(HTTPException) as error:
        _edit({"nacionalizador_id": identificador, "nome_lista": "Mudança que deve falhar"})
    assert error.value.status_code == 400
    assert path.read_bytes() == antes


@pytest.mark.parametrize("valor", ["", None, "  "])
def test_retirada_limpa_id_e_nome_sem_consultar_cadastro(registry, monkeypatch, valor):
    original = _seed_link(registry)
    monkeypatch.setattr(cadastro, "ler_nacionalizador_por_id", lambda *_args, **_kwargs: pytest.fail("Retirada não precisa ler cadastro"))
    response = _edit({"nacionalizador_id": valor, "nacionalizador": "Texto que não deve persistir"})
    salvo = _rows(registry)[0]
    assert response["lista"]["nacionalizador_id"] == salvo["nacionalizador_id"] == ""
    assert response["lista"]["nacionalizador"] == salvo["nacionalizador"] == ""
    assert (salvo["loja"], salvo["store_id"]) == (original["loja"], original["store_id"])


@pytest.mark.parametrize("estado", ["renamed", "missing", "invalid"])
def test_omissao_preserva_historico_sem_reconsultar_cadastro(registry, monkeypatch, estado):
    _seed_link(registry)
    path = registry / "tenant-a" / "cadastro_nacionalizadores.json"
    if estado == "missing":
        path.unlink()
    elif estado == "invalid":
        path.write_text("json inválido", encoding="utf-8")
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["nacionalizadores"][0]["nome_empresa"] = "Empresa Renomeada"
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(listas, "_resolver_nacionalizador_lista", lambda *_args: pytest.fail("Omissão deve preservar histórico"))
    response = _edit({"nome_lista": "Pedido Editado", "nacionalizador": "Nome Histórico"})
    assert response["lista"]["nacionalizador_id"] == "shared-id"
    assert response["lista"]["nacionalizador"] == "Nome Histórico"
    assert _rows(registry)[0]["nome_lista"] == "Pedido Editado"


def test_lista_antiga_sem_nacionalizador_continua_editavel(registry):
    response = _edit({"nome_lista": "Pedido Antigo Editado"})
    assert response["lista"]["nacionalizador_id"] == response["lista"]["nacionalizador"] == ""
    assert "nacionalizador_id" not in _rows(registry)[0]
    assert "nacionalizador" not in _rows(registry)[0]


@pytest.mark.parametrize("identificador", ["shared-id", ""])
@pytest.mark.parametrize("nome", ["Texto livre", "", None])
def test_nome_direto_diferente_do_historico_e_rejeitado(registry, identificador, nome):
    _seed_link(registry, identificador=identificador)
    antes = _path(registry).read_bytes()
    with pytest.raises(HTTPException) as error:
        _edit({"nacionalizador": nome, "nome_lista": "Alteração inválida"})
    assert error.value.status_code == 400
    assert _path(registry).read_bytes() == antes


def test_nome_sem_id_nao_vincula_lista_antiga(registry):
    antes = _path(registry).read_bytes()
    with pytest.raises(HTTPException) as error:
        _edit({"nacionalizador": "Empresa Sintética A"})
    assert error.value.status_code == 400
    assert _path(registry).read_bytes() == antes


@pytest.mark.parametrize("estado", ["missing", "invalid", "empty-name"])
def test_cadastro_ausente_ou_invalido_bloqueia_novo_vinculo(registry, estado):
    path = registry / "tenant-a" / "cadastro_nacionalizadores.json"
    if estado == "missing":
        path.unlink()
    elif estado == "invalid":
        path.write_text("json inválido", encoding="utf-8")
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["nacionalizadores"][0]["nome_empresa"] = ""
        path.write_text(json.dumps(payload), encoding="utf-8")
    antes = _path(registry).read_bytes()
    with pytest.raises(HTTPException) as error:
        _edit({"nacionalizador_id": "shared-id"})
    assert error.value.status_code == {"missing": 400, "invalid": 500, "empty-name": 409}[estado]
    assert _path(registry).read_bytes() == antes


@pytest.mark.parametrize("status", [401, 403])
def test_put_sem_autorizacao_nao_acessa_registro(monkeypatch, registry, status):
    antes = _path(registry).read_bytes()

    def deny(_request, _authorization=None):
        raise HTTPException(status_code=status, detail="Acesso negado.")

    monkeypatch.setattr(common, "get_tenant_path", lambda *_args: pytest.fail("Dados inacessíveis sem autorização"))
    with _api(monkeypatch, deny) as client:
        response = client.put("/api/medias-compras/listas-pedidos/list-a", json={"nacionalizador_id": "shared-id"})
    assert response.status_code == status
    assert _path(registry).read_bytes() == antes


def test_lista_de_outro_cliente_nao_pode_ser_alterada(registry):
    rows = _rows(registry, "tenant-b")
    rows[0]["id"] = "foreign-list"
    _path(registry, "tenant-b").write_text(json.dumps(rows), encoding="utf-8")
    antes = _path(registry, "tenant-b").read_bytes()
    with pytest.raises(HTTPException) as error:
        _edit({"nacionalizador_id": "shared-id"}, lista_id="foreign-list")
    assert error.value.status_code == 404
    assert _path(registry, "tenant-b").read_bytes() == antes


def test_resumos_expoem_vinculo_e_filtro_preserva_loja(registry, monkeypatch):
    _edit({"nacionalizador_id": "shared-id"})
    _edit({"nacionalizador_id": "second-id"}, lista_id="list-b")
    monkeypatch.setattr(listas, "_resolver_escopo_loja_medias", lambda _client, _loja, store_id: {"store_id": store_id})
    response = asyncio.run(listas.api_medias_compras_listas_pedidos(store_id="store-a", client_id="tenant-a"))
    assert len(response["listas"]) == 1
    assert response["listas"][0]["id"] == "list-a"
    assert response["listas"][0]["nacionalizador_id"] == "shared-id"
    assert response["listas"][0]["nacionalizador"] == "Empresa Sintética A"


def test_edicoes_concorrentes_de_listas_distintas_preservam_vinculos(registry, monkeypatch):
    barrier = threading.Barrier(2)
    original_save = listas._salvar_listas_pedidos

    def synchronized_save(client_id, rows):
        barrier.wait(timeout=5)
        return original_save(client_id, rows)

    monkeypatch.setattr(listas, "_salvar_listas_pedidos", synchronized_save)

    def edit(values):
        lista_id, nacionalizador_id = values
        return listas._api_medias_compras_lista_pedido_editar_sync(
            lista_id, ListaPedidoUpdateRequest(nacionalizador_id=nacionalizador_id), "tenant-a",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(edit, [("list-a", "shared-id"), ("list-b", "second-id")]))
    rows = _rows(registry)
    assert [(row["id"], row["nacionalizador_id"], row["store_id"]) for row in rows] == [
        ("list-a", "shared-id", "store-a"), ("list-b", "second-id", "store-b"),
    ]


def test_falha_de_commit_preserva_vinculo_anterior(registry, monkeypatch):
    _seed_link(registry)
    antes = _path(registry).read_bytes()

    def fail_replace(_source, _target):
        raise OSError("Falha sintética de publicação")

    monkeypatch.setattr(common.os, "replace", fail_replace)
    with pytest.raises(OSError):
        _edit({"nacionalizador_id": "second-id"})
    assert _path(registry).read_bytes() == antes
    assert not list(_path(registry).parent.glob(".listas-pedidos-*.tmp"))


def test_leitor_por_id_respeita_pasta_canonica_e_devolve_copia(registry):
    registro = cadastro.ler_nacionalizador_por_id("tenant-a", "shared-id", pasta_tenant=str(registry / "tenant-a"))
    assert registro["nome_empresa"] == "Empresa Sintética A"
    original = copy.deepcopy(registro)
    registro["nome_empresa"] = "Mudança apenas em memória"
    assert cadastro.ler_nacionalizador_por_id("tenant-a", "shared-id", pasta_tenant=str(registry / "tenant-a")) == original
