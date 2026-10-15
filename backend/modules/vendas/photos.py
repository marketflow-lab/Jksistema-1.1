"""Optional, read-only photo metadata for the new Vendas presentation."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import os
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit


PHOTO_HEADER = "X-JK-Vendas-Fotos"
_PHOTO_METADATA = {"photo_scope", "photo_store_name", "photo_ambiguous"}


def _sku_key(value: object) -> str:
    from backend.services.cadastro_common import _normalizar_sku_mes

    return _normalizar_sku_mes(str(value or "").strip()).casefold()


def _all_stores(value: object) -> bool:
    return str(value or "").strip().casefold() in {"", "__todas", "todas", "todas as lojas"}


def _product_name(product: dict) -> str:
    for field in ("produto", "produto_bling", "nome", "titulo_ml", "nome_bling", "descricao"):
        value = re.sub(r"\s+", " ", str(product.get(field) or "").strip()).casefold()
        if value and value != "-":
            return value
    return ""


def _aggregate_photo_projection(client_id: str, skus: tuple[str, ...]) -> dict:
    """Read individual photo provenance without relaxing Cadastro's global view."""
    from backend.services import cadastro_lojas_produtos as catalog

    requested = {_sku_key(sku) for sku in skus}
    path = catalog._cadastro_produtos_lojas_path(client_id)
    with catalog._bloquear_config_e_arquivo_cadastro(client_id, path):
        records, _ = catalog._ler_registros_persistidos(client_id)
        current = catalog._lojas_atuais(client_id)
        counts = Counter(str(store.get("store_id") or "").strip() for store in current)
        stores = [
            {key: store[key] for key in ("store_id", "nome", "nomes_anteriores") if key in store}
            for store in current
            if str(store.get("store_id") or "").strip()
            and counts[str(store.get("store_id") or "").strip()] == 1
        ]
        active_ids = {str(store["store_id"]).strip() for store in stores}
        controlled = {
            _sku_key(record.get("sku_normalizado") or record.get("sku"))
            for record in records
            if _sku_key(record.get("sku_normalizado") or record.get("sku"))
        }
        canonical = defaultdict(list)
        for record in records:
            key = _sku_key(record.get("sku_normalizado") or record.get("sku"))
            if key in requested and record.get("store_id") in active_ids and not str(record.get("deleted_at_utc") or "").strip():
                canonical[key].append(record)
        canonical_names = {
            key: _product_name(products[0]) for key, products in canonical.items()
            if all(_product_name(product) for product in products)
            and len({_product_name(product) for product in products}) == 1
        }
        consistent = set(canonical_names)
        products = []
        for store in stores:
            store_id = str(store["store_id"]).strip()
            products.extend(
                dict(product)
                for product in catalog._listar_produtos_loja_sync(
                    client_id, store_id, skus=skus, _registros_persistidos=records,
                )
                if product.get("store_id") == store_id
            )
        for product in products:
            key = _sku_key(product.get("sku"))
            if key in consistent and _product_name(product) != canonical_names[key]:
                # Legacy shadows remain separate identities: a canonical name
                # alone cannot authorize a different projected product.
                consistent.discard(key)
    return {
        "scope": "aggregate_photos", "loja_resolvida": True, "store_id": "",
        "produtos": products, "lojas": stores, "skus_controlados": controlled,
        "photo_identity_consistent": consistent,
    }


def _project_photos(client_id: str, loja: str, skus: tuple[str, ...]) -> dict:
    # Keep Cadastro's filesystem and runtime dependencies out of the old path.
    if _all_stores(loja):
        return _aggregate_photo_projection(client_id, skus)
    from backend.services.cadastro_compatibilidade import visao_produtos_cadastro_contexto_loja

    return visao_produtos_cadastro_contexto_loja(client_id, loja, skus=skus)


