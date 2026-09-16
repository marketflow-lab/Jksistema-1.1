import json
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from backend.modules.context_hub import api as hub
from backend.modules.context_hub.contracts import ContextHubConflictError, ContextHubValidationError
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.store_sku_repository import publish_store_sku_generation
from scripts import repair_sku_232_2 as repair_module

TENANT = "repair-test"
STORE = {"store_ref": "store-a", "store_name": "Loja A", "seller_id": "1234", "site_id": "MLB"}
OTHER_STORE = {"store_ref": "store-b", "store_name": "Loja B", "seller_id": "5678", "site_id": "MLB"}
ITEM = repair_module.ITEM_ID


def dossier():
    return {
        "sku": "232-2", "nome_produto": "Par de puxadores internos traseiros pretos BMW F30/F31",
        "oem": {"status": "documentado", "códigos": ["51427281465"]},
        "aplicação": {"tipo": "automóvel", "veículos_compatíveis": {"status": "documentado", "itens": [
            {"marca": "BMW", "modelo": "Série 3 F30", "anos": ["2011 a 2015"]},
        ]}},
        "revisão": {"anos_exibidos_em_cada_modelo": True, "nota_interna": "Preservar revisão"},
        "atualizado_em": "2026-09-01T00:00:00Z", "campo_usuario": {"valor": "Preservar canônico"},
    }


def publish(root, *, store=STORE, tenant=TENANT, item=ITEM, canonical=None, general=None):
    return publish_store_sku_generation(
        tenant, store, canonical_documents=canonical or {
            "232-2": dossier(), "other": {"sku": "other", "nome_produto": "Outro produto"}},
        store_guidance=general or {"orientacoes_perguntas": "Orientação geral preservada"},
        sku_guidance={"232-2": {"notas": "Orientação específica preservada"}, "other": {"notas": "Outra nota"}},
        bindings=[{"item_id": item, "sku": "232-2"}, {"item_id": "MLB999", "sku": "other"}],
        info_root=root, preserve_curated_files=False,
    )


@pytest.fixture()
def prepared(tmp_path):
    info, base = tmp_path / "info", tmp_path / "app"
    info.mkdir()
    base.mkdir()
    hub.stop_all_context_hub_watchers()
    hub.configure_context_hub(base_dir=base, info_root=info, surface="test")
    publish(info)
    publish(info, store=OTHER_STORE, item="MLB888")
    publish(info, tenant="other-tenant", item="MLB777")
    paths = _tenant_paths(TENANT, info_root=info)
    target = paths.tenant_dir / "SKU" / "232-2.json"
    target.parent.mkdir()
    target.write_bytes(("\ufeff" + json.dumps(dossier(), ensure_ascii=False, indent=2) + "\r\n").encode("utf-8"))
    extra = paths.tenant_dir / "SKU" / "other.json"
    extra.write_text('{"sku":"other","custom":"conservar"}', encoding="utf-8")
    editorial = next(paths.curated_dir.glob("Lojas/store-a--*/SKUs/232-2/Orientacoes.md"))
    editorial.write_text(editorial.read_text(encoding="utf-8") + "\n## Edição ainda não publicada\nPreservar.\n", encoding="utf-8")
    yield info, paths, target
    hub.stop_all_context_hub_watchers()


def call(root, **overrides):
    kwargs = {"info_root": root, "store_ref": STORE["store_ref"], "seller_id": STORE["seller_id"],
              "site_id": STORE["site_id"], "item_id": ITEM, **overrides}
    tenant = kwargs.pop("tenant", TENANT)
    return repair_module.repair(tenant, **kwargs)


def snapshot(root, store=STORE, tenant=TENANT):
    paths = _tenant_paths(tenant, info_root=root)
    scope = repair_module._scope_for_paths(paths, store)
    item = ITEM if tenant == TENANT and store == STORE else ("MLB888" if tenant == TENANT else "MLB777")
    return repair_module._snapshot(paths, scope, item)


def tree_bytes(root):
    # SQLite's read-side locking can maintain SHM/WAL sidecars without changing
    # logical data. Canonical, curated, main DB and all other files must stay put.
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*")
            if path.is_file() and not path.name.endswith((".db-shm", ".db-wal"))}


def test_preview_is_read_only_and_reports_no_bodies(prepared):
    root, paths, target = prepared
    before = tree_bytes(root)
    report = call(root)
    assert report["applied"] is False
    assert report["canonical_changed_count"] == report["projection_changed_count"] == 1
    assert tree_bytes(root) == before
    rendered = json.dumps(report)
    assert "puxadores" not in rendered and "232-2" not in rendered
    assert "MLB2781314798" not in rendered and str(root) not in rendered


