"""Extract only explicit operational references, never raw invoice/customer text."""

from __future__ import annotations

import re
import unicodedata


REFERENCE_FIELDS = ("order_id", "bling_order_id", "original_invoice_id",
                    "original_invoice_number", "bling_order_number")


def store_reference(note: dict, document: dict) -> None:
    """Store scalar, allowlisted metadata compatible with durable checkpoints."""
    reference = extract_reference(document)
    for key in (*REFERENCE_FIELDS, "reference_conflict"):
        field = "_return_reason_" + key
        note.pop(field, None)
        if key in reference:
            note[field] = reference[key]


def reference_from_note(note: dict) -> dict:
    return {key: str(note["_return_reason_" + key]) for key in REFERENCE_FIELDS
            if numeric_id(note.get("_return_reason_" + key))}


def numeric_id(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    text = str(value).strip()
    return text if re.fullmatch(r"[1-9][0-9]{0,31}", text) else ""


def document_number(value) -> str:
    text = str(value or "").strip()
    return str(int(text)) if re.fullmatch(r"[0-9]{1,20}", text) and int(text) > 0 else ""


def extract_reference(document: dict) -> dict:
    """numeroPedidoLoja/numeroLoja are shop IDs; N/V origins are Bling numbers."""
    if not isinstance(document, dict):
        return {}
    candidates = {numeric_id(document.get(key)) for key in ("numeroPedidoLoja", "numeroLoja")}
    candidates.discard("")
    if len(candidates) > 1:
        return {"reference_conflict": True}
    result = {"order_id": next(iter(candidates))} if candidates else {}
    pedido = document.get("pedidoVenda")
    if isinstance(pedido, dict) and numeric_id(pedido.get("id")):
        result["bling_order_id"] = numeric_id(pedido["id"])
    origins = set()
    for key in ("origem_codigo", "origem", "origemDocumento", "documentoOrigem", "referenciaOrigem"):
        origin = document.get(key)
        if isinstance(origin, dict):
            origin = f"{origin.get('tipo') or origin.get('sigla') or ''} {origin.get('numero') or ''}"
        match = re.fullmatch(r"\s*([NV])\s+([0-9]{1,20})\s*", str(origin or ""), re.I)
        if match and document_number(match[2]):
            field = "original_invoice_number" if match[1].upper() == "N" else "bling_order_number"
            origins.add((field, document_number(match[2])))
    if len(origins) > 1:
        return {"reference_conflict": True}
    if origins:
        field, number = next(iter(origins))
        result[field] = number
    return result


def safe_explicit_reason(value) -> str:
    """Keep a labelled reason, rejecting personal identifiers and secret-like text."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.strip().split())
    if not 1 <= len(text) <= 240:
        return ""
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    if (re.search(r"@|https?://|\d[\d\s()./+\-]{6,}\d", text, re.I)
            or re.search(r"\b(?:cpf|cnpj|telefone|email|e-mail|token|bearer|senha|secret)\b"
                         r"|access[_ -]?token|refresh[_ -]?token|api[_ .-]?key|authorization", normalized)):
        return ""
    return text


def explicit_reason(document: dict, sku: str = "") -> str:
    """Accept explicit fields or a labelled line, never classify free text/CFOP."""
    candidates = set()
    for key in ("motivoDevolucao", "motivo_devolucao"):
        reason = safe_explicit_reason(document.get(key))
        if reason:
            candidates.add(reason)
    texts = [document.get("observacoes"), document.get("observacoesInternas")]
    items = document.get("itens")
    if sku and isinstance(items, list):
        matches = [item for item in items if isinstance(item, dict)
                   and str(item.get("codigo") or item.get("sku") or "").strip().upper() == sku.strip().upper()]
        # A duplicated SKU cannot identify an individual invoice line.
        if len(matches) == 1:
            texts.append(matches[0].get("informacoesAdicionais"))
    for text in texts:
        for line in str(text or "").splitlines():
            match = re.fullmatch(r"\s*motivo\s+(?:da\s+)?devolu[cç][aã]o\s*:\s*(.+?)\s*", line, re.I)
            reason = safe_explicit_reason(match[1]) if match else ""
            if reason:
                candidates.add(reason)
    return next(iter(candidates)) if len(candidates) == 1 else ""
