"""Validated marketplace print layouts and their shared PDF/preview compositor.

Only geometry is accepted from the client. Product and fiscal values always
come from the uploaded document; preview components are returned in memory.
"""

from __future__ import annotations

import base64
import json
import math

import fitz

from backend.services.etiquetas_marketplaces_common import desenhar_rodape_sku, gerar_imagem_barcode


MM = 72 / 25.4
STORES = ("Mercado Livre", "Shopee", "TikTok", "Amazon")
ITEMS = ("etiqueta", "sku", "danfe")


def layout_padrao(store: str) -> dict:
    if store not in STORES:
        raise ValueError("Loja invalida.")
    bounds = {
        "etiqueta": (0, 0, 283, 390, 0, True),
        "sku": (0, 389, 283, 425, 0, True),
        "danfe": (256, 15, 283, 390, 90, True),
    }
    if store in ("Shopee", "TikTok"):
        bounds["etiqueta"] = (0, 0, 283, 425, 0, True)
        bounds["sku"] = (240, 0, 283, 350, 90, True) if store == "Shopee" else (272, 0, 283, 300, 90, True)
        bounds["danfe"] = (256, 15, 283, 390, 90, False)
    items = {
        name: {"x": x0 / MM, "y": y0 / MM, "width": (x1 - x0) / MM,
               "height": (y1 - y0) / MM, "rotation": rotation, "visible": visible}
        for name, (x0, y0, x1, y1, rotation, visible) in bounds.items()
    }
    return {"version": 1, "page": {"width": 283 / MM, "height": 425 / MM}, "items": items}


def layout_e_padrao_atual(layout: dict | None, store: str) -> bool:
    """Unchanged geometry must keep using the existing marketplace processor."""
    if layout is None:
        return True
    if layout.get("danfe_source"):
        return False
    current = layout_padrao(store)
    if layout.get("version") != current["version"]:
        return False
    for name, value in current["page"].items():
        if not math.isclose(layout["page"][name], value, abs_tol=0.000001):
            return False
    for name in ITEMS:
        for key, value in current["items"][name].items():
            candidate = layout["items"][name][key]
            if isinstance(value, bool):
                if candidate is not value:
                    return False
            elif not math.isclose(candidate, value, abs_tol=0.000001):
                return False
    return True


def _metadata_layout(store: str, layout: dict) -> dict:
    anchors = {
        "Mercado Livre": {"sku_box": [6, 397, 277, 424], "danfe_text_x": 262.5,
                           "danfe_barcode_box": [264.2, 20, 283, 385]},
        "Shopee": {"sku_origin": [279, 350], "sku_rotation": 90,
                    "product_origin": [265, 350], "customer_origin": [250, 350]},
        "TikTok": {"sku_origin": [281, 300], "sku_rotation": 90},
    }
    return {"current_layout": layout_padrao(store),
            "mode": "padrao_atual" if layout_e_padrao_atual(layout, store) else "personalizado",
            "capabilities": {"preview": True, "processing": store != "Amazon",
                             "embedded_danfe": store in ("Shopee", "TikTok")},
            "legacy_anchors": {"unit": "pt", "page": [283, 425], **anchors.get(store, {})}}


def validar_layout(raw, store: str) -> dict:
    """Reject malformed, non-finite and out-of-page geometry before PDF work."""
    if store not in STORES:
        raise ValueError("Loja invalida.")
    if raw is None or raw == "":
        return layout_padrao(store)
    if isinstance(raw, str):
        if len(raw) > 12000:
            raise ValueError("Configuracao de impressao muito grande.")
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("Configuracao de impressao invalida.") from exc
    if not isinstance(raw, dict) or not {"version", "page", "items"}.issubset(raw) or set(raw) - {"version", "page", "items", "danfe_source"}:
        raise ValueError("Configuracao de impressao invalida.")
    if type(raw["version"]) is not int or raw["version"] != 1:
        raise ValueError("Versao de configuracao nao suportada.")
    page = raw["page"]
    if not isinstance(page, dict) or set(page) != {"width", "height"}:
        raise ValueError("Medidas da folha invalidas.")

    def number(value, minimum, maximum):
        if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError("Medidas de impressao invalidas.")
        return float(value)

    width = number(page["width"], 20, 300)
    height = number(page["height"], 20, 500)
    items = raw["items"]
    if not isinstance(items, dict) or set(items) != set(ITEMS):
        raise ValueError("Componentes de impressao invalidos.")
    normalized = {}
    for name in ITEMS:
        item = items[name]
        if not isinstance(item, dict) or set(item) != {"x", "y", "width", "height", "rotation", "visible"}:
            raise ValueError("Componente de impressao invalido.")
        x = number(item["x"], 0, width)
        y = number(item["y"], 0, height)
        w = number(item["width"], 0.5, width)
        h = number(item["height"], 0.5, height)
        if x + w > width + 0.000001 or y + h > height + 0.000001:
            raise ValueError("Mantenha os componentes dentro da folha.")
        if type(item["rotation"]) is not int or item["rotation"] not in (0, 90, 180, 270):
            raise ValueError("Rotacao de impressao invalida.")
        if type(item["visible"]) is not bool:
            raise ValueError("Visibilidade de impressao invalida.")
        normalized[name] = {"x": x, "y": y, "width": w, "height": h,
                            "rotation": item["rotation"], "visible": item["visible"]}
    output = {"version": 1, "page": {"width": width, "height": height}, "items": normalized}
    if "danfe_source" in raw:
        source = raw["danfe_source"]
        if not isinstance(source, dict) or set(source) != {"x", "y", "width", "height"}:
            raise ValueError("Area original do DANFE invalida.")
        crop = {name: number(source[name], 0 if name in ("x", "y") else 0.001, 1) for name in source}
        if crop["x"] + crop["width"] > 1.000001 or crop["y"] + crop["height"] > 1.000001:
            raise ValueError("Mantenha a area do DANFE dentro da etiqueta original.")
        output["danfe_source"] = crop
    return output


