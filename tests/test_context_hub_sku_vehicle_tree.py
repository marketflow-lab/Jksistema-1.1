from __future__ import annotations

from copy import deepcopy
import random

import pytest

from backend.services.context_hub_sku_vehicle_tree import (
    MAX_ITEMS_PER_PART,
    MAX_VEHICLE_YEAR,
    MIN_VEHICLE_YEAR,
    VEHICLE_TREE_TRUTH_CLASS,
    VEHICLE_YEAR_TREE_SOURCE_REF,
    VEHICLE_YEAR_TREE_VERSION,
    build_vehicle_year_tree,
    normalize_sku_vehicle_years,
    vehicle_brand_id,
    vehicle_model_id,
    vehicle_year_id,
)


SOURCE = "info/000002/SKU/TESTE.json"


def _normalize(items: list[dict[str, object]], *, sku: str = "TESTE") -> dict[str, object]:
    return normalize_sku_vehicle_years(
        sku=sku,
        product_name="Peça de teste",
        vehicle_items=items,
        source_ref=SOURCE,
    )


def test_public_contract_is_versioned_and_secondary() -> None:
    result = _normalize([])

    assert VEHICLE_YEAR_TREE_VERSION == "1.0.0"
    assert VEHICLE_YEAR_TREE_SOURCE_REF == "info/<client_id>/SKU/*.json"
    assert result == {
        "tree_version": "1.0.0",
        "truth_class": VEHICLE_TREE_TRUTH_CLASS,
        "records": [],
        "pending": [],
        "application_count": 0,
        "overrides_applied": 0,
    }


@pytest.mark.parametrize(
    ("expressions", "expected"),
    (
        (["2012"], [2012]),
        (["2012 a 2014"], [2012, 2013, 2014]),
        (["2012", "2014 a 2015"], [2012, 2014, 2015]),
    ),
)
def test_accepts_only_exact_year_and_inclusive_range(
    expressions: list[str], expected: list[int]
) -> None:
    result = _normalize(
        [{"marca": "Ford", "modelo": "Focus", "anos": expressions}]
    )

    assert [record["year"] for record in result["records"]] == expected
    assert result["pending"] == []
    assert all(record["status"] == "fechado" for record in result["records"])


@pytest.mark.parametrize(
    "raw_year",
    (
        "2012 em diante",
        "a partir de 2012",
        "2012/2013",
        "2012 até 2014",
        "12",
        "2012 a 14",
        "2014 a 2012",
        " 2012 ",
    ),
)
def test_unsupported_or_ambiguous_year_expression_fails_closed(raw_year: str) -> None:
    result = _normalize(
        [{"marca": "Ford", "modelo": "Focus", "anos": [raw_year]}]
    )

    assert result["records"] == []
    assert result["application_count"] == 0
    assert result["pending"][0]["raw_year"] == raw_year
    assert result["pending"][0]["reason"] == "unsupported_or_ambiguous_year_expression"


@pytest.mark.parametrize(
    "raw_year",
    (
        str(MIN_VEHICLE_YEAR - 1),
        str(MAX_VEHICLE_YEAR + 1),
        f"{MIN_VEHICLE_YEAR} a {MIN_VEHICLE_YEAR + 150}",
    ),
)
def test_implausible_or_defensively_large_ranges_fail_closed(raw_year: str) -> None:
    result = _normalize(
        [{"marca": "Ford", "modelo": "Focus", "anos": [raw_year]}]
    )

    assert result["records"] == []
    assert result["pending"][0]["reason"] == "unsupported_or_ambiguous_year_expression"


def test_lists_are_unioned_deduplicated_and_gaps_are_preserved() -> None:
    result = _normalize(
        [
            {
                "marca": "Honda",
                "modelo": "Civic",
                "anos": ["2010 a 2012", "2012", "2015"],
                "restrições": ["Confirmar conector"],
            },
            {
                "marca": "Honda",
                "modelo": "Civic",
                "anos": ["2011", "2015"],
                "restrições": ["Confirmar OEM", "Confirmar conector"],
            },
        ]
    )

    assert [record["year"] for record in result["records"]] == [2010, 2011, 2012, 2015]
    assert 2013 not in [record["year"] for record in result["records"]]
    assert 2014 not in [record["year"] for record in result["records"]]
    restrictions_by_year = {
        record["year"]: record["restrictions"] for record in result["records"]
    }
    assert restrictions_by_year == {
        2010: ["Confirmar conector"],
        2011: ["Confirmar conector", "Confirmar OEM"],
        2012: ["Confirmar conector"],
        2015: ["Confirmar conector", "Confirmar OEM"],
    }


