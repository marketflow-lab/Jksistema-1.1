"""Executor fechado e somente leitura para recursos oficiais do Mercado Livre."""

from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import Any, Optional

import requests
from fastapi import HTTPException

from backend.services.mercado_livre_query_catalog import (
    CATALOG_VERSION,
    MERCADO_LIVRE_QUERY_RESOURCE_BY_ID,
    MERCADO_LIVRE_QUERY_RESOURCES,
    mercado_livre_query_catalog_public,
)


API_BASE = "https://api.mercadolibre.com"
FUNCTION_NAME = "mercado_livre_resource_query"
_SERVER_PATH_PARAMS = {"user_id", "site_id", "billing_info_id"}
_SECRET_KEYS = {
    "access_token", "refresh_token", "authorization", "cookie", "set-cookie",
    "client_secret", "app_secret", "password", "token", "secret",
}
_PII_KEYS = {
    "buyer", "payer", "receiver_address", "billing_info", "identification",
    "phone", "alternative_phone", "email", "first_name", "last_name",
    "address", "address_line", "street_name", "street_number", "zip_code",
    "receiver_name", "document", "document_number", "tax_id",
    "business_name", "trade_name", "legal_name",
    "from", "players", "customer", "counterpart", "payer_id", "collector_id",
    "receiver_id",
    "additional_info", "taxpayer_id", "customer_document", "full_name", "doc_number",
}
_PRIVATE_KEYS = {
    "attachments", "attachment", "download_url", "secure_url", "private_url",
    "thumbnail", "body", "message", "messages", "text", "raw",
}
_URL_RE = re.compile(r"https?://|www\.", re.I)
_URL_VALUE_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[-.\s]?\d{4}(?!\d)")
_DOCUMENT_RE = re.compile(r"\b(?:\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\b")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_ITEM_ID_RE = re.compile(r"^[A-Z]{3}\d{5,}$")
_USER_PRODUCT_ID_RE = re.compile(r"^[A-Z]{3}U\d{5,}$")
_NUMERIC_ID_RE = re.compile(r"^\d{1,30}$")


def _base_result(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "function": FUNCTION_NAME,
        "arguments": arguments,
        "result": {
            "read_only": True,
            "catalog_version": CATALOG_VERSION,
            "sources": [],
            "warnings": [],
        },
    }


def _failure(arguments: dict[str, Any], code: str, message: str, **extra: Any) -> dict[str, Any]:
    output = _base_result(arguments)
    output["result"].update({
        "success": False,
        "found": False,
        "error": str(code or "query_error")[:80],
        "error_code": str(code or "query_error")[:80],
        "message": str(message or "Consulta indisponivel.")[:240],
        **extra,
    })
    return output


def _catalog_search(message: str, limit: int) -> dict[str, Any]:
    query = " ".join(re.findall(r"[a-z0-9_./-]+", str(message or "").casefold()))
    terms = [term for term in query.split() if len(term) >= 2][:12]
    ranked: list[tuple[int, dict[str, Any]]] = []
    for resource in MERCADO_LIVRE_QUERY_RESOURCES:
        haystack = " ".join(
            str(resource.get(key) or "")
            for key in ("resource_id", "domain", "path_template", "policy", "ownership_rule")
        ).casefold()
        score = sum(3 if term in str(resource.get("resource_id") or "").casefold() else 1 for term in terms if term in haystack)
        if not terms or score:
            ranked.append((score, resource))
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("domain") or ""), str(item[1].get("resource_id") or "")))
    public = mercado_livre_query_catalog_public()
    public["resources"] = [dict(resource) for _score, resource in ranked[: max(1, min(limit, 150))]]
    public["returned"] = len(public["resources"])
    public["query"] = query[:160]
    return public


