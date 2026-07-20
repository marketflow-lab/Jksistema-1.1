from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from backend.services import perguntas_pos_venda_store as store


def _iso(hours: int = 0, days: int = 0) -> str:
    value = datetime.now(timezone.utc) + timedelta(hours=hours, days=days)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _conversation(
    pack_id: str,
    *,
    order_id: str | None = None,
    last_message_date: str | None = None,
    text: str = "Mensagem",
    unread: bool = False,
    sku: str = "SKU-1",
) -> dict:
    return {
        "pack_id": pack_id,
        "order_id": order_id or f"ORDER-{pack_id}",
        "date_created": last_message_date or _iso(hours=-2),
        "last_message_date": last_message_date or _iso(hours=-1),
        "last_message_id": f"MSG-{pack_id}",
        "last_message_text": text,
        "buyer_id": f"BUYER-{pack_id}",
        "buyer_name": f"Comprador {pack_id}",
        "nao_lida": unread,
        "unread_count": 1 if unread else 0,
        "items": [{"id": f"MLB-{pack_id}", "sku": sku, "title": f"Produto {pack_id}"}],
    }


def test_isolates_tenants_and_stores(tmp_path):
    tenant_a = tmp_path / "tenant-a"
    tenant_b = tmp_path / "tenant-b"
    conversation = _conversation("PACK-1")

    assert store.upsert_conversations(tenant_a, "Loja Azul", "SELLER-1", [conversation]) == 1
    assert store.upsert_conversations(tenant_b, "Loja Azul", "SELLER-1", [conversation]) == 1
    assert store.upsert_conversations(tenant_a, "Loja Verde", "SELLER-1", [_conversation("PACK-2")]) == 1

    assert store.list_conversations(tenant_a, "Loja Azul", "SELLER-1", 365, 0, 20)["total"] == 1
    assert store.list_conversations(tenant_a, "Loja Verde", "SELLER-1", 365, 0, 20)["total"] == 1
    assert store.list_conversations(tenant_b, "Loja Azul", "SELLER-1", 365, 0, 20)["total"] == 1
    assert (tenant_a / store.DB_FILENAME).exists()
    assert (tenant_b / store.DB_FILENAME).exists()


def test_upsert_is_idempotent_and_older_payload_cannot_replace_newer(tmp_path):
    tenant = tmp_path / "tenant"
    newer = _conversation(
        "PACK-1",
        last_message_date=_iso(hours=-1),
        text="Resposta mais nova",
        unread=True,
    )
    older = _conversation(
        "PACK-1",
        last_message_date=_iso(hours=-4),
        text="Mensagem antiga",
        unread=False,
    )

    assert store.upsert_conversations(tenant, "Loja", "SELLER", [newer]) == 1
    assert store.upsert_conversations(tenant, "Loja", "SELLER", [newer]) == 1
    assert store.upsert_conversations(tenant, "Loja", "SELLER", [older]) == 1

    result = store.list_conversations(tenant, "Loja", "SELLER", 365, 0, 20)
    assert result["total"] == 1
    assert result["conversations"][0]["last_message_text"] == "Resposta mais nova"
    assert result["conversations"][0]["nao_lida"] is True

    fallback = _conversation("", order_id="ORDER-FALLBACK", last_message_date=_iso(hours=-2))
    assert store.upsert_conversations(tenant, "Loja", "SELLER", [fallback, fallback]) == 2
    assert store.list_conversations(tenant, "Loja", "SELLER", 365, 0, 20)["total"] == 2


