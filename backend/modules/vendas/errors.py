"""Domain errors raised by Vendas without depending on FastAPI."""

from __future__ import annotations

from typing import Any


class VendasDomainError(Exception):
    def __init__(self, status_code: int, detail: Any, headers: dict[str, str] | None = None):
        super().__init__(str(detail))
        self.status_code = int(status_code)
        self.detail = detail
        self.headers = headers


__all__ = ["VendasDomainError"]
