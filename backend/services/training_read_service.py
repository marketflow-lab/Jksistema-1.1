"""Authorized display queries; canonical business locks belong to writers only."""
from __future__ import annotations

import json
import logging
import time

from fastapi import HTTPException

from backend.modules.context_hub.store_sku_contracts import STORE_SKU_PUBLIC_SURFACE
from backend.services.store_listing_service import read_store_cards

_LOG = logging.getLogger(__name__)


def resolve_read_scope(client_id: str, store_id: str) -> dict[str, str]:
    """Session-authorized identity, never a name or an editorial cache permission."""
    store_id = str(store_id or "").strip()
    if not store_id:
        raise HTTPException(409, detail={"code": "store_scope_required", "message": "Selecione a loja exata."})
    cards = read_store_cards(client_id)
    matches = [row for row in cards["lojas"] if row.get("store_id") == store_id]
    if len(matches) != 1:
        raise HTTPException(403, detail={"code": "store_access_denied", "message": "Esta loja nao esta disponivel nesta sessao."})
    row = matches[0]
    seller_id, site_id = str(row.get("seller_id") or ""), str(row.get("site_id") or "").upper()
    if not seller_id or site_id != "MLB":
        raise HTTPException(409, detail={"code": "store_marketplace_identity_incomplete",
                                        "message": "A loja precisa ter seller e site MLB confirmados."})
    return {"tenant_scope": f"tenant:{client_id}", "store_ref": store_id,
            "store_name": str(row.get("nome") or ""), "seller_id": seller_id,
            "site_id": site_id, "surface": STORE_SKU_PUBLIC_SURFACE}


def _read(client_id, scope, sku=None):
    from backend.modules.context_hub import training_read_index as index
    from backend.modules.context_hub.training_index_worker import request_refresh
    # Only enqueue/touch the activity lease. No scan or canonical read on this thread.
    try:
        request_refresh(client_id, scope, sku=sku or "")
    except (OSError, RuntimeError):
        # Scheduling failure cannot discard an already committed display row.
        pass
    started = time.perf_counter()
    result_code = "ready"
    try:
        if sku is None:
            return index.read_editor(client_id, scope)
        return index.read_ficha(client_id, scope, sku)
    except index.TrainingIndexUnavailable as exc:
        result_code = exc.code
        initializing = exc.code == "training_index_initializing"
        raise HTTPException(503, detail={"code": exc.code,
            "message": "Preparando a primeira leitura do Obsidian." if initializing else
                       "Nao foi possivel atualizar a leitura do Obsidian."},
            headers={"Retry-After": "2"}) from None
    finally:
        _LOG.debug("training_read stage=index code=%s elapsed_ms=%.2f",
                   result_code, (time.perf_counter() - started) * 1000)


def read_ficha(client_id, store_id, sku):
    scope = resolve_read_scope(client_id, store_id)
    if not isinstance(sku, str) or not sku.strip() or len(sku) > 200:
        raise HTTPException(422, detail={"code": "sku_required", "message": "Informe o SKU exato."})
    from backend.modules.context_hub.contracts import ContextHubValidationError
    try:
        return _read(client_id, scope, sku.strip())
    except ContextHubValidationError:
        raise HTTPException(422, detail={"code": "sku_invalid", "message": "Informe um SKU valido."}) from None


def read_editor(client_id, store_id):
    scope = resolve_read_scope(client_id, store_id)
    return scope, _read(client_id, scope)


def legacy_profile_for_display(client_id, scope):
    """Preserve post-sale fields without resolving canonical stores a second time."""
    from backend.modules.context_hub.paths import _tenant_paths
    from backend.modules.context_hub.path_safety import _assert_path_chain_safe
    from backend.services.ia_treinamento_ppv import _ia_treinamento_ppv_normalizar_payload
    paths = _tenant_paths(client_id)
    path = paths.tenant_dir / "ia_treinamento_perguntas_pos_venda.json"
    _assert_path_chain_safe(path, paths.info_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = {}
    except (OSError, ValueError):
        raise HTTPException(503, detail={"code": "training_profile_unavailable",
                                        "message": "Nao foi possivel ler a configuracao de treinamento."}) from None
    if not isinstance(payload, dict) or not isinstance(payload.get("por_loja", {}), dict):
        raise HTTPException(503, detail={"code": "training_profile_unavailable",
                                        "message": "A configuracao de treinamento esta indisponivel."})
    profiles = payload.get("por_loja") or {}
    store_id = scope["store_ref"]
    candidates = []
    for key, value in profiles.items():
        if not isinstance(value, dict):
            continue
        explicit = str(value.get("store_id") or "")
        keyed = str(key).removeprefix("store_id:") if str(key).startswith("store_id:") else ""
        if store_id not in (explicit, keyed):
            continue
        if any(identity and identity != store_id for identity in (explicit, keyed)):
            raise HTTPException(503, detail={"code": "training_profile_identity_conflict",
                                            "message": "A identidade da configuracao de treinamento precisa ser revisada."})
        candidates.append(value)
    cards = read_store_cards(client_id)
    if not candidates and not cards.get("session_scoped"):
        # Name aliases remain supported only if the current authorized projection
        # proves a unique identity, matching the legacy reader's safety boundary.
        from backend.services.ia_treinamento_ppv import _ia_treinamento_ppv_loja_key
        names = {}
        for row in cards["lojas"]:
            names.setdefault(_ia_treinamento_ppv_loja_key(row["nome"]), []).append(row["store_id"])
        candidates = [value for key, value in profiles.items() if isinstance(value, dict)
                      and not value.get("store_id") and not str(key).startswith("store_id:")
                      and names.get(_ia_treinamento_ppv_loja_key(value.get("loja") or key)) == [store_id]]
    return _ia_treinamento_ppv_normalizar_payload(candidates[0] if len(candidates) == 1 else {},
                                                 scope["store_name"], "store_id:" + store_id, store_id)


def request_refresh(client_id, store_id, sku=""):
    from backend.modules.context_hub.contracts import ContextHubValidationError
    from backend.modules.context_hub.training_index_worker import request_refresh as enqueue
    scope = resolve_read_scope(client_id, store_id)
    try:
        enqueue(client_id, scope, immediate=True, sku=sku)
    except ContextHubValidationError:
        raise HTTPException(422, detail={"code": "sku_invalid", "message": "Informe um SKU valido."}) from None
    except (OSError, RuntimeError):
        raise HTTPException(503, detail={"code": "training_refresh_unavailable",
                                        "message": "Nao foi possivel agendar a atualizacao."},
                            headers={"Retry-After": "2"}) from None
    return {"success": True, "store_id": scope["store_ref"], "sku": sku,
            "snapshot": {"state": "updating"}}


def notify_saved(client_id, scope, *, info_root=None):
    """A failed derived update must never turn a committed save into a retry."""
    from backend.modules.context_hub.training_index_worker import request_refresh as enqueue
    try:
        from backend.modules.context_hub.training_read_index import index_path
        if not index_path(client_id, info_root=info_root).exists():
            # No display consumer yet. The first GET constructs the initial index.
            return
        enqueue(client_id, scope, immediate=True, info_root=info_root)
    except Exception:
        # The active worker also reconciles periodically; no source data is logged.
        pass
