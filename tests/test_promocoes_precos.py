import unittest
from unittest.mock import patch

from backend.services import mercadolivre_legacy_pricing as pricing
from backend.services.mercadolivre_legacy_planilhas import _ml_iterar_campos_payload_limitado
from backend.services import promocoes_api_analise as analise


def _parse_float(value):
    if value is None or value == "":
        return None
    return float(str(value).replace(",", "."))


class PromocoesPrecosTests(unittest.TestCase):
    def setUp(self):
        self.parse_patch = patch.object(
            pricing,
            "_parse_float_flex",
            side_effect=_parse_float,
            create=True,
        )
        self.iter_patch = patch.object(
            pricing,
            "_ml_iterar_campos_payload_limitado",
            side_effect=_ml_iterar_campos_payload_limitado,
            create=True,
        )
        self.parse_patch.start()
        self.iter_patch.start()
        self.analise_parse_patch = patch.object(
            analise,
            "_parse_float_flex",
            side_effect=_parse_float,
            create=True,
        )
        self.analise_parse_patch.start()

    def tearDown(self):
        self.analise_parse_patch.stop()
        self.iter_patch.stop()
        self.parse_patch.stop()

    def test_seller_campaign_uses_suggested_price_before_maximum_limit(self):
        payload = {
            "id": "MLB2041042829",
            "status": "candidate",
            "original_price": 75.90,
            "min_discounted_price": 30.36,
            "max_discounted_price": 72.10,
            "sub_type": "FLEXIBLE_PERCENTAGE",
            "suggested_discounted_price": 70.00,
        }

        price, discount = pricing._ml_extrair_preco_promocao_raw(
            payload,
            priorizar_percentual_total_api=True,
        )

        self.assertEqual(price, 70.00)
        self.assertAlmostEqual(discount, ((75.90 - 70.00) / 75.90) * 100.0)

    def test_maximum_limit_remains_fallback_without_suggested_price(self):
        payload = {
            "status": "candidate",
            "original_price": 75.90,
            "max_discounted_price": 72.10,
        }

        price, _discount = pricing._ml_extrair_preco_promocao_raw(payload)

        self.assertEqual(price, 72.10)

    def test_active_price_remains_preferred_over_suggestion(self):
        payload = {
            "status": "started",
            "original_price": 75.90,
            "price": 69.50,
            "suggested_discounted_price": 70.00,
            "max_discounted_price": 72.10,
        }

        price, _discount = pricing._ml_extrair_preco_promocao_raw(payload)

        self.assertEqual(price, 69.50)

    def test_panel_uses_maximum_when_suggestion_has_no_candidate_smart(self):
        payload = {
            "status": "candidate",
            "sub_type": "FLEXIBLE_PERCENTAGE",
            "original_price": 129.90,
            "max_discounted_price": 123.40,
            "suggested_discounted_price": 118.59,
        }
        promocoes_item = [
            {"type": "SMART", "status": "candidate", "price": 116.01},
        ]

        ajustado = analise._promo_ajustar_preco_painel_seller_campaign(
            payload,
            "SELLER_CAMPAIGN",
            promocoes_item,
        )
        price, _discount = pricing._ml_extrair_preco_promocao_raw(ajustado)

        self.assertEqual(price, 123.40)
        self.assertEqual(ajustado["_jk_preco_painel_fonte"], "max_discounted_price")

    def test_panel_uses_suggestion_matching_candidate_smart(self):
        payload = {
            "status": "candidate",
            "sub_type": "FLEXIBLE_PERCENTAGE",
            "original_price": 75.90,
            "max_discounted_price": 72.10,
            "suggested_discounted_price": 70.00,
        }
        promocoes_item = [
            {"type": "SMART", "status": "candidate", "price": 70.00},
        ]

        ajustado = analise._promo_ajustar_preco_painel_seller_campaign(
            payload,
            "SELLER_CAMPAIGN",
            promocoes_item,
        )
        price, _discount = pricing._ml_extrair_preco_promocao_raw(ajustado)

        self.assertEqual(price, 70.00)
        self.assertEqual(
            ajustado["_jk_preco_painel_fonte"],
            "suggested_discounted_price_smart_candidate",
        )

    def test_started_smart_does_not_activate_suggested_price(self):
        payload = {
            "status": "candidate",
            "sub_type": "FLEXIBLE_PERCENTAGE",
            "max_discounted_price": 73.05,
            "suggested_discounted_price": 72.68,
        }
        promocoes_item = [
            {"type": "SMART", "status": "started", "price": 72.68},
        ]

        ajustado = analise._promo_ajustar_preco_painel_seller_campaign(
            payload,
            "SELLER_CAMPAIGN",
            promocoes_item,
        )

        self.assertEqual(ajustado["_jk_preco_painel_seller_campaign"], 73.05)

    def test_caso_real_boost_usa_preco_final_ativo(self):
        payload = {
            "promotion_id": "P-AGOSTO-TOP-ITENS",
            "promotion_type": "SMART",
            "status": "started",
            "offer_id": "OFFER-MLB4407660734-13520914530",
            "original_price": 192.28,
            "price": 148.46,
            "boosted_offer": True,
            "total_price_for_boosted_offer": 147.34,
            "discount_meli_boost_amount": 5.71,
        }

        price, discount = pricing._ml_extrair_preco_promocao_raw(
            payload,
            priorizar_percentual_total_api=True,
        )
        resolved_discount = pricing._ml_calcular_percentual_desconto_por_preco(192.28, price)

        self.assertEqual(price, 147.34)
        self.assertAlmostEqual(resolved_discount, 23.37216559184418)

    def test_sale_price_confirma_oferta_ativa_sem_mesclar_candidate(self):
        active = {
            "promotion_id": "P-AGOSTO-TOP-ITENS",
            "promotion_type": "SMART",
            "status": "started",
            "offer_id": "OFFER-MLB4407660734-13520914530",
            "original_price": 192.28,
            "price": 147.34,
            "boosted_offer": True,
            "discount_meli_boost_amount": 5.71,
        }
        candidate = {
            "promotion_id": "P-AGOSTO-TOP-ITENS",
            "promotion_type": "SMART",
            "status": "candidate",
            "ref_id": "CANDIDATE-MLB4407660734-77013259494",
            "original_price": 192.28,
            "price": 148.46,
            "boosted_offer": True,
            "discount_meli_boost_amount": 8.83,
        }
        price_info = {
            "price": 147.34,
            "promotion_id": "OFFER-MLB4407660734-13520914530",
            "price_source": "sale_price",
        }

        selected = analise._promo_selecionar_raw_ativo_sale_price(
            active,
            candidate,
            price_info,
            "P-AGOSTO-TOP-ITENS",
        )

        self.assertEqual(selected, active)
        self.assertEqual(selected["price"], 147.34)
        self.assertEqual(selected["discount_meli_boost_amount"], 5.71)
        self.assertEqual(
            analise._promo_preco_arquivo_ou_sale_price(
                active,
                price_info,
                "P-AGOSTO-TOP-ITENS",
                148.46,
            ),
            147.34,
        )

    def test_sale_price_nao_aceita_oferta_ou_campanha_divergente(self):
        active = {
            "promotion_id": "P-OUTRA-CAMPANHA",
            "status": "started",
            "offer_id": "OFFER-OUTRA",
            "price": 147.34,
        }
        candidate = {
            "promotion_id": "P-AGOSTO-TOP-ITENS",
            "status": "candidate",
            "ref_id": "CANDIDATE-MLB4407660734-77013259494",
            "price": 148.46,
        }

        price_info = {
            "price": 147.34,
            "promotion_id": "OFFER-MLB4407660734-13520914530",
            "price_source": "sale_price",
        }
        with self.subTest("campanha_divergente"):
            selected = analise._promo_selecionar_raw_ativo_sale_price(
                active,
                candidate,
                price_info,
                "P-AGOSTO-TOP-ITENS",
            )
            self.assertEqual(selected, candidate)

        with self.subTest("offer_divergente"):
            active_mesma_campanha = dict(active)
            active_mesma_campanha["promotion_id"] = "P-AGOSTO-TOP-ITENS"
            selected = analise._promo_selecionar_raw_ativo_sale_price(
                active_mesma_campanha,
                candidate,
                price_info,
                "P-AGOSTO-TOP-ITENS",
            )
            self.assertEqual(selected, candidate)
            self.assertEqual(
                analise._promo_preco_arquivo_ou_sale_price(
                    active_mesma_campanha,
                    price_info,
                    "P-AGOSTO-TOP-ITENS",
                    148.46,
                ),
                148.46,
            )

    def test_sale_price_usa_oferta_ativa_mais_barata_da_consulta_por_item(self):
        candidate = {
            "id": "P-MLB17919018",
            "type": "SMART",
            "status": "candidate",
            "ref_id": "CANDIDATE-MLB4407660734-77013259494",
            "price": 148.46,
            "original_price": 192.28,
            "seller_percentage": 18.2,
            "meli_percentage": 4.6,
        }
        active = {
            "id": "P-MLB17753016",
            "type": "SMART",
            "status": "started",
            "ref_id": "OFFER-MLB4407660734-13520914530",
            "price": 147.34,
            "original_price": 192.28,
            "seller_percentage": 20.4,
            "meli_percentage": 3.0,
        }
        price_info = {
            "price": 147.34,
            "promotion_id": "OFFER-MLB4407660734-13520914530",
            "price_source": "sale_price",
        }

        selected = analise._promo_selecionar_raw_ativo_sale_price(
            candidate,
            candidate,
            price_info,
            "P-MLB17919018",
            [candidate, active],
        )

        self.assertEqual(selected, active)
        self.assertEqual(selected["price"], 147.34)
        self.assertEqual(selected["seller_percentage"], 20.4)
        self.assertEqual(selected["meli_percentage"], 3.0)
        self.assertEqual(candidate["ref_id"], "CANDIDATE-MLB4407660734-77013259494")

    def test_sale_price_empatado_confirma_oferta_ativa_mas_preco_maior_nao_vence(self):
        candidate = {
            "id": "P-CANDIDATE",
            "type": "SMART",
            "status": "candidate",
            "ref_id": "CANDIDATE-1",
            "price": 147.34,
            "original_price": 192.28,
            "seller_percentage": 21.0,
            "meli_percentage": 2.4,
        }
        active = {
            "id": "P-ACTIVE",
            "type": "SMART",
            "status": "started",
            "ref_id": "OFFER-ACTIVE",
            "price": 147.34,
            "original_price": 192.28,
            "seller_percentage": 20.4,
            "meli_percentage": 3.0,
        }
        price_info = {
            "price": 147.34,
            "promotion_id": "OFFER-ACTIVE",
            "price_source": "sale_price",
        }

        selected = analise._promo_selecionar_raw_ativo_sale_price(
            candidate,
            candidate,
            price_info,
            "P-CANDIDATE",
            [candidate, active],
        )

        self.assertEqual(selected, active)
        self.assertEqual(selected["ref_id"], "OFFER-ACTIVE")
        self.assertEqual(selected["meli_percentage"], 3.0)

        active_maior = dict(active, price=147.36)
        price_info_maior = dict(price_info, price=147.36)
        self.assertEqual(
            analise._promo_selecionar_raw_ativo_sale_price(
                candidate,
                candidate,
                price_info_maior,
                "P-CANDIDATE",
                [candidate, active_maior],
            ),
            candidate,
        )

    def test_sale_price_de_outra_campanha_nao_vence_candidato_menor_ou_empate_ambiguo(self):
        candidate = {
            "id": "P-CANDIDATE",
            "status": "candidate",
            "ref_id": "CANDIDATE-1",
            "price": 148.46,
        }
        active_maior = {
            "id": "P-ACTIVE",
            "status": "started",
            "ref_id": "OFFER-ACTIVE",
            "price": 149.00,
        }
        price_info_maior = {
            "price": 149.00,
            "promotion_id": "OFFER-ACTIVE",
            "price_source": "sale_price",
        }
        self.assertEqual(
            analise._promo_selecionar_raw_ativo_sale_price(
                candidate,
                candidate,
                price_info_maior,
                "P-CANDIDATE",
                [active_maior],
            ),
            candidate,
        )

        active_menor = dict(active_maior, price=147.34)
        price_info_menor = dict(price_info_maior, price=147.34)
        self.assertEqual(
            analise._promo_selecionar_raw_ativo_sale_price(
                candidate,
                candidate,
                price_info_menor,
                "P-CANDIDATE",
                [active_menor, dict(active_menor)],
            ),
            candidate,
        )

    def test_sale_price_mesma_campanha_so_substitui_quando_ativo_e_mais_barato(self):
        candidate = {
            "id": "P-CAMPANHA",
            "status": "candidate",
            "ref_id": "CANDIDATE-1",
            "price": 147.34,
        }
        active = {
            "promotion_id": "P-CAMPANHA",
            "status": "started",
            "offer_id": "OFFER-1",
            "price": 148.46,
        }
        price_info = {
            "price": 148.46,
            "promotion_id": "OFFER-1",
            "price_source": "sale_price",
        }

        self.assertEqual(
            analise._promo_selecionar_raw_ativo_sale_price(
                active,
                candidate,
                price_info,
                "P-CAMPANHA",
            ),
            candidate,
        )

        active_mais_barato = dict(active, price=146.90)
        price_info_mais_barato = dict(price_info, price=146.90)
        self.assertEqual(
            analise._promo_selecionar_raw_ativo_sale_price(
                active_mais_barato,
                candidate,
                price_info_mais_barato,
                "P-CAMPANHA",
            ),
            active_mais_barato,
        )

    def test_sale_price_confirma_origem_por_offer_ou_por_campanha(self):
        candidate = {
            "id": "P-CAMPANHA",
            "status": "candidate",
            "ref_id": "CANDIDATE-1",
            "price": 148.46,
        }
        active = {
            "promotion_id": "P-CAMPANHA",
            "status": "started",
            "offer_id": "OFFER-1",
            "price": 147.34,
        }

        for origem in ("P-CAMPANHA", "OFFER-1"):
            with self.subTest(origem=origem):
                self.assertEqual(
                    analise._promo_selecionar_raw_ativo_sale_price(
                        active,
                        candidate,
                        {
                            "price": 147.34,
                            "promotion_id": origem,
                            "price_source": "sale_price",
                        },
                        "P-CAMPANHA",
                    ),
                    active,
                )

    def test_preco_importado_mais_barato_nao_e_substituido_por_sale_price(self):
        candidate = {
            "id": "P-CANDIDATE",
            "status": "candidate",
            "ref_id": "CANDIDATE-1",
            "price": 145.00,
        }
        active = {
            "id": "P-ACTIVE",
            "status": "started",
            "ref_id": "OFFER-ACTIVE",
            "price": 147.34,
        }
        price_info = {
            "price": 147.34,
            "promotion_id": "OFFER-ACTIVE",
            "price_source": "sale_price",
        }

        self.assertEqual(
            analise._promo_preco_arquivo_ou_sale_price(
                candidate,
                price_info,
                "P-CANDIDATE",
                145.00,
                promocoes_item=[active],
            ),
            145.00,
        )

if __name__ == "__main__":
    unittest.main()
