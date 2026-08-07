"""Internal slice for ia_tools."""

from __future__ import annotations

from __future__ import annotations
import asyncio
import base64
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlencode, urlparse
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import dotenv_values
from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from jose import JWTError
from backend.schemas import (
    IAAgentQueryRequest,
    IAChatRequest,
    IASalvarConversaRequest,
    IARagIndexRequest,
    IARagReindexRequest,
)
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
from backend.services.marketplace_tools import integrations as marketplace_integrations
from backend.services.marketplace_tools import listings as marketplace_listings
from backend.services.sales_tools import api as sales_tools
from backend.services.ia_common import *
from backend.services.ia_context import get_tenant_id, get_tenant_path
from backend.services.ia_state import *


def configure_ia_tools_dispatcher_runtime(runtime_module=None, peers=None):
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


configure_ia_tools_dispatcher_runtime()


def _ia_chat_executar_funcoes(payload: IAChatRequest, client_id: str) -> list[dict]:
    mensagem = str(payload.message or "").strip()
    contexto = payload.context if isinstance(payload.context, dict) else {}
    resultados = []
    produto_tool = None
    ref_produto = _ia_extrair_referencia_produto_mensagem(mensagem)
    texto_norm = _normalizar_texto(mensagem)
    pede_top_vendas = any(chave in texto_norm for chave in ("MAIS VENDEU", "MAIS VENDIDO", "TOP", "LIDER"))
    pede_vendas_loja_virtual = _ia_chat_pede_vendas_por_loja_virtual(mensagem)
    pede_analise_especialista = _ia_chat_pede_analise_especialista_vendas(mensagem, payload.page, contexto)
    data_inicio, data_fim = sales_tools.extract_sales_period(mensagem, contexto)
    loja = sales_tools.resolve_store(client_id, mensagem, contexto)
    pede_recorte_mensal = _ia_chat_pede_recorte_mensal(mensagem)
    mes_ano = sales_tools.extract_month_year(mensagem, contexto) if pede_recorte_mensal else None
    limite_top_mensal = _ia_chat_extrair_limite_top(mensagem, padrao=5, maximo=50)
    sku_mensal = ""
    meses_comparacao_mensal = sales_tools.extract_months_year(mensagem, contexto) if pede_recorte_mensal else []

    if pede_recorte_mensal and (_ia_chat_pede_consulta_vendas(mensagem) or _ia_chat_pede_consulta_devolucoes(mensagem) or pede_analise_especialista):
        if not data_inicio or not data_fim:
            data_inicio, data_fim = sales_tools.default_month_period(client_id, loja, meses=12)

        if _ia_chat_pede_consulta_vendas(mensagem):
            sku_mensal = sales_tools.resolve_sku(client_id, mensagem, produto_tool)
            if sku_mensal and mes_ano:
                venda_sku_mes = sales_tools.get_sku_sales_by_month(client_id, sku_mensal, mes_ano, loja)
                if venda_sku_mes:
                    resultados.append(venda_sku_mes)
            if sku_mensal and len(meses_comparacao_mensal) >= 2 and _ia_chat_pede_comparativo_periodo(mensagem):
                comparativo_sku_meses = sales_tools.compare_sku_sales_months(
                    client_id,
                    sku_mensal,
                    meses_comparacao_mensal,
                    loja,
                )
                if comparativo_sku_meses:
                    resultados.append(comparativo_sku_meses)

        if mes_ano:
            detalhes_mes = sales_tools.get_month_sales_returns_details(
                client_id,
                mes_ano,
                loja,
                limite_skus=200,
            )
            if detalhes_mes:
                resultados.append(detalhes_mes)

        if _ia_chat_pede_consulta_vendas(mensagem) or pede_analise_especialista:
            if mes_ano:
                top_vendas_mes = sales_tools.get_top_skus_sales_by_month(
                    client_id,
                    mes_ano,
                    loja,
                    limite=limite_top_mensal,
                )
                if top_vendas_mes:
                    resultados.append(top_vendas_mes)
            else:
                vendas_mensais = sales_tools.get_sales_by_month_period(
                    client_id,
                    data_inicio,
                    data_fim,
                    loja,
                    limite_meses=24,
                )
                if vendas_mensais:
                    resultados.append(vendas_mensais)

        if _ia_chat_pede_consulta_devolucoes(mensagem) or pede_analise_especialista:
            if mes_ano:
                top_devolucoes_mes = sales_tools.get_top_skus_returns_by_month(
                    client_id,
                    mes_ano,
                    loja,
                    limite=limite_top_mensal,
                )
                if top_devolucoes_mes:
                    resultados.append(top_devolucoes_mes)

    if (ref_produto.get("sku") or ref_produto.get("termo")) and (_ia_chat_pede_consulta_produto(mensagem) or (_ia_chat_pede_consulta_vendas(mensagem) and not pede_top_vendas)):
        produto_tool = _ia_tool_get_product_data(client_id, mensagem)
        if produto_tool:
            resultados.append(produto_tool)

    if _ia_chat_pede_status_integracoes(mensagem):
        status_integracoes = marketplace_integrations.get_status(client_id, loja)
        if status_integracoes:
            resultados.append(status_integracoes)

    if _ia_chat_pede_consulta_mercado_livre(mensagem):
        ml_tool = marketplace_listings.query(client_id, mensagem, loja, produto_tool)
        if ml_tool:
            resultados.append(ml_tool)

    if _ia_chat_pede_consulta_bling(mensagem):
        bling_tool = _ia_tool_get_bling_product(client_id, mensagem, loja, produto_tool)
        if bling_tool:
            resultados.append(bling_tool)

    if _ia_chat_pede_imagem_produto(mensagem):
        imagem_produto = _ia_tool_get_product_image(client_id, mensagem, produto_tool)
        if imagem_produto:
            resultados.append(imagem_produto)

    if _ia_chat_pede_consulta_estoque(mensagem):
        estoque_tool = sales_tools.get_stock_data(client_id, mensagem, produto_tool)
        if estoque_tool:
            resultados.append(estoque_tool)

    if _ia_chat_pede_info_cadastro_produto(mensagem):
        cadastro_tool = _ia_tool_get_product_registry_info(client_id, mensagem, produto_tool)
        if cadastro_tool:
            resultados.append(cadastro_tool)

    if _ia_chat_pede_top_dias_sem_venda(mensagem):
        opcoes_top_sem_venda = _ia_chat_extrair_opcoes_top_dias_sem_venda(mensagem)
        dias_sem_venda_top = sales_tools.get_days_without_sale_top(
            client_id,
            loja,
            limite=int(opcoes_top_sem_venda.get("limite") or 20),
            apenas_com_estoque=bool(opcoes_top_sem_venda.get("apenas_com_estoque")),
            apenas_ja_vendidos=bool(opcoes_top_sem_venda.get("apenas_ja_vendidos")),
        )
        if dias_sem_venda_top:
            resultados.append(dias_sem_venda_top)
    elif _ia_chat_pede_dias_sem_venda(mensagem):
        dias_sem_venda = sales_tools.get_days_without_sale(client_id, mensagem, produto_tool, loja)
        if dias_sem_venda:
            resultados.append(dias_sem_venda)

    if _ia_chat_pede_previsao_ruptura_estoque(mensagem):
        previsao_ruptura = sales_tools.get_stockout_forecast(client_id, mensagem, produto_tool, loja, lookback_days=30, limite=100)
        if previsao_ruptura:
            resultados.append(previsao_ruptura)

    if _ia_chat_pede_consulta_margem(mensagem):
        margem_tool = _ia_tool_get_product_margin(client_id, mensagem, produto_tool)
        if margem_tool:
            resultados.append(margem_tool)

    if _ia_chat_pede_consulta_vendas(mensagem) or pede_analise_especialista:
        if data_inicio and data_fim:
            qtd_vendas = sales_tools.get_sales_quantity_by_period(client_id, data_inicio, data_fim, loja)
            if qtd_vendas:
                resultados.append(qtd_vendas)
            if pede_vendas_loja_virtual:
                vendas_loja_virtual = sales_tools.get_sales_by_virtual_store_period(client_id, data_inicio, data_fim, loja, limite=10)
                if vendas_loja_virtual:
                    resultados.append(vendas_loja_virtual)

        if data_inicio and data_fim and pede_top_vendas:
            resumo_periodo = sales_tools.get_sales_by_period(client_id, data_inicio, data_fim, loja, limite=5)
            if resumo_periodo:
                resultados.append(resumo_periodo)
                resultados.append({
                    "function": "get_sales_data",
                    "arguments": {"data_inicio": data_inicio, "data_fim": data_fim, "loja": loja or ""},
                    "result": {"mode": "top_skus", "items": (resumo_periodo.get("result") or {}).get("top_skus") or []},
                })
        else:
            sku = sales_tools.resolve_sku(client_id, mensagem, produto_tool)
            if sku and data_inicio and data_fim:
                vendas_devolucoes = sales_tools.query_sku_sales_returns(client_id, data_inicio, data_fim, sku, loja)
                if vendas_devolucoes:
                    resultados.append({
                        "function": "get_sales_and_returns_data",
                        "arguments": {"sku": sku, "data_inicio": data_inicio, "data_fim": data_fim, "loja": loja or ""},
                        "result": vendas_devolucoes,
                    })
                venda = sales_tools.query_sku_sales(client_id, data_inicio, data_fim, sku, loja)
                if venda:
                    resultados.append({
                        "function": "get_sales_data",
                        "arguments": {"sku": sku, "data_inicio": data_inicio, "data_fim": data_fim, "loja": loja or ""},
                        "result": venda,
                    })
                if pede_vendas_loja_virtual:
                    vendas_sku_loja_virtual = sales_tools.get_sales_by_sku_virtual_store(
                        client_id,
                        mensagem,
                        contexto,
                        produto_tool,
                        loja,
                        limite=10,
                    )
                    if vendas_sku_loja_virtual:
                        resultados.append(vendas_sku_loja_virtual)
            elif data_inicio and data_fim:
                resumo_periodo = sales_tools.get_sales_by_period(client_id, data_inicio, data_fim, loja, limite=5)
                if resumo_periodo:
                    resultados.append(resumo_periodo)

    if _ia_chat_pede_consulta_devolucoes(mensagem) or pede_analise_especialista:
        sku_devolucao = sales_tools.resolve_sku(client_id, mensagem, produto_tool)
        if data_inicio and data_fim:
            qtd_devolucoes = sales_tools.get_returns_quantity_by_period(client_id, data_inicio, data_fim, loja)
            if qtd_devolucoes:
                resultados.append(qtd_devolucoes)
            resumo_devolucoes = sales_tools.get_returns_by_period(client_id, data_inicio, data_fim, loja, limite=10)
            if resumo_devolucoes:
                resultados.append(resumo_devolucoes)
            taxa_dev = sales_tools.get_return_rate_by_period(client_id, data_inicio, data_fim, loja)
            if taxa_dev:
                resultados.append(taxa_dev)

        if sku_devolucao and data_inicio and data_fim:
            devolucoes_sku = sales_tools.get_returns_by_sku_period(client_id, mensagem, contexto, produto_tool, loja)
            if devolucoes_sku:
                resultados.append(devolucoes_sku)

        devolucoes = sales_tools.get_returns_data(client_id, mensagem, contexto, produto_tool, loja)
        if devolucoes:
            resultados.append(devolucoes)

    if data_inicio and data_fim:
        if _ia_chat_pede_ticket_medio(mensagem):
            ticket = sales_tools.get_avg_ticket_by_period(client_id, data_inicio, data_fim, loja)
            if ticket:
                resultados.append(ticket)

        if _ia_chat_pede_taxa_devolucao(mensagem):
            taxa_dev = sales_tools.get_return_rate_by_period(client_id, data_inicio, data_fim, loja)
            if taxa_dev:
                resultados.append(taxa_dev)

        if _ia_chat_pede_serie_temporal(mensagem):
            serie = sales_tools.get_sales_timeseries(client_id, data_inicio, data_fim, loja)
            if serie:
                resultados.append(serie)

        if _ia_chat_pede_anomalia(mensagem):
            anomalias = sales_tools.detect_sales_anomalies(client_id, data_inicio, data_fim, loja)
            if anomalias:
                resultados.append(anomalias)

        if _ia_chat_pede_lucro_periodo(mensagem):
            lucro = sales_tools.get_profit_by_period(client_id, data_inicio, data_fim, loja)
            if lucro:
                resultados.append(lucro)

    if _ia_chat_pede_comparativo_periodo(mensagem):
        periodos = sales_tools.extract_comparison_periods(mensagem, contexto)
        if periodos:
            (data_inicio_a, data_fim_a), (data_inicio_b, data_fim_b) = periodos
            comparativo = sales_tools.get_period_comparison(
                client_id,
                data_inicio_a,
                data_fim_a,
                data_inicio_b,
                data_fim_b,
                loja,
            )
            if comparativo:
                resultados.append(comparativo)

    if data_inicio and data_fim and (_ia_chat_pede_consulta_vendas(mensagem) or _ia_chat_pede_consulta_devolucoes(mensagem)):
        aviso_escopo = sales_tools.scope_notice(client_id, loja)
        if aviso_escopo:
            resultados.append(aviso_escopo)

    return resultados


