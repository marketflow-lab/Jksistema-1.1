"""Excel helpers for Medias Compras pedido lists."""

from __future__ import annotations

import io
import os
import re
import unicodedata
from datetime import datetime

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


def _texto_excel_seguro(valor) -> str:
    texto = str(valor or "").strip()
    return texto


def _atribuir_texto_excel(celula, valor) -> None:
    celula.value = _texto_excel_seguro(valor)
    # Forca texto literal para impedir que dados externos iniciados por =, +,
    # - ou @ sejam interpretados como formulas ao abrir o arquivo.
    celula.data_type = "s"


def _formatar_ncm_commercial_invoice(valor) -> str:
    texto = str(valor or "").strip()
    digitos = re.sub(r"\D", "", texto)
    if len(digitos) == 8:
        return f"{digitos[:4]}.{digitos[4:6]}.{digitos[6:]}"
    return texto


def _formatar_data_commercial_invoice(valor) -> str:
    texto = str(valor or "").strip()
    if not texto:
        return ""
    try:
        data = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        meses = (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        )
        return f"{meses[data.month - 1]} {data.day}, {data.year}"
    except ValueError:
        return texto


def _resolver_caminho_foto_commercial_invoice(client_id: str | None, foto_ref: str, sku: str) -> str | None:
    diretorios = []
    if client_id:
        diretorios.append(os.path.join(get_tenant_path(client_id), "cadastro_fotos"))
    if PASTA_INFO:
        diretorios.append(os.path.join(PASTA_INFO, "default", "cadastro_fotos"))
    diretorios = [os.path.realpath(diretorio) for diretorio in diretorios if diretorio]

    def _caminho_permitido(caminho: str) -> bool:
        caminho_real = os.path.realpath(caminho)
        for diretorio in diretorios:
            try:
                if os.path.commonpath([caminho_real, diretorio]) == diretorio:
                    return True
            except ValueError:
                continue
        return False

    foto_txt = _resolver_foto_cadastro_sku(client_id, sku, foto_ref)
    foto_txt = str(foto_txt or "").replace("\\", "/").strip()
    if not foto_txt:
        return None
    if os.path.isabs(foto_txt):
        return foto_txt if os.path.isfile(foto_txt) and _caminho_permitido(foto_txt) else None

    nome = foto_txt.split("/", 1)[1] if foto_txt.lower().startswith("cadastro_fotos/") else foto_txt
    nome = os.path.basename(nome)
    if not nome:
        return None

    for diretorio in diretorios:
        candidato = os.path.join(diretorio, nome)
        if os.path.isfile(candidato):
            return candidato
    return None


