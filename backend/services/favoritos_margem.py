"""Shared margin helpers for Favoritos and Black Jhon reports."""

from __future__ import annotations

import re
from typing import Any


def margem_parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("\xa0", " ")
    text = re.sub(r"[^0-9,.\-]", "", text)
    if not text or text in {"-", ".", ","}:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except Exception:
        return None


def margem_to_rate(value: Any) -> float | None:
    parsed = margem_parse_float(value)
    if parsed is None:
        return None
    return (parsed / 100.0) if parsed > 1.0 else parsed


def margem_formatar_moeda(value: Any) -> str:
    parsed = margem_parse_float(value)
    if parsed is None:
        return ""
    raw = f"{parsed:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {raw}"


def _flag_frete_gratis(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return text in {"1", "true", "sim", "s", "yes", "gratis", "gratuito", "free"}


def _first_float(data: dict[str, Any], fields: tuple[str, ...]) -> float | None:
    for field in fields:
        parsed = margem_parse_float(data.get(field))
        if parsed is not None:
            return parsed
    return None


def margem_calcular_anuncio(
    anuncio: dict[str, Any],
    *,
    sku_hint: str = "",
    custo: Any = None,
    imposto_rate: Any = None,
    taxa_padrao: Any = None,
) -> dict[str, Any]:
    """Calculate ML-style margin using the same inputs expected by Favoritos.

    The result keeps the historical field names used by Favoritos, plus
    margem_completa/faltando_margem for Black Jhon reports.
    """

    data = dict(anuncio or {})
    sku_margem = str(
        sku_hint
        or data.get("sku")
        or data.get("sku_display")
        or data.get("seller_sku")
        or data.get("seller_custom_field")
        or data.get("SELLER_SKU")
        or ""
    ).strip()

    preco_final = _first_float(
        data,
        (
            "preco_final_margem",
            "preco_promocional",
            "promotional_price",
            "promotion_price",
            "sale_price",
            "price",
            "preco",
            "valor",
        ),
    )
    custo_float = margem_parse_float(custo)
    imposto_float = margem_to_rate(imposto_rate)

    tarifa = _first_float(data, ("ad_cost", "tarifa", "tarifa_ml", "fee_per_sale", "sale_fee_amount"))
    taxa_pct = margem_to_rate(data.get("sale_fee_pct"))
    if taxa_pct is None:
        taxa_pct = margem_to_rate(taxa_padrao)
    if tarifa is None and preco_final is not None and taxa_pct is not None:
        tarifa = round(float(preco_final) * float(taxa_pct), 2)

    frete = _first_float(
        data,
        (
            "shipping_seller_cost",
            "shipping_cost",
            "frete_ml",
            "shipping_list_cost",
            "shipping_base_cost",
            "frete",
            "custo_frete",
        ),
    )
    free_shipping = bool(
        data.get("free_shipping") is True
        or data.get("frete_gratis") is True
        or _flag_frete_gratis(data.get("Frete Gratis"))
        or _flag_frete_gratis(data.get("Frete Grátis"))
        or _flag_frete_gratis(data.get("Frete Grátis"))
    )
    shipping_info = data.get("shipping") if isinstance(data.get("shipping"), dict) else {}
    if shipping_info:
        free_shipping = free_shipping or bool(shipping_info.get("free_shipping") is True)
    if frete is None and shipping_info:
        fields = ("seller_cost", "shipping_cost", "base_cost", "list_cost")
        if not free_shipping:
            fields = fields + ("cost",)
        frete = _first_float(shipping_info, fields)

    result: dict[str, Any] = {"sku_margem": sku_margem}
    if preco_final is not None:
        result["preco_final_margem"] = round(float(preco_final), 2)
    if custo_float is not None:
        result["custo"] = round(float(custo_float), 4)
        result["custo_text"] = margem_formatar_moeda(custo_float)
    if imposto_float is not None:
        result["imposto_percentual"] = round(float(imposto_float) * 100.0, 4)
    if taxa_pct is not None:
        result["taxa_ml_percentual"] = round(float(taxa_pct) * 100.0, 4)
    if tarifa is not None:
        result["tarifa_ml"] = round(float(tarifa), 2)
        result["tarifa_ml_text"] = margem_formatar_moeda(tarifa)
    if frete is not None:
        result["frete_ml"] = round(float(frete), 2)
        result["frete_ml_text"] = margem_formatar_moeda(frete)

    faltando: list[str] = []
    if preco_final is None or preco_final <= 0:
        faltando.append("preco")
    if custo_float is None:
        faltando.append("custo")
    if imposto_float is None:
        faltando.append("imposto")
    if tarifa is None:
        faltando.append("tarifa")
    if frete is None and free_shipping:
        faltando.append("frete")

    result["faltando_margem"] = faltando
    result["margem_completa"] = not faltando
    if faltando:
        result["margem_status"] = "Faltando " + ", ".join(faltando)
        return result

    frete_calc = float(frete or 0.0)
    tarifa_calc = float(tarifa or 0.0)
    imposto_valor = round(float(preco_final) * float(imposto_float), 2)
    valor_liquido = round(float(preco_final) - float(custo_float) - frete_calc - imposto_valor - tarifa_calc, 2)
    margem_pct = round((valor_liquido * 100.0) / float(preco_final), 2)
    result.update(
        {
            "imposto_valor": imposto_valor,
            "imposto_valor_text": margem_formatar_moeda(imposto_valor),
            "valor_liquido": valor_liquido,
            "valor_liquido_text": margem_formatar_moeda(valor_liquido),
            "margem_percentual": margem_pct,
            "margem": margem_pct,
            "margem_text": f"{margem_pct:.2f}%".replace(".", ","),
            "margem_status": "ok",
        }
    )
    return result


__all__ = [
    "margem_calcular_anuncio",
    "margem_formatar_moeda",
    "margem_parse_float",
    "margem_to_rate",
]
