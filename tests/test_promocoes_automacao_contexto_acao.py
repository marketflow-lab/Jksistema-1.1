from backend.services import promocoes_api_jobs as jobs


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
