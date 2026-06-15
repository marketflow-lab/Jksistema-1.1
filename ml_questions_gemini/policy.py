from __future__ import annotations

from dataclasses import dataclass

from .schemas import ListingSnapshot, QuestionCategory, QuestionContext, RouteAction


@dataclass
class RouteDecision:
    action: RouteAction
    reason: str = ""


class PolicyRouter:
    def route(
        self,
        category: QuestionCategory,
        question: QuestionContext,
        listing: ListingSnapshot,
        *,
        prompt_injection: bool = False,
    ) -> RouteDecision:
        if prompt_injection:
            return RouteDecision(RouteAction.HUMAN_REVIEW, "prompt_injection")
        if category in {QuestionCategory.POST_SALE, QuestionCategory.REGULATED_PRODUCT}:
            return RouteDecision(RouteAction.HUMAN_REVIEW, category.value)
        if category in {QuestionCategory.COMPATIBILITY, QuestionCategory.PRODUCT_FEATURE, QuestionCategory.WARRANTY_ORIGINALITY, QuestionCategory.OTHER_PRODUCT}:
            return RouteDecision(RouteAction.SEARCH_AND_AI, "needs_context")
        return RouteDecision(RouteAction.AI, "default_ai")
