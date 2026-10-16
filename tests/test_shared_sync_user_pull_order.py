"""A new machine imports selected store identities before dependent Cadastro."""
import hashlib
import io
import json
from types import SimpleNamespace
import zipfile

import pandas as pd
import pytest
from fastapi import HTTPException

from backend.schemas.shared_sync import SharedSyncPreviewRequest, SharedSyncUserLinkRunRequest
from backend.services import shared_sync  # noqa: F401 - bind service peers only
from backend.services import cadastro_fotos, central_accounts_client, integracoes
from backend.services import shared_sync_apply_scope as apply_scope
from backend.services import shared_sync_collect_files as collect
from backend.services import shared_sync_operations as operations
from backend.services import shared_sync_user_endpoints as endpoints
from backend.services import shared_sync_user_pairs as pairs


SCOPES = ["cadastro", "lojas_integracoes"]
REL = "cadastro_produtos_lojas.csv"


def make_bundle(scope, files):
    entries = [
        {"relative_path": name, "size": len(data), "mtime": 1,
         "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in files
    ]
    manifest = {"schema": 2, "scope": scope, "file_count": len(files), "files": entries}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in files:
            archive.writestr("files/" + name, data)
    return output.getvalue(), manifest


@pytest.fixture
def new_machine(tmp_path, monkeypatch):
    tenant = tmp_path / "tenant-a"
    tenant.mkdir()
    (tenant / "lojas_config.json").write_text("[]", encoding="utf-8")
    session = {"client_id": "tenant-a", "username": "bob",
               "permissions": {"cadastro": True, "integracao": True}}
    permissions = {"alice": {"cadastro": True, "integracao": True}, "bob": session["permissions"]}
    link = {"id": "link-test", "active": True, "scopes": list(SCOPES),
            "source_username": "alice", "source_client_id": "tenant-a",
            "target_username": "bob", "target_client_id": "tenant-a",
            "bundles": {scope: "remote-" + scope for scope in SCOPES}}
    bundles, metas = {}, {}
    product = pd.DataFrame([{"store_id": "store-a", "sku": "001", "nome": "Synthetic",
                             "row_version": "1", "updated_at_utc": "2026-10-16T00:00:00Z",
                             "deleted_at_utc": ""}]).to_csv(index=False).encode("utf-8-sig")
    store = json.dumps([{"store_id": "store-a", "nome": "Synthetic store", "integracoes": {}}]).encode()

    def publish(scope, files):
        data, manifest = make_bundle(scope, files)
        key = "remote-" + scope
        bundles[key] = data
        metas[key] = {"id": key, "snapshot_id": key + "-snapshot", "schema": 2,
                      "scope": scope, "snapshot_hash": hashlib.sha256(data).hexdigest(),
                      "bundle_sha256": hashlib.sha256(data).hexdigest(), "files": manifest["files"]}

    photo_config = json.dumps({
        "schema": "jk.cadastro.fotos.v1", "strict_store_scope": True, "shared_groups": [],
    }).encode()
    product_files = [(REL, product), ("cadastro_fotos_config.json", photo_config)]
    publish("cadastro", product_files)
    publish("lojas_integracoes", [("lojas_config.json", store)])
    resolver = lambda client: str(tmp_path / client)
    for module in (collect, operations, apply_scope, cadastro_fotos):
        monkeypatch.setattr(module, "get_tenant_path", resolver, raising=False)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tmp_path), raising=False)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_carregar_lojas_foto", lambda client: json.loads((tmp_path / client / "lojas_config.json").read_text(encoding="utf-8-sig")))
    monkeypatch.setattr(integracoes, "_get_tenant_path", resolver)
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(integracoes, "ARQUIVO_LOJAS", str(tmp_path / "lojas_config.json"))
    monkeypatch.setattr(integracoes, "ARQUIVO_TEMP_AUTH", str(tmp_path / "temp_integracao.json"))
    monkeypatch.setattr(central_accounts_client, "current", lambda _: None)
    monkeypatch.setattr(endpoints, "_shared_sync_session", lambda *a: session)
    monkeypatch.setattr(endpoints, "_shared_sync_get_link", lambda _: link)
    monkeypatch.setattr(endpoints, "_carregar_permissoes_usuario", lambda user, client: permissions.get(user, {}) , raising=False)
    monkeypatch.setattr(endpoints, "_shared_sync_link_public", lambda item, sessao: {"id": item["id"]})
    monkeypatch.setattr(operations, "_OPERATIONS", {})
    for module in (operations, endpoints, pairs):
        monkeypatch.setattr(module, "_shared_sync_remote_meta_by_id", lambda key: metas.get(key))
    for module in (operations, pairs):
        monkeypatch.setattr(module, "_shared_sync_obter_bundle_remoto_para_guard", lambda key, **kwargs: (bundles[key], metas[key]))
    monkeypatch.setattr(pairs, "_shared_sync_obter_bundle_por_id", lambda key, meta=None: (bundles[key], metas[key]))
    monkeypatch.setattr(pairs, "_shared_sync_state_snapshot_id", lambda *a: "")
    monkeypatch.setattr(pairs, "_shared_sync_state_snapshot_hash", lambda *a: "")
    monkeypatch.setattr(pairs, "_shared_sync_state_update", lambda *a: None)
    monkeypatch.setattr(pairs, "_shared_sync_user_share_add_known_keys", lambda *a: None)
    from backend.modules.context_hub import catalog_product_sync
    monkeypatch.setattr(catalog_product_sync, "notify_catalog_committed", lambda *a, **k: None)
    calls, audit = [], []
    real_pull = pairs._shared_sync_pull_pair_scope

    def pull(sessao, item, scope, **kwargs):
        calls.append((scope, dict(kwargs)))
        return real_pull(sessao, item, scope, **kwargs)

    monkeypatch.setattr(endpoints, "_shared_sync_pull_pair_scope", pull)
    monkeypatch.setattr(endpoints, "_shared_sync_audit", lambda *a, **kwargs: audit.append(kwargs))

    def preview(scopes=None):
        result = endpoints.shared_sync_user_shares_link_preview(
            link["id"], SharedSyncPreviewRequest(direction="pull", scopes=scopes or SCOPES),
            authorization="Bearer synthetic", client_id="tenant-a",
        )
        return result["operation_id"]

    def execute(operation_id, scopes=None):
        return endpoints.shared_sync_user_shares_link_pull(
            link["id"], SharedSyncUserLinkRunRequest(scopes=scopes or SCOPES, operation_id=operation_id),
            authorization="Bearer synthetic", client_id="tenant-a",
        )

    return SimpleNamespace(tenant=tenant, session=session, permissions=permissions, link=link,
                           bundles=bundles, metas=metas, publish=publish, calls=calls,
                           audit=audit, preview=preview, execute=execute, product_files=product_files)


