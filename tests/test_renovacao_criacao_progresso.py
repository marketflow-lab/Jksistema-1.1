"""Offline contracts for asynchronous monthly creation and real progress."""

import contextvars
import copy
import threading
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

from backend.routers.renovacao import RenovacaoRouterConfig, create_renovacao_router
from backend.schemas.renovacao import RenovacaoCampanhaCriarRequest
from backend.services import renovacao_promocoes as service
from backend.services import renovacao_promocoes_helpers as helpers


TENANT = "tenant-creation-progress"
STORE = "Loja creation progress"
SOURCE = "C-MLB998610001"
DESTINATION = "C-MLB998610002"
ITEMS = ["MLB998610101", "MLB998610102", "MLB998610103", "MLB998610104"]


@pytest.fixture(autouse=True)
def offline_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A progress test must not call a real API")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(service.ctx, "_ml_api_request", forbidden)
    monkeypatch.setattr(service.ctx, "_ml_obter_promocoes_item", forbidden)
    monkeypatch.setattr(service.ctx, "_cache_invalidar_loja", lambda *args: None)
    monkeypatch.setattr(service.ctx, "_parse_float_flex", lambda value: float(value) if value is not None else None)
    monkeypatch.setattr(service.ctx, "logger", SimpleNamespace(error=lambda *args, **kwargs: None,
                                                             debug=lambda *args, **kwargs: None))
    monkeypatch.setattr(service.ctx, "RENOVACAO_SYNC_JOBS", {})
    monkeypatch.setattr(service.ctx, "RENOVACAO_SYNC_JOBS_LOCK", threading.Lock())


def request(**changes):
    return RenovacaoCampanhaCriarRequest(**{
        "loja": STORE, "campanha_id": SOURCE, "nome": "Promo Novembro",
        "quantidade_origem_esperada": 2, **changes,
    })


def router(**overrides):
    noop = lambda *args, **kwargs: {}
    return create_renovacao_router(RenovacaoRouterConfig(**{
        "get_tenant_id": noop, "listar_campanhas_usuario": noop,
        "criar_ou_completar_proximo_mes": noop, "criar_campanha_manual": noop,
        "atualizar_periodo_campanha": noop, "deletar_campanha": noop,
        "sincronizar_promocao_existente": noop, "iniciar_sincronizacao_promocao": noop,
        "sync_job_get": helpers._renovacao_sync_job_get, "agendamento_atual": noop,
        "agendamento_put": noop, **overrides,
    }))


def endpoint(routes, path):
    return next(route.endpoint for route in routes.routes if route.path == path)


