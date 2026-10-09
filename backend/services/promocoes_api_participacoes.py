"""Internal slice for promocoes_api."""

from __future__ import annotations

from __future__ import annotations
import asyncio
from backend.services.promocoes_validacao import validar_promocoes_por_anuncio
from backend.services.promocoes_lightning import (
    lightning_offer_key, lightning_offers, lightning_offer_window, lightning_window_valid,
)
from backend.services.promocoes_lightning_acao import (
    LIGHTNING_ROW_FIELDS, lightning_integer, lightning_stock_error,
    lightning_same_window, lightning_scoped_item_offers, lightning_stock_bounds,
    lightning_schedule_pending, lightning_has_schedule_data, lightning_reference,
)
from backend.services.mercadolivre_legacy_promocoes import (
    _promo_contextualizar_entry_campanha as _validar_contexto_entry_campanha,
    _promo_status_item_promocao as _lightning_status,
)
import inspect
import sys
from backend.services.promocoes_lightning_reanalise import (
    apply_reanalysed_lightning, ml_error_code, participation_error_detail, money_cents, price_number, validate_exact_lightning,
)
from backend.services.promocoes_reanalise_contexto import carregar_contexto_reanalise, resolver_linha_reanalise
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
import pandas as pd
import requests
from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from backend.schemas import (
    PromoAnaliseApiRequest,
    PromoAplicarParticipacaoRequest,
    PromoAutomacaoConfigRequest,
)
import base64
import copy
import csv
import functools
import hashlib
import html as html_lib
import io
import json
import logging
import math
import os
import random
import re
import sqlite3
import tempfile
import threading
import time
import traceback
import unicodedata
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.promocoes_common import *
from backend.services.promocoes_core import *

_PROMOCOES_RUNTIME_GET_TENANT_ID = None


def configure_promocoes_api_participacoes_runtime(runtime_module=None, peers=None):
    _configure_common = globals().get("configure_promocoes_common_runtime")
    if callable(_configure_common):
        try:
            _configure_common(runtime_module)
        except TypeError:
            _configure_common()
    runtime = bind_runtime_globals(globals(), runtime_module)
    runtime_get_tenant_id = getattr(runtime, "get_tenant_id", None) if runtime is not None else None
    if callable(runtime_get_tenant_id):
        globals()["_PROMOCOES_RUNTIME_GET_TENANT_ID"] = runtime_get_tenant_id
    if peers:
        globals().update({name: value for name, value in peers.items() if name != "get_tenant_id"})
    return runtime


configure_promocoes_api_participacoes_runtime()


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    resolver = globals().get("_PROMOCOES_RUNTIME_GET_TENANT_ID")
    if resolver is None:
        runtime = globals().get("_runtime")
        resolver = getattr(runtime, "get_tenant_id", None) if runtime is not None else None
    if not callable(resolver) or resolver is globals().get("_PROMOCOES_PLACEHOLDER_GET_TENANT_ID"):
        raise RuntimeError("Promocoes API runtime was not configured.")
    result = resolver(request, authorization)
    if inspect.isawaitable(result):
        return await result
    return result


_PROMOCOES_PLACEHOLDER_GET_TENANT_ID = get_tenant_id


_LIGHTNING_PARTICIPATING_STATES = {"pending", "programmed", "programada", "scheduled", "started", "active", "approved"}


def _promo_lightning_owner_matches(raw, cfg):
    containers = [raw] + [raw[key] for key in ("offer", "promotion", "campaign", "deal")
                          if isinstance(raw.get(key), dict)] if isinstance(raw, dict) else []
    owners = {str(container[key]).strip() for container in containers
              for key in ("seller_id", "user_id", "owner_id") if container.get(key) is not None}
    expected = str((cfg or {}).get("user_id") or (cfg or {}).get("seller_id") or "").strip()
    return not owners or bool(expected and owners == {expected})


def _promo_lightning_scoped_offers(records, promotion_id, item_id, cfg=None):
    records = [raw for raw in records if isinstance(raw, dict) and _promo_lightning_owner_matches(raw, cfg)] if isinstance(records, list) else []
    offers = lightning_scoped_item_offers(records, promotion_id, item_id, _validar_contexto_entry_campanha)
    return {lightning_offer_key(raw, campaign_id=promotion_id, item_id=item_id): raw for raw in offers}


def _promo_lightning_price(raw):
    value = raw.get("price") if "price" in raw else raw.get("deal_price")
    if isinstance(value, bool):
        return None
    value = _parse_float_flex(value)
    return value if value is not None and math.isfinite(value) and value > 0 else None


def _promo_lightning_conflicting_keys(records, promotion_id, item_id, cfg):
    seen, conflicts = {}, set()
    for raw in records if isinstance(records, list) else []:
        for key, entry in _promo_lightning_scoped_offers([raw], promotion_id, item_id, cfg).items():
            status = _lightning_status(entry)
            signature = (_promo_lightning_price(entry), lightning_stock_bounds(entry),
                         "candidate" if status in {"candidate", "eligible"} else status)
            if key in seen and seen[key] != signature:
                conflicts.add(key)
            seen[key] = signature
    return conflicts


def _promo_lightning_preexisting(raw, previous):
    reference = lightning_reference(raw)
    for entry in previous:
        if (reference and reference == lightning_reference(entry)) or lightning_same_window(raw, entry):
            return True
        if not lightning_window_valid(entry) and _promo_lightning_price(entry) == _promo_lightning_price(raw):
            # A previously observed participation without a calendar cannot be
            # linked to this new invitation merely because dates now appeared.
            return True
    return False


def _promo_lightning_confirmation_fields(raw):
    start, finish = lightning_offer_window(raw)
    return {"lightning_start_date": start, "lightning_finish_date": finish,
            "lightning_schedule_pending": not lightning_window_valid(raw), "status": _lightning_status(raw)}


