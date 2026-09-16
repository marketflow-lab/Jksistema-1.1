"""Estimativas locais a partir da cotacao e da oferta da campanha selecionada."""

from __future__ import annotations

import math


def _numero(valor):
    if valor is None or isinstance(valor, bool):
        return None
    try:
        numero = float(valor)
    except (ValueError, TypeError, OverflowError):
        return None
    return numero if math.isfinite(numero) else None


def estimar_tarifa_promocao(raw, preco, fee, *, preco_raw=None,
                            desconto_validado=None, conflito="", tipo_promocao=""):
    """Nao inventa aliquotas nem reaproveita cotacoes de outro preco/anuncio.

    O chamador confirma cliente, loja, anuncio e campanha antes de consultar a
    cotacao. Aqui, valores de outra faixa de preco nunca viram tarifa estimada.
    A estimativa nao transforma a evidencia em exata nem autoriza automacao.
    """
    preco = _numero(preco)
    contexto = _numero(fee.get("ad_cost_price_context"))
    fonte_api = str(fee.get("ad_cost_source") or "")
    if (preco is None or preco <= 0 or contexto is None
            or abs(contexto - preco) > 0.02 or "listing_prices" not in fonte_api):
        return None

    tarifa = _numero(fee.get("ad_cost"))
    fonte = "estimativa.listing_prices"
    motivo = "Tarifa-base consultada para o preco da campanha; beneficio ML nao confirmado."
    if tarifa is None:
        # percentage_fee ja inclui o parcelamento; nunca somar esse componente
        # novamente. Os percentuais separados so sao usados quando completos.
        percentual = _numero(fee.get("sale_fee_total_pct"))
        if percentual is None and fee.get("pct_scope") == "total_commission":
            percentual = _numero(fee.get("sale_fee_pct"))
        if percentual is None:
            meli = _numero(fee.get("meli_fee_pct"))
            financiamento = _numero(fee.get("financing_fee_pct"))
            if meli is not None and financiamento is not None and min(meli, financiamento) >= 0:
                percentual = meli + financiamento
        fixa = _numero(fee.get("fixed_fee_amount"))
        if (percentual is None or not 0 <= percentual <= 100 or fixa is None
                or fixa < 0 or fee.get("fixed_fee_source") in {"estimativa_legada", "listing_fee"}):
            return None
        tarifa = preco * percentual / 100.0 + fixa
        fonte = "estimativa.listing_prices.componentes"
        motivo = "Tarifa reconstruida com percentual total e taxa fixa da consulta no preco da campanha."
    if not 0 <= tarifa <= preco:
        return None

    charged = _numero(fee.get("promotion_fee_charged"))
    if fee.get("promotion_fee_discount_applied") is True:
        if charged is not None and 0 <= charged <= preco:
            tarifa = charged
        # ad_cost tambem pode conter o total ja ajustado. Nao abater novamente.
        return {"tarifa": round(tarifa, 2), "fonte": fonte + ".promocao_aplicada",
                "motivo": "Tarifa com beneficio ja aplicado; contexto da cotacao ainda nao confirmado."}

    if conflito:
        return {"tarifa": round(tarifa, 2), "fonte": fonte,
                "motivo": "Tarifa-base da consulta; os dados da oferta divergem e o beneficio ML nao foi abatido."}

    mesmo_preco = _numero(preco_raw) is not None and abs(float(preco_raw) - preco) <= 0.02
    desconto = _numero(desconto_validado) if mesmo_preco else None
    origem_desconto = "beneficio_validado"
    if desconto is None and mesmo_preco:
        tipo = str(raw.get("promotion_type") or raw.get("type") or tipo_promocao).strip().upper()
        base = _numero(raw.get("original_price"))
        meli = _numero(raw.get("meli_percentage"))
        seller = _numero(raw.get("seller_percentage"))
        # Um percentual ML isolado pode servir como estimativa, mas nunca vira
        # beneficio confirmado. O desconto total limita o valor possivel.
        if (tipo in {"SMART", "PRICE_MATCHING", "PRICE_MATCHING_MELI_ALL",
                     "SMART_PRICE_MATCHING", "MARKETPLACE_CAMPAIGN"}
                and base is not None and base > preco and meli is not None
                and 0 <= meli <= 100):
            total = round(base - preco, 2)
            aporte = round(base * meli / 100.0, 2)
            if 0 <= aporte <= total + 0.02:
                if raw.get("seller_percentage") in (None, ""):
                    desconto = min(aporte, total)
                    origem_desconto = "percentual_meli"
                elif seller is not None and 0 <= seller <= 100 and seller + meli <= 100:
                    residual = round(total - round(base * seller / 100.0, 2), 2)
                    # Percentuais arredondados podem divergir levemente da
                    # divisao monetaria; mantenha a conciliacao com o preco.
                    tolerancia = max(0.02, base * 0.001 + 0.01)
                    if 0 <= residual <= total and abs(residual - aporte) <= tolerancia:
                        desconto = residual
                        origem_desconto = "coparticipacao_reconciliada"
    if desconto is not None and 0 <= desconto <= tarifa:
        tarifa -= desconto
        fonte += ".menos_" + origem_desconto
        motivo = "Tarifa da consulta ajustada com a participacao ML informada para esta oferta; valor final estimado."
    return {"tarifa": round(tarifa, 2), "fonte": fonte, "motivo": motivo}
