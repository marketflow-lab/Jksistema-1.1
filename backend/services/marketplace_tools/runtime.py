"""Explicit runtime adapters for marketplace AI tools."""

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
class StoreAdapters:
    load: Adapter
    find: Adapter
    ml_connected: Adapter
    ml_config: Adapter


@dataclass(frozen=True)
class MercadoLivreAdapters:
    request: Adapter
    fetch_items_batch: Adapter
    favorites_items_by_sku: Adapter
    parse_error: Adapter
    item_photo: Adapter


@dataclass(frozen=True)
class ProductAdapters:
    normalize_text: Adapter
    get_data: Adapter
    get_registry_info: Adapter
    resolve_sku: Adapter
    extract_item_ids: Adapter
    extract_reference: Adapter


@dataclass(frozen=True)
class ImageAdapters:
    request_detected: Adapter
    provider_active: Adapter
    openai_api_key: Adapter
    extract_openai_b64: Adapter


@dataclass(frozen=True)
class FileAdapters:
    tenant_path: Adapter


@dataclass(frozen=True)
class MarketplaceToolsRuntime:
    stores: StoreAdapters
    mercado_livre: MercadoLivreAdapters
    products: ProductAdapters
    images: ImageAdapters
    files: FileAdapters


def _default_runtime() -> MarketplaceToolsRuntime:
    return MarketplaceToolsRuntime(
        stores=StoreAdapters(
            load=_lazy("backend.services.integracoes", "carregar_lojas"),
            find=_lazy("backend.services.integracoes", "buscar_loja"),
            ml_connected=_lazy("backend.services.sales_tools.api", "connected_ml_stores"),
            ml_config=_lazy("backend.services.mercadolivre_legacy_api", "_obter_cfg_ml"),
        ),
        mercado_livre=MercadoLivreAdapters(
            request=_lazy("backend.services.mercadolivre_legacy_api", "_ml_api_request"),
            fetch_items_batch=_lazy("backend.services.mercadolivre_legacy_items", "_ml_buscar_itens_batch"),
            favorites_items_by_sku=_lazy("backend.services.favoritos_ml", "_ml_favoritos_buscar_itens_por_sku"),
            parse_error=_lazy("backend.services.mercadolivre_legacy_api", "_ml_parse_error_detail"),
            item_photo=_lazy("backend.services.perguntas_pos_venda_perguntas_ml", "_ml_perguntas_foto_item"),
        ),
        products=ProductAdapters(
            normalize_text=_lazy("backend.services.ia_common", "_normalizar_texto"),
            get_data=_lazy("backend.services.ia_tools_produtos", "_ia_tool_get_product_data"),
            get_registry_info=_lazy("backend.services.ia_tools_produtos", "_ia_tool_get_product_registry_info"),
            resolve_sku=_lazy("backend.services.sales_tools.api", "resolve_sku"),
            extract_item_ids=_lazy("backend.services.sales_tools.api", "extract_ml_item_ids"),
            extract_reference=_lazy("backend.services.ia_tools_produtos", "_ia_extrair_referencia_produto_mensagem"),
        ),
        images=ImageAdapters(
            request_detected=_lazy("backend.services.ia_common", "_ia_chat_pede_geracao_imagem"),
            provider_active=_lazy("backend.services.ia_providers", "_ia_provedor_ativo"),
            openai_api_key=_lazy("backend.services.ia_providers", "_obter_openai_api_key"),
            extract_openai_b64=_lazy("backend.services.ia_common", "_extrair_b64_openai_image_response"),
        ),
        files=FileAdapters(
            tenant_path=_lazy("backend.services.ia_context", "get_tenant_path"),
        ),
    )


