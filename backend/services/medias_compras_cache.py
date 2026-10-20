"""Bounded, process-local full results and one surviving owner per logical query.

Authentication is performed by the endpoint dependency; ``probe`` revalidates
the current tenant/store/policy before *any* cache lookup. Source versions are
fences, deliberately not part of the in-flight key.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from backend.services.blocking_workers import run_blocking, run_heavy


@dataclass(frozen=True)
class Fontes:
    assinatura: tuple
    autoridade: tuple
    alvo: str = ""
    snapshot: Any = field(default=None, compare=False, repr=False)
    pendente: bool = False
    politica_fotos: tuple = ()


_COMMIT: ContextVar[list[str] | None] = ContextVar("medias_commit_receipt", default=None)


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def registrar_commit_lista(digest: str) -> None:
    receipt = _COMMIT.get()
    if receipt is not None:
        receipt[:] = [digest]


@dataclass
class _Resultado:
    tenant: str
    body: bytes
    fontes: Fontes
    instante: float
    gerado_em: str
    versao: str

    @property
    def peso(self):
        # Include a conservative allowance for the compact fences/entry object.
        return len(self.body) + 2048


def _compactar_fontes(source):
    digest = lambda value: hashlib.sha256(repr(value).encode()).hexdigest()
    return Fontes(
        (digest(source.assinatura),), (digest(source.autoridade),), source.alvo,
        pendente=source.pendente, politica_fotos=(digest(source.politica_fotos or source.autoridade),),
    )


class ResultadoCache:
    FRESH_SECONDS = 60
    STALE_SECONDS = 15 * 60
    MAX_ENTRIES = 64
    MAX_BYTES = 128 * 1024 * 1024
    TENANT_ENTRIES = 16
    TENANT_BYTES = 32 * 1024 * 1024
    MAX_INFLIGHT = 8
    TENANT_INFLIGHT = 4
    ERROR_RETRY_SECONDS = 5

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._lock = threading.RLock()
        self._results: OrderedDict[tuple, _Resultado] = OrderedDict()
        self._flights: dict[tuple, tuple[str, asyncio.Task]] = {}
        self._errors: dict[tuple, tuple[float, dict]] = {}

    @staticmethod
    def _error(exc: BaseException) -> dict:
        # Never expose filesystem paths, arbitrary exception text or data.
        code = "sources_changing" if isinstance(exc, HTTPException) and exc.status_code == 409 else "refresh_failed"
        return {"code": code, "message": (
            "Os dados estao mudando. Tente atualizar novamente." if code == "sources_changing"
            else "Nao foi possivel atualizar os dados. Tente novamente."
        )}

    def _expire(self, now: float) -> None:
        for key, result in list(self._results.items()):
            if now - result.instante > self.STALE_SECONDS:
                self._results.pop(key, None)
        for key, (instant, _) in list(self._errors.items()):
            if now - instant > self.ERROR_RETRY_SECONDS:
                self._errors.pop(key, None)

    def _store(self, key: tuple, value: dict, source: Fontes) -> bool:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(body) + 2048 > min(self.MAX_BYTES, self.TENANT_BYTES):
            return False
        tenant = key[0]
        self._results[key] = _Resultado(
            tenant, body, _compactar_fontes(source), self.clock(),
            datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            hashlib.sha256(repr((source.assinatura, source.autoridade, source.alvo)).encode()).hexdigest()[:24],
        )
        self._results.move_to_end(key)
        while True:
            tenant_keys = [k for k, v in self._results.items() if v.tenant == tenant]
            tenant_bytes = sum(self._results[k].peso for k in tenant_keys)
            if len(tenant_keys) > self.TENANT_ENTRIES or tenant_bytes > self.TENANT_BYTES:
                self._results.pop(tenant_keys[0])
            elif len(self._results) > self.MAX_ENTRIES or sum(v.peso for v in self._results.values()) > self.MAX_BYTES:
                self._results.popitem(last=False)
            else:
                break
        return True

    @staticmethod
    def _metadata(result, fonte, *, stale=False, running=False, error=None):
        return {
            "estado": ("error" if error else "refreshing" if result and running else "waiting" if running else "ready"),
            "gerado_em": result.gerado_em if result else None,
            "versao": result.versao if result else None,
            "stale": bool(stale), "atualizando": bool(running),
            "retry_after_ms": 1000 if running else 5000 if error else 0,
            "erro": error,
            "autoridade": hashlib.sha256(repr((fonte.autoridade, fonte.alvo)).encode()).hexdigest(),
            "politica_fotos": fonte.politica_fotos[0],
            "pendente_fontes": fonte.pendente,
            "completo": result is not None,
        }

    def _calculate(self, probe, load, submitted, initial):
        from backend.services.medias_compras_common import _ListaPedidoConflict
        from backend.services.medias_compras_leitura import leitura_medias, medir_etapa
        from backend.services.medias_compras_fontes import categorias_alteradas

        queue_ms = (time.perf_counter() - submitted) * 1000
        started = time.perf_counter()
        metrics = {}
        execution = uuid.uuid4().hex[:12]
        attempts = 0
        logger = logging.getLogger("jk_sistema")
        diagnostic_stages = (
            "fontes", "calculo", "cadastro", "cadastro_contexto", "catalogo_fiscal",
            "metadados_principal", "metadados_historico", "metadados_contexto", "fiscal",
            "enriquecimento", "frete", "itens", "fotos", "fotos_resolucao", "historico",
            "csv_leitura", "csv_parse", "csv_copia", "comparacao", "persistencia", "resumo",
        )
        try:
            for attempt in range(2):
                attempts = attempt + 1
                previous_metrics = metrics.copy()
                attempt_started = time.perf_counter()
                try:
                    # The authenticated preflight is the first before fence.
                    # Changes while queued consume the same single retry as a
                    # transactional conflict; diagnostics never relax the fence.
                    if attempt:
                        probe_started = time.perf_counter()
                        before = probe()
                        metrics["fontes"] = metrics.get("fontes", 0) + (time.perf_counter() - probe_started) * 1000
                    else:
                        before = initial
                    # An open publication/rollback journal guarantees this
                    # attempt cannot be published. Re-probe within the same
                    # retry budget instead of performing a full calculation
                    # that will necessarily be discarded.
                    if before.pendente:
                        logger.info("[MEDIAS_FENCE] execucao=%s tentativa=%d fontes_pendentes=1 calculo_adiado=1", execution, attempts)
                        continue
                    receipt = []
                    token = _COMMIT.set(receipt)
                    try:
                        with leitura_medias(metrics=metrics), medir_etapa("calculo"):
                            value = load(before)
                    except _ListaPedidoConflict:
                        logger.info("[MEDIAS_FENCE] execucao=%s tentativa=%d conflito_transacao=1", execution, attempts)
                        if attempt:
                            raise
                        continue
                    finally:
                        _COMMIT.reset(token)
                    probe_started = time.perf_counter()
                    after = probe()
                    metrics["fontes"] = metrics.get("fontes", 0) + (time.perf_counter() - probe_started) * 1000
                    expected_target = receipt[-1] if receipt else before.alvo
                    consistent = (not before.pendente and not after.pendente and
                                  before.assinatura == after.assinatura and before.autoridade == after.autoridade and
                                  expected_target == after.alvo)
                    logger.info(
                        "[MEDIAS_FENCE] execucao=%s tentativa=%d consistente=%d pendente_antes=%d pendente_depois=%d autoridade_alterada=%d alvo_alterado=%d commit_proprio=%d categorias=%s",
                        execution, attempts, consistent, before.pendente, after.pendente,
                        before.autoridade != after.autoridade, expected_target != after.alvo, bool(receipt),
                        json.dumps(categorias_alteradas(before, after), sort_keys=True, separators=(",", ":")),
                    )
                    if consistent:
                        return value, after
                finally:
                    stage_ms = {name: round(metrics.get(name, 0) - previous_metrics.get(name, 0), 1) for name in diagnostic_stages}
                    stage_ms["total"] = round((time.perf_counter() - attempt_started) * 1000, 1)
                    counts = {name: int(metrics.get(name, 0) - previous_metrics.get(name, 0)) for name in (
                        "leituras_fisicas", "csv_parses", "csv_copias",
                    )}
                    logger.info("[MEDIAS_ATTEMPT] execucao=%s tentativa=%d tempos_ms=%s contagens=%s", execution, attempts,
                                json.dumps(stage_ms, sort_keys=True, separators=(",", ":")),
                                json.dumps(counts, sort_keys=True, separators=(",", ":")))
            raise HTTPException(409, {"code": "sources_changing", "message": "Os dados mudaram durante o calculo. Tente novamente."})
        finally:
            logger.info(
                "[MEDIAS_TIMING] execucao=%s tentativas=%d fila_ms=%.1f fontes_ms=%.1f cadastro_ms=%.1f fotos_ms=%.1f historico_ms=%.1f calculo_ms=%.1f persistencia_ms=%.1f total_ms=%.1f",
                execution, attempts, queue_ms, metrics.get("fontes", 0), metrics.get("cadastro", 0), metrics.get("fotos", 0), metrics.get("historico", 0),
                metrics.get("calculo", 0), metrics.get("persistencia", 0), (time.perf_counter() - started) * 1000,
            )

    async def _owner(self, key, probe, load, initial):
        try:
            value, source = await run_heavy(self._calculate, probe, load, time.perf_counter(), initial)
            publish_started = time.perf_counter()
            with self._lock:
                if self._store(key, value, source):
                    self._errors.pop(key, None)
                else:
                    # Preserve legacy large responses without retaining them.
                    # Opt-in polling receives a terminal, sanitized warning.
                    self._errors[key] = (self.clock(), {
                        "code": "result_too_large", "message": "O resultado excede o limite de memoria da consulta.",
                    })
            logging.getLogger("jk_sistema").info("[MEDIAS_TIMING] publicacao_cache_ms=%.1f", (time.perf_counter() - publish_started) * 1000)
            return value
        except Exception as exc:
            with self._lock:
                self._errors[key] = (self.clock(), self._error(exc))
            raise
        finally:
            with self._lock:
                self._flights.pop(key, None)

    async def consultar(self, tenant: str, query: tuple, probe, load, *, stale: bool):
        # No registry lock is held during filesystem work or authentication.
        started = time.perf_counter()
        source = await run_blocking(probe)
        logging.getLogger("jk_sistema").info("[MEDIAS_TIMING] preflight_ms=%.1f", (time.perf_counter() - started) * 1000)
        compact_source = _compactar_fontes(source)
        logical_query = query(source) if callable(query) else query
        key = (str(tenant), *logical_query)
        with self._lock:
            now = self.clock()
            self._expire(now)
            result = self._results.get(key)
            if result and (result.fontes.autoridade != compact_source.autoridade or result.fontes.alvo != source.alvo or source.pendente):
                self._results.pop(key, None)
                result = None
            flight = self._flights.get(key)
            running = flight is not None
            fresh = bool(result and result.fontes.assinatura == compact_source.assinatura and now - result.instante <= self.FRESH_SECONDS)
            if stale and fresh and not running:
                self._results.move_to_end(key)
                return {**json.loads(result.body), "_cache": self._metadata(result, compact_source)}
            error_record = self._errors.get(key)
            error = error_record[1] if error_record else None
            if not flight and not (stale and error):
                busy = len(self._flights) >= self.MAX_INFLIGHT or sum(t == str(tenant) for t, _ in self._flights.values()) >= self.TENANT_INFLIGHT
                if busy:
                    if stale:
                        metadata = self._metadata(result, compact_source, stale=True)
                        metadata.update(estado="waiting", retry_after_ms=2000)
                        if result:
                            return {**json.loads(result.body), "_cache": metadata}
                        return JSONResponse(status_code=202, content={"success": True, "dados": None, "_cache": metadata}, headers={"Retry-After": "2"})
                    if not result:
                        raise HTTPException(503, "Ha consultas em andamento. Tente novamente em instantes.", headers={"Retry-After": "2"})
                    error = {"code": "busy", "message": "Ha consultas em andamento. Tente novamente em instantes."}
                else:
                    owner = asyncio.create_task(self._owner(key, probe, load, source))
                    # Keep ownership after disconnect, and retrieve unobserved failures.
                    owner.add_done_callback(lambda task: None if task.cancelled() else task.exception())
                    self._flights[key] = (str(tenant), owner)
                    flight, running, error = self._flights[key], True, None
            if stale:
                metadata = self._metadata(result, compact_source, stale=not fresh, running=running, error=error)
                if result:
                    self._results.move_to_end(key)
                    return {**json.loads(result.body), "_cache": metadata}
                if error and not running:
                    raise HTTPException(409 if error["code"] == "sources_changing" else 503, error)
                return JSONResponse(status_code=202, content={"success": True, "dados": None, "_cache": metadata}, headers={"Retry-After": "1"})
            if not flight:
                raise HTTPException(503, "Ha consultas em andamento. Tente novamente em instantes.")
        value = await asyncio.shield(flight[1])
        # A caller is not allowed to mutate the shared worker's result.
        copy_started = time.perf_counter()
        response = json.loads(json.dumps(value, ensure_ascii=False))
        logging.getLogger("jk_sistema").info("[MEDIAS_TIMING] copia_resposta_ms=%.1f", (time.perf_counter() - copy_started) * 1000)
        return response


resultados_medias = ResultadoCache()


async def consultar_snapshot_salvo(probe, load):
    """Opt-in saved projection, fenced but without an enrichment owner/job."""
    def read():
        for _attempt in range(2):
            before = probe()
            if before.pendente:
                metadata = ResultadoCache._metadata(None, _compactar_fontes(before))
                metadata.update(estado="waiting", stale=True, retry_after_ms=1000)
                return JSONResponse(status_code=202, content={"success": True, "dados": None, "_cache": metadata})
            value = load(before)
            after = probe()
            if before == after and not after.pendente:
                metadata = ResultadoCache._metadata(None, _compactar_fontes(after))
                metadata["gerado_em"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                metadata["versao"] = hashlib.sha256(repr((after.assinatura, after.alvo)).encode()).hexdigest()[:24]
                return {**value, "_cache": metadata}
        raise HTTPException(409, {"code": "sources_changing", "message": "Os dados mudaram durante a leitura. Tente novamente."})
    return await run_blocking(read)
