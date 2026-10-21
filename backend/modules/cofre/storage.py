"""Validated, tenant-local Discord archive. Imported text is never executable."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import threading
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, ValidationError, field_validator, model_validator

FORMAT = "jk-cofre-discord-v1"
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_ASSET_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10000
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{x}" for x in range(1, 10)), *(f"lpt{x}" for x in range(1, 10))}
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class CofreError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _identifier(value: str) -> str:
    if not _ID.fullmatch(value):
        raise ValueError("Identificador invalido.")
    return value


def _url(value: str) -> str:
    if not value:
        return value
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or any(ord(c) < 32 for c in value)):
        raise ValueError("URL deve usar HTTP ou HTTPS.")
    return value


def _archive_path(value: str) -> str:
    parts = PurePosixPath(value).parts
    if (not value or "\\" in value or ":" in value or value.startswith("/")
            or len(parts) < 2 or parts[0] != "assets"
            or any(p in {"", ".", ".."} for p in value.split("/"))
            or any(ord(c) < 32 for c in value)):
        raise ValueError("Caminho de anexo invalido.")
    return value


class Link(_Model):
    url: StrictStr = Field(max_length=16384)
    text: StrictStr = Field(default="", max_length=65536)
    _valid_url = field_validator("url")(_url)


class Attachment(_Model):
    url: StrictStr = Field(default="", max_length=16384)
    filename: StrictStr = Field(min_length=1, max_length=512)
    content_type: StrictStr | None = Field(default=None, max_length=255)
    archive_path: StrictStr | None = Field(default=None, max_length=1024)
    _valid_url = field_validator("url")(_url)

    @field_validator("archive_path")
    @classmethod
    def valid_archive(cls, value):
        return _archive_path(value) if value is not None else None


class Embed(_Model):
    text: StrictStr = Field(default="", max_length=1048576)
    links: list[Link] = Field(default_factory=list, max_length=1000)


class Message(_Model):
    id: StrictStr
    author: StrictStr = Field(default="", max_length=512)
    timestamp: StrictStr = Field(default="", max_length=128)
    content: StrictStr = Field(default="", max_length=1048576)
    links: list[Link] = Field(default_factory=list, max_length=1000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=1000)
    embeds: list[Embed] = Field(default_factory=list, max_length=1000)
    source_url: StrictStr = Field(default="", max_length=16384)
    _valid_id = field_validator("id")(_identifier)
    _valid_source = field_validator("source_url")(_url)


class Channel(_Model):
    id: StrictStr
    name: StrictStr = Field(min_length=1, max_length=512)
    category: StrictStr = Field(default="", max_length=512)
    url: StrictStr = Field(default="", max_length=16384)
    topic: StrictStr | None = Field(default=None, max_length=65536)
    messages: list[Message] = Field(default_factory=list, max_length=100000)
    history_complete: StrictBool = False
    reached_start: StrictBool | None = None
    reached_end: StrictBool | None = None
    last_capture_at: StrictStr | None = Field(default=None, max_length=128)
    kind: Literal["text", "voice"] = "text"
    _valid_id = field_validator("id")(_identifier)
    _valid_url = field_validator("url")(_url)


class Guild(_Model):
    id: StrictStr
    name: StrictStr = Field(min_length=1, max_length=512)
    _valid_id = field_validator("id")(_identifier)


class Manifest(_Model):
    format: Literal["jk-cofre-discord-v1"]
    guild: Guild
    exported_at: StrictStr = Field(max_length=128)
    channels: list[Channel] = Field(max_length=5000)


class MessagePatch(_Model):
    content: StrictStr | None = Field(default=None, max_length=1048576)
    author: StrictStr | None = Field(default=None, max_length=512)
    links: list[Link] | None = Field(default=None, max_length=1000)
    embeds: list[Embed] | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def valid_patch(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Informe ao menos um campo valido para editar a mensagem.")
        return self


class ChannelPatch(_Model):
    name: StrictStr | None = Field(default=None, min_length=1, max_length=512)
    category: StrictStr | None = Field(default=None, max_length=512)
    topic: StrictStr | None = Field(default=None, max_length=65536)

    @model_validator(mode="after")
    def valid_patch(self):
        if (not self.model_fields_set
                or any(getattr(self, key) is None for key in self.model_fields_set if key != "topic")):
            raise ValueError("Informe ao menos um campo valido para editar o canal.")
        return self


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CofreError("A importacao possui campos JSON duplicados.")
        result[key] = value
    return result


def _read_manifest(raw: bytes) -> Manifest:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise CofreError("Manifesto maior que 32 MB.", 413)
    try:
        payload = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_json_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        manifest = Manifest.model_validate(payload)
    except (UnicodeError, ValueError, ValidationError, RecursionError) as exc:
        if isinstance(exc, CofreError):
            raise
        raise CofreError("Arquivo de importacao invalido para o formato do Cofre.") from exc
    channel_ids = set()
    for channel in manifest.channels:
        if channel.id in channel_ids or len({m.id for m in channel.messages}) != len(channel.messages):
            raise CofreError("Canais ou mensagens duplicados na importacao.")
        channel_ids.add(channel.id)
    return manifest


def _assert_safe(path: Path) -> None:
    for current in (path, *path.parents):
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise CofreError("Diretorio do Cofre invalido.")


def _tenant_dir(info_root: Path, client_id: str) -> Path:
    if (not isinstance(client_id, str) or not _TENANT.fullmatch(client_id)
            or client_id.endswith(".") or client_id.split(".")[0].lower() in _RESERVED):
        raise CofreError("Cliente invalido.", 401)
    root = Path(os.path.abspath(info_root))
    directory = root / client_id / "cofre"
    _assert_safe(directory)
    _assert_safe(directory / "cofre.sqlite3")
    return directory


def _lock(directory: Path):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(directory), threading.RLock())


def _connect(directory: Path, *, writable=False):
    if writable:
        directory.mkdir(parents=True, exist_ok=True)
        _assert_safe(directory / "cofre.sqlite3")
        connection = sqlite3.connect(directory / "cofre.sqlite3", timeout=10)
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS guilds(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS channels(
                guild_id TEXT, id TEXT, position INTEGER, payload TEXT NOT NULL,
                PRIMARY KEY(guild_id,id));
            CREATE TABLE IF NOT EXISTS messages(
                guild_id TEXT, channel_id TEXT, id TEXT, timestamp TEXT,
                searchable TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(guild_id,channel_id,id));
            CREATE INDEX IF NOT EXISTS messages_channel_order
                ON messages(guild_id,channel_id,timestamp,id);
            CREATE TABLE IF NOT EXISTS assets(
                guild_id TEXT, hash TEXT, filename TEXT,
                PRIMARY KEY(guild_id,hash,filename));
            CREATE TABLE IF NOT EXISTS message_local(
                guild_id TEXT, channel_id TEXT, id TEXT,
                overrides TEXT NOT NULL DEFAULT '{}', deleted INTEGER NOT NULL DEFAULT 0,
                searchable TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(guild_id,channel_id,id));
            CREATE TABLE IF NOT EXISTS channel_local(
                guild_id TEXT, id TEXT, overrides TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(guild_id,id));
        """)
    else:
        target = directory / "cofre.sqlite3"
        if not target.exists():
            return None
        connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=10)
        existing_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"message_local", "channel_local"}.issubset(existing_tables):
            connection.close()
            with _lock(directory), closing(_connect(directory, writable=True)):
                pass
            connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=10)
        connection.execute("BEGIN")
    connection.row_factory = sqlite3.Row
    return connection


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _message_order(timestamp: str) -> str:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return ""


