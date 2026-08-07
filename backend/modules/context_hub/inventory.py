"""Context Hub inventory component."""

from __future__ import annotations

import json
import os
import re
from typing import (
    Any,
    Mapping,
    Sequence,
)

from backend.services.context_inventory import api as context_inventory_api



from backend.modules.context_hub.contracts import (
    CONTEXT_HUB_SCHEMA_VERSION,
    ContextHubRuntimeConfig,
)

from backend.modules.context_hub.findings import (
    _finding,
    _strip_volatile,
)

from backend.modules.context_hub.runtime import (
    _json_canonical,
    _sha256_text,
)


def _sanitize_inventory_findings(findings: object) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    if not isinstance(findings, list):
        return sanitized
    for item in findings:
        if not isinstance(item, Mapping):
            continue
        sanitized.append(
            _finding(
                str(item.get("code") or item.get("category") or "inventory_finding"),
                severity="blocker" if item.get("blocking") is True else str(item.get("severity") or "warning"),
                category=str(item.get("category") or "inventory"),
                source_ref=str(item.get("source_ref") or item.get("path") or ""),
                count=int(item.get("count") or 1),
            )
        )
    return sanitized


def _inventory_source_hash(
    inventory: Mapping[str, Any],
    curated_hashes: Sequence[tuple[str, str]],
    bundle_hashes: Sequence[tuple[str, str]],
) -> str:
    payload = {
        "schema_version": CONTEXT_HUB_SCHEMA_VERSION,
        "source_version": inventory.get("source_version") or "",
        "entities": _strip_volatile(inventory.get("entities") or []),
        "findings": _strip_volatile(_sanitize_inventory_findings(inventory.get("findings"))),
        "curated": sorted(curated_hashes),
        "bundle": sorted(bundle_hashes),
    }
    return _sha256_text(_json_canonical(payload))


def _build_inventory(config: ContextHubRuntimeConfig, client_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        builder = context_inventory_api.build_context_inventory
        if not callable(builder):
            raise AttributeError("build_context_inventory")
    except (ImportError, AttributeError):
        return ({"entities": [], "findings": [], "source_version": "", "stats": {}}, [
            _finding("inventory_adapter_unavailable", category="inventory")
        ])
    try:
        inventory = builder(
            config.base_dir,
            config.info_root,
            client_id,
            config.surface,
        )
    except Exception:
        return ({"entities": [], "findings": [], "source_version": "", "stats": {}}, [
            _finding("inventory_build_failed", category="inventory")
        ])
    if not isinstance(inventory, Mapping) or not isinstance(inventory.get("entities"), list):
        return ({"entities": [], "findings": [], "source_version": "", "stats": {}}, [
            _finding("inventory_contract_invalid", category="inventory")
        ])
    return dict(inventory), []


def _runtime_source_version(config: ContextHubRuntimeConfig) -> str:
    environment_version = str(os.getenv("JK_APP_VERSION") or "").strip()
    if re.fullmatch(r"[A-Za-z0-9._+/-]{1,120}", environment_version):
        return environment_version
    package_path = config.base_dir / "package.json"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
        package_version = str(package.get("version") or "").strip() if isinstance(package, dict) else ""
    except (OSError, UnicodeError, json.JSONDecodeError):
        package_version = ""
    return package_version if re.fullmatch(r"[A-Za-z0-9._+/-]{1,120}", package_version) else "unknown"
