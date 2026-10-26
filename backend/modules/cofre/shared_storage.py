"""Validated text snapshots and a separate, atomic company cache."""
from __future__ import annotations

import copy
import json
import re
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, StrictStr, ValidationError, field_validator, model_validator

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


class Ownership(storage._Model):
    guild_id: StrictStr
    channel_id: StrictStr
    message_id: StrictStr
    owner_username: StrictStr = Field(min_length=1, max_length=512)
    _valid_ids = field_validator("guild_id", "channel_id", "message_id")(storage._identifier)


class DeletedChannel(storage._Model):
    guild_id: StrictStr
    channel_id: StrictStr
    _valid_ids = field_validator("guild_id", "channel_id")(storage._identifier)


class Metadata(storage._Model):
    version: Literal[1]
    ownership: list[Ownership] = Field(default_factory=list, max_length=100000)
    deleted_channels: list[DeletedChannel] = Field(default_factory=list, max_length=10000)


def validate_metadata(value, state=None):
    """Metadata is supplied by a trusted remote adapter, never a manifest."""
    try:
        if (not isinstance(value, dict) or type(value.get("version")) is not int
                or len(storage._dump(value).encode("utf-8")) > MAX_STATE_BYTES):
            raise ValueError("invalid_metadata")
        result = Metadata.model_validate(value).model_dump()
        owners, channels = set(), set()
        known_channels = {(entry["guild"]["id"], channel["id"])
                          for entry in (state or {}).get("guilds", []) for channel in entry["channels"]}
        known_messages = {(entry["guild"]["id"], channel["id"], message["id"])
                          for entry in (state or {}).get("guilds", []) for channel in entry["channels"]
                          for message in channel["messages"]}
        for row in result["ownership"]:
            key = row["guild_id"], row["channel_id"], row["message_id"]
            if (key in owners or not re.fullmatch(r"jk_msg_[a-f0-9]{32}", row["message_id"])
                    or row["owner_username"] != row["owner_username"].strip()
                    or any(ord(char) < 32 for char in row["owner_username"])
                    or state is not None and key not in known_messages):
                raise ValueError("invalid_ownership")
            owners.add(key)
        for row in result["deleted_channels"]:
            key = row["guild_id"], row["channel_id"]
            if key in channels or state is not None and key not in known_channels:
                raise ValueError("invalid_channel_tombstone")
            channels.add(key)
        return result
    except (TypeError, ValueError, RecursionError):
        raise storage.CofreError("Metadados do Cofre invalidos.", 422) from None


def export_metadata(info_root: Path, client_id: str) -> dict:
    connection = storage._connect(storage._tenant_dir(info_root, client_id))
    if connection is None:
        return {"version": 1, "ownership": [], "deleted_channels": []}
    with closing(connection):
        owners = [dict(row) for row in connection.execute(
            "SELECT o.guild_id,o.channel_id,o.id message_id,o.owner_username FROM message_ownership o "
            "JOIN messages m ON m.guild_id=o.guild_id AND m.channel_id=o.channel_id AND m.id=o.id "
            "ORDER BY o.guild_id,o.channel_id,o.id")]
        deleted = [dict(row) for row in connection.execute(
            "SELECT t.guild_id,t.id channel_id FROM channel_tombstones t "
            "JOIN channels c ON c.guild_id=t.guild_id AND c.id=t.id "
            "WHERE t.deleted=1 ORDER BY t.guild_id,t.id")]
        return validate_metadata({"version": 1, "ownership": owners, "deleted_channels": deleted})


def export_state(info_root: Path, client_id: str, *, asset_url_resolver=None) -> dict:
    """Export effective text, including edits and tombstones, without asset paths."""
    directory = storage._tenant_dir(info_root, client_id)
    connection = storage._connect(directory)
    if connection is None:
        return {"format": FORMAT, "guilds": []}
    with closing(connection):
        state = _export_connection(connection, asset_url_resolver=asset_url_resolver)
    return validate_state(state)


