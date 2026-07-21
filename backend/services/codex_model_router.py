"""Policy-gated model routing for the JK Sistema internal Codex.

GPT-5.5 remains the baseline and fallback.  A GPT-5.6 variant can only become
effective when the exact task category is enabled by a versioned rollout
policy.  This module does not change the model used by Codex Desktop.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Optional


MODEL_DECISION_VERSION = "1"
BASELINE_MODEL = "gpt-5.5"
GPT_56_VARIANTS: tuple[str, ...] = (
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
)

CATEGORY_CANDIDATES: Mapping[str, str] = {
    "conversation": "gpt-5.6-luna",
    "intent_routing": "gpt-5.6-luna",
    "data_selection": "gpt-5.6-luna",
    "public_question": "gpt-5.6-luna",
    "general": "gpt-5.6-terra",
    "commerce_query": "gpt-5.6-terra",
    "report": "gpt-5.6-terra",
    "context_synthesis": "gpt-5.6-terra",
    "technical": "gpt-5.6-sol",
    "fitment": "gpt-5.6-sol",
    "high_risk": "gpt-5.6-sol",
    "arbitration": "gpt-5.6-sol",
}


def normalize_model_name(value: object) -> str:
    model = str(value or "").strip().lower()
    if model.startswith("codex:"):
        model = model.split(":", 1)[1]
    return model[:100]


def normalize_category(value: object) -> str:
    category = str(value or "general").strip().lower().replace("-", "_").replace(" ", "_")
    return category if category in CATEGORY_CANDIDATES else "unknown"


@dataclass(frozen=True)
class ModelRoutingPolicyV1:
    """Manual, category-specific rollout policy.

    A category absent from ``category_models`` is never promoted, even when
    another category already uses the same model.
    """

    enabled: bool = False
    policy_version: str = "disabled"
    category_models: Mapping[str, str] = field(default_factory=dict)
    category_rollout_percent: Mapping[str, float] = field(default_factory=dict)
    allowed_models: frozenset[str] = field(default_factory=lambda: frozenset({BASELINE_MODEL}))

    def __post_init__(self) -> None:
        if not str(self.policy_version or "").strip():
            raise ValueError("model_policy_version_required")
        for raw_category, raw_model in self.category_models.items():
            category = normalize_category(raw_category)
            model = normalize_model_name(raw_model)
            if category == "unknown" or model not in GPT_56_VARIANTS:
                raise ValueError("model_policy_category_invalid")
            if model != CATEGORY_CANDIDATES[category]:
                raise ValueError("model_policy_variant_category_mismatch")
        for raw_category, raw_percent in self.category_rollout_percent.items():
            if normalize_category(raw_category) == "unknown":
                raise ValueError("model_policy_rollout_category_invalid")
            percent = float(raw_percent)
            if percent < 0.0 or percent > 100.0:
                raise ValueError("model_policy_rollout_percent_invalid")

    @classmethod
    def disabled(cls, policy_version: str = "disabled") -> "ModelRoutingPolicyV1":
        return cls(policy_version=policy_version)

    @classmethod
    def rollout(
        cls,
        *,
        policy_version: str,
        categories: Mapping[str, float],
    ) -> "ModelRoutingPolicyV1":
        models = {
            normalize_category(category): CATEGORY_CANDIDATES[normalize_category(category)]
            for category in categories
            if normalize_category(category) != "unknown"
        }
        allowed = frozenset({BASELINE_MODEL, *models.values()})
        return cls(
            enabled=True,
            policy_version=policy_version,
            category_models=models,
            category_rollout_percent={normalize_category(key): float(value) for key, value in categories.items()},
            allowed_models=allowed,
        )


@dataclass(frozen=True)
class ModelDecisionV1:
    version: str
    category: str
    requested_model: str
    effective_model: str
    baseline_model: str
    candidate_model: str
    policy_version: str
    rollout_percent: float
    rollout_bucket: int
    reason_code: str
    rerouted: bool
    reroute_from: str = ""
    reroute_reason: str = ""
    fallback_chain: tuple[str, ...] = (BASELINE_MODEL,)

    def as_telemetry(self) -> dict[str, Any]:
        """Content-free model dimensions accepted by the telemetry service."""

        return {
            "model_decision_version": self.version,
            "category": self.category,
            "requested_model": self.requested_model,
            "effective_model": self.effective_model,
            "model_rerouted": self.rerouted,
            "model_reason_code": self.reason_code,
            "model_reroute_reason": self.reroute_reason,
            "model_policy_version": self.policy_version,
            "model_rollout_bucket": self.rollout_bucket,
            "model_rollout_percent": self.rollout_percent,
        }

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelRouter:
    """Deterministic policy router with an optional non-blocking audit hook."""

    def __init__(
        self,
        policy: Optional[ModelRoutingPolicyV1] = None,
        *,
        audit_hook: Optional[Callable[[dict[str, Any]], Any]] = None,
    ) -> None:
        self.policy = policy or ModelRoutingPolicyV1.disabled()
        self.audit_hook = audit_hook

    def decide(
        self,
        *,
        category: object,
        requested_model: object = BASELINE_MODEL,
        rollout_key: object,
        available_models: Optional[Iterable[str]] = None,
    ) -> ModelDecisionV1:
        category_code = normalize_category(category)
        requested = normalize_model_name(requested_model) or BASELINE_MODEL
        candidate = CATEGORY_CANDIDATES.get(category_code, "")
        rollout_percent = float(self.policy.category_rollout_percent.get(category_code, 0.0))
        bucket = self._bucket(category_code, rollout_key)
        available = None if available_models is None else {
            normalize_model_name(model) for model in available_models
        }

        effective = BASELINE_MODEL
        reason = "baseline_default"
        if category_code == "unknown":
            reason = "unknown_category"
        elif not self.policy.enabled:
            reason = "policy_disabled"
        elif category_code not in self.policy.category_models:
            reason = "category_not_promoted"
        elif self.policy.category_models.get(category_code) != candidate:
            reason = "category_model_not_permitted"
        elif candidate not in self.policy.allowed_models:
            reason = "model_not_allowed"
        elif bucket >= int(round(rollout_percent * 100)):
            reason = "outside_rollout"
        elif available is not None and candidate not in available:
            reason = "candidate_unavailable"
        else:
            effective = candidate
            reason = "category_rollout"

        if available is not None and effective not in available:
            # GPT-5.5 is the mandatory fallback contract.  Do not silently pick
            # another GPT-5.6 variant when it is missing.
            effective = BASELINE_MODEL
            reason = "baseline_required_unavailable"

        decision = ModelDecisionV1(
            version=MODEL_DECISION_VERSION,
            category=category_code,
            requested_model=requested,
            effective_model=effective,
            baseline_model=BASELINE_MODEL,
            candidate_model=candidate,
            policy_version=str(self.policy.policy_version),
            rollout_percent=rollout_percent,
            rollout_bucket=bucket,
            reason_code=reason,
            rerouted=effective != requested,
            reroute_from=requested if effective != requested else "",
            reroute_reason=reason if effective != requested else "",
            fallback_chain=(candidate, BASELINE_MODEL) if candidate else (BASELINE_MODEL,),
        )
        self._audit(decision)
        return decision

    def fallback(
        self,
        decision: ModelDecisionV1,
        *,
        failed_model: object,
        reason_code: str = "provider_failure",
    ) -> ModelDecisionV1:
        failed = normalize_model_name(failed_model) or decision.effective_model
        fallback = replace(
            decision,
            effective_model=BASELINE_MODEL,
            reason_code="runtime_fallback",
            rerouted=True,
            reroute_from=failed,
            reroute_reason=_safe_code(reason_code, "provider_failure"),
        )
        self._audit(fallback)
        return fallback

    def record_effective_reroute(
        self,
        decision: ModelDecisionV1,
        *,
        effective_model: object,
        reason_code: str,
    ) -> ModelDecisionV1:
        effective = normalize_model_name(effective_model)
        permitted = {BASELINE_MODEL}
        category_model = self.policy.category_models.get(decision.category)
        if self.policy.enabled and category_model in self.policy.allowed_models:
            permitted.add(str(category_model))
        if effective not in permitted:
            effective = BASELINE_MODEL
            reason_code = "unpermitted_runtime_reroute"
        rerouted = replace(
            decision,
            effective_model=effective,
            reason_code="runtime_reroute",
            rerouted=effective != decision.requested_model,
            reroute_from=decision.effective_model,
            reroute_reason=_safe_code(reason_code, "runtime_reroute"),
        )
        self._audit(rerouted)
        return rerouted

    def _bucket(self, category: str, rollout_key: object) -> int:
        material = (
            f"{self.policy.policy_version}|{category}|{str(rollout_key or '')}"
        ).encode("utf-8")
        return int(hashlib.sha256(material).hexdigest()[:8], 16) % 10_000

    def _audit(self, decision: ModelDecisionV1) -> None:
        if not self.audit_hook:
            return
        try:
            self.audit_hook(decision.as_telemetry())
        except Exception:
            # Model selection must not fail because observability is unavailable.
            return


def _safe_code(value: object, fallback: str = "") -> str:
    raw = str(value or "").strip().lower()
    if raw and len(raw) <= 100 and all(ch.isalnum() or ch in "._:/+-" for ch in raw):
        return raw
    return fallback


__all__ = [
    "BASELINE_MODEL",
    "CATEGORY_CANDIDATES",
    "GPT_56_VARIANTS",
    "MODEL_DECISION_VERSION",
    "ModelDecisionV1",
    "ModelRouter",
    "ModelRoutingPolicyV1",
    "normalize_category",
    "normalize_model_name",
]
