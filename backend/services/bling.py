"""Bling HTTP session and adaptive retry helpers."""

from __future__ import annotations

import random
import threading
import time
from typing import Callable

import requests
from requests.adapters import HTTPAdapter

from backend.services.env_config import _env_bool


def _get_bling_session():
    session = requests.Session()
    session.verify = _env_bool("BLING_VERIFY_SSL", True)
    # A politica de retry da Bling pertence exclusivamente ao helper GET
    # abaixo. Em especial, POSTs OAuth nunca podem ser repetidos pelo adapter.
    adapter = HTTPAdapter(max_retries=0)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


BLING_SESSION = _get_bling_session()
BLING_ACCOUNT_MIN_INTERVAL_SECONDS = 0.35
_BLING_ACCOUNT_LIMIT_LOCK = threading.Lock()
_BLING_ACCOUNT_NEXT_CALL: dict[str, float] = {}


def _bling_wait_account_turn(
    _headers: dict | None,
    *,
    cancel_callback: Callable[[], object] | None = None,
    deadline: float | None = None,
) -> None:
    """Serialize every Bling GET start under a conservative process-wide limit.

    The official quota is account-wide and OAuth tokens rotate. The desktop
    runtime does not have a trustworthy immutable Bling account id before a
    request, so a single bucket is deliberately stricter than the provider's
    per-account quota and cannot split one account across token generations.
    """

    key = "bling-process-wide"
    now = time.monotonic()
    with _BLING_ACCOUNT_LIMIT_LOCK:
        target = max(now, _BLING_ACCOUNT_NEXT_CALL.get(key, now))
        _BLING_ACCOUNT_NEXT_CALL[key] = target + BLING_ACCOUNT_MIN_INTERVAL_SECONDS
        if len(_BLING_ACCOUNT_NEXT_CALL) > 256:
            stale_before = now - 5 * 60
            for stale_key, next_call in list(_BLING_ACCOUNT_NEXT_CALL.items()):
                if next_call < stale_before:
                    _BLING_ACCOUNT_NEXT_CALL.pop(stale_key, None)
    _bling_cancelable_sleep(
        max(0.0, target - now),
        cancel_callback=cancel_callback,
        deadline=deadline,
    )


def _bling_retry_after_seconds(resp) -> float:
    """Le Retry-After quando disponivel e retorna segundos validos."""
    # requests.Response e falso para HTTP >= 400; justamente os 429 que
    # precisam ter o Retry-After lido. Teste identidade, nao truthiness.
    if resp is None:
        return 0.0
    raw = (resp.headers or {}).get("Retry-After")
    if not raw:
        return 0.0
    try:
        val = float(str(raw).strip())
        return max(0.0, min(val, 30.0))
    except Exception:
        return 0.0


class _BlingAdaptiveLimiter:
    """Rate limiter adaptativo para reduzir 429 e acelerar quando a API esta saudavel."""

    def __init__(self, min_interval: float = 0.03, max_interval: float = 3.0, start_interval: float = 0.08):
        self.min_interval = max(0.0, float(min_interval))
        self.max_interval = max(self.min_interval, float(max_interval))
        self.interval = min(self.max_interval, max(self.min_interval, float(start_interval)))
        self.last_call_ts = 0.0
        self.ok_streak = 0
        self.err_streak = 0

    def wait_turn(
        self,
        *,
        cancel_callback: Callable[[], object] | None = None,
        deadline: float | None = None,
    ):
        now = time.time()
        elapsed = now - self.last_call_ts
        if elapsed < self.interval:
            jitter = random.uniform(0, min(0.05, self.interval * 0.25))
            _bling_cancelable_sleep(
                (self.interval - elapsed) + jitter,
                cancel_callback=cancel_callback,
                deadline=deadline,
            )
        self.last_call_ts = time.time()

    def on_success(self):
        self.ok_streak += 1
        self.err_streak = 0
        if self.ok_streak >= 4:
            self.interval = max(self.min_interval, self.interval * 0.9)
            self.ok_streak = 0

    def on_throttle(self, retry_after: float = 0.0) -> float:
        self.err_streak += 1
        self.ok_streak = 0
        expo = min(4, self.err_streak)
        base = max(self.interval * 1.8, 0.4 * (2 ** (expo - 1)))
        self.interval = min(self.max_interval, max(self.interval, base))
        retry_delay = min(30.0, max(0.0, float(retry_after or 0.0)))
        wait_base = max(self.interval, retry_delay)
        return min(
            30.0,
            wait_base + random.uniform(0.05, min(0.6, max(0.05, wait_base * 0.35))),
        )

    def on_transient_error(self) -> float:
        self.err_streak += 1
        self.ok_streak = 0
        expo = min(3, self.err_streak)
        self.interval = min(self.max_interval, max(self.interval, 0.25 * (2 ** expo)))
        return self.interval + random.uniform(0.05, min(0.5, self.interval * 0.3))


def _bling_cancelable_sleep(
    seconds: float,
    *,
    cancel_callback: Callable[[], object] | None = None,
    deadline: float | None = None,
) -> None:
    """Espera em fatias curtas para cancelamento e limite total serem observados."""
    remaining = max(0.0, float(seconds or 0.0))
    while remaining > 0:
        if callable(cancel_callback):
            cancel_callback()
        if deadline is not None:
            remaining_deadline = deadline - time.monotonic()
            if remaining_deadline <= 0:
                return
        else:
            remaining_deadline = remaining
        slice_seconds = min(0.25, remaining, remaining_deadline)
        if slice_seconds <= 0:
            return
        time.sleep(slice_seconds)
        remaining -= slice_seconds


