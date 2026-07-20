from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

import pytest
import requests
from fastapi import HTTPException

from backend.services import bling, bling_oauth, bling_vendas


@dataclass
class FakeResponse:
    status_code: int
    payload: object = field(default_factory=dict)
    headers: dict = field(default_factory=dict)

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, *, get_results=None, post_results=None):
        self.get_results = list(get_results or [])
        self.post_results = list(post_results or [])
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        result = self.get_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        result = self.post_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_bling_session_adapter_nao_repete_status_http():
    session = bling._get_bling_session()

    assert session.adapters["https://"].max_retries.total == 0
    assert session.adapters["http://"].max_retries.total == 0


def test_get_limita_tentativas_timeout_e_retry_after(monkeypatch):
    fake = FakeSession(get_results=[FakeResponse(429, headers={"Retry-After": "99"}) for _ in range(3)])
    sleeps = []
    monkeypatch.setattr(bling, "BLING_SESSION", fake)
    monkeypatch.setattr(bling, "_bling_cancelable_sleep", lambda seconds, **kwargs: sleeps.append(seconds))

    response = bling._bling_get_with_adaptive_limit(
        "https://api.invalid/bling",
        headers={"Authorization": "Bearer fixture"},
        max_attempts=9,
    )

    assert response.status_code == 429
    assert len(fake.get_calls) == 3
    assert all(call[1]["timeout"] == (5.0, 20.0) for call in fake.get_calls)
    assert bling._bling_retry_after_seconds(FakeResponse(429, headers={"Retry-After": "99"})) == 30.0
    assert len(sleeps) >= 2


def test_retry_after_e_timeout_total_nao_ultrapassam_limites(monkeypatch):
    limiter = bling._BlingAdaptiveLimiter()
    monkeypatch.setattr(bling.random, "uniform", lambda *_args: 0.6)

    assert limiter.on_throttle(30) <= 30
    connect, read = bling._bling_bounded_timeout((5, 20), 0.1)
    assert connect + read <= 0.1 + 1e-9


def test_get_interrompe_resposta_gotejada_no_deadline_total(monkeypatch):
    class DripResponse(requests.Response):
        def __init__(self):
            super().__init__()
            self.status_code = 200
            self._content = False
            self._content_consumed = False

        def iter_content(self, chunk_size=1, decode_unicode=False):
            for _ in range(20):
                time.sleep(0.04)
                yield b"x"

    fake = FakeSession(get_results=[DripResponse()])
    monkeypatch.setattr(bling, "BLING_SESSION", fake)
    started = time.monotonic()

    response = bling._bling_get_with_adaptive_limit(
        "https://api.invalid/bling",
        headers={},
        max_attempts=1,
        total_timeout=0.1,
        limiter=bling._BlingAdaptiveLimiter(min_interval=0, start_interval=0),
    )

    assert response is None
    assert time.monotonic() - started < 0.25


def test_get_gotejado_real_normaliza_fechamento_do_socket_como_timeout(monkeypatch):
    class DripHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "200")
            self.end_headers()
            try:
                for _ in range(50):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), DripHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    session = requests.Session()
    monkeypatch.setattr(bling, "BLING_SESSION", session)
    started = time.monotonic()
    elapsed = None
    try:
        response = bling._bling_get_with_adaptive_limit(
            f"http://127.0.0.1:{server.server_port}/drip",
            headers={},
            max_attempts=1,
            total_timeout=0.1,
            limiter=bling._BlingAdaptiveLimiter(min_interval=0, start_interval=0),
        )
        elapsed = time.monotonic() - started
    finally:
        session.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)

    assert response is None
    assert elapsed is not None and elapsed < 0.25


def test_espera_de_retry_pode_ser_cancelada(monkeypatch):
    fake = FakeSession(get_results=[requests.ConnectionError("offline")])
    monkeypatch.setattr(bling, "BLING_SESSION", fake)

    class Cancelado(RuntimeError):
        pass

    chamadas = 0

    def cancelar():
        nonlocal chamadas
        chamadas += 1
        if chamadas >= 2:
            raise Cancelado("cancelado")

    with pytest.raises(Cancelado):
        bling._bling_get_with_adaptive_limit(
            "https://api.invalid/bling",
            headers={},
            cancel_callback=cancelar,
        )

    assert len(fake.get_calls) == 1


