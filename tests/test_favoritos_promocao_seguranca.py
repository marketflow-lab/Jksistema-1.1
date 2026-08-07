import json

import pytest
from fastapi import HTTPException

import backend_api  # noqa: F401 - configura o runtime modular usado pelos servicos
from backend.schemas.favoritos import FavoritosEfetivarPromocaoRequest
from backend.services import favoritos_endpoints, favoritos_ml


ITEM_ID = "MLB3575450631"
PROMOTION_ID = "C-MLB4870662"


class _Response:
    def __init__(self, status_code, body=None, *, headers=None):
        self.status_code = status_code
        self._body = body or {}
        self.text = json.dumps(self._body)
        self.headers = headers or {}

    def json(self):
        return self._body


def _req():
    return FavoritosEfetivarPromocaoRequest(
        loja="Uai Mineirinho",
        item_id=ITEM_ID,
        sku="001",
        preco_anuncio=101.01,
        preco_ideal=79.80,
        preco_promocional=79.80,
        preco_competitivo=79.80,
        percentual_promocao=21.0,
        campanha_id=PROMOTION_ID,
        campanha_nome="Campanha teste",
        promotion_type="SELLER_CAMPAIGN",
    )


def _confirmacao_insegura():
    return {
        "success": False,
        "attempt": 8,
        "active_promotions": [
            {
                "promotion_id": PROMOTION_ID,
                "promotion_type": "SELLER_CAMPAIGN",
                "status": "Ativo",
            }
        ],
        "strict": True,
        "sale_price_observable": True,
        "sale_price_has_promotion": True,
        "sale_price_amount": 36.90,
        "sale_price_regular_amount": 101.01,
        "sale_price_promotion_id": "OFFER-MLB3575450631-13496818627",
        "sale_price_campaign_id": PROMOTION_ID,
        "sale_price_promotion_type": "SELLER_CAMPAIGN",
        "direct_price_ok": False,
        "stable_reads": 0,
        "required_stable_reads": 3,
    }


def _confirmacao_limpa(preco=79.80):
    return {
        "success": True,
        "attempt": 3,
        "active_promotions": [],
        "strict": True,
        "sale_price_observable": True,
        "sale_price_has_promotion": False,
        "sale_price_amount": preco,
        "sale_price_regular_amount": None,
        "sale_price_promotion_id": None,
        "sale_price_campaign_id": None,
        "sale_price_promotion_type": None,
        "direct_price_ok": True,
        "stable_reads": 3,
        "required_stable_reads": 3,
    }


def _erro_targeted_persistente(cfg):
    erro = HTTPException(
        status_code=409,
        detail="DELETE direcionado aceito, mas a promocao permaneceu ativa.",
    )
    erro.favoritos_cfg = cfg
    erro.favoritos_remocoes = [
        {
            "promotion_id": PROMOTION_ID,
            "promotion_type": "SELLER_CAMPAIGN",
            "success": True,
            "status_code": 204,
        }
    ]
    erro.favoritos_clear_confirmation = _confirmacao_insegura()
    return erro


def test_delete_direcionado_aceito_mas_persistente_usa_bulk_v2_sem_filtros_e_confirma_clear(monkeypatch):
    chamadas_delete = []
    strict_recebido = []
    confirmacoes = iter([_confirmacao_insegura(), _confirmacao_limpa()])

    def _promocoes(_client_id, _loja, cfg, _item_id, **kwargs):
        strict_recebido.append(kwargs.get("strict"))
        return ([{
            "id": PROMOTION_ID,
            "status": "started",
            "promotion_type": "SELLER_CAMPAIGN",
        }], cfg)

    def _request(_client_id, _loja, cfg, method, _url, **kwargs):
        assert method == "DELETE"
        params = dict(kwargs.get("params") or {})
        chamadas_delete.append(params)
        if len(chamadas_delete) == 1:
            return _Response(204), cfg
        return _Response(200, {
            "successful_ids": [{"offer_id": "OFFER-1", "error": None}],
            "errors": [],
        }), cfg

    def _confirmar(_client_id, _loja, cfg, _item_id, **_kwargs):
        return next(confirmacoes), cfg

    monkeypatch.setattr(favoritos_ml, "_ml_obter_promocoes_item", _promocoes)
    monkeypatch.setattr(favoritos_ml, "_ml_api_request", _request)
    monkeypatch.setattr(favoritos_ml, "_favoritos_ml_confirmar_sem_promocoes", _confirmar)
    monkeypatch.setattr(favoritos_ml, "_favoritos_ml_remocao_max_attempts", lambda: 1)
    monkeypatch.setattr(favoritos_ml, "_favoritos_ml_remocao_max_cycles", lambda: 2)
    monkeypatch.setattr(favoritos_ml.time, "sleep", lambda *_args: None)

    removidas, _cfg = favoritos_ml._favoritos_ml_remover_promocoes_atuais(
        "cliente",
        "Uai Mineirinho",
        {},
        ITEM_ID,
        _req(),
    )

    assert strict_recebido == [True]
    assert chamadas_delete == [
        {
            "app_version": "v2",
            "promotion_type": "SELLER_CAMPAIGN",
            "promotion_id": PROMOTION_ID,
        },
        {"app_version": "v2"},
    ]
    assert [item["method"] for item in removidas] == ["targeted", "bulk_v2"]
    assert all(item["success"] is True for item in removidas)


