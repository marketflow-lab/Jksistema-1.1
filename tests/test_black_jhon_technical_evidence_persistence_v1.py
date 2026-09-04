from datetime import datetime, timedelta, timezone

from backend.modules.context_hub.product_evidence_activation import _support_for_claim
from backend.modules.perguntas_pos_venda.ai import technical_evidence_persistence as persistence


def _agent_input() -> dict:
    return {
        "tenant_id": "tenant-a",
        "store": "Loja A",
        "question": {
            "item_id": "MLB1",
            "text": "Este produto atende a aplicação informada?",
            "history": [],
        },
        "item": {
            "id": "MLB1",
            "seller_sku": "SKU-16",
            "variations": [],
        },
        "product_evidence_identity": {
            "store_ref": "Loja A",
            "seller_id": "seller-a",
            "site_id": "MLB",
            "sku": "SKU-16",
            "item_id": "MLB1",
            "variation_id": "",
        },
    }


def test_graph_only_vision_relation_is_persisted_with_provenance(monkeypatch) -> None:
    calls = {"sources": [], "claims": [], "complete": []}
    source_url = "https://catalogo.honda.example/fit/cooling.pdf"
    source_hash = "a" * 64

    monkeypatch.setattr(
        persistence,
        "create_product_evidence_batch",
        lambda client_id, **identity: {"batch_id": "batch-1"},
    )

    def add_source(client_id, batch_id, **payload):
        calls["sources"].append(payload)
        return {"source_id": "source-1"}

    def add_claim(client_id, batch_id, **payload):
        calls["claims"].append(payload)
        return {"claim_id": f"claim-{len(calls['claims'])}"}

    monkeypatch.setattr(persistence, "add_product_evidence_source", add_source)
    monkeypatch.setattr(persistence, "add_product_evidence_claim", add_claim)
    monkeypatch.setattr(
        persistence,
        "complete_product_evidence_batch",
        lambda client_id, batch_id, **payload: calls["complete"].append(payload) or {},
    )

    graph = {
        "entities": [
            {"id": "sensor", "kind": "product", "name": "37760-P00-003"},
            {"id": "housing", "kind": "component", "name": "carcaca da valvula termostatica"},
        ],
        "claims": [{
            "id": "claim-location",
            "entity_id": "sensor",
            "field_name": "installation_location",
            "value": "carcaca da valvula termostatica",
            "source_refs": ["diagram-p12"],
            "support": "supports",
        }],
        "passages": [{
            "id": "diagram-p12",
            "source_ref": source_url,
            "section_ref": "p. 12 | Cooling System | callout 14",
            "text": "Callout 14: 37760-P00-003 installed in thermostat housing.",
        }],
        "relations": [{
            "from_entity_id": "sensor",
            "relation": "installed_in",
            "to_entity_id": "housing",
            "source_refs": ["diagram-p12"],
            "claim_ids": ["claim-location"],
        }],
    }
    context = {
        "research_passages": [{
            "url": source_url,
            "source_type": "official_oem",
            "section_ref": "p. 12 | Cooling System | callout 14",
            "text": "Callout 14 technical table",
        }],
        "document_vision_page_refs": [{
            "source_url": source_url,
            "page": 12,
            "content_hash": source_hash,
        }],
    }

    result = persistence.persist_technical_evidence_graph(
        "tenant-a", _agent_input(), graph, context,
    )

    assert result == {"status": "completed", "facts_written": 2, "sources": 1}
    assert calls["sources"] == [{
        "url": source_url,
        "source_type": "official_oem",
        "content_hash": source_hash,
        "origin_key": "catalogo.honda.example",
        "section_ref": "p. 12 | Cooling System | callout 14",
    }]
    assert {claim["field_name"] for claim in calls["claims"]} == {
        "installation_location",
        "relation.installed_in.37760_p00_003",
    }
    assert all(claim["source_ids"] == ["source-1"] for claim in calls["claims"])
    serialized = repr({"sources": calls["sources"], "claims": calls["claims"]})
    assert "Callout 14" not in serialized
    assert "text" not in serialized
    assert calls["complete"][0]["coverage_complete"] is False