def test_missing_tenant_preview_creates_nothing(prepared):
    root, _, _ = prepared
    before = tree_bytes(root)
    with pytest.raises(ContextHubValidationError, match="store_database_missing"):
        call(root, tenant="absent-tenant")
    assert tree_bytes(root) == before
    assert not (root / "absent-tenant").exists()


def test_apply_preserves_guidance_other_products_stores_and_tenants(prepared):
    root, paths, target = prepared
    before = snapshot(root)
    other_store, other_tenant = snapshot(root, OTHER_STORE), snapshot(root, tenant="other-tenant")
    curated = tree_bytes(paths.curated_dir)
    other_file = (target.parent / "other.json").read_bytes()
    report = call(root, apply=True)
    after = snapshot(root)
    assert report["applied"] is True
    assert after["generation_id"] != before["generation_id"]
    corrected = json.loads(target.read_bytes())
    assert corrected["oem"]["códigos"] == ["51427281465", "51427281466"]
    assert corrected == after["canonical"]["232-2"]
    assert corrected["campo_usuario"] == dossier()["campo_usuario"]
    vehicles = corrected["aplicação"]["veículos_compatíveis"]["itens"]
    assert any(row["modelo"] == "318d F31 Touring" and row["anos"] == [] for row in vehicles)
    assert after["canonical"]["OTHER"] == before["canonical"]["OTHER"]
    for key in ("general", "guidance", "bindings"):
        assert after[key] == before[key]
    assert snapshot(root, OTHER_STORE) == other_store
    assert snapshot(root, tenant="other-tenant") == other_tenant
    assert tree_bytes(paths.curated_dir) == curated
    assert (target.parent / "other.json").read_bytes() == other_file
    note = next(paths.generated_dir.glob("Lojas/store-a--*/SKUs/232-2/Contexto.md"), None)
    if note is None:
        note = next(path for path in paths.generated_dir.rglob("*.md") if "51427281466" in path.read_text(encoding="utf-8"))
    assert "51427281466" in note.read_text(encoding="utf-8")


def test_repeated_apply_is_idempotent(prepared):
    root, paths, _ = prepared
    call(root, apply=True)
    before = tree_bytes(root)
    report = call(root, apply=True)
    assert report["applied"] is True and report["changed"] is False
    assert tree_bytes(root) == before


@pytest.mark.parametrize("change", [
    {"store_ref": "absent-store"}, {"seller_id": "9999"}, {"site_id": "MLA"},
    {"item_id": "MLB2781320522"}, {"store_id": "another-store"},
    {"expected_canonical_sha256": "0" * 64}, {"expected_generation_id": "obsolete"},
])
def test_wrong_identity_or_expected_revision_fails_before_write(prepared, change):
    root, _, _ = prepared
    before = tree_bytes(root)
    with pytest.raises((ContextHubValidationError, ContextHubConflictError)):
        call(root, apply=True, **change)
    assert tree_bytes(root) == before


def test_missing_exact_item_binding_is_not_replaced_by_another_sku_listing(prepared):
    root, _, _ = prepared
    publish(root, item="MLB2781320522")
    before = tree_bytes(root)
    with pytest.raises(ContextHubValidationError, match="exact_listing_sku_binding_missing"):
        call(root, apply=True)
    assert tree_bytes(root) == before


def test_publication_failure_rolls_back_exact_original_bytes(prepared, monkeypatch):
    root, _, target = prepared
    before, active = target.read_bytes(), snapshot(root)

    def fail(*args, **kwargs):
        raise RuntimeError("simulated publication failure")

    monkeypatch.setattr(repair_module, "publish_store_sku_generation", fail)
    with pytest.raises(ContextHubConflictError, match="publication_failed_canonical_restored"):
        call(root, apply=True)
    assert target.read_bytes() == before  # Includes original BOM and CRLF.
    assert snapshot(root) == active


def test_concurrent_canonical_change_before_write_is_preserved(prepared, monkeypatch):
    root, _, target = prepared
    original = repair_module.repair_sku_232_2_dossier
    concurrent = json.dumps({**dossier(), "campo_usuario": "Editado externamente"}).encode()

    def race(value):
        target.write_bytes(concurrent)
        return original(value)

    monkeypatch.setattr(repair_module, "repair_sku_232_2_dossier", race)
    before = snapshot(root)
    with pytest.raises(ContextHubConflictError, match="canonical_sku_revision_conflict"):
        call(root, apply=True)
    assert target.read_bytes() == concurrent
    assert snapshot(root) == before


