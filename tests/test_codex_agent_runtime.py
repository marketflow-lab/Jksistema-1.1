from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.services import codex_actions, codex_agent_runtime, codex_assistant_storage


def test_plan_is_idempotent_and_persists_state(tmp_path: Path) -> None:
    key = codex_agent_runtime.make_idempotency_key(
        client_id="cliente",
        username="admin",
        channel="app",
        conversation_id="conv-1",
        conversation_generation=1,
        request_id="request-1",
    )
    first = codex_agent_runtime.create_plan(
        str(tmp_path),
        "cliente",
        task_id="task-1",
        conversation_id="conv-1",
        conversation_generation=1,
        username="admin",
        channel="app",
        message="sincronize as vendas",
        mutable=True,
        idempotency_key=key,
    )
    second = codex_agent_runtime.create_plan(
        str(tmp_path),
        "cliente",
        task_id="task-duplicada",
        conversation_id="conv-1",
        conversation_generation=1,
        username="admin",
        channel="app",
        message="sincronize as vendas",
        mutable=True,
        idempotency_key=key,
    )
    assert second["plan_id"] == first["plan_id"]
    assert second["task_id"] == "task-1"

    updated = codex_agent_runtime.transition_plan(
        str(tmp_path),
        "cliente",
        first["plan_id"],
        "aguardando_aprovacao",
        current_step="aprovar",
        proposal={"proposal_id": "prop-1"},
    )
    assert updated["agent_state"] == "aguardando_aprovacao"
    assert updated["proposal"]["proposal_id"] == "prop-1"
    loaded = codex_assistant_storage.codex_assistant_agent_plan_get(
        str(tmp_path), "cliente", first["plan_id"]
    )
    assert loaded and loaded["current_step"] == "aprovar"


def test_guidance_versions_and_precedence(tmp_path: Path) -> None:
    base = str(tmp_path)
    codex_agent_runtime.save_guidance(
        base,
        "cliente",
        {"scope_type": "global", "text": "Regra global"},
        updated_by="admin",
    )
    first_module = codex_agent_runtime.save_guidance(
        base,
        "cliente",
        {"scope_type": "module", "scope_key": "vendas", "text": "Regra antiga"},
        updated_by="admin",
    )
    latest_module = codex_agent_runtime.save_guidance(
        base,
        "cliente",
        {"scope_type": "module", "scope_key": "vendas", "text": "Regra atual"},
        updated_by="admin",
    )
    codex_agent_runtime.save_guidance(
        base,
        "cliente",
        {"scope_type": "store", "scope_key": "JK Peças", "text": "Regra da loja"},
        updated_by="admin",
    )
    codex_agent_runtime.save_guidance(
        base,
        "cliente",
        {"scope_type": "sku", "scope_key": "ABC-1", "text": "Regra do SKU"},
        updated_by="admin",
    )

    resolved = codex_agent_runtime.resolve_guidance(
        base,
        "cliente",
        context={"module": "vendas", "store": "JK Peças", "sku": "ABC-1"},
    )
    configured = [item for item in resolved if item.get("source") == "agent_settings"]
    assert [item["scope_type"] for item in configured] == ["global", "module", "store", "sku"]
    assert latest_module["version"] == first_module["version"] + 1
    assert "Regra atual" in codex_agent_runtime.guidance_prompt(resolved)
    assert "Regra antiga" not in codex_agent_runtime.guidance_prompt(resolved)


def test_action_contracts_enforce_channel_and_sensitive_rules() -> None:
    whatsapp_action = codex_agent_runtime.enrich_action_contract(
        {
            "id": "ml.pergunta_responder",
            "module": "perguntas_pos_venda",
            "executor": "ml_pergunta_responder",
            "can_execute": True,
            "risk_level": "external_write",
        }
    )
    sensitive_action = codex_agent_runtime.enrich_action_contract(
        {
            "id": "usuarios.excluir",
            "module": "usuarios",
            "executor": "generic_route",
            "can_execute": False,
            "risk_level": "destructive",
        }
    )
    assert whatsapp_action["channels_allowed"] == ["app", "whatsapp"]
    assert whatsapp_action["requires_confirmation"] is True
    assert sensitive_action["operational_class"] == "app_only"
    assert sensitive_action["channels_allowed"] == ["app"]


