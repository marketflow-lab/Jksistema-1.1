from __future__ import annotations

import hashlib
import math
import re
import unicodedata
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from PIL import Image, ImageDraw, ImageFont


CANVAS_SIZE = (1080, 1350)
MAX_IMAGES = 2
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_SERIES_ROWS = 5000
MAX_RANKING_ROWS = 20000
MAX_CATEGORY_ROWS = 200
MAX_STORES = 12
MAX_KPIS = 12
MAX_LABEL_CHARS = 240

BACKGROUND = "#07111d"
PANEL = "#101e2d"
PANEL_ALT = "#142638"
TEXT = "#f5f8fb"
MUTED = "#aebdcc"
GRID = "#2a4055"
ACCENT = "#43d3c7"
BLUE = "#5ba8ff"
GREEN = "#55d98b"
YELLOW = "#ffc857"
ORANGE = "#ff9f43"
RED = "#ff6b6b"
PURPLE = "#aa8cff"
SERIES_COLORS = (ACCENT, BLUE, YELLOW, GREEN, PURPLE, ORANGE, RED)

_FONT_CANDIDATES = {
    "regular": (
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
    "bold": (
        "C:/Windows/Fonts/seguisb.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
}

_SENSITIVE_FIELD_RE = re.compile(
    r"(?i)\b(?:cpf|cnpj|documento|telefone|celular|whatsapp|e-?mail|endereco|endere[cç]o|"
    r"comprador|buyer|authorization|access[_ -]?token|refresh[_ -]?token|token)\b\s*[:=\-]\s*[^\n|;,]+"
)
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b")
_DOCUMENT_RE = re.compile(r"(?<!\d)(?:\d{3}[.\s-]?\d{3}[.\s-]?\d{3}[-.\s]?\d{2}|\d{2}[.\s-]?\d{3}[.\s-]?\d{3}[/\s-]?\d{4}[-.\s]?\d{2})(?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?55[\s.-]?)?(?:\(?\d{2}\)?[\s.-]?)9?\d{4}[\s.-]?\d{4}(?!\d)")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/=]{12,}")

_METRIC_ALIASES = {
    "orders": ("orders", "order_count", "pedidos", "pedidos_pagos", "quantidade_pedidos"),
    "items": ("items", "item_count", "itens", "itens_vendidos", "quantidade", "quantity", "qtd"),
    "gross": ("gross", "gross_value", "valor_bruto", "faturamento", "revenue", "value", "valor", "total"),
    "paid": ("paid", "paid_value", "valor_pago"),
    "refunds": ("refunds", "refund_value", "estornos", "reembolsos", "valor_estornado"),
    "net": ("net", "net_value", "valor_liquido", "liquido", "resultado_liquido"),
}

_KPI_DISPLAY_LABELS = {
    "orders": "Pedidos",
    "order_count": "Pedidos",
    "items": "Itens",
    "item_count": "Itens",
    "gross": "Valor bruto",
    "gross_value": "Valor bruto",
    "paid": "Valor pago",
    "paid_value": "Valor pago",
    "refunds": "Estornos",
    "refund_value": "Estornos",
    "net": "Valor líquido",
    "net_value": "Valor líquido",
    "stores": "Lojas",
    "skus": "SKUs",
    "listings": "Anúncios",
    "active": "Ativos",
    "alerts": "Alertas",
    "critical": "Críticos",
    "attention": "Atenção",
}

_KPI_LABELS = {
    "orders": "Pedidos",
    "items": "Itens vendidos",
    "gross": "Valor bruto",
    "paid": "Valor pago",
    "refunds": "Estornos",
    "net": "Valor líquido",
    "stores": "Lojas",
    "skus": "SKUs",
    "listings": "Anúncios",
    "active": "Ativos",
    "alerts": "Alertas",
    "critical": "Críticos",
    "attention": "Atenção",
}


def _normalized_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _safe_text(value: Any, *, field: str = "texto", maximum: int = MAX_LABEL_CHARS) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = re.sub(r"[\r\t]+", " ", text)
    text = re.sub(r" {2,}", " ", text)
    text = _BEARER_RE.sub("[dado protegido]", text)
    text = _EMAIL_RE.sub("[dado protegido]", text)
    text = _DOCUMENT_RE.sub("[dado protegido]", text)
    text = _PHONE_RE.sub("[dado protegido]", text)
    text = _SENSITIVE_FIELD_RE.sub("[dado protegido]", text)
    if len(text) > maximum:
        raise ValueError(f"chart_data_{field}_too_long")
    return text


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[^0-9,\.\-+]", "", text)
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _format_number(value: Any, decimals: int = 0) -> str:
    number = _as_float(value) or 0.0
    rendered = f"{number:,.{decimals}f}"
    return rendered.replace(",", "X").replace(".", ",").replace("X", ".")


def _format_compact(value: Any, *, money: bool = False) -> str:
    number = _as_float(value) or 0.0
    absolute = abs(number)
    if absolute >= 1_000_000_000:
        value_text = f"{number / 1_000_000_000:.1f}".replace(".", ",") + " bi"
    elif absolute >= 1_000_000:
        value_text = f"{number / 1_000_000:.1f}".replace(".", ",") + " mi"
    elif absolute >= 10_000:
        value_text = f"{number / 1_000:.1f}".replace(".", ",") + " mil"
    elif float(number).is_integer():
        value_text = _format_number(number)
    else:
        value_text = _format_number(number, 2)
    return f"R$ {value_text}" if money else value_text


def _format_money(value: Any) -> str:
    return f"R$ {_format_number(value, 2)}"


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES["bold" if bold else "regular"]:
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.ImageFont) -> float:
    return float(draw.textlength(value, font=font))


