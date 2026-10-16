"""Compatibility for persisted Cadastro rows whose raw SKU was left blank."""

import csv
import io

import pytest
from fastapi import HTTPException

from backend.services import shared_sync  # noqa: F401 - configure runtime facade
from backend.services import cadastro_lojas_produtos
from backend.services import shared_sync_delta


REL = "cadastro_produtos_lojas.csv"
COLUMNS = [
    "store_id", "sku", "sku_normalizado", "nome", "row_version",
    "updated_at_utc", "deleted_at_utc", "m3 individual",
]


def _row(**overrides):
    return {
        "store_id": "store-A",
        "sku": "",
        "sku_normalizado": "001-02/A",
        "nome": "Produto, preservado",
        "row_version": "4",
        "updated_at_utc": "2026-10-16T10:00:00Z",
        "deleted_at_utc": "",
        "m3 individual": "0.001250",
        **overrides,
    }


def _csv_bytes(rows, columns=COLUMNS):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def _rows(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


@pytest.mark.parametrize(
    "normalized_sku, expected_sku",
    [("NA", "NA"), ("00042", "00042"), ("0", "000"),
     ("001-02/A", "001-02/A"), ("001.02", "001.02"), ("02-MAR", "2-3")],
)
def test_export_fills_missing_sku_like_crud_and_preserves_source(
    tmp_path, monkeypatch, normalized_sku, expected_sku,
):
    row = _row(sku_normalizado=normalized_sku)
    original = _csv_bytes([row])
    source = tmp_path / REL
    source.write_bytes(original)
    monkeypatch.setattr(
        shared_sync_delta, "_shared_sync_ler_arquivo_pacote",
        lambda path: source.read_bytes(),
    )

    output, keys = shared_sync_delta._shared_sync_delta_for_entry(
        "cadastro", {"relative_path": REL, "abs_path": str(source)}, set(),
    )

    assert _rows(output) == [{**row, "sku": expected_sku}]
    assert expected_sku == cadastro_lojas_produtos._normalizar_registro_persistido(row)["sku"]
    assert source.read_bytes() == original
    assert len(keys) == 1
    # A repeat export recognizes the materialized row instead of resending it.
    assert shared_sync_delta._shared_sync_csv_delta_bytes(
        "cadastro", REL, original, set(keys),
    ) == (None, [])
    assert shared_sync_delta._shared_sync_csv_delta_bytes(
        "cadastro", REL, output, set(keys),
    ) == (None, [])


@pytest.mark.parametrize("raw_sku", ["0001-02/A", "NA", "0", "  RAW-001  "])
def test_export_preserves_explicit_raw_sku_even_when_normalized_differs(raw_sku):
    row = _row(sku=raw_sku, sku_normalizado="OTHER-999")

    output, keys = shared_sync_delta._shared_sync_csv_delta_bytes(
        "cadastro", REL, _csv_bytes([row]), set(),
    )

    assert _rows(output) == [row]
    assert len(keys) == 1


def test_fallback_keeps_store_identity_and_deleted_rows():
    rows = [
        _row(store_id="store-A"),
        _row(store_id="store-a", nome="Outra loja"),
        _row(store_id="store-B", deleted_at_utc="2026-10-16T11:00:00Z"),
    ]
    output, keys = shared_sync_delta._shared_sync_csv_delta_bytes(
        "cadastro", REL, _csv_bytes(rows), set(),
    )

    assert _rows(output) == [{**row, "sku": row["sku_normalizado"]} for row in rows]
    assert len(set(keys)) == 3
    partial, new_keys = shared_sync_delta._shared_sync_csv_delta_bytes(
        "cadastro", REL, _csv_bytes(rows), {keys[0]},
    )
    assert [row["store_id"] for row in _rows(partial)] == ["store-a", "store-B"]
    assert new_keys == keys[1:]


@pytest.mark.parametrize(
    "overrides",
    [
        {"sku": "", "sku_normalizado": ""},
        {"sku": " ", "sku_normalizado": " "},
        {"store_id": "", "sku_normalizado": "001"},
        {"store_id": " ", "sku_normalizado": "001"},
    ],
)
def test_export_rejects_missing_identity_without_silently_dropping_rows(overrides):
    with pytest.raises(HTTPException) as error:
        shared_sync_delta._shared_sync_csv_delta_bytes(
            "cadastro", REL, _csv_bytes([_row(sku="VALID"), _row(**overrides)]), set(),
        )

    assert error.value.status_code == 502
    assert "sem store_id ou sku" in str(error.value.detail)


@pytest.mark.parametrize("missing_column", ["sku", "store_id", "row_version"])
def test_fallback_does_not_relax_required_csv_headers(missing_column):
    columns = [column for column in COLUMNS if column != missing_column]

    with pytest.raises(HTTPException) as error:
        shared_sync_delta._shared_sync_csv_delta_bytes(
            "cadastro", REL, _csv_bytes([_row()], columns), set(),
        )

    assert error.value.status_code == 502
    assert "ausentes" in str(error.value.detail)
    assert missing_column in str(error.value.detail)


def test_fallback_rejects_ambiguous_normalized_sku_column():
    columns = [*COLUMNS, "SKU normalizado"]
    row = {**_row(), "SKU normalizado": "OTHER-999"}

    with pytest.raises(HTTPException) as error:
        shared_sync_delta._shared_sync_csv_delta_bytes(
            "cadastro", REL, _csv_bytes([row], columns), set(),
        )

    assert error.value.status_code == 502
    assert "duplicado/ambiguo" in str(error.value.detail)


def test_capture_and_delta_use_same_logical_row_without_changing_original_bytes():
    original = _csv_bytes([_row()])
    captured = shared_sync_delta._shared_sync_csv_read_bytes(
        original, scope="cadastro", rel=REL,
    ).to_dict(orient="records")
    output, _ = shared_sync_delta._shared_sync_csv_delta_bytes(
        "cadastro", REL, original, set(),
    )
    emitted = shared_sync_delta._shared_sync_csv_read_bytes(
        output, scope="cadastro", rel=REL,
    ).to_dict(orient="records")

    assert captured == emitted == [{**_row(), "sku": "001-02/A"}]
    assert _rows(original) == [_row()]
