"""Fixed-host provider transport. No redirects or automatic mutation retries."""
from __future__ import annotations

import base64
import json
import re
import time
from urllib.parse import unquote, urlsplit

import requests

from .central_accounts import CentralError


BASES = {"mercadolivre": "https://api.mercadolibre.com",
         "bling": "https://api.bling.com.br/Api/v3"}
RESOURCES = {
    "mercadolivre": {"items", "users", "orders", "shipments", "questions", "answers", "messages",
                     "packs", "payments", "inventories", "stock", "seller-promotions", "sites",
                     "categories", "domains", "products", "pictures", "pricing-automation",
                     "moderations", "claims", "post-purchase", "myfeeds", "trends", "highlights", "user-products"},
    "bling": {"produtos", "pedidos", "nfe", "nfce", "estoques", "depositos", "canais-venda",
              "configuracoes", "naturezas-operacoes", "contatos", "categorias", "logisticas",
              "formas-pagamentos", "situacoes", "campos-customizados"},
}
FORBIDDEN_KEYS = {"access_token", "refresh_token", "client_secret", "authorization", "password"}
MAX_BYTES = 8 * 1024 * 1024
SAFE_HEADERS = {"x-format-new", "x-version", "if-match", "if-none-match"}


def assert_provider_permission(principal, payload):
    if principal.permissions.get("full"):
        return
    resource = unquote(payload.path).split("/")[1]
    permissions = {
        "orders": {"vendas", "etiquetas"}, "pedidos": {"vendas", "etiquetas"},
        "nfe": {"vendas", "etiquetas", "impostos"}, "nfce": {"vendas", "impostos"},
        "shipments": {"vendas", "etiquetas", "perguntas_pos_venda", "mercado_full"},
        "packs": {"vendas", "etiquetas", "perguntas_pos_venda"}, "payments": {"vendas", "impostos"},
        "questions": {"perguntas_pos_venda"}, "answers": {"perguntas_pos_venda"},
        "messages": {"perguntas_pos_venda"}, "claims": {"perguntas_pos_venda"},
        "post-purchase": {"perguntas_pos_venda"},
        "inventories": {"estoque", "mercado_full"}, "stock": {"estoque", "mercado_full"},
        "user-products": {"estoque", "anuncios_ml", "mercado_full"},
        "estoques": {"estoque", "cadastro", "vendas"}, "depositos": {"estoque", "cadastro", "vendas"},
        "seller-promotions": {"analise_promo", "renovacao_fixa", "favoritos"},
        "pricing-automation": {"analise_promo", "renovacao_fixa", "favoritos", "anuncios_ml"},
    }.get(resource, {"cadastro", "anuncios_ml", "favoritos", "analise_promo", "estoque", "vendas", "integracao"})
    if payload.method != "GET" and resource in {"items", "produtos", "products", "pictures"}:
        permissions = {"cadastro", "anuncios_ml", "favoritos"}
    if not any(principal.permissions.get(permission) for permission in permissions):
        raise CentralError("central_permission_denied", 403)


def _has_secret(value):
    if isinstance(value, dict):
        return any(str(k).lower() in FORBIDDEN_KEYS or _has_secret(v) for k, v in value.items())
    return isinstance(value, list) and any(_has_secret(v) for v in value)


