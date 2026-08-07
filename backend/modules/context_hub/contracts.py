"""Context Hub contracts component."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Optional,
)




CONTEXT_HUB_SCHEMA_VERSION = 2


CONTEXT_RETRIEVAL_V3 = "jk.context-hub.retrieval.v3"


CONTEXT_RETRIEVAL_AUTHORITY_POLICY = "jk.context-hub.authority.v1"


CURATION_SCHEMA_VERSION = 2


CURATION_STATES = {"draft", "reviewed", "approved", "rejected"}


GENERATION_STATES = {
    "staging",
    "validating",
    "ready",
    "active",
    "superseded",
    "failed",
}


FRONTMATTER_REQUIRED = (
    "id",
    "type",
    "managed",
    "status",
    "ai_usage",
    "tenant_scope",
    "sensitivity",
    "truth_class",
    "required_permissions",
    "surface",
    "source_version",
    "source_refs",
    "source_hash",
    "generated_at",
)


VAULT_DIRECTORIES = (
    "00_Inicio",
    "70_Gerado/Mapas",
    "70_Gerado/Dominios",
    "70_Gerado/Fluxos",
    "70_Gerado/Contratos",
    "70_Gerado/Operacao",
    "70_Gerado/Produtos",
    "80_Curadoria/ADRs",
    "80_Curadoria/Regras",
    "80_Curadoria/Notas",
    "80_Curadoria/Black-Jhon",
    "90_Arquivo",
)


OBSIDIAN_GRAPH_DEFAULTS: dict[str, Any] = {
    "collapse-filter": False,
    "search": "",
    "showTags": False,
    "showAttachments": False,
    "hideUnresolved": True,
    "showOrphans": True,
    "collapse-color-groups": False,
    "colorGroups": [
        {
            "query": "[type:map OR domain]",
            "color": {"a": 1, "rgb": 3900150},  # #3B82F6 blue
        },
        {
            "query": 'path:"70_Gerado/Produtos/Categorias"',
            "color": {"a": 1, "rgb": 16096779},  # #F59E0B amber
        },
        {
            "query": (
                '(path:"70_Gerado/Produtos/Catalogo-SKU.md" OR '
                'path:"70_Gerado/Produtos/Familias-SKU.md" OR '
                'path:"70_Gerado/Produtos/Cobertura-SKU.md")'
            ),
            "color": {"a": 1, "rgb": 15485081},  # #EC4899 pink
        },
        {
            "query": (
                'path:"70_Gerado/Produtos/Veiculos-compativeis/" '
                'file:"Marca.md"'
            ),
            "color": {"a": 1, "rgb": 1096065},  # #10B981 emerald
        },
        {
            "query": (
                "path:/^70_Gerado\\/Produtos\\/(?:Veiculos-Compativeis\\.md$|"
                "Veiculos-compativeis\\/(?:Arvore\\.md$|Marcas\\/parte-\\d+\\.md$|"
                "[^/]+\\/Modelos\\/parte-\\d+\\.md$))/"
            ),
            "color": {"a": 1, "rgb": 9133302},  # #8B5CF6 violet
        },
        {
            "query": (
                'path:"70_Gerado/Produtos/Veiculos-compativeis/" '
                'file:"Modelo.md"'
            ),
            "color": {"a": 1, "rgb": 440020},  # #06B6D4 cyan
        },
    ],
    "collapse-display": False,
    "showArrow": False,
    "textFadeMultiplier": 0,
    "nodeSizeMultiplier": 1,
    "lineSizeMultiplier": 1,
    "collapse-forces": True,
    "centerStrength": 0.518713248970312,
    "repelStrength": 10,
    "linkStrength": 1,
    "linkDistance": 250,
    "scale": 0.15,
    "close": True,
}


_SAFE_CLIENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


_SAFE_REASON_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "conin$",
    "conout$",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


CONTEXT_BUNDLE_REQUIRED_PATHS = frozenset(
    {
        "docs/knowledge/README.md",
        "docs/knowledge/security-policy.md",
        "docs/knowledge/vault-structure.md",
        "docs/knowledge/templates/curated-note.md",
        "docs/knowledge/templates/generated-note.md",
    }
)


CONTEXT_BUNDLE_OBSIDIAN_PATHS = {
    "@bundle/mercado-livre-api-consultas.md": (
        "70_Gerado/Contratos/Mercado-Livre-API-Consultas.md"
    ),
}


CONTEXT_BUNDLE_GRAPH_ANCHORS = {
    "@bundle/mercado-livre-api-consultas.md": (
        "70_Gerado/Contratos/APIs/mercado-livre.md",
        "70_Gerado/Contratos/APIs.md",
        "70_Gerado/Mapas/Inventario-do-Programa.md",
    ),
}


CURATION_DASHBOARD_RELATIVE_PATH = "00_Inicio/Painel-de-Curadoria.md"


_VOLATILE_INVENTORY_KEYS = {
    "generated_at",
    "generation_time",
    "duration_ms",
    "elapsed_ms",
    "scanned_at",
    "updated_at",
}


CONTEXT_RETRIEVAL_FILTER_KEYS = frozenset(
    {
        "authority",
        "consumer_surface",
        "document_types",
        "domain",
        "entity_ids",
        "environment",
        "ids",
        "kind",
        "mlb",
        "module",
        "request_surface",
        "sensitivity",
        "sku",
        "source_type",
        "store_ref",
        "surface",
        "tags",
        "truth_class",
        "valid_at",
        "validity",
    }
)


_CONTEXT_AUTHORITY_TRUTH_CLASSES = {
    "authoritative": {"canonical", "source", "system_authoritative"},
    "verified_technical": {"generated_verified", "versioned_technical"},
    "advisory": {"human_curated"},
    "unverified": {"generated", "generated_secondary", "legacy_unverified"},
}


class ContextHubError(RuntimeError):
    """Base error safe to translate at the HTTP boundary."""


class ContextHubValidationError(ContextHubError):
    """The requested operation did not pass validation."""


class ContextHubConflictError(ContextHubError):
    """The active generation changed or another operation is running."""


class ContextHubNotFoundError(ContextHubError):
    """The requested generation was not found for this tenant."""


@dataclass(frozen=True)
class ContextHubRuntime:
    base_dir: Path
    info_root: Path
    surface: str
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    id_factory: Callable[[], str] = lambda: uuid.uuid4().hex


ContextHubRuntimeConfig = ContextHubRuntime


@dataclass(frozen=True)
class ContextHubPaths:
    client_id: str
    info_root: Path
    tenant_dir: Path
    vault_dir: Path
    generated_dir: Path
    curated_dir: Path
    internal_dir: Path
    db_path: Path
    staging_dir: Path
    generations_dir: Path
    lock_path: Path
    journal_path: Path
    backups_dir: Path
    restore_staging_dir: Path


@dataclass(frozen=True)
class _InfoRootSnapshot:
    absolute: Path
    resolved: Path
    identity: Optional[tuple[int, int, int]]
