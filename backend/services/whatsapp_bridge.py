"""Local zero-cost WhatsApp bridge for Joao Pretinho.

The public webhook lives at Cloudflare. This module only polls the authenticated
bridge endpoints, keeps media and transcription local, and creates tasks through
the same internal Codex task core used by the existing HTTP API.
"""

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
from backend.services.whatsapp.composition import BridgeDependencies
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
from backend.services import (
    admin_usuarios_common,
    codex_actions,
    codex_console,
    codex_whatsapp_agents,
    whatsapp_report_files,
    whatsapp_report_visuals,
    whatsapp_voice,
)
from backend.services.whatsapp.runtime import BridgeRuntimeState
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore


BRIDGE_RUNTIME = BridgeRuntimeState()
ZERO_COST_POLICY_VALID_UNTIL = "2026-09-30T23:59:59Z"
POLL_SECONDS = 3
CLAIM_LIMIT = 5
TYPING_REFRESH_SECONDS = 20
TYPING_MAX_SECONDS = 10 * 60
TYPING_MAX_CONSECUTIVE_ERRORS = 3
TRANSCRIPTION_TIMEOUT_SECONDS = 600
WHISPER_MODEL_EXPECTED_BYTES = 488_000_000
WHATSAPP_GATEWAY_PROTOCOL_VERSION = 1
SUPPORTED_IMAGE_MIMES = whatsapp_media.SUPPORTED_IMAGE_MIMES
SUPPORTED_AUDIO_MIMES = whatsapp_media.SUPPORTED_AUDIO_MIMES
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
APPROVAL_CODE_TTL_SECONDS = 10 * 60
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_ADHOC_MESSAGE_CHARS = 3500
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS
WHATSAPP_EXACT_ORDER_HISTORY_MARKER = whatsapp_formatting.WHATSAPP_EXACT_ORDER_HISTORY_MARKER
WHATSAPP_REPORT_MAX_PARTS = whatsapp_formatting.WHATSAPP_REPORT_MAX_PARTS
WHATSAPP_REPORT_BODY_CHARS = whatsapp_formatting.WHATSAPP_REPORT_BODY_CHARS
WHATSAPP_REPORT_RANKING_ITEMS_PER_PART = whatsapp_formatting.WHATSAPP_REPORT_RANKING_ITEMS_PER_PART
WHATSAPP_QUERY_CONTEXT_TTL_SECONDS = 24 * 3600
WHATSAPP_IMPLICIT_STORE_RECENT_SECONDS = whatsapp_intent.WHATSAPP_IMPLICIT_STORE_RECENT_SECONDS
WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES = whatsapp_media.WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES
WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES = whatsapp_media.WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES
WHATSAPP_OUTBOUND_IMAGE_MAX_PIXELS = whatsapp_media.WHATSAPP_OUTBOUND_IMAGE_MAX_PIXELS
WHATSAPP_WEEKLY_REPORT_START_HOUR = whatsapp_report_scheduling.WHATSAPP_WEEKLY_REPORT_START_HOUR
WHATSAPP_IMAGE_MARKDOWN_RE = whatsapp_media.WHATSAPP_IMAGE_MARKDOWN_RE
APPROVAL_COMMAND_RE = re.compile(
    r"^(APROVAR|CONFIRMAR|NEGAR|REJEITAR|CANCELAR)\s+([A-Z2-9]{8})$",
    re.IGNORECASE,
)
QUESTION_APPROVAL_COMMAND_RE = re.compile(
    r"^ppv_(approve|correct|reject|regenerate|suggest):([A-Z2-9]{8})$",
    re.IGNORECASE,
)
QUESTION_APPROVAL_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60
STORE_SELECTION_COMMAND_RE = re.compile(r"^store_select:([A-Z2-9]{8})$", re.IGNORECASE)
STORE_SELECTION_TOKEN_TTL_SECONDS = 10 * 60
WHATSAPP_AI_DEFAULT_MODEL = whatsapp_settings.WHATSAPP_AI_DEFAULT_MODEL
WHATSAPP_CODEX_REASONING_DEFAULT = whatsapp_settings.WHATSAPP_CODEX_REASONING_DEFAULT
WHATSAPP_CODEX_REASONING_OPTIONS = whatsapp_settings.WHATSAPP_CODEX_REASONING_OPTIONS
WHATSAPP_CODEX_REASONING_POLICY_DEFAULT = whatsapp_settings.WHATSAPP_CODEX_REASONING_POLICY_DEFAULT
WHATSAPP_CODEX_REASONING_POLICIES = whatsapp_settings.WHATSAPP_CODEX_REASONING_POLICIES
WHATSAPP_ORCHESTRATION_MODE_DEFAULT = whatsapp_settings.WHATSAPP_ORCHESTRATION_MODE_DEFAULT
WHATSAPP_PROGRESS_INTERVAL_DEFAULT = whatsapp_settings.WHATSAPP_PROGRESS_INTERVAL_DEFAULT
WHATSAPP_AGENT_ARCHITECTURE_DEFAULT = whatsapp_settings.WHATSAPP_AGENT_ARCHITECTURE_DEFAULT
WHATSAPP_CONVERSATION_AGENT_MODEL_DEFAULT = whatsapp_settings.WHATSAPP_CONVERSATION_AGENT_MODEL_DEFAULT
WHATSAPP_CONVERSATION_AGENT_REASONING_DEFAULT = whatsapp_settings.WHATSAPP_CONVERSATION_AGENT_REASONING_DEFAULT
WHATSAPP_TASK_AGENT_MODEL_DEFAULT = whatsapp_settings.WHATSAPP_TASK_AGENT_MODEL_DEFAULT
WHATSAPP_TASK_AGENT_REASONING_DEFAULT = whatsapp_settings.WHATSAPP_TASK_AGENT_REASONING_DEFAULT
WHATSAPP_CODEX_SPEED_DEFAULT = whatsapp_settings.WHATSAPP_CODEX_SPEED_DEFAULT
WHATSAPP_CODEX_SERVICE_TIER_DEFAULT = whatsapp_settings.WHATSAPP_CODEX_SERVICE_TIER_DEFAULT
WHATSAPP_CONVERSATION_INTERVAL_DEFAULT = whatsapp_settings.WHATSAPP_CONVERSATION_INTERVAL_DEFAULT
WHATSAPP_WAIT_MESSAGE_AFTER_DEFAULT = whatsapp_settings.WHATSAPP_WAIT_MESSAGE_AFTER_DEFAULT
WHATSAPP_WAIT_MESSAGE_REPEAT_DEFAULT = whatsapp_settings.WHATSAPP_WAIT_MESSAGE_REPEAT_DEFAULT
WHATSAPP_WAIT_MESSAGE_STEADY_DEFAULT = whatsapp_settings.WHATSAPP_WAIT_MESSAGE_STEADY_DEFAULT
WHATSAPP_PARTIAL_DEBOUNCE_DEFAULT = whatsapp_settings.WHATSAPP_PARTIAL_DEBOUNCE_DEFAULT
WHATSAPP_JOB_DEADLINE_DEFAULT = whatsapp_settings.WHATSAPP_JOB_DEADLINE_DEFAULT
WHATSAPP_ML_RESEARCH_DEADLINE_SECONDS = 5 * 60
WHATSAPP_REPORT_DEADLINE_SECONDS = 10 * 60
WHATSAPP_MAX_SUBTASKS_DEFAULT = whatsapp_settings.WHATSAPP_MAX_SUBTASKS_DEFAULT
WHATSAPP_MAX_ACTIVE_TASK_AGENTS_DEFAULT = whatsapp_settings.WHATSAPP_MAX_ACTIVE_TASK_AGENTS_DEFAULT
WHATSAPP_CONVERSATION_WORKER_COUNT_DEFAULT = whatsapp_settings.WHATSAPP_CONVERSATION_WORKER_COUNT_DEFAULT
WHATSAPP_CONVERSATION_RUNTIME_POOL_SIZE_DEFAULT = whatsapp_settings.WHATSAPP_CONVERSATION_RUNTIME_POOL_SIZE_DEFAULT
WHATSAPP_FUNCTION_MANAGER_ENABLED_DEFAULT = whatsapp_settings.WHATSAPP_FUNCTION_MANAGER_ENABLED_DEFAULT
WHATSAPP_FUNCTION_MANAGER_REQUIRED_DEFAULT = whatsapp_settings.WHATSAPP_FUNCTION_MANAGER_REQUIRED_DEFAULT
WHATSAPP_FUNCTION_MANAGER_WORKER_COUNT_DEFAULT = whatsapp_settings.WHATSAPP_FUNCTION_MANAGER_WORKER_COUNT_DEFAULT
WHATSAPP_FUNCTION_MANAGER_RUNTIME_POOL_SIZE_DEFAULT = whatsapp_settings.WHATSAPP_FUNCTION_MANAGER_RUNTIME_POOL_SIZE_DEFAULT
WHATSAPP_MAX_ACTIVE_TASK_AGENTS_GLOBAL_DEFAULT = whatsapp_settings.WHATSAPP_MAX_ACTIVE_TASK_AGENTS_GLOBAL_DEFAULT
WHATSAPP_RETRY_DELAYS_SECONDS = whatsapp_retry_policy.WHATSAPP_RETRY_DELAYS_SECONDS
WHATSAPP_MAX_RETRY_ATTEMPTS = whatsapp_retry_policy.WHATSAPP_MAX_RETRY_ATTEMPTS
WHATSAPP_LOCAL_QUEUE_CAPACITY = 32
DUAL_AGENT_STATE_LOCK = BRIDGE_RUNTIME.dual_agent_state_lock
BRIDGE_STATE_LOCK = BRIDGE_RUNTIME.bridge_state_lock
BRIDGE_SHARED_STATE: Optional[dict[str, Any]] = BRIDGE_RUNTIME.bridge_shared_state
BRIDGE_STORE: Optional[WhatsappBridgeStore] = BRIDGE_RUNTIME.bridge_store


