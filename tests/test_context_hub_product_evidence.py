from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.modules.context_hub import product_evidence, product_evidence_sync, storage
from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.locking import _exclusive_file_lock, _tenant_thread_lock
from backend.modules.context_hub.paths import _tenant_paths
from backend.services import context_hub


BASE_TIME = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def evidence_env(tmp_path: Path) -> Path:
    info_root = tmp_path / "info"
    info_root.mkdir()
    context_hub.configure_context_hub(
        base_dir=tmp_path,
        info_root=info_root,
        surface="test",
    )
    return info_root


def _hash(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _batch(
    info_root: Path,
    *,
    tenant: str = "tenant-a",
    variation_id: str = "var-1",
) -> str:
    result = product_evidence.create_product_evidence_batch(
        tenant,
        store_ref="store-a",
        seller_id="seller-a",
        site_id="MLB",
        sku="SKU-10",
        item_id="MLB100",
        variation_id=variation_id,
        started_at=BASE_TIME,
        info_root=info_root,
    )
    return result["batch_id"]


def _source(
    info_root: Path,
    batch_id: str,
    seed: str,
    *,
    tenant: str = "tenant-a",
    source_type: str = "official_manufacturer",
    domain: str | None = None,
    origin_key: str | None = None,
    copy_fingerprint: str | None = None,
    collected_at: datetime = BASE_TIME,
) -> str:
    domain = domain or f"{seed}.example"
    result = product_evidence.add_product_evidence_source(
        tenant,
        batch_id,
        url=f"https://{domain}/technical/{seed}",
        source_type=source_type,
        content_hash=_hash(seed),
        copy_fingerprint=copy_fingerprint,
        origin_key=origin_key or domain,
        section_ref="technical-data",
        collected_at=collected_at,
        info_root=info_root,
    )
    return result["source_id"]


def _claim(
    info_root: Path,
    batch_id: str,
    source_ids: list[str],
    *,
    tenant: str = "tenant-a",
    field_name: str = "electrical.voltage",
    scope: str = "product",
    value: object = 12,
    unit: str | None = "V",
    as_of: datetime = BASE_TIME,
) -> dict:
    return product_evidence.add_product_evidence_claim(
        tenant,
        batch_id,
        field_name=field_name,
        scope=scope,
        value=value,
        unit=unit,
        source_ids=source_ids,
        as_of=as_of,
        info_root=info_root,
    )


def _verified(
    info_root: Path,
    *,
    tenant: str = "tenant-a",
    variation_id: str = "var-1",
    as_of: datetime = BASE_TIME,
) -> list[dict]:
    return product_evidence.list_verified_product_evidence(
        tenant,
        store_ref="store-a",
        seller_id="seller-a",
        site_id="MLB",
        sku="SKU-10",
        item_id="MLB100",
        variation_id=variation_id,
        as_of=as_of,
        info_root=info_root,
    )


def _complete(
    info_root: Path,
    batch_id: str,
    *,
    tenant: str = "tenant-a",
    failed: bool = False,
    finished_at: datetime = BASE_TIME + timedelta(minutes=1),
) -> dict:
    return product_evidence.complete_product_evidence_batch(
        tenant,
        batch_id,
        coverage_complete=not failed,
        stop_reason="failed" if failed else "coverage_complete",
        failed=failed,
        finished_at=finished_at,
        info_root=info_root,
    )


def test_operation_lock_held_by_rebuild_does_not_block_operational_evidence_read(
    evidence_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        product_evidence_sync,
        "schedule_product_evidence_sync",
        lambda *_args, **_kwargs: {"success": True, "started": False},
    )
    batch_id = _batch(evidence_env)
    source_id = _source(evidence_env, batch_id, "nonblocking-maker")
    _claim(evidence_env, batch_id, [source_id], value="12 V", unit=None)
    _complete(evidence_env, batch_id)
    paths = _tenant_paths("tenant-a", info_root=evidence_env)
    holder_ready = threading.Event()
    release_holder = threading.Event()
    reader_done = threading.Event()
    errors: list[BaseException] = []
    result: list[list[dict]] = []

    def hold_rebuild_locks() -> None:
        try:
            with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
                holder_ready.set()
                release_holder.wait(3.0)
        except BaseException as error:  # pragma: no cover - surfaced below
            errors.append(error)
            holder_ready.set()

    def read_operational_evidence() -> None:
        try:
            result.append(_verified(evidence_env))
        except BaseException as error:  # pragma: no cover - surfaced below
            errors.append(error)
        finally:
            reader_done.set()

    holder = threading.Thread(target=hold_rebuild_locks, daemon=True)
    holder.start()
    assert holder_ready.wait(1.0)
    reader = threading.Thread(target=read_operational_evidence, daemon=True)
    reader.start()
    try:
        assert reader_done.wait(1.0), "operation.lock bloqueou a leitura operacional"
    finally:
        release_holder.set()
        holder.join(timeout=2.0)
        reader.join(timeout=2.0)

    assert errors == []
    assert len(result) == 1
    assert result[0][0]["value"] == "12"


def test_persists_structured_evidence_and_batch_metrics(evidence_env: Path) -> None:
    batch_id = _batch(evidence_env)
    source_id = _source(evidence_env, batch_id, "maker")

    claim = _claim(evidence_env, batch_id, [source_id], value="12 V", unit=None)
    result = product_evidence.complete_product_evidence_batch(
        "tenant-a",
        batch_id,
        coverage_complete=True,
        stop_reason="coverage_complete",
        pages_discovered=4,
        pages_read=4,
        fields_missing=1,
        finished_at=BASE_TIME + timedelta(minutes=1),
        info_root=evidence_env,
    )

    verified = _verified(evidence_env)
    assert claim["state"] == "candidate"
    assert len(verified) == 1
    assert verified[0]["activation_policy"] == "official_single_v1"
    assert result == {
        "batch_id": batch_id,
        "status": "completed",
        "coverage_complete": True,
        "fields_confirmed": 1,
        "conflicts_count": 0,
    }
    database = evidence_env / "tenant-a" / "context_hub" / "context_hub.db"
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'product_evidence_%'"
            )
        }
        assert tables == {
            "product_evidence_batches",
            "product_evidence_sources",
            "product_evidence_claims",
            "product_evidence_claim_sources",
        }
        columns = {
            row[1]
            for table in tables
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
    assert "copy_fingerprint" in columns
    assert not columns & {"vin", "question", "answer", "prompt", "raw_html", "snippet"}


def test_migrates_legacy_source_schema_without_rewriting_content_hash() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            """
            CREATE TABLE product_evidence_sources (
                source_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                content_hash TEXT NOT NULL
            )
            """
        )
        original_hash = _hash("legacy-real-content")
        connection.execute(
            "INSERT INTO product_evidence_sources VALUES ('source-1', 'batch-1', ?)",
            (original_hash,),
        )

        storage._migrate_product_evidence_schema(connection)

        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(product_evidence_sources)")
        }
        migrated = connection.execute(
            "SELECT content_hash, copy_fingerprint FROM product_evidence_sources"
        ).fetchone()
        assert "copy_fingerprint" in columns
        assert migrated == (original_hash, "")


