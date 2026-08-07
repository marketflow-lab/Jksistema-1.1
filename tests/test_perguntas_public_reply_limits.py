from __future__ import annotations

from backend.services import perguntas_pos_venda_codex as codex
from backend.services import perguntas_pos_venda_core as state
from backend.services import perguntas_pos_venda_state as state_module
from backend.services.favoritos_ranking_ia import _favoritos_normalizar_sem_acentos
from ml_questions_gemini.adapters import context_from_agent_input
from ml_questions_gemini.prompt_builder import PromptBuilder
from ml_questions_gemini.schemas import ListingSnapshot, QuestionCategory, QuestionContext, SellerRules
from ml_questions_gemini.validator import AnswerValidator


def test_public_question_uses_full_mercado_livre_limit_and_keeps_post_sale_limits():
    assert state.ML_RESPOSTA_PERGUNTA_MAX_CHARS == 2000
    assert state.ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO == 2000
    assert state.ML_POS_VENDA_DEFAULT_MAX_CHARS == 350
    assert state.ML_POS_VENDA_LIMITE_SEGURO == 340


def test_public_question_cleaner_preserves_2000_and_truncates_only_above_limit():
    exact = "x" * 2000
    over = "x" * 2001

    assert state._perguntas_ia_limpar_resposta(exact) == exact
    truncated = state._perguntas_ia_limpar_resposta(over)
    assert len(truncated) == 2000
    assert truncated.endswith("...")


def test_public_question_signature_stays_inside_2000_characters(monkeypatch):
    monkeypatch.setattr(
        state_module,
        "_favoritos_normalizar_sem_acentos",
        _favoritos_normalizar_sem_acentos,
        raising=False,
    )
    answer = state._perguntas_ia_resposta_final_loja("x" * 2000, "JK Pecas")

    assert len(answer) == 2000
    assert answer.endswith("Equipe JK Pecas agradece o seu contato.")


def test_public_question_draft_preserves_2000_characters_for_revision():
    payload = state._perguntas_ia_pergunta_para_agente({"_resposta_atual": "x" * 2000})

    assert len(payload["current_draft_to_avoid"]) == 2000


def test_public_question_prompt_and_validator_enforce_three_content_sentences():
    rules = SellerRules(store_name="JK Pecas", max_chars=2000, max_sentences=3, min_confidence=0)
    question = QuestionContext(id="Q1", text="Explique os detalhes confirmados.")
    listing = ListingSnapshot(id="MLB1", title="Produto", description="Descricao confirmada.")

    prompt = PromptBuilder().build(
        question=question,
        listing=listing,
        previous_questions=[],
        category=QuestionCategory.PRODUCT_FEATURE,
        rules=rules,
        search_results=[],
    )
    validation = AnswerValidator().validate(
        "Primeira. Segunda. Terceira. Quarta.",
        question=question,
        listing=listing,
        category=QuestionCategory.PRODUCT_FEATURE,
        rules=rules,
        confidence=1,
    )

    assert '"max_chars": 2000' in prompt
    assert '"max_sentences": 3' in prompt
    assert "string curta, em portugues do Brasil" in prompt
    assert "too_many_sentences" in validation.issues


def test_public_question_main_v3_prompt_receives_full_2000_character_draft():
    draft = "z" * 2000
    question, listing, previous, rules = context_from_agent_input({
        "store": "JK Pecas",
        "question": {
            "id": "Q-DRAFT",
            "text": "Revise a resposta.",
            "current_draft_to_avoid": draft,
        },
        "item": {"id": "MLB-DRAFT", "title": "Produto"},
    })
    rules.max_chars = 2000
    rules.max_sentences = 3

    prompt = PromptBuilder().build(
        question=question,
        listing=listing,
        previous_questions=previous,
        category=QuestionCategory.PRODUCT_FEATURE,
        rules=rules,
        search_results=[],
    )

    assert draft in prompt
    assert '"current_draft_to_revise"' in prompt


def test_post_sale_prompt_keeps_its_short_answer_contract():
    rules = SellerRules(store_name="JK Pecas", max_chars=340, max_sentences=3)
    prompt = PromptBuilder().build(
        question=QuestionContext(id="Q2", text="O produto apresentou defeito."),
        listing=ListingSnapshot(id="MLB2", title="Produto"),
        previous_questions=[],
        category=QuestionCategory.POST_SALE,
        rules=rules,
        search_results=[],
    )

    assert '"max_chars": 340' in prompt
    assert '"max_sentences": 3' in prompt
    assert "string curta, em portugues do Brasil" in prompt


def test_public_question_jobs_have_bounded_total_deadline_after_first_claim():
    job = {
        "task_type": codex.TASK_TYPE_PUBLIC_QUESTION,
        "first_started_at_epoch": 100,
        "deadline_at_epoch": 0,
    }

    assert codex._job_deadline_epoch(job) == 1000
    assert codex._job_deadline_expired(job, now=999) is False
    assert codex._job_deadline_expired(job, now=1000) is True
    assert job["deadline_seconds"] == 900
    assert job["deadline_at_epoch"] == 1000
    assert codex._task_retry_policy(job["task_type"]) == "bounded"
    assert codex._task_deadline_seconds(codex.TASK_TYPE_POST_SALE) == 180
    assert codex._task_retry_policy(codex.TASK_TYPE_POST_SALE) == "bounded"


def test_public_question_research_history_preserves_2000_character_draft():
    entry = codex._research_history_entry(
        {"task_type": codex.TASK_TYPE_PUBLIC_QUESTION, "attempt_count": 1},
        answer="x" * 2000,
    )

    assert len(entry["answer"]) == 2000
