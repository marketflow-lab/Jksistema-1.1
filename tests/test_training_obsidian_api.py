from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.modules.context_hub import api as hub
from backend.modules.perguntas_pos_venda.endpoints import store_config, training
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.services import integracoes, cadastro_compatibilidade, ia_treinamento_ppv


@pytest.fixture()
def editor_api(monkeypatch, tmp_path):
    info = tmp_path / "info"
    info.mkdir()
    hub.stop_all_context_hub_watchers()
    hub.configure_context_hub(base_dir=tmp_path, info_root=info, surface="test")
    stores = [
        {"nome": "Loja A", "store_id": "store-a", "integracoes": {"mercadolivre": {"user_id": "100", "site_id": "MLB"}}},
        {"nome": "Loja B", "store_id": "store-b", "integracoes": {"mercadolivre": {"user_id": "200", "site_id": "MLB"}}},
    ]
    monkeypatch.setattr(integracoes, "carregar_lojas", lambda _client: stores)
    monkeypatch.setattr(store_config, "carregar_lojas", lambda _client: stores)
    monkeypatch.setattr(store_config, "_perguntas_loja_configs_carregar", lambda _client: {})
    monkeypatch.setattr(store_config, "_perguntas_loja_config_obter", lambda *_args: {})
    monkeypatch.setattr(store_config, "_perguntas_loja_config_normalizar", lambda value: value)
    monkeypatch.setattr(store_config, "_integracoes_nome_normalizado", lambda value: str(value).lower())
    monkeypatch.setattr(store_config, "_ml_oauth_status", lambda value: {"conectado": True})
    monkeypatch.setattr(training, "_ia_treinamento_ppv_resolver", lambda *_args, **_kwargs: {"orientacoes_pos_venda": "Pos-venda preservado"})
    monkeypatch.setattr(training, "_ia_treinamento_ppv_tipo_normalizar", lambda value: value or "perguntas_anuncio")
    monkeypatch.setattr(training, "_ia_treinamento_ppv_listar_skus", lambda _client, store, strict=False: [{"sku": "001", "nome": store}])
    app = FastAPI()
    app.dependency_overrides[get_tenant_id] = lambda: "tenant-a"
    app.get("/lojas")(store_config.ml_perguntas_listar_lojas)
    app.get("/training")(training.ml_ia_treinamento_obter)
    app.post("/training")(training.ml_ia_treinamento_salvar)
    app.get("/skus")(training.ml_ia_treinamento_listar_skus)
    with TestClient(app) as client:
        yield client, info, stores
    hub.stop_all_context_hub_watchers()


def test_store_api_identity_drives_editor_and_catalog(editor_api):
    client, _info, _stores = editor_api
    stores = client.get("/lojas").json()["lojas"]
    assert [store["store_id"] for store in stores] == ["store-a", "store-b"]
    store = stores[0]
    snapshot = client.get("/training", params={"store_id": store["store_id"]})
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["editorial"]["revision"]
    assert client.get("/skus", params={"store_id": store["store_id"]}).json()["produtos"] == [{"sku": "001", "nome": "store-a"}]


def test_general_and_new_sku_roundtrip_preserve_text_and_other_store(editor_api):
    client, _info, _stores = editor_api
    before = client.get("/training", params={"store_id": "store-a"}).json()
    text = "  Orientacao integral\n\n- Primeiro\n- Segundo  "
    response = client.post("/training", json={"store_id": "store-a", "edit_target": "general", "expected_revision": before["editorial"]["revision"], "orientacoes": text})
    assert response.status_code == 200, response.text
    general = response.json()
    assert general["orientacoes"] == text
    assert general["orientacoes_pos_venda"] == "Pos-venda preservado"
    assert general["editorial"]["general"]["status"] == "draft"
    response = client.post("/training", json={"store_id": "store-a", "edit_target": "sku", "expected_revision": general["editorial"]["revision"], "sku": "001", "notas_sku": "  Nota\n\nSKU  ", "orientacoes": "nao substituir"})
    assert response.status_code == 200, response.text
    sku = response.json()
    assert sku["orientacoes"] == text
    assert sku["notas_sku"]["001"] == "  Nota\n\nSKU  "
    assert client.get("/training", params={"store_id": "store-a"}).json()["notas_sku"] == sku["notas_sku"]
    other = client.get("/training", params={"store_id": "store-b"}).json()
    assert other["orientacoes"] == ""
    assert other["notas_sku"] == {}
    removed = client.post("/training", json={"store_id": "store-a", "edit_target": "sku", "expected_revision": sku["editorial"]["revision"], "sku": "001", "notas_sku": ""})
    assert removed.status_code == 200, removed.text
    assert not removed.json()["notas_sku"].get("001")
    assert removed.json()["orientacoes"] == text
    cleared = client.post("/training", json={"store_id": "store-a", "edit_target": "general", "expected_revision": removed.json()["editorial"]["revision"], "orientacoes": ""})
    assert cleared.status_code == 200, cleared.text
    assert client.get("/training", params={"store_id": "store-a"}).json()["orientacoes"] == ""


