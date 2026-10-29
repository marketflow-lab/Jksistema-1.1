"""Receipt preserves inactive connections and historical sales restrictions."""
from copy import deepcopy
import json
import sqlite3

import pandas as pd
import pytest
from fastapi import HTTPException

from test_shared_sync_store_reconciliation import (  # noqa: F401
    bound_account, existing_account, new_machine, seed_binding, store,
)
from backend.services import central_accounts_migration
from backend.services import shared_sync_apply_scope as apply_scope
from backend.services import shared_sync_store_bindings as bindings


CADASTRO = "cadastro_produtos_lojas.csv"
SCOPES = ["cadastro", "lojas_integracoes"]


def _inactive(row, provider="bling"):
    config = row["integracoes"][provider]
    config.update(connected=False, oauth_invalid=True)
    for key in ("user_id", "seller_id", "account_id", "empresa_id"):
        config.pop(key, None)
    return row


def _publish(env, local, remote):
    local_bytes = json.dumps(local).encode()
    for name in ("lojas_config.json", "lojas_config.json.bak"):
        (env.tenant / name).write_bytes(local_bytes)
    remote_bytes = json.dumps(remote).encode()
    proof = bindings.source_connection_hashes(remote_bytes)
    env.publish("lojas_integracoes", [("lojas_config.json", remote_bytes)], source_connections=proof)
    env.publish("cadastro", env.product_files, source_connections=proof)
    return proof


def _snapshot(env):
    return {name: (env.tenant / name).read_bytes() if (env.tenant / name).exists() else None
            for name in ("lojas_config.json", "lojas_config.json.bak", "lojas_sync_tombstones.json",
                         bindings.BINDINGS_FILE, CADASTRO, "cadastro_fotos_config.json")}


def _receive(env, scopes=SCOPES):
    return env.execute(env.preview(scopes), scopes)


def _reject(env, *, code="shared_sync_store_account_unverifiable"):
    before = _snapshot(env)
    bundles = deepcopy(env.bundles)
    with pytest.raises(HTTPException) as caught:
        _receive(env)
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == code
    assert _snapshot(env) == before
    assert env.bundles == bundles
    return caught.value


def _seed_restricted(env, local, remote, *, provider="bling", local_unverified=False):
    accounts = {"mercadolivre": ["123", "MLB"], "bling": ["company-123", ""]}
    if local_unverified:
        accounts.pop(provider)
    return seed_binding(env, local, remote, unverified=[provider], accounts=accounts)


@pytest.mark.parametrize("side", ["local", "remote", "both"])
@pytest.mark.parametrize("provider", ["bling", "mercadolivre"])
def test_historical_alias_receives_inactive_connections_and_cadastro_without_lookup(existing_account, side, provider):
    env, local, remote, calls = existing_account
    local, remote = deepcopy(local), deepcopy(remote)
    local["nome"], remote["nome"] = "Destination name", "Source name"
    if side in {"local", "both"}:
        _inactive(local, provider)
    if side in {"remote", "both"}:
        _inactive(remote, provider)
    _publish(env, [local], [remote])
    _seed_restricted(env, local, remote, provider=provider, local_unverified=side in {"local", "both"})
    previous_record = deepcopy(_entry(env)[1]["stores"][remote["store_id"]])
    bundles = deepcopy(env.bundles)

    assert _receive(env)["success"]

    rows = json.loads((env.tenant / "lojas_config.json").read_bytes())
    assert [row["store_id"] for row in rows] == [local["store_id"]]
    assert rows[0]["nome"] == local["nome"]
    for current_provider, config in local["integracoes"].items():
        assert {key: rows[0]["integracoes"][current_provider][key] for key in config} == config
    assert pd.read_csv(env.tenant / CADASTRO, dtype=str)[["store_id", "sku"]].to_dict("records") == [
        {"store_id": local["store_id"], "sku": "001"},
    ]
    assert _entry(env)[1]["stores"][remote["store_id"]] == previous_record
    assert calls == []
    assert env.bundles == bundles
    before = _snapshot(env)
    assert _receive(env, ["cadastro"])["success"]
    assert _snapshot(env) == before
    assert calls == []