def _promo_confirmar_oferta_lightning(client_id, loja, cfg, promotion_id, item_id, selected):
    """Bind the analyzed invitation to a fresh scoped GET before any submission."""
    blocked = lambda reason: {"_lightning_blocked": reason}
    selected_key = str(selected.get("lightning_offer_key") or "").strip()
    if not selected_key:
        return blocked("Oferta relampago sem identidade da janela. Execute uma nova analise."), cfg
    try:
        records, cfg = _ml_obter_promocoes_item(client_id, loja, cfg, item_id, force_refresh=True)
    except Exception:
        return blocked("Nao foi possivel confirmar as ofertas atuais deste anuncio. Reconecte a loja ou tente novamente."), cfg
    offers = _promo_lightning_scoped_offers(records, promotion_id, item_id, cfg)
    selected_window = {"start_date": selected.get("lightning_start_date"),
                       "finish_date": selected.get("lightning_finish_date")}
    selected_has_schedule = lightning_has_schedule_data(selected_window)
    if selected_has_schedule and not lightning_window_valid(selected_window):
        return blocked("Data e horario selecionados sao invalidos. Execute uma nova analise."), cfg
    if selected_key in _promo_lightning_conflicting_keys(records, promotion_id, item_id, cfg):
        return blocked("Os dados atuais deste convite sao conflitantes. Execute uma nova analise."), cfg
    raw = offers.get(selected_key)
    if raw is None and selected_has_schedule:
        same_window = [entry for entry in offers.values() if lightning_same_window(entry, selected_window)]
        if len(same_window) == 1:
            raw = same_window[0]
    if raw is None:
        return blocked("O convite selecionado nao foi encontrado nos dados atuais. Execute uma nova analise."), cfg
    if selected_has_schedule:
        if not lightning_same_window(raw, selected_window):
            return blocked("A janela selecionada mudou. Execute uma nova analise."), cfg
    elif lightning_has_schedule_data(raw):
        return blocked("O agendamento deste convite mudou. Execute uma nova analise."), cfg
    expected_reference = lightning_reference(selected)
    current_reference = lightning_reference(raw)
    if (expected_reference and current_reference and expected_reference != current_reference
            and expected_reference.split("-", 1)[0] == current_reference.split("-", 1)[0]):
        return blocked("A referencia do convite selecionado mudou. Execute uma nova analise."), cfg
    status = _lightning_status(raw)
    if status in _LIGHTNING_PARTICIPATING_STATES:
        selected_price = selected.get("action_deal_price") if "action_deal_price" in selected else selected.get("deal_price", selected.get("price"))
        if money_cents(selected_price) is None or money_cents(selected_price) != money_cents(_promo_lightning_price(raw)):
            return blocked("A participacao atual nao confirmou o preco selecionado. Execute uma nova analise."), cfg
        if not selected_has_schedule:
            return blocked("A participacao atual nao foi vinculada ao convite sem agenda. Execute uma nova analise."), cfg
        return {"success": True, "found": True, "already_participating": True,
                **_promo_lightning_confirmation_fields(raw)}, cfg
    schedule_pending = lightning_schedule_pending(raw, promotion_id, item_id)
    if not selected_has_schedule and not schedule_pending:
        return blocked("O convite sem agenda nao confirmou referencia, preco e estoque. Execute uma nova analise."), cfg
    if status not in {"candidate", "eligible"}:
        return blocked("Esta oferta nao esta disponivel para participacao."), cfg
    reason = lightning_stock_error(raw, selected.get("lightning_stock"))
    if reason:
        return blocked(reason), cfg
    minimum, maximum = lightning_stock_bounds(raw)
    for field, current in (("lightning_stock_min", minimum), ("lightning_stock_max", maximum)):
        if field in selected and lightning_integer(selected[field]) != current:
            return blocked("Os limites de estoque mudaram desde a analise. Execute uma nova analise."), cfg
    if schedule_pending:
        candidates = [entry for entry in offers.values()
                      if _lightning_status(entry) in {"candidate", "eligible"}
                      and lightning_reference(entry).startswith("CANDIDATE-")
                      and _promo_lightning_price(entry) == _promo_lightning_price(raw)]
        if len(candidates) != 1:
            return blocked("Ha mais de um convite sem agenda para este preco. Execute uma nova analise."), cfg
    raw = dict(raw)
    raw["_jk_lightning_confirmed_for_send"] = True
    raw["_jk_lightning_preexisting_participations"] = [copy.deepcopy(entry) for entry in offers.values()
        if _lightning_status(entry) in _LIGHTNING_PARTICIPATING_STATES]
    return {"success": True, "found": True, "_lightning_raw_confirmed": raw}, cfg


