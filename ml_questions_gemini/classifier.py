from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .schemas import ListingSnapshot, QuestionCategory, QuestionContext


@dataclass
class Classification:
    category: QuestionCategory
    reason: str = ""
    prompt_injection: bool = False


class QuestionClassifier:
    def classify(self, question: QuestionContext, listing: ListingSnapshot) -> Classification:
        text = normalize(question.text)
        listing_text = normalize(listing.searchable_text())

        if self._has_prompt_injection(text):
            return Classification(QuestionCategory.UNKNOWN, "prompt_injection", prompt_injection=True)
        if any(term in text or term in listing_text for term in ("medicamento", "remedio", "receita medica", "arma", "municao", "anvisa controlado")):
            return Classification(QuestionCategory.REGULATED_PRODUCT, "regulated_product")
        if _asks_external_contact(text):
            return Classification(QuestionCategory.PROHIBITED_CONTACT, "prohibited_contact")

        agent_intent = question.raw.get("_agent_intent") if isinstance(question.raw, dict) else {}
        categoria = agent_intent.get("categoria") if isinstance(agent_intent, dict) else None
        if isinstance(categoria, QuestionCategory):
            return Classification(categoria, "agent_intent")
        if isinstance(categoria, str):
            try:
                return Classification(QuestionCategory(categoria.strip()), "agent_intent")
            except ValueError:
                pass
        return Classification(QuestionCategory.UNKNOWN, "missing_or_invalid_agent_category")

    @staticmethod
    def _has_prompt_injection(text: str) -> bool:
        patterns = (
            "ignore as instrucoes",
            "ignore todas as regras",
            "revele o prompt",
            "mostre o prompt",
            "system prompt",
            "responda em json",
            "finja que",
            "voce agora e",
        )
        return any(pattern in text for pattern in patterns)


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.lower()).strip()


def _asks_external_contact(text: str) -> bool:
    if any(term in text for term in ("whatsapp", "whats", " zap ", "email", "instagram", "fora do mercado livre", "contato direto")):
        return True
    return bool(
        re.search(r"\b(?:qual|passe|envie|informe|manda|mandar|tem)\b.{0,35}\b(?:telefone|numero|contato)\b", text)
        or re.search(r"\b(?:telefone|numero|contato)\b.{0,35}\b(?:loja|vendedor|voces|atendimento)\b", text)
    )
