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


def test_examples_live_in_exact_sku_note_and_partial_edits_preserve_them(editor_api):
    client, info, _stores = editor_api
    state = client.get("/training", params={"store_id": "store-a"}).json()

    def save(target, **fields):
        nonlocal state
        response = client.post("/training", json={"store_id": "store-a", "edit_target": target,
            "expected_revision": state["editorial"]["revision"], **fields})
        assert response.status_code == 200, response.text
        state = response.json()

    general = {"pergunta": "Atendimento?", "resposta": "Bom dia", "sku": ""}
    specific = {"pergunta": "  Tensão?  ", "resposta": "12 V\n", "sku": "001", "metadata": {"retain": True}}
    save("general", orientacoes="Regra da loja", exemplos=[general])
    save("sku", sku="001", notas_sku="  Nota existente  ")
    save("sku", sku="001", exemplos=[specific])
    assert state["notas_sku"]["001"] == "  Nota existente  "
    assert state["exemplos"]["perguntas_anuncio"] == [general, specific]
    vault = info / "tenant-a" / "ContextVault" / "80_Curadoria"
    general_note = vault / state["editorial"]["general"]["relative_path"]
    sku_note = vault / state["editorial"]["skus"]["001"]["relative_path"]
    assert "Tensão" not in general_note.read_text(encoding="utf-8")
    assert "Tensão" in sku_note.read_text(encoding="utf-8")
    save("general", orientacoes="Regra alterada")
    save("sku", sku="001", notas_sku="Nota alterada")
    assert state["exemplos"]["perguntas_anuncio"] == [general, specific]
    save("general", exemplos=[])
    assert state["exemplos"]["perguntas_anuncio"] == [specific]
    save("sku", sku="001", exemplos=[])
    assert state["exemplos"]["perguntas_anuncio"] == []
    assert state["notas_sku"]["001"] == "Nota alterada"
    assert client.get("/training", params={"store_id": "store-b"}).json()["exemplos"]["perguntas_anuncio"] == []


@pytest.mark.parametrize("target,sku,example_sku", [("general", "", "001"), ("sku", "001", "002"), ("sku", "001", "")])
def test_examples_cannot_cross_note_scope(editor_api, target, sku, example_sku):
    client, _info, _stores = editor_api
    before = client.get("/training", params={"store_id": "store-a"}).json()
    response = client.post("/training", json={"store_id": "store-a", "edit_target": target,
        "sku": sku, "expected_revision": before["editorial"]["revision"],
        "exemplos": [{"pergunta": "Pergunta", "resposta": "Resposta", "sku": example_sku}]})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "example_scope_mismatch"
    after = client.get("/training", params={"store_id": "store-a"}).json()
    assert after["editorial"]["revision"] == before["editorial"]["revision"]


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


def _publish_details_fixture(info, *, tenant="tenant-a", store="store-a", seller="100", items=None):
    from backend.modules.context_hub.store_sku_repository import publish_store_sku_generation
    return publish_store_sku_generation(
        tenant, {"store_ref": store, "store_name": store, "seller_id": seller, "site_id": "MLB"},
        canonical_documents={"001": {"sku": "001", "nome_produto": "Sensor", "caracteristicas_tecnicas": {
            "itens": items or ["12 V", "Rosca M10"]}}},
        store_guidance={}, sku_guidance={}, bindings=[{"item_id": "MLB100", "sku": "001"}],
        preserve_curated_files=True, info_root=info,
    )


