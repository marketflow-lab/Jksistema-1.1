import builtins
import json
import threading

import backend_api  # noqa: F401 - configura os peers modulares usados nos testes
from backend.schemas.favoritos import FavoritosEfetivarPromocaoRequest
from backend.services import favoritos_extract
from backend.services import favoritos_ml
from backend.services.promocoes_core_analise import _parse_float_flex


def _req_contingencia(preco_ranking, preco_minimo_margem, preco_promocional=60.05, preco_competitivo=37.89):
    return FavoritosEfetivarPromocaoRequest(
        loja="JK Pecas",
        item_id="MLB2894843116",
        sku="001",
        preco_anuncio=79.32,
        preco_promocional=preco_promocional,
        preco_competitivo=preco_competitivo,
        campanha_id="campanha-teste",
        simulacao={
            "precoRanking": preco_ranking,
            "precoMinimoMargem": preco_minimo_margem,
        },
    )


def _configurar_dependencias(monkeypatch):
    monkeypatch.setattr(favoritos_ml, "_parse_float_flex", _parse_float_flex, raising=False)
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_preco_ranking_simulado",
        lambda req: _parse_float_flex((req.simulacao or {}).get("precoRanking")),
        raising=False,
    )


def test_contingencia_usa_preco_minimo_margem_quando_nao_da_para_competir(monkeypatch):
    _configurar_dependencias(monkeypatch)
    req = _req_contingencia(preco_ranking=37.90, preco_minimo_margem=60.05)

    preco = favoritos_ml._favoritos_ml_preco_contingencia_sem_promocao(req)

    assert preco == 60.05


def test_contingencia_preserva_faixa_competitiva_quando_margem_permitem(monkeypatch):
    _configurar_dependencias(monkeypatch)
    req = _req_contingencia(
        preco_ranking=37.90,
        preco_minimo_margem=30.00,
        preco_promocional=None,
        preco_competitivo=None,
    )

    preco = favoritos_ml._favoritos_ml_preco_contingencia_sem_promocao(req)

    assert preco == 36.40


def test_contingencia_prefere_preco_final_promocional_sem_campanha(monkeypatch):
    _configurar_dependencias(monkeypatch)
    req = _req_contingencia(preco_ranking=49.50, preco_minimo_margem=49.84, preco_promocional=49.84)

    preco = favoritos_ml._favoritos_ml_preco_contingencia_sem_promocao(req)

    assert preco == 49.84


def test_erro_generico_ml_cai_no_fallback_sem_promocao(monkeypatch):
    from backend.services.promocoes_core_parsing import normalizar_texto

    monkeypatch.setattr(favoritos_ml, "normalizar_texto", normalizar_texto, raising=False)

    assert favoritos_ml._favoritos_ml_falha_por_percentual_promocao(
        "Preco atualizado, mas falhou ao aplicar a promocao: Oops! Something went wrong..."
    )


def test_ml_api_get_nao_repete_erro_deterministico(monkeypatch):
    chamadas = []
    pausas = []

    class Response:
        status_code = 403

    monkeypatch.setattr(favoritos_ml.requests, "get", lambda *args, **kwargs: chamadas.append((args, kwargs)) or Response())
    monkeypatch.setattr(favoritos_ml.time, "sleep", lambda delay: pausas.append(delay))

    assert favoritos_ml._ml_api_get("https://api.mercadolibre.com/items/MLB123", max_retries=3, delay=1) is None
    assert len(chamadas) == 1
    assert pausas == []


def test_ml_api_get_preserva_retry_para_429(monkeypatch):
    chamadas = []
    pausas = []

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

        def json(self):
            return {"id": "MLB123"}

    respostas = iter([Response(429), Response(200)])
    monkeypatch.setattr(favoritos_ml.requests, "get", lambda *args, **kwargs: chamadas.append((args, kwargs)) or next(respostas))
    monkeypatch.setattr(favoritos_ml.time, "sleep", lambda delay: pausas.append(delay))

    assert favoritos_ml._ml_api_get("https://api.mercadolibre.com/items/MLB123", max_retries=3, delay=1) == {"id": "MLB123"}
    assert len(chamadas) == 2
    assert pausas == [1]


