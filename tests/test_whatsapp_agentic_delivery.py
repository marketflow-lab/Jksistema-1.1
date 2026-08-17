from __future__ import annotations

from backend.services.whatsapp import delivery
from backend.services.whatsapp.orchestration import pending as pending_component
from backend.services.whatsapp.runtime import lifecycle


def _stock_partial_pending() -> dict:
    return {
        "subject_id": "subject-1",
        "job_group_id": "job-stock-uai",
        "manager_plan": {
            "intent_ids": ["stock.full.current", "stock.local.current"],
            "entities": {"sku": "001", "store_ref": "Uai Mineirinho"},
        },
        "manager_evidence": {
            "status": "partial",
            "sources": ["integração Mercado Livre", "estoque interno do JK Sistema"],
            "verified_facts": ['{"tool_id":"stock_data","records":0}'],
            "tool_results": [
                {
                    "tool_id": "mercado_livre_full_stock",
                    "tool_label": "estoque Full atual da API do Mercado Livre",
                    "records": 0,
                    "empty_reason": "A API não retornou saldo.",
                },
                {
                    "tool_id": "stock_data",
                    "tool_label": "estoque interno do JK Sistema",
                    "records": 0,
                    "empty_reason": "A consulta interna não retornou registros.",
                },
            ],
        },
        "verified_facts": ['{"tool_id":"stock_data","records":0}'],
        "verified_sources": ["integração Mercado Livre", "estoque interno do JK Sistema"],
    }


def test_stock_partial_text_is_scoped_plain_and_does_not_claim_zero() -> None:
    text = pending_component._IMPLEMENTATIONS["_pending_partial_text"](
        _stock_partial_pending(),
        "evidencia_interna_insuficiente",
    )

    assert "SKU 001" in text
    assert "Uai Mineirinho" in text
    assert "Mercado Livre Full" in text
    assert "Estoque da loja" in text
    assert "não confirma estoque zero" in text
    assert '"tool_id"' not in text


def test_proactive_delivery_falls_back_only_when_old_gateway_ignores_partial(monkeypatch) -> None:
    gateway_calls: list[dict] = []
    statuses = iter(("ignored_low_severity", "sent"))

    def gateway_json(_config: dict, _method: str, _path: str, payload: dict, **_kwargs) -> dict:
        gateway_calls.append(dict(payload))
        return {"success": True, "status": next(statuses)}

    monkeypatch.setattr(delivery, "_gateway_json", gateway_json, raising=False)
    result = delivery._IMPLEMENTATIONS["_post_proactive"](
        {"machine_id": "machine-1"},
        {
            "subject_id": "subject-1",
            "fingerprint": "job:stock-uai:partial-terminal",
            "event_type": "task_partial",
            "severity": "medium",
            "text": "Resposta parcial segura.",
        },
    )

    assert [item["event_type"] for item in gateway_calls] == ["task_partial", "task_failed"]
    assert gateway_calls[0]["fingerprint"] == gateway_calls[1]["fingerprint"]
    assert gateway_calls[0]["text"] == gateway_calls[1]["text"]
    assert result["status"] == "sent"
    assert result["compatibility_fallback"] is True
    assert result["delivery_event_type"] == "task_failed"


def test_proactive_delivery_does_not_retry_ambiguous_failure(monkeypatch) -> None:
    gateway_calls: list[dict] = []

    def gateway_json(_config: dict, _method: str, _path: str, payload: dict, **_kwargs) -> dict:
        gateway_calls.append(dict(payload))
        return {"success": False, "status": "gateway_timeout"}

    monkeypatch.setattr(delivery, "_gateway_json", gateway_json, raising=False)
    result = delivery._IMPLEMENTATIONS["_post_proactive"](
        {"machine_id": "machine-1"},
        {
            "subject_id": "subject-1",
            "fingerprint": "job:stock-uai:partial-terminal",
            "event_type": "task_partial",
            "severity": "medium",
            "text": "Resposta parcial segura.",
        },
    )

    assert len(gateway_calls) == 1
    assert result == {"success": False, "status": "gateway_timeout"}


