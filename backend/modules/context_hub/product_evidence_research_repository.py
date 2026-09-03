"""Read model for all tenant-scoped product research evidence."""

from __future__ import annotations

from typing import Any, Optional

from backend.modules.context_hub.bootstrap import bootstrap_context_hub
from backend.modules.context_hub.locking import (
    _exclusive_product_evidence_file_lock,
    _product_evidence_thread_lock,
)
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.product_evidence_model import _parse_timestamp
from backend.modules.context_hub.product_evidence_repository import (
    _all_research_claims,
    _claim_sources_by_signatures,
    _normalized_identity,
    _recalculate_identity,
)
from backend.modules.context_hub.runtime import _runtime_config
from backend.modules.context_hub.storage import _connect


def list_product_research_evidence(
    client_id: object,
    *,
    store_ref: object,
    seller_id: object,
    site_id: object,
    sku: object,
    item_id: object = "",
    variation_id: object = "",
    as_of: Optional[object] = None,
    info_root: Optional[object] = None,
    recalculate: bool = True,
) -> list[dict[str, Any]]:
    """List bounded compiled facts and provenance for agent-side judgment."""

    config = _runtime_config(info_root=info_root)
    bootstrap_context_hub(client_id, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    identity = _normalized_identity(
        store_ref, seller_id, site_id, sku, item_id, variation_id
    )
    evaluated_at = _parse_timestamp(as_of)
    with _product_evidence_thread_lock(paths), _exclusive_product_evidence_file_lock(paths):
        with _connect(paths) as connection:
            if recalculate:
                connection.execute("BEGIN IMMEDIATE")
                _recalculate_identity(connection, identity, evaluated_at)
            claims = _all_research_claims(connection, identity)
            signatures = [
                (
                    str(claim["field_name"]), str(claim["scope"]),
                    str(claim["normalized_key"]), str(claim["unit"]), str(claim["state"]),
                )
                for claim in claims
            ]
            sources_by_signature = _claim_sources_by_signatures(
                connection, identity, signatures
            )
            results: list[dict[str, Any]] = []
            by_signature: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
            for claim in claims:
                key = (
                    str(claim["field_name"]), str(claim["scope"]),
                    str(claim["normalized_key"]), str(claim["unit"]), str(claim["state"]),
                )
                rendered = by_signature.get(key)
                if rendered is None:
                    rendered = {
                        "field_name": key[0], "scope": key[1],
                        "value": str(claim["normalized_value"]), "unit": key[3],
                        "state": key[4], "activation_policy": str(claim["activation_policy"]),
                        "conflict_group": str(claim["conflict_group"]),
                        "valid_from": str(claim["valid_from"]),
                        "valid_until": str(claim["valid_until"] or ""), "sources": [],
                    }
                    by_signature[key] = rendered
                    results.append(rendered)
                seen_sources = {
                    (str(source.get("url") or ""), str(source.get("content_hash") or ""))
                    for source in rendered["sources"] if isinstance(source, dict)
                }
                for source in sources_by_signature.get(key, []):
                    source_key = (str(source["canonical_url"]), str(source["content_hash"]))
                    if source_key in seen_sources:
                        continue
                    seen_sources.add(source_key)
                    rendered["sources"].append({
                        "source_type": str(source["source_type"]),
                        "authority": str(source["authority"]),
                        "url": str(source["canonical_url"]),
                        "domain": str(source["domain"]),
                        "section_ref": str(source["section_ref"]),
                        "collected_at": str(source["collected_at"]),
                        "valid_until": str(source["valid_until"]),
                        "content_hash": str(source["content_hash"]),
                    })
            connection.commit()
    for result in results:
        result["sources"] = list(result["sources"])[:8]
    return results[:160]


__all__ = ["list_product_research_evidence"]
