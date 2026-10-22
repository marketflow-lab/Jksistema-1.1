"""Atomic, day-scoped synchronization for Vendas."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.schemas import VendasSyncRequest
from backend.services.bling_oauth import requires_bling_reauthorization
from backend.services.sqlite_coordination import configure_sqlite_connection, sqlite_lock_for_path

from .errors import VendasDomainError as HTTPException
from .legacy_duplicates import without_legacy_sale_duplicates
from .legacy import (
    _bling_executar_com_refresh,
    _bling_listar_naturezas,
    _bling_listar_notas_entrada,
    _bling_listar_vendas,
    _bling_listar_vendas_fallback_nf_saida,
    _bling_obter_detalhes_nf,
    _bling_obter_numero_nf,
    _carregar_mapeamento_lojas_virtuais_cliente,
    _carregar_mapeamento_unidades,
    _classificar_unidade_virtual_devolucao,
    _eh_devolucao_nota_entrada,
    _eh_devolucao_por_cfop_itens,
    _extrair_codigo_origem_nf,
    _get_vendas_db_path,
    _normalizar_nome_loja_virtual_candidato,
    _normalizar_texto,
    _normalizar_unidade_devolucao_entrada,
    _resolver_nome_loja_virtual,
    _tipo_devolucao_cfop_full_estoque,
    buscar_loja,
)
from .performance import invalidate_vendas_cache, legacy_store_conflicts
from .progress import (
    _criar_progresso,
    _set_progresso,
    _sync_context_key,
    _sync_log,
    _verificar_cancelamento,
)
from .state import SYNC_CANCEL_FLAGS, SYNC_LOGS


_SQL_INSERT_VENDAS = """
    INSERT OR REPLACE INTO vendas
    (id_unico, data, loja_conta, canal, numero, situacao, devolucao, sku, produto,
     quantidade, valor, mes_ano, numero_nf, comprador, unidade_negocio,
     nota_fiscal_id, loja_id, unidade_id, intermediador_nome, intermediador_cnpj)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SQL_INSERT_VENDAS_IDENTIFICADAS = _SQL_INSERT_VENDAS.replace(
    "intermediador_nome, intermediador_cnpj)",
    "intermediador_nome, intermediador_cnpj, origem_tipo, origem_id, item_id)",
).replace("VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")

_SQL_INSERT_NOTAS = """
    INSERT OR REPLACE INTO notas_entrada
    (id_unico, id_bling, numero, data_emissao, natureza_operacao,
     finalidade_operacao, devolucao, valor, fornecedor, origem_codigo,
     loja_conta, unidade_negocio, unidade_negocio_virtual)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SQL_INSERT_ITENS = """
    INSERT OR REPLACE INTO notas_entrada_itens
    (id_unico, id_nota, numero_nota, origem_codigo, data_emissao, sku, descricao,
     quantidade, valor_unitario, valor_total, natureza_operacao,
     finalidade_operacao, devolucao, fornecedor, loja_conta, unidade_negocio,
     unidade_negocio_virtual)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _insert_com_store_id(sql: str) -> str:
    columns, values = sql.split("VALUES", 1)
    return columns.rstrip().removesuffix(")") + ", store_id) VALUES" + values.rstrip().removesuffix(")") + ", ?)"

_SQL_CREATE_VENDAS = """
    CREATE TABLE IF NOT EXISTS vendas (
        id_unico TEXT PRIMARY KEY, data TEXT, loja_conta TEXT, canal TEXT,
        numero TEXT, situacao TEXT, sku TEXT, produto TEXT, quantidade REAL,
        valor REAL, mes_ano TEXT, devolucao INTEGER DEFAULT 0, numero_nf TEXT,
        comprador TEXT, unidade_negocio TEXT, nota_fiscal_id TEXT, loja_id TEXT,
        unidade_id TEXT, intermediador_nome TEXT, intermediador_cnpj TEXT
    )
"""

_SQL_CREATE_NOTAS = """
    CREATE TABLE IF NOT EXISTS notas_entrada (
        id_unico TEXT PRIMARY KEY, id_bling TEXT, numero TEXT, data_emissao TEXT,
        natureza_operacao TEXT, finalidade_operacao TEXT,
        devolucao INTEGER DEFAULT 0, valor REAL, fornecedor TEXT,
        origem_codigo TEXT, loja_conta TEXT, unidade_negocio TEXT,
        unidade_negocio_virtual TEXT
    )
"""

_SQL_CREATE_ITENS = """
    CREATE TABLE IF NOT EXISTS notas_entrada_itens (
        id_unico TEXT PRIMARY KEY, id_nota TEXT, numero_nota TEXT,
        data_emissao TEXT, sku TEXT, descricao TEXT, quantidade REAL,
        valor_unitario REAL, valor_total REAL, natureza_operacao TEXT,
        finalidade_operacao TEXT, devolucao INTEGER DEFAULT 0,
        fornecedor TEXT, origem_codigo TEXT, loja_conta TEXT,
        unidade_negocio TEXT, unidade_negocio_virtual TEXT
    )
"""


_SYNC_SCHEMA_COLUMNS = {
    "vendas": {
        "data": "TEXT",
        "loja_conta": "TEXT",
        "canal": "TEXT",
        "numero": "TEXT",
        "situacao": "TEXT",
        "sku": "TEXT",
        "produto": "TEXT",
        "quantidade": "REAL",
        "valor": "REAL",
        "mes_ano": "TEXT",
        "devolucao": "INTEGER DEFAULT 0",
        "numero_nf": "TEXT",
        "comprador": "TEXT",
        "unidade_negocio": "TEXT",
        "nota_fiscal_id": "TEXT",
        "loja_id": "TEXT",
        "unidade_id": "TEXT",
        "intermediador_nome": "TEXT",
        "intermediador_cnpj": "TEXT",
        "origem_tipo": "TEXT",
        "origem_id": "TEXT",
        "item_id": "TEXT",
        "store_id": "TEXT",
    },
    "notas_entrada": {
        "store_id": "TEXT",
        "id_bling": "TEXT",
        "numero": "TEXT",
        "data_emissao": "TEXT",
        "natureza_operacao": "TEXT",
        "finalidade_operacao": "TEXT",
        "devolucao": "INTEGER DEFAULT 0",
        "valor": "REAL",
        "fornecedor": "TEXT",
        "origem_codigo": "TEXT",
        "loja_conta": "TEXT",
        "unidade_negocio": "TEXT",
        "unidade_negocio_virtual": "TEXT",
    },
    "notas_entrada_itens": {
        "store_id": "TEXT",
        "id_nota": "TEXT",
        "numero_nota": "TEXT",
        "data_emissao": "TEXT",
        "sku": "TEXT",
        "descricao": "TEXT",
        "quantidade": "REAL",
        "valor_unitario": "REAL",
        "valor_total": "REAL",
        "natureza_operacao": "TEXT",
        "finalidade_operacao": "TEXT",
        "devolucao": "INTEGER DEFAULT 0",
        "fornecedor": "TEXT",
        "origem_codigo": "TEXT",
        "loja_conta": "TEXT",
        "unidade_negocio": "TEXT",
        "unidade_negocio_virtual": "TEXT",
    },
}


