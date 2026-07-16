from __future__ import annotations

from typing import Any

from .audit import AuditLogger
from .classifier import QuestionClassifier
from .config import GeminiQuestionsSettings
from .metrics import MetricsService
from .policy import PolicyRouter
from .prompt_builder import PromptBuilder
from .review import HumanReviewQueue
from .schemas import (
    ListingSnapshot,
    PreviousQA,
    ProcessingResult,
    PublishDecision,
    QuestionContext,
    RouteAction,
    SellerRules,
    ValidationResult,
)
from .search import SearchDecisionService, SearchService
from .gemini_client import MockGeminiClient
from .validator import AnswerValidator


class QuestionAnswerOrchestrator:
    def __init__(
        self,
        *,
        settings: GeminiQuestionsSettings | None = None,
        gemini_client: Any | None = None,
        classifier: QuestionClassifier | None = None,
        policy_router: PolicyRouter | None = None,
        search_decision: SearchDecisionService | None = None,
        search_service: SearchService | None = None,
        prompt_builder: PromptBuilder | None = None,
        validator: AnswerValidator | None = None,
        review_queue: HumanReviewQueue | None = None,
        audit_logger: AuditLogger | None = None,
        metrics: MetricsService | None = None,
    ):
        self.settings = settings or GeminiQuestionsSettings()
        self.gemini_client = gemini_client or MockGeminiClient()
        self.classifier = classifier or QuestionClassifier()
        self.policy_router = policy_router or PolicyRouter()
        self.search_decision = search_decision or SearchDecisionService()
        self.search_service = search_service or SearchService()
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.validator = validator or AnswerValidator()
        self.review_queue = review_queue or HumanReviewQueue()
        self.audit_logger = audit_logger or AuditLogger()
        self.metrics = metrics or MetricsService()

    def process(
        self,
        *,
        question: QuestionContext,
        listing: ListingSnapshot,
        previous_questions: list[PreviousQA] | None = None,
        rules: SellerRules | None = None,
    ) -> ProcessingResult:
        previous_questions = previous_questions or []
        rules = rules or SellerRules()
        rules.min_confidence = self.settings.min_confidence
        rules.max_chars = self.settings.max_chars
        rules.max_sentences = self.settings.max_sentences
        if not rules.whitelisted_domains:
            rules.whitelisted_domains = list(self.settings.whitelisted_domains)
        if not rules.auto_publish_enabled:
            rules.auto_publish_enabled = bool(self.settings.auto_publish_enabled)

        classification = self.classifier.classify(question, listing)
        route = self.policy_router.route(
            classification.category,
            question,
            listing,
            prompt_injection=classification.prompt_injection,
        )
        self.metrics.increment(f"category.{classification.category.value}")
        self.metrics.increment(f"route.{route.action.value}")

        if route.action in {RouteAction.HUMAN_REVIEW, RouteAction.BLOCKED}:
            validation = ValidationResult(False, [route.reason or route.action.value], 0.0)
            result = self._result(
                question=question,
                category=classification.category,
                route=route.action,
                decision=PublishDecision.HUMAN_REVIEW,
                validation=validation,
                needs_human=True,
                reason=route.reason,
                source="policy",
            )
            self.review_queue.enqueue(result)
            return result

        search_results = []
        effective_route = route.action
        if route.action == RouteAction.SEARCH_AND_AI and self.search_decision.should_search(classification.category, question, listing, rules):
            search_results = self.search_service.search(question.text, rules.whitelisted_domains)
        elif route.action == RouteAction.SEARCH_AND_AI:
            effective_route = RouteAction.AI

        prompt = self.prompt_builder.build(
            question=question,
            listing=listing,
            previous_questions=previous_questions,
            category=classification.category,
            rules=rules,
            search_results=search_results,
        )
        try:
            ai_answer = self.gemini_client.generate(
                prompt,
                {
                    "question_id": question.id,
                    "item_id": listing.id,
                    "question_text": question.text,
                    "listing_link": listing.permalink,
                    "listing_title": listing.title,
                    "history_count": len(previous_questions),
                    "history_source": "same_buyer_or_listing",
                    "category": classification.category.value,
                },
            )
        except Exception as exc:
            validation = ValidationResult(False, ["gemini_error"], 0.0)
            result = self._result(
                question=question,
                category=classification.category,
                route=effective_route,
                decision=PublishDecision.HUMAN_REVIEW,
                validation=validation,
                prompt=prompt,
                needs_human=True,
                reason=type(exc).__name__,
                source="gemini_error",
            )
            self.review_queue.enqueue(result)
            return result

        validation = self.validator.validate(
            ai_answer.answer,
            question=question,
            listing=listing,
            category=classification.category,
            rules=rules,
            confidence=ai_answer.confidence,
            compatibility_analysis=getattr(self.gemini_client, "compatibility_analysis", None),
        )
        decision = self._publication_decision(
            validation,
            ai_answer.confidence,
            rules.auto_publish_enabled and not ai_answer.requires_human_review,
        )
        result = self._result(
            question=question,
            category=classification.category,
            route=effective_route,
            decision=decision,
            answer=ai_answer.answer,
            confidence=ai_answer.confidence,
            validation=validation,
            prompt=prompt,
            ai_raw=ai_answer.raw,
            needs_human=decision != PublishDecision.PUBLISH,
            reason=ai_answer.reason,
            source="gemini",
        )
        if result.needs_human:
            self.review_queue.enqueue(result)
        return result

    def _publication_decision(self, validation: ValidationResult, confidence: float, auto_publish_enabled: bool) -> PublishDecision:
        if not validation.ok:
            return PublishDecision.HUMAN_REVIEW
        if not auto_publish_enabled:
            return PublishDecision.HUMAN_REVIEW
        if confidence < self.settings.min_confidence:
            return PublishDecision.HUMAN_REVIEW
        return PublishDecision.PUBLISH

    def _result(
        self,
        *,
        question: QuestionContext,
        category,
        route: RouteAction,
        decision: PublishDecision,
        validation: ValidationResult,
        answer: str = "",
        confidence: float = 0.0,
        prompt: str = "",
        ai_raw: Any = None,
        needs_human: bool = True,
        reason: str = "",
        source: str = "",
    ) -> ProcessingResult:
        audit = self.audit_logger.log(
            "ml_question_processed",
            question_id=question.id,
            category=category.value,
            route=route.value,
            decision=decision.value,
            needs_human=needs_human,
            validation_issues=list(validation.issues),
            source=source,
        )
        return ProcessingResult(
            question_id=question.id,
            category=category,
            route=route,
            decision=decision,
            answer=answer,
            needs_human=needs_human,
            confidence=confidence,
            validation=validation,
            prompt=prompt,
            ai_raw=ai_raw,
            audit=audit,
            source=source,
            reason=reason,
        )
