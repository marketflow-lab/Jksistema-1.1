"""Shared helpers and runtime context for Medias Compras."""

from __future__ import annotations

import inspect
import io
import json
import logging
import os
import re
import sqlite3
import unicodedata
from datetime import datetime
from typing import Any, Optional

import openpyxl
import pandas as pd
from fastapi import Header, Request
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl.cell.cell import MergedCell
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from backend.services.cadastro_common import _normalizar_sku_mes
from backend.services.runtime_bridge import bind_runtime_globals, current_backend_runtime

logger = logging.getLogger("jk_sistema")
_get_tenant_id_fn = None
_get_tenant_path_fn = None
_runtime = None
TEMP_FILES_STORAGE: dict[str, bytes] = {}
TEMP_FILES_META: dict[str, dict] = {}
LISTA_PEDIDO_XLSX_CACHE: dict[str, bytes] = {}
LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS = 40
PASTA_INFO = ""
TITULO_PRODUTO_INGLES_KEY = "Título do produto em inglês"
TITULO_PRODUTO_INGLES_KEYS_LEGADO = (
    TITULO_PRODUTO_INGLES_KEY,
    "TÃ­tulo do produto em inglÃªs",
    "TÃƒÂ­tulo do produto em inglÃƒÆ’Ã‚Âªs",
    "TÃƒÂ­tulo do produto em inglÃƒÂªs",
)
COR_LADO_LISTA_PEDIDO_KEY = "Color/side"
CBM_LISTA_PEDIDO_KEY = "Estimed CBM"
PESO_LISTA_PEDIDO_KEY = "Estimed Weigh"
EMBALAGEM_LISTA_PEDIDO_KEY = "Individual packaging"
C54_HEADERS_LISTA_PEDIDO = (
    "SKU",
    "Picture",
    "Description",
    "OEM/ Model",
    "Color/side",
    "Link Aliexpress",
    "quantity",
    "Cost",
    "Sub-total(USD)",
    "Estimed CBM",
    "Estimed Weigh",
    "Individual packaging",
)


def _sku_lookup_keys_sync_ncm(sku_val: str) -> tuple[str, str, str]:
    sku_norm = _normalizar_sku_mes(str(sku_val or "").strip())
    if re.match(r"^\d+\.0+$", sku_norm):
        sku_norm = str(int(float(sku_norm)))
    sku_compacto = re.sub(r"[^A-Z0-9]", "", sku_norm.upper())
    partes = re.split(r"([0-9]+)", sku_norm.upper())
    sku_numsoft = "".join(str(int(p)) if p.isdigit() else p for p in partes)
    sku_numsoft_compacto = re.sub(r"[^A-Z0-9]", "", sku_numsoft)
    return sku_norm, sku_compacto, sku_numsoft_compacto


def configure_medias_compras_common_runtime(runtime_module=None):
    runtime = runtime_module or current_backend_runtime()
    global logger, _get_tenant_id_fn, _get_tenant_path_fn, _runtime
    global TEMP_FILES_STORAGE, TEMP_FILES_META, LISTA_PEDIDO_XLSX_CACHE, LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS, PASTA_INFO
    _runtime = runtime
    bind_runtime_globals(globals(), runtime)
    if runtime is not None:
        runtime_logger = getattr(runtime, "logger", None)
        if runtime_logger is not None:
            logger = runtime_logger
        if hasattr(runtime, "get_tenant_id"):
            _get_tenant_id_fn = getattr(runtime, "get_tenant_id")
        if hasattr(runtime, "get_tenant_path"):
            _get_tenant_path_fn = getattr(runtime, "get_tenant_path")
        if hasattr(runtime, "TEMP_FILES_STORAGE"):
            TEMP_FILES_STORAGE = getattr(runtime, "TEMP_FILES_STORAGE")
        if hasattr(runtime, "TEMP_FILES_META"):
            TEMP_FILES_META = getattr(runtime, "TEMP_FILES_META")
        if hasattr(runtime, "LISTA_PEDIDO_XLSX_CACHE"):
            LISTA_PEDIDO_XLSX_CACHE = getattr(runtime, "LISTA_PEDIDO_XLSX_CACHE")
        if hasattr(runtime, "LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS"):
            LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS = getattr(runtime, "LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS")
        if hasattr(runtime, "PASTA_INFO"):
            PASTA_INFO = getattr(runtime, "PASTA_INFO")
    return runtime


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    if not callable(_get_tenant_id_fn):
        raise RuntimeError("Medias Compras runtime was not configured.")
    result = _get_tenant_id_fn(request, authorization)
    if inspect.isawaitable(result):
        return await result
    return result