def test_failed_publication_never_overwrites_concurrent_canonical_edit(prepared, monkeypatch):
    root, _, target = prepared
    concurrent = json.dumps({**dossier(), "campo_usuario": "Nova edição durante publicação"}).encode()

    def fail(*args, **kwargs):
        target.write_bytes(concurrent)
        raise RuntimeError("simulated concurrent writer")

    monkeypatch.setattr(repair_module, "publish_store_sku_generation", fail)
    with pytest.raises(ContextHubConflictError, match="canonical_sku_revision_conflict"):
        call(root, apply=True)
    assert target.read_bytes() == concurrent


def test_edit_during_canonical_staging_is_preserved(prepared, monkeypatch):
    root, _, target = prepared
    original_writer = repair_module._write_text_atomic
    concurrent = json.dumps({**dossier(), "campo_usuario": "Editado durante staging"}).encode()

    def race(staged, content):
        original_writer(staged, content)
        target.write_bytes(concurrent)

    monkeypatch.setattr(repair_module, "_write_text_atomic", race)
    with pytest.raises(ContextHubConflictError, match="canonical_sku_revision_conflict"):
        call(root, apply=True)
    assert target.read_bytes() == concurrent
    assert not list(target.parent.glob("*.repair"))


def test_competing_generation_is_not_overwritten_and_canonical_is_compensated(prepared, monkeypatch):
    root, _, target = prepared
    before = target.read_bytes()
    real_publish = repair_module.publish_store_sku_generation

    def race(*args, **kwargs):
        publish(root, general={"orientacoes_perguntas": "Nova versão concorrente"})
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(repair_module, "publish_store_sku_generation", race)
    with pytest.raises(ContextHubConflictError, match="publication_failed_canonical_restored"):
        call(root, apply=True)
    assert target.read_bytes() == before
    assert snapshot(root)["general"] == {"orientacoes_perguntas": "Nova versão concorrente"}


def test_lost_acknowledgement_after_commit_does_not_revert_canonical(prepared, monkeypatch):
    root, _, target = prepared
    real_publish = repair_module.publish_store_sku_generation

    def committed_then_error(*args, **kwargs):
        real_publish(*args, **kwargs)
        raise RuntimeError("response lost after commit")

    monkeypatch.setattr(repair_module, "publish_store_sku_generation", committed_then_error)
    report = call(root, apply=True)
    assert report["publication_acknowledgement_recovered"] is True
    assert report["applied"] is True
    assert json.loads(target.read_bytes()) == snapshot(root)["canonical"]["232-2"]


def test_commit_then_competing_publication_preserves_repaired_canonical(prepared, monkeypatch):
    root, _, target = prepared
    real_publish = repair_module.publish_store_sku_generation

    def committed_then_competitor(*args, **kwargs):
        real_publish(*args, **kwargs)
        publish(root, canonical=kwargs["canonical_documents"],
                general={"orientacoes_perguntas": "Edição posterior ao commit"})
        raise RuntimeError("response lost after another publisher")

    monkeypatch.setattr(repair_module, "publish_store_sku_generation", committed_then_competitor)
    with pytest.raises(ContextHubConflictError, match="publication_state_changed_canonical_preserved"):
        call(root, apply=True)
    current = snapshot(root)
    assert current["general"] == {"orientacoes_perguntas": "Edição posterior ao commit"}
    assert json.loads(target.read_bytes()) == current["canonical"]["232-2"]
    assert current["canonical"]["232-2"]["oem"]["códigos"] == ["51427281465", "51427281466"]


def test_tampered_hash_is_not_trusted(prepared):
    root, paths, _ = prepared
    active = snapshot(root)
    with sqlite3.connect(paths.db_path) as connection:
        connection.execute("UPDATE context_hub_store_sku_documents SET content_hash=? "
                           "WHERE generation_id=? AND sku='232-2' AND knowledge_role='canonical_sku'",
                           ("0" * 64, active["generation_id"]))
    connection.close()
    before = tree_bytes(root)
    with pytest.raises(ContextHubValidationError, match="generation_document_hash_mismatch"):
        call(root, apply=True)
    assert tree_bytes(root) == before


def test_preview_reads_recent_wal_commits_without_logical_mutations(prepared):
    root, paths, _ = prepared
    connection = sqlite3.connect(paths.db_path)
    try:
        version = snapshot(root)["version"]
        connection.execute("UPDATE context_hub_store_sku_active_generations SET version=version+1")
        connection.commit()
        before = tree_bytes(root)
        before_dump = tuple(connection.iterdump())
        assert call(root)["applied"] is False
        assert snapshot(root)["version"] == version + 1
        assert tree_bytes(root) == before
        assert tuple(connection.iterdump()) == before_dump
    finally:
        connection.close()


