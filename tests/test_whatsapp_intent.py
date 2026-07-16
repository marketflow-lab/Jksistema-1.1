from datetime import datetime

import pytest

from backend.services import codex_console, whatsapp_bridge
from backend.services.whatsapp import intent


@pytest.mark.parametrize(
    ("text", "domains"),
    [
        ("relatorio de vendas do Mercado Livre", ["vendas", "anuncios_ml"]),
        ("saldo do estoque Full", ["estoque", "mercado_full"]),
        ("ultima venda do SKU ABC-9 no ML", ["vendas", "anuncios_ml"]),
        ("qual e o clima hoje?", []),
    ],
)
def test_query_domains_component_matches_facade(text: str, domains: list[str]) -> None:
    assert intent.query_only_domains(text) == whatsapp_bridge._whatsapp_query_only_domains(text) == domains


@pytest.mark.parametrize(
    ("text", "readonly", "mutation"),
    [
        ("qual o saldo em estoque?", True, False),
        ("envie o relatorio de vendas", False, False),
        ("atualize agora os dados via API", False, False),
        ("altere o estoque agora", False, True),
        ("pause o anuncio do Mercado Livre", False, True),
        ("responda a pergunta do cliente", False, True),
    ],
)
def test_readonly_and_mutation_classification(text: str, readonly: bool, mutation: bool) -> None:
    assert intent.readonly_inquiry(text) == whatsapp_bridge._whatsapp_readonly_inquiry(text) is readonly
    component = intent.mutation_intent(text, mutation_detector=codex_console._codex_prompt_pede_alteracao)
    assert component == whatsapp_bridge._whatsapp_mutation_intent(text) is mutation


def test_protected_mutation_domains_keep_post_sale_exception() -> None:
    detector = codex_console._codex_prompt_pede_alteracao
    assert intent.protected_mutation_domains(
        "pause o anuncio do Mercado Livre", mutation_detector=detector
    ) == whatsapp_bridge._whatsapp_protected_mutation_domains("pause o anuncio do Mercado Livre") == [
        "anuncios_ml"
    ]
    assert intent.protected_mutation_domains(
        "responda a pergunta do Mercado Livre", mutation_detector=detector
    ) == whatsapp_bridge._whatsapp_protected_mutation_domains("responda a pergunta do Mercado Livre") == []


def test_action_spec_domains_support_dicts_and_objects() -> None:
    spec = {
        "id": "ml.anuncio.pause",
        "module": "anuncios_ml",
        "label": "Pausar anuncio",
        "side_effects": ["mercado_livre"],
    }
    assert intent.action_spec_query_only_domains(spec) == whatsapp_bridge._whatsapp_action_spec_query_only_domains(
        spec
    ) == ["anuncios_ml"]


def test_store_matching_prefers_the_most_specific_registered_name() -> None:
    stores = ["JK", "JK Pecas", "Deckas", "São Paulo"]
    assert intent.exact_store_matches("consulte a JK Pecas", stores) == whatsapp_bridge._whatsapp_exact_store_matches(
        "consulte a JK Pecas", stores
    ) == ["JK Pecas"]
    assert intent.exact_store_matches("compare Deckas e São Paulo", stores) == ["Deckas", "São Paulo"]
    assert intent.all_stores_requested("compare todas as lojas") is True
    assert intent.store_scoped_request("vendas deste mes") is True
    assert intent.api_query_requires_store("", ["estoque"]) is True
    assert intent.api_query_requires_store("", []) is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mais resultados", True),
        ("os proximos", True),
        ("mostre mais resultados da JK", False),
    ],
)
def test_pagination_request_is_an_exact_followup(text: str, expected: bool) -> None:
    assert intent.pagination_request(text) == whatsapp_bridge._whatsapp_pagination_request(text) is expected


def test_contextual_period_uses_clock_resolved_by_facade(monkeypatch) -> None:
    current = datetime(2026, 7, 16, 14, 30)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current.replace(tzinfo=tz) if tz is not None else current

    monkeypatch.setattr(whatsapp_bridge, "datetime", FrozenDateTime)
    assert intent.contextual_report_request("relatorio deste mes na mesma loja") is True
    assert intent.contextual_report_period("relatorio deste mes", current=current) == (
        "2026-07-01",
        "2026-07-16",
    )
    assert whatsapp_bridge._whatsapp_contextual_report_period("relatorio deste mes") == (
        "2026-07-01",
        "2026-07-16",
    )
    assert intent.contextual_report_period("relatorio de ontem", current=current) == ("", "")


def test_implicit_store_followup_respects_reference_age_and_all_store_scope() -> None:
    kwargs = {"context_age": 60.0, "has_direct_policy": True}
    assert intent.implicit_store_followup("e os detalhes?", **kwargs) is True
    assert whatsapp_bridge._whatsapp_implicit_store_followup("e os detalhes?", **kwargs) is True
    assert intent.implicit_store_followup(
        "detalhes dos pedidos",
        context_age=intent.WHATSAPP_IMPLICIT_STORE_RECENT_SECONDS + 1,
        has_direct_policy=True,
    ) is False
    assert intent.implicit_store_followup("todas as lojas", **kwargs) is False
