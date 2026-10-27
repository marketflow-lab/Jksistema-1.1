"""Offline coverage for seller campaign types and actionable ML errors."""

import copy
import unicodedata

import pytest
import requests
from fastapi import HTTPException

from backend.services import renovacao_promocoes as promotions
from backend.services import renovacao_promocoes_helpers as helpers


TENANT = "tenant-renovacao-tipos-test"
STORE = "loja-renovacao-tipos-test"
SOURCE = "C-MLB999100001"
DESTINATION = "C-MLB999100002"
CFG = {"user_id": "123", "site_id": "MLB"}
START = "2030-11-01T00:00:00"
FINISH = "2030-11-16T23:59:59"


class Response:
    def __init__(self, status, body):
        self.status_code = status
        self.body = body

    def json(self):
        return copy.deepcopy(self.body)


def parse_float(value):
    if value is None or value == "":
        return None
    return float(str(value).replace(",", "."))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected network request")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(helpers.ctx, "_ml_api_request", forbidden)
    monkeypatch.setattr(helpers.ctx, "_obter_cfg_ml", lambda *args: dict(CFG))
    monkeypatch.setattr(helpers.ctx, "_parse_float_flex", parse_float)
    monkeypatch.setattr(helpers.ctx, "_cache_invalidar_loja", lambda *args: None)
    monkeypatch.setattr(
        helpers.ctx, "_ml_parse_error_detail", lambda response, fallback: response.json().get("message", fallback)
    )
    monkeypatch.setattr(promotions, "_renovacao_remover_agendamento_campanha", lambda *args: None)


@pytest.mark.parametrize("campaign,kind,expected", [
    (SOURCE, "seller_campaign", "SELLER_CAMPAIGN"),
    (SOURCE, "DEAL", "SELLER_CAMPAIGN"),
    (SOURCE, "FLEXIBLE_PERCENTAGE", "SELLER_CAMPAIGN"),
    (SOURCE.lower(), "DEAL", "SELLER_CAMPAIGN"),
    (SOURCE, None, "SELLER_CAMPAIGN"),
    ("P-MLB999100001", " deal ", "DEAL"),
    ("", None, "SELLER_CAMPAIGN"),
])
def test_campaign_type_is_canonical_before_requests(campaign, kind, expected):
    assert helpers._renovacao_tipo_campanha(campaign, kind) == expected


@pytest.mark.parametrize("raw_type", ["seller_campaign", "DEAL", "FLEXIBLE_PERCENTAGE", ""])
def test_seller_campaign_list_uses_canonical_type_for_counts_and_frontend(monkeypatch, raw_type):
    counts = []
    raw = {"id": SOURCE, "name": "Origem", "type": raw_type,
           "status": "started", "programmed_count": 0}

    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store, method) == (TENANT, STORE, "GET")
        assert url == "https://api.mercadolibre.com/seller-promotions/users/123"
        return Response(200, {"results": [raw]}), cfg

    def count(client, store, cfg, campaign, kind, status):
        assert (client, store, campaign) == (TENANT, STORE, SOURCE)
        assert status in {"started", "pending"}
        counts.append((kind, status))
        return (908 if status == "started" else 194), cfg

    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)
    monkeypatch.setattr(helpers.ctx, "_ml_contar_itens_promocao_status", count)
    monkeypatch.setattr(
        helpers.ctx, "_ml_extrair_contagem_campanha",
        lambda obj, keys: next((obj[key] for key in keys if key in obj), None),
    )
    campaigns, cfg = helpers._renovacao_ml_listar_campanhas_usuario(TENANT, STORE)

    assert counts == [("SELLER_CAMPAIGN", "started"), ("SELLER_CAMPAIGN", "pending")]
    assert campaigns[0]["promotion_type"] == "SELLER_CAMPAIGN"
    assert campaigns[0]["type"] == "SELLER_CAMPAIGN"
    assert campaigns[0]["active_count"] == 908
    assert campaigns[0]["programmed_count"] == 194
    assert helpers._renovacao_contagem_esperada(campaigns[0]) == 1102
    assert cfg == CFG
    assert raw["type"] == raw_type


def test_period_put_includes_required_type_in_body_and_query(monkeypatch):
    sent = []

    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store, method) == (TENANT, STORE, "PUT")
        assert url == f"https://api.mercadolibre.com/seller-promotions/promotions/{DESTINATION}"
        sent.append(copy.deepcopy(kwargs))
        return Response(200, {"start_date": START, "finish_date": FINISH}), cfg

    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)
    monkeypatch.setattr(
        promotions, "_renovacao_buscar_campanha_ml",
        lambda client, store, cfg, campaign, kind: ({"start_date": START, "finish_date": FINISH}, cfg),
    )
    result = promotions._renovacao_atualizar_periodo_campanha_ml(
        TENANT, STORE, DESTINATION, "2030-11-01", "2030-11-16", "DEAL",
    )

    assert result["success"] is True
    assert len(sent) == 1
    assert sent[0]["params"]["promotion_type"] == "SELLER_CAMPAIGN"
    assert sent[0]["json"] == {
        "promotion_type": "SELLER_CAMPAIGN", "start_date": START, "finish_date": FINISH,
    }


