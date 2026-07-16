"""Compatibility facade for the isolated Vendas module."""

from __future__ import annotations

from backend.core import AppPaths
from backend.modules.vendas import (
    LegacyBlingVendasAdapter,
    VendasModuleDependencies,
    create_vendas_module,
    get_default_vendas_module,
    install_default_vendas_module,
)
from backend.modules.vendas.performance import _vendas_preparar_bancos_background, invalidate_vendas_cache, vendas_cache_stats
from backend.modules.vendas.sync_persistence import (
    _vendas_sync_dias_periodo, _vendas_sync_job_key, _vendas_sync_load_state, _vendas_sync_parse_date,
    _vendas_sync_prepare_job, _vendas_sync_state_path, _vendas_sync_update_job,
)
from backend.schemas import VendasQuery, VendasSyncRequest
from backend.services.vendas_grafico import grafico_vendas, skus_sem_venda
from backend.services.vendas_listagem import limites_vendas, limpar_todos_bancos_vendas, listar_vendas, listar_vendas_todas, resumo_vendas
from backend.services.vendas_notas import listar_itens_devolucoes, listar_notas_entrada, listar_notas_entrada_por_sku
from backend.services.vendas_sync import (
    _corrigir_texto_mojibake, _criar_progresso, _limpar_progresso, _set_progresso,
    _sincronizar_vendas_impl, _sincronizar_vendas_periodo_impl, _sincronizar_vendas_thread_worker,
    _sync_active_jobs, _sync_active_jobs_unlocked, _sync_build_aggregate_progress, _sync_context_key,
    _sync_context_loja, _sync_log, _sync_register_active_unlocked, _sync_unregister_active,
    _verificar_cancelamento, cancelar_sincronizacao_vendas, progresso_sincronizacao_vendas,
    sincronizar_vendas,
)
from backend.services.vendas_unidades import atualizar_unidade_negocio, listar_unidades_negocios, salvar_mapeamento_unidades


def configure_vendas_context(*, get_tenant_path=None, sync_state_lock=None): return None


def configure_vendas_runtime(runtime_module=None):
    if runtime_module is None:
        return None
    try:
        get_default_vendas_module()
        return runtime_module
    except RuntimeError:
        paths = AppPaths.create(
            base_dir=runtime_module.BASE_DIR,
            info_dir=runtime_module.PASTA_INFO,
            logger=runtime_module.logger,
        )
        module = create_vendas_module(VendasModuleDependencies(
            get_tenant_id=runtime_module.get_tenant_id,
            paths=paths,
            logger=runtime_module.logger,
            legacy=LegacyBlingVendasAdapter(),
        ))
        install_default_vendas_module(module)
        return runtime_module


__all__ = [
    "configure_vendas_context", "configure_vendas_runtime", "_vendas_preparar_bancos_background",
    "invalidate_vendas_cache", "vendas_cache_stats", "VendasQuery", "VendasSyncRequest",
    "_vendas_sync_parse_date", "_vendas_sync_dias_periodo", "_vendas_sync_state_path",
    "_vendas_sync_load_state", "_vendas_sync_job_key", "_vendas_sync_prepare_job", "_vendas_sync_update_job",
    "resumo_vendas", "listar_vendas", "limpar_todos_bancos_vendas", "listar_vendas_todas",
    "grafico_vendas", "skus_sem_venda", "limites_vendas", "listar_notas_entrada",
    "listar_itens_devolucoes", "listar_notas_entrada_por_sku", "listar_unidades_negocios",
    "atualizar_unidade_negocio", "salvar_mapeamento_unidades", "_corrigir_texto_mojibake",
    "_criar_progresso", "_sync_context_key", "_sync_context_loja", "_sync_active_jobs_unlocked",
    "_sync_active_jobs", "_sync_register_active_unlocked", "_sync_unregister_active",
    "_sync_build_aggregate_progress", "_set_progresso", "_limpar_progresso", "_sync_log",
    "_verificar_cancelamento", "cancelar_sincronizacao_vendas", "progresso_sincronizacao_vendas",
    "_sincronizar_vendas_thread_worker", "sincronizar_vendas", "_sincronizar_vendas_impl",
    "_sincronizar_vendas_periodo_impl",
]
