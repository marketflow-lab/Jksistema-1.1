"""Receipt does not query identities or renew historical sales evidence."""
from copy import deepcopy
import json

import pandas as pd
import pytest
from fastapi import HTTPException

from test_shared_sync_store_reconciliation import (  # noqa: F401
    bound_account, existing_account, new_machine, seed_binding, store,
)
from test_shared_sync_store_inactive_provider import (
    CADASTRO, _apply_sales, _entry, _publish, _receive, _sales_bytes, _snapshot,
)
from backend.services import central_accounts_migration as migration
from backend.services import shared_sync_store_bindings as bindings


@pytest.mark.parametrize("historical_alias", [False, True])
@pytest.mark.parametrize("failure", [401, 429, 503, 502, "unexpected"])
def test_receipt_never_calls_unavailable_identity_services(existing_account, monkeypatch, historical_alias, failure):
    env, local, remote, _ = existing_account
    if historical_alias:
        seed_binding(env, local, remote)
    before_ledger = (env.tenant / bindings.BINDINGS_FILE).read_bytes() if historical_alias else None
    calls = []

    def unavailable(*args):
        calls.append(args)
        if failure == "unexpected":
            raise ValueError("synthetic malformed response")
        raise HTTPException(failure, "synthetic unavailable account")

    monkeypatch.setattr(migration, "_identity", unavailable)
    bundles = deepcopy(env.bundles)
    assert _receive(env)["success"]
    rows = {row["store_id"]: row for row in json.loads((env.tenant / "lojas_config.json").read_bytes())}
    assert set(rows) == ({local["store_id"]} if historical_alias else {local["store_id"], remote["store_id"]})
    for provider, original in local["integracoes"].items():
        assert {key: rows[local["store_id"]]["integracoes"][provider][key] for key in original} == original
    expected_id = local["store_id"] if historical_alias else remote["store_id"]
    assert pd.read_csv(env.tenant / CADASTRO, dtype=str)[["store_id", "sku"]].to_dict("records") == [
        {"store_id": expected_id, "sku": "001"},
    ]
    assert calls == []
    assert env.bundles == bundles
    if historical_alias:
        assert (env.tenant / bindings.BINDINGS_FILE).read_bytes() == before_ledger
    else:
        assert not (env.tenant / bindings.BINDINGS_FILE).exists()


@pytest.mark.parametrize("source_proof", [None, {}, {"store-a": "b" * 64}])
def test_cadastro_ignores_connection_proof_and_unavailable_rotated_local_tokens(bound_account, monkeypatch, source_proof):
    env, local, remote, _ = bound_account
    local = deepcopy(local)
    local["integracoes"]["bling"]["access_token"] = "local-rotated-bling"
    local["integracoes"]["mercadolivre"]["access_token"] = "local-rotated-ml"
    path = env.tenant / "lojas_config.json"
    path.write_text(json.dumps([local]), encoding="utf-8")
    config, ledger = path.read_bytes(), (env.tenant / bindings.BINDINGS_FILE).read_bytes()
    env.publish("cadastro", env.product_files, source_connections=source_proof)
    monkeypatch.setattr(migration, "_identity", lambda *args: pytest.fail("Cadastro must not query OAuth"))

    assert _receive(env, ["cadastro"])["success"]

    assert path.read_bytes() == config
    assert (env.tenant / bindings.BINDINGS_FILE).read_bytes() == ledger
    assert pd.read_csv(env.tenant / CADASTRO, dtype=str)["store_id"].tolist() == [local["store_id"]]


@pytest.mark.parametrize("legacy", [False, True])
def test_historical_unverified_bling_blocks_sales_before_any_writes(existing_account, legacy):
    env, local, remote, calls = existing_account
    proof = _publish(env, [local], [remote])
    seed_binding(env, local, remote, unverified=["bling"], accounts={"mercadolivre": ["123", "MLB"]})
    assert _receive(env)["success"]
    _, entry = _entry(env)
    current = _sales_bytes(local["store_id"], local["nome"], legacy=legacy, sale_id="local-sale")
    (env.tenant / "vendas_historico.db").write_bytes(current)
    before = {path.relative_to(env.tenant): path.read_bytes() for path in env.tenant.rglob("*") if path.is_file()}

    with pytest.raises(HTTPException) as caught:
        _apply_sales(env, entry, proof, _sales_bytes(remote["store_id"], remote["nome"], legacy=legacy),
                     [("produtos_compilado.csv", b"store_id,sku\nstore-a,001\n")])

    assert caught.value.detail["code"] == "shared_sync_store_sales_account_unverified"
    assert {path.relative_to(env.tenant): path.read_bytes() for path in env.tenant.rglob("*") if path.is_file()} == before
    assert calls == []


@pytest.mark.parametrize("change", ["remote_token", "local_token"])
def test_receipt_does_not_renew_sales_proof_after_connection_changes(bound_account, monkeypatch, change):
    env, local, remote, _ = bound_account
    local, remote = deepcopy(local), deepcopy(remote)
    changed = remote if change == "remote_token" else local
    changed["integracoes"]["bling"]["access_token"] = "reconnected-bling"
    proof = _publish(env, [local], [remote])
    before_ledger = (env.tenant / bindings.BINDINGS_FILE).read_bytes()
    calls = []

    def identity(provider, token):
        calls.append((provider, token))
        raise HTTPException(503, "synthetic unavailable connection")

    monkeypatch.setattr(migration, "_identity", identity)
    assert _receive(env)["success"]
    assert calls == []
    assert (env.tenant / bindings.BINDINGS_FILE).read_bytes() == before_ledger
    _, entry = _entry(env)
    before = _snapshot(env)

    with pytest.raises(HTTPException) as caught:
        _apply_sales(env, entry, proof, _sales_bytes(remote["store_id"], remote["nome"]))

    expected = "shared_sync_store_binding_stale" if change == "remote_token" else "shared_sync_store_account_unverifiable"
    assert caught.value.detail["code"] == expected
    assert bool(calls) is (change == "local_token")
    assert _snapshot(env) == before
    assert not (env.tenant / "vendas_historico.db").exists()


@pytest.mark.parametrize("has_local", [False, True])
def test_same_unavailable_company_claim_does_not_merge_distinct_ids(existing_account, monkeypatch, has_local):
    env, local, remote, calls = existing_account
    local, remote = deepcopy(local), deepcopy(remote)
    remote["integracoes"]["mercadolivre"]["user_id"] = "456"
    for row in (local, remote):
        row["integracoes"]["bling"].update(empresa_id="shared-company", connected=False, oauth_invalid=True)
    if has_local:
        _publish(env, [local], [remote])
    else:
        _publish(env, [], [local, remote])
    monkeypatch.setattr(migration, "_identity", lambda *args: pytest.fail("receipt must not query OAuth"))

    assert _receive(env)["success"]

    rows = {row["store_id"]: row for row in json.loads((env.tenant / "lojas_config.json").read_bytes())}
    assert set(rows) == {local["store_id"], remote["store_id"]}
    assert rows[local["store_id"]]["integracoes"]["mercadolivre"]["user_id"] == "123"
    assert rows[remote["store_id"]]["integracoes"]["mercadolivre"]["user_id"] == "456"
    assert pd.read_csv(env.tenant / CADASTRO, dtype=str)["store_id"].tolist() == [remote["store_id"]]
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()
    assert calls == []
