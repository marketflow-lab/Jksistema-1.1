"""Private, bounded Cofre file transport; no Firebase or public bearer URLs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

import requests

from backend.services.transport_security import configure_requests_session
from . import storage

MAX_FILE_BYTES = 10 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}")


def cloud_url(service, asset_id, filename):
    return service.url + "/v1/assets/" + asset_id + "/" + quote(filename, safe="!~*'()")


def upload(service, principal, source, filename, request_id, guild_id, channel_id):
    service._assert_principal(principal["client_id"], principal)
    filename = storage._safe_filename(filename)
    source.seek(0)
    digest, size = hashlib.sha256(), 0
    while chunk := source.read(64 * 1024):
        size += len(chunk)
        if size > MAX_FILE_BYTES:
            raise storage.CofreError("Cada arquivo deve ter no maximo 10 MB.", 413)
        digest.update(chunk)
    if not size:
        raise storage.CofreError("O arquivo esta vazio.", 422)
    source.seek(0)
    _, record = service._remote("POST", "/v1/assets", principal, {
        "request_id": request_id, "guild_id": guild_id, "channel_id": channel_id,
        "filename": filename, "size": size, "sha256": digest.hexdigest()})
    asset_id = record.get("asset_id")
    if (not isinstance(asset_id, str) or not _HEX.fullmatch(asset_id)
            or record.get("filename") != filename or record.get("sha256") != digest.hexdigest()
            or record.get("size") != size or record.get("url") != cloud_url(service, asset_id, filename)):
        raise storage.CofreError("Confirmacao do anexo invalida.", 502)
    if record.get("ready") is not True:
        try:
            with requests.Session() as session:
                configure_requests_session(session)
                with session.put(service.url + "/v1/assets/" + asset_id + "/content", data=source,
                    headers={"Authorization": "Bearer " + principal["capability"],
                             "Content-Type": "application/octet-stream", "Content-Length": str(size)},
                    timeout=(3.05, 30), allow_redirects=False, stream=True) as response:
                    if response.status_code in {401, 403}:
                        from .shared import CofreSyncUnavailable
                        raise CofreSyncUnavailable()
                    if response.status_code not in {200, 201}:
                        raise storage.CofreError("Nao foi possivel enviar o anexo. Tente novamente.", 503)
                    raw = bytearray()
                    for chunk in response.iter_content(8192):
                        raw.extend(chunk)
                        if len(raw) > 8192:
                            raise storage.CofreError("Confirmacao do anexo invalida.", 502)
                    record = json.loads(raw, object_pairs_hook=storage._json_object)
                    if not isinstance(record, dict):
                        raise ValueError
        except (requests.RequestException, ValueError, UnicodeError, RecursionError):
            raise storage.CofreError("Nao foi possivel enviar o anexo. Tente novamente.", 503) from None
    if (record.get("asset_id") != asset_id or record.get("sha256") != digest.hexdigest()
            or record.get("size") != size or record.get("filename") != filename
            or record.get("url") != cloud_url(service, asset_id, filename) or record.get("ready") is not True):
        raise storage.CofreError("Confirmacao do anexo invalida.", 502)
    return record


def upload_request_id(request_id, index):
    value = hashlib.sha256((request_id + ":" + str(index)).encode()).digest()[:16]
    return str(uuid.UUID(bytes=value, version=4))


def _reference(service, client_id, guild_id, asset_id, filename):
    if not _HEX.fullmatch(asset_id) or filename != storage._safe_filename(filename):
        raise storage.CofreError("Anexo nao encontrado.", 404)
    root = service.read_root(client_id)
    channels = storage.list_channels(root, client_id, guild_id)["canais"]
    visible = {channel["id"] for channel in channels}
    connection = storage._connect(storage._tenant_dir(root, client_id))
    if connection is None:
        raise storage.CofreError("Anexo nao encontrado.", 404)
    local_url = f"/api/cofre/arquivos/{guild_id}/{asset_id}/{quote(filename)}"
    canonical = cloud_url(service, asset_id, filename)
    with closing(connection):
        for row in connection.execute("SELECT channel_id,payload FROM messages WHERE guild_id=?", (guild_id,)):
            if row["channel_id"] not in visible:
                continue
            for attachment in json.loads(row["payload"]).get("attachments", []):
                if (attachment.get("filename") == filename and
                        (attachment.get("local_url") == local_url or attachment.get("url") == canonical)):
                    return attachment
    raise storage.CofreError("Anexo nao encontrado.", 404)


def cached(service, client_id, principal, guild_id, asset_id, filename):
    service._assert_local_principal(client_id, principal)
    reference = _reference(service, client_id, guild_id, asset_id, filename)
    if reference.get("local_url"):
        try:
            return storage.get_asset(service.read_root(client_id), client_id, guild_id, asset_id, filename)
        except storage.CofreError as error:
            if error.status_code != 404:
                raise
            try:
                return storage.get_asset(service.local_root(client_id), client_id, guild_id, asset_id, filename)
            except storage.CofreError as error:
                if error.status_code != 404:
                    raise
    directory = service.cache_for(client_id) / "private_files"
    storage._assert_safe(directory)
    path, meta = directory / asset_id, directory / (asset_id + ".json")
    for candidate in (path, meta):
        storage._assert_safe(candidate)
    if path.is_file() and meta.is_file():
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            if (path.stat().st_size <= MAX_FILE_BYTES and path.stat().st_size == info["size"]
                    and hashlib.sha256(path.read_bytes()).hexdigest() == info["sha256"]):
                return path
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return None


def fetch(service, client_id, principal, guild_id, asset_id, filename):
    service._assert_principal(client_id, principal)
    reference = _reference(service, client_id, guild_id, asset_id, filename)
    if reference.get("url") != cloud_url(service, asset_id, filename):
        raise storage.CofreError("Anexo local indisponivel.", 404)
    directory = service.cache_for(client_id) / "private_files"
    storage._assert_safe(directory)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with requests.Session() as session:
            configure_requests_session(session)
            with session.get(cloud_url(service, asset_id, filename),
                    headers={"Authorization": "Bearer " + principal["capability"]},
                    stream=True, timeout=(3.05, 30), allow_redirects=False) as response:
                if response.status_code in {401, 403}:
                    from .shared import CofreSyncUnavailable
                    raise CofreSyncUnavailable()
                if response.status_code != 200:
                    raise storage.CofreError("Anexo temporariamente indisponivel.", 503)
                expected = response.headers.get("X-Cofre-SHA256", "")
                if not _HEX.fullmatch(expected):
                    raise storage.CofreError("Confirmacao do anexo invalida.", 502)
                size, digest = 0, hashlib.sha256()
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
                    temporary = Path(output.name)
                    for chunk in response.iter_content(64 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE_BYTES:
                            raise storage.CofreError("Anexo maior que o permitido.", 502)
                        digest.update(chunk)
                        output.write(chunk)
                if digest.hexdigest() != expected:
                    raise storage.CofreError("Conteudo do anexo invalido.", 502)
        path = directory / asset_id
        storage._assert_safe(path)
        os.replace(temporary, path)
        temporary = None
        meta = directory / (asset_id + ".json")
        storage._assert_safe(meta)
        meta.write_text(storage._dump({"sha256": expected, "size": size}), encoding="utf-8")
        return path
    except requests.RequestException:
        raise storage.CofreError("Nao foi possivel consultar o anexo.", 503) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
