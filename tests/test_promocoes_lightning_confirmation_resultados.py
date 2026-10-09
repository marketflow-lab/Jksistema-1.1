"""Offline batch, persistence and authenticated HTTP tests for late LIGHTNING results."""
import copy
import inspect
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.services import promocoes_api_participacoes as participation
from backend.services import mercadolivre_legacy_promocoes as legacy
from backend.services import promocoes_lightning_resultados as results
from backend.services.promocoes_lightning_acao import lightning_row_fields
from backend.schemas.promocoes import PromoAplicarParticipacaoRequest
from tests.test_promocoes_seller_campaign_reference import offline_runtime

TENANT = "tenant-confirmation-test"
STORE = "store-confirmation-test"
SELLER = "111"
CAMPAIGN = "LGH-MLB1000"
ANALYSIS = "a" * 32
ITEMS = [f"MLB999111{i:03}" for i in range(16)]
PRICE = 147.46


def candidate(item=ITEMS[-1], **changes):
    return {"id": CAMPAIGN, "type": "LIGHTNING", "item_id": item,
            "status": "candidate", "ref_id": f"CANDIDATE-{item}-UNIT",
            "price": PRICE, "original_price": 199.88,
            "min_discounted_price": 39.98, "max_discounted_price": 179.90,
            "suggested_discounted_price": 169.90, "stock": {"min": 5, "max": 20},
            **changes}


def detail(item, outcome, ref=None):
    return {"client_ref": ref or item, "item_id": item, "promotion_id": CAMPAIGN,
            "promotion_type": "LIGHTNING", "outcome": outcome,
            "success": False, "ignored": False, "error": "Previous error",
            "message": "Previous error", "price_diagnostics": {"attempt_count": 1}}


def batch_result():
    return {"loja": STORE, "success": False, "total_itens": 16, "total_sucesso": 0,
            "total_falha": 16, "total_ignorados": 0,
            "detalhes": [detail(item, "rejected") for item in ITEMS[:-1]] + [detail(ITEMS[-1], "unknown")],
            "promocoes": [{"promotion_id": CAMPAIGN, "promotion_type": "LIGHTNING",
                           "nome": "Ofertas relampago", "sucesso": 0, "falhas": 16,
                           "ignorados": 0, "erros": ["Previous error"]}]}


@pytest.fixture
def confirmation_flow(monkeypatch):
    state = SimpleNamespace(jobs={}, gets=[], posts=[], allow_post=False, phase="candidate",
                            records=None, cfg={"user_id": SELLER, "site_id": "MLB",
                                               "access_token": "synthetic-private-value"})

    def config(client, store):
        assert (client, store) == (TENANT, STORE)
        return copy.deepcopy(state.cfg)

    def get_job(job_id):
        return copy.deepcopy(state.jobs.get(job_id, {}))

    def set_job(job_id, **job):
        state.jobs[job_id] = copy.deepcopy(job)
        return copy.deepcopy(job)

    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store) == (TENANT, STORE)
        assert str(cfg["user_id"]) == SELLER
        if method == "POST":
            assert state.allow_post, "Confirmation must never resubmit"
            item = url.rsplit("/", 1)[-1]
            state.posts.append((item, copy.deepcopy(kwargs["json"])))
            if item == ITEMS[-1]:
                body, status = {"price": PRICE, "original_price": 199.88}, 201
            else:
                body, status = {"message": "ERROR_CREDIBILITY_DISCOUNTED_PRICE", "error": "bad_request"}, 400
            return SimpleNamespace(status_code=status, json=lambda: copy.deepcopy(body)), cfg
        assert method == "GET", "Unexpected mutation method"
        state.gets.append((url, copy.deepcopy(kwargs)))
        if url.endswith("/prices"):
            item = url.split("/")[-2]
            body = {"id": item, "prices": [{"type": "standard", "amount": 199.88,
                    "currency_id": "BRL", "conditions": {"context_restrictions": ["channel_marketplace"]}}]}
        elif url.endswith("/sale_price"):
            body = {"currency_id": "BRL", "amount": 153.16}
        else:
            item = url.rsplit("/", 1)[-1]
            raw = candidate(item)
            if state.phase == "pending" and item == ITEMS[-1]:
                raw.update(status="pending", ref_id=f"OFFER-{item}-CONFIRMED",
                           start_date="2030-01-01T12:00:00-03:00",
                           finish_date="2030-01-01T18:00:00-03:00")
            body = copy.deepcopy(state.records if state.records is not None else [raw])
        return SimpleNamespace(status_code=200, json=lambda: copy.deepcopy(body)), cfg

    def records(client, store, cfg, item, request_fn=None, **kwargs):
        return legacy._ml_obter_promocoes_item(client, store, cfg, item,
                                              request_fn=request_fn or request, **kwargs)

    monkeypatch.setattr(participation, "_obter_cfg_ml", config, raising=False)
    monkeypatch.setattr(participation, "_promo_job_get", get_job, raising=False)
    monkeypatch.setattr(participation, "_promo_job_set", set_job, raising=False)
    monkeypatch.setattr(participation, "_ml_api_request", request, raising=False)
    monkeypatch.setattr(participation, "_ml_obter_promocoes_item", records, raising=False)
    monkeypatch.setattr(participation, "_promo_erro_candidate_not_found", legacy._promo_erro_candidate_not_found, raising=False)

    def register(result=None, raw=None, ref=None, pending=None, analysis_id=ANALYSIS):
        raw = raw or candidate()
        pending = pending if pending is not None else [
            {"client_ref": ref or ITEMS[-1], "campaign": CAMPAIGN, "item_id": ITEMS[-1],
             "raw": raw, "price": PRICE}]
        result = results.register_confirmation(participation, TENANT, STORE, state.cfg,
                                                analysis_id, result or batch_result(), pending)
        state.confirmation_id = result.get("confirmation_id")
        return result

    state.register = register
    return state


