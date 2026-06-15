from __future__ import annotations

from typing import Any, Callable

from .parser import AIResponseParser
from .schemas import AIAnswer


class GeminiClient:
    def __init__(self, responder: Callable[[str, dict[str, Any] | None], Any], parser: AIResponseParser | None = None):
        self.responder = responder
        self.parser = parser or AIResponseParser()

    def generate(self, prompt: str, metadata: dict[str, Any] | None = None) -> AIAnswer:
        return self.parser.parse(self.responder(prompt, metadata))


class MockGeminiClient:
    def __init__(self, responder: Callable[[str, dict[str, Any] | None], Any] | None = None):
        self.responder = responder
        self.parser = AIResponseParser()

    def generate(self, prompt: str, metadata: dict[str, Any] | None = None) -> AIAnswer:
        if self.responder:
            return self.parser.parse(self.responder(prompt, metadata))
        return AIAnswer(
            answer="",
            confidence=0.0,
            requires_human_review=True,
            reason="gemini_mock_unconfigured",
            raw={"mock": True},
        )
