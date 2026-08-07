"""Automated post-sale polling workflow."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends
from fastapi.encoders import jsonable_encoder

from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id

_ml_oauth_status = runtime_adapter("_ml_oauth_status")
_ml_pos_venda_auditoria_registrar = runtime_adapter("_ml_pos_venda_auditoria_registrar")
_ml_pos_venda_executar_pipeline_ia = runtime_adapter("_ml_pos_venda_executar_pipeline_ia")
_ml_pos_venda_montar_conversa_normalizada = runtime_adapter("_ml_pos_venda_montar_conversa_normalizada")
_ml_pos_venda_preparar_conversa_ia = runtime_adapter("_ml_pos_venda_preparar_conversa_ia")
_obter_cfg_ml = runtime_adapter("_obter_cfg_ml")
_perguntas_ia_aprovacao_pendente = runtime_adapter("_perguntas_ia_aprovacao_pendente")
_perguntas_ia_aprovacoes_carregar = runtime_adapter("_perguntas_ia_aprovacoes_carregar")
_perguntas_ia_aprovacoes_salvar = runtime_adapter("_perguntas_ia_aprovacoes_salvar")
_perguntas_ia_ja_processada = runtime_adapter("_perguntas_ia_ja_processada")
_perguntas_ia_state_carregar = runtime_adapter("_perguntas_ia_state_carregar")
_perguntas_ia_state_salvar = runtime_adapter("_perguntas_ia_state_salvar")
_perguntas_loja_config_normalizar = runtime_adapter("_perguntas_loja_config_normalizar")
_perguntas_loja_configs_carregar = runtime_adapter("_perguntas_loja_configs_carregar")
carregar_lojas = runtime_adapter("carregar_lojas")
logger = runtime_adapter("logger")


def ml_pos_venda_automacao_poll(
    loja: Optional[str] = None,
    max_per_store: int = 2,
    client_id: str = Depends(get_tenant_id),
):
    # Politica permanente: pos-venda e exclusivamente manual. Este endpoint
    # permanece compativel com clientes antigos, mas nao consulta ML/IA, nao
    # cria aprovacoes e nao devolve sugestoes historicas.
    return jsonable_encoder({
        "success": True,
        "disabled": True,
        "motivo": "pos_venda_somente_manual",
        "novas_pendentes": [],
        "pendentes": [],
        "enviadas": [],
        "erros": [],
    })


__all__ = [
    "ml_pos_venda_automacao_poll",
]