def test_bulk_v2_retem_retry_after_e_limita_repeticoes_de_423(monkeypatch):
    chamadas = []
    esperas = []

    def _request(_client_id, _loja, cfg, method, _url, **kwargs):
        chamadas.append((method, dict(kwargs.get("params") or {})))
        return _Response(
            423,
            {"message": "423_ENTITY_LOCKED"},
            headers={"Retry-After": "7"},
        ), cfg

    monkeypatch.setattr(favoritos_ml, "_ml_api_request", _request)
    monkeypatch.setattr(favoritos_ml, "_favoritos_ml_remocao_max_attempts", lambda: 3)
    monkeypatch.setattr(favoritos_ml.random, "uniform", lambda *_args: 0.0)
    monkeypatch.setattr(favoritos_ml.time, "sleep", lambda segundos: esperas.append(segundos))

    resultado, _cfg = favoritos_ml._favoritos_ml_remover_promocoes_bulk_v2(
        "cliente",
        "Uai Mineirinho",
        {},
        ITEM_ID,
    )

    assert resultado["success"] is False
    assert resultado["status_code"] == 423
    assert resultado["attempts"] == 3
    assert chamadas == [("DELETE", {"app_version": "v2"})] * 3
    assert esperas == [7.0, 7.0]


def test_bulk_v2_http_200_com_errors_nao_e_sucesso(monkeypatch):
    chamadas = []

    def _request(_client_id, _loja, cfg, method, _url, **kwargs):
        chamadas.append((method, dict(kwargs.get("params") or {})))
        return _Response(200, {
            "successful_ids": [
                {"offer_id": "OFFER-OK", "error": None},
                {"offer_id": "OFFER-FALHOU", "error": "ENTITY_LOCKED"},
            ],
            "errors": [],
        }), cfg

    monkeypatch.setattr(favoritos_ml, "_ml_api_request", _request)

    resultado, _cfg = favoritos_ml._favoritos_ml_remover_promocoes_bulk_v2(
        "cliente",
        "Uai Mineirinho",
        {},
        ITEM_ID,
    )

    assert chamadas == [("DELETE", {"app_version": "v2"})]
    assert resultado["success"] is False
    assert resultado["status_code"] == 200
    assert "ENTITY_LOCKED" in resultado["detail"]


def test_bulk_v2_http_200_com_errors_vira_falha_http_409_no_fluxo(monkeypatch):
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_remover_promocoes_atuais_uma_vez",
        lambda _client_id, _loja, cfg, _item_id, _req: (_ for _ in ()).throw(
            _erro_targeted_persistente(cfg)
        ),
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_remover_promocoes_bulk_v2",
        lambda _client_id, _loja, cfg, _item_id: ({
            "method": "bulk_v2",
            "success": False,
            "status_code": 200,
            "detail": "ENTITY_LOCKED",
            "response": {"errors": [{"error": "ENTITY_LOCKED"}]},
            "attempts": 1,
        }, cfg),
    )
    monkeypatch.setattr(favoritos_ml, "_favoritos_ml_remocao_max_cycles", lambda: 1)

    with pytest.raises(HTTPException) as capturado:
        favoritos_ml._favoritos_ml_remover_promocoes_atuais(
            "cliente",
            "Uai Mineirinho",
            {},
            ITEM_ID,
            _req(),
        )

    assert capturado.value.status_code == 409
    assert capturado.value.favoritos_commercial_safety["state"] == "unsafe"


