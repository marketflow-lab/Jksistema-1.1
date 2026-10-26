"""Company sharing over D1; local browsing never calls Firebase or the network."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

from backend.services.transport_security import configure_requests_session
from . import posting, shared_assets, shared_storage, storage

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
        self._metadata_checks: set[str] = set()

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
                "can_publish": "cofre:write" in principal.get("scope", []),
                "can_post": "cofre:read" in principal.get("scope", []),
                "is_admin": "cofre:write" in principal.get("scope", [])}

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
                             "X-JK-Cofre-Metadata": "1",
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
                        if len(raw) > shared_storage.MAX_STATE_BYTES + 2 * 1024 * 1024 + 8192:
                            raise storage.CofreError("Resposta do Cofre maior que o permitido.", 502)
                    if status == 401:
                        raise CofreSyncUnavailable()
                    if status == 403:
                        if path.startswith("/v1/state"):
                            raise CofreSyncUnavailable()
                        raise storage.CofreError("Somente o autor ou um administrador pode realizar esta acao.", 403)
                    if status == 409:
                        raise storage.CofreError("Sincronize antes de publicar. Outra pessoa alterou o Cofre.", 409)
                    if status in {400, 404, 413, 415, 422, 429, 507}:
                        raise storage.CofreError("A operacao do Cofre nao pode ser concluida. Confira os dados e limites do envio.", status)
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
            after = current["revision"] if client_id in self._metadata_checks else 0
            code, response = self._remote("GET", "/v1/state?after=" + str(after), principal)
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
            if revision > current["revision"] or response.get("metadata") is not None:
                shared_storage.store_state(self.cache_for(client_id), client_id, state, revision,
                                           metadata=response.get("metadata"))
            if response.get("metadata") is not None:
                self._metadata_checks.add(client_id)
            self._checks[client_id] = now
            return {**self.status(client_id, principal), "changed": revision > current["revision"]}

    def _publish_state(self, client_id: str, principal: dict, state: dict, expected_revision: int, *, metadata=None):
        self._assert_principal(client_id, principal, write=True)
        if not self.url:
            raise storage.CofreError("Compartilhamento nao configurado.", 503)
        state = shared_storage.validate_state(state)
        try:
            body = {"expected_revision": expected_revision, "state": state}
            if metadata is not None:
                body["metadata"] = {"version": 1, "ownership": metadata.get("ownership", []),
                                    "deleted_channels": metadata.get("deleted_channels", [])}
            _, response = self._remote("PUT", "/v1/state", principal, body)
        except storage.CofreError as exc:
            if exc.status_code == 409:
                self._checks.pop(client_id, None)
            raise
        if type(response.get("revision")) is not int or response["revision"] != expected_revision + 1:
            raise storage.CofreError("Confirmacao de publicacao invalida. Sincronize o Cofre.", 502)
        shared_storage.store_state(self.cache_for(client_id), client_id, state, response["revision"],
                                   metadata=response.get("metadata", metadata))
        if response.get("metadata") is not None:
            self._metadata_checks.add(client_id)
        self._checks[client_id] = time.monotonic()
        return {**self.status(client_id, principal), "changed": True}

    def publish(self, client_id: str, principal: dict):
        self._assert_principal(client_id, principal, write=True)
        with self._lock(client_id):
            current = self.status(client_id, principal)
            source_root = self.read_root(client_id)

            def resolve(guild_id, channel_id, message_id, attachment):
                local_url = attachment.get("local_url", "")
                match = re.fullmatch(r"/api/cofre/arquivos/" + re.escape(guild_id) + r"/([0-9a-f]{64})/[^/]+", local_url)
                if match is None:
                    raise storage.CofreError("Anexo local nao encontrado para publicacao.", 422)
                filename = attachment["filename"]
                path = storage.get_asset(source_root, client_id, guild_id, match[1], filename)
                request_id = str(uuid.UUID(bytes=hashlib.sha256(
                    (principal["username"] + "\0" + guild_id + "\0" + channel_id + "\0" + message_id + "\0" + local_url).encode()).digest()[:16], version=4))
                with path.open("rb") as stream:
                    record = shared_assets.upload(self, principal, stream, filename, request_id, guild_id, channel_id)
                    return {"url": record["url"], "content_type": record.get("content_type", "application/octet-stream")}

            state = shared_storage.export_state(source_root, client_id, asset_url_resolver=resolve)
            metadata = shared_storage.export_metadata(source_root, client_id)
            return self._publish_state(client_id, principal, state, current["revision"], metadata=metadata)

    def project_messages(self, client_id, principal, guild_id, channel_id, data):
        permissions = posting.per_message_permissions(self.read_root(client_id), client_id, guild_id, channel_id, principal)
        for message in data.get("mensagens", []):
            message.update(permissions.get(message["id"], {"owner_username": None,
                "can_delete": posting.is_admin(principal), "can_edit": posting.is_admin(principal)}))
            for attachment in message.get("attachments", []):
                if not self.url:
                    continue
                prefix = self.url + "/v1/assets/"
                value = attachment.get("url", "")
                if value.startswith(prefix):
                    try:
                        if attachment["filename"] != storage._safe_filename(attachment["filename"]):
                            continue
                    except storage.CofreError:
                        continue
                    parts = value[len(prefix):].split("/")
                    if (len(parts) == 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0])
                            and value == shared_assets.cloud_url(self, parts[0], attachment["filename"])):
                        attachment["local_url"] = f"/api/cofre/arquivos/{guild_id}/{parts[0]}/{quote(attachment['filename'], safe='')}"
        return data

    def _mutate(self, client_id, principal, body):
        self._assert_principal(client_id, principal)
        current = self.status(client_id, principal)
        body = {"request_id": str(uuid.uuid4()), "expected_revision": current["revision"], **body}
        try:
            _, response = self._remote("POST", "/v1/mutations", principal, body)
        except storage.CofreError as error:
            if error.status_code == 409:
                self._checks.pop(client_id, None)
            raise
        revision = response.get("revision")
        if type(revision) is not int or revision < current["revision"] or not isinstance(response.get("metadata"), dict):
            raise storage.CofreError("Confirmacao da alteracao invalida.", 502)
        shared_storage.store_state(self.cache_for(client_id), client_id,
            shared_storage.validate_state(response.get("state")), revision, metadata=response["metadata"])
        self._checks[client_id] = time.monotonic()
        self._metadata_checks.add(client_id)
        return response

    def create_message(self, client_id, principal, guild_id, channel_id, payload, *, attachments=()):
        self._assert_local_principal(client_id, principal)
        with self._lock(client_id):
            if not self.status(client_id, principal)["enabled"]:
                result = posting.create_message(self.local_root(client_id), client_id, guild_id, channel_id,
                                                payload, principal, attachments=attachments)
            else:
                self._assert_principal(client_id, principal)
                if len(attachments) > posting.MAX_FILES:
                    raise storage.CofreError("Envie no maximo cinco arquivos.", 422)
                asset_ids = []
                for index, attachment in enumerate(attachments):
                    record = shared_assets.upload(self, principal, attachment.source, attachment.filename,
                        shared_assets.upload_request_id(payload.request_id, index), guild_id, channel_id)
                    asset_ids.append(record["asset_id"])
                response = self._mutate(client_id, principal, {"request_id": payload.request_id,
                    "action": "message.create", "guild_id": guild_id, "channel_id": channel_id,
                    "content": payload.content, "links": [link.model_dump() for link in payload.links],
                    "asset_ids": asset_ids})
                message_id = response.get("message_id")
                with closing(storage._connect(storage._tenant_dir(self.cache_for(client_id), client_id))) as connection:
                    result = {**posting._message_result(connection, guild_id, channel_id, message_id, principal),
                              "request_id": payload.request_id}
            result["mensagem"] = self.project_messages(client_id, principal, guild_id, channel_id,
                {"mensagens": [result["mensagem"]]})["mensagens"][0]
            return {**self.status(client_id, principal), **result, "success": True}

    def set_message_deleted(self, client_id, principal, guild_id, channel_id, message_id, *, deleted=True):
        self._assert_local_principal(client_id, principal)
        with self._lock(client_id):
            # The local cache narrows access before a cloud request; the Worker
            # independently checks its immutable signed-user ownership record.
            connection = storage._connect(storage._tenant_dir(self.read_root(client_id), client_id))
            if connection is None:
                raise storage.CofreError("Mensagem nao encontrada.", 404)
            with closing(connection):
                storage._channel_record(connection, guild_id, channel_id)
                storage._message_record(connection, guild_id, channel_id, message_id)
            permission = posting.per_message_permissions(self.read_root(client_id), client_id, guild_id, channel_id, principal).get(message_id)
            if not permission or not permission["can_delete"]:
                raise storage.CofreError("Somente o autor ou um administrador pode excluir esta mensagem.", 403)
            if not self.status(client_id, principal)["enabled"]:
                return posting.set_message_deleted(self.local_root(client_id), client_id, guild_id, channel_id,
                                                    message_id, principal, deleted=deleted)
            self._mutate(client_id, principal, {"action": "message.delete" if deleted else "message.restore",
                "guild_id": guild_id, "channel_id": channel_id, "message_id": message_id})
            with closing(storage._connect(storage._tenant_dir(self.cache_for(client_id), client_id))) as connection:
                return posting._message_result(connection, guild_id, channel_id, message_id, principal)

    def set_channel_deleted(self, client_id, principal, guild_id, channel_id, *, deleted=True):
        self._assert_local_principal(client_id, principal, write=True)
        if not deleted:
            raise storage.CofreError("Restauracao de canal nao disponivel nesta versao.", 422)
        with self._lock(client_id):
            if not self.status(client_id, principal)["enabled"]:
                return posting.set_channel_deleted(self.local_root(client_id), client_id, guild_id, channel_id, principal)
            self._mutate(client_id, principal, {"action": "channel.delete", "guild_id": guild_id, "channel_id": channel_id})
            return {"success": True, "channel_id": channel_id, "deleted": True}

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
                shared_storage.store_state(staging, client_id, state, current["revision"],
                    metadata=shared_storage.export_metadata(self.cache_for(client_id), client_id))
                result = storage.import_archive(staging, client_id, source, filename)
                state = shared_storage.export_state(staging, client_id)
                self._publish_state(client_id, principal, state, current["revision"])
                return result
