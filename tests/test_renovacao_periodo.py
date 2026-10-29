"""Campaign dates use the Brazilian calendar on both writes and verification."""

import datetime as dt
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

from backend.services import renovacao_promocoes as operations
from backend.services import renovacao_promocoes_helpers as helpers


SOURCE = "C-MLB999100001"
DESTINATION = "C-MLB999100002"
CFG = {"user_id": "123", "site_id": "MLB"}


class Response:
    def __init__(self, status, payload):
        self.status_code = status
        self.payload = payload

    def json(self):
        return dict(self.payload)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected network request")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(helpers.ctx, "_ml_api_request", forbidden)
    monkeypatch.setattr(helpers.ctx, "_obter_cfg_ml", lambda *args: dict(CFG))
    monkeypatch.setattr(helpers.ctx, "_cache_invalidar_loja", lambda *args: None)
    monkeypatch.setattr(helpers.ctx, "_parse_float_flex", lambda value: float(value) if value is not None else None)
    monkeypatch.setattr(operations, "_renovacao_ml_listar_campanhas_usuario", lambda *args: ([], dict(CFG)))


@pytest.fixture
def clock(monkeypatch):
    def freeze(instant):
        fixed = dt.datetime.fromisoformat(instant.replace("Z", "+00:00"))

        class FrozenDateTime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

        monkeypatch.setattr(helpers, "dt", SimpleNamespace(
            datetime=FrozenDateTime, timedelta=dt.timedelta, timezone=dt.timezone,
        ))

    return freeze


@pytest.mark.parametrize("now,source,expected_start,expected_end", [
    ("2026-10-29T15:00:00Z", "2026-10-03T00:00:00", "2026-11-01", "2026-11-30"),
    ("2026-10-29T15:00:00Z", "2026-11-03T00:00:00", "2026-12-01", "2026-12-31"),
    ("2026-12-20T15:00:00Z", "2026-12-31T03:00:00Z", "2027-01-01", "2027-01-31"),
    ("2027-01-20T15:00:00Z", "2027-01-31", "2027-02-01", "2027-02-28"),
    ("2028-01-20T15:00:00Z", "2028-01-31", "2028-02-01", "2028-02-29"),
    ("2026-03-20T15:00:00Z", "2026-03-03", "2026-04-01", "2026-04-30"),
    ("2026-10-29T15:00:00Z", "2025-08-03", "2026-11-01", "2026-11-30"),
    ("2026-10-01T15:00:00Z", "2026-09-03", "2026-11-01", "2026-11-30"),
    ("2026-11-01T01:00:00Z", None, "2026-11-01", "2026-11-30"),
    ("2026-10-29T15:00:00Z", "2026-11-01T01:00:00Z", "2026-11-01", "2026-11-30"),
    ("2026-10-29T15:00:00Z", "invalid", "2026-11-01", "2026-11-30"),
])
def test_monthly_renewal_covers_entire_next_calendar_month(clock, now, source, expected_start, expected_end):
    clock(now)
    # The source duration and its day must not shorten or shift the target month.
    campaign = {"start_date": source, "finish_date": "2026-11-01T02:59:59Z"}
    assert helpers._renovacao_datas_proximo_mes(campaign) == (
        expected_start + "T00:00:00", expected_end + "T23:59:59",
    )


@pytest.mark.parametrize("requested,returned,expected", [
    ("2026-11-30T23:59:59", "2026-12-01T02:59:59Z", True),
    ("2026-11-30T23:59:59", "2026-11-30T23:59:59-03:00", True),
    ("2026-11-30T23:59:59", "2026-12-01T02:59:59.000+0000", True),
    ("2026-11-30T23:59:59", "2026-11-30T23:59:59", True),
    ("2026-11-01T00:00:00", "2026-11-01T03:00:00Z", True),
    ("2026-11-30T23:59:59", "2026-12-02T02:59:59Z", False),
    ("2026-11-01T00:00:00", "2026-11-03T03:00:00Z", False),
    ("2026-11-30T23:59:59", None, False),
])
def test_verification_compares_local_campaign_dates(requested, returned, expected):
    assert operations._renovacao_data_periodo_igual(requested, returned) is expected


