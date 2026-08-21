from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.schemas.configuracoes import ConfiguracoesGlobaisRequest
from backend.schemas.ia import IAChatRequest
from backend.services import configuracoes, configuracoes_api
from backend.services import ia as _ia_facade  # noqa: F401
from backend.services import ia_providers
from backend.services import codex_assistant_storage
from backend.modules.perguntas_pos_venda.ai import execution as agent
from backend.services import perguntas_pos_venda_codex as orchestrator
from ml_questions_gemini.config import GeminiQuestionsSettings
from backend.services.codex.console import execution as console_execution
from backend.services.codex.console import security as console_security


class _ConfigApiFake:
    def __init__(self) -> None:
        self.data = configuracoes._normalizar_configuracoes_globais({})

    def carregar_configuracoes_globais(self) -> dict:
        return deepcopy(self.data)

    def salvar_configuracoes_globais(self, data: dict) -> None:
        self.data = configuracoes._normalizar_configuracoes_globais(data)

    normalizar_ia_modelo_padrao = staticmethod(ia_providers._normalizar_ia_modelo_padrao)
    normalizar_ia_modo = staticmethod(configuracoes._normalizar_ia_modo)
    vertex_modelo_nome_curto = staticmethod(ia_providers._vertex_modelo_nome_curto)
    salvar_ia_provider_api_key = staticmethod(lambda *_args, **_kwargs: None)
    salvar_vertex_agent_api_key = staticmethod(lambda *_args, **_kwargs: None)
    ia_secrets_publicar_no_provisionador_se_configurado = staticmethod(lambda: None)


def test_reasoning_configuration_round_trip_is_separate_and_normalized():
    fake = _ConfigApiFake()
    req = ConfiguracoesGlobaisRequest(
        auto_sync_estoque_janela_minutos=30,
        ia_modelo_perguntas="deepseek-chat",
        ia_modelo_pos_venda="gemini:gemini-2.5-flash",
        ia_raciocinio_perguntas=" HIGH ",
        ia_raciocinio_pos_venda="invalido",
    )

    result = configuracoes_api.atualizar_configuracoes_globais(fake, req)
    saved = result["configuracoes"]

    assert saved["ia_modelo_perguntas"] == "deepseek-chat"
    assert saved["ia_modelo_pos_venda"] == "gemini:gemini-2.5-flash"
    assert saved["ia_raciocinio_perguntas"] == "high"
    assert saved["ia_raciocinio_pos_venda"] == "medium"

    second = configuracoes_api.atualizar_configuracoes_globais(
        fake,
        ConfiguracoesGlobaisRequest(auto_sync_estoque_janela_minutos=45),
    )["configuracoes"]
    assert second["ia_raciocinio_perguntas"] == "high"
    assert second["ia_raciocinio_pos_venda"] == "medium"


def test_provider_resolves_reasoning_by_questions_and_post_sale(monkeypatch):
    monkeypatch.setattr(
        ia_providers,
        "_carregar_configuracoes_globais",
        lambda: {
            "ia_raciocinio_perguntas": "low",
            "ia_raciocinio_pos_venda": "xhigh",
        },
        raising=False,
    )

    question = IAChatRequest(
        message="Pergunta",
        context={"modulo": "perguntas_pos_venda", "tipo_treinamento": "perguntas_anuncio"},
    )
    post_sale = IAChatRequest(
        message="Pos-venda",
        context={"modulo": "perguntas_pos_venda", "ia_finalidade": "pos_venda"},
    )

    assert ia_providers._ia_codex_reasoning_effort_payload(question) == "low"
    assert ia_providers._ia_codex_reasoning_effort_payload(post_sale) == "xhigh"
    assert ia_providers._normalizar_codex_reasoning_effort("ultra") == "medium"


