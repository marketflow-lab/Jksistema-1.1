"""Compatibility facade for the Cadastro module."""

from __future__ import annotations

from backend.schemas import (
    CadastroFornecedorRequest,
    CadastroProdutoLojaAtualizacaoRequest,
    CadastroProdutoLojaRequest,
    CadastroProdutoRequest,
)
from backend.services.cadastro_common import *
from backend.services.cadastro_custos import *
from backend.services.cadastro_fotos import *
from backend.services.cadastro_sync_ncm import *
from backend.services.cadastro_lojas_produtos import *
from backend.services.cadastro_mercadolivre import *
from backend.services.cadastro_importacao_catalogos import *
from backend.services.cadastro_api import *


def configure_cadastro_runtime(runtime_module=None):
    configure_cadastro_common_runtime(runtime_module)
    configure_cadastro_custos_runtime(runtime_module)
    configure_cadastro_fotos_runtime(runtime_module)
    configure_cadastro_sync_ncm_runtime(runtime_module)
    configure_cadastro_lojas_produtos_runtime(runtime_module)
    configure_cadastro_mercadolivre_runtime(runtime_module)
    configure_cadastro_importacao_catalogos_runtime(runtime_module)
    configure_cadastro_api_runtime(runtime_module)
    return runtime_module


configure_cadastro_runtime()

__all__ = ['CadastroProdutoRequest', 'CadastroFornecedorRequest', 'configure_cadastro_runtime', 'CADASTRO_ENDPOINTS', 'CADASTRO_CUSTOS_LOJAS_COLS', 'SYNC_NCM_JOBS', 'CADASTRO_PESQUISA_COLS', 'CADASTRO_COLS_BASE', 'CADASTRO_DESCRICAO_COL_ALIASES', '_normalizar_sku_mes', '_chave_loja_favoritos', '_sku_lookup_variantes', '_cadastro_nome_esta_suspeito', '_cadastro_limpar_nome', '_cadastro_cols_base', '_cadastro_norm_coluna_texto', '_cadastro_canonizar_coluna_descricao', '_cadastro_garantir_colunas_pesquisa', '_cadastro_norm_col_custo', '_CADASTRO_COL_ALIASES_CUSTOS', '_cadastro_colunas_alias', '_cadastro_canonizar_colunas_custos', '_consolidar_cadastro_por_sku', '_cadastro_custos_lojas_path', '_cadastro_norm_loja_custo', '_cadastro_ler_custos_lojas', '_cadastro_salvar_custos_lojas', '_cadastro_mapa_custos_lojas', '_cadastro_anexar_custos_por_loja', '_cadastro_importar_custos_loja', '_exportar_planilha_xlsx_bytes', '_extrair_imagens_planilha_por_sku', '_nome_arquivo_foto_sku', '_cadastro_sku_sem_zeros_blocos', '_salvar_foto_data_url_no_tenant', '_cadastro_mapa_fotos_locais', '_cadastro_resolver_foto_local', 'upload_foto_cadastro', 'servir_foto_cadastro', 'servir_foto_cadastro_por_arquivo', '_sku_lookup_keys_sync_ncm', '_set_sync_ncm_job', '_sync_ncm_cadastro_worker', 'iniciar_sync_ncm_cadastro', 'progresso_sync_ncm_cadastro', 'listar_produtos_cadastro', 'salvar_produto_cadastro', 'importar_colunas_cadastro_por_sku', 'listar_colunas_cadastro', 'obter_produto_cadastro', 'atualizar_produto_cadastro_completo', 'incluir_produto_cadastro_completo', 'obter_produto_cadastro_query', 'atualizar_produto_cadastro_completo_query', 'listar_fornecedores_cadastro', 'criar_fornecedor_cadastro', 'atualizar_fornecedor_cadastro', 'excluir_fornecedor_cadastro']
__all__ += [
    'CadastroProdutoLojaRequest',
    'CadastroProdutoLojaAtualizacaoRequest',
    'CADASTRO_PRODUTOS_LOJAS_ARQUIVO',
    'CADASTRO_PRODUTOS_LOJAS_COLUNAS',
    'resolver_loja_cadastro',
    'salvar_produto_loja',
    'salvar_produtos_loja_em_lote',
    'listar_produtos_lojas',
    'listar_produtos_loja',
    'obter_produto_loja',
    'criar_produto_loja',
    'atualizar_produto_loja',
    'excluir_produto_loja',
    'listar_colunas_produtos_loja',
    'importar_colunas_cadastro_loja',
    'preview_migracao_produtos_loja',
    'preview_migracao_produtos_loja_endpoint',
    'consultar_produto_mercado_livre_loja',
    'consultar_produto_mercado_livre',
    'CATALOG_IMPORT_JOBS',
    'iniciar_preview_importacao_catalogo',
    'obter_importacao_catalogo',
    'cancelar_importacao_catalogo',
    'aplicar_importacao_catalogo',
]
