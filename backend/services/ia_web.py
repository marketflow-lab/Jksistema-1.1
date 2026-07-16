"""IA web-search cache and context helpers."""

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
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.ia_common import *
from backend.services.ia_context import get_tenant_id, get_tenant_path
from backend.services.ia_state import *

logger = None


def _configure_runtime_globals(target_globals, runtime_module=None, peers=None):
    runtime = bind_runtime_globals(target_globals, runtime_module)
    if runtime is not None:
        runtime_logger = getattr(runtime, "logger", None)
        if runtime_logger is not None:
            target_globals["logger"] = runtime_logger
    if peers:
        target_globals.update(peers)
    return runtime


def configure_ia_web_runtime(runtime_module=None, peers=None):
    return _configure_runtime_globals(globals(), runtime_module, peers)


def _ia_web_busca_ativa() -> bool:
    valor = str(os.getenv("IA_WEB_SEARCH_ENABLED") or "true").strip().lower()
    return valor not in {"0", "false", "nao", "nÃ£o", "off"}


def _vertex_google_search_grounding_ativo(payload: Optional[IAChatRequest] = None) -> bool:
    if not _ia_web_busca_ativa():
        return False
    ctx = payload.context if payload and isinstance(payload.context, dict) else {}
    if _ia_contexto_desativa_recursos_chat(ctx):
        return False
    if bool(ctx.get("ativar_google_search_grounding")):
        return True
    valor = str(os.getenv("VERTEX_GOOGLE_SEARCH_GROUNDING_ENABLED") or "true").strip().lower()
    if valor in {"0", "false", "nao", "nÃ£o", "off"}:
        return False
    tipo = str(ctx.get("tipo") or "").strip()
    return tipo in {"agente_cloud_perguntas_ml", "resposta_automatica_ml", "novo_fluxo_perguntas_v2"}


IA_WEB_CACHE_LOCK = threading.Lock()


def _ia_web_cache_path(client_id: str) -> str:
    pasta = os.path.join(get_tenant_path(client_id), "ia_web_cache")
    os.makedirs(pasta, exist_ok=True)
    return os.path.join(pasta, "buscas.json")


def _ia_web_ler_cache(client_id: str) -> dict:
    caminho = _ia_web_cache_path(client_id)
    if not os.path.exists(caminho):
        return {"consultas": {}}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if not isinstance(data, dict):
            return {"consultas": {}}
        if not isinstance(data.get("consultas"), dict):
            data["consultas"] = {}
        return data
    except Exception:
        return {"consultas": {}}


def _ia_web_salvar_cache(client_id: str, data: dict) -> None:
    caminho = _ia_web_cache_path(client_id)
    tmp = caminho + ".tmp"
    payload = data if isinstance(data, dict) else {}
    payload.setdefault("consultas", {})
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, caminho)


def _ia_web_cache_key(query: str, tipo: str = "web") -> str:
    normalizada = re.sub(r"\s+", " ", str(query or "").strip().lower())
    return hashlib.sha256(f"{tipo}:{normalizada}".encode("utf-8", errors="ignore")).hexdigest()


