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

_DIRECT_COMPATIBILITY_RE = re.compile(
    r"\b(?:serve|servir|serviria|servem|compativel|compatibilidade|aplica|aplicacao|encaixa|adapta|adaptavel)\b",
    re.IGNORECASE,
)
_TARGETED_COMPATIBILITY_RE = re.compile(
    r"\b(?:funciona|funcionaria|cabe|caberia)\b.{0,60}\b(?:no|na|nos|nas|em|para|com)\b",
    re.IGNORECASE,
)
_COMPATIBILITY_TARGET_PATTERNS = (
    re.compile(
        r"\b(?:serve|servir|serviria|aplica|aplicacao|encaixa|adapta|adaptavel|compativel|funciona|funcionaria|cabe|caberia)"
        r"\b.*?\b(?:no|na|nos|nas|em|para|com)\s+(.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bpara\s+(.+?)\s+(?:serve|serviria|funciona|funcionaria|cabe|caberia|encaixa|adapta)\b",
        re.IGNORECASE,
    ),
)

_PROFILE_TERMS = {
    "machine_tool": (
        "rocadeira", "motosserra", "cortador de grama", "enxada rotativa", "furadeira",
        "parafusadeira", "lixadeira", "esmerilhadeira", "serra circular", "serra eletrica",
        "ferramenta", "maquina", "implemento", "stihl", "husqvarna", "makita", "dewalt",
        "tramontina", "vonder", "black decker",
    ),
    "phone_computing": (
        "iphone", "ipad", "smartphone", "celular", "telefone celular", "tablet", "notebook",
        "laptop", "macbook", "galaxy", "motorola", "xiaomi", "magsafe", "lightning",
        "computador", "pc gamer",
    ),
    "hydraulic": (
        "torneira", "registro", "mangueira", "hidraul", "conexao pvc", "cano pvc", "tubo pvc",
        "sifao", "valvula", "engate rapido", "bomba dagua", "bomba de agua", "irrigacao",
    ),
    "electrical_electronic": (
        "televisao", " tv ", "smart tv", "monitor", "geladeira", "freezer", "microondas",
        "ar condicionado", "lavadora", "secadora", "eletrodomestico", "eletronico", "fonte",
        "carregador", "transformador", "inversor", "voltagem", "tensao", "110v", "127v",
        "220v", "bivolt", "tomada",
    ),
    "vehicle": (
        "veiculo", "carro", "automovel", "moto", "motocicleta", "caminhao", "caminhonete",
        "combustivel", "bomba de combustivel", "injecao", "tanque de combustivel", "motor gasolina",
        "motor diesel", "gasolina", "diesel", "flex", "land rover", "range rover", "evoque",
        "bmw", "honda civic", "honda fit", "toyota", "chevrolet", "volkswagen", "fiat",
        "ford", "hyundai", "jeep", "renault", "peugeot", "citroen", "mercedes", "audi",
        "nissan", "mitsubishi", "kawasaki", "yamaha", "suzuki", "triumph", "ducati", "ktm",
        "r1300gs", "r1250gs", "r1200gs", "1300gs", "1250gs", "1200gs", "850gs",
    ),
    "dimensional": (
        "medida", "tamanho", "dimensao", "diametro", "largura", "comprimento", "altura",
        "furacao", "flange", "rosca", "polegada", "milimetro", "centimetro",
    ),
}

_PROFILE_DEFAULT_DETAILS = {
    "vehicle": ("ano", "versao"),
    "machine_tool": ("modelo completo", "medida do eixo ou quantidade de estrias"),
    "phone_computing": ("modelo completo", "geracao ou tipo de conector"),
    "electrical_electronic": ("tensao", "tipo de conector"),
    "hydraulic": ("medida", "tipo de rosca"),
    "dimensional": ("medida", "padrao de furacao ou fixacao"),
    "generic": ("modelo completo", "tipo ou medida do encaixe"),
}

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


def is_compatibility_question(value: Any) -> bool:
    text = normalize_text(value)
    if not text:
        return False
    if any(term in text for term in ("chassi", " vin ", "numero vin")):
        return True
    return bool(_DIRECT_COMPATIBILITY_RE.search(text) or _TARGETED_COMPATIBILITY_RE.search(text))


def extract_compatibility_target(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    for pattern in _COMPATIBILITY_TARGET_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        target = re.sub(r"[?!.;,]+$", "", match.group(1)).strip()
        target = re.split(
            r"\?+|\b(?:e\s+)?(?:quantos?|quantas?|quanto|quanta|qual|quais|como|onde|quando)\b",
            target,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" ,;.-")
        target = re.sub(r"^(?:o|a|os|as|um|uma)\s+", "", target, flags=re.IGNORECASE)
        if len(target) >= 2:
            return target[:160]
    return ""


def infer_compatibility_profile(
    *,
    question: Any = "",
    title: Any = "",
    description: Any = "",
    category_id: Any = "",
    attributes: Any = None,
) -> dict[str, str]:
    attributes_text = ""
    if isinstance(attributes, list):
        attributes_text = " ".join(str(item or "") for item in attributes[:80])
    question_corpus = f" {normalize_text(question)} "
    corpus = f" {normalize_text(' '.join(str(item or '') for item in (title, question, description, category_id, attributes_text)))} "

    # Strong product families take precedence over incidental dimensional or
    # electrical words that can appear in almost every technical listing.
    # O alvo da pergunta tem precedencia sobre palavras incidentais da peca.
    # Uma bomba de combustivel, por exemplo, pode citar pressao e conexao sem
    # deixar de ser uma aplicacao automotiva. Colocar veiculo antes de
    # hidraulica evita transformar esse caso em uma consulta de rosca.
    order = (
        "vehicle", "machine_tool", "phone_computing", "electrical_electronic", "hydraulic", "dimensional"
    )
    target_type = "generic"
    for candidate in order:
        if any(term in question_corpus for term in _PROFILE_TERMS[candidate]):
            target_type = candidate
            break
    if target_type != "generic":
        return {
            "target_type": target_type,
            "compatibility_profile": PROFILE_BY_TARGET_TYPE[target_type],
        }
    for candidate in order:
        if any(term in corpus for term in _PROFILE_TERMS[candidate]):
            target_type = candidate
            break
    return {
        "target_type": target_type,
        "compatibility_profile": PROFILE_BY_TARGET_TYPE[target_type],
    }


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


def default_missing_details(target_type: Any, question: Any = "") -> tuple[str, str]:
    normalized_type = normalize_target_type(target_type)
    details = _PROFILE_DEFAULT_DETAILS[normalized_type]
    question_norm = normalize_text(question)
    if normalized_type == "vehicle" and any(term in question_norm for term in ("base", "suporte", "navigator")):
        return ("tipo da base original ou paralela", "modelo ou codigo gravado na base")
    return details


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
