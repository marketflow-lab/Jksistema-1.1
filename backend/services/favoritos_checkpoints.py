"""Durable, owner-scoped checkpoints for the renderer's manual ranking collector.

This service never opens a browser or performs a Mercado Livre operation.
History publication is a recoverable, idempotent second phase.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlsplit

from fastapi import HTTPException

VERSION = 1
LEASE_MS = 120000
MAX_CHECKPOINTS_LIST = 100
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def _lock(path):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(os.path.normcase(os.path.abspath(path)), threading.RLock())


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _fail(code, message, status=409):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def _bounded(value, depth=0):
    if depth > 7:
        _fail("invalid_payload", "Estrutura do checkpoint excede o limite.", 422)
    if isinstance(value, dict):
        if len(value) > 100:
            _fail("invalid_payload", "Objeto excede o limite de campos.", 422)
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 80:
                _fail("invalid_payload", "Campo invalido no checkpoint.", 422)
            if key == "descricao" and isinstance(item, str):
                if len(item) > 8000:
                    _fail("invalid_payload", "Descricao excede o limite do checkpoint.", 422)
            else:
                _bounded(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 240:
            _fail("invalid_payload", "Lista excede o limite de itens.", 422)
        for item in value:
            _bounded(item, depth + 1)
    elif isinstance(value, str):
        if len(value) > 1600:
            _fail("invalid_payload", "Texto excede o limite do checkpoint.", 422)
    elif isinstance(value, (float, int)) and not isinstance(value, bool):
        if not math.isfinite(value) or abs(value) > 1e15:
            _fail("invalid_payload", "Numero invalido no checkpoint.", 422)
    elif value is not None and not isinstance(value, bool):
        _fail("invalid_payload", "Valor invalido no checkpoint.", 422)


def _candidate_key(item):
    for field in ("url", "permalink", "link", "link_normalizado"):
        link_value = str(item.get(field) or "")
        if not link_value:
            continue
        parsed = urlsplit(link_value)
        host = str(parsed.hostname or "").lower()
        if parsed.scheme != "https" or (host != "mercadolivre.com.br" and not host.endswith(".mercadolivre.com.br")) or parsed.username or parsed.password:
            _fail("invalid_candidate", "Link do anuncio fora do Mercado Livre.", 422)
    value = str(item.get("id") or item.get("mlb") or "").upper().replace("-", "")
    if re.fullmatch(r"MLB[0-9]+", value):
        return "mlb:" + value.lower()
    link = str(item.get("url") or item.get("permalink") or item.get("link") or "")
    match = re.search(r"MLB-?([0-9]+)", link, re.I)
    if match:
        return "mlb:mlb" + match.group(1)
    if re.match(r"^https://(?:[^/]+\.)?mercadolivre\.com\.br/", link, re.I):
        return "url:" + link.split("#", 1)[0].split("?", 1)[0].rstrip("/").lower()
    _fail("invalid_candidate", "Anuncio do checkpoint sem identidade Mercado Livre valida.", 422)


def normalize_candidates(items, terms, max_per_term):
    by_key = {}
    allowed_terms = {term["termo"] for term in terms}
    for item in items:
        _bounded(item)
        key = _candidate_key(item)
        origins = list(dict.fromkeys(item.get("pesquisas_origem") or []))
        if not origins:
            if len(allowed_terms) != 1:
                _fail("invalid_candidate", "Origem da pesquisa obrigatoria para o candidato.", 422)
            origins = list(allowed_terms)
        item = {**item, "pesquisas_origem": origins}
        item["campos_origem"] = ["Pesquisa " + str(term["campo"]) for term in terms if term["termo"] in origins]
        if any(origin not in allowed_terms for origin in origins):
            _fail("invalid_candidate", "Origem do anuncio difere dos termos salvos.", 422)
        if key in by_key:
            target = by_key[key]
            for field, value in item.items():
                if field in ("pesquisas_origem", "campos_origem"):
                    target[field] = list(dict.fromkeys((target.get(field) or []) + (value or [])))
                elif value not in (None, "", [], {}) and target.get(field) in (None, "", [], {}):
                    target[field] = copy.deepcopy(value)
        else:
            by_key[key] = copy.deepcopy(item)
    result = list(by_key.values())
    if len(result) > len(terms) * max_per_term:
        _fail("invalid_candidate", "Quantidade de candidatos excede o limite das pesquisas.", 422)
    for term in allowed_terms:
        if sum(term in (item.get("pesquisas_origem") or []) for item in result) > max_per_term:
            _fail("invalid_candidate", "Quantidade de candidatos por pesquisa excede o limite.", 422)
    return result


def ranked_total(data):
    if data.get("total_rankeados") is not None:
        return data["total_rankeados"]
    if data.get("estado_fechamento") in ("fechamento_pendente", "confirmado") and data.get("closing_snapshot") is None:
        return 0
    return None


def metadata(data):
    names = ("id", "version", "revision", "loja", "sku", "titulo", "estado_coleta", "estado_fechamento",
             "history_entry_id", "parent_history_entry_id", "parent_id", "root_id", "created_at", "updated_at",
             "lease_expires_at_ms", "retomado_por", "retomada_numero", "duracao_execucao_ms", "snapshot_hash")
    result = {key: copy.deepcopy(data.get(key)) for key in names}
    result["termos"] = [{key: item.get(key) for key in ("campo", "termo", "estado")} for item in data["termos"]]
    result["total_candidatos"] = len(data.get("candidatos") or [])
    result["total_rankeados"] = ranked_total(data)
    result["retomavel"] = not (data.get("estado_coleta") == "concluida" and data.get("estado_fechamento") == "confirmado") and not data.get("retomado_por")
    return result


class CheckpointStore:
    def __init__(self, tenant_path: str, username: str, upsert_history: Callable, normalize_summaries: Callable):
        # Hashing prevents slug collisions between independently authenticated users.
        owner = hashlib.sha256(str(username).strip().casefold().encode("utf-8")).hexdigest()[:32]
        self.path = os.path.join(tenant_path, "favoritos_ranking_checkpoints_" + owner + ".db")
        self.upsert_history = upsert_history
        self.normalize_summaries = normalize_summaries

    @contextmanager
    def connection(self):
        with _lock(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=15)
            conn.execute("PRAGMA busy_timeout=15000")
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS checkpoints (id TEXT PRIMARY KEY, create_request_id TEXT UNIQUE NOT NULL, create_hash TEXT NOT NULL, payload_json TEXT NOT NULL, updated_at_ms INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS receipts (checkpoint_id TEXT NOT NULL, operation TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL, result_id TEXT NOT NULL, created_at_ms INTEGER NOT NULL, PRIMARY KEY(checkpoint_id, operation, request_id))")
                conn.commit()
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def read(self, conn, checkpoint_id, loja):
        if not re.fullmatch(r"[0-9a-f-]{36}", checkpoint_id):
            _fail("checkpoint_missing", "Checkpoint nao encontrado.", 404)
        row = conn.execute("SELECT payload_json FROM checkpoints WHERE id = ?", (checkpoint_id,)).fetchone()
        if not row:
            _fail("checkpoint_missing", "Checkpoint nao encontrado.", 404)
        data = json.loads(row[0])
        if data.get("version") != VERSION:
            _fail("unsupported_version", "Checkpoint de versao nao suportada.")
        if data["loja"] != loja:
            _fail("store_mismatch", "Checkpoint pertence a outra loja.", 403)
        return data

    def write(self, conn, data, create_request_id=None, create_hash=None):
        now = int(time.time() * 1000)
        data["updated_at"] = datetime.fromtimestamp(now / 1000, timezone.utc).isoformat()
        if create_request_id:
            conn.execute("INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?)", (data["id"], create_request_id, create_hash, _json(data), now))
        else:
            conn.execute("UPDATE checkpoints SET payload_json=?, updated_at_ms=? WHERE id=?", (_json(data), now, data["id"]))

    def receipt(self, conn, data, operation, request):
        row = conn.execute("SELECT request_hash, result_id FROM receipts WHERE checkpoint_id=? AND operation=? AND request_id=?", (data["id"], operation, request["request_id"])).fetchone()
        if row:
            if row[0] != _hash(request):
                _fail("idempotency_conflict", "Identificador de request reutilizado com dados diferentes.")
            return row[1]
        return None

    def record_receipt(self, conn, data, operation, request, result_id=None):
        conn.execute("INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?)", (data["id"], operation, request["request_id"], _hash(request), result_id or data["id"], int(time.time() * 1000)))
        conn.execute("DELETE FROM receipts WHERE checkpoint_id=? AND rowid NOT IN (SELECT rowid FROM receipts WHERE checkpoint_id=? ORDER BY rowid DESC LIMIT 64)", (data["id"], data["id"]))

    def guard(self, data, revision, token=None, lease=True):
        if data["revision"] != revision:
            _fail("revision_conflict", "Checkpoint alterado; recarregue antes de continuar.")
        if token is not None and not secrets.compare_digest(data["attempt_token"], token):
            _fail("attempt_conflict", "Tentativa de coleta invalida.")
        if lease and data["lease_expires_at_ms"] <= int(time.time() * 1000):
            _fail("lease_expired", "Tentativa expirou; retome pelo historico.")

    def full(self, data, token=False):
        result = copy.deepcopy(data)
        result["total_rankeados"] = ranked_total(data)
        result.pop("closing_snapshot", None)
        result.pop("closing_request_hash", None)
        result.pop("closing_request_id", None)
        if not token:
            result.pop("attempt_token", None)
        return result

    def create(self, request, loja):
        _bounded(request)
        with self.connection() as conn:
            row = conn.execute("SELECT create_hash,payload_json FROM checkpoints WHERE create_request_id=?", (request["request_id"],)).fetchone()
            if row:
                if row[0] != _hash(request):
                    _fail("idempotency_conflict", "Request de criacao reutilizado com dados diferentes.")
                data = json.loads(row[1])
                if data["loja"] != loja:
                    _fail("store_mismatch", "Checkpoint pertence a outra loja.", 403)
                return self.full(data, token=True)
            now = int(time.time() * 1000)
            checkpoint_id = str(uuid.uuid4())
            data = {
                "id": checkpoint_id, "version": VERSION, "revision": 1, "attempt_token": secrets.token_urlsafe(32),
                "lease_expires_at_ms": now + LEASE_MS, "loja": loja, "sku": request["sku"], "titulo": request["titulo"],
                "termos": [{"campo": term["campo"], "termo": term["termo"], "estado": "pendente", "confirmed_keys": [], "pending_keys": []} for term in request["termos"]],
                "opcoes": request["opcoes"], "candidatos": [], "resumo_coleta": [], "estado_coleta": "coletando", "estado_fechamento": "aberto",
                "parent_id": None, "root_id": checkpoint_id, "parent_history_entry_id": None, "history_entry_id": None,
                "created_at": datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(), "duracao_execucao_ms": 0,
                "retomado_por": None, "retomada_numero": 1, "snapshot_hash": None, "total_rankeados": None,
            }
            self.write(conn, data, request["request_id"], _hash(request))
            return self.full(data, token=True)

    def list(self, loja):
        with self.connection() as conn:
            result = []
            for row in conn.execute("SELECT payload_json FROM checkpoints ORDER BY updated_at_ms DESC"):
                data = json.loads(row[0])
                if data.get("loja") == loja and data.get("version") == VERSION:
                    result.append(metadata(data))
                    if len(result) >= MAX_CHECKPOINTS_LIST:
                        break
            return result

    def get(self, checkpoint_id, loja):
        with self.connection() as conn:
            return self.full(self.read(conn, checkpoint_id, loja))

    def update(self, checkpoint_id, request, loja):
        _bounded(request)
        with self.connection() as conn:
            data = self.read(conn, checkpoint_id, loja)
            if self.receipt(conn, data, "update", request):
                return self.full(data, token=True)
            self.guard(data, request["expected_revision"], request["attempt_token"])
            if data["estado_fechamento"] != "aberto" or data.get("retomado_por"):
                _fail("checkpoint_closed", "Checkpoint ja esta sendo finalizado ou retomado.")
            if request.get("termos") is not None:
                terms = copy.deepcopy(request["termos"])
                if [(term["campo"], term["termo"]) for term in terms] != [(term["campo"], term["termo"]) for term in data["termos"]]:
                    _fail("terms_mismatch", "Pesquisas diferem dos termos originais.", 422)
                for previous, term in zip(data["termos"], terms):
                    term["estado"] = term.get("estado", previous["estado"])
                    term["pending_keys"] = term.get("pending_keys", previous["pending_keys"])
                    if term["estado"] == "concluida":
                        term["pending_keys"] = []
                    term["confirmed_keys"] = list(dict.fromkeys(previous["confirmed_keys"] + term.get("confirmed_keys", [])))
                    if len(term["confirmed_keys"]) > 80:
                        _fail("invalid_payload", "Quantidade de chaves confirmadas excede o limite.", 422)
                    if previous["estado"] == "concluida" and term["estado"] != "concluida":
                        _fail("term_regression", "Pesquisa concluida nao pode voltar a pendente.", 422)
                data["termos"] = terms
            if request.get("candidatos") is not None:
                candidates = normalize_candidates(request["candidatos"], data["termos"], data["opcoes"]["max_anuncios"])
                before = {_candidate_key(item) for item in data["candidatos"]}
                if not before.issubset({_candidate_key(item) for item in candidates}):
                    _fail("candidate_loss", "Checkpoint nao pode descartar candidatos ja salvos.", 422)
                previous = {_candidate_key(item): item for item in data["candidatos"]}
                for item in candidates:
                    saved = previous.get(_candidate_key(item), {})
                    for field, value in saved.items():
                        if field in ("pesquisas_origem", "campos_origem"):
                            item[field] = list(dict.fromkeys(value + (item.get(field) or [])))
                        elif item.get(field) in (None, "", [], {}) and value not in (None, "", [], {}):
                            item[field] = copy.deepcopy(value)
                data["candidatos"] = normalize_candidates(candidates, data["termos"], data["opcoes"]["max_anuncios"])
            if request.get("resumo_coleta") is not None:
                data["resumo_coleta"] = self.normalize_summaries(request["resumo_coleta"])
            if request.get("duracao_execucao_ms") is not None:
                data["duracao_execucao_ms"] = max(data["duracao_execucao_ms"], request["duracao_execucao_ms"])
            if request.get("estado_coleta") is not None:
                data["estado_coleta"] = request["estado_coleta"]
            if data["estado_coleta"] == "concluida" and any(term["estado"] != "concluida" for term in data["termos"]):
                _fail("terms_incomplete", "Ainda existem pesquisas pendentes.", 422)
            self.keep_empty_collection_resumable(data)
            data["lease_expires_at_ms"] = int(time.time() * 1000) + LEASE_MS
            if data["estado_coleta"] in ("cancelada", "erro"):
                data["lease_expires_at_ms"] = 0
            data["revision"] += 1
            self.write(conn, data)
            self.record_receipt(conn, data, "update", request)
            return self.full(data, token=True)

    def resume(self, checkpoint_id, request, loja):
        with self.connection() as conn:
            parent = self.read(conn, checkpoint_id, loja)
            replay = self.receipt(conn, parent, "resume", request)
            if replay:
                return self.full(self.read(conn, replay, loja), token=True)
            self.guard(parent, request["expected_revision"], lease=False)
            if parent.get("retomado_por"):
                _fail("already_resumed", "Esta coleta ja tem uma retomada; abra a tentativa mais recente.")
            if parent["estado_fechamento"] == "fechamento_pendente":
                _fail("closing_pending", "Confirme o fechamento pendente antes de retomar.")
            if parent["estado_coleta"] == "concluida" and parent["estado_fechamento"] == "confirmado":
                _fail("already_complete", "Todas as pesquisas ja foram concluidas.")
            if parent["lease_expires_at_ms"] > int(time.time() * 1000):
                _fail("lease_active", "Esta coleta ainda tem uma tentativa ativa.")
            child = copy.deepcopy(parent)
            child["id"] = str(uuid.uuid4())
            child["parent_id"] = parent["id"]
            child["parent_history_entry_id"] = parent.get("history_entry_id") or parent.get("parent_history_entry_id")
            child["history_entry_id"] = None
            child["total_rankeados"] = None
            child["revision"] = 1
            child["retomada_numero"] = parent.get("retomada_numero", 1) + 1
            child["attempt_token"] = secrets.token_urlsafe(32)
            child["lease_expires_at_ms"] = int(time.time() * 1000) + LEASE_MS
            child["created_at"] = datetime.now(timezone.utc).isoformat()
            child["duracao_execucao_ms"] = 0
            child["estado_coleta"] = "coletando"
            child["estado_fechamento"] = "aberto"
            child["retomado_por"] = None
            child["snapshot_hash"] = None
            for field in ("closing_snapshot", "closing_request_hash", "closing_request_id"):
                child.pop(field, None)
            # Partial terms keep their captures; the fresh page supplies its own DOM positions.
            parent["retomado_por"] = child["id"]
            parent["revision"] += 1
            parent["lease_expires_at_ms"] = 0
            self.write(conn, parent)
            self.write(conn, child, "resume_" + child["id"], _hash(request))
            self.record_receipt(conn, parent, "resume", request, child["id"])
            return self.full(child, token=True)

    @staticmethod
    def keep_empty_collection_resumable(data):
        if data["estado_coleta"] == "concluida" and not data["candidatos"]:
            data["estado_coleta"] = "parcial"
            for term in data["termos"]:
                if term["estado"] == "concluida":
                    term["estado"] = "parcial"

    def finalize(self, checkpoint_id, request, loja, snapshot_factory):
        _bounded(request)
        # Phase one freezes the exact publication under an owner-scoped SQLite CAS.
        with self.connection() as conn:
            data = self.read(conn, checkpoint_id, loja)
            replay = data.get("closing_request_id") == request["request_id"]
            if replay and data.get("closing_request_hash") != _hash(request):
                _fail("idempotency_conflict", "Request de fechamento reutilizado com dados diferentes.")
            if data["estado_fechamento"] == "confirmado":
                if not replay:
                    self.guard(data, request["expected_revision"], lease=False)
                return {"checkpoint": self.full(data, token=replay), "historico_entry": copy.deepcopy(data.get("closing_snapshot")), "confirmed": True, "_newly_confirmed": False}
            if data["estado_fechamento"] == "fechamento_pendente":
                if not replay:
                    self.guard(data, request["expected_revision"], lease=False)
                    if not request.get("recuperar_fechamento"):
                        _fail("closing_pending", "Recupere o fechamento congelado antes de continuar.")
                    if data["lease_expires_at_ms"] > int(time.time() * 1000):
                        _fail("lease_active", "A tentativa de fechamento ainda esta ativa.")
            else:
                if request.get("recuperar_fechamento"):
                    _fail("no_closing_pending", "Nao ha fechamento pendente para recuperar.")
                if not request.get("attempt_token"):
                    _fail("attempt_conflict", "Token da tentativa obrigatorio.")
                self.guard(data, request["expected_revision"], request["attempt_token"])
                if data["estado_coleta"] in ("cancelada", "erro") or data.get("retomado_por"):
                    _fail("collection_stopped", "Coleta cancelada ou com erro nao pode concluir ranking.")
                if data["estado_coleta"] == "coletando":
                    data["estado_coleta"] = "concluida" if all(term["estado"] == "concluida" for term in data["termos"]) else "parcial"
                self.keep_empty_collection_resumable(data)
                data["history_entry_id"] = str(uuid.uuid4())
                data["closing_snapshot"] = snapshot_factory(data, request.get("grupo_ranking"))
                if data["closing_snapshot"] is None:
                    data["history_entry_id"] = None
                    data["total_rankeados"] = 0
                    data["estado_coleta"] = "parcial"
                else:
                    data["total_rankeados"] = sum(len(group.get("anuncios") or []) for group in data["closing_snapshot"].get("grupos") or [])
                data["snapshot_hash"] = _hash(data["closing_snapshot"])
                data["closing_request_id"] = request["request_id"]
                data["closing_request_hash"] = _hash(request)
                data["estado_fechamento"] = "fechamento_pendente"
                data["revision"] += 1
                self.write(conn, data)
            frozen = copy.deepcopy(data)
        # Publication is specific-ID and idempotent; a failure leaves phase one durable.
        try:
            entry = self.upsert_history(copy.deepcopy(frozen["closing_snapshot"])) if frozen["closing_snapshot"] else None
        except Exception as exc:
            # A known publication failure ends this lease; recovery may run immediately.
            # A process crash leaves the original lease intact until its bounded expiry.
            with self.connection() as conn:
                current = self.read(conn, checkpoint_id, loja)
                if current["estado_fechamento"] == "confirmado":
                    return {"checkpoint": self.full(current), "historico_entry": copy.deepcopy(current.get("closing_snapshot")), "confirmed": True, "_newly_confirmed": False}
                if current["snapshot_hash"] == frozen["snapshot_hash"] and current["lease_expires_at_ms"]:
                    current["lease_expires_at_ms"] = 0
                    current["revision"] += 1
                    self.write(conn, current)
                frozen = current
            raise HTTPException(status_code=503, detail={"code": "closing_pending", "message": "Fechamento salvo; o historico ainda precisa ser confirmado.", "checkpoint": metadata(frozen)}) from exc
        with self.connection() as conn:
            current = self.read(conn, checkpoint_id, loja)
            if current["snapshot_hash"] != frozen["snapshot_hash"]:
                _fail("snapshot_conflict", "Snapshot de fechamento mudou.")
            newly_confirmed = current["estado_fechamento"] != "confirmado"
            if newly_confirmed:
                current["estado_fechamento"] = "confirmado"
                current["lease_expires_at_ms"] = 0
                current["revision"] += 1
                self.write(conn, current)
            return {"checkpoint": self.full(current, token=bool(request.get("attempt_token"))), "historico_entry": entry, "confirmed": True, "_newly_confirmed": newly_confirmed}
