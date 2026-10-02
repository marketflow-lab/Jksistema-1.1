"""Compare previous and current approval selection using only synthetic data.

Run from the checkout: python -B scripts/benchmark_question_approvals.py
Repeat the full warm-cache comparison with --rounds 3 --full-warmup.
Only aggregate metrics are printed; the temporary database is removed on exit.
"""

from __future__ import annotations

import argparse
import ast
import json
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services import codex_assistant_storage
from backend.services import perguntas_pos_venda_codex as codex
from backend.services.codex.storage import customer_reply_state
from backend.services.whatsapp.approvals import question_workflow

BASELINE_REF = "43f9f590d758a170276db93726007d8111130b64"


def previous_function(ref: str, filename: str, name: str, namespace: dict):
    source = subprocess.check_output(
        ["git", "show", f"{ref}:{filename}"], cwd=ROOT, text=True, encoding="utf-8",
    )
    tree = ast.parse(source)
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, "<previous-approval-function>", "exec"), namespace)
    return namespace[name]


def seed(info_base: str, count: int) -> None:
    tenant = "synthetic-tenant"
    job = {
        "job_id": "job-0", "client_id": tenant, "profile": codex.PROFILE,
        "task_type": "public_question", "subject_key": "synthetic-question",
        "store": "Synthetic store", "status": "completed", "agent_state": "aguardando_aprovacao",
        "prompt_version": codex.PROMPT_VERSION, "prompt_hash": codex.PROMPT_HASH,
        "schema_version": codex.SCHEMA_VERSION, "queue_policy_version": codex.QUEUE_POLICY_VERSION,
        "result": {"resposta": "Synthetic answer", "requires_approval": True},
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, tenant, job)
    path = codex_assistant_storage.codex_assistant_state_db_path(info_base, tenant)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        original = dict(connection.execute("SELECT * FROM assistant_customer_reply_jobs LIMIT 1").fetchone())
        payload = json.loads(original["payload_json"])
        columns = list(original)
        sql = "INSERT INTO assistant_customer_reply_jobs (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")"
        for index in range(1, count):
            job_id = f"job-{index}"
            current = {**payload, "job_id": job_id}
            row = {**original, "job_id": job_id, "payload_json": json.dumps(current)}
            connection.execute(sql, [row[column] for column in columns])
            customer_reply_state._customer_reply_transient_put(path, current, {"result": dict(job["result"])})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical", type=int, default=2000)
    parser.add_argument("--pending", type=int, default=4)
    parser.add_argument("--rounds", "--repeats", dest="rounds", type=int, default=1)
    parser.add_argument("--full-warmup", action="store_true", help="Warm both cases with the full workload before measurement")
    parser.add_argument("--baseline-ref", default=BASELINE_REF)
    args = parser.parse_args()
    if args.historical < 1 or args.pending < 1 or args.rounds < 1:
        parser.error("All workload counts must be positive")
    old_approval = previous_function(
        args.baseline_ref, "backend/services/perguntas_pos_venda_codex.py", "approval_job_current", dict(vars(codex)),
    )
    old_namespace = dict(vars(question_workflow))
    old_namespace["perguntas_pos_venda_codex"] = SimpleNamespace(approval_job_current=old_approval)
    old_select = previous_function(
        args.baseline_ref, "backend/services/whatsapp/approvals/question_workflow.py", "_pending_question_approval", old_namespace,
    )
    ppv_state = SimpleNamespace(
        _perguntas_loja_config_obter=lambda configs, store: configs.get(store, {}),
        _perguntas_loja_config_normalizar=lambda config: config,
    )
    configs = {"Synthetic store": {"notificar_whatsapp_aprovacoes": True}}
    approvals = [
        {"id": f"approval-{index}", "codex_job_id": f"job-{index}", "loja": "Synthetic store",
         "status": "answered" if index < args.historical else "pending", "resposta_sugerida": "Synthetic answer"}
        for index in range(args.historical + args.pending)
    ]
    original_runtime = codex._RUNTIME
    original_sealed_enabled = customer_reply_state._customer_reply_sealed_results_enabled
    original_connect = sqlite3.connect
    connections = 0

    def counted_connect(*positional, **keyword):
        nonlocal connections
        connections += 1
        return original_connect(*positional, **keyword)

    try:
        customer_reply_state._customer_reply_sealed_results_enabled = lambda: False
        with tempfile.TemporaryDirectory(prefix="jk-approval-synthetic-") as temporary:
            codex._RUNTIME = SimpleNamespace(PASTA_INFO=temporary)
            seed(temporary, len(approvals))

            def previous():
                return old_select(ppv_state, configs, approvals, client_id="synthetic-tenant")

            def current():
                candidates = question_workflow._question_approval_candidates(ppv_state, configs, approvals)
                jobs = codex.approval_jobs_current("synthetic-tenant", [item["codex_job_id"] for item in candidates])
                selected = question_workflow._pending_question_approval(
                    ppv_state, configs, approvals, client_id="synthetic-tenant", current_jobs=jobs,
                )
                if selected and not codex.approval_job_current("synthetic-tenant", selected["codex_job_id"]):
                    return None
                return selected

            # Seeding warms the file cache; a short warmup also initializes both code paths.
            warmup_previous = previous if args.full_warmup else lambda: old_select(
                ppv_state, configs, approvals[args.historical:], client_id="synthetic-tenant",
            )
            for operation in (warmup_previous, current):
                assert operation()["id"] == approvals[args.historical]["id"]
            results = {"previous": [], "current": []}
            sqlite3.connect = counted_connect
            for round_index in range(args.rounds):
                for name, operation in (("previous", previous), ("current", current)):
                    connections = 0
                    wall_started, cpu_started = time.perf_counter(), time.process_time()
                    selected = operation()
                    cpu, wall = time.process_time() - cpu_started, time.perf_counter() - wall_started
                    assert selected["id"] == approvals[args.historical]["id"]
                    results[name].append((wall * 1000, cpu * 1000, connections))
                    print(json.dumps({
                        "case": name, "round": round_index + 1,
                        "wall_ms": round(wall * 1000, 3), "cpu_ms": round(cpu * 1000, 3),
                        "sqlite_connections": connections,
                    }, sort_keys=True), flush=True)
            aggregate = {
                "historical_records": args.historical, "pending_records": args.pending,
                "rounds": args.rounds, "selection_equal": True,
                "full_warmup": args.full_warmup,
                "current_includes_fresh_delivery_recheck": True,
            }
            metric_prefix = "median_" if args.rounds > 1 else ""
            for name, measurements in results.items():
                aggregate[name] = {
                    metric_prefix + "wall_ms": round(statistics.median(row[0] for row in measurements), 3),
                    metric_prefix + "cpu_ms": round(statistics.median(row[1] for row in measurements), 3),
                    metric_prefix + "sqlite_connections": statistics.median(row[2] for row in measurements),
                }
            print(json.dumps(aggregate, sort_keys=True), flush=True)
    finally:
        sqlite3.connect = original_connect
        codex._RUNTIME = original_runtime
        customer_reply_state._customer_reply_sealed_results_enabled = original_sealed_enabled
        with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
            customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()


if __name__ == "__main__":
    main()
