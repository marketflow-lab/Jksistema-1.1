"""Validated text snapshots and a separate, atomic company cache."""
from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, StrictStr, ValidationError, model_validator

from . import storage

FORMAT = "jk-cofre-shared-v1"
MAX_STATE_BYTES = 1024 * 1024


def _shared_url(value: str, *, required: bool = False) -> None:
    if (required and not value) or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ValueError("URL compartilhada invalida.")


class SharedMessage(storage.Message):
    deleted: StrictBool = False
    edited_fields: list[Literal["content", "author", "links", "embeds"]] = Field(default_factory=list, max_length=4)


class SharedChannel(storage.Channel):
    messages: list[SharedMessage] = Field(default_factory=list, max_length=100000)
    edited_fields: list[Literal["name", "category", "topic"]] = Field(default_factory=list, max_length=3)


class SharedGuild(storage._Model):
    guild: storage.Guild
    exported_at: StrictStr = Field(max_length=128)
    channels: list[SharedChannel] = Field(max_length=5000)


class SharedState(storage._Model):
    format: StrictStr
    guilds: list[SharedGuild] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_identity(self):
        if self.format != FORMAT:
            raise ValueError("Formato invalido.")
        guild_ids = set()
        for guild in self.guilds:
            if guild.guild.id in guild_ids:
                raise ValueError("Servidor duplicado.")
            guild_ids.add(guild.guild.id)
            channel_ids = set()
            for channel in guild.channels:
                _shared_url(channel.url)
                if len(set(channel.edited_fields)) != len(channel.edited_fields):
                    raise ValueError("Campos editados duplicados.")
                if channel.id in channel_ids:
                    raise ValueError("Canal duplicado.")
                channel_ids.add(channel.id)
                message_ids = set()
                for message in channel.messages:
                    if len(set(message.edited_fields)) != len(message.edited_fields):
                        raise ValueError("Campos editados duplicados.")
                    _shared_url(message.source_url)
                    for link in message.links:
                        _shared_url(link.url, required=True)
                    for embed in message.embeds:
                        for link in embed.links:
                            _shared_url(link.url, required=True)
                    if message.id in message_ids:
                        raise ValueError("Mensagem duplicada.")
                    message_ids.add(message.id)
                    for attachment in message.attachments:
                        _shared_url(attachment.url, required=True)
                        if attachment.archive_path or not attachment.url:
                            raise ValueError("Anexo sem URL compartilhavel.")
        return self


def validate_state(value: dict) -> dict:
    try:
        if len(storage._dump(value).encode("utf-8")) > MAX_STATE_BYTES:
            raise storage.CofreError("Acervo compartilhado maior que 1 MB.", 413)
        normalized = SharedState.model_validate(value).model_dump(exclude_none=True)
        if len(storage._dump(normalized).encode("utf-8")) > MAX_STATE_BYTES:
            raise storage.CofreError("Acervo compartilhado maior que 1 MB.", 413)
        return normalized
    except (ValidationError, TypeError, ValueError, RecursionError) as exc:
        if isinstance(exc, storage.CofreError):
            raise
        raise storage.CofreError("Acervo compartilhado invalido ou com anexo apenas local.", 422) from None


