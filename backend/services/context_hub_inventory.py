"""Compatibility facade for the semantic Context Hub inventory."""

from __future__ import annotations

from backend.services.context_inventory.api import (
    ENTITY_KEYS,
    INVENTORY_SCHEMA_VERSION,
    build_context_bundle_manifest,
    build_context_inventory,
    compare_context_inventory_openapi,
    render_context_entity_markdown,
    render_context_inventory_markdown,
)

__all__ = [
    "ENTITY_KEYS",
    "INVENTORY_SCHEMA_VERSION",
    "build_context_bundle_manifest",
    "build_context_inventory",
    "compare_context_inventory_openapi",
    "render_context_entity_markdown",
    "render_context_inventory_markdown",
]
