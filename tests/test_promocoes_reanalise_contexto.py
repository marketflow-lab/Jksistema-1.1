"""Offline regressions for the server-owned source of LIGHTNING reanalysis."""
import ast
import asyncio
import copy
import json
import os
import threading
import time
import types
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.services import promocoes_api_analise as analysis, promocoes_api_jobs as jobs
from backend.services import promocoes_reanalise_contexto as contexts
from backend.services.promocoes_lightning_acao import lightning_row_fields
from tests import test_promocoes_desconto_ml_fluxo as flow
from tests.test_promocoes_lightning_fluxo import offer, aggregate, CAMPAIGN, ITEM_ID

JOB = "a" * 32
CFG = {"user_id": "123", "site_id": "MLB", "access_token": "not-retained"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network is forbidden in these synthetic regressions")
    monkeypatch.setattr(analysis.requests.sessions.Session, "request", blocked)


def fixture_context():
    raw = offer()
    row = {"MLB": ITEM_ID, "Acao": "Participar", "SKU": "SKU-V2",
           "promotion_type": "LIGHTNING", "action_promotion_id": CAMPAIGN,
           "action_offer_id": raw["ref_id"], "action_deal_price": raw["price"],
           **lightning_row_fields(raw, "LIGHTNING", CAMPAIGN, ITEM_ID)}
    context = contexts.novo_contexto_reanalise("tenant-a", "store-a", CFG, "PROMO-A", "DEAL", 15, 2)
    contexts.registrar_linha_reanalise(context, row, item={"id": ITEM_ID, "site_id": "MLB", "seller_id": "123"},
        raw_acao=raw, custo=12.1234, imposto_rate=0.123456, status_promo_a="Ativo", margem_base=18.23456,
        preco_base=92.28, sku="SKU-V2", raw_a={"promotion_id": "PROMO-A", "price": 80}, preco_a=80)
    sink = []
    result = {"analises": [{"data": [row]}], "data": [row]}
    contexts.finalizar_contexto_reanalise(context, result, sink.append)
    return sink[0], row


def write_job(tmp_path, context, **changes):
    folder = tmp_path / "promo_worker_jobs"
    folder.mkdir(exist_ok=True)
    job = {"client_id": "tenant-a", "loja": "store-a", "status": "completed", "reanalysis_context": context}
    job.update(changes)
    (folder / f"{JOB}.json").write_text(json.dumps(job), encoding="utf-8")
    return job


def test_context_preserves_unrounded_financial_values_and_strips_private_fields():
    context, row = fixture_context()
    record = context["rows"][row["analysis_client_ref"]]
    assert record["custo"] == 12.1234 and record["imposto_rate"] == 0.123456
    assert record["margem_base"] == 18.23456 and record["raw_a"]["price"] == 80
    assert record["promocao_a_id"] == "PROMO-A" and record["sku"] == "SKU-V2"
    assert "access_token" not in json.dumps(context)
    assert "reanalysis_context" not in row
    raw = offer()
    raw.update(access_token="secret", offer={"email": "private", "headers": {"authorization": "private"}, "price": 66.32})
    item = {"id": ITEM_ID, "site_id": "MLB", "seller_id": "123", "access_token": "secret",
            "shipping": {"mode": "me2", "credentials": "secret", "email": "private"}}
    contexts.registrar_linha_reanalise(context, row, item=item, raw_acao=raw, custo=10, imposto_rate=0.1,
        status_promo_a="Ativo", margem_base=20, preco_base=92.28)
    encoded = json.dumps(context)
    assert "secret" not in encoded and "private" not in encoded


@pytest.mark.parametrize("mode", ["api", "files"])
def test_analysis_captures_three_independent_rows_and_only_public_refs(monkeypatch, mode):
    captured = []
    name = "analisar_promo_via_api_com_arquivos" if mode == "files" else "analisar_promo_via_api_sem_arquivos"
    original = getattr(analysis, name)
    async def capturing(*args, **kwargs):
        with contexts.capturar_contexto_reanalise(captured.append):
            return await original(*args, **kwargs)
    monkeypatch.setattr(analysis, name, capturing)
    harness = flow.PromocoesDescontoMlFluxoTests(methodName="runTest")
    harness.setUpClass()
    raw = aggregate([offer(9, 66.32), offer(10, 67.45), offer(12, 68.56)])
    result = harness._executar_fluxo(raw, promo_b_id=CAMPAIGN, promo_b_type="LIGHTNING", retornar_resultado=True,
        com_arquivos=mode == "files", variacoes=[{"sku": "SKU-V1"}, {"sku": "SKU-V2"}],
        custos_por_sku={"SKU-V1": 10.5, "SKU-V2": 17.1234}, imposto_rate=0.123456,
        price_info={"price": 90, "standard_price": 92.28, "original_price": 92.28,
                    "promotion_id": flow.PROMO_A_ID, "promotion_type": "SELLER_CAMPAIGN"})
    assert len(captured) == 1
    rows = result["analises"][0]["data"]
    refs = {row["analysis_client_ref"] for row in rows}
    assert len(rows) == len(refs) == len(captured[0]["rows"]) == 3
    assert refs == set(captured[0]["rows"])
    assert result["data"][0]["analysis_client_ref"] in refs
    assert "reanalysis_context" not in result
    for row in rows:
        record = captured[0]["rows"][row["analysis_client_ref"]]
        assert record["selected"]["deal_price"] == row["action_deal_price"]
        assert record["custo"] == 17.1234 and record["imposto_rate"] == 0.123456
        assert record["promocao_a_id"] == flow.PROMO_A_ID
        assert record["raw_a"]["promotion_id"] == flow.PROMO_A_ID
        decision = jobs._promo_automacao_linha_valor(row, ["Acao", "Ação"])
        assert record["approved"] == (decision == "Participar")


@pytest.mark.parametrize("client,store,cfg", [("tenant-b", "store-a", CFG), ("tenant-a", "store-b", CFG),
    ("tenant-a", "store-a", {"user_id": "456", "site_id": "MLB"}),
    ("tenant-a", "store-a", {"user_id": "123", "site_id": "MLA"}),
    ("tenant-a", "store-a", {"site_id": "MLB"})])
def test_loader_rejects_other_tenant_store_account_or_site(tmp_path, client, store, cfg):
    context, _ = fixture_context()
    write_job(tmp_path, context)
    with pytest.raises(HTTPException):
        contexts.carregar_contexto_reanalise(JOB, client, store, cfg, info_dir=tmp_path)


@pytest.mark.parametrize("status,context", [("running", {}), ("completed", {}), ("error", None)])
def test_loader_rejects_legacy_incomplete_or_failed_job(tmp_path, status, context):
    write_job(tmp_path, context, status=status)
    with pytest.raises(HTTPException):
        contexts.carregar_contexto_reanalise(JOB, "tenant-a", "store-a", CFG, info_dir=tmp_path)


@pytest.mark.parametrize("job_id", ["../escape", "a/../b", "a" * 31, "z" * 32, ""])
def test_loader_rejects_invalid_id_before_read(tmp_path, job_id):
    with pytest.raises(HTTPException):
        contexts.carregar_contexto_reanalise(job_id, "tenant-a", "store-a", CFG, info_dir=tmp_path)


def test_loader_and_resolver_return_detached_copy_and_allow_stock_within_bounds(tmp_path):
    context, row = fixture_context()
    write_job(tmp_path, context)
    loaded = contexts.carregar_contexto_reanalise(JOB, "tenant-a", "store-a", CFG, info_dir=tmp_path)
    selected = dict(loaded["rows"][row["analysis_client_ref"]]["selected"], lightning_stock=19,
                    custo=0, imposto_rate=0, margem_minima=-100, margem_base=-100)
    resolved = contexts.resolver_linha_reanalise(loaded, row["analysis_client_ref"], CAMPAIGN, ITEM_ID, selected)
    assert resolved["custo"] == 12.1234 and resolved["margem_minima"] == 15 and resolved["margem_base"] == 18.23456
    assert resolved["selected"]["lightning_stock"] == 19
    resolved["custo"] = 0
    assert loaded["rows"][row["analysis_client_ref"]]["custo"] == 12.1234


@pytest.mark.parametrize("field,value", [("item_id", "MLB888"), ("action_promotion_id", "OTHER"),
    ("offer_id", "CANDIDATE-MLB888-1"), ("lightning_offer_key", "other-key"),
    ("lightning_start_date", "2026-10-10T12:00:00-03:00"), ("lightning_finish_date", ""),
    ("deal_price", 1), ("action_deal_price", 1), ("lightning_stock_min", 1), ("lightning_stock_max", 100),
    ("lightning_schedule_pending", True), ("lightning_stock", 21), ("lightning_stock", True)])
def test_resolver_rejects_manipulated_invitation_and_invalid_stock(field, value):
    context, row = fixture_context()
    selected = copy.deepcopy(context["rows"][row["analysis_client_ref"]]["selected"])
    selected[field] = value
    with pytest.raises(HTTPException):
        contexts.resolver_linha_reanalise(context, row["analysis_client_ref"], CAMPAIGN, ITEM_ID, selected)


@pytest.mark.parametrize("change", ["campaign", "item", "ref", "not-approved", "seller", "site", "cost", "tax"])
def test_resolver_rejects_foreign_reference_or_incomplete_canonical_context(change):
    context, row = fixture_context()
    ref, campaign, item = row["analysis_client_ref"], CAMPAIGN, ITEM_ID
    record = context["rows"][ref]
    selected = copy.deepcopy(record["selected"])
    if change == "campaign": campaign = "OTHER"
    elif change == "item": item = "MLB888"
    elif change == "ref": ref = "other-ref"
    elif change == "not-approved": record["approved"] = False
    elif change in {"cost", "tax"}: record["custo" if change == "cost" else "imposto_rate"] = None
    else: record[f"{change}_id"] = "other"
    with pytest.raises(HTTPException):
        contexts.resolver_linha_reanalise(context, ref, campaign, item, selected)


def test_final_context_keeps_only_rows_returned_by_filter():
    context, row = fixture_context()
    context["rows"]["discarded"] = copy.deepcopy(next(iter(context["rows"].values())))
    captured = []
    contexts.finalizar_contexto_reanalise(context, {"analises": [{"data": [row]}]}, captured.append)
    assert list(captured[0]["rows"]) == [row["analysis_client_ref"]]


def test_automation_carries_origin_job_and_stable_ref_without_private_context():
    context, row = fixture_context()
    row.update(action_tecnico_apto=True, action_financeiro_exato=True)
    result = {"analysis_job_id": JOB, "analises": [{"promo_b_id": CAMPAIGN, "promo_b_type": "LIGHTNING", "data": [row]}]}
    payload = jobs._promo_automacao_montar_participacoes(result, "store-a")
    assert payload["analysis_job_id"] == JOB and payload["price_policy"] == "refresh_and_reanalyse"
    assert payload["promocoes"][0]["items"][0]["client_ref"] == row["analysis_client_ref"]
    assert "reanalysis_context" not in payload
    legacy = jobs._promo_automacao_montar_participacoes(dict(result, analysis_job_id=""), "store-a")
    assert "price_policy" not in legacy and "analysis_job_id" not in legacy


def test_background_job_persists_private_context_separately_from_public_result(monkeypatch, tmp_path):
    context, row = fixture_context()
    initial = {"client_id": "tenant-a", "loja": "store-a"}
    current = dict(initial)
    def save(job_id, **kwargs):
        current.update(kwargs)
        write_job(tmp_path, current.get("reanalysis_context"), **current)
    async def analysing(**kwargs):
        contexts.finalizar_contexto_reanalise(context, {"analises": [{"data": [row]}]})
        return {"analises": [{"data": [row]}], "data": [row]}
    monkeypatch.setattr(jobs, "_promo_job_set", save)
    monkeypatch.setattr(jobs, "analisar_promo_via_api_com_arquivos", analysing, raising=False)
    jobs._promo_analise_background_worker(job_id=JOB, loja="store-a", promocao_a_id="PROMO-A",
        promocao_a_type="DEAL", margem_minima=15, margem_tolerancia=2, promocoes_b_meta="[]",
        files_payload=[], client_id="tenant-a")
    assert current["status"] == "completed"
    assert current["result"]["analysis_job_id"] == JOB
    assert "reanalysis_context" not in current["result"]
    assert contexts.carregar_contexto_reanalise(JOB, "tenant-a", "store-a", CFG, info_dir=tmp_path)["rows"]


def worker_functions(tmp_path):
    # Compile real worker functions while avoiding the application bootstrap and
    # operational data initialization during this synthetic offline regression.
    source = Path(__file__).resolve().parents[1] / "promo_worker_api.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"_job_path", "_job_set", "_run_job"}
    selected = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[])
    namespace = {"os": os, "json": json, "time": time, "uuid": uuid, "threading": threading, "asyncio": asyncio,
                 "JOBS_DIR": str(tmp_path), "JOBS_LOCK": threading.Lock(), "JOBS_CACHE": {},
                 "capturar_contexto_reanalise": contexts.capturar_contexto_reanalise,
                 "_raise_if_job_cancelled": lambda job: None, "_job_push_log": lambda *args, **kwargs: None,
                 "_job_cancel_requested": lambda job: False, "PromoJobCancelled": type("PromoJobCancelled", (Exception,), {}),
                 "logger": types.SimpleNamespace(exception=lambda *args, **kwargs: None)}
    exec(compile(ast.fix_missing_locations(selected), str(source), "exec"), namespace)
    return namespace


