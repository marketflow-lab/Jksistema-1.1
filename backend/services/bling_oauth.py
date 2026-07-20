"""Low-level Bling OAuth exchange without automatic retries."""

from __future__ import annotations

import base64
from typing import Any

import requests
from fastapi import HTTPException

from backend.services.bling import BLING_SESSION


def _oauth_error_fields(response) -> tuple[str, str, str]:
    try:
        payload = response.json() or {}
    except Exception:
        return "", "", ""
    if not isinstance(payload, dict):
        return "", "", ""
    error = payload.get("error")
    if isinstance(error, dict):
        return (
            str(error.get("type") or "").strip().lower(),
            str(error.get("message") or "").strip().lower(),
            str(error.get("description") or "").strip().lower(),
        )
    return (
        "",
        str(error or "").strip().lower(),
        str(payload.get("error_description") or "").strip().lower(),
    )


def _retry_after_headers(response) -> dict[str, str] | None:
    value = str((getattr(response, "headers", None) or {}).get("Retry-After") or "").strip()
    return {"Retry-After": value} if value else None


def exchange_bling_refresh_token(client_id: str, client_secret: str, refresh_token: str) -> dict[str, Any]:
    """Executa exatamente um POST de refresh e preserva a classe do erro HTTP."""
    url = "https://www.bling.com.br/Api/v3/oauth/token"
    credential = f"{client_id}:{client_secret}"
    headers = {
        "Authorization": f"Basic {base64.b64encode(credential.encode()).decode()}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "enable-jwt": "1",
    }
    payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    try:
        response = BLING_SESSION.post(url, headers=headers, data=payload, timeout=(5, 20))
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail="Nao foi possivel conectar ao Bling para renovar token no momento.",
        ) from exc

    if response.status_code == 200:
        try:
            result = response.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Resposta invalida ao renovar token do Bling.") from exc
        if not isinstance(result, dict):
            raise HTTPException(status_code=502, detail="Resposta invalida ao renovar token do Bling.")
        return result

    error_type, error_message, error_description = _oauth_error_fields(response)
    if response.status_code == 400 and (
        error_type == "invalid_grant"
        or error_message == "invalid_grant"
        or "invalid refresh token" in error_description
    ):
        raise HTTPException(
            status_code=401,
            detail="Token Bling expirado para esta loja. Refaca a conexao em Integracoes para continuar.",
        )
    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Credenciais Bling invalidas. Refaca a conexao em Integracoes.")
    if response.status_code == 429:
        raise HTTPException(
            status_code=429,
            detail="Limite de solicitacoes da Bling atingido ao renovar token.",
            headers=_retry_after_headers(response),
        )
    if response.status_code in {500, 502, 503, 504}:
        raise HTTPException(status_code=503, detail="Servico Bling indisponivel ao renovar token.")
    raise HTTPException(
        status_code=502,
        detail=f"Falha ao renovar token do Bling (HTTP {response.status_code}).",
    )


__all__ = ["exchange_bling_refresh_token"]
