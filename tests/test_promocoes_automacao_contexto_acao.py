import copy

import pytest
from fastapi import HTTPException

from backend.services import promocoes_api_jobs as jobs
from backend.services import mercadolivre_legacy_planilhas as planilhas


@pytest.fixture(autouse=True)
def configured_count_helper(monkeypatch):
    # Production binds this real peer through the compatibility facade.
    monkeypatch.setattr(planilhas, "_parse_float_flex", jobs._parse_float_flex, raising=False)
    monkeypatch.setattr(jobs, "_promo_meta_contagem", planilhas._promo_meta_contagem, raising=False)


def montar(row):
    return jobs._promo_automacao_montar_participacoes({"analises": [{
        "promo_b_id": "P-ALVO", "promo_b_type": "SMART", "data": [row],
    }]}, "Loja Teste")


def test_automacao_separa_pendencia_tecnica_de_margem_aprovada():
    row = {"MLB": "MLB123456", "Acao": "Participar", "Margem ML": "28,01%",
           "action_financeiro_exato": True, "action_tecnico_apto": False,
           "action_impedimento_tecnico": "Oferta não informada."}
    assert montar(row)["promocoes"] == []
    row.update(action_tecnico_apto=True, action_offer_id="CANDIDATE-MLB123456-1",
               action_deal_price=30, action_promotion_id="P-ALVO")
    assert len(montar(row)["promocoes"][0]["items"]) == 1
    row["action_financeiro_exato"] = False
    row["action_financeiro_estimado"] = True
    assert montar(row)["promocoes"] == []


def test_automacao_usa_acao_independente_da_ordem_dos_campos():
    row = {"MLB": "MLB123456", "Acao": "Participar", "offer_id": "OFFER-OUTRA",
           "deal_price": 12, "discount_percentage": 80,
           "action_promotion_id": "P-ALVO", "action_offer_id": "CANDIDATE-MLB123456-1",
           "action_deal_price": 30, "action_discount_percentage": 10,
           "action_financeiro_exato": True, "action_tecnico_apto": True}
    for ordered in (row, dict(reversed(list(row.items())))):
        item = montar(ordered)["promocoes"][0]["items"][0]
        assert item["offer_id"] == "CANDIDATE-MLB123456-1"
        assert item["deal_price"] == 30
        assert item["discount_percentage"] == 10
        assert item["action_promotion_id"] == "P-ALVO"


def test_automacao_nao_completa_contexto_incompleto_com_outra_oferta():
    row = {"MLB": "MLB123456", "Acao": "Participar", "offer_id": "OFFER-OUTRA",
           "deal_price": 12, "discount_percentage": 80,
           "action_promotion_id": "P-ALVO", "action_offer_id": "",
           "action_deal_price": None, "action_discount_percentage": None}
    item = montar(row)["promocoes"][0]["items"][0]
    assert item["offer_id"] == ""
    assert item["deal_price"] is None
    assert item["discount_percentage"] is None


def test_automacao_preserva_payload_legado_sem_contexto_de_acao():
    row = {"MLB": "MLB123456", "Acao": "Participar", "offer_id": "CANDIDATE-MLB123456-1",
           "deal_price": 30, "discount_percentage": 10}
    item = montar(row)["promocoes"][0]["items"][0]
    assert item["offer_id"] == row["offer_id"]
    assert item["deal_price"] == 30
    assert item["discount_percentage"] == 10



def _date_selections():
    return [{"promo_b_id": "P-ALVO", "promo_b_type": "LIGHTNING",
             "lightning_window": {"start_date": f"2026-10-{day:02}T12:00:00-03:00",
                                  "finish_date": f"2026-10-{day:02}T18:00:00-03:00"}}
            for day in (9, 10, 12)]


