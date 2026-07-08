"""Impostos and Simulador router definitions."""

from __future__ import annotations

from dataclasses import dataclass
from fastapi import APIRouter


@dataclass(frozen=True)
class LegacyRouteSpec:
    method: str
    path: str
    endpoint_name: str


LEGACY_IMPOSTOS_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/api/impostos/regras", "listar_regras_impostos"),
    LegacyRouteSpec("POST", "/api/impostos/regras", "salvar_regra_impostos"),
    LegacyRouteSpec("DELETE", "/api/impostos/regras/{regra_id}", "excluir_regra_impostos"),
    LegacyRouteSpec("GET", "/api/impostos/produtos", "listar_produtos_impostos"),
    LegacyRouteSpec("POST", "/api/impostos/simular", "simular_impostos"),
    LegacyRouteSpec("POST", "/api/impostos/aplicar-cadastro", "aplicar_impostos_no_cadastro"),
    LegacyRouteSpec("GET", "/api/impostos/siscomex/config", "obter_config_siscomex_impostos"),
    LegacyRouteSpec("PUT", "/api/impostos/siscomex/config", "salvar_config_siscomex_impostos"),
    LegacyRouteSpec("POST", "/api/impostos/siscomex/testar", "testar_config_siscomex_impostos"),
    LegacyRouteSpec("POST", "/api/impostos/siscomex/consultar", "consultar_ttce_impostos"),
    LegacyRouteSpec("POST", "/api/impostos/siscomex/aliquotas-completas", "consultar_aliquotas_completas"),
    LegacyRouteSpec("POST", "/api/impostos/aliquotas-importacao/atualizar/{ncm}", "atualizar_aliquotas_importacao_ncm"),
    LegacyRouteSpec("GET", "/api/impostos/aliquotas-importacao/{ncm}", "obter_aliquotas_importacao_ncm"),
    LegacyRouteSpec("POST", "/api/impostos/ncm/importar-excel", "importar_ncm_excel_impostos"),
    LegacyRouteSpec("GET", "/api/impostos/ncm", "listar_ncm_impostos"),
    LegacyRouteSpec("POST", "/api/simulador/calcular", "calcular_preco_simulador"),
    LegacyRouteSpec("POST", "/api/impostos/convenio-mg/importar-excel", "importar_convenio_mg_excel_impostos"),
    LegacyRouteSpec("GET", "/api/impostos/convenio-mg", "listar_convenio_mg_impostos"),
)


router = APIRouter(tags=["impostos"])


def create_impostos_router() -> APIRouter:
    from backend.services import impostos

    impostos_router = APIRouter(tags=["impostos"])

    for spec in LEGACY_IMPOSTOS_ROUTES:
        endpoint = getattr(impostos, spec.endpoint_name)
        impostos_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return impostos_router