def test_malformed_identity_restrictions_and_year_list_fail_closed() -> None:
    result = _normalize(
        [
            {"marca": "", "modelo": "Focus", "anos": ["2012"]},
            {"marca": 123, "modelo": "Focus", "anos": ["2012"]},
            {"marca": "Ford", "modelo": "Focus", "anos": "2012"},
            {
                "marca": "Ford",
                "modelo": "Focus",
                "anos": ["2012"],
                "restrições": "confirmar",
            },
        ]
    )

    assert result["records"] == []
    assert {item["reason"] for item in result["pending"]} == {
        "vehicle_identity_missing",
        "years_invalid_or_empty",
        "restrictions_invalid",
    }


def test_sku_214_reviewed_overrides_expand_all_known_years() -> None:
    result = normalize_sku_vehicle_years(
        sku="214",
        product_name="Comutador elétrico de ignição Hyundai e Kia",
        vehicle_items=[
            {
                "marca": "Hyundai",
                "modelo": "HB20 1.0 e 1.6 com chave mecânica",
                "anos": ["2012 em diante"],
                "restrições": ["conector de 6 pinos"],
            },
            {
                "marca": "Hyundai",
                "modelo": "HB20S com chave mecânica",
                "anos": ["2013 em diante"],
            },
            {
                "marca": "Hyundai",
                "modelo": "HB20X com chave mecânica",
                "anos": ["2013 em diante"],
            },
            {
                "marca": "Hyundai",
                "modelo": "i20",
                "anos": ["2008 em diante"],
            },
        ],
        source_ref="info/000002/SKU/214.json",
    )

    assert result["pending"] == []
    assert result["overrides_applied"] == 4
    by_model: dict[str, list[dict[str, object]]] = {}
    for record in result["records"]:
        by_model.setdefault(str(record["model"]), []).append(record)
    assert set(by_model) == {"HB20", "HB20S", "HB20X", "i20 I (PB/PBT)"}
    assert [item["year"] for item in by_model["HB20"]] == list(range(2012, 2026))
    assert [item["year"] for item in by_model["HB20S"]] == list(range(2013, 2026))
    assert [item["year"] for item in by_model["HB20X"]] == list(range(2013, 2023))
    assert [item["year"] for item in by_model["i20 I (PB/PBT)"]] == list(
        range(2008, 2016)
    )
    assert {item["status"] for item in by_model["HB20"]} == {"vigente"}
    assert {item["verified_through"] for item in by_model["HB20"]} == {2025}
    assert {item["status"] for item in by_model["HB20S"]} == {"vigente"}
    assert {item["verified_through"] for item in by_model["HB20S"]} == {2025}
    assert all(
        "somente versões com chave mecânica" in item["restrictions"]
        for item in by_model["HB20"] + by_model["HB20S"] + by_model["HB20X"]
    )
    assert all(
        "motores 1.0 e 1.6" in item["restrictions"]
        for item in by_model["HB20"]
    )
    assert {item["status"] for item in by_model["HB20X"]} == {"fechado"}
    assert {item["verified_through"] for item in by_model["HB20X"]} == {None}
    assert {item["override_id"] for item in result["records"]} == {
        "sku-214-fitment-reviewed-2026-07-22"
    }


def test_open_range_override_is_exact_and_other_skus_stay_pending() -> None:
    result = _normalize(
        [
            {
                "marca": "Hyundai",
                "modelo": "HB20 1.0 e 1.6 com chave mecânica",
                "anos": ["2012 em diante"],
            }
        ],
        sku="OUTRO",
    )

    assert result["records"] == []
    assert result["overrides_applied"] == 0
    assert result["pending"][0]["reason"] == "unsupported_or_ambiguous_year_expression"


def test_normalization_is_deterministic_under_input_reordering() -> None:
    items = [
        {"marca": "Kia", "modelo": "Soul", "anos": ["2013 a 2015"]},
        {"marca": "Hyundai", "modelo": "i30", "anos": ["2012", "2014"]},
        {"marca": "Kia", "modelo": "Soul", "anos": ["2015", "2017"]},
    ]
    shuffled = deepcopy(items)
    random.Random(42).shuffle(shuffled)

    assert _normalize(items) == _normalize(shuffled)