def _ia_web_cache_valido(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if not item.get("resultados"):
        return False
    ttl_horas = int(os.getenv("IA_WEB_SEARCH_CACHE_TTL_HORAS", "24") or "24")
    criado = str(item.get("created_at") or "").strip()
    if not criado:
        return False
    try:
        criado_dt = datetime.fromisoformat(criado)
    except Exception:
        return False
    return datetime.now() - criado_dt <= timedelta(hours=max(1, ttl_horas))


def _ia_chat_precisa_busca_web(mensagem: str, page: Optional[str] = None, contexto: Optional[dict] = None) -> bool:
    if _ia_contexto_desativa_recursos_chat(contexto):
        return False
    if not _ia_web_busca_ativa():
        return False
    ctx = contexto if isinstance(contexto, dict) else {}
    if bool(ctx.get("forcar_busca_web_chat") or ctx.get("web_search_required")):
        return True
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False

    gatilhos_web_intencionais = (
        "PESQUISE", "PESQUISAR", "BUSQUE NA INTERNET", "BUSCAR NA INTERNET",
        "NA INTERNET", "NO GOOGLE", "SITE OFICIAL", "FONTE OFICIAL", "FONTES",
        "WEB", "ONLINE", "NOTICIA", "NOTICIAS", "NOTÃƒÂCIA", "NOTÃƒÂCIAS",
        "ULTIMA VERSAO", "ÃƒÅ¡LTIMA VERSÃƒÆ’O", "ATUALIZADO", "RECENTE", "RECENTES",
    )
    if (
        _ia_chat_pede_status_integracoes(mensagem)
        or _ia_chat_pede_consulta_mercado_livre(mensagem)
        or _ia_chat_pede_consulta_bling(mensagem)
    ) and not any(gatilho in texto for gatilho in gatilhos_web_intencionais):
        return False

    gatilhos_explicitos = (
        "PESQUISE", "PESQUISAR", "BUSQUE", "BUSCAR", "PROCURE", "PROCURAR",
        "CONSULTE", "CONSULTAR", "PESQUISA EXTERNA", "BUSCA EXTERNA",
        "NA INTERNET", "NO GOOGLE", "SITE OFICIAL", "FONTE OFICIAL", "FONTES",
        "DOCUMENTACAO", "DOCUMENTAÃƒâ€¡ÃƒÆ’O", "API", "MANUAL", "WEB", "ONLINE",
        "ULTIMA VERSAO", "ÃƒÅ¡LTIMA VERSÃƒÆ’O", "ATUALIZADO", "RECENTE", "RECENTES",
        "HOJE", "AGORA", "NOTICIA", "NOTÃƒÂCIA", "LEGISLACAO", "LEGISLAÃƒâ€¡ÃƒÆ’O",
        "COTACAO", "COTAÃƒâ€¡ÃƒÆ’O", "PRECO ATUAL", "VALOR ATUAL",
    )
    if any(gatilho in texto for gatilho in gatilhos_explicitos):
        return True

    if "HTTP://" in texto or "HTTPS://" in texto or ".COM" in texto or ".BR" in texto:
        return True

    gatilhos_dado_publico = (
        "QUAL O SITE", "QUAL A PAGINA", "ONDE ENCONTRAR", "QUEM E", "O QUE E",
        "COMO FUNCIONA", "COMO FAZER", "COMPATIBILIDADE", "ESPECIFICACAO",
        "ESPECIFICACOES", "CODIGO OEM", "PART NUMBER", "NUMERO DA PECA",
        "MODELO COMPATIVEL", "ANO COMPATIVEL", "MERCADO LIVRE", "CONCORRENTE",
    )
    if any(gatilho in texto for gatilho in gatilhos_dado_publico):
        return True

    page_norm = _normalizar_texto(page or "")
    ctx = contexto if isinstance(contexto, dict) else {}
    tem_contexto_estruturado = bool((ctx.get("cards") or ctx.get("table") or ctx.get("table_rows") or ctx.get("skus_ranking")))
    if not tem_contexto_estruturado and page_norm not in {"VENDAS", "ESTOQUE", "DEVOLUCOES", "DEVOLUÃƒâ€¡Ãƒâ€¢ES"}:
        return True

    return False


def _ia_web_normalizar_result_url(url: str) -> str:
    url_txt = str(url or "").strip()
    if not url_txt:
        return ""
    if url_txt.startswith("//"):
        url_txt = "https:" + url_txt
    try:
        parsed = urlparse(url_txt)
        if "duckduckgo.com" in (parsed.netloc or "") and parsed.path.startswith("/l/"):
            uddg = parse_qs(parsed.query).get("uddg")
            if uddg and uddg[0]:
                return unquote(uddg[0])
    except Exception:
        pass
    return url_txt


def _ia_web_extrair_resultados_jina_duckduckgo(texto_md: str, max_results: int = 5) -> list[dict]:
    """Extrai resultados do Markdown devolvido pelo Jina para o DuckDuckGo.

    Os links dos resultados chegam normalmente encapsulados em ``/l/?uddg=``.
    A normalizacao precisa ocorrer antes de descartar links internos do buscador.
    """
    texto = str(texto_md or "")
    limite = max(1, int(max_results or 1))
    resultados = []
    urls_vistas = set()
    matches = list(
        re.finditer(
            r"^##\s+\[(?P<title>[^\]]+)\]\((?P<url>https?://[^)]+)\)",
            texto,
            flags=re.MULTILINE,
        )
    )
    for idx, match in enumerate(matches):
        titulo = re.sub(r"\s+", " ", match.group("title") or "").strip()
        url = _ia_web_normalizar_result_url(match.group("url") or "")
        if not titulo or not url or "duckduckgo.com/y.js" in url or "ad_domain=" in url:
            continue
        parsed = urlparse(url)
        if "duckduckgo.com" in (parsed.netloc or ""):
            continue
        url_chave = url.lower()
        if url_chave in urls_vistas:
            continue
        urls_vistas.add(url_chave)
        trecho_inicio = match.end()
        trecho_fim = matches[idx + 1].start() if idx + 1 < len(matches) else min(len(texto), trecho_inicio + 800)
        snippet = re.sub(r"\s+", " ", texto[trecho_inicio:trecho_fim]).strip()
        snippet = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", snippet)
        resultados.append({
            "title": titulo[:180],
            "url": url[:600],
            "snippet": snippet[:360],
            "provider": "jina_duckduckgo",
        })
        if len(resultados) >= limite:
            break
    return resultados


def _ia_web_buscar_noticias(query: str, max_results: int = 5) -> list[dict]:
    if not _ia_web_busca_ativa():
        return []
    consulta = str(query or "").strip()
    if not consulta:
        return []
    try:
        hoje = datetime.utcnow().strftime("%Y-%m-%d")
        consulta_news = f"{consulta} when:1d {hoje}"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; JKSistema/1.0; +https://jksistema.local)",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
        }
        resp = requests.get(
            "https://news.google.com/rss/search",
            params={
                "q": consulta_news,
                "hl": "pt-BR",
                "gl": "BR",
                "ceid": "BR:pt-419",
            },
            headers=headers,
            timeout=12,
            verify=False,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "xml")
        resultados = []
        for item in soup.select("item"):
            titulo = (item.title.get_text(" ", strip=True) if item.title else "")
            url = (item.link.get_text(" ", strip=True) if item.link else "")
            pub_date = (item.pubDate.get_text(" ", strip=True) if item.pubDate else "")
            source = (item.source.get_text(" ", strip=True) if item.source else "")
            if not titulo or not url:
                continue
            resultados.append({
                "title": titulo[:220],
                "url": url[:600],
                "snippet": "",
                "source": source[:120],
                "published_at": pub_date[:120],
            })
            if len(resultados) >= max_results:
                break
        return resultados
    except Exception as exc:
        logger.warning(f"[IA WEB] Falha na busca de noticias: {type(exc).__name__}: {exc}")
        return []


