"""Deterministic secondary index of SKU compatibility by vehicle and year.

The canonical source remains each tenant's ``info/<client>/SKU/*.json`` file.
This module only normalizes that evidence into a generated, auditable tree.  It
does not infer missing years and deliberately fails closed on expressions that
are not an exact year or an inclusive year range.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
import hashlib
import re
import unicodedata
from typing import Any


SKU_VEHICLE_TREE_VERSION = "1.0.0"
VEHICLE_YEAR_TREE_VERSION = SKU_VEHICLE_TREE_VERSION
VEHICLE_YEAR_TREE_SOURCE_REF = "info/<client_id>/SKU/*.json"
VEHICLE_TREE_TRUTH_CLASS = "generated_secondary"
MAX_ITEMS_PER_PART = 12
MIN_VEHICLE_YEAR = 1886
MAX_VEHICLE_YEAR = 2100
MAX_YEAR_RANGE_SIZE = 150

_SINGLE_YEAR_RE = re.compile(r"^(\d{4})$")
_YEAR_RANGE_RE = re.compile(r"^(\d{4}) a (\d{4})$")


def _identity_text(value: Any) -> str:
    """Case/spacing-insensitive identity that does not collapse punctuation."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"\s+", " ", text)


def _ascii_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.casefold()


def _slug(value: Any, *, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _ascii_text(value)).strip("-")
    return slug or fallback


def _digest(*parts: Any) -> str:
    canonical = "\x1f".join(_identity_text(part) for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]


def vehicle_brand_id(brand: str) -> str:
    """Return a stable brand id; the hash disambiguates equal slugs."""

    return f"jk:sku-vehicle:brand:{_slug(brand, fallback='marca')}--{_digest(brand)}"


def vehicle_model_id(brand: str, model: str) -> str:
    """Return a stable model id whose path always includes its brand."""

    return (
        "jk:sku-vehicle:model:"
        f"{_slug(brand, fallback='marca')}:"
        f"{_slug(model, fallback='modelo')}--{_digest(brand, model)}"
    )


def vehicle_year_id(brand: str, model: str, year: int | str) -> str:
    """Return a stable year id whose path always includes brand and model."""

    year_text = str(year)
    return (
        "jk:sku-vehicle:year:"
        f"{_slug(brand, fallback='marca')}:"
        f"{_slug(model, fallback='modelo')}:"
        f"{year_text}--{_digest(brand, model, year_text)}"
    )


_SKU_214_OVERRIDE_ID = "sku-214-fitment-reviewed-2026-07-22"
_SKU_214_OVERRIDES: dict[tuple[str, str, str], dict[str, Any]] = {
    (
        _identity_text("Hyundai"),
        _identity_text("HB20 1.0 e 1.6 com chave mecânica"),
        "2012 em diante",
    ): {
        "model": "HB20",
        "start": 2012,
        "end": 2025,
        "status": "vigente",
        "verified_through": 2025,
        "restrictions": [
            "somente versões com chave mecânica",
            "motores 1.0 e 1.6",
        ],
    },
    (
        _identity_text("Hyundai"),
        _identity_text("HB20S com chave mecânica"),
        "2013 em diante",
    ): {
        "model": "HB20S",
        "start": 2013,
        "end": 2025,
        "status": "vigente",
        "verified_through": 2025,
        "restrictions": ["somente versões com chave mecânica"],
    },
    (
        _identity_text("Hyundai"),
        _identity_text("HB20X com chave mecânica"),
        "2013 em diante",
    ): {
        "model": "HB20X",
        "start": 2013,
        "end": 2022,
        "status": "fechado",
        "verified_through": None,
        "restrictions": ["somente versões com chave mecânica"],
    },
    (
        _identity_text("Hyundai"),
        _identity_text("i20"),
        "2008 em diante",
    ): {
        "model": "i20 I (PB/PBT)",
        "start": 2008,
        "end": 2015,
        "status": "fechado",
        "verified_through": None,
        "restrictions": [],
    },
}


def _pending(
    *,
    sku: str,
    product_name: str,
    brand: str = "",
    model: str = "",
    raw_year: Any = None,
    reason: str,
    source_ref: str,
) -> dict[str, Any]:
    return {
        "sku": str(sku),
        "product_name": str(product_name),
        "brand": brand,
        "model": model,
        "raw_year": raw_year,
        "reason": reason,
        "source_ref": str(source_ref),
    }

def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    if any(not isinstance(item, str) for item in value):
        return None
    return [item.strip() for item in value if item.strip()]


