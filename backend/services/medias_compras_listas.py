"""Pedido-list endpoints and Excel imports for Medias Compras."""

from __future__ import annotations

import io
import json
import os
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from backend.schemas import (
    ListaPedidoAddSkuRequest,
    ListaPedidoPreferenciasColunasRequest,
    ListaPedidoStatusRequest,
    ListaPedidoUpdateRequest,
    MediasComprasSkusOcultosRequest,
)
from backend.services import medias_compras_common as medias_common
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


async def api_medias_compras_listas_pedidos(
    loja: str = "__todas",
    client_id: str = Depends(medias_common.get_tenant_id)
):
    listas = _carregar_listas_pedidos(client_id)
    loja_sel = str(loja or "").strip().lower()
    if loja_sel and loja_sel != "__todas":
        listas = [
            l for l in listas
            if str((l or {}).get("loja") or "").strip().lower() == loja_sel
        ]
    m3_lookup = _construir_mapa_m3_sku(client_id)
    return {
        "success": True,
        "listas": [_resumo_lista_pedido(l, m3_lookup) for l in listas],
    }


async def api_medias_compras_skus_ocultos_get(
    request: Request,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    username = _extrair_username_do_request(request)
    prefs = _carregar_preferencias_skus_ocultos_medias(client_id, username)
    return {
        "success": True,
        "skus_ocultos": prefs.get("skus_ocultos") or [],
        "updated_at": prefs.get("updated_at"),
    }


async def api_medias_compras_skus_ocultos_put(
    req: MediasComprasSkusOcultosRequest,
    request: Request,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    username = _extrair_username_do_request(request)
    payload = _salvar_preferencias_skus_ocultos_medias(client_id, username, req.skus_ocultos or [])
    return {
        "success": True,
        "skus_ocultos": payload.get("skus_ocultos") or [],
        "updated_at": payload.get("updated_at"),
    }


async def api_medias_compras_preferencias_colunas_get(client_id: str = Depends(medias_common.get_tenant_id)):
    prefs = _carregar_preferencias_colunas_importacoes(client_id)
    return {
        "success": True,
        "ordem_colunas": prefs.get("ordem_colunas") or [],
        "larguras_colunas": prefs.get("larguras_colunas") or {},
    }


async def api_medias_compras_preferencias_colunas_put(
    req: ListaPedidoPreferenciasColunasRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    payload = _salvar_preferencias_colunas_importacoes(client_id, {
        "ordem_colunas": req.ordem_colunas or [],
        "larguras_colunas": req.larguras_colunas or {},
    })
    return {
        "success": True,
        "ordem_colunas": payload.get("ordem_colunas") or [],
        "larguras_colunas": payload.get("larguras_colunas") or {},
        "updated_at": payload.get("updated_at"),
    }


async def api_medias_compras_concorrentes_links(
    sku: str,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    sku_in = str(sku or "").strip()
    if not sku_in:
        raise HTTPException(status_code=400, detail="SKU ÃƒÂ© obrigatÃƒÂ³rio")

    try:
        gc = autenticar_google_sheets()
        if not gc:
            raise HTTPException(status_code=500, detail="NÃƒÂ£o foi possÃƒÂ­vel autenticar no Google Sheets")

        sh = gc.open_by_key(SPREADSHEET_ID_CONCORRENTES)
        ws = sh.get_worksheet(0)
        if ws is None:
            raise HTTPException(status_code=404, detail="Aba da planilha nÃ£o encontrada")

        rows = ws.get_all_values()
        if not rows:
            return {
                "success": True,
                "sku": sku_in,
                "concorrentes": {
                    "concorrente_1": "",
                    "concorrente_2": "",
                    "concorrente_3": "",
                    "concorrente_4": "",
                    "concorrente_5": "",
                },
                "concorrentes_valores": {
                    "concorrente_1": "",
                    "concorrente_2": "",
                    "concorrente_3": "",
                    "concorrente_4": "",
                    "concorrente_5": "",
                },
            }

        header = rows[0] if rows else []

        def _norm(v: str) -> str:
            txt = str(v or "").strip().upper().replace(" ", "")
            txt = re.sub(r"\.0+$", "", txt)
            return txt

        sku_col_idx = 0
        for i, h in enumerate(header):
            h_norm = str(h or "").strip().lower()
            if h_norm in {"sku", "cÃƒÂ³digo", "codigo", "codigo sku", "sku code"}:
                sku_col_idx = i
                break

        sku_target = _norm(sku_in)
        row_match = None
        for row in rows[1:]:
            if sku_col_idx >= len(row):
                continue
            if _norm(row[sku_col_idx]) == sku_target:
                row_match = row
                break

        if row_match is None:
            return {
                "success": True,
                "sku": sku_in,
                "concorrentes": {
                    "concorrente_1": "",
                    "concorrente_2": "",
                    "concorrente_3": "",
                    "concorrente_4": "",
                    "concorrente_5": "",
                },
                "concorrentes_valores": {
                    "concorrente_1": "",
                    "concorrente_2": "",
                    "concorrente_3": "",
                    "concorrente_4": "",
                    "concorrente_5": "",
                },
            }

        def _get_col(row: list[str], idx: int) -> str:
            if idx < 0 or idx >= len(row):
                return ""
            return str(row[idx] or "").strip()

        # Colunas E, I, M, Q, U (0-based: 4, 8, 12, 16, 20)
        links = {
            "concorrente_1": _get_col(row_match, 4),
            "concorrente_2": _get_col(row_match, 8),
            "concorrente_3": _get_col(row_match, 12),
            "concorrente_4": _get_col(row_match, 16),
            "concorrente_5": _get_col(row_match, 20),
        }

        # Colunas F, J, N, R, V (0-based: 5, 9, 13, 17, 21)
        valores = {
            "concorrente_1": _get_col(row_match, 5),
            "concorrente_2": _get_col(row_match, 9),
            "concorrente_3": _get_col(row_match, 13),
            "concorrente_4": _get_col(row_match, 17),
            "concorrente_5": _get_col(row_match, 21),
        }

        return {
            "success": True,
            "sku": sku_in,
            "concorrentes": links,
            "concorrentes_valores": valores,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Erro ao buscar links de concorrentes para SKU '{sku_in}': {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar links de concorrentes")


async def api_medias_compras_lista_pedido_detalhe(lista_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    alvo = listas[idx] if idx >= 0 else None
    if not alvo:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    # Backfill: garante coluna persistida de frete internacional para listas antigas.
    itens_recalculados = _recalcular_frete_internacional_itens_lista(client_id, alvo.get("itens") or [])
    if json.dumps(itens_recalculados, ensure_ascii=False, sort_keys=True) != json.dumps(alvo.get("itens") or [], ensure_ascii=False, sort_keys=True):
        alvo["itens"] = itens_recalculados
        alvo["updated_at"] = datetime.now().isoformat(timespec="seconds")
        listas[idx] = alvo
        _salvar_listas_pedidos(client_id, listas)
        _limpar_cache_lista_pedido(client_id, str(alvo.get("id", "") or ""), manter_versao=alvo.get("updated_at"))

    return {
        "success": True,
        "lista": {
            **_resumo_lista_pedido(alvo),
            "itens": itens_recalculados,
        }
    }


async def api_medias_compras_lista_pedido_editar(
    lista_id: str,
    req: ListaPedidoUpdateRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    lista = listas[idx]
    campos_informados = (
        getattr(req, "model_fields_set", None)
        or getattr(req, "__fields_set__", set())
        or set()
    )
    alterou = False
    if req.nome_lista is not None:
        nome_lista = str(req.nome_lista or "").strip()
        if nome_lista:
            lista["nome_lista"] = nome_lista
            alterou = True
    if req.status is not None:
        lista["status"] = _normalizar_status_lista_pedido(req.status)
        alterou = True
    if "loja" in campos_informados:
        loja = str(req.loja or "").strip()
        lista["loja"] = loja if loja and loja.lower() != "__todas" else "__todas"
        alterou = True

    if "itens" in campos_informados:
        itens_norm = _recalcular_frete_internacional_itens_lista(client_id, req.itens or [])
        lista["itens"] = itens_norm
        alterou = True

    if alterou:
        lista["updated_at"] = datetime.now().isoformat(timespec="seconds")
    listas[idx] = lista
    _salvar_listas_pedidos(client_id, listas)
    if alterou:
        _limpar_cache_lista_pedido(client_id, str(lista.get("id", "") or ""), manter_versao=lista.get("updated_at"))

    return {
        "success": True,
        "lista": {
            **_resumo_lista_pedido(lista),
            "itens": lista.get("itens") or [],
        }
    }


async def api_medias_compras_lista_pedido_adicionar_sku(
    lista_id: str,
    req: ListaPedidoAddSkuRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    sku_input = str(req.sku or "").strip()
    sku_in = _normalizar_sku_mes(sku_input)
    if not sku_in:
        raise HTTPException(status_code=400, detail="SKU invalido")

    # Regra solicitada: aceitar apenas SKU numÃƒÂ©rico existente no cadastro.
    sku_input_compacto = re.sub(r"\s+", "", sku_input)
    if not re.fullmatch(r"\d+", sku_input_compacto or ""):
        raise HTTPException(status_code=400, detail="Somente SKU numÃƒÂ©rico ÃƒÂ© permitido nesta inclusÃƒÂ£o")

    qtd = max(0, int(round(float(req.quantidade or 0))))
    if qtd <= 0:
        raise HTTPException(status_code=400, detail="Quantidade deve ser maior que zero")

    vu = round(float(req.valor_unitario or 0), 2)
    if vu <= 0:
        raise HTTPException(status_code=400, detail="Valor unitÃƒÂ¡rio deve ser maior que zero")

    # Busca dados do cadastro no backend para garantir consistÃƒÂªncia da inclusÃƒÂ£o.
    try:
        prod_resp = await obter_produto_cadastro_query(sku=sku_in, client_id=client_id)
        produto = (prod_resp or {}).get("produto") or {}
    except HTTPException:
        # Fallback: busca por comparacao normalizada na listagem inteira do cadastro.
        produtos = await listar_produtos_cadastro(client_id=client_id)
        alvo_cmp = re.sub(r"[^A-Za-z0-9]", "", sku_in).upper()
        produto = None
        for p in (produtos or []):
            sku_p = _normalizar_sku_mes(str((p or {}).get("sku") or ""))
            sku_cmp = re.sub(r"[^A-Za-z0-9]", "", sku_p).upper()
            if sku_cmp and sku_cmp == alvo_cmp:
                produto = p
                break
        if not produto:
            raise HTTPException(status_code=404, detail="SKU nÃ£o encontrado no cadastro")

    def _norm_prod_key(chave: str) -> str:
        txt = unicodedata.normalize("NFKD", str(chave or ""))
        txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
        txt = txt.lower().replace("_", " ").strip()
        txt = re.sub(r"\s+", " ", txt)
        txt = re.sub(r"[^a-z0-9 ]", "", txt)
        txt = txt.strip()
        if txt.startswith("cg "):
            txt = txt[3:].strip()
        return txt

    def _pick_prod(cands: list[str]) -> str:
        if not isinstance(produto, dict):
            return ""

        mapa = {}
        for k, v in produto.items():
            if v is None or not str(v).strip():
                continue
            nk = _norm_prod_key(str(k or ""))
            if nk and nk not in mapa:
                mapa[nk] = str(v).strip()

        for c in cands:
            key = _norm_prod_key(str(c or ""))
            if not key:
                continue
            val = mapa.get(key)
            if val:
                return val

            # Fallback por inclusÃƒÂ£o para colunas longas/variantes importadas de planilha.
            for mk, mv in mapa.items():
                if mk == key or key in mk or mk in key:
                    if mv:
                        return mv
        return ""

    sku_final = _normalizar_sku_mes(_pick_prod(["sku"]) or sku_in)
    if not re.fullmatch(r"\d+", str(sku_final or "")):
        raise HTTPException(status_code=400, detail="SKU do cadastro nÃ£o ÃƒÂ© numÃƒÂ©rico")
    titulo = _pick_prod([
        "produto bling", "produtos bling", "produto blig", "produtos blig",
        "titulo do produto em ingles", "titulo do produto em ingles",
        "titulo em ingles", "titulo", "product name", "nome", "produto",
        "traducao ptbr ou nome na bling"
    ])
    foto = _resolver_foto_cadastro_sku(client_id, sku_final, _pick_prod(["foto", "imagem", "url foto", "link foto"]))
    oem = _pick_prod(["oem", "codigo oem", "part number", "oem model", "oem model"])
    cor_lado = _pick_prod(["color side", "color/side", "cor lado", "cor/lado", "lado cor", "lado/cor", "lado", "cor", "color", "side"])
    link = _pick_prod(["link", "url", "link aliexpress", "url aliexpress", "mlb principal", "url ml"])

    lista = listas[idx]
    itens = [_normalizar_item_lista_pedido(i) for i in (lista.get("itens") or [])]
    idx_existente = next((i for i, it in enumerate(itens) if _normalizar_sku_mes(it.get("SKU", "")) == sku_final), -1)

    item_novo = {
        "SKU": sku_final,
        "Foto": foto,
        TITULO_PRODUTO_INGLES_KEY: titulo,
        "OEM": oem,
        COR_LADO_LISTA_PEDIDO_KEY: cor_lado,
        "Link": link,
        "Quantidade": qtd,
        "Valor unidade": vu,
        "Valor total": round(qtd * vu, 2),
    }

    acao = "adicionado"
    if idx_existente >= 0:
        itens[idx_existente] = _normalizar_item_lista_pedido({**itens[idx_existente], **item_novo})
        acao = "atualizado"
    else:
        itens.append(_normalizar_item_lista_pedido(item_novo))

    lista["itens"] = _recalcular_frete_internacional_itens_lista(client_id, itens)
    lista["updated_at"] = datetime.now().isoformat(timespec="seconds")
    listas[idx] = lista
    _salvar_listas_pedidos(client_id, listas)
    _limpar_cache_lista_pedido(client_id, str(lista.get("id", "") or ""), manter_versao=lista.get("updated_at"))

    return {
        "success": True,
        "acao": acao,
        "sku": sku_final,
        "lista": {
            **_resumo_lista_pedido(lista),
            "itens": lista.get("itens") or [],
        },
    }


async def api_medias_compras_lista_pedido_atualizar_status(
    lista_id: str,
    req: ListaPedidoStatusRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    lista = listas[idx]
    lista["status"] = _normalizar_status_lista_pedido(req.status)
    lista["updated_at"] = datetime.now().isoformat(timespec="seconds")
    listas[idx] = lista
    _salvar_listas_pedidos(client_id, listas)
    _limpar_cache_lista_pedido(client_id, str(lista.get("id", "") or ""), manter_versao=lista.get("updated_at"))

    return {
        "success": True,
        "lista": {
            **_resumo_lista_pedido(lista),
            "itens": lista.get("itens") or [],
        }
    }

def _lista_pedido_esta_em_importacoes(lista: dict[str, Any] | None) -> bool:
    status = unicodedata.normalize("NFD", str((lista or {}).get("status", "") or ""))
    status = "".join(ch for ch in status if unicodedata.category(ch) != "Mn")
    return status.strip().lower() == "analisando orcamento"


async def api_medias_compras_lista_pedido_excluir(
    lista_id: str,
    request: Request,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    lista = listas[idx]
    origem_delete = str(request.headers.get("x-jk-delete-origin", "") or "").strip().lower()
    if _lista_pedido_esta_em_importacoes(lista) and origem_delete != "importacoes":
        raise HTTPException(
            status_code=409,
            detail="Listas em Importacoes so podem ser excluidas pelo modulo Importacoes.",
        )

    removida = listas.pop(idx)
    _salvar_listas_pedidos(client_id, listas)
    _limpar_cache_lista_pedido(client_id, str(removida.get("id", "") or ""))
    return {
        "success": True,
        "lista_removida": {
            "id": str(removida.get("id", "") or ""),
            "nome_lista": str(removida.get("nome_lista", "") or ""),
        }
    }


async def api_medias_compras_lista_pedido_download(lista_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    listas = _carregar_listas_pedidos(client_id)
    lista = next((l for l in listas if str(l.get("id", "")) == str(lista_id)), None)
    if not lista:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    nome_lista = str(lista.get("nome_lista", "lista_pedido") or "lista_pedido").strip()
    nome_base = re.sub(r"[^A-Za-z0-9_-]+", "_", nome_lista).strip("_") or "lista_pedido"
    nome_arquivo = f"{nome_base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    versao_lista = str(lista.get("updated_at") or lista.get("created_at") or "")
    cache_key = f"{client_id}:{lista_id}:{versao_lista}"
    caminho_cache = _arquivo_cache_lista_pedido(client_id, str(lista_id), versao_lista)

    # Cache persistente em disco (mais estÃƒÂ¡vel entre requisiÃƒÂ§ÃƒÂµes/processos).
    if os.path.exists(caminho_cache):
        headers = {
            "Content-Disposition": f"attachment; filename={nome_arquivo}"
        }
        return FileResponse(
            caminho_cache,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=nome_arquivo,
            headers=headers,
        )

    file_bytes = LISTA_PEDIDO_XLSX_CACHE.get(cache_key)
    if not file_bytes:
        file_bytes = _gerar_excel_lista_pedido_bytes(nome_lista, lista.get("itens") or [], client_id=client_id)
        LISTA_PEDIDO_XLSX_CACHE[cache_key] = file_bytes
        # MantÃ©m cache pequeno para evitar crescimento sem controle.
        while len(LISTA_PEDIDO_XLSX_CACHE) > LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS:
            try:
                LISTA_PEDIDO_XLSX_CACHE.pop(next(iter(LISTA_PEDIDO_XLSX_CACHE)))
            except Exception:
                break

    try:
        with open(caminho_cache, "wb") as f:
            f.write(file_bytes)
        _limpar_cache_lista_pedido(client_id, str(lista_id), manter_versao=versao_lista)
    except Exception:
        pass

    headers = {
        "Content-Disposition": f"attachment; filename={nome_arquivo}"
    }
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


async def api_medias_compras_lista_pedido_gerar_download(lista_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    listas = _carregar_listas_pedidos(client_id)
    lista = next((l for l in listas if str(l.get("id", "")) == str(lista_id)), None)
    if not lista:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    nome_lista = str(lista.get("nome_lista", "lista_pedido") or "lista_pedido").strip()
    nome_base = re.sub(r"[^A-Za-z0-9_-]+", "_", nome_lista).strip("_") or "lista_pedido"
    nome_arquivo = f"{nome_base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    versao_lista = str(lista.get("updated_at") or lista.get("created_at") or "")
    cache_key = f"{client_id}:{lista_id}:{versao_lista}"
    caminho_cache = _arquivo_cache_lista_pedido(client_id, str(lista_id), versao_lista)

    if os.path.exists(caminho_cache):
        return {
            "success": True,
            "download_url": f"/api/medias-compras/listas-pedidos/{lista_id}/download",
            "filename": nome_arquivo,
            "lista_id": str(lista.get("id", "") or ""),
        }

    file_bytes = None
    file_bytes = LISTA_PEDIDO_XLSX_CACHE.get(cache_key)

    if file_bytes is None:
        file_bytes = _gerar_excel_lista_pedido_bytes(nome_lista, lista.get("itens") or [], client_id=client_id)
        LISTA_PEDIDO_XLSX_CACHE[cache_key] = file_bytes
        while len(LISTA_PEDIDO_XLSX_CACHE) > LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS:
            try:
                LISTA_PEDIDO_XLSX_CACHE.pop(next(iter(LISTA_PEDIDO_XLSX_CACHE)))
            except Exception:
                break
        _salvar_bytes_cache_lista_pedido(client_id, str(lista_id), versao_lista, file_bytes)

    file_id = str(uuid.uuid4())
    TEMP_FILES_STORAGE[file_id] = file_bytes
    TEMP_FILES_META[file_id] = {
        "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "filename": nome_arquivo,
    }
    return {
        "success": True,
        "file_id": file_id,
        "filename": nome_arquivo,
        "lista_id": str(lista.get("id", "") or ""),
    }




async def api_medias_compras_download(file_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    file_bytes = TEMP_FILES_STORAGE.get(file_id)
    if not file_bytes:
        raise HTTPException(status_code=404, detail="Arquivo nÃ£o encontrado ou expirado")

    meta = TEMP_FILES_META.get(file_id, {})
    media_type = meta.get("mime") or "application/octet-stream"
    filename = meta.get("filename") or f"arquivo_{file_id}.xlsx"

    headers = {
        "Content-Disposition": f"attachment; filename={filename}"
    }
    return StreamingResponse(io.BytesIO(file_bytes), media_type=media_type, headers=headers)



def configure_medias_compras_listas_runtime(runtime_module=None):
    return _configure_runtime_globals(runtime_module)


configure_medias_compras_listas_runtime()

__all__ = [
    "configure_medias_compras_listas_runtime",
    "api_medias_compras_listas_pedidos",
    "api_medias_compras_skus_ocultos_get",
    "api_medias_compras_skus_ocultos_put",
    "api_medias_compras_preferencias_colunas_get",
    "api_medias_compras_preferencias_colunas_put",
    "api_medias_compras_concorrentes_links",
    "api_medias_compras_lista_pedido_detalhe",
    "api_medias_compras_lista_pedido_editar",
    "api_medias_compras_lista_pedido_adicionar_sku",
    "api_medias_compras_lista_pedido_atualizar_status",
    "api_medias_compras_lista_pedido_excluir",
    "api_medias_compras_lista_pedido_download",
    "api_medias_compras_lista_pedido_gerar_download",
    "api_medias_compras_download",
]
