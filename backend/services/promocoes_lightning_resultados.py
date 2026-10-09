"""Persist and refresh server-owned LIGHTNING outcomes without resubmitting offers."""
from __future__ import annotations

import copy
import re
import threading
import time
import uuid

from fastapi import HTTPException

_KIND = "lightning_participation_confirmation"
_LOCK = threading.RLock()


def _summary(result):
    result = copy.deepcopy(result)
    details = result.get("detalhes", [])
    for detail in details:
        outcome = detail.get("outcome")
        detail["success"] = outcome in {"applied", "already_participating"}
        detail["ignored"] = outcome == "already_participating"
    result["total_sucesso"] = sum(d.get("outcome") == "applied" for d in details)
    result["total_ignorados"] = sum(d.get("outcome") == "already_participating" for d in details)
    result["total_falha"] = len(details) - result["total_sucesso"] - result["total_ignorados"]
    result["total_itens"] = len(details)
    result["success"] = result["total_falha"] == 0
    for group in result.get("promocoes", []):
        selected = [d for d in details if d.get("promotion_id") == group.get("promotion_id")]
        group["sucesso"] = sum(d.get("outcome") == "applied" for d in selected)
        group["ignorados"] = sum(d.get("outcome") == "already_participating" for d in selected)
        group["falhas"] = len(selected) - group["sucesso"] - group["ignorados"]
        group["erros"] = [f"{d.get('item_id') or d.get('client_ref')}: {d.get('message', '')}"
                          for d in selected if not d["success"]][:8]
    return result


def register_confirmation(service, client_id, loja, cfg, analysis_job_id, result, pending):
    """Only contexts captured by the server immediately before a POST are eligible."""
    from .promocoes_lightning_confirmacao import issue_confirmation_context, update_confirmation_context
    seller = str(cfg.get("user_id") or cfg.get("seller_id") or "").strip()
    if not pending or not seller:
        return result
    analysis_job_id = str(analysis_job_id or uuid.uuid4().hex)
    job = {"client_id": client_id, "loja": loja, "seller_id": seller, "kind": _KIND,
           "created_at": time.time(), "analysis_job_id": str(analysis_job_id or "")}
    eligible = {(d["client_ref"], d["promotion_id"], d["item_id"]): d
                for d in result["detalhes"] if d.get("outcome") == "unknown"}
    for entry in pending:
        key = (entry["client_ref"], entry["campaign"], entry["item_id"])
        if key not in eligible:
            continue
        try:
            issue_confirmation_context(job, client_id, loja, seller, str(analysis_job_id or ""),
                entry["client_ref"], entry["campaign"], entry["item_id"], entry["raw"], entry["price"])
            update_confirmation_context(job, client_id, loja, seller, entry["client_ref"],
                submission_attempted=True, outcome="unknown")
        except ValueError:
            continue
    if not job.get("lightning_confirmation_contexts"):
        return result
    confirmation_id = uuid.uuid4().hex
    result = copy.deepcopy(result)
    result.update(confirmation_id=confirmation_id, confirmation_pending=True)
    job["result"] = copy.deepcopy(result)
    service._promo_job_set(confirmation_id, **job)
    # The rest of the batch may have allowed the provider's read model to catch up.
    # Later browser/job polls keep using the same read-only operation.
    try:
        return refresh_confirmation(service, confirmation_id, client_id, loja=loja)
    except Exception:
        # A failed read must not erase completed sends or the durable handle.
        return result


def refresh_confirmation(service, confirmation_id, client_id, *, loja=None, budget_seconds=6.0):
    from .promocoes_lightning_confirmacao import (
        resolve_confirmation_context, update_confirmation_context, reconcile_lightning_participation,
    )
    if not re.fullmatch(r"[a-f0-9]{32}", str(confirmation_id or "")):
        raise HTTPException(status_code=404, detail="Confirmacao de participacao nao encontrada.")
    with _LOCK:
        job = copy.deepcopy(service._promo_job_get(confirmation_id))
    if job.get("kind") != _KIND or job.get("client_id") != client_id:
        raise HTTPException(status_code=404, detail="Confirmacao de participacao nao encontrada.")
    store = str(job.get("loja") or "")
    if loja is not None and str(loja).strip() != store:
        raise HTTPException(status_code=409, detail="A confirmacao pertence a outra loja.")
    cfg = service._obter_cfg_ml(client_id, store)
    seller = str(cfg.get("user_id") or cfg.get("seller_id") or "").strip()
    if not seller or seller != job.get("seller_id"):
        raise HTTPException(status_code=409, detail="A conta da loja mudou desde o envio.")
    contexts = job.get("lightning_confirmation_contexts") or {}
    deadline = time.monotonic() + max(0.0, min(float(budget_seconds), 12.0))
    observations = []
    ordered = sorted(contexts, key=lambda ref: contexts[ref].get("last_checked_at", 0))
    for ref in ordered:
        try:
            record = resolve_confirmation_context(job, client_id, store, seller, ref)
        except ValueError as exc:
            raise HTTPException(status_code=410, detail="A consulta de confirmacao expirou. Execute uma nova analise.") from exc
        if not record.get("pollable"):
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        matched, cfg, metadata = reconcile_lightning_participation(service, client_id, store, cfg,
            campaign=record["campaign"], item_id=record["item_id"], raw=record["raw"],
            price=record["price_cents"] / 100, max_reads=1, timeout_seconds=remaining)
        observations.append((ref, matched, metadata))
    with _LOCK:
        latest = copy.deepcopy(service._promo_job_get(confirmation_id))
        if latest.get("kind") != _KIND or latest.get("client_id") != client_id or latest.get("seller_id") != seller or latest.get("loja") != store:
            raise HTTPException(status_code=409, detail="O contexto da confirmacao mudou.")
        for ref, matched, metadata in observations:
            record = resolve_confirmation_context(latest, client_id, store, seller, ref)
            if record.get("confirmed"):
                continue
            if matched:
                update_confirmation_context(latest, client_id, store, seller, ref,
                    outcome="applied", matched=matched)
            current = latest["lightning_confirmation_contexts"][ref]
            current["last_checked_at"] = time.time()
            current["check_count"] = int(current.get("check_count", 0)) + int(metadata.get("read_count", 0))
        result = copy.deepcopy(latest["result"])
        for detail in result["detalhes"]:
            record = latest["lightning_confirmation_contexts"].get(detail["client_ref"])
            if not record or (detail["promotion_id"], detail["item_id"]) != (record["campaign"], record["item_id"]):
                continue
            diagnostic = detail.setdefault("price_diagnostics", {})
            diagnostic["confirmation_check_count"] = int(record.get("check_count", 0))
            confirmed = record.get("confirmed")
            if confirmed:
                detail.update(confirmed, outcome="applied", success=True, ignored=False,
                    message="Participacao confirmada pelo Mercado Livre em consulta posterior.")
                detail.pop("error", None)
                diagnostic["decision_code"] = "PARTICIPATION_CONFIRMED_BY_REFRESH"
                detail.setdefault("analysis_row_updates", {})["action_ja_participa"] = True
            elif detail.get("outcome") == "unknown":
                detail["message"] = "Envio realizado; aguardando confirmacao do Mercado Livre. A verificacao consulta o resultado sem reenviar a inscricao."
                detail["error"] = detail["message"]
        result = _summary(result)
        result["confirmation_pending"] = any(not record.get("confirmed") for record in latest["lightning_confirmation_contexts"].values())
        result["confirmation_id"] = confirmation_id
        latest["result"] = copy.deepcopy(result)
        service._promo_job_set(confirmation_id, **latest)
        return result
