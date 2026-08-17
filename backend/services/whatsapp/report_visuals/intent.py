"""Report-chart intent detection."""

from __future__ import annotations

import re
from typing import Any

from .common import _text_key

def should_generate_report_charts(prompt: Any) -> bool:
    """Return True only for analytical/report requests or an explicit chart."""

    text = _text_key(prompt)
    if not text:
        return False
    if re.search(r"\b(grafico|graficos|visual|visualizacao|chart)\b", text):
        return True
    if re.search(r"\b(relatorio|analise|comparacao|comparativo|diagnostico|balanco|consolidado)\b", text):
        return True
    numeric_domain = bool(
        re.search(
            r"\b(vendas?|pedidos?|faturamento|estoque|depositos?|anuncios?|skus?|"
            r"produtos?|alertas?|operacional|lojas?|contas?)\b",
            text,
        )
    )
    if numeric_domain and re.search(
        r"\b(comparar|compare|comparando|versus|vs|diferenca|evolucao|tendencia|ranking|"
        r"mais vendido|mais vendida|menos vendido|menos vendida|por loja|por conta|por dia|por semana|por mes)\b",
        text,
    ):
        return True
    return bool(
        re.search(r"\bresumo\b", text)
        and re.search(
            r"\b(vendas?|pedidos?|faturamento|estoque|depositos?|anuncios?|skus?|produtos?|alertas?|operacional)\b",
            text,
        )
    )
