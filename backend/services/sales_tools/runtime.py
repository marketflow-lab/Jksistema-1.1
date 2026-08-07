"""Explicit, allowlisted runtime adapters for sales and inventory tools."""

from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import import_module
from threading import RLock
from types import ModuleType
from typing import Any, Callable, Mapping


Adapter = Callable[..., Any]


def _lazy(module_name: str, attribute: str) -> Adapter:
    def invoke(*args, **kwargs):
        module = import_module(module_name)
        return getattr(module, attribute)(*args, **kwargs)

    invoke.__name__ = attribute.lstrip("_") or "adapter"
    return invoke


@dataclass(frozen=True)
class DateAdapters:
    normalize_period: Adapter
    normalize_date: Adapter
    parse_date: Adapter
    month_period: Adapter
    subtract_months: Adapter
    last_day_of_month: Adapter


@dataclass(frozen=True)
class DatabaseAdapters:
    list_sales_databases: Adapter
    sales_store_filter: Adapter
    returns_store_filter: Adapter


@dataclass(frozen=True)
class ProductAdapters:
    load_dataframe: Adapter
    get_data: Adapter
    get_info_by_sku: Adapter
    extract_reference: Adapter
    read_store_costs: Adapter
    sku_variants: Adapter


@dataclass(frozen=True)
class StoreAdapters:
    load: Adapter
    tenant_path: Adapter


@dataclass(frozen=True)
class TextAdapters:
    normalize: Adapter


@dataclass(frozen=True)
class SalesToolsRuntime:
    dates: DateAdapters
    databases: DatabaseAdapters
    products: ProductAdapters
    stores: StoreAdapters
    text: TextAdapters


def _default_runtime() -> SalesToolsRuntime:
    return SalesToolsRuntime(
        dates=DateAdapters(
            normalize_period=_lazy("backend.services.ia_common", "_ia_normalizar_periodo_chat"),
            normalize_date=_lazy("backend.services.ia_common", "_ia_normalizar_data_iso_chat"),
            parse_date=_lazy("backend.services.ia_common", "_ia_parse_data_iso_flex"),
            month_period=_lazy("backend.services.ia_common", "_ia_periodo_do_mes"),
            subtract_months=_lazy("backend.services.ia_common", "_ia_subtrair_meses"),
            last_day_of_month=_lazy("backend.services.ia_common", "_ia_ultimo_dia_mes"),
        ),
        databases=DatabaseAdapters(
            list_sales_databases=_lazy("backend.services.bling_vendas", "_listar_bancos_vendas_tenant"),
            sales_store_filter=_lazy("backend.services.bling_vendas", "_sql_filtro_loja_vendas"),
            returns_store_filter=_lazy("backend.services.bling_vendas", "_sql_filtro_loja_notas_entrada"),
        ),
        products=ProductAdapters(
            load_dataframe=_lazy("backend.services.ia_tools_produtos", "_ia_carregar_produtos_tool_df"),
            get_data=_lazy("backend.services.ia_tools_produtos", "_ia_tool_get_product_data"),
            get_info_by_sku=_lazy("backend.services.ia_tools_produtos", "_ia_produtos_info_por_sku"),
            extract_reference=_lazy(
                "backend.services.ia_tools_produtos",
                "_ia_extrair_referencia_produto_mensagem",
            ),
            read_store_costs=_lazy("backend.services.cadastro_custos", "_cadastro_ler_custos_lojas"),
            sku_variants=_lazy("backend.services.cadastro_common", "_sku_lookup_variantes"),
        ),
        stores=StoreAdapters(
            load=_lazy("backend.services.integracoes", "carregar_lojas"),
            tenant_path=_lazy("backend.services.ia_context", "get_tenant_path"),
        ),
        text=TextAdapters(
            normalize=_lazy("backend.services.ia_common", "_normalizar_texto"),
        ),
    )


