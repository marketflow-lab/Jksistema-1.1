"""Deterministic technical projection of a resolved, exact-store catalog row.

Source prose is reference data, never parsed into technical assertions. Only
explicit technical columns enter this projection; operational columns do not.
"""
from __future__ import annotations

import json
import re
import hashlib
from collections.abc import Mapping
from urllib.parse import urlsplit

from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.store_sku_contracts import content_sha256, normalize_sku


FIELDS = {
    "name": ("Nome", ("nome", "produto_bling", "nome_bling", "produto", "titulo")),
    "description": ("Descrição", ("descricao", "descricao_curta_bling")),
    "complementary_description": ("Descrição complementar", ("descricao_complementar_bling",)),
    "brand": ("Marca", ("marca", "marca_bling")),
    "category": ("Categoria", ("categoria", "categoria_bling")),
    "gtin": ("GTIN", ("gtin", "gtin_bling", "ean")),
    "package_gtin": ("GTIN da embalagem", ("gtin_embalagem_bling",)),
    "oem": ("Código OEM", ("oem", "codigo_oem")),
    "manufacturer_code": ("Código do fabricante", ("codigo_fabricante", "codigo_fabricante_bling")),
    "model": ("Modelo", ("modelo", "modelo_bling")),
    "unit": ("Unidade", ("unidade", "unidade_bling")),
    "width": ("Largura", ("largura", "largura_bling")),
    "height": ("Altura", ("altura", "altura_bling")),
    "depth": ("Profundidade", ("profundidade", "profundidade_bling")),
    "dimension_unit": ("Unidade das dimensões", ("unidade_medida_dimensoes_bling",)),
    "net_weight": ("Peso líquido", ("peso_liquido", "peso_liquido_bling")),
    "gross_weight": ("Peso bruto", ("peso_bruto", "peso_bruto_bling")),
}
_FORBIDDEN = re.compile(r"pre[cç]o|price|estoque|stock|cust[oa]|cost|imposto|tax|cest|ncm|cpf|cnpj|email|telefone|token|senha", re.I)


def _scalar(value):
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return ""
    return str(value).strip()


def _images(row, scope):
    result = []
    for field in ("foto", "imagem_url_bling", "imagens_bling"):
        value = row.get(field)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                value = [value]
        if not isinstance(value, list):
            value = [value]
        for item in value:
            url = _scalar(item.get("link") or item.get("url")) if isinstance(item, dict) else _scalar(item)
            try:
                parsed = urlsplit(url)
                safe = parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password
                # Signed/private URLs and local filesystem references are not
                # transported into the editorial vault.
                safe = safe and not parsed.query and not parsed.fragment
            except ValueError:
                safe = False
            local_prefix = "cadastro_fotos/lojas/sid-" + hashlib.sha256(scope.store_ref.encode()).hexdigest() + "/"
            if url.startswith(local_prefix) and not any(part in {"", ".", ".."} for part in url.split("/")) and "\\" not in url and ":" not in url and "?" not in url and "#" not in url:
                safe = True
            if safe and url not in result:
                result.append(url)
    return result


def project_catalog_product(scope, row):
    if not isinstance(row, Mapping):
        raise ContextHubValidationError("catalog_row_invalid")
    if str(row.get("store_id") or "") != scope.store_ref:
        raise ContextHubValidationError("catalog_store_mismatch")
    sku = normalize_sku(row.get("sku"))
    fields, sources, conflicts = {}, {}, []
    for name, (_, aliases) in FIELDS.items():
        available = [(key, _scalar(row.get(key))) for key in aliases if _scalar(row.get(key))]
        if available:
            fields[name] = available[0][1]
            sources[name] = available[0][0]
            for key, value in available[1:]:
                if value != available[0][1]:
                    conflicts.append({"field": name, "source_field": key, "value": value})
    images = _images(row, scope)
    if images:
        fields["images"] = images
        sources["images"] = "catalog_image_columns"
    # Only an explicitly structured characteristics column is accepted. Free
    # text and Bling custom/internal fields are never mined for inferred facts.
    explicit = row.get("caracteristicas") or row.get("characteristics")
    if isinstance(explicit, str):
        try:
            explicit = json.loads(explicit)
        except ValueError:
            explicit = None
    if isinstance(explicit, Mapping):
        attributes = {str(key).strip(): _scalar(value) for key, value in explicit.items()
                      if str(key).strip() and not _FORBIDDEN.search(str(key)) and _scalar(value)}
        if attributes:
            fields["attributes"] = attributes
            sources["attributes"] = "caracteristicas"
    identity = {key: getattr(scope, key) for key in ("tenant_scope", "store_ref", "seller_id", "site_id")}
    document = {"schema": "jk_store_catalog_product_v1", "sku": sku, "identity": identity,
                "fields": fields, "field_sources": sources, "source_conflicts": conflicts,
                "source": {"kind": "store_catalog", "store_ref": scope.store_ref,
                           "scope_source": str(row.get("scope_source") or "store_file")},
                "trust": "untrusted_reference_data"}
    return document


def catalog_characteristics(document):
    result = []
    fields = document.get("fields") or {}
    revision = content_sha256(document)
    for field, value in fields.items():
        if field == "images":
            continue
        values = value.items() if field == "attributes" and isinstance(value, dict) else [(None, value)]
        for attribute, current in values:
            path = field + ("." + attribute if attribute is not None else "")
            label = attribute if attribute is not None else FIELDS.get(field, (field,))[0]
            key = "catalog:" + content_sha256([document["identity"], document["sku"], path])[:32]
            result.append({"key": key, "label": label, "value": str(current),
                           "original_value": str(current), "source": "catalog", "field": path,
                           "source_revision": revision,
                           "source_field": (document.get("field_sources") or {}).get(field, "")})
    return result
