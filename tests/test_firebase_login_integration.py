"""Authentication-to-Firestore integration without loading operational databases."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import Mock

import bcrypt
import pytest
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from jose import JWTError, jwt

from backend.schemas import LoginRequest, UserChangePasswordRequest
from backend.services import admin_usuarios_auth as auth
from backend.services import admin_usuarios_common as common
from backend.services import admin_usuarios_firebase as firebase
from backend.services import admin_usuarios_login_core as login_core
from backend.services import firebase_user_session as sessions
from backend.services.remote_auth_contracts import RemoteAuthAttempt, RemoteAuthState


IDENTITY = {"username": "usuario", "client_id": "empresa-a", "machine_id": "pc:um"}
BACKEND = Path(__file__).resolve().parents[1] / "backend_api.py"


def backend_functions(*names, namespace=None):
    """Execute unchanged production functions, excluding startup/file side effects."""
    tree = ast.parse(BACKEND.read_text(encoding="utf-8-sig"))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    for node in nodes:
        node.decorator_list = []
    scope = {"jwt": jwt, "JWTError": JWTError, "Optional": Optional, "Request": Request,
             "HTTPException": HTTPException, "JSONResponse": JSONResponse,
             "JWT_SECRET": "test-only-key-which-is-long-enough-for-local-tests",
             "JWT_ALGORITHM": "HS256", "JWT_DECODE_OPTIONS": {"verify_exp": False},
             **(namespace or {})}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BACKEND), "exec"), scope)
    return scope


@pytest.fixture
def runtime(monkeypatch):
    sessions._SESSIONS.clear()
    marker = sessions._CURRENT.set(None)
    scope = backend_functions("criar_access_token", "decodificar_access_token", "central_request_context")
    monkeypatch.setattr(common, "decodificar_access_token", scope["decodificar_access_token"], raising=False)
    monkeypatch.setattr(common, "JWTError", JWTError, raising=False)
    monkeypatch.setattr(auth, "_payload_sessao_por_authorization", common._payload_sessao_por_authorization, raising=False)
    monkeypatch.setattr(auth, "_authenticated_session_machine_id", lambda identity, machine: identity["machine_id"], raising=False)
    monkeypatch.setattr(login_core, "criar_access_token", scope["criar_access_token"], raising=False)
    monkeypatch.setattr(login_core, "_normalizar_email", lambda value: str(value or "").lower(), raising=False)
    monkeypatch.setattr(login_core, "_normalizar_permissoes", lambda value: dict(value), raising=False)
    monkeypatch.setattr(auth, "_montar_resposta_login_sucesso", login_core._montar_resposta_login_sucesso, raising=False)
    yield scope
    sessions._SESSIONS.clear()
    sessions._CURRENT.reset(marker)


def ready_session(local_token):
    session = sessions.FirebaseUserSession(IDENTITY)
    session._credentials = Mock()
    session._credentials.token = "secret-firebase-id"
    session._credentials._refresh_token = "secret-firebase-refresh"
    session._credentials._api_key = "secret-test-api-key"
    session._code = "ready"
    grant = {**IDENTITY, "uid": sessions.expected_uid(IDENTITY["username"]), "profile": {
        **IDENTITY, "permissions": {"cadastro": True}, "active": True,
        "machine_ids": [IDENTITY["machine_id"]], "valid_until": None}}
    session._db = Mock()
    session._db.collection.return_value.document.return_value.get.return_value = SimpleNamespace(exists=True, to_dict=lambda: grant)
    sessions._SESSIONS[sessions._key(local_token)] = session
    return session


def test_remote_login_marks_local_jwt_and_does_not_expose_firebase_credentials(runtime, monkeypatch):
    access = {"id_token": "secret-firebase-id", "refresh_token": "secret-firebase-refresh", "api_key": "secret-test-api-key"}
    remote = RemoteAuthAttempt(RemoteAuthState.SUCCESS, user_data=IDENTITY,
                               permissions={"cadastro": True},
                               policy={"active": True, "valid_until": None, "max_machines": 1},
                               firebase_access=access)
    monkeypatch.setattr(auth, "attempt_remote_login", lambda **kwargs: remote)
    monkeypatch.setattr(auth, "bcrypt", bcrypt, raising=False)
    monkeypatch.setattr(auth, "_validar_versao_minima_app_ou_426", lambda value: value, raising=False)
    monkeypatch.setattr(auth, "_montar_machine_id_login", lambda request, machine: (machine, {}), raising=False)
    cached = Mock()
    monkeypatch.setattr(auth, "_salvar_usuarios_sql", cached, raising=False)
    for name in ("_registrar_login_maquina", "_machine_presence_save", "_machine_presence_record"):
        monkeypatch.setattr(auth, name, lambda *args: {}, raising=False)
    register = Mock(side_effect=lambda token, attempt: ready_session(token))
    monkeypatch.setattr(sessions, "register_login", register)
    response = asyncio.run(auth.login_endpoint(LoginRequest(
        username="usuario", password="login-secret", client_id="injected-tenant",
        machine_id="pc:um", app_version="1.0.140"), request=None))
    assert response.success is True
    claims = jwt.decode(response.access_token, runtime["JWT_SECRET"], algorithms=["HS256"])
    assert claims["jk_firebase"] == 1
    assert claims["client_id"] == "empresa-a"
    assert claims["machine_id"] == "pc:um"
    register.assert_called_once_with(response.access_token, remote)
    projection = response.model_dump_json() + repr(cached.call_args)
    for secret in (*access.values(), "login-secret"):
        assert secret not in projection
    assert response.user_data["firebase"] == {"ready": True, "code": "ready"}


def test_me_keeps_original_marked_local_jwt_and_uses_grant_permissions(runtime, monkeypatch):
    token = runtime["criar_access_token"]("usuario", "empresa-a", "pc:um", firebase_user=True)
    ready_session(token)
    monkeypatch.setattr(auth, "_obter_usuario_sql", lambda *args: pytest.fail("scoped session must read safe grant"), raising=False)
    response = auth.minha_sessao_auth(request=None, authorization=f"Bearer {token}")
    assert response.access_token == token
    assert response.permissions == {"cadastro": True}
    assert response.user_data["firebase"]["ready"] is True
    assert "secret-firebase" not in response.model_dump_json()


def test_expired_session_never_reaches_cached_admin_firestore(runtime, monkeypatch):
    token = runtime["criar_access_token"]("usuario", "empresa-a", "pc:um", firebase_user=True)
    session = ready_session(token)
    session._code = "session_expired"
    runtime["decodificar_access_token"](token)
    admin = object()
    monkeypatch.setattr(firebase, "FIREBASE_AUTH_DB", admin, raising=False)
    monkeypatch.setattr(firebase, "_firebase_deve_usar", lambda: pytest.fail("must never try Admin fallback"))
    with pytest.raises(HTTPException) as exc:
        firebase._firebase_db()
    assert exc.value.status_code == 401
    assert firebase.FIREBASE_AUTH_DB is admin


def app_with_production_middleware(runtime):
    app = FastAPI()
    app.middleware("http")(runtime["central_request_context"])
    # Use the actual backend's router import and registration statements.
    tree = ast.parse(BACKEND.read_text(encoding="utf-8-sig"))
    names = ("create_firebase_user_session_router",)
    nodes = [node for node in tree.body if (
        isinstance(node, ast.ImportFrom) and any(alias.name in names for alias in node.names)
        or isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "include_router"
        and any(isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id in names for arg in node.value.args))]
    assert len(nodes) == 2
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BACKEND), "exec"),
         {"app": app, "_payload_sessao_por_authorization": common._payload_sessao_por_authorization})
    return app


def test_status_route_requires_auth_and_returns_only_sanitized_state(runtime):
    token = runtime["criar_access_token"]("usuario", "empresa-a", "pc:um", firebase_user=True)
    ready_session(token)
    with TestClient(app_with_production_middleware(runtime)) as client:
        assert client.get("/api/firebase/session/status").status_code == 401
        assert client.get("/api/firebase/session/status", headers={"Authorization": "Bearer invalid"}).status_code == 401
        response = client.get("/api/firebase/session/status", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == {"success": True, "configured": True, "ready": True,
            "source": "user_session", "code": "ready", "restart_required": False,
            "can_migrate": False, "replacement_required": False}
        assert "secret" not in response.text
        assert client.get("/api/firebase/session/status").status_code == 401
    assert sessions.current() is None


def test_async_operation_sees_scope_from_sync_dependency_and_never_admin(runtime, monkeypatch):
    token = runtime["criar_access_token"]("usuario", "empresa-a", "pc:um", firebase_user=True)
    session = ready_session(token)
    app = app_with_production_middleware(runtime)

    def authorization_dependency(authorization: str | None = Header(default=None)):
        return common._payload_sessao_por_authorization(authorization)

    @app.get("/test-scoped")
    async def operation(identity=Depends(authorization_dependency)):
        assert sessions.current(identity["client_id"]) is session
        assert firebase._firebase_db() is session._db
        return {"scoped": True}

    monkeypatch.setattr(firebase, "_firebase_deve_usar", lambda: pytest.fail("Admin fallback"))
    with TestClient(app) as client:
        response = client.get("/test-scoped", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == {"scoped": True}
        session._code = "session_expired"
        assert client.get("/test-scoped", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert sessions.current() is None


def test_scoped_password_change_refreshes_credentials_under_same_local_token(runtime, monkeypatch):
    token = runtime["criar_access_token"]("usuario", "empresa-a", "pc:um", firebase_user=True)
    session = ready_session(token)
    remote = RemoteAuthAttempt(RemoteAuthState.SUCCESS, user_data=IDENTITY,
        permissions={"cadastro": True}, policy={"active": True},
        firebase_access={"id_token": "new-private-id", "refresh_token": "new-private-refresh"})
    monkeypatch.setattr(auth, "_obter_usuario_sql", lambda username: session.profile(), raising=False)
    change_password = Mock(return_value=remote)
    monkeypatch.setattr(auth, "attempt_remote_password_change", change_password)
    monkeypatch.setattr(auth, "_salvar_perfil_autenticado_remoto", lambda *args: {}, raising=False)
    monkeypatch.setattr(auth, "_login_senha_confere", lambda *args: pytest.fail("must use central authentication"), raising=False)
    register = Mock(return_value=session)
    monkeypatch.setattr(sessions, "register_login", register)
    response = auth.trocar_minha_senha(UserChangePasswordRequest(
        current_password="old-login-secret", new_password="new-login-secret",
        confirm_password="new-login-secret"), authorization=f"Bearer {token}")
    assert response["success"] is True
    register.assert_called_once_with(token, remote)
    assert change_password.call_args.kwargs["machine_id"] == "pc:um"
    assert "private" not in repr(response)


@pytest.fixture
def pending_firebase_sales_sync(runtime, monkeypatch, tmp_path):
    from backend.modules.vendas import sync_automation, sync_engine
    from backend.modules.vendas.router import create_vendas_router
    from backend.modules.vendas.state import VendasSyncState
    from backend.modules.vendas.sync_queue import SyncQueue
    from backend.modules.vendas.sync_service import VendasSyncService

    now = int(sessions.time.time())
    access = {"protocol": 1, "project_id": "project-safe", "grant_id": "a" * 64,
              "id_token": "synthetic-id-token", "refresh_token": "synthetic-refresh-token",
              "api_key": "synthetic-api-key", "expires_at": now + 3500,
              "session_expires_at": now + 28000}
    claims = {"aud": access["project_id"], "iss": "https://securetoken.google.com/project-safe",
              "sub": sessions.expected_uid(IDENTITY["username"]), "jk_firebase_v": 1,
              "jk_firebase_grant": access["grant_id"], "jk_client_id": IDENTITY["client_id"],
              "jk_username": IDENTITY["username"],
              "jk_machine_hash": sessions.machine_hash(IDENTITY["machine_id"]), "exp": now + 3600}
    grant = {**IDENTITY, "uid": claims["sub"], "profile": {
        **IDENTITY, "permissions": {"vendas": True}, "active": True,
        "machine_ids": [IDENTITY["machine_id"]]}}
    verifier = Mock(side_effect=TimeoutError("synthetic certificate outage"))
    monkeypatch.setattr(sessions, "_verify_token", verifier)
    db = Mock()
    db.collection.return_value.document.return_value.get.return_value = SimpleNamespace(
        exists=True, to_dict=lambda: grant)
    monkeypatch.setattr(sessions, "UserFirestoreClient", Mock(return_value=db))
    token = runtime["criar_access_token"]("usuario", "empresa-a", "pc:um", firebase_user=True)
    session = sessions.register_login(token, SimpleNamespace(success=True, user_data=IDENTITY,
                                                           firebase_access=access))
    assert session._code == "connection_failed"
    assert session._credentials is None

    def deny_fallback(*args, **kwargs):
        pytest.fail("Firebase user authorization must not fall back to Admin or cached permissions")

    monkeypatch.setattr(common, "_firebase_deve_usar", firebase._firebase_deve_usar, raising=False)
    monkeypatch.setattr(common, "_firebase_obter_usuario", firebase._firebase_obter_usuario, raising=False)
    monkeypatch.setattr(common, "_normalizar_permissoes", lambda value: dict(value), raising=False)
    for name in ("carregar_usuarios_sheets", "_carregar_usuarios_sql", "_carregar_cache_usuarios",
                 "_carregar_usuarios_local"):
        monkeypatch.setattr(common, name, deny_fallback, raising=False)
    for name in ("_firebase_collection", "_firebase_credencial"):
        monkeypatch.setattr(firebase, name, deny_fallback)
    auth_scope = backend_functions("get_tenant_id", namespace={
        **runtime, "asyncio": asyncio, "Header": Header, "logger": Mock(),
        "_permissao_exigida_por_rota": common._permissao_exigida_por_rota,
        "_carregar_permissoes_usuario": common._carregar_permissoes_usuario,
        "_permissoes_autorizam_rota": common._permissoes_autorizam_rota,
        "_normalizar_permissoes_exigidas": common._normalizar_permissoes_exigidas,
        "_machine_presence_auto_touch": Mock(),
    })
    queue = SyncQueue(IDENTITY["client_id"], tenant_path=tmp_path / IDENTITY["client_id"])
    enqueue = Mock(wraps=queue.enqueue)
    monkeypatch.setattr(queue, "enqueue", enqueue)
    queue_factory = Mock(return_value=queue)
    monkeypatch.setattr(sync_engine, "SyncQueue", queue_factory)

    def find_store(client_id, name, *, store_id):
        assert client_id == IDENTITY["client_id"]
        return {"nome": name, "store_id": store_id}

    store_lookup = Mock(side_effect=find_store)
    monkeypatch.setattr(sync_engine, "buscar_loja", store_lookup)
    for name in ("start", "stop", "remember_authorization", "dispatch"):
        monkeypatch.setattr(sync_automation, name, Mock())
    app = app_with_production_middleware(runtime)
    app.include_router(create_vendas_router(Mock(), VendasSyncService(VendasSyncState()),
                                            SimpleNamespace(get_tenant_id=auth_scope["get_tenant_id"])))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, session=session, verifier=verifier, claims=claims,
                              grant=grant, headers={"Authorization": f"Bearer {token}"},
                              queue=queue, enqueue=enqueue, queue_factory=queue_factory,
                              store_lookup=store_lookup, db=db)


def sales_sync_request(number=0):
    return {"loja": f"Loja {number:02}", "store_id": f"store-{number:02}",
            "data_inicio": "2026-10-01", "data_fim": "2026-10-02", "automatico": True}


def test_sales_sync_recovers_firebase_before_registering_one_durable_job(pending_firebase_sales_sync):
    state = pending_firebase_sales_sync
    for number in range(11):
        response = state.client.post("/api/vendas/sync", headers=state.headers,
                                     json=sales_sync_request(number))
        assert response.status_code == 503
        assert response.json() == {"detail": "connection_failed"}
    state.enqueue.assert_not_called()
    state.queue_factory.assert_not_called()
    state.store_lookup.assert_not_called()
    assert state.queue.list_jobs() == []

    state.verifier.side_effect = None
    state.verifier.return_value = state.claims
    response = state.client.post("/api/vendas/sync", headers=state.headers, json=sales_sync_request())
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    state.enqueue.assert_called_once()
    state.queue_factory.assert_called_once_with(IDENTITY["client_id"])
    assert state.session._code == "ready"
    assert state.session._access == {}
    jobs = state.queue.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["store_id"] == "store-00"
    assert jobs[0]["status"] == "queued"

    duplicate = state.client.post("/api/vendas/sync", headers=state.headers, json=sales_sync_request())
    assert duplicate.status_code == 200
    assert duplicate.json()["job_id"] == response.json()["job_id"]
    assert len(state.queue.list_jobs()) == 1
    assert sessions.current() is None


def test_sales_sync_recovery_still_requires_sales_permission(pending_firebase_sales_sync):
    state = pending_firebase_sales_sync
    state.grant["profile"]["permissions"] = {"cadastro": True, "vendas": False}
    state.verifier.side_effect = None
    state.verifier.return_value = state.claims
    response = state.client.post("/api/vendas/sync", headers=state.headers, json=sales_sync_request())
    assert response.status_code == 403
    assert state.session._code == "ready"
    state.enqueue.assert_not_called()
    state.queue_factory.assert_not_called()
    state.store_lookup.assert_not_called()
    assert state.queue.list_jobs() == []


def test_sales_sync_cannot_revive_expired_pending_firebase_grant(pending_firebase_sales_sync):
    state = pending_firebase_sales_sync
    state.session._access["session_expires_at"] = int(sessions.time.time()) - 1
    before = state.verifier.call_count
    state.verifier.side_effect = None
    state.verifier.return_value = state.claims
    response = state.client.post("/api/vendas/sync", headers=state.headers, json=sales_sync_request())
    assert response.status_code == 401
    assert response.json() == {"detail": "session_expired"}
    assert state.verifier.call_count == before
    state.enqueue.assert_not_called()
    state.queue_factory.assert_not_called()
    state.store_lookup.assert_not_called()
    assert state.queue.list_jobs() == []
