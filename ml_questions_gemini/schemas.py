from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class QuestionCategory(str, Enum):
    GREETING = "greeting"
    PRICE = "price"
    STOCK = "stock"
    SHIPPING = "shipping"
    COMPATIBILITY = "compatibility"
    PRODUCT_FEATURE = "product_feature"
    WARRANTY_ORIGINALITY = "warranty_originality"
    INVOICE = "invoice"
    OTHER_PRODUCT = "other_product"
    PROHIBITED_CONTACT = "prohibited_contact"
    REGULATED_PRODUCT = "regulated_product"
    POST_SALE = "post_sale"
    UNKNOWN = "unknown"


class RouteAction(str, Enum):
    AI = "ai"
    SEARCH_AND_AI = "search_and_ai"
    HUMAN_REVIEW = "human_review"
    BLOCKED = "blocked"


class PublishDecision(str, Enum):
    PUBLISH = "publish"
    HUMAN_REVIEW = "human_review"
    SKIP = "skip"
    ERROR = "error"


@dataclass
class QuestionContext:
    id: str
    text: str
    item_id: str = ""
    buyer_id: str = ""
    status: str = "UNANSWERED"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ListingSnapshot:
    id: str
    title: str = ""
    description: str = ""
    attributes: list[dict[str, Any]] = field(default_factory=list)
    permalink: str = ""
    price: Any = None
    available_quantity: Any = None
    category_id: str = ""
    condition: str = ""
    seller_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def attribute_value(self, names: list[str]) -> str:
        expected = {_normalize(name) for name in names}
        for attr in self.attributes:
            if not isinstance(attr, dict):
                continue
            attr_id = _normalize(attr.get("id"))
            attr_name = _normalize(attr.get("name"))
            if attr_id not in expected and attr_name not in expected:
                continue
            for key in ("value_name", "value", "values"):
                value = attr.get(key)
                if isinstance(value, list):
                    parts = []
                    for item in value:
                        if isinstance(item, dict):
                            parts.append(str(item.get("name") or item.get("value_name") or item.get("id") or "").strip())
                        else:
                            parts.append(str(item).strip())
                    return ", ".join([part for part in parts if part])
                if value is not None:
                    return str(value).strip()
        return ""

    def searchable_text(self) -> str:
        parts = [self.title, self.description, self.condition, self.category_id]
        for attr in self.attributes:
            if isinstance(attr, dict):
                parts.extend(str(attr.get(k) or "") for k in ("id", "name", "value_name", "value"))
        return " ".join(part for part in parts if part)


@dataclass
class PreviousQA:
    question: str
    answer: str = ""


@dataclass
class SellerRules:
    store_name: str = ""
    guidance: str = ""
    commercial_policy: dict[str, Any] = field(default_factory=dict)
    behavior_profile: dict[str, Any] = field(default_factory=dict)
    whitelisted_domains: list[str] = field(default_factory=list)
    auto_publish_enabled: bool = False
    min_confidence: float = 0.78
    max_sentences: int = 3
    max_chars: int = 900
    search_enabled: bool = True


@dataclass
class AIAnswer:
    answer: str
    confidence: float = 0.0
    category: str = ""
    requires_human_review: bool = True
    reason: str = ""
    raw: Any = None


@dataclass
class ValidationResult:
    ok: bool
    issues: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class ProcessingResult:
    question_id: str
    category: QuestionCategory
    route: RouteAction
    decision: PublishDecision
    answer: str = ""
    needs_human: bool = True
    confidence: float = 0.0
    validation: ValidationResult = field(default_factory=lambda: ValidationResult(False, ["not_validated"], 0.0))
    prompt: str = ""
    ai_raw: Any = None
    audit: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    reason: str = ""


def _normalize(value: Any) -> str:
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
