from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.services import codex_data_selection_agent, codex_whatsapp_agents, context_hub
from backend.services.context_hub_endpoints import ContextHubSearchRequest
from backend.services.whatsapp import black_jhon_prompting


def _legacy_plan(*, resource_id: str = "ml.orders.get") -> dict:
    return {
        "schema_version": codex_data_selection_agent.LEGACY_SCHEMA_VERSION,
        "action": "collect",
        "intents": ["mercado_livre_order"],
        "entities": {
            "sku": "",
            "mlb": "",
            "order_id": "200000000001",
            "period": "",
            "store_ref": "JK Pecas",
            "store_mode": "single",
        },
        "requested_fields": ["order_status"],
        "tool_calls": [
            {
                "tool_id": "mercado_livre_resource_query",
                "arguments": json.dumps(
                    {
                        "resource_id": resource_id,
                        "path_params": {"order_id": "200000000001"},
                        "loja": "JK Pecas",
                    }
                ),
                "required": True,
                "reason": "consultar o pedido",
                "depends_on": [],
            }
        ],
        "context_hub": {
            "mode": "not_applicable",
            "query": "",
            "filters": {},
            "top_k": 0,
            "snippet_max_chars": 0,
        },
        "missing_user_fields": [],
        "confidence": 0.9,
        "reason": "consulta atual em API",
    }


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_conversation_decision_v3_extends_v2_and_keeps_server_owned_scope() -> None:
    schema = black_jhon_prompting.conversation_decision_v3_schema(codex_whatsapp_agents.DECISION_SCHEMA)
    diagnostics = black_jhon_prompting.prompt_contract_v3_diagnostics()
    normalized = black_jhon_prompting.normalize_conversation_decision_v3(
        {
            "schema_version": black_jhon_prompting.CONVERSATION_DECISION_V2,
            "action": "delegate",
            "intent": "sku_stock",
            "resolved_context": {
                "store": "JK Pecas",
                "store_mode": "single",
                "sku": "001",
                "mlb": "",
                "period": "hoje",
            },
        }
    )

    assert schema["additionalProperties"] is False
    assert diagnostics["version"] == "black-jhon-whatsapp-prompts.v3"
    assert diagnostics["schemas"]["conversation_decision"] == black_jhon_prompting.CONVERSATION_DECISION_V3
    assert len(diagnostics["hash"]) == 64
    assert schema["properties"]["schema_version"]["enum"] == [
        black_jhon_prompting.CONVERSATION_DECISION_V3
    ]
    assert {"intent_id", "entities", "scope", "risk", "ambiguities"} <= set(schema["required"])
    assert normalized["schema_version"] == black_jhon_prompting.CONVERSATION_DECISION_V3
    assert normalized["source_schema_version"] == black_jhon_prompting.CONVERSATION_DECISION_V2
    assert normalized["intent_id"] == "commerce.stock"
    assert normalized["scope"] == {
        "tenant_source": "server",
        "store_mode": "single",
        "store_refs": ["JK Pecas"],
        "period": "hoje",
        "subject": "",
        "module": "",
    }
    assert {(item["type"], item["value"]) for item in normalized["entities"]} >= {
        ("store", "JK Pecas"),
        ("sku", "001"),
        ("period", "hoje"),
    }


def test_conversation_decision_v3_marks_mutation_as_review_without_authorizing() -> None:
    normalized = black_jhon_prompting.normalize_conversation_decision_v3(
        {
            "schema_version": black_jhon_prompting.CONVERSATION_DECISION_V3,
            "action": "delegate",
            "intent_id": "system.mutation_request",
            "risk": {
                "operation_class": "mutation_request",
                "level": "high",
                "requires_human_review": False,
                "reason_codes": ["user_requested_write"],
            },
        }
    )

    assert normalized["risk"]["operation_class"] == "mutation_request"
    assert normalized["risk"]["requires_human_review"] is True
    assert normalized["scope"]["tenant_source"] == "server"


