"""Forecast integration, store isolation and temporal purchase inputs."""

from __future__ import annotations

import copy
import json
import sqlite3
from datetime import datetime

import pytest

from backend.schemas.medias_compras import ListaCompraRequest
from backend.services import cadastro_compatibilidade
from backend.services import medias_compras_common as common
from backend.services import medias_compras_historico as historico
from backend.services import medias_compras_planejamento as planejamento
from backend.services import medias_compras_sugestoes as sugestoes
from backend.services import medias_compras_visao as visao


class _Agora(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 17, 12)


def _lista(status, quantidade=100, **extras):
    return {"id": "lista-sintetica", "loja": "Loja A", "store_id": "store-a",
            "status": status, "promised_delivery_date": "2027-01-17",
            "itens": [{"SKU": "STABLE", "Quantidade": quantidade}], **extras}


def test_entradas_confirmadas_descontam_recebidos_e_isolam_identidade():
    listas = [
        _lista("Analisando orçamento", 9000),
        _lista("Em trânsito", 100, itens=[{"SKU": "STABLE", "Quantidade": 100, "received_quantity": 40}]),
        _lista("Em produção", 500, store_id="store-b"),
        _lista("Em produção", 600, unida_em_lista_id="destino"),
        _lista("Recebido", 700),
        _lista("Em produção", 30, store_id="", loja="Loja B"),
    ]
    original = copy.deepcopy(listas)
    entradas = planejamento.entradas_confirmadas_por_sku(
        listas, loja="Loja A", store_id="store-a", data_referencia="2026-11-17",
    )
    assert entradas == {"STABLE": [{"quantidade": 60, "data_prevista": "2027-01-17", "data_estimada": False}]}
    assert listas == original


def test_entradas_planejamento_incluem_analise_pendente_sem_misturar_lojas():
    listas = [
        _lista("Analisando orçamento", 100, promised_delivery_date="", itens=[
            {"SKU": "STABLE", "Quantidade": 100, "received_quantity": 40},
            {"SKU": "RECEIVED", "Quantidade": 20, "received_quantity": 20},
        ]),
        _lista("Em trânsito", 100, itens=[{"SKU": "STABLE", "Quantidade": 100, "received_quantity": 40}]),
        _lista("Analisando orçamento", 500, store_id="store-b"),
        _lista("Analisando orçamento", 600, unida_em_lista_id="destino"),
        _lista("Recebido", 700),
        _lista("Pedido cancelado", 800),
        _lista("Lista gerada", 900),
        _lista("Analisando orçamento", 30, store_id="", loja="Loja B"),
        _lista("Analisando orçamento", 10, store_id="", loja="Loja A"),
    ]
    original = copy.deepcopy(listas)
    entradas = planejamento.entradas_planejamento_por_sku(
        iter(listas), loja="Loja A", store_id="store-a", data_referencia="2026-11-17",
    )
    assert entradas == {"STABLE": [
        {"quantidade": 60, "data_prevista": "", "data_estimada": True, "confirmada": False},
        {"quantidade": 60, "data_prevista": "2027-01-17", "data_estimada": False},
        {"quantidade": 10, "data_prevista": "2027-01-17", "data_estimada": False, "confirmada": False},
    ]}
    assert listas == original


@pytest.mark.parametrize("status", sorted(common.LISTA_PEDIDO_STATUS_CONTABILIZADOS_TRANSITO))
def test_planejamento_preserva_status_da_coluna_transito(status):
    entrada = planejamento.entradas_planejamento_por_sku(
        [_lista(status)], data_referencia="2026-11-17",
    )["STABLE"][0]
    assert entrada["quantidade"] == 100
    assert entrada.get("confirmada", True) is (status != "Analisando orçamento")