def test_codex_thread_run_receives_configured_post_sale_effort(monkeypatch, tmp_path):
    from backend.services import codex_console
    import openai_codex

    captured: dict = {}

    class _Thread:
        id = "thread-test"

        def run(self, _prompt: str, **kwargs):
            captured.update(kwargs)
            captured["registered"] = ia_providers.cancel_codex_persistent_turn("job-legacy-test")
            return SimpleNamespace(status=SimpleNamespace(value="completed"), final_response="Resposta")

    class _Codex:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def thread_start(self, **_kwargs):
            return _Thread()

    monkeypatch.setattr(console_execution, "enabled", lambda: True)
    monkeypatch.setattr(console_execution, "sdk_installed", lambda: True)
    monkeypatch.setattr(console_execution, "auth_detected", lambda: True)
    monkeypatch.setattr(console_execution, "runtime_bin", lambda: "codex")
    monkeypatch.setattr(console_execution, "sdk_env", lambda: {})
    monkeypatch.setattr(console_execution, "readonly_config_overrides", lambda: {})
    monkeypatch.setattr(console_security, "readonly_cwd", lambda *_args: str(tmp_path))
    monkeypatch.setattr(ia_providers, "_ia_raciocinio_pos_venda_configurado", lambda: "xhigh")
    monkeypatch.setattr(openai_codex, "Codex", _Codex)
    monkeypatch.setattr(openai_codex, "CodexConfig", lambda **kwargs: kwargs)

    response, thread_id = ia_providers._chamar_codex_chat_com_thread(
        IAChatRequest(
            message="Responda ao comprador",
            model="codex:gpt-5.5",
            context={"modulo": "perguntas_pos_venda", "ia_finalidade": "pos_venda"},
        ),
        "tenant-test",
        active_turn_key="job-legacy-test",
    )

    assert response == "Resposta"
    assert thread_id == "thread-test"
    assert captured["effort"].value == "xhigh"
    assert captured["registered"] is False


def test_codex_turn_path_registers_interruptible_turn(monkeypatch, tmp_path):
    from backend.services import codex_console
    import openai_codex

    captured: dict = {}

    class _Turn:
        def __init__(self):
            self.interruptions = 0

        def interrupt(self):
            self.interruptions += 1

        def run(self):
            captured["registered"] = ia_providers.cancel_codex_persistent_turn("job-turn-test")
            captured["interruptions"] = self.interruptions
            return SimpleNamespace(status=SimpleNamespace(value="completed"), final_response="Resposta")

    class _Thread:
        id = "thread-turn-test"

        def turn(self, _prompt: str, **kwargs):
            captured["turn_kwargs"] = kwargs
            captured["turn"] = _Turn()
            return captured["turn"]

        def run(self, _prompt: str, **_kwargs):
            raise AssertionError("thread.run nao deve ser usado quando thread.turn esta disponivel")

    class _Codex:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def thread_start(self, **_kwargs):
            return _Thread()

    monkeypatch.setattr(console_execution, "enabled", lambda: True)
    monkeypatch.setattr(console_execution, "sdk_installed", lambda: True)
    monkeypatch.setattr(console_execution, "auth_detected", lambda: True)
    monkeypatch.setattr(console_execution, "runtime_bin", lambda: "codex")
    monkeypatch.setattr(console_execution, "sdk_env", lambda: {})
    monkeypatch.setattr(console_execution, "readonly_config_overrides", lambda: {})
    monkeypatch.setattr(console_security, "readonly_cwd", lambda *_args: str(tmp_path))
    monkeypatch.setattr(openai_codex, "Codex", _Codex)
    monkeypatch.setattr(openai_codex, "CodexConfig", lambda **kwargs: kwargs)

    response, thread_id = ia_providers._chamar_codex_chat_com_thread(
        IAChatRequest(
            message="Responda ao comprador",
            model="codex:gpt-5.5",
            context={"modulo": "perguntas_pos_venda", "tipo_treinamento": "perguntas_anuncio"},
        ),
        "tenant-test",
        active_turn_key="job-turn-test",
        reasoning_effort="medium",
    )

    assert response == "Resposta"
    assert thread_id == "thread-turn-test"
    assert captured["registered"] is True
    assert captured["interruptions"] == 1
    assert captured["turn_kwargs"]["effort"].value == "medium"
    assert ia_providers.cancel_codex_persistent_turn("job-turn-test") is False


