"""Two-phase, store-scoped catalog imports for Cadastro.

External providers are collected in a background, read-only preview.  The
preview is kept server-side and can only be applied by the same tenant and
store.  Applying creates missing SKUs and fills empty fields.  Existing Bling
SKUs are the deliberate exception: only their Bling title plus canonical/source
NCM and CEST fields are refreshed, while every other existing value is kept.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import hashlib
import io
import json
import math
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import requests
from fastapi import Depends, Header, HTTPException, Request
from PIL import Image, ImageOps, UnidentifiedImageError

from backend.services import cadastro_mercadolivre as cadastro_ml
from backend.services import integracoes
from backend.services.cadastro_catalogo_common import (
    configuracao_catalogo_aplicacao_fingerprint,
    configuracao_catalogo_fingerprint,
)
from backend.services.cadastro_lojas_produtos import (
    _fingerprint_sombra_legada,
    _listar_produtos_loja_sync,
    _normalizar_sku_chave,
    resolver_loja_cadastro,
    salvar_produtos_loja_em_lote,
)
from backend.services.runtime_bridge import bind_runtime_globals


async def get_tenant_id(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    raise RuntimeError("Cadastro catalog import runtime was not configured.")


def configure_cadastro_importacao_catalogos_runtime(runtime_module=None):
    runtime = bind_runtime_globals(globals(), runtime_module)
    if runtime is not None and hasattr(runtime, "get_tenant_id"):
        globals()["get_tenant_id"] = getattr(runtime, "get_tenant_id")
    return runtime


configure_cadastro_importacao_catalogos_runtime()


async def _get_tenant_id_dependency(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    """Resolve the runtime dependency at request time, after facade binding."""

    return await get_tenant_id(request, authorization)


def _requester_fingerprint(request: Request, client_id: str) -> str:
    state = getattr(request, "state", None)
    username = str(getattr(state, "username", "") or "").strip()
    if not username:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "catalog_requester_missing",
                "message": "Nao foi possivel identificar o usuario desta importacao.",
            },
        )
    return hashlib.sha256(f"{client_id}\0{username}".encode("utf-8")).hexdigest()


CATALOG_IMPORT_JOBS: dict[str, dict[str, Any]] = {}
CATALOG_IMPORT_JOB_TTL_SECONDS = 2 * 60 * 60
# A coleta mais longa (Mercado Livre) pode ocupar ate 8 horas.  O lease de
# inatividade precisa ultrapassar esse limite para que uma consulta/poll tardio
# nao reclassifique um worker ainda valido antes do proprio deadline do coletor.
CATALOG_IMPORT_ACTIVE_TTL_SECONDS = 9 * 60 * 60
CATALOG_IMPORT_MAX_JOBS = 80
CATALOG_IMPORT_MAX_ACTIVE_JOBS = 8
CATALOG_IMPORT_MAX_ACTIVE_JOBS_PER_TENANT = 2
CATALOG_IMPORT_MAX_ACTIVE_BLING_JOBS = 1
CATALOG_IMPORT_MAX_PROVIDER_ITEMS = 25_000
CATALOG_IMPORT_PREVIEW_MAX_ROWS = 500
# Catalog cover downloads are prepared in memory before the existing atomic
# batch writer starts.  Keep a bounded aggregate so a very large catalog can
# still import its metadata without exhausting the desktop process.
CATALOG_IMPORT_PHOTO_APPLY_TIMEOUT_SECONDS = 30 * 60
CATALOG_IMPORT_PHOTO_MAX_DATA_URL_CHARS = 96 * 1024 * 1024
CATALOG_IMPORT_PHOTO_MAX_PIXELS = 24_000_000
CATALOG_IMPORT_PHOTO_MAX_EDGE_PX = 720
CATALOG_IMPORT_PHOTO_JPEG_QUALITY = 50
CATALOG_IMPORT_PHOTO_MAX_COMPRESSED_BYTES = 350 * 1024
_CATALOG_IMPORT_JOBS_LOCK = threading.RLock()
_ACTIVE_JOB_STATUSES = {"queued", "running", "applying"}

_SOURCE_INTEGRATION_KEYS = {
    "bling": "bling",
    "mercadolivre": "mercadolivre",
}

_BLING_FIELDS = {
    "id_bling",
    "id_produto_pai_bling",
    "produto_bling",
    "nome_bling",
    "situacao_bling",
    "tipo_bling",
    "formato_bling",
    "data_validade_bling",
    "tipo_producao_bling",
    "condicao_bling",
    "frete_gratis_bling",
    "action_estoque_bling",
    "linha_produto_bling",
    "artigo_perigoso_bling",
    "duns_bling",
    "ncm_bling",
    "cest_bling",
    "categoria_id_bling",
    "categoria_bling",
    "marca_bling",
    "gtin_bling",
    "gtin_embalagem_bling",
    "preco_bling",
    "custo_bling",
    "estoque_fisico_bling",
    "estoque_virtual_bling",
    "estoques_bling_json",
    "unidade_bling",
    "estoque_minimo_bling",
    "estoque_maximo_bling",
    "localizacao_bling",
    "cross_docking_bling",
    "crossdocking_bling",
    "peso_liquido_bling",
    "peso_bruto_bling",
    "largura_bling",
    "altura_bling",
    "profundidade_bling",
    "volumes_bling",
    "itens_por_caixa_bling",
    "descricao_curta_bling",
    "descricao_bling",
    "descricao_complementar_bling",
    "descricao_embalagem_discreta_bling",
    "observacoes_bling",
    "link_externo_bling",
    "imagem_url_bling",
    "imagens_bling_json",
    "imagens_bling",
    "video_bling",
    "fornecedor_bling_json",
    "tributacao_bling_json",
    "tributacao_bling",
    "variacoes_bling_json",
    "variacoes_bling",
    "componentes_bling_json",
    "estrutura_bling",
    "campos_customizados_bling_json",
    "campos_customizados_bling",
    "unidade_medida_dimensoes_bling",
    "dimensoes_bling",
    "consultado_em_utc_bling",
}

_ML_FIELDS = {
    "titulo_ml",
    "mlb_principal",
    "mlb_ids",
    "qtd_anuncios_mlb",
    "titulos_anuncios_mlb",
    "categoria",
    "categoria_id_mlb",
    "marca",
    "modelo",
    "gtins_mlb",
    "descricao",
    "preco_ml",
    "moeda_ml",
    "preco_base_ml",
    "preco_original_ml",
    "estoque_disponivel_ml",
    "vendidos_acumulados_ml",
    "termos_venda_ml_json",
    "envio_ml_json",
    "condicao_ml",
    "condicao_nome_ml",
    "garantia_ml",
    "criado_em_ml",
    "atualizado_em_ml",
    "canais_ml_json",
    "tags_ml_json",
    "familia_nome_ml",
    "familia_id_ml",
    "familia_ids_ml",
    "dominio_id_ml",
    "site_id_ml",
    "user_product_nome_ml",
    "site_id_user_product_ml",
    "catalog_product_id_user_product_ml",
    "catalog_product_ids_user_product_ml",
    "criado_em_user_product_ml",
    "atualizado_em_user_product_ml",
    "atributos_user_product_ml_json",
    "imagens_user_product_ml_json",
    "miniatura_user_product_ml_json",
    "miniatura_url_user_product_ml",
    "tags_user_product_ml_json",
    "bundle_user_product_ml_json",
    "estoque_user_product_total_ml",
    "estoque_localizacoes_ml_json",
    "estoque_multiorigem_ml",
    "anuncio_catalogo_ml",
    "modo_compra_ml",
    "status_ml",
    "variacao_id_ml",
    "variacao_ids_ml",
    "listing_type_ml",
    "catalog_product_id_ml",
    "catalog_product_ids_ml",
    "user_product_id_ml",
    "user_product_ids_ml",
    "inventory_id_ml",
    "inventory_ids_ml",
    "link_ml",
    "foto_url_ml",
    "imagens_ml_json",
    "atributos_ml_json",
    "anuncios_ml_json",
    "consultado_em_utc",
}

_ALLOWED_FIELDS = {
    "bling": _BLING_FIELDS,
    "mercadolivre": _ML_FIELDS,
}

_PRIVILEGED_DERIVED_FIELDS = {
    "bling": {"id_bling", "produto_bling", "nome_bling", "ncm_bling", "cest_bling"},
    "mercadolivre": set(),
}

_BLING_EXISTING_SYNC_GROUPS = {
    "produto_bling": {
        "sources": ("produto_bling", "nome_bling"),
        "targets": ("produto_bling", "nome_bling"),
    },
    "ncm_bling": {
        "sources": ("ncm_bling",),
        "targets": ("ncm_bling", "ncm"),
    },
    "cest_bling": {
        "sources": ("cest_bling",),
        "targets": ("cest_bling", "cest"),
    },
}

_BLING_EXISTING_FIELD_GROUP = {
    field_name: group_name
    for group_name, group in _BLING_EXISTING_SYNC_GROUPS.items()
    for field_name in (*group["sources"], *group["targets"])
}


def _agora_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fonte_valida(source: Any) -> str:
    fonte = str(source or "").strip().lower()
    if fonte not in _SOURCE_INTEGRATION_KEYS:
        raise HTTPException(
            status_code=404,
            detail={"code": "catalog_source_invalid", "message": "Fonte de catalogo invalida."},
        )
    return fonte


def _valor_preenchido(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _texto_comparacao(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _texto_preview(value: Any, limit: int = 500) -> str:
    text = _texto_comparacao(value)
    return text if len(text) <= limit else f"{text[:limit]}..."


def _detalhes_ignorado(item: Any) -> dict[str, Any]:
    """Keep only bounded identifiers useful to audit a skipped provider row."""

    if not isinstance(item, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("sku", "mlb", "variation_id", "entity"):
        value = _texto_preview(item.get(key), 160)
        if value:
            result[key] = value
    for key in ("candidates", "missing_fields"):
        values = item.get(key)
        if isinstance(values, (list, tuple)):
            safe_values = [
                _texto_preview(value, 160)
                for value in values[:20]
                if _texto_preview(value, 160)
            ]
            if safe_values:
                result[key] = safe_values
    external_ids = item.get("external_ids")
    if isinstance(external_ids, dict):
        safe_ids = {
            key: _texto_preview(external_ids.get(key), 160)
            for key in ("id_bling", "mlb", "variation_id")
            if _texto_preview(external_ids.get(key), 160)
        }
        if safe_ids:
            result["external_ids"] = safe_ids
    return result


def _configuracao_fonte(
    client_id: str,
    store_id: str,
    source: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    fonte = _fonte_valida(source)
    loja_resolvida = resolver_loja_cadastro(client_id, store_id)
    lojas = [
        dict(item)
        for item in (integracoes.carregar_lojas(client_id) or [])
        if isinstance(item, dict)
        and str(item.get("store_id") or "").strip() == loja_resolvida["store_id"]
    ]
    if len(lojas) != 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "store_config_changed",
                "message": "A configuracao da loja mudou durante a operacao.",
            },
        )
    integrations = lojas[0].get("integracoes")
    integrations = integrations if isinstance(integrations, dict) else {}
    config = integrations.get(_SOURCE_INTEGRATION_KEYS[fonte])
    if not isinstance(config, dict) or not config:
        nome = "Bling" if fonte == "bling" else "Mercado Livre"
        raise HTTPException(
            status_code=400,
            detail={
                "code": "catalog_source_not_configured",
                "message": f"{nome} nao esta configurado para a loja selecionada.",
            },
        )
    return loja_resolvida, copy.deepcopy(config)


def _configuracao_fingerprint(client_id: str, store_id: str, source: str) -> str:
    return _configuracao_fingerprints(client_id, store_id, source)[0]


def _configuracao_snapshot_fingerprint(
    loja: dict[str, Any],
    config: dict[str, Any],
    source: str,
) -> str:
    return configuracao_catalogo_fingerprint(
        source,
        loja["store_id"],
        loja["nome"],
        config,
    )


def _configuracao_aplicacao_fingerprint(
    client_id: str,
    store_id: str,
    source: str,
) -> str:
    return _configuracao_fingerprints(client_id, store_id, source)[1]


def _configuracao_fingerprints(
    client_id: str,
    store_id: str,
    source: str,
) -> tuple[str, str]:
    loja, config = _configuracao_fonte(client_id, store_id, source)
    return (
        _configuracao_snapshot_fingerprint(loja, config, source),
        _configuracao_aplicacao_snapshot_fingerprint(loja, config, source),
    )


def _configuracao_aplicacao_snapshot_fingerprint(
    loja: dict[str, Any],
    config: dict[str, Any],
    source: str,
) -> str:
    return configuracao_catalogo_aplicacao_fingerprint(
        source,
        loja["store_id"],
        loja["nome"],
        config,
    )


def _provider_for(source: str) -> Callable[..., dict[str, Any]]:
    if source == "bling":
        from backend.services.cadastro_catalogo_bling import coletar_catalogo_bling

        return coletar_catalogo_bling
    from backend.services.cadastro_catalogo_mercadolivre import (
        coletar_catalogo_mercadolivre,
    )

    return coletar_catalogo_mercadolivre


def _limpar_jobs_locked() -> None:
    agora = time.time()
    limite_ativo = agora - CATALOG_IMPORT_ACTIVE_TTL_SECONDS
    for job in CATALOG_IMPORT_JOBS.values():
        if (
            job.get("status") in {"queued", "running"}
            and float(job.get("updated_ts") or 0) < limite_ativo
        ):
            cancel_event = job.get("cancel_event")
            if cancel_event is not None and callable(getattr(cancel_event, "set", None)):
                cancel_event.set()
            job.update(
                status="error",
                can_apply=False,
                error={
                    "code": "catalog_job_timeout",
                    "message": "A importacao excedeu o tempo seguro. Gere uma nova previa.",
                },
                updated_ts=agora,
                updated_at_utc=_agora_utc(),
            )

    limite = agora - CATALOG_IMPORT_JOB_TTL_SECONDS
    removiveis = [
        job_id
        for job_id, job in CATALOG_IMPORT_JOBS.items()
        if float(job.get("updated_ts") or 0) < limite
        and job.get("status") not in _ACTIVE_JOB_STATUSES
    ]
    for job_id in removiveis:
        CATALOG_IMPORT_JOBS.pop(job_id, None)

    if len(CATALOG_IMPORT_JOBS) <= CATALOG_IMPORT_MAX_JOBS:
        return
    antigos = sorted(
        (
            (float(job.get("updated_ts") or 0), job_id)
            for job_id, job in CATALOG_IMPORT_JOBS.items()
            if job.get("status") not in _ACTIVE_JOB_STATUSES
        )
    )
    for _updated, job_id in antigos[: max(0, len(CATALOG_IMPORT_JOBS) - CATALOG_IMPORT_MAX_JOBS)]:
        CATALOG_IMPORT_JOBS.pop(job_id, None)


def _atualizar_job(job_id: str, **changes: Any) -> None:
    with _CATALOG_IMPORT_JOBS_LOCK:
        job = CATALOG_IMPORT_JOBS.get(job_id)
        if not job:
            return
        job.update(changes)
        job["updated_ts"] = time.time()
        job["updated_at_utc"] = _agora_utc()


def _progress_callback(job_id: str) -> Callable[..., None]:
    def callback(*args: Any, **kwargs: Any) -> None:
        payload = dict(args[0]) if len(args) == 1 and isinstance(args[0], dict) else {}
        if args and not payload:
            payload["stage"] = args[0]
        if len(args) > 1:
            payload["current"] = args[1]
        if len(args) > 2:
            payload["total"] = args[2]
        if len(args) > 3:
            payload["percent"] = args[3]
        if len(args) > 4:
            payload["message"] = args[4]
        payload.update(kwargs)
        stage = str(payload.get("stage") or payload.get("etapa") or "collecting").strip()[:80]
        try:
            current = max(0, int(payload.get("current") or payload.get("atual") or 0))
        except (TypeError, ValueError):
            current = 0
        try:
            total = max(0, int(payload.get("total") or 0))
        except (TypeError, ValueError):
            total = 0
        raw_percent = payload.get("percent", payload.get("percentual"))
        percent: float | None = None
        if raw_percent not in (None, ""):
            try:
                parsed_percent = float(raw_percent)
                if math.isfinite(parsed_percent):
                    percent = round(min(100.0, max(0.0, parsed_percent)), 1)
            except (TypeError, ValueError):
                percent = None
        message = str(payload.get("message") or payload.get("mensagem") or "").strip()[:300]
        progress = {
            "stage": stage,
            "current": current,
            "total": total,
            "percent": percent,
        }
        if message:
            progress["message"] = message
        _atualizar_job(
            job_id,
            progress=progress,
        )

    return callback


def _normalizar_fields(source: str, fields: Any) -> dict[str, Any]:
    permitidos = _ALLOWED_FIELDS[source]
    result: dict[str, Any] = {}
    if not isinstance(fields, dict):
        return result
    for raw_name, value in fields.items():
        name = str(raw_name or "").strip().lower()
        if name in permitidos and _valor_preenchido(value):
            result[name] = value
    return result


def _normalizar_plano_foto_ml(value: Any) -> dict[str, str]:
    """Validate the private cover plan again before it can reach persistence."""

    if not isinstance(value, dict):
        return {}
    url = str(value.get("url") or "").strip()
    item_id = cadastro_ml._normalizar_item_id(value.get("item_id"))
    if not item_id or not cadastro_ml._photo_url_allowed(url):
        return {}
    return {"url": url, "item_id": item_id}


def _comprimir_foto_catalogo_ml(
    downloaded: dict[str, Any],
    item_id: str,
) -> dict[str, str]:
    """Convert one trusted ML cover into a bounded, store-local JPEG."""

    data_url = str(downloaded.get("data_url") or "").strip()
    header, separator, encoded = data_url.partition(",")
    if (
        separator != ","
        or not header.lower().startswith("data:image/")
        or ";base64" not in header.lower()
    ):
        raise ValueError("invalid_photo_payload")
    try:
        source_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid_photo_payload") from exc
    if not source_bytes or len(source_bytes) > cadastro_ml.ML_PHOTO_MAX_BYTES:
        raise ValueError("invalid_photo_payload")

    try:
        with Image.open(io.BytesIO(source_bytes)) as source:
            width, height = source.size
            if (
                width <= 0
                or height <= 0
                or width * height > CATALOG_IMPORT_PHOTO_MAX_PIXELS
            ):
                raise ValueError("photo_pixel_limit_exceeded")
            source.seek(0)
            source.load()
            oriented = ImageOps.exif_transpose(source)
            has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
            if has_alpha:
                rgba = oriented.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, "white")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
            else:
                rgb = oriented.convert("RGB")
    except ValueError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise ValueError("invalid_photo_content") from exc

    compressed = b""
    profiles = (
        (CATALOG_IMPORT_PHOTO_MAX_EDGE_PX, CATALOG_IMPORT_PHOTO_JPEG_QUALITY),
        (600, 42),
        (480, 35),
    )
    for max_edge, quality in profiles:
        candidate = rgb.copy()
        candidate.thumbnail(
            (max_edge, max_edge),
            Image.Resampling.LANCZOS,
        )
        output = io.BytesIO()
        candidate.save(
            output,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling=2,
        )
        compressed = output.getvalue()
        if compressed and len(compressed) <= CATALOG_IMPORT_PHOTO_MAX_COMPRESSED_BYTES:
            break
    if not compressed or len(compressed) > CATALOG_IMPORT_PHOTO_MAX_COMPRESSED_BYTES:
        raise ValueError("photo_compression_limit_exceeded")

    safe_item_id = cadastro_ml._normalizar_item_id(item_id) or "mercado-livre"
    return {
        "data_url": "data:image/jpeg;base64,"
        + base64.b64encode(compressed).decode("ascii"),
        "filename": f"{safe_item_id}.jpg",
    }


def _consolidar_campos_duplicados_bling(
    entries: list[dict[str, Any]],
) -> tuple[dict[str, Any], set[str]]:
    """Keep only non-empty provider fields that agree across one Bling SKU."""

    valores_por_campo: dict[str, dict[str, Any]] = {}
    for entry in entries:
        for name, value in _normalizar_fields("bling", entry.get("fields")).items():
            valores_por_campo.setdefault(name, {}).setdefault(
                _texto_comparacao(value), value
            )

    consolidados: dict[str, Any] = {}
    divergentes: set[str] = set()
    for name, valores in valores_por_campo.items():
        if len(valores) == 1:
            consolidados[name] = next(iter(valores.values()))
        elif len(valores) > 1:
            divergentes.add(name)
    return consolidados, divergentes


def _campos_prioritarios_bling_existente(
    entries: list[dict[str, Any]],
) -> tuple[dict[str, Any], set[str]]:
    """Build the only Bling writes allowed for an SKU already in Cadastro.

    Empty provider values are ignored.  Conflicting values block just their
    logical title/NCM/CEST group so an agreeing group can still be applied.
    """

    valores_por_grupo: dict[str, dict[str, Any]] = {
        group_name: {} for group_name in _BLING_EXISTING_SYNC_GROUPS
    }
    grupos_bloqueados: set[str] = set()

    for entry in entries:
        fields = _normalizar_fields("bling", entry.get("fields"))
        for group_name, group in _BLING_EXISTING_SYNC_GROUPS.items():
            valores = valores_por_grupo[group_name]
            for source_name in group["sources"]:
                value = fields.get(source_name)
                if _valor_preenchido(value):
                    valores.setdefault(_texto_comparacao(value), value)

        raw_conflicts = entry.get("conflicts")
        if isinstance(raw_conflicts, dict):
            for raw_name in raw_conflicts:
                normalized_name = str(raw_name or "").strip().lower()
                group_name = _BLING_EXISTING_FIELD_GROUP.get(normalized_name)
                if group_name:
                    grupos_bloqueados.add(group_name)

    fields_apply: dict[str, Any] = {}
    grupos_divergentes: set[str] = set(grupos_bloqueados)
    for group_name, group in _BLING_EXISTING_SYNC_GROUPS.items():
        valores = valores_por_grupo[group_name]
        if len(valores) > 1:
            grupos_divergentes.add(group_name)
        if group_name in grupos_divergentes or len(valores) != 1:
            continue
        value = next(iter(valores.values()))
        for target_name in group["targets"]:
            fields_apply[target_name] = value
    return fields_apply, grupos_divergentes


def _construir_preview(
    source: str,
    provider_result: dict[str, Any],
    produtos_atuais: list[dict[str, Any]],
) -> dict[str, Any]:
    fonte = _fonte_valida(source)
    atuais: dict[str, dict[str, Any]] = {}
    for item in produtos_atuais:
        if not isinstance(item, dict):
            continue
        # Persistence keys are recalculated from the raw SKU during apply.  Use
        # that same identity here so a stale/forged sku_normalizado cannot point
        # the preview at a different row.
        chave_atual = _normalizar_sku_chave(item.get("sku") or "")
        if chave_atual:
            atuais[chave_atual] = item

    provider_items = provider_result.get("items") or []
    if not isinstance(provider_items, list):
        raise HTTPException(
            status_code=502,
            detail={
                "code": "catalog_provider_payload_invalid",
                "message": "A fonte externa retornou um catalogo invalido.",
            },
        )
    if len(provider_items) > CATALOG_IMPORT_MAX_PROVIDER_ITEMS:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "catalog_item_limit_exceeded",
                "message": (
                    "O catalogo excede o limite seguro desta importacao. "
                    "Divida a operacao antes de gerar uma nova previa."
                ),
            },
        )

    grupos: dict[str, list[dict[str, Any]]] = {}
    ignorados: list[dict[str, Any]] = []
    ignored_count = 0
    sku_identity_mismatch = False

    def ignorar(reason: str, **details: Any) -> None:
        nonlocal ignored_count
        ignored_count += 1
        if len(ignorados) < 100:
            ignorados.append({"reason": reason, **details})

    for item in provider_items:
        if not isinstance(item, dict):
            ignorar("item_invalido")
            continue
        sku = str(item.get("sku") or "").strip()
        sku_normalizado = _normalizar_sku_chave(sku)
        if not sku_normalizado:
            ignorar("sku_ausente")
            continue
        informado = str(item.get("sku_normalizado") or "").strip()
        if informado and _normalizar_sku_chave(informado) != sku_normalizado:
            sku_identity_mismatch = True
            ignorar("sku_inconsistente", sku=sku)
            continue
        grupos.setdefault(sku_normalizado, []).append({**item, "sku": sku})

    provider_skipped = provider_result.get("skipped")
    if isinstance(provider_skipped, list):
        for item in provider_skipped:
            ignorar(
                str(item.get("reason") or item.get("motivo") or "ignorado")
                if isinstance(item, dict) else "ignorado",
                **_detalhes_ignorado(item),
            )

    public_items: list[dict[str, Any]] = []
    public_items_total = 0

    def adicionar_item_publico(item: dict[str, Any]) -> None:
        nonlocal public_items_total
        public_items_total += 1
        if len(public_items) < CATALOG_IMPORT_PREVIEW_MAX_ROWS:
            public_items.append(item)

    apply_rows: list[dict[str, Any]] = []
    fotos_ml_sem_capa = 0
    summary = {
        "encontrados": len(grupos),
        "novos": 0,
        "preencher": 0,
        "inalterados": 0,
        "conflitos": 0,
        "ignorados": ignored_count,
        "aplicaveis": 0,
        "campos": 0,
    }

    for sku_normalizado in sorted(grupos):
        entries = grupos[sku_normalizado]
        first = entries[0]
        existente = atuais.get(sku_normalizado)
        bling_existente = fonte == "bling" and existente is not None
        duplicata_bling = fonte == "bling" and len(entries) > 1
        campos_divergentes_duplicata: set[str] = set()
        campos_bling_existente: dict[str, Any] = {}
        if bling_existente:
            (
                campos_bling_existente,
                campos_divergentes_duplicata,
            ) = _campos_prioritarios_bling_existente(entries)
        elif duplicata_bling:
            first = dict(first)
            (
                first["fields"],
                campos_divergentes_duplicata,
            ) = _consolidar_campos_duplicados_bling(entries)
        sku = str(first.get("sku") or sku_normalizado).strip()
        provider_conflict_labels: list[str] = []
        conflicting_source_fields: set[str] = set()
        hard_conflicts: list[str] = []
        if bling_existente:
            for group_name in sorted(campos_divergentes_duplicata):
                provider_conflict_labels.append(
                    f"{group_name}:valores_divergentes_na_fonte"
                )
        elif duplicata_bling:
            provider_conflict_labels.append("sku_duplicado_bling:consolidado")
            for name in sorted(campos_divergentes_duplicata):
                provider_conflict_labels.append(
                    f"{name}:valores_divergentes_na_fonte"
                )
                conflicting_source_fields.add(name)
        conflict_entries = entries if bling_existente else [first]
        for conflict_entry in conflict_entries:
            raw_provider_conflicts = conflict_entry.get("conflicts")
            if isinstance(raw_provider_conflicts, dict):
                if bling_existente:
                    continue
                for name in sorted(raw_provider_conflicts):
                    normalized_name = str(name or "").strip().lower()
                    provider_conflict_labels.append(
                        f"{normalized_name}:valores_divergentes_na_fonte"
                    )
                    if normalized_name in _ALLOWED_FIELDS[fonte]:
                        conflicting_source_fields.add(normalized_name)
            elif isinstance(raw_provider_conflicts, (list, tuple, set)):
                hard_conflicts.extend(
                    str(value)
                    for value in raw_provider_conflicts
                    if str(value).strip()
                )
            elif _valor_preenchido(raw_provider_conflicts):
                hard_conflicts.append("identidade_ambigua_na_fonte")
        if len(entries) > 1 and not duplicata_bling:
            hard_conflicts.append("sku_duplicado_na_fonte")
        if duplicata_bling:
            warnings = []
            for entry in entries:
                raw_warnings = entry.get("warnings")
                if not isinstance(raw_warnings, (list, tuple, set)):
                    raw_warnings = [raw_warnings] if _valor_preenchido(raw_warnings) else []
                for value in raw_warnings:
                    text = str(value).strip()
                    if text and text not in warnings:
                        warnings.append(text)
            warnings.append(f"sku_duplicado_bling_consolidado:{len(entries)}")
        else:
            warnings = [
                str(value)
                for value in (first.get("warnings") or [])
                if str(value).strip()
            ]
        external_ids = first.get("external_ids") or first.get("listings") or []
        if hard_conflicts:
            summary["conflitos"] += 1
            adicionar_item_publico(
                {
                    "sku": sku,
                    "sku_normalizado": sku_normalizado,
                    "status": "conflito",
                    "external_ids": external_ids,
                    "warnings": warnings,
                    "conflicts": hard_conflicts,
                    "changes": [],
                }
            )
            continue

        fields = (
            campos_bling_existente
            if bling_existente
            else _normalizar_fields(fonte, first.get("fields"))
        )
        for name in conflicting_source_fields:
            fields.pop(name, None)
        materializar_sombra = bool(
            existente and str(existente.get("scope_source") or "") == "legacy_shadow"
        )
        if existente and _valor_preenchido(existente.get("deleted_at_utc")):
            summary["conflitos"] += 1
            adicionar_item_publico(
                {
                    "sku": sku,
                    "sku_normalizado": sku_normalizado,
                    "status": "conflito",
                    "external_ids": external_ids,
                    "warnings": warnings,
                    "conflicts": ["sku_excluido_no_cadastro"],
                    "changes": [],
                }
            )
            continue
        fields_apply: dict[str, Any] = {}
        changes: list[dict[str, str]] = []
        row_conflicts = 0
        if existente is None:
            fields_apply.update(fields)
            changes.extend(
                {
                    "field": name,
                    "current": "" if existente is None else _texto_preview(existente.get(name)),
                    "incoming": _texto_preview(value),
                    "action": "fill",
                }
                for name, value in sorted(fields.items())
            )
        else:
            for name, value in sorted(fields.items()):
                current = existente.get(name)
                if not _valor_preenchido(current):
                    fields_apply[name] = value
                    changes.append(
                        {
                            "field": name,
                            "current": "",
                            "incoming": _texto_preview(value),
                            "action": "fill",
                        }
                    )
                elif _texto_comparacao(current) != _texto_comparacao(value):
                    if bling_existente:
                        fields_apply[name] = value
                        changes.append(
                            {
                                "field": name,
                                "current": _texto_preview(current),
                                "incoming": _texto_preview(value),
                                "action": "overwrite",
                            }
                        )
                    else:
                        row_conflicts += 1
                        changes.append(
                            {
                                "field": name,
                                "current": _texto_preview(current),
                                "incoming": _texto_preview(value),
                                "action": "preserve",
                            }
                        )

        photo_plan: dict[str, str] = {}
        if fonte == "mercadolivre" and not _valor_preenchido(
            (existente or {}).get("foto")
        ):
            photo_plan = _normalizar_plano_foto_ml(
                {
                    "url": fields.get("foto_url_ml"),
                    "item_id": fields.get("mlb_principal"),
                }
            )
            if photo_plan:
                changes.append(
                    {
                        "field": "foto",
                        "current": "",
                        "incoming": "Capa do Mercado Livre",
                        "action": "fill",
                    }
                )
            else:
                fotos_ml_sem_capa += 1

        if existente is None:
            status = "novo"
            summary["novos"] += 1
        elif fields_apply or materializar_sombra or photo_plan:
            status = "preencher"
            summary["preencher"] += 1
        elif row_conflicts:
            status = "conflito"
        else:
            status = "inalterado"
            summary["inalterados"] += 1
        if row_conflicts or provider_conflict_labels:
            status = "conflito"
            summary["conflitos"] += 1

        actionable = (
            existente is None
            or materializar_sombra
            or bool(fields_apply)
            or bool(photo_plan)
        )
        if actionable:
            row_version = int(str((existente or {}).get("row_version") or "0"))
            expected_scope = (
                "absent"
                if existente is None
                else "legacy_shadow"
                if materializar_sombra
                else "store_file"
            )
            sku_aplicacao = sku
            if existente is not None:
                sku_existente = str(existente.get("sku") or "").strip()
                if sku_existente:
                    sku_aplicacao = sku_existente
            apply_row = {
                "sku": sku_aplicacao,
                "sku_normalizado": sku_normalizado,
                "row_version": row_version,
                "expected_scope": expected_scope,
                "fields": fields_apply,
            }
            if photo_plan:
                apply_row["photo_plan"] = photo_plan
            if materializar_sombra:
                apply_row["legacy_snapshot_hash"] = str(
                    existente.get("__legacy_snapshot_hash")
                    or _fingerprint_sombra_legada(existente)
                )
            apply_rows.append(apply_row)
            summary["aplicaveis"] += 1
            summary["campos"] += len(fields_apply) + (1 if photo_plan else 0)

        adicionar_item_publico(
            {
                "sku": sku,
                "sku_normalizado": sku_normalizado,
                "status": status,
                "external_ids": external_ids,
                "warnings": warnings,
                "conflicts": [
                    *provider_conflict_labels,
                    *(
                        f"{change['field']}:valor_existente_preservado"
                        for change in changes
                        if change.get("action") == "preserve"
                    ),
                ],
                "changes": changes,
            }
        )

    coverage_complete = (
        provider_result.get("coverage_complete") is True
        and not sku_identity_mismatch
    )
    sku_coverage_complete = (
        provider_result.get(
            "sku_coverage_complete",
            provider_result.get("coverage_complete"),
        )
        is True
        and not sku_identity_mismatch
    )
    can_apply = sku_coverage_complete and bool(apply_rows)
    preview_warnings = [
        str(value) for value in (provider_result.get("warnings") or []) if str(value).strip()
    ]
    if sku_identity_mismatch:
        preview_warnings.append("provider_sku_identity_mismatch")
    if fotos_ml_sem_capa:
        preview_warnings.append(
            f"{fotos_ml_sem_capa} produto(s) sem foto local nao possuem uma capa confiavel disponivel no Mercado Livre."
        )
    return {
        "coverage_complete": coverage_complete,
        "sku_coverage_complete": sku_coverage_complete,
        "can_apply": can_apply,
        "summary": summary,
        "items": public_items,
        "items_total": public_items_total,
        "apply_rows": apply_rows,
        "ignored": ignorados,
        "ignored_total": ignored_count,
        "ignored_truncated": ignored_count > len(ignorados),
        "warnings": preview_warnings,
        "provider_stats": provider_result.get("stats")
        if isinstance(provider_result.get("stats"), dict)
        else {},
    }


def _erro_publico(exc: BaseException) -> dict[str, str]:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            return {
                "code": str(detail.get("code") or "catalog_provider_error")[:80],
                "message": str(detail.get("message") or "Nao foi possivel consultar o catalogo.")[:300],
            }
        return {"code": "catalog_provider_error", "message": str(detail)[:300]}
    return {
        "code": "catalog_provider_error",
        "message": "Nao foi possivel concluir a consulta do catalogo externo.",
    }


async def _executar_commit_sem_abandono(
    operation: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, asyncio.CancelledError | None]:
    """Wait for the atomic commit even if the HTTP coroutine is cancelled."""

    commit_task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    cancellation: asyncio.CancelledError | None = None
    while not commit_task.done():
        try:
            await asyncio.shield(commit_task)
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
    try:
        return commit_task.result(), cancellation
    except asyncio.CancelledError as exc:
        raise RuntimeError("O commit atomico foi cancelado internamente.") from exc


def _salvar_payloads_importacao_catalogo(
    client_id: str,
    store_id: str,
    source: str,
    payloads: list[dict[str, Any]],
    *,
    campos_derivados_permitidos: set[str],
    precommit_validator: Callable[[dict[str, str]], None],
    photo_progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Download trusted ML covers and commit metadata plus photos atomically."""

    fonte = _fonte_valida(source)
    seguros: list[dict[str, Any]] = []
    planos_total = sum(
        1
        for payload in payloads
        if isinstance(payload, dict) and payload.get("__catalog_photo_plan")
    )
    fotos_salvas = 0
    fotos_ignoradas = 0
    limite_atingido = False
    total_data_url_chars = 0
    deadline = time.monotonic() + max(
        0.01, float(CATALOG_IMPORT_PHOTO_APPLY_TIMEOUT_SECONDS)
    )
    cache: dict[tuple[str, str], dict[str, str] | None] = {}

    for payload in payloads:
        seguro = dict(payload)
        raw_plan = seguro.pop("__catalog_photo_plan", None)
        if fonte != "mercadolivre" or not raw_plan:
            seguros.append(seguro)
            continue

        plan = _normalizar_plano_foto_ml(raw_plan)
        if not plan or limite_atingido or time.monotonic() >= deadline:
            fotos_ignoradas += 1
            limite_atingido = limite_atingido or time.monotonic() >= deadline
        else:
            cache_key = (plan["url"], plan["item_id"])
            photo = cache.get(cache_key)
            if cache_key not in cache:
                try:
                    downloaded = cadastro_ml._download_photo_data_url(
                        plan["url"], plan["item_id"], deadline
                    )
                    optimized = _comprimir_foto_catalogo_ml(
                        downloaded,
                        plan["item_id"],
                    )
                    data_url = str(optimized.get("data_url") or "").strip()
                    filename = str(optimized.get("filename") or "").strip()
                    if not (
                        data_url.startswith("data:image/")
                        and ";base64," in data_url
                        and filename
                    ):
                        raise ValueError("invalid_photo_payload")
                    photo = {"data_url": data_url, "filename": filename}
                except (
                    HTTPException,
                    OSError,
                    ValueError,
                    requests.RequestException,
                ):
                    photo = None
                cache[cache_key] = photo

            data_url = str((photo or {}).get("data_url") or "")
            if photo and (
                total_data_url_chars + len(data_url)
                <= CATALOG_IMPORT_PHOTO_MAX_DATA_URL_CHARS
            ):
                seguro["__foto_data_url"] = data_url
                seguro["__foto_filename"] = str(photo["filename"])
                total_data_url_chars += len(data_url)
                fotos_salvas += 1
            else:
                fotos_ignoradas += 1
                if photo:
                    limite_atingido = True

        if photo_progress_callback is not None:
            photo_progress_callback(fotos_salvas + fotos_ignoradas, planos_total)
        seguros.append(seguro)

    result = salvar_produtos_loja_em_lote(
        client_id,
        store_id,
        seguros,
        campos_derivados_permitidos=campos_derivados_permitidos,
        precommit_validator=precommit_validator,
    )
    if fonte != "mercadolivre":
        return result

    result = dict(result)
    result.update(
        fotos_planejadas=planos_total,
        fotos_salvas=fotos_salvas,
        fotos_ignoradas=fotos_ignoradas,
    )
    if fotos_ignoradas:
        result["avisos_fotos"] = [
            (
                f"{fotos_ignoradas} capa(s) do Mercado Livre nao puderam ser salvas; "
                "os demais dados do cadastro foram mantidos."
            )
        ]
    return result


