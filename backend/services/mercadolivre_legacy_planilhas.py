"""Internal slice for mercadolivre_legacy_core."""

from __future__ import annotations

from __future__ import annotations
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


def configure_mercadolivre_legacy_planilhas_runtime(runtime_module=None, peers=None):
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


configure_mercadolivre_legacy_planilhas_runtime()

PROMO_DESCONTO_ML_NAO_INFORMADO = "Não informado pela API"
PROMO_DESCONTO_ML_CONFIAVEL_KEY = "_jk_desconto_ml_confiavel"
PROMO_DESCONTO_ML_FONTE_KEY = "_jk_desconto_ml_fonte"


def _normalizar_sku_saida(v):
    sku_raw = str(v or "").strip()
    if not sku_raw:
        return ""
    partes = [p.strip() for p in sku_raw.split(",") if p.strip()]
    if not partes:
        return ""
    return ", ".join(_normalizar_sku_mes(p) for p in partes)


def _build_df_planilha_analise_promo(dados_analise: list[dict], limpar_status_exibicao: bool = False) -> pd.DataFrame:
    linhas = []
    for item in dados_analise or []:
        item = dict(item or {})
        preco_final = _to_float_safe(item.get('M 21 Fixa'))
        preco_final_ml = _to_float_safe(item.get('M ML'))
        preco_final_ml_display = _to_float_safe(item.get('preco_final_ml_display') or item.get('recebe_ml'))
        custo = _to_float_safe(item.get('Custo'))
        taxa_pct = _to_rate_safe(item.get('%'))
        imposto_pct_rate = _to_rate_safe(item.get('Imposto %', item.get('Imposto', '')))
        desconto_ml_val = _to_float_safe(item.get('Desconto ML'))
        desconto_ml_fonte = str(item.get(PROMO_DESCONTO_ML_FONTE_KEY) or "").strip()
        desconto_ml_confiavel = bool(
            item.get(PROMO_DESCONTO_ML_CONFIAVEL_KEY) is True
            and desconto_ml_fonte
        )

        # Normaliza preÃ§os finais priorizando campos explÃƒÂ­citos quando disponÃƒÂ­veis.
        preco_final_base = _to_float_safe(item.get('PreÃ§o Final'))
        if preco_final_base is not None:
            preco_final = preco_final_base
        preco_final_ml_base = _to_float_safe(
            item.get('deal_price')
            or item.get('preco_promocional_ml')
            or item.get('PreÃ§o Promocional ML')
        )
        if preco_final_ml_base is None:
            preco_final_ml_base = _to_float_safe(item.get('PreÃ§o Final ML'))
        if preco_final_ml_base is not None:
            preco_final_ml = preco_final_ml_base
        if preco_final_ml_display is None:
            preco_final_ml_display = _to_float_safe(item.get('PreÃ§o Final ML'))

        frete_base = item.get('Frete', '')
        frete_ml = item.get('Frete ML', frete_base)
        frete_gratis = item.get('Frete Gratis', item.get('Frete GrÃ¡tis', ''))
        frete_gratis_ml = item.get('Frete Gratis ML', item.get('Frete GrÃ¡tis ML', ''))
        if not frete_gratis:
            frete_gratis = 'SIM' if (preco_final is not None and preco_final >= 79.0) else 'NÃƒO'
        if not frete_gratis_ml:
            frete_gratis_ml = 'SIM' if (preco_final_ml is not None and preco_final_ml >= 79.0) else 'NÃƒO'

        frete_gratis_bool = _flag_frete_gratis(frete_gratis)
        frete_gratis_ml_bool = _flag_frete_gratis(frete_gratis_ml)

        frete_val = _to_float_safe(frete_base)
        frete_ml_val = _to_float_safe(frete_ml)
        frete_ml_exato = str(item.get("frete_ml_exato") or "").strip().lower() in {"1", "true", "sim", "yes"}
        tipo_txt = str(item.get("Tipo") or "").strip().lower()
        listing_type_hint = "free" if "grat" in tipo_txt else ""
        taxa_fixa_item = _to_float_safe(item.get("Taxa Fixa"))
        taxa_fixa_ml_item = _to_float_safe(item.get("Taxa Fixa ML"))
        taxa_fixa_a = taxa_fixa_item
        taxa_fixa_b = taxa_fixa_ml_item
        if taxa_fixa_a is None and preco_final is not None:
            taxa_fixa_a = _ml_estimar_taxa_fixa_por_preco(preco_final, listing_type_id=listing_type_hint)
        if taxa_fixa_b is None and preco_final_ml is not None:
            taxa_fixa_b = _ml_estimar_taxa_fixa_por_preco(preco_final_ml, listing_type_id=listing_type_hint)

        # Para frete grÃ¡tis, prioriza o recÃƒÂ¡lculo por faixa oficial do ML
        # com base no preÃ§o final da planilha. Se nÃ£o der para reconhecer a
        # faixa do frete-base, mantÃƒÂ©m o ajuste proporcional como fallback.
        if not frete_ml_exato and frete_gratis_ml_bool and frete_val is not None and preco_final is not None and preco_final_ml is not None:
            frete_ml_ajustado = _recalcular_frete_por_faixa_ml(
                frete_val,
                preco_final,
                preco_final_ml,
                listing_type_id=listing_type_hint,
            )
            if frete_ml_ajustado is None:
                frete_ml_ajustado = _ajustar_frete_por_preco_base(frete_val, preco_final, preco_final_ml)
            if frete_ml_ajustado is not None:
                frete_ml_val = frete_ml_ajustado
                frete_ml = formatar_moeda_br(frete_ml_val)

        # Regra solicitada: sem frete grÃ¡tis, usar valor fixo na coluna de frete.
        # Frete fica com o valor da API/arquivo de frete.
        # Taxa fixa do ML pertence ao detalhamento da tarifa, nao substitui frete.

        tarifa = _to_float_safe(item.get('Tarifa'))
        tarifa_ml = _to_float_safe(item.get('Tarifa ML'))
        if tarifa is None:
            tarifa = (preco_final * taxa_pct) if (preco_final is not None and taxa_pct is not None) else None
        if tarifa_ml is None:
            tarifa_ml = (preco_final_ml * taxa_pct) if (preco_final_ml is not None and taxa_pct is not None) else None
        desconto_ml_val = _calcular_desconto_ml_valor(
            desconto_atual=desconto_ml_val,
            ml_pct=item.get('ML % Campanha'),
            preco_base=item.get('PreÃƒÂ§o Base') or item.get('PreÃƒÂ§o Original') or item.get('ORIGINAL_PRICE'),
            preco_final_ml=preco_final_ml,
            tarifa_base=tarifa,
            tarifa_ml=tarifa_ml,
            desconto_atual_confiavel=desconto_ml_confiavel,
        )
        imposto_valor = (preco_final * imposto_pct_rate) if (preco_final is not None and imposto_pct_rate is not None) else None
        imposto_ml_valor = (preco_final_ml * imposto_pct_rate) if (preco_final_ml is not None and imposto_pct_rate is not None) else None
        if imposto_valor is None:
            imposto_valor = _to_float_safe(item.get('Imposto'))
        if imposto_ml_valor is None:
            imposto_ml_valor = _to_float_safe(item.get('Imposto ML'))

        valor_liquido = None
        valor_liquido_ml = None
        if preco_final is not None and custo is not None:
            valor_liquido = preco_final - custo - (frete_val or 0.0) - (imposto_valor or 0.0) - (tarifa or 0.0)
        if preco_final_ml is not None and custo is not None and desconto_ml_val is not None:
            valor_liquido_ml = preco_final_ml - custo - (frete_ml_val or 0.0) - (imposto_ml_valor or 0.0) - (tarifa_ml or 0.0)
            valor_liquido_ml += desconto_ml_val
        elif desconto_ml_val is None:
            # Nao reutiliza liquido/margem calculados anteriormente assumindo
            # implicitamente que um desconto desconhecido seria zero.
            for chave in tuple(item):
                if "quido ML" in str(chave):
                    item[chave] = ""
            item["Margem ML"] = ""

        margem_pct = None
        margem_ml_pct = None
        if valor_liquido is not None and preco_final not in (None, 0):
            margem_pct = (valor_liquido * 100.0) / preco_final
        if valor_liquido_ml is not None and preco_final_ml not in (None, 0):
            margem_ml_pct = (valor_liquido_ml * 100.0) / preco_final_ml

        status_raw = item.get('SituaÃƒÂ§ÃƒÂ£o', item.get('Status', ''))
        if limpar_status_exibicao and status_raw == 'Sem promocao fixa':
            status_raw = ''

        linhas.append({
            'Tipo': item.get('Tipo', ''),
            '%': item.get('%', ''),
            'SKU': _normalizar_sku_saida(item.get('SKU', '')),
            'TÃ­tulo': item.get('TÃ­tulo', ''),
            'Frete': frete_base,
            'Frete ML': frete_ml,
            'Frete GrÃ¡tis': frete_gratis,
            'Frete GrÃ¡tis ML': frete_gratis_ml,
            'Custo': item.get('Custo', ''),
            'Tarifa': formatar_moeda_br(tarifa) if tarifa is not None else item.get('Tarifa', ''),
            'Tarifa ML': formatar_moeda_br(tarifa_ml) if tarifa_ml is not None else item.get('Tarifa ML', ''),
            'MLB': item.get('MLB', ''),
            'Campanha ML': item.get('Campanha ML', ''),
            '% Fixa': item.get('% Fixa', item.get('Desconto', '')),
            'ML % Campanha': item.get('ML % Campanha', ''),
            'PreÃ§o Final': formatar_moeda_br(preco_final) if preco_final is not None else item.get('PreÃ§o Final', item.get('M 21 Fixa', '')),
            'Imposto %': _format_pct_br(imposto_pct_rate * 100.0) if imposto_pct_rate is not None else item.get('Imposto %', item.get('Imposto', '')),
            'Imposto': formatar_moeda_br(imposto_valor) if imposto_valor is not None else item.get('Imposto', ''),
            'Imposto Fixa': item.get('Imposto Fixa', formatar_moeda_br(imposto_valor) if imposto_valor is not None else item.get('Imposto', '')),
            'PreÃ§o Final ML': formatar_moeda_br(preco_final_ml) if preco_final_ml is not None else item.get('PreÃ§o Final ML', item.get('M ML', '')),
            'Imposto ML': formatar_moeda_br(imposto_ml_valor) if imposto_ml_valor is not None else item.get('Imposto ML', ''),
            'Desconto ML': (
                formatar_moeda_br(desconto_ml_val)
                if desconto_ml_val is not None
                else PROMO_DESCONTO_ML_NAO_INFORMADO
            ),
            'Valor LÃ­quido': formatar_moeda_br(valor_liquido) if valor_liquido is not None else item.get('Valor LÃ­quido', ''),
            'Valor lÃ­quido ML': formatar_moeda_br(valor_liquido_ml) if valor_liquido_ml is not None else item.get('Valor lÃ­quido ML', ''),
            'Status': status_raw,
            'Margem': _format_pct_br(margem_pct) if margem_pct is not None else item.get('Margem', item.get('Margem %', '')),
            'Margem ML': _format_pct_br(margem_ml_pct) if margem_ml_pct is not None else item.get('Margem ML', item.get('Margem %', '')),
            'AÃ§Ã£o': item.get('Participar ou nÃ£o', item.get('AÃ§Ã£o', '')),
        })

    df = pd.DataFrame(linhas)
    for c in COLUNAS_PLANILHA_ANALISE_PROMO:
        if c not in df.columns:
            df[c] = ''
    return df[COLUNAS_PLANILHA_ANALISE_PROMO]


