"""HTTP endpoint handlers for Sala de Reuniao."""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Optional

from fastapi import Depends, Header, HTTPException

from backend.schemas import (
    SalaReuniaoCriarSalaRequest,
    SalaReuniaoUsoAdicionarRequest,
    SalaReuniaoEncerrarLocalRequest,
    SalaReuniaoEncerrarTodasRequest,
)
from backend.services import sala_reuniao_context as sala_context
from backend.services.sala_reuniao_daily import *
from backend.services.sala_reuniao_store import *
from backend.services.sala_reuniao_core import *


async def sala_reuniao_status(_client_id: str = Depends(sala_context.get_tenant_id)):
    """Informa se a chave Daily esta disponivel para criacao automatica de salas."""
    domain = str(os.getenv("DAILY_DOMAIN") or os.getenv("JK_DAILY_DOMAIN") or "").strip()
    return {
        "success": True,
        "daily_configurado": bool(_sala_reuniao_daily_api_key()),
        "daily_domain": domain,
        "recursos": {
            "salas": True,
            "token_host": True,
            "compartilhar_tela": True,
            "chat": True,
            "gravacao": False,
            "transcricao": False,
            "live_streaming": False,
            "legendas": False,
            "sala_espera": True,
            "salas_grupo": True,
            "dialout": False,
            "limite_manual_participantes": False,
            "chamadas_grandes": False,
            "cancelamento_ruido": False,
        },
        "recursos_pagos_removidos": [
            "gravacao_cloud",
            "transcricao",
            "live_rtmp",
            "dialout",
            "limite_manual_participantes",
            "chamadas_grandes",
            "cancelamento_ruido",
        ],
    }


