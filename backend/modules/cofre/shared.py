"""Company sharing over D1; local browsing never calls Firebase or the network."""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

from backend.services.transport_security import configure_requests_session
from . import shared_storage, storage

DEFAULT_URL = "https://jk-cofre-gateway.jk-sistema.workers.dev"
SYNC_INTERVAL = 60


class CofreSyncUnavailable(storage.CofreError):
    """A cloud proof failure must not invalidate the authenticated JK session."""

    def __init__(self):
        super().__init__("A sincronizacao do Cofre esta indisponivel. Sua copia local foi preservada.", 503)


class SharedCofre:
    def __init__(self, info_root: Path, *, url: str | None = None):
        self.root = Path(info_root)
        self.cache_root = self.root / "cofre_compartilhado"
        self.url = (os.environ.get("JK_COFRE_SHARED_URL", DEFAULT_URL) if url is None else url).rstrip("/")
        if self.url:
            parsed = urlsplit(self.url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
                raise ValueError("Endereco do Cofre compartilhado invalido.")
        self._checks: dict[str, float] = {}

    def _lock(self, client_id):
        return storage._lock(storage._tenant_dir(self.cache_for(client_id), client_id))

    def cache_for(self, client_id: str) -> Path:
        # D1 company keys are case-sensitive; Windows directory names are not.
        storage._tenant_dir(self.cache_root, client_id)
        return self.cache_root / hashlib.sha256(client_id.encode("utf-8")).hexdigest()

    def local_root(self, client_id: str) -> Path:
        storage._tenant_dir(self.root, client_id)
        namespaced = self.cache_root / "originais" / hashlib.sha256(client_id.encode("utf-8")).hexdigest()
        if storage._tenant_dir(namespaced, client_id).exists():
            return namespaced
        if self.root.exists():
            with os.scandir(self.root) as entries:
                if any(entry.name == client_id and entry.is_dir(follow_symlinks=False) for entry in entries):
                    return self.root
        return namespaced

    def status(self, client_id: str, principal: dict) -> dict:
        self._assert_local_principal(client_id, principal)
        meta = shared_storage.read_meta(self.cache_for(client_id), client_id)
        return {"success": True, "configured": bool(self.url), "enabled": bool(self.url) and meta["revision"] > 0,
                "revision": meta["revision"], "last_sync": meta["last_sync"],
                "can_publish": "cofre:write" in principal.get("scope", [])}

    def read_root(self, client_id: str) -> Path:
        cached = self.cache_for(client_id)
        meta = shared_storage.read_meta(cached, client_id)
        return cached if self.url and meta["revision"] > 0 else self.local_root(client_id)

    def _assert_local_principal(self, client_id: str, principal: dict, *, write=False):
        if (not isinstance(principal, dict) or principal.get("client_id") != client_id
                or not isinstance(principal.get("scope"), list) or "cofre:read" not in principal["scope"]
                or write and "cofre:write" not in principal["scope"]
                or type(principal.get("exp")) is not int or principal["exp"] <= time.time()):
            raise storage.CofreError("Acesso ao Cofre nao autorizado. Entre novamente.", 403)

    def _assert_principal(self, client_id: str, principal: dict, *, write=False):
        self._assert_local_principal(client_id, principal, write=write)
        if not isinstance(principal.get("capability"), str) or not principal["capability"]:
            raise CofreSyncUnavailable()

    def _remote(self, method: str, path: str, principal: dict, payload=None):
        try:
            with requests.Session() as session:
                configure_requests_session(session)
                with session.request(
                    method, self.url + path,
                    headers={"Authorization": "Bearer " + principal["capability"], "Accept": "application/json",
                             **({"Content-Type": "application/json"} if payload is not None else {})},
                    data=storage._dump(payload).encode("utf-8") if payload is not None else None,
                    timeout=(3.05, 10), allow_redirects=False, stream=True,
                ) as response:
                    status = response.status_code
                    if status == 304:
                        return status, None
                    raw = bytearray()
                    for chunk in response.iter_content(64 * 1024):
                        raw.extend(chunk)
                        if len(raw) > shared_storage.MAX_STATE_BYTES + 8192:
                            raise storage.CofreError("Resposta do Cofre maior que o permitido.", 502)
                    if status in {401, 403}:
                        raise CofreSyncUnavailable()
                    if status == 409:
                        raise storage.CofreError("Sincronize antes de publicar. Outra pessoa alterou o Cofre.", 409)
                    if status not in {200, 201}:
                        raise storage.CofreError("Cofre compartilhado indisponivel. Tente novamente.", 503)
                    import json
                    result = json.loads(bytes(raw).decode("utf-8"), object_pairs_hook=storage._json_object)
                    if not isinstance(result, dict):
                        raise ValueError
                    return status, result
        except storage.CofreError:
            raise
        except (requests.RequestException, ValueError, UnicodeError, TypeError, RecursionError):
            raise storage.CofreError("Nao foi possivel sincronizar. Sua copia local foi preservada.", 503) from None

    def sync(self, client_id: str, principal: dict) -> dict:
        self._assert_principal(client_id, principal)
        if not self.url:
            return {**self.status(client_id, principal), "changed": False}
        with self._lock(client_id):
            current = self.status(client_id, principal)
            now = time.monotonic()
            if now - self._checks.get(client_id, -SYNC_INTERVAL) < SYNC_INTERVAL:
                return {**current, "changed": False}
            code, response = self._remote("GET", "/v1/state?after=" + str(current["revision"]), principal)
            if code == 304:
                self._checks[client_id] = now
                return {**current, "changed": False}
            revision = response.get("revision")
            if type(revision) is not int or revision < current["revision"]:
                raise storage.CofreError("Versao compartilhada invalida.", 502)
            if revision == 0:
                self._checks[client_id] = now
                return {**current, "changed": False}
            state = shared_storage.validate_state(response.get("state"))
            if revision > current["revision"]:
                shared_storage.store_state(self.cache_for(client_id), client_id, state, revision)
            self._checks[client_id] = now
            return {**self.status(client_id, principal), "changed": revision > current["revision"]}

    def _publish_state(self, client_id: str, principal: dict, state: dict, expected_revision: int):
        self._assert_principal(client_id, principal, write=True)
        if not self.url:
            raise storage.CofreError("Compartilhamento nao configurado.", 503)
        state = shared_storage.validate_state(state)
        try:
            _, response = self._remote("PUT", "/v1/state", principal,
                                       {"expected_revision": expected_revision, "state": state})
        except storage.CofreError as exc:
            if exc.status_code == 409:
                self._checks.pop(client_id, None)
            raise
        if type(response.get("revision")) is not int or response["revision"] != expected_revision + 1:
            raise storage.CofreError("Confirmacao de publicacao invalida. Sincronize o Cofre.", 502)
        shared_storage.store_state(self.cache_for(client_id), client_id, state, response["revision"])
        self._checks[client_id] = time.monotonic()
        return {**self.status(client_id, principal), "changed": True}

    def publish(self, client_id: str, principal: dict):
        self._assert_principal(client_id, principal, write=True)
        with self._lock(client_id):
            current = self.status(client_id, principal)
            state = shared_storage.export_state(self.read_root(client_id), client_id)
            return self._publish_state(client_id, principal, state, current["revision"])

    def change_message(self, client_id, principal, guild_id, channel_id, message_id, *, patch=None, deleted=None):
        self._assert_local_principal(client_id, principal, write=True)
        with self._lock(client_id):
            current = self.status(client_id, principal)
            if not current["enabled"]:
                return storage.change_message(self.local_root(client_id), client_id, guild_id, channel_id, message_id,
                                              patch=patch, deleted=deleted)
            self._assert_principal(client_id, principal, write=True)
            state = shared_storage.export_state(self.cache_for(client_id), client_id)
            found = False
            for entry in state["guilds"]:
                if entry["guild"]["id"] != guild_id:
                    continue
                for channel in entry["channels"]:
                    if channel["id"] != channel_id:
                        continue
                    for message in channel["messages"]:
                        if message["id"] == message_id:
                            if patch is not None:
                                message.update(patch.model_dump(include=patch.model_fields_set))
                                message["edited_fields"] = sorted(set(message["edited_fields"]) | patch.model_fields_set)
                            if deleted is not None:
                                message["deleted"] = deleted
                            found = True
            if not found:
                raise storage.CofreError("Mensagem nao encontrada.", 404)
            self._publish_state(client_id, principal, state, current["revision"])
            return storage.change_message(self.cache_for(client_id), client_id, guild_id, channel_id, message_id,
                                          patch=patch, deleted=deleted)

    def change_channel(self, client_id, principal, guild_id, channel_id, patch):
        self._assert_local_principal(client_id, principal, write=True)
        with self._lock(client_id):
            current = self.status(client_id, principal)
            if not current["enabled"]:
                return storage.change_channel(self.local_root(client_id), client_id, guild_id, channel_id, patch)
            self._assert_principal(client_id, principal, write=True)
            state = shared_storage.export_state(self.cache_for(client_id), client_id)
            found = False
            for entry in state["guilds"]:
                if entry["guild"]["id"] == guild_id:
                    for channel in entry["channels"]:
                        if channel["id"] == channel_id:
                            channel.update(patch.model_dump(include=patch.model_fields_set))
                            channel["edited_fields"] = sorted(set(channel["edited_fields"]) | patch.model_fields_set)
                            found = True
            if not found:
                raise storage.CofreError("Canal nao encontrado.", 404)
            self._publish_state(client_id, principal, state, current["revision"])
            return storage.change_channel(self.cache_for(client_id), client_id, guild_id, channel_id, patch)

    def import_archive(self, client_id, principal, source, filename):
        self._assert_local_principal(client_id, principal, write=True)
        with self._lock(client_id):
            current = self.status(client_id, principal)
            if not current["enabled"]:
                return storage.import_archive(self.local_root(client_id), client_id, source, filename)
            self._assert_principal(client_id, principal, write=True)
            with tempfile.TemporaryDirectory(prefix="jk-cofre-shared-") as temporary:
                staging = Path(temporary)
                state = shared_storage.export_state(self.cache_for(client_id), client_id)
                shared_storage.store_state(staging, client_id, state, current["revision"])
                result = storage.import_archive(staging, client_id, source, filename)
                state = shared_storage.export_state(staging, client_id)
                self._publish_state(client_id, principal, state, current["revision"])
                return result