def test_ml_resource_resolver_is_bounded_versioned_and_prefers_visit_resources() -> None:
    resolved = codex_data_selection_agent.resolve_mercado_livre_resource_candidates(
        "Quantas visitas teve o MLB1234567890 no periodo?"
    )

    assert resolved["catalog_version"] != "unavailable"
    assert resolved["selection"] == "deterministic_top_n"
    assert 1 <= len(resolved["candidates"]) <= codex_data_selection_agent.MAX_ML_RESOURCE_CANDIDATES
    assert resolved["candidates"][0]["resource_id"].startswith("ml.visits.")
    assert all(item["policy"] not in {"document_only", "tombstone"} for item in resolved["candidates"])


def test_planner_prompt_receives_exact_ml_candidates_before_model_selection() -> None:
    prompt = codex_data_selection_agent.build_planner_prompt(
        request_text="Qual o status do envio 123?",
        job_prompt="Consultar somente o envio informado.",
        surface="whatsapp",
        allowed_tools=[
            {
                "id": "mercado_livre_resource_query",
                "read_only": True,
                "input_schema": {"properties": {"resource_id": {}, "path_params": {}, "loja": {}}},
            }
        ],
        authorized_stores=["JK Pecas"],
        conversation_anchors={"resolved_context": {"shipment_id": "123"}},
        previous_evidence={},
        data_gap={},
    )
    payload = json.loads(prompt.split("\n\n", 1)[1])
    tool = payload["tool_catalog"][0]

    assert payload["contract"] == codex_data_selection_agent.SCHEMA_VERSION
    assert tool["resource_catalog_version"] == payload["resource_catalog_version"]
    assert tool["resource_candidates"][0]["resource_id"] == "ml.shipments.get"
    assert "path_template" not in tool["resource_candidates"][0]


def test_data_selection_plan_v2_normalizes_v1_and_rejects_unknown_ml_resource() -> None:
    allowed_tools = [{"id": "mercado_livre_resource_query", "read_only": True}]
    plan, stats = codex_data_selection_agent.normalize_data_selection_plan(
        _legacy_plan(),
        allowed_tools=allowed_tools,
        authorized_stores=["JK Pecas"],
    )

    assert plan["schema_version"] == codex_data_selection_agent.SCHEMA_VERSION
    assert plan["source_schema_version"] == codex_data_selection_agent.LEGACY_SCHEMA_VERSION
    assert plan["entities"]["claim_id"] == ""
    assert plan["intent_ids"] == ["mercado_livre_order"]
    assert plan["coverage"]["expected_stores"] == ["JK Pecas"]
    assert plan["tool_calls"][0]["failure_policy"] == "fail_plan"
    assert plan["resource_catalog_version"] != "unavailable"
    assert stats["accepted"] == ["mercado_livre_resource_query"]

    with pytest.raises(
        codex_data_selection_agent.DataSelectionPlanError,
        match="data_selection_invalid_ml_resource_id",
    ):
        codex_data_selection_agent.normalize_data_selection_plan(
            _legacy_plan(resource_id="ml.invented.free_url"),
            allowed_tools=allowed_tools,
            authorized_stores=["JK Pecas"],
        )


