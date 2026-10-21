"""Deleted remote copies can bind to a live, account-proven survivor."""
from copy import deepcopy
import json

import pandas as pd
import pytest
from fastapi import HTTPException

from test_shared_sync_store_reconciliation import existing_account, new_machine, store  # noqa: F401
from backend.services import central_accounts_migration, integracoes
from backend.services import shared_sync_apply_scope as apply_scope
from backend.services import shared_sync_operations as operations
from backend.services import shared_sync_store_bindings as bindings

REL = "cadastro_produtos_lojas.csv"


def _set_stores(env, rows):
    payload = json.dumps(rows).encode()
    for name in ("lojas_config.json", "lojas_config.json.bak"):
        (env.tenant / name).write_bytes(payload)


def _delete(env, row):
    deleted = integracoes.excluir_loja("tenant-a", row["nome"], store_id=row["store_id"])
    assert deleted["store_id"] == row["store_id"]
    assert row["store_id"] not in {
        item["store_id"] for item in json.loads((env.tenant / "lojas_config.json").read_bytes())
    }
    return (env.tenant / "lojas_sync_tombstones.json").read_bytes()


def _delete_remote_copy(env, local, remote):
    _set_stores(env, [local, remote])
    return _delete(env, remote)


def _removed(env, *ids):
    payload = json.dumps([
        {"type": "store", "key": f"store:{key}:", "store_id": key,
         "version": 2, "deleted_at": "2026-10-21T10:00:00Z"}
        for key in ids
    ]).encode()
    (env.tenant / "lojas_sync_tombstones.json").write_bytes(payload)
    return payload


def _snapshot(env):
    return {name: (env.tenant / name).read_bytes() if (env.tenant / name).exists() else None
            for name in ("lojas_config.json", "lojas_config.json.bak", "lojas_sync_tombstones.json",
                         bindings.BINDINGS_FILE, REL, "cadastro_fotos_config.json")}


def _publish(env, incoming):
    source = {incoming["store_id"]: bindings._connection_hash(incoming)}
    env.publish("lojas_integracoes", [("lojas_config.json", json.dumps([incoming]).encode())], source_connections=source)
    files = [(name, data.replace(b"store-a", incoming["store_id"].encode()) if name == REL else data)
             for name, data in env.product_files]
    env.publish("cadastro", files, source_connections=source)
    return source


def _receive(env, scopes=None):
    original_bundles = dict(env.bundles)
    operation_id = env.preview(scopes)
    approved = deepcopy(operations._OPERATIONS[operation_id])
    result = env.execute(operation_id, scopes)
    assert result["success"]
    assert env.bundles == original_bundles
    for scope, kwargs in env.calls[-len(scopes or ["cadastro", "lojas_integracoes"]):]:
        assert kwargs["expected_remote_fingerprint"] == approved["remote_hashes"][scope]
        assert kwargs["expected_snapshot_id"] == approved["remote_snapshot_ids"][scope]
        assert kwargs["expected_bundle_hash"] == approved["remote_bundle_hashes"].get(scope, "")
    assert operations._OPERATIONS[operation_id] == {**approved, "used": True}
    return result


def _assert_store(env, original):
    saved = json.loads((env.tenant / "lojas_config.json").read_bytes())
    assert len(saved) == 1 and saved[0]["store_id"] == original["store_id"]
    for provider, expected in original["integracoes"].items():
        assert {key: saved[0]["integracoes"][provider][key] for key in expected} == expected
    frame = pd.read_csv(env.tenant / REL, dtype=str)
    assert frame["store_id"].tolist() == [original["store_id"]]
    assert frame["sku"].tolist() == ["001"]


def _ledger(env):
    return json.loads((env.tenant / bindings.BINDINGS_FILE).read_bytes())


