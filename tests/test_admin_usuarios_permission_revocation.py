"""Explicit permission revocations survive stale caches and SQL reloads."""
from __future__ import annotations

import ast
import copy
import json
import logging
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

from backend.schemas.admin import AdminUserUpsertRequest
from backend.services import admin_usuarios_common as common
from backend.services import admin_usuarios_login_core as login
from backend.services import admin_usuarios_store as store


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ast.parse((ROOT / "backend_api.py").read_text(encoding="utf-8-sig"))
PERMISSION_KEYS = ast.literal_eval(next(
    node.value for node in BACKEND.body if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "PERMISSION_KEYS" for target in node.targets)
))
USERNAME = "fixture-user"
IDENTITY = {"sub": USERNAME, "client_id": "fixture-company", "machine_id": "fixture-pc"}


@pytest.fixture
def policy(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Permission regression must not access real users or the network")

    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(common, "PERMISSION_KEYS", PERMISSION_KEYS, raising=False)
    monkeypatch.setattr(common, "_machine_id_eh_generico", lambda value: False, raising=False)
    monkeypatch.setattr(login, "PERMISSION_KEYS", PERMISSION_KEYS, raising=False)
    monkeypatch.setattr(login, "unicodedata", unicodedata, raising=False)
    monkeypatch.setattr(login, "_normalizar_permissoes", common._normalizar_permissoes, raising=False)
    for name in ("_normalizar_permissoes", "_normalizar_email", "_normalizar_empresa",
                 "_normalizar_data_sistema", "_normalizar_max_machines",
                 "_normalizar_lista_maquinas", "_pydantic_campo_enviado"):
        monkeypatch.setattr(store, name, getattr(common, name), raising=False)
    for name, value in {
        "PASTA_INFO": str(tmp_path), "ARQUIVO_AUTH_DB": str(tmp_path / "auth.db"),
        "_hash_password_se_preciso": lambda value: value,
        "_firebase_deve_usar": lambda: False,
        "_backend_cache_invalidate_user_views": lambda *args: None,
        "logger": logging.getLogger(__name__),
        "extrair_permissoes": login.extrair_permissoes,
    }.items():
        monkeypatch.setattr(store, name, value, raising=False)
    profile = {"password": "fixture-password", "client_id": IDENTITY["client_id"],
               "machine_id": IDENTITY["machine_id"], "active": True,
               "permissions": {"cofre": True}}
    cache = {USERNAME: copy.deepcopy(profile)}
    local = {USERNAME: copy.deepcopy(profile)}
    calls = []

    def cached():
        calls.append("cache")
        return copy.deepcopy(cache), ["cofre"]

    def local_users():
        calls.append("local")
        return copy.deepcopy(local), []

    monkeypatch.setattr(store, "_carregar_cache_usuarios", cached, raising=False)
    monkeypatch.setattr(store, "_carregar_usuarios_local", local_users, raising=False)
    store._salvar_usuarios_sql({USERNAME: profile}, source="sql-admin")

    function = next(node for node in BACKEND.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_cofre_verified_jk_session")
    scope = {"HTTPException": HTTPException, "decodificar_access_token": lambda token: IDENTITY,
             "_obter_politica_local_cofre_sql": store._obter_politica_local_cofre_sql,
             "_normalizar_permissoes": common._normalizar_permissoes,
             "_login_usuario_ativo": lambda user: user["active"],
             "_login_validade_ok": lambda user: (True, None)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "cofre-session", "exec"), scope)
    return SimpleNamespace(cache=cache, local=local, calls=calls,
                           authorize=scope["_cofre_verified_jk_session"])


def stored_permissions():
    conn = store._auth_db_conexao()
    try:
        return conn.execute("SELECT permissions_json FROM usuarios_auth WHERE username = ?",
                            (USERNAME,)).fetchone()[0]
    finally:
        conn.close()


def replace_stored_permissions(raw):
    conn = store._auth_db_conexao()
    try:
        conn.execute("UPDATE usuarios_auth SET permissions_json = ? WHERE username = ?", (raw, USERNAME))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("save_path", ["user_form", "permissions_endpoint"])
def test_revoking_only_cofre_stays_revoked_after_save_and_reload(policy, save_path):
    assert policy.authorize("fixture-token")["permissions"]["cofre"] is True
    if save_path == "user_form":
        result = store._salvar_usuario_admin_sql(AdminUserUpsertRequest(
            username=USERNAME, client_id=IDENTITY["client_id"], permissions={"cofre": False}))
    else:
        result = store._atualizar_permissoes_usuario_sql(USERNAME, {"cofre": False})

    assert not any(result["permissions"].values())
    before_reload = stored_permissions()
    assert not any(json.loads(before_reload).values())
    for _ in range(2):
        assert not any(store._obter_usuario_sql(USERNAME)["permissions"].values())
        assert not any(store._obter_politica_local_cofre_sql(USERNAME)["permissions"].values())
        with pytest.raises(HTTPException) as denied:
            policy.authorize("fixture-token")
        assert denied.value.status_code == 403
    assert stored_permissions() == before_reload
    assert policy.calls == []


@pytest.mark.parametrize("permissions", [
    {"cofre": False}, {"cofre": 0}, {"cofre": "false"}, {"full": False},
    {key: False for key in PERMISSION_KEYS}, {"unknown_old_permission": True},
    {"cofre": False, "vendas": True}, {"cofre": False, "full": True},
])
def test_nonempty_saved_policy_is_authoritative_even_when_all_denied(policy, permissions):
    raw = json.dumps(permissions)
    replace_stored_permissions(raw)
    expected = common._normalizar_permissoes(permissions)
    assert store._obter_usuario_sql(USERNAME)["permissions"] == expected
    assert stored_permissions() == raw
    assert policy.calls == []


@pytest.mark.parametrize("raw", ["{}", "", "invalid json", "null", "[]"])
@pytest.mark.parametrize("legacy_source", ["cache", "local", "original_row"])
def test_missing_legacy_policy_still_recovers_and_persists(policy, raw, legacy_source):
    replace_stored_permissions(raw)
    if legacy_source == "local":
        policy.cache.clear()
    elif legacy_source == "original_row":
        policy.cache[USERNAME] = {"original_row": ["true"]}
    assert store._obter_usuario_sql(USERNAME)["permissions"]["cofre"] is True
    assert json.loads(stored_permissions())["cofre"] is True
    policy.calls.clear()
    assert store._obter_usuario_sql(USERNAME)["permissions"]["cofre"] is True
    assert policy.calls == []
