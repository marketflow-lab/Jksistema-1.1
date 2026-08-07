"""Pydantic request contracts for the Codex console."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class CodexTaskRequest(BaseModel):
    prompt: str
    sandbox: str = "read_only"
    thread_id: Optional[str] = None
    conversation_id: Optional[str] = None
    cwd: Optional[str] = None
    model: Optional[str] = None
    approval_mode: Optional[str] = None
    reasoning_effort: Optional[str] = None
    speed: Optional[str] = None
    service_tier: Optional[str] = None
    goal: Optional[str] = None
    planning_mode: bool = False
    attachments: Optional[list[str]] = None
    reference_paths: Optional[list[str]] = None
    # Compatibilidade V1. Estes caminhos sao tratados apenas como referencias
    # de leitura e nunca ampliam o sandbox do assistente interno.
    paths: Optional[list[str]] = None
    screen_context: Optional[dict[str, Any]] = None
    history: Optional[list[dict[str, Any]]] = None
    request_id: Optional[str] = None




class CodexTaskSteerRequest(BaseModel):
    message: str
    request_id: Optional[str] = None

class CodexTaskApprovalRequest(BaseModel):
    paths: Optional[list[str]] = None
    screen_context: Optional[dict[str, Any]] = None

class CodexConversationResetRequest(BaseModel):
    confirm: bool = False

class CodexActionProposalRequest(BaseModel):
    message: str
    action_id: Optional[str] = None
    capability_id: Optional[str] = None
    params: Optional[dict[str, Any]] = None
    conversation_id: Optional[str] = None
    screen_context: Optional[dict[str, Any]] = None
    history: Optional[list[dict[str, Any]]] = None

class CodexActionApprovalRequest(BaseModel):
    proposal_version: Optional[int] = None
    proposal_hash: Optional[str] = None

class CodexActionRevisionRequest(BaseModel):
    params: Optional[dict[str, Any]] = None
    message: Optional[str] = None

class CodexAgentGuidanceRequest(BaseModel):
    guidance_id: Optional[str] = None
    scope_type: str = "global"
    scope_key: Optional[str] = None
    text: str = ""
    active: bool = True

class CodexAgentGuidanceSimulationRequest(BaseModel):
    module: Optional[str] = None
    store: Optional[str] = None
    supplier: Optional[str] = None
    sku: Optional[str] = None

class CodexCapabilityResolveRequest(BaseModel):
    message: str = ""
    capability_id: Optional[str] = None
    module: Optional[str] = None
    category: Optional[str] = None
    params: Optional[dict[str, Any]] = None
    limit: int = 8

class CodexEvaluationRunAPIRequest(BaseModel):
    models: list[str]
    repetitions: int = 1
    split: str = "holdout"
    case_ids: Optional[list[str]] = None
    purpose: str = "manual"



class CodexMCPRolloutRequest(BaseModel):
    mode: str = "off"
    allowed_tools: Optional[list[str]] = None
    baseline_p95_ms: float = 0.0
    observations: Optional[dict[str, Any]] = None



class CodexTaskFeedbackRequest(BaseModel):
    rating: int
    label: str = ""



__all__ = [
    "CodexTaskRequest",
    "CodexTaskSteerRequest",
    "CodexTaskApprovalRequest",
    "CodexTaskFeedbackRequest",
    "CodexEvaluationRunAPIRequest",
    "CodexMCPRolloutRequest",
    "CodexConversationResetRequest",
    "CodexActionProposalRequest",
    "CodexActionApprovalRequest",
    "CodexActionRevisionRequest",
    "CodexAgentGuidanceRequest",
    "CodexAgentGuidanceSimulationRequest",
    "CodexCapabilityResolveRequest",
]