def _exact_year_list(value: Any) -> list[str] | None:
    """Keep year spelling byte-for-byte so whitespace cannot widen the grammar."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    if any(not isinstance(item, str) for item in value):
        return None
    return list(value)


def _display_choice(values: Iterable[str]) -> str:
    return min(
        {str(value).strip() for value in values if str(value).strip()},
        key=lambda value: (_identity_text(value), value),
    )


def _parse_closed_years(raw_year: str) -> range | None:
    single = _SINGLE_YEAR_RE.fullmatch(raw_year)
    if single:
        year = int(single.group(1))
        if not MIN_VEHICLE_YEAR <= year <= MAX_VEHICLE_YEAR:
            return None
        return range(year, year + 1)
    interval = _YEAR_RANGE_RE.fullmatch(raw_year)
    if not interval:
        return None
    start, end = (int(interval.group(1)), int(interval.group(2)))
    if (
        end < start
        or start < MIN_VEHICLE_YEAR
        or end > MAX_VEHICLE_YEAR
        or end - start + 1 > MAX_YEAR_RANGE_SIZE
    ):
        return None
    return range(start, end + 1)


def normalize_sku_vehicle_years(
    *,
    sku: str,
    product_name: str,
    vehicle_items: Sequence[Mapping[str, Any]] | Any,
    source_ref: str,
) -> dict[str, Any]:
    """Expand safe compatibility expressions into unique year records.

    Only ``AAAA`` and ``AAAA a AAAA`` are accepted.  Multiple entries and
    overlapping ranges are unioned without filling gaps.  The four reviewed
    open ranges of SKU 214 are the sole explicit overrides in version 1.0.0.
    ``application_count`` is the number of unique expanded records.
    """

    sku_text = str(sku).strip()
    product_text = str(product_name).strip()
    source_text = str(source_ref).strip()
    pending: list[dict[str, Any]] = []
    candidates: dict[tuple[str, str, int], dict[str, Any]] = {}
    conflicting_keys: set[tuple[str, str, int]] = set()
    override_keys: set[tuple[str, str, str]] = set()

    if not isinstance(vehicle_items, Sequence) or isinstance(
        vehicle_items, (str, bytes, bytearray)
    ):
        pending.append(
            _pending(
                sku=sku_text,
                product_name=product_text,
                reason="vehicle_items_invalid",
                source_ref=source_text,
            )
        )
        return {
            "tree_version": SKU_VEHICLE_TREE_VERSION,
            "truth_class": VEHICLE_TREE_TRUTH_CLASS,
            "records": [],
            "pending": pending,
            "application_count": 0,
            "overrides_applied": 0,
        }

    for item in vehicle_items:
        if not isinstance(item, Mapping):
            pending.append(
                _pending(
                    sku=sku_text,
                    product_name=product_text,
                    raw_year=item,
                    reason="vehicle_item_invalid",
                    source_ref=source_text,
                )
            )
            continue
        raw_brand = item.get("marca")
        raw_model = item.get("modelo")
        brand = raw_brand.strip() if isinstance(raw_brand, str) else ""
        model = raw_model.strip() if isinstance(raw_model, str) else ""
        if not brand or not model:
            pending.append(
                _pending(
                    sku=sku_text,
                    product_name=product_text,
                    brand=brand,
                    model=model,
                    raw_year=item.get("anos"),
                    reason="vehicle_identity_missing",
                    source_ref=source_text,
                )
            )
            continue
        year_expressions = _exact_year_list(item.get("anos"))
        restrictions = _string_list(item.get("restrições", []))
        if restrictions is None:
            pending.append(
                _pending(
                    sku=sku_text,
                    product_name=product_text,
                    brand=brand,
                    model=model,
                    raw_year=item.get("anos"),
                    reason="restrictions_invalid",
                    source_ref=source_text,
                )
            )
            continue
        if year_expressions is None or not year_expressions:
            pending.append(
                _pending(
                    sku=sku_text,
                    product_name=product_text,
                    brand=brand,
                    model=model,
                    raw_year=item.get("anos"),
                    reason="years_invalid_or_empty",
                    source_ref=source_text,
                )
            )
            continue

        for raw_year in sorted(set(year_expressions)):
            override_key = (_identity_text(brand), _identity_text(model), raw_year)
            override = _SKU_214_OVERRIDES.get(override_key) if sku_text == "214" else None
            if override is not None:
                display_model = str(override["model"])
                years = range(int(override["start"]), int(override["end"]) + 1)
                status = str(override["status"])
                verified_through = override["verified_through"]
                override_restrictions = [
                    str(item) for item in override.get("restrictions", [])
                ]
                override_id = _SKU_214_OVERRIDE_ID
                override_keys.add(override_key)
            else:
                parsed = _parse_closed_years(raw_year)
                if parsed is None:
                    pending.append(
                        _pending(
                            sku=sku_text,
                            product_name=product_text,
                            brand=brand,
                            model=model,
                            raw_year=raw_year,
                            reason="unsupported_or_ambiguous_year_expression",
                            source_ref=source_text,
                        )
                    )
                    continue
                display_model = model
                years = parsed
                status = "fechado"
                verified_through = None
                override_restrictions = []
                override_id = ""

            for year in years:
                key = (_identity_text(brand), _identity_text(display_model), int(year))
                if key in conflicting_keys:
                    continue
                candidate = candidates.setdefault(
                    key,
                    {
                        "sku": sku_text,
                        "product_names": set(),
                        "brands": set(),
                        "models": set(),
                        "year": int(year),
                        "status": status,
                        "verified_through": verified_through,
                        "restrictions": set(),
                        "source_refs": set(),
                        "override_ids": set(),
                    },
                )
                if (
                    candidate["status"] != status
                    or candidate["verified_through"] != verified_through
                ):
                    pending.append(
                        _pending(
                            sku=sku_text,
                            product_name=product_text,
                            brand=brand,
                            model=display_model,
                            raw_year=raw_year,
                            reason="conflicting_year_evidence",
                            source_ref=source_text,
                        )
                    )
                    candidates.pop(key, None)
                    conflicting_keys.add(key)
                    continue
                candidate["product_names"].add(product_text)
                candidate["brands"].add(brand)
                candidate["models"].add(display_model)
                candidate["restrictions"].update(
                    [*restrictions, *override_restrictions]
                )
                candidate["source_refs"].add(source_text)
                if override_id:
                    candidate["override_ids"].add(override_id)

    records: list[dict[str, Any]] = []
    for (_brand_key, _model_key, _year), item in sorted(candidates.items()):
        records.append(
            {
                "sku": item["sku"],
                "product_name": _display_choice(item["product_names"]),
                "brand": _display_choice(item["brands"]),
                "model": _display_choice(item["models"]),
                "year": item["year"],
                "status": item["status"],
                "verified_through": item["verified_through"],
                "restrictions": sorted(item["restrictions"], key=_identity_text),
                "source_ref": _display_choice(item["source_refs"]),
                "tree_version": SKU_VEHICLE_TREE_VERSION,
                "truth_class": VEHICLE_TREE_TRUTH_CLASS,
                "override_id": _display_choice(item["override_ids"])
                if item["override_ids"]
                else "",
            }
        )
    pending.sort(
        key=lambda item: (
            _identity_text(item.get("brand")),
            _identity_text(item.get("model")),
            str(item.get("raw_year")),
            str(item.get("reason")),
        )
    )
    return {
        "tree_version": SKU_VEHICLE_TREE_VERSION,
        "truth_class": VEHICLE_TREE_TRUTH_CLASS,
        "records": records,
        "pending": pending,
        "application_count": len(records),
        "overrides_applied": len(override_keys),
    }


def _part_id(kind: str, owner_id: str, index: int, item_ids: Sequence[str]) -> str:
    return (
        f"jk:sku-vehicle:{kind}-part:{_slug(owner_id)}:"
        f"{index:03d}--{_digest(owner_id, index, *item_ids)}"
    )


def _chunks(values: Sequence[Any], size: int = MAX_ITEMS_PER_PART) -> list[list[Any]]:
    return [list(values[index : index + size]) for index in range(0, len(values), size)]


def _tree_record_key(record: Mapping[str, Any]) -> tuple[str, str, int, str]:
    raw_year = record.get("year")
    if isinstance(raw_year, bool):
        raise ValueError("vehicle year record has an invalid year")
    if isinstance(raw_year, int):
        year = raw_year
    elif isinstance(raw_year, str) and _SINGLE_YEAR_RE.fullmatch(raw_year):
        year = int(raw_year)
    else:
        raise ValueError("vehicle year record has an invalid year")
    brand = str(record.get("brand") or "").strip()
    model = str(record.get("model") or "").strip()
    sku = str(record.get("sku") or "").strip()
    if not brand or not model or not sku or not _SINGLE_YEAR_RE.fullmatch(str(year)):
        raise ValueError("vehicle year record is missing a stable identity")
    if not MIN_VEHICLE_YEAR <= year <= MAX_VEHICLE_YEAR:
        raise ValueError("vehicle year record has an implausible year")
    return (_identity_text(brand), _identity_text(model), year, _identity_text(sku))


def _deduplicate_tree_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for raw in records:
        if not isinstance(raw, Mapping):
            raise ValueError("vehicle year records must be mappings")
        key = _tree_record_key(raw)
        normalized = {
            "sku": str(raw.get("sku") or "").strip(),
            "product_name": str(raw.get("product_name") or "").strip(),
            "brand": str(raw.get("brand") or "").strip(),
            "model": str(raw.get("model") or "").strip(),
            "year": int(raw.get("year")),
            "status": str(raw.get("status") or "").strip(),
            "verified_through": raw.get("verified_through"),
            "restrictions": _string_list(raw.get("restrictions", [])),
            "source_ref": str(raw.get("source_ref") or "").strip(),
            "tree_version": SKU_VEHICLE_TREE_VERSION,
            "truth_class": VEHICLE_TREE_TRUTH_CLASS,
            "override_id": str(raw.get("override_id") or "").strip(),
        }
        if normalized["restrictions"] is None:
            raise ValueError("vehicle year record has invalid restrictions")
        existing = merged.get(key)
        if existing is None:
            merged[key] = normalized
            continue
        comparable = ("status", "verified_through", "product_name", "source_ref", "override_id")
        if any(existing[field] != normalized[field] for field in comparable):
            raise ValueError("ambiguous duplicate vehicle year record")
        existing["sku"] = _display_choice((existing["sku"], normalized["sku"]))
        existing["brand"] = _display_choice((existing["brand"], normalized["brand"]))
        existing["model"] = _display_choice((existing["model"], normalized["model"]))
        existing["restrictions"] = sorted(
            set(existing["restrictions"]) | set(normalized["restrictions"]),
            key=_identity_text,
        )
    return sorted(
        merged.values(),
        key=lambda item: (
            _identity_text(item["brand"]),
            _identity_text(item["model"]),
            int(item["year"]),
            _identity_text(item["sku"]),
        ),
    )


def build_vehicle_year_tree(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a bounded-degree brand/model page graph with inline year groups.

    Continuation parts form a chain.  Consequently each part has at most 12
    item children plus one continuation child and one parent (degree <= 14),
    even for unusually large catalogs.  Years are headings inside each model
    page, never individual pages, so a long application range does not inflate
    either the graph degree or the number of generated files.
    """

    normalized_records = _deduplicate_tree_records(records)
    root_id = "jk:sku-vehicle:root"
    nodes: list[dict[str, Any]] = []
    page_specs: list[dict[str, Any]] = []

    by_brand: dict[str, list[dict[str, Any]]] = defaultdict(list)
    brand_labels: dict[str, set[str]] = defaultdict(set)
    for record in normalized_records:
        brand_key = _identity_text(record["brand"])
        by_brand[brand_key].append(record)
        brand_labels[brand_key].add(record["brand"])
    ordered_brands = [
        (_display_choice(brand_labels[key]), by_brand[key])
        for key in sorted(by_brand)
    ]

    nodes_by_id: dict[str, dict[str, Any]] = {}

    def add_node(
        *,
        node_id: str,
        kind: str,
        label: str,
        parent_id: str,
        path: Sequence[str],
        child_ids: Sequence[str] = (),
        node_records: Sequence[Mapping[str, Any]] = (),
        year_groups: Sequence[Mapping[str, Any]] = (),
        relative_path: str,
    ) -> None:
        node = {
            "id": node_id,
            "kind": kind,
            "label": label,
            "parent_id": parent_id,
            "child_ids": list(child_ids),
            "path": list(path),
            "record_count": len(node_records),
            "year_group_count": len(year_groups),
        }
        if node_id in nodes_by_id:
            raise ValueError(f"duplicate vehicle tree node id: {node_id}")
        nodes_by_id[node_id] = node
        nodes.append(node)
        page_specs.append(
            {
                "node_id": node_id,
                "kind": kind,
                "title": label,
                "relative_path": relative_path,
                "parent_id": parent_id,
                "child_ids": list(child_ids),
                "records": [dict(record) for record in node_records],
                "year_groups": [dict(group) for group in year_groups],
            }
        )

    brand_nodes: list[tuple[str, str, list[dict[str, Any]]]] = []
    for brand, brand_records in ordered_brands:
        brand_nodes.append((vehicle_brand_id(brand), brand, brand_records))
    brand_parts = _chunks(brand_nodes)
    root_group_ids = [
        _part_id("brand", root_id, index, [item[0] for item in part])
        for index, part in enumerate(brand_parts, start=1)
    ]
    add_node(
        node_id=root_id,
        kind="root",
        label="Veículos compatíveis por marca, modelo e ano",
        parent_id="",
        path=("Veículos compatíveis",),
        child_ids=root_group_ids[:1],
        relative_path="Veiculos-compativeis/Arvore.md",
    )
    for index, part in enumerate(brand_parts):
        part_id = root_group_ids[index]
        child_ids = [item[0] for item in part]
        if index + 1 < len(root_group_ids):
            child_ids.append(root_group_ids[index + 1])
        parent_id = root_id if index == 0 else root_group_ids[index - 1]
        add_node(
            node_id=part_id,
            kind="brand_part",
            label=f"Marcas — parte {index + 1}",
            parent_id=parent_id,
            path=("Veículos compatíveis", f"Marcas — parte {index + 1}"),
            child_ids=child_ids,
            relative_path=f"Veiculos-compativeis/Marcas/parte-{index + 1:03d}.md",
        )

    for brand_id, brand, brand_records in brand_nodes:
        brand_slug = f"{_slug(brand, fallback='marca')}--{_digest(brand)}"
        by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
        model_labels: dict[str, set[str]] = defaultdict(set)
        for record in brand_records:
            model_key = _identity_text(record["model"])
            by_model[model_key].append(record)
            model_labels[model_key].add(record["model"])
        model_nodes = [
            (
                vehicle_model_id(brand, _display_choice(model_labels[key])),
                _display_choice(model_labels[key]),
                by_model[key],
            )
            for key in sorted(by_model)
        ]
        model_parts = _chunks(model_nodes)
        model_part_ids = [
            _part_id("model", brand_id, index, [item[0] for item in part])
            for index, part in enumerate(model_parts, start=1)
        ]
        add_node(
            node_id=brand_id,
            kind="brand",
            label=brand,
            parent_id=next(
                group_id
                for group_id, part in zip(root_group_ids, brand_parts)
                if any(item[0] == brand_id for item in part)
            ),
            path=("Veículos compatíveis", brand),
            child_ids=model_part_ids[:1],
            relative_path=f"Veiculos-compativeis/{brand_slug}/Marca.md",
        )
        for index, part in enumerate(model_parts):
            part_id = model_part_ids[index]
            child_ids = [item[0] for item in part]
            if index + 1 < len(model_part_ids):
                child_ids.append(model_part_ids[index + 1])
            parent_id = brand_id if index == 0 else model_part_ids[index - 1]
            add_node(
                node_id=part_id,
                kind="model_part",
                label=f"{brand} — modelos — parte {index + 1}",
                parent_id=parent_id,
                path=("Veículos compatíveis", brand, f"Modelos — parte {index + 1}"),
                child_ids=child_ids,
                relative_path=(
                    f"Veiculos-compativeis/{brand_slug}/Modelos/parte-{index + 1:03d}.md"
                ),
            )

        for model_id, model, model_records in model_nodes:
            model_slug = f"{_slug(model, fallback='modelo')}--{_digest(brand, model)}"
            by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for record in model_records:
                by_year[int(record["year"])].append(record)
            year_groups = [
                {
                    "id": vehicle_year_id(brand, model, year),
                    "year": year,
                    "records": [dict(record) for record in by_year[year]],
                    "render_individually": False,
                }
                for year in sorted(by_year)
            ]
            add_node(
                node_id=model_id,
                kind="model",
                label=model,
                parent_id=next(
                    part_id
                    for part_id, part in zip(model_part_ids, model_parts)
                    if any(item[0] == model_id for item in part)
                ),
                path=("Veículos compatíveis", brand, model),
                node_records=model_records,
                year_groups=year_groups,
                relative_path=(
                    f"Veiculos-compativeis/{brand_slug}/{model_slug}/Modelo.md"
                ),
            )

    max_degree = max(
        (
            len(node["child_ids"]) + (1 if node["parent_id"] else 0)
            for node in nodes
        ),
        default=0,
    )
    if max_degree > 20:
        raise ValueError("vehicle tree exceeds the maximum node degree")
    return {
        "tree_version": SKU_VEHICLE_TREE_VERSION,
        "truth_class": VEHICLE_TREE_TRUTH_CLASS,
        "root_id": root_id,
        "root_group_ids": root_group_ids,
        "nodes": nodes,
        "page_specs": page_specs,
        "stats": {
            "records": len(normalized_records),
            "brands": len(brand_nodes),
            "models": sum(1 for node in nodes if node["kind"] == "model"),
            "years": sum(
                len(page["year_groups"])
                for page in page_specs
                if page["kind"] == "model"
            ),
            "distinct_years": len({int(record["year"]) for record in normalized_records}),
            "nodes": len(nodes),
            "pages": len(page_specs),
            "model_parts": sum(1 for node in nodes if node["kind"] == "model_part"),
            "max_degree": max_degree,
        },
    }