CONFIG_LOCK = BRIDGE_RUNTIME.config_lock
BRIDGE_STOP_EVENT = BRIDGE_RUNTIME.stop_event
BRIDGE_THREAD: Optional[threading.Thread] = BRIDGE_RUNTIME.bridge_thread
DOWNLOAD_THREAD: Optional[threading.Thread] = BRIDGE_RUNTIME.download_thread
ALERT_THREAD: Optional[threading.Thread] = BRIDGE_RUNTIME.alert_thread
TYPING_PULSES_LOCK = BRIDGE_RUNTIME.typing_pulses_lock
TYPING_PULSES = BRIDGE_RUNTIME.typing_pulses
PROGRESS_PULSES_LOCK = BRIDGE_RUNTIME.progress_pulses_lock
PROGRESS_PULSES = BRIDGE_RUNTIME.progress_pulses
RUNTIME_STATE = BRIDGE_RUNTIME.runtime_diagnostics
MODEL_VALIDATION_CACHE = BRIDGE_RUNTIME.model_validation_cache
PHONE_DISPATCH_LOCK = BRIDGE_RUNTIME.phone_dispatch_lock
PHONE_DISPATCH_EXECUTOR = BRIDGE_RUNTIME.phone_dispatch_executor
PHONE_DISPATCH_EXECUTOR_WORKERS = BRIDGE_RUNTIME.phone_dispatch_executor_workers
PHONE_DISPATCH_OLD_EXECUTORS = BRIDGE_RUNTIME.phone_dispatch_old_executors
PHONE_DISPATCH_QUEUES = BRIDGE_RUNTIME.phone_dispatch_queues
PHONE_DISPATCH_ACTIVE = BRIDGE_RUNTIME.phone_dispatch_active
PHONE_DISPATCH_INFLIGHT = BRIDGE_RUNTIME.phone_dispatch_inflight
PHONE_DISPATCH_EVENT_IDS = BRIDGE_RUNTIME.phone_dispatch_event_ids
PHONE_DISPATCH_TICK_IDS = BRIDGE_RUNTIME.phone_dispatch_tick_ids
PHONE_DISPATCH_FUTURES = BRIDGE_RUNTIME.phone_dispatch_futures
PHONE_DISPATCH_SEQUENCE = BRIDGE_RUNTIME.phone_dispatch_sequence
PHONE_DISPATCH_ACCEPTING = BRIDGE_RUNTIME.phone_dispatch_accepting
FUNCTION_MANAGER_LOCK = BRIDGE_RUNTIME.function_manager_lock
FUNCTION_MANAGER_EXECUTOR = BRIDGE_RUNTIME.function_manager_executor
FUNCTION_MANAGER_EXECUTOR_WORKERS = BRIDGE_RUNTIME.function_manager_executor_workers
WEB_FALLBACK_LOCK = BRIDGE_RUNTIME.web_fallback_lock
WEB_FALLBACK_CIRCUIT = BRIDGE_RUNTIME.web_fallback_circuit
FUNCTION_MANAGER_FUTURES = BRIDGE_RUNTIME.function_manager_futures
PHONE_LATENCY_SAMPLES = BRIDGE_RUNTIME.phone_latency_samples

