from __future__ import annotations

import inspect

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.routers import firebase_provisioning as router_module
from backend.routers.firebase_provisioning import (
    MAX_MULTIPART_BODY_BYTES,
    FirebaseProvisioningRouterConfig,
    create_firebase_provisioning_router,
)
from backend.services import firebase_provisioning as service


RESPONSE_KEYS = {
    "success",
    "configured",
    "ready",
    "source",
    "code",
    "restart_required",
    "can_migrate",
    "replacement_required",
}


def _success_result() -> service.FirebaseProvisioningResult:
    return service.FirebaseProvisioningResult(
        status_code=200,
        payload={
            "success": True,
            "configured": True,
            "ready": False,
            "source": "canonical",
            "code": "imported",
            "restart_required": True,
            "can_migrate": False,
            "replacement_required": False,
        },
    )


def _test_app(tmp_path, *, authorize, receive_counter=None):
    base = tmp_path / "runtime"
    info = base / "info"
    base.mkdir(parents=True, exist_ok=True)
    app = FastAPI()
    router = create_firebase_provisioning_router(FirebaseProvisioningRouterConfig(
        base_dir=str(base),
        info_dir=str(info),
        authorize_recovery=authorize,
    ))
    app.include_router(router)
    if receive_counter is None:
        return app, router

    async def receive_audited_app(scope, receive, send):
        if scope.get("type") != "http":
            await app(scope, receive, send)
            return

        async def audited_receive():
            receive_counter["calls"] = int(receive_counter.get("calls") or 0) + 1
            return await receive()

        await app(scope, audited_receive, send)

    return receive_audited_app, router


def _assert_sanitized(response, *, status_code: int, code: str):
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == RESPONSE_KEYS
    assert payload["success"] is False
    assert payload["ready"] is False
    assert payload["code"] == code


def test_upload_route_has_no_fastapi_body_parameter(tmp_path):
    _app, router = _test_app(tmp_path, authorize=lambda _authorization: {})
    route = next(item for item in router.routes if item.path.endswith("/import"))

    assert "file" not in inspect.signature(route.endpoint).parameters
    assert route.dependant.body_params == []


def test_authorization_rejection_happens_before_any_body_read(tmp_path):
    counter = {"calls": 0}

    def deny(_authorization):
        raise HTTPException(status_code=403, detail="secret-path-and-credential-must-not-escape")

    app, _router = _test_app(tmp_path, authorize=deny, receive_counter=counter)
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.post(
        "/api/admin/firebase-provisioning/import",
        headers={
            "Authorization": "Bearer denied",
            "Content-Type": "multipart/form-data; boundary=guard",
            "Content-Length": "16",
        },
        content=b"must-not-be-read",
    )

    _assert_sanitized(response, status_code=403, code="forbidden")
    assert counter["calls"] == 0
    assert "secret-path" not in response.text
    assert "must-not-be-read" not in response.text


def test_content_length_is_required_and_checked_before_stream(tmp_path):
    counter = {"calls": 0}
    app, _router = _test_app(
        tmp_path,
        authorize=lambda _authorization: {},
        receive_counter=counter,
    )
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.post(
        "/api/admin/firebase-provisioning/import",
        headers={
            "Authorization": "Bearer test",
            "Content-Type": "multipart/form-data; boundary=guard",
            "Content-Length": str(MAX_MULTIPART_BODY_BYTES + 1),
        },
        content=b"small-body",
    )

    _assert_sanitized(response, status_code=413, code="oversize")
    assert counter["calls"] == 0


def test_real_stream_limit_rejects_more_than_declared(tmp_path):
    app, _router = _test_app(tmp_path, authorize=lambda _authorization: {})
    client = TestClient(app, client=("127.0.0.1", 50000))

    def chunks():
        yield b"x" * 32
        yield b"y"

    response = client.post(
        "/api/admin/firebase-provisioning/import",
        headers={
            "Authorization": "Bearer test",
            "Content-Type": "multipart/form-data; boundary=guard",
            "Content-Length": "32",
        },
        content=chunks(),
    )

    _assert_sanitized(response, status_code=413, code="oversize")


def test_multipart_accepts_exactly_one_file_field(tmp_path, monkeypatch):
    imported = []

    def import_stub(raw, **kwargs):
        imported.append((raw, kwargs))
        return _success_result()

    monkeypatch.setattr(service, "firebase_provisioning_import", import_stub)
    app, _router = _test_app(tmp_path, authorize=lambda _authorization: {})
    client = TestClient(app, client=("127.0.0.1", 50000))

    wrong_field = client.post(
        "/api/admin/firebase-provisioning/import",
        headers={"Authorization": "Bearer test"},
        files={"credential": ("private-name.json", b"wrong", "application/json")},
    )
    duplicate = client.post(
        "/api/admin/firebase-provisioning/import",
        headers={"Authorization": "Bearer test"},
        files=[
            ("file", ("first-private-name.json", b"first", "application/json")),
            ("file", ("second-private-name.json", b"second", "application/json")),
        ],
    )
    extra_field = client.post(
        "/api/admin/firebase-provisioning/import",
        headers={"Authorization": "Bearer test"},
        data={"note": "private-note"},
        files={"file": ("third-private-name.json", b"third", "application/json")},
    )

    for response in (wrong_field, duplicate, extra_field):
        _assert_sanitized(response, status_code=400, code="invalid_upload")
        assert "private-name" not in response.text
        assert "private-note" not in response.text
    assert imported == []


def test_valid_single_file_reaches_service_without_filename(tmp_path, monkeypatch):
    imported = []

    def import_stub(raw, **kwargs):
        imported.append((raw, kwargs))
        return _success_result()

    monkeypatch.setattr(service, "firebase_provisioning_import", import_stub)
    app, _router = _test_app(tmp_path, authorize=lambda _authorization: {})
    client = TestClient(app, client=("127.0.0.1", 50000))
    raw = b'{"synthetic":"payload"}'

    response = client.post(
        "/api/admin/firebase-provisioning/import?replace=true",
        headers={"Authorization": "Bearer test"},
        files={"file": ("customer-secret-name.json", raw, "application/json")},
    )

    assert response.status_code == 200
    assert set(response.json()) == RESPONSE_KEYS
    assert imported and imported[0][0] == raw
    assert imported[0][1]["replace"] is True
    assert "customer-secret-name" not in response.text
    assert raw.decode("utf-8") not in response.text


def test_malformed_multipart_never_echoes_parser_or_body_details(tmp_path):
    app, _router = _test_app(tmp_path, authorize=lambda _authorization: {})
    client = TestClient(app, client=("127.0.0.1", 50000))
    body = b"private-parser-canary"

    response = client.post(
        "/api/admin/firebase-provisioning/import",
        headers={
            "Authorization": "Bearer test",
            "Content-Type": "multipart/form-data; boundary=missing-boundary-in-body",
            "Content-Length": str(len(body)),
        },
        content=body,
    )

    _assert_sanitized(response, status_code=400, code="invalid_upload")
    assert "private-parser-canary" not in response.text
    assert "boundary" not in response.text
