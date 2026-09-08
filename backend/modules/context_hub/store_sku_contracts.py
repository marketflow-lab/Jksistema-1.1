"""Closed contracts for store-scoped, integral SKU knowledge."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping

from backend.modules.context_hub.contracts import ContextHubValidationError


STORE_SKU_BINDING_SCHEMA = "jk_context_store_sku_binding_v1"
STORE_SKU_QUESTION_CONTEXT_SCHEMA = "jk_ml_store_sku_question_context_v1"
STORE_SKU_MIGRATION_SCHEMA = "jk_context_store_sku_migration_v1"
STORE_SKU_PUBLIC_SURFACE = "mercado_livre_public_questions"

CANONICAL_DOCUMENT_MAX_CHARS = 24_000
APPLICABLE_GUIDANCE_MAX_CHARS = 8_000
OPERATIONAL_DATA_MAX_CHARS = 2_000
QUESTION_HISTORY_MAX_CHARS = 1_500
INTEGRAL_ENVELOPE_MAX_CHARS = 32_000
SIMPLE_PUBLIC_PROMPT_MAX_CHARS = 40_000
HIGH_RISK_STAGE_PROMPT_MAX_CHARS = 48_000
GLOBAL_TRANSPORT_PROMPT_MAX_CHARS = 52_000

_STORE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_SELLER_RE = re.compile(r"^[0-9]{1,32}$")
_SITE_RE = re.compile(r"^[A-Z]{3}$")
_ITEM_RE = re.compile(r"^[A-Z]{3}[0-9]{1,32}$")
_VARIATION_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def content_sha256(value: object) -> str:
    payload = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(payload.encode("utf-8", errors="strict")).hexdigest()


def normalize_sku(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "").strip())
    text = re.sub(r"\s+", " ", text)
    if not text or len(text) > 100 or any(ord(char) < 32 for char in text):
        raise ContextHubValidationError("SKU ausente ou invalido para conhecimento por loja.")
    return text.upper()


def store_directory_name(store_ref: object, store_name: object = "") -> str:
    store = str(store_ref or "").strip()
    if not _STORE_RE.fullmatch(store):
        raise ContextHubValidationError("store_id invalido para conhecimento por loja.")
    normalized = unicodedata.normalize("NFKD", str(store_name or "").strip().casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:64] or "loja"
    return f"{store}--{slug}"


@dataclass(frozen=True, slots=True)
class StoreSkuScope:
    tenant_scope: str
    store_ref: str
    store_name: str
    seller_id: str
    site_id: str
    surface: str = STORE_SKU_PUBLIC_SURFACE

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        tenant_scope: object = "",
    ) -> "StoreSkuScope":
        scope = cls(
            tenant_scope=str(value.get("tenant_scope") or tenant_scope or "").strip(),
            store_ref=str(value.get("store_ref") or value.get("store_id") or "").strip(),
            store_name=str(value.get("store_name") or value.get("loja") or "").strip()[:160],
            seller_id=str(value.get("seller_id") or "").strip(),
            site_id=str(value.get("site_id") or "").strip().upper(),
            surface=str(value.get("surface") or STORE_SKU_PUBLIC_SURFACE).strip(),
        )
        scope.validate()
        return scope

    def validate(self) -> None:
        if not self.tenant_scope or len(self.tenant_scope) > 96:
            raise ContextHubValidationError("tenant_scope ausente para conhecimento por loja.")
        if not _STORE_RE.fullmatch(self.store_ref):
            raise ContextHubValidationError("store_id ausente ou invalido para conhecimento por loja.")
        if not _SELLER_RE.fullmatch(self.seller_id):
            raise ContextHubValidationError("seller_id ausente ou invalido para conhecimento por loja.")
        if not _SITE_RE.fullmatch(self.site_id):
            raise ContextHubValidationError("site_id ausente ou invalido para conhecimento por loja.")
        if self.surface != STORE_SKU_PUBLIC_SURFACE:
            raise ContextHubValidationError("Surface invalida para conhecimento de perguntas publicas.")

    def as_dict(self) -> dict[str, str]:
        return {
            "tenant_scope": self.tenant_scope,
            "store_ref": self.store_ref,
            "store_name": self.store_name,
            "seller_id": self.seller_id,
            "site_id": self.site_id,
            "surface": self.surface,
        }


@dataclass(frozen=True, slots=True)
class StoreSkuBinding:
    item_id: str
    variation_id: str
    sku: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, site_id: str) -> "StoreSkuBinding":
        binding = cls(
            item_id=str(value.get("item_id") or value.get("mlb") or "").strip().upper(),
            variation_id=str(value.get("variation_id") or "").strip(),
            sku=normalize_sku(value.get("sku")),
        )
        binding.validate(site_id=site_id)
        return binding

    def validate(self, *, site_id: str) -> None:
        if not _ITEM_RE.fullmatch(self.item_id) or not self.item_id.startswith(site_id):
            raise ContextHubValidationError("item_id invalido ou divergente do site da loja.")
        if self.variation_id and not _VARIATION_RE.fullmatch(self.variation_id):
            raise ContextHubValidationError("variation_id invalido para conhecimento por loja.")

    def as_dict(self) -> dict[str, str]:
        return {
            "schema": STORE_SKU_BINDING_SCHEMA,
            "item_id": self.item_id,
            "variation_id": self.variation_id,
            "sku": self.sku,
        }


def validate_integral_size(value: object, maximum: int, *, code: str) -> int:
    size = len(canonical_json(value))
    if size > maximum:
        raise ContextHubValidationError(f"{code}:{size}>{maximum}")
    return size


__all__ = [
    "APPLICABLE_GUIDANCE_MAX_CHARS",
    "CANONICAL_DOCUMENT_MAX_CHARS",
    "GLOBAL_TRANSPORT_PROMPT_MAX_CHARS",
    "HIGH_RISK_STAGE_PROMPT_MAX_CHARS",
    "INTEGRAL_ENVELOPE_MAX_CHARS",
    "OPERATIONAL_DATA_MAX_CHARS",
    "QUESTION_HISTORY_MAX_CHARS",
    "SIMPLE_PUBLIC_PROMPT_MAX_CHARS",
    "STORE_SKU_BINDING_SCHEMA",
    "STORE_SKU_MIGRATION_SCHEMA",
    "STORE_SKU_PUBLIC_SURFACE",
    "STORE_SKU_QUESTION_CONTEXT_SCHEMA",
    "StoreSkuBinding",
    "StoreSkuScope",
    "canonical_json",
    "content_sha256",
    "normalize_sku",
    "store_directory_name",
    "validate_integral_size",
]