def test_cache_local_le_cada_json_uma_unica_vez(monkeypatch, tmp_path):
    caminho = tmp_path / "favoritos_anuncios_ignorados_teste.json"
    caminho.write_text(
        json.dumps({"anuncios": [{"id": "MLB1234567890", "data_criacao": "2024-01-02T00:00:00Z"}]}),
        encoding="utf-8",
    )
    leituras = []
    open_real = builtins.open

    def open_contado(path, *args, **kwargs):
        if str(path) == str(caminho):
            leituras.append(str(path))
        return open_real(path, *args, **kwargs)

    monkeypatch.setattr(favoritos_ml, "get_tenant_path", lambda _client: str(tmp_path), raising=False)
    monkeypatch.setattr(favoritos_ml, "open", open_contado, raising=False)

    resultado = favoritos_ml._ml_datas_cache_local("000002")

    assert resultado["MLB1234567890"].startswith("2024-01-02")
    assert leituras == [str(caminho)]


def test_extrator_usa_mapa_precarregado_sem_revarrer_arquivos(monkeypatch):
    item_id = "MLB1234567890"
    monkeypatch.setattr(
        favoritos_extract,
        "_ml_data_criacao_cache_local",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("nao deve revarrer cache local")),
        raising=False,
    )

    info = favoritos_extract._extrair_info_anuncio(
        "",
        item_id,
        client_id="000002",
        dados_base={
            "vendedor": "Loja Teste",
            "fonte_vendedor": "api_item",
            "vendas": 12,
            "fonte_vendas": "api_item_vendas",
        },
        datas_cache_local={item_id: "2024-01-02T00:00:00Z"},
    )

    assert info["data_criacao"].startswith("2024-01-02")
    assert info["fonte_data_criacao"] == "cache_local_item"


def test_multiget_resolve_itens_em_lojas_sucessivas_sem_perder_fallback(monkeypatch):
    item_a = "MLB1234567890"
    item_b = "MLB1234567891"
    chamadas = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    monkeypatch.setattr(
        favoritos_ml,
        "carregar_lojas",
        lambda _client: [
            {"nome": "Loja 1", "integracoes": {"mercadolivre": {"access_token": "token-1"}}},
            {"nome": "Loja 2", "integracoes": {"mercadolivre": {"access_token": "token-2"}}},
        ],
        raising=False,
    )

    def request_fake(_client, loja, _cfg, _method, _url, **kwargs):
        ids = kwargs["params"]["ids"].split(",")
        chamadas.append((loja, ids))
        if loja == "Loja 1":
            return Response([
                {"code": 403, "body": {"id": item_a}},
                {"code": 200, "body": {"id": item_b, "title": "Produto B"}},
            ]), {}
        return Response([{ "code": 200, "body": {"id": item_a, "title": "Produto A"}}]), {}

    monkeypatch.setattr(favoritos_ml, "_ml_api_request", request_fake, raising=False)
    monkeypatch.setattr(
        favoritos_ml,
        "_ml_api_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback publico nao deveria ser usado")),
        raising=False,
    )

    resultado = favoritos_ml._ml_api_items_multiget_tenant("000002", [item_a, item_b, item_a])

    assert set(resultado) == {item_a, item_b}
    assert {loja for loja, _ids in chamadas} == {"Loja 1", "Loja 2"}
    assert all(ids == [item_a, item_b] for _loja, ids in chamadas)


def test_multiget_consulta_lojas_em_paralelo_sem_perder_prioridade(monkeypatch):
    item_id = "MLB1234567890"
    barreira = threading.Barrier(4, timeout=2)
    chamadas = []

    class Response:
        status_code = 200

        def __init__(self, loja):
            self.loja = loja

        def json(self):
            return [{"code": 200, "body": {"id": item_id, "title": f"Produto {self.loja}"}}]

    monkeypatch.setattr(
        favoritos_ml,
        "carregar_lojas",
        lambda _client: [
            {"nome": f"Loja {indice}", "integracoes": {"mercadolivre": {"access_token": f"token-{indice}"}}}
            for indice in range(1, 5)
        ],
        raising=False,
    )

    def request_fake(_client, loja, _cfg, _method, _url, **_kwargs):
        chamadas.append(loja)
        barreira.wait()
        return Response(loja), {}

    monkeypatch.setattr(favoritos_ml, "_ml_api_request", request_fake, raising=False)
    monkeypatch.setattr(favoritos_ml, "_ml_api_get", lambda *_args, **_kwargs: [], raising=False)

    resultado = favoritos_ml._ml_api_items_multiget_tenant("000002", [item_id])

    assert set(chamadas) == {"Loja 1", "Loja 2", "Loja 3", "Loja 4"}
    assert resultado[item_id]["title"] == "Produto Loja 1"


