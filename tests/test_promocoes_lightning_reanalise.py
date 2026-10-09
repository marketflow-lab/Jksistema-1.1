"""Offline regression coverage for canonical LIGHTNING price recovery."""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests

from backend.services import promocoes_lightning_reanalise as recovery
from backend.services.promocoes_lightning_acao import lightning_row_fields
from backend.schemas.promocoes import PromoAplicarParticipacaoRequest

CAMPAIGN = "LGH-MLB1000"
ITEM = "MLB4049732004"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("Real network is forbidden"))


def candidate(price=195.60, original=260.64, item=ITEM, **fields):
    return {"promotion_id": CAMPAIGN, "type": "LIGHTNING", "item_id": item,
        "status": "candidate", "ref_id": f"CANDIDATE-{item}-same",
        "price": price, "original_price": original, "min_stock": 5, "max_stock": 20,
        "_jk_campaign_id_consultado": CAMPAIGN, "_jk_item_id_consultado": item,
        "_jk_promotion_type_consultado": "LIGHTNING", "_jk_contexto_promocao_confirmado": True, **fields}


def context(raw=None):
    raw = raw or candidate()
    selected = {"item_id": raw["item_id"], "deal_price": raw["price"],
        "offer_id": raw["ref_id"], **lightning_row_fields(raw, "LIGHTNING", CAMPAIGN, raw["item_id"])}
    return {"selected": selected, "promotion_id": CAMPAIGN, "item": {"currency_id": "BRL"},
        "promocao_a_id": "PROMO-A", "promocao_a_type": "SELLER_CAMPAIGN",
        "margem_base": 20, "status_promo_a": "Ativo", "margem_minima": 15, "margem_tolerancia": 1,
        "sku": "SKU-A", "site_id": "MLB", "fontes": {"custo": "cadastro/maior_custo_variacao"}}


@pytest.fixture
def harness(monkeypatch):
    state = SimpleNamespace(invites=[candidate()], prices={"standard_price": 260.64, "sale_price": 199.72},
        reads=0, sent=[], margins=[], approved=True, responses=[("applied", "", 201)], after=[])
    def confirm(client, store, cfg, campaign, item, selected):
        assert campaign == CAMPAIGN and item == ITEM
        raw = copy.deepcopy(state.invites[min(state.reads, len(state.invites)-1)])
        state.reads += 1
        raw["_jk_lightning_confirmed_for_send"] = True
        return {"_lightning_raw_confirmed": raw}, cfg
    def prices(service, client, store, cfg, item, currency):
        return state.prices, cfg
    def finance(service, analysis, client, store, cfg, item, raw, canonical, price, standard):
        state.margins.append(price)
        return {"margem": 19.5}, {"action_deal_price": price, "action_margem_ml": 19.5}, state.approved, cfg
    def send(client, store, cfg, **kwargs):
        state.sent.append(kwargs["deal_price"])
        outcome, code, status = state.responses[min(len(state.sent)-1, len(state.responses)-1)]
        kwargs["result_details"].update(outcome=outcome, submission_attempted=True, http_status=status, ml_error_code=code)
        return outcome == "applied", code, cfg
    service = SimpleNamespace(_promo_confirmar_oferta_lightning=confirm, _promo_aplicar_item_participacao_ml=send,
        _ml_obter_promocoes_item=lambda *a, **k: (copy.deepcopy(state.after), a[2]),
        _promo_lightning_scoped_offers=lambda records, *a: {str(i): r for i, r in enumerate(records)},
        _promo_lightning_conflicting_keys=lambda *a: set(),
        _promo_lightning_preexisting=lambda *a: False, _lightning_status=lambda raw: raw.get("status"),
        _LIGHTNING_PARTICIPATING_STATES={"pending", "started"},
        _promo_lightning_confirmation_fields=lambda raw: {"status": raw["status"], "lightning_schedule_pending": True})
    monkeypatch.setattr(recovery, "_official_prices", prices)
    monkeypatch.setattr(recovery, "_fresh_financial", finance)
    def run():
        details = {}
        result = recovery.apply_reanalysed_lightning(service, None, "tenant-a", "store-a", {},
            campaign=CAMPAIGN, item_id=ITEM, selected={"lightning_stock": 6}, row_context=context(), result_details=details)
        return result, details
    state.run = run
    return state