def _promo_aplicar_item_participacao_ml(
    client_id: str,
    loja: str,
    cfg: dict,
    *,
    item_id: str,
    promotion_id: str,
    promotion_type: str,
    offer_id: Optional[str] = None,
    deal_price: Optional[float] = None,
    discount_percentage: Optional[float] = None,
    allow_alternative_deal_price: bool = True,
    result_details: Optional[dict] = None,
    stock: Optional[int] = None,
    lightning_context: Optional[dict] = None,
    official_price_context: Optional[dict] = None,
) -> tuple[bool, str, dict]:
    # Opt-in outcome reporting keeps existing tuple callers and their defaults.
    if result_details is not None:
        result_details["outcome"] = "blocked"
    item_id = _promo_normalizar_mlb(item_id)
    promotion_id = str(promotion_id or "").strip()
    promotion_type = str(promotion_type or "").strip()
    if not promotion_type:
        promotion_type = "SMART" if promotion_id.upper().startswith("P-") else "SELLER_CAMPAIGN"
    if not item_id or not promotion_id:
        return False, "MLB ou promocao ausente", cfg

    def _extrair_numero_campo_promocao(valor) -> Optional[float]:
        if isinstance(valor, dict):
            for chave in (
                "amount",
                "value",
                "price",
                "deal_price",
                "suggested_discounted_price",
                "suggested_price",
                "min_discounted_price",
                "minimum_discounted_price",
                "min_deal_price",
            ):
                numero = _parse_float_flex(valor.get(chave))
                if numero is not None:
                    return numero
            return None
        return _parse_float_flex(valor)

    def _coletar_precos_fallback_credibilidade(raw_entry: dict, preco_referencia: Optional[float] = None) -> list[float]:
        if not isinstance(raw_entry, dict):
            return []
        prioridades = {
            "suggested_discounted_price": 0,
            "suggested_price": 1,
            "suggested_deal_price": 1,
            "recommended_discounted_price": 2,
            "recommended_price": 2,
            "min_discounted_price": 3,
            "minimum_discounted_price": 3,
            "min_deal_price": 3,
            "minimum_deal_price": 3,
            "deal_price": 4,
            "final_price": 4,
            "campaign_price": 4,
            "price": 5,
        }
        encontrados = []

        def _registrar(chave: str, valor):
            chave_norm = str(chave or "").strip().lower()
            if chave_norm not in prioridades:
                return
            numero = _extrair_numero_campo_promocao(valor)
            if numero is None or numero <= 0:
                return
            encontrados.append((prioridades[chave_norm], round(float(numero), 2), chave_norm))

        for chave, valor in raw_entry.items():
            _registrar(chave, valor)
        for caminho, valor in _ml_iterar_campos_payload_limitado(raw_entry, max_depth=7, max_nodes=3500):
            ultimo_campo = str(caminho or "").rsplit(".", 1)[-1].strip().lower()
            _registrar(ultimo_campo, valor)

        if not encontrados:
            return []
        referencia = _parse_float_flex(preco_referencia)
        vistos = set()
        ordenados = sorted(
            encontrados,
            key=lambda item: (
                item[0],
                abs(item[1] - float(referencia)) if referencia is not None else 0.0,
            ),
        )
        retorno = []
        for _prioridade, preco, _chave in ordenados:
            if preco <= 0:
                continue
            if preco in vistos:
                continue
            vistos.add(preco)
            retorno.append(preco)
        return retorno

    def _erro_credibilidade(texto_erro: str) -> bool:
        erro_norm = normalizar_texto(texto_erro or "")
        if not erro_norm:
            return False
        return (
            "error_credibility_discounted_price" in erro_norm
            or "error_credibility_price" in erro_norm
            or ("discounted price" in erro_norm and "credible" in erro_norm)
            or ("credibility" in erro_norm and "price" in erro_norm)
        )

    def _extrair_offer_id_promocao(raw_entry: dict) -> str:
        return _ml_promocao_raw_offer_id(raw_entry)

    def _erro_payload_ml(resp) -> str:
        try:
            data = resp.json() or {}
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""

        mensagens = []
        erros = data.get("errors")
        if isinstance(erros, list):
            for erro in erros[:5]:
                if isinstance(erro, dict):
                    mensagens.append(str(erro.get("error") or erro.get("message") or erro.get("cause") or erro))
                else:
                    mensagens.append(str(erro))

        for lista in (data.get("successful_ids"), data.get("results")):
            if not isinstance(lista, list):
                continue
            for item in lista[:8]:
                if isinstance(item, dict) and item.get("error"):
                    mensagens.append(str(item.get("error")))

        if mensagens:
            return " | ".join([m for m in mensagens if m][:5])
        return ""

    base_payload = {
        "promotion_id": promotion_id,
        "promotion_type": promotion_type,
    }
    promotion_type_upper = promotion_type.upper().strip()
    deal_price_num = _parse_float_flex(deal_price)
    discount_num = _parse_float_flex(discount_percentage)
    if result_details is not None:
        deal_price_num = deal_price_num if deal_price_num is not None and math.isfinite(deal_price_num) else None
        discount_num = discount_num if discount_num is not None and math.isfinite(discount_num) else None
    offer_id_texto = str(offer_id or "").strip()
    tipos_exigem_offer_id = {"SMART", "PRICE_MATCHING", "PRICE_MATCHING_MELI_ALL"}
    payloads = []
    payloads_vistos = set()

    def _adicionar_payload(payload: dict):
        if not isinstance(payload, dict):
            return
        assinatura = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        if assinatura in payloads_vistos:
            return
        payloads_vistos.add(assinatura)
        payloads.append(payload)

    if promotion_type_upper == "LIGHTNING":
        context = lightning_context if isinstance(lightning_context, dict) else {}
        schedule_pending = lightning_schedule_pending(context, promotion_id, item_id)
        if not context.get("_jk_lightning_confirmed_for_send") or not (lightning_window_valid(context) or schedule_pending):
            return False, "Convite da oferta relampago nao confirmado. Execute uma nova analise.", cfg
        stock_reason = lightning_stock_error(context, stock)
        if stock_reason:
            return False, stock_reason, cfg
        if deal_price_num is None or not math.isfinite(deal_price_num) or deal_price_num <= 0:
            return False, "Preco da oferta relampago ausente ou invalido.", cfg
        current_price = _promo_lightning_price(context)
        if current_price is None or not math.isfinite(current_price) or current_price <= 0:
            return False, "Preco atual desta oferta nao confirmado. Execute uma nova analise.", cfg
        if money_cents(current_price) != money_cents(deal_price_num) and official_price_context is None:
            return False, "O preco desta oferta mudou desde a analise. Execute uma nova analise antes de participar.", cfg
        # A server-reanalysed proposal is checked against this candidate's
        # official interval below; legacy exact callers keep suggestion equality.
        diagnostics = {"initial_price": money_cents(deal_price_num) / 100, "official_price": None,
            "sent_price": None, "standard_price": None, "sale_price": None, "min_price": None,
            "max_price": None, "recalculated_margin": None, "attempt_count": 0,
            "decision_code": "EXACT_PRICE_VALIDATED", "ml_error_code": "", "http_status": None}
        if result_details is not None:
            result_details["price_diagnostics"] = diagnostics
        context, reason, cfg = validate_exact_lightning(sys.modules[__name__], client_id, loja, cfg,
            promotion_id, item_id, context, deal_price_num, stock, diagnostics, official_price_context)
        if context is None:
            return False, reason, cfg
        lightning_context = context
        _adicionar_payload({
            "promotion_type": "LIGHTNING", "deal_price": money_cents(deal_price_num) / 100,
            "stock": lightning_integer(stock),
        })
        allow_alternative_deal_price = False
    elif promotion_type_upper in tipos_exigem_offer_id:
        raw_item_promocao = {}
        if not offer_id_texto:
            try:
                raw_item_promocao, cfg = _ml_obter_item_promocao_raw(
                    client_id,
                    loja,
                    cfg,
                    promotion_id,
                    promotion_type,
                    item_id,
                )
            except Exception:
                raw_item_promocao = {}
            if result_details is not None and raw_item_promocao:
                context_ids = (
                    ("_jk_campaign_id_consultado", promotion_id),
                    ("_jk_item_id_consultado", item_id),
                    ("_jk_promotion_type_consultado", promotion_type),
                )
                if any(
                    raw_item_promocao.get(key)
                    and str(raw_item_promocao[key]).upper() != expected.upper()
                    for key, expected in context_ids
                ):
                    return False, "A oferta retornada pertence a outra campanha ou anuncio.", cfg
                raw_item_promocao = _validar_contexto_entry_campanha(
                    raw_item_promocao, campaign_id=promotion_id,
                    promotion_type_consultado=promotion_type, item_id=item_id,
                )
            offer_id_texto = _extrair_offer_id_promocao(raw_item_promocao)
        if not offer_id_texto:
            return (
                False,
                (
                    f"Campanha {promotion_type_upper} exige offer_id do convite/candidato, "
                    "mas o Mercado Livre nao retornou esse identificador para o item."
                ),
                cfg,
            )
        if result_details is not None and any(
            _promo_normalizar_mlb(value) != item_id
            for value in re.findall(r"MLB[-_ ]?\d+", offer_id_texto.upper())
        ):
            return False, "O identificador da oferta pertence a outro anuncio.", cfg
        _adicionar_payload({**base_payload, "offer_id": offer_id_texto})
    # Em campanhas do vendedor com percentual fixo, o percentual ja foi usado para
    # calcular o preco cheio. Na adesao do item, a API espera o preco final
    # promocional (deal_price). Enviar fallbacks sem preco depois disso pode fazer
    # o ML recalcular a oferta de forma incorreta e rejeitar com FINAL_PRICE_*.
    elif deal_price_num is not None and deal_price_num > 0:
        _adicionar_payload({**base_payload, "deal_price": round(float(deal_price_num), 2)})
    elif discount_num is not None and discount_num > 0:
        _adicionar_payload({**base_payload, "discount_percentage": round(float(discount_num), 4)})
    elif promotion_type_upper not in {"SELLER_CAMPAIGN", "SELLER_COUPON_CAMPAIGN"}:
        _adicionar_payload(base_payload)

    if not payloads:
        return False, "Preco promocional ausente para aplicar a campanha.", cfg

    ultimo_erro = ""
    raw_credibilidade = None
    idx_payload = 0
    while idx_payload < len(payloads):
        payload = payloads[idx_payload]
        idx_payload += 1
        url_item_promocao = f"https://api.mercadolibre.com/seller-promotions/items/{item_id}"
        params_promocao = {"app_version": "v2"}
        if result_details is not None:
            result_details["outcome"] = "unknown"
            if promotion_type_upper == "LIGHTNING" and result_details.get("price_diagnostics"):
                result_details["price_diagnostics"]["decision_code"] = "PARTICIPATION_UNCONFIRMED"
            if promotion_type_upper == "LIGHTNING":
                result_details["submission_attempted"] = True
                result_details["price_diagnostics"].update(attempt_count=1, sent_price=money_cents(deal_price_num) / 100)
        try:
            resp, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                "POST",
                url_item_promocao,
                params=params_promocao,
                json=payload,
                timeout=20,
                **({"retry_unauthorized": False} if promotion_type_upper == "LIGHTNING" else {}),
            )
        except Exception:
            if result_details is None:
                raise
            if promotion_type_upper == "LIGHTNING":
                result_details["price_diagnostics"]["decision_code"] = "PARTICIPATION_UNCONFIRMED"
            return False, "Resultado nao confirmado apos a tentativa de inclusao.", cfg
        if result_details is not None:
            status_code = int(getattr(resp, "status_code", 0) or 0)
            result_details["http_status"] = status_code
            result_details["ml_error_code"] = ml_error_code(resp)
            if promotion_type_upper == "LIGHTNING":
                result_details["price_diagnostics"].update(http_status=status_code, ml_error_code=result_details["ml_error_code"],
                    decision_code="ML_PARTICIPATION_REJECTED")
            if status_code <= 0 or status_code == 408 or status_code >= 500:
                if promotion_type_upper == "LIGHTNING":
                    result_details["price_diagnostics"]["decision_code"] = "PARTICIPATION_UNCONFIRMED"
                return False, "Resultado nao confirmado pelo Mercado Livre apos a tentativa de inclusao.", cfg
            result_details["outcome"] = "rejected"
        if resp.status_code in (200, 201):
            if result_details is not None:
                try:
                    response_data = resp.json()
                except Exception:
                    response_data = None
                if not isinstance(response_data, dict):
                    result_details["outcome"] = "unknown"
                    if promotion_type_upper == "LIGHTNING" and result_details.get("price_diagnostics"):
                        result_details["price_diagnostics"]["decision_code"] = "PARTICIPATION_UNCONFIRMED"
                    return False, "Mercado Livre retornou uma resposta sem resultado reconhecivel.", cfg
                if response_data.get("error") or response_data.get("success") is False:
                    return False, str(response_data.get("message") or response_data.get("error") or "Mercado Livre recusou a inclusao."), cfg
            erro_payload = _erro_payload_ml(resp)
            if erro_payload:
                ultimo_erro = f"Mercado Livre respondeu {resp.status_code}, mas recusou a promocao: {erro_payload}"
                continue
            if promotion_type_upper == "LIGHTNING":
                try:
                    confirmed = resp.json()
                except Exception:
                    confirmed = {}
                confirmed_price = _promo_lightning_price(confirmed) if isinstance(confirmed, dict) else None
                if (confirmed_price is None or not math.isfinite(confirmed_price)
                        or money_cents(confirmed_price) != money_cents(deal_price_num)):
                    if result_details is not None:
                        result_details["outcome"] = "unknown"
                        if promotion_type_upper == "LIGHTNING" and result_details.get("price_diagnostics"):
                            result_details["price_diagnostics"]["decision_code"] = "PARTICIPATION_UNCONFIRMED"
                    return False, "Inclusao enviada, mas o preco retornado nao confirmou a oferta selecionada. Verifique no Mercado Livre.", cfg
                response_id = str(confirmed.get("id") or "").upper()
                response_item = str(confirmed.get("item_id") or confirmed.get("itemId") or "").upper()
                response_campaign = str(confirmed.get("promotion_id") or confirmed.get("campaign_id") or "").upper()
                if ((response_id.startswith("LGH-") and response_id != promotion_id.upper())
                        or (response_id.startswith("MLB") and response_id != item_id.upper())
                        or (response_item and response_item != item_id.upper())
                        or (response_campaign and response_campaign != promotion_id.upper())):
                    if result_details is not None:
                        result_details["outcome"] = "unknown"
                        if promotion_type_upper == "LIGHTNING" and result_details.get("price_diagnostics"):
                            result_details["price_diagnostics"]["decision_code"] = "PARTICIPATION_UNCONFIRMED"
                    return False, "A resposta de inclusao possui identidade divergente. Verifique no Mercado Livre.", cfg
                if not _promo_lightning_owner_matches(confirmed, cfg):
                    if result_details is not None:
                        result_details["outcome"] = "unknown"
                        if promotion_type_upper == "LIGHTNING" and result_details.get("price_diagnostics"):
                            result_details["price_diagnostics"]["decision_code"] = "PARTICIPATION_UNCONFIRMED"
                    return False, "A resposta de inclusao possui vendedor divergente. Verifique no Mercado Livre.", cfg
                response_scope = dict(confirmed)
                response_scope.setdefault("item_id", item_id)
                response_scope = _validar_contexto_entry_campanha(
                    response_scope, campaign_id=promotion_id,
                    promotion_type_consultado="LIGHTNING", item_id=item_id,
                )
                if not response_scope:
                    if result_details is not None:
                        result_details["outcome"] = "unknown"
                        if promotion_type_upper == "LIGHTNING" and result_details.get("price_diagnostics"):
                            result_details["price_diagnostics"]["decision_code"] = "PARTICIPATION_UNCONFIRMED"
                    return False, "A resposta de inclusao possui contexto divergente. Verifique no Mercado Livre.", cfg
                if (not schedule_pending and lightning_window_valid(confirmed)
                        and not lightning_same_window(confirmed, lightning_context)):
                    if result_details is not None:
                        result_details["outcome"] = "unknown"
                        if promotion_type_upper == "LIGHTNING" and result_details.get("price_diagnostics"):
                            result_details["price_diagnostics"]["decision_code"] = "PARTICIPATION_UNCONFIRMED"
                    return False, "A resposta de inclusao confirmou outra janela. Verifique no Mercado Livre.", cfg
                direct_matches = _promo_lightning_scoped_offers([confirmed], promotion_id, item_id, cfg)
                verified = [entry for entry in direct_matches.values()
                            if not schedule_pending and lightning_same_window(entry, lightning_context)
                            and _lightning_status(entry) in _LIGHTNING_PARTICIPATING_STATES
                            and not _promo_lightning_preexisting(entry, lightning_context.get("_jk_lightning_preexisting_participations", []))]
                if not verified:
                    # Scheduling is assigned by ML; only a new participation
                    # observed in a fresh GET can confirm an undated invitation.
                    try:
                        records, cfg = _ml_obter_promocoes_item(client_id, loja, cfg, item_id, force_refresh=True)
                    except Exception:
                        records = []
                    matches = _promo_lightning_scoped_offers(records, promotion_id, item_id, cfg)
                    conflicting_keys = _promo_lightning_conflicting_keys(records, promotion_id, item_id, cfg)
                    previous = lightning_context.get("_jk_lightning_preexisting_participations", [])
                    verified = []
                    for key, entry in matches.items():
                        price = _promo_lightning_price(entry)
                        if (key in conflicting_keys or _lightning_status(entry) not in _LIGHTNING_PARTICIPATING_STATES
                                or price is None or money_cents(price) != money_cents(deal_price_num)):
                            continue
                        if _promo_lightning_preexisting(entry, previous):
                            continue
                        if schedule_pending:
                            if lightning_has_schedule_data(entry) and not lightning_window_valid(entry):
                                continue
                            if not lightning_window_valid(entry) and not re.fullmatch(
                                rf"OFFER-{re.escape(item_id)}-.+", lightning_reference(entry)
                            ):
                                continue
                        elif not lightning_same_window(entry, lightning_context):
                            continue
                        verified.append(entry)
                if len(verified) != 1:
                    if result_details is not None:
                        result_details["outcome"] = "unknown"
                        if promotion_type_upper == "LIGHTNING" and result_details.get("price_diagnostics"):
                            result_details["price_diagnostics"]["decision_code"] = "PARTICIPATION_UNCONFIRMED"
                    return False, "Inclusao enviada, mas a participacao selecionada ainda nao foi confirmada. Verifique no Mercado Livre.", cfg
                if result_details is not None:
                    result_details.update(_promo_lightning_confirmation_fields(verified[0]))
            if result_details is not None:
                result_details["outcome"] = "applied"
                if promotion_type_upper == "LIGHTNING":
                    result_details["price_diagnostics"]["decision_code"] = "PARTICIPATION_CONFIRMED"
            return True, "", cfg
        if promotion_type_upper == "LIGHTNING" and resp.status_code == 401:
            if result_details is not None:
                result_details["price_diagnostics"]["decision_code"] = "ML_AUTHENTICATION_REQUIRED"
            return False, "Mercado Livre recusou a autenticacao da inclusao. Reconecte a loja e execute uma nova analise; nenhum reenvio foi realizado.", cfg
        erro = participation_error_detail(resp, f"Erro {resp.status_code} ao incluir item")
        erro_payload = _erro_payload_ml(resp)
        erro_completo = f"{erro} | {erro_payload}" if erro_payload else erro
        erro_norm = normalizar_texto(erro_completo)
        if _promo_erro_candidate_not_found(erro_completo):
            return (
                False,
                (
                    "CANDIDATE_NOT_FOUND: o Mercado Livre informou que este MLB nao esta como "
                    "candidate/elegivel para essa campanha."
                ),
                cfg,
            )
        if "already" in erro_norm or ("ja" in erro_norm and "promoc" in erro_norm):
            if promotion_type_upper == "LIGHTNING":
                try:
                    records, cfg = _ml_obter_promocoes_item(client_id, loja, cfg, item_id, force_refresh=True)
                except Exception:
                    records = []
                matches = _promo_lightning_scoped_offers(records, promotion_id, item_id, cfg)
                conflicting = _promo_lightning_conflicting_keys(records, promotion_id, item_id, cfg)
                context_reference = lightning_reference(lightning_context)
                verified = [entry for key, entry in matches.items()
                            if key not in conflicting and not schedule_pending and lightning_same_window(entry, lightning_context)
                            and _lightning_status(entry) in _LIGHTNING_PARTICIPATING_STATES
                            and money_cents(_promo_lightning_price(entry)) == money_cents(deal_price_num)
                            and not _promo_lightning_preexisting(entry, lightning_context.get("_jk_lightning_preexisting_participations", []))
                            and not (context_reference and lightning_reference(entry)
                                and context_reference != lightning_reference(entry)
                                and context_reference.split("-", 1)[0] == lightning_reference(entry).split("-", 1)[0])]
                if len(verified) == 1:
                    if result_details is not None:
                        result_details["outcome"] = "already_participating"
                        result_details.update(_promo_lightning_confirmation_fields(verified[0]))
                    return True, "Esta janela ja esta participando ou pendente.", cfg
                return False, "Mercado Livre informou participacao existente; a janela selecionada nao foi confirmada.", cfg
            if result_details is not None:
                try:
                    existing, cfg = _promo_consultar_item_na_campanha(
                        client_id, loja, cfg, promotion_id, promotion_type, item_id,
                    )
                except Exception:
                    existing = {}
                if (isinstance(existing, dict) and existing.get("success")
                        and existing.get("found") and existing.get("already_participating")):
                    result_details["outcome"] = "already_participating"
                    return True, "Item ja esta participando na campanha.", cfg
                return False, erro_completo, cfg
            edit_resp, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                "PUT",
                url_item_promocao,
                params=params_promocao,
                json=payload,
                timeout=20,
            )
            if edit_resp.status_code in (200, 201):
                erro_payload = _erro_payload_ml(edit_resp)
                if erro_payload:
                    ultimo_erro = f"Mercado Livre respondeu {edit_resp.status_code}, mas nao atualizou a promocao: {erro_payload}"
                    continue
                return True, "Participacao existente atualizada", cfg
            ultimo_erro = _ml_parse_error_detail(edit_resp, f"Erro {edit_resp.status_code} ao atualizar item na promocao")
            continue
        if _erro_credibilidade(erro_completo):
            if not allow_alternative_deal_price:
                if result_details is not None and promotion_type_upper == "LIGHTNING":
                    result_details["price_diagnostics"]["decision_code"] = "ML_PRICE_CREDIBILITY_REJECTED"
                ultimo_erro = (
                    "Mercado Livre rejeitou o preco promocional exato por credibilidade; "
                    "nenhum valor alternativo foi enviado. "
                    f"Detalhe: {erro_completo}"
                )
                continue
            if raw_credibilidade is None:
                try:
                    raw_credibilidade, cfg = _ml_obter_item_promocao_raw(
                        client_id,
                        loja,
                        cfg,
                        promotion_id,
                        promotion_type,
                        item_id,
                    )
                except Exception:
                    raw_credibilidade = {}
            precos_fallback = _coletar_precos_fallback_credibilidade(
                raw_credibilidade or {},
                preco_referencia=deal_price_num,
            )
            for preco_alt in precos_fallback:
                _adicionar_payload({**base_payload, "deal_price": round(float(preco_alt), 2)})
            if discount_num is not None and discount_num > 0:
                _adicionar_payload({**base_payload, "discount_percentage": round(float(discount_num), 4)})
            if idx_payload < len(payloads):
                ultimo_erro = (
                    "Mercado Livre rejeitou o preco promocional por credibilidade e o sistema tentou valor sugerido/fallback automaticamente."
                )
                continue
        ultimo_erro = erro_completo
    return False, ultimo_erro or "Mercado Livre nao aceitou a inclusao do item", cfg


