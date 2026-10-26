"""JK-authored local posts; immutable ownership is never imported display text."""
from __future__ import annotations

import hashlib
import errno
import os
import shutil
import tempfile
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from pydantic import Field, StrictStr, field_validator

from . import storage

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 5
_RASTER_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


class CreateMessage(storage._Model):
    request_id: StrictStr
    content: StrictStr = Field(default="", max_length=32000)
    links: list[storage.Link] = Field(default_factory=list, max_length=100)

    @field_validator("request_id")
    @classmethod
    def valid_request_id(cls, value):
        parsed = uuid.UUID(value)
        if parsed.version != 4 or parsed.variant != uuid.RFC_4122:
            raise ValueError("Identificador da mensagem invalido.")
        return str(parsed)

    @field_validator("links")
    @classmethod
    def valid_links(cls, values):
        if any(not link.url or any(ord(char) <= 32 or ord(char) == 127 for char in link.url) for link in values):
            raise ValueError("Informe links HTTP ou HTTPS validos.")
        return values


@dataclass(frozen=True)
class PreparedAttachment:
    source: BinaryIO
    filename: str
    content_type: str | None = None  # The actual bytes, not this hint, determine previews.


def is_admin(actor):
    return isinstance(actor, dict) and "cofre:write" in actor.get("scope", [])


def require_actor(client_id, actor, *, admin=False):
    if (not isinstance(actor, dict) or actor.get("client_id") != client_id
            or not isinstance(actor.get("username"), str) or not actor["username"].strip()
            or actor["username"] != actor["username"].strip()
            or len(actor["username"]) > 512 or any(ord(c) < 32 for c in actor["username"])
            or not isinstance(actor.get("scope"), list) or "cofre:read" not in actor["scope"]
            or type(actor.get("exp")) is not int or actor["exp"] <= time.time()
            or admin and not is_admin(actor)):
        raise storage.CofreError("Seu usuario nao possui permissao para esta operacao.", 403)
    return actor["username"]


def _sniff(prefix):
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _prepare_attachments(attachments, staging, guild_id):
    if not isinstance(attachments, (list, tuple)) or len(attachments) > MAX_FILES:
        raise storage.CofreError("Envie no maximo cinco arquivos.", 413)
    result, fingerprints, blobs = [], [], []
    for index, attachment in enumerate(attachments):
        if isinstance(attachment, PreparedAttachment):
            if (not isinstance(attachment.filename, str) or not attachment.filename
                    or len(attachment.filename) > 512):
                raise storage.CofreError("Nome de arquivo invalido.", 422)
            filename = storage._safe_filename(attachment.filename)
            staged = staging / str(index)
            size, prefix, digest = 0, b"", hashlib.sha256()
            try:
                attachment.source.seek(0)
                with staged.open("wb") as output:
                    while True:
                        chunk = attachment.source.read(64 * 1024)
                        if not isinstance(chunk, bytes):
                            raise storage.CofreError("Arquivo invalido.", 422)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_FILE_BYTES:
                            raise storage.CofreError("Cada arquivo deve ter no maximo 10 MiB.", 413)
                        prefix = (prefix + chunk)[:16] if len(prefix) < 16 else prefix
                        digest.update(chunk)
                        output.write(chunk)
            except (OSError, AttributeError, TypeError):
                raise storage.CofreError("Nao foi possivel preparar o arquivo.", 422) from None
            hexadecimal = digest.hexdigest()
            content_type = _sniff(prefix)
            value = {"url": "", "filename": filename, "content_type": content_type,
                     "local_url": f"/api/cofre/arquivos/{guild_id}/{hexadecimal}/{quote(filename)}"}
            fingerprints.append({"sha256": hexadecimal, "size": size,
                                 "filename": filename, "content_type": content_type})
            blobs.append((staged, hexadecimal, filename))
        else:
            # Only the trusted HTTP/service adapter supplies canonical Worker URLs.
            try:
                validated = attachment if isinstance(attachment, storage.Attachment) else storage.Attachment.model_validate(attachment)
                if (not validated.url or validated.archive_path
                        or validated.filename != storage._safe_filename(validated.filename)):
                    raise ValueError("invalid_uploaded_attachment")
                value = validated.model_dump(exclude_none=True)
                value.pop("archive_path", None)
                if value.get("content_type") not in _RASTER_TYPES:
                    value["content_type"] = "application/octet-stream"
            except (TypeError, ValueError):
                raise storage.CofreError("Anexo invalido.", 422) from None
            fingerprints.append(value)
        result.append(value)
    return result, fingerprints, blobs


