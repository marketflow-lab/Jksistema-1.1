import json

import backend_api  # noqa: F401 - configura o runtime modular
import pytest
from fastapi import HTTPException
from backend.schemas.favoritos import (
    FavoritosEfetivarPromocaoRequest,
    FavoritosValidarEfetivacaoItemRequest,
    FavoritosValidarEfetivacaoRequest,
)
from backend.services import favoritos_endpoints, favoritos_ml


def _req(listing_type_id_alvo="gold_special"):
    return FavoritosEfetivarPromocaoRequest(
        loja="JK Pecas",
        item_id="MLB4741353185",
        sku="001",
        preco_anuncio=183.53,
        preco_promocional=144.99,
        percentual_promocao=21.0,
        campanha_id="campanha-teste",
        campanha_nome="Campanha teste",
        listing_type_id_alvo=listing_type_id_alvo,
    )


def _estado(status="active", sub_status=None, listing_type_id="gold_pro", price=130.25):
    sub_status = list(sub_status or [])
    bloqueado = status in {"under_review", "closed", "inactive"} or any(
        valor in {"forbidden", "suspended", "deleted"} for valor in sub_status
    )
    retryable = status == "under_review" or "forbidden" in sub_status
    return {
        "item_id": "MLB4741353185",
        "status": status,
        "sub_status": sub_status,
        "listing_type_id": listing_type_id,
        "listing_type_name": "Classico" if listing_type_id == "gold_special" else "Premium",
        "price": price,
        "mutation_blocked": bloqueado,
        "retryable": retryable,
        "block_reason": "O anuncio esta em revisao no Mercado Livre." if bloqueado else "",
    }


def _payload_json(response):
    return json.loads(response.body.decode("utf-8"))


def test_estado_ml_normaliza_revisao_forbidden_como_bloqueio_retentavel():
    estado = favoritos_ml._favoritos_ml_resumir_estado_item("MLB4741353185", {
        "id": "MLB4741353185",
        "status": "under_review",
        "sub_status": ["forbidden"],
        "listing_type_id": "gold_special",
        "price": 130.25,
    })

    assert estado["mutation_blocked"] is True
    assert estado["retryable"] is True
    assert estado["listing_type_id"] == "gold_special"
    assert estado["price"] == 130.25


def test_validacao_preflight_bloqueia_anuncio_em_revisao_sem_troca_de_tipo(monkeypatch):
    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_obter_estado_item",
        lambda *_args: (_estado("under_review", ["forbidden"], "gold_special"), {}),
    )
    req = FavoritosValidarEfetivacaoRequest(
        itens=[
            FavoritosValidarEfetivacaoItemRequest(
                loja="JK Pecas",
                item_id="MLB4741353185",
            )
        ]
    )

    resultado = favoritos_endpoints.favoritos_ml_validar_efetivacao(req, client_id="cliente")

    assert resultado["itens"][0]["ok"] is False
    assert resultado["itens"][0]["outcome"] == "blocked_preflight"
    assert resultado["itens"][0]["retryable"] is True


