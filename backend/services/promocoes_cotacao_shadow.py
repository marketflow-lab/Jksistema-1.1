"""Compatibility facade for the shared Mercado Livre quote observer."""

from __future__ import annotations

from typing import Any

from backend.services.mercadolivre_cotacao_shadow import (
    observar_cotacao_financeira,
    registrar_erro_shadow,
    reset_contadores_shadow,
    snapshot_contadores_shadow,
)


def observar_cotacao_promocao(
    *,
    preco_efetivo: Any,
    custo_produto: Any,
    aliquota_imposto: Any,
    tarifa_total: Any,
    tarifa_exata: bool,
    tarifa_fonte: Any,
    tarifa_contexto_preco: Any,
    frete_vendedor: Any,
    frete_exato: bool,
    frete_fonte: Any,
    frete_contexto_preco: Any,
    promocao: Any,
    resultado_legado: Any,
    client_id: Any = None,
    loja: Any = None,
) -> str:
    """Preserve the original Promoções API while using the shared observer."""

    return observar_cotacao_financeira(
        origem="promocoes",
        preco_efetivo=preco_efetivo,
        custo_produto=custo_produto,
        aliquota_imposto=aliquota_imposto,
        tarifa_total=tarifa_total,
        tarifa_exata=tarifa_exata,
        tarifa_fonte=tarifa_fonte,
        tarifa_contexto_preco=tarifa_contexto_preco,
        frete_vendedor=frete_vendedor,
        frete_exato=frete_exato,
        frete_fonte=frete_fonte,
        frete_contexto_preco=frete_contexto_preco,
        contexto_financeiro=promocao,
        resultado_legado=resultado_legado,
        client_id=client_id,
        loja=loja,
    )


__all__ = [
    "observar_cotacao_promocao",
    "registrar_erro_shadow",
    "snapshot_contadores_shadow",
    "reset_contadores_shadow",
]