def test_context_retrieval_v3_adds_citations_authority_validity_and_conflicts() -> None:
    result = context_hub._finalize_context_retrieval_v3(
        {
            "success": True,
            "query": "compatibilidade ABC123",
            "generation_id": "gen-active",
            "generation": "gen-active",
            "source_version": "source-v1",
            "results": [
                {
                    "doc_id": "doc-a",
                    "chunk_id": "chunk-a",
                    "snippet": "O SKU ABC123 serve neste veiculo.",
                    "score": 10.0,
                    "reference": "manual-a.md",
                    "truth_class": "canonical",
                    "source_version": "source-v1",
                    "source_hash": "hash-a",
                    "generation_id": "gen-active",
                },
                {
                    "doc_id": "doc-b",
                    "chunk_id": "chunk-b",
                    "snippet": "O SKU ABC123 nao serve neste veiculo.",
                    "score": 5.0,
                    "reference": "nota-b.md",
                    "truth_class": "human_curated",
                    "source_version": "source-v1",
                    "source_hash": "hash-b",
                    "generation_id": "gen-active",
                },
            ],
            "embeddings_enabled": False,
        },
        filters={"valid_at": "2026-07-20", "authority": "authoritative"},
    )

    assert result["schema_version"] == context_hub.CONTEXT_RETRIEVAL_V3
    assert result["embeddings_enabled"] is False
    assert result["operational_data_source"] is False
    assert result["conflict_detected"] is True
    assert result["coverage_complete"] is False
    assert result["results"][0]["authority"] == "authoritative"
    assert result["results"][0]["validity"] == "active_generation"
    assert result["results"][0]["normalized_score"] == 1.0
    assert result["results"][1]["normalized_score"] == 0.5
    assert result["results"][0]["citation_id"].startswith("ctx-")
    assert result["results"][0]["conflict_with"] == [result["results"][1]["citation_id"]]


def test_context_retrieval_filters_are_closed_in_service_and_http_contract() -> None:
    with pytest.raises(context_hub.ContextHubValidationError, match="nao permitido"):
        context_hub._closed_context_filters({"unknown_filter": "x"})
    with pytest.raises(ValidationError):
        ContextHubSearchRequest(query="x", unknown_filter="x")

    request = ContextHubSearchRequest(
        query="manual",
        sku="ABC123",
        mlb="MLB123456789",
        store_ref="Loja Alfa",
        surface="installed",
        tags=["compatibilidade"],
        authority="verified_technical",
        validity="active_generation",
        valid_at="2026-07-20",
        document_types=["contract"],
    )
    assert request.authority == "verified_technical"
    assert request.validity == "active_generation"
    assert request.sku == "ABC123"
    assert request.mlb == "MLB123456789"
    assert request.store_ref == "Loja Alfa"
    assert request.surface == "installed"
    assert request.tags == ["compatibilidade"]


