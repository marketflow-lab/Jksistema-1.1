"""Ephemeral VIN capture with mandatory sanitization and one-shot storage."""

from __future__ import annotations

import re
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Final, Literal


VIN_MARKER: Final = "[CHASSI_PROTEGIDO]"
VIN_ENVELOPE_TTL_SECONDS: Final = 60.0
VIN_ENVELOPE_MAX_ENTRIES: Final = 1024

VinCaptureStatus = Literal["absent", "captured", "ambiguous", "invalid"]

_VIN_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{17})(?![A-Z0-9])", re.IGNORECASE)
_VIN_VALID_RE = re.compile(r"[A-HJ-NPR-Z0-9]{17}\Z", re.IGNORECASE)
_VIN_LABEL_RE = re.compile(r"\b(?:chassi|chassis|vin)\b", re.IGNORECASE)
_VIN_FIELD_RE = re.compile(r"(?:^|[_\s-])(?:chassi|chassis|vin)(?:$|[_\s-])", re.IGNORECASE)
_CLAUSE_BREAK_RE = re.compile(r"[.!?;\r\n]")
_LABEL_PREFIX_RE = re.compile(
    r"^(?:\s|[:#=\-]|n(?:[uú]mero|ro|[º°])|do|da|é|e){0,32}",
    re.IGNORECASE,
)
_ALNUM_GROUP_RE = re.compile(r"[A-Z0-9]+", re.IGNORECASE)
_VIN_LIKE_TOKEN_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{17})(?![A-Z0-9])", re.IGNORECASE)
_VIN_GROUPED_LIKE_RE = re.compile(
    r"(?<![A-Z0-9])((?:[A-Z0-9]{1,5}[ -]+){2,20}[A-Z0-9]{1,5})(?![A-Z0-9])",
    re.IGNORECASE,
)


def normalize_vin(value: object) -> str:
    """Normalize only harmless display separators; never guess characters."""

    return re.sub(r"[\s-]+", "", str(value or "")).upper()


def is_valid_vin(value: object) -> bool:
    return bool(_VIN_VALID_RE.fullmatch(normalize_vin(value)))


@dataclass(frozen=True, slots=True)
class VinCaptureV1:
    status: VinCaptureStatus
    detected_count: int
    sanitized_text: str = field(repr=False)
    envelope_token: str = field(default="", repr=False)

    def safe_metadata(self) -> dict[str, object]:
        return {
            "status": self.status,
            "detected_count": max(0, int(self.detected_count)),
            "has_envelope": bool(self.envelope_token),
        }


@dataclass(frozen=True, slots=True)
class VinPayloadCaptureV1:
    status: VinCaptureStatus
    detected_count: int
    sanitized_payload: Any = field(repr=False)
    envelope_token: str = field(default="", repr=False)

    def safe_metadata(self) -> dict[str, object]:
        return {
            "status": self.status,
            "detected_count": max(0, int(self.detected_count)),
            "has_envelope": bool(self.envelope_token),
        }


@dataclass(frozen=True, slots=True)
class _VinCandidate:
    start: int
    end: int
    normalized: str = field(repr=False)
    valid: bool = False


@dataclass(frozen=True, slots=True)
class _EnvelopeRecord:
    vin: str = field(repr=False)
    expires_at: float