def _salvar_planilha_analise_promo(client_id: str, dados_analise: list[dict], prefixo: str = 'analise_promo') -> str:
    tenant_path = get_tenant_path(client_id)
    pasta_tmp = os.path.join(tenant_path, 'tmp', 'promo')
    os.makedirs(pasta_tmp, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    nome = f"{prefixo}_{ts}.xlsx"
    caminho = os.path.join(pasta_tmp, nome)
    df = _build_df_planilha_analise_promo(dados_analise, limpar_status_exibicao=True)
    with pd.ExcelWriter(caminho, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Analise Promocao')
    return nome


def _promo_linha_status_ativo_ou_programado(row: dict) -> bool:
    status = str((row or {}).get("Status") or "").strip().lower()
    status_norm = normalizar_texto(status)
    return status_norm in {"ativo", "programado", "programada", "active", "scheduled", "programmed", "elegivel", "eligible"}


def _promo_linha_pct_fixa_maior_que_zero(row: dict) -> bool:
    valor = (row or {}).get("% Fixa", (row or {}).get("Desconto", ""))
    percentual = _parse_float_flex(valor)
    if percentual is None:
        return True
    return abs(float(percentual)) > 0.000001


def _promo_meta_contagem(meta: dict, *keys: str) -> Optional[int]:
    if not isinstance(meta, dict):
        return None
    for key in keys:
        valor = meta.get(key)
        parsed = _parse_float_flex(valor)
        if parsed is not None:
            return max(0, int(parsed))
    return None


def _promo_total_esperado_por_contagens(active_count: Optional[int], eligible_count: Optional[int]) -> Optional[int]:
    valores = [v for v in (active_count, eligible_count) if v is not None]
    if not valores:
        return None
    if active_count is not None and eligible_count is not None and active_count != eligible_count:
        return max(0, int(active_count)) + max(0, int(eligible_count))
    return max(0, int(max(valores)))


def _ml_classificar_status_promocao_valor(valor) -> str:
    status = normalizar_texto(valor)
    if not status:
        return ""
    if status in {"scheduled", "programmed", "programado", "programada", "agendado", "agendada", "pending"}:
        return "Programado"
    if "schedul" in status or "program" in status or "agend" in status:
        return "Programado"
    if status in {"active", "ativo", "participating", "participando", "started", "running"}:
        return "Ativo"
    if "start" in status or "particip" in status:
        return "Ativo"
    return ""


def _ml_classificar_status_promocao_entry(entry: dict) -> str:
    if not isinstance(entry, dict):
        return ""
    status_item_real = _promo_status_item_promocao(entry)
    if status_item_real in {"candidate", "eligible"}:
        return ""
    classificacao_real = _ml_classificar_status_promocao_valor(status_item_real)
    if classificacao_real:
        return classificacao_real
    candidatos = [
        entry.get("status"),
        entry.get("promotion_status"),
        entry.get("status_item"),
        entry.get("item_status"),
        entry.get("state"),
        entry.get("_jk_status_item_consultado"),
    ]
    for chave in ("promotion", "campaign", "deal", "offer"):
        obj = entry.get(chave)
        if isinstance(obj, dict):
            candidatos.extend([
                obj.get("status"),
                obj.get("promotion_status"),
                obj.get("status_item"),
                obj.get("state"),
            ])

    encontrou_ativo = False
    for candidato in candidatos:
        classificacao = _ml_classificar_status_promocao_valor(candidato)
        if classificacao == "Programado":
            return "Programado"
        if classificacao == "Ativo":
            encontrou_ativo = True
    return "Ativo" if encontrou_ativo else ""


def _ml_classificar_status_promocao_por_id(promocoes_item, campaign_id: str) -> str:
    campaign_norm = str(campaign_id or "").strip().lower()
    if not campaign_norm:
        return ""

    chaves_id = {
        "id",
        "promotion_id",
        "promotionid",
        "campaign_id",
        "campaignid",
        "deal_id",
        "dealid",
        "offer_id",
        "offerid",
    }
    classificacoes = []

    def _ids_do_objeto(obj: dict) -> set[str]:
        ids = set()
        for chave, valor in obj.items():
            chave_norm = str(chave or "").strip().lower()
            if chave_norm in chaves_id and valor is not None:
                ids.add(str(valor).strip().lower())
        return ids

    vistos = set()
    nodes = 0

    def visitar(obj, depth: int = 0):
        nonlocal nodes
        nodes += 1
        if nodes > 4000 or depth > 8:
            return
        if isinstance(obj, (dict, list)):
            obj_id = id(obj)
            if obj_id in vistos:
                return
            vistos.add(obj_id)
        if isinstance(obj, list):
            for item in obj[:250]:
                visitar(item, depth + 1)
            return
        if not isinstance(obj, dict):
            return

        if campaign_norm in _ids_do_objeto(obj):
            classificacoes.append(_ml_classificar_status_promocao_entry(obj))

        for chave, valor in obj.items():
            if str(chave or "").strip().lower() == "item":
                continue
            if isinstance(valor, (dict, list)):
                visitar(valor, depth + 1)

    visitar(promocoes_item)
    if "Programado" in classificacoes:
        return "Programado"
    if "Ativo" in classificacoes:
        return "Ativo"
    return ""


def _ml_status_promocao_usuario_exibicao(status: str) -> str:
    status_norm = str(status or "").strip()
    if status_norm == "Ativo":
        return "Ativo"
    if status_norm == "Programado":
        return "Programada"
    return "Elegível"


def _ml_iterar_campos_payload_limitado(obj, *, max_depth: int = 8, max_nodes: int = 4000):
    """Percorre payloads grandes do ML sem risco de recursao infinita/profundidade excessiva."""
    stack = [("", obj, 0)]
    vistos = set()
    nodes = 0
    while stack:
        caminho, valor, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            break

        if isinstance(valor, dict):
            obj_id = id(valor)
            if obj_id in vistos:
                continue
            vistos.add(obj_id)
            if depth >= max_depth:
                continue
            for chave, conteudo in valor.items():
                chave_norm = str(chave or "").strip().lower()
                novo_caminho = f"{caminho}.{chave_norm}" if caminho else chave_norm
                if isinstance(conteudo, (dict, list)):
                    stack.append((novo_caminho, conteudo, depth + 1))
                else:
                    yield novo_caminho, conteudo
            continue

        if isinstance(valor, list):
            obj_id = id(valor)
            if obj_id in vistos:
                continue
            vistos.add(obj_id)
            if depth >= max_depth:
                continue
            for item in valor[:250]:
                if isinstance(item, (dict, list)):
                    stack.append((caminho, item, depth + 1))
                else:
                    yield caminho, item
            continue

        yield caminho, valor

PEER_EXPORTS = ['_normalizar_sku_saida', '_build_df_planilha_analise_promo', '_salvar_planilha_analise_promo', '_promo_linha_status_ativo_ou_programado', '_promo_linha_pct_fixa_maior_que_zero', '_promo_meta_contagem', '_promo_total_esperado_por_contagens', '_ml_classificar_status_promocao_valor', '_ml_classificar_status_promocao_entry', '_ml_classificar_status_promocao_por_id', '_ml_status_promocao_usuario_exibicao', '_ml_iterar_campos_payload_limitado']
__all__ = PEER_EXPORTS + ["configure_mercadolivre_legacy_planilhas_runtime"]

configure_mercadolivre_legacy_planilhas_runtime()
