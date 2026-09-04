from __future__ import annotations

from types import SimpleNamespace

import backend_api  # noqa: F401 - configura dependencias legadas do runtime de IA
import pytest

from backend.modules.perguntas_pos_venda.ai import client_workflows
from backend.modules.perguntas_pos_venda.ai import compatibility
from backend.modules.perguntas_pos_venda.ai import evidence
from backend.modules.perguntas_pos_venda.ai import sources as research_sources


_OFFICIAL_URL = "https://si.shimano.com/en/pdfs/dm/MAFC002/DM-MAFC002-10-ENG.pdf"
_SECOND_TECHNICAL_URL = "https://www.bike-components.de/en/Shimano/FC-MT510-1-technical-data/"
_MARKETPLACE_URL = "https://produto.mercadolivre.com.br/MLB-1234567890"
_PRODUCT_REFERENCE = "Coroa BCD 94/96 mm com 4 furos e geometria simetrica."
_TARGET_REFERENCE = "Shimano FC-MT510-1: PCD 96 mm, 4 furos e geometria assimetrica."
_TARGET_EVIDENCE_REFERENCE = "interface.symmetry: asymmetric"
_DERIVED_SOURCE_TYPE = "derived_incompatibility_from_grounded_evidence"


def _agent_input(target_identity: str, product_reference: str) -> dict:
    return {
        "item": {"title": "Produto", "description": product_reference, "attributes": []},
        "intent": {
            "categoria": "compatibility",
            "fluxo": "perguntas_anuncio",
            "compatibilidade": {
                "aplicavel": True,
                "target_item": target_identity,
                "target_type": "machine_tool",
                "compatibility_profile": "machine_interface",
            },
        },
    }


def _target_sources(
    target_url: str,
    technical_quorum: bool,
    *,
    official_target: bool,
    duplicate_fingerprint: bool = False,
    duplicate_origin: bool = False,
) -> list[dict]:
    sources = [{
        "authority": "official_manufacturer" if official_target else "technical_independent",
        "url": target_url,
        "origin_key": "shimano.com" if target_url == _OFFICIAL_URL else "mercadolivre.com.br",
        "copy_fingerprint": "target-copy-a",
    }]
    if not official_target and technical_quorum and target_url == _OFFICIAL_URL:
        sources.append({
            "authority": "technical_distributor",
            "url": (
                "https://productinfo.shimano.com/en/product/FC-MT510-1.html"
                if duplicate_origin else _SECOND_TECHNICAL_URL
            ),
            "origin_key": "shimano.com" if duplicate_origin else "bike-components.de",
            "copy_fingerprint": "target-copy-a" if duplicate_fingerprint else "target-copy-b",
        })
    return sources


def _verified_target_result(
    *, target_identity: str, field_name: str, value: str,
    target_url: str = _OFFICIAL_URL, technical_quorum: bool = True,
    official_target: bool = True, duplicate_fingerprint: bool = False,
    duplicate_origin: bool = False,
) -> dict:
    return {
        "function": "web_search_question_context",
        "result": {
            "found": True,
            "context": "RAW_LEAD_NAO_DEVE_ENTRAR_NO_GROUNDING",
            "verified_target_evidence": [{
                "scope": "target",
                "target_identity": target_identity,
                "field_name": field_name,
                "value": value,
                "unit": "",
                "activation_policy": "official_or_two_independent_sources",
                "sources": _target_sources(
                    target_url,
                    technical_quorum,
                    official_target=official_target,
                    duplicate_fingerprint=duplicate_fingerprint,
                    duplicate_origin=duplicate_origin,
                ),
            }],
        },
    }


def _mt510_grounding(
    target_url: str,
    technical_quorum: bool,
    *,
    official_target: bool,
    contract_identity: str = "Shimano FC-MT510-1",
    duplicate_fingerprint: bool = False,
    duplicate_origin: bool = False,
) -> dict:
    result = _verified_target_result(
        target_identity=contract_identity,
        field_name="interface.symmetry",
        value="asymmetric",
        target_url=target_url,
        technical_quorum=technical_quorum,
        official_target=official_target,
        duplicate_fingerprint=duplicate_fingerprint,
        duplicate_origin=duplicate_origin,
    )
    safe_result = evidence._perguntas_ia_verified_research_view(result)
    return evidence._perguntas_ia_v2_grounding_coletar(
        [safe_result],
        agent_input=_agent_input("Shimano FC-MT510-1", _PRODUCT_REFERENCE),
    )


