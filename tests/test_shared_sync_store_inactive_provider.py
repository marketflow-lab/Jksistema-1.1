"""An inactive optional connection never substitutes for a proven account."""
from copy import deepcopy
import json
import sqlite3

import pandas as pd
import pytest
from fastapi import HTTPException

from test_shared_sync_store_reconciliation import existing_account, new_machine, store  # noqa: F401
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


@pytest.mark.parametrize("side", ["local", "remote", "both"])
@pytest.mark.parametrize("provider", ["bling", "mercadolivre"])
def test_other_authenticated_account_receives_stores_and_cadastro_without_changing_connections(existing_account, side, provider):
    env, local, remote, calls = existing_account
    local, remote = deepcopy(local), deepcopy(remote)
    local["nome"], remote["nome"] = "Destination name", "Source name"
    if side in {"local", "both"}:
        _inactive(local, provider)
    if side in {"remote", "both"}:
        _inactive(remote, provider)
    _publish(env, [local], [remote])
    bundles = deepcopy(env.bundles)

    assert _receive(env)["success"]

    rows = json.loads((env.tenant / "lojas_config.json").read_bytes())
    assert [row["store_id"] for row in rows] == [local["store_id"]]
    assert rows[0]["nome"] == local["nome"]
    for current_provider, config in local["integracoes"].items():
        assert {key: rows[0]["integracoes"][current_provider][key] for key in config} == config
    frame = pd.read_csv(env.tenant / CADASTRO, dtype=str)
    assert frame[["store_id", "sku"]].to_dict("records") == [{"store_id": local["store_id"], "sku": "001"}]
    other = "mercadolivre" if provider == "bling" else "bling"
    assert (other, local["integracoes"][other]["access_token"]) in calls
    assert (other, remote["integracoes"][other]["access_token"]) in calls
    for name, row in (("local", local), ("remote", remote)):
        if side in {name, "both"}:
            assert (provider, row["integracoes"][provider]["access_token"]) not in calls
    assert env.bundles == bundles
    before = _snapshot(env)
    calls.clear()
    assert _receive(env, ["cadastro"])["success"]
    assert _snapshot(env) == before
    assert calls == []


def test_single_remote_store_at_empty_destination_requires_other_fresh_proof(existing_account):
    env, _, remote, calls = existing_account
    remote = _inactive(deepcopy(remote))
    _publish(env, [], [remote])
    bundles = deepcopy(env.bundles)

    assert _receive(env)["success"]

    rows = json.loads((env.tenant / "lojas_config.json").read_bytes())
    assert [row["store_id"] for row in rows] == [remote["store_id"]]
    for provider, config in remote["integracoes"].items():
        assert {key: rows[0]["integracoes"][provider][key] for key in config} == config
    assert rows[0]["integracoes"]["bling"]["connected"] is False
    assert calls == [("mercadolivre", remote["integracoes"]["mercadolivre"]["access_token"])]
    assert pd.read_csv(env.tenant / CADASTRO, dtype=str)["store_id"].tolist() == [remote["store_id"]]
    assert env.bundles == bundles


@pytest.mark.parametrize("other", ["absent", "empty", "invalid", "missing_token", "unavailable"])
@pytest.mark.parametrize("invalid_has_tokens", [False, True])
def test_single_remote_optional_connection_cannot_import_without_another_authenticated_account(existing_account, monkeypatch, other, invalid_has_tokens):
    env, _, remote, _ = existing_account
    remote = _inactive(deepcopy(remote))
    if not invalid_has_tokens:
        remote["integracoes"]["bling"] = {"connected": False, "oauth_invalid": True}
    if other == "absent":
        remote["integracoes"].pop("mercadolivre")
    elif other == "empty":
        remote["integracoes"]["mercadolivre"] = {}
    elif other == "invalid":
        _inactive(remote, "mercadolivre")
    elif other == "missing_token":
        remote["integracoes"]["mercadolivre"].pop("access_token")
    else:
        def unavailable(*args):
            raise HTTPException(401, "Synthetic expired token")
        monkeypatch.setattr(central_accounts_migration, "_identity", unavailable)
    _publish(env, [], [remote])
    _reject(env)


@pytest.mark.parametrize("claim", ["user_id", "seller_id", "account_id", "empresa_id"])
@pytest.mark.parametrize("side", ["local", "remote"])
def test_invalid_connection_with_declared_identity_cannot_be_ignored(existing_account, claim, side):
    env, local, remote, _ = existing_account
    local, remote = deepcopy(local), deepcopy(remote)
    row = local if side == "local" else remote
    _inactive(row)["integracoes"]["bling"][claim] = "declared-account"
    _publish(env, [local], [remote])
    _reject(env)


