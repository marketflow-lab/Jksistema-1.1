import copy
import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.routers.favoritos import LEGACY_FAVORITOS_ROUTES
from backend.services import favoritos_checkpoints as service
from backend.services import favoritos_endpoints as endpoints
from backend.services import favoritos_storage as history

BASE = "/api/favoritos/ranking/checkpoints"


@pytest.fixture
def api(tmp_path, monkeypatch):
    tenant = lambda client: str(tmp_path / client)
    monkeypatch.setattr(endpoints, "get_tenant_path", tenant, raising=False)
    monkeypatch.setattr(endpoints, "_shared_sync_auto_enabled", lambda: False, raising=False)
    monkeypatch.setattr(history, "get_tenant_path", tenant, raising=False)
    monkeypatch.setattr(history, "_favoritos_usuario_slug", lambda value: re.sub(r"[^a-z0-9_-]+", "_", str(value).lower()), raising=False)
    monkeypatch.setattr(endpoints, "_lojas_favoritos_com_ml", lambda client: [{"nome": "Loja A"}, {"nome": "Loja B"}], raising=False)
    monkeypatch.setattr(endpoints, "_chave_loja_favoritos", lambda value: re.sub(r"[^a-z0-9]", "", str(value).lower()), raising=False)
    clock = [2000000000.0]
    monkeypatch.setattr(service.time, "time", lambda: clock[0])
    app = FastAPI()
    async def auth(request: Request):
        if request.headers.get("x-no-auth") != "yes":
            request.state.username = request.headers.get("x-user", "owner")
        return request.headers.get("x-client", "client-a")
    app.dependency_overrides[endpoints.get_tenant_id] = auth
    for spec in LEGACY_FAVORITOS_ROUTES:
        if spec.endpoint_name.startswith("favoritos_checkpoints_"):
            app.add_api_route("/api/favoritos" + spec.path, getattr(endpoints, spec.endpoint_name), methods=[spec.method])
    return TestClient(app), clock, tmp_path


def create(client, request_id="start", terms=None, **extra):
    body = {"request_id": request_id, "loja": "loja a", "sku": "AVULSO", "titulo": "Produto", "termos": terms or [{"campo": 1, "termo": "produto"}], **extra}
    response = client.post(BASE, json=body)
    assert response.status_code == 200, response.text
    return response.json()["checkpoint"]


def candidate(index=1, term="produto"):
    return {"id": f"MLB{index}", "url": f"https://produto.mercadolivre.com.br/MLB-{index}-produto_JM", "titulo": "Produto", "vendas": 0,
            "vendasFonte": "avantpro_dom", "vendedorFonte": "avantpro_dom", "data_criacao": "2024-01-01", "dataCriacaoFonte": "avantpro_dom",
            "media_mensal": 0, "media_mensal_fonte": "avantpro_dom", "origem_dados": "avantpro_primeira_pagina_controlada",
            "capturado_em_ms": 2000000000000, "pesquisas_origem": [term], "campos_origem": ["1"]}


def progress(client, cp, request_id="progress", **data):
    response = client.put(BASE + "/" + cp["id"], json={"request_id": request_id, "loja": "Loja A", "expected_revision": cp["revision"], "attempt_token": cp["attempt_token"], **data})
    assert response.status_code == 200, response.text
    return response.json()["checkpoint"]


def finalize(client, cp, request_id="finish", grupo=None):
    return client.post(BASE + "/" + cp["id"] + "/finalizar", json={"request_id": request_id, "loja": "Loja A", "expected_revision": cp["revision"], "attempt_token": cp["attempt_token"], "grupo_ranking": grupo})


def partial(client, cp, items=None):
    return progress(client, cp, candidatos=items or [candidate()], termos=[{"campo": 1, "termo": "produto", "estado": "parcial", "confirmed_keys": ["mlb:mlb1"], "pending_keys": ["mlb:mlb2"]}], estado_coleta="parcial", duracao_execucao_ms=90000)


