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


def configure_mercadolivre_legacy_api_runtime(runtime_module=None, peers=None):
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


configure_mercadolivre_legacy_api_runtime()


def _ml_refresh_token(client_id: str, nome_loja: str, cfg: dict):
    app_id = cfg.get("app_id") or cfg.get("client_id")
    client_secret = cfg.get("client_secret")
    refresh_token = cfg.get("refresh_token")

    if not app_id or not client_secret or not refresh_token:
        raise HTTPException(status_code=401, detail="ConfiguraÃƒÂ§ÃƒÂ£o OAuth do Mercado Livre incompleta. RefaÃƒÂ§a a autenticaÃƒÂ§ÃƒÂ£o.")

    url = "https://api.mercadolibre.com/oauth/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    payload = {
        "grant_type": "refresh_token",
        "client_id": app_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }

    verify_ssl = _env_bool("ML_VERIFY_SSL", True)
    session_token = f"oauth:{refresh_token}"
    try:
        resp = _ml_http_request(
            client_id,
            nome_loja,
            session_token,
            "POST",
            url,
            headers=headers,
            data=payload,
            timeout=15,
            verify_ssl=verify_ssl,
        )
        if resp.status_code == 200:
            result = resp.json()
            novo_refresh_token = result.get("refresh_token", refresh_token)
            cfg["access_token"] = result.get("access_token", cfg.get("access_token"))
            cfg["refresh_token"] = novo_refresh_token
            cfg["updated_at"] = str(time.time())
            atualizar_api_loja(client_id, nome_loja, 'mercadolivre', cfg)
            if str(novo_refresh_token or "") != str(refresh_token or ""):
                _ml_http_invalidar_session(client_id, nome_loja, session_token, verify_ssl)
            logger.info(f"[ML REFRESH] Ã¢Å“â€¦ Token renovado com sucesso para {nome_loja}")
            return cfg

        logger.error(f"[ML REFRESH] Ã¢ÂÅ’ Erro ao renovar token: {resp.status_code} - {resp.text}")
        raise HTTPException(status_code=401, detail="NÃƒÂ£o foi possÃƒÂ­vel renovar o token. RefaÃƒÂ§a a autenticaÃƒÂ§ÃƒÂ£o OAuth.")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[ML REFRESH] Ã¢ÂÅ’ Exception ao renovar token: {e}")
        raise HTTPException(status_code=401, detail="Erro ao renovar token. RefaÃƒÂ§a a autenticaÃƒÂ§ÃƒÂ£o OAuth.")


def _headers_ml(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }


def _ml_oauth_status(cfg: dict | None) -> dict:
    cfg = cfg if isinstance(cfg, dict) else {}
    app_id = str(cfg.get("app_id") or cfg.get("id") or cfg.get("client_id") or "").strip()
    client_secret = str(cfg.get("client_secret") or cfg.get("secret") or "").strip()
    access_token = str(cfg.get("access_token") or "").strip()
    refresh_token = str(cfg.get("refresh_token") or "").strip()
    faltando = []
    if not access_token:
        faltando.append("access_token")
    if not refresh_token:
        faltando.append("refresh_token")
    if not app_id:
        faltando.append("app_id")
    if not client_secret:
        faltando.append("client_secret")

    if not cfg:
        return {
            "conectado": False,
            "status": "pendente",
            "motivo": "Mercado Livre ainda nÃ£o foi autenticado.",
            "faltando": faltando,
        }
    if not faltando:
        return {
            "conectado": True,
            "status": "conectado",
            "motivo": "",
            "faltando": [],
        }
    return {
        "conectado": False,
        "status": str(cfg.get("status") or "reautenticar"),
        "motivo": str(cfg.get("motivo") or "OAuth do Mercado Livre incompleto. RefaÃ§a a autenticaÃ§Ã£o."),
        "faltando": faltando,
    }


def _ml_oauth_config_completa(cfg: dict | None) -> bool:
    cfg = cfg if isinstance(cfg, dict) else {}
    return bool(
        str(cfg.get("access_token") or "").strip()
        and str(cfg.get("refresh_token") or "").strip()
        and str(cfg.get("app_id") or cfg.get("id") or cfg.get("client_id") or "").strip()
        and str(cfg.get("client_secret") or cfg.get("secret") or "").strip()
    )


def _ml_normalizar_oauth_compartilhado(client_id: str, nome_loja: str, cfg: dict) -> dict:
    cfg = dict(cfg or {})
    if not _ml_oauth_config_completa(cfg):
        return cfg

    mudou = False
    if cfg.get("shared_without_oauth_tokens"):
        cfg["shared_without_oauth_tokens"] = False
        mudou = True
    if cfg.get("connected") is not True:
        cfg["connected"] = True
        mudou = True
    if str(cfg.get("status") or "").strip().lower() != "conectado":
        cfg["status"] = "conectado"
        mudou = True
    if str(cfg.get("motivo") or "").strip():
        cfg["motivo"] = ""
        mudou = True
    if mudou:
        cfg["updated_at"] = str(time.time())
        atualizar_api_loja(client_id, nome_loja, "mercadolivre", cfg)
    return cfg


