"""Stable facade for tenant-scoped structured product evidence."""

from backend.modules.context_hub.product_evidence_model import (
    NormalizedEvidenceValue,
    PRODUCT_EVIDENCE_POLICY,
    PRODUCT_EVIDENCE_SCOPES,
    PRODUCT_EVIDENCE_SOURCE_TYPES,
    normalize_product_evidence_value,
)
from backend.modules.context_hub.product_evidence_repository import (
    add_product_evidence_claim,
    add_product_evidence_source,
    complete_product_evidence_batch,
    create_product_evidence_batch,
    list_verified_product_evidence,
)


__all__ = [
    "NormalizedEvidenceValue",
    "PRODUCT_EVIDENCE_POLICY",
    "PRODUCT_EVIDENCE_SCOPES",
    "PRODUCT_EVIDENCE_SOURCE_TYPES",
    "add_product_evidence_claim",
    "add_product_evidence_source",
    "complete_product_evidence_batch",
    "create_product_evidence_batch",
    "list_verified_product_evidence",
    "normalize_product_evidence_value",
]