@pytest.mark.parametrize("other", ["absent", "empty", "inactive", "missing_token"])
def test_empty_destination_receives_inactive_connections_without_other_account(existing_account, monkeypatch, other):
    env, _, remote, calls = existing_account
    remote = _inactive(deepcopy(remote))
    if other == "absent":
        remote["integracoes"].pop("mercadolivre")
    elif other == "empty":
        remote["integracoes"]["mercadolivre"] = {}
    elif other == "inactive":
        _inactive(remote, "mercadolivre")
    else:
        remote["integracoes"]["mercadolivre"].pop("access_token")
    monkeypatch.setattr(central_accounts_migration, "_identity", lambda *args: pytest.fail("receipt must not query OAuth"))
    _publish(env, [], [remote])
    bundles = deepcopy(env.bundles)

    assert _receive(env)["success"]

    rows = json.loads((env.tenant / "lojas_config.json").read_bytes())
    assert [row["store_id"] for row in rows] == [remote["store_id"]]
    for provider, config in remote["integracoes"].items():
        assert {key: rows[0]["integracoes"][provider][key] for key in config} == config
    assert rows[0]["integracoes"]["bling"]["connected"] is False
    assert pd.read_csv(env.tenant / CADASTRO, dtype=str)["store_id"].tolist() == [remote["store_id"]]
    assert calls == []
    assert env.bundles == bundles
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()


@pytest.mark.parametrize("side", ["local", "remote"])
@pytest.mark.parametrize("change", ["invalid_flag", "declared_account"])
def test_cadastro_keeps_historical_alias_when_inactive_connection_changes(existing_account, side, change):
    env, local, remote, calls = existing_account
    local, remote = _inactive(deepcopy(local)), _inactive(deepcopy(remote))
    proof = _publish(env, [local], [remote])
    _, entry = _seed_restricted(env, local, remote, local_unverified=True)
    historical = deepcopy(entry["stores"][remote["store_id"]])
    row = local if side == "local" else remote
    old_hash = bindings._connection_hash(row)
    if change == "invalid_flag":
        row["integracoes"]["bling"]["oauth_invalid"] = False
    else:
        row["integracoes"]["bling"]["account_id"] = "new-claim"
    assert bindings._connection_hash(row) != old_hash
    if side == "local":
        (env.tenant / "lojas_config.json").write_text(json.dumps([local]), encoding="utf-8")
    else:
        proof = {remote["store_id"]: bindings._connection_hash(remote)}
    before = _snapshot(env)

    sources = bindings.remap_bound_sources("tenant-a", str(env.tenant), entry["identity"], "cadastro",
                                          env.product_files, source_connections=proof)

    from io import BytesIO
    assert pd.read_csv(BytesIO(dict(sources)[CADASTRO]), dtype=str)["store_id"].tolist() == [local["store_id"]]
    assert _snapshot(env) == before
    assert _entry(env)[1]["stores"][remote["store_id"]] == historical
    assert calls == []


def test_normal_connection_fingerprints_do_not_change_with_irrelevant_invalid_false_flag():
    row = store("normal", "normal")
    original = bindings._connection_hash(row)
    keys = ('app_id', 'client_id', 'id', 'client_secret', 'secret_key', 'secret',
            'access_token', 'token', 'refresh_token', 'user_id', 'seller_id',
            'account_id', 'empresa_id', 'site_id', 'connected')
    legacy = bindings._hash({provider: {key: config[key] for key in keys if key in config}
                             for provider, config in row["integracoes"].items()})
    assert original == legacy
    row["integracoes"]["bling"]["oauth_invalid"] = False
    assert bindings._connection_hash(row) == original


def _sales_bytes(store_id, label, *, legacy=False, sale_id="remote-sale"):
    with sqlite3.connect(":memory:") as database:
        columns = "id_unico TEXT PRIMARY KEY, loja_conta TEXT, loja_id TEXT, sku TEXT"
        values = [sale_id, label, "synthetic-bling-channel", "001"]
        if not legacy:
            columns += ", store_id TEXT"
            values.append(store_id)
        database.execute("CREATE TABLE vendas(" + columns + ")")
        database.execute("INSERT INTO vendas VALUES(" + ",".join("?" for _ in values) + ")", values)
        database.commit()
        return database.serialize()


def _read_sales(data, query):
    with sqlite3.connect(":memory:") as database:
        database.deserialize(data)
        return database.execute(query).fetchall()


def _entry(env):
    ledger = json.loads((env.tenant / bindings.BINDINGS_FILE).read_bytes())
    return ledger, next(iter(ledger["peers"].values()))


