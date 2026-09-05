"""Server-only persistence and authenticated encryption for central accounts.

Transactions contain only deterministic document changes, never provider calls.
"""
from __future__ import annotations

import base64
import json
import os
from copy import deepcopy
from threading import RLock

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import GatewayUnavailable


class Vault:
    def __init__(self, encoded_key: str):
        try:
            key = base64.b64decode(encoded_key, validate=True)
            if len(key) != 32:
                raise ValueError()
            self._cipher = AESGCM(key)
        except Exception:
            raise GatewayUnavailable("central_vault_configuration") from None

    def seal(self, context: str, value: dict) -> str:
        nonce = os.urandom(12)
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
        return base64.urlsafe_b64encode(nonce + self._cipher.encrypt(nonce, raw, context.encode())).decode()

    def open(self, context: str, value: str) -> dict:
        try:
            raw = base64.urlsafe_b64decode(value)
            return json.loads(self._cipher.decrypt(raw[:12], raw[12:], context.encode()))
        except Exception:
            raise GatewayUnavailable("central_vault_invalid") from None


class MemoryDocuments:
    """Deterministic adapter for tests; production always uses Firestore."""
    def __init__(self):
        self._documents = {}
        self._lock = RLock()

    def get(self, group, key):
        with self._lock:
            return deepcopy(self._documents.get((group, key)))

    def change(self, group, key, transform):
        with self._lock:
            value = transform(deepcopy(self._documents.get((group, key))))
            self._documents[group, key] = deepcopy(value)
            return deepcopy(value)

    def accessible(self, access_key):
        with self._lock:
            return deepcopy([v for (group, _), v in self._documents.items()
                             if group == "stores" and access_key in v.get("access_keys", [])])


class FirestoreDocuments:
    def __init__(self, client, timeout=5):
        self.client = client
        self.timeout = timeout

    def _ref(self, group, key):
        # Both names are generated internally, never used as caller-supplied paths.
        return self.client.collection("jk_central_v1_" + group).document(key)

    def get(self, group, key):
        return self._ref(group, key).get(retry=None, timeout=self.timeout).to_dict()

    def change(self, group, key, transform):
        from firebase_admin import firestore
        ref = self._ref(group, key)

        @firestore.transactional
        def update(transaction):
            snapshot = ref.get(transaction=transaction, retry=None, timeout=self.timeout)
            value = transform(snapshot.to_dict())
            transaction.set(ref, value)
            return value

        return update(self.client.transaction(max_attempts=3))

    def accessible(self, access_key):
        from google.cloud.firestore_v1.base_query import FieldFilter
        query = self.client.collection("jk_central_v1_stores").where(
            filter=FieldFilter("access_keys", "array_contains", access_key))
        # A bounded bootstrap is never a catalogue download.
        rows = list(query.limit(101).stream(retry=None, timeout=self.timeout))
        if len(rows) > 100:
            raise GatewayUnavailable("central_store_limit")
        return [row.to_dict() for row in rows]