def _bling_bounded_timeout(timeout, remaining: float) -> tuple[float, float]:
    """Normaliza qualquer timeout legado para connect<=5s/read<=20s e deadline."""
    if isinstance(timeout, (tuple, list)) and len(timeout) >= 2:
        connect_requested, read_requested = timeout[0], timeout[1]
    else:
        connect_requested, read_requested = 5.0, timeout
    try:
        connect_timeout = min(5.0, max(0.1, float(connect_requested or 5.0)))
    except (TypeError, ValueError):
        connect_timeout = 5.0
    try:
        read_timeout = min(20.0, max(0.1, float(read_requested or 20.0)))
    except (TypeError, ValueError):
        read_timeout = 20.0

    remaining = max(0.01, float(remaining or 0.01))
    if remaining < 0.2:
        connect_timeout = max(0.001, remaining / 2.0)
        read_timeout = max(0.001, remaining - connect_timeout)
        return connect_timeout, read_timeout
    connect_timeout = min(connect_timeout, max(0.1, remaining - 0.1))
    read_budget = max(0.1, remaining - connect_timeout)
    read_timeout = min(read_timeout, read_budget)
    return connect_timeout, read_timeout


def _bling_consume_response_before_deadline(
    response,
    *,
    deadline: float,
    cancel_callback: Callable[[], object] | None = None,
) -> None:
    """Consome o corpo com deadline absoluto, inclusive em resposta gotejada."""

    if not isinstance(response, requests.Response) or response._content_consumed:
        return
    expired = threading.Event()

    def expire_response() -> None:
        expired.set()
        try:
            response.close()
        except Exception:
            pass

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        expire_response()
        raise requests.Timeout("Bling GET total deadline exceeded")
    timer = threading.Timer(remaining, expire_response)
    timer.daemon = True
    timer.start()
    chunks: list[bytes] = []
    try:
        raw_read1 = getattr(getattr(response, "raw", None), "read1", None)
        if callable(raw_read1):
            def read_chunks():
                while True:
                    chunk = raw_read1(64 * 1024, decode_content=True)
                    if not chunk:
                        return
                    yield chunk
        else:
            # Respostas sinteticas/test doubles podem nao expor ``raw``.
            # Um byte evita que um iterador legado espere encher um bloco
            # grande enquanto o servidor mantem a conexao gotejando.
            def read_chunks():
                yield from response.iter_content(chunk_size=1)

        for chunk in read_chunks():
            if callable(cancel_callback):
                cancel_callback()
            if expired.is_set() or time.monotonic() >= deadline:
                raise requests.Timeout("Bling GET total deadline exceeded")
            if chunk:
                chunks.append(chunk)
        if expired.is_set() or time.monotonic() >= deadline:
            raise requests.Timeout("Bling GET total deadline exceeded")
        response._content = b"".join(chunks)
        response._content_consumed = True
    except BaseException as exc:
        try:
            response.close()
        except Exception:
            pass
        # Fechar uma ``requests.Response`` em outra thread enquanto urllib3/
        # http.client ainda le o socket pode produzir erros internos que nao
        # herdam de RequestException (por exemplo, AttributeError). Quando foi
        # o nosso deadline que fechou a resposta, normalize o erro para que a
        # camada de retry devolva indisponibilidade, nunca um 500 inesperado.
        if (
            expired.is_set()
            and isinstance(exc, Exception)
            and not isinstance(exc, requests.Timeout)
        ):
            raise requests.Timeout("Bling GET total deadline exceeded") from exc
        raise
    finally:
        timer.cancel()


def _bling_get_with_adaptive_limit(
    url: str,
    *,
    headers: dict,
    params: dict = None,
    timeout: int | tuple[float, float] = (5, 20),
    limiter: _BlingAdaptiveLimiter = None,
    max_attempts: int = 3,
    cancel_callback: Callable[[], object] | None = None,
    total_timeout: float = 60.0,
):
    """GET com no maximo 3 tentativas e 60s totais, incluindo esperas."""
    limiter = limiter or _BlingAdaptiveLimiter()
    last_resp = None
    attempts = min(3, max(1, int(max_attempts or 1)))
    deadline = time.monotonic() + min(60.0, max(0.1, float(total_timeout or 60.0)))

    for tentativa in range(attempts):
        if callable(cancel_callback):
            cancel_callback()
        if time.monotonic() >= deadline:
            break
        limiter.wait_turn(cancel_callback=cancel_callback, deadline=deadline)
        _bling_wait_account_turn(
            headers,
            cancel_callback=cancel_callback,
            deadline=deadline,
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            resp = BLING_SESSION.get(
                url,
                headers=headers,
                params=params,
                timeout=_bling_bounded_timeout(timeout, remaining),
                stream=True,
            )
            _bling_consume_response_before_deadline(
                resp,
                deadline=deadline,
                cancel_callback=cancel_callback,
            )
            last_resp = resp
        except requests.RequestException:
            if tentativa >= attempts - 1 or time.monotonic() >= deadline:
                return None
            _bling_cancelable_sleep(
                limiter.on_transient_error(),
                cancel_callback=cancel_callback,
                deadline=deadline,
            )
            continue

        status = resp.status_code
        if status == 429:
            if tentativa >= attempts - 1:
                return resp
            retry_after = _bling_retry_after_seconds(resp)
            _bling_cancelable_sleep(
                limiter.on_throttle(retry_after),
                cancel_callback=cancel_callback,
                deadline=deadline,
            )
            continue

        if status in (500, 502, 503, 504):
            if tentativa >= attempts - 1:
                return resp
            _bling_cancelable_sleep(
                limiter.on_transient_error(),
                cancel_callback=cancel_callback,
                deadline=deadline,
            )
            continue

        limiter.on_success()
        return resp

    return last_resp
