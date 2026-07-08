"""Read-only Codex assistant data tools, proactive checks, and reports."""

from __future__ import annotations

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

from fastapi import Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.services import codex_assistant_storage
from backend.services.favoritos_margem import (
    margem_calcular_anuncio,
    margem_formatar_moeda,
    margem_parse_float,
)
from backend.services.runtime_bridge import bind_runtime_globals


ASSISTANT_LOCK = threading.RLock()
PROACTIVE_INTERVAL_SECONDS = 30 * 60
EXTERNAL_CACHE_SECONDS = 15 * 60
DAILY_ANALYSIS_HOUR = int(os.getenv("JK_CODEX_DAILY_ANALYSIS_HOUR") or "8")
REPORT_FORMATS = {"html", "xlsx", "pdf"}
DEFAULT_RANKING_LIMIT = 50
BLING_REPORT_LIMIT = 200
CODEX_DATA_TOOLS_VERSION = "20260624-data-tools-v9-specialist-observability"
CODEX_DATA_CONTEXT_CHAR_LIMIT = int(os.getenv("JK_CODEX_DATA_CONTEXT_CHAR_LIMIT") or "600000")
CODEX_DATA_PREVIEW_CHAR_LIMIT = int(os.getenv("JK_CODEX_DATA_PREVIEW_CHAR_LIMIT") or "600000")
CODEX_SALES_RETURNS_CONTEXT_CHAR_LIMIT = int(os.getenv("JK_CODEX_SALES_RETURNS_CONTEXT_CHAR_LIMIT") or "560000")
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
        "description": "SKUs com saldo e sem venda recente.",
        "intent_examples": ["estoque parado", "sem vender", "dias sem venda"],
        "executor": "_ia_tool_get_days_without_sale_top",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["sku", "produto", "saldo_total", "dias_sem_vender"],
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
        "output_fields": ["id", "title", "status", "seller_sku", "price", "available_quantity", "sold_quantity", "health"],
        "fallbacks": ["integrations_status"],
        "status": "consultando Mercado Livre read-only",
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
        "description": "Consulta perguntas/pos-venda, aprovacoes, memoria por SKU e eventos locais.",
        "intent_examples": ["perguntas abertas", "pos venda pendente", "perguntas respondidas", "aprovacoes ML"],
        "executor": "codex_readonly_sources.questions_post_sale_query",
        "external": False,
        "cache_ttl_seconds": EXTERNAL_CACHE_SECONDS,
        "output_fields": ["records", "sources", "warnings"],
        "fallbacks": ["mercado_livre_readonly", "local_cache_query"],
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
        "description": "Consulta preferencias, lojas, SKUs, decisoes, relatorios e contexto operacional salvo do Joao Pretinho.",
        "intent_examples": ["o que voce lembra", "preferencias da loja", "skus frequentes", "relatorios anteriores"],
        "executor": "codex_operational_memory.query_memory",
        "external": False,
        "cache_ttl_seconds": 0,
        "output_fields": ["category", "content", "metadata", "source", "importance"],
        "fallbacks": [],
        "status": "consultando memoria operacional",
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


class CodexAssistantChatRequest(BaseModel):
    message: str
    screen_context: Optional[dict[str, Any]] = None
    history: Optional[list[dict[str, Any]]] = None
    force_refresh: bool = False


class CodexAssistantRunRequest(BaseModel):
    screen_context: Optional[dict[str, Any]] = None
    force: bool = False


class CodexAssistantReportRequest(BaseModel):
    prompt: str = ""
    screen_context: Optional[dict[str, Any]] = None
    thread_id: Optional[str] = None
    conversation_id: Optional[str] = None
    format: Optional[str] = "html"
    force_refresh: bool = False


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


