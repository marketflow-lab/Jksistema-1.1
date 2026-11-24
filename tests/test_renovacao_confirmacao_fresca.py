"""Offline regressions for fresh membership checks and bounded offer recovery."""

import copy
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

from backend.schemas.renovacao import RenovacaoCampanhaSincronizarRequest
from backend.services import renovacao_promocoes as promotions
from backend.services import renovacao_promocoes_helpers as helpers


TENANT = "tenant-renovacao-fresh-test"
STORE = "loja-renovacao-fresh-test"
SOURCE = "C-MLB999300001"
DESTINATION = "C-MLB999300002"
ITEM = "MLB999300101"
OTHER_ITEM = "MLB999300102"
CFG = {"user_id": "123", "site_id": "MLB", "_store_id_context": "store-fresh-test"}


def response(status, body):
    return SimpleNamespace(status_code=status, json=lambda: copy.deepcopy(body))


def participation(campaign=SOURCE, price=80, base=100, status="started", item=ITEM, **changes):
    return {
        "id": campaign, "item_id": item, "type": "SELLER_CAMPAIGN",
        "status": status, "price": price, "original_price": base,
        "discount_percentage": 20, **changes,
    }


def member(raw):
    return {"situacao": "participante", "raw": copy.deepcopy(raw)}


def absent():
    return {"situacao": "ausente", "raw": {}}


def inconclusive():
    return {"situacao": "inconclusivo", "erro": "Consulta atual nao confirmada"}


def install_lookup(monkeypatch, lookup):
    def with_config(client, store, cfg, item, campaigns):
        return lookup(client, store, cfg, item, campaigns), cfg

    monkeypatch.setattr(helpers, "_renovacao_consultar_participacoes", with_config, raising=False)
    monkeypatch.setattr(promotions, "_renovacao_consultar_participacoes", with_config, raising=False)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected real or unconfigured network request")

    def parse(value):
        if value is None or value == "":
            return None
        return float(str(value).replace(",", "."))

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(helpers.ctx, "_ml_api_request", forbidden)
    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item", forbidden)
    monkeypatch.setattr(helpers.ctx, "_parse_float_flex", parse)
    monkeypatch.setattr(
        helpers.ctx, "_ml_parse_error_detail",
        lambda result, fallback: result.json().get("message", fallback),
    )
    monkeypatch.setattr(
        helpers.ctx, "_ml_extrair_preco_promocao_raw",
        lambda raw, **kwargs: (raw.get("price"), raw.get("discount_percentage")),
    )
    monkeypatch.setattr(helpers.ctx, "_ml_obter_item_promocao_raw", lambda client, store, cfg, *args: ({}, cfg))


def test_lookup_is_strict_fresh_and_does_not_confuse_candidates_with_members(monkeypatch):
    calls = []

    def current(client, store, cfg, item, **kwargs):
        assert (client, store, cfg, item) == (TENANT, STORE, CFG, ITEM)
        calls.append(kwargs)
        return [participation(), participation(DESTINATION, price=0, status="candidate")], cfg

    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item", current)
    result, cfg = helpers._renovacao_consultar_participacoes(TENANT, STORE, dict(CFG), ITEM, [SOURCE, DESTINATION])

    assert calls and all(call.get("strict") is True and call.get("force_refresh") is True for call in calls)
    assert result[SOURCE]["situacao"] == "participante"
    assert result[DESTINATION]["situacao"] == "ausente"
    assert cfg == CFG


def test_lookup_failure_is_inconclusive_instead_of_absence(monkeypatch):
    def unavailable(*args, **kwargs):
        raise HTTPException(503, "Servico temporariamente indisponivel")

    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item", unavailable)
    result, _ = helpers._renovacao_consultar_participacoes(TENANT, STORE, dict(CFG), ITEM, [SOURCE, DESTINATION])
    assert result[SOURCE]["situacao"] == "inconclusivo"
    assert result[DESTINATION]["situacao"] == "inconclusivo"