def get_tenant_path(client_id: str) -> str:
    if not callable(_get_tenant_path_fn):
        raise RuntimeError("Medias Compras runtime was not configured.")
    return _get_tenant_path_fn(client_id)


def _to_float(valor, padrao: float = 0.0) -> float:
    if isinstance(valor, (int, float)):
        return float(valor)
    txt = str(valor if valor is not None else "").strip()
    if not txt:
        return float(padrao)
    txt = txt.replace("\u00a0", " ")
    txt = re.sub(r"(?i)(r\$|us\$|usd|brl|\$)", "", txt)
    txt = re.sub(r"[^0-9,.\-]+", "", txt.replace(" ", ""))
    if txt in {"", "-", ".", ","}:
        return float(padrao)
    if "," in txt and "." in txt:
        if txt.rfind(",") > txt.rfind("."):
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", "")
    elif "," in txt:
        txt = txt.replace(",", ".")
    try:
        return float(txt)
    except Exception:
        return float(padrao)


def _normalizar_codigo_fiscal(valor: str) -> str:
    return re.sub(r"\D", "", str(valor or "").strip())


def _arquivo_listas_pedidos(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "listas_pedidos.json")


def _arquivo_preferencias_colunas_importacoes(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "importacoes_lista_colunas_prefs.json")


def _carregar_preferencias_colunas_importacoes(client_id: str) -> dict:
    caminho = _arquivo_preferencias_colunas_importacoes(client_id)
    if not os.path.exists(caminho):
        return {"ordem_colunas": [], "larguras_colunas": {}}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        if not isinstance(dados, dict):
            return {"ordem_colunas": [], "larguras_colunas": {}}

        ordem = dados.get("ordem_colunas")
        larguras = dados.get("larguras_colunas")
        return {
            "ordem_colunas": ordem if isinstance(ordem, list) else [],
            "larguras_colunas": larguras if isinstance(larguras, dict) else {},
        }
    except Exception:
        return {"ordem_colunas": [], "larguras_colunas": {}}


