from __future__ import annotations

import datetime as dt
import os
import re
from typing import Any, Optional

from .runtime import (
    _ia_extrair_referencia_produto_mensagem,
    _ia_normalizar_data_iso_chat,
    _ia_normalizar_periodo_chat,
    _ia_parse_data_iso_flex,
    _normalizar_texto,
    get_tenant_path,
)


def _ia_tool_float(value: Any) -> float:
    try:
        texto = str(value or "").strip().replace("R$", "").replace(" ", "")
        if not texto:
            return 0.0
        if "," in texto and "." in texto:
            texto = texto.replace(".", "").replace(",", ".")
        elif "," in texto:
            texto = texto.replace(",", ".")
        return float(texto)
    except Exception:
        return 0.0


def _ia_tool_resolver_sku(client_id: str, mensagem: str, produto_tool: Optional[dict] = None) -> str:
    ref = _ia_extrair_referencia_produto_mensagem(mensagem)
    sku = str(ref.get("sku") or "").strip().upper()
    if sku:
        return sku
    if produto_tool:
        return str(((produto_tool.get("result") or {}).get("canonical_sku") or "")).strip().upper()
    return ""


def _ia_extrair_mes_ano_mensagem(mensagem: str, contexto: Optional[dict] = None) -> Optional[str]:
    raw = str(mensagem or "")
    texto = _normalizar_texto(raw)

    m1 = re.search(r"\b(20\d{2})[/-](0?[1-9]|1[0-2])\b", raw)
    if m1:
        ano = int(m1.group(1))
        mes = int(m1.group(2))
        return f"{ano:04d}-{mes:02d}"

    m2 = re.search(r"\b(0?[1-9]|1[0-2])[/-](20\d{2})\b", raw)
    if m2:
        mes = int(m2.group(1))
        ano = int(m2.group(2))
        return f"{ano:04d}-{mes:02d}"

    meses = {
        "JAN": 1, "JANEIRO": 1,
        "FEV": 2, "FEVEREIRO": 2,
        "MAR": 3, "MARCO": 3,
        "ABR": 4, "ABRIL": 4,
        "MAI": 5, "MAIO": 5,
        "JUN": 6, "JUNHO": 6,
        "JUL": 7, "JULHO": 7,
        "AGO": 8, "AGOSTO": 8,
        "SET": 9, "SETEMBRO": 9,
        "OUT": 10, "OUTUBRO": 10,
        "NOV": 11, "NOVEMBRO": 11,
        "DEZ": 12, "DEZEMBRO": 12,
    }

    m3 = re.search(r"\b(JANEIRO|JAN|FEVEREIRO|FEV|MARCO|MAR|ABRIL|ABR|MAIO|MAI|JUNHO|JUN|JULHO|JUL|AGOSTO|AGO|SETEMBRO|SET|OUTUBRO|OUT|NOVEMBRO|NOV|DEZEMBRO|DEZ)\s*(?:DE|/|-)?\s*(20\d{2})?\b", texto)
    if m3:
        mes = int(meses.get(m3.group(1)) or 0)
        ano_txt = str(m3.group(2) or "").strip()
        if ano_txt:
            ano = int(ano_txt)
            return f"{ano:04d}-{mes:02d}"

        ctx = contexto if isinstance(contexto, dict) else {}
        ini_ctx = _ia_normalizar_data_iso_chat(str(ctx.get("data_inicio") or ""))
        fim_ctx = _ia_normalizar_data_iso_chat(str(ctx.get("data_fim") or ""))
        ano_ref = None
        if ini_ctx:
            ano_ref = int(ini_ctx[:4])
        elif fim_ctx:
            ano_ref = int(fim_ctx[:4])
        else:
            ano_ref = dt.date.today().year
        return f"{int(ano_ref):04d}-{mes:02d}"

    m4 = re.search(r"\bMES\s*(0?[1-9]|1[0-2])(?:\s*DE\s*(20\d{2}))?\b", texto)
    if m4:
        mes = int(m4.group(1))
        ano_txt = str(m4.group(2) or "").strip()
        ano = int(ano_txt) if ano_txt else dt.date.today().year
        return f"{ano:04d}-{mes:02d}"

    return None