def test_ids_include_brand_and_hash_to_avoid_slug_collisions() -> None:
    assert vehicle_model_id("Ford", "X/Y") != vehicle_model_id("Ford", "X Y")
    assert vehicle_year_id("Ford", "X/Y", 2020) != vehicle_year_id("Ford", "X Y", 2020)
    assert vehicle_model_id("Ford", "Focus") != vehicle_model_id("Chevrolet", "Focus")
    assert ":ford:" in vehicle_model_id("Ford", "Focus")
    assert ":ford:focus:2020--" in vehicle_year_id("Ford", "Focus", 2020)
    assert "--" in vehicle_brand_id("São Paulo")
    assert vehicle_brand_id(" Marca  ") == vehicle_brand_id("marca")


def _records_for_many_models(model_count: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(model_count):
        normalized = _normalize(
            [
                {
                    "marca": "Marca Grande",
                    "modelo": f"Modelo {index:03d}",
                    "anos": ["2000 a 2025"],
                }
            ],
            sku=f"SKU-{index:03d}",
        )
        rows.extend(normalized["records"])
    return rows


def test_tree_pages_group_at_most_twelve_models_and_keep_degree_bounded() -> None:
    tree = build_vehicle_year_tree(_records_for_many_models(29))
    nodes_by_id = {node["id"]: node for node in tree["nodes"]}
    model_parts = [node for node in tree["nodes"] if node["kind"] == "model_part"]

    assert len(model_parts) == 3
    assert len(tree["page_specs"]) == 35
    assert not any(
        page["kind"] in {"year", "year_part"} for page in tree["page_specs"]
    )
    for part in model_parts:
        model_children = [
            child_id
            for child_id in part["child_ids"]
            if nodes_by_id[child_id]["kind"] == "model"
        ]
        assert len(model_children) <= MAX_ITEMS_PER_PART == 12
    assert tree["stats"]["max_degree"] <= 20
    assert all(
        len(node["child_ids"]) + (1 if node["parent_id"] else 0) <= 20
        for node in tree["nodes"]
    )


def test_tree_preserves_year_gaps_as_headings_inside_the_model_page() -> None:
    records = _normalize(
        [{"marca": "Ford", "modelo": "Focus", "anos": ["2012", "2014"]}]
    )["records"]
    tree = build_vehicle_year_tree(records)

    model_page = next(page for page in tree["page_specs"] if page["kind"] == "model")
    assert [group["year"] for group in model_page["year_groups"]] == [2012, 2014]
    assert all(group["render_individually"] is False for group in model_page["year_groups"])
    assert {record["year"] for record in model_page["records"]} == {2012, 2014}
    assert all(record["truth_class"] == "generated_secondary" for record in model_page["records"])
    assert not any(node["kind"] in {"year", "year_part"} for node in tree["nodes"])
    assert not any("/Anos/" in page["relative_path"] for page in tree["page_specs"])


def test_tree_is_deterministic_and_does_not_mutate_records() -> None:
    records = _records_for_many_models(3)
    original = deepcopy(records)
    shuffled = deepcopy(records)
    random.Random(7).shuffle(shuffled)

    assert build_vehicle_year_tree(records) == build_vehicle_year_tree(shuffled)
    assert records == original


def test_tree_fails_closed_on_ambiguous_duplicate_metadata() -> None:
    record = _normalize(
        [{"marca": "Ford", "modelo": "Focus", "anos": ["2012"]}]
    )["records"][0]
    conflicting = dict(record)
    conflicting["status"] = "vigente"

    with pytest.raises(ValueError, match="ambiguous duplicate"):
        build_vehicle_year_tree([record, conflicting])


@pytest.mark.parametrize("invalid_year", (2020.5, True, "2020.0"))
def test_tree_rejects_year_values_that_are_not_exact_integers(
    invalid_year: object,
) -> None:
    record = _normalize(
        [{"marca": "Ford", "modelo": "Focus", "anos": ["2020"]}]
    )["records"][0]
    malformed = dict(record)
    malformed["year"] = invalid_year

    with pytest.raises(ValueError, match="invalid year"):
        build_vehicle_year_tree([malformed])
