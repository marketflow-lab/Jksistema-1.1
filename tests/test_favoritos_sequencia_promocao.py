import json

import backend_api  # noqa: F401 - configura o runtime modular
from backend.schemas.favoritos import FavoritosEfetivarPromocaoRequest
from backend.services import favoritos_endpoints, favoritos_ml


ITEM_ID = "MLB2121768448"


class _Response:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = json.dumps(self._body)
        self.headers = {}

    def json(self):
        return self._body


def _req():
    return FavoritosEfetivarPromocaoRequest(
        loja="JK Pecas",
        item_id=ITEM_ID,
        sku="001",
        preco_anuncio=999.99,
        preco_ideal=169.32,
        preco_promocional=169.32,
        preco_competitivo=169.32,
        percentual_promocao=21.0,
        campanha_id="campanha-teste",
        campanha_nome="Campanha teste",
        promotion_type="SELLER_CAMPAIGN",
    )


def _estado(preco_alvo=None, *, ok=True):
    return {
        "item_id": ITEM_ID,
        "status": "active",
        "sub_status": [],
        "listing_type_id": "gold_special",
        "price": 287.22 if preco_alvo is None else preco_alvo,
        "mutation_blocked": False,
        "retryable": False,
        "block_reason": "",
        "price_preflight": {
            "checked": preco_alvo is not None,
            "ok": ok,
            "price_update_required": True,
            "target_price": preco_alvo,
            "reason": "" if ok else "pricing_automation_configured",
            "message": "Preco apto." if ok else "Automacao de precos bloqueia a alteracao.",
        },
    }


def _preparar_endpoint(monkeypatch, verificacao):
    alvos_estado = []

    def _obter_estado(*args):
        alvo = args[-1] if len(args) >= 5 else None
        alvos_estado.append(alvo)
        return _estado(alvo), {}

    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_obter_estado_item", _obter_estado)
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_remover_promocoes_atuais", lambda *_args: ([], {}))
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_atualizar_preco_item", lambda *_args: ({"success": True}, {}))
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_aguardar_preco_anuncio",
        lambda *_args, **_kwargs: ({
            "success": True,
            "authoritative_price": 214.33,
            "authoritative_price_field": "standard_price",
        }, {}),
    )
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_verificar_efetivacao", lambda *_args: (verificacao, {}))
    monkeypatch.setattr(favoritos_endpoints, "_cache_invalidar_loja", lambda *_args: None)
    monkeypatch.setattr(favoritos_endpoints.time, "sleep", lambda *_args: None)
    return alvos_estado


def _fallback_ideal():
    return {
        "fallback_sem_promocao_aplicado": True,
        "preco_anuncio_contingencia": 169.32,
        "preco_confirmacao_fallback": {
            "success": True,
            "authoritative_price": 169.32,
            "authoritative_price_field": "standard_price",
        },
        "promocao_clear_confirmada_fallback": {"success": True, "active_promotions": []},
    }


def test_preco_cheio_e_derivado_em_centavos_do_ideal_e_percentual():
    assert favoritos_ml._favoritos_ml_calcular_preco_cheio_centavos(169.32, 21) == 214.33
    assert favoritos_endpoints._favoritos_endpoint_preco_cheio(169.32, 21) == 214.33


def test_remocao_confirma_clear_strict_antes_de_retornar(monkeypatch):
    eventos = []
    consultas = iter([
        [{"id": "PROMO-ANTIGA", "status": "started", "promotion_type": "SELLER_CAMPAIGN"}],
        [],
        [],
        [],
    ])

    def _promocoes(*_args, **kwargs):
        eventos.append(("get_promotions", kwargs.get("strict")))
        return next(consultas), {}

    def _request(_client_id, _loja, cfg, method, url, **_kwargs):
        if method == "DELETE":
            eventos.append(("delete_promotion", True))
            return _Response(204), cfg
        eventos.append(("get_sale_price", True))
        return _Response(200, {"amount": 287.22, "regular_amount": None, "metadata": {}}), cfg

    monkeypatch.setattr(favoritos_ml, "_ml_obter_promocoes_item", _promocoes)
    monkeypatch.setattr(favoritos_ml, "_ml_api_request", _request)
    monkeypatch.setattr(favoritos_ml.time, "sleep", lambda *_args: None)

    removidas, _cfg = favoritos_ml._favoritos_ml_remover_promocoes_atuais(
        "cliente", "JK Pecas", {}, ITEM_ID, _req()
    )

    assert removidas[0]["success"] is True
    assert eventos == [
        ("get_promotions", True),
        ("delete_promotion", True),
        ("get_promotions", True),
        ("get_sale_price", True),
        ("get_promotions", True),
        ("get_sale_price", True),
        ("get_promotions", True),
        ("get_sale_price", True),
    ]