def test_restart_pool_and_provenance_survive_beyond_history_limit(api):
    client, _, directory = api
    cp = partial(client, create(client), [candidate(index) for index in range(1, 81)])
    cp = client.get(BASE + "/" + cp["id"], params={"loja": "Loja A"}).json()["checkpoint"]
    assert len(cp["candidatos"]) == 80
    assert cp["candidatos"][0]["vendas"] == 0
    assert cp["candidatos"][0]["media_mensal_fonte"] == "avantpro_dom"
    assert "attempt_token" not in cp
    listed = client.get(BASE, params={"loja": "Loja A"}).json()["checkpoints"][0]
    assert listed["total_candidatos"] == 80
    assert "candidatos" not in listed and "attempt_token" not in listed
    assert "confirmed_keys" not in listed["termos"][0]
    assert list((directory / "client-a").glob("favoritos_ranking_checkpoints_*.db"))


def test_creation_and_progress_idempotency_cas_and_lease(api):
    client, clock, _ = api
    cp = create(client)
    assert create(client)["id"] == cp["id"]
    replay_request = {"request_id": "beat", "loja": "Loja A", "expected_revision": cp["revision"], "attempt_token": cp["attempt_token"]}
    response = client.put(BASE + "/" + cp["id"], json=replay_request)
    assert response.status_code == 200
    next_cp = response.json()["checkpoint"]
    assert client.put(BASE + "/" + cp["id"], json=replay_request).json()["checkpoint"]["revision"] == next_cp["revision"]
    wrong = {**replay_request, "request_id": "other"}
    assert client.put(BASE + "/" + cp["id"], json=wrong).status_code == 409
    clock[0] += 121
    expired = {**wrong, "expected_revision": next_cp["revision"]}
    assert client.put(BASE + "/" + cp["id"], json=expired).json()["detail"]["code"] == "lease_expired"


def test_tenant_user_store_auth_and_closed_schema(api):
    client, _, _ = api
    cp = create(client)
    path = BASE + "/" + cp["id"]
    assert client.get(path, params={"loja": "Loja A"}, headers={"x-client": "client-b"}).status_code == 404
    assert client.get(path, params={"loja": "Loja A"}, headers={"x-user": "other"}).status_code == 404
    assert client.get(path, params={"loja": "Loja B"}).status_code == 403
    assert client.get(path, params={"loja": "unconnected"}).status_code == 403
    assert client.get(path, params={"loja": "Loja A"}, headers={"x-no-auth": "yes"}).status_code == 401
    bad = {"request_id": "spoof", "loja": "Loja A", "termos": [{"campo": 1, "termo": "produto"}], "username": "owner"}
    assert client.post(BASE, json=bad).status_code == 422
    bad.pop("username"); bad["termos"] *= 4
    assert client.post(BASE, json=bad).status_code == 422


def test_candidate_bounds_origins_identity_and_no_loss(api):
    client, _, _ = api
    cp = partial(client, create(client))
    base = {"request_id": "bad", "loja": "Loja A", "expected_revision": cp["revision"], "attempt_token": cp["attempt_token"]}
    for items in ([], [candidate(2, "foreign")], [{**candidate(2), "id": "", "url": "https://evil.example/p"}], [candidate(i) for i in range(1, 82)]):
        assert client.put(BASE + "/" + cp["id"], json={**base, "candidatos": items}).status_code == 422
    assert client.put(BASE + "/" + cp["id"], json={**base, "candidatos": [{**candidate(), "credentials": "forbidden"}]}).status_code == 422


