"""Directional bindings cannot cross peers or discard referenced local stores."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest
from fastapi import HTTPException

from test_shared_sync_store_reconciliation import bound_account, existing_account, new_machine, seed_binding  # noqa: F401
from backend.services import central_accounts_migration
from backend.services import shared_sync_apply_scope as apply_scope
from backend.services import shared_sync_store_bindings as bindings


CADASTRO = "cadastro_produtos_lojas.csv"


def _run(env, scopes):
    return env.execute(env.preview(scopes), scopes)


def _publish_stores(env, stores):
    payload = json.dumps(stores).encode()
    env.publish(
        "lojas_integracoes", [("lojas_config.json", payload)],
        source_connections=bindings.source_connection_hashes(payload),
    )


def _snapshot(env):
    return {
        name: (env.tenant / name).read_bytes() if (env.tenant / name).exists() else None
        for name in ("lojas_config.json", "lojas_config.json.bak", bindings.BINDINGS_FILE, CADASTRO)
    }


def _ledger(env):
    data = json.loads((env.tenant / bindings.BINDINGS_FILE).read_bytes())
    entry = next(iter(data["peers"].values()))
    return data, entry


def _bind(env):
    assert _run(env, ["lojas_integracoes"])["success"]
    env.calls.clear()
    return _ledger(env)


def _write_ledger(env, ledger):
    (env.tenant / bindings.BINDINGS_FILE).write_text(json.dumps(ledger), encoding="utf-8")


def _expect_unchanged_failure(env, scopes, *, code=None, status=409):
    before = _snapshot(env)
    with pytest.raises(HTTPException) as caught:
        _run(env, scopes)
    assert caught.value.status_code == status
    if code is not None:
        assert caught.value.detail["code"] == code
    assert _snapshot(env) == before
    return caught.value


def _duplicate(env, local, remote, *, backup=True):
    original = json.dumps([local, remote]).encode()
    (env.tenant / "lojas_config.json").write_bytes(original)
    (env.tenant / "lojas_config.json.bak").write_bytes(original)
    if backup:
        path = env.tenant / "_shared_sync_backups" / "lojas_integracoes_20261020_000000" / "lojas_config.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps([local]), encoding="utf-8")
        return path
    return None


@pytest.mark.parametrize("key,value", [
    ("link_id", "different-link"),
    ("sync_direction", "target_to_source"),
    ("source_client_id", "other-source-tenant"),
    ("source_username", "other-source-user"),
    ("target_username", "other-target-user"),
])
def test_binding_is_not_reused_for_another_peer_context(bound_account, key, value):
    env, local, remote, calls = bound_account
    _, entry = _bind(env)
    before = _snapshot(env)
    sources = deepcopy(env.product_files)
    correct = bindings.remap_bound_sources("tenant-a", str(env.tenant), entry["identity"], "cadastro", sources, source_connections=env.source_connections())
    assert pd.read_csv(io.BytesIO(dict(correct)[CADASTRO]), dtype=str)["store_id"].tolist() == [local["store_id"]]
    peer = {**entry["identity"], key: value}
    calls.clear()

    actual = bindings.remap_bound_sources("tenant-a", str(env.tenant), peer, "cadastro", sources, source_connections=env.source_connections())

    assert actual == sources == env.product_files
    assert pd.read_csv(io.BytesIO(dict(actual)[CADASTRO]), dtype=str)["store_id"].tolist() == [remote["store_id"]]
    assert calls == []
    assert _snapshot(env) == before


def test_binding_rejects_different_target_tenant(bound_account):
    env, *_ = bound_account
    _, entry = _bind(env)
    before = _snapshot(env)
    peer = {**entry["identity"], "target_client_id": "foreign-tenant"}
    with pytest.raises(HTTPException) as caught:
        bindings.remap_bound_sources("tenant-a", str(env.tenant), peer, "cadastro", env.product_files, source_connections=env.source_connections())
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "shared_sync_store_binding_invalid"
    assert _snapshot(env) == before


def test_apply_rejects_binding_for_another_recipient(bound_account):
    env, *_ = bound_account
    _, entry = _bind(env)
    before = _snapshot(env)
    peer = {**entry["identity"], "target_username": "other-recipient"}
    with pytest.raises(HTTPException) as caught:
        apply_scope._shared_sync_aplicar_pacote(
            "tenant-a", "cadastro", env.bundles["remote-cadastro"], username="bob",
            scope_config={"store_peer": peer, "user_share": True},
        )
    assert caught.value.status_code == 403
    assert _snapshot(env) == before


def test_new_link_cannot_import_cadastro_using_another_link_binding(bound_account):
    env, *_ = bound_account
    _bind(env)
    env.link["id"] = "new-link-without-binding"
    _expect_unchanged_failure(env, ["cadastro"], code="shared_sync_store_identity_invalid")
    assert [scope for scope, _ in env.calls] == ["cadastro"]


@pytest.mark.parametrize("corruption", [
    "invalid_json", "wrong_schema", "identity_mismatch", "stores_not_mapping",
    "bad_connection_hash", "unknown_provider", "missing_account", "bad_local_id",
    "missing_local_name", "bad_local_name",
])
def test_corrupt_current_binding_rejects_before_cadastro_write(bound_account, corruption):
    env, *_ = bound_account
    ledger, entry = _bind(env)
    record = next(iter(entry["stores"].values()))
    if corruption == "invalid_json":
        (env.tenant / bindings.BINDINGS_FILE).write_bytes(b"{invalid")
    else:
        if corruption == "wrong_schema":
            ledger["schema"] = 999
        elif corruption == "identity_mismatch":
            entry["identity"]["source_username"] = "wrong-participant"
        elif corruption == "stores_not_mapping":
            entry["stores"] = []
        elif corruption == "bad_connection_hash":
            record["connection_hash"] = "not-a-sha256"
        elif corruption == "unknown_provider":
            record["accounts"] = {"unknown-provider": ["account", "site"]}
        elif corruption == "missing_account":
            record["accounts"] = {"mercadolivre": ["", "MLB"]}
        elif corruption == "bad_local_id":
            record["local_id"] = []
        elif corruption == "missing_local_name":
            record.pop("local_name")
        elif corruption == "bad_local_name":
            record["local_name"] = []
        _write_ledger(env, ledger)
    _expect_unchanged_failure(env, ["cadastro"], code="shared_sync_store_binding_invalid")


@pytest.mark.parametrize("scope", ["cadastro", "lojas_integracoes"])
def test_orphan_binding_is_not_used_to_recreate_its_former_target(bound_account, scope):
    env, local, remote, calls = bound_account
    ledger, entry = _bind(env)
    next(iter(entry["stores"].values()))["local_id"] = "missing-local-store"
    _write_ledger(env, ledger)
    if scope == "cadastro":
        _expect_unchanged_failure(env, [scope], code="shared_sync_store_binding_invalid")
    else:
        assert _run(env, [scope])["success"]
        ids = {row["store_id"] for row in json.loads((env.tenant / "lojas_config.json").read_bytes())}
        assert ids == {local["store_id"], remote["store_id"]}
        assert _ledger(env)[1]["stores"] == {}
    assert calls == []


@pytest.mark.parametrize("reference", ["csv", "json", "json_utf16", "photo_path", "sqlite"])
def test_receipt_preserves_references_of_distinct_store_ids(existing_account, reference):
    env, local, remote, _ = existing_account
    backup = _duplicate(env, local, remote)
    backup_before = backup.read_bytes()
    reference_file = None
    if reference == "csv":
        reference_file = env.tenant / "cadastro_custos_lojas.csv"
        reference_file.write_bytes(b"store_id,sku,custo\nstore-a,001,42\n")
    elif reference == "json":
        reference_file = env.tenant / "cadastro_fotos_config.json"
        reference_file.write_text(json.dumps({"store_ids": [remote["store_id"]]}), encoding="utf-8")
    elif reference == "json_utf16":
        reference_file = env.tenant / "local_reference.json"
        reference_file.write_text(json.dumps({"store_id": remote["store_id"]}), encoding="utf-16")
    elif reference == "photo_path":
        segment = "sid-" + hashlib.sha256(remote["store_id"].encode()).hexdigest()
        reference_file = env.tenant / "cadastro_fotos" / segment / "001" / "synthetic.jpg"
        reference_file.parent.mkdir(parents=True)
        reference_file.write_bytes(b"synthetic image")
    else:
        reference_file = env.tenant / "vendas_historico.db"
        with sqlite3.connect(reference_file) as connection:
            connection.execute("CREATE TABLE synthetic_reference (store_id TEXT)")
            connection.execute("INSERT INTO synthetic_reference VALUES (?)", [remote["store_id"]])
    reference_before = reference_file.read_bytes()

    assert _run(env, ["lojas_integracoes"])["success"]
    assert {row["store_id"] for row in json.loads((env.tenant / "lojas_config.json").read_bytes())} == {local["store_id"], remote["store_id"]}

    assert reference_file.read_bytes() == reference_before
    assert backup.read_bytes() == backup_before


def test_duplicate_without_preimport_backup_is_not_removed(existing_account):
    env, local, remote, _ = existing_account
    _duplicate(env, local, remote, backup=False)
    assert _run(env, ["lojas_integracoes"])["success"]
    assert {row["store_id"] for row in json.loads((env.tenant / "lojas_config.json").read_bytes())} == {local["store_id"], remote["store_id"]}


def test_duplicate_edited_after_import_is_not_removed(existing_account):
    env, local, remote, _ = existing_account
    edited = deepcopy(remote)
    edited["user_note"] = "synthetic local edit"
    _duplicate(env, local, edited)
    assert _run(env, ["lojas_integracoes"])["success"]
    assert {row["store_id"] for row in json.loads((env.tenant / "lojas_config.json").read_bytes())} == {local["store_id"], remote["store_id"]}


@pytest.mark.parametrize("existing_binding", [False, True])
def test_partial_binding_write_restores_config_backup_and_ledger(existing_account, monkeypatch, existing_binding):
    env, local, remote, calls = existing_account
    remote = deepcopy(remote)
    if existing_binding:
        ledger, entry = seed_binding(env, local, remote)
        entry["stores"][remote["store_id"]]["local_id"] = "missing-target"
        _write_ledger(env, ledger)
    else:
        # A legacy receipt creates a durable ID, explicitly without OAuth proof.
        remote.pop("store_id")
    _publish_stores(env, [remote, {"store_id": "new-store", "nome": "Other synthetic store", "integracoes": {}}])
    before = _snapshot(env)
    original_write = apply_scope._shared_sync_atomic_write
    attempts = []

    def fail_after_binding_write(path, content):
        original_write(path, content)
        if Path(path).name == bindings.BINDINGS_FILE and not attempts:
            attempts.append(True)
            rows = json.loads((env.tenant / "lojas_config.json").read_bytes())
            assert "new-store" in {row["store_id"] for row in rows}
            assert (env.tenant / bindings.BINDINGS_FILE).read_bytes() != before[bindings.BINDINGS_FILE]
            raise OSError("synthetic binding write failure")

    monkeypatch.setattr(apply_scope, "_shared_sync_atomic_write", fail_after_binding_write)
    with pytest.raises(HTTPException) as caught:
        _run(env, ["lojas_integracoes"])
    assert caught.value.status_code == 500
    assert attempts == [True]
    assert _snapshot(env) == before
    assert calls == []


def test_changed_local_connection_does_not_block_cadastro_historical_id_mapping(bound_account, monkeypatch):
    env, local, _, _ = bound_account
    _bind(env)
    path = env.tenant / "lojas_config.json"
    rows = json.loads(path.read_bytes())
    rows[0]["integracoes"]["mercadolivre"].update(user_id="456", access_token="changed-ml")
    rows[0]["integracoes"]["bling"].update(access_token="changed-bling")
    path.write_text(json.dumps(rows), encoding="utf-8")
    before_connections = path.read_bytes()
    before_map = (env.tenant / bindings.BINDINGS_FILE).read_bytes()
    monkeypatch.setattr(central_accounts_migration, "_identity", lambda *args: pytest.fail("Cadastro must not query a provider"))
    assert _run(env, ["cadastro"])["success"]
    assert pd.read_csv(env.tenant / CADASTRO, dtype=str)["store_id"].tolist() == [local["store_id"]]
    assert path.read_bytes() == before_connections
    assert (env.tenant / bindings.BINDINGS_FILE).read_bytes() == before_map


def test_token_rotation_for_same_account_keeps_binding_usable(bound_account):
    env, local, _, calls = bound_account
    _bind(env)
    path = env.tenant / "lojas_config.json"
    rows = json.loads(path.read_bytes())
    rows[0]["integracoes"]["mercadolivre"]["access_token"] = "rotated-local-ml"
    path.write_text(json.dumps(rows), encoding="utf-8")
    calls.clear()
    before_map = (env.tenant / bindings.BINDINGS_FILE).read_bytes()

    assert _run(env, ["cadastro"])["success"]

    assert calls == []
    assert pd.read_csv(env.tenant / CADASTRO, dtype=str)["store_id"].tolist() == [local["store_id"]]
    assert (env.tenant / bindings.BINDINGS_FILE).read_bytes() == before_map


def test_receipt_keeps_distinct_ids_and_original_backup(existing_account):
    env, local, remote, _ = existing_account
    backup = _duplicate(env, local, remote)
    backup_before = backup.read_bytes()
    assert _run(env, ["lojas_integracoes"])["success"]
    assert {row["store_id"] for row in json.loads((env.tenant / "lojas_config.json").read_bytes())} == {local["store_id"], remote["store_id"]}
    assert backup.read_bytes() == backup_before


@pytest.mark.parametrize("stage", ["prepare", "cadastro"])
def test_tombstone_on_local_binding_target_prevents_reuse(bound_account, stage):
    env, local, remote, calls = bound_account
    _, entry = _bind(env)
    removed_id = local["store_id"]
    tombstones = [{"type": "store", "key": "store:" + removed_id + ":", "store_id": removed_id,
                   "version": 2, "deleted_at": "2026-10-20T00:00:00Z"}]
    path = env.tenant / "lojas_sync_tombstones.json"
    path.write_text(json.dumps(tombstones), encoding="utf-8")
    before = _snapshot(env)
    tombstone_before = path.read_bytes()
    if stage == "prepare":
        plan = bindings.prepare_store_import("tenant-a", str(env.tenant), before["lojas_config.json"],
                                             json.dumps([remote]).encode(), entry["identity"])
        assert plan["mapping"] == {remote["store_id"]: remote["store_id"]}
        assert plan["matched_local_ids"] == []
        assert _snapshot(env) == before
    else:
        _expect_unchanged_failure(env, ["cadastro"], code="shared_sync_store_binding_invalid")
    assert calls == []
    assert path.read_bytes() == tombstone_before


@pytest.mark.parametrize("scope", ["cadastro", "favoritos_planilhas"])
@pytest.mark.parametrize("proof_kind", ["valid", "absent", "different"])
def test_orphan_binding_does_not_block_sources_for_another_store(bound_account, scope, proof_kind):
    env, _, remote, calls = bound_account
    _, entry = _bind(env)
    # The old remote store remains in the peer ledger, but its former local
    # target no longer exists. This package refers only to an independent B.
    other = {"store_id": "store-b", "nome": "Other synthetic store", "integracoes": {}}
    for filename in ("lojas_config.json", "lojas_config.json.bak"):
        (env.tenant / filename).write_text(json.dumps([other]), encoding="utf-8")
    calls.clear()
    before = _snapshot(env)
    proof = env.source_connections() if proof_kind == "valid" else (
        None if proof_kind == "absent" else {remote["store_id"]: "0" * 64}
    )
    if scope == "cadastro":
        product = env.product_files[0][1].replace(b"store-a", b"store-b")
        sources = [(CADASTRO, product), env.product_files[1]]
        env.publish(scope, sources, source_connections=proof)
        assert _run(env, [scope])["success"]
        assert pd.read_csv(env.tenant / CADASTRO, dtype=str)["store_id"].tolist() == ["store-b"]
    else:
        name = "favoritos_planilhas_lojas.json"
        payload = {"planilhas": {"otherstore": {"loja": other["nome"], "url": "https://example.invalid/synthetic"}}}
        sources = [(name, json.dumps(payload).encode())]
        env.publish(scope, sources, source_connections=proof)
        bundle = env.bundles["remote-" + scope]
        result = apply_scope._shared_sync_aplicar_pacote(
            "tenant-a", scope, bundle, username="bob",
            scope_config={"store_peer": entry["identity"], "user_share": True},
        )
        assert result["file_count"] == 1
        assert json.loads((env.tenant / name).read_bytes()) == payload
    after = _snapshot(env)
    for filename in ("lojas_config.json", "lojas_config.json.bak", bindings.BINDINGS_FILE):
        assert after[filename] == before[filename]
    assert calls == []


@pytest.mark.parametrize("same_name", [False, True])
def test_bling_connections_without_prior_binding_do_not_merge_distinct_ids(existing_account, monkeypatch, same_name):
    env, local, remote, _ = existing_account
    local, remote = deepcopy(local), deepcopy(remote)
    local["nome"] = "Local Bling store"
    remote["nome"] = local["nome"] if same_name else "Distinct remote Bling store"
    for row in (local, remote):
        row["integracoes"] = {"bling": row["integracoes"]["bling"]}
    (env.tenant / "lojas_config.json").write_text(json.dumps([local]), encoding="utf-8")
    _publish_stores(env, [remote])
    monkeypatch.setattr(central_accounts_migration, "_identity", lambda *args: pytest.fail("Receipt must not query a provider"))
    assert _run(env, ["lojas_integracoes"])["success"]
    saved = {row["store_id"]: row for row in json.loads((env.tenant / "lojas_config.json").read_bytes())}
    assert set(saved) == {local["store_id"], remote["store_id"]}
    for original in (local, remote):
        for key, value in original["integracoes"]["bling"].items():
            assert saved[original["store_id"]]["integracoes"]["bling"][key] == value
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()


def test_unrelated_connected_stores_are_not_identity_lookup_candidates(existing_account, monkeypatch):
    env, local, remote, _ = existing_account
    local, remote = deepcopy(local), deepcopy(remote)
    other = deepcopy(local)
    other.update(store_id="other-local", nome="Other Bling")
    other["integracoes"]["bling"]["access_token"] = "expired-other-account"
    (env.tenant / "lojas_config.json").write_text(json.dumps([local, other]), encoding="utf-8")
    _publish_stores(env, [remote])
    monkeypatch.setattr(central_accounts_migration, "_identity", lambda *args: pytest.fail("Unrelated account lookup"))
    assert _run(env, ["lojas_integracoes"])["success"]
    assert {row["store_id"] for row in json.loads((env.tenant / "lojas_config.json").read_bytes())} == {local["store_id"], other["store_id"], remote["store_id"]}
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()


@pytest.mark.parametrize("unrelated_orphan", [False, True])
def test_name_only_favorites_follow_current_local_name_after_rename(bound_account, unrelated_orphan):
    env, local, remote, calls = bound_account
    ledger, entry = _bind(env)
    record = entry["stores"][remote["store_id"]]
    assert record["remote_name"] == record["local_name"] == local["nome"]
    new_name = "Renamed local store"
    path = env.tenant / "lojas_config.json"
    rows = json.loads(path.read_bytes())
    original_connections = deepcopy(rows[0]["integracoes"])
    rows[0]["nome"] = new_name
    for filename in ("lojas_config.json", "lojas_config.json.bak"):
        (env.tenant / filename).write_text(json.dumps(rows), encoding="utf-8")
    if unrelated_orphan:
        entry["stores"]["unrelated-remote"] = {
            **deepcopy(record), "local_id": "deleted-unrelated-local",
            "remote_name": "Unrelated old remote", "local_name": "Unrelated old local",
        }
        _write_ledger(env, ledger)
    before = _snapshot(env)
    calls.clear()
    name = "favoritos_planilhas_lojas.json"
    original_name = remote["nome"]
    payload = {"planilhas": {"syntheticstore": {"loja": original_name, "url": "https://example.invalid/keep-this-value"}}}
    env.publish("favoritos_planilhas", [(name, json.dumps(payload).encode())])
    bundle = env.bundles["remote-favoritos_planilhas"]

    result = apply_scope._shared_sync_aplicar_pacote(
        "tenant-a", "favoritos_planilhas", bundle, username="bob",
        scope_config={"store_peer": entry["identity"], "user_share": True},
    )

    assert result["file_count"] == 1
    assert json.loads((env.tenant / name).read_bytes()) == {
        "planilhas": {"renamedlocalstore": {"loja": new_name, "url": "https://example.invalid/keep-this-value"}},
    }
    assert json.loads(path.read_bytes())[0]["integracoes"] == original_connections
    assert _snapshot(env) == before
    assert calls == []


@pytest.mark.parametrize("empty_configs", [
    {},
    {"mercadolivre": {}, "bling": {}},
    {"mercadolivre": {"app_id": "default-app", "client_secret": "synthetic-default"},
     "bling": {"id": "default-bling", "secret": "synthetic-default"}},
])
def test_unrelated_empty_or_app_only_local_configs_do_not_block_new_account(existing_account, empty_configs):
    env, local, remote, calls = existing_account
    local = deepcopy(local)
    local["nome"] = "Unconnected unrelated store"
    local["integracoes"] = deepcopy(empty_configs)
    (env.tenant / "lojas_config.json").write_text(json.dumps([local]), encoding="utf-8")
    assert local["nome"] != remote["nome"]

    assert _run(env, ["lojas_integracoes"])["success"]

    rows = json.loads((env.tenant / "lojas_config.json").read_bytes())
    assert {row["store_id"] for row in rows} == {local["store_id"], remote["store_id"]}
    assert calls == []
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()


@pytest.mark.parametrize("empty_side", ["local", "remote", "both"])
def test_historical_binding_works_despite_empty_connection_configuration(bound_account, empty_side):
    env, local, remote, calls = bound_account
    local, remote = deepcopy(local), deepcopy(remote)
    if empty_side in {"local", "both"}:
        local["integracoes"]["bling"] = {}
    if empty_side in {"remote", "both"}:
        remote["integracoes"]["bling"] = {}
    (env.tenant / "lojas_config.json").write_text(json.dumps([local]), encoding="utf-8")
    _publish_stores(env, [remote])
    before_map = (env.tenant / bindings.BINDINGS_FILE).read_bytes()
    assert _run(env, ["lojas_integracoes"])["success"]
    rows = json.loads((env.tenant / "lojas_config.json").read_bytes())
    assert [row["store_id"] for row in rows] == [local["store_id"]]
    assert rows[0]["integracoes"]["mercadolivre"]["access_token"] == local["integracoes"]["mercadolivre"]["access_token"]
    assert (env.tenant / bindings.BINDINGS_FILE).read_bytes() == before_map
    assert calls == []


def test_same_name_without_account_proof_keeps_distinct_ids(existing_account):
    env, local, remote, calls = existing_account
    local, remote = deepcopy(local), deepcopy(remote)
    for row in (local, remote):
        row["integracoes"] = {"mercadolivre": {}, "bling": {"id": "only-app-id"}}
    (env.tenant / "lojas_config.json").write_text(json.dumps([local]), encoding="utf-8")
    _publish_stores(env, [remote])
    assert _run(env, ["lojas_integracoes"])["success"]
    assert {row["store_id"] for row in json.loads((env.tenant / "lojas_config.json").read_bytes())} == {local["store_id"], remote["store_id"]}
    assert calls == []


@pytest.mark.parametrize("proof_kind", ["absent", "missing_id", "different"])
@pytest.mark.parametrize("scope", ["cadastro", "favoritos_planilhas"])
def test_connection_proof_is_required_only_outside_cadastro(bound_account, proof_kind, scope):
    env, local, remote, calls = bound_account
    _, entry = _bind(env)
    proof = None if proof_kind == "absent" else (
        {} if proof_kind == "missing_id" else {remote["store_id"]: "0" * 64}
    )
    calls.clear()
    if scope == "cadastro":
        env.publish(scope, env.product_files, source_connections=proof)
        assert _run(env, [scope])["success"]
        assert pd.read_csv(env.tenant / CADASTRO, dtype=str)["store_id"].tolist() == [local["store_id"]]
    else:
        # A rename makes the name-only reference an effective projection too.
        rows = json.loads((env.tenant / "lojas_config.json").read_bytes())
        rows[0]["nome"] = "Current local name"
        for filename in ("lojas_config.json", "lojas_config.json.bak"):
            (env.tenant / filename).write_text(json.dumps(rows), encoding="utf-8")
        name = "favoritos_planilhas_lojas.json"
        payload = {"planilhas": {"syntheticstore": {"loja": local["nome"], "url": "https://example.invalid/synthetic"}}}
        env.publish(scope, [(name, json.dumps(payload).encode())], source_connections=proof)
        before = _snapshot(env)
        with pytest.raises(HTTPException) as caught:
            apply_scope._shared_sync_aplicar_pacote(
                "tenant-a", scope, env.bundles["remote-" + scope], username="bob",
                scope_config={"store_peer": entry["identity"], "user_share": True},
            )
        assert caught.value.status_code == 409
        assert caught.value.detail["code"] == "shared_sync_store_binding_stale"
        assert _snapshot(env) == before
        assert not (env.tenant / name).exists()
    assert calls == []


def test_historical_binding_without_remote_proof_still_serves_cadastro(bound_account):
    env, local, _, calls = bound_account
    ledger, entry = _bind(env)
    next(iter(entry["stores"].values())).pop("remote_connection_hash")
    _write_ledger(env, ledger)
    assert _run(env, ["cadastro"])["success"]
    assert pd.read_csv(env.tenant / CADASTRO, dtype=str)["store_id"].tolist() == [local["store_id"]]
    assert calls == []


@pytest.mark.parametrize("tamper", ["wrong_hash", "omitted_store", "extra_store"])
def test_store_manifest_proof_must_match_original_store_bytes(existing_account, tamper):
    env, _, remote, calls = existing_account
    proof = env.source_connections()
    if tamper == "wrong_hash":
        proof[remote["store_id"]] = "0" * 64
    elif tamper == "omitted_store":
        proof = {}
    else:
        proof["undeclared-store"] = "1" * 64
    env.publish(
        "lojas_integracoes", [("lojas_config.json", json.dumps([remote]).encode())],
        source_connections=proof,
    )

    _expect_unchanged_failure(env, ["lojas_integracoes"], code="shared_sync_store_binding_stale")

    assert calls == []


def test_reconnected_remote_store_keeps_cadastro_id_mapping_without_rewriting_proof(bound_account):
    env, local, remote, calls = bound_account
    _bind(env)
    original_map = (env.tenant / bindings.BINDINGS_FILE).read_bytes()
    remote["integracoes"]["mercadolivre"].update(user_id="456", access_token="reconnected-ml")
    remote["integracoes"]["bling"].update(access_token="reconnected-bling")
    env.publish("cadastro", env.product_files)
    assert _run(env, ["cadastro"])["success"]
    assert pd.read_csv(env.tenant / CADASTRO, dtype=str)["store_id"].tolist() == [local["store_id"]]
    assert (env.tenant / bindings.BINDINGS_FILE).read_bytes() == original_map
    assert calls == []
