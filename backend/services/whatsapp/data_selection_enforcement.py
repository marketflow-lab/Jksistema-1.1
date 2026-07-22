"""Server-owned safety enforcement for WhatsApp data-selection plans."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from backend.services.context_hub_inventory import _slug as _context_hub_slug
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import intent as whatsapp_intent


_TOOLS_WITHOUT_STORE_BALANCE_SCOPE = frozenset({"stock_data"})

_LIVE_QUESTION_QUEUE_RE = re.compile(
    r"\b(?:"
    r"(?:tem|ha|existe|existem|consulte|consultar|verifique|verificar|veja|listar?)\s+"
    r"(?:alguma?s?\s+)?perguntas?\s+(?:em\s+aberto|abertas?|pendentes?|sem\s+resposta|para\s+responder)"
    r"|perguntas?\s+(?:em\s+aberto|abertas?|pendentes?|sem\s+resposta|para\s+responder)"
    r"|(?:tem|ha|existe|existem)\s+(?:alguma?s?\s+)?perguntas?\s*(?:[?.!]|$)"
    r")\b",
    re.IGNORECASE,
)


def _is_live_question_queue_request(value: Any) -> bool:
    text = whatsapp_formatting._whatsapp_text_key(value)
    if re.search(
        r"\b(?:voce|black\s*jhon|joao\s+pretinho|assistente)\s+tem\s+"
        r"(?:alguma?s?\s+)?perguntas?\b",
        text,
    ):
        return False
    return bool(_LIVE_QUESTION_QUEUE_RE.search(text))


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
    query_policy: dict[str, Any],
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
    surface = str(filters.get("surface") or "").strip().casefold()[:100]
    if surface and re.fullmatch(r"[a-z0-9_.:-]{1,100}", surface):
        hub_args["surface"] = surface
    stable_ids = [
        str(item).strip()
        for item in list(filters.get("ids") or [])[:20]
        if re.fullmatch(r"jk:[A-Za-z0-9:_./-]{1,236}", str(item or "").strip())
    ]
    if stable_ids:
        hub_args["ids"] = stable_ids
    entity_sku = str(result.get("sku") or "").strip()
    filter_sku = str(filters.get("sku") or entity_sku).strip()[:100]
    if entity_sku and filter_sku and filter_sku.casefold() != entity_sku.casefold():
        raise RuntimeError("data_selection_context_scope_mismatch")
    if filter_sku and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", filter_sku):
        hub_args["sku"] = filter_sku
        normalized_sku = _context_hub_slug(filter_sku, fallback="")
        if normalized_sku:
            canonical_id = f"jk:sku:{normalized_sku}"
            if canonical_id not in stable_ids:
                stable_ids.insert(0, canonical_id)
            hub_args["ids"] = stable_ids[:20]
            hub_args.setdefault("source_type", "sku")
            hub_args.setdefault("module", "cadastro")

    entity_mlb = str(result.get("item_id") or "").strip().upper()
    filter_mlb = re.sub(r"[^A-Za-z0-9]", "", str(filters.get("mlb") or entity_mlb)).upper()[:60]
    if entity_mlb and filter_mlb and filter_mlb != entity_mlb:
        raise RuntimeError("data_selection_context_scope_mismatch")
    if filter_mlb and re.fullmatch(r"MLB\d{6,}", filter_mlb):
        hub_args["mlb"] = filter_mlb

    authorized_stores = [
        str(item or "").strip()
        for item in list(query_policy.get("authorized_stores") or [])
        if str(item or "").strip()
    ]
    requested_store = str(filters.get("store_ref") or result.get("store") or "").strip()[:180]
    if requested_store:
        store_matches = whatsapp_intent.exact_store_matches(requested_store, authorized_stores)
        if authorized_stores and len(store_matches) != 1:
            raise RuntimeError("data_selection_unauthorized_store")
        scoped_store = store_matches[0] if store_matches else requested_store
        if result.get("store") and scoped_store != result.get("store"):
            raise RuntimeError("data_selection_context_scope_mismatch")
        hub_args["store_ref"] = scoped_store

    tags = [
        str(item or "").strip()[:80]
        for item in list(filters.get("tags") or [])[:12]
        if str(item or "").strip()
    ]
    if tags:
        hub_args["tags"] = list(dict.fromkeys(tags))
    valid_at = str(filters.get("valid_at") or "").strip()[:40]
    if valid_at:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?", valid_at):
            raise RuntimeError("data_selection_invalid_context_valid_at")
        hub_args["valid_at"] = valid_at
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
    live_question_queue = _is_live_question_queue_request(context_request)
    if live_question_queue:
        # A fila atual e uma consulta operacional agregada. SKU/MLB de uma
        # tarefa ou rodada anterior nunca podem estreitar esse pedido nem
        # arrastar foto, anuncio ou Context Hub para a conclusao.
        entities = dict(result.get("entities") or {}) if isinstance(result.get("entities"), dict) else {}
        entities.update({"sku": "", "mlb": ""})
        result["entities"] = entities
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
        if exact_store and tool_id in _TOOLS_WITHOUT_STORE_BALANCE_SCOPE:
            # Capability validation only: this tool exposes an aggregate local
            # balance and cannot prove the balance of one named store.
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
        if tool_id != "context_hub_search":
            if result.get("sku") and tool_id != "bling_positive_stock_sku_count":
                args["sku"] = result["sku"]
            else:
                args.pop("sku", None)
            if result.get("item_id"):
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
    if live_question_queue and action != "mutation_candidate":
        result["sku"] = ""
        result["item_id"] = ""
        action = "collect"
        result["action"] = action
        proposed = []
        add_call(
            "questions_post_sale_query",
            {
                "status": "UNANSWERED",
                "todas_lojas": str(query_policy.get("store_mode") or "") == "all",
                "force_refresh": True,
            },
            True,
            "fila viva de perguntas em aberto",
        )
    elif positive_stock_sku_count:
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

    if live_question_queue:
        hub_mode, hub_plan = "not_applicable", {}
    else:
        hub_mode, hub_plan = _hub_call(result, add_call, query_policy)
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
        "live_question_queue": live_question_queue,
    }
    if action == "collect" and not calls:
        raise RuntimeError("data_selection_collect_without_authorized_source")
    return result


__all__ = ["enforce_plan"]
