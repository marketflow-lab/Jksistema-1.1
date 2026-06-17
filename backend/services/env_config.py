"""Environment and redirect configuration helpers."""

from __future__ import annotations

import os
from typing import Any, Optional

import requests
from dotenv import dotenv_values


BASE_DIR = os.getcwd()
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8001/auth/callback"
REDIRECT_URI = os.getenv("JK_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()


def configure_env_context(
    *,
    base_dir: str | None = None,
    default_redirect_uri: str | None = None,
    redirect_uri: str | None = None,
) -> None:
    global BASE_DIR, DEFAULT_REDIRECT_URI, REDIRECT_URI
    if base_dir:
        BASE_DIR = str(base_dir)
    if default_redirect_uri:
        DEFAULT_REDIRECT_URI = str(default_redirect_uri)
    if redirect_uri is not None:
        REDIRECT_URI = str(redirect_uri)


def _env_bool(nome: str, padrao: bool = True) -> bool:
    valor = str(os.getenv(nome, "") or "").strip().lower()
    if not valor:
        return padrao
    return valor in {"1", "true", "sim", "yes", "on"}


def _env_paths_programa() -> list[str]:
    paths = []
    for base in (BASE_DIR, os.getcwd()):
        try:
            caminho = os.path.join(base, ".env")
        except Exception:
            continue
        if caminho and caminho not in paths:
            paths.append(caminho)
    return paths


def _env_config_value(*keys: str, cache_as: str | None = None) -> str:
    normalized_keys = [str(key or "").replace("\ufeff", "").strip().upper() for key in keys if str(key or "").strip()]
    if not normalized_keys:
        return ""

    for key in normalized_keys:
        value = str(os.getenv(key) or "").strip()
        if value:
            return value

    wanted = set(normalized_keys)
    for env_path in _env_paths_programa():
        if not os.path.exists(env_path):
            continue
        try:
            values = dotenv_values(env_path)
        except Exception:
            continue
        if not isinstance(values, dict):
            continue
        for raw_key, raw_value in values.items():
            key_norm = str(raw_key or "").replace("\ufeff", "").strip().upper()
            if key_norm not in wanted:
                continue
            value = str(raw_value or "").strip()
            if value:
                os.environ[cache_as or normalized_keys[0]] = value
                return value
    return ""


def _env_config_bool(keys: tuple[str, ...] | list[str], default: bool = False) -> bool:
    value = _env_config_value(*keys)
    if not value:
        return default
    return str(value).strip().lower() in {"1", "true", "sim", "yes", "on"}


def _agent_service_only() -> bool:
    return _env_config_bool(("JK_AGENT_SERVICE_ONLY", "IA_AGENT_SERVICE_ONLY"), default=False)


def _request_eh_local(request: Optional[Any]) -> bool:
    if request is None:
        return False
    try:
        host = str(request.headers.get("host") or request.url.netloc or "").split(",")[0].strip().lower()
        return "127.0.0.1" in host or "localhost" in host
    except Exception:
        return False


def _descobrir_redirect_uri_ngrok() -> Optional[str]:
    try:
        resp = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
        if resp.status_code != 200:
            return None
        payload = resp.json() or {}
        for tunnel in payload.get("tunnels", []):
            public_url = str((tunnel or {}).get("public_url") or "").strip()
            proto = str((tunnel or {}).get("proto") or "").strip().lower()
            if public_url and proto == "https":
                return public_url.rstrip("/") + "/auth/callback"
    except Exception:
        pass
    return None


def _resolver_redirect_uri_publica(request: Optional[Any] = None, saved_redirect_uri: Optional[str] = None) -> str:
    for valor in [saved_redirect_uri, os.getenv("JK_REDIRECT_URI", "").strip()]:
        texto = str(valor or "").strip()
        if texto:
            return texto.rstrip("/")

    if request is not None:
        try:
            proto = str(request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip() or "https"
            host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc or "").split(",")[0].strip()
            if host and "127.0.0.1" not in host and "localhost" not in host:
                return f"{proto}://{host}/auth/callback"
        except Exception:
            pass

    usar_ngrok = str(os.getenv("JK_AUTO_NGROK_REDIRECT", "") or "").strip().lower() in {"1", "true", "sim", "yes"}
    if usar_ngrok:
        ngrok_uri = _descobrir_redirect_uri_ngrok()
        if ngrok_uri:
            return ngrok_uri

    valor_final = str(saved_redirect_uri or REDIRECT_URI or DEFAULT_REDIRECT_URI).strip() or DEFAULT_REDIRECT_URI
    return valor_final.rstrip("/")


def _resolver_redirect_uri_bling(request: Optional[Any] = None, saved_redirect_uri: Optional[str] = None) -> str:
    bling_uri = str(os.getenv("JK_BLING_REDIRECT_URI", "") or "").strip()
    if bling_uri:
        return bling_uri.rstrip("/")

    if saved_redirect_uri:
        return str(saved_redirect_uri).strip().rstrip("/")

    auto_ngrok = str(os.getenv("JK_AUTO_NGROK_REDIRECT", "") or "").strip().lower() in {"1", "true", "sim", "yes"}
    if _request_eh_local(request) and not auto_ngrok:
        return DEFAULT_REDIRECT_URI.rstrip("/")

    return _resolver_redirect_uri_publica(request=request)
