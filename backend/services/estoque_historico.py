"""Estoque historical snapshots and chart helpers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests
from fastapi import Depends, HTTPException

from backend.schemas.estoque import (
    EstoqueLancamentosSyncLoteRequest,
    EstoqueLancamentosSyncRequest,
    EstoquePreferenciasColunasRequest,
    EstoqueSyncRequest,
)
from backend.services import estoque_context
from backend.services.runtime_bridge import bind_runtime_globals


def _sync_context_names() -> None:
    for name in estoque_context.CONTEXT_EXPORTS:
        globals()[name] = getattr(estoque_context, name)


def configure_estoque_historico_runtime(runtime_module=None):
    runtime = estoque_context.configure_estoque_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    _sync_context_names()
    return runtime


configure_estoque_historico_runtime()

def _estoque_historico_db_path(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "estoque_historico.db")


def _lojas_estoque_configuradas(
    client_id: str,
    *,
    tenant_path: str | None = None,
) -> list[dict] | None:
    """Le o cadastro atual de lojas sem acionar migracoes ou gravacoes."""
    base_path = str(tenant_path or get_tenant_path(client_id))
    caminho = os.path.join(base_path, "lojas_config.json")
    try:
        with open(caminho, "r", encoding="utf-8-sig") as arquivo:
            payload = json.load(arquivo)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, list):
        return None
    return [item for item in payload if isinstance(item, dict)]


def _estoque_historico_ambiguidade(
    client_id: str,
    loja: str | None,
    *,
    tenant_path: str | None = None,
) -> dict | None:
    lojas = _lojas_estoque_configuradas(client_id, tenant_path=tenant_path)
    if not lojas:
        return {
            "code": "estoque_historico_identidade_indisponivel",
            "message": (
                "O histórico de estoque foi omitido porque a identidade das lojas "
                "não pôde ser comprovada."
            ),
            "store_ids": [],
        }
    grupos: dict[str, list[dict]] = {}
    for item in lojas:
        nome = str(item.get("nome") or "").strip()
        if nome:
            grupos.setdefault(nome.casefold(), []).append(item)
    ambiguos = {chave: itens for chave, itens in grupos.items() if len(itens) > 1}
    loja_txt = str(loja or "").strip()
    if loja_txt and loja_txt != "__todas":
        chave_loja = loja_txt.casefold()
        if chave_loja not in grupos:
            return {
                "code": "estoque_historico_identidade_indisponivel",
                "message": (
                    "O histórico de estoque foi omitido porque a loja não pôde ser "
                    "associada ao cadastro atual."
                ),
                "store_ids": [],
            }
        ambiguos = {chave_loja: ambiguos.get(chave_loja, [])}
        ambiguos = {chave: itens for chave, itens in ambiguos.items() if itens}
    if not ambiguos:
        return None
    store_ids = sorted({
        str(item.get("store_id") or "").strip()
        for itens in ambiguos.values()
        for item in itens
        if str(item.get("store_id") or "").strip()
    })
    return {
        "code": "estoque_historico_loja_ambigua",
        "message": (
            "O histórico de estoque foi omitido porque ainda é identificado pelo "
            "nome e existem lojas homônimas."
        ),
        "store_ids": store_ids,
    }


def _lojas_estoque_ativas(client_id: str) -> list[str]:
    """Lista nomes únicos de lojas ativas para compatibilidade histórica."""

    nomes: list[str] = []
    vistos: set[str] = set()
    for item in (_lojas_estoque_configuradas(client_id) or []):
        nome = str(item.get("nome") or "").strip()
        chave = nome.casefold()
        if not nome or chave in vistos:
            continue
        vistos.add(chave)
        nomes.append(nome)
    return nomes

def _garantir_tabela_historico_estoque(
    client_id: str,
    *,
    recuperar_pendentes: bool = True,
) -> None:
    db_path = _estoque_historico_db_path(client_id)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS estoque_historico (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_ref TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                loja_sync TEXT NOT NULL,
                sku TEXT NOT NULL,
                id_bling TEXT,
                nome_bling TEXT,
                situacao_bling TEXT,
                ncm_bling TEXT,
                saldo_loja REAL NOT NULL DEFAULT 0,
                saldo_full REAL NOT NULL DEFAULT 0,
                saldo_total REAL NOT NULL DEFAULT 0,
                UNIQUE (data_ref, loja_sync, sku)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_estoque_hist_data_loja ON estoque_historico (data_ref, loja_sync)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_estoque_hist_sku ON estoque_historico (sku)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS estoque_sync_eventos (
                event_id TEXT PRIMARY KEY,
                recorded_at TEXT NOT NULL,
                data_ref TEXT NOT NULL,
                loja_sync TEXT NOT NULL,
                origem TEXT NOT NULL DEFAULT 'estoque_sync',
                total_skus INTEGER NOT NULL DEFAULT 0,
                saldo_loja_total REAL NOT NULL DEFAULT 0,
                saldo_full_total REAL NOT NULL DEFAULT 0,
                saldo_total REAL NOT NULL DEFAULT 0,
                payload_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'committed'
                    CHECK(status IN ('pending', 'committed')),
                csv_hash TEXT
            )
            """
        )
        colunas_eventos = {
            str(row[1]) for row in cur.execute("PRAGMA table_info(estoque_sync_eventos)").fetchall()
        }
        if "status" not in colunas_eventos:
            cur.execute(
                "ALTER TABLE estoque_sync_eventos ADD COLUMN status TEXT NOT NULL "
                "DEFAULT 'committed' CHECK(status IN ('pending', 'committed'))"
            )
        if "csv_hash" not in colunas_eventos:
            cur.execute("ALTER TABLE estoque_sync_eventos ADD COLUMN csv_hash TEXT")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS estoque_sync_evento_itens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                data_ref TEXT NOT NULL,
                loja_sync TEXT NOT NULL,
                sku TEXT NOT NULL,
                id_bling TEXT,
                nome_bling TEXT,
                situacao_bling TEXT,
                ncm_bling TEXT,
                saldo_loja REAL NOT NULL DEFAULT 0,
                saldo_full REAL NOT NULL DEFAULT 0,
                saldo_total REAL NOT NULL DEFAULT 0,
                FOREIGN KEY(event_id) REFERENCES estoque_sync_eventos(event_id) ON DELETE RESTRICT,
                UNIQUE(event_id, sku)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_estoque_eventos_loja_data "
            "ON estoque_sync_eventos (loja_sync, data_ref, recorded_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_estoque_eventos_loja_nocase_data "
            "ON estoque_sync_eventos (loja_sync COLLATE NOCASE, data_ref, recorded_at, event_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_estoque_eventos_data_loja "
            "ON estoque_sync_eventos (data_ref, loja_sync COLLATE NOCASE, recorded_at, event_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_estoque_eventos_status_loja_data "
            "ON estoque_sync_eventos (status, loja_sync COLLATE NOCASE, data_ref, recorded_at, event_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_estoque_eventos_status_data_loja "
            "ON estoque_sync_eventos (status, data_ref, loja_sync COLLATE NOCASE, recorded_at, event_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_estoque_evento_itens_loja_sku_data "
            "ON estoque_sync_evento_itens (loja_sync, sku, data_ref, recorded_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_estoque_evento_itens_sku_evento "
            "ON estoque_sync_evento_itens (sku, event_id)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS estoque_historico_migracoes (
                migration_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        migration_id = "legacy_daily_snapshots_to_sync_events_v1"
        migracao_aplicada = cur.execute(
            "SELECT 1 FROM estoque_historico_migracoes WHERE migration_id = ?",
            (migration_id,),
        ).fetchone()
        if not migracao_aplicada:
            grupos_legados = cur.execute(
                """
                SELECT data_ref, loja_sync, MAX(recorded_at) AS recorded_at
                FROM estoque_historico
                WHERE trim(coalesce(loja_sync, '')) != ''
                  AND trim(coalesce(sku, '')) != ''
                GROUP BY data_ref, loja_sync
                ORDER BY data_ref, loja_sync
                """
            ).fetchall()
            for data_ref, loja_sync, recorded_at in grupos_legados:
                linhas = cur.execute(
                    """
                    SELECT sku, id_bling, nome_bling, situacao_bling, ncm_bling,
                           saldo_loja, saldo_full, saldo_total
                    FROM estoque_historico
                    WHERE data_ref = ? AND loja_sync = ?
                    ORDER BY id
                    """,
                    (data_ref, loja_sync),
                ).fetchall()
                itens_por_sku: dict[str, dict] = {}
                for linha in linhas:
                    sku_norm = str(linha[0] or "").strip().upper()
                    if not sku_norm:
                        continue
                    saldo_loja = float(linha[5] or 0)
                    saldo_full = float(linha[6] or 0)
                    itens_por_sku[sku_norm] = {
                        "sku": sku_norm,
                        "id_bling": str(linha[1] or "").strip(),
                        "nome_bling": str(linha[2] or "").strip(),
                        "situacao_bling": str(linha[3] or "").strip(),
                        "ncm_bling": str(linha[4] or "").strip(),
                        "saldo_loja": saldo_loja,
                        "saldo_full": saldo_full,
                        "saldo_total": saldo_loja + saldo_full,
                    }
                itens = list(itens_por_sku.values())
                if not itens:
                    continue
                identidade = f"{str(loja_sync).strip().casefold()}\0{data_ref}"
                event_id = "legacy-" + hashlib.sha256(identidade.encode("utf-8")).hexdigest()[:32]
                payload = {
                    "loja_sync": str(loja_sync).strip().casefold(),
                    "itens": sorted(itens, key=lambda item: item["sku"]),
                }
                payload_hash = hashlib.sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                recorded_at_txt = str(recorded_at or f"{data_ref}T00:00:00")
                saldo_loja_total = sum(item["saldo_loja"] for item in itens)
                saldo_full_total = sum(item["saldo_full"] for item in itens)
                cur.execute(
                    """
                    INSERT OR IGNORE INTO estoque_sync_eventos (
                        event_id, recorded_at, data_ref, loja_sync, origem,
                        total_skus, saldo_loja_total, saldo_full_total, saldo_total, payload_hash,
                        status
                    ) VALUES (?, ?, ?, ?, 'legacy_daily_snapshot', ?, ?, ?, ?, ?, 'committed')
                    """,
                    (
                        event_id,
                        recorded_at_txt,
                        str(data_ref),
                        str(loja_sync).strip(),
                        len(itens),
                        saldo_loja_total,
                        saldo_full_total,
                        saldo_loja_total + saldo_full_total,
                        payload_hash,
                    ),
                )
                cur.executemany(
                    """
                    INSERT OR IGNORE INTO estoque_sync_evento_itens (
                        event_id, recorded_at, data_ref, loja_sync, sku,
                        id_bling, nome_bling, situacao_bling, ncm_bling,
                        saldo_loja, saldo_full, saldo_total
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            event_id,
                            recorded_at_txt,
                            str(data_ref),
                            str(loja_sync).strip(),
                            item["sku"],
                            item["id_bling"],
                            item["nome_bling"],
                            item["situacao_bling"],
                            item["ncm_bling"],
                            item["saldo_loja"],
                            item["saldo_full"],
                            item["saldo_total"],
                        )
                        for item in itens
                    ],
                )
            cur.execute(
                "INSERT INTO estoque_historico_migracoes (migration_id, applied_at) VALUES (?, ?)",
                (migration_id, datetime.now().astimezone().isoformat(timespec="microseconds")),
            )
        conn.commit()
    finally:
        conn.close()
    if recuperar_pendentes:
        _recuperar_eventos_pendentes_publicados(client_id)


def _normalizar_registros_evento_estoque(registros: list[dict]) -> list[dict]:
    por_sku: dict[str, dict] = {}
    for registro in (registros or []):
        r = registro or {}
        sku = _normalizar_sku_estoque(r.get("sku"))
        if not sku:
            continue
        saldo_loja = float(r.get("saldo_loja") or 0)
        saldo_full = float(r.get("saldo_full") or 0)
        por_sku[sku] = {
            "sku": sku,
            "id_bling": str(r.get("id_bling") or "").strip(),
            "nome_bling": str(r.get("nome_bling") or "").strip(),
            "situacao_bling": str(r.get("situacao_bling") or "").strip(),
            "ncm_bling": str(r.get("ncm_bling") or "").strip(),
            "saldo_loja": saldo_loja,
            "saldo_full": saldo_full,
            "saldo_total": saldo_loja + saldo_full,
        }
    return list(por_sku.values())


def _hash_evento_estoque(loja_sync: str, registros: list[dict]) -> str:
    payload = {
        "loja_sync": str(loja_sync or "").strip().casefold(),
        "itens": sorted(registros, key=lambda item: item["sku"]),
    }
    bruto = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


def _novo_event_id_estoque() -> str:
    return uuid.uuid4().hex


def _atualizar_projecao_legada_evento(cur: sqlite3.Cursor, event_id: str) -> int:
    evento = cur.execute(
        "SELECT data_ref, recorded_at, loja_sync FROM estoque_sync_eventos WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not evento:
        raise ValueError("evento de estoque nao encontrado para confirmar")
    data_ref, recorded_at, loja_sync = evento
    itens = cur.execute(
        """
        SELECT sku, id_bling, nome_bling, situacao_bling, ncm_bling,
               saldo_loja, saldo_full, saldo_total
        FROM estoque_sync_evento_itens
        WHERE event_id = ?
        ORDER BY id
        """,
        (event_id,),
    ).fetchall()
    for item in itens:
        cur.execute(
            """
            INSERT INTO estoque_historico (
                data_ref, recorded_at, loja_sync, sku,
                id_bling, nome_bling, situacao_bling, ncm_bling,
                saldo_loja, saldo_full, saldo_total
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(data_ref, loja_sync, sku) DO UPDATE SET
                recorded_at=excluded.recorded_at,
                id_bling=excluded.id_bling,
                nome_bling=excluded.nome_bling,
                situacao_bling=excluded.situacao_bling,
                ncm_bling=excluded.ncm_bling,
                saldo_loja=excluded.saldo_loja,
                saldo_full=excluded.saldo_full,
                saldo_total=excluded.saldo_total
            """,
            (
                data_ref,
                recorded_at,
                loja_sync,
                item[0],
                item[1],
                item[2],
                item[3],
                item[4],
                item[5],
                item[6],
                item[7],
            ),
        )
    return len(itens)


def _registrar_snapshot_historico_estoque(
    client_id: str,
    loja_sync: str,
    registros: list[dict],
    *,
    event_id: str | None = None,
    recorded_at: str | None = None,
    status: str = "committed",
    csv_hash: str | None = None,
) -> int:
    _garantir_tabela_historico_estoque(client_id)
    db_path = _estoque_historico_db_path(client_id)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        agora = datetime.fromisoformat(recorded_at) if recorded_at else datetime.now().astimezone()
        hoje = agora.strftime("%Y-%m-%d")
        agora_iso = agora.isoformat(timespec="microseconds")
        loja_txt = str(loja_sync or "").strip()
        if not loja_txt:
            raise ValueError("loja_sync obrigatoria para registrar historico de estoque")
        itens = _normalizar_registros_evento_estoque(registros)
        evento_id = str(event_id or _novo_event_id_estoque()).strip()
        if not evento_id:
            raise ValueError("event_id obrigatorio para registrar historico de estoque")
        status_txt = str(status or "committed").strip().lower()
        if status_txt not in {"pending", "committed"}:
            raise ValueError("status de evento de estoque invalido")
        csv_hash_txt = str(csv_hash or "").strip() or None
        payload_hash = _hash_evento_estoque(loja_txt, itens)
        saldo_loja_total = sum(float(item["saldo_loja"]) for item in itens)
        saldo_full_total = sum(float(item["saldo_full"]) for item in itens)
        saldo_total = saldo_loja_total + saldo_full_total
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        existente = cur.execute(
            "SELECT payload_hash, total_skus, status, csv_hash "
            "FROM estoque_sync_eventos WHERE event_id = ?",
            (evento_id,),
        ).fetchone()
        if existente:
            if str(existente[0] or "") != payload_hash:
                raise ValueError("event_id ja registrado com payload de estoque diferente")
            if csv_hash_txt and existente[3] and str(existente[3]) != csv_hash_txt:
                raise ValueError("event_id ja registrado com CSV de estoque diferente")
            if status_txt == "committed" and str(existente[2] or "") == "pending":
                total_confirmado = _atualizar_projecao_legada_evento(cur, evento_id)
                cur.execute(
                    "UPDATE estoque_sync_eventos SET status = 'committed', "
                    "csv_hash = coalesce(csv_hash, ?) WHERE event_id = ? AND status = 'pending'",
                    (csv_hash_txt, evento_id),
                )
                conn.commit()
                return total_confirmado
            conn.rollback()
            return int(existente[1] or 0)

        cur.execute(
            """
            INSERT INTO estoque_sync_eventos (
                event_id, recorded_at, data_ref, loja_sync, origem,
                total_skus, saldo_loja_total, saldo_full_total, saldo_total, payload_hash,
                status, csv_hash
            ) VALUES (?, ?, ?, ?, 'estoque_sync', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evento_id,
                agora_iso,
                hoje,
                loja_txt,
                len(itens),
                saldo_loja_total,
                saldo_full_total,
                saldo_total,
                payload_hash,
                status_txt,
                csv_hash_txt,
            ),
        )

        for item in itens:
            cur.execute(
                """
                INSERT INTO estoque_sync_evento_itens (
                    event_id, recorded_at, data_ref, loja_sync, sku,
                    id_bling, nome_bling, situacao_bling, ncm_bling,
                    saldo_loja, saldo_full, saldo_total
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evento_id,
                    agora_iso,
                    hoje,
                    loja_txt,
                    item["sku"],
                    item["id_bling"],
                    item["nome_bling"],
                    item["situacao_bling"],
                    item["ncm_bling"],
                    item["saldo_loja"],
                    item["saldo_full"],
                    item["saldo_total"],
                ),
            )

        if status_txt == "committed":
            _atualizar_projecao_legada_evento(cur, evento_id)

        conn.commit()
        return len(itens)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _confirmar_evento_historico_estoque(
    client_id: str,
    event_id: str,
    *,
    csv_hash: str | None = None,
) -> int:
    _garantir_tabela_historico_estoque(client_id, recuperar_pendentes=False)
    db_path = _estoque_historico_db_path(client_id)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        evento = cur.execute(
            "SELECT status, total_skus, csv_hash FROM estoque_sync_eventos WHERE event_id = ?",
            (str(event_id or "").strip(),),
        ).fetchone()
        if not evento:
            raise ValueError("evento de estoque pendente nao encontrado")
        csv_hash_txt = str(csv_hash or "").strip() or None
        if csv_hash_txt and evento[2] and str(evento[2]) != csv_hash_txt:
            raise ValueError("CSV publicado diverge do evento de estoque pendente")
        if str(evento[0] or "") == "committed":
            conn.rollback()
            return int(evento[1] or 0)
        total = _atualizar_projecao_legada_evento(cur, str(event_id).strip())
        cur.execute(
            "UPDATE estoque_sync_eventos SET status = 'committed', "
            "csv_hash = coalesce(csv_hash, ?) WHERE event_id = ? AND status = 'pending'",
            (csv_hash_txt, str(event_id).strip()),
        )
        conn.commit()
        return total
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _descartar_evento_pendente_estoque(client_id: str, event_id: str) -> bool:
    _garantir_tabela_historico_estoque(client_id, recuperar_pendentes=False)
    db_path = _estoque_historico_db_path(client_id)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        evento = cur.execute(
            "SELECT status FROM estoque_sync_eventos WHERE event_id = ?",
            (str(event_id or "").strip(),),
        ).fetchone()
        if not evento or str(evento[0] or "") != "pending":
            conn.rollback()
            return False
        cur.execute("DELETE FROM estoque_sync_evento_itens WHERE event_id = ?", (str(event_id).strip(),))
        cur.execute(
            "DELETE FROM estoque_sync_eventos WHERE event_id = ? AND status = 'pending'",
            (str(event_id).strip(),),
        )
        removido = bool(cur.rowcount)
        conn.commit()
        return removido
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _recuperar_eventos_pendentes_publicados(client_id: str) -> int:
    """Commit pending events only when their staged CSV is already the current CSV."""
    db_path = _estoque_historico_db_path(client_id)
    if not os.path.exists(db_path):
        return 0
    conn = sqlite3.connect(db_path)
    try:
        tabela = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'estoque_sync_eventos'"
        ).fetchone()
        if not tabela:
            return 0
        colunas = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(estoque_sync_eventos)").fetchall()
        }
        if not {"status", "csv_hash"}.issubset(colunas):
            return 0
        pendentes = conn.execute(
            "SELECT COUNT(*) FROM estoque_sync_eventos WHERE status = 'pending'"
        ).fetchone()
        if not pendentes or int(pendentes[0] or 0) == 0:
            return 0
    finally:
        conn.close()

    arquivo_cliente = os.path.join(os.path.dirname(db_path), "produtos_compilado.csv")
    if not os.path.exists(arquivo_cliente):
        return 0
    digest = hashlib.sha256()
    try:
        with open(arquivo_cliente, "rb") as arquivo_csv:
            for bloco in iter(lambda: arquivo_csv.read(1024 * 1024), b""):
                digest.update(bloco)
    except OSError:
        return 0
    csv_hash = digest.hexdigest()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        eventos = cur.execute(
            """
            SELECT event_id
            FROM estoque_sync_eventos
            WHERE status = 'pending' AND csv_hash = ?
            ORDER BY recorded_at, event_id
            """,
            (csv_hash,),
        ).fetchall()
        recuperados = 0
        for (event_id,) in eventos:
            arquivo_temporario = os.path.join(
                os.path.dirname(db_path),
                f".produtos_compilado.{event_id}.tmp",
            )
            if os.path.exists(arquivo_temporario):
                continue
            _atualizar_projecao_legada_evento(cur, str(event_id))
            cur.execute(
                "UPDATE estoque_sync_eventos SET status = 'committed' "
                "WHERE event_id = ? AND status = 'pending' AND csv_hash = ?",
                (str(event_id), csv_hash),
            )
            recuperados += int(cur.rowcount or 0)
        conn.commit()
        return recuperados
    except Exception:
        conn.rollback()
        return 0
    finally:
        conn.close()

def _normalizar_sku_estoque(v: Any) -> str:
    return str(v or "").strip().upper()

def _label_mes_estoque(chave: str) -> str:
    ano, mes = chave.split("-")
    meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    return f"{meses[int(mes)-1]}/{ano}"

def _chave_intervalo_estoque(data_ref: datetime, intervalo: str) -> tuple[str, str]:
    if intervalo == "dia":
        chave = data_ref.strftime("%Y-%m-%d")
        return chave, chave
    if intervalo == "semana":
        inicio_semana = data_ref - timedelta(days=data_ref.weekday())
        chave = inicio_semana.strftime("%Y-%m-%d")
        return chave, inicio_semana.strftime("%d/%m/%Y")
    if intervalo == "mes":
        chave = data_ref.strftime("%Y-%m")
        return chave, _label_mes_estoque(chave)
    raise HTTPException(status_code=400, detail="intervalo invalido. Use dia, semana ou mes.")

def _inicio_periodo_estoque(
    periodo: str,
    data_inicio: str | None,
    data_fim_ref: datetime,
    client_id: str,
    loja: str,
) -> datetime:
    if data_inicio:
        try:
            return datetime.fromisoformat(data_inicio)
        except ValueError:
            raise HTTPException(status_code=400, detail="data_inicio invalida. Use YYYY-MM-DD.")

    periodo_norm = str(periodo or "3m").strip().lower()
    if periodo_norm == "3m":
        return data_fim_ref - timedelta(days=90)
    if periodo_norm == "6m":
        return data_fim_ref - timedelta(days=180)
    if periodo_norm == "1a":
        return data_fim_ref - timedelta(days=365)
    if periodo_norm == "2a":
        return data_fim_ref - timedelta(days=730)
    if periodo_norm == "max":
        db_hist = _estoque_historico_db_path(client_id)
        if not os.path.exists(db_hist):
            return data_fim_ref - timedelta(days=90)
        conn = sqlite3.connect(db_hist)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT MIN(date(data_ref))
                FROM estoque_historico
                WHERE lower(trim(loja_sync)) = lower(trim(?))
                """,
                (str(loja or "").strip(),),
            )
            row = cur.fetchone()
            if row and row[0]:
                return datetime.fromisoformat(str(row[0]))
            return data_fim_ref - timedelta(days=90)
        finally:
            conn.close()

    return data_fim_ref - timedelta(days=90)


def _tabela_sqlite_existe(conn: sqlite3.Connection, nome: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (nome,),
    ).fetchone()
    return bool(row)


def _estoque_serie_eventos(
    client_id: str,
    loja: str,
    intervalo: str,
    data_inicio_ref: datetime,
    data_fim_ref: datetime,
    sku: str | None = None,
) -> dict | None:
    """Return an event-backed stock series, or None when legacy fallback is required."""
    db_path = _estoque_historico_db_path(client_id)
    if not os.path.exists(db_path):
        return None
    _recuperar_eventos_pendentes_publicados(client_id)

    loja_txt = str(loja or "").strip()
    sku_norm = _normalizar_sku_estoque(sku) if sku else ""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not (
            _tabela_sqlite_existe(conn, "estoque_sync_eventos")
            and _tabela_sqlite_existe(conn, "estoque_sync_evento_itens")
        ):
            return None

        params: list[Any] = [
            loja_txt,
            data_inicio_ref.strftime("%Y-%m-%d"),
            data_fim_ref.strftime("%Y-%m-%d"),
        ]
        if sku_norm:
            query = """
                SELECT e.event_id, e.recorded_at, e.data_ref, e.total_skus,
                       i.saldo_loja AS saldo_loja
                FROM estoque_sync_eventos e
                LEFT JOIN estoque_sync_evento_itens i
                  ON i.event_id = e.event_id
                 AND i.sku = ?
                WHERE e.status = 'committed'
                  AND e.loja_sync = ? COLLATE NOCASE
                  AND e.data_ref >= ?
                  AND e.data_ref <= ?
                ORDER BY e.recorded_at, e.event_id
            """
            params.insert(0, sku_norm)
        else:
            query = """
                SELECT event_id, recorded_at, data_ref, total_skus,
                       saldo_loja_total AS saldo_loja
                FROM estoque_sync_eventos
                WHERE status = 'committed'
                  AND loja_sync = ? COLLATE NOCASE
                  AND data_ref >= ?
                  AND data_ref <= ?
                ORDER BY recorded_at, event_id
            """
        eventos = list(conn.execute(query, params).fetchall())
        if not eventos:
            return None

        selecionados: dict[str, sqlite3.Row] = {}
        labels_atualizacao: dict[str, str] = {}
        for evento in eventos:
            if intervalo == "atualizacao":
                chave = str(evento["event_id"])
                label = str(evento["recorded_at"])
            else:
                data_evento = datetime.fromisoformat(str(evento["data_ref"]))
                chave, label = _chave_intervalo_estoque(data_evento, intervalo)
            selecionados[chave] = evento
            labels_atualizacao[chave] = label

        movimentos: dict[str, dict[str, float]] = {}
        if intervalo != "atualizacao" and _tabela_sqlite_existe(conn, "estoque_lancamentos"):
            query_mov = """
                SELECT data_ref,
                       SUM(coalesce(entrada, 0)) AS entradas,
                       SUM(coalesce(saida, 0)) AS saidas
                FROM estoque_lancamentos
                WHERE loja_sync = ? COLLATE NOCASE
                  AND data_ref >= ?
                  AND data_ref <= ?
            """
            params_mov: list[Any] = [
                loja_txt,
                data_inicio_ref.strftime("%Y-%m-%d"),
                data_fim_ref.strftime("%Y-%m-%d"),
            ]
            if sku_norm:
                query_mov += " AND sku = ?"
                params_mov.append(sku_norm)
            query_mov += " GROUP BY data_ref"
            for row in conn.execute(query_mov, params_mov).fetchall():
                data_mov = datetime.fromisoformat(str(row["data_ref"]))
                chave, _label = _chave_intervalo_estoque(data_mov, intervalo)
                bucket = movimentos.setdefault(chave, {"entradas": 0.0, "saidas": 0.0})
                bucket["entradas"] += float(row["entradas"] or 0)
                bucket["saidas"] += float(row["saidas"] or 0)

        chaves = list(selecionados.keys()) if intervalo == "atualizacao" else sorted(selecionados.keys())
        ultimo = selecionados[chaves[-1]]
        por_sku = []
        for chave in chaves:
            evento = selecionados[chave]
            if sku_norm:
                por_sku.append({
                    "data": str(evento["recorded_at"] if intervalo == "atualizacao" else evento["data_ref"]),
                    "saldo": float(evento["saldo_loja"]) if evento["saldo_loja"] is not None else None,
                    "entradas": None if intervalo == "atualizacao" else float(movimentos.get(chave, {}).get("entradas", 0)),
                    "saidas": None if intervalo == "atualizacao" else float(movimentos.get(chave, {}).get("saidas", 0)),
                    "event_id": str(evento["event_id"]),
                })

        return {
            "success": True,
            "loja": loja_txt,
            "sku": sku_norm or None,
            "intervalo": intervalo,
            "fonte": "lancamentos",
            "historico_fonte": "eventos",
            "data_inicio": data_inicio_ref.strftime("%Y-%m-%d"),
            "data_fim": str(ultimo["data_ref"]),
            "base_snapshot_data": str(ultimo["data_ref"]),
            "total_skus": (
                1 if sku_norm and ultimo["saldo_loja"] is not None
                else 0 if sku_norm
                else int(ultimo["total_skus"] or 0)
            ),
            "labels": [labels_atualizacao[chave] for chave in chaves],
            "saldo_retroativo": [
                float(selecionados[chave]["saldo_loja"])
                if selecionados[chave]["saldo_loja"] is not None
                else None
                for chave in chaves
            ],
            "entradas": [
                None if intervalo == "atualizacao" else float(movimentos.get(chave, {}).get("entradas", 0))
                for chave in chaves
            ],
            "saidas": [
                None if intervalo == "atualizacao" else float(movimentos.get(chave, {}).get("saidas", 0))
                for chave in chaves
            ],
            "series_por_sku": por_sku,
            "event_ids": [str(selecionados[chave]["event_id"]) for chave in chaves],
        }
    finally:
        conn.close()


def _vendas_series_estoque_eventos(
    client_id: str,
    loja: str | None,
    intervalo: str,
    data_inicio_ref: datetime,
    data_fim_ref: datetime,
    chaves_periodo: list[str],
    incluir_geral: bool,
    incluir_sku: bool,
    incluir_skus_com_estoque: bool,
    sku_norm: str,
    pareto_skus_norm: set[str],
) -> dict | None:
    db_path = _estoque_historico_db_path(client_id)
    if not os.path.exists(db_path):
        return None
    _recuperar_eventos_pendentes_publicados(client_id)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not (
            _tabela_sqlite_existe(conn, "estoque_sync_eventos")
            and _tabela_sqlite_existe(conn, "estoque_sync_evento_itens")
        ):
            return None

        loja_txt = str(loja or "").strip()
        loja_especifica = bool(loja_txt and loja_txt != "__todas")
        lojas_ativas = [] if loja_especifica else _lojas_estoque_ativas(client_id)
        lojas_ativas_por_ref = {nome.casefold(): nome for nome in lojas_ativas}
        query = """
            SELECT event_id, recorded_at, data_ref, loja_sync, saldo_loja_total
            FROM estoque_sync_eventos
            WHERE status = 'committed'
              AND data_ref <= ?
        """
        params: list[Any] = [
            data_fim_ref.strftime("%Y-%m-%d"),
        ]
        if loja_especifica:
            query += " AND loja_sync = ? COLLATE NOCASE"
            params.append(loja_txt)
        query += " ORDER BY recorded_at, event_id"
        eventos = list(conn.execute(query, params).fetchall())
        if not eventos:
            return None

        saldo_sku_por_evento: dict[str, float] = {}
        if incluir_sku:
            query_sku = """
                SELECT i.event_id, i.saldo_loja
                FROM estoque_sync_evento_itens i
                JOIN estoque_sync_eventos e ON e.event_id = i.event_id
                WHERE e.status = 'committed'
                  AND i.sku = ?
                  AND e.data_ref <= ?
            """
            params_sku: list[Any] = [
                sku_norm,
                data_fim_ref.strftime("%Y-%m-%d"),
            ]
            if loja_especifica:
                query_sku += " AND e.loja_sync = ? COLLATE NOCASE"
                params_sku.append(loja_txt)
            saldo_sku_por_evento = {
                str(row["event_id"]): float(row["saldo_loja"])
                for row in conn.execute(query_sku, params_sku).fetchall()
            }

        eventos_por_loja: dict[str, list[tuple[str, sqlite3.Row]]] = {}
        lojas_historicas_ignoradas: set[str] = set()
        for evento in eventos:
            loja_ref = str(evento["loja_sync"] or "").strip().casefold()
            if lojas_ativas_por_ref and loja_ref not in lojas_ativas_por_ref:
                lojas_historicas_ignoradas.add(loja_ref)
                continue
            data_evento = datetime.fromisoformat(str(evento["data_ref"]))
            chave, _label = _chave_intervalo_estoque(data_evento, intervalo)
            eventos_por_loja.setdefault(loja_ref, []).append((chave, evento))
        for eventos_loja in eventos_por_loja.values():
            eventos_loja.sort(
                key=lambda item: (item[0], str(item[1]["recorded_at"]), str(item[1]["event_id"]))
            )

        bucket_geral: dict[str, float] = {}
        bucket_sku: dict[str, float] = {}
        eventos_contagem_por_chave: dict[str, list[str]] = {}
        lojas_cobertas_por_chave: dict[str, int] = {}
        for chave in (chaves_periodo or []):
            valores_geral: list[float] = []
            valores_sku: list[float] = []
            eventos_contagem: list[str] = []
            sku_completo = True
            for eventos_loja in eventos_por_loja.values():
                evento_atual = None
                for chave_evento, evento in eventos_loja:
                    if chave_evento <= chave:
                        evento_atual = evento
                    else:
                        break
                if evento_atual is None:
                    sku_completo = False
                    continue
                valores_geral.append(float(evento_atual["saldo_loja_total"]))
                event_id = str(evento_atual["event_id"])
                eventos_contagem.append(event_id)
                if event_id in saldo_sku_por_evento:
                    valores_sku.append(saldo_sku_por_evento[event_id])
                else:
                    sku_completo = False
            lojas_cobertas_por_chave[chave] = len(eventos_contagem)
            if incluir_geral and valores_geral:
                bucket_geral[chave] = sum(valores_geral)
            if incluir_sku and sku_completo and valores_sku:
                bucket_sku[chave] = sum(valores_sku)
            if incluir_skus_com_estoque and eventos_contagem:
                eventos_contagem_por_chave[chave] = eventos_contagem

        bucket_skus_com_estoque: dict[str, int] = {}
        bucket_skus_pareto_com_estoque: dict[str, int] = {}
        if incluir_skus_com_estoque and eventos_contagem_por_chave:
            event_ids = sorted({
                event_id
                for ids_chave in eventos_contagem_por_chave.values()
                for event_id in ids_chave
            })
            saldos_por_evento: dict[str, dict[str, float]] = {
                event_id: {} for event_id in event_ids
            }
            for inicio in range(0, len(event_ids), 900):
                lote = event_ids[inicio:inicio + 900]
                placeholders = ",".join("?" for _item in lote)
                query_itens = (
                    "SELECT event_id, sku, saldo_loja "
                    "FROM estoque_sync_evento_itens "
                    f"WHERE event_id IN ({placeholders}) "
                    "AND saldo_loja != 0"
                )
                for item in conn.execute(query_itens, lote).fetchall():
                    event_id = str(item["event_id"])
                    sku_item = _normalizar_sku_estoque(item["sku"])
                    if not sku_item:
                        continue
                    saldos_por_evento[event_id][sku_item] = (
                        float(saldos_por_evento[event_id].get(sku_item, 0) or 0)
                        + float(item["saldo_loja"] or 0)
                    )

            for chave, ids_chave in eventos_contagem_por_chave.items():
                saldos_agregados: dict[str, float] = {}
                for event_id in ids_chave:
                    for sku_item, saldo in saldos_por_evento.get(event_id, {}).items():
                        saldos_agregados[sku_item] = (
                            float(saldos_agregados.get(sku_item, 0) or 0)
                            + float(saldo or 0)
                        )
                bucket_skus_com_estoque[chave] = sum(
                    1 for saldo in saldos_agregados.values() if float(saldo or 0) > 0
                )
                if pareto_skus_norm:
                    bucket_skus_pareto_com_estoque[chave] = sum(
                        1
                        for sku_item, saldo in saldos_agregados.items()
                        if sku_item in pareto_skus_norm and float(saldo or 0) > 0
                    )

        total_lojas = len(lojas_ativas_por_ref) if lojas_ativas_por_ref else len(eventos_por_loja)
        lojas_sem_historico = [
            nome
            for chave, nome in lojas_ativas_por_ref.items()
            if chave not in eventos_por_loja
        ]
        lojas_com_historico = len(eventos_por_loja)
        detail = ""
        if lojas_sem_historico:
            detail = (
                f"Cobertura parcial: {lojas_com_historico} de {total_lojas} lojas com "
                "historico de estoque. Sem historico: " + ", ".join(lojas_sem_historico) + "."
            )

        return {
            "estoque_geral": [
                round(bucket_geral[chave], 2) if chave in bucket_geral else None
                for chave in (chaves_periodo or [])
            ] if incluir_geral else [],
            "estoque_sku": [
                round(bucket_sku[chave], 2) if chave in bucket_sku else None
                for chave in (chaves_periodo or [])
            ] if incluir_sku else [],
            "estoque_skus_com_saldo": [
                int(bucket_skus_com_estoque[chave]) if chave in bucket_skus_com_estoque else None
                for chave in (chaves_periodo or [])
            ] if incluir_skus_com_estoque else [],
            "estoque_skus_pareto_com_saldo": [
                int(bucket_skus_pareto_com_estoque[chave])
                if chave in bucket_skus_pareto_com_estoque else None
                for chave in (chaves_periodo or [])
            ] if pareto_skus_norm else [],
            "lojas": total_lojas,
            "lojas_com_historico": lojas_com_historico,
            "lojas_sem_historico": lojas_sem_historico,
            "lojas_historicas_ignoradas": len(lojas_historicas_ignoradas),
            "lojas_cobertas_por_periodo": [
                int(lojas_cobertas_por_chave.get(chave, 0))
                for chave in (chaves_periodo or [])
            ],
            "detail": detail,
        }
    finally:
        conn.close()

def _vendas_series_estoque_historico(
    client_id: str,
    loja: str | None,
    intervalo: str,
    data_inicio_ref: datetime,
    data_fim_ref: datetime,
    chaves_periodo: list[str],
    mostrar_estoque_geral: bool,
    mostrar_estoque_sku: bool,
    sku: str | None,
    pareto_skus: list[str] | None = None,
) -> dict:
    total_pontos = len(chaves_periodo or [])
    incluir_geral = bool(mostrar_estoque_geral)
    sku_norm = _normalizar_sku_estoque(sku) if sku else ""
    incluir_sku = bool(mostrar_estoque_sku and sku_norm)
    incluir_skus_com_estoque = bool(incluir_geral or incluir_sku)
    pareto_skus_norm = {
        sku_item
        for sku_item in (_normalizar_sku_estoque(item) for item in (pareto_skus or []))
        if sku_item
    }
    resultado = {
        "estoque_geral": [None] * total_pontos if incluir_geral else [],
        "estoque_sku": [None] * total_pontos if incluir_sku else [],
        "estoque_skus_com_saldo": (
            [None] * total_pontos if incluir_skus_com_estoque else []
        ),
        "estoque_skus_pareto_com_saldo": (
            [None] * total_pontos if pareto_skus_norm else []
        ),
        "estoque_meta": {
            "success": True,
            "requested": bool(mostrar_estoque_geral or mostrar_estoque_sku),
            "lojas": 0,
            "sku": sku_norm or None,
            "detail": "",
        },
    }

    if not (incluir_geral or incluir_sku):
        if mostrar_estoque_sku and not sku_norm:
            resultado["estoque_meta"]["detail"] = "Informe um SKU para exibir estoque por SKU."
        return resultado

    ambiguidade = _estoque_historico_ambiguidade(client_id, loja)
    if ambiguidade:
        resultado["estoque_meta"].update({
            "success": False,
            "code": ambiguidade["code"],
            "detail": ambiguidade["message"],
            "store_ids": ambiguidade["store_ids"],
        })
        return resultado

    db_hist = _estoque_historico_db_path(client_id)
    if not os.path.exists(db_hist):
        resultado["estoque_meta"]["detail"] = "Histórico de estoque ainda não foi gerado."
        return resultado

    try:
        serie_eventos = _vendas_series_estoque_eventos(
            client_id,
            loja,
            intervalo,
            data_inicio_ref,
            data_fim_ref,
            chaves_periodo,
            incluir_geral,
            incluir_sku,
            incluir_skus_com_estoque,
            sku_norm,
            pareto_skus_norm,
        )
    except sqlite3.OperationalError as exc:
        logger.warning(f"Falha ao consultar eventos de estoque; usando fallback legado: {exc}")
        serie_eventos = None
    if serie_eventos is not None:
        resultado["estoque_geral"] = serie_eventos["estoque_geral"]
        resultado["estoque_sku"] = serie_eventos["estoque_sku"]
        resultado["estoque_skus_com_saldo"] = serie_eventos["estoque_skus_com_saldo"]
        resultado["estoque_skus_pareto_com_saldo"] = serie_eventos["estoque_skus_pareto_com_saldo"]
        resultado["estoque_meta"].update({
            "lojas": int(serie_eventos["lojas"] or 0),
            "lojas_com_historico": int(serie_eventos.get("lojas_com_historico") or 0),
            "lojas_sem_historico": list(serie_eventos.get("lojas_sem_historico") or []),
            "lojas_historicas_ignoradas": int(serie_eventos.get("lojas_historicas_ignoradas") or 0),
            "lojas_cobertas_por_periodo": list(serie_eventos.get("lojas_cobertas_por_periodo") or []),
            "detail": str(serie_eventos.get("detail") or ""),
            "fonte": "eventos",
        })
        return resultado

    loja_txt = str(loja or "").strip()
    loja_especifica = bool(loja_txt and loja_txt != "__todas")
    data_fim_str = data_fim_ref.strftime("%Y-%m-%d")

    conn_hist = sqlite3.connect(db_hist)
    try:
        cur_hist = conn_hist.cursor()
        if loja_especifica:
            cur_hist.execute(
                """
                SELECT lower(trim(loja_sync)) AS loja_ref, MAX(date(data_ref)) AS data_base
                FROM estoque_historico
                WHERE lower(trim(loja_sync)) = lower(trim(?))
                  AND date(data_ref) <= date(?)
                GROUP BY lower(trim(loja_sync))
                """,
                (loja_txt, data_fim_str),
            )
        else:
            cur_hist.execute(
                """
                SELECT lower(trim(loja_sync)) AS loja_ref, MAX(date(data_ref)) AS data_base
                FROM estoque_historico
                WHERE trim(coalesce(loja_sync, '')) != ''
                  AND date(data_ref) <= date(?)
                GROUP BY lower(trim(loja_sync))
                """,
                (data_fim_str,),
            )
        lojas_base = [
            (str(row[0] or "").strip(), str(row[1] or "").strip())
            for row in cur_hist.fetchall()
            if str(row[0] or "").strip() and str(row[1] or "").strip()
        ]
        if not loja_especifica:
            lojas_ativas_ref = {nome.casefold() for nome in _lojas_estoque_ativas(client_id)}
            if lojas_ativas_ref:
                lojas_base = [item for item in lojas_base if item[0].casefold() in lojas_ativas_ref]

        if not lojas_base:
            resultado["estoque_meta"]["detail"] = "Sem snapshot de estoque no período solicitado."
            return resultado

        _garantir_tabela_lancamentos_estoque(client_id)
        bucket_geral: dict[str, float] = {}
        bucket_sku: dict[str, float] = {}
        bucket_saldos_por_sku: dict[str, dict[str, float]] = {}
        inicio_periodo_date = data_inicio_ref.date()
        fim_periodo_date = data_fim_ref.date()

        def somar_snapshot(loja_ref: str, data_base: str, apenas_sku: str = "") -> float | None:
            query = """
                SELECT SUM(coalesce(saldo_loja, 0))
                FROM estoque_historico
                WHERE lower(trim(loja_sync)) = ?
                  AND date(data_ref) = date(?)
            """
            params = [loja_ref, data_base]
            if apenas_sku:
                query += " AND upper(trim(sku)) = ?"
                params.append(apenas_sku)
            cur_hist.execute(query, params)
            row = cur_hist.fetchone()
            if not row or row[0] is None:
                return None
            return float(row[0] or 0)

        def carregar_movimentos(
            loja_ref: str,
            data_inicio_calc: str,
            data_base: str,
            apenas_sku: str = "",
        ) -> tuple[dict[str, float], dict[str, float]]:
            entradas: dict[str, float] = {}
            saidas: dict[str, float] = {}
            query = """
                SELECT date(data_ref) AS data_ref,
                       SUM(coalesce(entrada, 0)) AS entradas,
                       SUM(coalesce(saida, 0)) AS saidas
                FROM estoque_lancamentos
                WHERE lower(trim(loja_sync)) = ?
                  AND date(data_ref) >= date(?)
                  AND date(data_ref) <= date(?)
            """
            params = [loja_ref, data_inicio_calc, data_base]
            if apenas_sku:
                query += " AND upper(trim(sku)) = ?"
                params.append(apenas_sku)
            query += " GROUP BY date(data_ref)"
            cur_hist.execute(query, params)
            for data_ref, entradas_raw, saidas_raw in cur_hist.fetchall():
                data_ref_str = str(data_ref or "").strip()
                if not data_ref_str:
                    continue
                entradas[data_ref_str] = float(entradas_raw or 0)
                saidas[data_ref_str] = float(saidas_raw or 0)
            return entradas, saidas

        def carregar_snapshot_por_sku(loja_ref: str, data_base: str) -> dict[str, float]:
            cur_hist.execute(
                """
                SELECT upper(trim(sku)) AS sku_ref, SUM(coalesce(saldo_loja, 0))
                FROM estoque_historico
                WHERE lower(trim(loja_sync)) = ?
                  AND date(data_ref) = date(?)
                  AND trim(coalesce(sku, '')) != ''
                GROUP BY upper(trim(sku))
                """,
                (loja_ref, data_base),
            )
            return {
                str(sku_ref or "").strip(): float(saldo or 0)
                for sku_ref, saldo in cur_hist.fetchall()
                if str(sku_ref or "").strip()
            }

        def carregar_movimentos_por_sku(
            loja_ref: str,
            data_inicio_calc: str,
            data_base: str,
        ) -> dict[str, dict[str, tuple[float, float]]]:
            cur_hist.execute(
                """
                SELECT date(data_ref) AS data_ref,
                       upper(trim(sku)) AS sku_ref,
                       SUM(coalesce(entrada, 0)) AS entradas,
                       SUM(coalesce(saida, 0)) AS saidas
                FROM estoque_lancamentos
                WHERE lower(trim(loja_sync)) = ?
                  AND date(data_ref) >= date(?)
                  AND date(data_ref) <= date(?)
                  AND trim(coalesce(sku, '')) != ''
                GROUP BY date(data_ref), upper(trim(sku))
                """,
                (loja_ref, data_inicio_calc, data_base),
            )
            movimentos: dict[str, dict[str, tuple[float, float]]] = {}
            for data_ref, sku_ref, entradas_raw, saidas_raw in cur_hist.fetchall():
                data_ref_str = str(data_ref or "").strip()
                sku_ref_str = str(sku_ref or "").strip()
                if not data_ref_str or not sku_ref_str:
                    continue
                movimentos.setdefault(data_ref_str, {})[sku_ref_str] = (
                    float(entradas_raw or 0),
                    float(saidas_raw or 0),
                )
            return movimentos

        def acumular_buckets(
            destino: dict[str, float],
            loja_ref: str,
            valor_base: float | None,
            data_base: str,
            movimentos_sku: str = "",
        ) -> None:
            if valor_base is None:
                return
            data_base_dt = datetime.fromisoformat(data_base)
            data_inicio_calc = min(data_inicio_ref, data_base_dt).strftime("%Y-%m-%d")
            entradas, saidas = carregar_movimentos(
                loja_ref,
                data_inicio_calc,
                data_base,
                movimentos_sku,
            )
            saldo_atual = float(valor_base or 0)
            data_cursor = data_base_dt.date()
            limite_calc = datetime.fromisoformat(data_inicio_calc).date()
            buckets_loja: dict[str, float] = {}
            while data_cursor >= limite_calc:
                data_cursor_str = data_cursor.strftime("%Y-%m-%d")
                if inicio_periodo_date <= data_cursor <= fim_periodo_date:
                    chave_bucket, _label_bucket = _chave_intervalo_estoque(
                        datetime.combine(data_cursor, datetime.min.time()),
                        intervalo,
                    )
                    if chave_bucket not in buckets_loja:
                        buckets_loja[chave_bucket] = saldo_atual

                entradas_dia = float(entradas.get(data_cursor_str, 0) or 0)
                saidas_dia = float(saidas.get(data_cursor_str, 0) or 0)
                saldo_atual = saldo_atual - entradas_dia + saidas_dia
                data_cursor -= timedelta(days=1)

            for chave_bucket, valor_bucket in buckets_loja.items():
                destino[chave_bucket] = float(destino.get(chave_bucket, 0) or 0) + float(valor_bucket or 0)

        def acumular_buckets_skus(loja_ref: str, data_base: str) -> None:
            data_base_dt = datetime.fromisoformat(data_base)
            data_inicio_calc = min(data_inicio_ref, data_base_dt).strftime("%Y-%m-%d")
            saldos_atual = carregar_snapshot_por_sku(loja_ref, data_base)
            movimentos = carregar_movimentos_por_sku(loja_ref, data_inicio_calc, data_base)
            for movimentos_dia in movimentos.values():
                for sku_item in movimentos_dia:
                    saldos_atual.setdefault(sku_item, 0.0)

            data_cursor = data_base_dt.date()
            limite_calc = datetime.fromisoformat(data_inicio_calc).date()
            buckets_loja: dict[str, dict[str, float]] = {}
            while data_cursor >= limite_calc:
                data_cursor_str = data_cursor.strftime("%Y-%m-%d")
                if inicio_periodo_date <= data_cursor <= fim_periodo_date:
                    chave_bucket, _label_bucket = _chave_intervalo_estoque(
                        datetime.combine(data_cursor, datetime.min.time()),
                        intervalo,
                    )
                    if chave_bucket not in buckets_loja:
                        buckets_loja[chave_bucket] = dict(saldos_atual)

                for sku_item, (entradas_dia, saidas_dia) in movimentos.get(data_cursor_str, {}).items():
                    saldos_atual[sku_item] = (
                        float(saldos_atual.get(sku_item, 0) or 0)
                        - float(entradas_dia or 0)
                        + float(saidas_dia or 0)
                    )
                data_cursor -= timedelta(days=1)

            for chave_bucket, saldos_bucket in buckets_loja.items():
                destino = bucket_saldos_por_sku.setdefault(chave_bucket, {})
                for sku_item, saldo in saldos_bucket.items():
                    destino[sku_item] = (
                        float(destino.get(sku_item, 0) or 0)
                        + float(saldo or 0)
                    )

        for loja_ref_atual, data_base in lojas_base:
            if incluir_geral:
                total_base = somar_snapshot(loja_ref_atual, data_base)
                acumular_buckets(bucket_geral, loja_ref_atual, total_base, data_base)
            if incluir_sku:
                total_sku_base = somar_snapshot(loja_ref_atual, data_base, sku_norm)
                acumular_buckets(bucket_sku, loja_ref_atual, total_sku_base, data_base, sku_norm)
            if incluir_skus_com_estoque:
                acumular_buckets_skus(loja_ref_atual, data_base)

        for idx, chave in enumerate(chaves_periodo or []):
            if incluir_geral and chave in bucket_geral:
                resultado["estoque_geral"][idx] = round(float(bucket_geral[chave] or 0), 2)
            if incluir_sku and chave in bucket_sku:
                resultado["estoque_sku"][idx] = round(float(bucket_sku[chave] or 0), 2)
            if incluir_skus_com_estoque and chave in bucket_saldos_por_sku:
                resultado["estoque_skus_com_saldo"][idx] = sum(
                    1
                    for saldo in bucket_saldos_por_sku[chave].values()
                    if float(saldo or 0) > 0
                )
                if pareto_skus_norm:
                    resultado["estoque_skus_pareto_com_saldo"][idx] = sum(
                        1
                        for sku_item, saldo in bucket_saldos_por_sku[chave].items()
                        if sku_item in pareto_skus_norm and float(saldo or 0) > 0
                    )

        resultado["estoque_meta"].update({
            "lojas": len(lojas_base),
            "detail": "",
        })
        return resultado
    except sqlite3.OperationalError as exc:
        logger.warning(f"Falha ao consultar histórico de estoque para gráfico de vendas: {exc}")
        resultado["estoque_meta"].update({
            "success": False,
            "detail": "Não foi possível consultar o histórico de estoque.",
        })
        return resultado
    finally:
        conn_hist.close()


__all__ = [
    "configure_estoque_historico_runtime",
    "_estoque_historico_db_path",
    "_garantir_tabela_historico_estoque",
    "_registrar_snapshot_historico_estoque",
    "_confirmar_evento_historico_estoque",
    "_descartar_evento_pendente_estoque",
    "_recuperar_eventos_pendentes_publicados",
    "_novo_event_id_estoque",
    "_normalizar_sku_estoque",
    "_label_mes_estoque",
    "_chave_intervalo_estoque",
    "_inicio_periodo_estoque",
    "_estoque_serie_eventos",
    "_vendas_series_estoque_historico",
    "_estoque_historico_ambiguidade",
]
