"""List admission, interactive capacity and HTTP routing use synthetic work only."""

import asyncio
import contextvars
from threading import Event, Lock

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from backend.routers.medias_compras import create_medias_compras_router
from backend.services import blocking_workers as workers
from backend.services import medias_compras_common as common


CONFIG = "/api/medias-compras/listas-pedidos/configuracao-margens"
ANALISE = "/api/medias-compras/listas-pedidos/A/skus/001/analise-concorrentes"
REQUEST_CONTEXT = contextvars.ContextVar("synthetic_import_request", default="")


def app_autenticado():
    async def identity(authorization: str | None = Header(default=None)):
        if authorization != "Bearer synthetic":
            raise HTTPException(401, "Sessão necessária")
        return "tenant-synthetic"

    app = FastAPI()
    app.include_router(create_medias_compras_router())
    app.dependency_overrides[common.get_tenant_id] = identity
    return app


@pytest.mark.parametrize("env,expected", [(None, 4), ("2", 2), ("4", 4), ("8", 4), ("invalid", 4), ("", 4)])
def test_configuracao_autenticada_reflete_o_limiter_estavel(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("JK_IMPORTACOES_MARGENS_CONCORRENCIA", raising=False)
    else:
        monkeypatch.setenv("JK_IMPORTACOES_MARGENS_CONCORRENCIA", env)
    with TestClient(app_autenticado()) as client:
        assert client.get(CONFIG).status_code == 401
        result = client.get(CONFIG, headers={"Authorization": "Bearer synthetic"})
        assert result.status_code == 200
        assert result.json() == {"concorrencia_lista": expected}
        monkeypatch.setenv("JK_IMPORTACOES_MARGENS_CONCORRENCIA", "4" if expected == 2 else "2")
        assert client.get(CONFIG, headers={"Authorization": "Bearer synthetic"}).json() == result.json()


@pytest.mark.parametrize("revision,origin,lane", [
    (True, "lista", "lista"), (True, None, "interativa"), (True, "sku", "interativa"),
    (False, "lista", "legada"), (False, None, "legada"),
])
def test_patch_seleciona_fila_so_para_lista_revisionada(monkeypatch, revision, origin, lane):
    async def lista(function, *args, **kwargs):
        return {"fila": "lista", "cliente": kwargs["client_id"]}

    async def interativa(function, *args, **kwargs):
        return {"fila": "interativa", "cliente": kwargs["client_id"]}

    async def legada(function, *args, **kwargs):
        return {"fila": "legada", "cliente": kwargs["client_id"]}

    monkeypatch.setattr(workers, "run_import_list_financial", lista)
    monkeypatch.setattr(workers, "run_import_financial", interativa)
    monkeypatch.setattr(workers, "run_financial", legada)
    headers = {"Authorization": "Bearer synthetic"}
    if origin:
        headers["X-JK-Importacoes-Origem"] = origin
    payload = {"fonte_custo": "simulacao_importacao_sku", "custo_unitario": 10}
    if revision:
        payload.update(revisao_fontes_custo="a" * 64, assinatura_calculo="b" * 64)
    with TestClient(app_autenticado()) as client:
        result = client.patch(ANALISE, json=payload, headers=headers)
        assert result.status_code == 200
        assert result.json() == {"fila": lane, "cliente": "tenant-synthetic"}
        if revision:
            payload["precos_anuncios_loja"] = {"concorrente_1": 50}
            preview = client.post(ANALISE + "/previa", json=payload, headers=headers)
            assert preview.status_code == 200
            assert preview.json()["fila"] == "interativa"


class Trabalho:
    def __init__(self, label, *, fail=False):
        self.label = label
        self.fail = fail
        self.entered = Event()
        self.release = Event()
        self.context = None

    def run(self):
        self.context = REQUEST_CONTEXT.get()
        self.entered.set()
        assert self.release.wait(5), "Synthetic worker was not released"
        if self.fail:
            raise RuntimeError("synthetic failure")
        return self.label


async def iniciou(work):
    assert await asyncio.to_thread(work.entered.wait, 2), "Synthetic worker did not start"


@pytest.mark.parametrize("limit", [2, 4])
def test_lista_limitada_reserva_interacao_e_leituras_locais(monkeypatch, limit):
    monkeypatch.setenv("JK_IMPORTACOES_MARGENS_CONCORRENCIA", str(limit))

    async def scenario():
        lista = [Trabalho(f"lista-{i}") for i in range(limit + 1)]
        interativos = [Trabalho(f"sku-{i}") for i in range(2)]
        locais = [Trabalho(f"local-{i}") for i in range(2)]
        activity = {"active": 0, "maximum": 0}
        lock = Lock()

        def calcular(work):
            with lock:
                activity["active"] += 1
                activity["maximum"] = max(activity["maximum"], activity["active"])
            try:
                return work.run()
            finally:
                with lock:
                    activity["active"] -= 1

        async def request(work):
            token = REQUEST_CONTEXT.set(work.label)
            try:
                return await workers.run_import_list_financial(calcular, work)
            finally:
                REQUEST_CONTEXT.reset(token)

        tasks = [asyncio.create_task(request(work)) for work in lista]
        try:
            for work in lista[:limit]:
                await iniciou(work)
            await asyncio.sleep(.03)
            assert not lista[-1].entered.is_set()
            assert workers.import_list_financial_limiter().borrowed_tokens == limit
            tasks += [asyncio.create_task(workers.run_import_financial(work.run)) for work in interativos]
            tasks += [asyncio.create_task(workers.run_heavy(work.run)) for work in locais]
            for work in [*interativos, *locais]:
                await iniciou(work)
            assert not lista[-1].entered.is_set()
        finally:
            for work in [*lista, *interativos, *locais]:
                work.release.set()
            await asyncio.gather(*tasks)
        assert activity["maximum"] == limit
        assert [work.context for work in lista] == [work.label for work in lista]
        assert REQUEST_CONTEXT.get() == ""
        assert workers.import_list_financial_limiter().borrowed_tokens == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["queued", "running"])
