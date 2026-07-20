"""WhatsApp bridge settings normalization.

This component is intentionally independent from ``whatsapp_bridge``. Runtime
configuration loading stays in the compatibility facade and the resulting
mapping is passed into the functions below.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from backend.services import ia_providers, whatsapp_voice


WHATSAPP_AI_DEFAULT_MODEL = "codex:gpt-5.5"
WHATSAPP_CODEX_REASONING_DEFAULT = "xhigh"
WHATSAPP_CODEX_REASONING_OPTIONS = ("low", "medium", "high", "xhigh")
WHATSAPP_CODEX_REASONING_POLICY_DEFAULT = "adaptive"
WHATSAPP_CODEX_REASONING_POLICIES = ("adaptive", "fixed")
WHATSAPP_ORCHESTRATION_MODE_DEFAULT = "all_when_codex_selected"
WHATSAPP_PROGRESS_INTERVAL_DEFAULT = 8
WHATSAPP_AGENT_ARCHITECTURE_DEFAULT = "dual_codex"
WHATSAPP_RESPONSE_PROVIDER_POLICY_DEFAULT = "codex_only"
WHATSAPP_RESPONSE_PROVIDER_POLICIES = ("codex_only", "codex_then_configured_fallback")
WHATSAPP_CONVERSATION_AGENT_MODEL_DEFAULT = "gpt-5.6-luna"
WHATSAPP_CONVERSATION_AGENT_REASONING_DEFAULT = "low"
WHATSAPP_TASK_AGENT_MODEL_DEFAULT = "gpt-5.6-sol"
WHATSAPP_TASK_AGENT_REASONING_DEFAULT = "low"
WHATSAPP_CODEX_SPEED_DEFAULT = "fast"
WHATSAPP_CODEX_SERVICE_TIER_DEFAULT = "priority"
WHATSAPP_CONVERSATION_INTERVAL_DEFAULT = 30
WHATSAPP_WAIT_MESSAGE_AFTER_DEFAULT = 15
WHATSAPP_WAIT_MESSAGE_REPEAT_DEFAULT = 30
WHATSAPP_WAIT_MESSAGE_STEADY_DEFAULT = 60
WHATSAPP_PARTIAL_DEBOUNCE_DEFAULT = 2
WHATSAPP_JOB_DEADLINE_DEFAULT = 120
WHATSAPP_MAX_SUBTASKS_DEFAULT = 6
WHATSAPP_MAX_ACTIVE_TASK_AGENTS_DEFAULT = 6
WHATSAPP_CONVERSATION_WORKER_COUNT_DEFAULT = 4
WHATSAPP_CONVERSATION_RUNTIME_POOL_SIZE_DEFAULT = 4
WHATSAPP_DATA_SELECTION_ENABLED_DEFAULT = True
WHATSAPP_DATA_SELECTION_WORKER_COUNT_DEFAULT = 4
WHATSAPP_DATA_SELECTION_RUNTIME_POOL_SIZE_DEFAULT = 4
# Aliases de importacao preservados durante a remocao do Function Manager
# semantico. Os campos de configuracao antigos nao controlam mais o runtime.
WHATSAPP_FUNCTION_MANAGER_ENABLED_DEFAULT = WHATSAPP_DATA_SELECTION_ENABLED_DEFAULT
WHATSAPP_FUNCTION_MANAGER_REQUIRED_DEFAULT = True
WHATSAPP_FUNCTION_MANAGER_WORKER_COUNT_DEFAULT = WHATSAPP_DATA_SELECTION_WORKER_COUNT_DEFAULT
WHATSAPP_FUNCTION_MANAGER_RUNTIME_POOL_SIZE_DEFAULT = WHATSAPP_DATA_SELECTION_RUNTIME_POOL_SIZE_DEFAULT
WHATSAPP_MAX_ACTIVE_TASK_AGENTS_GLOBAL_DEFAULT = 12
_CONTEXT_HUB_CLIENT_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")


def normalize_ai_model(value: Any) -> str:
    raw = str(value or WHATSAPP_AI_DEFAULT_MODEL).strip()
    if len(raw) > 100 or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", raw):
        raise HTTPException(status_code=400, detail="Modelo de IA do WhatsApp invalido.")
    return ia_providers._normalizar_ia_modelo_padrao(raw)


def normalize_codex_reasoning_effort(value: Any) -> str:
    effort = str(value or WHATSAPP_CODEX_REASONING_DEFAULT).strip().lower()
    if effort not in WHATSAPP_CODEX_REASONING_OPTIONS:
        raise HTTPException(status_code=400, detail="Padrao de inteligencia do Codex invalido.")
    return effort


def normalize_codex_reasoning_policy(value: Any) -> str:
    policy = str(value or WHATSAPP_CODEX_REASONING_POLICY_DEFAULT).strip().lower()
    if policy not in WHATSAPP_CODEX_REASONING_POLICIES:
        raise HTTPException(status_code=400, detail="Politica de inteligencia do Codex invalida.")
    return policy


def normalize_codex_agent_model(value: Any, default: str) -> str:
    raw = str(value or default).strip()
    if raw.lower().startswith("codex:"):
        raw = raw.split(":", 1)[1]
    if len(raw) > 100 or not re.fullmatch(r"[A-Za-z0-9_.-]+", raw):
        raise HTTPException(status_code=400, detail="Modelo do agente Codex invalido.")
    return raw


def normalize_agent_architecture(value: Any) -> str:
    architecture = str(value or WHATSAPP_AGENT_ARCHITECTURE_DEFAULT).strip().lower()
    if architecture not in {"dual_codex", "legacy"}:
        raise HTTPException(status_code=400, detail="Arquitetura de agentes do WhatsApp invalida.")
    # One-release compatibility: accept the old administrative value but do
    # not reactivate the removed semantic router.  The direct cutover always
    # materializes the Luna + CodexDataSelectionAgent architecture.
    return "dual_codex"


def normalize_response_provider_policy(value: Any) -> str:
    policy = str(value or WHATSAPP_RESPONSE_PROVIDER_POLICY_DEFAULT).strip().lower()
    if policy not in WHATSAPP_RESPONSE_PROVIDER_POLICIES:
        raise HTTPException(status_code=400, detail="Politica de provedor de resposta do WhatsApp invalida.")
    return policy


def normalize_conversation_interval(value: Any) -> int:
    try:
        interval = int(value or WHATSAPP_CONVERSATION_INTERVAL_DEFAULT)
    except (TypeError, ValueError):
        interval = WHATSAPP_CONVERSATION_INTERVAL_DEFAULT
    return max(10, min(interval, 300))


def normalize_capacity(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value if value is not None else fallback)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def normalize_context_hub_client_id(value: Any) -> str:
    client_id = str(value or "").strip()
    return client_id if _CONTEXT_HUB_CLIENT_ID_RE.fullmatch(client_id) else ""


def normalize_context_hub_enabled_by_client(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, bool] = {}
    for raw_client_id, raw_enabled in value.items():
        client_id = normalize_context_hub_client_id(raw_client_id)
        if client_id and isinstance(raw_enabled, bool):
            normalized[client_id] = raw_enabled
    return normalized


def context_hub_enabled_default(config: Any) -> bool:
    source = config if isinstance(config, dict) else {}
    if "context_hub_enabled_default" in source:
        return source.get("context_hub_enabled_default") is not False
    # Compatibilidade com configuracoes anteriores ao escopo por cliente.
    return source.get("context_hub_enabled") is not False


def context_hub_enabled_for_client(config: Any, client_id: Any) -> bool:
    source = config if isinstance(config, dict) else {}
    normalized_client_id = normalize_context_hub_client_id(client_id)
    overrides = normalize_context_hub_enabled_by_client(source.get("context_hub_enabled_by_client"))
    if normalized_client_id and normalized_client_id in overrides:
        return overrides[normalized_client_id]
    return context_hub_enabled_default(source)


def set_context_hub_enabled_for_client(config: dict[str, Any], client_id: Any, enabled: bool) -> None:
    normalized_client_id = normalize_context_hub_client_id(client_id)
    if not normalized_client_id:
        raise ValueError("invalid_context_hub_client_id")
    default_enabled = context_hub_enabled_default(config)
    overrides = normalize_context_hub_enabled_by_client(config.get("context_hub_enabled_by_client"))
    overrides[normalized_client_id] = bool(enabled)
    config["context_hub_enabled_default"] = default_enabled
    config["context_hub_enabled_by_client"] = overrides
    # Campo escalar preservado para consumidores legados no tenant materializado.
    config["context_hub_enabled"] = bool(enabled)
    # Marcador somente em memoria: _save_config usa-o para mesclar o override
    # com o mapa persistido dentro do mesmo lock, sem perder outro tenant.
    config["_context_hub_enabled_override"] = {
        "client_id": normalized_client_id,
        "enabled": bool(enabled),
    }


def dual_agent_settings(
    config: dict[str, Any],
    *,
    report_deadline_seconds: int = 10 * 60,
    max_retry_attempts: int = 3,
    client_id: Any = None,
) -> dict[str, Any]:
    source = config if isinstance(config, dict) else {}
    worker_count = normalize_capacity(
        source.get("conversation_worker_count"), WHATSAPP_CONVERSATION_WORKER_COUNT_DEFAULT, 1, 8
    )
    runtime_pool_size = max(
        worker_count,
        normalize_capacity(
            source.get("conversation_runtime_pool_size"),
            WHATSAPP_CONVERSATION_RUNTIME_POOL_SIZE_DEFAULT,
            1,
            8,
        ),
    )
    selection_worker_count = normalize_capacity(
        source.get("data_selection_worker_count"),
        WHATSAPP_DATA_SELECTION_WORKER_COUNT_DEFAULT,
        1,
        8,
    )
    selection_pool_size = max(
        selection_worker_count,
        normalize_capacity(
            source.get("data_selection_runtime_pool_size"),
            WHATSAPP_DATA_SELECTION_RUNTIME_POOL_SIZE_DEFAULT,
            1,
            8,
        ),
    )
    return {
        "agent_architecture": normalize_agent_architecture(source.get("agent_architecture")),
        "response_provider_policy": normalize_response_provider_policy(
            source.get("response_provider_policy")
        ),
        "conversation_agent_model": normalize_codex_agent_model(
            source.get("conversation_agent_model"), WHATSAPP_CONVERSATION_AGENT_MODEL_DEFAULT
        ),
        "conversation_agent_reasoning": normalize_codex_reasoning_effort(
            source.get("conversation_agent_reasoning") or WHATSAPP_CONVERSATION_AGENT_REASONING_DEFAULT
        ),
        "task_agent_model": normalize_codex_agent_model(
            source.get("task_agent_model"), WHATSAPP_TASK_AGENT_MODEL_DEFAULT
        ),
        "task_agent_reasoning": WHATSAPP_TASK_AGENT_REASONING_DEFAULT,
        "conversation_agent_speed": WHATSAPP_CODEX_SPEED_DEFAULT,
        "conversation_agent_service_tier": WHATSAPP_CODEX_SERVICE_TIER_DEFAULT,
        "task_agent_speed": WHATSAPP_CODEX_SPEED_DEFAULT,
        "task_agent_service_tier": WHATSAPP_CODEX_SERVICE_TIER_DEFAULT,
        "conversation_interval_seconds": normalize_conversation_interval(
            source.get("conversation_interval_seconds")
        ),
        "wait_message_after_seconds": normalize_capacity(
            source.get("wait_message_after_seconds"), WHATSAPP_WAIT_MESSAGE_AFTER_DEFAULT, 5, 60
        ),
        "wait_message_repeat_seconds": normalize_capacity(
            source.get("wait_message_repeat_seconds"), WHATSAPP_WAIT_MESSAGE_REPEAT_DEFAULT, 15, 180
        ),
        "wait_message_steady_seconds": normalize_capacity(
            source.get("wait_message_steady_seconds"), WHATSAPP_WAIT_MESSAGE_STEADY_DEFAULT, 30, 300
        ),
        "partial_delivery_debounce_seconds": normalize_capacity(
            source.get("partial_delivery_debounce_seconds"), WHATSAPP_PARTIAL_DEBOUNCE_DEFAULT, 1, 10
        ),
        "job_deadline_seconds": normalize_capacity(
            source.get("job_deadline_seconds"),
            WHATSAPP_JOB_DEADLINE_DEFAULT,
            30,
            report_deadline_seconds,
        ),
        "retry_policy": "bounded",
        "max_retry_attempts": max_retry_attempts,
        "max_subtasks_per_job": normalize_capacity(
            source.get("max_subtasks_per_job"), WHATSAPP_MAX_SUBTASKS_DEFAULT, 1, 6
        ),
        "progress_messages_enabled": source.get("progress_messages_enabled") is True,
        "max_active_task_agents_per_conversation": normalize_capacity(
            source.get("max_active_task_agents_per_conversation"),
            WHATSAPP_MAX_ACTIVE_TASK_AGENTS_DEFAULT,
            1,
            6,
        ),
        "conversation_worker_count": worker_count,
        "conversation_runtime_pool_size": runtime_pool_size,
        "max_active_task_agents_global": normalize_capacity(
            source.get("max_active_task_agents_global"),
            WHATSAPP_MAX_ACTIVE_TASK_AGENTS_GLOBAL_DEFAULT,
            1,
            12,
        ),
        "preserve_order_per_phone": True,
        "data_selection_enabled": True,
        "data_selection_required_before_sol": True,
        "data_selection_worker_count": selection_worker_count,
        "data_selection_runtime_pool_size": selection_pool_size,
        "context_hub_enabled": context_hub_enabled_for_client(
            source,
            source.get("client_id") if client_id is None else client_id,
        ),
        # Compatibilidade de leitura para consumidores antigos. Estes valores
        # espelham o novo seletor; chaves `function_manager_*` recebidas na
        # configuracao sao deliberadamente ignoradas.
        "function_manager_enabled": True,
        "function_manager_required_before_sol": True,
        "function_manager_worker_count": selection_worker_count,
        "function_manager_runtime_pool_size": selection_pool_size,
        "function_manager_legacy_fields_ignored": True,
    }


def normalize_progress_interval(value: Any) -> int:
    try:
        interval = int(value or WHATSAPP_PROGRESS_INTERVAL_DEFAULT)
    except (TypeError, ValueError):
        interval = WHATSAPP_PROGRESS_INTERVAL_DEFAULT
    return max(8, min(interval, 60))


def normalize_voice_model(value: Any, fallback: str) -> str:
    model = str(value or fallback).strip()
    if len(model) > 100 or not re.fullmatch(r"[A-Za-z0-9_.-]+", model):
        raise HTTPException(status_code=400, detail="Modelo de voz OpenAI invalido.")
    return model


def normalize_voice_name(value: Any) -> str:
    voice = str(value or whatsapp_voice.VOICE_NAME_DEFAULT).strip().lower()
    allowed = {"alloy", "ash", "ballad", "cedar", "coral", "echo", "marin", "sage", "shimmer", "verse"}
    if voice not in allowed:
        raise HTTPException(status_code=400, detail="Voz Realtime invalida.")
    return voice


def normalize_voice_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value or fallback)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def normalize_voice_config(config: dict[str, Any]) -> dict[str, Any]:
    value = dict(config or {})
    value["voice_enabled"] = value.get("voice_enabled") is True
    value["voice_model"] = normalize_voice_model(value.get("voice_model"), whatsapp_voice.VOICE_MODEL_DEFAULT)
    value["voice_transcription_model"] = normalize_voice_model(
        value.get("voice_transcription_model"), whatsapp_voice.VOICE_TRANSCRIPTION_MODEL_DEFAULT
    )
    value["voice_name"] = normalize_voice_name(value.get("voice_name"))
    value["voice_language"] = "pt-BR"
    value["voice_max_call_minutes"] = normalize_voice_int(value.get("voice_max_call_minutes"), 30, 5, 60)
    value["voice_silence_timeout_seconds"] = normalize_voice_int(
        value.get("voice_silence_timeout_seconds"), 90, 30, 300
    )
    value["voice_long_task_offer_seconds"] = normalize_voice_int(
        value.get("voice_long_task_offer_seconds"), 90, 30, 300
    )
    value["voice_max_concurrent_calls"] = normalize_voice_int(value.get("voice_max_concurrent_calls"), 3, 1, 10)
    value["voice_progress_interval_seconds"] = normalize_voice_int(
        value.get("voice_progress_interval_seconds"), 8, 8, 30
    )
    value["voice_transcript_retention"] = "transcript_only"
    value["voice_store_audio"] = False
    value["voice_read_only"] = True
    return value


def ai_settings(config: dict[str, Any]) -> dict[str, str]:
    source = config if isinstance(config, dict) else {}
    model = normalize_ai_model(source.get("ai_model"))
    reasoning = normalize_codex_reasoning_effort(source.get("codex_reasoning_effort"))
    if model.startswith("codex:"):
        provider = "codex"
    elif model.startswith("vertex:"):
        provider = "vertex"
    elif model.startswith("gemini:"):
        provider = "gemini"
    elif model.startswith("deepseek-"):
        provider = "deepseek"
    else:
        provider = "openai"
    return {
        "model": model,
        # O Black Jhon sempre usa Codex como nucleo semantico. `ai_model`
        # identifica somente o destino opcional do fallback operacional.
        "provider": "codex",
        "fallback_model": model,
        "fallback_provider": provider,
        "response_provider_policy": normalize_response_provider_policy(
            source.get("response_provider_policy")
        ),
        "codex_reasoning_effort": reasoning,
        "codex_reasoning_policy": normalize_codex_reasoning_policy(source.get("codex_reasoning_policy")),
        "codex_reasoning_max": normalize_codex_reasoning_effort(source.get("codex_reasoning_max") or reasoning),
    }


def default_phone_notification_settings() -> dict[str, Any]:
    return {
        "label": "",
        "send_ml_question_suggestions": True,
        "send_weekly_report": False,
        "send_monthly_report": False,
        "ai_behavior": "",
        "allow_voice_calls": False,
    }


def normalize_phone_ai_behavior(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(lines).strip()[:2000]


def normalize_phone_notification_settings(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    result = default_phone_notification_settings()
    result.update(
        {
            "label": re.sub(r"\s+", " ", str(source.get("label") or "")).strip()[:60],
            "send_ml_question_suggestions": source.get("send_ml_question_suggestions") is not False,
            "send_weekly_report": source.get("send_weekly_report") is True,
            "send_monthly_report": source.get("send_monthly_report") is True,
            "ai_behavior": normalize_phone_ai_behavior(source.get("ai_behavior")),
            "allow_voice_calls": source.get("allow_voice_calls") is True,
        }
    )
    return result


def phone_notification_settings(
    config: dict[str, Any],
    subject_id: Any,
    *,
    client_id: Any = "",
    username: Any = "",
) -> dict[str, Any]:
    subject = str(subject_id or "").strip()
    settings_by_phone = config.get("phone_notification_settings")
    stored = settings_by_phone.get(subject) if subject and isinstance(settings_by_phone, dict) else {}
    result = normalize_phone_notification_settings(stored)
    result.update(
        {
            "subject_id": subject,
            "client_id": str(
                client_id or (stored.get("client_id") if isinstance(stored, dict) else "") or ""
            ).strip(),
            "username": str(
                username or (stored.get("username") if isinstance(stored, dict) else "") or ""
            ).strip().lower(),
        }
    )
    return result