def _mt510_analysis(
    *, attribute: str = "symmetry", decisive: bool = True,
    target_url: str = _OFFICIAL_URL, server_grounding: bool = True,
    technical_quorum: bool = True, official_target: bool = True,
    contract_identity: str = "Shimano FC-MT510-1",
    duplicate_fingerprint: bool = False, duplicate_origin: bool = False,
) -> dict:
    product_evidence = {"reference": _PRODUCT_REFERENCE}
    target_evidence = [{"reference": _TARGET_EVIDENCE_REFERENCE}]
    if not server_grounding:
        product_evidence.update({"authority": "internal_listing", "grounded": True})
        for item in target_evidence:
            item.update({"authority": "generated_verified_target", "grounded": True})
    payload = {
        "target_type": "machine_tool",
        "target_item": "Shimano FC-MT510-1",
        "compatibility_profile": "machine_interface",
        "product_interface": "BCD 94/96 mm, 4 furos, geometria simetrica",
        "target_interface": "PCD 96 mm, 4 furos, geometria assimetrica",
        "comparison_attributes": [{
            "attribute": attribute,
            "product_value": "simetrica",
            "target_value": "assimetrica",
            "result": "conflict",
            "decisive": decisive,
            "evidence_refs": [_PRODUCT_REFERENCE, _TARGET_REFERENCE],
        }],
        "decision": "no",
        "confidence": 0.93,
        "evidence": {
            "product": [product_evidence],
            "target_vehicle": target_evidence,
            "equivalence": [],
        },
    }
    grounding = _mt510_grounding(
        target_url,
        technical_quorum,
        official_target=official_target,
        contract_identity=contract_identity,
        duplicate_fingerprint=duplicate_fingerprint,
        duplicate_origin=duplicate_origin,
    ) if server_grounding else None
    return compatibility._perguntas_ia_v2_compatibilidade_normalizar(payload, grounding=grounding)


def _equivalent_exact_value_analysis(
    *, attribute: str, product_value: str, target_value: str,
    product_reference: str, target_reference: str, unit: str = "",
) -> dict:
    target_field = {
        "connector_type": "interface.connector_type",
        "fixation_geometry": "interface.fixation_geometry",
        "symmetry": "interface.symmetry",
    }.get(attribute, "interface.bolt_pattern")
    target_evidence_reference = f"{target_field}: {target_value}"
    result = evidence._perguntas_ia_verified_research_view(_verified_target_result(
        target_identity="alvo tecnico",
        field_name=target_field,
        value=target_value,
    ))
    grounding = evidence._perguntas_ia_v2_grounding_coletar(
        [result],
        agent_input=_agent_input("alvo tecnico", product_reference),
    )
    return compatibility._perguntas_ia_v2_compatibilidade_normalizar({
        "target_item": "alvo tecnico",
        "product_interface": product_reference,
        "target_interface": target_reference,
        "comparison_attributes": [{
            "attribute": attribute,
            "product_value": product_value,
            "target_value": target_value,
            "unit": unit,
            "result": "conflict",
            "decisive": True,
        }],
        "decision": "no",
        "evidence": {
            "product": [{"reference": product_reference}],
            "target_vehicle": [{"reference": target_evidence_reference}],
            "equivalence": [],
        },
    }, grounding=grounding)


def test_mt510_decisive_grounded_symmetry_conflict_derives_no() -> None:
    analysis = _mt510_analysis()

    assert analysis["decision"] == "no"
    derived = next(
        item for item in analysis["evidence"]["equivalence"]
        if item.get("source_type") == _DERIVED_SOURCE_TYPE
    )
    assert derived["authority"] == "derived"
    assert derived["grounded"] is True
    assert derived["reference"].startswith("Nao compativel:")
    assert derived["derived_from"]["attribute"] == "symmetry"
    assert derived["derived_from"]["product"] == [_PRODUCT_REFERENCE]
    assert derived["derived_from"]["target_vehicle"] == [_TARGET_EVIDENCE_REFERENCE]
    assert set(derived["derived_from"]["target_domains"]) == {"shimano.com"}


