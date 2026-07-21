"""Codex agent that plans the minimum read-only data collection for Black Jhon.

The model in this module is deliberately non-conversational: it cannot execute
tools and it never writes the answer shown to the user.  Authentication,
tenant/store isolation, tool execution and mutation approval remain server
responsibilities.
"""

from __future__ import annotations

import copy
import json
import re
import tempfile
import threading
import time
import unicodedata
import uuid
from collections import Counter, deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


LEGACY_SCHEMA_VERSION = "1.0"
SCHEMA_VERSION = "2.0"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_REASONING_EFFORT = "low"
MAX_PLAN_BYTES = 24 * 1024
MAX_EVIDENCE_BYTES = 12 * 1024
MAX_REPORT_EVIDENCE_BYTES = 24 * 1024
MAX_TOOL_CALLS = 6
MAX_CONTEXT_HUB_SNIPPETS = 6
MAX_CONTEXT_HUB_SNIPPET_CHARS = 320
MAX_ML_RESOURCE_CANDIDATES = 6
MERCADO_LIVRE_RESOURCE_TOOL_ID = "mercado_livre_resource_query"

PLAN_ACTIONS = ("answer_without_data", "clarify", "collect", "mutation_candidate")
CONTEXT_HUB_MODES = ("not_applicable", "optional", "required")

DATA_SELECTION_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "action",
        "intents",
        "intent_ids",
        "entities",
        "requested_fields",
        "tool_calls",
        "context_hub",
        "coverage",
        "resource_catalog_version",
        "missing_user_fields",
        "confidence",
        "reason",
    ],
    "properties": {
        "schema_version": {"type": "string", "enum": [SCHEMA_VERSION]},
        "action": {"type": "string", "enum": list(PLAN_ACTIONS)},
        "intents": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string", "maxLength": 120},
        },
        "intent_ids": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string", "pattern": r"^[a-z][a-z0-9_.-]{0,119}$"},
        },
        "entities": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "sku", "mlb", "order_id", "pack_id", "claim_id", "return_id", "shipment_id",
                "payment_id", "question_id", "category_id", "product_id", "user_product_id",
                "period", "store_ref", "store_mode",
            ],
            "properties": {
                "sku": {"type": "string", "maxLength": 120},
                "mlb": {"type": "string", "maxLength": 60},
                "order_id": {"type": "string", "maxLength": 100},
                "pack_id": {"type": "string", "maxLength": 100},
                "claim_id": {"type": "string", "maxLength": 100},
                "return_id": {"type": "string", "maxLength": 100},
                "shipment_id": {"type": "string", "maxLength": 100},
                "payment_id": {"type": "string", "maxLength": 100},
                "question_id": {"type": "string", "maxLength": 100},
                "category_id": {"type": "string", "maxLength": 100},
                "product_id": {"type": "string", "maxLength": 100},
                "user_product_id": {"type": "string", "maxLength": 100},
                "period": {"type": "string", "maxLength": 240},
                "store_ref": {"type": "string", "maxLength": 180},
                "store_mode": {"type": "string", "enum": ["single", "all", "none"]},
            },
        },
        "requested_fields": {
            "type": "array",
            "maxItems": 40,
            "items": {"type": "string", "maxLength": 120},
        },
        "tool_calls": {
            "type": "array",
            "maxItems": MAX_TOOL_CALLS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "tool_id", "arguments", "required", "reason", "depends_on",
                    "expected_fields", "success_condition", "failure_policy",
                ],
                "properties": {
                    "tool_id": {"type": "string", "maxLength": 120},
                    "arguments": {"type": "string", "maxLength": 6000},
                    "required": {"type": "boolean"},
                    "reason": {"type": "string", "maxLength": 600},
                    "depends_on": {
                        "type": "array",
                        "maxItems": MAX_TOOL_CALLS,
                        "items": {"type": "integer", "minimum": 0, "maximum": MAX_TOOL_CALLS - 1},
                    },
                    "expected_fields": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {"type": "string", "maxLength": 120},
                    },
                    "success_condition": {"type": "string", "maxLength": 500},
                    "failure_policy": {"type": "string", "enum": ["fail_plan", "partial", "skip"]},
                },
            },
        },
        "context_hub": {
            "type": "object",
            "additionalProperties": False,
            "required": ["mode", "query", "filters", "top_k", "snippet_max_chars"],
            "properties": {
                "mode": {"type": "string", "enum": list(CONTEXT_HUB_MODES)},
                "query": {"type": "string", "maxLength": 1000},
                "filters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "sku",
                        "mlb",
                        "store_ref",
                        "module",
                        "source_type",
                        "surface",
                        "ids",
                        "document_types",
                        "tags",
                        "truth_class",
                        "authority",
                        "sensitivity",
                        "valid_at",
                    ],
                    "properties": {
                        "sku": {"type": "string", "maxLength": 120},
                        "mlb": {"type": "string", "maxLength": 60},
                        "store_ref": {"type": "string", "maxLength": 180},
                        "module": {"type": "string", "maxLength": 100},
                        "source_type": {"type": "string", "maxLength": 100},
                        "surface": {"type": "string", "maxLength": 100},
                        "ids": {
                            "type": "array",
                            "maxItems": 20,
                            "items": {"type": "string", "maxLength": 160},
                        },
                        "document_types": {
                            "type": "array",
                            "maxItems": 10,
                            "items": {"type": "string", "maxLength": 80},
                        },
                        "tags": {
                            "type": "array",
                            "maxItems": 12,
                            "items": {"type": "string", "maxLength": 80},
                        },
                        "truth_class": {"type": "string", "maxLength": 100},
                        "authority": {
                            "type": "string",
                            "enum": ["authoritative", "verified_technical", "advisory", "unverified", ""],
                        },
                        "sensitivity": {"type": "string", "maxLength": 80},
                        "valid_at": {"type": "string", "maxLength": 40},
                    },
                },
                "top_k": {"type": "integer", "minimum": 0, "maximum": MAX_CONTEXT_HUB_SNIPPETS},
                "snippet_max_chars": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_CONTEXT_HUB_SNIPPET_CHARS,
                },
            },
        },
        "coverage": {
            "type": "object",
            "additionalProperties": False,
            "required": ["expected_stores", "completeness_condition", "stop_conditions"],
            "properties": {
                "expected_stores": {
                    "type": "array",
                    "maxItems": 30,
                    "items": {"type": "string", "maxLength": 180},
                },
                "completeness_condition": {
                    "type": "string",
                    "enum": ["not_applicable", "single_result", "all_required_sources", "all_authorized_stores", "best_effort"],
                },
                "stop_conditions": {
                    "type": "array",
                    "maxItems": 12,
                    "items": {"type": "string", "maxLength": 240},
                },
            },
        },
        "resource_catalog_version": {"type": "string", "maxLength": 80},
        "missing_user_fields": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string", "maxLength": 160},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 1200},
    },
}

_SENSITIVE_ARGUMENT_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "client_id",
    "cliente_id",
    "cookie",
    "credential",
    "credentials",
    "password",
    "permission",
    "permissions",
    "secret",
    "session",
    "tenant",
    "tenant_id",
    "token",
    "access_token",
    "refresh_token",
}
_MUTATING_TOOL_RE = re.compile(
    r"(?:^|[_./-])(create|update|delete|remove|publish|approve|reject|send|reply|cancel|write|edit|sync|import|export|execute|mutation)(?:$|[_./-])",
    re.IGNORECASE,
)
_STORE_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_CURRENT_OPERATIONAL_FIELD_MARKERS = (
    "stock",
    "estoque",
    "saldo",
    "price",
    "preco",
    "order",
    "pedido",
    "sale",
    "venda",
    "return",
    "devolucao",
    "refund",
    "reembolso",
    "visit",
    "visita",
    "status_atual",
    "current_status",
)