def _place_blob(staged: Path, target: Path):
    """Publish a complete blob atomically even when TEMP is on another volume."""
    try:
        os.replace(staged, target)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
            raise
    temporary = None
    try:
        # Copy to the target filesystem first. The final content-addressed name
        # is never exposed while bytes are incomplete, and Windows must close
        # the sibling file before its atomic rename.
        with tempfile.NamedTemporaryFile(mode="w+b", prefix=".jk-cofre-post-", suffix=".tmp",
                dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            with staged.open("rb") as source:
                shutil.copyfileobj(source, output, length=64 * 1024)
            output.flush()
            output.seek(0)
            if hashlib.file_digest(output, "sha256").hexdigest() != target.name:
                raise OSError(errno.EIO, "Attachment staging copy failed integrity check.")
            os.fsync(output.fileno())
        storage._assert_safe(temporary)
        storage._assert_safe(target)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _permissions_for_connection(connection, guild_id, channel_id, actor):
    deleted_channel = storage._channel_deleted(connection, guild_id, channel_id)
    return {row["id"]: {
        "owner_username": row["owner_username"],
        "can_delete": not deleted_channel and (is_admin(actor) or row["owner_username"] == actor["username"]),
        "can_edit": not deleted_channel and is_admin(actor),
    } for row in connection.execute(
        "SELECT m.id,o.owner_username FROM messages m LEFT JOIN message_ownership o "
        "ON o.guild_id=m.guild_id AND o.channel_id=m.channel_id AND o.id=m.id "
        "WHERE m.guild_id=? AND m.channel_id=?", (guild_id, channel_id))}


def per_message_permissions(info_root, client_id, guild_id, channel_id, actor):
    require_actor(client_id, actor)
    connection = storage._connect(storage._tenant_dir(info_root, client_id))
    if connection is None:
        return {}
    with closing(connection):
        return _permissions_for_connection(connection, guild_id, channel_id, actor)


def _message_result(connection, guild_id, channel_id, message_id, actor):
    message = storage._effective_message(storage._message_record(connection, guild_id, channel_id, message_id))
    message.update(_permissions_for_connection(connection, guild_id, channel_id, actor)[message_id])
    counts = storage._message_counts(connection, guild_id, channel_id)
    position = connection.execute(
        "SELECT COUNT(*) FROM messages m" + storage._MESSAGE_JOIN
        + "WHERE m.guild_id=? AND m.channel_id=? AND COALESCE(l.deleted,0)=0 "
        "AND (m.timestamp < (SELECT timestamp FROM messages WHERE guild_id=? AND channel_id=? AND id=?) "
        "OR (m.timestamp=(SELECT timestamp FROM messages WHERE guild_id=? AND channel_id=? AND id=?) AND m.id<?))",
        (guild_id, channel_id, guild_id, channel_id, message_id,
         guild_id, channel_id, message_id, message_id),
    ).fetchone()[0]
    return {"mensagem": message, **counts, "message_offset": max(position - 99, 0)}


def create_message(info_root, client_id, guild_id, channel_id, payload, actor, *, attachments=()):
    username = require_actor(client_id, actor)
    if not isinstance(payload, CreateMessage):
        payload = CreateMessage.model_validate(payload)
    storage._identifier(guild_id)
    storage._identifier(channel_id)
    if not payload.content.strip() and not payload.links and not attachments:
        raise storage.CofreError("Escreva uma mensagem ou envie um arquivo.", 422)
    directory = storage._tenant_dir(info_root, client_id)
    with storage._lock(directory), tempfile.TemporaryDirectory(prefix="jk-cofre-post-") as temporary:
        prepared, fingerprints, blobs = _prepare_attachments(attachments, Path(temporary), guild_id)
        body_digest = hashlib.sha256(storage._dump({"guild_id": guild_id, "channel_id": channel_id,
            "content": payload.content, "links": [link.model_dump() for link in payload.links],
            "attachments": fingerprints}).encode("utf-8")).hexdigest()
        created_blobs = []
        try:
            with closing(storage._editable_connection(directory)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                storage._channel_record(connection, guild_id, channel_id)
                receipt = connection.execute("SELECT * FROM posting_receipts WHERE username=? AND request_id=?",
                                             (username, payload.request_id)).fetchone()
                if receipt is not None:
                    if receipt["body_digest"] != body_digest:
                        raise storage.CofreError("Esta requisicao ja foi usada com outro conteudo.", 409)
                    return {**_message_result(connection, guild_id, channel_id, receipt["message_id"], actor),
                            "request_id": payload.request_id}
                message_id = "jk_msg_" + uuid.UUID(payload.request_id).hex
                if connection.execute("SELECT 1 FROM messages WHERE guild_id=? AND channel_id=? AND id=?",
                                      (guild_id, channel_id, message_id)).fetchone():
                    raise storage.CofreError("Identificador de mensagem ja utilizado.", 409)
                now = datetime.now(timezone.utc).isoformat()
                message = storage.Message(id=message_id, author=username, timestamp=now, content=payload.content,
                    links=payload.links, attachments=[], embeds=[], source_url="").model_dump()
                message["attachments"] = prepared
                if blobs:
                    blob_directory = directory / "assets"
                    storage._assert_safe(blob_directory)
                    blob_directory.mkdir(exist_ok=True)
                    for staged, digest, filename in blobs:
                        target = blob_directory / digest
                        storage._assert_safe(target)
                        if not target.exists():
                            _place_blob(staged, target)
                            created_blobs.append(target)
                        connection.execute("INSERT OR IGNORE INTO assets VALUES(?,?,?)", (guild_id, digest, filename))
                connection.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)", (guild_id, channel_id, message_id,
                    storage._message_order(now), storage._searchable_message(message), storage._dump(message)))
                connection.execute("INSERT INTO message_ownership VALUES(?,?,?,?,?)",
                                   (guild_id, channel_id, message_id, username, now))
                connection.execute("INSERT INTO posting_receipts VALUES(?,?,?,?,?,?)",
                                   (username, payload.request_id, guild_id, channel_id, message_id, body_digest))
                return {**_message_result(connection, guild_id, channel_id, message_id, actor),
                        "request_id": payload.request_id}
        except Exception:
            # Remove only blobs created by this failed attempt, after rollback.
            check = storage._connect(directory)
            if check is not None:
                with closing(check):
                    for path in created_blobs:
                        if not check.execute("SELECT 1 FROM assets WHERE hash=?", (path.name,)).fetchone():
                            path.unlink(missing_ok=True)
            raise


