"""Repair missing composition metadata during the existing Vendas worker.

The Bling catalog remains the source. Only confirmed account identities and
composition fields are written through Cadastro's optimistic, atomic writer.
Missing/invalid details remain eligible on the next sales synchronization.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import secrets
from typing import Callable

from backend.services import cadastro_catalogo_bling as provider
from backend.services import cadastro_importacao_catalogos as imports
from backend.services import cadastro_lojas_produtos as store

from .compositions import CompositionCatalog, _key, _product_id, load_composition_catalog


_FIELDS = frozenset({"id_bling", "formato_bling", "componentes_bling_json", "estrutura_bling"})
_CHECKED_AT = "composicao_consultada_em_utc_bling"
_REFRESH_INTERVAL = timedelta(days=1)


class CompositionSyncConflict(RuntimeError):
    """An auxiliary metadata conflict, distinct from sales cancellation."""

    status_code = 409


def _recent(value, now):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return now - _REFRESH_INTERVAL <= stamp <= now + timedelta(minutes=5)
    except (ValueError, TypeError):
        return False


def _components(record):
    value = record.get("componentes_bling_json")
    try:
        if value in (None, ""):
            structure = record.get("estrutura_bling") or {}
            structure = json.loads(structure) if isinstance(structure, str) else structure
            value = structure.get("componentes")
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, list) else None
    except (ValueError, TypeError, AttributeError):
        return None


def sincronizar_composicoes_vendas(
    client_id: str, store_id: str, *, cancel_callback: Callable[[], object],
) -> dict:
    """Refresh BOMs with bounded collection and item-level durable progress.

    This is called by the background sales worker, never by a report GET.
    Successful BOMs are fresh for one day; failures have no freshness marker.
    """
    cancel_callback()
    current = store._listar_produtos_loja_sync(
        client_id, store_id, include_deleted=True,
        include_legacy_snapshot_hash=True, include_catalog_metadata=True,
    )
    counts = Counter(store._normalizar_sku_chave(row.get("sku")) for row in current)
    existing = {
        store._normalizar_sku_chave(row.get("sku")): row for row in current
        if counts[store._normalizar_sku_chave(row.get("sku"))] == 1
    }
    now = datetime.now(timezone.utc)
    catalog = load_composition_catalog(client_id)
    fresh = {
        _product_id(row.get("id_bling")): store._normalizar_sku_chave(row.get("sku"))
        for row in current
        if row.get("__catalog_bling_id_persisted") is True
        and counts[store._normalizar_sku_chave(row.get("sku"))] == 1
        and _recent(row.get(_CHECKED_AT), now)
        and catalog._expansion(store_id, _key(row.get("sku"))) is not None
        and _product_id(row.get("id_bling"))
    }

    result = provider.coletar_catalogo_bling(
        client_id, store_id, compositions_only=True, skip_detail_ids=fresh,
        cancel_event=cancel_callback,
    )
    cancel_callback()
    if result.get("store_id") != store_id:
        raise CompositionSyncConflict("A identidade da loja mudou durante a consulta.")
    stats = result.get("stats") or {}
    # Without complete enumeration, a later page might contradict an ID/SKU.
    # Detail timeouts can still commit already confirmed items after listing.
    if not stats.get("composition_listing_complete") or stats.get("cancelled"):
        return {"updated": 0, "pending": True}

    groups = {}
    for item in result.get("items") or []:
        key = store._normalizar_sku_chave(item.get("sku"))
        if key:
            groups.setdefault(key, []).append(item)
    id_counts = Counter(
        _product_id((item.get("fields") or {}).get("id_bling"))
        for group in groups.values() for item in group
    )
    proposals = {}
    pending = not result.get("coverage_complete")
    for key, group in groups.items():
        if len(group) != 1 or counts.get(key, 0) > 1:
            pending = True
            continue
        item = group[0]
        fields = {name: value for name, value in (item.get("fields") or {}).items() if name in _FIELDS}
        product_id = _product_id(fields.get("id_bling"))
        if not product_id or id_counts[product_id] != 1:
            pending = True
            continue
        old = existing.get(key) or {}
        if old.get("deleted_at_utc"):
            continue
        updates, conflicts = imports._campos_composicao_bling_existente(
            [{**item, "fields": fields}], old, store_id=store_id,
        )
        if conflicts:
            pending = True
            continue
        confirmed = {**fields, **updates}
        cached = bool(
            fresh.get(product_id) == key and _product_id(old.get("id_bling")) == product_id
            and str(fields.get("formato_bling") or "").upper() not in {"S", "V"}
            and all(value == old.get(name) for name, value in updates.items())
        )
        if cached:
            # Cached fields are used only for an independently verified ID,
            # freshly enumerated in this same account with the same SKU.
            confirmed = {**{name: old.get(name) for name in _FIELDS}, **confirmed}
        if (
            str(confirmed.get("formato_bling") or "").upper() not in {"S", "V"}
            and not CompositionCatalog._declares_composition(confirmed)
        ):
            # An unclassified dependency might itself be a kit. A failed
            # detail must not silently turn it into a simple leaf.
            pending = True
            continue
        proposals[key] = {"sku": item["sku"], "store_id": store_id,
                          **confirmed, "_updates": updates, "_cached": cached}

    verified = CompositionCatalog(proposals.values(), stores=[{
        "store_id": store_id, "nome": result.get("store_name") or store_id,
    }])
    by_id = {_product_id(row.get("id_bling")): key for key, row in proposals.items()}
    targets = set()

    def include_dependencies(key, visited):
        if key in visited:
            return
        visited.add(key)
        targets.add(key)
        for component in _components(proposals[key]) or []:
            reference = component.get("produto") or {}
            child = by_id.get(_product_id(reference.get("id")))
            if not child:
                child = store._normalizar_sku_chave(reference.get("codigo") or reference.get("sku") or component.get("sku"))
            if child in proposals:
                include_dependencies(child, visited)

    for key, row in proposals.items():
        old = existing.get(key) or {}
        if CompositionCatalog._declares_composition(row):
            if verified._expansion(store_id, _key(row["sku"])) is None:
                pending = True
                continue
            if not row["_cached"]:
                include_dependencies(key, set())
        elif CompositionCatalog._declares_composition(old) and str(row.get("formato_bling") or "").upper() in {"S", "V"}:
            targets.add(key)

    payloads = []
    for key in sorted(targets):
        row = proposals[key]
        old = existing.get(key) or {}
        updates = dict(row["_updates"])
        if not old.get("__catalog_bling_id_persisted"):
            updates["id_bling"] = _product_id(row.get("id_bling"))
        # A displayed value can come from an unverified legacy overlay. Send
        # every confirmed field when materializing, even when it looks equal;
        # the Bling writer intentionally strips inherited composition fields.
        payload = {"sku": row["sku"], "row_version": int(old.get("row_version") or 0),
                   "__expected_scope": old.get("scope_source") or "absent",
                   **updates, _CHECKED_AT: now.isoformat()}
        if old.get("__legacy_snapshot_hash"):
            payload["__legacy_snapshot_hash"] = old["__legacy_snapshot_hash"]
        payloads.append(payload)

    def validate(_loja):
        cancel_callback()
        name, config = provider._resolver_loja_bling(client_id, store_id)
        fingerprint = provider.configuracao_catalogo_aplicacao_fingerprint("bling", store_id, name, config)
        if not secrets.compare_digest(fingerprint, str(result.get("apply_config_fingerprint") or "")):
            raise CompositionSyncConflict("A conexão da loja mudou durante a consulta.")

    if payloads:
        imports._salvar_payloads_importacao_catalogo(
            client_id, store_id, "bling", payloads,
            campos_derivados_permitidos={"id_bling"}, precommit_validator=validate,
        )
    return {"updated": len(payloads), "pending": bool(pending)}