def _aplicar_participacoes_promocoes_payload(
    req: PromoAplicarParticipacaoRequest,
    client_id: str,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    loja = str(req.loja or "").strip()
    if not loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    promocoes = req.promocoes if isinstance(req.promocoes, list) else []
    if not promocoes:
        raise HTTPException(status_code=400, detail="Nenhuma promocao enviada para aplicar.")

    validar_promocoes_por_anuncio(promocoes=promocoes)
    cfg = _obter_cfg_ml(client_id, loja)
    reanalysis_context = None
    context_error = ""
    if req.price_policy == "refresh_and_reanalyse":
        try:
            reanalysis_context = carregar_contexto_reanalise(req.analysis_job_id, client_id, loja, cfg)
        except Exception as exc:
            context_error = str(exc.detail) if isinstance(exc, HTTPException) else (str(exc) if isinstance(exc, ValueError) else "Contexto da analise nao confirmado.")
    resumo = []
    detalhes = []
    attempted_lightning = {}
    total_sucesso = 0
    total_falha = 0
    total_ignorados = 0
    total_itens = sum(
        len(grupo.get("items") or [])
        for grupo in promocoes
        if isinstance(grupo, dict) and isinstance(grupo.get("items"), list)
    )
    processados = 0

    def _notificar(mensagem: str):
        if not progress_callback:
            return
        progresso = 5
        if total_itens > 0:
            progresso = min(98, max(5, int((processados / total_itens) * 93) + 5))
        try:
            progress_callback({
                "progress": progresso,
                "message": mensagem,
                "processed": processados,
                "total": total_itens,
                "total_sucesso": total_sucesso,
                "total_falha": total_falha,
                "total_ignorados": total_ignorados,
            })
        except Exception:
            logger.exception("[PROMO APPLY] Falha ao atualizar progresso")

    _notificar(f"Iniciando entrada em {total_itens} anuncio(s) nas promocoes...")

    for group_index, grupo in enumerate(promocoes):
        if not isinstance(grupo, dict):
            continue
        promotion_id = str(grupo.get("promotion_id") or grupo.get("promo_b_id") or grupo.get("id") or "").strip()
        promotion_type = str(grupo.get("promotion_type") or grupo.get("promo_b_type") or "").strip()
        nome = str(grupo.get("nome") or grupo.get("name") or grupo.get("promo_b_nome") or promotion_id).strip()
        items = grupo.get("items") if isinstance(grupo.get("items"), list) else []
        promo_resumo = {
            "promotion_id": promotion_id,
            "promotion_type": promotion_type,
            "nome": nome,
            "sucesso": 0,
            "falhas": 0,
            "ignorados": 0,
            "erros": [],
        }
        for item_index, item in enumerate(items):
            valid_item = isinstance(item, dict)
            item = item if valid_item else {}
            item_id = _promo_normalizar_mlb(item.get("item_id") or item.get("mlb") or item.get("MLB"))
            detail = {
                "client_ref": str(item.get("client_ref") or f"{group_index}:{item_index}"),
                "item_id": item_id,
                "promotion_id": promotion_id,
                "promotion_type": promotion_type,
                "outcome": "blocked",
                "status": "",
            }
            if promotion_type.upper() == "LIGHTNING":
                detail["price_diagnostics"] = {"initial_price": price_number(item.get("action_deal_price", item.get("deal_price"))),
                    "official_price": None, "sent_price": None, "standard_price": None, "sale_price": None,
                    "min_price": None, "max_price": None, "recalculated_margin": None, "attempt_count": 0,
                    "decision_code": "OFFICIAL_INVITATION_UNCONFIRMED", "ml_error_code": "", "http_status": None}
                for field in LIGHTNING_ROW_FIELDS:
                    if field in item:
                        detail[field] = item[field]
            message = ""
            result_details = {}
            lightning_identity = (promotion_id.upper(), item_id, str(item.get("lightning_offer_key") or ""))
            lightning_selection = (item.get("action_deal_price", item.get("deal_price", item.get("price"))),
                                   item.get("lightning_stock"), item.get("lightning_start_date"), item.get("lightning_finish_date"))
            try:
                row_context = None
                row_context_error = ""
                if promotion_type.upper() == "LIGHTNING" and req.price_policy == "refresh_and_reanalyse":
                    row_context_error = context_error
                    if not row_context_error:
                        try:
                            reference = str(item.get("analysis_client_ref") or item.get("client_ref") or "")
                            row_context = resolver_linha_reanalise(reanalysis_context, reference, promotion_id, item_id, item)
                        except Exception as exc:
                            row_context_error = str(exc.detail) if isinstance(exc, HTTPException) else (str(exc) if isinstance(exc, ValueError) else "Contexto da linha nao confirmado para reanalise.")
                action_campaign = str(item.get("action_promotion_id") or "").strip()
                if not valid_item:
                    message = "Entrada invalida para participacao."
                elif not promotion_id:
                    message = "Promocao sem ID."
                elif not item_id or not re.fullmatch(r"MLB\d+", item_id):
                    message = "MLB ausente ou invalido."
                elif action_campaign and action_campaign != promotion_id:
                    message = "Os dados de execucao pertencem a outra campanha. Execute uma nova analise."
                elif row_context_error:
                    message = row_context_error
                    detail["price_diagnostics"].update(attempt_count=0, decision_code="ANALYSIS_ROW_UNCONFIRMED")
                elif promotion_type.upper() == "LIGHTNING" and lightning_identity in attempted_lightning:
                    previous, previous_selection = attempted_lightning[lightning_identity]
                    if previous["outcome"] in {"applied", "already_participating"} and lightning_selection == previous_selection:
                        detail["outcome"] = "already_participating"
                        for field in ("lightning_start_date", "lightning_finish_date", "lightning_schedule_pending", "status", "price_diagnostics", "analysis_row_updates"):
                            if field in previous:
                                detail[field] = previous[field]
                        message = "Este convite ja teve inclusao confirmada nesta operacao."
                    else:
                        detail["outcome"] = "unknown"
                        message = "Este convite ja teve uma tentativa de inclusao nesta operacao. Verifique no Mercado Livre."
                elif promotion_type.upper() == "LIGHTNING" and req.price_policy == "refresh_and_reanalyse":
                    if context_error:
                        message = context_error
                        detail["price_diagnostics"].update(attempt_count=0, decision_code="ANALYSIS_CONTEXT_UNCONFIRMED")
                    else:
                        try:
                            from backend.services import promocoes_api_analise as analysis
                            ok, message, cfg = apply_reanalysed_lightning(
                                sys.modules[__name__], analysis, client_id, loja, cfg,
                                campaign=promotion_id, item_id=item_id, selected=item,
                                row_context=row_context, result_details=result_details)
                            detail["outcome"] = result_details.get("outcome") or "blocked"
                            for field in ("status", "lightning_start_date", "lightning_finish_date",
                                          "lightning_schedule_pending", "price_diagnostics", "analysis_row_updates"):
                                if field in result_details:
                                    detail[field] = result_details[field]
                            if ok and not message:
                                message = "Inclusao confirmada pelo Mercado Livre."
                        except Exception as exc:
                            message = str(exc.detail) if isinstance(exc, HTTPException) else (str(exc) if isinstance(exc, ValueError) else "Contexto da linha nao confirmado para reanalise.")
                            detail["price_diagnostics"].update(attempt_count=0, decision_code="ANALYSIS_ROW_UNCONFIRMED")
                else:
                    # An inconclusive lookup does not revoke the user's selection.
                    try:
                        if promotion_type.upper() == "LIGHTNING":
                            elegibilidade, cfg = _promo_confirmar_oferta_lightning(
                                client_id, loja, cfg, promotion_id, item_id, item,
                            )
                        else:
                            elegibilidade, cfg = _promo_consultar_item_na_campanha(
                                client_id, loja, cfg, promotion_id, promotion_type, item_id,
                            )
                    except Exception:
                        elegibilidade = ({"_lightning_blocked": "Nao foi possivel confirmar a janela desta oferta."}
                                         if promotion_type.upper() == "LIGHTNING" else {})
                    elegibilidade = elegibilidade if isinstance(elegibilidade, dict) else {}
                    if elegibilidade.get("_lightning_blocked"):
                        message = elegibilidade["_lightning_blocked"]
                    elif (elegibilidade.get("success") and elegibilidade.get("found")
                            and elegibilidade.get("already_participating")):
                        detail["outcome"] = "already_participating"
                        detail["status"] = elegibilidade.get("status") or "started"
                        for field in ("lightning_start_date", "lightning_finish_date", "lightning_schedule_pending"):
                            if field in elegibilidade:
                                detail[field] = elegibilidade[field]
                        message = "Item ja esta participando ou pendente na campanha."
                    else:
                        has_action = any(key in item for key in (
                            "action_offer_id", "action_deal_price", "action_discount_percentage",
                        ))
                        offer_id = str((item.get("action_offer_id") if has_action else (
                            item.get("offer_id") or item.get("offerId") or item.get("ref_id") or item.get("refId")
                        )) or "").strip()
                        deal_price = _parse_float_flex(item.get("action_deal_price") if has_action else (
                            item.get("deal_price") or item.get("preco_final_ml") or item.get("price")
                        ))
                        discount_percentage = _parse_float_flex(item.get("action_discount_percentage") if has_action else (
                            item.get("discount_percentage") or item.get("percentual") or item.get("ml_pct")
                        ))
                        offer_items = re.findall(r"MLB[-_ ]?\d+", offer_id.upper())
                        if any(_promo_normalizar_mlb(value) != item_id for value in offer_items):
                            message = "O identificador da oferta pertence a outro anuncio."
                        else:
                            result_details = {}
                            lightning_kwargs = {}
                            if promotion_type.upper() == "LIGHTNING":
                                lightning_kwargs = {
                                    "stock": item.get("lightning_stock"),
                                    "lightning_context": elegibilidade.get("_lightning_raw_confirmed"),
                                }
                            ok, message, cfg = _promo_aplicar_item_participacao_ml(
                                client_id, loja, cfg,
                                item_id=item_id,
                                promotion_id=promotion_id,
                                promotion_type=promotion_type,
                                offer_id=offer_id,
                                deal_price=deal_price,
                                discount_percentage=discount_percentage,
                                allow_alternative_deal_price=False,
                                result_details=result_details,
                                **lightning_kwargs,
                            )
                            detail["outcome"] = result_details.get("outcome") or ("applied" if ok else "unknown")
                            for field in ("lightning_start_date", "lightning_finish_date", "lightning_schedule_pending", "status", "price_diagnostics", "analysis_row_updates"):
                                if field in result_details:
                                    detail[field] = result_details[field]
                            if ok and not message:
                                message = "Inclusao confirmada pelo Mercado Livre."
            except Exception:
                # Preserve preceding results and continue with the next selected row.
                detail["outcome"] = "unknown"
                message = "Nao foi possivel confirmar o resultado deste anuncio."

            if promotion_type.upper() == "LIGHTNING" and result_details.get("submission_attempted"):
                attempted_lightning[lightning_identity] = (dict(detail), lightning_selection)
            outcome = detail["outcome"]
            detail["success"] = outcome in {"applied", "already_participating"}
            detail["ignored"] = outcome == "already_participating"
            detail["message"] = message
            if outcome == "applied":
                total_sucesso += 1
                promo_resumo["sucesso"] += 1
            elif outcome == "already_participating":
                total_ignorados += 1
                promo_resumo["ignorados"] += 1
            else:
                total_falha += 1
                promo_resumo["falhas"] += 1
                detail["error"] = message
                if len(promo_resumo["erros"]) < 8:
                    promo_resumo["erros"].append(f"{item_id or detail['client_ref']}: {message}")
            detalhes.append(detail)
            processados += 1
            _notificar(f"Entrando nas promocoes: {processados}/{total_itens} anuncio(s).")
        resumo.append(promo_resumo)

    return {
        "success": total_falha == 0,
        "loja": loja,
        "total_itens": total_itens,
        "total_sucesso": total_sucesso,
        "total_falha": total_falha,
        "total_ignorados": total_ignorados,
        "detalhes": detalhes,
        "promocoes": resumo,
    }


def aplicar_participacoes_promocoes(req: PromoAplicarParticipacaoRequest, client_id: str = Depends(get_tenant_id)):
    return _aplicar_participacoes_promocoes_payload(req, client_id)


def aplicar_participacoes_promocoes_start(req: PromoAplicarParticipacaoRequest, client_id: str = Depends(get_tenant_id)):
    payload_req = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    loja = str(payload_req.get("loja") or "").strip()
    promocoes = payload_req.get("promocoes") if isinstance(payload_req.get("promocoes"), list) else []
    total_itens = sum(
        len(grupo.get("items") or [])
        for grupo in promocoes
        if isinstance(grupo, dict) and isinstance(grupo.get("items"), list)
    )
    if not loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    if total_itens <= 0:
        raise HTTPException(status_code=400, detail="Nenhuma promocao enviada para aplicar.")
    validar_promocoes_por_anuncio(promocoes=promocoes)
    job_id = uuid.uuid4().hex
    _promo_job_set(
        job_id,
        client_id=client_id,
        status="queued",
        progress=0,
        message=f"Entrada em {total_itens} anuncio(s) enviada para segundo plano.",
        result=None,
        error="",
    )
    threading.Thread(
        target=_promo_aplicar_participacoes_job_worker,
        args=(job_id, payload_req, client_id),
        name=f"promo-apply-{job_id[:8]}",
        daemon=True,
    ).start()
    return {
        "success": True,
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "message": f"Entrada em {total_itens} anuncio(s) enviada para segundo plano.",
    }


def aplicar_participacoes_promocoes_job(job_id: str, client_id: str = Depends(get_tenant_id)):
    job = _promo_job_get(job_id)
    if not job or job.get("client_id") != client_id:
        raise HTTPException(status_code=404, detail="Job de participacao em promocoes nao encontrado.")
    payload = dict(job)
    payload.pop("client_id", None)
    payload["success"] = True
    payload["job_id"] = job_id
    return payload

PEER_EXPORTS = ['_promo_aplicar_item_participacao_ml', '_aplicar_participacoes_promocoes_payload', 'aplicar_participacoes_promocoes', 'aplicar_participacoes_promocoes_start', 'aplicar_participacoes_promocoes_job']
__all__ = PEER_EXPORTS + ["configure_promocoes_api_participacoes_runtime"]

configure_promocoes_api_participacoes_runtime()