def _ia_extrair_meses_ano_mensagem(mensagem: str, contexto: Optional[dict] = None) -> list[str]:
    raw = str(mensagem or "")
    texto = _normalizar_texto(raw)
    meses_map = {
        "JAN": 1, "JANEIRO": 1,
        "FEV": 2, "FEVEREIRO": 2,
        "MAR": 3, "MARCO": 3,
        "ABR": 4, "ABRIL": 4,
        "MAI": 5, "MAIO": 5,
        "JUN": 6, "JUNHO": 6,
        "JUL": 7, "JULHO": 7,
        "AGO": 8, "AGOSTO": 8,
        "SET": 9, "SETEMBRO": 9,
        "OUT": 10, "OUTUBRO": 10,
        "NOV": 11, "NOVEMBRO": 11,
        "DEZ": 12, "DEZEMBRO": 12,
    }
    encontrados: list[str] = []

    for ano, mes in re.findall(r"\b(20\d{2})[/-](0?[1-9]|1[0-2])\b", raw):
        encontrados.append(f"{int(ano):04d}-{int(mes):02d}")
    for mes, ano in re.findall(r"\b(0?[1-9]|1[0-2])[/-](20\d{2})\b", raw):
        encontrados.append(f"{int(ano):04d}-{int(mes):02d}")

    ano_ref = None
    ctx = contexto if isinstance(contexto, dict) else {}
    ini_ctx = _ia_normalizar_data_iso_chat(str(ctx.get("data_inicio") or ""))
    fim_ctx = _ia_normalizar_data_iso_chat(str(ctx.get("data_fim") or ""))
    if ini_ctx:
        ano_ref = int(ini_ctx[:4])
    elif fim_ctx:
        ano_ref = int(fim_ctx[:4])
    else:
        ano_ref = dt.date.today().year

    padrao_mes = r"\b(JANEIRO|JAN|FEVEREIRO|FEV|MARCO|MAR|ABRIL|ABR|MAIO|MAI|JUNHO|JUN|JULHO|JUL|AGOSTO|AGO|SETEMBRO|SET|OUTUBRO|OUT|NOVEMBRO|NOV|DEZEMBRO|DEZ)\s*(?:DE|/|-)?\s*(20\d{2})?\b"
    for match in re.finditer(padrao_mes, texto):
        nome_mes = match.group(1)
        mes_num = meses_map.get(nome_mes)
        if not mes_num:
            continue
        ano = int(match.group(2) or ano_ref)
        encontrados.append(f"{ano:04d}-{mes_num:02d}")

    return list(dict.fromkeys(encontrados))


def _ia_extrair_periodo_mensagem_vendas(mensagem: str, contexto: Optional[dict] = None) -> tuple[str, str]:
    mensagem_raw = str(mensagem or "")
    texto = _normalizar_texto(mensagem_raw)

    padrao_data = r"(\d{4}[/-]\d{2}[/-]\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})"
    intervalo = re.search(rf"\bDE\s+{padrao_data}\s+(?:A|ATE|ATÉ|\-)\s+{padrao_data}\b", texto)
    if intervalo:
        d1 = _ia_parse_data_iso_flex(intervalo.group(1))
        d2 = _ia_parse_data_iso_flex(intervalo.group(2))
        if d1 and d2:
            return (d1, d2) if d1 <= d2 else (d2, d1)

    datas = re.findall(padrao_data, mensagem_raw)
    if len(datas) >= 2:
        d1 = _ia_parse_data_iso_flex(datas[0])
        d2 = _ia_parse_data_iso_flex(datas[1])
        if d1 and d2:
            return (d1, d2) if d1 <= d2 else (d2, d1)
    elif len(datas) == 1:
        d = _ia_parse_data_iso_flex(datas[0])
        if d:
            return d, d

    meses = {
        "JAN": 1, "JANEIRO": 1,
        "FEV": 2, "FEVEREIRO": 2,
        "MAR": 3, "MARCO": 3,
        "ABR": 4, "ABRIL": 4,
        "MAI": 5, "MAIO": 5,
        "JUN": 6, "JUNHO": 6,
        "JUL": 7, "JULHO": 7,
        "AGO": 8, "AGOSTO": 8,
        "SET": 9, "SETEMBRO": 9,
        "OUT": 10, "OUTUBRO": 10,
        "NOV": 11, "NOVEMBRO": 11,
        "DEZ": 12, "DEZEMBRO": 12,
    }
    match = re.search(r"\b(JANEIRO|JAN|FEVEREIRO|FEV|MARCO|MAR|ABRIL|ABR|MAIO|MAI|JUNHO|JUN|JULHO|JUL|AGOSTO|AGO|SETEMBRO|SET|OUTUBRO|OUT|NOVEMBRO|NOV|DEZEMBRO|DEZ)\s*(?:DE|/|-)?\s*(20\d{2})\b", texto)
    mes = ano = None
    if match:
        mes = meses.get(match.group(1))
        ano = int(match.group(2))
    if mes is None or ano is None:
        match_num = re.search(r"\b(0?[1-9]|1[0-2])[/-](20\d{2})\b", texto)
        if match_num:
            mes = int(match_num.group(1))
            ano = int(match_num.group(2))

    if mes and ano:
        data_ini = dt.date(ano, mes, 1)
        prox = dt.date(ano + 1, 1, 1) if mes == 12 else dt.date(ano, mes + 1, 1)
        data_fim = prox - dt.timedelta(days=1)
        return data_ini.isoformat(), data_fim.isoformat()

    ctx = contexto if isinstance(contexto, dict) else {}
    data_inicio = _ia_normalizar_data_iso_chat(str(ctx.get("data_inicio") or "").strip())
    data_fim = _ia_normalizar_data_iso_chat(str(ctx.get("data_fim") or "").strip())
    return _ia_normalizar_periodo_chat(data_inicio, data_fim)