_ADAPTER_FIELDS = {
    "_ia_normalizar_periodo_chat": ("dates", "normalize_period"),
    "_ia_normalizar_data_iso_chat": ("dates", "normalize_date"),
    "_ia_parse_data_iso_flex": ("dates", "parse_date"),
    "_ia_periodo_do_mes": ("dates", "month_period"),
    "_ia_subtrair_meses": ("dates", "subtract_months"),
    "_ia_ultimo_dia_mes": ("dates", "last_day_of_month"),
    "_listar_bancos_vendas_tenant": ("databases", "list_sales_databases"),
    "_sql_filtro_loja_vendas": ("databases", "sales_store_filter"),
    "_sql_filtro_loja_notas_entrada": ("databases", "returns_store_filter"),
    "_ia_carregar_produtos_tool_df": ("products", "load_dataframe"),
    "_ia_tool_get_product_data": ("products", "get_data"),
    "_ia_produtos_info_por_sku": ("products", "get_info_by_sku"),
    "_ia_extrair_referencia_produto_mensagem": ("products", "extract_reference"),
    "_cadastro_ler_custos_lojas": ("products", "read_store_costs"),
    "_sku_lookup_variantes": ("products", "sku_variants"),
    "carregar_lojas": ("stores", "load"),
    "get_tenant_path": ("stores", "tenant_path"),
    "_normalizar_texto": ("text", "normalize"),
}

_LOCK = RLock()
_RUNTIME = _default_runtime()


def configure(
    runtime_module: ModuleType | SalesToolsRuntime | None = None,
    peers: Mapping[str, object] | None = None,
) -> SalesToolsRuntime:
    """Apply only known adapters while remaining idempotent and thread-safe."""

    global _RUNTIME
    if isinstance(runtime_module, SalesToolsRuntime):
        with _LOCK:
            _RUNTIME = runtime_module
            return _RUNTIME

    sources: dict[str, object] = {}
    if runtime_module is not None:
        sources.update(vars(runtime_module))
    if peers:
        sources.update(dict(peers))

    with _LOCK:
        current_runtime = _RUNTIME
        groups = {
            "dates": current_runtime.dates,
            "databases": current_runtime.databases,
            "products": current_runtime.products,
            "stores": current_runtime.stores,
            "text": current_runtime.text,
        }
        for legacy_name, (group_name, field_name) in _ADAPTER_FIELDS.items():
            candidate = sources.get(legacy_name)
            if callable(candidate):
                groups[group_name] = replace(
                    groups[group_name],
                    **{field_name: candidate},
                )
        updated = SalesToolsRuntime(**groups)
        if updated != current_runtime:
            _RUNTIME = updated
        return _RUNTIME


def current() -> SalesToolsRuntime:
    with _LOCK:
        return _RUNTIME


def _ia_normalizar_periodo_chat(*args, **kwargs):
    return current().dates.normalize_period(*args, **kwargs)


def _ia_normalizar_data_iso_chat(*args, **kwargs):
    return current().dates.normalize_date(*args, **kwargs)


def _ia_parse_data_iso_flex(*args, **kwargs):
    return current().dates.parse_date(*args, **kwargs)


def _ia_periodo_do_mes(*args, **kwargs):
    return current().dates.month_period(*args, **kwargs)


def _ia_subtrair_meses(*args, **kwargs):
    return current().dates.subtract_months(*args, **kwargs)


def _ia_ultimo_dia_mes(*args, **kwargs):
    return current().dates.last_day_of_month(*args, **kwargs)


def _listar_bancos_vendas_tenant(*args, **kwargs):
    return current().databases.list_sales_databases(*args, **kwargs)


def _sql_filtro_loja_vendas(*args, **kwargs):
    return current().databases.sales_store_filter(*args, **kwargs)


def _sql_filtro_loja_notas_entrada(*args, **kwargs):
    return current().databases.returns_store_filter(*args, **kwargs)


def _ia_carregar_produtos_tool_df(*args, **kwargs):
    return current().products.load_dataframe(*args, **kwargs)


def _ia_tool_get_product_data(*args, **kwargs):
    return current().products.get_data(*args, **kwargs)


def _ia_produtos_info_por_sku(*args, **kwargs):
    return current().products.get_info_by_sku(*args, **kwargs)


def _ia_extrair_referencia_produto_mensagem(*args, **kwargs):
    return current().products.extract_reference(*args, **kwargs)


def _cadastro_ler_custos_lojas(*args, **kwargs):
    return current().products.read_store_costs(*args, **kwargs)


def _sku_lookup_variantes(*args, **kwargs):
    return current().products.sku_variants(*args, **kwargs)


def carregar_lojas(*args, **kwargs):
    return current().stores.load(*args, **kwargs)


def get_tenant_path(*args, **kwargs):
    return current().stores.tenant_path(*args, **kwargs)


def _normalizar_texto(*args, **kwargs):
    return current().text.normalize(*args, **kwargs)


__all__ = [
    "DatabaseAdapters",
    "DateAdapters",
    "ProductAdapters",
    "SalesToolsRuntime",
    "StoreAdapters",
    "TextAdapters",
    "configure",
    "current",
]
