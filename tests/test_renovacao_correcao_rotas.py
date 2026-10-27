"""Contracts that keep read-only checks separate from explicit repairs."""

import importlib
import json

import pytest
from fastapi import HTTPException

from backend.routers.renovacao import RenovacaoRouterConfig, create_renovacao_router
from backend.schemas.renovacao import RenovacaoCampanhaSincronizarRequest
from backend.services import renovacao_promocoes as service


SOURCE = "C-MLB998510001"
DESTINATION = "C-MLB998510002"
TENANT = "tenant-repair-route"
STORE = "Loja repair route"


def request(**changes):
    return RenovacaoCampanhaSincronizarRequest(
        **{
            "loja": STORE,
            "campanha_origem_id": SOURCE,
            "campanha_destino_id": DESTINATION,
            "quantidade_origem_esperada": 908,
            **changes,
        }
    )


def router_with_capture(capture):
    noop = lambda *args, **kwargs: {}
    return create_renovacao_router(RenovacaoRouterConfig(
        get_tenant_id=noop,
        listar_campanhas_usuario=noop,
        criar_ou_completar_proximo_mes=noop,
        criar_campanha_manual=noop,
        atualizar_periodo_campanha=noop,
        deletar_campanha=noop,
        sincronizar_promocao_existente=capture,
        iniciar_sincronizacao_promocao=capture,
        sync_job_get=noop,
        agendamento_atual=noop,
        agendamento_put=noop,
    ))


def endpoint_for(router, path):
    return next(route.endpoint for route in router.routes if route.path == path)


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("repair", [False, True])
def test_read_only_route_always_disables_repairs_without_mutating_request(read_only, repair):
    calls = []
    router = router_with_capture(lambda tenant, body: calls.append((tenant, body)) or {"job_id": "audit"})
    endpoint = endpoint_for(router, "/api/renovacao/conferir-promocao/iniciar")
    body = request(somente_conferir=read_only, corrigir_divergencias=repair)

    assert endpoint(body, client_id=TENANT) == {"job_id": "audit"}

    assert calls[0][0] == TENANT
    forwarded = calls[0][1]
    assert forwarded.somente_conferir is True
    assert forwarded.corrigir_divergencias is False
    assert body.somente_conferir is read_only
    assert body.corrigir_divergencias is repair


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("repair", [False, True])
def test_explicit_repair_route_cannot_be_downgraded_to_ordinary_sync(read_only, repair):
    calls = []
    router = router_with_capture(lambda tenant, body: calls.append((tenant, body)) or {"job_id": "repair"})
    endpoint = endpoint_for(router, "/api/renovacao/corrigir-divergencias/iniciar")
    body = request(somente_conferir=read_only, corrigir_divergencias=repair, client_id="other-tenant")

    assert endpoint(body, client_id=TENANT) == {"job_id": "repair"}

    assert calls[0][0] == TENANT
    forwarded = calls[0][1]
    assert forwarded.somente_conferir is False
    assert forwarded.corrigir_divergencias is True
    assert forwarded.quantidade_origem_esperada == 908
    assert forwarded.campanha_origem_id == SOURCE
    assert forwarded.campanha_destino_id == DESTINATION
    assert "client_id" not in forwarded.model_dump()
    assert body.somente_conferir is read_only
    assert body.corrigir_divergencias is repair


@pytest.mark.parametrize("path", [
    "/api/renovacao/sincronizar-promocao",
    "/api/renovacao/sincronizar-promocao/iniciar",
])
@pytest.mark.parametrize("read_only", [False, True])
def test_ordinary_routes_reject_repair_flag_before_dispatch(path, read_only):
    calls = []
    router = router_with_capture(lambda *args, **kwargs: calls.append((args, kwargs)))
    endpoint = endpoint_for(router, path)

    with pytest.raises(HTTPException) as error:
        endpoint(request(somente_conferir=read_only, corrigir_divergencias=True), client_id=TENANT)

    assert error.value.status_code == 409
    assert calls == []


