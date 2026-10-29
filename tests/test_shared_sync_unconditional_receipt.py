"""Receipt applies authorized store data without external account validation."""
from copy import deepcopy
import json
import sqlite3

import pandas as pd
import pytest
from fastapi import HTTPException

from test_shared_sync_user_pull_order import new_machine  # noqa: F401
from test_shared_sync_store_reconciliation import seed_binding, store
from backend.services import central_accounts_migration as migration
from backend.services import integracoes
from backend.services import shared_sync_apply_scope as apply_scope
from backend.services import shared_sync_store_bindings as bindings


CADASTRO = "cadastro_produtos_lojas.csv"
SCOPES = ["cadastro", "lojas_integracoes"]


@pytest.fixture
def receipt(new_machine, monkeypatch):
    calls = []

    def forbidden_identity(*args, **kwargs):
        calls.append("identity")
        raise AssertionError("Receipt must not query platform account identity")

    def forbidden_request(*args, **kwargs):
        calls.append("request")
        raise AssertionError("Receipt must not request external account validation")

    monkeypatch.setattr(migration, "_identity", forbidden_identity)
    monkeypatch.setattr(migration, "_request", forbidden_request)
    yield new_machine
    assert calls == []


def _publish(env, local, remote):
    data = json.dumps(local).encode()
    for name in ("lojas_config.json", "lojas_config.json.bak"):
        (env.tenant / name).write_bytes(data)
    env.publish("lojas_integracoes", [("lojas_config.json", json.dumps(remote).encode())])
    env.publish("cadastro", env.product_files)


def _receive(env, scopes=SCOPES):
    return env.execute(env.preview(scopes), scopes)


def _snapshot(env):
    return {
        name: (env.tenant / name).read_bytes() if (env.tenant / name).is_file() else None
        for name in ("lojas_config.json", "lojas_config.json.bak", "integracoes.json", bindings.BINDINGS_FILE,
                     CADASTRO, "cadastro_fotos_config.json", "vendas_historico.db")
    }


def _assert_idempotent_receipt(env, before):
    after = _snapshot(env)
    previous_config = before["lojas_config.json"]
    before = {name: value for name, value in before.items() if name != "lojas_config.json.bak"}
    backup = after.pop("lojas_config.json.bak")
    assert after == before
    assert json.loads(backup) == json.loads(previous_config)


def _rows(env):
    return json.loads((env.tenant / "lojas_config.json").read_bytes())


def _assert_connections(actual, expected):
    for provider, config in expected["integracoes"].items():
        assert {key: actual["integracoes"][provider][key] for key in config} == config


def _products(env):
    return pd.read_csv(env.tenant / CADASTRO, dtype=str)[["store_id", "sku"]].to_dict("records")


def _publish_sales(env, store_id, label):
    with sqlite3.connect(":memory:") as database:
        database.execute("CREATE TABLE vendas(id_unico TEXT PRIMARY KEY, loja_conta TEXT, loja_id TEXT, sku TEXT, store_id TEXT)")
        database.execute("INSERT INTO vendas VALUES(?, ?, ?, ?, ?)",
                         ("synthetic-sale", label, "synthetic-channel", "001", store_id))
        database.commit()
        data = database.serialize()
    env.publish("vendas", [("vendas_historico.db", data)])
    env.link["scopes"] = [*SCOPES, "vendas"]
    env.link["bundles"]["vendas"] = "remote-vendas"
    for permissions in env.permissions.values():
        permissions["vendas"] = True


@pytest.mark.parametrize("state", ["expired", "inactive", "without_tokens", "empty"])
def test_empty_destination_receives_original_connections_in_any_auth_state(receipt, state):
    env = receipt
    remote = store("store-a", "remote")
    for config in remote["integracoes"].values():
        if state == "expired":
            config["expires_at"] = 1
        elif state == "inactive":
            config.update(connected=False, oauth_invalid=True)
        elif state == "without_tokens":
            config.pop("access_token")
            config.pop("refresh_token")
        else:
            config.clear()
    _publish(env, [], [remote])
    bundles = deepcopy(env.bundles)

    assert _receive(env)["success"]

    assert [row["store_id"] for row in _rows(env)] == ["store-a"]
    _assert_connections(_rows(env)[0], remote)
    assert _products(env) == [{"store_id": "store-a", "sku": "001"}]
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()
    assert env.bundles == bundles


