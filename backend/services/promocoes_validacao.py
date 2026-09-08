"""Limites da comparacao de precos por anuncio."""

import json

from fastapi import HTTPException


def validar_promocoes_por_anuncio(tipo_a="", promocoes=None):
    """Cupons dependem do carrinho e nao representam preco final por anuncio."""
    tipos = [tipo_a]
    if isinstance(promocoes, str):
        try:
            promocoes = json.loads(promocoes)
        except (TypeError, ValueError):
            promocoes = []  # A validacao do payload continua no fluxo original.
    if isinstance(promocoes, list):
        for promo in promocoes:
            if isinstance(promo, dict):
                tipos.extend(promo.get(key) for key in ("promotion_type", "promo_b_type", "promoType", "type"))
    if any(str(tipo or "").strip().upper() == "SELLER_COUPON_CAMPAIGN" for tipo in tipos):
        raise HTTPException(
            status_code=400,
            detail=(
                "Campanhas de cupom por compra nao sao compativeis com esta analise e aplicacao por anuncio. "
                "Selecione uma campanha com preco promocional por anuncio."
            ),
        )
