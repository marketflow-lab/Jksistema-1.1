import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services import favoritos_storage
from backend.services import shared_sync_collect_files
from backend.services import shared_sync_delta
from backend.services import shared_sync_merge_sqlite
from backend.services import shared_sync_merge_user_data


def _slug(username):
    return re.sub(r"[^a-z0-9_-]+", "_", str(username or "default").strip().lower()) or "default"


def _entry(entry_id, data_iso="2026-07-08T10:00:00", sku="001"):
    return {
        "id": entry_id,
        "tipo": "ranking",
        "data_iso": data_iso,
        "loja": "JK Pecas",
        "usuario": "caio",
        "nome_usuario": "caio",
        "username": "caio",
        "total_skus": 1,
        "total_anuncios": 1,
        "grupos": [
            {
                "sku": sku,
                "titulo": "Produto teste",
                "total_anuncios": 1,
                "anuncios": [
                    {
                        "id": f"MLB{entry_id}",
                        "url": f"https://produto.mercadolivre.com.br/{entry_id}",
                        "titulo": "Anuncio teste",
                    }
                ],
            }
        ],
    }


def _db_count(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM favoritos_historico_entries").fetchone()[0]
    finally:
        conn.close()


class FavoritosHistoricoSqliteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tenant = Path(self.tmp.name) / "000002"
        self.tenant.mkdir()
        self.patches = [
            patch.object(favoritos_storage, "get_tenant_path", lambda client_id: str(self.tenant), create=True),
            patch.object(favoritos_storage, "_favoritos_usuario_slug", _slug, create=True),
            patch.object(shared_sync_collect_files, "get_tenant_path", lambda client_id: str(self.tenant), create=True),
            patch.object(shared_sync_collect_files, "_favoritos_usuario_slug", _slug, create=True),
            patch.object(shared_sync_merge_user_data, "_favoritos_usuario_slug", _slug, create=True),
            patch.object(
                shared_sync_merge_user_data,
                "_shared_sync_user_scoped_rels",
                shared_sync_collect_files._shared_sync_user_scoped_rels,
                create=True,
            ),
            patch.object(
                shared_sync_delta,
                "_shared_sync_historico_key",
                shared_sync_merge_user_data._shared_sync_historico_key,
                create=True,
            ),
        ]
        for name in (
            "_shared_sync_sqlite_lock_for_path",
            "_shared_sync_sqlite_configure",
            "_shared_sync_sqlite_retry_locked",
        ):
            self.patches.append(
                patch.object(shared_sync_collect_files, name, getattr(shared_sync_merge_sqlite, name), create=True)
            )
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_importa_json_legado_e_salva_so_no_sqlite(self):
        json_path = self.tenant / "favoritos_historico_caio.json"
        db_path = self.tenant / "favoritos_historico_caio.db"
        json_path.write_text(
            json.dumps(
                {
                    "updated_at": "2026-07-08T10:00:00",
                    "historico": [
                        _entry("hist-1", "2026-07-08T10:00:00"),
                        _entry("hist-2", "2026-07-08T09:00:00", "002"),
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        mtime_json = json_path.stat().st_mtime_ns

        payload = favoritos_storage._favoritos_carregar_historico("000002", "caio")

        self.assertTrue(db_path.exists())
        self.assertEqual(json_path.stat().st_mtime_ns, mtime_json)
        self.assertEqual([item["id"] for item in payload["historico"]], ["hist-1", "hist-2"])
        self.assertEqual(_db_count(db_path), 2)

        payload_2 = favoritos_storage._favoritos_carregar_historico("000002", "caio")

        self.assertEqual([item["id"] for item in payload_2["historico"]], ["hist-1", "hist-2"])
        self.assertEqual(_db_count(db_path), 2)

        salvo = favoritos_storage._favoritos_salvar_historico("000002", "caio", [_entry("hist-3")])

        self.assertEqual([item["id"] for item in salvo["historico"]], ["hist-3"])
        self.assertEqual(json_path.stat().st_mtime_ns, mtime_json)
        self.assertEqual(_db_count(db_path), 1)

    def test_shared_sync_usa_db_e_aceita_json_legado(self):
        favoritos_storage._favoritos_salvar_historico("000002", "caio", [_entry("hist-1")])

        entries, warnings = shared_sync_collect_files._shared_sync_coletar_arquivos(
            "000002",
            "favoritos_historico",
            username="caio",
            user_only=True,
        )

        self.assertEqual(warnings, [])
        self.assertEqual([item["relative_path"] for item in entries], ["favoritos_historico_caio.db"])
        self.assertEqual(entries[0]["data"][:16], b"SQLite format 3\x00")

        delta_bytes, keys = shared_sync_delta._shared_sync_favoritos_delta_bytes(
            "favoritos_historico",
            entries[0]["relative_path"],
            entries[0]["data"],
            set(),
        )

        self.assertEqual(keys, ["favoritos_historico:hist-1"])
        self.assertEqual(delta_bytes[:16], b"SQLite format 3\x00")

        merged = shared_sync_merge_user_data._shared_sync_merge_historico_usuario(
            "000002",
            "ana",
            [(entries[0]["relative_path"], delta_bytes)],
        )

        self.assertEqual([item["id"] for item in merged["historico"]], ["hist-1"])

        legado = json.dumps({"historico": [_entry("hist-2", "2026-07-08T11:00:00")]}, ensure_ascii=False).encode("utf-8")
        merged_legacy = shared_sync_merge_user_data._shared_sync_merge_historico_usuario(
            "000002",
            "ana",
            [("favoritos_historico_caio.json", legado)],
        )

        self.assertEqual([item["id"] for item in merged_legacy["historico"]], ["hist-2", "hist-1"])


if __name__ == "__main__":
    unittest.main()
