"""Renovacao completeness checks, with all remote boundaries simulated."""

import copy
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

from backend.services import renovacao_promocoes as operations
from backend.services import renovacao_promocoes_helpers as helpers


TENANT = "tenant-completude-test"
STORE = "loja-completude-test"
SOURCE = "C-MLB998000001"
DESTINATION = "C-MLB998000002"
OTHER_CAMPAIGN = "C-MLB998000003"
CFG = {"user_id": 123, "site_id": "MLB"}
IDS = [f"MLB{998100000 + index}" for index in range(908)]


def _entry(item_id, status="started", **changes):
    return {
        "id": item_id, "item_id": item_id, "campaign_id": SOURCE,
        "promotion_type": "SELLER_CAMPAIGN", "status": status,
        "price": 80, "original_price": 100, "discount_percentage": 20,
        **changes,
    }


def _snapshot(item_ids):
    return [{"id": item} for item in item_ids], {item: _entry(item) for item in item_ids}


def _campaigns(include_destination=False):
    result = [{
        "id": SOURCE, "name": "Promo Outubro", "promotion_type": "SELLER_CAMPAIGN",
        "active_count": 908, "programmed_count": 0, "total_count": 908,
        "start_date": "2030-10-01T00:00:00", "finish_date": "2030-11-01T00:00:00",
    }]
    if include_destination:
        result.append({
            "id": DESTINATION, "name": "Promo Novembro", "promotion_type": "SELLER_CAMPAIGN",
            "active_count": 0, "programmed_count": 61, "total_count": 61,
            "start_date": "2030-11-01T00:00:00", "finish_date": "2030-11-15T23:59:59",
        })
    return result


@pytest.fixture(autouse=True)
def offline_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected network or mutation outside the simulated boundary")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(helpers.ctx, "_ml_api_request", forbidden)
    monkeypatch.setattr(helpers.ctx, "_parse_float_flex",
                        lambda value: None if value is None else float(value))
    monkeypatch.setattr(helpers.ctx, "_ml_extrair_preco_promocao_raw",
                        lambda raw, **kwargs: (raw.get("price"), raw.get("discount_percentage")))
    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item",
                        lambda client, store, cfg, item, **kwargs: ([], cfg))
    monkeypatch.setattr(helpers.ctx, "_ml_extrair_ids_promocoes_item",
                        lambda records: {str(entry.get("campaign_id") or entry.get("id") or "").lower()
                                         for entry in records if isinstance(entry, dict)})
    monkeypatch.setattr(helpers.ctx, "_ml_encontrar_promocao_raw_item",
                        lambda records, campaign: next((entry for entry in records
                            if isinstance(entry, dict) and
                            str(entry.get("campaign_id") or entry.get("id") or "") == campaign), {}))
    monkeypatch.setattr(helpers.ctx, "_ml_obter_item_promocao_raw",
                        lambda client, store, cfg, *args: ({}, cfg))
    monkeypatch.setattr(helpers.ctx, "_ml_buscar_itens_batch",
                        lambda client, store, cfg, items: ([{
                            "id": item, "price": 80, "original_price": 100, "currency_id": "BRL",
                        } for item in items], cfg))
    monkeypatch.setattr(helpers.ctx, "_cache_invalidar_loja", lambda *args: None)


def _patch_direct(monkeypatch, item_ids, raw=None):
    entries, default_raw = _snapshot(item_ids)
    records = raw if raw is not None else default_raw

    def direct(client, store, cfg, campaign, **kwargs):
        assert (client, store) == (TENANT, STORE)
        assert campaign == SOURCE
        return copy.deepcopy(entries), copy.deepcopy(records), cfg

    monkeypatch.setattr(helpers.ctx, "_ml_listar_itens_promocao_com_raw", direct)


def _patch_incomplete_complement(monkeypatch):
    calls = []

    def complement(client, store, cfg, campaign, ids, raw, **kwargs):
        calls.append(list(ids))
        return list(ids), dict(raw), cfg

    monkeypatch.setattr(helpers, "_renovacao_complementar_itens_campanha_por_varredura", complement)
    return calls


def _assert_incomplete(error, expected, found):
    assert error.value.status_code == 409
    assert str(expected) in str(error.value.detail)
    assert str(found) in str(error.value.detail)


def test_candidate_record_is_not_a_campaign_participant():
    assert not helpers._renovacao_participante_campanha(
        _entry(IDS[0], "candidate"), SOURCE, IDS[0], contexto_campanha=True
    )


def test_exact_item_pending_record_without_offer_is_a_participant():
    raw = {"id": SOURCE, "type": "SELLER_CAMPAIGN", "status": "pending",
           "price": 80, "original_price": 100}
    assert helpers._renovacao_participante_campanha(raw, SOURCE, IDS[0])


