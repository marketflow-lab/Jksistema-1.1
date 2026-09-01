from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.modules.context_hub import storage
from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.metadata import _parse_frontmatter
from backend.modules.context_hub.product_evidence_editorial import (
    PRODUCT_EVIDENCE_EDITORIAL_ROOT,
    collect_product_evidence_editorial_snapshot,
    render_product_evidence_editorial,
)


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    storage._create_schema(connection)
    return connection


def _batch(
    connection: sqlite3.Connection, seed: str, *, variation: str = "var-1", id_prefix: str = ""
) -> str:
    batch_id = f"batch-{id_prefix}{seed}"
    connection.execute(
        """
        INSERT INTO product_evidence_batches(
            batch_id, store_ref, seller_id, site_id, sku, item_id, variation_id,
            status, started_at, finished_at
        ) VALUES (?, 'store-a', 'seller-a', 'MLB', 'SKU-10', 'MLB100', ?,
                  'completed', ?, ?)
        """,
        (batch_id, variation, _iso(NOW - timedelta(minutes=5)), _iso(NOW)),
    )
    return batch_id


def _source(
    connection: sqlite3.Connection,
    batch_id: str,
    seed: str,
    *,
    source_type: str = "official_manufacturer",
    collected_at: datetime = NOW - timedelta(days=1),
    valid_until: datetime = NOW + timedelta(days=365),
    domain: str | None = None,
    copy_fingerprint: str = "",
    id_prefix: str = "",
) -> str:
    source_id = f"source-{id_prefix}{seed}"
    domain = domain or f"{seed}.example"
    authority = (
        "official" if source_type.startswith("official_")
        else "technical" if source_type.startswith("technical_") else "lead"
    )
    connection.execute(
        """
        INSERT INTO product_evidence_sources(
            source_id, batch_id, canonical_url, domain, source_type, authority,
            origin_key, content_hash, copy_fingerprint, section_ref,
            collected_at, valid_until
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ficha-tecnica', ?, ?)
        """,
        (
            source_id,
            batch_id,
            f"https://{domain}/technical/{seed}",
            domain,
            source_type,
            authority,
            domain,
            _sha(f"content-{seed}"),
            copy_fingerprint,
            _iso(collected_at),
            _iso(valid_until),
        ),
    )
    return source_id


