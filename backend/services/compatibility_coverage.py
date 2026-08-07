"""Deterministic compatibility coverage extracted from canonical SKU dossiers.

The contract is intentionally small and fail-closed.  It never turns titles,
marketplace copy, web results or legacy memory into universal coverage.  The
caller must still bind each rule to the exact tenant/SKU generation returned by
Context Hub before using it in a customer-facing decision.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Mapping, Sequence


COMPATIBILITY_COVERAGE_VERSION = "compatibility-coverage-v1"

_ALLOWED_MODES = {"universal", "enumerated", "conditional"}
_ALLOWED_TARGET_TYPES = {
    "vehicle",
    "machine_tool",
    "phone_computing",
    "electrical_electronic",
    "hydraulic",
    "dimensional",
    "generic",
}
_ALLOWED_TARGET_KINDS_BY_TYPE = {
    "vehicle": {"vehicle", "motorcycle"},
    "machine_tool": {"machine_tool"},
    "phone_computing": {"phone", "computer"},
    "electrical_electronic": {"electronic_device"},
    "hydraulic": {"hydraulic_component"},
    "dimensional": {"dimensional_component"},
    "generic": {"generic"},
}
_ALLOWED_SCOPES = {
    "physical_fit",
    "vehicle_application",
    "dimensional",
    "electrical",
    "protocol",
    "function",
}
_DOCUMENTED_STATUSES = {"documentado", "revisado", "confirmado", "aprovado"}

_TARGET_PATTERNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("phone_computing", "phone", ("celular", "celulares", "smartphone", "smartphones", "telefone movel", "iphone")),
    ("phone_computing", "computer", ("computador", "computadores", "notebook", "notebooks", "laptop", "laptops", " pc ")),
    ("vehicle", "motorcycle", ("motocicleta", "motocicletas", "moto ", "motos ")),
    ("vehicle", "vehicle", ("veiculo", "veiculos", "automovel", "automoveis", "carro", "carros", "caminhao", "caminhoes")),
    ("machine_tool", "machine_tool", ("rocadeira", "rocadeiras", "maquina", "maquinas", "ferramenta", "ferramentas", "motor de popa", "motores de popa")),
    ("electrical_electronic", "electronic_device", (
        "equipamento eletrico", "equipamentos eletricos", "aparelho eletrico", "aparelhos eletricos",
        "equipamento eletronico", "equipamentos eletronicos", "televisor", "televisores", " tv ",
        "sensor", "sensores", "controle remoto", "sistema eletronico", "sistemas eletronicos",
    )),
    ("hydraulic", "hydraulic_component", ("sistema hidraulico", "sistemas hidraulicos", "mangueira hidraulica", "mangueiras hidraulicas")),
    ("dimensional", "dimensional_component", ("tubo", "tubos", "eixo", "eixos", "rosca", "roscas", "componente", "componentes")),
)

_SCOPE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("protocol", ("obd2", " obd ", "canbus", "barramento can", "bluetooth", "wifi", " wi fi", " qi ", "tpms", "protocolo")),
    ("electrical", ("tensao", "voltagem", "potencia", "corrente", "amperagem", "carregamento", "carregador", "carregar", "carrega", "recarrega", " usb ", "conexao eletrica")),
    ("physical_fit", ("encaixe", "encaixar", "fixacao", "fixar", "diametro", "rosca", "estria", "estrias", "furacao", "eixo", "tubo", "tamanho fisico")),
    ("dimensional", ("medida", "medidas", "dimensao", "dimensoes", "milimetro", "milimetros", "polegada", "polegadas")),
    ("function", ("funciona", "funcionar", "funcao", "funcoes")),
)

_UNIVERSAL_PATTERNS = (
    r"\bqualquer\b",
    r"\btodos?\b",
    r"\btodas?\b",
    r"\bindependentemente\b",
    r"\bsem limite de\b",
    r"\buniversal(?:mente)?\b",
)
_CONDITIONAL_PATTERNS = (
    r"\bdesde que\b",
    r"\bmediante\b",
    r"\bdepende(?:nte)?\b",
    r"\bcondicionad[oa]\b",
    r"\bequipad[oa]s? com\b",
    r"\bcom (?:conector|porta|interface|sistema|tubo|eixo|rosca|protocolo)\b",
)


def normalize_coverage_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _sku_doc_slug(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9._-]+", "-", text)
    return re.sub(r"[-_.]{2,}", "-", text).strip("-._")


def _field(mapping: Any, normalized_name: str, default: Any = None) -> Any:
    if not isinstance(mapping, Mapping):
        return default
    for key, value in mapping.items():
        if normalize_coverage_text(key).replace(" ", "_") == normalized_name:
            return value
    return default


def _items(section: Any) -> list[Any]:
    value = _field(section, "itens", [])
    return list(value) if isinstance(value, list) else []


def _documented(section: Any) -> bool:
    return normalize_coverage_text(_field(section, "status", "")) in _DOCUMENTED_STATUSES


def _infer_target(text: Any) -> tuple[str, str]:
    padded = f" {normalize_coverage_text(text)} "
    for target_type, target_kind, patterns in _TARGET_PATTERNS:
        if any(pattern in padded for pattern in patterns):
            return target_type, target_kind
    return "", ""


def _infer_scope(text: Any, target_type: str = "") -> str:
    padded = f" {normalize_coverage_text(text)} "
    for scope, patterns in _SCOPE_PATTERNS:
        if any(pattern in padded for pattern in patterns):
            return scope
    if any(marker in padded for marker in (" suporte ", " berco ", " base de fixacao ")):
        return "physical_fit"
    if target_type == "vehicle":
        return "vehicle_application"
    return ""


def _is_universal(text: Any) -> bool:
    normalized = normalize_coverage_text(text)
    return any(re.search(pattern, normalized) for pattern in _UNIVERSAL_PATTERNS)


def _is_conditional(text: Any) -> bool:
    normalized = normalize_coverage_text(text)
    return any(re.search(pattern, normalized) for pattern in _CONDITIONAL_PATTERNS)


def _stable_rule_id(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8", errors="ignore")).hexdigest()[:20]


def _related_condition(text: str, primary_scope: str) -> dict[str, str] | None:
    normalized = str(text or "").strip()
    if not normalized:
        return None
    scope = _infer_scope(normalized)
    if not scope or scope == primary_scope:
        return None
    return {"scope": scope, "text": normalized[:800]}


def _text_exclusions(text: Any) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    match = re.search(
        r"(?i)\b(?:exceto|menos|nao inclui|nao serve(?: para)?|incompativel com)\b(.+)$",
        normalized,
    )
    return [match.group(1).strip(" .,:;-")[:800]] if match and match.group(1).strip(" .,:;-") else []


def _text_rule(
    text: str,
    *,
    source_field: str,
    status: str,
    observation: str = "",
) -> dict[str, Any] | None:
    target_type, target_kind = _infer_target(text)
    scope = _infer_scope(text, target_type)
    if not target_type or not target_kind or not scope:
        return None
    universal = _is_universal(text)
    conditional = _is_conditional(text)
    mode = "universal" if universal else ("conditional" if conditional else "enumerated")
    conditions: list[str] = []
    related_conditions: list[dict[str, str]] = []
    observation = str(observation or "").strip()
    if observation:
        observation_scope = _infer_scope(observation, target_type)
        if observation_scope == scope and _is_conditional(observation):
            conditions.append(observation[:800])
        else:
            related = _related_condition(observation, scope)
            if related:
                related_conditions.append(related)
    if mode == "conditional":
        conditions.insert(0, str(text).strip()[:800])
    base = {
        "coverage_mode": mode,
        "target_type": target_type,
        "target_kind": target_kind,
        "scope": scope,
        "target_expression": str(text).strip()[:1200],
        "years": [],
        "conditions": list(dict.fromkeys(item for item in conditions if item)),
        "restrictions": [],
        "exclusions": _text_exclusions(text),
        "related_conditions": related_conditions,
        "source_field": source_field,
        "source_status": normalize_coverage_text(status),
    }
    return {"rule_id": _stable_rule_id(base), **base}


def build_compatibility_coverage(data: Mapping[str, Any], sku: str) -> dict[str, Any]:
    """Build CompatibilityCoverageV1 from one reviewed canonical SKU dossier."""

    review = _field(data, "revisao", {})
    pending = _field(review, "pendencias", [])
    if normalize_coverage_text(_field(review, "status", "")) != "revisado" or (
        isinstance(pending, list) and any(str(item or "").strip() for item in pending)
    ):
        return {"schema_version": COMPATIBILITY_COVERAGE_VERSION, "sku": str(sku or ""), "rules": []}

    application = _field(data, "aplicacao", {})
    rules: list[dict[str, Any]] = []

    vehicles = _field(application, "veiculos_compativeis", {})
    if _documented(vehicles):
        observation = str(_field(vehicles, "observacao", "") or "").strip()
        for item in _items(vehicles):
            if not isinstance(item, Mapping):
                continue
            brand = str(_field(item, "marca", "") or "").strip()
            model = str(_field(item, "modelo", "") or "").strip()
            years = _field(item, "anos", [])
            year_values = [str(value or "").strip() for value in years] if isinstance(years, list) else []
            restrictions = _field(item, "restricoes", [])
            restriction_values = [str(value or "").strip()[:800] for value in restrictions] if isinstance(restrictions, list) else []
            exclusion_values = [
                value for value in restriction_values
                if re.search(r"(?i)\b(?:exceto|menos|nao|incompativel|exclui)\b", value)
            ]
            condition_values = [value for value in restriction_values if value not in exclusion_values]
            target = " ".join(part for part in (brand, model) if part).strip()
            if not target:
                continue
            base = {
                "coverage_mode": "conditional" if restriction_values or observation else "enumerated",
                "target_type": "vehicle",
                "target_kind": "vehicle",
                "scope": "vehicle_application",
                "target_expression": target[:1200],
                "years": [item for item in year_values if item][:40],
                "conditions": ([observation[:800]] if observation else []) + condition_values[:20],
                "restrictions": condition_values[:20],
                "exclusions": exclusion_values[:20],
                "related_conditions": [],
                "source_field": "aplicacao.veiculos_compativeis",
                "source_status": normalize_coverage_text(_field(vehicles, "status", "")),
            }
            rules.append({"rule_id": _stable_rule_id(base), **base})

    equipment = _field(application, "equipamentos_ou_aplicacoes_compativeis", {})
    if _documented(equipment):
        observation = str(_field(equipment, "observacao", "") or "").strip()
        status = str(_field(equipment, "status", "") or "")
        for item in _items(equipment):
            if not isinstance(item, str):
                continue
            rule = _text_rule(
                item,
                source_field="aplicacao.equipamentos_ou_aplicacoes_compativeis",
                status=status,
                observation=observation,
            )
            if rule:
                rules.append(rule)

    characteristics = _field(data, "caracteristicas_tecnicas", {})
    if _documented(characteristics):
        status = str(_field(characteristics, "status", "") or "")
        observation = str(_field(characteristics, "observacao", "") or "").strip()
        for item in _items(characteristics):
            if not isinstance(item, str) or not _is_universal(item):
                continue
            rule = _text_rule(
                item,
                source_field="caracteristicas_tecnicas",
                status=status,
                observation=observation,
            )
            if rule:
                rules.append(rule)

    unique: dict[str, dict[str, Any]] = {}
    for rule in rules:
        unique.setdefault(str(rule.get("rule_id") or ""), rule)
    return {
        "schema_version": COMPATIBILITY_COVERAGE_VERSION,
        "sku": str(sku or "").strip()[:120],
        "rules": list(unique.values())[:80],
    }


def normalize_compatibility_coverage(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    if source.get("schema_version") != COMPATIBILITY_COVERAGE_VERSION:
        return {}
    sku = str(source.get("sku") or "").strip()[:120]
    normalized_rules: list[dict[str, Any]] = []
    for raw in source.get("rules") if isinstance(source.get("rules"), list) else []:
        if not isinstance(raw, Mapping):
            continue
        mode = str(raw.get("coverage_mode") or "").strip()
        target_type = str(raw.get("target_type") or "").strip()
        target_kind = str(raw.get("target_kind") or "").strip()[:80]
        scope = str(raw.get("scope") or "").strip()
        expression = str(raw.get("target_expression") or "").strip()[:1200]
        if mode not in _ALLOWED_MODES or target_type not in _ALLOWED_TARGET_TYPES or scope not in _ALLOWED_SCOPES:
            continue
        if target_kind not in _ALLOWED_TARGET_KINDS_BY_TYPE.get(target_type, set()) or not expression:
            continue
        rule = {
            "rule_id": str(raw.get("rule_id") or "").strip()[:40],
            "coverage_mode": mode,
            "target_type": target_type,
            "target_kind": target_kind,
            "scope": scope,
            "target_expression": expression,
            "years": [str(item or "").strip()[:80] for item in (raw.get("years") or []) if str(item or "").strip()][:40],
            "conditions": [str(item or "").strip()[:800] for item in (raw.get("conditions") or []) if str(item or "").strip()][:20],
            "restrictions": [str(item or "").strip()[:800] for item in (raw.get("restrictions") or []) if str(item or "").strip()][:20],
            "exclusions": [str(item or "").strip()[:800] for item in (raw.get("exclusions") or []) if str(item or "").strip()][:20],
            "related_conditions": [
                {"scope": str(item.get("scope") or "")[:80], "text": str(item.get("text") or "")[:800]}
                for item in (raw.get("related_conditions") or [])
                if isinstance(item, Mapping) and str(item.get("scope") or "") in _ALLOWED_SCOPES and str(item.get("text") or "").strip()
            ][:20],
            "source_field": str(raw.get("source_field") or "").strip()[:200],
            "source_status": str(raw.get("source_status") or "").strip()[:80],
        }
        expected_id = _stable_rule_id({key: value for key, value in rule.items() if key != "rule_id"})
        if rule["rule_id"] and rule["rule_id"] != expected_id:
            continue
        rule["rule_id"] = expected_id
        normalized_rules.append(rule)
    return {"schema_version": COMPATIBILITY_COVERAGE_VERSION, "sku": sku, "rules": normalized_rules}


def bind_compatibility_coverage(
    value: Any,
    *,
    source_hash: str,
    generation_id: str,
    truth_class: str,
    doc_id: str,
) -> dict[str, Any]:
    coverage = normalize_compatibility_coverage(value)
    source_hash = str(source_hash or "").strip().lower()
    generation_id = str(generation_id or "").strip()
    truth_class = str(truth_class or "").strip().lower()
    doc_id = str(doc_id or "").strip()
    if not coverage or truth_class != "canonical" or not re.fullmatch(r"[a-f0-9]{64}", source_hash):
        return {}
    if not generation_id or not doc_id.startswith("jk:sku:"):
        return {}
    expected_doc = "jk:sku:" + _sku_doc_slug(coverage.get("sku"))
    if expected_doc != doc_id:
        return {}
    coverage["source_binding"] = {
        "doc_id": doc_id,
        "source_hash": source_hash,
        "generation_id": generation_id,
        "truth_class": truth_class,
    }
    return coverage


def _meaningful_tokens(value: Any) -> set[str]:
    stop = {
        "serve", "servir", "compativel", "produto", "item", "esse", "essa", "este", "esta",
        "com", "para", "uma", "uns", "das", "dos", "que", "meu", "minha", "modelo",
        "veiculo", "celular", "telefone", "maquina", "ferramenta", "equipamento",
        "nao", "exceto", "menos", "inclui", "serve", "para", "versao",
    }
    return {token for token in normalize_coverage_text(value).split() if len(token) >= 2 and token not in stop}


def _identity_tokens(value: Any) -> set[str]:
    stop = {
        "serve", "servir", "compativel", "produto", "item", "esse", "essa", "este", "esta",
        "com", "para", "uma", "uns", "das", "dos", "que", "meu", "minha", "no", "na",
        "veiculo", "celular", "telefone", "maquina", "ferramenta", "equipamento",
    }
    return {token for token in normalize_coverage_text(value).split() if token and token not in stop}


def _question_scopes(question_text: str, product_text: str) -> set[str]:
    question = normalize_coverage_text(question_text)
    padded = f" {question} "
    scopes = {
        scope
        for scope, patterns in _SCOPE_PATTERNS
        if any(pattern in padded for pattern in patterns)
    }
    if scopes:
        return scopes
    product_scope = _infer_scope(product_text)
    return {product_scope} if product_scope else set()


def _rule_allows_year(rule: Mapping[str, Any], token: str) -> bool:
    if not re.fullmatch(r"(?:19|20)\d{2}", token):
        return False
    year = int(token)
    for value in rule.get("years") if isinstance(rule.get("years"), list) else []:
        numbers = [int(item) for item in re.findall(r"(?:19|20)\d{2}", str(value or ""))]
        if len(numbers) >= 2 and min(numbers[0], numbers[1]) <= year <= max(numbers[0], numbers[1]):
            return True
        if len(numbers) == 1 and year == numbers[0]:
            return True
    return False


def select_compatibility_coverage(
    coverages: Sequence[Mapping[str, Any]],
    *,
    target_type: str,
    target_item: str,
    question_text: str,
    product_text: str = "",
) -> dict[str, Any]:
    """Select a canonical rule that resolves the declared target and scope."""

    requested_type = str(target_type or "").strip()
    inferred_type, inferred_kind = _infer_target(f"{question_text} {target_item}")
    if requested_type not in _ALLOWED_TARGET_TYPES or (inferred_type and inferred_type != requested_type):
        return {}
    scopes = _question_scopes(question_text, product_text)
    target_tokens = _identity_tokens(target_item)
    candidates: list[tuple[int, dict[str, Any], Mapping[str, Any]]] = []
    for coverage in coverages:
        normalized = normalize_compatibility_coverage(coverage)
        binding = coverage.get("source_binding") if isinstance(coverage.get("source_binding"), Mapping) else {}
        if not normalized or not binding:
            continue
        for rule in normalized.get("rules") or []:
            if rule.get("target_type") != requested_type:
                continue
            if inferred_kind and rule.get("target_kind") != inferred_kind:
                continue
            if scopes and rule.get("scope") not in scopes:
                continue
            mode = str(rule.get("coverage_mode") or "")
            priority = 0
            if mode == "universal":
                if not inferred_kind:
                    continue
                priority = 20
            else:
                expression_tokens = _identity_tokens(rule.get("target_expression"))
                overlap = target_tokens & expression_tokens
                numeric_target = {token for token in target_tokens if any(char.isdigit() for char in token)}
                unmatched_numeric = numeric_target - expression_tokens
                if not overlap or any(not _rule_allows_year(rule, token) for token in unmatched_numeric):
                    continue
                priority = 30 if mode == "enumerated" else 25
            target_context_tokens = _meaningful_tokens(f"{target_item} {question_text}")
            if any(
                exclusion_tokens
                and exclusion_tokens.issubset(target_context_tokens)
                for exclusion_tokens in (
                    _meaningful_tokens(item) for item in (rule.get("exclusions") or [])
                )
            ):
                continue
            candidates.append((priority, dict(rule), binding))
    if not candidates:
        return {}
    _, rule, binding = sorted(candidates, key=lambda item: (-item[0], str(item[1].get("rule_id") or "")))[0]
    conditions = list(dict.fromkeys(
        [*list(rule.get("conditions") or []), *list(rule.get("restrictions") or [])]
    ))
    decision = "conditional" if conditions else "yes"
    return {
        "contract_version": COMPATIBILITY_COVERAGE_VERSION,
        "decision": decision,
        "rule": rule,
        "source_binding": dict(binding),
        "target_item": str(target_item or "").strip()[:240],
        "target_type": requested_type,
        "target_kind": inferred_kind or str(rule.get("target_kind") or ""),
        "scope": str(rule.get("scope") or ""),
        "conditions": conditions[:20],
        "related_conditions": list(rule.get("related_conditions") or [])[:20],
        "research_skipped": "canonical_coverage_sufficient",
    }