def test_dedicated_worker_persists_context_before_completed_is_visible(tmp_path):
    context, row = fixture_context()
    namespace = worker_functions(tmp_path)
    async def analysing(**kwargs):
        contexts.finalizar_contexto_reanalise(context, {"analises": [{"data": [row]}]})
        return {"analises": [{"data": [row]}]}
    namespace["analisar_promo_via_api_sem_arquivos"] = analysing
    namespace["_job_set"](JOB, client_id="tenant-a", loja="store-a", status="queued")
    namespace["_run_job"](job_id=JOB, loja="store-a", promocao_a_id="PROMO-A", promocao_a_type="DEAL",
        margem_minima=15, margem_tolerancia=2, promocoes_b_meta="[]", files_payload=[], client_id="tenant-a")
    disk = json.loads((tmp_path / f"{JOB}.json").read_text(encoding="utf-8"))
    assert disk["status"] == "completed" and disk["reanalysis_context"] == context
    assert disk["result"]["analysis_job_id"] == JOB and "reanalysis_context" not in disk["result"]
    assert namespace["JOBS_CACHE"][JOB] == disk and not list(tmp_path.glob("*.tmp"))


def test_private_context_capture_is_not_exposed_in_endpoint_signature():
    import inspect
    assert "context_sink" not in inspect.signature(analysis.analisar_promo_via_api_com_arquivos).parameters
    assert "context_sink" not in inspect.signature(analysis.analisar_promo_via_api_sem_arquivos).parameters


