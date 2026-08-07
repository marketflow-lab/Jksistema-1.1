"""Explicit, allowlisted runtime adapters for endpoint workflows."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from backend.modules.perguntas_pos_venda.endpoints.state import ENDPOINTS_STATE


DEPENDENCY_GROUPS: Mapping[str, frozenset[str]] = MappingProxyType({
    "tenant": frozenset({"get_tenant_id", "get_tenant_path"}),
    "stores": frozenset({
        "carregar_lojas", "_integracoes_nome_normalizado", "_obter_cfg_ml", "_ml_oauth_status",
        "_perguntas_loja_config_normalizar", "_perguntas_loja_config_obter",
        "_perguntas_loja_config_salvar", "_perguntas_loja_configs_carregar",
    }),
    "questions": frozenset({
        "_ml_api_item", "_ml_api_item_com_oauth_tenant", "_ml_api_request", "_ml_buscar_itens_batch",
        "_ml_extrair_sku", "_ml_parse_error_detail", "_ml_perguntas_anexar_historico_comprador",
        "_ml_perguntas_buscar_usuarios", "_ml_perguntas_completar_skus_itens",
        "_ml_perguntas_normalizar", "_ml_perguntas_resumir_status", "_ml_perguntas_tempo_resposta",
        "_perguntas_ia_enviar_resposta_ml", "_perguntas_ia_pergunta_respondida_ml",
    }),
    "post_sale": frozenset({
        "_ml_mediacao_buscar_motivos_claims", "_ml_mediacao_normalizar_claim", "_ml_mediacao_order_id",
        "_ml_pos_venda_auditoria_registrar", "_ml_pos_venda_buscar_pedido",
        "_ml_pos_venda_conversa_corresponde_busca", "_ml_pos_venda_conversa_nao_lida",
        "_ml_pos_venda_conversa_respondida_pela_loja", "_ml_pos_venda_data_iso",
        "_ml_pos_venda_enviar_resposta_ml", "_ml_pos_venda_executar_pipeline_ia",
        "_ml_pos_venda_item_ids_pedido", "_ml_pos_venda_memoria_registrar_resposta_enviada",
        "_ml_pos_venda_montar_conversa_normalizada", "_ml_pos_venda_normalizar_pedido",
        "_ml_pos_venda_normalizar_termo_busca", "_ml_pos_venda_pedido_corresponde_busca",
        "_ml_pos_venda_pipeline_resumo", "_ml_pos_venda_preparar_conversa_ia",
    }),
    "ai": frozenset({
        "_chamar_codex_chat", "_chamar_deepseek_chat", "_chamar_gemini_chat",
        "_chamar_openai_responses", "_chamar_vertex_ai_chat", "_codex_modelo_nome_curto",
        "_gemini_nome_curto", "_ia_modelo_perguntas_configurado", "_ia_modo_perguntas_configurado",
        "_ia_modo_pos_venda_configurado", "_ia_treinamento_ppv_listar_skus",
        "_ia_treinamento_ppv_produto_por_sku", "_ia_treinamento_ppv_produto_prompt",
        "_ia_treinamento_ppv_resolver", "_ia_treinamento_ppv_salvar",
        "_ia_treinamento_ppv_tipo_label", "_ia_treinamento_ppv_tipo_normalizar",
        "_modelo_eh_codex", "_modelo_eh_gemini_api", "_modelo_eh_vertex_ai",
        "_normalizar_ia_modelo_padrao", "_normalizar_sku_mes", "_perguntas_ia_gerar_resposta",
        "_perguntas_ia_limpar_resposta", "_perguntas_ia_resposta_final_loja",
        "_pos_venda_ia_limpar_resposta", "_vertex_ai_modelo_padrao", "_vertex_modelo_nome_curto",
    }),
    "persistence": frozenset({
        "_ml_questions_v2_webhook_events_path", "_perguntas_ia_aprovacao_id",
        "_perguntas_ia_aprovacao_pendente", "_perguntas_ia_aprovacoes_carregar",
        "_perguntas_ia_aprovacoes_salvar", "_perguntas_ia_assinatura_loja", "_perguntas_ia_ja_processada",
        "_perguntas_ia_ler_json", "_perguntas_ia_marcar_processada",
        "_perguntas_ia_memoria_registrar_resposta_aprovada", "_perguntas_ia_resolver_aprovacao",
        "_perguntas_ia_resolver_aprovacoes_pendentes", "_perguntas_ia_salvar_json",
        "_perguntas_ia_state_carregar", "_perguntas_ia_state_salvar", "_pos_venda_ia_aprovacao_id",
    }),
    "integration": frozenset({"MercadoLivreWebhookReceiver", "logger"}),
})
ALLOWED_DEPENDENCIES = frozenset().union(*DEPENDENCY_GROUPS.values())


@dataclass(frozen=True)
class EndpointAdapterGroup:
    source: Any
    names: frozenset[str]

    def resolve(self, name: str) -> Any:
        if name not in self.names:
            raise KeyError(name)
        return getattr(self.source, name)


@dataclass(frozen=True)
class PerguntasEndpointsRuntime:
    source: Any
    groups: Mapping[str, EndpointAdapterGroup]

    def resolve(self, name: str) -> Any:
        if name not in ALLOWED_DEPENDENCIES:
            raise RuntimeError(f"Dependencia nao autorizada nos endpoints PPV: {name}")
        for group in self.groups.values():
            if name in group.names:
                return group.resolve(name)
        raise RuntimeError(f"Dependencia nao configurada nos endpoints PPV: {name}")


def configure_runtime(source: Any) -> PerguntasEndpointsRuntime:
    if isinstance(source, PerguntasEndpointsRuntime):
        configured = source
    else:
        missing = sorted(name for name in ALLOWED_DEPENDENCIES if not hasattr(source, name))
        if missing:
            raise RuntimeError(f"Runtime PPV incompleto: {', '.join(missing[:8])}")
        groups = MappingProxyType({
            label: EndpointAdapterGroup(source=source, names=names)
            for label, names in DEPENDENCY_GROUPS.items()
        })
        configured = PerguntasEndpointsRuntime(source=source, groups=groups)
    with ENDPOINTS_STATE.runtime_guard:
        current = ENDPOINTS_STATE.runtime
        if current is configured or (
            isinstance(current, PerguntasEndpointsRuntime) and current.source is configured.source
        ):
            return current
        ENDPOINTS_STATE.runtime = configured
        return configured


def current_runtime() -> PerguntasEndpointsRuntime:
    with ENDPOINTS_STATE.runtime_guard:
        runtime = ENDPOINTS_STATE.runtime
    if not isinstance(runtime, PerguntasEndpointsRuntime):
        raise RuntimeError("perguntas_pos_venda_endpoints runtime was not configured")
    return runtime


def runtime_dependency(name: str) -> Any:
    return current_runtime().resolve(name)


class RuntimeAdapter:
    """Resolve an allowlisted adapter lazily, preserving runtime monkeypatches."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        if name not in ALLOWED_DEPENDENCIES:
            raise RuntimeError(f"Dependencia nao autorizada nos endpoints PPV: {name}")
        self._name = name

    def __call__(self, *args, **kwargs):
        return runtime_dependency(self._name)(*args, **kwargs)

    def __getattr__(self, attribute: str) -> Any:
        return getattr(runtime_dependency(self._name), attribute)


def runtime_adapter(name: str) -> RuntimeAdapter:
    return RuntimeAdapter(name)
