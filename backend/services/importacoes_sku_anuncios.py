"""Current own advertisements for the exact store and SKU of an import list."""

from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import unquote, urlsplit

import requests
from fastapi import HTTPException

from backend.services import integracoes
from backend.services.mercadolivre_legacy_api import _ml_api_request
from backend.services.perguntas_loading_transport import read_budget
from backend.services.perguntas_store_config import obter_cfg_ml_snapshot


LIMITE_ANUNCIOS = 5
TAMANHO_PAGINA = 50
LIMITE_CANDIDATOS = 100
LIMITE_PAGINAS = 3
TEMPO_TOTAL_SEGUNDOS = 20
_SKU_FIELDS = ("seller_sku", "sellerSku", "SELLER_SKU", "sku", "SKU",
               "seller_custom_field", "sellerCustomField", "SELLER_CUSTOM_FIELD", "custom_sku", "item_sku")
_SKU_ATTRIBUTES = {"SELLER_SKU", "SKU", "SELLER_CUSTOM_FIELD"}
_LEGACY_SKU_FIELDS = {"seller_custom_field", "sellerCustomField", "SELLER_CUSTOM_FIELD"}


def _erro(status: int, code: str, message: str):
    raise HTTPException(status, {"code": code, "message": message})


def _sku_texto(value: Any) -> str:
    return str(value).strip() if value is not None and not isinstance(value, (dict, list, bool)) else ""


def _skus_declarados(item: dict) -> set[str]:
    modern_attributes: set[str] = set()
    legacy_attributes: set[str] = set()
    for field in ("attributes", "attribute_combinations"):
        attributes = item.get(field) or []
        if not isinstance(attributes, list):
            return {""}
        for attr in attributes:
            if not isinstance(attr, dict) or attr.get("id") not in _SKU_ATTRIBUTES:
                continue
            values = legacy_attributes if attr["id"] == "SELLER_CUSTOM_FIELD" else modern_attributes
            primary = next((_sku_texto(attr.get(key)) for key in ("value_name", "value", "value_id")
                            if _sku_texto(attr.get(key))), "")
            if primary:
                values.add(primary)
            attribute_values = attr.get("values") or []
            if not isinstance(attribute_values, list):
                return {""}
            for value in attribute_values:
                if not isinstance(value, dict):
                    continue
                semantic = next((_sku_texto(value.get(key)) for key in ("name", "value_name", "value", "value_id", "id")
                                 if _sku_texto(value.get(key))), "")
                if semantic:
                    values.add(semantic)
    # The canonical ML extractor prioritizes current SKU attributes over the
    # legacy seller_custom_field, which may retain a previous SKU (e.g. 261).
    # Multiple current attributes still have to agree with each other.
    if modern_attributes:
        return modern_attributes
    current_fields = {_sku_texto(item.get(field)) for field in _SKU_FIELDS if field not in _LEGACY_SKU_FIELDS}
    current_fields.discard("")
    if current_fields:
        return current_fields
    if legacy_attributes:
        return legacy_attributes
    legacy_fields = {_sku_texto(item.get(field)) for field in _LEGACY_SKU_FIELDS}
    legacy_fields.discard("")
    return legacy_fields


def anuncio_corresponde_sku_exato(item: dict, sku: str) -> bool:
    """Require literal SKU evidence; every variation must agree explicitly.

    Current SKU attributes take precedence over a legacy custom field.
    Titles, partial matches, compound SKUs and a parent's SKU alone cannot
    authorize a variation whose own SKU is absent or different.
    """
    target = _sku_texto(sku)
    if not isinstance(item, dict) or not target:
        return False
    parent = _skus_declarados(item)
    variations = item.get("variations", [])
    if variations is None:
        variations = []
    if not isinstance(variations, list):
        return False
    if variations:
        return (not parent or parent == {target}) and all(
            isinstance(variation, dict) and _skus_declarados(variation) == {target}
            for variation in variations
        )
    return parent == {target}


def _preco(item: dict) -> float | None:
    raw = item.get("price")
    if isinstance(raw, bool):
        return None
    try:
        price = float(raw)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(price) or price <= 0 or item.get("currency_id") != "BRL":
        return None
    for variation in item.get("variations") or []:
        if variation.get("price") is None:
            continue
        if isinstance(variation["price"], bool):
            return None
        try:
            variation_price = float(variation["price"])
        except (ValueError, TypeError):
            return None
        if not math.isfinite(variation_price) or abs(variation_price - price) > .005:
            return None
    return price