def _wrap_text(draw: ImageDraw.ImageDraw, value: Any, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    text = str(value or "")
    if not text:
        return [""]
    output: list[str] = []
    for paragraph in text.splitlines() or [text]:
        words = paragraph.split(" ")
        line = ""
        for word in words:
            candidate = word if not line else f"{line} {word}"
            if _text_width(draw, candidate, font) <= max_width:
                line = candidate
                continue
            if line:
                output.append(line)
                line = ""
            if _text_width(draw, word, font) <= max_width:
                line = word
                continue
            fragment = ""
            for char in word:
                candidate_fragment = fragment + char
                if fragment and _text_width(draw, candidate_fragment, font) > max_width:
                    output.append(fragment)
                    fragment = char
                else:
                    fragment = candidate_fragment
            line = fragment
        if line or not words:
            output.append(line)
    return output or [""]


def _draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    value: Any,
    font: ImageFont.ImageFont,
    fill: str,
    max_width: int,
    *,
    spacing: int = 5,
) -> int:
    lines = _wrap_text(draw, value, font, max_width)
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    line_height = max(1, bbox[3] - bbox[1])
    draw.multiline_text(xy, "\n".join(lines), font=font, fill=fill, spacing=spacing)
    return len(lines) * line_height + max(0, len(lines) - 1) * spacing


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:10]
    for pattern in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(normalized, pattern).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _row_metric(row: dict[str, Any], metric: str) -> Optional[float]:
    normalized = {_normalized_key(key): value for key, value in row.items()}
    for alias in _METRIC_ALIASES[metric]:
        if alias in normalized:
            return _as_float(normalized[alias])
    return None


def _row_label(row: dict[str, Any], *keys: str, default: str = "") -> str:
    normalized = {_normalized_key(key): value for key, value in row.items()}
    for key in keys:
        value = normalized.get(_normalized_key(key))
        if value not in (None, ""):
            return _safe_text(value, field="label")
    return default


