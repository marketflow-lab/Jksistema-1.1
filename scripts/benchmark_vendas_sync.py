"""Deterministic, offline synchronization benchmark using the real queue/runner.

The baseline sources come from an explicit Git commit. Both versions execute
their own SyncQueue and synchronization coroutine with the same fake source.
Only waits in the real quota functions are translated to asyncio waits so a
virtual event loop can measure concurrent I/O without wall-clock sleeps.

This measures scheduling and HTTP-quota waiting, not production CPU, SQLite or
Firestore latency. It never starts a backend or reads operational data.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from contextlib import asynccontextmanager, contextmanager
import contextvars
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASELINE_COMMIT = "751885950c0bcff6390bc15e79baaa4c7b669bbd"
FINAL_DAY = dt.date(2026, 10, 26)
EPOCH = dt.datetime(2026, 10, 26, 12, tzinfo=dt.timezone(dt.timedelta(hours=-3))).timestamp()
TENANT = "benchmark"


class VirtualClock:
    now = 0.0

    def monotonic(self):
        return self.now

    def time(self):
        return EPOCH + self.now


class VirtualLoop(asyncio.BaseEventLoop):
    """Use asyncio's actual ready/timer queues, jumping only over idle waits."""

    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        # No network is used: avoid even asyncio's local wake-up socket pair.
        self._selector = SimpleNamespace(select=lambda _: [])

    def _process_events(self, events):
        pass

    def _write_to_self(self):
        pass

    def time(self):
        return self.clock.now

    def _run_once(self):
        if not self._ready and self._scheduled:
            due = [handle._when for handle in self._scheduled if not handle.cancelled()]
            if due:
                self.clock.now = max(self.clock.now, min(due))
        super()._run_once()


class VirtualCondition:
    """Each task is one production worker; synchronous sections remain atomic."""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    async def wait(self, delay):
        await asyncio.sleep(delay)

    def notify_all(self):
        pass


class WorkerContext:
    """Task-local equivalent of the production runner's thread-local context."""

    def __init__(self):
        object.__setattr__(self, "value", contextvars.ContextVar("benchmark_worker"))

    def bind(self, value):
        return self.value.set(value)

    def __getattr__(self, name):
        return getattr(self.value.get(), name)


class AsyncQuota(ast.NodeTransformer):
    """Translate only blocking wait syntax; quota decisions stay source-exact."""

    def visit_Call(self, node):
        self.generic_visit(node)
        if ((isinstance(node.func, ast.Name) and node.func.id == "_bling_cancelable_sleep")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "wait")):
            return ast.copy_location(ast.Await(value=node), node)
        return node


def _git_source(commit, path):
    result = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=ROOT,
                            check=True, capture_output=True)
    return result.stdout.decode("utf-8")


def _module(source, label):
    module = ModuleType("backend.modules.vendas._benchmark_" + label)
    module.__package__ = "backend.modules.vendas"
    exec(compile(source, "<benchmark:" + label + ">", "exec"), module.__dict__)
    return module


def _virtual_quota(source, candidate, clock):
    from backend.services import bling
    from backend.services.bling_accounts import register_bling_account

    name = "bling_account_request" if candidate else "_bling_wait_account_turn"
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    function = AsyncQuota().visit(copy.deepcopy(function))
    adapted = ast.AsyncFunctionDef(
        name=function.name, args=function.args, body=function.body,
        decorator_list=[ast.Name(id="asynccontextmanager", ctx=ast.Load())] if candidate else [],
        returns=function.returns, type_comment=function.type_comment,
        type_params=getattr(function, "type_params", []),
    )
    tree = ast.fix_missing_locations(ast.Module(body=[adapted], type_ignores=[]))
    namespace = dict(bling.__dict__)
    namespace.update(
        time=clock, asynccontextmanager=asynccontextmanager,
        _BLING_ACCOUNT_NEXT_CALL={}, _BLING_ACCOUNT_LIMIT_LOCK=VirtualCondition(),
        _BLING_QUOTA_CONDITION=VirtualCondition(), _BLING_QUOTA_ACTIVE=0,
        _BLING_QUOTA_ACTIVE_KEYS=set(),
        _BLING_QUOTA_UNKNOWN_ACTIVE=False, _BLING_QUOTA_UNKNOWN_WAITERS=0,
        _BLING_QUOTA_LAST_START=0.0, _BLING_QUOTA_UNKNOWN_UNTIL=0.0,
    )

    async def sleep(seconds, **_):
        await asyncio.sleep(seconds)

    namespace["_bling_cancelable_sleep"] = sleep
    exec(compile(tree, "<benchmark:async-quota>", "exec"), namespace)
    for index in range(4):
        register_bling_account(f"offline-account-{index}", str(10001 + index))
    return namespace[name]


