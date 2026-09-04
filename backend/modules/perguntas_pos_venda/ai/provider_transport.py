"""Low-level network transports without dependencies on the PPV state graph."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping

import httpx
import requests

from backend.schemas.ia import IAChatRequest
from backend.services.ia_providers import (
    _chamar_codex_chat,
    _chamar_codex_chat_com_thread,
    _chamar_deepseek_chat,
    _chamar_gemini_chat,
    _chamar_openai_responses,
    _chamar_vertex_ai_chat,
    _codex_modelo_nome_curto,
    _gemini_nome_curto,
    _modelo_eh_codex,
    _modelo_eh_gemini_api,
    _modelo_eh_vertex_ai,
    _vertex_ai_modelo_padrao,
    _vertex_modelo_nome_curto,
)
from .deep_research_contracts import PUBLIC_RESEARCH_MAX_DECOMPRESSED_BYTES


TECHNICAL_QUESTION_PLAN_SCHEMA_VERSION = "jk_ml_technical_question_plan_v1"
TECHNICAL_EVIDENCE_GRAPH_SCHEMA_VERSION = "jk_ml_evidence_graph_v2"
TECHNICAL_RESOLUTION_SCHEMA_VERSION = "jk_ml_technical_resolution_v1"
FACTUAL_REVIEW_SCHEMA_VERSION = "jk_ml_factual_review_v1"

_REQUIREMENT_KINDS = [
    "compatibility",
    "application",
    "installation_location",
    "function",
    "specification",
    "reference_relation",
    "originality",
    "kit",
    "variation",
    "other",
]
_REQUIREMENT_RELATIONS = [
    "fits",
    "mounted_on",
    "installed_in",
    "used_in",
    "part_of",
    "applies_to",
    "compatible_with",
    "has_function",
    "has_property",
    "replaces",
    "superseded_by",
    "equivalent_to",
    "includes",
    "is_original",
    "other",
]
_TECHNICAL_DECISIONS = ["yes", "no", "conditional", "insufficient", "not_applicable"]
_COMMERCIAL_STATES = ["fits", "variant", "partial", "insufficient", "incompatible", "not_applicable"]

_IDENTIFIER_ORIGIN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["value", "origin", "source_ref"],
    "properties": {
        "value": {"type": "string", "maxLength": 240},
        "origin": {"type": "string", "maxLength": 160},
        "source_ref": {"type": "string", "maxLength": 700},
    },
}

_SUBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "name", "identifiers", "identifier_origins"],
    "properties": {
        "kind": {"type": "string", "maxLength": 100},
        "name": {"type": "string", "maxLength": 500},
        "identifiers": {
            "type": "array",
            "maxItems": 20,
            "items": {"type": "string", "maxLength": 240},
        },
        "identifier_origins": {
            "type": "array", "maxItems": 20,
            "items": _IDENTIFIER_ORIGIN_SCHEMA,
        },
    },
}

_EVIDENCE_GRAPH_ENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "kind", "name", "identifiers"],
    "properties": {
        "id": {"type": "string", "maxLength": 100},
        "kind": {"type": "string", "maxLength": 100},
        "name": {"type": "string", "maxLength": 500},
        "identifiers": {
            "type": "array",
            "maxItems": 20,
            "items": {"type": "string", "maxLength": 240},
        },
    },
}

_EVIDENCE_GRAPH_CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id", "entity_id", "field_name", "value", "unit", "source_refs",
        "support", "requirement_ids",
    ],
    "properties": {
        "id": {"type": "string", "maxLength": 100},
        "entity_id": {"type": "string", "maxLength": 100},
        "field_name": {"type": "string", "maxLength": 160},
        "value": {"type": "string", "maxLength": 1600},
        "unit": {"type": "string", "maxLength": 80},
        "source_refs": {
            "type": "array", "maxItems": 16,
            "items": {"type": "string", "maxLength": 1000},
        },
        "support": {"type": "string", "enum": ["supports", "refutes", "context"]},
        "requirement_ids": {
            "type": "array", "maxItems": 8,
            "items": {"type": "string", "maxLength": 100},
        },
    },
}

_EVIDENCE_GRAPH_PASSAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id", "source_ref", "section_ref", "text", "matched_identifiers",
        "requirement_ids",
    ],
    "properties": {
        "id": {"type": "string", "maxLength": 100},
        "source_ref": {"type": "string", "maxLength": 1000},
        "section_ref": {"type": "string", "maxLength": 300},
        "text": {"type": "string", "maxLength": 2400},
        "matched_identifiers": {
            "type": "array", "maxItems": 20,
            "items": {"type": "string", "maxLength": 240},
        },
        "requirement_ids": {
            "type": "array", "maxItems": 8,
            "items": {"type": "string", "maxLength": 100},
        },
    },
}

_EVIDENCE_GRAPH_RELATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id", "from_entity_id", "relation", "to_entity_id", "claim_ids",
        "source_refs", "requirement_ids",
    ],
    "properties": {
        "id": {"type": "string", "maxLength": 100},
        "from_entity_id": {"type": "string", "maxLength": 100},
        "relation": {"type": "string", "enum": _REQUIREMENT_RELATIONS},
        "to_entity_id": {"type": "string", "maxLength": 100},
        "claim_ids": {
            "type": "array", "maxItems": 20,
            "items": {"type": "string", "maxLength": 100},
        },
        "source_refs": {
            "type": "array", "maxItems": 16,
            "items": {"type": "string", "maxLength": 1000},
        },
        "requirement_ids": {
            "type": "array", "maxItems": 8,
            "items": {"type": "string", "maxLength": 100},
        },
    },
}

TECHNICAL_EVIDENCE_GRAPH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema", "entities", "claims", "passages", "relations",
        "unresolved_requirement_ids",
    ],
    "properties": {
        "schema": {"type": "string", "enum": [TECHNICAL_EVIDENCE_GRAPH_SCHEMA_VERSION]},
        "entities": {
            "type": "array", "maxItems": 40,
            "items": _EVIDENCE_GRAPH_ENTITY_SCHEMA,
        },
        "claims": {
            "type": "array", "maxItems": 120,
            "items": _EVIDENCE_GRAPH_CLAIM_SCHEMA,
        },
        "passages": {
            "type": "array", "maxItems": 80,
            "items": _EVIDENCE_GRAPH_PASSAGE_SCHEMA,
        },
        "relations": {
            "type": "array", "maxItems": 80,
            "items": _EVIDENCE_GRAPH_RELATION_SCHEMA,
        },
        "unresolved_requirement_ids": {
            "type": "array", "maxItems": 8,
            "items": {"type": "string", "maxLength": 100},
        },
    },
}

_TECHNICAL_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["type", "query", "requirement_ids", "preferred_authority"],
    "properties": {
        "type": {"type": "string", "maxLength": 100},
        "query": {"type": "string", "maxLength": 1000},
        "requirement_ids": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 100},
        },
        "preferred_authority": {"type": "string", "maxLength": 200},
    },
}

TECHNICAL_QUESTION_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "requirements", "queries"],
    "properties": {
        "schema": {"type": "string", "enum": [TECHNICAL_QUESTION_PLAN_SCHEMA_VERSION]},
        "requirements": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "essential",
                    "kind",
                    "question",
                    "subject",
                    "target",
                    "relation",
                    "required_fields",
                    "search_terms",
                ],
                "properties": {
                    "id": {"type": "string", "maxLength": 100},
                    "essential": {"type": "boolean"},
                    "kind": {"type": "string", "enum": _REQUIREMENT_KINDS},
                    "question": {"type": "string", "maxLength": 600},
                    "subject": _SUBJECT_SCHEMA,
                    "target": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["kind", "name", "identifiers", "identifier_origins"],
                        "properties": {
                            "kind": {"type": "string", "maxLength": 100},
                            "name": {"type": "string", "maxLength": 500},
                            "identifiers": {
                                "type": "array",
                                "maxItems": 20,
                                "items": {"type": "string", "maxLength": 240},
                            },
                            "identifier_origins": {
                                "type": "array", "maxItems": 20,
                                "items": _IDENTIFIER_ORIGIN_SCHEMA,
                            },
                        },
                    },
                    "relation": {"type": "string", "enum": _REQUIREMENT_RELATIONS},
                    "required_fields": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {"type": "string", "maxLength": 160},
                    },
                    "search_terms": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {"type": "string", "maxLength": 500},
                    },
                },
            },
        },
        "queries": {
            "type": "array",
            "maxItems": 4,
            "items": _TECHNICAL_QUERY_SCHEMA,
        },
    },
}

_TECHNICAL_FACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["field_name", "relation", "value", "source_refs", "support"],
    "properties": {
        "field_name": {"type": "string", "maxLength": 160},
        "relation": {"type": "string", "maxLength": 120},
        "value": {"type": "string", "maxLength": 1200},
        "source_refs": {
            "type": "array",
            "maxItems": 16,
            "items": {"type": "string", "maxLength": 700},
        },
        "support": {"type": "string", "enum": ["supports", "refutes", "context"]},
    },
}

_COMPATIBILITY_COMPARISON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "attribute",
        "product_value",
        "target_value",
        "unit",
        "result",
        "decisive",
        "evidence_refs",
    ],
    "properties": {
        "attribute": {"type": "string", "maxLength": 160},
        "product_value": {"type": "string", "maxLength": 600},
        "target_value": {"type": "string", "maxLength": 600},
        "unit": {"type": "string", "maxLength": 80},
        "result": {"type": "string", "enum": ["match", "conflict", "missing", "unknown"]},
        "decisive": {"type": "boolean"},
        "evidence_refs": {
            "type": "array",
            "maxItems": 16,
            "items": {"type": "string", "maxLength": 700},
        },
    },
}

_COMPATIBILITY_EVIDENCE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_type", "authority", "reference", "url", "grounded"],
    "properties": {
        "source_type": {"type": "string", "maxLength": 120},
        "authority": {"type": "string", "maxLength": 120},
        "reference": {"type": "string", "maxLength": 800},
        "url": {"type": "string", "maxLength": 1000},
        "grounded": {"type": "boolean"},
    },
}

_COMPATIBILITY_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["product", "target", "target_vehicle", "equivalence"],
    "properties": {
        key: {
            "type": "array",
            "maxItems": 8,
            "items": _COMPATIBILITY_EVIDENCE_ITEM_SCHEMA,
        }
        for key in ("product", "target", "target_vehicle", "equivalence")
    },
}

_COMPATIBILITY_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["type", "query"],
    "properties": {
        "type": {"type": "string", "maxLength": 80},
        "query": {"type": "string", "maxLength": 300},
    },
}

_COMPATIBILITY_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "target_type",
        "target_item",
        "target_vehicle",
        "compatibility_profile",
        "product_interface",
        "target_interface",
        "comparison_attributes",
        "decision",
        "condition",
        "missing_fields",
        "evidence",
        "queries",
        "sources",
        "confidence",
        "reason",
    ],
    "properties": {
        "target_type": {
            "type": "string",
            "enum": [
                "vehicle",
                "machine_tool",
                "phone_computing",
                "electrical_electronic",
                "hydraulic",
                "dimensional",
                "generic",
            ],
        },
        "target_item": {"type": "string", "maxLength": 300},
        "target_vehicle": {"type": "string", "maxLength": 300},
        "compatibility_profile": {"type": "string", "maxLength": 120},
        "product_interface": {"type": "string", "maxLength": 1200},
        "target_interface": {"type": "string", "maxLength": 1200},
        "comparison_attributes": {
            "type": "array",
            "maxItems": 24,
            "items": _COMPATIBILITY_COMPARISON_SCHEMA,
        },
        "decision": {"type": "string", "enum": ["yes", "no", "conditional", "insufficient"]},
        "condition": {"type": "string", "maxLength": 1200},
        "missing_fields": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string", "maxLength": 160},
        },
        "evidence": _COMPATIBILITY_EVIDENCE_SCHEMA,
        "queries": {
            "type": "array",
            "maxItems": 12,
            "items": _COMPATIBILITY_QUERY_SCHEMA,
        },
        "sources": {
            "type": "array",
            "maxItems": 16,
            "items": {"type": "string", "maxLength": 700},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 1200},
    },
}

TECHNICAL_RESOLUTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema",
        "round",
        "final",
        "requirements",
        "reference_relations",
        "overall_decision",
        "commercial_state",
        "confidence",
        "reason",
        "gap_queries",
        "contingency_answer_body",
        "compatibility_analysis",
    ],
    "properties": {
        "schema": {"type": "string", "enum": [TECHNICAL_RESOLUTION_SCHEMA_VERSION]},
        "round": {"type": "integer", "minimum": 1, "maximum": 2},
        "final": {"type": "boolean"},
        "requirements": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "decision",
                    "conclusion",
                    "condition",
                    "commercial_impact",
                    "facts",
                    "missing_fields",
                    "confidence",
                ],
                "properties": {
                    "id": {"type": "string", "maxLength": 100},
                    "decision": {"type": "string", "enum": _TECHNICAL_DECISIONS},
                    "conclusion": {"type": "string", "maxLength": 1600},
                    "condition": {"type": "string", "maxLength": 1200},
                    "commercial_impact": {
                        "type": "string",
                        "enum": ["satisfies", "variant", "partial", "incompatible", "informational", "unknown"],
                    },
                    "facts": {
                        "type": "array",
                        "maxItems": 32,
                        "items": _TECHNICAL_FACT_SCHEMA,
                    },
                    "missing_fields": {
                        "type": "array",
                        "maxItems": 12,
                        "items": {"type": "string", "maxLength": 240},
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "reference_relations": {
            "type": "array",
            "maxItems": 24,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["from_code", "relation", "to_code", "source_refs"],
                "properties": {
                    "from_code": {"type": "string", "maxLength": 240},
                    "relation": {
                        "type": "string",
                        "enum": [
                            "superseded_by",
                            "replaces",
                            "equivalent_to",
                            "application_specific",
                            "unrelated",
                            "unresolved",
                        ],
                    },
                    "to_code": {"type": "string", "maxLength": 240},
                    "source_refs": {
                        "type": "array",
                        "maxItems": 16,
                        "items": {"type": "string", "maxLength": 700},
                    },
                },
            },
        },
        "overall_decision": {"type": "string", "enum": _TECHNICAL_DECISIONS},
        "commercial_state": {"type": "string", "enum": _COMMERCIAL_STATES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 2000},
        "gap_queries": {
            "type": "array",
            "maxItems": 4,
            "items": _TECHNICAL_QUERY_SCHEMA,
        },
        "contingency_answer_body": {"type": "string", "maxLength": 4000},
        "compatibility_analysis": _COMPATIBILITY_ANALYSIS_SCHEMA,
    },
}

FACTUAL_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "verdict", "issues", "revision_instructions", "confidence"],
    "properties": {
        "schema": {"type": "string", "enum": [FACTUAL_REVIEW_SCHEMA_VERSION]},
        "verdict": {"type": "string", "enum": ["pass", "revise", "insufficient"]},
        "issues": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "message", "claim", "source_refs"],
                "properties": {
                    "code": {
                        "type": "string",
                        "enum": [
                            "unsupported_claim",
                            "false_conflict",
                            "missing_subquestion",
                            "redundant_question",
                            "commercial_mismatch",
                            "privacy_issue",
                            "other",
                        ],
                    },
                    "message": {"type": "string", "maxLength": 1200},
                    "claim": {"type": "string", "maxLength": 1200},
                    "source_refs": {
                        "type": "array",
                        "maxItems": 16,
                        "items": {"type": "string", "maxLength": 700},
                    },
                },
            },
        },
        "revision_instructions": {
            "type": "array",
            "maxItems": 16,
            "items": {"type": "string", "maxLength": 1200},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

_CODEX_STRUCTURED_STAGE_SCHEMAS: dict[str, dict[str, Any]] = {
    "technical_question_plan": TECHNICAL_QUESTION_PLAN_SCHEMA,
    "technical_evidence_graph": TECHNICAL_EVIDENCE_GRAPH_SCHEMA,
    "technical_resolution_round_1": TECHNICAL_RESOLUTION_SCHEMA,
    "technical_resolution_final": TECHNICAL_RESOLUTION_SCHEMA,
    "factual_critic": FACTUAL_REVIEW_SCHEMA,
}


def _codex_output_schema_for_context(context: object) -> dict[str, Any] | None:
    """Select only server-owned schemas for explicitly allowlisted internal stages."""

    if not isinstance(context, dict):
        return None
    stage = str(context.get("context_collection_stage") or "").strip().lower()
    return _CODEX_STRUCTURED_STAGE_SCHEMAS.get(stage)


@dataclass
class _BufferedResearchResponse:
    status_code: int
    content: bytes
    encoding: str = "utf-8"
    closed: bool = False

    def iter_content(self, *, chunk_size: int, decode_unicode: bool = False):
        for start in range(0, len(self.content), max(1, int(chunk_size))):
            chunk = self.content[start : start + chunk_size]
            yield chunk.decode(self.encoding, errors="replace") if decode_unicode else chunk

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def close(self) -> None:
        self.closed = True


async def _fetch_buffered_research_response(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: tuple[float, float],
    verify: object,
    allow_redirects: bool,
    maximum_bytes: int,
    transport: httpx.AsyncBaseTransport | None,
) -> _BufferedResearchResponse:
    request_timeout = httpx.Timeout(
        connect=float(timeout[0]),
        read=float(timeout[1]),
        write=float(timeout[0]),
        pool=float(timeout[0]),
    )
    async with httpx.AsyncClient(
        verify=verify,
        timeout=request_timeout,
        follow_redirects=allow_redirects,
        transport=transport,
    ) as client:
        async with client.stream("GET", url, headers=dict(headers)) as response:
            if int(response.status_code) >= 300:
                return _BufferedResearchResponse(
                    status_code=int(response.status_code),
                    content=b"",
                    encoding=str(response.encoding or "utf-8"),
                )
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                remaining = maximum_bytes - len(payload)
                if remaining <= 0:
                    break
                payload.extend(chunk[:remaining])
                if len(chunk) >= remaining:
                    break
            return _BufferedResearchResponse(
                status_code=int(response.status_code),
                content=bytes(payload),
                encoding=str(response.encoding or "utf-8"),
            )


def fetch_research_response(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: tuple[float, float],
    verify: object,
    allow_redirects: bool,
    stream: bool,
    deadline_monotonic: float | None = None,
    maximum_bytes: int = PUBLIC_RESEARCH_MAX_DECOMPRESSED_BYTES,
    transport: httpx.AsyncBaseTransport | None = None,
) -> object:
    """Fetch a reader response with a hard deadline over headers and body."""

    if deadline_monotonic is None:
        return requests.get(
            url,
            headers=dict(headers),
            timeout=timeout,
            verify=verify,
            allow_redirects=allow_redirects,
            stream=stream,
        )
    loop_timeout = float(deadline_monotonic) - time.monotonic()
    if loop_timeout <= 0:
        raise requests.exceptions.ReadTimeout("research deadline exceeded")

    async def run_with_deadline() -> _BufferedResearchResponse:
        return await asyncio.wait_for(
            _fetch_buffered_research_response(
                url,
                headers=headers,
                timeout=timeout,
                verify=verify,
                allow_redirects=allow_redirects,
                maximum_bytes=max(1, int(maximum_bytes)),
                transport=transport,
            ),
            timeout=loop_timeout,
        )

    try:
        return asyncio.run(run_with_deadline())
    except TimeoutError as exc:
        raise requests.exceptions.ReadTimeout("research deadline exceeded") from exc
    except httpx.ConnectTimeout as exc:
        raise requests.exceptions.ConnectTimeout(str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise requests.exceptions.ReadTimeout(str(exc)) from exc
    except httpx.TransportError as exc:
        raise requests.exceptions.ConnectionError(str(exc)) from exc


def _model_adapter(name: str, default):
    from .runtime import resolve_runtime_adapter

    return resolve_runtime_adapter("models", name, default)


def invoke_model(client_id: str, payload: IAChatRequest, model_req: str) -> tuple[str, str]:
    if _modelo_eh_codex(model_req):
        context = payload.context if isinstance(payload.context, dict) else {}
        output_schema = _codex_output_schema_for_context(context)
        on_thread_ready = context.pop("_codex_on_thread_ready", None)
        thread_id = str(context.get("_codex_thread_id") or "").strip()
        if context.get("_codex_persist_thread") or thread_id:
            thread_kwargs: dict[str, Any] = {
                "thread_id": thread_id,
                "persist_thread": True,
                "conversation_key": str(context.get("_codex_conversation_key") or context.get("_codex_job_id") or ""),
                "active_turn_key": str(context.get("_codex_active_turn_key") or context.get("_codex_job_id") or ""),
                "on_thread_ready": on_thread_ready if callable(on_thread_ready) else None,
            }
            if output_schema is not None:
                thread_kwargs["output_schema"] = output_schema
            response, resulting_thread_id = _model_adapter(
                "call_codex_thread", _chamar_codex_chat_com_thread
            )(payload, client_id, **thread_kwargs)
            context["_codex_thread_id_result"] = resulting_thread_id
            payload.context = context
        else:
            codex_kwargs: dict[str, Any] = {}
            if output_schema is not None:
                codex_kwargs["output_schema"] = output_schema
            response = _model_adapter("call_codex", _chamar_codex_chat)(
                payload, client_id, **codex_kwargs
            )
        return response, f"codex:{_codex_modelo_nome_curto(model_req)}"
    if _modelo_eh_vertex_ai(model_req):
        response = _model_adapter("call_vertex", _chamar_vertex_ai_chat)(payload, client_id)
        return response, f"vertex:{_vertex_modelo_nome_curto(model_req) or _vertex_ai_modelo_padrao()}"
    if _modelo_eh_gemini_api(model_req):
        response = _model_adapter("call_gemini", _chamar_gemini_chat)(payload, client_id)
        return response, f"gemini:{_gemini_nome_curto(model_req) or 'gemini-2.5-flash'}"
    if model_req.startswith("deepseek-"):
        return _model_adapter("call_deepseek", _chamar_deepseek_chat)(payload, client_id), model_req
    response = _model_adapter("call_openai", _chamar_openai_responses)(payload, client_id)
    return response, model_req or (os.getenv("OPENAI_MODEL") or "gpt-5.4-nano").strip()


__all__ = [
    "FACTUAL_REVIEW_SCHEMA",
    "FACTUAL_REVIEW_SCHEMA_VERSION",
    "TECHNICAL_QUESTION_PLAN_SCHEMA",
    "TECHNICAL_QUESTION_PLAN_SCHEMA_VERSION",
    "TECHNICAL_RESOLUTION_SCHEMA",
    "TECHNICAL_RESOLUTION_SCHEMA_VERSION",
    "fetch_research_response",
    "invoke_model",
]
