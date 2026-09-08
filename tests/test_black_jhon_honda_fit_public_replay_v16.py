from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.modules.perguntas_pos_venda.ai import client_workflows, clients
from backend.modules.perguntas_pos_venda.ai import technical_resolution
from backend.modules.perguntas_pos_venda.ai.technical_resolution import (
    TECHNICAL_EVIDENCE_GRAPH_SCHEMA,
    TECHNICAL_QUESTION_PLAN_SCHEMA,
    TECHNICAL_RESOLUTION_SCHEMA,
)
from backend.schemas.ia import IAChatAttachment
from backend.services import perguntas_pos_venda_codex as orchestrator
from backend.services import perguntas_pos_venda_state as question_state


ROUND_ONE_BODY = "  Ainda falta comprovar o ponto exato de instalação.\r\n  "
FINAL_TECHNICAL_BODY = (
    "  O catálogo confirma a instalação na carcaça da válvula termostática.\r\n  "
)
PUBLIC_BODY = (
    "Sim, este interruptor é instalado na carcaça da válvula termostática do "
    "Honda Fit 1.4 de 2003 a 2005. O código 37760-P00-003 substitui a referência "
    "37760-PHM-004 e mantém a aplicação correta do conjunto. Pode realizar a compra."
)


def _tool_result(function: str, context: str = "") -> dict:
    return {
        "function": function,
        "arguments": {},
        "result": {
            "found": True,
            "read_only": True,
            "context": context or "Dados atuais do produto.",
            "matches": [],
        },
    }


def _question_plan() -> dict:
    return {
        "schema": TECHNICAL_QUESTION_PLAN_SCHEMA,
        "target": {
            "kind": "vehicle",
            "name": "Honda Fit 1.4 2003 2004 2005",
            "identifiers": ["37760-P00-003", "37760-PHM-004"],
        },
        "requirements": [{
            "id": "q1",
            "essential": True,
            "kind": "installation_location",
            "question": "O interruptor é instalado na carcaça da válvula termostática?",
            "relation": "installed_in",
            "required_fields": ["installation.location", "reference.supersession"],
        }],
        "queries": [{
            "type": "installation_location_by_code",
            "query": (
                "37760-P00-003 Honda Fit 1.4 2003 2004 2005 instalado na "
                "carcaça da válvula termostática diagrama catálogo OEM"
            ),
            "requirement_ids": ["q1"],
        }],
    }


def _evidence_graph(*, resolved: bool) -> dict:
    source_ref = "https://techinfo.honda.example/fit/thermostat-housing.pdf#page=12"
    claims = []
    relations = []
    passages = [{
        "id": "passage-12",
        "source_ref": source_ref,
        "section_ref": "p. 12 | Cooling System | callout 14",
        "text": (
            "37760-P00-003 SWITCH ASSY., THERMO installed in thermostat housing; "
            "37760-PHM-004 superseded by 37760-P00-003."
            if resolved
            else "Catálogo inicial localizado; a chamada de montagem ainda não foi lida."
        ),
        "matched_identifiers": ["37760-P00-003", "37760-PHM-004"],
        "requirement_ids": ["q1"],
    }]
    if resolved:
        claims = [{
            "id": "claim-location",
            "entity_id": "part-current",
            "field_name": "installation.location",
            "value": "carcaça da válvula termostática",
            "source_refs": ["passage-12"],
            "support": "supports",
            "requirement_ids": ["q1"],
        }]
        relations = [
            {
                "id": "relation-installation",
                "from_entity_id": "part-current",
                "relation": "installed_in",
                "to_entity_id": "thermostat-housing",
                "claim_ids": ["claim-location"],
                "source_refs": ["passage-12"],
                "requirement_ids": ["q1"],
            },
            {
                "id": "relation-supersession",
                "from_entity_id": "part-old",
                "relation": "superseded_by",
                "to_entity_id": "part-current",
                "claim_ids": ["claim-location"],
                "source_refs": ["passage-12"],
                "requirement_ids": ["q1"],
            },
        ]
    return {
        "schema": TECHNICAL_EVIDENCE_GRAPH_SCHEMA,
        "entities": [
            {
                "id": "part-current",
                "kind": "part",
                "name": "Interruptor térmico 37760-P00-003",
                "identifiers": ["37760-P00-003"],
            },
            {
                "id": "part-old",
                "kind": "part",
                "name": "Referência anterior 37760-PHM-004",
                "identifiers": ["37760-PHM-004"],
            },
            {
                "id": "thermostat-housing",
                "kind": "location",
                "name": "Carcaça da válvula termostática",
                "identifiers": [],
            },
        ],
        "claims": claims,
        "passages": passages,
        "relations": relations,
        "unresolved_requirement_ids": [] if resolved else ["q1"],
    }


