"""Deterministic Mercado Livre listing bundles for WhatsApp delivery."""

from __future__ import annotations

import re
from typing import Any

from backend.services.whatsapp import formatting


LISTING_TEXT_MAX_CHARS = 8000
LISTING_TEXT_MAX_ITEMS = 12
LISTING_PICTURES_PER_ITEM = 3


def delivery_requested(value: Any) -> bool:
    """Recognize requests for a listing bundle without relying on an LLM."""

    text = formatting._whatsapp_text_key(value)
    if not text:
        return False
    product_reference = bool(
        re.search(r"\bsku\s*[a-z0-9._/-]+\b", text)
        or re.search(r"\bmlb[\s_-]*\d{6,}\b", text)
        or re.search(r"\b(anuncio|anuncios|produto)\b", text)
    )
    requested_data = bool(
        re.search(
            r"\b(link|links|url|foto|fotos|imagem|imagens|descricao|descricoes|detalhe|detalhes|"
            r"dados|informacao|informacoes|mlb|anuncio|anuncios)\b",
            text,
        )
    )
    return bool(product_reference and requested_data)


def pictures_requested(value: Any) -> bool:
    text = formatting._whatsapp_text_key(value)
    if re.search(r"\b(foto|fotos|imagem|imagens)\b", text):
        return True
    return bool(
        delivery_requested(value)
        and re.search(r"\b(manda|mande|mandar|envia|envie|enviar|mostra|mostre|mostrar)\b", text)
        and re.search(r"\b(anuncio|anuncios)\b", text)
    )


def deterministic_response_requested(value: Any) -> bool:
    text = formatting._whatsapp_text_key(value)
    requires_analysis = bool(
        re.search(
            r"\b(serve|funciona|encaixa|compativel|compatibilidade|aplica|aplicacao|manual|oem|fabricante|"
            r"compare|comparar|melhor|recomenda|recomendar|analise|analisar|avalie|avaliar)\b",
            text,
        )
    )
    return bool(delivery_requested(value) and not requires_analysis)


def _row_picture_values(row: dict[str, Any]) -> list[dict[str, Any]]:
    pictures: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_pictures = row.get("pictures") if isinstance(row.get("pictures"), list) else []
    for item in raw_pictures[:LISTING_PICTURES_PER_ITEM]:
        if isinstance(item, dict):
            url = str(item.get("secure_url") or item.get("url") or "").strip()
            picture_id = str(item.get("id") or "").strip()
            width = item.get("width")
            height = item.get("height")
        else:
            url = str(item or "").strip()
            picture_id = ""
            width = None
            height = None
        if not url or url in seen:
            continue
        seen.add(url)
        pictures.append({"id": picture_id, "secure_url": url, "width": width, "height": height})
    for value in list(row.get("picture_urls") or [])[:LISTING_PICTURES_PER_ITEM]:
        url = str(value or "").strip()
        if url and url not in seen:
            seen.add(url)
            pictures.append({"id": "", "secure_url": url, "width": None, "height": None})
    thumbnail = str(row.get("thumbnail") or "").strip()
    if thumbnail and thumbnail not in seen:
        pictures.append({"id": "", "secure_url": thumbnail, "width": None, "height": None})
    return pictures[:LISTING_PICTURES_PER_ITEM]


