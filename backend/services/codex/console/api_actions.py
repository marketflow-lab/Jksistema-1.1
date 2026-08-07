"""Codex console api actions component."""

from __future__ import annotations


import copy
import base64
import importlib.util
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import unicodedata
import uuid
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any, Optional

from fastapi import File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel

from backend.services import (
    codex_actions,
    codex_agent_runtime,
    codex_ai_telemetry,
    codex_assistant_storage,
    codex_capabilities,
    codex_evaluations,
    codex_mcp_rollout,
    codex_model_router,
    codex_operational_memory,
    codex_turn_context,
)
from .contracts import (
    CodexActionApprovalRequest,
    CodexActionProposalRequest,
    CodexActionRevisionRequest,
    CodexAgentGuidanceRequest,
    CodexAgentGuidanceSimulationRequest,
    CodexCapabilityResolveRequest,
)


def codex_actions_listar(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    _codex_require_full_admin(request, authorization)
    return codex_actions.list_actions()


def codex_program_functions(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    q: str = "",
    module: str = "",
    category: str = "",
    limit: int = 300,
):
    sessao = _codex_require_full_admin(request, authorization)
    try:
        limit_safe = max(1, min(int(limit or 300), 2000))
    except Exception:
        limit_safe = 300
    return codex_capabilities.list_capabilities(
        client_id=str(sessao.get("client_id") or "default"),
        query=str(q or ""),
        module=str(module or ""),
        category=str(category or ""),
        limit=limit_safe,
    )


def codex_capabilities_listar(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    q: str = "",
    module: str = "",
    category: str = "",
    limit: int = 300,
):
    sessao = _codex_require_full_admin(request, authorization)
    try:
        limit_safe = max(1, min(int(limit or 300), 2000))
    except Exception:
        limit_safe = 300
    return codex_capabilities.list_capabilities(
        client_id=str(sessao.get("client_id") or "default"),
        query=str(q or ""),
        module=str(module or ""),
        category=str(category or ""),
        limit=limit_safe,
    )


def codex_capabilities_modules(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_capabilities.list_modules(client_id=str(sessao.get("client_id") or "default"))


def codex_capabilities_resolver(
    payload: CodexCapabilityResolveRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_capabilities.resolve_capability(
        client_id=str(sessao.get("client_id") or "default"),
        message=str(payload.message or ""),
        capability_id=str(payload.capability_id or ""),
        module=str(payload.module or ""),
        category=str(payload.category or ""),
        params=payload.params if isinstance(payload.params, dict) else {},
        limit=payload.limit,
    )


def codex_actions_criar_proposta(
    payload: CodexActionProposalRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    message = str(payload.message or "").strip()
    if not message and not payload.action_id and not payload.capability_id:
        raise HTTPException(status_code=400, detail="Informe uma mensagem, action_id ou capability_id.")
    conversation_state = _codex_load_or_create_conversation_state(
        str(sessao.get("client_id") or "default"),
        str(sessao.get("username") or ""),
        channel="app",
    )
    return codex_actions.create_proposal(
        client_id=str(sessao.get("client_id") or "default"),
        username=str(sessao.get("username") or ""),
        message=message,
        action_id=str(payload.action_id or ""),
        capability_id=str(payload.capability_id or ""),
        params=payload.params if isinstance(payload.params, dict) else {},
        screen_context=payload.screen_context if isinstance(payload.screen_context, dict) else {},
        history=payload.history if isinstance(payload.history, list) else [],
        conversation_id=str(conversation_state.get("conversation_id") or ""),
        conversation_generation=int(conversation_state.get("generation") or 1),
    )


def codex_actions_listar_propostas(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    status: str = "awaiting_approval",
    limit: int = 100,
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_actions.list_proposals(
        client_id=str(sessao.get("client_id") or "default"),
        username=str(sessao.get("username") or ""),
        status=str(status or ""),
        limit=max(1, min(int(limit or 100), 500)),
    )


def codex_actions_aprovar_proposta(
    proposal_id: str,
    request: Request,
    payload: Optional[CodexActionApprovalRequest] = None,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_actions.approve_proposal(
        proposal_id,
        username=str(sessao.get("username") or ""),
        client_id=str(sessao.get("client_id") or "default"),
        authorization=authorization,
        source="app",
        proposal_version=payload.proposal_version if payload else None,
        proposal_hash=str(payload.proposal_hash or "") if payload else "",
    )


def codex_actions_revisar_proposta(
    proposal_id: str,
    payload: CodexActionRevisionRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_actions.revise_proposal(
        proposal_id,
        username=str(sessao.get("username") or ""),
        client_id=str(sessao.get("client_id") or "default"),
        params=payload.params if isinstance(payload.params, dict) else {},
        message=str(payload.message or ""),
        source="app",
    )


def codex_actions_rejeitar_proposta(
    proposal_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_actions.reject_proposal(
        proposal_id,
        username=str(sessao.get("username") or ""),
        client_id=str(sessao.get("client_id") or "default"),
        source="app",
    )


def codex_actions_obter_execucao(
    run_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_actions.get_run(run_id, client_id=str(sessao.get("client_id") or "default"))


def codex_actions_cancelar_execucao(
    run_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_actions.cancel_run(run_id, client_id=str(sessao.get("client_id") or "default"))


def codex_agent_settings_get(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    guidance = codex_assistant_storage.codex_assistant_agent_guidance_list(
        _codex_base_info_dir(),
        client_id,
        active_only=False,
        latest_only=True,
    )
    return {
        "success": True,
        "states": list(codex_agent_runtime.AGENT_STATES),
        "guidance_scope_types": sorted(codex_agent_runtime.GUIDANCE_SCOPE_TYPES),
        "guidance": guidance,
        "policies": {
            "read_only_autonomous": True,
            "mutations_require_confirmation": True,
            "generic_route_execution": False,
            "whatsapp_non_destructive_only": True,
            "sensitive_actions_app_only": True,
        },
    }


def codex_agent_guidance_put(
    payload: CodexAgentGuidanceRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    try:
        saved = codex_agent_runtime.save_guidance(
            _codex_base_info_dir(),
            str(sessao.get("client_id") or "default"),
            _codex_model_dump(payload),
            updated_by=str(sessao.get("username") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "guidance": saved}


def codex_agent_guidance_simulate(
    payload: CodexAgentGuidanceSimulationRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    context = _codex_model_dump(payload)
    resolved = codex_agent_runtime.resolve_guidance(
        _codex_base_info_dir(),
        str(sessao.get("client_id") or "default"),
        context=context,
    )
    return {
        "success": True,
        "context": context,
        "guidance_applied": resolved,
        "prompt_preview": codex_agent_runtime.guidance_prompt(resolved),
    }


def codex_agent_capability_coverage(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    _codex_require_full_admin(request, authorization)
    from backend.services.codex.assistant import catalog as assistant_catalog

    actions_payload = codex_actions.list_actions()
    tools = assistant_catalog.public_tools({"full": True})
    return codex_agent_runtime.capability_coverage(
        actions=list(actions_payload.get("actions") or []),
        data_tools=list(tools or []),
    )





def codex_agent_plan_get(
    plan_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    plan = codex_assistant_storage.codex_assistant_agent_plan_get(
        _codex_base_info_dir(),
        str(sessao.get("client_id") or "default"),
        plan_id,
    )
    if not isinstance(plan, dict) or str(plan.get("created_by") or "").strip().lower() != str(sessao.get("username") or "").strip().lower():
        raise HTTPException(status_code=404, detail="Plano do Black Jhon nao encontrado.")
    return {"success": True, "plan": plan}
__codex_dependencies__ = ['_codex_base_info_dir', '_codex_load_or_create_conversation_state', '_codex_model_dump', '_codex_require_authenticated', '_codex_require_full_admin']

__codex_exports__ = ['codex_actions_listar', 'codex_program_functions', 'codex_capabilities_listar', 'codex_capabilities_modules', 'codex_capabilities_resolver', 'codex_actions_criar_proposta', 'codex_actions_listar_propostas', 'codex_actions_aprovar_proposta', 'codex_actions_revisar_proposta', 'codex_actions_rejeitar_proposta', 'codex_actions_obter_execucao', 'codex_actions_cancelar_execucao', 'codex_agent_settings_get', 'codex_agent_guidance_put', 'codex_agent_guidance_simulate', 'codex_agent_capability_coverage', 'codex_agent_plan_get']
