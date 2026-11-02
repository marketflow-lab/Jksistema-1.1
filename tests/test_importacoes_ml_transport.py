import pytest
from urllib3.util import Timeout

from backend.services import central_accounts_client as desktop, mercadolivre_http
from test_central_accounts_desktop import client, clean_context


@pytest.mark.parametrize("timeout,expected", [
    (12, (3.05, 12)), ((2, 5), (2, 5)), (90, (3.05, 35)),
])
def test_ml_item_query_preserves_caller_timeout_and_central_ceiling(monkeypatch, timeout, expected):
    value, transport = client()
    desktop._current.set(value)
    marker = value.stores()[0]["integracoes"]["mercadolivre"]["access_token"]
    monkeypatch.setattr(mercadolivre_http, "_ml_http_obter_session", lambda *_args, **_kwargs:
                        pytest.fail("A conexão central não deve consultar o provedor diretamente."))
    response = mercadolivre_http._ml_http_request(
        "tenant-a", "Shop", marker, "GET", "https://api.mercadolibre.com/items/MLB111",
        params={"include_attributes": "all"}, timeout=timeout,
    )
    assert response.json() == {"ok": True}
    assert len(transport.calls) == 1
    method, url, request = transport.calls[0]
    assert method == "POST" and url.startswith("https://auth.example.test/")
    assert request["timeout"] == expected
    assert request["json"]["path"] == "/items/MLB111"
    assert request["json"]["params"] == {"include_attributes": "all"}
    assert "opaque-session" not in str(value.public_stores())


def test_ml_item_query_bounds_timeout_object_without_mutating_it():
    value, transport = client()
    desktop._current.set(value)
    marker = value.stores()[0]["integracoes"]["mercadolivre"]["access_token"]
    requested = Timeout(total=8, connect=2, read=6)
    mercadolivre_http._ml_http_request(
        "tenant-a", "Shop", marker, "GET", "https://api.mercadolibre.com/items/MLB111",
        timeout=requested,
    )
    actual = transport.calls[0][2]["timeout"]
    assert actual is not requested
    assert actual.total <= 8 and actual.connect_timeout <= 2 and actual.read_timeout <= 6
    assert requested.total == 8 and requested.read_timeout == 6
