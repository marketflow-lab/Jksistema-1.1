"""Feature-gated, read-only financial comparison screen and API."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.services.codex.console.security import require_full_admin
from backend.services.mercadolivre_cotacao_comparacao import (
    captura_comparacao_habilitada,
    interface_comparacao_habilitada,
    permitir_recotacao_readonly,
    permitir_recotacao_readonly_tenant,
    resumo_comparacao,
)
from backend.services.mercadolivre_cotacao_readonly import (
    cotar_preco_candidato_readonly,
)


_UI_DIR = Path(__file__).resolve().parents[1] / "ui" / "financial_comparison"
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
_READONLY_QUOTE_CONCURRENCY = threading.BoundedSemaphore(4)
_MAX_DATA_API_BODY_BYTES = 4096


def _validate_store_name(value: str) -> str:
    if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("invalid store name")
    return value


class FinancialComparisonSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loja: str = Field(min_length=1, max_length=180)
    limit: int = Field(default=100, ge=1, le=250)

    _store_name = field_validator("loja")(_validate_store_name)


class FinancialCandidateQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loja: str = Field(min_length=1, max_length=180)
    item_id: str = Field(min_length=4, max_length=32)
    preco: Decimal = Field(
        gt=0,
        le=Decimal("9999999999.99"),
        max_digits=12,
        decimal_places=2,
        allow_inf_nan=False,
    )
    custo: Decimal = Field(
        ge=0,
        le=Decimal("9999999999.9999"),
        max_digits=14,
        decimal_places=4,
        allow_inf_nan=False,
    )
    imposto_percentual: Decimal = Field(
        ge=0,
        le=100,
        max_digits=7,
        decimal_places=4,
        allow_inf_nan=False,
    )

    _store_name = field_validator("loja")(_validate_store_name)


async def _read_bounded_json_model(
    request: Request,
    model_type: type[BaseModel],
) -> BaseModel:
    content_type = str(request.headers.get("content-type") or "").split(";", 1)[0]
    if content_type.strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="Corpo JSON obrigatorio.")
    content_length = str(request.headers.get("content-length") or "").strip()
    if content_length:
        try:
            if int(content_length) > _MAX_DATA_API_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Corpo da requisicao muito grande.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length invalido.") from exc
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_DATA_API_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Corpo da requisicao muito grande.")
        body.extend(chunk)
    try:
        return model_type.model_validate_json(bytes(body))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Corpo da requisicao invalido.") from exc


@dataclass(frozen=True)
class FinancialComparisonRouterConfig:
    load_ml_stores: Callable[[str], list[dict]]
    ml_api_request: Callable[..., Any]
    build_shipping_context: Callable[[dict, Any], dict]


def _resolve_ml_store_exact(
    config: FinancialComparisonRouterConfig,
    client_id: str,
    requested_name: str,
) -> tuple[str, dict[str, Any]]:
    stores = config.load_ml_stores(client_id)
    if not isinstance(stores, list):
        raise ValueError("invalid store collection")
    matches = []
    for store in stores:
        if not isinstance(store, dict):
            continue
        canonical_name = str(store.get("nome") or "").strip()
        if canonical_name == requested_name:
            matches.append((canonical_name, store))
    if len(matches) != 1:
        raise LookupError("store must match one canonical name exactly")
    canonical_name, store = matches[0]
    integrations = store.get("integracoes") if isinstance(store.get("integracoes"), dict) else {}
    raw_cfg = integrations.get("mercadolivre") if isinstance(integrations, dict) else {}
    if not isinstance(raw_cfg, dict):
        raise LookupError("store has no Mercado Livre configuration")
    cfg = dict(raw_cfg)
    cfg["app_id"] = cfg.get("app_id") or cfg.get("id") or cfg.get("client_id")
    cfg["client_secret"] = cfg.get("client_secret") or cfg.get("secret")
    if not str(cfg.get("access_token") or "").strip():
        raise LookupError("store has no Mercado Livre token")
    return canonical_name, cfg


def _ui_file(filename: str, media_type: str) -> FileResponse:
    path = _UI_DIR / filename
    if not path.is_file():
        raise HTTPException(
            status_code=503,
            detail="Interface de comparacao financeira indisponivel.",
        )
    response = FileResponse(path, media_type=media_type)
    for key, value in _NO_STORE_HEADERS.items():
        response.headers[key] = value
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'none'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'self'"
    )
    return response


async def financial_comparison_page() -> FileResponse:
    """Serve an inert shell; all financial data still requires full-admin API auth."""

    return _ui_file("index.html", "text/html; charset=utf-8")


async def financial_comparison_script() -> FileResponse:
    return _ui_file("app.js", "application/javascript; charset=utf-8")


async def financial_comparison_summary(
    config: FinancialComparisonRouterConfig | None,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    session = require_full_admin(request, authorization)
    if not captura_comparacao_habilitada():
        raise HTTPException(
            status_code=503,
            detail="Captura de comparacao financeira esta desabilitada.",
        )
    client_id = str(session.get("client_id") or "").strip()
    if not client_id:
        raise HTTPException(status_code=401, detail="Sessao sem tenant valido.")
    payload = await _read_bounded_json_model(
        request,
        FinancialComparisonSummaryRequest,
    )
    if config is None:
        raise HTTPException(
            status_code=503,
            detail="Resolvedor de lojas nao configurado.",
        )
    try:
        canonical_store, _cfg = _resolve_ml_store_exact(
            config,
            client_id,
            payload.loja,
        )
        result = resumo_comparacao(
            client_id=client_id,
            loja=canonical_store,
            limit=payload.limit,
        )
    except (LookupError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=404,
            detail="Loja Mercado Livre nao encontrada.",
        ) from exc
    return JSONResponse(result, headers=dict(_NO_STORE_HEADERS))


async def financial_comparison_candidate_quote(
    config: FinancialComparisonRouterConfig | None,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Freshly re-quote one candidate price without changing Mercado Livre."""

    session = require_full_admin(request, authorization)
    if config is None:
        raise HTTPException(
            status_code=503,
            detail="Resolvedor read-only nao configurado.",
        )
    client_id = str(session.get("client_id") or "").strip()
    if not client_id:
        raise HTTPException(status_code=401, detail="Sessao sem tenant valido.")
    payload = await _read_bounded_json_model(
        request,
        FinancialCandidateQuoteRequest,
    )
    normalized_item_id = str(payload.item_id or "").strip().upper()
    if not re.fullmatch(r"MLB\d{3,20}", normalized_item_id):
        raise HTTPException(status_code=400, detail="Anuncio Mercado Livre invalido.")
    if not permitir_recotacao_readonly_tenant(client_id=client_id):
        raise HTTPException(
            status_code=429,
            detail="Limite temporario de recotacoes atingido para este tenant.",
        )
    if not _READONLY_QUOTE_CONCURRENCY.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="Recotacoes simultaneas em andamento. Tente novamente em instantes.",
        )
    try:
        try:
            canonical_store, cfg = _resolve_ml_store_exact(
                config,
                client_id,
                payload.loja,
            )
        except (LookupError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=404,
                detail="Loja Mercado Livre nao encontrada.",
            ) from exc
        if not permitir_recotacao_readonly(
            client_id=client_id,
            loja=canonical_store,
        ):
            raise HTTPException(
                status_code=429,
                detail="Limite temporario de recotacoes atingido para esta loja.",
            )
        if not str(cfg.get("user_id") or "").strip():
            raise HTTPException(
                status_code=409,
                detail="A loja ainda nao possui identidade ML confirmada para recotacao.",
            )
        response, cfg = config.ml_api_request(
            client_id,
            canonical_store,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/items/{normalized_item_id}",
            timeout=12,
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code == 404:
            raise HTTPException(status_code=404, detail="Anuncio nao encontrado.")
        if status_code != 200:
            raise HTTPException(
                status_code=502,
                detail="Mercado Livre nao respondeu a consulta do anuncio.",
            )
        item = response.json()
        if not isinstance(item, dict):
            raise HTTPException(status_code=502, detail="Resposta invalida do Mercado Livre.")
        seller_expected = str((cfg or {}).get("user_id") or "").strip()
        seller_observed = str(item.get("seller_id") or "").strip()
        if not seller_observed or seller_observed != seller_expected:
            raise HTTPException(status_code=404, detail="Anuncio nao pertence a esta loja.")
        result, _cfg = cotar_preco_candidato_readonly(
            client_id=client_id,
            loja=canonical_store,
            cfg=cfg,
            item=item,
            preco_efetivo=payload.preco,
            custo_produto=payload.custo,
            aliquota_imposto=payload.imposto_percentual / Decimal("100"),
            construir_contexto_frete=config.build_shipping_context,
            request_fn=config.ml_api_request,
        )
    except HTTPException:
        raise
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Dados financeiros invalidos.") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Nao foi possivel obter uma cotacao exata agora.",
        ) from exc
    finally:
        _READONLY_QUOTE_CONCURRENCY.release()
    return JSONResponse(
        {"mode": "readonly_candidate", **result},
        headers=dict(_NO_STORE_HEADERS),
    )