def test_partial_terminal_closes_after_compatibility_delivery(monkeypatch) -> None:
    pending = _stock_partial_pending()
    proactive: list[dict] = []
    saved: list[dict] = []
    removed: list[tuple[str, dict]] = []
    task_updates: list[dict] = []
    timings: list[dict] = []

    def post_proactive(_config: dict, payload: dict) -> dict:
        proactive.append(dict(payload))
        return {
            "success": True,
            "status": "sent",
            "compatibility_fallback": True,
            "delivery_event_type": "task_failed",
        }

    monkeypatch.setattr(pending_component, "_post_proactive", post_proactive, raising=False)
    monkeypatch.setattr(pending_component, "_save_pending", lambda _state, _message_id, value: saved.append(dict(value)), raising=False)
    monkeypatch.setattr(pending_component, "_update_pending_codex_tasks", lambda _pending, **values: task_updates.append(values), raising=False)
    monkeypatch.setattr(pending_component, "_record_message_timing", lambda _message_id, **values: timings.append(values), raising=False)
    monkeypatch.setattr(pending_component, "_now", lambda: "2026-07-21T00:00:00Z", raising=False)
    monkeypatch.setattr(
        pending_component,
        "_remove_pending",
        lambda _state, message_id, **values: removed.append((message_id, values)),
        raising=False,
    )

    assert pending_component._IMPLEMENTATIONS["_terminate_pending_partial"](
        {}, {}, "wamid-partial", pending, reason="evidencia_interna_insuficiente",
    ) is True

    assert [item["event_type"] for item in proactive] == ["task_partial"]
    assert pending["partial_delivery_event_type"] == "task_failed"
    assert pending["terminal_delivery_started"] is True
    assert task_updates[-1]["delivery_state"] == "sent"
    assert timings[-1]["terminal_status"] == "partial"
    assert removed == [("wamid-partial", {"status": "partial", "reason": "evidencia_interna_insuficiente"})]
    assert saved


def test_bridge_heartbeat_renews_only_active_pending_message_leases_when_due(monkeypatch) -> None:
    state = {
        "gateway_lease_renewed_at_epoch": 0,
        "pending_messages": {
            "wamid.long-running": {"kind": "dual_worker", "task_id": "task-long"},
        },
    }
    gateway_calls: list[tuple[str, dict]] = []

    def gateway_json(_config, _method, path, payload, **_kwargs):
        gateway_calls.append((path, dict(payload)))
        if path == "/bridge/heartbeat":
            return {"success": True, "status": "online", "renewed_leases": 1}
        if path == "/bridge/claim":
            return {"success": True, "messages": []}
        raise AssertionError(path)

    monkeypatch.setattr(lifecycle, "_load_config", lambda: {"enabled": True, "machine_id": "machine"}, raising=False)
    monkeypatch.setattr(lifecycle, "_load_state", lambda: state, raising=False)
    monkeypatch.setattr(lifecycle, "_save_state", lambda _state: None, raising=False)
    monkeypatch.setattr(lifecycle, "_gateway_json", gateway_json, raising=False)
    monkeypatch.setattr(lifecycle, "_now", lambda: "2026-07-21T00:00:00Z", raising=False)
    monkeypatch.setattr(
        lifecycle,
        "_whatsapp_dual_agent_settings",
        lambda _config: {
            "conversation_worker_count": 1,
            "max_active_task_agents_global": 1,
            "max_active_task_agents_per_conversation": 1,
        },
        raising=False,
    )
    monkeypatch.setattr(lifecycle, "_configure_phone_dispatcher", lambda _workers: None, raising=False)
    monkeypatch.setattr(lifecycle.console_queueing, "configure_dual_limit", lambda *_args: None)
    monkeypatch.setattr(lifecycle, "_phone_dispatch_capacity", lambda: 1, raising=False)
    monkeypatch.setattr(lifecycle, "CLAIM_LIMIT", 5, raising=False)
    monkeypatch.setattr(lifecycle, "RUNTIME_STATE", {}, raising=False)
    monkeypatch.setattr(lifecycle, "_monitor_pending", lambda *_args: None, raising=False)
    monkeypatch.setattr(lifecycle, "_forward_task_transitions", lambda *_args: None, raising=False)
    monkeypatch.setattr(lifecycle, "_forward_question_approvals", lambda *_args: None, raising=False)
    result = lifecycle._IMPLEMENTATIONS["whatsapp_bridge_poll_once"]()

    heartbeat_payload = next(payload for path, payload in gateway_calls if path == "/bridge/heartbeat")
    assert result["success"] is True
    assert heartbeat_payload["active_message_ids"] == ["wamid.long-running"]
    assert state["gateway_lease_renewed_count"] == 1
    assert state["gateway_lease_renewed_at_epoch"] > 0
