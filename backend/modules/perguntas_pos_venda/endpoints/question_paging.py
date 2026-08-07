"""Bounded Mercado Livre question pagination."""

from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import HTTPException

from backend.modules.perguntas_pos_venda.endpoints.contracts import _PERGUNTAS_AUTOMACAO_MAX_PAGES, _PERGUNTAS_AUTOMACAO_PAGE_LIMIT
from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter

_ml_parse_error_detail = runtime_adapter("_ml_parse_error_detail")


def _perguntas_automacao_question_ids(perguntas: Any) -> list[str]:
    if not isinstance(perguntas, list):
        return []
    question_ids: set[str] = set()
    for pergunta in perguntas:
        if not isinstance(pergunta, dict):
            continue
        question_id = str(pergunta.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", question_id):
            continue
        if str(pergunta.get("status") or "").strip().upper() != "UNANSWERED":
            continue
        if pergunta.get("hold") or pergunta.get("deleted_from_listing") or pergunta.get("suspected_spam"):
            continue
        question_ids.add(question_id)
    return sorted(question_ids)


def _perguntas_automacao_paging_int(payload: dict[str, Any], field: str) -> Optional[int]:
    containers = [payload]
    paging = payload.get("paging")
    if isinstance(paging, dict):
        containers.append(paging)
    for container in containers:
        value = container.get(field)
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None


def _perguntas_automacao_buscar_todas(
    *,
    client_id: str,
    nome_loja: str,
    cfg: dict[str, Any],
    seller_id: str,
    request_fn,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read a stable, complete UNANSWERED snapshot before processing any question."""

    perguntas: list[dict[str, Any]] = []
    question_ids: set[str] = set()
    offset = 0
    total_esperado: Optional[int] = None

    for _page_number in range(_PERGUNTAS_AUTOMACAO_MAX_PAGES):
        resp, cfg = request_fn(
            client_id,
            nome_loja,
            cfg,
            "GET",
            "https://api.mercadolibre.com/questions/search",
            params={
                "seller_id": seller_id,
                "api_version": 4,
                "status": "UNANSWERED",
                "sort_fields": "date_created",
                "sort_types": "DESC",
                "limit": _PERGUNTAS_AUTOMACAO_PAGE_LIMIT,
                "offset": offset,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=_ml_parse_error_detail(resp, "Erro ao buscar perguntas para automacao"),
            )

        try:
            payload = resp.json() or {}
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Resposta invalida ao paginar perguntas.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="Resposta invalida ao paginar perguntas.")

        pagina_raw = payload.get("questions")
        if pagina_raw is None:
            pagina_raw = payload.get("results")
        if pagina_raw is None:
            pagina_raw = []
        if not isinstance(pagina_raw, list) or any(not isinstance(item, dict) for item in pagina_raw):
            raise HTTPException(status_code=502, detail="Colecao invalida ao paginar perguntas.")

        response_offset = _perguntas_automacao_paging_int(payload, "offset")
        if response_offset is not None and response_offset != offset:
            raise HTTPException(status_code=502, detail="Offset divergente ao paginar perguntas.")

        page_total = _perguntas_automacao_paging_int(payload, "total")
        if page_total is not None:
            if total_esperado is None:
                total_esperado = page_total
            elif page_total != total_esperado:
                raise HTTPException(
                    status_code=409,
                    detail="A colecao de perguntas mudou durante a paginacao; nova checagem necessaria.",
                )

        for pergunta in pagina_raw:
            question_id = str(pergunta.get("id") or "").strip()
            if not question_id:
                raise HTTPException(status_code=502, detail="Pergunta sem identificacao durante a paginacao.")
            if question_id in question_ids:
                raise HTTPException(status_code=502, detail="Pagina repetida durante a paginacao de perguntas.")
            question_ids.add(question_id)
            perguntas.append(pergunta)

        page_size = len(pagina_raw)
        next_offset = offset + page_size
        if total_esperado is not None:
            if next_offset > total_esperado:
                raise HTTPException(status_code=502, detail="Total divergente ao paginar perguntas.")
            if next_offset == total_esperado:
                return perguntas, cfg
            if page_size == 0:
                raise HTTPException(status_code=502, detail="Pagina vazia antes do fim das perguntas.")
        else:
            response_limit = _perguntas_automacao_paging_int(payload, "limit")
            effective_limit = response_limit or _PERGUNTAS_AUTOMACAO_PAGE_LIMIT
            if page_size < effective_limit:
                return perguntas, cfg

        if next_offset <= offset:
            raise HTTPException(status_code=502, detail="Paginacao de perguntas sem progresso.")
        offset = next_offset

    raise HTTPException(status_code=502, detail="Limite de seguranca da paginacao de perguntas excedido.")
