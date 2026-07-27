import json

import backend_api  # noqa: F401 - configura o runtime modular
import pytest
from fastapi import HTTPException

from backend.schemas.favoritos import (
    FavoritosEfetivarPromocaoRequest,
    FavoritosValidarEfetivacaoItemRequest,
)
from backend.services import favoritos_endpoints, favoritos_ml, mercadolivre_legacy_promocoes


ITEM_ID = "MLB2121768448"


class _Response:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body or {}, ensure_ascii=False)
        self.headers = {}

    def json(self):
        return self._body


def _req(listing_type_id_alvo="gold_special"):
    return FavoritosEfetivarPromocaoRequest(
        loja="JK Pecas",
        item_id=ITEM_ID,
        sku="001",
        preco_anuncio=214.33,
        preco_promocional=169.32,
        percentual_promocao=21.0,
        campanha_id="campanha-teste",
        campanha_nome="Campanha teste",
        listing_type_id_alvo=listing_type_id_alvo,
    )


def _estado_preflight(*, ok=True, update_required=True, reason=""):
    return {
        "item_id": ITEM_ID,
        "status": "active",
        "sub_status": [],
        "listing_type_id": "gold_pro",
        "listing_type_name": "Premium",
        "price": 287.22,
        "mutation_blocked": False,
        "retryable": False,
        "block_reason": "",
        "price_preflight": {
            "checked": True,
            "ok": ok,
            "price_update_required": update_required,
            "reason": reason,
            "message": "Automacao de precos bloqueia a alteracao." if not ok else "Preco apto.",
        },
    }


def _payload_json(response):
    return json.loads(response.body.decode("utf-8"))


def test_schema_de_preflight_preserva_compatibilidade_e_aceita_preco_alvo():
    legado = FavoritosValidarEfetivacaoItemRequest(loja="JK Pecas", item_id=ITEM_ID)
    novo = FavoritosValidarEfetivacaoItemRequest(
        loja="JK Pecas",
        item_id=ITEM_ID,
        preco_anuncio_alvo=214.33,
    )

    assert legado.preco_anuncio_alvo is None
    assert novo.preco_anuncio_alvo == 214.33


def test_tag_dynamic_standard_price_bloqueia_sem_consultar_automacao(monkeypatch):
    chamadas = []

    def _request(_client_id, _loja, cfg, method, url, **_kwargs):
        chamadas.append((method, url))
        return _Response(200, {
            "id": ITEM_ID,
            "status": "active",
            "listing_type_id": "gold_pro",
            "price": 287.22,
            "base_price": 287.22,
            "tags": ["dynamic_standard_price"],
            "has_bids": True,
            "sold_quantity": 7,
        }), cfg

    monkeypatch.setattr(favoritos_ml, "_ml_api_request", _request)

    estado, _cfg = favoritos_ml._favoritos_ml_obter_estado_item(
        "cliente", "JK Pecas", {}, ITEM_ID, 214.33
    )

    assert estado["dynamic_standard_price"] is True
    assert estado["has_bids"] is True
    assert estado["sold_quantity"] == 7
    assert estado["price_preflight"]["ok"] is False
    assert estado["price_preflight"]["reason"] == "dynamic_standard_price"
    assert len(chamadas) == 1
    assert chamadas[0][1].endswith(f"/items/{ITEM_ID}")


def test_automacao_configurada_bloqueia_preflight(monkeypatch):
    respostas = iter([
        _Response(200, {
            "id": ITEM_ID,
            "status": "active",
            "listing_type_id": "gold_pro",
            "price": 287.22,
            "base_price": 287.22,
            "tags": [],
        }),
        _Response(200, {
            "item_id": ITEM_ID,
            "status": "ACTIVE",
            "item_rule": {"rule_id": "RULE-1"},
        }),
    ])
    chamadas = []

    def _request(_client_id, _loja, cfg, method, url, **_kwargs):
        chamadas.append((method, url))
        return next(respostas), cfg

    monkeypatch.setattr(favoritos_ml, "_ml_api_request", _request)

    estado, _cfg = favoritos_ml._favoritos_ml_obter_estado_item(
        "cliente", "JK Pecas", {}, ITEM_ID, 214.33
    )

    preflight = estado["price_preflight"]
    assert preflight["ok"] is False
    assert preflight["reason"] == "pricing_automation_configured"
    assert preflight["automation_status"] == "ACTIVE"
    assert chamadas[1][1].endswith(f"/pricing-automation/items/{ITEM_ID}/automation")


