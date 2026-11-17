"""Shared GPT capabilities, separated by Codex and direct API transport.

Only public model metadata is cached in memory. Normalization never starts a
runtime or reads business data; discovery is an explicit, bounded operation.
"""

from __future__ import annotations

import copy
import os
import re
import threading
import time
import unicodedata
from typing import Any


DEFAULT_CODEX_MODEL = "gpt-6.1-sol"
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
REASONING_LABELS = {
    "none": "Sem raciocínio", "minimal": "Mínimo", "low": "Baixo",
    "medium": "Médio", "high": "Alto", "xhigh": "Extra alto",
    "max": "Máximo", "ultra": "Ultra",
}
CODEX_MODEL_IDS = (
    "gpt-6.1-sol", "gpt-6-astra", "gpt-6.1-terra", "gpt-6-sol", "gpt-6-luna",
    "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
)
_API_LEGACY_IDS = ("gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.3-codex-spark")
_MODEL_ID = re.compile(r"gpt-[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}\Z")
_LOCK = threading.RLock()
_CACHE: list[dict[str, Any]] = []
_CACHE_AT = 0.0
_ATTEMPT_AT = 0.0
_INFLIGHT: threading.Event | None = None
_CACHE_TTL = 300.0
_RETRY_SECONDS = 30.0


def normalize_codex_model(value: object, default: str = DEFAULT_CODEX_MODEL) -> str:
    model = str(value or default).strip().lower()
    if model.startswith("codex:"):
        model = model.split(":", 1)[1].strip()
    if not model or model == "gpt-5.5":
        model = str(default or DEFAULT_CODEX_MODEL).removeprefix("codex:").strip().lower()
    if not _MODEL_ID.fullmatch(model):
        raise ValueError("invalid_gpt_model")
    return model


def default_codex_model() -> str:
    with _LOCK:
        default = next((row["id"] for row in _CACHE if row.get("is_default") and row["id"] != "gpt-5.5"), None)
        if default:
            return default
        if _CACHE and not any(row["id"] == DEFAULT_CODEX_MODEL for row in _CACHE):
            return next((row["id"] for row in _CACHE if row["id"] != "gpt-5.5"), DEFAULT_CODEX_MODEL)
    return DEFAULT_CODEX_MODEL


def configured_codex_model() -> str:
    return normalize_codex_model(os.getenv("JK_CODEX_MODEL"), default_codex_model())


def _api_provider(provider: str) -> bool:
    return provider in {"openai", "api"}


def _fallback_efforts(model: str, provider: str) -> tuple[str, ...]:
    if _api_provider(provider):
        if model in (*CODEX_MODEL_IDS, "gpt-5.6"):
            base = ("low", "medium", "high", "xhigh", "max")
            return base if model.startswith(("gpt-6-astra", "gpt-6.1-sol")) else ("none", *base)
        if model.startswith(("gpt-5.4", "gpt-5.5")):
            return ("none", "low", "medium", "high", "xhigh")
        if model.startswith("gpt-5.3-codex"):
            return ("low", "medium", "high", "xhigh")
        return ()
    if model in CODEX_MODEL_IDS:
        base = ("low", "medium", "high", "xhigh", "max")
        return base if model.endswith("-luna") else (*base, "ultra")
    # Unknown future models keep the conservative common Codex levels until
    # model/list supplies their actual capabilities.
    return ("low", "medium", "high", "xhigh")


def _fallback_default(model: str, provider: str) -> str:
    if not _api_provider(provider) and model in {"gpt-6.1-sol", "gpt-6.1-terra", "gpt-5.6-sol"}:
        return "low"
    return "medium"


def _display_name(model: str) -> str:
    return "GPT-" + model.removeprefix("gpt-").replace("-", " ").title()


