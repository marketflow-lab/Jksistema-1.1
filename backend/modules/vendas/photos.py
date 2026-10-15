"""Optional, read-only photo metadata for the new Vendas presentation."""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit


PHOTO_HEADER = "X-JK-Vendas-Fotos"


def _project_photos(client_id: str, loja: str, skus: tuple[str, ...]) -> dict:
    # Keep Cadastro's filesystem and runtime dependencies out of the old path.
    from backend.services.cadastro_compatibilidade import visao_produtos_cadastro_contexto_loja

    return visao_produtos_cadastro_contexto_loja(client_id, loja, skus=skus)


def _photo_reference(value: object, client_id: str, store_id: str) -> str:
    """Expose only a scoped local reference or a plain HTTP image reference."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 8192 or any(ord(char) < 32 for char in raw) or "\\" in raw:
        return ""
    from backend.services import cadastro_fotos

    if cadastro_fotos._cadastro_foto_referencia_local_cadastro(raw):
        parts = cadastro_fotos._cadastro_foto_partes_loja_referencia_local(raw, client_id)
        if not parts or len(parts) != 3 or parts[0].casefold() != "lojas":
            return ""
        expected = "sid-" + hashlib.sha256(store_id.encode("utf-8")).hexdigest()
        if parts[1] not in {expected, store_id}:
            return ""
        if parts[1] == store_id and not cadastro_fotos._cadastro_store_id_foto_legado_permitido(client_id, store_id):
            return ""
        return "cadastro_fotos/" + "/".join(parts)
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        parsed.port
    except ValueError:
        return ""
    return raw


def enrich_summary_photos(payload: object, client_id: str, loja: str | None) -> object:
    """Copy a financial result and add display metadata without changing totals.

    The transport invokes this only after an explicit photo header. Cached
    financial objects remain untouched, and a catalog failure never prevents
    viewing sales. A global SKU does not identify a store photo.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        return payload
    result = dict(payload)
    rows = [dict(row, foto="", store_id="") if isinstance(row, dict) else row for row in payload["rows"]]
    result["rows"] = rows
    result["photo_client_id"] = str(client_id or "").strip()
    reference = str(loja or "").strip()
    if not rows or not result["photo_client_id"]:
        return result
    skus = tuple(dict.fromkeys(str(row.get("sku") or "").strip() for row in rows if isinstance(row, dict) and str(row.get("sku") or "").strip()))
    if not skus:
        return result
    try:
        projection = _project_photos(result["photo_client_id"], reference, skus)
        store_id = str(projection.get("store_id") or "").strip()
        scope = projection.get("scope")
        if not projection.get("loja_resolvida") or scope not in {"store", "global_safe"} or (scope == "store" and not store_id):
            return result
        by_sku: dict[str, dict | None] = {}
        for product in projection.get("produtos") or []:
            if not isinstance(product, dict):
                continue
            product_store = str(product.get("store_id") or "").strip()
            if not product_store or (scope == "store" and product_store != store_id):
                continue
            if scope == "global_safe" and product.get("store_scope_ambiguous") is not False:
                continue
            key = str(product.get("sku") or "").strip().casefold()
            if not key:
                continue
            by_sku[key] = None if key in by_sku else product
        for row in rows:
            if not isinstance(row, dict):
                continue
            product = by_sku.get(str(row.get("sku") or "").strip().casefold())
            if product is None:
                continue
            photo_store = str(product.get("store_id") or "").strip()
            row["store_id"] = photo_store
            row["foto"] = _photo_reference(product.get("foto"), result["photo_client_id"], photo_store)
    except Exception:
        # This optional display projection must not change the financial path.
        for row in rows:
            if isinstance(row, dict):
                row.update(foto="", store_id="")
    return result
