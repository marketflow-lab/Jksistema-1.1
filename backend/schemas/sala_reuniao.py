"""Pydantic schemas for sala reuniao."""

from typing import Any, Optional

from pydantic import BaseModel


class SalaReuniaoCriarSalaRequest(BaseModel):
    nome: str | None = None
    privacidade: str | None = "public"
    expira_em_minutos: int | None = None
    duracao_maxima_minutos: int | None = None
    limitar_participantes: bool | None = False
    max_participantes: int | None = 12
    idioma: str | None = "pt-BR"
    iniciar_audio_desligado: bool | None = True
    iniciar_video_desligado: bool | None = True
    habilitar_prejoin: bool | None = True
    habilitar_sala_espera: bool | None = False
    habilitar_compartilhar_tela: bool | None = True
    habilitar_chat: bool | None = True
    habilitar_historico_chat: bool | None = True
    habilitar_chat_avancado: bool | None = True
    habilitar_pessoas: bool | None = True
    habilitar_mao_levantada: bool | None = True
    habilitar_reacoes: bool | None = True
    habilitar_rede: bool | None = True
    habilitar_pip: bool | None = True
    habilitar_legendas: bool | None = True
    habilitar_cancelamento_ruido: bool | None = True
    habilitar_fundo_virtual: bool | None = True
    habilitar_salas_grupo: bool | None = False
    habilitar_alerta_cpu: bool | None = True
    habilitar_participantes_ocultos: bool | None = False
    habilitar_chamadas_grandes: bool | None = False
    habilitar_simulcast_adaptativo: bool | None = False
    exigir_user_id_unico: bool | None = False
    habilitar_log_reduzido: bool | None = False
    habilitar_dialout: bool | None = False
    ejetar_na_expiracao: bool | None = False
    modo_gravacao: str | None = ""
    criar_token_host: bool | None = True
    nome_host: str | None = "Anfitriao JK Sistema"
    auto_iniciar_gravacao: bool | None = False
    auto_iniciar_transcricao: bool | None = False


class SalaReuniaoUsoAdicionarRequest(BaseModel):
    room_name: str | None = None
    room_url: str | None = None
    participant_seconds: float | None = 0
    participant_count: int | None = None
    reason: str | None = None


class SalaReuniaoEncerrarLocalRequest(BaseModel):
    room_name: str | None = None
    room_url: str | None = None
    participant_count: int | None = 1
    suppress_seconds: int | None = 300


class SalaReuniaoEncerrarTodasRequest(BaseModel):
    suppress_seconds: int | None = 1800


__all__ = [
    "SalaReuniaoCriarSalaRequest",
    "SalaReuniaoUsoAdicionarRequest",
    "SalaReuniaoEncerrarLocalRequest",
    "SalaReuniaoEncerrarTodasRequest",
]
