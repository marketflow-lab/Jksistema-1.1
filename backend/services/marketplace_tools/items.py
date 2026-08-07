"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Optional

from . import runtime as _runtime

logger = logging.getLogger(__name__)

def needs_description(mensagem: str) -> bool:
    texto = _runtime.normalize_text(mensagem or "")
    return any(chave in texto for chave in ("DESCRICAO", "DESCRICAO DO ANUNCIO", "TEXTO DO ANUNCIO", "ANUNCIO COMPLETO"))

def summarize_item(
    item: dict,
    loja: str,
    descricao: str = "",
    detalhes: Optional[dict] = None,
    requested_sku: str = "",
) -> dict:
    pictures = []
    picture_urls = []
    for picture in (item.get("pictures") or [])[:10]:
        if not isinstance(picture, dict):
            continue
        secure_url = str(picture.get("secure_url") or picture.get("url") or "").strip()
        if not secure_url or secure_url in picture_urls:
            continue
        picture_urls.append(secure_url)
        pictures.append({
            "id": str(picture.get("id") or "").strip(),
            "secure_url": secure_url,
            "url": str(picture.get("url") or "").strip(),
            "width": picture.get("width"),
            "height": picture.get("height"),
        })
    variacoes = [
        _ia_ml_variation_summary(var)
        for var in (item.get("variations") or [])[:8]
        if isinstance(var, dict)
    ]
    sku_match = item.get("_ia_sku_match") if isinstance(item.get("_ia_sku_match"), dict) else {}
    if requested_sku and not sku_match:
        sku_match = _ia_ml_find_exact_sku_match(item, requested_sku)
    matched_variation = sku_match.get("variation") if isinstance(sku_match.get("variation"), dict) else None
    parent_sku = _ia_ml_parent_sku_item(item)
    effective_sku = str(sku_match.get("matched_sku") or _ia_ml_sku_item(item) or "").strip()
    effective_price = matched_variation.get("price") if matched_variation else item.get("price")
    effective_available = matched_variation.get("available_quantity") if matched_variation else item.get("available_quantity")
    effective_sold = matched_variation.get("sold_quantity") if matched_variation else item.get("sold_quantity")
    result = {
        "loja": loja,
        "id": str(item.get("id") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "status": str(item.get("status") or "").strip(),
        "sub_status": item.get("sub_status") or [],
        "seller_sku": effective_sku,
        "parent_sku": parent_sku,
        "requested_sku": str(requested_sku or "").strip(),
        "matched_sku": str(sku_match.get("matched_sku") or "").strip(),
        "match": {
            "exact": bool(sku_match.get("exact")),
            "matched_by": str(sku_match.get("matched_by") or "").strip(),
            "variation_id": str((matched_variation or {}).get("id") or "").strip(),
        },
        "selected_variation": matched_variation,
        "currency_id": str(item.get("currency_id") or "BRL").strip(),
        "price": effective_price,
        "base_price": item.get("base_price"),
        "original_price": item.get("original_price"),
        "available_quantity": effective_available,
        "sold_quantity": effective_sold,
        "sold_quantity_scope": "acumulado_da_variacao" if matched_variation else "acumulado_do_anuncio",
        "item_price": item.get("price"),
        "item_available_quantity": item.get("available_quantity"),
        "item_sold_quantity": item.get("sold_quantity"),
        "listing_type_id": str(item.get("listing_type_id") or "").strip(),
        "category_id": str(item.get("category_id") or "").strip(),
        "permalink": str(item.get("permalink") or "").strip(),
        "thumbnail": str(item.get("thumbnail") or "").strip(),
        "pictures": pictures,
        "picture_urls": picture_urls,
        "health": item.get("health"),
        "catalog_listing": bool(item.get("catalog_listing")),
        "variations": variacoes,
        "description": descricao[:1500] if descricao else "",
    }
    if isinstance(detalhes, dict):
        result["details"] = detalhes
    return result

def get_item_description(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    *,
    timeout: int = 15,
) -> tuple[str, dict]:
    item_id_txt = str(item_id or "").strip()
    if not item_id_txt:
        return "", cfg
    try:
        resp, cfg = _runtime.ml_api_request(
            client_id,
            loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/items/{item_id_txt}/description",
            timeout=max(1, min(int(timeout or 15), 15)),
        )
        if resp.status_code != 200:
            return "", cfg
        data = resp.json() or {}
        return str(data.get("plain_text") or data.get("text") or "").strip(), cfg
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha ao consultar descricao ML %s/%s: %s", loja, item_id_txt, exc)
        return "", cfg

def _ia_ml_normalizar_item_ids(*values: Any) -> list[str]:
    ids = []
    seen = set()
    for value in values:
        for candidate in re.findall(r"\bMLB[\s_-]*\d+\b", str(value or "").upper()):
            normalized = re.sub(r"[^A-Z0-9]", "", str(candidate or "").upper())
            if normalized.startswith("MLB") and normalized not in seen:
                seen.add(normalized)
                ids.append(normalized)
    return ids

def _ia_ml_sku_parece_data(value: Any) -> bool:
    texto = str(value or "").strip()
    return bool(
        re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", texto)
        or re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", texto)
    )

def _ia_ml_sku_item(item: dict) -> str:
    extractor = globals().get("_ml_extrair_sku")
    if callable(extractor):
        try:
            sku = str(extractor(item) or "").strip()
            if sku:
                return sku
        except Exception:
            pass
    for key in ("seller_sku", "seller_custom_field", "sku"):
        sku = str((item or {}).get(key) or "").strip()
        if sku:
            return sku
    for attribute in (item or {}).get("attributes") or []:
        if not isinstance(attribute, dict):
            continue
        if str(attribute.get("id") or "").upper() in {"SELLER_SKU", "SKU"}:
            sku = str(attribute.get("value_name") or attribute.get("value_id") or "").strip()
            if sku:
                return sku
    return ""

def _ia_ml_sku_comparison_key(value: Any) -> str:
    """Normaliza SKU sem confundir variantes numericamente diferentes."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").upper()
    return re.sub(r"[^A-Z0-9]", "", text)

def _ia_ml_parent_sku_item(item: dict) -> str:
    """Extrai apenas o SKU do anuncio pai, sem herdar SKUs das variacoes."""
    parent = dict(item or {})
    parent.pop("variations", None)
    parent.pop("variations_data", None)
    return _ia_ml_sku_item(parent)

def _ia_ml_variation_options(variation: dict) -> tuple[list[dict[str, str]], str]:
    options: list[dict[str, str]] = []
    for attribute in (variation or {}).get("attribute_combinations") or []:
        if not isinstance(attribute, dict):
            continue
        name = str(attribute.get("name") or attribute.get("id") or "Variação").strip()
        value = str(attribute.get("value_name") or attribute.get("value_id") or "").strip()
        if not value:
            values = attribute.get("values") if isinstance(attribute.get("values"), list) else []
            for entry in values:
                if isinstance(entry, dict) and str(entry.get("name") or "").strip():
                    value = str(entry.get("name") or "").strip()
                    break
        if value:
            options.append({"id": str(attribute.get("id") or "").strip(), "name": name, "value": value})
    label = " | ".join(
        f"{option['name']}: {option['value']}" if option.get("name") else option["value"]
        for option in options
    )
    return options, label

def _ia_ml_variation_summary(variation: dict) -> dict:
    options, option_label = _ia_ml_variation_options(variation)
    return {
        "id": str((variation or {}).get("id") or "").strip(),
        "sku": _ia_ml_sku_item(variation or {}),
        "option_label": option_label,
        "options": options,
        "price": (variation or {}).get("price"),
        "available_quantity": (variation or {}).get("available_quantity"),
        "sold_quantity": (variation or {}).get("sold_quantity"),
    }

def _ia_ml_find_exact_sku_match(item: dict, requested_sku: str) -> dict:
    requested = str(requested_sku or "").strip()
    requested_key = _ia_ml_sku_comparison_key(requested)
    if not requested_key:
        return {}
    parent_sku = _ia_ml_parent_sku_item(item)
    if parent_sku and _ia_ml_sku_comparison_key(parent_sku) == requested_key:
        return {
            "exact": True,
            "requested_sku": requested,
            "matched_sku": parent_sku,
            "matched_by": "parent_seller_sku",
            "parent_sku": parent_sku,
            "variation": None,
        }
    for variation in (item or {}).get("variations") or []:
        if not isinstance(variation, dict):
            continue
        variation_sku = _ia_ml_sku_item(variation)
        if variation_sku and _ia_ml_sku_comparison_key(variation_sku) == requested_key:
            return {
                "exact": True,
                "requested_sku": requested,
                "matched_sku": variation_sku,
                "matched_by": "variation_seller_sku",
                "parent_sku": parent_sku,
                "variation": _ia_ml_variation_summary(variation),
            }
    return {}
