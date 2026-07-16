from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from backend.services import promocoes_api_jobs
from backend.services import promocoes_common


class _HealthResponse:
    ok = True
    content = b"{}"

    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload


def test_promo_worker_health_requires_matching_protocol_and_app_version(monkeypatch):
    monkeypatch.setenv("JK_APP_VERSION", "1.0.94")

    monkeypatch.setattr(
        promocoes_common.requests,
        "get",
        lambda *args, **kwargs: _HealthResponse(
            {"ok": True, "protocolVersion": promocoes_common.PROMO_WORKER_PROTOCOL_VERSION, "appVersion": "1.0.94"}
        ),
    )
    assert promocoes_common._promo_worker_healthcheck() is True

    monkeypatch.setattr(
        promocoes_common.requests,
        "get",
        lambda *args, **kwargs: _HealthResponse(
            {"ok": True, "protocolVersion": promocoes_common.PROMO_WORKER_PROTOCOL_VERSION, "appVersion": "1.0.93"}
        ),
    )
    available, compatible, payload = promocoes_common._promo_worker_health_state()
    assert available is True
    assert compatible is False
    assert payload["appVersion"] == "1.0.93"


def test_promo_worker_health_rejects_legacy_health_payload(monkeypatch):
    monkeypatch.setenv("JK_APP_VERSION", "1.0.94")
    monkeypatch.setattr(
        promocoes_common.requests,
        "get",
        lambda *args, **kwargs: _HealthResponse({"ok": True}),
    )

    available, compatible, _ = promocoes_common._promo_worker_health_state()
    assert available is True
    assert compatible is False


def test_promo_start_converts_unexpected_error_to_json_http_error(monkeypatch):
    def _fail_start(**_kwargs):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(promocoes_api_jobs, "_promo_start_api_worker_job", _fail_start)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            promocoes_api_jobs.iniciar_analise_promo_via_api(
                loja="JK Pecas",
                promocao_a_id="C-TEST",
                promocao_a_type="SMART",
                margem_minima=15.0,
                promocoes_b_meta="[]",
                client_id="000002",
            )
        )

    assert exc_info.value.status_code == 503
    assert "Nao foi possivel iniciar a analise de promocoes" in str(exc_info.value.detail)