def create_financial_comparison_router(
    config: FinancialComparisonRouterConfig | None = None,
) -> APIRouter:
    """Return an empty router unless the isolated UI flag is explicitly enabled."""

    router = APIRouter(tags=["financial-comparison-internal"])
    if not interface_comparacao_habilitada():
        return router
    router.add_api_route(
        "/internal/financial-comparison",
        financial_comparison_page,
        methods=["GET"],
        name="financial_comparison_page",
        include_in_schema=False,
    )
    router.add_api_route(
        "/internal/financial-comparison/app.js",
        financial_comparison_script,
        methods=["GET"],
        name="financial_comparison_script",
        include_in_schema=False,
    )
    async def _summary(
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ) -> JSONResponse:
        return await financial_comparison_summary(
            config,
            request,
            authorization,
        )

    router.add_api_route(
        "/api/internal/financial-comparison/summary",
        _summary,
        methods=["POST"],
        name="financial_comparison_summary",
    )

    async def _candidate_quote(
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ) -> JSONResponse:
        return await financial_comparison_candidate_quote(
            config,
            request,
            authorization,
        )

    router.add_api_route(
        "/api/internal/financial-comparison/candidate-quote",
        _candidate_quote,
        methods=["POST"],
        name="financial_comparison_candidate_quote",
    )
    return router


__all__ = [
    "FinancialComparisonRouterConfig",
    "create_financial_comparison_router",
    "financial_comparison_candidate_quote",
    "financial_comparison_page",
    "financial_comparison_script",
    "financial_comparison_summary",
]
