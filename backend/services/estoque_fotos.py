"""Request-local photo projection for Estoque, without catalog imports or writes."""

from __future__ import annotations

import csv
import os
from collections import Counter, defaultdict
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlsplit

from fastapi import HTTPException

from backend.services import cadastro_fotos
from backend.services.cadastro_common import _normalizar_sku_mes


def _sku_chave(value: Any) -> str:
    return _normalizar_sku_mes(str(value or "").strip()).upper()


def _foto_url(client_id: str, store_id: str, reference: Any, *, explicit_scope: bool = False) -> str:
    """Keep authenticated local photos in scope and accept plain HTTP images."""
    raw = cadastro_fotos._cadastro_foto_referencia_limpar_wrappers(reference)
    if not raw or len(raw) > 8192 or any(ord(char) < 32 for char in raw):
        return ""
    if cadastro_fotos._cadastro_foto_referencia_local_cadastro(raw):
        parts = cadastro_fotos._cadastro_foto_partes_loja_referencia_local(raw, client_id)
        legacy_root = parts is None and explicit_scope
        if legacy_root:
            # Root images are accepted only when the scoped Cadastro row
            # explicitly associates them. Never infer this from a global SKU.
            local = raw.replace("\\", "/")
            for _ in range(3):
                decoded = unquote(local)
                if decoded == local:
                    break
                local = decoded.replace("\\", "/")
            try:
                parsed = urlsplit(local)
            except ValueError:
                return ""
            local = parsed.path
            tenant_prefix = "/api/cadastro/foto/"
            file_prefix = "/api/cadastro/foto-arquivo/"
            if local.lower().startswith(tenant_prefix):
                local_tenant, separator, local = local[len(tenant_prefix):].partition("/")
                if not separator or local_tenant != client_id:
                    return ""
            elif local.lower().startswith(file_prefix):
                local = local[len(file_prefix):]
            if local.lower().startswith("cadastro_fotos/"):
                local = local.split("/", 1)[1]
            if not local or "/" in local or ":" in local:
                return ""
            parts = [local]
        elif not parts or len(parts) != 3:
            return ""
        filename = "/".join(parts)
        if os.path.splitext(parts[-1])[1].lower() not in cadastro_fotos.CADASTRO_FOTOS_EXTENSOES:
            return ""
        try:
            cadastro_fotos._cadastro_resolver_arquivo_foto(
                client_id, filename, store_id, tenant_only=True,
            )
        except (HTTPException, ValueError, OSError):
            return ""
        path = "/".join(quote(part, safe="") for part in parts)
        query = {"store_id": store_id}
        if legacy_root:
            query["tenant_only"] = "true"
        return f"/api/cadastro/foto/{quote(client_id, safe='')}/{path}?{urlencode(query)}"
    try:
        parsed = urlsplit(raw)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "\\" in raw
        ):
            return ""
        parsed.port  # Validate the port without contacting the image host.
    except ValueError:
        return ""
    return raw


def _cadastro_fotos_por_chave(tenant_path: str) -> dict[tuple[str, str], str | None]:
    """Read only the scoped Cadastro file; duplicate/deleted keys fail closed."""
    path = os.path.join(tenant_path, "cadastro_produtos_lojas.csv")
    if cadastro_fotos._cadastro_caminho_e_link(path) or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(8192)
            handle.seek(0)
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
            except csv.Error:
                delimiter = ";" if sample.splitlines()[0].count(";") > sample.splitlines()[0].count(",") else ","
            reader = csv.DictReader(handle, delimiter=delimiter, strict=True)
            fields = [str(field or "").strip().lower() for field in (reader.fieldnames or [])]
            if len(fields) != len(set(fields)) or any(not field for field in fields):
                return {}
            photos: dict[tuple[str, str], str | None] = {}
            for original in reader:
                if None in original:
                    return {}
                row = {str(key).strip().lower(): value for key, value in original.items()}
                store_id = str(row.get("store_id") or "").strip()
                sku = _sku_chave(row.get("sku_normalizado") or row.get("sku"))
                if not store_id or not sku:
                    continue
                key = (store_id, sku)
                invalid_sku = row.get("sku") and _sku_chave(row["sku"]) != sku
                if key in photos or row.get("deleted_at_utc") or invalid_sku:
                    photos[key] = None
                else:
                    photos[key] = str(row.get("foto") or "").strip()
            return photos
    except (OSError, UnicodeError, csv.Error, IndexError):
        return {}