def _safe_filename(value):
    name = re.sub(r'[^A-Za-z0-9._-]', "_", value)[:160].strip(".")
    if not name or name.split(".")[0].lower() in _RESERVED:
        name = "anexo_" + name
    return name



def _attachment_identity(attachment: dict) -> tuple[str, str]:
    url = attachment.get("url", "")
    parsed = urlsplit(url)
    if (parsed.hostname in {"cdn.discordapp.com", "media.discordapp.net"}
            and parsed.path.startswith("/attachments/")):
        # Discord renews signed CDN query parameters without changing the attachment.
        url = parsed.netloc.casefold() + parsed.path
    return url, attachment.get("filename", "")


def _merge_attachments(current: list[dict], previous: list[dict]) -> list[dict]:
    merged = {}
    for attachment in current:
        key = _attachment_identity(attachment)
        if key not in merged or (attachment.get("local_url") and not merged[key].get("local_url")):
            merged[key] = dict(attachment)
    for attachment in previous:
        if not attachment.get("local_url"):
            continue
        key = _attachment_identity(attachment)
        if key not in merged:
            merged[key] = dict(attachment)
        elif not merged[key].get("local_url"):
            merged[key]["local_url"] = attachment["local_url"]
            if not merged[key].get("content_type"):
                merged[key]["content_type"] = attachment.get("content_type")
    return list(merged.values())