def _tenant_photo_reference(raw: str, client_id: str) -> str:
    """Canonicalize an existing legacy image from this tenant, never default."""
    from backend.services import cadastro_fotos

    try:
        parsed = urlsplit(raw)
        if parsed.username is not None or parsed.password is not None:
            return ""
        path = parsed.path
        for _ in range(3):
            decoded = unquote(path)
            if decoded == path:
                break
            path = decoded
        if "\\" in path or any(ord(char) < 32 for char in path):
            return ""
        tenant_prefix = "/api/cadastro/foto/"
        file_prefix = "/api/cadastro/foto-arquivo/"
        if path.casefold().startswith(tenant_prefix):
            parts = path[len(tenant_prefix):].split("/")
            # Verify the declared tenant before extracting a basename.
            if len(parts) != 2 or parts[0] != client_id:
                return ""
            name = parts[1]
        elif path.casefold().startswith(file_prefix):
            name = path[len(file_prefix):]
        elif path.casefold().startswith("cadastro_fotos/"):
            name = path.split("/", 1)[1]
        elif not parsed.scheme and not path.startswith("/"):
            name = path
        else:
            return ""
        if not name or "/" in name or name in {".", ".."} or Path(name).suffix.lower() not in cadastro_fotos.CADASTRO_FOTOS_EXTENSOES:
            return ""
        cadastro_fotos._cadastro_resolver_arquivo_foto(client_id, name, tenant_only=True)
    except Exception:
        return ""
    return f"/api/cadastro/foto/{quote(client_id, safe='')}/{quote(name, safe='')}?tenant_only=true"