@pytest.mark.parametrize(
    ("response", "expected_status"),
    [
        (FakeResponse(429, {"error": "rate"}, {"Retry-After": "7"}), 429),
        (FakeResponse(503, {"error": "down"}), 503),
        (FakeResponse(200, ValueError("json invalido")), 502),
    ],
)
def test_refresh_post_unico_preserva_status(monkeypatch, response, expected_status):
    fake = FakeSession(post_results=[response])
    monkeypatch.setattr(bling_oauth, "BLING_SESSION", fake)

    with pytest.raises(HTTPException) as exc:
        bling_oauth.exchange_bling_refresh_token("cliente", "segredo", "refresh-fixture")

    assert exc.value.status_code == expected_status
    assert len(fake.post_calls) == 1
    assert fake.post_calls[0][1]["timeout"] == (5, 20)


def _patch_sales_enrichments(monkeypatch):
    monkeypatch.setattr(bling_vendas, "_bling_map_canais_venda_basico", lambda token: ({}, 200))
    monkeypatch.setattr(bling_vendas, "_bling_map_lojas_virtuais", lambda token: ({}, 200))
    monkeypatch.setattr(bling_vendas, "_bling_map_unidades_por_canais", lambda token: ({}, 200))


def test_vendas_aborta_quando_detalhe_obrigatorio_falha(monkeypatch):
    _patch_sales_enrichments(monkeypatch)
    responses = iter(
        [
            FakeResponse(200, {"data": [{"id": 10}]}),
            FakeResponse(503, {"error": "down"}),
        ]
    )
    monkeypatch.setattr(bling_vendas, "_bling_get_with_adaptive_limit", lambda *args, **kwargs: next(responses))

    with pytest.raises(HTTPException) as exc:
        bling_vendas._bling_listar_vendas("token", "2026-01-01", "2026-01-01", "Loja")

    assert exc.value.status_code == 503


def test_vendas_rejeita_json_invalido_com_502(monkeypatch):
    _patch_sales_enrichments(monkeypatch)
    monkeypatch.setattr(
        bling_vendas,
        "_bling_get_with_adaptive_limit",
        lambda *args, **kwargs: FakeResponse(200, ValueError("json invalido")),
    )

    with pytest.raises(HTTPException) as exc:
        bling_vendas._bling_listar_vendas("token", "2026-01-01", "2026-01-01", "Loja")

    assert exc.value.status_code == 502


def test_vendas_preserva_429_e_retry_after(monkeypatch):
    _patch_sales_enrichments(monkeypatch)
    monkeypatch.setattr(
        bling_vendas,
        "_bling_get_with_adaptive_limit",
        lambda *args, **kwargs: FakeResponse(429, {"error": "rate"}, {"Retry-After": "9"}),
    )

    with pytest.raises(HTTPException) as exc:
        bling_vendas._bling_listar_vendas("token", "2026-01-01", "2026-01-01", "Loja")

    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "9"}


@pytest.mark.parametrize(
    "detail",
    [
        {"data": "2026-01-01"},
        {"data": "2026-01-01", "itens": []},
        {"data": "2026-01-01", "itens": "parcial"},
        {"data": "2026-01-01", "itens": [None]},
        {"data": "2026-01-01", "itens": [{"quantidade": "invalida", "valor": 1}]},
    ],
)
def test_vendas_rejeita_detalhe_200_parcial_com_502(monkeypatch, detail):
    _patch_sales_enrichments(monkeypatch)
    responses = iter(
        [
            FakeResponse(200, {"data": [{"id": 10}]}),
            FakeResponse(200, {"data": detail}),
        ]
    )
    monkeypatch.setattr(
        bling_vendas,
        "_bling_get_with_adaptive_limit",
        lambda *args, **kwargs: next(responses),
    )

    with pytest.raises(HTTPException) as exc:
        bling_vendas._bling_listar_vendas("token", "2026-01-01", "2026-01-01", "Loja")

    assert exc.value.status_code == 502