def _assistant_tools_public() -> list[dict[str, Any]]:
    public = []
    for tool in CODEX_DATA_TOOLS:
        public.append(
            {
                "id": tool.get("id"),
                "module": tool.get("module"),
                "description": tool.get("description"),
                "intent_examples": tool.get("intent_examples") or [],
                "input_schema": _assistant_tool_input_schema(str(tool.get("id") or "")),
                "executor": tool.get("executor"),
                "external": bool(tool.get("external")),
                "cache_ttl_seconds": int(tool.get("cache_ttl_seconds") or EXTERNAL_CACHE_SECONDS),
                "output_fields": tool.get("output_fields") or [],
                "fallbacks": tool.get("fallbacks") or [],
                "read_only": True,
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
        "mercado_livre_listing": {"mensagem": "SKU, MLB, produto ou pedido de listagem", "loja": "nome da loja/conta ou vazio", "limite": 20},
        "bling_product": {"mensagem": "SKU, id Bling ou produto", "loja": "nome da loja/conta ou vazio"},
        "bling_status": {"loja": "nome da loja/conta ou vazio"},
        "bling_products": {"mensagem": "SKU, id, GTIN, EAN ou nome do produto", "loja": "nome da loja/conta ou vazio", "limite": 50},
        "bling_fiscal_product": {"mensagem": "SKU, id, GTIN, EAN ou nome do produto", "loja": "nome da loja/conta ou vazio"},
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
            "mensagem": "pergunta, comprador, SKU, status ou aprovacao",
            "source_id": "fonte de perguntas opcional",
            "limite": 50,
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
    "questions_post_sale_query": "perguntas e pos-venda do Mercado Livre",
    "fiscal_local_query": "cadastro fiscal local",
    "bling_status": "status da integracao Bling",
    "bling_products": "produtos cadastrados na Bling",
    "bling_product": "produtos cadastrados na Bling",
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
    "capability_resolve": "catalogo de capacidades do Joao Pretinho",
    "operational_memory_query": "memoria operacional do Joao Pretinho",
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


def _assistant_normalize_sku(value: Any) -> str:
    sku = str(value or "").strip().upper()
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
    blocked = {"PERIODO", "LOJA", "LOJAS", "CONTA", "CONTAS", "TODAS", "VENDA", "VENDAS", "DEVOLUCAO", "DEVOLUCOES"}
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        sku = _assistant_normalize_sku(match.group(1))
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
                            "comprador": str(row["comprador"] or ""),
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
                                "comprador": str(row["comprador"] or ""),
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
    return bool(
        re.search(r"\bsku\s+[a-z0-9._/-]{2,}\b", text)
        or re.search(r"\bmlb\d{6,}\b", text)
        or re.search(r"\bproduto\s+.{3,}", text)
        or re.search(r"[a-z0-9]{2,}[-_/][a-z0-9]{1,}", text)
    )


def _assistant_select_tool_ids(message: str, mode: str, screen_context: Any) -> list[str]:
    text = _assistant_texto_norm(message)
    page = _assistant_texto_norm(_assistant_context_page(screen_context))
    selected: list[str] = []

    def add(*tool_ids: str) -> None:
        for tool_id in tool_ids:
            if tool_id not in selected:
                selected.append(tool_id)

    wants_report = mode in {"daily", "report"} or bool(re.search(r"\b(relatorio|analise|analisar|diagnostico|oportunidade|melhoria|melhorias)\b", text))
    wants_sales = wants_report or bool(re.search(r"\b(venda|vendas|vendido|vendidos|faturamento|pedido|pedidos|ranking|rank|top|mais vendido|mais vendeu)\b", text))
    wants_ranking = bool(re.search(r"\b(ranking|rank|top|mais vendido|mais vendidos|mais vendeu|lider|campeao)\b", text))
    wants_stock = wants_report or bool(re.search(r"\b(estoque|ruptura|saldo|parado|sem vender|giro|reposicao)\b", text))
    wants_returns = wants_report or bool(re.search(r"\b(devolucao|devolucoes|devolvido|devolvidos|return)\b", text))
    wants_margin = wants_report or bool(re.search(r"\b(margem|lucro|rentabilidade|custo|imposto|preco)\b", text))
    wants_external = bool(re.search(r"\b(mercado livre|mercadolivre|bling|anuncio|anuncios|integracao|integracoes|token|conta|loja)\b", text))
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
    mentions_ml = bool(re.search(r"\b(mercado livre|mercadolivre|mlb\d+|anuncio|anuncios)\b", text))
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
        if mentions_ml:
            add("mercado_livre_readonly", "mercado_livre_listing")
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
    if not selected:
        add("operational_dispatcher", "integrations_status")
    return selected[:28]


def _assistant_registry_plan(client_id: str, message: str, screen_context: Any, mode: str) -> dict[str, Any]:
    data_inicio, data_fim = _assistant_resolve_period(client_id, message, screen_context)
    prev_inicio, prev_fim = _assistant_previous_period(data_inicio, data_fim)
    loja = _assistant_resolve_loja(client_id, message, screen_context)
    sku = _assistant_extract_sku_filter(message, screen_context)
    separar_por_loja = _assistant_wants_store_breakdown(message, loja)
    selected = _assistant_select_tool_ids(message, mode, screen_context)
    steps = ["interpretando pedido"]
    for tool_id in selected:
        status = str(_assistant_tool_meta(tool_id).get("status") or "").strip()
        if status and status not in steps:
            steps.append(status)
    return {
        "client_id": str(client_id or "default"),
        "intent": "sales_ranking" if "sales_ranking" in selected else ("report" if mode in {"daily", "report"} else "data_query"),
        "mode": mode,
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
        "status_steps": steps,
    }


def _assistant_tool_rows(result: Any) -> list[Any]:
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return []
    for key in (
        "rows",
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
    if tool_id == "sales_ranking":
        return (
            "Nenhuma venda por SKU foi encontrada para "
            f"{plan.get('loja') or 'todas as lojas'} entre {plan.get('data_inicio')} e {plan.get('data_fim')}."
        )
    if tool_id == "mercado_livre_listing":
        return "Nenhum anuncio Mercado Livre foi retornado para a consulta read-only."
    if tool_id == "bling_product":
        return "Nenhum produto Bling foi encontrado ou nao havia SKU/produto suficiente para consultar."
    if str(tool_id or "").startswith("bling_"):
        return "Consulta Bling read-only nao retornou registros para os filtros informados."
    if tool_id == "program_action_match":
        return "Nenhuma acao operacional aprovada foi reconhecida para este pedido."
    if tool_id == "program_functions_catalog":
        return "Catalogo do programa nao retornou funcoes para o filtro informado."
    return "Consulta read-only nao retornou registros."


def _assistant_standard_result(tool_id: str, raw: Optional[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
    meta = _assistant_tool_meta(tool_id)
    raw = raw if isinstance(raw, dict) else {}
    result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
    rows = _assistant_tool_rows(result)
    records = _assistant_result_count(result)
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
    source_raw = raw.get("function") or meta.get("executor") or tool_id
    tool_label = _assistant_human_tool_label(tool_id)
    source_label = _assistant_human_source_label(source_raw, tool_id)
    return {
        "tool_id": tool_id,
        "tool_label": tool_label,
        "module": meta.get("module"),
        "description": meta.get("description"),
        "executor": meta.get("executor"),
        "external": bool(meta.get("external")),
        "read_only": True,
        "records": records,
        "rows": rows if tool_id == "sales_returns_query" and isinstance(rows, list) else (rows[:500] if isinstance(rows, list) else []),
        "summary": {key: value for key, value in result.items() if not isinstance(value, list)} if isinstance(result, dict) else {},
        "source": source_raw,
        "source_label": source_label,
        "sources_human": _assistant_human_source_list([source_raw], tool_id),
        "arguments": raw.get("arguments") or {},
        "periodo": plan.get("periodo") or {},
        "loja": plan.get("loja") or "",
        "empty_reason": _assistant_tool_empty_reason(tool_id, records, result, plan),
        "next_fallbacks": meta.get("fallbacks") or [],
        "next_fallbacks_human": _assistant_human_fallback_list(meta.get("fallbacks") or []),
        "generated_at": _assistant_now(),
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
    default_limit = BLING_REPORT_LIMIT if mode in {"daily", "report"} else DEFAULT_RANKING_LIMIT
    try:
        limit_safe = int(plan.get("limite") or plan.get("limit") or default_limit)
    except Exception:
        limit_safe = default_limit
    limit_safe = max(1, min(limit_safe, 500 if mode in {"daily", "report"} else 200))

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
            raw = _assistant_call_ia_tool("_ia_tool_get_days_without_sale_top", client_id, loja, limit_safe, True, False)
        elif tool_id == "integrations_status":
            raw = _assistant_call_ia_tool("_ia_tool_get_integrations_status", client_id, loja)
        elif tool_id == "mercado_livre_listing":
            ml_message = message if re.search(r"\b(mercado livre|mercadolivre|mlb\d+|sku|anuncio|anuncios|listar)\b", _assistant_texto_norm(message)) else "listar anuncios ativos mercado livre"
            raw = _assistant_call_ia_tool("_ia_tool_get_mercado_livre_listing", client_id, ml_message, loja, None, 20)
        elif tool_id == "bling_product":
            bling_message = _assistant_bling_message_with_refs(message, plan, screen_context, existing_registry_results)
            raw = _assistant_call_ia_tool("_ia_tool_get_bling_product", client_id, bling_message, loja, None)
        elif str(tool_id or "").startswith("bling_"):
            from backend.services import codex_bling_tools

            bling_message = _assistant_bling_message_with_refs(message, plan, screen_context, existing_registry_results)
            raw = codex_bling_tools.execute_bling_tool(
                client_id=client_id,
                tool_id=tool_id,
                message=bling_message,
                loja=loja,
                data_inicio=data_inicio,
                data_fim=data_fim,
                limit=limit_safe,
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
        elif tool_id in {
            "source_discovery",
            "local_database_query",
            "local_csv_query",
            "local_cache_query",
            "sync_logs_query",
            "mercado_livre_readonly",
            "questions_post_sale_query",
            "fiscal_local_query",
        }:
            from backend.services import codex_readonly_sources

            result = codex_readonly_sources.execute_readonly_source_tool(
                client_id=client_id,
                tool_id=tool_id,
                message=message,
                loja=loja or "",
                data_inicio=data_inicio,
                data_fim=data_fim,
                limit=limit_safe,
                args={
                    "source_id": str(plan.get("source_id") or ""),
                    "module": str(plan.get("module_filter") or ""),
                    "type": str(plan.get("source_type") or ""),
                    "sql": str(plan.get("sql") or ""),
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
    confidence = "alta" if records > 0 and not empty_reasons else ("media" if records > 0 else "baixa")
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
        "motivo": (
            "Dados suficientes para responder com as fontes retornadas."
            if enough
            else "Dados insuficientes; tentar fallback compativel antes de concluir."
        ),
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
) -> dict[str, Any]:
    meta = _assistant_tool_meta(tool_id)
    rows: list[Any] = []
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
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        summaries.append(
            {
                "tool_id": item.get("tool_id"),
                "tool_label": item.get("tool_label") or _assistant_human_tool_label(item.get("tool_id")),
                "module": item.get("module"),
                "records": int(item.get("records") or 0),
                "source": item.get("source") or "",
                "source_label": item.get("source_label") or _assistant_human_source_label(item.get("source") or "", item.get("tool_id")),
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
            if fallback_id and fallback_id not in next_fallbacks:
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

    return {
        "success": True,
        "client_id": str(client_id or ""),
        "tool_id": tool_id,
        "tool_label": _assistant_human_tool_label(tool_id),
        "module": meta.get("module") or "",
        "description": meta.get("description") or "",
        "external": bool(meta.get("external")),
        "read_only": True,
        "args": _assistant_agent_compact(args),
        "records": records,
        "top_rows": _assistant_agent_compact(rows[:CODEX_AGENT_TOP_ROWS_LIMIT]),
        "summary": summaries[:12],
        "sources": sources_human[:20],
        "sources_raw": sources[:20],
        "sources_human": sources_human[:20],
        "source_label": (sources_human[:1] or [_assistant_human_tool_label(tool_id)])[0],
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
    if meta.get("read_only") is False:
        return {
            "success": False,
            "tool_id": tool_id,
            "error": "Ferramenta mutavel bloqueada. Acoes mutaveis exigem aprovacao explicita.",
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

    data_inicio = str(args.get("data_inicio") or args.get("inicio") or args.get("start_date") or "").strip()
    data_fim = str(args.get("data_fim") or args.get("fim") or args.get("end_date") or "").strip()
    if not (re.match(r"^\d{4}-\d{2}-\d{2}$", data_inicio) and re.match(r"^\d{4}-\d{2}-\d{2}$", data_fim)):
        data_inicio, data_fim = _assistant_resolve_period(client_id, message, screen_context)
    prev_inicio, prev_fim = _assistant_previous_period(data_inicio, data_fim)
    loja = str(args.get("loja") or args.get("conta") or args.get("store") or "").strip()
    if not loja:
        loja = _assistant_resolve_loja(client_id, message, screen_context)
    sku = _assistant_normalize_sku(args.get("sku") or args.get("codigo") or args.get("seller_sku") or "")
    if not sku:
        sku = _assistant_extract_sku_filter(message, screen_context)
    if tool_id in {"program_functions_catalog", "capability_resolve"}:
        default_limit = 1000
    else:
        default_limit = CODEX_AGENT_REPORT_ROW_LIMIT if mode in {"report", "daily"} else CODEX_AGENT_NORMAL_ROW_LIMIT
    limit_safe = _assistant_agent_int(args.get("limite") or args.get("limit"), default_limit, 1, 500)

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
        "limite": limit_safe,
        "module_filter": str(args.get("modulo") or args.get("module") or args.get("module_filter") or "").strip(),
        "category_filter": str(args.get("categoria") or args.get("category") or args.get("category_filter") or "").strip(),
        "capability_id": str(args.get("capability_id") or args.get("capacidade_id") or "").strip(),
        "source_id": str(args.get("source_id") or args.get("fonte") or "").strip(),
        "source_type": str(args.get("tipo") or args.get("type") or "").strip(),
        "sql": str(args.get("sql") or "").strip(),
        "include_routes": bool(args.get("incluir_rotas", args.get("include_routes", True))),
        "include_services": bool(args.get("incluir_servicos", args.get("include_services", True))),
        "include_actions": bool(args.get("incluir_acoes", args.get("include_actions", True))),
        "include_pages": bool(args.get("incluir_telas", args.get("include_pages", True))),
        "action_id": str(args.get("action_id") or "").strip(),
        "action_params": args.get("params") if isinstance(args.get("params"), dict) else {},
        "history": args.get("history") if isinstance(args.get("history"), list) else [],
        "separar_por_loja": bool(args.get("separar_por_loja")) or _assistant_wants_store_breakdown(message, loja),
        "incluir_registros": bool(args.get("incluir_registros", True)),
        "selected_tools": [tool_id],
    }

    registry_seed = list(previous_results or []) if isinstance(previous_results, list) else []
    raw_results: list[dict[str, Any]] = []
    registry_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    executed_tool_ids: set[str] = set()

    prereq_ids: list[str] = []
    if tool_id == "bling_stock_balances":
        prereq_ids = ["product_data", "product_registry", "stock_data", "bling_product"]
    elif tool_id in {"bling_fiscal_product", "bling_lots", "bling_lot_movements"}:
        prereq_ids = ["product_data", "product_registry", "bling_product"]

    for prereq_id in prereq_ids:
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
    if not has_records and tool_id != "operational_memory_query":
        fallback_limit = 5 if mode in {"report", "daily"} else 3
        fallback_count = 0
        for fallback_id in _assistant_agent_fallback_ids(tool_id, message):
            if fallback_count >= fallback_limit:
                break
            if fallback_id in executed_tool_ids:
                continue
            fallback_meta = _assistant_tool_meta(fallback_id)
            if not fallback_meta.get("id") or fallback_meta.get("read_only") is False:
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

    return _assistant_agent_result_package(client_id, tool_id, args, raw_results, registry_results, warnings)


def _assistant_execute_registry(
    client_id: str,
    message: str,
    screen_context: Any,
    mode: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[str]]:
    plan = _assistant_registry_plan(client_id, message, screen_context, mode)
    raw_results: list[dict[str, Any]] = []
    registry_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    executed: set[str] = set()

    def run(tool_id: str) -> None:
        if tool_id in executed:
            return
        if tool_id == "bling_stock_balances":
            for prereq_id in ("product_data", "product_registry", "stock_data"):
                run(prereq_id)
            refs = _assistant_product_refs_from_context(screen_context, registry_results)
            if not refs["skus"] and not refs["bling_ids"]:
                warnings.append(
                    "Saldo Bling: nao encontrei SKU/id Bling no texto, na tela ou no cadastro local antes da consulta externa."
                )
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
                lines.append("  - " + json.dumps(row, ensure_ascii=False, default=str)[:900])
        if summary:
            lines.append("  resumo: " + json.dumps(summary, ensure_ascii=False, default=str)[:1200])
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
    parado_limite = 100 if mode in {"daily", "report"} else 30

    candidates = [
        _assistant_call_ia_tool("_ia_tool_get_integrations_status", client_id, None),
        _assistant_call_ia_tool("_ia_tool_get_sales_by_period", client_id, inicio_30, fim, None, 10),
        _assistant_call_ia_tool("_ia_tool_get_returns_by_period", client_id, inicio_30, fim, None, 10),
        _assistant_call_ia_tool("_ia_tool_get_stockout_forecast", client_id, "previsao ruptura estoque", None, None, 30, estoque_limite),
        _assistant_call_ia_tool("_ia_tool_get_days_without_sale_top", client_id, None, parado_limite, True, False),
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
    for key in (
        "rows",
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
        row = {
            "sku": sku,
            "produto": produto,
            "saldo_loja": _assistant_qty(saldo_loja),
            "dias_sem_vender": _assistant_qty(dias, 0) if str(dias or "").strip() else "-",
            "ultima_venda": ultima_venda,
            "motivo": "SKU possui saldo de loja e nao teve venda recente no periodo analisado.",
            "acao_recomendada": "Revisar preco, titulo, foto, anuncio, kit/combo e estrategia de liquidacao antes de comprar mais unidades.",
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


def _assistant_collect_ml_listing_refs(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}

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
        refs.setdefault(sku, item)

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
        custo, imposto_rate = _assistant_resolve_margin_cost_tax(custos_por_sku, impostos_por_sku, sku)
        ml_ref = dict(ml_refs.get(sku) or {})
        anuncio_ref = {**ml_ref, "price": preco_unitario or ml_ref.get("price") or ml_ref.get("preco")}
        margem = margem_calcular_anuncio(anuncio_ref, sku_hint=sku, custo=custo, imposto_rate=imposto_rate)
        custo_total = round(float(custo) * float(qtd_num), 2) if custo is not None and qtd_num else None
        lucro_unitario = margem_parse_float(margem.get("valor_liquido")) if margem.get("margem_completa") else None
        lucro_total = round(float(lucro_unitario) * float(qtd_num), 2) if lucro_unitario is not None and qtd_num else None
        rows.append(
            {
                "SKU": sku,
                "Produto": str(row.get("produto") or "").strip(),
                "Qtd vendida": _assistant_qty(qtd_num),
                "Valor vendido": _assistant_money(valor_num),
                "Custo unitario": margem_formatar_moeda(custo) if custo is not None else "",
                "Custo total": margem_formatar_moeda(custo_total) if custo_total is not None else "",
                "Imposto": f"{_assistant_float(margem.get('imposto_percentual')):.2f}%".replace(".", ",") if margem.get("imposto_percentual") is not None else "",
                "Frete": margem.get("frete_ml_text") or "",
                "Tarifa": margem.get("tarifa_ml_text") or "",
                "Lucro estimado": margem_formatar_moeda(lucro_total) if lucro_total is not None else "",
                "Margem %": margem.get("margem_text") or "",
                "Status da margem": _assistant_margin_status_label(margem),
                "_valor_num": valor_num,
                "_qtd_num": qtd_num,
                "_custo_total_num": float(custo_total) if custo_total is not None else None,
                "_lucro_num": float(lucro_total) if lucro_total is not None else None,
                "_margem_completa": bool(margem.get("margem_completa")),
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
        margem = _assistant_float(profit.get("margem_percentual_estimada"))
        lucro = _assistant_float(profit.get("lucro_estimado"))
        skus_considerados = int(_assistant_float(profit.get("skus_considerados")))
        skus_com_custo = int(_assistant_float(profit.get("skus_com_custo")))
        cobertura = (skus_com_custo / skus_considerados * 100.0) if skus_considerados else 0.0
        severity = "critical" if margem < 5 else "warning" if margem < 15 else "ok"
        kpis.append({"label": "Lucro estimado", "value": _assistant_money(lucro), "detail": f"Margem {_assistant_percent(margem)}", "severity": severity})
        _assistant_add_finding(
            sections,
            "Margem",
            "Margem estimada do periodo",
            f"Lucro estimado: {_assistant_money(lucro)}; margem: {_assistant_percent(margem)}; cobertura de custo: {_assistant_percent(cobertura)} dos SKUs considerados.",
            "Priorizar revisao de preco/custo dos SKUs de maior faturamento e completar cadastro de custo dos SKUs sem custo para melhorar a confiabilidade da margem.",
            severity,
            "Margem baixa ou custo incompleto faz o relatorio de vendas superestimar o resultado real.",
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
                "Sem custo cadastrado, o Joao Pretinho nao deve inventar lucro nem margem.",
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
            first = parados[0]
            kpis.append({"label": "Estoque parado", "value": str(len(parados)), "detail": "SKU(s) com saldo sem venda recente", "severity": "warning"})
            _assistant_add_finding(
                sections,
                "Estoque",
                "Capital parado em produtos sem giro",
                f"{len(parados)} SKU(s) com saldo de loja e sem venda recente. Primeiro: {_assistant_sku_label(first)}; dias sem vender: {first.get('dias_sem_vender')}; saldo loja: {first.get('saldo_loja', first.get('saldo_total'))}.",
                "Criar fila de acao: ajustar preco, revisar titulo/foto, montar kit, liquidar, transferir canal ou pausar compra ate recuperar giro.",
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


def _assistant_suggestions_from_results(results: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    today = _assistant_today()

    def add(kind: str, title: str, detail: str, severity: str = "info", source: str = "", recommendation: str = "") -> None:
        raw = f"{today}|{kind}|{title}|{detail}|{source}"
        sid = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        source_label = _assistant_human_source_label(source, source)
        suggestions.append(
            {
                "id": sid,
                "kind": kind,
                "title": title[:120],
                "detail": detail[:420],
                "severity": severity,
                "source": source_label,
                "source_raw": source,
                "recommendation": recommendation[:420],
                "created_at": _assistant_now(),
            }
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
            itens = result.get("itens") if isinstance(result.get("itens"), list) else []
            parados = [it for it in itens if _assistant_float(it.get("saldo_loja", it.get("saldo_total"))) > 0 and (it.get("dias_sem_vender") is None or int(it.get("dias_sem_vender") or 0) >= 30)]
            if parados:
                primeiro = parados[0]
                add(
                    "stale_stock",
                    "Estoque parado com saldo",
                    f"{len(parados)} SKU(s) com saldo de loja e sem venda recente. Primeiro: {primeiro.get('sku') or '-'} - {primeiro.get('produto') or ''}.",
                    "info",
                    name,
                )
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
    if mode in {"proactive", "daily", "report"} or broad_chat:
        all_results.extend(_assistant_direct_tools(client_id, mode if mode in {"proactive", "daily", "report"} else "report"))

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
        tool_context_parts.append(json.dumps(all_results[:12], ensure_ascii=False, default=str)[:CODEX_DATA_PREVIEW_CHAR_LIMIT])

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
        "tool_results_preview": json.dumps(all_results[:20], ensure_ascii=False, default=str)[:CODEX_DATA_PREVIEW_CHAR_LIMIT],
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


def _assistant_report_chat_text(title: str, context: dict[str, Any], suggestions: list[dict[str, Any]], report_id: str = "") -> str:
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
                "A analise considera saldo de loja e tempo sem venda recente. O estoque Full nao entra como saldo disponivel para esta acao.",
                "| SKU | Produto | Saldo loja | Dias sem vender | Ultima venda | Motivo | Acao |",
                "|---|---|---:|---:|---|---|---|",
            ]
        )
        for row in stale_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        md(row.get("sku"), 80),
                        md(row.get("produto"), 180),
                        md(row.get("saldo_loja"), 50),
                        md(row.get("dias_sem_vender"), 50),
                        md(row.get("ultima_venda"), 90),
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
            rows.append({"fonte": name, "tipo": "resumo", "dados": json.dumps(result, ensure_ascii=False, default=str)[:1000]})
            continue
        for key, values in collections:
            limit = len(values) if name == "sales_returns_query" and key in {"vendas", "devolucoes", "por_sku", "por_loja"} else min(len(values), 500)
            for value in values[:limit]:
                if isinstance(value, dict):
                    row = {"fonte": name, "tipo": key}
                    for k, v in value.items():
                        if isinstance(v, (dict, list)):
                            row[str(k)] = json.dumps(v, ensure_ascii=False, default=str)[:600]
                        else:
                            row[str(k)] = v
                    rows.append(row)
    return rows[:5000]


def _assistant_build_report_html(title: str, context: dict[str, Any], suggestions: list[dict[str, Any]]) -> str:
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
            "<p class=\"muted\">A tabela considera saldo de loja e dias sem venda recente. O estoque Full nao entra como saldo disponivel para esta acao.</p>"
            "<table><thead><tr><th>SKU</th><th>Produto</th><th>Saldo loja</th><th>Dias sem vender</th><th>Ultima venda</th><th>Motivo</th><th>Acao recomendada</th></tr></thead><tbody>"
        )
        for row in stale_rows:
            parts.append(
                "<tr>"
                f"<td>{esc(row.get('sku'))}</td>"
                f"<td>{esc(row.get('produto'))}</td>"
                f"<td class=\"num\">{esc(row.get('saldo_loja'))}</td>"
                f"<td class=\"num\">{esc(row.get('dias_sem_vender'))}</td>"
                f"<td>{esc(row.get('ultima_venda'))}</td>"
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


def _assistant_write_xlsx(path: str, context: dict[str, Any], suggestions: list[dict[str, Any]]) -> None:
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


def _assistant_write_pdf(path: str, title: str, suggestions: list[dict[str, Any]], sources: list[dict[str, Any]], analysis: Optional[dict[str, Any]] = None, context: Optional[dict[str, Any]] = None) -> None:
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
                f"- SKU {row.get('sku')} | saldo loja {row.get('saldo_loja')} | "
                f"dias sem vender {row.get('dias_sem_vender')}"
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
    if not isinstance(context.get("management_analysis"), dict):
        context["management_analysis"] = _assistant_management_analysis(context)
    analysis = context.get("management_analysis") if isinstance(context.get("management_analysis"), dict) else {}
    context["specialist_sku_diagnostics"] = _assistant_specialist_sku_diagnostics(context, limit=500)
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
        "registry_results": context.get("registry_results") or [],
        "tool_plan": context.get("tool_plan") or {},
        "status_steps": context.get("status_steps") or [],
        "warnings": context.get("warnings") or [],
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
    context = _assistant_collect_data(
        str(sessao.get("client_id") or "default"),
        message,
        payload.screen_context,
        mode="chat",
        force_refresh=bool(payload.force_refresh),
    )
    resposta = _assistant_answer_from_context(message, context)
    return {
        "success": True,
        "resposta": resposta,
        "answer": resposta,
        "context": {
            "sources": context.get("sources") or [],
            "warnings": context.get("warnings") or [],
            "suggestions": context.get("suggestions") or [],
            "management_analysis": context.get("management_analysis") or {},
            "registry_results": context.get("registry_results") or [],
            "tool_plan": context.get("tool_plan") or {},
            "status_steps": context.get("status_steps") or [],
            "tool_results_count": context.get("tool_results_count") or 0,
            "generated_at": context.get("generated_at"),
            "cache_hit": context.get("cache_hit"),
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
    _assistant_require_full_admin(request, authorization)
    tools = _assistant_tools_public()
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
            return {"success": True, "status": "skipped", "due": False, "suggestions": suggestions[:30], "scheduler": state}
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
        return {"success": True, "status": "completed", "due": True, "suggestions": suggestions[:30], "scheduler": state}


def _assistant_daily_due(state: dict[str, Any], force: bool = False) -> bool:
    if force:
        return True
    now = datetime.now()
    today = now.date().isoformat()
    if str(state.get("last_daily_date") or "") == today:
        return False
    return now.hour >= DAILY_ANALYSIS_HOUR


def codex_assistant_daily_analysis_run(
    payload: CodexAssistantRunRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    with ASSISTANT_LOCK:
        state = _assistant_scheduler_state(client_id)
        if not _assistant_daily_due(state, bool(payload.force)):
            report = state.get("last_daily_report") if isinstance(state.get("last_daily_report"), dict) else None
            if report:
                report = _assistant_ensure_report_chat_text(report)
            return {"success": True, "status": "skipped", "due": False, "scheduler": state, "report": report}
        context = _assistant_collect_data(
            client_id,
            "analise diaria completa de vendas, estoque, devolucoes, margem, ruptura e oportunidades",
            payload.screen_context,
            mode="daily",
            force_refresh=True,
        )
        suggestions = _assistant_save_suggestions(client_id, context.get("suggestions") or [])
        title = "Analise diaria Codex - vendas e estoque"
        report = _assistant_create_report(client_id, title, context, "daily")
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
            report.setdefault("warnings", []).append(f"Falha ao persistir relatorio no historico Codex: {exc}")
        state.update(
            {
                "last_daily_date": _assistant_today(),
                "last_daily_at": _assistant_now(),
                "last_daily_report": report,
                "last_daily_suggestions_count": len(context.get("suggestions") or []),
            }
        )
        _assistant_save_scheduler_state(client_id, state)
        return {
            "success": True,
            "status": "completed",
            "due": True,
            "suggestions": suggestions[:30],
            "report": report,
            "scheduler": state,
        }


def codex_assistant_report_create(
    payload: CodexAssistantReportRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    prompt = str(payload.prompt or "").strip() or "relatorio operacional solicitado ao Codex"
    context = _assistant_collect_data(
        client_id,
        prompt,
        payload.screen_context,
        mode="report",
        force_refresh=bool(payload.force_refresh),
    )
    report = _assistant_create_report(client_id, "Relatorio Codex Assistente", context, prompt)
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
    "codex_assistant_proactive_run",
    "codex_assistant_daily_analysis_run",
    "codex_assistant_report_create",
    "codex_assistant_report_get",
    "codex_assistant_report_download",
]