def _catalog_import_worker(job_id: str) -> None:
    with _CATALOG_IMPORT_JOBS_LOCK:
        job = CATALOG_IMPORT_JOBS.get(job_id)
        if not job:
            return
        client_id = str(job["client_id"])
        store_id = str(job["store_id"])
        source = str(job["source"])
        started_config_fingerprint = str(job.get("config_fingerprint") or "")
        started_apply_config_fingerprint = str(
            job.get("apply_config_fingerprint") or ""
        )
        cancel_event = job["cancel_event"]
        if cancel_event.is_set() or job.get("status") == "cancelled":
            return
        if job.get("status") != "queued":
            return
        job["status"] = "running"
        job["updated_ts"] = time.time()
        job["updated_at_utc"] = _agora_utc()
    try:
        current_started_config_fingerprint = _configuracao_fingerprint(
            client_id,
            store_id,
            source,
        )
        exact_start_match = bool(
            started_config_fingerprint
            and secrets.compare_digest(
                current_started_config_fingerprint,
                started_config_fingerprint,
            )
        )
        stable_start_match = False
        if (
            not exact_start_match
            and source == "bling"
            and started_apply_config_fingerprint
        ):
            stable_start_match = secrets.compare_digest(
                _configuracao_aplicacao_fingerprint(client_id, store_id, source),
                started_apply_config_fingerprint,
            )
        if not exact_start_match and not stable_start_match:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_config_changed",
                    "message": "A configuracao da integracao mudou antes da consulta.",
                },
            )
        provider = _provider_for(source)
        provider_kwargs = {
            "progress_callback": _progress_callback(job_id),
            "cancel_event": cancel_event,
            "expected_config_fingerprint": started_config_fingerprint,
        }
        if source == "bling":
            provider_kwargs["expected_apply_config_fingerprint"] = (
                started_apply_config_fingerprint
            )
        result = provider(client_id, store_id, **provider_kwargs)
        if cancel_event.is_set():
            _atualizar_job(job_id, status="cancelled", can_apply=False)
            return
        if not isinstance(result, dict):
            raise RuntimeError("invalid provider result")
        if str(result.get("source") or "").strip().lower() != source:
            raise RuntimeError("provider source mismatch")
        if str(result.get("store_id") or "").strip() != store_id:
            raise RuntimeError("provider store mismatch")
        provider_started_fingerprint = str(
            result.get("started_config_fingerprint") or ""
        ).strip().lower()
        provider_started_apply_fingerprint = str(
            result.get("started_apply_config_fingerprint") or ""
        ).strip().lower()
        exact_provider_start_match = secrets.compare_digest(
            provider_started_fingerprint,
            started_config_fingerprint.lower(),
        )
        stable_provider_start_match = bool(
            source == "bling"
            and started_apply_config_fingerprint
            and provider_started_apply_fingerprint
            and secrets.compare_digest(
                provider_started_apply_fingerprint,
                started_apply_config_fingerprint.lower(),
            )
        )
        if not exact_provider_start_match and not stable_provider_start_match:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_config_changed",
                    "message": "A fonte iniciou a consulta com outra configuracao.",
                },
            )
        provider_fingerprint = str(result.get("config_fingerprint") or "").strip().lower()
        if len(provider_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in provider_fingerprint
        ):
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "catalog_provider_payload_invalid",
                    "message": "A fonte externa nao confirmou a configuracao usada na coleta.",
                },
            )
        (
            current_exact_fingerprint,
            apply_config_fingerprint,
        ) = _configuracao_fingerprints(client_id, store_id, source)
        provider_apply_fingerprint = str(
            result.get("apply_config_fingerprint") or ""
        ).strip().lower()
        exact_provider_final_match = secrets.compare_digest(
            current_exact_fingerprint,
            provider_fingerprint,
        )
        stable_provider_final_match = bool(
            source == "bling"
            and len(provider_apply_fingerprint) == 64
            and all(
                character in "0123456789abcdef"
                for character in provider_apply_fingerprint
            )
            and secrets.compare_digest(
                apply_config_fingerprint,
                provider_apply_fingerprint,
            )
        )
        if not exact_provider_final_match and not stable_provider_final_match:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "store_config_changed",
                    "message": "A configuracao da integracao mudou durante a consulta.",
                },
            )
        produtos = _listar_produtos_loja_sync(
            client_id,
            store_id,
            include_deleted=True,
            include_legacy_snapshot_hash=True,
        )
        preview = _construir_preview(source, result, produtos)
        with _CATALOG_IMPORT_JOBS_LOCK:
            current_job = CATALOG_IMPORT_JOBS.get(job_id)
            if not current_job:
                return
            if cancel_event.is_set() or current_job.get("status") == "cancelled":
                current_job["status"] = "cancelled"
                current_job["can_apply"] = False
                current_job["updated_ts"] = time.time()
                current_job["updated_at_utc"] = _agora_utc()
                return
            current_job.update(
                status="ready",
                progress={"stage": "ready", "current": 1, "total": 1, "percent": 100.0},
                preview=preview,
                config_fingerprint=current_exact_fingerprint,
                apply_config_fingerprint=apply_config_fingerprint,
                can_apply=preview["can_apply"] is True,
            )
            current_job["updated_ts"] = time.time()
            current_job["updated_at_utc"] = _agora_utc()
    except BaseException as exc:
        if cancel_event.is_set():
            _atualizar_job(job_id, status="cancelled", can_apply=False)
        else:
            _atualizar_job(
                job_id,
                status="error",
                can_apply=False,
                error=_erro_publico(exc),
            )


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    preview = job.get("preview") if isinstance(job.get("preview"), dict) else {}
    items = preview.get("items") if isinstance(preview.get("items"), list) else []
    items_total = int(preview.get("items_total") or len(items))
    public_items = items[:CATALOG_IMPORT_PREVIEW_MAX_ROWS]
    sku_coverage_complete = (
        preview.get("sku_coverage_complete", preview.get("coverage_complete"))
        is True
    )
    apply_rows = preview.get("apply_rows")
    has_apply_rows = isinstance(apply_rows, list) and bool(apply_rows)
    payload: dict[str, Any] = {
        "job_id": job["job_id"],
        "source": job["source"],
        "store_id": job["store_id"],
        "store_name": job["store_name"],
        "status": job["status"],
        "progress": copy.deepcopy(job.get("progress") or {}),
        "created_at_utc": job["created_at_utc"],
        "updated_at_utc": job["updated_at_utc"],
        "coverage_complete": preview.get("coverage_complete") is True,
        "sku_coverage_complete": sku_coverage_complete,
        "can_apply": (
            job.get("can_apply") is True
            and job.get("status") == "ready"
            and sku_coverage_complete
            and has_apply_rows
        ),
        "summary": copy.deepcopy(preview.get("summary") or {}),
        "warnings": copy.deepcopy(preview.get("warnings") or []),
        "provider_stats": copy.deepcopy(preview.get("provider_stats") or {}),
        "items": copy.deepcopy(public_items),
        "items_total": items_total,
        "items_truncated": items_total > len(public_items),
        "ignored": copy.deepcopy((preview.get("ignored") or [])[:100]),
        "ignored_total": int(
            preview.get("ignored_total")
            or (preview.get("summary") or {}).get("ignorados")
            or len(preview.get("ignored") or [])
        ),
        "ignored_truncated": bool(preview.get("ignored_truncated")),
    }
    if job.get("error"):
        payload["error"] = copy.deepcopy(job["error"])
    if job.get("apply_result"):
        payload["apply_result"] = copy.deepcopy(job["apply_result"])
    return payload