import backend.services.whatsapp.config_store as whatsapp_component_config_store
import backend.services.whatsapp.artifacts as whatsapp_component_artifacts
import backend.services.whatsapp.intent_runtime as whatsapp_component_intent_runtime
import backend.services.whatsapp.query_context as whatsapp_component_query_context
import backend.services.whatsapp.gateway_runtime as whatsapp_component_gateway_runtime
import backend.services.whatsapp.transcription as whatsapp_component_transcription
import backend.services.whatsapp.message_runtime as whatsapp_component_message_runtime
import backend.services.whatsapp.delivery as whatsapp_component_delivery
import backend.services.whatsapp.approvals.question_tokens as whatsapp_component_question_tokens
import backend.services.whatsapp.approvals.question_workflow as whatsapp_component_question_workflow
import backend.services.whatsapp.orchestration.retry_coordinator as whatsapp_component_retry_coordinator
import backend.services.whatsapp.orchestration.conversation as whatsapp_component_conversation
import backend.services.whatsapp.orchestration.function_manager as whatsapp_component_function_manager
import backend.services.whatsapp.orchestration.manager_tasks as whatsapp_component_manager_tasks
import backend.services.whatsapp.orchestration.manager_results as whatsapp_component_manager_results
import backend.services.whatsapp.orchestration.pending as whatsapp_component_pending
import backend.services.whatsapp.orchestration.completion_groups as whatsapp_component_completion_groups
import backend.services.whatsapp.orchestration.completion as whatsapp_component_completion
import backend.services.whatsapp.approvals.actions as whatsapp_component_actions
import backend.services.whatsapp.orchestration.processor as whatsapp_component_processor
import backend.services.whatsapp.runtime.dispatcher as whatsapp_component_dispatcher
import backend.services.whatsapp.runtime.monitor as whatsapp_component_monitor
import backend.services.whatsapp.runtime.scheduler as whatsapp_component_scheduler
import backend.services.whatsapp.runtime.lifecycle as whatsapp_component_lifecycle
import backend.services.whatsapp.api_status as whatsapp_component_api_status
import backend.services.whatsapp.api_endpoints as whatsapp_component_api_endpoints


_COMPONENTS = {
    'config_store': whatsapp_component_config_store,
    'artifacts': whatsapp_component_artifacts,
    'intent_runtime': whatsapp_component_intent_runtime,
    'query_context': whatsapp_component_query_context,
    'gateway_runtime': whatsapp_component_gateway_runtime,
    'transcription': whatsapp_component_transcription,
    'message_runtime': whatsapp_component_message_runtime,
    'delivery': whatsapp_component_delivery,
    'question_tokens': whatsapp_component_question_tokens,
    'question_workflow': whatsapp_component_question_workflow,
    'retry_coordinator': whatsapp_component_retry_coordinator,
    'conversation': whatsapp_component_conversation,
    'function_manager': whatsapp_component_function_manager,
    'manager_tasks': whatsapp_component_manager_tasks,
    'manager_results': whatsapp_component_manager_results,
    'pending': whatsapp_component_pending,
    'completion_groups': whatsapp_component_completion_groups,
    'completion': whatsapp_component_completion,
    'actions': whatsapp_component_actions,
    'processor': whatsapp_component_processor,
    'dispatcher': whatsapp_component_dispatcher,
    'monitor': whatsapp_component_monitor,
    'scheduler': whatsapp_component_scheduler,
    'lifecycle': whatsapp_component_lifecycle,
    'api_status': whatsapp_component_api_status,
    'api_endpoints': whatsapp_component_api_endpoints,
}
_MIRRORED_SCALAR_GLOBALS = {
    "ALERT_THREAD",
    "BRIDGE_SHARED_STATE",
    "BRIDGE_STORE",
    "BRIDGE_THREAD",
    "DOWNLOAD_THREAD",
    "FUNCTION_MANAGER_EXECUTOR",
    "FUNCTION_MANAGER_EXECUTOR_WORKERS",
    "PHONE_DISPATCH_ACCEPTING",
    "PHONE_DISPATCH_EXECUTOR",
    "PHONE_DISPATCH_EXECUTOR_WORKERS",
    "PHONE_DISPATCH_INFLIGHT",
}
_RUNTIME_SCALAR_ATTRIBUTES = {
    "ALERT_THREAD": "alert_thread",
    "BRIDGE_SHARED_STATE": "bridge_shared_state",
    "BRIDGE_STORE": "bridge_store",
    "BRIDGE_THREAD": "bridge_thread",
    "DOWNLOAD_THREAD": "download_thread",
    "FUNCTION_MANAGER_EXECUTOR": "function_manager_executor",
    "FUNCTION_MANAGER_EXECUTOR_WORKERS": "function_manager_executor_workers",
    "PHONE_DISPATCH_ACCEPTING": "phone_dispatch_accepting",
    "PHONE_DISPATCH_EXECUTOR": "phone_dispatch_executor",
    "PHONE_DISPATCH_EXECUTOR_WORKERS": "phone_dispatch_executor_workers",
    "PHONE_DISPATCH_INFLIGHT": "phone_dispatch_inflight",
}


def _sync_components() -> None:
    dependencies = BridgeDependencies.from_namespace(globals())
    for component in _COMPONENTS.values():
        component.bind_bridge_dependencies(dependencies)


def _invoke_component(component_name: str, function_name: str, *args: Any, **kwargs: Any) -> Any:
    component = _COMPONENTS[component_name]
    _sync_components()
    try:
        return component.invoke(function_name, *args, **kwargs)
    finally:
        namespace = globals()
        for name in _MIRRORED_SCALAR_GLOBALS:
            if name == "PHONE_DISPATCH_INFLIGHT":
                namespace[name] = BRIDGE_RUNTIME.phone_dispatch_inflight
                continue
            if hasattr(component, name):
                value = getattr(component, name)
                namespace[name] = value
                setattr(BRIDGE_RUNTIME, _RUNTIME_SCALAR_ATTRIBUTES[name], value)

