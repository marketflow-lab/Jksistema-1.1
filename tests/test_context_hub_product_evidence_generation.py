from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.modules.context_hub import generations as hub_generations
from backend.modules.context_hub import documents as hub_documents
from backend.modules.context_hub import product_evidence
from backend.modules.context_hub import product_evidence_sync
from backend.modules.context_hub import storage
from backend.modules.context_hub.contracts import ContextHubConflictError
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.perguntas_pos_venda.ai import context as agent_context
from backend.services import context_hub


CLIENT_ID = "tenant-editorial-generation"
EVIDENCE_IDENTITY = {
    "store_ref": "Loja JK Peças",
    "seller_id": "seller-a",
    "site_id": "MLB",
    "sku": "SKU-290",
    "item_id": "MLB100",
    "variation_id": "var-1",
}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture()
def generation_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "app"
    info = tmp_path / "info"
    base.mkdir()
    info.mkdir()
    context_hub.configure_context_hub(base_dir=base, info_root=info, surface="test")
    inventory = {
        "source_version": "1.0.124",
        "entities": [
            {
                "id": "jk:domain:test",
                "kind": "domain",
                "domain": "sistema",
                "title": "Contexto seguro",
                "content": "Conhecimento operacional seguro.",
                "source_refs": ["backend/modules/context_hub"],
                "source_hash": _sha("domain-test"),
            }
        ],
        "findings": [],
        "stats": {"entities": 1},
    }
    monkeypatch.setattr(hub_generations, "_build_inventory", lambda *_args: (inventory, []))
    monkeypatch.setattr(hub_generations, "_collect_curated_notes", lambda *_args: ([], [], []))
    monkeypatch.setattr(hub_generations, "_load_context_bundle", lambda *_args, **_kwargs: ([], [], []))
    monkeypatch.setattr(
        hub_documents.curation_records,
        "_render_inventory",
        lambda _inventory: {
            "70_Gerado/Dominios/Contexto-seguro.md": (
                "---\nid: jk:domain:test\ntype: domain\n---\n\n"
                "# Contexto seguro\n\nConhecimento operacional seguro.\n"
            )
        },
    )
    monkeypatch.setattr(
        product_evidence_sync,
        "schedule_product_evidence_sync",
        lambda *_args, **_kwargs: {"success": True, "started": False},
    )
    yield info
    product_evidence_sync.stop_all_product_evidence_sync_workers()
    context_hub.stop_all_context_hub_watchers()


def _seed_verified_fact(
    info_root: Path,
    *,
    field_name: str,
    value: str,
    seed: str,
    identity: dict[str, str] | None = None,
) -> None:
    selected = identity or EVIDENCE_IDENTITY
    collected_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    batch = product_evidence.create_product_evidence_batch(
        CLIENT_ID,
        store_ref=selected["store_ref"],
        seller_id=selected["seller_id"],
        site_id=selected["site_id"],
        sku=selected["sku"],
        item_id=selected["item_id"],
        variation_id=selected["variation_id"],
        started_at=collected_at,
        info_root=info_root,
    )
    source = product_evidence.add_product_evidence_source(
        CLIENT_ID,
        batch["batch_id"],
        url=f"https://fabricante.example/ficha/{seed}",
        source_type="official_manufacturer",
        content_hash=_sha(seed),
        origin_key="fabricante.example",
        section_ref="dados tecnicos",
        collected_at=collected_at,
        info_root=info_root,
    )
    product_evidence.add_product_evidence_claim(
        CLIENT_ID,
        batch["batch_id"],
        field_name=field_name,
        scope="product",
        value=value,
        source_ids=[source["source_id"]],
        as_of=collected_at,
        info_root=info_root,
    )
    product_evidence.complete_product_evidence_batch(
        CLIENT_ID,
        batch["batch_id"],
        coverage_complete=True,
        stop_reason="coverage_complete",
        finished_at=collected_at + timedelta(seconds=1),
        info_root=info_root,
    )