def test_execucao_remove_promocao_antes_de_bloquear_preco_no_preflight_pos_clear(monkeypatch):
    chamadas = []
    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_obter_estado_item",
        lambda *_args: (_estado_preflight(ok=False, reason="pricing_automation_configured"), {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_remover_promocoes_atuais",
        lambda *_args: chamadas.append("remover_promocao") or ([], {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_atualizar_tipo_listing_item",
        lambda *_args: chamadas.append("tipo") or ({}, {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_atualizar_preco_item",
        lambda *_args: chamadas.append("preco") or ({}, {}),
    )

    response = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(), client_id="cliente")
    payload = _payload_json(response)

    assert response.status_code == 409
    assert payload["outcome"] == "partial_failure"
    assert payload["retryable"] is False
    assert payload["retry_requires_approval"] is False
    assert payload["price_preflight"]["reason"] == "pricing_automation_configured"
    assert chamadas == ["remover_promocao"]


def test_falha_transitoria_no_preflight_pode_ser_tentada_novamente(monkeypatch):
    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})

    def _falhar(*_args):
        raise HTTPException(status_code=503, detail="pricing automation temporarily unavailable")

    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_obter_estado_item", _falhar)

    response = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(), client_id="cliente")
    payload = _payload_json(response)

    assert response.status_code == 503
    assert payload["outcome"] == "failed"
    assert payload["retryable"] is True
    assert payload["retry_requires_approval"] is True


def test_atualizacao_de_preco_usa_apenas_put_items(monkeypatch):
    chamadas = []

    def _request(_client_id, _loja, cfg, method, url, **kwargs):
        chamadas.append((method, url, kwargs.get("json")))
        return _Response(200, {"id": ITEM_ID, "price": 214.33, "base_price": 214.33}), cfg

    monkeypatch.setattr(favoritos_ml, "_ml_api_request", _request)

    resultado, _cfg = favoritos_ml._favoritos_ml_atualizar_preco_item(
        "cliente", "JK Pecas", {}, ITEM_ID, 214.33
    )

    assert resultado["method"] == "items_put"
    assert chamadas == [("PUT", f"https://api.mercadolibre.com/items/{ITEM_ID}", {"price": 214.33})]
    assert all("/prices/standard" not in url for _method, url, _payload in chamadas)


def test_price_not_modifiable_nao_repete_e_marca_erro_terminal(monkeypatch):
    chamadas = []

    def _request(_client_id, _loja, cfg, method, url, **_kwargs):
        chamadas.append((method, url))
        return _Response(409, {
            "error": "validation_error",
            "message": "Cannot update item",
            "cause": [{"code": "item.price.not_modifiable", "message": "price is not modifiable"}],
        }), cfg

    monkeypatch.setattr(favoritos_ml, "_ml_api_request", _request)
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_ml_aguardar_preco_anuncio",
        lambda *_args, **_kwargs: ({"success": False, "preco_alvo": 214.33}, {}),
    )
    monkeypatch.setattr(favoritos_ml.time, "sleep", lambda *_args: None)

    with pytest.raises(HTTPException) as capturado:
        favoritos_ml._favoritos_ml_atualizar_preco_item(
            "cliente", "JK Pecas", {}, ITEM_ID, 214.33
        )

    assert capturado.value.status_code == 409
    assert capturado.value.favoritos_retryable is False
    assert capturado.value.favoritos_price_business_rule is True
    assert chamadas == [("PUT", f"https://api.mercadolibre.com/items/{ITEM_ID}")]


