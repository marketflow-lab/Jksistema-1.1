"""Minimal public NHTSA vPIC adapter with a fixed, non-redirecting POST."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, Final

import requests

from backend.services.vehicle_identity import VehicleIdentityFactsV1, empty_vehicle_identity
from backend.services.vin_transient import is_valid_vin, normalize_vin


VPIC_DECODE_VIN_VALUES_BATCH_URL: Final = (
    "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVINValuesBatch/"
)
VPIC_TIMEOUT_SECONDS: Final = 15.0

_MAX_FIELD_CHARS = 160
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_YEAR_RE = re.compile(r"(?:18|19|20|21)\d{2}\Z")
_FACT_FIELDS: Final[dict[str, str]] = {
    "Manufacturer": "manufacturer",
    "Make": "make",
    "Model": "model",
    "ModelYear": "model_year",
    "Series": "series",
    "VehicleType": "vehicle_type",
    "BodyClass": "body_class",
    "EngineModel": "engine_model",
    "DisplacementL": "engine_displacement_l",
    "FuelTypePrimary": "fuel_type",
    "PlantCountry": "plant_country",
    "PlantCompanyName": "plant_company",
}


def _safe_fact(value: object, *, forbidden_vin: str = "") -> str:
    rendered = _CONTROL_RE.sub(" ", str(value or ""))
    normalized_vin = normalize_vin(forbidden_vin)
    if normalized_vin:
        grouped_pattern = r"[\s-]*".join(re.escape(character) for character in normalized_vin)
        rendered = re.sub(
            grouped_pattern,
            " ",
            rendered,
            flags=re.IGNORECASE,
        )
        rendered = re.sub(re.escape(normalized_vin), " ", rendered, flags=re.IGNORECASE)
    compact = " ".join(rendered.split())
    return compact[:_MAX_FIELD_CHARS]


def _safe_year(value: object, *, forbidden_vin: str = "") -> str:
    year = _safe_fact(value, forbidden_vin=forbidden_vin)
    return year if _YEAR_RE.fullmatch(year) else ""


def _error_codes(row: Mapping[str, Any]) -> tuple[str, ...]:
    raw = str(row.get("ErrorCode") or "")
    return tuple(code for code in re.findall(r"\d+", raw) if code)


def _facts_from_row(row: Mapping[str, Any], *, forbidden_vin: str = "") -> VehicleIdentityFactsV1:
    values = {
        target: _safe_fact(row.get(source), forbidden_vin=forbidden_vin)
        for source, target in _FACT_FIELDS.items()
    }
    values["model_year"] = _safe_year(row.get("ModelYear"), forbidden_vin=forbidden_vin)
    has_facts = any(values.values())
    if not has_facts:
        return empty_vehicle_identity("not_found", reason="decoder_no_identity")

    core_complete = bool(values["make"] and values["model"] and values["model_year"])
    codes = _error_codes(row)
    has_decoder_error = any(code != "0" for code in codes)
    status = "confirmed" if core_complete and not has_decoder_error else "partial"
    reason = "" if status == "confirmed" else "decoder_partial"
    return VehicleIdentityFactsV1(status=status, reason=reason, **values)


class VpicPublicVinDecoder:
    """Decode a single transient VIN without exposing an endpoint override."""

    def __init__(
        self,
        *,
        timeout_seconds: float = VPIC_TIMEOUT_SECONDS,
        post: Callable[..., Any] | None = None,
    ) -> None:
        self._timeout_seconds = min(30.0, max(1.0, float(timeout_seconds)))
        self._post = post or requests.post

    def decode(self, vin: object) -> VehicleIdentityFactsV1:
        normalized = normalize_vin(vin)
        if not is_valid_vin(normalized):
            return empty_vehicle_identity("invalid", reason="vin_invalid")

        try:
            response = self._post(
                VPIC_DECODE_VIN_VALUES_BATCH_URL,
                data={"data": normalized, "format": "json"},
                headers={
                    "Accept": "application/json",
                    "Cache-Control": "no-store",
                    "Pragma": "no-cache",
                    "User-Agent": "JK-Sistema/vehicle-identity-v1",
                },
                timeout=self._timeout_seconds,
                allow_redirects=False,
            )
        except requests.RequestException:
            return empty_vehicle_identity("unavailable", reason="transport_error")

        status_code = int(getattr(response, "status_code", 0) or 0)
        if 300 <= status_code < 400:
            return empty_vehicle_identity("unavailable", reason="redirect_blocked")
        if status_code == 429:
            return empty_vehicle_identity("unavailable", reason="rate_limited")
        if status_code < 200 or status_code >= 300:
            return empty_vehicle_identity("unavailable", reason="http_error")

        try:
            payload = response.json()
        except (TypeError, ValueError):
            return empty_vehicle_identity("unavailable", reason="invalid_payload")
        if not isinstance(payload, Mapping):
            return empty_vehicle_identity("unavailable", reason="invalid_payload")

        raw_results = payload.get("Results")
        if not isinstance(raw_results, list):
            return empty_vehicle_identity("unavailable", reason="invalid_payload")
        rows = [row for row in raw_results if isinstance(row, Mapping)]
        if not rows:
            return empty_vehicle_identity("not_found", reason="decoder_no_identity")
        if len(rows) != 1:
            return empty_vehicle_identity("ambiguous", reason="decoder_multiple_results")
        return _facts_from_row(rows[0], forbidden_vin=normalized)


__all__ = [
    "VPIC_DECODE_VIN_VALUES_BATCH_URL",
    "VPIC_TIMEOUT_SECONDS",
    "VpicPublicVinDecoder",
]