def test_partial_finalize_idempotent_and_child_preserves_old_snapshot(api):
    client, _, _ = api
    cp = partial(client, create(client))
    group = {"sku": "forged", "loja": "Loja B", "anuncios": [candidate()]}
    response = finalize(client, cp, grupo=group)
    assert response.status_code == 200, response.text
    result = response.json(); parent = result["checkpoint"]; first_entry = result["historico_entry"]
    assert result["confirmed"] and parent["estado_coleta"] == "parcial" and parent["estado_fechamento"] == "confirmado"
    assert first_entry["loja"] == "Loja A" and first_entry["grupos"][0]["sku"] == "AVULSO"
    assert first_entry["grupos"][0]["checkpoint_ref"]["id"] == cp["id"]
    assert finalize(client, cp, grupo=group).json()["historico_entry"]["id"] == first_entry["id"]
    resumed = client.post(BASE + "/" + cp["id"] + "/retomar", json={"request_id": "resume", "expected_revision": parent["revision"], "loja": "Loja A"})
    assert resumed.status_code == 200, resumed.text
    child = resumed.json()["checkpoint"]
    assert child["id"] != cp["id"] and child["parent_history_entry_id"] == first_entry["id"]
    assert len(child["candidatos"]) == 1 and child["opcoes"] == cp["opcoes"]
    assert parent["retomada_numero"] == 1 and child["retomada_numero"] == 2
    assert first_entry["grupos"][0]["checkpoint_ref"]["retomada_numero"] == 1
    child = progress(client, child, "child-progress", termos=[{"campo": 1, "termo": "produto", "estado": "concluida"}], estado_coleta="concluida")
    second = finalize(client, child, "child-finish", group).json()["historico_entry"]
    assert second["id"] != first_entry["id"]
    stored = history._favoritos_carregar_historico("client-a", "owner")["historico"]
    assert len(stored) == 2
    assert next(entry for entry in stored if entry["id"] == first_entry["id"]) == first_entry


def test_failure_between_phases_recovery_needs_no_collection(api, monkeypatch):
    client, _, _ = api
    cp = partial(client, create(client))
    upsert = history._favoritos_checkpoint_upsert_historico
    def fail(*args):
        raise OSError("simulated")
    monkeypatch.setattr(history, "_favoritos_checkpoint_upsert_historico", fail)
    response = finalize(client, cp, grupo={"anuncios": [candidate()]})
    assert response.status_code == 503
    pending = client.get(BASE + "/" + cp["id"], params={"loja": "Loja A"}).json()["checkpoint"]
    assert pending["estado_fechamento"] == "fechamento_pendente"
    resume = {"request_id": "resume", "expected_revision": pending["revision"], "loja": "Loja A"}
    assert client.post(BASE + "/" + cp["id"] + "/retomar", json=resume).json()["detail"]["code"] == "closing_pending"
    recovery = {"request_id": "recover", "expected_revision": pending["revision"], "loja": "Loja A", "recuperar_fechamento": True}
    assert pending["lease_expires_at_ms"] == 0
    assert response.json()["detail"]["checkpoint"]["revision"] == pending["revision"]
    monkeypatch.setattr(history, "_favoritos_checkpoint_upsert_historico", upsert)
    response = client.post(BASE + "/" + cp["id"] + "/finalizar", json=recovery)
    assert response.status_code == 200, response.text
    assert response.json()["historico_entry"]["id"] == pending["history_entry_id"]
    assert len(history._favoritos_carregar_historico("client-a", "owner")["historico"]) == 1


def test_crash_after_history_before_confirmation_keeps_same_entry(api, monkeypatch):
    client, _, _ = api
    cp = partial(client, create(client))
    original = history._favoritos_checkpoint_upsert_historico
    calls = []
    def persist_then_crash(*args):
        entry = original(*args); calls.append(entry["id"])
        raise OSError("crash after publication")
    monkeypatch.setattr(history, "_favoritos_checkpoint_upsert_historico", persist_then_crash)
    group = {"anuncios": [candidate()]}
    assert finalize(client, cp, grupo=group).status_code == 503
    monkeypatch.setattr(history, "_favoritos_checkpoint_upsert_historico", original)
    done = finalize(client, cp, grupo=group)
    assert done.status_code == 200, done.text
    assert done.json()["historico_entry"]["id"] == calls[0]
    assert len(history._favoritos_carregar_historico("client-a", "owner")["historico"]) == 1


def test_zero_candidates_checkpoint_is_visible_without_fake_history(api):
    client, _, _ = api
    cp = create(client)
    result = finalize(client, cp).json()
    assert result["confirmed"] and result["historico_entry"] is None
    assert result["checkpoint"]["history_entry_id"] is None
    assert result["checkpoint"]["estado_coleta"] == "parcial"
    assert history._favoritos_carregar_historico("client-a", "owner")["historico"] == []
    listed = client.get(BASE, params={"loja": "Loja A"}).json()["checkpoints"][0]
    assert listed["total_candidatos"] == 0 and listed["retomavel"]


