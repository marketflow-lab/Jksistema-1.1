"""Internal Codex Assistant component."""

from __future__ import annotations

import copy
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.services import codex_assistant_storage, codex_turn_context
from backend.services.favoritos_margem import margem_calcular_anuncio, margem_formatar_moeda, margem_parse_float
from backend.services.whatsapp import intent as whatsapp_intent

from .catalog_data import CODEX_DATA_TOOLS
from .settings import DEFAULT_RANKING_LIMIT, EXTERNAL_CACHE_SECONDS
# Permissoes de dados exigidas por ferramenta. A checagem e cumulativa: quando
# uma ferramenta combina dominios (por exemplo, vendas + estoque), o usuario
# precisa ter acesso a todos eles. `full` continua sendo o override explicito.
#
# Ferramentas universais capazes de descobrir/consultar arquivos, enumerar o
# programa, preparar acoes ou ler memoria compartilhada ficam full-only. Elas
# nao podem ser liberadas apenas pelo `module` informado pelo modelo, pois
# `source_id`, fallbacks e classificacoes heuristicas permitiriam atravessar a
# fronteira de um modulo.
ASSISTANT_TOOL_PERMISSION_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "sales_returns_query": ("vendas",),
    "sales_ranking": ("vendas",),
    "sales_summary": ("vendas",),
    "returns_summary": ("vendas",),
    "return_rate": ("vendas",),
    "profit_summary": ("vendas", "cadastro", "impostos"),
    "product_costs_and_margin": ("vendas", "cadastro", "impostos"),
    "stockout_forecast": ("estoque", "vendas"),
    "stale_stock": ("estoque", "vendas"),
    "avg_ticket": ("vendas",),
    "period_comparison": ("vendas",),
    "sales_timeseries": ("vendas",),
    "sales_anomalies": ("vendas",),
    "integrations_status": ("integracao",),
    "mercado_livre_listing": ("anuncios_ml",),
    "mercado_livre_visits": ("anuncios_ml",),
    "mercado_livre_promotions": ("anuncios_ml",),
    "mercado_livre_post_sale_detail": ("perguntas_pos_venda",),
    "mercado_livre_orders": ("vendas", "anuncios_ml"),
    "mercado_livre_returns": ("vendas", "anuncios_ml"),
    "mercado_livre_full_stock": ("mercado_full",),
    "bling_product": ("integracao", "cadastro"),
    "bling_status": ("integracao",),
    "bling_products": ("integracao", "cadastro"),
    "bling_fiscal_product": ("integracao", "impostos"),
    "bling_positive_stock_sku_count": ("integracao", "estoque"),
    "bling_stock_balances": ("integracao", "estoque"),
    "bling_deposits": ("integracao", "estoque"),
    "bling_sales_orders": ("integracao", "vendas"),
    "bling_sales_order_detail": ("integracao", "vendas"),
    "bling_fiscal_nfe": ("integracao", "impostos"),
    "bling_fiscal_nfe_detail": ("integracao", "impostos"),
    "bling_operation_natures": ("integracao", "impostos"),
    "bling_lots": ("integracao", "estoque"),
    "bling_lot_movements": ("integracao", "estoque"),
    "product_data": ("cadastro",),
    "product_registry": ("cadastro",),
    "stock_data": ("estoque",),
    "product_margin": ("cadastro", "impostos"),
    "product_image": ("cadastro",),
    "sync_logs_query": ("integracao",),
    "mercado_livre_readonly": ("anuncios_ml", "perguntas_pos_venda"),
    "questions_post_sale_query": ("perguntas_pos_venda",),
    "fiscal_local_query": ("impostos",),
}

ASSISTANT_FULL_ONLY_TOOLS = frozenset(
    {
        "bling_finance_summary",
        "bling_resource_query",
        "mercado_livre_resource_query",
        "source_discovery",
        "local_database_query",
        "local_csv_query",
        "local_cache_query",
        "operational_memory_query",
        "context_hub_search",
        "program_functions_catalog",
        "capability_resolve",
        "program_action_match",
        "operational_dispatcher",
    }
)