@pytest.mark.parametrize("item,price,original,sale,minimum,maximum,invalid", [
    ("MLB3502651779",110.68,91.01,71.89,29.50,132.73,True),
    ("MLB4049732004",195.60,260.64,199.72,52.13,234.58,False),
    ("MLB3331195036",77.76,103.67,79.39,20.74,93.31,False),
])
def test_reported_prices_are_checked_against_standard_not_current_winner(item,price,original,sale,minimum,maximum,invalid):
    # The middle fixture's suggestion is intentionally not used as an offer.
    raw = candidate(price, original, item, min_discounted_price=minimum,max_discounted_price=maximum)
    reason, _, _, checked = recovery._price_reason(raw,{"standard_price":original,"sale_price":sale})
    assert checked == price and bool(reason) is invalid


def test_money_uses_integer_cents_and_rejects_nonfinite():
    assert recovery.money_cents("195.605") == 19561
    assert recovery.money_cents("195.60") == 19560
    for value in (True, "NaN", "Infinity", 0, -1, None):
        assert recovery.price_number(value) is None


def test_incoherent_invite_refreshes_once_without_post(harness):
    harness.invites=[candidate(300)]
    _, details=harness.run()
    assert harness.reads==2 and not harness.sent
    assert details["price_diagnostics"]["decision_code"]=="OFFICIAL_PRICE_INCONSISTENT"


def test_new_official_price_before_first_post_is_recalculated(harness):
    harness.invites=[candidate(190)]
    result, details=harness.run()
    assert result[0] and harness.sent==harness.margins==[190]
    assert details["analysis_row_updates"]["action_deal_price"]==190


def test_credibility_same_official_price_does_not_repeat(harness):
    harness.responses=[("rejected","ERROR_CREDIBILITY_DISCOUNTED_PRICE",400)]
    _,details=harness.run()
    assert harness.sent==[195.60]
    assert details["price_diagnostics"]["decision_code"]=="OFFICIAL_PRICE_UNCHANGED_AFTER_REJECTION"


def test_credibility_only_retries_a_different_official_price(harness):
    harness.invites=[candidate(195.60),candidate(190)]
    harness.responses=[("rejected","ERROR_CREDIBILITY_DISCOUNTED_PRICE",400),("applied","",201)]
    result,details=harness.run()
    assert result[0] and harness.sent==harness.margins==[195.60,190]
    assert details["price_diagnostics"]["attempt_count"]==2


def test_two_rejections_never_send_a_third_price(harness):
    harness.invites=[candidate(195.60),candidate(190),candidate(180)]
    harness.responses=[("rejected","ERROR_CREDIBILITY_DISCOUNTED_PRICE",400)]
    _,details=harness.run()
    assert harness.sent==[195.60,190] and details["outcome"]=="rejected"


def test_other_credibility_code_does_not_activate_recovery(harness):
    harness.responses=[("rejected","ERROR_CREDIBILITY_PRICE",400)]
    _,details=harness.run()
    assert harness.sent==[195.60] and harness.reads==1


def test_new_price_margin_below_rules_never_posts(harness):
    harness.approved=False
    _,details=harness.run()
    assert not harness.sent and details["price_diagnostics"]["decision_code"]=="RECALCULATED_MARGIN_REJECTED"


@pytest.mark.parametrize("status",[None,408,500,503])
def test_unknown_response_consults_without_resending(harness,status):
    harness.responses=[("unknown","",status)]
    _,details=harness.run()
    assert harness.sent==[195.60] and details["price_diagnostics"]["decision_code"]=="PARTICIPATION_UNCONFIRMED"


def test_unknown_response_with_unique_new_participation_is_confirmed(harness):
    harness.responses=[("unknown","",503)]
    harness.after=[candidate(status="pending",ref_id=f"OFFER-{ITEM}-new")]
    result,details=harness.run()
    assert result[0] and details["outcome"]=="applied" and harness.sent==[195.60]