def _make_delegator(component_name: str, function_name: str):
    import functools

    implementation = _COMPONENTS[component_name]._IMPLEMENTATIONS[function_name]

    @functools.wraps(implementation)
    def delegator(*args: Any, **kwargs: Any) -> Any:
        return _invoke_component(component_name, function_name, *args, **kwargs)

    return delegator



_now = _make_delegator('config_store', '_now')

_base_dir = _make_delegator('config_store', '_base_dir')

_info_dir = _make_delegator('config_store', '_info_dir')

_config_path = _make_delegator('config_store', '_config_path')

_state_path = _make_delegator('config_store', '_state_path')

_store_path = _make_delegator('config_store', '_store_path')

_bridge_store = _make_delegator('config_store', '_bridge_store')

_download_model_dir = _make_delegator('config_store', '_download_model_dir')

_bundled_model_dir = _make_delegator('config_store', '_bundled_model_dir')

_model_manifest_path = _make_delegator('config_store', '_model_manifest_path')

_json_read = _make_delegator('config_store', '_json_read')

_validate_model_dir = _make_delegator('config_store', '_validate_model_dir')

_model_dir = _make_delegator('config_store', '_model_dir')

_json_write = _make_delegator('config_store', '_json_write')

_host_machine_id = _make_delegator('config_store', '_host_machine_id')

_normalize_ai_model = _make_delegator('config_store', '_normalize_ai_model')

_normalize_codex_reasoning_effort = _make_delegator('config_store', '_normalize_codex_reasoning_effort')

_normalize_codex_reasoning_policy = _make_delegator('config_store', '_normalize_codex_reasoning_policy')

_normalize_codex_agent_model = _make_delegator('config_store', '_normalize_codex_agent_model')

_normalize_agent_architecture = _make_delegator('config_store', '_normalize_agent_architecture')

_normalize_conversation_interval = _make_delegator('config_store', '_normalize_conversation_interval')

_normalize_capacity = _make_delegator('config_store', '_normalize_capacity')

_whatsapp_dual_agent_settings = _make_delegator('config_store', '_whatsapp_dual_agent_settings')

_normalize_progress_interval = _make_delegator('config_store', '_normalize_progress_interval')

_normalize_voice_model = _make_delegator('config_store', '_normalize_voice_model')

_normalize_voice_name = _make_delegator('config_store', '_normalize_voice_name')

_normalize_voice_int = _make_delegator('config_store', '_normalize_voice_int')

_normalize_voice_config = _make_delegator('config_store', '_normalize_voice_config')

_whatsapp_ai_settings = _make_delegator('config_store', '_whatsapp_ai_settings')

_default_phone_notification_settings = _make_delegator('config_store', '_default_phone_notification_settings')

_normalize_phone_ai_behavior = _make_delegator('config_store', '_normalize_phone_ai_behavior')

_normalize_phone_notification_settings = _make_delegator('config_store', '_normalize_phone_notification_settings')

_phone_notification_settings = _make_delegator('config_store', '_phone_notification_settings')

_default_config = _make_delegator('config_store', '_default_config')

_load_config = _make_delegator('config_store', '_load_config')

_save_config = _make_delegator('config_store', '_save_config')

_load_state = _make_delegator('config_store', '_load_state')

_save_state = _make_delegator('config_store', '_save_state')

_mask_phone = _make_delegator('artifacts', '_mask_phone')

_normalize_registered_phone = _make_delegator('artifacts', '_normalize_registered_phone')

_whatsapp_table_blocks_to_mobile = _make_delegator('artifacts', '_whatsapp_table_blocks_to_mobile')

_whatsapp_clean_markdown = _make_delegator('artifacts', '_whatsapp_clean_markdown')

_whatsapp_image_requested = _make_delegator('artifacts', '_whatsapp_image_requested')

_whatsapp_image_references = _make_delegator('artifacts', '_whatsapp_image_references')

_whatsapp_path_within = _make_delegator('artifacts', '_whatsapp_path_within')

_whatsapp_image_roots = _make_delegator('artifacts', '_whatsapp_image_roots')

_whatsapp_resolve_image_reference = _make_delegator('artifacts', '_whatsapp_resolve_image_reference')

_whatsapp_sku_candidates = _make_delegator('artifacts', '_whatsapp_sku_candidates')

_whatsapp_normalized_sku = _make_delegator('artifacts', '_whatsapp_normalized_sku')

_whatsapp_image_matches_skus = _make_delegator('artifacts', '_whatsapp_image_matches_skus')

_whatsapp_find_image_by_sku = _make_delegator('artifacts', '_whatsapp_find_image_by_sku')

_whatsapp_image_mime = _make_delegator('artifacts', '_whatsapp_image_mime')

_whatsapp_prepare_outbound_image = _make_delegator('artifacts', '_whatsapp_prepare_outbound_image')

_whatsapp_strip_image_references = _make_delegator('artifacts', '_whatsapp_strip_image_references')

_whatsapp_outbound_image_caption = _make_delegator('artifacts', '_whatsapp_outbound_image_caption')

_whatsapp_deliver_requested_images = _make_delegator('artifacts', '_whatsapp_deliver_requested_images')

_whatsapp_report_chart_path = _make_delegator('artifacts', '_whatsapp_report_chart_path')

_whatsapp_report_document_path = _make_delegator('artifacts', '_whatsapp_report_document_path')

_whatsapp_deliver_report_artifacts = _make_delegator('artifacts', '_whatsapp_deliver_report_artifacts')

_whatsapp_text_key = _make_delegator('intent_runtime', '_whatsapp_text_key')

_whatsapp_daily_sales_report_requested = _make_delegator('intent_runtime', '_whatsapp_daily_sales_report_requested')

_whatsapp_sales_report_requested = _make_delegator('intent_runtime', '_whatsapp_sales_report_requested')

_whatsapp_query_only_domains = _make_delegator('intent_runtime', '_whatsapp_query_only_domains')

_whatsapp_source_policy = _make_delegator('intent_runtime', '_whatsapp_source_policy')

