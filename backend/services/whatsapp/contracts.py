"""Contratos HTTP e erros internos do bridge do WhatsApp."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class _QuestionResearchPending(RuntimeError):
    def __init__(self, job_id: str, status: str = "queued") -> None:
        super().__init__("question_research_pending")
        self.job_id = str(job_id or "")
        self.status = str(status or "queued")


QuestionResearchPending = _QuestionResearchPending


class WhatsappBridgeConfigRequest(BaseModel):
    worker_url: Optional[str] = None
    bridge_token: Optional[str] = None
    business_phone: Optional[str] = None
    ai_model: Optional[str] = None
    codex_reasoning_effort: Optional[str] = None
    codex_reasoning_policy: Optional[str] = None
    codex_reasoning_max: Optional[str] = None
    orchestration_mode: Optional[str] = None
    progress_interval_seconds: Optional[int] = None
    progress_explain_wait: Optional[bool] = None
    active_task_policy: Optional[str] = None
    agent_architecture: Optional[str] = None
    response_provider_policy: Optional[str] = None
    conversation_agent_model: Optional[str] = None
    conversation_agent_reasoning: Optional[str] = None
    task_agent_model: Optional[str] = None
    task_agent_reasoning: Optional[str] = None
    conversation_interval_seconds: Optional[int] = None
    wait_message_after_seconds: Optional[int] = None
    wait_message_repeat_seconds: Optional[int] = None
    wait_message_steady_seconds: Optional[int] = None
    partial_delivery_debounce_seconds: Optional[int] = None
    job_deadline_seconds: Optional[int] = None
    max_subtasks_per_job: Optional[int] = None
    progress_messages_enabled: Optional[bool] = None
    max_active_task_agents_per_conversation: Optional[int] = None
    conversation_worker_count: Optional[int] = None
    conversation_runtime_pool_size: Optional[int] = None
    max_active_task_agents_global: Optional[int] = None
    preserve_order_per_phone: Optional[bool] = None
    data_selection_enabled: Optional[bool] = None
    data_selection_worker_count: Optional[int] = None
    data_selection_runtime_pool_size: Optional[int] = None
    # Aceitos apenas para compatibilidade de clientes antigos. O backend nao
    # usa mais estes campos para ativar ou dimensionar o seletor de dados.
    function_manager_enabled: Optional[bool] = None
    function_manager_required_before_sol: Optional[bool] = None
    context_hub_enabled: Optional[bool] = None
    function_manager_worker_count: Optional[int] = None
    function_manager_runtime_pool_size: Optional[int] = None
    voice_model: Optional[str] = None
    voice_transcription_model: Optional[str] = None
    voice_name: Optional[str] = None
    voice_language: Optional[str] = None
    voice_max_call_minutes: Optional[int] = None
    voice_silence_timeout_seconds: Optional[int] = None
    voice_long_task_offer_seconds: Optional[int] = None
    voice_max_concurrent_calls: Optional[int] = None
    voice_progress_interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None


class WhatsappTemplatesRequest(BaseModel):
    create_missing: bool = False


class WhatsappPairingCodeRequest(BaseModel):
    username: Optional[str] = None
    client_id: Optional[str] = None


class WhatsappBindingRevokeRequest(BaseModel):
    subject_id: Optional[str] = None
    revoke_all: bool = False
    username: Optional[str] = None
    client_id: Optional[str] = None


class WhatsappPhoneSettingsRequest(BaseModel):
    subject_id: str
    username: str
    client_id: str
    label: Optional[str] = None
    send_ml_question_suggestions: bool = True
    send_weekly_report: bool = False
    send_monthly_report: bool = False
    ai_behavior: Optional[str] = None
    allow_voice_calls: bool = False


class WhatsappPhoneRegistrationRequest(BaseModel):
    username: str
    client_id: str
    name: str
    phone_number: str
    send_ml_question_suggestions: bool = True
    send_weekly_report: bool = False
    send_monthly_report: bool = False
    welcome_message: Optional[str] = None
    send_welcome_message: bool = False
    allow_voice_calls: bool = False


class WhatsappVoiceToggleRequest(BaseModel):
    confirmed: bool = False


class WhatsappAdhocMessageRequest(BaseModel):
    phone_number: str
    message: str


__all__ = [
    "QuestionResearchPending",
    "_QuestionResearchPending",
    "WhatsappAdhocMessageRequest",
    "WhatsappBindingRevokeRequest",
    "WhatsappBridgeConfigRequest",
    "WhatsappPairingCodeRequest",
    "WhatsappPhoneRegistrationRequest",
    "WhatsappPhoneSettingsRequest",
    "WhatsappTemplatesRequest",
    "WhatsappVoiceToggleRequest",
]
