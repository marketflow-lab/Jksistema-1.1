"""Compare event-loop blocking and worker isolation over equivalent local HTTP.

Run: python -B scripts/diagnosticos/benchmark_server_responsiveness.py
Synthetic workload: two 1.5 second blocking calls, identical health/HTML probes.
This measures scheduling isolation, not production database or CPU optimization.
"""

from __future__ import annotations

import json
import math
import socket
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
import uvicorn
from fastapi import FastAPI

from backend.routers.frontend import FrontendRouterConfig, create_frontend_router
from backend.services.blocking_workers import run_heavy


@contextmanager
def server(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    instance = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, loop="asyncio", ws="none",
        lifespan="off", log_level="critical", access_log=False,
        timeout_graceful_shutdown=3,
    ))
    thread = threading.Thread(target=instance.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not instance.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        if not instance.started:
            raise RuntimeError("Uvicorn did not start")
        yield f"http://127.0.0.1:{port}"
    finally:
        instance.should_exit = True
        thread.join(5)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("Uvicorn did not stop")


def measure(mode, static_root):
    app = FastAPI()
    entered = threading.Event()

    def blocking_job():
        entered.set()
        time.sleep(1.5)
        return {"finished_at": time.perf_counter()}

    @app.get("/slow")
    async def slow():
        if mode == "prior_event_loop":
            return blocking_job()
        return await run_heavy(blocking_job)

    @app.get("/health")
    async def health():
        return {"ready": True}

    app.include_router(create_frontend_router(FrontendRouterConfig(base_dir=str(static_root))))
    paths = ("/health", "/dashboard.html", "/vendas.html", "/perguntas_pos_venda.html", "/importacoes_lista.html")
    latencies = []
    with server(app) as base, ThreadPoolExecutor(max_workers=2) as jobs:
        with httpx.Client(timeout=10, trust_env=False) as first_client, httpx.Client(timeout=10, trust_env=False) as second_client, httpx.Client(timeout=10, trust_env=False) as probe:
            for client in (first_client, second_client, probe):
                assert client.get(base + "/health").status_code == 200
            for path in paths:
                assert probe.get(base + path).status_code == 200
            cpu_start = time.process_time()
            started = time.perf_counter()
            first = jobs.submit(first_client.get, base + "/slow")
            assert entered.wait(2)
            second = jobs.submit(second_client.get, base + "/slow")
            time.sleep(.03)
            for _ in range(3):
                for path in paths:
                    before = time.perf_counter()
                    response = probe.get(base + path)
                    assert response.status_code == 200
                    latencies.append((time.perf_counter() - before) * 1000)
            results = (first.result(5), second.result(5))
            assert all(response.status_code == 200 for response in results)
            duration = max(response.json()["finished_at"] for response in results) - started
            cpu_duration = time.process_time() - cpu_start
    ordered = sorted(latencies)
    return {
        "mode": mode,
        "blocking_calls": 2,
        "seconds_per_call": 1.5,
        "probe_count": len(latencies),
        "jobs_duration_ms": round(duration * 1000, 2),
        "process_cpu_ms": round(cpu_duration * 1000, 2),
        "probe_max_ms": round(max(latencies), 2),
        "probe_p95_ms": round(ordered[math.ceil(len(ordered) * .95) - 1], 2),
        "probe_mean_ms": round(sum(latencies) / len(latencies), 2),
    }


def main():
    with tempfile.TemporaryDirectory(prefix="jk-responsiveness-benchmark-") as directory:
        root = Path(directory)
        static = root / "static"
        static.mkdir()
        for name in ("dashboard", "vendas", "perguntas_pos_venda", "importacoes_lista"):
            (static / (name + ".html")).write_text(f"<html>{name}</html>", encoding="utf-8")
        results = [measure(mode, root) for mode in ("prior_event_loop", "worker_limiter_2")]
    print(json.dumps({"workload": "synthetic_blocking_http", "results": results}, indent=2))


if __name__ == "__main__":
    main()
