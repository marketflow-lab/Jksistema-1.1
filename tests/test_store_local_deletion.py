"""Explicit local deletion detaches configuration while preserving domain data."""
import asyncio
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest
from fastapi import HTTPException

from test_integracoes_store_lifecycle import _configure
from backend.services import cadastro_fotos, central_accounts_client, integracoes, integracoes_api
from backend.services import store_snapshot_transactions as transactions


CLIENT = "cliente-a"
REMOVED = "StoreA"
HOMONYM = "storea"
CONFIG_FILES = (
    "lojas_config.json", "lojas_config.json.bak", "lojas_sync_tombstones.json",
    "cadastro_fotos_config.json", "lojas_public_snapshot.json",
)


@pytest.fixture
def local_tenant(tmp_path, monkeypatch):
    tenant = _configure(tmp_path)
    monkeypatch.setattr(central_accounts_client, "current", lambda _client: None)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tenant.parent), raising=False)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", lambda client: str(tenant.parent / client), raising=False)
    rows = [
        {"store_id": REMOVED, "nome": "Loja Igual", "nomes_anteriores": ["Nome Antigo", "Nome Anterior"],
         "integracoes": {"mercadoturbo": {"token": "synthetic-original", "connected": True}}},
        {"store_id": HOMONYM, "nome": "Loja Igual",
         "integracoes": {"mercadoturbo": {"token": "synthetic-homonym", "connected": True}}},
        {"store_id": "store-c", "nome": "Terceira", "integracoes": {}},
        {"store_id": "store-d", "nome": "Quarta", "integracoes": {}},
        {"store_id": "store-e", "nome": "Quinta", "integracoes": {}},
    ]
    integracoes.salvar_lojas(CLIENT, rows)
    return tenant


def _configuration_state(tenant):
    return {name: (tenant / name).read_bytes() if (tenant / name).exists() else None for name in CONFIG_FILES}


