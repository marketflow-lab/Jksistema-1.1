from __future__ import annotations

from typing import Any, Protocol

from .schemas import ListingSnapshot, PreviousQA, QuestionContext, SellerRules


class MercadoLivreWebhookReceiver:
    def normalize_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "topic": payload.get("topic") or payload.get("resource") or "",
            "resource": payload.get("resource") or "",
            "user_id": payload.get("user_id") or payload.get("seller_id") or "",
            "raw": payload,
        }


class QuestionFetcher(Protocol):
    def fetch_question(self, question_id: str) -> QuestionContext:
        ...


class ListingFetcher(Protocol):
    def fetch_listing(self, item_id: str) -> ListingSnapshot:
        ...


class PreviousQuestionsFetcher(Protocol):
    def fetch_previous(self, question: QuestionContext) -> list[PreviousQA]:
        ...


class MercadoLivreAnswerPublisher(Protocol):
    def publish_answer(self, question_id: str, answer: str) -> dict[str, Any]:
        ...


class SafeMercadoLivreAnswerPublisher:
    def __init__(self, publisher: MercadoLivreAnswerPublisher):
        self.publisher = publisher
        self._answered_ids: set[str] = set()

    def publish_answer(self, question_id: str, answer: str, *, auto_publish_enabled: bool = False) -> dict[str, Any]:
        question_id = str(question_id or "").strip()
        if not auto_publish_enabled:
            return {"published": False, "reason": "auto_publish_disabled"}
        if not question_id:
            return {"published": False, "reason": "missing_question_id"}
        if question_id in self._answered_ids:
            return {"published": False, "reason": "duplicate_answer_blocked"}
        result = self.publisher.publish_answer(question_id, answer)
        self._answered_ids.add(question_id)
        return {"published": True, "mercadolivre": result}


def context_from_agent_input(agent_input: dict[str, Any], *, auto_publish_enabled: bool = False) -> tuple[QuestionContext, ListingSnapshot, list[PreviousQA], SellerRules]:
    data = agent_input if isinstance(agent_input, dict) else {}
    question_data = data.get("question") if isinstance(data.get("question"), dict) else {}
    item_data = data.get("item") if isinstance(data.get("item"), dict) else {}
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    agent_intent = data.get("intent") if isinstance(data.get("intent"), dict) else {}
    if not agent_intent and isinstance(context.get("intencao_atendimento"), dict):
        agent_intent = context.get("intencao_atendimento") or {}
    question = QuestionContext(
        id=str(question_data.get("id") or context.get("question_id") or "").strip(),
        text=str(question_data.get("text") or context.get("pergunta") or "").strip(),
        item_id=str(question_data.get("item_id") or item_data.get("id") or context.get("item_id") or "").strip(),
        buyer_id=str((question_data.get("from") or {}).get("id") if isinstance(question_data.get("from"), dict) else question_data.get("buyer_id") or "").strip(),
        status=str(question_data.get("status") or "UNANSWERED").strip(),
        raw={**question_data, "_agent_intent": dict(agent_intent)},
    )
    listing = ListingSnapshot(
        id=str(item_data.get("id") or context.get("item_id") or question.item_id or "").strip(),
        title=str(item_data.get("title") or context.get("titulo") or "").strip(),
        description=str(context.get("descricao") or item_data.get("description") or "").strip(),
        attributes=item_data.get("attributes") if isinstance(item_data.get("attributes"), list) else [],
        permalink=str(item_data.get("permalink") or item_data.get("link") or item_data.get("url") or context.get("permalink") or context.get("link") or "").strip(),
        price=item_data.get("price"),
        available_quantity=item_data.get("available_quantity"),
        category_id=str(item_data.get("category_id") or "").strip(),
        condition=str(item_data.get("condition") or "").strip(),
        seller_id=str(item_data.get("seller_id") or "").strip(),
        raw=item_data,
    )
    previous: list[PreviousQA] = []
    for event in question_data.get("history") or question_data.get("buyer_question_chat") or []:
        if not isinstance(event, dict):
            continue
        role = str(event.get("role") or event.get("from_role") or "").lower()
        text = str(event.get("text") or "").strip()
        if not text:
            continue
        if role in {"seller", "loja", "store"} and previous:
            previous[-1].answer = text
        elif role not in {"seller", "loja", "store"}:
            previous.append(PreviousQA(question=text))
    rules = SellerRules(
        store_name=str(data.get("store") or data.get("loja") or "").strip(),
        guidance=str(data.get("app_guidance") or "").strip(),
        commercial_policy=(
            dict(data.get("commercial_state_policy"))
            if isinstance(data.get("commercial_state_policy"), dict)
            else {}
        ),
        behavior_profile=(
            dict(data.get("seller_behavior_profile"))
            if isinstance(data.get("seller_behavior_profile"), dict)
            else {}
        ),
        auto_publish_enabled=auto_publish_enabled,
    )
    return question, listing, previous, rules