_whatsapp_readonly_inquiry = _make_delegator('intent_runtime', '_whatsapp_readonly_inquiry')

_whatsapp_general_answer_request = _make_delegator('intent_runtime', '_whatsapp_general_answer_request')

_whatsapp_mutation_intent = _make_delegator('intent_runtime', '_whatsapp_mutation_intent')

_whatsapp_post_sale_action = _make_delegator('intent_runtime', '_whatsapp_post_sale_action')

_whatsapp_protected_mutation_domains = _make_delegator('intent_runtime', '_whatsapp_protected_mutation_domains')

_whatsapp_action_spec_query_only_domains = _make_delegator('intent_runtime', '_whatsapp_action_spec_query_only_domains')

_whatsapp_load_store_configs = _make_delegator('intent_runtime', '_whatsapp_load_store_configs')

_whatsapp_authorized_api_stores = _make_delegator('intent_runtime', '_whatsapp_authorized_api_stores')

_whatsapp_exact_store_matches = _make_delegator('intent_runtime', '_whatsapp_exact_store_matches')

_whatsapp_all_stores_requested = _make_delegator('intent_runtime', '_whatsapp_all_stores_requested')

_whatsapp_store_scoped_request = _make_delegator('intent_runtime', '_whatsapp_store_scoped_request')

_whatsapp_session_stores = _make_delegator('intent_runtime', '_whatsapp_session_stores')

_whatsapp_store_scope_policy = _make_delegator('intent_runtime', '_whatsapp_store_scope_policy')

_whatsapp_api_query_requires_store = _make_delegator('intent_runtime', '_whatsapp_api_query_requires_store')

_whatsapp_requested_api_providers = _make_delegator('intent_runtime', '_whatsapp_requested_api_providers')

_whatsapp_query_policy = _make_delegator('intent_runtime', '_whatsapp_query_policy')

_whatsapp_pagination_request = _make_delegator('intent_runtime', '_whatsapp_pagination_request')

_whatsapp_contextual_report_request = _make_delegator('intent_runtime', '_whatsapp_contextual_report_request')

_whatsapp_contextual_report_period = _make_delegator('intent_runtime', '_whatsapp_contextual_report_period')

_whatsapp_implicit_store_followup = _make_delegator('intent_runtime', '_whatsapp_implicit_store_followup')

_whatsapp_inherit_query_store_context = _make_delegator('query_context', '_whatsapp_inherit_query_store_context')

_whatsapp_query_continuation_policy = _make_delegator('query_context', '_whatsapp_query_continuation_policy')

_whatsapp_remember_query_context = _make_delegator('query_context', '_whatsapp_remember_query_context')

_whatsapp_update_query_context_from_task = _make_delegator('query_context', '_whatsapp_update_query_context_from_task')

_whatsapp_query_only_block_text = _make_delegator('query_context', '_whatsapp_query_only_block_text')

_whatsapp_store_required_text = _make_delegator('query_context', '_whatsapp_store_required_text')

_whatsapp_plain_inline = _make_delegator('query_context', '_whatsapp_plain_inline')

_whatsapp_field = _make_delegator('query_context', '_whatsapp_field')

_whatsapp_heading_value = _make_delegator('query_context', '_whatsapp_heading_value')

_whatsapp_report_section_kind = _make_delegator('query_context', '_whatsapp_report_section_kind')

_whatsapp_report_sections = _make_delegator('query_context', '_whatsapp_report_sections')

_whatsapp_metric_label = _make_delegator('query_context', '_whatsapp_metric_label')

_whatsapp_number = _make_delegator('query_context', '_whatsapp_number')

_whatsapp_money = _make_delegator('query_context', '_whatsapp_money')

_whatsapp_report_date = _make_delegator('query_context', '_whatsapp_report_date')

_whatsapp_daily_ml_sales_report = _make_delegator('query_context', '_whatsapp_daily_ml_sales_report')

_whatsapp_report_summary = _make_delegator('query_context', '_whatsapp_report_summary')

_whatsapp_ranking_items = _make_delegator('query_context', '_whatsapp_ranking_items')

_whatsapp_rank_marker = _make_delegator('query_context', '_whatsapp_rank_marker')

_whatsapp_report_ranking_parts = _make_delegator('query_context', '_whatsapp_report_ranking_parts')

_whatsapp_report_text_section = _make_delegator('query_context', '_whatsapp_report_text_section')

_whatsapp_report_response_parts = _make_delegator('query_context', '_whatsapp_report_response_parts')

_whatsapp_split_body = _make_delegator('query_context', '_whatsapp_split_body')

_whatsapp_operational_title = _make_delegator('query_context', '_whatsapp_operational_title')

_whatsapp_response_parts = _make_delegator('query_context', '_whatsapp_response_parts')

_whatsapp_result_title = _make_delegator('query_context', '_whatsapp_result_title')

_approval_code = _make_delegator('gateway_runtime', '_approval_code')

_approval_command = _make_delegator('gateway_runtime', '_approval_command')

_normalize_worker_url = _make_delegator('gateway_runtime', '_normalize_worker_url')

_require_full = _make_delegator('gateway_runtime', '_require_full')

_binding_target = _make_delegator('gateway_runtime', '_binding_target')

_gateway_headers = _make_delegator('gateway_runtime', '_gateway_headers')

_gateway_request = _make_delegator('gateway_runtime', '_gateway_request')

_gateway_json = _make_delegator('gateway_runtime', '_gateway_json')

_worker_health = _make_delegator('gateway_runtime', '_worker_health')

_directory_size = _make_delegator('transcription', '_directory_size')

_whisper_status = _make_delegator('transcription', '_whisper_status')

_whisper_runner = _make_delegator('transcription', '_whisper_runner')

_download_whisper_worker = _make_delegator('transcription', '_download_whisper_worker')

whatsapp_bridge_download_whisper = _make_delegator('transcription', 'whatsapp_bridge_download_whisper')

_transcribe_audio = _make_delegator('transcription', '_transcribe_audio')

_safe_filename = _make_delegator('transcription', '_safe_filename')

_media_extension = _make_delegator('transcription', '_media_extension')

_download_media = _make_delegator('transcription', '_download_media')

_message_phone = _make_delegator('message_runtime', '_message_phone')

_conversation_id = _make_delegator('message_runtime', '_conversation_id')

