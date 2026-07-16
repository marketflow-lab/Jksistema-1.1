"""Compatibility facade for promocoes_api."""

from __future__ import annotations

from backend.services import promocoes_api_analise as _module_0
from backend.services import promocoes_api_jobs as _module_1
from backend.services import promocoes_api_participacoes as _module_2
from backend.services.promocoes_api_analise import *
from backend.services.promocoes_api_jobs import *
from backend.services.promocoes_api_participacoes import *

_MODULES = (
    _module_0,
    _module_1,
    _module_2,
)


def _peer_globals() -> dict[str, object]:
    peers = {}
    for module in _MODULES:
        for name in getattr(module, "PEER_EXPORTS", getattr(module, "__all__", ())):
            if name == "get_tenant_id":
                continue
            if hasattr(module, name):
                peers[name] = getattr(module, name)
    return peers


def configure_promocoes_api_runtime(runtime_module=None, peers=None):
    combined = dict(peers or {})
    for module in _MODULES:
        configure = getattr(module, f"configure_{module.__name__.rsplit('.', 1)[-1]}_runtime", None)
        if callable(configure):
            configure(runtime_module, combined)
    combined.update(_peer_globals())
    for module in _MODULES:
        configure = getattr(module, f"configure_{module.__name__.rsplit('.', 1)[-1]}_runtime", None)
        if callable(configure):
            configure(runtime_module, combined)
    for name, value in combined.items():
        if name in __all__ or name.startswith("_"):
            globals()[name] = value
    return runtime_module


__all__ = ['analisar_promo_via_api', 'analisar_promo_via_api_sem_arquivos', 'analisar_promo_via_api_com_arquivos', '_promo_analise_background_worker', 'PROMO_AUTOMACAO_INTERVAL_UNITS', '_promo_automacao_path', '_promo_automacao_carregar', '_promo_automacao_salvar', '_promo_automacao_intervalo', '_promo_automacao_sanitizar', '_promo_automacao_public_payload', '_promo_start_api_worker_job', '_promo_consultar_worker_job', '_promo_automacao_linha_valor', '_promo_automacao_montar_participacoes', '_promo_automacao_refresh_job_state', '_promo_automacao_config_pronta', '_promo_automacao_processar_tenant', '_promo_automacao_tenants', '_promo_automacao_worker', '_promo_automacao_iniciar_background', 'iniciar_analise_promo_via_api', 'iniciar_analise_promo_via_api_com_arquivos', 'progresso_analise_promo_via_api_com_arquivos', 'cancelar_analise_promo_via_api_com_arquivos', 'promo_automacao_obter', 'promo_automacao_salvar', '_promo_aplicar_item_participacao_ml', '_aplicar_participacoes_promocoes_payload', 'aplicar_participacoes_promocoes', '_promo_aplicar_participacoes_job_worker', 'aplicar_participacoes_promocoes_start', 'aplicar_participacoes_promocoes_job', 'analisar_promo_automatico', 'PROMOCOES_ENDPOINTS', 'configure_promocoes_api_runtime']
if "configure_promocoes_api_runtime" not in __all__:
    __all__.append("configure_promocoes_api_runtime")

configure_promocoes_api_runtime()
