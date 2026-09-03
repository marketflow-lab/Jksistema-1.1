"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException

from . import runtime as _runtime

logger = logging.getLogger(__name__)

def bling_connected_stores(client_id: str) -> list[str]:
    lojas = []
    for loja in _runtime.load_stores(client_id) or []:
        if not isinstance(loja, dict):
            continue
        nome = str(loja.get("nome") or "").strip()
        integracoes = loja.get("integracoes") or {}
        cfg = integracoes.get("bling") if isinstance(integracoes, dict) else {}
        if nome and isinstance(cfg, dict) and str(cfg.get("access_token") or "").strip():
            lojas.append(nome)
    return lojas

def connected_stores(client_id: str, provedor: str, loja: Optional[str] = None) -> list[str]:
    provedor_norm = str(provedor or "").strip().lower()
    conectadas = _runtime.ml_connected_stores(client_id) if provedor_norm in {"ml", "mercadolivre", "mercado_livre"} else bling_connected_stores(client_id)
    loja_txt = str(loja or "").strip()
    if not loja_txt or loja_txt in {"__todas", "Todas as lojas"}:
        return conectadas[:5]

    alvo_norm = _runtime.normalize_text(loja_txt)
    for nome in conectadas:
        if _runtime.normalize_text(nome) == alvo_norm:
            return [nome]
    for nome in conectadas:
        nome_norm = _runtime.normalize_text(nome)
        if alvo_norm and (alvo_norm in nome_norm or nome_norm in alvo_norm):
            return [nome]
    return conectadas[:5]

def get_bling_config(client_id: str, nome_loja: str) -> dict:
    loja = _runtime.find_store(client_id, nome_loja)
    if not loja:
        raise HTTPException(status_code=404, detail="Loja nao encontrada")

    integracoes = loja.get("integracoes") or {}
    cfg = dict(integracoes.get("bling") or {})
    if not cfg:
        raise HTTPException(status_code=400, detail="Integracao Bling nao configurada para esta loja")

    cfg["id"] = cfg.get("id") or cfg.get("client_id")
    cfg["secret"] = cfg.get("secret") or cfg.get("client_secret")
    if not cfg.get("access_token"):
        raise HTTPException(status_code=401, detail="Token Bling ausente. Refaca a autenticacao OAuth.")
    store_id = str(loja.get("store_id") or "").strip()
    if not store_id:
        raise HTTPException(status_code=409, detail="Loja sem store_id persistido.")
    cfg["_store_id_context"] = store_id
    return cfg

def get_status(client_id: str, loja: Optional[str] = None) -> Optional[dict]:
    try:
        loja_filtro = str(loja or "").strip()
        registros = []
        for loja_cfg in _runtime.load_stores(client_id) or []:
            if not isinstance(loja_cfg, dict):
                continue
            nome = str(loja_cfg.get("nome") or "").strip()
            if not nome:
                continue
            if loja_filtro and loja_filtro not in {"__todas", "Todas as lojas"}:
                if _runtime.normalize_text(loja_filtro) not in _runtime.normalize_text(nome):
                    continue
            integracoes = loja_cfg.get("integracoes") or {}
            cfg_ml = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else {}
            cfg_bling = integracoes.get("bling") if isinstance(integracoes, dict) else {}
            registros.append({
                "loja": nome,
                "mercado_livre_conectado": bool(isinstance(cfg_ml, dict) and str(cfg_ml.get("access_token") or "").strip()),
                "mercado_livre_user_id": str((cfg_ml or {}).get("user_id") or "").strip() if isinstance(cfg_ml, dict) else "",
                "bling_conectado": bool(isinstance(cfg_bling, dict) and str(cfg_bling.get("access_token") or "").strip()),
                "bling_cliente_configurado": bool(isinstance(cfg_bling, dict) and str((cfg_bling or {}).get("id") or (cfg_bling or {}).get("client_id") or "").strip()),
            })

        return {
            "function": "get_integrations_status",
            "arguments": {"loja": loja_filtro or ""},
            "result": {
                "lojas": registros,
                "total_lojas": len(registros),
                "ml_conectadas": sum(1 for item in registros if item.get("mercado_livre_conectado")),
                "bling_conectadas": sum(1 for item in registros if item.get("bling_conectado")),
            },
        }
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha ao consultar status das integracoes: %s", exc)
        return None