def test_batch_16_201_unknown_later_pending_updates_result_without_resubmitting(confirmation_flow):
    state = confirmation_flow
    selected = []
    for item in ITEMS:
        raw = participation._validar_contexto_entry_campanha(candidate(item),
                campaign_id=CAMPAIGN, promotion_type_consultado="LIGHTNING", item_id=item)
        selected.append({"client_ref": item, "item_id": item, "deal_price": PRICE,
                         "offer_id": raw["ref_id"], "lightning_stock": 5,
                         **lightning_row_fields(raw, "LIGHTNING", CAMPAIGN, item)})
    req = PromoAplicarParticipacaoRequest(loja=STORE, analysis_job_id=ANALYSIS,
        promocoes=[{"promotion_id": CAMPAIGN, "promotion_type": "LIGHTNING",
                    "nome": "Ofertas relampago", "items": selected}])
    state.allow_post = True
    initial = participation.aplicar_participacoes_promocoes(req, TENANT)
    state.allow_post = False
    assert len(state.posts) == 16
    assert initial["total_sucesso"] == 0 and initial["total_falha"] == 16
    assert [d["outcome"] for d in initial["detalhes"]] == ["rejected"] * 15 + ["unknown"]
    assert initial["confirmation_pending"] is True
    state.phase = "pending"
    state.gets.clear()
    refreshed = participation.confirmar_participacoes_promocoes(initial["confirmation_id"], STORE, TENANT)
    assert len(state.posts) == 16 and len(state.gets) == 1
    _url, request_options = state.gets[0]
    assert request_options["retry_unauthorized"] is False
    assert request_options["params"] == {"app_version": "v2"}
    assert 0 < request_options["timeout"] <= 6.0
    assert refreshed["total_sucesso"] == 1 and refreshed["total_falha"] == 15
    assert refreshed["confirmation_pending"] is False
    assert refreshed["promocoes"][0]["nome"] == "Ofertas relampago"
    assert refreshed["promocoes"][0]["sucesso"] == 1
    confirmed = refreshed["detalhes"][-1]
    assert confirmed["success"] and confirmed["outcome"] == "applied"
    assert confirmed["status"] == "pending" and "error" not in confirmed
    assert confirmed["analysis_row_updates"]["action_ja_participa"] is True
    assert confirmed["price_diagnostics"]["decision_code"] == "PARTICIPATION_CONFIRMED_BY_REFRESH"
    assert all(d["outcome"] == "rejected" for d in refreshed["detalhes"][:-1])
    state.gets.clear()
    replay = participation.confirmar_participacoes_promocoes(initial["confirmation_id"], STORE, TENANT)
    assert replay == refreshed and state.gets == []