@pytest.mark.parametrize("claim", [0, False, "invalid/account"])
def test_falsy_or_malformed_account_claim_is_not_treated_as_missing(existing_account, claim):
    env, local, remote, _ = existing_account
    remote = _inactive(deepcopy(remote))
    remote["integracoes"]["bling"]["account_id"] = claim
    _publish(env, [local], [remote])
    _reject(env)


@pytest.mark.parametrize("flags", [
    {"connected": False}, {"connected": False, "oauth_invalid": False},
    {"connected": False, "oauth_invalid": "true"}, {"connected": True, "oauth_invalid": True},
])
def test_missing_active_token_or_unconfirmed_invalid_state_is_still_rejected(existing_account, flags):
    env, local, remote, _ = existing_account
    remote = deepcopy(remote)
    remote["integracoes"]["bling"].update(flags)
    remote["integracoes"]["bling"].pop("access_token")
    _publish(env, [local], [remote])
    _reject(env)


def test_optional_inactive_provider_keeps_candidate_with_no_common_authenticated_platform_blocked(existing_account):
    env, local, remote, _ = existing_account
    local, remote = deepcopy(local), _inactive(deepcopy(remote))
    local["nome"], remote["nome"] = "Bling destination", "ML source"
    local["integracoes"].pop("mercadolivre")
    _publish(env, [local], [remote])
    _reject(env)


@pytest.mark.parametrize("conflict", ["account", "site"])
def test_optional_provider_cannot_hide_active_identity_mismatch(existing_account, monkeypatch, conflict):
    env, local, remote, _ = existing_account
    local, remote = _inactive(deepcopy(local)), _inactive(deepcopy(remote))
    _publish(env, [local], [remote])

    def wrong_identity(provider, token):
        return ("different-account", "MLB") if conflict == "account" else ("123", "MLA")

    monkeypatch.setattr(central_accounts_migration, "_identity", wrong_identity)
    _reject(env, code="shared_sync_store_account_mismatch")


def test_optional_provider_does_not_hide_duplicate_remote_active_accounts(existing_account):
    env, _, remote, _ = existing_account
    first = _inactive(deepcopy(remote))
    second = _inactive(store("another-remote", "second"))
    second["nome"] = "Different display name"
    _publish(env, [], [first, second])
    error = _reject(env, code="shared_sync_store_reconciliation_ambiguous")
    assert error.detail["reason"] == "duplicate_remote_account"


@pytest.mark.parametrize("side", ["local", "remote"])
@pytest.mark.parametrize("change", ["invalid_flag", "declared_account"])
def test_optional_account_proof_is_not_reused_after_only_its_state_changes(existing_account, side, change):
    env, local, remote, _ = existing_account
    local, remote = _inactive(deepcopy(local)), _inactive(deepcopy(remote))
    proof = _publish(env, [local], [remote])
    assert _receive(env)["success"]
    entry = next(iter(json.loads((env.tenant / bindings.BINDINGS_FILE).read_bytes())["peers"].values()))
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
    with pytest.raises(HTTPException) as caught:
        bindings.remap_bound_sources("tenant-a", str(env.tenant), entry["identity"], "cadastro",
                                    env.product_files, source_connections=proof)
    expected = "shared_sync_store_account_unverifiable" if side == "local" else "shared_sync_store_binding_stale"
    assert caught.value.detail["code"] == expected
    assert _snapshot(env) == before


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


def test_legacy_source_proof_cannot_reuse_optional_account_binding(existing_account):
    env, local, remote, calls = existing_account
    local, remote = _inactive(deepcopy(local)), _inactive(deepcopy(remote))
    _publish(env, [local], [remote])
    assert _receive(env)["success"]
    entry = next(iter(json.loads((env.tenant / bindings.BINDINGS_FILE).read_bytes())["peers"].values()))
    legacy_remote = deepcopy(remote)
    legacy_remote["integracoes"]["bling"].pop("oauth_invalid")
    legacy_proof = {remote["store_id"]: bindings._connection_hash(legacy_remote)}
    before = _snapshot(env)
    calls.clear()
    with pytest.raises(HTTPException) as caught:
        bindings.remap_bound_sources("tenant-a", str(env.tenant), entry["identity"], "cadastro",
                                    env.product_files, source_connections=legacy_proof)
    assert caught.value.detail["code"] == "shared_sync_store_binding_stale"
    assert calls == []
    assert _snapshot(env) == before


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
    assert _receive(env)["success"]  # Lojas and Cadastro remain permitted.
    _, entry = _entry(env)
    record = entry["stores"][remote["store_id"]]
    assert record["unverified_providers"] == ["bling"]
    if side == "remote":
        assert "bling" in record["accounts"]  # Local proof is not source proof.
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
    assert "Reconecte o Bling" in caught.value.detail["message"]
    assert {str(path.relative_to(env.tenant)): path.read_bytes()
            for path in env.tenant.rglob("*") if path.is_file()} == before
    assert _read_sales(source, "SELECT id_unico FROM vendas") == [("remote-sale",)]
    assert calls == []