_message_prompt = _make_delegator('message_runtime', '_message_prompt')

_post_typing_indicator = _make_delegator('delivery', '_post_typing_indicator')

_post_message_progress = _make_delegator('delivery', '_post_message_progress')

_progress_stage = _make_delegator('delivery', '_progress_stage')

_progress_message = _make_delegator('delivery', '_progress_message')

_record_progress_event = _make_delegator('delivery', '_record_progress_event')

_progress_pulse_worker = _make_delegator('delivery', '_progress_pulse_worker')

_start_progress_pulse = _make_delegator('delivery', '_start_progress_pulse')

_stop_progress_pulse = _make_delegator('delivery', '_stop_progress_pulse')

_typing_pulse_worker = _make_delegator('delivery', '_typing_pulse_worker')

_start_typing_pulse = _make_delegator('delivery', '_start_typing_pulse')

_stop_typing_pulse = _make_delegator('delivery', '_stop_typing_pulse')

_post_message_result = _make_delegator('delivery', '_post_message_result')

_post_outbound_image = _make_delegator('delivery', '_post_outbound_image')

_post_outbound_document = _make_delegator('delivery', '_post_outbound_document')

_post_proactive_image = _make_delegator('delivery', '_post_proactive_image')

_post_proactive_document = _make_delegator('delivery', '_post_proactive_document')

_post_proactive = _make_delegator('delivery', '_post_proactive')

_post_interactive_approval = _make_delegator('delivery', '_post_interactive_approval')

_post_interactive_store_selection = _make_delegator('delivery', '_post_interactive_store_selection')

_whatsapp_store_selection_token = _make_delegator('query_context', '_whatsapp_store_selection_token')

_whatsapp_consume_store_selection = _make_delegator('query_context', '_whatsapp_consume_store_selection')

_whatsapp_send_store_selection = _make_delegator('query_context', '_whatsapp_send_store_selection')

_question_approval_allowed = _make_delegator('question_tokens', '_question_approval_allowed')

_question_approval_product_identity = _make_delegator('question_tokens', '_question_approval_product_identity')

_question_approval_body = _make_delegator('question_tokens', '_question_approval_body')

_question_approval_token = _make_delegator('question_tokens', '_question_approval_token')

_question_thread_key = _make_delegator('question_tokens', '_question_thread_key')

_question_set_active_thread = _make_delegator('question_tokens', '_question_set_active_thread')

_question_clear_active_thread = _make_delegator('question_tokens', '_question_clear_active_thread')

_question_active_approval = _make_delegator('question_tokens', '_question_active_approval')

_deliver_completed_question_research = _make_delegator('question_workflow', '_deliver_completed_question_research')

_forward_question_approvals = _make_delegator('question_workflow', '_forward_question_approvals')

_reload_bound_session = _make_delegator('retry_coordinator', '_reload_bound_session')

_pending_task_for_message = _make_delegator('retry_coordinator', '_pending_task_for_message')

_active_pending_for_conversation = _make_delegator('retry_coordinator', '_active_pending_for_conversation')

_pending_task_ids = _make_delegator('retry_coordinator', '_pending_task_ids')

_pending_codex_tasks = _make_delegator('retry_coordinator', '_pending_codex_tasks')

_update_pending_codex_tasks = _make_delegator('retry_coordinator', '_update_pending_codex_tasks')

_dual_retry_reason_text = _make_delegator('retry_coordinator', '_dual_retry_reason_text')

_dual_retry_is_auth_error = _make_delegator('retry_coordinator', '_dual_retry_is_auth_error')

_dual_retry_classification = _make_delegator('retry_coordinator', '_dual_retry_classification')

_dual_retry_delay_seconds = _make_delegator('retry_coordinator', '_dual_retry_delay_seconds')

_local_web_fallback_context = _make_delegator('retry_coordinator', '_local_web_fallback_context')

_dual_append_unique = _make_delegator('retry_coordinator', '_dual_append_unique')

_dual_preserve_worker_result = _make_delegator('retry_coordinator', '_dual_preserve_worker_result')

_dual_worker_disposition = _make_delegator('retry_coordinator', '_dual_worker_disposition')

_dual_schedule_retry = _make_delegator('retry_coordinator', '_dual_schedule_retry')

_dual_migrate_pending_v7 = _make_delegator('retry_coordinator', '_dual_migrate_pending_v7')

_recover_dual_pending_after_restart = _make_delegator('retry_coordinator', '_recover_dual_pending_after_restart')

_whatsapp_is_job_status_probe = _make_delegator('conversation', '_whatsapp_is_job_status_probe')

_whatsapp_is_task_complement = _make_delegator('conversation', '_whatsapp_is_task_complement')

_dual_conversation_record = _make_delegator('conversation', '_dual_conversation_record')

_dual_append_conversation_turn = _make_delegator('conversation', '_dual_append_conversation_turn')

_dual_recent_conversation_context = _make_delegator('conversation', '_dual_recent_conversation_context')

_dual_remember_conversation_turn = _make_delegator('conversation', '_dual_remember_conversation_turn')

_save_dual_conversation_record = _make_delegator('conversation', '_save_dual_conversation_record')

_dual_active_job_snapshot = _make_delegator('conversation', '_dual_active_job_snapshot')

_run_conversation_agent = _make_delegator('conversation', '_run_conversation_agent')

_deterministic_direct_query_plan = _make_delegator('conversation', '_deterministic_direct_query_plan')

_dual_delegate_query_policy = _make_delegator('conversation', '_dual_delegate_query_policy')

_function_manager_catalog = _make_delegator('function_manager', '_function_manager_catalog')

_function_manager_extract_identifiers = _make_delegator('function_manager', '_function_manager_extract_identifiers')

_function_manager_enforce_plan = _make_delegator('function_manager', '_function_manager_enforce_plan')

_stock_balance_contract = _make_delegator('function_manager', '_stock_balance_contract')

_normalize_tool_result_contract = _make_delegator('function_manager', '_normalize_tool_result_contract')

_function_manager_compact_result = _make_delegator('function_manager', '_function_manager_compact_result')

_marketplace_listing_stock_contract = _make_delegator('function_manager', '_marketplace_listing_stock_contract')

_local_stock_contract = _make_delegator('function_manager', '_local_stock_contract')

