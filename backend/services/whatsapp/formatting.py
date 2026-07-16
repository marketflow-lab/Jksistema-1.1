"""Formatação pura de mensagens e relatórios para WhatsApp.

O módulo não acessa rede, disco, configuração ou estado global do bridge. Os
nomes legados são mantidos durante a migração para que a fachada possa delegar
sem alterar o contrato já usado pelos testes e consumidores internos.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional


WHATSAPP_PART_BODY_CHARS = 2600
WHATSAPP_MAX_PARTS = 8
WHATSAPP_EXACT_ORDER_HISTORY_MARKER = "<!-- JK_EXACT_ORDER_FULL_HISTORY -->"
WHATSAPP_REPORT_MAX_PARTS = 30
WHATSAPP_REPORT_BODY_CHARS = 2100
WHATSAPP_REPORT_RANKING_ITEMS_PER_PART = 8


def _whatsapp_table_blocks_to_mobile(text: str) -> str:
    lines = str(text or "").splitlines()
    output: list[str] = []
    index = 0
    separator_re = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")

    def cells(line: str) -> list[str]:
        return [item.strip() for item in line.strip().strip("|").split("|")]

    while index < len(lines):
        if index + 1 < len(lines) and "|" in lines[index] and separator_re.match(lines[index + 1] or ""):
            headers = cells(lines[index])
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(cells(lines[index]))
                index += 1
            for row in rows:
                fields = []
                for pos, value in enumerate(row):
                    if not value:
                        continue
                    label = headers[pos] if pos < len(headers) and headers[pos] else f"Campo {pos + 1}"
                    fields.append(f"*{label}:* {value}")
                if fields:
                    output.append("• " + "\n  ".join(fields))
            continue
        output.append(lines[index])
        index += 1
    return "\n".join(output)


def _whatsapp_clean_markdown(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    text = text.replace(WHATSAPP_EXACT_ORDER_HISTORY_MARKER, "").strip()
    text = _whatsapp_table_blocks_to_mobile(text)
    text = re.sub(r"(?m)^\s*#{1,6}\s+(.+?)\s*$", r"*\1*", text)
    text = re.sub(r"\*\*([^\n*]+?)\*\*", r"*\1*", text)
    text = re.sub(r"__([^\n_]+?)__", r"*\1*", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1: \2", text)
    text = re.sub(r"(?m)^\s*[-+]\s+", "• ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _whatsapp_text_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).strip().lower()
    return re.sub(r"\s+", " ", text)


def _whatsapp_daily_sales_report_requested(value: Any) -> bool:
    text = _whatsapp_text_key(value)
    return bool(
        re.search(r"\b(relatorio|resumo)\b", text)
        and re.search(r"\b(do dia|diario|diaria|de hoje|hoje)\b", text)
    )


def _whatsapp_sales_report_requested(value: Any) -> bool:
    text = _whatsapp_text_key(value)
    return bool(
        _whatsapp_daily_sales_report_requested(value)
        or (
            re.search(r"\b(relatorio|resumo|analise)\b", text)
            and re.search(r"\b(venda|vendas|vendido|vendidos|faturamento|pedido|pedidos|mercado livre|mercadolivre)\b", text)
        )
    )


def _whatsapp_plain_inline(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\*+|\*+$", "", text).strip()
    text = re.sub(r"^`+|`+$", "", text).strip()
    return re.sub(r"\s+", " ", text)


def _whatsapp_field(line: Any) -> tuple[str, str]:
    text = str(line or "").strip()
    text = re.sub(r"^(?:[•*-]|\d+[.)])\s*", "", text).strip()
    match = re.match(r"^\*([^*:\n]{1,60}?):\*\s*(.+?)\s*$", text)
    if not match:
        match = re.match(r"^([^:\n]{1,60}?):\s*(.+?)\s*$", text)
    if not match:
        return "", ""
    return _whatsapp_plain_inline(match.group(1)), _whatsapp_plain_inline(match.group(2))


def _whatsapp_heading_value(line: Any) -> str:
    text = str(line or "").strip()
    match = re.match(r"^\*([^*\n]{2,90})\*$", text)
    if match:
        return _whatsapp_plain_inline(match.group(1)).rstrip(":").strip()
    if len(text) <= 70 and not re.search(r"[.!?]$", text):
        key = _whatsapp_text_key(text.rstrip(":"))
        known = (
            "dados principais",
            "indicadores",
            "resumo",
            "resumo executivo",
            "skus mais vendidos",
            "produtos mais vendidos",
            "mais vendidos",
            "ranking",
            "analise",
            "insights",
            "conclusao",
            "proximas acoes",
            "recomendacoes",
            "fontes",
            "fontes e cobertura",
            "onde consultou",
            "qualidade dos dados",
            "avisos",
        )
        if key in known:
            return text.rstrip(":").strip()
    return ""


def _whatsapp_report_section_kind(heading: Any) -> str:
    key = _whatsapp_text_key(heading)
    if not key:
        return ""
    if key.startswith("relatorio"):
        return "report_title"
    if any(token in key for token in ("mais vendido", "skus vendidos", "sku vendidos", "ranking", "top sku", "top produto")):
        return "ranking"
    if any(token in key for token in ("fonte", "onde consult", "cobertura", "qualidade dos dados", "aviso")):
        return "sources"
    if any(token in key for token in ("analise", "insight", "conclusao", "proxima acao", "proximas acoes", "recomend")):
        return "analysis"
    if any(token in key for token in ("dados principais", "indicadores", "resumo", "visao geral")):
        return "summary"
    return ""


def _whatsapp_report_sections(value: Any) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"summary": [], "ranking": [], "analysis": [], "sources": []}
    current = "summary"
    for raw_line in _whatsapp_clean_markdown(value).splitlines():
        line = raw_line.rstrip()
        field_label, field_value = _whatsapp_field(line)
        field_kind = _whatsapp_report_section_kind(field_label)
        if field_value and field_kind in {"analysis", "sources"}:
            current = field_kind
            sections[current].append(f"{field_label}: {field_value}")
            continue
        heading = _whatsapp_heading_value(line)
        kind = _whatsapp_report_section_kind(heading)
        if kind == "report_title":
            if heading:
                sections["summary"].append(f"*{heading}*")
            current = "summary"
            continue
        if kind in sections:
            current = kind
            continue
        sections[current].append(line)
    for key, lines in sections.items():
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        sections[key] = lines
    return sections


def _whatsapp_metric_label(value: Any) -> str:
    label = _whatsapp_plain_inline(value)
    key = _whatsapp_text_key(label)
    aliases = (
        (("quantidade vendida", "itens vendidos", "unidades vendidas"), "Itens vendidos"),
        (("faturamento bruto", "faturamento total", "faturamento"), "Faturamento"),
        (("resultado apos devolucoes", "receita liquida", "resultado liquido"), "Receita líquida"),
        (("valor devolvido", "valor de devolucoes"), "Valor devolvido"),
        (("ticket medio",), "Ticket médio"),
        (("devolucoes", "itens devolvidos"), "Devoluções"),
        (("quantidade de pedidos", "pedidos"), "Pedidos"),
    )
    for names, compact in aliases:
        if key in names:
            return compact
    return label


def _whatsapp_number(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if number.is_integer():
        return f"{int(number):,}".replace(",", ".")
    return f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _whatsapp_money(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return "R$ " + f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _whatsapp_report_date(value: Any) -> str:
    raw = str(value or "").strip()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    return f"{match.group(3)}/{match.group(2)}/{match.group(1)}" if match else raw


def _whatsapp_daily_ml_sales_report(
    tool_results: list[dict[str, Any]],
    query_policy: dict[str, Any],
    request_text: Any,
) -> str:
    """Monta o relatorio diario diretamente do agregado da API do Mercado Livre."""
    if not _whatsapp_sales_report_requested(request_text):
        return ""
    if str(query_policy.get("store_mode") or "") == "all":
        return ""
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    if "mercado_livre_orders" not in (source_policy.get("required_tools") or []):
        return ""
    result = next(
        (
            item for item in tool_results
            if isinstance(item, dict) and str(item.get("tool_id") or "") == "mercado_livre_orders"
        ),
        None,
    )
    if not isinstance(result, dict) or result.get("success") is not True:
        return ""

    summaries = [item for item in (result.get("summary") or []) if isinstance(item, dict)]
    primary = next(
        (item for item in summaries if str(item.get("tool_id") or "") == "mercado_livre_orders"),
        summaries[0] if summaries else {},
    )
    data = primary.get("summary") if isinstance(primary.get("summary"), dict) else {}
    if data.get("api_consulted") is False or data.get("error"):
        return ""
    totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
    rows = [row for row in (result.get("all_rows") or result.get("top_rows") or []) if isinstance(row, dict)]
    rows = [
        row for row in rows
        if str(
            row.get("sku")
            or row.get("seller_sku")
            or row.get("item_id")
            or row.get("mlb")
            or row.get("id")
            or row.get("title")
            or ""
        ).strip()
    ]

    period = data.get("period") if isinstance(data.get("period"), dict) else {}
    requested_period = period.get("requested") if isinstance(period.get("requested"), dict) else {}
    start = _whatsapp_report_date(requested_period.get("from") or (primary.get("periodo") or {}).get("data_inicio"))
    end = _whatsapp_report_date(requested_period.get("to") or (primary.get("periodo") or {}).get("data_fim"))
    period_label = start if start and start == end else f"{start} a {end}".strip(" a")
    store = str(query_policy.get("store") or primary.get("loja") or data.get("store") or "").strip()

    lines = [f"# Relatório de vendas — {store or 'Mercado Livre'}"]
    if period_label:
        lines.append(f"Período: {period_label}")
    if store:
        lines.append(f"Loja: {store}")
    lines.extend([
        "",
        "## Dados principais",
        f"- **Pedidos:** {_whatsapp_number(totals.get('orders'))}",
        f"- **Itens vendidos:** {_whatsapp_number(totals.get('items_quantity'))}",
        f"- **Valor bruto:** {_whatsapp_money(totals.get('gross_amount'))}",
        f"- **Valor pago:** {_whatsapp_money(totals.get('paid_amount'))}",
    ])
    if totals.get("refund_amount") is None:
        lines.append("- **Estornos:** indisponível")
        lines.append("- **Valor líquido:** indisponível")
    else:
        lines.append(f"- **Estornos:** {_whatsapp_money(totals.get('refund_amount'))}")
        lines.append(f"- **Valor líquido:** {_whatsapp_money(totals.get('net_amount'))}")

    lines.extend(["", "## SKUs vendidos"])
    if rows:
        for row in rows:
            sku = _whatsapp_plain_inline(
                row.get("sku")
                or row.get("seller_sku")
                or row.get("item_id")
                or row.get("mlb")
                or row.get("id")
                or "não informado"
            )
            title = _whatsapp_plain_inline(row.get("title") or row.get("produto") or row.get("nome") or "Produto sem título")
            quantity = float(row.get("quantity") or 0)
            gross = float(row.get("gross_amount") or 0)
            unit = gross / quantity if quantity > 0 else 0.0
            lines.extend([
                f"- **SKU:** {sku}",
                f"  **Produto:** {title}",
                f"  **Qtd.:** {_whatsapp_number(quantity)}",
                f"  **Valor unitário médio:** {_whatsapp_money(unit)}",
                f"  **Total vendido:** {_whatsapp_money(gross)}",
            ])
    else:
        lines.append("Nenhum SKU vendido no período consultado.")

    coverage_complete = (
        data.get("coverage_complete") is not False
        and not bool(data.get("truncated"))
        and not bool(paging.get("has_more"))
    )
    pages = int(paging.get("pages_fetched") or 0)
    scanned_orders = int(paging.get("scanned") or paging.get("returned") or totals.get("orders") or 0)
    considered_orders = int(totals.get("orders") or paging.get("returned") or 0)
    lines.extend([
        "",
        "## Fontes e cobertura",
        (
            f"Consulta direta à API do Mercado Livre da loja {store or 'selecionada'}, "
            f"no período {period_label or 'informado'}, com {pages} página(s), "
            f"{scanned_orders} pedido(s) verificado(s), {considered_orders} pedido(s) considerado(s) "
            f"e {len(rows)} SKU(s) consolidado(s)."
        ),
    ])
    if coverage_complete:
        lines.append("Cobertura completa para o período informado.")
    else:
        lines.append("A API indicou mais registros além da cobertura recebida; o relatório está incompleto e não estimou valores.")
    return "\n".join(lines).strip()


def _whatsapp_report_summary(lines: list[str]) -> str:
    report_name = ""
    metadata: list[tuple[str, str]] = []
    metrics: list[tuple[str, str]] = []
    notes: list[str] = []
    metadata_keys = {"periodo", "conta", "loja", "filtro", "filtros"}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        heading = _whatsapp_heading_value(stripped)
        if heading and _whatsapp_report_section_kind(heading) == "report_title":
            report_name = re.sub(r"(?i)^relat[oó]rio\s+(?:de\s+)?", "", heading).strip(" —-") or heading
            if report_name:
                report_name = report_name[:1].upper() + report_name[1:]
            continue
        label, value = _whatsapp_field(stripped)
        if label and value:
            key = _whatsapp_text_key(label)
            if key in metadata_keys:
                metadata.append((key, value))
            else:
                metrics.append((_whatsapp_metric_label(label), value))
            continue
        notes.append(stripped)

    output: list[str] = []
    if report_name:
        output.append(f"*{report_name}*")
    icons = {"periodo": "🗓", "conta": "🏪", "loja": "🏪", "filtro": "🔎", "filtros": "🔎"}
    for key, value in metadata:
        output.append(f"{icons.get(key, '•')} {value}")
    if metrics:
        output.append("\n*📌 INDICADORES*")
        output.extend(f"*{label}:* {value}" for label, value in metrics)
    if notes:
        output.append("\n".join(notes))
    return "\n".join(output).strip()


def _whatsapp_ranking_items(lines: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None

    def new_item(sku: str = "") -> dict[str, Any]:
        return {"sku": sku, "product": "", "quantity": "", "unit_value": "", "value": "", "extra": []}

    def append_current(*, discard_orphan: bool = False) -> None:
        nonlocal current
        if not current:
            return
        if not discard_orphan or str(current.get("sku") or "").strip():
            items.append(current)
        current = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        sku_heading = _whatsapp_plain_inline(stripped)
        sku_heading = re.sub(r"^(?:â€¢|•|[-+])\s*", "", sku_heading).strip()
        sku_match = re.match(
            r"^(?:\d{1,4}\s*[.)-]\s*)?SKU\s*(?:[:#-]\s*)?(.+?)\s*$",
            sku_heading,
            flags=re.IGNORECASE,
        )
        if sku_match:
            append_current(discard_orphan=True)
            current = new_item(_whatsapp_plain_inline(sku_match.group(1)))
            continue

        label, value = _whatsapp_field(stripped)
        key = _whatsapp_text_key(label)
        if label and value and key in {"sku", "codigo", "codigo sku", "produto sku"}:
            append_current(discard_orphan=True)
            current = new_item(value)
            continue
        if current is None:
            current = new_item()
        if label and value:
            if key in {"produto", "descricao", "nome", "titulo"}:
                current["product"] = value
            elif key in {"qtd", "qtde", "quantidade", "unidades", "quantidade vendida"}:
                current["quantity"] = value
            elif key in {"valor unitario", "valor unitario medio", "preco unitario", "preco medio"}:
                current["unit_value"] = value
            elif key in {"valor", "valor vendido", "total vendido", "faturamento", "total", "receita"}:
                current["value"] = value
            else:
                current["extra"].append(f"{label}: {value}")
        else:
            plain = _whatsapp_plain_inline(stripped)
            if current.get("sku") and not current.get("product") and plain:
                current["product"] = plain
            else:
                current["extra"].append(stripped)
    if current:
        items.append(current)
    return [item for item in items if any(item.get(field) for field in ("sku", "product", "quantity", "value", "extra"))]


def _whatsapp_rank_marker(index: int) -> str:
    markers = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
    return markers[index - 1] if 1 <= index <= len(markers) else f"{index}."


def _whatsapp_report_ranking_parts(lines: list[str]) -> list[str]:
    items = _whatsapp_ranking_items(lines)
    if not items:
        fallback = "\n".join(lines).strip()
        return [f"*📦 SKUs VENDIDOS*\n\n{fallback}"] if fallback else []

    cards: list[str] = []
    for index, item in enumerate(items, 1):
        sku = _whatsapp_plain_inline(item.get("sku") or "não informado")
        product = _whatsapp_plain_inline(item.get("product"))
        card = [f"{_whatsapp_rank_marker(index)} *SKU {sku}*"]
        if product:
            card.append(product)
        details = []
        if item.get("quantity"):
            details.append(f"Qtd. {_whatsapp_plain_inline(item['quantity'])}")
        if item.get("unit_value"):
            details.append(f"Unit. {_whatsapp_plain_inline(item['unit_value'])}")
        if item.get("value"):
            details.append(f"Total {_whatsapp_plain_inline(item['value'])}")
        if details:
            card.append("`" + "  |  ".join(details) + "`")
        card.extend(_whatsapp_plain_inline(value) for value in item.get("extra") or [] if _whatsapp_plain_inline(value))
        cards.append("\n".join(card))

    parts: list[str] = []
    current: list[str] = []
    for card in cards:
        candidate = "\n\n".join(current + [card])
        if current and (
            len(current) >= WHATSAPP_REPORT_RANKING_ITEMS_PER_PART
            or len(candidate) > WHATSAPP_REPORT_BODY_CHARS - 80
        ):
            parts.append("*📦 SKUs VENDIDOS*\n\n" + "\n\n".join(current))
            current = [card]
        else:
            current.append(card)
    if current:
        parts.append("*📦 SKUs VENDIDOS*\n\n" + "\n\n".join(current))
    return parts


def _whatsapp_report_text_section(title: str, lines: list[str]) -> list[str]:
    body = "\n".join(lines).strip()
    if not body:
        return []
    return [f"*{title}*\n\n{part}" for part in _whatsapp_split_body(body, WHATSAPP_REPORT_BODY_CHARS - 80)]


def _whatsapp_report_response_parts(value: Any, title: str) -> list[str]:
    sections = _whatsapp_report_sections(value)
    semantic_parts: list[tuple[str, str]] = []
    summary = _whatsapp_report_summary(sections["summary"])
    if summary:
        semantic_parts.append(("Resumo", summary))
    semantic_parts.extend(("SKUs vendidos", part) for part in _whatsapp_report_ranking_parts(sections["ranking"]))
    semantic_parts.extend(("Análise", part) for part in _whatsapp_report_text_section("🔎 ANÁLISE", sections["analysis"]))
    semantic_parts.extend(("Fontes", part) for part in _whatsapp_report_text_section("🧾 FONTES E COBERTURA", sections["sources"]))
    if not semantic_parts:
        semantic_parts = [("Resumo", part) for part in _whatsapp_split_body(value, WHATSAPP_REPORT_BODY_CHARS)]
    if len(semantic_parts) > WHATSAPP_REPORT_MAX_PARTS:
        semantic_parts = semantic_parts[:WHATSAPP_REPORT_MAX_PARTS]
        label, last = semantic_parts[-1]
        semantic_parts[-1] = (
            label,
            last[: WHATSAPP_REPORT_BODY_CHARS - 100].rstrip()
            + "\n\n_Conteúdo adicional disponível no histórico do Black John._",
        )

    total = len(semantic_parts)
    result: list[str] = []
    for index, (label, body) in enumerate(semantic_parts, 1):
        counter = f" • {index} de {total}" if total > 1 else ""
        heading = "*📊 Relatório*\n" if index == 1 else ""
        result.append(f"{heading}_{label}{counter}_\n{body}")
    return result


def _whatsapp_split_body(
    value: Any,
    limit: int = WHATSAPP_PART_BODY_CHARS,
    max_parts: Optional[int] = WHATSAPP_MAX_PARTS,
) -> list[str]:
    text = _whatsapp_clean_markdown(value)
    if not text:
        return []
    units: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= limit:
            units.append(paragraph)
            continue
        current_lines: list[str] = []
        current_size = 0
        for line in paragraph.splitlines() or [paragraph]:
            line = line.strip()
            while len(line) > limit:
                if current_lines:
                    units.append("\n".join(current_lines))
                    current_lines, current_size = [], 0
                cut = line.rfind(" ", 0, limit)
                cut = cut if cut >= max(200, limit // 2) else limit
                units.append(line[:cut].rstrip())
                line = line[cut:].lstrip()
            projected = current_size + (1 if current_lines else 0) + len(line)
            if current_lines and projected > limit:
                units.append("\n".join(current_lines))
                current_lines, current_size = [], 0
            if line:
                current_lines.append(line)
                current_size += (1 if current_size else 0) + len(line)
        if current_lines:
            units.append("\n".join(current_lines))

    parts: list[str] = []
    current = ""
    for unit in units:
        candidate = unit if not current else f"{current}\n\n{unit}"
        if current and len(candidate) > limit:
            parts.append(current)
            current = unit
        else:
            current = candidate
    if current:
        parts.append(current)
    if max_parts is not None and max_parts > 0 and len(parts) > max_parts:
        kept = parts[:max_parts]
        kept[-1] = kept[-1][: max(1, limit - 110)].rstrip() + "\n\n_Conteudo adicional disponivel no historico do Black John._"
        parts = kept
    return parts


def _whatsapp_operational_title(title: Any) -> bool:
    key = _whatsapp_text_key(title)
    return bool(
        re.search(
            r"\b(confirmacao|aprovar|aprovacao|autorizada|negada|rejeitada|bloqueado|acesso negado|"
            r"nao concluido|erro|falha|codigo|decisao|expirada|seguranca|aguardando janela|somente consulta)\b",
            key,
        )
    )


def _whatsapp_response_parts(value: Any, title: str) -> list[str]:
    if WHATSAPP_EXACT_ORDER_HISTORY_MARKER in str(value or ""):
        body_parts = _whatsapp_split_body(value, WHATSAPP_PART_BODY_CHARS, max_parts=None) or ["Sem detalhes adicionais."]
        total = len(body_parts)
        return [
            f"_Parte {index} de {total}_\n\n{body}".strip() if total > 1 else body
            for index, body in enumerate(body_parts, 1)
        ]
    if "relatorio" in _whatsapp_text_key(title):
        return _whatsapp_report_response_parts(value, title)
    body_parts = _whatsapp_split_body(value) or ["Sem detalhes adicionais."]
    total = len(body_parts)
    result: list[str] = []
    show_title = _whatsapp_operational_title(title)
    for index, body in enumerate(body_parts, 1):
        prefix = f"*{title}*\n\n" if show_title and index == 1 else ""
        counter = f"_Parte {index} de {total}_\n\n" if total > 1 else ""
        result.append(f"{prefix}{counter}{body}".strip())
    return result


def _whatsapp_result_title(prompt: Any, status: str = "completed") -> str:
    prompt_text = str(prompt or "").lower()
    if status == "completed" and re.search(r"\b(relat[oó]rio|an[aá]lise|diagn[oó]stico)\b", prompt_text):
        return "📊 BLACK JOHN — RELATÓRIO"
    if status == "completed":
        return "✅ BLACK JOHN — RESULTADO"
    if status == "awaiting_approval":
        return "🔐 BLACK JOHN — CONFIRMAÇÃO"
    return "⚠️ BLACK JOHN — NÃO CONCLUÍDO"


__all__ = [
    "_whatsapp_clean_markdown",
    "_whatsapp_daily_ml_sales_report",
    "_whatsapp_daily_sales_report_requested",
    "_whatsapp_field",
    "_whatsapp_heading_value",
    "_whatsapp_metric_label",
    "_whatsapp_money",
    "_whatsapp_number",
    "_whatsapp_operational_title",
    "_whatsapp_plain_inline",
    "_whatsapp_rank_marker",
    "_whatsapp_ranking_items",
    "_whatsapp_report_date",
    "_whatsapp_report_ranking_parts",
    "_whatsapp_report_response_parts",
    "_whatsapp_report_section_kind",
    "_whatsapp_report_sections",
    "_whatsapp_report_summary",
    "_whatsapp_report_text_section",
    "_whatsapp_response_parts",
    "_whatsapp_result_title",
    "_whatsapp_sales_report_requested",
    "_whatsapp_split_body",
    "_whatsapp_table_blocks_to_mobile",
    "_whatsapp_text_key",
]