def _job_do_request(
    client_id: str,
    store_id: str,
    job_id: str,
    requester_fingerprint: str,
) -> dict[str, Any]:
    with _CATALOG_IMPORT_JOBS_LOCK:
        _limpar_jobs_locked()
        job = CATALOG_IMPORT_JOBS.get(str(job_id or "").strip())
        if not _job_ainda_pertence_request(
            job,
            client_id,
            store_id,
            requester_fingerprint,
        ):
            raise _erro_job_nao_encontrado()
        return job


def _job_ainda_pertence_request(
    job: dict[str, Any] | None,
    client_id: str,
    store_id: str,
    requester_fingerprint: str,
) -> bool:
    return bool(
        job
        and secrets.compare_digest(str(job.get("client_id") or ""), str(client_id or ""))
        and secrets.compare_digest(str(job.get("store_id") or ""), str(store_id or ""))
        and secrets.compare_digest(
            str(job.get("requester_fingerprint") or ""),
            str(requester_fingerprint or ""),
        )
    )


def _erro_job_nao_encontrado() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "catalog_job_not_found", "message": "Importacao nao encontrada."},
    )


async def iniciar_preview_importacao_catalogo(
    store_id: str,
    source: str,
    request: Request,
    client_id: str = Depends(_get_tenant_id_dependency),
):
    fonte = _fonte_valida(source)
    requester_fingerprint = _requester_fingerprint(request, client_id)
    loja, config = _configuracao_fonte(client_id, store_id, fonte)
    config_fingerprint = _configuracao_snapshot_fingerprint(loja, config, fonte)
    apply_config_fingerprint = _configuracao_aplicacao_snapshot_fingerprint(
        loja,
        config,
        fonte,
    )
    with _CATALOG_IMPORT_JOBS_LOCK:
        _limpar_jobs_locked()
        for existing in CATALOG_IMPORT_JOBS.values():
            if (
                existing.get("client_id") == client_id
                and existing.get("store_id") == loja["store_id"]
                and existing.get("source") == fonte
                and existing.get("status") in _ACTIVE_JOB_STATUSES
            ):
                if secrets.compare_digest(
                    str(existing.get("requester_fingerprint") or ""), requester_fingerprint
                ):
                    return _public_job(existing)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "catalog_import_already_running",
                        "message": "Esta loja ja possui uma importacao desta fonte em andamento.",
                    },
                )
        active_jobs = [
            existing
            for existing in CATALOG_IMPORT_JOBS.values()
            if existing.get("status") in _ACTIVE_JOB_STATUSES
        ]
        if len(active_jobs) >= CATALOG_IMPORT_MAX_ACTIVE_JOBS:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "catalog_import_capacity_reached",
                    "message": "Ha muitas importacoes em andamento. Tente novamente em instantes.",
                },
            )
        tenant_active = sum(
            1 for existing in active_jobs if existing.get("client_id") == client_id
        )
        if tenant_active >= CATALOG_IMPORT_MAX_ACTIVE_JOBS_PER_TENANT:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "catalog_import_tenant_capacity_reached",
                    "message": "Este cliente ja possui importacoes em andamento.",
                },
            )
        bling_active = sum(1 for existing in active_jobs if existing.get("source") == "bling")
        if fonte == "bling" and bling_active >= CATALOG_IMPORT_MAX_ACTIVE_BLING_JOBS:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "catalog_import_bling_capacity_reached",
                    "message": "Ja existe uma coleta Bling em andamento. Aguarde sua conclusao.",
                },
            )
        job_id = secrets.token_urlsafe(24)
        now = _agora_utc()
        job = {
            "job_id": job_id,
            "client_id": client_id,
            "requester_fingerprint": requester_fingerprint,
            "store_id": loja["store_id"],
            "store_name": loja["nome"],
            "source": fonte,
            "status": "queued",
            "progress": {"stage": "queued", "current": 0, "total": 0, "percent": None},
            "can_apply": False,
            "created_at_utc": now,
            "updated_at_utc": now,
            "updated_ts": time.time(),
            "cancel_event": threading.Event(),
            "config_fingerprint": config_fingerprint,
            "apply_config_fingerprint": apply_config_fingerprint,
        }
        CATALOG_IMPORT_JOBS[job_id] = job
    thread = threading.Thread(
        target=_catalog_import_worker,
        args=(job_id,),
        name=f"cadastro-catalog-{fonte}",
        daemon=True,
    )
    thread.start()
    return _public_job(job)


