"""Short-lived Firestore grants issued only after remote authentication.

Grants contain a safe user projection and immutable resource identifiers. The
administrative Firebase credential stays in Cloud Run; client tokens are subject
to Firestore Rules on every operation, including after token refresh.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .errors import GatewayUnavailable
from .policy import (assert_access_allowed, assert_registered_machine, machine_hash,
                     normalize_machine_ids, normalize_permissions, uid_for_username,
                     user_client_id, normalize_max_machines)

GRANTS_COLLECTION = "jk_sistema_firebase_grants"
SESSION_SECONDS = 8 * 60 * 60
SCOPE_PERMISSIONS = {
    "cadastro": "cadastro", "lojas_integracoes": "integracao", "vendas": "vendas",
    "favoritos_historico": "favoritos", "sku_campos_pesquisa": "favoritos",
    "favoritos_planilhas": "favoritos", "anuncios_ml": "favoritos",
}
# These values are safe to expose and must remain equal to the authoritative
# user record. Missing/legacy fields use exactly the same defaults in Rules.
POLICY_DEFAULTS = {
    "active": None, "ativo": None, "valid_until": None, "validade": None,
    "vencimento": None, "client_id": None, "cliente": None, "tenant_id": None,
    "permissions": None, "permissoes": None, "machine_ids": None,
    "maquinas": None, "machines": None, "machine_id": None,
    "max_machines": None, "limite_maquinas": None,
    "updated_at": None,
}


def resource_hash(*parts: str) -> str:
    material = "|".join(str(part or "").strip().lower() for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_grant(user: dict, username: str, machine_id: str, users_collection: str,
                *, now: int | None = None) -> dict:
    expiry = assert_access_allowed(user)
    assert_registered_machine(user, machine_id)
    now = int(time.time()) if now is None else int(now)
    expires = now + SESSION_SECONDS
    if expiry:
        access_end = (datetime.strptime(expiry, "%d/%m/%Y") + timedelta(days=1)).replace(
            tzinfo=ZoneInfo("America/Sao_Paulo"))
        expires = min(expires, int(access_end.timestamp()))
    tenant = user_client_id(user)
    permissions = normalize_permissions(user.get("permissions") or user.get("permissoes"))
    machines = normalize_machine_ids(user.get("machine_ids") or user.get("maquinas")
                                    or user.get("machines"), user.get("machine_id"))
    scopes = [scope for scope, permission in SCOPE_PERMISSIONS.items()
              if permissions.get("full") is True or permissions.get(permission) is True]
    scope_pointers = {scope: resource_hash("shared-sync-machine", tenant, username, scope)
                      for scope in scopes}
    keyring_id = resource_hash("shared-sync-keyring", tenant, username)
    member_id = resource_hash("shared-sync-keyring-member", tenant, username, machine_id)
    return {
        "protocol": 1, "uid": uid_for_username(username), "username": username,
        "client_id": tenant, "machine_id": machine_id, "machine_ids": machines,
        "permissions": permissions, "session_expires_at": expires,
        "expires_at": datetime.fromtimestamp(expires, timezone.utc),
        "users_collection": users_collection,
        "user_policy": {field: user.get(field, default) for field, default in POLICY_DEFAULTS.items()},
        "profile": {"username": username, "client_id": tenant, "machine_id": machine_id,
                    "machine_ids": machines, "permissions": permissions,
                    "active": True, "valid_until": expiry,
                    "name": str(user.get("name") or user.get("nome") or username).strip() or username,
                    "email": str(user.get("email") or user.get("google_email") or "").strip().lower(),
                    "max_machines": normalize_max_machines(user.get("max_machines", user.get("limite_maquinas", 1)))},
        "scope_pointers": scope_pointers,
        "pointers": {pointer: scope for scope, pointer in scope_pointers.items()},
        "config_id": resource_hash("shared-sync-config", tenant),
        "keyring_id": keyring_id, "member_id": member_id,
        "member_ids": [resource_hash("shared-sync-keyring-member", tenant, username, item)
                       for item in machines],
        "owner_client_hash": resource_hash("client", tenant)[:16],
        "owner_user_hash": resource_hash("user", tenant, username)[:16],
        "receipt_id": machine_hash(machine_id),
    }


class FirebaseSessionService:
    def __init__(self, settings, store, token_issuer, *, clock=time.time):
        self.settings, self.store, self.token_issuer, self.clock = settings, store, token_issuer, clock

    def bootstrap_session(self, user: dict, username: str, machine_id: str) -> dict:
        now = int(self.clock())
        grant = build_grant(user, username, machine_id, self.settings.users_collection, now=now)
        grant_id = secrets.token_hex(32)
        try:
            self.store.collection(GRANTS_COLLECTION).document(grant_id).create(
                grant, retry=None, timeout=self.settings.firestore_timeout_seconds)
            token = self.token_issuer.issue_session(grant["uid"], {
                "jk_firebase_v": 1, "jk_firebase_grant": grant_id,
                "jk_username": username, "jk_client_id": grant["client_id"],
                "jk_machine_hash": machine_hash(machine_id),
            })
            if not token.get("id_token") or not token.get("refresh_token"):
                raise GatewayUnavailable("firebase_session_invalid")
            return {
                "protocol": 1, "project_id": self.settings.project_id, "grant_id": grant_id,
                "id_token": token["id_token"], "refresh_token": token["refresh_token"],
                "api_key": self.settings.firebase_web_api_key,
                "expires_at": min(now + int(token["expires_in"]), grant["session_expires_at"]),
                "session_expires_at": grant["session_expires_at"],
            }
        except GatewayUnavailable:
            raise
        except Exception as exc:
            raise GatewayUnavailable("firebase_session_unavailable") from exc


def build_firebase_sessions(settings):
    from firebase_admin import firestore
    from .firebase_adapter import FirebaseIdentityTokenIssuer, firebase_app
    return FirebaseSessionService(settings,
        firestore.client(app=firebase_app(settings.project_id)), FirebaseIdentityTokenIssuer(settings))