@pytest.mark.parametrize("similarity", ["same_name", "same_account_claims"])
def test_new_remote_id_is_not_merged_into_a_local_store(receipt, similarity):
    env = receipt
    local, remote = store("local-original", "local"), store("store-a", "remote")
    if similarity == "same_name":
        remote["integracoes"]["mercadolivre"]["user_id"] = "different-account"
    else:
        remote["nome"] = "Different display name"
    _publish(env, [local], [remote])

    assert _receive(env)["success"]

    rows = {row["store_id"]: row for row in _rows(env)}
    assert set(rows) == {"local-original", "store-a"}
    _assert_connections(rows["local-original"], local)
    _assert_connections(rows["store-a"], remote)
    assert _products(env) == [{"store_id": "store-a", "sku": "001"}]
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()


@pytest.mark.parametrize("missing_provider", ["bling", "mercadolivre"])
def test_same_store_id_preserves_local_connections_and_adds_missing_provider(receipt, missing_provider):
    env = receipt
    local, remote = store("store-a", "local"), store("store-a", "remote")
    local["integracoes"].pop(missing_provider)
    _publish(env, [local], [remote])

    assert _receive(env)["success"]

    assert len(_rows(env)) == 1
    current = _rows(env)[0]
    _assert_connections(current, local)
    supplied = remote["integracoes"][missing_provider]
    assert {key: current["integracoes"][missing_provider][key] for key in supplied} == supplied
    assert _products(env) == [{"store_id": "store-a", "sku": "001"}]
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()


@pytest.mark.parametrize("scopes", [["cadastro"], SCOPES])
def test_historical_alias_maps_cadastro_despite_changed_oauth_connections(receipt, scopes):
    env = receipt
    local, remote = store("local-original", "local"), store("store-a", "remote")
    _publish(env, [local], [remote])
    seed_binding(env, local, remote)
    original_ledger = (env.tenant / bindings.BINDINGS_FILE).read_bytes()
    for side, row in (("local", local), ("remote", remote)):
        row["integracoes"]["mercadolivre"].update(user_id=side + "-changed-account", access_token=side + "-new-token")
        row["integracoes"]["bling"].update(connected=False, oauth_invalid=True)
    _publish(env, [local], [remote])

    assert _receive(env, scopes)["success"]

    assert [row["store_id"] for row in _rows(env)] == ["local-original"]
    _assert_connections(_rows(env)[0], local)
    assert _products(env) == [{"store_id": "local-original", "sku": "001"}]
    assert (env.tenant / bindings.BINDINGS_FILE).read_bytes() == original_ledger


@pytest.mark.parametrize("destination", ["empty", "occupied", "historical_alias"])
def test_receiving_the_same_package_twice_is_idempotent(receipt, destination):
    env = receipt
    local, remote = store("local-original", "local"), store("store-a", "remote")
    _publish(env, [] if destination == "empty" else [local], [remote])
    if destination == "historical_alias":
        seed_binding(env, local, remote)
    bundles = deepcopy(env.bundles)
    assert _receive(env)["success"]
    before = _snapshot(env)

    assert _receive(env)["success"]

    _assert_idempotent_receipt(env, before)
    assert env.bundles == bundles


def test_legacy_store_receives_a_stable_id_without_creating_sales_account_proof(receipt):
    env = receipt
    remote = store("store-a", "remote")
    remote.pop("store_id")
    _publish(env, [], [remote])
    assert _receive(env, ["lojas_integracoes"])["success"]
    first = _snapshot(env)
    local_id = _rows(env)[0]["store_id"]
    assert local_id
    ledger = json.loads((env.tenant / bindings.BINDINGS_FILE).read_bytes())
    entry = next(iter(ledger["peers"].values()))
    record = next(iter(entry["stores"].values()))
    assert record["local_id"] == local_id
    assert record["receipt_only"] is True
    assert record["accounts"] == {}
    assert record["unverified_providers"] == ["bling", "mercadolivre"]

    assert _receive(env, ["lojas_integracoes"])["success"]

    _assert_idempotent_receipt(env, first)
    _publish_sales(env, "", remote["nome"])
    with pytest.raises(HTTPException) as caught:
        _receive(env, ["vendas"])
    assert caught.value.detail["code"] == "shared_sync_store_sales_account_unverified"
    assert not (env.tenant / "vendas_historico.db").exists()