def _day_payload(account, day):
    """Same complete commercial source for both runners; amounts are cents."""
    key = f"{account}:{day}"
    return {
        "sales": [
            {"id": key + ":sale-1", "sku": "simple", "quantity": 2, "value_cents": 2500},
            {"id": key + ":sale-2", "sku": "kit", "quantity": 1, "value_cents": 3100},
        ],
        "returns": [{"id": key + ":return", "sku": "simple", "quantity": 1, "value_cents": 1250}],
        "items": [{"id": key + ":invoice-item", "sku": "simple", "quantity": 1}],
    }


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _scenario(sources, *, candidate, accounts, days, latency):
    from backend.schemas import VendasSyncRequest

    clock = VirtualClock()
    queue_module = _module(sources["queue"], "candidate_queue" if candidate else "baseline_queue")
    engine = _module(sources["engine"], "candidate_engine" if candidate else "baseline_engine")
    queue_module.time = clock
    engine.time = clock

    class FixedDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(clock.time(), tz)

    engine.datetime = FixedDatetime
    context = WorkerContext()
    engine.SYNC_THREAD_CONTEXT = context
    engine.SYNC_META = {}
    engine.SYNC_DAY_CONTEXT = {}
    engine._sync_context_key = lambda _: context.job_id
    engine._set_progresso = lambda *_: None
    engine._sync_log = lambda *_: None
    engine._verificar_cancelamento = lambda _: None
    quota = _virtual_quota(sources["bling"], candidate, clock)
    commits, requests, compositions, available = {}, [], {}, {}
    turns = []
    max_active = 0
    start = str(FINAL_DAY - dt.timedelta(days=days - 1))
    end = str(FINAL_DAY)

    async def request(account, endpoint):
        headers = {"Authorization": f"Bearer offline-account-{account}"}

        async def transport():
            requests.append({"account": account, "endpoint": endpoint, "at": clock.now})
            await asyncio.sleep(latency)

        if candidate:
            async with quota(headers) as mark_start:
                mark_start()
                await transport()
        else:
            await quota(headers)
            await transport()

    async def collect(req, _client, reset_estado=False):
        account = int(req.store_id.removeprefix("store-"))
        turns.append({"account": account, "day": req.data_inicio, "at": clock.now})
        # One orders page, two required order details, both invoice listings,
        # and one required return-invoice detail. All six return valid responses.
        for endpoint in ("orders", "order-1", "order-2", "outgoing-invoices", "incoming-invoices", "return-detail"):
            await request(account, endpoint)
        commits[f"{account}:{req.data_inicio}"] = _day_payload(account, req.data_inicio)
        return {"success": True, "total": 2, "notas_entrada_total": 1,
                "vendas_processadas": 2, "notas_processadas": 1}

    def composition(_client, store_id, **_):
        compositions[store_id] = {"kit": [{"sku": "simple", "quantity": 2}]}
        return {"updated": 0, "pending": False}

    @contextmanager
    def checkpoint(_client, _store, job_id, _day, **_):
        yield SimpleNamespace(cycle_id=job_id, started_at=clock.time())

    engine._sincronizar_vendas_periodo_impl = collect
    engine.collection_checkpoint = checkpoint
    engine.collection_day_summary = lambda *_: {"documentos_salvos": 3}
    engine.finish_collection_day = lambda *_, **__: None
    composition_module = ModuleType("backend.modules.vendas.composition_sync")
    composition_module.sincronizar_composicoes_vendas = composition
    with tempfile.TemporaryDirectory(prefix="vendas-benchmark-", dir=ROOT.parent) as scratch:
        queue = queue_module.SyncQueue(TENANT, tenant_path=Path(scratch))
        engine.SyncQueue = lambda _: queue
        engine._get_vendas_db_path = lambda _client, name: str(Path(scratch) / (name + ".db"))
        real_complete_day = queue.complete_day

        def complete_day(job_id, owner, day, result, *args, **kwargs):
            saved = real_complete_day(job_id, owner, day, result, *args, **kwargs)
            if day == end:
                available.setdefault(saved["store_id"], clock.now)
            return saved

        queue.complete_day = complete_day
        for index in range(accounts):
            queue.enqueue(f"store-{index}", f"Account {index}", start, end,
                          now=clock.time())

        async def run():
            nonlocal max_active
            active = set()
            sequence = 0

            async def execute(job, owner):
                context.bind(SimpleNamespace(job_id=job["id"], queue_job=job, queue_owner=owner,
                    lease_check=lambda: queue.owns(job["id"], owner), loja=job["loja"]))
                req = VendasSyncRequest(loja=job["loja"], store_id=job["store_id"],
                                       data_inicio=start, data_fim=end)
                options = {"day_budget": 1} if candidate else {}
                return await engine._sincronizar_vendas_impl(req, TENANT, **options)

            while True:
                while True:
                    sequence += 1
                    owner = f"offline-worker-{sequence}"
                    job = queue.claim(owner, now=clock.time(), lease_seconds=3600, limit=50)
                    if job is None:
                        break
                    active.add(asyncio.create_task(execute(job, owner)))
                    max_active = max(max_active, len(active))
                if not active:
                    break
                done, active = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            assert all(job["status"] == "complete" for job in queue.list_jobs())

        loop = VirtualLoop(clock)
        try:
            asyncio.set_event_loop(loop)
            with patch.dict(sys.modules, {composition_module.__name__: composition_module}):
                loop.run_until_complete(run())
        finally:
            loop.close()
            asyncio.set_event_loop(None)
        assert len(commits) == accounts * days
        assert len(available) == accounts
        assert len(compositions) == accounts
        account_starts = {}
        for item in requests:
            key = item["account"] if candidate else "global"
            previous = account_starts.get(key)
            assert previous is None or item["at"] - previous >= 0.35 - 1e-8
            account_starts[key] = item["at"]
        result = {
            "latest_day_all_accounts_seconds": round(max(available.values()), 6),
            "total_seconds": round(clock.now, 6),
            "requests": len(requests), "max_active_accounts": max_active,
            "confirmed_days": len(commits), "commercial_digest": _digest(commits),
            "compositions_digest": _digest(compositions), "turns": turns,
            "totals": {"sales": accounts * days * 2, "sales_quantity": accounts * days * 3,
                       "sales_value_cents": accounts * days * 5600, "returns": accounts * days,
                       "return_quantity": accounts * days, "return_value_cents": accounts * days * 1250,
                       "return_items": accounts * days, "composition_catalogues": accounts},
        }
    return result


