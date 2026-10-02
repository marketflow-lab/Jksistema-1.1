"""Builder component."""



from __future__ import annotations



from collections import Counter
from pathlib import Path
from typing import Any



from .contracts import INVENTORY_SCHEMA_VERSION



from .normalization import _slug

from .cache_sources import _validated_source_root
from .static_cache import STATIC_INVENTORY_CACHE









from .python_scanner import (
    _scan_schemas,
    _scan_services,
    _scan_sql_schemas,
)



from .route_scanner import (
    _scan_routes,
    _link_implementation_relationships,
)



from .legacy import (
    _add_domains,
    _deduplicate_entities,
    _scan_legacy_docs,
    _source_version,
)

from .sku_scanner import _scan_sku

from .surface_scanner import (
    _scan_capabilities,
    _scan_frontend,
    _scan_integrations,
    _scan_tests,
)



def _build_static_inventory(base: Path, surface_value: str) -> dict[str, Any]:
    """Cache only derived source entities; ASTs and tenant inputs stay local."""
    findings: list[dict[str, str]] = []
    schemas, schema_lookup = _scan_schemas(base, surface_value, findings)
    routes = _scan_routes(base, surface_value, schema_lookup, findings)
    services, parsed_files = _scan_services(base, surface_value, findings)
    data_schemas = _scan_sql_schemas(parsed_files, base, surface_value)
    _link_implementation_relationships(routes, services, data_schemas)
    screens, frontend_calls = _scan_frontend(base, surface_value, routes, findings)
    tests = _scan_tests(base, surface_value)
    integrations = _scan_integrations(base, surface_value)
    legacy_docs, legacy_findings = _scan_legacy_docs(base, surface_value)
    findings.extend(legacy_findings)
    return {
        "schemas": schemas, "routes": routes, "services": services,
        "data_schemas": data_schemas, "screens": screens,
        "frontend_calls": frontend_calls, "tests": tests,
        "integrations": integrations, "legacy_docs": legacy_docs,
        "findings": findings, "source_version": _source_version(base),
    }


def build_context_inventory(
    base_dir: str, info_root: str, client_id: str, surface: str,
) -> dict[str, Any]:
    """Build inventory without importing the application or persisting source data.

    Static derived entities are reused; capabilities, SKU and final domains are
    rebuilt on every invocation and are never stored in the shared cache.
    """
    base = _validated_source_root(Path(base_dir))
    info = Path(info_root).expanduser().resolve(strict=True)
    surface_value = _slug(surface, fallback="checkout")
    tenant = str(client_id or "").strip()
    static = STATIC_INVENTORY_CACHE.get_or_build(
        base, surface_value, lambda: _build_static_inventory(base, surface_value),
    )
    findings = static["findings"]
    schemas, routes, services = static["schemas"], static["routes"], static["services"]
    data_schemas, screens = static["data_schemas"], static["screens"]
    frontend_calls, tests = static["frontend_calls"], static["tests"]
    integrations, legacy_docs = static["integrations"], static["legacy_docs"]
    capabilities = _scan_capabilities(base, surface_value, tenant, routes, findings)
    sku_entities, sku_stats = _scan_sku(base, info, tenant, surface_value, findings)

    base_entities = _deduplicate_entities(
        [
            *schemas,
            *routes,
            *services,
            *data_schemas,
            *screens,
            *frontend_calls,
            *tests,
            *integrations,
            *capabilities,
            *sku_entities,
            *legacy_docs,
        ],
        findings,
    )
    domains = _add_domains(base_entities, surface_value)
    entities = _deduplicate_entities([*base_entities, *domains], findings)
    kind_counts = Counter(str(entity["kind"]) for entity in entities)
    domain_counts = Counter(str(entity["domain"]) for entity in entities)
    finding_counts = Counter(str(item["severity"]) for item in findings)
    route_keys = {
        (str(row["metadata"].get("method") or ""), str(row["metadata"].get("path") or ""))
        for row in routes
    }
    capability_route_keys = {
        (str(row["metadata"].get("method") or ""), str(row["metadata"].get("route_path") or ""))
        for row in capabilities
        if row["metadata"].get("method") and str(row["metadata"].get("route_path") or "").startswith("/")
    }
    stats = {
        "entities": len(entities),
        "by_kind": dict(sorted(kind_counts.items())),
        "by_domain": dict(sorted(domain_counts.items())),
        "findings": dict(sorted(finding_counts.items())),
        "sku": sku_stats,
        "canonical_screens": len(screens),
        "routes": len(routes),
        "schemas": len(schemas),
        "services": len(services),
        "tests": len(tests),
        "capabilities": len(capabilities),
        "route_crosscheck": {
            "ast_routes": len(route_keys),
            "capability_routes": len(capability_route_keys),
            "matched": len(route_keys & capability_route_keys),
            "capability_only": len(capability_route_keys - route_keys),
        },
    }
    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "source_version": static["source_version"],
        "surface": surface_value,
        "client_id": tenant,
        "entities": entities,
        "findings": sorted(
            findings,
            key=lambda item: (
                {"blocker": 0, "error": 1, "warning": 2, "info": 3}.get(item.get("severity", ""), 9),
                item.get("code", ""),
                item.get("source_ref", ""),
                item.get("entity_id", ""),
            ),
        ),
        "stats": stats,
    }
