import asyncio

import pytest
from fastapi import HTTPException

from backend.schemas.medias_compras import ListaCompraRequest
from backend.services import medias_compras_sugestoes as sugestoes


def test_lista_compra_request_accepts_quantidades_sugeridas():
    req = ListaCompraRequest(opcao="media_6m", quantidades_sugeridas={"001": 7, "002": 0})

    assert req.quantidades_sugeridas == {"001": 7, "002": 0}


def test_normaliza_quantidades_sugeridas_e_rejeita_valores_invalidos():
    assert sugestoes._normalizar_quantidades_sugeridas('{"001": 7, "sku-2": 0}') == {
        "001": 7,
        "SKU-2": 0,
    }

    for valor in ('{"001": -1}', '{"001": 1.5}', '{"001": true}', '[]', 'json-invalido'):
        with pytest.raises(HTTPException) as exc_info:
            sugestoes._normalizar_quantidades_sugeridas(valor)
        assert exc_info.value.status_code == 400


def test_resolve_quantidade_manual_inclusive_zero():
    aplicadas = set()
    ajustes = {"001": 9, "002": 0}

    assert sugestoes._resolver_quantidade_sugerida("001", 4, ajustes, aplicadas) == 9
    assert sugestoes._resolver_quantidade_sugerida("002", 4, ajustes, aplicadas) == 0
    assert sugestoes._resolver_quantidade_sugerida("003", 4, ajustes, aplicadas) == 4
    assert aplicadas == {"001", "002"}


def test_lista_compra_salva_quantidades_editadas(monkeypatch, tmp_path):
    estoque_csv = tmp_path / "produtos_compilado.csv"
    estoque_csv.write_text(
        "sku,saldo_loja,loja_sync\n001,5,JK Pecas\n002,5,JK Pecas\n",
        encoding="utf-8",
    )
    listas_salvas = []

    monkeypatch.setattr(
        sugestoes,
        "_resolver_escopo_loja_medias",
        lambda *_args, **_kwargs: {
            "loja": "JK Pecas",
            "store_id": "store-jk",
            "scope": "store",
        },
    )
    monkeypatch.setattr(sugestoes, "ARQUIVO_DB_PRODUTOS", "produtos_compilado.csv", raising=False)
    monkeypatch.setattr(sugestoes, "ARQUIVO_DB_CADASTRO_PRODUTOS", "cadastro_produtos.csv", raising=False)
    monkeypatch.setattr(sugestoes, "_listar_bancos_vendas_tenant", lambda *_args: [], raising=False)
    monkeypatch.setattr(
        sugestoes,
        "_migrar_arquivo_legado_para_tenant",
        lambda _client_id, legado, _destino: str(estoque_csv) if legado == "produtos_compilado.csv" else "",
        raising=False,
    )
    monkeypatch.setattr(sugestoes, "_mapa_estoque_em_transito_por_sku", lambda *_args: {}, raising=False)
    monkeypatch.setattr(
        sugestoes,
        "calcular_reposicao_periodo",
        lambda **_kwargs: {"compra_sugerida": 4},
    )
    monkeypatch.setattr(sugestoes, "_carregar_listas_pedidos", lambda _client_id: [], raising=False)
    monkeypatch.setattr(sugestoes, "_recalcular_frete_internacional_itens_lista", lambda _client_id, itens, **_kwargs: itens)
    monkeypatch.setattr(sugestoes, "_gerar_excel_lista_pedido_bytes", lambda *_args, **_kwargs: b"xlsx")
    monkeypatch.setattr(sugestoes, "_salvar_listas_pedidos", lambda _client_id, listas: listas_salvas.extend(listas), raising=False)
    monkeypatch.setattr(sugestoes, "_salvar_bytes_cache_lista_pedido", lambda *_args: None, raising=False)
    monkeypatch.setattr(sugestoes, "TEMP_FILES_STORAGE", {})
    monkeypatch.setattr(sugestoes, "TEMP_FILES_META", {})

    resultado = asyncio.run(
        sugestoes.api_medias_compras_gerar_lista_compra(
            ListaCompraRequest(
                opcao="media_6m",
                nome_lista="Pedido editado",
                loja="JK Pecas",
                store_id="store-jk",
                periodo_meses=6,
                quantidades_sugeridas={"001": 7, "002": 0},
            ),
            client_id="000002",
        )
    )

    assert [(item["SKU"], item["Quantidade"]) for item in listas_salvas[0]["itens"]] == [("001", 7)]
    assert resultado["total_itens"] == 1
    assert resultado["total_quantidades_ajustadas"] == 2


def test_lista_sugestao_aplica_edicoes_antes_de_gerar_excel(monkeypatch):
    async def fake_visao(*, meses, loja, store_id, client_id):
        assert meses == 6
        assert loja == "JK Pecas"
        assert store_id == "store-jk"
        assert client_id == "000002"
        return {
            "itens": [
                {"sku": "001", "compra_sugerida": 5, "titulo_anuncio": "Produto 1"},
                {"sku": "002", "compra_sugerida": 4, "titulo_anuncio": "Produto 2"},
                {"sku": "003", "compra_sugerida": 0, "titulo_anuncio": "Produto 3"},
            ]
        }

    itens_gerados = []

    def fake_gerar_excel(_nome, itens, *, client_id, loja=""):
        assert client_id == "000002"
        assert loja == "store-jk"
        itens_gerados.extend(itens)
        return b"xlsx"

    monkeypatch.setattr(sugestoes, "api_medias_compras_visao", fake_visao)
    monkeypatch.setattr(
        sugestoes,
        "_resolver_escopo_loja_medias",
        lambda *_args, **_kwargs: {
            "loja": "JK Pecas",
            "store_id": "store-jk",
            "scope": "store",
        },
    )
    monkeypatch.setattr(sugestoes, "_resolver_foto_cadastro_sku", lambda *_args: "")
    monkeypatch.setattr(sugestoes, "_normalizar_item_lista_pedido", lambda item: item)
    monkeypatch.setattr(sugestoes, "_recalcular_frete_internacional_itens_lista", lambda _client_id, itens, **_kwargs: itens)
    monkeypatch.setattr(sugestoes, "_gerar_excel_lista_pedido_bytes", fake_gerar_excel)
    monkeypatch.setattr(sugestoes, "TEMP_FILES_STORAGE", {})
    monkeypatch.setattr(sugestoes, "TEMP_FILES_META", {})

    resultado = asyncio.run(
        sugestoes.api_medias_compras_gerar_lista_sugestao(
            meses=6,
            loja="JK Pecas",
            store_id="store-jk",
            quantidades_sugeridas='{"001": 7, "002": 0, "003": 3}',
            client_id="000002",
        )
    )

    assert [(item["SKU"], item["Quantidade"]) for item in itens_gerados] == [
        ("001", 7),
        ("003", 3),
    ]
    assert resultado["total_itens"] == 2
    assert resultado["total_quantidades_ajustadas"] == 3