def test_sku_details_without_guidance_shows_canonical_and_exact_documents(editor_api):
    from pathlib import Path
    client, info, _stores = editor_api
    _publish_details_fixture(info)
    _publish_details_fixture(info, tenant="tenant-b", items=["Outra pessoa"])
    details = client.get("/training", params={"store_id": "store-a", "sku": "001"}).json()["sku_details"]
    assert details["canonical_document"]["nome_produto"] == "Sensor"
    assert details["guidance"] == {}
    assert any(row["value"] == "12 V" for row in details["characteristics"])
    assert details["documents"]
    assert "Outra pessoa" not in str(details)
    original = info / "tenant-a" / "ContextVault" / details["documents"][0]["relative_path"]
    raw = original.read_text(encoding="utf-8")
    for name, content in {
        "other-store.md": raw.replace("store_ref: store-a", "store_ref: store-b"),
        "other-surface.md": raw.replace("surface: mercado_livre_public_questions", "surface: private"),
        "sensitive.md": raw + "\nContact private.person@example.com\n",
    }.items():
        (original.parent / name).write_text(content, encoding="utf-8")
    current = client.get("/training", params={"store_id": "store-a", "sku": "001"}).json()["sku_details"]
    assert len(current["documents"]) == len(details["documents"])
    other = client.get("/training", params={"store_id": "store-b", "sku": "001"}).json()["sku_details"]
    assert other["canonical_document"] == {} and other["documents"] == []
    assert client.get("/training", params={"store_id": "store-a", "sku": "1"}).status_code == 409
    assert client.get("/training", params={"sku": "001"}).status_code == 409


