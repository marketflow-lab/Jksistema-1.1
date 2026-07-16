"""Compatibility exports for isolated Vendas performance helpers."""

from backend.modules.vendas.performance import (
    VENDAS_CACHE_MAX_ENTRIES,
    VENDAS_CACHE_TTL_SECONDS,
    _vendas_preparar_bancos_background,
    append_indexable_date_filter,
    cache_vendas_response,
    cached_file_value,
    cached_table_columns,
    invalidate_vendas_cache,
    open_vendas_readonly,
    prepare_vendas_database,
    prepare_vendas_databases_in_root,
    select_compatible_column,
    vendas_cache_stats,
    vendas_file_signature,
    vendas_source_paths,
    vendas_sources_signature,
)

def configure_vendas_performance_runtime(runtime_module=None): return runtime_module

__all__ = [
    "VENDAS_CACHE_TTL_SECONDS", "VENDAS_CACHE_MAX_ENTRIES", "configure_vendas_performance_runtime",
    "cache_vendas_response", "invalidate_vendas_cache", "vendas_cache_stats", "vendas_file_signature",
    "vendas_sources_signature", "vendas_source_paths", "cached_file_value", "open_vendas_readonly",
    "cached_table_columns", "select_compatible_column", "append_indexable_date_filter",
    "prepare_vendas_database", "prepare_vendas_databases_in_root", "_vendas_preparar_bancos_background",
]
