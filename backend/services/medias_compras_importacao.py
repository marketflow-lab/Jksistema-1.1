"""Excel import endpoints for Medias Compras pedido lists."""

from __future__ import annotations

import io
import re
import unicodedata
import uuid
from datetime import datetime

import openpyxl
from fastapi import Depends, File, Form, HTTPException, UploadFile

from backend.services import medias_compras_common as medias_common
from backend.services.cadastro_common import _normalizar_sku_mes
from backend.services.medias_compras_common import *
from backend.services.medias_compras_excel import *
from backend.services.medias_compras_fiscal import *
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


def _mapear_cabecalho_lista_pedido_excel(ws) -> tuple[dict[str, int], int]:
    max_row = int(ws.max_row or 0)
    max_col = int(ws.max_column or 0)
    if max_row <= 0 or max_col <= 0:
        return {}, 1

    linhas_para_testar = min(max_row, 15)
    melhores_headers: dict[str, int] = {}
    melhor_linha = 1
    melhor_pontuacao = -1
    colunas_relevantes = {
        "sku",
        "picture",
        "description",
        "oemmodel",
        "colorside",
        "linkaliexpress",
        "quantity",
        "cost",
        "subtotalusd",
        "valorunidade",
        "valorunitario",
        "precounitario",
        "preco",
        "valor",
        "custo",
        "valortotal",
        "subtotal",
    }

    for row in range(1, linhas_para_testar + 1):
        headers = {}
        for col in range(1, max_col + 1):
            chave = _normalizar_cabecalho_excel(ws.cell(row=row, column=col).value)
            if chave and chave not in headers:
                headers[chave] = col
        pontuacao = sum(1 for chave in headers if chave in colunas_relevantes)
        if "sku" in headers:
            pontuacao += 4
        if pontuacao > melhor_pontuacao:
            melhores_headers = headers
            melhor_linha = row
            melhor_pontuacao = pontuacao
        if "sku" in headers and pontuacao >= 6:
            return headers, row

    return melhores_headers, melhor_linha


def _normalizar_sku_lista_pedido_excel(sku: str) -> str:
    sku_txt = str(sku or "").strip()
    sku_txt = re.sub(r"(?i)^\s*sku\s*[:#-]?\s*", "", sku_txt).strip()
    return _normalizar_sku_mes(sku_txt)