def test_recursive_snapshot_drops_unrecognized_fields_and_keeps_domain_values():
    context, row = fixture_context()
    raw = dict(offer(), offer={"price": 66.32, "accessToken": "hidden", "request": {"raw": "hidden"},
                              "promotion": {"promotion_id": CAMPAIGN, "http_body": "hidden"}})
    item = {"id": ITEM_ID, "seller_id": "123", "site_id": "MLB", "shipping": {"mode": "me2",
            "dimensions": "20x10x5,500", "auth": {"accessToken": "hidden"}, "unexpected": "hidden"},
            "attributes": [{"id": "PACKAGE_WEIGHT", "value_struct": {"number": 500, "unit": "g", "secretBody": "hidden"}}]}
    contexts.registrar_linha_reanalise(context, row, item=item, raw_acao=raw, custo=10, imposto_rate=0.1,
        status_promo_a="Ativo", margem_base=20, preco_base=92.28)
    record = context["rows"][row["analysis_client_ref"]]
    assert "hidden" not in json.dumps(record)
    assert record["item"]["shipping"] == {"mode": "me2", "dimensions": "20x10x5,500"}
    assert record["item"]["attributes"][0]["value_struct"] == {"number": 500, "unit": "g"}
    assert record["raw_acao"]["offer"]["promotion"] == {"promotion_id": CAMPAIGN}