def _safe_path_value(name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text or "/" in text or "\\" in text or ".." in text or _URL_RE.search(text):
        raise ValueError(f"Parametro de caminho invalido: {name}.")
    if name == "item_id" and not _ITEM_ID_RE.fullmatch(text.upper()):
        raise ValueError("item_id deve ser um MLB/MLA/... valido.")
    if name == "user_product_id" and not _USER_PRODUCT_ID_RE.fullmatch(text.upper()):
        raise ValueError("user_product_id invalido.")
    if name in {"order_id", "pack_id", "shipment_id", "claim_id", "return_id", "payment_id", "question_id"} and not _NUMERIC_ID_RE.fullmatch(text):
        raise ValueError(f"{name} deve ser numerico.")
    if not _SAFE_ID_RE.fullmatch(text):
        raise ValueError(f"Parametro de caminho invalido: {name}.")
    return text.upper() if name in {"item_id", "user_product_id", "category_id", "product_id", "country_id", "currency_id"} else text


def _safe_scalar(key: str, value: Any) -> Any:
    normalized_key = str(key or "").casefold()
    if isinstance(value, dict):
        raise ValueError(f"Parametro aninhado nao permitido: {key}.")
    if ("date" in normalized_key or normalized_key == "ending") and not isinstance(value, str):
        raise ValueError(f"Data invalida para {key}; use YYYY-MM-DD.")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if key == "limit":
            return max(1, min(int(value), 100))
        if key == "offset":
            return max(0, min(int(value), 1000))
        return value
    if isinstance(value, (list, tuple)):
        cap = 60 if key == "order_ids" else 20
        values = [_safe_scalar(key, item) for item in list(value)[:cap]]
        return ",".join(str(item) for item in values)
    text = str(value or "").strip()
    if len(text) > 500 or _URL_RE.search(text) or "\r" in text or "\n" in text:
        raise ValueError(f"Parametro de consulta invalido: {key}.")
    if key in {"ids", "order_ids"}:
        cap = 60 if key == "order_ids" else 20
        parts = [part.strip() for part in text.split(",") if part.strip()][:cap]
        if not parts or any(not _SAFE_ID_RE.fullmatch(part) for part in parts):
            raise ValueError(f"Lista de identificadores invalida: {key}.")
        return ",".join(parts)
    if "date" in normalized_key or normalized_key == "ending":
        try:
            parsed = date.fromisoformat(text[:10])
        except ValueError as exc:
            raise ValueError(f"Data invalida para {key}; use YYYY-MM-DD.") from exc
        age_days = (date.today() - parsed).days
        if age_days < -1 or age_days > 366:
            raise ValueError(f"Data fora da janela segura de 366 dias: {key}.")
    if normalized_key == "last":
        try:
            return max(1, min(int(text), 150))
        except ValueError as exc:
            raise ValueError("last deve ser numerico.") from exc
    return text


def _safe_query_params(resource: dict[str, Any], supplied: Any, seller_id: str, site_id: str) -> dict[str, Any]:
    if supplied is None:
        supplied = {}
    if not isinstance(supplied, dict):
        raise ValueError("params deve ser um objeto simples.")
    allowed = set(resource.get("allowed_query_params") or [])
    unknown = sorted(str(key) for key in supplied if str(key) not in allowed)
    if unknown:
        raise ValueError("Parametros nao permitidos para este recurso: " + ", ".join(unknown[:10]))
    params = {str(key): _safe_scalar(str(key), value) for key, value in supplied.items() if value is not None}
    params.update(dict(resource.get("fixed_query_params") or {}))
    resource_id = str(resource.get("resource_id") or "")
    if resource_id == "ml.products.search":
        params["site_id"] = site_id
    if resource_id == "ml.orders.search":
        params["seller"] = seller_id
    if resource_id == "ml.questions.search":
        params["seller_id"] = seller_id
    if resource_id == "ml.claims.search":
        params["players.user_id"] = seller_id
        params["players.role"] = "respondent"
    if resource_id == "ml.fulfillment.operations_search":
        params["seller_id"] = seller_id
    if "limit" in params:
        params["limit"] = max(1, min(int(params["limit"]), 100))
    if "offset" in params:
        params["offset"] = max(0, min(int(params["offset"]), 1000))
    return params


def _seller_from(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    seller = payload.get("seller") if isinstance(payload.get("seller"), dict) else {}
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    return str(payload.get("seller_id") or payload.get("user_id") or seller.get("id") or payload.get("sender_id") or sender.get("id") or "").strip()


def _claim_owned(payload: Any, seller_id: str) -> bool:
    if not isinstance(payload, dict):
        return False
    players = payload.get("players") if isinstance(payload.get("players"), list) else []
    for player in players:
        if not isinstance(player, dict):
            continue
        player_id = str(player.get("user_id") or player.get("id") or "").strip()
        role = str(player.get("role") or "").strip().casefold()
        if player_id == seller_id and role in {"respondent", "seller", "vendedor"}:
            return True
    return False


def _response_json(response: Any) -> Any:
    try:
        return response.json()
    except Exception:
        return {}


def _redact_text(value: str) -> str:
    text = str(value or "")[:2000]
    text = _EMAIL_RE.sub("[email removido]", text)
    text = _PHONE_RE.sub("[telefone removido]", text)
    text = _DOCUMENT_RE.sub("[documento removido]", text)
    text = _URL_VALUE_RE.sub("[url removida]", text)
    return text


def _sanitize(value: Any, *, sensitive: bool, depth: int = 0) -> Any:
    if depth >= 7:
        return "[conteudo reduzido]"
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:200]:
            key = str(raw_key or "")[:100]
            normalized = key.casefold()
            secret_like = any(marker in normalized for marker in ("token", "secret", "authorization", "password", "cookie"))
            pii_like = any(marker in normalized for marker in (
                "taxpayer", "tax_id", "document_number", "customer_document", "doc_number",
                "full_name", "first_name", "last_name", "email", "phone", "address",
                "identification", "receiver_name",
            ))
            if normalized in _SECRET_KEYS or secret_like or normalized in _PII_KEYS or (sensitive and (normalized in _PRIVATE_KEYS or pii_like)):
                continue
            clean[key] = _sanitize(raw_value, sensitive=sensitive, depth=depth + 1)
        return clean
    if isinstance(value, list):
        return [_sanitize(item, sensitive=sensitive, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return _redact_text(value)[:1000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value))[:500]


def _bounded_payload(value: Any) -> Any:
    clean = value
    try:
        encoded = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return {}
    if len(encoded.encode("utf-8")) <= 240_000:
        return clean
    def compact_preview(item: Any, depth: int = 0) -> Any:
        if depth >= 4:
            return "[conteudo reduzido]"
        if isinstance(item, dict):
            return {
                str(key)[:80]: compact_preview(child, depth + 1)
                for key, child in list(item.items())[:30]
            }
        if isinstance(item, list):
            return [compact_preview(child, depth + 1) for child in item[:10]]
        if isinstance(item, str):
            return item[:300]
        return item if item is None or isinstance(item, (bool, int, float)) else str(item)[:160]

    preview = compact_preview(clean)
    if isinstance(preview, dict):
        preview["_truncated"] = True
    else:
        preview = {"preview": preview, "_truncated": True}
    try:
        if len(json.dumps(preview, ensure_ascii=False).encode("utf-8")) <= 240_000:
            return preview
    except Exception:
        pass
    return {"_truncated": True, "message": "Resposta reduzida por exceder o limite seguro."}


def _http_failure(response: Any) -> tuple[str, str, bool]:
    status = int(getattr(response, "status_code", 0) or 0)
    if status == 401:
        return "reconnect_required", "A autenticacao do Mercado Livre precisa ser refeita para esta loja.", True
    if status == 403:
        return "resource_forbidden", "A conta nao possui permissao para consultar este recurso.", False
    if status == 404:
        return "resource_not_found", "O recurso nao foi encontrado na loja escolhida.", False
    if status == 429:
        return "rate_limited", "O Mercado Livre limitou temporariamente as consultas. Tente novamente mais tarde.", False
    return f"http_{status or 'error'}", "O Mercado Livre nao concluiu esta consulta.", False


def _delegated_query(
    client_id: str,
    resource: dict[str, Any],
    message: str,
    loja: str,
    path_values: dict[str, str],
    params: dict[str, Any],
    limit: int,
    deadline: Optional[float],
) -> dict[str, Any]:
    from backend.services import codex_readonly_sources
    from backend.services.marketplace_tools import listings, orders, promotions, traffic

    tool = str(resource.get("delegated_tool") or "")
    if tool == "mercado_livre_listing":
        return listings.query(
            client_id, message, loja=loja, limite=limit,
            item_id=path_values.get("item_id", ""),
            incluir_descricao=str(resource.get("resource_id")) == "ml.items.description",
            incluir_detalhes=True, query_deadline=deadline,
        )
    if tool == "mercado_livre_visits":
        return traffic.query_visits(
            client_id, message, loja=loja, item_id=path_values.get("item_id", ""),
            dias=int(params.get("last") or 30), query_deadline=deadline,
        )
    if tool == "mercado_livre_promotions":
        return promotions.query(
            client_id, message, loja=loja, limite=min(limit, 50), query_deadline=deadline,
        )
    if tool == "questions_post_sale_query":
        return codex_readonly_sources.questions_post_sale_query(
            client_id=client_id, message=message, loja=loja,
            status=str(params.get("status") or ""), limit=limit, all_stores=False,
        )
    if tool == "mercado_livre_post_sale_detail":
        return codex_readonly_sources.mercado_livre_post_sale_detail(
            client_id=client_id, message=message, loja=loja,
            pack_id=path_values.get("pack_id", ""), limit=limit,
        )
    if tool == "mercado_livre_orders":
        identifier = path_values.get("order_id") or path_values.get("pack_id") or ""
        return orders.query(
            client_id, message, loja=loja, id_pedido=identifier,
            status=str(params.get("order.status") or "paid,partially_refunded"),
            offset=int(params.get("offset") or 0), limite=limit, query_deadline=deadline,
        )
    return _failure({}, "delegated_tool_unavailable", "A consulta especializada nao esta disponivel.")


def execute_mercado_livre_query(
    client_id: str,
    message: str = "",
    loja: Optional[str] = None,
    resource_id: str = "",
    params: Optional[dict[str, Any]] = None,
    limit: int = 50,
    query_deadline: Optional[float] = None,
) -> dict[str, Any]:
    """Executa um recurso catalogado; URL, metodo e identidade nunca sao livres."""

    rid = str(resource_id or "").strip()
    arguments = {
        "resource_id": rid,
        "loja": str(loja or "").strip(),
        "parameter_count": min(len(params), 40) if isinstance(params, dict) else 0,
        "limit": max(1, min(int(limit or 50), 100)),
    }
    if not rid:
        output = _base_result(arguments)
        output["result"].update(_catalog_search(message, arguments["limit"]))
        return output
    if "/" in rid or "\\" in rid or _URL_RE.search(rid):
        return _failure(arguments, "free_url_blocked", "Use somente um resource_id exato do catalogo oficial.")
    resource = MERCADO_LIVRE_QUERY_RESOURCE_BY_ID.get(rid)
    if not resource:
        return _failure(arguments, "resource_not_catalogued", "resource_id nao encontrado no catalogo oficial.")
    policy = str(resource.get("policy") or "")
    if policy == "tombstone":
        return _failure(
            arguments, "resource_deprecated", "Este endpoint foi removido e permanece apenas como referencia historica.",
            resource=dict(resource), deprecated_at=resource.get("deprecated_at") or "",
        )
    if policy == "document_only":
        return _failure(
            arguments, "resource_document_only", "Endpoint documentado, mas nao liberado para execucao pelo assistente interno.",
            resource=dict(resource),
        )

    from backend.services import mercadolivre_legacy_api
    from backend.services.marketplace_tools import client as marketplace_client
    from backend.services.marketplace_tools import listing_search

    store, store_failure = marketplace_client.resolve_store(client_id, loja)
    if not store:
        return _failure(arguments, str(store_failure.get("code") or "store_required"), str(store_failure.get("message") or "Informe uma loja exata."), available_stores=list(store_failure.get("available_stores") or []))
    deadline = time.monotonic() + 30
    if query_deadline is not None:
        try:
            deadline = min(deadline, float(query_deadline))
        except (TypeError, ValueError):
            pass

    try:
        cfg = mercadolivre_legacy_api._obter_cfg_ml(client_id, store)
        seller_id = str((cfg or {}).get("user_id") or "").strip()
        site_id = str((cfg or {}).get("site_id") or "MLB").strip().upper()
        if not seller_id:
            return _failure(arguments, "seller_id_missing", "A loja nao possui vendedor confirmado pelo OAuth.")
        supplied = dict(params or {}) if isinstance(params, dict) else {}
        ownership = str(resource.get("ownership_rule") or "public")
        billing_order_id = supplied.pop("order_id", "") if ownership == "billing_from_order" else ""
        path_values: dict[str, str] = {"user_id": seller_id, "site_id": site_id}
        for name in resource.get("required_path_params") or []:
            if name in _SERVER_PATH_PARAMS:
                continue
            path_values[name] = _safe_path_value(name, supplied.pop(name, ""))
        safe_params = _safe_query_params(resource, supplied, seller_id, site_id)

        if policy == "delegated":
            raw = _delegated_query(client_id, resource, message, store, path_values, safe_params, arguments["limit"], deadline)
            clean = _bounded_payload(_sanitize(raw, sensitive=rid.startswith(("ml.orders", "ml.packs", "ml.messages", "ml.questions"))))
            if isinstance(clean, dict):
                clean["catalog_resource"] = {key: resource.get(key) for key in ("resource_id", "domain", "method", "path_template", "official_source")}
            return clean if isinstance(clean, dict) else _failure(arguments, "invalid_delegated_response", "A consulta especializada retornou formato invalido.")

        preflight_payload: Any = None
        preflight_path = ""
        if ownership == "owned_item":
            item, cfg, failure = listing_search.resolve_owned_item(client_id, store, cfg, path_values.get("item_id", ""), deadline=deadline)
            if failure:
                return _failure(arguments, str(failure.get("code") or "ownership_denied"), str(failure.get("message") or "Propriedade nao confirmada."))
            preflight_payload = item
        elif ownership == "owned_user_product":
            preflight_path = f"/user-products/{path_values.get('user_product_id', '')}"
        elif ownership == "owned_family":
            preflight_path = f"/sites/{site_id}/user-products-families/{path_values.get('family_id', '')}"
        elif ownership == "owned_order" or ownership == "billing_from_order":
            order_id = path_values.get("order_id") or str(billing_order_id or "").strip()
            order_id = _safe_path_value("order_id", order_id)
            path_values["order_id"] = order_id
            preflight_path = f"/orders/{order_id}"
        elif ownership == "owned_pack":
            preflight_path = f"/packs/{path_values.get('pack_id', '')}"
        elif ownership == "owned_shipment":
            preflight_path = f"/shipments/{path_values.get('shipment_id', '')}"
        elif ownership == "owned_claim":
            preflight_path = f"/post-purchase/v1/claims/{path_values.get('claim_id', '')}"

        if preflight_path:
            preflight_response, cfg = marketplace_client.request_get(
                client_id, store, cfg, API_BASE + preflight_path,
                headers={"x-format-new": "true"} if ownership == "owned_shipment" else None,
                timeout=15, deadline=deadline,
            )
            if int(getattr(preflight_response, "status_code", 0) or 0) not in {200, 206}:
                code, msg, reconnect = _http_failure(preflight_response)
                return _failure(arguments, code, msg, reconnect_required=reconnect)
            preflight_payload = _response_json(preflight_response)
            if ownership == "owned_claim":
                owned = _claim_owned(preflight_payload, seller_id)
            elif ownership == "owned_pack":
                pack_orders = preflight_payload.get("orders") if isinstance(preflight_payload, dict) and isinstance(preflight_payload.get("orders"), list) else []
                owned = bool(pack_orders)
                for linked in pack_orders[:20]:
                    linked_id = str(linked.get("id") or "").strip() if isinstance(linked, dict) else ""
                    if not _NUMERIC_ID_RE.fullmatch(linked_id):
                        owned = False
                        break
                    linked_response, cfg = marketplace_client.request_get(
                        client_id, store, cfg, API_BASE + f"/orders/{linked_id}",
                        timeout=15, deadline=deadline,
                    )
                    if int(getattr(linked_response, "status_code", 0) or 0) not in {200, 206} or _seller_from(_response_json(linked_response)) != seller_id:
                        owned = False
                        break
            else:
                owned = _seller_from(preflight_payload) == seller_id
            if not owned:
                return _failure(arguments, "ownership_denied", "O recurso nao pertence, ou nao pode ser confirmado, na loja escolhida.")

        if ownership == "billing_from_order":
            buyer = preflight_payload.get("buyer") if isinstance(preflight_payload, dict) and isinstance(preflight_payload.get("buyer"), dict) else {}
            billing = buyer.get("billing_info") if isinstance(buyer.get("billing_info"), dict) else {}
            billing_id = str(buyer.get("billing_info_id") or billing.get("id") or "").strip()
            if not billing_id:
                return _failure(arguments, "billing_info_unavailable", "O pedido nao informou um billing_info_id consultavel.")
            path_values["billing_info_id"] = _safe_path_value("billing_info_id", billing_id)

        path = str(resource.get("path_template") or "")
        for name in resource.get("required_path_params") or []:
            value = path_values.get(name)
            if value is None:
                return _failure(arguments, "path_parameter_missing", f"Parametro obrigatorio ausente: {name}.")
            path = path.replace("{" + name + "}", str(value))
        if "{" in path or not path.startswith("/") or ".." in path or _URL_RE.search(path):
            return _failure(arguments, "path_materialization_failed", "O caminho oficial nao pode ser materializado com seguranca.")

        final_payload = preflight_payload if preflight_path == path and not safe_params else None
        status = 200
        selected_response_headers: dict[str, str] = {}
        if final_payload is None:
            response, cfg = marketplace_client.request_get(
                client_id, store, cfg, API_BASE + path, params=safe_params,
                headers=dict(resource.get("required_headers") or {}), timeout=20, deadline=deadline,
            )
            status = int(getattr(response, "status_code", 0) or 0)
            if status not in {200, 206}:
                code, msg, reconnect = _http_failure(response)
                return _failure(arguments, code, msg, reconnect_required=reconnect)
            final_payload = _response_json(response)
            response_headers = getattr(response, "headers", {}) or {}
            for header_name in resource.get("response_headers") or []:
                header_value = str(response_headers.get(header_name) or response_headers.get(str(header_name).title()) or "").strip()
                if header_value and len(header_value) <= 160:
                    selected_response_headers[str(header_name).lower()] = header_value

        sensitive = policy == "allowlisted_redacted" or str(resource.get("domain") or "") in {"orders", "shipments", "messages", "claims", "returns", "billing", "invoices", "users"}
        clean_payload = _bounded_payload(_sanitize(final_payload, sensitive=sensitive))
        locally_truncated = bool(isinstance(clean_payload, dict) and clean_payload.get("_truncated") is True)
        rows = clean_payload if isinstance(clean_payload, list) else (
            clean_payload.get("results") if isinstance(clean_payload, dict) and isinstance(clean_payload.get("results"), list) else []
        )
        output = _base_result(arguments)
        output["result"].update({
            "success": True,
            "found": bool(clean_payload not in ({}, [], None)),
            "store": store,
            "resource": {key: resource.get(key) for key in ("resource_id", "domain", "method", "path_template", "policy", "ownership_rule", "official_source", "last_verified_at")},
            "data": clean_payload,
            "rows": rows[:100] if isinstance(rows, list) else [],
            "records": len(rows) if isinstance(rows, list) else (1 if clean_payload else 0),
            "partial_response": status == 206,
            "coverage_complete": status == 200 and not locally_truncated,
            "truncated": locally_truncated,
            "response_headers": selected_response_headers,
            "sources": [{"provider": "mercado_livre", "resource": str(resource.get("resource_id") or ""), "method": "GET", "store": store}],
        })
        if status == 206:
            output["result"]["warnings"].append("O Mercado Livre devolveu resposta parcial (HTTP 206).")
        if locally_truncated:
            output["result"]["warnings"].append("A resposta foi reduzida por exceder o limite seguro de tamanho.")
        return output
    except ValueError as exc:
        return _failure(arguments, "invalid_parameters", str(exc))
    except requests.exceptions.Timeout:
        return _failure(arguments, "timeout", "A consulta excedeu o limite seguro de tempo.")
    except HTTPException as exc:
        reconnect = int(getattr(exc, "status_code", 0) or 0) == 401
        return _failure(arguments, "reconnect_required" if reconnect else "integration_error", "A integracao do Mercado Livre nao esta disponivel para esta loja.", reconnect_required=reconnect)
    except Exception:
        return _failure(arguments, "query_failed", "A consulta nao foi concluida com seguranca.")


__all__ = ["execute_mercado_livre_query"]
