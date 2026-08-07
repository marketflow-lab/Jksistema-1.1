"""Extracted WhatsApp bridge component: message_runtime."""

from __future__ import annotations
import base64
import concurrent.futures
import hashlib
import heapq
import importlib.util
import itertools
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse
from zoneinfo import ZoneInfo
import requests
from fastapi import Header, HTTPException, Request
from backend.schemas import IAChatAttachment, IAChatRequest
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import gateway as whatsapp_gateway
from backend.services.whatsapp import intent as whatsapp_intent
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import message as whatsapp_message
from backend.services.whatsapp import report_scheduling as whatsapp_report_scheduling
from backend.services.whatsapp import retry_policy as whatsapp_retry_policy
from backend.services.whatsapp import settings as whatsapp_settings
from backend.services.whatsapp import tool_results as whatsapp_tool_results
from backend.services.whatsapp.contracts import (
    _QuestionResearchPending,
    WhatsappAdhocMessageRequest,
    WhatsappBindingRevokeRequest,
    WhatsappBridgeConfigRequest,
    WhatsappPairingCodeRequest,
    WhatsappPhoneRegistrationRequest,
    WhatsappPhoneSettingsRequest,
    WhatsappTemplatesRequest,
    WhatsappVoiceToggleRequest,
)
from backend.services import admin_usuarios_common, codex_actions, codex_whatsapp_agents, whatsapp_report_files, whatsapp_report_visuals, whatsapp_voice
from backend.services.codex.console import conversations as console_conversations
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore

from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS


def _message_phone(config: dict[str, Any], message: dict[str, Any]) -> str:
    return whatsapp_message.message_phone(
        config,
        message,
        normalize_phone=console_conversations.normalize_phone,
    )

def _conversation_id(config: dict[str, Any], message: dict[str, Any]) -> str:
    phone = _message_phone(config, message)
    if not phone:
        raise RuntimeError("whatsapp_phone_identity_missing")
    client_id = str(message.get("client_id") or config.get("client_id") or "default")
    username = str(message.get("username") or config.get("username") or "user")
    if message.get("binding_is_primary") is True or message.get("binding_is_primary") == 1:
        return console_conversations.shared_id(client_id, username)
    return console_conversations.canonical_id(
        client_id,
        username,
        channel="whatsapp",
        phone=phone,
    )

def _message_prompt(
    message: dict[str, Any],
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
    *,
    mobile_full_access: bool = False,
    query_policy: Optional[dict[str, Any]] = None,
    ai_behavior: str = "",
    general_answer: bool = False,
) -> str:
    return whatsapp_message.message_prompt(
        message,
        media,
        transcription,
        mobile_full_access=mobile_full_access,
        query_policy=query_policy,
        ai_behavior=ai_behavior,
        general_answer=general_answer,
    )


_COMPONENT_FUNCTIONS = frozenset((
    '_message_phone',
    '_conversation_id',
    '_message_prompt'
))
_IMPLEMENTATIONS = {
    '_message_phone': _message_phone,
    '_conversation_id': _conversation_id,
    '_message_prompt': _message_prompt
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
