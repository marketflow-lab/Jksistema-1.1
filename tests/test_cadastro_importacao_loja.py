import asyncio
import csv
import io
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from backend.services import (
    cadastro_custos,
    cadastro_fotos,
    cadastro_importacao,
    cadastro_lojas_produtos,
    integracoes,
)


def _configurar(monkeypatch, tmp_path):
    raiz = tmp_path / "info"
    lojas = {
        "cliente-a": [
            {"store_id": "store-a", "nome": "Loja A"},
            {"store_id": "store-b", "nome": "Loja B"},
        ]
    }

    def tenant_path(client_id):
        pasta = raiz / str(client_id)
        pasta.mkdir(parents=True, exist_ok=True)
        return str(pasta)

    monkeypatch.setattr(cadastro_importacao, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(raiz), raising=False)
    monkeypatch.setattr(
        integracoes,
        "carregar_lojas",
        lambda client_id: lojas.get(str(client_id), []),
    )
    return raiz, lojas


def _upload(conteudo: str, nome="importacao.csv"):
    return UploadFile(filename=nome, file=io.BytesIO(conteudo.encode("utf-8")))


def _ler_csv(caminho: Path):
    with caminho.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_importacao_geral_isola_mesmo_sku_e_ignora_metadata(monkeypatch, tmp_path):
    _configurar(monkeypatch, tmp_path)

    resposta_a = asyncio.run(
        cadastro_importacao.importar_colunas_cadastro_loja(
            "store-a",
            _upload(
                "sku;nome;categoria;custo;store_id;row_version\n"
                "001;Primeira versao;Ferramentas;10;store-b;999\n"
                "001;Produto A;Casa;11;store-b;999\n"
            ),
            "geral",
            "cliente-a",
        )
    )
    resposta_b = asyncio.run(
        cadastro_importacao.importar_colunas_cadastro_loja(
            "store-b",
            _upload("sku;nome;categoria\n001;Produto B;Auto\n"),
            "geral",
            "cliente-a",
        )
    )

    produto_a = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-a", "001"
    )
    produto_b = cadastro_lojas_produtos._obter_produto_loja_sync(
        "cliente-a", "store-b", "001"
    )
    assert produto_a["nome"] == "Produto A"
    assert produto_a["categoria"] == "Casa"
    assert produto_a["store_id"] == "store-a"
    assert produto_a["row_version"] == 1
    assert "custo" not in produto_a or produto_a["custo"] == ""
    assert produto_b["nome"] == "Produto B"
    assert produto_b["store_id"] == "store-b"
    assert resposta_a["skus_incluidos"] == 1
    assert resposta_b["skus_incluidos"] == 1
    assert resposta_a["colunas_importadas"] == ["nome", "categoria"]


def test_importacao_custos_usa_store_id_e_sobrevive_a_rename(monkeypatch, tmp_path):
    raiz, lojas = _configurar(monkeypatch, tmp_path)

    asyncio.run(
        cadastro_importacao.importar_colunas_cadastro_loja(
            "store-a",
            _upload("sku;produto;custo;preco;imposto\n001;Produto;10;20;1\n"),
            "custos",
            "cliente-a",
        )
    )
    asyncio.run(
        cadastro_importacao.importar_colunas_cadastro_loja(
            "store-b",
            _upload("sku;produto;custo;preco;imposto\n001;Produto;90;190;9\n"),
            "custos",
            "cliente-a",
        )
    )
    lojas["cliente-a"][0]["nome"] = "Loja A Renomeada"

    linhas = _ler_csv(raiz / "cliente-a" / "cadastro_custos_lojas.csv")
    por_loja = {linha["store_id"]: linha for linha in linhas}
    assert por_loja["store-a"]["custo"] == "10"
    assert por_loja["store-b"]["custo"] == "90"
    produto_a = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a", "store-a", {"sku": "001", "nome": "Produto A"}
    )
    assert produto_a["custo"] == "10"
    assert produto_a["loja_sync"] == "Loja A Renomeada"


def test_importacao_nao_altera_cadastro_legado(monkeypatch, tmp_path):
    raiz, _ = _configurar(monkeypatch, tmp_path)
    legado = raiz / "cliente-a" / "cadastro_produtos.csv"
    legado.parent.mkdir(parents=True, exist_ok=True)
    legado.write_text("sku,nome\n001,Legado\n", encoding="utf-8")
    antes = legado.read_bytes()

    asyncio.run(
        cadastro_importacao.importar_colunas_cadastro_loja(
            "store-a",
            _upload("sku;nome\n001;Novo por loja\n"),
            "geral",
            "cliente-a",
        )
    )

    assert legado.read_bytes() == antes


