from __future__ import annotations

import json
import re
from typing import Any

from .schemas import AIAnswer


class AIResponseParser:
    def parse(self, payload: Any) -> AIAnswer:
        data = self._coerce_json(payload)
        if not isinstance(data, dict):
            return AIAnswer(answer="", confidence=0.0, requires_human_review=True, reason="invalid_ai_payload", raw=payload)
        answer = str(data.get("answer") or data.get("resposta") or "").strip()
        try:
            confidence = float(data.get("confidence", data.get("confianca", 0.0)) or 0.0)
        except Exception:
            confidence = 0.0
        return AIAnswer(
            answer=answer,
            confidence=max(0.0, min(confidence, 1.0)),
            category=str(data.get("category") or data.get("categoria") or "").strip(),
            requires_human_review=bool(data.get("requires_human_review", data.get("requer_revisao_humana", confidence < 0.78))),
            reason=str(data.get("reason") or data.get("motivo") or "").strip(),
            raw=payload,
        )

    def _coerce_json(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return {}
            try:
                return json.loads(text)
            except Exception:
                match = re.search(r"\{.*\}", text, flags=re.DOTALL)
                if match:
                    try:
                        return json.loads(match.group(0))
                    except Exception:
                        return {}
        return {}