def _apply_sales(env, entry, source_connections, data, extra_sources=()):
    env.publish("vendas", [("vendas_historico.db", data), *extra_sources], source_connections=source_connections)
    return apply_scope._shared_sync_aplicar_pacote(
        "tenant-a", "vendas", env.bundles["remote-vendas"], username="bob",
        scope_config={"store_peer": entry["identity"], "user_share": True},
    )


def test_legacy_source_proof_still_cannot_authorize_sales(existing_account):
    env, local, remote, calls = existing_account
    local, remote = _inactive(deepcopy(local), "mercadolivre"), _inactive(deepcopy(remote), "mercadolivre")
    _publish(env, [local], [remote])
    _, entry = _seed_restricted(env, local, remote, provider="mercadolivre", local_unverified=True)
    legacy_remote = deepcopy(remote)
    legacy_remote["integracoes"]["mercadolivre"].pop("oauth_invalid")
    legacy_proof = {remote["store_id"]: bindings._connection_hash(legacy_remote)}
    before = _snapshot(env)

    with pytest.raises(HTTPException) as caught:
        _apply_sales(env, entry, legacy_proof, _sales_bytes(remote["store_id"], remote["nome"]))

    assert caught.value.detail["code"] == "shared_sync_store_binding_stale"
    assert calls == []
    assert _snapshot(env) == before
    assert not (env.tenant / "vendas_historico.db").exists()


@pytest.mark.parametrize("side", ["local", "remote", "both"])
@pytest.mark.parametrize("legacy", [False, True])
def test_unverified_bling_blocks_all_sales_before_writes_even_with_same_legacy_name(existing_account, side, legacy):
    env, local, remote, calls = existing_account
    local, remote = deepcopy(local), deepcopy(remote)
    if side in {"local", "both"}:
        _inactive(local)
    if side in {"remote", "both"}:
        _inactive(remote)
    proof = _publish(env, [local], [remote])
    _seed_restricted(env, local, remote, local_unverified=side in {"local", "both"})
    assert _receive(env)["success"]
    _, entry = _entry(env)
    assert entry["stores"][remote["store_id"]]["unverified_providers"] == ["bling"]
    assert local["nome"] == remote["nome"]
    current = _sales_bytes(local["store_id"], local["nome"], legacy=legacy, sale_id="local-sale")
    (env.tenant / "vendas_historico.db").write_bytes(current)
    source = _sales_bytes(remote["store_id"], remote["nome"], legacy=legacy)
    before = {str(path.relative_to(env.tenant)): path.read_bytes()
              for path in env.tenant.rglob("*") if path.is_file()}
    calls.clear()

    with pytest.raises(HTTPException) as caught:
        _apply_sales(env, entry, proof, source,
                     [("produtos_compilado.csv", b"store_id,sku\nstore-a,001\n")])

    assert caught.value.detail["code"] == "shared_sync_store_sales_account_unverified"
    assert {str(path.relative_to(env.tenant)): path.read_bytes()
            for path in env.tenant.rglob("*") if path.is_file()} == before
    assert _read_sales(source, "SELECT id_unico FROM vendas") == [("remote-sale",)]
    assert calls == []


@pytest.mark.parametrize("legacy", [False, True])
def test_normal_historical_alias_still_imports_sales(bound_account, legacy):
    env, local, remote, calls = bound_account
    proof = _publish(env, [local], [remote])
    assert _receive(env)["success"]
    _, entry = _entry(env)
    assert "unverified_providers" not in entry["stores"][remote["store_id"]]
    source = _sales_bytes(remote["store_id"], remote["nome"], legacy=legacy)

    result = _apply_sales(env, entry, proof, source)

    assert result["file_count"] == 1
    persisted = (env.tenant / "vendas_historico.db").read_bytes()
    assert _read_sales(persisted, "SELECT id_unico FROM vendas") == [("remote-sale",)]
    if not legacy:
        assert _read_sales(persisted, "SELECT store_id FROM vendas") == [(local["store_id"],)]
    assert calls == []


def test_unverified_bling_does_not_block_sales_without_reference_to_that_alias(existing_account):
    env, local, remote, calls = existing_account
    remote = _inactive(deepcopy(remote))
    proof = _publish(env, [local], [remote])
    _, entry = _seed_restricted(env, local, remote)
    sources = [("vendas_historico.db", _sales_bytes("unrelated-id", "Unrelated name"))]
    assert bindings.remap_bound_sources("tenant-a", str(env.tenant), entry["identity"], "vendas",
                                       sources, source_connections=proof) == sources
    assert calls == []


