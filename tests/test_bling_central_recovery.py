"""Old snapshots cannot restore a local renewal authority after Bling migration."""
from __future__ import annotations

import copy
import json

import pytest

from backend.services import shared_sync_merge_integracoes as merge


CENTRAL = {
    "central": True, "central_migrated": True, "central_scope": "provider",
    "connected": True, "user_id": "company-a", "site_id": "",
    "migration_id": "migration-a", "updated_at": "10", "_sync_version": 2,
}
OLD = {
    "id": "fixture-app", "secret": "fixture-secret", "access_token": "fixture-old-access",
    "refresh_token": "fixture-old-refresh", "oauth_connection_id": "fixture-old-connection",
    "connected": True, "updated_at": "99999", "_sync_version": 100,
}


def store(bling, **extra):
    return {"store_id": "a" * 32, "nome": "Shop", "integracoes": {
        "bling": copy.deepcopy(bling), **copy.deepcopy(extra),
    }}


def encoded(value):
    return json.dumps(value).encode()


@pytest.mark.parametrize("flag", ["central", "central_migrated"])
@pytest.mark.parametrize("preserve", [False, True])
@pytest.mark.parametrize("strict", [False, True])
def test_old_oauth_never_replaces_migrated_connection(flag, preserve, strict):
    local = {**CENTRAL, "central": False, "central_migrated": False, flag: True}
    before = copy.deepcopy(local)
    actual = merge._shared_sync_merge_integracao_loja(
        local, copy.deepcopy(OLD), servico_key="bling", base=copy.deepcopy(local),
        preserve_local_connections=preserve, strict_oauth_conflicts=strict,
    )
    assert actual == before
    assert local == before


def test_migrated_disconnect_cannot_be_reconnected_by_newer_remote_token():
    local = {**CENTRAL, "connected": False}
    result = merge._shared_sync_merge_loja_integracoes_authoritative(store(local), store(OLD))
    assert result["integracoes"]["bling"] == local


def test_authoritative_snapshot_preserves_bling_and_updates_other_integration():
    ml = {"access_token": "ml-current", "refresh_token": "ml-refresh", "user_id": "seller-a",
          "app_id": "ml-app", "client_secret": "ml-secret", "_sync_version": 1}
    newer_ml = {**ml, "access_token": "ml-new", "_sync_version": 2}
    local = store(CENTRAL, mercadolivre=ml, mercadoturbo={"token": "turbo-local"})
    result = merge._shared_sync_merge_loja_integracoes_authoritative(
        local, store(OLD, mercadolivre=newer_ml),
    )
    assert result["integracoes"]["bling"] == CENTRAL
    assert result["integracoes"]["mercadolivre"]["access_token"] == "ml-new"
    assert result["integracoes"]["mercadoturbo"] == {"token": "turbo-local"}
    assert local["integracoes"]["mercadolivre"] == ml


@pytest.mark.parametrize("preserve", [False, True])
def test_store_snapshot_keeps_central_authority_without_touching_other_providers(tmp_path, preserve):
    local = store(CENTRAL, mercadolivre={"access_token": "ml-current"},
                  mercadoturbo={"token": "turbo-local"})
    result = json.loads(merge._shared_sync_merge_lojas_integracoes_bytes(
        str(tmp_path / "lojas_config.json"), encoded([store(OLD)]),
        current_bytes=encoded([local]), preserve_local_connections=preserve,
    ))
    assert result == [local]


def test_additive_backup_recovery_preserves_migrated_config_and_recovers_missing_ml():
    local = store(CENTRAL)
    result = json.loads(merge._shared_sync_recuperar_backup_lojas_integracoes_bytes(
        encoded([local]), encoded([store(OLD, mercadolivre={"access_token": "ml-backup"})]),
    ))
    assert result[0]["integracoes"]["bling"] == CENTRAL
    assert result[0]["integracoes"]["mercadolivre"] == {"access_token": "ml-backup"}
    assert local["integracoes"] == {"bling": CENTRAL}


@pytest.mark.parametrize("source", ["normalizer", "new_snapshot", "backup", "preservation", "legacy"])
def test_central_references_cannot_carry_residual_provider_secrets(tmp_path, source):
    public = copy.deepcopy(CENTRAL)
    contaminated = {**public, "access_token": "residual-access", "refresh_token": "residual-refresh",
                    "secret": "residual-secret", "api_key": "residual-api-key",
                    "oauth_draft": {"secret": "draft-secret"}}
    if source == "normalizer":
        config = merge._shared_sync_normalizar_integracao_conectada("bling", contaminated)
    elif source == "new_snapshot":
        rows = json.loads(merge._shared_sync_merge_lojas_integracoes_bytes(
            str(tmp_path / "lojas_config.json"), encoded([store(contaminated)]), current_bytes=b"[]",
        ))
        config = rows[0]["integracoes"]["bling"]
    elif source == "backup":
        rows = json.loads(merge._shared_sync_recuperar_backup_lojas_integracoes_bytes(
            b"[]", encoded([store(contaminated)]),
        ))
        config = rows[0]["integracoes"]["bling"]
    elif source == "preservation":
        rows = json.loads(merge._shared_sync_preserve_connection_blocks(
            encoded([store(contaminated)]), encoded([store(OLD)]),
        ))
        config = rows[0]["integracoes"]["bling"]
    else:
        rows = json.loads(merge._shared_sync_merge_integracoes_legacy_bytes(
            str(tmp_path / "integracoes.json"), encoded({"Shop": {"bling": contaminated}}),
        ))
        config = rows["Shop"]["bling"]
    assert config == public
    assert contaminated["refresh_token"] == "residual-refresh"


