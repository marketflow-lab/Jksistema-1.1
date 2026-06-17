"""Application lifecycle registration."""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from fastapi import FastAPI


@dataclass(frozen=True)
class StartupEventSpec:
    endpoint_name: str


STARTUP_EVENTS: tuple[StartupEventSpec, ...] = (
    StartupEventSpec("_promo_automacao_iniciar_background"),
    StartupEventSpec("_renovacao_iniciar_agendamento_background"),
    StartupEventSpec("_perguntas_automacao_iniciar_background"),
)


def register_startup_events(app: FastAPI, legacy_module: ModuleType) -> None:
    for spec in STARTUP_EVENTS:
        app.router.add_event_handler("startup", getattr(legacy_module, spec.endpoint_name))