def test_verified_research_is_projected_and_searchable_after_manual_publish(
    generation_env: Path,
) -> None:
    _seed_verified_fact(
        generation_env,
        field_name="electrical.voltage",
        value="12 V",
        seed="voltage-12v",
    )

    ready = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env)
    assert ready["success"] is True
    assert ready["status"] == "ready"
    paths = _tenant_paths(CLIENT_ID, info_root=generation_env)
    snapshot_root = paths.generations_dir / ready["generation_id"] / "70_Gerado"
    verified_notes = list(snapshot_root.rglob("Fatos-Verificados/*.md"))
    assert len(verified_notes) == 1
    assert "12 V" in verified_notes[0].read_text(encoding="utf-8")

    with storage._connect(paths) as connection:
        evidence_docs = connection.execute(
            "SELECT kind, valid_from, valid_to FROM context_hub_documents "
            "WHERE generation_id=? AND kind='product_evidence_fact'",
            (ready["generation_id"],),
        ).fetchall()
        attestation = connection.execute(
            "SELECT snapshot_hash, policy_version, next_transition_at FROM "
            "context_hub_generation_product_evidence WHERE generation_id=?",
            (ready["generation_id"],),
        ).fetchone()
    assert len(evidence_docs) == 1
    assert evidence_docs[0]["valid_from"] < evidence_docs[0]["valid_to"]
    assert len(attestation["snapshot_hash"]) == 64
    assert attestation["policy_version"] == "jk_product_evidence_v1"

    published = context_hub.publish_generation(
        CLIENT_ID,
        ready["generation_id"],
        info_root=generation_env,
    )
    with storage._connect(paths) as connection:
        outbox = connection.execute(
            "SELECT completed_generation_id, not_before FROM "
            "context_hub_product_evidence_outbox WHERE singleton_id=1"
        ).fetchone()
    result = context_hub.search_context(
        CLIENT_ID,
        "tensão 12 V",
        info_root=generation_env,
        _product_evidence_identity=EVIDENCE_IDENTITY,
    )
    assert published["status"] == "active"
    assert outbox["completed_generation_id"] == ready["generation_id"]
    assert outbox["not_before"] == attestation["next_transition_at"]
    assert any(
        str(item["doc_id"]).startswith("jk:product-evidence:")
        for item in result["results"]
    )


def test_ready_generation_cannot_publish_after_evidence_changes(
    generation_env: Path,
) -> None:
    _seed_verified_fact(
        generation_env,
        field_name="electrical.voltage",
        value="12 V",
        seed="voltage-before",
    )
    ready = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env)
    _seed_verified_fact(
        generation_env,
        field_name="electrical.power",
        value="60 W",
        seed="power-after",
    )

    with pytest.raises(ContextHubConflictError):
        context_hub.publish_generation(
            CLIENT_ID,
            ready["generation_id"],
            info_root=generation_env,
        )
    paths = _tenant_paths(CLIENT_ID, info_root=generation_env)
    with storage._connect(paths) as connection:
        active = connection.execute(
            "SELECT generation_id FROM context_hub_active_generation WHERE singleton_id=1"
        ).fetchone()[0]
    assert active is None


def test_product_facts_require_exact_store_seller_site_item_and_variation(
    generation_env: Path,
) -> None:
    identities = [
        EVIDENCE_IDENTITY,
        EVIDENCE_IDENTITY | {"variation_id": "var-2"},
        EVIDENCE_IDENTITY | {"seller_id": "seller-b"},
        EVIDENCE_IDENTITY | {"site_id": "MCO"},
        EVIDENCE_IDENTITY | {"sku": EVIDENCE_IDENTITY["sku"].lower()},
    ]
    expected_values = ("12 V", "24 V", "36 V", "48 V", "60 V")
    for index, (identity, value) in enumerate(zip(identities, expected_values), start=1):
        _seed_verified_fact(
            generation_env,
            field_name="electrical.voltage",
            value=value,
            seed=f"isolated-{index}",
            identity=identity,
        )

    ready = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env)
    context_hub.publish_generation(CLIENT_ID, ready["generation_id"], info_root=generation_env)
    paths = _tenant_paths(CLIENT_ID, info_root=generation_env)
    with storage._connect(paths) as connection:
        stored_identities = {
            tuple(row)
            for row in connection.execute(
                "SELECT store_ref, seller_id, site_id, sku, item_id, variation_id "
                "FROM context_hub_documents WHERE generation_id=? "
                "AND kind='product_evidence_fact'",
                (ready["generation_id"],),
            ).fetchall()
        }
    assert stored_identities == {
        tuple(identity[key] for key in (
            "store_ref", "seller_id", "site_id", "sku", "item_id", "variation_id"
        ))
        for identity in identities
    }
    for identity, expected in zip(identities, expected_values):
        result = context_hub.search_context(
            CLIENT_ID,
            "tensão",
            info_root=generation_env,
            _product_evidence_identity=identity,
        )
        evidence = [item for item in result["results"] if item["type"] == "product_evidence_fact"]
        assert len({item["doc_id"] for item in evidence}) == 1
        combined = " ".join(item["snippet"] for item in evidence)
        assert expected in combined
        assert all(other not in combined for other in expected_values if other != expected)

    unscoped = context_hub.search_context(CLIENT_ID, "tensão", info_root=generation_env)
    wrong_item = context_hub.search_context(
        CLIENT_ID,
        "tensão",
        info_root=generation_env,
        _product_evidence_identity=EVIDENCE_IDENTITY | {"item_id": "MLB999"},
    )
    assert not any(item["type"] == "product_evidence_fact" for item in unscoped["results"])
    assert not any(item["type"] == "product_evidence_fact" for item in wrong_item["results"])