class ProviderTransport:
    def __init__(self, session_factory=requests.Session, clock=time.time):
        self.session_factory, self.clock = session_factory, clock

    @staticmethod
    def validate(payload, connection):
        path = payload.path
        parsed = urlsplit(path)
        decoded = unquote(path)
        if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not path.startswith("/")
                or "\\" in decoded or "%" in decoded or "//" in decoded
                or any(p in (".", "..") for p in decoded.split("/"))
                or any(ord(c) < 32 for c in decoded)
                or decoded.split("/")[1] not in RESOURCES[payload.provider]):
            raise CentralError("central_endpoint_denied", 400)
        if _has_secret(payload.params) or _has_secret(payload.body):
            raise CentralError("central_credential_argument_denied", 400)
        resource = decoded.split("/")[1]
        if resource == "users" and (payload.method != "GET" or not re.fullmatch(
                r"/users/(?:me|[0-9]+)(?:/items/search|/shipping_options/free)?", decoded)):
            raise CentralError("central_endpoint_denied", 400)
        if resource == "configuracoes" and (payload.method != "GET" or decoded != "/configuracoes/lojas-virtuais"):
            raise CentralError("central_endpoint_denied", 400)
        if payload.method != "GET" and resource not in {
                "items", "produtos", "pedidos", "nfe", "nfce", "estoques", "stock", "user-products",
                "seller-promotions", "pricing-automation", "answers", "questions", "messages",
                "claims", "post-purchase", "pictures", "shipments"}:
            raise CentralError("central_endpoint_denied", 400)
        if (set(payload.headers) - SAFE_HEADERS or any(len(value) > 256 or "\n" in value or "\r" in value
                                                     for value in payload.headers.values())):
            raise CentralError("central_header_denied", 400)
        if len(json.dumps([payload.params, payload.body]).encode()) > 1024 * 1024:
            raise CentralError("central_request_too_large", 413)
        # Account-scoped searches cannot substitute another seller.
        if payload.provider == "mercadolivre":
            match = re.match(r"^/users/([^/]+)", decoded)
            if match and match.group(1) not in ("me", str(connection["seller_id"])):
                raise CentralError("central_seller_denied", 403)
            for key in ("seller", "seller_id", "user_id"):
                if key in payload.params and str(payload.params[key]) != str(connection["seller_id"]):
                    raise CentralError("central_seller_denied", 403)

    def _http(self, method, url, **kwargs):
        with self.session_factory() as session:
            response = session.request(method, url, timeout=(3.05, 25), allow_redirects=False,
                                       stream=True, **kwargs)
            try:
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise CentralError("central_response_too_large", 413)
                    chunks.append(chunk)
                return response.status_code, dict(response.headers), b"".join(chunks)
            finally:
                response.close()

    def _token(self, provider, app_id, app_secret, form):
        if provider == "bling":
            url = "https://www.bling.com.br/Api/v3/oauth/token"
            kwargs = {"auth": (app_id, app_secret), "data": form}
        else:
            url = BASES[provider] + "/oauth/token"
            kwargs = {"data": {**form, "client_id": app_id, "client_secret": app_secret}}
        status, _, raw = self._http("POST", url, **kwargs)
        if status != 200:
            raise CentralError("central_reconnect_required", 409)
        try:
            value = json.loads(raw)
            if not value.get("access_token") or not value.get("refresh_token") or int(value["expires_in"]) < 60:
                raise ValueError()
            return {"provider": provider, "app_id": app_id, "app_secret": app_secret,
                    "access_token": value["access_token"], "refresh_token": value["refresh_token"],
                    "expires_at": self.clock() + int(value["expires_in"])}
        except (KeyError, ValueError, TypeError):
            raise CentralError("central_oauth_invalid", 502) from None

    def exchange(self, draft, code):
        creds = self._token(draft["provider"], draft["app_id"], draft["app_secret"], {
            "grant_type": "authorization_code", "code": code, "redirect_uri": draft["redirect_uri"]})
        endpoint = "/users/me" if draft["provider"] == "mercadolivre" else "/empresas/me/dados-basicos"
        status, _, raw = self._http("GET", BASES[draft["provider"]] + endpoint,
                                    headers={"Authorization": "Bearer " + creds["access_token"]})
        if status != 200:
            raise CentralError("central_account_identity_unavailable", 502)
        value = json.loads(raw)
        account = value if draft["provider"] == "mercadolivre" else value.get("data", {})
        seller = str(account.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", seller):
            raise CentralError("central_account_identity_invalid", 502)
        return {**creds, "seller_id": seller, "site_id": str(account.get("site_id") or "")}

    def refresh(self, credentials):
        updated = self._token(credentials["provider"], credentials["app_id"], credentials["app_secret"],
                              {"grant_type": "refresh_token", "refresh_token": credentials["refresh_token"]})
        return {**credentials, **updated}

    def request(self, credentials, payload):
        status, headers, raw = self._http(payload.method, BASES[payload.provider] + payload.path,
                                         headers={**payload.headers, "Authorization": "Bearer " + credentials["access_token"]},
                                         params=payload.params, json=payload.body)
        # Provider errors never carry token-bearing request echoes to the desktop.
        if status >= 300:
            raw = json.dumps({"error": "provider_rejected", "status": status}).encode()
            headers = {"Content-Type": "application/json", "Retry-After": headers.get("Retry-After", "")}
        elif any(str(credentials.get(key, "")).encode() in raw for key in
                 ("access_token", "refresh_token", "app_secret") if credentials.get(key)):
            raise CentralError("central_sensitive_response_blocked", 502)
        return {"status": status, "body_base64": base64.b64encode(raw).decode(),
                "headers": {k: v for k, v in headers.items() if k.lower() in ("content-type", "retry-after", "x-version", "etag")}}