def export_state(info_root: Path, client_id: str) -> dict:
    """Export effective text, including edits and tombstones, without asset paths."""
    directory = storage._tenant_dir(info_root, client_id)
    connection = storage._connect(directory)
    guilds = []
    if connection is None:
        return {"format": FORMAT, "guilds": []}
    with closing(connection):
        for guild_row in connection.execute("SELECT id,payload FROM guilds ORDER BY id"):
            raw_guild = json.loads(guild_row["payload"])
            guild_id = guild_row["id"]
            channels = []
            for channel_row in connection.execute(
                "SELECT c.id,l.overrides FROM channels c LEFT JOIN channel_local l"
                " ON l.guild_id=c.guild_id AND l.id=c.id WHERE c.guild_id=? ORDER BY c.position,c.id", (guild_id,)
            ):
                effective = storage._channel_record(connection, guild_id, channel_row["id"])
                channel = {key: value for key, value in effective.items()
                           if key in storage.Channel.model_fields and key != "messages"}
                channel["edited_fields"] = sorted(json.loads(channel_row["overrides"] or "{}"))
                messages = []
                for row in connection.execute(
                    "SELECT m.payload,l.overrides,COALESCE(l.deleted,0) deleted FROM messages m"
                    + storage._MESSAGE_JOIN
                    + "WHERE m.guild_id=? AND m.channel_id=? ORDER BY m.timestamp,m.id",
                    (guild_id, channel_row["id"]),
                ):
                    message = storage._effective_message(row)
                    message.pop("edited", None)
                    message["edited_fields"] = sorted(json.loads(row["overrides"] or "{}"))
                    for attachment in message.get("attachments", []):
                        attachment.pop("local_url", None)
                        attachment.pop("archive_path", None)
                    messages.append(message)
                channel["messages"] = messages
                channels.append(channel)
            guilds.append({"guild": {"id": guild_id, "name": raw_guild["name"]},
                           "exported_at": raw_guild.get("exported_at", ""), "channels": channels})
    return validate_state({"format": FORMAT, "guilds": guilds})


def read_meta(info_root: Path, client_id: str) -> dict:
    connection = storage._connect(storage._tenant_dir(info_root, client_id))
    if connection is None:
        return {"revision": 0, "last_sync": None}
    with closing(connection):
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cofre_shared_meta'"
        ).fetchone():
            return {"revision": 0, "last_sync": None}
        row = connection.execute("SELECT revision,last_sync FROM cofre_shared_meta WHERE id=1").fetchone()
        return dict(row) if row else {"revision": 0, "last_sync": None}


def store_state(info_root: Path, client_id: str, state: dict, revision: int) -> None:
    state = validate_state(state)
    if type(revision) is not int or revision < 1:
        raise storage.CofreError("Versao compartilhada invalida.", 502)
    directory = storage._tenant_dir(info_root, client_id)
    with storage._lock(directory), closing(storage._connect(directory, writable=True)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS cofre_shared_meta("
            "id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,last_sync TEXT NOT NULL)"
        )
        old = connection.execute("SELECT revision FROM cofre_shared_meta WHERE id=1").fetchone()
        if old and old[0] > revision:
            return
        for table in ("message_local", "channel_local", "messages", "channels", "guilds", "assets"):
            connection.execute("DELETE FROM " + table)
        now = datetime.now(timezone.utc).isoformat()
        for entry in state["guilds"]:
            guild_id = entry["guild"]["id"]
            connection.execute("INSERT INTO guilds VALUES(?,?)",
                               (guild_id, storage._dump({**entry["guild"], "exported_at": entry["exported_at"]})))
            for position, raw_channel in enumerate(entry["channels"]):
                channel = dict(raw_channel)
                messages = channel.pop("messages")
                channel_edits = channel.pop("edited_fields")
                connection.execute("INSERT INTO channels VALUES(?,?,?,?)",
                                   (guild_id, channel["id"], position, storage._dump(channel)))
                if channel_edits:
                    connection.execute("INSERT INTO channel_local VALUES(?,?,?,?)",
                                       (guild_id, channel["id"],
                                        storage._dump({key: channel.get(key) for key in channel_edits}), now))
                for raw_message in messages:
                    message = dict(raw_message)
                    deleted = message.pop("deleted")
                    message_edits = message.pop("edited_fields")
                    searchable = storage._searchable_message(message)
                    identity = (guild_id, channel["id"], message["id"])
                    connection.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)",
                                       (*identity, storage._message_order(message["timestamp"]),
                                        searchable, storage._dump(message)))
                    if deleted or message_edits:
                        connection.execute("INSERT INTO message_local VALUES(?,?,?,?,?,?,?)",
                                           (*identity, storage._dump({key: message[key] for key in message_edits}),
                                            int(deleted), searchable, now))
        connection.execute(
            "INSERT INTO cofre_shared_meta VALUES(1,?,?) ON CONFLICT(id) "
            "DO UPDATE SET revision=excluded.revision,last_sync=excluded.last_sync", (revision, now)
        )
