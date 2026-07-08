"""IA router definitions."""

from __future__ import annotations

from dataclasses import dataclass
from fastapi import APIRouter


@dataclass(frozen=True)
class LegacyRouteSpec:
    method: str
    path: str
    endpoint_name: str


LEGACY_IA_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("POST", "/api/ia/agente/perguntas/query", "ia_agent_perguntas_query"),
    LegacyRouteSpec("POST", "/api/ia/agent/perguntas/query", "ia_agent_perguntas_query"),
    LegacyRouteSpec("GET", "/api/ia/secrets/status", "ia_secrets_status"),
    LegacyRouteSpec("POST", "/api/ia/secrets/provisionar", "ia_secrets_provisionar"),
    LegacyRouteSpec("POST", "/api/ia/chat", "ia_chat"),
    LegacyRouteSpec("GET", "/api/ia/modelos", "ia_listar_modelos"),
    LegacyRouteSpec("POST", "/api/ia/conversas/salvar", "ia_salvar_conversa"),
    LegacyRouteSpec("GET", "/api/ia/conversas/listar", "ia_listar_conversas"),
    LegacyRouteSpec("GET", "/api/ia/conversas/{conversa_id}", "ia_carregar_conversa"),
    LegacyRouteSpec("DELETE", "/api/ia/conversas/{conversa_id}", "ia_deletar_conversa"),
    LegacyRouteSpec("GET", "/api/ia/rag/status", "ia_rag_status"),
    LegacyRouteSpec("POST", "/api/ia/rag/indexar", "ia_rag_indexar"),
    LegacyRouteSpec("POST", "/api/ia/rag/reindexar", "ia_rag_reindexar"),
    LegacyRouteSpec("GET", "/api/ia/imagens/{filename:path}", "servir_imagem_ia"),
)


router = APIRouter(tags=["ia"])


def create_ia_router() -> APIRouter:
    from backend.services import ia

    ia_router = APIRouter(tags=["ia"])

    for spec in LEGACY_IA_ROUTES:
        endpoint = getattr(ia, spec.endpoint_name)
        ia_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return ia_router