def benchmark(*, baseline_commit=BASELINE_COMMIT, days=7, latency_ms=125):
    if not 1 <= days <= 31 or latency_ms < 100:
        raise ValueError("Use 1-31 days and at least 100 ms latency per fake request.")
    paths = {"queue": "backend/modules/vendas/sync_queue.py",
             "engine": "backend/modules/vendas/sync_engine.py", "bling": "backend/services/bling.py"}
    baseline = {key: _git_source(baseline_commit, path) for key, path in paths.items()}
    candidate = {key: (ROOT / path).read_text(encoding="utf-8") for key, path in paths.items()}
    output = {
        "kind": "offline_virtual_io_simulation", "baseline_commit": baseline_commit,
        "source_sha256": {"baseline": {key: hashlib.sha256(value.encode()).hexdigest() for key, value in baseline.items()},
                          "candidate": {key: hashlib.sha256(value.encode()).hexdigest() for key, value in candidate.items()}},
        "fixture": {"accounts": [1, 4], "days": days, "final_day": str(FINAL_DAY),
                    "requests_per_account_day": 6, "http_latency_ms": latency_ms,
                    "compositions": "same previously confirmed catalogue; zero additional HTTP"},
        "limitations": ["Simulated I/O time, not observed production improvement.",
                        "Excludes CPU, SQLite/Firestore latency, company discovery and transient errors.",
                        "Commercial parity covers the fake source and actual queue acknowledgements; production persistence and kit calculation need their integration tests.",
                        "Runs actual Git-baseline/current queue and engine coroutines; quota wait syntax alone is adapted to asyncio.",
                        "One ready task represents one production worker; real per-account and global quota decisions are retained."],
        "scenarios": {},
    }
    for accounts in (1, 4):
        old = _scenario(baseline, candidate=False, accounts=accounts, days=days, latency=latency_ms / 1000)
        new = _scenario(candidate, candidate=True, accounts=accounts, days=days, latency=latency_ms / 1000)
        assert old["commercial_digest"] == new["commercial_digest"]
        assert old["compositions_digest"] == new["compositions_digest"]
        assert old["totals"] == new["totals"]
        total_change = (new["total_seconds"] / old["total_seconds"] - 1) * 100
        recent_reduction = (1 - new["latest_day_all_accounts_seconds"] / old["latest_day_all_accounts_seconds"]) * 100
        output["scenarios"][str(accounts)] = {
            "baseline": old, "candidate": new, "same_commercial_data": True,
            "latest_day_reduction_percent": round(recent_reduction, 3),
            "total_change_percent": round(total_change, 3),
            "target_passed": total_change <= 5.0 if accounts == 1 else recent_reduction >= 40.0,
        }
    output["all_targets_passed"] = all(value["target_passed"] for value in output["scenarios"].values())
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-commit", default=BASELINE_COMMIT)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--latency-ms", type=float, default=125)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--enforce-targets", action="store_true")
    args = parser.parse_args()
    result = benchmark(baseline_commit=args.baseline_commit, days=args.days, latency_ms=args.latency_ms)
    serialized = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
        print(json.dumps({"output": str(args.output), "all_targets_passed": result["all_targets_passed"],
                          "scenarios": {key: {name: value[name] for name in
                              ("latest_day_reduction_percent", "total_change_percent", "target_passed")}
                              for key, value in result["scenarios"].items()}}, ensure_ascii=False))
    else:
        print(serialized, end="")
    return 1 if args.enforce_targets and not result["all_targets_passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