def test_cancel_and_late_put_do_not_finalize_or_reopen(api):
    client, _, _ = api
    original = create(client)
    cp = progress(client, original, estado_coleta="cancelada")
    assert finalize(client, cp).status_code == 409
    cp = partial(client, create(client, "second"))
    old = copy.deepcopy(cp)
    cp = finalize(client, cp, grupo={"anuncios": [candidate()]}).json()["checkpoint"]
    response = client.put(BASE + "/" + cp["id"], json={"request_id": "late", "loja": "Loja A", "expected_revision": cp["revision"], "attempt_token": old["attempt_token"]})
    assert response.status_code == 409
    assert client.get(BASE + "/" + cp["id"], params={"loja": "Loja A"}).json()["checkpoint"]["estado_fechamento"] == "confirmado"


def test_two_competing_updates_one_revision_wins(api):
    client, _, _ = api
    cp = create(client)
    def put(number):
        return client.put(BASE + "/" + cp["id"], json={"request_id": f"beat-{number}", "loja": "Loja A", "expected_revision": cp["revision"], "attempt_token": cp["attempt_token"]}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(put, range(2))) == [200, 409]


def test_existing_sku_keeps_identity_in_checkpoint_and_history(api):
    client, _, _ = api
    cp = partial(client, create(client, sku="1005-K"))
    result = finalize(client, cp, grupo={"sku": "forged", "anuncios": [candidate()]}).json()
    assert result["checkpoint"]["sku"] == "1005-K"
    assert result["historico_entry"]["grupos"][0]["sku"] == "1005-K"
    assert not result["historico_entry"]["grupos"][0]["avulso"]
    assert not result["historico_entry"]["grupos"][0]["pesquisa_avulsa"]


def test_completed_but_empty_collection_can_reopen_terms(api):
    client, _, _ = api
    cp = progress(client, create(client), termos=[{"campo": 1, "termo": "produto", "estado": "concluida"}], estado_coleta="concluida")
    assert cp["estado_coleta"] == "parcial" and cp["termos"][0]["estado"] == "parcial"
    result = finalize(client, cp).json()
    cp = result["checkpoint"]
    assert result["historico_entry"] is None
    response = client.post(BASE + "/" + cp["id"] + "/retomar", json={"request_id": "resume", "expected_revision": cp["revision"], "loja": "Loja A"})
    assert response.status_code == 200
    assert response.json()["checkpoint"]["termos"][0]["estado"] == "parcial"


def test_description_sources_and_history_provenance_survive_partial_updates(api):
    client, _, _ = api
    listing = {**candidate(), "descricao": "Produto detalhado " * 400, "tituloFonte": "avantpro_dom", "linkFonte": "avantpro_dom",
               "ritmo_vendas_mes": 0, "ritmo_vendas_mes_fonte": "avantpro_dom", "cacheAvant": True}
    cp = partial(client, create(client), [listing])
    cp = progress(client, cp, "new-data", candidatos=[{"id": "MLB1", "pesquisas_origem": ["produto"], "preco": 25}], termos=[{"campo": 1, "termo": "produto", "estado": "parcial"}])
    assert cp["candidatos"][0]["descricao"] == listing["descricao"].strip()
    assert cp["candidatos"][0]["cacheAvant"] is True
    assert cp["candidatos"][0]["dataCriacaoFonte"] == "avantpro_dom"
    assert cp["candidatos"][0]["vendas"] == 0 and cp["candidatos"][0]["preco"] == 25
    assert cp["termos"][0]["confirmed_keys"] == ["mlb:mlb1"]
    result = finalize(client, cp, grupo={"anuncios": [{"id": "MLB1"}]}).json()
    listing = result["historico_entry"]["grupos"][0]["anuncios"][0]
    assert listing["linkFonte"] == "avantpro_dom"
    assert listing["ritmo_vendas_mes"] == 0 and listing["ritmo_vendas_mes_fonte"] == "avantpro_dom"
    assert history._favoritos_normalizar_historico([result["historico_entry"]])[0] == result["historico_entry"]


