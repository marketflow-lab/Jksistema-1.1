"""Lossless model-context projection for Mercado Livre customer replies."""

from __future__ import annotations

from copy import deepcopy


_DUPLICATE_AGENT_KEYS = frozenset({
    # The unified envelope already carries the server-owned identity and the
    # versioned response policy. Keeping these copies makes the largest
    # free-text prompt, including the listing description, travel twice.
    "prompt",
    "app_guidance",
    "app_guidance_source",
    "app_guidance_truth_class",
    "app_guidance_usage",
    "commercial_method_version",
})


def _same_value(left: object, right: object) -> bool:
    if isinstance(left, str) or isinstance(right, str):
        return str(left or "").strip() == str(right or "").strip()
    return left == right


def _remove_matching(payload: dict, key: str, expected: object) -> None:
    if key in payload and _same_value(payload.get(key), expected):
        payload.pop(key, None)


def unified_agent_input_for_model(
    agent_input: dict,
    *,
    server_identity: dict,
    response_signature: str,
) -> dict:
    """Return model input without copies already present in the unified envelope.

    The complete input remains available to server-side authorization,
    research, diagnostics and evidence persistence. Context Hub/Obsidian
    documents live in ``initial_read_only_context`` and never enter this
    filter.
    """

    projected = deepcopy(agent_input)
    for key in _DUPLICATE_AGENT_KEYS:
        projected.pop(key, None)

    _remove_matching(projected, "tenant_id", server_identity.get("tenant_id"))
    _remove_matching(projected, "store", server_identity.get("store"))

    question = projected.get("question") if isinstance(projected.get("question"), dict) else {}
    item = projected.get("item") if isinstance(projected.get("item"), dict) else {}
    input_context = projected.get("context") if isinstance(projected.get("context"), dict) else {}

    canonical_permalink = item.get("permalink") or item.get("url") or item.get("link")
    for alias in ("link", "url"):
        _remove_matching(item, alias, canonical_permalink)

    _remove_matching(question, "item_id", server_identity.get("item_id"))
    question.pop("history_count", None)
    question.pop("history_source", None)

    duplicate_context_values = {
        "tenant_id": server_identity.get("tenant_id"),
        "loja": server_identity.get("store"),
        "store_id": server_identity.get("store_id"),
        "seller_id": server_identity.get("seller_id"),
        "site_id": server_identity.get("site_id"),
        "question_id": question.get("id"),
        "item_id": server_identity.get("item_id"),
        "variation_id": server_identity.get("variation_id"),
        "order_id": server_identity.get("order_id"),
        "titulo": item.get("title"),
        "sku": server_identity.get("sku"),
        "permalink": canonical_permalink,
        "descricao": item.get("description"),
        "pergunta": question.get("text"),
        "assinatura_obrigatoria": response_signature,
        "intencao_atendimento": projected.get("intent"),
    }
    for key, expected in duplicate_context_values.items():
        _remove_matching(input_context, key, expected)
    input_context.pop("descricao_chars", None)
    input_context.pop("descricao_disponivel", None)
    if input_context.get("busca_outra_peca") in (None, "", [], {}):
        input_context.pop("busca_outra_peca", None)

    if item:
        projected["item"] = item
    if question:
        projected["question"] = question
    if input_context:
        projected["context"] = input_context
    else:
        projected.pop("context", None)
    return projected


__all__ = ["unified_agent_input_for_model"]