def test_clear_rejeita_sale_price_promocional_mesmo_sem_promocao_na_lista(monkeypatch):
    monkeypatch.setattr(favoritos_ml, "_ml_obter_promocoes_item", lambda *_args, **_kwargs: ([], {}))
    monkeypatch.setattr(
        favoritos_ml,
        "_ml_api_request",
        lambda _client_id, _loja, cfg, _method, _url, **_kwargs: (
            _Response(200, {
                "amount": 48.41,
                "regular_amount": 61.28,
                "metadata": {
                    "campaign_id": "C-MLB4783005",
                    "promotion_id": "OFFER-MLB1984892935-13417327562",
                    "promotion_type": "custom",
                },
            }),
            cfg,
        ),
    )
    monkeypatch.setattr(favoritos_ml.time, "sleep", lambda *_args: None)

    resultado, _cfg = favoritos_ml._favoritos_ml_confirmar_sem_promocoes(
        "cliente",
        "JK Pecas",
        {},
        "MLB1984892935",
        preco_direto=48.41,
        tentativas=2,
        leituras_estaveis=2,
    )

    assert resultado["success"] is False
    assert resultado["active_promotions"] == []
    assert resultado["sale_price_observable"] is True
    assert resultado["sale_price_has_promotion"] is True
    assert resultado["sale_price_amount"] == 48.41
    assert resultado["sale_price_regular_amount"] == 61.28
    assert resultado["stable_reads"] == 0


def test_clear_exige_tres_leituras_estaveis_no_sale_price(monkeypatch):
    chamadas = []
    monkeypatch.setattr(favoritos_ml, "_ml_obter_promocoes_item", lambda *_args, **_kwargs: ([], {}))

    def _request(_client_id, _loja, cfg, method, url, **_kwargs):
        chamadas.append((method, url))
        return _Response(200, {"amount": 169.32, "regular_amount": None, "metadata": {}}), cfg

    monkeypatch.setattr(favoritos_ml, "_ml_api_request", _request)
    monkeypatch.setattr(favoritos_ml.time, "sleep", lambda *_args: None)

    resultado, _cfg = favoritos_ml._favoritos_ml_confirmar_sem_promocoes(
        "cliente",
        "JK Pecas",
        {},
        ITEM_ID,
        preco_direto=169.32,
        tentativas=4,
        leituras_estaveis=3,
    )

    assert resultado["success"] is True
    assert resultado["stable_reads"] == 3
    assert resultado["direct_price_ok"] is True
    assert len(chamadas) == 3


def test_fallback_remove_novamente_se_promocao_reaparecer(monkeypatch):
    remocoes = []
    atualizacoes = []
    confirmacoes = iter([
        {
            "success": False,
            "active_promotions": [{"promotion_id": "campanha-teste"}],
            "sale_price_has_promotion": True,
            "sale_price_amount": 169.32,
            "sale_price_regular_amount": 214.33,
        },
        {
            "success": True,
            "active_promotions": [],
            "sale_price_has_promotion": False,
            "sale_price_amount": 169.32,
            "sale_price_regular_amount": None,
        },
    ])

    def _remover(*_args):
        numero = len(remocoes) + 1
        remocoes.append(numero)
        return ([{"success": True, "round": numero}], {})

    def _atualizar(*args):
        atualizacoes.append(args[-1])
        return ({"success": True}, {})

    monkeypatch.setattr(favoritos_ml, "_favoritos_ml_remover_promocoes_atuais", _remover)
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_obter_estado_item",
        lambda *_args: (_estado(169.32), {}),
    )
    monkeypatch.setattr(favoritos_ml, "_favoritos_ml_atualizar_preco_item", _atualizar)
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_aguardar_preco_anuncio",
        lambda *_args, **_kwargs: ({"success": True, "authoritative_price": 169.32}, {}),
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_confirmar_sem_promocoes",
        lambda *_args, **_kwargs: (next(confirmacoes), {}),
    )

    resultado, _cfg = favoritos_ml._favoritos_ml_aplicar_contingencia_sem_promocao(
        "cliente",
        "JK Pecas",
        {},
        ITEM_ID,
        _req(),
        "percentual divergente",
    )

    assert resultado["fallback_sem_promocao_aplicado"] is True
    assert remocoes == [1, 2]
    assert atualizacoes == [169.32, 169.32]
    assert len(resultado["promocoes_removidas_fallback"]) == 2
    assert resultado["preco_update_reconciliacao"]["success"] is True


