"""Codex console api conversations component."""

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
    CodexConversationResetRequest,
    CodexTaskApprovalRequest,
    CodexTaskSteerRequest,
)


def codex_listar_conversas_whatsapp(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    limit: int = 50,
    offset: int = 0,
):
    sessao = _codex_require_authenticated(request, authorization)
    max_items = max(1, min(100, int(limit or 50)))
    page_offset = max(0, int(offset or 0))
    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "").strip().lower()
    grouped: dict[str, dict[str, Any]] = {}

    for record in _codex_whatsapp_history_records(sessao):
        task = record["task"]
        conversation_id = str(record.get("conversation_id") or "")
        phone = str(record.get("phone") or "")
        group = grouped.get(conversation_id)
        if group is None:
            state = _codex_load_conversation_summary(client_id, username, conversation_id)
            group = {
                "conversation_id": conversation_id,
                "channel": "whatsapp",
                "phone": phone,
                "phone_display": _codex_whatsapp_phone_display(phone),
                "generation": int(state.get("generation") or task.get("conversation_generation") or 1),
                "state": str(state.get("state") or "active"),
                "updated_at": str(record.get("activity_at") or ""),
                "exchange_count": 0,
                "active_count": 0,
                "failed_count": 0,
                "last_prompt_preview": "",
                "last_response_preview": "",
                "registered": False,
                "label": "",
                "last_inbound_at": "",
            }
            grouped[conversation_id] = group
        status = str(task.get("status") or "")
        if status in {"queued", "running", "awaiting_approval", "cancel_requested"}:
            group["active_count"] += 1
        elif status in {"failed", "cancelled"}:
            group["failed_count"] += 1
        if _codex_whatsapp_completed_exchange(task):
            group["exchange_count"] += 1
            if not group["last_prompt_preview"]:
                group["last_prompt_preview"] = str(task.get("prompt") or "").strip()[:180]
                group["last_response_preview"] = str(task.get("final_response") or "").strip()[:240]

    for binding in _codex_registered_whatsapp_bindings(sessao):
        conversation_id = str(binding.get("conversation_id") or "")
        if not conversation_id:
            continue
        registered_at = str(binding.get("registered_at") or "")
        last_inbound_at = str(binding.get("last_inbound_at") or "")
        activity_at = last_inbound_at or registered_at
        group = grouped.get(conversation_id)
        if group is None:
            phone = str(binding.get("phone") or "")
            state = _codex_load_conversation_summary(client_id, username, conversation_id)
            group = {
                "conversation_id": conversation_id,
                "channel": "whatsapp",
                "phone": phone,
                "phone_display": _codex_whatsapp_phone_display(phone),
                "generation": int(state.get("generation") or 1),
                "state": str(state.get("state") or "active"),
                "updated_at": activity_at,
                "exchange_count": 0,
                "active_count": 0,
                "failed_count": 0,
                "last_prompt_preview": "",
                "last_response_preview": "",
                "registered": True,
                "label": str(binding.get("label") or ""),
                "last_inbound_at": last_inbound_at,
            }
            grouped[conversation_id] = group
        else:
            # O numero do cadastro e preferido na apresentacao; a identidade
            # canonica ja reuniu a variante Meta com ou sem o nono digito.
            phone = str(binding.get("phone") or group.get("phone") or "")
            group["phone"] = phone
            group["phone_display"] = _codex_whatsapp_phone_display(phone)
            group["registered"] = True
            group["label"] = str(binding.get("label") or "")
            group["last_inbound_at"] = last_inbound_at
            if activity_at > str(group.get("updated_at") or ""):
                group["updated_at"] = activity_at

    conversations = sorted(
        grouped.values(),
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    )
    page = conversations[page_offset : page_offset + max_items]
    return {
        "success": True,
        "channel": "whatsapp",
        "conversations": page,
        "total": len(conversations),
        "limit": max_items,
        "offset": page_offset,
        "has_more": page_offset + len(page) < len(conversations),
    }


