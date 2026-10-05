"""Authenticated persistence requests for the desktop market collector."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PesquisaMercadoStatus = Literal[
    "running", "paused", "waiting_login", "cancelled", "completed",
    "completed_partial", "failed",
]


class PesquisaMercadoCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=500, strict=True)
    nome: str = Field(min_length=1, max_length=400, strict=True)
    url: str = Field(min_length=1, max_length=4000, strict=True)


class PesquisaMercadoStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: PesquisaMercadoCategory | None = None


class PesquisaMercadoCheckpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint: dict[str, Any] | None = None
    status: PesquisaMercadoStatus | None = None
    counters: dict[str, int] | None = None
    message: str | None = Field(default=None, max_length=600)


class PesquisaMercadoSellerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller: dict[str, Any]
    anuncios: list[dict[str, Any]]
