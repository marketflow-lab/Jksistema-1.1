"""Sanitized diagnostics and compatibility projections."""

from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


from ml_questions_gemini.compatibility import (
    normalize_comparison_attributes,
    normalize_profile,
    normalize_target_type,
)

_PERGUNTAS_IA_DIAGNOSTICO_MAX_ITENS = 8
_PERGUNTAS_IA_DIAGNOSTICO_MAX_TEXTO = 1200
_PERGUNTAS_IA_DIAGNOSTICO_SEGREDO_RE = re.compile(
    r"(?i)\b(access[_-]?token|refresh[_-]?token|authorization|client[_-]?secret|api[_-]?key)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)


def _perguntas_ia_diagnostico_texto(valor: Any, limite: int = _PERGUNTAS_IA_DIAGNOSTICO_MAX_TEXTO) -> str:
    texto = re.sub(r"\s+", " ", str(valor or "")).strip()
    if not texto:
        return ""
    texto = _PERGUNTAS_IA_DIAGNOSTICO_SEGREDO_RE.sub(r"\1\2[redacted]", texto)
    if texto.lower().startswith(("http://", "https://")):
        try:
            partes = urlsplit(texto)
            texto = urlunsplit((partes.scheme, partes.netloc, partes.path, "", ""))
        except ValueError:
            pass
    return texto[: max(1, int(limite or 1))]


def _perguntas_ia_diagnostico_lista_textos(valor: Any, *, limite_texto: int = 240) -> list[str]:
    itens = valor if isinstance(valor, (list, tuple, set)) else ([valor] if valor not in (None, "") else [])
    resultado: list[str] = []
    for item in itens:
        texto = _perguntas_ia_diagnostico_texto(item, limite_texto)
        if texto and texto not in resultado:
            resultado.append(texto)
        if len(resultado) >= _PERGUNTAS_IA_DIAGNOSTICO_MAX_ITENS:
            break
    return resultado


def _perguntas_ia_diagnostico_query(valor: Any) -> str | dict[str, Any] | None:
    if isinstance(valor, str):
        return _perguntas_ia_diagnostico_texto(valor, 600) or None
    if not isinstance(valor, dict):
        return None
    limites = {
        "type": 80,
        "name": 120,
        "query": 600,
        "status": 80,
        "provider": 120,
        "reason": 400,
    }
    normalizado: dict[str, Any] = {}
    orcamento_texto = 2200
    for chave, limite in limites.items():
        if valor.get(chave) in (None, "") or orcamento_texto <= 0:
            continue
        texto = _perguntas_ia_diagnostico_texto(valor.get(chave), min(limite, orcamento_texto))
        if texto:
            normalizado[chave] = texto
            orcamento_texto -= len(texto)
    return normalizado or None


def _perguntas_ia_diagnostico_source(valor: Any) -> str | dict[str, Any] | None:
    if isinstance(valor, str):
        return _perguntas_ia_diagnostico_texto(valor, 1000) or None
    if not isinstance(valor, dict):
        return None
    limites = {
        "type": 80,
        "source_type": 80,
        "kind": 80,
        "name": 160,
        "title": 300,
        "url": 1000,
        "domain": 240,
        "provider": 120,
        "authority": 120,
        "status": 80,
        "reference": 500,
        "snippet": 1200,
        "excerpt": 1200,
        "fact": 1200,
        "claim": 1200,
        "supports": 500,
        "page": 80,
    }
    normalizado: dict[str, Any] = {}
    orcamento_texto = 2200
    for chave, limite in limites.items():
        if valor.get(chave) in (None, "") or orcamento_texto <= 0:
            continue
        texto = _perguntas_ia_diagnostico_texto(valor.get(chave), min(limite, orcamento_texto))
        if texto:
            normalizado[chave] = texto
            orcamento_texto -= len(texto)
    return normalizado or None


def _perguntas_ia_diagnostico_lista(
    valor: Any,
    normalizar_item,
) -> list[Any]:
    itens = valor if isinstance(valor, (list, tuple)) else ([valor] if valor not in (None, "") else [])
    resultado: list[Any] = []
    vistos: set[str] = set()
    for item in itens:
        normalizado = normalizar_item(item)
        if normalizado in (None, "", {}):
            continue
        chave = repr(normalizado)
        if chave in vistos:
            continue
        vistos.add(chave)
        resultado.append(normalizado)
        if len(resultado) >= _PERGUNTAS_IA_DIAGNOSTICO_MAX_ITENS:
            break
    return resultado


def _perguntas_ia_compatibility_analysis_normalizar(valor: Any) -> dict[str, Any]:
    if not isinstance(valor, dict):
        return {}
    limites = {
        "target_type": 80,
        "target_item": 300,
        "compatibility_profile": 100,
        "product_interface": 800,
        "target_vehicle": 300,
        "target_interface": 800,
        "decision": 80,
        "condition": 1000,
    }
    normalizado: dict[str, Any] = {
        chave: _perguntas_ia_diagnostico_texto(valor.get(chave), limite)
        for chave, limite in limites.items()
        if valor.get(chave) not in (None, "")
    }
    alvo = normalizado.get("target_item") or normalizado.get("target_vehicle")
    if alvo:
        normalizado["target_item"] = alvo
        normalizado["target_vehicle"] = alvo
    if normalizado.get("target_type") or normalizado.get("compatibility_profile"):
        target_type = normalize_target_type(normalizado.get("target_type"), "generic")
        normalizado["target_type"] = target_type
        normalizado["compatibility_profile"] = normalize_profile(
            normalizado.get("compatibility_profile"),
            target_type,
        )
    comparacoes = normalize_comparison_attributes(valor.get("comparison_attributes"))
    if comparacoes:
        normalizado["comparison_attributes"] = [
            {
                "attribute": _perguntas_ia_diagnostico_texto(item.get("attribute"), 120),
                "product_value": _perguntas_ia_diagnostico_texto(item.get("product_value"), 300),
                "target_value": _perguntas_ia_diagnostico_texto(item.get("target_value"), 300),
                "unit": _perguntas_ia_diagnostico_texto(item.get("unit"), 24),
                "result": _perguntas_ia_diagnostico_texto(item.get("result"), 32),
                "decisive": bool(item.get("decisive")),
                "evidence_refs": _perguntas_ia_diagnostico_lista_textos(item.get("evidence_refs"), limite_texto=300),
            }
            for item in comparacoes[:_PERGUNTAS_IA_DIAGNOSTICO_MAX_ITENS]
        ]
    missing_fields = _perguntas_ia_diagnostico_lista_textos(valor.get("missing_fields"), limite_texto=200)
    if missing_fields:
        normalizado["missing_fields"] = missing_fields
    evidencias_brutas = valor.get("evidence")
    if evidencias_brutas in (None, ""):
        evidencias_brutas = valor.get("evidences") or valor.get("evidencias")
    if isinstance(evidencias_brutas, dict):
        evidencias: dict[str, list[Any]] = {}
        evidencias_restantes = _PERGUNTAS_IA_DIAGNOSTICO_MAX_ITENS
        for grupo in ("product", "target", "equivalence"):
            origem_grupo = grupo
            if grupo == "target" and not isinstance(evidencias_brutas.get("target"), list):
                origem_grupo = "target_vehicle"
            itens = _perguntas_ia_diagnostico_lista(
                list(evidencias_brutas.get(origem_grupo) or [])[:evidencias_restantes],
                _perguntas_ia_diagnostico_source,
            )
            if itens:
                evidencias[grupo] = itens
                evidencias_restantes -= len(itens)
            if evidencias_restantes <= 0:
                break
        if evidencias:
            if evidencias.get("target"):
                evidencias["target_vehicle"] = list(evidencias["target"])
            normalizado["evidence"] = evidencias
    else:
        evidencias_lista = _perguntas_ia_diagnostico_lista(
            evidencias_brutas,
            _perguntas_ia_diagnostico_source,
        )
        if evidencias_lista:
            normalizado["evidence"] = evidencias_lista

    queries = _perguntas_ia_diagnostico_lista(valor.get("queries"), _perguntas_ia_diagnostico_query)
    if queries:
        normalizado["queries"] = queries
    sources = _perguntas_ia_diagnostico_lista(valor.get("sources"), _perguntas_ia_diagnostico_source)
    if sources:
        normalizado["sources"] = sources
    try:
        confidence = float(valor.get("confidence"))
    except (TypeError, ValueError):
        confidence = math.nan
    if math.isfinite(confidence) and 0.0 <= confidence <= 1.0:
        normalizado["confidence"] = round(confidence, 4)
    reason = _perguntas_ia_diagnostico_texto(valor.get("reason"), 1000)
    if reason:
        normalizado["reason"] = reason
    return normalizado


def _perguntas_ia_context_pipeline_normalizar(valor: Any) -> list[dict[str, Any]]:
    if not isinstance(valor, (list, tuple)):
        return []
    resultado: list[dict[str, Any]] = []
    for entrada in valor:
        if not isinstance(entrada, dict):
            continue
        normalizada: dict[str, Any] = {}
        if isinstance(entrada.get("step"), int):
            normalizada["step"] = max(0, min(int(entrada["step"]), 999))
        limites = {
            "name": 160,
            "status": 80,
            "reason": 400,
            "query": 600,
            "provider": 120,
        }
        normalizada.update({
            chave: _perguntas_ia_diagnostico_texto(entrada.get(chave), limite)
            for chave, limite in limites.items()
            if entrada.get(chave) not in (None, "")
        })
        if isinstance(entrada.get("source_count"), int):
            normalizada["source_count"] = max(0, min(int(entrada["source_count"]), 999))
        if normalizada:
            resultado.append(normalizada)
        if len(resultado) >= _PERGUNTAS_IA_DIAGNOSTICO_MAX_ITENS:
            break
    return resultado


def _perguntas_ia_diagnostico_resultado(contexto: Any) -> dict[str, Any]:
    if not isinstance(contexto, dict):
        return {}
    diagnostico = contexto.get("diagnostico_ia")
    if not isinstance(diagnostico, list):
        return {}
    for entrada in diagnostico:
        if isinstance(entrada, dict) and isinstance(entrada.get("result"), dict):
            return entrada["result"]
    return {}


def _perguntas_ia_diagnostico_aprovacao(contexto: Any) -> dict[str, Any]:
    """Return the bounded, non-secret V2 diagnostic subset stored with a listing-question review."""
    if not isinstance(contexto, dict):
        return {}
    diagnostico = _perguntas_ia_diagnostico_resultado(contexto)

    def primeiro(*chaves: str):
        for origem in (contexto, diagnostico):
            for chave in chaves:
                if origem.get(chave) not in (None, "", [], {}):
                    return origem[chave]
        return None

    persistido: dict[str, Any] = {}
    compatibility_analysis = _perguntas_ia_compatibility_analysis_normalizar(primeiro("compatibility_analysis"))
    if compatibility_analysis:
        persistido["compatibility_analysis"] = compatibility_analysis

    context_pipeline_bruto = primeiro("context_pipeline", "context_collection_pipeline")
    context_pipeline = _perguntas_ia_context_pipeline_normalizar(context_pipeline_bruto)
    if context_pipeline:
        persistido["context_pipeline"] = context_pipeline

    queries = _perguntas_ia_diagnostico_lista(
        primeiro("queries", "research_queries") or compatibility_analysis.get("queries"),
        _perguntas_ia_diagnostico_query,
    )
    if not queries:
        queries = _perguntas_ia_diagnostico_lista(
            [
                entrada.get("query")
                for entrada in context_pipeline
                if isinstance(entrada, dict) and entrada.get("query")
            ] + [
                query
                for entrada in (context_pipeline_bruto or [])
                if isinstance(entrada, dict)
                for query in (entrada.get("queries") or [])
            ],
            _perguntas_ia_diagnostico_query,
        )
    if queries:
        persistido["queries"] = queries

    sources = _perguntas_ia_diagnostico_lista(
        primeiro("sources", "research_sources") or compatibility_analysis.get("sources"),
        _perguntas_ia_diagnostico_source,
    )
    if not sources:
        sources = _perguntas_ia_diagnostico_lista(
            [
                source
                for entrada in (context_pipeline_bruto or [])
                if isinstance(entrada, dict)
                for source in (entrada.get("sources") or [])
            ],
            _perguntas_ia_diagnostico_source,
        )
    if sources:
        persistido["sources"] = sources

    confianca_bruta = primeiro("confidence", "ia_confidence")
    if confianca_bruta in (None, ""):
        confianca_bruta = compatibility_analysis.get("confidence")
    try:
        confidence = float(confianca_bruta)
    except (TypeError, ValueError):
        confidence = math.nan
    if math.isfinite(confidence) and 0.0 <= confidence <= 1.0:
        persistido["confidence"] = round(confidence, 4)

    reason_bruto = primeiro("reason", "decision_reason")
    if reason_bruto in (None, ""):
        reason_bruto = compatibility_analysis.get("reason")
    reason = _perguntas_ia_diagnostico_texto(reason_bruto, 1000)
    if reason:
        persistido["reason"] = reason
    return persistido