def _url(item: dict, item_id: str) -> str | None:
    value = item.get("permalink")
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in
                {"mercadolivre.com.br", "www.mercadolivre.com.br", "produto.mercadolivre.com.br"}
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            return None
    except ValueError:
        return None
    identifiers = {"MLB" + digits for digits in re.findall(r"\bMLB-?(\d+)\b", unquote(value))}
    return value if item_id in identifiers else None


def _cfg_atual(client_id: str, scope: dict) -> dict:
    cfg = obter_cfg_ml_snapshot(client_id, scope["loja"], scope["store_id"],
                               seller_id=scope["seller_id"], site_id="MLB", require_name_match=True)
    if (not isinstance(cfg, dict) or not str(cfg.get("access_token") or "").strip()
            or str(cfg.get("_store_id_context") or "") != scope["store_id"]
            or str(cfg.get("user_id") or "") != scope["seller_id"]
            or str(cfg.get("site_id") or "") != "MLB"):
        _erro(409, "sku_ads_identity_changed", "A conexão da loja mudou. Atualize a lista para consultar os anúncios.")
    return cfg


def _get(client_id: str, scope: dict, endpoint: str, *, params=None):
    cfg = _cfg_atual(client_id, scope)
    response, returned_cfg = _ml_api_request(client_id, scope["loja"], cfg, "GET",
                                            "https://api.mercadolibre.com/" + endpoint,
                                            params=params, timeout=12)
    if (not isinstance(returned_cfg, dict)
            or not str(returned_cfg.get("access_token") or "").strip()
            or str(returned_cfg.get("_store_id_context") or "") != scope["store_id"]
            or str(returned_cfg.get("user_id") or "") != scope["seller_id"]
            or str(returned_cfg.get("site_id") or "") != "MLB"):
        _erro(409, "sku_ads_identity_changed", "A identidade da conexão mudou durante a consulta dos anúncios.")
    return response


def _json(response):
    try:
        return response.json()
    except (ValueError, TypeError):
        _erro(502, "sku_ads_invalid_response", "O Mercado Livre retornou dados inválidos dos anúncios.")


def _exigir_resposta(response):
    status = getattr(response, "status_code", None)
    if status == 200:
        return
    if status in (401, 403):
        _erro(status, "sku_ads_access_denied", "A conexão desta loja não autorizou a consulta de seus anúncios.")
    if status == 429:
        _erro(503, "sku_ads_rate_limited", "O Mercado Livre limitou a consulta dos anúncios. Tente novamente em instantes.")
    _erro(503, "sku_ads_unavailable", "Não foi possível consultar os anúncios desta loja no Mercado Livre.")


def _buscar_ids(client_id: str, scope: dict, sku: str) -> list[str]:
    ids: list[str] = []
    total = None
    offset = 0
    for _page in range(LIMITE_PAGINAS):
        response = _get(client_id, scope, "users/" + scope["seller_id"] + "/items/search",
                        params={"seller_sku": sku, "status": "active", "offset": offset, "limit": TAMANHO_PAGINA})
        _exigir_resposta(response)
        body = _json(response)
        paging = body.get("paging") if isinstance(body, dict) else None
        results = body.get("results") if isinstance(body, dict) else None
        if (not isinstance(results, list) or not isinstance(paging, dict)
                or any(type(paging.get(field)) is not int for field in ("total", "offset", "limit"))
                or paging["total"] < 0 or paging["offset"] != offset or paging["limit"] != TAMANHO_PAGINA):
            _erro(502, "sku_ads_invalid_paging", "O Mercado Livre não confirmou a paginação dos anúncios do SKU.")
        if paging["total"] > LIMITE_CANDIDATOS:
            _erro(503, "sku_ads_search_limit", "Há anúncios demais para conferir este SKU nesta consulta. Refine o cadastro do SKU.")
        if total is not None and total != paging["total"]:
            _erro(409, "sku_ads_catalog_changed", "Os anúncios mudaram durante a consulta. Tente novamente.")
        total = paging["total"]
        if len(results) != min(TAMANHO_PAGINA, max(0, total - offset)):
            _erro(409, "sku_ads_catalog_changed", "A busca de anúncios está incompleta. Tente novamente.")
        for entry in results:
            item_id = entry.get("id") if isinstance(entry, dict) else entry
            if not isinstance(item_id, str) or not re.fullmatch(r"MLB\d+", item_id):
                _erro(502, "sku_ads_invalid_response", "O Mercado Livre retornou uma identificação de anúncio inválida.")
            if item_id in ids:
                _erro(409, "sku_ads_catalog_changed", "Os anúncios mudaram durante a paginação. Tente novamente.")
            ids.append(item_id)
        offset += len(results)
        if offset >= total:
            return sorted(ids)
    _erro(503, "sku_ads_search_limit", "A consulta atingiu o limite de páginas dos anúncios deste SKU.")


