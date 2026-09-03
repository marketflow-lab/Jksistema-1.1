"""Privacy-safe public-query and technical-plan projections."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

from backend.services.vin_transient import contains_vin_like_identifier

def _perguntas_ia_texto_sem_diacriticos_com_indices(valor: object) -> tuple[str, list[int]]:
    partes: list[str] = []
    indices: list[int] = []
    for indice, char in enumerate(str(valor or "")):
        for decomposed in unicodedata.normalize("NFKD", char):
            if unicodedata.combining(decomposed):
                continue
            for folded in decomposed.casefold():
                partes.append(folded)
                indices.append(indice)
    return "".join(partes), indices


def _perguntas_ia_remover_nome_comprador_texto(valor: object, agent_input: Optional[dict[str, Any]]) -> str:
    entrada = agent_input if isinstance(agent_input, dict) else {}
    pergunta = entrada.get("question") if isinstance(entrada.get("question"), dict) else {}
    nome = re.sub(r"\s+", " ", str(pergunta.get("buyer_name") or "")).strip()
    texto = str(valor or "")
    if len(nome) < 3:
        return texto
    nome_normalizado, _ = _perguntas_ia_texto_sem_diacriticos_com_indices(nome)
    texto_normalizado, indices = _perguntas_ia_texto_sem_diacriticos_com_indices(texto)
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(parte) for parte in nome_normalizado.split()) + r"(?!\w)"
    ranges = [
        (indices[match.start()], indices[match.end() - 1] + 1)
        for match in re.finditer(pattern, texto_normalizado)
        if match.end() > match.start() and indices
    ]
    for start, end in reversed(ranges):
        texto = texto[:start] + " " + texto[end:]
    return texto


def _perguntas_ia_v2_texto_busca_curto(valor: object, max_palavras: int = 14, max_chars: int = 180) -> str:
    texto = re.sub(r"\bMLB[\s_-]*\d{5,}\b", " ", str(valor or ""), flags=re.IGNORECASE)
    texto = re.sub(r"\bSKU\s*[:#-]?\s*[A-Z0-9._/-]+\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"https?://\S+", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"[^0-9A-Za-zÀ-ÿ+./-]+", " ", texto)
    palavras = [parte for parte in texto.split() if parte]
    return " ".join(palavras[:max(1, int(max_palavras or 14))])[:max_chars].strip()


def _perguntas_ia_proteger_token_tecnico(
    match: re.Match[str], technical_tokens: dict[str, str],
) -> str:
    candidate = match.group(0)
    prefix = match.string[max(0, match.start() - 40):match.start()]
    if re.search(
        r"\b(?:telefone|fone|tel\.?|whats(?:app)?|contato)\s*[:#=-]?\s*$",
        prefix,
        flags=re.IGNORECASE,
    ):
        return candidate
    compact = re.sub(r"[^A-Za-z0-9]", "", candidate)
    if len(compact) == 17 and contains_vin_like_identifier(compact):
        return candidate
    marker = f"\ue000{len(technical_tokens)}\ue001"
    technical_tokens[marker] = candidate
    return marker


def _perguntas_ia_proteger_part_number_numerico(
    match: re.Match[str], technical_tokens: dict[str, str],
) -> str:
    """Keep explicit numeric part numbers, but never nearby contact data."""

    candidate = match.group(0)
    surrounding = match.string[
        max(0, match.start() - 40):min(len(match.string), match.end() + 40)
    ]
    if re.search(
        r"\b(?:telefone|fone|tel\.?|whats(?:app)?|contato|cpf|cnpj|cep|"
        r"pedido|order|compra|rastreio|e-?mail)\b",
        surrounding,
        flags=re.IGNORECASE,
    ):
        return candidate
    marker = f"\ue000{len(technical_tokens)}\ue001"
    technical_tokens[marker] = candidate
    return marker


def _perguntas_ia_v2_texto_classificado_busca(
    valor: object,
    max_palavras: int = 14,
    max_chars: int = 180,
) -> str:
    """Sanitize AI-classified or unstructured text before a public search."""

    texto = re.sub(r"\s+", " ", str(valor or "")).strip()
    technical_tokens: dict[str, str] = {}

    # A VIN heuristic must not consume exact part numbers or vehicle/year
    # predicates merely because their combined alphanumerics total 17 chars.
    for technical_pattern in (
        r"(?<![A-Za-z0-9])(?=[A-Za-z0-9./-]{5,48}(?![A-Za-z0-9]))"
        r"(?=[A-Za-z0-9./-]*[A-Za-z])(?=[A-Za-z0-9./-]*\d)"
        r"[A-Za-z0-9]+(?:[./-][A-Za-z0-9]+){2,}(?![A-Za-z0-9])",
        r"\b\d+(?:[.,]\d+)\s+(?:(?:19|20)\d{2}(?:\s+|$)){1,5}",
        r"\b(?:(?:19|20)\d{2}(?:\s+|$)){2,5}",
    ):
        texto = re.sub(
            technical_pattern,
            lambda match: _perguntas_ia_proteger_token_tecnico(match, technical_tokens),
            texto,
            flags=re.IGNORECASE,
        )
    texto = re.sub(
        r"(?<![A-Za-z0-9])(?:"
        r"c[oó]d(?:igo)?(?:\s+(?:oem|original|da\s+pe[cç]a|do\s+produto))?"
        r"|oem|refer[eê]ncia|part(?:\s+number)?|p\s*/\s*n"
        r")\s*[:#=-]?\s*\d{7,15}(?![A-Za-z0-9])",
        lambda match: _perguntas_ia_proteger_part_number_numerico(match, technical_tokens),
        texto,
        flags=re.IGNORECASE,
    )
    texto = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        " ", texto, flags=re.IGNORECASE,
    )
    texto = re.sub(
        r"\b(?:nome(?:\s+do\s+comprador)?|cliente|comprador)\b"
        r"[^;|.!?]{0,160}(?:[;|.!?]|$)", " ", texto, flags=re.IGNORECASE,
    )
    texto = re.sub(
        r"\bde\s+[A-ZÀ-Ý][a-zà-ÿ]{1,30}\s+[A-ZÀ-Ý][a-zà-ÿ]{1,30}"
        r"(?=\s+(?i:vin|chassi|placa)\b)", " ", texto,
    )
    texto = re.sub(
        r"\b(?:chassi|vin)\b(?=[^;|.!?]{0,80}\d)[^;|.!?]{0,100}(?:[;|.!?]|$)",
        " ", texto, flags=re.IGNORECASE,
    )
    texto = re.sub(
        r"(?<![A-Z0-9])(?=(?:[A-HJ-NPR-Z0-9][ -]?){0,16}\d)"
        r"(?=(?:[A-HJ-NPR-Z0-9][ -]?){0,16}[A-HJ-NPR-Z])"
        r"(?:[A-HJ-NPR-Z0-9][ -]?){16}[A-HJ-NPR-Z0-9](?![A-Z0-9])",
        " ", texto, flags=re.IGNORECASE,
    )
    texto = re.sub(
        r"\bplaca\s*[:#-]?\s*[A-Z]{3}[- ]?(?:\d{4}|\d[A-Z]\d{2})\b",
        " ", texto, flags=re.IGNORECASE,
    )
    texto = re.sub(r"\b[A-Z]{3}\d[A-Z]\d{2}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\bCEP\s*[:#-]?\s*\d{5}-?\d{3}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(
        r"(?<!\w)(?:endere[cç]o(?:\s+(?:de\s+)?entrega)?|rua|avenida|av\.?|travessa|alameda|estrada|rodovia|"
        r"r\.(?=\s+[A-Za-zÀ-ÿ]{2,}))(?=\s|[:#-])"
        r"(?!\s*[:#-]?\s*(?:i2c|0x[0-9a-f]+|ip|mem[oó]ria)\b)"
        r"\s*[:#-]?\s*[^;|.!?]{0,160}(?:[;|.!?]|$)",
        " ", texto, flags=re.IGNORECASE,
    )
    texto = re.sub(
        r"\b(?:telefone|fone|tel\.?|whats(?:app)?|contato)\s*"
        r"(?:(?:n[uú]mero|n[ºo.]?)\s*)?[:#=-]?\s*"
        r"(?:\+?\d[\d\s()./-]{5,}\d)\b",
        " ", texto, flags=re.IGNORECASE,
    )
    texto = re.sub(
        r"\b(?:pedido|order|compra)\s*"
        r"(?:(?:n[uú]mero|n[ºo.]?)\s*)?[:#=-]?\s*"
        r"(?=[A-Z0-9._/-]*\d)[A-Z0-9][A-Z0-9._/-]{5,}\b",
        " ", texto, flags=re.IGNORECASE,
    )
    texto = re.sub(r"(?<![A-Za-z0-9])(?:\d[\s()./-]?){7,8}\d(?![A-Za-z0-9])", " ", texto)
    texto = re.sub(r"(?<![A-Za-z0-9])\+?\d[\d\s()./-]{8,}\d(?![A-Za-z0-9])", " ", texto)
    texto = re.sub(
        r"\b(?:ignore|ignorar|desconsidere|desconsiderar)\b"
        r"(?=[^;|.!?]{0,120}\b(?:regra|regras|instru[cç][aã]o|instru[cç][oõ]es|prompt|pol[ií]tica|sistema)\b)"
        r"[^;|.!?]{0,160}(?:[;|.!?]|$)", " ", texto, flags=re.IGNORECASE,
    )
    texto = re.sub(
        r"\b(?:revele|revelar|exiba|mostrar|vaze|vazar|troque|mude|altere)\b"
        r"(?=[^;|.!?]{0,120}\b(?:tenant|loja|segredo|prompt|regra|pol[ií]tica|papel|ferramenta)\b)"
        r"[^;|.!?]{0,160}(?:[;|.!?]|$)", " ", texto, flags=re.IGNORECASE,
    )
    for marker, technical_value in technical_tokens.items():
        texto = texto.replace(marker, technical_value)
    return _perguntas_ia_v2_texto_busca_curto(texto, max_palavras=max_palavras, max_chars=max_chars)


def _perguntas_ia_technical_query_segura(
    valor: object,
    *,
    default_type: str,
) -> dict[str, str]:
    """Project one planner query through the same public-search privacy boundary."""

    raw = valor if isinstance(valor, dict) else {"query": valor}
    query = _perguntas_ia_v2_texto_classificado_busca(
        raw.get("query") or raw.get("text") or raw.get("consulta"),
        max_palavras=36,
        max_chars=260,
    )
    if not query:
        return {}
    query_type = re.sub(
        r"[^a-z0-9_.-]",
        "",
        str(raw.get("type") or raw.get("query_type") or default_type).strip().lower(),
    )[:80]
    return {"type": query_type or default_type, "query": query}


def _perguntas_ia_technical_gap_queries_seguras(valor: object) -> list[dict[str, str]]:
    values = valor if isinstance(valor, list) else []
    safe: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values[:12]:
        projected = _perguntas_ia_technical_query_segura(
            value,
            default_type="technical_gap",
        )
        normalized = projected.get("query", "").casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        safe.append(projected)
    return safe[:8]


def _perguntas_ia_technical_question_plan_seguro(valor: object) -> dict[str, Any]:
    """Keep only planner metadata and VIN/PII-safe technical search queries."""

    raw = valor if isinstance(valor, dict) else {}
    query_values: list[object] = []
    for field in ("queries", "primary_queries", "research_queries"):
        values = raw.get(field)
        if isinstance(values, list):
            query_values.extend(values)
    for subquestion in (raw.get("subquestions") or [])[:12]:
        if not isinstance(subquestion, dict):
            continue
        for field in ("queries", "search_queries", "research_queries"):
            values = subquestion.get(field)
            if isinstance(values, list):
                query_values.extend(values)
    safe_queries: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in query_values[:24]:
        projected = _perguntas_ia_technical_query_segura(
            value,
            default_type="technical_plan",
        )
        normalized = projected.get("query", "").casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        safe_queries.append(projected)
    version = re.sub(r"[^a-z0-9_.-]", "", str(raw.get("version") or "").lower())[:80]
    return {
        **({"version": version} if version else {}),
        "queries": safe_queries[:8],
    }
