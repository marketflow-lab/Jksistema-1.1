"""Cadastro API endpoint facade."""

from __future__ import annotations

from backend.services.cadastro_listagem import *
from backend.services.cadastro_importacao import *
from backend.services.cadastro_produtos import *


def configure_cadastro_api_runtime(runtime_module=None):
    configure_cadastro_listagem_runtime(runtime_module)
    configure_cadastro_importacao_runtime(runtime_module)
    configure_cadastro_produtos_runtime(runtime_module)
    return runtime_module


configure_cadastro_api_runtime()

CADASTRO_ENDPOINTS = ('iniciar_sync_ncm_cadastro', 'progresso_sync_ncm_cadastro', 'listar_produtos_cadastro', 'salvar_produto_cadastro', 'importar_colunas_cadastro_por_sku', 'listar_colunas_cadastro', 'obter_produto_cadastro', 'atualizar_produto_cadastro_completo', 'incluir_produto_cadastro_completo', 'upload_foto_cadastro', 'obter_produto_cadastro_query', 'atualizar_produto_cadastro_completo_query', 'servir_foto_cadastro', 'servir_foto_cadastro_por_arquivo')

__all__ = ['listar_produtos_cadastro', 'importar_colunas_cadastro_por_sku', 'salvar_produto_cadastro', 'listar_colunas_cadastro', 'obter_produto_cadastro', 'atualizar_produto_cadastro_completo', 'incluir_produto_cadastro_completo', 'obter_produto_cadastro_query', 'atualizar_produto_cadastro_completo_query', 'CADASTRO_ENDPOINTS', 'configure_cadastro_api_runtime']
