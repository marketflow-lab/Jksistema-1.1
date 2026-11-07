"""Exercise the shared HTTP contract with real SQLite and synthetic tenants."""

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.testclient import TestClient

from backend.routers.comentarios_sku import create_comentarios_sku_router
from backend.services import admin_usuarios_common as admin
from backend.services import cadastro_compatibilidade as cadastro


def test_comentarios_persistem_com_permissao_e_escopo(monkeypatch, tmp_path):
    endpoint = "/api/comentarios-sku"
    lojas = {
        "cliente-a": [{"store_id": "a", "nome": "Loja A"}, {"store_id": "b", "nome": "Loja B"}],
        "cliente-b": [{"store_id": "a", "nome": "Loja A"}],
    }
    sessoes = {
        "medias-a": ("cliente-a", {"medias_compras": True}),
        "vendas-a": ("cliente-a", {"vendas": True}),
        "vendas-b": ("cliente-b", {"vendas": True}),
        "cadastro-a": ("cliente-a", {"cadastro": True}),
    }

    def pasta_tenant(cliente):
        pasta = tmp_path / cliente
        pasta.mkdir(exist_ok=True)
        return str(pasta)

    monkeypatch.setattr(cadastro, "_lojas_atuais", lojas.__getitem__)
    monkeypatch.setattr(cadastro._store, "get_tenant_path", pasta_tenant)

    def autorizar(request: Request, authorization: str = Header(default="")):
        sessao = sessoes.get(authorization.removeprefix("Bearer "))
        if not sessao:
            raise HTTPException(401)
        cliente, permissoes = sessao
        regra = admin._permissao_exigida_por_rota(request.url.path, request.method)
        if not admin._permissoes_autorizam_rota(permissoes, regra):
            raise HTTPException(403)
        return cliente

    def abrir():
        app = FastAPI()
        app.include_router(create_comentarios_sku_router(autorizar, pasta_tenant))
        return TestClient(app)

    def token(nome):
        return {"Authorization": "Bearer " + nome}

    loja_a = {"store_id": "a"}
    nota = {"sku": " 1 ", "comentario": "Revisar estoque\nPróximo pedido"}
    with abrir() as api:
        assert api.get(endpoint).status_code == 401
        assert api.get(endpoint, headers=token("cadastro-a")).status_code == 403
        assert api.put(endpoint, headers=token("cadastro-a"), json=nota).status_code == 403
        assert api.get(endpoint, headers=token("medias-a")).json() == {"comentarios": {}}
        assert not (tmp_path / "cliente-a" / "comentarios_sku.db").exists()

        salvo = api.put(endpoint, params=loja_a, headers=token("medias-a"), json=nota)
        assert salvo.status_code == 200
        assert salvo.json() == {"sku": "001", "comentario": nota["comentario"]}
        assert salvo.headers["cache-control"] == "no-store"
        legado = api.put(endpoint, params=loja_a, headers=token("vendas-a"),
                         json={"sku": "04/Jan", "comentario": "Produto de data"})
        assert legado.json()["sku"] == "4-1"
        assert api.put(endpoint, params={"store_id": "outra"}, headers=token("medias-a"), json=nota).status_code == 409
        assert api.put(endpoint, headers=token("medias-a"), json={**nota, "cliente": "cliente-b"}).status_code == 422
        assert api.put(endpoint, headers=token("medias-a"), json={**nota, "sku": " "}).status_code == 422

    # Nova aplicação e novas conexões: a observação continua na loja correta.
    with abrir() as api:
        vendas = token("vendas-a")
        assert api.get(endpoint, params={"loja": "Loja A"}, headers=vendas).json() == {
            "comentarios": {"001": nota["comentario"], "4-1": "Produto de data"},
        }
        assert api.get(endpoint, params=loja_a, headers=token("vendas-b")).json() == {"comentarios": {}}
        assert api.get(endpoint, params={"store_id": "b"}, headers=vendas).json() == {"comentarios": {}}
        assert api.get(endpoint, headers=vendas).json() == {"comentarios": {}}

        editada = {"sku": "001", "comentario": "Atualizado"}
        assert api.put(endpoint, params=loja_a, headers=vendas, json=editada).json() == editada
        assert api.put(endpoint, headers=vendas, json=editada).json() == editada
        assert api.put(endpoint, params=loja_a, headers=vendas, json={**editada, "comentario": ""}).status_code == 200
        assert api.get(endpoint, params=loja_a, headers=vendas).json() == {"comentarios": {"4-1": "Produto de data"}}
        assert api.get(endpoint, headers=vendas).json() == {"comentarios": {"001": "Atualizado"}}
