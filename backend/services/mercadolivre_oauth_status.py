"""Pure Mercado Livre connection status, without runtime dependencies."""

from __future__ import annotations


def oauth_status(cfg: dict | None) -> dict:
    cfg = cfg if isinstance(cfg, dict) else {}
    if cfg.get("central") and cfg.get("connected"):
        return {"conectado": True, "status": "conectado", "motivo": "", "faltando": []}
    app_id = str(cfg.get("app_id") or cfg.get("id") or cfg.get("client_id") or "").strip()
    client_secret = str(cfg.get("client_secret") or cfg.get("secret") or "").strip()
    access_token = str(cfg.get("access_token") or "").strip()
    refresh_token = str(cfg.get("refresh_token") or "").strip()
    faltando = []
    if not access_token:
        faltando.append("access_token")
    if not refresh_token:
        faltando.append("refresh_token")
    if not app_id:
        faltando.append("app_id")
    if not client_secret:
        faltando.append("client_secret")

    if not cfg:
        return {
            "conectado": False,
            "status": "pendente",
            "motivo": "Mercado Livre ainda nÃ£o foi autenticado.",
            "faltando": faltando,
        }
    if not faltando:
        return {
            "conectado": True,
            "status": "conectado",
            "motivo": "",
            "faltando": [],
        }
    return {
        "conectado": False,
        "status": str(cfg.get("status") or "reautenticar"),
        "motivo": str(cfg.get("motivo") or "OAuth do Mercado Livre incompleto. RefaÃ§a a autenticaÃ§Ã£o."),
        "faltando": faltando,
    }
