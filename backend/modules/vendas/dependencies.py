"""Explicit runtime dependencies for the isolated Vendas module."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from backend.core import AppPaths
from backend.services import bling_vendas, estoque_historico, integracoes

from .errors import VendasDomainError


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

_ALLOWED_INTEGRACOES_HELPERS = frozenset({"atualizar_api_loja", "buscar_loja"})
_ALLOWED_ESTOQUE_HELPERS = frozenset(
    {"_normalizar_sku_estoque", "_vendas_series_estoque_historico"}
)


def _is_fastapi_http_exception(exc: BaseException) -> bool:
    return any(
        cls.__name__ == "HTTPException" and cls.__module__.startswith("fastapi")
        for cls in type(exc).__mro__
    )


def _translate_fastapi_http_exception(exc: Exception) -> None:
    if not _is_fastapi_http_exception(exc):
        raise exc
    raise VendasDomainError(
        status_code=getattr(exc, "status_code", 500),
        detail=getattr(exc, "detail", str(exc)),
        headers=getattr(exc, "headers", None),
    ) from exc


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
        try:
            if helper_name in _ALLOWED_BLING_HELPERS:
                helper = getattr(bling_vendas, helper_name)
            elif helper_name in _ALLOWED_INTEGRACOES_HELPERS:
                helper = getattr(integracoes, helper_name)
            elif helper_name in _ALLOWED_ESTOQUE_HELPERS:
                helper = getattr(estoque_historico, helper_name)
            else:
                raise AttributeError(f"Helper de Vendas nao permitido: {helper_name}")
            return helper(*args, **kwargs)
        except Exception as exc:
            _translate_fastapi_http_exception(exc)

    def buscar_loja(self, client_id: str, nome_loja: str):
        return self.call("buscar_loja", client_id, nome_loja)

    def atualizar_api_loja(
        self,
        client_id: str,
        nome_loja: str,
        api_nome: str,
        dados_api: dict,
        *,
        store_id: str,
    ):
        return self.call(
            "atualizar_api_loja",
            client_id,
            nome_loja,
            api_nome,
            dados_api,
            store_id=store_id,
        )

    def normalizar_sku_estoque(self, value: Any) -> str:
        return self.call("_normalizar_sku_estoque", value)

    def vendas_series_estoque_historico(self, *args, **kwargs):
        return self.call("_vendas_series_estoque_historico", *args, **kwargs)


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
