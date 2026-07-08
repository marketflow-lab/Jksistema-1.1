"""Compatibility facade for the Sala de Reuniao module."""

from __future__ import annotations

from backend.schemas import (
    SalaReuniaoCriarSalaRequest,
    SalaReuniaoUsoAdicionarRequest,
    SalaReuniaoEncerrarLocalRequest,
    SalaReuniaoEncerrarTodasRequest,
)
from backend.services.sala_reuniao_context import configure_sala_reuniao_context
from backend.services.sala_reuniao_daily import *
from backend.services.sala_reuniao_store import *
from backend.services.sala_reuniao_core import *
from backend.services.sala_reuniao_api import *


def configure_sala_reuniao_runtime(runtime_module=None):
    return configure_sala_reuniao_context(runtime_module)


configure_sala_reuniao_runtime()

__all__ = ['configure_sala_reuniao_runtime', 'SalaReuniaoCriarSalaRequest', 'SalaReuniaoUsoAdicionarRequest', 'SalaReuniaoEncerrarLocalRequest', 'SalaReuniaoEncerrarTodasRequest', 'DAILY_API_BASE_URL', 'DAILY_RECORDING_MODES', 'DAILY_LANGS', 'SALA_REUNIAO_FREE_PARTICIPANT_MINUTES', 'SALA_REUNIAO_BLOCK_PARTICIPANT_MINUTES', '_sala_reuniao_daily_api_key', '_sala_reuniao_bool', '_sala_reuniao_privacidade', '_sala_reuniao_idioma', '_sala_reuniao_modo_gravacao', '_sala_reuniao_daily_headers', '_sala_reuniao_daily_room_name', '_sala_reuniao_daily_error', '_sala_reuniao_daily_get', '_sala_reuniao_daily_post', '_sala_reuniao_daily_delete', '_sala_reuniao_url_com_token', '_sala_reuniao_username', '_sala_reuniao_sessao', '_sala_reuniao_uso_path', '_sala_reuniao_mes_atual', '_sala_reuniao_proximo_reset', '_sala_reuniao_carregar_uso', '_sala_reuniao_salvar_uso', '_sala_reuniao_resumo_uso', '_sala_reuniao_adicionar_uso', '_sala_reuniao_salas_path', '_sala_reuniao_carregar_salas', '_sala_reuniao_salvar_salas', '_sala_reuniao_domain_host', '_sala_reuniao_room_url', '_sala_reuniao_room_name_from_url', '_sala_reuniao_room_keys', '_sala_reuniao_iso_from_timestamp', '_sala_reuniao_participant_count', '_sala_reuniao_participant_names', '_sala_reuniao_iso_timestamp', '_sala_reuniao_local_pending_seconds', '_sala_reuniao_registrar_sala', '_sala_reuniao_encontrar_sala_local', '_sala_reuniao_usuario_pode_encerrar', '_sala_reuniao_exigir_permissao_encerrar', '_sala_reuniao_marcar_sala_encerrada', '_sala_reuniao_encerrar_sala', '_sala_reuniao_listar_reunioes_ativas', '_sala_reuniao_criar_token_host', 'sala_reuniao_status', 'sala_reuniao_criar_sala', 'sala_reuniao_reunioes_ativas', 'sala_reuniao_salas_ativas_alias', 'sala_reuniao_salas_listar', 'sala_reuniao_encerrar_local', 'sala_reuniao_encerrar', 'sala_reuniao_encerrar_todas', 'sala_reuniao_encerrar_local_alias', 'sala_reuniao_uso_mensal', 'sala_reuniao_uso_mensal_adicionar', 'sala_reuniao_gravacoes', 'sala_reuniao_transcricoes', 'sala_reuniao_rustdesk_status', 'sala_reuniao_rustdesk_abrir', 'sala_reuniao_rustdesk_acoplar', 'sala_reuniao_rustdesk_ocultar', 'SALA_REUNIAO_ENDPOINTS']
