"""Named conversation identity and persistence API."""

from .attachments import (
    _codex_canonical_conversation_id as canonical_id,
    _codex_normalize_phone as normalize_phone,
    _codex_shared_conversation_id as shared_id,
)
from .conversation_store import (
    _codex_load_or_create_conversation_state as load_or_create,
    _codex_load_or_create_shared_conversation_state as load_or_create_shared,
    _codex_update_conversation_memory as update_memory,
)


def resolve_identity(client_id: str, username: str, *, phone: str = "") -> str:
    return canonical_id(client_id, username, channel="whatsapp", phone=phone)


__all__ = [
    "canonical_id", "load_or_create", "load_or_create_shared", "normalize_phone",
    "resolve_identity", "shared_id", "update_memory",
]
__codex_dependencies__ = []