@pytest.fixture
def consulte_com_respostas(monkeypatch):
    from backend.services import mercadolivre_legacy_promocoes as leitor

    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item", leitor._ml_obter_promocoes_item)
    monkeypatch.setattr(leitor, "ML_ITEM_PROMOTIONS_CACHE", {}, raising=False)
    monkeypatch.setattr(leitor, "_cache_set", lambda *args: None, raising=False)
    monkeypatch.setattr(leitor, "_promo_remover_contexto_consultado", lambda item: item, raising=False)

    def executar(*respostas):
        fila = iter(respostas)
        esperas = []
        chamadas = []
        def get(client, loja, cfg, metodo, url, **opcoes):
            assert (client, loja, metodo, cfg["user_id"]) == (TENANT, STORE, "GET", CFG["user_id"])
            chamadas.append(url)
            entrada = next(fila, None)
            if entrada is None:
                pytest.fail("O leitor tentou consultar novamente um resultado definitivo")
            if isinstance(entrada, Exception):
                raise entrada
            return entrada, cfg

        monkeypatch.setattr(helpers.ctx, "_ml_api_request", get)
        monkeypatch.setattr(helpers.time, "sleep", esperas.append)
        estado, _ = helpers._renovacao_consultar_participacoes(TENANT, STORE, dict(CFG), ITEM, [SOURCE])
        assert chamadas == [f"https://api.mercadolibre.com/seller-promotions/items/{ITEM}"] * len(respostas)
        return estado[SOURCE], esperas

    return executar


@pytest.mark.parametrize("primeiro", [response(429, {}), HTTPException(429, headers={"retry-after": "5"})])
def test_participacao_respeita_retry_after_e_recupera_timeout(consulte_com_respostas, primeiro):
    if not isinstance(primeiro, HTTPException):
        primeiro.headers = {"Retry-After": "5"}
    estado, esperas = consulte_com_respostas(primeiro, requests.ReadTimeout(), response(200, [participation()]))
    assert estado["situacao"] == "participante"
    assert len(esperas) == 2 and esperas[0] >= 5


def test_participacao_esgota_falha_temporaria_sem_inferir_ausencia(consulte_com_respostas):
    estado, esperas = consulte_com_respostas(response(503, {}), requests.ConnectionError(), response(502, {}))
    assert len(esperas) == 2
    assert estado == {"situacao": "inconclusivo", "erro": "Consulta de participacoes recebeu HTTP 502"}


@pytest.mark.parametrize(("falha", "tentativas", "motivo"), [
    (requests.ReadTimeout("detalhes privados"), 3, "Tempo esgotado"),
    (requests.ConnectionError("detalhes privados"), 3, "Falha de conexao"),
    (requests.exceptions.SSLError("detalhes privados"), 1, "Falha na conexao segura (TLS)"),
])
def test_participacao_distingue_falhas_de_transporte(consulte_com_respostas, falha, tentativas, motivo):
    estado, esperas = consulte_com_respostas(*([falha] * tentativas))
    assert len(esperas) == tentativas - 1
    assert estado["situacao"] == "inconclusivo"
    assert estado["erro"].startswith(motivo) and "detalhes privados" not in estado["erro"]


def test_resposta_200_invalida_nao_e_repetida_como_um_502_remoto(consulte_com_respostas):
    estado, esperas = consulte_com_respostas(response(200, {"invalid": True}))
    assert estado == {"situacao": "inconclusivo", "erro": "Resposta de participacoes invalida"}
    assert esperas == []


@pytest.mark.parametrize(("resposta", "situacao"), [
    (response(200, [participation(item=OTHER_ITEM)]), "inconclusivo"),
    (response(200, []), "ausente"),
    (response(404, {"message": "no offers found"}), "ausente"),
    (response(403, {}), "inconclusivo"),
    (HTTPException(429, headers={"Retry-After": "120"}), "inconclusivo"),
])
def test_participacao_nao_repete_resposta_definitiva_ou_antes_do_prazo(consulte_com_respostas, resposta, situacao):
    estado, esperas = consulte_com_respostas(resposta)
    assert estado["situacao"] == situacao
    assert esperas == []


@pytest.mark.parametrize("change", [
    {"status": None, "status_item": "active"},
    {"status": None, "state": None},
    {"item_id": OTHER_ITEM},
])
def test_lookup_rejects_ambiguous_membership_or_conflicting_item(monkeypatch, change):
    monkeypatch.setattr(
        helpers.ctx, "_ml_obter_promocoes_item",
        lambda client, store, cfg, item, **kwargs: ([participation(**change)], cfg),
    )
    result, _ = helpers._renovacao_consultar_participacoes(TENANT, STORE, dict(CFG), ITEM, [SOURCE])
    assert result[SOURCE]["situacao"] == "inconclusivo"


