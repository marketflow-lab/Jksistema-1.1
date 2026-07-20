"""Cliente HTTP do gateway público do WhatsApp.

Este módulo não conhece estado global, filas ou persistência do bridge. Isso o
mantém testável e evita que chamadas HTTP sejam misturadas à orquestração.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import requests
from fastapi import HTTPException


def normalize_worker_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlparse(text)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not parsed.hostname or (parsed.scheme != "https" and not (local and parsed.scheme == "http")):
        raise HTTPException(status_code=400, detail="Use uma URL HTTPS do Worker (HTTP e aceito apenas em localhost).")
    return text


def gateway_headers(config: dict[str, Any]) -> dict[str, str]:
    token = str(config.get("bridge_token") or "").strip()
    return {"authorization": f"Bearer {token}", "accept": "application/json"}


def _machine_scoped_path(config: dict[str, Any], method: str, path: str) -> str:
    """Bind sensitive bridge reads to the configured local machine.

    The machine id is carried in the query string because these endpoints are
    GETs.  Any caller-supplied value is replaced by the authenticated local
    configuration so a reused bridge token cannot select another machine.
    """

    raw_path = str(path or "")
    parsed = urlsplit(raw_path)
    sensitive_read = parsed.path == "/bridge/status" or parsed.path.startswith("/bridge/media/")
    if method.upper() != "GET" or not sensitive_read:
        return raw_path
    machine_id = str(config.get("machine_id") or "").strip()
    if not machine_id:
        raise RuntimeError("machine_id_missing")
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "machine_id"]
    query.append(("machine_id", machine_id))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def gateway_request(
    config: dict[str, Any],
    method: str,
    path: str,
    *,
    payload: Optional[dict[str, Any]] = None,
    timeout: int = 15,
    stream: bool = False,
) -> requests.Response:
    worker_url = normalize_worker_url(config.get("worker_url"))
    token = str(config.get("bridge_token") or "").strip()
    if not worker_url or not token:
        raise RuntimeError("worker_url_or_bridge_token_missing")
    safe_path = _machine_scoped_path(config, method, path)
    response = requests.request(
        method.upper(),
        worker_url + safe_path,
        headers=gateway_headers(config),
        json=payload,
        timeout=timeout,
        stream=stream,
    )
    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:500]
        raise RuntimeError(f"gateway_http_{response.status_code}: {detail}")
    return response


def gateway_json(
    config: dict[str, Any],
    method: str,
    path: str,
    payload: Optional[dict[str, Any]] = None,
    timeout: int = 15,
) -> dict[str, Any]:
    response = gateway_request(config, method, path, payload=payload, timeout=timeout)
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("gateway_invalid_json")
    return value


__all__ = ["gateway_headers", "gateway_json", "gateway_request", "normalize_worker_url"]