def _gerar_commercial_invoice_bytes(
    lista: dict,
    *,
    client_id: str | None = None,
    data_aprovacao: str = "",
) -> bytes:
    lista = lista if isinstance(lista, dict) else {}
    itens = [
        (item, _normalizar_item_lista_pedido(item))
        for item in (lista.get("itens") or [])
        if isinstance(item, dict)
    ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Commercial"
    ws.sheet_view.zoomScale = 100
    ws.sheet_view.showGridLines = True
    ws.freeze_panes = None
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    borda = Border(
        left=Side(style="thin", color="000000"),
        right=Side(style="thin", color="000000"),
        top=Side(style="thin", color="000000"),
        bottom=Side(style="thin", color="000000"),
    )
    fonte_base = Font(name="Times New Roman", size=9, color="000000")
    fonte_negrito = Font(name="Times New Roman", size=9, bold=True, color="000000")
    alinhamento_centro = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for intervalo in (
        "A1:I1",
        "A2:I2",
        "A3:B3",
        "F3:G3",
        "H3:I3",
        "A4:E6",
        "F4:I6",
        "A7:H7",
        "D8:E8",
    ):
        ws.merge_cells(intervalo)

    fornecedor = str(lista.get("supplier") or lista.get("fornecedor") or "").strip()
    numero_invoice = str(lista.get("numero_invoice") or lista.get("invoice") or "").strip()
    nome_lista = str(lista.get("nome_lista") or "").strip()
    loja = str(lista.get("loja") or "").strip()
    if loja == "__todas":
        loja = ""

    _atribuir_texto_excel(ws["A1"], fornecedor)
    _atribuir_texto_excel(ws["A2"], "COMMERCIAL INVOICE")
    _atribuir_texto_excel(ws["A3"], "INV. No.:")
    _atribuir_texto_excel(ws["C3"], numero_invoice)
    _atribuir_texto_excel(ws["D3"], "DATE:")
    _atribuir_texto_excel(ws["E3"], _formatar_data_commercial_invoice(data_aprovacao))
    _atribuir_texto_excel(ws["F3"], "FROM:")
    _atribuir_texto_excel(ws["H3"], "")
    _atribuir_texto_excel(ws["A4"], f"LIST: {nome_lista}" if nome_lista else "")
    _atribuir_texto_excel(ws["F4"], f"STORE: {loja}" if loja else "")
    _atribuir_texto_excel(ws["A7"], "Descriptions")

    cabecalhos = {
        "A8": "NO",
        "B8": "OE NO",
        "C8": "HSCODE",
        "D8": "PRODUCT DESCRIPTION",
        "F8": "PIC",
        "G8": "Quantity",
        "H8": "Price Unit",
        "I8": "Amount",
    }
    for endereco, texto in cabecalhos.items():
        _atribuir_texto_excel(ws[endereco], texto)

    ws.row_dimensions[1].height = 93
    ws.row_dimensions[2].height = 12
    ws.row_dimensions[3].height = 17.25
    ws.row_dimensions[4].height = 12.75
    ws.row_dimensions[5].height = 11.25
    ws.row_dimensions[6].height = 147
    ws.row_dimensions[7].height = 11.25
    ws.row_dimensions[8].height = 21.75

    primeira_linha_item = 9
    total_unidades = 0
    subtotal = 0.0
    for indice, (item_original, item) in enumerate(itens, start=1):
        linha = primeira_linha_item + indice - 1
        ws.merge_cells(start_row=linha, start_column=4, end_row=linha, end_column=5)

        def _valor_numerico_presente(chaves: list[str]) -> float | None:
            for chave in chaves:
                if chave not in item_original:
                    continue
                bruto = item_original.get(chave)
                if bruto is None or (isinstance(bruto, str) and not bruto.strip()):
                    continue
                numero = _to_float_excel(bruto)
                return max(0.0, numero) if numero is not None else None
            return None

        quantidade_numero = _valor_numerico_presente(["Quantidade", "quantity", "Qtd", "Qtde", "Qty"])
        preco = _valor_numerico_presente(
            ["Valor unidade", "Valor unitario", "Valor unitário", "Cost", "Custo", "Preco", "Preço"]
        )
        valor_total = _valor_numerico_presente(
            ["Valor total", "Sub-total(USD)", "Subtotal USD", "Sub total", "Subtotal"]
        )
        quantidade = int(quantidade_numero) if quantidade_numero is not None else None
        if (preco is None or preco <= 0) and quantidade and valor_total is not None and valor_total > 0:
            preco = valor_total / quantidade
        montante = None
        if quantidade is not None and preco is not None:
            montante = quantidade * preco
        elif valor_total is not None:
            montante = valor_total

        descricao = str(item.get(TITULO_PRODUTO_INGLES_KEY) or "").strip() or _primeiro_texto_item(
            item,
            [
                "Título em português",
                "Titulo em portugues",
                "Título do produto em português",
                "Titulo do produto em portugues",
                "Descrição",
                "Descricao",
                "Descricao do NCM",
            ],
        )
        ncm = _primeiro_texto_item(item, ["NCM", "Código NCM", "Codigo NCM", "HS Code", "HSCODE"])
        sku = str(item.get("SKU") or "").strip()
        oe_no = sku if re.match(r"(?i)^sku(?:\s|$)", sku) else (f"SKU {sku}" if sku else "")

        _atribuir_texto_excel(ws.cell(linha, 1), indice)
        _atribuir_texto_excel(ws.cell(linha, 2), oe_no)
        _atribuir_texto_excel(ws.cell(linha, 3), _formatar_ncm_commercial_invoice(ncm))
        _atribuir_texto_excel(ws.cell(linha, 4), descricao)
        if quantidade is not None:
            ws.cell(linha, 7).value = quantidade
            total_unidades += quantidade
        if preco is not None:
            ws.cell(linha, 8).value = preco
        if montante is not None:
            ws.cell(linha, 9).value = montante
            subtotal += montante

        ws.cell(linha, 7).number_format = "#,##0"
        ws.cell(linha, 8).number_format = "0.00"
        ws.cell(linha, 9).number_format = '"US$"#,##0.00'
        ws.row_dimensions[linha].height = 60

        caminho_foto = _resolver_caminho_foto_commercial_invoice(
            client_id,
            item.get("Foto", ""),
            item.get("SKU", ""),
        )
        if caminho_foto:
            try:
                imagem = XLImage(caminho_foto)
                largura = float(getattr(imagem, "width", 0) or 0)
                altura = float(getattr(imagem, "height", 0) or 0)
                altura_alvo = 74
                if largura > 0 and altura > 0:
                    imagem.height = altura_alvo
                    imagem.width = max(38, min(138, int(altura_alvo * (largura / altura))))
                else:
                    imagem.height = altura_alvo
                    imagem.width = 74
                ws.add_image(imagem, f"F{linha}")
            except Exception:
                pass

    primeira_linha_resumo = primeira_linha_item + len(itens)
    linha_peso_liquido = primeira_linha_resumo
    linha_peso_bruto = primeira_linha_resumo + 1
    linha_cbm = primeira_linha_resumo + 2
    linha_ctns = primeira_linha_resumo + 3
    linha_rodape = primeira_linha_resumo + 4

    for linha in (linha_peso_liquido, linha_peso_bruto, linha_cbm, linha_ctns):
        ws.merge_cells(start_row=linha, start_column=3, end_row=linha, end_column=4)
        ws.row_dimensions[linha].height = 15.75
    ws.merge_cells(start_row=linha_peso_bruto, start_column=6, end_row=linha_peso_bruto, end_column=8)
    ws.merge_cells(start_row=linha_cbm, start_column=6, end_row=linha_cbm, end_column=8)

    _atribuir_texto_excel(ws.cell(linha_peso_liquido, 3), "TOTAL NET WEIGHT")
    _atribuir_texto_excel(ws.cell(linha_peso_liquido, 6), "Units Total")
    ws.cell(linha_peso_liquido, 7).value = total_unidades
    _atribuir_texto_excel(ws.cell(linha_peso_liquido, 8), "Subtotal")
    ws.cell(linha_peso_liquido, 9).value = subtotal

    _atribuir_texto_excel(ws.cell(linha_peso_bruto, 3), "TOTAL GROSS WEIGHT")
    _atribuir_texto_excel(ws.cell(linha_peso_bruto, 6), "Freight USD")
    _atribuir_texto_excel(ws.cell(linha_peso_bruto, 9), "")

    _atribuir_texto_excel(ws.cell(linha_cbm, 3), "CBM:")
    _atribuir_texto_excel(ws.cell(linha_cbm, 6), "Insurance USD")
    _atribuir_texto_excel(ws.cell(linha_cbm, 9), "")

    _atribuir_texto_excel(ws.cell(linha_ctns, 3), "CTNS TOTAL")
    _atribuir_texto_excel(ws.cell(linha_ctns, 6), "Shipping:")
    _atribuir_texto_excel(ws.cell(linha_ctns, 7), "")
    _atribuir_texto_excel(ws.cell(linha_ctns, 8), "TOTAL:")
    ws.cell(linha_ctns, 9).value = subtotal

    ws.merge_cells(start_row=linha_rodape, start_column=1, end_row=linha_rodape, end_column=4)
    ws.merge_cells(start_row=linha_rodape, start_column=5, end_row=linha_rodape, end_column=8)
    moeda = str(lista.get("currency") or "").strip()
    incoterm = str(lista.get("incoterm") or "").strip()
    rodape_condicoes = "\n".join(
        texto
        for texto in (
            f"Incoterms: {incoterm}" if incoterm else "",
            f"Payment Currency: {moeda}" if moeda else "",
        )
        if texto
    )
    _atribuir_texto_excel(ws.cell(linha_rodape, 1), rodape_condicoes)
    _atribuir_texto_excel(ws.cell(linha_rodape, 5), "")
    _atribuir_texto_excel(ws.cell(linha_rodape, 9), "Company stamp and signature")
    ws.row_dimensions[linha_rodape].height = 101.25

    for coluna, largura in {
        "A": 6,
        "B": 15.63,
        "C": 15.88,
        "D": 14.38,
        "E": 27.75,
        "F": 18.25,
        "G": 13,
        "H": 13.13,
        "I": 12.38,
    }.items():
        ws.column_dimensions[coluna].width = largura

    for linha in range(1, linha_rodape + 1):
        for coluna in range(1, 10):
            celula = ws.cell(linha, coluna)
            celula.border = borda
            celula.font = fonte_base
            celula.alignment = alinhamento_centro

    ws["A1"].font = Font(name="Arial", size=11, bold=True, color="000000")
    ws["A2"].font = fonte_negrito
    for endereco in ("C3", "E3", "A4", "F4", "A7"):
        ws[endereco].font = fonte_negrito
    for coluna in range(1, 10):
        ws.cell(8, coluna).font = fonte_negrito
    for linha in range(primeira_linha_item, primeira_linha_resumo):
        for coluna in range(1, 10):
            ws.cell(linha, coluna).font = fonte_negrito
    for linha in range(primeira_linha_resumo, linha_rodape + 1):
        for coluna in range(1, 10):
            ws.cell(linha, coluna).font = fonte_negrito
    ws.cell(linha_peso_liquido, 7).number_format = "#,##0"
    ws.cell(linha_peso_liquido, 9).number_format = '"US$"#,##0.00'
    ws.cell(linha_ctns, 9).number_format = '"US$"#,##0.00'
    ws.print_area = f"A1:I{linha_rodape}"

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


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
    "_formatar_ncm_commercial_invoice",
    "_gerar_commercial_invoice_bytes",
]
