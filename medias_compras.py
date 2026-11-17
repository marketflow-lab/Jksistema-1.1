import calendar
import math
from datetime import date, datetime
from statistics import median
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
    *,
    vendas_mensais: dict[str, Any] | None = None,
    data_referencia: Any = None,
    entradas_previstas: list[dict[str, Any]] | None = None,
    primeira_venda: Any = None,
) -> dict[str, Any]:
    """Keep legacy callers stable; monthly history enables arrival-based planning.

    The new policy projects available stock before receipt and completes seven
    months in total after receipt. The legacy purchase-cycle parameter applies
    only to callers without monthly history.
    """
    if vendas_mensais is not None:
        return _calcular_planejamento_reposicao(
            vendas_mensais, saldo_atual, estoque_em_transito, periodo_meses,
            lead_time_meses, margem_seguranca_meses, fator_crescimento,
            data_referencia, entradas_previstas, primeira_venda,
        )
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


def _numero_planejamento(valor: Any, padrao: float = 0.0) -> float:
    """Accept finite quantities without interpreting a decimal dot as thousands."""
    try:
        if isinstance(valor, bool):
            return padrao
        if isinstance(valor, str):
            valor = valor.strip()
            if "," in valor:
                valor = valor.replace(".", "").replace(",", ".")
        numero = float(valor)
        return max(0.0, numero) if math.isfinite(numero) else padrao
    except (TypeError, ValueError, OverflowError):
        return padrao


def _data_planejamento(valor: Any) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor or "").strip()[:10])
    except ValueError:
        return None


def _somar_meses_planejamento(referencia: date, meses: int) -> date:
    ordinal = referencia.year * 12 + referencia.month - 1 + meses
    ano, mes_zero = divmod(ordinal, 12)
    mes = mes_zero + 1
    return date(ano, mes, min(referencia.day, calendar.monthrange(ano, mes)[1]))