class _NoStartThread:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def start(self) -> None:
        return None


def _save_test_proposal(tmp_path: Path, *, proposal_id: str, channels: list[str]) -> dict:
    proposal = {
        "proposal_id": proposal_id,
        "version": 1,
        "client_id": "cliente",
        "created_by": "admin",
        "status": "awaiting_approval",
        "expires_at": "2099-01-01T00:00:00Z",
        "action_id": "ml.pergunta_responder",
        "action": {
            "id": "ml.pergunta_responder",
            "label": "Responder pergunta",
            "executor": "ml_pergunta_responder",
        },
        "params": {"question_id": "123", "resposta": "Resposta"},
        "can_execute": True,
        "channels_allowed": channels,
        "preconditions": ["pergunta ainda aberta"],
        "postconditions": ["resposta confirmada na API"],
    }
    proposal["proposal_hash"] = codex_agent_runtime.proposal_hash(proposal)
    return codex_assistant_storage.codex_assistant_action_proposal_save(
        str(tmp_path), "cliente", proposal
    )


def test_proposal_hash_channel_and_idempotent_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_actions, "PASTA_INFO", str(tmp_path), raising=False)
    monkeypatch.setattr(codex_actions.threading, "Thread", _NoStartThread)
    proposal = _save_test_proposal(tmp_path, proposal_id="prop-ok", channels=["app", "whatsapp"])

    with pytest.raises(HTTPException) as changed:
        codex_actions.approve_proposal(
            "prop-ok",
            username="admin",
            client_id="cliente",
            authorization=None,
            source="whatsapp",
            wa_id="5511999999999",
            proposal_version=1,
            proposal_hash="hash-incorreto",
        )
    assert changed.value.status_code == 409

    approved = codex_actions.approve_proposal(
        "prop-ok",
        username="admin",
        client_id="cliente",
        authorization=None,
        source="whatsapp",
        wa_id="5511999999999",
        proposal_version=1,
        proposal_hash=proposal["proposal_hash"],
    )
    replay = codex_actions.approve_proposal(
        "prop-ok",
        username="admin",
        client_id="cliente",
        authorization=None,
        source="whatsapp",
        wa_id="5511999999999",
        proposal_version=1,
        proposal_hash=proposal["proposal_hash"],
    )
    assert approved["run"]["run_id"] == replay["run"]["run_id"]
    assert replay["idempotent_replay"] is True

    app_only = _save_test_proposal(tmp_path, proposal_id="prop-app", channels=["app"])
    with pytest.raises(HTTPException) as wrong_channel:
        codex_actions.approve_proposal(
            "prop-app",
            username="admin",
            client_id="cliente",
            authorization=None,
            source="whatsapp",
            proposal_version=1,
            proposal_hash=app_only["proposal_hash"],
        )
    assert wrong_channel.value.status_code == 409


def test_capability_inventory_is_fully_classified() -> None:
    coverage = codex_agent_runtime.capability_coverage(
        actions=[
            {"id": "config.excluir", "module": "configuracoes", "executor": "generic_route", "can_execute": False},
            {"id": "vendas.sync_periodo", "module": "vendas", "executor": "vendas_sync", "can_execute": True},
        ],
        data_tools=[{"id": "mercado_livre_orders", "module": "vendas"}],
    )
    assert coverage["total"] == coverage["classified"] == 3
    assert coverage["coverage_percent"] == 100.0
    assert all(item.get("operational_class") for item in coverage["contracts"])


def test_audit_redacts_credentials_and_personal_data() -> None:
    redacted = codex_agent_runtime.redact_sensitive(
        {
            "authorization": "Bearer secret",
            "wa_id": "5511999999999",
            "nested": {"email": "cliente@example.com", "message": "Bearer abc.def"},
        }
    )
    assert redacted["authorization"] == "[redigido]"
    assert redacted["wa_id"] == "[redigido]"
    assert redacted["nested"]["email"] == "[redigido]"
    assert "abc.def" not in redacted["nested"]["message"]
