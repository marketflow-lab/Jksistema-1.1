"""Photo retries share admitted work without retaining responses or crossing scopes."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest
from fastapi import HTTPException

from backend.schemas import MediasVisaoFotosRequest
from backend.services import blocking_workers, medias_compras_visao as visao


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    roots = {"default": tmp_path}
    monkeypatch.setattr(visao, "_FOTOS_VISAO_EM_ANDAMENTO", {})
    monkeypatch.setattr(visao, "get_tenant_path", lambda client: str(roots.get(client, roots["default"])))
    monkeypatch.setattr(visao, "_resolver_escopo_loja_medias", lambda _client, loja, store: {"loja": loja, "store_id": store})

    async def immediate(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(blocking_workers, "run_blocking", immediate)
    yield roots
    assert not visao._FOTOS_VISAO_EM_ANDAMENTO


def request(**changes):
    return MediasVisaoFotosRequest(**{"loja": "Loja A", "store_id": "store-a", "skus": ["SKU"], **changes})


async def entered(event):
    assert await asyncio.to_thread(event.wait, 5)


def test_cancelamento_compartilha_worker_materializa_pedido_e_copia_respostas(ambiente, monkeypatch):
    started, release = Event(), Event()
    calls = []

    def load(client, req):
        calls.append((client, req))
        started.set()
        assert release.wait(5)
        return {"fotos": dict.fromkeys(req.skus, "synthetic-photo.png"), "politica_fotos": "policy"}

    monkeypatch.setattr(visao, "_carregar_fotos_visao_sync", load)

    async def scenario():
        req = request()
        first = asyncio.create_task(visao.api_medias_compras_visao_fotos(req, "tenant-a"))
        await entered(started)
        req.skus.append("MUTATED")
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        followers = [asyncio.create_task(visao.api_medias_compras_visao_fotos(request(), "tenant-a")) for _ in range(2)]
        await asyncio.sleep(0)
        assert len(visao._FOTOS_VISAO_EM_ANDAMENTO) == len(calls) == 1
        release.set()
        left, right = await asyncio.gather(*followers)
        assert set(left["fotos"]) == {"SKU"}
        left["fotos"]["SKU"] = "changed"
        assert right["fotos"]["SKU"] == "synthetic-photo.png"
        assert not visao._FOTOS_VISAO_EM_ANDAMENTO
        await visao.api_medias_compras_visao_fotos(request(), "tenant-a")
        assert len(calls) == 2, "completed responses must not become a photo cache"

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_erro_libera_tarefa_e_permite_nova_tentativa(ambiente, monkeypatch):
    started, release = Event(), Event()
    calls = []

    def load(*_args):
        calls.append(1)
        started.set()
        assert release.wait(5)
        if len(calls) == 1:
            raise HTTPException(503, "synthetic failure")
        return {"fotos": {}, "politica_fotos": "policy"}

    monkeypatch.setattr(visao, "_carregar_fotos_visao_sync", load)

    async def scenario():
        tasks = [asyncio.create_task(visao.api_medias_compras_visao_fotos(request(), "tenant-a")) for _ in range(2)]
        await entered(started)
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(result, HTTPException) and result.status_code == 503 for result in results)
        assert len(calls) == 1 and not visao._FOTOS_VISAO_EM_ANDAMENTO
        assert (await visao.api_medias_compras_visao_fotos(request(), "tenant-a"))["fotos"] == {}
        assert len(calls) == 2

    try:
        asyncio.run(scenario())
    finally:
        release.set()


@pytest.mark.parametrize("change", ["tenant", "root", "loja", "store", "skus", "catalog", "policy"])
def test_escopos_e_revisoes_distintos_nao_compartilham(ambiente, monkeypatch, change, tmp_path):
    started, both_started, release = Event(), Event(), Event()
    calls = []

    def load(client, req):
        catalog = tmp_path / "cadastro_produtos_lojas.csv"
        policy = tmp_path / "cadastro_fotos_config.json"
        photo = catalog.read_text(encoding="utf-8") if catalog.exists() else "old-photo"
        policy_value = policy.read_text(encoding="utf-8") if policy.exists() else "old-policy"
        calls.append((client, req))
        started.set()
        if len(calls) == 2:
            both_started.set()
        assert release.wait(5)
        return {"fotos": {"SKU": photo}, "politica_fotos": policy_value}

    monkeypatch.setattr(visao, "_carregar_fotos_visao_sync", load)

    async def scenario():
        first = asyncio.create_task(visao.api_medias_compras_visao_fotos(request(), "tenant-a"))
        await entered(started)
        client, changes = "tenant-a", {}
        if change == "tenant":
            client = "tenant-b"
        elif change == "root":
            other = tmp_path / "other"
            other.mkdir()
            ambiente["tenant-a"] = other
        elif change == "loja":
            changes["loja"] = "Loja B"
        elif change == "store":
            changes["store_id"] = "store-b"
        elif change == "skus":
            changes["skus"] = ["OTHER"]
        else:
            path = tmp_path / ("cadastro_produtos_lojas.csv" if change == "catalog" else "cadastro_fotos_config.json")
            path.write_text("new synthetic revision", encoding="utf-8")
        second = asyncio.create_task(visao.api_medias_compras_visao_fotos(request(**changes), client))
        await entered(both_started)
        assert len(calls) == len(visao._FOTOS_VISAO_EM_ANDAMENTO) == 2
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert isinstance(results[0], HTTPException) if change == "root" else isinstance(results[0], dict)
        assert isinstance(results[1], dict)
        if change == "catalog":
            assert results[0]["fotos"]["SKU"] == "old-photo"
            assert results[1]["fotos"]["SKU"] == "new synthetic revision"
        elif change == "policy":
            assert results[0]["politica_fotos"] == "old-policy"
            assert results[1]["politica_fotos"] == "new synthetic revision"

    try:
        asyncio.run(scenario())
    finally:
        release.set()


@pytest.mark.parametrize("limit", ["tenant", "global"])
def test_admissao_limitada_permite_seguidor_e_recusa_novo_lote(ambiente, monkeypatch, limit):
    release = Event()
    monkeypatch.setattr(visao, "_carregar_fotos_visao_sync", lambda *_args: (
        release.wait(5) and {"fotos": {}, "politica_fotos": "policy"}
    ))

    async def scenario():
        total = 2 if limit == "tenant" else 32
        clients = ["tenant-a"] * total if limit == "tenant" else [f"tenant-{i // 2}" for i in range(total)]
        tasks = [asyncio.create_task(visao.api_medias_compras_visao_fotos(request(skus=[str(i)]), client)) for i, client in enumerate(clients)]
        await asyncio.sleep(0)
        try:
            assert len(visao._FOTOS_VISAO_EM_ANDAMENTO) == total
            follower = asyncio.create_task(visao.api_medias_compras_visao_fotos(request(skus=["0"]), clients[0]))
            tasks.append(follower)
            await asyncio.sleep(0)
            with pytest.raises(HTTPException) as rejected:
                await visao.api_medias_compras_visao_fotos(request(skus=["new"]), clients[0] if limit == "tenant" else "other-tenant")
            assert rejected.value.status_code == 503
            assert rejected.value.headers == {"Retry-After": "2"}
            assert len(visao._FOTOS_VISAO_EM_ANDAMENTO) == total
        finally:
            release.set()
            await asyncio.gather(*tasks)

    asyncio.run(scenario())


def test_loops_distintos_nao_compartilham_tarefas(ambiente, monkeypatch):
    both_started, release, lock = Event(), Event(), Lock()
    calls = []

    def load(*_args):
        with lock:
            calls.append(1)
            if len(calls) == 2:
                both_started.set()
        assert release.wait(5)
        return {"fotos": {}, "politica_fotos": "policy"}

    monkeypatch.setattr(visao, "_carregar_fotos_visao_sync", load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(lambda: asyncio.run(visao.api_medias_compras_visao_fotos(request(), "tenant-a"))) for _ in range(2)]
        try:
            assert both_started.wait(5)
        finally:
            release.set()
        assert all(future.result(timeout=5)["fotos"] == {} for future in futures)
    assert len(calls) == 2