def test_new_machine_applies_stores_before_cadastro_without_changing_preview(new_machine):
    env = new_machine
    operation_id = env.preview()
    approved = dict(operations._OPERATIONS[operation_id])
    assert approved["scopes"] == SCOPES
    result = env.execute(operation_id)
    assert result["success"] is True
    assert [scope for scope, _ in env.calls] == ["lojas_integracoes", "cadastro"]
    assert [item["scope"] for item in result["results"]] == ["lojas_integracoes", "cadastro"]
    for scope, kwargs in env.calls:
        assert kwargs["force"] is True
        assert kwargs["expected_snapshot_id"] == approved["remote_snapshot_ids"][scope]
        assert kwargs["expected_remote_fingerprint"] == approved["remote_hashes"][scope]
        assert kwargs["expected_bundle_hash"] == approved["remote_bundle_hashes"].get(scope, "")
    saved = operations._OPERATIONS[operation_id]
    assert saved == {**approved, "used": True}
    assert json.loads((env.tenant / "lojas_config.json").read_text())[0]["store_id"] == "store-a"
    frame = pd.read_csv(env.tenant / REL, dtype=str)
    assert frame[["store_id", "sku"]].to_dict("records") == [{"store_id": "store-a", "sku": "001"}]
    assert len(env.audit) == 1


def test_cadastro_alone_does_not_invent_or_import_unselected_store(new_machine):
    env = new_machine
    operation_id = env.preview(["cadastro"])
    with pytest.raises(HTTPException) as caught:
        env.execute(operation_id, ["cadastro"])
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "shared_sync_store_identity_invalid"
    assert [scope for scope, _ in env.calls] == ["cadastro"]
    assert json.loads((env.tenant / "lojas_config.json").read_text()) == []
    assert not (env.tenant / REL).exists()
    assert env.audit == []


