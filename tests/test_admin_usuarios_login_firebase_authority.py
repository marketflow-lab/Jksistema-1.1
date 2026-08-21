from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.services import admin_usuarios_login_core as login_core


def _configure_login_sources(
    monkeypatch,
    *,
    firebase_active: bool,
    firebase_required: bool,
    firebase_first: bool,
    firebase_users,
    local_users,
):
    calls = []

    monkeypatch.setattr(login_core, "_firebase_deve_usar", lambda: firebase_active, raising=False)
    monkeypatch.setattr(login_core, "_firebase_access_obrigatorio", lambda: firebase_required, raising=False)
    monkeypatch.setattr(
        login_core,
        "_env_config_bool",
        lambda _names, default=False: firebase_first,
        raising=False,
    )

    def load_firebase(*, seed_if_empty=True):
        calls.append(("firebase", seed_if_empty))
        return firebase_users

    def load_local_sql():
        calls.append(("sql", None))
        return local_users, ["local-header"]

    monkeypatch.setattr(login_core, "_firebase_listar_usuarios", load_firebase, raising=False)
    monkeypatch.setattr(login_core, "_carregar_usuarios_sql", load_local_sql, raising=False)
    return calls


def test_firebase_required_is_authoritative_even_when_first_flag_is_false(monkeypatch):
    remote = {"usuario": {"active": False, "source": "firebase"}}
    local = {"usuario": {"active": True, "source": "firebase-cache"}}
    calls = _configure_login_sources(
        monkeypatch,
        firebase_active=True,
        firebase_required=True,
        firebase_first=False,
        firebase_users=remote,
        local_users=local,
    )

    users, worksheet, headers = login_core.carregar_usuarios_sheets()

    assert users is remote
    assert worksheet is None
    assert headers == []
    assert calls == [("firebase", False)]


def test_firebase_required_fails_closed_when_remote_returns_no_result(monkeypatch):
    calls = _configure_login_sources(
        monkeypatch,
        firebase_active=True,
        firebase_required=True,
        firebase_first=True,
        firebase_users=None,
        local_users={"usuario": {"active": True}},
    )

    with pytest.raises(HTTPException) as exc_info:
        login_core.carregar_usuarios_sheets()

    assert exc_info.value.status_code == 503
    assert "Firebase indisponivel" in str(exc_info.value.detail)
    assert calls == [("firebase", False)]


def test_firebase_required_does_not_bootstrap_an_empty_remote_from_local(monkeypatch):
    calls = _configure_login_sources(
        monkeypatch,
        firebase_active=True,
        firebase_required=True,
        firebase_first=True,
        firebase_users={},
        local_users={"usuario": {"active": True}},
    )

    users, worksheet, headers = login_core.carregar_usuarios_sheets()

    assert users == {}
    assert worksheet is None
    assert headers == []
    assert calls == [("firebase", False)]


def test_firebase_auto_uses_remote_first_by_default(monkeypatch):
    remote = {"usuario": {"source": "firebase"}}
    calls = []
    monkeypatch.setattr(login_core, "_firebase_deve_usar", lambda: True, raising=False)
    monkeypatch.setattr(login_core, "_firebase_access_obrigatorio", lambda: False, raising=False)
    monkeypatch.setattr(
        login_core,
        "_env_config_bool",
        lambda _names, default=False: default,
        raising=False,
    )
    monkeypatch.setattr(
        login_core,
        "_firebase_listar_usuarios",
        lambda *, seed_if_empty=True: calls.append(("firebase", seed_if_empty)) or remote,
        raising=False,
    )
    monkeypatch.setattr(
        login_core,
        "_carregar_usuarios_sql",
        lambda: pytest.fail("SQL local nao deveria preceder o Firebase no modo automatico padrao"),
        raising=False,
    )

    users, _worksheet, _headers = login_core.carregar_usuarios_sheets()

    assert users is remote
    assert calls == [("firebase", True)]


def test_firebase_auto_falls_back_to_sql_when_remote_is_unavailable(monkeypatch):
    local = {"usuario": {"source": "firebase-cache"}}
    calls = _configure_login_sources(
        monkeypatch,
        firebase_active=True,
        firebase_required=False,
        firebase_first=True,
        firebase_users=None,
        local_users=local,
    )

    users, worksheet, headers = login_core.carregar_usuarios_sheets()

    assert users is local
    assert worksheet is None
    assert headers == ["local-header"]
    assert calls == [("firebase", True), ("sql", None)]


def test_firebase_auto_respects_explicit_local_first_override(monkeypatch):
    local = {"usuario": {"source": "firebase-cache"}}
    calls = _configure_login_sources(
        monkeypatch,
        firebase_active=True,
        firebase_required=False,
        firebase_first=False,
        firebase_users={"usuario": {"source": "firebase"}},
        local_users=local,
    )

    users, worksheet, headers = login_core.carregar_usuarios_sheets()

    assert users is local
    assert worksheet is None
    assert headers == ["local-header"]
    assert calls == [("sql", None)]