def _pdf_texto(text: str, *, extra_text: str = "") -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=280, height=55 if extra_text else 28)
    text_rect = fitz.Rect(3, 2, 277, 27)
    for fontsize in (13, 12, 10, 9, 8, 7, 6, 5, 4):
        if page.insert_textbox(text_rect, text, fontname="hebo", fontsize=fontsize,
                               align=1, color=(1, 0, 0) if "N/D" in text else (0, 0, 0)) >= 0:
            break
    if extra_text:
        page.insert_textbox(fitz.Rect(3, 29, 277, 55), extra_text, fontsize=8, align=1)
    output = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return output


def _pdf_codigo(code: str | None, text: str | None = None) -> bytes | None:
    if not code:
        return None
    image = gerar_imagem_barcode(code)
    if not image:
        raise ValueError("Nao foi possivel gerar o codigo de barras.")
    doc = fitz.open()
    page = doc.new_page(width=365, height=30)
    label = text or code
    label_size = min(6, 359 / max(fitz.get_text_length(label, fontsize=1), 1))
    label_width = fitz.get_text_length(label, fontsize=label_size)
    page.insert_text(fitz.Point((365 - label_width) / 2, 7), label, fontsize=label_size)
    page.insert_image(fitz.Rect(2, 10, 363, 30), stream=image, keep_proportion=False)
    output = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return output


def _pdf_original(doc, page_index: int, clip=None, *, counter: int | None = None,
                  store: str = "Mercado Livre") -> bytes:
    source = doc[page_index]
    area = fitz.Rect(clip) if clip is not None else source.rect
    component = fitz.open()
    page = component.new_page(width=283, height=390 if store in ("Mercado Livre", "Amazon") else 425)
    page.show_pdf_page(page.rect, doc, page_index, clip=area)
    if counter is not None:
        label = str(counter)
        if store == "Shopee":
            page.insert_text(fitz.Point(240, 20), f"#{label}", fontsize=10, color=(0.5, 0.5, 0.5))
        elif store == "TikTok":
            page.draw_rect(fitz.Rect(250, 0, 283, 15), color=None, fill=(1, 1, 1))
            page.insert_text(fitz.Point(255, 12), label, fontsize=12)
        else:
            size = min(12, 21 / max(fitz.get_text_length(label, fontsize=1), 1))
            page.insert_text(fitz.Point(260, 13), label, fontsize=size)
    output = component.tobytes(garbage=3, deflate=True)
    component.close()
    return output