def test_record_is_minimal_without_provider_or_credentials(confirmation_flow):
    raw = candidate(title="synthetic-title", access_token="synthetic-private-value", seller_email="synthetic-email")
    raw["_jk_lightning_preexisting_participations"] = [candidate(
        ref_id=f"OFFER-{ITEMS[-1]}-PREVIOUS", status="pending", price=140,
        title="synthetic-title", access_token="synthetic-private-value")]
    first = confirmation_flow.register(raw=raw)
    record = confirmation_flow.jobs[first["confirmation_id"]]["lightning_confirmation_contexts"][ITEMS[-1]]
    encoded = json.dumps(confirmation_flow.jobs)
    assert not any(value in encoded for value in ("synthetic-private-value", "synthetic-title", "synthetic-email", "access_token"))
    assert set(record["raw"]) <= {"ref_id", "price", "start_date", "finish_date", "_jk_lightning_preexisting_participations"}
    assert record["price_cents"] == 14746
    assert record["submission_attempted"] is True and record["outcome"] == "unknown"


@pytest.mark.parametrize("condition,status", [("tenant", 404), ("store", 409), ("seller", 409),
                                               ("missing", 404), ("malformed", 404), ("ttl", 410), ("kind", 404)])
def test_confirmation_scope_and_expiration_block_before_provider_read(confirmation_flow, condition, status):
    state = confirmation_flow
    first = state.register()
    job_id, tenant, store = first["confirmation_id"], TENANT, STORE
    if condition == "tenant": tenant = "different-tenant"
    elif condition == "store": store = "different-store"
    elif condition == "seller": state.cfg["user_id"] = "222"
    elif condition == "missing": job_id = "b" * 32
    elif condition == "malformed": job_id = "../private"
    elif condition == "kind": state.jobs[job_id]["kind"] = "different-job-kind"
    elif condition == "ttl":
        record = state.jobs[job_id]["lightning_confirmation_contexts"][ITEMS[-1]]
        record["issued_at"] -= 90000
        record["expires_at"] -= 90000
    state.gets.clear()
    with pytest.raises(HTTPException) as error:
        participation.confirmar_participacoes_promocoes(job_id, store, tenant)
    assert error.value.status_code == status
    assert state.gets == [] and state.posts == []


@pytest.mark.parametrize("analysis_id", ["untrusted", "a" * 31])
def test_invalid_analysis_job_cannot_register_confirmation(confirmation_flow, analysis_id):
    initial = confirmation_flow.register(analysis_id=analysis_id)
    assert "confirmation_id" not in initial
    assert not confirmation_flow.jobs and not confirmation_flow.gets


@pytest.mark.parametrize("field,value", [("min_discounted_price", 39.99),
                                         ("max_discounted_price", 179.89),
                                         ("suggested_discounted_price", 169.89)])
def test_duplicate_candidate_conflicting_price_terms_are_detected(field, value):
    raw = candidate()
    records = [raw, {**raw, field: value}]
    offers = participation._promo_lightning_scoped_offers(records, CAMPAIGN, ITEMS[-1], {"user_id": SELLER})
    assert len(offers) == 1
    assert participation._promo_lightning_conflicting_keys(records, CAMPAIGN, ITEMS[-1], {"user_id": SELLER}) == set(offers)


def test_duplicate_client_reference_does_not_confirm_other_item(confirmation_flow):
    initial = batch_result()
    initial["detalhes"][-2] = detail(ITEMS[-2], "unknown", ref=ITEMS[-1])
    pending = [{"client_ref": ITEMS[-1], "campaign": CAMPAIGN, "item_id": item,
                "raw": candidate(item), "price": PRICE} for item in [ITEMS[-1], ITEMS[-2]]]
    first = confirmation_flow.register(result=initial, pending=pending)
    confirmation_flow.phase = "pending"
    refreshed = participation.confirmar_participacoes_promocoes(first["confirmation_id"], STORE, TENANT)
    assert refreshed["detalhes"][-1]["outcome"] == "applied"
    assert refreshed["detalhes"][-2]["outcome"] == "unknown"
    assert refreshed["total_sucesso"] == 1 and refreshed["total_falha"] == 15
    assert all(ITEMS[-2] not in url for url, _ in confirmation_flow.gets)


