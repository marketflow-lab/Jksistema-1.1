import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from backend.services import favoritos_endpoints, favoritos_jobs


def _request(username: str | None = None, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/favoritos/jobs/test/status",
            "headers": headers or [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        }
    )
    if username:
        request.state.username = username
    return request


def _job(job_id: str, client_id: str, username: str, status: str = "done") -> dict:
    now = favoritos_jobs._now_iso()
    return {
        "id": job_id,
        "client_id": client_id,
        "username": username,
        "owner_key": favoritos_jobs._owner_key(client_id, username),
        "status": status,
        "percentual": 100 if status == "done" else 10,
        "resultados": [],
        "logs": [],
        "started_at": now,
        "updated_at": now,
        "finished_at": now if status == "done" else "",
    }


class FavoritosJobsSecurityTests(unittest.TestCase):
    def setUp(self):
        favoritos_jobs.FAVORITOS_JOB_ACTIVE.clear()
        favoritos_jobs.FAVORITOS_JOB_BY_OWNER.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.safe_dir_patch = patch.object(
            favoritos_jobs,
            "_safe_client_dir",
            side_effect=lambda client_id: self._client_dir(client_id),
        )
        self.safe_dir_patch.start()

    def tearDown(self):
        self.safe_dir_patch.stop()
        self.tmp.cleanup()
        favoritos_jobs.FAVORITOS_JOB_ACTIVE.clear()
        favoritos_jobs.FAVORITOS_JOB_BY_OWNER.clear()

    def _client_dir(self, client_id: str) -> Path:
        path = self.base / str(client_id) / "favoritos_jobs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _persist(self, job: dict) -> None:
        path = self._client_dir(job["client_id"]) / f"{job['id']}.json"
        path.write_text(json.dumps(job), encoding="utf-8")

    def test_request_username_uses_only_verified_state(self):
        spoofed = _request(headers=[(b"x-username", b"outro_usuario")])
        with self.assertRaises(HTTPException) as ctx:
            favoritos_endpoints._extrair_username_do_request(spoofed)
        self.assertEqual(ctx.exception.status_code, 401)

        trusted = _request("usuario_verificado", headers=[(b"x-username", b"outro_usuario")])
        self.assertEqual(
            favoritos_endpoints._extrair_username_do_request(trusted),
            "usuario_verificado",
        )

    def test_in_memory_job_isolated_by_client_and_username(self):
        job = _job("job-a", "cliente-a", "alice")
        favoritos_jobs.FAVORITOS_JOB_ACTIVE[job["id"]] = job

        self.assertEqual(
            favoritos_jobs._job_get(job["id"], "cliente-a", "alice")["id"],
            "job-a",
        )
        for client_id, username in (("cliente-b", "alice"), ("cliente-a", "bob")):
            with self.assertRaises(HTTPException) as ctx:
                favoritos_jobs._job_get(job["id"], client_id, username)
            self.assertEqual(ctx.exception.status_code, 404)

    def test_persisted_job_never_searches_another_client_directory(self):
        self._persist(_job("job-a", "cliente-a", "alice"))
        self.assertIsNone(favoritos_jobs._job_load("job-a", "cliente-b", "alice"))
        self.assertIsNone(favoritos_jobs._job_load("job-a", "cliente-a", "bob"))
        self.assertEqual(
            favoritos_jobs._job_load("job-a", "cliente-a", "alice")["id"],
            "job-a",
        )

    def test_unknown_id_does_not_fall_back_to_latest(self):
        self._persist(_job("job-latest", "cliente-a", "alice"))
        self.assertIsNone(
            favoritos_jobs._job_resolve_active("id-inexistente", "cliente-a", "alice")
        )
        with self.assertRaises(HTTPException) as ctx:
            favoritos_jobs.favoritos_jobs_status(
                "id-inexistente",
                _request("alice"),
                client_id="cliente-a",
            )
        self.assertEqual(ctx.exception.status_code, 404)
        latest = favoritos_jobs.favoritos_jobs_latest(
            _request("alice"),
            client_id="cliente-a",
        )
        self.assertEqual(latest["job_id"], "job-latest")

    def test_pause_resume_cancel_and_reload_after_restart(self):
        job = _job("job-control", "cliente-a", "alice", status="running")
        job["percentual"] = 10
        job["finished_at"] = ""
        job["cancel_requested"] = False
        favoritos_jobs.FAVORITOS_JOB_ACTIVE[job["id"]] = job
        favoritos_jobs.FAVORITOS_JOB_BY_OWNER[job["owner_key"]] = job["id"]
        favoritos_jobs._job_persist(job)

        paused = favoritos_jobs.favoritos_jobs_pause(
            job["id"],
            _request("alice"),
            client_id="cliente-a",
        )
        self.assertEqual(paused["status"], "paused")
        self.assertTrue(paused["paused"])

        resumed = favoritos_jobs.favoritos_jobs_resume(
            job["id"],
            _request("alice"),
            client_id="cliente-a",
        )
        self.assertEqual(resumed["status"], "running")

        canceled = favoritos_jobs.favoritos_jobs_cancel(
            job["id"],
            _request("alice"),
            client_id="cliente-a",
        )
        self.assertEqual(canceled["status"], "canceling")
        self.assertTrue(canceled["cancel_requested"])

        favoritos_jobs.FAVORITOS_JOB_ACTIVE.clear()
        favoritos_jobs.FAVORITOS_JOB_BY_OWNER.clear()
        reloaded = favoritos_jobs.favoritos_jobs_status(
            job["id"],
            _request("alice"),
            client_id="cliente-a",
        )
        self.assertEqual(reloaded["status"], "canceled")
        self.assertTrue(reloaded["cancel_requested"])
        self.assertTrue(reloaded["finished_at"])

    def test_visual_job_cancel_is_terminal_and_rejects_late_collection(self):
        job = _job("job-visual", "cliente-a", "alice", status="running")
        job.update(
            {
                "modo_coleta": favoritos_jobs.FAVORITOS_JOB_MODE_AVANTPRO_BROWSER,
                "cancel_requested": False,
                "fila_coletas": [
                    {"key": "sku-1|1", "sku": "SKU-1", "loja": "Loja", "campo": 1, "termo": "produto"}
                ],
                "coletas": {},
                "finished_at": "",
            }
        )
        favoritos_jobs.FAVORITOS_JOB_ACTIVE[job["id"]] = job
        favoritos_jobs.FAVORITOS_JOB_BY_OWNER[job["owner_key"]] = job["id"]
        favoritos_jobs._job_persist(job)

        canceled = favoritos_jobs.favoritos_jobs_cancel(
            job["id"],
            _request("alice"),
            client_id="cliente-a",
        )
        self.assertEqual(canceled["status"], "canceled")
        self.assertTrue(canceled["cancel_requested"])
        self.assertTrue(canceled["finished_at"])

        favoritos_jobs.FAVORITOS_JOB_ACTIVE.clear()
        favoritos_jobs.FAVORITOS_JOB_BY_OWNER.clear()
        pending = favoritos_jobs.favoritos_jobs_proxima_coleta(
            job["id"],
            _request("alice"),
            client_id="cliente-a",
        )
        self.assertFalse(pending["pending"])
        self.assertEqual(pending["status"], "canceled")

        with self.assertRaises(HTTPException) as ctx:
            favoritos_jobs.favoritos_jobs_coleta_termo(
                job["id"],
                {
                    "sku": "SKU-1",
                    "loja": "Loja",
                    "campo": 1,
                    "termo": "produto",
                    "anuncios": [{"id": "MLB1"}],
                },
                _request("alice"),
                client_id="cliente-a",
            )
        self.assertEqual(ctx.exception.status_code, 409)
        reloaded = favoritos_jobs._job_get(job["id"], "cliente-a", "alice")
        self.assertEqual(reloaded["status"], "canceled")
        self.assertEqual(reloaded.get("coletas"), {})

    def test_visual_cancel_cannot_be_overwritten_by_concurrent_routes(self):
        for suffix, operation in (("next", "next"), ("complete", "complete")):
            job = _job(f"job-race-{suffix}", "cliente-a", "alice", status="running")
            queue = [
                {"key": "sku-1|1", "sku": "SKU-1", "loja": "Loja", "campo": 1, "termo": "produto"},
                {"key": "sku-1|2", "sku": "SKU-1", "loja": "Loja", "campo": 2, "termo": "produto 2"},
            ]
            job.update(
                {
                    "modo_coleta": favoritos_jobs.FAVORITOS_JOB_MODE_AVANTPRO_BROWSER,
                    "cancel_requested": False,
                    "fila_coletas": queue,
                    "coletas": {},
                    "finished_at": "",
                }
            )
            favoritos_jobs.FAVORITOS_JOB_ACTIVE[job["id"]] = job
            favoritos_jobs.FAVORITOS_JOB_BY_OWNER[job["owner_key"]] = job["id"]
            favoritos_jobs._job_persist(job)
            barrier = threading.Barrier(2)

            def cancel():
                barrier.wait(timeout=2)
                return favoritos_jobs.favoritos_jobs_cancel(
                    job["id"], _request("alice"), client_id="cliente-a"
                )

            def competing_route():
                barrier.wait(timeout=2)
                if operation == "next":
                    return favoritos_jobs.favoritos_jobs_proxima_coleta(
                        job["id"], _request("alice"), client_id="cliente-a"
                    )
                try:
                    return favoritos_jobs.favoritos_jobs_coleta_termo(
                        job["id"],
                        {
                            "sku": "SKU-1",
                            "loja": "Loja",
                            "campo": 1,
                            "termo": "produto",
                            "anuncios": [{"id": "MLB1"}],
                        },
                        _request("alice"),
                        client_id="cliente-a",
                    )
                except HTTPException as exc:
                    self.assertEqual(exc.status_code, 409)
                    return None

            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(cancel)
                second = pool.submit(competing_route)
                first.result(timeout=3)
                second.result(timeout=3)

            final = favoritos_jobs._job_get(job["id"], "cliente-a", "alice")
            self.assertEqual(final["status"], "canceled")
            self.assertTrue(final["cancel_requested"])