ASSISTANT_CONTEXT_HUB_BOUND_PERMISSION = "context_hub_read_full"

def _assistant_normalize_permissions(permissions: Any) -> dict[str, bool]:
    if not isinstance(permissions, dict):
        return {}
    return {
        str(key or "").strip().lower(): value is True
        for key, value in permissions.items()
        if str(key or "").strip()
    }


def _assistant_tool_access(tool_id: str, permissions: Any) -> dict[str, Any]:
    tool_id = str(tool_id or "").strip()
    normalized = _assistant_normalize_permissions(permissions)
    if normalized.get("full") is True:
        return {
            "allowed": True,
            "full": True,
            "required_permissions": [],
            "missing_permissions": [],
            "reason": "full",
        }
    if (
        tool_id == "context_hub_search"
        and normalized.get(ASSISTANT_CONTEXT_HUB_BOUND_PERMISSION) is True
    ):
        # This narrowly scoped capability is injected only while rebuilding a
        # locally bound WhatsApp session.  It must never behave as `full` or
        # unlock any of the other full-only data tools.
        return {
            "allowed": True,
            "full": False,
            "required_permissions": [ASSISTANT_CONTEXT_HUB_BOUND_PERMISSION],
            "missing_permissions": [],
            "reason": "bound_whatsapp_context_hub",
        }
    if tool_id in ASSISTANT_FULL_ONLY_TOOLS:
        return {
            "allowed": False,
            "full": False,
            "required_permissions": ["full"],
            "missing_permissions": ["full"],
            "reason": "full_only",
        }
    required = list(ASSISTANT_TOOL_PERMISSION_REQUIREMENTS.get(tool_id) or ())
    if not required:
        return {
            "allowed": False,
            "full": False,
            "required_permissions": [],
            "missing_permissions": [],
            "reason": "unmapped_tool",
        }
    missing = [permission for permission in required if normalized.get(permission) is not True]
    return {
        "allowed": not missing,
        "full": False,
        "required_permissions": required,
        "missing_permissions": missing,
        "reason": "allowed" if not missing else "missing_permissions",
    }


def _assistant_tool_allowed(tool_id: str, permissions: Any) -> bool:
    return bool(_assistant_tool_access(tool_id, permissions).get("allowed"))


def _assistant_tools_public(permissions: Any = None) -> list[dict[str, Any]]:
    public = []
    for tool in CODEX_DATA_TOOLS:
        tool_id = str(tool.get("id") or "").strip()
        access = _assistant_tool_access(tool_id, permissions)
        if not access.get("allowed"):
            continue
        fallbacks = [
            str(fallback or "").strip()
            for fallback in (tool.get("fallbacks") or [])
            if str(fallback or "").strip() and _assistant_tool_allowed(str(fallback or "").strip(), permissions)
        ]
        public.append(
            {
                "id": tool_id,
                "module": tool.get("module"),
                "description": tool.get("description"),
                "intent_examples": tool.get("intent_examples") or [],
                "input_schema": _assistant_tool_input_schema(tool_id),
                "executor": tool.get("executor"),
                "external": bool(tool.get("external")),
                "cache_ttl_seconds": int(tool.get("cache_ttl_seconds") or EXTERNAL_CACHE_SECONDS),
                "output_fields": tool.get("output_fields") or [],
                "fallbacks": fallbacks,
                "companion_tools": [
                    str(companion or "")
                    for companion in (tool.get("companion_tools") or [])
                    if str(companion or "") and _assistant_tool_allowed(str(companion or ""), permissions)
                ],
                "zero_is_authoritative": tool.get("zero_is_authoritative") is True,
                "source_role": tool.get("source_role") or "context",
                "aggregation_policy": tool.get("aggregation_policy") or "standard",
                "required_permissions": list(access.get("required_permissions") or []),
                "read_only": tool.get("read_only") is True,
                "mutating_requires_approval": True,
            }
        )
    return public


