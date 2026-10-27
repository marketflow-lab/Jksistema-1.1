"""Offline checks for exact promotion ownership before Renovacao mutations."""

import copy

import pytest
import requests
from fastapi import HTTPException

from backend.services import renovacao_promocoes as operations
from backend.services import renovacao_promocoes_helpers as helpers


TENANT = "tenant-identidade-test"
STORE = "loja-identidade-test"
SOURCE = "C-MLB997000001"
DESTINATION = "C-MLB997000002"
OTHER = "C-MLB997000003"
CFG = {"user_id": "123", "site_id": "MLB"}
IDS = [f"MLB{997100000 + index}" for index in range(1102)]


def _participation(campaign=SOURCE, status="started", **changes):
    return {"id": campaign, "type": "SELLER_CAMPAIGN", "status": status,
            "price": 80, "original_price": 100, "discount_percentage": 20, **changes}


def _direct_record(item):
    # A campaign endpoint may return this shape without identifying the campaign.
    return {"id": item, "status": "started", "promotion_type": "SELLER_CAMPAIGN",
            "price": 80, "original_price": 100, "discount_percentage": 20}


@pytest.fixture(autouse=True)
def offline_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Real network or unconfigured mutation is forbidden in this offline test")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(helpers.ctx, "_ml_api_request", forbidden)
    monkeypatch.setattr(helpers.ctx, "_parse_float_flex",
                        lambda value: None if value is None else float(value))
    monkeypatch.setattr(helpers.ctx, "_ml_extrair_preco_promocao_raw",
                        lambda raw, **kwargs: (raw.get("price"), raw.get("discount_percentage")))
    monkeypatch.setattr(helpers.ctx, "_ml_buscar_itens_batch",
                        lambda client, store, cfg, items: ([{
                            "id": item, "price": 80, "original_price": 100,
                            "currency_id": "BRL", "seller_id": "123",
                        } for item in items], cfg))
    monkeypatch.setattr(helpers.ctx, "_ml_listar_ids_anuncios_ativos",
                        lambda client, store, cfg, seller, **kwargs: ([], cfg))
    monkeypatch.setattr(helpers.ctx, "_cache_invalidar_loja", lambda *args: None)


def _select(records, **kwargs):
    return helpers._renovacao_selecionar_participacao_exata(
        records, SOURCE, IDS[0], **kwargs
    )


@pytest.mark.parametrize("status", ["started", "pending"])
def test_explicit_campaign_participation_without_offer_is_valid(status):
    result = _select([_participation(status=status)])
    assert result["situacao"] == "participante"
    assert result["raw"]["id"] == SOURCE
    assert result["raw"]["status"] == status


def test_explicit_state_pending_is_valid_without_offer():
    raw = _participation()
    raw.pop("status")
    raw["state"] = "pending"
    result = _select([raw])
    assert result["situacao"] == "participante"
    assert result["raw"]["state"] == "pending"


@pytest.mark.parametrize("status", ["candidate", "finished"])
def test_explicit_nonparticipating_state_confirms_absence(status):
    result = _select([_participation(status=status)])
    assert result["situacao"] == "ausente"
    assert not result.get("raw")


def test_listing_status_alone_does_not_prove_promotion_participation():
    raw = _participation()
    raw.pop("status")
    raw["status_item"] = "active"
    result = _select([raw])
    assert result["situacao"] == "inconclusivo"
    assert not result.get("raw")


def test_campaign_endpoint_context_cannot_replace_an_explicit_campaign_id():
    result = _select([_direct_record(IDS[0])])
    assert result["situacao"] == "inconclusivo"
    assert not result.get("raw")


@pytest.mark.parametrize("changes", [
    {"campaign": {"id": OTHER}},
    {"item_id": IDS[1]},
    {"seller_id": "999"},
])
def test_conflicting_campaign_item_or_seller_is_not_authorized(changes):
    result = _select([_participation(**changes)], seller_id="123")
    assert result["situacao"] == "inconclusivo"
    assert not result.get("raw")


def test_duplicate_records_with_conflicting_financial_terms_are_inconclusive():
    result = _select([
        _participation(),
        _participation(price=70, discount_percentage=30),
    ])
    assert result["situacao"] == "inconclusivo"
    assert not result.get("raw")