def test_automation_config_roundtrip_preserves_three_windows_and_detached_metadata():
    incoming = {"enabled": True, "loja": "Loja Teste", "promocao_a_id": "PROMO-A",
                "promocoes_b_meta": _date_selections()}
    before = copy.deepcopy(incoming)
    config = jobs._promo_automacao_sanitizar(incoming)
    assert len(config["promocoes_b_meta"]) == 3
    assert len({meta["selection_id"] for meta in config["promocoes_b_meta"]}) == 3
    assert {meta["promo_b_id"] for meta in config["promocoes_b_meta"]} == {"P-ALVO"}
    assert all("lightning_window" in meta for meta in config["promocoes_b_meta"])
    public = jobs._promo_automacao_public_payload("synthetic-client", config)
    assert public["config"]["promocoes_b_meta"] == config["promocoes_b_meta"]
    assert jobs._promo_automacao_sanitizar(public["config"])["promocoes_b_meta"] == config["promocoes_b_meta"]
    assert incoming == before
    config["promocoes_b_meta"][0]["lightning_window"]["start_date"] = "changed"
    assert incoming == before


@pytest.mark.parametrize("kind,window,selection_id", [
    ("LIGHTNING", {"start_date": "not-a-date"}, None),
    ("LIGHTNING", {"finish_date": "2026-99-99"}, None),
    ("LIGHTNING", {}, None),
    ("LIGHTNING", [], None),
    ("SMART", {"start_date": "2026-10-09T12:00:00-03:00"}, None),
    ("LIGHTNING", {"start_date": "2026-10-09T12:00:00-03:00"}, "client-forged-key"),
])
def test_automation_config_rejects_invalid_window_type_and_forged_identity(kind, window, selection_id):
    meta = {"promo_b_id": "P-ALVO", "promo_b_type": kind, "lightning_window": window}
    if selection_id is not None:
        meta["selection_id"] = selection_id
    with pytest.raises(HTTPException) as error:
        jobs._promo_automacao_sanitizar({"promocoes_b_meta": [meta]})
    assert error.value.status_code == 400


@pytest.mark.parametrize("kind", ["LIGHTNING", "SMART"])
def test_automation_config_legacy_without_window_does_not_add_date_filter(kind):
    config = jobs._promo_automacao_sanitizar({"promocoes_b_meta": [{"promo_b_id": "P-ALVO", "promo_b_type": kind}]})
    meta = config["promocoes_b_meta"][0]
    assert meta["promo_b_id"] == "P-ALVO"
    assert "lightning_window" not in meta
    assert "selection_id" not in meta


@pytest.mark.parametrize("window", [{"undated": True}, {"finish_date": "2026-10-09T18:00:00-03:00"}])
def test_automation_config_preserves_undated_and_partial_groups(window):
    first = jobs._promo_automacao_sanitizar({"promocoes_b_meta": [
        {"promo_b_id": "P-ALVO", "promo_b_type": "LIGHTNING", "lightning_window": window}]})
    assert jobs._promo_automacao_sanitizar(first)["promocoes_b_meta"] == first["promocoes_b_meta"]


def test_automation_participation_keeps_groups_same_campaign_separate_by_window():
    metas = jobs._promo_automacao_sanitizar({"promocoes_b_meta": _date_selections()})["promocoes_b_meta"]
    analyses = [{**meta, "data": [{"MLB": "MLB123456", "Acao": "Participar",
                 "analysis_client_ref": f"analysis:window-{index}", "action_promotion_id": "P-ALVO",
                 "action_offer_id": f"offer-{index}", "action_deal_price": 30,
                 "lightning_offer_key": f"window-{index}",
                 "lightning_start_date": meta["lightning_window"]["start_date"],
                 "lightning_finish_date": meta["lightning_window"]["finish_date"]}]}
                for index, meta in enumerate(metas)]
    payload = jobs._promo_automacao_montar_participacoes({"analises": analyses}, "Loja Teste", "a" * 32)
    groups = payload["promocoes"]
    assert len(groups) == 3
    assert [group["selection_id"] for group in groups] == [meta["selection_id"] for meta in metas]
    assert [group["lightning_window"] for group in groups] == [meta["lightning_window"] for meta in metas]
    assert {group["promotion_id"] for group in groups} == {"P-ALVO"}
    assert [group["items"][0]["client_ref"] for group in groups] == [f"analysis:window-{index}" for index in range(3)]
