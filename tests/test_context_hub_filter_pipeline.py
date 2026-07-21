from __future__ import annotations

from backend.services import codex_assistant, codex_console, context_hub
from backend.services.codex_data_selection_agent import (
    LEGACY_SCHEMA_VERSION,
    normalize_data_selection_plan,
)
from backend.services.context_hub_endpoints import ContextHubSearchFilters, ContextHubSearchRequest
from backend.services.whatsapp import data_selection_enforcement


FILTERS = {
    "sku": "SKU-001",
    "mlb": "MLB123456789",
    "store_ref": "Loja Alfa",
    "tags": ["compatibilidade", "manual"],
    "surface": "installed",
    "valid_at": "2026-07-20",
}


def _normalized_plan() -> dict:
    plan, _stats = normalize_data_selection_plan(
        {
            "schema_version": LEGACY_SCHEMA_VERSION,
            "action": "collect",
            "intents": ["contexto_tecnico_sku"],
            "entities": {
                "sku": FILTERS["sku"],
                "mlb": FILTERS["mlb"],
                "order_id": "",
                "period": "",
                "store_ref": FILTERS["store_ref"],
                "store_mode": "single",
            },
            "requested_fields": ["compatibility"],
            "tool_calls": [],
            "context_hub": {
                "mode": "required",
                "query": "manual tecnico do produto",
                "filters": dict(FILTERS),
                "top_k": 4,
                "snippet_max_chars": 320,
            },
            "missing_user_fields": [],
            "confidence": 0.95,
            "reason": "usar conhecimento tecnico versionado",
        },
        allowed_tools=[],
        authorized_stores=[FILTERS["store_ref"]],
    )
    return plan


def _assert_filter_contract(arguments: dict) -> None:
    for key in ("sku", "mlb", "store_ref", "tags", "surface", "valid_at"):
        assert arguments[key] == FILTERS[key]


def test_materialized_desktop_decision_keeps_all_context_hub_filters() -> None:
    calls = codex_console._codex_agent_planned_calls(_normalized_plan())

    assert len(calls) == 1
    assert calls[0]["tool_id"] == "context_hub_search"
    _assert_filter_contract(calls[0]["arguments"])


def test_whatsapp_canary_applies_materialized_filters_in_context_hub(monkeypatch) -> None:
    guarded = data_selection_enforcement.enforce_plan(
        _normalized_plan(),
        request_text="Consulte a compatibilidade do SKU-001.",
        query_policy={
            "authorized_stores": [FILTERS["store_ref"]],
            "store": FILTERS["store_ref"],
            "store_mode": "single",
            "context_hub_enabled": True,
        },
        catalog=[{"id": "context_hub_search"}],
        max_calls=6,
    )
    assert len(guarded["tool_calls"]) == 1
    call = guarded["tool_calls"][0]
    _assert_filter_contract(call["arguments"])

    captured = {}

    def fake_search_context(*, client_id, query, filters, limit):
        captured.update(
            {
                "client_id": client_id,
                "query": query,
                "filters": dict(filters),
                "limit": limit,
            }
        )
        return {
            "success": True,
            "generation_id": "generation-canary",
            "source_version": "canary-v1",
            "results": [],
            "count": 0,
        }

    monkeypatch.setattr(context_hub, "search_context", fake_search_context)
    args = dict(call["arguments"])
    args["request_surface"] = "black_jhon_whatsapp"
    codex_assistant.codex_assistant_execute_tool_call(
        client_id="tenant-canary",
        tool_id="context_hub_search",
        args=args,
        screen_context={},
        permissions={"full": True},
        audit_user="canary",
    )

    assert captured["client_id"] == "tenant-canary"
    assert captured["limit"] == 4
    assert captured["filters"]["request_surface"] == "black_jhon_whatsapp"
    _assert_filter_contract(captured["filters"])


def test_admin_search_models_accept_the_same_closed_filter_contract() -> None:
    nested = ContextHubSearchFilters(**FILTERS)
    request = ContextHubSearchRequest(
        query="manual tecnico",
        filters=nested,
        **FILTERS,
    )

    assert nested.model_dump(include=set(FILTERS)) == FILTERS
    assert request.model_dump(include=set(FILTERS)) == FILTERS
