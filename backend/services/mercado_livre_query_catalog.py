"""Versioned catalogue of Mercado Livre consultation resources.

The internal assistant selects only a stable ``resource_id``.  Hostnames,
seller identifiers, HTTP methods and path templates are never model supplied.
"""

from __future__ import annotations

import re
from typing import Any


CATALOG_VERSION = "2026-07-20.1"
LAST_VERIFIED_AT = "2026-07-20"

DOCS = {
    "users": "https://developers.mercadolivre.com.br/pt_br/servico-consulta-de-usuarios",
    "addresses": "https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/enderecos-do-usuario",
    "questions": "https://developers.mercadolivre.com.br/pt_br/busca-produtos-por-categoria/perguntas-e-respostas",
    "notifications": "https://developers.mercadolivre.com.br/pt_br/produto-receba-notificacoes",
    "categories": "https://developers.mercadolivre.com.br/pt_br/categorias-e-publicacoes",
    "attributes": "https://developers.mercadolivre.com.br/pt_br/api-docs-pt-br/atributos",
    "locations": "https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas",
    "items": "https://developers.mercadolivre.com.br/pt_br/itens-e-buscas",
    "products": "https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/buscador-de-produtos",
    "user_products": "https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/user-products",
    "prices": "https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/precos-liquidos",
    "listing_types": "https://developers.mercadolivre.com.br/pt_br/tutorial-tipos-de-publicacao-y-atualizacao-de-artigos",
    "price_refs": "https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/referencias-de-precos",
    "reputation": "https://developers.mercadolivre.com.br/pt_br/recuperacao-de-reputacao",
    "quality": "https://developers.mercadolivre.com.br/pt_br/como-comecar/qualidade-das-publicacoes",
    "visits": "https://developers.mercadolivre.com.br/pt_br/recurso-visits",
    "trends": "https://developers.mercadolivre.com.br/pt_br/relatorios-de-faturamento/tendencias",
    "highlights": "https://developers.mercadolivre.com.br/pt_br/escolha-tipo-de-servico/mais-vendidos-no-mercado-livre",
    "compat": "https://developers.mercadolivre.com.br/pt_br/publicacao-de-produtos/compatibilidades-itens-e-produtos-de-autopecas",
    "orders": "https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas",
    "billing_info": "https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas/faturamento-billing-info",
    "shipments": "https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-envios",
    "messages": "https://developers.mercadolivre.com.br/mensagens-post-venda",
    "unread": "https://developers.mercadolivre.com.br/pt_br/mensagens-pendentes",
    "claims": "https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-reclamacoes",
    "returns": "https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-devolucoes",
    "promotions": "https://developers.mercadolivre.com.br/pt_br/usuarios-e-aplicativos/gerenciar-ofertas",
    "fulfillment": "https://developers.mercadolivre.com.br/pt_br/localizacao-e-moedas/envios-fulfillment",
    "distributed_stock": "https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/estoque-distribuido",
    "billing": "https://developers.mercadolivre.com.br/pt_br/guia-para-imoveis/boas-praticas-para-o-consumo-das-apis-de-relatorios-de-faturamento",
    "invoices": "https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas/obtendo-nota-fiscal",
    "ads": "https://developers.mercadolivre.com.br/pt_br/product-ads-leitura",
}


def _resource(
    resource_id: str,
    domain: str,
    method: str,
    path: str,
    *,
    policy: str = "allowlisted",
    ownership: str = "public",
    params: str = "",
    fixed_params: dict[str, Any] | None = None,
    docs: str,
    delegated_tool: str = "",
    headers: dict[str, str] | None = None,
    response_headers: tuple[str, ...] = (),
    feature_gate: str = "",
    side_effect: str = "none",
    deprecated_at: str = "",
) -> dict[str, Any]:
    return {
        "resource_id": resource_id,
        "domain": domain,
        "method": method,
        "path_template": path,
        "required_path_params": re.findall(r"\{([a-z0-9_]+)\}", path),
        "policy": policy,
        "ownership_rule": ownership,
        "allowed_query_params": [value for value in params.split(",") if value],
        "fixed_query_params": dict(fixed_params or {}),
        "required_headers": dict(headers or {}),
        "response_headers": list(response_headers),
        "delegated_tool": delegated_tool,
        "feature_gate": feature_gate,
        "side_effect": side_effect,
        "deprecated_at": deprecated_at,
        "official_source": DOCS[docs],
        "last_verified_at": LAST_VERIFIED_AT,
        "read_only": True,
    }


