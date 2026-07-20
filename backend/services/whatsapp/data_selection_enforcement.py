"""Server-owned safety enforcement for WhatsApp data-selection plans."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from backend.services.context_hub_inventory import _slug as _context_hub_slug
from backend.services.whatsapp import intent as whatsapp_intent


def _apply_scope(
    result: dict[str, Any], query_policy: dict[str, Any]
) -> str:
    entities = result.get("entities") if isinstance(result.get("entities"), dict) else {}
    raw_sku = str(entities.get("sku") or "").strip()[:100]
    raw_item_id = re.sub(r"[^A-Z0-9]", "", str(entities.get("mlb") or "").strip().upper())[:60]
    result["sku"] = raw_sku if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", raw_sku) else ""
    result["item_id"] = raw_item_id if re.fullmatch(r"MLB\d{6,}", raw_item_id) else ""
    authorized = [
        str(item or "").strip()
        for item in list(query_policy.get("authorized_stores") or [])
        if str(item or "").strip()
    ]
    exact_store = ""
    agent_store = str(entities.get("store_ref") or "").strip()
    if agent_store:
        matches = whatsapp_intent.exact_store_matches(agent_store, authorized)
        exact_store = matches[0] if len(matches) == 1 else ""
        if not exact_store:
            raise RuntimeError("data_selection_unauthorized_store")
    elif str(query_policy.get("store_mode") or "") == "single" and str(query_policy.get("store") or "").strip():
        policy_store = str(query_policy.get("store") or "").strip()
        exact_store = policy_store if not authorized or policy_store in authorized else ""
    if exact_store:
        result.update({"store": exact_store, "store_mode": "single"})
        query_policy.update({"store": exact_store, "store_mode": "single", "stores": [exact_store]})
    elif str(entities.get("store_mode") or "") == "all" and authorized:
        result.update({"store": "", "store_mode": "all", "stores": authorized})
        query_policy.update({"store": "", "store_mode": "all", "stores": authorized})
    else:
        result.update({"store": "", "store_mode": "none"})
        query_policy.pop("store", None)
        query_policy.pop("stores", None)
        query_policy["store_mode"] = "none"
    return exact_store


def _hub_call(
    result: dict[str, Any],
    add_call: Callable[..., Optional[int]],
) -> tuple[str, dict[str, Any]]:
    hub_plan = result.get("context_hub") if isinstance(result.get("context_hub"), dict) else {}
    hub_mode = str(hub_plan.get("mode") or "off").strip().lower()
    if hub_mode not in {"required", "optional", "search", "use"}:
        return hub_mode, hub_plan
    hub_args: dict[str, Any] = {
        "query": str(hub_plan.get("query") or "").strip()[:1000],
        "limit": max(1, min(6, int(hub_plan.get("top_k") or hub_plan.get("limit") or 6))),
        "force_refresh": False,
    }
    filters = hub_plan.get("filters") if isinstance(hub_plan.get("filters"), dict) else {}
    for key in ("module", "source_type"):
        value = str(filters.get(key) or "").strip()
        if re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", value):
            hub_args[key] = value
    surface = str(filters.get("surface") or "").strip().lower()
    if surface in {"development", "installed"}:
        hub_args["environment"] = surface
    stable_ids = [
        str(item).strip()
        for item in list(filters.get("ids") or [])[:20]
        if re.fullmatch(r"jk:[A-Za-z0-9:_./-]{1,236}", str(item or "").strip())
    ]
    if stable_ids:
        hub_args["ids"] = stable_ids
    filter_sku = str(filters.get("sku") or result.get("sku") or "").strip()
    if filter_sku and filter_sku == result.get("sku"):
        normalized_sku = _context_hub_slug(filter_sku, fallback="")
        if normalized_sku:
            canonical_id = f"jk:sku:{normalized_sku}"
            if canonical_id not in stable_ids:
                stable_ids.insert(0, canonical_id)
            hub_args.update({"ids": stable_ids[:20], "source_type": "sku", "module": "cadastro"})
    if hub_args["query"]:
        add_call(
            "context_hub_search", hub_args, hub_mode == "required",
            "conhecimento selecionado pelo agente de dados",
        )
    return hub_mode, hub_plan


def enforce_plan(
    plan: dict[str, Any], *, request_text: str, query_policy: dict[str, Any],
    catalog: list[dict[str, Any]], max_calls: int,
) -> dict[str, Any]:
    """Validate agent-selected read-only calls against server-owned scope."""

    result = dict(plan or {})
    context_request = str(query_policy.get("context_request") or request_text or "").strip()
    allowed = {str(item.get("id") or "") for item in catalog if isinstance(item, dict)}
    exact_store = _apply_scope(result, query_policy)
    calls: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    denied_keys = {
        "authorization", "permissions", "client_id", "tenant_id", "tenant",
        "cliente_id", "access_token", "refresh_token", "token", "api_key",
    }
    hub_allowed = query_policy.get("context_hub_enabled") is not False and "context_hub_search" in allowed

    def add_call(
        tool_id: str, arguments: Optional[dict[str, Any]] = None, required: bool = True,
        reason: str = "", depends_on: Optional[list[int]] = None,
    ) -> Optional[int]:
        if tool_id not in allowed or (tool_id == "context_hub_search" and not hub_allowed):
            return None
        args = {
            str(key): value for key, value in dict(arguments or {}).items()
            if str(key).strip().lower() not in denied_keys
        }
        if exact_store and tool_id != "context_hub_search":
            args["loja"] = exact_store
        elif str(query_policy.get("store_mode") or "") == "all":
            args.pop("loja", None)
            args.pop("store", None)
        if result.get("sku") and tool_id not in {"bling_positive_stock_sku_count", "context_hub_search"}:
            args["sku"] = result["sku"]
        else:
            args.pop("sku", None)
        if result.get("item_id") and tool_id != "context_hub_search":
            args["item_id"] = result["item_id"]
        else:
            args.pop("item_id", None)
        args.setdefault("message", context_request[:4000])
        signature = json.dumps({"tool_id": tool_id, "arguments": args}, ensure_ascii=False, sort_keys=True, default=str)
        if signature in seen:
            return seen[signature]
        if len(calls) >= max(1, min(6, int(max_calls or 6))):
            return None
        call_index = len(calls)
        seen[signature] = call_index
        calls.append({
            "tool_id": tool_id, "arguments": args, "required": bool(required),
            "reason": str(reason or "")[:500],
            "depends_on": [
                int(item) for item in list(depends_on or [])[:6]
                if isinstance(item, int) and 0 <= item < call_index
            ],
        })
        return call_index

    action = str(result.get("action") or "collect").strip().lower()
    proposed = result.get("tool_calls") if isinstance(result.get("tool_calls"), list) else []
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    positive_stock_sku_count = source_policy.get("positive_stock_sku_count_requested") is True
    if positive_stock_sku_count:
        # Esta consulta e agregada sobre o catalogo; um falso SKU extraido de
        # "SKUs" nao pode transforma-la em saldo de um produto individual.
        result["sku"] = ""
        result["item_id"] = ""
        proposed = []
        add_call(
            "bling_positive_stock_sku_count",
            {},
            True,
            "contagem agregada de SKUs com saldo positivo",
        )
    if action not in {"answer_without_data", "clarify", "mutation_candidate"}:
        accepted: dict[int, int] = {}
        for raw_index, item in enumerate(proposed):
            if not isinstance(item, dict) or str(item.get("tool_id") or "") == "context_hub_search":
                continue
            raw_args = item.get("arguments")
            if not isinstance(raw_args, dict):
                try:
                    raw_args = json.loads(str(raw_args or "{}"))
                except Exception:
                    raw_args = {}
            mapped = [
                accepted[dep] for dep in list(item.get("depends_on") or [])[:6]
                if isinstance(dep, int) and dep in accepted
            ]
            accepted_index = add_call(
                str(item.get("tool_id") or ""), raw_args if isinstance(raw_args, dict) else {},
                item.get("required") is not False, str(item.get("reason") or ""), mapped,
            )
            if accepted_index is not None:
                accepted[raw_index] = accepted_index

    hub_mode, hub_plan = _hub_call(result, add_call)
    result.update({"tool_calls": calls, "requires_web": False, "requires_sol": False})
    hub_planned = any(str(item.get("tool_id") or "") == "context_hub_search" for item in calls)
    result["missing_user_fields"] = [
        str(item or "").strip()[:160]
        for item in list(result.get("missing_user_fields") or [])[:12]
        if str(item or "").strip()
    ]
    result["manager_guard"] = {
        "data_selection_action": action,
        "context_hub_mode": "selected" if hub_planned else "blocked" if hub_mode not in {"", "off", "none", "not_needed", "not_applicable"} else "not_selected",
        "context_hub_reason": str(hub_plan.get("reason") or "agent_selection" if hub_planned else "")[:300],
        "context_hub_allowed": hub_allowed,
        "positive_stock_sku_count": positive_stock_sku_count,
    }
    if action == "collect" and not calls:
        raise RuntimeError("data_selection_collect_without_authorized_source")
    return result


__all__ = ["enforce_plan"]