@pytest.mark.parametrize("existing", [False, True])
def test_monthly_creation_maps_real_phases_without_regression_or_result_changes(monkeypatch, existing):
    source = {"id": SOURCE, "name": "Promo Outubro", "active_count": 2}
    destination = {"id": DESTINATION, "name": "Promo Novembro",
                   "start_date": "2026-11-01T03:00:00Z", "finish_date": "2026-12-01T02:59:59Z"} if existing else None
    items = [{"id": item} for item in ITEMS[:2]]
    raw = {item: {"id": item} for item in ITEMS[:2]}
    copied = {"success": False, "incluidos": 1, "falhas_total": 1,
              "conferencia": {"completa": False, "itens": []}}
    events, mutations = [], []
    monkeypatch.setattr(service, "_renovacao_ml_listar_campanhas_usuario", lambda *args: ([source], {}))
    target_dates = ("2026-11-01T00:00:00", "2026-11-30T23:59:59")
    monkeypatch.setattr(service, "_renovacao_datas_proximo_mes", lambda *args: target_dates)
    monkeypatch.setattr(service, "_renovacao_encontrar_campanha_destino", lambda *args: destination)

    def listing(*args, **kwargs):
        assert kwargs["expected_count"] == 2
        callback = kwargs["progress_callback"]
        helpers._renovacao_emitir_progresso(callback, "Conferencia", kwargs["progress_start"],
                                            "Conferindo origem.", processados=0, total=2)
        helpers._renovacao_emitir_progresso(callback, "Conferencia", kwargs["progress_end"],
                                            "Origem conferida.", processados=2, total=2)
        return items, raw, {}

    def create(*args, **kwargs):
        assert kwargs["datas_alvo"] == target_dates
        assert kwargs["confirmar_periodo"] is True
        mutations.append("created")
        return DESTINATION, {"name": "Promo Novembro", "start_date": target_dates[0], "finish_date": target_dates[1]}, {}

    def copy_items(*args, **kwargs):
        assert kwargs["origem_preparada"] == (items, raw)
        assert kwargs["expected_origem_count"] == 2
        assert kwargs["verificar_destino"] is existing
        for stage, percent in [("Conferencia", 18), ("Conferencia", 48),
                               ("Sincronizacao", 60), ("Sincronizacao", 90),
                               ("Conferencia", 91), ("Conferencia", 97)]:
            helpers._renovacao_emitir_progresso(kwargs["progress_callback"], stage, percent,
                                                "Etapa concluida.", processados=2, total=2)
        return copy.deepcopy(copied), {}

    monkeypatch.setattr(service, "_renovacao_listar_itens_campanha", listing)
    monkeypatch.setattr(service, "_renovacao_criar_campanha_ml", create)
    monkeypatch.setattr(service, "_renovacao_copiar_mlbs_campanha", copy_items)
    result = service._renovacao_criar_ou_completar_proximo_mes(
        TENANT, STORE, SOURCE, "Promo Novembro", quantidade_origem_esperada=2,
        progress_callback=events.append,
    )

    percentages = [event["percentual"] for event in events]
    assert percentages == sorted(percentages)
    assert percentages[0] == 2 and percentages[-1] == 98
    assert {"Conferindo origem", "Preparando campanha", "Copiando anuncios",
            "Conferindo participacoes", "Conferindo resultado", "Finalizando"} <= {
                event["etapa"] for event in events}
    assert any(event.get("processados") == 2 and event.get("total") == 2 for event in events)
    assert mutations == ([] if existing else ["created"])
    assert result["nova_campanha"]["id"] == DESTINATION
    assert result["campanha_existente"] is existing
    assert {key: result[key] for key in copied} == copied


def test_copy_counts_only_completed_attempts_including_skips_and_failures(monkeypatch):
    timeline, progress = [], []
    records = {item: {SOURCE: {"situacao": "participante", "raw": {"id": item}},
                      DESTINATION: {"situacao": "participante" if item == ITEMS[1] else "ausente",
                                    "raw": {"id": item}}} for item in ITEMS}

    def confirm(client, store, cfg, item_ids, campaigns, stats, *args):
        stats["consultas"] = copy.deepcopy(records)
        return stats["consultas"], cfg

    def discount(item, *args):
        timeline.append(("attempt", item))
        return (None if item == ITEMS[0] else 20), None, 80

    def add(client, store, cfg, item, *args, **kwargs):
        timeline.append(("mutation", item))
        if item == ITEMS[3]:
            raise HTTPException(502, "Falha simulada")
        return False, "Falha simulada", cfg

    def callback(event):
        if event["etapa"] == "Sincronizacao":
            processed = event["processados"]
            assert len([entry for entry in timeline if entry[0] == "attempt"]) == processed
            progress.append(event)

    monkeypatch.setattr(service, "_renovacao_confirmar_lote", confirm)
    monkeypatch.setattr(service, "_renovacao_desconto_item", discount)
    monkeypatch.setattr(service, "_renovacao_participacao_mantem_desconto", lambda *args: True)
    monkeypatch.setattr(service, "_renovacao_preco_base_atual", lambda client, store, cfg, *args: (100, cfg))
    monkeypatch.setattr(service, "_renovacao_adicionar_item_campanha", add)
    monkeypatch.setattr(service.ctx, "_ml_buscar_itens_batch", lambda client, store, cfg, *args: ([], cfg))
    monkeypatch.setattr(service, "_renovacao_montar_conferencia", lambda *args: {
        "completa": False, "total_origem": 4, "inconclusivos": [],
        "itens": [{"item_id": item, "confirmado": False, "erro": "Pendente"} for item in ITEMS],
    })

    result, _ = service._renovacao_copiar_mlbs_campanha(
        TENANT, STORE, {}, SOURCE, DESTINATION, expected_origem_count=4,
        origem_preparada=([{"id": item} for item in ITEMS], {}), progress_callback=callback,
    )

    assert [event["processados"] for event in progress] == [0, 1, 2, 3, 4]
    assert all(event["total"] == 4 for event in progress)
    assert [item for action, item in timeline if action == "mutation"] == ITEMS[2:]
    assert result["success"] is False


