from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from backend.services import perguntas_pos_venda_perguntas_ml as questions
from backend.services import perguntas_pos_venda_pos_venda as post_sale


def _question(question_id: str, *, item_id: str | None, buyer_id: str) -> dict:
    question = {
        "id": question_id,
        "seller_id": "10",
        "from": {"id": buyer_id},
        "text": f"Texto {question_id}",
        "date_created": f"2026-09-17T12:00:0{question_id[-1]}Z",
        "answer": None,
    }
    if item_id is not None:
        question["item_id"] = item_id
    return question


def _normalize(question: dict, _items: dict, _users: dict) -> dict:
    source = deepcopy(question)
    buyer = source.get("from") if isinstance(source.get("from"), dict) else {}
    return {
        "id": source.get("id"),
        "date_created": source.get("date_created") or "",
        "last_updated": source.get("last_updated") or "",
        "item_id": source.get("item_id") or "",
        "item_title": "",
        "item_permalink": "",
        "item_thumbnail": "",
        "item_sku": "",
        "variation_id": "",
        "seller_id": source.get("seller_id"),
        "status": source.get("status") or "",
        "text": source.get("text") or "",
        "from_id": buyer.get("id"),
        "buyer_id": buyer.get("id"),
        "buyer_name": "",
        "buyer_nickname": "",
        "answer": source.get("answer"),
    }


def _response(rows: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(status_code=200, json=lambda: {"questions": deepcopy(rows)})


def test_question_history_keeps_same_buyer_only_on_current_item(monkeypatch):
    rows = [
        _question("Q1", item_id="MLB1", buyer_id="20"),
        _question("Q2", item_id="MLB2", buyer_id="20"),
        _question("Q3", item_id="MLB1", buyer_id="99"),
        _question("Q4", item_id=None, buyer_id="20"),
    ]

    def request(*_args, **kwargs):
        assert kwargs["params"]["item_id"] == "MLB1"
        return _response(rows), {"access_token": "refreshed"}

    monkeypatch.setattr(questions, "_ml_api_request", request, raising=False)
    monkeypatch.setattr(questions, "_ml_perguntas_normalizar", _normalize, raising=False)
    selected = _normalize(_question("Q0", item_id="MLB1", buyer_id="20"), {}, {})

    result, cfg = questions._ml_perguntas_anexar_historico_comprador(
        "tenant", "Loja", {}, "10", [selected]
    )

    assert cfg == {"access_token": "refreshed"}
    assert [row["id"] for row in result[0]["buyer_question_history"]] == ["Q0", "Q1"]
    assert [event["text"] for event in result[0]["buyer_question_chat"]] == [
        "Texto Q0", "Texto Q1"
    ]


def test_post_sale_listing_history_rejects_other_items_for_same_buyer(monkeypatch):
    rows = [
        _question("Q1", item_id="MLB1", buyer_id="20"),
        _question("Q2", item_id="MLB2", buyer_id="20"),
        _question("Q3", item_id="MLB1", buyer_id="99"),
        _question("Q4", item_id=None, buyer_id="20"),
    ]

    def request(*_args, **kwargs):
        assert kwargs["params"]["item_id"] == "MLB1"
        return _response(rows), {"access_token": "refreshed"}

    monkeypatch.setattr(post_sale, "_ml_api_request", request, raising=False)
    monkeypatch.setattr(post_sale, "_ml_perguntas_normalizar", _normalize, raising=False)
    monkeypatch.setattr(
        post_sale, "_ml_perguntas_copia_historico",
        questions._ml_perguntas_copia_historico, raising=False,
    )
    monkeypatch.setattr(
        post_sale, "_ml_perguntas_montar_chat_historico",
        questions._ml_perguntas_montar_chat_historico, raising=False,
    )
    conversation = {
        "buyer_id": "20",
        "items": [{"id": "MLB1", "title": "Anuncio atual", "sku": "SKU-1"}],
    }

    result, cfg = post_sale._ml_pos_venda_anexar_perguntas_anuncio_comprador(
        "tenant", "Loja", {}, "10", conversation
    )

    assert cfg == {"access_token": "refreshed"}
    assert [row["id"] for row in result["buyer_listing_question_history"]] == ["Q1"]
    assert [event["text"] for event in result["buyer_listing_question_chat"]] == ["Texto Q1"]
    assert list(result["buyer_listing_question_history_by_item"]) == ["MLB1"]