def test_execucao_preflight_nao_muta_anuncio_que_ja_esta_em_revisao(monkeypatch):
    chamadas = []
    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_obter_estado_item",
        lambda *_args: (_estado("under_review", ["forbidden"], "gold_special"), {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_remover_promocoes_atuais",
        lambda *_args: chamadas.append("remover") or ([], {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_atualizar_preco_item",
        lambda *_args: chamadas.append("preco") or ({}, {}),
    )

    response = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(), client_id="cliente")
    payload = _payload_json(response)

    assert response.status_code == 409
    assert payload["outcome"] == "blocked_preflight"
    assert payload["completed"] is False
    assert chamadas == []


def test_troca_de_tipo_que_abre_revisao_para_antes_do_preco_e_campanha(monkeypatch):
    estados = iter([
        (_estado("active", [], "gold_pro"), {}),
        (_estado("under_review", ["forbidden"], "gold_special"), {}),
    ])
    chamadas = []
    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_obter_estado_item", lambda *_args: next(estados))
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_remover_promocoes_atuais", lambda *_args: ([], {}))
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_atualizar_tipo_listing_item",
        lambda *_args: ({
            "success": True,
            "changed": True,
            "current": "gold_pro",
            "current_name": "Premium",
            "target": "gold_special",
            "target_name": "Classico",
        }, {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_atualizar_preco_item",
        lambda *_args: chamadas.append("preco") or ({}, {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_promo_aplicar_item_participacao_ml",
        lambda *_args, **_kwargs: chamadas.append("promocao") or (True, "", {}),
    )
    monkeypatch.setattr(favoritos_endpoints, "_cache_invalidar_loja", lambda *_args: chamadas.append("cache"))
    monkeypatch.setattr(favoritos_endpoints.time, "sleep", lambda *_args: None)

    response = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(), client_id="cliente")
    payload = _payload_json(response)

    assert response.status_code == 202
    assert payload["outcome"] == "pending_review"
    assert payload["listing_type_update"]["changed"] is True
    assert payload["stages"]["listing_type"]["status"] == "completed"
    assert payload["stages"]["price"]["status"] == "pending"
    assert "preco" not in chamadas
    assert "promocao" not in chamadas


def test_nova_aprovacao_com_anuncio_ativo_retoma_e_conclui(monkeypatch):
    chamadas = []
    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_obter_estado_item",
        lambda *_args: (_estado("active", [], "gold_special"), {}),
    )
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_remover_promocoes_atuais", lambda *_args: ([], {}))
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_atualizar_tipo_listing_item",
        lambda *_args: ({
            "success": True,
            "changed": False,
            "current": "gold_special",
            "current_name": "Classico",
            "target": "gold_special",
            "target_name": "Classico",
        }, {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_atualizar_preco_item",
        lambda *_args: chamadas.append("preco") or ({"success": True}, {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_aguardar_preco_anuncio",
        lambda *_args, **_kwargs: ({"success": True}, {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_promo_aplicar_item_participacao_ml",
        lambda *_args, **_kwargs: chamadas.append("promocao") or (True, "", {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_verificar_efetivacao",
        lambda *_args: ({"success": True}, {}),
    )
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_verificacao_exige_contingencia_por_margem",
        lambda *_args: (False, "", {}),
    )
    monkeypatch.setattr(favoritos_endpoints, "_cache_invalidar_loja", lambda *_args: None)
    monkeypatch.setattr(favoritos_endpoints.time, "sleep", lambda *_args: None)

    resultado = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(), client_id="cliente")

    assert resultado["success"] is True
    assert resultado["completed"] is True
    assert resultado["outcome"] == "completed"
    assert chamadas == ["preco", "promocao"]


def _preparar_fluxo_ate_promocao(monkeypatch, *, estados=None):
    estados = iter(estados or [_estado("active", [], "gold_special"), _estado("active", [], "gold_special", 183.53)])
    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_obter_estado_item", lambda *_args: (next(estados), {}))
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_remover_promocoes_atuais", lambda *_args: ([], {}))
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_atualizar_preco_item", lambda *_args: ({"success": True}, {}))
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_aguardar_preco_anuncio",
        lambda *_args, **_kwargs: ({"success": True}, {}),
    )
    monkeypatch.setattr(favoritos_endpoints, "_cache_invalidar_loja", lambda *_args: None)


def test_excecao_ao_aplicar_promocao_preserva_preco_e_retorna_parcial(monkeypatch):
    _preparar_fluxo_ate_promocao(monkeypatch)

    def _falhar_promocao(*_args, **_kwargs):
        raise HTTPException(status_code=503, detail="Mercado Livre indisponivel")

    monkeypatch.setattr(favoritos_endpoints, "_promo_aplicar_item_participacao_ml", _falhar_promocao)

    response = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(""), client_id="cliente")
    payload = _payload_json(response)

    assert response.status_code == 503
    assert payload["outcome"] == "partial_failure"
    assert payload["preco_anuncio_atual"] == 183.53
    assert payload["stages"]["price"]["status"] == "completed"
    assert payload["stages"]["promotion"]["status"] == "failed"


def test_excecao_na_verificacao_final_retorna_parcial_sem_perder_estado(monkeypatch):
    _preparar_fluxo_ate_promocao(monkeypatch)
    monkeypatch.setattr(
        favoritos_endpoints,
        "_promo_aplicar_item_participacao_ml",
        lambda *_args, **_kwargs: (True, "", {}),
    )

    def _falhar_verificacao(*_args):
        raise HTTPException(status_code=502, detail="Falha ao conferir promocao")

    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_verificar_efetivacao", _falhar_verificacao)

    response = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(""), client_id="cliente")
    payload = _payload_json(response)

    assert response.status_code == 502
    assert payload["outcome"] == "partial_failure"
    assert payload["preco_anuncio_atual"] == 183.53
    assert payload["stages"]["promotion"]["status"] == "completed"
    assert payload["stages"]["verification"]["status"] == "failed"


def test_excecao_no_fallback_de_margem_retorna_parcial(monkeypatch):
    _preparar_fluxo_ate_promocao(monkeypatch)
    monkeypatch.setattr(
        favoritos_endpoints,
        "_promo_aplicar_item_participacao_ml",
        lambda *_args, **_kwargs: (False, "percentual promocional invalido", {}),
    )
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_falha_por_percentual_promocao", lambda *_args: True)

    def _falhar_fallback(*_args):
        raise HTTPException(status_code=409, detail="Fallback recusado")

    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_aplicar_contingencia_sem_promocao", _falhar_fallback)

    response = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(""), client_id="cliente")
    payload = _payload_json(response)

    assert response.status_code == 409
    assert payload["outcome"] == "partial_failure"
    assert payload["preco_anuncio_atual"] == 183.53
    assert payload["stages"]["promotion"]["status"] == "fallback_failed"


def test_remocao_parcial_preserva_promocoes_ja_removidas_e_nao_altera_preco(monkeypatch):
    chamadas = []
    estados = iter([_estado("active", [], "gold_special"), _estado("active", [], "gold_special")])
    monkeypatch.setattr(favoritos_endpoints, "_obter_cfg_ml", lambda *_args: {})
    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_obter_estado_item", lambda *_args: (next(estados), {}))

    def _falhar_remocao(*_args):
        erro = HTTPException(status_code=500, detail="Segunda promocao nao removida")
        erro.favoritos_remocoes = [
            {"promotion_id": "PROMO-1", "success": True},
            {"promotion_id": "PROMO-2", "success": False},
        ]
        erro.favoritos_cfg = {"token": "renovado"}
        raise erro

    monkeypatch.setattr(favoritos_endpoints, "_favoritos_ml_remover_promocoes_atuais", _falhar_remocao)
    monkeypatch.setattr(
        favoritos_endpoints,
        "_favoritos_ml_atualizar_preco_item",
        lambda *_args: chamadas.append("preco") or ({}, {}),
    )
    monkeypatch.setattr(favoritos_endpoints, "_cache_invalidar_loja", lambda *_args: None)

    response = favoritos_endpoints.favoritos_ml_efetivar_promocao(_req(""), client_id="cliente")
    payload = _payload_json(response)

    assert response.status_code == 500
    assert payload["outcome"] == "partial_failure"
    assert payload["promocoes_removidas"] == [{"promotion_id": "PROMO-1", "success": True}]
    assert payload["stages"]["promotion_removal"]["status"] == "partial"
    assert chamadas == []


def test_helper_de_remocao_anexa_resultados_anteriores_ao_erro(monkeypatch):
    class _Response:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self._body = body
            self.text = json.dumps(body)
            self.headers = {}

        def json(self):
            return self._body

    respostas = iter([
        (_Response(204, {}), {"token": "apos-primeira"}),
        (_Response(500, {"message": "falha definitiva"}), {"token": "apos-segunda"}),
    ])
    monkeypatch.setattr(
        favoritos_ml,
        "_ml_obter_promocoes_item",
        lambda *_args: ([
            {"id": "PROMO-1", "status": "started", "promotion_type": "SELLER_CAMPAIGN"},
            {"id": "PROMO-2", "status": "started", "promotion_type": "SELLER_CAMPAIGN"},
        ], {}),
    )
    monkeypatch.setattr(favoritos_ml, "_ml_api_request", lambda *_args, **_kwargs: next(respostas))
    monkeypatch.setattr(favoritos_ml, "_favoritos_ml_remocao_max_attempts", lambda: 1)

    with pytest.raises(HTTPException) as capturado:
        favoritos_ml._favoritos_ml_remover_promocoes_atuais(
            "cliente",
            "JK Pecas",
            {},
            "MLB4741353185",
            _req(""),
        )

    erro = capturado.value
    assert [item["success"] for item in erro.favoritos_remocoes] == [True, False]
    assert erro.favoritos_cfg == {"token": "apos-segunda"}


def test_helper_de_remocao_preserva_resultados_quando_refresh_lanca_http_exception(monkeypatch):
    class _Response:
        status_code = 204
        text = ""
        headers = {}

        @staticmethod
        def json():
            return {}

    chamadas = 0

    def _request(*_args, **_kwargs):
        nonlocal chamadas
        chamadas += 1
        if chamadas == 1:
            return _Response(), {"token": "apos-primeira"}
        raise HTTPException(status_code=401, detail="Falha ao renovar acesso")

    monkeypatch.setattr(
        favoritos_ml,
        "_ml_obter_promocoes_item",
        lambda *_args: ([
            {"id": "PROMO-1", "status": "started", "promotion_type": "SELLER_CAMPAIGN"},
            {"id": "PROMO-2", "status": "started", "promotion_type": "SELLER_CAMPAIGN"},
        ], {}),
    )
    monkeypatch.setattr(favoritos_ml, "_ml_api_request", _request)
    monkeypatch.setattr(favoritos_ml, "_favoritos_ml_remocao_max_attempts", lambda: 1)

    with pytest.raises(HTTPException) as capturado:
        favoritos_ml._favoritos_ml_remover_promocoes_atuais(
            "cliente",
            "JK Pecas",
            {},
            "MLB4741353185",
            _req(""),
        )

    erro = capturado.value
    assert erro.status_code == 401
    assert [item["success"] for item in erro.favoritos_remocoes] == [True, False]
    assert erro.favoritos_cfg == {"token": "apos-primeira"}