def test_context_capture_restores_outer_sink_and_isolates_concurrent_tasks():
    context, row = fixture_context()
    result = {"analises": [{"data": [row]}]}
    outer, inner = [], []
    with contexts.capturar_contexto_reanalise(outer.append):
        with contexts.capturar_contexto_reanalise(inner.append):
            contexts.finalizar_contexto_reanalise(context, result)
        contexts.finalizar_contexto_reanalise(context, result)
    contexts.finalizar_contexto_reanalise(context, result)
    assert len(outer) == len(inner) == 1
    async def run():
        destinations = [[], []]
        async def task(index):
            local = copy.deepcopy(context)
            local["client_id"] = f"tenant-{index}"
            with contexts.capturar_contexto_reanalise(destinations[index].append):
                await asyncio.sleep(0)
                contexts.finalizar_contexto_reanalise(local, result)
        await asyncio.gather(task(0), task(1))
        return destinations
    destinations = asyncio.run(run())
    assert [values[0]["client_id"] for values in destinations] == ["tenant-0", "tenant-1"]


@pytest.fixture
def real_participation_boundary(monkeypatch, tmp_path):
    from backend.services import promocoes_api_participacoes as participation
    from backend.services import promocoes_lightning_reanalise as recovery
    from backend.schemas import PromoAplicarParticipacaoRequest
    flow.PromocoesDescontoMlFluxoTests.setUpClass()
    context, row = fixture_context()
    row.update(action_tecnico_apto=True, action_financeiro_exato=True)
    public = {"analysis_job_id": JOB, "analises": [{"promo_b_id": CAMPAIGN, "promo_b_type": "LIGHTNING", "data": [row]}], "data": [row]}
    write_job(tmp_path, context, result=public)
    monkeypatch.setattr(contexts.promocoes_common, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(participation, "_obter_cfg_ml", lambda client, store: dict(CFG), raising=False)
    monkeypatch.setattr(jobs, "_obter_cfg_ml", lambda client, store: dict(CFG), raising=False)
    state = types.SimpleNamespace(sent=[], reads=[], finances=[], row=row, context=context, public=public)
    refreshed = offer(price=64.5)
    def offers(client, store, cfg, item, **kwargs):
        assert (client, store, item, kwargs.get("force_refresh")) == ("tenant-a", "store-a", ITEM_ID, True)
        state.reads.append(item)
        return [copy.deepcopy(refreshed)], cfg
    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store) == ("tenant-a", "store-a")
        if method == "GET" and url.endswith("/prices"):
            body = {"id": ITEM_ID, "prices": [{"type": "standard", "amount": 92.28, "currency_id": "BRL", "conditions": {"context_restrictions": ["channel_marketplace"]}}]}
            return types.SimpleNamespace(status_code=200, json=lambda: body), cfg
        if method == "GET" and url.endswith("/sale_price"):
            return types.SimpleNamespace(status_code=200, json=lambda: {"amount": 90}), cfg
        assert method == "POST" and url.endswith(f"/items/{ITEM_ID}")
        state.sent.append(copy.deepcopy(kwargs["json"]))
        confirmed = dict(refreshed, status="pending", ref_id=f"OFFER-{ITEM_ID}-9")
        return types.SimpleNamespace(status_code=201, json=lambda: confirmed), cfg
    def finance(service, analysis, client, store, cfg, item, raw, canonical, price, standard):
        state.finances.append(copy.deepcopy(canonical))
        assert canonical["custo"] == 12.1234 and canonical["imposto_rate"] == 0.123456
        assert canonical["margem_minima"] == 15 and canonical["margem_tolerancia"] == 2
        assert price == 64.5 and standard == 92.28
        return {"margem": 25}, {"action_deal_price": price, "action_margem_ml": 25}, True, cfg
    monkeypatch.setattr(participation, "_ml_obter_promocoes_item", offers, raising=False)
    monkeypatch.setattr(participation, "_ml_api_request", request, raising=False)
    monkeypatch.setattr(recovery, "_fresh_financial", finance)
    state.participation = participation
    state.request_schema = PromoAplicarParticipacaoRequest
    def automatic_config():
        return {"enabled": True, "approval_required": False, "last_job_id": JOB, "loja": "store-a",
                "promocao_a_id": "PROMO-A", "promocao_a_type": "DEAL", "margem_minima": 15, "margem_tolerancia": 2,
                "promocoes_b_meta": [{"promo_b_id": CAMPAIGN, "promo_b_type": "LIGHTNING"}], "next_run_at": time.time()+3600}
    state.automatic_config = automatic_config
    monkeypatch.setattr(jobs, "_promo_consultar_worker_job", lambda *args: {"status": "completed", "result": public})
    def apply(req, client_id):
        return participation._aplicar_participacoes_promocoes_payload(req, client_id)
    monkeypatch.setattr(jobs, "aplicar_participacoes_promocoes", apply, raising=False)
    return state