def _ensure_table_columns(
    cursor: sqlite3.Cursor, table: str, expected: dict[str, str]
) -> None:
    current = {
        str(row[1]) for row in cursor.execute(f'PRAGMA table_info("{table}")').fetchall()
    }
    if "id_unico" not in current:
        raise sqlite3.DatabaseError(f"Tabela {table} sem a chave id_unico")
    for column, definition in expected.items():
        if column not in current:
            cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')


def _ensure_sync_schema(cursor: sqlite3.Cursor) -> None:
    """Cria/migra o schema indispensavel dentro da transacao do proprio dia."""

    cursor.execute(_SQL_CREATE_VENDAS)
    cursor.execute(_SQL_CREATE_NOTAS)
    cursor.execute(_SQL_CREATE_ITENS)
    for table, columns in _SYNC_SCHEMA_COLUMNS.items():
        _ensure_table_columns(cursor, table, columns)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendas_documento_origem ON vendas(loja_conta, origem_tipo, origem_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendas_store_documento ON vendas(store_id, origem_tipo, origem_id)")


def _normalizar_numero_deduplicacao_venda(valor: Any) -> str:
    return re.sub(r"\s+", "", str(valor or "").strip())


def _normalizar_valor_deduplicacao_venda(valor: Any) -> str:
    try:
        return f"{float(valor):.8f}".rstrip("0").rstrip(".")
    except Exception:
        return str(valor or "").strip()


def _montar_chave_deduplicacao_venda(
    loja_conta: str,
    numero: str,
    sku: str,
    data: str,
    numero_nf: str = "",
    nota_fiscal_id: str = "",
    canal: str = "",
    comprador: str = "",
    quantidade: Any = 0,
    valor: Any = 0,
    id_pedido: str = "",
) -> tuple:
    return (
        _normalizar_numero_deduplicacao_venda(loja_conta).lower(),
        _normalizar_numero_deduplicacao_venda(id_pedido or numero_nf or numero).lower(),
        _normalizar_numero_deduplicacao_venda(sku).upper(),
        _normalizar_numero_deduplicacao_venda(data)[:10],
        _normalizar_numero_deduplicacao_venda(canal).lower(),
        _normalizar_numero_deduplicacao_venda(comprador).lower(),
        _normalizar_valor_deduplicacao_venda(quantidade),
        _normalizar_valor_deduplicacao_venda(valor),
        _normalizar_numero_deduplicacao_venda(nota_fiscal_id),
        _normalizar_numero_deduplicacao_venda(numero_nf),
    )


def _montar_id_unico_venda(
    loja_conta: str,
    id_pedido: str = "",
    numero_nf: str = "",
    numero: str = "",
    sku: str = "",
    quantidade: Any = 0,
    valor: Any = 0,
) -> str:
    referencia = str(id_pedido or numero_nf or numero or "sem_numero").strip()
    referencia = _normalizar_numero_deduplicacao_venda(referencia or "sem_numero")
    sku_normalizado = _normalizar_numero_deduplicacao_venda(sku).upper() or "sem_sku"
    quantidade_normalizada = _normalizar_valor_deduplicacao_venda(quantidade) or "0"
    valor_normalizado = _normalizar_valor_deduplicacao_venda(valor) or "0"
    loja = _normalizar_numero_deduplicacao_venda(loja_conta).replace("/", "_") or "sem_loja"
    return f"{loja}_{referencia}_{sku_normalizado}_{quantidade_normalizada}_{valor_normalizado}"


def _id_item_documento(loja: str, tipo: str, documento: str, item: str) -> str:
    material = json.dumps([loja, tipo, documento, item], ensure_ascii=False, separators=(",", ":"))
    return "bling_v2_" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _coerce_status_code(status: Any) -> int:
    try:
        return int(status)
    except (TypeError, ValueError):
        return 0


def _raise_for_bling_status(status: Any, contexto: str) -> None:
    status_code = _coerce_status_code(status)
    if status_code == 200:
        return
    if status_code == 401:
        raise HTTPException(
            status_code=401,
            detail="Token Bling expirado. Refaça a conexão em Integrações.",
            code="bling_reconnect_required",
        )
    if status_code == 429:
        raise HTTPException(
            status_code=429,
            detail=f"Limite de solicitações da Bling atingido durante {contexto}.",
        )
    if status_code == 503:
        raise HTTPException(
            status_code=503,
            detail=f"Bling indisponível durante {contexto}.",
        )
    raise HTTPException(
        status_code=502,
        detail=f"Resposta inválida da Bling durante {contexto} (HTTP {status_code or 'desconhecido'}).",
    )


def _require_list(value: Any, contexto: str) -> list:
    if not isinstance(value, list):
        raise HTTPException(
            status_code=502,
            detail=f"Resposta inválida da Bling durante {contexto}.",
        )
    return value


def _require_date_in_period(value: Any, req: VendasSyncRequest, contexto: str) -> str:
    text = str(value or "").strip()
    day = text[:10]
    try:
        datetime.fromisoformat(day)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Data inválida da Bling durante {contexto}.",
        ) from exc
    if day < str(req.data_inicio)[:10] or day > str(req.data_fim)[:10]:
        raise HTTPException(
            status_code=502,
            detail=f"Registro fora do período solicitado durante {contexto}.",
        )
    return text