def test_persistencia_ate_limite_retorna_seguranca_comercial_e_nao_atualiza_preco_direto(monkeypatch):
    chamadas_bulk = []
    atualizacoes_preco = []

    def _targeted(_client_id, _loja, cfg, _item_id, _req):
        raise _erro_targeted_persistente(cfg)

    def _bulk(_client_id, _loja, cfg, _item_id):
        chamadas_bulk.append(_item_id)
        return ({
            "method": "bulk_v2",
            "success": True,
            "status_code": 200,
            "detail": "",
            "response": {"successful_ids": [{"offer_id": "OFFER-1", "error": None}]},
            "attempts": 1,
        }, cfg)

    monkeypatch.setattr(favoritos_ml, "_favoritos_ml_remover_promocoes_atuais_uma_vez", _targeted)
    monkeypatch.setattr(favoritos_ml, "_favoritos_ml_remover_promocoes_bulk_v2", _bulk)
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_confirmar_sem_promocoes",
        lambda _client_id, _loja, cfg, _item_id, **_kwargs: (_confirmacao_insegura(), cfg),
    )
    monkeypatch.setattr(favoritos_ml, "_favoritos_ml_remocao_max_cycles", lambda: 2)
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_atualizar_preco_item",
        lambda *_args: atualizacoes_preco.append(_args[-1]) or ({"success": True}, {}),
    )

    with pytest.raises(HTTPException) as capturado:
        favoritos_ml._favoritos_ml_aplicar_contingencia_sem_promocao(
            "cliente",
            "Uai Mineirinho",
            {},
            ITEM_ID,
            _req(),
            "percentual observado divergente",
        )

    erro = capturado.value
    assert chamadas_bulk == [ITEM_ID, ITEM_ID]
    assert atualizacoes_preco == []
    assert erro.favoritos_stop_batch is True
    assert erro.favoritos_commercial_safety["state"] == "unsafe"
    assert erro.favoritos_commercial_safety["batch_abort_required"] is True
    assert erro.favoritos_commercial_safety["promotion_active"] is True
    assert erro.favoritos_commercial_safety["observed_discount_pct"] == 63.47
    assert erro.favoritos_observados_autoritativos == {
        "promotion_id": PROMOTION_ID,
        "promotion_type": "SELLER_CAMPAIGN",
        "base_price": 101.01,
        "final_price": 36.90,
        "discount_pct": 63.47,
        "sale_price_observable": True,
        "stable_reads": 0,
        "required_stable_reads": 3,
    }


def test_endpoint_preserva_verificacao_observados_e_exige_abortar_lote(monkeypatch):
    aplicacoes_promocao = []
    verificacao = {
        "success": False,
        "confirmed_divergence": True,
        "observable": True,
        "promotion_id": PROMOTION_ID,
        "promotion_type": "SELLER_CAMPAIGN",
        "promotion_type_observed": "SELLER_CAMPAIGN",
        "promocao_ok": True,
        "base_price": 101.01,
        "base_price_field": "standard_price",
        "final_price": 36.90,
        "desconto_esperado": 21.0,
        "desconto_info": 63.47,
        "stable_reads": 0,
        "divergent_stable_reads": 3,
        "required_stable_reads": 3,
        "price_info": {"standard_price": 101.01, "price": 36.90, "discount_pct": 63.47},
    }
    observados = {
        "promotion_id": PROMOTION_ID,
        "promotion_type": "SELLER_CAMPAIGN",
        "base_price": 101.01,
        "final_price": 36.90,
        "discount_pct": 63.47,
        "stable_reads": 0,
        "required_stable_reads": 3,
    }
    commercial_safety = {
        "state": "unsafe",
        "reason": "promotion_clear_not_confirmed",
        "batch_abort_required": True,
        "promotion_active": True,
        "active_promotions": _confirmacao_insegura()["active_promotions"],
        "sale_price_has_promotion": True,
        "observed_discount_pct": 63.47,
    }

    def _estado(*args):
        alvo = args[-1] if len(args) >= 5 else None
        return ({
            "item_id": ITEM_ID,
            "status": "active",
            "sub_status": [],
            "listing_type_id": "gold_special",
            "listing_type_name": "Classico",
            "price": alvo if alvo is not None else 36.90,
            "mutation_blocked": False,
            "retryable": False,
            "block_reason": "",
            "price_preflight": {
                "checked": alvo is not None,
                "ok": True,
                "price_update_required": True,
                "target_price": alvo,
            },
        }, {})

    def _falhar_fallback(*_args):
        erro = HTTPException(status_code=409, detail="A promocao permaneceu ativa.")
        erro.favoritos_commercial_safety = commercial_safety
        erro.favoritos_observados_autoritativos = observados
        erro.favoritos_stop_batch = True
        erro.favoritos_remocoes = [{"method": "bulk_v2", "success": True}]
        raise erro

    def _aplicar_promocao(*_args, **kwargs):
        aplicacoes_promocao.append(dict(kwargs))
        return True, "", {}

    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_obter_estado_item", _estado)
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_remover_promocoes_atuais", lambda *_args: ([], {}))
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_atualizar_preco_item", lambda *_args: ({"success": True}, {}))
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_aguardar_preco_anuncio",
        lambda *_args, **_kwargs: ({
            "success": True,
            "authoritative_price": 101.01,
            "authoritative_price_field": "standard_price",
        }, {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_promo_aplicar_item_participacao_ml",
        _aplicar_promocao,
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_verificar_efetivacao",
        lambda *_args, **_kwargs: (verificacao, {}),
    )
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_aplicar_contingencia_sem_promocao", _falhar_fallback)
    monkeypatch.setattr(favoritos_endpoints, "_cache_invalidar_loja", lambda *_args: None)
    monkeypatch.setattr(favoritos_endpoints.time, "sleep", lambda *_args: None)

    response = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(), client_id="cliente")
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 409
    assert payload["outcome"] == "partial_failure"
    assert payload["percentual_promocao"] == 21.0
    assert payload["verificacao"] == verificacao
    assert payload["observados_autoritativos"] == observados
    assert payload["commercial_safety"] == commercial_safety
    assert payload["stop_batch"] is True
    assert payload["promotion_still_active"] is True
    assert payload["stages"]["promotion"]["status"] == "fallback_failed"
    assert len(aplicacoes_promocao) == 1
    assert aplicacoes_promocao[0]["allow_alternative_deal_price"] is False