def codex_listar_mensagens_conversa_whatsapp(
    conversation_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
    limit: int = 20,
    offset: int = 0,
):
    sessao = _codex_require_authenticated(request, authorization)
    requested_id = _codex_safe_id(str(conversation_id or "").strip(), "")
    if not requested_id or not requested_id.startswith("wa_"):
        raise HTTPException(status_code=404, detail="Conversa do WhatsApp nao encontrada.")
    matching = [
        record
        for record in _codex_whatsapp_history_records(sessao)
        if str(record.get("conversation_id") or "") == requested_id
    ]
    registered = {
        str(item.get("conversation_id") or ""): item
        for item in _codex_registered_whatsapp_bindings(sessao)
    }
    binding = registered.get(requested_id)
    if not matching and not binding:
        raise HTTPException(status_code=404, detail="Conversa do WhatsApp nao encontrada para este usuario.")

    completed = [record for record in matching if _codex_whatsapp_completed_exchange(record["task"])]
    max_items = max(1, min(50, int(limit or 20)))
    page_offset = max(0, int(offset or 0))
    page = completed[page_offset : page_offset + max_items]
    phone = str((binding or {}).get("phone") or (matching[0].get("phone") if matching else "") or "")
    return {
        "success": True,
        "conversation": {
            "conversation_id": requested_id,
            "channel": "whatsapp",
            "phone": phone,
            "phone_display": _codex_whatsapp_phone_display(phone),
            "registered": bool(binding),
            "label": str((binding or {}).get("label") or ""),
            "last_inbound_at": str((binding or {}).get("last_inbound_at") or ""),
        },
        "messages": [_codex_whatsapp_history_message_preview(record["task"]) for record in page],
        "total": len(completed),
        "limit": max_items,
        "offset": page_offset,
        "has_more": page_offset + len(page) < len(completed),
    }