def test_unknown_response_does_not_confirm_partial_calendar(harness):
    harness.responses=[("unknown","",503)]
    harness.after=[candidate(status="pending",ref_id=f"OFFER-{ITEM}-new",start_date="2026-10-09T12:00:00-03:00")]
    result,details=harness.run()
    assert not result[0] and details["outcome"]=="unknown"


@pytest.fixture
def prices_service():
    standard={"type":"standard","amount":260.64,"currency_id":"BRL","conditions":{"context_restrictions":["channel_marketplace"]}}
    state=SimpleNamespace(entries=[standard],body_id=ITEM,sale=199.72,sale_currency="BRL")
    def request(client,store,cfg,method,url,**kwargs):
        assert method=="GET"
        body={"id":state.body_id,"prices":state.entries} if url.endswith("/prices") else {"amount":state.sale,"currency_id":state.sale_currency}
        return SimpleNamespace(status_code=200,json=lambda:body),cfg
    state.service=SimpleNamespace(_ml_api_request=request)
    return state


@pytest.mark.parametrize("extra",[
    {"amount":1,"conditions":{"context_restrictions":["channel_mshops"]}},
    {"amount":2,"conditions":{"context_restrictions":["channel_marketplace","user_type_business"]}},
    {"amount":3,"currency_id":"USD"},
    {"amount":3.5,"amount_tax_inclusion_type":"net"},
    {"amount":4,"conditions":{"context_restrictions":["channel_marketplace"],"min_purchase_unit":10}},
    {"amount":5,"conditions":{"context_restrictions":["channel_marketplace"],"start_time":(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}},
    {"amount":6,"conditions":{"context_restrictions":["channel_marketplace"],"end_time":(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}},
])
def test_standard_ignores_other_context_currency_quantity_and_time(prices_service,extra):
    prices_service.entries.append({**prices_service.entries[0],**extra})
    prices,_=recovery._official_prices(prices_service.service,"tenant-a","store-a",{},ITEM,"BRL")
    assert prices["standard_price"]==260.64 and prices["sale_price"]==199.72


def test_standard_conflict_or_wrong_item_is_not_guessed(prices_service):
    prices_service.entries.append({**prices_service.entries[0],"amount":270})
    with pytest.raises(ValueError):
        recovery._official_prices(prices_service.service,"tenant-a","store-a",{},ITEM,"BRL")
    prices_service.entries=prices_service.entries[:1]
    prices_service.body_id="MLB3331195036"
    with pytest.raises(ValueError):
        recovery._official_prices(prices_service.service,"tenant-a","store-a",{},ITEM,"BRL")


def test_request_policy_is_adaptive_only_when_explicitly_selected():
    assert PromoAplicarParticipacaoRequest(loja="store-a",promocoes=[]).price_policy=="exact"
    with pytest.raises(ValueError):
        PromoAplicarParticipacaoRequest(loja="store-a",promocoes=[],price_policy="guess")

@pytest.fixture
def actual_finance(monkeypatch):
    from backend.services import promocoes_api_analise as analysis
    from backend.services import promocoes_api_participacoes as service
    from tests.test_promocoes_desconto_ml_fluxo import PromocoesDescontoMlFluxoTests
    PromocoesDescontoMlFluxoTests.setUpClass()
    product={"id":ITEM,"seller_id":111,"site_id":"MLB","currency_id":"BRL","price":199.72}
    reference=candidate(199.72,status="started",promotion_id="PROMO-A",type="SELLER_CAMPAIGN",
        _jk_campaign_id_consultado="PROMO-A",_jk_promotion_type_consultado="SELLER_CAMPAIGN")
    state=SimpleNamespace(calls=[],costs={"SKU-A":20,"SKU-B":35},taxes={"SKU-A":.1},reference=reference,product=product)
    monkeypatch.setattr(service,"_ml_api_request",lambda *a,**k:(SimpleNamespace(status_code=200,json=lambda:product),a[2]),raising=False)
    monkeypatch.setattr(service,"_ml_obter_promocoes_item",lambda *a,**k:([reference],a[2]),raising=False)
    monkeypatch.setattr(analysis,"_ml_obter_item_promocao_raw",lambda *a,**k:(state.reference,a[2]))
    monkeypatch.setattr(analysis,"_ml_extrair_sku",lambda item:"SKU-A")
    monkeypatch.setattr(analysis,"_ml_extrair_variacoes_resumo",lambda *a,**k:[{"sku":"SKU-A"},{"sku":"SKU-B"}])
    monkeypatch.setattr(analysis,"_carregar_custos_impostos_cadastro_por_sku_loja",lambda *a:(state.costs,state.taxes))
    monkeypatch.setattr(analysis,"_promo_observar_cotacao_financeira_shadow",lambda **k:None)
    def shipping(client,store,cfg,item_id,item,first,second):
        state.calls.append(("shipping",second))
        data={"shipping_cost":5,"shipping_exact_for_price":True,"shipping_price_context":second,
              "shipping_cost_retry_source":"users/shipping_options/free/contexto"}
        return copy.deepcopy(data),data,cfg
    def fees(client,store,cfg,item,*a,**k):
        price=item["price"]
        state.calls.append(("fees",price))
        return {"ad_cost":round(price*.1,2),"ad_cost_price_context":price,
                "ad_cost_source":"sites/MLB/listing_prices","ad_cost_exact_for_price":False},cfg
    monkeypatch.setattr(analysis,"_promo_obter_fretes_por_preco",shipping)
    monkeypatch.setattr(analysis,"_ml_obter_taxas_anuncio",fees)
    def run(**rules):
        canonical=context()
        canonical.update(margem_base=1,**rules)
        return recovery._fresh_financial(service,analysis,"tenant-a","store-a",{"user_id":111},
            ITEM,candidate(190),canonical,190,260.64)
    state.run=run
    return state


def test_actual_finance_refreshes_both_campaigns_and_highest_variation_cost(actual_finance):
    financial,updates,approved,_=actual_finance.run(margem_tolerancia=2)
    assert financial["margem"]==pytest.approx((190-19-5-19-35)*100/190)
    assert actual_finance.calls==[("shipping",190),("fees",190),("shipping",199.72),("fees",199.72)]
    assert approved and financial["estimado"]
    assert updates["action_financeiro_estimado"] and updates["action_financeiro_exato"] is False
    assert updates["reference_margin"]>financial["margem"]>1
    assert updates["Custo"]=="R$ 35,00"
    assert {"Preço Final ML","Valor líquido ML","Preço Final","Valor Líquido","Margem"}<=set(updates)
    assert not any("Ã" in key or "Â" in key for key in updates)


@pytest.mark.parametrize("rules",[{"margem_tolerancia":0},{"margem_tolerancia":100,"margem_minima":60}])
def test_current_reference_and_minimum_are_enforced(actual_finance,rules):
    _,_,approved,_=actual_finance.run(**rules)
    assert approved is False  # The stale snapshot margin=1 cannot approve this target.


def test_missing_current_cost_is_not_replaced_by_old_snapshot(actual_finance):
    actual_finance.costs={}
    with pytest.raises(ValueError,match="Custo ou imposto"):
        actual_finance.run()


def test_actual_reference_context_cannot_be_swapped(actual_finance):
    actual_finance.reference={**actual_finance.reference,"_jk_campaign_id_consultado":"OTHER"}
    with pytest.raises(ValueError,match="Promocao 1"):
        actual_finance.run()


from tests.test_promocoes_participacao_resultados import ml, apply
from tests.test_promocoes_lightning_fluxo import offer, selected, fresh, ITEM_ID


def test_legacy_exact_incoherent_price_refreshes_once_without_post(ml,monkeypatch):
    raw=offer(price=110.68)
    ml.standard_price=91.01
    fresh(monkeypatch,[raw])
    result=apply([selected(raw)],campaign=CAMPAIGN,kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"]=="blocked" and not ml.calls
    assert result["detalhes"][0]["price_diagnostics"]["decision_code"]=="OFFICIAL_PRICE_INCONSISTENT"
    assert len(ml.price_lookups)==4


def test_legacy_exact_outside_official_bounds_is_blocked(ml,monkeypatch):
    raw=offer(price=66.32)
    raw.update(min_discounted_price=70,max_discounted_price=80)
    fresh(monkeypatch,[raw])
    result=apply([selected(raw)],campaign=CAMPAIGN,kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"]=="blocked" and not ml.calls


def test_legacy_exact_still_sends_only_selected_price_and_never_suggestion(ml,monkeypatch):
    raw=offer(price=66.32)
    raw.update(suggested_discounted_price=40)
    fresh(monkeypatch,[raw])
    ml.response=(400,{"message":"ERROR_CREDIBILITY_DISCOUNTED_PRICE"})
    result=apply([selected(raw)],campaign=CAMPAIGN,kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"]=="rejected"
    assert len(ml.calls)==1 and ml.calls[0][4]["json"]["deal_price"]==66.32
    assert result["detalhes"][0]["price_diagnostics"]["attempt_count"]==1

@pytest.mark.parametrize("response",[(201,{"price":66.32}),(201,None)])
def test_dated_201_cannot_confirm_preexisting_other_reference(ml,monkeypatch,response):
    from backend.services import promocoes_api_participacoes as service
    raw=offer()
    previous={**raw,"status":"pending","ref_id":f"OFFER-{ITEM_ID}-old"}
    snapshots=iter([[raw,previous],[previous]])
    monkeypatch.setattr(service,"_ml_obter_promocoes_item",lambda *a,**k:(copy.deepcopy(next(snapshots)),a[2]),raising=False)
    ml.response=(response[0],previous if response[1] is None else response[1])
    result=apply([selected(raw)],campaign=CAMPAIGN,kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"]=="unknown" and len(ml.calls)==1


def test_dated_timeout_reconciliation_cannot_confirm_preexisting_other_reference(ml,monkeypatch):
    from backend.services import promocoes_api_participacoes as service
    raw=offer()
    previous={**raw,"status":"pending","ref_id":f"OFFER-{ITEM_ID}-old"}
    raw["_jk_lightning_preexisting_participations"]=[previous]
    fresh(monkeypatch,[previous])
    match,_=recovery._reconcile(service,"tenant-a","store-a",{"scope":("tenant-a","store-a")},CAMPAIGN,ITEM_ID,raw,66.32)
    assert match is None


def test_one_cent_difference_is_not_success(ml,monkeypatch):
    raw=offer()
    fresh(monkeypatch,[raw])
    ml.response=(201,{**raw,"price":66.33,"status":"pending"})
    result=apply([selected(raw)],campaign=CAMPAIGN,kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"]=="unknown" and len(ml.calls)==1


def test_inconsistent_after_rejection_stops_after_one_refresh(harness):
    harness.invites=[candidate(195.60),candidate(300),candidate(190)]
    harness.responses=[("rejected","ERROR_CREDIBILITY_DISCOUNTED_PRICE",400)]
    _,details=harness.run()
    assert harness.reads==2 and harness.sent==[195.60]
    assert details["price_diagnostics"]["decision_code"]=="OFFICIAL_PRICE_INCONSISTENT"

@pytest.fixture
def actual_transport(monkeypatch):
    from backend.services import mercadolivre_legacy_api as api
    from backend.services import promocoes_api_participacoes as service
    from backend.services import store_oauth_refresh
    from backend.services.mercadolivre_legacy_promocoes import _promo_erro_candidate_not_found
    state=SimpleNamespace(posts=[],responses=[401,201],refreshes=[],invites=[offer()],reads=0)
    monkeypatch.setattr(api,"_headers_ml",lambda token:{},raising=False)
    monkeypatch.setattr(api,"_ml_http_invalidar_session",lambda *a:None,raising=False)
    def refresh(client,store,cfg):
        state.refreshes.append((client,store))
        return {**cfg,"access_token":"synthetic-refreshed"}
    monkeypatch.setattr(api,"_ml_refresh_token",refresh,raising=False)
    monkeypatch.setattr(store_oauth_refresh,"revalidate_ml_mutation",lambda client,store,cfg:cfg)
    def http(client,store,token,method,url,**kwargs):
        if method=="POST":
            state.posts.append(kwargs["json"])
            status=state.responses[min(len(state.posts)-1,len(state.responses)-1)]
            body={"message":"ERROR_CREDIBILITY_DISCOUNTED_PRICE"} if status==400 else {"error":"invalid_token"}
        elif url.endswith("/prices"):
            status=200
            body={"id":ITEM_ID,"prices":[{"type":"standard","amount":92.28,"currency_id":"BRL",
                  "conditions":{"context_restrictions":["channel_marketplace"]}}]}
        else:
            status=200
            body={"amount":79.39,"currency_id":"BRL"}
        return SimpleNamespace(status_code=status,json=lambda:body)
    monkeypatch.setattr(api,"_ml_http_request",http,raising=False)
    monkeypatch.setattr(service,"_ml_api_request",api._ml_api_request,raising=False)
    monkeypatch.setattr(service,"_promo_erro_candidate_not_found",_promo_erro_candidate_not_found,raising=False)
    def confirm(client,store,cfg,campaign,item,selected):
        raw=copy.deepcopy(state.invites[min(state.reads,len(state.invites)-1)])
        raw["_jk_lightning_confirmed_for_send"]=True
        state.reads+=1
        return {"_lightning_raw_confirmed":raw},cfg
    monkeypatch.setattr(service,"_promo_confirmar_oferta_lightning",confirm)
    monkeypatch.setattr(recovery,"_fresh_financial",lambda *a,**k:({"margem":20},{"action_deal_price":a[-2]},True,a[4]))
    state.api=api
    state.service=service
    return state


def test_transport_default_preserves_unauthorized_refresh_for_other_callers(actual_transport):
    response,_=actual_transport.api._ml_api_request("tenant-a","store-a",{"access_token":"synthetic"},
        "POST","https://api.mercadolibre.com/other",json={"field":"fixture"})
    assert response.status_code==201 and len(actual_transport.posts)==2 and len(actual_transport.refreshes)==1


def test_lightning_actual_transport_never_reposts_after_unauthorized(actual_transport):
    raw=offer()
    raw["_jk_lightning_confirmed_for_send"]=True
    details={}
    result=actual_transport.service._promo_aplicar_item_participacao_ml("tenant-a","store-a",{"access_token":"synthetic"},
        item_id=ITEM_ID,promotion_id=CAMPAIGN,promotion_type="LIGHTNING",deal_price=66.32,
        stock=6,lightning_context=raw,result_details=details)
    assert not result[0] and len(actual_transport.posts)==1 and not actual_transport.refreshes
    assert details["price_diagnostics"]["decision_code"]=="ML_AUTHENTICATION_REQUIRED"


def test_recovery_actual_transport_two_business_attempts_never_exceed_two_posts(actual_transport):
    actual_transport.responses=[400,401,201]
    actual_transport.invites=[offer(price=66.32),offer(price=65.32)]
    details={}
    result=recovery.apply_reanalysed_lightning(actual_transport.service,None,"tenant-a","store-a",{"access_token":"synthetic"},
        campaign=CAMPAIGN,item_id=ITEM_ID,selected={"lightning_stock":6},row_context=context(offer()),result_details=details)
    assert not result[0] and len(actual_transport.posts)==2 and not actual_transport.refreshes
    assert details["price_diagnostics"]["attempt_count"]==2
    assert details["price_diagnostics"]["decision_code"]=="ML_AUTHENTICATION_REQUIRED"


def test_marketplace_brl_cannot_be_replaced_by_canonical_usd(prices_service):
    prices_service.entries[0]["currency_id"]="USD"
    with pytest.raises(ValueError,match="Moeda"):
        recovery._official_prices(prices_service.service,"tenant-a","store-a",{},ITEM,"USD")


def test_actual_finance_blocks_non_brl_product_before_margin(actual_finance):
    actual_finance.product["currency_id"]="USD"
    with pytest.raises(ValueError,match="Moeda atual"):
        actual_finance.run()
    assert not actual_finance.calls


def test_foreign_currency_winner_is_not_displayed_as_brl(prices_service):
    prices_service.sale_currency="USD"
    prices,_=recovery._official_prices(prices_service.service,"tenant-a","store-a",{},ITEM,"BRL")
    assert prices["standard_price"]==260.64 and prices["sale_price"] is None