def test_rejected_only_batch_has_no_confirmation_registration(confirmation_flow):
    initial = batch_result()
    initial["detalhes"][-1]["outcome"] = "rejected"
    result = confirmation_flow.register(result=initial)
    assert "confirmation_id" not in result and not confirmation_flow.jobs


def test_pending_refresh_cannot_mutate_registration_input(confirmation_flow):
    result, raw = batch_result(), candidate()
    previous_result, previous_raw = copy.deepcopy(result), copy.deepcopy(raw)
    first = confirmation_flow.register(result=result, raw=raw)
    assert first["confirmation_pending"]
    assert result == previous_result and raw == previous_raw


def test_http_confirmation_route_uses_tenant_auth_and_existing_contracts(confirmation_flow, monkeypatch):
    from backend.routers.promocoes import create_promocoes_router
    router = create_promocoes_router()
    expected = {
        ("/api/promo/aplicar-participacoes", "POST"),
        ("/api/promo/aplicar-participacoes/start", "POST"),
        ("/api/promo/aplicar-participacoes/jobs/{job_id}", "GET"),
        ("/api/promo/confirmar-participacoes/{confirmation_id}", "GET"),
    }
    assert expected <= {(route.path, method) for route in router.routes for method in route.methods}
    assert list(inspect.signature(participation.aplicar_participacoes_promocoes).parameters) == ["req", "client_id"]
    assert list(inspect.signature(participation.aplicar_participacoes_promocoes_start).parameters) == ["req", "client_id"]
    assert list(inspect.signature(participation.aplicar_participacoes_promocoes_job).parameters) == ["job_id", "client_id"]
    assert set(PromoAplicarParticipacaoRequest.model_fields) == {"loja", "promocoes", "analysis_job_id", "price_policy"}
    async def authenticate(request, authorization):
        if authorization != "Bearer synthetic-session":
            raise HTTPException(status_code=401, detail="Authentication required")
        return TENANT
    monkeypatch.setattr(participation, "_PROMOCOES_RUNTIME_GET_TENANT_ID", authenticate, raising=False)
    app = FastAPI()
    app.include_router(router)
    first = confirmation_flow.register()
    confirmation_flow.phase = "pending"
    confirmation_flow.gets.clear()
    path = f"/api/promo/confirmar-participacoes/{first['confirmation_id']}"
    with TestClient(app) as client:
        unauthenticated = client.get(path, params={"loja": STORE})
        assert unauthenticated.status_code == 401
        assert confirmation_flow.gets == []
        response = client.get(path, params={"loja": STORE}, headers={"Authorization": "Bearer synthetic-session"})
        assert response.status_code == 200
        assert response.json()["total_sucesso"] == 1
        assert len(confirmation_flow.gets) == 1
        assert client.post(path, headers={"Authorization": "Bearer synthetic-session"}).status_code == 405
    assert confirmation_flow.posts == []


@pytest.mark.parametrize("analysis_id", [None, ""])
def test_legacy_exact_mode_materializes_internal_confirmation_identity(confirmation_flow, analysis_id):
    first = confirmation_flow.register(analysis_id=analysis_id)
    assert first["confirmation_pending"] is True
    job = confirmation_flow.jobs[first["confirmation_id"]]
    assert len(job["analysis_job_id"]) == 32
    assert job["analysis_job_id"] != first["confirmation_id"]
    record = job["lightning_confirmation_contexts"][ITEMS[-1]]
    assert record["analysis_job_id"] == job["analysis_job_id"]
    confirmation_flow.phase = "pending"
    result = participation.confirmar_participacoes_promocoes(first["confirmation_id"], STORE, TENANT)
    assert result["total_sucesso"] == 1 and confirmation_flow.posts == []
