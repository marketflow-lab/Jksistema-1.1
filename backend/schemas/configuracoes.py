"""Pydantic schemas for configuracoes."""

from typing import Any, Optional

from pydantic import BaseModel


class ConfiguracoesGlobaisRequest(BaseModel):
    auto_sync_estoque_janela_minutos: int
    ia_modelo_padrao: str | None = None
    ia_modelo_perguntas: str | None = None
    ia_modelo_pos_venda: str | None = None
    ia_raciocinio_perguntas: str | None = None
    ia_raciocinio_pos_venda: str | None = None
    ia_modelo_chat: str | None = None
    ia_modelo_favoritos: str | None = None
    ia_modo_padrao: str | None = None
    ia_modo_perguntas: str | None = None
    ia_modo_pos_venda: str | None = None
    ia_modo_chat: str | None = None
    ia_modo_favoritos: str | None = None
    ia_vertex_project_id: str | None = None
    ia_vertex_location: str | None = None
    ia_vertex_model: str | None = None
    ia_vertex_service_account_email: str | None = None
    ia_agent_resource_name: str | None = None
    ia_agent_endpoint_url: str | None = None
    ia_openai_api_key: str | None = None
    ia_openai_api_key_limpar: bool | None = None
    ia_deepseek_api_key: str | None = None
    ia_deepseek_api_key_limpar: bool | None = None
    ia_gemini_api_key: str | None = None
    ia_gemini_api_key_limpar: bool | None = None
    ia_agent_api_key: str | None = None
    ia_agent_api_key_limpar: bool | None = None
    ia_favoritos_usar_imagem: bool | None = None
    ia_openai_ativa: bool | None = None
    ia_deepseek_ativa: bool | None = None
    ia_gemini_ativa: bool | None = None
    ia_vertex_ativa: bool | None = None


__all__ = [
    "ConfiguracoesGlobaisRequest",
]