_R = _resource
MERCADO_LIVRE_QUERY_RESOURCES: list[dict[str, Any]] = [
    # User, app and communications.
    _R("ml.users.me", "users", "GET", "/users/me", policy="allowlisted_redacted", ownership="seller", docs="users"),
    _R("ml.users.get", "users", "GET", "/users/{user_id}", policy="allowlisted_redacted", ownership="seller", docs="users"),
    _R("ml.users.addresses", "users", "GET", "/users/{user_id}/addresses", policy="document_only", ownership="seller", docs="addresses"),
    _R("ml.users.blocked_by_questions", "users", "GET", "/block-api/search/users/{buyer_id}", policy="document_only", ownership="token_scoped", fixed_params={"type": "blocked_by_questions"}, docs="questions"),
    _R("ml.users.blocked_by_order", "users", "GET", "/block-api/search/users/{buyer_id}", policy="document_only", ownership="token_scoped", fixed_params={"type": "blocked_by_order"}, docs="questions"),
    _R("ml.users.reputation_recovery_status", "users", "GET", "/users/reputation/seller_recovery/status", policy="allowlisted_redacted", ownership="seller", docs="reputation"),
    _R("ml.users.reputation_recovery_legal_document", "users", "GET", "/users/reputation/seller_recovery/legal-document", policy="document_only", ownership="seller", params="type", docs="reputation"),
    _R("ml.communications.notices", "communications", "GET", "/communications/notices", policy="allowlisted_redacted", ownership="token_scoped", params="limit,offset", docs="notifications"),
    _R("ml.notifications.missed_feeds", "notifications", "GET", "/missed_feeds", policy="document_only", ownership="token_scoped", params="app_id,topic,limit,offset", docs="notifications"),

    # Sites, categories, attributes, locations and currencies.
    _R("ml.sites.list", "catalog", "GET", "/sites", docs="categories"),
    _R("ml.sites.listing_types", "catalog", "GET", "/sites/{site_id}/listing_types", ownership="site", docs="categories"),
    _R("ml.sites.listing_exposures", "catalog", "GET", "/sites/{site_id}/listing_exposures", ownership="site", docs="categories"),
    _R("ml.categories.roots", "catalog", "GET", "/sites/{site_id}/categories", ownership="site", docs="categories"),
    _R("ml.categories.dump", "catalog", "GET", "/sites/{site_id}/categories/all", policy="document_only", ownership="site", docs="categories"),
    _R("ml.categories.get", "catalog", "GET", "/categories/{category_id}", docs="categories"),
    _R("ml.categories.predict", "catalog", "GET", "/sites/{site_id}/domain_discovery/search", ownership="site", params="q,limit,target", docs="categories"),
    _R("ml.categories.attributes", "catalog", "GET", "/categories/{category_id}/attributes", docs="attributes"),
    _R("ml.categories.techspec_input", "catalog", "GET", "/categories/{category_id}/technical_specs/input", docs="attributes"),
    _R("ml.categories.techspec_output", "catalog", "GET", "/categories/{category_id}/technical_specs/output", docs="attributes"),
    _R("ml.categories.conditional_attributes", "catalog", "POST", "/categories/{category_id}/attributes/conditional", policy="document_only", ownership="public", docs="attributes"),
    _R("ml.locations.countries", "locations", "GET", "/classified_locations/countries", docs="locations"),
    _R("ml.locations.country", "locations", "GET", "/classified_locations/countries/{country_id}", docs="locations"),
    _R("ml.locations.state", "locations", "GET", "/classified_locations/states/{state_id}", docs="locations"),
    _R("ml.locations.city", "locations", "GET", "/classified_locations/cities/{city_id}", docs="locations"),
    _R("ml.currencies.list", "locations", "GET", "/currencies", docs="locations"),
    _R("ml.currencies.get", "locations", "GET", "/currencies/{currency_id}", docs="locations"),
    _R("ml.currencies.convert", "locations", "GET", "/currency_conversions/search", params="from,to", docs="locations"),
    _R("ml.zip.get", "locations", "GET", "/countries/{country_id}/zip_codes/{zip_code}", docs="locations"),
    _R("ml.zip.range", "locations", "GET", "/country/{country_id}/zip_codes/search_between", params="zip_code_from,zip_code_to", docs="locations"),

    # Products, listings, prices, catalogue, moderation and quality.
    _R("ml.items.public_search", "items", "GET", "/sites/{site_id}/search", ownership="site", params="q,category,official_store_id,nickname,offset,limit,sort", docs="items"),
    _R("ml.items.seller_search", "items", "GET", "/users/{user_id}/items/search", ownership="seller", params="status,category,sku,seller_sku,tags,user_product_id,has_compatibilities,orders,offset,limit,search_type,scroll_id,include_filters,missing_product_identifiers,sort", docs="items"),
    _R("ml.items.seller_search_restrictions", "items", "GET", "/users/{user_id}/items/search/restrictions", policy="allowlisted_redacted", ownership="seller", docs="items"),
    _R("ml.items.multiget", "items", "GET", "/items", params="ids,attributes", docs="items"),
    _R("ml.items.get", "items", "GET", "/items/{item_id}", policy="delegated", ownership="owned_item", docs="items", delegated_tool="mercado_livre_listing"),
    _R("ml.items.description", "items", "GET", "/items/{item_id}/description", policy="delegated", ownership="owned_item", docs="items", delegated_tool="mercado_livre_listing"),
    _R("ml.items.prices", "pricing", "GET", "/items/{item_id}/prices", policy="allowlisted_redacted", ownership="owned_item", docs="prices"),
    _R("ml.items.sale_price", "pricing", "GET", "/items/{item_id}/sale_price", policy="allowlisted_redacted", ownership="owned_item", params="context,quantity,destination_states", docs="prices"),
    _R("ml.items.price_to_win", "pricing", "GET", "/items/{item_id}/price_to_win", policy="allowlisted_redacted", ownership="owned_item", fixed_params={"version": "v2"}, docs="prices"),
    _R("ml.items.listing_prices", "pricing", "GET", "/sites/{site_id}/listing_prices", ownership="site", params="price,category_id,listing_type_id,logistic_type,shipping_mode,free_shipping", docs="categories"),
    _R("ml.items.available_listing_types", "items", "GET", "/items/{item_id}/available_listing_types", ownership="owned_item", docs="listing_types"),
    _R("ml.items.available_upgrades", "items", "GET", "/items/{item_id}/available_upgrades", ownership="owned_item", docs="listing_types"),
    _R("ml.items.available_downgrades", "items", "GET", "/items/{item_id}/available_downgrades", ownership="owned_item", docs="listing_types"),
    _R("ml.users.available_listing_types", "items", "GET", "/users/{user_id}/available_listing_types", ownership="seller", params="category_id", docs="listing_types"),
    _R("ml.users.available_listing_type", "items", "GET", "/users/{user_id}/available_listing_type/{listing_type_id}", ownership="seller", params="category_id", docs="listing_types"),
    _R("ml.suggestions.user_items", "pricing", "GET", "/suggestions/user/{user_id}/items", policy="allowlisted_redacted", ownership="seller", params="limit,offset", docs="price_refs"),
    _R("ml.suggestions.item_details", "pricing", "GET", "/suggestions/items/{item_id}/details", policy="allowlisted_redacted", ownership="owned_item", docs="price_refs"),
    _R("ml.products.search", "catalog", "GET", "/products/search", params="site_id,q,domain_id,product_identifier,parent_product_id,status,offset,limit", docs="products"),
    _R("ml.products.get", "catalog", "GET", "/products/{product_id}", docs="products"),
    _R("ml.user_products.get", "user_products", "GET", "/user-products/{user_product_id}", policy="allowlisted_redacted", ownership="owned_user_product", docs="user_products"),
    _R("ml.user_products.family", "user_products", "GET", "/sites/{site_id}/user-products-families/{family_id}", policy="allowlisted_redacted", ownership="owned_family", docs="user_products"),
    _R("ml.user_products.uptin_validate", "user_products", "GET", "/items/{item_id}/user_product_listings/validate", policy="allowlisted_redacted", ownership="owned_item", docs="user_products"),
    _R("ml.reviews.item", "reviews", "GET", "/reviews/item/{item_id}", params="catalog_product_id,limit,offset", docs="products"),
    _R("ml.pictures.errors", "moderations", "GET", "/pictures/{picture_id}/errors", docs="items"),
    _R("ml.pictures.diagnostic", "moderations", "POST", "/moderations/pictures/diagnostic", policy="document_only", ownership="token_scoped", docs="items"),
    _R("ml.moderations.last", "moderations", "GET", "/moderations/last_moderation/{reference_id}", policy="document_only", ownership="token_scoped", docs="items"),
    _R("ml.moderations.infractions", "moderations", "GET", "/moderations/infractions/{user_id}", policy="allowlisted_redacted", ownership="seller", params="limit,offset,sort", docs="items"),
    _R("ml.quality.item_performance", "quality", "GET", "/item/{item_id}/performance", policy="allowlisted_redacted", ownership="owned_item", docs="quality"),
    _R("ml.quality.user_product_performance", "quality", "GET", "/user-product/{user_product_id}/performance", policy="allowlisted_redacted", ownership="owned_user_product", docs="quality"),

    # Visits and trends.
    _R("ml.visits.items_total", "metrics", "GET", "/visits/items", params="ids", docs="visits"),
    _R("ml.visits.items_period", "metrics", "GET", "/items/visits", params="ids,date_from,date_to", docs="visits"),
    _R("ml.visits.user_period", "metrics", "GET", "/users/{user_id}/items_visits", policy="allowlisted_redacted", ownership="seller", params="date_from,date_to", docs="visits"),
    _R("ml.visits.item_window", "metrics", "GET", "/items/{item_id}/visits/time_window", policy="allowlisted_redacted", ownership="owned_item", params="last,unit,ending", docs="visits"),
    _R("ml.visits.user_window", "metrics", "GET", "/users/{user_id}/items_visits/time_window", policy="allowlisted_redacted", ownership="seller", params="last,unit,ending", docs="visits"),
    _R("ml.trends.site", "metrics", "GET", "/trends/{site_id}", ownership="site", docs="trends"),
    _R("ml.trends.category", "metrics", "GET", "/trends/{site_id}/{category_id}", ownership="site", docs="trends"),
    _R("ml.highlights.category", "metrics", "GET", "/highlights/{site_id}/category/{category_id}", ownership="site", params="attribute", docs="highlights"),
    _R("ml.highlights.product", "metrics", "GET", "/highlights/{site_id}/product/{product_id}", ownership="site", docs="highlights"),
    _R("ml.highlights.item", "metrics", "GET", "/highlights/{site_id}/item/{item_id}", ownership="site", docs="highlights"),

    # Auto-parts compatibility.
    _R("ml.compat.item_list", "compatibilities", "GET", "/items/{item_id}/compatibilities", policy="allowlisted_redacted", ownership="owned_item", params="extended,offset,limit", docs="compat", feature_gate="auto_parts"),
    _R("ml.compat.item_get", "compatibilities", "GET", "/items/{item_id}/compatibilities/{compatibility_id}", policy="allowlisted_redacted", ownership="owned_item", docs="compat", feature_gate="auto_parts"),
    _R("ml.compat.item_note", "compatibilities", "GET", "/items/{item_id}/compatibilities/{compatibility_id}/note", policy="allowlisted_redacted", ownership="owned_item", docs="compat", feature_gate="auto_parts"),
    _R("ml.compat.user_product_list", "compatibilities", "GET", "/user-products/{user_product_id}/compatibilities", policy="allowlisted_redacted", ownership="owned_user_product", params="main_domain_id,offset,limit", docs="compat", feature_gate="auto_parts"),
    _R("ml.compat.restriction_values", "compatibilities", "GET", "/catalog_compatibilities/restrictions/values", policy="allowlisted", params="main_domain_id,secondary_domain_id", docs="compat", feature_gate="auto_parts"),
    _R("ml.compat.items_summary", "compatibilities", "POST", "/items/compatibilities_summary", policy="document_only", docs="compat", feature_gate="auto_parts"),
    _R("ml.compat.family_product_count", "compatibilities", "POST", "/catalog_compatibilities/products_search/count_family_products", policy="document_only", docs="compat", feature_gate="auto_parts"),
    _R("ml.compat.order_snapshot", "compatibilities", "GET", "/compats-snapshots/orders/{order_id}", policy="document_only", ownership="owned_order", docs="compat", feature_gate="auto_parts"),
    _R("ml.compat.chunk_search_removed", "compatibilities", "POST", "/catalog_compatibilities/products_search/chunks", policy="tombstone", docs="compat", feature_gate="auto_parts", deprecated_at="2026-07-15"),

    # Questions and classified contacts.
    _R("ml.questions.search", "questions", "GET", "/questions/search", policy="allowlisted_redacted", ownership="seller", params="item_id,from,status,offset,limit,scroll_id,sort_fields,sort_types,search_type", fixed_params={"api_version": "4"}, docs="questions"),
    _R("ml.questions.received", "questions", "GET", "/my/received_questions/search", policy="allowlisted_redacted", ownership="seller", params="item,from,status,offset,limit,sort_fields,sort_types,search_type", fixed_params={"api_version": "4"}, docs="questions"),
    _R("ml.questions.response_time", "questions", "GET", "/users/{user_id}/questions/response_time", policy="allowlisted_redacted", ownership="seller", docs="questions"),
    _R("ml.questions.get", "questions", "GET", "/questions/{question_id}", policy="document_only", ownership="token_scoped", fixed_params={"api_version": "4"}, docs="questions"),
    _R("ml.contacts.item_questions", "classifieds", "GET", "/items/{item_id}/contacts/questions", policy="document_only", ownership="owned_item", params="date_from,date_to", docs="questions", feature_gate="classifieds"),
    _R("ml.contacts.user_questions", "classifieds", "GET", "/users/{user_id}/contacts/questions", policy="document_only", ownership="seller", params="date_from,date_to", docs="questions", feature_gate="classifieds"),
    _R("ml.contacts.item_questions_window", "classifieds", "GET", "/items/{item_id}/contacts/questions/time_window", policy="document_only", ownership="owned_item", params="last,unit,ending", docs="questions", feature_gate="classifieds"),
    _R("ml.contacts.user_questions_window", "classifieds", "GET", "/users/{user_id}/contacts/questions/time_window", policy="document_only", ownership="seller", params="last,unit,ending", docs="questions", feature_gate="classifieds"),

    # Orders, packs, discounts, billing info and feedback.
    _R("ml.orders.get", "orders", "GET", "/orders/{order_id}", policy="allowlisted_redacted", ownership="owned_order", docs="orders"),
    _R("ml.orders.search", "orders", "GET", "/orders/search", policy="allowlisted_redacted", ownership="seller", params="order.status,order.date_created.from,order.date_created.to,order.date_last_updated.from,order.date_last_updated.to,q,offset,limit,sort", docs="orders"),
    _R("ml.packs.get", "orders", "GET", "/packs/{pack_id}", policy="allowlisted_redacted", ownership="owned_pack", docs="orders"),
    _R("ml.orders.discounts", "orders", "GET", "/orders/{order_id}/discounts", policy="allowlisted_redacted", ownership="owned_order", docs="orders"),
    _R("ml.orders.product", "orders", "GET", "/orders/{order_id}/product", policy="allowlisted_redacted", ownership="owned_order", params="offset,limit", docs="orders"),
    _R("ml.orders.billing_info", "orders", "GET", "/orders/billing-info/{site_id}/{billing_info_id}", policy="allowlisted_redacted", ownership="billing_from_order", docs="billing_info"),
    _R("ml.orders.feedback", "orders", "GET", "/orders/{order_id}/feedback", policy="allowlisted_redacted", ownership="owned_order", docs="orders"),
    _R("ml.feedback.get", "orders", "GET", "/feedback/{feedback_id}", policy="document_only", ownership="token_scoped", docs="orders"),
    _R("ml.orders.notes", "orders", "GET", "/orders/{order_id}/notes", policy="allowlisted_redacted", ownership="owned_order", docs="orders"),
    _R("ml.payments.collection", "payments", "GET", "/collections/{payment_id}", policy="document_only", ownership="token_scoped", docs="orders"),
    _R("ml.payment_methods.get", "payments", "GET", "/sites/{site_id}/payment_methods/{method_id}", ownership="site", docs="orders"),

    # Shipments and fulfillment.
    _R("ml.shipments.get", "shipments", "GET", "/shipments/{shipment_id}", policy="allowlisted_redacted", ownership="owned_shipment", docs="shipments", headers={"x-format-new": "true"}),
    *[
        _R(f"ml.shipments.{suffix}", "shipments", "GET", f"/shipments/{{shipment_id}}/{suffix}", policy="allowlisted_redacted", ownership="owned_shipment", docs="shipments", headers={"x-format-new": "true"})
        for suffix in ("items", "payments", "costs", "delays", "lead_time", "carrier", "sla", "history")
    ],
    _R("ml.shipments.statuses", "shipments", "GET", "/shipment_statuses", docs="shipments"),
    _R("ml.shipments.labels", "shipments", "GET", "/shipment_labels", policy="document_only", ownership="token_scoped", params="shipment_ids,response_type", docs="shipments"),
    _R("ml.shipping.item_options", "shipments", "GET", "/items/{item_id}/shipping_options", policy="document_only", ownership="owned_item", params="zip_code", docs="shipments"),
    _R("ml.flex.assignment", "shipments", "GET", "/flex/sites/{site_id}/shipments/{shipment_id}/assignment/v2", policy="document_only", ownership="owned_shipment", docs="shipments", feature_gate="flex"),
    _R("ml.fulfillment.stock", "fulfillment", "GET", "/inventories/{inventory_id}/stock/fulfillment", policy="allowlisted_redacted", ownership="token_scoped", params="include_attributes", docs="fulfillment"),
    _R("ml.fulfillment.operations_search", "fulfillment", "GET", "/stock/fulfillment/operations/search", policy="allowlisted_redacted", ownership="seller", params="inventory_id,date_from,date_to,type,external_references.shipment_id,sort,limit,scroll_id", docs="fulfillment"),
    _R("ml.fulfillment.operation_get", "fulfillment", "GET", "/stock/fulfillment/operations/{operation_id}", policy="allowlisted_redacted", ownership="token_scoped", docs="fulfillment"),
    _R("ml.stock.user_product", "fulfillment", "GET", "/user-products/{user_product_id}/stock", policy="allowlisted_redacted", ownership="owned_user_product", response_headers=("x-version",), docs="distributed_stock"),
    _R("ml.stock.locations", "fulfillment", "GET", "/users/{user_id}/stores/search", policy="allowlisted_redacted", ownership="seller", params="tags,limit,offset", fixed_params={"tags": "stock_location"}, docs="distributed_stock"),

    # Post-sale messages. Conversation GET is delegated because it can mark as read.
    _R("ml.messages.conversation", "messages", "GET", "/messages/packs/{pack_id}/sellers/{user_id}", policy="allowlisted_redacted", ownership="owned_pack", params="limit,offset", fixed_params={"tag": "post_sale", "mark_as_read": "false"}, docs="messages", side_effect="blocked_by_fixed_mark_as_read_false"),
    _R("ml.messages.get", "messages", "GET", "/messages/{message_id}", policy="document_only", ownership="token_scoped", fixed_params={"tag": "post_sale"}, docs="messages"),
    _R("ml.messages.unread", "messages", "GET", "/messages/unread", policy="allowlisted_redacted", ownership="seller", params="role,limit", fixed_params={"tag": "post_sale", "role": "seller"}, docs="unread"),
    _R("ml.messages.unread_resource", "messages", "GET", "/messages/unread/{resource}", policy="document_only", ownership="seller", fixed_params={"tag": "post_sale"}, docs="unread"),

    # Claims and returns.
    _R("ml.claims.search", "claims", "GET", "/post-purchase/v1/claims/search", policy="allowlisted_redacted", ownership="seller", params="status,stage,type,resource,resource_id,date_created.from,date_created.to,offset,limit", docs="claims"),
    *[
        _R(f"ml.claims.{suffix}", "claims", "GET", f"/post-purchase/v1/claims/{{claim_id}}/{path_suffix}" if path_suffix else "/post-purchase/v1/claims/{claim_id}", policy="allowlisted_redacted", ownership="owned_claim", docs="claims")
        for suffix, path_suffix in (
            ("get", ""),
            ("detail", "detail"),
            ("actions_history", "actions-history"),
            ("status_history", "status-history"),
            ("affects_reputation", "affects-reputation"),
            ("messages", "messages"),
            ("evidences", "evidences"),
            ("partial_refund_offers", "partial-refund/available-offers"),
        )
    ],
    _R("ml.claims.attachment_meta", "claims", "GET", "/post-purchase/v1/claims/{claim_id}/attachments/{attachment_id}", policy="document_only", ownership="owned_claim", docs="claims"),
    _R("ml.claims.attachment_download", "claims", "GET", "/post-purchase/v1/claims/{claim_id}/attachments/{attachment_id}/download", policy="document_only", ownership="owned_claim", docs="claims"),
    _R("ml.claims.evidence_attachment_meta", "claims", "GET", "/post-purchase/v1/claims/{claim_id}/attachments-evidences/{attachment_id}", policy="document_only", ownership="owned_claim", docs="claims"),
    _R("ml.claims.evidence_attachment_download", "claims", "GET", "/post-purchase/v1/claims/{claim_id}/attachments-evidences/{attachment_id}/download", policy="document_only", ownership="owned_claim", docs="claims"),
    _R("ml.returns.get_by_claim", "returns", "GET", "/post-purchase/v2/claims/{claim_id}/returns", policy="allowlisted_redacted", ownership="owned_claim", docs="returns"),
    _R("ml.returns.review", "returns", "GET", "/post-purchase/v1/returns/{return_id}/reviews", policy="document_only", ownership="token_scoped", docs="returns"),
    _R("ml.returns.reasons", "returns", "GET", "/post-purchase/v1/returns/reasons", policy="allowlisted_redacted", ownership="token_scoped", params="flow,claim_id", docs="returns"),
    _R("ml.returns.cost", "returns", "GET", "/post-purchase/v1/claims/{claim_id}/charges/return-cost", policy="allowlisted_redacted", ownership="owned_claim", params="calculate_amount_usd", docs="returns"),

    # Promotions.
    _R("ml.promotions.user", "promotions", "GET", "/seller-promotions/users/{user_id}", policy="delegated", ownership="seller", fixed_params={"app_version": "v2"}, docs="promotions", delegated_tool="mercado_livre_promotions"),
    _R("ml.promotions.candidate", "promotions", "GET", "/seller-promotions/candidates/{candidate_id}", policy="allowlisted_redacted", ownership="token_scoped", fixed_params={"app_version": "v2"}, docs="promotions"),
    _R("ml.promotions.offer", "promotions", "GET", "/seller-promotions/offers/{offer_id}", policy="allowlisted_redacted", ownership="token_scoped", fixed_params={"app_version": "v2"}, docs="promotions"),
    _R("ml.promotions.get", "promotions", "GET", "/seller-promotions/promotions/{promotion_id}", policy="allowlisted_redacted", ownership="token_scoped", params="promotion_type", fixed_params={"app_version": "v2"}, docs="promotions"),
    _R("ml.promotions.items", "promotions", "GET", "/seller-promotions/promotions/{promotion_id}/items", policy="allowlisted_redacted", ownership="token_scoped", params="promotion_type,item_id,status,status_item,limit,search_after", fixed_params={"app_version": "v2"}, docs="promotions"),
    _R("ml.promotions.item", "promotions", "GET", "/seller-promotions/items/{item_id}", policy="allowlisted_redacted", ownership="owned_item", fixed_params={"app_version": "v2"}, docs="promotions"),
    _R("ml.promotions.exclusion_list", "promotions", "GET", "/seller-promotions/exclusion-list/seller", policy="allowlisted_redacted", ownership="seller", fixed_params={"app_version": "v2"}, docs="promotions"),
    _R("ml.promotions.exclusion_item", "promotions", "GET", "/seller-promotions/exclusion-list/seller/{item_id}", policy="allowlisted_redacted", ownership="owned_item", fixed_params={"app_version": "v2"}, docs="promotions"),

    # Billing and invoices. Binary downloads remain document-only.
    _R("ml.billing.periods", "billing", "GET", "/billing/integration/monthly/periods", policy="allowlisted_redacted", ownership="token_scoped", params="document_type,offset,limit", docs="billing"),
    _R("ml.billing.documents", "billing", "GET", "/billing/integration/periods/key/{period_key}/documents", policy="allowlisted_redacted", ownership="token_scoped", params="group,document_type,offset,limit", docs="billing"),
    _R("ml.billing.summary_details", "billing", "GET", "/billing/integration/periods/key/{period_key}/summary/details", policy="allowlisted_redacted", ownership="token_scoped", docs="billing"),
    _R("ml.billing.ml_details", "billing", "GET", "/billing/integration/periods/key/{period_key}/group/ML/details", policy="allowlisted_redacted", ownership="token_scoped", params="document_type,limit,from_id", docs="billing"),
    _R("ml.billing.mp_details", "billing", "GET", "/billing/integration/periods/key/{period_key}/group/MP/details", policy="document_only", ownership="token_scoped", docs="billing"),
    _R("ml.billing.order_details", "billing", "GET", "/billing/integration/group/ML/order/details", policy="allowlisted_redacted", ownership="token_scoped", params="order_ids", docs="billing"),
    _R("ml.billing.payment_details", "billing", "GET", "/billing/integration/periods/key/{period_key}/group/ML/payment/details", policy="allowlisted_redacted", ownership="token_scoped", params="limit,from_id", docs="billing"),
    _R("ml.billing.payment_charges", "billing", "GET", "/billing/integration/payment/{payment_id}/charges", policy="allowlisted_redacted", ownership="token_scoped", params="sort,offset,limit", docs="billing"),
    _R("ml.billing.perceptions_summary", "billing", "GET", "/billing/integration/periods/key/{period_key}/perceptions/summary", policy="document_only", ownership="token_scoped", docs="billing", feature_gate="site_mla"),
    _R("ml.billing.legal_document", "billing", "GET", "/billing/integration/legal_document/{file_id}", policy="document_only", ownership="token_scoped", docs="billing"),
    _R("ml.billing.report_download", "billing", "GET", "/billing/integration/reports/{file_id}", policy="document_only", ownership="token_scoped", docs="billing"),
    _R("ml.invoices.get", "invoices", "GET", "/users/{user_id}/invoices/{invoice_id}", policy="document_only", ownership="seller", docs="invoices"),
    _R("ml.invoices.by_order", "invoices", "GET", "/users/{user_id}/invoices/orders/{order_id}", policy="document_only", ownership="owned_order", docs="invoices"),
    _R("ml.invoices.by_shipment", "invoices", "GET", "/users/{user_id}/invoices/shipments/{shipment_id}", policy="document_only", ownership="owned_shipment", docs="invoices"),
    _R("ml.invoices.xml_authorized", "invoices", "GET", "/users/{user_id}/invoices/documents/xml/{invoice_id}/authorized", policy="document_only", ownership="seller", docs="invoices"),

    # Advertising is catalogued but gated until a separate permission rollout.
    _R("ml.ads.advertisers", "ads", "GET", "/advertising/advertisers", policy="document_only", ownership="token_scoped", params="product_id", docs="ads", feature_gate="ads"),
    _R("ml.ads.item", "ads", "GET", "/advertising/{site_id}/product_ads/ads/{item_id}", policy="document_only", ownership="owned_item", docs="ads", feature_gate="ads", headers={"api-version": "2"}),
    _R("ml.ads.ads_search", "ads", "GET", "/advertising/{site_id}/advertisers/{advertiser_id}/product_ads/ads/search", policy="document_only", ownership="token_scoped", params="date_from,date_to,metrics,offset,limit", docs="ads", feature_gate="ads", headers={"api-version": "2"}),
    _R("ml.ads.campaigns_search", "ads", "GET", "/advertising/{site_id}/advertisers/{advertiser_id}/product_ads/campaigns/search", policy="document_only", ownership="token_scoped", params="date_from,date_to,offset,limit", docs="ads", feature_gate="ads", headers={"api-version": "2"}),
    _R("ml.ads.campaign_metrics", "ads", "GET", "/advertising/{site_id}/product_ads/campaigns/{campaign_id}", policy="document_only", ownership="token_scoped", params="date_from,date_to,metrics,aggregation", docs="ads", feature_gate="ads", headers={"api-version": "2"}),
    _R("ml.ads.ad_groups_search", "ads", "GET", "/advertising/{site_id}/advertisers/{advertiser_id}/product_ads/ad_groups/search", policy="document_only", ownership="token_scoped", params="offset,limit", docs="ads", feature_gate="ads", headers={"api-version": "2"}),
    _R("ml.ads.ad_group_metrics", "ads", "GET", "/advertising/{site_id}/product_ads/campaigns/{campaign_id}/ad_groups/metrics", policy="document_only", ownership="token_scoped", params="date_from,date_to,metrics", docs="ads", feature_gate="ads", headers={"api-version": "2"}),
]


