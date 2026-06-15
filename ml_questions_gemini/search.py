from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from .classifier import normalize
from .schemas import ListingSnapshot, QuestionCategory, QuestionContext, SellerRules


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


class SearchProvider(Protocol):
    def search(self, query: str, domains: list[str], max_results: int = 5) -> list[SearchResult]:
        ...


class SearchDecisionService:
    def should_search(self, category: QuestionCategory, question: QuestionContext, listing: ListingSnapshot, rules: SellerRules) -> bool:
        if not rules.search_enabled:
            return False
        if category not in {QuestionCategory.COMPATIBILITY, QuestionCategory.PRODUCT_FEATURE, QuestionCategory.WARRANTY_ORIGINALITY, QuestionCategory.OTHER_PRODUCT}:
            return False
        listing_text = normalize(listing.searchable_text())
        return len(listing_text) < 500 or category in {QuestionCategory.COMPATIBILITY, QuestionCategory.OTHER_PRODUCT}


class SearchService:
    def __init__(self, provider: SearchProvider | None = None):
        self.provider = provider

    def search(self, query: str, domains: list[str], max_results: int = 5) -> list[SearchResult]:
        whitelist = [domain.lower().strip() for domain in domains if domain.strip()]
        if not whitelist or not self.provider:
            return []
        raw_results = self.provider.search(query, whitelist, max_results=max_results)
        return [result for result in raw_results if domain_allowed(result.url, whitelist)][:max_results]


class StaticSearchProvider:
    def __init__(self, results: list[SearchResult]):
        self.results = list(results)

    def search(self, query: str, domains: list[str], max_results: int = 5) -> list[SearchResult]:
        return self.results[:max_results]


def domain_allowed(url: str, whitelist: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for allowed in whitelist:
        allowed = allowed.lower().strip()
        if allowed.startswith("www."):
            allowed = allowed[4:]
        if host == allowed or host.endswith("." + allowed):
            return True
    return False
