"""Excel helpers for Medias Compras pedido lists."""

from __future__ import annotations

import io
import os
import re
import unicodedata

import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from backend.services import medias_compras_common as medias_common
from backend.services.medias_compras_common import *
from backend.services.runtime_bridge import bind_runtime_globals

_RUNTIME_NAMES = (
    "logger",
    "get_tenant_path",
    "TEMP_FILES_STORAGE",
    "TEMP_FILES_META",
    "LISTA_PEDIDO_XLSX_CACHE",
    "LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS",
    "PASTA_INFO",
)


def _sync_common_names() -> None:
    for name in medias_common.COMMON_EXPORTS:
        globals()[name] = getattr(medias_common, name)
    for name in _RUNTIME_NAMES:
        globals()[name] = getattr(medias_common, name)


def _configure_runtime_globals(runtime_module=None):
    runtime = medias_common.configure_medias_compras_common_runtime(runtime_module)
    bind_runtime_globals(globals(), runtime)
    _sync_common_names()
    return runtime

def _normalizar_cabecalho_excel(texto: str) -> str:
    base = unicodedata.normalize("NFKD", str(texto or ""))
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", base.lower())


def _to_float_excel(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        s = s.replace("\u00a0", " ")
        s = re.sub(r"(?i)(r\$|us\$|usd|brl|\$)", "", s)
        s = re.sub(r"[^0-9,.\-]+", "", s.replace(" ", ""))
        if s in {"", "-", ".", ","}:
            return None
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        return float(s)
    except Exception:
        return None


def _linha_resumo_excel_sku(sku: str) -> bool:
    txt = str(sku or "").strip()
    if not txt:
        return False
    base = unicodedata.normalize("NFKD", txt)
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    base = re.sub(r"[^a-z0-9]+", "", base.lower())
    return base in {"total", "totais", "subtotal", "subtotais"}


def _gerar_excel_lista_pedido_bytes(nome_lista: str, itens: list[dict], client_id: str | None = None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Import 54"
    ws.sheet_view.zoomScale = 110

    headers = list(C54_HEADERS_LISTA_PEDIDO)
    ws.append(headers)

    cabecalho_fill = PatternFill(fill_type="solid", fgColor="B4E4E8")
    cabecalho_font = Font(color="000000", bold=False, size=11)
    cabecalho_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    borda_fina = Border(
        left=Side(style="thin", color="D9E1E2"),
        right=Side(style="thin", color="D9E1E2"),
        top=Side(style="thin", color="D9E1E2"),
        bottom=Side(style="thin", color="D9E1E2"),
    )

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = cabecalho_fill
        cell.font = cabecalho_font
        cell.alignment = cabecalho_alignment
        cell.border = borda_fina
    ws.row_dimensions[1].height = 25.5

    diretorios_fotos = []
    if client_id:
        diretorios_fotos.append(os.path.join(get_tenant_path(client_id), "cadastro_fotos"))
    diretorios_fotos.append(os.path.join(PASTA_INFO, "default", "cadastro_fotos"))

    cache_fotos: dict[str, str | None] = {}

    def _resolver_caminho_foto_lista(foto_ref: str, sku: str) -> str | None:
        foto_txt = _resolver_foto_cadastro_sku(client_id, sku, foto_ref)
        foto_txt = str(foto_txt or "").replace("\\", "/").strip()
        if not foto_txt:
            return None

        if os.path.isabs(foto_txt) and os.path.exists(foto_txt):
            return foto_txt

        nome = foto_txt
        if nome.lower().startswith("cadastro_fotos/"):
            nome = nome.split("/", 1)[1]
        nome = os.path.basename(nome)
        if not nome:
            return None

        if nome in cache_fotos:
            return cache_fotos[nome]

        caminho = None
        for pasta_fotos in diretorios_fotos:
            candidato = os.path.join(pasta_fotos, nome)
            if os.path.exists(candidato):
                caminho = candidato
                break
        cache_fotos[nome] = caminho
        return caminho

    itens_norm = [_normalizar_item_lista_pedido(i) for i in (itens or [])]
    for idx, item in enumerate(itens_norm, start=2):
        quantidade = int(max(0.0, _to_float(item.get("Quantidade", 0), 0.0)))
        valor_unidade = max(0.0, _to_float(item.get("Valor unidade", 0), 0.0))
        ws.append([
            item.get("SKU", ""),
            "",
            item.get(TITULO_PRODUTO_INGLES_KEY, ""),
            item.get("OEM", ""),
            item.get(COR_LADO_LISTA_PEDIDO_KEY, ""),
            item.get("Link", ""),
            quantidade,
            valor_unidade if valor_unidade > 0 else None,
            None,
            item.get(CBM_LISTA_PEDIDO_KEY, ""),
            item.get(PESO_LISTA_PEDIDO_KEY, ""),
            item.get(EMBALAGEM_LISTA_PEDIDO_KEY, ""),
        ])
        faixa_fill = PatternFill(fill_type="solid", fgColor=("F7FAFC" if idx % 2 == 0 else "FFFFFF"))
        for col_idx in range(1, len(headers) + 1):
            c = ws.cell(row=idx, column=col_idx)
            c.fill = faixa_fill
            c.border = borda_fina
            c.alignment = Alignment(
                vertical="center",
                horizontal=("center" if col_idx in (2, 7) else "left"),
                wrap_text=(col_idx in (3, 4, 5, 6, 12)),
            )

        ws.cell(row=idx, column=7).number_format = "#,##0"
        ws.cell(row=idx, column=8).number_format = '"$" #,##0.00'
        ws.cell(row=idx, column=9).number_format = '"$" #,##0.00'
        ws.cell(row=idx, column=10).number_format = "#,##0.000"
        ws.cell(row=idx, column=11).number_format = "#,##0.000"
        ws.row_dimensions[idx].height = max(float(ws.row_dimensions[idx].height or 0), 84.75)

        cel_sku = ws.cell(row=idx, column=1)
        cel_sku.font = Font(bold=True, color="0F2D52")
        cel_sku.fill = PatternFill(fill_type="solid", fgColor="EAF2FB")

        link_val = str(item.get("Link", "") or "").strip()
        if link_val:
            cel_link = ws.cell(row=idx, column=6)
            cel_link.value = link_val
            cel_link.hyperlink = link_val
            cel_link.style = "Hyperlink"
            cel_link.alignment = Alignment(vertical="center", horizontal="left")

        caminho_foto = _resolver_caminho_foto_lista(item.get("Foto", ""), item.get("SKU", ""))
        if caminho_foto:
            try:
                img = XLImage(caminho_foto)
                altura_alvo_px = 82
                largura_original = float(getattr(img, "width", 0) or 0)
                altura_original = float(getattr(img, "height", 0) or 0)

                if largura_original > 0 and altura_original > 0:
                    proporcao = largura_original / altura_original
                    img.height = altura_alvo_px
                    img.width = max(42, min(130, int(altura_alvo_px * proporcao)))
                else:
                    img.height = altura_alvo_px
                    img.width = 82

                ws.add_image(img, f"B{idx}")
            except Exception:
                ws[f"B{idx}"] = str(item.get("Foto", "") or "")
        else:
            ws[f"B{idx}"] = str(item.get("Foto", "") or "")

        ws[f"I{idx}"] = f"=G{idx}*H{idx}"

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:L{max(1, len(itens_norm) + 1)}"
    for col, width in {
        "A": 12.57,
        "B": 17.43,
        "C": 34,
        "D": 18,
        "E": 16,
        "F": 24,
        "G": 13,
        "H": 13,
        "I": 16,
        "J": 13,
        "K": 13,
        "L": 18,
    }.items():
        ws.column_dimensions[col].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()



def configure_medias_compras_excel_runtime(runtime_module=None):
    return _configure_runtime_globals(runtime_module)


configure_medias_compras_excel_runtime()

__all__ = [
    "configure_medias_compras_excel_runtime",
    "_normalizar_cabecalho_excel",
    "_to_float_excel",
    "_linha_resumo_excel_sku",
    "_gerar_excel_lista_pedido_bytes",
]