@pytest.mark.parametrize("strict", [False, True])
def test_legacy_snapshot_cannot_reintroduce_old_bling_oauth(tmp_path, strict):
    target = tmp_path / "integracoes.json"
    local = {"Shop": {"bling": CENTRAL, "mercadoturbo": {"token": "turbo-local"}}}
    target.write_bytes(encoded(local))
    actual = json.loads(merge._shared_sync_merge_integracoes_legacy_bytes(
        str(target), encoded({"Shop": {"bling": OLD}}), strict_oauth_conflicts=strict,
    ))
    assert actual == local
    assert json.loads(target.read_bytes()) == local


def test_receipt_accepts_central_authority_in_place_of_obsolete_remote_credentials():
    assert merge._shared_sync_integracao_remota_presente(store(CENTRAL), "bling", OLD)
    assert merge._shared_sync_integracoes_legacy_preserva_oauth_completo(
        {"Shop": {"bling": CENTRAL}}, {"Shop": {"bling": OLD}},
    )


@pytest.mark.parametrize("provider", ["mercadolivre", "mercadoturbo"])
def test_central_bling_sanitization_does_not_strip_other_integrations(provider):
    config = {"central_migrated": True, "access_token": "other-provider-access",
              "refresh_token": "other-provider-refresh", "token": "other-provider-token"}
    actual = merge._shared_sync_normalizar_integracao_conectada(provider, config)
    assert actual["access_token"] == config["access_token"]
    assert actual["refresh_token"] == config["refresh_token"]
    assert actual["token"] == config["token"]


@pytest.mark.parametrize("damage", ["corrupt_main", "missing_main", "stale_backup"])
def test_index_prevents_recovery_from_reactivating_local_refresh_authority(tmp_path, damage):
    from backend.services import central_accounts_client as client
    from backend.services import central_accounts_store_index as index
    from backend.services import integracoes

    info = tmp_path / "info"
    tenant = info / "tenant-a"
    tenant.mkdir(parents=True)
    integracoes.configure_integracoes_context(
        pasta_info=str(info), get_tenant_path=lambda name: str(info / name),
        normalizar_integracao_conectada=lambda _provider, cfg: cfg,
    )
    token = client._current.set(None)
    try:
        ml = {"app_id": "ml-app", "client_secret": "ml-secret", "access_token": "ml-access",
              "refresh_token": "ml-refresh", "user_id": "seller-a", "site_id": "MLB"}
        local = store(OLD, mercadolivre=ml, mercadoturbo={"token": "turbo-local"})
        integracoes.salvar_lojas("tenant-a", [copy.deepcopy(local)])
        integracoes.salvar_lojas("tenant-a", [copy.deepcopy(local)])
        path = tenant / "lojas_config.json"
        backup = tenant / "lojas_config.json.bak"
        saved_before = json.loads(path.read_bytes())[0]["integracoes"]
        expected_ml = saved_before["mercadolivre"]
        expected_turbo = saved_before["mercadoturbo"]
        public = {"store_id": local["store_id"], "nome": local["nome"],
                  "owner_client_id": "tenant-a", "access": "owner", "integracoes": {
                      "bling": {"central": True, "connected": True, "user_id": "company-a", "site_id": ""}}}

        index.materialize_store_index("tenant-a", [public], providers=["bling"])
        if damage == "stale_backup":
            # A successful same-state login also repairs an older backup left
            # by interruption, even when the main configuration needs no write.
            backup.write_bytes(encoded([local]))
            index.materialize_store_index("tenant-a", [public], providers=["bling"])
        mirrored = json.loads(backup.read_bytes())[0]
        assert mirrored["integracoes"]["bling"]["central_migrated"]
        assert not mirrored["integracoes"]["bling"].get("refresh_token")
        assert mirrored["integracoes"]["mercadolivre"] == expected_ml
        if damage == "missing_main":
            path.unlink()
        else:
            path.write_text("{invalid", encoding="utf-8")

        recovered = integracoes.carregar_lojas("tenant-a")[0]
        cfg = recovered["integracoes"]["bling"]
        assert cfg["central_migrated"] and not cfg["connected"]
        assert not cfg.get("refresh_token") and not cfg.get("access_token")
        def operational(config):
            return {key: value for key, value in config.items() if not key.startswith("_sync_")}
        assert operational(recovered["integracoes"]["mercadolivre"]) == operational(expected_ml)
        assert operational(recovered["integracoes"]["mercadoturbo"]) == operational(expected_turbo)
        assert "fixture-old-refresh" not in path.read_text(encoding="utf-8")
    finally:
        client._current.reset(token)