class DataSelectionPlanError(RuntimeError):
    """Raised when the planner output cannot pass the server contract."""


def _clean_text(value: Any, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[: max(0, int(limit))]


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    text = _clean_text(value, MAX_PLAN_BYTES * 2)
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except Exception as exc:
        raise DataSelectionPlanError("data_selection_invalid_json") from exc
    if not isinstance(parsed, dict):
        raise DataSelectionPlanError("data_selection_plan_not_object")
    return parsed


def _unique_texts(value: Any, *, maximum: int, item_limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in list(value or []) if isinstance(value, (list, tuple)) else []:
        item = _clean_text(raw, item_limit)
        key = item.casefold()
        if not item or key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= maximum:
            break
    return result


def _strip_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _clean_text(key, 120): _strip_sensitive(item)
            for key, item in value.items()
            if _argument_key(_clean_text(key, 120)) not in _SENSITIVE_ARGUMENT_KEYS
        }
    if isinstance(value, list):
        return [_strip_sensitive(item) for item in value[:100]]
    if isinstance(value, str):
        return _clean_text(value, 4000)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _clean_text(value, 1000)


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        parsed = value
    else:
        text = _clean_text(value, 6000)
        if not text:
            parsed = {}
        else:
            try:
                parsed = json.loads(text)
            except Exception as exc:
                raise DataSelectionPlanError("data_selection_invalid_tool_arguments") from exc
    if not isinstance(parsed, dict):
        raise DataSelectionPlanError("data_selection_tool_arguments_not_object")
    return _strip_sensitive(parsed)


def _argument_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _clean_text(value, 120).casefold()).strip("_")


def _tool_id(entry: Any) -> str:
    if isinstance(entry, str):
        return _clean_text(entry, 120)
    if isinstance(entry, dict):
        return _clean_text(entry.get("tool_id") or entry.get("id") or entry.get("name"), 120)
    return ""


def _tool_is_read_only(entry: Any) -> bool:
    tool_id = _tool_id(entry)
    if not tool_id or _MUTATING_TOOL_RE.search(tool_id):
        return False
    if not isinstance(entry, dict):
        return True
    if entry.get("read_only") is False or entry.get("mutating") is True:
        return False
    if entry.get("requires_approval") is True or str(entry.get("risk") or "").lower() in {"write", "mutation", "destructive"}:
        return False
    return True


def compact_tool_catalog(allowed_tools: Optional[Iterable[Any]]) -> list[dict[str, Any]]:
    """Return only model-useful, non-secret fields from the server catalog."""

    compact: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in list(allowed_tools or []):
        if not _tool_is_read_only(entry):
            continue
        tool_id = _tool_id(entry)
        key = tool_id.casefold()
        if not tool_id or key in seen:
            continue
        seen.add(key)
        item: dict[str, Any] = {"tool_id": tool_id, "read_only": True}
        if isinstance(entry, dict):
            description = _clean_text(entry.get("description") or entry.get("summary"), 500)
            if description:
                item["description"] = description
            schema = entry.get("input_schema") or entry.get("parameters") or entry.get("arguments_schema")
            if isinstance(schema, dict):
                properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
                item["arguments"] = [
                    _clean_text(name, 100)
                    for name in list(properties)[:40]
                    if _argument_key(name) not in _SENSITIVE_ARGUMENT_KEYS
                ]
        compact.append(item)
    return compact


_ML_DOMAIN_CUES: dict[str, tuple[str, ...]] = {
    "ads": ("ads", "anuncio patrocinado", "campanha", "publicidade"),
    "billing": ("faturamento", "cobranca", "tarifa", "relatorio financeiro"),
    "catalog": ("atributo", "categoria", "catalogo", "produto"),
    "claims": ("claim", "mediacao", "reclamacao"),
    "compatibilities": ("aplicacao", "autopeca", "compatibilidade", "veiculo"),
    "fulfillment": ("estoque distribuido", "estoque full", "full", "inventario"),
    "invoices": ("invoice", "nota fiscal", "nf-e", "nfe"),
    "items": ("anuncio", "item", "mlb", "publicacao", "sku"),
    "messages": ("mensagem", "pos venda", "pos-venda"),
    "metrics": ("mais vendido", "tendencia", "visita"),
    "orders": ("order", "pack", "pedido", "venda"),
    "payments": ("collection", "pagamento"),
    "pricing": ("preco", "precos", "price"),
    "promotions": ("desconto", "oferta", "promocao"),
    "questions": ("pergunta", "question", "tempo de resposta"),
    "returns": ("devolucao", "retorno"),
    "shipments": ("envio", "frete", "rastreamento", "shipment"),
    "users": ("comprador", "endereco", "reputacao", "usuario"),
}
DATA_SELECTION_PLAN_V2_SCHEMA = DATA_SELECTION_PLAN_SCHEMA
_ML_RESOURCE_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ml.visits.", ("visita", "visitas")),
    ("ml.trends.", ("tendencia", "tendencias")),
    ("ml.highlights.", ("destaque", "mais vendido")),
    ("ml.items.description", ("descricao do anuncio", "descricao")),
    ("ml.items.get", ("detalhe do anuncio", "dados do mlb")),
    ("ml.items.seller_search", ("anuncios do vendedor", "buscar sku", "listar anuncios")),
    ("ml.questions.", ("pergunta", "perguntas")),
    ("ml.orders.search", ("buscar pedidos", "listar pedidos", "pedidos do periodo")),
    ("ml.orders.get", ("detalhe do pedido", "status do pedido")),
    ("ml.claims.messages", ("mensagens da reclamacao", "mensagem da reclamacao")),
    ("ml.claims.status_history", ("historico da reclamacao", "status da reclamacao")),
    ("ml.returns.", ("devolucao", "devolucoes")),
    ("ml.shipments.get", ("detalhe do envio", "status do envio")),
    ("ml.shipments.history", ("historico do envio", "rastrear envio", "rastreamento")),
    ("ml.fulfillment.stock", ("estoque full", "saldo full")),
    ("ml.stock.user_product", ("estoque distribuido", "saldo distribuido")),
)


