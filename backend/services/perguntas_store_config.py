"""Current, authorized ML credentials for question reads and explicit sending."""

from __future__ import annotations

import re

from fastapi import HTTPException

from backend.services import integracoes
from backend.services.mercadolivre_legacy_api import _ml_cfg_com_store_id_context


def _identity_error(code: str, message: str) -> HTTPException:
    return HTTPException(409, detail={"code": code, "message": message})


def obter_cfg_ml_snapshot(
    client_id: str,
    nome_loja: str,
    store_id: str | None = None,
    *,
    seller_id: str | None = None,
    site_id: str | None = None,
    require_name_match: bool = False,
) -> dict:
    """Resolve credentials without acquiring the catalog/store writer mutex.

    The source intersects published identities with current canonical credentials
    and applies the initiating central session when present. A legacy name must
    resolve unambiguously; an explicit ID never falls back to another store.
    OAuth refresh remains the API transport's responsibility and retains the
    exact store ID for persistence. Display-only status normalization does not
    need to write store configuration before a question read or answer POST.
    """
    rows = integracoes.ler_lojas(client_id)
    exact = str(store_id or "").strip()
    if exact:
        matches = [row for row in rows if str(row.get("store_id") or "").strip() == exact]
        if len(matches) > 1:
            raise _identity_error("store_config_ambiguous", "A identidade da loja esta duplicada.")
        row = matches[0] if matches else None
    else:
        row = integracoes._integracoes_encontrar_loja(rows, nome_loja)
    if row is None:
        raise HTTPException(404, detail="Loja nao autorizada ou indisponivel nesta sessao.")
    if require_name_match and (
        not str(nome_loja or "").strip()
        or integracoes._integracoes_nome_chave_legado(nome_loja)
        != integracoes._integracoes_nome_chave_legado(row.get("nome"))
    ):
        raise _identity_error("store_identity_changed", "O nome da loja mudou. Atualize a lista de lojas.")
    exact = str(row.get("store_id") or "").strip()
    if not exact:
        raise _identity_error("store_id_required", "A identidade exata da loja nao esta disponivel.")
    cfg = dict((row.get("integracoes") or {}).get("mercadolivre") or {})
    if "mercadolivre" in row.get("_unavailable_providers", []):
        from .store_read_service import unavailable
        raise unavailable()
    current_seller = str(cfg.get("user_id") or "").strip()
    current_site = str(cfg.get("site_id") or row.get("site_id") or "").strip()
    if not current_seller or (current_site and not re.fullmatch(r"[A-Z]{3}", current_site)):
        raise _identity_error("store_identity_unconfirmed", "Confirme a conexao desta loja ao Mercado Livre.")
    if ((seller_id is not None and current_seller != str(seller_id).strip())
            or (site_id is not None and (
                not re.fullmatch(r"[A-Z]{3}", str(site_id).strip())
                or (current_site and current_site != str(site_id).strip())
            ))):
        raise _identity_error("store_identity_changed", "A conexao da loja mudou. Atualize a lista de lojas.")
    if not str(cfg.get("access_token") or "").strip():
        raise HTTPException(401, detail="Reconecte esta loja ao Mercado Livre.")
    cfg["app_id"] = cfg.get("app_id") or cfg.get("id") or cfg.get("client_id")
    cfg["client_secret"] = cfg.get("client_secret") or cfg.get("secret")
    # Legacy stores may omit site_id. Only a caller that revalidated the
    # generation preflight may provide the resolved site; never infer MLB.
    if current_site or site_id is not None:
        cfg["site_id"] = current_site or str(site_id).strip()
    return _ml_cfg_com_store_id_context(cfg, exact)
