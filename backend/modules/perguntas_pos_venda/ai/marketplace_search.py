"""Marketplace listing lookup adapters for public-question research."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import requests

from backend.services.marketplace_tools import integrations as marketplace_integrations

from .runtime import (
    _ml_api_request,
    _obter_cfg_ml,
    logger,
    requests_tls_verify,
    resolve_runtime_adapter,
)

def _ia_agent_perguntas_relaxar_query_web(query: str) -> str:
    texto = str(query or "")
    texto = re.sub(r"\bMLB[\s_-]*\d{5,}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\bSKU[-_/A-Z0-9]{2,}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\b\d{8,14}\b", " ", texto)
    texto = re.sub(r"\b[A-Z]{2,8}[-./][A-Z0-9]{3,}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"[\"']+", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:500]

def _ia_agent_perguntas_query_ml_publica(query: str) -> str:
    texto = str(query or "")
    texto = re.sub(r"\bMLB[\s_-]*\d{5,}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\bSKU[-_/A-Z0-9]{2,}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"[\"']+", " ", texto)
    texto = re.sub(
        r"\b(mercado livre|anuncio|anuncios|descri[cç][aã]o|produto similar|compatibilidade|especificacao|aplicacao)\b",
        " ",
        texto,
        flags=re.IGNORECASE,
    )
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:180]

def _ia_agent_perguntas_anuncios_publicos_ml(query: str, max_results: int = 4) -> list[dict]:
    consulta = _ia_agent_perguntas_query_ml_publica(query)
    if not consulta:
        return []
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
            "Origin": "https://www.mercadolivre.com.br",
            "Referer": "https://www.mercadolivre.com.br/",
        }
        resp = requests.get(
            "https://api.mercadolibre.com/sites/MLB/search",
            params={"q": consulta, "limit": max(1, min(int(max_results or 4), 6))},
            headers=headers,
            timeout=15,
            verify=requests_tls_verify(),
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        resultados = []
        for item in (payload.get("results") or [])[:max_results]:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            descricao = ""
            if item_id:
                try:
                    desc_resp = requests.get(
                        f"https://api.mercadolibre.com/items/{quote(item_id, safe='')}/description",
                        headers=headers,
                        timeout=10,
                        verify=requests_tls_verify(),
                    )
                    if desc_resp.status_code == 200:
                        desc_data = desc_resp.json() or {}
                        descricao = str(desc_data.get("plain_text") or desc_data.get("text") or "").strip()
                except Exception as exc:
                    logger.warning(
                        "[IA AGENT PERGUNTAS] Falha ao consultar descricao publica ML: %s",
                        type(exc).__name__,
                    )
            resultados.append({
                "id": item_id,
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("permalink") or "").strip(),
                "price": item.get("price"),
                "available_quantity": item.get("available_quantity"),
                "condition": item.get("condition"),
                "seller": ((item.get("seller") or {}).get("nickname") if isinstance(item.get("seller"), dict) else ""),
                "description": descricao[:900],
            })
        return resultados
    except Exception as exc:
        logger.warning("[IA AGENT PERGUNTAS] Falha na busca publica de anuncios ML: %s", type(exc).__name__)
        return []

def _ia_agent_perguntas_anuncios_ml_autenticado(client_id: str, loja: str, query: str, max_results: int = 4) -> list[dict]:
    consulta = _ia_agent_perguntas_query_ml_publica(query)
    if not consulta:
        return []
    lojas = marketplace_integrations.connected_stores(client_id, "mercadolivre", loja)
    if not lojas:
        return []
    for nome_loja in lojas[:3]:
        try:
            cfg = resolve_runtime_adapter("sources", "mercado_livre_config", _obter_cfg_ml)(client_id, nome_loja)
            resp, cfg = resolve_runtime_adapter("sources", "mercado_livre_request", _ml_api_request)(
                client_id,
                nome_loja,
                cfg,
                "GET",
                "https://api.mercadolibre.com/sites/MLB/search",
                params={"q": consulta, "limit": max(1, min(int(max_results or 4), 6))},
                timeout=18,
            )
            if resp.status_code != 200:
                continue
            payload = resp.json() or {}
            resultados = []
            for item in (payload.get("results") or [])[:max_results]:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "").strip()
                descricao = ""
                if item_id:
                    desc_resp, cfg = resolve_runtime_adapter("sources", "mercado_livre_request", _ml_api_request)(
                        client_id,
                        nome_loja,
                        cfg,
                        "GET",
                        f"https://api.mercadolibre.com/items/{quote(item_id, safe='')}/description",
                        timeout=10,
                    )
                    if desc_resp.status_code == 200:
                        desc_data = desc_resp.json() or {}
                        descricao = str(desc_data.get("plain_text") or desc_data.get("text") or "").strip()
                resultados.append({
                    "loja_consulta": nome_loja,
                    "id": item_id,
                    "title": str(item.get("title") or "").strip(),
                    "url": str(item.get("permalink") or "").strip(),
                    "price": item.get("price"),
                    "available_quantity": item.get("available_quantity"),
                    "condition": item.get("condition"),
                    "seller": ((item.get("seller") or {}).get("nickname") if isinstance(item.get("seller"), dict) else ""),
                    "description": descricao[:900],
                })
            if resultados:
                return resultados
        except Exception as exc:
            logger.warning(
                "[IA AGENT PERGUNTAS] Falha na busca autenticada de anuncios ML: %s",
                type(exc).__name__,
            )
            continue
    return []
