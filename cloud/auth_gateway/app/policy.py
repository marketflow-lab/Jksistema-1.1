"""Password, access, tenant, machine and rate-limit policy."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import bcrypt

from .errors import AuthRejected


AUTH_PROTOCOL_VERSION = 1
PERMISSION_KEYS = (
    "analise_promo",
    "renovacao_fixa",
    "vendas",
    "estoque",
    "integracao",
    "etiquetas",
    "full",
    "favoritos",
    "avant",
    "perguntas_pos_venda",
    "anuncios_ml",
    "medias_compras",
    "mercado_full",
    "cadastro",
    "impostos",
    "configuracoes",
    "importacoes",
    "simulador",
    "sala_reuniao",
    "admin_usuarios",
)


def truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "sim", "verdadeiro", "yes", "y"}
    return bool(value)


def normalize_permissions(value: Any) -> dict[str, bool]:
    source = value if isinstance(value, dict) else {}
    normalized = {key: truthy(source.get(key)) for key in PERMISSION_KEYS}
    if "avant" not in source:
        normalized["avant"] = normalized["favoritos"]
    if normalized["full"]:
        normalized = {key: True for key in PERMISSION_KEYS}
    return normalized


def normalize_machine_ids(value: Any, primary: Any = None) -> list[str]:
    source = value
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except (TypeError, ValueError):
            source = re.split(r"[\n,;|]+", source)
    candidates = list(source) if isinstance(source, (list, tuple, set)) else []
    if primary:
        candidates.append(primary)
    result: list[str] = []
    for item in candidates:
        machine_id = str(item or "").strip()
        if machine_id and machine_id not in result:
            result.append(machine_id)
    return result


def normalize_max_machines(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 1
    return max(0, parsed)


def password_value(user: dict[str, Any]) -> str:
    return str(user.get("password_hash") or user.get("password") or user.get("senha") or "")


def password_matches(candidate: str, stored: str) -> bool:
    if not candidate or not stored:
        return False
    if stored.startswith("$2"):
        try:
            return bcrypt.checkpw(candidate.encode("utf-8"), stored.encode("utf-8"))
        except (ValueError, TypeError):
            return False
    return secrets.compare_digest(candidate, stored)


def is_user_active(user: dict[str, Any]) -> bool:
    value = user.get("active", user.get("ativo", True))
    if isinstance(value, str):
        return value.strip().lower() not in {
            "0",
            "false",
            "falso",
            "nao",
            "não",
            "inativo",
            "bloqueado",
            "off",
        }
    return bool(value)


def normalized_expiry(user: dict[str, Any]) -> str | None:
    raw = str(user.get("valid_until") or user.get("validade") or user.get("vencimento") or "").strip()
    if not raw:
        return None
    for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:10], pattern).strftime("%d/%m/%Y")
        except ValueError:
            continue
    raise AuthRejected("access_policy_invalid", 403)


def assert_access_allowed(user: dict[str, Any]) -> str | None:
    if not is_user_active(user):
        raise AuthRejected("inactive_user", 403)
    expiry = normalized_expiry(user)
    if expiry:
        expiry_date = datetime.strptime(expiry, "%d/%m/%Y").date()
        today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        if today > expiry_date:
            raise AuthRejected("expired_access", 403)
    return expiry


def version_key(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"\s*v?(\d+(?:\.\d+){0,3})(?:[-+][0-9A-Za-z.-]+)?\s*", str(value or ""))
    if not match:
        return tuple()
    parts = tuple(int(part) for part in match.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def assert_supported_version(current: str, minimum: str) -> None:
    current_key = version_key(current)
    minimum_key = version_key(minimum)
    if not current_key or not minimum_key or current_key < minimum_key:
        raise AuthRejected("update_required", 426)


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def machine_hash(machine_id: str) -> str:
    return hashlib.sha256(str(machine_id).encode("utf-8")).hexdigest()


def uid_for_username(username: str) -> str:
    return "jk-" + hashlib.sha256(username.encode("utf-8")).hexdigest()[:40]


def user_client_id(user: dict[str, Any]) -> str:
    return str(user.get("client_id") or user.get("cliente") or user.get("tenant_id") or "default").strip() or "default"


def user_can_use_unlimited_devices(username: str, permissions: dict[str, bool]) -> bool:
    return bool(permissions.get("full")) or username in {"admin", "administrador"}


def machine_claim_updates(user: dict[str, Any], username: str, machine_id: str) -> dict[str, Any]:
    assert_access_allowed(user)
    permissions = normalize_permissions(user.get("permissions") or user.get("permissoes"))
    machines = normalize_machine_ids(
        user.get("machine_ids") or user.get("maquinas") or user.get("machines"),
        user.get("machine_id"),
    )
    if machine_id in machines:
        return {}
    maximum = normalize_max_machines(user.get("max_machines", user.get("limite_maquinas", 1)))
    if not user_can_use_unlimited_devices(username, permissions) and maximum != 0 and len(machines) >= maximum:
        raise AuthRejected("device_limit", 403)
    machines.append(machine_id)
    return {"machine_id": machines[0], "machine_ids": machines}


def assert_registered_machine(user: dict[str, Any], machine_id: str) -> None:
    machines = normalize_machine_ids(
        user.get("machine_ids") or user.get("maquinas") or user.get("machines"),
        user.get("machine_id"),
    )
    if machine_id not in machines:
        raise AuthRejected("machine_not_registered", 403)


class LoginRateLimiter:
    """Small per-instance guard; Cloud Armor can add a distributed outer layer."""

    def __init__(self, attempts: int, window_seconds: int) -> None:
        self._attempts = attempts
        self._window = window_seconds
        self._entries: dict[str, deque[float]] = defaultdict(deque)
        self._salt = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._maximum_keys = 10_000
        self._last_sweep = time.monotonic()

    def _sweep(self, now: float) -> None:
        if len(self._entries) < self._maximum_keys and now - self._last_sweep < self._window:
            return
        cutoff = now - self._window
        stale = [key for key, entries in self._entries.items() if not entries or entries[-1] < cutoff]
        for key in stale:
            self._entries.pop(key, None)
        self._last_sweep = now

    def consume(self, username: str, address: str) -> bool:
        material = f"{username}|{address}".encode("utf-8", errors="ignore")
        key = hashlib.sha256(self._salt + material).hexdigest()
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            if key not in self._entries and len(self._entries) >= self._maximum_keys:
                return False
            entries = self._entries[key]
            cutoff = now - self._window
            while entries and entries[0] < cutoff:
                entries.popleft()
            if len(entries) >= self._attempts:
                return False
            entries.append(now)
            return True


__all__ = [
    "AUTH_PROTOCOL_VERSION",
    "LoginRateLimiter",
    "assert_access_allowed",
    "assert_registered_machine",
    "assert_supported_version",
    "canonical_hash",
    "machine_claim_updates",
    "machine_hash",
    "normalize_machine_ids",
    "normalize_max_machines",
    "normalize_permissions",
    "password_matches",
    "password_value",
    "uid_for_username",
    "user_can_use_unlimited_devices",
    "user_client_id",
]