def test_preflight_de_preco_so_recebe_alvo_depois_do_clear(monkeypatch):
    chamadas = []
    estados = iter([_estado(None), _estado(214.33, ok=False)])
    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})

    def _obter_estado(*args):
        chamadas.append(("estado", args[-1]))
        return next(estados), {}

    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_obter_estado_item", _obter_estado)
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_remover_promocoes_atuais",
        lambda *_args: chamadas.append(("clear", None)) or ([], {}),
    )

    response = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(), client_id="cliente")
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 409
    assert payload["outcome"] == "partial_failure"
    assert chamadas == [("estado", None), ("clear", None), ("estado", 214.33)]


def test_rejeicao_explicita_aplica_preco_ideal_direto_sem_promocao(monkeypatch):
    _preparar_endpoint(monkeypatch, {})
    chamadas = []
    monkeypatch.setattr(
        favoritos_endpoints,
        "_promo_aplicar_item_participacao_ml",
        lambda *_args, **_kwargs: (False, "percentual recusado", {}),
    )

    def _fallback(*args):
        chamadas.append(args[4].preco_ideal)
        return _fallback_ideal(), {}

    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_aplicar_contingencia_sem_promocao", _fallback)

    resultado = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(), client_id="cliente")

    assert resultado["operation_state"] == "completed_fallback"
    assert resultado["campanha_aplicada"] is False
    assert resultado["preco_anuncio"] == 169.32
    assert resultado["observados_autoritativos"]["final_price"] == 169.32
    assert chamadas == [169.32]


def test_percentual_observado_de_36_porcento_dispara_fallback(monkeypatch):
    verificacao = {
        "success": False,
        "confirmed_divergence": True,
        "observable": True,
        "desconto_esperado": 21.0,
        "desconto_info": 36.0,
        "base_price": 214.33,
        "final_price": 137.17,
    }
    _preparar_endpoint(monkeypatch, verificacao)
    monkeypatch.setattr(
        favoritos_endpoints,
        "_promo_aplicar_item_participacao_ml",
        lambda *_args, **_kwargs: (True, "", {}),
    )
    chamadas = []
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_aplicar_contingencia_sem_promocao",
        lambda *_args: chamadas.append("fallback") or (_fallback_ideal(), {}),
    )

    resultado = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(), client_id="cliente")

    assert resultado["operation_state"] == "completed_fallback"
    assert resultado["verificacao"]["desconto_info"] == 36.0
    assert chamadas == ["fallback"]


