"""Server-bound read-only adapters used by the unified pre-sale agent."""

from __future__ import annotations

import copy
from typing import Any

from .client_workflows import GeneralBindings, _classified_tool, _mandatory_web_tool
from .general_commercial import _collect_general_internal_sources
from .inputs import _perguntas_ia_research_input
from .sku_question_context import bind_client_sku_question_context


def invoke_unified_turn(
    client: Any,
    prompt: str,
    tool_results: list[dict[str, Any]],
    force_answer: bool,
) -> dict[str, Any]:
    """Run one structured turn without leaving the client's provider thread."""

    thread_reused = bool(client.codex_thread_id)
    payload = client._call_structured_model(
        prompt,
        {
            "category": "post_sale" if client._is_post_sale else "unified_response",
            "force_answer": bool(force_answer),
        },
        stage="unified_response_agent",
        tool_results=list(tool_results or []),
    )
    category = str(payload.get("category") or "unknown").strip().lower()
    subquestions = [
        str(value or "").strip()
        for value in list(payload.get("subquestions") or [])[:12]
        if str(value or "").strip()
    ]
    intent = {
        "fluxo": "pos_venda" if client._is_post_sale else "perguntas_anuncio",
        "categoria": category,
        "categorias": [category] if category else [],
        "subperguntas": subquestions,
        "origem": "unified_response_agent",
    }
    client.agent_input["intent"] = intent
    client.agent_input["classification"] = copy.deepcopy(intent)
    client.agent_input["category"] = category
    client.agent_input["subquestions"] = subquestions
    commercial_state = str(payload.get("commercial_state") or "").strip().lower()
    if commercial_state:
        client.commercial_state = commercial_state
    compatibility_analysis = payload.get("compatibility_analysis")
    if isinstance(compatibility_analysis, dict):
        client.compatibility_analysis = copy.deepcopy(compatibility_analysis)
    client.context_pipeline.append({
        "step": len(client.context_pipeline) + 1,
        "name": "unified_response_agent",
        "status": "completed",
        "action": str(payload.get("action") or ""),
        "force_answer": bool(force_answer),
        "thread_reused": thread_reused,
    })
    return payload


def collect_unified_initial_context(
    client: Any,
    metadata: dict[str, Any],
    bindings: GeneralBindings,
) -> dict[str, Any]:
    """Collect and bind current listing, catalog, Bling and Context Hub data."""

    client.context_pipeline = [{
        "step": 1,
        "name": "buyer_question_history_and_listing_snapshot",
        "status": "completed",
        "history_count": int(metadata.get("history_count") or 0),
        "listing_loaded": bool(metadata.get("item_id") or metadata.get("listing_title")),
    }]
    internal = _collect_general_internal_sources(
        client,
        metadata,
        bindings,
        {"get_mercado_livre_listing", "get_product_data", "get_bling_product"},
        _classified_tool,
    )
    hub = client._tool_segura(
        "context_hub_search",
        lambda: bindings.context_hub_tool(
            client.client_id,
            _perguntas_ia_research_input(client.agent_input),
        ),
    )
    client._registrar_etapa_tool(3, "context_hub_sku_reference", hub)
    packet = bind_client_sku_question_context(
        client,
        metadata,
        internal_sources=internal,
        context_hub=hub,
    )
    return {"sku_question_context": copy.deepcopy(packet)}


def _unavailable_research(tool_type: str, reason: str) -> dict[str, Any]:
    return {
        "function": f"unified_{tool_type}",
        "arguments": {},
        "result": {
            "found": False,
            "unavailable": True,
            "read_only": True,
            "reason": reason,
        },
    }


