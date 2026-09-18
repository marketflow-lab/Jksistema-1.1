"""Synthetic send failures: no runtime setup, tenant data, or network access."""
import logging

import pytest
from fastapi import HTTPException

from backend.modules.perguntas_pos_venda.endpoints import manual_questions as api
from backend.schemas.perguntas_pos_venda import PerguntasEnviarRespostaRequest


@pytest.fixture
def sending(monkeypatch):
    calls = []
    config_calls = []
    cfg = {"_store_id_context": "Store-A"}

    def config(tenant, name, *, store_id, require_name_match):
        assert require_name_match is True
        config_calls.append((tenant, name, store_id))
        return cfg

    def send(*args):
        calls.append(args)
        return {"status": "ACTIVE"}, cfg

    monkeypatch.setattr(api, "obter_cfg_ml_snapshot", config)
    monkeypatch.setattr(api, "_obter_cfg_ml", lambda *a, **k: pytest.fail("canonical store maintenance must not run"))
    monkeypatch.setattr(api, "_perguntas_ia_enviar_resposta_ml", send)
    monkeypatch.setattr(api, "_perguntas_ia_resolver_aprovacoes_pendentes", lambda *a, **k: [])
    monkeypatch.setattr(api, "_perguntas_ia_memoria_registrar_resposta_aprovada", lambda *a, **k: True)
    monkeypatch.setattr(api, "_perguntas_ia_state_carregar", lambda *a: {})
    monkeypatch.setattr(api, "_perguntas_ia_marcar_processada", lambda *a: None)
    monkeypatch.setattr(api, "_perguntas_ia_state_salvar", lambda *a: None)
    monkeypatch.setattr(api, "logger", logging.getLogger(__name__))
    monkeypatch.setattr(api.perguntas_pos_venda_codex, "approve_or_refresh_proposal", lambda **k: {
        "job": {"store_id": "Store-A"}, "proposal_version": 1, "proposal_hash": "hash",
    })
    monkeypatch.setattr(api.perguntas_pos_venda_codex, "mark_verified", lambda **k: None)
    request = PerguntasEnviarRespostaRequest(
        loja="Fixture store", store_id="Store-A", question_id="question-fixture",
        resposta="Approved fixture answer.", proposal_id="proposal-fixture",
    )
    return request, calls, config_calls


@pytest.mark.parametrize("failure,expected", [
    ("verification", "local_verification_pending"),
    ("approvals", "local_approvals_pending"),
    ("state", "local_state_pending"),
])
def test_remote_confirmation_survives_local_bookkeeping_failure(monkeypatch, sending, failure, expected):
    request, sends, config_calls = sending

    def unavailable(*a, **k):
        raise RuntimeError("synthetic persistence outage")

    targets = {
        "verification": (api.perguntas_pos_venda_codex, "mark_verified"),
        "approvals": (api, "_perguntas_ia_resolver_aprovacoes_pendentes"),
        "state": (api, "_perguntas_ia_state_salvar"),
    }
    monkeypatch.setattr(*targets[failure], unavailable)
    result = api.ml_perguntas_responder_manual(request, "tenant-fixture")
    assert result["success"] is True
    assert result["warnings"] == [expected]
    assert len(sends) == 1
    assert config_calls == [("tenant-fixture", "Fixture store", "Store-A")]


def test_remote_failure_never_retries_post_or_masks_original_error(monkeypatch, sending):
    request, sends, _ = sending
    rejected = HTTPException(status_code=403, detail="remote denied")

    def send(*args):
        sends.append(args)
        raise rejected

    def verification(**kwargs):
        raise RuntimeError("synthetic local failure")

    monkeypatch.setattr(api, "_perguntas_ia_enviar_resposta_ml", send)
    monkeypatch.setattr(api.perguntas_pos_venda_codex, "mark_verified", verification)
    with pytest.raises(HTTPException) as error:
        api.ml_perguntas_responder_manual(request, "tenant-fixture")
    assert error.value is rejected
    assert len(sends) == 1


def test_store_contention_fails_before_proposal_approval_and_post(monkeypatch, sending):
    request, sends, _ = sending

    def busy(*a, **k):
        raise HTTPException(status_code=409, detail={"code": "stores_busy"})

    monkeypatch.setattr(api, "obter_cfg_ml_snapshot", busy)
    monkeypatch.setattr(api.perguntas_pos_venda_codex, "approve_or_refresh_proposal", lambda **k: pytest.fail("no approval on configuration failure"))
    with pytest.raises(HTTPException) as error:
        api.ml_perguntas_responder_manual(request, "tenant-fixture")
    assert error.value.detail["code"] == "stores_busy"
    assert sends == []


def test_proposal_from_another_exact_store_cannot_send(monkeypatch, sending):
    request, sends, _ = sending
    monkeypatch.setattr(api.perguntas_pos_venda_codex, "approve_or_refresh_proposal", lambda **k: {"job": {"store_id": "Store-B"}})
    with pytest.raises(HTTPException) as error:
        api.ml_perguntas_responder_manual(request, "tenant-fixture")
    assert error.value.status_code == 403
    assert sends == []


def test_legacy_request_without_store_id_remains_accepted(sending):
    request, sends, config_calls = sending
    request.store_id = ""
    request.proposal_id = ""
    result = api.ml_perguntas_responder_manual(request, "tenant-fixture")
    assert result["success"] is True
    assert len(sends) == 1
    assert config_calls[0][2] == ""
