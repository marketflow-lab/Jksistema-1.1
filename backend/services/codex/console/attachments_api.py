"""Named attachment storage API."""

from .attachments import (
    _codex_attachment_dir as conversation_dir,
    _codex_attachment_public_payload as public_payload,
    _codex_attachments_base_dir as base_dir,
    _codex_safe_id as safe_id,
)

__all__ = ["base_dir", "conversation_dir", "public_payload", "safe_id"]
__codex_dependencies__ = []