class TransientVinEnvelopeStore:
    """Process-local capability store; values are consumed at most once."""

    def __init__(
        self,
        *,
        ttl_seconds: float = VIN_ENVELOPE_TTL_SECONDS,
        max_entries: int = VIN_ENVELOPE_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        requested_ttl = float(ttl_seconds)
        self._ttl_seconds = min(VIN_ENVELOPE_TTL_SECONDS, max(0.001, requested_ttl))
        self._max_entries = max(1, min(VIN_ENVELOPE_MAX_ENTRIES, int(max_entries)))
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self._records: OrderedDict[str, _EnvelopeRecord] = OrderedDict()
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.RLock()

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def create(self, vin: object) -> str:
        normalized = normalize_vin(vin)
        if not is_valid_vin(normalized):
            raise ValueError("vin_invalid")

        with self._lock:
            for _attempt in range(8):
                token = str(self._token_factory() or "").strip()
                if token and token not in self._records:
                    return self._put_locked(token, normalized, float(self._clock()))
        raise RuntimeError("vin_envelope_token_unavailable")

    def put(self, envelope_key: object, vin: object) -> str:
        """Bind a VIN to an opaque in-process job key, replacing stale input."""

        key = str(envelope_key or "").strip()
        if not key or len(key) > 256:
            raise ValueError("vin_envelope_key_invalid")
        normalized = normalize_vin(vin)
        if not is_valid_vin(normalized):
            raise ValueError("vin_invalid")
        with self._lock:
            return self._put_locked(key, normalized, float(self._clock()))

    def consume(self, token: object) -> str | None:
        safe_token = str(token or "").strip()
        if not safe_token:
            return None
        now = float(self._clock())
        with self._lock:
            record = self._records.pop(safe_token, None)
            self._cancel_timer_locked(safe_token)
        if record is None or now >= record.expires_at:
            return None
        return record.vin

    def discard(self, token: object) -> bool:
        safe_token = str(token or "").strip()
        if not safe_token:
            return False
        with self._lock:
            removed = self._records.pop(safe_token, None) is not None
            self._cancel_timer_locked(safe_token)
            return removed

    def discard_family(self, token_prefix: object) -> int:
        """Discard every generation bound to one opaque job identifier."""

        prefix = str(token_prefix or "").strip()
        if not prefix or len(prefix) > 256:
            return 0
        family_prefix = prefix + ":"
        with self._lock:
            tokens = [
                token
                for token in self._records
                if token == prefix or token.startswith(family_prefix)
            ]
            for token in tokens:
                self._records.pop(token, None)
                self._cancel_timer_locked(token)
            return len(tokens)

    def rebind(self, source_token: object, target_token: object) -> bool:
        """Move an unopened envelope without exposing or extending its VIN lifetime."""

        source = str(source_token or "").strip()
        target = str(target_token or "").strip()
        if not source or not target or len(target) > 256:
            return False
        now = float(self._clock())
        with self._lock:
            record = self._records.pop(source, None)
            self._cancel_timer_locked(source)
            if record is None or now >= record.expires_at:
                return False
            self._records.pop(target, None)
            self._cancel_timer_locked(target)
            self._records[target] = record
            timer: threading.Timer | None = None
            try:
                timer = threading.Timer(
                    max(0.001, record.expires_at - now),
                    self._expire_key,
                    args=(target, record.expires_at),
                )
                timer.daemon = True
                self._timers[target] = timer
                timer.start()
            except Exception:
                self._timers.pop(target, None)
                current = self._records.get(target)
                if current is not None and current.expires_at == record.expires_at:
                    self._records.pop(target, None)
                if timer is not None:
                    try:
                        timer.cancel()
                    except Exception:
                        pass
                raise
            return True

    def purge_expired(self) -> int:
        with self._lock:
            return self._purge_expired_locked(float(self._clock()))

    def _purge_expired_locked(self, now: float) -> int:
        expired = [token for token, record in self._records.items() if now >= record.expires_at]
        for token in expired:
            self._records.pop(token, None)
            self._cancel_timer_locked(token)
        return len(expired)

    def _cancel_timer_locked(self, key: str) -> None:
        timer = self._timers.pop(key, None)
        if timer is not None and timer is not threading.current_thread():
            timer.cancel()

    def _expire_key(self, key: str, expires_at: float) -> None:
        with self._lock:
            record = self._records.get(key)
            if record is not None and record.expires_at == expires_at:
                self._records.pop(key, None)
            self._timers.pop(key, None)

    def _put_locked(self, key: str, normalized: str, now: float) -> str:
        self._purge_expired_locked(now)
        self._records.pop(key, None)
        self._cancel_timer_locked(key)
        while len(self._records) >= self._max_entries:
            oldest_key, _record = self._records.popitem(last=False)
            self._cancel_timer_locked(oldest_key)
        expires_at = now + self._ttl_seconds
        self._records[key] = _EnvelopeRecord(
            vin=normalized,
            expires_at=expires_at,
        )
        timer: threading.Timer | None = None
        try:
            timer = threading.Timer(self._ttl_seconds, self._expire_key, args=(key, expires_at))
            timer.daemon = True
            self._timers[key] = timer
            timer.start()
        except Exception:
            # Arming a timer can fail under process/thread exhaustion.  Do not
            # leave the raw value behind when the TTL mechanism was not armed.
            self._timers.pop(key, None)
            current = self._records.get(key)
            if current is not None and current.expires_at == expires_at:
                self._records.pop(key, None)
            if timer is not None:
                try:
                    timer.cancel()
                except Exception:
                    pass
            raise
        return key


def contains_vin_like_identifier(value: object) -> bool:
    """Reject VIN-shaped operational identifiers before hashing or persistence."""

    text = str(value or "")
    if _find_candidates(text):
        return True
    compact_text = normalize_vin(text)
    if (
        len(compact_text) == 17
        and is_valid_vin(compact_text)
        and bool(re.fullmatch(r"[A-Z0-9\s-]{17,48}", text.strip(), flags=re.IGNORECASE))
    ):
        return True
    for match in _VIN_LIKE_TOKEN_RE.finditer(text):
        candidate = match.group(1)
        if is_valid_vin(candidate) or sum(character.isdigit() for character in candidate) >= 2:
            return True
    for match in _VIN_GROUPED_LIKE_RE.finditer(text):
        compact = normalize_vin(match.group(1))
        if len(compact) == 17 and (
            is_valid_vin(compact)
            or sum(character.isdigit() for character in compact) >= 2
        ):
            return True
    return False


def _sanitize_mapping_key(key: Any) -> tuple[Any, list[_VinCandidate]]:
    if not isinstance(key, str):
        return key, []
    candidates = list(_find_candidates(key))
    for match in _VIN_LIKE_TOKEN_RE.finditer(key):
        raw = match.group(1).upper()
        if not is_valid_vin(raw) and sum(character.isdigit() for character in raw) < 2:
            continue
        candidate = _VinCandidate(
            match.start(1),
            match.end(1),
            raw,
            is_valid_vin(raw),
        )
        if any(
            candidate.start < current.end and current.start < candidate.end
            for current in candidates
        ):
            continue
        candidates.append(candidate)
    candidates.sort(key=lambda item: (item.start, item.end))
    return _sanitize_candidates(key, candidates), candidates


def _associated_with_label(text: str, start: int, end: int, labels: list[re.Match[str]]) -> bool:
    for label in labels:
        if label.end() <= start:
            between = text[label.end() : start]
        elif end <= label.start():
            between = text[end : label.start()]
        else:
            return True
        if len(between) <= 64 and not _CLAUSE_BREAK_RE.search(between):
            return True
    return False


def _candidate_after_label(text: str, label: re.Match[str]) -> _VinCandidate | None:
    window_end = min(len(text), label.end() + 96)
    remainder = text[label.end() : window_end]
    prefix = _LABEL_PREFIX_RE.match(remainder)
    cursor = int(prefix.end() if prefix else 0)
    chunks: list[tuple[str, int, int]] = []
    total = 0

    while cursor < len(remainder) and len(chunks) < 8:
        match = _ALNUM_GROUP_RE.match(remainder, cursor)
        if not match:
            break
        raw = match.group(0)
        chunks.append((raw, match.start(), match.end()))
        total += len(raw)
        if total >= 17:
            break
        separator = re.match(r"[ -]+", remainder[match.end() :])
        if not separator:
            break
        cursor = match.end() + separator.end()

    if not chunks:
        return None

    candidate_start = label.end() + chunks[0][1]
    candidate_end = label.end() + chunks[-1][2]
    joined = "".join(chunk for chunk, _start, _end in chunks).upper()
    if len(joined) == 17:
        if is_valid_vin(joined):
            return _VinCandidate(candidate_start, candidate_end, joined, True)
        if sum(character.isdigit() for character in joined) < 2:
            return None
        if len(chunks) > 1:
            chunk_lengths_ok = all(2 <= len(chunk) <= 8 for chunk, _start, _end in chunks)
            digit_chunks = sum(any(char.isdigit() for char in chunk) for chunk, _start, _end in chunks)
            if not chunk_lengths_ok or digit_chunks < 2:
                return _VinCandidate(candidate_start, candidate_end, joined, False)
        return _VinCandidate(candidate_start, candidate_end, joined, False)

    if sum(character.isdigit() for character in joined) < 2:
        return None

    # A labelled but incomplete/oversized identifier is sanitized and rejected.
    if 6 <= len(joined) <= 40:
        return _VinCandidate(candidate_start, candidate_end, joined, False)
    return None


def _find_candidates(text: str) -> list[_VinCandidate]:
    labels = list(_VIN_LABEL_RE.finditer(text))
    if not labels:
        return []

    candidates: list[_VinCandidate] = []
    for match in _VIN_RE.finditer(text):
        if _associated_with_label(text, match.start(1), match.end(1), labels):
            normalized = match.group(1).upper()
            candidates.append(
                _VinCandidate(match.start(1), match.end(1), normalized, is_valid_vin(normalized))
            )

    for label in labels:
        candidate = _candidate_after_label(text, label)
        if candidate is None:
            continue
        if any(candidate.start < current.end and current.start < candidate.end for current in candidates):
            continue
        candidates.append(candidate)

    unique: list[_VinCandidate] = []
    for candidate in sorted(candidates, key=lambda item: (item.start, item.end)):
        if any(candidate.start == item.start and candidate.end == item.end for item in unique):
            continue
        unique.append(candidate)
    return unique


def _sanitize_candidates(text: str, candidates: list[_VinCandidate]) -> str:
    sanitized = text
    for candidate in sorted(candidates, key=lambda item: item.start, reverse=True):
        sanitized = f"{sanitized[:candidate.start]}{VIN_MARKER}{sanitized[candidate.end:]}"
    return sanitized


def sanitize_vin_like_text(value: object) -> str:
    """Redact labelled VIN/identifier candidates without creating an envelope."""

    text = str(value or "")
    return _sanitize_candidates(text, _find_candidates(text))


def _find_field_candidates(text: str, *, labelled_field: bool) -> list[_VinCandidate]:
    candidates = _find_candidates(text)
    if candidates or not labelled_field:
        return candidates

    synthetic_prefix = "VIN: "
    synthetic = _find_candidates(f"{synthetic_prefix}{text}")
    candidates = [
        _VinCandidate(
            candidate.start - len(synthetic_prefix),
            candidate.end - len(synthetic_prefix),
            candidate.normalized,
            candidate.valid,
        )
        for candidate in synthetic
        if candidate.start >= len(synthetic_prefix)
    ]
    if candidates:
        return candidates

    leading = len(text) - len(text.lstrip())
    trailing = len(text.rstrip())
    raw_value = text[leading:trailing]
    normalized = normalize_vin(raw_value)
    if (
        6 <= len(normalized) <= 40
        and normalized.isalnum()
        and sum(character.isdigit() for character in normalized) >= 2
    ):
        return [
            _VinCandidate(
                leading,
                trailing,
                normalized,
                len(normalized) == 17 and is_valid_vin(normalized),
            )
        ]
    return []


def _sanitize_payload(
    value: Any,
    *,
    labelled_field: bool = False,
) -> tuple[Any, list[_VinCandidate]]:
    if isinstance(value, str):
        candidates = _find_field_candidates(value, labelled_field=labelled_field)
        return _sanitize_candidates(value, candidates), candidates
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        found: list[_VinCandidate] = []
        for index, (key, nested) in enumerate(value.items(), start=1):
            sanitized_key, key_candidates = _sanitize_mapping_key(key)
            if sanitized_key in sanitized:
                sanitized_key = f"protected_field_{index}"
            nested_value, nested_candidates = _sanitize_payload(
                nested,
                labelled_field=bool(_VIN_FIELD_RE.search(str(key or ""))),
            )
            sanitized[sanitized_key] = nested_value
            found.extend(key_candidates)
            found.extend(nested_candidates)
        return sanitized, found
    if isinstance(value, list):
        sanitized_list: list[Any] = []
        found = []
        for nested in value:
            nested_value, nested_candidates = _sanitize_payload(
                nested,
                labelled_field=labelled_field,
            )
            sanitized_list.append(nested_value)
            found.extend(nested_candidates)
        return sanitized_list, found
    if isinstance(value, tuple):
        sanitized_items: list[Any] = []
        found = []
        for nested in value:
            nested_value, nested_candidates = _sanitize_payload(
                nested,
                labelled_field=labelled_field,
            )
            sanitized_items.append(nested_value)
            found.extend(nested_candidates)
        return tuple(sanitized_items), found
    return value, []


DEFAULT_VIN_ENVELOPE_STORE = TransientVinEnvelopeStore()


def capture_vin(
    text: object,
    *,
    envelope_store: TransientVinEnvelopeStore = DEFAULT_VIN_ENVELOPE_STORE,
) -> VinCaptureV1:
    """Capture one labelled VIN, returning only sanitized durable text."""

    source_text = str(text or "")
    candidates = _find_candidates(source_text)
    if not candidates:
        return VinCaptureV1(status="absent", detected_count=0, sanitized_text=source_text)

    sanitized = _sanitize_candidates(source_text, candidates)
    if len(candidates) != 1:
        return VinCaptureV1(
            status="ambiguous",
            detected_count=len(candidates),
            sanitized_text=sanitized,
        )

    candidate = candidates[0]
    if not candidate.valid:
        return VinCaptureV1(status="invalid", detected_count=1, sanitized_text=sanitized)

    token = envelope_store.create(candidate.normalized)
    return VinCaptureV1(
        status="captured",
        detected_count=1,
        sanitized_text=sanitized,
        envelope_token=token,
    )


def capture_vin_payload(
    payload: Any,
    *,
    envelope_store: TransientVinEnvelopeStore = DEFAULT_VIN_ENVELOPE_STORE,
    envelope_key: object = "",
    retain_envelope: bool = True,
) -> VinPayloadCaptureV1:
    """Recursively sanitize a JSON-like payload and bind one VIN in memory.

    Repeated occurrences of the same VIN are treated as one identity. Distinct
    VINs, or a mix of valid and invalid labelled identifiers, are ambiguous and
    no envelope is created.
    """

    sanitized_payload, candidates = _sanitize_payload(payload)
    if not candidates:
        return VinPayloadCaptureV1(
            status="absent",
            detected_count=0,
            sanitized_payload=sanitized_payload,
        )

    valid_values = {candidate.normalized for candidate in candidates if candidate.valid}
    invalid_count = sum(not candidate.valid for candidate in candidates)
    detected_count = len(valid_values) + invalid_count
    if (invalid_count and valid_values) or len(valid_values) > 1:
        return VinPayloadCaptureV1(
            status="ambiguous",
            detected_count=detected_count,
            sanitized_payload=sanitized_payload,
        )
    if invalid_count:
        return VinPayloadCaptureV1(
            status="invalid",
            detected_count=invalid_count,
            sanitized_payload=sanitized_payload,
        )

    normalized = next(iter(valid_values))
    token = ""
    if retain_envelope:
        token = (
            envelope_store.put(envelope_key, normalized)
            if str(envelope_key or "").strip()
            else envelope_store.create(normalized)
        )
    return VinPayloadCaptureV1(
        status="captured",
        detected_count=1,
        sanitized_payload=sanitized_payload,
        envelope_token=token,
    )


__all__ = [
    "DEFAULT_VIN_ENVELOPE_STORE",
    "TransientVinEnvelopeStore",
    "VIN_ENVELOPE_MAX_ENTRIES",
    "VIN_ENVELOPE_TTL_SECONDS",
    "VIN_MARKER",
    "VinCaptureStatus",
    "VinCaptureV1",
    "VinPayloadCaptureV1",
    "capture_vin",
    "capture_vin_payload",
    "contains_vin_like_identifier",
    "is_valid_vin",
    "normalize_vin",
]