def _fallback_row(model: str, provider: str) -> dict[str, Any]:
    return {
        "id": model,
        "name": model if _api_provider(provider) else f"codex:{model}",
        "display_name": ("" if _api_provider(provider) else "Codex ") + _display_name(model),
        "reasoning_efforts": list(_fallback_efforts(model, provider)),
        "default_reasoning_effort": _fallback_default(model, provider),
        "is_default": model == DEFAULT_CODEX_MODEL,
        "source": "bundled",
        "available": None,
    }


def _plain(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(by_alias=True)
    return {}


def _effort_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def register_codex_models(response: Any) -> list[dict[str, Any]]:
    """Accept model/list from an existing SDK client, retaining no private data."""
    global _CACHE, _CACHE_AT
    payload = _plain(response)
    entries = response if isinstance(response, (list, tuple)) else payload.get("data", payload.get("models", []))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in list(entries or [])[:250]:
        item = _plain(entry)
        slug = str(item.get("id") or item.get("model") or "").strip().lower()
        if not _MODEL_ID.fullmatch(slug) or slug in seen or item.get("hidden") is True:
            continue
        seen.add(slug)
        row = _fallback_row(slug, "codex")
        options = item.get("supportedReasoningEfforts", item.get("supported_reasoning_efforts", item.get("reasoning_efforts", [])))
        efforts = []
        for option in options or []:
            raw = _plain(option)
            effort = _effort_value(raw.get("reasoningEffort", raw.get("reasoning_effort", option)))
            if effort in REASONING_EFFORTS and effort not in efforts:
                efforts.append(effort)
        if efforts:
            row["reasoning_efforts"] = efforts
        default = _effort_value(item.get("defaultReasoningEffort", item.get("default_reasoning_effort")))
        if default in row["reasoning_efforts"]:
            row["default_reasoning_effort"] = default
        elif row["default_reasoning_effort"] not in row["reasoning_efforts"]:
            row["default_reasoning_effort"] = row["reasoning_efforts"][0]
        label = str(item.get("displayName", item.get("display_name")) or "").strip()
        if label and len(label) <= 120 and not re.search(r"[\x00-\x1f<>]", label):
            row["display_name"] = label if label.lower().startswith("codex ") else f"Codex {label}"
        row.update(is_default=bool(item.get("isDefault", item.get("is_default", False))), source="codex_runtime", available=True)
        rows.append(row)
    if rows:
        with _LOCK:
            _CACHE = copy.deepcopy(rows)
            _CACHE_AT = time.monotonic()
    return copy.deepcopy(rows)


def public_model(model: object, *, provider: str = "codex") -> dict[str, Any]:
    slug = str(model or DEFAULT_CODEX_MODEL).strip().lower().removeprefix("codex:")
    if not _MODEL_ID.fullmatch(slug):
        raise ValueError("invalid_gpt_model")
    if not _api_provider(provider):
        with _LOCK:
            row = next((row for row in _CACHE if row["id"] == slug), None)
            if row is not None:
                return copy.deepcopy(row)
    return _fallback_row(slug, provider)


def reasoning_efforts(model: object, *, provider: str = "codex") -> tuple[str, ...]:
    try:
        return tuple(public_model(model, provider=provider)["reasoning_efforts"])
    except ValueError:
        # Runtime aliases are transport-compatible but are not advertised as
        # GPT entries in the public catalog.
        return () if _api_provider(provider) else ("low", "medium", "high", "xhigh")


def default_reasoning(model: object, *, provider: str = "codex") -> str:
    try:
        return str(public_model(model, provider=provider)["default_reasoning_effort"])
    except ValueError:
        return "medium"


def normalize_reasoning(value: object, *, model: object = None, default: str = "medium", provider: str = "codex", strict: bool = False) -> str:
    raw = unicodedata.normalize("NFKD", str(value or default).strip().lower())
    raw = "".join(char for char in raw if not unicodedata.combining(char))
    aliases = {"nenhum": "none", "minimo": "minimal", "baixa": "low", "baixo": "low",
               "media": "medium", "medio": "medium", "alta": "high", "alto": "high",
               "altissimo": "xhigh", "altissima": "xhigh", "extra alto": "xhigh", "maximo": "max"}
    effort = aliases.get(raw, raw)
    if effort not in REASONING_EFFORTS:
        if strict:
            raise ValueError("invalid_reasoning_effort")
        effort = default if default in REASONING_EFFORTS else "medium"
    if model is None:
        return effort
    supported = reasoning_efforts(model, provider=provider)
    if not supported or effort in supported:
        return effort
    # Preserve the requested budget as closely as possible when old settings
    # are reused with a model that does not provide that level.
    rank = REASONING_EFFORTS.index(effort)
    return min(supported, key=lambda candidate: abs(REASONING_EFFORTS.index(candidate) - rank))


def sdk_reasoning_effort(value: object, *, model: object = None, default: str = "medium"):
    # Prefer the shipped SDK before the first import, so current wire values
    # remain representable instead of silently becoming a lower effort.
    from backend.services import ia_providers
    ia_providers._codex_prefer_packaged_provider_sdk()
    from openai_codex.generated.v2_all import ReasoningEffort

    effort = normalize_reasoning(value, model=model, default=default)
    try:
        return ReasoningEffort(effort)
    except ValueError as exc:
        raise RuntimeError("codex_sdk_reasoning_effort_unsupported") from exc


def next_reasoning(value: object, maximum: object, *, model: object) -> str:
    levels = reasoning_efforts(model)
    current = normalize_reasoning(value, model=model)
    limit = normalize_reasoning(maximum, model=model)
    index, cap = levels.index(current), levels.index(limit)
    return levels[min(index + 1, cap)] if index < cap else current


def get_api_fallback_models() -> list[dict[str, Any]]:
    return [_fallback_row(model, "openai") for model in (*CODEX_MODEL_IDS, *_API_LEGACY_IDS)]


def _discover_codex_models() -> Any:
    from backend.services import ia_providers
    ia_providers._codex_prefer_packaged_provider_sdk()
    from backend.services.codex.console import execution
    from openai_codex import Codex, CodexConfig

    if not execution.enabled() or not execution.sdk_installed():
        return []
    execution.apply_sdk_protocol_compat()
    config = CodexConfig(
        codex_bin=execution.require_runtime_ready(), env=execution.sdk_env(),
        config_overrides=execution.readonly_config_overrides(),
    )
    with Codex(config) as client:
        return client.models(include_hidden=False)


def _refresh(event: threading.Event) -> None:
    global _INFLIGHT
    try:
        register_codex_models(_discover_codex_models())
    except Exception:
        # Offline/authentication/runtime failure leaves the last good metadata
        # available. Do not store exception text, paths or credentials.
        pass
    finally:
        with _LOCK:
            if _INFLIGHT is event:
                _INFLIGHT = None
            event.set()


def get_codex_models(*, refresh: bool = False, discover: bool = True) -> list[dict[str, Any]]:
    global _INFLIGHT, _ATTEMPT_AT
    enabled = str(os.getenv("JK_GPT_CATALOG_DISCOVERY_ENABLED", "true")).lower() not in {"0", "false", "off"}
    event = None
    with _LOCK:
        now = time.monotonic()
        stale = not _CACHE or now - _CACHE_AT >= _CACHE_TTL
        if discover and enabled and (refresh or stale):
            if _INFLIGHT is None and (refresh or not _ATTEMPT_AT or now - _ATTEMPT_AT >= _RETRY_SECONDS):
                event = threading.Event()
                _INFLIGHT, _ATTEMPT_AT = event, now
                threading.Thread(target=_refresh, args=(event,), name="jk-gpt-model-catalog", daemon=True).start()
            else:
                event = _INFLIGHT
        has_cache = bool(_CACHE)
    if event is not None and not has_cache:
        event.wait(2.0)
    with _LOCK:
        return copy.deepcopy(_CACHE) if _CACHE else [_fallback_row(model, "codex") for model in CODEX_MODEL_IDS]
