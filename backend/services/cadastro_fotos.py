"""Cadastro photo extraction, upload and serving helpers."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import Header, Request

from backend.services.runtime_bridge import bind_runtime_globals

logger = logging.getLogger("jk_sistema")


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    raise RuntimeError("Cadastro runtime was not configured.")


def get_tenant_path(client_id: str):
    raise RuntimeError("Cadastro runtime was not configured.")


def _configure_runtime_globals(target_globals, runtime_module=None):
    runtime = bind_runtime_globals(target_globals, runtime_module)
    if runtime is not None:
        runtime_logger = getattr(runtime, "logger", None)
        if runtime_logger is not None:
            target_globals["logger"] = runtime_logger
        if hasattr(runtime, "get_tenant_id"):
            target_globals["get_tenant_id"] = getattr(runtime, "get_tenant_id")
        if hasattr(runtime, "get_tenant_path"):
            target_globals["get_tenant_path"] = getattr(runtime, "get_tenant_path")
    return runtime


import base64
import io
import os
import re
from typing import Any, Optional

import openpyxl
import requests
from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest

from backend.services.cadastro_common import *


def configure_cadastro_fotos_runtime(runtime_module=None):
    configure_cadastro_common_runtime(runtime_module)
    return _configure_runtime_globals(globals(), runtime_module)


configure_cadastro_fotos_runtime()

def _exportar_planilha_xlsx_bytes(spreadsheet_id: str) -> bytes:
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    from google.auth.transport.requests import Request as GoogleAuthRequest
    creds.refresh(GoogleAuthRequest())
    headers = {"Authorization": f"Bearer {creds.token}"}
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export"
    resp = requests.get(
        url,
        headers=headers,
        params={"format": "xlsx", "gid": str(SPREADSHEET_GID_FOTOS_SKU)},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content

def _extrair_imagens_planilha_por_sku() -> dict:
    """Extrai imagens ancoradas na coluna B e relaciona com o SKU da coluna A."""
    try:
        xlsx_bytes = _exportar_planilha_xlsx_bytes(SPREADSHEET_ID_FOTOS_SKU)
    except Exception as e:
        logger.warning(f"[CADASTRO] NÃƒÂ£o foi possÃƒÂ­vel exportar planilha para extrair imagens: {e}")
        return {}

    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
        ws = None
        for nome in wb.sheetnames:
            if nome.strip().lower() == "dados":
                ws = wb[nome]
                break
        if ws is None:
            ws = wb[wb.sheetnames[0]]

        imagens_por_linha = {}
        for img in getattr(ws, "_images", []):
            anc = getattr(img, "anchor", None)
            from_marker = getattr(anc, "_from", None)
            if from_marker is None:
                continue
            row_1 = int(from_marker.row) + 1
            col_1 = int(from_marker.col) + 1
            if col_1 != 2 or row_1 < 2:
                continue
            try:
                payload = img._data()
            except Exception:
                continue
            ext = str(getattr(img, "format", "png") or "png").lower()
            if ext == "jpeg":
                ext = "jpg"
            imagens_por_linha[row_1] = {"bytes": payload, "ext": ext}

        if not imagens_por_linha:
            return {}

        mapa = {}
        for row_1, payload in imagens_por_linha.items():
            sku_raw = ws.cell(row=row_1, column=1).value
            sku_norm = _normalizar_sku_mes(str(sku_raw or "").strip())
            if sku_norm:
                atual = mapa.get(sku_norm)
                if atual is None or len(payload["bytes"]) > len(atual["bytes"]):
                    mapa[sku_norm] = payload
        return mapa
    except Exception as e:
        logger.warning(f"[CADASTRO] Falha ao processar imagens exportadas da planilha: {e}")
        return {}

def _nome_arquivo_foto_sku(sku: str, ext: str) -> str:
    sku_limpo = re.sub(r'[\\/:*?"<>|]+', '_', str(sku or '').strip())
    ext_limpa = re.sub(r'[^a-zA-Z0-9]+', '', str(ext or 'png').lower()) or 'png'
    return f"{sku_limpo}.{ext_limpa}"

def _cadastro_sku_sem_zeros_blocos(sku: str) -> str:
    sku_norm = _normalizar_sku_mes(str(sku or "").strip())
    partes = re.split(r"(\d+)", sku_norm.upper())
    return "".join(str(int(p)) if p.isdigit() else p for p in partes)

def _salvar_foto_data_url_no_tenant(client_id: str, sku: str, foto_data_url: str, foto_filename: str = "") -> str:
    """Salva uma imagem enviada como data URL e retorna o caminho relativo para o campo foto."""
    raw = str(foto_data_url or "").strip()
    if not raw:
        return ""

    m = re.match(r"^data:image/([a-zA-Z0-9.+-]+);base64,(.+)$", raw, re.IGNORECASE | re.DOTALL)
    if not m:
        raise HTTPException(status_code=400, detail="Formato de imagem invalido para salvar.")

    mime_ext = m.group(1).lower()
    b64 = m.group(2).strip()
    ext_map = {
        "jpeg": "jpg",
        "jpg": "jpg",
        "png": "png",
        "webp": "webp",
        "gif": "gif",
        "bmp": "bmp",
    }
    ext = ext_map.get(mime_ext)
    if not ext:
        raise HTTPException(status_code=400, detail="Tipo de imagem nÃ£o suportado.")

    try:
        conteudo = base64.b64decode(b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="ConteÃƒÂºdo da imagem invalido (base64).")

    if not conteudo:
        raise HTTPException(status_code=400, detail="Imagem vazia.")

    sku_norm = _normalizar_sku_mes(sku)
    if not sku_norm:
        raise HTTPException(status_code=400, detail="SKU invalido para salvar imagem.")

    tenant_path = get_tenant_path(client_id)
    pasta_fotos = os.path.join(tenant_path, "cadastro_fotos")
    os.makedirs(pasta_fotos, exist_ok=True)

    if foto_filename:
        nome_base = os.path.splitext(os.path.basename(str(foto_filename)))[0]
        nome_base = re.sub(r'[\\/:*?"<>|]+', '_', nome_base).strip() or sku_norm
        nome_arquivo = f"{nome_base}.{ext}"
    else:
        nome_arquivo = _nome_arquivo_foto_sku(sku_norm, ext)

    caminho_arquivo = os.path.join(pasta_fotos, nome_arquivo)
    with open(caminho_arquivo, "wb") as f:
        f.write(conteudo)

    return f"cadastro_fotos/{nome_arquivo}"

def _cadastro_mapa_fotos_locais(client_id: str) -> dict[str, str]:
    """Mapeia fotos jÃ¡ salvas em cadastro_fotos pelo SKU do nome do arquivo."""
    candidatos_dir = [
        os.path.join(get_tenant_path(client_id), "cadastro_fotos"),
        os.path.join(PASTA_INFO, "default", "cadastro_fotos"),
    ]
    mapa: dict[str, str] = {}
    extensoes = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

    for pasta in candidatos_dir:
        if not os.path.isdir(pasta):
            continue
        try:
            arquivos = sorted(
                os.listdir(pasta),
                key=lambda nome: (os.path.splitext(nome)[1].lower() != ".png", nome.lower()),
            )
        except Exception:
            continue

        for nome in arquivos:
            base, ext = os.path.splitext(nome)
            if ext.lower() not in extensoes:
                continue
            sku_norm = _normalizar_sku_mes(base)
            if not sku_norm:
                continue
            caminho_relativo = f"cadastro_fotos/{nome}"
            mapa.setdefault(f"exact:{sku_norm}", caminho_relativo)
            mapa.setdefault(f"soft:{_cadastro_sku_sem_zeros_blocos(sku_norm)}", caminho_relativo)

    return mapa

def _cadastro_resolver_foto_local(mapa_fotos: dict[str, str], sku: str) -> str:
    sku_norm = _normalizar_sku_mes(str(sku or "").strip())
    if not sku_norm:
        return ""
    return (
        mapa_fotos.get(f"exact:{sku_norm}")
        or mapa_fotos.get(f"soft:{_cadastro_sku_sem_zeros_blocos(sku_norm)}")
        or ""
    )

async def upload_foto_cadastro(
    sku: str = Form(...),
    arquivo: UploadFile = File(...),
    client_id: str = Depends(get_tenant_id)
):
    sku_norm = _normalizar_sku_mes(sku)
    if not sku_norm:
        raise HTTPException(status_code=400, detail="SKU invalido para upload da imagem.")

    if not arquivo:
        raise HTTPException(status_code=400, detail="Arquivo de imagem nÃ£o informado.")

    nome_original = str(getattr(arquivo, "filename", "") or "").strip()
    ext = os.path.splitext(nome_original)[1].lower()
    if not ext:
        ctype = str(getattr(arquivo, "content_type", "") or "").lower()
        if "png" in ctype:
            ext = ".png"
        elif "jpeg" in ctype or "jpg" in ctype:
            ext = ".jpg"
        elif "webp" in ctype:
            ext = ".webp"
        elif "gif" in ctype:
            ext = ".gif"

    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise HTTPException(status_code=400, detail="Formato invalido. Use PNG, JPG, WEBP ou GIF.")

    try:
        conteudo = await arquivo.read()
        if not conteudo:
            raise HTTPException(status_code=400, detail="Imagem vazia.")

        tenant_path = get_tenant_path(client_id)
        pasta_fotos = os.path.join(tenant_path, "cadastro_fotos")
        os.makedirs(pasta_fotos, exist_ok=True)

        ext_limpa = ext.replace(".", "")
        nome_arquivo = _nome_arquivo_foto_sku(sku_norm, ext_limpa)
        caminho_arquivo = os.path.join(pasta_fotos, nome_arquivo)

        with open(caminho_arquivo, "wb") as f:
            f.write(conteudo)

        caminho_relativo = f"cadastro_fotos/{nome_arquivo}"
        return {
            "success": True,
            "foto": caminho_relativo,
            "url": f"/api/cadastro/foto-arquivo/{quote_plus(nome_arquivo)}"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar imagem do cadastro: {str(e)}")

async def servir_foto_cadastro(client_id: str, filename: str):
    nome_original = str(filename or "").replace("\\", "/").strip("/")
    if nome_original.lower().startswith("cadastro_fotos/"):
        nome_original = nome_original.split("/", 1)[1]

    nome_seguro = os.path.basename(nome_original)
    if not nome_seguro:
        raise HTTPException(status_code=404, detail="Arquivo de foto invalido")

    candidatos = []
    # 1) tenant informado
    candidatos.append(os.path.join(get_tenant_path(client_id), "cadastro_fotos", nome_seguro))
    # 2) fallback para default
    candidatos.append(os.path.join(PASTA_INFO, "default", "cadastro_fotos", nome_seguro))
    # 3) fallback em todos os tenants conhecidos
    try:
        for pasta in os.listdir(PASTA_INFO):
            tenant_dir = os.path.join(PASTA_INFO, pasta)
            if not os.path.isdir(tenant_dir):
                continue
            candidatos.append(os.path.join(tenant_dir, "cadastro_fotos", nome_seguro))
    except Exception:
        pass

    caminho_arquivo = next((p for p in candidatos if os.path.exists(p)), None)
    if not caminho_arquivo:
        raise HTTPException(status_code=404, detail="Foto nÃ£o encontrada")

    response = FileResponse(caminho_arquivo)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

async def servir_foto_cadastro_por_arquivo(filename: str):
    nome_original = str(filename or "").replace("\\", "/").strip("/")
    if nome_original.lower().startswith("cadastro_fotos/"):
        nome_original = nome_original.split("/", 1)[1]

    nome_seguro = os.path.basename(nome_original)
    if not nome_seguro:
        raise HTTPException(status_code=404, detail="Arquivo de foto invalido")

    candidatos = []
    try:
        for pasta in os.listdir(PASTA_INFO):
            tenant_dir = os.path.join(PASTA_INFO, pasta)
            if not os.path.isdir(tenant_dir):
                continue
            candidatos.append(os.path.join(tenant_dir, "cadastro_fotos", nome_seguro))
    except Exception:
        pass

    caminho_arquivo = next((p for p in candidatos if os.path.exists(p)), None)
    if not caminho_arquivo:
        raise HTTPException(status_code=404, detail="Foto nÃ£o encontrada")

    response = FileResponse(caminho_arquivo)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

__all__ = ['_exportar_planilha_xlsx_bytes', '_extrair_imagens_planilha_por_sku', '_nome_arquivo_foto_sku', '_cadastro_sku_sem_zeros_blocos', '_salvar_foto_data_url_no_tenant', '_cadastro_mapa_fotos_locais', '_cadastro_resolver_foto_local', 'upload_foto_cadastro', 'servir_foto_cadastro', 'servir_foto_cadastro_por_arquivo', 'configure_cadastro_fotos_runtime']
