"""Sku Content component."""



from __future__ import annotations



import json
from typing import (
    Any,
    Mapping,
)



from .contracts import (
    _SKU_FORBIDDEN_KEY_PARTS,
    _SKU_SENSITIVE_VALUE_PATTERNS,
)



from .normalization import (
    _canonical_json,
    _slug,
    _normal_key,
)









from backend.services.compatibility_coverage import (
    COMPATIBILITY_COVERAGE_VERSION,
    build_compatibility_coverage,
)



def _contains_forbidden_sku_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _normal_key(key)
            if any(part in normalized for part in _SKU_FORBIDDEN_KEY_PARTS):
                return True
            if _contains_forbidden_sku_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_sku_key(item) for item in value)
    return False

def _contains_sensitive_sku_value(value: Mapping[str, Any]) -> bool:
    serialized = _canonical_json(value)
    return any(pattern.search(serialized) for pattern in _SKU_SENSITIVE_VALUE_PATTERNS)

def _sku_section(data: Mapping[str, Any], normalized_name: str) -> Any:
    for key, value in data.items():
        if _normal_key(key) == normalized_name:
            return value
    return None

def _sku_top_keys(data: Mapping[str, Any]) -> set[str]:
    return {_normal_key(key) for key in data}

def _sku_items(section: Any, field: str = "itens") -> list[Any]:
    if not isinstance(section, dict):
        return []
    for key, value in section.items():
        if _normal_key(key) == field and isinstance(value, list):
            return value
    return []

def _sku_field(section: Any, field: str, default: Any = "") -> Any:
    if not isinstance(section, dict):
        return default
    for key, value in section.items():
        if _normal_key(key) == field:
            return value
    return default

def _sku_family(name: str, application_type: str) -> str:
    stop = {"a", "as", "com", "da", "das", "de", "do", "dos", "e", "em", "para", "por"}
    words = [word for word in _slug(name).split("-") if word and word not in stop]
    noun = "-".join(words[:2]) or "produto"
    return f"{_slug(application_type, fallback='geral')}:{noun}"

def build_sku_content(data: Mapping[str, Any], sku: str, family: str) -> tuple[str, dict[str, Any]]:
    name = str(data.get("nome_produto") or "").strip()
    description = str(_sku_section(data, "o_que_e") or "").strip()
    application = _sku_section(data, "aplicacao")
    application_type = str(_sku_field(application, "tipo", "geral") or "geral")
    characteristics = [str(item) for item in _sku_items(_sku_section(data, "caracteristicas_tecnicas")) if isinstance(item, str)]
    uses = [str(item) for item in _sku_items(_sku_section(data, "para_que_serve")) if isinstance(item, str)]
    vehicles_section = _sku_field(application, "veiculos_compativeis", {})
    vehicles: list[str] = []
    for item in _sku_items(vehicles_section):
        if not isinstance(item, dict):
            continue
        brand = str(_sku_field(item, "marca", "") or "").strip()
        model = str(_sku_field(item, "modelo", "") or "").strip()
        years = _sku_field(item, "anos", [])
        years_text = ", ".join(str(year) for year in years) if isinstance(years, list) else ""
        label = " ".join(part for part in (brand, model) if part)
        if years_text:
            label += f" ({years_text})"
        if label:
            vehicles.append(label)
    equipment_section = _sku_field(application, "equipamentos_ou_aplicacoes_compativeis", {})
    equipment = [str(item) for item in _sku_items(equipment_section) if isinstance(item, str)]
    vehicle_status = str(_sku_field(vehicles_section, "status", "") or "").strip()
    vehicle_observation = str(_sku_field(vehicles_section, "observacao", "") or "").strip()
    equipment_status = str(_sku_field(equipment_section, "status", "") or "").strip()
    equipment_observation = str(_sku_field(equipment_section, "observacao", "") or "").strip()
    compatibility_coverage = build_compatibility_coverage(data, sku)
    lines = [f"SKU: {sku}", f"Produto: {name}", f"Familia: {family}"]
    if description:
        lines.append(f"Descricao: {description}")
    if uses:
        lines.append("Uso: " + " ".join(uses))
    if characteristics:
        lines.append("Caracteristicas: " + " ".join(characteristics))
    if vehicles:
        lines.append("Veiculos compativeis: " + "; ".join(vehicles))
    if vehicle_status:
        lines.append("Status das aplicacoes veiculares: " + vehicle_status)
    if vehicle_observation:
        lines.append("Observacao das aplicacoes veiculares: " + vehicle_observation)
    if equipment:
        lines.append("Aplicacoes compativeis: " + "; ".join(equipment))
    if equipment_status:
        lines.append("Status das aplicacoes compativeis: " + equipment_status)
    if equipment_observation:
        lines.append("Observacao das aplicacoes compativeis: " + equipment_observation)
    if compatibility_coverage.get("rules"):
        lines.append(
            "CompatibilityCoverageV1: "
            + json.dumps(compatibility_coverage, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    allowed = {
        "schema_version": data.get("schema_version"),
        "sku": sku,
        "nome_produto": name,
        "o_que_e": description,
        "para_que_serve": uses,
        "caracteristicas_tecnicas": characteristics,
        "aplicacao_tipo": application_type,
        "veiculos": vehicles,
        "equipamentos": equipment,
        "veiculos_status": vehicle_status,
        "veiculos_observacao": vehicle_observation,
        "equipamentos_status": equipment_status,
        "equipamentos_observacao": equipment_observation,
        "compatibility_coverage_version": COMPATIBILITY_COVERAGE_VERSION,
        "compatibility_coverage": compatibility_coverage,
        "revisao": _sku_section(data, "revisao"),
    }
    return "\n".join(lines), allowed