async def sala_reuniao_criar_sala(req: SalaReuniaoCriarSalaRequest, _client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Cria uma sala Daily Prebuilt quando DAILY_API_KEY ou JK_DAILY_API_KEY esta configurada."""
    sessao = _sala_reuniao_sessao(authorization, _client_id)
    api_key = _sala_reuniao_daily_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="Configure DAILY_API_KEY ou JK_DAILY_API_KEY para criar salas automaticamente.")
    uso = _sala_reuniao_resumo_uso(_client_id)
    if uso.get("blocked"):
        usado = float(uso.get("participant_minutes") or 0)
        bloqueio = int(uso.get("block_participant_minutes") or SALA_REUNIAO_BLOCK_PARTICIPANT_MINUTES)
        reset = uso.get("resets_at") or "dia 1"
        raise HTTPException(
            status_code=429,
            detail=(
                f"Uso mensal da Sala de Reuniao atingiu {usado:.1f} de {bloqueio} participant-minutes. "
                f"O modulo fica bloqueado ate o proximo reset em {reset}."
            ),
        )

    privacidade = _sala_reuniao_privacidade(req.privacidade)
    idioma = _sala_reuniao_idioma(req.idioma)
    try:
        expira_em = int(req.expira_em_minutos) if req.expira_em_minutos is not None else 0
    except Exception:
        expira_em = 0
    exp_timestamp = 0
    if expira_em > 0:
        expira_em = max(15, min(expira_em, 480))
        exp_timestamp = int(time.time()) + (expira_em * 60)
    nome_sala = _sala_reuniao_daily_room_name(req.nome)
    properties: dict[str, Any] = {
        "enable_prejoin_ui": _sala_reuniao_bool(req.habilitar_prejoin, True),
        "enable_knocking": _sala_reuniao_bool(req.habilitar_sala_espera, False),
        "enable_screenshare": _sala_reuniao_bool(req.habilitar_compartilhar_tela, True),
        "enable_chat": _sala_reuniao_bool(req.habilitar_chat, True),
        "enable_shared_chat_history": _sala_reuniao_bool(req.habilitar_historico_chat, True),
        "enable_advanced_chat": _sala_reuniao_bool(req.habilitar_chat_avancado, True),
        "enable_people_ui": _sala_reuniao_bool(req.habilitar_pessoas, True),
        "enable_hand_raising": _sala_reuniao_bool(req.habilitar_mao_levantada, True),
        "enable_emoji_reactions": _sala_reuniao_bool(req.habilitar_reacoes, True),
        "enable_pip_ui": _sala_reuniao_bool(req.habilitar_pip, True),
        "enable_network_ui": _sala_reuniao_bool(req.habilitar_rede, True),
        "enable_live_captions_ui": False,
        "enable_noise_cancellation_ui": False,
        "enable_video_processing_ui": _sala_reuniao_bool(req.habilitar_fundo_virtual, True),
        "enable_breakout_rooms": _sala_reuniao_bool(req.habilitar_salas_grupo, False),
        "enable_cpu_warning_notifications": _sala_reuniao_bool(req.habilitar_alerta_cpu, True),
        "enable_hidden_participants": False,
        "experimental_optimize_large_calls": False,
        "enable_adaptive_simulcast": False,
        "enable_multiparty_adaptive_simulcast": False,
        "enforce_unique_user_ids": _sala_reuniao_bool(req.exigir_user_id_unico, False),
        "enable_terse_logging": False,
        "enable_dialout": False,
        "eject_at_room_exp": _sala_reuniao_bool(req.ejetar_na_expiracao, False),
        "lang": idioma,
        "start_audio_off": _sala_reuniao_bool(req.iniciar_audio_desligado, True),
        "start_video_off": _sala_reuniao_bool(req.iniciar_video_desligado, True),
    }
    if exp_timestamp > 0:
        properties["exp"] = exp_timestamp
    if req.duracao_maxima_minutos:
        duracao = max(5, min(int(req.duracao_maxima_minutos), 480))
        properties["eject_after_elapsed"] = duracao * 60

    payload = {
        "name": nome_sala,
        "privacy": privacidade,
        "properties": properties,
    }

    data = _sala_reuniao_daily_post("/rooms", api_key, payload)
    host_token = None
    token_data: dict[str, Any] = {}
    if _sala_reuniao_bool(req.criar_token_host, True):
        token_data = _sala_reuniao_criar_token_host(api_key, data.get("name") or nome_sala, req, exp_timestamp, idioma)
        host_token = token_data.get("token")
    room_url = data.get("url")
    _sala_reuniao_registrar_sala(_client_id, {
        "name": data.get("name") or nome_sala,
        "url": room_url,
        "privacy": data.get("privacy") or privacidade,
    }, host_token, exp_timestamp, sessao.get("username") or "")
    return {
        "success": True,
        "room": {
            "name": data.get("name") or nome_sala,
            "url": room_url,
            "host_url": _sala_reuniao_url_com_token(room_url, host_token),
            "privacy": data.get("privacy") or privacidade,
            "expires_at": datetime.utcfromtimestamp(exp_timestamp).isoformat() + "Z" if exp_timestamp > 0 else "",
            "config": data.get("config") or data.get("properties") or {},
            "requested_config": properties,
        },
        "host_token": host_token,
        "token": token_data,
    }


async def sala_reuniao_reunioes_ativas(_client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Lista salas abertas e sessoes em andamento para a tela inicial do modulo."""
    sessao = _sala_reuniao_sessao(authorization, _client_id)
    data = _sala_reuniao_listar_reunioes_ativas(_client_id, sessao)
    return {
        "success": True,
        "daily_configurado": bool(_sala_reuniao_daily_api_key()),
        "is_admin": bool(sessao.get("is_admin")),
        "rooms": data.get("rooms") or [],
        "warning": data.get("warning") or "",
    }


async def sala_reuniao_salas_ativas_alias(_client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Alias de compatibilidade para listar reunioes ativas."""
    return await sala_reuniao_reunioes_ativas(_client_id, authorization)


async def sala_reuniao_salas_listar(_client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Alias GET para listar salas ativas sem conflitar com o POST de criacao."""
    return await sala_reuniao_reunioes_ativas(_client_id, authorization)


async def sala_reuniao_encerrar_local(req: SalaReuniaoEncerrarLocalRequest, _client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Oculta localmente uma sala que acabou de ser encerrada no navegador."""
    sessao = _sala_reuniao_sessao(authorization, _client_id)
    _sala_reuniao_exigir_permissao_encerrar(_client_id, req, sessao)
    data = _sala_reuniao_marcar_sala_encerrada(_client_id, req, sessao)
    return {
        "success": True,
        "rooms": data.get("rooms") or [],
        "warning": data.get("warning") or "",
    }


async def sala_reuniao_encerrar(req: SalaReuniaoEncerrarLocalRequest, _client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Encerra uma sala Daily quando possivel e remove da lista local de reunioes ativas."""
    sessao = _sala_reuniao_sessao(authorization, _client_id)
    data = _sala_reuniao_encerrar_sala(_client_id, req, sessao)
    return {
        "success": True,
        "rooms": data.get("rooms") or [],
        "warning": data.get("warning") or "",
        "daily_encerrada": bool(data.get("daily_encerrada")),
    }


async def sala_reuniao_encerrar_todas(req: SalaReuniaoEncerrarTodasRequest, _client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Encerra todas as salas que aparecem na lista de reunioes ativas."""
    sessao = _sala_reuniao_sessao(authorization, _client_id)
    if not sessao.get("is_admin"):
        raise HTTPException(status_code=403, detail="Apenas administradores podem encerrar todas as reunioes.")
    try:
        suppress_seconds = int(req.suppress_seconds or 1800)
    except Exception:
        suppress_seconds = 1800
    suppress_seconds = max(300, min(suppress_seconds, 7200))
    atuais = _sala_reuniao_listar_reunioes_ativas(_client_id, sessao).get("rooms") or []
    warnings: list[str] = []
    encerradas = 0
    daily_encerradas = 0
    data = {"rooms": atuais, "warning": ""}
    for room in atuais:
        if not isinstance(room, dict):
            continue
        encerradas += 1
        result = _sala_reuniao_encerrar_sala(_client_id, SalaReuniaoEncerrarLocalRequest(
            room_name=room.get("name") or room.get("room_name") or "",
            room_url=room.get("url") or "",
            participant_count=room.get("participants_count") or 0,
            suppress_seconds=suppress_seconds,
        ), sessao)
        data = result
        if result.get("daily_encerrada"):
            daily_encerradas += 1
        if result.get("warning"):
            warnings.append(str(result.get("warning")))
    warning = "; ".join(dict.fromkeys([w for w in warnings if w]))
    return {
        "success": True,
        "rooms": data.get("rooms") or [],
        "warning": warning,
        "encerradas": encerradas,
        "daily_encerradas": daily_encerradas,
    }


async def sala_reuniao_encerrar_local_alias(req: SalaReuniaoEncerrarLocalRequest, _client_id: str = Depends(sala_context.get_tenant_id), authorization: Optional[str] = Header(default=None)):
    """Alias de compatibilidade para marcar uma sala como encerrada localmente."""
    return await sala_reuniao_encerrar_local(req, _client_id, authorization)


async def sala_reuniao_uso_mensal(_client_id: str = Depends(sala_context.get_tenant_id)):
    """Retorna o uso estimado em participant-minutes da sala de reuniao no mes atual."""
    return {
        "success": True,
        "usage": _sala_reuniao_resumo_uso(_client_id),
    }


async def sala_reuniao_uso_mensal_adicionar(req: SalaReuniaoUsoAdicionarRequest, _client_id: str = Depends(sala_context.get_tenant_id)):
    """Acumula participant-seconds estimados pela interface durante uma chamada."""
    return {
        "success": True,
        "usage": _sala_reuniao_adicionar_uso(_client_id, req),
    }


async def sala_reuniao_gravacoes(room_name: str = "", limit: int = 20, _client_id: str = Depends(sala_context.get_tenant_id)):
    """Lista gravacoes cloud armazenadas no Daily para esta conta."""
    api_key = _sala_reuniao_daily_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="Configure DAILY_API_KEY ou JK_DAILY_API_KEY para consultar gravacoes.")
    params: dict[str, Any] = {"limit": max(1, min(int(limit or 20), 100))}
    room_name = str(room_name or "").strip()
    if room_name:
        params["room_name"] = room_name
    data = _sala_reuniao_daily_get("/recordings", api_key, params)
    return {
        "success": True,
        "total_count": data.get("total_count", 0),
        "recordings": data.get("data", []),
        "raw": data,
    }


async def sala_reuniao_transcricoes(room_id: str = "", mtg_session_id: str = "", limit: int = 20, _client_id: str = Depends(sala_context.get_tenant_id)):
    """Lista transcricoes geradas pelo Daily."""
    api_key = _sala_reuniao_daily_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="Configure DAILY_API_KEY ou JK_DAILY_API_KEY para consultar transcricoes.")
    params: dict[str, Any] = {"limit": max(1, min(int(limit or 20), 100))}
    room_id = str(room_id or "").strip()
    mtg_session_id = str(mtg_session_id or "").strip()
    if room_id:
        params["roomId"] = room_id
    if mtg_session_id:
        params["mtgSessionId"] = mtg_session_id
    data = _sala_reuniao_daily_get("/transcript", api_key, params)
    return {
        "success": True,
        "total_count": data.get("total_count", 0),
        "transcripts": data.get("data", []),
        "raw": data,
    }


SALA_REUNIAO_ENDPOINTS = ('sala_reuniao_status', 'sala_reuniao_criar_sala', 'sala_reuniao_reunioes_ativas', 'sala_reuniao_salas_ativas_alias', 'sala_reuniao_salas_listar', 'sala_reuniao_encerrar_local', 'sala_reuniao_encerrar', 'sala_reuniao_encerrar_todas', 'sala_reuniao_encerrar_local_alias', 'sala_reuniao_uso_mensal', 'sala_reuniao_uso_mensal_adicionar', 'sala_reuniao_gravacoes', 'sala_reuniao_transcricoes')

__all__ = ['sala_reuniao_status', 'sala_reuniao_criar_sala', 'sala_reuniao_reunioes_ativas', 'sala_reuniao_salas_ativas_alias', 'sala_reuniao_salas_listar', 'sala_reuniao_encerrar_local', 'sala_reuniao_encerrar', 'sala_reuniao_encerrar_todas', 'sala_reuniao_encerrar_local_alias', 'sala_reuniao_uso_mensal', 'sala_reuniao_uso_mensal_adicionar', 'sala_reuniao_gravacoes', 'sala_reuniao_transcricoes', 'SALA_REUNIAO_ENDPOINTS']