@pytest.mark.parametrize("restriction", ["stale_source_proof", "unverified_bling"])
def test_receiving_cadastro_does_not_rewrite_historical_sales_proof(receipt, restriction):
    env = receipt
    local, remote = store("local-original", "local"), store("store-a", "remote")
    _publish(env, [local], [remote])
    seed_binding(env, local, remote, unverified=["bling"] if restriction == "unverified_bling" else [])
    original_ledger = (env.tenant / bindings.BINDINGS_FILE).read_bytes()
    assert _receive(env)["success"]
    assert (env.tenant / bindings.BINDINGS_FILE).read_bytes() == original_ledger
    _publish_sales(env, remote["store_id"], remote["nome"])

    with pytest.raises(HTTPException) as caught:
        _receive(env, ["vendas"])

    expected = "shared_sync_store_sales_account_unverified" if restriction == "unverified_bling" else "shared_sync_store_binding_stale"
    assert caught.value.detail["code"] == expected
    assert (env.tenant / bindings.BINDINGS_FILE).read_bytes() == original_ledger
    assert not (env.tenant / "vendas_historico.db").exists()


@pytest.mark.parametrize("side", ["local", "remote"])
def test_an_additional_disconnected_store_does_not_block_receipt(receipt, side):
    env = receipt
    local, remote = store("local-original", "local"), store("store-a", "remote")
    extra = store("extra-store", "extra")
    extra["nome"] = "Additional disconnected store"
    for config in extra["integracoes"].values():
        config.update(connected=False, oauth_invalid=True)
        config.pop("access_token")
        config.pop("refresh_token")
    _publish(env, [local, extra] if side == "local" else [local], [remote, extra] if side == "remote" else [remote])

    assert _receive(env)["success"]

    assert {row["store_id"] for row in _rows(env)} == {"local-original", "store-a", "extra-store"}
    assert _products(env) == [{"store_id": "store-a", "sku": "001"}]


def test_legacy_local_recovery_does_not_reinstate_the_oauth_conflict_guard(receipt, monkeypatch):
    env = receipt
    local = store("store-a", "local", seller="123")
    legacy = store("store-a", "legacy", seller="456")
    remote = store("store-a", "remote", seller="123")
    _publish(env, [local], [remote])
    (env.tenant.parent / "lojas_config.json").write_text(json.dumps([legacy]), encoding="utf-8")
    monkeypatch.setattr(integracoes, "_integracoes_pode_criar_lojas_legadas", lambda client: True)
    monkeypatch.setattr(apply_scope, "_shared_sync_legacy_root_pertence_ao_cliente", lambda *args: True)

    assert _receive(env)["success"]

    assert [row["store_id"] for row in _rows(env)] == ["store-a"]
    _assert_connections(_rows(env)[0], local)
    assert _products(env) == [{"store_id": "store-a", "sku": "001"}]


@pytest.mark.parametrize("occupied", [False, True])
def test_remote_legacy_accounts_are_received_without_replacing_local_connections(receipt, occupied):
    env = receipt
    local, remote = store("store-a", "local"), store("store-a", "remote")
    local["integracoes"].pop("bling")
    canonical = {"store_id": remote["store_id"], "nome": remote["nome"], "integracoes": {}}
    _publish(env, [local] if occupied else [], [canonical])
    env.publish("lojas_integracoes", [
        ("lojas_config.json", json.dumps([canonical]).encode()),
        ("integracoes.json", json.dumps({remote["nome"]: remote["integracoes"]}).encode()),
    ])

    assert _receive(env)["success"]

    assert [row["store_id"] for row in _rows(env)] == ["store-a"]
    expected = deepcopy(remote)
    if occupied:
        expected["integracoes"]["mercadolivre"] = local["integracoes"]["mercadolivre"]
    _assert_connections(_rows(env)[0], expected)
    assert _products(env) == [{"store_id": "store-a", "sku": "001"}]
    first = _snapshot(env)

    assert _receive(env)["success"]

    _assert_idempotent_receipt(env, first)


def test_legacy_name_normalization_matches_only_the_source_snapshot(receipt):
    env = receipt
    local, remote = store("local-original", "local"), store("store-a", "remote")
    local["nome"] = "LOJA SAO PAULO"
    remote["nome"] = "Loja S\u00e3o Paulo"
    canonical = {"store_id": "store-a", "nome": remote["nome"], "integracoes": {}}
    _publish(env, [local], [canonical])
    env.publish("lojas_integracoes", [
        ("lojas_config.json", json.dumps([canonical]).encode()),
        ("integracoes.json", json.dumps({"LOJA SAO PAULO": remote["integracoes"]}).encode()),
    ])

    assert _receive(env)["success"]

    rows = {row["store_id"]: row for row in _rows(env)}
    assert set(rows) == {"local-original", "store-a"}
    _assert_connections(rows["local-original"], local)
    _assert_connections(rows["store-a"], remote)
    assert _products(env) == [{"store_id": "store-a", "sku": "001"}]


