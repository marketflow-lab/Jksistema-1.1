from __future__ import annotations

import re
import unicodedata
from typing import Any


TARGET_TYPES = {
    "vehicle",
    "machine_tool",
    "phone_computing",
    "electrical_electronic",
    "hydraulic",
    "dimensional",
    "generic",
}

PROFILE_BY_TARGET_TYPE = {
    "vehicle": "vehicle_fitment",
    "machine_tool": "machine_interface",
    "phone_computing": "device_interface",
    "electrical_electronic": "electrical_interface",
    "hydraulic": "hydraulic_interface",
    "dimensional": "dimensional_fit",
    "generic": "generic_interface",
}

COMPARISON_RESULTS = {"match", "conflict", "missing", "unknown"}

_UNIT_ALIASES = {
    "milimetro": "mm", "milimetros": "mm", "mm": "mm",
    "centimetro": "cm", "centimetros": "cm", "cm": "cm",
    "metro": "m", "metros": "m", "m": "m",
    "polegada": "in", "polegadas": "in", "pol": "in", "in": "in", '"': "in",
    "volt": "V", "volts": "V", "v": "V",
    "hertz": "Hz", "hz": "Hz",
    "watt": "W", "watts": "W", "w": "W",
    "ampere": "A", "amperes": "A", "a": "A",
    "bar": "bar", "psi": "psi",
}


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.lower()).strip()


def normalize_target_type(value: Any, default: str = "generic") -> str:
    normalized = normalize_text(value).replace("-", "_").replace(" ", "_")
    aliases = {
        "vehicle_fitment": "vehicle",
        "car": "vehicle",
        "motorcycle": "vehicle",
        "machine": "machine_tool",
        "tool": "machine_tool",
        "device": "phone_computing",
        "phone": "phone_computing",
        "electrical": "electrical_electronic",
        "electronic": "electrical_electronic",
        "size": "dimensional",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in TARGET_TYPES else (default if default in TARGET_TYPES else "generic")


def normalize_profile(value: Any, target_type: str) -> str:
    allowed = set(PROFILE_BY_TARGET_TYPE.values())
    normalized = normalize_text(value).replace("-", "_").replace(" ", "_")
    return normalized if normalized in allowed else PROFILE_BY_TARGET_TYPE[normalize_target_type(target_type)]


def normalize_unit(value: Any) -> str:
    raw = str(value or "").strip()
    normalized = normalize_text(raw)
    return _UNIT_ALIASES.get(normalized, raw[:24])


def normalize_comparison_attributes(value: Any, limit: int = 16) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value[:limit]:
        if not isinstance(item, dict):
            continue
        attribute = str(item.get("attribute") or item.get("name") or "").strip()[:120]
        if not attribute:
            continue
        result = normalize_text(item.get("result") or item.get("status") or "unknown").replace("-", "_")
        result_aliases = {
            "yes": "match", "compatible": "match", "compativel": "match",
            "no": "conflict", "incompatible": "conflict", "incompativel": "conflict",
            "absent": "missing", "missing_value": "missing",
        }
        result = result_aliases.get(result, result)
        if result not in COMPARISON_RESULTS:
            result = "unknown"
        refs = item.get("evidence_refs") or item.get("sources") or []
        if not isinstance(refs, list):
            refs = [refs] if refs else []
        output.append({
            "attribute": attribute,
            "product_value": str(item.get("product_value") or "").strip()[:300],
            "target_value": str(item.get("target_value") or "").strip()[:300],
            "unit": normalize_unit(item.get("unit")),
            "result": result,
            "decisive": bool(item.get("decisive", True)),
            "evidence_refs": list(dict.fromkeys(str(ref or "").strip()[:300] for ref in refs if str(ref or "").strip()))[:6],
        })
    return output


def profile_language_issues(answer: Any, target_type: Any) -> list[str]:
    normalized_type = normalize_target_type(target_type)
    if normalized_type == "vehicle":
        return []
    text = normalize_text(answer)
    incompatible = (
        "ano do veiculo", "versao do veiculo", "motor do veiculo", "chassi", "numero vin",
        "base original ou paralela", "mecanico de confianca", "oficina de confianca",
    )
    return [term for term in incompatible if term in text]