def test_cancelamento_mantem_admissao_ate_trabalho_terminar(monkeypatch, stage):
    monkeypatch.setenv("JK_IMPORTACOES_MARGENS_CONCORRENCIA", "2")

    async def scenario():
        works = [Trabalho(f"work-{i}") for i in range(4)]
        tasks = []
        try:
            tasks += [asyncio.create_task(workers.run_import_list_financial(work.run)) for work in works[:2]]
            for work in works[:2]:
                await iniciou(work)
            if stage == "running":
                target, target_work = tasks[1], works[1]
            else:
                target_work = works[2]
                target = asyncio.create_task(workers.run_import_list_financial(target_work.run))
                tasks.append(target)
                await asyncio.sleep(.03)
                assert not target_work.entered.is_set()
            target.cancel()
            with pytest.raises(asyncio.CancelledError):
                await target
            assert workers.import_list_financial_limiter().borrowed_tokens == 2
            follower = works[3]
            tasks.append(asyncio.create_task(workers.run_import_list_financial(follower.run)))
            await asyncio.sleep(.03)
            assert not follower.entered.is_set()
            if stage == "queued":
                works[0].release.set()
                await iniciou(target_work)
                assert not follower.entered.is_set()
            target_work.release.set()
            await iniciou(follower)
        finally:
            for work in works:
                work.release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            while workers.import_list_financial_limiter().borrowed_tokens:
                await asyncio.sleep(.01)

    asyncio.run(scenario())


def test_falha_financeira_libera_vaga_sem_perder_contexto(monkeypatch):
    monkeypatch.setenv("JK_IMPORTACOES_MARGENS_CONCORRENCIA", "2")

    async def scenario():
        failed, blocked, follower = Trabalho("falha", fail=True), Trabalho("bloqueado"), Trabalho("seguinte")
        tasks = [asyncio.create_task(workers.run_import_list_financial(work.run)) for work in (failed, blocked, follower)]
        try:
            await iniciou(failed)
            await iniciou(blocked)
            assert not follower.entered.is_set()
            failed.release.set()
            with pytest.raises(RuntimeError, match="synthetic failure"):
                await tasks[0]
            await iniciou(follower)
        finally:
            for work in (failed, blocked, follower):
                work.release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert workers.import_list_financial_limiter().borrowed_tokens == 0

    asyncio.run(scenario())