@pytest.mark.parametrize("extras, data, estimada", [
    ({"promised_delivery_date": "", "eta_date": "2027-02-10"}, "2027-02-10", True),
    ({"promised_delivery_date": "", "order_date": "2026-08-31"}, "2027-02-28", True),
    ({"promised_delivery_date": "", "order_date": "2026-11-01", "lead_time_days": 180}, "2027-04-30", True),
    ({"promised_delivery_date": "2026-10-01"}, "", True),
    ({"promised_delivery_date": ""}, "", True),
    ({"promised_delivery_date": "", "order_date": "9999-12-31"}, "", True),
    ({"promised_delivery_date": "", "order_date": "2026-11-01", "lead_time_days": 1e100}, "", True),
])
def test_datas_estimadas_e_pendentes_ficam_explicitas(extras, data, estimada):
    entradas = planejamento.entradas_confirmadas_por_sku(
        [_lista("Em trânsito", **extras)], loja="Loja A", store_id="store-a", data_referencia="2026-11-17",
    )
    assert entradas["STABLE"][0]["data_prevista"] == data
    assert entradas["STABLE"][0]["data_estimada"] is estimada


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    for modulo in (common, sugestoes, visao):
        monkeypatch.setattr(modulo, "get_tenant_path", lambda client: str(tmp_path / client))
    for client in ("tenant-a", "tenant-b"):
        raiz = tmp_path / client
        raiz.mkdir()
        (raiz / "cadastro_fornecedores.json").write_text(json.dumps({
            "fornecedores": [{"id": "supplier", "nome_empresa": "Fornecedor sintético"}],
        }), encoding="utf-8")
        (raiz / "produtos_compilado.csv").write_text(
            "sku,saldo_loja,saldo,loja_sync\nUP,700,9999,Loja A\nDOWN,700,9999,Loja A\n"
            "STABLE,700,9999,Loja A\nZERO,0,9999,Loja A\nSTABLE,5000,5000,Loja B\n", encoding="utf-8",
        )
        with sqlite3.connect(raiz / "vendas_historico.db") as conn:
            conn.execute("CREATE TABLE vendas (id_unico TEXT, sku TEXT, data TEXT, produto TEXT, quantidade REAL, loja_conta TEXT)")
            for sku, quantidades in {
                "UP": [100, 100, 100, 130, 130, 130],
                "DOWN": [100, 100, 100, 70, 70, 70],
                "STABLE": [100] * 6, "ZERO": [100] * 6,
            }.items():
                for mes, quantidade in zip(range(5, 11), quantidades):
                    conn.execute("INSERT INTO vendas VALUES (?,?,?,?,?,?)", (
                        f"{sku}-{mes}", sku, f"2026-{mes:02d}-01", sku,
                        quantidade * (2 if client == "tenant-b" else 1), "Loja A",
                    ))
            conn.execute("INSERT INTO vendas VALUES ('parcial','STABLE','2026-11-10','STABLE',9999,'Loja A')")
            conn.execute("INSERT INTO vendas VALUES ('outra-loja','STABLE','2026-10-01','STABLE',9999,'Loja B')")
        common._salvar_listas_pedidos(client, [
            _lista("Em trânsito", 100, id="confirmada", itens=[{"SKU": "STABLE", "Quantidade": 100, "received_quantity": 40}]),
            _lista("Em produção", 5000, id="outra-loja", store_id="store-b"),
        ])

    def escopo(_client, loja, store, **_kwargs):
        return {"loja": loja, "store_id": store, "scope": "store"}

    for modulo in (visao, sugestoes):
        monkeypatch.setattr(modulo, "datetime", _Agora)
        monkeypatch.setattr(modulo, "_resolver_escopo_loja_medias", escopo)
        monkeypatch.setattr(modulo, "_listar_bancos_vendas_tenant", lambda client, *_a: [str(tmp_path / client / "vendas_historico.db")], raising=False)
        monkeypatch.setattr(modulo, "_sql_filtro_loja_vendas", lambda loja: (" AND loja_conta = ?", [loja]), raising=False)
        monkeypatch.setattr(modulo, "_migrar_arquivo_legado_para_tenant", lambda client, nome, _alvo: str(tmp_path / client / nome), raising=False)
        monkeypatch.setattr(modulo, "ARQUIVO_DB_PRODUTOS", "", raising=False)
        monkeypatch.setattr(modulo, "ARQUIVO_DB_CADASTRO_PRODUTOS", "", raising=False)
    monkeypatch.setattr(visao, "normalizar_texto", str, raising=False)
    monkeypatch.setattr(visao, "_cadastro_resolver_foto_local", lambda *_a: "", raising=False)
    monkeypatch.setattr(visao, "_mapa_ultima_venda_por_sku", lambda *_a, **_kw: {})
    monkeypatch.setattr(visao, "_carregar_contexto_cadastro_visao", lambda _client, escopo, **_kw:
                        visao._CadastroVisao({}, set(), escopo["store_id"], {}, True))
    monkeypatch.setattr(cadastro_compatibilidade, "mesclar_produtos_legados_com_contexto_loja", lambda *_a, **_kw:
                        {"produtos": [], "store_id": "store-a", "scope": "store", "loja_resolvida": True})
    monkeypatch.setattr(sugestoes, "_recalcular_frete_internacional_itens_lista", lambda _client, itens, **_kw: itens)
    monkeypatch.setattr(sugestoes, "_gerar_excel_lista_pedido_bytes", lambda *_a, **_kw: b"xlsx-sintetico")
    monkeypatch.setattr(sugestoes, "_salvar_bytes_cache_lista_pedido", lambda *_a, **_kw: None)
    monkeypatch.setattr(sugestoes, "TEMP_FILES_STORAGE", {})
    monkeypatch.setattr(sugestoes, "TEMP_FILES_META", {})
    monkeypatch.setattr(historico, "_PROJECOES", historico._MemoLimitado(4 * 1024 * 1024, 32))
    monkeypatch.setattr(historico, "_CONSOLIDADOS", historico._MemoLimitado(4 * 1024 * 1024, 16))
    return tmp_path