def _searchable_message(payload: dict) -> str:
    parts = [payload["author"], payload["content"]]
    parts.extend(link["text"] + " " + link["url"] for link in payload["links"])
    parts.extend(attachment["filename"] for attachment in payload["attachments"])
    for embed in payload["embeds"]:
        parts.append(embed["text"])
        parts.extend(link["text"] + " " + link["url"] for link in embed["links"])
    return "\n".join(parts).casefold()


def _extract_package(source: BinaryIO, filename: str, staging: Path):
    source.seek(0, os.SEEK_END)
    size = source.tell()
    source.seek(0)
    if size > MAX_UPLOAD_BYTES:
        raise CofreError("Arquivo maior que 512 MB.", 413)
    if filename.lower().endswith(".json"):
        if size > MAX_MANIFEST_BYTES:
            raise CofreError("Manifesto maior que 32 MB.", 413)
        manifest = _read_manifest(source.read(MAX_MANIFEST_BYTES + 1))
        return manifest, {}
    if not filename.lower().endswith(".zip"):
        raise CofreError("Selecione um arquivo JSON ou ZIP do Cofre.")
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise CofreError("ZIP possui arquivos demais.", 413)
            names, expanded = set(), 0
            for entry in entries:
                if entry.orig_filename != entry.filename:
                    raise CofreError("ZIP possui caminho nao canonico.")
                name = entry.filename.rstrip("/") if entry.is_dir() else entry.filename
                canonical = name.casefold()
                if canonical in names:
                    raise CofreError("ZIP possui caminhos duplicados.")
                names.add(canonical)
                if entry.flag_bits & 1 or stat.S_ISLNK(entry.external_attr >> 16):
                    raise CofreError("ZIP criptografado ou com links nao permitido.")
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise CofreError("ZIP possui entrada especial nao permitida.")
                if name != "manifest.json" and not (name == "assets" and entry.is_dir()):
                    _archive_path(name)
                if name == "manifest.json" and entry.is_dir():
                    raise CofreError("Manifesto ausente no ZIP.")
                expanded += entry.file_size
                max_size = MAX_MANIFEST_BYTES if name == "manifest.json" else MAX_ASSET_BYTES
                if (entry.file_size > max_size or expanded > MAX_EXPANDED_BYTES
                        or entry.file_size > max(1024 * 1024, entry.compress_size * 200)):
                    raise CofreError("ZIP excede os limites de expansao.", 413)
            if "manifest.json" not in archive.namelist():
                raise CofreError("ZIP deve conter manifest.json na raiz.")
            with archive.open("manifest.json") as handle:
                manifest = _read_manifest(handle.read(MAX_MANIFEST_BYTES + 1))
            needed = {a.archive_path for c in manifest.channels for m in c.messages
                      for a in m.attachments if a.archive_path}
            assets = {}
            for name in needed:
                if name not in archive.namelist() or archive.getinfo(name).is_dir():
                    raise CofreError("Anexo declarado ausente no ZIP.")
                target = staging / str(len(assets))
                digest = hashlib.sha256()
                count = 0
                with archive.open(name) as incoming, target.open("xb") as outgoing:
                    while chunk := incoming.read(1024 * 1024):
                        count += len(chunk)
                        if count > MAX_ASSET_BYTES:
                            raise CofreError("Anexo maior que 128 MB.", 413)
                        digest.update(chunk)
                        outgoing.write(chunk)
                assets[name] = (digest.hexdigest(), target)
            return manifest, assets
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, ValueError) as exc:
        if isinstance(exc, CofreError):
            raise
        raise CofreError("ZIP invalido ou incompleto.") from exc