async def obter_importacao_catalogo(
    store_id: str,
    job_id: str,
    request: Request,
    client_id: str = Depends(_get_tenant_id_dependency),
):
    requester_fingerprint = _requester_fingerprint(request, client_id)
    return _public_job(
        _job_do_request(client_id, store_id, job_id, requester_fingerprint)
    )


async def cancelar_importacao_catalogo(
    store_id: str,
    job_id: str,
    request: Request,
    client_id: str = Depends(_get_tenant_id_dependency),
):
    """Cancel or discard one preview owned by the current user.

    An atomic Cadastro commit cannot be interrupted after it entered the
    ``applying`` state. Completed terminal jobs are returned idempotently.
    """

    requester_fingerprint = _requester_fingerprint(request, client_id)
    job = _job_do_request(client_id, store_id, job_id, requester_fingerprint)
    with _CATALOG_IMPORT_JOBS_LOCK:
        status = str(job.get("status") or "")
        if status == "applying":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "catalog_apply_in_progress",
                    "message": "A aplicacao atomica ja comecou e nao pode ser interrompida.",
                },
            )
        if status in {"queued", "running", "ready"}:
            cancel_event = job.get("cancel_event")
            if cancel_event is not None and callable(getattr(cancel_event, "set", None)):
                cancel_event.set()
            job["status"] = "cancelled"
            job["can_apply"] = False
            job["progress"] = {
                "stage": "cancelled",
                "current": 0,
                "total": 0,
                "percent": None,
            }
            job.pop("error", None)
            job["updated_ts"] = time.time()
            job["updated_at_utc"] = _agora_utc()
        return _public_job(job)