def _assistant_period_tool_schemas() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    period_schema = {
        "data_inicio": "YYYY-MM-DD",
        "data_fim": "YYYY-MM-DD",
        "loja": "nome da loja/conta ou vazio para consolidado",
    }
    return period_schema, {
        "sales_returns_query": {
            **period_schema,
            "sku": "SKU opcional",
            "separar_por_loja": True,
            "incluir_registros": True,
        },
        "sales_ranking": {**period_schema, "limite": DEFAULT_RANKING_LIMIT},
        "sales_summary": period_schema,
        "returns_summary": {**period_schema, "limite": 50},
        "return_rate": period_schema,
        "profit_summary": period_schema,
        "product_costs_and_margin": {**period_schema, "sku": "SKU opcional", "limite": DEFAULT_RANKING_LIMIT},
        "avg_ticket": period_schema,
        "period_comparison": {
            "data_inicio_a": "YYYY-MM-DD",
            "data_fim_a": "YYYY-MM-DD",
            "data_inicio_b": "YYYY-MM-DD",
            "data_fim_b": "YYYY-MM-DD",
            "loja": "nome da loja/conta ou vazio para consolidado",
        },
        "sales_timeseries": period_schema,
        "sales_anomalies": period_schema,
        "stockout_forecast": {"loja": "nome da loja/conta ou vazio", "lookback_days": 30, "limite": 100},
        "stale_stock": {"loja": "nome da loja/conta ou vazio", "limite": 50, "apenas_com_estoque": True},
        "integrations_status": {"loja": "nome da loja/conta ou vazio"},
    }


def _assistant_marketplace_tool_schemas(period_schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "mercado_livre_listing": {
            "mensagem": "SKU, MLB, produto ou pedido de listagem",
            "loja": "nome da loja/conta ou vazio",
            "status": "active|paused|closed",
            "sku": "SKU opcional",
            "item_id": "MLB opcional",
            "offset": 0,
            "limite": "1..100",
            "incluir_detalhes": False,
            "incluir_comercial": False,
            "force_refresh": False,
        },
        "mercado_livre_resource_query": {
            "mensagem": "termos para pesquisar o catalogo; opcional quando resource_id for informado",
            "resource_id": "identificador exato ml.*; vazio lista ou pesquisa o catalogo",
            "loja": "nome exato da loja/conta Mercado Livre para executar",
            "params": "objeto fechado com os parametros permitidos pelo recurso",
            "limite": "1..100",
            "force_refresh": False,
        },
        "mercado_livre_visits": {
            "mensagem": "MLB exato e periodo desejado",
            "loja": "nome exato da loja/conta Mercado Livre",
            "item_id": "MLB obrigatorio",
            "dias": "1..150",
            "force_refresh": False,
        },
        "mercado_livre_promotions": {
            "mensagem": "campanhas ou promocoes da conta",
            "loja": "nome exato da loja/conta Mercado Livre",
            "status": "status exato opcional",
            "promotion_id": "ID da promocao opcional",
            "incluir_contagens": False,
            "limite": "1..50",
            "force_refresh": False,
        },
        "mercado_livre_post_sale_detail": {
            "mensagem": "conversa pos-venda por pack",
            "loja": "nome exato da loja/conta Mercado Livre",
            "pack_id": "pack numerico obrigatorio",
            "order_id": "pedido numerico opcional",
            "limite": "1..100 mensagens",
        },
        "mercado_livre_orders": {
            **period_schema,
            "status": "paid,partially_refunded por padrao",
            "sku": "SKU opcional",
            "item_id": "MLB opcional",
            "id_pedido": "ID opcional da order ou do pack Mercado Livre; quando preenchido ignora periodo e paginacao",
            "offset": 0,
            "limite": "1..100 em consultas comuns; 1..20000 em relatorios completos",
            "max_paginas": "1..400 em relatorios completos; cada pagina consulta ate 50 pedidos",
            "incluir_detalhes": False,
            "force_refresh": False,
        },
        "mercado_livre_returns": {
            **period_schema,
            "sku": "SKU opcional",
            "item_id": "MLB opcional",
            "offset": 0,
            "limite": "1..100; use 1 para a ultima devolucao",
            "force_refresh": True,
        },
        "mercado_livre_full_stock": {
            "mensagem": "SKU, MLB, produto ou solicitacao de soma do Full",
            "loja": "nome exato da loja/conta Mercado Livre",
            "sku": "SKU opcional",
            "item_id": "MLB opcional",
            "limite": "1..20000",
            "force_refresh": True,
        },
    }