def test_tenant_databases_are_isolated(evidence_env: Path) -> None:
    batch_id = _batch(evidence_env, tenant="tenant-a")
    source_id = _source(evidence_env, batch_id, "maker", tenant="tenant-a")
    _claim(evidence_env, batch_id, [source_id], tenant="tenant-a")
    _complete(evidence_env, batch_id, tenant="tenant-a")

    assert len(_verified(evidence_env, tenant="tenant-a")) == 1
    assert _verified(evidence_env, tenant="tenant-b") == []
    assert (evidence_env / "tenant-a" / "context_hub" / "context_hub.db").is_file()
    assert (evidence_env / "tenant-b" / "context_hub" / "context_hub.db").is_file()


def test_collecting_and_failed_batches_never_affect_public_activation(
    evidence_env: Path,
) -> None:
    completed_batch = _batch(evidence_env)
    completed_source = _source(evidence_env, completed_batch, "maker-completed")
    _claim(evidence_env, completed_batch, [completed_source], value="12 V", unit=None)
    _complete(evidence_env, completed_batch)

    partial_batch = _batch(evidence_env)
    partial_source = _source(evidence_env, partial_batch, "maker-partial")
    partial_claim = _claim(
        evidence_env,
        partial_batch,
        [partial_source],
        value="24 V",
        unit=None,
    )

    before_failure = _verified(evidence_env)
    assert partial_claim["state"] == "candidate"
    assert [(item["value"], item["unit"]) for item in before_failure] == [("12", "V")]

    failed = _complete(evidence_env, partial_batch, failed=True)
    after_failure = _verified(evidence_env)
    assert failed["status"] == "failed"
    assert [(item["value"], item["unit"]) for item in after_failure] == [("12", "V")]

    database = evidence_env / "tenant-a" / "context_hub" / "context_hub.db"
    with sqlite3.connect(database) as connection:
        state = connection.execute(
            "SELECT state FROM product_evidence_claims WHERE batch_id=?",
            (partial_batch,),
        ).fetchone()[0]
    assert state == "candidate"