@pytest.mark.parametrize("empty_block", [{}, {"connected": False, "oauth_invalid": True}])
def test_legacy_account_fills_a_canonical_provider_without_account_data(receipt, empty_block):
    env = receipt
    remote = store("store-a", "remote")
    canonical = {"store_id": "store-a", "nome": remote["nome"],
                 "integracoes": {provider: deepcopy(empty_block) for provider in remote["integracoes"]}}
    _publish(env, [], [canonical])
    env.publish("lojas_integracoes", [
        ("lojas_config.json", json.dumps([canonical]).encode()),
        ("integracoes.json", json.dumps({remote["nome"]: remote["integracoes"]}).encode()),
    ])

    assert _receive(env)["success"]

    assert [row["store_id"] for row in _rows(env)] == ["store-a"]
    _assert_connections(_rows(env)[0], remote)
    assert _products(env) == [{"store_id": "store-a", "sku": "001"}]


def test_ambiguous_normalized_legacy_name_is_rejected_without_writes(receipt):
    env = receipt
    first, second = store("store-a", "first"), store("store-b", "second")
    first["nome"], second["nome"] = "Loja S\u00e3o Paulo", "Loja Sao Paulo"
    _publish(env, [], [first, second])
    env.publish("lojas_integracoes", [
        ("lojas_config.json", json.dumps([first, second]).encode()),
        ("integracoes.json", json.dumps({"LOJA SAO PAULO": first["integracoes"]}).encode()),
    ])
    before = _snapshot(env)

    with pytest.raises(HTTPException) as caught:
        _receive(env)

    assert caught.value.status_code == 502
    assert "ambigua" in str(caught.value.detail)
    assert _snapshot(env) == before


def test_invalid_remote_legacy_integrations_are_rejected_before_any_store_write(receipt):
    env = receipt
    local, remote = store("local-original", "local"), store("store-a", "remote")
    _publish(env, [local], [remote])
    env.publish("lojas_integracoes", [
        ("lojas_config.json", json.dumps([remote]).encode()),
        ("integracoes.json", b"{"),
    ])
    before = _snapshot(env)

    with pytest.raises(HTTPException) as caught:
        _receive(env)

    assert caught.value.status_code in {409, 502}
    assert _snapshot(env) == before


@pytest.mark.parametrize("invalid", [b"{", b"{}"])
def test_structurally_invalid_store_package_is_still_rejected(receipt, invalid):
    env = receipt
    env.publish("lojas_integracoes", [("lojas_config.json", invalid)])
    before = _snapshot(env)

    with pytest.raises(HTTPException) as caught:
        _receive(env)

    assert caught.value.status_code in {409, 502}
    assert _snapshot(env) == before


def test_duplicate_remote_store_ids_are_still_rejected(receipt):
    env = receipt
    first, second = store("store-a", "first"), store("store-a", "second")
    second["nome"] = "Second store with duplicate id"
    _publish(env, [], [first, second])
    before = _snapshot(env)

    with pytest.raises(HTTPException) as caught:
        _receive(env)

    assert caught.value.status_code in {409, 502}
    assert "duplicado" in str(caught.value.detail)
    assert _snapshot(env) == before


@pytest.mark.parametrize("participant", ["alice", "bob"])
def test_both_participants_still_need_store_permission(receipt, participant):
    env = receipt
    operation = env.preview()
    env.permissions[participant]["integracao"] = False
    before = _snapshot(env)

    with pytest.raises(HTTPException) as caught:
        env.execute(operation)

    assert caught.value.status_code == 403
    assert _snapshot(env) == before
    assert env.calls == []


def test_another_user_cannot_receive_an_approved_package(receipt):
    env = receipt
    operation = env.preview()
    env.session["username"] = "another-user"
    before = _snapshot(env)

    with pytest.raises(HTTPException) as caught:
        env.execute(operation)

    assert caught.value.status_code == 403
    assert _snapshot(env) == before
    assert env.calls == []