def _salvar_preferencias_colunas_importacoes(client_id: str, preferencias: dict) -> dict:
    caminho = _arquivo_preferencias_colunas_importacoes(client_id)
    pasta = os.path.dirname(caminho)
    if pasta and not os.path.exists(pasta):
        os.makedirs(pasta, exist_ok=True)

    ordem_raw = (preferencias or {}).get("ordem_colunas")
    larguras_raw = (preferencias or {}).get("larguras_colunas")

    ordem: list[str] = []
    if isinstance(ordem_raw, list):
        ordem = [str(v or "").strip() for v in ordem_raw if str(v or "").strip()]

    larguras: dict[str, int] = {}
    if isinstance(larguras_raw, dict):
        for k, v in larguras_raw.items():
            key = str(k or "").strip()
            if not key:
                continue
            try:
                width = int(float(v))
            except Exception:
                continue
            larguras[key] = max(12, min(1200, width))

    payload = {
        "ordem_colunas": ordem,
        "larguras_colunas": larguras,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return payload


def _arquivo_preferencias_skus_ocultos_medias(client_id: str, username: str) -> str:
    usuario_slug = re.sub(r"[^0-9A-Za-z._-]+", "_", str(username or "").strip().lower())
    if not usuario_slug:
        usuario_slug = "anon"
    return os.path.join(get_tenant_path(client_id), f"medias_compras_skus_ocultos_{usuario_slug}.json")


def _normalizar_lista_skus_ocultos(lista: Any) -> list[str]:
    if not isinstance(lista, list):
        return []
    vistos = set()
    saida: list[str] = []
    for item in lista:
        sku = str(item or "").strip().upper()
        if not sku or sku in vistos:
            continue
        vistos.add(sku)
        saida.append(sku)
        if len(saida) >= 5000:
            break
    return saida


def _carregar_preferencias_skus_ocultos_medias(client_id: str, username: str) -> dict:
    caminho = _arquivo_preferencias_skus_ocultos_medias(client_id, username)
    if not os.path.exists(caminho):
        return {"skus_ocultos": [], "updated_at": None}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        if isinstance(dados, dict):
            skus = _normalizar_lista_skus_ocultos(dados.get("skus_ocultos") or [])
            return {
                "skus_ocultos": skus,
                "updated_at": dados.get("updated_at"),
            }
        if isinstance(dados, list):
            return {
                "skus_ocultos": _normalizar_lista_skus_ocultos(dados),
                "updated_at": None,
            }
    except Exception:
        logger.exception("Erro ao carregar SKUs ocultos de mÃƒÂ©dias (tenant=%s, user=%s)", client_id, username)
    return {"skus_ocultos": [], "updated_at": None}


def _salvar_preferencias_skus_ocultos_medias(client_id: str, username: str, skus_ocultos: Any) -> dict:
    caminho = _arquivo_preferencias_skus_ocultos_medias(client_id, username)
    pasta = os.path.dirname(caminho)
    if pasta and not os.path.exists(pasta):
        os.makedirs(pasta, exist_ok=True)

    payload = {
        "skus_ocultos": _normalizar_lista_skus_ocultos(skus_ocultos),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return payload


def _pasta_cache_listas_pedidos(client_id: str) -> str:
    pasta = os.path.join(get_tenant_path(client_id), "cache_listas_pedidos")
    os.makedirs(pasta, exist_ok=True)
    return pasta


def _token_versao_lista(valor: str | None) -> str:
    token = re.sub(r"[^0-9A-Za-z]+", "", str(valor or ""))
    return token or "semversao"


def _arquivo_cache_lista_pedido(client_id: str, lista_id: str, versao_lista: str | None) -> str:
    pasta = _pasta_cache_listas_pedidos(client_id)
    token = _token_versao_lista(versao_lista)
    return os.path.join(pasta, f"{lista_id}_{token}.xlsx")


def _limpar_cache_lista_pedido(client_id: str, lista_id: str, manter_versao: str | None = None) -> None:
    pasta = _pasta_cache_listas_pedidos(client_id)
    manter_token = _token_versao_lista(manter_versao) if manter_versao is not None else None
    prefixo = f"{lista_id}_"
    try:
        for nome in os.listdir(pasta):
            if not nome.startswith(prefixo) or not nome.lower().endswith(".xlsx"):
                continue
            token_nome = nome[len(prefixo):-5]
            if manter_token and token_nome == manter_token:
                continue
            try:
                os.remove(os.path.join(pasta, nome))
            except Exception:
                pass
    except Exception:
        pass


def _salvar_bytes_cache_lista_pedido(client_id: str, lista_id: str, versao_lista: str | None, file_bytes: bytes) -> str | None:
    if not file_bytes:
        return None
    caminho = _arquivo_cache_lista_pedido(client_id, str(lista_id), versao_lista)
    try:
        with open(caminho, "wb") as f:
            f.write(file_bytes)
        _limpar_cache_lista_pedido(client_id, str(lista_id), manter_versao=versao_lista)
        return caminho
    except Exception:
        return None


LISTA_PEDIDO_STATUS_VALIDOS = {
    "Lista gerada",
    "Em Orçamento",
    "Analisando orçamento",
    "Pedido Aprovado",
    "Pedido emitido",
    "Em produção",
    "Em trânsito",
    "Em desembaraço",
    "Recebido",
    "Pedido cancelado",
}

LISTA_PEDIDO_STATUS_CONTABILIZADOS_TRANSITO = {
    "Analisando orçamento",
    "Pedido Aprovado",
    "Pedido emitido",
    "Em produção",
    "Em trânsito",
    "Em desembaraço",
}


def _corrigir_mojibake_texto(valor: str) -> str:
    texto = str(valor or "")
    marcadores = ("Ãƒ", "Ã‚", "Ã§", "Ã¡", "Ã©", "Ãª", "Ã³", "Ãº", "ï¿½")
    if not texto or not any(m in texto for m in marcadores):
        return texto
    for _ in range(3):
        try:
            corrigido = texto.encode("cp1252").decode("utf-8")
        except UnicodeError:
            break
        if not corrigido or corrigido == texto:
            break
        texto = corrigido
        if not any(m in texto for m in marcadores):
            break
    return texto


def _normalizar_status_lista_pedido(status: str | None) -> str:
    texto = _corrigir_mojibake_texto(str(status or "").strip())

    def _status_key(valor: str) -> str:
        base = unicodedata.normalize("NFKD", str(valor or ""))
        base = "".join(ch for ch in base if not unicodedata.combining(ch))
        return re.sub(r"\s+", " ", base).strip().lower()

    aliases = {
        "pedido feito": "Pedido Aprovado",
        "lista gerada": "Lista gerada",
        "em orcamento": "Em Orçamento",
        "analisando orcamento": "Analisando orçamento",
        "pedido aprovado": "Pedido Aprovado",
        "pedido emitido": "Pedido emitido",
        "em producao": "Em produção",
        "em transito": "Em trânsito",
        "em desembaraco": "Em desembaraço",
        "recebido": "Recebido",
        "pedido cancelado": "Pedido cancelado",
    }
    return aliases.get(_status_key(texto), "Lista gerada")


def _carregar_listas_pedidos(client_id: str) -> list[dict]:
    caminho = _arquivo_listas_pedidos(client_id)
    if not os.path.exists(caminho):
        return []
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, list) else []
    except Exception:
        return []


def _mapa_estoque_em_transito_detalhado_por_sku(
    client_id: str,
    loja: str = "__todas",
) -> dict[str, dict[str, Any]]:
    listas = _carregar_listas_pedidos(client_id)
    loja_sel = str(loja or "").strip().lower() or "__todas"
    mapa: dict[str, dict[str, Any]] = {}

    for lista in listas:
        lista = lista if isinstance(lista, dict) else {}
        if _normalizar_status_lista_pedido(lista.get("status")) not in LISTA_PEDIDO_STATUS_CONTABILIZADOS_TRANSITO:
            continue

        loja_lista = str(lista.get("loja") or "").strip().lower() or "__todas"
        if loja_sel != "__todas" and loja_lista not in {loja_sel, "__todas"}:
            continue

        quantidades_lista: dict[str, float] = {}
        for item in (lista.get("itens") or []):
            item = item if isinstance(item, dict) else {}
            sku = _normalizar_sku_mes(str((item or {}).get("SKU") or "").strip())
            if not sku:
                continue
            try:
                qtd = max(0.0, float(item.get("Quantidade", 0) or 0))
            except Exception:
                qtd = 0.0
            if qtd <= 0:
                continue
            quantidades_lista[sku] = quantidades_lista.get(sku, 0.0) + qtd

        lista_id = str(lista.get("id") or "").strip()
        nome_lista = str(lista.get("nome_lista") or "").strip() or "Sem nome"
        for sku, quantidade in quantidades_lista.items():
            detalhe = mapa.setdefault(sku, {"total": 0.0, "listas": []})
            detalhe["total"] = float(detalhe.get("total", 0) or 0) + quantidade
            detalhe["listas"].append({
                "lista_id": lista_id,
                "nome_lista": nome_lista,
                "quantidade": quantidade,
            })

    return mapa


def _mapa_estoque_em_transito_por_sku(client_id: str, loja: str = "__todas") -> dict[str, float]:
    detalhes = _mapa_estoque_em_transito_detalhado_por_sku(client_id, loja)
    return {
        sku: float((detalhe or {}).get("total", 0) or 0)
        for sku, detalhe in detalhes.items()
    }


def _meses_sem_vender_desde(data_iso: str | None) -> int:
    texto = str(data_iso or "").strip()
    if not texto:
        return 999
    try:
        ref = datetime.strptime(texto[:10], "%Y-%m-%d")
    except Exception:
        return 999

    agora = datetime.now()
    return max(0, (agora.year - ref.year) * 12 + (agora.month - ref.month))


def _mensagem_sem_venda(meses_sem_vender: int) -> str:
    meses = int(meses_sem_vender or 0)
    if meses >= 999:
        return "Sem histórico de venda"
    if meses > 6:
        return f"Há {meses} meses sem vender"
    if meses >= 6:
        return "Há 6 meses sem vender"
    if meses >= 3:
        return f"Há {meses} meses sem vender"
    return ""


def _mapa_ultima_venda_por_sku(client_id: str, loja: str = "__todas") -> dict[str, str]:
    db_paths = _listar_bancos_vendas_tenant(client_id, loja)
    resultado: dict[str, str] = {}

    for db_vendas in db_paths:
        if not (db_vendas and os.path.exists(db_vendas)):
            continue

        conn = sqlite3.connect(db_vendas)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            query = (
                """
                SELECT sku, MAX(date(data)) AS ultima_data
                FROM vendas
                WHERE sku IS NOT NULL
                  AND trim(sku) != ''
                """
            )
            params: list[Any] = []
            filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_vendas(loja)
            if filtro_loja_sql:
                query += filtro_loja_sql
                params.extend(filtro_loja_params)
            query += " GROUP BY sku"

            rows = cur.execute(query, params).fetchall()
            for r in rows:
                sku = _normalizar_sku_mes(str(r["sku"] or "").strip())
                ultima_data = str(r["ultima_data"] or "").strip()
                if not sku or not ultima_data:
                    continue
                if sku not in resultado or ultima_data > resultado[sku]:
                    resultado[sku] = ultima_data
        finally:
            conn.close()

    return resultado


def _salvar_listas_pedidos(client_id: str, listas: list[dict]) -> None:
    caminho = _arquivo_listas_pedidos(client_id)
    pasta = os.path.dirname(caminho)
    if pasta and not os.path.exists(pasta):
        os.makedirs(pasta, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(listas or [], f, ensure_ascii=False, indent=2)


def _primeiro_texto_item(item: dict, chaves: list[str] | tuple[str, ...]) -> str:
    if not isinstance(item, dict):
        return ""

    mapa: dict[str, Any] = {}
    for chave, valor in item.items():
        if valor is None:
            continue
        texto = str(valor).strip()
        if not texto:
            continue
        base = unicodedata.normalize("NFKD", str(chave or ""))
        base = "".join(ch for ch in base if not unicodedata.combining(ch))
        base = re.sub(r"[^a-z0-9]+", "", base.lower())
        if base and base not in mapa:
            mapa[base] = valor

    for chave in chaves:
        base = unicodedata.normalize("NFKD", str(chave or ""))
        base = "".join(ch for ch in base if not unicodedata.combining(ch))
        base = re.sub(r"[^a-z0-9]+", "", base.lower())
        if not base:
            continue
        valor = mapa.get(base)
        if valor is not None and str(valor).strip():
            return str(valor).strip()
    return ""


def _resolver_foto_cadastro_sku(client_id: str | None, sku: str, foto_ref: str = "") -> str:
    foto_txt = str(foto_ref or "").replace("\\", "/").strip()
    if foto_txt:
        return foto_txt

    sku_txt = str(sku or "").strip()
    if not sku_txt:
        return ""

    bases = [sku_txt, sku_txt.upper()]
    if re.fullmatch(r"\d+", sku_txt):
        bases.extend([str(int(sku_txt)), sku_txt.zfill(3)])
    bases = list(dict.fromkeys([b for b in bases if b]))

    extensoes = [".png", ".jpg", ".jpeg", ".webp"]
    diretorios: list[str] = []
    if client_id:
        diretorios.append(os.path.join(get_tenant_path(client_id), "cadastro_fotos"))
    if PASTA_INFO:
        diretorios.append(os.path.join(PASTA_INFO, "default", "cadastro_fotos"))

    for pasta in diretorios:
        if not pasta or not os.path.isdir(pasta):
            continue
        for base in bases:
            for ext in extensoes:
                nome = f"{base}{ext}"
                if os.path.exists(os.path.join(pasta, nome)):
                    return nome
    return ""


def _normalizar_item_lista_pedido(item: dict) -> dict:
    if not isinstance(item, dict):
        item = {}
    sku = _primeiro_texto_item(item, ["SKU", "sku"])
    foto = _primeiro_texto_item(item, ["Foto", "Picture", "foto", "picture", "imagem", "image"])
    titulo = _primeiro_texto_item(item, [
        *TITULO_PRODUTO_INGLES_KEYS_LEGADO,
        "Description",
        "Descricao",
        "Descrição",
        "Titulo",
        "Título",
        "Produto",
        "Nome",
    ])
    oem = _primeiro_texto_item(item, ["OEM", "OEM/ Model", "OEM Model", "Modelo", "Numero do modelo", "Número do modelo"])
    cor_lado = _primeiro_texto_item(item, [
        COR_LADO_LISTA_PEDIDO_KEY,
        "Color side",
        "Color/Side",
        "Cor/Lado",
        "Cor lado",
        "Lado/Cor",
        "Lado",
        "Cor",
        "Color",
        "Side",
    ])
    link = _primeiro_texto_item(item, ["Link", "Link Aliexpress", "url aliexpress", "URL", "Url", "Link produto"])
    quantidade = _to_float(_primeiro_texto_item(item, ["Quantidade", "quantity", "Qtd", "Qtde", "Qty"]), 0.0)
    valor_unidade = _to_float(_primeiro_texto_item(item, ["Valor unidade", "Valor unitario", "Valor unitário", "Cost", "Custo", "Preco", "Preço"]), 0.0)
    valor_total = _to_float(_primeiro_texto_item(item, ["Valor total", "Sub-total(USD)", "Subtotal USD", "Sub total", "Subtotal"]), 0.0)
    preservados = {
        _corrigir_mojibake_texto(str(chave)): _corrigir_mojibake_texto(valor) if isinstance(valor, str) else valor
        for chave, valor in item.items()
    }
    normalizado = {
        **preservados,
        "SKU": sku,
        "Foto": foto,
        TITULO_PRODUTO_INGLES_KEY: titulo,
        "OEM": oem,
        COR_LADO_LISTA_PEDIDO_KEY: cor_lado,
        "Link": link,
        "Quantidade": int(max(0.0, quantidade)),
        "Valor unidade": max(0.0, valor_unidade),
        "Valor total": max(0.0, valor_total),
        CBM_LISTA_PEDIDO_KEY: _primeiro_texto_item(item, [CBM_LISTA_PEDIDO_KEY, "Estimated CBM", "CBM", "M3", "M³"]),
        PESO_LISTA_PEDIDO_KEY: _primeiro_texto_item(item, [PESO_LISTA_PEDIDO_KEY, "Estimated Weight", "Estimated Weigh", "Weight", "Peso"]),
        EMBALAGEM_LISTA_PEDIDO_KEY: _primeiro_texto_item(item, [EMBALAGEM_LISTA_PEDIDO_KEY, "Packaging", "Embalagem individual", "Embalagem"]),
        "M3 individual": _primeiro_texto_item(item, ["M3 individual", "M3 Individual", "M³ individual", "M³ Individual", "m3_individual", "CBM individual"]),
        "Frete Internacional": max(0.0, _to_float(item.get("Frete Internacional", 0), 0.0)),
        "MOQ": max(0.0, _to_float(_primeiro_texto_item(item, ["MOQ", "moq", "Quantidade minima", "Quantidade mínima"]), 0.0)),
        "package_multiple": max(1.0, _to_float(_primeiro_texto_item(item, ["package_multiple", "Multiplo embalagem", "Múltiplo embalagem", "Packing multiple"]), 1.0)),
        "received_quantity": max(0.0, _to_float(_primeiro_texto_item(item, ["received_quantity", "Quantidade recebida", "Qtd recebida"]), 0.0)),
        "defective_quantity": max(0.0, _to_float(_primeiro_texto_item(item, ["defective_quantity", "Quantidade defeituosa", "Qtd defeituosa"]), 0.0)),
    }

    campos_preservar = {
        "NCM": ["NCM", "ncm"],
        "CEST": ["CEST", "Cest", "cest"],
        "II": ["II", "ii"],
        "IPI": ["IPI", "ipi"],
        "PIS": ["PIS", "Pis", "pis"],
        "COFINS": ["COFINS", "Cofins", "cofins"],
        "Imposto": ["Imposto", "imposto"],
        "Monofasico": ["Monofasico", "monofasico"],
    }
    for destino, aliases in campos_preservar.items():
        valor = _primeiro_texto_item(item, aliases)
        if valor:
            normalizado[destino] = valor

    for chave, valor in item.items():
        if valor is None or not str(valor).strip():
            continue
        chave_norm = unicodedata.normalize("NFKD", str(chave or ""))
        chave_norm = "".join(ch for ch in chave_norm if not unicodedata.combining(ch))
        chave_norm = re.sub(r"[^a-z0-9]+", "", chave_norm.lower())
        if "ncm" in chave_norm and ("descr" in chave_norm or "desc" in chave_norm):
            normalizado["Descricao do NCM"] = str(valor).strip()
            break

    return normalizado




def _resumo_lista_pedido(lista: dict, m3_lookup: dict | None = None) -> dict:
    itens = lista.get("itens") or []
    total_usd = 0.0
    total_m3 = 0.0
    for i in itens:
        qtd = max(0.0, float(i.get("Quantidade", 0) or 0))
        vt = max(0.0, float(i.get("Valor total", 0) or 0))
        vu = max(0.0, float(i.get("Valor unidade", 0) or 0))
        total_usd += vt if vt > 0 else (qtd * vu)
        if m3_lookup is not None:
            sku = str(i.get("SKU", "") or "").strip()
            k1, k2, k3 = _sku_lookup_keys_sync_ncm(sku)
            m3_ind = m3_lookup.get(k1) or m3_lookup.get(k2) or m3_lookup.get(k3) or 0.0
            total_m3 += m3_ind * qtd
    return {
        "id": str(lista.get("id", "") or ""),
        "nome_lista": str(lista.get("nome_lista", "") or ""),
        "loja": str(lista.get("loja", "") or "").strip() or "__todas",
        "status": _normalizar_status_lista_pedido(lista.get("status")),
        "created_at": str(lista.get("created_at", "") or ""),
        "updated_at": str(lista.get("updated_at", "") or ""),
        "total_itens": len(itens),
        "total_quantidade": int(sum(float(i.get("Quantidade", 0) or 0) for i in itens)),
        "total_usd": round(total_usd, 2),
        "total_m3": round(total_m3, 4),
        "skus": [
            str(i.get("SKU", "") or "").strip()
            for i in itens
            if str(i.get("SKU", "") or "").strip()
        ],
        "numero_invoice": str(lista.get("numero_invoice") or lista.get("invoice") or "").strip(),
        "supplier": str(lista.get("supplier") or lista.get("fornecedor") or "").strip(),
        "currency": str(lista.get("currency") or "USD").strip(),
        "incoterm": str(lista.get("incoterm") or "").strip(),
        "exchange_rate": _to_float(lista.get("exchange_rate") or lista.get("dolar_hoje") or lista.get("cotacao_dolar"), 0.0),
        "lead_time_days": int(_to_float(lista.get("lead_time_days"), 0.0)),
        "moq_default": _to_float(lista.get("moq_default"), 0.0),
        "package_multiple_default": max(1.0, _to_float(lista.get("package_multiple_default"), 1.0)),
        "order_date": str(lista.get("order_date") or ""),
        "promised_ship_date": str(lista.get("promised_ship_date") or ""),
        "actual_ship_date": str(lista.get("actual_ship_date") or ""),
        "eta_date": str(lista.get("eta_date") or ""),
        "customs_clearance_date": str(lista.get("customs_clearance_date") or ""),
        "received_at": str(lista.get("received_at") or ""),
    }




COMMON_EXPORTS = [
    "configure_medias_compras_common_runtime",
    "get_tenant_id",
    "get_tenant_path",
    "logger",
    "TEMP_FILES_STORAGE",
    "TEMP_FILES_META",
    "LISTA_PEDIDO_XLSX_CACHE",
    "LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS",
    "PASTA_INFO",
    "TITULO_PRODUTO_INGLES_KEY",
    "TITULO_PRODUTO_INGLES_KEYS_LEGADO",
    "COR_LADO_LISTA_PEDIDO_KEY",
    "CBM_LISTA_PEDIDO_KEY",
    "PESO_LISTA_PEDIDO_KEY",
    "EMBALAGEM_LISTA_PEDIDO_KEY",
    "C54_HEADERS_LISTA_PEDIDO",
    "LISTA_PEDIDO_STATUS_VALIDOS",
    "LISTA_PEDIDO_STATUS_CONTABILIZADOS_TRANSITO",
    "_to_float",
    "_normalizar_sku_mes",
    "_sku_lookup_keys_sync_ncm",
    "_normalizar_codigo_fiscal",
    "_arquivo_listas_pedidos",
    "_arquivo_preferencias_colunas_importacoes",
    "_carregar_preferencias_colunas_importacoes",
    "_salvar_preferencias_colunas_importacoes",
    "_arquivo_preferencias_skus_ocultos_medias",
    "_normalizar_lista_skus_ocultos",
    "_carregar_preferencias_skus_ocultos_medias",
    "_salvar_preferencias_skus_ocultos_medias",
    "_pasta_cache_listas_pedidos",
    "_token_versao_lista",
    "_arquivo_cache_lista_pedido",
    "_limpar_cache_lista_pedido",
    "_salvar_bytes_cache_lista_pedido",
    "_normalizar_status_lista_pedido",
    "_carregar_listas_pedidos",
    "_mapa_estoque_em_transito_detalhado_por_sku",
    "_mapa_estoque_em_transito_por_sku",
    "_meses_sem_vender_desde",
    "_mensagem_sem_venda",
    "_mapa_ultima_venda_por_sku",
    "_salvar_listas_pedidos",
    "_primeiro_texto_item",
    "_resolver_foto_cadastro_sku",
    "_normalizar_item_lista_pedido",
    "_resumo_lista_pedido",
]

__all__ = COMMON_EXPORTS