def _prever_vendas_mensais(vendas, referencia, periodo, saldo, primeira_venda):
    """Transparent six-month signal, damped toward the preceding quarter.

    The last (possibly partial) calendar month is never used. A 70/30 blend
    updates the demand level rather than compounding a quarterly percentage
    into every future month. Small/intermittent series remain reviewable.
    """
    refs = [_somar_meses_planejamento(referencia.replace(day=1), i).strftime("%Y-%m")
            for i in range(-12, 0)]
    valores = {}
    for mes in refs:
        if mes not in vendas:
            continue
        valor = _numero_planejamento(vendas[mes], -1.0)
        if valor >= 0:
            valores[mes] = valor
    primeira = _data_planejamento(primeira_venda)
    inicio = primeira.strftime("%Y-%m") if primeira else next(
        (mes for mes in refs if valores.get(mes, 0) > 0), refs[0])
    disponiveis = [mes for mes in refs if mes >= inicio and mes in valores]
    janela = [valores[mes] for mes in refs[-periodo:] if mes in disponiveis]
    media = sum(janela) / len(janela) if janela else 0.0
    resultado = {
        "media_mensal": media,
        "media_mensal_prevista": media,
        "tendencia_vendas": "insuficiente",
        "variacao_vendas_percentual": None,
        "meses_historico": len(disponiveis),
        "tendencia_motivo": "Histórico insuficiente para comparar dois períodos de três meses completos.",
    }
    ultimos = refs[-6:]
    if not all(mes in disponiveis for mes in ultimos):
        return resultado
    serie = [valores[mes] for mes in ultimos]
    if sum(serie) < 12 or sum(valor > 0 for valor in serie) < 4:
        resultado["tendencia_motivo"] = "Vendas baixas ou esporádicas; revise a previsão antes de comprar."
        return resultado
    if any(valor == 0 for valor in serie):
        resultado.update(
            tendencia_vendas="indeterminada",
            tendencia_motivo=(
                "Há meses sem vendas; confirme a disponibilidade do produto antes de interpretar alta ou queda."
            ),
        )
        return resultado
    anterior, recente = sum(serie[:3]) / 3, sum(serie[3:]) / 3
    if anterior <= 0:
        resultado["tendencia_motivo"] = "Não há vendas suficientes no período anterior para estimar a tendência."
        return resultado
    variacao = round((recente / anterior - 1) * 100, 10)
    direcao = 1 if variacao >= 10 else -1 if variacao <= -10 else 0
    primeiro, meio, ultimo = serie[3:]
    variacao_recente = round((ultimo / primeiro - 1) * 100, 10)
    direcao_recente = (
        1 if variacao_recente >= 10 and meio >= primeiro * 1.05 - 1e-9
        and ultimo >= meio * 1.05 - 1e-9 else
        -1 if variacao_recente <= -10 and meio <= primeiro * 0.95 + 1e-9
        and ultimo <= meio * 0.95 + 1e-9 else 0
    )
    if direcao and direcao_recente and direcao != direcao_recente:
        resultado.update(
            tendencia_vendas="indeterminada",
            variacao_vendas_percentual=variacao_recente,
            tendencia_motivo=(
                "O movimento dos três últimos meses completos diverge da comparação entre trimestres; "
                "revise a tendência antes de ajustar a compra."
            ),
        )
        return resultado
    sinal_recente = not direcao and bool(direcao_recente)
    if sinal_recente:
        direcao, variacao = direcao_recente, variacao_recente
    resultado["variacao_vendas_percentual"] = variacao
    if direcao < 0 and saldo <= 0:
        resultado.update(
            tendencia_vendas="indeterminada",
            tendencia_motivo="Queda recente com estoque atual zerado; confirme se houve falta de produto.",
        )
        return resultado
    if sinal_recente:
        resultado.update(
            tendencia_vendas="alta" if direcao > 0 else "queda",
            media_mensal_prevista=ultimo * 0.7 + recente * 0.3,
            tendencia_motivo=(
                "Os três últimos meses completos se movem no mesmo sentido, com variação acumulada "
                "de pelo menos 10%. A previsão combina 70% do último mês e 30% da média recente, "
                "sem repetir esse crescimento ou queda nos meses futuros."
            ),
        )
        return resultado
    consistentes = sum((valor - anterior) * direcao > anterior * 0.05 for valor in serie[3:])
    if direcao and consistentes < 2:
        resultado.update(
            tendencia_vendas="indeterminada",
            media_mensal_prevista=float(median(serie)),
            tendencia_motivo="Variação concentrada em um mês; a previsão usa o nível típico dos seis meses.",
        )
        return resultado
    resultado.update(
        tendencia_vendas="alta" if direcao > 0 else "queda" if direcao < 0 else "estavel",
        media_mensal_prevista=recente * 0.7 + anterior * 0.3,
        tendencia_motivo=(
            "Comparação dos três últimos meses completos com os três anteriores; "
            "alta ou queda a partir de 10%, com movimento consistente em pelo menos dois meses. "
            "Previsão com 70% do ritmo recente e 30% do anterior, sem crescimento composto."
        ),
    )
    return resultado


