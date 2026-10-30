"""Tax product reads must leave the API loop available to other requests."""

import asyncio
from contextvars import ContextVar
import threading

from fastapi import HTTPException
import pytest

from backend.services import impostos_regras


@pytest.mark.parametrize("client_id", ["cliente-a", "cliente-b"])
def test_listar_produtos_impostos_preserva_contexto_sem_bloquear_loop(monkeypatch, client_id):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    request_context = ContextVar("tax_loading_request")
    calls = []
    payload = {"produtos": [{"sku": "001"}], "resumo": {"qtd_produtos": 1}}

    def calculate(tenant_id, reserve):
        calls.append((tenant_id, reserve, request_context.get(), threading.get_ident()))
        entered.set()
        try:
            release.wait(timeout=1)
            return payload
        finally:
            finished.set()

    monkeypatch.setattr(impostos_regras, "_montar_simulacao_impostos", calculate)

    async def exercise():
        token = request_context.set((client_id, "request"))
        task = asyncio.create_task(impostos_regras.listar_produtos_impostos(client_id))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), timeout=2)
            # This coroutine represents another request admitted while the tax
            # calculation is still waiting on local work.
            assert not finished.is_set(), "A consulta fiscal bloqueou o loop da API"
            assert len(calls) == 1
            assert calls[0][:3] == (client_id, 0.0, (client_id, "request"))
            assert calls[0][3] != threading.get_ident()
            release.set()
            assert await asyncio.wait_for(task, timeout=2) is payload
        finally:
            release.set()
            await task
            request_context.reset(token)

    asyncio.run(exercise())


@pytest.mark.parametrize("error", [HTTPException(503, "Tax data unavailable"), RuntimeError("read failure")])
def test_listar_produtos_impostos_preserva_erros_da_consulta(monkeypatch, error):
    def calculate(tenant_id, reserve):
        assert (tenant_id, reserve) == ("cliente-a", 0.0)
        raise error

    monkeypatch.setattr(impostos_regras, "_montar_simulacao_impostos", calculate)

    async def exercise():
        with pytest.raises(type(error)) as caught:
            await impostos_regras.listar_produtos_impostos("cliente-a")
        assert caught.value is error

    asyncio.run(exercise())