def _ml_descobrir_user_id_oauth(client_id: str, nome_loja: str, cfg: dict) -> dict:
    cfg = dict(cfg or {})
    if str(cfg.get("user_id") or "").strip() or not str(cfg.get("access_token") or "").strip():
        return cfg

    try:
        resp, cfg = _ml_api_request(
            client_id,
            nome_loja,
            cfg,
            "GET",
            "https://api.mercadolibre.com/users/me",
            timeout=15,
        )
    except Exception as exc:
        logger.warning("[ML OAUTH] Falha ao descobrir user_id da loja %s: %s", nome_loja, exc)
        return cfg

    if resp.status_code != 200:
        logger.warning("[ML OAUTH] users/me retornou HTTP %s para loja %s", resp.status_code, nome_loja)
        return cfg

    try:
        data = resp.json() or {}
    except Exception:
        data = {}
    user_id = str(data.get("id") or data.get("user_id") or "").strip()
    if not user_id:
        logger.warning("[ML OAUTH] users/me sem id para loja %s", nome_loja)
        return cfg

    cfg["user_id"] = user_id
    if data.get("site_id"):
        cfg["site_id"] = data.get("site_id")
    if data.get("nickname"):
        cfg["nickname"] = data.get("nickname")
    cfg["connected"] = True
    cfg["status"] = "conectado"
    cfg["motivo"] = ""
    cfg["shared_without_oauth_tokens"] = False
    cfg["updated_at"] = str(time.time())
    atualizar_api_loja(client_id, nome_loja, "mercadolivre", cfg)
    _cache_invalidar_loja(client_id, nome_loja)
    logger.info("[ML OAUTH] user_id %s recuperado automaticamente para loja %s", user_id, nome_loja)
    return cfg


def _obter_cfg_ml(client_id: str, nome_loja: str) -> dict:
    """ObtÃƒÂ©m e normaliza a configuraÃƒÂ§ÃƒÂ£o do Mercado Livre da loja."""
    loja = buscar_loja(client_id, nome_loja)
    if not loja:
        raise HTTPException(status_code=404, detail="Loja nÃ£o encontrada")

    integracoes = loja.get("integracoes") or {}
    cfg = dict(integracoes.get("mercadolivre") or {})
    if not cfg:
        raise HTTPException(status_code=400, detail="IntegraÃƒÂ§ÃƒÂ£o do Mercado Livre nÃ£o configurada para esta loja")

    cfg["app_id"] = cfg.get("app_id") or cfg.get("id") or cfg.get("client_id")
    cfg["client_secret"] = cfg.get("client_secret") or cfg.get("secret")

    if not cfg.get("access_token"):
        raise HTTPException(status_code=401, detail="Token do Mercado Livre ausente. RefaÃƒÂ§a a autenticaÃƒÂ§ÃƒÂ£o OAuth.")

    cfg = _ml_normalizar_oauth_compartilhado(client_id, nome_loja, cfg)
    cfg = _ml_descobrir_user_id_oauth(client_id, nome_loja, cfg)
    return cfg


def _ml_api_request(
    client_id: str,
    loja: str,
    cfg: dict,
    method: str,
    url: str,
    *,
    params=None,
    json=None,
    data=None,
    timeout: int = 15,
    verify_ssl: bool | None = None,
):
    """Executa chamada autenticada na API do Mercado Livre com refresh automÃƒÂ¡tico do token."""
    verify = _env_bool("ML_VERIFY_SSL", True) if verify_ssl is None else bool(verify_ssl)
    access_token = str((cfg or {}).get("access_token") or "").strip()
    resp = _ml_http_request(
        client_id,
        loja,
        access_token,
        method,
        url,
        headers=_headers_ml(cfg["access_token"]),
        params=params,
        json=json,
        data=data,
        timeout=timeout,
        verify_ssl=verify,
    )
    if resp.status_code == 401:
        _ml_http_invalidar_session(client_id, loja, access_token, verify)
        cfg = _ml_refresh_token(client_id, loja, cfg)
        access_token = str((cfg or {}).get("access_token") or "").strip()
        resp = _ml_http_request(
            client_id,
            loja,
            access_token,
            method,
            url,
            headers=_headers_ml(cfg["access_token"]),
            params=params,
            json=json,
            data=data,
            timeout=timeout,
            verify_ssl=verify,
        )
    return resp, cfg