def set_message_deleted(info_root, client_id, guild_id, channel_id, message_id, actor, *, deleted=True):
    username = require_actor(client_id, actor)
    if type(deleted) is not bool:
        raise storage.CofreError("Estado de exclusao invalido.", 422)
    directory = storage._tenant_dir(info_root, client_id)
    with storage._lock(directory), closing(storage._editable_connection(directory)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        storage._channel_record(connection, guild_id, channel_id)
        storage._message_record(connection, guild_id, channel_id, message_id)
        owner = connection.execute("SELECT owner_username FROM message_ownership WHERE guild_id=? AND channel_id=? AND id=?",
                                   (guild_id, channel_id, message_id)).fetchone()
        if not is_admin(actor) and (owner is None or owner[0] != username):
            raise storage.CofreError("Somente o autor JK ou um administrador pode excluir esta mensagem.", 403)
        storage._change_message_record(connection, guild_id, channel_id, message_id, deleted=deleted)
        return _message_result(connection, guild_id, channel_id, message_id, actor)


def set_channel_deleted(info_root, client_id, guild_id, channel_id, actor, *, deleted=True):
    username = require_actor(client_id, actor, admin=True)
    if type(deleted) is not bool:
        raise storage.CofreError("Estado de exclusao invalido.", 422)
    directory = storage._tenant_dir(info_root, client_id)
    with storage._lock(directory), closing(storage._editable_connection(directory)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        storage._channel_record(connection, guild_id, channel_id, include_deleted=True)
        connection.execute("INSERT INTO channel_tombstones VALUES(?,?,?,?,?) "
                           "ON CONFLICT(guild_id,id) DO UPDATE SET deleted=excluded.deleted,"
                           "deleted_by=excluded.deleted_by,deleted_at=excluded.deleted_at",
                           (guild_id, channel_id, int(deleted), username, datetime.now(timezone.utc).isoformat()))
        channel = storage._channel_record(connection, guild_id, channel_id, include_deleted=True)
        channel.update(can_post=not deleted, can_delete=True, can_edit=not deleted)
        return {"canal": channel, "servidor": storage._guild_summary(connection, guild_id)}