def test_graph_persistence_rejects_cross_tenant_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        persistence,
        "create_product_evidence_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not persist")),
    )
    agent_input = _agent_input()
    agent_input["tenant_id"] = "tenant-b"

    result = persistence.persist_technical_evidence_graph(
        "tenant-a", agent_input, {"claims": [{}]}, {},
    )

    assert result == {"status": "skipped", "reason": "identity_unavailable"}


def test_graph_urls_are_never_authority_without_real_research_provenance(monkeypatch) -> None:
    monkeypatch.setattr(
        persistence,
        "create_product_evidence_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not persist")),
    )
    graph = {
        "entities": [{"id": "part", "kind": "product", "name": "Peca"}],
        "claims": [{
            "id": "claim-1", "entity_id": "part", "field_name": "electrical.voltage",
            "value": "12 V", "support": "supports", "source_refs": ["made-up-1", "made-up-2"],
        }],
        "passages": [
            {"id": "made-up-1", "source_ref": "https://inventada-a.example/ficha", "text": "12 V"},
            {"id": "made-up-2", "source_ref": "https://inventada-b.example/ficha", "text": "12 V"},
        ],
    }

    result = persistence.persist_technical_evidence_graph(
        "tenant-a", _agent_input(), graph, {}, expected_store="Loja A",
    )

    assert result == {"status": "skipped", "reason": "no_sourced_graph_facts"}


def test_refutation_is_advisory_and_context_is_not_persisted_as_a_fact(monkeypatch) -> None:
    calls = {"claims": []}
    monkeypatch.setattr(
        persistence, "create_product_evidence_batch", lambda *_args, **_kwargs: {"batch_id": "batch-1"},
    )
    monkeypatch.setattr(
        persistence, "add_product_evidence_source", lambda *_args, **_kwargs: {"source_id": "source-1"},
    )
    monkeypatch.setattr(
        persistence, "add_product_evidence_claim",
        lambda *_args, **payload: calls["claims"].append(payload) or {"claim_id": "claim"},
    )
    monkeypatch.setattr(persistence, "complete_product_evidence_batch", lambda *_args, **_kwargs: {})
    url = "https://catalogo.example/ficha"
    graph = {
        "entities": [{"id": "part", "kind": "product", "name": "Peca"}],
        "claims": [
            {"id": "c1", "entity_id": "part", "field_name": "electrical.voltage", "value": "24 V", "support": "refutes", "source_refs": [url]},
            {"id": "c2", "entity_id": "part", "field_name": "electrical.voltage", "value": "valor citado sem vínculo", "support": "context", "source_refs": [url]},
        ],
    }
    context = {"research_passages": [{
        "url": url, "source_type": "official_manufacturer", "text": "ficha tecnica",
    }]}

    result = persistence.persist_technical_evidence_graph(
        "tenant-a", _agent_input(), graph, context, expected_store="Loja A",
    )

    assert result["facts_written"] == 1
    assert {value["field_name"] for value in calls["claims"]} == {
        "refutation.electrical.voltage",
    }
    assert not any(value["field_name"] == "electrical.voltage" for value in calls["claims"])


def test_refutation_field_cannot_activate_even_from_an_official_source() -> None:
    now = datetime.now(timezone.utc)
    supported, policy, _valid_until, has_active = _support_for_claim(
        "refutation.electrical.voltage",
        "product",
        [{
            "source_type": "official_manufacturer",
            "collected_at": now.isoformat(),
            "valid_until": (now + timedelta(days=365)).isoformat(),
        }],
        now,
    )

    assert supported is False
    assert policy == "advisory_candidate_v1"
    assert has_active is True


