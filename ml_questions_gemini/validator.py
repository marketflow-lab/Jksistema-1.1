from __future__ import annotations

import re
from typing import Any

from .classifier import normalize
from .compatibility import normalize_comparison_attributes, normalize_target_type, profile_language_issues
from .schemas import ListingSnapshot, QuestionCategory, QuestionContext, SellerRules, ValidationResult


class AnswerValidator:
    def validate(
        self,
        answer: str,
        *,
        question: QuestionContext,
        listing: ListingSnapshot,
        category: QuestionCategory,
        rules: SellerRules,
        confidence: float,
        compatibility_analysis: dict[str, Any] | None = None,
    ) -> ValidationResult:
        issues: list[str] = []
        text = str(answer or "").strip()
        norm = normalize(text)
        if not text:
            issues.append("empty_answer")
        if len(text) > rules.max_chars:
            issues.append("too_long")
        if int(rules.max_sentences or 0) > 0 and count_sentences(text) > int(rules.max_sentences):
            issues.append("too_many_sentences")
        if confidence < rules.min_confidence:
            issues.append("low_confidence")
        if category == QuestionCategory.POST_SALE:
            issues.append("post_sale_requires_review")
        if category == QuestionCategory.REGULATED_PRODUCT:
            issues.append("regulated_product")
        if _has_external_contact(text):
            issues.append("external_contact")
        if _has_internal_ai_terms(norm):
            issues.append("internal_ai_leak")
        if _has_forbidden_identity(norm):
            issues.append("forbidden_identity")
        if category != QuestionCategory.POST_SALE:
            if _asks_for_photo(norm):
                issues.append("public_question_asks_for_photo")
            elif _asks_for_attachment(norm):
                issues.append("public_question_asks_for_attachment")
        if category == QuestionCategory.COMPATIBILITY:
            if _uses_forbidden_compatibility_phrase(norm):
                issues.append("forbidden_compatibility_phrase")
            if _asks_for_chassis(norm):
                issues.append("asks_for_chassis")
            if _recommends_generic_mechanic(norm):
                issues.append("generic_mechanic_referral")
            _validate_compatibility(
                issues,
                norm=norm,
                question=question,
                listing=listing,
                analysis=compatibility_analysis,
            )
        if category == QuestionCategory.WARRANTY_ORIGINALITY and _asserts_originality_or_warranty(norm) and not _has_originality_or_warranty_evidence(listing):
            issues.append("originality_or_warranty_without_evidence")
        if category == QuestionCategory.INVOICE and "nota fiscal" in norm and not _listing_has_any(listing, ("nota fiscal", "nf-e", "nfe")):
            issues.append("invoice_without_evidence")
        return ValidationResult(ok=not issues, issues=issues, confidence=confidence)


def count_sentences(text: str) -> int:
    cleaned = re.sub(r"\b(sr|sra|dr|dra)\.", r"\1", text.lower())
    parts = [part.strip() for part in re.split(r"[.!?]+", cleaned) if part.strip()]
    return len(parts) if parts else (1 if text.strip() else 0)