def test_deleted_remote_copy_maps_to_live_same_account_and_repeats(existing_account):
    env, local, remote, calls = existing_account
    tombstones = _delete_remote_copy(env, local, remote)
    remote_source = dict(env.bundles)
    for _ in range(2):
        calls.clear()
        _receive(env)
        _assert_store(env, local)
        assert calls  # Every explicit Lojas reception verifies the live account.
        assert (env.tenant / "lojas_sync_tombstones.json").read_bytes() == tombstones
        assert env.bundles == remote_source
    record = next(iter(_ledger(env)["peers"].values()))["stores"][remote["store_id"]]
    assert record["local_id"] == local["store_id"]
    assert record["remote_connection_hash"] == bindings._connection_hash(remote)


@pytest.mark.parametrize("source_client", ["tenant-a", "another-source-tenant"])
def test_receive_b_then_c_with_different_remote_ids_keeps_one_local_identity(existing_account, source_client):
    env, local, remote, _ = existing_account
    tombstones = _delete_remote_copy(env, local, remote)
    _receive(env)
    incoming = store("remote-c", "remote-c")
    env.link.update(id="link-user-c", source_username="charlie", source_client_id=source_client)
    env.permissions["charlie"] = dict(env.permissions["alice"])
    _publish(env, incoming)
    _receive(env)
    _receive(env)
    _assert_store(env, local)
    peers = list(_ledger(env)["peers"].values())
    assert len(peers) == 2
    assert {entry["identity"]["source_username"] for entry in peers} == {"alice", "charlie"}
    assert {key: row["local_id"] for entry in peers for key, row in entry["stores"].items()} == {
        remote["store_id"]: local["store_id"], incoming["store_id"]: local["store_id"],
    }
    assert (env.tenant / "lojas_sync_tombstones.json").read_bytes() == tombstones


@pytest.mark.parametrize("survivor_id", ["live-survivor", "store-a"])
def test_deleted_binding_target_rebuilds_only_after_new_store_account_proof(existing_account, survivor_id):
    env, local, remote, calls = existing_account
    _receive(env, ["lojas_integracoes"])
    survivor = store(survivor_id, "survivor")
    _set_stores(env, [local, survivor])
    tombstones = _delete(env, local)
    before = _snapshot(env)
    with pytest.raises(HTTPException):
        env.execute(env.preview(["cadastro"]), ["cadastro"])
    assert _snapshot(env) == before
    calls.clear()
    _receive(env)
    _assert_store(env, survivor)
    assert calls
    assert (env.tenant / "lojas_sync_tombstones.json").read_bytes() == tombstones
    entry = next(iter(_ledger(env)["peers"].values()))
    if survivor_id == remote["store_id"]:
        assert remote["store_id"] not in entry["stores"]
    else:
        assert entry["stores"][remote["store_id"]]["local_id"] == survivor_id
    _receive(env, ["cadastro"])
    _assert_store(env, survivor)


def test_deleted_target_without_live_survivor_is_not_recreated(existing_account):
    env, local, _, _ = existing_account
    _receive(env, ["lojas_integracoes"])
    _delete(env, local)
    before = _snapshot(env)
    with pytest.raises(HTTPException) as error:
        env.execute(env.preview(), None)
    assert error.value.status_code == 409
    assert _snapshot(env) == before


def test_tombstoned_local_target_is_never_used_even_if_still_in_store_file(existing_account):
    env, local, _, calls = existing_account
    _removed(env, local["store_id"])
    before = _snapshot(env)
    with pytest.raises(HTTPException) as error:
        env.execute(env.preview(), None)
    assert error.value.status_code == 409 and calls == []
    assert _snapshot(env) == before


def test_rebuilding_target_requires_fresh_successful_account_lookup(existing_account, monkeypatch):
    env, local, _, calls = existing_account
    _receive(env, ["lojas_integracoes"])
    _set_stores(env, [local, store("survivor", "survivor")])
    _delete(env, local)
    before = _snapshot(env)
    monkeypatch.setattr(central_accounts_migration, "_identity", lambda *args: (_ for _ in ()).throw(HTTPException(503, "unavailable")))
    with pytest.raises(HTTPException) as error:
        env.execute(env.preview(), None)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "shared_sync_store_account_unverifiable"
    assert _snapshot(env) == before