async def api_medias_compras_lista_pedido_importar_excel_precos(
    lista_id: str,
    file: UploadFile = File(...),
    confirmar_inclusoes: str = Form("0"),
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    conteudo = await file.read()
    if not conteudo:
        raise HTTPException(status_code=400, detail="Arquivo Excel vazio")

    try:
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True)
        ws = wb.active
    except Exception:
        raise HTTPException(status_code=400, detail="Arquivo invalido. Envie um Excel .xlsx")

    mapa_headers, linha_cabecalho = _mapear_cabecalho_lista_pedido_excel(ws)

    col_sku = mapa_headers.get("sku")
    col_vu = (
        mapa_headers.get("valorunidade")
        or mapa_headers.get("valorunitario")
        or mapa_headers.get("precounitario")
        or mapa_headers.get("preco")
        or mapa_headers.get("valor")
        or mapa_headers.get("cost")
        or mapa_headers.get("custo")
    )
    col_vt = (
        mapa_headers.get("valortotal")
        or mapa_headers.get("subtotalusd")
        or mapa_headers.get("subtotal")
        or mapa_headers.get("subtotaldolar")
        or mapa_headers.get("subtotalusd")
    )
    col_qtd = (
        mapa_headers.get("quantidade")
        or mapa_headers.get("qtd")
        or mapa_headers.get("qtde")
        or mapa_headers.get("qty")
        or mapa_headers.get("quantity")
    )
    col_titulo = (
        mapa_headers.get("titulodoprodutoemingles")
        or mapa_headers.get("titulo")
        or mapa_headers.get("descricao")
        or mapa_headers.get("descricaodoproduto")
        or mapa_headers.get("produto")
        or mapa_headers.get("description")
    )
    col_foto = mapa_headers.get("foto") or mapa_headers.get("imagem") or mapa_headers.get("picture")
    col_oem = mapa_headers.get("oem") or mapa_headers.get("oemmodel") or mapa_headers.get("modelo")
    col_cor_lado = (
        mapa_headers.get("colorside")
        or mapa_headers.get("corside")
        or mapa_headers.get("corlado")
        or mapa_headers.get("ladocor")
        or mapa_headers.get("lado")
        or mapa_headers.get("cor")
    )
    col_link = mapa_headers.get("link") or mapa_headers.get("url") or mapa_headers.get("linkaliexpress")
    col_cbm = mapa_headers.get("estimedcbm") or mapa_headers.get("estimatedcbm") or mapa_headers.get("cbm")
    col_peso = mapa_headers.get("estimedweigh") or mapa_headers.get("estimatedweight") or mapa_headers.get("peso") or mapa_headers.get("weight")
    col_embalagem = mapa_headers.get("individualpackaging") or mapa_headers.get("embalagemindividual") or mapa_headers.get("embalagem")

    if not col_sku:
        raise HTTPException(status_code=400, detail="Excel sem coluna SKU")
    if not col_vu and not col_vt:
        raise HTTPException(status_code=400, detail="Excel sem coluna de preÃ§o (Valor, Valor unidade ou Valor total)")

    confirmar = str(confirmar_inclusoes or "").strip().lower() in {"1", "true", "sim", "yes", "y"}

    def _normalizar_titulo_comparacao(texto: str) -> str:
        base = unicodedata.normalize("NFKD", str(texto or ""))
        base = "".join(ch for ch in base if not unicodedata.combining(ch))
        return re.sub(r"\s+", " ", base).strip().lower()

    def _preco_preenchido(v: float | None) -> bool:
        return v is not None and float(v) > 0

    atualizacoes = {}
    skus_sem_preco = []
    for row in range(linha_cabecalho + 1, ws.max_row + 1):
        sku = str(ws.cell(row=row, column=col_sku).value or "").strip()
        if _linha_resumo_excel_sku(sku):
            continue
        sku_norm = _normalizar_sku_lista_pedido_excel(sku)
        if not sku_norm:
            continue
        vu = _to_float_excel(ws.cell(row=row, column=col_vu).value) if col_vu else None
        vt = _to_float_excel(ws.cell(row=row, column=col_vt).value) if col_vt else None
        qtd = _to_float_excel(ws.cell(row=row, column=col_qtd).value) if col_qtd else None
        titulo = str(ws.cell(row=row, column=col_titulo).value or "").strip() if col_titulo else ""
        foto = str(ws.cell(row=row, column=col_foto).value or "").strip() if col_foto else ""
        oem = str(ws.cell(row=row, column=col_oem).value or "").strip() if col_oem else ""
        cor_lado = str(ws.cell(row=row, column=col_cor_lado).value or "").strip() if col_cor_lado else ""
        link = str(ws.cell(row=row, column=col_link).value or "").strip() if col_link else ""
        cbm = str(ws.cell(row=row, column=col_cbm).value or "").strip() if col_cbm else ""
        peso = str(ws.cell(row=row, column=col_peso).value or "").strip() if col_peso else ""
        embalagem = str(ws.cell(row=row, column=col_embalagem).value or "").strip() if col_embalagem else ""
        if vu is None and vt is None and qtd is None and not any([titulo, foto, oem, cor_lado, link, cbm, peso, embalagem]):
            continue
        if not _preco_preenchido(vu) and not _preco_preenchido(vt):
            skus_sem_preco.append(sku_norm)
            continue
        atualizacoes[sku_norm] = {
            "sku": sku_norm,
            "sku_original": sku,
            "vu": vu,
            "vt": vt,
            "qtd": qtd,
            "titulo": titulo,
            "foto": foto,
            "oem": oem,
            "cor_lado": cor_lado,
            "link": link,
            "cbm": cbm,
            "peso": peso,
            "embalagem": embalagem,
        }

    if skus_sem_preco:
        skus_unicos = list(dict.fromkeys(skus_sem_preco))
        amostra = ", ".join(skus_unicos[:12])
        sufixo = "" if len(skus_unicos) <= 12 else f" e mais {len(skus_unicos) - 12} SKU(s)"
        raise HTTPException(
            status_code=400,
            detail=(
                "Preço obrigatório: preencha Valor unidade ou Valor total para todos os produtos. "
                f"SKU(s) sem preÃ§o: {amostra}{sufixo}."
            ),
        )

    lista = listas[idx]
    itens = [_normalizar_item_lista_pedido(i) for i in (lista.get("itens") or [])]
    itens_por_sku = {}
    for item in itens:
        sku_norm_item = _normalizar_sku_lista_pedido_excel(item.get("SKU", ""))
        if sku_norm_item:
            itens_por_sku[sku_norm_item] = item

    pendencias = []
    aplicaveis = []
    skus_ignorados = []
    for sku_norm, novo in atualizacoes.items():
        item_existente = itens_por_sku.get(sku_norm)
        titulo_excel = str(novo.get("titulo") or "").strip()
        if not item_existente:
            pendencias.append({
                "tipo": "sku_novo",
                "sku": sku_norm,
                "titulo_arquivo": titulo_excel,
                "motivo": "SKU nÃ£o existia na lista original",
            })
            if confirmar:
                aplicaveis.append({"novo": novo, "item": None})
            else:
                skus_ignorados.append(sku_norm)
            continue

        titulo_atual = ""
        for chave_titulo in TITULO_PRODUTO_INGLES_KEYS_LEGADO:
            titulo_atual = str(item_existente.get(chave_titulo, "") or "").strip()
            if titulo_atual:
                break
        if titulo_excel and _normalizar_titulo_comparacao(titulo_excel) != _normalizar_titulo_comparacao(titulo_atual):
            pendencias.append({
                "tipo": "titulo_diferente",
                "sku": sku_norm,
                "titulo_atual": titulo_atual,
                "titulo_arquivo": titulo_excel,
                "motivo": "Título do arquivo é diferente do título atual da lista",
            })
        aplicaveis.append({"novo": novo, "item": item_existente})

    if pendencias and not confirmar and not aplicaveis:
        total_sku_novo = sum(1 for p in pendencias if p.get("tipo") == "sku_novo")
        total_titulo_diferente = sum(1 for p in pendencias if p.get("tipo") == "titulo_diferente")
        return {
            "success": False,
            "requer_confirmacao": True,
            "mensagem": "Foram encontradas divergências no arquivo. Confirme para incluir os itens com diferença.",
            "pendencias": pendencias,
            "resumo_pendencias": {
                "total": len(pendencias),
                "total_sku_novo": total_sku_novo,
                "total_titulo_diferente": total_titulo_diferente,
            },
            "total_skus_lidos": len(atualizacoes),
        }

    atualizados = 0
    quantidades_atualizadas = 0
    incluidos = 0
    skus_incluidos = []

    for registro in aplicaveis:
        novo = registro.get("novo") or {}
        item = registro.get("item")

        vu = novo.get("vu")
        vt = novo.get("vt")
        qtd_nova = novo.get("qtd")
        titulo_novo = str(novo.get("titulo") or "").strip()
        foto_nova = str(novo.get("foto") or "").strip()
        oem_novo = str(novo.get("oem") or "").strip()
        cor_lado_novo = str(novo.get("cor_lado") or "").strip()
        link_novo = str(novo.get("link") or "").strip()
        cbm_novo = str(novo.get("cbm") or "").strip()
        peso_novo = str(novo.get("peso") or "").strip()
        embalagem_nova = str(novo.get("embalagem") or "").strip()

        if item is None:
            qtd_val = int(max(0, round(float(qtd_nova or 0))))
            vu_val = round(float(vu or 0), 2)
            if vt is not None:
                vt_val = round(float(vt), 2)
            else:
                vt_val = round(qtd_val * vu_val, 2)

            novo_item = {
                "SKU": str(novo.get("sku") or "").strip(),
                "Foto": foto_nova,
                TITULO_PRODUTO_INGLES_KEY: titulo_novo,
                "OEM": oem_novo,
                COR_LADO_LISTA_PEDIDO_KEY: cor_lado_novo,
                "Link": link_novo,
                "Quantidade": qtd_val,
                "Valor unidade": vu_val,
                "Valor total": vt_val,
                CBM_LISTA_PEDIDO_KEY: cbm_novo,
                PESO_LISTA_PEDIDO_KEY: peso_novo,
                EMBALAGEM_LISTA_PEDIDO_KEY: embalagem_nova,
            }
            itens.append(novo_item)
            incluidos += 1
            skus_incluidos.append(str(novo_item.get("SKU", "") or "").strip())
            continue

        if qtd_nova is not None:
            qtd_int = int(max(0, round(float(qtd_nova))))
            if int(float(item.get("Quantidade", 0) or 0)) != qtd_int:
                quantidades_atualizadas += 1
            item["Quantidade"] = qtd_int

        qtd_item = float(item.get("Quantidade", 0) or 0)
        if vu is not None:
            item["Valor unidade"] = round(float(vu), 2)
            if vt is None:
                item["Valor total"] = round(qtd_item * float(vu), 2)
        if vt is not None:
            item["Valor total"] = round(float(vt), 2)

        if titulo_novo:
            item[TITULO_PRODUTO_INGLES_KEY] = titulo_novo
        if foto_nova:
            item["Foto"] = foto_nova
        if oem_novo:
            item["OEM"] = oem_novo
        if cor_lado_novo:
            item[COR_LADO_LISTA_PEDIDO_KEY] = cor_lado_novo
        if link_novo:
            item["Link"] = link_novo
        if cbm_novo:
            item[CBM_LISTA_PEDIDO_KEY] = cbm_novo
        if peso_novo:
            item[PESO_LISTA_PEDIDO_KEY] = peso_novo
        if embalagem_nova:
            item[EMBALAGEM_LISTA_PEDIDO_KEY] = embalagem_nova

        atualizados += 1

    lista["itens"] = _recalcular_frete_internacional_itens_lista(client_id, itens)
    lista["status"] = _normalizar_status_lista_pedido("Analisando orçamento")
    lista["updated_at"] = datetime.now().isoformat(timespec="seconds")
    listas[idx] = lista
    _salvar_listas_pedidos(client_id, listas)
    _limpar_cache_lista_pedido(client_id, str(lista.get("id", "") or ""), manter_versao=lista.get("updated_at"))

    return {
        "success": True,
        "lista": {
            **_resumo_lista_pedido(lista),
            "itens": lista.get("itens") or [],
        },
        "total_skus_lidos": len(atualizacoes),
        "total_skus_atualizados": int(atualizados),
        "total_quantidades_atualizadas": int(quantidades_atualizadas),
        "total_skus_incluidos": int(incluidos),
        "skus_incluidos": skus_incluidos,
        "total_skus_ignorados": int(len(skus_ignorados)),
        "skus_ignorados": skus_ignorados,
        "pendencias_confirmadas": len(pendencias) if confirmar else 0,
    }