def _ia_web_buscar(query: str, max_results: int = 5, *, fast: bool = False) -> list[dict]:
    if not _ia_web_busca_ativa():
        return []
    consulta = str(query or "").strip()
    if not consulta:
        return []
    try:
        resultado_api = _favoritos_busca_externa_chamar_api(
            consulta,
            max_results=max_results,
            timeout_s=4 if fast else 18,
        )
        if isinstance(resultado_api, dict) and resultado_api.get("resultados"):
            return [
                {
                    "title": item.get("titulo") or item.get("title") or "",
                    "url": _ia_web_normalizar_result_url(item.get("url") or item.get("link") or ""),
                    "snippet": item.get("trecho") or item.get("snippet") or "",
                    "provider": resultado_api.get("provider") or "",
                }
                for item in resultado_api.get("resultados") or []
                if isinstance(item, dict)
            ][:max_results]
    except Exception as exc:
        logger.warning(f"[IA WEB] Falha na busca por provedor externo: {type(exc).__name__}: {exc}")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; JKSistema/1.0; +https://jksistema.local)",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
        }
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": consulta},
            headers=headers,
            timeout=4 if fast else 12,
            verify=False,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        resultados = []
        for item in soup.select(".result"):
            link = item.select_one("a.result__a")
            snippet_el = item.select_one(".result__snippet")
            if not link:
                continue
            titulo = link.get_text(" ", strip=True)
            url = _ia_web_normalizar_result_url(link.get("href") or "")
            snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
            if "duckduckgo.com/y.js" in url or "ad_domain=" in url:
                continue
            if not titulo or not url:
                continue
            resultados.append({
                "title": titulo[:180],
                "url": url[:600],
                "snippet": snippet[:360],
            })
            if len(resultados) >= max_results:
                break
        if resultados:
            return resultados
    except Exception as exc:
        logger.warning(f"[IA WEB] Falha na busca web: {type(exc).__name__}: {exc}")
    try:
        if fast:
            raise RuntimeError("fallback_lite_omitido_no_modo_rapido")
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; JKSistema/1.0; +https://jksistema.local)",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
        }
        resp = requests.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": consulta},
            headers=headers,
            timeout=12,
            verify=False,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        resultados = []
        for link in soup.select("a.result-link, td.result-link a, a[href]"):
            titulo = link.get_text(" ", strip=True)
            href = str(link.get("href") or "").strip()
            if href.startswith("/"):
                href = "https://duckduckgo.com" + href
            url = _ia_web_normalizar_result_url(href)
            if not titulo or not url or "duckduckgo.com/y.js" in url or "ad_domain=" in url:
                continue
            if "duckduckgo.com" in (urlparse(url).netloc or "") and not urlparse(url).path.startswith("/l/"):
                continue
            snippet = ""
            row = link.find_parent("tr")
            if row:
                prox = row.find_next_sibling("tr")
                if prox:
                    snippet = prox.get_text(" ", strip=True)
            resultados.append({
                "title": titulo[:180],
                "url": url[:600],
                "snippet": snippet[:360],
                "provider": "duckduckgo_lite",
            })
            if len(resultados) >= max_results:
                break
        if resultados:
            return resultados
    except Exception as exc:
        if not fast:
            logger.warning(f"[IA WEB] Falha na busca web lite: {type(exc).__name__}: {exc}")
    try:
        jina_url = "https://r.jina.ai/http://https://duckduckgo.com/html/?" + urlencode({"q": consulta})
        resp = requests.get(
            jina_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; JKSistema/1.0; +https://jksistema.local)"},
            timeout=5 if fast else 18,
            verify=False,
        )
        resp.raise_for_status()
        return _ia_web_extrair_resultados_jina_duckduckgo(resp.text or "", max_results=max_results)
    except Exception as exc:
        logger.warning(f"[IA WEB] Falha na busca web jina: {type(exc).__name__}: {exc}")
        return []