def _web_research(
    client: Any,
    requests: list[dict[str, str]],
    round_number: int,
    bindings: GeneralBindings,
) -> dict[str, Any]:
    research_input = copy.deepcopy(client.agent_input)
    safe_queries = [
        {
            "type": str(request.get("type") or "technical_web")[:80],
            "query": str(request.get("query") or "")[:260],
        }
        for request in requests
        if str(request.get("query") or "").strip()
    ][:8]
    research_input["technical_question_plan"] = {
        "version": "unified_response_agent_v1",
        "queries": safe_queries,
    }
    research_input["technical_gap_queries"] = safe_queries if round_number > 1 else []
    research_input["research_attempt"] = round_number
    research_input["force_external_research"] = True
    research_input["use_web_search"] = True
    research_input["web_search_required"] = True
    research_input["allowed_tools"] = [
        "web_search", "web_search_product_identity", "web_search_question_context",
    ]
    result = _mandatory_web_tool(
        "web_search_question_context",
        lambda: bindings.web_tool(
            client.client_id,
            _perguntas_ia_research_input(research_input),
            list(client.unified_research_results),
        ),
    )
    return result if isinstance(result, dict) else _unavailable_research(
        "technical_web", "empty_web_result",
    )


def execute_unified_research(
    client: Any,
    requests: list[dict[str, str]],
    round_number: int,
    bindings: GeneralBindings,
) -> list[dict[str, Any]]:
    """Materialize model requests with server-bound identities and read-only tools."""

    client.unified_research_rounds = max(client.unified_research_rounds, int(round_number or 0))
    results: list[dict[str, Any]] = []
    web_requests = [request for request in requests if request.get("type") == "technical_web"]
    if web_requests:
        results.append(_web_research(client, web_requests, round_number, bindings))
    if not client.sku_question_context:
        collect_unified_initial_context(client, {}, bindings)
    packet = bind_client_sku_question_context(client, {})
    operational = list(packet.get("operational_data") or [])

    def bound_result(tool_type: str) -> dict[str, Any]:
        if tool_type == "listing":
            value = copy.deepcopy(packet.get("listing_facts") or {})
            function_name = "get_mercado_livre_listing"
        elif tool_type == "context_hub":
            value = {
                key: copy.deepcopy(packet.get(key))
                for key in (
                    "canonical_document", "catalog_document", "catalog_generation",
                    "guidance", "generation", "source_hashes", "validity", "binding_hash",
                    "conflicts", "gaps",
                )
                if packet.get(key) not in (None, "", [], {})
            }
            function_name = "context_hub_search"
        else:
            expected_source = {
                "internal_catalog": "get_product_data",
                "bling": "get_bling_product",
            }[tool_type]
            matches = [
                copy.deepcopy(value)
                for value in operational
                if isinstance(value, dict) and value.get("source") == expected_source
            ]
            value = {"sources": matches} if matches else {}
            function_name = expected_source
        if not value:
            return _unavailable_research(tool_type, "bound_source_unavailable")
        return {
            "function": function_name,
            "arguments": {},
            "result": {
                "found": True,
                "read_only": True,
                "content_role": "untrusted_reference_data",
                "store_sku_bound": True,
                "data": value,
            },
        }

    for request in requests:
        tool_type = str(request.get("type") or "")
        if tool_type == "technical_web":
            continue
        if tool_type in {"listing", "internal_catalog", "bling", "context_hub"}:
            results.append(bound_result(tool_type))
            continue
        if tool_type == "same_store_listing":
            results.append(client._tool_segura(
                "find_same_store_compatible_alternative",
                lambda: bindings.alternative_tool(
                    client.client_id,
                    client.loja,
                    client.agent_input,
                    client.compatibility_analysis,
                ),
            ))
            continue
        results.append(_unavailable_research(
            tool_type or "unknown",
            "tool_not_available_in_pre_sale_flow",
        ))
    bind_client_sku_question_context(
        client,
        {},
        external_sources=results,
        force_high_risk=bool(web_requests),
    )
    client.unified_research_results.extend(copy.deepcopy(results))
    client.context_pipeline.append({
        "step": len(client.context_pipeline) + 1,
        "name": "unified_read_only_research",
        "status": "completed" if results else "unavailable",
        "round": int(round_number or 0),
        "request_count": len(requests),
        "result_count": len(results),
    })
    return results


__all__ = [
    "collect_unified_initial_context",
    "execute_unified_research",
    "invoke_unified_turn",
]