def _photo_reference(value: object, client_id: str, store_id: str) -> str:
    """Expose exact store references or explicitly tenant-only legacy images."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 8192 or any(ord(char) < 32 for char in raw) or "\\" in raw:
        return ""
    from backend.services import cadastro_fotos

    if cadastro_fotos._cadastro_foto_referencia_local_cadastro(raw):
        parts = cadastro_fotos._cadastro_foto_partes_loja_referencia_local(raw, client_id)
        if parts is None:
            return _tenant_photo_reference(raw, client_id)
        if not store_id or len(parts) != 3 or parts[0].casefold() != "lojas":
            return ""
        expected = "sid-" + hashlib.sha256(store_id.encode("utf-8")).hexdigest()
        if parts[1] not in {expected, store_id}:
            return ""
        if parts[1] == store_id and not cadastro_fotos._cadastro_store_id_foto_legado_permitido(client_id, store_id):
            return ""
        if not parts[2] or parts[2] in {".", ".."} or ":" in parts[2]:
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


def _tenant_root_photos(client_id: str, skus: tuple[str, ...]) -> dict[str, str]:
    """Find exact legacy SKU filenames only below the authenticated tenant."""
    from backend.services import cadastro_fotos

    if cadastro_fotos._cadastro_fotos_escopo_estrito(client_id):
        return {}
    tenant = cadastro_fotos._cadastro_tenant_path_fotos_seguro(client_id)
    if not tenant:
        return {}
    folder = os.path.join(tenant, "cadastro_fotos")
    if not cadastro_fotos._cadastro_diretorio_seguro_abaixo(tenant, folder) or not os.path.isdir(folder):
        return {}
    requested = {_sku_key(sku) for sku in skus}
    result = {}
    for name in sorted(os.listdir(folder), key=lambda item: (Path(item).suffix.lower() != ".png", item.casefold())):
        key = _sku_key(Path(name).stem)
        if key in requested and key not in result and Path(name).suffix.lower() in cadastro_fotos.CADASTRO_FOTOS_EXTENSOES:
            reference = _tenant_photo_reference(f"cadastro_fotos/{name}", client_id)
            if reference:
                result[key] = reference
    return result


def _photo_metadata(product: dict, client_id: str, store_names: dict[str, str], *, controlled: bool = False) -> dict:
    store_id = str(product.get("store_id") or "").strip()
    reference = _photo_reference(product.get("foto"), client_id, store_id)
    if not reference:
        return {}
    if reference.startswith("/api/cadastro/foto/"):
        if controlled and product.get("scope_source") == "legacy_shadow":
            # A historical global row cannot resurrect a store-owned SKU,
            # including a tombstone normalized from a legacy SKU alias.
            return {}
        return {"foto": reference, "store_id": "", "photo_scope": "tenant"}
    result = {"foto": reference, "store_id": store_id}
    name = store_names.get(store_id) or str(product.get("loja_sync") or "").strip()
    if name:
        result["photo_store_name"] = name
    return result


def _apply_aggregate(rows: list, client_id: str, projection: dict, skus: tuple[str, ...]) -> None:
    from backend.services.cadastro_compatibilidade import resolver_loja_ativa_para_leitura

    stores = projection.get("lojas") or []
    names = {str(store.get("store_id") or "").strip(): str(store.get("nome") or "").strip() for store in stores if isinstance(store, dict)}
    active_ids = set(names) - {""}
    products = defaultdict(list)
    for product in projection.get("produtos") or []:
        if isinstance(product, dict) and str(product.get("store_id") or "").strip() in active_ids:
            products[_sku_key(product.get("sku"))].append(product)
    controlled = {_sku_key(sku) for sku in projection.get("skus_controlados") or []}
    consistent = {_sku_key(sku) for sku in projection.get("photo_identity_consistent") or []}
    legacy = tuple(sku for sku in skus if _sku_key(sku) not in controlled)
    roots = _tenant_root_photos(client_id, legacy) if legacy else {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _sku_key(row.get("sku"))
        candidates = products.get(key, [])
        origin = str(row.get("loja_conta") or "").strip()
        identity = resolver_loja_ativa_para_leitura(client_id, origin, origin if origin in active_ids else "", lojas=stores) if origin else {}
        origin_id = str(identity.get("store_id") or "").strip() if identity.get("loja_resolvida") else ""
        matching = [product for product in candidates if product.get("store_id") == origin_id]
        if len(matching) == 1:
            product = matching[0]
            decisive_name = bool(_product_name(row) and _product_name(row) == _product_name(product))
            if key not in controlled or key in consistent or decisive_name:
                metadata = _photo_metadata(product, client_id, names, controlled=key in controlled)
                if metadata:
                    row.update(metadata)
                    continue
        available = [metadata for product in candidates if (metadata := _photo_metadata(product, client_id, names, controlled=key in controlled))]
        # Cross-store representation is restricted to an aggregate with one
        # canonical product identity. Specific stores never reach this path.
        unique = {metadata["foto"] for metadata in available}
        if key in consistent and len(unique) == 1 and available:
            row.update(available[0])
        elif key in roots:
            row.update(foto=roots[key], store_id="", photo_scope="tenant")
        elif available:
            row["photo_ambiguous"] = True


def enrich_summary_photos(payload: object, client_id: str, loja: str | None) -> object:
    """Copy cached sales and add optional provenance without changing totals."""
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        return payload
    result = dict(payload)
    rows = [dict(row, foto="", store_id="") if isinstance(row, dict) else row for row in payload["rows"]]
    for row in rows:
        if isinstance(row, dict):
            for key in _PHOTO_METADATA:
                row.pop(key, None)
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
        if not projection.get("loja_resolvida"):
            return result
        if scope == "aggregate_photos" and _all_stores(reference):
            _apply_aggregate(rows, result["photo_client_id"], projection, skus)
            return result
        if scope not in {"store", "global_safe"} or (scope == "store" and not store_id):
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
            key = _sku_key(product.get("sku"))
            if key:
                by_sku[key] = None if key in by_sku else product
        controlled = {_sku_key(sku) for sku in projection.get("skus_controlados") or []}
        legacy = tuple(sku for sku in skus if _sku_key(sku) not in controlled)
        roots = _tenant_root_photos(result["photo_client_id"], legacy) if legacy else {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = _sku_key(row.get("sku"))
            product = by_sku.get(key)
            if product is not None:
                metadata = _photo_metadata(product, result["photo_client_id"], {}, controlled=key in controlled)
                if metadata:
                    row.update(metadata)
                else:
                    row["store_id"] = str(product.get("store_id") or "").strip()
            elif key in roots:
                row.update(foto=roots[key], store_id="", photo_scope="tenant")
    except Exception:
        # A catalog failure cannot mutate or prevent the financial result.
        for row in rows:
            if isinstance(row, dict):
                row.update(foto="", store_id="")
                for key in _PHOTO_METADATA:
                    row.pop(key, None)
    return result