def test_participant_gate_rejects_explicit_campaign_and_type_conflicts():
    assert not helpers._renovacao_participante_campanha(
        _entry(IDS[0], campaign_id=OTHER_CAMPAIGN), SOURCE, IDS[0], contexto_campanha=True
    )
    assert not helpers._renovacao_participante_campanha(
        _entry(IDS[0], promotion_type="SMART"), SOURCE, IDS[0], contexto_campanha=True
    )


def test_partial_61_of_908_still_incomplete_after_complement_raises_409(monkeypatch):
    _patch_direct(monkeypatch, IDS[:61])
    scans = _patch_incomplete_complement(monkeypatch)
    with pytest.raises(HTTPException) as error:
        helpers._renovacao_listar_itens_campanha(
            TENANT, STORE, CFG, SOURCE, "SELLER_CAMPAIGN", expected_count=908
        )
    _assert_incomplete(error, 908, 61)
    assert len(scans) == 1
    assert set(scans[0]) == set(IDS[:61])


def test_partial_page_is_completed_before_any_item_mutation(monkeypatch):
    _patch_direct(monkeypatch, IDS[:61])
    state = {"completed": False, "mutations": []}

    def complement(client, store, cfg, campaign, ids, raw, **kwargs):
        assert len(ids) == 61
        assert not state["mutations"]
        state["completed"] = True
        _, complete_raw = _snapshot(IDS)
        return list(IDS), complete_raw, cfg

    def apply(client, store, cfg, item, *args, **kwargs):
        assert state["completed"]
        state["mutations"].append(item)
        return True, "", cfg

    monkeypatch.setattr(helpers, "_renovacao_complementar_itens_campanha_por_varredura", complement)
    monkeypatch.setattr(operations, "_renovacao_adicionar_item_campanha", apply)
    result, _ = operations._renovacao_copiar_mlbs_campanha(
        TENANT, STORE, CFG, SOURCE, DESTINATION,
        expected_origem_count=908, verificar_destino=False,
    )
    assert result["total_origem"] == 908
    assert result["incluidos"] == 908
    assert result["falhas_total"] == 0
    assert set(state["mutations"]) == set(IDS)


def test_candidates_do_not_make_a_partial_source_look_complete(monkeypatch):
    raw = {item: _entry(item, "started" if index < 61 else "candidate")
           for index, item in enumerate(IDS)}
    _patch_direct(monkeypatch, IDS, raw)
    scans = _patch_incomplete_complement(monkeypatch)
    with pytest.raises(HTTPException) as error:
        helpers._renovacao_listar_itens_campanha(
            TENANT, STORE, CFG, SOURCE, "SELLER_CAMPAIGN", expected_count=908
        )
    _assert_incomplete(error, 908, 61)
    assert set(scans[0]) == set(IDS[:61])


def test_missing_direct_raw_can_be_confirmed_pending_by_fresh_exact_item_get(monkeypatch):
    _patch_direct(monkeypatch, IDS[:1], {})
    lookups = []

    def exact_item(client, store, cfg, item, **kwargs):
        assert (client, store, item) == (TENANT, STORE, IDS[0])
        assert kwargs.get("force_refresh") is True
        lookups.append(item)
        return [{"id": SOURCE, "type": "SELLER_CAMPAIGN", "status": "pending",
                 "price": 80, "original_price": 100, "discount_percentage": 20}], cfg

    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item", exact_item)
    items, raw, _ = helpers._renovacao_listar_itens_campanha(
        TENANT, STORE, CFG, SOURCE, "SELLER_CAMPAIGN", expected_count=1
    )
    assert items == [{"id": IDS[0]}]
    assert raw[IDS[0]]["status"] == "pending"
    assert lookups


def test_complement_uses_fresh_item_state_and_filters_candidates(monkeypatch):
    monkeypatch.setattr(helpers.ctx, "_ml_listar_ids_anuncios_ativos",
                        lambda client, store, cfg, seller, **kwargs: (IDS[:2], cfg))
    lookups = []

    def exact_item(client, store, cfg, item, **kwargs):
        assert kwargs.get("force_refresh") is True
        lookups.append(item)
        return [{"id": SOURCE, "type": "SELLER_CAMPAIGN",
                 "status": "pending" if item == IDS[0] else "candidate",
                 "price": 80, "original_price": 100}], cfg

    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item", exact_item)
    ids, raw, _ = helpers._renovacao_complementar_itens_campanha_por_varredura(
        TENANT, STORE, CFG, SOURCE, [], {}
    )
    assert ids == IDS[:1]
    assert raw[IDS[0]]["status"] == "pending"
    assert set(lookups) == set(IDS[:2])


