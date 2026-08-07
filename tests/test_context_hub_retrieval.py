from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from backend.modules.context_hub import paths as hub_paths
from backend.modules.context_hub import storage as hub_storage
from backend.services import context_hub


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture()
def retrieval_hub(tmp_path: Path):
    base = tmp_path / "app"
    info = tmp_path / "info"
    base.mkdir()
    info.mkdir()
    context_hub.configure_context_hub(base_dir=base, info_root=info, surface="installed")
    context_hub.bootstrap_context_hub("tenant-rag")
    paths = hub_paths._tenant_paths("tenant-rag", info_root=info)
    generation_id = "a" * 32
    source_version = "1.0.105"

    documents: list[tuple[str, str, list[str]]] = [
        (
            "jk:sku:abc-123",
            "Produto ABC-123",
            ["Compatibilidade confirmada para o suporte ABC-123."],
        ),
        (
            "jk:manual:navigator",
            "Manual Navigator",
            ["Manual tecnico do navegador com suporte confiavel."],
        ),
    ]
    for index in range(10):
        chunks = [f"Manual tecnico produto numero {index} com orientacao unica."]
        if index == 0:
            chunks = [
                "Manual tecnico produto destaque especial parte um.",
                "Manual tecnico produto destaque especial parte dois.",
                "Manual tecnico produto destaque especial parte tres.",
                "Manual tecnico produto destaque especial parte quatro.",
                "Manual tecnico produto destaque especial parte quatro.",
            ]
        documents.append((f"jk:guide:{index}", f"Guia produto {index}", chunks))

    with hub_storage._connect(paths) as connection:
        connection.execute(
            """
            INSERT INTO context_hub_generations(
                generation_id, status, source_hash, source_version, surface,
                reason, findings_json, stats_json, created_at, published_at
            ) VALUES (?, 'active', ?, ?, 'installed', 'test', '[]', '{}', ?, ?)
            """,
            (generation_id, _sha("generation"), source_version, "2026-07-20T00:00:00+00:00", "2026-07-20T00:00:00+00:00"),
        )
        connection.execute(
            "UPDATE context_hub_active_generation SET generation_id=?, version=1 WHERE singleton_id=1",
            (generation_id,),
        )
        try:
            connection.execute("SELECT COUNT(*) FROM context_hub_chunks_fts").fetchone()
        except sqlite3.OperationalError:
            pytest.skip("SQLite de teste sem FTS5")

        for doc_id, title, chunks in documents:
            document_content = "\n\n".join(chunks)
            source_hash = _sha(doc_id + ":source")
            content_hash = _sha(document_content)
            reference = f"info/tenant-rag/ContextVault/{doc_id.replace(':', '-')}.md"
            connection.execute(
                """
                INSERT INTO context_hub_documents(
                    generation_id, doc_id, entity_id, relative_path, title, kind,
                    module, surface, truth_class, sensitivity, source_version,
                    source_hash, content_hash, source_refs_json, content, managed
                ) VALUES (?, ?, ?, ?, ?, 'technical_knowledge', 'black_jhon',
                          'installed', 'versioned_technical', 'internal', ?, ?, ?, ?, ?, 1)
                """,
                (
                    generation_id,
                    doc_id,
                    doc_id,
                    reference,
                    title,
                    source_version,
                    source_hash,
                    content_hash,
                    json.dumps([reference]),
                    document_content,
                ),
            )
            for ordinal, content in enumerate(chunks):
                chunk_id = _sha(f"{doc_id}:{ordinal}:{content}")[:32]
                connection.execute(
                    """
                    INSERT INTO context_hub_chunks(
                        generation_id, chunk_id, doc_id, ordinal, content, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (generation_id, chunk_id, doc_id, ordinal, content, _sha(content)),
                )
                connection.execute(
                    """
                    INSERT INTO context_hub_chunks_fts(
                        generation_id, chunk_id, doc_id, title, content,
                        module, kind, surface, truth_class
                    ) VALUES (?, ?, ?, ?, ?, 'black_jhon', 'technical_knowledge',
                              'installed', 'versioned_technical')
                    """,
                    (generation_id, chunk_id, doc_id, title, content),
                )
        connection.commit()

    yield info
    context_hub.stop_all_context_hub_watchers()


def test_exact_identifier_precedes_strict_search_and_prevents_unneeded_relaxation(retrieval_hub: Path) -> None:
    result = context_hub.search_context(
        "tenant-rag",
        "SKU ABC-123 termo totalmente ausente",
        info_root=retrieval_hub,
    )

    assert result["search_stages"] == ["exact_identifier", "bm25_strict"]
    assert result["relaxation_used"] is False
    hit = result["results"][0]
    assert hit["doc_id"] == "jk:sku:abc-123"
    assert hit["selection_strategy"] == "exact_identifier"
    assert hit["selection_reason"].startswith("identifier_exact_match:")
    assert hit["reference"].endswith("jk-sku-abc-123.md")
    assert hit["version"] == "1.0.105"
    assert hit["hash"] == hit["source_hash"]
    assert len(hit["hash"]) == 64
    assert hit["snippet"]


def test_zero_hit_strict_search_relaxes_only_to_significant_terms(retrieval_hub: Path) -> None:
    result = context_hub.search_context(
        "tenant-rag",
        "SKU ZZ-999 manual inexistente",
        info_root=retrieval_hub,
    )

    assert result["search_stages"] == ["exact_identifier", "bm25_strict", "bm25_relaxed"]
    assert result["relaxation_used"] is True
    assert result["count"] >= 1
    assert {item["selection_strategy"] for item in result["results"]} == {"bm25_relaxed"}
    assert all(
        item["selection_reason"] == "significant_terms_after_zero_hit"
        for item in result["results"]
    )


def test_strict_bm25_hit_does_not_run_relaxed_stage(retrieval_hub: Path) -> None:
    result = context_hub.search_context(
        "tenant-rag",
        "manual tecnico produto",
        info_root=retrieval_hub,
    )

    assert result["search_engine"] == "fts5_bm25"
    assert result["search_stages"] == ["bm25_strict"]
    assert result["relaxation_used"] is False
    assert result["count"] >= 1
    assert {item["selection_strategy"] for item in result["results"]} == {"bm25_strict"}


def test_whatsapp_limit_deduplicates_and_diversifies_documents(retrieval_hub: Path) -> None:
    whatsapp = context_hub.search_context(
        "tenant-rag",
        "manual tecnico produto",
        filters={"request_surface": "whatsapp"},
        limit=99,
        info_root=retrieval_hub,
    )
    general = context_hub.search_context(
        "tenant-rag",
        "manual tecnico produto",
        limit=99,
        info_root=retrieval_hub,
    )

    assert whatsapp["count"] == 8
    assert len({item["doc_id"] for item in whatsapp["results"]}) == 8
    assert len({item["snippet"] for item in whatsapp["results"]}) == 8
    assert general["count"] > whatsapp["count"]
    assert general["count"] <= 12


def test_local_lexical_fallback_preserves_strict_then_relaxed_contract(retrieval_hub: Path) -> None:
    paths = hub_paths._tenant_paths("tenant-rag", info_root=retrieval_hub)
    with hub_storage._connect(paths) as connection:
        connection.execute("DELETE FROM context_hub_chunks_fts")
        connection.commit()

    result = context_hub.search_context(
        "tenant-rag",
        "manual tecnico produto",
        info_root=retrieval_hub,
    )

    assert result["search_engine"] == "lexical_fallback"
    assert result["search_stages"] == ["lexical_strict"]
    assert result["relaxation_used"] is False
    assert result["count"] >= 1
    assert {item["selection_strategy"] for item in result["results"]} == {"lexical_strict"}
