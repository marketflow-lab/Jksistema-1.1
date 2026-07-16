"""Explicit runtime dependencies for the isolated Vendas module."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from backend.core import AppPaths
from backend.services import bling_vendas, estoque_historico, integracoes


_ALLOWED_BLING_HELPERS = frozenset(
    {
        "_bling_refresh_token",
        "_bling_marcar_oauth_invalido",
        "_bling_renovar_token_loja",
        "_bling_executar_com_refresh",
        "_carregar_mapeamento_unidades",
        "_salvar_mapeamento_unidades",
        "_normalizar_nome_loja_virtual_candidato",
        "_carregar_mapeamento_lojas_virtuais_cliente",
        "_resolver_nome_loja_virtual",
        "_normalizar_unidade_negocio_ml",
        "_normalizar_unidade_devolucao_entrada",
        "_eh_devolucao_nota_entrada",
        "_eh_devolucao_por_cfop_itens",
        "_tipo_devolucao_cfop_full_estoque",
        "_classificar_unidade_virtual_devolucao",
        "_sql_filtro_unidade_devolucao",
        "_sql_filtro_loja_notas_entrada",
        "_bling_obter_numero_nf",
        "_sql_filtro_unidade_com_mapa",
        "_sql_filtro_loja_vendas",
        "_get_vendas_db_path",
        "_listar_bancos_vendas_tenant",
        "_deduplicar_vendas_consolidadas",
        "_deve_excluir_venda_ebazar",
        "_get_vendas_db",
        "_bling_listar_vendas",
        "_bling_listar_naturezas",
        "_normalizar_texto",
        "_bling_obter_detalhes_nf",
        "_extrair_codigo_origem_nf",
        "_bling_listar_notas_entrada",
        "_bling_listar_vendas_fallback_nf_saida",
        "_get_notas_entrada_db",
    }
)


@dataclass(frozen=True)
class LegacyBlingVendasAdapter:
    """Narrow adapter over shared legacy Bling/integration helpers."""

    def configure(self, paths: AppPaths, logger: logging.Logger) -> None:
        bling_vendas.configure_bling_vendas_context(
            pasta_info=str(paths.info_dir),
            get_tenant_path=paths.tenant_path,
            logger_instance=logger,
        )

    def call(self, helper_name: str, *args, **kwargs):
        if helper_name not in _ALLOWED_BLING_HELPERS:
            raise AttributeError(f"Helper de Vendas nao permitido: {helper_name}")
        return getattr(bling_vendas, helper_name)(*args, **kwargs)

    def buscar_loja(self, client_id: str, nome_loja: str):
        return integracoes.buscar_loja(client_id, nome_loja)

    def atualizar_api_loja(self, client_id: str, nome_loja: str, api_nome: str, dados_api: dict):
        return integracoes.atualizar_api_loja(client_id, nome_loja, api_nome, dados_api)

    def normalizar_sku_estoque(self, value: Any) -> str:
        return estoque_historico._normalizar_sku_estoque(value)

    def vendas_series_estoque_historico(self, *args, **kwargs):
        return estoque_historico._vendas_series_estoque_historico(*args, **kwargs)


@dataclass(frozen=True)
class VendasModuleDependencies:
    get_tenant_id: Callable[..., Any]
    paths: AppPaths
    logger: logging.Logger
    legacy: LegacyBlingVendasAdapter
    max_active_sync: int = 2


_dependencies: VendasModuleDependencies | None = None


class _DependencyLogger:
    def __getattr__(self, name: str):
        return getattr(get_vendas_dependencies().logger, name)


logger = _DependencyLogger()


def install_vendas_dependencies(dependencies: VendasModuleDependencies) -> None:
    global _dependencies
    if int(dependencies.max_active_sync) != 2:
        raise ValueError("O limite de sincronizacoes de Vendas deve permanecer em 2.")
    dependencies.legacy.configure(dependencies.paths, dependencies.logger)
    _dependencies = dependencies


def get_vendas_dependencies() -> VendasModuleDependencies:
    if _dependencies is None:
        raise RuntimeError("O modulo Vendas ainda nao foi configurado.")
    return _dependencies


def get_tenant_path(client_id: str) -> str:
    return get_vendas_dependencies().paths.tenant_path(client_id)


def migrate_legacy_file(client_id: str, filename: str, legacy_path: str | None) -> str:
    return get_vendas_dependencies().paths.migrate_legacy_file(client_id, filename, legacy_path)


def legacy_call(helper_name: str, *args, **kwargs):
    return get_vendas_dependencies().legacy.call(helper_name, *args, **kwargs)


__all__ = [
    "LegacyBlingVendasAdapter",
    "VendasModuleDependencies",
    "install_vendas_dependencies",
    "get_vendas_dependencies",
    "get_tenant_path",
    "migrate_legacy_file",
    "legacy_call",
    "logger",
]
