"""Bling HTTP session and adaptive retry helpers."""

from __future__ import annotations

import random
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend.services.env_config import _env_bool


def _get_bling_session():
    session = requests.Session()
    session.verify = _env_bool("BLING_VERIFY_SSL", True)
    retry = Retry(
        total=3,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


BLING_SESSION = _get_bling_session()


def _bling_retry_after_seconds(resp) -> float:
    """Le Retry-After quando disponivel e retorna segundos validos."""
    if not resp:
        return 0.0
    raw = (resp.headers or {}).get("Retry-After")
    if not raw:
        return 0.0
    try:
        val = float(str(raw).strip())
        return max(0.0, min(val, 60.0))
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

    def wait_turn(self):
        now = time.time()
        elapsed = now - self.last_call_ts
        if elapsed < self.interval:
            jitter = random.uniform(0, min(0.05, self.interval * 0.25))
            time.sleep((self.interval - elapsed) + jitter)
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
        if retry_after and retry_after > 0:
            base = max(base, retry_after)
        self.interval = min(self.max_interval, max(self.interval, base))
        return self.interval + random.uniform(0.05, min(0.6, self.interval * 0.35))

    def on_transient_error(self) -> float:
        self.err_streak += 1
        self.ok_streak = 0
        expo = min(3, self.err_streak)
        self.interval = min(self.max_interval, max(self.interval, 0.25 * (2 ** expo)))
        return self.interval + random.uniform(0.05, min(0.5, self.interval * 0.3))


def _bling_get_with_adaptive_limit(
    url: str,
    *,
    headers: dict,
    params: dict = None,
    timeout: int = 25,
    limiter: _BlingAdaptiveLimiter = None,
    max_attempts: int = 6,
):
    """GET com controle adaptativo de ritmo e retry inteligente para 429/5xx."""
    limiter = limiter or _BlingAdaptiveLimiter()
    last_resp = None

    for tentativa in range(max(1, int(max_attempts))):
        limiter.wait_turn()
        try:
            resp = BLING_SESSION.get(url, headers=headers, params=params, timeout=timeout)
            last_resp = resp
        except requests.RequestException:
            if tentativa >= max_attempts - 1:
                return None
            time.sleep(limiter.on_transient_error())
            continue

        status = resp.status_code
        if status == 429:
            if tentativa >= max_attempts - 1:
                return resp
            retry_after = _bling_retry_after_seconds(resp)
            time.sleep(limiter.on_throttle(retry_after))
            continue

        if status in (500, 502, 503, 504):
            if tentativa >= max_attempts - 1:
                return resp
            time.sleep(limiter.on_transient_error())
            continue

        limiter.on_success()
        return resp

    return last_resp