def test_identical_duplicate_records_do_not_create_false_ambiguity():
    record = _participation(status="pending")
    result = _select([record, copy.deepcopy(record)])
    assert result["situacao"] == "participante"
    assert result["raw"]["price"] == 80


def test_complete_response_for_another_campaign_confirms_source_absence():
    result = _select([_participation(campaign=OTHER)])
    assert result["situacao"] == "ausente"
    assert not result.get("raw")


def test_one_strict_fresh_get_resolves_source_and_destination_independently(monkeypatch):
    calls = []

    def exact(client, store, cfg, item, **kwargs):
        calls.append((client, store, item, kwargs))
        return [_participation(), _participation(campaign=DESTINATION, status="pending")], cfg

    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item", exact)
    results, cfg = helpers._renovacao_consultar_participacoes(
        TENANT, STORE, CFG, IDS[0], [SOURCE, DESTINATION]
    )
    assert cfg == CFG
    assert results[SOURCE]["situacao"] == "participante"
    assert results[DESTINATION]["situacao"] == "participante"
    assert results[SOURCE]["raw"]["id"] == SOURCE
    assert results[DESTINATION]["raw"]["id"] == DESTINATION
    assert len(calls) == 1
    assert calls[0][:3] == (TENANT, STORE, IDS[0])
    assert calls[0][3].get("strict") is True
    assert calls[0][3].get("force_refresh") is True


@pytest.mark.parametrize("error", [
    requests.exceptions.Timeout("Simulated timeout"),
    HTTPException(status_code=429, detail="Simulated rate limit"),
])
def test_lookup_error_is_inconclusive_for_both_campaigns_and_never_absence(monkeypatch, error):
    def exact(*args, **kwargs):
        raise error

    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item", exact)
    results, _ = helpers._renovacao_consultar_participacoes(
        TENANT, STORE, CFG, IDS[0], [SOURCE, DESTINATION]
    )
    assert set(results) == {SOURCE, DESTINATION}
    for result in results.values():
        assert result["situacao"] == "inconclusivo"
        assert result.get("erro")
        assert not result.get("raw")


def test_1102_confirmed_ids_do_not_satisfy_an_expected_count_of_908():
    with pytest.raises(HTTPException) as error:
        helpers._renovacao_exigir_coleta_completa([{"id": item} for item in IDS], 908, SOURCE)
    assert error.value.status_code == 409
    assert "1102" in str(error.value.detail)
    assert "908" in str(error.value.detail)


def _patch_discovery(monkeypatch, actual_source_ids, destination_ids=None):
    actual_source = set(actual_source_ids)
    confirmed_destination = destination_ids if destination_ids is not None else set()
    lookups = []

    def direct(client, store, cfg, campaign, **kwargs):
        assert (client, store, campaign) == (TENANT, STORE, SOURCE)
        return ([{"id": item} for item in IDS],
                {item: _direct_record(item) for item in IDS}, cfg)

    def exact(client, store, cfg, item, **kwargs):
        assert kwargs.get("strict") is True
        assert kwargs.get("force_refresh") is True
        lookups.append(item)
        campaign = SOURCE if item in actual_source else OTHER
        records = [_participation(campaign=campaign)]
        if item in confirmed_destination:
            records.append(_participation(campaign=DESTINATION, status="pending"))
        return records, cfg

    monkeypatch.setattr(helpers.ctx, "_ml_listar_itens_promocao_com_raw", direct)
    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item", exact)
    return lookups


def test_908_real_and_194_foreign_ids_only_allow_908_confirmed_mutations(monkeypatch):
    actual_source = set(IDS[:908])
    confirmed_destination = set()
    lookups = _patch_discovery(monkeypatch, actual_source, confirmed_destination)
    mutations = []

    def apply(client, store, cfg, item, *args, **kwargs):
        assert item in actual_source
        assert set(lookups) == set(IDS), "All discovery IDs must be checked before copying"
        mutations.append(item)
        confirmed_destination.add(item)
        return True, "", cfg

    monkeypatch.setattr(operations, "_renovacao_adicionar_item_campanha", apply)
    result, _ = operations._renovacao_copiar_mlbs_campanha(
        TENANT, STORE, CFG, SOURCE, DESTINATION,
        expected_origem_count=908, verificar_destino=False,
    )
    assert result["total_origem"] == 908
    assert result["incluidos"] == 908
    assert result["falhas_total"] == 0
    assert set(mutations) == actual_source
    assert confirmed_destination == actual_source
    assert set(lookups) == set(IDS)