def _ia_web_buscar_cached(
    query: str,
    client_id: Optional[str] = None,
    max_results: int = 5,
    *,
    fast: bool = False,
) -> list[dict]:
    if not client_id:
        if _ia_chat_pede_noticias(query):
            return _ia_web_buscar_noticias(query, max_results=max_results)
        return _ia_web_buscar(query, max_results=max_results, fast=True) if fast else _ia_web_buscar(query, max_results=max_results)
    tipo = "news" if _ia_chat_pede_noticias(query) else "web"
    chave = _ia_web_cache_key(query, tipo=tipo)
    with IA_WEB_CACHE_LOCK:
        cache = _ia_web_ler_cache(client_id)
        consultas = cache.setdefault("consultas", {})
        item = consultas.get(chave)
        if item and _ia_web_cache_valido(item):
            resultados = item.get("resultados")
            if isinstance(resultados, list) and resultados:
                return resultados
        if item and isinstance(item.get("resultados"), list) and not item.get("resultados"):
            # Defesa adicional para caches negativos legados, mesmo que um
            # validador customizado os classifique incorretamente como validos.
            consultas.pop(chave, None)
            _ia_web_salvar_cache(client_id, cache)

    if tipo == "news":
        resultados = _ia_web_buscar_noticias(query, max_results=max_results)
    else:
        resultados = _ia_web_buscar(query, max_results=max_results, fast=True) if fast else _ia_web_buscar(query, max_results=max_results)
    if tipo == "news" and not resultados:
        resultados = _ia_web_buscar(query, max_results=max_results, fast=True) if fast else _ia_web_buscar(query, max_results=max_results)
    with IA_WEB_CACHE_LOCK:
        cache = _ia_web_ler_cache(client_id)
        consultas = cache.setdefault("consultas", {})
        if resultados:
            consultas[chave] = {
                "query": str(query or "").strip(),
                "tipo": tipo,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "resultados": resultados,
            }
        else:
            # Uma lista vazia representa falha de pesquisa, nao um resultado
            # reutilizavel. Remove inclusive entradas negativas de versoes
            # anteriores para que a proxima tentativa alcance os fallbacks.
            consultas.pop(chave, None)
        _ia_web_salvar_cache(client_id, cache)
    return resultados


