"""Local Cofre uses the existing JK session; cloud authorization stays strict."""
import ast
import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from types import SimpleNamespace

import jwt
import pytest
import requests
from fastapi import Header, HTTPException, Request

from backend.services import cofre_auth, remote_auth_client


ROOT = Path(__file__).resolve().parents[1]
KEY = "fixture-jk-local-signing-key-" + "x" * 64
IDENTITY = {"sub": "fixture-user", "client_id": "fixture-company", "machine_id": "fixture-pc"}
NOW = datetime(2030, 1, 1, 12, tzinfo=timezone.utc).timestamp()


def bindings(*, profile=None, payload=None):
    record = {"client_id": "fixture-company", "machine_ids": ["fixture-pc"],
              "active": True, "valid_until": None, "permissions": {"cofre": True}}
    record.update(profile or {})
    tree = ast.parse((ROOT / "backend_api.py").read_text("utf-8-sig"))
    names = {"_cofre_verified_jk_session", "get_cofre_tenant_id", "get_cofre_remote_principal"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    scope = {"HTTPException": HTTPException, "Request": Request, "Optional": Optional, "Header": Header,
        "decodificar_access_token": lambda token: jwt.decode(token, KEY, algorithms=["HS256"]),
        "_obter_politica_local_cofre_sql": lambda username: copy.deepcopy(record),
        "_login_usuario_ativo": lambda user: user.get("active") is True,
        "_login_validade_ok": lambda user: (user.get("valid_until") != "expired", None),
        "_normalizar_permissoes": lambda value: dict(value)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "real-cofre-dependencies", "exec"), scope)
    token = jwt.encode(payload or IDENTITY, KEY, algorithm="HS256")
    authorization = "Bearer " + token
    request = Request({"type": "http", "headers": [(b"authorization", authorization.encode())]})
    return SimpleNamespace(scope=scope, token=token, authorization=authorization, request=request)


@pytest.fixture
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Local Cofre must not read cloud proofs, WinCred, JWKS, Firebase or the network")
    monkeypatch.setattr(cofre_auth, "principal_for_token", forbidden)
    monkeypatch.setattr(cofre_auth, "_store", forbidden)
    monkeypatch.setattr(cofre_auth, "_public_keys", forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(remote_auth_client, "attempt_cofre_session", forbidden)
    monkeypatch.setattr(cofre_auth.time, "time", lambda: NOW)


def local(fixture):
    tenant = fixture.scope["get_cofre_tenant_id"](fixture.request, fixture.authorization)
    return tenant, fixture.request.state.cofre_principal


def test_local_archive_opens_without_remote_capability_or_ticket(offline):
    fixture = bindings()
    tenant, principal = local(fixture)
    assert tenant == IDENTITY["client_id"]
    assert principal == {"username": IDENTITY["sub"], "client_id": IDENTITY["client_id"],
                         "scope": ["cofre:read"], "exp": int(NOW) + cofre_auth.MAX_SESSION_SECONDS, "capability": ""}
    assert fixture.request.state.username == IDENTITY["sub"]
    assert fixture.request.state.client_id == tenant


def test_local_operation_can_finish_after_a_slow_upload_without_becoming_cloud_authority(offline, monkeypatch, tmp_path):
    from backend.modules.cofre.shared import CofreSyncUnavailable, SharedCofre
    tenant, principal = local(bindings(profile={"permissions": {"full": True}}))
    monkeypatch.setattr(cofre_auth.time, "time", lambda: NOW + 120)
    shared = SharedCofre(tmp_path, url="https://cofre.example.test")
    shared._assert_local_principal(tenant, principal, write=True)
    with pytest.raises(CofreSyncUnavailable):
        shared._assert_principal(tenant, principal, write=True)
    assert principal["capability"] == ""


@pytest.mark.parametrize("permissions,scope", [
    ({"cofre": True}, ["cofre:read"]),
    ({"cofre": True, "admin_usuarios": True}, ["cofre:read", "cofre:write"]),
    ({"full": True}, ["cofre:read", "cofre:write"]),
])
def test_local_write_scope_follows_current_jk_policy(offline, permissions, scope):
    assert local(bindings(profile={"permissions": permissions}))[1]["scope"] == scope


@pytest.mark.parametrize("profile", [
    {"client_id": "other-company"}, {"machine_ids": ["other-machine"]},
    {"active": False}, {"valid_until": "expired"},
    {"permissions": {"cofre": False, "admin_usuarios": True}},
])
def test_local_access_still_rejects_company_machine_activity_licence_and_permission(offline, profile):
    with pytest.raises(HTTPException) as error:
        local(bindings(profile=profile))
    assert error.value.status_code == 403


def test_token_permission_claims_do_not_override_current_policy(offline):
    fixture = bindings(payload={**IDENTITY, "permissions": {"full": True}})
    assert local(fixture)[1]["scope"] == ["cofre:read"]


@pytest.mark.parametrize("authorization", [None, "", "Basic fixture", "Bearer ", "Bearer forged"])
def test_invalid_local_tokens_are_401_with_bearer_challenge(offline, authorization):
    fixture = bindings()
    with pytest.raises(HTTPException) as error:
        fixture.scope["get_cofre_tenant_id"](fixture.request, authorization)
    assert error.value.status_code == 401
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.parametrize("changes,status", [
    ({"sub": ""}, 401), ({"client_id": "../another-company"}, 403), ({"machine_id": ""}, 401),
])
def test_incomplete_or_invalid_tenant_identity_cannot_open_local_archive(offline, changes, status):
    fixture = bindings(payload={**IDENTITY, **changes})
    with pytest.raises(HTTPException) as error:
        local(fixture)
    assert error.value.status_code == status


def test_local_licence_ends_at_sao_paulo_midnight(offline, monkeypatch):
    midnight = datetime(2030, 1, 2, 3, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(cofre_auth.time, "time", lambda: midnight - 10)
    assert local(bindings(profile={"valid_until": "01/01/2030"}))[1]["exp"] == int(midnight)
    monkeypatch.setattr(cofre_auth.time, "time", lambda: midnight)
    with pytest.raises(HTTPException) as error:
        local(bindings(profile={"valid_until": "01/01/2030"}))
    assert error.value.status_code == 403


@pytest.mark.parametrize("validity", ["31/02/2030", "1/1/2030", 123, ""])
def test_malformed_local_licence_fails_closed(offline, validity):
    with pytest.raises(HTTPException) as error:
        local(bindings(profile={"valid_until": validity}))
    assert error.value.status_code == 403


def test_local_principal_never_becomes_a_cloud_credential(monkeypatch):
    fixture = bindings()
    monkeypatch.setattr(cofre_auth.time, "time", lambda: NOW)
    monkeypatch.setattr(cofre_auth, "_SESSIONS", {})
    monkeypatch.setattr(cofre_auth, "_store", lambda: SimpleNamespace(load=lambda key: None))
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("No cloud call without proof"))
    assert local(fixture)[1]["capability"] == ""
    assert cofre_auth._SESSIONS == {}
    with pytest.raises(HTTPException) as error:
        fixture.scope["get_cofre_remote_principal"](fixture.request)
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "cofre_session_unavailable"
    assert cofre_auth._SESSIONS == {}


def test_router_registers_separate_local_and_remote_dependencies():
    tree = ast.parse((ROOT / "backend_api.py").read_text("utf-8-sig"))
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "create_cofre_router")
    kwargs = {item.arg: item.value for item in call.keywords}
    assert kwargs["tenant_dependency"].id == "get_cofre_tenant_id"
    assert kwargs["remote_principal"].id == "get_cofre_remote_principal"
