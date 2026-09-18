from __future__ import annotations

import copy

from fastapi import HTTPException
import pytest

from backend.services import perguntas_store_config as config


def _store(identity="StoreA", name="Loja A", seller="seller-a", site="MLB"):
    return {"store_id": identity, "nome": name,
            "integracoes": {"mercadolivre": {
                "user_id": seller, "site_id": site, "access_token": "fixture-access",
                "id": "fixture-app", "secret": "fixture-secret",
                "shared_without_oauth_tokens": True,
            }}}


@pytest.fixture
def rows(monkeypatch):
    values = {"tenant-a": [_store()], "tenant-b": [_store("StoreB", "Loja B", "seller-b")]}
    monkeypatch.setattr(config.integracoes, "ler_lojas", lambda tenant: values[tenant])
    def canonical_forbidden(*_args, **_kwargs):
        pytest.fail("Question credentials must not enter the canonical writer path")
    monkeypatch.setattr(config.integracoes, "carregar_lojas", canonical_forbidden)
    monkeypatch.setattr(config.integracoes, "salvar_lojas", canonical_forbidden)
    return values


def test_current_credentials_keep_exact_identity_without_writes(rows):
    before = copy.deepcopy(rows)
    first = config.obter_cfg_ml_snapshot("tenant-a", "obsolete-name", "StoreA",
                                        seller_id="seller-a", site_id="MLB")
    assert first["_store_id_context"] == "StoreA"
    assert first["app_id"] == "fixture-app"
    assert first["client_secret"] == "fixture-secret"
    assert rows == before
    rows["tenant-a"][0]["integracoes"]["mercadolivre"]["access_token"] = "refreshed-access"
    assert config.obter_cfg_ml_snapshot("tenant-a", "Loja A")["access_token"] == "refreshed-access"


@pytest.mark.parametrize("tenant,identity", [("tenant-a", "StoreB"), ("tenant-a", "storea"),
                                             ("tenant-b", "StoreA")])
def test_explicit_identity_never_falls_back_to_name_or_other_tenant(rows, tenant, identity):
    with pytest.raises(HTTPException) as caught:
        config.obter_cfg_ml_snapshot(tenant, "Loja A", identity)
    assert caught.value.status_code == 404


def test_legacy_ambiguous_name_cannot_select_an_account(rows):
    rows["tenant-a"].append(_store("StoreOther", "Loja A", "seller-other"))
    with pytest.raises(HTTPException) as caught:
        config.obter_cfg_ml_snapshot("tenant-a", "Loja A")
    assert caught.value.status_code == 409
    assert config.obter_cfg_ml_snapshot("tenant-a", "Loja A", "StoreA")["user_id"] == "seller-a"


def test_send_name_must_match_the_selected_store(rows):
    with pytest.raises(HTTPException) as caught:
        config.obter_cfg_ml_snapshot("tenant-a", "Loja B", "StoreA", require_name_match=True)
    assert caught.value.detail["code"] == "store_identity_changed"
    cfg = config.obter_cfg_ml_snapshot("tenant-a", " loja a ", "StoreA", require_name_match=True)
    assert cfg["_store_id_context"] == "StoreA"


@pytest.mark.parametrize("expected", [{"seller_id": "seller-other"}, {"site_id": "MLA"},
                                     {"seller_id": ""}, {"site_id": ""}])
def test_expected_identity_change_blocks_credentials(rows, expected):
    with pytest.raises(HTTPException) as caught:
        config.obter_cfg_ml_snapshot("tenant-a", "Loja A", "StoreA", **expected)
    assert caught.value.detail["code"] == "store_identity_changed"


@pytest.mark.parametrize("field,value,status", [("user_id", "", 409),
                                              ("site_id", "wrong", 409), ("access_token", "", 401)])
def test_incomplete_credentials_fail_closed(rows, field, value, status):
    rows["tenant-a"][0]["integracoes"]["mercadolivre"][field] = value
    with pytest.raises(HTTPException) as caught:
        config.obter_cfg_ml_snapshot("tenant-a", "Loja A", "StoreA")
    assert caught.value.status_code == status


def test_legacy_store_site_can_come_from_validated_generation_preflight(rows):
    source = rows["tenant-a"][0]["integracoes"]["mercadolivre"]
    source.pop("site_id")
    cfg = config.obter_cfg_ml_snapshot("tenant-a", "Loja A", "StoreA",
                                      seller_id="seller-a", site_id="MLA")
    assert cfg["site_id"] == "MLA"
    assert "site_id" not in source


def test_legacy_send_does_not_infer_or_require_an_absent_site(rows):
    source = rows["tenant-a"][0]["integracoes"]["mercadolivre"]
    source.pop("site_id")
    cfg = config.obter_cfg_ml_snapshot("tenant-a", "Loja A", "StoreA", require_name_match=True)
    assert not cfg.get("site_id")
    assert cfg["user_id"] == "seller-a"
    assert cfg["_store_id_context"] == "StoreA"


def test_snapshot_unavailable_is_propagated_without_canonical_fallback(rows, monkeypatch):
    error = HTTPException(503, detail={"code": "stores_snapshot_unavailable"},
                          headers={"Retry-After": "2"})
    def unavailable(_tenant):
        raise error
    monkeypatch.setattr(config.integracoes, "ler_lojas", unavailable)
    with pytest.raises(HTTPException) as caught:
        config.obter_cfg_ml_snapshot("tenant-a", "Loja A", "StoreA")
    assert caught.value is error
