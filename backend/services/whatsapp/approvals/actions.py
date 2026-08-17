"""Extracted WhatsApp bridge component: actions."""

from __future__ import annotations
import base64
import concurrent.futures
import heapq
import importlib.util
import itertools
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import unicodedata
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse
from zoneinfo import ZoneInfo
import requests
from fastapi import Header, Request
from backend.schemas import IAChatAttachment, IAChatRequest
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import gateway as whatsapp_gateway
from backend.services.whatsapp import intent as whatsapp_intent
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import message as whatsapp_message
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
from backend.services import admin_usuarios_common, codex_whatsapp_agents
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore

from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS


def _message_request_text(message: dict[str, Any], transcription: Optional[dict[str, Any]] = None) -> str:
    return whatsapp_message.message_request_text(message, transcription)

def _mobile_screen_context(
    message_id: str,
    subject: str,
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
    query_policy: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "title": "WhatsApp — Black John",
        "pathname": "/whatsapp",
        "modulo_atual": "black_jhon_mobile",
        "selection": {
            "origin": "whatsapp",
            "message_id": message_id,
            "subject_id": subject,
            "mobile_full_access": True,
            "media": media or {},
            "transcription": transcription or {},
            "query_policy": dict(query_policy or {}) if isinstance(query_policy, dict) else {},
        },
    }

def _post_command_reply(config: dict[str, Any], message_id: str, text: str, title: str) -> None:
    parts = _whatsapp_response_parts(text, title)
    _post_message_result(config, message_id, {"status": "completed", "response": parts[0], "response_parts": parts})

def _handle_approval_command(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
) -> bool:
    action, _code = _approval_command(message.get("text_body"))
    if not action:
        return False
    message_id = str(message.get("message_id") or "")
    _post_command_reply(
        config,
        message_id,
        "A aprovacao desta tarefa nunca pode ocorrer pelo WhatsApp. Abra o JK Sistema para revisar e aprovar. "
        "A unica mutacao permitida aqui e a resposta ao comprador do Mercado Livre pelo botao tokenizado Aprovar e enviar.",
        "BLACK JHON - APROVACAO SOMENTE NO APLICATIVO",
    )
    return True


_COMPONENT_FUNCTIONS = frozenset((
    '_message_request_text',
    '_mobile_screen_context',
    '_post_command_reply',
    '_handle_approval_command'
))
_IMPLEMENTATIONS = {
    '_message_request_text': _message_request_text,
    '_mobile_screen_context': _mobile_screen_context,
    '_post_command_reply': _post_command_reply,
    '_handle_approval_command': _handle_approval_command
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