@pytest.mark.parametrize("invalid", [None, False, "bling", [], ["unknown"], ["bling", "bling"],
                                     ["mercadolivre", "bling"], [{}]])
def test_invalid_optional_provider_marker_rejects_ledger_before_sales_write(existing_account, invalid):
    env, local, remote, _ = existing_account
    remote = _inactive(deepcopy(remote))
    proof = _publish(env, [local], [remote])
    ledger, entry = _seed_restricted(env, local, remote)
    entry["stores"][remote["store_id"]]["unverified_providers"] = invalid
    (env.tenant / bindings.BINDINGS_FILE).write_text(json.dumps(ledger), encoding="utf-8")
    before = _snapshot(env)

    with pytest.raises(HTTPException) as caught:
        _apply_sales(env, entry, proof, _sales_bytes(remote["store_id"], remote["nome"]))

    assert caught.value.detail["code"] == "shared_sync_store_binding_invalid"
    assert _snapshot(env) == before
    assert not (env.tenant / "vendas_historico.db").exists()


@pytest.mark.parametrize("side", ["local", "remote", "both"])
def test_receiving_reconnected_settings_does_not_clear_historical_sales_restriction(existing_account, side):
    env, local, remote, calls = existing_account
    local, remote = deepcopy(local), deepcopy(remote)
    if side in {"local", "both"}:
        _inactive(local)
    if side in {"remote", "both"}:
        _inactive(remote)
    _publish(env, [local], [remote])
    _, entry = _seed_restricted(env, local, remote, local_unverified=side in {"local", "both"})
    historical = deepcopy(entry["stores"][remote["store_id"]])
    for row in (local, remote):
        row["integracoes"]["bling"].update(connected=True, oauth_invalid=False)
    proof = _publish(env, [local], [remote])

    assert _receive(env)["success"]

    _, entry = _entry(env)
    assert entry["stores"][remote["store_id"]] == historical
    assert calls == []
    before = _snapshot(env)
    with pytest.raises(HTTPException) as caught:
        _apply_sales(env, entry, proof, _sales_bytes(remote["store_id"], remote["nome"]))
    assert caught.value.detail["code"] == "shared_sync_store_sales_account_unverified"
    assert _snapshot(env) == before
    assert not (env.tenant / "vendas_historico.db").exists()


@pytest.mark.parametrize("side", ["local", "remote", "both"])
@pytest.mark.parametrize("replacement", ["remove", "empty"])
def test_removing_invalid_configuration_does_not_clear_sales_account_restriction(existing_account, side, replacement):
    env, local, remote, calls = existing_account
    local, remote = deepcopy(local), deepcopy(remote)
    if side in {"local", "both"}:
        _inactive(local)
    if side in {"remote", "both"}:
        _inactive(remote)
    _publish(env, [local], [remote])
    _, entry = _seed_restricted(env, local, remote, local_unverified=side in {"local", "both"})
    historical = deepcopy(entry["stores"][remote["store_id"]])
    for name, row in (("local", local), ("remote", remote)):
        if side in {name, "both"}:
            if replacement == "remove":
                row["integracoes"].pop("bling")
            else:
                row["integracoes"]["bling"] = {}
    proof = _publish(env, [local], [remote])

    assert _receive(env)["success"]

    _, entry = _entry(env)
    assert entry["stores"][remote["store_id"]] == historical
    assert calls == []
    before = _snapshot(env)
    with pytest.raises(HTTPException) as caught:
        _apply_sales(env, entry, proof, _sales_bytes(remote["store_id"], remote["nome"]))
    assert caught.value.detail["code"] == "shared_sync_store_sales_account_unverified"
    assert _snapshot(env) == before
    assert not (env.tenant / "vendas_historico.db").exists()


def test_new_store_at_empty_destination_has_no_alias_or_sales_restriction(existing_account):
    env, _, remote, calls = existing_account
    remote = _inactive(deepcopy(remote))
    proof = _publish(env, [], [remote])
    assert _receive(env)["success"]
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()
    peer = {"link_id": env.link["id"], "sync_direction": "source_to_target",
            "source_client_id": "tenant-a", "source_username": "alice",
            "target_client_id": "tenant-a", "target_username": "bob"}
    result = _apply_sales(env, {"identity": peer}, proof,
                          _sales_bytes(remote["store_id"], remote["nome"]))
    assert result["file_count"] == 1
    assert _read_sales((env.tenant / "vendas_historico.db").read_bytes(),
                       "SELECT store_id FROM vendas") == [(remote["store_id"],)]

    assert calls == []