@pytest.fixture()
def missing_binding(prepared):
    root, paths, _ = prepared
    publish(root, item="MLB2781320522")
    (paths.tenant_dir / "lojas_config.json").write_text(json.dumps([{
        "store_id": STORE["store_ref"], "nome": STORE["store_name"],
        "integracoes": {"mercadolivre": {
            "user_id": STORE["seller_id"], "site_id": STORE["site_id"],
            "access_token": "synthetic-token-not-a-real-credential",
        }},
    }]), encoding="utf-8")
    return prepared


def live_item(**changes):
    return {"id": ITEM, "seller_id": int(STORE["seller_id"]), "site_id": "MLB",
            "status": "active", "variations": [], "seller_custom_field": None,
            "attributes": [{"id": "SELLER_SKU", "value_name": "232-2"}], **changes}


def mock_lookup(monkeypatch, payload, *, status=200):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=status, json=lambda: payload, close=lambda: None)

    monkeypatch.setattr(repair_module.requests, "get", get)
    return calls


def test_refresh_binding_preview_proves_identity_without_mutations(missing_binding, monkeypatch):
    root, _, _ = missing_binding
    before = tree_bytes(root)
    calls = mock_lookup(monkeypatch, live_item())
    report = call(root, refresh_binding=True)
    assert report["applied"] is False and report["added_bindings"] == 1
    assert tree_bytes(root) == before
    assert len(calls) == 1
    url, options = calls[0]
    assert url == "https://api.mercadolibre.com/items/MLB2781314798"
    assert options["timeout"] == 25 and options["allow_redirects"] is False
    assert options["headers"]["Authorization"].startswith("Bearer ")
    assert "synthetic-token" not in json.dumps(report)


def test_refresh_binding_apply_adds_only_exact_binding_and_is_idempotent(missing_binding, monkeypatch):
    root, paths, target = missing_binding
    prior = repair_module._snapshot(paths, repair_module._scope_for_paths(paths, STORE),
                                    "MLB2781320522")
    curated = tree_bytes(paths.curated_dir)
    calls = mock_lookup(monkeypatch, live_item(seller_custom_field="232-2"))
    report = call(root, refresh_binding=True, apply=True)
    current = snapshot(root)
    assert report["applied"] is True and report["added_bindings"] == 1
    assert current["bindings"] == sorted([
        *prior["bindings"], {"item_id": ITEM, "variation_id": "", "sku": "232-2"},
    ], key=lambda row: (row["item_id"], row["variation_id"], row["sku"]))
    assert current["general"] == prior["general"] and current["guidance"] == prior["guidance"]
    assert current["canonical"]["OTHER"] == prior["canonical"]["OTHER"]
    assert tree_bytes(paths.curated_dir) == curated
    assert json.loads(target.read_bytes()) == current["canonical"]["232-2"]
    after = tree_bytes(root)
    repeated = call(root, refresh_binding=True, apply=True)
    assert repeated["changed"] is False and repeated["added_bindings"] == 0
    assert len(calls) == 1  # Existing exact evidence needs no extra network call.
    assert tree_bytes(root) == after


@pytest.mark.parametrize("changes", [
    {"id": "MLB1"}, {"seller_id": 999}, {"site_id": "MLA"}, {"status": "paused"},
    {"variations": [{"id": 1}]}, {"variations": None},
    {"attributes": []}, {"attributes": [{"id": "SELLER_SKU", "value_name": "wrong"}]},
    {"seller_custom_field": "another-sku"},
    {"attributes": [{"id": "SELLER_SKU", "value_name": "232-2"},
                    {"id": "SELLER_SKU", "value_name": "wrong"}]},
])
def test_refresh_binding_rejects_unproven_or_conflicting_live_identity(missing_binding, monkeypatch, changes):
    root, _, _ = missing_binding
    before = tree_bytes(root)
    mock_lookup(monkeypatch, live_item(**changes))
    with pytest.raises(ContextHubValidationError, match="binding_live_"):
        call(root, refresh_binding=True, apply=True)
    assert tree_bytes(root) == before


@pytest.mark.parametrize("config_change", [
    {"user_id": "999"}, {"seller_id": "999"}, {"site_id": "MLA"}, {"access_token": ""},
])
def test_refresh_binding_rejects_invalid_local_identity_before_network(missing_binding, monkeypatch, config_change):
    root, paths, _ = missing_binding
    config_path = paths.tenant_dir / "lojas_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config[0]["integracoes"]["mercadolivre"].update(config_change)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    calls = mock_lookup(monkeypatch, live_item())
    before = tree_bytes(root)
    with pytest.raises(ContextHubValidationError, match="binding_(store_config_identity_mismatch|access_token_missing)"):
        call(root, refresh_binding=True, apply=True)
    assert calls == []
    assert tree_bytes(root) == before


