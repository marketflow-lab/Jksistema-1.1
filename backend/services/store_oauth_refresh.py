"""ML refresh single-flight and compare-and-set without network under store locks."""
from __future__ import annotations

import copy
from contextlib import contextmanager

from fastapi import HTTPException

from .store_lock_diagnostics import operation_scope


def _changed():
    return HTTPException(409, detail={"code": "store_identity_changed", "message": "A conexao da loja mudou. Atualize a lista de lojas."})


def _identity(cfg):
    return tuple(str(cfg.get(key) or "").strip() for key in ("user_id", "site_id", "oauth_connection_id")) + (
        str(cfg.get("app_id") or cfg.get("id") or cfg.get("client_id") or "").strip(),
    )


def _version(cfg):
    # Metadata revisions can change while the provider rotates a token. Only
    # origin credentials decide CAS, otherwise a valid rotated token is lost.
    return (str(cfg.get("access_token") or ""), str(cfg.get("refresh_token") or ""),
            str(cfg.get("client_secret") or cfg.get("secret") or ""))


def _config(row, site_hint=""):
    cfg = dict(((row or {}).get("integracoes") or {}).get("mercadolivre") or {})
    if cfg:
        cfg["site_id"] = cfg.get("site_id") or (row or {}).get("site_id") or site_hint
    return cfg


@contextmanager
def oauth_gate(tenant_path, store_id, provider):
    from .store_coordination import oauth_refresh_lock, StoreCoordinationError
    from .perguntas_loading_transport import remaining
    budget = remaining()
    if budget is not None and budget <= 0:
        raise HTTPException(504, "Tempo de consulta das perguntas esgotado.")
    try:
        with oauth_refresh_lock(tenant_path, store_id, provider, timeout_seconds=min(10, budget) if budget is not None else 10):
            yield
    except StoreCoordinationError:
        raise HTTPException(503, detail={"code": "store_oauth_busy", "message": "A conexao da loja esta sendo atualizada."},
                            headers={"Retry-After": "2"}) from None


def revalidate_ml_mutation(client_id, nome_loja, cfg):
    """Immediately before an external write, bind it to the prepared account."""
    from .perguntas_store_config import obter_cfg_ml_snapshot
    store_id = str((cfg or {}).get("_store_id_context") or "").strip()
    if not store_id or not str((cfg or {}).get("user_id") or "").strip():
        raise _changed()
    current = obter_cfg_ml_snapshot(client_id, nome_loja, store_id, seller_id=cfg["user_id"],
                                     site_id=cfg.get("site_id") or None)
    if _identity(current) != _identity(cfg):
        raise _changed()
    return current


@operation_scope("oauth_refresh")
def refresh_ml(client_id, nome_loja, hint, exchange):
    from . import integracoes as source
    from .central_accounts_client import is_marker

    if is_marker((hint or {}).get("access_token")):
        raise HTTPException(409, "A renovacao desta conexao e controlada pela central.")
    store_id = str((hint or {}).get("_store_id_context") or "").strip()
    if not store_id:
        raise _changed()
    with oauth_gate(source._tenant_path(client_id), store_id, "mercadolivre"):
        row = source.buscar_loja_snapshot(client_id, nome_loja, store_id)
        current = _config(row, hint.get("site_id") or "")
        if not current or _identity(current) != _identity(hint):
            raise _changed()
        if _version(current) != _version(hint):
            return dict(current, _store_id_context=store_id)
        base = dict(current, _store_id_context=store_id)
        base["app_id"] = base.get("app_id") or base.get("id") or base.get("client_id")
        base["client_secret"] = base.get("client_secret") or base.get("secret")
        # This call may take seconds. No store/catalog/photo writer mutex is held.
        updated = exchange(client_id, nome_loja, copy.deepcopy(base))
        if not str(updated.get("access_token") or "").strip():
            raise HTTPException(502, "Resposta invalida ao renovar token do Mercado Livre.")
        with source._integracoes_bloquear_rmw_lojas(client_id):
            rows = source.carregar_lojas(client_id)
            actual_row = source._integracoes_encontrar_loja_identidade(rows, nome_loja, store_id)
            actual = _config(actual_row, hint.get("site_id") or "")
            if not actual or _identity(actual) != _identity(current):
                raise _changed()
            if _version(actual) != _version(current):
                return dict(actual, _store_id_context=store_id)
            # Commit only token fields. A concurrent rename or unrelated setting
            # cannot be replaced by the snapshot sent to the provider.
            for key in ("access_token", "refresh_token", "updated_at"):
                if key in updated:
                    actual[key] = updated[key]
            persisted = dict(actual_row["integracoes"]["mercadolivre"])
            for key in ("access_token", "refresh_token", "updated_at"):
                if key in updated:
                    persisted[key] = updated[key]
            actual_row["integracoes"]["mercadolivre"] = persisted
            source.salvar_lojas(client_id, rows)
            return dict(actual, _store_id_context=store_id)