def _has_external_contact(text: str) -> bool:
    norm = normalize(text)
    if any(term in norm for term in ("whatsapp", "whats", " zap ", "telefone", "instagram", "email", "pix", "fora do mercado livre")):
        return True
    if re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", text, flags=re.I):
        return True
    if re.search(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?\d{4,5}[-.\s]?\d{4}", text):
        return True
    if re.search(r"https?://|www\.", text, flags=re.I):
        return True
    return False


def _has_internal_ai_terms(norm: str) -> bool:
    return any(term in norm for term in (
        "vertex", "gemini", "inteligencia artificial", " ia ", "prompt", "json", "api",
        "modelo de linguagem", "modelo da ia", "modelo interno", "treinamento salvo", "ferramenta externa",
    ))


def _has_forbidden_identity(norm: str) -> bool:
    return any(term in norm for term in ("assistente da jk sistema", "assistente do jk sistema", "sou o assistente", "sou a assistente", "sou uma ia", "sou um assistente", "jk sistema"))


def _uses_forbidden_compatibility_phrase(norm: str) -> bool:
    return "nao conseguimos confirmar a compatibilidade" in norm


def _asks_for_photo(norm: str) -> bool:
    photo_terms = r"(?:foto(?:s|grafia(?:s)?)?|imagem|imagens)"
    if not re.search(rf"\b{photo_terms}\b", norm):
        return False

    # Referencias ao material visual que ja integra o anuncio sao informativas e
    # nao pedem um novo arquivo ao comprador.
    without_listing_references = re.sub(
        rf"\b{photo_terms}\b.{{0,24}}\b(?:do|no|deste|neste|nas|nos)\s+anuncio\b",
        " referencia visual do anuncio ",
        norm,
    )
    without_listing_references = re.sub(
        rf"\b(?:anuncio|produto anunciado)\b.{{0,24}}\b{photo_terms}\b",
        " referencia visual do anuncio ",
        without_listing_references,
    )
    if not re.search(rf"\b{photo_terms}\b", without_listing_references):
        return False

    request_verbs = (
        "envie", "enviar", "manda", "mande", "mandar", "anexa", "anexe", "anexar",
        "encaminhe", "encaminhar", "carregue", "carregar", "adicione", "adicionar",
        "publique", "publicar", "compartilhe", "compartilhar", "forneca", "fornecer",
        "mostre", "mostrar", "passe", "passar",
    )
    request_pattern = r"(?:{})".format("|".join(re.escape(term) for term in request_verbs))
    without_listing_references = re.sub(
        rf"\bnao\s+(?:(?:e|seria)\s+necessari[oa]\s+|precisa(?:mos)?\s+)?(?:{request_pattern})\b.{{0,80}}\b{photo_terms}\b",
        " foto nao solicitada ",
        without_listing_references,
    )
    if not re.search(rf"\b{photo_terms}\b", without_listing_references):
        return False
    if re.search(rf"\b{request_pattern}\b.{{0,80}}\b{photo_terms}\b", without_listing_references):
        return True
    if re.search(rf"\b{photo_terms}\b.{{0,80}}\b{request_pattern}\b", without_listing_references):
        return True

    need_pattern = r"(?:preciso|precisamos|precisaria|necessito|necessitamos|e necessario|e necessaria|seria preciso|seria precisa|seria necessario|seria necessaria|gostaria|tem como|se puder|se possivel|caso possa)"
    if re.search(rf"\b{need_pattern}\b.{{0,80}}\b{photo_terms}\b", without_listing_references):
        return True
    if re.search(rf"\b{photo_terms}\b.{{0,50}}\b(?:ajudaria|necessaria|necessario|indispensavel)\b", without_listing_references):
        return True
    if re.search(rf"\b(?:por favor)\b.{{0,50}}\b{photo_terms}\b", without_listing_references):
        return True
    if re.search(rf"\b{photo_terms}\b.{{0,30}}\b(?:por favor)\b", without_listing_references):
        return True
    return bool(re.search(rf"\bsem\s+(?:uma\s+|as\s+)?{photo_terms}\b", without_listing_references))


def _asks_for_attachment(norm: str) -> bool:
    attachment_terms = r"(?:arquivo|anexo|documento|captura|print|retrato|registro visual)"
    visual_context = r"(?:mostrar|mostrando|visual|base|suporte|encaixe|instalad[oa]|moto|veiculo|produto|peca)"
    if not re.search(rf"\b{attachment_terms}\b", norm) or not re.search(rf"\b{visual_context}\b", norm):
        return False
    request_terms = r"(?:envie|enviar|mande|mandar|anexe|anexar|encaminhe|encaminhar|carregue|carregar|compartilhe|compartilhar|forneca|fornecer)"
    without_negated_request = re.sub(
        rf"\bnao\s+(?:(?:e|seria)\s+necessari[oa]\s+|precisa(?:mos)?\s+)?\b{request_terms}\b.{{0,80}}\b{attachment_terms}\b",
        " anexo nao solicitado ",
        norm,
    )
    need_terms = r"(?:preciso|precisamos|precisaria|necessito|necessitamos|e necessario|e necessaria|seria necessario|seria necessaria|gostaria|se puder|se possivel)"
    return bool(
        re.search(rf"\b{request_terms}\b.{{0,100}}\b{attachment_terms}\b.{{0,100}}\b{visual_context}\b", without_negated_request)
        or re.search(rf"\b{request_terms}\b.{{0,100}}\b{visual_context}\b.{{0,100}}\b{attachment_terms}\b", without_negated_request)
        or re.search(rf"\b{attachment_terms}\b.{{0,100}}\b{visual_context}\b.{{0,100}}\b{request_terms}\b", without_negated_request)
        or re.search(rf"\b{need_terms}\b.{{0,100}}\b{attachment_terms}\b.{{0,100}}\b{visual_context}\b", without_negated_request)
        or re.search(rf"\b{attachment_terms}\b.{{0,100}}\b{visual_context}\b.{{0,100}}\b(?:necessari[oa]|ajudaria|por favor)\b", without_negated_request)
    )


def _asks_for_chassis(norm: str) -> bool:
    if "chassi" not in norm and "vin" not in norm:
        return False
    request_terms = (
        "informe", "envie", "mande", "passe", "forneca", "digite", "encaminhe",
        "pode informar", "poderia informar", "favor informar", "preciso",
        "precisamos", "necessario", "necessaria",
    )
    chassis_terms = r"(?:chassi|vin)"
    request_pattern = r"(?:{})".format("|".join(re.escape(term) for term in request_terms))
    return bool(
        re.search(request_pattern + r".{0,90}\b" + chassis_terms + r"\b", norm)
        or re.search(r"\b" + chassis_terms + r"\b.{0,90}" + request_pattern, norm)
    )


def _recommends_generic_mechanic(norm: str) -> bool:
    professional_terms = r"(?:mecanico|mecanica|oficina|profissional especializado)"
    if not re.search(rf"\b{professional_terms}\b", norm):
        return False
    recommendation_terms = r"(?:recomendo|recomendamos|consulte|consultar|procure|procurar|confirme|confirmar|verifique|verificar|confira|conferir|leve|levar|fale|falar)"
    norm = re.sub(
        rf"\bnao\s+(?:(?:e|seria)\s+necessari[oa]\s+)?\b{recommendation_terms}\b.{{0,90}}\b{professional_terms}\b",
        " profissional nao recomendado ",
        norm,
    )
    return bool(
        re.search(rf"\b{recommendation_terms}\b.{{0,90}}\b{professional_terms}\b", norm)
        or re.search(rf"\b{professional_terms}\b.{{0,90}}\b{recommendation_terms}\b", norm)
    )


def _validate_compatibility(
    issues: list[str],
    *,
    norm: str,
    question: QuestionContext,
    listing: ListingSnapshot,
    analysis: dict[str, Any] | None,
) -> None:
    answer_decision = _answer_compatibility_decision(norm)
    if not answer_decision:
        issues.append("compatibility_without_decision")

    if analysis is None:
        if answer_decision in {"yes", "no", "conditional"} and not _has_compatibility_evidence(question, listing):
            issues.append("compatibility_without_evidence")
        if answer_decision == "insufficient" and not _asks_for_approved_compatibility_detail(norm):
            issues.append("compatibility_missing_detail_request")
        return

    if not isinstance(analysis, dict):
        issues.append("invalid_compatibility_analysis")
        return

    analysis_decision = _normalize_compatibility_decision(analysis.get("decision"))
    if not analysis_decision:
        issues.append("invalid_compatibility_decision")
        return
    if answer_decision and answer_decision != analysis_decision:
        issues.append("compatibility_decision_mismatch")
    target_type_default = "vehicle" if analysis.get("target_vehicle") and not analysis.get("target_item") else "generic"
    target_type = normalize_target_type(analysis.get("target_type"), target_type_default)
    if profile_language_issues(norm, target_type):
        issues.append("compatibility_profile_language_mismatch")

    if analysis_decision in {"yes", "no", "conditional"}:
        target_item = analysis.get("target_item") or analysis.get("target_vehicle")
        if not str(analysis.get("product_interface") or "").strip() or not str(target_item or "").strip() or not str(analysis.get("target_interface") or "").strip():
            issues.append("incomplete_compatibility_analysis")
        if not _has_structured_compatibility_evidence(analysis, analysis_decision):
            issues.append("compatibility_without_evidence")
        comparisons = normalize_comparison_attributes(analysis.get("comparison_attributes"))
        if "comparison_attributes" in analysis and not comparisons:
            issues.append("compatibility_comparison_missing")
        elif comparisons:
            decisive_results = {item.get("result") for item in comparisons if item.get("decisive")}
            coherent = (
                analysis_decision in {"yes", "conditional"}
                and "match" in decisive_results
                and "conflict" not in decisive_results
            ) or (analysis_decision == "no" and "conflict" in decisive_results)
            if not coherent:
                issues.append("compatibility_comparison_mismatch")
        if analysis_decision == "conditional":
            condition = str(analysis.get("condition") or "").strip()
            if not condition:
                issues.append("compatibility_condition_missing")
            elif not _compatibility_condition_matches_answer(condition, norm):
                issues.append("compatibility_condition_mismatch")
            elif not _compatibility_condition_supported_by_analysis(condition, analysis):
                issues.append("compatibility_condition_unsupported")
        return

    missing_fields = analysis.get("missing_fields")
    if not _has_meaningful_value(missing_fields):
        issues.append("compatibility_missing_fields_absent")
    if not _asks_for_approved_compatibility_detail(norm):
        issues.append("compatibility_missing_detail_request")


def _answer_compatibility_decision(norm: str) -> str:
    negative_patterns = (
        "nao serve", "nao e compativel", "nao encaixa", "nao funciona",
        "nao se aplica", "incompativel",
    )
    if any(pattern in norm for pattern in negative_patterns):
        return "no"

    insufficient_patterns = (
        "nao ha confirmacao", "nao temos confirmacao", "falta informacao", "faltam dados",
        "evidencia insuficiente", "para confirmar, informe", "para confirmar informe",
        "para confirmar a aplicacao", "para confirmar a compatibilidade", "preciso comparar", "precisamos comparar",
        "precisamos do ano", "precisamos da versao", "informe o ano", "informe a versao",
        "informe o modelo da base", "informe o codigo", "confirme se a base",
        "necessario informar o ano", "necessaria informar o ano",
        "necessario informar a versao", "necessaria informar a versao",
    )
    if any(pattern in norm for pattern in insufficient_patterns):
        return "insufficient"

    dependency_patterns = (
        "compatibilidade depende", "aplicacao depende", "encaixe depende",
        "funcionamento depende", "vai depender", "depende de a ", "depende de o ",
        "depende da ", "depende do ", "compatibilidade esta condicionada",
        "aplicacao esta condicionada", "condicionada a ", "condicionado a ",
        "encaixe requer", "encaixe exige", "funcionamento requer", "funcionamento exige",
        "so funciona", "somente funciona",
    )
    if any(pattern in norm for pattern in dependency_patterns):
        return "conditional"

    positive_patterns = (
        "serve", "e compativel", "compativel com", "encaixa", "funciona no",
        "funciona na", "funciona com", "aplica no", "aplica na",
    )
    if not any(pattern in norm for pattern in positive_patterns):
        return ""
    conditional_text = re.sub(
        r"\bnao\s+(?:(?:e|seria)\s+)?(?:necessari[oa]|obrigatori[oa])\b",
        " ",
        norm,
    )
    conditional_patterns = (
        "desde que", "caso ", "se a ", "se o ", "somente", "apenas",
        "equipada", "equipado", "com a base", "com o suporte", "necessaria",
        "necessario", "requer ", "exige ", "depende ", "quando ",
    )
    if any(pattern in conditional_text for pattern in conditional_patterns):
        return "conditional"
    return "yes"


def _normalize_compatibility_decision(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", normalize(value)).strip("_")
    aliases = {
        "yes": "yes",
        "sim": "yes",
        "compatible": "yes",
        "compativel": "yes",
        "no": "no",
        "nao": "no",
        "incompatible": "no",
        "incompativel": "no",
        "conditional": "conditional",
        "condicional": "conditional",
        "depends": "conditional",
        "depende": "conditional",
        "insufficient": "insufficient",
        "insufficient_evidence": "insufficient",
        "evidence_insufficient": "insufficient",
        "insuficiente": "insufficient",
        "evidencia_insuficiente": "insufficient",
        "missing_information": "insufficient",
        "informacao_insuficiente": "insufficient",
    }
    return aliases.get(normalized, "")


def _has_structured_compatibility_evidence(analysis: dict[str, Any], decision: str) -> bool:
    product, target, comparison = _compatibility_evidence_groups(analysis)
    if not _has_usable_evidence(product):
        return False
    # A interface do alvo nao pode ser sustentada somente por outro anuncio.
    if not _has_non_marketplace_evidence(target):
        return False
    # Alem dos dois lados, exija a prova explicita de equivalencia ou diferenca.
    return _comparison_evidence_supports_decision(comparison, decision)


def _compatibility_evidence_groups(analysis: dict[str, Any]) -> tuple[Any, Any, Any]:
    grouped = analysis.get("evidence")
    if grouped in (None, ""):
        grouped = analysis.get("evidences") or analysis.get("evidencias")
    if isinstance(grouped, dict):
        product = grouped.get("product") or grouped.get("produto") or analysis.get("product_evidence")
        target = (
            grouped.get("target_vehicle") or grouped.get("vehicle") or grouped.get("target")
            or analysis.get("target_evidence") or analysis.get("vehicle_evidence")
        )
        comparison = (
            grouped.get("equivalence") or grouped.get("incompatibility") or grouped.get("comparison")
            or analysis.get("equivalence_evidence") or analysis.get("comparison_evidence")
        )
        return product, target, comparison

    product_items: list[Any] = []
    target_items: list[Any] = []
    comparison_items: list[Any] = []
    for item in grouped if isinstance(grouped, (list, tuple, set)) else []:
        if not isinstance(item, dict):
            continue
        role = _evidence_descriptor(item, keys=("scope", "group", "role", "evidence_type"))
        if any(marker in role for marker in ("product", "produto")):
            product_items.append(item)
        elif any(marker in role for marker in ("target", "vehicle", "veiculo")):
            target_items.append(item)
        elif any(marker in role for marker in ("equivalence", "comparison", "match", "incompatibility", "equivalencia", "comparacao")):
            comparison_items.append(item)
    return product_items, target_items, comparison_items


def _evidence_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if _has_usable_evidence(value):
            return [value]
        items: list[dict[str, Any]] = []
        for key in ("evidence", "results", "items", "sources"):
            items.extend(_evidence_items(value.get(key)))
        return items
    if isinstance(value, (list, tuple, set)):
        items: list[dict[str, Any]] = []
        for item in value:
            items.extend(_evidence_items(item))
        return items
    return []


def _evidence_descriptor(item: dict[str, Any], *, keys: tuple[str, ...] | None = None) -> str:
    selected = keys or ("authority", "source_type", "provider", "source", "url")
    text = " ".join(str(item.get(key) or "") for key in selected)
    return re.sub(r"[^a-z0-9]+", " ", normalize(text)).strip()


def _is_marketplace_evidence(item: dict[str, Any]) -> bool:
    descriptor = _evidence_descriptor(item)
    marketplace_markers = (
        "marketplace", "marketplace hint", "mercado livre", "mercadolivre", "similar listing",
        "listing similar", "internal listing", "seller listing", "amazon", "shopee", "aliexpress",
    )
    return any(marker in descriptor for marker in marketplace_markers) or descriptor == "listing"


def _has_non_marketplace_evidence(value: Any) -> bool:
    return any(
        _evidence_descriptor(item) and not _is_marketplace_evidence(item)
        for item in _evidence_items(value)
    )


def _comparison_evidence_supports_decision(value: Any, decision: str) -> bool:
    usable = [
        item for item in _evidence_items(value)
        if _evidence_descriptor(item) and not _is_marketplace_evidence(item)
    ]
    if not usable:
        return False
    if decision != "no":
        for item in usable:
            descriptor = _evidence_descriptor(item)
            derived_from = item.get("derived_from") if isinstance(item.get("derived_from"), dict) else {}
            shared_terms = derived_from.get("shared_terms") if isinstance(derived_from.get("shared_terms"), list) else []
            shared_norm = {normalize(term) for term in shared_terms if normalize(term)}
            interface_markers = {
                "navigator", "garmin", "usb", "lightning", "typec", "canbus", "bluetooth",
                "carplay", "androidauto", "magsafe", "mount", "cradle", "socket", "plug",
                "eixo", "haste", "estria", "estrias", "rosca", "diametro", "flange", "furacao",
                "fixacao", "hdmi", "displayport", "wifi", "tensao", "voltagem", "frequencia",
                "potencia", "pressao", "protocolo",
            }
            identifier_ok = bool(shared_norm & interface_markers) or any(
                bool(re.match(r"^(?:m\d+|\d+(?:mm|cm|in|pol|v|hz|w|a|bar|psi|pinos?|pins?))$", term))
                for term in shared_norm
            )
            if (
                bool(item.get("grounded"))
                and ("derived" in descriptor or "derivada" in descriptor)
                and _has_meaningful_value(derived_from.get("product"))
                and _has_meaningful_value(derived_from.get("target") or derived_from.get("target_vehicle"))
                and identifier_ok
            ):
                return True
    text = " ".join(_evidence_factual_text(item) for item in usable)
    if decision == "no":
        markers = (
            "incompativ", "interfaces diferentes", "interface diferente", "nao coincide",
            "nao corresponde", "mismatch", "divergente", "outro encaixe", "different interface",
        )
    else:
        markers = (
            "equival", "mesma interface", "interfaces iguais", "interface igual", "corresponde",
            "compativ", "mesmo encaixe", "technical match", "interface match", "same interface",
            "ambos usam",
        )
    return any(marker in text for marker in markers)


def _evidence_factual_text(item: dict[str, Any]) -> str:
    fields = (
        "fact", "reference", "claim", "snippet", "excerpt", "content", "value", "title",
        "authority", "source_type",
    )
    text = " ".join(str(item.get(field) or "") for field in fields)
    return re.sub(r"[^a-z0-9]+", " ", normalize(text)).strip()


def _has_usable_evidence(value: Any) -> bool:
    if isinstance(value, dict):
        status = normalize(value.get("status") or value.get("state") or "")
        if status in {
            "error", "failed", "failure", "empty", "not_found", "sem_resultado", "erro", "falha",
            "unavailable", "no_results", "indisponivel",
        }:
            return False
        try:
            http_status = int(value.get("http_status") or value.get("status_code") or 0)
        except (TypeError, ValueError):
            http_status = 0
        if http_status >= 400:
            return False
        # O agente V2 usa `authority` + `reference`; o contrato historico do
        # orquestrador usa `source_type` + `fact`. Aceite os dois formatos,
        # exigindo sempre algum conteudo factual (nao apenas o tipo da fonte).
        factual_keys = (
            "fact", "reference", "claim", "snippet", "excerpt", "content", "value",
            "title", "url",
        )
        factual_text = normalize(" ".join(str(value.get(key) or "") for key in factual_keys))
        failure_markers = (
            "http 403", "erro 403", "sem resultado", "nenhum resultado", "search failed",
            "busca falhou", "resultado vazio", "no results", "unavailable", "indisponivel",
        )
        if any(marker in factual_text for marker in failure_markers):
            return False
        if any(_has_meaningful_value(value.get(key)) for key in factual_keys):
            return True
        nested_keys = ("evidence", "results", "items", "sources")
        return any(_has_usable_evidence(value.get(key)) for key in nested_keys)
    if isinstance(value, (list, tuple, set)):
        return any(_has_usable_evidence(item) for item in value)
    text = normalize(value)
    if not text:
        return False
    failure_markers = (
        "http 403", "erro 403", "sem resultado", "nenhum resultado", "search failed", "busca falhou",
        "resultado vazio", "no results", "unavailable",
    )
    return not any(marker in text for marker in failure_markers)


def _compatibility_condition_matches_answer(condition: str, answer_norm: str) -> bool:
    return _compatibility_condition_matches_text(condition, answer_norm)


def _compatibility_condition_supported_by_analysis(condition: str, analysis: dict[str, Any]) -> bool:
    product, target, comparison = _compatibility_evidence_groups(analysis)
    evidence_text = " ".join(
        _evidence_factual_text(item)
        for value in (product, target, comparison)
        for item in _evidence_items(value)
    )
    structured_text = " ".join([
        str(analysis.get("product_interface") or ""),
        str(analysis.get("target_interface") or ""),
        evidence_text,
    ])
    return _compatibility_condition_matches_text(condition, structured_text)


def _compatibility_condition_matches_text(condition: str, compared_text: str) -> bool:
    condition_tokens = _compatibility_condition_tokens(condition)
    if not condition_tokens:
        return False
    answer_tokens = _compatibility_condition_tokens(compared_text)
    numeric = {token for token in condition_tokens if any(char.isdigit() for char in token)}
    if numeric and numeric & answer_tokens:
        return True
    overlap = condition_tokens & answer_tokens
    required = min(2, len(condition_tokens)) if len(condition_tokens) <= 3 else max(2, len(condition_tokens) // 3)
    return len(overlap) >= required


def _compatibility_condition_tokens(value: Any) -> set[str]:
    text = re.sub(r"[^a-z0-9]+", " ", normalize(value))
    text = re.sub(r"\b(?:base|suporte|preparacao|encaixe)\b", " interface ", text)
    text = re.sub(r"\b(?:de fabrica|fabrica)\b", " original ", text)
    stopwords = {
        "moto", "veiculo", "produto", "adaptador", "equipada", "equipado", "possuir", "possui",
        "usar", "usa", "utilizar", "ter", "tenha", "com", "para", "desde", "que", "uma", "um",
        "de", "da", "do", "das", "dos", "a", "o", "e", "ser", "seja", "somente", "apenas",
        "necessaria", "necessario", "depende", "compativel", "compatibilidade",
    }
    return {token for token in text.split() if len(token) >= 2 and token not in stopwords}


def _has_meaningful_value(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_has_meaningful_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_meaningful_value(item) for item in value)
    return bool(str(value or "").strip())


def _asks_for_approved_compatibility_detail(norm: str) -> bool:
    request_terms = r"(?:informe|informar|confirme|confirmar|diga|indique|precisamos|necessario|necessaria)"
    allowed_details = (
        r"(?:ano|versao|motor|base original|base paralela|modelo da base|modelo do suporte|codigo gravado|"
        r"codigo da base|codigo do suporte|modelo completo|modelo do aparelho|modelo da maquina|modelo da ferramenta|"
        r"eixo|estrias?|rosca|diametro|fixacao|geracao|conector|protocolo|tensao|voltagem|frequencia|"
        r"potencia|pressao|furacao|medida|dimensao|tipo de encaixe|tipo de conexao)"
    )
    return bool(
        re.search(rf"\b{request_terms}\b.{{0,100}}\b{allowed_details}\b", norm)
        or re.search(rf"\b{allowed_details}\b.{{0,100}}\b{request_terms}\b", norm)
    )


def _has_compatibility_evidence(question: QuestionContext, listing: ListingSnapshot) -> bool:
    listing_text = normalize(listing.searchable_text())
    question_text = normalize(question.text)
    generic_tokens = {
        "serve", "servir", "compativel", "aplica", "funciona", "encaixa", "carro", "moto",
        "produto", "peca", "adaptador", "suporte", "base", "gps", "essa", "esse", "nesta", "neste",
    }
    tokens = [token for token in re.findall(r"[a-z0-9]{3,}", question_text) if token not in generic_tokens]
    if not tokens:
        return False
    hits = sum(1 for token in tokens if token in listing_text)
    return hits >= min(2, len(tokens))


def _asserts_originality_or_warranty(norm: str) -> bool:
    return any(term in norm for term in ("produto original", "peca original", "tem garantia", "garantia de", "genuino", "genuina"))


def _has_originality_or_warranty_evidence(listing: ListingSnapshot) -> bool:
    return _listing_has_any(listing, ("original", "genuino", "genuina", "garantia", "nota fiscal"))


def _listing_has_any(listing: ListingSnapshot, needles: tuple[str, ...]) -> bool:
    listing_text = normalize(listing.searchable_text())
    return any(needle in listing_text for needle in needles)