def _ml_api_request_com_retry(
    client_id: str,
    loja: str,
    cfg: dict,
    method: str,
    url: str,
    *,
    params=None,
    json=None,
    data=None,
    timeout: int = 35,
    max_attempts: int = 4,
    progress_callback: Optional[Callable[[str], None]] = None,
):
    """Chamada ML com retry para etapas pesadas que sofrem timeout/429/5xx."""
    ultimo_resp = None
    ultimo_erro = None
    attempts = max(1, int(max_attempts or 1))
    for tentativa in range(1, attempts + 1):
        try:
            resp, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                method,
                url,
                params=params,
                json=json,
                data=data,
                timeout=timeout,
            )
            ultimo_resp = resp
            if resp.status_code not in (429, 500, 502, 503, 504):
                return resp, cfg
            ultimo_erro = _ml_parse_error_detail(resp, f"HTTP {resp.status_code}")
            deve_tentar = tentativa < attempts
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout, requests.exceptions.Timeout) as exc:
            ultimo_erro = exc
            deve_tentar = tentativa < attempts
        except requests.exceptions.ConnectionError as exc:
            ultimo_erro = exc
            deve_tentar = tentativa < attempts

        if not deve_tentar:
            break

        espera = min(18.0, (1.8 ** tentativa) + random.uniform(0.2, 1.0))
        mensagem = (
            "Mercado Livre demorou para responder, tentando novamente... "
            f"tentativa {tentativa + 1}/{attempts}"
        )
        if progress_callback:
            try:
                progress_callback(mensagem)
            except Exception:
                pass
        logger.warning("[ML API] %s url=%s erro=%s", mensagem, url, ultimo_erro)
        time.sleep(espera)

    if ultimo_resp is not None:
        return ultimo_resp, cfg
    raise ultimo_erro or requests.exceptions.Timeout("Mercado Livre nao respondeu.")


def _ml_parse_error_detail(resp, fallback: str = "Erro na API do Mercado Livre") -> str:
    try:
        data = resp.json()
        if isinstance(data, dict):
            detail = data.get("message") or data.get("error") or data.get("detail") or fallback
            causas = [c.get("message") for c in (data.get("cause") or []) if isinstance(c, dict) and c.get("message")]
        else:
            detail = data or fallback
            causas = []
        if causas:
            detail = f"{detail} | {' | '.join(causas[:3])}"
    except Exception:
        detail = getattr(resp, "text", None) or fallback
    if not isinstance(detail, str):
        try:
            detail = json.dumps(detail, ensure_ascii=False)
        except Exception:
            detail = str(detail)

    if "price" in detail.lower() and ("automat" in detail.lower() or "ignore" in detail.lower() or resp.status_code == 400):
        detail = (
            "Mercado Livre recusou ou ignorou a atualizacao de preco. "
            "Verifique automacao de precos, campanhas e se o anuncio e de catalogo. "
            f"Detalhe: {detail}"
        )
    return detail


def _ml_response_eh_rate_limit(resp, detalhe: str = "") -> bool:
    texto = f"{detalhe or ''} {getattr(resp, 'text', '') or ''}".lower()
    return (
        getattr(resp, "status_code", None) == 429
        or "local_rate_limited" in texto
        or "rate_limited" in texto
        or "too many requests" in texto
    )


def _ml_favoritos_api_request(client_id: str, loja: str, cfg: dict, method: str, url: str, *, params=None, json=None, data=None, timeout: int = 15):
    """Chamada ML usada apenas pelo mÃ³dulo Favoritos.

    Alguns ambientes Windows/Electron deste app nÃ£o possuem a cadeia de certificados
    atualizada. Em Favoritos, a falha era tratada como "sem anÃºncio"; aqui ela fica
    limitada a este mÃ³dulo e evita falso negativo na busca de descriÃ§Ãµes.
    """
    cfg_local = dict(cfg or {})
    return _ml_api_request(
        client_id,
        loja,
        cfg_local,
        method,
        url,
        params=params,
        json=json,
        data=data,
        timeout=timeout,
        verify_ssl=False,
    )

PEER_EXPORTS = ['_ml_refresh_token', '_headers_ml', '_ml_oauth_status', '_ml_oauth_config_completa', '_ml_normalizar_oauth_compartilhado', '_ml_descobrir_user_id_oauth', '_obter_cfg_ml', '_ml_api_request', '_ml_api_request_com_retry', '_ml_parse_error_detail', '_ml_response_eh_rate_limit', '_ml_favoritos_api_request']
__all__ = PEER_EXPORTS + ["configure_mercadolivre_legacy_api_runtime"]

configure_mercadolivre_legacy_api_runtime()