def test_graph_persistence_rejects_personal_data_and_store_mismatch(monkeypatch) -> None:
    calls = {"claims": []}
    monkeypatch.setattr(
        persistence, "create_product_evidence_batch", lambda *_args, **_kwargs: {"batch_id": "batch-1"},
    )
    monkeypatch.setattr(
        persistence, "add_product_evidence_source", lambda *_args, **_kwargs: {"source_id": "source-1"},
    )
    monkeypatch.setattr(
        persistence, "add_product_evidence_claim",
        lambda *_args, **payload: calls["claims"].append(payload) or {"claim_id": "claim"},
    )
    monkeypatch.setattr(persistence, "complete_product_evidence_batch", lambda *_args, **_kwargs: {})
    url = "https://catalogo.example/ficha"
    graph = {
        "entities": [{"id": "part", "kind": "product", "name": "Peca"}],
        "claims": [
            {"id": "c1", "entity_id": "part", "field_name": "installation.location", "value": "Rua das Flores, 123", "support": "supports", "source_refs": [url]},
            {"id": "c2", "entity_id": "part", "field_name": "technical.note", "value": "Alameda Santos, 123", "support": "supports", "source_refs": [url]},
            {"id": "c3", "entity_id": "part", "field_name": "technical.note", "value": "1132654321", "support": "supports", "source_refs": [url]},
            {"id": "c4", "entity_id": "part", "field_name": "electrical.voltage", "value": "12 V", "support": "supports", "source_refs": [url]},
        ],
    }
    context = {"research_passages": [{
        "url": url, "source_type": "official_manufacturer", "text": "ficha tecnica",
    }]}

    result = persistence.persist_technical_evidence_graph(
        "tenant-a", _agent_input(), graph, context, expected_store="Loja A",
    )

    assert result["facts_written"] == 1
    assert [value["value"] for value in calls["claims"]] == ["12 V"]
    mismatched = _agent_input()
    mismatched["store"] = "Loja B"
    assert persistence.persist_technical_evidence_graph(
        "tenant-a", mismatched, graph, context, expected_store="Loja A",
    ) == {"status": "skipped", "reason": "identity_unavailable"}

    missing_tenant = _agent_input()
    missing_tenant["tenant_id"] = ""
    assert persistence.persist_technical_evidence_graph(
        "tenant-a", missing_tenant, graph, context, expected_store="Loja A",
    ) == {"status": "skipped", "reason": "identity_unavailable"}

    mismatched_identity = _agent_input()
    mismatched_identity["product_evidence_identity"]["store_ref"] = "Loja B"
    assert persistence.persist_technical_evidence_graph(
        "tenant-a", mismatched_identity, graph, context, expected_store="Loja A",
    ) == {"status": "skipped", "reason": "identity_unavailable"}


def test_graph_persistence_requires_exact_item_and_resolved_variation(monkeypatch) -> None:
    calls = {"batches": 0}

    def create_batch(*_args, **_kwargs):
        calls["batches"] += 1
        return {"batch_id": "batch-1"}

    monkeypatch.setattr(persistence, "create_product_evidence_batch", create_batch)
    monkeypatch.setattr(
        persistence,
        "add_product_evidence_source",
        lambda *_args, **_kwargs: {"source_id": "source-1"},
    )
    monkeypatch.setattr(
        persistence,
        "add_product_evidence_claim",
        lambda *_args, **_kwargs: {"claim_id": "claim-1"},
    )
    monkeypatch.setattr(
        persistence,
        "complete_product_evidence_batch",
        lambda *_args, **_kwargs: {},
    )
    url = "https://catalogo.example/ficha"
    graph = {
        "entities": [{"id": "part", "kind": "product", "name": "Peca"}],
        "claims": [{
            "id": "c1",
            "entity_id": "part",
            "field_name": "electrical.voltage",
            "value": "12 V",
            "support": "supports",
            "source_refs": [url],
        }],
    }
    context = {"research_passages": [{
        "url": url,
        "source_type": "official_manufacturer",
        "text": "ficha tecnica",
    }]}

    missing_item = _agent_input()
    missing_item["product_evidence_identity"]["item_id"] = ""
    assert persistence.persist_technical_evidence_graph(
        "tenant-a", missing_item, graph, context, expected_store="Loja A",
    ) == {"status": "skipped", "reason": "identity_unavailable"}

    mismatched_item = _agent_input()
    mismatched_item["product_evidence_identity"]["item_id"] = "MLB2"
    assert persistence.persist_technical_evidence_graph(
        "tenant-a", mismatched_item, graph, context, expected_store="Loja A",
    ) == {"status": "skipped", "reason": "identity_unavailable"}

    unresolved = _agent_input()
    unresolved["item"]["variations"] = [{"id": "V12"}, {"id": "V24"}]
    assert persistence.persist_technical_evidence_graph(
        "tenant-a", unresolved, graph, context, expected_store="Loja A",
    ) == {"status": "skipped", "reason": "identity_unavailable"}

    wrong_variation = _agent_input()
    wrong_variation["item"]["variations"] = [{"id": "V12"}, {"id": "V24"}]
    wrong_variation["product_evidence_identity"]["variation_id"] = "V99"
    assert persistence.persist_technical_evidence_graph(
        "tenant-a", wrong_variation, graph, context, expected_store="Loja A",
    ) == {"status": "skipped", "reason": "identity_unavailable"}

    unverified_variation = _agent_input()
    unverified_variation["product_evidence_identity"]["variation_id"] = "V12"
    assert persistence.persist_technical_evidence_graph(
        "tenant-a", unverified_variation, graph, context, expected_store="Loja A",
    ) == {"status": "skipped", "reason": "identity_unavailable"}

    resolved = _agent_input()
    resolved["item"]["variations"] = [{"id": "V12"}, {"id": "V24"}]
    resolved["product_evidence_identity"]["variation_id"] = "V12"
    result = persistence.persist_technical_evidence_graph(
        "tenant-a", resolved, graph, context, expected_store="Loja A",
    )
    assert result["status"] == "completed"
    assert result["facts_written"] == 1
    assert calls["batches"] == 1