def _overlay_atual(store: str, name: str, *, sku_text="", extra_text="",
                   code=None, code_text=None, quantidade_sku=None) -> bytes | None:
    """Build isolated layers at the unchanged processor's point coordinates."""
    with fitz.open() as overlay:
        page = overlay.new_page(width=283, height=425)
        if name == "sku":
            color = (1, 0, 0) if "N/D" in sku_text else (0, 0, 0)
            if store == "Shopee":
                page.insert_text(fitz.Point(279, 350), sku_text, fontsize=12, color=color, rotate=90)
                product, _, customer = extra_text.partition("\n")
                if product:
                    page.insert_text(fitz.Point(265, 350), product[:45], fontsize=8, rotate=90)
                if customer:
                    page.insert_text(fitz.Point(250, 350), customer[:35], fontsize=9, rotate=90)
            elif store == "TikTok":
                prefix, separator, quantity = sku_text.rpartition("  (x")
                if separator and prefix.startswith("SKU: ") and len(prefix[5:]) > 25:
                    sku_text = prefix[:30] + "..." + separator + quantity
                page.insert_text(fitz.Point(281, 300), sku_text, fontsize=8, color=color, rotate=90)
            else:
                desenhar_rodape_sku(page, sku_text, cor=color, quantidade=quantidade_sku)
        else:
            title = "CHAVE DE ACESSO / VENDA"
            text = (code_text or code) if code else "CODIGO NAO ENCONTRADO"
            text_size = 5.5 if code else 5
            text_color = (0, 0, 0) if code else (0.5, 0.5, 0.5)
            title_length = fitz.get_text_length(title, fontsize=5)
            text_length = fitz.get_text_length(text, fontsize=text_size)
            title_end = (390 - title_length - 8 - text_length) / 2 + title_length
            page.insert_text(fitz.Point(262.5, title_end), title, fontsize=5, rotate=90)
            page.insert_text(fitz.Point(262.5, title_end + 8 + text_length), text,
                             fontsize=text_size, color=text_color, rotate=90)
            barcode = gerar_imagem_barcode(code) if code else None
            if barcode:
                page.insert_image(fitz.Rect(264.2, 20, 283, 385), stream=barcode, rotate=90, keep_proportion=False)
        item = layout_padrao(store)["items"][name]
        clip = fitz.Rect(item["x"] * MM, item["y"] * MM,
                         (item["x"] + item["width"]) * MM, (item["y"] + item["height"]) * MM)
        rotation = item["rotation"]
        with fitz.open() as component:
            target = component.new_page(width=clip.height if rotation in (90, 270) else clip.width,
                                        height=clip.width if rotation in (90, 270) else clip.height)
            target.show_pdf_page(target.rect, overlay, 0, clip=clip, rotate=(360 - rotation) % 360)
            return component.tobytes(garbage=3, deflate=True)


def _compose(page, components: dict, layout: dict) -> None:
    page.set_mediabox(fitz.Rect(0, 0, layout["page"]["width"] * MM, layout["page"]["height"] * MM))
    for name in ITEMS:
        item = layout["items"][name]
        data = components.get(name)
        if not item["visible"] or not data:
            continue
        rect = fitz.Rect(item["x"] * MM, item["y"] * MM,
                         (item["x"] + item["width"]) * MM, (item["y"] + item["height"]) * MM)
        with fitz.open(stream=data, filetype="pdf") as source:
            page.show_pdf_page(rect, source, 0, rotate=item["rotation"], keep_proportion=True)


def _separar_danfe(original: bytes, selection: dict) -> tuple[bytes, bytes]:
    """Extract exactly the region explicitly selected by the user, once."""
    with fitz.open(stream=original, filetype="pdf") as source:
        page = source[0]
        rect = fitz.Rect(selection["x"] * page.rect.width, selection["y"] * page.rect.height,
                         (selection["x"] + selection["width"]) * page.rect.width,
                         (selection["y"] + selection["height"]) * page.rect.height)
        with fitz.open() as component:
            target = component.new_page(width=rect.width, height=rect.height)
            target.show_pdf_page(target.rect, source, 0, clip=rect)
            danfe = component.tobytes(garbage=3, deflate=True)
        page.add_redact_annot(rect, fill=(1, 1, 1))
        # Blank only selected image pixels and remove contained line art/text.
        # Any clipping is explicitly chosen in the editor, never inferred.
        page.apply_redactions(images=2, graphics=1, text=0)
        remaining = source.tobytes(garbage=3, deflate=True)
        return remaining, danfe


def renderizar_etiqueta_layout(page, doc, page_index, *, layout, sku_text,
                               code=None, code_text=None, clip=None,
                               extra_text="", counter=None, embedded_danfe=False,
                               collector=None, store="Mercado Livre", quantidade_sku=None) -> None:
    """Compose raw source plus new layers once, for print and for preview."""
    original = _pdf_original(doc, page_index, clip, counter=counter, store=store)
    components = {
        "etiqueta": original,
        "sku": _overlay_atual(
            store, "sku", sku_text=sku_text, extra_text=extra_text, quantidade_sku=quantidade_sku,
        ),
        "danfe": None if embedded_danfe else _overlay_atual(store, "danfe", code=code, code_text=code_text),
    }
    warnings = []
    if layout.get("danfe_source"):
        components["etiqueta"], components["danfe"] = _separar_danfe(original, layout["danfe_source"])
    elif embedded_danfe:
        warnings.append("O codigo DANFE desta loja faz parte da etiqueta original e acompanha o movimento dela.")
    elif not code:
        warnings.append("Codigo DANFE nao encontrado neste arquivo.")
    _compose(page, components, layout)
    if collector is not None and not collector:
        collector.append({"components": components, "warnings": warnings, "source": original,
                          "can_select_danfe": bool(embedded_danfe or not code or layout.get("danfe_source"))})