def _marcar_stale(
    job_id: str,
    cause: str,
    message: str | None = None,
) -> None:
    safe_cause = str(cause or "cadastro_changed").strip().lower()[:80]
    _atualizar_job(
        job_id,
        status="stale",
        can_apply=False,
        error={
            "code": "catalog_preview_stale",
            "cause": safe_cause,
            "message": str(
                message
                or "A loja, a integracao ou o Cadastro mudou. Gere uma nova previa."
            )[:300],
        },
    )


async def aplicar_importacao_catalogo(
    store_id: str,
    job_id: str,
    request: Request,
    client_id: str = Depends(_get_tenant_id_dependency),
):
    requester_fingerprint = _requester_fingerprint(request, client_id)
    job = _job_do_request(client_id, store_id, job_id, requester_fingerprint)
    with _CATALOG_IMPORT_JOBS_LOCK:
        job_atual = CATALOG_IMPORT_JOBS.get(str(job_id or "").strip())
        if (
            job_atual is not job
            or not _job_ainda_pertence_request(
                job_atual,
                client_id,
                store_id,
                requester_fingerprint,
            )
        ):
            raise _erro_job_nao_encontrado()
        job = job_atual
        if job.get("status") == "applied" and job.get("apply_result"):
            return _public_job(job)
        raw_preview = job.get("preview")
        raw_preview = raw_preview if isinstance(raw_preview, dict) else {}
        sku_coverage_complete = (
            raw_preview.get(
                "sku_coverage_complete",
                raw_preview.get("coverage_complete"),
            )
            is True
        )
        apply_rows = raw_preview.get("apply_rows")
        if (
            job.get("status") != "ready"
            or job.get("can_apply") is not True
            or not sku_coverage_complete
            or not isinstance(apply_rows, list)
            or not apply_rows
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "catalog_preview_not_applicable",
                    "message": (
                        "A previa ainda nao esta pronta ou nao possui cobertura completa de SKUs."
                    ),
                },
            )
        job["status"] = "applying"
        job["can_apply"] = False
        job["progress"] = {
            "stage": "applying",
            "current": 0,
            "total": len(apply_rows),
            "percent": 0.0,
            "message": "Preparando dados e capas para salvar no Cadastro.",
        }
        job["updated_ts"] = time.time()
        source = str(job["source"])
        expected_fingerprint = str(job.get("apply_config_fingerprint") or "")
        preview = copy.deepcopy(raw_preview)

    try:
        if not expected_fingerprint or not secrets.compare_digest(
            _configuracao_aplicacao_fingerprint(client_id, store_id, source),
            expected_fingerprint,
        ):
            _marcar_stale(
                job_id,
                "integration_changed",
                "A conexao da integracao mudou. Gere uma nova previa.",
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "catalog_preview_stale",
                    "cause": "integration_changed",
                    "message": "A configuracao da integracao mudou. Gere uma nova previa.",
                },
            )

        payloads: list[dict[str, Any]] = []
        for row in preview.get("apply_rows") or []:
            sku = str(row.get("sku") or "").strip()
            sku_normalizado = _normalizar_sku_chave(sku)
            if not sku_normalizado or sku_normalizado != str(
                row.get("sku_normalizado") or ""
            ).strip():
                _marcar_stale(
                    job_id,
                    "sku_identity_changed",
                    "A identidade de um SKU mudou. Gere uma nova previa.",
                )
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "catalog_preview_stale",
                        "cause": "sku_identity_changed",
                        "message": "A identidade de um SKU mudou. Gere uma nova previa.",
                    },
                )
            payload = {
                "sku": sku,
                "row_version": row["row_version"],
                "__expected_scope": str(row.get("expected_scope") or "").strip(),
                **dict(row.get("fields") or {}),
            }
            legacy_snapshot_hash = str(row.get("legacy_snapshot_hash") or "").strip()
            if legacy_snapshot_hash:
                payload["__legacy_snapshot_hash"] = legacy_snapshot_hash
            if source == "mercadolivre" and row.get("photo_plan"):
                payload["__catalog_photo_plan"] = copy.deepcopy(row["photo_plan"])
            payloads.append(payload)

        def precommit_validator(_loja: dict[str, str]) -> None:
            atual = _configuracao_aplicacao_fingerprint(client_id, store_id, source)
            if not secrets.compare_digest(atual, expected_fingerprint):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "catalog_preview_stale",
                        "cause": "integration_changed",
                        "message": "A configuracao da integracao mudou. Gere uma nova previa.",
                    },
                )

        def photo_progress(current: int, total: int) -> None:
            percent = round((max(0, current) / max(1, total)) * 90.0, 2)
            _atualizar_job(
                job_id,
                progress={
                    "stage": "photos",
                    "current": max(0, current),
                    "total": max(0, total),
                    "percent": percent,
                    "message": "Baixando capas confiaveis do Mercado Livre.",
                },
            )

        result, cancellation = await _executar_commit_sem_abandono(
            _salvar_payloads_importacao_catalogo,
            client_id,
            store_id,
            source,
            payloads,
            campos_derivados_permitidos=_PRIVILEGED_DERIVED_FIELDS[source],
            precommit_validator=precommit_validator,
            photo_progress_callback=photo_progress,
        )
        apply_result = {
            "incluidos": int(result.get("incluidos") or 0),
            "atualizados": int(result.get("atualizados") or 0),
            "total": int(result.get("total") or 0),
        }
        if source == "mercadolivre":
            apply_result.update(
                fotos_planejadas=int(result.get("fotos_planejadas") or 0),
                fotos_salvas=int(result.get("fotos_salvas") or 0),
                fotos_ignoradas=int(result.get("fotos_ignoradas") or 0),
                avisos_fotos=[
                    str(value)[:300]
                    for value in (result.get("avisos_fotos") or [])
                    if str(value).strip()
                ][:10],
            )
        _atualizar_job(
            job_id,
            status="applied",
            can_apply=False,
            progress={"stage": "applied", "current": 1, "total": 1, "percent": 100.0},
            apply_result=apply_result,
        )
        response = _public_job(
            _job_do_request(client_id, store_id, job_id, requester_fingerprint)
        )
        if cancellation is not None:
            raise cancellation
        return response
    except asyncio.CancelledError:
        # The commit result was recorded before cancellation is propagated.
        raise
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        error_code = str(detail.get("code") or "")
        if error_code in {"catalog_preview_stale", "store_config_changed"}:
            cause = str(detail.get("cause") or "").strip()
            if not cause:
                cause = (
                    "integration_changed"
                    if error_code == "store_config_changed"
                    else "cadastro_changed"
                )
            _marcar_stale(job_id, cause, str(detail.get("message") or "") or None)
        elif exc.status_code in {400, 404}:
            _marcar_stale(job_id, "integration_unavailable")
        elif exc.status_code == 409:
            _marcar_stale(job_id, "cadastro_changed")
        elif exc.status_code >= 500:
            _atualizar_job(
                job_id,
                status="error",
                can_apply=False,
                error=_erro_publico(exc),
            )
        raise
    except BaseException as exc:
        _atualizar_job(job_id, status="ready", can_apply=True, error=_erro_publico(exc))
        raise HTTPException(
            status_code=500,
            detail={
                "code": "catalog_apply_failed",
                "message": "Nao foi possivel aplicar a importacao; nenhuma alteracao parcial foi mantida.",
            },
        ) from exc


__all__ = [
    "CATALOG_IMPORT_JOBS",
    "configure_cadastro_importacao_catalogos_runtime",
    "iniciar_preview_importacao_catalogo",
    "obter_importacao_catalogo",
    "cancelar_importacao_catalogo",
    "aplicar_importacao_catalogo",
]