_stock_tool_result_confirmed = _make_delegator('function_manager', '_stock_tool_result_confirmed')

_function_manager_execute_tools = _make_delegator('function_manager', '_function_manager_execute_tools')

_function_manager_evidence = _make_delegator('function_manager', '_function_manager_evidence')

_function_manager_merge_evidence = _make_delegator('function_manager', '_function_manager_merge_evidence')

_configure_function_manager_executor = _make_delegator('function_manager', '_configure_function_manager_executor')

_record_function_manager_diagnostic = _make_delegator('function_manager', '_record_function_manager_diagnostic')

_create_dual_worker_task = _make_delegator('manager_tasks', '_create_dual_worker_task')

_function_manager_worker_query_policy = _make_delegator('manager_tasks', '_function_manager_worker_query_policy')

_function_manager_start_sol = _make_delegator('manager_tasks', '_function_manager_start_sol')

_function_manager_deliver_direct = _make_delegator('manager_tasks', '_function_manager_deliver_direct')

_format_stock_quantity = _make_delegator('manager_results', '_format_stock_quantity')

_deterministic_stock_result_text = _make_delegator('manager_results', '_deterministic_stock_result_text')

_deterministic_tool_result_text = _make_delegator('manager_results', '_deterministic_tool_result_text')

_whatsapp_report_metadata_text = _make_delegator('manager_results', '_whatsapp_report_metadata_text')

_function_manager_retry = _make_delegator('function_manager', '_function_manager_retry')

_function_manager_job = _make_delegator('function_manager', '_function_manager_job')

_function_manager_future_done = _make_delegator('function_manager', '_function_manager_future_done')

_submit_function_manager_job = _make_delegator('function_manager', '_submit_function_manager_job')

_dual_retry_pending_due = _make_delegator('retry_coordinator', '_dual_retry_pending_due')

_whatsapp_is_retry_command = _make_delegator('retry_coordinator', '_whatsapp_is_retry_command')

_resume_dual_pending_with_message = _make_delegator('retry_coordinator', '_resume_dual_pending_with_message')

_process_dual_codex_message = _make_delegator('conversation', '_process_dual_codex_message')

_job_deadline_seconds = _make_delegator('pending', '_job_deadline_seconds')

_ensure_job_contract = _make_delegator('pending', '_ensure_job_contract')

_save_pending = _make_delegator('pending', '_save_pending')

_archive_pending = _make_delegator('pending', '_archive_pending')

_remove_pending = _make_delegator('pending', '_remove_pending')

_pending_partial_text = _make_delegator('pending', '_pending_partial_text')

_worker_result_fallback_text = _make_delegator('pending', '_worker_result_fallback_text')

_terminate_pending_partial = _make_delegator('pending', '_terminate_pending_partial')

_ensure_pending_approval = _make_delegator('pending', '_ensure_pending_approval')

_approval_notice = _make_delegator('pending', '_approval_notice')

_notify_pending_approval = _make_delegator('pending', '_notify_pending_approval')

_action_result_text = _make_delegator('pending', '_action_result_text')

_complete_action_pending = _make_delegator('pending', '_complete_action_pending')

_dual_task_snapshot = _make_delegator('completion_groups', '_dual_task_snapshot')

_maybe_send_dual_conversation_tick = _make_delegator('completion_groups', '_maybe_send_dual_conversation_tick')

_dual_worker_result_is_meaningful = _make_delegator('completion_groups', '_dual_worker_result_is_meaningful')

_dual_worker_result_is_auth_error = _make_delegator('completion_groups', '_dual_worker_result_is_auth_error')

_maybe_send_dual_auth_notice = _make_delegator('completion_groups', '_maybe_send_dual_auth_notice')

_aggregate_dual_group_results = _make_delegator('completion_groups', '_aggregate_dual_group_results')

_function_manager_requeue_from_sol = _make_delegator('completion_groups', '_function_manager_requeue_from_sol')

_complete_dual_job_group_pending = _make_delegator('completion_groups', '_complete_dual_job_group_pending')

_complete_dual_worker_pending = _make_delegator('completion', '_complete_dual_worker_pending')

_complete_pending = _make_delegator('completion', '_complete_pending')

_message_request_text = _make_delegator('actions', '_message_request_text')

_mobile_screen_context = _make_delegator('actions', '_mobile_screen_context')

_try_create_action_pending = _make_delegator('actions', '_try_create_action_pending')

_find_pending_approval = _make_delegator('actions', '_find_pending_approval')

_post_command_reply = _make_delegator('actions', '_post_command_reply')

_question_approval_command = _make_delegator('question_tokens', '_question_approval_command')

_question_approval_lookup = _make_delegator('question_tokens', '_question_approval_lookup')

_question_bind_token_draft = _make_delegator('question_tokens', '_question_bind_token_draft')

_question_validate_approval_send = _make_delegator('question_tokens', '_question_validate_approval_send')

_regenerate_question_approval_response = _make_delegator('question_tokens', '_regenerate_question_approval_response')

_question_natural_action = _make_delegator('question_tokens', '_question_natural_action')

_question_suggestion_guidance = _make_delegator('question_tokens', '_question_suggestion_guidance')

_question_explicit_response = _make_delegator('question_tokens', '_question_explicit_response')

_handle_question_natural_language = _make_delegator('question_workflow', '_handle_question_natural_language')

_handle_question_approval_command = _make_delegator('question_workflow', '_handle_question_approval_command')

_handle_approval_command = _make_delegator('actions', '_handle_approval_command')

_provider_tool_summary = _make_delegator('processor', '_provider_tool_summary')

_provider_task_attachments = _make_delegator('processor', '_provider_task_attachments')

_whatsapp_execute_source_policy_tools = _make_delegator('processor', '_whatsapp_execute_source_policy_tools')

_whatsapp_provider_task_worker = _make_delegator('processor', '_whatsapp_provider_task_worker')

_create_provider_task = _make_delegator('processor', '_create_provider_task')

_whatsapp_adaptive_reasoning_level = _make_delegator('processor', '_whatsapp_adaptive_reasoning_level')

_create_selected_ai_task = _make_delegator('processor', '_create_selected_ai_task')

_process_message = _make_delegator('processor', '_process_message')

_timing_epoch = _make_delegator('dispatcher', '_timing_epoch')

_record_latency = _make_delegator('dispatcher', '_record_latency')

