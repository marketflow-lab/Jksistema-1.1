from __future__ import annotations

import inspect
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from fastapi.params import Depends
from pydantic import ValidationError

from backend.modules.perguntas_pos_venda.endpoints import api, jobs
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.routers.perguntas_pos_venda import create_perguntas_pos_venda_router
from backend.schemas import PerguntasAssistantDraftPatchRequest
from backend.services import codex_assistant_storage
from backend.services import perguntas_pos_venda_codex as orchestrator
from backend.services.perguntas_pos_venda_state import ML_RESPOSTA_PERGUNTA_MAX_CHARS
from backend.services.codex.storage import customer_reply_state


ROUTE = "/api/mercadolivre/assistant/jobs/{job_id}/draft"


def _request(tenant: str = "tenant-a") -> Request:
    request = Request({"type": "http", "method": "PATCH", "path": ROUTE})
    request.state.username = "operator"
    request.state.client_id = tenant
    return request


def _job(**overrides):
    result = {
        "resposta": "Resposta original.",
        "proposal_id": "job-draft",
        "proposal_version": 1,
        "proposal_hash": "hash-original",
        "data_sufficient": True,
        "requires_approval": True,
        "publish_attempted": False,
        "draft_source": "ai",
    }
    values = {
        "job_id": "job-draft",
        "profile": orchestrator.PROFILE,
        "client_id": "tenant-a",
        "task_type": orchestrator.TASK_TYPE_PUBLIC_QUESTION,
        "subject_key": "question:Q-1",
        "event_subject_key": "Q-1",
        "question_id": "Q-1",
        "item_id": "MLB-1",
        "store": "Loja A",
        "store_id": "store-a",
        "seller_id": "seller-a",
        "site_id": "MLB",
        "status": "completed",
        "agent_state": "aguardando_aprovacao",
        "current_step": "aprovar",
        "prompt_version": orchestrator.PROMPT_VERSION,
        "prompt_hash": orchestrator.PROMPT_HASH,
        "schema_version": orchestrator.SCHEMA_VERSION,
        "queue_policy_version": orchestrator.QUEUE_POLICY_VERSION,
        "proposal_id": "job-draft",
        "proposal_version": 1,
        "proposal_hash": "hash-original",
        "result": result,
    }
    values.update(overrides)
    if "result" not in overrides:
        values["result"] = result
    return values


@pytest.fixture
def draft_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", SimpleNamespace(PASTA_INFO=str(tmp_path)))
    monkeypatch.setattr(customer_reply_state, "_customer_reply_sealed_results_enabled", lambda: True)
    monkeypatch.setattr(
        customer_reply_state,
        "_customer_reply_result_key",
        lambda _client_id, *, create: b"D" * 32,
    )
    monkeypatch.setattr(
        jobs.questions_loading_support,
        "authorized_stores",
        lambda request, client_id: [
            {
                "store_id": "store-a",
                "nome": "Loja A",
                "integracoes": {
                    "mercadolivre": {"user_id": "seller-a", "site_id": "MLB"}
                },
            }
        ]
        if request.state.client_id == client_id and request.state.username
        else [],
    )
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "tenant-a", _job()
    )
    return tmp_path


def _patch(**overrides):
    data = {
        "store_id": "store-a",
        "question_id": "Q-1",
        "resposta": "Resposta revisada pelo operador.",
        "expected_proposal_version": 1,
        "expected_proposal_hash": "hash-original",
    }
    data.update(overrides)
    tenant = data.pop("tenant", "tenant-a")
    return api.ml_customer_reply_job_draft_patch(
        job_id="job-draft",
        payload=PerguntasAssistantDraftPatchRequest(**data),
        request=_request(tenant),
        client_id=tenant,
    )


def test_draft_patch_route_uses_authenticated_tenant_dependency() -> None:
    handler = api.ml_customer_reply_job_draft_patch
    parameter = inspect.signature(handler).parameters["client_id"]

    assert isinstance(parameter.default, Depends)
    assert parameter.default.dependency is get_tenant_id
    assert any(
        route.path == ROUTE
        and route.name == "ml_customer_reply_job_draft_patch"
        and route.methods == {"PATCH"}
        for route in create_perguntas_pos_venda_router().routes
    )


def test_patch_persists_a_sealed_revision_and_returns_closed_payload(draft_runtime) -> None:
    response = _patch()

    assert set(response) == {
        "job_id",
        "store_id",
        "question_id",
        "proposal_id",
        "proposal_version",
        "proposal_hash",
        "resposta",
        "draft_updated_at",
        "draft_cleared",
    }
    assert response["proposal_version"] == 2
    assert response["proposal_hash"] != "hash-original"
    assert response["resposta"] == "Resposta revisada pelo operador."
    assert response["draft_cleared"] is False

    with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
        customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()
    recovered = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(draft_runtime), "tenant-a", "job-draft"
    )
    assert recovered["result"]["resposta"] == response["resposta"]
    assert recovered["result"]["proposal_hash"] == response["proposal_hash"]
    assert recovered["result"]["draft_cleared"] is False


def test_empty_patch_persists_tombstone_and_cannot_restore_old_answer(draft_runtime) -> None:
    response = _patch(resposta="   ")

    assert response["resposta"] == ""
    assert response["draft_cleared"] is True
    assert response["proposal_version"] == 2

    with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
        customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()
    recovered = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(draft_runtime), "tenant-a", "job-draft"
    )
    assert recovered["draft_cleared"] is True
    assert recovered["proposal_hash"] == response["proposal_hash"]
    assert "result" not in recovered

    public = orchestrator.get_job("tenant-a", "job-draft")
    assert public["draft_cleared"] is True
    assert public["result"] == {}
    assert public["blocked_without_draft"] is False

    restored = _patch(
        resposta="Nova resposta depois da limpeza.",
        expected_proposal_version=response["proposal_version"],
        expected_proposal_hash=response["proposal_hash"],
    )
    assert restored["proposal_version"] == response["proposal_version"] + 1
    assert restored["draft_cleared"] is False
    assert restored["resposta"] == "Nova resposta depois da limpeza."


