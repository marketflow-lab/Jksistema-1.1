from backend.modules.perguntas_pos_venda.ai.factual_critic import (
    FACTUAL_REVIEW_VERSION,
    factual_review_prompt,
    factual_revision_prompt,
    normalize_factual_review,
    review_requires_revision,
)


def test_normalize_factual_review_keeps_advisory_issue_and_clamps_confidence():
    review = normalize_factual_review({
        "schema": "ignored",
        "verdict": "revise",
        "issues": [{
            "code": "unsupported_claim",
            "message": "Codigo sem origem",
            "claim": "37760-PWA-J01",
            "source_refs": ["passage-1", "passage-1"],
        }],
        "revision_instructions": ["Remova o codigo sem procedencia."],
        "confidence": 9,
    })

    assert review["schema"] == FACTUAL_REVIEW_VERSION
    assert review["verdict"] == "revise"
    assert review["issues"][0]["source_refs"] == ["passage-1"]
    assert review["confidence"] == 1.0
    assert review_requires_revision(review) is True


def test_invalid_critic_payload_is_insufficient_and_never_becomes_public_text():
    review = normalize_factual_review("not-json")

    assert review == {
        "schema": FACTUAL_REVIEW_VERSION,
        "verdict": "insufficient",
        "issues": [],
        "revision_instructions": [],
        "confidence": 0.0,
    }
    assert review_requires_revision(review) is False


def test_prompts_preserve_candidate_bytes_inside_untrusted_json():
    candidate = "  Sim, serve.\r\nLinha dois ✅  "
    review_prompt = factual_review_prompt(
        candidate_body=candidate,
        technical_resolution={"commercial_state": "fits"},
        research={"product_research_evidence": []},
    )
    revision_prompt = factual_revision_prompt(
        preserved_candidate_body=candidate,
        review={"verdict": "revise"},
        technical_resolution={"commercial_state": "fits"},
        research={},
    )

    assert "  Sim, serve.\\r\\nLinha dois ✅  " in review_prompt
    assert "  Sim, serve.\\r\\nLinha dois ✅  " in revision_prompt
    assert "UNTRUSTED_REFERENCE_DATA" in review_prompt
    assert "assinatura de loja" in review_prompt
    assert "Nao inclua assinatura no answer" in revision_prompt
    assert "Equipe Loja agradece" not in revision_prompt
