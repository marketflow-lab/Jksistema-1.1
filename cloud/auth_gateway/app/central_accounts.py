"""Tenant/store authorization, bootstrap and provider operations on demand."""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from urllib.parse import urlencode

from .errors import AuthRejected
from .policy import (assert_access_allowed, assert_registered_machine, canonical_hash,
                     normalize_permissions, password_value, user_client_id)


class CentralError(Exception):
    def __init__(self, code, status=409, details=None):
        self.code, self.status = code, status
        self.details = dict(details or {})
        super().__init__(code)


def key_for(*parts):
    return canonical_hash(list(parts))


@dataclass(frozen=True)
class Principal:
    username: str
    tenant: str
    machine: str
    permissions: dict = field(repr=False)
    password_epoch: str = field(default="", repr=False)

    @property
    def key(self):
        return key_for(self.tenant, self.username)


class CentralAccounts:
    def __init__(self, users, documents, vault, provider, public_origin, *, clock=time.time,
                 enrollment_required=False):
        self.users, self.documents, self.vault = users, documents, vault
        self.provider, self.public_origin, self.clock = provider, public_origin.rstrip("/"), clock
        self.enrollment_required = enrollment_required

    def enabled_for(self, user):
        return not self.enrollment_required or user.get("central_accounts_enabled") is True

    def migration_session(self, user, username, machine):
        if self.enabled_for(user):
            raise CentralError("central_already_enabled", 409)
        tenant = user_client_id(user)
        principal = Principal(username, tenant, machine,
                              normalize_permissions(user.get("permissions") or user.get("permissoes")))
        self._can_manage(principal)
        expires = int(self.clock()) + 30 * 60
        session = self.vault.seal("migration-session-v1", {
            "username": username, "tenant": tenant, "machine": machine, "expires": expires,
            "password_epoch": key_for(password_value(user)), "nonce": secrets.token_hex(16)})
        return {"protocol": 1, "mode": "legacy_adoption", "session": session,
                "expires_at": expires}

    def bootstrap_session(self, user, username, machine):
        if not self.enabled_for(user):
            raise CentralError("central_access_revoked", 403)
        tenant = user_client_id(user)
        principal = Principal(username, tenant, machine,
                              normalize_permissions(user.get("permissions") or user.get("permissoes")))
        expires = int(self.clock()) + 8 * 3600
        session = self.vault.seal("desktop-session-v1", {
            "username": username, "tenant": tenant, "machine": machine, "expires": expires,
            "password_epoch": key_for(password_value(user)), "nonce": secrets.token_hex(16)})
        return {"protocol": 1, "sync_mode": "manual", "session": session,
                "expires_at": expires, "stores": self.list_stores(principal)}

    def authenticate(self, token, machine):
        try:
            data = self.vault.open("desktop-session-v1", token)
            if data["expires"] <= self.clock() or not secrets.compare_digest(data["machine"], machine):
                raise ValueError()
            user = self.users.get_user(data["username"])
            if not user or not self.enabled_for(user) or user_client_id(user) != data["tenant"]:
                raise ValueError()
            assert_access_allowed(user)
            assert_registered_machine(user, machine)
            if not secrets.compare_digest(data["password_epoch"], key_for(password_value(user))):
                raise ValueError()
            return Principal(data["username"], data["tenant"], machine,
                             normalize_permissions(user.get("permissions") or user.get("permissoes")))
        except AuthRejected:
            raise CentralError("central_access_revoked", 403) from None
        except (KeyError, ValueError):
            raise CentralError("central_session_expired", 401) from None

    def authenticate_migration(self, token, machine):
        try:
            data = self.vault.open("migration-session-v1", token)
            if data["expires"] <= self.clock() or not secrets.compare_digest(data["machine"], machine):
                raise ValueError()
            user = self.users.get_user(data["username"])
            if not user or user_client_id(user) != data["tenant"]:
                raise ValueError()
            assert_access_allowed(user)
            assert_registered_machine(user, machine)
            if not secrets.compare_digest(data["password_epoch"], key_for(password_value(user))):
                raise ValueError()
            principal = Principal(data["username"], data["tenant"], machine,
                                  normalize_permissions(user.get("permissions") or user.get("permissoes")),
                                  data["password_epoch"])
            self._can_manage(principal)
            return principal
        except AuthRejected:
            raise CentralError("central_migration_revoked", 403) from None
        except (KeyError, ValueError):
            raise CentralError("central_migration_expired", 401) from None

    @staticmethod
    def _legacy_credentials(connection):
        return {
            "provider": connection.provider,
            "app_id": connection.app_id,
            "app_secret": connection.app_secret.get_secret_value(),
            "access_token": connection.access_token.get_secret_value(),
            "refresh_token": connection.refresh_token.get_secret_value(),
            "expires_at": connection.expires_at,
        }

    @staticmethod
    def _migration_public(row):
        return {
            "success": row.get("status") == "completed",
            "operation_id": row["operation_id"],
            "status": row["status"],
            "stores_total": int(row.get("stores_total") or 0),
            "connections_total": int(row.get("connections_total") or 0),
            "completed_at": row.get("completed_at"),
            "failure": row.get("failure"),
        }

    def migration_status(self, principal, operation_id):
        row = self.documents.get("operations", key_for("legacy-adoption", principal.key, operation_id))
        if not row or row.get("kind") != "legacy-adoption" or row.get("principal") != principal.key:
            raise CentralError("central_migration_not_found", 404)
        return self._migration_public(row)

    def adopt_legacy(self, principal, payload):
        providers_expected = {"mercadolivre", "bling"}
        store_ids = [store.store_id for store in payload.stores]
        if len(set(store_ids)) != len(store_ids):
            raise CentralError("central_migration_duplicate_store", 400)
        normalized = []
        for store in payload.stores:
            providers = [connection.provider for connection in store.connections]
            if set(providers) != providers_expected or len(set(providers)) != 2:
                raise CentralError("central_migration_connections_incomplete", 400,
                                   {"store_id": store.store_id})
            normalized.append({
                "store_id": store.store_id,
                "name": store.name,
                "connections": [{
                    **self._legacy_credentials(connection),
                    "expected_account_id": connection.expected_account_id,
                    "expected_site_id": connection.expected_site_id,
                } for connection in store.connections],
            })
        fingerprint = key_for(normalized)
        operation_key = key_for("legacy-adoption", principal.key, payload.operation_id)

        def start(old):
            if old:
                if old.get("fingerprint") != fingerprint or old.get("principal") != principal.key:
                    raise CentralError("central_migration_conflict", 409)
                return old
            return {
                "kind": "legacy-adoption", "operation_id": payload.operation_id,
                "principal": principal.key, "tenant": principal.tenant,
                "fingerprint": fingerprint, "status": "validating",
                "stores_total": len(normalized),
                "connections_total": sum(len(store["connections"]) for store in normalized),
                "created_at": int(self.clock()),
                "delete_after": datetime.fromtimestamp(self.clock() + 7 * 86400, timezone.utc),
            }

        operation = self.documents.change("operations", operation_key, start)
        if operation.get("status") == "completed":
            return self._migration_public(operation)

        verified = []
        try:
            for store in normalized:
                current_store = self.documents.get("stores", key_for("store", store["store_id"]))
                if current_store and (current_store.get("tenant") != principal.tenant
                                      or current_store.get("owner") != principal.key):
                    raise CentralError("central_store_identity_conflict", 409,
                                       {"store_id": store["store_id"]})
                verified_connections = {}
                for connection in store["connections"]:
                    try:
                        identity = self.provider.identify(connection)
                    except CentralError as exc:
                        raise CentralError(exc.code, exc.status, {
                            "store_id": store["store_id"], "provider": connection["provider"]}) from None
                    seller_id = str(identity.get("seller_id") or "")
                    site_id = str(identity.get("site_id") or "")
                    if (connection["expected_account_id"]
                            and not secrets.compare_digest(connection["expected_account_id"], seller_id)):
                        raise CentralError("central_identity_mismatch", 409, {
                            "store_id": store["store_id"], "provider": connection["provider"]})
                    if (connection["expected_site_id"]
                            and not secrets.compare_digest(connection["expected_site_id"], site_id)):
                        raise CentralError("central_identity_mismatch", 409, {
                            "store_id": store["store_id"], "provider": connection["provider"]})
                    credentials = {**connection, "seller_id": seller_id, "site_id": site_id}
                    credentials.pop("expected_account_id", None)
                    credentials.pop("expected_site_id", None)
                    connection_id = key_for(connection["provider"], connection["app_id"], seller_id)
                    existing = self.documents.get("connections", connection_id)
                    if existing and existing.get("tenant") != principal.tenant:
                        raise CentralError("central_account_already_owned", 409, {
                            "store_id": store["store_id"], "provider": connection["provider"]})
                    attached = (current_store or {}).get("connections", {}).get(connection["provider"], {})
                    if attached and str(attached.get("seller_id") or "") != seller_id:
                        raise CentralError("central_identity_mismatch", 409, {
                            "store_id": store["store_id"], "provider": connection["provider"]})
                    verified_connections[connection["provider"]] = {
                        "id": connection_id, "seller_id": seller_id, "site_id": site_id,
                        "credentials": credentials,
                    }
                verified.append({**store, "connections": verified_connections})

            for store in verified:
                for connection in store["connections"].values():
                    connection_id = connection["id"]
                    credentials = connection["credentials"]

                    def save_connection(old, *, connection_id=connection_id, credentials=credentials):
                        if old and old.get("tenant") != principal.tenant:
                            raise CentralError("central_account_already_owned", 409)
                        return {"tenant": principal.tenant,
                                "version": int((old or {}).get("version", 0)) + 1,
                                "sealed": self.vault.seal("connection:" + connection_id, credentials),
                                "status": "ready", "lease_until": 0}

                    self.documents.change("connections", connection_id, save_connection)

            for store in verified:
                public_connections = {provider: {key: value[key] for key in ("id", "seller_id", "site_id")}
                                      for provider, value in store["connections"].items()}

                def save_store(old, *, store=store, public_connections=public_connections):
                    if old and (old.get("tenant") != principal.tenant or old.get("owner") != principal.key):
                        raise CentralError("central_store_identity_conflict", 409)
                    return {"store_id": store["store_id"], "tenant": principal.tenant,
                            "owner": principal.key, "name": store["name"],
                            "connections": public_connections,
                            "grants": {principal.key: "owner"}, "access_keys": [principal.key]}

                self.documents.change("stores", key_for("store", store["store_id"]), save_store)

            self.users.enable_central_accounts(
                principal.username, principal.tenant, principal.machine, principal.password_epoch)
            completed_at = int(self.clock())
            operation = self.documents.change("operations", operation_key, lambda old: {
                **old, "status": "completed", "completed_at": completed_at, "failure": None})
            return self._migration_public(operation)
        except CentralError as exc:
            failure = {"code": exc.code, **exc.details}
            self.documents.change("operations", operation_key, lambda old: {
                **old, "status": "failed", "failure": failure})
            raise
        except Exception:
            try:
                self.documents.change("operations", operation_key, lambda old: {
                    **old, "status": "failed",
                    "failure": {"code": "central_migration_unavailable"}})
            except Exception:
                pass
            raise CentralError("central_migration_unavailable", 503) from None

    @staticmethod
    def _can_manage(principal):
        if not any(principal.permissions.get(p) for p in ("integracao", "admin_usuarios", "full")):
            raise CentralError("central_permission_denied", 403)

    def store(self, principal, store_id, *, write=False, manage=False):
        row = self.documents.get("stores", key_for("store", store_id))
        if not row or row.get("deleted") or principal.key not in row.get("access_keys", []):
            raise CentralError("central_store_denied", 403)
        grant = row["grants"].get(principal.key)
        if write and grant not in ("owner", "write"):
            raise CentralError("central_store_read_only", 403)
        if manage:
            self._can_manage(principal)
            if row["owner"] != principal.key:
                raise CentralError("central_owner_required", 403)
        if not any(principal.permissions.values()):
            raise CentralError("central_permission_denied", 403)
        return row

    @staticmethod
    def public_store(row, principal):
        return {"store_id": row["store_id"], "nome": row["name"],
                "owner_client_id": row["tenant"], "access": row["grants"][principal.key],
                "integracoes": {name: {"connected": True, "central": True,
                                        "user_id": connection.get("seller_id", ""),
                                        "site_id": connection.get("site_id", "")}
                                for name, connection in row.get("connections", {}).items()}}

    def list_stores(self, principal):
        if not any(principal.permissions.values()):
            return []
        return [self.public_store(row, principal) for row in self.documents.accessible(principal.key)
                if not row.get("deleted")]

    def create_store(self, principal, payload):
        self._can_manage(principal)
        store_id = key_for(principal.key, payload.request_id)[:32]
        initial = {"store_id": store_id, "tenant": principal.tenant, "owner": principal.key,
                   "name": payload.name, "connections": {}, "grants": {principal.key: "owner"},
                   "access_keys": [principal.key]}
        row = self.documents.change("stores", key_for("store", store_id), lambda old: old or initial)
        return self.public_store(row, principal)

    def grant(self, principal, store_id, payload):
        self.store(principal, store_id, manage=True)
        username = payload.username.lower()
        user = self.users.get_user(username)
        if not user:
            raise CentralError("central_user_not_found", 404)
        target = key_for(user_client_id(user), username)

        def change(row):
            if not row or row.get("deleted") or row["owner"] != principal.key or target == row["owner"]:
                raise CentralError("central_grant_invalid", 403)
            if payload.access == "revoke":
                row["grants"].pop(target, None)
            else:
                if len(row["grants"]) >= 100 and target not in row["grants"]:
                    raise CentralError("central_grant_limit")
                row["grants"][target] = payload.access
            row["access_keys"] = sorted(row["grants"])
            return row

        self.documents.change("stores", key_for("store", store_id), change)
        return {"success": True}

    def delete_store(self, principal, store_id):
        self.store(principal, store_id, manage=True)

        def remove(row):
            row.update(deleted=True, connections={}, grants={}, access_keys=[])
            return row

        self.documents.change("stores", key_for("store", store_id), remove)
        return {"success": True, "store_id": store_id}

    def disconnect(self, principal, store_id, provider):
        self.store(principal, store_id, manage=True)

        def change(row):
            row["connections"].pop(provider, None)
            return row

        self.documents.change("stores", key_for("store", store_id), change)
        return {"success": True}

    def connect(self, principal, store_id, payload):
        row = self.store(principal, store_id, manage=True)
        state = secrets.token_urlsafe(32)
        callback = self.public_origin + "/api/central/v1/oauth/callback"
        draft = {"tenant": row["tenant"], "store_id": store_id, "owner": principal.key,
                 "username": principal.username, "machine": principal.machine,
                 "provider": payload.provider, "app_id": payload.app_id,
                 "app_secret": payload.app_secret.get_secret_value(), "redirect_uri": callback}
        self.documents.change("oauth", key_for(state), lambda _: {
            "expires": self.clock() + 600, "status": "pending",
            "delete_after": datetime.fromtimestamp(self.clock() + 86400, timezone.utc),
            "sealed": self.vault.seal("oauth:" + key_for(state), draft)})
        host = ("https://auth.mercadolivre.com.br/authorization" if payload.provider == "mercadolivre"
                else "https://www.bling.com.br/Api/v3/oauth/authorize")
        return {"success": True, "redirect_uri": callback, "url": host + "?" + urlencode({
            "response_type": "code", "client_id": payload.app_id, "redirect_uri": callback, "state": state})}

    def complete_oauth(self, state, code):
        state_key = key_for(state)

        def claim(row):
            if not row or row["expires"] <= self.clock() or row["status"] != "pending":
                raise CentralError("central_oauth_expired", 400)
            row["status"] = "consumed"
            return row

        row = self.documents.change("oauth", state_key, claim)
        draft = self.vault.open("oauth:" + state_key, row["sealed"])
        # Recheck authorization after the browser round trip, before consuming the code.
        current = self.users.get_user(draft["username"])
        if not current or user_client_id(current) != draft["tenant"]:
            raise CentralError("central_access_revoked", 403)
        assert_access_allowed(current)
        assert_registered_machine(current, draft["machine"])
        principal = Principal(draft["username"], draft["tenant"], draft["machine"],
                              normalize_permissions(current.get("permissions") or current.get("permissoes")))
        self.store(principal, draft["store_id"], manage=True)
        credentials = self.provider.exchange(draft, code)
        # One refresh authority per provider/app/account, including duplicated store labels.
        connection_id = key_for(draft["provider"], draft["app_id"], credentials["seller_id"])

        def save(old):
            if old and old["tenant"] != draft["tenant"]:
                raise CentralError("central_account_already_owned", 409)
            return {"tenant": draft["tenant"], "version": int((old or {}).get("version", 0)) + 1,
                    "sealed": self.vault.seal("connection:" + connection_id, credentials),
                    "status": "ready", "lease_until": 0}

        self.documents.change("connections", connection_id, save)

        def attach(store):
            if not store or store.get("deleted") or store["owner"] != draft["owner"]:
                raise CentralError("central_store_changed")
            store["connections"][draft["provider"]] = {
                "id": connection_id, "seller_id": credentials["seller_id"],
                "site_id": credentials.get("site_id", "")}
            return store

        self.documents.change("stores", key_for("store", draft["store_id"]), attach)
        return {"success": True}

    def _credentials(self, connection_id, tenant, *, force=False, rejected_token=None):
        row = self.documents.get("connections", connection_id)
        if not row or row["tenant"] != tenant:
            raise CentralError("central_connection_unavailable")
        if row["status"] == "refreshing":
            # An expired lease is uncertain: never replay a possibly consumed refresh token.
            raise CentralError("central_refresh_busy" if row["lease_until"] > self.clock()
                               else "central_reconnect_required", 409)
        if row["status"] != "ready":
            raise CentralError("central_reconnect_required", 409)
        credentials = self.vault.open("connection:" + connection_id, row["sealed"])
        if rejected_token and credentials["access_token"] != rejected_token:
            return credentials
        if not force and credentials["expires_at"] > self.clock() + 60:
            return credentials
        lease = secrets.token_hex(16)

        def claim(current):
            if current["version"] != row["version"] or current["status"] != "ready":
                raise CentralError("central_refresh_busy", 409)
            current.update(status="refreshing", lease=lease, lease_until=self.clock() + 45)
            return current

        self.documents.change("connections", connection_id, claim)
        try:
            refreshed = self.provider.refresh(credentials)
        except Exception:
            def uncertain(current):
                if current.get("lease") == lease:
                    current["status"] = "reconnect_required"
                return current
            self.documents.change("connections", connection_id, uncertain)
            raise CentralError("central_reconnect_required", 409) from None

        def finish(current):
            if current.get("lease") != lease or current["version"] != row["version"]:
                raise CentralError("central_connection_changed")
            current.update(sealed=self.vault.seal("connection:" + connection_id, refreshed),
                           status="ready", lease_until=0, version=current["version"] + 1)
            current.pop("lease", None)
            return current

        self.documents.change("connections", connection_id, finish)
        return refreshed

    def request(self, principal, store_id, payload):
        writing = payload.method != "GET"
        store = self.store(principal, store_id, write=writing)
        connection = store["connections"].get(payload.provider)
        if not connection:
            raise CentralError("central_connection_missing", 409)
        self.provider.validate(payload, connection)
        from .central_provider import assert_provider_permission
        assert_provider_permission(principal, payload)
        operation_key = key_for(principal.key, store_id, payload.request_id)
        fingerprint = key_for(payload.model_dump())
        if writing:
            def claim(old):
                if old:
                    raise CentralError("central_operation_already_submitted", 409)
                return {"fingerprint": fingerprint, "status": "pending", "expires": self.clock() + 86400,
                        "delete_after": datetime.fromtimestamp(self.clock() + 7 * 86400, timezone.utc)}
            self.documents.change("operations", operation_key, claim)
        credentials = self._credentials(connection["id"], store["tenant"])
        try:
            response = self.provider.request(credentials, payload)
            if response["status"] == 401:
                credentials = self._credentials(connection["id"], store["tenant"], force=True,
                                                rejected_token=credentials["access_token"])
                # Retry only reads; mutations are never automatically replayed.
                if not writing:
                    response = self.provider.request(credentials, payload)
            if writing:
                self.documents.change("operations", operation_key, lambda old: {**old, "status": "completed"})
            return response
        except CentralError:
            raise
        except Exception:
            raise CentralError("central_result_uncertain" if writing else "central_provider_unavailable", 502) from None