def test_public_question_context_hub_path_recovers_only_exact_product_evidence(
    generation_env: Path,
) -> None:
    _seed_verified_fact(
        generation_env,
        field_name="electrical.voltage",
        value="12 V",
        seed="ppv-context-exact",
    )
    ready = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env)
    context_hub.publish_generation(CLIENT_ID, ready["generation_id"], info_root=generation_env)
    agent_input = {
        "store": EVIDENCE_IDENTITY["store_ref"],
        "question": {"text": "Qual a tensao deste produto?"},
        "item": {
            "id": EVIDENCE_IDENTITY["item_id"],
            "seller_sku": EVIDENCE_IDENTITY["sku"],
            "title": "Modulo eletrico SKU-290",
        },
        "context": {"sku": EVIDENCE_IDENTITY["sku"]},
        "intent": {
            "categoria": "product_feature",
            "categorias": ["product_feature"],
            "fluxo": "perguntas_anuncio",
            "flags": {},
            "subperguntas": [],
            "compatibilidade": {"aplicavel": False},
        },
        "product_evidence_identity": dict(EVIDENCE_IDENTITY),
    }

    exact = agent_context._perguntas_ia_context_hub_tool(CLIENT_ID, agent_input)
    mismatched_input = {
        **agent_input,
        "product_evidence_identity": EVIDENCE_IDENTITY | {"item_id": "MLB999"},
    }
    mismatch = agent_context._perguntas_ia_context_hub_tool(CLIENT_ID, mismatched_input)

    exact_evidence = [
        row for row in exact["result"]["results"]
        if row.get("type") == "product_evidence_fact"
    ]
    assert exact_evidence
    assert len({row["doc_id"] for row in exact_evidence}) == 1
    assert "12 V" in " ".join(row["snippet"] for row in exact_evidence)
    assert all("compatibility_coverage" not in row for row in exact_evidence)
    assert exact["result"]["product_evidence_count"] == len(exact_evidence)
    assert mismatch["result"]["product_evidence_count"] == 0
    assert not any(
        row.get("type") == "product_evidence_fact"
        for row in mismatch["result"]["results"]
    )


def test_legacy_generation_without_product_evidence_is_backfilled_for_rollback(
    generation_env: Path,
) -> None:
    first = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env, force=True)
    context_hub.publish_generation(CLIENT_ID, first["generation_id"], info_root=generation_env)
    second = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env, force=True)
    context_hub.publish_generation(CLIENT_ID, second["generation_id"], info_root=generation_env)
    paths = _tenant_paths(CLIENT_ID, info_root=generation_env)
    with storage._connect(paths) as connection:
        connection.execute(
            "DELETE FROM context_hub_generation_materialization WHERE generation_id=?",
            (first["generation_id"],),
        )
        connection.execute(
            "DELETE FROM context_hub_generation_product_evidence WHERE generation_id=?",
            (first["generation_id"],),
        )
        connection.commit()

    rolled_back = context_hub.rollback_generation(
        CLIENT_ID,
        first["generation_id"],
        info_root=generation_env,
    )

    assert rolled_back["success"] is True
    assert rolled_back["rollback"] is True
    assert rolled_back["status"] == "active"


def test_legacy_generation_backfill_rejects_tampered_snapshot(
    generation_env: Path,
) -> None:
    first = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env, force=True)
    context_hub.publish_generation(CLIENT_ID, first["generation_id"], info_root=generation_env)
    second = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env, force=True)
    context_hub.publish_generation(CLIENT_ID, second["generation_id"], info_root=generation_env)
    paths = _tenant_paths(CLIENT_ID, info_root=generation_env)
    with storage._connect(paths) as connection:
        connection.execute(
            "DELETE FROM context_hub_generation_materialization WHERE generation_id=?",
            (first["generation_id"],),
        )
        connection.execute(
            "DELETE FROM context_hub_generation_product_evidence WHERE generation_id=?",
            (first["generation_id"],),
        )
        connection.commit()
    target = next(
        (paths.generations_dir / first["generation_id"] / "70_Gerado").rglob("*.md")
    )
    target.write_text("CONTEUDO ADULTERADO ACEITO.\n", encoding="utf-8")

    with pytest.raises(ContextHubConflictError):
        context_hub.rollback_generation(
            CLIENT_ID,
            first["generation_id"],
            info_root=generation_env,
        )

    assert "CONTEUDO ADULTERADO ACEITO" not in "\n".join(
        path.read_text(encoding="utf-8")
        for path in paths.generated_dir.rglob("*.md")
    )