def test_two_independent_technical_sources_activate_and_duplicates_do_not(
    evidence_env: Path,
) -> None:
    batch_id = _batch(evidence_env)
    source_a = _source(
        evidence_env,
        batch_id,
        "technical-a",
        source_type="technical_independent",
        domain="one.example",
        origin_key="publisher-one",
    )
    duplicate = product_evidence.add_product_evidence_source(
        "tenant-a",
        batch_id,
        url="https://mirror.example/copied",
        source_type="technical_independent",
        content_hash=_hash("technical-a"),
        origin_key="publisher-copy",
        collected_at=BASE_TIME,
        info_root=evidence_env,
    )
    first = _claim(evidence_env, batch_id, [source_a], value="800 W", unit=None)
    second_source = _source(
        evidence_env,
        batch_id,
        "technical-b",
        source_type="technical_distributor",
        domain="two.example",
        origin_key="publisher-two",
    )
    activated = _claim(
        evidence_env,
        batch_id,
        [source_a, second_source],
        value="800 W",
        unit=None,
    )

    assert duplicate["source_id"] == source_a
    assert duplicate["deduplicated"] is True
    assert first["state"] == "candidate"
    assert activated["state"] == "candidate"
    _complete(evidence_env, batch_id)
    verified = _verified(evidence_env)
    assert len(verified) == 1
    assert verified[0]["activation_policy"] == "two_independent_technical_v1"


def test_copy_fingerprint_deduplicates_distinct_real_content_hashes(
    evidence_env: Path,
) -> None:
    batch_id = _batch(evidence_env)
    copy_fingerprint = _hash("normalized-technical-copy")
    first = product_evidence.add_product_evidence_source(
        "tenant-a",
        batch_id,
        url="https://one.example/manual-a",
        source_type="technical_independent",
        content_hash=_hash("raw-page-a"),
        copy_fingerprint=copy_fingerprint,
        origin_key="publisher-one",
        collected_at=BASE_TIME,
        info_root=evidence_env,
    )
    duplicate = product_evidence.add_product_evidence_source(
        "tenant-a",
        batch_id,
        url="https://mirror.example/manual-b",
        source_type="technical_independent",
        content_hash=_hash("raw-page-b"),
        copy_fingerprint=copy_fingerprint,
        origin_key="publisher-copy",
        collected_at=BASE_TIME,
        info_root=evidence_env,
    )

    database = evidence_env / "tenant-a" / "context_hub" / "context_hub.db"
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT content_hash, copy_fingerprint FROM product_evidence_sources"
        ).fetchall()
    assert duplicate["source_id"] == first["source_id"]
    assert duplicate["deduplicated"] is True
    assert rows == [(_hash("raw-page-a"), copy_fingerprint)]