def test_stale_proposal_is_rejected_without_overwriting_newer_revision(draft_runtime) -> None:
    first = _patch(resposta="Primeira revisao.")

    same_current = _patch(
        resposta="Primeira revisao.",
        expected_proposal_version=first["proposal_version"],
        expected_proposal_hash=first["proposal_hash"],
    )
    assert same_current["proposal_version"] == first["proposal_version"]
    assert same_current["proposal_hash"] == first["proposal_hash"]

    replay = _patch(resposta="Primeira revisao.")
    assert replay["proposal_version"] == first["proposal_version"]
    assert replay["proposal_hash"] == first["proposal_hash"]

    with pytest.raises(HTTPException) as caught:
        _patch(resposta="Revisao concorrente.")

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "proposal_conflict"
    assert caught.value.detail["proposal_version"] == first["proposal_version"]
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(draft_runtime), "tenant-a", "job-draft"
    )
    assert stored["result"]["resposta"] == "Primeira revisao."


def test_public_question_limit_is_enforced_by_schema_and_service(draft_runtime) -> None:
    oversized = "x" * (ML_RESPOSTA_PERGUNTA_MAX_CHARS + 1)
    with pytest.raises(ValidationError):
        PerguntasAssistantDraftPatchRequest(
            store_id="store-a",
            question_id="Q-1",
            resposta=oversized,
            expected_proposal_version=1,
            expected_proposal_hash="hash-original",
        )

    with pytest.raises(ValueError, match=str(ML_RESPOSTA_PERGUNTA_MAX_CHARS)):
        orchestrator.revise_proposal_draft(
            client_id="tenant-a",
            job_id="job-draft",
            store_id="store-a",
            seller_id="seller-a",
            site_id="MLB",
            question_id="Q-1",
            answer=oversized,
            expected_proposal_version=1,
            expected_proposal_hash="hash-original",
        )


def test_cross_tenant_job_is_not_visible(draft_runtime) -> None:
    with pytest.raises(HTTPException) as caught:
        _patch(tenant="tenant-b")

    assert caught.value.status_code == 404


def test_expired_draft_cannot_be_recreated_by_patch(draft_runtime, monkeypatch) -> None:
    expired_at = time.time() + customer_reply_state._CUSTOMER_REPLY_SEALED_RESULT_TTL_SECONDS + 1
    monkeypatch.setattr(customer_reply_state.time, "time", lambda: expired_at)
    with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
        customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()

    with pytest.raises(HTTPException) as caught:
        _patch()

    assert caught.value.status_code == 409
    assert "expirou" in str(caught.value.detail).lower()


def test_already_sent_proposal_is_not_editable(draft_runtime) -> None:
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(draft_runtime), "tenant-a", "job-draft"
    )
    stored["agent_state"] = "concluido"
    stored["result"]["publish_attempted"] = True
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(draft_runtime), "tenant-a", stored
    )

    with pytest.raises(HTTPException) as caught:
        _patch()

    assert caught.value.status_code == 409


def test_homonymous_store_cannot_cross_exact_store_scope(draft_runtime, monkeypatch) -> None:
    monkeypatch.setattr(
        jobs.questions_loading_support,
        "authorized_stores",
        lambda *_args: [
            {
                "store_id": "store-a",
                "nome": "Loja Homonima",
                "integracoes": {"mercadolivre": {"user_id": "seller-a", "site_id": "MLB"}},
            },
            {
                "store_id": "store-b",
                "nome": "Loja Homonima",
                "integracoes": {"mercadolivre": {"user_id": "seller-b", "site_id": "MLB"}},
            },
        ],
    )

    with pytest.raises(HTTPException) as caught:
        _patch(store_id="store-b")

    assert caught.value.status_code == 403


@pytest.mark.parametrize(
    ("job_patch", "request_patch", "status_code"),
    [
        ({"seller_id": "seller-other"}, {}, 403),
        ({"site_id": "MLA"}, {}, 403),
        ({}, {"question_id": "Q-OTHER"}, 403),
        ({"prompt_hash": "contract-old"}, {}, 409),
    ],
)
def test_patch_validates_store_identity_question_and_contract(
    tmp_path, monkeypatch, job_patch, request_patch, status_code
) -> None:
    monkeypatch.setattr(orchestrator, "_RUNTIME", SimpleNamespace(PASTA_INFO=str(tmp_path)))
    monkeypatch.setattr(
        jobs.questions_loading_support,
        "authorized_stores",
        lambda _request, _client_id: [
            {
                "store_id": "store-a",
                "nome": "Loja A",
                "integracoes": {
                    "mercadolivre": {"user_id": "seller-a", "site_id": "MLB"}
                },
            }
        ],
    )
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "tenant-a", _job(**job_patch)
    )

    with pytest.raises(HTTPException) as caught:
        _patch(**request_patch)

    assert caught.value.status_code == status_code


def test_patch_rejects_store_outside_authenticated_catalog(draft_runtime, monkeypatch) -> None:
    monkeypatch.setattr(jobs.questions_loading_support, "authorized_stores", lambda *_args: [])

    with pytest.raises(HTTPException) as caught:
        _patch()

    assert caught.value.status_code == 403
