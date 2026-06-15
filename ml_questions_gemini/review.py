from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .schemas import ProcessingResult


class HumanReviewQueue:
    def __init__(self):
        self.items: list[dict[str, Any]] = []

    def enqueue(self, result: ProcessingResult) -> dict[str, Any]:
        item = {
            "question_id": result.question_id,
            "category": result.category.value,
            "answer": result.answer,
            "reason": result.reason,
            "validation": asdict(result.validation),
            "source": result.source,
        }
        self.items.append(item)
        return item
