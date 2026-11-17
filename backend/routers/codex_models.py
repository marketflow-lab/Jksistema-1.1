"""Authenticated, read-only model metadata for the internal chat."""

from typing import Optional
import os

from fastapi import Header, Request

from backend.services import gpt_model_catalog
from backend.services.codex.console.security import require_authenticated


def codex_listar_modelos(request: Request, authorization: Optional[str] = Header(default=None)):
    require_authenticated(request, authorization)
    models = gpt_model_catalog.get_codex_models()
    default = gpt_model_catalog.configured_codex_model()
    return {
        "success": True,
        "models": models,
        "default_model": default,
        "default_reasoning_effort": gpt_model_catalog.normalize_reasoning(
            os.getenv("JK_CODEX_REASONING_EFFORT"), model=default,
            default=gpt_model_catalog.default_reasoning(default),
        ),
        "reasoning_efforts": list(gpt_model_catalog.REASONING_EFFORTS),
    }