def _hashes(tenant, names=None):
    paths = [tenant / name for name in names] if names is not None else [path for path in tenant.rglob("*") if path.is_file()]
    return {path.relative_to(tenant).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _operational_files(tenant):
    files = {
        "cadastro_produtos_lojas.csv": (
            "store_id,sku,sku_normalizado,deleted_at_utc\n"
            "StoreA,001,001,\nStoreA,002,002,2026-10-01T00:00:00Z\nstorea,003,003,\n"
        ).encode(),
        "cadastro_custos_lojas.csv": b"store_id,sku,custo\nStoreA,001,42\nstorea,003,9\n",
        "cadastro_produtos.csv": b"sku,loja_sync,nome\n004,Loja Igual | Nome Antigo,Legado\n",
        "produtos_compilado.csv": b"store_id,loja_sync,sku,nome\nStoreA,Nome Anterior,005,Compilado\n",
        "medias/listas.json": json.dumps({"store_id": REMOVED, "store_ref": HOMONYM, "items": ["001"]}).encode(),
        "context_hub/local.json": json.dumps({"store_id": REMOVED, "status": "pending"}).encode(),
        "shared_sync_store_bindings.json": b'{"schema":1,"peers":{}}',
        f"cadastro_fotos/lojas/sid-{hashlib.sha256(REMOVED.encode()).hexdigest()}/001.jpg": b"synthetic-scoped-image",
        "cadastro_fotos/legacy.jpg": b"synthetic-legacy-image",
    }
    for name, content in files.items():
        path = tenant / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for name in ("vendas_historico.db", "estoque_historico.db"):
        with sqlite3.connect(tenant / name) as connection:
            connection.execute("CREATE TABLE records (store_id TEXT, sku TEXT, loja_conta TEXT)")
            connection.execute("INSERT INTO records VALUES (?, ?, ?)", (REMOVED, "001", "Nome Antigo"))
        files[name] = b""
    return list(files)


def _photo_groups(tenant, members, *, strict=True):
    payload = {
        "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
        "strict_store_scope": strict,
        "shared_groups": [
            {"group_id": "selected", "store_ids": members},
            {"group_id": "unrelated", "store_ids": ["store-d", "store-e"]},
        ],
    }
    (tenant / "cadastro_fotos_config.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_exact_local_deletion_preserves_all_domain_bytes_and_other_tenant(local_tenant, monkeypatch):
    tenant = local_tenant
    operational = _operational_files(tenant)
    original_rows = json.loads((tenant / "lojas_config.json").read_bytes())
    original_homonym = next(row for row in original_rows if row["store_id"] == HOMONYM)
    other = tenant.parent / "cliente-b"
    integracoes.salvar_lojas("cliente-b", [{"store_id": REMOVED, "nome": "Loja Igual", "integracoes": {}}])
    (other / "cadastro_produtos_lojas.csv").write_bytes(b"store_id,sku\nStoreA,999\n")
    before_other = _hashes(other)
    before_domain = _hashes(tenant, operational)
    save_real = integracoes.salvar_lojas
    saves = []

    def record_save(client, rows, **kwargs):
        saves.append((client, kwargs.get("_exclusao_local_store_id")))
        return save_real(client, rows, **kwargs)

    monkeypatch.setattr(integracoes, "salvar_lojas", record_save)
    removed = integracoes.excluir_loja(CLIENT, "nome antigo da tela", store_id=REMOVED)

    assert removed["store_id"] == REMOVED
    assert saves == [(CLIENT, REMOVED)]
    assert _hashes(tenant, operational) == before_domain
    assert _hashes(other) == before_other
    current = json.loads((tenant / "lojas_config.json").read_bytes())
    assert {row["store_id"] for row in current} == {HOMONYM, "store-c", "store-d", "store-e"}
    assert next(row for row in current if row["store_id"] == HOMONYM) == original_homonym
    assert {row["store_id"] for row in json.loads((tenant / "lojas_config.json.bak").read_bytes())} == {row["store_id"] for row in current}
    tombstone = next(row for row in json.loads((tenant / "lojas_sync_tombstones.json").read_bytes()) if row["store_id"] == REMOVED)
    assert tombstone["type"] == "store" and tombstone["deleted_at"]
    assert tombstone["version"] > removed["_sync_version"]
    assert set(tombstone["store_names"]) == {"Loja Igual", "Nome Antigo", "Nome Anterior"}
    assert len(tombstone["store_names"]) == 3
    assert not transactions.publication_pending(tenant)
    assert REMOVED not in {row["store_id"] for row in integracoes.carregar_lojas(CLIENT)}
    assert _hashes(tenant, operational) == before_domain


@pytest.mark.parametrize("members", [[REMOVED, HOMONYM], [REMOVED, HOMONYM, "store-c"]])
@pytest.mark.parametrize("strict", [False, True])
def test_photo_membership_is_detached_without_touching_photo_or_catalog_files(local_tenant, members, strict):
    tenant = local_tenant
    operational = _operational_files(tenant)
    previous = _photo_groups(tenant, members, strict=strict)
    before = _hashes(tenant, operational)

    integracoes.excluir_loja(CLIENT, "Loja Igual", store_id=REMOVED)

    actual = json.loads((tenant / "cadastro_fotos_config.json").read_bytes())
    expected_groups = [previous["shared_groups"][1]]
    if len(members) > 2:
        expected_groups.insert(0, {"group_id": "selected", "store_ids": [HOMONYM, "store-c"]})
    assert actual == {**previous, "shared_groups": expected_groups}
    assert _hashes(tenant, operational) == before
    assert not transactions.publication_pending(tenant)


@pytest.mark.parametrize("failure_stage", ["after_save", "after_tombstone"])
@pytest.mark.parametrize("existing_tombstone", [False, True])
def test_failed_deletion_restores_config_backup_photos_tombstones_and_cards(local_tenant, monkeypatch, failure_stage, existing_tombstone):
    tenant = local_tenant
    operational = _operational_files(tenant)
    _photo_groups(tenant, [REMOVED, HOMONYM])
    if existing_tombstone:
        integracoes.registrar_tombstone_integracao(
            CLIENT, loja={"store_id": "already-removed", "nome": "Earlier store", "integracoes": {}}, tipo="store",
        )
    before = _configuration_state(tenant)
    before_domain = _hashes(tenant, operational)
    invoked = []
    if failure_stage == "after_save":
        original = integracoes.salvar_lojas

        def failing_stage(*args, **kwargs):
            original(*args, **kwargs)
            invoked.append(True)
            assert REMOVED not in {row["store_id"] for row in json.loads((tenant / "lojas_config.json").read_bytes())}
            raise HTTPException(500, "synthetic failure after store save")

        monkeypatch.setattr(integracoes, "salvar_lojas", failing_stage)
    else:
        original = integracoes.registrar_tombstone_integracao

        def failing_stage(*args, **kwargs):
            original(*args, **kwargs)
            invoked.append(True)
            # A process crash at this point must also restore the photo config.
            journal = json.loads((tenant / "_stores_publication" / "transaction.json").read_bytes())
            assert {"lojas_config.json", "lojas_config.json.bak", "lojas_sync_tombstones.json", "cadastro_fotos_config.json"} <= set(journal["files"])
            assert (tenant / "cadastro_fotos_config.json").read_bytes() != before["cadastro_fotos_config.json"]
            assert (tenant / "lojas_public_snapshot.json").read_bytes() == before["lojas_public_snapshot.json"]
            raise HTTPException(500, "synthetic failure after tombstone")

        monkeypatch.setattr(integracoes, "registrar_tombstone_integracao", failing_stage)
    with pytest.raises(HTTPException) as caught:
        integracoes.excluir_loja(CLIENT, "Loja Igual", store_id=REMOVED)
    assert caught.value.status_code == 500
    assert invoked == [True]
    assert _configuration_state(tenant) == before
    assert _hashes(tenant, operational) == before_domain
    assert not transactions.publication_pending(tenant)


@pytest.mark.parametrize("case", ["wrong_id", "multiple_removals", "addition", "no_removal"])
def test_internal_deletion_bypass_cannot_authorize_other_snapshot_changes(local_tenant, case):
    tenant = local_tenant
    operational = _operational_files(tenant)
    rows = json.loads((tenant / "lojas_config.json").read_bytes())
    selected = [row for row in rows if row["store_id"] != REMOVED]
    bypass = REMOVED
    if case == "wrong_id":
        bypass = HOMONYM
    elif case == "multiple_removals":
        selected = [row for row in selected if row["store_id"] != HOMONYM]
    elif case == "addition":
        selected.append({"store_id": "new-store", "nome": "New", "integracoes": {}})
    else:
        selected = rows
    before = _configuration_state(tenant)
    before_domain = _hashes(tenant, operational)

    with pytest.raises(HTTPException) as caught:
        integracoes.salvar_lojas(
            CLIENT, selected, permitir_reducao_confirmada=True,
            _exclusao_local_store_id=bypass,
        )

    assert caught.value.status_code == 409
    assert _configuration_state(tenant) == before
    assert _hashes(tenant, operational) == before_domain


def test_generic_save_keeps_guard_for_legacy_data_without_explicit_deletion(local_tenant):
    tenant = local_tenant
    (tenant / "cadastro_produtos.csv").write_bytes(b"sku,loja_sync\n001,Nome Antigo\n")
    rows = json.loads((tenant / "lojas_config.json").read_bytes())
    before = _configuration_state(tenant)
    legacy = (tenant / "cadastro_produtos.csv").read_bytes()
    with pytest.raises(HTTPException) as caught:
        integracoes.salvar_lojas(CLIENT, [row for row in rows if row["store_id"] != REMOVED], permitir_reducao_confirmada=True)
    assert caught.value.detail["code"] == "store_has_legacy_catalog_records"
    assert _configuration_state(tenant) == before
    assert (tenant / "cadastro_produtos.csv").read_bytes() == legacy


def test_old_snapshot_and_async_update_cannot_restore_detached_store_with_data(local_tenant):
    tenant = local_tenant
    operational = _operational_files(tenant)
    stale = deepcopy(integracoes.carregar_lojas(CLIENT))
    integracoes.excluir_loja(CLIENT, "Loja Igual", store_id=REMOVED)
    before = _configuration_state(tenant)
    before_domain = _hashes(tenant, operational)

    with pytest.raises(HTTPException) as caught:
        integracoes.salvar_lojas(CLIENT, stale)
    assert caught.value.detail["code"] == "store_tombstoned"
    with pytest.raises(HTTPException) as caught:
        integracoes.atualizar_api_loja(CLIENT, "Loja Igual", "mercadolivre", {"access_token": "synthetic-stale"}, store_id=REMOVED)
    assert caught.value.detail["code"] == "store_config_changed"
    assert _configuration_state(tenant) == before
    assert _hashes(tenant, operational) == before_domain


def test_invalid_photo_config_still_fails_before_local_deletion(local_tenant):
    tenant = local_tenant
    (tenant / "cadastro_fotos_config.json").write_bytes(b"{invalid")
    before = _configuration_state(tenant)
    with pytest.raises(HTTPException) as caught:
        integracoes.excluir_loja(CLIENT, "Loja Igual", store_id=REMOVED)
    assert caught.value.status_code == 409
    assert _configuration_state(tenant) == before


@pytest.mark.parametrize("denied", [False, True])
def test_central_deletion_still_delegates_without_local_data_mutation(local_tenant, monkeypatch, denied):
    tenant = local_tenant
    operational = _operational_files(tenant)
    before = _configuration_state(tenant)
    before_domain = _hashes(tenant, operational)
    calls = []
    response = {"success": True, "store_id": REMOVED}

    class Central:
        def call(self, method, path):
            calls.append((method, path))
            if denied:
                raise HTTPException(403, "synthetic central permission denied")
            return response

        def refresh_stores(self):
            calls.append(("refresh",))
            return []

    monkeypatch.setattr(central_accounts_client, "current", lambda client: Central() if client == CLIENT else None)
    monkeypatch.setattr(integracoes_api, "excluir_loja", lambda *args, **kwargs: pytest.fail("Central deletion must stay delegated"))
    request = integracoes_api.delete_loja("Loja Igual", store_id=REMOVED, client_id=CLIENT)
    if denied:
        with pytest.raises(HTTPException) as caught:
            asyncio.run(request)
        assert caught.value.status_code == 403
        assert calls == [("DELETE", "/stores/" + REMOVED)]
    else:
        assert asyncio.run(request) == response
        assert calls == [("DELETE", "/stores/" + REMOVED), ("refresh",)]
    assert _configuration_state(tenant) == before
    assert _hashes(tenant, operational) == before_domain
