"""Persistent SKU comments for a single tenant."""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


@contextmanager
def _abrir_banco(caminho: Path):
    with closing(sqlite3.connect(caminho, timeout=5)) as conexao, conexao:
        conexao.execute("""
            CREATE TABLE IF NOT EXISTS comentarios_sku (
                store_id TEXT NOT NULL,
                sku TEXT NOT NULL,
                comentario TEXT NOT NULL,
                PRIMARY KEY (store_id, sku)
            )
        """)
        yield conexao


def listar_comentarios(pasta_tenant: str, store_id: str) -> dict[str, str]:
    caminho = Path(pasta_tenant) / "comentarios_sku.db"
    if not caminho.is_file():
        return {}
    with _abrir_banco(caminho) as conexao:
        return dict(conexao.execute(
            "SELECT sku, comentario FROM comentarios_sku WHERE store_id = ?",
            (store_id,),
        ))


def salvar_comentario(pasta_tenant: str, store_id: str, sku: str, comentario: str) -> None:
    caminho = Path(pasta_tenant) / "comentarios_sku.db"
    if not comentario and not caminho.is_file():
        return
    with _abrir_banco(caminho) as conexao:
        if comentario:
            conexao.execute("""
                INSERT INTO comentarios_sku (store_id, sku, comentario) VALUES (?, ?, ?)
                ON CONFLICT (store_id, sku) DO UPDATE SET comentario = excluded.comentario
            """, (store_id, sku, comentario))
        else:
            conexao.execute(
                "DELETE FROM comentarios_sku WHERE store_id = ? AND sku = ?",
                (store_id, sku),
            )