def test_fresh_record_from_another_campaign_cannot_complete_the_source(monkeypatch):
    _patch_direct(monkeypatch, IDS[:1], {})
    _patch_incomplete_complement(monkeypatch)
    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item",
                        lambda client, store, cfg, item, **kwargs: ([{
                            "id": OTHER_CAMPAIGN, "type": "SELLER_CAMPAIGN", "status": "pending",
                            "price": 80, "original_price": 100,
                        }], cfg))
    with pytest.raises(HTTPException) as error:
        helpers._renovacao_listar_itens_campanha(
            TENANT, STORE, CFG, SOURCE, "SELLER_CAMPAIGN", expected_count=1
        )
    _assert_incomplete(error, 1, 0)


def test_monthly_incomplete_source_blocks_campaign_creation_and_all_item_mutations(monkeypatch):
    _patch_direct(monkeypatch, IDS[:61])
    _patch_incomplete_complement(monkeypatch)
    monkeypatch.setattr(operations, "_renovacao_ml_listar_campanhas_usuario",
                        lambda client, store: (_campaigns(), CFG))

    def forbidden_mutation(*args, **kwargs):
        pytest.fail("Incomplete source must be detected before campaign or participation mutation")

    monkeypatch.setattr(operations, "_renovacao_criar_campanha_ml", forbidden_mutation)
    monkeypatch.setattr(operations, "_renovacao_adicionar_item_campanha", forbidden_mutation)
    with pytest.raises(HTTPException) as error:
        operations._renovacao_criar_ou_completar_proximo_mes(
            TENANT, STORE, SOURCE, "Promo Novembro"
        )
    _assert_incomplete(error, 908, 61)


def test_monthly_passes_expected_count_and_reuses_complete_preflight(monkeypatch):
    source_calls = []
    items, raw = _snapshot(IDS)

    def listing(client, store, cfg, campaign, *args, **kwargs):
        assert campaign == SOURCE
        assert kwargs.get("expected_count") == 908
        source_calls.append(campaign)
        return copy.deepcopy(items), copy.deepcopy(raw), cfg

    monkeypatch.setattr(operations, "_renovacao_listar_itens_campanha", listing)
    monkeypatch.setattr(operations, "_renovacao_ml_listar_campanhas_usuario",
                        lambda client, store: (_campaigns(), CFG))
    monkeypatch.setattr(operations, "_renovacao_criar_campanha_ml",
                        lambda client, store, cfg, *args, **kwargs:
                        (DESTINATION, {"name": "Promo Novembro"}, cfg))
    monkeypatch.setattr(operations, "_renovacao_adicionar_item_campanha",
                        lambda client, store, cfg, *args, **kwargs: (True, "", cfg))
    result = operations._renovacao_criar_ou_completar_proximo_mes(
        TENANT, STORE, SOURCE, "Promo Novembro"
    )
    assert result["total_origem"] == result["incluidos"] == 908
    assert source_calls == [SOURCE]


def test_synchronization_passes_expected_source_and_destination_counts(monkeypatch):
    calls = []

    def copy_campaign(client, store, cfg, source, destination, *args, **kwargs):
        calls.append((source, destination, kwargs))
        return {"total_origem": 908, "incluidos": 847, "atualizados": 0,
                "mlbs_ja_presentes": 61, "falhas": [], "falhas_total": 0}, cfg

    monkeypatch.setattr(operations, "_renovacao_ml_listar_campanhas_usuario",
                        lambda client, store: (_campaigns(include_destination=True), CFG))
    monkeypatch.setattr(operations, "_renovacao_copiar_mlbs_campanha", copy_campaign)
    result = operations._renovacao_sincronizar_promocao_existente(
        TENANT, STORE, SOURCE, DESTINATION
    )
    assert result["success"]
    assert len(calls) == 1
    assert calls[0][:2] == (SOURCE, DESTINATION)
    assert calls[0][2]["expected_origem_count"] == 908
    assert calls[0][2]["expected_destino_count"] == 61


def test_expected_count_covers_active_programmed_and_total_fallback():
    assert helpers._renovacao_contagem_esperada({"active_count": 900, "programmed_count": 8}) == 908
    assert helpers._renovacao_contagem_esperada({"active_count": 0, "scheduled_count": 908}) == 908
    assert helpers._renovacao_contagem_esperada({"total_count": 908}) == 908