def test_thread_id_is_persisted_before_failed_turn_and_reused_on_retry(monkeypatch, tmp_path):
    from backend.services import codex_console
    import openai_codex

    calls = {"runs": 0, "resumed": []}

    class _Turn:
        def run(self):
            calls["runs"] += 1
            if calls["runs"] == 1:
                raise RuntimeError("turn crashed")
            return SimpleNamespace(status=SimpleNamespace(value="completed"), final_response="Resposta")

        def interrupt(self):
            return None

    class _Thread:
        def __init__(self, thread_id):
            self.id = thread_id

        def turn(self, _prompt, **_kwargs):
            return _Turn()

    class _Codex:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def thread_start(self, **_kwargs):
            return _Thread("thread-created-before-run")

        def thread_resume(self, thread_id, **_kwargs):
            calls["resumed"].append(thread_id)
            return _Thread(thread_id)

    monkeypatch.setattr(orchestrator, "_RUNTIME", SimpleNamespace(PASTA_INFO=str(tmp_path)))
    monkeypatch.setattr(orchestrator, "_schedule_retry_timer", lambda _job: None)
    monkeypatch.setattr(console_execution, "enabled", lambda: True)
    monkeypatch.setattr(console_execution, "sdk_installed", lambda: True)
    monkeypatch.setattr(console_execution, "auth_detected", lambda: True)
    monkeypatch.setattr(console_execution, "runtime_bin", lambda: "codex")
    monkeypatch.setattr(console_execution, "sdk_env", lambda: {})
    monkeypatch.setattr(console_execution, "readonly_config_overrides", lambda: {})
    monkeypatch.setattr(console_security, "readonly_cwd", lambda *_args: str(tmp_path))
    monkeypatch.setattr(openai_codex, "Codex", _Codex)
    monkeypatch.setattr(openai_codex, "CodexConfig", lambda **kwargs: kwargs)
    job = {
        "job_id": "job-thread-ready",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "public_question",
        "subject_key": "Q-THREAD",
        "store": "JK Pecas",
        "status": "running",
        "lease_owner": orchestrator._WORKER_ID,
        "lease_expires_ts": 9999999999,
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", job)
    payload = IAChatRequest(message="Responda", model="codex:gpt-5.5", context={"modulo": "perguntas_pos_venda"})

    with pytest.raises(Exception, match="Codex indisponivel"):
        ia_providers._chamar_codex_chat_com_thread(
            payload,
            "cliente",
            persist_thread=True,
            active_turn_key=job["job_id"],
            reasoning_effort="medium",
            on_thread_ready=lambda thread_id: orchestrator._persist_thread_ready(job, thread_id),
        )

    waiting = orchestrator._persist_retry(job, error="turn crashed")
    assert waiting["status"] == "waiting_retry"
    assert waiting["thread_id"] == "thread-created-before-run"
    running = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path),
        "cliente",
        {**waiting, "status": "running", "lease_owner": orchestrator._WORKER_ID},
    )
    response, thread_id = ia_providers._chamar_codex_chat_com_thread(
        payload,
        "cliente",
        thread_id=running["thread_id"],
        persist_thread=True,
        active_turn_key=job["job_id"],
        reasoning_effort="medium",
        on_thread_ready=lambda ready: orchestrator._persist_thread_ready(running, ready),
    )

    assert response == "Resposta"
    assert thread_id == "thread-created-before-run"
    assert calls["resumed"] == ["thread-created-before-run"]


