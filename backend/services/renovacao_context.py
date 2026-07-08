"""Shared runtime context for the Renovacao service."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable


def _renovacao_missing_dependency(*_args, **_kwargs):
    raise RuntimeError("Renovacao service context was not configured")


@dataclass(frozen=True)
class RenovacaoServiceConfig:
    logger: Any
    pasta_info: str
    get_tenant_path: Callable[[str], str]
    agent_service_only: Callable[[], bool]
    obter_cfg_ml: Callable
    ml_api_request: Callable
    ml_parse_error_detail: Callable
    ml_extrair_contagem_campanha: Callable
    ml_contar_itens_promocao_status: Callable
    ml_extrair_preco_promocao_raw: Callable
    parse_float_flex: Callable
    ml_listar_ids_anuncios_ativos: Callable
    ml_obter_promocoes_item: Callable
    ml_extrair_ids_promocoes_item: Callable
    ml_encontrar_promocao_raw_item: Callable
    ml_listar_itens_promocao_com_raw: Callable
    ml_buscar_itens_batch: Callable
    ml_obter_item_promocao_raw: Callable
    cache_invalidar_loja: Callable


logger = logging.getLogger("jk_sistema")
PASTA_INFO = ""
_get_tenant_path: Callable[[str], str] = lambda client_id: os.path.join(PASTA_INFO, str(client_id or "default"))
_agent_service_only: Callable[[], bool] = lambda: False
_obter_cfg_ml = _renovacao_missing_dependency
_ml_api_request = _renovacao_missing_dependency
_ml_parse_error_detail = _renovacao_missing_dependency
_ml_extrair_contagem_campanha = _renovacao_missing_dependency
_ml_contar_itens_promocao_status = _renovacao_missing_dependency
_ml_extrair_preco_promocao_raw = _renovacao_missing_dependency
_parse_float_flex = _renovacao_missing_dependency
_ml_listar_ids_anuncios_ativos = _renovacao_missing_dependency
_ml_obter_promocoes_item = _renovacao_missing_dependency
_ml_extrair_ids_promocoes_item = _renovacao_missing_dependency
_ml_encontrar_promocao_raw_item = _renovacao_missing_dependency
_ml_listar_itens_promocao_com_raw = _renovacao_missing_dependency
_ml_buscar_itens_batch = _renovacao_missing_dependency
_ml_obter_item_promocao_raw = _renovacao_missing_dependency
_cache_invalidar_loja = _renovacao_missing_dependency

RENOVACAO_SYNC_JOBS: dict[str, dict] = {}
RENOVACAO_SYNC_JOBS_LOCK = threading.Lock()
RENOVACAO_AGENDAMENTO_LOCK = threading.Lock()
RENOVACAO_AGENDAMENTO_THREAD_STARTED = False


def get_tenant_path(client_id: str) -> str:
    return _get_tenant_path(client_id)


def configure_renovacao_context(config: RenovacaoServiceConfig) -> None:
    global logger, PASTA_INFO, _get_tenant_path, _agent_service_only
    global _obter_cfg_ml, _ml_api_request, _ml_parse_error_detail
    global _ml_extrair_contagem_campanha, _ml_contar_itens_promocao_status
    global _ml_extrair_preco_promocao_raw, _parse_float_flex
    global _ml_listar_ids_anuncios_ativos, _ml_obter_promocoes_item
    global _ml_extrair_ids_promocoes_item, _ml_encontrar_promocao_raw_item
    global _ml_listar_itens_promocao_com_raw, _ml_buscar_itens_batch
    global _ml_obter_item_promocao_raw, _cache_invalidar_loja

    logger = config.logger or logger
    PASTA_INFO = str(config.pasta_info or "")
    _get_tenant_path = config.get_tenant_path
    _agent_service_only = config.agent_service_only
    _obter_cfg_ml = config.obter_cfg_ml
    _ml_api_request = config.ml_api_request
    _ml_parse_error_detail = config.ml_parse_error_detail
    _ml_extrair_contagem_campanha = config.ml_extrair_contagem_campanha
    _ml_contar_itens_promocao_status = config.ml_contar_itens_promocao_status
    _ml_extrair_preco_promocao_raw = config.ml_extrair_preco_promocao_raw
    _parse_float_flex = config.parse_float_flex
    _ml_listar_ids_anuncios_ativos = config.ml_listar_ids_anuncios_ativos
    _ml_obter_promocoes_item = config.ml_obter_promocoes_item
    _ml_extrair_ids_promocoes_item = config.ml_extrair_ids_promocoes_item
    _ml_encontrar_promocao_raw_item = config.ml_encontrar_promocao_raw_item
    _ml_listar_itens_promocao_com_raw = config.ml_listar_itens_promocao_com_raw
    _ml_buscar_itens_batch = config.ml_buscar_itens_batch
    _ml_obter_item_promocao_raw = config.ml_obter_item_promocao_raw
    _cache_invalidar_loja = config.cache_invalidar_loja