def test_put_accepts_utc_confirmation_without_repeating_mutation(monkeypatch):
    calls, invalidations = [], []
    confirmed = {"id": DESTINATION, "status": "pending",
                 "start_date": "2026-11-01T03:00:00Z", "finish_date": "2026-12-01T02:59:59Z"}

    def request(client, store, cfg, method, url, **kwargs):
        calls.append((method, kwargs))
        return Response(200, confirmed), cfg

    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)
    monkeypatch.setattr(helpers.ctx, "_cache_invalidar_loja", lambda *args: invalidations.append(args))
    result = operations._renovacao_atualizar_periodo_campanha_ml(
        "tenant-test", "store-test", DESTINATION, "2026-11-01", "2026-11-30",
    )
    assert result["success"] is True
    assert [method for method, _ in calls] == ["PUT", "GET"]
    assert calls[0][1]["json"] == {
        "promotion_type": "SELLER_CAMPAIGN",
        "start_date": "2026-11-01T00:00:00", "finish_date": "2026-11-30T23:59:59",
    }
    assert result["campaign"] == confirmed
    assert invalidations == [("tenant-test", "store-test")]


def test_true_divergence_keeps_conflict_and_describes_local_dates(monkeypatch):
    confirmed = {"start_date": "2026-11-03T03:00:00Z", "finish_date": "2026-12-01T02:59:59Z"}
    monkeypatch.setattr(helpers.ctx, "_ml_api_request", lambda client, store, cfg, *a, **kw: (Response(200, confirmed), cfg))
    with pytest.raises(HTTPException) as caught:
        operations._renovacao_atualizar_periodo_campanha_ml(
            "tenant-test", "store-test", DESTINATION, "2026-11-01", "2026-11-30",
        )
    assert caught.value.status_code == 409
    assert "2026-11-03 ate 2026-11-30" in caught.value.detail
    assert "2026-12-01" not in caught.value.detail


@pytest.mark.parametrize("confirmed", [{}, {"start_date": "2026-11-01T03:00:00Z"}])
def test_missing_confirmation_is_not_reported_as_success(monkeypatch, confirmed):
    monkeypatch.setattr(helpers.ctx, "_ml_api_request", lambda client, store, cfg, *a, **kw: (Response(200, confirmed), cfg))
    with pytest.raises(HTTPException, match="Nao foi possivel confirmar") as caught:
        operations._renovacao_atualizar_periodo_campanha_ml(
            "tenant-test", "store-test", DESTINATION, "2026-11-01", "2026-11-30",
        )
    assert caught.value.status_code == 409


def test_started_campaign_sends_only_changed_finish_date(monkeypatch):
    calls = []
    confirmed = {"status": "started", "start_date": "2026-11-01T03:00:00Z",
                 "finish_date": "2026-12-01T02:59:59Z"}

    def request(client, store, cfg, method, url, **kwargs):
        calls.append((method, kwargs))
        return Response(200, confirmed), cfg

    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)
    result = operations._renovacao_atualizar_periodo_campanha_ml(
        "tenant-test", "store-test", DESTINATION, None, "2026-11-30",
    )
    assert result["success"] is True
    assert calls[0][1]["json"] == {"promotion_type": "SELLER_CAMPAIGN", "finish_date": "2026-11-30T23:59:59"}


def test_creation_sends_full_month_without_shortening(clock, monkeypatch):
    clock("2026-10-29T15:00:00Z")
    calls = []

    def request(client, store, cfg, method, url, **kwargs):
        calls.append((method, kwargs))
        return Response(201, {"id": DESTINATION}), cfg

    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)
    campaign = {"name": "Promo Novembro", "start_date": "2026-11-03T03:00:00Z"}
    name = helpers._renovacao_nome_proximo_mes(campaign)
    new_id, payload, _ = operations._renovacao_criar_campanha_ml("tenant-test", "store-test", CFG, name, campaign)
    assert new_id == DESTINATION
    assert payload["name"] == "Promo Dezembro"
    assert payload["start_date"] == "2026-12-01T00:00:00"
    assert payload["finish_date"] == "2026-12-31T23:59:59"
    assert calls == [("POST", {"params": {"app_version": "v2"}, "json": payload, "timeout": 30})]


