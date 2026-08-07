from __future__ import annotations

import logging
import os
from typing import Optional

from .parsing import (
    _ia_extrair_periodo_mensagem_vendas,
    _ia_resolver_loja_mensagem_vendas,
    _ia_tool_resolver_sku,
)
from .repository import _ia_devolucoes_db_resumo
from .runtime import get_tenant_path

logger = logging.getLogger(__name__)


def _ia_tool_get_returns_quantity_by_period(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None) -> Optional[dict]:
    try:
        resumo = _ia_devolucoes_db_resumo(client_id, data_inicio, data_fim, loja, sku=None, limite_top_skus=0)
        if not resumo:
            return None

        return {
            "function": "get_returns_quantity_by_period",
            "arguments": {"data_inicio": resumo.get("data_inicio") or "", "data_fim": resumo.get("data_fim") or "", "loja": resumo.get("loja") or ""},
            "result": {
                "data_inicio": resumo.get("data_inicio") or "",
                "data_fim": resumo.get("data_fim") or "",
                "loja": resumo.get("loja") or "",
                "quantidade_devolvida_total": float(resumo.get("quantidade_devolvida_total") or 0),
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter quantidade de devoluções por período: {exc}")
        return None


def _ia_tool_get_returns_by_period(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None, limite: int = 10) -> Optional[dict]:
    try:
        resumo = _ia_devolucoes_db_resumo(client_id, data_inicio, data_fim, loja, sku=None, limite_top_skus=max(1, min(int(limite or 10), 50)))
        if not resumo:
            return None
        return {
            "function": "get_returns_by_period",
            "arguments": {
                "data_inicio": resumo.get("data_inicio") or "",
                "data_fim": resumo.get("data_fim") or "",
                "loja": resumo.get("loja") or "",
                "limite": max(1, min(int(limite or 10), 50)),
            },
            "result": {
                "data_inicio": resumo.get("data_inicio") or "",
                "data_fim": resumo.get("data_fim") or "",
                "loja": resumo.get("loja") or "",
                "quantidade_devolvida_total": float(resumo.get("quantidade_devolvida_total") or 0),
                "valor_devolvido_total": float(resumo.get("valor_devolvido_total") or 0),
                "ultima_devolucao": resumo.get("ultima_devolucao") or "",
                "top_skus": resumo.get("top_skus") or [],
                "bancos_consultados": int(resumo.get("bancos_consultados") or 0),
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter resumo de devoluções por período: {exc}")
        return None


def _ia_chat_scope_notice(client_id: str, loja: Optional[str]) -> Optional[dict]:
    if loja and str(loja).strip() not in ("", "__todas", "Todas as lojas"):
        return None
    try:
        tenant_path = get_tenant_path(client_id)
        lojas = []
        if os.path.exists(tenant_path):
            for nome in sorted(os.listdir(tenant_path)):
                if nome.startswith("vendas_historico_") and nome.endswith(".db") and ".backup_" not in nome:
                    slug = nome[len("vendas_historico_"):-3]
                    if slug:
                        lojas.append(slug.replace("_", " "))
        if not lojas:
            return None
        return {
            "function": "store_scope_notice",
            "arguments": {"loja": ""},
            "result": {
                "scope": "all_stores",
                "lojas_disponiveis": lojas,
                "message": "Loja não informada; consulta consolidada em todas as lojas. Se quiser, pergunte ao usuário qual loja específica deseja analisar.",
            },
        }
    except Exception:
        return None


def _ia_tool_get_returns_data(
    client_id: str,
    mensagem: str,
    contexto: Optional[dict] = None,
    produto_tool: Optional[dict] = None,
    loja: Optional[str] = None,
) -> Optional[dict]:
    try:
        ctx = contexto if isinstance(contexto, dict) else {}
        data_inicio, data_fim = _ia_extrair_periodo_mensagem_vendas(mensagem, ctx)
        loja_msg = _ia_resolver_loja_mensagem_vendas(client_id, mensagem, ctx)
        sku = _ia_tool_resolver_sku(client_id, mensagem, produto_tool)

        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else loja_msg
        resumo = _ia_devolucoes_db_resumo(client_id, data_inicio, data_fim, loja_filtro, sku=sku or None, limite_top_skus=10)
        if not resumo:
            return None

        return {
            "function": "get_returns_data",
            "arguments": {
                "sku": sku or "",
                "data_inicio": resumo.get("data_inicio") or "",
                "data_fim": resumo.get("data_fim") or "",
                "loja": resumo.get("loja") or "",
            },
            "result": {
                "sku": sku or "",
                "produto": resumo.get("produto") or "",
                "quantidade_devolvida": float(resumo.get("quantidade_devolvida_total") or 0),
                "valor_devolvido": float(resumo.get("valor_devolvido_total") or 0),
                "ultima_devolucao": resumo.get("ultima_devolucao") or "",
                "top_skus": resumo.get("top_skus") or [],
                "bancos_consultados": int(resumo.get("bancos_consultados") or 0),
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter devoluções: {exc}")
        return None


def _ia_tool_get_returns_by_sku_period(
    client_id: str,
    mensagem: str,
    contexto: Optional[dict] = None,
    produto_tool: Optional[dict] = None,
    loja: Optional[str] = None,
) -> Optional[dict]:
    try:
        sku = _ia_tool_resolver_sku(client_id, mensagem, produto_tool)
        if not sku:
            return None

        ctx = contexto if isinstance(contexto, dict) else {}
        data_inicio, data_fim = _ia_extrair_periodo_mensagem_vendas(mensagem, ctx)
        loja_msg = _ia_resolver_loja_mensagem_vendas(client_id, mensagem, ctx)
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else loja_msg

        resumo = _ia_devolucoes_db_resumo(client_id, data_inicio, data_fim, loja_filtro, sku=sku, limite_top_skus=0)
        if not resumo:
            return None

        return {
            "function": "get_returns_by_sku_period",
            "arguments": {
                "sku": sku,
                "data_inicio": resumo.get("data_inicio") or "",
                "data_fim": resumo.get("data_fim") or "",
                "loja": resumo.get("loja") or "",
            },
            "result": {
                "sku": sku,
                "produto": resumo.get("produto") or "",
                "data_inicio": resumo.get("data_inicio") or "",
                "data_fim": resumo.get("data_fim") or "",
                "loja": resumo.get("loja") or "",
                "quantidade_devolvida": float(resumo.get("quantidade_devolvida_total") or 0),
                "valor_devolvido": float(resumo.get("valor_devolvido_total") or 0),
                "ultima_devolucao": resumo.get("ultima_devolucao") or "",
                "bancos_consultados": int(resumo.get("bancos_consultados") or 0),
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter devoluções por SKU/período: {exc}")
        return None


__all__ = [
    "_ia_tool_get_returns_quantity_by_period",
    "_ia_tool_get_returns_by_period",
    "_ia_chat_scope_notice",
    "_ia_tool_get_returns_data",
    "_ia_tool_get_returns_by_sku_period",
]