def test_search_context_returns_v3_and_executes_authority_validity_filters(tmp_path: Path) -> None:
    base = tmp_path / "app"
    info = tmp_path / "info"
    base.mkdir()
    info.mkdir()
    context_hub.configure_context_hub(base_dir=base, info_root=info, surface="installed")
    context_hub.bootstrap_context_hub("tenant-v3")
    paths = context_hub._tenant_paths("tenant-v3", info_root=info)
    generation_id = "b" * 32
    source_version = "1.0.109"
    content = "Manual tecnico confirmado para o SKU ABC123 e o anuncio MLB123456789."
    with context_hub._connect(paths) as connection:
        connection.execute(
            """
            INSERT INTO context_hub_generations(
                generation_id,status,source_hash,source_version,surface,reason,
                findings_json,stats_json,created_at,published_at
            ) VALUES (?, 'active', ?, ?, 'installed', 'test', '[]', '{}', ?, ?)
            """,
            (generation_id, _sha("generation"), source_version, "2026-07-20T00:00:00+00:00", "2026-07-20T00:00:00+00:00"),
        )
        connection.execute(
            "UPDATE context_hub_active_generation SET generation_id=?, version=1 WHERE singleton_id=1",
            (generation_id,),
        )
        connection.execute(
            """
            INSERT INTO context_hub_documents(
                generation_id,doc_id,entity_id,relative_path,title,kind,module,surface,
                truth_class,sensitivity,source_version,source_hash,content_hash,
                source_refs_json,content,managed
            ) VALUES (?, 'doc-v3', 'doc-v3', 'manual.md', 'Manual V3', 'technical_knowledge',
                      'black_jhon', 'installed', 'versioned_technical', 'internal', ?, ?, ?,
                      '["manual.md"]', ?, 1)
            """,
            (generation_id, source_version, _sha("source"), _sha(content), content),
        )
        chunk_id = _sha(content)[:32]
        connection.execute(
            "INSERT INTO context_hub_chunks(generation_id,chunk_id,doc_id,ordinal,content,content_hash) VALUES (?,?,'doc-v3',0,?,?)",
            (generation_id, chunk_id, content, _sha(content)),
        )
        connection.execute(
            "UPDATE context_hub_documents SET store_ref='Loja Alfa', tags_text='manual\ncompatibilidade', valid_from='2026-01-01', valid_to='2026-12-31' WHERE generation_id=? AND doc_id='doc-v3'",
            (generation_id,),
        )
        try:
            connection.execute(
                """
                INSERT INTO context_hub_chunks_fts(
                    generation_id,chunk_id,doc_id,title,content,module,kind,surface,truth_class
                ) VALUES (?,?,'doc-v3','Manual V3',?,'black_jhon','technical_knowledge','installed','versioned_technical')
                """,
                (generation_id, chunk_id, content),
            )
        except sqlite3.OperationalError:
            pytest.skip("SQLite de teste sem FTS5")
        connection.commit()

    result = context_hub.search_context(
        "tenant-v3",
        "manual tecnico ABC123",
        filters={
            "authority": "verified_technical",
            "validity": "active_generation",
            "sensitivity": "internal",
            "document_types": ["technical_knowledge"],
            "store_ref": "Loja Alfa",
            "tags": ["compatibilidade"],
            "sku": "ABC123",
            "mlb": "MLB123456789",
            "surface": "installed",
            "valid_at": "2026-07-20",
        },
        info_root=info,
    )

    assert result["schema_version"] == context_hub.CONTEXT_RETRIEVAL_V3
    assert result["count"] == 1
    assert result["results"][0]["authority"] == "verified_technical"
    assert result["results"][0]["validity"] == "active_generation"
    assert result["results"][0]["citation_id"].startswith("ctx-")
    assert result["operational_data_source"] is False
    assert result["embeddings_enabled"] is False
    assert {
        "sku": "ABC123",
        "mlb": "MLB123456789",
        "store_ref": "Loja Alfa",
        "surface": "installed",
        "valid_at": "2026-07-20",
    }.items() <= result["filters_applied"].items()

    wrong_store = context_hub.search_context(
        "tenant-v3",
        "manual tecnico ABC123",
        filters={"store_ref": "Loja Beta", "valid_at": "2026-07-20"},
        info_root=info,
    )
    assert wrong_store["count"] == 0

    wrong_identifier = context_hub.search_context(
        "tenant-v3",
        "manual tecnico",
        filters={"sku": "OUTRO-SKU", "surface": "installed"},
        info_root=info,
    )
    assert wrong_identifier["count"] == 0

    unverified = context_hub.search_context(
        "tenant-v3",
        "manual tecnico ABC123",
        filters={"authority": "unverified"},
        info_root=info,
    )
    assert unverified["count"] == 0


def test_retrieval_v3_normalizer_preserves_v2_records_and_provenance() -> None:
    normalized = black_jhon_prompting.normalize_retrieval_result_v3(
        {
            "schema_version": context_hub.CONTEXT_RETRIEVAL_V3,
            "query": "manual ABC123",
            "generation_id": "gen-active",
            "source_version": "source-v1",
            "coverage_complete": True,
            "results": [
                {
                    "citation_id": "ctx-abc",
                    "doc_id": "doc-a",
                    "chunk_id": "chunk-a",
                    "reference": "manual.md",
                    "snippet": "Conteudo confirmado.",
                    "generation_id": "gen-active",
                    "source_version": "source-v1",
                    "truth_class": "canonical",
                    "authority": "authoritative",
                    "validity": "active_generation",
                    "normalized_score": 1.0,
                    "conflict": False,
                    "conflict_with": [],
                }
            ],
        }
    )

    assert normalized["schema_version"] == black_jhon_prompting.RETRIEVAL_RESULT_V3
    assert normalized["generation_id"] == "gen-active"
    assert normalized["records"][0]["source"] == "ctx-abc"
    assert normalized["citations"][0]["authority"] == "authoritative"
    assert normalized["operational_data_source"] is False
    assert normalized["embeddings_enabled"] is False