def _search_normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean_text(value, 8000))
    text = "".join(char for char in text if not unicodedata.combining(char)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _mercado_livre_catalog() -> tuple[str, list[dict[str, Any]]]:
    """Load the versioned catalogue lazily to keep planner import isolation."""

    try:
        from backend.services import mercado_livre_query_catalog

        return (
            str(mercado_livre_query_catalog.CATALOG_VERSION),
            [dict(item) for item in mercado_livre_query_catalog.MERCADO_LIVRE_QUERY_RESOURCES],
        )
    except Exception:
        return "unavailable", []


def resolve_mercado_livre_resource_candidates(
    request_text: Any,
    *,
    conversation_anchors: Optional[dict[str, Any]] = None,
    max_candidates: int = MAX_ML_RESOURCE_CANDIDATES,
) -> dict[str, Any]:
    """Rank a bounded set of executable ML resource IDs before model planning."""

    catalog_version, resources = _mercado_livre_catalog()
    anchors = conversation_anchors if isinstance(conversation_anchors, dict) else {}
    resolved = anchors.get("resolved_context") if isinstance(anchors.get("resolved_context"), dict) else {}
    entity_hints = " ".join(
        _clean_text(resolved.get(key), 160)
        for key in (
            "sku", "mlb", "order_id", "pack_id", "claim_id", "return_id", "shipment_id",
            "payment_id", "question_id", "category_id", "product_id", "user_product_id", "period",
        )
        if _clean_text(resolved.get(key), 160)
    )
    normalized = _search_normalized(f"{_clean_text(request_text, 6000)} {entity_hints}")
    terms = {term for term in normalized.split() if len(term) >= 3}
    ranked: list[tuple[int, str, dict[str, Any], list[str]]] = []
    for resource in resources:
        policy = str(resource.get("policy") or "")
        if resource.get("read_only") is not True or policy in {"document_only", "tombstone"}:
            continue
        resource_id = str(resource.get("resource_id") or "")
        domain = str(resource.get("domain") or "")
        haystack = _search_normalized(
            " ".join(
                [
                    resource_id,
                    domain,
                    str(resource.get("path_template") or ""),
                    " ".join(str(item) for item in list(resource.get("required_path_params") or [])),
                    " ".join(str(item) for item in list(resource.get("allowed_query_params") or [])),
                ]
            )
        )
        score = sum(3 if term in _search_normalized(resource_id) else 1 for term in terms if term in haystack)
        reasons: list[str] = []
        for cue in _ML_DOMAIN_CUES.get(domain, ()):
            normalized_cue = _search_normalized(cue)
            if normalized_cue and normalized_cue in normalized:
                score += 12
                reasons.append(f"domain:{domain}")
                break
        for resource_prefix, cues in _ML_RESOURCE_CUES:
            if not resource_id.startswith(resource_prefix):
                continue
            if any(_search_normalized(cue) in normalized for cue in cues):
                score += 18
                reasons.append(f"resource_prefix:{resource_prefix.rstrip('.')}")
                break
        required_params = [str(item) for item in list(resource.get("required_path_params") or [])]
        for parameter in required_params:
            parameter_terms = set(_search_normalized(parameter).split())
            if parameter_terms & terms:
                score += 5
                reasons.append(f"entity:{parameter}")
        leaf = _search_normalized(resource_id.rsplit(".", 1)[-1])
        if leaf and leaf in normalized:
            score += 8
            reasons.append(f"resource:{leaf}")
        if score <= 0:
            continue
        candidate = {
            "resource_id": resource_id,
            "domain": domain,
            "purpose": resource_id.removeprefix("ml.").replace(".", " ").replace("_", " "),
            "required_path_params": required_params,
            "allowed_query_params": [str(item) for item in list(resource.get("allowed_query_params") or [])[:30]],
            "policy": policy,
            "ownership_rule": str(resource.get("ownership_rule") or ""),
            "selection_score": score,
            "selection_reasons": list(dict.fromkeys(reasons))[:6],
        }
        ranked.append((score, resource_id, candidate, reasons))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    maximum = max(1, min(MAX_ML_RESOURCE_CANDIDATES, int(max_candidates or MAX_ML_RESOURCE_CANDIDATES)))
    return {
        "catalog_version": catalog_version,
        "selection": "deterministic_top_n",
        "candidates": [item[2] for item in ranked[:maximum]],
    }


def _authorized_store_names(authorized_stores: Optional[Iterable[Any]]) -> list[str]:
    names: list[str] = []
    for item in list(authorized_stores or []):
        if isinstance(item, str):
            name = _clean_text(item, 180)
        elif isinstance(item, dict):
            name = _clean_text(item.get("name") or item.get("nome") or item.get("store") or item.get("label"), 180)
        else:
            name = ""
        if name and name.casefold() not in {existing.casefold() for existing in names}:
            names.append(name)
    return names


def _store_key(value: str) -> str:
    import unicodedata

    ascii_value = "".join(
        char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char)
    ).casefold()
    return _STORE_NORMALIZE_RE.sub("", ascii_value)


def _store_is_authorized(store_ref: str, authorized_stores: list[str]) -> bool:
    wanted = _store_key(store_ref)
    if not wanted:
        return True
    return any(wanted == _store_key(item) for item in authorized_stores)


