"""Cadastro API endpoint facade."""

from __future__ import annotations

from backend.services.cadastro_listagem import *
from backend.services.cadastro_importacao import *
from backend.services.cadastro_produtos import *
from backend.services.cadastro_lojas_produtos import *
from backend.services.cadastro_mercadolivre import *
from backend.services.cadastro_importacao_catalogos import *
from backend.services.cadastro_fornecedores import *


def configure_cadastro_api_runtime(runtime_module=None):
    configure_cadastro_listagem_runtime(runtime_module)
    configure_cadastro_importacao_runtime(runtime_module)
    configure_cadastro_produtos_runtime(runtime_module)
    configure_cadastro_lojas_produtos_runtime(runtime_module)
    configure_cadastro_mercadolivre_runtime(runtime_module)
    configure_cadastro_importacao_catalogos_runtime(runtime_module)
    configure_cadastro_fornecedores_runtime(runtime_module)
    return runtime_module


configure_cadastro_api_runtime()

CADASTRO_ENDPOINTS = ('iniciar_sync_ncm_cadastro', 'progresso_sync_ncm_cadastro', 'listar_produtos_cadastro', 'salvar_produto_cadastro', 'importar_colunas_cadastro_por_sku', 'listar_colunas_cadastro', 'obter_produto_cadastro', 'atualizar_produto_cadastro_completo', 'incluir_produto_cadastro_completo', 'upload_foto_cadastro', 'obter_produto_cadastro_query', 'atualizar_produto_cadastro_completo_query', 'servir_foto_cadastro', 'servir_foto_cadastro_por_arquivo', 'listar_fornecedores_cadastro', 'criar_fornecedor_cadastro', 'atualizar_fornecedor_cadastro', 'excluir_fornecedor_cadastro')
CADASTRO_ENDPOINTS += (
    'listar_produtos_loja',
    'obter_produto_loja',
    'criar_produto_loja',
    'atualizar_produto_loja',
    'excluir_produto_loja',
    'listar_colunas_produtos_loja',
    'importar_colunas_cadastro_loja',
    'preview_migracao_produtos_loja_endpoint',
    'consultar_produto_mercado_livre_loja',
    'iniciar_preview_importacao_catalogo',
    'obter_importacao_catalogo',
    'cancelar_importacao_catalogo',
    'aplicar_importacao_catalogo',
)

__all__ = ['listar_produtos_cadastro', 'importar_colunas_cadastro_por_sku', 'salvar_produto_cadastro', 'listar_colunas_cadastro', 'obter_produto_cadastro', 'atualizar_produto_cadastro_completo', 'incluir_produto_cadastro_completo', 'obter_produto_cadastro_query', 'atualizar_produto_cadastro_completo_query', 'listar_fornecedores_cadastro', 'criar_fornecedor_cadastro', 'atualizar_fornecedor_cadastro', 'excluir_fornecedor_cadastro', 'CADASTRO_ENDPOINTS', 'configure_cadastro_api_runtime']
__all__ += [
    'listar_produtos_loja',
    'obter_produto_loja',
    'criar_produto_loja',
    'atualizar_produto_loja',
    'excluir_produto_loja',
    'listar_colunas_produtos_loja',
    'importar_colunas_cadastro_loja',
    'preview_migracao_produtos_loja_endpoint',
    'resolver_loja_cadastro',
    'salvar_produto_loja',
    'salvar_produtos_loja_em_lote',
    'preview_migracao_produtos_loja',
    'consultar_produto_mercado_livre_loja',
    'consultar_produto_mercado_livre',
    'iniciar_preview_importacao_catalogo',
    'obter_importacao_catalogo',
    'cancelar_importacao_catalogo',
    'aplicar_importacao_catalogo',
]