def test_detalhe_nf_preserva_retry_after(monkeypatch):
    monkeypatch.setattr(
        bling_vendas,
        "_bling_get_with_adaptive_limit",
        lambda *args, **kwargs: FakeResponse(429, {}, {"Retry-After": "11"}),
    )

    with pytest.raises(HTTPException) as exc:
        bling_vendas._bling_obter_detalhes_nf("token", "nf-1")

    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "11"}


def test_nota_entrada_aborta_quando_detalhe_obrigatorio_falha(monkeypatch):
    monkeypatch.setattr(
        bling_vendas,
        "_bling_get_with_adaptive_limit",
        lambda *args, **kwargs: FakeResponse(200, {"data": [{"id": 33, "itens": []}]}),
    )
    monkeypatch.setattr(bling_vendas, "_bling_obter_detalhes_nf", lambda *args, **kwargs: (None, 503))

    with pytest.raises(HTTPException) as exc:
        bling_vendas._bling_listar_notas_entrada(
            "token",
            "2026-01-01",
            "2026-01-01",
            {},
        )

    assert exc.value.status_code == 503


def test_nota_entrada_sempre_detalha_mesmo_quando_listagem_traz_itens(monkeypatch):
    responses = iter(
        [
            FakeResponse(200, {"data": [{"id": 33, "dataEmissao": "2026-01-01", "itens": [{"codigo": "PARCIAL"}]}]}),
            FakeResponse(200, {"data": []}),
        ]
    )
    detail_calls = []
    monkeypatch.setattr(
        bling_vendas,
        "_bling_get_with_adaptive_limit",
        lambda *args, **kwargs: next(responses),
    )

    def detail(*_args, **_kwargs):
        detail_calls.append(1)
        return {
            "dataEmissao": "2026-01-01",
            "itens": [{"codigo": "COMPLETO", "quantidade": 1, "valor": 2}],
        }, 200

    monkeypatch.setattr(bling_vendas, "_bling_obter_detalhes_nf", detail)

    notes, items, status = bling_vendas._bling_listar_notas_entrada(
        "token", "2026-01-01", "2026-01-01", {}
    )

    assert status == 200
    assert detail_calls == [1]
    assert notes[0]["_detalhe_validado"] is True
    assert [item["sku"] for item in items] == ["COMPLETO"]


def test_fallback_saida_rejeita_detalhe_sem_itens(monkeypatch):
    monkeypatch.setattr(
        bling_vendas,
        "_bling_get_with_adaptive_limit",
        lambda *args, **kwargs: FakeResponse(200, {"data": [{"id": 44, "dataEmissao": "2026-01-01"}]}),
    )
    monkeypatch.setattr(
        bling_vendas,
        "_bling_obter_detalhes_nf",
        lambda *_args, **_kwargs: ({"dataEmissao": "2026-01-01"}, 200),
    )

    with pytest.raises(HTTPException) as exc:
        bling_vendas._bling_listar_vendas_fallback_nf_saida(
            "token", "2026-01-01", "2026-01-01", "Loja"
        )

    assert exc.value.status_code == 502


def test_fallback_403_e_permitido_mas_registrado(monkeypatch):
    monkeypatch.setattr(
        bling_vendas,
        "_bling_get_with_adaptive_limit",
        lambda *args, **kwargs: FakeResponse(403, {"error": {"type": "insufficient_scope"}}),
    )
    logs = []

    registros, status = bling_vendas._bling_listar_vendas_fallback_nf_saida(
        "token",
        "2026-01-01",
        "2026-01-01",
        "Loja",
        log_callback=logs.append,
    )

    assert registros == []
    assert status == 403
    assert any("403" in mensagem for mensagem in logs)
