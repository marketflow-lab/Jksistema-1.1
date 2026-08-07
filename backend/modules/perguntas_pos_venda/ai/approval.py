"""Approval policy used by public-question and post-sale consumers."""

from __future__ import annotations

from .inputs import _perguntas_ia_mensagens_aprovacao
from .validation import _perguntas_ia_v2_exigir_aprovacao, _pos_venda_ia_v2_exigir_aprovacao


def build_messages(question: dict, store: str) -> list[dict]:
    return _perguntas_ia_mensagens_aprovacao(question, store)


def question_requires_approval() -> bool:
    return _perguntas_ia_v2_exigir_aprovacao()


def post_sale_requires_approval() -> bool:
    return _pos_venda_ia_v2_exigir_aprovacao()


__all__ = ["build_messages", "post_sale_requires_approval", "question_requires_approval"]
