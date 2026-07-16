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


if __name__ == "__main__":
    unittest.main()