def test_other_user_same_name_different_account_remains_distinct(existing_account, monkeypatch):
    env, local, remote, _ = existing_account
    _delete_remote_copy(env, local, remote)
    _receive(env)
    incoming = store("foreign-account-store", "foreign", seller="456")
    env.link.update(id="link-different-account", source_username="charlie")
    env.permissions["charlie"] = dict(env.permissions["alice"])
    _publish(env, incoming)

    def identity(provider, token):
        account = "456" if token.startswith("foreign") else "123"
        return (account, "MLB") if provider == "mercadolivre" else ("company-" + account, "")

    monkeypatch.setattr(central_accounts_migration, "_identity", identity)
    _receive(env)
    saved = {row["store_id"]: row for row in json.loads((env.tenant / "lojas_config.json").read_bytes())}
    assert set(saved) == {local["store_id"], incoming["store_id"]}
    assert saved[local["store_id"]]["integracoes"]["mercadolivre"]["access_token"] == local["integracoes"]["mercadolivre"]["access_token"]
    assert saved[incoming["store_id"]]["integracoes"]["mercadolivre"]["user_id"] == "456"
    assert set(pd.read_csv(env.tenant / REL, dtype=str)["store_id"]) == set(saved)


@pytest.mark.parametrize("invalid", ["peer", "source_proof", "local_target"])
def test_deleted_remote_alias_keeps_peer_proof_and_live_target_guards(existing_account, invalid):
    env, local, remote, _ = existing_account
    _delete_remote_copy(env, local, remote)
    _receive(env)
    entry = next(iter(_ledger(env)["peers"].values()))
    peer = deepcopy(entry["identity"])
    if invalid == "peer":
        peer["link_id"] = "unrelated-link"
    elif invalid == "source_proof":
        env.publish("cadastro", env.product_files, source_connections={remote["store_id"]: "b" * 64})
    else:
        _removed(env, local["store_id"], remote["store_id"])
    before = _snapshot(env)
    with pytest.raises(HTTPException) as error:
        apply_scope._shared_sync_aplicar_pacote(
            "tenant-a", "cadastro", env.bundles["remote-cadastro"], "bob",
            {"store_peer": peer, "user_share": True},
        )
    assert error.value.status_code == 409
    assert _snapshot(env) == before


@pytest.mark.parametrize("survivors", ["different_account", "ambiguous_account"])
def test_deleted_binding_target_requires_one_matching_active_survivor(existing_account, monkeypatch, survivors):
    env, local, _, calls = existing_account
    _receive(env, ["lojas_integracoes"])
    if survivors == "different_account":
        rows = [store("different-survivor", "foreign", seller="456")]
    else:
        rows = [store("first-survivor", "first"), store("second-survivor", "second")]
    _set_stores(env, [local, *rows])
    _delete(env, local)
    before = _snapshot(env)
    calls.clear()

    def identity(provider, token):
        calls.append((provider, token))
        account = "456" if token.startswith("foreign") else "123"
        return (account, "MLB") if provider == "mercadolivre" else ("company-" + account, "")

    monkeypatch.setattr(central_accounts_migration, "_identity", identity)
    with pytest.raises(HTTPException) as error:
        env.execute(env.preview())
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "shared_sync_store_binding_invalid"
    assert calls
    assert _snapshot(env) == before


@pytest.mark.parametrize("same_name", [False, True])
def test_empty_destination_rejects_two_remote_ids_for_same_authenticated_account(existing_account, same_name):
    env, _, _, calls = existing_account
    _set_stores(env, [])
    rows = [store("remote-one", "first"), store("remote-two", "second")]
    if not same_name:
        rows[1]["nome"] = "Another remote name"
    payload = json.dumps(rows).encode()
    env.publish("lojas_integracoes", [("lojas_config.json", payload)],
                source_connections=bindings.source_connection_hashes(payload))
    before = _snapshot(env)
    original_bundles = dict(env.bundles)

    with pytest.raises(HTTPException) as error:
        env.execute(env.preview(["lojas_integracoes"]), ["lojas_integracoes"])

    assert error.value.status_code == 409
    assert error.value.detail["reason"] == "duplicate_remote_account"
    assert "contas repetidas ou conflitantes" in error.value.detail["message"]
    assert len(calls) == 4
    assert _snapshot(env) == before
    assert env.bundles == original_bundles