class FavoritosDescricaoCacheTests(unittest.TestCase):
    def setUp(self):
        favoritos_endpoints.FAVORITOS_DESCRICAO_CACHE.clear()
        favoritos_endpoints.FAVORITOS_DESCRICAO_INFLIGHT.clear()
        favoritos_endpoints.FAVORITOS_DESCRICAO_SEMAPHORES.clear()

    def tearDown(self):
        favoritos_endpoints.FAVORITOS_DESCRICAO_CACHE.clear()
        favoritos_endpoints.FAVORITOS_DESCRICAO_INFLIGHT.clear()
        favoritos_endpoints.FAVORITOS_DESCRICAO_SEMAPHORES.clear()

    def test_description_cache_and_force_refresh(self):
        calls = 0

        def fetch(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return {"descricao": "Descricao real", "erro": ""}

        with patch.object(
            favoritos_endpoints,
            "_ml_favoritos_obter_descricao_item",
            side_effect=fetch,
            create=True,
        ):
            first = favoritos_endpoints._favoritos_obter_descricao_item_controlada(
                "cliente",
                "Loja",
                {},
                "MLB1",
            )
            second = favoritos_endpoints._favoritos_obter_descricao_item_controlada(
                "cliente",
                "Loja",
                {},
                "MLB1",
            )
            forced = favoritos_endpoints._favoritos_obter_descricao_item_controlada(
                "cliente",
                "Loja",
                {},
                "MLB1",
                force_refresh=True,
            )

        self.assertEqual(calls, 2)
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertFalse(forced["cache_hit"])

    def test_inflight_requests_are_deduplicated(self):
        calls = 0
        started = threading.Event()

        def fetch(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            started.set()
            time.sleep(0.12)
            return {"descricao": "Compartilhada", "erro": ""}

        with patch.object(
            favoritos_endpoints,
            "_ml_favoritos_obter_descricao_item",
            side_effect=fetch,
            create=True,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(
                    favoritos_endpoints._favoritos_obter_descricao_item_controlada,
                    "cliente",
                    "Loja",
                    {},
                    "MLB2",
                )
                self.assertTrue(started.wait(timeout=1))
                second = executor.submit(
                    favoritos_endpoints._favoritos_obter_descricao_item_controlada,
                    "cliente",
                    "Loja",
                    {},
                    "MLB2",
                )
                results = [first.result(timeout=2), second.result(timeout=2)]

        self.assertEqual(calls, 1)
        self.assertEqual([item["descricao"] for item in results], ["Compartilhada", "Compartilhada"])
        self.assertEqual(sum(bool(item.get("cache_hit")) for item in results), 1)


if __name__ == "__main__":
    unittest.main()
