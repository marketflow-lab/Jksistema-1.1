"""Cadastro router definitions."""

from __future__ import annotations

from fastapi import APIRouter


def create_cadastro_router() -> APIRouter:
    from backend.services import cadastro

    router = APIRouter(prefix="/api/cadastro", tags=["cadastro"])
    router.add_api_route("/sync-ncm/iniciar", cadastro.iniciar_sync_ncm_cadastro, methods=["POST"], name="iniciar_sync_ncm_cadastro")
    router.add_api_route("/sync-ncm/progresso/{job_id}", cadastro.progresso_sync_ncm_cadastro, methods=["GET"], name="progresso_sync_ncm_cadastro")
    router.add_api_route("/produtos", cadastro.listar_produtos_cadastro, methods=["GET"], name="listar_produtos_cadastro")
    router.add_api_route("/produtos", cadastro.salvar_produto_cadastro, methods=["POST"], name="salvar_produto_cadastro")
    router.add_api_route("/fornecedores", cadastro.listar_fornecedores_cadastro, methods=["GET"], name="listar_fornecedores_cadastro")
    router.add_api_route("/fornecedores", cadastro.criar_fornecedor_cadastro, methods=["POST"], name="criar_fornecedor_cadastro")
    router.add_api_route("/fornecedores/{fornecedor_id}", cadastro.atualizar_fornecedor_cadastro, methods=["PUT"], name="atualizar_fornecedor_cadastro")
    router.add_api_route("/fornecedores/{fornecedor_id}", cadastro.excluir_fornecedor_cadastro, methods=["DELETE"], name="excluir_fornecedor_cadastro")
    router.add_api_route("/importar-colunas", cadastro.importar_colunas_cadastro_por_sku, methods=["POST"], name="importar_colunas_cadastro_por_sku")
    router.add_api_route("/colunas", cadastro.listar_colunas_cadastro, methods=["GET"], name="listar_colunas_cadastro")
    router.add_api_route("/produto/{sku}", cadastro.obter_produto_cadastro, methods=["GET"], name="obter_produto_cadastro")
    router.add_api_route("/produto/{sku}", cadastro.atualizar_produto_cadastro_completo, methods=["PUT"], name="atualizar_produto_cadastro_completo")
    router.add_api_route("/produto", cadastro.incluir_produto_cadastro_completo, methods=["POST"], name="incluir_produto_cadastro_completo")
    router.add_api_route("/foto/upload", cadastro.upload_foto_cadastro, methods=["POST"], name="upload_foto_cadastro")
    router.add_api_route("/foto-upload", cadastro.upload_foto_cadastro, methods=["POST"], name="upload_foto_cadastro")
    router.add_api_route("/produto", cadastro.obter_produto_cadastro_query, methods=["GET"], name="obter_produto_cadastro_query")
    router.add_api_route("/produto", cadastro.atualizar_produto_cadastro_completo_query, methods=["PUT"], name="atualizar_produto_cadastro_completo_query")
    router.add_api_route("/foto/{client_id}/{filename:path}", cadastro.servir_foto_cadastro, methods=["GET"], name="servir_foto_cadastro")
    router.add_api_route("/foto-arquivo/{filename:path}", cadastro.servir_foto_cadastro_por_arquivo, methods=["GET"], name="servir_foto_cadastro_por_arquivo")
    router.add_api_route("/lojas/produtos", cadastro.listar_produtos_lojas, methods=["GET"], name="listar_produtos_lojas")
    router.add_api_route("/lojas/{store_id}/produtos", cadastro.listar_produtos_loja, methods=["GET"], name="listar_produtos_loja")
    router.add_api_route("/lojas/{store_id}/produtos", cadastro.criar_produto_loja, methods=["POST"], name="criar_produto_loja")
    router.add_api_route("/lojas/{store_id}/produtos/{sku:path}", cadastro.obter_produto_loja, methods=["GET"], name="obter_produto_loja")
    router.add_api_route("/lojas/{store_id}/produtos/{sku:path}", cadastro.atualizar_produto_loja, methods=["PUT"], name="atualizar_produto_loja")
    router.add_api_route("/lojas/{store_id}/produtos/{sku:path}", cadastro.excluir_produto_loja, methods=["DELETE"], name="excluir_produto_loja")
    router.add_api_route("/lojas/{store_id}/colunas", cadastro.listar_colunas_produtos_loja, methods=["GET"], name="listar_colunas_produtos_loja")
    router.add_api_route("/lojas/{store_id}/mercado-livre/produto", cadastro.consultar_produto_mercado_livre_loja, methods=["GET"], name="consultar_produto_mercado_livre_loja")
    router.add_api_route("/lojas/{store_id}/importacoes/{source}/preview", cadastro.iniciar_preview_importacao_catalogo, methods=["POST"], name="iniciar_preview_importacao_catalogo", status_code=202)
    router.add_api_route("/lojas/{store_id}/importacoes/{job_id}", cadastro.obter_importacao_catalogo, methods=["GET"], name="obter_importacao_catalogo")
    router.add_api_route("/lojas/{store_id}/importacoes/{job_id}/cancelar", cadastro.cancelar_importacao_catalogo, methods=["POST"], name="cancelar_importacao_catalogo")
    router.add_api_route("/lojas/{store_id}/importacoes/{job_id}/aplicar", cadastro.aplicar_importacao_catalogo, methods=["POST"], name="aplicar_importacao_catalogo")
    router.add_api_route("/lojas/{store_id}/importar-colunas", cadastro.importar_colunas_cadastro_loja, methods=["POST"], name="importar_colunas_cadastro_loja")
    router.add_api_route("/lojas/{store_id}/migracao/preview", cadastro.preview_migracao_produtos_loja_endpoint, methods=["GET"], name="preview_migracao_produtos_loja")
    return router


__all__ = ["create_cadastro_router"]