@pytest.mark.parametrize(
    ("post_sale", "expected_max_chars", "expected_max_sentences"),
    ((False, 2000, 3), (True, 340, 3)),
)
def test_v2_applies_channel_reply_limits_and_keeps_provider_only_as_fallback(
    monkeypatch,
    post_sale,
    expected_max_chars,
    expected_max_sentences,
):
    captured: dict = {}

    class _Rules:
        min_confidence = 0.0
        max_chars = 0
        max_sentences = 0
        whitelisted_domains: list[str] = []

    class _Client:
        def __init__(self, _client_id, _store, model, _agent_input, reasoning_effort=None):
            captured["model"] = model
            captured["reasoning_effort"] = reasoning_effort
            self.model_usado = model
            self.context_pipeline = []
            self.compatibility_analysis = {}
            self.evidence_records = []
            self.codex_thread_id = ""

    result = SimpleNamespace(
        answer="Resposta segura",
        category=SimpleNamespace(value="product_detail"),
        route=SimpleNamespace(value="listing_only"),
        decision=SimpleNamespace(value="answer"),
        needs_human=False,
        confidence=0.9,
        source="gemini",
        reason="listing",
        validation=SimpleNamespace(ok=True, issues=[]),
        prompt="prompt",
        audit={},
    )

    monkeypatch.setattr(agent, "GeminiQuestionsSettings", GeminiQuestionsSettings, raising=False)
    monkeypatch.setattr(agent, "ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO", 900, raising=False)
    monkeypatch.setattr(agent, "ML_RESPOSTA_PERGUNTA_MAX_CHARS", 2000, raising=False)
    monkeypatch.setattr(agent, "ML_POS_VENDA_LIMITE_SEGURO", 340, raising=False)
    monkeypatch.setattr(agent, "_normalizar_ia_modelo_padrao", ia_providers._normalizar_ia_modelo_padrao, raising=False)
    monkeypatch.setattr(agent, "_modelo_eh_vertex_ai", ia_providers._modelo_eh_vertex_ai, raising=False)
    monkeypatch.setattr(agent, "_modelo_eh_codex", ia_providers._modelo_eh_codex, raising=False)
    monkeypatch.setattr(agent, "_ia_modelo_perguntas_configurado", lambda: "deepseek-chat", raising=False)
    monkeypatch.setattr(agent, "_ia_modelo_pos_venda_configurado", lambda: "deepseek-chat", raising=False)
    monkeypatch.setattr(agent, "_ia_raciocinio_perguntas_configurado", lambda: "high", raising=False)
    monkeypatch.setattr(agent, "_ia_raciocinio_pos_venda_configurado", lambda: "high", raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_fluxo_pos_venda", lambda _input: post_sale, raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_v2_exigir_aprovacao", lambda: True, raising=False)
    monkeypatch.setattr(agent, "_pos_venda_ia_v2_exigir_aprovacao", lambda: True, raising=False)
    monkeypatch.setattr(
        agent,
        "context_from_agent_input",
        lambda *_args, **_kwargs: (object(), object(), [], _Rules()),
        raising=False,
    )
    monkeypatch.setattr(agent, "_PerguntasCodexV3Client", _Client)
    def _orchestrator_factory(**kwargs):
        captured["max_chars"] = kwargs["settings"].max_chars
        captured["max_sentences"] = kwargs["settings"].max_sentences
        return SimpleNamespace(process=lambda **_process_kwargs: result)

    monkeypatch.setattr(agent, "QuestionAnswerOrchestrator", _orchestrator_factory, raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_limpar_resposta", lambda value: value, raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_resposta_final_loja", lambda value, _store: value, raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_resposta_fallback_invalida", lambda _value: False, raising=False)
    monkeypatch.setattr(agent, "_ia_agent_perguntas_violacoes_resposta", lambda *_args: [], raising=False)
    monkeypatch.setattr(agent, "_ia_agent_perguntas_log_perf", lambda *_args, **_kwargs: None, raising=False)

    answer, model, diagnostics = agent._perguntas_ia_v2_gerar_resposta(
        "tenant-test",
        {
            "store": "JK Pecas",
            "question": {"text": "Tem garantia?"},
            "intent": {"categoria": "post_sale" if post_sale else "product_feature"},
        },
    )

    assert answer == "Resposta segura"
    assert model == "codex:gpt-5.5"
    assert captured == {
        "model": "codex:gpt-5.5",
        "reasoning_effort": "high",
        "max_chars": expected_max_chars,
        "max_sentences": expected_max_sentences,
    }
    assert diagnostics[0]["result"]["reasoning_effort"] == "high"
    assert diagnostics[0]["result"]["response_provider_policy"] == "codex_only"
    assert diagnostics[0]["result"]["configured_fallback"] == "deepseek-chat"
    assert diagnostics[0]["result"]["fallback_used"] is False


def _configure_v2_insufficient_draft_case(
    monkeypatch,
    corrected_answer: str,
    *,
    initial_answer: str = "Ainda nao ha dados suficientes para responder.",
) -> dict:
    captured: dict = {"repair_calls": 0}

    class _Rules:
        min_confidence = 0.0
        max_chars = 0
        max_sentences = 0
        whitelisted_domains: list[str] = []

    class _Client:
        def __init__(self, _client_id, _store, model, _agent_input, reasoning_effort=None):
            self.model_usado = model
            self.context_pipeline = []
            self.compatibility_analysis = {
                "decision": "insufficient",
                "missing_fields": ["codigo_da_peca", "medida_bcd"],
                "confidence": 0.3,
            }
            self.evidence_records = []
            self.codex_thread_id = ""

    result = SimpleNamespace(
        answer=initial_answer,
        category=SimpleNamespace(value="compatibility"),
        route=SimpleNamespace(value="search_and_ai"),
        decision=SimpleNamespace(value="human_review"),
        needs_human=True,
        confidence=0.3,
        source="gemini",
        reason="compatibility_evidence_insufficient",
        validation=SimpleNamespace(
            ok=False,
            issues=["low_confidence", "compatibility_missing_detail_request"],
        ),
        prompt="prompt",
        audit={},
    )

    monkeypatch.setattr(agent, "GeminiQuestionsSettings", GeminiQuestionsSettings, raising=False)
    monkeypatch.setattr(agent, "ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO", 900, raising=False)
    monkeypatch.setattr(agent, "ML_RESPOSTA_PERGUNTA_MAX_CHARS", 2000, raising=False)
    monkeypatch.setattr(agent, "ML_POS_VENDA_LIMITE_SEGURO", 340, raising=False)
    monkeypatch.setattr(agent, "_normalizar_ia_modelo_padrao", ia_providers._normalizar_ia_modelo_padrao, raising=False)
    monkeypatch.setattr(agent, "_modelo_eh_vertex_ai", ia_providers._modelo_eh_vertex_ai, raising=False)
    monkeypatch.setattr(agent, "_modelo_eh_codex", ia_providers._modelo_eh_codex, raising=False)
    monkeypatch.setattr(agent, "_ia_modelo_perguntas_configurado", lambda: "deepseek-chat", raising=False)
    monkeypatch.setattr(agent, "_ia_raciocinio_perguntas_configurado", lambda: "high", raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_fluxo_pos_venda", lambda _input: False, raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_v2_exigir_aprovacao", lambda: True, raising=False)
    monkeypatch.setattr(
        agent,
        "context_from_agent_input",
        lambda *_args, **_kwargs: (object(), object(), [], _Rules()),
        raising=False,
    )
    monkeypatch.setattr(agent, "_PerguntasCodexV3Client", _Client)
    monkeypatch.setattr(
        agent,
        "QuestionAnswerOrchestrator",
        lambda **_kwargs: SimpleNamespace(process=lambda **_process_kwargs: result),
        raising=False,
    )
    monkeypatch.setattr(agent, "_perguntas_ia_limpar_resposta", lambda value: value, raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_resposta_final_loja", lambda value, _store: value, raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_resposta_fallback_invalida", lambda _value: False, raising=False)
    monkeypatch.setattr(agent, "_ia_agent_perguntas_violacoes_resposta", lambda *_args: [], raising=False)
    monkeypatch.setattr(agent, "_ia_agent_perguntas_log_perf", lambda *_args, **_kwargs: None, raising=False)

    def repair(_client_id, _store, _input, _model, _blocked, violations):
        captured["repair_calls"] += 1
        captured["violations"] = list(violations)
        return corrected_answer, "codex:gpt-5.5"

    monkeypatch.setattr(agent, "_perguntas_ia_v2_corrigir_resposta_bloqueada", repair, raising=False)
    return captured


def test_v2_accepts_safe_available_information_draft_without_requesting_data(monkeypatch):
    captured = _configure_v2_insufficient_draft_case(
        monkeypatch,
        "Para confirmar, informe o codigo da coroa e a medida BCD.",
    )

    answer, _model, diagnostics = agent._perguntas_ia_v2_gerar_resposta(
        "tenant-test",
        {
            "store": "JK Pecas",
            "question": {"text": "Essa coroa serve no pe de vela BCD 96?"},
            "item": {},
            "intent": {"categoria": "compatibility"},
        },
    )

    assert answer == "Ainda nao ha dados suficientes para responder."
    assert captured["repair_calls"] == 0
    assert "app_validation_repaired" not in diagnostics[0]["result"]


def test_v2_preserves_ai_draft_without_validation_or_repair(monkeypatch):
    original_answer = (
        "O uso desta coroa em uma relação 2x9 com outra coroa não está confirmado. "
        "Ela é uma coroa única Narrow Wide, com BCD 104 mm e 52 dentes."
    )
    captured = _configure_v2_insufficient_draft_case(
        monkeypatch,
        "Sim, serve perfeitamente. Envie uma foto para confirmar.",
        initial_answer=original_answer,
    )

    answer, _model, diagnostics = agent._perguntas_ia_v2_gerar_resposta(
        "tenant-test",
        {
            "store": "JK Pecas",
            "question": {"text": "Essa coroa serve em uma relação 2x9?"},
            "item": {},
            "intent": {"categoria": "compatibility"},
        },
    )

    assert answer == original_answer
    assert captured["repair_calls"] == 0
    assert "app_validation_repaired" not in diagnostics[0]["result"]
