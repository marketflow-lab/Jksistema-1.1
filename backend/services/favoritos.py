"""Favoritos persistence helpers."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import HTTPException


FAVORITOS_ML_PERSIST_CACHE_VERSION = 1
FAVORITOS_ML_PERSIST_CACHE_LOCK = threading.RLock()

_logger = logging.getLogger("jk_sistema")
_get_tenant_path: Callable[[str], str] | None = None
_chave_loja_favoritos: Callable[[Any], str] = lambda valor: str(valor or "").strip().lower()


def configure_favoritos_context(
    *,
    get_tenant_path: Callable[[str], str],
    logger: logging.Logger | None = None,
    chave_loja_favoritos: Callable[[Any], str] | None = None,
) -> None:
    global _get_tenant_path, _logger, _chave_loja_favoritos
    _get_tenant_path = get_tenant_path
    if logger is not None:
        _logger = logger
    if chave_loja_favoritos is not None:
        _chave_loja_favoritos = chave_loja_favoritos


def _tenant_path(client_id: str) -> str:
    if not callable(_get_tenant_path):
        raise RuntimeError("Favoritos service context was not configured.")
    return _get_tenant_path(client_id)


def _favoritos_ml_persist_cache_slug(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-z0-9._-]+", "_", texto).strip("._-")
    return texto[:120] or "cache"


def _favoritos_ml_persist_cache_path(client_id: str, categoria: str, cache_id: str) -> str:
    categoria_slug = _favoritos_ml_persist_cache_slug(categoria)
    cache_slug = _favoritos_ml_persist_cache_slug(cache_id)
    return os.path.join(_tenant_path(client_id), "favoritos_ml_cache", categoria_slug, f"{cache_slug}.json")


def _favoritos_ml_persist_cache_clean_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    try:
        limpo = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        limpo = dict(payload)
    for chave in (
        "cache",
        "cache_hit",
        "cache_persistente",
        "cache_updated_at",
        "cache_age_seconds",
        "cache_refreshed",
        "stale_cache",
    ):
        limpo.pop(chave, None)
    return limpo


def _favoritos_ml_persist_cache_meta(envelope: dict) -> dict:
    updated_at = str((envelope or {}).get("updated_at") or "").strip()
    age_seconds = None
    if updated_at:
        try:
            age_seconds = max(0, int(time.time() - datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()))
        except Exception:
            age_seconds = None
    return {
        "updated_at": updated_at,
        "age_seconds": age_seconds,
        "version": int((envelope or {}).get("version") or 0),
        "cache_id": str((envelope or {}).get("cache_id") or "").strip(),
    }


def _favoritos_ml_persist_cache_read(client_id: str, categoria: str, cache_id: str) -> tuple[dict, dict] | None:
    caminho = _favoritos_ml_persist_cache_path(client_id, categoria, cache_id)
    if not os.path.exists(caminho):
        return None
    try:
        with FAVORITOS_ML_PERSIST_CACHE_LOCK:
            with open(caminho, "r", encoding="utf-8") as f:
                envelope = json.load(f)
        payload = envelope.get("payload") if isinstance(envelope, dict) else None
        if not isinstance(payload, dict):
            return None
        return payload, _favoritos_ml_persist_cache_meta(envelope)
    except Exception as exc:
        _logger.warning("[Favoritos ML Cache] Falha ao ler cache %s/%s: %s", categoria, cache_id, exc)
        return None


def _favoritos_ml_persist_cache_write(client_id: str, categoria: str, cache_id: str, payload: dict) -> dict:
    caminho = _favoritos_ml_persist_cache_path(client_id, categoria, cache_id)
    agora = datetime.now().isoformat(timespec="seconds")
    envelope = {
        "version": FAVORITOS_ML_PERSIST_CACHE_VERSION,
        "cache_id": str(cache_id or "").strip(),
        "updated_at": agora,
        "payload": _favoritos_ml_persist_cache_clean_payload(payload),
    }
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp = f"{caminho}.tmp"
    with FAVORITOS_ML_PERSIST_CACHE_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(envelope, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, caminho)
    return _favoritos_ml_persist_cache_meta(envelope)


def _favoritos_ml_persist_cache_apply(payload: dict, meta: dict, *, hit: bool, refreshed: bool = False, warning: str = "") -> dict:
    saida = _favoritos_ml_persist_cache_clean_payload(payload)
    cache_meta = {
        "hit": bool(hit),
        "persistente": True,
        "updated_at": (meta or {}).get("updated_at") or "",
        "age_seconds": (meta or {}).get("age_seconds"),
        "refreshed": bool(refreshed),
    }
    saida["cache"] = cache_meta
    saida["cache_hit"] = bool(hit)
    saida["cache_persistente"] = True
    saida["cache_updated_at"] = cache_meta["updated_at"]
    saida["cache_age_seconds"] = cache_meta["age_seconds"]
    saida["cache_refreshed"] = bool(refreshed)
    if warning:
        warning_atual = str(saida.get("warning") or "").strip()
        saida["warning"] = f"{warning_atual} | {warning}" if warning_atual else warning
        saida["stale_cache"] = True
    return saida


def _favoritos_usuario_slug(username: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z._-]+", "_", str(username or "").strip().lower())
    return slug or "anon"


def _favoritos_normalizar_skus_ocultos(lista: Any) -> list[str]:
    if not isinstance(lista, list):
        return []
    vistos: set[str] = set()
    saida: list[str] = []
    for item in lista:
        sku = str(item or "").strip().upper()
        if not sku or sku in vistos:
            continue
        vistos.add(sku)
        saida.append(sku)
        if len(saida) >= 10000:
            break
    return saida


def _favoritos_arquivo_skus_ocultos(client_id: str, username: str) -> str:
    return os.path.join(_tenant_path(client_id), f"favoritos_skus_ocultos_{_favoritos_usuario_slug(username)}.json")


def _favoritos_arquivo_planilhas_lojas(client_id: str) -> str:
    return os.path.join(_tenant_path(client_id), "favoritos_planilhas_lojas.json")


def _favoritos_normalizar_url_planilha_google(valor: Any) -> str:
    url = re.sub(r"\s+", "", str(valor or "").strip())
    if not url:
        return ""
    if len(url) > 1200:
        raise HTTPException(status_code=400, detail="Link da planilha muito longo.")
    parsed = urlparse(url)
    host = str(parsed.netloc or "").lower()
    path = str(parsed.path or "").lower()
    if parsed.scheme not in {"http", "https"} or host != "docs.google.com" or not path.startswith("/spreadsheets/"):
        raise HTTPException(status_code=400, detail="Informe um link valido do Google Sheets.")
    return url


def _favoritos_normalizar_planilha_loja_item(loja: Any, url: Any, updated_at: Any = None) -> dict | None:
    nome = re.sub(r"\s+", " ", str(loja or "").strip())
    if not nome:
        return None
    chave = _chave_loja_favoritos(nome)
    if not chave:
        return None
    return {
        "loja": nome[:160],
        "url": _favoritos_normalizar_url_planilha_google(url),
        "updated_at": str(updated_at or "").strip() or None,
    }


def _favoritos_carregar_planilhas_lojas(client_id: str) -> dict:
    caminho = _favoritos_arquivo_planilhas_lojas(client_id)
    if not os.path.exists(caminho):
        return {"planilhas": {}, "updated_at": None}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        bruto = dados.get("planilhas") if isinstance(dados, dict) else {}
        if not isinstance(bruto, dict):
            bruto = {}
        planilhas: dict[str, dict] = {}
        for chave, item in bruto.items():
            if isinstance(item, dict):
                nome = item.get("loja") or chave
                url = item.get("url") or ""
                updated_at = item.get("updated_at")
            else:
                nome = chave
                url = item
                updated_at = None
            normalizado = _favoritos_normalizar_planilha_loja_item(nome, url, updated_at)
            if not normalizado:
                continue
            planilhas[_chave_loja_favoritos(normalizado.get("loja"))] = normalizado
        return {
            "planilhas": planilhas,
            "updated_at": dados.get("updated_at") if isinstance(dados, dict) else None,
        }
    except HTTPException:
        raise
    except Exception as exc:
        _logger.warning("[Favoritos Planilhas] Falha ao carregar links por loja: %s", exc)
        return {"planilhas": {}, "updated_at": None}


def _favoritos_salvar_planilhas_lojas(client_id: str, itens: list[Any]) -> dict:
    payload = _favoritos_carregar_planilhas_lojas(client_id)
    planilhas = dict(payload.get("planilhas") or {})
    agora = datetime.now().isoformat(timespec="seconds")
    atualizadas = []
    for item in itens or []:
        normalizado = _favoritos_normalizar_planilha_loja_item(item.loja, item.url, agora)
        if not normalizado:
            continue
        normalizado["updated_at"] = agora
        chave = _chave_loja_favoritos(normalizado.get("loja"))
        planilhas[chave] = normalizado
        atualizadas.append(normalizado)
    caminho = _favoritos_arquivo_planilhas_lojas(client_id)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    salvo = {"planilhas": planilhas, "updated_at": agora}
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(salvo, f, ensure_ascii=False, indent=2)
    return {**salvo, "atualizadas": atualizadas}