def _resolution(*, final: bool) -> dict:
    decision = "yes" if final else "insufficient"
    body = FINAL_TECHNICAL_BODY if final else ROUND_ONE_BODY
    source_url = "https://techinfo.honda.example/fit/thermostat-housing.pdf"
    facts = []
    relations = []
    missing_fields = ["installation.location"]
    gap_queries = [{
        "type": "oem_exploded_diagram",
        "query": (
            "37760-PHM-004 superseded by 37760-P00-003 Honda Fit 1.4 "
            "thermostat housing exploded diagram"
        ),
        "requirement_ids": ["q1"],
    }]
    if final:
        facts = [{
            "field_name": "installation.location",
            "relation": "installed_in",
            "value": "carcaça da válvula termostática",
            "source_refs": [source_url],
            "support": "supports",
        }]
        relations = [
            {
                "from": "37760-P00-003",
                "relation": "installed_in",
                "to": "carcaça da válvula termostática",
                "source_refs": [source_url],
            },
            {
                "from": "37760-PHM-004",
                "relation": "superseded_by",
                "to": "37760-P00-003",
                "source_refs": [source_url],
            },
        ]
        missing_fields = []
        gap_queries = []
    return {
        "schema": TECHNICAL_RESOLUTION_SCHEMA,
        "round": 2 if final else 1,
        "final": final,
        "requirements": [{
            "id": "q1",
            "decision": decision,
            "conclusion": (
                "Instalação confirmada no diagrama OEM."
                if final
                else "Local de instalação ainda não comprovado."
            ),
            "facts": facts,
            "missing_fields": missing_fields,
            "confidence": 0.98 if final else 0.42,
        }],
        "reference_relations": relations,
        "overall_decision": decision,
        "commercial_state": "fits" if final else "insufficient",
        "confidence": 0.98 if final else 0.42,
        "reason": "oem_diagram_confirmed" if final else "gap_requires_oem_diagram",
        "gap_queries": gap_queries,
        "contingency_answer_body": body,
        "compatibility_analysis": {
            "target_type": "vehicle",
            "target_item": "Honda Fit 1.4 2003 a 2005",
            "target_vehicle": "Honda Fit 1.4 2003 a 2005",
            "compatibility_profile": "local de instalação e referência OEM",
            "product_interface": "interruptor térmico 37760-P00-003",
            "target_interface": "carcaça da válvula termostática",
            "comparison_attributes": [],
            "decision": decision,
            "condition": "",
            "missing_fields": missing_fields,
            "evidence": {
                "product": [{
                    "authority": "official_document",
                    "url": source_url,
                    "reference": "37760-P00-003",
                }] if final else [],
                "target": [],
                "target_vehicle": [],
                "equivalence": [{
                    "authority": "official_document",
                    "url": source_url,
                    "reference": "37760-PHM-004 superseded by 37760-P00-003",
                }] if final else [],
            },
            "queries": [],
            "sources": [source_url] if final else [],
            "confidence": 0.98 if final else 0.42,
            "reason": "oem_diagram_confirmed" if final else "gap_requires_oem_diagram",
        },
    }


