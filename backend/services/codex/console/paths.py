"""Named filesystem locations owned by the configured Console runtime."""

from .runtime import (
    _codex_base_dir as base_dir,
    _codex_base_info_dir as base_info_dir,
    _codex_info_dir as info_dir,
)

__all__ = ["base_dir", "base_info_dir", "info_dir"]