def test_copy_fingerprint_prevents_mirrors_from_becoming_independent_support(
    evidence_env: Path,
) -> None:
    shared_copy = _hash("shared-copy")
    first_batch = _batch(evidence_env)
    first_source = _source(
        evidence_env,
        first_batch,
        "raw-one",
        source_type="technical_independent",
        domain="one.example",
        origin_key="publisher-one",
        copy_fingerprint=shared_copy,
    )
    _claim(evidence_env, first_batch, [first_source], value="22 W", unit=None)
    _complete(evidence_env, first_batch)

    mirrored_batch = _batch(evidence_env)
    mirrored_source = _source(
        evidence_env,
        mirrored_batch,
        "raw-two",
        source_type="technical_distributor",
        domain="mirror.example",
        origin_key="publisher-copy",
        copy_fingerprint=shared_copy,
    )
    _claim(evidence_env, mirrored_batch, [mirrored_source], value="22 W", unit=None)
    _complete(evidence_env, mirrored_batch)
    assert _verified(evidence_env) == []

    independent_batch = _batch(evidence_env)
    independent_source = _source(
        evidence_env,
        independent_batch,
        "raw-three",
        source_type="technical_independent",
        domain="three.example",
        origin_key="publisher-three",
        copy_fingerprint=_hash("independent-copy"),
    )
    _claim(evidence_env, independent_batch, [independent_source], value="22 W", unit=None)
    _complete(evidence_env, independent_batch)

    verified = _verified(evidence_env)
    assert len(verified) == 1
    domains = {source["domain"] for source in verified[0]["sources"]}
    assert len(domains) == 2
    assert "three.example" in domains
    assert domains & {"one.example", "mirror.example"}


def test_independent_support_is_aggregated_across_research_batches(
    evidence_env: Path,
) -> None:
    first_batch = _batch(evidence_env)
    first_source = _source(
        evidence_env,
        first_batch,
        "research-one",
        source_type="technical_independent",
        domain="research-one.example",
        origin_key="publisher-one",
    )
    first = _claim(evidence_env, first_batch, [first_source], value="22 W", unit=None)
    _complete(evidence_env, first_batch)

    second_batch = _batch(evidence_env)
    second_source = _source(
        evidence_env,
        second_batch,
        "research-two",
        source_type="technical_distributor",
        domain="research-two.example",
        origin_key="publisher-two",
    )
    second = _claim(evidence_env, second_batch, [second_source], value="22 W", unit=None)
    assert _verified(evidence_env) == []
    _complete(evidence_env, second_batch)
    verified = _verified(evidence_env)

    assert first["state"] == "candidate"
    assert second["state"] == "candidate"
    assert len(verified) == 1
    assert verified[0]["activation_policy"] == "two_independent_technical_v1"
    assert {source["domain"] for source in verified[0]["sources"]} == {
        "research-one.example",
        "research-two.example",
    }


def test_subdomains_of_one_publisher_are_not_independent_sources(
    evidence_env: Path,
) -> None:
    batch_id = _batch(evidence_env)
    source_a = _source(
        evidence_env,
        batch_id,
        "mirror-a",
        source_type="technical_independent",
        domain="catalog.publisher.example",
        origin_key="catalog.publisher.example",
    )
    source_b = _source(
        evidence_env,
        batch_id,
        "mirror-b",
        source_type="technical_distributor",
        domain="shop.publisher.example",
        origin_key="shop.publisher.example",
    )

    claim = _claim(
        evidence_env,
        batch_id,
        [source_a, source_b],
        value="22 W",
        unit=None,
    )
    _complete(evidence_env, batch_id)

    assert claim["state"] == "candidate"
    assert _verified(evidence_env) == []


