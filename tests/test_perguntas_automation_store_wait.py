import datetime as dt
import logging
import threading

import pytest
from fastapi import HTTPException
from backend.services import perguntas_pos_venda_automacao as auto


@pytest.fixture
def state(monkeypatch):
    monkeypatch.setattr(auto, "dt", dt, raising=False)
    monkeypatch.setattr(auto, "logger", logging.getLogger(__name__), raising=False)
    for key, value in {
        "PERGUNTAS_AUTOMACAO_BG_LOCK": threading.Lock(),
        "PERGUNTAS_AUTOMACAO_BG_RUNNING": set(), "PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS": {},
        "PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS": {}, "_STORE_ENUM_NEXT": {}, "_STORE_ENUM_FAILURES": {},
    }.items():
        monkeypatch.setattr(auto, key, value, raising=False)
    clock = [1000.0]
    monkeypatch.setattr(auto.time, "time", lambda: clock[0])
    return clock


def test_store_wait_preserves_baseline_and_retries_boundedly(state, monkeypatch):
    key = auto._perguntas_automacao_bg_key("tenant", "Store", "perguntas")
    auto.PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS[key] = {
        "question_snapshot_initialized": True, "question_ids": ["42"], "change_token": "a" * 64,
        "enviadas": 2, "novas_pendentes": 1,
    }
    def unavailable(**kwargs):
        raise HTTPException(503, detail={"code": "stores_snapshot_initializing"})
    monkeypatch.setattr(auto, "ml_perguntas_automacao_poll", unavailable, raising=False)
    for delay in [5, 15, 30, 300, 300]:
        auto._perguntas_automacao_bg_executar("tenant", "Store", "perguntas", 300)
        saved = auto.PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS[key]
        assert saved["question_ids"] == ["42"]
        assert saved["change_token"] == "a" * 64
        assert saved["enviadas"] == 2
        assert not saved["question_snapshot_complete"] and not saved["success"]
        assert auto.PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS[key] == state[0] + delay
        state[0] += delay
    monkeypatch.setattr(auto, "ml_perguntas_automacao_poll", lambda **kw: {
        "question_snapshots": [{"question_snapshot_complete": True, "question_ids": ["42", "43"]}]
    })
    auto._perguntas_automacao_bg_executar("tenant", "Store", "perguntas", 300)
    saved = auto.PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS[key]
    assert saved["store_read_attempt"] == 0 and saved["new_question_ids"] == ["43"]


def test_unavailable_tenant_does_not_stop_others_or_repeat_early(state, monkeypatch):
    monkeypatch.setattr(auto, "_perguntas_automacao_bg_tenants", lambda: ["a", "b"])
    monkeypatch.setattr(auto, "_perguntas_loja_configs_carregar", lambda tenant: {
        "Store": {"responder_automaticamente": True, "intervalo_minutos": 5}}, raising=False)
    monkeypatch.setattr(auto, "_perguntas_loja_config_normalizar", lambda value: value, raising=False)
    monkeypatch.setattr(auto, "_ml_oauth_status", lambda cfg: {"conectado": True}, raising=False)
    calls, reads = [], []
    def stores(tenant):
        reads.append(tenant)
        if tenant == "a":
            raise HTTPException(409, detail={"code": "stores_busy"})
        return [{"nome": "Store", "integracoes": {"mercadolivre": {}}}]
    monkeypatch.setattr(auto, "carregar_lojas", stores)
    monkeypatch.setattr(auto, "_perguntas_automacao_bg_executar", lambda tenant, *args: calls.append(tenant))
    auto._perguntas_automacao_bg_tick()
    auto._perguntas_automacao_bg_tick()
    assert calls == ["b", "b"] and reads == ["a", "b", "b"]
    assert auto._STORE_ENUM_NEXT["a"] == state[0] + 5


def test_disabled_post_sale_never_prepares_sync_or_reads_credentials(state, monkeypatch):
    monkeypatch.setattr(auto, "ml_pos_venda_automacao_poll", lambda **kw: {"disabled": True}, raising=False)
    monkeypatch.setattr(auto, "_perguntas_automacao_pos_venda_preparar_sync", lambda *a: pytest.fail("disabled flow consulted credentials"))
    auto._perguntas_automacao_bg_executar("tenant", "Store", "pos_venda", 300)


def test_identity_conflict_is_not_temporary():
    assert not auto._stores_temporarily_unavailable(HTTPException(409, detail={"code": "store_identity_changed"}))
    assert not auto._stores_temporarily_unavailable(HTTPException(403, detail={"code": "stores_busy"}))


def test_store_wait_after_fetch_does_not_consume_new_questions(state):
    key = auto._perguntas_automacao_bg_key("tenant", "Store", "perguntas")
    auto.PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS[key] = {
        "question_snapshot_initialized": True, "question_ids": ["42"], "change_token": "a" * 64,
    }
    snapshot = {"question_snapshots": [{"question_snapshot_complete": True, "question_ids": ["42", "43"]}]}
    auto._perguntas_automacao_bg_finalizar(key, 300, {**snapshot, "_store_read_unavailable": True})
    saved = auto.PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS[key]
    assert saved["question_ids"] == ["42"] and saved["change_token"] == "a" * 64
    assert saved["new_question_ids"] == [] and not saved["question_snapshot_complete"]
    auto._perguntas_automacao_bg_finalizar(key, 300, snapshot)
    assert auto.PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS[key]["new_question_ids"] == ["43"]


def test_unpublished_provider_and_invalid_store_do_not_stop_next_store(state, monkeypatch):
    monkeypatch.setattr(auto, "_perguntas_automacao_bg_tenants", lambda: ["tenant"])
    config = {"responder_automaticamente": True, "intervalo_minutos": 5}
    monkeypatch.setattr(auto, "_perguntas_loja_configs_carregar", lambda tenant: {
        "updating": config, "broken": {**config, "intervalo_minutos": "invalid"}, "ready": config,
    }, raising=False)
    monkeypatch.setattr(auto, "_perguntas_loja_config_normalizar", lambda value: value, raising=False)
    monkeypatch.setattr(auto, "_ml_oauth_status", lambda cfg: {"conectado": True}, raising=False)
    monkeypatch.setattr(auto, "carregar_lojas", lambda tenant: [
        {"nome": "updating", "_unavailable_providers": ["mercadolivre"]},
        {"nome": "broken"}, {"nome": "ready"},
    ])
    calls = []
    monkeypatch.setattr(auto, "_perguntas_automacao_bg_executar", lambda tenant, name, *args: calls.append(name))
    auto._perguntas_automacao_bg_tick()
    assert calls == ["ready"]
    key = auto._perguntas_automacao_bg_key("tenant", "updating", "perguntas")
    assert auto.PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS[key] == state[0] + 5
    assert not auto.PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS[key]["question_snapshot_complete"]
    auto._perguntas_automacao_bg_tick()
    assert auto.PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS[key] == state[0] + 5
