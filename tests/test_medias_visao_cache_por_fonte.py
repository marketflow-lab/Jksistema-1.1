"""Exercise the actual view and its consistency fence with synthetic sources."""

import asyncio
from collections import Counter

import pytest

from backend.services import cadastro_compatibilidade, medias_compras_cache, medias_compras_visao as visao
from backend.services.bling_vendas import _sql_filtro_loja_vendas
from backend.services.cadastro_lojas_produtos import _ler_csv_capturado
from backend.services.medias_compras_leitura import capturar_bytes
from backend.services.promocoes_core_parsing import normalizar_texto


@pytest.mark.parametrize("alterar_titulo", [False, True], ids=["saldo", "saldo_e_catalogo"])
def test_estoque_alterado_durante_calculo_reaproveita_apenas_fontes_estaveis(
    tmp_path, monkeypatch, alterar_titulo,
):
    stock = tmp_path / "produtos_compilado.csv"
    calls = Counter()

    def publicar_estoque(saldo, titulo):
        temp = tmp_path / "compilado-pendente.csv"
        temp.write_text(
            f"sku,loja_sync,saldo_loja,titulo_anuncio\nA1,Loja A,{saldo},{titulo}\n",
            encoding="utf-8",
        )
        temp.replace(stock)

    publicar_estoque(3, "Título original")

    def estoque(*args):
        calls["estoque"] += 1
        return estoque_original(*args)

    def transito(*args):
        calls["transito"] += 1
        return {"A1": {"total": 2, "listas": []}}

    def catalogo(_client, _legado, store):
        calls["catalogo"] += 1
        produtos, _ = _ler_csv_capturado(capturar_bytes(stock))
        if calls["catalogo"] == 1:
            publicar_estoque(8, "Título novo" if alterar_titulo else "Título original")
        return {"produtos": produtos, "store_id": store, "scope": "store"}

    def fotos(*args):
        calls["fotos"] += 1
        return {"exact:A1": "cadastro_fotos/A1.png"}

    estoque_original = visao._carregar_estoque_visao
    monkeypatch.setattr(visao, "get_tenant_path", lambda _client: str(tmp_path))
    monkeypatch.setattr(visao, "_resolver_escopo_loja_medias", lambda *_args: {"loja": "Loja A", "store_id": "store-a"})
    monkeypatch.setattr(visao, "_listar_bancos_vendas_tenant", lambda *_args: [], raising=False)
    monkeypatch.setattr(visao, "_sql_filtro_loja_vendas", _sql_filtro_loja_vendas, raising=False)
    monkeypatch.setattr(visao, "normalizar_texto", normalizar_texto, raising=False)
    monkeypatch.setattr(visao, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(visao, "ARQUIVO_DB_PRODUTOS", str(stock), raising=False)
    monkeypatch.setattr(visao, "ARQUIVO_DB_CADASTRO_PRODUTOS", "", raising=False)
    monkeypatch.setattr(visao, "_migrar_arquivo_legado_para_tenant", lambda _client, nome, _legado: str(tmp_path / nome), raising=False)
    monkeypatch.setattr(visao, "_carregar_estoque_visao", estoque)
    monkeypatch.setattr(visao, "_mapa_estoque_em_transito_detalhado_por_sku", transito)
    monkeypatch.setattr(visao, "_cadastro_mapa_fotos_locais", fotos, raising=False)
    monkeypatch.setattr(visao, "_cadastro_resolver_foto_local", lambda mapa, sku: mapa.get(f"exact:{sku}", ""), raising=False)
    monkeypatch.setattr(cadastro_compatibilidade, "mesclar_produtos_legados_com_contexto_loja", catalogo)
    monkeypatch.setattr(medias_compras_cache, "resultados_medias", medias_compras_cache.ResultadoCache())

    resultado = asyncio.run(visao.api_medias_compras_visao(
        meses=6, loja="Loja A", store_id="store-a", client_id="tenant-a",
    ))
    [linha] = resultado["itens"]
    assert linha["saldo_atual_estoque"] == 8
    assert linha["estoque_em_transito"] == 2
    assert linha["posicao_estoque"] == 10
    assert linha["titulo_anuncio"] == ("Título novo" if alterar_titulo else "Título original")
    assert linha["foto"] == "cadastro_fotos/A1.png"
    assert calls == {"estoque": 2, "transito": 1, "catalogo": 2 if alterar_titulo else 1, "fotos": 1}

    outro_periodo = asyncio.run(visao.api_medias_compras_visao(
        meses=3, loja="Loja A", store_id="store-a", client_id="tenant-a",
    ))
    assert outro_periodo["itens"][0]["saldo_atual_estoque"] == 8
    assert outro_periodo["itens"][0]["titulo_anuncio"] == linha["titulo_anuncio"]
    assert calls == {"estoque": 2, "transito": 1, "catalogo": 2 if alterar_titulo else 1, "fotos": 1}

    # A change of the authorization source must also fence component reuse,
    # even when the store_id and the individual data files are unchanged.
    (tmp_path / "lojas_public_snapshot.json").write_text("{}", encoding="utf-8")
    apos_politica = asyncio.run(visao.api_medias_compras_visao(
        meses=3, loja="Loja A", store_id="store-a", client_id="tenant-a",
    ))
    assert apos_politica == outro_periodo
    assert calls == {"estoque": 3, "transito": 2, "catalogo": 3 if alterar_titulo else 2, "fotos": 2}
