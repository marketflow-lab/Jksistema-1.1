import asyncio
import copy
import json

import pytest
from fastapi import HTTPException

from backend.schemas.medias_compras import ListaPedidoAddSkuRequest
from backend.services import cadastro_compatibilidade, cadastro_listagem, cadastro_produtos
from backend.services import medias_compras_common as common
from backend.services import medias_compras_listas as service


CLIENT_ID = "tenant-test"
STORE_ID = "store-a"
LISTA_ID = "lista-1"


@pytest.fixture
def pedido(monkeypatch, tmp_path):
    arquivo = tmp_path / "listas_pedidos.json"
    estado = {"cadastro": [], "arquivo": arquivo}

    def caminho(client_id):
        assert client_id == CLIENT_ID
        return str(arquivo)

    def obter_produto(sku, client_id):
        assert client_id == CLIENT_ID
        sku_publico = common._normalizar_sku_mes(sku)
        for produto in estado["cadastro"]:
            if produto["sku"].casefold() == sku_publico.casefold():
                return {"produto": copy.deepcopy(produto)}
        raise HTTPException(status_code=404, detail="SKU nao encontrado")

    def listar_produtos(client_id):
        assert client_id == CLIENT_ID
        return copy.deepcopy(estado["cadastro"])

    def contexto_loja(client_id, produtos_legados, loja_referencia, *, skus=None):
        assert client_id == CLIENT_ID
        assert loja_referencia == STORE_ID
        return {
            "store_id": STORE_ID,
            "produtos": [
                copy.deepcopy(produto)
                for produto in estado["cadastro"]
                if produto["store_id"] == loja_referencia
            ],
        }

    def resolver_foto(client_id, sku, foto_ref, store_id):
        assert client_id == CLIENT_ID
        assert store_id == STORE_ID
        return foto_ref

    monkeypatch.setattr(common, "_arquivo_listas_pedidos", caminho)
    monkeypatch.setattr(service, "_carregar_listas_pedidos", common._carregar_listas_pedidos)
    monkeypatch.setattr(service, "_salvar_listas_pedidos", common._salvar_listas_pedidos)
    monkeypatch.setattr(service, "_limpar_cache_lista_pedido", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        service,
        "_recalcular_frete_internacional_itens_lista",
        lambda _client_id, itens, **_kwargs: itens,
    )
    monkeypatch.setattr(service, "_resumo_lista_pedido", lambda lista: {"id": lista["id"]})
    monkeypatch.setattr(service, "_resolver_foto_cadastro_sku", resolver_foto)
    monkeypatch.setattr(cadastro_produtos, "_obter_produto_cadastro_sync", obter_produto)
    monkeypatch.setattr(cadastro_listagem, "_listar_produtos_cadastro_sync", listar_produtos)
    monkeypatch.setattr(
        cadastro_compatibilidade,
        "mesclar_produtos_legados_com_contexto_loja",
        contexto_loja,
    )
    common._salvar_listas_pedidos(
        CLIENT_ID,
        [{"id": LISTA_ID, "store_id": STORE_ID, "loja": "Loja teste", "itens": []}],
    )
    return estado


def _produto(sku, store_id=STORE_ID):
    return {
        "sku": sku,
        "store_id": store_id,
        "nome": "Produto do cadastro",
        "foto": "produto.png",
        "oem": "OEM-TESTE",
    }


def _adicionar(sku, quantidade=30, valor_unitario=6, lista_id=LISTA_ID):
    return asyncio.run(
        service.api_medias_compras_lista_pedido_adicionar_sku(
            lista_id,
            ListaPedidoAddSkuRequest(
                sku=sku,
                quantidade=quantidade,
                valor_unitario=valor_unitario,
            ),
            client_id=CLIENT_ID,
        )
    )


def _itens_salvos(pedido):
    return json.loads(pedido["arquivo"].read_text(encoding="utf-8"))[0]["itens"]


@pytest.mark.parametrize("sku", ["493-1", "LETRAS", "AB12", "ABC-01", "001-A", "000493", "493"])
def test_inclusao_preserva_sku_livre_e_dados_do_cadastro(pedido, sku):
    pedido["cadastro"] = [_produto(sku)]

    resultado = _adicionar(sku)

    assert resultado["success"] is True
    assert resultado["acao"] == "adicionado"
    assert resultado["sku"] == sku
    itens = _itens_salvos(pedido)
    assert len(itens) == 1
    assert itens[0]["SKU"] == sku
    assert itens[0]["Quantidade"] == 30
    assert itens[0]["Valor unidade"] == 6
    assert itens[0]["Valor total"] == 180
    assert itens[0]["Foto"] == "produto.png"
    assert itens[0]["OEM"] == "OEM-TESTE"
    assert resultado["lista"]["itens"] == itens