def test_public_writes_require_revision_and_exact_catalog_membership(editor_api):
    client, _info, _stores = editor_api
    before = client.get("/training", params={"store_id": "store-a"}).json()
    payload = {"store_id": "store-a", "edit_target": "general", "orientacoes": "A"}
    assert client.post("/training", json=payload).status_code == 428
    payload["expected_revision"] = before["editorial"]["revision"]
    assert client.post("/training", json=payload).status_code == 200
    assert client.post("/training", json=payload).status_code == 409
    payload.update(edit_target="sku", sku="not-in-store")
    assert client.post("/training", json=payload).json()["detail"]["code"] == "sku_store_scope_unresolved"
    assert client.get("/training", params={"store_id": "another-tenant-store"}).status_code == 409


def test_catalog_failure_is_not_successful_empty_result(editor_api, monkeypatch):
    client, _info, _stores = editor_api
    def fail(*_args, **_kwargs):
        raise OSError("unavailable")
    monkeypatch.setattr(training, "_ia_treinamento_ppv_listar_skus", fail)
    response = client.get("/skus", params={"store_id": "store-a"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "store_catalog_read_failed"


def test_post_sale_save_remains_compatible(editor_api, monkeypatch):
    client, _info, _stores = editor_api
    monkeypatch.setattr(training, "_ia_treinamento_ppv_salvar", lambda *_args, **_kwargs: {"orientacoes_pos_venda": "Atendimento"})
    response = client.post("/training", json={"store_id": "store-a", "tipo": "pos_venda", "orientacoes": "Atendimento"})
    assert response.status_code == 200
    assert response.json()["orientacoes_pos_venda"] == "Atendimento"


def test_catalog_strict_raises_and_legacy_keeps_fallback(monkeypatch):
    monkeypatch.setattr(ia_treinamento_ppv, "logger", SimpleNamespace(warning=lambda *_args: None))
    def fail(*_args, **_kwargs):
        raise OSError("unavailable")
    monkeypatch.setattr(cadastro_compatibilidade, "mesclar_produtos_legados_com_contexto_loja", fail)
    with pytest.raises(OSError):
        ia_treinamento_ppv._ia_treinamento_ppv_listar_skus("tenant-a", "store-a", strict=True)
    assert ia_treinamento_ppv._ia_treinamento_ppv_listar_skus("tenant-a", "store-a") == []


@pytest.mark.parametrize("failure", [OSError("unavailable"), sqlite3.OperationalError("busy"), training.ContextHubConflictError("locked")])
def test_storage_failure_has_explicit_sync_error(editor_api, monkeypatch, failure):
    client, _info, _stores = editor_api
    def fail(*_args, **_kwargs):
        raise failure
    monkeypatch.setattr(training, "load_store_guidance_editor", fail)
    response = client.get("/training", params={"store_id": "store-a"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "editorial_read_failed"
    monkeypatch.setattr(training, "save_store_guidance_editor", fail)
    response = client.post("/training", json={"store_id": "store-a", "edit_target": "general", "expected_revision": "revision", "orientacoes": "Texto"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "editorial_write_failed"


def test_external_obsidian_edit_is_visible_and_stale_save_is_rejected(editor_api):
    client, info, _stores = editor_api
    initial = client.get("/training", params={"store_id": "store-a"}).json()
    payload = {"store_id": "store-a", "edit_target": "general", "expected_revision": initial["editorial"]["revision"], "orientacoes": "Original", "contexto_loja": "Contexto mantido"}
    saved = client.post("/training", json=payload).json()
    note = info / "tenant-a" / "ContextVault" / "80_Curadoria" / saved["editorial"]["general"]["relative_path"]
    note.write_text(note.read_text(encoding="utf-8").replace('"Original"', '"Editado no Obsidian"'), encoding="utf-8")
    payload["expected_revision"] = saved["editorial"]["revision"]
    assert client.post("/training", json=payload).status_code == 409
    current = client.get("/training", params={"store_id": "store-a"}).json()
    assert current["orientacoes"] == "Editado no Obsidian"
    assert current["contexto_loja"] == "Contexto mantido"
    payload.update(expected_revision=current["editorial"]["revision"], orientacoes="")
    cleared = client.post("/training", json=payload)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["orientacoes"] == ""
    assert cleared.json()["contexto_loja"] == "Contexto mantido"
