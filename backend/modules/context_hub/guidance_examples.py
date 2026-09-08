"""Scope validation for public question examples in editorial notes."""

from collections.abc import Mapping

from .contracts import ContextHubValidationError
from .store_sku_contracts import normalize_sku


def validate_guidance_examples(guidance: Mapping, sku: str = "") -> None:
    examples = guidance.get("exemplos_perguntas", [])
    if not isinstance(examples, list):
        raise ContextHubValidationError("Modelos de perguntas devem ser uma lista.")
    for example in examples:
        if not isinstance(example, Mapping):
            raise ContextHubValidationError("Modelo de pergunta invalido.")
        example_sku = str(example.get("sku") or "").strip()
        if example_sku and (not sku or normalize_sku(example_sku) != normalize_sku(sku)):
            raise ContextHubValidationError("Modelo de SKU deve estar na nota do mesmo produto, nunca nas orientacoes gerais.")