def test_execucao_propaga_price_not_modifiable_como_nao_repetivel(monkeypatch):
    estados = iter([
        (_estado_preflight(ok=True, update_required=True), {}),
        (_estado_preflight(ok=True, update_required=True), {}),
        (_estado_preflight(ok=True, update_required=True), {}),
    ])
    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_obter_estado_item", lambda *_args: next(estados))
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_remover_promocoes_atuais", lambda *_args: ([], {}))
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_atualizar_tipo_listing_item",
        lambda *_args: ({"success": True, "changed": True, "target": "gold_special"}, {}),
    )

    def _falhar_preco(*_args):
        raise HTTPException(status_code=409, detail="item.price.not_modifiable: price is not modifiable")

    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_atualizar_preco_item", _falhar_preco)
    monkeypatch.setattr(favoritos_endpoints, "_cache_invalidar_loja", lambda *_args: None)
    monkeypatch.setattr(favoritos_endpoints.time, "sleep", lambda *_args: None)

    response = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(), client_id="cliente")
    payload = _payload_json(response)

    assert response.status_code == 409
    assert payload["outcome"] == "partial_failure"
    assert payload["retryable"] is False
    assert payload["retry_requires_approval"] is False
    assert payload["stages"]["listing_type"]["status"] == "completed"
    assert payload["stages"]["price"]["status"] == "failed"


def test_preco_ja_no_alvo_nao_faz_put_nem_marca_parcial_sem_mutacao(monkeypatch):
    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_obter_estado_item",
        lambda *_args: (_estado_preflight(ok=True, update_required=False), {}),
    )
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_remover_promocoes_atuais", lambda *_args: ([], {}))
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_atualizar_preco_item",
        lambda *_args: pytest.fail("PUT de preco nao deveria ocorrer quando o preco ja esta no alvo"),
    )

    def _falhar_conferencia(*_args, **_kwargs):
        raise HTTPException(status_code=503, detail="conferencia temporariamente indisponivel")

    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_aguardar_preco_anuncio", _falhar_conferencia)

    response = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(""), client_id="cliente")
    payload = _payload_json(response)

    assert response.status_code == 503
    assert payload["outcome"] == "failed"
    assert payload["retryable"] is True
    assert payload["preco_update"]["method"] == "skipped_current_price"


def test_consulta_de_promocoes_falha_fechada_apenas_no_modo_strict(monkeypatch):
    monkeypatch.setattr(mercadolivre_legacy_promocoes, "_cache_get", lambda *_args: None)
    monkeypatch.setattr(mercadolivre_legacy_promocoes, "_cache_set", lambda *_args: None)

    def _request(_client_id, _loja, cfg, _method, _url, **_kwargs):
        return _Response(500, {"message": "internal error"}), cfg

    legado, _cfg = mercadolivre_legacy_promocoes._ml_obter_promocoes_item(
        "cliente", "JK Pecas", {}, ITEM_ID, request_fn=_request
    )
    assert legado == []

    with pytest.raises(HTTPException) as capturado:
        mercadolivre_legacy_promocoes._ml_obter_promocoes_item(
            "cliente", "JK Pecas", {}, ITEM_ID, request_fn=_request, strict=True
        )
    assert capturado.value.status_code == 500


def test_fluxo_mutante_nao_engole_falha_ao_listar_promocoes(monkeypatch):
    chamadas = []

    def _falhar(*_args, **kwargs):
        assert kwargs["strict"] is True
        raise HTTPException(status_code=503, detail="Falha ao confirmar promocoes")

    monkeypatch.setattr(favoritos_ml, "_ml_obter_promocoes_item", _falhar)
    monkeypatch.setattr(
        favoritos_ml,
        "_ml_api_request",
        lambda *_args, **_kwargs: chamadas.append("mutacao") or (_Response(204, {}), {}),
    )

    with pytest.raises(HTTPException) as capturado:
        favoritos_ml._favoritos_ml_remover_promocoes_atuais(
            "cliente", "JK Pecas", {}, ITEM_ID, _req("")
        )

    assert capturado.value.status_code == 503
    assert chamadas == []