def test_materialized_generation_bytes_are_attested_before_publish(
    generation_env: Path,
) -> None:
    _seed_verified_fact(
        generation_env,
        field_name="electrical.voltage",
        value="12 V",
        seed="materialization-original",
    )
    ready = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env)
    paths = _tenant_paths(CLIENT_ID, info_root=generation_env)
    target = next(
        (paths.generations_dir / ready["generation_id"] / "70_Gerado").rglob(
            "Fatos-Verificados/*.md"
        )
    )
    original = target.read_bytes()
    target.write_bytes(original.replace(b"12 V", b"24 V"))

    with pytest.raises(ContextHubConflictError, match="materializado"):
        context_hub.publish_generation(
            CLIENT_ID, ready["generation_id"], info_root=generation_env
        )
    with storage._connect(paths) as connection:
        assert connection.execute(
            "SELECT generation_id FROM context_hub_active_generation WHERE singleton_id=1"
        ).fetchone()[0] is None


def test_unpublished_ready_evidence_is_not_current_for_empty_active_generation(
    generation_env: Path,
) -> None:
    empty = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env)
    context_hub.publish_generation(CLIENT_ID, empty["generation_id"], info_root=generation_env)
    _seed_verified_fact(
        generation_env,
        field_name="electrical.voltage",
        value="12 V",
        seed="ready-not-published",
    )
    ready = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env)
    paths = _tenant_paths(CLIENT_ID, info_root=generation_env)
    assert product_evidence_sync._ack_attested_generation(
        paths,
        expected_revision=1,
        generation_id=ready["generation_id"],
    ) == "acked"

    status = context_hub.get_status(CLIENT_ID, info_root=generation_env)["product_evidence_sync"]
    assert status["pending"] is False
    assert status["active_verified_facts"] == 0
    assert status["active_evidence_current"] is False


def test_new_evidence_revision_suppresses_old_active_facts_until_publish(
    generation_env: Path,
) -> None:
    _seed_verified_fact(
        generation_env,
        field_name="electrical.voltage",
        value="12 V",
        seed="voltage-active",
    )
    first = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env)
    context_hub.publish_generation(CLIENT_ID, first["generation_id"], info_root=generation_env)
    assert context_hub.search_context(
        CLIENT_ID,
        "tensão 12 V",
        info_root=generation_env,
        _product_evidence_identity=EVIDENCE_IDENTITY,
    )["count"] >= 1

    _seed_verified_fact(
        generation_env,
        field_name="electrical.power",
        value="60 W",
        seed="power-pending",
    )
    stale = context_hub.search_context(
        CLIENT_ID,
        "tensão 12 V",
        info_root=generation_env,
        _product_evidence_identity=EVIDENCE_IDENTITY,
    )
    ordinary = context_hub.search_context(
        CLIENT_ID, "Conhecimento operacional seguro", info_root=generation_env
    )
    status = context_hub.get_status(CLIENT_ID, info_root=generation_env)

    assert not any(
        item["type"] == "product_evidence_fact" for item in stale["results"]
    )
    assert any(item["doc_id"] == "jk:domain:test" for item in ordinary["results"])
    assert status["product_evidence_sync"]["pending"] is True
    assert status["product_evidence_sync"]["active_evidence_current"] is False
    assert status["product_evidence_sync"]["active_verified_facts"] == 1


def test_rollback_cannot_restore_obsolete_product_evidence(
    generation_env: Path,
) -> None:
    _seed_verified_fact(
        generation_env,
        field_name="electrical.voltage",
        value="12 V",
        seed="voltage-first",
    )
    first = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env)
    context_hub.publish_generation(CLIENT_ID, first["generation_id"], info_root=generation_env)
    _seed_verified_fact(
        generation_env,
        field_name="electrical.power",
        value="60 W",
        seed="power-second",
    )
    second = context_hub.rebuild_context(CLIENT_ID, info_root=generation_env)
    context_hub.publish_generation(CLIENT_ID, second["generation_id"], info_root=generation_env)

    with pytest.raises(ContextHubConflictError):
        context_hub.rollback_generation(
            CLIENT_ID,
            first["generation_id"],
            info_root=generation_env,
        )
    paths = _tenant_paths(CLIENT_ID, info_root=generation_env)
    with storage._connect(paths) as connection:
        active = connection.execute(
            "SELECT generation_id FROM context_hub_active_generation WHERE singleton_id=1"
        ).fetchone()[0]
    assert active == second["generation_id"]