_record_message_timing = _make_delegator('dispatcher', '_record_message_timing')

_dispatch_phone_key = _make_delegator('dispatcher', '_dispatch_phone_key')

_configure_phone_dispatcher = _make_delegator('dispatcher', '_configure_phone_dispatcher')

_phone_dispatch_capacity = _make_delegator('dispatcher', '_phone_dispatch_capacity')

_phone_dispatch_done = _make_delegator('dispatcher', '_phone_dispatch_done')

_enqueue_phone_event = _make_delegator('dispatcher', '_enqueue_phone_event')

_finish_phone_event = _make_delegator('dispatcher', '_finish_phone_event')

_process_phone_event = _make_delegator('dispatcher', '_process_phone_event')

_drain_phone_events = _make_delegator('dispatcher', '_drain_phone_events')

_dual_waiting_tick_due = _make_delegator('monitor', '_dual_waiting_tick_due')

_expire_dual_pending_if_due = _make_delegator('monitor', '_expire_dual_pending_if_due')

_retry_dual_pending_interrupts = _make_delegator('monitor', '_retry_dual_pending_interrupts')

_monitor_pending = _make_delegator('monitor', '_monitor_pending')

_forward_task_transitions = _make_delegator('monitor', '_forward_task_transitions')

_whatsapp_week_key = _make_delegator('scheduler', '_whatsapp_week_key')

_whatsapp_month_key = _make_delegator('scheduler', '_whatsapp_month_key')

_send_weekly_visual = _make_delegator('scheduler', '_send_weekly_visual')

_remember_pending_weekly_visual = _make_delegator('scheduler', '_remember_pending_weekly_visual')

_flush_pending_weekly_visuals = _make_delegator('scheduler', '_flush_pending_weekly_visuals')

_whatsapp_compact_alert_detail = _make_delegator('scheduler', '_whatsapp_compact_alert_detail')

_whatsapp_weekly_operational_parts = _make_delegator('scheduler', '_whatsapp_weekly_operational_parts')

_forward_operational_alerts = _make_delegator('scheduler', '_forward_operational_alerts')

_start_operational_alert_scan = _make_delegator('scheduler', '_start_operational_alert_scan')

_scheduled_report_period = _make_delegator('scheduler', '_scheduled_report_period')

_scheduled_report_parts = _make_delegator('scheduler', '_scheduled_report_parts')

_send_scheduled_report_visuals = _make_delegator('scheduler', '_send_scheduled_report_visuals')

_scheduled_report_targets = _make_delegator('scheduler', '_scheduled_report_targets')

_scheduled_report_result_accepted = _make_delegator('scheduler', '_scheduled_report_result_accepted')

_forward_scheduled_reports = _make_delegator('scheduler', '_forward_scheduled_reports')

_start_phone_notification_report_scan = _make_delegator('scheduler', '_start_phone_notification_report_scan')

_activate_completed_pairing = _make_delegator('lifecycle', '_activate_completed_pairing')

whatsapp_bridge_poll_once = _make_delegator('lifecycle', 'whatsapp_bridge_poll_once')

_bridge_loop = _make_delegator('lifecycle', '_bridge_loop')

whatsapp_bridge_iniciar_background = _make_delegator('lifecycle', 'whatsapp_bridge_iniciar_background')

whatsapp_bridge_parar_background = _make_delegator('lifecycle', 'whatsapp_bridge_parar_background')

_latency_diagnostics = _make_delegator('api_status', '_latency_diagnostics')

_phone_dispatch_diagnostics = _make_delegator('api_status', '_phone_dispatch_diagnostics')

_public_status = _make_delegator('api_status', '_public_status')

whatsapp_bridge_status = _make_delegator('api_status', 'whatsapp_bridge_status')

whatsapp_bridge_update_config = _make_delegator('api_endpoints', 'whatsapp_bridge_update_config')

whatsapp_bridge_update_phone_settings = _make_delegator('api_endpoints', 'whatsapp_bridge_update_phone_settings')

whatsapp_bridge_register_phone = _make_delegator('api_endpoints', 'whatsapp_bridge_register_phone')

whatsapp_bridge_send_adhoc_message = _make_delegator('api_endpoints', 'whatsapp_bridge_send_adhoc_message')

whatsapp_bridge_test = _make_delegator('api_endpoints', 'whatsapp_bridge_test')

whatsapp_bridge_voice_preflight = _make_delegator('api_endpoints', 'whatsapp_bridge_voice_preflight')

whatsapp_bridge_voice_enable = _make_delegator('api_endpoints', 'whatsapp_bridge_voice_enable')

whatsapp_bridge_voice_disable = _make_delegator('api_endpoints', 'whatsapp_bridge_voice_disable')

whatsapp_bridge_voice_calls = _make_delegator('api_endpoints', 'whatsapp_bridge_voice_calls')

whatsapp_bridge_pairing_code = _make_delegator('api_endpoints', 'whatsapp_bridge_pairing_code')

whatsapp_bridge_revoke = _make_delegator('api_endpoints', 'whatsapp_bridge_revoke')

whatsapp_bridge_sync_templates = _make_delegator('api_endpoints', 'whatsapp_bridge_sync_templates')


__all__ = [
    "WhatsappAdhocMessageRequest",
    "WhatsappBridgeConfigRequest",
    "WhatsappBindingRevokeRequest",
    "WhatsappPairingCodeRequest",
    "WhatsappPhoneRegistrationRequest",
    "WhatsappPhoneSettingsRequest",
    "WhatsappTemplatesRequest",
    "WhatsappVoiceToggleRequest",
    "whatsapp_bridge_download_whisper",
    "whatsapp_bridge_iniciar_background",
    "whatsapp_bridge_pairing_code",
    "whatsapp_bridge_poll_once",
    "whatsapp_bridge_register_phone",
    "whatsapp_bridge_revoke",
    "whatsapp_bridge_send_adhoc_message",
    "whatsapp_bridge_status",
    "whatsapp_bridge_sync_templates",
    "whatsapp_bridge_test",
    "whatsapp_bridge_update_config",
    "whatsapp_bridge_update_phone_settings",
    "whatsapp_bridge_voice_calls",
    "whatsapp_bridge_voice_disable",
    "whatsapp_bridge_voice_enable",
    "whatsapp_bridge_voice_preflight",
]
