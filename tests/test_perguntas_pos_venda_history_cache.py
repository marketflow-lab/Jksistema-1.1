from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from backend.modules.perguntas_pos_venda.endpoints import post_sale_queries
from backend.modules.perguntas_pos_venda.endpoints import post_sale_sync as endpoints
from backend.modules.perguntas_pos_venda.endpoints.contracts import _ML_POS_VENDA_RECENT_DAYS
from backend.services import perguntas_pos_venda_store as store


def _iso(*, days: int = 0, minutes: int = 0) -> str:
    value = datetime.now(timezone.utc) + timedelta(days=days, minutes=minutes)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _conversation(pack_id: str, *, days: int = 0, text: str = "Mensagem") -> dict:
    return {
        "pack_id": pack_id,
        "order_id": f"ORDER-{pack_id}",
        "date_created": _iso(days=days, minutes=-5),
        "last_message_date": _iso(days=days),
        "last_message_id": f"MSG-{pack_id}",
        "last_message_text": text,
        "buyer_id": f"BUYER-{pack_id}",
        "buyer_name": f"Comprador {pack_id}",
        "nao_lida": True,
        "unread_count": 1,
        "items": [{"id": f"MLB-{pack_id}", "sku": f"SKU-{pack_id}"}],
    }


def _prepare_endpoint_globals(monkeypatch) -> None:
    class _Logger:
        def warning(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(endpoints, "logger", _Logger(), raising=False)
    monkeypatch.setattr(
        endpoints,
        "_integracoes_nome_normalizado",
        lambda value: str(value or "").strip().casefold(),
        raising=False,
    )
    monkeypatch.setattr(
        endpoints,
        "_perguntas_ia_diagnostico_texto",
        lambda value, _limit=500: str(value or "").strip(),
        raising=False,
    )
    endpoints.ENDPOINTS_STATE.pos_sale_sync_threads.clear()


def test_bootstrap_365_persists_all_pages_and_marks_complete(tmp_path, monkeypatch):
    _prepare_endpoint_globals(monkeypatch)
    calls = []

    def fake_remote(**kwargs):
        calls.append(dict(kwargs))
        offset = int(kwargs["offset"])
        if offset == 0:
            return {
                "conversas": [_conversation("PACK-NEW")],
                "orders_avaliadas": 20,
                "orders_total": 40,
                "next_offset": 20,
            }
        assert offset == 20
        return {
            "conversas": [_conversation("PACK-OLD", days=-200)],
            "orders_avaliadas": 20,
            "orders_total": 40,
            "next_offset": None,
        }

    monkeypatch.setattr(endpoints, "_ml_pos_venda_listar_conversas_remoto", fake_remote)
    monkeypatch.setattr(endpoints.time, "sleep", lambda _seconds: None)

    endpoints._ml_pos_venda_sync_worker(
        client_id="CLIENT-A",
        tenant_path=str(tmp_path),
        loja="Loja A",
        seller_id="SELLER-A",
        mode="bootstrap",
        coverage_days=365,
        max_orders=10000,
    )

    assert [(call["dias"], call["offset"]) for call in calls] == [(365, 0), (365, 20)]
    state = store.get_state(tmp_path, "Loja A", "SELLER-A")
    assert state["status"] == "completed"
    assert state["bootstrap_complete"] is True
    assert state["orders_total"] == 40
    cached = store.list_conversations(tmp_path, "Loja A", "SELLER-A", 365, 0, 20)
    assert {item["pack_id"] for item in cached["conversations"]} == {"PACK-NEW", "PACK-OLD"}


def test_incremental_uses_recent_window_and_keeps_365_day_history(tmp_path, monkeypatch):
    _prepare_endpoint_globals(monkeypatch)
    store.upsert_conversations(
        tmp_path,
        "Loja A",
        "SELLER-A",
        [_conversation("PACK-HISTORY", days=-200)],
    )
    store.finish_sync(
        tmp_path,
        "Loja A",
        "SELLER-A",
        mode="bootstrap",
        coverage_days=365,
        bootstrap_complete=True,
    )
    calls = []

    def fake_remote(**kwargs):
        calls.append(dict(kwargs))
        return {
            "conversas": [_conversation("PACK-RECENT")],
            "orders_avaliadas": 3,
            "orders_total": 3,
            "next_offset": None,
        }

    monkeypatch.setattr(endpoints, "_ml_pos_venda_listar_conversas_remoto", fake_remote)
    endpoints._ml_pos_venda_sync_worker(
        client_id="CLIENT-A",
        tenant_path=str(tmp_path),
        loja="Loja A",
        seller_id="SELLER-A",
        mode="incremental",
        coverage_days=_ML_POS_VENDA_RECENT_DAYS,
        max_orders=10000,
    )

    assert len(calls) == 1
    assert calls[0]["dias"] == 30
    assert calls[0]["offset"] == 0
    cached = store.list_conversations(tmp_path, "Loja A", "SELLER-A", 365, 0, 20)
    assert {item["pack_id"] for item in cached["conversations"]} == {
        "PACK-HISTORY",
        "PACK-RECENT",
    }
    assert store.get_state(tmp_path, "Loja A", "SELLER-A")["bootstrap_complete"] is True


def test_partial_bootstrap_resumes_from_saved_cursor_on_next_run(tmp_path, monkeypatch):
    _prepare_endpoint_globals(monkeypatch)
    calls = []

    def fake_remote(**kwargs):
        offset = int(kwargs["offset"])
        calls.append(offset)
        if offset == 0:
            return {
                "conversas": [_conversation("PACK-PAGE-1")],
                "orders_avaliadas": 20,
                "orders_total": 40,
                "next_offset": 20,
            }
        assert offset == 20
        return {
            "conversas": [_conversation("PACK-PAGE-2")],
            "orders_avaliadas": 20,
            "orders_total": 40,
            "next_offset": None,
        }

    monkeypatch.setattr(endpoints, "_ml_pos_venda_listar_conversas_remoto", fake_remote)
    monkeypatch.setattr(endpoints.time, "sleep", lambda _seconds: None)

    common = {
        "client_id": "CLIENT-A",
        "tenant_path": str(tmp_path),
        "loja": "Loja A",
        "seller_id": "SELLER-A",
        "mode": "bootstrap",
        "coverage_days": 365,
        "max_orders": 20,
    }
    endpoints._ml_pos_venda_sync_worker(**common)
    partial = store.get_state(tmp_path, "Loja A", "SELLER-A")
    assert partial["status"] == "failed"
    assert partial["cursor"] == 20
    assert partial["orders_scanned"] == 20
    assert partial["bootstrap_complete"] is False

    endpoints._ml_pos_venda_sync_worker(**common)
    complete = store.get_state(tmp_path, "Loja A", "SELLER-A")
    assert calls == [0, 20]
    assert complete["status"] == "completed"
    assert complete["cursor"] == 40
    assert complete["orders_scanned"] == 40
    assert complete["bootstrap_complete"] is True
    assert store.list_conversations(tmp_path, "Loja A", "SELLER-A", 365, 0, 20)["total"] == 2


def test_remote_failure_preserves_cache_and_exposes_stale_state(tmp_path, monkeypatch):
    _prepare_endpoint_globals(monkeypatch)
    store.upsert_conversations(
        tmp_path,
        "Loja A",
        "SELLER-A",
        [_conversation("PACK-CACHED")],
    )

    def fake_remote(**_kwargs):
        raise HTTPException(status_code=429, detail="rate limit")

    monkeypatch.setattr(endpoints, "_ml_pos_venda_listar_conversas_remoto", fake_remote)
    endpoints._ml_pos_venda_sync_worker(
        client_id="CLIENT-A",
        tenant_path=str(tmp_path),
        loja="Loja A",
        seller_id="SELLER-A",
        mode="bootstrap",
        coverage_days=365,
        max_orders=10000,
    )

    response = endpoints._ml_pos_venda_cache_response(
        tenant_path=str(tmp_path),
        client_id="CLIENT-A",
        loja="Loja A",
        seller_id="SELLER-A",
        dias=365,
        offset=0,
        limit=20,
        busca="",
        nao_lidas=False,
        summary_only=False,
    )
    assert response["success"] is True
    assert [item["pack_id"] for item in response["conversas"]] == ["PACK-CACHED"]
    assert response["sync"]["status"] == "failed"
    assert response["sync"]["stale"] is True
    assert "rate limit" in response["sync"]["last_error"]
    assert response["sync"]["bootstrap_complete"] is False


def test_endpoint_schedules_bootstrap_once_then_only_recent_refresh(tmp_path, monkeypatch):
    _prepare_endpoint_globals(monkeypatch)
    scheduled = []
    monkeypatch.setattr(
        post_sale_queries,
        "_obter_cfg_ml",
        lambda *_args: {"user_id": "SELLER-A"},
        raising=False,
    )
    monkeypatch.setattr(post_sale_queries, "get_tenant_path", lambda _client_id: str(tmp_path), raising=False)
    monkeypatch.setattr(
        post_sale_queries,
        "_ml_pos_venda_schedule_sync",
        lambda **kwargs: scheduled.append(dict(kwargs)) or True,
    )

    first = post_sale_queries.ml_pos_venda_listar_conversas(loja="Loja A", client_id="CLIENT-A")
    assert first["source"] == "local_cache"
    assert [(item["mode"], item["coverage_days"]) for item in scheduled] == [("bootstrap", 365)]

    store.finish_sync(
        tmp_path,
        "Loja A",
        "SELLER-A",
        mode="bootstrap",
        coverage_days=365,
        bootstrap_complete=True,
    )
    post_sale_queries.ml_pos_venda_listar_conversas(
        loja="Loja A",
        client_id="CLIENT-A",
        force_refresh=True,
    )
    assert [(item["mode"], item["coverage_days"]) for item in scheduled] == [
        ("bootstrap", 365),
        ("incremental", 30),
    ]

    post_sale_queries.ml_pos_venda_listar_conversas(
        loja="Loja A",
        client_id="CLIENT-A",
        summary_only=True,
        sync_mode="cache",
    )
    assert len(scheduled) == 2