def test_sku_characteristics_roundtrip_preserves_sources_and_reorder_does_not_retarget(editor_api):
    client, info, _stores = editor_api
    published = _publish_details_fixture(info)
    before = client.get("/training", params={"store_id": "store-a", "sku": "001"}).json()
    field = next(row for row in before["sku_details"]["characteristics"] if row["value"] == "12 V")
    payload = {"store_id": "store-a", "edit_target": "sku", "sku": "001",
               "expected_revision": before["editorial"]["revision"], "notas_sku": "Nota mantida",
               "caracteristicas_sku": {field["key"]: " 24 V\n"},
               "exemplos": [{"sku": "001", "pergunta": "Tensão?", "resposta": "Confira a ficha"}]}
    response = client.post("/training", json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["caracteristicas_sku"]["001"][field["key"]] == " 24 V\n"
    edited = next(row for row in saved["sku_details"]["characteristics"] if row["key"] == field["key"])
    assert edited["edited"] and edited["original_value"] == "12 V"
    assert saved["context_generation_id"] == published["generation_id"]
    note = info / "tenant-a" / "ContextVault" / "80_Curadoria" / saved["editorial"]["skus"]["001"]["relative_path"]
    raw = note.read_text(encoding="utf-8")
    assert "caracteristicas_fontes" in raw and "24 V" in raw and "Nota mantida" in raw
    note.write_text(raw + "\nComentario externo preservado\n", encoding="utf-8")
    payload["expected_revision"] = saved["editorial"]["revision"]
    assert client.post("/training", json=payload).status_code == 409
    _publish_details_fixture(info, items=["Rosca M10", "12 V"])
    current = client.get("/training", params={"store_id": "store-a", "sku": "001"}).json()
    rows = current["sku_details"]["characteristics"]
    assert next(row for row in rows if row["original_value"] == "Rosca M10")["value"] == "Rosca M10"
    orphan = next(row for row in rows if row["key"] == field["key"])
    assert orphan["source_missing"] and orphan["value"] == " 24 V\n"
    payload.update(expected_revision=current["editorial"]["revision"], caracteristicas_sku={})
    cleared = client.post("/training", json=payload)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["caracteristicas_sku"]["001"] == {}
    assert "Comentario externo preservado" in note.read_text(encoding="utf-8")


def test_unknown_or_wrong_target_characteristics_are_rejected(editor_api):
    client, info, _stores = editor_api
    _publish_details_fixture(info)
    state = client.get("/training", params={"store_id": "store-a"}).json()
    payload = {"store_id": "store-a", "edit_target": "sku", "sku": "001",
               "expected_revision": state["editorial"]["revision"],
               "caracteristicas_sku": {"canonical:" + "a" * 32: "Unknown"}}
    assert client.post("/training", json=payload).status_code == 422
    payload.update(edit_target="general", caracteristicas_sku={})
    assert client.post("/training", json=payload).status_code == 422


def test_preview_failure_after_saving_reports_success_and_can_be_retried(editor_api, monkeypatch):
    client, _info, _stores = editor_api
    state = client.get("/training", params={"store_id": "store-a"}).json()
    def fail(*_args, **_kwargs):
        raise OSError("temporarily unavailable")
    monkeypatch.setattr(training, "load_store_sku_details", fail)
    response = client.post("/training", json={"store_id": "store-a", "edit_target": "sku", "sku": "001",
        "expected_revision": state["editorial"]["revision"], "notas_sku": "Saved"})
    assert response.status_code == 200, response.text
    assert response.json()["success"]
    assert response.json()["sku_details_error"]["code"] == "sku_details_read_failed"
    assert client.get("/training", params={"store_id": "store-a"}).json()["notas_sku"]["001"] == "Saved"


def test_disappearing_document_does_not_hide_other_sku_information(editor_api, monkeypatch):
    from pathlib import Path
    client, info, _stores = editor_api
    _publish_details_fixture(info)
    read_text = Path.read_text
    def read(path, *args, **kwargs):
        if path.name == "vanishing.md":
            raise FileNotFoundError("removed concurrently")
        return read_text(path, *args, **kwargs)
    target = info / "tenant-a" / "ContextVault" / "70_Gerado" / "vanishing.md"
    target.write_text("gone", encoding="utf-8")
    monkeypatch.setattr(Path, "read_text", read)
    response = client.get("/training", params={"store_id": "store-a", "sku": "001"})
    assert response.status_code == 200, response.text
    assert response.json()["sku_details"]["canonical_document"]["sku"] == "001"


def test_equal_values_in_distinct_list_attributes_do_not_share_keys_after_reorder():
    from backend.modules.context_hub.store_sku_details import _canonical_characteristics
    attrs = [{"id": "current", "value": 12}, {"id": "voltage", "value": 12}]
    before = _canonical_characteristics({"attributes": attrs})
    after = _canonical_characteristics({"attributes": list(reversed(attrs))})
    old = next(row for row in before if row["label"] == "attributes / 1 / value")
    new = next(row for row in after if row["label"] == "attributes / 1 / value")
    assert old["key"] != new["key"]


def test_research_evidence_preserves_sources_and_does_not_cross_store(editor_api):
    from backend.modules.context_hub import product_evidence
    client, info, _stores = editor_api
    for store, seller, value in [("store-a", "100", 12), ("store-b", "200", 48)]:
        batch = product_evidence.create_product_evidence_batch(
            "tenant-a", store_ref=store, seller_id=seller, site_id="MLB", sku="001", info_root=info)
        source = product_evidence.add_product_evidence_source(
            "tenant-a", batch["batch_id"], url="https://manufacturer.example/specification",
            source_type="official_manufacturer", content_hash="a" * 64, info_root=info)
        product_evidence.add_product_evidence_claim(
            "tenant-a", batch["batch_id"], field_name="electrical.voltage", scope="product",
            value=value, unit="V", source_ids=[source["source_id"]], info_root=info)
        product_evidence.complete_product_evidence_batch(
            "tenant-a", batch["batch_id"], coverage_complete=True, stop_reason="coverage_complete", info_root=info)
    response = client.get("/training", params={"store_id": "store-a", "sku": "001"})
    assert response.status_code == 200, response.text
    details = response.json()["sku_details"]
    assert len(details["evidence"]) == 1
    assert details["evidence"][0]["value"] == "12"
    assert details["evidence"][0]["sources"][0]["url"] == "https://manufacturer.example/specification"
    field = next(row for row in details["characteristics"] if row["source"] == "evidence")
    saved = client.post("/training", json={"store_id": "store-a", "edit_target": "sku", "sku": "001",
        "expected_revision": response.json()["editorial"]["revision"], "caracteristicas_sku": {field["key"]: "24 V"}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["sku_details"]["evidence"] == details["evidence"]
    assert saved.json()["sku_details"]["characteristics"][0]["value"] == "24 V"