def test_graph_persistence_rejects_operational_text_smuggled_as_technical_facts(
    monkeypatch,
) -> None:
    calls = {"sources": [], "claims": []}
    monkeypatch.setattr(
        persistence,
        "create_product_evidence_batch",
        lambda *_args, **_kwargs: {"batch_id": "batch-1"},
    )
    monkeypatch.setattr(
        persistence,
        "add_product_evidence_source",
        lambda *_args, **payload: calls["sources"].append(payload) or {"source_id": "source-1"},
    )
    monkeypatch.setattr(
        persistence,
        "add_product_evidence_claim",
        lambda *_args, **payload: calls["claims"].append(payload) or {"claim_id": "claim-1"},
    )
    monkeypatch.setattr(
        persistence,
        "complete_product_evidence_batch",
        lambda *_args, **_kwargs: {},
    )

    agent_input = _agent_input()
    buyer_question = "O comprador perguntou se esta peca serve no Honda Fit 2005?"
    prior_answer = "Ja respondemos ao comprador que deveria informar o codigo da peca instalada."
    internal_prompt = (
        "INSTRUCAO INTERNA DO SISTEMA que nunca pode ser convertida em conhecimento "
        "tecnico reutilizavel nem publicada no dossie do produto."
    )
    agent_input["question"].update({
        "text": buyer_question,
        "history": [{"role": "seller", "text": prior_answer}],
        "current_draft_to_avoid": "Resposta anterior destinada somente a esta conversa publica.",
    })
    agent_input["prompt"] = internal_prompt

    url = "https://catalogo.example/ficha"
    graph = {
        "entities": [{"id": "part", "kind": "product", "name": "Peca"}],
        "claims": [
            {"id": "q", "entity_id": "part", "field_name": "technical.note", "value": buyer_question, "support": "supports", "source_refs": [url]},
            {"id": "h", "entity_id": "part", "field_name": "technical.note", "value": prior_answer, "support": "supports", "source_refs": [url]},
            {"id": "p", "entity_id": "part", "field_name": "technical.note", "value": internal_prompt[:180], "support": "supports", "source_refs": [url]},
            {"id": "f", "entity_id": "part", "field_name": f"technical.{buyer_question}", "value": "24 V", "support": "supports", "source_refs": [url]},
            {"id": "safe", "entity_id": "part", "field_name": "technical.note", "value": "Torque de aperto: 22 Nm", "support": "supports", "source_refs": [url]},
            {"id": "voltage", "entity_id": "part", "field_name": "electrical.voltage", "value": "12 V", "support": "supports", "source_refs": [url]},
        ],
    }
    context = {"research_passages": [{
        "url": url,
        "source_type": "official_manufacturer",
        "text": "ficha tecnica",
        "section_ref": buyer_question,
    }]}

    result = persistence.persist_technical_evidence_graph(
        "tenant-a", agent_input, graph, context, expected_store="Loja A",
    )

    assert result == {"status": "completed", "facts_written": 2, "sources": 1}
    assert [claim["value"] for claim in calls["claims"]] == [
        "Torque de aperto: 22 Nm",
        "12 V",
    ]
    assert calls["sources"][0]["section_ref"] == ""
