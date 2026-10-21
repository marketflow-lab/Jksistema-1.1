"""Recognize obsolete short sales identities using persisted document evidence."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation


def _text(value: object) -> str:
    return "" if value is None else str(value)


def is_short_legacy_sale(item: dict) -> bool:
    """Accept only the exact historical ``account_number_sku`` identity."""
    if any(_text(item.get(field)).strip() for field in ("origem_tipo", "origem_id", "item_id")):
        return False
    account, number, sku = (_text(item.get(field)) for field in ("loja_conta", "numero", "sku"))
    if not all(value.strip() for value in (account, number, sku)):
        return False
    return _text(item.get("id_unico")) == f"{account}_{number}_{sku}"


def _finite_number(value: object) -> Decimal | None:
    try:
        number = Decimal(_text(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _sale_evidence(item: dict) -> tuple | None:
    invoice, number, sku = (
        _text(item.get(field)).strip() for field in ("nota_fiscal_id", "numero", "sku")
    )
    if not all((invoice, number, sku)):
        return None
    day = _text(item.get("data"))[:10]
    try:
        if len(day) != 10 or date.fromisoformat(day).isoformat() != day:
            return None
    except ValueError:
        return None
    quantity, amount = (_finite_number(item.get(field)) for field in ("quantidade", "valor"))
    if quantity is None or amount is None:
        return None
    return invoice, number, sku, day, quantity, amount


def _buyer(item: dict) -> str:
    return " ".join(_text(item.get("comprador")).split()).casefold()


def without_legacy_sale_duplicates(
    rows: list[dict], *, blocked_store_names: set[str] | None = None
) -> list[dict]:
    """Suppress proven short legacy copies while preserving every typed item.

    Missing account IDs may be adopted only through a literal account name with
    one observed stable ID. Callers can block names whose ambiguity is known
    outside the supplied rows. Missing or conflicting document evidence keeps
    the original row. The input rows and their order are preserved.
    """
    store_ids_by_name: dict[str, set[str]] = defaultdict(set)
    typed_buyers: dict[tuple, set[str]] = defaultdict(set)
    for item in rows:
        store_id = _text(item.get("store_id")).strip()
        if not store_id:
            continue
        name = _text(item.get("loja_conta"))
        if name.strip():
            store_ids_by_name[name].add(store_id)
        if _text(item.get("origem_tipo")).strip() not in {"pedido", "nfe_saida"}:
            continue
        if not all(_text(item.get(field)).strip() for field in ("origem_id", "item_id")):
            continue
        evidence = _sale_evidence(item)
        if evidence is not None:
            typed_buyers[(store_id, evidence)].add(_buyer(item))

    blocked = blocked_store_names or set()
    result = []
    for item in rows:
        if not is_short_legacy_sale(item) or item.get("__legacy_store_ambiguous"):
            result.append(item)
            continue
        evidence = _sale_evidence(item)
        if evidence is None:
            result.append(item)
            continue
        store_id = _text(item.get("store_id")).strip()
        if not store_id:
            name = _text(item.get("loja_conta"))
            candidates = store_ids_by_name.get(name, set())
            if name in blocked or len(candidates) != 1:
                result.append(item)
                continue
            store_id = next(iter(candidates))
        buyers = typed_buyers.get((store_id, evidence), set())
        buyer = _buyer(item)
        if not buyers or (buyer and "" not in buyers and buyer not in buyers):
            result.append(item)
    return result
