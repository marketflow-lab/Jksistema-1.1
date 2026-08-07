from __future__ import annotations

import inspect

from backend.modules.perguntas_pos_venda.endpoints import customer_reply
from backend.modules.perguntas_pos_venda.endpoints import diagnostics as endpoints


def test_approval_diagnostics_extracts_bounded_compatibility_context():
    context = {
        "diagnostico_ia": [{
            "function": "ml_questions_v2",
            "result": {
                "compatibility_analysis": {
                    "product_interface": "Base BMW Navigator IV/V/VI",
                    "target_vehicle": "BMW R1300GS",
                    "target_interface": "Preparacao BMW Navigator IV ou posterior",
                    "decision": "conditional",
                    "condition": "A moto precisa ter a preparacao original BMW.",
                    "missing_fields": ["ano", "modelo da base", "ano"],
                    "evidence": {
                        "product": [{
                            "source_type": "internal_listing",
                            "authority": "internal_listing",
                            "title": "Descricao do produto",
                            "url": "https://produto.mercadolivre.com.br/MLB-1?access_token=segredo",
                            "snippet": "Compativel com Navigator IV, V e VI.",
                            "raw_payload": "nao deve ser persistido",
                        }],
                        "target_vehicle": [{
                            "source_type": "manual",
                            "authority": "official",
                            "reference": "Manual BMW R1300GS, pagina 254",
                            "url": "https://manuals.bmw.example/r1300.pdf?signature=tracking",
                        }],
                        "equivalence": [{
                            "status": "matched",
                            "snippet": "Ambos usam a preparacao Navigator IV ou posterior.",
                        }],
                    },
                    "queries": [
                        {"type": "vehicle_interface", "query": "BMW R1300GS Navigator IV manual"},
                        {"type": "product_interface", "query": "access_token=segredo base Navigator V"},
                    ],
                    "sources": [
                        "https://manuals.bmw.example/r1300.pdf?access_token=segredo",
                        "https://produto.mercadolivre.com.br/MLB-1?tracking=abc",
                    ],
                    "confidence": 0.87654,
                    "reason": "Interfaces tecnicas equivalentes.",
                    "unknown_large_payload": "x" * 50_000,
                },
                "context_collection_pipeline": [{
                    "step": 4,
                    "name": "official_technical_research",
                    "status": "completed",
                    "query": "BMW R1300GS manual Navigator IV",
                    "sources": ["https://manuals.bmw.example/r1300.pdf?token=tracking"],
                    "access_token": "nao deve ser persistido",
                }],
                "confidence": 0.87654,
                "reason": "Interfaces tecnicas equivalentes.",
            },
        }],
    }

    persisted = endpoints._perguntas_ia_diagnostico_aprovacao(context)

    analysis = persisted["compatibility_analysis"]
    assert analysis["decision"] == "conditional"
    assert analysis["missing_fields"] == ["ano", "modelo da base"]
    assert analysis["confidence"] == 0.8765
    assert analysis["evidence"]["product"][0]["url"] == "https://produto.mercadolivre.com.br/MLB-1"
    assert "raw_payload" not in analysis["evidence"]["product"][0]
    assert "unknown_large_payload" not in analysis
    assert persisted["context_pipeline"][0] == {
        "step": 4,
        "name": "official_technical_research",
        "status": "completed",
        "query": "BMW R1300GS manual Navigator IV",
    }
    assert persisted["queries"][1]["query"] == "access_token=[redacted] base Navigator V"
    assert persisted["sources"] == [
        "https://manuals.bmw.example/r1300.pdf",
        "https://produto.mercadolivre.com.br/MLB-1",
    ]
    assert persisted["confidence"] == 0.8765
    assert persisted["reason"] == "Interfaces tecnicas equivalentes."


def test_approval_diagnostics_omits_absent_or_invalid_values():
    assert endpoints._perguntas_ia_diagnostico_aprovacao(None) == {}
    assert endpoints._perguntas_ia_diagnostico_aprovacao({"diagnostico_ia": []}) == {}

    persisted = endpoints._perguntas_ia_diagnostico_aprovacao({
        "compatibility_analysis": {
            "decision": "insufficient",
            "confidence": 4,
            "evidence": {"product": [], "target_vehicle": [], "equivalence": []},
        },
        "confidence": "not-a-number",
    })
    assert persisted == {
        "compatibility_analysis": {"decision": "insufficient"},
    }


def test_diagnostic_persistence_is_only_wired_to_listing_question_approval():
    listing_source = inspect.getsource(customer_reply._customer_reply_question_approval)
    post_sale_source = inspect.getsource(customer_reply._customer_reply_post_sale_approval)

    assert "approval.update(_perguntas_ia_diagnostico_aprovacao(contexto))" in listing_source
    assert "_perguntas_ia_diagnostico_aprovacao" not in post_sale_source