def _preparar_payload_vendas(
    registros: list[dict], req: VendasSyncRequest, client_id: str
) -> list[tuple]:
    payload: list[tuple] = []
    chaves_processadas: set[tuple] = set()
    linhas_identificadas: dict[tuple, dict] = {}
    indices_documentos: dict[tuple, int] = {}
    total = len(registros)
    lote_size = 100
    total_lotes = (total + lote_size - 1) // lote_size

    for idx, registro in enumerate(registros):
        if not isinstance(registro, dict):
            raise HTTPException(status_code=502, detail="Venda inválida retornada pela Bling.")
        if idx % lote_size == 0:
            _verificar_cancelamento(client_id)
        if idx and idx % lote_size == 0:
            lote = (idx // lote_size) + 1
            percentual = int((idx / max(total, 1)) * 100)
            _sync_log(
                client_id,
                f"[SYNC] Preparando lote {lote}/{total_lotes} - {idx}/{total} ({percentual}%)",
            )
            _set_progresso(
                client_id,
                _criar_progresso(
                    "Vendas",
                    lote,
                    total_lotes,
                    min(60, 40 + int((idx / max(total, 1)) * 20)),
                    f"Preparando lote {lote}/{total_lotes}",
                ),
            )

        data_str = _require_date_in_period(registro.get("data"), req, "vendas")
        data_base = data_str[:10]
        try:
            mes_ano = datetime.fromisoformat(data_base).strftime("%Y-%m")
        except Exception:
            mes_ano = ""
        numero = registro.get("numero") or ""
        sku = registro.get("sku") or ""
        id_pedido = str(registro.get("id_pedido") or "").strip()
        numero_nf = str(registro.get("numero_nf") or "").strip()
        nota_fiscal_id = str(registro.get("nota_fiscal_id") or "").strip()
        origem_tipo = str(registro.get("origem_tipo") or ("pedido" if id_pedido else "")).strip()
        origem_id = str(registro.get("origem_id") or id_pedido or "").strip()
        document_key = (origem_tipo, origem_id)
        indices_documentos[document_key] = indices_documentos.get(document_key, 0) + 1
        item_id = str(registro.get("item_id") or indices_documentos[document_key])
        id_unico = _montar_id_unico_venda(
            loja_conta=req.loja,
            id_pedido=id_pedido,
            numero_nf=numero_nf,
            numero=numero,
            sku=sku,
            quantidade=registro.get("quantidade", 0),
            valor=registro.get("valor", 0),
        )
        chave = _montar_chave_deduplicacao_venda(
            loja_conta=req.loja,
            numero=numero,
            sku=sku,
            data=data_base,
            numero_nf=numero_nf,
            nota_fiscal_id=nota_fiscal_id,
            canal=registro.get("canal", ""),
            comprador=registro.get("comprador", ""),
            quantidade=registro.get("quantidade", 0),
            valor=registro.get("valor", 0),
            id_pedido=id_pedido,
        )
        if origem_tipo and origem_id:
            identity = (origem_tipo, origem_id, item_id)
            previous = linhas_identificadas.get(identity)
            if previous is not None:
                if previous != registro:
                    raise HTTPException(status_code=502, detail="Item de venda repetido com dados conflitantes.")
                continue
            linhas_identificadas[identity] = registro
            id_unico = _id_item_documento(str(getattr(req, "store_id", None) or req.loja), origem_tipo, origem_id, item_id)
        else:
            if chave in chaves_processadas:
                continue
            chaves_processadas.add(chave)
        row = (
                id_unico,
                data_str,
                req.loja,
                registro.get("canal"),
                numero,
                registro.get("situacao"),
                registro.get("devolucao", 0),
                sku,
                registro.get("produto"),
                registro.get("quantidade", 0),
                registro.get("valor", 0),
                mes_ano,
                numero_nf,
                registro.get("comprador"),
                registro.get("unidade_negocio", ""),
                nota_fiscal_id,
                registro.get("loja_id", ""),
                registro.get("unidade_id", ""),
                registro.get("intermediador_nome", ""),
                registro.get("intermediador_cnpj", ""),
        )
        if origem_tipo and origem_id:
            row += (origem_tipo, origem_id, item_id)
        payload.append(row)
    return payload


def _preparar_payload_notas(
    notas: list[dict], notas_itens: list[dict], req: VendasSyncRequest, client_id: str
) -> tuple[list[tuple], list[tuple]]:
    payload_notas: dict[str, tuple] = {}
    for nota in notas:
        _verificar_cancelamento(client_id)
        if not isinstance(nota, dict):
            raise HTTPException(status_code=502, detail="Nota de entrada inválida retornada pela Bling.")
        data_str = _require_date_in_period(nota.get("data_emissao"), req, "notas de entrada")
        data_base = data_str[:10]
        unidade_virtual = nota.get("unidade_negocio_virtual")
        if int(nota.get("devolucao") or 0) == 1:
            unidade_virtual = _classificar_unidade_virtual_devolucao(
                unidade_virtual,
                nota.get("natureza_operacao"),
                req.loja,
            )
        id_bling = nota.get("id")
        id_unico = (
            f"{req.loja}_{id_bling}"
            if id_bling is not None
            else f"{req.loja}_{nota.get('numero')}_{data_base}"
        )
        if getattr(req, "store_id", None) and id_bling is not None:
            id_unico = _id_item_documento(str(req.store_id), "nfe_entrada", str(id_bling), "header")
        payload_notas[id_unico] = (
            id_unico,
            id_bling,
            nota.get("numero"),
            data_str,
            nota.get("natureza_operacao"),
            nota.get("finalidade_operacao"),
            nota.get("devolucao", 0),
            nota.get("valor", 0),
            nota.get("fornecedor"),
            nota.get("origem_codigo"),
            req.loja,
            nota.get("unidade_negocio"),
            unidade_virtual,
        )

    payload_itens: dict[str, tuple] = {}
    indices_notas: dict[str, int] = {}
    for idx, item in enumerate(notas_itens):
        if idx % 100 == 0:
            _verificar_cancelamento(client_id)
        if not isinstance(item, dict):
            raise HTTPException(status_code=502, detail="Item de nota inválido retornado pela Bling.")
        data_item = _require_date_in_period(
            item.get("data_emissao"), req, "itens de notas de entrada"
        )
        unidade_virtual = item.get("unidade_negocio_virtual")
        if int(item.get("devolucao") or 0) == 1:
            unidade_virtual = _classificar_unidade_virtual_devolucao(
                unidade_virtual,
                item.get("natureza_operacao"),
                req.loja,
            )
        nota_id = str(item.get("id_nota") or "")
        indices_notas[nota_id] = indices_notas.get(nota_id, 0) + 1
        item_id = str(item.get("item_id") or indices_notas[nota_id])
        id_unico = _id_item_documento(str(getattr(req, "store_id", None) or req.loja), "nfe_entrada", nota_id, item_id)
        if id_unico in payload_itens:
            raise HTTPException(status_code=502, detail="Item de nota repetido no documento coletado.")
        payload_itens[id_unico] = (
            id_unico,
            item.get("id_nota"),
            item.get("numero_nota"),
            item.get("origem_codigo"),
            data_item,
            item.get("sku"),
            item.get("descricao"),
            item.get("quantidade"),
            item.get("valor_unitario"),
            item.get("valor_total"),
            item.get("natureza_operacao"),
            item.get("finalidade_operacao"),
            item.get("devolucao", 0),
            item.get("fornecedor"),
            req.loja,
            item.get("unidade_negocio"),
            unidade_virtual,
        )
    return list(payload_notas.values()), list(payload_itens.values())


def _filtrar_vendas_ja_existentes(
    cursor: sqlite3.Cursor, payload: list[tuple], loja: str, store_id: str = ""
) -> list[tuple]:
    if not payload:
        return []
    existentes_ids: set[str] = set()
    chaves_existentes: set[tuple] = set()
    for row in cursor.execute(
        """
        SELECT loja_conta, numero, sku, data, numero_nf, nota_fiscal_id,
               canal, comprador, quantidade, valor, id_unico
        FROM vendas
        WHERE (store_id = ? AND ? != '')
           OR (coalesce(store_id, '') = '' AND ? = '' AND loja_conta = ?)
        """,
        (store_id, store_id, store_id, loja),
    ).fetchall():
        if str(row[10] or "").strip():
            existentes_ids.add(str(row[10]).strip())
        chaves_existentes.add(
            _montar_chave_deduplicacao_venda(
                loja_conta=row[0] or "",
                numero=row[1] or "",
                sku=row[2] or "",
                data=str(row[3] or "")[:10],
                numero_nf=row[4] or "",
                nota_fiscal_id=row[5] or "",
                canal=row[6] or "",
                comprador=row[7] or "",
                quantidade=row[8] or 0,
                valor=row[9] or 0,
            )
        )

    hoje = datetime.now().date().isoformat()
    filtrado: list[tuple] = []
    for item in payload:
        data_base = str(item[1] or "")[:10]
        chave = _montar_chave_deduplicacao_venda(
            loja_conta=item[2] or "",
            numero=item[4] or "",
            sku=item[7] or "",
            data=data_base,
            numero_nf=item[12] or "",
            nota_fiscal_id=item[15] or "",
            canal=item[3] or "",
            comprador=item[13] or "",
            quantidade=item[9] or 0,
            valor=item[10] or 0,
        )
        if data_base != hoje and (str(item[0]) in existentes_ids or chave in chaves_existentes):
            continue
        filtrado.append(item)
    return filtrado


def _escopo_store(req: VendasSyncRequest) -> tuple[str, tuple]:
    store_id = str(getattr(req, "store_id", None) or "").strip()
    if store_id:
        return "store_id = ?", (store_id,)
    return "coalesce(store_id, '') = '' AND loja_conta = ?", (req.loja,)


def _legado_loja_nao_ambiguo(cursor: sqlite3.Cursor, req: VendasSyncRequest) -> bool:
    """A known different account with the same name prevents legacy adoption."""
    store_id = str(getattr(req, "store_id", None) or "").strip()
    for table in ("vendas", "notas_entrada", "notas_entrada_itens"):
        if cursor.execute(
            f"SELECT 1 FROM {table} WHERE loja_conta=? AND trim(coalesce(store_id,''))!='' "
            "AND store_id!=? LIMIT 1", (req.loja, store_id),
        ).fetchone():
            return False
    return True


def _substituir_documentos_vendas(
    cursor: sqlite3.Cursor, rows: list[tuple], req: VendasSyncRequest,
    notas_saida_observadas: set[str] | None = None,
) -> None:
    """Replace only observed documents; keep unrelated/unknown legacy sales."""
    documents: dict[tuple[str, str], list[tuple]] = {}
    for row in rows:
        documents.setdefault((row[20], row[21]), []).append(row)
    observed_invoices = set(notas_saida_observadas or ())
    if not documents and not observed_invoices:
        return
    store_clause, store_args = _escopo_store(req)
    legacy_allowed = _legado_loja_nao_ambiguo(cursor, req)
    if getattr(req, "store_id", None) and legacy_allowed:
        store_clause = f"({store_clause}) OR (coalesce(store_id,'')='' AND loja_conta=?)"
        store_args += (req.loja,)
    # A legacy row carries its document reference in id_unico, but no typed ID.
    # Adopt it only when the exact old identity can be reconstructed. Never
    # remove records merely because buyer, amount or SKU happen to be equal.
    legacy_rows = cursor.execute(
        "SELECT id_unico, sku, quantidade, valor, numero_nf, numero, nota_fiscal_id, canal, "
        "data, comprador, origem_id, item_id "
        "FROM vendas WHERE loja_conta=? AND coalesce(store_id,'')='' AND coalesce(origem_tipo,'')=''",
        (req.loja,),
    ).fetchall() if legacy_allowed else []
    # Index the evidence from each observed document once. Reference strings
    # retain the old ID normalizer, while invoice/number checks stay exact.
    orders_by_reference: dict[str, tuple[set[str], set[str]]] = {}
    orders_without_reference: tuple[set[str], set[str]] = (set(), set())
    order_skus_by_invoice: dict[str, set[str]] = {}
    invoice_documents: set[Any] = set()
    other_document_kind = False
    for invoice_id in observed_invoices:
        cursor.execute(
            f"DELETE FROM vendas WHERE ({store_clause}) AND origem_tipo='nfe_saida' AND origem_id=?",
            (*store_args, invoice_id),
        )
    for (kind, document_id), document_rows in documents.items():
        cursor.execute(
            f"DELETE FROM vendas WHERE ({store_clause}) AND origem_tipo=? AND origem_id=?",
            (*store_args, kind, document_id),
        )
        invoice_ids = {str(row[15]) for row in document_rows if str(row[15] or "")}
        covered_skus = {str(row[7] or "").strip().upper() for row in document_rows}
        if kind == "pedido":
            if document_id:
                reference = _normalizar_numero_deduplicacao_venda(
                    str(document_id).strip() or "sem_numero"
                )
                numbers, invoices = orders_by_reference.setdefault(reference, (set(), set()))
            else:
                numbers, invoices = orders_without_reference
            numbers.update(str(row[4] or "") for row in document_rows)
            invoices.update(invoice_ids)
            for invoice_id in invoice_ids:
                order_skus_by_invoice.setdefault(invoice_id, set()).update(covered_skus)
                cursor.executemany(
                    f"DELETE FROM vendas WHERE ({store_clause}) AND origem_tipo='nfe_saida' AND origem_id=? AND upper(trim(coalesce(sku,'')))=?",
                    [(*store_args, invoice_id, sku) for sku in covered_skus],
                )
        elif kind == "nfe_saida":
            invoice_documents.add(document_id)
        else:
            other_document_kind = True

    remove_legacy: set[str] = set()
    prefix = (
        _normalizar_numero_deduplicacao_venda(req.loja).replace("/", "_") or "sem_loja"
    ) + "_"
    for legacy in legacy_rows:
        legacy_id, sku, quantity, amount, invoice_number, number, invoice_id, _channel = legacy[:8]
        legacy_id = str(legacy_id)
        invoice = str(invoice_id or "")
        legacy_invoice_id = _montar_id_unico_venda(
            req.loja, numero_nf=invoice_number, numero=number, sku=sku,
            quantidade=quantity, valor=amount,
        )
        invoice_identity = legacy_id == legacy_invoice_id
        if invoice_identity and (
            invoice in observed_invoices or invoice in invoice_documents
            or other_document_kind
            or str(sku or "").strip().upper() in order_skus_by_invoice.get(invoice, ())
        ):
            remove_legacy.add(legacy_id)
            continue

        # Split only the known prefix/suffix of this exact legacy row. SKU and
        # reference may contain underscores; splitting on separators loses IDs.
        suffix = (
            "_" + (_normalizar_numero_deduplicacao_venda(sku).upper() or "sem_sku")
            + "_" + (_normalizar_valor_deduplicacao_venda(quantity) or "0")
            + "_" + (_normalizar_valor_deduplicacao_venda(amount) or "0")
        )
        reference = None
        if (
            legacy_id.startswith(prefix) and legacy_id.endswith(suffix)
            and len(legacy_id) > len(prefix) + len(suffix)
        ):
            reference = legacy_id[len(prefix):-len(suffix)]
        numbers, invoices = orders_by_reference.get(reference, ((), ()))
        if str(number or "") in numbers or (invoice and invoice in invoices):
            remove_legacy.add(legacy_id)
        elif invoice_identity and (
            str(number or "") in orders_without_reference[0]
            or (invoice and invoice in orders_without_reference[1])
        ):
            remove_legacy.add(legacy_id)
    # The earliest IDs contain only account, displayed order number and SKU.
    # Migrate them using the same documentary evidence used by read endpoints;
    # the typed item IDs still preserve every legitimate repeated line.
    store_id = str(getattr(req, "store_id", None) or "").strip()
    if store_id and legacy_allowed and rows:
        legacy_fields = (
            "id_unico", "sku", "quantidade", "valor", "numero_nf", "numero",
            "nota_fiscal_id", "canal", "data", "comprador", "origem_id", "item_id",
        )
        old_rows = [dict(zip(legacy_fields, row), loja_conta=req.loja) for row in legacy_rows]
        typed_fields = {
            "id_unico": 0, "data": 1, "loja_conta": 2, "canal": 3, "numero": 4,
            "devolucao": 6, "sku": 7, "quantidade": 9, "valor": 10, "numero_nf": 12,
            "comprador": 13, "nota_fiscal_id": 15, "origem_tipo": 20, "origem_id": 21, "item_id": 22,
        }
        new_rows = [
            dict(((key, row[index]) for key, index in typed_fields.items()), store_id=store_id)
            for row in rows
        ]
        # A homonymous account may exist only in another historical database
        # of this tenant. Include it before adopting a row with no store ID.
        source_path = next((row[2] for row in cursor.execute("PRAGMA database_list") if row[1] == "main"), "")
        identity_sources = [source_path] if source_path else []
        if source_path:
            identity_sources.extend(
                str(path) for path in Path(source_path).parent.glob("vendas_historico*.db")
                if ".backup_" not in path.name
            )
        blocked = legacy_store_conflicts(identity_sources, old_rows, store_id=store_id)
        kept = {
            row["id_unico"] for row in without_legacy_sale_duplicates(
                old_rows + new_rows, blocked_store_names=blocked,
            )
        }
        remove_legacy.update(row["id_unico"] for row in old_rows if row["id_unico"] not in kept)
    cursor.executemany("DELETE FROM vendas WHERE coalesce(store_id,'')='' AND loja_conta=? AND id_unico=?",
                       [(req.loja, row_id) for row_id in remove_legacy])


def _persistir_periodo_atomico(
    db_path: str,
    req: VendasSyncRequest,
    payload_vendas: list[tuple],
    payload_notas: list[tuple],
    payload_itens: list[tuple],
    client_id: str,
    *,
    notas_saida_observadas: set[str] | None = None,
) -> dict[str, int]:
    """Persist all commercial tables in one SQLite transaction."""

    with sqlite_lock_for_path(db_path):
        existed_before = os.path.exists(db_path)
        committed = False
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            # Alterar journal_mode ocorre fora de transacao no SQLite. Aqui so
            # aplicamos busy_timeout para que uma falha preserve o arquivo.
            configure_sqlite_connection(conn)
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.cursor()
            _ensure_sync_schema(cursor)
            store_id = str(getattr(req, "store_id", None) or "").strip()
            store_clause, store_args = _escopo_store(req)
            legacy_allowed = _legado_loja_nao_ambiguo(cursor, req)

            if req.forcar_resync:
                force_clause, force_args = store_clause, store_args
                if store_id and legacy_allowed:
                    force_clause += " OR (coalesce(store_id,'')='' AND loja_conta=?)"
                    force_args += (req.loja,)
                elif not store_id and not legacy_allowed:
                    force_clause, force_args = "0", ()
                cursor.execute(
                    f"DELETE FROM vendas WHERE ({force_clause}) AND date(data) BETWEEN ? AND ?",
                    (*force_args, req.data_inicio, req.data_fim),
                )
                cursor.execute(
                    f"DELETE FROM notas_entrada WHERE ({force_clause}) AND date(data_emissao) BETWEEN ? AND ?",
                    (*force_args, req.data_inicio, req.data_fim),
                )
                cursor.execute(
                    f"DELETE FROM notas_entrada_itens WHERE ({force_clause}) AND date(data_emissao) BETWEEN ? AND ?",
                    (*force_args, req.data_inicio, req.data_fim),
                )
            identified_sales = [row for row in payload_vendas if len(row) == 23]
            legacy_sales = [row for row in payload_vendas if len(row) == 20]
            if len(identified_sales) + len(legacy_sales) != len(payload_vendas):
                raise HTTPException(status_code=502, detail="Formato invalido de venda para persistencia.")
            _substituir_documentos_vendas(cursor, identified_sales, req, notas_saida_observadas)
            vendas_para_salvar = legacy_sales if req.forcar_resync else _filtrar_vendas_ja_existentes(cursor, legacy_sales, req.loja, store_id)
            _verificar_cancelamento(client_id)

            if vendas_para_salvar:
                cursor.executemany(_insert_com_store_id(_SQL_INSERT_VENDAS), [(*row, store_id) for row in vendas_para_salvar])
            if identified_sales:
                cursor.executemany(_insert_com_store_id(_SQL_INSERT_VENDAS_IDENTIFICADAS), [(*row, store_id) for row in identified_sales])
            _verificar_cancelamento(client_id)

            if payload_notas:
                # The full details of each observed note are validated before
                # this transaction. Replace its items, including removed SKUs.
                for row in payload_notas:
                    note_clause, note_args = store_clause, store_args
                    note_id = str(row[1])
                    if store_id and legacy_allowed:
                        legacy_header = cursor.execute(
                            "SELECT id_unico FROM notas_entrada WHERE coalesce(store_id,'')='' "
                            "AND loja_conta=? AND CAST(id_bling AS TEXT)=?", (req.loja, note_id),
                        ).fetchall()
                        if any(str(header[0]) in {
                            f"{req.loja}_{note_id}",
                            _id_item_documento(req.loja, "nfe_entrada", note_id, "header"),
                        } for header in legacy_header):
                            note_clause += " OR (coalesce(store_id,'')='' AND loja_conta=?)"
                            note_args += (req.loja,)
                    cursor.execute(
                        f"DELETE FROM notas_entrada_itens WHERE ({note_clause}) AND CAST(id_nota AS TEXT)=?",
                        (*note_args, note_id),
                    )
                    cursor.execute(
                        f"DELETE FROM notas_entrada WHERE ({note_clause}) AND CAST(id_bling AS TEXT)=?",
                        (*note_args, note_id),
                    )
                cursor.executemany(_insert_com_store_id(_SQL_INSERT_NOTAS), [(*row, store_id) for row in payload_notas])
            _verificar_cancelamento(client_id)

            if payload_itens:
                cursor.executemany(_insert_com_store_id(_SQL_INSERT_ITENS), [(*row, store_id) for row in payload_itens])
            _verificar_cancelamento(client_id)

            conn.commit()
            committed = True
            return {
                "vendas": len(vendas_para_salvar) + len(identified_sales),
                "notas": len(payload_notas),
                "itens": len(payload_itens),
            }
        except BaseException as exc:
            if conn.in_transaction:
                conn.rollback()
            if isinstance(exc, HTTPException):
                raise
            if isinstance(exc, Exception):
                raise HTTPException(
                    status_code=500,
                    detail=f"Erro ao salvar sincronização: {exc}",
                ) from exc
            raise
        finally:
            conn.close()
            if not committed and not existed_before:
                for candidate in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
                    try:
                        if os.path.exists(candidate):
                            os.remove(candidate)
                    except OSError:
                        pass


def _nota_suspeita_para_detalhe(nota: dict) -> bool:
    if not isinstance(nota, dict) or int(nota.get("devolucao") or 0) == 1:
        return False
    natureza = _normalizar_texto(nota.get("natureza_operacao") or "")
    finalidade = _normalizar_texto(nota.get("finalidade_operacao") or "")
    origem = str(nota.get("origem_codigo") or "").strip()
    unidade = str(nota.get("unidade_negocio") or "").strip()
    return (
        (not finalidade and ("COMPRA" in natureza or "COMERCIALIZ" in natureza))
        or (not finalidade and not origem)
        or not unidade
    )


def _aplicar_detalhe_nota(
    nota: dict,
    detalhe: dict,
    mapa_lojas_cliente: dict,
) -> list[dict]:
    natureza = nota.get("natureza_operacao")
    natureza_obj = detalhe.get("naturezaOperacao") or {}
    if isinstance(natureza_obj, dict):
        natureza = natureza_obj.get("descricao") or natureza_obj.get("nome") or natureza

    finalidade = nota.get("finalidade_operacao") or ""
    finalidade_obj = detalhe.get("finalidade") or detalhe.get("finalidadeOperacao") or {}
    if isinstance(finalidade_obj, dict):
        finalidade = (
            finalidade_obj.get("descricao")
            or finalidade_obj.get("nome")
            or finalidade_obj.get("label")
            or finalidade_obj.get("valor")
            or finalidade
        )
    elif finalidade_obj:
        finalidade = str(finalidade_obj)

    unidade = str(nota.get("unidade_negocio") or "")
    loja_descricao = ""
    loja_id = ""
    unidade_id = ""
    loja = detalhe.get("loja") or {}
    if isinstance(loja, dict):
        loja_id = str(loja.get("id") or "").strip()
        unidade_obj = loja.get("unidadeNegocio") or {}
        if isinstance(unidade_obj, dict):
            unidade_id = str(unidade_obj.get("id") or "").strip()
            unidade = str(unidade_obj.get("nome") or unidade_obj.get("descricao") or unidade).strip()
        loja_descricao = str(loja.get("descricao") or "").strip()
        if not unidade:
            unidade = loja_descricao

    intermediador = detalhe.get("intermediador") or {}
    intermediador_nome = ""
    intermediador_cnpj = ""
    if isinstance(intermediador, dict):
        intermediador_nome = _normalizar_nome_loja_virtual_candidato(
            intermediador.get("nomeUsuario")
        )
        intermediador_cnpj = str(intermediador.get("cnpj") or "").strip()
    unidade_resolvida = _resolver_nome_loja_virtual(
        mapa_lojas_cliente,
        loja_id=loja_id,
        unidade_id=unidade_id,
        nome_oficial=unidade or loja_descricao,
        intermediador_nome=intermediador_nome,
        intermediador_cnpj=intermediador_cnpj,
        canal=loja_descricao,
    )

    origem = _extrair_codigo_origem_nf(detalhe) or nota.get("origem_codigo")
    devolucao = _eh_devolucao_nota_entrada(
        natureza, unidade, loja_descricao, str(finalidade or "")
    )
    unidade_virtual = _normalizar_unidade_devolucao_entrada(
        unidade_resolvida or unidade, natureza, loja_descricao
    )
    itens = detalhe.get("itens", [])
    if not isinstance(itens, list) or not itens:
        raise HTTPException(status_code=502, detail="Itens obrigatórios ausentes no detalhe de nota da Bling.")
    if _eh_devolucao_por_cfop_itens(itens):
        devolucao = 1
    tipo_especial = _tipo_devolucao_cfop_full_estoque(natureza, str(finalidade or ""), itens)
    if tipo_especial:
        unidade_virtual = tipo_especial

    nota.update(
        {
            "natureza_operacao": natureza,
            "finalidade_operacao": str(finalidade or ""),
            "origem_codigo": origem,
            "devolucao": devolucao,
            "unidade_negocio": unidade,
            "unidade_negocio_virtual": unidade_virtual,
        }
    )

    resultado: list[dict] = []
    for item in itens:
        if not isinstance(item, dict):
            raise HTTPException(status_code=502, detail="Item inválido no detalhe de nota da Bling.")
        try:
            quantidade = float(item.get("quantidade") or 0)
            valor_unitario = float(item.get("valor") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="Valor inválido no detalhe de nota da Bling.") from exc
        resultado.append(
            {
                "id_nota": nota.get("id"),
                "numero_nota": nota.get("numero"),
                "data_emissao": nota.get("data_emissao"),
                "sku": item.get("codigo") or item.get("sku") or item.get("id"),
                "descricao": item.get("descricao") or item.get("nome"),
                "quantidade": quantidade,
                "valor_unitario": valor_unitario,
                "valor_total": quantidade * valor_unitario if quantidade and valor_unitario else 0,
                "natureza_operacao": natureza,
                "finalidade_operacao": str(finalidade or ""),
                "devolucao": devolucao,
                "fornecedor": nota.get("fornecedor"),
                "origem_codigo": origem,
                "unidade_negocio": unidade,
                "unidade_negocio_virtual": unidade_virtual,
            }
        )
    return resultado


async def _sincronizar_vendas_periodo_impl(
    req: VendasSyncRequest, client_id: str, reset_estado: bool = True
):
    if reset_estado:
        SYNC_CANCEL_FLAGS.pop(_sync_context_key(client_id), None)
        SYNC_LOGS[_sync_context_key(client_id)] = []
        _set_progresso(
            client_id,
            _criar_progresso("Preparando", 0, 0, 0, "Iniciando sincronização."),
        )
        _sync_log(client_id, "[SYNC] Iniciando sincronização")
    if not req.loja or req.loja == "__todas":
        raise HTTPException(status_code=400, detail="Selecione uma loja para sincronizar.")
    if not req.data_inicio or not req.data_fim:
        raise HTTPException(status_code=400, detail="Informe data inicial e final.")

    requested_store_id = str(getattr(req, "store_id", None) or "").strip()
    loja = buscar_loja(client_id, req.loja, store_id=requested_store_id) if requested_store_id else buscar_loja(client_id, req.loja)
    if not loja:
        raise HTTPException(status_code=404, detail="Loja não encontrada para o cliente.")
    store_id = str(loja.get("store_id") or "").strip()
    if requested_store_id and requested_store_id != store_id:
        raise HTTPException(status_code=409, detail="A conta da sincronizacao nao corresponde a loja conectada.")
    if not store_id:
        raise HTTPException(
            status_code=409,
            detail="Loja sem store_id persistido; recarregue a configuração.",
        )
    req = req.model_copy(update={"store_id": store_id})
    bling_cfg = loja.get("integracoes", {}).get("bling")
    if not bling_cfg:
        raise HTTPException(status_code=400, detail="Loja não possui integração Bling conectada.")
    if requires_bling_reauthorization(bling_cfg):
        raise HTTPException(
            status_code=401,
            detail="A conexão Bling desta loja precisa ser refeita em Lojas e APIs.",
            code="bling_reconnect_required",
        )
    if not (
        bling_cfg.get("access_token")
        and (bling_cfg.get("central") or (bling_cfg.get("id") and bling_cfg.get("secret")))
    ):
        raise HTTPException(status_code=400, detail="Credenciais Bling incompletas para esta loja.")

    _verificar_cancelamento(client_id)
    _set_progresso(
        client_id,
        _criar_progresso("Bling", 0, 0, 5, "Conectando na Bling e listando vendas..."),
    )
    unidades_cache: dict = {}
    unidades_mapeamento = _carregar_mapeamento_unidades()
    mapa_lojas_cliente = _carregar_mapeamento_lojas_virtuais_cliente(client_id)

    registros, status_vendas, bling_cfg = _bling_executar_com_refresh(
        client_id,
        req.loja,
        bling_cfg,
        lambda token: _bling_listar_vendas(
            token,
            req.data_inicio,
            req.data_fim,
            req.loja,
            client_id,
            unidades_cache,
            unidades_mapeamento,
            mapa_lojas_cliente,
        ),
        on_refresh=lambda: _set_progresso(
            client_id,
            _criar_progresso("Bling", 0, 0, 8, "Renovando token da Bling..."),
        ),
        store_id=store_id,
    )
    _raise_for_bling_status(status_vendas, "listagem de vendas")
    registros = copy.deepcopy(_require_list(registros, "listagem de vendas"))

    _sync_log(client_id, "[SYNC] Verificando NF-e de saída para complementar vendas...")
    registros_nf, status_nf_saida, bling_cfg = _bling_executar_com_refresh(
        client_id,
        req.loja,
        bling_cfg,
        lambda token: _bling_listar_vendas_fallback_nf_saida(
            token,
            req.data_inicio,
            req.data_fim,
            req.loja,
            client_id,
            mapa_lojas_cliente,
        ),
        on_refresh=lambda: _sync_log(
            client_id, "[SYNC] Renovando token para consultar NF-e de saída..."
        ),
        store_id=store_id,
    )
    if _coerce_status_code(status_nf_saida) == 403:
        _sync_log(
            client_id,
            "[SYNC] NF-e de saída bloqueada pela Bling (403); seguindo apenas com pedidos.",
        )
        registros_nf = []
    else:
        _raise_for_bling_status(status_nf_saida, "listagem de NF-e de saída")
        registros_nf = _require_list(registros_nf, "listagem de NF-e de saída")

    chaves_nf_existentes = {
        (
            str(item.get("nota_fiscal_id") or "").strip(),
            str(item.get("sku") or "").strip().upper(),
            str(item.get("data") or "")[:10],
        )
        for item in registros
        if isinstance(item, dict)
    }
    for item in registros_nf:
        if not isinstance(item, dict):
            raise HTTPException(status_code=502, detail="Venda inválida em NF-e de saída.")
        chave = (
            str(item.get("nota_fiscal_id") or "").strip(),
            str(item.get("sku") or "").strip().upper(),
            str(item.get("data") or "")[:10],
        )
        if chave not in chaves_nf_existentes:
            registros.append(copy.deepcopy(item))

    _sync_log(client_id, f"[SYNC] {len(registros)} itens de venda coletados e validados.")

    # Visible invoice number is optional enrichment. Its failure is logged and does
    # not turn an otherwise complete sale into a partial day.
    nf_enriquecidas = 0
    ids_nf = sorted(
        {
            str(item.get("nota_fiscal_id") or "").strip()
            for item in registros
            if isinstance(item, dict)
            and str(item.get("nota_fiscal_id") or "").strip()
            and not str(item.get("numero_nf") or "").strip()
        }
    )
    for indice, nf_id in enumerate(ids_nf, start=1):
        if indice % 25 == 0:
            _verificar_cancelamento(client_id)
        try:
            numero_nf, status_numero, bling_cfg = _bling_executar_com_refresh(
                client_id,
                req.loja,
                bling_cfg,
                lambda token, current=nf_id: _bling_obter_numero_nf(token, current),
                on_refresh=lambda: _sync_log(
                    client_id, "[SYNC] Renovando token para enriquecer número de NF..."
                ),
                store_id=store_id,
            )
        except HTTPException as exc:
            _sync_log(
                client_id,
                f"[SYNC] Aviso: enriquecimento de NF indisponível (HTTP {exc.status_code}).",
            )
            break
        status_numero_code = _coerce_status_code(status_numero)
        if status_numero_code != 200 or not numero_nf:
            _sync_log(
                client_id,
                f"[SYNC] Aviso: número visível de NF não disponível (HTTP {status_numero_code}).",
            )
            if status_numero_code in {401, 429, 503}:
                break
            continue
        for item in registros:
            if (
                str(item.get("nota_fiscal_id") or "").strip() == nf_id
                and not str(item.get("numero_nf") or "").strip()
            ):
                item["numero_nf"] = str(numero_nf)
                nf_enriquecidas += 1

    nf_pendentes = sum(
        1
        for item in registros
        if str(item.get("nota_fiscal_id") or "").strip()
        and not str(item.get("numero_nf") or "").strip()
    )
    payload_vendas = _preparar_payload_vendas(registros, req, client_id)

    _sync_log(client_id, "[SYNC] Coletando notas fiscais de entrada...")
    _set_progresso(
        client_id,
        _criar_progresso("Notas", 0, 0, 76, "Buscando notas fiscais de entrada..."),
    )
    _verificar_cancelamento(client_id)
    natureza_map, status_naturezas, bling_cfg = _bling_executar_com_refresh(
        client_id,
        req.loja,
        bling_cfg,
        _bling_listar_naturezas,
        on_refresh=lambda: _sync_log(client_id, "[SYNC] Renovando token para naturezas..."),
        store_id=store_id,
    )
    _raise_for_bling_status(status_naturezas, "listagem de naturezas")
    if not isinstance(natureza_map, dict):
        raise HTTPException(status_code=502, detail="Resposta inválida da Bling na listagem de naturezas.")

    def _listar_notas(token: str):
        notas_resultado, itens_resultado, status_resultado = _bling_listar_notas_entrada(
            token,
            req.data_inicio,
            req.data_fim,
            natureza_map,
            client_id,
        )
        return (notas_resultado, itens_resultado), status_resultado

    notas_e_itens, status_notas, bling_cfg = _bling_executar_com_refresh(
        client_id,
        req.loja,
        bling_cfg,
        _listar_notas,
        on_refresh=lambda: _sync_log(client_id, "[SYNC] Renovando token para notas de entrada..."),
        store_id=store_id,
    )
    _raise_for_bling_status(status_notas, "listagem de notas de entrada")
    if not isinstance(notas_e_itens, (tuple, list)) or len(notas_e_itens) != 2:
        raise HTTPException(status_code=502, detail="Resposta inválida da Bling na listagem de notas.")
    notas = copy.deepcopy(_require_list(notas_e_itens[0], "listagem de notas"))
    notas_itens = copy.deepcopy(_require_list(notas_e_itens[1], "listagem de itens de notas"))

    # O helper oficial atesta cada detalhe completo. Fontes legadas ou mocks
    # sem essa atestacao sao detalhados aqui, mesmo que a listagem traga itens.
    notas_para_detalhar = [
        nota
        for nota in notas
        if isinstance(nota, dict)
        and not bool(nota.get("_detalhe_validado"))
    ]
    for indice, nota in enumerate(notas_para_detalhar, start=1):
        _verificar_cancelamento(client_id)
        nota_id = str(nota.get("id") or "").strip()
        if not nota_id:
            raise HTTPException(
                status_code=502,
                detail="Nota obrigatória sem identificador no retorno da Bling.",
            )
        _set_progresso(
            client_id,
            _criar_progresso(
                "Notas",
                indice,
                len(notas_para_detalhar),
                min(90, 82 + int((indice / max(len(notas_para_detalhar), 1)) * 8)),
                f"Validando detalhe de nota {indice}/{len(notas_para_detalhar)}",
            ),
        )
        detalhe, status_detalhe, bling_cfg = _bling_executar_com_refresh(
            client_id,
            req.loja,
            bling_cfg,
            lambda token, current=nota_id: _bling_obter_detalhes_nf(token, current),
            on_refresh=lambda: _sync_log(
                client_id, "[SYNC] Renovando token para detalhe de nota..."
            ),
            store_id=store_id,
        )
        _raise_for_bling_status(status_detalhe, "detalhe obrigatório de nota")
        if not isinstance(detalhe, dict) or not detalhe:
            raise HTTPException(
                status_code=502,
                detail="Detalhe obrigatório de nota ausente na resposta da Bling.",
            )
        notas_itens = [
            item
            for item in notas_itens
            if str((item or {}).get("id_nota") or "").strip() != nota_id
        ]
        notas_itens.extend(_aplicar_detalhe_nota(nota, detalhe, mapa_lojas_cliente))

    payload_notas, payload_itens = _preparar_payload_notas(
        notas, notas_itens, req, client_id
    )
    _sync_log(
        client_id,
        (
            f"[SYNC] Coleta validada: {len(payload_vendas)} vendas, "
            f"{len(payload_notas)} notas e {len(payload_itens)} itens."
        ),
    )

    # Schema e dados sao preparados dentro da mesma transacao do dia.
    _verificar_cancelamento(client_id)
    db_path = _get_vendas_db_path(client_id, req.loja)
    _set_progresso(
        client_id,
        _criar_progresso("Banco", 1, 1, 92, "Gravando o dia em transação única..."),
    )
    persistidos = _persistir_periodo_atomico(
        db_path,
        req,
        payload_vendas,
        payload_notas,
        payload_itens,
        client_id,
        notas_saida_observadas={
            str(item.get("nota_fiscal_id") or "").strip()
            for item in registros_nf if str(item.get("nota_fiscal_id") or "").strip()
        },
    )

    # O banco ja foi confirmado neste ponto. Invalide por dia para que um
    # periodo parcialmente concluido (por falha em um dia posterior) nunca
    # continue servindo uma resposta anterior ao commit que deu certo.
    invalidate_vendas_cache(client_id)

    _sync_log(
        client_id,
        (
            f"[SYNC] Finalizado: {persistidos['vendas']} vendas e "
            f"{persistidos['notas']} notas gravadas atomicamente."
        ),
    )
    resultado = {
        "total": persistidos["vendas"],
        "notas_entrada_total": persistidos["notas"],
        "nf_enriquecidas": nf_enriquecidas,
        "nf_pendentes_restantes": nf_pendentes,
        "vendas_processadas": persistidos["vendas"],
        "notas_processadas": persistidos["notas"],
    }
    progresso_final = _criar_progresso(
        "Finalizado", 1, 1, 100, "Sincronização finalizada com sucesso."
    )
    progresso_final["concluido"] = True
    progresso_final["result"] = resultado
    _set_progresso(client_id, progresso_final)
    return {"success": True, **resultado}


__all__ = [
    "_persistir_periodo_atomico",
    "_sincronizar_vendas_periodo_impl",
]