async def api_medias_compras_lista_pedido_importar_excel_nova_lista(
    file: UploadFile = File(...),
    nome_lista: str = Form(""),
    loja: str = Form("__todas"),
    client_id: str = Depends(medias_common.get_tenant_id),
):
    conteudo = await file.read()
    if not conteudo:
        raise HTTPException(status_code=400, detail="Arquivo Excel vazio")

    try:
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True)
        ws = wb.active
    except Exception:
        raise HTTPException(status_code=400, detail="Arquivo invalido. Envie um Excel .xlsx")

    mapa_headers, linha_cabecalho = _mapear_cabecalho_lista_pedido_excel(ws)

    col_sku = mapa_headers.get("sku")
    col_vu = (
        mapa_headers.get("valorunidade")
        or mapa_headers.get("valorunitario")
        or mapa_headers.get("precounitario")
        or mapa_headers.get("preco")
        or mapa_headers.get("valor")
        or mapa_headers.get("cost")
        or mapa_headers.get("custo")
    )
    col_vt = (
        mapa_headers.get("valortotal")
        or mapa_headers.get("subtotalusd")
        or mapa_headers.get("subtotal")
        or mapa_headers.get("subtotaldolar")
    )
    col_qtd = (
        mapa_headers.get("quantidade")
        or mapa_headers.get("qtd")
        or mapa_headers.get("qtde")
        or mapa_headers.get("qty")
        or mapa_headers.get("quantity")
    )
    col_titulo = (
        mapa_headers.get("titulodoprodutoemingles")
        or mapa_headers.get("titulo")
        or mapa_headers.get("descricao")
        or mapa_headers.get("descricaodoproduto")
        or mapa_headers.get("produto")
        or mapa_headers.get("description")
    )
    col_foto = mapa_headers.get("foto") or mapa_headers.get("imagem") or mapa_headers.get("picture")
    col_oem = mapa_headers.get("oem") or mapa_headers.get("oemmodel") or mapa_headers.get("modelo")
    col_cor_lado = (
        mapa_headers.get("colorside")
        or mapa_headers.get("corside")
        or mapa_headers.get("corlado")
        or mapa_headers.get("ladocor")
        or mapa_headers.get("lado")
        or mapa_headers.get("cor")
    )
    col_link = mapa_headers.get("link") or mapa_headers.get("url") or mapa_headers.get("linkaliexpress")
    col_cbm = mapa_headers.get("estimedcbm") or mapa_headers.get("estimatedcbm") or mapa_headers.get("cbm")
    col_peso = mapa_headers.get("estimedweigh") or mapa_headers.get("estimatedweight") or mapa_headers.get("peso") or mapa_headers.get("weight")
    col_embalagem = mapa_headers.get("individualpackaging") or mapa_headers.get("embalagemindividual") or mapa_headers.get("embalagem")

    if not col_sku:
        raise HTTPException(status_code=400, detail="Excel sem coluna SKU")
    itens_por_sku = {}

    for row in range(linha_cabecalho + 1, ws.max_row + 1):
        sku_raw = str(ws.cell(row=row, column=col_sku).value or "").strip()
        if _linha_resumo_excel_sku(sku_raw):
            continue
        sku = _normalizar_sku_lista_pedido_excel(sku_raw)
        if not sku:
            continue

        vu = _to_float_excel(ws.cell(row=row, column=col_vu).value) if col_vu else None
        vt = _to_float_excel(ws.cell(row=row, column=col_vt).value) if col_vt else None
        qtd = _to_float_excel(ws.cell(row=row, column=col_qtd).value) if col_qtd else None
        titulo = str(ws.cell(row=row, column=col_titulo).value or "").strip() if col_titulo else ""
        foto = str(ws.cell(row=row, column=col_foto).value or "").strip() if col_foto else ""
        oem = str(ws.cell(row=row, column=col_oem).value or "").strip() if col_oem else ""
        cor_lado = str(ws.cell(row=row, column=col_cor_lado).value or "").strip() if col_cor_lado else ""
        link = str(ws.cell(row=row, column=col_link).value or "").strip() if col_link else ""
        cbm = str(ws.cell(row=row, column=col_cbm).value or "").strip() if col_cbm else ""
        peso = str(ws.cell(row=row, column=col_peso).value or "").strip() if col_peso else ""
        embalagem = str(ws.cell(row=row, column=col_embalagem).value or "").strip() if col_embalagem else ""

        if vu is None and vt is None and qtd is None and not any([titulo, foto, oem, cor_lado, link, cbm, peso, embalagem]):
            continue

        qtd_int = int(max(0.0, round(float(qtd or 0))))
        valor_unidade = float(vu or 0)
        valor_total = float(vt or 0)

        if valor_unidade <= 0 and valor_total > 0 and qtd_int > 0:
            valor_unidade = valor_total / qtd_int
        if valor_total <= 0 and valor_unidade > 0 and qtd_int > 0:
            valor_total = valor_unidade * qtd_int

        itens_por_sku[sku] = {
            "SKU": sku,
            "Foto": foto,
            TITULO_PRODUTO_INGLES_KEY: titulo,
            "OEM": oem,
            COR_LADO_LISTA_PEDIDO_KEY: cor_lado,
            "Link": link,
            "Quantidade": qtd_int,
            "Valor unidade": round(max(0.0, valor_unidade), 2),
            "Valor total": round(max(0.0, valor_total), 2),
            CBM_LISTA_PEDIDO_KEY: cbm,
            PESO_LISTA_PEDIDO_KEY: peso,
            EMBALAGEM_LISTA_PEDIDO_KEY: embalagem,
        }

    itens_lidos = list(itens_por_sku.values())
    if not itens_lidos:
        raise HTTPException(status_code=400, detail="Nenhum SKU valido encontrado para importar")

    nome_lista_final = str(nome_lista or "").strip()
    if not nome_lista_final:
        nome_arquivo = str(getattr(file, "filename", "") or "").strip()
        nome_arquivo = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", nome_arquivo).strip()
        nome_lista_final = nome_arquivo or f"Lista importada {datetime.now().strftime('%d/%m/%Y %H:%M')}"

    agora_iso = datetime.now().isoformat(timespec="seconds")
    lista_id = str(uuid.uuid4())
    itens_norm = _recalcular_frete_internacional_itens_lista(client_id, itens_lidos)
    loja_final = str(loja or "__todas").strip() or "__todas"

    listas_salvas = _carregar_listas_pedidos(client_id)
    status_importacao = _normalizar_status_lista_pedido("Analisando orçamento")
    listas_salvas.insert(0, {
        "id": lista_id,
        "nome_lista": nome_lista_final,
        "loja": loja_final,
        "status": status_importacao,
        "created_at": agora_iso,
        "updated_at": agora_iso,
        "origem": "importacao_excel",
        "itens": itens_norm,
    })
    _salvar_listas_pedidos(client_id, listas_salvas)
    _limpar_cache_lista_pedido(client_id, lista_id, manter_versao=agora_iso)

    return {
        "success": True,
        "mensagem": "Lista criada com sucesso a partir do Excel.",
        "lista": {
            "id": lista_id,
            "nome_lista": nome_lista_final,
            "loja": loja_final,
            "status": status_importacao,
            "created_at": agora_iso,
            "updated_at": agora_iso,
            "itens": itens_norm,
            "total_itens": len(itens_norm),
            "total_quantidade": int(sum(float(i.get("Quantidade", 0) or 0) for i in itens_norm)),
        },
    }



def configure_medias_compras_importacao_runtime(runtime_module=None):
    return _configure_runtime_globals(runtime_module)


configure_medias_compras_importacao_runtime()

__all__ = [
    "configure_medias_compras_importacao_runtime",
    "api_medias_compras_lista_pedido_importar_excel_precos",
    "api_medias_compras_lista_pedido_importar_excel_nova_lista",
]