def test_lookup_does_not_confirm_conflicting_duplicate_offers(monkeypatch):
    monkeypatch.setattr(
        helpers.ctx, "_ml_obter_promocoes_item",
        lambda client, store, cfg, item, **kwargs: (
            [participation(DESTINATION, price=80), participation(DESTINATION, price=90)], cfg,
        ),
    )
    result, _ = helpers._renovacao_consultar_participacoes(TENANT, STORE, dict(CFG), ITEM, [DESTINATION])
    assert result[DESTINATION]["situacao"] == "inconclusivo"


@pytest.mark.parametrize("response_base", [100, 200])
def test_post_2xx_with_no_fresh_membership_never_assumes_offer_exists(monkeypatch, response_base):
    calls = []
    lookups = []

    def lookup(client, store, cfg, item, campaigns):
        assert (client, store, item) == (TENANT, STORE, ITEM)
        lookups.append(tuple(campaigns))
        return {SOURCE: member(participation()), DESTINATION: absent()}

    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store) == (TENANT, STORE)
        assert url.endswith(f"/items/{ITEM}")
        calls.append((method, kwargs["json"]))
        return response(201, {"price": 80, "original_price": response_base}), cfg

    install_lookup(monkeypatch, lookup)
    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)
    ok, _, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, dict(CFG), ITEM, DESTINATION, 20, 100, 80, campanha_origem_id=SOURCE,
    )

    assert ok is False
    assert lookups
    assert [method for method, _ in calls] == ["POST"]


def test_put_offer_missing_recovers_once_only_after_confirmed_absence(monkeypatch):
    calls = []
    state = {"phase": "existing"}
    lookup_phases = []

    def lookup(client, store, cfg, item, campaigns):
        lookup_phases.append(state["phase"])
        target = absent() if state["phase"] == "missing" else member(
            participation(DESTINATION, price=80 if state["phase"] == "confirmed" else 90, status="pending")
        )
        return {SOURCE: member(participation()), DESTINATION: target}

    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store) == (TENANT, STORE)
        assert url.endswith(f"/items/{ITEM}")
        calls.append(method)
        if method == "PUT":
            state["phase"] = "missing"
            return response(400, {"message": "No offers found for item"}), cfg
        assert method == "POST"
        assert state["phase"] == "missing"
        assert kwargs["json"] == {
            "promotion_id": DESTINATION, "promotion_type": "SELLER_CAMPAIGN", "deal_price": 80,
        }
        state["phase"] = "confirmed"
        return response(201, {"price": 80, "original_price": 100}), cfg

    install_lookup(monkeypatch, lookup)
    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)
    ok, error, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, dict(CFG), ITEM, DESTINATION, 20, 100, 80,
        metodo="PUT", campanha_origem_id=SOURCE,
    )

    assert ok is True, error
    assert calls == ["PUT", "POST"]
    assert "missing" in lookup_phases and "confirmed" in lookup_phases


def test_missing_offer_error_is_resolved_by_fresh_matching_participation_without_post(monkeypatch):
    calls = []
    fresh_prices = []

    def lookup(client, store, cfg, item, campaigns):
        price = 80 if calls else 90
        fresh_prices.append(price)
        return {
            SOURCE: member(participation()),
            DESTINATION: member(participation(DESTINATION, price=price, status="pending")),
        }

    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store) == (TENANT, STORE)
        assert url.endswith(f"/items/{ITEM}")
        calls.append(method)
        return response(400, {"message": "No offers found for item"}), cfg

    install_lookup(monkeypatch, lookup)
    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)
    ok, error, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, dict(CFG), ITEM, DESTINATION, 20, 100, 80,
        metodo="PUT", campanha_origem_id=SOURCE,
    )

    assert ok is True, error
    assert calls == ["PUT"]
    assert fresh_prices == [90, 80]


