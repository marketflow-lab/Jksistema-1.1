"""Shared IA in-memory state and constants."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

IA_RAG_REINDEX_ACTIVE = {}
IA_RAG_REINDEX_META = {}
IA_RAG_REINDEX_LOCK = threading.Lock()
IA_RAG_SEARCH_EXECUTOR = ThreadPoolExecutor(max_workers=2)
IA_PERGUNTAS_TOOLS_EXECUTOR = ThreadPoolExecutor(max_workers=8)
IA_GEMINI_MODELS_CACHE = {"expires_at": 0.0, "items": []}
IA_GEMINI_MODELS_CACHE_TTL_S = 900
IA_GEMINI_MODELS_LOCK = threading.Lock()
IA_CHAT_MESSAGE_MAX_CHARS = 4000
IA_CHAT_MESSAGE_COMPACT_TARGET_CHARS = 3900

__all__ = [name for name in globals() if name.startswith("IA_")]
