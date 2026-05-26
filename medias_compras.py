import math
from typing import Any

ItemEntrada = dict[str, Any]
ItemResultado = dict[str, Any]
ResultadoCalculo = dict[str, Any]


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)

    texto = str(value).strip().replace(".", "").replace(",", ".")
    if not texto:
        return default
    try:
        return float(texto)
    except Exception:
        return default


def calcular_reposicao_periodo(
    total_vendido_periodo: Any,
    saldo_atual: Any,
    estoque_em_transito: Any = 0.0,
    periodo_meses: float = 6.0,
    lead_time_meses: float = 6.0,
    ciclo_compra_meses: float = 3.0,
    margem_seguranca_meses: float = 1.0,
    fator_crescimento: float = 1.0,
) -> dict[str, Any]:
    periodo = max(_to_float(periodo_meses, 6.0), 1.0)
    lead = max(_to_float(lead_time_meses, 6.0), 0.0)
    ciclo = max(_to_float(ciclo_compra_meses, 3.0), 0.0)
    margem = max(_to_float(margem_seguranca_meses, 1.0), 0.0)
    fator = max(_to_float(fator_crescimento, 1.0), 0.0)
    total_periodo = max(_to_float(total_vendido_periodo, 0.0), 0.0)
    estoque_fisico = max(_to_float(saldo_atual, 0.0), 0.0)
    estoque_transito = max(_to_float(estoque_em_transito, 0.0), 0.0)

    vmm = total_periodo / periodo if periodo > 0 else 0.0
    vmm_ajustada = vmm * fator
    meses_cobertura = lead + ciclo + margem
    demanda_total = vmm_ajustada * meses_cobertura
    posicao_estoque = estoque_fisico + estoque_transito
    compra_bruta = demanda_total - posicao_estoque
    compra_sugerida = 0 if compra_bruta <= 0 else int(math.ceil(compra_bruta / 5.0) * 5)

    return {
        "periodo_meses": round(periodo, 2),
        "lead_time_meses": round(lead, 2),
        "ciclo_compra_meses": round(ciclo, 2),
        "margem_seguranca_meses": round(margem, 2),
        "meses_cobertura": round(meses_cobertura, 2),
        "fator_crescimento": round(fator, 4),
        "media_mensal": round(vmm, 2),
        "media_mensal_ajustada": round(vmm_ajustada, 2),
        "demanda_total": round(demanda_total, 2),
        "estoque_fisico": round(estoque_fisico, 2),
        "estoque_em_transito": round(estoque_transito, 2),
        "posicao_estoque": round(posicao_estoque, 2),
        "compra_sugerida": int(compra_sugerida),
    }


def calcular_medias_compras(itens: list[ItemEntrada], meses_cobertura: float = 2.0) -> ResultadoCalculo:
    """Calcula compra sugerida por SKU com base em media mensal e estoque atual."""
    meses = max(_to_float(meses_cobertura, 2.0), 0.1)

    resultados: list[ItemResultado] = []
    total_compra = 0
    total_investimento = 0.0

    for item in itens:
        sku = str(item.get("sku") or "").strip()
        descricao = str(item.get("descricao") or "").strip()
        media_mensal = max(_to_float(item.get("media_mensal"), 0.0), 0.0)
        estoque_atual = max(_to_float(item.get("estoque_atual"), 0.0), 0.0)
        custo_unitario = max(_to_float(item.get("custo_unitario"), 0.0), 0.0)
        lead_time_dias = max(int(_to_float(item.get("lead_time_dias"), 15)), 0)

        consumo_diario = media_mensal / 30.0
        demanda_lead_time = consumo_diario * lead_time_dias
        estoque_alvo = media_mensal * meses

        compra_sugerida = max(0, math.ceil((estoque_alvo + demanda_lead_time) - estoque_atual))
        investimento = compra_sugerida * custo_unitario

        total_compra += compra_sugerida
        total_investimento += investimento

        resultados.append({
            "sku": sku,
            "descricao": descricao,
            "media_mensal": round(media_mensal, 2),
            "estoque_atual": round(estoque_atual, 2),
            "lead_time_dias": lead_time_dias,
            "meses_cobertura": round(meses, 2),
            "estoque_alvo": round(estoque_alvo, 2),
            "demanda_lead_time": round(demanda_lead_time, 2),
            "compra_sugerida": int(compra_sugerida),
            "custo_unitario": round(custo_unitario, 2),
            "investimento": round(investimento, 2),
        })

    return {
        "success": True,
        "resumo": {
            "itens": len(resultados),
            "total_compra_sugerida": int(total_compra),
            "total_investimento": round(total_investimento, 2),
            "meses_cobertura": round(meses, 2),
        },
        "itens": resultados,
    }