def test_confirmed_1102_source_ids_raise_409_in_collection_instead_of_being_truncated(monkeypatch):
    lookups = _patch_discovery(monkeypatch, IDS)
    with pytest.raises(HTTPException) as error:
        helpers._renovacao_listar_itens_campanha(
            TENANT, STORE, CFG, SOURCE, "SELLER_CAMPAIGN", expected_count=908
        )
    assert error.value.status_code == 409
    assert "1102" in str(error.value.detail)
    assert "908" in str(error.value.detail)
    assert set(lookups) == set(IDS)


def test_readonly_audit_can_return_all_1102_verified_ids_without_truncating_to_908(monkeypatch):
    lookups = _patch_discovery(monkeypatch, IDS)
    statistics = {}
    items, raw, _ = helpers._renovacao_listar_itens_campanha(
        TENANT, STORE, CFG, SOURCE, "SELLER_CAMPAIGN", expected_count=908,
        validar_quantidade=False, estatisticas=statistics,
    )
    assert {item["id"] for item in items} == set(IDS)
    assert set(raw) == set(IDS)
    assert set(lookups) == set(IDS)
    assert statistics["estados"][SOURCE]["started"] == 1102


def test_audit_keeps_lookup_failures_as_inconclusive_not_absent(monkeypatch):
    def direct(client, store, cfg, campaign, **kwargs):
        return [{"id": IDS[0]}], {IDS[0]: _direct_record(IDS[0])}, cfg

    def exact(*args, **kwargs):
        raise requests.exceptions.Timeout("Simulated inconclusive state")

    def complement(client, store, cfg, campaign, ids, raw, **kwargs):
        return ids, raw, cfg

    monkeypatch.setattr(helpers.ctx, "_ml_listar_itens_promocao_com_raw", direct)
    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item", exact)
    monkeypatch.setattr(helpers, "_renovacao_complementar_itens_campanha_por_varredura", complement)
    statistics = {}
    items, raw, _ = helpers._renovacao_listar_itens_campanha(
        TENANT, STORE, CFG, SOURCE, "SELLER_CAMPAIGN", expected_count=1,
        validar_quantidade=False, estatisticas=statistics,
    )
    assert not items
    assert not raw
    assert statistics["inconclusivos"][IDS[0]][SOURCE]
    assert isinstance(statistics["estados"], dict)
    assert sum(statistics["estados"].get(SOURCE, {}).values()) == 0


def test_prepared_source_cannot_bypass_the_1102_versus_908_mutation_guard(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No mutation is allowed for a source count that differs from the expected count")

    monkeypatch.setattr(operations, "_renovacao_adicionar_item_campanha", forbidden)
    prepared = ([{"id": item} for item in IDS],
                {item: _participation() for item in IDS})
    with pytest.raises(HTTPException) as error:
        operations._renovacao_copiar_mlbs_campanha(
            TENANT, STORE, CFG, SOURCE, DESTINATION,
            expected_origem_count=908, verificar_destino=False, origem_preparada=prepared,
        )
    assert error.value.status_code == 409


def test_unidentified_record_without_type_is_inconclusive_instead_of_absent():
    result = _select([{"status": "pending", "price": 80, "original_price": 100}])
    assert result["situacao"] == "inconclusivo"
    assert not result.get("raw")


def test_conflicting_type_and_promotion_type_fields_are_inconclusive():
    result = _select([_participation(type="SMART", promotion_type="SELLER_CAMPAIGN")])
    assert result["situacao"] == "inconclusivo"
    assert not result.get("raw")


def test_conflicting_item_id_to_cannot_authorize_another_listing():
    result = _select([_participation(item_id_to=IDS[1])])
    assert result["situacao"] == "inconclusivo"
    assert not result.get("raw")
