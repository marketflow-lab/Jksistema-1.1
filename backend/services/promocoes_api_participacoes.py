"""Internal slice for promocoes_api."""

from __future__ import annotations

from __future__ import annotations
import asyncio
import inspect
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


def configure_promocoes_api_participacoes_runtime(runtime_module=None, peers=None):
    _configure_common = globals().get("configure_promocoes_common_runtime")
    if callable(_configure_common):
        try:
            _configure_common(runtime_module)
        except TypeError:
            _configure_common()
    runtime = bind_runtime_globals(globals(), runtime_module)
    if peers:
        globals().update(peers)
    return runtime


configure_promocoes_api_participacoes_runtime()


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    raise RuntimeError("Promocoes API runtime was not configured.")


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
) -> tuple[bool, str, dict]:
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

    if promotion_type_upper in tipos_exigem_offer_id:
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
        resp, cfg = _ml_api_request(
            client_id,
            loja,
            cfg,
            "POST",
            url_item_promocao,
            params=params_promocao,
            json=payload,
            timeout=20,
        )
        if resp.status_code in (200, 201):
            erro_payload = _erro_payload_ml(resp)
            if erro_payload:
                ultimo_erro = f"Mercado Livre respondeu {resp.status_code}, mas recusou a promocao: {erro_payload}"
                continue
            return True, "", cfg
        erro = _ml_parse_error_detail(resp, f"Erro {resp.status_code} ao incluir item")
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

    cfg = _obter_cfg_ml(client_id, loja)
    resumo = []
    detalhes = []
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

    for grupo in promocoes:
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
        if not promotion_id:
            promo_resumo["falhas"] = len(items)
            promo_resumo["erros"].append("Promocao sem ID.")
            total_falha += len(items)
            processados += len(items)
            _notificar(f"Promocao sem ID ignorada ({processados}/{total_itens}).")
            resumo.append(promo_resumo)
            continue
        for item in items:
            if not isinstance(item, dict):
                total_ignorados += 1
                promo_resumo["ignorados"] += 1
                processados += 1
                _notificar(f"Item invalido ignorado ({processados}/{total_itens}).")
                continue
            item_id = _promo_normalizar_mlb(item.get("item_id") or item.get("mlb") or item.get("MLB"))
            offer_id = str(
                item.get("offer_id")
                or item.get("offerId")
                or item.get("ref_id")
                or item.get("refId")
                or ""
            ).strip()
            deal_price = _parse_float_flex(item.get("deal_price") or item.get("preco_final_ml") or item.get("price"))
            discount_percentage = _parse_float_flex(item.get("discount_percentage") or item.get("percentual") or item.get("ml_pct"))
            if not item_id:
                total_ignorados += 1
                promo_resumo["ignorados"] += 1
                processados += 1
                _notificar(f"Item sem MLB ignorado ({processados}/{total_itens}).")
                continue
            elegibilidade, cfg = _promo_consultar_item_na_campanha(
                client_id,
                loja,
                cfg,
                promotion_id,
                promotion_type,
                item_id,
            )
            if elegibilidade.get("success") and elegibilidade.get("found"):
                if elegibilidade.get("already_participating"):
                    total_ignorados += 1
                    promo_resumo["ignorados"] += 1
                    processados += 1
                    status_atual = elegibilidade.get("status") or "started"
                    if len(promo_resumo["erros"]) < 8:
                        promo_resumo["erros"].append(f"{item_id}: ja esta na campanha ({status_atual}).")
                    if len(detalhes) < 300:
                        detalhes.append({
                            "item_id": item_id,
                            "promotion_id": promotion_id,
                            "promotion_type": promotion_type,
                            "success": True,
                            "ignored": True,
                            "status": status_atual,
                            "message": "Item ja esta participando ou pendente na campanha.",
                        })
                    _notificar(f"{item_id} ja esta na campanha; ignorado ({processados}/{total_itens}).")
                    continue
                if not elegibilidade.get("can_participate"):
                    total_ignorados += 1
                    promo_resumo["ignorados"] += 1
                    processados += 1
                    status_atual = elegibilidade.get("status") or "-"
                    if len(promo_resumo["erros"]) < 8:
                        promo_resumo["erros"].append(f"{item_id}: status {status_atual}; nao esta candidate.")
                    if len(detalhes) < 300:
                        detalhes.append({
                            "item_id": item_id,
                            "promotion_id": promotion_id,
                            "promotion_type": promotion_type,
                            "success": True,
                            "ignored": True,
                            "status": status_atual,
                            "message": "Item nao esta com status candidate para essa campanha.",
                        })
                    _notificar(f"{item_id} nao esta candidate; ignorado ({processados}/{total_itens}).")
                    continue
            elif elegibilidade.get("success") and not elegibilidade.get("found"):
                total_ignorados += 1
                promo_resumo["ignorados"] += 1
                processados += 1
                detalhe = str(elegibilidade.get("detail") or "Nao elegivel para essa campanha.")
                if len(promo_resumo["erros"]) < 8:
                    promo_resumo["erros"].append(f"{item_id}: nao elegivel/candidate nesta campanha.")
                if len(detalhes) < 300:
                    detalhes.append({
                        "item_id": item_id,
                        "promotion_id": promotion_id,
                        "promotion_type": promotion_type,
                        "success": True,
                        "ignored": True,
                        "status": "not_candidate",
                        "message": detalhe,
                    })
                _notificar(f"{item_id} nao e candidato da campanha; ignorado ({processados}/{total_itens}).")
                continue
            ok, erro, cfg = _promo_aplicar_item_participacao_ml(
                client_id,
                loja,
                cfg,
                item_id=item_id,
                promotion_id=promotion_id,
                promotion_type=promotion_type,
                offer_id=offer_id,
                deal_price=deal_price,
                discount_percentage=discount_percentage,
            )
            if ok:
                total_sucesso += 1
                promo_resumo["sucesso"] += 1
            else:
                if _promo_erro_candidate_not_found(erro):
                    total_ignorados += 1
                    promo_resumo["ignorados"] += 1
                    if len(promo_resumo["erros"]) < 8:
                        promo_resumo["erros"].append(f"{item_id}: nao elegivel/candidate nesta campanha.")
                    if len(detalhes) < 300:
                        detalhes.append({
                            "item_id": item_id,
                            "promotion_id": promotion_id,
                            "promotion_type": promotion_type,
                            "success": True,
                            "ignored": True,
                            "status": "candidate_not_found",
                            "message": erro,
                        })
                    processados += 1
                    _notificar(f"{item_id} nao e candidato da campanha; ignorado ({processados}/{total_itens}).")
                    continue
                total_falha += 1
                promo_resumo["falhas"] += 1
                if len(promo_resumo["erros"]) < 8:
                    promo_resumo["erros"].append(f"{item_id}: {erro}")
                if len(detalhes) < 300:
                    detalhes.append({
                        "item_id": item_id,
                        "promotion_id": promotion_id,
                        "promotion_type": promotion_type,
                        "success": False,
                        "error": erro,
                    })
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