_ADAPTER_FIELDS = {
    "carregar_lojas": ("stores", "load"),
    "buscar_loja": ("stores", "find"),
    "_ia_lojas_ml_conectadas": ("stores", "ml_connected"),
    "_obter_cfg_ml": ("stores", "ml_config"),
    "_ml_api_request": ("mercado_livre", "request"),
    "_ml_buscar_itens_batch": ("mercado_livre", "fetch_items_batch"),
    "_ml_favoritos_buscar_itens_por_sku": ("mercado_livre", "favorites_items_by_sku"),
    "_ml_parse_error_detail": ("mercado_livre", "parse_error"),
    "_ml_perguntas_foto_item": ("mercado_livre", "item_photo"),
    "_normalizar_texto": ("products", "normalize_text"),
    "_ia_tool_get_product_data": ("products", "get_data"),
    "_ia_tool_get_product_registry_info": ("products", "get_registry_info"),
    "_ia_tool_resolver_sku": ("products", "resolve_sku"),
    "_ia_extrair_item_ids_ml": ("products", "extract_item_ids"),
    "_ia_extrair_referencia_produto_mensagem": ("products", "extract_reference"),
    "_ia_chat_pede_geracao_imagem": ("images", "request_detected"),
    "_ia_provedor_ativo": ("images", "provider_active"),
    "_obter_openai_api_key": ("images", "openai_api_key"),
    "_extrair_b64_openai_image_response": ("images", "extract_openai_b64"),
    "get_tenant_path": ("files", "tenant_path"),
}

_LOCK = RLock()
_RUNTIME = _default_runtime()


def configure(runtime_module: ModuleType | MarketplaceToolsRuntime | None = None, peers: Mapping[str, object] | None = None) -> MarketplaceToolsRuntime:
    """Configure only allowlisted adapters and remain idempotent."""
    global _RUNTIME
    if isinstance(runtime_module, MarketplaceToolsRuntime):
        with _LOCK:
            _RUNTIME = runtime_module
            return _RUNTIME
    sources: dict[str, object] = {}
    if runtime_module is not None:
        sources.update(vars(runtime_module))
    if peers:
        sources.update(dict(peers))
    with _LOCK:
        current = _RUNTIME
        groups = {"stores": current.stores, "mercado_livre": current.mercado_livre,
                  "products": current.products, "images": current.images, "files": current.files}
        for legacy_name, (group_name, field_name) in _ADAPTER_FIELDS.items():
            candidate = sources.get(legacy_name)
            if callable(candidate):
                groups[group_name] = replace(groups[group_name], **{field_name: candidate})
        updated = MarketplaceToolsRuntime(**groups)
        if updated != current:
            _RUNTIME = updated
        return _RUNTIME


def current() -> MarketplaceToolsRuntime:
    with _LOCK:
        return _RUNTIME


def load_stores(*args, **kwargs): return current().stores.load(*args, **kwargs)
def find_store(*args, **kwargs): return current().stores.find(*args, **kwargs)
def ml_connected_stores(*args, **kwargs): return current().stores.ml_connected(*args, **kwargs)
def ml_config(*args, **kwargs): return current().stores.ml_config(*args, **kwargs)
def ml_api_request(*args, **kwargs): return current().mercado_livre.request(*args, **kwargs)
def ml_fetch_items_batch(*args, **kwargs): return current().mercado_livre.fetch_items_batch(*args, **kwargs)
def ml_favorites_items_by_sku(*args, **kwargs): return current().mercado_livre.favorites_items_by_sku(*args, **kwargs)
def ml_error_detail(*args, **kwargs): return current().mercado_livre.parse_error(*args, **kwargs)
def ml_question_item_photo(*args, **kwargs): return current().mercado_livre.item_photo(*args, **kwargs)
def normalize_text(*args, **kwargs): return current().products.normalize_text(*args, **kwargs)
def get_product_data(*args, **kwargs): return current().products.get_data(*args, **kwargs)
def get_product_registry_info(*args, **kwargs): return current().products.get_registry_info(*args, **kwargs)
def resolve_sku(*args, **kwargs): return current().products.resolve_sku(*args, **kwargs)
def extract_item_ids(*args, **kwargs): return current().products.extract_item_ids(*args, **kwargs)
def extract_product_reference(*args, **kwargs): return current().products.extract_reference(*args, **kwargs)
def chat_requests_image_generation(*args, **kwargs): return current().images.request_detected(*args, **kwargs)
def active_provider(*args, **kwargs): return current().images.provider_active(*args, **kwargs)
def openai_api_key(*args, **kwargs): return current().images.openai_api_key(*args, **kwargs)
def extract_openai_image_b64(*args, **kwargs): return current().images.extract_openai_b64(*args, **kwargs)
def tenant_path(*args, **kwargs): return current().files.tenant_path(*args, **kwargs)


__all__ = ["MarketplaceToolsRuntime", "StoreAdapters", "MercadoLivreAdapters", "ProductAdapters",
           "ImageAdapters", "FileAdapters", "configure", "current"]