@pytest.mark.parametrize(
    ("commercial_state", "outcome", "promotion_active"),
    [
        ("unsafe", "partial_failure", True),
        ("unknown", "partial_unknown", False),
    ],
)
def test_remocao_inicial_sem_sucesso_preserva_clear_e_aborta_lote(
    monkeypatch,
    commercial_state,
    outcome,
    promotion_active,
):
    clear_confirmation = _confirmacao_insegura()
    if commercial_state == "unknown":
        clear_confirmation = {
            **clear_confirmation,
            "active_promotions": [],
            "sale_price_observable": False,
            "sale_price_has_promotion": False,
            "sale_price_amount": None,
            "sale_price_regular_amount": None,
            "sale_price_promotion_id": None,
            "sale_price_campaign_id": None,
            "sale_price_promotion_type": None,
        }
    commercial_safety = {
        "state": commercial_state,
        "reason": "promotion_clear_not_confirmed",
        "batch_abort_required": True,
        "promotion_active": promotion_active,
        "active_promotions": clear_confirmation["active_promotions"],
        "sale_price_has_promotion": clear_confirmation["sale_price_has_promotion"],
    }
    observados = favoritos_ml._favoritos_ml_observados_clear(clear_confirmation)
    mutacoes_posteriores = []

    def _estado(*_args):
        return ({
            "item_id": ITEM_ID,
            "status": "active",
            "sub_status": [],
            "listing_type_id": "gold_special",
            "listing_type_name": "Classico",
            "price": 36.90,
            "mutation_blocked": False,
            "retryable": False,
            "block_reason": "",
            "price_preflight": {"checked": False, "ok": True},
        }, {})

    def _falhar_remocao(*_args):
        erro = HTTPException(status_code=409, detail="Remocao ainda nao reconciliada.")
        erro.favoritos_remocoes = [{
            "method": "targeted",
            "success": False,
            "status_code": 409,
        }]
        erro.favoritos_clear_confirmation = clear_confirmation
        erro.favoritos_commercial_safety = commercial_safety
        erro.favoritos_observados_autoritativos = observados
        erro.favoritos_stop_batch = True
        raise erro

    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_obter_estado_item", _estado)
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_remover_promocoes_atuais", _falhar_remocao)
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_atualizar_preco_item",
        lambda *_args: mutacoes_posteriores.append("preco") or ({"success": True}, {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_promo_aplicar_item_participacao_ml",
        lambda *_args, **_kwargs: mutacoes_posteriores.append("promocao") or (True, "", {}),
    )
    monkeypatch.setattr(favoritos_endpoints, "_cache_invalidar_loja", lambda *_args: None)

    response = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(), client_id="cliente")
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 409
    assert payload["outcome"] == outcome
    assert payload["promocoes_removidas"] == []
    assert payload["commercial_safety"] == commercial_safety
    assert payload["observados_autoritativos"] == observados
    assert payload["promocao_clear_confirmation"] == clear_confirmation
    assert payload["stop_batch"] is True
    assert payload["promotion_still_active"] is promotion_active
    assert payload["stages"]["promotion_removal"]["status"] == "unknown"
    assert mutacoes_posteriores == []