def _canonical_kpis(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        source: Iterable[Any] = ({"label": key, "value": value} for key, value in raw.items())
    elif isinstance(raw, list):
        source = raw
    else:
        source = []
    for item in source:
        if len(rows) >= MAX_KPIS:
            break
        if not isinstance(item, dict):
            continue
        label = _row_label(item, "label", "name", "nome", "metric", default="Indicador")
        label = _KPI_DISPLAY_LABELS.get(_normalized_key(label), label)
        label = _KPI_LABELS.get(_normalized_key(label), label)
        value = item.get("value", item.get("valor", item.get("total", "")))
        numeric = _as_float(value)
        rendered = _safe_text(value, field="kpi_value", maximum=80) if numeric is None else value
        rows.append({"label": label, "value": rendered, "numeric": numeric})
    return rows


def _canonical_rows(raw: Any, *, limit: int, field: str) -> list[dict[str, Any]]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError(f"chart_data_{field}_must_be_list")
    if len(raw) > limit:
        raise ValueError(f"chart_data_{field}_too_many_rows")
    return [dict(row) for row in raw if isinstance(row, dict)]


def _validate_and_normalize(chart_data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(chart_data, dict):
        raise TypeError("chart_data_must_be_dict")
    analysis_type = _normalized_key(chart_data.get("analysis_type") or "analysis")
    title = _safe_text(chart_data.get("title") or "Análise do Black Jhon", field="title")
    source = _safe_text(chart_data.get("source") or "JK Sistema", field="source", maximum=160)
    period_start = _parse_date(chart_data.get("period_start"))
    period_end = _parse_date(chart_data.get("period_end"))
    if period_start and period_end and period_start > period_end:
        raise ValueError("chart_data_invalid_period")

    stores_raw = chart_data.get("stores") or []
    if isinstance(stores_raw, (str, dict)):
        stores_raw = [stores_raw]
    if not isinstance(stores_raw, list):
        raise ValueError("chart_data_stores_must_be_list")
    if len(stores_raw) > MAX_STORES:
        raise ValueError("chart_data_too_many_stores")
    stores: list[Any] = []
    for store in stores_raw:
        if isinstance(store, dict):
            clean = dict(store)
            clean["name"] = _row_label(clean, "name", "store", "loja", "conta", default="Loja")
            stores.append(clean)
        else:
            stores.append(_safe_text(store, field="store", maximum=120))

    normalized = {
        "analysis_type": analysis_type,
        "title": title,
        "source": source,
        "period_start": period_start,
        "period_end": period_end,
        "stores": stores,
        "coverage_complete": bool(chart_data.get("coverage_complete", True)),
        "kpis": _canonical_kpis(chart_data.get("kpis")),
        "series": _canonical_rows(chart_data.get("series"), limit=MAX_SERIES_ROWS, field="series"),
        "ranking": _canonical_rows(chart_data.get("ranking"), limit=MAX_RANKING_ROWS, field="ranking"),
        "categories": _canonical_rows(chart_data.get("categories"), limit=MAX_CATEGORY_ROWS, field="categories"),
    }
    _validate_series_totals(normalized)
    return normalized


def _kpi_metric(kpis: list[dict[str, Any]], metric: str) -> Optional[float]:
    aliases = set(_METRIC_ALIASES[metric])
    for row in kpis:
        label = _normalized_key(row.get("label"))
        if label in aliases and row.get("numeric") is not None:
            return float(row["numeric"])
    return None


def _validate_series_totals(data: dict[str, Any]) -> None:
    rows = data["series"]
    if not rows:
        return
    for metric in ("orders", "items", "gross", "paid", "refunds", "net"):
        expected = _kpi_metric(data["kpis"], metric)
        if expected is None:
            continue
        values = [_row_metric(row, metric) for row in rows]
        if not values or any(value is None for value in values):
            continue
        actual = sum(float(value or 0.0) for value in values)
        tolerance = max(0.01, abs(expected) * 0.000001)
        if abs(actual - expected) > tolerance:
            raise ValueError(f"chart_data_total_mismatch:{metric}")


def _bucket_mode(data: dict[str, Any]) -> str:
    start, end = data["period_start"], data["period_end"]
    if start and end:
        days = (end - start).days + 1
        if days <= 31:
            return "day"
        if days <= 180:
            return "week"
        return "month"
    dated = [_parse_date(row.get("date") or row.get("data") or row.get("bucket")) for row in data["series"]]
    dated = [item for item in dated if item]
    if len(dated) > 1:
        days = (max(dated) - min(dated)).days + 1
        if days <= 31:
            return "day"
        if days <= 180:
            return "week"
        return "month"
    return "label"


def _bucket_key(day: date, mode: str) -> tuple[str, date]:
    if mode == "week":
        monday = day - timedelta(days=day.weekday())
        return f"Semana {monday.strftime('%d/%m')}", monday
    if mode == "month":
        first = day.replace(day=1)
        return first.strftime("%m/%Y"), first
    return day.strftime("%d/%m"), day


def _aggregate_series(data: dict[str, Any]) -> list[dict[str, Any]]:
    mode = _bucket_mode(data)
    aggregate: dict[tuple[str, str], dict[str, Any]] = {}
    order_index = 0
    for row in data["series"]:
        day = _parse_date(row.get("date") or row.get("data") or row.get("bucket") or row.get("period"))
        if day:
            label, sort_date = _bucket_key(day, mode)
            sort_key: Any = sort_date
        else:
            label = _row_label(row, "label", "bucket", "period", "periodo", default=str(order_index + 1))
            sort_key = order_index
        store = _row_label(row, "store", "loja", "conta", default="")
        key = (label, store)
        if key not in aggregate:
            aggregate[key] = {"label": label, "store": store, "_sort": sort_key}
            for metric in _METRIC_ALIASES:
                aggregate[key][metric] = 0.0
                aggregate[key][f"_{metric}_present"] = False
        point = aggregate[key]
        for metric in _METRIC_ALIASES:
            value = _row_metric(row, metric)
            if value is not None:
                point[metric] += value
                point[f"_{metric}_present"] = True
        order_index += 1
    return sorted(aggregate.values(), key=lambda row: (row["_sort"], row["store"]))


def _ranking_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    non_money_ranking = any(
        token in data.get("analysis_type", "")
        for token in ("stock", "estoque", "inventory", "listing", "anuncio", "advert", "ads")
    )
    for index, row in enumerate(data["ranking"]):
        sku = _row_label(row, "sku", "item_id", "mlb", "code", "codigo", default="")
        name = _row_label(row, "title", "name", "nome", "product", "produto", default="Produto")
        label = f"{sku} — {name}" if sku and sku not in name else name
        value = _row_metric(row, "gross")
        quantity = _row_metric(row, "items")
        explicit_money = row.get("money")
        if isinstance(explicit_money, str):
            explicit_money = _normalized_key(explicit_money) in {"1", "true", "sim", "yes"}
        money = bool(explicit_money) if explicit_money is not None else not non_money_ranking
        value_label = _row_label(row, "value_label", "rotulo_valor", default="")
        display_value = _row_label(row, "display_value", "valor_exibicao", default="")
        output.append({
            "label": label,
            "value": value or 0.0,
            "quantity": quantity or 0.0,
            "money": money,
            "value_label": value_label,
            "display_value": display_value,
            "_index": index,
        })
    output.sort(key=lambda row: (-row["value"], -row["quantity"], row["_index"]))
    return output[:10]


def _category_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, row in enumerate(data["categories"]):
        category = _row_label(row, "label", "category", "categoria", "name", "nome", default="Categoria")
        severity = _row_label(row, "severity", "severidade", default="")
        label = f"{severity} — {category}" if severity else category
        value = _row_metric(row, "items")
        if value is None:
            value = _as_float(row.get("count", row.get("value", row.get("valor", 0)))) or 0.0
        output.append({"label": label, "value": value, "_index": index})
    output.sort(key=lambda row: (-row["value"], row["_index"]))
    return output[:12]


def _store_rows(data: dict[str, Any], series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_store: dict[str, dict[str, float]] = defaultdict(lambda: {metric: 0.0 for metric in _METRIC_ALIASES})
    series_store_metrics: dict[str, set[str]] = defaultdict(set)
    for point in series:
        store = point.get("store") or ""
        if not store:
            continue
        for metric in _METRIC_ALIASES:
            if point.get(f"_{metric}_present"):
                by_store[store][metric] += float(point.get(metric) or 0.0)
                series_store_metrics[store].add(metric)
    output: list[dict[str, Any]] = []
    for store in data["stores"]:
        if isinstance(store, dict):
            name = store["name"]
            direct_metrics = {metric: _row_metric(store, metric) for metric in _METRIC_ALIASES}
            metrics = {
                metric: direct_metrics[metric] if direct_metrics[metric] is not None else by_store[name][metric]
                for metric in _METRIC_ALIASES
            }
            has_data = any(value is not None for value in direct_metrics.values()) or bool(series_store_metrics[name])
        else:
            name = store
            metrics = dict(by_store[name])
            has_data = bool(series_store_metrics[name])
        output.append({"label": name, "_has_data": has_data, **metrics})
    if not output:
        output = [{"label": name, "_has_data": bool(series_store_metrics[name]), **metrics} for name, metrics in by_store.items()]
    output.sort(key=lambda row: (-row.get("gross", 0.0), row["label"]))
    return output


def _new_canvas(data: dict[str, Any], section_title: str) -> tuple[Image.Image, ImageDraw.ImageDraw, int, int]:
    image = Image.new("RGB", CANVAS_SIZE, BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((38, 36, 1042, 1314), radius=28, fill=PANEL, outline=GRID, width=2)
    draw.ellipse((72, 68, 120, 116), fill=ACCENT)
    draw.ellipse((87, 83, 105, 101), fill=BACKGROUND)
    draw.text((140, 72), "BLACK JHON  •  ANÁLISE VISUAL", font=_font(24, bold=True), fill=ACCENT)

    y = 132
    title_length = len(data["title"])
    title_font_size = 43 if title_length <= 80 else 36 if title_length <= 150 else 30
    title_height = _draw_wrapped(draw, (72, y), data["title"], _font(title_font_size, bold=True), TEXT, 936, spacing=8)
    y += title_height + 18
    draw.text((72, y), section_title, font=_font(28, bold=True), fill=MUTED)
    y += 48
    if not data["coverage_complete"]:
        draw.rounded_rectangle((72, y, 1008, y + 54), radius=16, fill="#493a16", outline=YELLOW, width=2)
        draw.text((94, y + 12), "COBERTURA PARCIAL — confira os detalhes no relatório em texto", font=_font(21, bold=True), fill=YELLOW)
        y += 74

    footer_top = 1162
    draw.line((72, footer_top, 1008, footer_top), fill=GRID, width=2)
    footer_y = footer_top + 20
    period = _period_text(data)
    source = f"Fonte: {data['source']}"
    store_names = [
        str(store.get("name") or "").strip() if isinstance(store, dict) else str(store or "").strip()
        for store in data.get("stores") or []
    ]
    store_names = [name for name in store_names if name]
    if len(store_names) == 1:
        store_scope = f"Loja: {store_names[0]}"
    elif len(store_names) == 2:
        store_scope = f"Lojas: {store_names[0]} e {store_names[1]}"
    elif store_names:
        store_scope = f"Lojas comparadas: {len(store_names)}"
    else:
        store_scope = "Loja: não informada"
    _draw_wrapped(draw, (72, footer_y), source, _font(19, bold=True), MUTED, 936)
    _draw_wrapped(draw, (72, footer_y + 30), store_scope, _font(18), MUTED, 936)
    _draw_wrapped(draw, (72, footer_y + 58), period, _font(18), MUTED, 936)
    coverage = "Cobertura completa" if data["coverage_complete"] else "Cobertura parcial"
    draw.text((72, footer_y + 88), coverage, font=_font(18, bold=True), fill=GREEN if data["coverage_complete"] else YELLOW)
    return image, draw, y, footer_top - 18


def _period_text(data: dict[str, Any]) -> str:
    start, end = data["period_start"], data["period_end"]
    if start and end:
        return f"Período: {start.strftime('%d/%m/%Y')} a {end.strftime('%d/%m/%Y')}"
    if start:
        return f"Período iniciado em {start.strftime('%d/%m/%Y')}"
    if end:
        return f"Período encerrado em {end.strftime('%d/%m/%Y')}"
    return "Período informado no relatório em texto"


def _draw_kpi_cards(draw: ImageDraw.ImageDraw, kpis: list[dict[str, Any]], top: int, bottom: int) -> None:
    if not kpis:
        draw.rounded_rectangle((72, top + 20, 1008, min(bottom, top + 260)), radius=24, fill=PANEL_ALT)
        _draw_wrapped(draw, (106, top + 70), "Dados insuficientes para formar uma série", _font(34, bold=True), TEXT, 860, spacing=8)
        _draw_wrapped(draw, (106, top + 165), "O relatório em texto continua sendo a fonte completa.", _font(24), MUTED, 860)
        return
    columns = 2 if len(kpis) > 1 else 1
    rows = math.ceil(len(kpis) / columns)
    gap = 18
    available_height = bottom - top - gap * max(0, rows - 1)
    card_height = max(76, min(148, available_height // max(1, rows)))
    card_width = (936 - gap * (columns - 1)) // columns
    label_font_size = 22 if rows <= 4 else 18
    value_font_size = 31 if rows <= 4 else 25
    for index, item in enumerate(kpis):
        column = index % columns
        row = index // columns
        x = 72 + column * (card_width + gap)
        y = top + row * (card_height + gap)
        draw.rounded_rectangle((x, y, x + card_width, y + card_height), radius=18, fill=PANEL_ALT, outline=GRID, width=1)
        label = item["label"]
        _draw_wrapped(draw, (x + 20, y + 15), label, _font(label_font_size, bold=True), MUTED, card_width - 40, spacing=2)
        numeric = item.get("numeric")
        if numeric is None:
            value_text = str(item.get("value") or "Não informado")
        elif any(token in _normalized_key(label) for token in ("valor", "bruto", "liquido", "faturamento", "estorno", "reembolso")):
            value_text = _format_money(numeric)
        else:
            value_text = _format_number(numeric, 2 if not float(numeric).is_integer() else 0)
        draw.text((x + 20, y + card_height - value_font_size - 24), value_text, font=_font(value_font_size, bold=True), fill=TEXT)


def _render_kpi(data: dict[str, Any]) -> Image.Image:
    image, draw, top, bottom = _new_canvas(data, "Indicadores confirmados")
    _draw_kpi_cards(draw, data["kpis"], top + 16, bottom - 10)
    return image


def _metric_for_series(points: list[dict[str, Any]]) -> tuple[str, bool]:
    for metric, money in (("gross", True), ("net", True), ("items", False), ("orders", False)):
        if any(point.get(f"_{metric}_present") for point in points):
            return metric, money
    return "items", False


def _draw_summary_strip(draw: ImageDraw.ImageDraw, data: dict[str, Any], top: int) -> int:
    kpis = data["kpis"][:3]
    if not kpis:
        return top
    width = (936 - 24) // 3
    for index, item in enumerate(kpis):
        x = 72 + index * (width + 12)
        draw.rounded_rectangle((x, top, x + width, top + 88), radius=15, fill=PANEL_ALT)
        label = item["label"]
        _draw_wrapped(draw, (x + 14, top + 10), label, _font(16, bold=True), MUTED, width - 28, spacing=1)
        value = item.get("numeric")
        value_text = str(item.get("value") or "—") if value is None else _format_compact(value, money=any(token in _normalized_key(label) for token in ("valor", "bruto", "liquido", "faturamento")))
        draw.text((x + 14, top + 51), value_text, font=_font(23, bold=True), fill=TEXT)
    return top + 112


def _render_trend(data: dict[str, Any], points: list[dict[str, Any]], *, multi_store: bool) -> Image.Image:
    image, draw, top, bottom = _new_canvas(data, "Evolução no período")
    top = _draw_summary_strip(draw, data, top + 4)
    draw.rounded_rectangle((72, top + 8, 1008, bottom - 20), radius=22, fill=PANEL_ALT)

    metric, money = _metric_for_series(points)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if multi_store:
        for point in points:
            grouped[point.get("store") or "Loja"].append(point)
    else:
        candidates = [("Valor bruto", "gross"), ("Valor líquido", "net")] if money else [("Itens", "items"), ("Pedidos", "orders")]
        for label, candidate_metric in candidates:
            if any(point.get(f"_{candidate_metric}_present") for point in points):
                grouped[label] = [{**point, "_selected_value": point.get(candidate_metric, 0.0)} for point in points]
        if not grouped:
            grouped["Resultado"] = points

    legend_rows = 1
    legend_width = 0
    legend_font = _font(17, bold=True)
    for series_name in grouped:
        item_width = 70 + int(_text_width(draw, series_name, legend_font))
        if legend_width and legend_width + item_width > 880:
            legend_rows += 1
            legend_width = item_width
        else:
            legend_width += item_width
    plot = (126, top + 42 + (legend_rows - 1) * 30, 966, bottom - 90)

    all_values: list[float] = []
    for rows in grouped.values():
        for point in rows:
            all_values.append(float(point.get("_selected_value", point.get(metric, 0.0)) or 0.0))
    maximum = max(all_values or [1.0])
    minimum = min(0.0, min(all_values or [0.0]))
    span = maximum - minimum or 1.0
    left, chart_top, right, chart_bottom = plot
    for tick in range(5):
        ratio = tick / 4
        y = chart_bottom - int((chart_bottom - chart_top) * ratio)
        draw.line((left, y, right, y), fill=GRID, width=1)
        value = minimum + span * ratio
        draw.text((80, y - 10), _format_compact(value, money=money), font=_font(15), fill=MUTED)

    labels = []
    seen_labels = set()
    for point in points:
        if point["label"] not in seen_labels:
            seen_labels.add(point["label"])
            labels.append(point["label"])
    x_positions = {label: left + int(index * (right - left) / max(1, len(labels) - 1)) for index, label in enumerate(labels)}
    label_step = max(1, math.ceil(len(labels) / 6))
    for index, label in enumerate(labels):
        if index % label_step and index != len(labels) - 1:
            continue
        x = x_positions[label]
        draw.line((x, chart_bottom, x, chart_bottom + 8), fill=GRID, width=1)
        draw.text((x - 28, chart_bottom + 15), label, font=_font(15), fill=MUTED)

    legend_x = 98
    legend_y = top + 20
    for series_index, (series_name, rows) in enumerate(grouped.items()):
        color = SERIES_COLORS[series_index % len(SERIES_COLORS)]
        item_width = 70 + int(_text_width(draw, series_name, legend_font))
        if legend_x > 98 and legend_x + item_width > 988:
            legend_x = 98
            legend_y += 30
        draw.line((legend_x, legend_y + 8, legend_x + 28, legend_y + 8), fill=color, width=5)
        draw.text((legend_x + 38, legend_y - 4), series_name, font=legend_font, fill=TEXT)
        legend_x += item_width
        coordinates: list[tuple[int, int, float]] = []
        for point in rows:
            value = float(point.get("_selected_value", point.get(metric, 0.0)) or 0.0)
            x = x_positions.get(point["label"], left)
            y = chart_bottom - int((value - minimum) / span * (chart_bottom - chart_top))
            coordinates.append((x, y, value))
        if len(coordinates) >= 2:
            draw.line([(x, y) for x, y, _ in coordinates], fill=color, width=5, joint="curve")
        for x, y, _ in coordinates:
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=color, outline=PANEL_ALT, width=2)
        for selected_index in sorted({0, len(coordinates) - 1, max(range(len(coordinates)), key=lambda idx: coordinates[idx][2]) if coordinates else 0}):
            if not coordinates:
                continue
            x, y, value = coordinates[selected_index]
            label_value = _format_compact(value, money=money)
            text_x = min(right - int(_text_width(draw, label_value, _font(15, bold=True))), max(left, x - 24))
            if series_index % 2:
                text_y = min(chart_bottom - 19, y + 10)
            else:
                text_y = max(chart_top, y - 28)
            draw.text((text_x, text_y), label_value, font=_font(15, bold=True), fill=color)
    return image


def _render_ranking(
    data: dict[str, Any],
    rows: list[dict[str, Any]],
    section_title: str = "SKUs com maior valor vendido",
    value_label: str = "Valor",
    *,
    show_quantity: bool = True,
    quantity_label: str = "Quantidade",
) -> Image.Image:
    image, draw, top, bottom = _new_canvas(data, section_title)
    available = max(1, bottom - top - 20)
    font_size = 24
    row_layouts: list[tuple[list[str], int]] = []
    while font_size >= 12:
        font = _font(font_size, bold=True)
        line_height = max(16, draw.textbbox((0, 0), "Ag", font=font)[3] - draw.textbbox((0, 0), "Ag", font=font)[1])
        row_layouts = []
        total = 0
        for row in rows:
            lines = _wrap_text(draw, row["label"], font, 888)
            height = len(lines) * line_height + 48
            row_layouts.append((lines, height))
            total += height
        if total <= available:
            break
        font_size -= 1
    maximum = max((row["value"] for row in rows), default=1.0) or 1.0
    used_height = sum(height for _, height in row_layouts)
    extra_per_row = min(24, max(0, (available - used_height) // max(1, len(rows))))
    row_layouts = [(lines, height + extra_per_row) for lines, height in row_layouts]
    y = top + 2
    label_font = _font(font_size, bold=True)
    meta_font = _font(max(14, font_size - 1))
    line_height = max(16, draw.textbbox((0, 0), "Ag", font=label_font)[3] - draw.textbbox((0, 0), "Ag", font=label_font)[1])
    for index, (row, (lines, height)) in enumerate(zip(rows, row_layouts), start=1):
        draw.text((76, y), f"{index:02d}", font=_font(font_size, bold=True), fill=ACCENT)
        draw.multiline_text((124, y), "\n".join(lines), font=label_font, fill=TEXT, spacing=2)
        meta_y = y + len(lines) * line_height + 5
        rendered_value = _rendered_ranking_value(row)
        if show_quantity:
            meta = (
                f"{quantity_label}: {_format_number(row['quantity'], 2 if not float(row['quantity']).is_integer() else 0)}"
                f"    {value_label}: {rendered_value}"
            )
        else:
            meta = f"{value_label}: {rendered_value}"
        draw.text((124, meta_y), meta, font=meta_font, fill=MUTED)
        bar_y = meta_y + 28
        draw.rounded_rectangle((124, bar_y, 988, bar_y + 7), radius=4, fill=GRID)
        bar_right = 124 + max(4, int(864 * row["value"] / maximum))
        draw.rounded_rectangle((124, bar_y, bar_right, bar_y + 7), radius=4, fill=BLUE)
        y += height
    return image


def _rendered_ranking_value(row: dict[str, Any]) -> str:
    display_value = str(row.get("display_value") or "").strip()
    if display_value:
        return display_value
    if row.get("money", True):
        return _format_money(row.get("value"))
    value = float(row.get("value") or 0.0)
    return _format_number(value, 2 if not value.is_integer() else 0)


def _render_categories(data: dict[str, Any], rows: list[dict[str, Any]], section: str) -> Image.Image:
    image, draw, top, bottom = _new_canvas(data, section)
    maximum = max((row["value"] for row in rows), default=1.0) or 1.0
    available = max(1, bottom - top - 10)
    row_height = max(55, available // max(1, len(rows)))
    label_font = _font(20 if len(rows) <= 8 else 16, bold=True)
    for index, row in enumerate(rows):
        y = top + index * row_height
        lines = _wrap_text(draw, row["label"], label_font, 730)
        draw.multiline_text((82, y), "\n".join(lines), font=label_font, fill=TEXT, spacing=2)
        value_text = _format_number(row["value"], 2 if not float(row["value"]).is_integer() else 0)
        value_width = int(_text_width(draw, value_text, _font(20, bold=True)))
        draw.text((998 - value_width, y), value_text, font=_font(20, bold=True), fill=ACCENT)
        bar_y = min(y + row_height - 17, y + len(lines) * 22 + 8)
        draw.rounded_rectangle((82, bar_y, 998, bar_y + 9), radius=5, fill=GRID)
        width = max(5, int(916 * row["value"] / maximum))
        draw.rounded_rectangle((82, bar_y, 82 + width, bar_y + 9), radius=5, fill=ACCENT)
    return image


def _render_period_comparison(data: dict[str, Any], rows: list[dict[str, Any]]) -> Image.Image:
    image, draw, top, bottom = _new_canvas(data, "Comparação entre períodos")
    rows = rows[-6:]
    if len(rows) < 2:
        _draw_kpi_cards(draw, data["kpis"], top + 16, bottom - 10)
        return image

    gross_max = max((float(row.get("gross") or 0.0) for row in rows), default=1.0) or 1.0
    comparison_bottom = bottom - 190
    available = max(260, comparison_bottom - top - 10)
    row_height = max(112, min(174, available // len(rows)))
    for index, row in enumerate(rows):
        y = top + 4 + index * row_height
        card_bottom = min(comparison_bottom - 8, y + row_height - 12)
        draw.rounded_rectangle((72, y, 1008, card_bottom), radius=18, fill=PANEL_ALT, outline=GRID, width=1)
        label = _safe_text(row.get("label") or f"Período {index + 1}", field="period_label", maximum=160)
        _draw_wrapped(draw, (94, y + 14), label, _font(24, bold=True), TEXT, 870, spacing=2)
        meta_y = y + 56
        draw.text((94, meta_y), f"Faturamento: {_format_money(row.get('gross'))}", font=_font(19, bold=True), fill=BLUE)
        draw.text((490, meta_y), f"Pedidos: {_format_number(row.get('orders'))}", font=_font(18), fill=MUTED)
        draw.text((750, meta_y), f"Itens: {_format_number(row.get('items'))}", font=_font(18), fill=MUTED)
        bar_y = card_bottom - 24
        draw.rounded_rectangle((94, bar_y, 986, bar_y + 8), radius=4, fill=GRID)
        width = max(5, int(892 * float(row.get("gross") or 0.0) / gross_max))
        draw.rounded_rectangle((94, bar_y, 94 + width, bar_y + 8), radius=4, fill=BLUE)

    first, last = rows[0], rows[-1]
    delta_top = max(top + len(rows) * row_height + 8, bottom - 174)
    draw.text((76, delta_top), f"Variação: {first.get('label')} → {last.get('label')}", font=_font(21, bold=True), fill=TEXT)
    metrics = (("Faturamento", "gross", True), ("Pedidos", "orders", False), ("Itens", "items", False))
    card_width = 296
    for index, (label, metric, money) in enumerate(metrics):
        previous = float(first.get(metric) or 0.0)
        current = float(last.get(metric) or 0.0)
        delta = current - previous
        percent = None if abs(previous) < 1e-9 else (delta / previous) * 100.0
        x = 72 + index * (card_width + 24)
        y = delta_top + 38
        color = GREEN if delta >= 0 else RED
        draw.rounded_rectangle((x, y, x + card_width, y + 105), radius=16, fill=PANEL_ALT, outline=GRID, width=1)
        draw.text((x + 16, y + 12), label, font=_font(17, bold=True), fill=MUTED)
        delta_text = _format_money(delta) if money else _format_number(delta)
        if delta > 0:
            delta_text = f"+{delta_text}"
        draw.text((x + 16, y + 43), delta_text, font=_font(22, bold=True), fill=color)
        percent_text = "base anterior zerada" if percent is None else f"{percent:+.2f}%".replace(".", ",")
        draw.text((x + 16, y + 76), percent_text, font=_font(16, bold=True), fill=color)
    return image


def _render_store_comparison(data: dict[str, Any], rows: list[dict[str, Any]]) -> Image.Image:
    image, draw, top, bottom = _new_canvas(data, "Comparação entre lojas")
    maximum = max((row.get("gross", 0.0) for row in rows), default=1.0) or 1.0
    available = max(1, bottom - top - 12)
    row_height = max(63, available // max(1, len(rows)))
    label_font = _font(22 if len(rows) <= 7 else 17, bold=True)
    meta_font = _font(18 if len(rows) <= 7 else 15)
    for index, row in enumerate(rows):
        y = top + index * row_height
        lines = _wrap_text(draw, row["label"], label_font, 900)
        draw.multiline_text((82, y), "\n".join(lines), font=label_font, fill=TEXT, spacing=2)
        meta_y = y + len(lines) * (26 if len(rows) <= 7 else 20) + 4
        meta = f"Faturamento: {_format_money(row.get('gross'))}    Pedidos: {_format_number(row.get('orders'))}    Itens: {_format_number(row.get('items'))}"
        draw.text((82, meta_y), meta, font=meta_font, fill=MUTED)
        bar_y = min(y + row_height - 13, meta_y + 26)
        draw.rounded_rectangle((82, bar_y, 998, bar_y + 8), radius=4, fill=GRID)
        width = max(5, int(916 * float(row.get("gross") or 0.0) / maximum))
        draw.rounded_rectangle((82, bar_y, 82 + width, bar_y + 8), radius=4, fill=BLUE)
    return image


def _save_artifact(image: Image.Image, data: dict[str, Any], output_dir: Path, kind: str, section_title: str) -> dict[str, Any]:
    filename = f"report-chart-{uuid.uuid4().hex}.png"
    path = output_dir / filename
    try:
        image.save(path, format="PNG", optimize=True)
        byte_size = path.stat().st_size
        if byte_size <= 0 or byte_size > MAX_IMAGE_BYTES:
            raise ValueError("report_chart_exceeds_whatsapp_limit")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    title = _safe_text(f"{data['title']} — {section_title}", field="artifact_title", maximum=360)
    return {
        "artifact_type": "report_chart",
        "path": str(path.resolve()),
        "mime_type": "image/png",
        "sha256": digest,
        "byte_size": byte_size,
        "title": title,
        # O WhatsApp exibe ``caption`` como um bloco separado logo abaixo da
        # imagem. Fonte, período e cobertura já constam no próprio gráfico e
        # no relatório textual; repetir esses dados criava um rodapé solto.
        "caption": "",
        "kind": kind,
        "expires_at": expires_at,
    }


def generate_report_charts(chart_data: dict, output_dir: Path, max_images: int = 2) -> list[dict]:
    """Render safe, deterministic WhatsApp report charts as private PNG artifacts.

    Only the documented aggregate fields are read. Unknown fields (including any
    buyer/order payload) are deliberately ignored. The caller owns deletion of
    returned files after delivery; every artifact also carries a 24-hour expiry.
    """

    try:
        requested = int(max_images)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_images_must_be_integer") from exc
    if requested <= 0:
        return []
    requested = min(requested, MAX_IMAGES)
    data = _validate_and_normalize(chart_data)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if not destination.is_dir():
        raise ValueError("report_chart_output_not_directory")

    series = _aggregate_series(data)
    ranking = _ranking_rows(data)
    categories = _category_rows(data)
    store_rows = _store_rows(data, series)
    analysis_type = data["analysis_type"]
    weekly = any(token in analysis_type for token in ("weekly", "week", "semanal", "semana"))
    stock = any(token in analysis_type for token in ("stock", "estoque", "inventory"))
    stockout = any(token in analysis_type for token in ("stockout", "ruptura"))
    listing = any(token in analysis_type for token in ("listing", "anuncio", "advert", "ads"))
    period_comparison = any(token in analysis_type for token in ("period_comparison", "comparacao_periodos"))
    requested_multi_store = len(store_rows) > 1
    comparable_store_rows = [row for row in store_rows if row.get("_has_data")]
    series_stores = {point.get("store") for point in series if point.get("store")}

    renderers: list[tuple[str, str, Any]] = []
    if weekly:
        if categories:
            renderers.append(("weekly_categories", "Alertas da semana", lambda: _render_categories(data, categories, "Alertas da semana")))
        elif len(series) >= 2:
            renderers.append(("weekly_trend", "Evolução da semana", lambda: _render_trend(data, series, multi_store=len(series_stores) > 1)))
        else:
            renderers.append(("kpi_card", "Indicadores confirmados", lambda: _render_kpi(data)))
        requested = 1
    elif period_comparison:
        if len(series) >= 2:
            renderers.append((
                "period_comparison",
                "Comparação entre períodos",
                lambda: _render_period_comparison(data, series),
            ))
        else:
            renderers.append(("kpi_card", "Indicadores confirmados", lambda: _render_kpi(data)))
        if ranking:
            renderers.append(("sku_ranking", "SKUs com maior valor vendido", lambda: _render_ranking(data, ranking)))
    elif requested_multi_store:
        if len(comparable_store_rows) >= 2:
            renderers.append(("store_comparison", "Comparação entre lojas", lambda: _render_store_comparison(data, comparable_store_rows)))
        else:
            renderers.append(("kpi_card", "Indicadores confirmados", lambda: _render_kpi(data)))
        if len(series_stores) >= 2 and len({point["label"] for point in series}) >= 2:
            renderers.append(("store_trend", "Evolução das lojas", lambda: _render_trend(data, series, multi_store=True)))
    elif stock:
        if categories:
            renderers.append(("stock_distribution", "Distribuição do estoque", lambda: _render_categories(data, categories, "Distribuição do estoque")))
        else:
            renderers.append(("kpi_card", "Indicadores confirmados", lambda: _render_kpi(data)))
        if ranking:
            stock_money = any(row.get("money") for row in ranking)
            explicit_stock_value_label = next((row.get("value_label") for row in ranking if row.get("value_label")), "")
            if stockout:
                stock_section_title = "SKUs com maior risco de ruptura"
                stock_value_label = explicit_stock_value_label or "Risco"
            else:
                stock_section_title = "Maior capital em estoque" if stock_money else "Maiores saldos"
                stock_value_label = explicit_stock_value_label or ("Capital conhecido" if stock_money else "Saldo")
            renderers.append((
                "stock_ranking",
                stock_section_title,
                lambda: _render_ranking(
                    data,
                    ranking,
                    stock_section_title,
                    stock_value_label,
                    show_quantity=stock_money or stockout,
                    quantity_label="Saldo" if stockout else "Quantidade",
                ),
            ))
    elif listing:
        if categories:
            renderers.append((
                "listing_status",
                "Anúncios por status",
                lambda: _render_categories(data, categories, "Anúncios por status"),
            ))
        else:
            renderers.append(("kpi_card", "Indicadores confirmados", lambda: _render_kpi(data)))
        if ranking:
            renderers.append((
                "listing_sold_ranking",
                "Vendidos acumulados por anúncio",
                lambda: _render_ranking(
                    data,
                    ranking,
                    "Vendidos acumulados por anúncio",
                    "Vendidos acumulados",
                    show_quantity=False,
                ),
            ))
    else:
        if len({point["label"] for point in series}) >= 2:
            renderers.append(("sales_trend", "Evolução no período", lambda: _render_trend(data, series, multi_store=False)))
        else:
            renderers.append(("kpi_card", "Indicadores confirmados", lambda: _render_kpi(data)))
        if ranking:
            renderers.append(("sku_ranking", "SKUs com maior valor vendido", lambda: _render_ranking(data, ranking)))
        elif categories and len(renderers) < requested:
            renderers.append(("category_distribution", "Distribuição por categoria", lambda: _render_categories(data, categories, "Distribuição por categoria")))

    if not renderers:
        renderers.append(("kpi_card", "Indicadores confirmados", lambda: _render_kpi(data)))

    artifacts: list[dict[str, Any]] = []
    try:
        for kind, section_title, renderer in renderers[:requested]:
            image = renderer()
            try:
                artifacts.append(_save_artifact(image, data, destination, kind, section_title))
            finally:
                image.close()
    except Exception:
        for artifact in artifacts:
            Path(artifact["path"]).unlink(missing_ok=True)
        raise
    return artifacts


__all__ = ["generate_report_charts"]