def test_manual_reanalysis_uses_real_persisted_loader_and_resolver(real_participation_boundary):
    state = real_participation_boundary
    selected = copy.deepcopy(state.context["rows"][state.row["analysis_client_ref"]]["selected"])
    selected.update(lightning_stock=6, custo=0, margem_minima=-100, margem_tolerancia=100, imposto_rate=0)
    req = state.request_schema(loja="store-a", analysis_job_id=JOB, price_policy="refresh_and_reanalyse",
        promocoes=[{"promotion_id": CAMPAIGN, "promotion_type": "LIGHTNING", "items": [selected]}])
    result = state.participation._aplicar_participacoes_promocoes_payload(req, "tenant-a")
    assert result["total_sucesso"] == 1 and result["detalhes"][0]["outcome"] == "applied"
    assert result["detalhes"][0]["client_ref"] == state.row["analysis_client_ref"]
    assert result["detalhes"][0]["analysis_row_updates"]["action_deal_price"] == 64.5
    assert state.sent == [{"promotion_type": "LIGHTNING", "deal_price": 64.5, "stock": 6}]
    assert len(state.finances) == 1


def test_automatic_reanalysis_uses_builder_and_same_real_loader_resolver(real_participation_boundary):
    state = real_participation_boundary
    cfg, changed = jobs._promo_automacao_refresh_job_state("tenant-a", state.automatic_config(), aplicar=True)
    assert changed and cfg["last_applied_job_id"] == JOB
    result = cfg["last_apply_result"]
    assert result["total_sucesso"] == 1 and result["detalhes"][0]["outcome"] == "applied"
    assert result["detalhes"][0]["client_ref"] == state.row["analysis_client_ref"]
    assert result["detalhes"][0]["analysis_row_updates"]["action_deal_price"] == 64.5
    assert state.sent == [{"promotion_type": "LIGHTNING", "deal_price": 64.5, "stock": 5}]