def codex_obter_mensagem_conversa_whatsapp(
    conversation_id: str,
    task_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    requested_id = _codex_safe_id(str(conversation_id or "").strip(), "")
    requested_task_id = str(task_id or "").strip()
    if not requested_id or not requested_task_id:
        raise HTTPException(status_code=404, detail="Mensagem do WhatsApp nao encontrada.")
    for record in _codex_whatsapp_history_records(sessao):
        task = record["task"]
        if str(record.get("conversation_id") or "") != requested_id:
            continue
        if str(task.get("task_id") or "") != requested_task_id:
            continue
        if not _codex_whatsapp_completed_exchange(task):
            raise HTTPException(status_code=409, detail="Esta conversa ainda nao possui uma resposta concluida.")
        return {
            "success": True,
            "message": {
                "task_id": requested_task_id,
                "created_at": str(task.get("created_at") or ""),
                "completed_at": str(task.get("completed_at") or ""),
                "prompt": str(task.get("prompt") or "").strip(),
                "response": str(task.get("final_response") or "").strip(),
            },
        }
    raise HTTPException(status_code=404, detail="Mensagem do WhatsApp nao encontrada para este usuario.")


def codex_obter_tarefa(
    task_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    task = _codex_require_owned_task(task_id, sessao)
    return {"success": True, "task": _codex_public_task(task)}


def codex_complementar_tarefa_para_sessao(
    task_id: str,
    message: str,
    sessao: dict[str, Any],
    *,
    request_id: str = "",
    subject_id: str = "",
    wa_id: str = "",
) -> dict[str, Any]:
    task = _codex_require_owned_task(task_id, sessao)
    text = str(message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Informe o complemento da tarefa.")
    if len(text) > 12000:
        raise HTTPException(status_code=400, detail="O complemento excede 12 mil caracteres.")
    status = str(task.get("status") or "")
    if status not in {"queued", "running"}:
        return {"success": False, "accepted": False, "reason": "task_not_steerable", "task": _codex_public_task(task)}
    if task.get("mutable_intent") or task.get("proposal"):
        return {"success": False, "accepted": False, "reason": "proposal_or_mutation_locked", "task": _codex_public_task(task)}
    agent_state = str(task.get("agent_state") or "").strip().lower()
    if agent_state in {"aguardando_aprovacao", "executando", "verificando", "concluido", "parcial", "falhou", "cancelado"}:
        return {"success": False, "accepted": False, "reason": "agent_state_locked", "task": _codex_public_task(task)}
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    if str(task.get("origin") or "") == "whatsapp":
        expected_subject = str(metadata.get("subject_id") or "").strip()
        expected_phone = _codex_normalize_phone(metadata.get("wa_id"))
        supplied_phone = _codex_normalize_phone(wa_id)
        if expected_subject and expected_subject != str(subject_id or "").strip():
            raise HTTPException(status_code=404, detail="Tarefa Codex nao encontrada para este numero.")
        if expected_phone and expected_phone != supplied_phone:
            raise HTTPException(status_code=404, detail="Tarefa Codex nao encontrada para este telefone.")
    event_id = str(request_id or uuid.uuid4().hex).strip()[:200]
    events = [item for item in (task.get("steer_events") or []) if isinstance(item, dict)]
    if any(str(item.get("request_id") or "") == event_id for item in events):
        return {"success": True, "accepted": True, "idempotent_replay": True, "task": _codex_public_task(task)}
    event = {"request_id": event_id, "message_preview": text[:500], "created_at": _codex_now(), "mode": "queued"}
    if status == "queued":
        with CODEX_TASKS_LOCK:
            current = CODEX_TASKS.get(task_id) or task
            current["prompt"] = (str(current.get("prompt") or "").rstrip() + "\n\n[Complemento do usuario]\n" + text).strip()
            current["steer_events"] = (events + [event])[-20:]
            current["live_status"] = "Complemento incorporado antes do inicio da tarefa."
            _codex_persist_task(current)
        return {"success": True, "accepted": True, "mode": "queued_prompt", "task": _codex_public_task(current)}
    with CODEX_ACTIVE_TURNS_LOCK:
        turn = CODEX_ACTIVE_TURNS.get(task_id)
    if turn is None or not str(getattr(turn, "id", "") or "").strip():
        return {"success": False, "accepted": False, "reason": "active_turn_unavailable", "task": _codex_public_task(task)}
    try:
        turn.steer(text)
    except Exception as exc:
        _codex_log(task, f"Nao foi possivel incorporar o complemento no turno ativo: {exc}", "warning")
        return {"success": False, "accepted": False, "reason": "steer_failed", "error": str(exc)[:500], "task": _codex_public_task(task)}
    event["mode"] = "turn_steer"
    _codex_update_task(
        task_id,
        steer_events=(events + [event])[-20:],
        live_status="Complemento do usuario incorporado ao turno ativo.",
        wait_reason="codex_turn",
    )
    task = _codex_load_task(task_id) or task
    _codex_log(task, "Complemento autenticado incorporado ao turno ativo do Codex.")
    return {"success": True, "accepted": True, "mode": "turn_steer", "task": _codex_public_task(task)}


def codex_complementar_tarefa(
    task_id: str,
    payload: CodexTaskSteerRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    return codex_complementar_tarefa_para_sessao(
        task_id,
        payload.message,
        sessao,
        request_id=str(payload.request_id or ""),
    )


def codex_deletar_tarefa(
    task_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    task = _codex_require_owned_task(task_id, sessao)
    if str(task.get("status") or "") in {"queued", "running", "awaiting_approval", "cancel_requested"}:
        raise HTTPException(status_code=409, detail="Cancele ou aguarde a tarefa terminar antes de excluir.")
    public = _codex_public_task(task)
    with CODEX_TASKS_LOCK:
        CODEX_TASKS.pop(task_id, None)
    try:
        path = _codex_task_path(task_id)
        if os.path.exists(path):
            os.remove(path)
        _codex_delete_conversation_memory_if_unused(task)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Nao foi possivel excluir a conversa: {exc}") from exc
    return {"success": True, "deleted": True, "task": public}


def codex_deletar_conversa(
    conversation_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "").strip().lower()
    conv_id = _codex_safe_id(str(conversation_id or "").strip(), "")
    if not conv_id:
        raise HTTPException(status_code=400, detail="Informe a conversa para excluir.")
    try:
        state_paths = list(_codex_conversation_dir(client_id, username).glob("*.json"))
    except Exception:
        state_paths = []
    for state_path in state_paths:
        if state_path.name.startswith("_"):
            continue
        try:
            with state_path.open("r", encoding="utf-8") as fh:
                state_payload = json.load(fh)
        except Exception:
            continue
        if (
            isinstance(state_payload, dict)
            and str(state_payload.get("conversation_id") or "") == conv_id
            and str(state_payload.get("state") or "active") == "active"
            and int(state_payload.get("generation") or 0) >= 1
        ):
            raise HTTPException(
                status_code=409,
                detail="A conversa ativa do Black Jhon nao pode ser excluida. Use Reiniciar memoria.",
            )

    matches: list[dict[str, Any]] = []
    blocked: list[str] = []
    try:
        paths = list(Path(_codex_info_dir()).glob("*.json"))
    except Exception:
        paths = []

    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as fh:
                task = json.load(fh)
        except Exception:
            continue
        if not isinstance(task, dict):
            continue
        if str(task.get("client_id") or "default") != client_id:
            continue
        created_by = str(task.get("created_by") or "").strip().lower()
        if not created_by or created_by != username:
            continue
        task_id = str(task.get("task_id") or path.stem).strip()
        if conv_id not in _codex_task_conversation_keys(task):
            continue
        status = str(task.get("status") or "").strip()
        if status in {"queued", "running", "awaiting_approval", "cancel_requested"}:
            blocked.append(task_id)
            continue
        matches.append({"task": task, "path": path, "task_id": task_id})

    if blocked:
        raise HTTPException(
            status_code=409,
            detail="Cancele ou aguarde as tarefas em andamento antes de excluir a conversa.",
        )

    deleted_ids: list[str] = []
    with CODEX_TASKS_LOCK:
        for item in matches:
            task_id = str(item.get("task_id") or "").strip()
            if task_id:
                CODEX_TASKS.pop(task_id, None)
                deleted_ids.append(task_id)
    for item in matches:
        path = item.get("path")
        try:
            if isinstance(path, Path) and path.exists():
                path.unlink()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Nao foi possivel excluir a conversa: {exc}") from exc

    try:
        summary_path = _codex_conversation_path(client_id, username, conv_id)
        if summary_path.exists():
            summary_path.unlink()
    except Exception:
        pass
    _codex_mark_conversation_deleted(client_id, conv_id, username, deleted_ids)
    return {
        "success": True,
        "deleted": True,
        "conversation_id": conv_id,
        "deleted_count": len(deleted_ids),
        "task_ids": deleted_ids,
    }


def codex_reset_current_conversation(
    payload: CodexConversationResetRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    if payload.confirm is not True:
        raise HTTPException(status_code=400, detail="Confirme o reinicio da memoria do Black Jhon.")
    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "").strip().lower()
    shared_continuity = _codex_shared_continuity_for_session(sessao)
    with CODEX_CONVERSATION_LOCK:
        state = (
            _codex_load_or_create_shared_conversation_state(client_id, username)
            if shared_continuity
            else _codex_load_or_create_conversation_state(client_id, username, channel="app")
        )
        conversation_id = str(state.get("conversation_id") or "")
        generation = int(state.get("generation") or 1)
        active_statuses = {"queued", "running", "awaiting_approval", "cancel_requested"}
        busy = [
            task
            for task in _codex_owned_persisted_tasks(client_id, username)
            if _codex_task_stored_conversation_id(task) == conversation_id
            and int(task.get("conversation_generation") or 1) == generation
            and str(task.get("status") or "") in active_statuses
        ]
        if busy:
            raise HTTPException(
                status_code=409,
                detail="Aguarde ou cancele as tarefas ativas antes de reiniciar a memoria.",
            )
        audit = list(state.get("reset_audit") or [])
        audit.append(
            {
                "at": _codex_now(),
                "by": username,
                "archived_generation": generation,
            }
        )
        state.update(
            {
                "generation": generation + 1,
                "summary": "",
                "recent_messages": [],
                "legacy_conversation_ids": [],
                "latest_thread_id": "",
                "thread_prompt_fingerprint": "",
                "thread_schema_fingerprint": "",
                "thread_scope_fingerprint": "",
                "thread_conversation_key": "",
                "thread_restart_reason": "manual_reset",
                "compacted_until": "",
                "summary_updated_at": "",
                "estimated_tokens_before": 0,
                "estimated_tokens_after": 0,
                "reset_audit": audit[-50:],
                "updated_at": _codex_now(),
            }
        )
        _codex_save_conversation_summary(client_id, username, conversation_id, state)
        if shared_continuity:
            app_state = _codex_load_or_create_conversation_state(
                client_id,
                username,
                channel="app",
            )
            app_state.update(
                {
                    "generation": int(state.get("generation") or generation + 1),
                    "summary": "",
                    "recent_messages": [],
                    "latest_thread_id": "",
                    "thread_prompt_fingerprint": "",
                    "thread_schema_fingerprint": "",
                    "thread_scope_fingerprint": "",
                    "thread_conversation_key": "",
                    "thread_restart_reason": "manual_reset",
                    "updated_at": _codex_now(),
                }
            )
            _codex_save_conversation_summary(
                client_id,
                username,
                str(app_state.get("conversation_id") or ""),
                app_state,
            )
    if shared_continuity:
        try:
            from backend.services import whatsapp_bridge

            whatsapp_bridge._reset_shared_conversation_for_session(sessao)
        except Exception:
            pass
    return {
        "success": True,
        "conversation": {
            "conversation_id": conversation_id,
            "channel": "shared" if shared_continuity else "app",
            "generation": int(state.get("generation") or generation + 1),
            "state": "active",
            "can_reset": True,
            "queue": {"running": 0, "pending": 0},
        },
    }











def codex_aprovar_tarefa_para_sessao(task_id: str, sessao: dict[str, Any], payload: Optional[CodexTaskApprovalRequest]=None, *, approval_source: str='app', subject_id: str=''):
    if not bool(sessao.get('is_full')) or (sessao.get('permissions') or {}).get('full') is not True:
        raise HTTPException(status_code=403, detail='A aprovacao exige um usuario full ativo.')
    task = _codex_require_owned_task(task_id, sessao)
    requested_source = 'whatsapp' if str(approval_source or '').strip().lower() == 'whatsapp' else 'app'
    if requested_source == 'whatsapp':
        raise HTTPException(status_code=403, detail='Acoes operacionais preparadas pelo WhatsApp devem ser confirmadas no aplicativo JK Sistema.')
    task_proposal = task.get('proposal') if isinstance(task.get('proposal'), dict) else {}
    if task_proposal.get('proposal_id'):
        source = requested_source
        metadata = task.get('channel_metadata') if isinstance(task.get('channel_metadata'), dict) else {}
        if source == 'whatsapp':
            expected_subject = str(metadata.get('subject_id') or '').strip()
            if not expected_subject or expected_subject != str(subject_id or '').strip():
                raise HTTPException(status_code=404, detail='Tarefa Codex nao encontrada para este numero.')
        approved_action = codex_actions.approve_proposal(str(task_proposal.get('proposal_id') or ''), username=str(sessao.get('username') or ''), client_id=str(sessao.get('client_id') or 'default'), authorization=None, source=source, wa_id=str(metadata.get('wa_id') or ''), proposal_version=int(task_proposal.get('version') or 1), proposal_hash=str(task_proposal.get('proposal_hash') or ''))
        _codex_update_task(task_id, status='running', approved=True, approval_source=source, approved_at=_codex_now(), approved_by=str(sessao.get('username') or ''), proposal=approved_action.get('proposal') if isinstance(approved_action.get('proposal'), dict) else task_proposal, action_run=approved_action.get('run') if isinstance(approved_action.get('run'), dict) else {}, agent_state='executando', current_step='executar')
        return {'success': True, 'task': _codex_public_task(_codex_load_task(task_id) or task), **approved_action}
    if _codex_task_whatsapp_query_only(task):
        raise HTTPException(status_code=403, detail='Consultas de vendas e anuncios originadas do WhatsApp usam politica query_only e nao podem ser aprovadas para execucao mutavel.')
    if task.get('status') == 'awaiting_approval':
        raise HTTPException(status_code=409, detail=_codex_development_requires_desktop_detail())
    if task.get('status') != 'awaiting_approval':
        return {'success': True, 'task': _codex_public_task(task)}
    source = str(approval_source or 'app').strip().lower()
    if source not in {'app', 'whatsapp'}:
        raise HTTPException(status_code=400, detail='Origem de aprovacao invalida.')
    channel_metadata = task.get('channel_metadata') if isinstance(task.get('channel_metadata'), dict) else {}
    mobile_approval = bool(source == 'whatsapp' and str(task.get('origin') or '') == 'whatsapp' and (task.get('whatsapp_full_access') is True))
    if source == 'whatsapp' and (not mobile_approval):
        raise HTTPException(status_code=403, detail='Esta tarefa nao permite aprovacao pelo WhatsApp.')
    if mobile_approval:
        expected_subject = str(channel_metadata.get('subject_id') or '').strip()
        actual_subject = str(subject_id or '').strip()
        if not expected_subject or not actual_subject or expected_subject != actual_subject:
            raise HTTPException(status_code=404, detail='Tarefa Codex nao encontrada para este numero.')
        approval = payload or CodexTaskApprovalRequest()
        screen_context = _codex_normalizar_screen_context(approval.screen_context)
        scope = _codex_build_scope(prompt=str(task.get('prompt') or ''), sandbox='full_access', paths=[], screen_context=screen_context, cwd=str(task.get('cwd') or _codex_base_dir()))
        scope.update({'enforced': False, 'reason': 'whatsapp_full_user_approval', 'broad_request': True, 'approved_subject_fingerprint': hashlib.sha256(actual_subject.encode('utf-8')).hexdigest()[:16]})
        _codex_update_task(task_id, screen_context=screen_context or task.get('screen_context') or {}, scope=scope, sandbox='full_access', approval_mode='full_access')
    elif str(task.get('origin') or '') == 'whatsapp':
        approval = payload or CodexTaskApprovalRequest()
        screen_context = _codex_normalizar_screen_context(approval.screen_context)
        conversation_id = _codex_task_conversation_id(task)
        paths = _codex_resolver_paths_for_session(approval.paths, sessao, conversation_id, allow_external_for_admin=False)
        attachment_root = _codex_attachments_base_dir().resolve()
        scoped_paths: list[str] = []
        for path in paths:
            try:
                if os.path.commonpath([str(attachment_root), str(Path(path).resolve())]) == str(attachment_root):
                    continue
            except Exception:
                continue
            scoped_paths.append(path)
        modules = _codex_scope_modules_from_screen(screen_context)
        if not scoped_paths and (not modules):
            raise HTTPException(status_code=400, detail='Tarefa do WhatsApp exige modulo da tela ou caminho explicito antes da aprovacao.')
        scope = _codex_build_scope(prompt=str(task.get('prompt') or ''), sandbox='workspace_write', paths=scoped_paths, screen_context=screen_context, cwd=str(task.get('cwd') or _codex_base_dir()))
        if not scope.get('allowed_exact') and (not scope.get('allowed_prefixes')):
            raise HTTPException(status_code=400, detail='O escopo informado nao gerou nenhum caminho permitido.')
        scope.update({'enforced': True, 'reason': 'whatsapp_app_approval', 'broad_request': False})
        merged_paths = list(task.get('paths') or [])
        for path in scoped_paths:
            if path not in merged_paths:
                merged_paths.append(path)
        _codex_update_task(task_id, paths=merged_paths, screen_context=screen_context, scope=scope, sandbox='workspace_write', approval_mode='request')
    _codex_update_task(task_id, status='queued', approved=True, approval_source=source, approved_at=_codex_now(), approved_by=str(sessao.get('username') or ''))
    task = _codex_load_task(task_id) or task
    _codex_log(task, 'Execucao aprovada pelo WhatsApp vinculado.' if mobile_approval else 'Execucao aprovada pelo administrador.')
    _codex_start_thread(task_id)
    return {'success': True, 'task': _codex_public_task(task)}




def codex_aprovar_tarefa(
    task_id: str,
    request: Request,
    payload: Optional[CodexTaskApprovalRequest] = None,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_aprovar_tarefa_para_sessao(task_id, sessao, payload, approval_source="app")


def codex_cancelar_tarefa_para_sessao(
    task_id: str,
    sessao: dict[str, Any],
    *,
    cancel_source: str = "app",
    subject_id: str = "",
):
    task = _codex_require_owned_task(task_id, sessao)
    source = str(cancel_source or "app").strip().lower()
    if source == "whatsapp":
        metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
        expected_subject = str(metadata.get("subject_id") or "").strip()
        if task.get("whatsapp_full_access") is not True or not expected_subject or expected_subject != str(subject_id or "").strip():
            raise HTTPException(status_code=404, detail="Tarefa Codex nao encontrada para este numero.")
    if task.get("status") in {"completed", "partial", "failed", "canceled"}:
        return {"success": True, "task": _codex_public_task(task)}
    task_proposal = task.get("proposal") if isinstance(task.get("proposal"), dict) else {}
    if task_proposal.get("proposal_id") and task.get("status") == "awaiting_approval":
        codex_actions.reject_proposal(
            str(task_proposal.get("proposal_id") or ""),
            username=str(sessao.get("username") or ""),
            client_id=str(sessao.get("client_id") or "default"),
            source=source,
        )
    running = task.get("status") == "running"
    interrupted = _codex_interrupt_active_turn(task_id) if running else False
    status = "cancel_requested" if running else "canceled"
    _codex_update_task(
        task_id,
        status=status,
        completed_at=_codex_now(),
        cancel_source=source,
        interrupt_requested=bool(interrupted),
    )
    task = _codex_load_task(task_id) or task
    _codex_log(task, "Cancelamento solicitado pelo WhatsApp vinculado." if source == "whatsapp" else "Cancelamento solicitado.")
    return {"success": True, "task": _codex_public_task(task)}


def codex_cancelar_tarefa(
    task_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    return codex_cancelar_tarefa_para_sessao(task_id, sessao, cancel_source="app")
__codex_dependencies__ = ['CODEX_ACTIVE_TURNS', 'CODEX_ACTIVE_TURNS_LOCK', 'CODEX_CONVERSATION_LOCK', 'CODEX_TASKS', 'CODEX_TASKS_LOCK', '_codex_attachments_base_dir', '_codex_base_dir', '_codex_build_scope', '_codex_conversation_dir', '_codex_conversation_path', '_codex_delete_conversation_memory_if_unused', '_codex_development_requires_desktop_detail', '_codex_info_dir', '_codex_interrupt_active_turn', '_codex_load_conversation_summary', '_codex_load_or_create_conversation_state', '_codex_load_or_create_shared_conversation_state', '_codex_load_task', '_codex_log', '_codex_mark_conversation_deleted', '_codex_normalizar_screen_context', '_codex_normalize_phone', '_codex_now', '_codex_owned_persisted_tasks', '_codex_persist_task', '_codex_public_task', '_codex_registered_whatsapp_bindings', '_codex_require_authenticated', '_codex_require_full_admin', '_codex_require_owned_task', '_codex_resolver_paths_for_session', '_codex_safe_id', '_codex_save_conversation_summary', '_codex_scope_modules_from_screen', '_codex_shared_continuity_for_session', '_codex_start_thread', '_codex_task_conversation_id', '_codex_task_conversation_keys', '_codex_task_path', '_codex_task_stored_conversation_id', '_codex_task_whatsapp_query_only', '_codex_update_task', '_codex_whatsapp_completed_exchange', '_codex_whatsapp_history_message_preview', '_codex_whatsapp_history_records', '_codex_whatsapp_phone_display']

__codex_exports__ = ['codex_listar_conversas_whatsapp', 'codex_listar_mensagens_conversa_whatsapp', 'codex_obter_mensagem_conversa_whatsapp', 'codex_obter_tarefa', 'codex_complementar_tarefa_para_sessao', 'codex_complementar_tarefa', 'codex_deletar_tarefa', 'codex_deletar_conversa', 'codex_reset_current_conversation', 'codex_aprovar_tarefa_para_sessao', 'codex_aprovar_tarefa', 'codex_cancelar_tarefa_para_sessao', 'codex_cancelar_tarefa']
