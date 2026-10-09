"""Server-owned analysis context for a bounded LIGHTNING price reanalysis."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from decimal import Decimal, InvalidOperation
from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import HTTPException

from backend.services import promocoes_common
from backend.services.promocoes_lightning_acao import LIGHTNING_ROW_FIELDS, lightning_stock_error, lightning_integer

CONTEXT_VERSION = 1
_ITEM_FIELDS = frozenset((
    "id", "seller_id", "site_id", "price", "original_price", "currency_id", "category_id",
    "domain_id", "listing_type_id", "condition", "shipping", "sale_terms", "attributes", "tags", "available_quantity",
))
_RAW_FIELDS = frozenset((
    "id", "item_id", "itemId", "item_id_to", "promotion_id", "promotionId", "campaign_id", "campaignId",
    "deal_id", "dealId", "type", "promotion_type", "promotionType", "campaign_type", "campaignType",
    "offer_id", "offerId", "ref_id", "refId", "offer_ref_id", "candidate_offer_id", "candidateOfferId",
    "status", "state", "status_item", "price", "deal_price", "original_price", "standard_price",
    "discount_percentage", "seller_percentage", "meli_percentage", "seller_receives",
    "min_discounted_price", "max_discounted_price", "suggested_discounted_price",
    "min_stock", "minimum_stock", "min_stock_quantity", "max_stock", "maximum_stock", "max_stock_quantity",
    "stock", "start_date", "startDate", "date_start", "begin_date", "finish_date", "finishDate",
    "end_date", "endDate", "date_end", "offer", "promotion", "campaign", "deal",
    "_jk_campaign_id_consultado", "_jk_item_id_consultado", "_jk_promotion_type_consultado",
    "_jk_contexto_promocao_confirmado", "_jk_status_item_consultado", "_jk_tipo_promocao_consultado",
))
_RAW_NESTED_FIELDS = _RAW_FIELDS | frozenset(("min", "max", "quantity", "amount", "value", "name"))
_SHIPPING_FIELDS = frozenset(("mode", "logistic_type", "dimensions", "free_shipping", "local_pick_up", "store_pick_up", "tags"))
_ATTRIBUTE_FIELDS = frozenset(("id", "value_id", "value_name", "value_struct", "values", "number", "unit", "name"))
_CONTEXT_SINK = ContextVar("promotion_reanalysis_context_sink", default=None)


@contextmanager
def capturar_contexto_reanalise(callback):
    """Private Python channel; never part of an HTTP endpoint signature."""
    token = _CONTEXT_SINK.set(callback if callable(callback) else None)
    try:
        yield
    finally:
        _CONTEXT_SINK.reset(token)


def _safe_value(value, fields=frozenset()):
    if isinstance(value, dict):
        return {str(key): _safe_value(val, fields) for key, val in value.items() if key in fields}
    if isinstance(value, (list, tuple)):
        return [_safe_value(val, fields) for val in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if value is None or isinstance(value, (str, int, float, bool)) else None


def _raw_snapshot(value):
    return {key: _safe_value(val, _RAW_NESTED_FIELDS) for key, val in (value or {}).items() if key in _RAW_FIELDS}


def _item_snapshot(item):
    snapshot = {}
    for key, value in item.items():
        if key not in _ITEM_FIELDS:
            continue
        fields = _SHIPPING_FIELDS if key == "shipping" else _ATTRIBUTE_FIELDS if key in {"sale_terms", "attributes"} else frozenset()
        snapshot[key] = _safe_value(value, fields)
    return snapshot


def _text(value):
    return str(value or "").strip()


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _price(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return result.quantize(Decimal("0.01")) if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def novo_contexto_reanalise(client_id, loja, cfg, promocao_a_id, promocao_a_type, margem_minima, margem_tolerancia):
    cfg = cfg if isinstance(cfg, dict) else {}
    return {
        "version": CONTEXT_VERSION, "client_id": _text(client_id), "loja": _text(loja),
        "seller_id": _text(cfg.get("user_id") or cfg.get("seller_id")),
        "site_id": _text(cfg.get("site_id")).upper(),
        "promocao_a_id": _text(promocao_a_id), "promocao_a_type": _text(promocao_a_type).upper(),
        "margem_minima": _number(margem_minima), "margem_tolerancia": _number(margem_tolerancia), "rows": {},
    }


def registrar_linha_reanalise(contexto, row, *, item, raw_acao, custo, imposto_rate, status_promo_a,
                              margem_base, preco_base, skus_custo=(), sku_imposto="", sku="", raw_a=None, preco_a=None):
    """Capture unrounded canonical inputs while a row is built on the server."""
    item = item if isinstance(item, dict) else {}
    raw_acao = raw_acao if isinstance(raw_acao, dict) else {}
    item_id = _text(row.get("MLB") or item.get("id")).upper()
    promotion_id = _text(row.get("action_promotion_id"))
    promotion_type = _text(row.get("promotion_type")).upper()
    identity = [contexto["client_id"], contexto["loja"], promotion_id, promotion_type, item_id,
                _text(row.get("action_offer_id")), _text(row.get("lightning_offer_key"))]
    ref = "analysis:" + hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode("utf-8")).hexdigest()
    row["analysis_client_ref"] = ref
    selected = {
        "client_ref": ref, "item_id": item_id, "action_promotion_id": promotion_id,
        "offer_id": _text(row.get("action_offer_id")), "deal_price": _number(row.get("action_deal_price")),
        "discount_percentage": _number(row.get("action_discount_percentage")),
    }
    selected.update({key: _safe_value(row[key]) for key in LIGHTNING_ROW_FIELDS if key in row})
    site_id = _text(item.get("site_id") or contexto.get("site_id")).upper()
    if not site_id and re.fullmatch(r"MLB\d+", item_id):
        site_id = "MLB"
    record = {
        "analysis_client_ref": ref, "item_id": item_id, "promotion_id": promotion_id,
        "promotion_type": promotion_type, "seller_id": _text(item.get("seller_id") or contexto.get("seller_id")),
        "site_id": site_id, "selected": selected,
        "item": _item_snapshot(item),
        "raw_acao": _raw_snapshot(raw_acao),
        "sku": _text(sku), "custo": _number(custo), "imposto_rate": _number(imposto_rate),
        "raw_a": _raw_snapshot(raw_a),
        "preco_a": _number(preco_a),
        "status_promo_a": _text(status_promo_a), "margem_base": _number(margem_base),
        "preco_base": _number(preco_base),
        "promocao_a_id": contexto["promocao_a_id"], "promocao_a_type": contexto["promocao_a_type"],
        "margem_minima": contexto["margem_minima"], "margem_tolerancia": contexto["margem_tolerancia"],
        "approved": _text(row.get("Ação") or row.get("Acao") or row.get("AÃ§Ã£o")).lower() == "participar",
        "fontes": {"custo": "cadastro/maior_custo_variacao", "imposto": "cadastro/imposto_sku",
                    "skus_custo": [_text(sku) for sku in (skus_custo or []) if _text(sku)],
                    "sku_imposto": _text(sku_imposto),
                    "preco": "seller_promotions/convite", "margem_base": "analise/promocao_1"},
    }
    contexto["rows"][ref] = record
    return ref


def finalizar_contexto_reanalise(contexto, resultado, context_sink=None):
    """Keep only the rows actually delivered to the user; do not expose context."""
    context_sink = context_sink or _CONTEXT_SINK.get()
    if not callable(context_sink):
        return
    analyses = resultado.get("analises") or [{"data": resultado.get("data") or []}]
    refs = {_text(row.get("analysis_client_ref")) for analysis in analyses if isinstance(analysis, dict)
            for row in (analysis.get("data") or []) if isinstance(row, dict)}
    private = copy.deepcopy(contexto)
    private["rows"] = {ref: row for ref, row in private["rows"].items() if ref in refs}
    sites = {_text(row.get("site_id")) for row in private["rows"].values() if row.get("site_id")}
    if not private["site_id"] and len(sites) == 1:
        private["site_id"] = next(iter(sites))
    campaigns = set()
    for analysis in analyses:
        if not isinstance(analysis, dict):
            continue
        promotion_id = _text(analysis.get("promo_b_id") or analysis.get("promotion_id") or analysis.get("promocao_b_id"))
        promotion_type = _text(analysis.get("promo_b_type") or analysis.get("promotion_type") or analysis.get("promocao_b_type")).upper()
        if promotion_id:
            campaigns.add((promotion_id, promotion_type))
    if not campaigns:
        campaigns = {(row["promotion_id"], row["promotion_type"]) for row in private["rows"].values()}
    private["promocoes_b"] = [{"promotion_id": campaign, "promotion_type": kind} for campaign, kind in sorted(campaigns)]
    context_sink(private)


def carregar_contexto_reanalise(analysis_job_id, client_id, loja, cfg, *, info_dir=None):
    """Load only a completed job from the configured canonical data directory."""
    job_id = _text(analysis_job_id)
    if not re.fullmatch(r"[a-fA-F0-9]{32}", job_id):
        raise HTTPException(status_code=409, detail="Analise de origem nao confirmada. Execute uma nova analise.")
    base = os.path.abspath(os.fspath(info_dir or promocoes_common.PASTA_INFO))
    job = None
    for folder in ("promo_worker_jobs", "promo_jobs"):
        path = os.path.join(base, folder, f"{job_id}.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as stream:
                job = json.load(stream)
        except (OSError, ValueError, TypeError):
            raise HTTPException(status_code=409, detail="Analise de origem indisponivel. Execute uma nova analise.")
        break
    if not isinstance(job, dict) or _text(job.get("client_id")) != _text(client_id) or _text(job.get("loja")) != _text(loja):
        raise HTTPException(status_code=404, detail="Analise de origem nao encontrada para esta loja.")
    context = job.get("reanalysis_context")
    if _text(job.get("status")).lower() != "completed" or not isinstance(context, dict) or context.get("version") != CONTEXT_VERSION:
        raise HTTPException(status_code=409, detail="Analise de origem sem contexto de recalculo. Execute uma nova analise.")
    if not isinstance(context.get("rows"), dict) or not context.get("promocao_a_id") or any(
        not isinstance(row, dict) for row in context["rows"].values()
    ):
        raise HTTPException(status_code=409, detail="Contexto da analise de origem incompleto. Execute uma nova analise.")
    cfg = cfg if isinstance(cfg, dict) else {}
    seller = _text(cfg.get("user_id") or cfg.get("seller_id"))
    site = _text(cfg.get("site_id")).upper()
    canonical_site = _text(context.get("site_id")).upper()
    if not site and canonical_site == "MLB" and all(
        re.fullmatch(r"MLB\d+", _text(row.get("item_id"))) and row.get("site_id") == "MLB"
        for row in (context.get("rows") or {}).values()
    ):
        site = "MLB"
    if (context.get("client_id") != _text(client_id) or context.get("loja") != _text(loja)
            or not seller or context.get("seller_id") != seller or not site or canonical_site != site):
        raise HTTPException(status_code=409, detail="Conta ou site da analise de origem mudou. Execute uma nova analise.")
    return copy.deepcopy(context)


def resolver_linha_reanalise(contexto, client_ref, promotion_id, item_id, selected):
    """Resolve the approved row and reject a client changing its original offer."""
    ref = _text(client_ref)
    record = (contexto.get("rows") or {}).get(ref) if isinstance(contexto, dict) else None
    if not isinstance(record, dict):
        raise HTTPException(status_code=409, detail="Linha da analise de origem nao confirmada. Execute uma nova analise.")
    if (record.get("analysis_client_ref") != ref or record.get("promotion_id") != _text(promotion_id)
            or record.get("item_id") != _text(item_id).upper() or record.get("promotion_type") != "LIGHTNING"
            or not record.get("approved") or record.get("seller_id") != contexto.get("seller_id")
            or record.get("site_id") != contexto.get("site_id")):
        raise HTTPException(status_code=409, detail="Oferta nao corresponde a linha aprovada na analise.")
    if hasattr(selected, "model_dump"):
        selected = selected.model_dump()
    elif hasattr(selected, "dict"):
        selected = selected.dict()
    if not isinstance(selected, dict):
        raise HTTPException(status_code=409, detail="Selecao da oferta nao confirmada.")
    canonical = record.get("selected") or {}
    text_pairs = {"item_id": "item_id", "action_promotion_id": "action_promotion_id", "offer_id": "offer_id",
                  "action_offer_id": "offer_id", "lightning_offer_key": "lightning_offer_key",
                  "lightning_start_date": "lightning_start_date", "lightning_finish_date": "lightning_finish_date"}
    for given, original in text_pairs.items():
        if given in selected and _text(selected[given]) != _text(canonical.get(original)):
            raise HTTPException(status_code=409, detail="Identidade da oferta mudou em relacao a analise.")
    for given in ("deal_price", "action_deal_price"):
        if given in selected and _price(selected[given]) != _price(canonical.get("deal_price")):
            raise HTTPException(status_code=409, detail="Preco selecionado nao corresponde ao convite analisado.")
    for field in ("lightning_stock_min", "lightning_stock_max"):
        if field in selected and (lightning_integer(selected[field]) is None or lightning_integer(selected[field]) != canonical.get(field)):
            raise HTTPException(status_code=409, detail="Limites de estoque mudaram em relacao a analise.")
    if "lightning_schedule_pending" in selected and (
        not isinstance(selected["lightning_schedule_pending"], bool)
        or selected["lightning_schedule_pending"] != canonical.get("lightning_schedule_pending")
    ):
        raise HTTPException(status_code=409, detail="Calendario da oferta mudou em relacao a analise.")
    stock = selected.get("lightning_stock", canonical.get("lightning_stock"))
    reason = lightning_stock_error(record.get("raw_acao"), stock)
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    if record.get("custo") is None or record.get("imposto_rate") is None or record.get("margem_minima") is None or record.get("margem_tolerancia") is None:
        raise HTTPException(status_code=409, detail="Contexto financeiro da analise incompleto. Execute uma nova analise.")
    result = copy.deepcopy(record)
    result["selected"]["lightning_stock"] = stock
    return result


def configuracao_automacao_reanalise_confere(contexto, cfg):
    """A completed job cannot authorize an automatic action under changed rules."""
    if not isinstance(contexto, dict) or not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return False
    if (contexto.get("loja") != _text(cfg.get("loja"))
            or contexto.get("promocao_a_id") != _text(cfg.get("promocao_a_id"))
            or contexto.get("promocao_a_type") != _text(cfg.get("promocao_a_type")).upper()):
        return False
    for field in ("margem_minima", "margem_tolerancia"):
        current = _number(cfg.get(field))
        if current is None or current != contexto.get(field):
            return False
    selected = cfg.get("promocoes_b_meta")
    canonical = contexto.get("promocoes_b")
    if not isinstance(selected, list) or not isinstance(canonical, list):
        return False
    current_campaigns = {
        (_text(meta.get("promo_b_id") or meta.get("promotion_id") or meta.get("value")),
         _text(meta.get("promo_b_type") or meta.get("promotion_type") or meta.get("promoType")).upper())
        for meta in selected if isinstance(meta, dict)
    }
    canonical_campaigns = {
        (_text(meta.get("promotion_id")), _text(meta.get("promotion_type")).upper())
        for meta in canonical if isinstance(meta, dict)
    }
    return bool(canonical_campaigns) and current_campaigns == canonical_campaigns