@pytest.mark.parametrize("failure", ["timeout", "forbidden", "redirect", "invalid_json"])
def test_refresh_binding_network_failure_closes_without_mutations(missing_binding, monkeypatch, failure):
    root, _, _ = missing_binding

    def failed_get(*args, **kwargs):
        if failure == "timeout":
            raise repair_module.requests.Timeout("synthetic request detail")
        def decoded():
            raise ValueError("synthetic invalid JSON")
        return SimpleNamespace(status_code={"forbidden": 403, "redirect": 302}.get(failure, 200),
                               json=decoded, close=lambda: None)

    monkeypatch.setattr(repair_module.requests, "get", failed_get)
    before = tree_bytes(root)
    with pytest.raises(ContextHubValidationError, match="binding_live_lookup_failed"):
        call(root, refresh_binding=True, apply=True)
    assert tree_bytes(root) == before


def test_refresh_binding_never_replaces_existing_conflicting_binding(missing_binding, monkeypatch):
    root, _, _ = missing_binding
    publish_store_sku_generation(
        TENANT, STORE, canonical_documents={"232-2": dossier(), "OTHER": {"sku": "OTHER"}},
        store_guidance={}, sku_guidance={}, bindings=[
            {"item_id": "MLB2781320522", "sku": "232-2"}, {"item_id": ITEM, "sku": "OTHER"},
        ], preserve_curated_files=True, info_root=root,
    )
    calls = mock_lookup(monkeypatch, live_item())
    before = tree_bytes(root)
    with pytest.raises(ContextHubValidationError, match="exact_listing_sku_binding_mismatch"):
        call(root, refresh_binding=True, apply=True)
    assert calls == [] and tree_bytes(root) == before


def test_refresh_binding_publication_failure_compensates_canonical(missing_binding, monkeypatch):
    root, paths, target = missing_binding
    original = target.read_bytes()
    prior = repair_module._snapshot(paths, repair_module._scope_for_paths(paths, STORE), "MLB2781320522")
    mock_lookup(monkeypatch, live_item())

    def failed_publish(*args, **kwargs):
        raise RuntimeError("publication unavailable")

    monkeypatch.setattr(repair_module, "publish_store_sku_generation", failed_publish)
    with pytest.raises(ContextHubConflictError, match="publication_failed_canonical_restored"):
        call(root, refresh_binding=True, apply=True)
    assert target.read_bytes() == original
    assert repair_module._snapshot(paths, repair_module._scope_for_paths(paths, STORE), "MLB2781320522") == prior


@pytest.mark.parametrize("error,code", [
    (ContextHubConflictError("Outra operacao do Context Hub esta em andamento."), "context_hub_operation_locked"),
    (ContextHubConflictError("canonical_sku_revision_conflict"), "canonical_sku_revision_conflict"),
    (ContextHubValidationError("binding_live_identity_mismatch"), "binding_live_identity_mismatch"),
    (RuntimeError("synthetic-secret /private/path"), "repair_failed"),
])
def test_cli_reports_only_allowlisted_error_codes(monkeypatch, capsys, error, code):
    monkeypatch.setattr(sys, "argv", ["repair", "--tenant", TENANT, "--info-root", "unused",
        "--store-ref", STORE["store_ref"], "--seller-id", STORE["seller_id"],
        "--site-id", "MLB", "--item-id", ITEM])

    def fail(**kwargs):
        raise error

    monkeypatch.setattr(repair_module, "repair", fail)
    assert repair_module.main() == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {"applied": False, "error_type": type(error).__name__, "error_code": code}
    assert "synthetic-secret" not in output and "/private/path" not in output


def test_cli_reports_safe_nested_cause_without_exception_payload(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["repair", "--tenant", TENANT, "--info-root", "unused",
        "--store-ref", STORE["store_ref"], "--seller-id", STORE["seller_id"],
        "--site-id", "MLB", "--item-id", ITEM])

    def fail(**kwargs):
        raise ContextHubConflictError("projection_publication_failed_canonical_restored") from PermissionError("synthetic-secret /private/path")

    monkeypatch.setattr(repair_module, "repair", fail)
    assert repair_module.main() == 1
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["error_code"] == "projection_publication_failed_canonical_restored"
    assert report["cause_error_type"] == "PermissionError" and report["cause_error_code"] == "repair_failed"
    assert "synthetic-secret" not in output and "/private/path" not in output
