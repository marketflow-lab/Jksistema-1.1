import pytest
from fastapi import HTTPException

from backend.services.whatsapp import gateway


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, text="") -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = text

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_normalize_worker_url_accepts_https_and_local_http() -> None:
    assert gateway.normalize_worker_url("https://worker.example/") == "https://worker.example"
    assert gateway.normalize_worker_url("http://localhost:8787/") == "http://localhost:8787"
    assert gateway.normalize_worker_url(None) == ""


def test_normalize_worker_url_rejects_external_http() -> None:
    with pytest.raises(HTTPException) as error:
        gateway.normalize_worker_url("http://worker.example")
    assert error.value.status_code == 400


def test_gateway_request_forwards_the_complete_http_contract(monkeypatch) -> None:
    captured = {}
    response = FakeResponse(payload={"success": True})

    def fake_request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return response

    monkeypatch.setattr(gateway.requests, "request", fake_request)
    config = {"worker_url": "https://worker.example/", "bridge_token": "secret"}

    result = gateway.gateway_request(
        config,
        "post",
        "/bridge/test",
        payload={"value": 1},
        timeout=7,
        stream=True,
    )

    assert result is response
    assert captured == {
        "method": "POST",
        "url": "https://worker.example/bridge/test",
        "headers": {"authorization": "Bearer secret", "accept": "application/json"},
        "json": {"value": 1},
        "timeout": 7,
        "stream": True,
    }


def test_gateway_request_requires_url_and_token() -> None:
    with pytest.raises(RuntimeError, match="worker_url_or_bridge_token_missing"):
        gateway.gateway_request({}, "GET", "/bridge/status")


@pytest.mark.parametrize(
    ("response", "detail"),
    [
        (FakeResponse(status_code=401, payload={"error": "denied"}), "denied"),
        (FakeResponse(status_code=502, payload=ValueError("invalid json"), text="bad gateway"), "bad gateway"),
    ],
)
def test_gateway_request_reports_http_failures(monkeypatch, response, detail) -> None:
    monkeypatch.setattr(gateway.requests, "request", lambda *args, **kwargs: response)
    config = {"worker_url": "https://worker.example", "bridge_token": "secret", "machine_id": "machine-1"}

    with pytest.raises(RuntimeError, match=f"gateway_http_{response.status_code}.*{detail}"):
        gateway.gateway_request(config, "GET", "/bridge/status")


@pytest.mark.parametrize("path", ["/bridge/status", "/bridge/media/wamid.1"])
def test_gateway_request_binds_sensitive_gets_to_configured_machine(monkeypatch, path) -> None:
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return FakeResponse(payload={"success": True})

    monkeypatch.setattr(gateway.requests, "request", fake_request)
    config = {"worker_url": "https://worker.example", "bridge_token": "secret", "machine_id": "machine-local"}

    gateway.gateway_request(config, "GET", path + "?machine_id=attacker&keep=1")

    assert "machine_id=machine-local" in captured["url"]
    assert "machine_id=attacker" not in captured["url"]
    assert "keep=1" in captured["url"]


def test_gateway_request_fails_closed_without_machine_for_sensitive_get(monkeypatch) -> None:
    monkeypatch.setattr(gateway.requests, "request", lambda *_args, **_kwargs: pytest.fail("HTTP must not be called"))
    config = {"worker_url": "https://worker.example", "bridge_token": "secret"}

    with pytest.raises(RuntimeError, match="machine_id_missing"):
        gateway.gateway_request(config, "GET", "/bridge/media/wamid.1")


def test_gateway_json_requires_an_object(monkeypatch) -> None:
    monkeypatch.setattr(gateway, "gateway_request", lambda *args, **kwargs: FakeResponse(payload=["invalid"]))

    with pytest.raises(RuntimeError, match="gateway_invalid_json"):
        gateway.gateway_json({}, "GET", "/bridge/status")


def test_gateway_json_returns_object(monkeypatch) -> None:
    expected = {"success": True}
    monkeypatch.setattr(gateway, "gateway_request", lambda *args, **kwargs: FakeResponse(payload=expected))

    assert gateway.gateway_json({}, "GET", "/bridge/status") is expected