@pytest.mark.parametrize("after_error", ["inconclusive", "source_gone"])
def test_missing_offer_never_triggers_post_when_lookup_is_unsafe(monkeypatch, after_error):
    calls = []

    def lookup(client, store, cfg, item, campaigns):
        if not calls:
            return {SOURCE: member(participation()), DESTINATION: member(participation(DESTINATION, price=90))}
        if after_error == "source_gone":
            return {SOURCE: absent(), DESTINATION: absent()}
        return {SOURCE: member(participation()), DESTINATION: inconclusive()}

    def request(client, store, cfg, method, url, **kwargs):
        calls.append(method)
        return response(400, {"message": "No offers found for item"}), cfg

    install_lookup(monkeypatch, lookup)
    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)
    ok, _, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, dict(CFG), ITEM, DESTINATION, 20, 100, 80,
        metodo="PUT", campanha_origem_id=SOURCE,
    )

    assert ok is False
    assert calls == ["PUT"]


def test_item_no_longer_in_source_is_never_mutated(monkeypatch):
    install_lookup(
        monkeypatch,
        lambda client, store, cfg, item, campaigns: {SOURCE: absent(), DESTINATION: absent()},
    )
    ok, _, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, dict(CFG), ITEM, DESTINATION, 20, 100, 80, campanha_origem_id=SOURCE,
    )
    assert ok is False


@pytest.mark.parametrize("status,message", [
    (412, "No offers found for item"),
    (400, "ERROR_CREDIBILITY_DISCOUNTED_PRICE"),
])
def test_other_put_errors_do_not_switch_to_post(monkeypatch, status, message):
    calls = []
    install_lookup(
        monkeypatch,
        lambda client, store, cfg, item, campaigns: {
            SOURCE: member(participation()), DESTINATION: member(participation(DESTINATION, price=90)),
        },
    )

    def request(client, store, cfg, method, url, **kwargs):
        calls.append(method)
        return response(status, {"message": message}), cfg

    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)
    ok, _, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, dict(CFG), ITEM, DESTINATION, 20, 100, 80,
        metodo="PUT", campanha_origem_id=SOURCE,
    )
    assert ok is False
    assert calls == ["PUT"]


@pytest.mark.parametrize("all_confirmed", [False, True])
def test_read_only_conference_never_changes_promotions(monkeypatch, all_confirmed):
    source_items = [{"id": ITEM}, {"id": OTHER_ITEM}]
    source_raw = {item["id"]: participation(item=item["id"]) for item in source_items}
    destination_items = source_items if all_confirmed else source_items[:1]
    destination_raw = {
        item["id"]: participation(DESTINATION, status="pending", item=item["id"])
        for item in destination_items
    }
    audited = []

    def list_campaign(client, store, cfg, campaign, kind, **kwargs):
        assert (client, store) == (TENANT, STORE)
        if campaign == SOURCE:
            return source_items, source_raw, cfg
        assert campaign == DESTINATION
        return destination_items, destination_raw, cfg

    def lookup(client, store, cfg, item, campaigns):
        audited.append(item)
        target = member(destination_raw[item]) if item in destination_raw else absent()
        return {SOURCE: member(source_raw[item]), DESTINATION: target}

    monkeypatch.setattr(promotions, "_renovacao_listar_itens_campanha", list_campaign)
    monkeypatch.setattr(
        helpers.ctx, "_ml_buscar_itens_batch",
        lambda client, store, cfg, items: ([{"id": item, "price": 80, "original_price": 100} for item in items], cfg),
    )
    monkeypatch.setattr(promotions, "_renovacao_preco_base_atual", lambda client, store, cfg, *args: (100, cfg))
    install_lookup(monkeypatch, lookup)
    result, _ = promotions._renovacao_copiar_mlbs_campanha(
        TENANT, STORE, dict(CFG), SOURCE, DESTINATION,
        expected_origem_count=2, expected_destino_count=len(destination_items), somente_conferir=True,
    )

    assert result["success"] is all_confirmed
    assert set(audited) == {ITEM, OTHER_ITEM}


def test_sync_schema_defaults_to_mutation_and_accepts_explicit_conference():
    original = RenovacaoCampanhaSincronizarRequest(
        loja=STORE, campanha_origem_id=SOURCE, campanha_destino_id=DESTINATION,
    )
    assert original.somente_conferir is False
    assert original.quantidade_origem_esperada is None

    audit = RenovacaoCampanhaSincronizarRequest(
        loja=STORE, campanha_origem_id=SOURCE, campanha_destino_id=DESTINATION,
        somente_conferir=True, quantidade_origem_esperada=908,
    )
    assert audit.somente_conferir is True
    assert audit.quantidade_origem_esperada == 908