def test_malformed_nested_snapshot_and_unrecognized_summary_rejected(api):
    client, _, _ = api
    cp = partial(client, create(client))
    assert finalize(client, cp, grupo={"anuncios": {"id": "MLB1"}}).status_code == 422
    assert finalize(client, cp, grupo={}).status_code == 422
    assert finalize(client, cp, grupo={"anuncios": {}}).status_code == 422
    assert finalize(client, cp, grupo={"anuncios": [candidate(2)]}).status_code == 422
    assert finalize(client, cp, grupo={"anuncios": [{**candidate(), "url": "https://evil.example/MLB-1"}]}).status_code == 422
    response = client.put(BASE + "/" + cp["id"], json={"request_id": "bad-summary", "loja": "Loja A", "expected_revision": cp["revision"], "attempt_token": cp["attempt_token"], "resumo_coleta": [{"termo": "produto", "credentials": "secret"}]})
    assert response.status_code == 422


def test_completed_term_cannot_regress_on_late_progress(api):
    client, _, _ = api
    cp = progress(client, create(client), candidatos=[candidate()], termos=[{"campo": 1, "termo": "produto", "estado": "concluida"}], estado_coleta="concluida")
    response = client.put(BASE + "/" + cp["id"], json={"request_id": "regress", "loja": "Loja A", "expected_revision": cp["revision"], "attempt_token": cp["attempt_token"], "termos": [{"campo": 1, "termo": "produto", "estado": "parcial"}]})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "term_regression"


def test_pending_crash_lease_blocks_takeover_until_expiry(api, monkeypatch):
    client, clock, _ = api
    cp = partial(client, create(client))
    # BaseException models abrupt process death: the failure-release handler cannot run.
    class SimulatedCrash(BaseException):
        pass
    def crash(*args):
        raise SimulatedCrash()
    original = history._favoritos_checkpoint_upsert_historico
    monkeypatch.setattr(history, "_favoritos_checkpoint_upsert_historico", crash)
    with pytest.raises(SimulatedCrash):
        finalize(client, cp, grupo={"anuncios": [candidate()]})
    pending = client.get(BASE + "/" + cp["id"], params={"loja": "Loja A"}).json()["checkpoint"]
    recovery = {"request_id": "recover", "expected_revision": pending["revision"], "loja": "Loja A", "recuperar_fechamento": True}
    assert client.post(BASE + "/" + cp["id"] + "/finalizar", json=recovery).json()["detail"]["code"] == "lease_active"
    clock[0] += 121
    monkeypatch.setattr(history, "_favoritos_checkpoint_upsert_historico", original)
    assert client.post(BASE + "/" + cp["id"] + "/finalizar", json=recovery).status_code == 200


def test_full_renderer_summary_and_samples_are_persisted(api):
    client, _, _ = api
    summary = {"pesquisa": 1, "campo": 1, "termo": "produto", "visiveis": 50, "coletados": 1,
               "com_titulo": 1, "com_foto": 0, "com_preco": 1, "com_link": 1, "com_dados_avant": 1,
               "incompletos": 1, "suspeitos": 0, "avant_nao_vinculado": 0, "login_avant_bloqueado": False,
               "motivo_encerramento": "tempo_limite", "passadas": 1, "posicoes_percorridas": 1,
               "cliques_avant": 1, "capturados_avant": 1, "tempo_navegacao_ms": 1500,
               "tempo_materializacao_ms": 1000, "tempo_avant_ms": 80000, "tempo_finalizacao_ms": 2000,
               "motivos_incompletos": {"foto": 1}, "origens_dados": {"avantpro_primeira_pagina_controlada": 1},
               "amostras_incompletos": [{"posicao": 1, "mlb": "MLB1", "titulo": "Produto", "faltas": ["foto"],
                 "origem_dados": "avantpro_primeira_pagina_controlada", "tituloFonte": "avantpro_dom", "fotoFonte": "",
                 "precoFonte": "ml_card_dom", "vendasFonte": "avantpro_dom", "vendedorFonte": "avantpro_dom", "dataCriacaoFonte": "avantpro_dom"}]}
    cp = progress(client, create(client), candidatos=[candidate()], resumo_coleta=[summary])
    assert cp["resumo_coleta"][0]["tempo_navegacao_ms"] == 1500
    entry = finalize(client, cp, grupo={"anuncios": [candidate()]}).json()["historico_entry"]
    summary = entry["grupos"][0]["resumo_coleta"][0]
    assert summary["tempo_navegacao_ms"] == 1500 and summary["amostras_incompletos"][0]["faltas"] == ["foto"]


