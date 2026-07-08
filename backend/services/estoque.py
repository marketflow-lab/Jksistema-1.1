"""Compatibility facade for the Estoque module."""

from __future__ import annotations

from backend.schemas.estoque import (
    EstoqueLancamentosSyncLoteRequest,
    EstoqueLancamentosSyncRequest,
    EstoquePreferenciasColunasRequest,
    EstoqueSyncRequest,
)
from backend.services import estoque_context
from backend.services import estoque_common, estoque_historico, estoque_lancamentos, estoque_sync
from backend.services.estoque_common import *
from backend.services.estoque_historico import *
from backend.services.estoque_lancamentos import *
from backend.services.estoque_sync import *


def _wire_internal_dependencies() -> None:
    estoque_historico._garantir_tabela_lancamentos_estoque = estoque_lancamentos._garantir_tabela_lancamentos_estoque


def configure_estoque_runtime(runtime_module=None):
    runtime = estoque_context.configure_estoque_context(runtime_module)
    estoque_common.configure_estoque_common_runtime(runtime)
    estoque_historico.configure_estoque_historico_runtime(runtime)
    estoque_lancamentos.configure_estoque_lancamentos_runtime(runtime)
    estoque_sync.configure_estoque_sync_runtime(runtime)
    _wire_internal_dependencies()
    return runtime


configure_estoque_runtime()

__all__ = [
    "configure_estoque_runtime",
    "listar_estoque",
    "_set_estoque_progresso",
    "_set_estoque_lanc_progresso",
    "_estoque_lanc_log",
    "_arquivo_preferencias_colunas_estoque",
    "_arquivo_preferencias_colunas_promo",
    "_carregar_preferencias_colunas_estoque",
    "_salvar_preferencias_colunas_estoque",
    "_carregar_preferencias_colunas_promo",
    "_salvar_preferencias_colunas_promo",
    "_estoque_log",
    "progresso_sincronizacao_estoque",
    "api_estoque_preferencias_colunas_get",
    "api_estoque_preferencias_colunas_put",
    "_estoque_historico_db_path",
    "_garantir_tabela_historico_estoque",
    "_registrar_snapshot_historico_estoque",
    "_normalizar_sku_estoque",
    "_label_mes_estoque",
    "_chave_intervalo_estoque",
    "_inicio_periodo_estoque",
    "_vendas_series_estoque_historico",
    "_bling_listar_lotes_produto",
    "_bling_listar_lancamentos_lote",
    "_extrair_entradas_saidas_lancamento",
    "_garantir_tabela_lancamentos_estoque",
    "_resolver_id_bling_por_sku_snapshot",
    "_salvar_lancamentos_estoque",
    "_agrupar_lancamentos_cache",
    "_contar_lancamentos_cache_periodo",
    "_limpar_lancamentos_nf_periodo",
    "_salvar_movimentos_nf_estoque",
    "_sincronizar_lancamentos_estoque_sku_api",
    "sincronizar_lancamentos_estoque_api",
    "_sincronizar_lancamentos_estoque_lote_impl",
    "_sincronizar_lancamentos_estoque_lote_thread_worker",
    "sincronizar_lancamentos_estoque_lote_api",
    "progresso_sincronizacao_lancamentos_estoque",
    "estoque_serie_retroativa",
    "_estoque_verificar_cancelamento",
    "_sincronizar_estoque_thread_worker",
    "sincronizar_estoque",
    "_sincronizar_estoque_impl",
    "EstoqueSyncRequest",
    "EstoqueLancamentosSyncRequest",
    "EstoqueLancamentosSyncLoteRequest",
    "EstoquePreferenciasColunasRequest",
]