def test_job_start_rejects_ambiguous_mode_before_creating_job_or_thread(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("An ambiguous mode must not create or start a job")

    monkeypatch.setattr(service, "_renovacao_sync_jobs_limpar_antigos", forbidden)
    monkeypatch.setattr(service, "_renovacao_sync_job_update", forbidden)
    monkeypatch.setattr(service.threading, "Thread", forbidden)

    with pytest.raises(HTTPException) as error:
        service._renovacao_sincronizar_promocao_iniciar_payload(
            TENANT, request(somente_conferir=True, corrigir_divergencias=True))

    assert error.value.status_code == 400


def worker_capture(monkeypatch, tmp_path):
    updates = []
    monkeypatch.setattr(service.ctx, "get_tenant_path", lambda tenant: str(tmp_path / tenant))
    monkeypatch.setattr(service, "_renovacao_sync_job_update", lambda job, **data: updates.append(data))
    return updates


def test_worker_dispatches_repair_with_authenticated_scope_and_records_partial_result(monkeypatch, tmp_path):
    correction = importlib.import_module("backend.services.renovacao_correcao")
    calls = []

    def repair(*args, **kwargs):
        calls.append((args, kwargs))
        return {
            "success": False,
            "corrigir_divergencias": True,
            "somente_conferir": False,
            "conferencia": {"contagem_coerente": False, "confirmados": 1098},
        }

    def forbidden(*args, **kwargs):
        pytest.fail("Repair jobs must not run the full-copy sync")

    monkeypatch.setattr(correction, "_renovacao_corrigir_divergencias_promocao", repair)
    monkeypatch.setattr(service, "_renovacao_sincronizar_promocao_existente", forbidden)
    updates = worker_capture(monkeypatch, tmp_path)

    service._renovacao_sincronizar_promocao_worker(TENANT, "repair-job", request(
        somente_conferir=False, corrigir_divergencias=True).model_dump())

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:4] == (TENANT, STORE, SOURCE, DESTINATION)
    assert kwargs["quantidade_origem_esperada"] == 908
    assert "somente_conferir" not in kwargs
    assert callable(kwargs["progress_callback"])
    final = updates[-1]
    assert final["status"] == "done"
    assert final["etapa"] == "Pendencias"
    assert final["result"]["success"] is False
    report_path = tmp_path / TENANT / "renovacao_auditorias" / "repair-job.json"
    stored = json.loads(report_path.read_text(encoding="utf-8"))
    assert stored["corrigir_divergencias"] is True
    assert stored["somente_conferir"] is False
    assert stored["resultado"]["conferencia"]["contagem_coerente"] is False
    assert not (tmp_path / "other-tenant").exists()


def test_worker_rejects_ambiguous_flags_without_calling_any_operation(monkeypatch, tmp_path):
    correction = importlib.import_module("backend.services.renovacao_correcao")

    def forbidden(*args, **kwargs):
        pytest.fail("An ambiguous job must not call either operation")

    monkeypatch.setattr(correction, "_renovacao_corrigir_divergencias_promocao", forbidden)
    monkeypatch.setattr(service, "_renovacao_sincronizar_promocao_existente", forbidden)
    updates = worker_capture(monkeypatch, tmp_path)

    service._renovacao_sincronizar_promocao_worker(TENANT, "ambiguous-job", request(
        somente_conferir=True, corrigir_divergencias=True).model_dump())

    assert updates[-1]["status"] == "error"
    assert updates[-1]["etapa"] == "Erro"


def test_read_only_worker_never_dispatches_correction(monkeypatch, tmp_path):
    correction = importlib.import_module("backend.services.renovacao_correcao")
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("Read-only workers must not call correction")

    def audit(*args, **kwargs):
        calls.append((args, kwargs))
        return {"success": True, "somente_conferir": True}

    monkeypatch.setattr(correction, "_renovacao_corrigir_divergencias_promocao", forbidden)
    monkeypatch.setattr(service, "_renovacao_sincronizar_promocao_existente", audit)
    updates = worker_capture(monkeypatch, tmp_path)

    service._renovacao_sincronizar_promocao_worker(TENANT, "audit-job", request(
        somente_conferir=True, corrigir_divergencias=False).model_dump())

    assert len(calls) == 1
    assert calls[0][1]["somente_conferir"] is True
    assert updates[-1]["result"]["success"] is True