def _ia_chat_contexto_funcoes(payload: IAChatRequest, client_id: str) -> str:
    resultados = payload.tool_results if isinstance(payload.tool_results, list) else None
    if resultados is None:
        resultados = _ia_chat_executar_funcoes(payload, client_id)
        payload.tool_results = resultados
    if not resultados:
        return ""

    linhas = [
        "Resultados de funÃƒÂ§ÃƒÂµes executadas pelo backend com dados reais:",
        "FunÃƒÂ§ÃƒÂµes disponÃƒÂ­veis: get_product_data(sku_ou_termo), get_product_registry_info(sku_ou_termo), get_stock_data(sku_ou_termo), get_product_margin(sku_ou_termo), get_days_without_sale(sku, loja), get_days_without_sale_top(loja, limite, apenas_com_estoque, apenas_ja_vendidos), get_stockout_forecast(sku, data_inicio, data_fim, janela_dias, loja), get_sales_data(sku, data_inicio, data_fim, loja), get_returns_data(sku, data_inicio, data_fim, loja), get_returns_by_period(data_inicio, data_fim, loja, limite), get_returns_by_sku_period(sku, data_inicio, data_fim, loja), get_sales_and_returns_data(sku, data_inicio, data_fim, loja), get_sales_by_period(data_inicio, data_fim, loja), get_sales_quantity_by_period(data_inicio, data_fim, loja), get_sales_by_virtual_store_period(data_inicio, data_fim, loja, limite), get_sales_by_sku_virtual_store(sku, data_inicio, data_fim, loja, limite), get_returns_quantity_by_period(data_inicio, data_fim, loja), get_avg_ticket_by_period(data_inicio, data_fim, loja), get_return_rate_by_period(data_inicio, data_fim, loja), get_period_comparison(data_inicio_a, data_fim_a, data_inicio_b, data_fim_b, loja), get_sales_timeseries(data_inicio, data_fim, loja), detect_sales_anomalies(data_inicio, data_fim, loja), get_profit_by_period(data_inicio, data_fim, loja).",
    ]
    linhas.append(
        "Funcao de imagem disponivel: get_product_image(sku_ou_termo). "
        "Quando ela retornar imagem_markdown, use exatamente esse Markdown para mostrar a imagem no chat."
    )
    linhas.append(
        "Funcoes read-only de integracoes externas: get_integrations_status(loja), "
        "get_mercado_livre_listing(sku/item_id/loja) e get_bling_product(sku/id_bling/loja). "
        "Use esses resultados apenas para consulta; nao afirme que alterou preco, estoque, anuncios, pedidos ou produtos."
    )
    linhas.append(
        "Funcoes mensais disponiveis: get_sales_by_month_period(data_inicio, data_fim, loja, limite_meses), "
        "get_month_sales_returns_details(mes_ano, loja, limite_skus), "
        "get_sku_sales_by_month(sku, mes_ano, loja), compare_sku_sales_months(sku, meses_ano, loja), "
        "get_top_skus_sales_by_month(mes_ano, loja, limite), "
        "get_top_skus_returns_by_month(mes_ano, loja, limite)."
    )

    for item in resultados:
        nome_funcao = str(item.get("function") or "funcao")
        args = item.get("arguments") or {}
        argumentos = json.dumps(args, ensure_ascii=False)
        resultado = item.get("result") or {}
        linhas.append(f"FunÃƒÂ§ÃƒÂ£o: {nome_funcao}")
        linhas.append(f"Argumentos: {argumentos}")
        if nome_funcao == "get_product_data":
            matches = resultado.get("matches") or []
            if not matches:
                linhas.append("Resultado: nenhum produto encontrado.")
            else:
                linhas.append("Resultado:")
                for prod in matches[:5]:
                    linhas.append(
                        f"- SKU {prod.get('sku')} | {prod.get('nome')} | marca {prod.get('marca') or '-'} | categoria {prod.get('categoria') or '-'} | saldo loja {float(prod.get('saldo_loja') or 0):g} | saldo full {float(prod.get('saldo_full') or 0):g} | preco {prod.get('preco') or '-'} | imagem {prod.get('imagem_url') or '-'}"
                    )
        elif nome_funcao == "get_product_registry_info":
            matches = resultado.get("matches") or []
            if not matches:
                linhas.append("Resultado: cadastro do produto nÃ£o encontrado.")
            else:
                linhas.append("Resultado:")
                for prod in matches[:3]:
                    linhas.append(
                        f"- SKU {prod.get('sku')} | {prod.get('nome')} | marca {prod.get('marca') or '-'} | categoria {prod.get('categoria') or '-'} | ncm {prod.get('ncm') or '-'} | cest {prod.get('cest') or '-'} | imagem {prod.get('imagem_url') or '-'}"
                    )
                    if prod.get("descricao"):
                        linhas.append(f"  descricao: {str(prod.get('descricao'))[:240]}")
        elif nome_funcao == "get_product_image":
            if not resultado.get("found"):
                linhas.append("Resultado: produto nÃƒÆ’Ã‚Â£o encontrado no cadastro.")
            elif not resultado.get("imagem_encontrada"):
                linhas.append(
                    "Resultado: produto encontrado, mas sem imagem salva no cadastro. "
                    f"SKU {resultado.get('sku') or '-'} | {resultado.get('produto') or '-'}"
                )
            else:
                origem = str(resultado.get("origem_imagem") or "cadastro")
                origem_txt = "Mercado Livre" if origem == "mercado_livre" else "cadastro"
                linhas.append(
                    f"Resultado: imagem do SKU encontrada via {origem_txt}. "
                    f"SKU {resultado.get('sku') or '-'} | {resultado.get('produto') or '-'} | URL {resultado.get('imagem_url') or '-'}"
                )
                linhas.append(
                    "Instrucao: para enviar a imagem ao usuario, responda usando exatamente esta linha Markdown:"
                )
                linhas.append(str(resultado.get("imagem_markdown") or ""))
        elif nome_funcao == "get_integrations_status":
            linhas.append(
                "Resultado: "
                f"{int(resultado.get('total_lojas') or 0)} lojas avaliadas | "
                f"Mercado Livre conectado em {int(resultado.get('ml_conectadas') or 0)} | "
                f"Bling conectado em {int(resultado.get('bling_conectadas') or 0)}"
            )
            for loja_item in (resultado.get("lojas") or [])[:12]:
                linhas.append(
                    f"- {loja_item.get('loja') or '-'} | ML {'sim' if loja_item.get('mercado_livre_conectado') else 'nao'}"
                    f" | user_id {loja_item.get('mercado_livre_user_id') or '-'}"
                    f" | Bling {'sim' if loja_item.get('bling_conectado') else 'nao'}"
                )
        elif nome_funcao == "get_mercado_livre_listing":
            matches = resultado.get("matches") or []
            linhas.append(
                "Resultado: "
                f"consulta read-only Mercado Livre | encontrados {len(matches)} anuncios."
            )
            if not matches:
                linhas.append(str(resultado.get("message") or "Nenhum anuncio encontrado."))
            for anuncio in matches[:10]:
                linhas.append(
                    f"- Loja {anuncio.get('loja') or '-'} | {anuncio.get('id') or '-'} | "
                    f"{anuncio.get('title') or '-'} | status {anuncio.get('status') or '-'} | "
                    f"SKU {anuncio.get('seller_sku') or '-'} | preco {anuncio.get('price') or '-'} | "
                    f"disponivel {anuncio.get('available_quantity') if anuncio.get('available_quantity') is not None else '-'} | "
                    f"vendidos {anuncio.get('sold_quantity') if anuncio.get('sold_quantity') is not None else '-'} | "
                    f"link {anuncio.get('permalink') or '-'}"
                )
                if anuncio.get("description"):
                    linhas.append(f"  descricao: {str(anuncio.get('description'))[:700]}")
                for var in (anuncio.get("variations") or [])[:5]:
                    linhas.append(
                        f"  variacao {var.get('id') or '-'} | SKU {var.get('sku') or '-'} | "
                        f"preco {var.get('price') or '-'} | disponivel {var.get('available_quantity') if var.get('available_quantity') is not None else '-'}"
                    )
            for erro in (resultado.get("errors") or [])[:3]:
                linhas.append(f"- Aviso {erro.get('loja') or '-'}: {erro.get('erro') or '-'}")
        elif nome_funcao == "get_bling_product":
            matches = resultado.get("matches") or []
            linhas.append(
                "Resultado: "
                f"consulta read-only Bling | encontrados {len(matches)} produtos."
            )
            if not matches:
                linhas.append(str(resultado.get("message") or "Nenhum produto encontrado no Bling."))
            for prod in matches[:10]:
                linhas.append(
                    f"- Loja {prod.get('loja') or '-'} | id {prod.get('id_bling') or '-'} | "
                    f"SKU {prod.get('sku') or '-'} | {prod.get('nome') or '-'} | "
                    f"situacao {prod.get('situacao') or '-'} | preco {prod.get('preco') or '-'} | "
                    f"custo {prod.get('preco_custo') or '-'} | saldo loja {float(prod.get('saldo_loja') or 0):g} | "
                    f"saldo full {float(prod.get('saldo_full') or 0):g} | ncm {prod.get('ncm') or '-'} | cest {prod.get('cest') or '-'}"
                )
            for erro in (resultado.get("errors") or [])[:3]:
                linhas.append(f"- Aviso {erro.get('loja') or '-'}: {erro.get('erro') or '-'}")
        elif nome_funcao == "get_stock_data":
            linhas.append(
                "Resultado: "
                f"SKU {resultado.get('sku') or '-'} | {resultado.get('nome') or '-'} | saldo loja {float(resultado.get('saldo_loja_total') or 0):g} | saldo full {float(resultado.get('saldo_full_total') or 0):g} | saldo total {float(resultado.get('saldo_total') or 0):g}"
            )
        elif nome_funcao == "get_stockout_forecast":
            if isinstance(resultado.get("itens"), list):
                linhas.append(
                    "Resultado: "
                    f"previsÃƒÂ£o de ruptura para {int(resultado.get('total_skus_analisados') or 0)} SKUs (janela {int(resultado.get('janela_dias') or 0)} dias)."
                )
                for item_prev in (resultado.get("itens") or [])[:10]:
                    dias = item_prev.get("dias_ate_ruptura")
                    dias_txt = "sem previsÃƒÂ£o" if dias is None else f"{float(dias):.1f} dias"
                    linhas.append(
                        f"- SKU {item_prev.get('sku')} | {item_prev.get('nome') or '-'} | saldo {float(item_prev.get('saldo_total') or 0):g} | media/dia {float(item_prev.get('media_venda_dia') or 0):.2f} | ruptura {dias_txt} ({item_prev.get('data_prevista_ruptura') or '-'}) | risco {item_prev.get('risco_ruptura') or '-'}"
                    )
            else:
                dias = resultado.get("dias_ate_ruptura")
                dias_txt = "sem previsÃƒÂ£o" if dias is None else f"{float(dias):.1f} dias"
                linhas.append(
                    "Resultado: "
                    f"SKU {resultado.get('sku') or '-'} | {resultado.get('nome') or '-'} | saldo {float(resultado.get('saldo_total') or 0):g} | media/dia {float(resultado.get('media_venda_dia') or 0):.2f} | ruptura {dias_txt} ({resultado.get('data_prevista_ruptura') or '-'}) | risco {resultado.get('risco_ruptura') or '-'}"
                )
        elif nome_funcao == "get_product_margin":
            linhas.append(
                "Resultado: "
                f"SKU {resultado.get('sku') or '-'} | preco R$ {float(resultado.get('preco') or 0):.2f} | custo R$ {float(resultado.get('custo') or 0):.2f} | imposto {float(resultado.get('imposto_percentual') or 0):.2f}% | margem estimada R$ {float(resultado.get('margem_valor_estimada') or 0):.2f} ({float(resultado.get('margem_percentual_estimada') or 0):.2f}%)"
            )
        elif nome_funcao == "get_days_without_sale":
            dias = resultado.get("dias_sem_vender")
            dias_txt = "sem histÃƒÂ³rico de venda" if dias is None else f"{int(dias)} dias"
            linhas.append(
                "Resultado: "
                f"SKU {resultado.get('sku') or '-'} | {resultado.get('produto') or '-'} | ÃƒÂºltima venda {resultado.get('ultima_venda') or '-'} | sem vender hÃƒÂ¡ {dias_txt}"
            )
        elif nome_funcao == "get_days_without_sale_top":
            filtros = []
            if bool((args or {}).get("apenas_com_estoque")):
                filtros.append("somente com estoque")
            if bool((args or {}).get("apenas_ja_vendidos")):
                filtros.append("somente jÃƒÂ¡ vendidos")
            filtros_txt = " | filtros: " + ", ".join(filtros) if filtros else ""
            linhas.append(
                "Resultado: "
                f"ranking de SKUs sem venda (top {len(resultado.get('itens') or [])}) | total avaliados {int(resultado.get('total_skus_avaliados') or 0)}{filtros_txt}"
            )
            for item_sem in (resultado.get("itens") or [])[:20]:
                dias = item_sem.get("dias_sem_vender")
                dias_txt = "sem histÃƒÂ³rico" if dias is None else f"{int(dias)} dias"
                linhas.append(
                    f"- SKU {item_sem.get('sku')} | {item_sem.get('produto') or '-'} | saldo {float(item_sem.get('saldo_total') or 0):g} | ÃƒÂºltima venda {item_sem.get('ultima_venda') or '-'} | {dias_txt}"
                )
        elif nome_funcao == "get_sales_by_period":
            linhas.append(
                "Resultado: "
                f"periodo {resultado.get('data_inicio')} a {resultado.get('data_fim')} | pedidos {int(resultado.get('pedidos_total') or 0)} | unidades {float(resultado.get('quantidade_total') or 0):g} | faturamento R$ {float(resultado.get('valor_total') or 0):.2f}"
            )
            for venda in (resultado.get("top_skus") or [])[:5]:
                linhas.append(
                    f"- top SKU {venda.get('sku')} | {venda.get('nome')} | {float(venda.get('qtd') or 0):g} unidades | R$ {float(venda.get('valor') or 0):.2f}"
                )
        elif nome_funcao == "get_month_sales_returns_details":
            totais = resultado.get("totais") or {}
            linhas.append(
                "Resultado: "
                f"detalhamento completo do mes {resultado.get('mes_ano')} ({resultado.get('data_inicio')} a {resultado.get('data_fim')}) | "
                f"vendido {float(totais.get('quantidade_vendida') or 0):g} un / R$ {float(totais.get('valor_vendido') or 0):.2f} | "
                f"devolvido {float(totais.get('quantidade_devolvida') or 0):g} un / R$ {float(totais.get('valor_devolvido') or 0):.2f} | "
                f"liquido {float(totais.get('quantidade_liquida') or 0):g} un / R$ {float(totais.get('valor_liquido') or 0):.2f} | "
                f"SKUs {int(totais.get('total_skus') or 0)}"
            )
            for item_mes in (resultado.get("itens") or [])[:200]:
                img = item_mes.get("imagem_url") or "-"
                linhas.append(
                    f"- SKU {item_mes.get('sku')} | {item_mes.get('produto') or '-'} | "
                    f"vendido {float(item_mes.get('quantidade_vendida') or 0):g} un / R$ {float(item_mes.get('valor_vendido') or 0):.2f} | "
                    f"devolvido {float(item_mes.get('quantidade_devolvida') or 0):g} un / R$ {float(item_mes.get('valor_devolvido') or 0):.2f} | "
                    f"imagem {img}"
                )
        elif nome_funcao == "get_sku_sales_by_month":
            linhas.append(
                "Resultado: "
                f"SKU {resultado.get('sku') or '-'} | {resultado.get('produto') or '-'} | mes {resultado.get('mes_ano') or '-'} | "
                f"vendido {float(resultado.get('quantidade_vendida') or 0):g} unidades | "
                f"valor total vendido R$ {float(resultado.get('valor_vendido') or 0):.2f} | "
                f"devolvido {float(resultado.get('quantidade_devolvida') or 0):g} unidades | "
                f"valor devolvido R$ {float(resultado.get('valor_devolvido') or 0):.2f}"
            )
        elif nome_funcao == "compare_sku_sales_months":
            comp = resultado.get("comparativo_primeiro_ultimo") or {}
            linhas.append(
                "Resultado: "
                f"comparativo mensal do SKU {resultado.get('sku') or '-'} | "
                f"{comp.get('mes_base') or '-'} vs {comp.get('mes_comparado') or '-'} | "
                f"variacao quantidade {float(comp.get('variacao_quantidade') or 0):g} un | "
                f"variacao valor R$ {float(comp.get('variacao_valor') or 0):.2f}"
            )
            if comp.get("variacao_quantidade_percentual") is not None:
                linhas.append(f"- variaÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o quantidade percentual: {float(comp.get('variacao_quantidade_percentual') or 0):.2f}%")
            if comp.get("variacao_valor_percentual") is not None:
                linhas.append(f"- variaÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o valor percentual: {float(comp.get('variacao_valor_percentual') or 0):.2f}%")
            for mes_item in (resultado.get("meses") or [])[:12]:
                linhas.append(
                    f"- {mes_item.get('mes_ano')} | vendido {float(mes_item.get('quantidade_vendida') or 0):g} un | "
                    f"R$ {float(mes_item.get('valor_vendido') or 0):.2f} | devolvido {float(mes_item.get('quantidade_devolvida') or 0):g} un"
                )
        elif nome_funcao == "get_sales_by_month_period":
            mes_valor = resultado.get("mes_campeao_valor") or {}
            mes_qtd = resultado.get("mes_campeao_quantidade") or {}
            linhas.append(
                "Resultado: "
                f"vendas mensais no periodo {resultado.get('data_inicio')} a {resultado.get('data_fim')} | "
                f"mes campeao em faturamento {mes_valor.get('mes_ano') or '-'} com R$ {float(mes_valor.get('valor_vendido') or 0):.2f} | "
                f"mes campeao em quantidade {mes_qtd.get('mes_ano') or '-'} com {float(mes_qtd.get('quantidade_vendida') or 0):g} un"
            )
            for mes_item in (resultado.get("meses") or [])[:24]:
                linhas.append(
                    f"- {mes_item.get('mes_ano')} | pedidos {int(mes_item.get('pedidos_total') or 0)} | "
                    f"vendido {float(mes_item.get('quantidade_vendida') or 0):g} un | R$ {float(mes_item.get('valor_vendido') or 0):.2f}"
                )
        elif nome_funcao == "get_top_skus_sales_by_month":
            campeao = resultado.get("sku_campeao") or {}
            linhas.append(
                "Resultado: "
                f"top SKUs vendidos no mes {resultado.get('mes_ano')} ({resultado.get('data_inicio')} a {resultado.get('data_fim')}) | "
                f"SKU lider {campeao.get('sku') or '-'} | {campeao.get('produto') or '-'} | "
                f"{float(campeao.get('quantidade_vendida') or 0):g} un | R$ {float(campeao.get('valor_vendido') or 0):.2f}"
            )
            for item_top in (resultado.get("top_skus") or [])[:20]:
                linhas.append(
                    f"- SKU {item_top.get('sku')} | {item_top.get('produto') or '-'} | "
                    f"vendido {float(item_top.get('quantidade_vendida') or 0):g} un | R$ {float(item_top.get('valor_vendido') or 0):.2f}"
                )
        elif nome_funcao == "get_top_skus_returns_by_month":
            campeao = resultado.get("sku_campeao") or {}
            linhas.append(
                "Resultado: "
                f"top SKUs devolvidos no mes {resultado.get('mes_ano')} ({resultado.get('data_inicio')} a {resultado.get('data_fim')}) | "
                f"total devolvido {float(resultado.get('quantidade_devolvida_total') or 0):g} un / R$ {float(resultado.get('valor_devolvido_total') or 0):.2f} | "
                f"SKU lider {campeao.get('sku') or '-'} | {campeao.get('produto') or '-'} | "
                f"{float(campeao.get('quantidade_devolvida') or 0):g} un | R$ {float(campeao.get('valor_devolvido') or 0):.2f}"
            )
            for item_top in (resultado.get("top_skus") or [])[:20]:
                linhas.append(
                    f"- SKU {item_top.get('sku')} | {item_top.get('produto') or '-'} | "
                    f"devolvido {float(item_top.get('quantidade_devolvida') or 0):g} un | R$ {float(item_top.get('valor_devolvido') or 0):.2f}"
                )
        elif nome_funcao == "get_sales_data":
            if resultado.get("mode") == "top_skus":
                linhas.append("Resultado:")
                for venda in (resultado.get("items") or [])[:5]:
                    linhas.append(
                        f"- SKU {venda.get('sku')} | {venda.get('nome')} | {float(venda.get('qtd') or 0):g} unidades | R$ {float(venda.get('valor') or 0):.2f}"
                    )
            else:
                linhas.append(
                    "Resultado: "
                    f"SKU {resultado.get('sku')} | {resultado.get('produto') or '-'} | "
                    f"{float(resultado.get('quantidade') or 0):g} unidades | R$ {float(resultado.get('valor') or 0):.2f} | "
                    f"ultima venda {resultado.get('ultima_data') or '-'}"
                )
        elif nome_funcao == "get_sales_quantity_by_period":
            linhas.append(
                "Resultado: "
                f"periodo {resultado.get('data_inicio')} a {resultado.get('data_fim')} | quantidade vendida total {float(resultado.get('quantidade_vendida_total') or 0):g} unidades"
            )
        elif nome_funcao == "get_sales_by_virtual_store_period":
            linhas.append(
                "Resultado: "
                f"vendas por loja virtual no perÃƒÂ­odo {resultado.get('data_inicio')} a {resultado.get('data_fim')} | total de lojas virtuais {int(resultado.get('total_lojas_virtuais') or 0)}"
            )
            for item_lv in (resultado.get("itens") or [])[:10]:
                linhas.append(
                    f"- {item_lv.get('loja_virtual') or '-'} | pedidos {int(item_lv.get('pedidos_total') or 0)} | vendido {float(item_lv.get('quantidade_vendida') or 0):g} un | R$ {float(item_lv.get('valor_vendido') or 0):.2f}"
                )
        elif nome_funcao == "get_sales_by_sku_virtual_store":
            linhas.append(
                "Resultado: "
                f"SKU {resultado.get('sku') or '-'} por loja virtual no perÃƒÂ­odo {resultado.get('data_inicio')} a {resultado.get('data_fim')} | total de lojas virtuais {int(resultado.get('total_lojas_virtuais') or 0)}"
            )
            for item_lv in (resultado.get("itens") or [])[:10]:
                linhas.append(
                    f"- {item_lv.get('loja_virtual') or '-'} | pedidos {int(item_lv.get('pedidos_total') or 0)} | vendido {float(item_lv.get('quantidade_vendida') or 0):g} un | R$ {float(item_lv.get('valor_vendido') or 0):.2f} | ÃƒÂºltima venda {item_lv.get('ultima_venda') or '-'}"
                )
        elif nome_funcao == "get_returns_quantity_by_period":
            linhas.append(
                "Resultado: "
                f"periodo {resultado.get('data_inicio')} a {resultado.get('data_fim')} | quantidade devolvida total {float(resultado.get('quantidade_devolvida_total') or 0):g} unidades"
            )
        elif nome_funcao == "get_returns_by_period":
            linhas.append(
                "Resultado: "
                f"devoluÃƒÂ§ÃƒÂµes no perÃƒÂ­odo {resultado.get('data_inicio')} a {resultado.get('data_fim')} | qtd {float(resultado.get('quantidade_devolvida_total') or 0):g} | valor R$ {float(resultado.get('valor_devolvido_total') or 0):.2f}"
            )
            for dev in (resultado.get("top_skus") or [])[:10]:
                linhas.append(
                    f"- SKU {dev.get('sku')} | {dev.get('produto') or '-'} | devolvido {float(dev.get('quantidade_devolvida') or 0):g} un | R$ {float(dev.get('valor_devolvido') or 0):.2f}"
                )
        elif nome_funcao == "get_returns_by_sku_period":
            linhas.append(
                "Resultado: "
                f"SKU {resultado.get('sku') or '-'} | {resultado.get('produto') or '-'} | perÃƒÂ­odo {resultado.get('data_inicio')} a {resultado.get('data_fim')} | devolvido {float(resultado.get('quantidade_devolvida') or 0):g} un | R$ {float(resultado.get('valor_devolvido') or 0):.2f} | ÃƒÂºltima devoluÃƒÂ§ÃƒÂ£o {resultado.get('ultima_devolucao') or '-'}"
            )
        elif nome_funcao == "get_avg_ticket_by_period":
            linhas.append(
                "Resultado: "
                f"periodo {resultado.get('data_inicio')} a {resultado.get('data_fim')} | ticket mÃƒÂ©dio R$ {float(resultado.get('ticket_medio') or 0):.2f} | pedidos {int(resultado.get('pedidos_total') or 0)}"
            )
        elif nome_funcao == "get_return_rate_by_period":
            linhas.append(
                "Resultado: "
                f"periodo {resultado.get('data_inicio')} a {resultado.get('data_fim')} | taxa devoluÃƒÂ§ÃƒÂ£o (qtd) {float(resultado.get('taxa_devolucao_quantidade_percentual') or 0):.2f}% | taxa devoluÃƒÂ§ÃƒÂ£o (valor) {float(resultado.get('taxa_devolucao_valor_percentual') or 0):.2f}%"
            )
        elif nome_funcao == "get_period_comparison":
            comp = resultado.get("comparativo") or {}
            linhas.append(
                "Resultado: "
                f"comparativo entre perÃƒÂ­odos | quantidade {float(comp.get('variacao_quantidade') or 0):g} ({float(comp.get('variacao_quantidade_percentual') or 0):.2f}%) | "
                f"faturamento R$ {float(comp.get('variacao_faturamento') or 0):.2f} ({float(comp.get('variacao_faturamento_percentual') or 0):.2f}%) | "
                f"pedidos {int(comp.get('variacao_pedidos') or 0)} ({float(comp.get('variacao_pedidos_percentual') or 0):.2f}%)"
            )
        elif nome_funcao == "get_sales_timeseries":
            pontos = (resultado.get("pontos") or [])[:5]
            linhas.append(f"Resultado: sÃƒÂ©rie temporal com {len(resultado.get('pontos') or [])} pontos.")
            for p in pontos:
                linhas.append(
                    f"- {p.get('data')} | vendido {float(p.get('quantidade_vendida') or 0):g} un | R$ {float(p.get('valor_vendido') or 0):.2f} | devolvido {float(p.get('quantidade_devolvida') or 0):g} un"
                )
        elif nome_funcao == "detect_sales_anomalies":
            alertas = (resultado.get("alertas") or [])[:10]
            linhas.append(f"Resultado: {len(resultado.get('alertas') or [])} anomalias detectadas.")
            for alerta in alertas:
                linhas.append(
                    f"- {alerta.get('data')} | {alerta.get('tipo')} | R$ {float(alerta.get('valor_vendido') or 0):.2f}"
                )
        elif nome_funcao == "get_profit_by_period":
            cobertura = float(resultado.get("cobertura_faturamento_percentual") or 0)
            if resultado.get("coverage_sufficient"):
                linhas.append(
                    "Resultado: "
                    f"periodo {resultado.get('data_inicio')} a {resultado.get('data_fim')} | faturamento R$ {float(resultado.get('faturamento_total') or 0):.2f} | "
                    f"custo estimado R$ {float(resultado.get('custo_total_estimado') or 0):.2f} | imposto estimado R$ {float(resultado.get('imposto_total_estimado') or 0):.2f} | "
                    f"lucro estimado R$ {float(resultado.get('lucro_estimado') or 0):.2f} ({float(resultado.get('margem_percentual_estimada') or 0):.2f}%) | "
                    f"cobertura {cobertura:.2f}%"
                )
            else:
                linhas.append(
                    "Resultado: margem consolidada indisponivel por dados insuficientes | "
                    f"faturamento R$ {float(resultado.get('faturamento_total') or 0):.2f} | cobertura de custo {cobertura:.2f}% | "
                    f"parcela calculavel R$ {float(resultado.get('faturamento_com_custo') or 0):.2f}"
                )
        elif nome_funcao == "get_sales_and_returns_data":
            linhas.append(
                "Resultado: "
                f"SKU {resultado.get('sku') or '-'} | {resultado.get('produto') or '-'} | "
                f"vendido {float(resultado.get('quantidade_vendida') or 0):g} unidades / R$ {float(resultado.get('valor_vendido') or 0):.2f} | "
                f"devolvido {float(resultado.get('quantidade_devolvida') or 0):g} unidades / R$ {float(resultado.get('valor_devolvido') or 0):.2f} | "
                f"ultima venda {resultado.get('ultima_venda') or '-'} | ultima devolucao {resultado.get('ultima_devolucao') or '-'}"
            )
        elif nome_funcao == "get_returns_data":
            linhas.append(
                "Resultado: "
                f"SKU {resultado.get('sku') or '-'} | {resultado.get('produto') or '-'} | devolvido {float(resultado.get('quantidade_devolvida') or 0):g} unidades | R$ {float(resultado.get('valor_devolvido') or 0):.2f} | ultima devolucao {resultado.get('ultima_devolucao') or '-'}"
            )
        elif nome_funcao == "store_scope_notice":
            linhas.append(
                "InstruÃƒÂ§ÃƒÂ£o: loja nÃ£o foi informada na pergunta. Informe que os nÃƒÂºmeros consideram todas as lojas e, se fizer sentido, pergunte qual loja especÃƒÂ­fica o usuÃƒÂ¡rio quer analisar."
            )
        else:
            linhas.append(f"Resultado: {json.dumps(resultado, ensure_ascii=False)}")
    return "\n".join(linhas)


