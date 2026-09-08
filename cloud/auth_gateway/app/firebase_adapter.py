"""Firebase/Firestore adapters used only by the Cloud Run gateway."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

import firebase_admin
import requests
from firebase_admin import auth, firestore

from .config import GatewaySettings
from .domain import (
    AuthRejected,
    GatewayUnavailable,
    assert_access_allowed,
    assert_registered_machine,
    machine_claim_updates,
    password_value,
)


def firebase_app(project_id: str):
    try:
        return firebase_admin.get_app()
    except ValueError:
        return firebase_admin.initialize_app(options={"projectId": project_id})


class FirestoreUserRepository:
    def __init__(self, settings: GatewaySettings) -> None:
        self._settings = settings
        self._client = firestore.client(app=firebase_app(settings.project_id))
        self._collection = self._client.collection(settings.users_collection)

    @staticmethod
    def _document_id(username: str) -> str:
        return str(username or "").strip().lower().replace("/", "_")

    def get_user(self, username: str) -> dict[str, Any] | None:
        try:
            snapshot = self._collection.document(self._document_id(username)).get(
                retry=None,
                timeout=self._settings.firestore_timeout_seconds,
            )
        except Exception as exc:
            raise GatewayUnavailable("firestore_read_failed") from exc
        if not snapshot.exists:
            return None
        data = snapshot.to_dict() or {}
        stored_username = str(data.get("username") or username).strip().lower()
        if stored_username != username:
            return None
        return data

    def claim_machine(
        self,
        username: str,
        expected_password_value: str,
        machine_id: str,
    ) -> dict[str, Any]:
        document_ref = self._collection.document(self._document_id(username))
        transaction = self._client.transaction(max_attempts=3)

        @firestore.transactional
        def claim(current_transaction):
            snapshot = document_ref.get(transaction=current_transaction)
            if not snapshot.exists:
                raise AuthRejected("invalid_credentials", 401)
            current = snapshot.to_dict() or {}
            if not secrets.compare_digest(password_value(current), expected_password_value):
                raise AuthRejected("invalid_credentials", 401)
            updates = machine_claim_updates(current, username, machine_id)
            if not updates:
                return current
            updates["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            current_transaction.update(document_ref, updates)
            current.update(updates)
            return current

        try:
            return claim(transaction)
        except AuthRejected:
            raise
        except Exception as exc:
            raise GatewayUnavailable("firestore_transaction_failed") from exc

    def change_password(
        self,
        username: str,
        expected_password_value: str,
        new_password_hash: str,
        machine_id: str,
    ) -> dict[str, Any]:
        document_ref = self._collection.document(self._document_id(username))
        transaction = self._client.transaction(max_attempts=3)

        @firestore.transactional
        def change(current_transaction):
            snapshot = document_ref.get(transaction=current_transaction)
            if not snapshot.exists:
                raise AuthRejected("invalid_credentials", 401)
            current = snapshot.to_dict() or {}
            if not secrets.compare_digest(password_value(current), expected_password_value):
                raise AuthRejected("invalid_credentials", 401)
            assert_access_allowed(current)
            assert_registered_machine(current, machine_id)
            updates = {
                "password_hash": new_password_hash,
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            current_transaction.update(document_ref, updates)
            current.update(updates)
            return current

        try:
            return change(transaction)
        except AuthRejected:
            raise
        except Exception as exc:
            raise GatewayUnavailable("firestore_password_update_failed") from exc

    def enable_central_accounts(
        self,
        username: str,
        expected_client_id: str,
        machine_id: str,
        expected_password_epoch: str,
    ) -> dict[str, Any]:
        document_ref = self._collection.document(self._document_id(username))
        transaction = self._client.transaction(max_attempts=3)

        @firestore.transactional
        def enable(current_transaction):
            snapshot = document_ref.get(transaction=current_transaction)
            if not snapshot.exists:
                raise AuthRejected("invalid_credentials", 401)
            current = snapshot.to_dict() or {}
            assert_access_allowed(current)
            assert_registered_machine(current, machine_id)
            from .policy import canonical_hash, user_client_id
            if user_client_id(current) != expected_client_id:
                raise AuthRejected("invalid_credentials", 401)
            if not secrets.compare_digest(canonical_hash([password_value(current)]), expected_password_epoch):
                raise AuthRejected("invalid_credentials", 401)
            if current.get("central_accounts_enabled") is True:
                return current
            updates = {
                "central_accounts_enabled": True,
                "central_accounts_enabled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            current_transaction.update(document_ref, updates)
            current.update(updates)
            return current

        try:
            return enable(transaction)
        except AuthRejected:
            raise
        except Exception as exc:
            raise GatewayUnavailable("firestore_central_enable_failed") from exc


class FirebaseIdentityTokenIssuer:
    def __init__(self, settings: GatewaySettings) -> None:
        self._settings = settings
        self._app = firebase_app(settings.project_id)

    def issue(self, uid: str, claims: dict[str, Any]) -> tuple[str, int]:
        try:
            custom_token = auth.create_custom_token(uid, developer_claims=claims, app=self._app)
            custom_token_text = (
                custom_token.decode("utf-8") if isinstance(custom_token, bytes) else str(custom_token)
            )
            with requests.Session() as session:
                response = session.post(
                    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken",
                    params={"key": self._settings.firebase_web_api_key},
                    json={"token": custom_token_text, "returnSecureToken": True},
                    timeout=(3.05, self._settings.identity_timeout_seconds),
                    allow_redirects=False,
                )
            if response.status_code != 200:
                raise GatewayUnavailable("identity_exchange_rejected")
            payload = response.json()
            identity_token = str(payload.get("idToken") or "").strip()
            expires_in = int(payload.get("expiresIn") or 0)
            if not identity_token or expires_in < 60:
                raise GatewayUnavailable("identity_exchange_invalid")
            return identity_token, expires_in
        except GatewayUnavailable:
            raise
        except Exception as exc:
            raise GatewayUnavailable("identity_exchange_failed") from exc


__all__ = ["FirebaseIdentityTokenIssuer", "FirestoreUserRepository", "firebase_app"]