def normalize_data_selection_plan(
    value: Any,
    *,
    allowed_tools: Optional[Iterable[Any]],
    authorized_stores: Optional[Iterable[Any]],
    max_calls: int = MAX_TOOL_CALLS,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Validate model output and enforce the read-only server boundary."""

    parsed = _parse_json_object(value)
    source_schema_version = _clean_text(parsed.get("schema_version"), 40) or LEGACY_SCHEMA_VERSION
    if source_schema_version not in {LEGACY_SCHEMA_VERSION, SCHEMA_VERSION}:
        raise DataSelectionPlanError("data_selection_unsupported_schema_version")
    action = _clean_text(parsed.get("action"), 40).lower()
    if action not in PLAN_ACTIONS:
        raise DataSelectionPlanError("data_selection_invalid_action")

    entities_raw = parsed.get("entities") if isinstance(parsed.get("entities"), dict) else {}
    # One-release compatibility for the previous Function Manager shape.
    entities = {
        "sku": _clean_text(entities_raw.get("sku") or parsed.get("sku"), 120),
        "mlb": _clean_text(
            entities_raw.get("mlb") or entities_raw.get("item_id") or parsed.get("item_id"), 60
        ).upper(),
        "order_id": _clean_text(entities_raw.get("order_id") or parsed.get("order_id"), 100),
        "pack_id": _clean_text(entities_raw.get("pack_id") or parsed.get("pack_id"), 100),
        "claim_id": _clean_text(entities_raw.get("claim_id") or parsed.get("claim_id"), 100),
        "return_id": _clean_text(entities_raw.get("return_id") or parsed.get("return_id"), 100),
        "shipment_id": _clean_text(entities_raw.get("shipment_id") or parsed.get("shipment_id"), 100),
        "payment_id": _clean_text(entities_raw.get("payment_id") or parsed.get("payment_id"), 100),
        "question_id": _clean_text(entities_raw.get("question_id") or parsed.get("question_id"), 100),
        "category_id": _clean_text(entities_raw.get("category_id") or parsed.get("category_id"), 100),
        "product_id": _clean_text(entities_raw.get("product_id") or parsed.get("product_id"), 100),
        "user_product_id": _clean_text(
            entities_raw.get("user_product_id") or parsed.get("user_product_id"), 100
        ),
        "period": _clean_text(entities_raw.get("period") or parsed.get("period"), 240),
        "store_ref": _clean_text(entities_raw.get("store_ref") or parsed.get("store"), 180),
        "store_mode": _clean_text(entities_raw.get("store_mode") or parsed.get("store_mode"), 20).lower() or "none",
    }
    if entities["store_mode"] not in {"single", "all", "none"}:
        raise DataSelectionPlanError("data_selection_invalid_store_mode")
    store_names = _authorized_store_names(authorized_stores)
    if entities["store_mode"] == "single":
        if not entities["store_ref"]:
            raise DataSelectionPlanError("data_selection_store_required")
        if not _store_is_authorized(entities["store_ref"], store_names):
            raise DataSelectionPlanError("data_selection_unauthorized_store")
    if entities["store_mode"] == "all":
        entities["store_ref"] = ""

    catalog = compact_tool_catalog(allowed_tools)
    allowed_ids = {item["tool_id"].casefold(): item["tool_id"] for item in catalog}
    ml_catalog_version, ml_resources = _mercado_livre_catalog()
    ml_resource_ids = {str(item.get("resource_id") or "") for item in ml_resources}
    requested_catalog_version = _clean_text(parsed.get("resource_catalog_version"), 80)
    limit = max(0, min(MAX_TOOL_CALLS, int(max_calls or MAX_TOOL_CALLS)))
    proposed: list[str] = []
    accepted: list[str] = []
    rejected: list[str] = []
    calls: list[dict[str, Any]] = []
    call_keys: set[str] = set()
    call_key_indexes: dict[str, int] = {}
    raw_to_accepted: dict[int, int] = {}
    raw_calls = parsed.get("tool_calls") if isinstance(parsed.get("tool_calls"), list) else []
    for raw_index, raw in enumerate(raw_calls):
        if not isinstance(raw, dict):
            continue
        proposed_id = _clean_text(raw.get("tool_id") or raw.get("name"), 120)
        if not proposed_id:
            continue
        proposed.append(proposed_id)
        canonical_id = allowed_ids.get(proposed_id.casefold())
        if not canonical_id:
            rejected.append(proposed_id)
            continue
        arguments = _parse_arguments(raw.get("arguments"))
        if canonical_id.casefold() == MERCADO_LIVRE_RESOURCE_TOOL_ID.casefold():
            resource_id = _clean_text(arguments.get("resource_id"), 160)
            if resource_id and resource_id not in ml_resource_ids:
                raise DataSelectionPlanError("data_selection_invalid_ml_resource_id")
            if requested_catalog_version and requested_catalog_version not in {ml_catalog_version, "unavailable"}:
                raise DataSelectionPlanError("data_selection_ml_catalog_version_mismatch")
        for store_key in ("loja", "store", "store_ref", "account", "conta"):
            argument_store = _clean_text(arguments.get(store_key), 180)
            if argument_store and not _store_is_authorized(argument_store, store_names):
                raise DataSelectionPlanError("data_selection_unauthorized_store")
        canonical_arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        key = f"{canonical_id.casefold()}:{canonical_arguments}"
        if key in call_keys:
            raw_to_accepted[raw_index] = call_key_indexes[key]
            continue
        if len(calls) >= limit:
            rejected.append(proposed_id)
            continue
        call_keys.add(key)
        call_index = len(calls)
        call_key_indexes[key] = call_index
        raw_to_accepted[raw_index] = call_index
        accepted.append(canonical_id)
        depends_on: list[int] = []
        for dependency in list(raw.get("depends_on") or [])[:MAX_TOOL_CALLS]:
            if not isinstance(dependency, int) or dependency < 0 or dependency >= raw_index:
                raise DataSelectionPlanError("data_selection_invalid_dependency")
            if dependency not in raw_to_accepted:
                raise DataSelectionPlanError("data_selection_missing_dependency")
            depends_on.append(raw_to_accepted[dependency])
        calls.append(
            {
                "tool_id": canonical_id,
                "arguments": canonical_arguments,
                "required": bool(raw.get("required", True)),
                "reason": _clean_text(raw.get("reason"), 600),
                "depends_on": list(dict.fromkeys(depends_on)),
                "expected_fields": _unique_texts(
                    raw.get("expected_fields"), maximum=20, item_limit=120
                ),
                "success_condition": _clean_text(raw.get("success_condition"), 500),
                "failure_policy": (
                    _clean_text(raw.get("failure_policy"), 40).lower()
                    if _clean_text(raw.get("failure_policy"), 40).lower() in {"fail_plan", "partial", "skip"}
                    else "fail_plan" if bool(raw.get("required", True)) else "partial"
                ),
            }
        )

    hub_raw = parsed.get("context_hub") if isinstance(parsed.get("context_hub"), dict) else {}
    hub_mode = _clean_text(hub_raw.get("mode"), 30).lower() or "not_applicable"
    if hub_mode not in CONTEXT_HUB_MODES:
        raise DataSelectionPlanError("data_selection_invalid_context_hub_mode")
    hub_filter_source = _strip_sensitive(
        hub_raw.get("filters") if isinstance(hub_raw.get("filters"), dict) else {}
    )
    if not isinstance(hub_filter_source, dict):
        hub_filter_source = {}
    hub_filter_store = _clean_text(hub_filter_source.get("store_ref"), 180)
    if hub_filter_store and not _store_is_authorized(hub_filter_store, store_names):
        raise DataSelectionPlanError("data_selection_unauthorized_store")
    hub_filter_candidates: dict[str, Any] = {
        "sku": _clean_text(hub_filter_source.get("sku"), 120),
        "mlb": _clean_text(hub_filter_source.get("mlb"), 60).upper(),
        "store_ref": hub_filter_store,
        "module": _clean_text(hub_filter_source.get("module") or hub_filter_source.get("domain"), 100),
        "source_type": _clean_text(hub_filter_source.get("source_type") or hub_filter_source.get("kind"), 100),
        "surface": _clean_text(hub_filter_source.get("surface") or hub_filter_source.get("environment"), 100),
        "ids": _unique_texts(
            hub_filter_source.get("ids") or hub_filter_source.get("entity_ids"), maximum=20, item_limit=160
        ),
        "document_types": _unique_texts(
            hub_filter_source.get("document_types"), maximum=10, item_limit=80
        ),
        "tags": _unique_texts(hub_filter_source.get("tags"), maximum=12, item_limit=80),
        "truth_class": _clean_text(hub_filter_source.get("truth_class"), 100),
        "authority": (
            _clean_text(hub_filter_source.get("authority"), 40).lower()
            if _clean_text(hub_filter_source.get("authority"), 40).lower()
            in {"authoritative", "verified_technical", "advisory", "unverified"}
            else ""
        ),
        "sensitivity": _clean_text(hub_filter_source.get("sensitivity"), 80),
        "valid_at": _clean_text(hub_filter_source.get("valid_at"), 40),
    }
    hub_filters = {key: value for key, value in hub_filter_candidates.items() if value not in ("", [], {})}
    try:
        top_k = int(hub_raw.get("top_k") or (MAX_CONTEXT_HUB_SNIPPETS if hub_mode != "not_applicable" else 0))
    except Exception:
        top_k = 0
    try:
        snippet_max_chars = int(hub_raw.get("snippet_max_chars") or MAX_CONTEXT_HUB_SNIPPET_CHARS)
    except Exception:
        snippet_max_chars = MAX_CONTEXT_HUB_SNIPPET_CHARS
    context_hub = {
        "mode": hub_mode,
        "query": _clean_text(hub_raw.get("query"), 1000) if hub_mode != "not_applicable" else "",
        "filters": hub_filters if hub_mode != "not_applicable" else {},
        "top_k": max(0, min(MAX_CONTEXT_HUB_SNIPPETS, top_k)) if hub_mode != "not_applicable" else 0,
        "snippet_max_chars": max(0, min(MAX_CONTEXT_HUB_SNIPPET_CHARS, snippet_max_chars))
        if hub_mode != "not_applicable"
        else 0,
    }
    if hub_mode != "not_applicable" and not context_hub["query"]:
        raise DataSelectionPlanError("data_selection_context_hub_query_required")

    if action == "answer_without_data" and (calls or hub_mode != "not_applicable"):
        raise DataSelectionPlanError("data_selection_answer_without_data_has_collection")
    if action == "collect" and not calls and hub_mode == "not_applicable":
        raise DataSelectionPlanError("data_selection_collect_without_source")
    if action in {"clarify", "mutation_candidate"}:
        calls = []
        accepted = []
        context_hub = {
            "mode": "not_applicable",
            "query": "",
            "filters": {},
            "top_k": 0,
            "snippet_max_chars": 0,
        }

    confidence_raw = parsed.get("confidence", 0)
    try:
        confidence = float(confidence_raw)
    except Exception:
        confidence = 0.0
    intents = _unique_texts(parsed.get("intents") or [parsed.get("intent")], maximum=12, item_limit=120)
    raw_intent_ids = parsed.get("intent_ids") if isinstance(parsed.get("intent_ids"), (list, tuple)) else []
    intent_ids = [
        normalized
        for item in raw_intent_ids
        if (normalized := re.sub(r"[^a-z0-9_.-]+", "_", _clean_text(item, 120).casefold()).strip("_.-"))
    ][:12]
    if not intent_ids:
        intent_ids = [
            normalized
            for item in intents
            if (normalized := re.sub(r"[^a-z0-9_.-]+", "_", item.casefold()).strip("_.-"))
        ][:12]
    coverage_raw = parsed.get("coverage") if isinstance(parsed.get("coverage"), dict) else {}
    expected_stores = _unique_texts(
        coverage_raw.get("expected_stores"), maximum=30, item_limit=180
    )
    if not expected_stores:
        expected_stores = (
            list(store_names)
            if entities["store_mode"] == "all"
            else [entities["store_ref"]] if entities["store_mode"] == "single" and entities["store_ref"] else []
        )
    for expected_store in expected_stores:
        if not _store_is_authorized(expected_store, store_names):
            raise DataSelectionPlanError("data_selection_unauthorized_store")
    completeness_condition = _clean_text(coverage_raw.get("completeness_condition"), 60).lower()
    if completeness_condition not in {
        "not_applicable", "single_result", "all_required_sources", "all_authorized_stores", "best_effort"
    }:
        completeness_condition = (
            "all_authorized_stores" if entities["store_mode"] == "all"
            else "all_required_sources" if action == "collect"
            else "not_applicable"
        )
    plan = {
        "schema_version": SCHEMA_VERSION,
        "source_schema_version": source_schema_version,
        "action": action,
        "intents": intents,
        "intent_ids": list(dict.fromkeys(intent_ids)),
        "entities": entities,
        "requested_fields": _unique_texts(parsed.get("requested_fields"), maximum=40, item_limit=120),
        "tool_calls": calls,
        "context_hub": context_hub,
        "coverage": {
            "expected_stores": expected_stores,
            "completeness_condition": completeness_condition,
            "stop_conditions": _unique_texts(
                coverage_raw.get("stop_conditions"), maximum=12, item_limit=240
            ),
        },
        "resource_catalog_version": ml_catalog_version,
        "missing_user_fields": _unique_texts(parsed.get("missing_user_fields"), maximum=12, item_limit=160),
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": _clean_text(parsed.get("reason"), 1200),
    }
    operational_fields = {
        _argument_key(item)
        for item in plan["requested_fields"]
        if any(marker in _argument_key(item) for marker in _CURRENT_OPERATIONAL_FIELD_MARKERS)
    }
    non_hub_calls = [item for item in calls if item.get("tool_id") != "context_hub_search"]
    if hub_mode != "not_applicable" and operational_fields and not non_hub_calls:
        raise DataSelectionPlanError("data_selection_context_hub_not_operational_source")
    if action == "clarify" and not plan["missing_user_fields"]:
        raise DataSelectionPlanError("data_selection_clarify_without_missing_fields")
    if _json_bytes(plan) > MAX_PLAN_BYTES:
        raise DataSelectionPlanError("data_selection_plan_too_large")
    return plan, {
        "proposed": proposed,
        "accepted": accepted,
        "rejected": rejected,
    }


def compact_evidence(value: Any, *, report: bool = False) -> Any:
    """Keep complete JSON fields while fitting the evidence contract.

    No serialized JSON is sliced.  Large lists lose trailing complete items and
    large mappings lose trailing complete fields, with an explicit truncation
    marker added only when it itself fits.
    """

    budget = MAX_REPORT_EVIDENCE_BYTES if report else MAX_EVIDENCE_BYTES

    def compact(node: Any, remaining: int) -> Any:
        if node is None or isinstance(node, (bool, int, float)):
            return node
        if isinstance(node, str):
            clean = _clean_text(node, min(4000, remaining))
            while clean and _json_bytes(clean) > remaining:
                clean = clean[: max(0, len(clean) - 64)].rstrip()
            return clean
        if isinstance(node, list):
            output: list[Any] = []
            for item in node:
                candidate = compact(item, max(64, remaining - _json_bytes(output)))
                if _json_bytes(output + [candidate]) > remaining:
                    break
                output.append(candidate)
            return output
        if isinstance(node, dict):
            output: dict[str, Any] = {}
            for raw_key, item in node.items():
                key = _clean_text(raw_key, 120)
                if not key or _argument_key(key) in _SENSITIVE_ARGUMENT_KEYS:
                    continue
                candidate = compact(item, max(64, remaining - _json_bytes(output)))
                attempt = {**output, key: candidate}
                if _json_bytes(attempt) > remaining:
                    break
                output = attempt
            return output
        return compact(_clean_text(node, 1000), remaining)

    cleaned = compact(_strip_sensitive(value), budget)
    if _json_bytes(cleaned) > budget:
        return {"truncated": True}
    return cleaned


def _bounded_prompt_payload(payload: dict[str, Any]) -> str:
    """Serialize a prompt payload without cutting JSON in the middle."""

    candidate = copy.deepcopy(payload)
    optional_fields = ("previous_evidence", "conversation_anchors", "job_prompt", "authorized_stores", "tool_catalog")
    while _json_bytes(candidate) > MAX_PLAN_BYTES:
        changed = False
        for field in optional_fields:
            value = candidate.get(field)
            if isinstance(value, list) and value:
                candidate[field] = value[:-1]
                changed = True
            elif isinstance(value, dict) and value:
                candidate[field] = dict(list(value.items())[:-1])
                changed = True
            elif isinstance(value, str) and value:
                candidate[field] = value[: max(0, len(value) // 2)]
                changed = True
            if changed:
                break
        if not changed:
            question = str(candidate.get("question") or "")
            if len(question) <= 200:
                raise DataSelectionPlanError("data_selection_prompt_too_large")
            candidate["question"] = question[: max(200, len(question) // 2)]
    return json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))


def build_planner_prompt(
    *,
    request_text: str,
    job_prompt: str,
    surface: str,
    allowed_tools: Optional[Iterable[Any]],
    authorized_stores: Optional[Iterable[Any]],
    conversation_anchors: Optional[dict[str, Any]],
    previous_evidence: Any,
    data_gap: Any,
    validation_error: str = "",
) -> str:
    tool_catalog = compact_tool_catalog(allowed_tools)
    has_ml_resource_tool = any(
        str(item.get("tool_id") or "").casefold() == MERCADO_LIVRE_RESOURCE_TOOL_ID.casefold()
        for item in tool_catalog
    )
    ml_resource_selection = (
        resolve_mercado_livre_resource_candidates(
            request_text,
            conversation_anchors=conversation_anchors,
        )
        if has_ml_resource_tool
        else {"catalog_version": "not_applicable", "selection": "not_applicable", "candidates": []}
    )
    if has_ml_resource_tool:
        for item in tool_catalog:
            if str(item.get("tool_id") or "").casefold() == MERCADO_LIVRE_RESOURCE_TOOL_ID.casefold():
                item["resource_catalog_version"] = ml_resource_selection["catalog_version"]
                item["resource_candidates"] = ml_resource_selection["candidates"]
    payload = {
        "contract": SCHEMA_VERSION,
        "surface": _clean_text(surface, 40) or "app",
        "question": _clean_text(request_text, 12000),
        "job_prompt": _clean_text(job_prompt, 6000),
        "conversation_anchors": _strip_sensitive(conversation_anchors or {}),
        "authorized_stores": _authorized_store_names(authorized_stores),
        "tool_catalog": tool_catalog,
        "resource_catalog_version": ml_resource_selection["catalog_version"],
        "previous_evidence": compact_evidence(previous_evidence or {}, report=False),
        "data_gap": compact_evidence(data_gap or {}, report=False),
        "validation_error": _clean_text(validation_error, 200),
    }
    payload_text = _bounded_prompt_payload(payload)
    return (
        "Planeje somente a selecao minima de dados para a pergunta. Nao responda ao usuario, nao execute "
        "ferramentas, nao invente IDs e nao inclua tenant, client_id, permissoes ou credenciais. Use apenas "
        "ferramentas read-only do catalogo. Dados atuais de estoque, preco, pedidos e vendas nunca podem vir do "
        "Context Hub. Se a pergunta pedir todas as lojas, mantenha store_mode=all e planeje consultas separadas "
        "por loja autorizada quando a ferramenta exigir loja; nunca misture saldos ou resultados entre contas. "
        "Para saldo de uma loja nomeada, escolha uma ferramenta cujo input_schema aceite loja/store; stock_data "
        "e apenas um saldo local agregado e nao comprova o saldo de uma loja especifica. "
        "conversation_anchors.resolved_context contem referencias ja resolvidas pelo Luna para esta conversa. "
        "Use loja, SKU, MLB e periodo dali quando ainda forem aplicaveis ao pedido atual; a pergunta atual explicita "
        "tem precedencia. Se o Luna nao resolveu um campo indispensavel, use clarify em vez de adivinhar. "
        "Quando usar mercado_livre_resource_query, escolha somente um resource_id exato de resource_candidates e "
        "copie resource_catalog_version para o plano; nunca invente URL, rota ou resource_id. Preencha coverage, "
        "expected_fields, success_condition e failure_policy de forma verificavel. "
        "Quando data_gap contiver attempted_tools ou attempted_call_signatures, produza um plano materialmente "
        "diferente: escolha outra fonte autorizada ou refine os argumentos. Nao repita a mesma combinacao de "
        "ferramenta e argumentos que ja retornou evidencia insuficiente, salvo se a evidencia marcar a falha como "
        "transitoria e retryable. "
        "Para pergunta geral use answer_without_data; para campo essencial ausente use clarify; "
        "para alteracao use mutation_candidate sem ferramentas. Retorne apenas o JSON do schema.\n\n" + payload_text
    )


PlannerCallable = Callable[..., Any]


def _isolated_planner_cwd() -> str:
    path = Path(tempfile.gettempdir()) / "jk-codex-data-selection"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


class _CodexPlannerRuntime:
    """One isolated warm Codex app-server used only for data planning."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._codex: Any = None
        self._available_models: list[str] = []
        self._effective_model = ""
        self._started_at = ""
        self._last_used_at = ""
        self._last_error_code = ""

    def _start_locked(self) -> Any:
        if self._codex is not None:
            return self._codex
        from backend.services import codex_console
        from openai_codex import Codex, CodexConfig

        codex_console._codex_apply_sdk_protocol_compat()
        runtime_bin = codex_console._codex_runtime_require_ready()
        client = Codex(
            CodexConfig(
                codex_bin=runtime_bin,
                env=codex_console._codex_sdk_env(),
                cwd=_isolated_planner_cwd(),
                config_overrides=codex_console._codex_nonfull_config_overrides(fast_mode=True),
            )
        )
        client.__enter__()
        self._codex = client
        self._started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            response = client.models(include_hidden=False)
            data = getattr(response, "data", None) or getattr(response, "models", None) or []
            self._available_models = list(
                dict.fromkeys(
                    _clean_text(
                        getattr(item, "id", None)
                        or getattr(item, "model", None)
                        or (item.get("id") if isinstance(item, dict) else ""),
                        100,
                    )
                    for item in data
                )
            )
            self._available_models = [item for item in self._available_models if item]
        except Exception:
            self._available_models = []
        return client

    def _close_locked(self) -> None:
        client, self._codex = self._codex, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def resolve_model(self, requested: str) -> str:
        value = _clean_text(requested, 100).removeprefix("codex:") or DEFAULT_MODEL
        with self._lock:
            self._start_locked()
            if not self._available_models or value in self._available_models:
                return value
            for fallback in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.5", "gpt-5.4"):
                if fallback in self._available_models:
                    return fallback
        raise RuntimeError("data_selection_model_unavailable")

    def run(
        self,
        *,
        prompt: str,
        model: str,
        reasoning_effort: str,
        client_id: str = "",
        trace_id: str = "",
        telemetry_manage_trace: bool = True,
        attempt: int = 0,
    ) -> dict[str, Any]:
        from backend.services import codex_console
        from openai_codex.generated.v2_all import ReasoningSummary

        tenant = _clean_text(client_id, 80) or "default"
        telemetry_trace_id = _clean_text(trace_id, 200) or uuid.uuid4().hex
        span_id = f"{telemetry_trace_id}:selector:{max(0, int(attempt or 0))}"
        started = time.perf_counter()
        telemetry = None
        effective_model = _clean_text(model, 100).removeprefix("codex:") or DEFAULT_MODEL
        try:
            telemetry = codex_console._codex_ai_telemetry_instance()
            telemetry.schedule_retention(tenant)
            telemetry.start_span(
                tenant,
                trace_id=telemetry_trace_id,
                span_id=span_id,
                stage="selector",
            )
        except Exception:
            telemetry = None
        with self._lock:
            try:
                client = self._start_locked()
                effective_model = self.resolve_model(model)
                service_tier = codex_console._codex_normalizar_service_tier("priority", "fast")
                thread = client.thread_start(
                    cwd=_isolated_planner_cwd(),
                    model=effective_model,
                    approval_mode=codex_console._codex_approval_mode_enum("read_only", "read_only"),
                    sandbox=codex_console._codex_sandbox_enum("read_only"),
                    ephemeral=True,
                    developer_instructions=(
                        "Voce e o CodexDataSelectionAgent do Black Jhon. Voce nao conversa com o usuario, nao "
                        "possui ferramentas nem credenciais e nao responde a pergunta. Sua unica funcao e produzir "
                        "um plano minimo e estruturado de coleta read-only a partir do catalogo permitido pelo servidor."
                    ),
                    service_tier=service_tier,
                )
                result = thread.run(
                    prompt,
                    model=effective_model,
                    effort=codex_console._codex_reasoning_effort_enum(reasoning_effort),
                    approval_mode=codex_console._codex_approval_mode_enum("read_only", "read_only"),
                    output_schema=DATA_SELECTION_PLAN_SCHEMA,
                    summary=ReasoningSummary.model_validate("none"),
                    service_tier=service_tier,
                )
                self._effective_model = effective_model
                self._last_used_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._last_error_code = ""
                duration_ms = (time.perf_counter() - started) * 1000
                if telemetry is not None:
                    telemetry.finish_span(
                        tenant,
                        trace_id=telemetry_trace_id,
                        span_id=span_id,
                        stage="selector",
                        status="completed",
                        duration_ms=duration_ms,
                    )
                    telemetry.record_event(
                        tenant,
                        event_id=f"{telemetry_trace_id}:selector:{max(0, int(attempt or 0))}",
                        trace_id=telemetry_trace_id,
                        span_id=span_id,
                        event_type="inference",
                        status="completed",
                        requested_model=model,
                        effective_model=effective_model,
                        provider="openai_codex",
                        provider_path="data_selection_agent",
                        model_rerouted=effective_model != _clean_text(model, 100).removeprefix("codex:"),
                        duration_ms=duration_ms,
                        dimensions={"surface": "data_selection", "category": "selector"},
                    )
                return {
                    "response": getattr(result, "final_response", ""),
                    "effective_model": effective_model,
                }
            except Exception as exc:
                self._last_error_code = type(exc).__name__[:120]
                self._close_locked()
                duration_ms = (time.perf_counter() - started) * 1000
                if telemetry is not None:
                    telemetry.finish_span(
                        tenant,
                        trace_id=telemetry_trace_id,
                        span_id=span_id,
                        stage="selector",
                        status="failed",
                        duration_ms=duration_ms,
                        error_code=type(exc).__name__,
                    )
                    telemetry.record_event(
                        tenant,
                        event_id=f"{telemetry_trace_id}:selector:{max(0, int(attempt or 0))}",
                        trace_id=telemetry_trace_id,
                        span_id=span_id,
                        event_type="inference",
                        status="failed",
                        requested_model=model,
                        effective_model=effective_model,
                        provider="openai_codex",
                        provider_path="data_selection_agent",
                        duration_ms=duration_ms,
                        error_code=type(exc).__name__,
                        dimensions={"surface": "data_selection", "category": "selector"},
                    )
                raise

    def warm(self, model: str) -> dict[str, Any]:
        with self._lock:
            effective = self.resolve_model(model)
            self._effective_model = effective
            return {"ready": True, "effective_model": effective}

    def diagnostics(self) -> dict[str, Any]:
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {
                "ready": self._codex is not None and not self._last_error_code,
                "busy": True,
                "effective_model": self._effective_model,
                "last_error_code": self._last_error_code,
            }
        try:
            return {
                "ready": self._codex is not None and not self._last_error_code,
                "busy": False,
                "effective_model": self._effective_model,
                "started_at": self._started_at,
                "last_used_at": self._last_used_at,
                "last_error_code": self._last_error_code,
            }
        finally:
            self._lock.release()

    def close(self) -> None:
        with self._lock:
            self._close_locked()


class CodexDataSelectionRuntime:
    """Small isolated pool plus aggregate, content-free diagnostics."""

    def __init__(self, default_size: int = 4, planner: Optional[PlannerCallable] = None) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._slots: list[_CodexPlannerRuntime] = []
        self._available: deque[_CodexPlannerRuntime] = deque()
        self._busy: set[int] = set()
        self._target_size = max(1, min(8, int(default_size or 4)))
        self._planner = planner
        self._stats_lock = threading.RLock()
        self._counts: Counter[str] = Counter()
        self._latency_total_ms = 0.0
        self._latency_max_ms = 0.0
        self._last_prompt_bytes = 0
        self._last_plan_bytes = 0
        self._last_evidence_bytes = 0
        self._proposed_tools: Counter[str] = Counter()
        self._accepted_tools: Counter[str] = Counter()
        self._rejected_tools: Counter[str] = Counter()
        self._last_error_code = ""
        self._closing = False
        self._telemetry_local = threading.local()

    @contextmanager
    def telemetry_scope(
        self,
        *,
        client_id: str,
        trace_id: str = "",
        manage_trace: bool = True,
    ):
        """Bind server-only telemetry metadata without exposing tenant IDs to the planner."""

        previous = getattr(self._telemetry_local, "context", None)
        self._telemetry_local.context = {
            "client_id": _clean_text(client_id, 80),
            "trace_id": _clean_text(trace_id, 200),
            "manage_trace": bool(manage_trace),
        }
        try:
            yield
        finally:
            self._telemetry_local.context = previous

    def _checkout(self, timeout: float = 60.0) -> _CodexPlannerRuntime:
        deadline = time.monotonic() + max(0.1, float(timeout or 0.1))
        with self._condition:
            while not self._available and not self._closing:
                if not self._slots:
                    slot = _CodexPlannerRuntime()
                    self._slots.append(slot)
                    self._available.append(slot)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("data_selection_runtime_pool_unavailable")
                self._condition.wait(min(remaining, 1.0))
            if self._closing:
                raise RuntimeError("data_selection_runtime_pool_closed")
            slot = self._available.popleft()
            self._busy.add(id(slot))
            return slot

    def _release(self, slot: _CodexPlannerRuntime) -> None:
        with self._condition:
            self._busy.discard(id(slot))
            if slot in self._slots and not self._closing:
                self._available.append(slot)
            self._condition.notify_all()

    def _invoke_planner(
        self,
        *,
        prompt: str,
        model: str,
        reasoning_effort: str,
        attempt: int,
        client_id: str = "",
        trace_id: str = "",
        telemetry_manage_trace: bool = True,
    ) -> Any:
        if self._planner is not None:
            return self._planner(
                prompt=prompt,
                model=model,
                reasoning_effort=reasoning_effort,
                attempt=attempt,
                output_schema=DATA_SELECTION_PLAN_SCHEMA,
            )
        slot = self._checkout()
        try:
            result = slot.run(
                prompt=prompt,
                model=model,
                reasoning_effort=reasoning_effort,
                client_id=client_id,
                trace_id=trace_id,
                telemetry_manage_trace=telemetry_manage_trace,
                attempt=attempt,
            )
            return result.get("response")
        finally:
            self._release(slot)

    def plan(
        self,
        *,
        request_text: str,
        job_prompt: str = "",
        surface: str = "app",
        allowed_tools: Optional[Iterable[Any]] = None,
        authorized_stores: Optional[Iterable[Any]] = None,
        conversation_anchors: Optional[dict[str, Any]] = None,
        previous_evidence: Any = None,
        data_gap: Any = None,
        model: str = DEFAULT_MODEL,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
        max_calls: int = MAX_TOOL_CALLS,
    ) -> dict[str, Any]:
        if not _clean_text(request_text, 12000):
            raise DataSelectionPlanError("data_selection_question_required")
        started = time.monotonic()
        telemetry_context = getattr(self._telemetry_local, "context", None)
        telemetry_context = telemetry_context if isinstance(telemetry_context, dict) else {}
        client_id = _clean_text(telemetry_context.get("client_id"), 80)
        trace_id = _clean_text(telemetry_context.get("trace_id"), 200)
        telemetry_manage_trace = telemetry_context.get("manage_trace") is not False
        tenant = _clean_text(client_id, 80) or "default"
        telemetry_trace_id = _clean_text(trace_id, 200) or uuid.uuid4().hex
        telemetry = None
        if telemetry_manage_trace and client_id:
            try:
                from backend.services import codex_console

                telemetry = codex_console._codex_ai_telemetry_instance()
                telemetry.schedule_retention(tenant)
                telemetry.start_trace(
                    tenant,
                    trace_id=telemetry_trace_id,
                    surface=surface,
                    category="selector",
                    requested_model=model,
                    expected_spans=("selector",),
                )
            except Exception:
                telemetry = None
        validation_error = ""
        last_error: Optional[Exception] = None
        for attempt in range(2):
            prompt = build_planner_prompt(
                request_text=request_text,
                job_prompt=job_prompt,
                surface=surface,
                allowed_tools=allowed_tools,
                authorized_stores=authorized_stores,
                conversation_anchors=conversation_anchors,
                previous_evidence=previous_evidence,
                data_gap=data_gap,
                validation_error=validation_error,
            )
            with self._stats_lock:
                self._last_prompt_bytes = len(prompt.encode("utf-8"))
            try:
                raw = self._invoke_planner(
                    prompt=prompt,
                    model=_clean_text(model, 100) or DEFAULT_MODEL,
                    reasoning_effort=_clean_text(reasoning_effort, 20) or DEFAULT_REASONING_EFFORT,
                    attempt=attempt,
                    client_id=client_id,
                    trace_id=telemetry_trace_id,
                    telemetry_manage_trace=False,
                )
                plan, tool_stats = normalize_data_selection_plan(
                    raw,
                    allowed_tools=allowed_tools,
                    authorized_stores=authorized_stores,
                    max_calls=max_calls,
                )
                latency_ms = (time.monotonic() - started) * 1000
                with self._stats_lock:
                    self._counts["plans"] += 1
                    if attempt:
                        self._counts["replans"] += 1
                    self._latency_total_ms += latency_ms
                    self._latency_max_ms = max(self._latency_max_ms, latency_ms)
                    self._last_plan_bytes = _json_bytes(plan)
                    self._last_evidence_bytes = _json_bytes(compact_evidence(previous_evidence or {}))
                    self._proposed_tools.update(tool_stats["proposed"])
                    self._accepted_tools.update(tool_stats["accepted"])
                    self._rejected_tools.update(tool_stats["rejected"])
                    self._last_error_code = ""
                if telemetry is not None:
                    telemetry.finish_trace(
                        tenant,
                        trace_id=telemetry_trace_id,
                        status="completed",
                        effective_model=_clean_text(model, 100).removeprefix("codex:"),
                        provider="openai_codex",
                        duration_ms=latency_ms,
                    )
                return plan
            except Exception as exc:
                last_error = exc
                validation_error = (
                    _clean_text(str(exc), 200)
                    if isinstance(exc, DataSelectionPlanError)
                    else "planner_runtime_error"
                )
                with self._stats_lock:
                    if isinstance(exc, DataSelectionPlanError):
                        self._counts["schema_failures"] += 1
                    else:
                        self._counts["runtime_failures"] += 1
                        if "timeout" in type(exc).__name__.casefold() or "timeout" in str(exc).casefold():
                            self._counts["timeouts"] += 1
                if attempt == 0:
                    continue
        with self._stats_lock:
            self._counts["failures"] += 1
            self._last_error_code = (
                _clean_text(str(last_error), 160)
                if isinstance(last_error, DataSelectionPlanError)
                else "planner_runtime_error"
            )
        if telemetry is not None:
            telemetry.finish_trace(
                tenant,
                trace_id=telemetry_trace_id,
                status="failed",
                effective_model=_clean_text(model, 100).removeprefix("codex:"),
                provider="openai_codex",
                duration_ms=(time.monotonic() - started) * 1000,
                error_code=self._last_error_code,
            )
        raise RuntimeError(f"data_selection_agent_failed:{self._last_error_code}") from last_error

    def record_evidence_size(self, value: Any, *, report: bool = False) -> int:
        """Record only the bounded byte count; never retain commercial evidence."""

        size = _json_bytes(compact_evidence(value, report=report))
        with self._stats_lock:
            self._last_evidence_bytes = size
        return size

    def warm(self, conversation_model: str = DEFAULT_MODEL, task_model: str = "", pool_size: Optional[int] = None) -> dict[str, Any]:
        del task_model
        if self._planner is not None:
            return self.diagnostics()
        target = max(1, min(8, int(pool_size or self._target_size)))
        additions: list[_CodexPlannerRuntime] = []
        with self._condition:
            current = len(self._slots)
            self._target_size = target
        try:
            for _ in range(max(0, target - current)):
                slot = _CodexPlannerRuntime()
                slot.warm(conversation_model or DEFAULT_MODEL)
                additions.append(slot)
        except Exception:
            for slot in additions:
                slot.close()
            raise
        with self._condition:
            for slot in additions:
                self._slots.append(slot)
                self._available.append(slot)
            self._condition.notify_all()
        return self.diagnostics()

    def diagnostics(self) -> dict[str, Any]:
        with self._condition:
            slots = list(self._slots)
            busy = len(self._busy)
            target = self._target_size
        details = [slot.diagnostics() for slot in slots]
        healthy = sum(1 for item in details if item.get("ready"))
        with self._stats_lock:
            plans = int(self._counts["plans"])
            diagnostics = {
                "ready": bool(self._planner) or (bool(slots) and healthy == len(slots)),
                "model": next(
                    (str(item.get("effective_model") or "") for item in details if item.get("effective_model")),
                    DEFAULT_MODEL,
                ),
                "pool_size": len(slots),
                "target_pool_size": target,
                "busy": busy,
                "plans": plans,
                "average_latency_ms": round(self._latency_total_ms / plans, 2) if plans else 0.0,
                "max_latency_ms": round(self._latency_max_ms, 2),
                "last_prompt_bytes": self._last_prompt_bytes,
                "last_plan_bytes": self._last_plan_bytes,
                "last_evidence_bytes": self._last_evidence_bytes,
                "replans": int(self._counts["replans"]),
                "schema_failures": int(self._counts["schema_failures"]),
                "runtime_failures": int(self._counts["runtime_failures"]),
                "timeouts": int(self._counts["timeouts"]),
                "failures": int(self._counts["failures"]),
                "proposed_tools": dict(self._proposed_tools),
                "accepted_tools": dict(self._accepted_tools),
                "rejected_tools": dict(self._rejected_tools),
                "last_error_code": self._last_error_code,
            }
        return diagnostics

    def close(self) -> None:
        with self._condition:
            self._closing = True
            slots = list(self._slots)
            self._slots.clear()
            self._available.clear()
            self._condition.notify_all()
        for slot in slots:
            slot.close()


DATA_SELECTION_RUNTIME = CodexDataSelectionRuntime(default_size=4)


__all__ = [
    "CodexDataSelectionRuntime",
    "DATA_SELECTION_PLAN_SCHEMA",
    "DATA_SELECTION_PLAN_V2_SCHEMA",
    "DATA_SELECTION_RUNTIME",
    "DEFAULT_MODEL",
    "DataSelectionPlanError",
    "LEGACY_SCHEMA_VERSION",
    "MAX_CONTEXT_HUB_SNIPPETS",
    "MAX_CONTEXT_HUB_SNIPPET_CHARS",
    "MAX_EVIDENCE_BYTES",
    "MAX_PLAN_BYTES",
    "MAX_ML_RESOURCE_CANDIDATES",
    "MAX_REPORT_EVIDENCE_BYTES",
    "MERCADO_LIVRE_RESOURCE_TOOL_ID",
    "build_planner_prompt",
    "compact_evidence",
    "compact_tool_catalog",
    "normalize_data_selection_plan",
    "resolve_mercado_livre_resource_candidates",
]