def test_normalized_progress_request_replays_without_a_second_write(api):
    client, _, _ = api
    cp = partial(client, create(client))
    request = {"request_id": "normalized-update", "loja": "Loja A", "expected_revision": cp["revision"], "attempt_token": cp["attempt_token"],
               "termos": [{"campo": 1, "termo": "produto", "estado": "parcial"}]}
    first = client.put(BASE + "/" + cp["id"], json=request)
    second = client.put(BASE + "/" + cp["id"], json=request)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["checkpoint"]["revision"] == second.json()["checkpoint"]["revision"]


def test_sharing_hooks_run_only_after_first_confirmation_and_gate(api, monkeypatch):
    client, _, _ = api
    cp = partial(client, create(client))
    events = []
    monkeypatch.setattr(endpoints, "_shared_sync_auto_enabled", lambda: True)
    monkeypatch.setattr(endpoints, "_favoritos_carregar_historico", lambda *args: {"historico": [{"id": "saved"}]}, raising=False)
    def share(*args):
        current = client.get(BASE + "/" + cp["id"], params={"loja": "Loja A"}).json()["checkpoint"]
        assert current["estado_fechamento"] == "confirmado"
        events.append("share")
        return {"updated_users": ["another"], "eligible_users": ["owner", "another"]}
    monkeypatch.setattr(endpoints, "_favoritos_propagar_historico_para_usuarios", share, raising=False)
    monkeypatch.setattr(endpoints, "_favoritos_historico_publicar_evento_realtime", lambda *args: events.append("event") or True, raising=False)
    result = finalize(client, cp, grupo={"anuncios": [candidate()]})
    assert result.status_code == 200 and result.json()["confirmed"]
    assert result.json()["realtime_sync"]["scheduled"]
    assert finalize(client, cp, grupo={"anuncios": [candidate()]}).status_code == 200
    assert events == ["share", "event"]
    monkeypatch.setattr(endpoints, "_shared_sync_auto_enabled", lambda: False)
    cp2 = partial(client, create(client, "second"))
    assert finalize(client, cp2, "second-finish", {"anuncios": [candidate()]}).status_code == 200
    assert events == ["share", "event"]


def test_sharing_failure_cannot_invalidate_confirmed_snapshot(api, monkeypatch):
    client, _, _ = api
    cp = partial(client, create(client))
    monkeypatch.setattr(endpoints, "_shared_sync_auto_enabled", lambda: True)
    monkeypatch.setattr(endpoints, "_favoritos_carregar_historico", lambda *args: {"historico": []}, raising=False)
    def fail(*args):
        raise OSError("simulated sharing failure")
    monkeypatch.setattr(endpoints, "_favoritos_propagar_historico_para_usuarios", fail, raising=False)
    result = finalize(client, cp, grupo={"anuncios": [candidate()]})
    assert result.status_code == 200 and result.json()["confirmed"]
    assert result.json()["realtime_sync"]["scheduled"]
    assert result.json()["checkpoint"]["estado_fechamento"] == "confirmado"
    assert len(history._favoritos_carregar_historico("client-a", "owner")["historico"]) == 1


def test_explicit_avulso_option_is_preserved_through_resume(api):
    client, _, _ = api
    cp = partial(client, create(client, sku="1005-K", opcoes={"avulso": True}))
    result = finalize(client, cp, grupo={"anuncios": [candidate()]}).json()
    assert result["historico_entry"]["grupos"][0]["avulso"] is True
    cp = result["checkpoint"]
    response = client.post(BASE + "/" + cp["id"] + "/retomar", json={"request_id": "resume", "expected_revision": cp["revision"], "loja": "Loja A"})
    child = response.json()["checkpoint"]
    assert child["opcoes"]["avulso"] is True and child["sku"] == "1005-K"
    cp2 = partial(client, create(client, "second", opcoes={"avulso": False}))
    entry = finalize(client, cp2, "finish-second", {"anuncios": [candidate()]}).json()["historico_entry"]
    assert cp2["opcoes"]["avulso"] is False
    assert entry["grupos"][0]["sku"] == "AVULSO" and entry["grupos"][0]["avulso"] is False
    assert entry["grupos"][0]["pesquisa_avulsa"] is False