def test_conflicting_supported_values_make_field_unavailable(evidence_env: Path) -> None:
    batch_id = _batch(evidence_env)
    source_a = _source(evidence_env, batch_id, "maker-a", domain="maker-a.example")
    source_b = _source(evidence_env, batch_id, "maker-b", domain="maker-b.example")
    first = _claim(evidence_env, batch_id, [source_a], value="12 V", unit=None)
    second = _claim(evidence_env, batch_id, [source_b], value="24 V", unit=None)
    _complete(evidence_env, batch_id)

    assert first["state"] == "candidate"
    assert second["state"] == "candidate"
    assert _verified(evidence_env) == []
    database = evidence_env / "tenant-a" / "context_hub" / "context_hub.db"
    with sqlite3.connect(database) as connection:
        states = connection.execute(
            "SELECT state, conflict_group FROM product_evidence_claims ORDER BY normalized_value"
        ).fetchall()
    assert {state for state, _group in states} == {"conflict"}
    assert all(group for _state, group in states)


def test_compatibility_and_originality_require_manufacturer_or_oem(
    evidence_env: Path,
) -> None:
    batch_id = _batch(evidence_env)
    listing = _source(
        evidence_env,
        batch_id,
        "listing",
        source_type="official_listing",
        domain="mercadolivre.example",
    )
    technical_a = _source(
        evidence_env,
        batch_id,
        "tech-a",
        source_type="technical_independent",
        domain="tech-a.example",
    )
    technical_b = _source(
        evidence_env,
        batch_id,
        "tech-b",
        source_type="technical_distributor",
        domain="tech-b.example",
    )
    weak = _claim(
        evidence_env,
        batch_id,
        [listing, technical_a, technical_b],
        field_name="compatibility.vehicle",
        scope="application",
        value="Peugeot 307 1.6 2012",
        unit=None,
    )
    application_scope_only = _claim(
        evidence_env,
        batch_id,
        [technical_a, technical_b],
        field_name="vehicle.model",
        scope="application",
        value="Peugeot 307",
        unit=None,
    )
    maker = _source(evidence_env, batch_id, "oem", source_type="official_oem")
    strong = _claim(
        evidence_env,
        batch_id,
        [listing, technical_a, technical_b, maker],
        field_name="compatibility.vehicle",
        scope="application",
        value="Peugeot 307 1.6 2012",
        unit=None,
    )
    originality = _claim(
        evidence_env,
        batch_id,
        [listing],
        field_name="product.originality",
        value="genuine",
        unit=None,
    )
    _complete(evidence_env, batch_id)
    verified = _verified(evidence_env)

    assert weak["state"] == "candidate"
    assert application_scope_only["state"] == "candidate"
    assert strong["state"] == "candidate"
    assert originality["state"] == "candidate"
    assert [(item["field_name"], item["activation_policy"]) for item in verified] == [
        ("compatibility.vehicle", "strong_official_only_v1")
    ]