def test_list_pagination_search_unread_and_summary(tmp_path):
    tenant = tmp_path / "tenant"
    conversations = [
        _conversation("PACK-1", last_message_date=_iso(hours=-1), text="Bomba nova", sku="ABC-1"),
        _conversation(
            "PACK-2",
            last_message_date=_iso(hours=-2),
            text="Preciso de ajuda",
            unread=True,
            sku="ÁRVORE-123",
        ),
        _conversation("PACK-3", last_message_date=_iso(hours=-3), text="Terceira conversa", sku="XYZ-9"),
        _conversation("PACK-OLD", last_message_date=_iso(days=-500), text="Fora da cobertura"),
    ]
    assert store.upsert_conversations(tenant, "Loja", "SELLER", conversations) == 4

    first = store.list_conversations(tenant, "Loja", "SELLER", 365, 0, 2)
    assert [item["pack_id"] for item in first["conversations"]] == ["PACK-1", "PACK-2"]
    assert first == {
        "conversations": first["conversations"],
        "total": 3,
        "next_offset": 2,
        "has_next": True,
    }

    second = store.list_conversations(tenant, "Loja", "SELLER", 365, 2, 2)
    assert [item["pack_id"] for item in second["conversations"]] == ["PACK-3"]
    assert second["next_offset"] is None
    assert second["has_next"] is False

    searched = store.list_conversations(
        tenant,
        "Loja",
        "SELLER",
        365,
        0,
        20,
        search="arvore 123",
    )
    assert [item["pack_id"] for item in searched["conversations"]] == ["PACK-2"]

    unread = store.list_conversations(
        tenant,
        "Loja",
        "SELLER",
        365,
        0,
        20,
        unread_only=True,
    )
    assert [item["pack_id"] for item in unread["conversations"]] == ["PACK-2"]

    result_summary = store.summary(tenant, "Loja", "SELLER", 365)
    assert result_summary["total"] == 3
    assert result_summary["conversations_total"] == 3
    assert result_summary["unread_total"] == 1
    assert result_summary["conversations_unread_total"] == 1


def test_sync_state_resumes_partial_bootstrap_and_preserves_completion_on_error(tmp_path):
    tenant = tmp_path / "tenant"

    initial = store.get_state(tenant, "Loja", "SELLER")
    assert initial["status"] == "idle"
    assert initial["cursor"] == 0
    assert initial["bootstrap_complete"] is False

    started = store.begin_sync(tenant, "Loja", "SELLER", "bootstrap", 365, cursor=0)
    assert started["status"] == "running"
    progress = store.update_sync_progress(
        tenant,
        "Loja",
        "SELLER",
        cursor=40,
        conversations_saved=7,
        orders_scanned=40,
    )
    assert progress["cursor"] == 40
    assert progress["conversations_saved"] == 7

    failed = store.fail_sync(tenant, "Loja", "SELLER", "429 temporario", cursor=20)
    assert failed["status"] == "failed"
    assert failed["cursor"] == 40
    assert failed["bootstrap_complete"] is False

    resumed = store.begin_sync(tenant, "Loja", "SELLER", "bootstrap", 365, cursor=0)
    assert resumed["cursor"] == 40
    assert resumed["conversations_saved"] == 7
    completed = store.finish_sync(
        tenant,
        "Loja",
        "SELLER",
        "bootstrap",
        365,
        bootstrap_complete=True,
        cursor=100,
        orders_total=100,
    )
    assert completed["status"] == "completed"
    assert completed["bootstrap_complete"] is True
    assert completed["cursor"] == 100

    incremental = store.begin_sync(tenant, "Loja", "SELLER", "incremental", 30, cursor=0)
    assert incremental["bootstrap_complete"] is True
    assert incremental["cursor"] == 0
    store.update_sync_progress(
        tenant,
        "Loja",
        "SELLER",
        cursor=25,
        conversations_saved=2,
        orders_scanned=25,
    )
    failed_incremental = store.fail_sync(tenant, "Loja", "SELLER", "timeout", cursor=10)
    assert failed_incremental["bootstrap_complete"] is True
    assert failed_incremental["cursor"] == 25
    assert failed_incremental["error"] == "timeout"


def test_database_uses_wal_and_busy_timeout(tmp_path):
    tenant = tmp_path / "tenant"
    store.get_state(tenant, "Loja", "SELLER")
    connection = sqlite3.connect(tenant / store.DB_FILENAME)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] > 0
    finally:
        connection.close()