@pytest.mark.parametrize("field,value", [("deal_price", 1), ("lightning_offer_key", "foreign-window"), ("action_promotion_id", "OTHER")])
def test_manual_tamper_is_blocked_by_real_canonical_boundary(real_participation_boundary, field, value):
    state = real_participation_boundary
    payload = jobs._promo_automacao_montar_participacoes(state.public, "store-a")
    payload["promocoes"][0]["items"][0][field] = value
    result = state.participation._aplicar_participacoes_promocoes_payload(state.request_schema(**payload), "tenant-a")
    assert result["detalhes"][0]["outcome"] == "blocked" and not state.sent and not state.finances


@pytest.mark.parametrize("field,value", [("margem_minima", 30), ("margem_tolerancia", 0), ("promocao_a_id", "OTHER"),
    ("promocao_a_type", "SMART"), ("loja", "store-b"), ("promocoes_b_meta", [{"promo_b_id": "OTHER", "promo_b_type": "LIGHTNING"}])])
def test_automatic_job_cannot_apply_after_configuration_changes(real_participation_boundary, field, value):
    state = real_participation_boundary
    cfg = state.automatic_config()
    cfg[field] = value
    start = time.time()
    cfg, changed = jobs._promo_automacao_refresh_job_state("tenant-a", cfg, aplicar=True)
    assert changed and not state.sent and not state.finances
    assert cfg["last_apply_result"]["decision_code"] == "AUTOMATION_CONTEXT_CHANGED"
    assert cfg["next_run_at"] >= start and cfg["next_run_at"] <= time.time()


@pytest.mark.parametrize("setting", ["disabled", "requires-approval"])
def test_automation_disabled_or_waiting_approval_never_applies_old_job(real_participation_boundary, setting):
    state = real_participation_boundary
    cfg = state.automatic_config()
    cfg["enabled" if setting == "disabled" else "approval_required"] = setting != "disabled"
    cfg, changed = jobs._promo_automacao_refresh_job_state("tenant-a", cfg, aplicar=True)
    assert changed and not state.sent and not state.finances
    assert "last_apply_result" not in cfg


def test_http_openapi_does_not_expose_private_context_channel():
    from fastapi import FastAPI
    app = FastAPI()
    app.add_api_route("/analysis", analysis.analisar_promo_via_api_com_arquivos, methods=["POST"])
    operation = app.openapi()["paths"]["/analysis"]["post"]
    assert "context_sink" not in {parameter["name"] for parameter in operation.get("parameters", [])}



def test_duplicate_same_window_foreign_ref_is_blocked(real_participation_boundary):
    state = real_participation_boundary
    payload = jobs._promo_automacao_montar_participacoes(state.public, "store-a")
    selected = payload["promocoes"][0]["items"][0]
    duplicate = copy.deepcopy(selected)
    duplicate["client_ref"] = "invented-ref"
    payload["promocoes"][0]["items"].append(duplicate)
    result = state.participation._aplicar_participacoes_promocoes_payload(state.request_schema(**payload), "tenant-a")
    assert [detail["outcome"] for detail in result["detalhes"]] == ["applied", "blocked"]
    assert len(state.sent) == 1