def test_crash_after_complete_collection_resumes_only_calculation(api):
    client, clock, _ = api
    cp = progress(client, create(client), candidatos=[candidate()], termos=[{"campo": 1, "termo": "produto", "estado": "concluida"}], estado_coleta="concluida")
    assert cp["estado_fechamento"] == "aberto"
    assert client.get(BASE, params={"loja": "Loja A"}).json()["checkpoints"][0]["retomavel"]
    clock[0] += 121
    response = client.post(BASE + "/" + cp["id"] + "/retomar", json={"request_id": "resume", "expected_revision": cp["revision"], "loja": "Loja A"})
    assert response.status_code == 200, response.text
    child = response.json()["checkpoint"]
    assert child["termos"][0]["estado"] == "concluida" and len(child["candidatos"]) == 1
    result = finalize(client, child, grupo={"anuncios": [candidate()]}).json()
    assert result["checkpoint"]["estado_coleta"] == "concluida"
    cp = result["checkpoint"]
    response = client.post(BASE + "/" + cp["id"] + "/retomar", json={"request_id": "resume-complete", "expected_revision": cp["revision"], "loja": "Loja A"})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "already_complete"


def test_confirmation_response_is_built_before_background_sharing(api, monkeypatch):
    from fastapi import BackgroundTasks
    from starlette.requests import Request as HttpRequest
    from backend.schemas.favoritos_checkpoints import FavoritosCheckpointFinalizeRequest
    client, _, _ = api
    cp = partial(client, create(client))
    events = []
    monkeypatch.setattr(endpoints, "_shared_sync_auto_enabled", lambda: True)
    monkeypatch.setattr(endpoints, "_favoritos_checkpoint_sync_confirmado", lambda *args: events.append("shared"))
    background = BackgroundTasks()
    request = HttpRequest({"type": "http", "headers": []})
    request.state.username = "owner"
    body = FavoritosCheckpointFinalizeRequest(request_id="finish", loja="Loja A", expected_revision=cp["revision"], attempt_token=cp["attempt_token"], grupo_ranking={"anuncios": [candidate()]})
    response = endpoints.favoritos_checkpoints_finalize(cp["id"], body, request, background, client_id="client-a")
    assert response["confirmed"] and response["checkpoint"]["estado_fechamento"] == "confirmado"
    assert response["realtime_sync"]["scheduled"]
    assert events == [] and len(background.tasks) == 1


def test_full_240_candidate_pool_accepts_unicode_descriptions_within_bound(api):
    client, _, _ = api
    terms = [{"campo": index, "termo": "produto " + str(index)} for index in range(1, 4)]
    cp = create(client, terms=terms)
    description = "\U0001f680" * 8000
    items = [{**candidate(index + field * 100, "produto " + str(field)), "descricao": description}
             for field in range(1, 4) for index in range(1, 81)]
    cp = progress(client, cp, candidatos=items)
    assert len(cp["candidatos"]) == 240
    assert all(item["descricao"] == description for item in cp["candidatos"])
    restarted = client.get(BASE + "/" + cp["id"], params={"loja": "Loja A"}).json()["checkpoint"]
    assert len(restarted["candidatos"]) == 240 and restarted["candidatos"][-1]["descricao"] == description
    assert all(sum(term["termo"] in item["pesquisas_origem"] for item in restarted["candidatos"]) == 80 for term in terms)