def _fotos_locais_da_loja(client_id: str, tenant_path: str, store_id: str) -> dict[str, str]:
    segment = cadastro_fotos._cadastro_store_id_foto_segmento(store_id)
    segments = [segment]
    if (
        not cadastro_fotos._cadastro_fotos_escopo_estrito(client_id)
        and cadastro_fotos._cadastro_store_id_foto_legado_permitido(client_id, store_id)
    ):
        segments.append(store_id)
    photos: dict[str, str] = {}
    for segment in segments:
        folder = os.path.join(tenant_path, "cadastro_fotos", "lojas", segment)
        if not cadastro_fotos._cadastro_diretorio_seguro_abaixo(tenant_path, folder):
            continue
        try:
            filenames = sorted(os.listdir(folder), key=lambda name: (not name.lower().endswith(".png"), name))
        except OSError:
            continue
        for name in filenames:
            stem, extension = os.path.splitext(name)
            if extension.lower() not in cadastro_fotos.CADASTRO_FOTOS_EXTENSOES:
                continue
            photos.setdefault(_sku_chave(stem), f"cadastro_fotos/lojas/{segment}/{name}")
    return photos


def anexar_fotos_estoque(client_id: str, records: list[dict[str, Any]]) -> None:
    """Add display metadata only after an exact tenant/store/SKU resolution."""
    for row in records:
        row["__estoque_foto"] = ""
    if not records:
        return
    try:
        tenant_path = cadastro_fotos._cadastro_tenant_path_fotos_seguro(client_id)
        if not tenant_path:
            return
        stores = cadastro_fotos._cadastro_carregar_lojas_foto(client_id)
        id_counts = Counter(str(store.get("store_id") or "").strip() for store in stores)
        ids = {key for key, count in id_counts.items() if key and count == 1}
        names: dict[str, list[str]] = defaultdict(list)
        for store in stores:
            store_id = str(store.get("store_id") or "").strip()
            name = str(store.get("nome") or "").strip()
            if name:
                names[name].append(store_id)
        scoped_photos = _cadastro_fotos_por_chave(tenant_path)
        local_photos: dict[str, dict[str, str]] = {}
        urls: dict[tuple[str, str, bool], str] = {}
        for row in records:
            store_id = str(row.get("store_id") or "").strip()
            if not store_id:
                candidates = names.get(str(row.get("loja_sync") or row.get("loja") or "").strip(), [])
                store_id = candidates[0] if len(candidates) == 1 else ""
            sku = _sku_chave(row.get("sku"))
            if store_id not in ids or not sku:
                continue
            key = (store_id, sku)
            if key in scoped_photos and scoped_photos[key] is None:
                continue
            if store_id not in local_photos:
                local_photos[store_id] = _fotos_locais_da_loja(client_id, tenant_path, store_id)
            # Match Cadastro's precedence: a current file in this exact store
            # wins over a stale saved reference; tombstones were excluded above.
            candidates = (
                (local_photos[store_id].get(sku, ""), False),
                (scoped_photos.get(key, ""), True),
            )
            for reference, explicit_scope in candidates:
                if not reference:
                    continue
                url_key = (store_id, reference, explicit_scope)
                if url_key not in urls:
                    urls[url_key] = _foto_url(client_id, store_id, reference, explicit_scope=explicit_scope)
                if urls[url_key]:
                    row["__estoque_foto"] = urls[url_key]
                    break
    except (HTTPException, ValueError, OSError):
        # A photo/config issue must not prevent reading the stock quantities.
        return