def _claim(
    connection: sqlite3.Connection,
    batch_id: str,
    seed: str,
    source_ids: list[str],
    *,
    field_name: str,
    value: str,
    unit: str = "",
    scope: str = "product",
    persisted_state: str = "candidate",
    id_prefix: str = "",
) -> None:
    claim_id = f"claim-{id_prefix}{seed}"
    connection.execute(
        """
        INSERT INTO product_evidence_claims(
            claim_id, batch_id, field_name, scope, normalized_value,
            normalized_key, unit, state, valid_from, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            claim_id,
            batch_id,
            field_name,
            scope,
            value,
            value.casefold(),
            unit,
            persisted_state,
            _iso(NOW - timedelta(days=1)),
            _iso(NOW - timedelta(days=1)),
            _iso(NOW - timedelta(days=1)),
        ),
    )
    connection.executemany(
        "INSERT INTO product_evidence_claim_sources(claim_id, source_id) VALUES (?, ?)",
        ((claim_id, source_id) for source_id in source_ids),
    )


def _mixed_evidence_database(*, reverse: bool = False, id_prefix: str = "") -> sqlite3.Connection:
    connection = _database()
    batch_id = _batch(connection, "mixed", id_prefix=id_prefix)
    definitions = [
        ("power", "electrical.power", "800", "W", "official_manufacturer", NOW - timedelta(days=20), NOW + timedelta(days=300), "candidate"),
        ("voltage-12", "electrical.voltage", "12", "V", "official_oem", NOW - timedelta(days=10), NOW + timedelta(days=120), "candidate"),
        ("voltage-24", "electrical.voltage", "24", "V", "official_oem", NOW - timedelta(days=8), NOW + timedelta(days=140), "candidate"),
        ("weight-old", "physical.weight", "1200", "g", "official_manufacturer", NOW - timedelta(days=500), NOW - timedelta(days=2), "verified"),
        ("material-lead", "product.material", "Aluminio", "", "marketplace", NOW - timedelta(days=1), NOW + timedelta(days=1), "candidate"),
        ("brand-rejected", "product.brand", "Marca X", "", "official_manufacturer", NOW - timedelta(days=1), NOW + timedelta(days=300), "rejected"),
    ]
    for seed, field, value, unit, source_type, collected, valid, state in (
        reversed(definitions) if reverse else definitions
    ):
        source_id = _source(
            connection,
            batch_id,
            seed,
            source_type=source_type,
            collected_at=collected,
            valid_until=valid,
            id_prefix=id_prefix,
        )
        _claim(
            connection,
            batch_id,
            seed,
            [source_id],
            field_name=field,
            value=value,
            unit=unit,
            persisted_state=state,
            id_prefix=id_prefix,
        )
    connection.commit()
    return connection


def _render(connection: sqlite3.Connection):
    snapshot = collect_product_evidence_editorial_snapshot(
        connection, client_id="tenant-a", as_of=NOW
    )
    rendered = render_product_evidence_editorial(
        snapshot,
        client_id="tenant-a",
        surface="test",
        source_version="1.0.124",
        generated_at=NOW,
    )
    return snapshot, rendered


def test_projection_indexes_only_verified_and_keeps_conflict_expired_visible() -> None:
    connection = _mixed_evidence_database()
    before_changes = connection.total_changes

    snapshot, (documents, managed_files, findings) = _render(connection)

    facts = snapshot["identities"][0]["facts"]
    assert [(fact["field_name"], fact["value"], fact["state"]) for fact in facts] == [
        ("electrical.power", "800", "verified"),
        ("electrical.voltage", "12", "conflict"),
        ("electrical.voltage", "24", "conflict"),
        ("physical.weight", "1200", "expired"),
    ]
    assert snapshot["stats"] == {
        "identities": 1,
        "facts": 5,
        "candidate": 1,
        "verified": 1,
        "conflict": 2,
        "expired": 1,
    }
    assert findings == []
    assert len(documents) == 1
    assert "/Fatos-Verificados/" in documents[0]["relative_path"]
    assert documents[0]["metadata"]["ai_usage"] == "allowed"
    assert documents[0]["metadata"]["truth_class"] == "generated_verified"
    assert any("/Conflitos/" in path for path in managed_files)
    assert any("/Candidatas/" in path for path in managed_files)
    assert any("/Expiradas/" in path for path in managed_files)
    assert all("/Candidatas/" not in item["relative_path"] for item in documents)
    assert all("/Conflitos/" not in item["relative_path"] for item in documents)
    assert all("/Expiradas/" not in item["relative_path"] for item in documents)
    combined = "\n".join(managed_files.values())
    assert "Aluminio" in combined
    assert "Marca X" not in combined
    assert connection.total_changes == before_changes

    for path, content in managed_files.items():
        metadata, _body = _parse_frontmatter(content)
        if any(part in path for part in ("/Candidatas/", "/Conflitos/", "/Expiradas/")):
            assert metadata["ai_usage"] == "denied"
            assert metadata["truth_class"] == "generated_secondary"
        if "/Candidatas/" in path:
            assert metadata["status"] == "review_required"
            assert metadata["type"] == "product_evidence_editorial"


def test_snapshot_and_paths_ignore_row_ids_and_insertion_order() -> None:
    first = _mixed_evidence_database()
    second = _mixed_evidence_database(reverse=True, id_prefix="other-")

    first_snapshot, (first_docs, first_files, _) = _render(first)
    second_snapshot, (second_docs, second_files, _) = _render(second)

    assert first_snapshot["snapshot_hash"] == second_snapshot["snapshot_hash"]
    assert first_snapshot["next_transition_at"] == second_snapshot["next_transition_at"]
    assert list(first_files) == list(second_files)
    assert first_files == second_files
    assert [item["relative_path"] for item in first_docs] == [
        item["relative_path"] for item in second_docs
    ]
    assert all(path.startswith(PRODUCT_EVIDENCE_EDITORIAL_ROOT + "/") for path in first_files)


def test_two_independent_sources_set_valid_from_at_second_origin_and_copy_is_ignored() -> None:
    connection = _database()
    batch_id = _batch(connection, "technical")
    first_collected = NOW - timedelta(days=20)
    second_collected = NOW - timedelta(days=8)
    shared_copy = _sha("shared-copy")
    first = _source(
        connection,
        batch_id,
        "first",
        source_type="technical_independent",
        collected_at=first_collected,
        valid_until=NOW + timedelta(days=60),
        domain="one.example",
        copy_fingerprint=shared_copy,
    )
    mirror = _source(
        connection,
        batch_id,
        "mirror",
        source_type="technical_distributor",
        collected_at=first_collected + timedelta(days=1),
        valid_until=NOW + timedelta(days=70),
        domain="mirror.example",
        copy_fingerprint=shared_copy,
    )
    independent = _source(
        connection,
        batch_id,
        "independent",
        source_type="technical_distributor",
        collected_at=second_collected,
        valid_until=NOW + timedelta(days=80),
        domain="two.example",
        copy_fingerprint=_sha("independent-copy"),
    )
    _claim(
        connection,
        batch_id,
        "technical",
        [first, mirror, independent],
        field_name="performance.flow_rate",
        value="1200",
        unit="L/h",
    )
    connection.commit()

    snapshot = collect_product_evidence_editorial_snapshot(
        connection, client_id="tenant-a", as_of=NOW
    )
    fact = snapshot["identities"][0]["facts"][0]

    assert fact["state"] == "verified"
    assert fact["activation_policy"] == "two_independent_technical_v1"
    assert fact["valid_from"] == _iso(second_collected)
    assert len(fact["supporting_sources"]) == 2
    # The activation policy deterministically keeps one representative for the
    # copied content; the representative selected by the current policy is the
    # mirror with the later validity, never two independent votes.
    assert snapshot["next_transition_at"] == _iso(NOW + timedelta(days=70))


def test_application_validity_and_next_transition_use_compatibility_cap() -> None:
    connection = _database()
    batch_id = _batch(connection, "fitment")
    collected = NOW - timedelta(days=10)
    source_id = _source(
        connection,
        batch_id,
        "fitment-oem",
        source_type="official_oem",
        collected_at=collected,
        valid_until=NOW + timedelta(days=355),
    )
    _claim(
        connection,
        batch_id,
        "fitment",
        [source_id],
        field_name="compatibility.vehicle",
        value="Peugeot 307 1.6 2012",
        scope="application",
    )
    connection.commit()

    snapshot = collect_product_evidence_editorial_snapshot(
        connection, client_id="tenant-a", as_of=NOW
    )
    fact = snapshot["identities"][0]["facts"][0]
    expected = _iso(collected + timedelta(days=90))

    assert fact["valid_until"] == expected
    assert snapshot["next_transition_at"] == expected


def test_dlp_and_tenant_binding_fail_closed_without_persistence() -> None:
    connection = _database()
    batch_id = _batch(connection, "sensitive")
    source_id = _source(connection, batch_id, "sensitive")
    _claim(
        connection,
        batch_id,
        "sensitive",
        [source_id],
        field_name="installation.requirement",
        value="WhatsApp: +55 11 99999-9999",
    )
    connection.commit()
    before_changes = connection.total_changes

    with pytest.raises(ContextHubValidationError, match="dado pessoal"):
        collect_product_evidence_editorial_snapshot(
            connection, client_id="tenant-a", as_of=NOW
        )
    assert connection.total_changes == before_changes

    safe_connection = _mixed_evidence_database()
    snapshot = collect_product_evidence_editorial_snapshot(
        safe_connection, client_id="tenant-a", as_of=NOW
    )
    with pytest.raises(ContextHubValidationError, match="outro tenant"):
        render_product_evidence_editorial(
            snapshot,
            client_id="tenant-b",
            surface="test",
            source_version="1.0.124",
            generated_at=NOW,
        )
    snapshot["identities"][0]["sku"] = "SKU-ALTERADO"
    with pytest.raises(ContextHubValidationError, match="adulterado"):
        render_product_evidence_editorial(
            snapshot,
            client_id="tenant-a",
            surface="test",
            source_version="1.0.124",
            generated_at=NOW,
        )


def test_candidate_free_legacy_snapshot_without_projection_hash_is_supported() -> None:
    snapshot = collect_product_evidence_editorial_snapshot(
        _mixed_evidence_database(), client_id="tenant-a", as_of=NOW
    )
    legacy = dict(snapshot)
    legacy.pop("projection_hash")
    legacy.pop("projection_next_transition_at")
    legacy.pop("editorial_identities")

    documents, managed_files, findings = render_product_evidence_editorial(
        legacy,
        client_id="tenant-a",
        surface="test",
        source_version="1.0.126",
        generated_at=NOW,
    )

    assert documents
    assert managed_files
    assert findings == []


def test_candidate_only_snapshot_creates_review_notes_without_ai_indexing() -> None:
    connection = _database()
    batch_id = _batch(connection, "candidate")
    lead = _source(
        connection,
        batch_id,
        "lead",
        source_type="marketplace",
        valid_until=NOW + timedelta(days=1),
    )
    _claim(
        connection,
        batch_id,
        "lead",
        [lead],
        field_name="product.material",
        value="Aço",
    )
    connection.commit()

    snapshot, (documents, managed_files, findings) = _render(connection)
    empty = collect_product_evidence_editorial_snapshot(
        _database(), client_id="tenant-a", as_of=NOW
    )

    assert snapshot["identities"] == []
    assert len(snapshot["editorial_identities"]) == 1
    assert snapshot["stats"] == {
        "identities": 1,
        "facts": 1,
        "candidate": 1,
        "verified": 0,
        "conflict": 0,
        "expired": 0,
    }
    assert snapshot["snapshot_hash"] == empty["snapshot_hash"]
    assert snapshot["projection_hash"] != empty["projection_hash"]
    assert documents == []
    assert findings == []
    candidate_paths = [path for path in managed_files if "/Candidatas/" in path]
    assert len(candidate_paths) == 1
    metadata, body = _parse_frontmatter(managed_files[candidate_paths[0]])
    assert metadata["ai_usage"] == "denied"
    assert metadata["truth_class"] == "generated_secondary"
    assert metadata["status"] == "review_required"
    assert metadata["evidence_state"] == "candidate"
    assert metadata["type"] == "product_evidence_editorial"
    assert "Aço" in body
    assert "Fontes candidatas registradas" in body
    assert all(
        forbidden not in managed_files[candidate_paths[0]].casefold()
        for forbidden in ("pergunta do comprador", "resposta da ia", "vin:", "chassi:")
    )
    missing_projection_hash = dict(snapshot)
    missing_projection_hash.pop("projection_hash")
    with pytest.raises(ContextHubValidationError, match="adulterado"):
        render_product_evidence_editorial(
            missing_projection_hash,
            client_id="tenant-a",
            surface="test",
            source_version="1.0.126",
            generated_at=NOW,
        )