def test_ttl_is_24_hours_90_days_or_365_days_by_evidence_kind(
    evidence_env: Path,
) -> None:
    batch_id = _batch(evidence_env)
    listing = _source(
        evidence_env,
        batch_id,
        "listing",
        source_type="official_listing",
    )
    maker = _source(evidence_env, batch_id, "maker")
    listing_claim = _claim(
        evidence_env,
        batch_id,
        [listing],
        field_name="electrical.voltage",
        value="12 V",
        unit=None,
    )
    _claim(
        evidence_env,
        batch_id,
        [maker],
        field_name="physical.weight",
        value="1 kg",
        unit=None,
    )
    _claim(
        evidence_env,
        batch_id,
        [maker],
        field_name="compatibility.vehicle",
        scope="application",
        value="Peugeot 307 1.6",
        unit=None,
    )
    _complete(evidence_env, batch_id)

    assert listing_claim["state"] == "candidate"
    assert {item["field_name"] for item in _verified(evidence_env)} == {
        "compatibility.vehicle",
        "physical.weight",
    }
    assert {item["field_name"] for item in _verified(
        evidence_env, as_of=BASE_TIME + timedelta(hours=25)
    )} == {"compatibility.vehicle", "physical.weight"}
    database = evidence_env / "tenant-a" / "context_hub" / "context_hub.db"
    with sqlite3.connect(database) as connection:
        listing_state = connection.execute(
            "SELECT state FROM product_evidence_claims WHERE field_name='electrical.voltage'"
        ).fetchone()[0]
    assert listing_state == "expired"
    assert {item["field_name"] for item in _verified(
        evidence_env, as_of=BASE_TIME + timedelta(days=91)
    )} == {"physical.weight"}
    assert _verified(evidence_env, as_of=BASE_TIME + timedelta(days=366)) == []


def test_variation_evidence_does_not_cross_to_another_variation(evidence_env: Path) -> None:
    batch_id = _batch(evidence_env, variation_id="12v")
    source_id = _source(evidence_env, batch_id, "maker")
    _claim(evidence_env, batch_id, [source_id], scope="variation", value="12 V", unit=None)
    _complete(evidence_env, batch_id)

    assert len(_verified(evidence_env, variation_id="12v")) == 1
    assert _verified(evidence_env, variation_id="24v") == []
    assert _verified(evidence_env, variation_id="") == []


@pytest.mark.parametrize(
    ("value", "unit", "expected_value", "expected_unit"),
    [
        ("1,5 kg", None, "1500", "g"),
        ("500 mA", None, "0.5", "A"),
        (2, "kW", "2000", "W"),
        ("10 x 20 x 3 cm", None, "100x200x30", "mm"),
    ],
)
def test_normalizes_units(
    value: object,
    unit: str | None,
    expected_value: str,
    expected_unit: str,
) -> None:
    normalized = product_evidence.normalize_product_evidence_value(value, unit=unit)

    assert normalized.value == expected_value
    assert normalized.key == expected_value
    assert normalized.unit == expected_unit


def test_text_comparison_key_is_case_and_accent_insensitive() -> None:
    accented = product_evidence.normalize_product_evidence_value("Alumínio")
    plain = product_evidence.normalize_product_evidence_value("ALUMINIO")

    assert accented.value == "Alumínio"
    assert accented.key == plain.key == "aluminio"


@pytest.mark.parametrize(
    ("left", "right", "expected_value", "expected_unit"),
    [
        ("1200 L/h", "20 L/min", "1200", "L/h"),
        ("2 m3/h", "2000 LPH", "2000", "L/h"),
        ("3 bar", "300 kPa", "300", "kPa"),
        ("0.3 MPa", "300 kPa", "300", "kPa"),
    ],
)
def test_performance_units_normalize_to_shared_flow_or_pressure_basis(
    left: str,
    right: str,
    expected_value: str,
    expected_unit: str,
) -> None:
    normalized_left = product_evidence.normalize_product_evidence_value(left)
    normalized_right = product_evidence.normalize_product_evidence_value(right)

    assert (normalized_left.value, normalized_left.unit) == (expected_value, expected_unit)
    assert normalized_left.key == normalized_right.key
    assert normalized_left.unit == normalized_right.unit


def test_pump_flow_and_pressure_claims_persist_and_activate_together(
    evidence_env: Path,
) -> None:
    batch_id = _batch(evidence_env)
    source_id = _source(evidence_env, batch_id, "pump-maker")

    _claim(
        evidence_env,
        batch_id,
        [source_id],
        field_name="performance.flow_rate",
        value="20 L/min",
        unit=None,
    )
    _claim(
        evidence_env,
        batch_id,
        [source_id],
        field_name="performance.pressure",
        value="0.3 MPa",
        unit=None,
    )
    _complete(evidence_env, batch_id)

    verified = {
        item["field_name"]: (item["value"], item["unit"])
        for item in _verified(evidence_env)
    }
    assert verified == {
        "performance.flow_rate": ("1200", "L/h"),
        "performance.pressure": ("300", "kPa"),
    }


