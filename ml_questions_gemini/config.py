from __future__ import annotations

import os
from dataclasses import dataclass, field


def truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "sim", "yes", "on"}:
        return True
    if text in {"0", "false", "nao", "no", "off"}:
        return False
    return default


def csv_list(value: object) -> list[str]:
    if value is None:
        return []
    return [part.strip().lower() for part in str(value).split(",") if part.strip()]


@dataclass
class GeminiQuestionsSettings:
    model: str = ""
    auto_publish_enabled: bool = False
    human_review_default: bool = True
    min_confidence: float = 0.78
    max_chars: int = 900
    max_sentences: int = 3
    whitelisted_domains: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "GeminiQuestionsSettings":
        try:
            confidence = float(os.getenv("AI_MIN_CONFIDENCE") or "0.78")
        except Exception:
            confidence = 0.78
        try:
            max_chars = int(float(os.getenv("AI_MAX_CHARS") or "900"))
        except Exception:
            max_chars = 900
        try:
            max_sentences = int(float(os.getenv("AI_MAX_SENTENCES") or "3"))
        except Exception:
            max_sentences = 3
        return cls(
            model=(os.getenv("ML_PERGUNTAS_GEMINI_MODEL") or os.getenv("IA_PERGUNTAS_GEMINI_MODEL") or "").strip(),
            auto_publish_enabled=truthy(os.getenv("AUTO_PUBLISH_ENABLED"), default=False),
            human_review_default=truthy(os.getenv("HUMAN_REVIEW_DEFAULT"), default=True),
            min_confidence=confidence,
            max_chars=max_chars,
            max_sentences=max_sentences,
            whitelisted_domains=csv_list(os.getenv("SEARCH_WHITELIST_DOMAINS")),
        )