def _ia_web_contexto(query: str, client_id: Optional[str] = None) -> str:
    resultados = _ia_web_buscar_cached(query, client_id=client_id)
    if not resultados:
        return ""
    linhas = []
    for idx, item in enumerate(resultados, start=1):
        linha = f"{idx}. {item.get('title')}\nURL: {item.get('url')}"
        if item.get("provider"):
            linha += f"\nProvedor: {item.get('provider')}"
        if item.get("source"):
            linha += f"\nFonte: {item.get('source')}"
        if item.get("published_at"):
            linha += f"\nData: {item.get('published_at')}"
        linha += f"\nResumo: {item.get('snippet') or 'Sem resumo disponÃƒÂ­vel.'}"
        linhas.append(linha)
    titulo = "Resultados de noticias recentes:" if _ia_chat_pede_noticias(query) else "Resultados de busca web relevantes:"
    return titulo + "\n" + "\n\n".join(linhas)


def _ia_chat_eh_saudacao_curta(mensagem: str) -> bool:
    texto = str(mensagem or "").strip().lower()
    if not texto:
        return False
    texto = re.sub(r"[!?.,;:()\[\]{}\-_/\\\"']+", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    saudacoes = {
        "oi",
        "ola",
        "e ai",
        "bom dia",
        "boa tarde",
        "boa noite",
        "opa",
        "hello",
    }
    return texto in saudacoes


def _ia_chat_resposta_saudacao(payload: IAChatRequest) -> str:
    contexto = payload.context if isinstance(payload.context, dict) else {}
    usuario_atual = contexto.get("usuario_atual") if isinstance(contexto.get("usuario_atual"), dict) else {}
    nome = str(usuario_atual.get("nome") or contexto.get("nome_usuario") or "").strip()
    primeiro_nome = nome.split()[0] if nome else ""
    abertura = f"Oi, {primeiro_nome}!" if primeiro_nome else "Oi!"
    return (
        f"{abertura} Que bom te ver por aqui. Me diga o que voce quer analisar agora "
        "(ex.: vendas do periodo, devolucoes, SKUs parados ou comparacao entre lojas)."
    )


def _ia_chat_eh_pedido_rapido_sidebar(
    mensagem: str,
    contexto: Optional[dict] = None,
    anexos: Optional[list] = None,
) -> bool:
    if anexos:
        return False
    texto_raw = str(mensagem or "").strip()
    if not texto_raw:
        return False
    texto = _normalizar_texto(texto_raw)
    palavras = re.findall(r"[A-Z0-9]+", texto)
    if len(texto_raw) > 220 or len(palavras) > 32:
        return False
    if re.search(r"\bMLB\d{5,}\b|\bSKU\b|R\$", texto):
        return False

    termos_operacionais = (
        "VENDA", "VENDAS", "PEDIDO", "PEDIDOS", "ESTOQUE", "SKU", "SKUS",
        "DEVOLUCAO", "DEVOLUCOES", "NOTA", "NFE", "NF E", "PRODUTO", "PRODUTOS",
        "PERIODO", "TELA", "LISTA", "RANKING", "LOJA", "LOJAS", "UNIDADE",
        "FATURAMENTO", "VALOR BRUTO", "ITENS VENDIDOS", "ITENS DEVOLVIDOS",
        "MARGEM", "CUSTO", "IMPOSTO", "BLING", "MERCADO LIVRE", "ANUNCIO",
        "ANUNCIOS", "FAVORITO", "FAVORITOS", "CAMPANHA", "PROMOCAO",
        "SINCRONIZAR", "SINCRONIZACAO", "ERRO", "PROBLEMA", "RELATORIO",
        "PLANILHA", "CALCULE", "CALCULAR", "ANALISE", "ANALISAR", "COMPARE",
        "COMPARAR", "BUSQUE", "BUSCAR", "PESQUISE", "PESQUISAR", "LISTE",
        "LISTAR", "MOSTRE", "MOSTRAR", "VERIFIQUE", "VERIFICAR",
    )
    if any(termo in texto for termo in termos_operacionais):
        return False

    termos_rapidos = (
        "OI", "OLA", "BOM DIA", "BOA TARDE", "BOA NOITE", "OBRIGADO", "OBRIGADA",
        "VALEU", "REPITA", "REPETE", "REPETIR", "FALE DE NOVO", "DIGA DE NOVO",
        "QUE DIA", "QUAL DIA", "QUAL DATA", "DATA DE HOJE", "HOJE E", "HOJE",
        "AGORA", "QUE HORAS", "HORARIO",
    )
    if any(termo in texto for termo in termos_rapidos):
        return True
    return len(palavras) <= 8


def _ia_chat_usa_contexto_tela(mensagem: str, anexos: Optional[list[dict]] = None) -> bool:
    texto = str(mensagem or "").strip().lower()
    if not texto:
        return bool(anexos)

    termos_contexto = (
        "venda", "vendas", "pedido", "pedidos", "estoque", "sku", "skus",
        "devolucao", "devolucoes", "nota", "nf", "produto", "produtos",
        "periodo", "periodo atual", "tela", "lista", "ranking", "loja",
        "unidade", "comparar", "comparacao", "faturamento", "valor bruto",
        "itens vendidos", "itens devolvidos", "ÃƒÂºltima venda", "ultima venda",
    )
    if any(termo in texto for termo in termos_contexto):
        return True

    indicios_contexto = (
        "aqui", "nesta", "nesse", "nessa", "do que aparece", "da tela",
        "do periodo", "do perÃƒÂ­odo", "na tabela", "na lista", "nos cards",
        "no painel", "comparar isso", "isso aqui",
    )
    if any(frase in texto for frase in indicios_contexto):
        return True

    return bool(anexos)


def _ia_chat_deve_anexar_estoque_contexto(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    if not texto:
        return False
    termos = (
        "ESTOQUE", "SALDO", "FULL", "RUPTURA", "PARADO", "SEM VENDA",
        "SKU", "PRODUTO", "CADASTRO", "MARGEM", "CUSTO",
    )
    return any(termo in texto for termo in termos)

configure_ia_web_runtime()

__all__ = [
    name
    for name in globals()
    if (
        (name.startswith("_") and not name.startswith("__"))
        or name.startswith("IA_")
        or name.startswith("FAVORITOS_PESQUISAS_IA")
        or name.startswith("GEMINI_")
        or name.startswith("VERTEX_")
        or name.startswith("ia_")
        or name == "servir_imagem_ia"
    )
]
