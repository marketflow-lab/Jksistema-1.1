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


def _entry(entry_id, data_iso="2026-07-08T10:00:00", sku="001", duracao_execucao_ms=None):
    entry = {
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
    if duracao_execucao_ms is not None:
        entry["duracao_execucao_ms"] = duracao_execucao_ms
        entry["grupos"][0]["duracao_execucao_ms"] = duracao_execucao_ms
    return entry


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

    def test_normalizador_deduplica_mlb_canonico_e_mescla_metadados(self):
        entrada = _entry("hist-dedup")
        entrada["grupos"][0]["total_anuncios"] = 2
        entrada["grupos"][0]["anuncios"] = [
            {
                "id": "mlb-4402208097",
                "url": "https://produto.mercadolivre.com.br/MLB-4402208097#primeiro",
                "titulo": "",
                "vendedor": "LOJA PRIMEIRA",
                "vendas": 0,
                "parcelamento_sem_juros": False,
                "pesquisas_origem": ["pesquisa 1"],
                "campos_origem": ["Pesquisa 1"],
            },
            {
                "id": "MLB4402208097",
                "url": "https://www.mercadolivre.com.br/produto/up/MLBU1234567890",
                "titulo": "Titulo completo",
                "vendedor": "LOJA SEGUNDA",
                "vendas": 100,
                "preco": 36.99,
                "parcelamento_sem_juros": True,
                "pesquisas_origem": ["pesquisa 2"],
                "campos_origem": ["Pesquisa 2"],
            },
        ]

        normalizado = favoritos_storage._favoritos_normalizar_historico([entrada])[0]
        grupo = normalizado["grupos"][0]

        self.assertEqual(len(grupo["anuncios"]), 1)
        self.assertEqual(grupo["total_anuncios"], 1)
        self.assertEqual(normalizado["total_anuncios"], 1)
        anuncio = grupo["anuncios"][0]
        self.assertEqual(anuncio["vendedor"], "LOJA PRIMEIRA")
        self.assertEqual(anuncio["vendas"], 0)
        self.assertIs(anuncio["parcelamento_sem_juros"], False)
        self.assertEqual(anuncio["titulo"], "Titulo completo")
        self.assertEqual(anuncio["preco"], 36.99)
        self.assertEqual(anuncio["pesquisas_origem"], ["pesquisa 1", "pesquisa 2"])
        self.assertEqual(anuncio["campos_origem"], ["Pesquisa 1", "Pesquisa 2"])

    def test_normalizador_nao_colapsa_mlbs_distintos_com_mesma_url_catalogo(self):
        entrada = _entry("hist-catalogo")
        url_catalogo = "https://www.mercadolivre.com.br/produto/up/MLBU1234567890"
        entrada["grupos"][0]["anuncios"] = [
            {"id": "MLB11111111", "url": url_catalogo, "titulo": "Primeiro"},
            {"id": "MLB22222222", "url": url_catalogo, "titulo": "Segundo"},
            {"url": url_catalogo, "titulo": "Catalogo sem MLB", "pesquisas_origem": ["pesquisa 1"]},
            {"url": f"{url_catalogo}#duplicado", "preco": 25.0, "pesquisas_origem": ["pesquisa 2"]},
            {"titulo": "Sem chave 1"},
            {"titulo": "Sem chave 2"},
        ]

        grupo = favoritos_storage._favoritos_normalizar_historico([entrada])[0]["grupos"][0]

        self.assertEqual(len(grupo["anuncios"]), 5)
        self.assertEqual(grupo["total_anuncios"], 5)
        self.assertEqual([item["id"] for item in grupo["anuncios"][:2]], ["MLB11111111", "MLB22222222"])
        self.assertEqual(grupo["anuncios"][2]["titulo"], "Catalogo sem MLB")
        self.assertEqual(grupo["anuncios"][2]["preco"], 25.0)
        self.assertEqual(grupo["anuncios"][2]["pesquisas_origem"], ["pesquisa 1", "pesquisa 2"])
        self.assertEqual([item["titulo"] for item in grupo["anuncios"][3:]], ["Sem chave 1", "Sem chave 2"])

    def test_normalizador_deduplica_antes_do_limite_e_preserva_60_unicos(self):
        entrada = _entry("hist-limite-dedup")
        anuncios = [
            {"id": f"MLB{10000000 + indice}", "titulo": f"Anuncio {indice}"}
            for indice in range(59)
        ]
        anuncios.insert(10, {"id": "mlb-10000000", "titulo": "Duplicado antes do limite"})
        anuncios.append({"id": "MLB99999999", "titulo": "Unico depois da posicao 60"})
        entrada["grupos"][0]["anuncios"] = anuncios
        entrada["grupos"][0]["total_anuncios"] = len(anuncios)
        entrada["total_anuncios"] = len(anuncios)

        normalizado = favoritos_storage._favoritos_normalizar_historico([entrada])[0]
        grupo = normalizado["grupos"][0]

        self.assertEqual(len(grupo["anuncios"]), 60)
        self.assertEqual(len({favoritos_storage._favoritos_mlb_canonico_historico(item) for item in grupo["anuncios"]}), 60)
        self.assertEqual(grupo["anuncios"][-1]["id"], "MLB99999999")
        self.assertEqual(grupo["total_anuncios"], 60)
        self.assertEqual(normalizado["total_anuncios"], 60)

    def test_sqlite_recarrega_historico_com_mlb_unico(self):
        entrada = _entry("hist-reload-dedup")
        entrada["grupos"][0]["total_anuncios"] = 2
        entrada["grupos"][0]["anuncios"] = [
            {"id": "MLB-4402208097", "titulo": "", "pesquisas_origem": ["pesquisa 1"]},
            {"id": "mlb4402208097", "titulo": "Titulo recarregado", "pesquisas_origem": ["pesquisa 2"]},
        ]

        salvo = favoritos_storage._favoritos_salvar_historico("000002", "caio", [entrada])
        recarregado = favoritos_storage._favoritos_carregar_historico("000002", "caio")

        for payload in (salvo, recarregado):
            historico = payload["historico"][0]
            grupo = historico["grupos"][0]
            self.assertEqual(len(grupo["anuncios"]), 1)
            self.assertEqual(grupo["total_anuncios"], 1)
            self.assertEqual(historico["total_anuncios"], 1)
            self.assertEqual(grupo["anuncios"][0]["titulo"], "Titulo recarregado")
            self.assertEqual(grupo["anuncios"][0]["pesquisas_origem"], ["pesquisa 1", "pesquisa 2"])

    def test_shared_sync_usa_db_e_aceita_json_legado(self):
        favoritos_storage._favoritos_salvar_historico(
            "000002",
            "caio",
            [_entry("hist-1", duracao_execucao_ms=90500)],
        )

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
        self.assertEqual(merged["historico"][0]["duracao_execucao_ms"], 90500)

        legado = json.dumps({"historico": [_entry("hist-2", "2026-07-08T11:00:00")]}, ensure_ascii=False).encode("utf-8")
        merged_legacy = shared_sync_merge_user_data._shared_sync_merge_historico_usuario(
            "000002",
            "ana",
            [("favoritos_historico_caio.json", legado)],
        )

        self.assertEqual([item["id"] for item in merged_legacy["historico"]], ["hist-2", "hist-1"])

    def test_preserva_duracao_execucao_sem_alterar_historico_legado(self):
        payload = favoritos_storage._favoritos_salvar_historico(
            "000002",
            "caio",
            [
                _entry("hist-tempo", duracao_execucao_ms=90500),
                _entry("hist-legado", "2026-07-08T09:00:00", "002"),
            ],
        )

        com_tempo = next(item for item in payload["historico"] if item["id"] == "hist-tempo")
        legado = next(item for item in payload["historico"] if item["id"] == "hist-legado")
        self.assertEqual(com_tempo["duracao_execucao_ms"], 90500)
        self.assertEqual(com_tempo["grupos"][0]["duracao_execucao_ms"], 90500)
        self.assertNotIn("duracao_execucao_ms", legado)

        recarregado = favoritos_storage._favoritos_carregar_historico("000002", "caio")
        com_tempo_recarregado = next(item for item in recarregado["historico"] if item["id"] == "hist-tempo")
        legado_recarregado = next(item for item in recarregado["historico"] if item["id"] == "hist-legado")
        self.assertEqual(com_tempo_recarregado["duracao_execucao_ms"], 90500)
        self.assertNotIn("duracao_execucao_ms", legado_recarregado)

    def test_finaliza_duracao_no_commit_e_recarrega_entrada_e_grupo(self):
        inicio_ms = 1_720_000_000_000
        with patch.object(favoritos_storage.time, "time", return_value=(inicio_ms + 90_500) / 1000):
            payload = favoritos_storage._favoritos_salvar_historico(
                "000002",
                "caio",
                [_entry("hist-final")],
                finalizar_ids=["hist-final"],
                inicio_execucao_ms=inicio_ms,
            )

        self.assertEqual(payload["duracao_execucao_ms"], 90_500)
        self.assertEqual(payload["finalizados_ids"], ["hist-final"])
        self.assertEqual(payload["historico"][0]["duracao_execucao_ms"], 90_500)
        self.assertEqual(payload["historico"][0]["grupos"][0]["duracao_execucao_ms"], 90_500)

        recarregado = favoritos_storage._favoritos_carregar_historico("000002", "caio")
        self.assertEqual(recarregado["historico"][0]["duracao_execucao_ms"], 90_500)
        self.assertEqual(recarregado["historico"][0]["grupos"][0]["duracao_execucao_ms"], 90_500)

    def test_preserva_telemetria_da_coleta_no_mesmo_historico(self):
        entrada = _entry("hist-telemetria", duracao_execucao_ms=90_500)
        entrada["grupos"][0]["resumo_coleta"] = [{
            "pesquisa": 1,
            "termo": "sensor cb500",
            "campo": "titulo",
            "visiveis": 80,
            "coletados": 80,
            "com_titulo": 80,
            "com_foto": 80,
            "com_preco": 79,
            "com_link": 80,
            "com_dados_avant": 78,
            "incompletos": 2,
            "suspeitos": 1,
            "avant_nao_vinculado": 0,
            "tempo_esgotado": False,
            "login_avant_bloqueado": False,
            "motivo_encerramento": "stable_plateau",
            "passadas": 2,
            "posicoes_percorridas": 24,
            "cliques_avant": 4,
            "capturados_avant": 3,
            "tempo_materializacao_ms": 1_250,
            "tempo_avant_ms": 8_400,
            "tempo_finalizacao_ms": 350,
            "motivos_incompletos": {"preco": 1, "avant": 2},
            "origens_dados": {"dom": 80},
            "amostras_incompletos": [{
                "posicao": 7,
                "mlb": "MLB123",
                "titulo": "Anuncio incompleto",
                "faltas": ["avant"],
                "origem_dados": "dom",
            }],
            "campo_nao_permitido": "nao deve ser persistido",
        }]

        payload = favoritos_storage._favoritos_salvar_historico("000002", "caio", [entrada])
        resumo = payload["historico"][0]["grupos"][0]["resumo_coleta"][0]
        self.assertEqual(resumo["motivo_encerramento"], "stable_plateau")
        self.assertEqual(resumo["tempo_avant_ms"], 8_400)
        self.assertEqual(resumo["motivos_incompletos"], {"preco": 1, "avant": 2})
        self.assertEqual(resumo["amostras_incompletos"][0]["faltas"], ["avant"])
        self.assertNotIn("campo_nao_permitido", resumo)

        recarregado = favoritos_storage._favoritos_carregar_historico("000002", "caio")
        resumo_recarregado = recarregado["historico"][0]["grupos"][0]["resumo_coleta"][0]
        self.assertEqual(resumo_recarregado, resumo)
        self.assertEqual(recarregado["historico"][0]["duracao_execucao_ms"], 90_500)

    def test_finalizacao_atomica_recusa_id_ausente_sem_substituir_historico(self):
        favoritos_storage._favoritos_salvar_historico("000002", "caio", [_entry("hist-preservado")])
        inicio_ms = 1_720_000_000_000
        with patch.object(favoritos_storage.time, "time", return_value=(inicio_ms + 10_000) / 1000):
            with self.assertRaises(Exception) as contexto:
                favoritos_storage._favoritos_salvar_historico(
                    "000002",
                    "caio",
                    [_entry("hist-preservado")],
                    finalizar_ids=["hist-ausente"],
                    inicio_execucao_ms=inicio_ms,
                )
        self.assertEqual(getattr(contexto.exception, "status_code", None), 409)
        recarregado = favoritos_storage._favoritos_carregar_historico("000002", "caio")
        self.assertEqual([item["id"] for item in recarregado["historico"]], ["hist-preservado"])

    def test_merge_mesmo_id_nunca_apaga_duracao_execucao(self):
        rica = _entry("hist-merge", duracao_execucao_ms=90500)
        antiga = _entry("hist-merge")

        for base, nova in (([rica], [antiga]), ([antiga], [rica])):
            merged = favoritos_storage._favoritos_historico_merge_listas(base, nova)
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["duracao_execucao_ms"], 90500)
            self.assertEqual(merged[0]["grupos"][0]["duracao_execucao_ms"], 90500)

        mais_longa = _entry("hist-merge", duracao_execucao_ms=120000)
        merged_max = favoritos_storage._favoritos_historico_merge_listas([rica], [mais_longa])
        self.assertEqual(merged_max[0]["duracao_execucao_ms"], 120000)

    def test_shared_sync_copia_antiga_nao_apaga_duracao_execucao(self):
        rica = _entry("hist-sync", duracao_execucao_ms=90500)
        favoritos_storage._favoritos_salvar_historico("000002", "ana", [rica])
        pacote_antigo = json.dumps({"historico": [_entry("hist-sync")]}, ensure_ascii=False).encode("utf-8")

        merged = shared_sync_merge_user_data._shared_sync_merge_historico_usuario(
            "000002",
            "ana",
            [("favoritos_historico_caio.json", pacote_antigo)],
        )

        self.assertEqual(merged["historico"][0]["duracao_execucao_ms"], 90500)
        self.assertEqual(merged["historico"][0]["grupos"][0]["duracao_execucao_ms"], 90500)


if __name__ == "__main__":
    unittest.main()
