from __future__ import annotations

import inspect
import os
import sqlite3
from pathlib import Path

from backend.services import vendas_grafico, vendas_listagem, vendas_notas, vendas_sync_progress, vendas_sync_runner, vendas_unidades
from backend.services.vendas_performance import (
    append_indexable_date_filter,
    cache_vendas_response,
    invalidate_vendas_cache,
    prepare_vendas_database,
    vendas_cache_stats,
)


def _create_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE vendas (
                id_unico TEXT PRIMARY KEY,
                data TEXT,
                loja_conta TEXT,
                sku TEXT,
                quantidade REAL,
                valor REAL
            );
            CREATE TABLE notas_entrada_itens (
                id_unico TEXT PRIMARY KEY,
                data_emissao TEXT,
                sku TEXT,
                devolucao INTEGER DEFAULT 0
            );
            INSERT INTO vendas VALUES
                ('1', '2026-06-01T08:00:00', 'JK', 'A', 1, 10),
                ('2', '2026-06-30T23:59:59', 'JK', 'B', 2, 20),
                ('3', '2026-07-01T00:00:00', 'JK', 'C', 3, 30);
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_prepare_migrates_and_indexes_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "vendas_historico_jk.db"
    _create_legacy_db(db_path)

    assert prepare_vendas_database(str(db_path)) is True

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(vendas)")}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(vendas)")}
        assert {"devolucao", "numero_nf", "unidade_negocio", "intermediador_cnpj"} <= columns
        assert {"idx_vendas_data", "idx_vendas_sku_data", "idx_vendas_loja_data", "idx_vendas_unidade_data"} <= indexes

        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT sku FROM vendas WHERE data >= ? AND data < ?",
            ("2026-06-01", "2026-07-01"),
        ).fetchall()
        assert any("idx_vendas_data" in str(row) for row in plan)
    finally:
        conn.close()


def test_indexable_date_filter_preserves_inclusive_end_date(tmp_path: Path) -> None:
    db_path = tmp_path / "vendas_historico.db"
    _create_legacy_db(db_path)
    conditions: list[str] = []
    params: list[str] = []
    append_indexable_date_filter(conditions, params, "data", "2026-06-01", "2026-06-30")

    conn = sqlite3.connect(db_path)
    try:
        optimized = conn.execute(
            "SELECT id_unico FROM vendas WHERE " + " AND ".join(conditions) + " ORDER BY id_unico",
            params,
        ).fetchall()
        legacy = conn.execute(
            "SELECT id_unico FROM vendas WHERE date(data) BETWEEN ? AND ? ORDER BY id_unico",
            ("2026-06-01", "2026-06-30"),
        ).fetchall()
        assert optimized == legacy == [("1",), ("2",)]
    finally:
        conn.close()


def test_response_cache_uses_file_signature_and_explicit_invalidation(tmp_path: Path) -> None:
    invalidate_vendas_cache()
    source = tmp_path / "source.db"
    source.write_bytes(b"one")
    calls = {"count": 0}

    @cache_vendas_response("test", lambda _arguments: [str(source)])
    def endpoint(client_id: str, filtro: str = "") -> dict:
        calls["count"] += 1
        return {"call": calls["count"], "filtro": filtro}

    assert endpoint("000002", "x")["call"] == 1
    assert endpoint("000002", "x")["call"] == 1
    assert calls["count"] == 1

    source.write_bytes(b"two-two")
    assert endpoint("000002", "x")["call"] == 2
    invalidate_vendas_cache("000002")
    assert endpoint("000002", "x")["call"] == 3
    assert vendas_cache_stats()["hits"] >= 1


def test_blocking_vendas_endpoints_are_synchronous_for_fastapi_threadpool() -> None:
    endpoints = (
        vendas_listagem.listar_vendas,
        vendas_listagem.resumo_vendas,
        vendas_listagem.limites_vendas,
        vendas_grafico.grafico_vendas,
        vendas_grafico.skus_sem_venda,
        vendas_notas.listar_notas_entrada,
        vendas_notas.listar_itens_devolucoes,
        vendas_unidades.listar_unidades_negocios,
        vendas_sync_progress.progresso_sincronizacao_vendas,
        vendas_sync_runner.sincronizar_vendas,
    )
    assert all(not inspect.iscoroutinefunction(endpoint) for endpoint in endpoints)


def test_get_paths_do_not_run_migrations_or_create_indexes() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "backend/services/vendas_listagem.py",
        "backend/services/vendas_notas.py",
        "backend/services/vendas_grafico.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "ALTER TABLE" not in source
        assert "CREATE INDEX" not in source