def _rows_from_result(value: dict[str, Any]) -> list[dict[str, Any]]:
    function_name = str(value.get("function") or "").strip()
    tool_id = str(value.get("tool_id") or "").strip()
    if tool_id and tool_id != "mercado_livre_listing":
        return []
    if function_name and function_name != "get_mercado_livre_listing":
        return []
    nested_bundle = value.get("listing_bundle") if isinstance(value.get("listing_bundle"), dict) else {}
    if isinstance(nested_bundle.get("listings"), list):
        return [item for item in nested_bundle.get("listings") if isinstance(item, dict)]
    for key in ("all_rows", "top_rows", "rows", "matches"):
        rows = value.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
    payload = value.get("result") if isinstance(value.get("result"), dict) else {}
    for key in ("matches", "rows", "items"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
    data = value.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("matches", "rows", "items"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [item for item in rows if isinstance(item, dict)]
    return []


def _coverage_is_partial(value: Any, depth: int = 0) -> bool:
    """Read coverage markers from both raw and compacted Codex result shapes."""

    if depth > 6:
        return False
    if isinstance(value, list):
        return any(_coverage_is_partial(item, depth + 1) for item in value)
    if not isinstance(value, dict):
        return False
    if (
        value.get("coverage_complete") is False
        or value.get("partial_response") is True
        or value.get("truncated") is True
        or value.get("has_more") is True
    ):
        return True
    missing_fields = value.get("campos_faltantes") if isinstance(value.get("campos_faltantes"), list) else []
    if any("cobertura" in formatting._whatsapp_text_key(item) for item in missing_fields):
        return True
    for key in ("result", "summary", "paging", "chart_data", "exact_coverage", "coverage", "tool_validation"):
        if _coverage_is_partial(value.get(key), depth + 1):
            return True
    return False


def build_listing_bundle(tool_results: Any) -> dict[str, Any]:
    """Normalize raw or compacted ML tool results into a stable media contract."""

    if isinstance(tool_results, dict) and isinstance(tool_results.get("listings"), list):
        return dict(tool_results)
    values = tool_results if isinstance(tool_results, list) else [tool_results]
    listings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    stores: list[str] = []
    total_records = 0
    coverage_complete = True
    warnings: list[str] = []
    for result in values:
        if not isinstance(result, dict):
            continue
        rows = _rows_from_result(result)
        if not rows and str(result.get("tool_id") or "") != "mercado_livre_listing" and str(result.get("function") or "") != "get_mercado_livre_listing":
            continue
        total_records = max(total_records, int(result.get("records") or len(rows) or 0))
        if _coverage_is_partial(result):
            coverage_complete = False
        for warning in list(result.get("warnings") or []):
            text = str(warning or "").strip()
            if text and text not in warnings:
                warnings.append(text[:500])
        default_store = str(result.get("manager_store") or "").strip()
        payload = result.get("result") if isinstance(result.get("result"), dict) else {}
        default_store = default_store or str(payload.get("store") or result.get("store") or "").strip()
        for row in rows:
            store = str(row.get("loja") or row.get("store") or default_store).strip()
            item_id = re.sub(r"[\s_-]+", "", str(row.get("id") or row.get("item_id") or "").upper())
            sku = str(row.get("matched_sku") or row.get("seller_sku") or row.get("sku") or "").strip()
            signature = (formatting._whatsapp_text_key(store), item_id, sku.upper())
            if signature in seen:
                continue
            seen.add(signature)
            if store and store not in stores:
                stores.append(store)
            listings.append(
                {
                    "store": store,
                    "item_id": item_id,
                    "sku": sku,
                    "title": str(row.get("title") or row.get("titulo") or "").strip(),
                    "status": str(row.get("status") or "").strip(),
                    "currency_id": str(row.get("currency_id") or "BRL").strip(),
                    "price": row.get("price"),
                    "available_quantity": row.get("available_quantity"),
                    "sold_quantity": row.get("sold_quantity"),
                    "permalink": str(row.get("permalink") or row.get("link") or "").strip(),
                    "description": str(row.get("description") or row.get("descricao") or "").strip()[:1500],
                    "pictures": _row_picture_values(row),
                }
            )
    picture_count = sum(len(item.get("pictures") or []) for item in listings)
    return {
        "version": "20260717-ml-listing-whatsapp-v1",
        "source": "mercado_livre_api",
        "stores": stores,
        "listings": listings,
        "listing_count": len(listings),
        "record_count": max(total_records, len(listings)),
        "picture_count": picture_count,
        "coverage_complete": bool(listings and coverage_complete),
        "warnings": warnings[:20],
    }


def _money(value: Any, currency_id: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "nao informado"
    prefix = "R$" if str(currency_id or "").upper() == "BRL" else str(currency_id or "").upper()
    rendered = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{prefix} {rendered}".strip()


def _quantity(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "nao informada"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "nao informada"
    return str(int(number)) if number.is_integer() else str(number).replace(".", ",")


def format_listing_bundle(bundle: Any, request_text: Any = "") -> str:
    contract = build_listing_bundle(bundle)
    listings = [item for item in list(contract.get("listings") or []) if isinstance(item, dict)]
    if not listings or (request_text and not deterministic_response_requested(request_text)):
        return ""
    stores = [str(item or "").strip() for item in list(contract.get("stores") or []) if str(item or "").strip()]
    header_scope = ", ".join(stores) if stores else "loja consultada"
    lines = [f"*Anuncios do Mercado Livre — {header_scope}*"]
    rendered = 0
    for index, item in enumerate(listings[:LISTING_TEXT_MAX_ITEMS], start=1):
        description = re.sub(r"\s+", " ", str(item.get("description") or "")).strip()
        if len(description) > 700:
            description = description[:697].rstrip() + "..."
        block = [
            "",
            f"*{index}. {str(item.get('title') or 'Anuncio sem titulo').strip()}*",
            f"MLB: {str(item.get('item_id') or 'nao informado')}",
            f"SKU: {str(item.get('sku') or 'nao informado')}",
            f"Status: {str(item.get('status') or 'nao informado')}",
            f"Preco: {_money(item.get('price'), str(item.get('currency_id') or 'BRL'))}",
            f"Disponivel: {_quantity(item.get('available_quantity'))}",
            f"Link: {str(item.get('permalink') or 'nao retornado pela API')}",
            f"Descricao: {description or 'nao retornada pela API'}",
        ]
        candidate = "\n".join([*lines, *block])
        if len(candidate) > LISTING_TEXT_MAX_CHARS:
            break
        lines.extend(block)
        rendered += 1
    omitted = len(listings) - rendered
    if omitted > 0:
        lines.extend(["", f"Mais {omitted} anuncio(s) foram encontrados; reduza o filtro para receber todos os detalhes."])
    if int(contract.get("picture_count") or 0) > 0 and pictures_requested(request_text):
        lines.append("Fotos oficiais do anuncio: envio de ate 3 imagens em seguida.")
    lines.extend(["", "Fonte: API oficial do Mercado Livre, na loja indicada."])
    if contract.get("coverage_complete") is not True:
        lines.append("Cobertura: parcial; os dados confirmados foram preservados sem completar campos por estimativa.")
    return "\n".join(lines).strip()[:LISTING_TEXT_MAX_CHARS]


def image_candidates(bundle: Any, *, max_images: int = 3) -> list[dict[str, Any]]:
    """Pick one official photo per listing before extra photos of the same item."""

    contract = build_listing_bundle(bundle)
    listings = [item for item in list(contract.get("listings") or []) if isinstance(item, dict)]
    limit = max(0, min(int(max_images or 0), 3))
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    picture_index = 0
    while len(candidates) < limit:
        added = False
        for listing in listings:
            pictures = [item for item in list(listing.get("pictures") or []) if isinstance(item, dict)]
            if picture_index >= len(pictures):
                continue
            url = str(pictures[picture_index].get("secure_url") or pictures[picture_index].get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            candidates.append(
                {
                    "url": url,
                    "store": str(listing.get("store") or "").strip(),
                    "item_id": str(listing.get("item_id") or "").strip(),
                    "sku": str(listing.get("sku") or "").strip(),
                    "title": str(listing.get("title") or "").strip(),
                    "listing_picture_index": picture_index + 1,
                }
            )
            added = True
            if len(candidates) >= limit:
                break
        if not added:
            break
        picture_index += 1
    for index, item in enumerate(candidates, start=1):
        item["sequence"] = index
        item["sequence_total"] = len(candidates)
    return candidates


__all__ = [
    "build_listing_bundle",
    "delivery_requested",
    "deterministic_response_requested",
    "format_listing_bundle",
    "image_candidates",
    "pictures_requested",
]
