"""Read-only Codex assistant data tools, proactive checks, and reports."""

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
from backend.services.favoritos_margem import (
    margem_calcular_anuncio,
    margem_formatar_moeda,
    margem_parse_float,
)
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.whatsapp import intent as whatsapp_intent


ASSISTANT_LOCK = threading.RLock()
API_QUERY_AUDIT_LOCK = threading.RLock()
PROACTIVE_INTERVAL_SECONDS = 30 * 60
EXTERNAL_CACHE_SECONDS = 120
API_QUERY_TIMEOUT_SECONDS = 60
DAILY_ANALYSIS_ENABLED = False
REPORT_FORMATS = {"html", "xlsx", "pdf"}
DEFAULT_RANKING_LIMIT = 50
BLING_REPORT_LIMIT = 200
CODEX_DATA_TOOLS_VERSION = "20260720-data-tools-v22-ml-query-catalog"
CODEX_DATA_CONTEXT_CHAR_LIMIT = int(os.getenv("JK_CODEX_DATA_CONTEXT_CHAR_LIMIT") or "64000")
CODEX_DATA_PREVIEW_CHAR_LIMIT = int(os.getenv("JK_CODEX_DATA_PREVIEW_CHAR_LIMIT") or "24000")
CODEX_SALES_RETURNS_CONTEXT_CHAR_LIMIT = int(os.getenv("JK_CODEX_SALES_RETURNS_CONTEXT_CHAR_LIMIT") or "64000")
CODEX_AGENT_NORMAL_ROW_LIMIT = int(os.getenv("JK_CODEX_AGENT_NORMAL_ROW_LIMIT") or "50")
CODEX_AGENT_REPORT_ROW_LIMIT = int(os.getenv("JK_CODEX_AGENT_REPORT_ROW_LIMIT") or "200")
CODEX_AGENT_TOP_ROWS_LIMIT = int(os.getenv("JK_CODEX_AGENT_TOP_ROWS_LIMIT") or "16")
CODEX_AGENT_TEXT_VALUE_LIMIT = int(os.getenv("JK_CODEX_AGENT_TEXT_VALUE_LIMIT") or "900")


CODEX_DATA_TOOLS: list[dict[str, Any]] = [
    {
        "id": "sales_returns_query",
        "module": "vendas_devolucoes",
        "description": "Consulta unificada de vendas e devolucoes por periodo, loja e SKU.",
        "intent_examples": ["vendas e devolucoes por loja", "maior devolucao", "vendas por sku no periodo"],
        "executor": "sales_returns_query",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["totais", "por_loja", "por_sku", "vendas", "devolucoes"],
        "fallbacks": ["sales_ranking", "returns_summary", "integrations_status"],
        "status": "consultando vendas e devolucoes",
    },
    {
        "id": "sales_ranking",
        "module": "vendas",
        "description": "Ranking de SKUs mais vendidos por periodo, com quantidade e valor.",
        "intent_examples": ["ranking dos ultimos 30 dias", "skus mais vendidos", "top produtos"],
        "executor": "_ia_tool_get_sales_by_period",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["sku", "nome", "qtd", "valor", "quantidade_total", "valor_total", "pedidos_total"],
        "fallbacks": ["integrations_status", "bling_sales_orders", "mercado_livre_listing"],
        "status": "consultando vendas locais",
    },
    {
        "id": "sales_summary",
        "module": "vendas",
        "description": "Resumo de quantidade vendida no periodo.",
        "intent_examples": ["quanto vendeu", "total vendido", "itens vendidos"],
        "executor": "_ia_tool_get_sales_quantity_by_period",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["quantidade_total", "quantidade_vendida_total"],
        "fallbacks": ["integrations_status"],
        "status": "resumindo vendas",
    },
    {
        "id": "returns_summary",
        "module": "devolucoes",
        "description": "Resumo e ranking de devolucoes por periodo.",
        "intent_examples": ["devolucoes do periodo", "produtos devolvidos", "taxa de devolucao"],
        "executor": "_ia_tool_get_returns_by_period",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["quantidade_devolvida_total", "valor_devolvido_total", "top_skus"],
        "fallbacks": ["integrations_status"],
        "status": "consultando devolucoes",
    },
    {
        "id": "return_rate",
        "module": "devolucoes",
        "description": "Taxa de devolucao por quantidade e por valor.",
        "intent_examples": ["taxa de devolucao", "percentual devolvido"],
        "executor": "_ia_tool_get_return_rate_by_period",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["taxa_devolucao_quantidade_percentual", "taxa_devolucao_valor_percentual"],
        "fallbacks": ["returns_summary"],
        "status": "calculando taxa de devolucao",
    },
    {
        "id": "profit_summary",
        "module": "margem",
        "description": "Lucro e margem estimados por periodo.",
        "intent_examples": ["lucro", "margem", "rentabilidade"],
        "executor": "_ia_tool_get_profit_by_period",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["lucro_estimado", "margem_percentual_estimada", "skus_considerados", "skus_com_custo"],
        "fallbacks": ["product_margin"],
        "status": "calculando margem",
    },
    {
        "id": "product_costs_and_margin",
        "module": "margem",
        "description": "Consulta custos/impostos do cadastro e calcula margem por SKU vendido usando a formula do Favoritos.",
        "intent_examples": ["margem real por sku", "custo dos produtos vendidos", "relatorio com lucro por produto"],
        "executor": "codex_assistant.product_costs_and_margin",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": [
            "sku",
            "produto",
            "quantidade_vendida",
            "valor_vendido",
            "custo_unitario",
            "custo_total",
            "imposto",
            "frete",
            "tarifa",
            "lucro_estimado",
            "margem_percentual",
            "status_margem",
        ],
        "fallbacks": ["sales_ranking", "product_margin", "mercado_livre_listing"],
        "status": "calculando custo e margem por SKU",
    },
    {
        "id": "stockout_forecast",
        "module": "estoque",
        "description": "Previsao de ruptura de estoque usando vendas recentes.",
        "intent_examples": ["risco de ruptura", "quando acaba", "estoque acabando"],
        "executor": "_ia_tool_get_stockout_forecast",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["sku", "saldo_total", "media_venda_dia", "dias_ate_ruptura", "risco_ruptura"],
        "fallbacks": ["stock_data", "sales_ranking"],
        "status": "consultando estoque",
    },
    {
        "id": "stale_stock",
        "module": "estoque",
        "description": "SKUs com saldo de loja e sem venda recente, com idade, custo cadastrado e capital estimado.",
        "intent_examples": ["estoque parado", "sem vender", "dias sem venda"],
        "executor": "_ia_tool_get_days_without_sale_top",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": [
            "sku",
            "produto",
            "saldo_loja",
            "saldo_full",
            "ultima_venda",
            "dias_sem_vender",
            "custo_unitario",
            "valor_custo_estoque_loja",
        ],
        "fallbacks": ["stock_data"],
        "status": "procurando estoque parado",
    },
    {
        "id": "avg_ticket",
        "module": "vendas",
        "description": "Ticket medio por periodo.",
        "intent_examples": ["ticket medio", "valor medio por pedido"],
        "executor": "_ia_tool_get_avg_ticket_by_period",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["ticket_medio", "pedidos_total", "valor_vendido_total"],
        "fallbacks": ["sales_ranking"],
        "status": "calculando ticket medio",
    },
    {
        "id": "period_comparison",
        "module": "vendas",
        "description": "Comparacao entre periodo atual e anterior.",
        "intent_examples": ["comparar periodo", "cresceu ou caiu", "mesmo periodo anterior"],
        "executor": "_ia_tool_get_period_comparison",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["variacao_quantidade_percentual", "variacao_faturamento_percentual", "variacao_pedidos_percentual"],
        "fallbacks": ["sales_ranking"],
        "status": "comparando periodos",
    },
    {
        "id": "sales_timeseries",
        "module": "vendas",
        "description": "Serie diaria de vendas e devolucoes.",
        "intent_examples": ["evolucao diaria", "curva de vendas", "serie temporal"],
        "executor": "_ia_tool_get_sales_timeseries",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["data", "quantidade_vendida", "valor_vendido", "quantidade_devolvida", "valor_devolvido"],
        "fallbacks": ["sales_ranking"],
        "status": "montando curva de vendas",
    },
    {
        "id": "sales_anomalies",
        "module": "vendas",
        "description": "Picos e quedas fora do padrao na curva de vendas.",
        "intent_examples": ["anomalias", "queda de venda", "pico de venda"],
        "executor": "_ia_tool_detect_sales_anomalies",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["alertas", "media_valor_diario", "desvio_padrao_valor_diario"],
        "fallbacks": ["sales_timeseries"],
        "status": "detectando anomalias",
    },
    {
        "id": "integrations_status",
        "module": "integracoes",
        "description": "Status read-only de conexoes Mercado Livre e Bling por loja.",
        "intent_examples": ["status integracoes", "contas conectadas", "token vencido"],
        "executor": "_ia_tool_get_integrations_status",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["loja", "mercado_livre_conectado", "bling_conectado"],
        "fallbacks": [],
        "status": "validando integracoes",
    },
    {
        "id": "mercado_livre_listing",
        "module": "anuncios",
        "description": "Consulta read-only de anuncios Mercado Livre conectados.",
        "intent_examples": ["anuncios mercado livre", "saude do anuncio", "itens ativos"],
        "executor": "_ia_tool_get_mercado_livre_listing",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": [
            "id", "title", "status", "seller_sku", "price", "available_quantity", "sold_quantity",
            "health", "permalink", "description", "pictures", "picture_urls",
        ],
        "fallbacks": ["integrations_status"],
        "status": "consultando Mercado Livre read-only",
    },
    {
        "id": "mercado_livre_resource_query",
        "module": "mercado_livre",
        "description": "Catalogo oficial versionado e executor fechado de endpoints de consulta do Mercado Livre. Aceita resource_id; nunca URL livre.",
        "intent_examples": [
            "catalogo de endpoints mercado livre",
            "consultar recurso oficial do mercado livre",
            "buscar estoque de user product ou desempenho do anuncio",
        ],
        "executor": "mercado_livre_query_service.execute_mercado_livre_query",
        "external": True,
        "read_only": True,
        "sensitive": True,
        "cache_ttl_seconds": 0,
        "output_fields": ["catalog_version", "resource", "data", "rows", "records", "sources"],
        "fallbacks": [],
        "zero_is_authoritative": True,
        "source_role": "primary_api",
        "aggregation_policy": "separate_sources_no_sum",
        "status": "consultando recurso oficial do Mercado Livre",
    },
    {
        "id": "mercado_livre_visits",
        "module": "anuncios",
        "description": "Visitas diarias de um anuncio MLB exato, com ownership confirmado na loja escolhida.",
        "intent_examples": ["visitas do MLB", "visualizacoes do anuncio", "acessos nos ultimos 30 dias"],
        "executor": "_ia_tool_get_mercado_livre_visits",
        "external": True,
        "read_only": True,
        "cache_ttl_seconds": 120,
        "output_fields": ["item_id", "date_from", "date_to", "total_visits", "average", "record", "results"],
        "fallbacks": [],
        "zero_is_authoritative": True,
        "status": "consultando visitas do anuncio no Mercado Livre",
    },
    {
        "id": "mercado_livre_promotions",
        "module": "anuncios",
        "description": "Campanhas e promocoes read-only da conta Mercado Livre exata.",
        "intent_examples": ["campanhas do mercado livre", "promocoes ativas", "ofertas da conta"],
        "executor": "_ia_tool_get_mercado_livre_promotions",
        "external": True,
        "read_only": True,
        "cache_ttl_seconds": 120,
        "output_fields": ["id", "name", "type", "status", "start_date", "finish_date", "benefits", "counts"],
        "fallbacks": [],
        "zero_is_authoritative": True,
        "status": "consultando promocoes do Mercado Livre",
    },
    {
        "id": "mercado_livre_post_sale_detail",
        "module": "perguntas_pos_venda",
        "description": "Conversa read-only de pos-venda por pack exato, com PII e URLs privadas removidas.",
        "intent_examples": ["mensagens do pack", "detalhe da conversa pos venda", "anexos da conversa"],
        "executor": "codex_readonly_sources.mercado_livre_post_sale_detail",
        "external": True,
        "read_only": True,
        "sensitive": True,
        "cache_ttl_seconds": 0,
        "output_fields": ["pack_id", "order_id", "status", "items", "messages", "messages_returned"],
        "fallbacks": [],
        "zero_is_authoritative": True,
        "status": "consultando conversa de pos-venda Mercado Livre",
    },
    {
        "id": "mercado_livre_orders",
        "module": "vendas_mercado_livre",
        "description": "Consulta read-only de pedidos e vendas pela API do Mercado Livre.",
        "intent_examples": ["pedidos mercado livre", "vendas via api mercado livre", "pedidos pagos por sku"],
        "executor": "_ia_tool_get_mercado_livre_orders",
        "external": True,
        "read_only": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": [
            "order_id", "pack_id", "status", "date_created", "date_closed",
            "items", "gross_amount", "paid_amount", "refund_amount", "net_amount",
            "buyer_name", "buyer_nickname", "shipment", "fulfillment", "claims",
            "returns", "conversations",
        ],
        "fallbacks": [],
        "companion_tools": ["bling_sales_orders"],
        "zero_is_authoritative": True,
        "status": "consultando pedidos Mercado Livre read-only",
    },
    {
        "id": "mercado_livre_returns",
        "module": "vendas_mercado_livre",
        "description": "Consulta devolucoes diretamente na API de claims/returns do Mercado Livre, ordenadas pela atualizacao mais recente.",
        "intent_examples": ["ultima devolucao da loja", "devolucoes mercado livre", "retorno mais recente pela api"],
        "executor": "_ia_tool_get_mercado_livre_returns",
        "external": True,
        "read_only": True,
        "cache_ttl_seconds": 15,
        "output_fields": ["claim_id", "order_id", "last_updated", "status", "reason_id", "order", "return_detail"],
        "fallbacks": [],
        "zero_is_authoritative": True,
        "status": "consultando devolucoes Mercado Livre read-only",
    },
    {
        "id": "mercado_livre_full_stock",
        "module": "mercado_full",
        "description": "Estoque Full atual consultado exclusivamente na API de inventario do Mercado Livre.",
        "intent_examples": ["estoque full", "saldo fulfillment", "somar estoque full por sku"],
        "executor": "full_mercadolivre.listar_anuncios_full_mercadolivre_payload",
        "external": True,
        "read_only": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["sku", "inventory_id", "full_available_quantity", "full_not_available_quantity", "full_total_quantity"],
        "fallbacks": [],
        "zero_is_authoritative": True,
        "source_role": "primary_api",
        "aggregation_policy": "full_mercado_livre_only",
        "status": "consultando estoque Full no Mercado Livre",
    },
    {
        "id": "bling_product",
        "module": "bling",
        "description": "Consulta read-only de produto no Bling quando houver SKU/produto.",
        "intent_examples": ["produto no bling", "id bling", "dados bling"],
        "executor": "_ia_tool_get_bling_product",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["sku", "id", "nome", "preco", "estoque"],
        "fallbacks": ["integrations_status", "product_data"],
        "status": "consultando Bling read-only",
    },
    {
        "id": "bling_status",
        "module": "bling",
        "description": "Status read-only das contas Bling, canais e depositos conectados.",
        "intent_examples": ["status bling", "contas bling conectadas", "token bling"],
        "executor": "codex_bling_tools.bling_status",
        "external": True,
        "cache_ttl_seconds": 30 * 60,
        "output_fields": ["loja", "bling_conectado", "canais_venda", "depositos"],
        "fallbacks": ["integrations_status"],
        "status": "validando Bling read-only",
    },
    {
        "id": "bling_products",
        "module": "bling",
        "description": "Busca read-only de produtos Bling por SKU, nome, ID, codigo ou GTIN.",
        "intent_examples": ["buscar produto na bling", "dados do sku no bling", "produto por ean"],
        "executor": "codex_bling_tools.bling_products",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["id", "sku", "nome", "preco", "preco_custo", "ncm", "cest"],
        "fallbacks": ["product_data", "integrations_status"],
        "status": "consultando produtos Bling",
    },
    {
        "id": "bling_fiscal_product",
        "module": "bling",
        "description": "Dados fiscais read-only do produto no Bling, incluindo NCM, CEST, origem e unidade.",
        "intent_examples": ["ncm do sku na bling", "cest produto bling", "dados fiscais do produto"],
        "executor": "codex_bling_tools.bling_fiscal_product",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["sku", "ncm", "cest", "origem", "unidade", "preco_custo"],
        "fallbacks": ["bling_products", "product_registry"],
        "status": "consultando fiscal de produto Bling",
    },
    {
        "id": "bling_stock_balances",
        "module": "bling",
        "description": "Saldo de estoque read-only da Bling geral e por deposito.",
        "intent_examples": ["saldo em estoque na bling", "estoque do sku no bling", "saldo por deposito"],
        "executor": "codex_bling_tools.bling_stock_balances",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["sku", "produto", "saldo_total", "depositos"],
        "fallbacks": ["bling_products", "stock_data"],
        "status": "consultando saldo Bling",
    },
    {
        "id": "bling_positive_stock_sku_count",
        "module": "bling",
        "description": "Conta, com paginacao completa, os SKUs distintos com saldo positivo na loja Bling selecionada.",
        "intent_examples": ["quantos SKUs estao com estoque", "numero de produtos com saldo positivo", "total de SKUs com estoque na loja"],
        "executor": "codex_bling_tools.bling_positive_stock_sku_count",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["loja", "positive_sku_count", "store_available", "coverage_complete", "catalog_products_scanned"],
        "fallbacks": [],
        "zero_is_authoritative": True,
        "status": "contando SKUs com estoque na Bling",
    },
    {
        "id": "bling_deposits",
        "module": "bling",
        "description": "Lista read-only de depositos Bling e classificacao loja/full.",
        "intent_examples": ["depositos bling", "estoque full bling", "deposito padrao"],
        "executor": "codex_bling_tools.bling_deposits",
        "external": True,
        "cache_ttl_seconds": 30 * 60,
        "output_fields": ["id", "descricao", "situacao", "tipo_detectado"],
        "fallbacks": ["bling_status"],
        "status": "consultando depositos Bling",
    },
    {
        "id": "bling_sales_orders",
        "module": "bling",
        "description": "Pedidos de venda Bling por periodo, com ranking por SKU quando detalhes estiverem disponiveis.",
        "intent_examples": ["vendas na bling ultimos 30 dias", "pedidos bling", "ranking skus bling"],
        "executor": "codex_bling_tools.bling_sales_orders",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["pedidos", "top_skus", "sku", "quantidade_vendida", "valor_total"],
        "fallbacks": ["sales_ranking", "integrations_status"],
        "status": "consultando pedidos Bling",
    },
    {
        "id": "bling_sales_order_detail",
        "module": "bling",
        "description": "Detalhe read-only de um pedido de venda Bling por ID.",
        "intent_examples": ["detalhe pedido bling 123", "itens do pedido bling"],
        "executor": "codex_bling_tools.bling_sales_order_detail",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["id", "numero", "situacao", "itens"],
        "fallbacks": ["bling_sales_orders"],
        "status": "consultando pedido Bling",
    },
    {
        "id": "bling_fiscal_nfe",
        "module": "bling",
        "description": "NF-e Bling read-only por periodo, tipo, numero, situacao ou chave.",
        "intent_examples": ["notas fiscais bling", "nfe ultimos 30 dias", "nota de saida bling"],
        "executor": "codex_bling_tools.bling_fiscal_nfe",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["id", "numero", "serie", "situacao", "tipo", "valor", "chave_acesso"],
        "fallbacks": ["bling_operation_natures", "integrations_status"],
        "status": "consultando NF-e Bling",
    },
    {
        "id": "bling_fiscal_nfe_detail",
        "module": "bling",
        "description": "Detalhe read-only de NF-e Bling por ID ou chave de acesso.",
        "intent_examples": ["detalhe nfe bling", "nota fiscal chave"],
        "executor": "codex_bling_tools.bling_fiscal_nfe_detail",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["id", "numero", "itens", "tributacao"],
        "fallbacks": ["bling_fiscal_nfe"],
        "status": "consultando detalhe NF-e Bling",
    },
    {
        "id": "bling_operation_natures",
        "module": "bling",
        "description": "Naturezas de operacao Bling read-only.",
        "intent_examples": ["naturezas bling", "natureza de operacao", "tributacao bling"],
        "executor": "codex_bling_tools.bling_operation_natures",
        "external": True,
        "cache_ttl_seconds": 30 * 60,
        "output_fields": ["id", "descricao"],
        "fallbacks": ["integrations_status"],
        "status": "consultando naturezas Bling",
    },
    {
        "id": "bling_lots",
        "module": "bling",
        "description": "Lotes de produtos Bling read-only por SKU/produto.",
        "intent_examples": ["lotes do sku", "validade produto bling", "controle de lote"],
        "executor": "codex_bling_tools.bling_lots",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["id", "id_produto", "sku", "produto", "validade", "saldo"],
        "fallbacks": ["bling_products"],
        "status": "consultando lotes Bling",
    },
    {
        "id": "bling_lot_movements",
        "module": "bling",
        "description": "Lancamentos de lote Bling read-only por ID de lote ou SKU.",
        "intent_examples": ["movimentos do lote", "lancamentos lote bling", "entradas e saidas lote"],
        "executor": "codex_bling_tools.bling_lot_movements",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["id_lote", "sku", "produto", "quantidade", "tipo", "data"],
        "fallbacks": ["bling_lots"],
        "status": "consultando lancamentos Bling",
    },
    {
        "id": "bling_finance_summary",
        "module": "bling",
        "description": "Resumo read-only financeiro Bling: contas a receber, pagar e caixas.",
        "intent_examples": ["financeiro bling", "contas a receber bling", "contas a pagar"],
        "executor": "codex_bling_tools.bling_finance_summary",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["tipo", "quantidade", "valor_total", "amostra"],
        "fallbacks": ["bling_resource_query"],
        "status": "consultando financeiro Bling",
    },
    {
        "id": "bling_resource_query",
        "module": "bling",
        "description": "Fallback generico seguro para rotas Bling read-only liberadas por allowlist.",
        "intent_examples": ["consultar recurso bling", "qualquer informacao da bling", "rota bling"],
        "executor": "codex_bling_tools.bling_resource_query",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["rows", "resources", "sources"],
        "fallbacks": ["bling_status"],
        "status": "consultando recurso Bling read-only",
    },
    {
        "id": "product_data",
        "module": "cadastro",
        "description": "Cadastro/estoque local de produtos por SKU ou termo.",
        "intent_examples": ["dados do produto", "buscar sku", "cadastro produto"],
        "executor": "_ia_tool_get_product_data",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["sku", "nome", "saldo_loja", "saldo_full", "preco", "custo", "imposto"],
        "fallbacks": ["bling_product", "mercado_livre_listing"],
        "status": "consultando cadastro local",
    },
    {
        "id": "product_registry",
        "module": "cadastro",
        "description": "Informacoes completas do cadastro de produtos.",
        "intent_examples": ["ncm", "cest", "descricao", "dados fiscais do produto"],
        "executor": "_ia_tool_get_product_registry_info",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["sku", "nome", "ncm", "cest", "descricao"],
        "fallbacks": ["product_data"],
        "status": "consultando cadastro detalhado",
    },
    {
        "id": "stock_data",
        "module": "estoque",
        "description": "Saldo local de estoque por SKU ou produto.",
        "intent_examples": ["saldo do sku", "estoque do produto"],
        "executor": "_ia_tool_get_stock_data",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["sku", "saldo_loja_total", "saldo_full_total", "saldo_total"],
        "fallbacks": ["product_data"],
        "status": "consultando saldo local",
    },
    {
        "id": "product_margin",
        "module": "impostos",
        "description": "Margem/custo/imposto de um produto especifico.",
        "intent_examples": ["margem do sku", "custo do produto", "imposto do produto"],
        "executor": "_ia_tool_get_product_margin",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["sku", "preco", "custo", "imposto", "margem"],
        "fallbacks": ["product_data"],
        "status": "consultando margem do produto",
    },
    {
        "id": "product_image",
        "module": "cadastro",
        "description": "Imagem local/ML do produto quando houver SKU ou termo.",
        "intent_examples": ["imagem do produto", "foto do sku"],
        "executor": "_ia_tool_get_product_image",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["sku", "imagem_url", "imagem_markdown"],
        "fallbacks": ["product_data", "mercado_livre_listing"],
        "status": "buscando imagem do produto",
    },
    {
        "id": "source_discovery",
        "module": "sistema",
        "description": "Descobre fontes read-only disponiveis no info do cliente: bancos, CSVs, caches, logs e JSONs.",
        "intent_examples": ["quais fontes voce consegue consultar", "liste bases locais", "fontes de dados disponiveis"],
        "executor": "codex_readonly_sources.discover_data_sources",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["sources", "total_sources", "schema"],
        "fallbacks": ["program_functions_catalog"],
        "status": "descobrindo fontes read-only",
    },
    {
        "id": "local_database_query",
        "module": "dados_locais",
        "description": "Consulta segura em bancos SQLite locais, somente SELECT, dentro de info/<client_id>.",
        "intent_examples": ["busque no banco local", "procure nos sqlite", "consulte vendas_historico"],
        "executor": "codex_readonly_sources.local_database_query",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["records", "sources", "filters"],
        "fallbacks": ["local_csv_query", "sync_logs_query"],
        "status": "consultando bancos locais",
    },
    {
        "id": "local_csv_query",
        "module": "dados_locais",
        "description": "Consulta read-only em CSVs locais de cadastro, custos, produtos, importacoes e mapas.",
        "intent_examples": ["busque nos csv", "cadastro_custos_lojas", "cadastro_produtos"],
        "executor": "codex_readonly_sources.local_csv_query",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["records", "sources", "filters"],
        "fallbacks": ["local_database_query", "local_cache_query"],
        "status": "consultando CSVs locais",
    },
    {
        "id": "local_cache_query",
        "module": "dados_locais",
        "description": "Consulta caches/JSONs locais com segredos redigidos.",
        "intent_examples": ["busque no cache", "cache mercado livre", "historico favorito", "json local"],
        "executor": "codex_readonly_sources.local_cache_query",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["records", "sources", "sensitive_fields_redacted"],
        "fallbacks": ["source_discovery"],
        "status": "consultando caches locais",
    },
    {
        "id": "sync_logs_query",
        "module": "integracoes",
        "description": "Consulta logs, estados e erros de sincronizacao sem executar sync.",
        "intent_examples": ["por que nao aparecem vendas", "erro de sincronizacao", "status do sync", "logs de sync"],
        "executor": "codex_readonly_sources.sync_logs_query",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["records", "sources", "warnings"],
        "fallbacks": ["integrations_status", "source_discovery"],
        "status": "consultando logs de sincronizacao",
    },
    {
        "id": "mercado_livre_readonly",
        "module": "mercado_livre",
        "description": "Consulta Mercado Livre em modo read-only: anuncios, itens e contexto local de perguntas.",
        "intent_examples": ["anuncios mercado livre", "pergunta aberta no ML", "dados do item Mercado Livre"],
        "executor": "codex_readonly_sources.mercado_livre_readonly",
        "external": True,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["records", "sources", "warnings"],
        "fallbacks": ["mercado_livre_listing", "questions_post_sale_query", "local_cache_query"],
        "status": "consultando Mercado Livre read-only",
    },
    {
        "id": "questions_post_sale_query",
        "module": "perguntas_pos_venda",
        "description": "Consulta a fila atual do Mercado Livre por loja e retorna perguntas, anuncio, produto e historico do comprador para auxiliar a resposta.",
        "intent_examples": ["perguntas abertas", "pos venda pendente", "perguntas respondidas", "aprovacoes ML"],
        "executor": "codex_readonly_sources.questions_post_sale_query",
        "external": True,
        "cache_ttl_seconds": 15,
        "output_fields": ["records", "questions_by_store", "coverage_complete", "sources", "warnings"],
        "fallbacks": [],
        "status": "consultando perguntas e pos-venda",
    },
    {
        "id": "fiscal_local_query",
        "module": "fiscal",
        "description": "Consulta fiscal local: NCM, CEST, impostos, aliquotas, regras e cadastro fiscal.",
        "intent_examples": ["dados fiscais do sku", "ncm cest", "regras fiscais", "imposto local"],
        "executor": "codex_readonly_sources.fiscal_local_query",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["records", "sources", "warnings"],
        "fallbacks": ["bling_fiscal_product", "bling_fiscal_nfe"],
        "status": "consultando fiscal local",
    },
    {
        "id": "operational_memory_query",
        "module": "memoria_operacional",
        "description": "Consulta preferencias, lojas, SKUs, decisoes, relatorios e contexto operacional salvo do Black Jhon.",
        "intent_examples": ["o que voce lembra", "preferencias da loja", "skus frequentes", "relatorios anteriores"],
        "executor": "codex_operational_memory.query_memory",
        "external": False,
        "cache_ttl_seconds": 0,
        "output_fields": ["category", "content", "metadata", "source", "importance"],
        "fallbacks": [],
        "status": "consultando memoria operacional",
    },
    {
        "id": "context_hub_search",
        "module": "sistema",
        "description": "Busca read-only na geracao ativa e validada do Context Hub tecnico do JK Sistema.",
        "intent_examples": ["como funciona esta tela", "qual rota implementa isso", "arquitetura do modulo", "contexto tecnico do SKU"],
        "executor": "context_hub.search_context",
        "external": False,
        "cache_ttl_seconds": 0,
        "output_fields": ["doc_id", "chunk_id", "snippet", "score", "reference", "truth_class", "source_version", "source_hash", "generation_id"],
        "fallbacks": [],
        "status": "consultando Context Hub",
    },
    {
        "id": "program_functions_catalog",
        "module": "sistema",
        "description": "Catalogo normalizado de capacidades, rotas, telas, servicos e acoes aprovaveis do JK Sistema.",
        "intent_examples": ["liste todas as funcoes", "o que o programa faz", "o que voce consegue fazer", "rotas do sistema", "modulos disponiveis"],
        "executor": "codex_capabilities.list_capabilities",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["capabilities", "modules", "totals"],
        "fallbacks": ["operational_dispatcher"],
        "status": "consultando catalogo do programa",
    },
    {
        "id": "capability_resolve",
        "module": "sistema",
        "description": "Resolve a intencao do usuario para uma capacidade do JK Sistema antes de dizer que nao consegue.",
        "intent_examples": ["como faco isso no sistema", "voce consegue responder pergunta ML", "qual ferramenta usa para saldo Bling"],
        "executor": "codex_capabilities.resolve_capability",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["capability", "candidates", "missing_params"],
        "fallbacks": ["program_functions_catalog", "program_action_match"],
        "status": "resolvendo capacidade",
    },
    {
        "id": "program_action_match",
        "module": "sistema",
        "description": "Identifica se um pedido pode virar acao operacional aprovada, sem executar nada.",
        "intent_examples": ["voce consegue sincronizar vendas", "pode atualizar estoque", "qual acao executa isso", "faca isso no programa"],
        "executor": "codex_actions.match_action_dry_run",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["matched", "action", "params", "missing_params", "requires_confirmation"],
        "fallbacks": ["program_functions_catalog"],
        "status": "avaliando acao disponivel",
    },
    {
        "id": "operational_dispatcher",
        "module": "full_favoritos_perguntas_impostos",
        "description": "Dispatcher read-only legado para Full, Favoritos, perguntas/pos-venda, impostos e consultas especificas ainda nao mapeadas diretamente.",
        "intent_examples": ["full", "favoritos", "perguntas", "pos-venda", "impostos"],
        "executor": "_ia_chat_executar_funcoes",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["tool_results"],
        "fallbacks": ["integrations_status"],
        "status": "consultando modulo operacional",
    },
]

# O registro historico nasceu exclusivamente com leitores e varias entradas
# antigas nao declaravam o campo. Materialize o contrato no proprio registro
# para que catalogo e executor possam falhar fechados sem quebrar compatibilidade.
for _tool_contract in CODEX_DATA_TOOLS:
    _tool_contract.setdefault("read_only", True)

# Consultas direcionadas a uma API devem preservar o zero retornado por essa
# fonte. Um resultado vazio do provedor nao pode virar silenciosamente dados de
# banco local ou de outra integracao.
for _api_tool_id in {"mercado_livre_listing", "mercado_livre_resource_query", "mercado_livre_visits", "mercado_livre_promotions", "mercado_livre_post_sale_detail", "mercado_livre_orders", "mercado_livre_returns", "mercado_livre_full_stock", "bling_sales_orders", "questions_post_sale_query"}:
    for _tool_contract in CODEX_DATA_TOOLS:
        if str(_tool_contract.get("id") or "") == _api_tool_id:
            _tool_contract["zero_is_authoritative"] = True
            _tool_contract["fallbacks"] = []
            _tool_contract["source_role"] = "primary_api"
            _tool_contract["aggregation_policy"] = "separate_sources_no_sum"
            if _api_tool_id == "bling_sales_orders":
                _tool_contract["companion_tools"] = ["mercado_livre_orders"]
            elif _api_tool_id == "mercado_livre_orders":
                _tool_contract["companion_tools"] = ["bling_sales_orders"]
            break


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


class CodexAssistantChatRequest(BaseModel):
    message: str
    screen_context: Optional[dict[str, Any]] = None
    history: Optional[list[dict[str, Any]]] = None
    force_refresh: bool = False


class CodexAssistantRunRequest(BaseModel):
    screen_context: Optional[dict[str, Any]] = None
    force: bool = False
    compact: bool = False


class CodexAssistantReportRequest(BaseModel):
    prompt: str = ""
    screen_context: Optional[dict[str, Any]] = None
    thread_id: Optional[str] = None
    conversation_id: Optional[str] = None
    format: Optional[str] = "html"
    force_refresh: bool = False
    profile: Optional[str] = None
    store: Optional[str] = None
    import_list_id: Optional[str] = None


class CodexAssistantReportSettingsRequest(BaseModel):
    settings: dict[str, Any]


class CodexAssistantFinancialAdjustmentRequest(BaseModel):
    adjustment_id: Optional[str] = None
    kind: str = "advertising"
    store: str
    period_start: str
    period_end: str
    amount: float
    platform: str = "manual"
    note: str = ""


class CodexAssistantActionQueueRequest(BaseModel):
    report_id: str = ""
    action_id: str = ""
    action_type: str
    status: str = "queued"
    store: str = ""
    skus: list[str] = []
    impact_brl: Optional[float] = None
    confidence: str = ""
    owner_username: str = ""
    owner_role: str = ""
    due_at: str = ""
    title: str = ""
    evidence: str = ""
    recommendation: str = ""


class CodexAssistantActionQueueUpdateRequest(BaseModel):
    status: Optional[str] = None
    owner_username: Optional[str] = None
    owner_role: Optional[str] = None
    due_at: Optional[str] = None
    note: Optional[str] = None
    result: Optional[dict[str, Any]] = None


class CodexAssistantEvaluationRequest(BaseModel):
    screen_context: Optional[dict[str, Any]] = None
    force: bool = False


class CodexOperationalMemoryRequest(BaseModel):
    category: str = "decisoes"
    content: str
    source: Optional[str] = "manual"
    metadata: Optional[dict[str, Any]] = None
    importance: int = 3
    entry_id: Optional[str] = None


CODEX_ASSISTANT_EVALUATION_CASES: list[dict[str, Any]] = [
    {
        "id": "bling_saldo_sku_124",
        "question": "saldo SKU 124 na Bling",
        "mode": "chat",
        "expected_tools": ["bling_stock_balances"],
        "compatible_tools": ["bling_product", "bling_products", "product_data", "stock_data"],
    },
    {
        "id": "sales_30d_jk_pecas",
        "question": "gere um relatorio dos ultimos 30 dias da JK Pecas",
        "mode": "report",
        "expected_tools": ["sales_ranking"],
        "compatible_tools": ["sales_returns_query", "stockout_forecast", "stale_stock", "product_costs_and_margin", "bling_sales_orders"],
    },
    {
        "id": "open_questions",
        "question": "perguntas sem resposta",
        "mode": "chat",
        "expected_tools": ["questions_post_sale_query"],
        "compatible_tools": ["mercado_livre_readonly", "operational_dispatcher", "local_cache_query"],
    },
    {
        "id": "margin_top_skus",
        "question": "margem dos top SKUs",
        "mode": "report",
        "expected_tools": ["product_costs_and_margin"],
        "compatible_tools": ["sales_ranking", "mercado_livre_listing", "product_registry", "local_csv_query"],
    },
]


def configure_codex_assistant_runtime(runtime_module=None):
    runtime = bind_runtime_globals(globals(), runtime_module)
    try:
        from backend.services import codex_readonly_sources

        codex_readonly_sources.configure_codex_readonly_sources_runtime(runtime_module)
    except Exception:
        pass
    try:
        from backend.services import codex_operational_memory

        codex_operational_memory.configure_codex_operational_memory_runtime(runtime_module)
    except Exception:
        pass
    return runtime


def _assistant_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _assistant_today() -> str:
    return date.today().isoformat()


def _assistant_base_dir() -> str:
    base = str(globals().get("BASE_DIR") or os.getcwd()).strip()
    return os.path.abspath(base or os.getcwd())


def _assistant_info_base() -> str:
    base_info = str(globals().get("PASTA_INFO") or os.path.join(_assistant_base_dir(), "info")).strip()
    if not os.path.isabs(base_info):
        base_info = os.path.join(_assistant_base_dir(), base_info)
    os.makedirs(base_info, exist_ok=True)
    return base_info


def _assistant_safe_id(value: str, fallback: str = "default") -> str:
    safe = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_"})[:80]
    return safe or fallback


def _assistant_client_dir(client_id: str) -> str:
    path = os.path.join(_assistant_info_base(), _assistant_safe_id(client_id), "codex_assistant")
    os.makedirs(path, exist_ok=True)
    os.makedirs(os.path.join(path, "reports"), exist_ok=True)
    os.makedirs(os.path.join(path, "cache"), exist_ok=True)
    return path


def _assistant_path(client_id: str, name: str) -> str:
    return os.path.join(_assistant_client_dir(client_id), name)


def _assistant_read_json(path: str, fallback: Any) -> Any:
    try:
        if not os.path.exists(path):
            return fallback
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data
    except Exception:
        return fallback


def _assistant_write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    os.replace(temp, path)


def _assistant_require_full_admin(request: Request, authorization: Optional[str]) -> dict[str, Any]:
    from backend.services import codex_console

    return codex_console._codex_require_full_admin(request, authorization)


def _assistant_cache_key(client_id: str, mode: str, message: str, screen_context: Any) -> str:
    contexto = {}
    if isinstance(screen_context, dict):
        contexto = {
            "page": screen_context.get("modulo_atual") or screen_context.get("page") or screen_context.get("pathname") or "",
            "periodo": screen_context.get("periodo") or "",
            "loja": screen_context.get("loja") or "",
            "data_inicio": screen_context.get("data_inicio") or "",
            "data_fim": screen_context.get("data_fim") or "",
        }
    raw = json.dumps(
        {"client_id": client_id, "mode": mode, "message": message, "context": contexto, "tools_version": CODEX_DATA_TOOLS_VERSION},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _assistant_cache_get(client_id: str, key: str, ttl_seconds: int) -> Optional[dict[str, Any]]:
    try:
        return codex_assistant_storage.codex_assistant_cache_get(
            _assistant_info_base(),
            client_id,
            key,
            ttl_seconds,
        )
    except Exception:
        pass
    path = os.path.join(_assistant_client_dir(client_id), "cache", f"{key}.json")
    data = _assistant_read_json(path, None)
    if not isinstance(data, dict):
        return None
    created = float(data.get("_created_ts") or 0)
    if created <= 0 or time.time() - created > ttl_seconds:
        return None
    payload = data.get("payload")
    return payload if isinstance(payload, dict) else None


def _assistant_cache_set(client_id: str, key: str, payload: dict[str, Any]) -> None:
    codex_assistant_storage.codex_assistant_cache_set(
        _assistant_info_base(),
        client_id,
        key,
        payload,
        EXTERNAL_CACHE_SECONDS,
    )


_ASSISTANT_CACHE_SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:access_?token|refresh_?token|token|secret|client_?secret|authorization|cookie|api_?key|password|senha)(?:$|_)",
    flags=re.IGNORECASE,
)


def _assistant_cache_safe_payload(value: Any, depth: int = 0) -> Any:
    """Copy a cache payload while dropping credential-shaped fields."""

    if depth > 10:
        return None
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key or "")[:120]
            if _ASSISTANT_CACHE_SECRET_KEY_RE.search(key_text):
                continue
            clean[key_text] = _assistant_cache_safe_payload(item, depth + 1)
        return clean
    if isinstance(value, list):
        return [_assistant_cache_safe_payload(item, depth + 1) for item in value[:1000]]
    if isinstance(value, tuple):
        return [_assistant_cache_safe_payload(item, depth + 1) for item in value[:1000]]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)[:2000]


def _assistant_external_cache_key(client_id: str, tool_id: str, plan: dict[str, Any]) -> str:
    safe_contract = {
        "client_id": str(client_id or "default"),
        "tool_id": str(tool_id or ""),
        "message": str(plan.get("message") or "")[:4000],
        "loja": str(plan.get("loja") or "")[:200],
        "data_inicio": str(plan.get("data_inicio") or ""),
        "data_fim": str(plan.get("data_fim") or ""),
        "status": str(plan.get("status") or "")[:120],
        "sku": str(plan.get("sku") or "")[:80],
        "item_id": str(plan.get("item_id") or "")[:40],
        "id_pedido": str(plan.get("id_pedido") or "")[:60],
        "pack_id": str(plan.get("pack_id") or "")[:60],
        "offset": int(plan.get("offset") or 0),
        "limite": int(plan.get("limite") or 0),
        "mode": str(plan.get("mode") or "")[:20],
        "max_paginas": int(plan.get("max_paginas") or 0),
        "incluir_detalhes": bool(plan.get("incluir_detalhes")),
        "incluir_comercial": bool(plan.get("incluir_comercial")),
        "dias": int(plan.get("dias") or 0),
        "promotion_id": str(plan.get("promotion_id") or "")[:120],
        "incluir_contagens": bool(plan.get("incluir_contagens")),
        "tools_version": CODEX_DATA_TOOLS_VERSION,
    }
    raw = json.dumps(safe_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "external_tool_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


_ASSISTANT_AUDITED_API_TOOLS = {
    "bling_sales_orders",
    "bling_positive_stock_sku_count",
    "bling_stock_balances",
    "mercado_livre_orders",
    "mercado_livre_returns",
    "mercado_livre_listing",
    "mercado_livre_visits",
    "mercado_livre_promotions",
    "mercado_livre_post_sale_detail",
    "mercado_livre_full_stock",
    "questions_post_sale_query",
}


def _assistant_api_query_audit(
    client_id: str,
    tool_id: str,
    plan: dict[str, Any],
    result: dict[str, Any],
    *,
    audit_user: str = "",
    cache_hit: bool = False,
) -> None:
    """Append a credential-free audit event for commercial API reads."""

    if str(tool_id or "") not in _ASSISTANT_AUDITED_API_TOOLS:
        return
    provider = "bling" if str(tool_id).startswith("bling_") else "mercado_livre"
    summaries = result.get("summary") if isinstance(result.get("summary"), list) else []
    provider_status = "ok"
    fallback_used = False
    for item in summaries:
        if not isinstance(item, dict):
            continue
        if str(item.get("source_role") or "") == "supporting_local_history":
            fallback_used = True
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        error = str(summary.get("error") or "").strip()
        status = str(summary.get("status") or "").strip()
        if error:
            provider_status = error[:80]
        elif status and status not in {"ok", "success"}:
            provider_status = status[:80]
        if summary.get("fallback_used") is True:
            fallback_used = True
    if any("fallback" in str(item or "").lower() for item in (result.get("warnings") or [])):
        fallback_used = True
    event = {
        "timestamp": _assistant_now(),
        "user": str(audit_user or "system")[:120],
        "tenant": str(client_id or "default")[:120],
        "store": str(plan.get("loja") or "")[:160],
        "provider": provider,
        "tool": str(tool_id or "")[:80],
        "filters": {
            "data_inicio": str(plan.get("data_inicio") or "")[:20],
            "data_fim": str(plan.get("data_fim") or "")[:20],
            "status": str(plan.get("status") or "")[:120],
            "sku": str(plan.get("sku") or "")[:80],
            "item_id": str(plan.get("item_id") or "")[:40],
            "id_pedido": str(plan.get("id_pedido") or "")[:60],
            "offset": int(plan.get("offset") or 0),
            "limit": int(plan.get("limite") or 0),
        },
        "method": "GET",
        "status": provider_status,
        "records": max(0, int(result.get("records") or 0)),
        "cache_hit": bool(cache_hit),
        "fallback_used": bool(fallback_used),
        "read_only": True,
    }
    exact = result.get("exact_metadata") if isinstance(result.get("exact_metadata"), dict) else {}
    if exact.get("exact_lookup") is True:
        event["exact_lookup"] = {
            "requested_id": str(exact.get("requested_id") or "")[:60],
            "identifier_type": str(exact.get("identifier_type") or "")[:20],
            "matched_stores": [str(item)[:160] for item in (exact.get("matched_stores") or [])[:20]],
            "resolved_order_ids": [str(item)[:60] for item in (exact.get("resolved_order_ids") or [])[:100]],
            "resources": [str(item)[:160] for item in (exact.get("resources") or [])[:30]],
            "stores_checked": [
                {
                    "store": str(item.get("store") or "")[:160],
                    "order_http": item.get("order_http"),
                    "pack_http": item.get("pack_http"),
                    "result": str(item.get("result") or "")[:80],
                }
                for item in (exact.get("searched_stores") or [])[:20]
                if isinstance(item, dict)
            ],
            "partial_response": bool(exact.get("partial_response")),
        }
    for item in summaries:
        if not isinstance(item, dict) or str(item.get("tool_id") or "") != str(tool_id or ""):
            continue
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        target = summary.get("target") if isinstance(summary.get("target"), dict) else {}
        coverage = summary.get("exact_coverage") if isinstance(summary.get("exact_coverage"), dict) else {}
        match = summary.get("match") if isinstance(summary.get("match"), dict) else {}
        if not target or not coverage:
            continue
        event["exact_target"] = {
            "sku": str(target.get("sku") or "")[:80],
            "item_ids": [str(value)[:40] for value in (target.get("item_ids") or [])[:100]],
            "matched": bool(match.get("exact")),
            "matched_by": str(match.get("matched_by") or "")[:60],
            "coverage_complete": bool(coverage.get("complete")),
            "stop_reason": str(coverage.get("stop_reason") or "")[:80],
            "pages_fetched": int(coverage.get("pages_fetched") or 0),
            "claims_scanned": int(coverage.get("claims_scanned") or 0),
            "orders_inspected": int(coverage.get("orders_inspected") or 0),
            "error": str(summary.get("error") or "")[:80],
        }
        break
    try:
        path = _assistant_path(client_id, "api_query_audit.jsonl")
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
        with API_QUERY_AUDIT_LOCK:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        logging.getLogger("codex_assistant.api_audit").exception("Falha ao registrar auditoria read-only")


def _assistant_api_error_code(result: Any) -> str:
    if not isinstance(result, dict):
        return "provider_unavailable"
    for item in result.get("summary") if isinstance(result.get("summary"), list) else []:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        code = str(summary.get("error") or "").strip().lower()
        if code:
            return code
    return str(result.get("error_code") or result.get("error") or "").strip().lower()


def _assistant_retryable_api_error(result: Any) -> bool:
    code = _assistant_api_error_code(result)
    return bool(
        code in {"rate_limited", "timeout", "provider_unavailable"}
        or re.fullmatch(r"http_5\d\d", code or "")
    )


def _assistant_non_retryable_auth_failure(warnings: Any, registry_results: Any) -> bool:
    values = [str(item or "") for item in warnings if str(item or "").strip()] if isinstance(warnings, list) else []
    for item in registry_results if isinstance(registry_results, list) else []:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        values.extend(str(summary.get(key) or "") for key in ("error", "message", "detail"))
    text = _assistant_texto_norm(" ".join(values))
    return bool(
        re.search(
            r"\b(token.*expir\w*|http 401|http 403|unauthorized|forbidden|nao autoriz\w*|sem permiss\w*|autentic\w*|credencial\w*)\b",
            text,
        )
    )


def _assistant_previous_contains_tool(previous_results: Any, tool_id: str) -> bool:
    for result in previous_results if isinstance(previous_results, list) else []:
        if not isinstance(result, dict):
            continue
        if str(result.get("tool_id") or "") == tool_id:
            return True
        for item in result.get("summary") if isinstance(result.get("summary"), list) else []:
            if isinstance(item, dict) and str(item.get("tool_id") or "") == tool_id:
                return True
    return False


def _assistant_context_page(screen_context: Any) -> str:
    if not isinstance(screen_context, dict):
        return "codex"
    page = (
        str(screen_context.get("modulo_atual") or "").strip()
        or str(screen_context.get("page") or "").strip()
        or str(screen_context.get("pathname") or "").strip().strip("/").split("/", 1)[0]
        or str(screen_context.get("title") or "").strip()
    )
    return page or "codex"


def _assistant_texto_norm(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _assistant_slug(value: str) -> str:
    text = _assistant_texto_norm(value)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _assistant_periodo_padrao(days: int = 30) -> tuple[str, str]:
    fim = date.today()
    inicio = fim - timedelta(days=max(1, int(days or 30)) - 1)
    return inicio.isoformat(), fim.isoformat()


def _assistant_call_ia_tool(func_name: str, *args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    try:
        from backend.services import ia as ia_service
        try:
            from backend.services import ia_tools_marketplaces, ia_tools_produtos, ia_tools_vendas

            fallback_logger = logging.getLogger("codex_assistant.ia_tools")
            for module in (ia_tools_marketplaces, ia_tools_produtos, ia_tools_vendas):
                if getattr(module, "logger", None) is None:
                    setattr(module, "logger", fallback_logger)
        except Exception:
            pass

        func = getattr(ia_service, func_name, None)
        if not callable(func):
            return None
        result = func(*args, **kwargs)
        return result if isinstance(result, dict) else None
    except Exception as exc:
        return {
            "function": func_name,
            "arguments": {},
            "result": {"error": str(exc)[:300], "read_only": True},
        }


def _assistant_tool_call_signature_error(raw: Any) -> bool:
    result = raw.get("result") if isinstance(raw, dict) and isinstance(raw.get("result"), dict) else {}
    error = _assistant_texto_norm(str(result.get("error") or ""))
    return bool(
        "unexpected keyword" in error
        or "argumento de palavra-chave inesperado" in error
        or "positional argument" in error
        or "argumentos posicionais" in error
    )


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


def _assistant_tool_input_schema(tool_id: str) -> dict[str, Any]:
    period_schema = {
        "data_inicio": "YYYY-MM-DD",
        "data_fim": "YYYY-MM-DD",
        "loja": "nome da loja/conta ou vazio para consolidado",
    }
    schemas = {
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
            "module": "modulo ou dominio opcional",
            "ids": "lista opcional de IDs estaveis jk:*",
            "source_type": "tipo de fonte opcional",
            "environment": "development|installed opcional",
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
    return schemas.get(tool_id, {})


def _assistant_tool_meta(tool_id: str) -> dict[str, Any]:
    for tool in CODEX_DATA_TOOLS:
        if str(tool.get("id") or "") == tool_id:
            return tool
    return {"id": tool_id, "module": "desconhecido", "description": "", "executor": "", "external": False, "fallbacks": [], "status": "consultando dados"}


_ASSISTANT_SOURCE_LABELS: dict[str, str] = {
    "sales_returns_query": "vendas e devolucoes do JK Sistema",
    "sales_ranking": "historico de vendas do JK Sistema",
    "sales_summary": "resumo de vendas do JK Sistema",
    "returns_summary": "devolucoes do JK Sistema",
    "stockout_forecast": "analise de ruptura de estoque",
    "stale_stock": "estoque parado do JK Sistema",
    "product_data": "cadastro de produtos do JK Sistema",
    "product_registry": "cadastro detalhado de produtos",
    "stock_data": "estoque interno do JK Sistema",
    "product_margin": "cadastro de custo, imposto e margem",
    "product_costs_and_margin": "cadastro de custo, imposto e margem",
    "product_image": "imagens de produto",
    "source_discovery": "lista de arquivos e bases disponiveis",
    "local_database_query": "bancos locais do JK Sistema",
    "local_csv_query": "planilhas e cadastros locais",
    "local_cache_query": "caches locais do JK Sistema",
    "sync_logs_query": "logs e estado de sincronizacao",
    "integrations_status": "status das integracoes cadastradas",
    "mercado_livre_readonly": "dados do Mercado Livre",
    "mercado_livre_listing": "anuncios do Mercado Livre",
    "mercado_livre_resource_query": "catalogo e consultas oficiais do Mercado Livre",
    "mercado_livre_visits": "visitas de anuncio do Mercado Livre",
    "mercado_livre_promotions": "promocoes do Mercado Livre",
    "mercado_livre_post_sale_detail": "conversa de pos-venda do Mercado Livre",
    "mercado_livre_orders": "pedidos e vendas do Mercado Livre",
    "mercado_livre_returns": "devolucoes da API do Mercado Livre",
    "mercado_livre_full_stock": "estoque Full atual da API do Mercado Livre",
    "questions_post_sale_query": "perguntas e pos-venda do Mercado Livre",
    "fiscal_local_query": "cadastro fiscal local",
    "bling_status": "status da integracao Bling",
    "bling_products": "produtos cadastrados na Bling",
    "bling_product": "produtos cadastrados na Bling",
    "bling_positive_stock_sku_count": "contagem de SKUs com estoque na Bling",
    "bling_stock_balances": "saldos de estoque na Bling",
    "bling_deposits": "depositos cadastrados na Bling",
    "bling_sales_orders": "pedidos de venda da Bling",
    "bling_sales_order_detail": "detalhes de pedido da Bling",
    "bling_fiscal_nfe": "notas fiscais na Bling",
    "bling_fiscal_nfe_detail": "detalhes de nota fiscal na Bling",
    "bling_fiscal_product": "dados fiscais do produto na Bling",
    "bling_operation_natures": "naturezas de operacao da Bling",
    "bling_lots": "lotes e validade na Bling",
    "bling_lot_movements": "movimentacoes de lote na Bling",
    "bling_finance_summary": "resumo financeiro da Bling",
    "bling_resource_query": "consulta read-only na Bling",
    "program_functions_catalog": "catalogo de funcoes do JK Sistema",
    "program_action_match": "catalogo de acoes aprovaveis",
    "capability_resolve": "catalogo de capacidades do Black Jhon",
    "operational_memory_query": "memoria operacional do Black Jhon",
    "context_hub_search": "Context Hub tecnico do JK Sistema",
    "operational_dispatcher": "leitores operacionais do JK Sistema",
    "get_integrations_status": "status das integracoes cadastradas",
    "get_stockout_forecast": "analise de ruptura de estoque",
    "get_days_without_sale_top": "estoque parado do JK Sistema",
    "detect_sales_anomalies": "analise de anomalias de vendas",
    "get_returns_by_period": "devolucoes do JK Sistema",
    "get_sales_by_period": "historico de vendas do JK Sistema",
    "_ia_tool_get_sales_by_period": "historico de vendas do JK Sistema",
    "_ia_tool_get_sales_quantity_by_period": "resumo de vendas do JK Sistema",
    "_ia_tool_get_returns_by_period": "devolucoes do JK Sistema",
    "_ia_tool_get_product_data": "cadastro de produtos do JK Sistema",
    "_ia_tool_get_product_registry_info": "cadastro detalhado de produtos",
    "_ia_tool_get_stock_data": "estoque interno do JK Sistema",
    "_ia_tool_get_product_margin": "cadastro de custo, imposto e margem",
    "_ia_tool_get_product_image": "imagens de produto",
    "_ia_tool_get_integrations_status": "status das integracoes cadastradas",
    "_ia_tool_get_stockout_forecast": "analise de ruptura de estoque",
    "_ia_tool_get_days_without_sale_top": "estoque parado do JK Sistema",
}


_ASSISTANT_SOURCE_FILE_LABELS: tuple[tuple[str, str], ...] = (
    ("estoque_historico", "historico de estoque do JK Sistema"),
    ("vendas_historico", "historico de vendas do JK Sistema"),
    ("cadastro_custos_lojas", "cadastro de custos por loja"),
    ("cadastro_produtos", "cadastro de produtos do JK Sistema"),
    ("lojas_config", "configuracao das lojas e integracoes"),
    ("perguntas", "perguntas e pos-venda do Mercado Livre"),
    ("pos_venda", "perguntas e pos-venda do Mercado Livre"),
    ("mercado_livre", "integracao Mercado Livre"),
    ("mercadolivre", "integracao Mercado Livre"),
    ("bling", "integracao Bling"),
    ("sync", "logs e estado de sincronizacao"),
    ("sincronizacao", "logs e estado de sincronizacao"),
    ("fiscal", "cadastro fiscal local"),
    ("imposto", "cadastro fiscal local"),
    ("favoritos", "favoritos e simulador do Mercado Livre"),
    ("full", "modulo Full"),
    ("cache", "caches locais do JK Sistema"),
)


def _assistant_human_tool_label(tool_id: Any) -> str:
    key = str(tool_id or "").strip()
    if not key:
        return "consulta do JK Sistema"
    if key in _ASSISTANT_SOURCE_LABELS:
        return _ASSISTANT_SOURCE_LABELS[key]
    short = key.rsplit(".", 1)[-1]
    if short in _ASSISTANT_SOURCE_LABELS:
        return _ASSISTANT_SOURCE_LABELS[short]
    meta = _assistant_tool_meta(short if short != key else key)
    description = str(meta.get("description") or "").strip()
    if description:
        return description[:120]
    return "consulta do JK Sistema"


def _assistant_human_source_label(source: Any, tool_id: Any = "") -> str:
    raw = str(source or "").strip()
    fallback = _assistant_human_tool_label(tool_id)
    if not raw:
        return fallback
    key = raw.rsplit(".", 1)[-1]
    if raw in _ASSISTANT_SOURCE_LABELS:
        return _ASSISTANT_SOURCE_LABELS[raw]
    if key in _ASSISTANT_SOURCE_LABELS:
        return _ASSISTANT_SOURCE_LABELS[key]
    norm = _assistant_texto_norm(raw).replace("\\", "/")
    for needle, label in _ASSISTANT_SOURCE_FILE_LABELS:
        if needle in norm:
            return label
    if re.search(r"\b(select|sqlite|\.db)\b", norm):
        return "bancos locais do JK Sistema"
    if ".csv" in norm:
        return "planilhas e cadastros locais"
    if ".json" in norm:
        return "arquivos locais do JK Sistema"
    if re.match(r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?$", raw, flags=re.I):
        return fallback
    return raw[:160]


def _assistant_human_source_list(sources: Any, tool_id: Any = "") -> list[str]:
    labels: list[str] = []
    if not isinstance(sources, list):
        sources = [sources] if sources else []
    for source in sources:
        if isinstance(source, dict):
            label = str(source.get("source_label") or source.get("function_label") or source.get("label") or "").strip()
            if not label:
                label = _assistant_human_source_label(
                    source.get("source") or source.get("function") or source.get("tool_id") or "",
                    source.get("tool_id") or tool_id,
                )
        else:
            label = _assistant_human_source_label(source, tool_id)
        if label and label not in labels:
            labels.append(label)
    return labels


def _assistant_human_fallback_list(tool_ids: Any) -> list[str]:
    labels: list[str] = []
    if not isinstance(tool_ids, list):
        return labels
    for tool_id in tool_ids:
        label = _assistant_human_tool_label(tool_id)
        if label and label not in labels:
            labels.append(label)
    return labels


def _assistant_source_display(src: Any) -> str:
    if isinstance(src, dict):
        return str(
            src.get("source_label")
            or src.get("function_label")
            or _assistant_human_source_label(src.get("source") or src.get("function") or src.get("tool_id") or "", src.get("tool_id") or "")
        ).strip()
    return _assistant_human_source_label(src)


def _assistant_humanize_source_text(value: Any) -> str:
    text = str(value if value is not None else "")
    if not text:
        return ""
    for key in sorted(_ASSISTANT_SOURCE_LABELS, key=len, reverse=True):
        label = _ASSISTANT_SOURCE_LABELS[key]
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", label, text)
    text = text.replace("ferramenta principal", "consulta principal")
    text = text.replace("fallbacks read-only", "consultas alternativas de leitura")
    text = text.replace("fallback read-only", "consulta alternativa de leitura")
    return text


def _assistant_resolve_period(client_id: str, message: str, screen_context: Any) -> tuple[str, str]:
    contexto = dict(screen_context) if isinstance(screen_context, dict) else {}
    try:
        from backend.services import ia as ia_service

        extractor = getattr(ia_service, "_ia_extrair_periodo_mensagem_vendas", None)
        if callable(extractor):
            data_inicio, data_fim = extractor(str(message or ""), contexto)
            if data_inicio and data_fim:
                return str(data_inicio), str(data_fim)
    except Exception:
        pass
    for start_key, end_key in (("data_inicio", "data_fim"), ("inicio", "fim"), ("start_date", "end_date")):
        data_inicio = str(contexto.get(start_key) or "").strip()
        data_fim = str(contexto.get(end_key) or "").strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}$", data_inicio) and re.match(r"^\d{4}-\d{2}-\d{2}$", data_fim):
            return data_inicio, data_fim
    text = _assistant_texto_norm(message)
    if (
        re.search(r"\b(relatorio|resumo)\b", text)
        and re.search(r"\b(do dia|diario|diaria|de hoje|hoje)\b", text)
    ):
        today = datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()
        return today, today
    match_days = re.search(r"\bultimos?\s+(\d{1,3})\s+dias?\b", text)
    if not match_days:
        match_days = re.search(r"\b(\d{1,3})\s+dias?\b", text)
    if match_days:
        return _assistant_periodo_padrao(max(1, min(int(match_days.group(1)), 365)))
    return _assistant_periodo_padrao(30)


def _assistant_previous_period(data_inicio: str, data_fim: str) -> tuple[str, str]:
    try:
        ini = date.fromisoformat(data_inicio)
        fim = date.fromisoformat(data_fim)
        days = max(1, (fim - ini).days + 1)
        prev_fim = ini - timedelta(days=1)
        prev_ini = prev_fim - timedelta(days=days - 1)
        return prev_ini.isoformat(), prev_fim.isoformat()
    except Exception:
        fim = date.today() - timedelta(days=30)
        ini = fim - timedelta(days=29)
        return ini.isoformat(), fim.isoformat()


def _assistant_resolve_loja(client_id: str, message: str, screen_context: Any) -> str:
    contexto = dict(screen_context) if isinstance(screen_context, dict) else {}
    try:
        from backend.services import ia as ia_service

        resolver = getattr(ia_service, "_ia_resolver_loja_mensagem_vendas", None)
        if callable(resolver):
            loja = resolver(str(client_id or ""), str(message or ""), contexto)
            if loja:
                return str(loja)
    except Exception:
        pass
    for key in ("loja", "conta", "store", "store_name", "loja_atual", "contexto_loja"):
        loja = str(contexto.get(key) or "").strip()
        if loja and loja not in {"__todas", "Todas as lojas"}:
            return loja
    text = _assistant_texto_norm(message)
    try:
        from backend.services.ia_context import get_tenant_path

        tenant_path = get_tenant_path(client_id)
    except Exception:
        tenant_path = os.path.join(_assistant_info_base(), _assistant_safe_id(client_id))
    if os.path.exists(tenant_path):
        for name in os.listdir(tenant_path):
            if not (name.startswith("vendas_historico_") and name.endswith(".db")):
                continue
            slug = name[len("vendas_historico_"):-3]
            loja = slug.replace("_", " ").strip()
            loja_norm = _assistant_texto_norm(loja)
            if loja_norm and loja_norm in text:
                return loja
    return ""


def _assistant_candidate_info_roots() -> list[str]:
    roots: list[str] = []

    def add(path: Any) -> None:
        text = str(path or "").strip()
        if not text:
            return
        text = os.path.abspath(os.path.expandvars(os.path.expanduser(text)))
        if text not in roots and os.path.isdir(text):
            roots.append(text)

    for env_name in ("JK_CODEX_INFO_FALLBACK_DIRS", "JK_INFO_DIR"):
        value = os.getenv(env_name) or ""
        parts = value.split(os.pathsep) if env_name.endswith("DIRS") else [value]
        for part in parts:
            add(part)

    add(_assistant_info_base())
    add(os.path.join(os.getcwd(), "info"))
    try:
        for parent in Path(__file__).resolve().parents:
            add(parent / "info")
    except Exception:
        pass

    appdata = os.getenv("APPDATA") or ""
    if appdata:
        add(os.path.join(appdata, "JK Sistema Cliente", "local_app", "info"))
        add(os.path.join(appdata, "JK Sistema Cliente", "info"))

    return roots


def _assistant_sales_db_candidates(client_id: str, loja: Optional[str]) -> list[tuple[str, str]]:
    client_safe = _assistant_safe_id(client_id)
    loja_slug = _assistant_slug(loja or "")
    loja_norm = _assistant_texto_norm(loja or "")
    roots = _assistant_candidate_info_roots()

    if loja_slug:
        exact_name = f"vendas_historico_{loja_slug}.db"
        for root in roots:
            path = os.path.join(root, client_safe, exact_name)
            if os.path.exists(path):
                return [(path, loja_slug.replace("_", " "))]

    for root in roots:
        tenant_path = os.path.join(root, client_safe)
        if not os.path.isdir(tenant_path):
            continue
        found: list[tuple[str, str]] = []
        for name in sorted(os.listdir(tenant_path)):
            if name == "vendas_historico.db":
                if not loja_slug:
                    found.append((os.path.join(tenant_path, name), "geral"))
                continue
            if not name.startswith("vendas_historico_") or not name.endswith(".db") or ".backup_" in name:
                continue
            slug = name[len("vendas_historico_"):-3]
            store_name = slug.replace("_", " ").strip() or "loja"
            slug_norm = _assistant_texto_norm(store_name)
            if loja_slug and loja_norm and loja_norm not in slug_norm and slug_norm not in loja_norm:
                continue
            found.append((os.path.join(tenant_path, name), store_name))
        if found:
            return found
    return []


def _assistant_sales_db_candidates_query(client_id: str, loja: Optional[str]) -> list[tuple[str, str]]:
    candidates = _assistant_sales_db_candidates(client_id, loja)
    if loja and str(loja).strip() and str(loja).strip() not in {"__todas", "Todas as lojas"}:
        return candidates
    store_candidates = [
        (path, store)
        for path, store in candidates
        if os.path.basename(path) != "vendas_historico.db"
    ]
    return store_candidates or candidates


def _assistant_store_label(store_name: str) -> str:
    raw = str(store_name or "").strip()
    norm = _assistant_texto_norm(raw)
    known = {
        "jk pecas": "JK Pecas",
        "uai mineirinho": "Uai Mineirinho",
        "carlos jose": "Carlos Jose",
        "deckas": "Deckas",
        "dona nina": "Dona Nina",
        "multimarcas": "Multimarcas",
        "imports": "Imports",
        "geral": "Geral",
    }
    if norm in known:
        return known[norm]
    return raw.replace("_", " ").strip().title() or "Loja"


def _assistant_calendar_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _assistant_bool_arg(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return bool(default)
    normalized = _assistant_texto_norm(str(value))
    if normalized in {"1", "true", "sim", "yes", "on", "com", "incluir"}:
        return True
    if normalized in {"0", "false", "nao", "no", "off", "sem", "excluir"}:
        return False
    return bool(default)


def _assistant_force_refresh_requested(args: Any, message: str) -> bool:
    args = args if isinstance(args, dict) else {}
    if _assistant_bool_arg(args.get("force_refresh", args.get("atualizar_cache")), False):
        return True
    text = _assistant_texto_norm(message)
    return bool(re.search(r"\b(atualize agora|atualizar agora|consulte agora|sem cache|ignorar cache|force refresh)\b", text))


def _assistant_normalize_api_status(value: Any, default: str = "") -> str:
    raw_values = value if isinstance(value, (list, tuple, set)) else re.split(r"[,;|\s]+", str(value or ""))
    statuses: list[str] = []
    for item in raw_values:
        status = re.sub(r"[^a-z0-9_-]+", "", _assistant_texto_norm(str(item or "")).replace(" ", "_"))
        if status and status not in statuses:
            statuses.append(status[:40])
    return ",".join(statuses[:12]) or str(default or "")


def _assistant_normalize_identifier(value: Any, limit: int = 60) -> str:
    raw = str(value or "").strip()
    match = re.search(r"[A-Za-z0-9][A-Za-z0-9._-]*", raw)
    return str(match.group(0) if match else "")[:limit]


def _assistant_normalize_ml_item_id(value: Any) -> str:
    raw = str(value or "").strip().upper()
    match = re.search(r"\bMLB[\s_-]?(\d{6,})\b", raw)
    if match:
        return "MLB" + match.group(1)
    digits = re.sub(r"\D+", "", raw)
    return ("MLB" + digits) if len(digits) >= 6 and re.fullmatch(r"[\d\s_-]+", raw or "") else ""


def _assistant_normalize_sku(value: Any) -> str:
    raw = str(value or "").strip()
    if _assistant_calendar_date(raw):
        return ""
    sku = raw.upper()
    sku = re.sub(r"\s+", "", sku)
    return sku[:80]


def _assistant_extract_sku_filter(message: str, screen_context: Any = None) -> str:
    if isinstance(screen_context, dict):
        for key in ("sku", "selected_sku", "sku_atual", "filtro_sku", "produto_sku"):
            sku_ctx = _assistant_normalize_sku(screen_context.get(key))
            if sku_ctx:
                return sku_ctx
    text = str(message or "")
    patterns = (
        r"\bsku\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{1,79})",
        r"\bSKU\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{1,79})",
    )
    blocked = {
        "PERIODO", "LOJA", "LOJAS", "CONTA", "CONTAS", "TODAS", "VENDA", "VENDAS",
        "DEVOLUCAO", "DEVOLUCOES", "ULTIMA", "ULTIMO", "MAIS", "RECENTE",
    }
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        sku = _assistant_normalize_sku(match.group(1))
        if sku and sku not in blocked:
            return sku
    if _assistant_latest_ml_event_kind(message):
        normalized = _assistant_texto_norm(message)
        inferred = re.findall(
            r"\b(?:venda|pedido|devolucao|reembolso|estorno)\b[^.?!]{0,80}?\b(?:do|da|de)\s+(?:sku\s*)?([a-z0-9][a-z0-9._/-]{1,79})",
            normalized,
        )
        for candidate in reversed(inferred):
            sku = _assistant_normalize_sku(candidate)
            if sku and sku not in blocked:
                return sku
    return ""


def _assistant_product_refs_from_context(*values: Any) -> dict[str, list[str]]:
    refs: dict[str, list[str]] = {"skus": [], "bling_ids": []}
    seen_nodes = 0

    def add(kind: str, value: Any) -> None:
        raw_values = value if isinstance(value, (list, tuple, set)) else [value]
        for raw in raw_values:
            text = str(raw or "").strip().strip(".,;:)")
            if not text:
                continue
            if kind == "skus":
                text = _assistant_normalize_sku(text)
                if not text or text in {"SKU", "PRODUTO", "SALDO", "ESTOQUE", "BLING"}:
                    continue
            else:
                if not re.fullmatch(r"\d{3,}", text):
                    continue
            if text not in refs[kind]:
                refs[kind].append(text)
            if len(refs[kind]) >= 20:
                return

    def scan_text(text: str) -> None:
        raw = str(text or "")
        for match in re.finditer(r"\bsku\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{1,79})", raw, flags=re.I):
            add("skus", match.group(1))
        for match in re.finditer(
            r"\b(?:id_bling|id\s*bling|bling\s*id|id_produto_bling|id\s*produto\s*bling|idsProdutos\[\]|ids_produtos)\b[^0-9]{0,30}((?:\d{3,}[\s,;/|]*){1,12})",
            raw,
            flags=re.I,
        ):
            add("bling_ids", re.findall(r"\d{3,}", match.group(1) or ""))

    def visit(obj: Any, depth: int = 0) -> None:
        nonlocal seen_nodes
        if seen_nodes >= 600 or depth > 6:
            return
        seen_nodes += 1
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_norm = _assistant_texto_norm(str(key or "")).replace(" ", "_").replace("-", "_")
                if key_norm in {"sku", "selected_sku", "sku_atual", "filtro_sku", "produto_sku", "seller_sku", "canonical_sku", "codigo"}:
                    add("skus", value)
                if "bling" in key_norm and "id" in key_norm:
                    if isinstance(value, str):
                        add("bling_ids", re.findall(r"\d{3,}", value))
                    else:
                        add("bling_ids", value)
                if key_norm in {"idsprodutos[]", "ids_produtos", "id_produto_bling", "id_bling"}:
                    if isinstance(value, str):
                        add("bling_ids", re.findall(r"\d{3,}", value))
                    else:
                        add("bling_ids", value)
                if isinstance(value, str):
                    scan_text(value)
                if isinstance(value, (dict, list, tuple)):
                    visit(value, depth + 1)
        elif isinstance(obj, (list, tuple)):
            for item in obj[:80]:
                visit(item, depth + 1)
        elif isinstance(obj, str):
            scan_text(obj)

    for value in values:
        visit(value)
    return refs


def _assistant_bling_message_with_refs(
    message: str,
    plan: dict[str, Any],
    screen_context: Any,
    existing_registry_results: Optional[list[dict[str, Any]]] = None,
) -> str:
    refs = _assistant_product_refs_from_context(screen_context, existing_registry_results or [])
    plan_sku = _assistant_normalize_sku(plan.get("sku"))
    if plan_sku and plan_sku not in refs["skus"]:
        refs["skus"].insert(0, plan_sku)
    if not refs["skus"] and not refs["bling_ids"]:
        return message
    lines = [str(message or "").strip(), "", "Contexto resolvido para consulta Bling read-only:"]
    if refs["skus"]:
        lines.append("SKU " + ", ".join(refs["skus"][:5]))
    if refs["bling_ids"]:
        lines.append("id_bling " + ", ".join(refs["bling_ids"][:12]))
    loja = str(plan.get("loja") or "").strip()
    if loja:
        lines.append("loja " + loja)
    return "\n".join(line for line in lines if line is not None).strip()


def _assistant_wants_store_breakdown(message: str, loja: Optional[str]) -> bool:
    text = _assistant_texto_norm(message)
    if re.search(r"\b(todas as lojas|todas as contas|por loja|por conta|cada loja|cada conta|loja a loja|conta a conta|separado por loja|separada por loja|separando por loja|individualmente)\b", text):
        return True
    if not loja and re.search(r"\b(lojas|contas)\b", text) and re.search(r"\b(vendas?|devolucoes?|devolvidos?)\b", text):
        return True
    return False


def _assistant_sr_empty_store(store: str, source_path: str) -> dict[str, Any]:
    return {
        "loja": store,
        "source_path": source_path,
        "vendas_quantidade": 0.0,
        "vendas_valor": 0.0,
        "vendas_pedidos": 0,
        "vendas_registros": 0,
        "devolucoes_quantidade": 0.0,
        "devolucoes_valor": 0.0,
        "devolucoes_registros": 0,
        "devolucoes_fonte": "",
    }


def _assistant_sr_empty_sku(sku: str) -> dict[str, Any]:
    return {
        "sku": sku or "SEM SKU",
        "produto": "",
        "quantidade_vendida": 0.0,
        "valor_vendido": 0.0,
        "pedidos": 0,
        "quantidade_devolvida": 0.0,
        "valor_devolvido": 0.0,
        "lojas": set(),
        "_pedidos": set(),
    }


def _assistant_sr_add_sku(aggregates: dict[str, dict[str, Any]], sku_raw: Any, produto: Any, loja: str, kind: str, quantidade: Any, valor: Any, pedido_key: str = "") -> None:
    sku = _assistant_normalize_sku(sku_raw) or "SEM SKU"
    item = aggregates.setdefault(sku, _assistant_sr_empty_sku(sku))
    produto_txt = str(produto or "").strip()
    if produto_txt and not item.get("produto"):
        item["produto"] = produto_txt[:220]
    if loja:
        item["lojas"].add(loja)
    qtd = _assistant_float(quantidade)
    val = _assistant_float(valor)
    if kind == "venda":
        item["quantidade_vendida"] += qtd
        item["valor_vendido"] += val
        if pedido_key:
            item["_pedidos"].add(pedido_key)
    else:
        item["quantidade_devolvida"] += qtd
        item["valor_devolvido"] += val


def _assistant_sr_finalize_sku_rows(aggregates: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in aggregates.values():
        vendas_qtd = float(item.get("quantidade_vendida") or 0)
        vendas_valor = float(item.get("valor_vendido") or 0)
        devol_qtd = float(item.get("quantidade_devolvida") or 0)
        devol_valor = float(item.get("valor_devolvido") or 0)
        rows.append(
            {
                "sku": item.get("sku") or "SEM SKU",
                "produto": item.get("produto") or "",
                "quantidade_vendida": vendas_qtd,
                "valor_vendido": vendas_valor,
                "pedidos": len(item.get("_pedidos") or []),
                "quantidade_devolvida": devol_qtd,
                "valor_devolvido": devol_valor,
                "taxa_devolucao_quantidade_percentual": (devol_qtd / vendas_qtd * 100.0) if vendas_qtd > 0 else 0.0,
                "taxa_devolucao_valor_percentual": (devol_valor / vendas_valor * 100.0) if vendas_valor > 0 else 0.0,
                "lojas": sorted(item.get("lojas") or []),
            }
        )
    return sorted(rows, key=lambda row: (float(row.get("valor_devolvido") or 0), float(row.get("quantidade_devolvida") or 0), float(row.get("valor_vendido") or 0)), reverse=True)


def _assistant_sr_compact_record(record: dict[str, Any], kind: str) -> str:
    if kind == "venda":
        return (
            f"{record.get('data') or '-'} | {record.get('loja') or '-'} | SKU {record.get('sku') or '-'} | "
            f"qtd {record.get('quantidade') or 0} | valor {_assistant_money(record.get('valor') or 0)} | "
            f"pedido {record.get('numero') or record.get('id_unico') or '-'} | {record.get('produto') or ''}"
        )
    return (
        f"{record.get('data') or '-'} | {record.get('loja') or '-'} | SKU {record.get('sku') or '-'} | "
        f"qtd {record.get('quantidade') or 0} | valor {_assistant_money(record.get('valor_total') or 0)} | "
        f"nota {record.get('numero_nota') or record.get('numero') or '-'} | {record.get('produto') or ''}"
    )


def _assistant_sales_returns_context_text(result: dict[str, Any]) -> str:
    totals = result.get("totais") if isinstance(result.get("totais"), dict) else {}
    lines = [
        "Consulta unificada de vendas e devolucoes:",
        f"Periodo: {result.get('data_inicio') or '-'} a {result.get('data_fim') or '-'}",
        f"Loja/conta: {result.get('loja') or 'todas'} | SKU: {result.get('sku') or 'todos'}",
        (
            "Totais: "
            f"vendas qtd {totals.get('quantidade_vendida_total') or 0}, "
            f"vendas valor {_assistant_money(totals.get('valor_vendido_total') or 0)}, "
            f"pedidos {totals.get('pedidos_total') or 0}, "
            f"devolucoes qtd {totals.get('quantidade_devolvida_total') or 0}, "
            f"devolucoes valor {_assistant_money(totals.get('valor_devolvido_total') or 0)}, "
            f"taxa qtd {totals.get('taxa_devolucao_quantidade_percentual') or 0:.2f}%, "
            f"taxa valor {totals.get('taxa_devolucao_valor_percentual') or 0:.2f}%."
        ),
        "",
        "Por loja:",
    ]
    for row in result.get("por_loja") or []:
        lines.append(
            f"- {row.get('loja') or '-'}: vendas qtd {row.get('vendas_quantidade') or 0}, "
            f"valor {_assistant_money(row.get('vendas_valor') or 0)}, pedidos {row.get('vendas_pedidos') or 0}; "
            f"devolucoes qtd {row.get('devolucoes_quantidade') or 0}, valor {_assistant_money(row.get('devolucoes_valor') or 0)} "
            f"({row.get('devolucoes_fonte') or 'sem fonte'})."
        )
    lines.append("")
    lines.append("Por SKU:")
    for row in result.get("por_sku") or []:
        lines.append(
            f"- SKU {row.get('sku') or '-'} | {row.get('produto') or ''} | "
            f"vendido qtd {row.get('quantidade_vendida') or 0}, valor {_assistant_money(row.get('valor_vendido') or 0)}, pedidos {row.get('pedidos') or 0}; "
            f"devolvido qtd {row.get('quantidade_devolvida') or 0}, valor {_assistant_money(row.get('valor_devolvido') or 0)}; "
            f"lojas {', '.join(row.get('lojas') or []) or '-'}."
        )
    lines.append("")
    lines.append("Vendas:")
    for record in result.get("vendas") or []:
        lines.append("- " + _assistant_sr_compact_record(record, "venda"))
    lines.append("")
    lines.append("Devolucoes:")
    for record in result.get("devolucoes") or []:
        lines.append("- " + _assistant_sr_compact_record(record, "devolucao"))
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    if warnings:
        lines.append("")
        lines.append("Avisos:")
        for warning in warnings:
            lines.append("- " + str(warning))
    return "\n".join(lines)[:CODEX_SALES_RETURNS_CONTEXT_CHAR_LIMIT]


def _assistant_sales_returns_query(
    client_id: str,
    data_inicio: str,
    data_fim: str,
    loja: Optional[str] = None,
    sku: Optional[str] = None,
    separar_por_loja: bool = False,
    incluir_registros: bool = True,
) -> Optional[dict[str, Any]]:
    if not data_inicio or not data_fim:
        return None

    loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
    sku_ref = _assistant_normalize_sku(sku)
    candidates = _assistant_sales_db_candidates_query(client_id, loja_filtro)
    checked = [path for path, _ in candidates]
    warnings: list[str] = []
    if not candidates:
        warnings.append("Nenhum banco vendas_historico*.db foi encontrado nas pastas de dados conhecidas.")

    vendas_records: list[dict[str, Any]] = []
    devol_records: list[dict[str, Any]] = []
    por_loja: list[dict[str, Any]] = []
    sku_aggregates: dict[str, dict[str, Any]] = {}
    pedidos_total_keys: set[str] = set()
    totals = {
        "quantidade_vendida_total": 0.0,
        "valor_vendido_total": 0.0,
        "pedidos_total": 0,
        "quantidade_devolvida_total": 0.0,
        "valor_devolvido_total": 0.0,
        "taxa_devolucao_quantidade_percentual": 0.0,
        "taxa_devolucao_valor_percentual": 0.0,
        "vendas_registros_total": 0,
        "devolucoes_registros_total": 0,
        "total_registros": 0,
    }

    for db_path, store_name in candidates:
        store = _assistant_store_label(store_name)
        store_summary = _assistant_sr_empty_store(store, db_path)
        conn = None
        try:
            conn = sqlite3.connect(db_path, timeout=8)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            tables = {str(row[0]) for row in cur.execute("select name from sqlite_master where type='table'").fetchall()}
            if "vendas" not in tables:
                warnings.append(f"{os.path.basename(db_path)}: tabela vendas ausente.")
                continue

            sku_sql = " AND UPPER(TRIM(COALESCE(sku, ''))) = ?" if sku_ref else ""
            sales_params: list[Any] = [data_inicio, data_fim]
            if sku_ref:
                sales_params.append(sku_ref)
            sales_rows = cur.execute(
                f"""
                SELECT
                    id_unico,
                    substr(coalesce(data, ''), 1, 10) AS data,
                    loja_conta,
                    canal,
                    numero,
                    situacao,
                    sku,
                    produto,
                    quantidade,
                    valor,
                    numero_nf,
                    comprador,
                    unidade_negocio,
                    loja_id,
                    unidade_id,
                    intermediador_nome,
                    intermediador_cnpj
                FROM vendas
                WHERE substr(coalesce(data, ''), 1, 10) BETWEEN ? AND ?
                  AND coalesce(devolucao, 0) = 0
                  AND lower(coalesce(situacao, '')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                  {sku_sql}
                ORDER BY date(data) DESC, numero DESC, sku
                """,
                sales_params,
            ).fetchall()
            for row in sales_rows or []:
                sku_row = _assistant_normalize_sku(row["sku"]) or "SEM SKU"
                pedido_ref = str(row["numero"] or row["id_unico"] or "").strip()
                pedido_key = f"{store}|{pedido_ref or row['id_unico'] or len(vendas_records)}"
                pedido_store_key = pedido_key
                pedidos_total_keys.add(pedido_key)
                qtd = _assistant_float(row["quantidade"])
                valor = _assistant_float(row["valor"])
                store_summary["vendas_quantidade"] += qtd
                store_summary["vendas_valor"] += valor
                store_summary["vendas_registros"] += 1
                _assistant_sr_add_sku(sku_aggregates, sku_row, row["produto"], store, "venda", qtd, valor, pedido_store_key)
                if incluir_registros:
                    vendas_records.append(
                        {
                            "loja": store,
                            "data": str(row["data"] or ""),
                            "id_unico": str(row["id_unico"] or ""),
                            "numero": str(row["numero"] or ""),
                            "numero_nf": str(row["numero_nf"] or ""),
                            "sku": sku_row,
                            "produto": str(row["produto"] or ""),
                            "quantidade": qtd,
                            "valor": valor,
                            "situacao": str(row["situacao"] or ""),
                            "canal": str(row["canal"] or ""),
                            "unidade_negocio": str(row["unidade_negocio"] or ""),
                            "loja_conta": str(row["loja_conta"] or ""),
                        }
                    )

            store_summary["vendas_pedidos"] = len({
                str(row["numero"] or row["id_unico"] or idx)
                for idx, row in enumerate(sales_rows or [])
            })

            notes_table_exists = "notas_entrada_itens" in tables
            if notes_table_exists:
                store_summary["devolucoes_fonte"] = "notas_entrada_itens"
                returns_params: list[Any] = [data_inicio, data_fim]
                if sku_ref:
                    returns_params.append(sku_ref)
                returns_rows = cur.execute(
                    f"""
                    SELECT
                        numero_nota,
                        origem_codigo,
                        substr(coalesce(data_emissao, ''), 1, 10) AS data,
                        sku,
                        descricao,
                        quantidade,
                        valor_unitario,
                        valor_total,
                        fornecedor,
                        unidade_negocio_virtual,
                        unidade_negocio,
                        natureza_operacao,
                        finalidade_operacao,
                        loja_conta
                    FROM notas_entrada_itens
                    WHERE devolucao = 1
                      AND date(data_emissao) BETWEEN ? AND ?
                      AND trim(coalesce(sku, '')) != ''
                      {sku_sql}
                    ORDER BY date(data_emissao) DESC, numero_nota DESC, sku
                    """,
                    returns_params,
                ).fetchall()
                for row in returns_rows or []:
                    sku_row = _assistant_normalize_sku(row["sku"]) or "SEM SKU"
                    qtd = _assistant_float(row["quantidade"])
                    valor = _assistant_float(row["valor_total"])
                    store_summary["devolucoes_quantidade"] += qtd
                    store_summary["devolucoes_valor"] += valor
                    store_summary["devolucoes_registros"] += 1
                    _assistant_sr_add_sku(sku_aggregates, sku_row, row["descricao"], store, "devolucao", qtd, valor)
                    if incluir_registros:
                        devol_records.append(
                            {
                                "loja": store,
                                "data": str(row["data"] or ""),
                                "numero_nota": str(row["numero_nota"] or ""),
                                "origem_codigo": str(row["origem_codigo"] or ""),
                                "sku": sku_row,
                                "produto": str(row["descricao"] or ""),
                                "quantidade": qtd,
                                "valor_unitario": _assistant_float(row["valor_unitario"]),
                                "valor_total": valor,
                                "fornecedor": str(row["fornecedor"] or ""),
                                "unidade_negocio_virtual": str(row["unidade_negocio_virtual"] or ""),
                                "unidade_negocio": str(row["unidade_negocio"] or ""),
                                "natureza_operacao": str(row["natureza_operacao"] or ""),
                                "finalidade_operacao": str(row["finalidade_operacao"] or ""),
                                "loja_conta": str(row["loja_conta"] or ""),
                                "fonte": "notas_entrada_itens",
                            }
                        )
            else:
                store_summary["devolucoes_fonte"] = "vendas.devolucao"
                warnings.append(f"{os.path.basename(db_path)}: notas_entrada_itens ausente; usei fallback vendas.devolucao=1.")
                returns_params = [data_inicio, data_fim]
                if sku_ref:
                    returns_params.append(sku_ref)
                fallback_rows = cur.execute(
                    f"""
                    SELECT
                        id_unico,
                        substr(coalesce(data, ''), 1, 10) AS data,
                        loja_conta,
                        canal,
                        numero,
                        situacao,
                        sku,
                        produto,
                        quantidade,
                        valor,
                        numero_nf,
                        comprador,
                        unidade_negocio
                    FROM vendas
                    WHERE substr(coalesce(data, ''), 1, 10) BETWEEN ? AND ?
                      AND coalesce(devolucao, 0) = 1
                      {sku_sql}
                    ORDER BY date(data) DESC, numero DESC, sku
                    """,
                    returns_params,
                ).fetchall()
                for row in fallback_rows or []:
                    sku_row = _assistant_normalize_sku(row["sku"]) or "SEM SKU"
                    qtd = _assistant_float(row["quantidade"])
                    valor = _assistant_float(row["valor"])
                    store_summary["devolucoes_quantidade"] += qtd
                    store_summary["devolucoes_valor"] += valor
                    store_summary["devolucoes_registros"] += 1
                    _assistant_sr_add_sku(sku_aggregates, sku_row, row["produto"], store, "devolucao", qtd, valor)
                    if incluir_registros:
                        devol_records.append(
                            {
                                "loja": store,
                                "data": str(row["data"] or ""),
                                "id_unico": str(row["id_unico"] or ""),
                                "numero": str(row["numero"] or ""),
                                "numero_nf": str(row["numero_nf"] or ""),
                                "sku": sku_row,
                                "produto": str(row["produto"] or ""),
                                "quantidade": qtd,
                                "valor_total": valor,
                                "situacao": str(row["situacao"] or ""),
                                "canal": str(row["canal"] or ""),
                                "unidade_negocio": str(row["unidade_negocio"] or ""),
                                "loja_conta": str(row["loja_conta"] or ""),
                                "fonte": "vendas.devolucao",
                            }
                        )

            por_loja.append(store_summary)
            totals["quantidade_vendida_total"] += float(store_summary["vendas_quantidade"] or 0)
            totals["valor_vendido_total"] += float(store_summary["vendas_valor"] or 0)
            totals["quantidade_devolvida_total"] += float(store_summary["devolucoes_quantidade"] or 0)
            totals["valor_devolvido_total"] += float(store_summary["devolucoes_valor"] or 0)
        except Exception as exc:
            warnings.append(f"{os.path.basename(db_path)}: {str(exc)[:220]}")
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

    totals["pedidos_total"] = len(pedidos_total_keys)
    totals["vendas_registros_total"] = len(vendas_records) if incluir_registros else sum(int(row.get("vendas_registros") or 0) for row in por_loja)
    totals["devolucoes_registros_total"] = len(devol_records) if incluir_registros else sum(int(row.get("devolucoes_registros") or 0) for row in por_loja)
    totals["total_registros"] = int(totals["vendas_registros_total"] or 0) + int(totals["devolucoes_registros_total"] or 0)
    vendas_qtd = float(totals["quantidade_vendida_total"] or 0)
    vendas_valor = float(totals["valor_vendido_total"] or 0)
    devol_qtd = float(totals["quantidade_devolvida_total"] or 0)
    devol_valor = float(totals["valor_devolvido_total"] or 0)
    totals["taxa_devolucao_quantidade_percentual"] = (devol_qtd / vendas_qtd * 100.0) if vendas_qtd > 0 else 0.0
    totals["taxa_devolucao_valor_percentual"] = (devol_valor / vendas_valor * 100.0) if vendas_valor > 0 else 0.0

    result = {
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "loja": loja_filtro or "",
        "sku": sku_ref,
        "separar_por_loja": bool(separar_por_loja),
        "incluir_registros": bool(incluir_registros),
        "totais": totals,
        "por_loja": por_loja if separar_por_loja or not loja_filtro else por_loja[:1],
        "por_sku": _assistant_sr_finalize_sku_rows(sku_aggregates),
        "vendas": vendas_records if incluir_registros else [],
        "devolucoes": devol_records if incluir_registros else [],
        "source_paths": checked,
        "warnings": warnings[:20],
        "fallback_used": any("fallback" in warning.lower() for warning in warnings),
        "total_registros": totals["total_registros"],
    }
    result["context_text"] = _assistant_sales_returns_context_text(result)
    return {
        "function": "sales_returns_query",
        "arguments": {
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "loja": loja_filtro or "",
            "sku": sku_ref,
            "separar_por_loja": bool(separar_por_loja),
            "incluir_registros": bool(incluir_registros),
        },
        "result": result,
    }


def _assistant_sr_raw_result(raw: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    result = raw.get("result")
    return result if isinstance(result, dict) else {}


def _assistant_sr_as_sales_by_period(raw: Optional[dict[str, Any]], limite: int = DEFAULT_RANKING_LIMIT) -> Optional[dict[str, Any]]:
    result = _assistant_sr_raw_result(raw)
    if not result:
        return None
    totals = result.get("totais") if isinstance(result.get("totais"), dict) else {}
    limit_safe = max(1, min(int(limite or DEFAULT_RANKING_LIMIT), 500))
    top_skus = [
        {
            "sku": item.get("sku") or "SEM SKU",
            "nome": item.get("produto") or "",
            "qtd": item.get("quantidade_vendida") or 0,
            "valor": item.get("valor_vendido") or 0,
            "pedidos": item.get("pedidos") or 0,
            "lojas": item.get("lojas") or [],
        }
        for item in (result.get("por_sku") or [])
        if isinstance(item, dict) and (_assistant_float(item.get("quantidade_vendida")) > 0 or _assistant_float(item.get("valor_vendido")) > 0)
    ]
    top_skus = sorted(top_skus, key=lambda item: (_assistant_float(item.get("qtd")), _assistant_float(item.get("valor"))), reverse=True)[:limit_safe]
    return {
        "function": "sales_returns_query_sales_by_period",
        "arguments": raw.get("arguments") if isinstance(raw, dict) else {},
        "result": {
            "data_inicio": result.get("data_inicio") or "",
            "data_fim": result.get("data_fim") or "",
            "loja": result.get("loja") or "",
            "quantidade_total": totals.get("quantidade_vendida_total") or 0,
            "valor_total": totals.get("valor_vendido_total") or 0,
            "pedidos_total": totals.get("pedidos_total") or 0,
            "top_skus": top_skus,
            "source_paths": result.get("source_paths") or [],
            "fallback_used": True,
        },
    }


def _assistant_sr_as_sales_quantity(raw: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    result = _assistant_sr_raw_result(raw)
    if not result:
        return None
    totals = result.get("totais") if isinstance(result.get("totais"), dict) else {}
    return {
        "function": "sales_returns_query_sales_quantity_by_period",
        "arguments": raw.get("arguments") if isinstance(raw, dict) else {},
        "result": {
            "data_inicio": result.get("data_inicio") or "",
            "data_fim": result.get("data_fim") or "",
            "loja": result.get("loja") or "",
            "quantidade_vendida_total": totals.get("quantidade_vendida_total") or 0,
            "quantidade_total": totals.get("quantidade_vendida_total") or 0,
            "valor_total": totals.get("valor_vendido_total") or 0,
            "pedidos_total": totals.get("pedidos_total") or 0,
            "source_paths": result.get("source_paths") or [],
            "fallback_used": True,
        },
    }


def _assistant_sr_as_returns_by_period(raw: Optional[dict[str, Any]], limite: int = 50) -> Optional[dict[str, Any]]:
    result = _assistant_sr_raw_result(raw)
    if not result:
        return None
    totals = result.get("totais") if isinstance(result.get("totais"), dict) else {}
    limit_safe = max(1, min(int(limite or 50), 500))
    top_skus = [
        {
            "sku": item.get("sku") or "SEM SKU",
            "produto": item.get("produto") or "",
            "quantidade_devolvida": item.get("quantidade_devolvida") or 0,
            "valor_devolvido": item.get("valor_devolvido") or 0,
            "lojas": item.get("lojas") or [],
        }
        for item in (result.get("por_sku") or [])
        if isinstance(item, dict) and (_assistant_float(item.get("quantidade_devolvida")) > 0 or _assistant_float(item.get("valor_devolvido")) > 0)
    ]
    top_skus = sorted(top_skus, key=lambda item: (_assistant_float(item.get("valor_devolvido")), _assistant_float(item.get("quantidade_devolvida"))), reverse=True)[:limit_safe]
    return {
        "function": "sales_returns_query_returns_by_period",
        "arguments": raw.get("arguments") if isinstance(raw, dict) else {},
        "result": {
            "data_inicio": result.get("data_inicio") or "",
            "data_fim": result.get("data_fim") or "",
            "loja": result.get("loja") or "",
            "quantidade_devolvida_total": totals.get("quantidade_devolvida_total") or 0,
            "valor_devolvido_total": totals.get("valor_devolvido_total") or 0,
            "top_skus": top_skus,
            "devolucoes": result.get("devolucoes") or [],
            "source_paths": result.get("source_paths") or [],
            "fallback_used": True,
        },
    }


def _assistant_sr_as_return_rate(raw: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    result = _assistant_sr_raw_result(raw)
    if not result:
        return None
    totals = result.get("totais") if isinstance(result.get("totais"), dict) else {}
    return {
        "function": "sales_returns_query_return_rate_by_period",
        "arguments": raw.get("arguments") if isinstance(raw, dict) else {},
        "result": {
            "data_inicio": result.get("data_inicio") or "",
            "data_fim": result.get("data_fim") or "",
            "loja": result.get("loja") or "",
            "quantidade_vendida_total": totals.get("quantidade_vendida_total") or 0,
            "quantidade_devolvida_total": totals.get("quantidade_devolvida_total") or 0,
            "valor_vendido_total": totals.get("valor_vendido_total") or 0,
            "valor_devolvido_total": totals.get("valor_devolvido_total") or 0,
            "taxa_devolucao_quantidade_percentual": totals.get("taxa_devolucao_quantidade_percentual") or 0,
            "taxa_devolucao_valor_percentual": totals.get("taxa_devolucao_valor_percentual") or 0,
            "fallback_used": True,
        },
    }


def _assistant_raw_records(raw: Optional[dict[str, Any]]) -> int:
    if not isinstance(raw, dict):
        return 0
    result = raw.get("result")
    if not isinstance(result, dict):
        return 0
    records = _assistant_result_count(result)
    if records:
        return records
    scalar_keys = (
        "quantidade_total",
        "quantidade_vendida_total",
        "quantidade_devolvida_total",
        "valor_total",
        "valor_vendido_total",
        "valor_devolvido_total",
        "pedidos_total",
        "total_registros",
    )
    if any(result.get(key) not in (None, "", 0, 0.0) for key in scalar_keys):
        return 1
    return 0


def _assistant_sqlite_sales_by_period(
    client_id: str,
    data_inicio: str,
    data_fim: str,
    loja: Optional[str],
    limite: int = DEFAULT_RANKING_LIMIT,
) -> Optional[dict[str, Any]]:
    if not data_inicio or not data_fim:
        return None
    candidates = _assistant_sales_db_candidates(client_id, loja)
    checked = [path for path, _ in candidates]
    if not candidates:
        return {
            "function": "codex_sqlite_sales_by_period",
            "arguments": {"data_inicio": data_inicio, "data_fim": data_fim, "loja": loja or "", "limite": limite},
            "result": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "loja": loja or "",
                "quantidade_total": 0,
                "valor_total": 0,
                "pedidos_total": 0,
                "top_skus": [],
                "source_paths": [],
                "fallback_used": True,
                "empty_reason": "Nenhum banco vendas_historico*.db foi encontrado nas pastas de dados conhecidas.",
            },
        }

    aggregates: dict[str, dict[str, Any]] = {}
    total_qtd = 0.0
    total_valor = 0.0
    total_pedidos = 0
    errors: list[str] = []
    limit_safe = max(1, min(int(limite or DEFAULT_RANKING_LIMIT), 500))
    where = """
        substr(coalesce(data, ''), 1, 10) BETWEEN ? AND ?
        AND coalesce(devolucao, 0) = 0
        AND lower(coalesce(situacao, '')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
    """

    for db_path, store_name in candidates:
        conn = None
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            tables = {str(row[0]) for row in cur.execute("select name from sqlite_master where type='table'").fetchall()}
            if "vendas" not in tables:
                errors.append(f"{db_path}: tabela vendas ausente")
                continue
            total = cur.execute(
                f"""
                SELECT
                    coalesce(sum(coalesce(quantidade, 0)), 0) AS qtd,
                    coalesce(sum(coalesce(valor, 0)), 0) AS valor,
                    count(distinct coalesce(nullif(trim(numero), ''), id_unico, rowid)) AS pedidos
                FROM vendas
                WHERE {where}
                """,
                [data_inicio, data_fim],
            ).fetchone()
            if total:
                total_qtd += float(total["qtd"] or 0)
                total_valor += float(total["valor"] or 0)
                total_pedidos += int(total["pedidos"] or 0)
            rows = cur.execute(
                f"""
                SELECT
                    coalesce(nullif(trim(sku), ''), 'SEM SKU') AS sku,
                    max(coalesce(nullif(trim(produto), ''), '')) AS produto,
                    coalesce(sum(coalesce(quantidade, 0)), 0) AS qtd,
                    coalesce(sum(coalesce(valor, 0)), 0) AS valor,
                    count(distinct coalesce(nullif(trim(numero), ''), id_unico, rowid)) AS pedidos
                FROM vendas
                WHERE {where}
                GROUP BY coalesce(nullif(trim(sku), ''), 'SEM SKU')
                ORDER BY qtd DESC, valor DESC
                LIMIT ?
                """,
                [data_inicio, data_fim, limit_safe],
            ).fetchall()
            for row in rows or []:
                sku = str(row["sku"] or "").strip() or "SEM SKU"
                item = aggregates.setdefault(
                    sku,
                    {
                        "sku": sku,
                        "nome": str(row["produto"] or "").strip()[:160],
                        "qtd": 0.0,
                        "valor": 0.0,
                        "pedidos": 0,
                        "loja": store_name,
                    },
                )
                if not item.get("nome") and row["produto"]:
                    item["nome"] = str(row["produto"] or "").strip()[:160]
                item["qtd"] += float(row["qtd"] or 0)
                item["valor"] += float(row["valor"] or 0)
                item["pedidos"] += int(row["pedidos"] or 0)
        except Exception as exc:
            errors.append(f"{db_path}: {str(exc)[:180]}")
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

    top_skus = sorted(
        aggregates.values(),
        key=lambda item: (float(item.get("qtd") or 0), float(item.get("valor") or 0)),
        reverse=True,
    )[:limit_safe]
    return {
        "function": "codex_sqlite_sales_by_period",
        "arguments": {
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "loja": loja or "",
            "limite": limit_safe,
            "source_paths": checked,
        },
        "result": {
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "loja": loja or "",
            "quantidade_total": total_qtd,
            "valor_total": total_valor,
            "pedidos_total": total_pedidos,
            "top_skus": top_skus,
            "source_paths": checked,
            "source_errors": errors[:8],
            "fallback_used": True,
        },
    }


def _assistant_message_has_product_ref(message: str) -> bool:
    text = _assistant_texto_norm(message)
    text_without_dates = re.sub(r"\b\d{1,2}/\d{1,2}/\d{4}\b", " ", text)
    return bool(
        re.search(r"\bsku\s+[a-z0-9._/-]{2,}\b", text_without_dates)
        or re.search(r"\bmlb[\s_-]?\d{6,}\b", text_without_dates)
        or re.search(r"\bproduto\s+.{3,}", text_without_dates)
        or re.search(r"[a-z0-9]{2,}[-_/][a-z0-9]{1,}", text_without_dates)
    )


def _assistant_is_generic_sales_api_query(message: str) -> bool:
    text = _assistant_texto_norm(message)
    wants_sales = bool(re.search(r"\b(venda|vendas|vendido|vendidos|faturamento|pedido|pedidos|ranking|top)\b", text))
    wants_api = bool(re.search(r"\b(api|apis|via api|pelas apis|pela api)\b", text))
    names_provider = bool(re.search(r"\b(bling|mercado livre|mercadolivre|ml|mlb[\s_-]*\d+)\b", text))
    return bool(wants_sales and wants_api and not names_provider)


def _assistant_latest_ml_event_kind(message: Any) -> str:
    """Classify latest sale/return intent without treating a return order id as a sale query."""

    text = _assistant_texto_norm(str(message or ""))
    if not re.search(r"\b(ultima|ultimo|mais recente|ultima ocorrencia|ultimo registro)\b", text):
        return ""
    latest_period = bool(
        re.search(
            r"\b(ultima|ultimo)\s+(semana|mes|ano|dia|periodo|trimestre|bimestre|semestre)\b",
            text,
        )
    )
    latest_return = bool(
        re.search(
            r"\b(ultima|ultimo|mais recente)\s+(devolucao|devolucoes|reembolso|estorno)\b",
            text,
        )
        or re.search(r"\b(devolucao|devolucoes|reembolso|estorno)\s+mais recente\b", text)
    )
    latest_sale = whatsapp_intent.mercado_livre_sales_lookup(message).get("mode") == "latest"
    if not latest_return and not latest_sale:
        latest_sale = bool(
            not latest_period
            and
            re.search(r"\b(mercado livre|mercadolivre|ml)\b", text)
            and re.search(r"\b(sku\s*[a-z0-9._/-]+|mlb\s*\d{6,})\b", text)
        )
    explicit_both = bool(
        re.search(
            r"\b(ultima|ultimo|mais recente)\s+(venda|pedido)\b[^.]{0,100}\b(e|tambem|junto|ambos)\b"
            r"[^.]{0,100}\b(ultima|ultimo|mais recente)\s+(devolucao|reembolso|estorno)\b",
            text,
        )
        or re.search(
            r"\b(ultima|ultimo|mais recente)\s+(devolucao|reembolso|estorno)\b[^.]{0,100}\b(e|tambem|junto|ambos)\b"
            r"[^.]{0,100}\b(ultima|ultimo|mais recente)\s+(venda|pedido)\b",
            text,
        )
    )
    if explicit_both:
        return "both"
    if latest_return:
        return "return"
    if latest_sale:
        return "sale"
    return ""


def _assistant_source_routing_policy(message: Any) -> dict[str, Any]:
    """Contrato de origem para consultas operacionais do Black Jhon."""
    text = _assistant_texto_norm(str(message or ""))
    wants_stock = bool(re.search(r"\b(estoque|saldo|quantidade em estoque|disponivel em estoque)\b", text))
    wants_positive_sku_count = whatsapp_intent.positive_stock_sku_count_requested(message)
    wants_full = bool(wants_stock and re.search(r"\b(full|fulfillment|mercado envios)\b", text))
    wants_sum = bool(wants_stock and re.search(r"\b(somar|some|soma|somatorio|totalizar|total geral|loja\s*\+\s*full|loja e full)\b", text))
    combined_stock = bool(
        wants_stock
        and re.search(r"\b(loja\s*\+\s*full|loja e full|estoque total geral|saldo total geral)\b", text)
    )
    wants_listing_description = bool(
        re.search(r"\b(anuncio|anuncios|mercado livre|mercadolivre|mlb[\s_-]*\d+)\b", text)
        and re.search(
            r"\b(descricao|descricoes|detalhe|detalhes|atributo|atributos|ficha|conteudo|"
            r"link|links|url|foto|fotos|imagem|imagens|dados|informacao|informacoes|mlb)\b",
            text,
        )
    )
    wants_listing_commercial = bool(
        re.search(r"\b(anuncio|anuncios|mercado livre|mercadolivre|mlb[\s_-]*\d+)\b", text)
        and re.search(r"\b(taxa|taxas|tarifa|tarifas|comissao|comissoes|frete|custo de envio|preco liquido|valor liquido)\b", text)
    )
    wants_visits = bool(
        re.search(r"\b(visita|visitas|visualizacao|visualizacoes|acesso|acessos)\b", text)
        and re.search(r"\b(mercado livre|mercadolivre|mlb[\s_-]*\d+|anuncio)\b", text)
    )
    wants_promotions = bool(
        re.search(r"\b(promocao|promocoes|campanha|campanhas|oferta|ofertas)\b", text)
        and re.search(r"\b(mercado livre|mercadolivre|conta|loja)\b", text)
    )
    wants_post_sale_detail = bool(
        re.search(r"\b(conversa|conversas|mensagem|mensagens|anexo|anexos|mediacao|pos venda|pos-venda)\b", text)
        and re.search(r"\b(pack|pedido|order)\b", text)
    )
    daily_sales_report = bool(
        re.search(r"\b(relatorio|resumo)\b", text)
        and re.search(r"\b(do dia|diario|diaria|de hoje|hoje)\b", text)
    )
    wants_ml_sales = bool(
        daily_sales_report
        or re.search(r"\b(pedido|pedidos|venda|vendas|vendido|vendidos|faturamento|ranking|mais vendido)\b", text)
    )
    wants_ml_returns = bool(re.search(r"\b(devolucao|devolucoes|devolvido|devolvidos|reembolso|reembolsos|estorno|estornos)\b", text))
    if wants_post_sale_detail:
        wants_ml_sales = False
    if wants_ml_returns and whatsapp_intent.mercado_livre_return_reference_only(message):
        wants_ml_sales = False
    latest_event = bool(re.search(r"\b(ultima|ultimo|mais recente|ultima ocorrencia|ultimo registro)\b", text))
    latest_kind = _assistant_latest_ml_event_kind(message)
    if latest_kind == "return":
        wants_ml_sales = False
        wants_ml_returns = True
    elif latest_kind == "sale":
        wants_ml_sales = True
        wants_ml_returns = False
    elif latest_kind == "both":
        wants_ml_sales = True
        wants_ml_returns = True
    required_tools: list[str] = []
    forbidden_tools: list[str] = []
    providers: list[str] = []
    intents: list[str] = []

    def add_unique(target: list[str], *items: str) -> None:
        for item in items:
            if item and item not in target:
                target.append(item)

    if wants_positive_sku_count:
        add_unique(required_tools, "bling_positive_stock_sku_count")
        add_unique(
            forbidden_tools,
            "bling_stock_balances",
            "bling_deposits",
            "mercado_livre_listing",
            "stock_data",
            "stockout_forecast",
            "stale_stock",
            "product_data",
            "product_registry",
        )
        add_unique(providers, "bling")
        intents.append("positive_stock_sku_count")
    elif combined_stock:
        add_unique(required_tools, "bling_stock_balances", "mercado_livre_full_stock")
        add_unique(forbidden_tools, "bling_deposits")
        add_unique(providers, "bling", "mercado_livre")
        intents.append("combined_store_and_full_stock")
    elif wants_full:
        add_unique(required_tools, "mercado_livre_full_stock")
        add_unique(forbidden_tools, "bling_stock_balances", "bling_deposits", "stock_data")
        add_unique(providers, "mercado_livre")
        intents.append("full_stock")
    elif wants_stock:
        add_unique(required_tools, "bling_stock_balances")
        add_unique(forbidden_tools, "bling_deposits")
        add_unique(providers, "bling")
        intents.append("current_store_stock")
    if wants_listing_description or wants_listing_commercial:
        add_unique(required_tools, "mercado_livre_listing")
        add_unique(providers, "mercado_livre")
        intents.append("listing_commercial" if wants_listing_commercial else "listing_description")
    if wants_visits:
        add_unique(required_tools, "mercado_livre_visits")
        add_unique(providers, "mercado_livre")
        intents.append("listing_visits")
    if wants_promotions:
        add_unique(required_tools, "mercado_livre_promotions")
        add_unique(providers, "mercado_livre")
        intents.append("promotions")
    if wants_post_sale_detail:
        add_unique(required_tools, "mercado_livre_post_sale_detail")
        add_unique(providers, "mercado_livre")
        intents.append("post_sale_detail")
    if wants_ml_sales:
        add_unique(required_tools, "mercado_livre_orders")
        add_unique(providers, "mercado_livre")
        intents.append("orders_and_sales")
    if wants_ml_returns:
        add_unique(required_tools, "mercado_livre_returns")
        add_unique(providers, "mercado_livre")
        intents.append("returns")
    if latest_event and (wants_ml_sales or wants_ml_returns):
        add_unique(
            forbidden_tools,
            "sales_returns_query",
            "sales_ranking",
            "sales_summary",
            "returns_summary",
            "return_rate",
        )
    if not required_tools:
        return {}
    return {
        "version": "20260718-whatsapp-source-routing-v6-positive-stock-sku-count",
        "intent": "+".join(intents),
        "required_tools": required_tools,
        "forbidden_tools": forbidden_tools,
        "preferred_providers": providers,
        "force_refresh": bool(wants_stock or wants_listing_description or wants_listing_commercial or wants_visits or wants_promotions or wants_post_sale_detail or wants_ml_sales or wants_ml_returns),
        "include_listing_details": wants_listing_description,
        "include_commercial_detail": wants_listing_commercial,
        "sum_requested": wants_sum,
        "full_exclusive": bool(wants_full and not combined_stock),
        "full_stock_provider": "mercado_livre_api_only",
        "bling_stock_scope": "exclude_full",
        "positive_stock_sku_count_requested": wants_positive_sku_count,
        "aggregation_policy": (
            "single_store_scalar_no_sum"
            if wants_positive_sku_count
            else "sum_bling_store_plus_mercado_livre_full"
            if combined_stock
            else "sum_mercado_livre_full_only"
            if wants_full and wants_sum
            else "separate_sources_no_sum"
        ),
    }


def _assistant_select_tool_ids(message: str, mode: str, screen_context: Any) -> list[str]:
    text = _assistant_texto_norm(message)
    page = _assistant_texto_norm(_assistant_context_page(screen_context))
    selected: list[str] = []
    source_policy = _assistant_source_routing_policy(message)
    if source_policy.get("positive_stock_sku_count_requested") is True:
        return ["bling_positive_stock_sku_count"]

    def add(*tool_ids: str) -> None:
        for tool_id in tool_ids:
            if tool_id not in selected:
                selected.append(tool_id)

    add(*list(source_policy.get("required_tools") or []))

    wants_report = mode in {"daily", "report"} or bool(re.search(r"\b(relatorio|analise|analisar|diagnostico|oportunidade|melhoria|melhorias)\b", text))
    wants_sales = wants_report or bool(re.search(r"\b(venda|vendas|vendido|vendidos|faturamento|pedido|pedidos|ranking|rank|top|mais vendido|mais vendeu)\b", text))
    wants_ranking = bool(re.search(r"\b(ranking|rank|top|mais vendido|mais vendidos|mais vendeu|lider|campeao)\b", text))
    wants_stock = wants_report or bool(re.search(r"\b(estoque|ruptura|saldo|parado|sem vender|giro|reposicao)\b", text))
    wants_returns = wants_report or bool(re.search(r"\b(devolucao|devolucoes|devolvido|devolvidos|return)\b", text))
    wants_margin = wants_report or bool(re.search(r"\b(margem|lucro|rentabilidade|custo|imposto|preco)\b", text))
    wants_api = bool(re.search(r"\b(api|apis|via api|pelas apis|pela api)\b", text))
    wants_external = wants_api or bool(re.search(r"\b(mercado livre|mercadolivre|bling|anuncio|anuncios|integracao|integracoes|token|conta|loja)\b", text))
    wants_product = _assistant_message_has_product_ref(message) or bool(re.search(r"\b(produto|cadastro|imagem|foto|ncm|cest)\b", text))
    wants_operational = bool(re.search(r"\b(full|favoritos|pergunta|perguntas|pos venda|pos-venda|fiscal|simulador|importacao|importacoes|promocao)\b", text + " " + page))
    wants_program_catalog = bool(re.search(r"\b(funcoes|funcionalidades|o que o programa faz|o que voce consegue|modulos|rotas|telas|servicos|catalogo|inventario|saber fazer tudo|fazer tudo)\b", text))
    wants_action_match = bool(re.search(r"\b(faca|faz|execute|executar|acionar|rodar|sincronizar|sicronizar|atualizar|salvar|gerar|aprovar|enviar|cancelar|remover|limpar)\b", text))
    wants_sources = bool(re.search(r"\b(fonte|fontes|banco|sqlite|db|csv|cache|json|arquivo local|dados locais|qualquer fonte|logs?)\b", text))
    wants_sync_logs = bool(re.search(r"\b(log|logs|erro|falha|status|estado|nao aparecem|nao apareceu|sumiu|sincronizacao|sincronizar|sync|shared sync|shared-sync)\b", text))
    wants_memory = wants_report or wants_action_match or bool(
        re.search(r"\b(lembra|memoria|preferencia|preferencias|regra|regras|decisao|decisoes|relatorio anterior|relatorios anteriores|custo pendente|custos pendentes|alerta ignorado|alertas ignorados|sku frequente|loja usada)\b", text)
    )
    mentions_bling = "bling" in text
    mentions_ml = bool(re.search(r"\b(mercado livre|mercadolivre|mlb[\s_-]*\d+|anuncio|anuncios)\b", text))
    wants_generic_sales_apis = _assistant_is_generic_sales_api_query(message)
    wants_fiscal = bool(re.search(r"\b(fiscal|nota|notas|nf|nfe|nf-e|nfce|nfse|ncm|cest|tributacao|natureza|naturezas|cfop)\b", text))
    wants_finance = bool(re.search(r"\b(financeiro|contas? a receber|contas? a pagar|receber|pagar|caixa|banco|boleto|boletos)\b", text))
    wants_lots = bool(re.search(r"\b(lote|lotes|validade|lancamento|lancamentos|entrada|saida)\b", text))
    wants_deposits = bool(re.search(r"\b(deposito|depositos|full|fulfillment)\b", text))
    wants_broad_bling = (
        mentions_bling
        and bool(re.search(r"\b(qualquer|tudo|todas|todos|geral|dados|informacao|informacoes|relatorio|analise)\b", text))
        and not any((wants_fiscal, wants_stock, wants_sales, wants_finance, wants_lots, wants_product))
    )

    if wants_memory:
        add("operational_memory_query")
    if wants_program_catalog or wants_action_match or wants_operational or wants_report:
        add("capability_resolve")
    if wants_sources:
        add("source_discovery")
    if wants_sync_logs:
        add("sync_logs_query")
    if wants_program_catalog:
        add("program_functions_catalog")
    if wants_action_match:
        add("capability_resolve", "program_action_match")
    if wants_sales or wants_returns or wants_ranking:
        add("sales_returns_query")
    if wants_sales or wants_ranking:
        add("sales_ranking", "sales_summary")
    if wants_report:
        add("avg_ticket", "period_comparison", "sales_timeseries", "sales_anomalies")
    if wants_returns:
        add("returns_summary", "return_rate")
    if wants_stock:
        add("stockout_forecast", "stale_stock")
    if wants_margin:
        add("profit_summary", "product_costs_and_margin")
    if wants_report:
        add("integrations_status")
    if wants_external:
        add("integrations_status")
        if wants_generic_sales_apis:
            add("bling_sales_orders", "mercado_livre_orders")
        if mentions_ml:
            add("mercado_livre_readonly", "mercado_livre_listing")
            if wants_sales:
                add("mercado_livre_orders")
        if mentions_bling:
            add("bling_status")
            if wants_product:
                add("bling_products")
            if wants_stock or wants_deposits:
                add("bling_stock_balances", "bling_deposits")
            if wants_sales or wants_ranking:
                add("bling_sales_orders")
                if re.search(r"\b(detalhe|detalhar|itens do pedido|pedido\s+\d+)\b", text):
                    add("bling_sales_order_detail")
            if wants_fiscal:
                if wants_product or "ncm" in text or "cest" in text:
                    add("bling_fiscal_product")
                add("bling_fiscal_nfe", "bling_operation_natures")
                if re.search(r"\b(detalhe|detalhar|chave|44 digitos|44 digitos|nota\s+\d+|nfe\s+\d+)\b", text):
                    add("bling_fiscal_nfe_detail")
            if wants_lots:
                add("bling_lots", "bling_lot_movements")
            if wants_finance:
                add("bling_finance_summary")
            if wants_broad_bling:
                add("bling_deposits", "bling_resource_query")
    if wants_product:
        add("product_data", "product_registry", "stock_data", "local_csv_query", "local_database_query")
        if wants_margin:
            add("product_margin", "product_costs_and_margin")
        if "imagem" in text or "foto" in text:
            add("product_image")
    if wants_operational:
        if re.search(r"\b(pergunta|perguntas|pos venda|pos-venda)\b", text + " " + page):
            add("questions_post_sale_query", "mercado_livre_readonly")
        if wants_fiscal:
            add("fiscal_local_query")
        add("operational_dispatcher")
    if wants_fiscal:
        add("fiscal_local_query")
    if wants_sources and not any(tool in selected for tool in ("local_database_query", "local_csv_query", "local_cache_query")):
        add("local_database_query", "local_csv_query", "local_cache_query")
    if wants_generic_sales_apis:
        primary = [tool for tool in ("bling_sales_orders", "mercado_livre_orders") if tool in selected]
        supporting = [tool for tool in ("sales_returns_query", "sales_ranking", "sales_summary") if tool in selected]
        selected = primary + supporting + [tool for tool in selected if tool not in primary and tool not in supporting]
    if not selected:
        add("operational_dispatcher", "integrations_status")
    forbidden = {str(item or "") for item in (source_policy.get("forbidden_tools") or [])}
    if forbidden:
        selected = [tool for tool in selected if tool not in forbidden]
    required = [str(item or "") for item in (source_policy.get("required_tools") or []) if str(item or "")]
    if required:
        selected = required + [tool for tool in selected if tool not in required]
    return selected[:28]


def _assistant_registry_plan(client_id: str, message: str, screen_context: Any, mode: str) -> dict[str, Any]:
    data_inicio, data_fim = _assistant_resolve_period(client_id, message, screen_context)
    text_norm = _assistant_texto_norm(message)
    effective_mode = str(mode or "chat").strip().lower() or "chat"
    if effective_mode == "chat" and re.search(r"\b(relatorio|relatorios)\b", text_norm):
        effective_mode = "report"
    if (
        re.search(r"\b(ultima|ultimo|mais recente|ultima ocorrencia|ultimo registro)\b", text_norm)
        and re.search(r"\b(venda|pedido|devolucao|devolvido|reembolso|estorno)\b", text_norm)
        and not re.search(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b", str(message or ""))
    ):
        data_inicio, data_fim = _assistant_periodo_padrao(365)
    prev_inicio, prev_fim = _assistant_previous_period(data_inicio, data_fim)
    loja = _assistant_resolve_loja(client_id, message, screen_context)
    sku = _assistant_extract_sku_filter(message, screen_context)
    separar_por_loja = _assistant_wants_store_breakdown(message, loja)
    selected = _assistant_select_tool_ids(message, effective_mode, screen_context)
    source_policy = _assistant_source_routing_policy(message)
    api_sales_query = _assistant_is_generic_sales_api_query(message)
    steps = ["interpretando pedido"]
    for tool_id in selected:
        status = str(_assistant_tool_meta(tool_id).get("status") or "").strip()
        if status and status not in steps:
            steps.append(status)
    return {
        "client_id": str(client_id or "default"),
        "intent": "sales_ranking" if "sales_ranking" in selected else ("report" if effective_mode in {"daily", "report"} else "data_query"),
        "mode": effective_mode,
        "message": str(message or ""),
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
        "periodo_anterior": {"data_inicio": prev_inicio, "data_fim": prev_fim},
        "loja": loja,
        "sku": sku,
        "separar_por_loja": separar_por_loja,
        "incluir_registros": True,
        "selected_tools": selected,
        "source_policy": source_policy,
        "force_refresh": bool(source_policy.get("force_refresh")),
        "api_sales_query": api_sales_query,
        "source_roles": {
            tool_id: (
                "primary_api"
                if tool_id in {"bling_sales_orders", "mercado_livre_orders", "mercado_livre_returns"}
                else "supporting_local_history"
                if api_sales_query and tool_id in {"sales_returns_query", "sales_ranking", "sales_summary"}
                else "context"
            )
            for tool_id in selected
        },
        "aggregation_policy": "separate_sources_no_sum" if api_sales_query else "standard",
        "status_steps": steps,
    }


def _assistant_tool_rows(result: Any) -> list[Any]:
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return []
    for key in (
        "rows",
        "results",
        "campaigns",
        "by_sku",
        "por_sku",
        "vendas",
        "devolucoes",
        "por_loja",
        "top_skus",
        "items",
        "itens",
        "matches",
        "lojas",
        "produtos",
        "produtos_fiscais",
        "saldos",
        "depositos",
        "pedidos",
        "orders",
        "notas",
        "naturezas",
        "lotes",
        "lancamentos",
        "financeiro",
        "resources",
        "sources",
        "capabilities",
        "candidates",
        "routes",
        "services",
        "actions",
        "pages",
        "modules",
        "functions",
        "missing_params",
        "alertas",
        "anomalias",
        "pontos",
    ):
        value = result.get(key)
        if isinstance(value, list):
            return value
    return []


def _assistant_tool_empty_reason(tool_id: str, records: int, result: Any, plan: dict[str, Any]) -> str:
    if records > 0:
        return ""
    if isinstance(result, dict) and result.get("error"):
        return str(result.get("error") or "")[:300]
    if isinstance(result, dict) and result.get("live_query"):
        if result.get("coverage_complete"):
            return ""
        failed = str(result.get("lojas_incompletas_text") or "").strip()
        errors = result.get("errors_by_store") if isinstance(result.get("errors_by_store"), dict) else {}
        affected = ", ".join([name for name in list(errors) + failed.split(", ") if str(name or "").strip()])
        return (
            f"Nao foi possivel confirmar toda a fila atual{f' de {affected}' if affected else ''}."
        )[:300]
    if tool_id == "sales_ranking":
        return (
            "Nenhuma venda por SKU foi encontrada para "
            f"{plan.get('loja') or 'todas as lojas'} entre {plan.get('data_inicio')} e {plan.get('data_fim')}."
        )
    if tool_id == "mercado_livre_listing":
        if (
            isinstance(result, dict)
            and result.get("coverage_complete") is True
            and result.get("partial_response") is not True
            and not result.get("error")
        ):
            return ""
        return "Nenhum anuncio Mercado Livre foi retornado para a consulta read-only."
    if tool_id == "mercado_livre_orders":
        return "A API do Mercado Livre retornou zero pedidos para os filtros informados."
    if tool_id == "mercado_livre_returns":
        return "A API do Mercado Livre retornou zero devolucoes para o periodo informado."
    if tool_id == "mercado_livre_full_stock":
        return "A API de inventario Full do Mercado Livre nao retornou saldo para os filtros informados."
    if tool_id == "bling_product":
        return "Nenhum produto Bling foi encontrado ou nao havia SKU/produto suficiente para consultar."
    if str(tool_id or "").startswith("bling_"):
        return "Consulta Bling read-only nao retornou registros para os filtros informados."
    if tool_id == "program_action_match":
        return "Nenhuma acao operacional aprovada foi reconhecida para este pedido."
    if tool_id == "program_functions_catalog":
        return "Catalogo do programa nao retornou funcoes para o filtro informado."
    if tool_id == "context_hub_search":
        return "A geracao ativa do Context Hub nao retornou resultados para a consulta."
    return "Consulta read-only nao retornou registros."


def _assistant_dlp_safe_rows(rows: Any) -> tuple[list[Any], int]:
    """Drop complete evidence rows whose snippets trip the Context Hub DLP."""

    from backend.services import context_hub

    def snippets(value: Any) -> list[str]:
        if isinstance(value, dict):
            found = [str(value.get("snippet") or "")] if "snippet" in value else []
            return found + [item for child in value.values() for item in snippets(child)]
        if isinstance(value, list):
            return [item for child in value for item in snippets(child)]
        return []

    safe: list[Any] = []
    blocked = 0
    for row in rows if isinstance(rows, list) else []:
        if any(context_hub.scan_dlp(value, source_ref="whatsapp_tool_snippet") for value in snippets(row) if value):
            blocked += 1
        else:
            safe.append(row)
    return safe, blocked


def _assistant_standard_result(tool_id: str, raw: Optional[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
    meta = _assistant_tool_meta(tool_id)
    raw = raw if isinstance(raw, dict) else {}
    result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
    rows = _assistant_tool_rows(result)
    rows, dlp_blocked_count = _assistant_dlp_safe_rows(rows)
    if tool_id == "context_hub_search":
        allowed_keys = {
            "doc_id",
            "chunk_id",
            "snippet",
            "score",
            "truth_class",
            "source_version",
            "source_hash",
            "content_hash",
            "generation_id",
            "version",
            "hash",
            "generation",
            "type",
            "module",
            "surface",
            "selection_strategy",
            "selection_reason",
        }
        # Source references may contain local absolute paths.  They are useful
        # in the full admin UI but must not leak into WhatsApp task evidence.
        rows = [
            {
                str(key): value
                for key, value in row.items()
                if str(key) in allowed_keys
            } | ({"reference": str(row.get("doc_id") or "")[:240]} if row.get("doc_id") else {})
            for row in rows
            if isinstance(row, dict)
        ]
    single_sale_rows = bool(
        tool_id == "mercado_livre_orders"
        and isinstance(result.get("orders"), list)
        and (
            _assistant_latest_ml_event_kind(plan.get("message")) in {"sale", "both"}
            or result.get("exact_lookup") is True
        )
    )
    if (
        tool_id == "mercado_livre_orders"
        and isinstance(result.get("orders"), list)
        and (
            single_sale_rows
            or result.get("exact_lookup") is True
            or (
                isinstance(result.get("target"), dict)
                and isinstance(result.get("exact_coverage"), dict)
            )
        )
    ):
        rows = result.get("orders") or []
    records = len(rows) if single_sale_rows else _assistant_result_count(result)
    if dlp_blocked_count:
        records = len(rows)
    if rows and records == 0:
        records = len(rows)
    if records == 0 and isinstance(result, dict):
        scalar_keys = (
            "quantidade_total",
            "quantidade_vendida_total",
            "quantidade_devolvida_total",
            "valor_total",
            "valor_vendido_total",
            "valor_devolvido_total",
            "pedidos_total",
            "lucro_estimado",
            "ticket_medio",
            "taxa_devolucao_quantidade_percentual",
            "taxa_devolucao_valor_percentual",
        )
        if any(result.get(key) not in (None, "", 0, 0.0) for key in scalar_keys):
            records = 1
    if (
        tool_id == "stock_data"
        and records == 0
        and result.get("found") is True
        and str(result.get("sku") or "").strip()
        and all(key in result for key in ("saldo_loja_total", "saldo_full_total", "saldo_total"))
    ):
        # A row with a confirmed numeric zero is still one inventory record.
        records = 1
    source_raw = raw.get("function") or meta.get("executor") or tool_id
    tool_label = _assistant_human_tool_label(tool_id)
    source_label = _assistant_human_source_label(source_raw, tool_id)
    if tool_id == "context_hub_search":
        safe_summary = {
            key: result.get(key)
            for key in ("success", "count", "generation_id", "generation", "source_version")
            if result.get(key) not in (None, "")
        }
        safe_arguments: dict[str, Any] = {}
    else:
        safe_summary = {
            key: value for key, value in result.items() if not isinstance(value, list)
        } if isinstance(result, dict) else {}
        safe_arguments = raw.get("arguments") or {}
    if dlp_blocked_count:
        safe_summary["dlp_blocked_count"] = dlp_blocked_count
    exact_metadata = {}
    if tool_id == "mercado_livre_orders" and result.get("exact_lookup") is True:
        exact_metadata = {
            "exact_lookup": True,
            "requested_id": str(result.get("requested_id") or ""),
            "identifier_type": str(result.get("identifier_type") or ""),
            "matched_stores": copy.deepcopy(result.get("matched_stores") or []),
            "resolved_order_ids": copy.deepcopy(result.get("resolved_order_ids") or []),
            "searched_stores": copy.deepcopy(result.get("searched_stores") or []),
            "resources": [
                str(item.get("resource") or "")
                for item in (result.get("sources") or [])
                if isinstance(item, dict) and str(item.get("resource") or "").strip()
            ],
            "found": bool(result.get("found")),
            "error": str(result.get("error") or ""),
            "message": str(result.get("message") or ""),
            "partial_response": bool(result.get("partial_response")),
            "coverage_complete": bool(result.get("coverage_complete")),
        }
    return {
        "tool_id": tool_id,
        "tool_label": tool_label,
        "module": meta.get("module"),
        "description": meta.get("description"),
        "executor": meta.get("executor"),
        "external": bool(meta.get("external")),
        "read_only": meta.get("read_only") is True,
        "records": records,
        "rows": rows if tool_id == "sales_returns_query" and isinstance(rows, list) else (rows[:500] if isinstance(rows, list) else []),
        "summary": safe_summary,
        "exact_metadata": exact_metadata,
        "source": source_raw,
        "source_label": source_label,
        "sources_human": _assistant_human_source_list([source_raw], tool_id),
        "source_role": str((plan.get("source_roles") or {}).get(tool_id) or plan.get("source_role") or "context"),
        "aggregation_policy": str(plan.get("aggregation_policy") or "standard"),
        "arguments": safe_arguments,
        "periodo": plan.get("periodo") or {},
        "loja": plan.get("loja") or "",
        "empty_reason": _assistant_tool_empty_reason(tool_id, records, result, plan),
        "next_fallbacks": meta.get("fallbacks") or [],
        "next_fallbacks_human": _assistant_human_fallback_list(meta.get("fallbacks") or []),
        "generated_at": _assistant_now(),
    }


def _assistant_mercado_livre_full_stock_raw(client_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    from backend.services import full_mercadolivre

    loja = str(plan.get("loja") or "").strip()
    sku = _assistant_normalize_sku(plan.get("sku") or "")
    item_id = _assistant_normalize_ml_item_id(plan.get("item_id") or plan.get("mlb") or "")
    try:
        limite = max(100, min(int(plan.get("limite") or plan.get("limit") or 10000), 20000))
    except Exception:
        limite = 10000
    arguments = {
        "loja": loja,
        "sku": sku,
        "item_id": item_id,
        "limit": limite,
        "force_refresh": bool(plan.get("force_refresh", True)),
    }
    payload = full_mercadolivre.listar_anuncios_full_mercadolivre_payload(
        client_id,
        loja,
        limite,
        force_refresh=bool(plan.get("force_refresh", True)),
    )
    rows = [item for item in (payload.get("results") or []) if isinstance(item, dict)]
    if item_id:
        rows = [item for item in rows if _assistant_normalize_ml_item_id(item.get("id")) == item_id]
    if sku:
        sku_key = _assistant_normalize_sku(sku)

        def sku_matches(item: dict[str, Any]) -> bool:
            candidates = [item.get("sku"), item.get("sku_display")]
            for variation in item.get("variations") if isinstance(item.get("variations"), list) else []:
                if isinstance(variation, dict):
                    candidates.extend([variation.get("sku"), variation.get("seller_sku")])
            return any(sku_key == _assistant_normalize_sku(value) for value in candidates if str(value or "").strip())

        rows = [item for item in rows if sku_matches(item)]
    warnings = []
    for item in rows:
        if str(item.get("stock_source") or "") != "fulfillment_stock":
            warnings.append(
                f"{item.get('id') or item.get('sku') or 'item'}: o inventario Full nao confirmou saldo atual."
            )
    available = sum(_assistant_float(item.get("full_available_quantity")) for item in rows)
    unavailable = sum(_assistant_float(item.get("full_not_available_quantity")) for item in rows)
    total = sum(_assistant_float(item.get("full_total_quantity")) for item in rows)
    return {
        "function": "get_mercado_livre_full_stock",
        "arguments": arguments,
        "result": {
            "found": bool(rows),
            "store": loja,
            "matches": rows,
            "records": len(rows),
            "full_available_quantity_total": available,
            "full_not_available_quantity_total": unavailable,
            "full_total_quantity": total,
            "stock_scope": "mercado_livre_fulfillment_only",
            "api_consulted": True,
            "sources": [{
                "provider": "mercado_livre",
                "resource": "inventories/{inventory_id}/stock/fulfillment",
                "method": "GET",
                "store": loja,
            }],
            "warnings": warnings,
            "read_only": True,
        },
    }


def _assistant_execute_registry_tool(
    client_id: str,
    tool_id: str,
    message: str,
    screen_context: Any,
    plan: dict[str, Any],
    existing_registry_results: Optional[list[dict[str, Any]]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    raw_results: list[dict[str, Any]] = []
    registry_results: list[dict[str, Any]] = []
    data_inicio = str(plan.get("data_inicio") or "")
    data_fim = str(plan.get("data_fim") or "")
    loja = str(plan.get("loja") or "") or None
    sku = str(plan.get("sku") or "") or None
    separar_por_loja = bool(plan.get("separar_por_loja"))
    incluir_registros = bool(plan.get("incluir_registros", True))
    prev = plan.get("periodo_anterior") if isinstance(plan.get("periodo_anterior"), dict) else {}
    mode = str(plan.get("mode") or "")
    report_mode = mode in {"daily", "report"}
    default_limit = 20_000 if tool_id == "mercado_livre_orders" and report_mode else (
        BLING_REPORT_LIMIT if report_mode else DEFAULT_RANKING_LIMIT
    )
    try:
        limit_safe = int(plan.get("limite") or plan.get("limit") or default_limit)
    except Exception:
        limit_safe = default_limit
    limit_safe = max(1, min(limit_safe, 20_000 if tool_id == "mercado_livre_orders" and report_mode else (500 if report_mode else 200)))

    try:
        raw: Optional[dict[str, Any]] = None
        if tool_id == "sales_returns_query":
            raw = _assistant_sales_returns_query(client_id, data_inicio, data_fim, loja, sku, separar_por_loja, incluir_registros)
        elif tool_id == "sales_ranking":
            raw = _assistant_call_ia_tool("_ia_tool_get_sales_by_period", client_id, data_inicio, data_fim, loja, limit_safe)
            if _assistant_raw_records(raw) == 0:
                sr_raw = _assistant_sales_returns_query(client_id, data_inicio, data_fim, loja, sku, separar_por_loja, incluir_registros)
                fallback_raw = _assistant_sr_as_sales_by_period(sr_raw, limit_safe) or _assistant_sqlite_sales_by_period(client_id, data_inicio, data_fim, loja, limit_safe)
                if isinstance(fallback_raw, dict):
                    fallback_records = _assistant_raw_records(fallback_raw)
                    if fallback_records > 0 or not isinstance(raw, dict):
                        raw = fallback_raw
                        warnings.append(
                            "sales_ranking: usei sales_returns_query/SQLite direto dos bancos vendas_historico*.db "
                            "porque a ferramenta principal retornou vazio."
                        )
        elif tool_id == "sales_summary":
            raw = _assistant_call_ia_tool("_ia_tool_get_sales_quantity_by_period", client_id, data_inicio, data_fim, loja)
            if _assistant_raw_records(raw) == 0:
                sr_raw = _assistant_sales_returns_query(client_id, data_inicio, data_fim, loja, sku, separar_por_loja, incluir_registros)
                raw = _assistant_sr_as_sales_quantity(sr_raw)
                if isinstance(raw, dict) and _assistant_raw_records(raw) > 0:
                    warnings.append(
                        "sales_summary: usei sales_returns_query direto dos bancos vendas_historico*.db "
                        "porque a ferramenta principal retornou vazio."
                    )
        elif tool_id == "returns_summary":
            raw = _assistant_call_ia_tool("_ia_tool_get_returns_by_period", client_id, data_inicio, data_fim, loja, 50)
            if _assistant_raw_records(raw) == 0:
                sr_raw = _assistant_sales_returns_query(client_id, data_inicio, data_fim, loja, sku, separar_por_loja, incluir_registros)
                fallback_raw = _assistant_sr_as_returns_by_period(sr_raw, 50)
                if isinstance(fallback_raw, dict) and (_assistant_raw_records(fallback_raw) > 0 or not isinstance(raw, dict)):
                    raw = fallback_raw
                    warnings.append(
                        "returns_summary: usei sales_returns_query com notas_entrada_itens/vendas.devolucao "
                        "porque a ferramenta principal retornou vazio."
                    )
        elif tool_id == "return_rate":
            raw = _assistant_call_ia_tool("_ia_tool_get_return_rate_by_period", client_id, data_inicio, data_fim, loja)
            if _assistant_raw_records(raw) == 0:
                sr_raw = _assistant_sales_returns_query(client_id, data_inicio, data_fim, loja, sku, separar_por_loja, incluir_registros)
                fallback_raw = _assistant_sr_as_return_rate(sr_raw)
                if isinstance(fallback_raw, dict):
                    raw = fallback_raw
                    warnings.append("return_rate: calculei a taxa pela sales_returns_query.")
        elif tool_id == "profit_summary":
            raw = _assistant_call_ia_tool("_ia_tool_get_profit_by_period", client_id, data_inicio, data_fim, loja)
        elif tool_id == "product_costs_and_margin":
            raw = _assistant_product_costs_and_margin_raw(client_id, plan, existing_registry_results)
        elif tool_id == "avg_ticket":
            raw = _assistant_call_ia_tool("_ia_tool_get_avg_ticket_by_period", client_id, data_inicio, data_fim, loja)
        elif tool_id == "period_comparison":
            raw = _assistant_call_ia_tool(
                "_ia_tool_get_period_comparison",
                client_id,
                str(prev.get("data_inicio") or ""),
                str(prev.get("data_fim") or ""),
                data_inicio,
                data_fim,
                loja,
            )
        elif tool_id == "sales_timeseries":
            raw = _assistant_call_ia_tool("_ia_tool_get_sales_timeseries", client_id, data_inicio, data_fim, loja)
        elif tool_id == "sales_anomalies":
            raw = _assistant_call_ia_tool("_ia_tool_detect_sales_anomalies", client_id, data_inicio, data_fim, loja)
        elif tool_id == "stockout_forecast":
            raw = _assistant_call_ia_tool("_ia_tool_get_stockout_forecast", client_id, message, None, loja, 30, 100)
        elif tool_id == "stale_stock":
            raw = _assistant_call_ia_tool("_ia_tool_get_days_without_sale_top", client_id, loja, limit_safe, True, False, True)
        elif tool_id == "integrations_status":
            raw = _assistant_call_ia_tool("_ia_tool_get_integrations_status", client_id, loja)
        elif tool_id == "mercado_livre_resource_query":
            from backend.services import mercado_livre_query_service

            raw = mercado_livre_query_service.execute_mercado_livre_query(
                client_id=client_id,
                message=message,
                loja=loja,
                resource_id=str(plan.get("resource_id") or ""),
                params=plan.get("resource_params") if isinstance(plan.get("resource_params"), dict) else {},
                limit=limit_safe,
                query_deadline=plan.get("query_deadline"),
            )
        elif tool_id == "mercado_livre_listing":
            ml_message = message if re.search(r"\b(mercado livre|mercadolivre|mlb[\s_-]*\d+|sku|anuncio|anuncios|listar)\b", _assistant_texto_norm(message)) else "listar anuncios ativos mercado livre"
            raw = _assistant_call_ia_tool(
                "_ia_tool_get_mercado_livre_listing",
                client_id,
                ml_message,
                loja=loja,
                produto_tool=None,
                limite=limit_safe,
                incluir_descricao=bool(plan.get("incluir_detalhes")),
                status=str(plan.get("status") or "active"),
                sku=str(plan.get("sku") or ""),
                item_id=str(plan.get("item_id") or ""),
                offset=int(plan.get("offset") or 0),
                incluir_detalhes=bool(plan.get("incluir_detalhes")),
                force_refresh=bool(plan.get("force_refresh")),
                query_deadline=plan.get("query_deadline"),
                incluir_comercial=bool(plan.get("incluir_comercial")),
            )
            if _assistant_tool_call_signature_error(raw):
                # Runtime anterior: mantenha a listagem basica enquanto a nova
                # assinatura ainda nao estiver presente no espelho instalado.
                raw = _assistant_call_ia_tool(
                    "_ia_tool_get_mercado_livre_listing",
                    client_id,
                    ml_message,
                    loja,
                    None,
                    limit_safe,
                    bool(plan.get("incluir_detalhes")),
                )
        elif tool_id == "mercado_livre_visits":
            raw = _assistant_call_ia_tool(
                "_ia_tool_get_mercado_livre_visits",
                client_id,
                message,
                loja=loja,
                item_id=str(plan.get("item_id") or ""),
                dias=int(plan.get("dias") or 30),
                force_refresh=bool(plan.get("force_refresh")),
                query_deadline=plan.get("query_deadline"),
            )
        elif tool_id == "mercado_livre_promotions":
            raw = _assistant_call_ia_tool(
                "_ia_tool_get_mercado_livre_promotions",
                client_id,
                message,
                loja=loja,
                status=str(plan.get("status") or ""),
                promotion_id=str(plan.get("promotion_id") or ""),
                incluir_contagens=bool(plan.get("incluir_contagens")),
                limite=limit_safe,
                force_refresh=bool(plan.get("force_refresh")),
                query_deadline=plan.get("query_deadline"),
            )
        elif tool_id == "mercado_livre_orders":
            raw = _assistant_call_ia_tool(
                "_ia_tool_get_mercado_livre_orders",
                client_id,
                message,
                loja=loja,
                data_inicio=data_inicio,
                data_fim=data_fim,
                status=str(plan.get("status") or "paid,partially_refunded"),
                sku=str(plan.get("sku") or ""),
                item_id=str(plan.get("item_id") or ""),
                id_pedido=str(plan.get("id_pedido") or ""),
                offset=int(plan.get("offset") or 0),
                limite=limit_safe,
                incluir_detalhes=bool(plan.get("incluir_detalhes")),
                force_refresh=bool(plan.get("force_refresh")),
                query_deadline=plan.get("query_deadline"),
                modo_relatorio=report_mode,
                max_paginas=int(plan.get("max_paginas") or (400 if report_mode else 2)),
            )
            if _assistant_tool_call_signature_error(raw):
                raw = _assistant_call_ia_tool(
                    "_ia_tool_get_mercado_livre_orders",
                    client_id,
                    message,
                    loja=loja,
                    data_inicio=data_inicio,
                    data_fim=data_fim,
                    status=str(plan.get("status") or "paid,partially_refunded"),
                    sku=str(plan.get("sku") or ""),
                    item_id=str(plan.get("item_id") or ""),
                    id_pedido=str(plan.get("id_pedido") or ""),
                    offset=int(plan.get("offset") or 0),
                    limite=min(limit_safe, 100),
                    incluir_detalhes=bool(plan.get("incluir_detalhes")),
                    force_refresh=bool(plan.get("force_refresh")),
                    query_deadline=plan.get("query_deadline"),
                )
            if raw is None:
                raw = {
                    "function": "get_mercado_livre_orders",
                    "arguments": {
                        "loja": loja or "",
                        "data_inicio": data_inicio,
                        "data_fim": data_fim,
                        "status": str(plan.get("status") or "paid,partially_refunded"),
                        "sku": str(plan.get("sku") or ""),
                        "item_id": str(plan.get("item_id") or ""),
                        "id_pedido": str(plan.get("id_pedido") or ""),
                        "offset": int(plan.get("offset") or 0),
                        "limite": limit_safe,
                    },
                    "result": {
                        "error": "Consulta de pedidos Mercado Livre indisponivel neste runtime.",
                        "pedidos": [],
                        "read_only": True,
                    },
                }
        elif tool_id == "mercado_livre_returns":
            raw = _assistant_call_ia_tool(
                "_ia_tool_get_mercado_livre_returns",
                client_id,
                message,
                loja=loja,
                data_inicio=data_inicio,
                data_fim=data_fim,
                limite=limit_safe,
                offset=int(plan.get("offset") or 0),
                sku=str(plan.get("sku") or ""),
                item_id=str(plan.get("item_id") or ""),
                force_refresh=bool(plan.get("force_refresh")),
                query_deadline=plan.get("query_deadline"),
            )
            if raw is None:
                raw = {
                    "function": "get_mercado_livre_returns",
                    "arguments": {
                        "loja": loja or "",
                        "data_inicio": data_inicio,
                        "data_fim": data_fim,
                        "offset": int(plan.get("offset") or 0),
                        "limite": limit_safe,
                        "sku": str(plan.get("sku") or ""),
                        "item_id": str(plan.get("item_id") or ""),
                    },
                    "result": {
                        "error": "Consulta de devolucoes Mercado Livre indisponivel neste runtime.",
                        "devolucoes": [],
                        "read_only": True,
                    },
                }
        elif tool_id == "mercado_livre_full_stock":
            raw = _assistant_mercado_livre_full_stock_raw(client_id, plan)
        elif tool_id == "bling_product":
            bling_message = _assistant_bling_message_with_refs(message, plan, screen_context, existing_registry_results)
            raw = _assistant_call_ia_tool("_ia_tool_get_bling_product", client_id, bling_message, loja, None)
        elif str(tool_id or "").startswith("bling_"):
            from backend.services import codex_bling_tools

            bling_message = (
                message
                if tool_id == "bling_positive_stock_sku_count"
                else _assistant_bling_message_with_refs(message, plan, screen_context, existing_registry_results)
            )
            if tool_id == "bling_sales_order_detail" and str(plan.get("id_pedido") or ""):
                bling_message = f"pedido {plan.get('id_pedido')} {bling_message}".strip()
            raw = codex_bling_tools.execute_bling_tool(
                client_id=client_id,
                tool_id=tool_id,
                message=bling_message,
                loja=loja,
                data_inicio=data_inicio,
                data_fim=data_fim,
                limit=limit_safe,
                offset=int(plan.get("offset") or 0),
                status=str(plan.get("status") or ""),
                sku=str(plan.get("sku") or ""),
                item_id=str(plan.get("item_id") or ""),
                id_pedido=str(plan.get("id_pedido") or ""),
                force_refresh=bool(plan.get("force_refresh")),
                query_deadline=plan.get("query_deadline"),
            )
        elif tool_id == "product_data":
            raw = _assistant_call_ia_tool("_ia_tool_get_product_data", client_id, message, 10)
        elif tool_id == "product_registry":
            raw = _assistant_call_ia_tool("_ia_tool_get_product_registry_info", client_id, message, None, 10)
        elif tool_id == "stock_data":
            raw = _assistant_call_ia_tool("_ia_tool_get_stock_data", client_id, message, None)
        elif tool_id == "product_margin":
            raw = _assistant_call_ia_tool("_ia_tool_get_product_margin", client_id, message, None)
        elif tool_id == "product_image":
            raw = _assistant_call_ia_tool("_ia_tool_get_product_image", client_id, message, None)
        elif tool_id == "operational_memory_query":
            from backend.services import codex_operational_memory

            result = codex_operational_memory.query_memory(
                client_id=client_id,
                message=message,
                category=str(plan.get("category_filter") or ""),
                limit=limit_safe,
            )
            raw = {
                "function": "codex_operational_memory.query_memory",
                "arguments": {
                    "message": message,
                    "category": str(plan.get("category_filter") or ""),
                    "limit": limit_safe,
                },
                "result": result,
            }
        elif tool_id == "context_hub_search":
            from backend.services import context_hub

            filters = {
                "module": str(plan.get("module_filter") or ""),
                "ids": list(plan.get("context_ids") or [])[:50],
                "source_type": str(plan.get("source_type") or ""),
                "environment": str(plan.get("environment_filter") or ""),
                "request_surface": str(plan.get("request_surface") or ""),
            }
            filters = {key: value for key, value in filters.items() if value not in ("", [], None)}
            result = context_hub.search_context(
                client_id=client_id,
                query=message,
                filters=filters,
                limit=min(limit_safe, 12),
            )
            raw = {
                "function": "context_hub.search_context",
                "arguments": {
                    "query": message,
                    "filters": filters,
                    "limit": min(limit_safe, 12),
                },
                "result": result,
            }
        elif tool_id in {
            "source_discovery",
            "local_database_query",
            "local_csv_query",
            "local_cache_query",
            "sync_logs_query",
            "mercado_livre_readonly",
            "questions_post_sale_query",
            "mercado_livre_post_sale_detail",
            "fiscal_local_query",
        }:
            from backend.services import codex_readonly_sources

            query_deadline = plan.get("query_deadline")
            try:
                query_deadline_seconds = max(5, min(15, int(float(query_deadline) - time.monotonic())))
            except (TypeError, ValueError):
                query_deadline_seconds = 15
            result = codex_readonly_sources.execute_readonly_source_tool(
                client_id=client_id,
                tool_id=tool_id,
                message=message,
                loja=loja or "",
                data_inicio=data_inicio,
                data_fim=data_fim,
                limit=limit_safe,
                query_deadline_seconds=query_deadline_seconds,
                args={
                    "source_id": str(plan.get("source_id") or ""),
                    "module": str(plan.get("module_filter") or ""),
                    "type": str(plan.get("source_type") or ""),
                    "sql": str(plan.get("sql") or ""),
                    "status": str(plan.get("status") or ""),
                    "separar_por_loja": bool(plan.get("separar_por_loja")),
                    "pack_id": str(plan.get("pack_id") or ""),
                    "order_id": str(plan.get("id_pedido") or ""),
                },
            )
            raw = {
                "function": f"codex_readonly_sources.{tool_id}",
                "arguments": {
                    "message": message,
                    "loja": loja or "",
                    "source_id": str(plan.get("source_id") or ""),
                    "module": str(plan.get("module_filter") or ""),
                    "type": str(plan.get("source_type") or ""),
                    "limit": limit_safe,
                    "sql": bool(plan.get("sql")),
                },
                "result": result,
            }
        elif tool_id == "program_functions_catalog":
            from backend.services import codex_capabilities

            result = codex_capabilities.list_capabilities(
                client_id=client_id,
                query=message,
                module=str(plan.get("module_filter") or ""),
                category=str(plan.get("category_filter") or ""),
                limit=limit_safe,
            )
            raw = {
                "function": "codex_capabilities.list_capabilities",
                "arguments": {
                    "query": message,
                    "module": str(plan.get("module_filter") or ""),
                    "category": str(plan.get("category_filter") or ""),
                    "limit": limit_safe,
                },
                "result": result,
            }
        elif tool_id == "capability_resolve":
            from backend.services import codex_capabilities

            result = codex_capabilities.resolve_capability(
                client_id=client_id,
                message=message,
                capability_id=str(plan.get("capability_id") or ""),
                module=str(plan.get("module_filter") or ""),
                category=str(plan.get("category_filter") or ""),
                params=plan.get("action_params") if isinstance(plan.get("action_params"), dict) else {},
                limit=min(limit_safe, 20),
            )
            raw = {
                "function": "codex_capabilities.resolve_capability",
                "arguments": {
                    "message": message,
                    "capability_id": str(plan.get("capability_id") or ""),
                    "module": str(plan.get("module_filter") or ""),
                    "category": str(plan.get("category_filter") or ""),
                },
                "result": result,
            }
        elif tool_id == "program_action_match":
            from backend.services import codex_actions

            result = codex_actions.match_action_dry_run(
                client_id=client_id,
                username="codex",
                message=message,
                action_id=str(plan.get("action_id") or ""),
                capability_id=str(plan.get("capability_id") or ""),
                params=plan.get("action_params") if isinstance(plan.get("action_params"), dict) else {},
                screen_context=screen_context if isinstance(screen_context, dict) else {},
                history=plan.get("history") if isinstance(plan.get("history"), list) else [],
            )
            if isinstance(result, dict) and result.get("matched"):
                result["rows"] = [
                    {
                        "action_id": ((result.get("action") or {}) if isinstance(result.get("action"), dict) else {}).get("id") or "",
                        "label": ((result.get("action") or {}) if isinstance(result.get("action"), dict) else {}).get("label") or "",
                        "params": result.get("params") or {},
                        "missing_params": result.get("missing_params") or [],
                        "proposal_preview": result.get("proposal_preview") if isinstance(result.get("proposal_preview"), dict) else {},
                        "requires_confirmation": True,
                    }
                ]
            raw = {
                "function": "codex_actions.match_action_dry_run",
                "arguments": {
                    "message": message,
                    "action_id": str(plan.get("action_id") or ""),
                    "capability_id": str(plan.get("capability_id") or ""),
                    "params": plan.get("action_params") if isinstance(plan.get("action_params"), dict) else {},
                },
                "result": result,
            }
        elif tool_id == "operational_dispatcher":
            dispatcher_results, _, dispatcher_warnings = _assistant_execute_dispatcher(client_id, message, screen_context)
            warnings.extend(dispatcher_warnings)
            raw_results.extend(dispatcher_results)
            for item in dispatcher_results:
                registry_results.append(_assistant_standard_result(tool_id, item, plan))
            if not dispatcher_results:
                registry_results.append(_assistant_standard_result(tool_id, None, plan))
            return raw_results, registry_results, warnings
        if isinstance(raw, dict):
            raw_results.append(raw)
            raw_result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
            for warning in raw_result.get("warnings") if isinstance(raw_result.get("warnings"), list) else []:
                warning_text = str(warning or "").strip()
                if warning_text and warning_text not in warnings:
                    warnings.append(warning_text[:600])
        registry_results.append(_assistant_standard_result(tool_id, raw, plan))
    except Exception as exc:
        warnings.append(f"{tool_id}: {str(exc)[:300]}")
        registry_results.append(_assistant_standard_result(tool_id, {"function": tool_id, "result": {"error": str(exc)[:300]}}, plan))
    return raw_results, registry_results, warnings


def _assistant_agent_int(value: Any, default: int, minimum: int = 1, maximum: int = 500) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(minimum, min(parsed, maximum))


def _assistant_agent_compact(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return str(value)[:160]
    if isinstance(value, str):
        text = value.strip()
        return text[:CODEX_AGENT_TEXT_VALUE_LIMIT]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_assistant_agent_compact(item, depth + 1) for item in value[:CODEX_AGENT_TOP_ROWS_LIMIT]]
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in list(value.items())[:80]:
            key_text = str(key)[:80]
            if key_text in {"context_text", "tool_context", "tool_results_preview", "html", "raw", "payload"}:
                clean[key_text] = str(item or "")[:1200]
                continue
            clean[key_text] = _assistant_agent_compact(item, depth + 1)
        return clean
    return str(value)[:CODEX_AGENT_TEXT_VALUE_LIMIT]


def _assistant_tool_validation(
    *,
    tool_id: str,
    records: int,
    sources: list[str],
    warnings: list[str],
    empty_reasons: list[str],
    next_fallbacks: list[str],
    registry_results: list[dict[str, Any]],
) -> dict[str, Any]:
    meta = _assistant_tool_meta(tool_id)
    primary_items = [
        item
        for item in registry_results
        if isinstance(item, dict) and str(item.get("tool_id") or "") == tool_id
    ]
    primary_record_count = sum(max(0, int(item.get("records") or 0)) for item in primary_items)

    def primary_complete(item: dict[str, Any]) -> bool:
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        chart = summary.get("chart_data") if isinstance(summary.get("chart_data"), dict) else {}
        exact = summary.get("exact_coverage") if isinstance(summary.get("exact_coverage"), dict) else {}
        paging = summary.get("paging") if isinstance(summary.get("paging"), dict) else {}
        if summary.get("error") or summary.get("partial_response") is True or summary.get("truncated") is True:
            return False
        if paging.get("has_more") is True:
            return False
        return bool(
            summary.get("coverage_complete") is True
            or chart.get("coverage_complete") is True
            or exact.get("complete") is True
        )

    authoritative_zero = bool(
        meta.get("zero_is_authoritative") is True
        and primary_items
        and primary_record_count == 0
        and all(primary_complete(item) for item in primary_items)
    )
    primary_stock_contract = False
    if tool_id == "stock_data":
        primary_stock_contract = any(
            isinstance(item.get("summary"), dict)
            and item["summary"].get("found") is True
            and str(item["summary"].get("sku") or "").strip()
            and all(
                isinstance(item["summary"].get(key), (int, float))
                and not isinstance(item["summary"].get(key), bool)
                for key in ("saldo_loja_total", "saldo_full_total", "saldo_total")
            )
            for item in primary_items
        )
    positive_sku_count_contract = False
    if tool_id == "bling_positive_stock_sku_count":
        positive_sku_count_contract = any(
            isinstance(row, dict)
            and isinstance(row.get("positive_sku_count"), (int, float))
            and not isinstance(row.get("positive_sku_count"), bool)
            and row.get("coverage_complete") is True
            for item in primary_items
            for row in (item.get("rows") if isinstance(item.get("rows"), list) else [])
        )
    attempted = [
        str(item.get("tool_id") or "").strip()
        for item in registry_results
        if isinstance(item, dict) and str(item.get("tool_id") or "").strip()
    ]
    attempted = list(dict.fromkeys(attempted))
    fields_missing: list[str] = []
    if records <= 0:
        fields_missing.append("registros")
    if not sources:
        fields_missing.append("fontes")
    if empty_reasons:
        fields_missing.append("evidencia_conclusiva")
    enough = records > 0
    if records <= 0 and next_fallbacks:
        enough = False
    if tool_id == "operational_memory_query" and records <= 0:
        enough = True
        fields_missing = []
    if authoritative_zero:
        enough = True
        fields_missing = []
    if tool_id == "stock_data":
        # Cadastro de produto e imagens podem ajudar a identificar o SKU, mas
        # nunca transformam uma consulta de saldo local vazia em evidência.
        enough = primary_stock_contract
        fields_missing = [] if primary_stock_contract else ["saldo_numerico_confirmado"]
    if tool_id == "bling_positive_stock_sku_count":
        enough = positive_sku_count_contract
        fields_missing = [] if positive_sku_count_contract else ["contagem_completa_de_skus"]
    live_question_summaries = []
    for item in registry_results:
        summary = item.get("summary") if isinstance(item, dict) and isinstance(item.get("summary"), dict) else {}
        if summary.get("live_query"):
            live_question_summaries.append(summary)
    live_question_query = bool(live_question_summaries)
    live_question_complete = live_question_query and all(bool(item.get("coverage_complete")) for item in live_question_summaries)
    live_question_partial_with_records = live_question_query and records > 0
    if live_question_query:
        enough = live_question_complete or live_question_partial_with_records
        fields_missing = [] if live_question_complete else ["cobertura_lojas"]
        if records <= 0 and not live_question_complete:
            fields_missing.insert(0, "registros")
    exact_event_summaries = []
    if tool_id in {"mercado_livre_orders", "mercado_livre_returns"}:
        for item in registry_results:
            if not isinstance(item, dict) or str(item.get("tool_id") or "") != tool_id:
                continue
            summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            if isinstance(summary.get("target"), dict) and isinstance(summary.get("exact_coverage"), dict):
                exact_event_summaries.append((item, summary))
    exact_event_query = bool(exact_event_summaries)
    exact_event_conclusive = False
    exact_event_empty = False
    exact_event_match = False
    exact_event_error = False
    if exact_event_query:
        exact_event_match = any(
            bool((summary.get("match") or {}).get("exact"))
            for _item, summary in exact_event_summaries
            if isinstance(summary.get("match"), dict)
        )
        exact_event_complete = all(
            bool((summary.get("exact_coverage") or {}).get("complete"))
            for _item, summary in exact_event_summaries
        )
        exact_event_error = any(bool(summary.get("error")) for _item, summary in exact_event_summaries)
        primary_records = sum(max(0, int(item.get("records") or 0)) for item, _summary in exact_event_summaries)
        exact_event_empty = bool(primary_records == 0 and exact_event_complete and not exact_event_error)
        exact_event_conclusive = bool(
            exact_event_complete
            and not exact_event_error
            and (exact_event_match or exact_event_empty)
        )
        enough = exact_event_conclusive
        fields_missing = [] if exact_event_conclusive else ["evidencia_conclusiva"]
        if not exact_event_complete:
            fields_missing.append("cobertura_conclusiva")
        if primary_records <= 0 and not exact_event_empty:
            fields_missing.insert(0, "registros")
        fields_missing = list(dict.fromkeys(fields_missing))
    confidence = (
        "alta"
        if exact_event_conclusive or live_question_complete or authoritative_zero or primary_stock_contract or positive_sku_count_contract
        else "media"
        if live_question_partial_with_records
        else "alta"
        if records > 0 and not empty_reasons
        else "media"
        if records > 0
        else "baixa"
    )
    if exact_event_query and not exact_event_conclusive:
        confidence = "baixa"
    if authoritative_zero and not exact_event_query and not live_question_query:
        reason = "A fonte primaria confirmou zero registros com cobertura completa."
    elif tool_id == "bling_positive_stock_sku_count":
        reason = (
            "Contagem de SKUs com saldo positivo confirmada em todo o catalogo da loja Bling."
            if positive_sku_count_contract
            else "A varredura da loja Bling ficou incompleta; a contagem nao pode ser tratada como exata."
        )
    elif tool_id == "stock_data":
        reason = (
            "Saldo numerico confirmado no estoque interno do JK Sistema."
            if primary_stock_contract
            else "O cadastro identificou o produto, mas nao confirmou um saldo numerico de estoque."
        )
    elif exact_event_query:
        reason = (
            "Evento exato confirmado diretamente na API do Mercado Livre."
            if exact_event_match and exact_event_conclusive
            else "A API do Mercado Livre confirmou que nao ha registro correspondente no periodo."
            if exact_event_empty
            else "A busca direta nao teve cobertura suficiente; historico local, quando presente, e apenas apoio separado."
        )
    elif live_question_query:
        reason = (
            "Fila atual do Mercado Livre confirmada em todas as lojas solicitadas."
            if live_question_complete
            else "Foram encontradas perguntas, mas uma ou mais lojas nao puderam ser confirmadas."
            if live_question_partial_with_records
            else "Nao foi possivel confirmar a fila atual em todas as lojas solicitadas."
        )
    else:
        reason = (
            "Dados suficientes para responder com as fontes retornadas."
            if enough
            else "Dados insuficientes; tentar fallback compativel antes de concluir."
        )
    return {
        "dados_suficientes": enough,
        "enough_data": enough,
        "campos_faltantes": fields_missing[:8],
        "fontes_usadas": _assistant_human_source_list(sources, tool_id)[:20],
        "fontes_usadas_raw": sources[:20],
        "fontes_usadas_humanas": _assistant_human_source_list(sources, tool_id)[:20],
        "fallbacks_tentados": _assistant_human_fallback_list(attempted)[:20],
        "fallbacks_tentados_ids": attempted[:20],
        "fallbacks_tentados_humanos": _assistant_human_fallback_list(attempted)[:20],
        "proximas_fontes": next_fallbacks[:10] if not enough else [],
        "proximas_fontes_humanas": _assistant_human_fallback_list(next_fallbacks[:10]) if not enough else [],
        "confidence": confidence,
        "motivo": reason,
        "tool_id": tool_id,
        "warnings": [_assistant_humanize_source_text(str(item or "")[:300]) for item in warnings[:8]],
    }


def _assistant_agent_fallback_ids(tool_id: str, message: str) -> list[str]:
    text = _assistant_texto_norm(message)
    meta = _assistant_tool_meta(tool_id)
    fallbacks: list[str] = []

    def add(*items: str) -> None:
        for item in items:
            item = str(item or "").strip()
            if item and item not in fallbacks:
                fallbacks.append(item)

    add(*[str(item or "") for item in (meta.get("fallbacks") or [])])
    if re.search(r"\b(venda|vendas|pedido|pedidos|ranking|mais vendido|top)\b", text):
        add("sales_returns_query", "local_database_query", "local_csv_query", "sync_logs_query", "bling_sales_orders", "mercado_livre_readonly")
    if re.search(r"\b(estoque|saldo|ruptura|reposicao|sku)\b", text):
        add("stock_data", "product_data", "local_csv_query", "local_cache_query", "bling_products", "bling_stock_balances", "mercado_livre_listing")
    if re.search(r"\b(pergunta|perguntas|pos venda|pos-venda|mercado livre|mercadolivre|mlb\d+|anuncio)\b", text):
        add("questions_post_sale_query", "mercado_livre_readonly", "local_cache_query", "product_data")
    if re.search(r"\b(fiscal|ncm|cest|nota|nfe|imposto|tributacao)\b", text):
        add("fiscal_local_query", "product_registry", "local_csv_query", "bling_fiscal_product", "bling_fiscal_nfe")
    add("source_discovery")
    return fallbacks[:12]


def _assistant_agent_result_package(
    client_id: str,
    tool_id: str,
    args: dict[str, Any],
    raw_results: list[dict[str, Any]],
    registry_results: list[dict[str, Any]],
    warnings: list[str],
    permissions: Any = None,
) -> dict[str, Any]:
    meta = _assistant_tool_meta(tool_id)
    rows: list[Any] = []
    primary_rows: list[Any] = []
    records = 0
    summaries: list[dict[str, Any]] = []
    sources: list[str] = []
    sources_human: list[str] = []
    empty_reasons: list[str] = []
    next_fallbacks: list[str] = []

    for item in registry_results:
        if not isinstance(item, dict):
            continue
        records += int(item.get("records") or 0)
        item_rows = item.get("rows") if isinstance(item.get("rows"), list) else []
        rows.extend(item_rows)
        if str(item.get("tool_id") or "") == tool_id:
            primary_rows.extend(item_rows)
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        summaries.append(
            {
                "tool_id": item.get("tool_id"),
                "tool_label": item.get("tool_label") or _assistant_human_tool_label(item.get("tool_id")),
                "module": item.get("module"),
                "records": int(item.get("records") or 0),
                "source": item.get("source") or "",
                "source_label": item.get("source_label") or _assistant_human_source_label(item.get("source") or "", item.get("tool_id")),
                "source_role": item.get("source_role") or "context",
                "aggregation_policy": item.get("aggregation_policy") or "standard",
                "periodo": item.get("periodo") or {},
                "loja": item.get("loja") or "",
                "summary": _assistant_agent_compact(summary),
            }
        )
        source = str(item.get("source") or "").strip()
        if source and source not in sources:
            sources.append(source)
        for label in _assistant_human_source_list(item.get("sources_human") or [source], item.get("tool_id")):
            if label and label not in sources_human:
                sources_human.append(label)
        empty_reason = str(item.get("empty_reason") or "").strip()
        if empty_reason and empty_reason not in empty_reasons:
            empty_reasons.append(empty_reason)
        for fallback in item.get("next_fallbacks") or []:
            fallback_id = str(fallback or "").strip()
            if (
                fallback_id
                and fallback_id not in next_fallbacks
                and _assistant_tool_allowed(fallback_id, permissions)
            ):
                next_fallbacks.append(fallback_id)

    if not sources:
        for raw in raw_results:
            source = str((raw or {}).get("function") or "").strip()
            if source and source not in sources:
                sources.append(source)
    if not sources_human:
        sources_human = _assistant_human_source_list(sources, tool_id)

    tool_validation = _assistant_tool_validation(
        tool_id=tool_id,
        records=records,
        sources=sources,
        warnings=warnings,
        empty_reasons=empty_reasons,
        next_fallbacks=next_fallbacks,
        registry_results=registry_results,
    )

    primary_exact_metadata = {}
    for item in registry_results:
        if not isinstance(item, dict) or str(item.get("tool_id") or "") != tool_id:
            continue
        candidate = item.get("exact_metadata")
        if isinstance(candidate, dict) and candidate.get("exact_lookup") is True:
            primary_exact_metadata = copy.deepcopy(candidate)
            break

    primary_chart_data: dict[str, Any] = {}
    chart_function_aliases = {
        "get_mercado_livre_orders": "mercado_livre_orders",
        "get_mercado_livre_listing": "mercado_livre_listing",
        "get_mercado_livre_visits": "mercado_livre_visits",
        "get_mercado_livre_promotions": "mercado_livre_promotions",
        "codex_readonly_sources.mercado_livre_post_sale_detail": "mercado_livre_post_sale_detail",
        "get_days_without_sale_top": "stale_stock",
        "get_stockout_forecast": "stockout_forecast",
        "get_sales_by_period": "sales_ranking",
        "get_period_comparison": "period_comparison",
        "get_sales_timeseries": "sales_timeseries",
    }
    matching_raw_results = [
        raw
        for raw in raw_results
        if isinstance(raw, dict)
        and chart_function_aliases.get(str(raw.get("function") or "").strip(), str(raw.get("function") or "").strip()) == tool_id
    ]
    if not matching_raw_results and len(raw_results) == 1:
        matching_raw_results = list(raw_results)
    for raw in matching_raw_results:
        raw_payload = raw.get("result") if isinstance(raw, dict) and isinstance(raw.get("result"), dict) else {}
        candidate = raw_payload.get("chart_data") if isinstance(raw_payload.get("chart_data"), dict) else {}
        if candidate:
            # Contrato agregado e sem PII produzido pelo provedor. Ele precisa
            # chegar inteiro ao renderizador; o resumo conversacional limita
            # listas e nao serve como fonte de uma serie completa.
            primary_chart_data = copy.deepcopy(candidate)
            break

    return {
        "success": True,
        "client_id": str(client_id or ""),
        "tool_id": tool_id,
        "tool_label": _assistant_human_tool_label(tool_id),
        "module": meta.get("module") or "",
        "description": meta.get("description") or "",
        "external": bool(meta.get("external")),
        "read_only": meta.get("read_only") is True,
        "args": {} if tool_id == "context_hub_search" or meta.get("sensitive") is True else _assistant_agent_compact(args),
        "records": records,
        "top_rows": _assistant_agent_compact(rows[:CODEX_AGENT_TOP_ROWS_LIMIT]),
        # O relatorio diario do WhatsApp precisa listar cada SKU retornado pela
        # API, e nao apenas a amostra usada pelo agente conversacional.
        "all_rows": (
            copy.deepcopy(primary_rows[:20000])
            if tool_id == "mercado_livre_orders"
            else copy.deepcopy(primary_rows[:100])
            if tool_id == "mercado_livre_listing"
            else []
        ),
        "chart_data": primary_chart_data,
        "exact_metadata": primary_exact_metadata,
        "summary": summaries[:12],
        "sources": sources_human[:20],
        "sources_raw": sources[:20],
        "sources_human": sources_human[:20],
        "source_label": (sources_human[:1] or [_assistant_human_tool_label(tool_id)])[0],
        "source_role": (
            "primary_api"
            if tool_id in {"bling_sales_orders", "bling_positive_stock_sku_count", "bling_stock_balances", "mercado_livre_resource_query", "mercado_livre_orders", "mercado_livre_returns", "mercado_livre_listing", "mercado_livre_visits", "mercado_livre_promotions", "mercado_livre_post_sale_detail", "mercado_livre_full_stock", "questions_post_sale_query"}
            else "supporting_local_history"
            if _assistant_is_generic_sales_api_query(str(args.get("message") or args.get("mensagem") or ""))
            and tool_id in {"sales_returns_query", "sales_ranking", "sales_summary"}
            else "context"
        ),
        "aggregation_policy": (
            "separate_sources_no_sum"
            if tool_id in {"bling_sales_orders", "bling_positive_stock_sku_count", "bling_stock_balances", "mercado_livre_resource_query", "mercado_livre_orders", "mercado_livre_returns", "mercado_livre_listing", "mercado_livre_visits", "mercado_livre_promotions", "mercado_livre_post_sale_detail", "mercado_livre_full_stock", "questions_post_sale_query"}
            or _assistant_is_generic_sales_api_query(str(args.get("message") or args.get("mensagem") or ""))
            else "standard"
        ),
        "warnings": [_assistant_humanize_source_text(str(item or "")[:600]) for item in warnings[:20]],
        "empty_reason": "; ".join(empty_reasons)[:900],
        "next_fallbacks": next_fallbacks[:10],
        "next_fallbacks_human": _assistant_human_fallback_list(next_fallbacks[:10]),
        "tool_validation": tool_validation,
        "dados_suficientes": bool(tool_validation.get("dados_suficientes")),
        "proximas_fontes": list(tool_validation.get("proximas_fontes") or []),
        "proximas_fontes_humanas": list(tool_validation.get("proximas_fontes_humanas") or []),
        "generated_at": _assistant_now(),
    }


def codex_assistant_execute_tool_call(
    client_id: str,
    tool_id: str,
    args: Optional[dict[str, Any]] = None,
    screen_context: Any = None,
    previous_results: Optional[list[dict[str, Any]]] = None,
    permissions: Any = None,
    audit_user: str = "",
    query_deadline: Optional[float] = None,
) -> dict[str, Any]:
    """Execute one registered read-only data tool for the Codex agent loop."""

    tool_id = str(tool_id or "").strip()
    args = dict(args or {}) if isinstance(args, dict) else {}
    meta = _assistant_tool_meta(tool_id)
    if not tool_id or not any(str(tool.get("id") or "") == tool_id for tool in CODEX_DATA_TOOLS):
        return {
            "success": False,
            "tool_id": tool_id,
            "error": "Ferramenta inexistente no Codex Data Tools Registry.",
            "generated_at": _assistant_now(),
        }
    if meta.get("read_only") is not True:
        return {
            "success": False,
            "tool_id": tool_id,
            "error": "Ferramenta mutavel bloqueada. Acoes mutaveis exigem aprovacao explicita.",
            "generated_at": _assistant_now(),
        }
    access = _assistant_tool_access(tool_id, permissions)
    if not access.get("allowed"):
        return {
            "success": False,
            "tool_id": tool_id,
            "module": meta.get("module") or "",
            "error": "Acesso negado: usuario sem permissao para consultar esta fonte de dados.",
            "error_code": "tool_permission_denied",
            "required_permissions": list(access.get("required_permissions") or []),
            "missing_permissions": list(access.get("missing_permissions") or []),
            "records": 0,
            "read_only": True,
            "generated_at": _assistant_now(),
        }

    message = str(
        args.get("message")
        or args.get("mensagem")
        or args.get("query")
        or args.get("pergunta")
        or args.get("recurso")
        or tool_id
    ).strip()
    mode = str(args.get("mode") or args.get("modo") or "").strip().lower()
    if mode not in {"chat", "report", "daily", "proactive"}:
        wants_report = bool(re.search(r"\b(relatorio|analise completa|ultimos?\s+\d+\s+dias?)\b", _assistant_texto_norm(message)))
        mode = "report" if wants_report else "chat"

    data_inicio = _assistant_calendar_date(args.get("data_inicio") or args.get("inicio") or args.get("start_date"))
    data_fim = _assistant_calendar_date(args.get("data_fim") or args.get("fim") or args.get("end_date"))
    if not (data_inicio and data_fim):
        data_inicio, data_fim = _assistant_resolve_period(client_id, message, screen_context)
    latest_kind = _assistant_latest_ml_event_kind(message)
    latest_ml_event = bool(
        tool_id in {"mercado_livre_orders", "mercado_livre_returns"}
        and latest_kind in {
            "both",
            "sale" if tool_id == "mercado_livre_orders" else "return",
        }
        and not re.search(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b", str(message or ""))
    )
    if latest_ml_event:
        data_inicio, data_fim = _assistant_periodo_padrao(365)
    prev_inicio, prev_fim = _assistant_previous_period(data_inicio, data_fim)
    if tool_id == "period_comparison":
        explicit_a = (
            _assistant_calendar_date(args.get("data_inicio_a") or args.get("inicio_a") or args.get("start_date_a")),
            _assistant_calendar_date(args.get("data_fim_a") or args.get("fim_a") or args.get("end_date_a")),
        )
        explicit_b = (
            _assistant_calendar_date(args.get("data_inicio_b") or args.get("inicio_b") or args.get("start_date_b")),
            _assistant_calendar_date(args.get("data_fim_b") or args.get("fim_b") or args.get("end_date_b")),
        )
        if all((*explicit_a, *explicit_b)) and explicit_a[0] <= explicit_a[1] and explicit_b[0] <= explicit_b[1]:
            # O agente pode chamar A=periodo atual e B=periodo anterior. Para
            # que a variacao seja sempre intuitiva, a ferramenta recebe os
            # periodos em ordem cronologica: anterior primeiro, atual depois.
            if explicit_a[0] <= explicit_b[0]:
                (prev_inicio, prev_fim), (data_inicio, data_fim) = explicit_a, explicit_b
            else:
                (prev_inicio, prev_fim), (data_inicio, data_fim) = explicit_b, explicit_a
    loja = str(args.get("loja") or args.get("conta") or args.get("store") or "").strip()
    if not loja:
        loja = _assistant_resolve_loja(client_id, message, screen_context)
    sku = _assistant_normalize_sku(args.get("sku") or args.get("codigo") or args.get("seller_sku") or "")
    if not sku:
        sku = _assistant_extract_sku_filter(message, screen_context)
    item_id = _assistant_normalize_ml_item_id(args.get("item_id") or args.get("mlb") or args.get("id_anuncio"))
    if not item_id:
        item_match = re.search(r"\bMLB[\s_-]?\d{6,}\b", str(message or ""), flags=re.IGNORECASE)
        item_id = _assistant_normalize_ml_item_id(item_match.group(0) if item_match else "")
    id_pedido = _assistant_normalize_identifier(
        args.get("id_pedido") or args.get("pedido_id") or args.get("order_id") or args.get("id_order")
    )
    if not id_pedido:
        pedido_match = re.search(
            r"\b(?:pedido|order)\s*(?:id|numero|n\.?|#)?\s*[:#-]?\s*(\d{3,})\b",
            str(message or ""),
            flags=re.IGNORECASE,
        )
        id_pedido = _assistant_normalize_identifier(pedido_match.group(1) if pedido_match else "")
    if not id_pedido and tool_id == "mercado_livre_orders":
        standalone_ids = re.findall(r"\b\d{10,20}\b", str(message or ""))
        if (
            len(standalone_ids) == 1
            and re.search(r"\b(venda|pedido|order|pack|compra)\b", _assistant_texto_norm(message))
        ):
            id_pedido = _assistant_normalize_identifier(standalone_ids[0])
    pack_id = _assistant_normalize_identifier(args.get("pack_id") or args.get("pack") or "")
    if not pack_id:
        pack_match = re.search(
            r"\bpack(?:\s*(?:id|numero|#))?\s*[:#-]?\s*(\d{5,30})\b",
            str(message or ""),
            flags=re.IGNORECASE,
        )
        pack_id = _assistant_normalize_identifier(pack_match.group(1) if pack_match else "")
    default_status = ""
    if tool_id == "mercado_livre_listing":
        default_status = "active"
    elif tool_id == "mercado_livre_orders":
        default_status = "paid,partially_refunded"
    status = _assistant_normalize_api_status(args.get("status") or args.get("situacao"), default_status)
    offset = _assistant_agent_int(args.get("offset"), 0, 0, 10000)
    incluir_detalhes = _assistant_bool_arg(
        args.get("incluir_detalhes", args.get("include_details", args.get("incluir_descricao"))),
        False,
    )
    routing_policy = _assistant_source_routing_policy(message)
    incluir_comercial = _assistant_bool_arg(
        args.get("incluir_comercial", args.get("include_commercial")),
        bool(routing_policy.get("include_commercial_detail")),
    )
    strict_latest_ml_event = bool(
        latest_ml_event and tool_id in {"mercado_livre_orders", "mercado_livre_returns"}
    )
    if strict_latest_ml_event:
        offset = 0
    force_refresh = bool(
        _assistant_force_refresh_requested(args, message) or strict_latest_ml_event
    )
    if meta.get("external") is True and query_deadline is None:
        query_deadline = time.monotonic() + API_QUERY_TIMEOUT_SECONDS
    api_sales_query = _assistant_is_generic_sales_api_query(message)
    if tool_id in {"program_functions_catalog", "capability_resolve"}:
        default_limit = 1000
        maximum_limit = 1000
    elif tool_id == "context_hub_search":
        request_surface = str(args.get("request_surface") or "").strip().casefold()
        default_limit = 8 if request_surface in {"whatsapp", "black_jhon_whatsapp"} else 12
        maximum_limit = default_limit
    elif tool_id == "mercado_livre_full_stock":
        default_limit = 10000
        maximum_limit = 20000
    elif tool_id == "mercado_livre_returns":
        default_limit = 1
        maximum_limit = 100
    elif tool_id in {"mercado_livre_listing", "mercado_livre_resource_query"}:
        default_limit = 20
        maximum_limit = 100
    elif tool_id == "mercado_livre_promotions":
        default_limit = 20
        maximum_limit = 50
    elif tool_id == "mercado_livre_post_sale_detail":
        default_limit = 50
        maximum_limit = 100
    elif tool_id == "mercado_livre_orders":
        default_limit = 20_000 if mode in {"report", "daily"} else 50
        maximum_limit = 20_000 if mode in {"report", "daily"} else 100
    else:
        default_limit = CODEX_AGENT_REPORT_ROW_LIMIT if mode in {"report", "daily"} else CODEX_AGENT_NORMAL_ROW_LIMIT
        maximum_limit = 500
    limit_safe = _assistant_agent_int(args.get("limite") or args.get("limit"), default_limit, 1, maximum_limit)
    if strict_latest_ml_event:
        limit_safe = 1

    plan = {
        "intent": tool_id,
        "mode": mode,
        "message": message,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
        "periodo_anterior": {"data_inicio": prev_inicio, "data_fim": prev_fim},
        "loja": loja,
        "sku": sku,
        "item_id": item_id,
        "mlb": item_id,
        "id_pedido": id_pedido,
        "pack_id": pack_id,
        "status": status,
        "offset": offset,
        "incluir_detalhes": incluir_detalhes,
        "incluir_comercial": incluir_comercial,
        "dias": _assistant_agent_int(args.get("dias") or args.get("days"), 30, 1, 150),
        "promotion_id": str(args.get("promotion_id") or args.get("promocao_id") or "").strip()[:120],
        "resource_id": str(args.get("resource_id") or args.get("recurso_id") or args.get("endpoint_id") or "").strip()[:160],
        "resource_params": (
            dict(args.get("params"))
            if isinstance(args.get("params"), dict)
            else dict(args.get("parametros"))
            if isinstance(args.get("parametros"), dict)
            else {}
        ),
        "incluir_contagens": _assistant_bool_arg(args.get("incluir_contagens", args.get("include_counts")), False),
        "force_refresh": force_refresh,
        "query_deadline": query_deadline,
        "api_sales_query": api_sales_query,
        "source_role": (
            "primary_api"
            if tool_id in {"bling_sales_orders", "bling_positive_stock_sku_count", "mercado_livre_orders", "mercado_livre_returns", "mercado_livre_listing", "mercado_livre_visits", "mercado_livre_promotions", "mercado_livre_post_sale_detail"}
            else "supporting_local_history"
            if api_sales_query and tool_id in {"sales_returns_query", "sales_ranking", "sales_summary"}
            else "context"
        ),
        "aggregation_policy": (
            "single_store_scalar_no_sum"
            if tool_id == "bling_positive_stock_sku_count"
            else "separate_sources_no_sum"
            if api_sales_query
            else "standard"
        ),
        "limite": limit_safe,
        "max_paginas": _assistant_agent_int(
            args.get("max_paginas") or args.get("max_pages"),
            400 if mode in {"report", "daily"} else 2,
            1,
            400 if mode in {"report", "daily"} else 2,
        ),
        "module_filter": str(args.get("modulo") or args.get("module") or args.get("module_filter") or "").strip(),
        "category_filter": str(args.get("categoria") or args.get("category") or args.get("category_filter") or "").strip(),
        "capability_id": str(args.get("capability_id") or args.get("capacidade_id") or "").strip(),
        "source_id": str(args.get("source_id") or args.get("fonte") or "").strip(),
        "source_type": str(args.get("source_type") or args.get("tipo") or args.get("type") or "").strip(),
        "context_ids": [
            str(item).strip()
            for item in (
                args.get("ids")
                if isinstance(args.get("ids"), list)
                else args.get("entity_ids")
                if isinstance(args.get("entity_ids"), list)
                else []
            )[:50]
            if str(item or "").strip()
        ],
        "environment_filter": str(args.get("environment") or args.get("ambiente") or "").strip(),
        "sql": str(args.get("sql") or "").strip(),
        "include_routes": bool(args.get("incluir_rotas", args.get("include_routes", True))),
        "include_services": bool(args.get("incluir_servicos", args.get("include_services", True))),
        "include_actions": bool(args.get("incluir_acoes", args.get("include_actions", True))),
        "include_pages": bool(args.get("incluir_telas", args.get("include_pages", True))),
        "action_id": str(args.get("action_id") or "").strip(),
        "action_params": args.get("params") if isinstance(args.get("params"), dict) else {},
        "history": args.get("history") if isinstance(args.get("history"), list) else [],
        "separar_por_loja": bool(
            args.get("separar_por_loja") or args.get("todas_lojas") or args.get("all_stores")
        ) or _assistant_wants_store_breakdown(message, loja),
        "incluir_registros": bool(args.get("incluir_registros", True)),
        "request_surface": str(args.get("request_surface") or "").strip().casefold(),
        "selected_tools": [tool_id],
    }

    external_cache_key = ""
    external_cache_ttl = 0
    recent_cached: Optional[dict[str, Any]] = None
    if meta.get("external") is True and meta.get("sensitive") is not True:
        external_cache_ttl = max(1, min(int(meta.get("cache_ttl_seconds") or EXTERNAL_CACHE_SECONDS), EXTERNAL_CACHE_SECONDS))
        external_cache_key = _assistant_external_cache_key(client_id, tool_id, plan)
        cached = _assistant_cache_get(client_id, external_cache_key, external_cache_ttl)
        if isinstance(cached, dict) and str(cached.get("tool_id") or "") == tool_id:
            recent_cached = copy.deepcopy(cached)
            if not force_refresh:
                cached_result = copy.deepcopy(cached)
                cached_result["cache_hit"] = True
                cached_result["cache_ttl_seconds"] = external_cache_ttl
                _assistant_api_query_audit(
                    client_id,
                    tool_id,
                    plan,
                    cached_result,
                    audit_user=audit_user,
                    cache_hit=True,
                )
                return cached_result

    registry_seed = list(previous_results or []) if isinstance(previous_results, list) else []
    raw_results: list[dict[str, Any]] = []
    registry_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    executed_tool_ids: set[str] = set()

    prereq_ids: list[str] = []
    if tool_id in {"bling_fiscal_product", "bling_lots", "bling_lot_movements"}:
        prereq_ids = ["product_data", "product_registry", "bling_product"]

    for prereq_id in prereq_ids:
        if not _assistant_tool_allowed(prereq_id, permissions):
            warnings.append(f"Pre-requisito omitido por permissao insuficiente: {prereq_id}.")
            continue
        executed_tool_ids.add(prereq_id)
        raw, registry, local_warnings = _assistant_execute_registry_tool(
            client_id,
            prereq_id,
            message,
            screen_context,
            {**plan, "selected_tools": [prereq_id]},
            registry_seed + registry_results,
        )
        raw_results.extend(raw)
        registry_results.extend(registry)
        warnings.extend(local_warnings)
    executed_tool_ids.add(tool_id)
    raw, registry, local_warnings = _assistant_execute_registry_tool(
        client_id,
        tool_id,
        message,
        screen_context,
        plan,
        registry_seed + registry_results,
    )
    raw_results.extend(raw)
    registry_results.extend(registry)
    warnings.extend(local_warnings)

    if tool_id == "operational_memory_query":
        meaningful_results = [item for item in registry_results if isinstance(item, dict)]
    else:
        meaningful_results = [
            item for item in registry_results
            if isinstance(item, dict)
            and str(item.get("tool_id") or "") not in {"capability_resolve", "program_action_match", "program_functions_catalog", "operational_memory_query"}
        ]
    has_records = any(int(item.get("records") or 0) > 0 for item in meaningful_results or registry_results)
    primary_exact_conclusive = False
    if strict_latest_ml_event:
        for item in meaningful_results or registry_results:
            if not isinstance(item, dict) or str(item.get("tool_id") or "") != tool_id:
                continue
            summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            coverage = summary.get("exact_coverage") if isinstance(summary.get("exact_coverage"), dict) else {}
            match = summary.get("match") if isinstance(summary.get("match"), dict) else {}
            paging = summary.get("paging") if isinstance(summary.get("paging"), dict) else {}
            latest_api_conclusive = bool(
                not summary.get("error")
                and summary.get("partial_response") is not True
                and int(paging.get("offset") or 0) == 0
                and (int(item.get("records") or 0) > 0 or paging.get("has_more") is False)
            )
            if (
                latest_api_conclusive
                or (
                    coverage.get("complete") is True
                    and not summary.get("error")
                    and (match.get("exact") is True or int(item.get("records") or 0) == 0)
                )
            ):
                primary_exact_conclusive = True
                break
    needs_supporting_history = (
        not primary_exact_conclusive
        if strict_latest_ml_event
        else not has_records
    )
    if (
        needs_supporting_history
        and not strict_latest_ml_event
        and tool_id in {"bling_sales_orders", "mercado_livre_orders", "mercado_livre_returns"}
        and (tool_id != "mercado_livre_returns" or strict_latest_ml_event)
        and not (tool_id == "mercado_livre_orders" and bool(plan.get("id_pedido")))
        and _assistant_tool_allowed("sales_returns_query", permissions)
        and not _assistant_previous_contains_tool(previous_results, "sales_returns_query")
    ):
        warnings.append(
            "A API primaria nao confirmou o registro exato; o historico local foi consultado apenas como apoio separado, sem substituir a confirmacao do Mercado Livre."
        )
        support_plan = {
            **plan,
            "selected_tools": ["sales_returns_query"],
            "source_role": "supporting_local_history",
            "source_roles": {"sales_returns_query": "supporting_local_history"},
            "aggregation_policy": "separate_sources_no_sum",
        }
        raw, registry, local_warnings = _assistant_execute_registry_tool(
            client_id,
            "sales_returns_query",
            message,
            screen_context,
            support_plan,
            registry_seed + registry_results,
        )
        for support_item in registry:
            if isinstance(support_item, dict):
                support_item["next_fallbacks"] = []
                support_item["next_fallbacks_human"] = []
        raw_results.extend(raw)
        registry_results.extend(registry)
        warnings.extend(local_warnings)
    if not has_records and tool_id != "operational_memory_query" and meta.get("zero_is_authoritative") is not True:
        auth_failure = _assistant_non_retryable_auth_failure(warnings, registry_results)
        fallback_limit = 5 if mode in {"report", "daily"} else 3
        fallback_count = 0
        for fallback_id in _assistant_agent_fallback_ids(tool_id, message):
            if fallback_count >= fallback_limit:
                break
            if fallback_id in executed_tool_ids:
                continue
            fallback_meta = _assistant_tool_meta(fallback_id)
            if (
                not fallback_meta.get("id")
                or fallback_meta.get("read_only") is not True
                or not _assistant_tool_allowed(fallback_id, permissions)
            ):
                continue
            if auth_failure and fallback_meta.get("external") is True:
                continue
            executed_tool_ids.add(fallback_id)
            fallback_count += 1
            warnings.append(f"Fallback automatico apos dados insuficientes: {fallback_id}.")
            raw, registry, local_warnings = _assistant_execute_registry_tool(
                client_id,
                fallback_id,
                message,
                screen_context,
                {**plan, "selected_tools": [fallback_id]},
                registry_seed + registry_results,
            )
            raw_results.extend(raw)
            registry_results.extend(registry)
            warnings.extend(local_warnings)
            if any(int(item.get("records") or 0) > 0 for item in registry):
                break

    result_package = _assistant_agent_result_package(
        client_id,
        tool_id,
        args,
        raw_results,
        registry_results,
        warnings,
        permissions=permissions,
    )
    result_package["cache_hit"] = False
    strict_live_sale_lookup = bool(
        tool_id == "mercado_livre_orders"
        and (strict_latest_ml_event or bool(plan.get("id_pedido")))
    )
    if recent_cached is not None and not strict_live_sale_lookup and _assistant_retryable_api_error(result_package):
        cached_result = copy.deepcopy(recent_cached)
        cached_result["cache_hit"] = True
        cached_result["cache_fallback"] = True
        cached_result["cache_ttl_seconds"] = external_cache_ttl
        cached_warnings = list(cached_result.get("warnings") or [])
        cached_warnings.append(
            "A atualizacao da API falhou temporariamente; usei o cache recente de ate 2 minutos e mantive a fonte identificada."
        )
        cached_result["warnings"] = cached_warnings[:20]
        _assistant_api_query_audit(
            client_id,
            tool_id,
            plan,
            cached_result,
            audit_user=audit_user,
            cache_hit=True,
        )
        return cached_result
    _assistant_api_query_audit(
        client_id,
        tool_id,
        plan,
        result_package,
        audit_user=audit_user,
        cache_hit=False,
    )
    if external_cache_key and external_cache_ttl and not _assistant_api_error_code(result_package):
        result_package["cache_ttl_seconds"] = external_cache_ttl
        safe_payload = _assistant_cache_safe_payload(result_package)
        if isinstance(safe_payload, dict):
            try:
                _assistant_cache_set(client_id, external_cache_key, safe_payload)
            except Exception:
                # A consulta continua valida se o cache local estiver
                # temporariamente indisponivel.
                pass
    return result_package


def _assistant_execute_registry(
    client_id: str,
    message: str,
    screen_context: Any,
    mode: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[str]]:
    plan = _assistant_registry_plan(client_id, message, screen_context, mode)
    if any(str(item or "") in _ASSISTANT_AUDITED_API_TOOLS for item in (plan.get("selected_tools") or [])):
        plan["query_deadline"] = time.monotonic() + API_QUERY_TIMEOUT_SECONDS
    raw_results: list[dict[str, Any]] = []
    registry_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    executed: set[str] = set()

    def run(tool_id: str) -> None:
        if tool_id in executed:
            return
        executed.add(tool_id)
        raw, registry, local_warnings = _assistant_execute_registry_tool(
            client_id,
            tool_id,
            message,
            screen_context,
            plan,
            registry_results,
        )
        raw_results.extend(raw)
        registry_results.extend(registry)
        warnings.extend(local_warnings)
        if tool_id in _ASSISTANT_AUDITED_API_TOOLS:
            audited_item = next(
                (item for item in reversed(registry) if isinstance(item, dict) and str(item.get("tool_id") or "") == tool_id),
                {},
            )
            audit_summary = audited_item.get("summary") if isinstance(audited_item.get("summary"), dict) else {}
            selection = screen_context.get("selection") if isinstance(screen_context, dict) and isinstance(screen_context.get("selection"), dict) else {}
            _assistant_api_query_audit(
                client_id,
                tool_id,
                plan,
                {
                    "records": int(audited_item.get("records") or 0),
                    "warnings": list(local_warnings or []),
                    "summary": [{"tool_id": tool_id, "summary": audit_summary}],
                },
                audit_user=str(selection.get("username") or selection.get("user") or "system"),
                cache_hit=False,
            )

    for tool_id in plan.get("selected_tools") or []:
        run(str(tool_id))

    empty_sales = any(item.get("tool_id") == "sales_ranking" and int(item.get("records") or 0) == 0 for item in registry_results)
    if empty_sales:
        fallback_ids = ["integrations_status", "mercado_livre_listing"]
        if "bling" in _assistant_texto_norm(message):
            fallback_ids.insert(1, "bling_sales_orders")
        for fallback_id in fallback_ids:
            run(fallback_id)
        warnings.append(
            "Ranking de vendas local retornou vazio; foram executadas consultas read-only de diagnostico/fallback. "
            "Sincronizar pedidos ou atualizar bancos continua exigindo aprovacao."
        )
    source_tool_ids = {
        "source_discovery",
        "local_database_query",
        "local_csv_query",
        "local_cache_query",
        "sync_logs_query",
        "mercado_livre_readonly",
        "questions_post_sale_query",
        "fiscal_local_query",
        "operational_memory_query",
    }
    ignored_empty = {"capability_resolve", "program_action_match", "program_functions_catalog", "integrations_status", "operational_memory_query"}
    any_empty_specialized = any(
        item.get("tool_id") not in source_tool_ids
        and item.get("tool_id") not in ignored_empty
        and _assistant_tool_meta(str(item.get("tool_id") or "")).get("zero_is_authoritative") is not True
        and int(item.get("records") or 0) == 0
        for item in registry_results
    )
    if any_empty_specialized:
        text_norm = _assistant_texto_norm(message)
        fallback_ids = ["source_discovery"]
        if re.search(r"\b(log|erro|falha|sync|sincronizacao|nao aparecem|nao apareceu)\b", text_norm):
            fallback_ids.append("sync_logs_query")
        if re.search(r"\b(pergunta|perguntas|pos venda|pos-venda|mercado livre|mercadolivre|mlb\d+|anuncio)\b", text_norm):
            fallback_ids.extend(["questions_post_sale_query", "mercado_livre_readonly"])
        if re.search(r"\b(fiscal|ncm|cest|nota|nfe|imposto|tributacao)\b", text_norm):
            fallback_ids.append("fiscal_local_query")
        fallback_ids.extend(["local_database_query", "local_csv_query", "local_cache_query"])
        for fallback_id in list(dict.fromkeys(fallback_ids)):
            run(fallback_id)
        warnings.append(
            "Uma ou mais consultas especializadas retornaram vazio; executei fallbacks read-only em fontes locais "
            "e logs antes de finalizar a resposta."
        )
    plan["executed_tools"] = list(executed)
    plan["status_steps"] = list(dict.fromkeys((plan.get("status_steps") or []) + ["gerando resposta"]))
    return raw_results, registry_results, plan, warnings


def _assistant_registry_context_text(plan: dict[str, Any], registry_results: list[dict[str, Any]]) -> str:
    if not registry_results:
        return ""
    lines = [
        "Codex Data Tools Registry executado em modo read-only:",
        f"Intencao: {plan.get('intent') or '-'}",
        f"Periodo: {plan.get('data_inicio') or '-'} a {plan.get('data_fim') or '-'}",
        f"Loja/conta: {plan.get('loja') or 'todas'}",
    ]
    for item in registry_results[:30]:
        tool_label = item.get("tool_label") or _assistant_human_tool_label(item.get("tool_id"))
        source_label = item.get("source_label") or _assistant_human_source_label(item.get("source") or "", item.get("tool_id"))
        lines.append(
            f"- {tool_label} [{item.get('module')}]: {int(item.get('records') or 0)} registro(s); "
            f"fonte {source_label or '-'}; externo {'sim' if item.get('external') else 'nao'}."
        )
        if item.get("empty_reason"):
            lines.append(f"  vazio: {item.get('empty_reason')}")
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        if item.get("tool_id") == "sales_returns_query" and summary.get("context_text"):
            lines.append("  dados detalhados:")
            lines.append(str(summary.get("context_text") or "")[:CODEX_SALES_RETURNS_CONTEXT_CHAR_LIMIT])
            continue
        rows = item.get("rows") if isinstance(item.get("rows"), list) else []
        if rows:
            lines.append("  amostra:")
            for row in rows[:8]:
                lines.append(
                    "  - "
                    + codex_turn_context.bounded_json(
                        row,
                        max_bytes=900,
                        priority_paths=("field", "value", "source", "coverage", "status", "reference"),
                    )
                )
        if summary:
            lines.append(
                "  resumo: "
                + codex_turn_context.bounded_json(
                    summary,
                    max_bytes=1200,
                    priority_paths=("field", "value", "source", "coverage", "status", "reference"),
                )
            )
    return "\n".join(lines)[:CODEX_DATA_CONTEXT_CHAR_LIMIT]


def _assistant_direct_tools(client_id: str, mode: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    inicio_30, fim = _assistant_periodo_padrao(30)
    inicio_90, fim_90 = _assistant_periodo_padrao(90)
    fim_date = date.today()
    atual_inicio = (fim_date - timedelta(days=29)).isoformat()
    atual_fim = fim_date.isoformat()
    anterior_fim_date = fim_date - timedelta(days=30)
    anterior_inicio = (anterior_fim_date - timedelta(days=29)).isoformat()
    anterior_fim = anterior_fim_date.isoformat()
    estoque_limite = 100 if mode in {"daily", "report"} else 50
    parado_limite = 500 if mode in {"daily", "report"} else 60

    candidates = [
        _assistant_call_ia_tool("_ia_tool_get_integrations_status", client_id, None),
        _assistant_call_ia_tool("_ia_tool_get_sales_by_period", client_id, inicio_30, fim, None, 10),
        _assistant_call_ia_tool("_ia_tool_get_returns_by_period", client_id, inicio_30, fim, None, 10),
        _assistant_call_ia_tool("_ia_tool_get_stockout_forecast", client_id, "previsao ruptura estoque", None, None, 30, estoque_limite),
        _assistant_call_ia_tool("_ia_tool_get_days_without_sale_top", client_id, None, parado_limite, True, False, True),
    ]
    if mode in {"daily", "report"}:
        candidates.extend(
            [
                _assistant_call_ia_tool("_ia_tool_get_sales_quantity_by_period", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_get_returns_quantity_by_period", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_get_return_rate_by_period", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_get_profit_by_period", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_detect_sales_anomalies", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_get_avg_ticket_by_period", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_get_sales_timeseries", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_get_period_comparison", client_id, anterior_inicio, anterior_fim, atual_inicio, atual_fim, None),
                _assistant_call_ia_tool("_ia_tool_get_mercado_livre_listing", client_id, "listar anuncios ativos mercado livre", None, None, 20),
            ]
        )
    for item in candidates:
        if isinstance(item, dict):
            results.append(item)
    return results


def _assistant_prompt_queries(message: str, mode: str) -> list[str]:
    text = _assistant_texto_norm(message)
    queries = []
    if str(message or "").strip():
        queries.append(str(message or "").strip())
    wants_broad = bool(re.search(r"\b(analise|analisar|relatorio|relatorio|oportunidade|melhoria|melhorias|vendas|estoque|devolucao|devolucoes|ruptura|margem|lucro)\b", text))
    wants_external = any(word in text for word in ("mercado livre", "mercadolivre", "bling", "anuncio", "integracao", "integracoes"))
    if wants_external:
        queries.append("status das integracoes Mercado Livre e Bling; consultar Mercado Livre ou Bling quando houver SKU, item ou produto informado.")
    if wants_broad or mode in {"daily", "report"}:
        queries.append(
            "analise especialista de vendas, estoque, devolucoes, margem, lucro, anomalias, ruptura de estoque "
            "e produtos com estoque sem venda nos ultimos 30 dias."
        )
    if mode == "proactive":
        queries.append(
            "verificar alertas operacionais leves: integracoes desconectadas, anomalias de vendas, devolucoes, "
            "previsao de ruptura e produtos com estoque sem venda."
        )
    return queries[:4]


def _assistant_execute_dispatcher(client_id: str, message: str, screen_context: Any) -> tuple[list[dict[str, Any]], str, list[str]]:
    warnings: list[str] = []
    try:
        from backend.schemas.ia import IAChatRequest
        from backend.services import ia as ia_service

        contexto = dict(screen_context) if isinstance(screen_context, dict) else {}
        page = _assistant_context_page(contexto)
        payload = IAChatRequest(
            message=str(message or ""),
            page=page,
            modulo=page,
            context=contexto,
            history=[],
        )
        tool_results = ia_service._ia_chat_executar_funcoes(payload, client_id)
        payload.tool_results = tool_results
        tool_context = ia_service._ia_chat_contexto_funcoes(payload, client_id) if tool_results else ""
        return list(tool_results or []), str(tool_context or ""), warnings
    except Exception as exc:
        warnings.append(str(exc)[:300])
        return [], "", warnings


def _assistant_dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    clean: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        key_raw = json.dumps(
            {
                "function": item.get("function"),
                "arguments": item.get("arguments"),
                "result": item.get("result"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )[:5000]
        key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        clean.append(item)
    return clean


def _assistant_result_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if not isinstance(value, dict):
        return 0
    try:
        total_registros = int(value.get("total_registros") or 0)
        if total_registros:
            return total_registros
    except Exception:
        pass
    # Comparacoes validas sao agregados com dois objetos, nao listas. Sem
    # este reconhecimento o executor marcava o resultado como vazio e
    # disparava um fallback desnecessario para sales_ranking.
    if isinstance(value.get("periodo_a"), dict) and isinstance(value.get("periodo_b"), dict):
        return 2
    for key in (
        "rows",
        "results",
        "campaigns",
        "by_sku",
        "por_sku",
        "vendas",
        "devolucoes",
        "por_loja",
        "items",
        "itens",
        "matches",
        "lojas",
        "top_skus",
        "produtos",
        "produtos_fiscais",
        "saldos",
        "depositos",
        "pedidos",
        "orders",
        "notas",
        "naturezas",
        "lotes",
        "lancamentos",
        "financeiro",
        "resources",
        "sources",
        "capabilities",
        "candidates",
        "routes",
        "services",
        "actions",
        "pages",
        "modules",
        "functions",
        "missing_params",
        "anomalias",
        "alertas",
        "pontos",
    ):
        if isinstance(value.get(key), list):
            return len(value.get(key) or [])
    total = (
        value.get("record_count")
        or value.get("records")
        or value.get("count")
        or value.get("total_registros")
        or value.get("total")
        or value.get("total_lojas")
        or value.get("total_skus_analisados")
        or value.get("total_skus_avaliados")
    )
    try:
        total_int = int(total or 0)
        if total_int:
            return total_int
    except Exception:
        pass
    scalar_keys = (
        "quantidade_total",
        "quantidade_vendida_total",
        "quantidade_devolvida_total",
        "valor_total",
        "valor_vendido_total",
        "valor_devolvido_total",
        "pedidos_total",
        "lucro_estimado",
        "ticket_medio",
        "taxa_devolucao_quantidade_percentual",
        "taxa_devolucao_valor_percentual",
    )
    if any(value.get(key) not in (None, "", 0, 0.0) for key in scalar_keys):
        return 1
    return 0


def _assistant_function_name(item: dict[str, Any]) -> str:
    name = str((item or {}).get("function") or "consulta").strip()
    if name.startswith("_ia_tool_"):
        name = name[len("_ia_tool_"):]
    return name or "consulta"


def _assistant_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _assistant_money(value: Any) -> str:
    amount = _assistant_float(value)
    raw = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {raw}"


def _assistant_percent(value: Any) -> str:
    return f"{_assistant_float(value):.1f}%".replace(".", ",")


def _assistant_find_result(results: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for item in results:
        if not isinstance(item, dict):
            continue
        if _assistant_function_name(item) == name:
            result = item.get("result")
            return result if isinstance(result, dict) else {}
    return {}


def _assistant_first_list(result: dict[str, Any], *keys: str) -> list[Any]:
    if not isinstance(result, dict):
        return []
    for key in keys:
        value = result.get(key)
        if isinstance(value, list):
            return value
    return []


def _assistant_sku_label(item: dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return "-"
    sku = str(item.get("sku") or item.get("seller_sku") or item.get("id") or "-").strip() or "-"
    nome = str(item.get("produto") or item.get("nome") or item.get("title") or "").strip()
    return f"{sku} - {nome}" if nome else sku


def _assistant_qty(value: Any, decimals: int = 1) -> str:
    amount = _assistant_float(value)
    if abs(amount - round(amount)) < 0.0001:
        return str(int(round(amount)))
    return f"{amount:.{decimals}f}".replace(".", ",")


def _assistant_risk_label(value: Any) -> str:
    risk = _assistant_texto_norm(value)
    labels = {
        "critico": "Critico",
        "alto": "Alto",
        "medio": "Medio",
        "baixo": "Baixo",
        "sem_consumo": "Sem consumo recente",
    }
    return labels.get(risk, str(value or "-").strip() or "-")


def _assistant_stockout_store_metrics(item: dict[str, Any], data_referencia: Any = "") -> dict[str, Any]:
    saldo = _assistant_float(item.get("saldo_loja"))
    media = _assistant_float(item.get("media_venda_dia"))
    dias = None
    data_prevista = ""
    risco = "sem_consumo"
    if media > 0:
        dias = saldo / media if saldo > 0 else 0.0
        try:
            ref = date.fromisoformat(str(data_referencia or "")[:10]) if data_referencia else date.today()
        except Exception:
            ref = date.today()
        data_prevista = (ref + timedelta(days=max(0, int(math.ceil(dias))))).isoformat()
        if dias <= 7:
            risco = "critico"
        elif dias <= 15:
            risco = "alto"
        elif dias <= 30:
            risco = "medio"
        else:
            risco = "baixo"
    return {
        "saldo_considerado": saldo,
        "media_venda_dia": media,
        "dias_ate_ruptura": dias,
        "data_prevista_ruptura": data_prevista,
        "risco_ruptura": risco,
    }


def _assistant_stockout_reason(item: dict[str, Any], janela_dias: Any = 30) -> str:
    saldo = _assistant_float(item.get("saldo_considerado", item.get("saldo_loja")))
    media = _assistant_float(item.get("media_venda_dia"))
    vendida = _assistant_float(item.get("quantidade_vendida_janela"))
    dias = item.get("dias_ate_ruptura_considerado", item.get("dias_ate_ruptura"))
    risco = _assistant_texto_norm(item.get("risco_relatorio", item.get("risco_ruptura")))
    janela = int(_assistant_float(janela_dias, 30) or 30)
    if media <= 0:
        return f"Saldo de loja de {_assistant_qty(saldo)} un., mas sem venda na janela de {janela} dias; nao e ruptura imediata, e sim alerta de giro."
    if saldo <= 0:
        return f"Produto ja esta sem saldo de loja e vendeu {_assistant_qty(vendida)} un. nos ultimos {janela} dias."
    cobertura = _assistant_qty(dias if dias is not None else 0, 1)
    base = (
        f"Saldo de loja {_assistant_qty(saldo)} un. cobre cerca de {cobertura} dia(s), "
        f"considerando media de {_assistant_qty(media, 2)} un./dia e {_assistant_qty(vendida)} un. vendidas nos ultimos {janela} dias."
    )
    if risco in {"critico", "alto"}:
        return base + " O estoque Full foi ignorado nesta analise; a cobertura de loja esta abaixo do minimo operacional."
    return base


def _assistant_stockout_action(item: dict[str, Any]) -> str:
    saldo_loja = _assistant_float(item.get("saldo_considerado", item.get("saldo_loja")))
    media = _assistant_float(item.get("media_venda_dia"))
    dias = item.get("dias_ate_ruptura_considerado", item.get("dias_ate_ruptura"))
    if media <= 0:
        return "Revisar anuncio/preco antes de comprar mais; se houver saldo alto, criar acao de giro ou kit."
    if _assistant_float(dias, 9999) <= 7:
        return "Reposicao imediata para estoque de loja, conferir compra em aberto e evitar escalar campanha antes de recompor saldo local."
    if saldo_loja <= 0:
        return "Repor estoque de loja antes de manter venda ativa; sem saldo local, o risco comercial e imediato."
    return "Planejar reposicao, revisar curva de venda e proteger campanha dos SKUs com maior giro."


def _assistant_collect_stockout_rows(context: dict[str, Any], only_risky: bool = True, limit: int = 50) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_item(item: Any, janela_dias: Any = 30, loja: str = "", data_referencia: Any = "") -> None:
        if not isinstance(item, dict):
            return
        sku = str(item.get("sku") or item.get("seller_sku") or "").strip().upper()
        if not sku:
            return
        metrics = _assistant_stockout_store_metrics(item, data_referencia)
        risco_norm = _assistant_texto_norm(metrics.get("risco_ruptura"))
        if only_risky and risco_norm not in {"critico", "alto"}:
            return
        key = f"{sku}|{loja}|{metrics.get('data_prevista_ruptura') or ''}"
        if key in seen:
            return
        seen.add(key)
        enriched = {
            **item,
            "saldo_considerado": metrics.get("saldo_considerado"),
            "dias_ate_ruptura_considerado": metrics.get("dias_ate_ruptura"),
            "risco_relatorio": metrics.get("risco_ruptura"),
            "data_prevista_ruptura_considerada": metrics.get("data_prevista_ruptura"),
        }
        row = {
            "sku": sku,
            "produto": str(item.get("produto") or item.get("nome") or item.get("title") or "").strip(),
            "risco": _assistant_risk_label(metrics.get("risco_ruptura")),
            "saldo_loja": _assistant_qty(metrics.get("saldo_considerado")),
            "saldo_considerado": _assistant_qty(metrics.get("saldo_considerado")),
            "vendido_janela": _assistant_qty(item.get("quantidade_vendida_janela")),
            "media_dia": _assistant_qty(item.get("media_venda_dia"), 2),
            "dias_ate_ruptura": _assistant_qty(metrics.get("dias_ate_ruptura"), 1) if metrics.get("dias_ate_ruptura") is not None else "-",
            "ruptura_prevista": str(metrics.get("data_prevista_ruptura") or "-"),
            "motivo": _assistant_stockout_reason(enriched, janela_dias),
            "acao_recomendada": _assistant_stockout_action(enriched),
        }
        if loja:
            row["loja"] = loja
        rows.append(row)

    for raw in context.get("tool_results") or []:
        if not isinstance(raw, dict) or _assistant_function_name(raw) != "get_stockout_forecast":
            continue
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        janela = result.get("janela_dias") or (raw.get("arguments") or {}).get("janela_dias") or 30
        data_ref = result.get("data_referencia") or ""
        loja = str(result.get("loja") or (raw.get("arguments") or {}).get("loja") or "").strip()
        items = result.get("itens") if isinstance(result.get("itens"), list) else []
        if not items and result.get("sku"):
            items = [result]
        for item in items:
            add_item(item, janela, loja, data_ref)

    for item in context.get("registry_results") or []:
        if not isinstance(item, dict) or item.get("tool_id") != "stockout_forecast":
            continue
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        janela = summary.get("janela_dias") or (item.get("arguments") or {}).get("janela_dias") or 30
        data_ref = summary.get("data_referencia") or ""
        loja = str(item.get("loja") or summary.get("loja") or "").strip()
        for row in item.get("rows") or []:
            add_item(row, janela, loja, data_ref)

    risk_weight = {"Critico": 0, "Alto": 1, "Medio": 2, "Baixo": 3, "Sem consumo recente": 4}
    rows.sort(
        key=lambda row: (
            risk_weight.get(str(row.get("risco") or ""), 9),
            _assistant_float(str(row.get("dias_ate_ruptura") or "9999").replace(",", "."), 9999),
            -_assistant_float(str(row.get("media_dia") or "0").replace(",", "."), 0),
        )
    )
    return rows[:limit]


def _assistant_collect_stale_stock_rows(context: dict[str, Any], limit: int = 120) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_item(item: Any, loja: str = "") -> None:
        if not isinstance(item, dict):
            return
        sku = str(item.get("sku") or item.get("seller_sku") or item.get("SKU") or "").strip().upper()
        if not sku:
            return
        saldo_loja = item.get("saldo_loja", item.get("saldo_total", item.get("saldo", 0)))
        saldo_num = _assistant_float(saldo_loja)
        if saldo_num <= 0:
            return
        key = f"{sku}|{loja}"
        if key in seen:
            return
        seen.add(key)
        dias = item.get("dias_sem_vender")
        if dias is None:
            dias = item.get("dias_sem_venda", item.get("dias", ""))
        produto = str(item.get("produto") or item.get("nome") or item.get("title") or "").strip()
        ultima_venda = str(item.get("ultima_venda") or item.get("data_ultima_venda") or item.get("last_sale_date") or "-").strip() or "-"
        status = _assistant_texto_norm(item.get("status"))
        nunca_vendeu = status == "nunca_vendeu" or (not str(dias if dias is not None else "").strip() and ultima_venda == "-")
        dias_informado = str(dias if dias is not None else "").strip() not in {"", "-"}
        dias_num = max(0, int(_assistant_float(dias))) if dias_informado else None
        if not nunca_vendeu and dias_num is not None and dias_num < 30:
            return

        if nunca_vendeu:
            situacao = "Nunca vendeu"
            prioridade = "1 - Imediata"
            motivo = "SKU possui saldo de loja, mas nao tem historico de venda localizado."
            acao = "Validar cadastro e anuncio, bloquear nova compra e decidir entre lancamento, kit, transferencia ou liquidacao."
        elif dias_num is None:
            situacao = "Data da ultima venda indisponivel"
            prioridade = "2 - Alta"
            motivo = "SKU possui saldo de loja, mas a data da ultima venda nao pôde ser confirmada."
            acao = "Conferir a sincronizacao do historico de vendas antes de comprar mais ou iniciar liquidacao."
        elif dias_num >= 180:
            situacao = f"{dias_num} dias sem venda"
            prioridade = "1 - Imediata"
            motivo = "SKU esta ha pelo menos 180 dias sem venda e continua ocupando estoque de loja."
            acao = "Bloquear nova compra, revisar anuncio e preco e preparar liquidacao, kit ou transferencia de canal."
        elif dias_num >= 90:
            situacao = f"{dias_num} dias sem venda"
            prioridade = "2 - Alta"
            motivo = "SKU esta entre 90 e 179 dias sem venda, indicando perda relevante de giro."
            acao = "Revisar preco, titulo, foto, frete e concorrencia; testar kit ou promocao com prazo definido."
        elif dias_num >= 60:
            situacao = f"{dias_num} dias sem venda"
            prioridade = "3 - Atencao"
            motivo = "SKU esta entre 60 e 89 dias sem venda e precisa de intervencao antes de virar estoque cronico."
            acao = "Revisar anuncio e exposicao, testar ajuste comercial e acompanhar o giro nas proximas duas semanas."
        else:
            situacao = f"{dias_num or 0} dias sem venda"
            prioridade = "4 - Monitorar"
            motivo = "SKU possui saldo de loja e esta ha pelo menos 30 dias sem venda."
            acao = "Monitorar o giro, revisar a oferta e evitar recomprar ate ocorrer nova venda."

        saldo_full = item.get("saldo_full", 0)
        custo_cadastrado = item.get("custo_cadastrado") is True or (
            item.get("custo_cadastrado") is None and item.get("custo_unitario") not in (None, "")
        )
        custo_unitario = _assistant_float(item.get("custo_unitario")) if custo_cadastrado else None
        custo_origem = str(item.get("custo_origem") or "cadastro").strip() if custo_cadastrado else ""
        capital_custo = item.get("valor_custo_estoque_loja")
        if capital_custo is None and custo_cadastrado:
            capital_custo = saldo_num * float(custo_unitario or 0)
        impacto = (
            f"{_assistant_qty(saldo_num)} un. paradas; {_assistant_money(capital_custo)} pelo custo cadastrado ({custo_origem})."
            if custo_cadastrado
            else f"{_assistant_qty(saldo_num)} un. paradas; custo nao cadastrado para estimar o capital."
        )
        row = {
            "sku": sku,
            "produto": produto,
            "saldo_loja": _assistant_qty(saldo_loja),
            "saldo_full": _assistant_qty(saldo_full),
            "dias_sem_vender": _assistant_qty(dias_num, 0) if dias_num is not None else "-",
            "ultima_venda": ultima_venda,
            "situacao": situacao,
            "prioridade": prioridade,
            "impacto_financeiro": impacto,
            "custo_unitario": _assistant_money(custo_unitario) if custo_cadastrado else "nao cadastrado",
            "custo_origem": custo_origem or "indisponivel",
            "capital_custo": _assistant_money(capital_custo) if custo_cadastrado else "indisponivel",
            "motivo": motivo,
            "acao_recomendada": acao,
        }
        if loja:
            row["loja"] = loja
        rows.append(row)

    for raw in context.get("tool_results") or []:
        if not isinstance(raw, dict) or _assistant_function_name(raw) != "get_days_without_sale_top":
            continue
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        loja = str(result.get("loja") or (raw.get("arguments") or {}).get("loja") or "").strip()
        for item in _assistant_first_list(result, "itens", "items", "rows"):
            add_item(item, loja)

    for item in context.get("registry_results") or []:
        if not isinstance(item, dict) or item.get("tool_id") != "stale_stock":
            continue
        loja = str(item.get("loja") or (item.get("arguments") or {}).get("loja") or "").strip()
        for row in item.get("rows") or []:
            add_item(row, loja)

    rows.sort(
        key=lambda row: (
            int(str(row.get("prioridade") or "9").split(" ", 1)[0]) if str(row.get("prioridade") or "").split(" ", 1)[0].isdigit() else 9,
            0 if str(row.get("situacao") or "") == "Nunca vendeu" else 1,
            -_assistant_float(str(row.get("dias_sem_vender") or "0").replace(",", "."), 0),
            -_assistant_float(str(row.get("saldo_loja") or "0").replace(",", "."), 0),
            str(row.get("sku") or ""),
        )
    )
    return rows[:limit]


def _assistant_collect_sales_rank_rows(context: dict[str, Any], limit: int = 30) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_row(item: Any) -> None:
        if not isinstance(item, dict):
            return
        sku = str(item.get("sku") or item.get("SKU") or item.get("seller_sku") or "").strip().upper()
        if not sku:
            return
        if sku in seen:
            return
        seen.add(sku)
        qtd = item.get("quantidade_vendida") or item.get("quantidade") or item.get("qtd") or item.get("quantidade_total") or 0
        valor = item.get("valor_vendido") or item.get("valor_total") or item.get("valor") or 0
        qtd_num = margem_parse_float(qtd) or 0.0
        valor_num = margem_parse_float(valor) or 0.0
        preco_unitario = (valor_num / qtd_num) if qtd_num > 0 else 0.0
        pedidos = item.get("pedidos") or item.get("numero_pedidos") or item.get("pedidos_total") or "-"
        rows.append(
            {
                "sku": sku,
                "produto": str(item.get("produto") or item.get("nome") or item.get("title") or "").strip(),
                "quantidade_vendida": _assistant_qty(qtd),
                "valor_vendido": _assistant_money(valor),
                "quantidade_num": qtd_num,
                "valor_num": valor_num,
                "preco_unitario_num": preco_unitario,
                "pedidos": pedidos,
                "analise": "SKU relevante para proteger estoque, margem, anuncio e reposicao no periodo consultado.",
            }
        )

    for raw in context.get("tool_results") or []:
        if not isinstance(raw, dict) or _assistant_function_name(raw) != "get_sales_by_period":
            continue
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        for item in _assistant_first_list(result, "top_skus", "por_sku", "items", "itens"):
            add_row(item)
    for item in context.get("registry_results") or []:
        if not isinstance(item, dict) or item.get("tool_id") != "sales_ranking":
            continue
        for row in item.get("rows") or []:
            add_row(row)
    return rows[:limit]


def _assistant_load_margin_cost_maps(client_id: str, loja: str) -> tuple[dict, dict, list[str]]:
    warnings: list[str] = []
    try:
        from backend.services.promocoes_core_custos import (
            _carregar_custos_impostos_cadastro_por_sku_loja,
        )

        custos, impostos = _carregar_custos_impostos_cadastro_por_sku_loja(str(client_id or "default"), str(loja or ""))
        return custos if isinstance(custos, dict) else {}, impostos if isinstance(impostos, dict) else {}, warnings
    except Exception as exc:
        warnings.append(f"Nao consegui carregar custos/impostos do cadastro: {str(exc)[:220]}")
        return {}, {}, warnings


def _assistant_resolve_margin_cost_tax(custos_por_sku: dict, impostos_por_sku: dict, sku: str) -> tuple[float | None, float | None]:
    try:
        from backend.services.promocoes_core_custos import _resolver_custo_por_sku, _resolver_imposto_rate_por_sku

        custo = _resolver_custo_por_sku(custos_por_sku or {}, sku)
        imposto = _resolver_imposto_rate_por_sku(impostos_por_sku or {}, sku)
        return (
            float(custo) if custo is not None else None,
            float(imposto) if imposto is not None else None,
        )
    except Exception:
        return None, None


def _assistant_collect_ml_listing_refs(context: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    refs: dict[str, list[dict[str, Any]]] = {}

    def add(item: Any) -> None:
        if not isinstance(item, dict):
            return
        sku = str(
            item.get("sku")
            or item.get("SKU")
            or item.get("seller_sku")
            or item.get("seller_custom_field")
            or item.get("sku_display")
            or ""
        ).strip().upper()
        if not sku:
            return
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        commercial = details.get("commercial") if isinstance(details.get("commercial"), dict) else {}
        price = commercial.get("price") if isinstance(commercial.get("price"), dict) else {}
        fees = commercial.get("fees") if isinstance(commercial.get("fees"), dict) else {}
        shipping = commercial.get("shipping") if isinstance(commercial.get("shipping"), dict) else {}
        normalized = {
            **item,
            "price": price.get("amount") if price.get("amount") is not None else item.get("price"),
            "original_price": price.get("regular_amount") if price.get("regular_amount") is not None else item.get("original_price"),
            "sale_fee_amount": fees.get("sale_fee_amount"),
            "shipping_seller_cost": shipping.get("seller_cost"),
            "free_shipping": shipping.get("free_shipping"),
            "commercial_collected": bool(commercial),
        }
        identity = (
            str(normalized.get("loja") or normalized.get("store") or ""),
            str(normalized.get("id") or normalized.get("item_id") or ""),
            str(normalized.get("variation_id") or ((normalized.get("match") or {}).get("variation_id") if isinstance(normalized.get("match"), dict) else "")),
        )
        current = refs.setdefault(sku, [])
        if not any(
            (
                str(value.get("loja") or value.get("store") or ""),
                str(value.get("id") or value.get("item_id") or ""),
                str(value.get("variation_id") or ((value.get("match") or {}).get("variation_id") if isinstance(value.get("match"), dict) else "")),
            ) == identity
            for value in current
        ):
            current.append(normalized)

    def lists_from_result(result: dict[str, Any]) -> list[Any]:
        values: list[Any] = []
        for key in ("rows", "items", "itens", "matches", "anuncios", "produtos", "results"):
            current = result.get(key)
            if isinstance(current, list):
                values.extend(current)
        return values

    for raw in context.get("tool_results") or []:
        if not isinstance(raw, dict):
            continue
        if _assistant_function_name(raw) != "get_mercado_livre_listing":
            continue
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        for item in lists_from_result(result):
            add(item)
    for item in context.get("registry_results") or []:
        if not isinstance(item, dict) or item.get("tool_id") != "mercado_livre_listing":
            continue
        for row in item.get("rows") or []:
            add(row)
    return refs


def _assistant_margin_status_label(margem: dict[str, Any]) -> str:
    faltando = [str(item or "").strip() for item in margem.get("faltando_margem") or [] if str(item or "").strip()]
    if not faltando:
        return "completa"
    if "custo" in faltando:
        return "custo ausente"
    return "margem incompleta: " + ", ".join(faltando)


def _assistant_collect_margin_rows(context: dict[str, Any], limit: int = 120) -> list[dict[str, Any]]:
    client_id = str(context.get("client_id") or "").strip()
    tool_plan = context.get("tool_plan") if isinstance(context.get("tool_plan"), dict) else {}
    if not client_id:
        client_id = str(tool_plan.get("client_id") or "default").strip() or "default"
    loja = str(tool_plan.get("loja") or "").strip()
    sales_rows = _assistant_collect_sales_rank_rows(context, limit=max(limit, 120))
    if not sales_rows:
        return []
    custos_por_sku, impostos_por_sku, warnings = _assistant_load_margin_cost_maps(client_id, loja)
    cost_maps_by_store: dict[str, tuple[dict, dict]] = {
        _assistant_texto_norm(loja): (custos_por_sku, impostos_por_sku)
    }
    if warnings:
        context.setdefault("warnings", []).extend(warnings)
    ml_refs = _assistant_collect_ml_listing_refs(context)
    rows: list[dict[str, Any]] = []
    for row in sales_rows[:limit]:
        sku = str(row.get("sku") or "").strip().upper()
        if not sku:
            continue
        qtd_num = margem_parse_float(row.get("quantidade_num")) or margem_parse_float(row.get("quantidade_vendida")) or 0.0
        valor_num = margem_parse_float(row.get("valor_num")) or margem_parse_float(row.get("valor_vendido")) or 0.0
        preco_unitario = margem_parse_float(row.get("preco_unitario_num"))
        if (preco_unitario is None or preco_unitario <= 0) and qtd_num > 0:
            preco_unitario = valor_num / qtd_num
        ml_candidates = [dict(item) for item in ml_refs.get(sku) or []]
        if not ml_candidates:
            ml_candidates = [{}]
        multiple_ads = len(ml_candidates) > 1
        for ml_ref in ml_candidates:
            candidate_store = str(ml_ref.get("loja") or ml_ref.get("store") or row.get("loja") or row.get("store") or loja).strip()
            store_key = _assistant_texto_norm(candidate_store)
            if store_key not in cost_maps_by_store:
                store_costs, store_taxes, store_warnings = _assistant_load_margin_cost_maps(client_id, candidate_store)
                cost_maps_by_store[store_key] = (store_costs, store_taxes)
                if store_warnings:
                    context.setdefault("warnings", []).extend(store_warnings)
            candidate_costs, candidate_taxes = cost_maps_by_store[store_key]
            candidate_cost, candidate_tax_rate = _assistant_resolve_margin_cost_tax(candidate_costs, candidate_taxes, sku)
            # Um preco atual de anuncio nunca substitui silenciosamente o preco
            # historico vendido. Sem MLB exato, o preco medio permanece apenas
            # como estimativa por SKU.
            has_listing = bool(ml_ref)
            anuncio_ref = dict(ml_ref)
            if not anuncio_ref:
                anuncio_ref["price"] = preco_unitario
            margem = margem_calcular_anuncio(
                anuncio_ref,
                sku_hint=sku,
                custo=candidate_cost,
                imposto_rate=candidate_tax_rate,
                require_shipping=has_listing,
            )
            custo_total = round(float(candidate_cost) * float(qtd_num), 2) if candidate_cost is not None and qtd_num and not multiple_ads else None
            lucro_unitario = margem_parse_float(margem.get("valor_liquido")) if margem.get("margem_completa") else None
            lucro_total = round(float(lucro_unitario) * float(qtd_num), 2) if lucro_unitario is not None and qtd_num and not multiple_ads and not has_listing else None
            status_label = _assistant_margin_status_label(margem)
            if multiple_ads:
                status_label += "; resultado historico nao rateado entre MLBs"
            rows.append(
                {
                "SKU": sku,
                "Loja": candidate_store,
                "MLB": str(ml_ref.get("id") or ml_ref.get("item_id") or ""),
                "Variacao": str(ml_ref.get("variation_id") or ((ml_ref.get("match") or {}).get("variation_id") if isinstance(ml_ref.get("match"), dict) else "")),
                "Produto": str(row.get("produto") or "").strip(),
                "Qtd vendida": _assistant_qty(qtd_num) if not multiple_ads else "",
                "Valor vendido": _assistant_money(valor_num) if not multiple_ads else "",
                "Custo unitario": margem_formatar_moeda(candidate_cost) if candidate_cost is not None else "",
                "Custo total": margem_formatar_moeda(custo_total) if custo_total is not None else "",
                "Imposto": f"{_assistant_float(margem.get('imposto_percentual')):.2f}%".replace(".", ",") if margem.get("imposto_percentual") is not None else "",
                "Frete": margem.get("frete_ml_text") or "",
                "Tarifa": margem.get("tarifa_ml_text") or "",
                "Contribuicao unitaria atual": margem.get("valor_liquido_text") or "",
                "Lucro estimado": margem_formatar_moeda(lucro_total) if lucro_total is not None else "",
                "Margem %": margem.get("margem_text") or "",
                "Status da margem": status_label,
                "_valor_num": valor_num if not multiple_ads and not has_listing else 0.0,
                "_qtd_num": qtd_num if not multiple_ads else 0.0,
                "_custo_total_num": float(custo_total) if custo_total is not None else None,
                "_lucro_num": float(lucro_total) if lucro_total is not None else None,
                "_margem_completa": bool(margem.get("margem_completa") and lucro_total is not None),
                "_current_listing_margin_complete": bool(margem.get("margem_completa") and has_listing),
                }
            )
    return rows


def _assistant_report_row_sku(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip().upper()
        if value:
            return value
    return ""


def _assistant_index_by_sku(rows: list[dict[str, Any]], *keys: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sku = _assistant_report_row_sku(row, *keys)
        if sku and sku not in indexed:
            indexed[sku] = row
    return indexed


def _assistant_parse_percent_text(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", text)
    if not match:
        return None
    raw = match.group(0)
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    return _assistant_float(raw)


def _assistant_diagnostic_priority(score: int) -> str:
    if score <= 1:
        return "1 - Imediata"
    if score == 2:
        return "2 - Alta"
    if score == 3:
        return "3 - Media"
    return "4 - Monitorar"


def _assistant_specialist_sku_diagnostics(context: dict[str, Any], limit: int = 200) -> list[dict[str, Any]]:
    existing = context.get("specialist_sku_diagnostics")
    if isinstance(existing, list) and existing:
        return existing[:limit]

    sales_rows = _assistant_collect_sales_rank_rows(context, limit=max(limit, 200))
    margin_rows = _assistant_collect_margin_rows(context, limit=max(limit, 200))
    stockout_rows = _assistant_collect_stockout_rows(context, only_risky=False, limit=max(limit, 200))
    stale_rows = _assistant_collect_stale_stock_rows(context, limit=max(limit, 200))

    sales_by_sku = _assistant_index_by_sku(sales_rows, "sku", "SKU")
    margin_by_sku = _assistant_index_by_sku(margin_rows, "SKU", "sku")
    stockout_by_sku = _assistant_index_by_sku(stockout_rows, "sku", "SKU")
    stale_by_sku = _assistant_index_by_sku(stale_rows, "sku", "SKU")

    ordered_skus: list[str] = []
    for source in (stockout_rows, margin_rows, stale_rows, sales_rows):
        for row in source:
            sku = _assistant_report_row_sku(row, "sku", "SKU")
            if sku and sku not in ordered_skus:
                ordered_skus.append(sku)

    diagnostics: list[dict[str, Any]] = []
    for sku in ordered_skus:
        sale = sales_by_sku.get(sku, {})
        margin = margin_by_sku.get(sku, {})
        stockout = stockout_by_sku.get(sku, {})
        stale = stale_by_sku.get(sku, {})

        produto = (
            str(stockout.get("produto") or "").strip()
            or str(sale.get("produto") or "").strip()
            or str(margin.get("Produto") or "").strip()
            or str(stale.get("produto") or "").strip()
        )
        motivos: list[str] = []
        acoes: list[str] = []
        fontes: list[str] = []
        priority_score = 4
        risk_score = 4
        risco = "Monitorar"
        responsavel = "Comercial/Estoque"

        stockout_risk = str(stockout.get("risco") or "").strip()
        stockout_risk_norm = _assistant_texto_norm(stockout_risk)
        if stockout:
            fontes.append("ruptura_estoque")
            motivo = str(stockout.get("motivo") or "").strip()
            if motivo:
                motivos.append(motivo)
            action = str(stockout.get("acao_recomendada") or "").strip()
            if action:
                acoes.append(action)
            if stockout_risk_norm in {"critico", "alto"}:
                risco = "Critico" if stockout_risk_norm == "critico" else "Alto"
                risk_score = min(risk_score, 1 if stockout_risk_norm == "critico" else 2)
                priority_score = min(priority_score, 1 if stockout_risk_norm == "critico" else 2)
                responsavel = "Compras/Estoque"
            elif stockout_risk_norm == "medio":
                risco = "Medio"
                risk_score = min(risk_score, 3)
                priority_score = min(priority_score, 3)

        status_margem = str(margin.get("Status da margem") or "").strip().lower()
        margem_pct = _assistant_parse_percent_text(margin.get("Margem %"))
        lucro_num = margin.get("_lucro_num")
        valor_num = _assistant_float(margin.get("_valor_num") if margin else sale.get("valor_num"))
        qtd_num = _assistant_float(margin.get("_qtd_num") if margin else sale.get("quantidade_num"))
        if margin:
            fontes.append("margem_cadastro_favoritos")
            if "custo ausente" in status_margem:
                motivos.append("Custo ausente no cadastro; a margem e o lucro do SKU nao ficam confiaveis.")
                acoes.append("Cadastrar custo por loja ou custo geral antes de decidir reposicao, preco ou campanha.")
                risco = "Alto" if risk_score > 2 else risco
                risk_score = min(risk_score, 2)
                priority_score = min(priority_score, 2)
                responsavel = (
                    "Cadastro/Custos"
                    if responsavel == "Comercial/Estoque"
                    else responsavel if "Cadastro/Custos" in responsavel else f"{responsavel} + Cadastro/Custos"
                )
            elif status_margem and status_margem != "completa":
                motivos.append(f"Margem incompleta: {status_margem}.")
                acoes.append("Completar frete, tarifa, imposto e custo para fechar o diagnostico financeiro.")
                risco = "Medio" if risk_score > 3 else risco
                risk_score = min(risk_score, 3)
                priority_score = min(priority_score, 3)
                responsavel = (
                    "Cadastro/Custos"
                    if responsavel == "Comercial/Estoque"
                    else responsavel if "Cadastro/Custos" in responsavel else f"{responsavel} + Cadastro/Custos"
                )
            elif margem_pct is not None and margem_pct < 5:
                motivos.append(f"Margem estimada baixa ({margin.get('Margem %')}); SKU pode estar vendendo com lucro apertado.")
                acoes.append("Revisar preco, custo, tarifa e frete antes de escalar campanha ou reposicao.")
                risco = "Alto" if margem_pct < 0 else ("Medio" if risk_score > 3 else risco)
                risk_score = min(risk_score, 2 if margem_pct < 0 else 3)
                priority_score = min(priority_score, 2 if margem_pct < 0 else 3)
                responsavel = "Comercial/Precificacao" if responsavel == "Comercial/Estoque" else f"{responsavel} + Precificacao"

        if stale:
            fontes.append("estoque_parado")
            motivo = str(stale.get("motivo") or "").strip()
            if motivo:
                motivos.append(motivo)
            action = str(stale.get("acao_recomendada") or "").strip()
            if action:
                acoes.append(action)
            if risk_score > 3:
                risco = "Medio"
                risk_score = 3
            priority_score = min(priority_score, 3)
            if responsavel == "Comercial/Estoque":
                responsavel = "Comercial/Anuncios"

        if sale:
            fontes.append("ranking_vendas")
            if not motivos:
                motivos.append("SKU esta entre os mais vendidos; precisa ser acompanhado por cobertura, margem e reposicao.")
            if not acoes:
                acoes.append("Monitorar giro, margem e estoque de loja para manter venda sem ruptura.")

        impacto_partes: list[str] = []
        if stockout and stockout_risk_norm in {"critico", "alto"}:
            media_dia = _assistant_float(str(stockout.get("media_dia") or "0").replace(",", "."))
            preco_unit = (valor_num / qtd_num) if qtd_num > 0 and valor_num > 0 else 0.0
            risco_semana = media_dia * preco_unit * 7
            if risco_semana > 0:
                impacto_partes.append(f"Venda semanal em risco: {_assistant_money(risco_semana)}")
        if valor_num > 0:
            impacto_partes.append(f"Valor vendido no periodo: {_assistant_money(valor_num)}")
        if lucro_num is not None:
            impacto_partes.append(f"Lucro estimado: {_assistant_money(lucro_num)}")
        elif "custo ausente" in status_margem and valor_num > 0:
            impacto_partes.append(f"Valor sem margem confiavel: {_assistant_money(valor_num)}")

        diagnostics.append(
            {
                "SKU": sku,
                "Produto": produto,
                "Motivo": " ".join(dict.fromkeys(motivos))[:900],
                "Risco": risco,
                "Impacto financeiro": "; ".join(impacto_partes) or "Impacto financeiro nao calculavel com os dados atuais.",
                "Margem": str(margin.get("Margem %") or margin.get("Status da margem") or "nao calculada"),
                "Custo ausente": "Sim" if "custo ausente" in status_margem else "Nao",
                "Acao recomendada": " ".join(dict.fromkeys(acoes))[:900],
                "Prioridade": _assistant_diagnostic_priority(priority_score),
                "Responsavel": responsavel,
                "Fonte": ", ".join(dict.fromkeys(fontes)),
                "_risk_score": risk_score,
                "_priority_score": priority_score,
                "_valor_num": valor_num,
            }
        )

    diagnostics.sort(
        key=lambda row: (
            int(row.get("_priority_score") or 9),
            int(row.get("_risk_score") or 9),
            -_assistant_float(row.get("_valor_num")),
            str(row.get("SKU") or ""),
        )
    )
    clean_rows = [{key: value for key, value in row.items() if not str(key).startswith("_")} for row in diagnostics[:limit]]
    context["specialist_sku_diagnostics"] = clean_rows
    return clean_rows


def _assistant_product_costs_and_margin_raw(
    client_id: str,
    plan: dict[str, Any],
    existing_registry_results: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    context = {
        "client_id": client_id,
        "tool_plan": {**(plan or {}), "client_id": client_id},
        "registry_results": list(existing_registry_results or []),
        "tool_results": [],
        "warnings": [],
    }
    limit = int(plan.get("limite") or DEFAULT_RANKING_LIMIT or 50)
    rows = _assistant_collect_margin_rows(context, limit=max(1, min(limit, 500)))
    completos = [row for row in rows if row.get("_margem_completa")]
    valor_completo = sum(_assistant_float(row.get("_valor_num")) for row in completos)
    lucro = sum(_assistant_float(row.get("_lucro_num")) for row in completos)
    margem_pct = (lucro / valor_completo * 100.0) if valor_completo > 0 else 0.0
    sem_custo = sum(1 for row in rows if str(row.get("Status da margem") or "") == "custo ausente")
    return {
        "function": "codex_product_costs_and_margin",
        "arguments": {
            "data_inicio": plan.get("data_inicio") or "",
            "data_fim": plan.get("data_fim") or "",
            "loja": plan.get("loja") or "",
            "limite": limit,
        },
        "result": {
            "rows": [{key: value for key, value in row.items() if not str(key).startswith("_")} for row in rows],
            "total_registros": len(rows),
            "skus_com_margem_completa": len(completos),
            "skus_sem_custo": sem_custo,
            "lucro_estimado_completo": lucro,
            "margem_percentual_completa": margem_pct,
            "warnings": context.get("warnings") or [],
        },
    }


def _assistant_add_finding(
    sections: dict[str, list[dict[str, Any]]],
    section: str,
    title: str,
    evidence: str,
    recommendation: str,
    severity: str = "info",
    impact: str = "",
    source: str = "",
) -> None:
    sections.setdefault(section, []).append(
        {
            "section": section,
            "title": str(title or "")[:160],
            "severity": severity,
            "evidence": str(evidence or "")[:700],
            "impact": str(impact or "")[:500],
            "recommendation": str(recommendation or "")[:700],
            "source": source,
        }
    )


def _assistant_management_analysis(context: dict[str, Any]) -> dict[str, Any]:
    results = context.get("tool_results") if isinstance(context.get("tool_results"), list) else []
    sources = context.get("sources") if isinstance(context.get("sources"), list) else []
    warnings = context.get("warnings") if isinstance(context.get("warnings"), list) else []
    generated_at = str(context.get("generated_at") or _assistant_now())

    sales = _assistant_find_result(results, "get_sales_by_period")
    sales_qty = _assistant_find_result(results, "get_sales_quantity_by_period")
    returns = _assistant_find_result(results, "get_returns_by_period")
    return_rate = _assistant_find_result(results, "get_return_rate_by_period")
    profit = _assistant_find_result(results, "get_profit_by_period")
    stockout = _assistant_find_result(results, "get_stockout_forecast")
    stale = _assistant_find_result(results, "get_days_without_sale_top")
    stale_candidates = [
        item.get("result") for item in results
        if isinstance(item, dict)
        and _assistant_function_name(item) == "get_days_without_sale_top"
        and isinstance(item.get("result"), dict)
    ]
    if stale_candidates:
        stale = max(
            stale_candidates,
            key=lambda value: int(
                ((value.get("resumo_estoque_parado") or {}).get("total_skus") if isinstance(value.get("resumo_estoque_parado"), dict) else 0)
                or len(_assistant_first_list(value, "itens"))
            ),
        )
    anomalies = _assistant_find_result(results, "detect_sales_anomalies")
    comparison = _assistant_find_result(results, "get_period_comparison")
    avg_ticket = _assistant_find_result(results, "get_avg_ticket_by_period")
    integrations = _assistant_find_result(results, "get_integrations_status")
    ml_listing = _assistant_find_result(results, "get_mercado_livre_listing")

    kpis: list[dict[str, Any]] = []
    sections: dict[str, list[dict[str, Any]]] = {}

    if sales:
        kpis.extend(
            [
                {"label": "Faturamento", "value": _assistant_money(sales.get("valor_total")), "detail": "Periodo base de vendas", "severity": "info"},
                {"label": "Pedidos", "value": str(int(_assistant_float(sales.get("pedidos_total")))), "detail": "Pedidos validos", "severity": "info"},
                {"label": "Itens vendidos", "value": f"{_assistant_float(sales.get('quantidade_total')):.0f}", "detail": "Quantidade liquida vendida", "severity": "info"},
            ]
        )
        if _assistant_float(sales.get("valor_total")) <= 0:
            _assistant_add_finding(
                sections,
                "Vendas",
                "Sem faturamento no periodo consultado",
                "A consulta de vendas retornou faturamento zerado.",
                "Validar se o periodo, a loja e a sincronizacao de vendas estao corretos antes de tomar decisoes comerciais.",
                "warning",
                "Sem vendas confiaveis, qualquer leitura de estoque e margem pode ficar distorcida.",
                "get_sales_by_period",
            )
        top_skus = _assistant_first_list(sales, "top_skus")
        if top_skus:
            top = top_skus[0] if isinstance(top_skus[0], dict) else {}
            _assistant_add_finding(
                sections,
                "Vendas",
                "SKU lider deve orientar reposicao e campanha",
                f"Principal SKU vendido: {_assistant_sku_label(top)}. Quantidade: {top.get('quantidade') or top.get('qtd') or top.get('quantidade_total') or '-'}; valor: {_assistant_money(top.get('valor') or top.get('valor_total') or 0)}.",
                "Garantir estoque, revisar buy box/anuncio e usar esse SKU como referencia de margem, fotos, preco e frete para produtos similares.",
                "info",
                "SKU lider sem cobertura de estoque vira perda direta de receita; SKU lider com baixa margem exige ajuste de preco/custo.",
                "get_sales_by_period",
            )

    if sales_qty and not sales:
        kpis.append({"label": "Itens vendidos", "value": f"{_assistant_float(sales_qty.get('quantidade_total') or sales_qty.get('quantidade_vendida_total')):.0f}", "detail": "Quantidade vendida no periodo", "severity": "info"})

    if avg_ticket:
        ticket = _assistant_float(avg_ticket.get("ticket_medio"))
        kpis.append({"label": "Ticket medio", "value": _assistant_money(ticket), "detail": "Receita media por pedido", "severity": "info"})
        if ticket > 0 and ticket < 80:
            _assistant_add_finding(
                sections,
                "Vendas",
                "Ticket medio baixo",
                f"Ticket medio estimado em {_assistant_money(ticket)}.",
                "Avaliar kits, combos, frete progressivo e anuncios complementares para elevar valor por pedido sem depender apenas de volume.",
                "info",
                "Ticket baixo aumenta peso de frete, tarifa e operacao sobre a margem.",
                "get_avg_ticket_by_period",
            )

    comp = comparison.get("comparativo") if isinstance(comparison.get("comparativo"), dict) else {}
    if comp:
        delta_valor_pct = _assistant_float(comp.get("variacao_faturamento_percentual"))
        delta_qtd_pct = _assistant_float(comp.get("variacao_quantidade_percentual"))
        severity = "warning" if delta_valor_pct <= -10 else "ok" if delta_valor_pct >= 10 else "info"
        _assistant_add_finding(
            sections,
            "Vendas",
            "Comparativo de periodo",
            f"Faturamento variou {_assistant_percent(delta_valor_pct)} e quantidade variou {_assistant_percent(delta_qtd_pct)} contra o periodo anterior.",
            "Quando houver queda, separar efeito de preco, ruptura, pausa de anuncio e reducao de demanda. Quando houver alta, proteger estoque dos SKUs que puxaram o crescimento.",
            severity,
            "Quedas acima de 10% exigem acao comercial ou verificacao de sincronizacao/anuncios.",
            "get_period_comparison",
        )

    alertas = _assistant_first_list(anomalies, "alertas", "anomalias")
    if alertas:
        quedas = [item for item in alertas if isinstance(item, dict) and str(item.get("tipo") or "").lower() == "queda"]
        picos = [item for item in alertas if isinstance(item, dict) and str(item.get("tipo") or "").lower() == "pico"]
        _assistant_add_finding(
            sections,
            "Vendas",
            "Curva de vendas com pontos fora do padrao",
            f"Foram encontrados {len(alertas)} alerta(s): {len(quedas)} queda(s) e {len(picos)} pico(s).",
            "Investigar datas de queda para identificar anuncio pausado, ruptura, alteracao de preco, fim de campanha ou falha de sincronizacao. Replicar causas dos picos quando saudaveis.",
            "warning" if quedas else "info",
            "Anomalias podem esconder perda de venda ou dependencia excessiva de poucos dias/campanhas.",
            "detect_sales_anomalies",
        )

    if return_rate:
        taxa_qtd = _assistant_float(return_rate.get("taxa_devolucao_quantidade_percentual"))
        taxa_valor = _assistant_float(return_rate.get("taxa_devolucao_valor_percentual"))
        severity = "critical" if taxa_qtd >= 10 or taxa_valor >= 10 else "warning" if taxa_qtd >= 5 or taxa_valor >= 5 else "info"
        kpis.append({"label": "Taxa devolucao", "value": _assistant_percent(max(taxa_qtd, taxa_valor)), "detail": "Maior taxa entre qtd/valor", "severity": severity})
        if taxa_qtd > 0 or taxa_valor > 0:
            _assistant_add_finding(
                sections,
                "Devolucoes",
                "Devolucoes impactam a rentabilidade",
                f"Taxa por quantidade: {_assistant_percent(taxa_qtd)}; taxa por valor: {_assistant_percent(taxa_valor)}.",
                "Separar top SKUs devolvidos, conferir compatibilidade, descricao, fotos, embalagem e recorrencia por loja/canal. Priorizar itens com devolucao alta e margem baixa.",
                severity,
                "Devolucao consome frete, reputacao, atendimento e capital de giro.",
                "get_return_rate_by_period",
            )

    if returns:
        top_returns = _assistant_first_list(returns, "top_skus")
        if top_returns:
            top = top_returns[0] if isinstance(top_returns[0], dict) else {}
            _assistant_add_finding(
                sections,
                "Devolucoes",
                "SKU com devolucao relevante",
                f"SKU em destaque nas devolucoes: {_assistant_sku_label(top)}. Valor devolvido: {_assistant_money(top.get('valor_devolvido') or top.get('valor') or 0)}.",
                "Revisar anuncio, aplicacao, dimensoes, perguntas frequentes e qualidade do envio antes de escalar campanha desse item.",
                "warning",
                "Um SKU com devolucao recorrente pode parecer vender bem, mas destruir margem no fechamento.",
                "get_returns_by_period",
            )

    if profit:
        margem_raw = profit.get("margem_percentual_estimada")
        lucro_raw = profit.get("lucro_estimado")
        margem = _assistant_float(margem_raw)
        lucro = _assistant_float(lucro_raw)
        skus_considerados = int(_assistant_float(profit.get("skus_considerados")))
        skus_com_custo = int(_assistant_float(profit.get("skus_com_custo")))
        cobertura = _assistant_float(profit.get("cobertura_faturamento_percentual"))
        if not cobertura:
            cobertura = (skus_com_custo / skus_considerados * 100.0) if skus_considerados else 0.0
        dados_suficientes = bool(profit.get("dados_suficientes")) if "dados_suficientes" in profit else cobertura >= 95.0
        if dados_suficientes and margem_raw is not None and lucro_raw is not None:
            severity = "critical" if margem < 5 else "warning" if margem < 15 else "ok"
            kpis.append({"label": "Lucro estimado", "value": _assistant_money(lucro), "detail": f"Margem {_assistant_percent(margem)} | cobertura {_assistant_percent(cobertura)}", "severity": severity})
            _assistant_add_finding(
                sections,
                "Margem",
                "Margem estimada do periodo",
                f"Lucro estimado: {_assistant_money(lucro)}; margem: {_assistant_percent(margem)}; cobertura ponderada pelo faturamento: {_assistant_percent(cobertura)}.",
                "Priorizar revisao de preco/custo dos SKUs de maior faturamento e manter a cobertura acima de 95%.",
                severity,
                "A margem consolidada so e publicada quando a cobertura minima de dados e atendida.",
                "get_profit_by_period",
            )
        else:
            kpis.append({"label": "Margem consolidada", "value": "indisponivel", "detail": f"Cobertura {_assistant_percent(cobertura)}; minimo 95,0%", "severity": "warning"})
            _assistant_add_finding(
                sections,
                "Margem",
                "Margem consolidada indisponivel",
                f"Cobertura ponderada pelo faturamento: {_assistant_percent(cobertura)}; minimo exigido: 95,0%.",
                "Completar custo, imposto, frete e tarifa dos SKUs de maior faturamento antes de usar lucro ou margem na decisao.",
                "warning",
                "Dados ausentes nao foram tratados como custo zero e nenhum lucro consolidado foi inventado.",
                "get_profit_by_period",
            )
        if skus_considerados and cobertura < 80:
            _assistant_add_finding(
                sections,
                "Margem",
                "Cadastro de custo incompleto",
                f"{skus_com_custo} de {skus_considerados} SKU(s) vendidos tinham custo cadastrado.",
                "Completar custo e imposto dos SKUs vendidos antes de decidir promocao, reposicao ou aumento de verba.",
                "warning",
                "Sem custo, o lucro estimado fica otimista ou impreciso.",
                "get_profit_by_period",
            )

    margin_rows = _assistant_collect_margin_rows(context, limit=500)
    if margin_rows:
        complete_rows = [row for row in margin_rows if row.get("_margem_completa")]
        valor_completo = sum(_assistant_float(row.get("_valor_num")) for row in complete_rows)
        lucro_completo = sum(_assistant_float(row.get("_lucro_num")) for row in complete_rows)
        margem_real = (lucro_completo / valor_completo * 100.0) if valor_completo > 0 else 0.0
        missing_cost = [row for row in margin_rows if str(row.get("Status da margem") or "") == "custo ausente"]
        incomplete = [row for row in margin_rows if not row.get("_margem_completa")]
        severity = "critical" if margem_real < 5 and complete_rows else "warning" if margem_real < 15 and complete_rows else "ok"
        kpis.append(
            {
                "label": "Margem real por SKU",
                "value": _assistant_percent(margem_real) if complete_rows else "incompleta",
                "detail": f"{len(complete_rows)} de {len(margin_rows)} SKU(s) com custo/imposto/frete/tarifa suficientes",
                "severity": severity if complete_rows else "warning",
            }
        )
        if complete_rows:
            _assistant_add_finding(
                sections,
                "Margem",
                "Margem calculada com custo do cadastro",
                f"Nos SKUs com dados completos, lucro estimado foi {_assistant_money(lucro_completo)} sobre {_assistant_money(valor_completo)}, margem {_assistant_percent(margem_real)}.",
                "Comparar os SKUs de maior faturamento com baixa margem, revisar preco, custo de compra, frete gratis e tarifa antes de escalar campanha.",
                severity,
                "Este calculo usa custo/imposto do cadastro e a mesma logica de margem do modulo Favoritos.",
                "product_costs_and_margin",
            )
        if missing_cost:
            exemplos = ", ".join(str(row.get("SKU") or "") for row in missing_cost[:8])
            _assistant_add_finding(
                sections,
                "Margem",
                "SKU vendido sem custo cadastrado",
                f"{len(missing_cost)} SKU(s) vendidos nao tinham custo localizado no cadastro. Exemplos: {exemplos}.",
                "Cadastrar custo por loja quando existir; se nao houver, preencher custo geral do produto para permitir margem real nos proximos relatorios.",
                "warning",
                "Sem custo cadastrado, o Black Jhon nao deve inventar lucro nem margem.",
                "product_costs_and_margin",
            )
        elif incomplete:
            exemplos = ", ".join(f"{row.get('SKU')} ({row.get('Status da margem')})" for row in incomplete[:6])
            _assistant_add_finding(
                sections,
                "Margem",
                "Margem incompleta por frete/tarifa/imposto",
                f"{len(incomplete)} SKU(s) ainda ficaram com margem incompleta. Exemplos: {exemplos}.",
                "Cruzar os anuncios Mercado Livre para preencher tarifa/frete, e revisar imposto no cadastro antes de decidir promocao.",
                "warning",
                "Margem incompleta deve orientar cadastro e integracao antes de decisao comercial.",
                "product_costs_and_margin",
            )

    stockout_report_rows = _assistant_collect_stockout_rows({"tool_results": results}, only_risky=True, limit=50)
    if stockout_report_rows:
        criticos = stockout_report_rows
        if criticos:
            first = criticos[0]
            top_labels = "; ".join(
                f"{item.get('sku')} - {item.get('produto') or ''} (saldo loja {item.get('saldo_considerado')}, media {item.get('media_dia')}/dia, ruptura {item.get('ruptura_prevista')})"
                for item in criticos[:8]
                if isinstance(item, dict)
            )
            kpis.append({"label": "Ruptura alta", "value": str(len(criticos)), "detail": "SKU(s) com risco alto/critico", "severity": "critical"})
            _assistant_add_finding(
                sections,
                "Estoque",
                "Risco de ruptura em SKUs com venda",
                f"{len(criticos)} SKU(s) com risco alto/critico considerando apenas saldo de loja. Principais: {top_labels or first.get('sku') or '-'}.",
                "Gerar lista de reposicao para estoque de loja, conferir compras em aberto e evitar escalar campanha antes de recompor saldo local.",
                "critical",
                "Ruptura interrompe ranking, perde venda e pode elevar custo de recuperacao do anuncio.",
                "get_stockout_forecast",
            )

    stale_items = _assistant_first_list(stale, "itens")
    if stale_items:
        parados = [
            item for item in stale_items
            if isinstance(item, dict) and _assistant_float(item.get("saldo_loja", item.get("saldo_total"))) > 0
        ]
        if parados:
            alert = _assistant_stale_stock_alert_payload(stale, parados)
            evidence = alert["detail"].replace("**", "").replace("`", "")
            kpis.append({"label": "Estoque parado", "value": str(len(parados)), "detail": "SKU(s) com saldo sem venda recente", "severity": "warning"})
            _assistant_add_finding(
                sections,
                "Estoque",
                "Capital parado em produtos sem giro",
                evidence,
                alert["recommendation"],
                "warning",
                "Estoque parado consome capital e espaco, e mascara falta de verba para produtos de maior giro.",
                "get_days_without_sale_top",
            )

    lojas = _assistant_first_list(integrations, "lojas")
    if lojas:
        desconectadas = [
            loja for loja in lojas
            if isinstance(loja, dict) and (not loja.get("mercado_livre_conectado") or not loja.get("bling_conectado"))
        ]
        if desconectadas:
            nomes = ", ".join(str(item.get("loja") or "-") for item in desconectadas[:6])
            _assistant_add_finding(
                sections,
                "Integracoes",
                "Integracoes desconectadas ou incompletas",
                f"{len(desconectadas)} loja(s) com Mercado Livre ou Bling desconectado/incompleto: {nomes}.",
                "Regularizar token antes de confiar em estoque, pedidos, anuncios e relatorios externos dessa loja.",
                "warning",
                "Token vencido gera leitura parcial e pode esconder ruptura, pedido ou anuncio problematico.",
                "get_integrations_status",
            )

    matches = _assistant_first_list(ml_listing, "matches")
    if matches:
        ruins = [
            item for item in matches
            if isinstance(item, dict)
            and (
                str(item.get("status") or "").lower() != "active"
                or _assistant_float(item.get("available_quantity")) <= 0
                or (_assistant_float(item.get("health"), 1.0) > 0 and _assistant_float(item.get("health"), 1.0) < 0.7)
            )
        ]
        if ruins:
            first = ruins[0]
            _assistant_add_finding(
                sections,
                "Anuncios",
                "Anuncios do Mercado Livre precisam de revisao",
                f"{len(ruins)} anuncio(s) com status, estoque ou saude abaixo do ideal. Primeiro: {_assistant_sku_label(first)}; status {first.get('status')}; saude {first.get('health')}.",
                "Revisar saude do anuncio, estoque disponivel, preco, fotos, catalogo e tipo de anuncio antes de investir trafego.",
                "warning",
                "Anuncio com baixa saude ou sem estoque reduz conversao e pode bloquear escala de vendas.",
                "get_mercado_livre_listing",
            )

    if warnings:
        _assistant_add_finding(
            sections,
            "Qualidade dos dados",
            "Avisos durante a coleta",
            "; ".join(str(item) for item in warnings[:4]),
            "Resolver avisos de coleta para que o diagnostico tenha cobertura completa.",
            "warning",
            "Dados incompletos podem mudar as prioridades do relatorio.",
            "warnings",
        )

    diagnostic_rows = _assistant_specialist_sku_diagnostics(context, limit=120)
    if diagnostic_rows:
        immediate = [row for row in diagnostic_rows if str(row.get("Prioridade") or "").startswith("1")]
        high = [row for row in diagnostic_rows if str(row.get("Prioridade") or "").startswith("2")]
        cost_missing = [row for row in diagnostic_rows if str(row.get("Custo ausente") or "").lower() == "sim"]
        kpis.append(
            {
                "label": "Diagnostico por SKU",
                "value": str(len(diagnostic_rows)),
                "detail": f"{len(immediate)} imediatos, {len(high)} altos, {len(cost_missing)} sem custo",
                "severity": "critical" if immediate else ("warning" if high or cost_missing else "info"),
            }
        )
        top_diag = diagnostic_rows[0]
        _assistant_add_finding(
            sections,
            "Diagnostico por SKU",
            "Fila especialista de acao por SKU",
            (
                f"Primeira prioridade: SKU {top_diag.get('SKU')} - {top_diag.get('Produto') or '-'}; "
                f"risco {top_diag.get('Risco')}; impacto {top_diag.get('Impacto financeiro')}."
            ),
            str(top_diag.get("Acao recomendada") or "Executar a fila de acoes priorizadas por SKU."),
            "critical" if immediate else ("warning" if high or cost_missing else "info"),
            "O diagnostico cruza vendas, margem/custo, ruptura de loja e estoque parado para evitar uma decisao baseada em apenas um indicador.",
            "specialist_sku_diagnostics",
        )

    ordered_sections = [
        {"title": title, "findings": findings}
        for title, findings in sections.items()
        if findings
    ]
    severity_weight = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    for section in ordered_sections:
        section["findings"] = sorted(section["findings"], key=lambda item: severity_weight.get(str(item.get("severity")), 2))

    all_findings = [finding for section in ordered_sections for finding in section.get("findings", [])]
    priority_actions = []
    for finding in sorted(all_findings, key=lambda item: severity_weight.get(str(item.get("severity")), 2)):
        rec = str(finding.get("recommendation") or "").strip()
        if rec and rec not in priority_actions:
            priority_actions.append(rec)
    executive = []
    critical_count = sum(1 for item in all_findings if item.get("severity") == "critical")
    warning_count = sum(1 for item in all_findings if item.get("severity") == "warning")
    if all_findings:
        executive.append(f"Foram encontrados {len(all_findings)} ponto(s) de gestao: {critical_count} critico(s), {warning_count} de atencao.")
    else:
        executive.append("Nenhum ponto critico foi detectado com os dados disponiveis, mas as fontes devem ser revisadas para confirmar cobertura.")
    executive.append(f"Consulta gerada em {generated_at}; fontes consultadas: {len(sources)}.")

    return {
        "generated_at": generated_at,
        "executive_summary": executive,
        "kpis": kpis[:12],
        "sections": ordered_sections,
        "priority_actions": priority_actions[:10],
        "data_quality": {
            "sources_count": len(sources),
            "tool_results_count": int(context.get("tool_results_count") or len(results)),
            "warnings": warnings[:10],
        },
    }


def _assistant_sources(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources = []
    for item in results:
        result = item.get("result") if isinstance(item, dict) else {}
        if not isinstance(result, dict):
            result = {}
        name = _assistant_function_name(item if isinstance(item, dict) else {})
        external = any(token in name for token in ("mercado_livre", "bling", "integrations"))
        label = _assistant_human_source_label(name, name)
        sources.append(
            {
                "function": name,
                "function_label": label,
                "source_label": label,
                "arguments": item.get("arguments") or {},
                "records": _assistant_result_count(result),
                "external": external,
                "read_only": True,
            }
        )
    return sources


def _assistant_stale_stock_alert_payload(result: dict[str, Any], parados: list[dict[str, Any]]) -> dict[str, str]:
    def dias_item(item: dict[str, Any]) -> Optional[int]:
        value = item.get("dias_sem_vender")
        if value is None or str(value).strip() == "":
            return None
        return max(0, int(_assistant_float(value)))

    def nunca_vendeu(item: dict[str, Any]) -> bool:
        return _assistant_texto_norm(item.get("status")) == "nunca_vendeu" or (
            dias_item(item) is None and not str(item.get("ultima_venda") or "").strip()
        )

    ordered = sorted(
        (item for item in parados if isinstance(item, dict)),
        key=lambda item: (
            0 if nunca_vendeu(item) else 1,
            -(dias_item(item) or 0),
            -_assistant_float(item.get("saldo_loja", item.get("saldo_total"))),
            str(item.get("sku") or ""),
        ),
    )
    summary = result.get("resumo_estoque_parado") if isinstance(result.get("resumo_estoque_parado"), dict) else {}
    total_skus = int(summary.get("total_skus") or len(ordered))
    total_units = _assistant_float(
        summary.get("total_unidades_loja"),
        sum(_assistant_float(item.get("saldo_loja", item.get("saldo_total"))) for item in ordered),
    )
    never_count = int(summary.get("nunca_venderam") or sum(1 for item in ordered if nunca_vendeu(item)))
    over_180 = int(summary.get("dias_180_mais") or sum(1 for item in ordered if not nunca_vendeu(item) and (dias_item(item) or 0) >= 180))
    between_90_179 = int(summary.get("dias_90_179") or sum(1 for item in ordered if 90 <= (dias_item(item) or -1) < 180))
    between_30_89 = int(summary.get("dias_30_89") or sum(1 for item in ordered if 30 <= (dias_item(item) or -1) < 90))
    unknown_dates = sum(1 for item in ordered if dias_item(item) is None and not nunca_vendeu(item))
    known_cost = [
        item for item in ordered
        if item.get("custo_cadastrado") is True or (
            item.get("custo_cadastrado") is None and item.get("custo_unitario") not in (None, "")
        )
    ]
    known_cost_count = int(summary.get("custos_cobertos") or len(known_cost))
    capital_known = _assistant_float(
        summary.get("capital_custo_conhecido"),
        sum(
            _assistant_float(
                item.get("valor_custo_estoque_loja")
                if item.get("valor_custo_estoque_loja") is not None
                else _assistant_float(item.get("saldo_loja", item.get("saldo_total"))) * _assistant_float(item.get("custo_unitario"))
            )
            for item in known_cost
        ),
    )

    preview_lines: list[str] = []
    for index, item in enumerate(ordered[:4], 1):
        sku = str(item.get("sku") or "-").strip() or "-"
        product = re.sub(r"\s+", " ", str(item.get("produto") or "Produto sem nome").strip())
        if len(product) > 62:
            product = product[:61].rstrip() + "…"
        saldo = _assistant_qty(item.get("saldo_loja", item.get("saldo_total")))
        days = dias_item(item)
        age = "nunca vendeu" if nunca_vendeu(item) else (f"{days} dias sem venda" if days is not None else "ultima venda sem data")
        last_sale = str(item.get("ultima_venda") or "").strip()
        last_text = f" • ultima: {last_sale}" if last_sale else ""
        preview_lines.append(f"{index}. `{sku}` — {product}\n   Saldo: {saldo} un. • {age}{last_text}")

    age_parts = [
        f"{never_count} nunca venderam",
        f"{over_180} com 180+ dias",
        f"{between_90_179} com 90–179 dias",
        f"{between_30_89} com 30–89 dias",
    ]
    if unknown_dates:
        age_parts.append(f"{unknown_dates} sem data confiavel")
    if known_cost_count:
        missing_cost = max(0, total_skus - known_cost_count)
        capital_text = (
            f"**Capital estimado pelo custo cadastrado:** {_assistant_money(capital_known)} em {known_cost_count}/{total_skus} SKU(s)"
            + (f"; {missing_cost} sem custo." if missing_cost else ".")
        )
    else:
        capital_text = f"**Capital:** indisponivel — os {total_skus} SKU(s) estao sem custo utilizavel."

    scope = str(result.get("loja") or "todas as lojas").strip() or "todas as lojas"
    reference_date = str(result.get("data_referencia") or _assistant_today()).strip()
    detail = "\n\n".join(
        [
            f"**Resumo:** {total_skus} SKU(s) e {_assistant_qty(total_units)} unidade(s) paradas em estoque de loja.",
            "**Idade do estoque:** " + " • ".join(age_parts) + ".",
            capital_text,
            "**Mais urgentes:**\n" + "\n".join(preview_lines),
            f"**Base:** {scope} • posicao {reference_date}. No consolidado, o custo usa a media dos cadastros por loja; saldo Full e apenas contexto.",
        ]
    )
    urgent_count = never_count + over_180
    recommendation = (
        f"Priorizar os {urgent_count or total_skus} SKU(s) sem historico ou com 180+ dias: bloquear recompra, validar cadastro/anuncio, "
        "revisar preco e oferta e definir kit, transferencia ou liquidacao com prazo e responsavel."
    )
    report_prompt = (
        "Gere um relatorio detalhado e focado somente no alerta de estoque parado com saldo de loja. "
        "Reconsulte o historico de vendas, o estoque e o cadastro para listar todos os SKUs envolvidos, sem limitar aos primeiros. "
        "Comece por um resumo com total de SKUs, total de unidades, faixas de 30–89, 90–179 e 180+ dias e itens que nunca venderam. "
        "Para cada SKU, informe produto, saldo de loja, saldo Full apenas como contexto, ultima venda, dias parado, custo cadastrado, "
        "capital estimado quando houver custo, prioridade, motivo e acao recomendada. Nao trate custo ausente como zero. "
        "Finalize com uma fila de acao ordenada, prazo sugerido, responsavel recomendado e avisos de dados incompletos."
    )
    return {"detail": detail, "recommendation": recommendation, "report_prompt": report_prompt}


def _assistant_suggestions_from_results(results: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    today = _assistant_today()

    def add(
        kind: str,
        title: str,
        detail: str,
        severity: str = "info",
        source: str = "",
        recommendation: str = "",
        report_prompt: str = "",
    ) -> None:
        raw = f"{today}|{kind}|{title}|{detail}|{source}"
        sid = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        source_label = _assistant_human_source_label(source, source)
        suggestion = {
            "id": sid,
            "kind": kind,
            "title": title[:120],
            "detail": detail[:1400],
            "severity": severity,
            "source": source_label,
            "source_raw": source,
            "recommendation": recommendation[:700],
            "created_at": _assistant_now(),
        }
        if report_prompt:
            suggestion["report_prompt"] = report_prompt[:1800]
        suggestions.append(suggestion)

    best_stale_result: dict[str, Any] = {}
    best_stale_items: list[dict[str, Any]] = []
    best_stale_score = -1
    for candidate in results:
        if not isinstance(candidate, dict) or _assistant_function_name(candidate) != "get_days_without_sale_top":
            continue
        candidate_result = candidate.get("result") if isinstance(candidate.get("result"), dict) else {}
        candidate_items = candidate_result.get("itens") if isinstance(candidate_result.get("itens"), list) else []
        candidate_stale = [
            item for item in candidate_items
            if isinstance(item, dict)
            and _assistant_float(item.get("saldo_loja", item.get("saldo_total"))) > 0
            and (item.get("dias_sem_vender") is None or _assistant_float(item.get("dias_sem_vender")) >= 30)
        ]
        candidate_summary = candidate_result.get("resumo_estoque_parado") if isinstance(candidate_result.get("resumo_estoque_parado"), dict) else {}
        candidate_score = int(candidate_summary.get("total_skus") or len(candidate_stale))
        if candidate_stale and candidate_score > best_stale_score:
            best_stale_result = candidate_result
            best_stale_items = candidate_stale
            best_stale_score = candidate_score
    if best_stale_items:
        alert = _assistant_stale_stock_alert_payload(best_stale_result, best_stale_items)
        add(
            "stale_stock",
            "Estoque parado com saldo",
            alert["detail"],
            "warning",
            "get_days_without_sale_top",
            alert["recommendation"],
            alert["report_prompt"],
        )

    for item in results:
        name = _assistant_function_name(item if isinstance(item, dict) else {})
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if name == "get_integrations_status":
            lojas = result.get("lojas") if isinstance(result.get("lojas"), list) else []
            desconectadas = [
                loja
                for loja in lojas
                if not loja.get("mercado_livre_conectado") or not loja.get("bling_conectado")
            ]
            if desconectadas:
                add(
                    "integrations",
                    "Integracoes precisam de atencao",
                    f"{len(desconectadas)} loja(s) tem Mercado Livre ou Bling desconectado/incompleto.",
                    "warning",
                    name,
                )
        elif name == "get_stockout_forecast":
            criticos = _assistant_collect_stockout_rows({"tool_results": [item]}, only_risky=True, limit=50)
            if criticos:
                primeiro = criticos[0]
                top_skus = ", ".join(str(it.get("sku") or "-") for it in criticos[:8])
                add(
                    "stockout",
                    "Risco de ruptura detectado",
                    (
                        f"{len(criticos)} SKU(s) com risco alto/critico considerando apenas saldo de loja. "
                        f"SKUs principais: {top_skus}. Primeiro: {primeiro.get('sku') or '-'} - {primeiro.get('produto') or ''}; "
                        f"saldo loja {primeiro.get('saldo_considerado')}; media/dia {primeiro.get('media_dia')}; "
                        f"ruptura prevista {primeiro.get('ruptura_prevista')}."
                    ),
                    "warning",
                    name,
                    "Gerar relatorio completo no chat com todos os SKUs, motivo por SKU e acao de reposicao de estoque de loja.",
                )
        elif name == "get_days_without_sale_top":
            continue
        elif name == "detect_sales_anomalies":
            anomalias = []
            if isinstance(result.get("anomalias"), list):
                anomalias = result.get("anomalias") or []
            elif isinstance(result.get("alertas"), list):
                anomalias = result.get("alertas") or []
            if anomalias:
                add(
                    "sales_anomaly",
                    "Anomalia de vendas no periodo",
                    f"{len(anomalias)} ponto(s) fora do padrao foram encontrados na curva de vendas.",
                    "warning",
                    name,
                )
        elif name in {"get_returns_by_period", "sales_returns_query"}:
            itens = result.get("items") or result.get("itens") or []
            if name == "sales_returns_query":
                itens = result.get("devolucoes") or result.get("por_sku") or []
            if isinstance(itens, list) and itens:
                add(
                    "returns",
                    "Devolucoes merecem revisao",
                    f"Ha {len(itens)} SKU(s)/registro(s) relevantes de devolucao no periodo consultado.",
                    "info",
                    name,
                )

    if not suggestions and mode == "proactive":
        add("ok", "Nenhum alerta critico agora", "As leituras leves nao encontraram risco operacional relevante neste ciclo.", "ok", "proactive")
    return suggestions[:8]


def _assistant_collect_data(
    client_id: str,
    message: str,
    screen_context: Any = None,
    mode: str = "chat",
    force_refresh: bool = False,
) -> dict[str, Any]:
    mode = str(mode or "chat").strip().lower() or "chat"
    source_policy = _assistant_source_routing_policy(message)
    if source_policy.get("force_refresh"):
        force_refresh = True
    strict_api_route = bool(
        set(source_policy.get("forbidden_tools") or []).intersection(
            {"sales_returns_query", "sales_ranking", "sales_summary", "returns_summary", "return_rate"}
        )
    )
    key = _assistant_cache_key(client_id, mode, message, screen_context)
    if not force_refresh:
        cached = _assistant_cache_get(client_id, key, EXTERNAL_CACHE_SECONDS)
        if cached:
            cached["cache_hit"] = True
            return cached

    warnings: list[str] = []
    all_results: list[dict[str, Any]] = []
    tool_context_parts: list[str] = []
    registry_results: list[dict[str, Any]] = []
    tool_plan: dict[str, Any] = {}
    registry_raw, registry_results, tool_plan, registry_warnings = _assistant_execute_registry(client_id, message, screen_context, mode)
    all_results.extend(registry_raw)
    warnings.extend(registry_warnings)
    registry_context = _assistant_registry_context_text(tool_plan, registry_results)
    if registry_context:
        tool_context_parts.append(registry_context)
    broad_chat = bool(re.search(r"\b(analise|analisar|relatorio|melhoria|melhorias|oportunidade|vendas|estoque|ruptura|devolucao|devolucoes)\b", _assistant_texto_norm(message)))
    if not strict_api_route and (mode in {"proactive", "daily", "report"} or broad_chat):
        all_results.extend(_assistant_direct_tools(client_id, mode if mode in {"proactive", "daily", "report"} else "report"))

    if not strict_api_route:
        for query in _assistant_prompt_queries(message, mode):
            results, context_text, query_warnings = _assistant_execute_dispatcher(client_id, query, screen_context)
            all_results.extend(results)
            if context_text:
                tool_context_parts.append(context_text)
            warnings.extend(query_warnings)

    all_results = _assistant_dedupe_results(all_results)
    sources = _assistant_sources(all_results)
    suggestions = _assistant_suggestions_from_results(all_results, mode)
    if not tool_context_parts and all_results:
        tool_context_parts.append(
            codex_turn_context.bounded_json(
                all_results[:12],
                max_bytes=CODEX_DATA_PREVIEW_CHAR_LIMIT,
                priority_paths=("field", "value", "source", "coverage", "status", "reference"),
            )
        )

    payload = {
        "success": True,
        "enabled": True,
        "client_id": str(client_id or "default"),
        "mode": mode,
        "generated_at": _assistant_now(),
        "tool_results_count": len(all_results),
        "tool_results": all_results[:40],
        "registry_results": registry_results[:80],
        "tool_plan": tool_plan,
        "status_steps": tool_plan.get("status_steps") or [],
        "tool_context": "\n\n".join(tool_context_parts)[:CODEX_DATA_CONTEXT_CHAR_LIMIT],
        "tool_results_preview": codex_turn_context.bounded_json(
            all_results[:20],
            max_bytes=CODEX_DATA_PREVIEW_CHAR_LIMIT,
            priority_paths=("field", "value", "source", "coverage", "status", "reference"),
        ),
        "sources": sources,
        "warnings": [_assistant_humanize_source_text(item)[:600] for item in warnings[:10]],
        "suggestions": suggestions,
        "cache_hit": False,
    }
    payload["management_analysis"] = _assistant_management_analysis(payload)
    _assistant_cache_set(client_id, key, payload)
    return payload


def codex_assistant_collect_context(
    prompt: str,
    screen_context: Any,
    client_id: str,
    mode: str = "chat",
    force_refresh: bool = False,
) -> dict[str, Any]:
    return _assistant_collect_data(client_id, prompt, screen_context, mode=mode, force_refresh=force_refresh)


def _assistant_answer_from_context(message: str, context: dict[str, Any]) -> str:
    sources = context.get("sources") if isinstance(context.get("sources"), list) else []
    suggestions = context.get("suggestions") if isinstance(context.get("suggestions"), list) else []
    warnings = context.get("warnings") if isinstance(context.get("warnings"), list) else []
    analysis = context.get("management_analysis") if isinstance(context.get("management_analysis"), dict) else {}
    registry_results = context.get("registry_results") if isinstance(context.get("registry_results"), list) else []
    tool_plan = context.get("tool_plan") if isinstance(context.get("tool_plan"), dict) else {}
    lines = []
    if sources:
        ext = sum(1 for src in sources if src.get("external"))
        lines.append(f"Consultei {len(sources)} fonte(s) read-only do JK Sistema" + (f", incluindo {ext} externa(s)" if ext else "") + ".")
    else:
        lines.append("Nao encontrei dados estruturados suficientes para responder com seguranca.")
    if tool_plan:
        periodo = tool_plan.get("periodo") if isinstance(tool_plan.get("periodo"), dict) else {}
        lines.append(
            "Escopo usado: "
            f"{periodo.get('data_inicio') or tool_plan.get('data_inicio') or '-'} a "
            f"{periodo.get('data_fim') or tool_plan.get('data_fim') or '-'}; "
            f"loja/conta: {tool_plan.get('loja') or 'todas'}."
        )
    sales_rank = next((item for item in registry_results if item.get("tool_id") == "sales_ranking"), None)
    if sales_rank:
        rows = sales_rank.get("rows") if isinstance(sales_rank.get("rows"), list) else []
        if rows:
            lines.append("")
            lines.append("Ranking de SKUs mais vendidos:")
            for idx, row in enumerate(rows[:15], 1):
                if not isinstance(row, dict):
                    continue
                sku = row.get("sku") or row.get("SKU") or "-"
                nome = row.get("nome") or row.get("produto") or row.get("title") or ""
                qtd = row.get("qtd") or row.get("quantidade") or row.get("quantidade_total") or 0
                valor = row.get("valor") or row.get("valor_total") or 0
                lines.append(f"{idx}. SKU {sku} | {nome} | qtd {qtd} | valor {_assistant_money(valor)}")
        elif sales_rank.get("empty_reason"):
            lines.append("")
            lines.append("Diagnostico da consulta de ranking:")
            lines.append(f"- {sales_rank.get('empty_reason')}")
            fallbacks = sales_rank.get("next_fallbacks") if isinstance(sales_rank.get("next_fallbacks"), list) else []
            if fallbacks:
                fallback_labels = _assistant_human_fallback_list(fallbacks)
                lines.append(f"- Proximas consultas alternativas: {', '.join(fallback_labels or [str(item) for item in fallbacks])}.")
    bling_sales = next((item for item in registry_results if item.get("tool_id") == "bling_sales_orders"), None)
    if bling_sales:
        rows = bling_sales.get("rows") if isinstance(bling_sales.get("rows"), list) else []
        if rows:
            lines.append("")
            lines.append("Dados de vendas consultados na Bling:")
            for idx, row in enumerate(rows[:12], 1):
                if not isinstance(row, dict):
                    continue
                sku = row.get("sku") or row.get("SKU") or row.get("id") or "-"
                nome = row.get("produto") or row.get("nome") or row.get("numero") or ""
                qtd = row.get("quantidade_vendida") or row.get("quantidade") or "-"
                valor = row.get("valor_total") or row.get("valor") or row.get("valor_total") or 0
                lines.append(f"{idx}. {sku} | {nome} | qtd {qtd} | valor {_assistant_money(valor)}")
    bling_stock = next((item for item in registry_results if item.get("tool_id") == "bling_stock_balances"), None)
    if bling_stock:
        rows = bling_stock.get("rows") if isinstance(bling_stock.get("rows"), list) else []
        if rows:
            lines.append("")
            lines.append("Saldo Bling:")
            for row in rows[:10]:
                if not isinstance(row, dict):
                    continue
                sku = row.get("sku") or "-"
                nome = row.get("produto") or ""
                saldo = row.get("saldo_total")
                lines.append(f"- SKU {sku} | {nome} | saldo total {saldo}")
    bling_fiscal = next((item for item in registry_results if item.get("tool_id") in {"bling_fiscal_product", "bling_fiscal_nfe"}), None)
    if bling_fiscal:
        rows = bling_fiscal.get("rows") if isinstance(bling_fiscal.get("rows"), list) else []
        if rows:
            lines.append("")
            lines.append("Fiscal Bling:")
            for row in rows[:8]:
                if not isinstance(row, dict):
                    continue
                ref = row.get("sku") or row.get("numero") or row.get("id") or "-"
                desc = row.get("nome") or row.get("produto") or row.get("situacao") or ""
                ncm = row.get("ncm")
                cest = row.get("cest")
                extra = f" | NCM {ncm or '-'} | CEST {cest or '-'}" if ncm or cest else ""
                lines.append(f"- {ref} | {desc}{extra}")
    stockout_rows = _assistant_collect_stockout_rows(context, only_risky=True, limit=12)
    if stockout_rows:
        lines.append("")
        lines.append("SKUs com risco de ruptura:")
        for idx, row in enumerate(stockout_rows[:8], 1):
            lines.append(
                f"{idx}. SKU {row.get('sku')} | {row.get('produto') or '-'} | risco {row.get('risco')} | "
                f"saldo loja {row.get('saldo_considerado')} | media/dia {row.get('media_dia')} | "
                f"dias ate ruptura {row.get('dias_ate_ruptura')} | motivo: {row.get('motivo')}"
            )
    kpis = analysis.get("kpis") if isinstance(analysis.get("kpis"), list) else []
    if kpis:
        lines.append("")
        lines.append("Indicadores principais:")
        for item in kpis[:6]:
            lines.append(f"- {item.get('label')}: {item.get('value')} ({item.get('detail')})")
    sections = analysis.get("sections") if isinstance(analysis.get("sections"), list) else []
    findings = []
    for section in sections:
        for finding in section.get("findings") or []:
            if isinstance(finding, dict):
                findings.append(finding)
    if findings:
        lines.append("")
        lines.append("Diagnostico especialista:")
        for item in findings[:6]:
            lines.append(f"- {item.get('title')}: {item.get('evidence')} Acao: {item.get('recommendation')}")
    actions = analysis.get("priority_actions") if isinstance(analysis.get("priority_actions"), list) else []
    if actions:
        lines.append("")
        lines.append("Prioridades sugeridas:")
        for idx, action in enumerate(actions[:5], 1):
            lines.append(f"{idx}. {action}")
    if suggestions:
        lines.append("")
        lines.append("Alertas automaticos:")
        for item in suggestions[:5]:
            lines.append(f"- {item.get('title')}: {item.get('detail')}")
    if sources:
        lines.append("")
        lines.append("Fontes consultadas:")
        for src in sources[:10]:
            lines.append(f"- {_assistant_source_display(src)} ({src.get('records')} registro(s))")
    if warnings:
        lines.append("")
        lines.append("Avisos:")
        for warning in warnings[:3]:
            lines.append(f"- {warning}")
    if not suggestions and sources:
        lines.append("")
        lines.append("Os dados estao disponiveis para o Codex responder com mais detalhes no chat principal.")
    return "\n".join(lines)


def _assistant_advanced_report_chat_text(title: str, context: dict[str, Any], report_id: str = "") -> str:
    scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
    quality = context.get("data_quality") if isinstance(context.get("data_quality"), dict) else {}
    financial = context.get("financial_summary") if isinstance(context.get("financial_summary"), dict) else {}
    coverage = context.get("financial_coverage") if isinstance(context.get("financial_coverage"), dict) else {}
    actions = context.get("top_actions") if isinstance(context.get("top_actions"), list) else []
    stores = context.get("store_summaries") if isinstance(context.get("store_summaries"), list) else []

    def money_or_unavailable(value: Any) -> str:
        return _assistant_money(value) if value is not None else "indisponivel"

    margin_coverage_text = (
        _assistant_percent(coverage.get("complete_margin_by_revenue_pct"))
        if coverage.get("complete_margin_by_revenue_pct") is not None
        else "indisponivel"
    )

    lines = [
        f"# {str(title or 'Relatorio Black Jhon')[:140]}",
        "",
        (
            f"Escopo: {scope.get('period_start') or '-'} a {scope.get('period_end') or '-'} | "
            f"comparacao: {scope.get('comparison_start') or '-'} a {scope.get('comparison_end') or '-'} | "
            f"loja: {scope.get('selected_store') or 'consolidado com blocos por loja'}"
        ),
        f"Confiabilidade: {quality.get('confidence') or 'baixa'} ({quality.get('score') or 0}/100) | estoque em: {scope.get('stock_as_of') or 'indisponivel'}",
    ]
    if report_id:
        lines.append(f"Relatorio: {report_id}")
    lines.extend(
        [
            "",
            "## Resumo financeiro",
            f"- Receita bruta: {money_or_unavailable(financial.get('gross_revenue_brl'))}",
            f"- Devolucoes: {money_or_unavailable(financial.get('returns_brl'))}",
            f"- Receita liquida: {money_or_unavailable(financial.get('net_revenue_brl'))}",
            f"- Publicidade: {money_or_unavailable(financial.get('advertising_brl'))}",
            f"- Margem de contribuicao consolidada: {money_or_unavailable(financial.get('contribution_profit_brl'))}",
            f"- Resultado de contribuicao apos publicidade: {money_or_unavailable(financial.get('net_profit_after_ads_brl'))}",
            (
                f"- Cobertura de margem: {margin_coverage_text} "
                f"(minimo {_assistant_percent(coverage.get('minimum_required_pct') or 95)}); status {coverage.get('status') or 'insufficient'}."
            ),
            "",
            "## Decisoes prioritarias",
        ]
    )
    if actions:
        for index, action in enumerate(actions[:5], 1):
            skus = ", ".join(str(item) for item in (action.get("skus") or [])[:8]) or "-"
            lines.extend(
                [
                    f"{index}. **{action.get('title') or 'Acao recomendada'}**",
                    f"   Loja: {action.get('store') or '-'} | SKUs: {skus}",
                    f"   Impacto: {action.get('impact_label') or 'nao estimavel'} | urgencia: {action.get('urgency') or '-'} | confianca: {action.get('confidence') or '-'}",
                    f"   Responsavel: {action.get('owner_username') or action.get('owner_role') or '-'} | prazo: {action.get('due_at') or '-'}",
                    f"   Acao: {action.get('recommendation') or '-'}",
                ]
            )
    else:
        lines.append("- Nenhuma acao prioritaria foi produzida com os dados confiaveis disponiveis.")
    if stores:
        lines.extend(["", "## Lojas"])
        for item in stores[:12]:
            lines.append(
                f"- {item.get('store') or '-'}: receita liquida {money_or_unavailable(item.get('net_revenue_brl'))}; "
                f"variacao {_assistant_percent(item.get('trend_pct')) if item.get('trend_pct') is not None else 'indisponivel'}; "
                f"meta {_assistant_percent(item.get('target_attainment_pct')) if item.get('target_attainment_pct') is not None else 'nao cadastrada'}."
            )
    sources = quality.get("source_health") if isinstance(quality.get("source_health"), list) else []
    if sources:
        lines.extend(["", "## Confiabilidade das fontes"])
        for item in sources[:10]:
            coverage_text = f" | cobertura {item.get('coverage_pct')}%" if item.get("coverage_pct") is not None else ""
            lines.append(f"- {item.get('source') or 'Fonte'}: {item.get('status') or 'indisponivel'} | {item.get('records') or 0} registro(s){coverage_text}.")
    warnings = quality.get("warnings") if isinstance(quality.get("warnings"), list) else []
    if warnings:
        lines.extend(["", "## Avisos de dados"])
        lines.extend(f"- {str(item)[:420]}" for item in warnings[:6])
    lines.extend(["", "Use Abrir relatorio para o HTML executivo ou baixe a Planilha XLSX para todos os SKUs."])
    return "\n".join(lines).strip()


def _assistant_report_chat_text(title: str, context: dict[str, Any], suggestions: list[dict[str, Any]], report_id: str = "") -> str:
    if str(context.get("report_type") or "").strip():
        return _assistant_advanced_report_chat_text(title, context, report_id)
    analysis = context.get("management_analysis") if isinstance(context.get("management_analysis"), dict) else _assistant_management_analysis(context)
    sources = context.get("sources") if isinstance(context.get("sources"), list) else []
    warnings = context.get("warnings") if isinstance(context.get("warnings"), list) else []
    tool_plan = context.get("tool_plan") if isinstance(context.get("tool_plan"), dict) else {}
    executive = analysis.get("executive_summary") if isinstance(analysis.get("executive_summary"), list) else []
    kpis = analysis.get("kpis") if isinstance(analysis.get("kpis"), list) else []
    sections = analysis.get("sections") if isinstance(analysis.get("sections"), list) else []
    actions = analysis.get("priority_actions") if isinstance(analysis.get("priority_actions"), list) else []

    def clean(value: Any, limit: int = 900) -> str:
        text = str(value if value is not None else "").replace("\r", " ").strip()
        text = re.sub(r"\s+", " ", text)
        return text[:limit].rstrip()

    def severity_label(value: Any) -> str:
        severity = str(value or "info").lower()
        return {
            "critical": "Critico",
            "warning": "Atencao",
            "ok": "OK",
            "info": "Info",
        }.get(severity, severity.title())

    def md(value: Any, limit: int = 260) -> str:
        return clean(value, limit).replace("|", "/")

    periodo = tool_plan.get("periodo") if isinstance(tool_plan.get("periodo"), dict) else {}
    data_inicio = periodo.get("data_inicio") or tool_plan.get("data_inicio") or "-"
    data_fim = periodo.get("data_fim") or tool_plan.get("data_fim") or "-"
    loja = tool_plan.get("loja") or "todas"
    intent = tool_plan.get("intent") or tool_plan.get("modo") or "analise operacional"
    chat_table_limit = 500
    stockout_rows = _assistant_collect_stockout_rows(context, only_risky=True, limit=chat_table_limit)
    stale_rows = _assistant_collect_stale_stock_rows(context, limit=chat_table_limit)
    sales_rows = _assistant_collect_sales_rank_rows(context, limit=chat_table_limit)
    margin_rows = _assistant_collect_margin_rows(context, limit=chat_table_limit)
    diagnostic_rows = _assistant_specialist_sku_diagnostics(context, limit=chat_table_limit)

    lines = [
        f"# {clean(title, 120)}",
        "",
        f"Gerado em: {clean(context.get('generated_at') or _assistant_now(), 80)}",
        f"Escopo: {clean(data_inicio, 40)} a {clean(data_fim, 40)} | loja/conta: {clean(loja, 120)} | foco: {clean(intent, 140)}",
    ]
    if report_id:
        lines.append(f"ID: `{clean(report_id, 80)}`")

    lines.extend(["", "## Resumo executivo"])
    if executive:
        lines.extend(f"- {clean(item, 600)}" for item in executive[:8])
    else:
        lines.append("- Nenhum resumo executivo foi montado com os dados disponiveis.")

    if kpis:
        lines.extend(["", "## Indicadores", "| Indicador | Valor | Leitura |", "|---|---:|---|"])
        for item in kpis[:12]:
            label = clean(item.get("label"), 120)
            value = clean(item.get("value"), 80)
            detail = clean(item.get("detail"), 180)
            severity = severity_label(item.get("severity"))
            lines.append(f"| {label} | {value} | {severity}: {detail} |")

    if diagnostic_rows:
        lines.extend(
            [
                "",
                "## Diagnostico especialista por SKU",
                "Cada linha cruza vendas, saldo de loja, margem/custo, ruptura e estoque parado. O estoque Full nao entra nas decisoes de ruptura/reposicao de loja.",
                "| SKU | Produto | Motivo | Risco | Impacto financeiro | Margem | Custo ausente | Acao recomendada | Prioridade | Responsavel |",
                "|---|---|---|---|---|---:|---|---|---|---|",
            ]
        )
        for row in diagnostic_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        md(row.get("SKU"), 80),
                        md(row.get("Produto"), 170),
                        md(row.get("Motivo"), 360),
                        md(row.get("Risco"), 60),
                        md(row.get("Impacto financeiro"), 220),
                        md(row.get("Margem"), 80),
                        md(row.get("Custo ausente"), 50),
                        md(row.get("Acao recomendada"), 320),
                        md(row.get("Prioridade"), 80),
                        md(row.get("Responsavel"), 100),
                    ]
                )
                + " |"
            )

    if stockout_rows:
        lines.extend(
            [
                "",
                "## SKUs em risco de ruptura",
                "A analise cruza somente o saldo de loja com a media diaria de vendas. O estoque Full nao entra neste calculo.",
                "| SKU | Produto | Risco | Saldo loja considerado | Media/dia | Dias | Ruptura prevista | Motivo | Acao |",
                "|---|---|---|---:|---:|---:|---|---|---|",
            ]
        )
        for row in stockout_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        md(row.get("sku"), 80),
                        md(row.get("produto"), 170),
                        md(row.get("risco"), 50),
                        md(row.get("saldo_considerado"), 40),
                        md(row.get("media_dia"), 40),
                        md(row.get("dias_ate_ruptura"), 40),
                        md(row.get("ruptura_prevista"), 80),
                        md(row.get("motivo"), 360),
                        md(row.get("acao_recomendada"), 320),
                    ]
                )
                + " |"
            )

    if stale_rows:
        lines.extend(
            [
                "",
                "## Estoque parado com saldo de loja",
                "A fila esta ordenada por urgencia. O saldo Full aparece somente como contexto e nao reduz o estoque parado da loja.",
                "| Prioridade | SKU | Produto | Saldo loja | Saldo Full* | Situacao | Ultima venda | Capital a custo | Origem do custo | Motivo | Acao |",
                "|---|---|---|---:|---:|---|---|---:|---|---|---|",
            ]
        )
        for row in stale_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        md(row.get("prioridade"), 80),
                        md(row.get("sku"), 80),
                        md(row.get("produto"), 180),
                        md(row.get("saldo_loja"), 50),
                        md(row.get("saldo_full"), 50),
                        md(row.get("situacao"), 100),
                        md(row.get("ultima_venda"), 90),
                        md(row.get("capital_custo"), 100),
                        md(row.get("custo_origem"), 100),
                        md(row.get("motivo"), 280),
                        md(row.get("acao_recomendada"), 320),
                    ]
                )
                + " |"
            )

    if sales_rows:
        lines.extend(
            [
                "",
                "## SKUs que puxam vendas",
                "| SKU | Produto | Quantidade | Valor | Pedidos | Analise |",
                "|---|---|---:|---:|---:|---|",
            ]
        )
        for row in sales_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        md(row.get("sku"), 80),
                        md(row.get("produto"), 190),
                        md(row.get("quantidade_vendida"), 60),
                        md(row.get("valor_vendido"), 80),
                        md(row.get("pedidos"), 60),
                        md(row.get("analise"), 260),
                    ]
                )
                + " |"
            )

    if margin_rows:
        lines.extend(
            [
                "",
                "## Margem real por SKU vendido",
                "A margem usa custo/imposto do cadastro e a formula do Favoritos. Quando faltar custo, frete, tarifa ou imposto, o status fica incompleto.",
                "| SKU | Produto | Qtd vendida | Valor vendido | Custo unitario | Custo total | Imposto | Frete | Tarifa | Lucro estimado | Margem % | Status |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in margin_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        md(row.get("SKU"), 80),
                        md(row.get("Produto"), 180),
                        md(row.get("Qtd vendida"), 60),
                        md(row.get("Valor vendido"), 80),
                        md(row.get("Custo unitario"), 80),
                        md(row.get("Custo total"), 80),
                        md(row.get("Imposto"), 60),
                        md(row.get("Frete"), 70),
                        md(row.get("Tarifa"), 70),
                        md(row.get("Lucro estimado"), 90),
                        md(row.get("Margem %"), 70),
                        md(row.get("Status da margem"), 120),
                    ]
                )
                + " |"
            )

    if actions:
        lines.extend(["", "## Prioridades recomendadas"])
        for idx, action in enumerate(actions[:10], 1):
            lines.append(f"{idx}. {clean(action, 700)}")

    lines.extend(["", "## Diagnostico por area"])
    if sections:
        for section in sections[:10]:
            section_title = clean(section.get("title"), 120)
            findings = section.get("findings") if isinstance(section.get("findings"), list) else []
            if not findings:
                continue
            lines.extend(["", f"### {section_title}"])
            for finding in findings[:8]:
                if not isinstance(finding, dict):
                    continue
                marker = severity_label(finding.get("severity"))
                title_line = clean(finding.get("title"), 180)
                evidence = clean(finding.get("evidence"), 650)
                impact = clean(finding.get("impact"), 420)
                recommendation = clean(finding.get("recommendation"), 650)
                source = clean(_assistant_human_source_label(finding.get("source"), finding.get("source")), 140)
                lines.append(f"- **{marker} - {title_line}**")
                if evidence:
                    lines.append(f"  Evidencia: {evidence}")
                if impact:
                    lines.append(f"  Impacto: {impact}")
                if recommendation:
                    lines.append(f"  Acao sugerida: {recommendation}")
                if source:
                    lines.append(f"  Fonte: `{source}`")
    else:
        lines.append("- Nenhum diagnostico por area foi produzido com as consultas executadas.")

    if suggestions:
        lines.extend(["", "## Alertas automaticos"])
        for item in suggestions[:12]:
            if not isinstance(item, dict):
                continue
            title_alert = clean(item.get("title"), 160)
            detail = clean(item.get("detail"), 520)
            recommendation = clean(item.get("recommendation"), 520)
            line = f"- **{title_alert}**: {detail}"
            if recommendation:
                line += f" Acao: {recommendation}"
            lines.append(line)

    if sources:
        lines.extend(["", "## Fontes consultadas"])
        for src in sources[:18]:
            if not isinstance(src, dict):
                continue
            flags = []
            if src.get("external"):
                flags.append("externa")
            if src.get("read_only"):
                flags.append("read-only")
            suffix = f" ({', '.join(flags)})" if flags else ""
            lines.append(f"- {clean(_assistant_source_display(src), 140)}: {clean(src.get('records'), 40)} registro(s){suffix}.")

    if warnings:
        lines.extend(["", "## Avisos de dados"])
        for warning in warnings[:10]:
            lines.append(f"- {clean(warning, 500)}")

    lines.extend(["", "Os botoes abaixo tambem deixam uma copia em PDF ou Planilha XLSX para baixar."])
    return "\n".join(lines).strip()


def _assistant_report_dir(client_id: str, report_id: str) -> str:
    path = os.path.join(_assistant_client_dir(client_id), "reports", _assistant_safe_id(report_id))
    os.makedirs(path, exist_ok=True)
    return path


def _assistant_flatten_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in results:
        raw_name = str(item.get("function") or "consulta")
        name = _assistant_human_source_label(raw_name, raw_name)
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        collections = []
        for key in ("vendas", "devolucoes", "por_sku", "por_loja", "items", "itens", "matches", "lojas", "anomalias", "top_skus"):
            value = result.get(key)
            if isinstance(value, list):
                collections.append((key, value))
        if not collections:
            rows.append(
                {
                    "fonte": name,
                    "tipo": "resumo",
                    "dados": codex_turn_context.bounded_json(result, max_bytes=1000),
                }
            )
            continue
        for key, values in collections:
            limit = len(values) if name == "sales_returns_query" and key in {"vendas", "devolucoes", "por_sku", "por_loja"} else min(len(values), 500)
            for value in values[:limit]:
                if isinstance(value, dict):
                    row = {"fonte": name, "tipo": key}
                    for k, v in value.items():
                        if isinstance(v, (dict, list)):
                            row[str(k)] = codex_turn_context.bounded_json(v, max_bytes=600)
                        else:
                            row[str(k)] = v
                    rows.append(row)
    return rows[:5000]


def _assistant_build_advanced_report_html(title: str, context: dict[str, Any]) -> str:
    scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
    quality = context.get("data_quality") if isinstance(context.get("data_quality"), dict) else {}
    financial = context.get("financial_summary") if isinstance(context.get("financial_summary"), dict) else {}
    coverage = context.get("financial_coverage") if isinstance(context.get("financial_coverage"), dict) else {}
    actions = context.get("top_actions") if isinstance(context.get("top_actions"), list) else []

    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else ""))

    def money(value: Any) -> str:
        return _assistant_money(value) if value is not None else "Indisponivel"

    margin_coverage_text = (
        _assistant_percent(coverage.get("complete_margin_by_revenue_pct"))
        if coverage.get("complete_margin_by_revenue_pct") is not None
        else "Indisponivel"
    )

    def table(section_title: str, rows: Any, columns: list[tuple[str, str]]) -> str:
        values = [item for item in (rows if isinstance(rows, list) else []) if isinstance(item, dict)][:20]
        if not values:
            return ""
        parts = [f"<h2>{esc(section_title)}</h2><div class=\"table-wrap\"><table><thead><tr>"]
        parts.extend(f"<th>{esc(label)}</th>" for key, label in columns)
        parts.append("</tr></thead><tbody>")
        for row in values:
            parts.append("<tr>")
            for key, _ in columns:
                value = row.get(key)
                if (key.endswith("_brl") or key in {"revenue", "impact_brl"}) and value is not None:
                    value = _assistant_money(value)
                elif key.endswith("_pct") and value is not None:
                    value = _assistant_percent(value)
                parts.append(f"<td>{esc(value if value is not None else 'Indisponivel')}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table></div>")
        return "".join(parts)

    quality_class = "ok" if quality.get("confidence") == "alta" else "warning" if quality.get("confidence") == "média" else "critical"
    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        f"<title>{esc(title)}</title>",
        "<style>body{font-family:Arial,sans-serif;margin:28px;color:#13202c;background:#fff;line-height:1.42}h1{font-size:25px;margin:0 0 8px}h2{font-size:18px;margin:24px 0 10px;color:#0f3557}.meta{color:#526170;font-size:12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px}.card{border:1px solid #d5dde6;border-radius:9px;padding:11px;background:#f8fbff}.card strong{display:block;font-size:17px;margin-top:3px}.quality{border-left:6px solid #d97706}.quality.ok{border-left-color:#059669}.quality.critical{border-left-color:#dc2626}.action{border:1px solid #d5dde6;border-left:5px solid #2563eb;border-radius:9px;padding:12px;margin:9px 0;background:#fbfdff}.action.immediate{border-left-color:#dc2626}.action.high{border-left-color:#ea580c}.pill{display:inline-block;padding:3px 8px;border-radius:999px;background:#e8f5f2;color:#075d56;font-size:11px;font-weight:700;margin-right:5px}.table-wrap{overflow:auto;max-width:100%}table{border-collapse:collapse;width:100%;font-size:12px}td,th{border:1px solid #d5dde6;padding:7px;text-align:left;vertical-align:top}th{background:#eef5ff;color:#0f3557;white-space:nowrap}.warning-text{color:#9a3412}.footer{margin-top:26px;border-top:1px solid #d5dde6;padding-top:10px;color:#526170;font-size:11px}</style></head><body>",
        f"<h1>{esc(title)}</h1>",
        f"<p class=\"meta\">Gerado em {esc(scope.get('generated_at') or _assistant_now())}. Periodo {esc(scope.get('period_start') or '-')} a {esc(scope.get('period_end') or '-')}; comparacao {esc(scope.get('comparison_start') or '-')} a {esc(scope.get('comparison_end') or '-')}.</p>",
        "<h2>Confiabilidade dos dados</h2>",
        f"<div class=\"card quality {quality_class}\"><span class=\"pill\">{esc(quality.get('confidence') or 'baixa')}</span><strong>{esc(quality.get('score') or 0)}/100</strong><span>Estoque em {esc(scope.get('stock_as_of') or 'indisponivel')}; loja {esc(scope.get('selected_store') or 'consolidado com blocos por loja')}.</span></div>",
        "<h2>Resumo financeiro</h2><div class=\"grid\">",
    ]
    financial_cards = [
        ("Receita bruta", financial.get("gross_revenue_brl")),
        ("Devolucoes", financial.get("returns_brl")),
        ("Receita liquida", financial.get("net_revenue_brl")),
        ("Publicidade", financial.get("advertising_brl")),
        ("Margem de contribuicao", financial.get("contribution_profit_brl")),
        ("Resultado de contribuicao apos publicidade", financial.get("net_profit_after_ads_brl")),
        ("Resultado de contribuicao apos publicidade e devolucoes reconciliadas", financial.get("net_profit_after_ads_and_returns_brl")),
    ]
    for label, value in financial_cards:
        parts.append(f"<div class=\"card\"><span>{esc(label)}</span><strong>{esc(money(value))}</strong></div>")
    parts.append("</div>")
    parts.append(
        f"<p class=\"meta\">Cobertura completa de margem: {esc(margin_coverage_text)}; "
        f"minimo exigido: {esc(_assistant_percent(coverage.get('minimum_required_pct') or 95))}. "
        f"Receita coberta: {esc(money(coverage.get('covered_revenue_brl')))}; "
        f"receita nao coberta: {esc(money(coverage.get('uncovered_revenue_brl')))}. "
        "Valores ausentes nao foram tratados como zero.</p>"
    )
    parts.append(
        table(
            "Contribuicao unitaria atual por anuncio e variacao",
            context.get("listing_margin_rows"),
            [("store", "Loja"), ("mlb", "MLB"), ("variation_id", "Variacao"), ("sku", "SKU"),
             ("current_price_brl", "Preco atual"), ("unit_cost_brl", "Custo"), ("tax_pct", "Imposto %"),
             ("ml_fee_brl", "Tarifa ML"), ("seller_shipping_brl", "Frete vendedor"),
             ("unit_contribution_brl", "Contribuicao"), ("contribution_margin_pct", "Margem %"),
             ("margin_status_label", "Status"), ("missing_components_text", "Ausentes")],
        )
    )
    parts.append(
        table(
            "Resultado historico reconciliado por anuncio",
            context.get("historical_margin_ledger"),
            [("store", "Loja"), ("order_id", "Pedido"), ("line_number", "Linha"), ("pack_id", "Pack"), ("mlb", "MLB"),
             ("variation_id", "Variacao"), ("sku", "SKU"), ("quantity", "Qtd"),
             ("gross_amount_brl", "Receita vendida"), ("contribution_total_brl", "Contribuicao"),
             ("shipping_scope_label", "Escopo frete"), ("margin_status_label", "Status"),
             ("missing_components_text", "Ausentes")],
        )
    )
    parts.append("<h2>Decisoes prioritarias</h2>")
    if not actions:
        parts.append("<p>Nenhuma acao prioritaria foi produzida com os dados confiaveis disponiveis.</p>")
    for action in actions[:5]:
        urgency = str(action.get("urgency") or "medium")
        parts.append(f"<div class=\"action {esc(urgency)}\">")
        parts.append(f"<strong>{esc(action.get('title') or 'Acao recomendada')}</strong>")
        parts.append(
            f"<p><span class=\"pill\">{esc(action.get('impact_label') or 'nao estimavel')}</span>"
            f"<span class=\"pill\">urgencia {esc(urgency)}</span><span class=\"pill\">confianca {esc(action.get('confidence') or '-')}</span></p>"
        )
        parts.append(f"<p>{esc(action.get('evidence') or '')}</p><p><strong>Acao:</strong> {esc(action.get('recommendation') or '-')}</p>")
        parts.append(f"<p class=\"meta\">Loja {esc(action.get('store') or '-')}; SKUs {esc(', '.join(str(item) for item in (action.get('skus') or [])[:12]) or '-')}; responsavel {esc(action.get('owner_username') or action.get('owner_role') or '-')}; prazo {esc(action.get('due_at') or '-')}.</p></div>")
    parts.append(
        table(
            "Desempenho por loja",
            context.get("store_summaries"),
            [("store", "Loja"), ("net_revenue_brl", "Receita liquida"), ("trend_pct", "Variacao"), ("target_attainment_pct", "Meta"), ("orders", "Pedidos"), ("units", "Unidades")],
        )
    )
    parts.append(
        table(
            "SKUs que puxam vendas",
            context.get("sales_rows"),
            [("store", "Loja"), ("sku", "SKU"), ("product", "Produto"), ("abc", "ABC"), ("revenue", "Receita"), ("revenue_share_pct", "Participacao"), ("trend_pct", "Tendencia")],
        )
    )
    parts.append(
        table(
            "Planejamento de estoque",
            context.get("inventory_rows"),
            [("store", "Loja"), ("sku", "SKU"), ("abc", "ABC"), ("xyz", "XYZ"), ("local_stock", "Local"), ("full_stock", "Full"), ("coverage_days", "Cobertura dias"), ("reorder_point", "Ponto reposicao"), ("suggested_purchase", "Compra sugerida"), ("capital_tied_brl", "Capital parado")],
        )
    )
    parts.append(
        table(
            "Compras e mercadoria em fluxo",
            context.get("purchase_pipeline_rows"),
            [("store", "Loja"), ("order_name", "Pedido"), ("supplier", "Fornecedor"), ("status", "Status"), ("sku", "SKU"), ("quantity", "Quantidade"), ("eta", "Previsao")],
        )
    )
    import_analysis = context.get("import_analysis") if isinstance(context.get("import_analysis"), dict) else None
    if import_analysis:
        parts.append("<h2>Importacao</h2><div class=\"grid\">")
        for label, value in (
            ("FOB", f"US$ {_assistant_float(import_analysis.get('fob_usd')):,.2f}"),
            ("Frete internacional", f"US$ {_assistant_float(import_analysis.get('freight_usd')):,.2f}"),
            ("Caixa necessario", money(import_analysis.get("cash_required_brl"))),
            ("Faixa tributaria", _assistant_percent(import_analysis.get("tax_band_pct") or 0)),
        ):
            parts.append(f"<div class=\"card\"><span>{esc(label)}</span><strong>{esc(value)}</strong></div>")
        parts.append("</div>")
        parts.append(
            table(
                "Custo posto por SKU",
                import_analysis.get("items"),
                [("sku", "SKU"), ("quantity", "Quantidade"), ("fob_usd", "FOB USD"), ("landed_total_brl", "Custo posto total"), ("landed_unit_brl", "Custo posto unitario"), ("minimum_sale_price_brl", "Preco minimo")],
            )
        )
        parts.append(table("Cenarios", import_analysis.get("scenarios"), [("kind", "Cenario"), ("change_pct", "Variacao"), ("delay_days", "Atraso dias"), ("cash_required_brl", "Caixa necessario")]))
    sources = quality.get("source_health") if isinstance(quality.get("source_health"), list) else []
    parts.append(table("Fontes consultadas", sources, [("source", "Fonte"), ("status", "Status"), ("records", "Registros"), ("last_sync_at", "Atualizacao"), ("coverage_pct", "Cobertura")]))
    warnings = quality.get("warnings") if isinstance(quality.get("warnings"), list) else []
    if warnings:
        parts.append("<h2>Avisos</h2><ul class=\"warning-text\">")
        parts.extend(f"<li>{esc(item)}</li>" for item in warnings[:20])
        parts.append("</ul>")
    parts.append("<p class=\"footer\">Relatorio executivo read-only. A Planilha XLSX contem todos os SKUs. Acoes internas exigem aprovacao de administrador full e nunca alteram anuncios ou estoque externo.</p></body></html>")
    return "".join(parts)


def _assistant_build_report_html(title: str, context: dict[str, Any], suggestions: list[dict[str, Any]]) -> str:
    if str(context.get("report_type") or "").strip():
        return _assistant_build_advanced_report_html(title, context)
    generated = html.escape(str(context.get("generated_at") or _assistant_now()))
    sources = context.get("sources") if isinstance(context.get("sources"), list) else []
    warnings = context.get("warnings") if isinstance(context.get("warnings"), list) else []
    rows = _assistant_flatten_rows(context.get("tool_results") if isinstance(context.get("tool_results"), list) else [])
    analysis = context.get("management_analysis") if isinstance(context.get("management_analysis"), dict) else _assistant_management_analysis(context)
    kpis = analysis.get("kpis") if isinstance(analysis.get("kpis"), list) else []
    sections = analysis.get("sections") if isinstance(analysis.get("sections"), list) else []
    actions = analysis.get("priority_actions") if isinstance(analysis.get("priority_actions"), list) else []
    executive = analysis.get("executive_summary") if isinstance(analysis.get("executive_summary"), list) else []
    tool_plan = context.get("tool_plan") if isinstance(context.get("tool_plan"), dict) else {}
    stockout_rows = _assistant_collect_stockout_rows(context, only_risky=True, limit=120)
    stale_rows = _assistant_collect_stale_stock_rows(context, limit=120)
    sales_rows = _assistant_collect_sales_rank_rows(context, limit=60)
    diagnostic_rows = _assistant_specialist_sku_diagnostics(context, limit=500)
    margin_rows = _assistant_collect_margin_rows(context, limit=500)

    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else ""))

    def severity_label(value: Any) -> str:
        severity = str(value or "info")
        labels = {"critical": "Critico", "warning": "Atencao", "ok": "OK", "info": "Info"}
        return labels.get(severity, severity.title())

    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        f"<title>{esc(title)}</title>",
        "<style>"
        "body{font-family:Arial,sans-serif;margin:28px;color:#13202c;background:#fff;line-height:1.42}"
        "h1{font-size:25px;margin:0 0 10px}h2{font-size:18px;margin:26px 0 10px;color:#0f3557}h3{font-size:15px;margin:18px 0 8px;color:#13202c}"
        "p{margin:7px 0}.meta{color:#516070;font-size:12px}.pill{display:inline-block;padding:4px 9px;border-radius:99px;background:#e8f5f2;color:#075d56;font-weight:700}"
        ".kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0 18px}"
        ".kpi{border:1px solid #d5dde6;border-left:5px solid #4d8fd9;border-radius:8px;padding:10px;background:#f8fbff}"
        ".kpi strong{display:block;font-size:18px;margin-top:2px}.kpi span{font-size:12px;color:#536271}"
        ".insight{border:1px solid #d5dde6;border-radius:10px;background:#f8fbff;padding:12px;margin:12px 0}.insight strong{color:#0f3557}"
        ".finding{border:1px solid #d5dde6;border-radius:8px;padding:10px 12px;margin:9px 0;background:#fbfdff}"
        ".finding.critical{border-left:5px solid #dc2626}.finding.warning{border-left:5px solid #f59e0b}.finding.ok{border-left:5px solid #059669}.finding.info{border-left:5px solid #3b82f6}"
        ".tag{display:inline-block;font-size:11px;font-weight:800;border-radius:99px;padding:2px 7px;background:#eef2ff;color:#3730a3;margin-right:6px}"
        ".action-list li{margin:7px 0}table{border-collapse:collapse;width:100%;font-size:12px;margin:8px 0 18px}td,th{border:1px solid #d5dde6;padding:7px;text-align:left;vertical-align:top}th{background:#eef5ff;color:#0f3557}.warn{color:#9a3412}.num{text-align:right;white-space:nowrap}.critical-cell{color:#b91c1c;font-weight:800}.muted{color:#516070;font-size:12px}"
        "</style>",
        "</head><body>",
        f"<h1>{esc(title)}</h1>",
        f"<p><span class=\"pill\">Gerado em {generated}</span></p>",
        "<p class=\"meta\">Relatorio gerencial read-only. Alteracoes em arquivos, anuncios, sincronizacoes ou respostas externas continuam exigindo aprovacao explicita.</p>",
        "<h2>Resumo executivo</h2>",
    ]
    if executive:
        parts.append("<ul>")
        for item in executive:
            parts.append(f"<li>{esc(item)}</li>")
        parts.append("</ul>")
    else:
        parts.append("<p>Nao houve resumo executivo disponivel para os dados coletados.</p>")

    if tool_plan:
        parts.append("<h2>Escopo do relatorio</h2>")
        parts.append(
            f"<p>Periodo usado: <strong>{esc(tool_plan.get('data_inicio') or '-')} a {esc(tool_plan.get('data_fim') or '-')}</strong>. "
            f"Loja/conta: <strong>{esc(tool_plan.get('loja') or 'todas')}</strong>. "
            f"Foco: <strong>{esc(tool_plan.get('intent') or '-')}</strong>.</p>"
        )

    if kpis:
        parts.append("<h2>Indicadores-chave</h2><div class=\"kpis\">")
        for item in kpis:
            severity = esc(item.get("severity") or "info")
            parts.append(
                f"<div class=\"kpi {severity}\"><span>{esc(item.get('label'))}</span>"
                f"<strong>{esc(item.get('value'))}</strong><span>{esc(item.get('detail'))}</span></div>"
            )
        parts.append("</div>")

    if diagnostic_rows or stockout_rows or stale_rows or sales_rows or margin_rows:
        parts.append("<h2>Tabelas operacionais</h2>")
    if diagnostic_rows:
        parts.append(
            "<div class=\"insight\"><strong>Diagnostico especialista:</strong> esta tabela cruza vendas, saldo de loja, margem/custo, ruptura e estoque parado. "
            "O estoque Full nao entra nas decisoes de ruptura ou reposicao de loja.</div>"
        )
        parts.append(
            "<h3>Diagnostico especialista por SKU</h3>"
            "<table><thead><tr><th>SKU</th><th>Produto</th><th>Motivo</th><th>Risco</th><th>Impacto financeiro</th><th>Margem</th><th>Custo ausente</th><th>Acao recomendada</th><th>Prioridade</th><th>Responsavel</th></tr></thead><tbody>"
        )
        for row in diagnostic_rows:
            risk_class = "critical-cell" if str(row.get("Risco") or "").lower() in {"critico", "alto"} else ""
            parts.append(
                "<tr>"
                f"<td>{esc(row.get('SKU'))}</td>"
                f"<td>{esc(row.get('Produto'))}</td>"
                f"<td>{esc(row.get('Motivo'))}</td>"
                f"<td class=\"{risk_class}\">{esc(row.get('Risco'))}</td>"
                f"<td>{esc(row.get('Impacto financeiro'))}</td>"
                f"<td class=\"num\">{esc(row.get('Margem'))}</td>"
                f"<td>{esc(row.get('Custo ausente'))}</td>"
                f"<td>{esc(row.get('Acao recomendada'))}</td>"
                f"<td>{esc(row.get('Prioridade'))}</td>"
                f"<td>{esc(row.get('Responsavel'))}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
    if stockout_rows:
        parts.append(
            "<div class=\"insight\"><strong>Leitura da ruptura:</strong> a tabela abaixo cruza apenas o saldo de loja "
            "com a velocidade de venda. O estoque Full nao entra no calculo deste relatorio.</div>"
        )
        parts.append(
            "<h3>SKUs em risco de ruptura</h3>"
            "<table><thead><tr><th>SKU</th><th>Produto</th><th>Risco</th><th>Saldo loja considerado</th><th>Media/dia</th><th>Dias</th><th>Previsao</th><th>Motivo</th><th>Acao recomendada</th></tr></thead><tbody>"
        )
        for row in stockout_rows:
            risk_class = "critical-cell" if str(row.get("risco") or "").lower() in {"critico", "alto"} else ""
            parts.append(
                "<tr>"
                f"<td>{esc(row.get('sku'))}</td>"
                f"<td>{esc(row.get('produto'))}</td>"
                f"<td class=\"{risk_class}\">{esc(row.get('risco'))}</td>"
                f"<td class=\"num\">{esc(row.get('saldo_considerado'))}</td>"
                f"<td class=\"num\">{esc(row.get('media_dia'))}</td>"
                f"<td class=\"num\">{esc(row.get('dias_ate_ruptura'))}</td>"
                f"<td>{esc(row.get('ruptura_prevista'))}</td>"
                f"<td>{esc(row.get('motivo'))}</td>"
                f"<td>{esc(row.get('acao_recomendada'))}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
    if stale_rows:
        parts.append(
            "<h3>Estoque parado com saldo de loja</h3>"
            "<p class=\"muted\">Fila ordenada por urgencia. O saldo Full aparece somente como contexto e nao reduz o estoque parado da loja. Capital indisponivel significa custo ausente, nunca custo zero presumido.</p>"
            "<table><thead><tr><th>Prioridade</th><th>SKU</th><th>Produto</th><th>Saldo loja</th><th>Saldo Full*</th><th>Situacao</th><th>Ultima venda</th><th>Capital a custo</th><th>Origem do custo</th><th>Motivo</th><th>Acao recomendada</th></tr></thead><tbody>"
        )
        for row in stale_rows:
            priority_class = "critical-cell" if str(row.get("prioridade") or "").startswith("1") else ""
            parts.append(
                "<tr>"
                f"<td class=\"{priority_class}\">{esc(row.get('prioridade'))}</td>"
                f"<td>{esc(row.get('sku'))}</td>"
                f"<td>{esc(row.get('produto'))}</td>"
                f"<td class=\"num\">{esc(row.get('saldo_loja'))}</td>"
                f"<td class=\"num\">{esc(row.get('saldo_full'))}</td>"
                f"<td>{esc(row.get('situacao'))}</td>"
                f"<td>{esc(row.get('ultima_venda'))}</td>"
                f"<td class=\"num\">{esc(row.get('capital_custo'))}</td>"
                f"<td>{esc(row.get('custo_origem'))}</td>"
                f"<td>{esc(row.get('motivo'))}</td>"
                f"<td>{esc(row.get('acao_recomendada'))}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
    if sales_rows:
        parts.append(
            "<h3>SKUs que puxam vendas</h3>"
            "<p class=\"muted\">Use esta lista para cruzar giro, reposicao, margem e investimento em anuncio.</p>"
            "<table><thead><tr><th>SKU</th><th>Produto</th><th>Quantidade</th><th>Valor</th><th>Pedidos</th><th>Analise</th></tr></thead><tbody>"
        )
        for row in sales_rows:
            parts.append(
                "<tr>"
                f"<td>{esc(row.get('sku'))}</td>"
                f"<td>{esc(row.get('produto'))}</td>"
                f"<td class=\"num\">{esc(row.get('quantidade_vendida'))}</td>"
                f"<td class=\"num\">{esc(row.get('valor_vendido'))}</td>"
                f"<td class=\"num\">{esc(row.get('pedidos'))}</td>"
                f"<td>{esc(row.get('analise'))}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
    if margin_rows:
        parts.append(
            "<h3>Margem real por SKU vendido</h3>"
            "<p class=\"muted\">A margem usa custo/imposto do cadastro e a formula do Favoritos. Status incompleto indica dados que nao devem ser inventados.</p>"
            "<table><thead><tr><th>SKU</th><th>Produto</th><th>Qtd vendida</th><th>Valor vendido</th><th>Custo unitario</th><th>Custo total</th><th>Imposto</th><th>Frete</th><th>Tarifa</th><th>Lucro estimado</th><th>Margem %</th><th>Status</th></tr></thead><tbody>"
        )
        for row in margin_rows:
            parts.append(
                "<tr>"
                f"<td>{esc(row.get('SKU'))}</td>"
                f"<td>{esc(row.get('Produto'))}</td>"
                f"<td class=\"num\">{esc(row.get('Qtd vendida'))}</td>"
                f"<td class=\"num\">{esc(row.get('Valor vendido'))}</td>"
                f"<td class=\"num\">{esc(row.get('Custo unitario'))}</td>"
                f"<td class=\"num\">{esc(row.get('Custo total'))}</td>"
                f"<td class=\"num\">{esc(row.get('Imposto'))}</td>"
                f"<td class=\"num\">{esc(row.get('Frete'))}</td>"
                f"<td class=\"num\">{esc(row.get('Tarifa'))}</td>"
                f"<td class=\"num\">{esc(row.get('Lucro estimado'))}</td>"
                f"<td class=\"num\">{esc(row.get('Margem %'))}</td>"
                f"<td>{esc(row.get('Status da margem'))}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")

    if actions:
        parts.append("<h2>Prioridades de acao</h2><ol class=\"action-list\">")
        for action in actions[:10]:
            parts.append(f"<li>{esc(action)}</li>")
        parts.append("</ol>")

    if sections:
        parts.append("<h2>Diagnostico por area</h2>")
        for section in sections:
            parts.append(f"<h3>{esc(section.get('title'))}</h3>")
            for finding in (section.get("findings") or [])[:12]:
                severity = str(finding.get("severity") or "info")
                parts.append(f"<div class=\"finding {esc(severity)}\">")
                parts.append(f"<p><span class=\"tag\">{esc(severity_label(severity))}</span><strong>{esc(finding.get('title'))}</strong></p>")
                if finding.get("evidence"):
                    parts.append(f"<p><strong>Evidencia:</strong> {esc(finding.get('evidence'))}</p>")
                if finding.get("impact"):
                    parts.append(f"<p><strong>Impacto:</strong> {esc(finding.get('impact'))}</p>")
                if finding.get("recommendation"):
                    parts.append(f"<p><strong>Recomendacao:</strong> {esc(finding.get('recommendation'))}</p>")
                if finding.get("source"):
                    parts.append(f"<p class=\"meta\">Fonte: {esc(_assistant_human_source_label(finding.get('source'), finding.get('source')))}</p>")
                parts.append("</div>")
    elif suggestions:
        parts.append("<h2>Alertas automaticos</h2><ul>")
        for item in suggestions[:12]:
            parts.append(f"<li><strong>{esc(item.get('title'))}</strong>: {esc(item.get('detail'))}</li>")
        parts.append("</ul>")
    else:
        parts.append("<h2>Diagnostico por area</h2><p>Nenhuma oportunidade critica foi detectada pelos leitores automaticos.</p>")

    parts.append("<h2>Fontes consultadas</h2><ul>")
    for src in sources:
        ext = " externa" if src.get("external") else ""
        readonly = " read-only" if src.get("read_only") else ""
        parts.append(f"<li>{esc(_assistant_source_display(src))}: {esc(src.get('records'))} registro(s){ext}{readonly}</li>")
    parts.append("</ul>")
    if warnings:
        parts.append("<h2>Avisos</h2><ul>")
        for warning in warnings:
            parts.append(f"<li class=\"warn\">{esc(warning)}</li>")
        parts.append("</ul>")
    if rows:
        headers = sorted({key for row in rows[:200] for key in row.keys()})[:24]
        parts.append("<h2>Dados estruturados</h2><table><thead><tr>")
        for header in headers:
            parts.append(f"<th>{esc(header)}</th>")
        parts.append("</tr></thead><tbody>")
        for row in rows[:500]:
            parts.append("<tr>")
            for header in headers:
                parts.append(f"<td>{esc(row.get(header, ''))}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")
    parts.append("</body></html>")
    return "".join(parts)


def _assistant_write_advanced_xlsx(path: str, context: dict[str, Any]) -> None:
    import pandas as pd

    scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
    quality = context.get("data_quality") if isinstance(context.get("data_quality"), dict) else {}
    financial = context.get("financial_summary") if isinstance(context.get("financial_summary"), dict) else {}
    coverage = context.get("financial_coverage") if isinstance(context.get("financial_coverage"), dict) else {}
    summary_rows = [
        {"Indicador": "Perfil", "Valor": context.get("report_type") or ""},
        {"Indicador": "Periodo", "Valor": f"{scope.get('period_start') or '-'} a {scope.get('period_end') or '-'}"},
        {"Indicador": "Comparacao", "Valor": f"{scope.get('comparison_start') or '-'} a {scope.get('comparison_end') or '-'}"},
        {"Indicador": "Loja", "Valor": scope.get("selected_store") or "Consolidado"},
        {"Indicador": "Estoque em", "Valor": scope.get("stock_as_of") or "Indisponivel"},
        {"Indicador": "Confiabilidade", "Valor": f"{quality.get('confidence') or 'baixa'} ({quality.get('score') or 0}/100)"},
    ]
    summary_rows.extend({"Indicador": str(key), "Valor": value if value is not None else "Indisponivel"} for key, value in financial.items())
    summary_rows.extend({"Indicador": f"cobertura_{key}", "Valor": value if value is not None else "Indisponivel"} for key, value in coverage.items())
    actions = []
    for action in context.get("top_actions") or []:
        if not isinstance(action, dict):
            continue
        actions.append({**action, "skus": ", ".join(str(item) for item in action.get("skus") or [])})
    import_analysis = context.get("import_analysis") if isinstance(context.get("import_analysis"), dict) else {}
    import_rows = []
    for item in import_analysis.get("items") or []:
        if isinstance(item, dict):
            import_rows.append({"record_type": "item", **item})
    for item in import_analysis.get("scenarios") or []:
        if isinstance(item, dict):
            import_rows.append({"record_type": "scenario", **item})
    sources = quality.get("source_health") if isinstance(quality.get("source_health"), list) else []
    warnings = [{"Aviso": str(item)} for item in (quality.get("warnings") if isinstance(quality.get("warnings"), list) else [])]
    sheets = {
        "Resumo": summary_rows,
        "Margens_MLB": context.get("listing_margin_rows") or [],
        "Historico_Margens": context.get("historical_margin_ledger") or [],
        "Ações": actions,
        "Lojas": context.get("store_summaries") or [],
        "Vendas_SKU": context.get("sales_rows") or [],
        "Estoque": context.get("inventory_rows") or [],
        "Compras_Transito": context.get("purchase_pipeline_rows") or [],
        "Importacao": import_rows,
        "Fontes": sources,
        "Fontes_ML": (context.get("marketplace_commercial") or {}).get("sources") if isinstance(context.get("marketplace_commercial"), dict) else [],
        "Avisos": warnings,
    }
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, rows in sheets.items():
            frame = pd.DataFrame(rows or [{"Informacao": "Sem dados disponiveis"}])
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.book[sheet_name]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for column_cells in worksheet.columns:
                values = [str(cell.value or "") for cell in list(column_cells)[:200]]
                width = min(60, max(10, max((len(value) for value in values), default=10) + 2))
                worksheet.column_dimensions[column_cells[0].column_letter].width = width


def _assistant_write_xlsx(path: str, context: dict[str, Any], suggestions: list[dict[str, Any]]) -> None:
    if str(context.get("report_type") or "").strip():
        _assistant_write_advanced_xlsx(path, context)
        return
    import pandas as pd

    rows = _assistant_flatten_rows(context.get("tool_results") if isinstance(context.get("tool_results"), list) else [])
    sources = context.get("sources") if isinstance(context.get("sources"), list) else []
    analysis = context.get("management_analysis") if isinstance(context.get("management_analysis"), dict) else _assistant_management_analysis(context)
    kpis = analysis.get("kpis") if isinstance(analysis.get("kpis"), list) else []
    stockout_rows = _assistant_collect_stockout_rows(context, only_risky=True, limit=500)
    stale_rows = _assistant_collect_stale_stock_rows(context, limit=500)
    sales_rows = _assistant_collect_sales_rank_rows(context, limit=500)
    diagnostic_rows = _assistant_specialist_sku_diagnostics(context, limit=500)
    margin_rows = [
        {key: value for key, value in row.items() if not str(key).startswith("_")}
        for row in _assistant_collect_margin_rows(context, limit=500)
    ]
    findings = []
    for section in analysis.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for finding in section.get("findings") or []:
            if isinstance(finding, dict):
                findings.append(finding)
    actions = [{"prioridade": idx, "acao": action} for idx, action in enumerate(analysis.get("priority_actions") or [], 1)]
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(kpis or [{"info": "Sem indicadores"}]).to_excel(writer, sheet_name="indicadores", index=False)
        pd.DataFrame(diagnostic_rows or [{"info": "Sem diagnostico especialista por SKU"}]).to_excel(writer, sheet_name="diagnostico_skus", index=False)
        pd.DataFrame(stockout_rows or [{"info": "Sem SKUs com risco alto/critico de ruptura"}]).to_excel(writer, sheet_name="ruptura_skus", index=False)
        pd.DataFrame(stale_rows or [{"info": "Sem estoque parado com saldo de loja"}]).to_excel(writer, sheet_name="estoque_parado", index=False)
        pd.DataFrame(sales_rows or [{"info": "Sem ranking de SKUs vendido"}]).to_excel(writer, sheet_name="top_skus", index=False)
        pd.DataFrame(margin_rows or [{"info": "Sem margem calculavel por SKU vendido"}]).to_excel(writer, sheet_name="margens_skus", index=False)
        pd.DataFrame(findings or [{"info": "Sem diagnostico"}]).to_excel(writer, sheet_name="diagnostico", index=False)
        pd.DataFrame(actions or [{"info": "Sem acoes priorizadas"}]).to_excel(writer, sheet_name="acoes", index=False)
        pd.DataFrame(suggestions or []).to_excel(writer, sheet_name="sugestoes", index=False)
        pd.DataFrame(sources or []).to_excel(writer, sheet_name="fontes", index=False)
        pd.DataFrame(rows or [{"info": "Sem dados tabulares"}]).to_excel(writer, sheet_name="dados", index=False)


def _assistant_write_advanced_pdf(path: str, title: str, context: dict[str, Any]) -> None:
    from reportlab.lib import colors
    from reportlab.lib.fonts import addMapping
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular_font = "Helvetica"
    bold_font = "Helvetica-Bold"
    font_candidates = [
        (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for regular_path, bold_path in font_candidates:
        if not (os.path.isfile(regular_path) and os.path.isfile(bold_path)):
            continue
        try:
            if "JKReport" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("JKReport", regular_path))
                pdfmetrics.registerFont(TTFont("JKReport-Bold", bold_path))
                addMapping("JKReport", 0, 0, "JKReport")
                addMapping("JKReport", 1, 0, "JKReport-Bold")
            regular_font = "JKReport"
            bold_font = "JKReport-Bold"
            break
        except Exception:
            continue

    styles = getSampleStyleSheet()
    for style_name in ("Normal", "BodyText"):
        styles[style_name].fontName = regular_font
    for style_name in ("Title", "Heading1", "Heading2", "Heading3"):
        styles[style_name].fontName = bold_font
    body = styles["BodyText"]
    small = styles["BodyText"].clone("Small")
    small.fontSize = 7
    small.leading = 9
    story: list[Any] = [Paragraph(html.escape(title), styles["Title"]), Spacer(1, 4 * mm)]
    scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
    quality = context.get("data_quality") if isinstance(context.get("data_quality"), dict) else {}
    financial = context.get("financial_summary") if isinstance(context.get("financial_summary"), dict) else {}
    story.append(
        Paragraph(
            html.escape(
                f"Período {scope.get('period_start') or '-'} a {scope.get('period_end') or '-'}; "
                f"comparação {scope.get('comparison_start') or '-'} a {scope.get('comparison_end') or '-'}; "
                f"confiabilidade {quality.get('confidence') or 'baixa'} ({quality.get('score') or 0}/100)."
            ),
            body,
        )
    )
    story.append(Spacer(1, 3 * mm))
    coverage = context.get("financial_coverage") if isinstance(context.get("financial_coverage"), dict) else {}
    story.append(
        Paragraph(
            html.escape(
                f"Cobertura financeira {coverage.get('complete_margin_by_revenue_pct') if coverage.get('complete_margin_by_revenue_pct') is not None else 'indisponível'}%; "
                f"mínimo {coverage.get('minimum_required_pct') or 95}%; receita coberta "
                f"{_assistant_money(coverage.get('covered_revenue_brl')) if coverage.get('covered_revenue_brl') is not None else 'indisponível'}; receita não coberta "
                f"{_assistant_money(coverage.get('uncovered_revenue_brl')) if coverage.get('uncovered_revenue_brl') is not None else 'indisponível'}. "
                "Campos ausentes não são zero."
            ),
            body,
        )
    )
    story.append(Spacer(1, 2 * mm))
    financial_cell = body.clone("FinancialCell")
    financial_cell.fontSize = 8
    financial_cell.leading = 10
    financial_header = financial_cell.clone("FinancialHeader")
    financial_header.fontName = bold_font
    financial_rows = [[Paragraph("Indicador", financial_header), Paragraph("Valor", financial_header)]]
    labels = {
        "gross_revenue_brl": "Receita bruta",
        "returns_brl": "Devoluções",
        "net_revenue_brl": "Receita líquida",
        "advertising_brl": "Publicidade",
        "contribution_profit_brl": "Margem de contribuição",
        "net_profit_after_ads_brl": "Resultado de contribuição após publicidade",
        "net_profit_after_ads_and_returns_brl": "Resultado de contribuição após publicidade e devoluções reconciliadas",
    }
    for key, label in labels.items():
        value = financial.get(key)
        financial_rows.append(
            [
                Paragraph(html.escape(label), financial_cell),
                Paragraph(html.escape(_assistant_money(value) if value is not None else "Indisponível"), financial_cell),
            ]
        )
    table = Table(financial_rows, colWidths=[100 * mm, 45 * mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbeafe")), ("GRID", (0, 0), (-1, -1), .35, colors.grey), ("FONTNAME", (0, 0), (-1, -1), regular_font), ("FONTNAME", (0, 0), (-1, 0), bold_font), ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.extend([Paragraph("Resumo financeiro", styles["Heading2"]), table, Spacer(1, 3 * mm), Paragraph("Decisões prioritárias", styles["Heading2"])])
    for index, action in enumerate((context.get("top_actions") or [])[:5], 1):
        if not isinstance(action, dict):
            continue
        text_value = (
            f"<b>{index}. {html.escape(str(action.get('title') or 'Acao recomendada'))}</b><br/>"
            f"Loja {html.escape(str(action.get('store') or '-'))}; impacto {html.escape(str(action.get('impact_label') or 'nao estimavel'))}; "
            f"urgência {html.escape(str(action.get('urgency') or '-'))}; confiança {html.escape(str(action.get('confidence') or '-'))}.<br/>"
            f"{html.escape(str(action.get('recommendation') or '-'))}<br/>"
            f"Responsável {html.escape(str(action.get('owner_username') or action.get('owner_role') or '-'))}; prazo {html.escape(str(action.get('due_at') or '-'))}."
        )
        story.extend([Paragraph(text_value, body), Spacer(1, 2 * mm)])

    def add_table(title_value: str, rows: Any, columns: list[tuple[str, str]], limit: int = 20) -> None:
        all_values = [item for item in (rows if isinstance(rows, list) else []) if isinstance(item, dict)]
        values = all_values[:limit]
        if not values:
            return
        displayed_title = (
            f"{title_value} - exibindo {len(values)} de {len(all_values)}; base completa no XLSX"
            if len(all_values) > len(values)
            else title_value
        )
        story.append(Paragraph(html.escape(displayed_title), styles["Heading2"]))
        data: list[list[Any]] = [[Paragraph(html.escape(label), small) for _, label in columns]]
        for row in values:
            cells = []
            for key, _ in columns:
                value = row.get(key)
                if (key.endswith("_brl") or key == "revenue") and value is not None:
                    value = _assistant_money(value)
                cells.append(Paragraph(html.escape(str(value if value is not None else "Indisponível"))[:320], small))
            data.append(cells)
        widths = [landscape(A4)[0] / len(columns) - 8 * mm for _ in columns]
        output = Table(data, colWidths=widths, repeatRows=1)
        output.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbeafe")), ("GRID", (0, 0), (-1, -1), .25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
        story.extend([output, Spacer(1, 3 * mm)])

    add_table("Lojas", context.get("store_summaries"), [("store", "Loja"), ("net_revenue_brl", "Receita líquida"), ("trend_pct", "Variação"), ("target_attainment_pct", "Meta")])
    add_table(
        "Contribuição atual por anúncio e variação",
        context.get("listing_margin_rows"),
        [("store", "Loja"), ("mlb", "MLB"), ("variation_id", "Variação"), ("sku", "SKU"),
         ("current_price_brl", "Preço"), ("ml_fee_brl", "Tarifa"), ("seller_shipping_brl", "Frete"),
         ("unit_contribution_brl", "Contrib."), ("contribution_margin_pct", "Margem %"),
         ("margin_status_label", "Status"), ("missing_components_text", "Ausentes")],
        limit=40,
    )
    add_table(
        "Resultado histórico reconciliado",
        context.get("historical_margin_ledger"),
        [("store", "Loja"), ("order_id", "Pedido"), ("line_number", "Linha"), ("pack_id", "Pack"), ("mlb", "MLB"),
         ("variation_id", "Variação"), ("sku", "SKU"), ("gross_amount_brl", "Receita"),
         ("contribution_total_brl", "Contrib."), ("shipping_scope_label", "Frete"),
         ("margin_status_label", "Status"), ("missing_components_text", "Ausentes")],
        limit=40,
    )
    add_table("Riscos de estoque", context.get("inventory_rows"), [("store", "Loja"), ("sku", "SKU"), ("abc", "ABC"), ("xyz", "XYZ"), ("coverage_days", "Cobertura"), ("suggested_purchase", "Comprar")])
    import_analysis = context.get("import_analysis") if isinstance(context.get("import_analysis"), dict) else {}
    add_table("Custo posto por SKU", import_analysis.get("items"), [("sku", "SKU"), ("quantity", "Qtd"), ("landed_unit_brl", "Custo posto"), ("minimum_sale_price_brl", "Preco minimo")])
    add_table(
        "Saúde das fontes",
        quality.get("source_health"),
        [("source", "Fonte"), ("status", "Status"), ("records", "Registros"),
         ("last_sync_at", "Atualização"), ("coverage_pct", "Cobertura %")],
        limit=30,
    )
    marketplace_sources = context.get("marketplace_commercial") if isinstance(context.get("marketplace_commercial"), dict) else {}
    add_table(
        "Recursos Mercado Livre consultados",
        marketplace_sources.get("sources"),
        [("provider", "Provedor"), ("resource", "Recurso"), ("method", "Método"),
         ("store", "Loja"), ("day", "Data")],
        limit=30,
    )
    warnings = quality.get("warnings") if isinstance(quality.get("warnings"), list) else []
    if warnings:
        story.append(Paragraph("Avisos de dados", styles["Heading2"]))
        for warning in warnings[:12]:
            story.append(Paragraph("- " + html.escape(str(warning)[:420]), small))
    marketplace = context.get("marketplace_commercial") if isinstance(context.get("marketplace_commercial"), dict) else {}
    story.extend(
        [
            Paragraph("Fontes e metodologia", styles["Heading2"]),
            Paragraph(
                html.escape(
                    f"Snapshot Mercado Livre: status {marketplace.get('status') or 'indisponível'}; coletado em "
                    f"{marketplace.get('collected_at') or 'indisponível'}; cache vencido: {'sim' if marketplace.get('stale') else 'não'}. "
                    "Preço, custo, imposto, tarifa e frete do vendedor são obrigatórios. Preços atuais nunca são apresentados como históricos. "
                    "Custo e imposto do cadastro só entram no histórico quando capturados no dia da venda ou em D+1; backfill posterior fica indisponível. "
                    "Frete de packs com vários produtos permanece no pack e não é rateado."
                ),
                small,
            ),
        ]
    )
    doc = SimpleDocTemplate(path, pagesize=landscape(A4), rightMargin=12 * mm, leftMargin=12 * mm, topMargin=12 * mm, bottomMargin=14 * mm)

    def draw_footer(canvas_obj: Any, document: Any) -> None:
        canvas_obj.saveState()
        page_width, _page_height = landscape(A4)
        canvas_obj.setStrokeColor(colors.HexColor("#d5dde6"))
        canvas_obj.line(12 * mm, 9 * mm, page_width - 12 * mm, 9 * mm)
        canvas_obj.setFont(regular_font, 7)
        canvas_obj.setFillColor(colors.HexColor("#526170"))
        canvas_obj.drawString(12 * mm, 5.5 * mm, "JK Peças - relatório gerencial somente leitura")
        canvas_obj.drawRightString(page_width - 12 * mm, 5.5 * mm, f"Página {document.page}")
        canvas_obj.restoreState()

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)


def _assistant_write_pdf(path: str, title: str, suggestions: list[dict[str, Any]], sources: list[dict[str, Any]], analysis: Optional[dict[str, Any]] = None, context: Optional[dict[str, Any]] = None) -> None:
    if isinstance(context, dict) and str(context.get("report_type") or "").strip():
        _assistant_write_advanced_pdf(path, title, context)
        return
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    y = height - 48
    c.setFont("Helvetica-Bold", 15)
    c.drawString(40, y, title[:80])
    y -= 26
    c.setFont("Helvetica", 9)
    c.drawString(40, y, f"Gerado em {_assistant_now()}")
    y -= 28

    def line(text: str, bold: bool = False) -> None:
        nonlocal y
        if y < 60:
            c.showPage()
            y = height - 48
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 9 if not bold else 11)
        safe = str(text or "").encode("latin-1", "replace").decode("latin-1")
        c.drawString(40, y, safe[:110])
        y -= 15

    line("Principais achados", True)
    findings = []
    if isinstance(analysis, dict):
        for section in analysis.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for finding in section.get("findings") or []:
                if isinstance(finding, dict):
                    findings.append(finding)
    if findings:
        for item in findings[:16]:
            line(f"- {item.get('title')}: {item.get('recommendation')}")
    else:
        for item in suggestions[:16]:
            line(f"- {item.get('title')}: {item.get('detail')}")
    if isinstance(analysis, dict) and analysis.get("priority_actions"):
        y -= 8
        line("Prioridades de acao", True)
        for idx, action in enumerate((analysis.get("priority_actions") or [])[:8], 1):
            line(f"{idx}. {action}")
    diagnostic_rows = _assistant_specialist_sku_diagnostics(context or {}, limit=12)
    if diagnostic_rows:
        y -= 8
        line("Diagnostico especialista por SKU", True)
        for row in diagnostic_rows[:12]:
            line(
                f"- SKU {row.get('SKU')} | risco {row.get('Risco')} | prioridade {row.get('Prioridade')} | "
                f"responsavel {row.get('Responsavel')} | impacto {row.get('Impacto financeiro')}"
            )
    stockout_rows = _assistant_collect_stockout_rows(context or {}, only_risky=True, limit=12)
    if stockout_rows:
        y -= 8
        line("SKUs em risco de ruptura", True)
        for row in stockout_rows[:12]:
            line(
                f"- SKU {row.get('sku')} | risco {row.get('risco')} | saldo loja {row.get('saldo_considerado')} | "
                f"media/dia {row.get('media_dia')} | dias {row.get('dias_ate_ruptura')}"
            )
    stale_rows = _assistant_collect_stale_stock_rows(context or {}, limit=12)
    if stale_rows:
        y -= 8
        line("Estoque parado com saldo de loja", True)
        for row in stale_rows[:12]:
            line(
                f"- {row.get('prioridade')} | SKU {row.get('sku')} | saldo loja {row.get('saldo_loja')} | "
                f"{row.get('situacao')} | capital {row.get('capital_custo')} | acao {row.get('acao_recomendada')}"
            )
    margin_rows = _assistant_collect_margin_rows(context or {}, limit=12)
    if margin_rows:
        y -= 8
        line("Margem real por SKU vendido", True)
        for row in margin_rows[:12]:
            line(
                f"- SKU {row.get('SKU')} | qtd {row.get('Qtd vendida')} | valor {row.get('Valor vendido')} | "
                f"lucro {row.get('Lucro estimado') or '-'} | status {row.get('Status da margem')}"
            )
    y -= 8
    line("Fontes consultadas", True)
    for src in sources[:20]:
        line(f"- {_assistant_source_display(src)} ({src.get('records')} registros)")
    c.save()


def _assistant_create_report(client_id: str, title: str, context: dict[str, Any], prompt: str) -> dict[str, Any]:
    suggestions = context.get("suggestions") if isinstance(context.get("suggestions"), list) else []
    advanced_report = bool(str(context.get("report_type") or "").strip())
    if advanced_report:
        financial = context.get("financial_summary") if isinstance(context.get("financial_summary"), dict) else {}
        quality = context.get("data_quality") if isinstance(context.get("data_quality"), dict) else {}
        net_revenue = financial.get("net_revenue_brl")
        context["management_analysis"] = {
            "generated_at": (context.get("scope") or {}).get("generated_at") if isinstance(context.get("scope"), dict) else _assistant_now(),
            "executive_summary": [
                f"Confiabilidade {quality.get('confidence') or 'baixa'} ({quality.get('score') or 0}/100).",
                f"Receita liquida {_assistant_money(net_revenue) if net_revenue is not None else 'indisponivel'}.",
            ],
            "kpis": [],
            "sections": [],
            "priority_actions": [str(item.get("recommendation") or item.get("title") or "") for item in (context.get("top_actions") or [])[:5]],
            "data_quality": quality,
        }
    elif not isinstance(context.get("management_analysis"), dict):
        context["management_analysis"] = _assistant_management_analysis(context)
    analysis = context.get("management_analysis") if isinstance(context.get("management_analysis"), dict) else {}
    context["specialist_sku_diagnostics"] = [] if advanced_report else _assistant_specialist_sku_diagnostics(context, limit=500)
    report_id = f"codex_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    report_dir = _assistant_report_dir(client_id, report_id)
    html_path = os.path.join(report_dir, "report.html")
    xlsx_path = os.path.join(report_dir, "report.xlsx")
    pdf_path = os.path.join(report_dir, "report.pdf")
    metadata_path = os.path.join(report_dir, "metadata.json")

    html_content = _assistant_build_report_html(title, context, suggestions)
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)
    try:
        _assistant_write_xlsx(xlsx_path, context, suggestions)
    except Exception as exc:
        context.setdefault("warnings", []).append(f"Falha ao gerar XLSX: {exc}")
    try:
        _assistant_write_pdf(pdf_path, title, suggestions, context.get("sources") if isinstance(context.get("sources"), list) else [], analysis, context)
    except Exception as exc:
        context.setdefault("warnings", []).append(f"Falha ao gerar PDF: {exc}")

    chat_text = _assistant_report_chat_text(title, context, suggestions, report_id)
    metadata = {
        "report_id": report_id,
        "title": title,
        "prompt": prompt,
        "created_at": _assistant_now(),
        "formats": {
            "html": os.path.exists(html_path),
            "xlsx": os.path.exists(xlsx_path),
            "pdf": os.path.exists(pdf_path),
        },
        "downloads": {
            fmt: f"/api/admin/codex/assistant/reports/{report_id}/download?format={fmt}"
            for fmt in REPORT_FORMATS
            if os.path.exists(os.path.join(report_dir, f"report.{fmt}"))
        },
        "sources": context.get("sources") or [],
        "suggestions": suggestions,
        "management_analysis": analysis,
        "specialist_sku_diagnostics": context.get("specialist_sku_diagnostics") or [],
        "registry_results": (
            [
                {
                    "tool_id": item.get("tool_id"),
                    "tool_label": item.get("tool_label"),
                    "records": item.get("records"),
                    "sources_human": item.get("sources_human") or [],
                    "warnings": item.get("warnings") or [],
                }
                for item in (context.get("registry_results") or [])[:50]
                if isinstance(item, dict)
            ]
            if advanced_report
            else context.get("registry_results") or []
        ),
        "tool_plan": context.get("tool_plan") or {},
        "status_steps": context.get("status_steps") or [],
        "warnings": context.get("warnings") or [],
        "report_type": context.get("report_type") or "",
        "scope": context.get("scope") or {},
        "data_quality": context.get("data_quality") or {},
        "financial_coverage": context.get("financial_coverage") or {},
        "financial_summary": context.get("financial_summary") or {},
        "marketplace_commercial": context.get("marketplace_commercial") or {},
        "listing_margin_rows": context.get("listing_margin_rows") or [],
        "historical_margin_ledger": context.get("historical_margin_ledger") or [],
        "top_actions": (context.get("top_actions") or [])[:5],
        "store_summaries": context.get("store_summaries") or [],
        "sales_rows": context.get("sales_rows") or [],
        "inventory_rows": context.get("inventory_rows") or [],
        "purchase_pipeline_rows": context.get("purchase_pipeline_rows") or [],
        "import_analysis": context.get("import_analysis"),
        "supplier_performance": context.get("supplier_performance"),
        "chat_text": chat_text,
        "chat_download_formats": [fmt for fmt in ("pdf", "xlsx") if os.path.exists(os.path.join(report_dir, f"report.{fmt}"))],
    }
    saved = codex_assistant_storage.codex_assistant_report_save(_assistant_info_base(), client_id, metadata)
    return saved if isinstance(saved, dict) and saved else metadata


def _assistant_ensure_report_chat_text(metadata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    chat_text_atual = str(metadata.get("chat_text") or "")
    tem_cobertura_tecnica_antiga = any(
        trecho in chat_text_atual
        for trecho in (
            "## Cobertura das ferramentas",
            "Escopo e cobertura das consultas",
            "| Ferramenta | Modulo | Registros | Fonte |",
        )
    )
    if metadata.get("chat_text") and metadata.get("chat_download_formats") and not tem_cobertura_tecnica_antiga:
        return metadata
    context = {
        "generated_at": metadata.get("created_at"),
        "sources": metadata.get("sources") or [],
        "suggestions": metadata.get("suggestions") or [],
        "management_analysis": metadata.get("management_analysis") or {},
        "specialist_sku_diagnostics": metadata.get("specialist_sku_diagnostics") or [],
        "registry_results": metadata.get("registry_results") or [],
        "tool_plan": metadata.get("tool_plan") or {},
        "status_steps": metadata.get("status_steps") or [],
        "warnings": metadata.get("warnings") or [],
        "report_type": metadata.get("report_type") or "",
        "scope": metadata.get("scope") or {},
        "data_quality": metadata.get("data_quality") or {},
        "financial_coverage": metadata.get("financial_coverage") or {},
        "financial_summary": metadata.get("financial_summary") or {},
        "marketplace_commercial": metadata.get("marketplace_commercial") or {},
        "listing_margin_rows": metadata.get("listing_margin_rows") or [],
        "historical_margin_ledger": metadata.get("historical_margin_ledger") or [],
        "top_actions": metadata.get("top_actions") or [],
        "store_summaries": metadata.get("store_summaries") or [],
        "sales_rows": metadata.get("sales_rows") or [],
        "inventory_rows": metadata.get("inventory_rows") or [],
        "purchase_pipeline_rows": metadata.get("purchase_pipeline_rows") or [],
        "import_analysis": metadata.get("import_analysis"),
        "supplier_performance": metadata.get("supplier_performance"),
    }
    suggestions = metadata.get("suggestions") if isinstance(metadata.get("suggestions"), list) else []
    metadata["chat_text"] = _assistant_report_chat_text(
        str(metadata.get("title") or "Relatorio Codex Assistente"),
        context,
        suggestions,
        str(metadata.get("report_id") or ""),
    )
    downloads = metadata.get("downloads") if isinstance(metadata.get("downloads"), dict) else {}
    metadata["chat_download_formats"] = [fmt for fmt in ("pdf", "xlsx") if fmt in downloads]
    return metadata


def _assistant_compact_chat_text_response(payload: Any, preview_limit: int = 600) -> Any:
    """Project proactive/daily responses without copying their large datasets."""

    limit = max(1, min(int(preview_limit or 600), 600))
    source = payload if isinstance(payload, dict) else {}

    def scalar(value: Any, max_chars: int = 300) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:max_chars]

    def compact_status_steps(value: Any) -> list[Any]:
        output: list[Any] = []
        for item in (value if isinstance(value, list) else [])[:12]:
            if isinstance(item, dict):
                projected = {}
                for key in ("status", "label", "at", "tool_id", "records", "success"):
                    if key in item and isinstance(item.get(key), (str, bool, int, float, type(None))):
                        projected[key] = scalar(item.get(key), 240)
                if projected:
                    output.append(projected)
            else:
                output.append(scalar(item, 240))
        return output

    def compact_report(value: Any) -> Optional[dict[str, Any]]:
        if not isinstance(value, dict):
            return None
        chat_text = str(value.get("chat_text") or value.get("chat_text_preview") or "")
        original_length = value.get("chat_text_length")
        try:
            chat_text_length = max(len(chat_text), int(original_length or 0))
        except Exception:
            chat_text_length = len(chat_text)
        report: dict[str, Any] = {
            "report_id": scalar(value.get("report_id"), 160) or "",
            "title": scalar(value.get("title"), 240) or "",
            "generated_at": scalar(value.get("generated_at"), 80) or "",
            "created_at": scalar(value.get("created_at"), 80) or "",
            "kind": scalar(value.get("kind"), 80) or "",
            "status": scalar(value.get("status"), 80) or "",
            "status_steps": compact_status_steps(value.get("status_steps")),
            "warnings": [scalar(item, 300) for item in (value.get("warnings") if isinstance(value.get("warnings"), list) else [])[:12]],
            "formats": {
                str(key)[:24]: bool(enabled)
                for key, enabled in list((value.get("formats") if isinstance(value.get("formats"), dict) else {}).items())[:8]
            },
            "downloads": {
                str(key)[:24]: scalar(url, 600)
                for key, url in list((value.get("downloads") if isinstance(value.get("downloads"), dict) else {}).items())[:8]
                if isinstance(url, (str, int, float))
            },
            "chat_download_formats": [
                str(item)[:24]
                for item in (value.get("chat_download_formats") if isinstance(value.get("chat_download_formats"), list) else [])[:8]
            ],
            "chat_text_preview": chat_text[:limit],
            "chat_text_length": chat_text_length,
            "truncated": bool(value.get("truncated")) or chat_text_length > limit,
        }
        report_type = str(value.get("report_type") or "").strip()
        if report_type:
            report["report_type"] = report_type[:80]
        scope = value.get("scope") if isinstance(value.get("scope"), dict) else {}
        if scope:
            report["scope"] = {
                key: ([scalar(item, 120) for item in field[:20]] if isinstance(field, list) else scalar(field, 240))
                for key in (
                    "period_start", "period_end", "comparison_start", "comparison_end",
                    "selected_store", "stores", "stock_as_of", "generated_at",
                )
                if (field := scope.get(key)) is not None
            }
        quality = value.get("data_quality") if isinstance(value.get("data_quality"), dict) else {}
        if quality:
            report["data_quality"] = {
                "status": scalar(quality.get("status"), 40),
                "score": quality.get("score"),
                "confidence": scalar(quality.get("confidence"), 40),
                "source_health": [
                    {
                        key: scalar(item.get(key), 180)
                        for key in ("source", "status", "records", "last_sync_at", "coverage_pct")
                        if item.get(key) is not None
                    }
                    for item in (quality.get("source_health") if isinstance(quality.get("source_health"), list) else [])[:8]
                    if isinstance(item, dict)
                ],
                "warnings": [scalar(item, 260) for item in (quality.get("warnings") if isinstance(quality.get("warnings"), list) else [])[:6]],
            }
        for key in ("financial_coverage", "financial_summary"):
            source_dict = value.get(key) if isinstance(value.get(key), dict) else {}
            if source_dict:
                report[key] = {
                    str(item_key)[:80]: scalar(item_value, 160)
                    for item_key, item_value in list(source_dict.items())[:30]
                    if isinstance(item_value, (str, bool, int, float, type(None)))
                }
        actions = value.get("top_actions") if isinstance(value.get("top_actions"), list) else []
        if actions:
            report["top_actions"] = [
                {
                    key: (
                        [scalar(entry, 80) for entry in item.get(key, [])[:20]]
                        if key == "skus" and isinstance(item.get(key), list)
                        else scalar(item.get(key), 500)
                    )
                    for key in (
                        "action_id", "action_type", "title", "store", "skus", "impact_brl",
                        "impact_label", "urgency", "confidence", "owner_username", "owner_role",
                        "due_at", "evidence", "recommendation", "queueable",
                    )
                    if item.get(key) is not None
                }
                for item in actions[:5]
                if isinstance(item, dict)
            ]
        return report

    def compact_scheduler(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        scheduler: dict[str, Any] = {}
        for key in (
            "last_proactive_ts",
            "last_proactive_at",
            "last_proactive_tool_results_count",
            "last_daily_date",
            "last_daily_at",
            "last_daily_suggestions_count",
            "last_weekly_key",
            "last_weekly_at",
        ):
            item = value.get(key)
            if isinstance(item, (str, bool, int, float, type(None))):
                scheduler[key] = scalar(item, 120)
        nested_report = value.get("last_daily_report")
        if isinstance(nested_report, dict) and nested_report.get("report_id"):
            scheduler["last_daily_report_id"] = scalar(nested_report.get("report_id"), 160)
        weekly_report = value.get("last_weekly_report")
        if isinstance(weekly_report, dict) and weekly_report.get("report_id"):
            scheduler["last_weekly_report_id"] = scalar(weekly_report.get("report_id"), 160)
        sources = value.get("last_proactive_sources")
        if isinstance(sources, list):
            scheduler["last_proactive_sources_count"] = len(sources)
        return scheduler

    def compact_suggestion(value: Any) -> Optional[dict[str, Any]]:
        if not isinstance(value, dict):
            return None
        suggestion: dict[str, Any] = {}
        limits = {
            "id": 120,
            "kind": 80,
            "title": 160,
            "detail": 900,
            "severity": 40,
            "source": 160,
            "recommendation": 600,
            "report_prompt": 1200,
            "created_at": 80,
        }
        for key, max_chars in limits.items():
            if key in value and isinstance(value.get(key), (str, bool, int, float, type(None))):
                suggestion[key] = scalar(value.get(key), max_chars)
        return suggestion or None

    result: dict[str, Any] = {"compact": True}
    for key in ("success", "status", "due"):
        if key in source and isinstance(source.get(key), (str, bool, int, float, type(None))):
            result[key] = scalar(source.get(key), 120)
    report = compact_report(source.get("report"))
    if report is not None:
        result["report"] = report
    result["scheduler"] = compact_scheduler(source.get("scheduler"))
    suggestions = []
    for item in (source.get("suggestions") if isinstance(source.get("suggestions"), list) else [])[:12]:
        projected = compact_suggestion(item)
        if projected:
            suggestions.append(projected)
    if "suggestions" in source:
        result["suggestions"] = suggestions
    return result


def _assistant_save_suggestions(client_id: str, suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = _assistant_path(client_id, "suggestions.json")
    existing = _assistant_read_json(path, [])
    if not isinstance(existing, list):
        existing = []
    by_id = {str(item.get("id") or ""): item for item in existing if isinstance(item, dict)}
    for item in suggestions:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        by_id[str(item.get("id"))] = item
    merged = sorted(by_id.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)[:80]
    _assistant_write_json(path, merged)
    return merged


def _assistant_scheduler_state(client_id: str) -> dict[str, Any]:
    try:
        data = codex_assistant_storage.codex_assistant_scheduler_get(_assistant_info_base(), client_id)
    except Exception:
        data = _assistant_read_json(_assistant_path(client_id, "scheduler_state.json"), {})
    return data if isinstance(data, dict) else {}


def _assistant_save_scheduler_state(client_id: str, state: dict[str, Any]) -> None:
    codex_assistant_storage.codex_assistant_scheduler_save(_assistant_info_base(), client_id, state)


def codex_assistant_chat(
    payload: CodexAssistantChatRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    message = str(payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Informe uma mensagem.")
    client_id = str(sessao.get("client_id") or "").strip()
    if not client_id:
        raise HTTPException(status_code=401, detail="Sessao sem tenant autenticado.")

    # Compatibilidade de rota: o antigo assistente deterministico nao escolhe
    # mais fontes. Sistema e WhatsApp compartilham o mesmo seletor Codex e os
    # mesmos guards de tenant, loja, permissao e read-only do /api/ia/chat.
    from backend.schemas import IAChatRequest
    from backend.services import ia_endpoints

    delegated = ia_endpoints.ia_chat(
        IAChatRequest(
            message=message,
            page="codex_assistant",
            context={
                "selection": (
                    payload.screen_context.get("selection")
                    if isinstance(payload.screen_context, dict)
                    and isinstance(payload.screen_context.get("selection"), dict)
                    else {}
                )
            },
        ),
        request,
        client_id=client_id,
    )
    resposta = str(delegated.get("resposta") or "")
    tool_results = [
        item for item in list(delegated.get("tool_results") or []) if isinstance(item, dict)
    ]
    return {
        "success": delegated.get("success") is True,
        "resposta": resposta,
        "answer": resposta,
        "model": delegated.get("model") or "",
        "context": {
            "sources": list(dict.fromkeys(
                str(source or "")[:300]
                for item in tool_results
                for source in list(item.get("sources_human") or item.get("sources") or [])[:8]
                if str(source or "").strip()
            ))[:20],
            "warnings": list(dict.fromkeys(
                str(warning or "")[:300]
                for item in tool_results
                for warning in list(item.get("warnings") or [])[:8]
                if str(warning or "").strip()
            ))[:20],
            "suggestions": [],
            "management_analysis": {},
            "registry_results": [],
            "tool_plan": {"managed_by": "CodexDataSelectionAgent"},
            "status_steps": [],
            "tool_results_count": len(tool_results),
            "generated_at": _assistant_now(),
            "cache_hit": False,
        },
    }


def codex_assistant_suggestions(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    suggestions = _assistant_read_json(_assistant_path(client_id, "suggestions.json"), [])
    state = _assistant_scheduler_state(client_id)
    if not isinstance(suggestions, list):
        suggestions = []
    return {"success": True, "suggestions": suggestions[:30], "scheduler": state}


def codex_assistant_tools(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    tools = _assistant_tools_public(sessao.get("permissions") or {})
    modules = sorted({str(tool.get("module") or "") for tool in tools if tool.get("module")})
    return {
        "success": True,
        "tools": tools,
        "modules": modules,
        "total": len(tools),
        "version": CODEX_DATA_TOOLS_VERSION,
        "read_only": True,
        "mutating_actions_require_approval": True,
        "generated_at": _assistant_now(),
    }


def _assistant_evaluation_run(client_id: str, screen_context: Any = None) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    total = len(CODEX_ASSISTANT_EVALUATION_CASES)
    passed = 0
    for case in CODEX_ASSISTANT_EVALUATION_CASES:
        question = str(case.get("question") or "").strip()
        mode = str(case.get("mode") or "chat").strip() or "chat"
        plan = _assistant_registry_plan(client_id, question, screen_context or {}, mode)
        planned_tools = [str(item or "").strip() for item in plan.get("selected_tools") or [] if str(item or "").strip()]
        expected = [str(item or "").strip() for item in case.get("expected_tools") or [] if str(item or "").strip()]
        compatible = [str(item or "").strip() for item in case.get("compatible_tools") or [] if str(item or "").strip()]
        matched_expected = [tool for tool in expected if tool in planned_tools]
        matched_compatible = [tool for tool in compatible if tool in planned_tools]
        missing = [tool for tool in expected if tool not in planned_tools]
        mutating = [
            tool
            for tool in planned_tools
            if _assistant_tool_meta(tool).get("read_only") is False
        ]
        ok = bool(matched_expected) and not mutating
        if ok:
            passed += 1
        confidence = "alta" if ok and matched_compatible else ("media" if ok else "baixa")
        cases.append(
            {
                "id": case.get("id"),
                "question": question,
                "mode": mode,
                "expected_tools": expected,
                "compatible_tools": compatible,
                "planned_tools": planned_tools,
                "matched_expected": matched_expected,
                "matched_compatible": matched_compatible,
                "missing_tools": missing,
                "mutating_tools": mutating,
                "passed": ok,
                "confidence": confidence,
                "status_steps": plan.get("status_steps") or [],
                "notes": (
                    "Plano chamou ferramenta esperada e permaneceu read-only."
                    if ok
                    else "Plano nao chamou a ferramenta esperada; revisar selecao de intencao/capacidade."
                ),
            }
        )
    score = round((passed / total) * 100.0, 1) if total else 0.0
    return {
        "success": True,
        "client_id": client_id,
        "generated_at": _assistant_now(),
        "read_only": True,
        "tools_version": CODEX_DATA_TOOLS_VERSION,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "score_percent": score,
        "cases": cases,
    }


def codex_assistant_evaluation_get(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    path = _assistant_path(client_id, "evaluation_last.json")
    data = _assistant_read_json(path, {})
    if isinstance(data, dict) and data.get("cases"):
        return data
    result = _assistant_evaluation_run(client_id, {})
    _assistant_write_json(path, result)
    return result


def codex_assistant_evaluation_run(
    payload: CodexAssistantEvaluationRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    result = _assistant_evaluation_run(client_id, payload.screen_context or {})
    _assistant_write_json(_assistant_path(client_id, "evaluation_last.json"), result)
    return result


def codex_assistant_memory_get(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    category: str = "",
    q: str = "",
    limit: int = 100,
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_operational_memory

    client_id = str(sessao.get("client_id") or "default")
    if str(q or "").strip():
        return codex_operational_memory.query_memory(
            client_id=client_id,
            message=str(q or ""),
            category=str(category or ""),
            limit=limit,
        )
    return codex_operational_memory.list_memory(
        client_id=client_id,
        category=str(category or ""),
        limit=limit,
    )


def codex_assistant_memory_post(
    payload: CodexOperationalMemoryRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_operational_memory

    return codex_operational_memory.add_memory(
        client_id=str(sessao.get("client_id") or "default"),
        category=str(payload.category or "decisoes"),
        content=str(payload.content or ""),
        source=str(payload.source or "manual"),
        metadata=payload.metadata if isinstance(payload.metadata, dict) else {},
        importance=payload.importance,
        entry_id=str(payload.entry_id or ""),
    )


def codex_assistant_memory_delete(
    entry_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_operational_memory

    return codex_operational_memory.delete_memory(
        client_id=str(sessao.get("client_id") or "default"),
        entry_id=str(entry_id or ""),
    )


def codex_assistant_data_sources(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    q: str = "",
    module: str = "",
    type: str = "",
    limit: int = 300,
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_readonly_sources

    return codex_readonly_sources.discover_data_sources(
        client_id=str(sessao.get("client_id") or "default"),
        query=str(q or ""),
        module=str(module or ""),
        source_type=str(type or ""),
        limit=limit,
    )


def codex_assistant_bling_resources(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    _assistant_require_full_admin(request, authorization)
    from backend.services import codex_bling_tools

    payload = codex_bling_tools.bling_resources_public()
    payload["mutating_actions_require_approval"] = True
    return payload


def codex_assistant_mercado_livre_resources(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    _assistant_require_full_admin(request, authorization)
    from backend.services.mercado_livre_query_catalog import mercado_livre_query_catalog_public

    payload = mercado_livre_query_catalog_public()
    payload["execution_requires_exact_store"] = True
    payload["mutating_routes_blocked"] = True
    return payload


def codex_assistant_proactive_run(
    payload: CodexAssistantRunRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    with ASSISTANT_LOCK:
        state = _assistant_scheduler_state(client_id)
        last_ts = float(state.get("last_proactive_ts") or 0)
        due = bool(payload.force) or (time.time() - last_ts >= PROACTIVE_INTERVAL_SECONDS)
        if not due:
            suggestions = _assistant_read_json(_assistant_path(client_id, "suggestions.json"), [])
            response = {"success": True, "status": "skipped", "due": False, "suggestions": suggestions[:30], "scheduler": state}
            return _assistant_compact_chat_text_response(response) if payload.compact else response
        context = _assistant_collect_data(
            client_id,
            "verificacao proativa de 30 minutos",
            payload.screen_context,
            mode="proactive",
            force_refresh=bool(payload.force),
        )
        suggestions = _assistant_save_suggestions(client_id, context.get("suggestions") or [])
        state.update(
            {
                "last_proactive_ts": time.time(),
                "last_proactive_at": _assistant_now(),
                "last_proactive_sources": context.get("sources") or [],
                "last_proactive_tool_results_count": context.get("tool_results_count") or 0,
            }
        )
        _assistant_save_scheduler_state(client_id, state)
        response = {"success": True, "status": "completed", "due": True, "suggestions": suggestions[:30], "scheduler": state}
        return _assistant_compact_chat_text_response(response) if payload.compact else response


def _assistant_daily_due(state: dict[str, Any], force: bool = False) -> bool:
    del state, force
    return False


def _assistant_weekly_due(state: dict[str, Any], force: bool = False) -> bool:
    if force:
        return True
    now = datetime.now()
    if now.weekday() == 0 and now.hour < 8:
        return False
    week_key = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    return str(state.get("last_weekly_key") or "") != week_key


def _assistant_profile_requires_marketplace(profile: str, prompt: str = "") -> bool:
    if profile in {"weekly_sales_stock", "daily_exceptions"}:
        return True
    if profile != "custom":
        return False
    normalized = _assistant_texto_norm(prompt)
    return any(
        token in normalized
        for token in ("margem", "lucro", "financeir", "rentabil", "tarifa", "frete", "custo mercado livre")
    )


def _assistant_connected_ml_stores(client_id: str, selected_store: str = "") -> list[str]:
    from backend.services import ia_tools_marketplaces

    stores: list[str] = []
    for row in ia_tools_marketplaces.carregar_lojas(client_id) or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("nome") or "").strip()
        integrations = row.get("integracoes") if isinstance(row.get("integracoes"), dict) else {}
        cfg = integrations.get("mercadolivre") if isinstance(integrations.get("mercadolivre"), dict) else {}
        if name and str(cfg.get("access_token") or "").strip():
            stores.append(name)
    selected = str(selected_store or "").strip()
    if selected and _assistant_texto_norm(selected) not in {
        _assistant_texto_norm("todas"),
        _assistant_texto_norm("todas as lojas"),
        _assistant_texto_norm("__todas"),
    }:
        exact = [name for name in stores if _assistant_texto_norm(name) == _assistant_texto_norm(selected)]
        return exact
    return list(dict.fromkeys(stores))


def _assistant_ml_tool_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    return result if isinstance(result, dict) else {}


def _assistant_marketplace_fetchers(period_start: str, period_end: str) -> dict[str, Any]:
    from backend.services import ia_tools_marketplaces, mercadolivre_legacy_pricing

    order_cache: dict[tuple[str, str, str, int, int], dict[str, Any]] = {}

    def orders_fetcher(*, client_id: str, store: str, date_from: str, date_to: str, offset: int, limit: int) -> dict[str, Any]:
        cache_key = (store, date_from, date_to, int(offset), int(limit))
        if cache_key in order_cache:
            return copy.deepcopy(order_cache[cache_key])
        payload = ia_tools_marketplaces._ia_tool_get_mercado_livre_orders(
            client_id,
            "conciliacao financeira somente leitura do relatorio",
            loja=store,
            data_inicio=date_from,
            data_fim=date_to,
            offset=offset,
            limite=limit,
            modo_relatorio=True,
            max_paginas=2,
            force_refresh=True,
        )
        result = _assistant_ml_tool_result(payload)
        order_cache[cache_key] = copy.deepcopy(result)
        return result

    def listing_fetcher(*, client_id: str, store: str, relevant_skus: list[str]) -> dict[str, Any]:
        del relevant_skus
        collected: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        complete = True
        for status_value in ("active", "paused"):
            offset = 0
            for _page in range(1000):
                payload = ia_tools_marketplaces._ia_tool_get_mercado_livre_listing(
                    client_id,
                    f"listar anuncios {status_value}",
                    loja=store,
                    limite=100,
                    status=status_value,
                    offset=offset,
                    incluir_detalhes=False,
                    incluir_comercial=False,
                    force_refresh=True,
                )
                result = _assistant_ml_tool_result(payload)
                warnings.extend(str(item) for item in result.get("warnings") or [])
                if result.get("error"):
                    complete = False
                    break
                matches = [item for item in result.get("matches") or [] if isinstance(item, dict)]
                for item in matches:
                    if _assistant_float(item.get("available_quantity")) > 0:
                        collected[str(item.get("id") or "").upper()] = item
                paging = result.get("paging") if isinstance(result.get("paging"), dict) else {}
                next_offset = paging.get("next_offset")
                if not paging.get("has_more") or next_offset in (None, ""):
                    break
                offset = int(next_offset)
            else:
                complete = False
                warnings.append(f"Paginacao de anuncios {status_value} excedeu o limite de seguranca em {store}.")

        sold_ids: set[str] = set()
        cursor = date.fromisoformat(period_start)
        end = date.fromisoformat(period_end)
        while cursor <= end:
            offset = 0
            for _page in range(400):
                orders = orders_fetcher(
                    client_id=client_id,
                    store=store,
                    date_from=cursor.isoformat(),
                    date_to=cursor.isoformat(),
                    offset=offset,
                    limit=60,
                )
                for order in orders.get("orders") or []:
                    if not isinstance(order, dict):
                        continue
                    for item in order.get("items") or []:
                        if isinstance(item, dict):
                            item_id = str(item.get("item_id") or "").strip().upper()
                            if re.fullmatch(r"MLB\d+", item_id):
                                sold_ids.add(item_id)
                paging = orders.get("paging") if isinstance(orders.get("paging"), dict) else {}
                next_offset = paging.get("next_offset")
                if not paging.get("has_more") or next_offset in (None, ""):
                    break
                offset = int(next_offset)
            else:
                complete = False
                warnings.append(f"Paginacao de pedidos excedeu o limite de seguranca em {store}/{cursor.isoformat()}.")
            cursor += timedelta(days=1)

        for item_id in sorted(sold_ids - set(collected)):
            payload = ia_tools_marketplaces._ia_tool_get_mercado_livre_listing(
                client_id,
                f"consultar {item_id}",
                loja=store,
                item_id=item_id,
                limite=1,
                incluir_detalhes=False,
                incluir_comercial=False,
                force_refresh=True,
            )
            result = _assistant_ml_tool_result(payload)
            matches = [item for item in result.get("matches") or [] if isinstance(item, dict)]
            if matches:
                collected[item_id] = matches[0]
            else:
                complete = False
                warnings.append(f"Anuncio vendido {store}/{item_id} nao ficou disponivel para o snapshot atual.")
        return {
            "items": list(collected.values()),
            "coverage": {"complete": complete},
            "warnings": list(dict.fromkeys(warnings))[:100],
        }

    def commercial_fetcher(*, client_id: str, store: str, item_id: str, listing: dict[str, Any]) -> dict[str, Any]:
        payload = ia_tools_marketplaces._ia_tool_get_mercado_livre_listing(
            client_id,
            f"consultar dados comerciais {item_id}",
            loja=store,
            item_id=item_id,
            limite=1,
            incluir_detalhes=True,
            incluir_comercial=True,
            force_refresh=True,
        )
        result = _assistant_ml_tool_result(payload)
        matches = [item for item in result.get("matches") or [] if isinstance(item, dict)]
        exact = matches[0] if matches else {}
        details = exact.get("details") if isinstance(exact.get("details"), dict) else {}
        commercial = copy.deepcopy(details.get("commercial") if isinstance(details.get("commercial"), dict) else {})
        shipping = commercial.get("shipping") if isinstance(commercial.get("shipping"), dict) else {}
        try:
            cfg = ia_tools_marketplaces._obter_cfg_ml(client_id, store)
            shipping_info, _cfg = mercadolivre_legacy_pricing._ml_obter_frete_detalhado(
                client_id,
                store,
                cfg,
                item_id,
                shipping,
                contexto_frete={
                    "item_price": ((commercial.get("price") or {}).get("amount") if isinstance(commercial.get("price"), dict) else exact.get("price")),
                    "listing_type_id": exact.get("listing_type_id") or listing.get("listing_type_id"),
                    "category_id": exact.get("category_id") or listing.get("category_id"),
                    "mode": shipping.get("mode"),
                    "logistic_type": shipping.get("logistic_type"),
                    "free_shipping": shipping.get("free_shipping"),
                },
            )
            seller_cost = shipping_info.get("shipping_seller_cost")
            shipping["seller_cost"] = seller_cost
            shipping["seller_cost_available"] = seller_cost is not None
            shipping["source"] = str(shipping_info.get("shipping_cost_retry_source") or "shipping_options")
            if seller_cost is None:
                commercial["coverage_complete"] = False
                commercial.setdefault("warnings", []).append("Custo monetario do frete do vendedor indisponivel.")
            commercial["shipping"] = shipping
        except Exception as exc:
            shipping["seller_cost"] = None
            shipping["seller_cost_available"] = False
            commercial["shipping"] = shipping
            commercial["coverage_complete"] = False
            commercial.setdefault("warnings", []).append(f"Frete do vendedor indisponivel: {type(exc).__name__}.")
        raw_variations: list[dict[str, Any]] = []
        variation_commercial: dict[str, dict[str, Any]] = {}
        try:
            cfg_variations = ia_tools_marketplaces._obter_cfg_ml(client_id, store)
            raw_item, cfg_variations, ownership_failure = ia_tools_marketplaces._ia_ml_owned_item(
                client_id,
                store,
                cfg_variations,
                item_id,
                deadline=time.monotonic() + 45,
            )
            if ownership_failure:
                raise ValueError(str(ownership_failure.get("code") or "listing_not_in_store"))
            raw_item = raw_item if isinstance(raw_item, dict) else {}
            raw_variations = [item for item in raw_item.get("variations") or [] if isinstance(item, dict)]
            item_shipping = raw_item.get("shipping") if isinstance(raw_item.get("shipping"), dict) else {}
            listing_type_id = str(raw_item.get("listing_type_id") or exact.get("listing_type_id") or listing.get("listing_type_id") or "")
            category_id = str(raw_item.get("category_id") or exact.get("category_id") or listing.get("category_id") or "")
            site_id = str(cfg_variations.get("site_id") or "MLB").strip().upper()
            if not re.fullmatch(r"ML[A-Z]", site_id):
                site_id = "MLB"
            for variation in raw_variations:
                variation_id = str(variation.get("id") or "").strip()
                variation_price = variation.get("price")
                entry: dict[str, Any] = {
                    "price": {
                        "amount": variation_price,
                        "regular_amount": None,
                        "currency_id": str(raw_item.get("currency_id") or "BRL"),
                        "source": "item_variation",
                        "available": variation_price is not None,
                    },
                    "fees": {"sale_fee_amount": None, "source": "listing_prices", "available": False},
                    "shipping": {
                        "mode": str(item_shipping.get("mode") or shipping.get("mode") or ""),
                        "logistic_type": str(item_shipping.get("logistic_type") or shipping.get("logistic_type") or ""),
                        "free_shipping": bool(item_shipping.get("free_shipping")),
                        "seller_cost": None,
                        "seller_cost_available": False,
                    },
                    "coverage_complete": True,
                    "warnings": [],
                    "sources": [],
                }
                if variation_price is None or not listing_type_id or not category_id:
                    entry["coverage_complete"] = False
                    entry["warnings"].append("Variacao sem preco, categoria ou tipo de anuncio para tarifa oficial.")
                else:
                    fee_params: dict[str, Any] = {
                        "price": variation_price,
                        "listing_type_id": listing_type_id,
                        "category_id": category_id,
                    }
                    if entry["shipping"]["logistic_type"]:
                        fee_params["logistic_type"] = entry["shipping"]["logistic_type"]
                    if entry["shipping"]["mode"]:
                        fee_params["shipping_mode"] = entry["shipping"]["mode"]
                    fee_response, cfg_variations = ia_tools_marketplaces._ia_ml_request_get(
                        client_id,
                        store,
                        cfg_variations,
                        f"{ia_tools_marketplaces.ML_IA_API_BASE}/sites/{site_id}/listing_prices",
                        params=fee_params,
                        timeout=12,
                    )
                    if int(getattr(fee_response, "status_code", 0) or 0) == 200:
                        fee_payload = fee_response.json() or {}
                        if isinstance(fee_payload, list):
                            candidates = [value for value in fee_payload if isinstance(value, dict)]
                            fee_payload = next(
                                (value for value in candidates if str(value.get("listing_type_id") or "") == listing_type_id),
                                candidates[0] if candidates else {},
                            )
                        fee_payload = fee_payload if isinstance(fee_payload, dict) else {}
                        sale_details = fee_payload.get("sale_fee_details") if isinstance(fee_payload.get("sale_fee_details"), dict) else {}
                        entry["fees"] = {
                            "sale_fee_amount": fee_payload.get("sale_fee_amount"),
                            "fixed_fee_amount": sale_details.get("fixed_fee"),
                            "percentage_fee": sale_details.get("percentage_fee"),
                            "meli_percentage_fee": sale_details.get("meli_percentage_fee"),
                            "financing_add_on_fee": sale_details.get("financing_add_on_fee"),
                            "source": "listing_prices",
                            "available": fee_payload.get("sale_fee_amount") is not None,
                        }
                    if entry["fees"].get("sale_fee_amount") is None:
                        entry["coverage_complete"] = False
                        entry["warnings"].append("Tarifa oficial da variacao indisponivel.")
                    entry["sources"].append({"provider": "mercado_livre", "resource": "listing_prices", "method": "GET", "store": store})
                try:
                    variant_shipping, cfg_variations = mercadolivre_legacy_pricing._ml_obter_frete_detalhado(
                        client_id,
                        store,
                        cfg_variations,
                        item_id,
                        item_shipping,
                        contexto_frete={
                            "item_price": variation_price,
                            "listing_type_id": listing_type_id,
                            "category_id": category_id,
                            "mode": entry["shipping"]["mode"],
                            "logistic_type": entry["shipping"]["logistic_type"],
                            "free_shipping": entry["shipping"]["free_shipping"],
                        },
                    )
                    seller_cost = variant_shipping.get("shipping_seller_cost")
                    entry["shipping"]["seller_cost"] = seller_cost
                    entry["shipping"]["seller_cost_available"] = seller_cost is not None
                    entry["shipping"]["source"] = str(variant_shipping.get("shipping_cost_retry_source") or "shipping_options")
                    if seller_cost is None:
                        entry["coverage_complete"] = False
                        entry["warnings"].append("Frete do vendedor da variacao indisponivel.")
                    entry["sources"].append({"provider": "mercado_livre", "resource": "shipping_options", "method": "GET", "store": store})
                except Exception as exc:
                    entry["coverage_complete"] = False
                    entry["warnings"].append(f"Frete da variacao indisponivel: {type(exc).__name__}.")
                if variation_id:
                    variation_commercial[variation_id] = entry
            if variation_commercial:
                commercial["variations"] = variation_commercial
        except Exception as exc:
            if exact.get("variations"):
                commercial["coverage_complete"] = False
                commercial.setdefault("warnings", []).append(f"Detalhamento comercial de variacoes indisponivel: {type(exc).__name__}.")
        sources = [item for item in result.get("sources") or [] if isinstance(item, dict)]
        sources.append({"provider": "mercado_livre", "resource": "shipping_options", "method": "GET", "store": store})
        return {
            "details": {"commercial": commercial},
            "listing_variations": raw_variations or exact.get("variations") or [],
            "sources": sources,
        }

    def shipping_fetcher(*, client_id: str, store: str, shipment_id: str = "", pack_id: str = "", **_kwargs: Any) -> dict[str, Any]:
        target = str(shipment_id or pack_id or "").strip()
        if not target:
            raise ValueError("shipment_id ausente")
        cfg = ia_tools_marketplaces._obter_cfg_ml(client_id, store)
        response, _cfg = ia_tools_marketplaces._ia_ml_request_get(
            client_id,
            store,
            cfg,
            f"{ia_tools_marketplaces.ML_IA_API_BASE}/shipments/{target}/costs",
            timeout=15,
        )
        if int(getattr(response, "status_code", 0) or 0) != 200:
            raise HTTPException(status_code=int(getattr(response, "status_code", 500) or 500), detail="Frete real indisponivel")
        result = response.json() or {}
        if isinstance(result, dict):
            result["_seller_id"] = str(cfg.get("user_id") or "")
        return result

    def order_billing_fetcher(*, client_id: str, store: str, order_ids: list[str], **_kwargs: Any) -> dict[str, Any]:
        clean_ids = [re.sub(r"\D+", "", str(value or "")) for value in order_ids][:60]
        clean_ids = [value for value in clean_ids if value]
        if not clean_ids:
            return {"results": []}
        cfg = ia_tools_marketplaces._obter_cfg_ml(client_id, store)
        response, _cfg = ia_tools_marketplaces._ia_ml_request_get(
            client_id,
            store,
            cfg,
            f"{ia_tools_marketplaces.ML_IA_API_BASE}/billing/integration/group/ML/order/details",
            params={"order_ids": ",".join(clean_ids)},
            timeout=20,
        )
        if int(getattr(response, "status_code", 0) or 0) != 200:
            raise HTTPException(status_code=int(getattr(response, "status_code", 500) or 500), detail="Faturamento ML indisponivel")
        return response.json() or {}

    return {
        "listing_fetcher": listing_fetcher,
        "orders_fetcher": orders_fetcher,
        "shipping_fetcher": shipping_fetcher,
        "billing_fetcher": commercial_fetcher,
        "order_billing_fetcher": order_billing_fetcher,
    }


def _assistant_enrich_marketplace_ledger(
    client_id: str,
    advanced: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    from backend.services.codex_marketplace_margin import MarketplaceMarginLedger

    rows = [copy.deepcopy(item) for item in snapshot.get("ledger_rows") or [] if isinstance(item, dict)]
    if not rows:
        return snapshot
    cost_index: dict[tuple[str, str], dict[str, Any]] = {}
    for source_name in ("sales_rows", "inventory_rows"):
        for item in advanced.get(source_name) or []:
            if not isinstance(item, dict):
                continue
            key = (_assistant_texto_norm(item.get("store")), str(item.get("sku") or "").strip().upper())
            current = cost_index.setdefault(key, {})
            if current.get("unit_cost") is None and item.get("unit_cost") is not None:
                current["unit_cost"] = item.get("unit_cost")
            if current.get("tax_pct") is None and item.get("tax_pct") is not None:
                current["tax_pct"] = item.get("tax_pct")
    observed = snapshot.get("collected_at") or _assistant_now()
    observed_day_text = _assistant_calendar_date(str(observed)[:10])
    changed_by_store: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = (_assistant_texto_norm(row.get("store")), str(row.get("sku") or "").strip().upper())
        cost = cost_index.get(key) or {}
        if row.get("unit_cost") is None and cost.get("unit_cost") is not None:
            row["unit_cost"] = cost.get("unit_cost")
            row["cost_source"] = "cadastro_snapshot_reconciliacao"
            row["cost_observed_at"] = observed
        if row.get("tax_pct") is None and cost.get("tax_pct") is not None:
            row["tax_pct"] = cost.get("tax_pct")
            row["tax_source"] = "cadastro_snapshot_reconciliacao"
            row["tax_observed_at"] = observed
        order_day_text = _assistant_calendar_date(
            str(row.get("order_date") or row.get("date_created") or "")[:10]
        )
        near_sale_snapshot = False
        if order_day_text and observed_day_text:
            age_days = (date.fromisoformat(observed_day_text) - date.fromisoformat(order_day_text)).days
            near_sale_snapshot = 0 <= age_days <= 1
        if row.get("historical_cost_confirmed") is None:
            cost_scope = str(row.get("cost_scope") or row.get("cost_source") or "").strip().lower()
            row["historical_cost_confirmed"] = bool(
                near_sale_snapshot and row.get("unit_cost") is not None
            ) or cost_scope in {"order_historical", "ledger_historical"}
        if row.get("historical_tax_confirmed") is None:
            tax_scope = str(row.get("tax_scope") or row.get("tax_source") or "").strip().lower()
            row["historical_tax_confirmed"] = bool(
                near_sale_snapshot and row.get("tax_pct") is not None
            ) or tax_scope in {"order_historical", "ledger_historical"}
        both_historical_components_confirmed = (
            row.get("historical_cost_confirmed") is True
            and row.get("historical_tax_confirmed") is True
        )
        if not (both_historical_components_confirmed and row.get("historical_component_basis")):
            row["historical_component_basis"] = (
                "near_sale_reconciliation_snapshot"
                if near_sale_snapshot
                else "current_reconciliation_snapshot"
            )
        if row.get("pack_item_count") is None:
            row["pack_item_count"] = 2 if row.get("shipping_allocation_status") == "pack_multi_item_not_allocated" else 1
        required_components_known = all(
            row.get(field) is not None
            for field in ("unit_price", "unit_cost", "tax_pct", "shipping_seller_cost")
        )
        fee_known = row.get("sale_fee_amount") is not None or row.get("sale_fee_total") is not None
        row["reconciliation_state"] = "reconciled" if (
            required_components_known
            and fee_known
            and row.get("historical_cost_confirmed") is True
            and row.get("historical_tax_confirmed") is True
            and int(row.get("pack_item_count") or 1) <= 1
        ) else "partial"
        changed_by_store.setdefault(str(row.get("store") or ""), []).append(row)
    ledger = MarketplaceMarginLedger(_assistant_info_base(), client_id)
    for store_name, store_rows in changed_by_store.items():
        if store_name:
            ledger.upsert_orders(store_name, store_rows)
    scope = advanced.get("scope") if isinstance(advanced.get("scope"), dict) else {}
    snapshot["ledger_rows"] = ledger.list_rows(
        snapshot.get("stores") or [],
        scope.get("period_start"),
        scope.get("period_end"),
        None,
    )
    snapshot["order_rows"] = copy.deepcopy(snapshot["ledger_rows"])
    return snapshot


def _assistant_apply_advanced_profile(
    client_id: str,
    context: dict[str, Any],
    profile: str,
    *,
    store: str = "",
    import_list_id: str = "",
    force_refresh: bool = False,
    prompt: str = "",
) -> dict[str, Any]:
    from backend.services import codex_reports_advanced

    marketplace_required = _assistant_profile_requires_marketplace(profile, prompt)
    margin_rows = [] if marketplace_required else _assistant_collect_margin_rows(context, limit=500)
    advanced = codex_reports_advanced.build_profile_context(
        info_base=_assistant_info_base(),
        client_id=client_id,
        profile=profile,
        store=store,
        import_list_id=import_list_id,
        context=context,
        margin_rows=margin_rows,
    )
    if marketplace_required:
        stores = _assistant_connected_ml_stores(client_id, str(store or ""))
        scope = advanced.get("scope") if isinstance(advanced.get("scope"), dict) else {}
        relevant_skus = {
            str(item.get("sku") or "").strip().upper()
            for source_name in ("sales_rows", "inventory_rows")
            for item in (advanced.get(source_name) or [])
            if isinstance(item, dict) and str(item.get("sku") or "").strip()
        }
        if not stores:
            marketplace_snapshot = {
                "status": "unavailable",
                "collected_at": _assistant_now(),
                "stale": False,
                "stores": [],
                "listing_rows": [],
                "order_rows": [],
                "ledger_rows": [],
                "sources": [],
                "warnings": ["Nenhuma loja Mercado Livre conectada ficou disponivel para a coleta comercial."],
            }
        else:
            try:
                from backend.services.codex_marketplace_margin import collect_marketplace_commercial_snapshot

                fetchers = _assistant_marketplace_fetchers(
                    str(scope.get("period_start") or date.today().isoformat()),
                    str(scope.get("period_end") or date.today().isoformat()),
                )
                marketplace_snapshot = collect_marketplace_commercial_snapshot(
                    _assistant_info_base(),
                    client_id,
                    stores,
                    scope.get("period_start"),
                    scope.get("period_end"),
                    relevant_skus,
                    force_refresh=force_refresh,
                    listing_fetcher=fetchers["listing_fetcher"],
                    orders_fetcher=fetchers["orders_fetcher"],
                    shipping_fetcher=fetchers["shipping_fetcher"],
                    commercial_fetcher=fetchers["billing_fetcher"],
                    order_billing_fetcher=fetchers["order_billing_fetcher"],
                )
                marketplace_snapshot = _assistant_enrich_marketplace_ledger(client_id, advanced, marketplace_snapshot)
            except Exception as exc:
                marketplace_snapshot = {
                    "status": "unavailable",
                    "collected_at": _assistant_now(),
                    "stale": False,
                    "stores": stores,
                    "listing_rows": [],
                    "order_rows": [],
                    "ledger_rows": [],
                    "sources": [],
                    "warnings": [f"Coleta comercial Mercado Livre indisponivel: {type(exc).__name__}."],
                }
        advanced = codex_reports_advanced.apply_marketplace_commercial(advanced, marketplace_snapshot)
    context.update(advanced)
    scope = advanced.get("scope") if isinstance(advanced.get("scope"), dict) else {}
    tool_plan = context.get("tool_plan") if isinstance(context.get("tool_plan"), dict) else {}
    context["tool_plan"] = {
        **tool_plan,
        "data_inicio": scope.get("period_start") or tool_plan.get("data_inicio"),
        "data_fim": scope.get("period_end") or tool_plan.get("data_fim"),
        "comparison_start": scope.get("comparison_start"),
        "comparison_end": scope.get("comparison_end"),
        "loja": scope.get("selected_store") or "todas",
        "intent": profile,
    }
    quality_warnings = (advanced.get("data_quality") or {}).get("warnings") if isinstance(advanced.get("data_quality"), dict) else []
    if quality_warnings:
        context["warnings"] = list(dict.fromkeys(list(context.get("warnings") or []) + list(quality_warnings)))
    return context


def codex_assistant_daily_analysis_run(
    payload: CodexAssistantRunRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    with ASSISTANT_LOCK:
        state = _assistant_scheduler_state(client_id)
        response = {
            "success": True,
            "status": "disabled_weekly_only",
            "due": False,
            "suggestions": [],
            "report": None,
            "scheduler": state,
            "message": "Relatorios diarios desativados. O Black Jhon gera somente o relatorio semanal.",
        }
        return _assistant_compact_chat_text_response(response) if payload.compact else response


def codex_assistant_weekly_analysis_run(
    payload: CodexAssistantRunRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    with ASSISTANT_LOCK:
        state = _assistant_scheduler_state(client_id)
        if not _assistant_weekly_due(state, bool(payload.force)):
            report = state.get("last_weekly_report") if isinstance(state.get("last_weekly_report"), dict) else None
            response = {"success": True, "status": "skipped", "due": False, "scheduler": state, "report": report}
            return _assistant_compact_chat_text_response(response) if payload.compact else response
        context = _assistant_collect_data(
            client_id,
            "relatorio semanal completo de vendas, estoque, compras e oportunidades",
            payload.screen_context,
            mode="report",
            force_refresh=True,
        )
        context = _assistant_apply_advanced_profile(
            client_id,
            context,
            "weekly_sales_stock",
            force_refresh=True,
            prompt="relatorio semanal completo de vendas, estoque, compras, margem e oportunidades",
        )
        title = "Black Jhon - Semanal completo de vendas e estoque"
        report = _assistant_create_report(client_id, title, context, "weekly")
        try:
            from backend.services import codex_console

            codex_console.codex_register_report_history(
                client_id=client_id,
                username=str(sessao.get("username") or ""),
                prompt=title,
                report=report,
                thread_id="",
                screen_context=payload.screen_context if isinstance(payload.screen_context, dict) else {},
            )
        except Exception as exc:
            report.setdefault("warnings", []).append(f"Falha ao persistir relatorio semanal no historico: {exc}")
        now = datetime.now()
        week_key = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
        state.update(
            {
                "last_weekly_key": week_key,
                "last_weekly_at": _assistant_now(),
                "last_weekly_report": report,
            }
        )
        _assistant_save_scheduler_state(client_id, state)
        response = {"success": True, "status": "completed", "due": True, "report": report, "scheduler": state}
        return _assistant_compact_chat_text_response(response) if payload.compact else response


def codex_assistant_report_create(
    payload: CodexAssistantReportRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    prompt = str(payload.prompt or "").strip() or "relatorio operacional solicitado ao Codex"
    profile = str(payload.profile or "").strip()
    if profile and profile not in {"daily_exceptions", "weekly_sales_stock", "import_order", "custom"}:
        raise HTTPException(status_code=400, detail="Perfil de relatorio invalido.")
    if profile == "import_order" and not str(payload.import_list_id or "").strip():
        raise HTTPException(status_code=400, detail="Informe import_list_id para o relatorio de importacao.")
    context = _assistant_collect_data(
        client_id,
        prompt,
        payload.screen_context,
        mode="report",
        force_refresh=bool(payload.force_refresh),
    )
    if profile:
        context = _assistant_apply_advanced_profile(
            client_id,
            context,
            profile,
            store=str(payload.store or ""),
            import_list_id=str(payload.import_list_id or ""),
            force_refresh=bool(payload.force_refresh),
            prompt=prompt,
        )
    title = {
        "daily_exceptions": "Black Jhon - Diario executivo de vendas e estoque",
        "weekly_sales_stock": "Black Jhon - Semanal completo de vendas e estoque",
        "import_order": "Black Jhon - Analise de importacao por pedido",
        "custom": "Relatorio Black Jhon",
    }.get(profile, "Relatorio Codex Assistente")
    report = _assistant_create_report(client_id, title, context, prompt)
    thread_id = str(payload.thread_id or "").strip()
    conversation_id = str(payload.conversation_id or "").strip()
    if thread_id or conversation_id:
        report["thread_id"] = thread_id
        report["conversation_id"] = conversation_id
        report["screen_context"] = payload.screen_context if isinstance(payload.screen_context, dict) else {}
        try:
            saved_report = codex_assistant_storage.codex_assistant_report_save(_assistant_info_base(), client_id, report)
            if isinstance(saved_report, dict) and saved_report:
                report = saved_report
        except Exception as exc:
            report.setdefault("warnings", []).append(f"Falha ao atualizar metadata do relatorio: {exc}")
    try:
        from backend.services import codex_console

        history_task = codex_console.codex_register_report_history(
            client_id=client_id,
            username=str(sessao.get("username") or ""),
            prompt=prompt,
            report=report,
            thread_id=thread_id,
            conversation_id=conversation_id,
            screen_context=payload.screen_context if isinstance(payload.screen_context, dict) else {},
        )
        if history_task:
            report["history_task"] = history_task
    except Exception as exc:
        report.setdefault("warnings", []).append(f"Falha ao persistir relatorio no historico Codex: {exc}")
    return {"success": True, "report": report}


def codex_assistant_report_get(
    report_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    try:
        metadata = codex_assistant_storage.codex_assistant_report_get(_assistant_info_base(), client_id, report_id)
    except Exception:
        metadata = _assistant_read_json(os.path.join(_assistant_report_dir(client_id, report_id), "metadata.json"), None)
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=404, detail="Relatorio Codex nao encontrado.")
    metadata = _assistant_ensure_report_chat_text(metadata)
    try:
        codex_assistant_storage.codex_assistant_report_save(_assistant_info_base(), client_id, metadata)
    except Exception:
        pass
    return {"success": True, "report": metadata}


def codex_assistant_report_settings_get(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_reports_advanced

    settings = codex_reports_advanced.report_settings_get(
        _assistant_info_base(),
        str(sessao.get("client_id") or "default"),
    )
    return {"success": True, "settings": settings}


def codex_assistant_report_settings_put(
    payload: CodexAssistantReportSettingsRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_reports_advanced

    settings = codex_reports_advanced.report_settings_save(
        _assistant_info_base(),
        str(sessao.get("client_id") or "default"),
        payload.settings,
        str(sessao.get("username") or ""),
    )
    return {"success": True, "settings": settings}


def codex_assistant_financial_adjustments_get(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    kind: str = "advertising",
    store: str = "",
    period_start: str = "",
    period_end: str = "",
    limit: int = 500,
):
    sessao = _assistant_require_full_admin(request, authorization)
    items = codex_assistant_storage.codex_assistant_financial_adjustments_list(
        _assistant_info_base(),
        str(sessao.get("client_id") or "default"),
        kind=kind,
        store=store,
        period_start=period_start,
        period_end=period_end,
        limit=limit,
    )
    return {"success": True, "adjustments": items}


def codex_assistant_financial_adjustments_post(
    payload: CodexAssistantFinancialAdjustmentRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    if payload.amount < 0:
        raise HTTPException(status_code=400, detail="O valor do ajuste nao pode ser negativo.")
    try:
        start = date.fromisoformat(str(payload.period_start)[:10])
        end = date.fromisoformat(str(payload.period_end)[:10])
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Periodo do ajuste invalido.") from exc
    if start > end:
        raise HTTPException(status_code=400, detail="O inicio do ajuste deve ser anterior ao fim.")
    item = codex_assistant_storage.codex_assistant_financial_adjustment_save(
        _assistant_info_base(),
        str(sessao.get("client_id") or "default"),
        payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(),
        created_by=str(sessao.get("username") or ""),
    )
    return {"success": True, "adjustment": item}


def codex_assistant_financial_adjustments_delete(
    adjustment_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    removed = codex_assistant_storage.codex_assistant_financial_adjustment_delete(
        _assistant_info_base(),
        str(sessao.get("client_id") or "default"),
        adjustment_id,
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Ajuste financeiro nao encontrado.")
    return {"success": True, "deleted": True}


def codex_assistant_action_queue_get(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    status: str = "",
    action_type: str = "",
    owner_username: str = "",
    limit: int = 500,
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_reports_advanced

    actions = codex_reports_advanced.queue_actions_list(
        _assistant_info_base(),
        str(sessao.get("client_id") or "default"),
        status=status,
        action_type=action_type,
        owner_username=owner_username,
        limit=limit,
    )
    return {"success": True, "actions": actions}


def codex_assistant_action_queue_post(
    payload: CodexAssistantActionQueueRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_reports_advanced

    item = codex_reports_advanced.create_queue_action(
        info_base=_assistant_info_base(),
        client_id=str(sessao.get("client_id") or "default"),
        username=str(sessao.get("username") or ""),
        payload=payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(),
    )
    return {"success": True, "action": item}


def codex_assistant_action_queue_patch(
    action_id: str,
    payload: CodexAssistantActionQueueUpdateRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_reports_advanced

    raw_updates = payload.model_dump(exclude_none=True) if hasattr(payload, "model_dump") else payload.dict(exclude_none=True)
    try:
        item = codex_reports_advanced.update_queue_action(
            info_base=_assistant_info_base(),
            client_id=str(sessao.get("client_id") or "default"),
            action_id=action_id,
            username=str(sessao.get("username") or ""),
            updates=raw_updates,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Acao interna nao encontrada.") from exc
    return {"success": True, "action": item}


def codex_assistant_report_download(
    report_id: str,
    request: Request,
    format: str = "html",
    inline: bool = False,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    fmt = str(format or "html").strip().lower()
    if fmt not in REPORT_FORMATS:
        raise HTTPException(status_code=400, detail="Formato de relatorio invalido.")
    report_dir = _assistant_report_dir(client_id, report_id)
    path = os.path.join(report_dir, f"report.{fmt}")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Arquivo do relatorio nao encontrado.")
    media_types = {
        "html": "text/html; charset=utf-8",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pdf": "application/pdf",
    }
    filename = f"codex_relatorio_{_assistant_safe_id(report_id)}.{fmt}"
    return FileResponse(
        path,
        media_type=media_types[fmt],
        filename=filename,
        content_disposition_type="inline" if inline else "attachment",
    )


configure_codex_assistant_runtime()


__all__ = [
    "CodexAssistantChatRequest",
    "CodexAssistantRunRequest",
    "CodexAssistantReportRequest",
    "CodexAssistantReportSettingsRequest",
    "CodexAssistantFinancialAdjustmentRequest",
    "CodexAssistantActionQueueRequest",
    "CodexAssistantActionQueueUpdateRequest",
    "CodexAssistantEvaluationRequest",
    "CodexOperationalMemoryRequest",
    "configure_codex_assistant_runtime",
    "codex_assistant_collect_context",
    "codex_assistant_execute_tool_call",
    "codex_assistant_chat",
    "codex_assistant_suggestions",
    "codex_assistant_tools",
    "codex_assistant_evaluation_get",
    "codex_assistant_evaluation_run",
    "codex_assistant_memory_get",
    "codex_assistant_memory_post",
    "codex_assistant_memory_delete",
    "codex_assistant_data_sources",
    "codex_assistant_bling_resources",
    "codex_assistant_mercado_livre_resources",
    "codex_assistant_proactive_run",
    "codex_assistant_daily_analysis_run",
    "codex_assistant_weekly_analysis_run",
    "codex_assistant_report_create",
    "codex_assistant_report_get",
    "codex_assistant_report_settings_get",
    "codex_assistant_report_settings_put",
    "codex_assistant_financial_adjustments_get",
    "codex_assistant_financial_adjustments_post",
    "codex_assistant_financial_adjustments_delete",
    "codex_assistant_action_queue_get",
    "codex_assistant_action_queue_post",
    "codex_assistant_action_queue_patch",
    "codex_assistant_report_download",
]