@pytest.mark.parametrize("same_name", [False, True])
def test_empty_destination_accepts_two_different_remote_accounts(existing_account, monkeypatch, same_name):
    env, _, _, calls = existing_account
    _set_stores(env, [])
    rows = [store("remote-one", "first"), store("remote-two", "second", seller="456")]
    if not same_name:
        rows[1]["nome"] = "Another remote name"
    payload = json.dumps(rows).encode()
    env.publish("lojas_integracoes", [("lojas_config.json", payload)],
                source_connections=bindings.source_connection_hashes(payload))

    def identity(provider, token):
        calls.append((provider, token))
        account = "456" if token.startswith("second") else "123"
        return (account, "MLB") if provider == "mercadolivre" else ("company-" + account, "")

    monkeypatch.setattr(central_accounts_migration, "_identity", identity)
    _receive(env, ["lojas_integracoes"])
    saved = {row["store_id"]: row for row in json.loads((env.tenant / "lojas_config.json").read_bytes())}
    assert set(saved) == {row["store_id"] for row in rows}
    for row in rows:
        for provider, expected in row["integracoes"].items():
            assert {key: saved[row["store_id"]]["integracoes"][provider][key] for key in expected} == expected
    assert len(calls) == 4
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()


@pytest.mark.parametrize("configs", [{}, {"mercadolivre": {}, "bling": {}}, {
    "mercadolivre": {"app_id": "same-app", "client_secret": "synthetic-app-secret"},
    "bling": {"id": "same-bling-app", "secret": "synthetic-app-secret"},
}])
@pytest.mark.parametrize("first_connected", [False, True])
def test_empty_remote_configs_are_not_account_evidence(existing_account, configs, first_connected):
    env, _, _, calls = existing_account
    _set_stores(env, [])
    rows = [store("remote-one", "first"), store("remote-two", "second")]
    rows[1]["integracoes"] = deepcopy(configs)
    if not first_connected:
        rows[0]["integracoes"] = deepcopy(configs)
    payload = json.dumps(rows).encode()
    env.publish("lojas_integracoes", [("lojas_config.json", payload)],
                source_connections=bindings.source_connection_hashes(payload))

    _receive(env, ["lojas_integracoes"])

    saved = json.loads((env.tenant / "lojas_config.json").read_bytes())
    assert {row["store_id"] for row in saved} == {"remote-one", "remote-two"}
    assert calls == []
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()


@pytest.mark.parametrize("failure", ["missing_token", "unavailable"])
def test_remote_account_candidates_require_successful_authentication(existing_account, monkeypatch, failure):
    env, _, _, calls = existing_account
    _set_stores(env, [])
    rows = [store("remote-one", "first"), store("remote-two", "second")]
    if failure == "missing_token":
        rows[1]["integracoes"]["mercadolivre"].pop("access_token")
    else:
        def unavailable(*args):
            raise HTTPException(503, "synthetic unavailable")
        monkeypatch.setattr(central_accounts_migration, "_identity", unavailable)
    payload = json.dumps(rows).encode()
    env.publish("lojas_integracoes", [("lojas_config.json", payload)],
                source_connections=bindings.source_connection_hashes(payload))
    before = _snapshot(env)
    original_bundles = dict(env.bundles)

    with pytest.raises(HTTPException) as error:
        env.execute(env.preview(["lojas_integracoes"]), ["lojas_integracoes"])

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "shared_sync_store_account_unverifiable"
    assert calls == []
    assert _snapshot(env) == before
    assert env.bundles == original_bundles