def test_promocao_so_confirma_apos_duas_leituras_autoritativas_iguais(monkeypatch):
    chamadas_item = []
    promocao = {
        "id": "campanha-teste",
        "status": "started",
        "promotion_type": "SELLER_CAMPAIGN",
        "price": 169.32,
        "discount_percentage": 21.0,
    }

    def _request(_client_id, _loja, cfg, method, url, **_kwargs):
        chamadas_item.append((method, url))
        return _Response(200, {"id": ITEM_ID, "status": "active", "base_price": 214.33}), cfg

    monkeypatch.setattr(favoritos_ml, "_ml_api_request", _request)
    monkeypatch.setattr(
        favoritos_ml,
        "_ml_obter_preco_detalhado",
        lambda *_args, **_kwargs: ({"standard_price": 214.33, "price": 169.32, "discount_pct": 21.0}, {}),
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_ml_obter_promocoes_item",
        lambda *_args, **kwargs: ([promocao], {}),
    )
    monkeypatch.setattr(favoritos_ml.time, "sleep", lambda *_args: None)

    resultado, _cfg = favoritos_ml._favoritos_ml_verificar_efetivacao(
        "cliente",
        "JK Pecas",
        {},
        ITEM_ID,
        "campanha-teste",
        "SELLER_CAMPAIGN",
        214.33,
        169.32,
        21.0,
        tentativas=3,
        leituras_estaveis=2,
    )

    assert resultado["success"] is True
    assert resultado["stable_reads"] == 2
    assert resultado["final_price"] == 169.32
    assert len(chamadas_item) == 2


def test_divergencia_de_36_porcento_exige_duas_leituras_iguais(monkeypatch):
    chamadas_item = []
    promocao = {
        "id": "campanha-teste",
        "status": "started",
        "promotion_type": "SELLER_CAMPAIGN",
        "price": 137.17,
        "discount_percentage": 36.0,
    }

    def _request(_client_id, _loja, cfg, method, url, **_kwargs):
        chamadas_item.append((method, url))
        return _Response(200, {"id": ITEM_ID, "status": "active", "base_price": 214.33}), cfg

    monkeypatch.setattr(favoritos_ml, "_ml_api_request", _request)
    monkeypatch.setattr(
        favoritos_ml,
        "_ml_obter_preco_detalhado",
        lambda *_args, **_kwargs: ({"standard_price": 214.33, "price": 137.17, "discount_pct": 36.0}, {}),
    )
    monkeypatch.setattr(favoritos_ml, "_ml_obter_promocoes_item", lambda *_args, **_kwargs: ([promocao], {}))
    monkeypatch.setattr(favoritos_ml.time, "sleep", lambda *_args: None)

    resultado, _cfg = favoritos_ml._favoritos_ml_verificar_efetivacao(
        "cliente",
        "JK Pecas",
        {},
        ITEM_ID,
        "campanha-teste",
        "SELLER_CAMPAIGN",
        214.33,
        169.32,
        21.0,
        tentativas=3,
        leituras_estaveis=2,
    )

    assert resultado["success"] is False
    assert resultado["confirmed_divergence"] is True
    assert resultado["desconto_info"] == 36.0
    assert resultado["divergent_stable_reads"] == 2
    assert len(chamadas_item) == 2


def test_uma_divergencia_seguida_de_leitura_incompleta_permanece_desconhecida(monkeypatch):
    itens = iter([
        {"id": ITEM_ID, "status": "active", "base_price": 214.33},
        {"id": ITEM_ID, "status": "active"},
    ])
    promocao = {
        "id": "campanha-teste",
        "status": "started",
        "promotion_type": "SELLER_CAMPAIGN",
        "price": 137.17,
        "discount_percentage": 36.0,
    }

    monkeypatch.setattr(
        favoritos_ml,
        "_ml_api_request",
        lambda _client_id, _loja, cfg, _method, _url, **_kwargs: (_Response(200, next(itens)), cfg),
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_ml_obter_preco_detalhado",
        lambda *_args, **_kwargs: ({"price": 137.17, "discount_pct": 36.0}, {}),
    )
    monkeypatch.setattr(favoritos_ml, "_ml_obter_promocoes_item", lambda *_args, **_kwargs: ([promocao], {}))
    monkeypatch.setattr(favoritos_ml.time, "sleep", lambda *_args: None)

    resultado, _cfg = favoritos_ml._favoritos_ml_verificar_efetivacao(
        "cliente",
        "JK Pecas",
        {},
        ITEM_ID,
        "campanha-teste",
        "SELLER_CAMPAIGN",
        214.33,
        169.32,
        21.0,
        tentativas=2,
        leituras_estaveis=2,
    )

    assert resultado["success"] is False
    assert resultado["confirmed_divergence"] is False
    assert resultado["observable"] is False
    assert resultado["divergent_stable_reads"] == 0