def test_document_bound_and_individual_limits_remain_enforced():
    from pydantic import ValidationError
    from backend.schemas.favoritos_checkpoints import CheckpointModel, FavoritosCheckpointCandidato, MAX_CHECKPOINT_BYTES
    class BoundedDocument(CheckpointModel):
        text: str
    # Unicode is measured as UTF-8 bytes, rather than character count.
    with pytest.raises(ValidationError, match="limite de tamanho"):
        BoundedDocument(text="\U0001f680" * (MAX_CHECKPOINT_BYTES // 4 + 1))
    with pytest.raises(ValidationError):
        FavoritosCheckpointCandidato(id="MLB1", descricao="a" * 8001)


def test_explicit_empty_ranking_preserves_complete_pool_and_can_resume_calculation(api):
    client, _, _ = api
    cp = progress(client, create(client), candidatos=[candidate(), candidate(2)],
                  termos=[{"campo": 1, "termo": "produto", "estado": "concluida", "confirmed_keys": ["mlb:mlb1", "mlb:mlb2"]}], estado_coleta="concluida")
    pool = copy.deepcopy(cp["candidatos"])
    group = {"anuncios": [], "removidos_ia": []}
    response = finalize(client, cp, grupo=group)
    assert response.status_code == 200, response.text
    result = response.json(); closed = result["checkpoint"]
    assert result["confirmed"] and result["historico_entry"] is None
    assert closed["total_rankeados"] == 0 and closed["history_entry_id"] is None
    assert closed["estado_fechamento"] == "confirmado" and closed["estado_coleta"] == "parcial"
    assert closed["termos"] == cp["termos"] and closed["candidatos"] == pool
    assert finalize(client, cp, grupo=group).json()["checkpoint"]["revision"] == closed["revision"]
    assert history._favoritos_carregar_historico("client-a", "owner")["historico"] == []
    metadata = client.get(BASE, params={"loja": "Loja A"}).json()["checkpoints"][0]
    assert metadata["total_rankeados"] == 0 and metadata["total_candidatos"] == 2 and metadata["retomavel"]
    restarted = client.get(BASE + "/" + cp["id"], params={"loja": "Loja A"}).json()["checkpoint"]
    assert restarted["total_rankeados"] == 0 and restarted["candidatos"] == pool
    response = client.post(BASE + "/" + cp["id"] + "/retomar", json={"request_id": "resume", "expected_revision": closed["revision"], "loja": "Loja A"})
    child = response.json()["checkpoint"]
    assert child["total_rankeados"] is None and child["termos"] == cp["termos"] and child["candidatos"] == pool
    done = finalize(client, child, "finish-child", {"anuncios": [candidate()]}).json()
    assert done["checkpoint"]["estado_coleta"] == "concluida" and done["checkpoint"]["total_rankeados"] == 1
    assert len(history._favoritos_carregar_historico("client-a", "owner")["historico"]) == 1
    assert client.get(BASE + "/" + cp["id"], params={"loja": "Loja A"}).json()["checkpoint"]["total_rankeados"] == 0


def test_store_empty_calculation_is_frozen_without_calling_history(tmp_path):
    publications = []
    store = service.CheckpointStore(str(tmp_path), "owner", lambda entry: publications.append(entry), lambda summaries: summaries)
    request = {"request_id": "start", "sku": "AVULSO", "titulo": "Produto", "termos": [{"campo": 1, "termo": "produto"}], "opcoes": {"max_anuncios": 80}}
    cp = store.create(request, "Loja A")
    cp = store.update(cp["id"], {"request_id": "collect", "expected_revision": cp["revision"], "attempt_token": cp["attempt_token"],
                    "candidatos": [candidate()], "termos": [{"campo": 1, "termo": "produto", "estado": "concluida"}], "estado_coleta": "concluida"}, "Loja A")
    closing = {"request_id": "finish", "expected_revision": cp["revision"], "attempt_token": cp["attempt_token"], "grupo_ranking": {"anuncios": []}}
    evaluations = []
    def calculate(data, group):
        evaluations.append(data["id"])
        return None
    first = store.finalize(cp["id"], closing, "Loja A", calculate)
    replay = store.finalize(cp["id"], closing, "Loja A", calculate)
    assert first["checkpoint"]["total_rankeados"] == 0 and first["checkpoint"]["estado_coleta"] == "parcial"
    assert first["checkpoint"]["termos"][0]["estado"] == "concluida"
    assert first["checkpoint"]["revision"] == replay["checkpoint"]["revision"]
    assert evaluations == [cp["id"]] and publications == []