def _calcular_planejamento_reposicao(
    vendas, saldo_atual, estoque_em_transito, periodo_meses, lead_time_meses,
    margem_seguranca_meses, fator_crescimento, data_referencia, entradas,
    primeira_venda,
):
    referencia = _data_planejamento(data_referencia) or date.today()
    periodo = min(12, max(1, int(_numero_planejamento(periodo_meses, 6))))
    lead = min(36, max(0, int(_numero_planejamento(lead_time_meses, 6))))
    reserva_meses = _numero_planejamento(margem_seguranca_meses, 1)
    fator = _numero_planejamento(fator_crescimento, 1)
    fisico = _numero_planejamento(saldo_atual)
    transito_legado = _numero_planejamento(estoque_em_transito)
    previsao = _prever_vendas_mensais(vendas, referencia, periodo, fisico, primeira_venda)
    ritmo = previsao["media_mensal_prevista"] * fator
    chegada = _somar_meses_planejamento(referencia, lead)
    alvo = ritmo * 7  # Total AFTER receipt; already includes the safety month.
    reserva = ritmo * reserva_meses
    avisos = []
    revisar = previsao["tendencia_vendas"] in {"insuficiente", "indeterminada"}
    if revisar:
        avisos.append(previsao["tendencia_motivo"])
    eventos = []
    mesmo_dia = sem_data = confirmado = posteriores = 0.0
    for entrada in entradas or []:
        if not isinstance(entrada, dict):
            continue
        quantidade = _numero_planejamento(entrada.get("quantidade"))
        if quantidade <= 0:
            continue
        confirmado += quantidade
        prevista = _data_planejamento(entrada.get("data_prevista"))
        if prevista is None or prevista < referencia:
            # Prevent duplicate purchasing without pretending an undated order
            # can protect stock before arrival. The result is explicitly provisional.
            sem_data += quantidade
            continue
        if entrada.get("data_estimada"):
            revisar = True
            if "Há recebimentos com data estimada; confirme o prazo antes de fechar a compra." not in avisos:
                avisos.append("Há recebimentos com data estimada; confirme o prazo antes de fechar a compra.")
        if prevista == chegada:
            mesmo_dia += quantidade
        elif prevista > chegada:
            posteriores += quantidade
        else:
            meses = 0
            while _somar_meses_planejamento(referencia, meses + 1) <= prevista:
                meses += 1
            inicio = _somar_meses_planejamento(referencia, meses)
            fim = _somar_meses_planejamento(referencia, meses + 1)
            instante = meses + (prevista - inicio).days / (fim - inicio).days
            eventos.append((instante, quantidade))
    saldo, faltante, instante_anterior = fisico, 0.0, 0.0
    for instante, quantidade in sorted(eventos):
        consumo = ritmo * (instante - instante_anterior)
        faltante += max(0.0, consumo - saldo)
        saldo = max(0.0, saldo - consumo) + quantidade
        instante_anterior = instante
    consumo = ritmo * (lead - instante_anterior)
    faltante += max(0.0, consumo - saldo)
    antes = max(0.0, saldo - consumo)
    necessidade = max(0.0, alvo - antes - mesmo_dia - sem_data)
    # Ignore floating point dust at exact pack boundaries.
    compra = max(0, int(math.ceil((necessidade - 1e-9) / 5) * 5))
    depois = antes + mesmo_dia + sem_data + compra
    if sem_data:
        revisar = True
        avisos.append(
            "Há compras confirmadas sem previsão válida de recebimento. Elas reduzem provisoriamente "
            "o novo pedido, mas não garantem a reserva antes da chegada; confirme as datas."
        )
    if transito_legado > confirmado + 0.01:
        avisos.append("Orçamentos ou outras quantidades sem compra confirmada não abastecem esta previsão.")
    if posteriores:
        avisos.append("Há recebimentos posteriores à nova chegada; revise o estoque acumulado nas próximas entregas.")
    if faltante > 1e-7:
        avisos.append("Há previsão de falta antes da chegada. A nova compra não repõe as vendas perdidas nesse intervalo.")
    elif antes + 1e-7 < reserva:
        avisos.append("A sobra prevista antes da chegada é menor que a reserva de um mês.")
    return {
        "periodo_meses": periodo,
        "lead_time_meses": lead,
        "ciclo_compra_meses": 6.0,
        "margem_seguranca_meses": reserva_meses,
        "meses_cobertura": lead + 7,
        "fator_crescimento": round(fator, 4),
        "media_mensal": round(previsao["media_mensal"], 2),
        "media_mensal_ajustada": round(ritmo, 2),
        "demanda_total": round(ritmo * (lead + 7), 2),
        "estoque_fisico": round(fisico, 2),
        "estoque_em_transito": round(transito_legado, 2),
        "posicao_estoque": round(fisico + transito_legado, 2),
        "compra_sugerida": compra,
        "planejamento_versao": 1,
        "media_mensal_prevista": round(ritmo, 2),
        "tendencia_vendas": previsao["tendencia_vendas"],
        "variacao_vendas_percentual": (
            round(previsao["variacao_vendas_percentual"], 2)
            if previsao["variacao_vendas_percentual"] is not None else None
        ),
        "tendencia_motivo": previsao["tendencia_motivo"],
        "meses_historico": previsao["meses_historico"],
        "data_chegada_prevista": chegada.isoformat(),
        "meses_estoque_apos_chegada": 7,
        "reserva_antes_chegada_quantidade": round(reserva, 2),
        "estoque_previsto_antes_chegada": round(antes, 2),
        "estoque_previsto_apos_chegada": round(depois, 2),
        "estoque_alvo_apos_chegada": round(alvo, 2),
        "falta_antes_chegada_quantidade": round(faltante, 2),
        "reserva_antes_chegada_atendida": antes + 1e-7 >= reserva,
        "risco_ruptura_antes_chegada": faltante > 1e-7,
        "avisos_planejamento": avisos,
        "transito_confirmado": round(confirmado, 2),
        "transito_sem_previsao_quantidade": round(sem_data, 2),
        "planejamento_requer_revisao": revisar,
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