@pytest.mark.parametrize("modo", ["geral", "custos"])
def test_importacao_revalida_loja_renomeada_durante_upload(
    monkeypatch,
    tmp_path,
    modo,
):
    raiz, lojas = _configurar(monkeypatch, tmp_path)

    class UploadComRename:
        filename = "importacao.csv"

        async def read(self):
            lojas["cliente-a"][0]["nome"] = "Loja A Renomeada Durante Upload"
            if modo == "custos":
                return b"sku;custo;preco\n001;10;20\n"
            return b"sku;nome\n001;Produto\n"

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_importacao.importar_colunas_cadastro_loja(
                "store-a",
                UploadComRename(),
                modo,
                "cliente-a",
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_config_changed"
    assert not (raiz / "cliente-a" / "cadastro_produtos_lojas.csv").exists()
    assert not (raiz / "cliente-a" / "cadastro_custos_lojas.csv").exists()


def test_importacao_csv_malformado_falha_sem_omitir_linha(monkeypatch, tmp_path):
    raiz, _ = _configurar(monkeypatch, tmp_path)
    destino = raiz / "cliente-a" / "cadastro_produtos_lojas.csv"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            cadastro_importacao.importar_colunas_cadastro_loja(
                "store-a",
                _upload(
                    "sku;nome\n"
                    "001;Produto A\n"
                    "002;Produto B;campo inesperado\n"
                    "003;Produto C\n"
                ),
                "geral",
                "cliente-a",
            )
        )

    assert exc.value.status_code == 400
    assert not destino.exists()


@pytest.mark.parametrize(
    ("store_id", "modo", "status"),
    [("store-inexistente", "geral", 404), ("store-a", "desconhecido", 400)],
)
def test_importacao_rejeita_escopo_ou_modo_invalido(
    monkeypatch, tmp_path, store_id, modo, status
):
    _configurar(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            cadastro_importacao.importar_colunas_cadastro_loja(
                store_id,
                _upload("sku;nome\n001;Produto\n"),
                modo,
                "cliente-a",
            )
        )

    assert exc.value.status_code == status


def test_router_expoe_importacao_por_loja():
    from backend.routers.cadastro import create_cadastro_router

    rotas = {
        (route.path, method)
        for route in create_cadastro_router().routes
        for method in (route.methods or set())
    }
    assert ("/api/cadastro/lojas/{store_id}/importar-colunas", "POST") in rotas


def _preparar_cadastro_legado_para_custos(raiz: Path) -> Path:
    legado = raiz / "cliente-a" / "cadastro_produtos.csv"
    legado.parent.mkdir(parents=True, exist_ok=True)
    legado.write_text("sku,nome\n001,Produto legado\n", encoding="utf-8")
    return legado


def test_importacao_global_custos_materializa_store_id_atual(monkeypatch, tmp_path):
    raiz, _lojas = _configurar(monkeypatch, tmp_path)
    _preparar_cadastro_legado_para_custos(raiz)

    resposta = asyncio.run(
        cadastro_importacao.importar_colunas_cadastro_por_sku(
            _upload("sku;custo;preco\n001;10;20\n"),
            "Loja A",
            "custos",
            "cliente-a",
        )
    )

    linhas = _ler_csv(raiz / "cliente-a" / "cadastro_custos_lojas.csv")
    assert resposta["custos_loja"] == "Loja A"
    assert len(linhas) == 1
    assert linhas[0]["store_id"] == "store-a"
    assert linhas[0]["loja_sync"] == "Loja A"
    assert linhas[0]["sku"] == "001"
    assert linhas[0]["custo"] == "10"
    assert linhas[0]["preco"] == "20"


def test_importacao_global_custos_rejeita_nome_homonimo(monkeypatch, tmp_path):
    raiz, lojas = _configurar(monkeypatch, tmp_path)
    legado = _preparar_cadastro_legado_para_custos(raiz)
    lojas["cliente-a"][1]["nome"] = "Loja A"
    antes = legado.read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_importacao.importar_colunas_cadastro_por_sku(
                _upload("sku;custo\n001;10\n"),
                "Loja A",
                "custos",
                "cliente-a",
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_name_ambiguous"
    assert legado.read_bytes() == antes
    assert not (raiz / "cliente-a" / "cadastro_custos_lojas.csv").exists()


def test_importacao_global_custos_nao_transfere_nome_de_loja_excluida(
    monkeypatch,
    tmp_path,
):
    raiz, lojas = _configurar(monkeypatch, tmp_path)
    legado = _preparar_cadastro_legado_para_custos(raiz)
    lojas["cliente-a"] = [
        loja for loja in lojas["cliente-a"] if loja["store_id"] != "store-a"
    ]
    antes = legado.read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_importacao.importar_colunas_cadastro_por_sku(
                _upload("sku;custo\n001;10\n"),
                "Loja A",
                "custos",
                "cliente-a",
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "store_not_found"
    assert legado.read_bytes() == antes
    assert not (raiz / "cliente-a" / "cadastro_custos_lojas.csv").exists()

    lojas["cliente-a"].append({"store_id": "store-nova", "nome": "Loja A"})
    produto = cadastro_lojas_produtos.salvar_produto_loja(
        "cliente-a",
        "store-nova",
        {"sku": "001", "nome": "Produto da nova identidade"},
    )
    assert str(produto.get("custo") or "") == ""