def _visao(client="tenant-a", meses=6):
    resposta = visao._api_medias_compras_visao_sync(meses=meses, loja="Loja A", store_id="store-a", client_id=client)
    return {item["sku"]: item for item in resposta["itens"]}


def test_tendencia_independe_da_janela_exibida_e_preserva_zero_real(ambiente):
    resultados = [_visao(meses=meses) for meses in (3, 6, 12)]
    for itens in resultados:
        assert itens["UP"]["tendencia_vendas"] == "alta"
        assert itens["DOWN"]["tendencia_vendas"] == "queda"
        assert itens["STABLE"]["media_mensal_prevista"] == 100
        assert itens["UP"]["compra_sugerida"] > itens["STABLE"]["compra_sugerida"] > itens["DOWN"]["compra_sugerida"]
        assert itens["ZERO"]["saldo_atual_estoque"] == 0
        assert itens["ZERO"]["risco_ruptura_antes_chegada"] is True
        assert itens["STABLE"]["transito_confirmado"] == 60
        assert itens["STABLE"]["estoque_previsto_antes_chegada"] == pytest.approx(160)
        assert itens["STABLE"]["compra_sugerida"] == 540
        assert itens["STABLE"]["estoque_previsto_apos_chegada"] == 700
    for sku in resultados[0]:
        assert len({itens[sku]["compra_sugerida"] for itens in resultados}) == 1
    assert _visao(client="tenant-b")["STABLE"]["media_mensal_prevista"] == 200


def test_geracao_e_download_usam_as_mesmas_quantidades_da_tabela(ambiente, monkeypatch):
    esperados = {sku: item["compra_sugerida"] for sku, item in _visao().items() if item["compra_sugerida"] > 0}
    req = ListaCompraRequest(opcao="media_6m", nome_lista="Pedido sintético", loja="Loja A", store_id="store-a",
                            fornecedor_id="supplier", periodo_meses=6)
    resultado = sugestoes._api_medias_compras_gerar_lista_compra_sync(req, client_id="tenant-a")
    criada = next(lista for lista in common._carregar_listas_pedidos("tenant-a") if lista["id"] == resultado["lista_id"])
    assert {item["SKU"]: item["Quantidade"] for item in criada["itens"]} == esperados

    exportados = []
    def excel(_nome, itens, **_kwargs):
        exportados.extend(itens)
        return b"xlsx-sintetico"
    monkeypatch.setattr(sugestoes, "_gerar_excel_lista_pedido_bytes", excel)
    sugestoes._api_medias_compras_gerar_lista_sugestao_sync(meses=6, loja="Loja A", store_id="store-a", client_id="tenant-a")
    assert {item["SKU"]: item["Quantidade"] for item in exportados} == esperados


def test_pedido_em_analise_abate_sugestao_na_tabela_lista_e_excel(ambiente, monkeypatch):
    raiz = ambiente / "tenant-a"
    with (raiz / "produtos_compilado.csv").open("a", encoding="utf-8") as arquivo:
        arquivo.write("79,0,0,Loja A\n")
    with sqlite3.connect(raiz / "vendas_historico.db") as conn:
        for mes, quantidade in zip(range(5, 12), [73, 16, 75, 147, 253, 182, 97]):
            conn.execute("INSERT INTO vendas VALUES (?,?,?,?,?,?)", (
                f"sintetico-79-{mes}", "79", f"2026-{mes:02d}-01", "Produto sintético", quantidade, "Loja A",
            ))
    listas = common._carregar_listas_pedidos("tenant-a")
    listas.append(_lista(
        "Analisando orçamento", id="orcamento-sintetico", promised_delivery_date="",
        itens=[{"SKU": "79", "Quantidade": 700, "received_quantity": 0}],
    ))
    common._salvar_listas_pedidos("tenant-a", listas)

    linha = _visao()["79"]
    assert linha["media_mensal_prevista"] == pytest.approx(152.2)
    assert linha["estoque_em_transito"] == 700
    assert linha["transito_confirmado"] == 0
    assert linha["transito_em_analise_quantidade"] == 700
    assert linha["pedidos_considerados_quantidade"] == 700
    assert linha["abatimento_pedidos_quantidade"] == 700
    assert linha["compra_sugerida"] == 370
    assert linha["estoque_previsto_antes_chegada"] == 0
    assert linha["reserva_antes_chegada_atendida"] is False
    assert linha["planejamento_requer_revisao"] is True
    assert "79" not in _visao(client="tenant-b")

    req = ListaCompraRequest(opcao="media_6m", nome_lista="Pedido sintético com análise", loja="Loja A",
                            store_id="store-a", fornecedor_id="supplier", periodo_meses=6)
    resultado = sugestoes._api_medias_compras_gerar_lista_compra_sync(req, client_id="tenant-a")
    criada = next(lista for lista in common._carregar_listas_pedidos("tenant-a") if lista["id"] == resultado["lista_id"])
    assert next(item["Quantidade"] for item in criada["itens"] if item["SKU"] == "79") == 370
    assert resultado["planejamento_requer_revisao"] is True

    exportados = []
    def excel(_nome, itens, **_kwargs):
        exportados.extend(itens)
        return b"xlsx-sintetico"
    monkeypatch.setattr(sugestoes, "_gerar_excel_lista_pedido_bytes", excel)
    sugestoes._api_medias_compras_gerar_lista_sugestao_sync(
        meses=6, loja="Loja A", store_id="store-a", client_id="tenant-a",
    )
    assert next(item["Quantidade"] for item in exportados if item["SKU"] == "79") == 370


