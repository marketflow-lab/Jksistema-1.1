"""Ranking and bounded excerpt analysis for public technical sources."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .runtime import (
    _favoritos_normalizar_sem_acentos,
    _normalizar_texto,
    _perguntas_ia_v2_grounding_texto,
)

def _perguntas_ia_v2_prioridade_fonte_web(item: dict, url: str) -> tuple[int, str]:
    try:
        parsed = urlparse(str(url or ""))
        host = str(parsed.hostname or "").lower()
        caminho = str(parsed.path or "").lower()
    except Exception:
        host = ""
        caminho = ""
    texto = _favoritos_normalizar_sem_acentos(" ".join([
        str(item.get("title") or ""),
        str(item.get("provider") or ""),
        str(item.get("source") or ""),
        str(url or ""),
    ]))
    marketplace = any(
        dominio in texto
        for dominio in ("mercadolivre", "amazon.", "shopee", "aliexpress", "magazineluiza")
    )
    espelho_manual = any(
        dominio in host
        for dominio in ("manualslib.", "manualzz.", "scribd.", "manualpdf.", "manuals.plus")
    )
    host_documentacao = any(
        host.startswith(prefixo)
        for prefixo in ("manual.", "manuals.", "support.", "docs.", "service.", "help.")
    )
    fonte_institucional = host.endswith(".gov") or ".gov." in host or host.endswith(".edu") or ".edu." in host
    oficial = any(
        termo in texto
        for termo in ("manual", "fabricante", "manufacturer", "official", "oficial", "support.", ".gov", "oem")
    )
    if marketplace:
        prioridade = 0
    elif espelho_manual:
        prioridade = 1
    elif host_documentacao or fonte_institucional:
        prioridade = 6
    elif caminho.endswith(".pdf") and oficial:
        prioridade = 5
    elif oficial:
        prioridade = 4
    else:
        prioridade = 2
    return (prioridade, str(url or ""))


_PERGUNTAS_IA_WEB_EXCLUDED_SEGMENTS = frozenset({
    "help", "contact", "contacts", "contact-us", "policy", "policies",
    "privacy", "login", "log-in", "signin", "sign-in", "sales", "sale",
})
_PERGUNTAS_IA_WEB_RANK_STOPWORDS = frozenset({
    "A", "AS", "COM", "DA", "DAS", "DE", "DO", "DOS", "E", "EM", "FICHA",
    "FOR", "MANUAL", "O", "OEM", "OS", "PARA", "POR", "PRODUTO", "TECNICA", "TECHNICAL",
})


def _perguntas_ia_v2_fonte_web_excluida(item: dict, url: str) -> bool:
    try:
        parsed = urlparse(str(url or ""))
        coordinates = [
            part.casefold()
            for part in [*(parsed.hostname or "").split("."), *(parsed.path or "").split("/")]
            if part
        ]
    except Exception:
        coordinates = []
    if any(part in _PERGUNTAS_IA_WEB_EXCLUDED_SEGMENTS for part in coordinates):
        return True
    title = _favoritos_normalizar_sem_acentos(str(item.get("title") or "")).casefold()
    return bool(re.search(
        r"\b(?:help|ajuda|contact(?: us)?|contato|privacy policy|politica de privacidade|"
        r"login|sign[ -]?in|sales(?: department)?|departamento de vendas)\b",
        title,
        flags=re.IGNORECASE,
    ))


def _perguntas_ia_v2_rank_fonte_web(item: dict, url: str, query: str) -> tuple:
    searchable = " ".join([
        str(item.get("title") or ""),
        str(item.get("snippet") or ""),
        str(item.get("provider") or ""),
        str(item.get("source") or ""),
        str(url or ""),
    ])
    normalized_searchable = _normalizar_texto(searchable)
    compact_searchable = re.sub(r"[^A-Z0-9]", "", normalized_searchable)
    normalized_query = _normalizar_texto(query)
    codes: list[str] = []
    for token in re.findall(r"[A-Z0-9]+(?:[./-][A-Z0-9]+)+|[A-Z0-9]{5,}", normalized_query):
        compact = re.sub(r"[^A-Z0-9]", "", token)
        if (
            len(compact) >= 5
            and any(char.isalpha() for char in compact)
            and any(char.isdigit() for char in compact)
            and compact not in codes
        ):
            codes.append(compact)
    exact_codes = sum(code in compact_searchable for code in codes)
    tokens = [
        token
        for token in re.findall(r"[A-Z0-9]{3,}", normalized_query)
        if token not in _PERGUNTAS_IA_WEB_RANK_STOPWORDS
        and not re.fullmatch(r"(?:19|20)\d{2}", token)
    ]
    matched_tokens = sum(token in normalized_searchable for token in dict.fromkeys(tokens))
    exact_phrase = int(bool(normalized_query and normalized_query in normalized_searchable))
    coordinate = _favoritos_normalizar_sem_acentos(str(url or "")).casefold()
    technical_coordinate = int(any(
        marker in coordinate
        for marker in (".pdf", "/diagram", "/catalog", "/parts", "/manual", "/service", "/technical")
    ))
    source_priority = _perguntas_ia_v2_prioridade_fonte_web(item, url)[0]
    return (
        exact_codes,
        exact_phrase,
        technical_coordinate,
        source_priority,
        matched_tokens,
        str(url or ""),
    )

def _perguntas_ia_v2_recortes_fonte_tecnica(texto: str, query: str, max_chars: int = 1200) -> str:
    texto = str(texto or "")
    if not texto:
        return ""
    termos_query = {
        termo
        for termo in re.findall(r"[a-z0-9]{4,}", _favoritos_normalizar_sem_acentos(query))
        if termo not in {
            "manual", "fabricante", "oficial", "official", "interface", "especificacoes",
            "compatibilidade", "preparacao", "produto", "adaptador", "suporte",
        }
    }
    sinais_interface = {
        "navigator", "navigation", "navegacao", "navegacion", "preparation", "preparacao",
        "preparacion", "preinstalacao", "preinstalacion", "mount", "base",
        "connector", "conector", "conexao", "socket", "encaixe", "interface", "adapter", "adaptador",
        "engate", "engates", "abracadeira", "mangueira", "mangueiras",
        "eixo", "haste", "estria", "estrias", "rosca", "diametro", "flange", "furacao",
        "fixacao", "medida", "dimensao", "tensao", "voltagem", "frequencia", "potencia",
        "pressao", "hdmi", "displayport", "wifi", "bluetooth", "protocolo",
    }
    sinais_decisao = {
        "suitable", "compatible", "compatível", "compativel", "adequada", "adequado", "fits",
        "fit", "later", "posterior", "onward", "requires", "requer", "only", "somente", "designed",
        "apta", "apto", "admite", "aceita", "desde", "partir",
    }
    candidatos: list[tuple[int, int, str]] = []
    vistos: set[str] = set()
    for posicao, linha_original in enumerate(texto.splitlines()):
        linha = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", str(linha_original or ""))
        linha = re.sub(r"^[#>*`\-\s]+", "", linha)
        # Leitores de PDF preservam hifenizacao de fim de linha, como
        # Navi-gator, prepara-tion e na-vegacion. Reunir a palavra evita
        # esconder justamente o nome da interface pesquisada.
        linha = re.sub(r"(?<=[A-Za-zÀ-ÿ])-(?=[A-Za-zÀ-ÿ])", "", linha)
        linha = re.sub(r"\s+", " ", linha).strip()
        if len(linha) < 18 or len(linha) > 900:
            continue
        normalizada = _favoritos_normalizar_sem_acentos(linha)
        if not normalizada or normalizada in vistos:
            continue
        vistos.add(normalizada)
        palavras = set(re.findall(r"[a-z0-9]{3,}", normalizada))
        hits_query = len(termos_query & palavras)
        hits_interface = len(sinais_interface & palavras)
        hits_decisao = len(sinais_decisao & palavras)
        if not hits_interface or not (hits_query or hits_decisao):
            continue
        pontuacao = (hits_decisao * 6) + (hits_interface * 3) + (hits_query * 2)
        candidatos.append((pontuacao, -posicao, linha))
    candidatos.sort(reverse=True)
    recortes: list[str] = []
    total = 0
    for _, _, linha in candidatos:
        acrescimo = len(linha) + (1 if recortes else 0)
        if total + acrescimo > max_chars:
            continue
        recortes.append(linha)
        total += acrescimo
        if len(recortes) >= 5:
            break
    return " ".join(recortes)

def _perguntas_ia_v2_recorte_confirma_interface(texto: str) -> bool:
    normalizado = _perguntas_ia_v2_grounding_texto(texto)
    interfaces = (
        "navigator", "navigation", "navegacao", "navegacion", "preparation", "preparacao",
        "preparacion", "preinstalacao", "preinstalacion", "mount", "base", "conector", "connector",
        "conexao", "engate", "engates", "abracadeira", "mangueira", "mangueiras",
        "encaixe", "interface", "eixo", "haste", "estria", "estrias", "rosca", "diametro",
        "flange", "furacao", "fixacao", "medida", "dimensao", "tensao", "voltagem",
        "frequencia", "potencia", "pressao", "hdmi", "displayport", "wifi", "bluetooth", "protocolo",
    )
    decisoes = (
        "suitable", "compatible", "compativel", "adequada", "adequado", "fits", "fit", "later",
        "posterior", "onward", "apta", "apto", "admite", "aceita", "suporta", "desde", "a partir",
        "nao compativel", "incompativel", "does not fit", "nao encaixa",
    )
    return any(termo in normalizado for termo in interfaces) and any(
        termo in normalizado for termo in decisoes
    )