MERCADO_LIVRE_QUERY_RESOURCE_BY_ID = {
    str(resource["resource_id"]): resource for resource in MERCADO_LIVRE_QUERY_RESOURCES
}


def mercado_livre_query_catalog_public() -> dict[str, Any]:
    policies: dict[str, int] = {}
    domains: dict[str, int] = {}
    for resource in MERCADO_LIVRE_QUERY_RESOURCES:
        policies[resource["policy"]] = policies.get(resource["policy"], 0) + 1
        domains[resource["domain"]] = domains.get(resource["domain"], 0) + 1
    return {
        "success": True,
        "catalog_version": CATALOG_VERSION,
        "last_verified_at": LAST_VERIFIED_AT,
        "read_only": True,
        "free_url_blocked": True,
        "mutating_routes_blocked": True,
        "resources": [dict(resource) for resource in MERCADO_LIVRE_QUERY_RESOURCES],
        "total": len(MERCADO_LIVRE_QUERY_RESOURCES),
        "by_policy": policies,
        "by_domain": domains,
    }


__all__ = [
    "CATALOG_VERSION",
    "LAST_VERIFIED_AT",
    "MERCADO_LIVRE_QUERY_RESOURCES",
    "MERCADO_LIVRE_QUERY_RESOURCE_BY_ID",
    "mercado_livre_query_catalog_public",
]