def test_multiget_publico_preserva_resultado_parcial_para_fallback_individual(monkeypatch):
    item_a = "MLB1234567890"
    item_b = "MLB1234567891"
    chamadas = []
    monkeypatch.setattr(favoritos_ml, "carregar_lojas", lambda _client: [], raising=False)

    def public_fake(_url, params=None, **_kwargs):
        chamadas.append(params["ids"])
        return [{"code": 200, "body": {"id": item_a, "title": "Produto A"}}, {"code": 404, "body": {"id": item_b}}]

    monkeypatch.setattr(favoritos_ml, "_ml_api_get", public_fake, raising=False)

    resultado = favoritos_ml._ml_api_items_multiget_tenant("000002", [item_a, item_b])

    assert set(resultado) == {item_a}
    assert chamadas == [f"{item_a},{item_b}"]


def test_extrator_preload_multiget_mantem_campos_do_fluxo_individual(monkeypatch):
    item_id = "MLB1234567890"
    api_item = {
        "id": item_id,
        "date_created": "2024-02-03T12:00:00Z",
        "seller": {"id": 99, "nickname": "LOJA TESTE"},
        "sold_quantity": 42,
        "condition": "new",
        "listing_type_id": "gold_special",
        "installments": {"rate": 0},
        "shipping": {"logistic_type": "fulfillment", "mode": "me2"},
    }
    monkeypatch.setattr(favoritos_extract, "_ml_api_visitas_com_oauth_tenant", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(favoritos_extract, "_ml_wayback_primeira_captura_data", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(favoritos_extract, "_ml_primeira_pergunta_publica_data", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(favoritos_extract, "_ml_api_item_com_oauth_tenant", lambda *_args, **_kwargs: api_item, raising=False)
    monkeypatch.setattr(
        favoritos_extract,
        "_ml_api_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("OAuth deveria resolver o item legado")),
        raising=False,
    )

    preloaded = favoritos_extract._extrair_info_anuncio(
        "",
        item_id,
        client_id="000002",
        datas_cache_local={},
        api_item_precarregado=api_item,
    )
    legacy = favoritos_extract._extrair_info_anuncio(
        "",
        item_id,
        client_id="000002",
        datas_cache_local={},
    )

    campos = [
        "data_criacao", "vendedor", "vendas", "listing_type_id", "listing_type_name",
        "tipo_anuncio", "parcelamento_sem_juros", "logistic_type", "shipping_mode",
        "is_full", "condicao", "condition", "item_condition",
    ]
    assert {campo: preloaded.get(campo) for campo in campos} == {campo: legacy.get(campo) for campo in campos}


def test_extrator_nao_repete_api_individual_apos_multiget_sem_resultado(monkeypatch):
    item_id = "MLB1234567890"
    monkeypatch.setattr(
        favoritos_extract,
        "_ml_api_item_com_oauth_tenant",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("OAuth individual nao deve repetir o multiget")),
        raising=False,
    )
    monkeypatch.setattr(
        favoritos_extract,
        "_ml_api_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("API publica individual nao deve repetir o multiget")),
        raising=False,
    )
    monkeypatch.setattr(favoritos_extract, "_ml_api_visitas_com_oauth_tenant", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(favoritos_extract, "_ml_wayback_primeira_captura_data", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(favoritos_extract, "_ml_primeira_pergunta_publica_data", lambda *_args, **_kwargs: None, raising=False)

    info = favoritos_extract._extrair_info_anuncio(
        "",
        item_id,
        client_id="000002",
        datas_cache_local={},
        api_item_precarregado=None,
        api_item_precarregado_tentado=True,
    )

    assert info["data_criacao"] is None
    assert info["vendedor"] is None


def test_extrator_nao_repete_consulta_de_visitas_para_o_mesmo_item(monkeypatch):
    item_id = "MLB1234567890"
    chamadas_visitas = []
    api_item = {
        "id": item_id,
        "date_created": "2024-02-03T12:00:00Z",
        "seller": {"id": 99, "nickname": "LOJA TESTE"},
        "sold_quantity": 42,
    }
    monkeypatch.setattr(favoritos_extract, "_ml_api_visitas_com_oauth_tenant", lambda *_args, **_kwargs: chamadas_visitas.append(item_id) or None, raising=False)
    monkeypatch.setattr(favoritos_extract, "_ml_wayback_primeira_captura_data", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(favoritos_extract, "_ml_primeira_pergunta_publica_data", lambda *_args, **_kwargs: None, raising=False)

    favoritos_extract._extrair_info_anuncio(
        "",
        item_id,
        client_id="000002",
        datas_cache_local={},
        api_item_precarregado=api_item,
    )

    assert chamadas_visitas == [item_id]


def test_extrator_preserva_metadados_completos_recebidos_da_pagina(monkeypatch):
    item_id = "MLB1234567890"
    monkeypatch.setattr(
        favoritos_extract,
        "_ml_api_visitas_com_oauth_tenant",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("visitas ja vieram da pagina")),
        raising=False,
    )

    info = favoritos_extract._extrair_info_anuncio(
        "",
        item_id,
        client_id="000002",
        datas_cache_local={item_id: "2024-02-03T12:00:00Z"},
        api_item_precarregado={"id": item_id},
        dados_base={
            "vendedor": "LOJA TESTE",
            "fonte_vendedor": "api_item",
            "vendas": 42,
            "fonte_vendas": "api_item_vendas",
            "visitas": 99,
            "fonte_visitas": "api_visitas",
            "sku": "001",
            "listing_type_id": "gold_pro",
            "listing_type_name": "Premium",
            "tipo_anuncio": "Premium",
            "parcelamento_sem_juros": True,
            "shipping": {"logistic_type": "fulfillment", "mode": "me2"},
            "logistic_type": "fulfillment",
            "shipping_mode": "me2",
            "is_full": True,
            "condicao": "new",
        },
    )

    assert info["visitas"] == 99
    assert info["sku"] == "001"
    assert info["listing_type_id"] == "gold_pro"
    assert info["tipo_anuncio"] == "Premium"
    assert info["parcelamento_sem_juros"] is True
    assert info["logistic_type"] == "fulfillment"
    assert info["shipping_mode"] == "me2"
    assert info["is_full"] is True
    assert info["condicao"] == info["condition"] == info["item_condition"] == "new"


def test_visitas_consulta_lojas_em_paralelo_e_preserva_prioridade(monkeypatch):
    item_id = "MLB1234567890"
    barreira = threading.Barrier(4, timeout=2)
    chamadas = []
    cache_salvo = []

    class Response:
        status_code = 200

        def __init__(self, loja):
            self.loja = loja

        def json(self):
            return {"total_visits": int(self.loja.rsplit(" ", 1)[-1])}

    monkeypatch.setattr(
        favoritos_ml,
        "carregar_lojas",
        lambda _client: [
            {"nome": f"Loja {indice}", "integracoes": {"mercadolivre": {"access_token": f"token-{indice}"}}}
            for indice in range(1, 5)
        ],
        raising=False,
    )
    monkeypatch.setattr(favoritos_ml, "_ml_cache_get", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(favoritos_ml, "_ml_cache_set", lambda chave, valor: cache_salvo.append((chave, valor)), raising=False)

    def request_fake(_client, loja, _cfg, _method, _url, **_kwargs):
        chamadas.append(loja)
        barreira.wait()
        return Response(loja), {}

    monkeypatch.setattr(favoritos_ml, "_ml_api_request", request_fake, raising=False)

    resultado = favoritos_ml._ml_api_visitas_com_oauth_tenant("000002", item_id)

    assert set(chamadas) == {"Loja 1", "Loja 2", "Loja 3", "Loja 4"}
    assert resultado == {"visitas": 1, "fonte": "api_visitas", "loja": "Loja 1"}
    assert cache_salvo and cache_salvo[0][1] == resultado
