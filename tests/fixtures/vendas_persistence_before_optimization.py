"""Frozen behavioral oracle from commit 8ba5ebfa, before persistence optimization.

Keep the complete prior algorithms to compare resulting database rows, including
ambiguous legacy identities. This fixture is not application code.
"""
from __future__ import annotations
import sqlite3
from datetime import datetime
from backend.schemas import VendasSyncRequest
from backend.modules.vendas.sync_period import (
    _escopo_store, _legado_loja_nao_ambiguo, _montar_id_unico_venda,
    _montar_chave_deduplicacao_venda,
)


def _filtrar_vendas_ja_existentes(
    cursor: sqlite3.Cursor, payload: list[tuple], loja: str, store_id: str = ""
) -> list[tuple]:
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
        "SELECT id_unico, sku, quantidade, valor, numero_nf, numero, nota_fiscal_id, canal "
        "FROM vendas WHERE loja_conta=? AND coalesce(store_id,'')='' AND coalesce(origem_tipo,'')=''",
        (req.loja,),
    ).fetchall() if legacy_allowed else []
    remove_legacy: set[str] = set()
    for invoice_id in observed_invoices:
        cursor.execute(
            f"DELETE FROM vendas WHERE ({store_clause}) AND origem_tipo='nfe_saida' AND origem_id=?",
            (*store_args, invoice_id),
        )
    for legacy in legacy_rows:
        legacy_id, sku, quantity, amount, invoice_number, number, invoice_id, _channel = legacy
        if str(invoice_id or "") in observed_invoices and str(legacy_id) == _montar_id_unico_venda(
            req.loja, numero_nf=invoice_number, numero=number, sku=sku, quantidade=quantity, valor=amount,
        ):
            remove_legacy.add(str(legacy_id))
    for (kind, document_id), document_rows in documents.items():
        cursor.execute(
            f"DELETE FROM vendas WHERE ({store_clause}) AND origem_tipo=? AND origem_id=?",
            (*store_args, kind, document_id),
        )
        invoice_ids = {str(row[15]) for row in document_rows if str(row[15] or "")}
        covered_skus = {str(row[7] or "").strip().upper() for row in document_rows}
        if kind == "pedido":
            for invoice_id in invoice_ids:
                cursor.executemany(
                    f"DELETE FROM vendas WHERE ({store_clause}) AND origem_tipo='nfe_saida' AND origem_id=? AND upper(trim(coalesce(sku,'')))=?",
                    [(*store_args, invoice_id, sku) for sku in covered_skus],
                )
        for legacy in legacy_rows:
            legacy_id, sku, quantity, amount, invoice_number, number, invoice_id, channel = legacy
            legacy_invoice_id = _montar_id_unico_venda(
                req.loja, numero_nf=invoice_number, numero=number, sku=sku,
                quantidade=quantity, valor=amount,
            )
            if (kind == "pedido" and str(invoice_id or "") in invoice_ids
                    and str(sku or "").strip().upper() in covered_skus and str(legacy_id) == legacy_invoice_id):
                remove_legacy.add(str(legacy_id))
                continue
            expected = _montar_id_unico_venda(
                req.loja, id_pedido=document_id if kind == "pedido" else "",
                numero_nf=invoice_number, numero=number, sku=sku, quantidade=quantity, valor=amount,
            )
            if str(legacy_id) != expected:
                continue
            if kind == "nfe_saida" and str(invoice_id or "") != document_id:
                continue
            # Same old reference could mean an order ID or an invoice number.
            # Keep ambiguous rows rather than adopting another document.
            if kind == "pedido" and not any(
                str(row[4] or "") == str(number or "")
                or (str(invoice_id or "") and str(row[15] or "") == str(invoice_id))
                for row in document_rows
            ):
                continue
            remove_legacy.add(str(legacy_id))
    cursor.executemany("DELETE FROM vendas WHERE coalesce(store_id,'')='' AND loja_conta=? AND id_unico=?",
                       [(req.loja, row_id) for row_id in remove_legacy])
