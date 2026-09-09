import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import shared_sync  # configure compatibility facade
from backend.services import shared_sync_apply_scope as apply
from backend.services import shared_sync_bundle as bundle_module
from backend.services import central_accounts_client as client
from backend.services import central_accounts_store_index as index
from backend.services import cadastro_fotos, integracoes

A, B = "a" * 24, "b" * 24


def stores(*ids):
    return [{"store_id": sid, "nome": "Same name", "owner_client_id": "000002", "access": "owner",
             "integracoes": {"mercadolivre": {"connected": True, "central": True, "user_id": "123", "site_id": "MLB"}}} for sid in ids]


def bundle(files, scope="cadastro"):
    manifest = {"schema": 2, "scope": scope, "file_count": len(files), "files": [
        {"relative_path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in files]}
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in files:
            archive.writestr("files/" + name, data)
    return result.getvalue()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    monkeypatch.setattr(apply, "get_tenant_path", lambda _: str(tenant), raising=False)
    monkeypatch.setattr(bundle_module, "get_tenant_path", lambda _: str(tenant), raising=False)
    monkeypatch.setattr(integracoes, "_get_tenant_path", lambda _: str(tenant))
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tmp_path), raising=False)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", lambda _: str(tenant), raising=False)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_carregar_lojas_foto", lambda _: json.loads((tenant / "lojas_config.json").read_text()))
    authorized = stores(A)
    central = SimpleNamespace(tenant="000002", public_stores=lambda: authorized,
                              refresh_stores=lambda: index.materialize_store_index("000002", authorized))
    token = client._current.set(central)
    yield tenant, authorized
    client._current.reset(token)


def test_first_manual_import_materializes_central_identity_without_credentials(setup):
    tenant, _ = setup
    data = f"store_id,sku,custo,updated_at\n{A},001,12,2026-09-01T10:00:00Z\n".encode()
    result = apply._shared_sync_aplicar_pacote("000002", "cadastro", bundle([("cadastro_custos_lojas.csv", data)]))
    assert result["file_count"] == 1
    assert "001" in (tenant / "cadastro_custos_lojas.csv").read_text(encoding="utf-8-sig")
    config = json.loads((tenant / "lojas_config.json").read_text())
    assert config[0]["store_id"] == A
    assert "access_token" not in json.dumps(config)
    assert "refresh_token" not in json.dumps(config)


def test_delta_cannot_fan_out_to_revoked_store_in_local_photo_group(setup):
    tenant, _ = setup
    index.materialize_store_index("000002", stores(A, B))
    (tenant / "cadastro_fotos_config.json").write_text(json.dumps({"schema": "jk.cadastro.fotos.v1", "strict_store_scope": True,
        "shared_groups": [{"group_id": "group", "store_ids": [A, B]}]}))
    data = f"store_id,sku,custo,updated_at\n{A},001,12,2026-09-01T10:00:00Z\n".encode()
    with pytest.raises(HTTPException) as caught:
        apply._shared_sync_aplicar_pacote("000002", "cadastro", bundle([("cadastro_custos_lojas.csv", data)]))
    assert caught.value.status_code == 403
    assert not (tenant / "cadastro_custos_lojas.csv").exists()


def test_export_rechecks_revoked_store_and_freezes_authorized_payload(setup, monkeypatch):
    tenant, authorized = setup
    index.materialize_store_index("000002", stores(A, B))
    data = f"store_id,sku,custo\n{B},001,20\n".encode()
    path = tenant / "cadastro_custos_lojas.csv"
    path.write_bytes(data)
    entries = [{"relative_path": path.name, "abs_path": str(path), "size": len(data), "mtime": 1,
                "sha256": hashlib.sha256(data).hexdigest(), "item_keys": []}]
    monkeypatch.setattr(bundle_module, "_shared_sync_coletar_arquivos", lambda *a, **k: (entries, []))
    with pytest.raises(HTTPException) as caught:
        bundle_module._shared_sync_montar_pacote_locked("000002", "cadastro", "user", user_only=True)
    assert caught.value.status_code == 403
    authorized.extend(stores(B))
    payload, manifest, _ = bundle_module._shared_sync_montar_pacote_locked("000002", "cadastro", "user", user_only=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.read("files/" + path.name) == data
    assert manifest["files"][0]["sha256"] == hashlib.sha256(data).hexdigest()


def test_migrated_stores_reject_legacy_import_and_export_even_after_logout(setup):
    tenant, _ = setup
    index.materialize_store_index("000002", stores(A))
    original = (tenant / "lojas_config.json").read_bytes()
    client._current.set(None)
    old = json.dumps([{"store_id": A, "nome": "Same name", "integracoes": {"mercadolivre": {"access_token": "stale", "refresh_token": "stale"}}}]).encode()
    with pytest.raises(HTTPException):
        apply._shared_sync_aplicar_pacote("000002", "lojas_integracoes", bundle([("lojas_config.json", old)], "lojas_integracoes"))
    with pytest.raises(HTTPException):
        bundle_module._shared_sync_montar_pacote_locked("000002", "lojas_integracoes", "user", user_only=True)
    assert (tenant / "lojas_config.json").read_bytes() == original


def test_central_machine_selection_does_not_block_cadastro_with_legacy_store_scope(setup):
    from backend.services import shared_sync_config as config
    session = {"client_id": "000002", "username": "user", "is_admin": True}
    normalized = config._shared_sync_machine_config_normalizar(session, {
        "enabled": True, "scopes": ["lojas_integracoes", "cadastro"], "auto_pull": True,
    })
    assert normalized["scopes"] == ["cadastro"]
    assert normalized["auto_pull"] is False