def test_delete_uses_canonical_type_for_seller_campaign(monkeypatch):
    sent = []

    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store, method) == (TENANT, STORE, "DELETE")
        assert url.endswith(f"/promotions/{DESTINATION}")
        sent.append(kwargs["params"])
        return Response(204, {}), cfg

    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)
    result = promotions._renovacao_deletar_campanha_ml(TENANT, STORE, DESTINATION, "DEAL")

    assert result["deleted"] is True
    assert sent == [{"app_version": "v2", "promotion_type": "SELLER_CAMPAIGN"}]


@pytest.mark.parametrize("flow", ["monthly", "sync"])
def test_copy_paths_normalize_both_campaign_types(monkeypatch, flow):
    campaigns = [
        {"id": SOURCE, "name": "Origem", "type": "DEAL", "promotion_type": "DEAL",
         "active_count": 908, "programmed_count": 0},
        {"id": DESTINATION, "name": "Destino", "type": "FLEXIBLE_PERCENTAGE",
         "promotion_type": "FLEXIBLE_PERCENTAGE", "start_date": START, "finish_date": FINISH},
    ]
    copy_calls = []
    preflight_calls = []
    monkeypatch.setattr(promotions, "_renovacao_ml_listar_campanhas_usuario", lambda *args: (campaigns, dict(CFG)))
    monkeypatch.setattr(promotions, "_renovacao_datas_proximo_mes", lambda *args: (START, FINISH))
    monkeypatch.setattr(promotions, "_renovacao_encontrar_campanha_destino", lambda *args: campaigns[1])

    def source_preflight(client, store, cfg, campaign, kind, **kwargs):
        assert (client, store, campaign, kind) == (TENANT, STORE, SOURCE, "SELLER_CAMPAIGN")
        assert kwargs["expected_count"] == 908
        preflight_calls.append(campaign)
        items = [{"id": f"MLB{999200000 + index}"} for index in range(908)]
        return items, {}, cfg

    monkeypatch.setattr(promotions, "_renovacao_listar_itens_campanha", source_preflight)

    def copy_campaign(*args, **kwargs):
        copy_calls.append((args, kwargs))
        return {"total_origem": 908, "incluidos": 908, "atualizados": 0,
                "mlbs_ja_presentes": 0, "faltantes": 908, "falhas": [], "falhas_total": 0}, args[2]

    monkeypatch.setattr(promotions, "_renovacao_copiar_mlbs_campanha", copy_campaign)
    if flow == "monthly":
        result = promotions._renovacao_criar_ou_completar_proximo_mes(TENANT, STORE, SOURCE, "Destino", "DEAL")
    else:
        result = promotions._renovacao_sincronizar_promocao_existente(
            TENANT, STORE, SOURCE, DESTINATION, "DEAL", "FLEXIBLE_PERCENTAGE",
        )

    assert result["success"] is True
    assert preflight_calls == ([SOURCE] if flow == "monthly" else [])
    assert len(copy_calls) == 1
    args, kwargs = copy_calls[0]
    assert args[:2] == (TENANT, STORE)
    assert args[3:7] == (SOURCE, DESTINATION, "SELLER_CAMPAIGN", "SELLER_CAMPAIGN")


@pytest.mark.parametrize("action,context_words", [
    ("list", ("listar", "consultar")),
    ("create", ("criar", "criacao")),
    ("period", ("periodo",)),
    ("delete", ("deletar", "excluir", "exclusao")),
])
def test_ml_invalid_type_errors_keep_status_and_identify_operation(monkeypatch, action, context_words):
    monkeypatch.setattr(
        helpers.ctx, "_ml_api_request",
        lambda client, store, cfg, *args, **kwargs: (Response(400, {"message": "Invalid promotion type"}), cfg),
    )

    with pytest.raises(HTTPException) as caught:
        if action == "list":
            helpers._renovacao_ml_listar_campanhas_usuario(TENANT, STORE)
        elif action == "create":
            promotions._renovacao_criar_campanha_ml(TENANT, STORE, dict(CFG), "Destino", {}, (START, FINISH))
        elif action == "period":
            promotions._renovacao_atualizar_periodo_campanha_ml(
                TENANT, STORE, DESTINATION, "2030-11-01", "2030-11-16", "SELLER_CAMPAIGN",
            )
        else:
            promotions._renovacao_deletar_campanha_ml(TENANT, STORE, DESTINATION, "SELLER_CAMPAIGN")

    assert caught.value.status_code == 400
    detail = str(caught.value.detail)
    assert "Invalid promotion type" in detail
    readable = unicodedata.normalize("NFKD", detail).encode("ascii", "ignore").decode().lower()
    assert any(word in readable for word in context_words)
    assert "campanh" in readable
