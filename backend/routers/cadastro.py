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
    return router


__all__ = ["create_cadastro_router"]
