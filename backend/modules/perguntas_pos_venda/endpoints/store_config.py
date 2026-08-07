"""Store configuration and automation status endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException

from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.schemas import PerguntasLojaConfigRequest, PerguntasLojasConfigLoteRequest
from backend.services.perguntas_pos_venda_automacao import (
    _perguntas_automacao_bg_status,
    _perguntas_automacao_bg_worker_iniciado,
)
from backend.services.vendas_sync_progress import _corrigir_texto_mojibake

_integracoes_nome_normalizado = runtime_adapter("_integracoes_nome_normalizado")
_ml_oauth_status = runtime_adapter("_ml_oauth_status")
_perguntas_loja_config_normalizar = runtime_adapter("_perguntas_loja_config_normalizar")
_perguntas_loja_config_obter = runtime_adapter("_perguntas_loja_config_obter")
_perguntas_loja_config_salvar = runtime_adapter("_perguntas_loja_config_salvar")
_perguntas_loja_configs_carregar = runtime_adapter("_perguntas_loja_configs_carregar")
carregar_lojas = runtime_adapter("carregar_lojas")


def ml_perguntas_listar_lojas(client_id: str = Depends(get_tenant_id)):
    lojas = []
    configs_lojas = _perguntas_loja_configs_carregar(client_id)
    lojas_index = set()
    for loja in carregar_lojas(client_id) or []:
        if not isinstance(loja, dict):
            continue
        nome = _corrigir_texto_mojibake(str(loja.get("nome") or "").strip())
        if not nome:
            continue
        lojas_index.add(_integracoes_nome_normalizado(nome))
        integracoes = loja.get("integracoes") or {}
        cfg = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else {}
        ml_status = _ml_oauth_status(cfg)
        conectado = bool(ml_status.get("conectado"))
        lojas.append({
            "nome": nome,
            "mercadolivre_conectado": conectado,
            "mercadolivre_status": ml_status.get("status") or ("conectado" if conectado else "pendente"),
            "mercadolivre_motivo": ml_status.get("motivo") or "",
            "mercadolivre_oauth_faltando": ml_status.get("faltando") or [],
            "seller_id": str((cfg or {}).get("user_id") or "").strip(),
            "config_perguntas": _perguntas_loja_config_normalizar(_perguntas_loja_config_obter(configs_lojas, nome)),
            "precisa_reintegrar": False,
        })

    for nome_config, config in (configs_lojas or {}).items():
        nome = _corrigir_texto_mojibake(str(nome_config or "").strip())
        nome_norm = _integracoes_nome_normalizado(nome)
        if not nome or not nome_norm or nome_norm in lojas_index:
            continue
        lojas_index.add(nome_norm)
        lojas.append({
            "nome": nome,
            "mercadolivre_conectado": False,
            "mercadolivre_status": "reautenticar",
            "mercadolivre_motivo": "Loja tinha configuracao em Perguntas e pos-venda, mas nao esta mais autenticada em Integracoes.",
            "mercadolivre_oauth_faltando": ["access_token", "refresh_token", "app_id", "client_secret"],
            "seller_id": "",
            "config_perguntas": _perguntas_loja_config_normalizar(config),
            "precisa_reintegrar": True,
        })

    lojas.sort(key=lambda item: (
        0 if item.get("mercadolivre_conectado") else 1,
        _integracoes_nome_normalizado(item.get("nome")),
    ))
    return {"success": True, "lojas": lojas}


def ml_perguntas_salvar_config_loja(req: PerguntasLojaConfigRequest, client_id: str = Depends(get_tenant_id)):
    config = _perguntas_loja_config_salvar(
        client_id,
        req.loja,
        req.responder_automaticamente,
        req.solicitar_aprovacao,
        req.notificar_whatsapp_aprovacoes,
        req.habilitar_pos_venda_automatico,
        req.intervalo_minutos,
    )
    return {"success": True, "loja": req.loja, "config_perguntas": config}


def ml_perguntas_salvar_config_lojas_lote(req: PerguntasLojasConfigLoteRequest, client_id: str = Depends(get_tenant_id)):
    configs_lojas = _perguntas_loja_configs_carregar(client_id)
    intervalo_cfg = _perguntas_loja_config_normalizar({"intervalo_minutos": req.intervalo_minutos})
    intervalo_minutos = intervalo_cfg["intervalo_minutos"]
    atualizadas = []
    ignoradas = []
    for loja_cfg in carregar_lojas(client_id) or []:
        if not isinstance(loja_cfg, dict):
            continue
        nome = _corrigir_texto_mojibake(str(loja_cfg.get("nome") or "").strip())
        if not nome:
            continue
        integracoes = loja_cfg.get("integracoes") or {}
        ml_cfg = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else {}
        conectado = bool(_ml_oauth_status(ml_cfg).get("conectado"))
        if req.somente_conectadas and not conectado:
            ignoradas.append({"loja": nome, "motivo": "mercado_livre_desconectado"})
            continue
        config_atual = _perguntas_loja_config_normalizar(_perguntas_loja_config_obter(configs_lojas, nome))
        config = _perguntas_loja_config_salvar(
            client_id,
            nome,
            config_atual.get("responder_automaticamente") is True,
            config_atual.get("solicitar_aprovacao") is True,
            config_atual.get("notificar_whatsapp_aprovacoes") is True,
            config_atual.get("habilitar_pos_venda_automatico") is True,
            intervalo_minutos,
        )
        atualizadas.append({"loja": nome, "config_perguntas": config})
    if not atualizadas:
        raise HTTPException(status_code=400, detail="Nenhuma conta Mercado Livre conectada para atualizar.")
    return {
        "success": True,
        "intervalo_minutos": intervalo_minutos,
        "atualizadas": atualizadas,
        "ignoradas": ignoradas,
        "total": len(atualizadas),
    }


def ml_perguntas_automacao_status(
    loja: Optional[str] = None,
    client_id: str = Depends(get_tenant_id),
):
    configs_lojas = _perguntas_loja_configs_carregar(client_id)
    loja_filtro = str(loja or "").strip()
    loja_filtro_norm = _integracoes_nome_normalizado(loja_filtro)
    lojas_status = []
    lojas_vistas = set()

    for loja_cfg in carregar_lojas(client_id) or []:
        if not isinstance(loja_cfg, dict):
            continue
        nome_runtime = str(loja_cfg.get("nome") or "").strip()
        nome_publico = _corrigir_texto_mojibake(nome_runtime)
        nome_norm = _integracoes_nome_normalizado(nome_publico)
        if not nome_runtime or not nome_norm or nome_norm in lojas_vistas:
            continue
        if loja_filtro_norm and nome_norm != loja_filtro_norm:
            continue
        lojas_vistas.add(nome_norm)

        config = _perguntas_loja_config_normalizar(
            _perguntas_loja_config_obter(configs_lojas, nome_runtime)
        )
        status = _perguntas_automacao_bg_status(client_id, nome_runtime, "perguntas")
        lojas_status.append({
            "loja": nome_publico,
            "automacao_ativa": bool(config.get("responder_automaticamente")),
            "intervalo_minutos": config.get("intervalo_minutos"),
            **status,
        })

    lojas_status.sort(key=lambda item: _integracoes_nome_normalizado(item.get("loja")))
    return {
        "success": True,
        "worker_iniciado": _perguntas_automacao_bg_worker_iniciado(),
        "lojas": lojas_status,
        "total": len(lojas_status),
    }


__all__ = [
    "ml_perguntas_listar_lojas",
    "ml_perguntas_salvar_config_loja",
    "ml_perguntas_salvar_config_lojas_lote",
    "ml_perguntas_automacao_status",
]