def test_complete_908_source_keeps_31_item_failures_separate_from_missing_collection(monkeypatch):
    items, raw = _snapshot(IDS)
    failed = set(IDS[:31])

    def listing(client, store, cfg, campaign, *args, **kwargs):
        return (copy.deepcopy(items), copy.deepcopy(raw), cfg) if campaign == SOURCE else ([], {}, cfg)

    def apply(client, store, cfg, item, *args, **kwargs):
        return (False, "Simulated participation failure", cfg) if item in failed else (True, "", cfg)

    monkeypatch.setattr(operations, "_renovacao_listar_itens_campanha", listing)
    monkeypatch.setattr(operations, "_renovacao_adicionar_item_campanha", apply)
    result, _ = operations._renovacao_copiar_mlbs_campanha(
        TENANT, STORE, CFG, SOURCE, DESTINATION,
        expected_origem_count=908, verificar_destino=False,
    )
    assert result["total_origem"] == 908
    assert result["incluidos"] == 877
    assert result["falhas_total"] == 31
    assert {entry["item_id"] for entry in result["falhas"]} == failed
    assert {entry["erro"] for entry in result["falhas"]} == {"Simulated participation failure"}


@pytest.mark.parametrize("status_fields", [
    {"status_item": "active"},
    {"statusItem": "pending"},
    {"status": {"id": "pending"}},
])
def test_participant_gate_accepts_explicit_status_aliases(status_fields):
    raw = _entry(IDS[0])
    raw.pop("status")
    raw.update(status_fields)
    assert helpers._renovacao_participante_campanha(
        raw, SOURCE, IDS[0], contexto_campanha=True
    )


def test_pending_status_alias_is_collected_without_an_extra_item_get(monkeypatch):
    raw = _entry(IDS[0])
    raw.pop("status")
    raw["statusItem"] = "pending"
    _patch_direct(monkeypatch, IDS[:1], {IDS[0]: raw})

    def unnecessary_lookup(*args, **kwargs):
        pytest.fail("An explicit pending status alias already confirms participation")

    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item", unnecessary_lookup)
    items, collected, _ = helpers._renovacao_listar_itens_campanha(
        TENANT, STORE, CFG, SOURCE, "SELLER_CAMPAIGN", expected_count=1
    )
    assert items == [{"id": IDS[0]}]
    assert collected[IDS[0]]["statusItem"] == "pending"


def test_partly_unknown_metadata_refreshes_both_counts_before_accepting_908_items(monkeypatch):
    metadata = {"active_count": None, "programmed_count": 61, "total_count": 908}
    expected = helpers._renovacao_contagem_esperada(metadata)
    assert expected is None
    queries = []

    def count(client, store, cfg, campaign, kind, status):
        assert (client, store, campaign, kind) == (TENANT, STORE, SOURCE, "SELLER_CAMPAIGN")
        queries.append(status)
        return (847 if status == "started" else 61), cfg

    items, _ = _snapshot(IDS)
    raw = {item: _entry(item, "started" if index < 847 else "pending")
           for index, item in enumerate(IDS)}

    def direct(client, store, cfg, campaign, **kwargs):
        assert queries == ["started", "pending"]
        return copy.deepcopy(items), copy.deepcopy(raw), cfg

    monkeypatch.setattr(helpers.ctx, "_ml_contar_itens_promocao_status", count)
    monkeypatch.setattr(helpers.ctx, "_ml_listar_itens_promocao_com_raw", direct)
    collected, _, _ = helpers._renovacao_listar_itens_campanha(
        TENANT, STORE, CFG, SOURCE, "SELLER_CAMPAIGN", expected_count=expected
    )
    assert len(collected) == 908
    assert queries == ["started", "pending"]


def test_campaign_metadata_queries_missing_programmed_count_even_when_campaign_is_started(monkeypatch):
    source = {"id": SOURCE, "name": "Promo Outubro", "type": "SELLER_CAMPAIGN",
              "status": "started", "total_count": 908}
    queries = []

    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store, method) == (TENANT, STORE, "GET")
        return SimpleNamespace(status_code=200, json=lambda: {"results": [copy.deepcopy(source)]}), cfg

    def extract(raw, keys):
        return next((raw[key] for key in keys if raw.get(key) is not None), None)

    def count(client, store, cfg, campaign, kind, status):
        assert (client, store, campaign, kind) == (TENANT, STORE, SOURCE, "SELLER_CAMPAIGN")
        queries.append(status)
        return (61 if status == "started" else 847), cfg

    monkeypatch.setattr(helpers.ctx, "_agent_service_only", lambda: False)
    monkeypatch.setattr(helpers.ctx, "_obter_cfg_ml", lambda client, store: CFG)
    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)
    monkeypatch.setattr(helpers.ctx, "_ml_extrair_contagem_campanha", extract)
    monkeypatch.setattr(helpers.ctx, "_ml_contar_itens_promocao_status", count)
    campaigns, _ = helpers._renovacao_ml_listar_campanhas_usuario(TENANT, STORE)
    assert len(campaigns) == 1
    assert campaigns[0]["active_count"] == 61
    assert campaigns[0]["programmed_count"] == 847
    assert helpers._renovacao_contagem_esperada(campaigns[0]) == 908
    assert queries == ["started", "pending"]
