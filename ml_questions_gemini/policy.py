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
            return RouteDecision(RouteAction.AI, "prompt_injection_as_untrusted_data")
        if category == QuestionCategory.REGULATED_PRODUCT:
            return RouteDecision(RouteAction.AI, "regulated_product_safe_draft")
        if category == QuestionCategory.UNKNOWN:
            return RouteDecision(RouteAction.AI, "classification_advisory_only")
        if category == QuestionCategory.POST_SALE:
            return RouteDecision(RouteAction.AI, "post_sale_draft")
        if category in {QuestionCategory.COMPATIBILITY, QuestionCategory.PRODUCT_FEATURE, QuestionCategory.WARRANTY_ORIGINALITY, QuestionCategory.OTHER_PRODUCT}:
            return RouteDecision(RouteAction.SEARCH_AND_AI, "needs_context")
        return RouteDecision(RouteAction.AI, "default_ai")
