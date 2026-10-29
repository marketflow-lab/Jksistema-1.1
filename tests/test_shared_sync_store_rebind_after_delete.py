"""Historical aliases respect deletions without new account reconciliation."""
from copy import deepcopy
import json

import pandas as pd
import pytest
from fastapi import HTTPException

from test_shared_sync_store_reconciliation import bound_account, existing_account, new_machine, seed_binding, store  # noqa: F401
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


def test_deleted_remote_copy_reuses_historical_live_alias_and_repeats(bound_account):
    env, local, remote, calls = bound_account
    tombstones = _delete_remote_copy(env, local, remote)
    historical = (env.tenant / bindings.BINDINGS_FILE).read_bytes()
    for _ in range(2):
        _receive(env)
        _assert_store(env, local)
        assert calls == []
        assert (env.tenant / "lojas_sync_tombstones.json").read_bytes() == tombstones
        assert (env.tenant / bindings.BINDINGS_FILE).read_bytes() == historical


@pytest.mark.parametrize("source_client", ["tenant-a", "another-source-tenant"])
def test_two_historical_peer_aliases_keep_one_local_identity(bound_account, source_client):
    env, local, remote, calls = bound_account
    tombstones = _delete_remote_copy(env, local, remote)
    _receive(env)
    incoming = store("remote-c", "remote-c")
    env.link.update(id="link-user-c", source_username="charlie", source_client_id=source_client)
    env.permissions["charlie"] = dict(env.permissions["alice"])
    seed_binding(env, local, incoming)
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
    assert calls == []
    assert (env.tenant / "lojas_sync_tombstones.json").read_bytes() == tombstones


@pytest.mark.parametrize("survivor_id", ["live-survivor", "store-a"])
def test_deleted_binding_target_does_not_reconcile_to_a_different_survivor(bound_account, survivor_id):
    env, local, remote, calls = bound_account
    survivor = store(survivor_id, "survivor")
    _set_stores(env, [local, survivor])
    tombstones = _delete(env, local)
    before = _snapshot(env)
    if survivor_id != remote["store_id"]:
        with pytest.raises(HTTPException):
            env.execute(env.preview(["cadastro"]), ["cadastro"])
        assert _snapshot(env) == before
    _receive(env)
    saved = {row["store_id"]: row for row in json.loads((env.tenant / "lojas_config.json").read_bytes())}
    assert set(saved) == {survivor_id, remote["store_id"]}
    assert saved[survivor_id]["integracoes"]["bling"]["access_token"] == "survivor-bling"
    assert local["store_id"] not in saved
    assert pd.read_csv(env.tenant / REL, dtype=str)["store_id"].tolist() == [remote["store_id"]]
    assert calls == []
    assert (env.tenant / "lojas_sync_tombstones.json").read_bytes() == tombstones
    assert next(iter(_ledger(env)["peers"].values()))["stores"] == {}
    _receive(env, ["cadastro"])
    assert pd.read_csv(env.tenant / REL, dtype=str)["store_id"].tolist() == [remote["store_id"]]


def test_deleted_target_stays_deleted_when_original_remote_id_is_imported(bound_account):
    env, local, remote, calls = bound_account
    tombstones = _delete(env, local)
    _receive(env)
    _assert_store(env, remote)
    assert (env.tenant / "lojas_sync_tombstones.json").read_bytes() == tombstones
    assert next(iter(_ledger(env)["peers"].values()))["stores"] == {}
    assert calls == []


def test_deleted_legacy_receipt_is_not_recreated_with_a_new_id(existing_account):
    env, _, remote, calls = existing_account
    _set_stores(env, [])
    remote = deepcopy(remote)
    remote.pop("store_id")
    payload = json.dumps([remote]).encode()
    env.publish("lojas_integracoes", [("lojas_config.json", payload)],
                source_connections=bindings.source_connection_hashes(payload))
    _receive(env, ["lojas_integracoes"])
    imported = json.loads((env.tenant / "lojas_config.json").read_bytes())[0]
    ledger_before = (env.tenant / bindings.BINDINGS_FILE).read_bytes()
    record = next(iter(next(iter(_ledger(env)["peers"].values()))["stores"].values()))
    assert record["receipt_only"] is True
    assert record["local_id"] == imported["store_id"]
    tombstones = _delete(env, imported)

    for _ in range(2):
        _receive(env, ["lojas_integracoes"])
        assert json.loads((env.tenant / "lojas_config.json").read_bytes()) == []
        assert (env.tenant / "lojas_sync_tombstones.json").read_bytes() == tombstones
        assert (env.tenant / bindings.BINDINGS_FILE).read_bytes() == ledger_before
    assert calls == []