def _data_png(data: bytes, *, alpha: bool = False) -> str:
    with fitz.open(stream=data, filetype="pdf") as doc:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=alpha)
        return "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode("ascii")


def _componentes_amostra(store: str) -> dict:
    doc = fitz.open()
    embedded = store in ("Shopee", "TikTok")
    page = doc.new_page(width=283, height=425 if embedded else 390)
    page.draw_rect(fitz.Rect(8, 8, 275, 417 if embedded else 382), width=1)
    page.insert_textbox(fitz.Rect(18, 20, 265, 155),
                        f"{store}\nAMOSTRA DE ETIQUETA\n\nDESTINATARIO\nCliente de exemplo\nEndereco de exemplo\nPedido de exemplo", fontsize=12)
    tracking = gerar_imagem_barcode("AMOSTRA000001")
    if tracking:
        page.insert_image(fitz.Rect(25, 210, 258, 260), stream=tracking)
    page.insert_text(fitz.Point(85, 330), "SEM VALOR FISCAL", fontsize=10)
    code = "12345678901234567890123456789012345678901234"
    if embedded:
        fiscal = gerar_imagem_barcode(code)
        if fiscal:
            page.insert_image(fitz.Rect(25, 355, 258, 385), stream=fiscal)
        page.insert_text(fitz.Point(25, 398), code, fontsize=6)
    original = _pdf_original(doc, 0, counter=1, store=store)
    doc.close()
    sku = "SKU EXEMPLO-001  x  2" if not embedded else "SKU: EXEMPLO-001 (x2)"
    return {"etiqueta": original, "sku": _overlay_atual(
                store, "sku", sku_text=sku, quantidade_sku="2" if store == "Mercado Livre" else None,
            ),
            "danfe": None if embedded else _overlay_atual(store, "danfe", code=code, code_text=code)}


def gerar_preview_layout(store: str, bytes_etiquetas=None, bytes_lista=None,
                         bytes_nf=None, layout=None) -> dict:
    normalized = validar_layout(layout, store)
    sample = not bytes_etiquetas
    if store == "Amazon":
        sample = True
    warnings = []
    if sample:
        components = _componentes_amostra(store)
        original_source = components["etiqueta"]
        can_select_danfe = bool(store in ("Shopee", "TikTok") or normalized.get("danfe_source"))
        if store == "Amazon":
            warnings.append("Processamento Amazon ainda nao esta disponivel. Esta e apenas uma amostra do editor.")
        else:
            warnings.append("Amostra de impressao. Envie um PDF para visualizar seus dados reais.")
        if store in ("Shopee", "TikTok"):
            warnings.append("Nos arquivos reais desta loja, o codigo DANFE acompanha a etiqueta original.")
        if normalized.get("danfe_source"):
            components["etiqueta"], components["danfe"] = _separar_danfe(original_source, normalized["danfe_source"])
        doc = fitz.open()
        page = doc.new_page()
        _compose(page, components, normalized)
        result_pdf = doc.tobytes()
        doc.close()
    else:
        from backend.services.etiquetas_marketplaces import processar_etiquetas_loja

        collector = []
        result = processar_etiquetas_loja(store, bytes_etiquetas, bytes_lista, bytes_nf,
                                          layout=normalized, _component_collector=collector, _limit=1)
        if not result.get("success"):
            return result
        if not collector or not result.get("out_etiquetas"):
            return {"success": False, "message": "Nenhuma etiqueta reconhecida neste PDF."}
        components = collector[0]["components"]
        original_source = collector[0]["source"]
        can_select_danfe = collector[0]["can_select_danfe"]
        warnings.extend(collector[0]["warnings"])
        result_pdf = result["out_etiquetas"]
        if layout_e_padrao_atual(normalized, store):
            # Exact output of today's processor, including fixed counters/rules.
            # Opening/saving an untouched layout never substitutes a new format.
            current = processar_etiquetas_loja(store, bytes_etiquetas, bytes_lista, bytes_nf, _limit=1)
            if current.get("out_etiquetas"):
                result_pdf = current["out_etiquetas"]
    return {
        "success": True, "layout": normalized, "sample": sample,
        "components": {name: _data_png(data, alpha=True) if data else None for name, data in components.items()},
        "availability": {name: bool(data) for name, data in components.items()},
        "can_select_danfe": can_select_danfe,
        "preview": _data_png(result_pdf), "source": _data_png(original_source), "warnings": warnings,
        **_metadata_layout(store, normalized),
    }


__all__ = ["layout_padrao", "layout_e_padrao_atual", "validar_layout", "gerar_preview_layout", "renderizar_etiqueta_layout"]