def test_confirmation_counts_after_each_finished_lookup(monkeypatch):
    finished, events = [], []

    def lookup(client, store, cfg, item, campaigns):
        finished.append(item)
        return {SOURCE: {"situacao": "ausente"}}, cfg

    def callback(event):
        assert event["processados"] <= len(finished)
        events.append(event)

    monkeypatch.setattr(helpers, "_renovacao_consultar_participacoes", lookup)
    helpers._renovacao_confirmar_lote(TENANT, STORE, {}, ITEMS + ITEMS[:1], [SOURCE],
                                     progress_callback=callback)

    assert [event["processados"] for event in events] == [1, 2, 3, 4]
    assert all(event["total"] == 4 for event in events)


@pytest.mark.parametrize("complete", [False, True])
def test_worker_finishes_with_original_result_and_distinguishes_pending(monkeypatch, complete):
    expected = {"success": complete, "nova_campanha": {"id": DESTINATION},
                "conferencia": {"completa": complete}, "incluidos": 2 if complete else 1}
    updates = []
    original_update = service._renovacao_sync_job_update

    def capture(job, **data):
        updates.append(data)
        original_update(job, **data)

    def execute(client, store, campaign, name, kind, **kwargs):
        assert (client, store, campaign) == (TENANT, STORE, SOURCE)
        assert kwargs["quantidade_origem_esperada"] == 2
        callback = kwargs["progress_callback"]
        callback({"etapa": "Copiando anuncios", "percentual": 62, "processados": 1, "total": 2})
        callback({"etapa": "Conferindo resultado", "percentual": 40})
        callback({"etapa": "Finalizando", "percentual": 100})
        return copy.deepcopy(expected)

    monkeypatch.setattr(service, "_renovacao_sync_job_update", capture)
    monkeypatch.setattr(service, "_renovacao_criar_ou_completar_proximo_mes", execute)
    service._renovacao_criar_proximo_mes_worker(TENANT, "job", request().model_dump())

    assert [update["percentual"] for update in updates] == [62, 62, 99, 100]
    assert updates[1]["processados"] is None and updates[1]["total"] is None
    job = helpers._renovacao_sync_job_get("job")
    assert job["status"] == "done" and job["progress"] == 100
    assert job["etapa"] == ("Concluido" if complete else "Pendencias")
    assert job["result"] == expected
    assert job["error"] == ""


@pytest.mark.parametrize("error", [HTTPException(502, "Falha Bearer segredo123"),
                                    RuntimeError("access_token=segredo123")])
def test_worker_error_preserves_actual_progress_and_hides_credentials(monkeypatch, error):
    def execute(*args, **kwargs):
        kwargs["progress_callback"]({"etapa": "Copiando anuncios", "percentual": 63,
                                     "processados": 1, "total": 2})
        raise error

    monkeypatch.setattr(service, "_renovacao_criar_ou_completar_proximo_mes", execute)
    service._renovacao_criar_proximo_mes_worker(TENANT, "job", request().model_dump())

    job = helpers._renovacao_sync_job_get("job")
    assert job["status"] == "error"
    assert job["percentual"] == job["progress"] == 63
    assert job["processados"] == 1 and job["total"] == 2
    assert "segredo123" not in str(job)
    assert job["mensagem"] == job["error"]


def test_start_keeps_tenant_store_operation_and_initiating_request_context(monkeypatch):
    authorization = contextvars.ContextVar("creation_authorization", default="missing")
    calls, threads = [], []

    def worker(client, job_id, payload):
        calls.append((client, job_id, payload, authorization.get()))

    class DeferredThread:
        def __init__(self, **kwargs):
            self.options = kwargs
            threads.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(service, "_renovacao_criar_proximo_mes_worker", worker)
    monkeypatch.setattr(service.threading, "Thread", DeferredThread)
    token = authorization.set("request-only")
    try:
        result = service._renovacao_criar_proximo_mes_iniciar_payload(
            TENANT, request(client_id="other-tenant"))
    finally:
        authorization.reset(token)

    job = helpers._renovacao_sync_job_get(result["job_id"])
    assert job["client_id"] == TENANT and job["loja"] == STORE
    assert job["operacao"] == "criar_proximo_mes" and job["campanha_origem_id"] == SOURCE
    assert job["status"] == "running" and job["percentual"] == 0
    assert job["processados"] is None and job["total"] is None
    assert calls == [] and len(threads) == 1 and threads[0].started
    options = threads[0].options
    options["target"](*options["args"])
    assert calls[0][0] == TENANT and calls[0][3] == "request-only"
    assert "client_id" not in calls[0][2]
    assert options["daemon"] is True