def _comparison_year(value: Optional[str], fallback: Optional[int] = None) -> int:
    if value is None or str(value).strip() == "":
        return int(fallback or dt.date.today().year)
    year_text = str(value).strip()
    year = int(year_text)
    return 2000 + year if len(year_text) <= 2 else year


def _month_interval(year: int, month: int) -> tuple[str, str]:
    start = dt.date(year, month, 1)
    next_month = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    return start.isoformat(), (next_month - dt.timedelta(days=1)).isoformat()


def _previous_and_current_quarter() -> tuple[tuple[str, str], tuple[str, str]]:
    today = dt.date.today()
    current_quarter = ((today.month - 1) // 3) + 1
    previous_quarter = current_quarter - 1 or 4
    previous_year = today.year - (1 if current_quarter == 1 else 0)
    current_start_month = (current_quarter - 1) * 3 + 1
    previous_start_month = (previous_quarter - 1) * 3 + 1
    current_start = dt.date(today.year, current_start_month, 1)
    previous_start = dt.date(previous_year, previous_start_month, 1)
    current_end = (
        dt.date(today.year, current_start_month + 3, 1) - dt.timedelta(days=1)
        if current_quarter < 4 else dt.date(today.year, 12, 31)
    )
    previous_end = (
        dt.date(previous_year, previous_start_month + 3, 1) - dt.timedelta(days=1)
        if previous_quarter < 4 else dt.date(previous_year, 12, 31)
    )
    return (
        (previous_start.isoformat(), previous_end.isoformat()),
        (current_start.isoformat(), current_end.isoformat()),
    )


def _named_month_comparison(text: str) -> Optional[tuple[tuple[str, str], tuple[str, str]]]:
    months = {
        "JAN": 1, "JANEIRO": 1, "FEV": 2, "FEVEREIRO": 2,
        "MAR": 3, "MARCO": 3, "ABR": 4, "ABRIL": 4,
        "MAI": 5, "MAIO": 5, "JUN": 6, "JUNHO": 6,
        "JUL": 7, "JULHO": 7, "AGO": 8, "AGOSTO": 8,
        "SET": 9, "SETEMBRO": 9, "OUT": 10, "OUTUBRO": 10,
        "NOV": 11, "NOVEMBRO": 11, "DEZ": 12, "DEZEMBRO": 12,
    }
    matches = list(re.finditer(
        r"\b(JANEIRO|JAN|FEVEREIRO|FEV|MARCO|MAR|ABRIL|ABR|MAIO|MAI|JUNHO|JUN|JULHO|JUL|AGOSTO|AGO|SETEMBRO|SET|OUTUBRO|OUT|NOVEMBRO|NOV|DEZEMBRO|DEZ)\s*(?:DE|/|-)?\s*((?:20)?\d{2})?\b",
        text,
    ))
    if len(matches) < 2:
        return None
    first, second = matches[:2]
    default_year = dt.date.today().year
    first_year = _comparison_year(first.group(2), _comparison_year(second.group(2), default_year))
    second_year = _comparison_year(second.group(2), first_year)
    first_month, second_month = months.get(first.group(1)), months.get(second.group(1))
    if not first_month or not second_month:
        return None
    return _month_interval(first_year, first_month), _month_interval(second_year, second_month)


def _numeric_month_comparison(text: str) -> Optional[tuple[tuple[str, str], tuple[str, str]]]:
    pairs = re.findall(r"\b(0?[1-9]|1[0-2])\s*/\s*((?:20)?\d{2})\b", text)
    if len(pairs) < 2:
        return None
    (first_month, first_year), (second_month, second_year) = pairs[:2]
    year_a = _comparison_year(first_year)
    year_b = _comparison_year(second_year, year_a)
    return _month_interval(year_a, int(first_month)), _month_interval(year_b, int(second_month))


def _ia_extrair_periodos_comparacao(
    mensagem: str,
    contexto: Optional[dict] = None,
) -> Optional[tuple[tuple[str, str], tuple[str, str]]]:
    raw_message = str(mensagem or "")
    text = _normalizar_texto(raw_message)
    if not text:
        return None
    if "TRIMESTRE" in text and any(key in text for key in ("ANTERIOR", "PASSADO")):
        return _previous_and_current_quarter()

    dates = re.findall(r"(\d{4}[/-]\d{2}[/-]\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})", raw_message)
    if len(dates) >= 4:
        parsed = [_ia_parse_data_iso_flex(value) for value in dates[:4]]
        if all(parsed):
            first = tuple(sorted(parsed[:2]))
            second = tuple(sorted(parsed[2:]))
            return first, second

    month_comparison = _named_month_comparison(text) or _numeric_month_comparison(text)
    if month_comparison:
        return month_comparison
    ctx = contexto if isinstance(contexto, dict) else {}
    explicit = tuple(str(ctx.get(key) or "").strip() for key in (
        "data_inicio_a", "data_fim_a", "data_inicio_b", "data_fim_b"
    ))
    if all(explicit):
        return (explicit[0], explicit[1]), (explicit[2], explicit[3])

    current_start, current_end = _ia_extrair_periodo_mensagem_vendas(mensagem, contexto)
    if not current_start or not current_end:
        return None
    try:
        start_date = dt.date.fromisoformat(current_start)
        end_date = dt.date.fromisoformat(current_end)
        if end_date < start_date:
            return None
        days = (end_date - start_date).days + 1
        previous_end = start_date - dt.timedelta(days=1)
        previous_start = previous_end - dt.timedelta(days=days - 1)
        return (
            (previous_start.isoformat(), previous_end.isoformat()),
            (start_date.isoformat(), end_date.isoformat()),
        )
    except Exception:
        return None


def _ia_resolver_loja_mensagem_vendas(client_id: str, mensagem: str, contexto: Optional[dict] = None) -> Optional[str]:
    texto = _normalizar_texto(mensagem or "")
    tenant_path = get_tenant_path(client_id)
    candidatos: list[tuple[str, str]] = []

    if os.path.exists(tenant_path):
        for nome in sorted(os.listdir(tenant_path)):
            if not nome.startswith("vendas_historico_") or not nome.endswith(".db") or ".backup_" in nome:
                continue
            slug = nome[len("vendas_historico_"):-3]
            if not slug:
                continue
            candidatos.append((slug.replace("_", " "), _normalizar_texto(slug.replace("_", " "))))

    for nome_loja, nome_norm in candidatos:
        if nome_norm and nome_norm in texto:
            return nome_loja

    ctx = contexto if isinstance(contexto, dict) else {}
    loja_ctx = str(ctx.get("loja") or "").strip()
    if loja_ctx and loja_ctx not in ("__todas", "Todas as lojas"):
        return loja_ctx
    return None


__all__ = [
    "_ia_tool_float",
    "_ia_tool_resolver_sku",
    "_ia_extrair_mes_ano_mensagem",
    "_ia_extrair_meses_ano_mensagem",
    "_ia_extrair_periodo_mensagem_vendas",
    "_ia_extrair_periodos_comparacao",
    "_ia_resolver_loja_mensagem_vendas",
]