def test_primeira_venda_antiga_e_mes_fechado_extra_compartilham_leitura(ambiente):
    raiz = ambiente / "tenant-a"
    with sqlite3.connect(raiz / "vendas_historico.db") as conn:
        conn.execute("INSERT INTO vendas VALUES ('zerada','STABLE','2000-01-01','STABLE',0,'Loja A')")
        conn.execute("INSERT INTO vendas VALUES ('negativa','STABLE','2001-01-01','STABLE',-1,'Loja A')")
        conn.execute("INSERT INTO vendas VALUES ('invalida','STABLE','2002-01-01','STABLE','inválida','Loja A')")
        conn.execute("INSERT INTO vendas VALUES ('antiga','STABLE','2020-01-01','STABLE',1,'Loja A')")
        conn.execute("INSERT INTO vendas VALUES ('extra','STABLE','2025-11-01','STABLE',3,'Loja A')")
    saida = {}
    historico.ler_historico_vendas(
        client_id="tenant-a", loja="Loja A", store_id="store-a", db_paths=[str(raiz / "vendas_historico.db")],
        meses=3, fim_periodo="2026-11-17", filtro_sql=" AND loja_conta = ?", filtro_params=["Loja A"],
        normalizar_sku=str, titulo_valido=str, planejamento=saida,
    )
    assert saida["STABLE"]["primeira_venda"] == "2020-01-01"
    assert saida["STABLE"]["vendas_mensais"]["2025-11"] == 3


def test_estoque_por_identidade_nao_mistura_lojas_homonimas_ou_duplicata_legada(ambiente):
    (ambiente / "tenant-a" / "produtos_compilado.csv").write_text(
        "sku,saldo_loja,saldo,loja_sync,store_id\nSTABLE,0,9999,Loja A,store-a\n"
        "STABLE,5000,5000,Loja A,store-b\nSTABLE,8000,8000,Loja A,\nLEGACY,3,3,Loja A,\n",
        encoding="utf-8",
    )
    estoque = visao._carregar_estoque_visao("tenant-a", "Loja A", "store-a")
    assert estoque.completo is True
    assert estoque.saldo == {"STABLE": 0, "LEGACY": 3}
    linha = _visao()["STABLE"]
    assert linha["saldo_atual_estoque"] == 0
    req = ListaCompraRequest(opcao="media_6m", nome_lista="Homônimos sintéticos", loja="Loja A", store_id="store-a",
                            fornecedor_id="supplier", periodo_meses=6)
    resultado = sugestoes._api_medias_compras_gerar_lista_compra_sync(req, client_id="tenant-a")
    criada = next(lista for lista in common._carregar_listas_pedidos("tenant-a") if lista["id"] == resultado["lista_id"])
    assert next(item["Quantidade"] for item in criada["itens"] if item["SKU"] == "STABLE") == linha["compra_sugerida"]


def test_estoque_ausente_e_fonte_incompleta_exigem_revisao(ambiente):
    caminho = ambiente / "tenant-a" / "produtos_compilado.csv"
    caminho.unlink()
    assert visao._carregar_estoque_visao("tenant-a", "Loja A", "store-a").completo is False
    itens = _visao()
    assert itens["STABLE"]["planejamento_requer_revisao"] is True
    assert any("Estoque indisponível" in aviso for aviso in itens["STABLE"]["avisos_planejamento"])
    caminho.write_text("sku,loja_sync\nSTABLE,Loja A\n", encoding="utf-8")
    assert visao._carregar_estoque_visao("tenant-a", "Loja A", "store-a").completo is False
    caminho.write_text("sku,saldo_loja,loja_sync\n", encoding="utf-8")
    assert visao._carregar_estoque_visao("tenant-a", "Loja A", "store-a").completo is False
    assert _visao()["STABLE"]["planejamento_requer_revisao"] is True