@pytest.mark.parametrize("confirmation,can_copy", [
    ("full_month", True), ("aliases", True), ("short_month", False), ("unavailable", False), ("timeout", False),
])
def test_monthly_creation_confirms_dates_before_copying(clock, monkeypatch, confirmation, can_copy):
    clock("2026-10-29T15:00:00Z")
    source = {"id": SOURCE, "name": "Promo Novembro", "start_date": "2026-11-03T03:00:00Z",
              "active_count": 0, "programmed_count": 1}
    calls, copies = [], []
    monkeypatch.setattr(operations, "_renovacao_ml_listar_campanhas_usuario", lambda *args: ([source], CFG))
    monkeypatch.setattr(operations, "_renovacao_listar_itens_campanha", lambda *args, **kwargs: ([{"id": "MLB999"}], {}, CFG))

    def request(client, store, cfg, method, url, **kwargs):
        calls.append(method)
        if method == "POST":
            assert kwargs["json"]["start_date"] == "2026-12-01T00:00:00"
            assert kwargs["json"]["finish_date"] == "2026-12-31T23:59:59"
            return Response(201, {"id": DESTINATION}), cfg
        assert method == "GET" and url.endswith("/" + DESTINATION)
        if confirmation == "timeout":
            raise requests.Timeout("offline timeout")
        if confirmation == "unavailable":
            return Response(503, {}), cfg
        if confirmation == "aliases":
            return Response(200, {
                "id": DESTINATION, "date_start": "2026-12-01T03:00:00Z", "date_end": "2027-01-01T02:59:59Z",
            }), cfg
        return Response(200, {
            "id": DESTINATION, "start_date": "2026-12-01T03:00:00Z",
            "finish_date": "2027-01-01T02:59:59Z" if can_copy else "2026-12-15T02:59:59Z",
        }), cfg

    def copy(*args, **kwargs):
        copies.append(args)
        return {"total_origem": 1}, CFG

    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)
    monkeypatch.setattr(operations, "_renovacao_copiar_mlbs_campanha", copy)
    if can_copy:
        result = operations._renovacao_criar_ou_completar_proximo_mes("tenant-test", "store-test", SOURCE)
        assert result["success"] is True
        assert len(copies) == 1
    else:
        with pytest.raises(HTTPException) as caught:
            operations._renovacao_criar_ou_completar_proximo_mes("tenant-test", "store-test", SOURCE)
        assert caught.value.status_code == 409
        assert DESTINATION in caught.value.detail
        assert "foi criada" in caught.value.detail
        assert "Nenhum anuncio foi copiado" in caught.value.detail
        assert copies == []
    assert calls == ["POST", "GET"]


@pytest.mark.parametrize("start,finish,can_copy", [
    ("2026-11-01T03:00:00Z", "2026-12-01T02:59:59Z", True),
    ("2026-11-01T03:00:00Z", "2026-11-15T02:59:59Z", False),
    ("2026-11-01T03:00:00Z", None, False),
    ("2026-11-01T15:00:00Z", "2026-12-01T02:59:59Z", False),
    ("2026-11-01T03:00:00Z", "2026-11-30T03:00:00Z", False),
])
def test_existing_destination_must_cover_full_month(clock, monkeypatch, start, finish, can_copy):
    clock("2026-10-29T15:00:00Z")
    campaigns = [
        {"id": SOURCE, "name": "Promo Outubro", "start_date": "2026-10-03T03:00:00Z",
         "active_count": 1, "programmed_count": 0},
        {"id": DESTINATION, "name": "Promo Novembro", "start_date": start,
         "finish_date": finish},
    ]
    copies = []
    monkeypatch.setattr(operations, "_renovacao_ml_listar_campanhas_usuario", lambda *args: (campaigns, CFG))
    monkeypatch.setattr(operations, "_renovacao_listar_itens_campanha", lambda *args, **kwargs: ([{"id": "MLB999"}], {}, CFG))

    def copy(*args, **kwargs):
        copies.append(args)
        return {"total_origem": 1}, CFG

    monkeypatch.setattr(operations, "_renovacao_copiar_mlbs_campanha", copy)
    if can_copy:
        result = operations._renovacao_criar_ou_completar_proximo_mes("tenant-test", "store-test", SOURCE)
        assert result["success"] is True
        assert result["campanha_existente"] is True
        assert len(copies) == 1
    else:
        with pytest.raises(HTTPException, match="2026-11-01 ate 2026-11-30") as caught:
            operations._renovacao_criar_ou_completar_proximo_mes("tenant-test", "store-test", SOURCE)
        assert caught.value.status_code == 409
        assert copies == []