def _montar_contexto_ia(payload: IAChatRequest, client_id: str) -> str:
    contexto = payload.context if isinstance(payload.context, dict) else {}
    filtros = contexto.get("filtros") or []
    if isinstance(filtros, dict):
        filtros = [f"{k}: {v}" for k, v in list(filtros.items())[:12] if str(v or "").strip()]

    table = contexto.get("table") or []
    if isinstance(table, dict):
        table = [table]

    contexto_extra = {}
    for key in (
        "data_inicio",
        "data_fim",
        "colunas",
        "table_headers",
        "table_rows",
        "listas",
        "indicadores",
        "resumo_texto",
        "analise_mensal",
        "historico_recente",
        "usuario_atual",
        "nome_usuario",
        "memoria_conversas_usuario",
        "preferencias_resposta_usuario",
        "permissoes_usuario",
    ):
        value = contexto.get(key)
        if value:
            contexto_extra[key] = value

    contexto_seguro = {
        "client_id": client_id,
        "pagina": payload.page or contexto.get("page") or "",
        "titulo": contexto.get("title") or "",
        "url": contexto.get("url") or "",
        "periodo": contexto.get("periodo") or "",
        "loja": contexto.get("loja") or "",
        "data_inicio": contexto.get("data_inicio") or "",
        "data_fim": contexto.get("data_fim") or "",
        "total_linhas": contexto.get("totalLinhas") or 0,
        "cards": (contexto.get("cards") or [])[:8],
        "filtros": filtros[:12],
        "skus_ranking": (contexto.get("skus_ranking") or [])[:30],
        "analise_mensal": contexto.get("analise_mensal") or None,
        "tabela_ociosos": table[:10],
    }
    contexto_seguro.update(contexto_extra)
    return json.dumps(contexto_seguro, ensure_ascii=False, default=str)

PEER_EXPORTS = ['_ia_chat_executar_funcoes', '_ia_chat_contexto_funcoes', '_montar_contexto_ia']
__all__ = PEER_EXPORTS + ["configure_ia_tools_dispatcher_runtime"]

configure_ia_tools_dispatcher_runtime()