def _export_connection(connection, *, asset_url_resolver=None):
    guilds = []
    for guild_row in connection.execute("SELECT id,payload FROM guilds ORDER BY id"):
        raw_guild = json.loads(guild_row["payload"])
        guild_id = guild_row["id"]
        channels = []
        for channel_row in connection.execute(
            "SELECT c.id,l.overrides FROM channels c LEFT JOIN channel_local l"
            " ON l.guild_id=c.guild_id AND l.id=c.id WHERE c.guild_id=? ORDER BY c.position,c.id", (guild_id,)
        ):
            effective = storage._channel_record(connection, guild_id, channel_row["id"], include_deleted=True)
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
                    if asset_url_resolver is not None and (
                            not attachment.get("url") or attachment.get("archive_path")
                            and not attachment.get("url", "").startswith(("https://", "http://"))):
                        resolved = asset_url_resolver(guild_id, channel_row["id"], message["id"], dict(attachment))
                        if isinstance(resolved, dict):
                            if set(resolved) != {"url", "content_type"}:
                                raise storage.CofreError("Confirmacao do anexo invalida.", 502)
                            attachment.update(resolved)
                        else:
                            attachment["url"] = resolved
                    attachment.pop("local_url", None)
                    attachment.pop("archive_path", None)
                messages.append(message)
            channel["messages"] = messages
            channels.append(channel)
        guilds.append({"guild": {"id": guild_id, "name": raw_guild["name"]},
                       "exported_at": raw_guild.get("exported_at", ""), "channels": channels})
    return {"format": FORMAT, "guilds": guilds}


def _preserve_protected(connection, state):
    """Legacy snapshots cannot erase JK authorship or tombstoned history by omission."""
    protected_messages = {}
    for row in connection.execute("SELECT guild_id,channel_id,id FROM message_ownership"):
        protected_messages.setdefault((row[0], row[1]), set()).add(row[2])
    protected_channels = {(row[0], row[1]) for row in connection.execute(
        "SELECT guild_id,id FROM channel_tombstones WHERE deleted=1")}
    if not protected_messages and not protected_channels:
        return state
    merged = copy.deepcopy(state)
    guilds = {entry["guild"]["id"]: entry for entry in merged["guilds"]}
    for old_guild in _export_connection(connection)["guilds"]:
        guild_id = old_guild["guild"]["id"]
        for old_channel in old_guild["channels"]:
            channel_id = old_channel["id"]
            keep_all = (guild_id, channel_id) in protected_channels
            keep_ids = protected_messages.get((guild_id, channel_id), set())
            if not keep_all and not keep_ids:
                continue
            if guild_id not in guilds:
                guilds[guild_id] = {**old_guild, "channels": []}
                merged["guilds"].append(guilds[guild_id])
            channels = {row["id"]: row for row in guilds[guild_id]["channels"]}
            if channel_id not in channels:
                guilds[guild_id]["channels"].append(old_channel)
                continue
            known_ids = {message["id"] for message in channels[channel_id]["messages"]}
            channels[channel_id]["messages"].extend(message for message in old_channel["messages"]
                if message["id"] not in known_ids and (keep_all or message["id"] in keep_ids))
    return validate_state(merged)


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


def store_state(info_root: Path, client_id: str, state: dict, revision: int, metadata=None) -> None:
    state = validate_state(state)
    if type(revision) is not int or revision < 1:
        raise storage.CofreError("Versao compartilhada invalida.", 502)
    if metadata is not None:
        metadata = validate_metadata(metadata)
    directory = storage._tenant_dir(info_root, client_id)
    with storage._lock(directory), closing(storage._connect(directory, writable=True)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS cofre_shared_meta("
            "id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,last_sync TEXT NOT NULL)"
        )
        old = connection.execute("SELECT revision FROM cofre_shared_meta WHERE id=1").fetchone()
        if old and old[0] > revision:
            return
        state = _preserve_protected(connection, state)
        if metadata is not None:
            metadata = validate_metadata(metadata, state)
            for owner in metadata["ownership"]:
                old_owner = connection.execute("SELECT owner_username FROM message_ownership WHERE guild_id=? AND channel_id=? AND id=?",
                    (owner["guild_id"], owner["channel_id"], owner["message_id"])).fetchone()
                if old_owner and old_owner[0] != owner["owner_username"]:
                    raise storage.CofreError("A autoria original da mensagem nao pode ser alterada.", 409)
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
        if metadata is not None:
            for owner in metadata["ownership"]:
                connection.execute("INSERT OR IGNORE INTO message_ownership VALUES(?,?,?,?,?)",
                    (owner["guild_id"], owner["channel_id"], owner["message_id"], owner["owner_username"], now))
            connection.execute("DELETE FROM channel_tombstones")
            for channel in metadata["deleted_channels"]:
                connection.execute("INSERT INTO channel_tombstones VALUES(?,?,1,'',?)",
                    (channel["guild_id"], channel["channel_id"], now))