@pytest.mark.parametrize("legacy", [False, True])
def test_normal_existing_alias_without_optional_provider_still_imports_sales(existing_account, legacy):
    env, local, remote, _ = existing_account
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


def test_unverified_bling_does_not_block_sales_without_reference_to_that_alias(existing_account):
    env, local, remote, _ = existing_account
    remote = _inactive(deepcopy(remote))
    proof = _publish(env, [local], [remote])
    assert _receive(env)["success"]
    _, entry = _entry(env)
    sources = [("vendas_historico.db", _sales_bytes("unrelated-id", "Unrelated name"))]
    assert bindings.remap_bound_sources("tenant-a", str(env.tenant), entry["identity"], "vendas",
                                       sources, source_connections=proof) == sources


@pytest.mark.parametrize("invalid", [None, False, "bling", [], ["unknown"], ["bling", "bling"],
                                     ["mercadolivre", "bling"], [{}]])
def test_invalid_optional_provider_marker_rejects_ledger_before_sales_write(existing_account, invalid):
    env, local, remote, _ = existing_account
    proof = _publish(env, [local], [_inactive(deepcopy(remote))])
    assert _receive(env)["success"]
    ledger, entry = _entry(env)
    entry["stores"][remote["store_id"]]["unverified_providers"] = invalid
    (env.tenant / bindings.BINDINGS_FILE).write_text(json.dumps(ledger), encoding="utf-8")
    before = _snapshot(env)

    with pytest.raises(HTTPException) as caught:
        _apply_sales(env, entry, proof, _sales_bytes(remote["store_id"], remote["nome"]))

    assert caught.value.detail["code"] == "shared_sync_store_binding_invalid"
    assert _snapshot(env) == before
    assert not (env.tenant / "vendas_historico.db").exists()


@pytest.mark.parametrize("side", ["local", "remote", "both"])
def test_new_successful_bling_verification_clears_sales_restriction(existing_account, side):
    env, local, remote, calls = existing_account
    local, remote = deepcopy(local), deepcopy(remote)
    if side in {"local", "both"}:
        _inactive(local)
    if side in {"remote", "both"}:
        _inactive(remote)
    _publish(env, [local], [remote])
    assert _receive(env)["success"]
    assert _entry(env)[1]["stores"][remote["store_id"]]["unverified_providers"] == ["bling"]
    for row in (local, remote):
        row["integracoes"]["bling"].update(connected=True, oauth_invalid=False)
    proof = _publish(env, [local], [remote])
    calls.clear()

    assert _receive(env, ["lojas_integracoes"])["success"]

    _, entry = _entry(env)
    assert "unverified_providers" not in entry["stores"][remote["store_id"]]
    assert ("bling", local["integracoes"]["bling"]["access_token"]) in calls
    assert ("bling", remote["integracoes"]["bling"]["access_token"]) in calls
    result = _apply_sales(env, entry, proof, _sales_bytes(remote["store_id"], remote["nome"]))
    assert result["file_count"] == 1
    assert _read_sales((env.tenant / "vendas_historico.db").read_bytes(),
                       "SELECT store_id FROM vendas") == [(local["store_id"],)]


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
    assert _receive(env)["success"]
    for name, row in (("local", local), ("remote", remote)):
        if side in {name, "both"}:
            if replacement == "remove":
                row["integracoes"].pop("bling")
            else:
                row["integracoes"]["bling"] = {}
    proof = _publish(env, [local], [remote])
    calls.clear()

    assert _receive(env, ["lojas_integracoes"])["success"]

    _, entry = _entry(env)
    assert entry["stores"][remote["store_id"]]["unverified_providers"] == ["bling"]
    for name in ("local", "remote"):
        if side in {name, "both"}:
            assert ("bling", name + "-bling") not in calls
    before = _snapshot(env)
    with pytest.raises(HTTPException) as caught:
        _apply_sales(env, entry, proof, _sales_bytes(remote["store_id"], remote["nome"]))
    assert caught.value.detail["code"] == "shared_sync_store_sales_account_unverified"
    assert _snapshot(env) == before
    assert not (env.tenant / "vendas_historico.db").exists()


def test_new_store_at_empty_destination_has_no_alias_or_sales_restriction(existing_account):
    env, _, remote, _ = existing_account
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