def test_fallback_final_sem_evidencia_atual_anexa_estado_desconhecido(monkeypatch):
    confirmacao_desconhecida = {
        "success": False,
        "item_id": ITEM_ID,
        "active_promotions": [],
        "sale_price_observable": False,
        "sale_price_has_promotion": False,
        "sale_price_amount": None,
        "sale_price_regular_amount": None,
        "sale_price_promotion_id": None,
        "sale_price_campaign_id": None,
        "sale_price_promotion_type": None,
        "direct_price_ok": False,
        "stable_reads": 0,
        "required_stable_reads": 3,
    }
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_remover_promocoes_atuais",
        lambda _client_id, _loja, cfg, _item_id, _req: ([], cfg),
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_obter_estado_item",
        lambda _client_id, _loja, cfg, _item_id, alvo: ({
            "item_id": ITEM_ID,
            "mutation_blocked": False,
            "price_preflight": {"checked": True, "ok": True, "target_price": alvo},
        }, cfg),
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_atualizar_preco_item",
        lambda _client_id, _loja, cfg, _item_id, alvo: ({"success": True, "price": alvo}, cfg),
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_aguardar_preco_anuncio",
        lambda _client_id, _loja, cfg, _item_id, alvo, **_kwargs: ({
            "success": True,
            "authoritative_price": alvo,
        }, cfg),
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_confirmar_sem_promocoes",
        lambda _client_id, _loja, cfg, _item_id, **_kwargs: (confirmacao_desconhecida, cfg),
    )

    with pytest.raises(HTTPException) as capturado:
        favoritos_ml._favoritos_ml_aplicar_contingencia_sem_promocao(
            "cliente",
            "Uai Mineirinho",
            {},
            ITEM_ID,
            _req(),
            "percentual observado divergente",
        )

    erro = capturado.value
    assert erro.favoritos_clear_confirmation == confirmacao_desconhecida
    assert erro.favoritos_commercial_safety["state"] == "unknown"
    assert erro.favoritos_commercial_safety["promotion_active"] is False
    assert erro.favoritos_observados_autoritativos["sale_price_observable"] is False
    assert erro.favoritos_stop_batch is True


def test_confirmacao_sem_promocao_do_favoritos_consulta_modo_estrito(monkeypatch):
    strict_recebido = []

    def _promocoes(_client_id, _loja, cfg, _item_id, **kwargs):
        strict_recebido.append(kwargs.get("strict"))
        return [], cfg

    monkeypatch.setattr(favoritos_ml, "_ml_obter_promocoes_item", _promocoes)
    monkeypatch.setattr(
        favoritos_ml,
        "_ml_api_request",
        lambda _client_id, _loja, cfg, _method, _url, **_kwargs: (
            _Response(200, {"amount": 79.80, "regular_amount": None, "metadata": {}}),
            cfg,
        ),
    )
    monkeypatch.setattr(favoritos_ml.time, "sleep", lambda *_args: None)

    resultado, _cfg = favoritos_ml._favoritos_ml_confirmar_sem_promocoes(
        "cliente",
        "Uai Mineirinho",
        {},
        ITEM_ID,
        preco_direto=79.80,
        tentativas=2,
        leituras_estaveis=2,
    )

    assert resultado["success"] is True
    assert resultado["stable_reads"] == 2
    assert strict_recebido == [True, True]


def test_sale_price_http_200_vazio_nao_comprova_clear(monkeypatch):
    monkeypatch.setattr(
        favoritos_ml,
        "_ml_obter_promocoes_item",
        lambda _client_id, _loja, cfg, _item_id, **_kwargs: ([], cfg),
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_ml_api_request",
        lambda _client_id, _loja, cfg, _method, _url, **_kwargs: (_Response(200, {}), cfg),
    )
    monkeypatch.setattr(favoritos_ml.time, "sleep", lambda *_args: None)

    resultado, _cfg = favoritos_ml._favoritos_ml_confirmar_sem_promocoes(
        "cliente",
        "Uai Mineirinho",
        {},
        ITEM_ID,
        tentativas=3,
        leituras_estaveis=3,
    )

    assert resultado["success"] is False
    assert resultado["sale_price_observable"] is False
    assert resultado["stable_reads"] == 0
