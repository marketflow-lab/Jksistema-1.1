from __future__ import annotations

import re

from .classifier import normalize
from .schemas import ListingSnapshot, QuestionCategory, QuestionContext, SellerRules, ValidationResult


class AnswerValidator:
    def validate(
        self,
        answer: str,
        *,
        question: QuestionContext,
        listing: ListingSnapshot,
        category: QuestionCategory,
        rules: SellerRules,
        confidence: float,
    ) -> ValidationResult:
        issues: list[str] = []
        text = str(answer or "").strip()
        norm = normalize(text)
        if not text:
            issues.append("empty_answer")
        if len(text) > rules.max_chars:
            issues.append("too_long")
        if count_sentences(text) > rules.max_sentences:
            issues.append("too_many_sentences")
        if confidence < rules.min_confidence:
            issues.append("low_confidence")
        if category == QuestionCategory.POST_SALE:
            issues.append("post_sale_not_supported_v2")
        if category == QuestionCategory.REGULATED_PRODUCT:
            issues.append("regulated_product")
        if _has_external_contact(text):
            issues.append("external_contact")
        if _has_internal_ai_terms(norm):
            issues.append("internal_ai_leak")
        if _has_forbidden_identity(norm):
            issues.append("forbidden_identity")
        if category == QuestionCategory.COMPATIBILITY and _asserts_compatibility(norm) and not _has_compatibility_evidence(question, listing):
            issues.append("compatibility_without_evidence")
        if category == QuestionCategory.WARRANTY_ORIGINALITY and _asserts_originality_or_warranty(norm) and not _has_originality_or_warranty_evidence(listing):
            issues.append("originality_or_warranty_without_evidence")
        if category == QuestionCategory.INVOICE and "nota fiscal" in norm and not _listing_has_any(listing, ("nota fiscal", "nf-e", "nfe")):
            issues.append("invoice_without_evidence")
        return ValidationResult(ok=not issues, issues=issues, confidence=confidence)


def count_sentences(text: str) -> int:
    cleaned = re.sub(r"\b(sr|sra|dr|dra)\.", r"\1", text.lower())
    parts = [part.strip() for part in re.split(r"[.!?]+", cleaned) if part.strip()]
    return len(parts) if parts else (1 if text.strip() else 0)


def _has_external_contact(text: str) -> bool:
    norm = normalize(text)
    if any(term in norm for term in ("whatsapp", "whats", " zap ", "telefone", "instagram", "email", "pix", "fora do mercado livre")):
        return True
    if re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", text, flags=re.I):
        return True
    if re.search(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?\d{4,5}[-.\s]?\d{4}", text):
        return True
    if re.search(r"https?://|www\.", text, flags=re.I):
        return True
    return False


def _has_internal_ai_terms(norm: str) -> bool:
    return any(term in norm for term in ("vertex", "gemini", "inteligencia artificial", " ia ", "prompt", "json", "api", "modelo", "treinamento salvo", "ferramenta externa"))


def _has_forbidden_identity(norm: str) -> bool:
    return any(term in norm for term in ("assistente da jk sistema", "assistente do jk sistema", "sou o assistente", "sou a assistente", "sou uma ia", "sou um assistente", "jk sistema"))


def _asserts_compatibility(norm: str) -> bool:
    if any(term in norm for term in ("preciso confirmar", "nao tenho informacao", "nao consigo confirmar", "recomendo confirmar", "verifique", "informe o modelo")):
        return False
    return any(term in norm for term in ("sim, serve", "serve sim", "e compativel", "compativel com", "funciona no", "aplica no"))


def _has_compatibility_evidence(question: QuestionContext, listing: ListingSnapshot) -> bool:
    listing_text = normalize(listing.searchable_text())
    question_text = normalize(question.text)
    tokens = [token for token in re.findall(r"[a-z0-9]{3,}", question_text) if token not in {"serve", "compativel", "aplica", "funciona", "carro", "moto"}]
    if not tokens:
        return False
    hits = sum(1 for token in tokens if token in listing_text)
    if hits >= min(2, len(tokens)):
        return True
    return any(attr in listing_text for attr in ("compatibilidade", "veiculos compativeis", "aplicacao"))


def _asserts_originality_or_warranty(norm: str) -> bool:
    return any(term in norm for term in ("produto original", "peca original", "tem garantia", "garantia de", "genuino", "genuina"))


def _has_originality_or_warranty_evidence(listing: ListingSnapshot) -> bool:
    return _listing_has_any(listing, ("original", "genuino", "genuina", "garantia", "nota fiscal"))


def _listing_has_any(listing: ListingSnapshot, needles: tuple[str, ...]) -> bool:
    listing_text = normalize(listing.searchable_text())
    return any(needle in listing_text for needle in needles)