def test_honda_fit_v16_public_job_replays_all_six_stages_without_publishing(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_calls: list[dict] = []
    web_calls: list[dict] = []
    vision_phases: list[str] = []
    normalized_bodies: dict[int, str] = {}
    generated_candidates: list[str] = []
    evidence_graph_transports: list[str] = []
    graph_calls = 0

    def fake_model(_client_id, payload, model):
        nonlocal graph_calls
        stage = str((payload.context or {}).get("context_collection_stage") or "")
        stage_calls.append({
            "stage": stage,
            "model": model,
            "reasoning": (payload.context or {}).get("_codex_reasoning_effort"),
            "isolated": not bool((payload.context or {}).get("_codex_thread_id")),
            "attachments": len(payload.attachments or []),
        })
        assert model == "codex:gpt-5.6-sol"
        assert (payload.context or {}).get("_codex_reasoning_effort") == "high"
        if stage == "technical_question_plan":
            value = _question_plan()
        elif stage == "technical_evidence_graph":
            graph_calls += 1
            evidence_graph_transports.append(json.dumps(
                payload.tool_results or [], ensure_ascii=False, sort_keys=True,
            ))
            assert len(payload.attachments or []) == 1
            value = _evidence_graph(resolved=graph_calls == 2)
        elif stage == "technical_resolution_round_1":
            value = _resolution(final=False)
        elif stage == "technical_resolution_final":
            value = _resolution(final=True)
        elif stage == "compatibility_public_answer":
            generated_candidates.append(PUBLIC_BODY)
            value = {
                "answer": PUBLIC_BODY,
                "confidence": 0.98,
                "category": "compatibility",
                "requires_human_review": False,
                "reason": "commercial_answer_after_technical_resolution",
            }
        elif stage == "factual_critic":
            value = {
                "schema": "jk_ml_factual_review_v1",
                "verdict": "pass",
                "issues": [],
                "revision_instructions": [],
                "confidence": 0.99,
            }
        else:  # pragma: no cover - exposes an unexpected production stage
            raise AssertionError(f"Unexpected model stage: {stage}")
        return json.dumps(value, ensure_ascii=False), model

    def fake_web(_client_id, projected, _previous):
        snapshot = dict(projected)
        web_calls.append(snapshot)
        if len(web_calls) == 1:
            return {
                "function": "web_search_question_context",
                "arguments": {"queries": _question_plan()["queries"]},
                "result": {
                    "found": True,
                    "read_only": True,
                    "context": (
                        "Catálogo localizado, mas a chamada de montagem ainda está aberta. "
                        "Uma página genérica de marketplace cita 37760-PWA-J01 para outro "
                        "componente; páginas de suporte e contato não trazem tabela técnica."
                    ),
                    "sources": [
                        "https://techinfo.honda.example/fit/catalog.pdf",
                        "https://produto.mercadolivre.com.br/MLB-generic-pwa-j01",
                        "https://techinfo.honda.example/support/faq",
                        "https://techinfo.honda.example/contact/sales",
                    ],
                    "research_metrics": {"coverage_complete": False, "stop_reason": "coverage_incomplete"},
                },
            }
        assert snapshot["research_attempt"] >= 2
        assert snapshot["research_gap_only"] is True
        assert snapshot["technical_gap_queries"][0]["type"] == "oem_exploded_diagram"
        return {
            "function": "web_search_question_context",
            "arguments": {"queries": snapshot["technical_gap_queries"]},
            "result": {
                "found": True,
                "read_only": True,
                "context": (
                    "Diagrama OEM: 37760-P00-003 instalado na carcaça da válvula "
                    "termostática; 37760-PHM-004 substituído por 37760-P00-003. "
                    "Na seção de outro componente, 37760-PWA-J01 aparece em outra chamada."
                ),
                "sources": ["https://techinfo.honda.example/fit/thermostat-housing.pdf"],
                "research_metrics": {"coverage_complete": True, "stop_reason": "coverage_complete"},
            },
        }

    def fake_vision(client, _metadata, _research, *, phase):
        vision_phases.append(phase)
        client._document_vision_attachments = [IAChatAttachment(
            name=f"honda-fit-{phase}.png",
            mime_type="image/png",
            data_base64=base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode("ascii"),
        )]
        refs = [{
            "source_url": "https://techinfo.honda.example/fit/thermostat-housing.pdf",
            "page": 12,
            "section_ref": "Cooling System | callout 14",
            "phase": phase,
        }]
        client._document_vision_page_refs = refs
        return refs

    real_normalize = technical_resolution.normalize_technical_resolution

    def record_normalized(value, *, plan, round_number, final):
        result = real_normalize(
            value,
            plan=plan,
            round_number=round_number,
            final=final,
        )
        normalized_bodies[round_number] = result.contingency_answer_body
        return result

    monkeypatch.setattr(clients, "_ia_agent_perguntas_chamar_modelo", fake_model)
    monkeypatch.setattr(clients, "_ia_raciocinio_perguntas_configurado", lambda: "high")
    monkeypatch.setattr(clients, "_ia_raciocinio_pos_venda_configurado", lambda: "high")
    monkeypatch.setattr(
        clients,
        "marketplace_listing_query",
        lambda *_args, **_kwargs: _tool_result("get_mercado_livre_listing"),
    )
    monkeypatch.setattr(
        clients,
        "_ia_tool_get_product_data",
        lambda *_args, **_kwargs: _tool_result("get_product_data"),
    )
    monkeypatch.setattr(
        clients,
        "_ia_tool_get_bling_product",
        lambda *_args, **_kwargs: _tool_result("get_bling_product"),
    )
    monkeypatch.setattr(
        clients,
        "_perguntas_ia_context_hub_tool",
        lambda *_args, **_kwargs: _tool_result("context_hub_search"),
    )
    monkeypatch.setattr(
        clients,
        "_ia_agent_perguntas_product_identity_web_tool",
        lambda *_args, **_kwargs: _tool_result(
            "web_search_product_identity",
            "Identidade do produto: interruptor térmico 37760-P00-003.",
        ),
    )
    monkeypatch.setattr(clients, "_ia_agent_perguntas_web_tool", fake_web)
    monkeypatch.setattr(clients, "_perguntas_ia_legacy_sku_memory_reader_enabled", lambda: False)
    monkeypatch.setattr(clients, "_perguntas_ia_legacy_guidance_fallback", lambda *_args: "")
    monkeypatch.setattr(client_workflows, "_prepare_document_vision", fake_vision)
    monkeypatch.setattr(technical_resolution, "normalize_technical_resolution", record_normalized)

    agent_input = {
        "_codex_job_id": "job-honda-fit-public-v16",
        "tenant_id": "tenant-honda-fit",
        "store": "Uai Mineirinho",
        "task": "mercado_livre_public_question_draft",
        "question": {
            "id": "Q-HONDA-FIT-V16",
            "item_id": "MLB-HONDA-FIT-V16",
            "text": "Esse cebolão é instalado na carcaça da válvula termostática?",
            "history": [],
        },
        "item": {
            "id": "MLB-HONDA-FIT-V16",
            "seller_sku": "SKU-FIT-CEBOLAO",
            "title": "Interruptor Ventoinha Honda Fit 1.4 2003 2004 2005",
            "description": "Código anunciado 37760-P00-003.",
        },
        "context": {
            "titulo": "Interruptor Ventoinha Honda Fit 1.4 2003 2004 2005",
            "descricao": "Código anunciado 37760-P00-003.",
        },
        "intent": {
            "intencao": "compatibilidade",
            "fluxo": "perguntas_anuncio",
            "categoria": "compatibility",
            "categorias": ["compatibility"],
            "confianca": 0.99,
            "continuidade": {
                "tipo": "independente",
                "herdou_historico": False,
            },
            "flags": {
                "usar_busca_web": True,
                "usar_mercado_livre_anuncio": True,
                "usar_bling": True,
            },
            "subperguntas": [{
                "intent": "compatibility",
                "question": "O produto é instalado na carcaça da válvula termostática?",
                "required_evidence": "diagrama OEM com código, local e aplicação",
            }],
            "compatibilidade": {
                "aplicavel": True,
                "target_type": "vehicle",
                "target_item": "Honda Fit 1.4 2003 a 2005",
                "compatibility_profile": "vehicle_fitment",
                "technical_focus": "instalação na carcaça da válvula termostática",
                "missing_fields": [],
                "decisive_fields": ["installation.location", "reference.supersession"],
            },
        },
        "subquestions": [{
            "intent": "compatibility",
            "question": "O produto é instalado na carcaça da válvula termostática?",
            "required_evidence": "diagrama OEM com código, local e aplicação",
        }],
        "commercial_state_policy": {"fits": {"cta": "direct_purchase"}},
    }

    captured: dict = {}

    def load_public_context(_job):
        client = clients._PerguntasCodexV3Client(
            "tenant-honda-fit",
            "Uai Mineirinho",
            "codex:gpt-5.6-sol",
            agent_input,
            reasoning_effort="high",
        )
        try:
            candidate = client.generate("synthetic public Honda Fit replay", {"category": "compatibility"})
        except Exception as exc:
            captured["loader_error"] = f"{type(exc).__name__}: {exc}"
            raise
        captured["candidate"] = candidate.answer
        captured["pipeline"] = list(client.context_pipeline)
        captured["analysis"] = dict(client.compatibility_analysis)
        signed = question_state._perguntas_ia_resposta_final_loja(
            candidate.answer,
            "Uai Mineirinho",
        )
        return signed, {
            "loja": "Uai Mineirinho",
            "model": client.model_usado,
            "intencao_atendimento": {
                "subperguntas": agent_input["subquestions"],
            },
            "diagnostico_ia": [{
                "result": {
                    "validation_ok": True,
                    "validation_issues": [],
                    "confidence": 0.98,
                    "effective_model": client.model_usado,
                    "compatibility_analysis": client.compatibility_analysis,
                },
            }],
            "evidence_envelope": {
                "schema_version": "evidence-envelope-v2",
                "status": "completed",
                "records": [{
                    "field": "compatibility",
                    "value": "installed_in + superseded_by",
                    "store": "Uai Mineirinho",
                    "source": "https://techinfo.honda.example/fit/thermostat-housing.pdf",
                    "coverage": "confirmed",
                    "authority": "official",
                }],
                "sources": ["https://techinfo.honda.example/fit/thermostat-housing.pdf"],
                "gaps": [],
                "confidence": "high",
                "evidence_sufficient": True,
                "coverage_complete": True,
            },
        }

    generated_public_context = load_public_context({})

    monkeypatch.setattr(orchestrator, "_RUNTIME", SimpleNamespace(PASTA_INFO=str(tmp_path)))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    with patch.object(orchestrator, "_schedule", return_value=True), patch.object(
        orchestrator.codex_agent_runtime,
        "resolve_guidance",
        return_value=[],
    ):
        created = orchestrator.create_job(
            client_id="tenant-honda-fit",
            task_type="question",
            store="Uai Mineirinho",
            subject_key="Q-HONDA-FIT-V16",
            request={
                "pergunta": agent_input["question"],
                "item": agent_input["item"],
                "question_text": agent_input["question"]["text"],
            },
        )
    monkeypatch.setattr(
        orchestrator,
        "_load_question_context",
        lambda _job: generated_public_context,
    )
    orchestrator._run_job("tenant-honda-fit", created["job_id"])
    completed = orchestrator.get_job("tenant-honda-fit", created["job_id"])

    signature = question_state._perguntas_ia_assinatura_loja("Uai Mineirinho")
    expected_public_reply = f"{PUBLIC_BODY}\n\n{signature}"
    assert not completed.get("error"), completed
    assert "loader_error" not in captured, captured.get("loader_error")
    assert normalized_bodies == {
        1: ROUND_ONE_BODY,
        2: FINAL_TECHNICAL_BODY,
    }
    assert generated_candidates == [PUBLIC_BODY]
    assert captured["candidate"] == PUBLIC_BODY
    assert completed["result"]["resposta"] == expected_public_reply
    assert completed["result"]["resposta"].count(signature) == 1
    assert question_state._perguntas_ia_resposta_final_loja(
        completed["result"]["resposta"],
        "Uai Mineirinho",
    ) == expected_public_reply

    assert vision_phases == ["initial", "gap"]
    assert len(web_calls) == 2
    assert all("store_sku_question_context" in value for value in evidence_graph_transports)
    assert any("37760-PWA-J01" in value for value in evidence_graph_transports)
    assert "37760-PWA-J01" not in captured["candidate"]
    assert "37760-P00-003" in captured["candidate"]
    assert [call["stage"] for call in stage_calls] == [
        "technical_question_plan",
        "technical_evidence_graph",
        "technical_resolution_round_1",
        "technical_evidence_graph",
        "technical_resolution_final",
        "compatibility_public_answer",
        "factual_critic",
    ]
    assert [call["attachments"] for call in stage_calls if call["stage"] == "technical_evidence_graph"] == [1, 1]
    assert all(call["model"] == "codex:gpt-5.6-sol" for call in stage_calls)
    assert all(call["reasoning"] == "high" for call in stage_calls)
    assert captured["analysis"]["decision"] == "yes"
    assert completed["status"] == "completed"
    assert completed["agent_state"] == "aguardando_aprovacao"
    assert completed["result"]["requires_approval"] is True
    assert completed["result"]["publish_attempted"] is False
