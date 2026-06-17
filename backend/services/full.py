"""Full helpers shared by legacy Full endpoints."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import unicodedata
import uuid
from datetime import datetime
from typing import Callable

import fitz


_get_tenant_path: Callable[[str], str] | None = None


def configure_full_context(*, get_tenant_path: Callable[[str], str]) -> None:
    global _get_tenant_path
    _get_tenant_path = get_tenant_path


def _tenant_path(client_id: str) -> str:
    if not callable(_get_tenant_path):
        raise RuntimeError("Full service context was not configured.")
    return _get_tenant_path(client_id)


def _full_numero(valor) -> float:
    if isinstance(valor, (int, float)):
        try:
            return float(valor) if math.isfinite(float(valor)) else 0.0
        except Exception:
            return 0.0
    texto = str(valor or "").strip()
    if not texto:
        return 0.0
    normalizado = re.sub(r"[^\d,.-]", "", texto).replace(".", "").replace(",", ".")
    try:
        numero = float(normalizado)
        return numero if math.isfinite(numero) else 0.0
    except Exception:
        return 0.0


def _full_envios_db_path(client_id: str) -> str:
    return os.path.join(_tenant_path(client_id), "full_envios_transito.db")


def _full_envios_files_dir(client_id: str) -> str:
    path = os.path.join(_tenant_path(client_id), "full_envios_transito_pdfs")
    os.makedirs(path, exist_ok=True)
    return path


def _full_envios_status_norm(status: str) -> str:
    valor = str(status or "aguardando_inicio").strip().lower()
    valor = unicodedata.normalize("NFKD", valor).encode("ascii", "ignore").decode("ascii")
    valor = re.sub(r"[^a-z0-9]+", "_", valor).strip("_")
    permitidos = {
        "aguardando_inicio",
        "em_processamento",
        "finalizando",
        "finalizado",
        "recebimento_pendente",
        "recebido",
        "inativo",
    }
    return valor if valor in permitidos else "aguardando_inicio"


def _full_envios_data_iso(valor: str | None, fallback_hoje: bool = False) -> str:
    texto = str(valor or "").strip()
    if not texto:
        return datetime.now().strftime("%Y-%m-%d") if fallback_hoje else ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto[:10], fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    match = re.search(r"(\d{2})[/-](\d{2})[/-](\d{4})", texto)
    if match:
        try:
            return datetime(int(match.group(3)), int(match.group(2)), int(match.group(1))).strftime("%Y-%m-%d")
        except Exception:
            pass
    return datetime.now().strftime("%Y-%m-%d") if fallback_hoje else ""


def _garantir_tabela_full_envios_transito(client_id: str) -> None:
    db_path = _full_envios_db_path(client_id)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS envios_transito (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_envio TEXT NOT NULL,
                loja TEXT,
                status TEXT NOT NULL DEFAULT 'aguardando_inicio',
                situacao_ml TEXT,
                data_envio TEXT,
                data_recebimento TEXT,
                total_unidades REAL NOT NULL DEFAULT 0,
                total_produtos INTEGER NOT NULL DEFAULT 0,
                arquivo_pdf TEXT,
                arquivo_nome TEXT,
                itens_json TEXT NOT NULL DEFAULT '[]',
                observacoes TEXT,
                ativo INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_full_envios_status ON envios_transito (status, ativo)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_full_envios_data ON envios_transito (data_envio)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_full_envios_codigo ON envios_transito (codigo_envio)")
        conn.commit()
    finally:
        conn.close()


def _full_envio_row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    try:
        itens = json.loads(data.get("itens_json") or "[]")
    except Exception:
        itens = []
    data["itens"] = itens if isinstance(itens, list) else []
    data["ativo"] = bool(data.get("ativo"))
    data.pop("itens_json", None)
    return data


def _full_envios_agrupar_itens(itens: list[dict]) -> list[dict]:
    agrupado: dict[tuple[str, str, str], dict] = {}
    for item in itens or []:
        sku_item = str(item.get("sku") or "").strip()
        produto = str(item.get("produto") or "").strip()
        variacao = str(item.get("variacao") or "").strip()
        key = (sku_item.upper(), produto.lower(), variacao.lower())
        atual = agrupado.get(key) or {
            "sku": sku_item,
            "produto": produto,
            "variacao": variacao,
            "quantidade": 0,
            "mlb": str(item.get("mlb") or "").strip(),
        }
        atual["quantidade"] = float(atual.get("quantidade") or 0) + _full_numero(item.get("quantidade"))
        if not atual.get("mlb"):
            atual["mlb"] = str(item.get("mlb") or "").strip()
        agrupado[key] = atual
    return list(agrupado.values())


def _full_envios_extrair_texto_pdf(conteudo: bytes) -> str:
    doc = fitz.open(stream=conteudo, filetype="pdf")
    try:
        partes = []
        for idx in range(min(len(doc), 80)):
            partes.append(doc[idx].get_text() or "")
        return "\n".join(partes)
    finally:
        doc.close()


def _full_envios_parse_itens(texto: str) -> list[dict]:
    itens: list[dict] = []
    for match in re.finditer(r"(?is)Produto\s+Varia..o\s+Qnt\s+SKU\s+(.*?)(?:Checklist de carregamento|ID Pedido|Corte aqui|$)", texto or ""):
        linhas = [ln.strip() for ln in match.group(1).splitlines() if ln.strip()]
        if not linhas:
            continue
        if linhas and re.fullmatch(r"\d+", linhas[0]):
            linhas = linhas[1:]
        sku_idx = None
        for idx in range(len(linhas) - 1, -1, -1):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{1,40}", linhas[idx]):
                sku_idx = idx
                break
        if sku_idx is None:
            continue
        qtd_idx = None
        for idx in range(sku_idx - 1, -1, -1):
            if re.fullmatch(r"\d+(?:[,.]\d+)?", linhas[idx]):
                qtd_idx = idx
                break
        if qtd_idx is None:
            continue
        nome_partes = linhas[:qtd_idx]
        variacao = ""
        if len(nome_partes) > 1:
            variacao = nome_partes[-1]
            nome_partes = nome_partes[:-1]
        itens.append({
            "produto": " ".join(nome_partes).strip(),
            "variacao": variacao,
            "quantidade": _full_numero(linhas[qtd_idx]),
            "sku": linhas[sku_idx],
            "mlb": "",
        })

    if itens:
        return _full_envios_agrupar_itens(itens)

    for match in re.finditer(r"(?im)\bSKU\s*[:\-]?\s*([A-Z0-9._/-]{2,})", texto or ""):
        start = max(0, match.start() - 180)
        trecho = (texto or "")[start:match.start()]
        qtd_match = re.search(r"(?im)(?:qtd|quantidade|unidades?)\s*[:\-]?\s*(\d+(?:[,.]\d+)?)", trecho)
        produto_match = re.search(r"(?im)(?:produto|titulo|descri..o)\s*[:\-]?\s*(.+)$", trecho)
        itens.append({
            "produto": produto_match.group(1).strip() if produto_match else "",
            "variacao": "",
            "quantidade": _full_numero(qtd_match.group(1)) if qtd_match else 1,
            "sku": match.group(1).strip(),
            "mlb": "",
        })
    return _full_envios_agrupar_itens(itens)


def _full_envios_parse_pdf(nome_arquivo: str, conteudo: bytes) -> dict:
    texto = _full_envios_extrair_texto_pdf(conteudo)
    codigo = ""
    for pattern in (
        r"#\s*(\d{5,})",
        r"\b(?:envio|remessa|shipment|inbound)\s*(?:n(?:ro|o|r|\.)*)?\s*[:#-]?\s*(\d{5,})",
        r"\b(\d{6,10})\b",
    ):
        match = re.search(pattern, texto, flags=re.IGNORECASE)
        if match:
            codigo = match.group(1)
            break
    if not codigo:
        nome_match = re.search(r"(\d{5,})", nome_arquivo or "")
        codigo = nome_match.group(1) if nome_match else f"ENV-{uuid.uuid4().hex[:8].upper()}"

    data_envio = ""
    previsto = re.search(r"(?is)(?:envio previsto|data(?: de)? envio|criado em|recebimento)\D{0,30}(\d{2}[/-]\d{2}[/-]\d{4})", texto or "")
    if previsto:
        data_envio = _full_envios_data_iso(previsto.group(1))
    if not data_envio:
        data_envio = _full_envios_data_iso(texto, fallback_hoje=True)

    situacao = ""
    texto_norm = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii").lower()
    if "recebimento pendente" in texto_norm:
        situacao = "Recebimento pendente"
    elif re.search(r"\brecebido\b", texto_norm):
        situacao = "Recebido"

    itens = _full_envios_parse_itens(texto)
    total_unidades = sum(_full_numero(item.get("quantidade")) for item in itens)
    if not total_unidades:
        match_unidades = re.search(r"(\d+(?:[,.]\d+)?)\s+unidades", texto or "", flags=re.IGNORECASE)
        total_unidades = _full_numero(match_unidades.group(1)) if match_unidades else 0

    status = "recebido" if situacao.lower() == "recebido" else "recebimento_pendente"
    return {
        "codigo_envio": codigo,
        "status": status,
        "situacao_ml": situacao,
        "data_envio": data_envio,
        "data_recebimento": data_envio if status == "recebido" else "",
        "total_unidades": total_unidades,
        "total_produtos": len(itens),
        "itens": itens,
        "observacoes": "",
    }


def _full_envios_inserir_registro(client_id: str, payload: dict) -> dict:
    _garantir_tabela_full_envios_transito(client_id)
    agora = datetime.now().isoformat(timespec="seconds")
    itens = payload.get("itens") if isinstance(payload.get("itens"), list) else []
    itens = _full_envios_agrupar_itens([dict(item) for item in itens])
    total_unidades = payload.get("total_unidades")
    if total_unidades is None:
        total_unidades = sum(_full_numero(item.get("quantidade")) for item in itens)
    status = _full_envios_status_norm(payload.get("status"))
    ativo = 0 if status == "inativo" else (1 if payload.get("ativo", True) else 0)
    codigo = str(payload.get("codigo_envio") or "").strip() or f"ENV-{uuid.uuid4().hex[:8].upper()}"

    conn = sqlite3.connect(_full_envios_db_path(client_id))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO envios_transito (
                codigo_envio, loja, status, situacao_ml, data_envio, data_recebimento,
                total_unidades, total_produtos, arquivo_pdf, arquivo_nome,
                itens_json, observacoes, ativo, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                codigo,
                str(payload.get("loja") or "").strip(),
                status,
                str(payload.get("situacao_ml") or "").strip(),
                _full_envios_data_iso(payload.get("data_envio"), fallback_hoje=True),
                _full_envios_data_iso(payload.get("data_recebimento")),
                float(total_unidades or 0),
                len(itens),
                str(payload.get("arquivo_pdf") or "").strip(),
                str(payload.get("arquivo_nome") or "").strip(),
                json.dumps(itens, ensure_ascii=False),
                str(payload.get("observacoes") or "").strip(),
                ativo,
                agora,
                agora,
            ),
        )
        conn.commit()
        cur.execute("SELECT * FROM envios_transito WHERE id = ?", (cur.lastrowid,))
        return _full_envio_row_to_dict(cur.fetchone())
    finally:
        conn.close()