def test_question_web_tool_and_prepare_grounding_preserve_only_verified_target_contract_for_mt510(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_input = _agent_input("Shimano FC-MT510-1", _PRODUCT_REFERENCE)
    raw_result = _verified_target_result(
        target_identity="Shimano FC-MT510-1",
        field_name="interface.symmetry",
        value="asymmetric",
    )
    monkeypatch.setattr(research_sources, "_ia_agent_perguntas_precisa_web", lambda _entry: True)
    monkeypatch.setattr(
        research_sources,
        "_ia_agent_perguntas_queries_web",
        lambda _entry, _results: [{"query": "Shimano FC-MT510-1 symmetry", "type": "target_interface_official"}],
    )
    monkeypatch.setattr(
        research_sources,
        "_ia_agent_perguntas_contexto_web_profundo",
        lambda *_args, **_kwargs: {
            "context": raw_result["result"]["context"],
            "verified_target_evidence": raw_result["result"]["verified_target_evidence"],
            "verified_product_evidence": [],
            "research_metrics": {},
        },
    )
    web_result = research_sources._ia_agent_perguntas_web_tool("tenant-a", agent_input, [])
    assert web_result is not None
    assert web_result["result"]["verified_target_evidence"]
    client = SimpleNamespace(
        agent_input=agent_input,
        compatibility_analysis=evidence._perguntas_ia_v2_compatibilidade_padrao(agent_input),
    )

    client_workflows._prepare_grounding(client, [web_result], {}, web_result)

    target_grounding = client._compatibility_grounding["target_vehicle"]
    assert len(target_grounding) == 1
    assert target_grounding[0]["source_type"] == "verified_target_evidence"
    assert target_grounding[0]["authority"] == "generated_verified_target"
    assert "RAW_LEAD_NAO_DEVE_ENTRAR_NO_GROUNDING" not in str(client._compatibility_grounding)
    analysis = compatibility._perguntas_ia_v2_compatibilidade_normalizar({
        "product_interface": "BCD 94/96 mm, 4 furos, geometria simetrica",
        "target_interface": "PCD 96 mm, 4 furos, geometria assimetrica",
        "comparison_attributes": [{
            "attribute": "symmetry",
            "product_value": "simetrica",
            "target_value": "assimetrica",
            "result": "conflict",
            "decisive": True,
        }],
        "decision": "no",
        "evidence": {
            "product": [{"reference": _PRODUCT_REFERENCE}],
            "target_vehicle": [{"reference": _TARGET_EVIDENCE_REFERENCE}],
            "equivalence": [],
        },
    }, base=client.compatibility_analysis, grounding=client._compatibility_grounding)
    assert analysis["decision"] == "no"


def test_mt510_non_decisive_conflict_does_not_derive_but_preserves_model_decision() -> None:
    analysis = _mt510_analysis(decisive=False)

    assert analysis["decision"] == "no"
    assert not any(item.get("source_type") == _DERIVED_SOURCE_TYPE for item in analysis["evidence"]["equivalence"])


def test_mt510_self_attested_grounding_is_not_derived_but_does_not_rewrite_model() -> None:
    analysis = _mt510_analysis(server_grounding=False)

    assert analysis["decision"] == "no"
    assert not any(item.get("source_type") == _DERIVED_SOURCE_TYPE for item in analysis["evidence"]["equivalence"])


def test_mt510_single_non_official_source_is_not_derived_but_model_decision_survives() -> None:
    analysis = _mt510_analysis(technical_quorum=False, official_target=False)

    assert analysis["decision"] == "no"
    assert not any(item.get("source_type") == _DERIVED_SOURCE_TYPE for item in analysis["evidence"]["equivalence"])


def test_mt510_marketplace_only_target_is_not_derived_but_model_decision_survives() -> None:
    analysis = _mt510_analysis(target_url=_MARKETPLACE_URL)

    assert analysis["decision"] == "no"
    assert analysis["evidence"]["target_vehicle"] == []
    assert not any(item.get("source_type") == _DERIVED_SOURCE_TYPE for item in analysis["evidence"]["equivalence"])


def test_mt510_two_independent_technical_sources_can_bind_target() -> None:
    analysis = _mt510_analysis(technical_quorum=True, official_target=False)

    assert analysis["decision"] == "no"
    assert any(item.get("source_type") == _DERIVED_SOURCE_TYPE for item in analysis["evidence"]["equivalence"])


@pytest.mark.parametrize(
    "grounding_override",
    [
        {"contract_identity": "Shimano FC-MT610-1"},
        {"official_target": False, "duplicate_fingerprint": True},
        {"official_target": False, "duplicate_origin": True},
    ],
)
def test_mt510_unbound_identity_or_non_independent_quorum_does_not_derive(
    grounding_override: dict,
) -> None:
    analysis = _mt510_analysis(**grounding_override)

    assert analysis["decision"] == "no"
    assert not any(item.get("source_type") == _DERIVED_SOURCE_TYPE for item in analysis["evidence"]["equivalence"])


def test_raw_target_web_context_never_becomes_target_evidence() -> None:
    raw_result = {
        "function": "web_search_question_context",
        "result": {
            "found": True,
            "context": _TARGET_REFERENCE,
            "verified_target_evidence": [],
        },
    }
    safe_result = evidence._perguntas_ia_verified_research_view(raw_result)
    grounding = evidence._perguntas_ia_v2_grounding_coletar(
        [safe_result],
        agent_input=_agent_input("Shimano FC-MT510-1", _PRODUCT_REFERENCE),
    )

    assert grounding["target_vehicle"] == []
    assert _TARGET_REFERENCE not in str(grounding)


def test_research_view_keeps_all_states_but_sanitizes_pii_and_drops_scope_overrides() -> None:
    raw_vin = "8AD2MKFWXCG035615"
    raw_email = "comprador@example.com"
    raw_phone = "+55 11 99999-8888"
    raw_result = {
        "function": "web_search_question_context",
        "arguments": {
            "queries": [{"query": f"VIN {raw_vin} email {raw_email}"}],
            "tenant_id": "tenant-b",
        },
        "result": {
            "found": True,
            "tenant_id": "tenant-b",
            "store": "Outra Loja",
            "context": (
                "CONTEXT_CANARY "
                f"VIN {raw_vin}; email {raw_email}; telefone {raw_phone}."
            ),
            "product_research_evidence": [
                {
                    "field_name": "Compatibility.Application",
                    "scope": "application",
                    "value": f"STATE_CANARY candidate VIN {raw_vin}",
                    "state": "candidate",
                    "sources": [{
                        "source_type": "technical_independent",
                        "authority": "technical_independent",
                        "url": "https://catalogo.example/candidate",
                        "domain": "catalogo.example",
                        "section_ref": f"Contato {raw_email}",
                    }],
                },
                {
                    "field_name": "electrical.voltage",
                    "scope": "product",
                    "value": "12 V",
                    "state": "conflict",
                    "conflict_group": "voltage-1",
                    "sources": [{
                        "source_type": "official_manufacturer",
                        "authority": "official_manufacturer",
                        "url": "https://maker.example/conflict",
                        "domain": "maker.example",
                    }],
                },
                {
                    "field_name": "physical.weight",
                    "scope": "product",
                    "value": "1 kg",
                    "state": "expired",
                    "sources": [{
                        "source_type": "official_listing",
                        "authority": "official_listing",
                        "url": "https://produto.mercadolivre.com.br/MLB-1",
                        "domain": "produto.mercadolivre.com.br",
                    }],
                },
            ],
            "secret": "must-not-cross",
        },
    }

    research_view = evidence._perguntas_ia_research_view(raw_result)
    rendered = str(research_view)
    projected = research_view["result"]

    assert research_view["arguments"] == {}
    assert projected["scope"] == "sanitized_research_agent_discretion"
    assert projected["found"] is True
    assert "CONTEXT_CANARY" in projected["context"]
    assert {item["state"] for item in projected["product_research_evidence"]} == {
        "candidate", "conflict", "expired",
    }
    assert projected["product_research_evidence"][0]["field_name"] == "compatibility.application"
    assert raw_vin not in rendered
    assert raw_email not in rendered
    assert raw_phone not in rendered
    assert "[CHASSI_PROTEGIDO]" in rendered
    assert "[EMAIL_PROTEGIDO]" in rendered
    assert "[TELEFONE_PROTEGIDO]" in rendered
    assert "tenant-b" not in rendered
    assert "Outra Loja" not in rendered
    assert "must-not-cross" not in rendered


def test_prompt_views_redact_unlabelled_all_letter_vin_from_source_metadata() -> None:
    raw_vin = "ABCDEFGHJKLMNPRST"
    contaminated_url = f"https://maker.example/{raw_vin}/manual"
    safe_url = "https://maker.example/catalog/manual"
    raw_result = {
        "function": "web_search_question_context",
        "result": {
            "found": True,
            "context": f"URL: {contaminated_url}",
            "verified_product_evidence": [{
                "field_name": "electrical.power",
                "scope": "product",
                "value": "22",
                "unit": "W",
                "state": "verified",
                "source_authorities": [raw_vin],
                "sources": [{
                    "source_type": "official_manufacturer",
                    "authority": "official_manufacturer",
                    "url": safe_url,
                    "domain": "maker.example",
                    "section_ref": f"codigo {raw_vin}",
                }],
            }],
            "verified_target_evidence": [{
                "scope": "target",
                "target_identity": "Peugeot 207 XR 1.4 2010",
                "field_name": "interface.fixation_geometry",
                "value": "three_point_mount",
                "unit": raw_vin,
                "activation_policy": raw_vin,
                "sources": [
                    {
                        "authority": "official_manufacturer",
                        "url": contaminated_url,
                        "origin_key": "maker.example",
                        "copy_fingerprint": "copy-a",
                    },
                    {
                        "authority": "official_manufacturer",
                        "url": safe_url,
                        "origin_key": f"publisher-{raw_vin}",
                        "copy_fingerprint": f"copy-{raw_vin}",
                    },
                ],
            }],
            "product_research_evidence": [{
                "field_name": "electrical.power",
                "scope": "product",
                "value": "22",
                "unit": "W",
                "state": "candidate",
                "sources": [{
                    "source_type": "technical_independent",
                    "authority": "technical_independent",
                    "url": safe_url,
                    "domain": "maker.example",
                    "section_ref": f"codigo {raw_vin}",
                }],
            }, {
                "field_name": raw_vin,
                "scope": "product",
                "value": "safe-value",
                "state": "candidate",
                "sources": [],
            }],
            "research_metrics": {
                "stop_reason": f"done-{raw_vin}",
                raw_vin: 1,
            },
        },
    }

    verified_view = evidence._perguntas_ia_verified_research_view(raw_result)
    research_view = evidence._perguntas_ia_research_view(raw_result)

    assert raw_vin not in str(verified_view)
    assert raw_vin not in str(research_view)
    assert contaminated_url not in research_view["result"]["research_sources"]


@pytest.mark.parametrize(
    ("attribute", "product_value", "target_value", "unit", "product_reference", "target_reference"),
    [
        (
            "connector_type", "USB Type-C", "USB-C", "",
            "Tipo de conector: USB Type-C.", "Connector type: USB-C.",
        ),
        (
            "connector_type", "USB-C femea", "USB-C", "",
            "Tipo de conector: USB-C femea.", "Connector type: USB-C.",
        ),
        (
            "connector_type", "USB-A/USB-C", "USB-C", "",
            "Tipo de conector: USB-A ou USB-C.", "Connector type: USB-C.",
        ),
        (
            "fixation_geometry", "geometria simetrica", "simetrica", "",
            "Geometria de fixacao simetrica.", "Geometria de fixacao: simetrica.",
        ),
        (
            "bcd_pcd", "BCD 4 x 9,6 cm", "PCD 4x96 mm", "mm",
            "Padrao de furacao BCD 4 x 9,6 cm.", "Bolt pattern PCD 4x96 mm.",
        ),
        (
            "bcd_pcd", "BCD 94/96 mm", "PCD 96 mm", "mm",
            "Padrao de furacao BCD 94/96 mm.", "Bolt pattern PCD 96 mm.",
        ),
        (
            "bcd_pcd", "4x94/96 mm", "4x96 mm", "mm",
            "Padrao de furacao 4x94/96 mm.", "Bolt pattern 4x96 mm.",
        ),
        (
            "bcd_pcd", "BCD 4x94/96 mm", "PCD 4x96 mm", "mm",
            "Padrao de furacao BCD 4x94/96 mm.", "Bolt pattern PCD 4x96 mm.",
        ),
    ],
)
def test_equal_or_overlapping_exact_values_do_not_derive_false_incompatibility(
    attribute: str,
    product_value: str,
    target_value: str,
    unit: str,
    product_reference: str,
    target_reference: str,
) -> None:
    analysis = _equivalent_exact_value_analysis(
        attribute=attribute,
        product_value=product_value,
        target_value=target_value,
        unit=unit,
        product_reference=product_reference,
        target_reference=target_reference,
    )

    assert analysis["decision"] == "no"
    assert not any(item.get("source_type") == _DERIVED_SOURCE_TYPE for item in analysis["evidence"]["equivalence"])


@pytest.mark.parametrize(
    ("attribute", "product_value", "target_value", "unit", "product_reference", "target_reference"),
    [
        (
            "connector_type", "USB-A", "USB-C", "",
            "Tipo de conector: USB-A ou USB-C.", "Connector type: USB-C.",
        ),
        (
            "bcd_pcd", "BCD 94 mm", "PCD 96 mm", "mm",
            "Padrao de furacao BCD 94/96 mm.", "Bolt pattern PCD 96 mm.",
        ),
    ],
)
def test_model_selected_partial_product_value_never_derives_incompatibility(
    attribute: str,
    product_value: str,
    target_value: str,
    unit: str,
    product_reference: str,
    target_reference: str,
) -> None:
    analysis = _equivalent_exact_value_analysis(
        attribute=attribute,
        product_value=product_value,
        target_value=target_value,
        unit=unit,
        product_reference=product_reference,
        target_reference=target_reference,
    )

    assert analysis["decision"] == "no"
    assert not any(
        item.get("source_type") == _DERIVED_SOURCE_TYPE
        for item in analysis["evidence"]["equivalence"]
    )


@pytest.mark.parametrize(
    "product_reference",
    [
        "Nao possui conector USB-A.",
        "Sem conector USB-A.",
        "Nao inclui USB-A; conector: USB-C.",
        "Nunca possui conector USB-A.",
        "Conector USB-A nao suportado.",
        "Nao oferece conector USB-A.",
        "Tipo de conector nao USB-A.",
        "Connector not USB-A.",
        "Conector: nao USB-A.",
        "Nao compativel com USB-A.",
        "Not compatible with USB-A.",
        "USB-A nao e compativel.",
        "USB-A is not compatible.",
        "USB-A sem suporte.",
        "USB-A lacks support.",
    ],
)
def test_negated_product_value_never_derives_incompatibility(
    product_reference: str,
) -> None:
    analysis = _equivalent_exact_value_analysis(
        attribute="connector_type",
        product_value="USB-A",
        target_value="USB-C",
        product_reference=product_reference,
        target_reference="Connector type: USB-C.",
    )

    assert analysis["decision"] == "no"
    assert not any(
        item.get("source_type") == _DERIVED_SOURCE_TYPE
        for item in analysis["evidence"]["equivalence"]
    )


@pytest.mark.parametrize(
    ("attribute", "product_value", "target_value", "unit", "product_reference", "target_reference"),
    [
        (
            "bcd_pcd", "BCD 4x104 mm", "PCD 4x96 mm", "mm",
            "Padrao de furacao BCD 4x104 mm.", "Bolt pattern PCD 4x96 mm.",
        ),
        (
            "connector_type", "USB-A", "USB-C", "",
            "Tipo de conector: USB-A.", "Connector type: USB-C.",
        ),
        (
            "fixation_geometry", "geometria simetrica", "geometria assimetrica", "",
            "Geometria de fixacao simetrica.", "Geometria de fixacao assimetrica.",
        ),
    ],
)
def test_disjoint_allowlisted_exact_values_derive_incompatibility(
    attribute: str,
    product_value: str,
    target_value: str,
    unit: str,
    product_reference: str,
    target_reference: str,
) -> None:
    analysis = _equivalent_exact_value_analysis(
        attribute=attribute,
        product_value=product_value,
        target_value=target_value,
        unit=unit,
        product_reference=product_reference,
        target_reference=target_reference,
    )

    assert analysis["decision"] == "no"
    assert any(item.get("source_type") == _DERIVED_SOURCE_TYPE for item in analysis["evidence"]["equivalence"])


@pytest.mark.parametrize("attribute", ["outside_diameter", "voltage", "tolerance"])
def test_mt510_non_allowlisted_conflict_does_not_derive_but_preserves_model_decision(attribute: str) -> None:
    analysis = _mt510_analysis(attribute=attribute)

    assert analysis["decision"] == "no"
    assert not any(item.get("source_type") == _DERIVED_SOURCE_TYPE for item in analysis["evidence"]["equivalence"])