def _assistant_operational_tool_schemas(period_schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "bling_product": {"mensagem": "SKU, id Bling ou produto", "loja": "nome da loja/conta ou vazio"},
        "bling_status": {"loja": "nome da loja/conta ou vazio"},
        "bling_products": {"mensagem": "SKU, id, GTIN, EAN ou nome do produto", "loja": "nome da loja/conta ou vazio", "limite": 50},
        "bling_fiscal_product": {"mensagem": "SKU, id, GTIN, EAN ou nome do produto", "loja": "nome da loja/conta ou vazio"},
        "bling_positive_stock_sku_count": {"loja": "nome exato da loja/conta", "force_refresh": True},
        "bling_stock_balances": {"mensagem": "SKU, codigo ou produto", "loja": "nome da loja/conta ou vazio", "limite": 50},
        "bling_deposits": {"loja": "nome da loja/conta ou vazio", "limite": 50},
        "bling_sales_orders": {**period_schema, "limite": 50},
        "bling_sales_order_detail": {"id_pedido": "ID do pedido Bling", "loja": "nome da loja/conta ou vazio"},
        "bling_fiscal_nfe": {**period_schema, "tipo": "entrada|saida|vazio", "limite": 50},
        "bling_fiscal_nfe_detail": {"id_ou_chave": "ID da NF-e ou chave de acesso", "loja": "nome da loja/conta ou vazio"},
        "bling_operation_natures": {"loja": "nome da loja/conta ou vazio", "limite": 50},
        "bling_lots": {"mensagem": "SKU, codigo ou produto", "loja": "nome da loja/conta ou vazio", "limite": 50},
        "bling_lot_movements": {"id_lote_ou_sku": "ID do lote ou SKU/produto", "loja": "nome da loja/conta ou vazio", "limite": 50},
        "bling_finance_summary": {**period_schema, "tipo": "receber|pagar|caixa|financeiro", "limite": 50},
        "bling_resource_query": {"recurso": "rota GET Bling ou descricao do recurso", **period_schema, "limite": 50},
        "product_data": {"mensagem": "SKU ou termo de produto"},
        "product_registry": {"mensagem": "SKU ou termo de produto"},
        "stock_data": {"mensagem": "SKU ou termo de produto"},
        "product_margin": {"mensagem": "SKU ou termo de produto"},
        "product_image": {"mensagem": "SKU ou termo de produto"},
        "source_discovery": {
            "mensagem": "termo opcional de busca",
            "modulo": "vendas|estoque|cadastro|bling|mercado_livre|perguntas_pos_venda|fiscal|integracoes opcional",
            "tipo": "sqlite|csv|json|cache|sync_log|text opcional",
            "limite": 300,
        },
        "local_database_query": {
            "mensagem": "termo, SKU, produto ou descricao da busca",
            "source_id": "fonte especifica opcional retornada por source_discovery",
            "sql": "SELECT opcional e seguro; nunca INSERT/UPDATE/DELETE",
            "modulo": "modulo opcional",
            "limite": 50,
        },
        "local_csv_query": {
            "mensagem": "termo, SKU, produto ou descricao da busca",
            "source_id": "fonte CSV especifica opcional",
            "modulo": "modulo opcional",
            "limite": 50,
        },
        "local_cache_query": {
            "mensagem": "termo, SKU, loja, erro ou descricao da busca",
            "source_id": "fonte JSON/cache especifica opcional",
            "modulo": "modulo opcional",
            "limite": 50,
        },
        "sync_logs_query": {
            "mensagem": "erro, loja, modulo ou periodo da sincronizacao",
            "source_id": "fonte de log/estado opcional",
            "modulo": "integracoes|vendas|shared_sync opcional",
            "limite": 50,
        },
        "mercado_livre_readonly": {
            "mensagem": "SKU, MLB, produto, pergunta, anuncio ou pedido de contexto ML",
            "loja": "nome da loja/conta ou vazio",
            "limite": 50,
        },
        "questions_post_sale_query": {
            "mensagem": "pergunta, comprador, SKU ou pedido para consultar a fila atual",
            "loja": "nome exato da loja/conta; deixe vazio para todas as lojas conectadas",
            "status": "UNANSWERED por padrao; ANSWERED, CLOSED_UNANSWERED ou vazio para historico completo",
            "todas_lojas": "true para consultar e separar todas as contas conectadas",
            "limite": 50,
            "force_refresh": "true para ignorar o cache curto e consultar agora",
        },
        "fiscal_local_query": {
            "mensagem": "SKU, NCM, CEST, produto ou regra fiscal",
            "source_id": "fonte fiscal opcional",
            "limite": 50,
        },
        "operational_memory_query": {
            "mensagem": "pedido atual ou termo para buscar na memoria operacional",
            "categoria": "lojas_usadas|skus_frequentes|relatorios_anteriores|decisoes|regras_comerciais|custos_pendentes|alertas_ignorados|preferencias_resposta opcional",
            "limite": 20,
        },
        "context_hub_search": {
            "query": "pergunta ou termos tecnicos",
            "sku": "SKU exato opcional",
            "mlb": "MLB exato opcional",
            "store_ref": "loja exata autorizada opcional",
            "module": "modulo ou dominio opcional",
            "ids": "lista opcional de IDs estaveis jk:*",
            "source_type": "tipo de fonte opcional",
            "environment": "development|installed opcional",
            "surface": "superficie documental exata opcional; alias preferencial de environment",
            "tags": "lista opcional de tags obrigatorias",
            "valid_at": "data ISO opcional para validade documental",
            "limit": "1..12",
        },
        "program_functions_catalog": {
            "mensagem": "pedido do usuario ou termo de busca",
            "modulo": "modulo opcional",
            "categoria": "consultar|diagnosticar|gerar_relatorio|preparar_acao|executar_acao_aprovada opcional",
            "limite": 300,
            "incluir_rotas": True,
            "incluir_servicos": True,
            "incluir_acoes": True,
            "incluir_telas": True,
        },
        "capability_resolve": {
            "mensagem": "pedido do usuario",
            "capability_id": "ID opcional da capacidade",
            "modulo": "modulo opcional",
            "categoria": "categoria opcional",
            "params": "parametros ja conhecidos",
            "limite": 8,
        },
        "program_action_match": {
            "mensagem": "pedido de acao do usuario",
            "action_id": "ID opcional da acao",
            "capability_id": "ID opcional da capacidade resolvida",
            "params": "parametros opcionais",
        },
        "operational_dispatcher": {"mensagem": "pedido do usuario", "screen_context": "contexto da tela atual"},
    }


def _assistant_tool_input_schema(tool_id: str) -> dict[str, Any]:
    period_schema, schemas = _assistant_period_tool_schemas()
    schemas.update(_assistant_marketplace_tool_schemas(period_schema))
    schemas.update(_assistant_operational_tool_schemas(period_schema))
    return schemas.get(tool_id, {})


def _assistant_tool_meta(tool_id: str) -> dict[str, Any]:
    for tool in CODEX_DATA_TOOLS:
        if str(tool.get("id") or "") == tool_id:
            return tool
    return {"id": tool_id, "module": "desconhecido", "description": "", "executor": "", "external": False, "fallbacks": [], "status": "consultando dados"}


def public_tools(permissions: Any = None) -> list[dict[str, Any]]:
    """Return the public, permission-filtered tool catalog."""

    return _assistant_tools_public(permissions)


def tool_meta(tool_id: str) -> dict[str, Any]:
    """Return metadata for one registered tool."""

    return _assistant_tool_meta(tool_id)