def import_archive(info_root: Path, client_id: str, source: BinaryIO, filename: str) -> dict:
    directory = _tenant_dir(info_root, client_id)
    with _lock(directory):
        # Validation and decompression finish before any existing archive changes.
        with tempfile.TemporaryDirectory(prefix="jk-cofre-import-") as temporary:
            manifest, imported_assets = _extract_package(source, filename, Path(temporary))
            needed = {a.archive_path for c in manifest.channels for m in c.messages
                      for a in m.attachments if a.archive_path}
            if needed - imported_assets.keys():
                raise CofreError("JSON com anexos locais deve ser importado dentro de ZIP.")
            guild_id = manifest.guild.id
            directory.mkdir(parents=True, exist_ok=True)
            blobs = directory / "assets"
            _assert_safe(blobs)
            blobs.mkdir(exist_ok=True)
            for digest, staged in imported_assets.values():
                destination = blobs / digest
                _assert_safe(destination)
                if not destination.exists():
                    os.replace(staged, destination)
            with closing(_connect(directory, writable=True)) as connection, connection:
                guild = {**manifest.guild.model_dump(), "exported_at": manifest.exported_at}
                connection.execute("INSERT INTO guilds VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                                   (guild_id, _dump(guild)))
                inserted = 0
                for position, channel in enumerate(manifest.channels):
                    channel_data = channel.model_dump(exclude={"messages", "reached_start", "reached_end", "last_capture_at"})
                    connection.execute("INSERT INTO channels VALUES(?,?,?,?) ON CONFLICT(guild_id,id) DO UPDATE SET position=excluded.position,payload=excluded.payload",
                                       (guild_id, channel.id, position, _dump(channel_data)))
                    for message in channel.messages:
                        payload = message.model_dump()
                        for attachment in payload["attachments"]:
                            archive_path = attachment.get("archive_path")
                            if archive_path:
                                digest = imported_assets[archive_path][0]
                                safe_name = _safe_filename(attachment["filename"])
                                attachment["local_url"] = f"/api/cofre/arquivos/{guild_id}/{digest}/{quote(safe_name)}"
                                connection.execute("INSERT OR IGNORE INTO assets VALUES(?,?,?)", (guild_id, digest, safe_name))
                            attachment.pop("archive_path", None)
                        previous = connection.execute("SELECT payload FROM messages WHERE guild_id=? AND channel_id=? AND id=?",
                                                      (guild_id, channel.id, message.id)).fetchone()
                        old_attachments = json.loads(previous["payload"]).get("attachments", []) if previous else []
                        # Partial DOM recaptures may omit attachments already copied into this archive.
                        payload["attachments"] = _merge_attachments(payload["attachments"], old_attachments)
                        if not previous:
                            inserted += 1
                        searchable = _searchable_message(payload)
                        connection.execute("INSERT INTO messages VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,channel_id,id) DO UPDATE SET timestamp=excluded.timestamp,searchable=excluded.searchable,payload=excluded.payload",
                                           (guild_id, channel.id, message.id, _message_order(message.timestamp), searchable, _dump(payload)))
                        local = connection.execute("SELECT overrides FROM message_local WHERE guild_id=? AND channel_id=? AND id=?",
                                                   (guild_id, channel.id, message.id)).fetchone()
                        if local:
                            effective = {**payload, **json.loads(local["overrides"])}
                            connection.execute("UPDATE message_local SET searchable=? WHERE guild_id=? AND channel_id=? AND id=?",
                                               (_searchable_message(effective), guild_id, channel.id, message.id))
                summary = _guild_summary(connection, guild_id)
            return {"servidor": summary, "imported_messages": inserted, "total_messages": summary["archive_message_count"]}



_MESSAGE_JOIN = " LEFT JOIN message_local l ON l.guild_id=m.guild_id AND l.channel_id=m.channel_id AND l.id=m.id "


def _message_counts(connection, guild_id, channel_id=None):
    where, args = "m.guild_id=?", [guild_id]
    if channel_id is not None:
        where += " AND m.channel_id=?"
        args.append(channel_id)
    row = connection.execute("SELECT COUNT(*),COALESCE(SUM(COALESCE(l.deleted,0)),0) FROM messages m" + _MESSAGE_JOIN + "WHERE " + where, args).fetchone()
    return {"total_ativos": row[0] - row[1], "total_excluidos": row[1]}


def _effective_message(row):
    overrides = json.loads(row["overrides"]) if row["overrides"] else {}
    return {**json.loads(row["payload"]), **overrides,
            "edited": bool(overrides), "deleted": bool(row["deleted"])}


def _channel_record(connection, guild_id, channel_id):
    row = connection.execute("SELECT c.payload,l.overrides FROM channels c LEFT JOIN channel_local l ON l.guild_id=c.guild_id AND l.id=c.id WHERE c.guild_id=? AND c.id=?",
                             (guild_id, channel_id)).fetchone()
    if not row:
        raise CofreError("Canal nao encontrado.", 404)
    overrides = json.loads(row["overrides"]) if row["overrides"] else {}
    counts = _message_counts(connection, guild_id, channel_id)
    return {**json.loads(row["payload"]), **overrides, "edited": bool(overrides),
            "message_count": counts["total_ativos"], "deleted_message_count": counts["total_excluidos"],
            "archive_message_count": counts["total_ativos"] + counts["total_excluidos"]}


def _guild_summary(connection, guild_id):
    row = connection.execute("SELECT payload FROM guilds WHERE id=?", (guild_id,)).fetchone()
    if not row:
        raise CofreError("Servidor nao encontrado.", 404)
    result = json.loads(row["payload"])
    channel_rows = connection.execute("SELECT payload FROM channels WHERE guild_id=?", (guild_id,)).fetchall()
    result["channel_count"] = len(channel_rows)
    result["history_complete"] = bool(channel_rows) and all(json.loads(r["payload"])["history_complete"] for r in channel_rows)
    counts = _message_counts(connection, guild_id)
    result["message_count"] = counts["total_ativos"]
    result["deleted_message_count"] = counts["total_excluidos"]
    result["archive_message_count"] = counts["total_ativos"] + counts["total_excluidos"]
    result["attachment_count"] = sum(len(json.loads(r[0]).get("attachments", [])) for r in
                                     connection.execute("SELECT m.payload FROM messages m" + _MESSAGE_JOIN + "WHERE m.guild_id=? AND COALESCE(l.deleted,0)=0", (guild_id,)))
    return result


def list_guilds(info_root: Path, client_id: str):
    directory = _tenant_dir(info_root, client_id)
    connection = _connect(directory)
    if connection is None:
        return {"servidores": []}
    with closing(connection):
        return {"servidores": [_guild_summary(connection, row[0]) for row in
                                connection.execute("SELECT id FROM guilds ORDER BY id").fetchall()]}


def list_channels(info_root: Path, client_id: str, guild_id: str):
    directory = _tenant_dir(info_root, client_id)
    connection = _connect(directory)
    if connection is None:
        raise CofreError("Servidor nao encontrado.", 404)
    with closing(connection):
        summary = _guild_summary(connection, guild_id)
        channels = [_channel_record(connection, guild_id, row[0]) for row in
                    connection.execute("SELECT id FROM channels WHERE guild_id=? ORDER BY position,id", (guild_id,)).fetchall()]
        return {"servidor": summary, "canais": channels}


def list_messages(info_root: Path, client_id: str, guild_id: str, channel_id: str,
                  *, q="", offset=0, limit=100, excluidos=False):
    if offset < 0 or limit < 1 or limit > 200 or len(q) > 500:
        raise CofreError("Paginacao ou busca invalida.")
    connection = _connect(_tenant_dir(info_root, client_id))
    if connection is None:
        raise CofreError("Canal nao encontrado.", 404)
    with closing(connection):
        if not connection.execute("SELECT 1 FROM channels WHERE guild_id=? AND id=?", (guild_id, channel_id)).fetchone():
            raise CofreError("Canal nao encontrado.", 404)
        counts = _message_counts(connection, guild_id, channel_id)
        where = "m.guild_id=? AND m.channel_id=? AND COALESCE(l.deleted,0)=?"
        args = [guild_id, channel_id, int(excluidos)]
        if q:
            where += " AND instr(COALESCE(l.searchable,m.searchable),?)>0"
            args.append(q.casefold())
        total = connection.execute("SELECT COUNT(*) FROM messages m" + _MESSAGE_JOIN + "WHERE " + where, args).fetchone()[0]
        rows = connection.execute("SELECT m.payload,l.overrides,COALESCE(l.deleted,0) deleted FROM messages m" + _MESSAGE_JOIN + "WHERE " + where + " ORDER BY m.timestamp,m.id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return {"mensagens": [_effective_message(row) for row in rows], "total": total, **counts,
                "offset": offset, "limit": limit, "has_more": offset + len(rows) < total}


def _editable_connection(directory):
    if not (directory / "cofre.sqlite3").is_file():
        raise CofreError("Item nao encontrado.", 404)
    return _connect(directory, writable=True)


def _message_record(connection, guild_id, channel_id, message_id):
    row = connection.execute("SELECT m.payload,l.overrides,COALESCE(l.deleted,0) deleted FROM messages m" + _MESSAGE_JOIN + "WHERE m.guild_id=? AND m.channel_id=? AND m.id=?",
                             (guild_id, channel_id, message_id)).fetchone()
    if not row:
        raise CofreError("Mensagem nao encontrada.", 404)
    return row


def change_message(info_root: Path, client_id: str, guild_id: str, channel_id: str,
                   message_id: str, *, patch: MessagePatch | None = None, deleted: bool | None = None):
    if patch is None and deleted is None:
        raise CofreError("Nenhuma alteracao informada.")
    directory = _tenant_dir(info_root, client_id)
    with _lock(directory), closing(_editable_connection(directory)) as connection, connection:
        row = _message_record(connection, guild_id, channel_id, message_id)
        overrides = json.loads(row["overrides"]) if row["overrides"] else {}
        if patch is not None:
            overrides.update(patch.model_dump(include=patch.model_fields_set))
        effective = {**json.loads(row["payload"]), **overrides}
        next_deleted = bool(row["deleted"]) if deleted is None else deleted
        connection.execute("INSERT INTO message_local VALUES(?,?,?,?,?,?,?) ON CONFLICT(guild_id,channel_id,id) DO UPDATE SET overrides=excluded.overrides,deleted=excluded.deleted,searchable=excluded.searchable,updated_at=excluded.updated_at",
                           (guild_id, channel_id, message_id, _dump(overrides), int(next_deleted),
                            _searchable_message(effective), datetime.now(timezone.utc).isoformat()))
        result = _effective_message(_message_record(connection, guild_id, channel_id, message_id))
        return {"mensagem": result, **_message_counts(connection, guild_id, channel_id)}


def change_channel(info_root: Path, client_id: str, guild_id: str, channel_id: str, patch: ChannelPatch):
    directory = _tenant_dir(info_root, client_id)
    with _lock(directory), closing(_editable_connection(directory)) as connection, connection:
        _channel_record(connection, guild_id, channel_id)
        old = connection.execute("SELECT overrides FROM channel_local WHERE guild_id=? AND id=?", (guild_id, channel_id)).fetchone()
        overrides = json.loads(old[0]) if old else {}
        overrides.update(patch.model_dump(include=patch.model_fields_set))
        connection.execute("INSERT INTO channel_local VALUES(?,?,?,?) ON CONFLICT(guild_id,id) DO UPDATE SET overrides=excluded.overrides,updated_at=excluded.updated_at",
                           (guild_id, channel_id, _dump(overrides), datetime.now(timezone.utc).isoformat()))
        return {"canal": _channel_record(connection, guild_id, channel_id)}


def get_asset(info_root: Path, client_id: str, guild_id: str, digest: str, filename: str):
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or filename != _safe_filename(filename):
        raise CofreError("Anexo nao encontrado.", 404)
    directory = _tenant_dir(info_root, client_id)
    connection = _connect(directory)
    if connection is None:
        raise CofreError("Anexo nao encontrado.", 404)
    with closing(connection):
        if not connection.execute("SELECT 1 FROM assets WHERE guild_id=? AND hash=? AND filename=?", (guild_id, digest, filename)).fetchone():
            raise CofreError("Anexo nao encontrado.", 404)
    path = directory / "assets" / digest
    _assert_safe(path)
    if not path.is_file():
        raise CofreError("Anexo nao encontrado.", 404)
    return path