@pytest.mark.parametrize("seller", ["123", "456"])
def test_another_peer_without_alias_keeps_its_original_id(bound_account, seller):
    env, local, remote, calls = bound_account
    _delete_remote_copy(env, local, remote)
    _receive(env)
    incoming = store("other-peer-store", "foreign", seller=seller)
    env.link.update(id="link-other-peer", source_username="charlie")
    env.permissions["charlie"] = dict(env.permissions["alice"])
    _publish(env, incoming)
    _receive(env)
    saved = {row["store_id"]: row for row in json.loads((env.tenant / "lojas_config.json").read_bytes())}
    assert set(saved) == {local["store_id"], incoming["store_id"]}
    assert saved[local["store_id"]]["integracoes"]["mercadolivre"]["access_token"] == "local-ml"
    assert saved[incoming["store_id"]]["integracoes"]["mercadolivre"]["user_id"] == seller
    assert set(pd.read_csv(env.tenant / REL, dtype=str)["store_id"]) == set(saved)
    assert len(_ledger(env)["peers"]) == 1
    assert calls == []


@pytest.mark.parametrize("invalid", ["peer", "local_target"])
def test_deleted_remote_alias_keeps_peer_and_live_target_guards(bound_account, invalid):
    env, local, remote, calls = bound_account
    _delete_remote_copy(env, local, remote)
    _receive(env)
    entry = next(iter(_ledger(env)["peers"].values()))
    peer = deepcopy(entry["identity"])
    if invalid == "peer":
        peer["link_id"] = "unrelated-link"
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
    assert calls == []


@pytest.mark.parametrize("survivors", ["different_account", "same_account"])
def test_stale_alias_does_not_select_a_survivor_by_account(bound_account, survivors):
    env, local, remote, calls = bound_account
    rows = ([store("different-survivor", "foreign", seller="456")] if survivors == "different_account"
            else [store("first-survivor", "first"), store("second-survivor", "second")])
    _set_stores(env, [local, *rows])
    tombstones = _delete(env, local)
    _receive(env)
    saved = json.loads((env.tenant / "lojas_config.json").read_bytes())
    assert {row["store_id"] for row in saved} == {remote["store_id"], *(row["store_id"] for row in rows)}
    assert pd.read_csv(env.tenant / REL, dtype=str)["store_id"].tolist() == [remote["store_id"]]
    assert next(iter(_ledger(env)["peers"].values()))["stores"] == {}
    assert (env.tenant / "lojas_sync_tombstones.json").read_bytes() == tombstones
    assert calls == []


@pytest.mark.parametrize("same_name", [False, True])
@pytest.mark.parametrize("seller", ["123", "456"])
def test_empty_destination_keeps_each_remote_id_regardless_of_account_or_name(existing_account, same_name, seller):
    env, _, _, calls = existing_account
    _set_stores(env, [])
    rows = [store("remote-one", "first"), store("remote-two", "second", seller=seller)]
    if not same_name:
        rows[1]["nome"] = "Another remote name"
    payload = json.dumps(rows).encode()
    env.publish("lojas_integracoes", [("lojas_config.json", payload)],
                source_connections=bindings.source_connection_hashes(payload))
    _receive(env, ["lojas_integracoes"])
    saved = {row["store_id"]: row for row in json.loads((env.tenant / "lojas_config.json").read_bytes())}
    assert set(saved) == {row["store_id"] for row in rows}
    for row in rows:
        for provider, expected in row["integracoes"].items():
            assert {key: saved[row["store_id"]]["integracoes"][provider][key] for key in expected} == expected
    assert calls == []
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()


@pytest.mark.parametrize("configs", [{}, {"mercadolivre": {}, "bling": {}}, {
    "mercadolivre": {"app_id": "same-app", "client_secret": "synthetic-app-secret"},
    "bling": {"id": "same-bling-app", "secret": "synthetic-app-secret"},
}])
@pytest.mark.parametrize("first_connected", [False, True])
def test_empty_remote_configs_are_received_without_lookup(existing_account, configs, first_connected):
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
def test_remote_store_receipt_does_not_require_account_authentication(existing_account, monkeypatch, failure):
    env, _, _, calls = existing_account
    _set_stores(env, [])
    rows = [store("remote-one", "first"), store("remote-two", "second")]
    if failure == "missing_token":
        rows[1]["integracoes"]["mercadolivre"].pop("access_token")
    def unavailable(*args):
        calls.append(args)
        raise HTTPException(503, "synthetic unavailable")
    monkeypatch.setattr(central_accounts_migration, "_identity", unavailable)
    payload = json.dumps(rows).encode()
    env.publish("lojas_integracoes", [("lojas_config.json", payload)],
                source_connections=bindings.source_connection_hashes(payload))
    _receive(env, ["lojas_integracoes"])
    saved = json.loads((env.tenant / "lojas_config.json").read_bytes())
    assert {row["store_id"] for row in saved} == {"remote-one", "remote-two"}
    assert calls == []
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()