@pytest.mark.parametrize(
    "query_key",
    [
        "token",
        "ACCESS_TOKEN",
        "api_key",
        "Key",
        "auth",
        "Authorization",
        "secret",
        "signature",
        "SIG",
        "session",
        "X-Amz-Credential",
        "x-goog-signature",
    ],
)
def test_signed_or_credential_urls_are_rejected_before_persistence(
    evidence_env: Path,
    query_key: str,
) -> None:
    batch_id = _batch(evidence_env)
    with pytest.raises(ContextHubValidationError, match="parametro sensivel"):
        product_evidence.add_product_evidence_source(
            "tenant-a",
            batch_id,
            url=f"https://maker.example/manual?{query_key}=credential-value",
            source_type="official_manufacturer",
            content_hash=_hash(query_key),
            info_root=evidence_env,
        )

    database = evidence_env / "tenant-a" / "context_hub" / "context_hub.db"
    with sqlite3.connect(database) as connection:
        persisted = connection.execute(
            "SELECT COUNT(*) FROM product_evidence_sources"
        ).fetchone()[0]
    assert persisted == 0


def test_rejects_sensitive_or_raw_fields(evidence_env: Path) -> None:
    batch_id = _batch(evidence_env)
    with pytest.raises(ContextHubValidationError, match="proibido"):
        product_evidence.add_product_evidence_source(
            "tenant-a",
            batch_id,
            url="https://maker.example/search?vin=1M8GDM9AXKP042788",
            source_type="official_manufacturer",
            content_hash=_hash("sensitive"),
            info_root=evidence_env,
        )
    with pytest.raises(ContextHubValidationError, match="proibido"):
        product_evidence.add_product_evidence_source(
            "tenant-a",
            batch_id,
            url="https://maker.example/search?chassi=1M8G%20DM9AX%20KP04%202788",
            source_type="official_manufacturer",
            content_hash=_hash("sensitive-spaced"),
            info_root=evidence_env,
        )
    for sensitive_path in (
        "https://maker.example/8-A-D-2-M-K-F-W-X-C-G-0-3-5-6-1-5/manual",
        "https://maker.example/%38%41%44%32%4D%4B%46%57%58%43%47%30%33%35%36%31%35/manual",
    ):
        with pytest.raises(ContextHubValidationError, match="dado pessoal"):
            product_evidence.add_product_evidence_source(
                "tenant-a",
                batch_id,
                url=sensitive_path,
                source_type="official_manufacturer",
                content_hash=_hash(sensitive_path),
                info_root=evidence_env,
            )
    source_id = _source(evidence_env, batch_id, "maker")
    with pytest.raises(ContextHubValidationError, match="Campo proibido"):
        _claim(
            evidence_env,
            batch_id,
            [source_id],
            field_name="raw_html",
            value="conteudo",
            unit=None,
        )
    for forbidden_field in (
        "commercial.price",
        "inventory.stock",
        "promotion.current",
        "shipping.delivery_time",
    ):
        with pytest.raises(ContextHubValidationError, match="Campo proibido"):
            _claim(
                evidence_env,
                batch_id,
                [source_id],
                field_name=forbidden_field,
                value="nao persistir",
                unit=None,
            )
    with pytest.raises(ContextHubValidationError, match="dado pessoal"):
        _claim(
            evidence_env,
            batch_id,
            [source_id],
            field_name="installation.requirement.contact",
            value="WhatsApp: +55 11 99999-9999",
            unit=None,
        )