@pytest.mark.parametrize("user", ["alice", "bob"])
def test_both_participants_still_require_store_permission(new_machine, user):
    env = new_machine
    operation_id = env.preview()
    env.permissions[user]["integracao"] = False
    with pytest.raises(HTTPException) as caught:
        env.execute(operation_id)
    assert caught.value.status_code == 403
    assert "Origem e destino" in caught.value.detail
    assert env.calls == []
    assert operations._OPERATIONS[operation_id]["used"] is False


def test_foreign_participant_cannot_use_approved_operation(new_machine):
    env = new_machine
    operation_id = env.preview()
    env.session["username"] = "mallory"
    with pytest.raises(HTTPException) as caught:
        env.execute(operation_id)
    assert caught.value.status_code == 403
    assert env.calls == []
    assert operations._OPERATIONS[operation_id]["used"] is False


def test_changed_remote_snapshot_still_rejects_before_any_apply(new_machine):
    env = new_machine
    operation_id = env.preview()
    env.metas["remote-lojas_integracoes"]["snapshot_id"] = "changed-after-preview"
    with pytest.raises(HTTPException) as caught:
        env.execute(operation_id)
    assert caught.value.status_code == 409
    assert "mudaram depois da previa" in caught.value.detail
    assert env.calls == []
    assert not (env.tenant / REL).exists()



def test_invalid_store_package_aborts_before_cadastro(new_machine):
    env = new_machine
    env.publish("lojas_integracoes", [("lojas_config.json", b"{}")])
    operation_id = env.preview()
    with pytest.raises(HTTPException) as caught:
        env.execute(operation_id)
    assert caught.value.status_code == 502
    assert caught.value.detail == "lojas_config.json remoto nao contem uma lista."
    assert [scope for scope, _ in env.calls] == ["lojas_integracoes"]
    assert json.loads((env.tenant / "lojas_config.json").read_text()) == []
    assert not (env.tenant / REL).exists()
    assert env.audit == []


def test_tombstoned_store_is_not_recreated_by_selected_store_package(new_machine):
    env = new_machine
    tombstones = [{"type": "store", "key": "store:store-a:", "store_id": "store-a",
                   "version": 2, "deleted_at": "2026-10-16T01:00:00Z"}]
    tombstone_file = env.tenant / "lojas_sync_tombstones.json"
    tombstone_file.write_text(json.dumps(tombstones), encoding="utf-8")
    operation_id = env.preview()
    with pytest.raises(HTTPException) as caught:
        env.execute(operation_id)
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "shared_sync_store_identity_invalid"
    assert caught.value.detail["store_ids_tombstonados"] == ["store-a"]
    assert [scope for scope, _ in env.calls] == ["lojas_integracoes", "cadastro"]
    assert json.loads((env.tenant / "lojas_config.json").read_text()) == []
    assert json.loads(tombstone_file.read_text()) == tombstones
    assert not (env.tenant / REL).exists()


def test_store_existing_only_in_another_tenant_is_not_accepted(new_machine):
    env = new_machine
    other = env.tenant.parent / "tenant-b"
    other.mkdir()
    other_config = other / "lojas_config.json"
    other_config.write_text(json.dumps([{"store_id": "store-foreign", "nome": "Other"}]))
    before = other_config.read_bytes()
    product = env.product_files[0][1].replace(b"store-a", b"store-foreign")
    env.publish("cadastro", [(REL, product), env.product_files[1]])
    operation_id = env.preview()
    with pytest.raises(HTTPException) as caught:
        env.execute(operation_id)
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "shared_sync_store_identity_invalid"
    assert caught.value.detail["store_ids_ausentes"] == ["store-foreign"]
    assert [scope for scope, _ in env.calls] == ["lojas_integracoes", "cadastro"]
    assert [store["store_id"] for store in json.loads((env.tenant / "lojas_config.json").read_text())] == ["store-a"]
    assert other_config.read_bytes() == before
    assert not (env.tenant / REL).exists()
