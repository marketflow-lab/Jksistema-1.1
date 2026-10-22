"""Resolve explicit Bling document links without changing the sales collector."""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import requests

from backend.services.bling import _bling_get_with_adaptive_limit
from backend.services.bling_oauth import requires_bling_reauthorization
from backend.services.bling_return_references import document_number, explicit_reason, extract_reference, numeric_id

from .legacy import _bling_executar_com_refresh, buscar_loja


_BASE = "https://api.bling.com.br/Api/v3"


class _Stop(Exception):
    def __init__(self, code, *, retry=False, after=0, status="unavailable"):
        self.code, self.retry, self.after, self.status = code, retry, after, status


def _result(status="not_linked", code=""):
    return {"result_status": status, "error_code": code, "retryable": False,
            "retry_after_seconds": 0, "reason_id": "", "reason_description": "",
            "reason_source": "bling", "order_id": ""}


class _Lookup:
    def __init__(self, client_id, store_id, loja, config):
        self.client_id, self.store_id, self.loja, self.config = client_id, store_id, loja, config
        self.deadline = time.monotonic() + 25

    def get(self, path, *, params=None):
        if time.monotonic() >= self.deadline:
            raise _Stop("bling_timeout", retry=True, after=60)
        def operation(token):
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise _Stop("bling_timeout", retry=True, after=60)
            response = _bling_get_with_adaptive_limit(
                _BASE + path, headers={"Authorization": f"Bearer {token}"}, params=params,
                timeout=(3.05, min(8, remaining)), max_attempts=1, total_timeout=min(10, remaining),
            )
            if response is None:
                raise _Stop("bling_unavailable", retry=True, after=60)
            target = urlsplit(str(getattr(response, "url", "")))
            if (target.scheme != "https" or target.hostname != "api.bling.com.br"
                    or target.path != "/Api/v3" + path or target.username or target.password
                    or target.port not in (None, 443)):
                raise _Stop("bling_unexpected_destination")
            status = int(response.status_code)
            if status == 401:
                return None, status
            if status == 429:
                delay = str((getattr(response, "headers", {}) or {}).get("Retry-After", "60"))
                raise _Stop("bling_rate_limit", retry=True, after=min(3600, max(5, int(delay))) if delay.isdigit() else 60)
            if status >= 500:
                raise _Stop("bling_unavailable", retry=True, after=60)
            if status != 200:
                raise _Stop("bling_not_found" if status == 404 else "bling_forbidden")
            body = response.json()
            if not isinstance(body, dict) or "data" not in body:
                raise _Stop("bling_incomplete_response", retry=True, after=60)
            return body["data"], status

        data, status, config = _bling_executar_com_refresh(
            self.client_id, self.loja, self.config, operation, store_id=self.store_id,
        )
        self.config = config
        if status == 401:
            raise _Stop("bling_reconnect_required", status="reconnect_required", after=900)
        store = buscar_loja(self.client_id, self.loja, store_id=self.store_id)
        if (not store or str(store.get("store_id") or "") != self.store_id
                or dict((store.get("integracoes") or {}).get("bling") or {}) != config):
            raise _Stop("bling_identity_changed", retry=True, after=60)
        return data

    def document(self, resource, identifier):
        result = self.get(f"/{resource}/{identifier}")
        if not isinstance(result, dict) or numeric_id(result.get("id")) != identifier:
            raise _Stop("bling_document_mismatch")
        return result

    def by_number(self, resource, number):
        params = {"numero": number, "limite": 2, "pagina": 1}
        if resource == "nfe":
            params["tipo"] = 1
        rows = self.get("/" + resource, params=params)
        if not isinstance(rows, list):
            raise _Stop("bling_incomplete_response", retry=True, after=60)
        if len(rows) > 1:
            raise _Stop("bling_ambiguous_document", status="ambiguous")
        if not rows:
            return None
        row = rows[0]
        if (not isinstance(row, dict) or document_number(row.get("numero")) != number
                or not numeric_id(row.get("id"))
                or (resource == "nfe" and str(row.get("tipo")) != "1")):
            raise _Stop("bling_document_mismatch")
        detail = self.document(resource, numeric_id(row["id"]))
        if (document_number(detail.get("numero")) != number
                or (resource == "nfe" and str(detail.get("tipo")) != "1")):
            raise _Stop("bling_document_mismatch")
        return detail


def resolve_reference(client_id: str, store_id: str, loja: str, job: dict) -> dict:
    """Return a documented shop order ID or an explicitly labelled Bling reason."""
    result = _result()
    try:
        if job.get("store_id") and str(job["store_id"]) != store_id:
            return _result("unavailable", "store_identity_mismatch")
        if not store_id or not numeric_id(job.get("nota_id")):
            return result
        store = buscar_loja(client_id, loja, store_id=store_id)
        if not store or str(store.get("store_id") or "") != store_id:
            return _result("unavailable", "store_identity_mismatch")
        config = dict((store.get("integracoes") or {}).get("bling") or {})
        if requires_bling_reauthorization(config):
            return _result("reconnect_required", "bling_reconnect_required")
        if not config.get("access_token"):
            return _result("unavailable", "bling_not_connected")
        lookup = _Lookup(client_id, store_id, loja, config)
        document = lookup.document("nfe", numeric_id(job["nota_id"]))
        if str(document.get("tipo")) != "0":
            return _result("unavailable", "bling_not_inbound_invoice")
        reference = extract_reference(document)
        if reference.get("reference_conflict"):
            return _result("ambiguous", "bling_conflicting_reference")
        if numeric_id(job.get("order_id")) and reference.get("order_id") != numeric_id(job["order_id"]):
            return _result("ambiguous", "bling_order_reference_changed")
        reason = explicit_reason(document, str(job.get("sku") or ""))
        if not reference.get("order_id"):
            linked = None
            if reference.get("bling_order_id"):
                linked = lookup.document("pedidos/vendas", reference["bling_order_id"])
            elif reference.get("bling_order_number"):
                linked = lookup.by_number("pedidos/vendas", reference["bling_order_number"])
            elif reference.get("original_invoice_number"):
                linked = lookup.by_number("nfe", reference["original_invoice_number"])
            if linked:
                linked_reference = extract_reference(linked)
                if linked_reference.get("reference_conflict"):
                    return _result("ambiguous", "bling_conflicting_reference")
                reference.update(linked_reference)
                reason = reason or explicit_reason(linked, str(job.get("sku") or ""))
        result["order_id"] = reference.get("order_id", "")
        if reason:
            result.update(result_status="identified", reason_id="explicit_text", reason_description=reason)
        return result
    except _Stop as exc:
        result.update(result_status=exc.status, error_code=exc.code,
                      retryable=exc.retry, retry_after_seconds=exc.after)
    except requests.exceptions.Timeout:
        result.update(result_status="unavailable", error_code="bling_timeout", retryable=True, retry_after_seconds=60)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status == 401:
            result.update(result_status="reconnect_required", error_code="bling_reconnect_required", retry_after_seconds=900)
        elif status in {403, 404, 409}:
            result.update(result_status="unavailable", error_code="bling_access_unavailable")
        else:
            result.update(result_status="unavailable", error_code="bling_lookup_failed", retryable=True, retry_after_seconds=60)
    return result
