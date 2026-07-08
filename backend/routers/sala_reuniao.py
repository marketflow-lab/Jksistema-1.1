"""Sala de Reuniao router definitions."""

from __future__ import annotations

from fastapi import APIRouter


def create_sala_reuniao_router() -> APIRouter:
    from backend.services import sala_reuniao

    router = APIRouter(tags=["sala-reuniao"])
    router.add_api_route("/api/sala-reuniao/status", sala_reuniao.sala_reuniao_status, methods=["GET"], name="sala_reuniao_status")
    router.add_api_route("/api/sala-reuniao/salas", sala_reuniao.sala_reuniao_criar_sala, methods=["POST"], name="sala_reuniao_criar_sala")
    router.add_api_route("/api/sala-reuniao/reunioes-ativas", sala_reuniao.sala_reuniao_reunioes_ativas, methods=["GET"], name="sala_reuniao_reunioes_ativas")
    router.add_api_route("/api/sala-reuniao/salas-ativas", sala_reuniao.sala_reuniao_salas_ativas_alias, methods=["GET"], name="sala_reuniao_salas_ativas_alias")
    router.add_api_route("/api/sala-reuniao/salas", sala_reuniao.sala_reuniao_salas_listar, methods=["GET"], name="sala_reuniao_salas_listar")
    router.add_api_route("/api/sala-reuniao/reunioes-ativas/encerrar-local", sala_reuniao.sala_reuniao_encerrar_local, methods=["POST"], name="sala_reuniao_encerrar_local")
    router.add_api_route("/api/sala-reuniao/reunioes-ativas/encerrar", sala_reuniao.sala_reuniao_encerrar, methods=["POST"], name="sala_reuniao_encerrar")
    router.add_api_route("/api/sala-reuniao/reunioes-ativas/encerrar-todas", sala_reuniao.sala_reuniao_encerrar_todas, methods=["POST"], name="sala_reuniao_encerrar_todas")
    router.add_api_route("/api/sala-reuniao/salas/encerrar-local", sala_reuniao.sala_reuniao_encerrar_local_alias, methods=["POST"], name="sala_reuniao_encerrar_local_alias")
    router.add_api_route("/api/sala-reuniao/uso-mensal", sala_reuniao.sala_reuniao_uso_mensal, methods=["GET"], name="sala_reuniao_uso_mensal")
    router.add_api_route("/api/sala-reuniao/uso-mensal/adicionar", sala_reuniao.sala_reuniao_uso_mensal_adicionar, methods=["POST"], name="sala_reuniao_uso_mensal_adicionar")
    router.add_api_route("/api/sala-reuniao/gravacoes", sala_reuniao.sala_reuniao_gravacoes, methods=["GET"], name="sala_reuniao_gravacoes")
    router.add_api_route("/api/sala-reuniao/transcricoes", sala_reuniao.sala_reuniao_transcricoes, methods=["GET"], name="sala_reuniao_transcricoes")
    router.add_api_route("/api/sala-reuniao/rustdesk/status", sala_reuniao.sala_reuniao_rustdesk_status, methods=["GET"], name="sala_reuniao_rustdesk_status")
    router.add_api_route("/api/sala-reuniao/rustdesk/abrir", sala_reuniao.sala_reuniao_rustdesk_abrir, methods=["POST"], name="sala_reuniao_rustdesk_abrir")
    router.add_api_route("/api/sala-reuniao/rustdesk/acoplar", sala_reuniao.sala_reuniao_rustdesk_acoplar, methods=["POST"], name="sala_reuniao_rustdesk_acoplar")
    router.add_api_route("/api/sala-reuniao/rustdesk/ocultar", sala_reuniao.sala_reuniao_rustdesk_ocultar, methods=["POST"], name="sala_reuniao_rustdesk_ocultar")
    return router


__all__ = ["create_sala_reuniao_router"]