@pytest.mark.parametrize("changes,status", [({"loja": " "}, 400), ({"campanha_id": ""}, 400),
                                           ({"quantidade_origem_esperada": None}, 409)])
def test_start_rejects_invalid_scope_or_missing_count_before_thread(monkeypatch, changes, status):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid creation cannot create a job or thread")

    monkeypatch.setattr(service, "_renovacao_sync_job_update", forbidden)
    monkeypatch.setattr(service.threading, "Thread", forbidden)
    with pytest.raises(HTTPException) as raised:
        service._renovacao_criar_proximo_mes_iniciar_payload(TENANT, request(**changes))
    assert raised.value.status_code == status


def test_thread_start_failure_does_not_leave_running_job(monkeypatch):
    class BrokenThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("Simulated thread failure")

    monkeypatch.setattr(service.threading, "Thread", BrokenThread)
    with pytest.raises(HTTPException) as raised:
        service._renovacao_criar_proximo_mes_iniciar_payload(TENANT, request())
    assert raised.value.status_code == 503
    job = next(iter(service.ctx.RENOVACAO_SYNC_JOBS.values()))
    assert job["status"] == "error" and job["percentual"] == 0


def test_start_route_uses_authenticated_tenant_and_expected_count():
    calls = []
    routes = router(iniciar_criacao_proximo_mes=lambda *args: calls.append(args) or {"job_id": "job"})
    start = endpoint(routes, "/api/renovacao/criar-proximo-mes/iniciar")
    body = request(client_id="other-tenant")
    assert start(body, client_id=TENANT) == {"job_id": "job"}
    assert calls == [(TENANT, body)]
    with pytest.raises(HTTPException) as raised:
        start(request(quantidade_origem_esperada=None), client_id=TENANT)
    assert raised.value.status_code == 409 and len(calls) == 1


@pytest.mark.parametrize("client,operation", [("other-tenant", "criar_proximo_mes"),
                                              (TENANT, None), (TENANT, "sincronizar_promocao")])
def test_creation_progress_rejects_other_tenant_or_operation(client, operation):
    helpers._renovacao_sync_job_update("job", client_id=client, operacao=operation,
                                       result={"private": "hidden"})
    read = endpoint(router(), "/api/renovacao/criar-proximo-mes/progresso/{job_id}")
    with pytest.raises(HTTPException) as raised:
        read("job", client_id=TENANT)
    assert raised.value.status_code == 404
    assert "hidden" not in str(raised.value.detail)


def test_progress_preserves_registry_and_old_sync_route_rejects_creation():
    stored = {"client_id": TENANT, "operacao": "criar_proximo_mes", "loja": STORE,
              "status": "running", "percentual": 41}
    routes = router(sync_job_get=lambda *args: stored)
    read = endpoint(routes, "/api/renovacao/criar-proximo-mes/progresso/{job_id}")
    result = read("job", client_id=TENANT)
    assert "client_id" not in result and stored["client_id"] == TENANT
    assert result["loja"] == STORE
    old_read = endpoint(routes, "/api/renovacao/sincronizar-promocao/progresso/{job_id}")
    with pytest.raises(HTTPException) as raised:
        old_read("job", client_id=TENANT)
    assert raised.value.status_code == 404


def test_legacy_router_configuration_and_synchronous_creation_stay_available():
    calls = []
    routes = router(criar_ou_completar_proximo_mes=lambda *args, **kwargs:
                    calls.append((args, kwargs)) or {"success": True})
    assert endpoint(routes, "/api/renovacao/criar-proximo-mes")(request(), client_id=TENANT) == {"success": True}
    assert calls[0][0][:3] == (TENANT, STORE, SOURCE)
    assert calls[0][1] == {"quantidade_origem_esperada": 2}
    with pytest.raises(HTTPException) as raised:
        endpoint(routes, "/api/renovacao/criar-proximo-mes/iniciar")(request(), client_id=TENANT)
    assert raised.value.status_code == 503