@pytest.mark.parametrize("entrada, cadastrado", [("abc-01", "ABC-01"), ("ABC-01", "abc-01")])
def test_busca_sem_diferenciar_caixa_preserva_sku_cadastrado(pedido, entrada, cadastrado):
    pedido["cadastro"] = [_produto(cadastrado)]

    resultado = _adicionar(f"  {entrada}  ")

    assert resultado["sku"] == "ABC-01"
    assert _itens_salvos(pedido)[0]["SKU"] == "ABC-01"


@pytest.mark.parametrize("existente, cadastrado", [("abc-01", "ABC-01"), ("ABC-01", "abc-01")])
def test_reinclusao_atualiza_item_sem_duplicar_e_preserva_metadados(pedido, existente, cadastrado):
    pedido["cadastro"] = [_produto(cadastrado)]
    listas = common._carregar_listas_pedidos(CLIENT_ID)
    listas[0]["itens"] = [{"SKU": existente, "Quantidade": 1, "observacao": "Preservar"}]
    common._salvar_listas_pedidos(CLIENT_ID, listas)

    resultado = _adicionar(existente, quantidade=3, valor_unitario=2.5)

    assert resultado["acao"] == "atualizado"
    itens = _itens_salvos(pedido)
    assert len(itens) == 1
    assert itens[0]["SKU"] == "ABC-01"
    assert itens[0]["Quantidade"] == 3
    assert itens[0]["Valor unidade"] == 2.5
    assert itens[0]["Valor total"] == 7.5
    assert itens[0]["observacao"] == "Preservar"


@pytest.mark.parametrize("sku_bruto, sku_publico", [("1", "001"), ("001-JAN", "1-1")])
def test_inclusao_prioriza_identidade_bruta_da_cubagem(pedido, sku_bruto, sku_publico):
    pedido["cadastro"] = [{**_produto(sku_publico), "cubagem_sku": sku_bruto}]

    resultado = _adicionar(sku_bruto)

    assert resultado["acao"] == "adicionado"
    assert resultado["sku"] == sku_bruto
    assert _itens_salvos(pedido)[0]["SKU"] == sku_bruto


@pytest.mark.parametrize("sku_bruto, sku_publico", [("1", "001"), ("001-JAN", "1-1")])
def test_sku_publico_nao_substitui_identidade_bruta_da_cubagem(pedido, sku_bruto, sku_publico):
    pedido["cadastro"] = [{**_produto(sku_publico), "cubagem_sku": sku_bruto}]
    antes = pedido["arquivo"].read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        _adicionar(sku_publico)

    assert exc_info.value.status_code == 404
    assert pedido["arquivo"].read_bytes() == antes


def test_hifen_distingue_skus_na_inclusao_e_atualizacao(pedido):
    pedido["cadastro"] = [_produto("493-1"), _produto("4931")]

    _adicionar("493-1", quantidade=1)
    _adicionar("4931", quantidade=2)
    resultado = _adicionar("493-1", quantidade=3)

    assert resultado["acao"] == "atualizado"
    assert [(item["SKU"], item["Quantidade"]) for item in _itens_salvos(pedido)] == [
        ("493-1", 3),
        ("4931", 2),
    ]


@pytest.mark.parametrize("entrada, cadastrado", [("493-1", "4931"), ("4931", "493-1")])
def test_fallback_de_busca_nao_confunde_pontuacao_do_sku(pedido, entrada, cadastrado):
    pedido["cadastro"] = [_produto(cadastrado)]
    antes = pedido["arquivo"].read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        _adicionar(entrada)

    assert exc_info.value.status_code == 404
    assert pedido["arquivo"].read_bytes() == antes


def test_sku_inexistente_nao_altera_lista(pedido):
    pedido["cadastro"] = [_produto("ABC-01")]
    antes = pedido["arquivo"].read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        _adicionar("DESCONHECIDO")

    assert exc_info.value.status_code == 404
    assert pedido["arquivo"].read_bytes() == antes


def test_sku_de_outra_loja_nao_altera_lista(pedido):
    pedido["cadastro"] = [_produto("ABC-01", store_id="store-b")]
    antes = pedido["arquivo"].read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        _adicionar("abc-01")

    assert exc_info.value.status_code == 404
    assert "cadastro desta loja" in exc_info.value.detail
    assert pedido["arquivo"].read_bytes() == antes


@pytest.mark.parametrize("quantidade, valor_unitario", [(0, 6), (-1, 6), (30, 0), (30, -1)])
def test_quantidade_e_valor_invalidos_nao_alteram_lista(pedido, quantidade, valor_unitario):
    pedido["cadastro"] = [_produto("ABC-01")]
    antes = pedido["arquivo"].read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        _adicionar("ABC-01", quantidade=quantidade, valor_unitario=valor_unitario)

    assert exc_info.value.status_code == 400
    assert pedido["arquivo"].read_bytes() == antes


@pytest.mark.parametrize("sku", ["", "   "])
def test_sku_vazio_nao_altera_lista(pedido, sku):
    antes = pedido["arquivo"].read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        _adicionar(sku)

    assert exc_info.value.status_code == 400
    assert pedido["arquivo"].read_bytes() == antes