def buscar_anuncios_sku_da_lista(client_id: str, lista: dict, sku: str) -> dict:
    """Read the selected account only; publish at most five verified own ads."""
    target = _sku_texto(sku)
    store_id = str((lista or {}).get("store_id") or "").strip()
    if not store_id:
        _erro(409, "sku_ads_store_required", "A lista precisa identificar sua loja para consultar anúncios próprios.")
    if not target or not any(isinstance(item, dict) and _sku_texto(item.get("SKU", item.get("sku"))) == target
                             for item in lista.get("itens") or []):
        _erro(404, "sku_ads_sku_not_in_list", "O SKU não pertence a esta lista de importação.")
    rows = [row for row in integracoes.ler_lojas(client_id) if isinstance(row, dict)
            and str(row.get("store_id") or "") == store_id]
    if len(rows) != 1:
        _erro(403, "sku_ads_store_not_authorized", "A loja desta lista não está autorizada nesta consulta.")
    row = rows[0]
    provider = (row.get("integracoes") or {}).get("mercadolivre") or {}
    if not isinstance(provider, dict):
        _erro(409, "sku_ads_identity_unconfirmed", "Confirme a conexão da conta desta lista.")
    scope = {"store_id": store_id, "loja": str(row.get("nome") or "").strip(),
             "seller_id": str(provider.get("user_id") or "").strip()}
    if "mercadolivre" in (row.get("_unavailable_providers") or []):
        _erro(503, "sku_ads_store_unavailable", "A conexão da loja está sendo atualizada. Tente novamente.")
    if provider.get("connected") is False or provider.get("oauth_invalid") is True:
        _erro(401, "sku_ads_connection_unavailable", "Confira a conexão desta loja ao Mercado Livre.")
    if not scope["loja"] or not scope["seller_id"] or str(provider.get("site_id") or row.get("site_id") or "") != "MLB":
        _erro(409, "sku_ads_identity_unconfirmed", "Confirme a identidade e o site da conta desta lista.")
    try:
        with read_budget(TEMPO_TOTAL_SEGUNDOS):
            candidates = _buscar_ids(client_id, scope, target)
            ads = []
            for item_id in candidates:
                response = _get(client_id, scope, "items/" + item_id, params={"include_attributes": "all"})
                if getattr(response, "status_code", None) == 404:
                    continue
                _exigir_resposta(response)
                item = _json(response)
                if (not isinstance(item, dict) or item.get("id") != item_id or item.get("site_id") != "MLB"
                        or str(item.get("seller_id") or "") != scope["seller_id"]):
                    _erro(409, "sku_ads_item_identity_mismatch", "O Mercado Livre não confirmou o anúncio desta conta e site.")
                if item.get("status") != "active" or not anuncio_corresponde_sku_exato(item, target):
                    continue
                price = _preco(item)
                url = _url(item, item_id)
                if price is None or url is None:
                    continue
                ads.append({"item_id": item_id, "preco": price, "url": url,
                            "seller_id": scope["seller_id"], "site_id": "MLB", "sku": target})
            for ad in ads[:LIMITE_ANUNCIOS]:
                response = _get(client_id, scope, f"items/{ad['item_id']}/sale_price",
                                params={"context": "channel_marketplace"})
                _exigir_resposta(response)
                sale = _json(response)
                if not isinstance(sale, dict):
                    _erro(502, "sku_ads_invalid_response", "O Mercado Livre retornou um preço de venda inválido.")
                # The item price is only a listing check. A missing sale price cannot quote a margin.
                ad["preco"] = _preco({"price": sale.get("amount"), "currency_id": sale.get("currency_id")})
            _cfg_atual(client_id, scope)  # Recheck authorization after the last network response.
            return {"store_id": store_id, "loja": scope["loja"], "anuncios": ads[:LIMITE_ANUNCIOS],
                    "total_encontrado": len(ads), "total_candidatos": len(candidates),
                    "limite": LIMITE_ANUNCIOS, "truncado": len(ads) > LIMITE_ANUNCIOS}
    except requests.Timeout:
        _erro(504, "sku_ads_timeout", "Tempo limite ao consultar os anúncios próprios deste SKU.")
    except requests.RequestException:
        _erro(503, "sku_ads_network_unavailable", "A conexão com o Mercado Livre está indisponível. Tente novamente.")
