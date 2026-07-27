import unittest
from unittest.mock import patch

from backend.services import mercadolivre_legacy_pricing as pricing
from backend.services import mercadolivre_legacy_promocoes as promocoes
from backend.services.mercadolivre_legacy_planilhas import (
    _ml_iterar_campos_payload_limitado,
)


def _parse_float(value):
    if value is None or value == "":
        return None
    text = str(value).strip().replace("R$", "").replace("%", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return float(text)


class PromocoesDescontoMlTests(unittest.TestCase):
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
        self.to_float_patch = patch.object(
            pricing,
            "_to_float_safe",
            side_effect=_parse_float,
            create=True,
        )
        self.promocoes_to_float_patch = patch.object(
            promocoes,
            "_to_float_safe",
            side_effect=_parse_float,
            create=True,
        )
        self.parse_patch.start()
        self.iter_patch.start()
        self.to_float_patch.start()
        self.promocoes_to_float_patch.start()

    def tearDown(self):
        self.promocoes_to_float_patch.stop()
        self.to_float_patch.stop()
        self.iter_patch.stop()
        self.parse_patch.stop()

    def test_boosted_amount_is_the_authoritative_fee_discount(self):
        payload = {
            "boosted_offer": True,
            "discount_meli_boost_amount": 6.64,
            "discount_meli_boosted_percentage": 4.45,
            "original_price": 149.24,
            "meli_percentage": 2.4,
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertEqual(desconto, 6.64)

    def test_missing_boosted_amount_does_not_derive_discount_from_percentage(self):
        payload = {
            "boosted_offer": True,
            "discount_meli_boosted_percentage": 4.45,
            "original_price": 149.24,
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertIsNone(desconto)

    def test_zero_boosted_amount_is_incomplete_and_blocks_percentage_fallback(self):
        payload = {
            "boosted_offer": True,
            "discount_meli_boost_amount": 0,
            "discount_meli_boosted_percentage": 4.45,
            "original_price": 149.24,
            "meli_percentage": 2.4,
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertIsNone(desconto)

    def test_explicitly_non_boosted_offer_is_rendered_as_zero(self):
        payload = {
            "boosted_offer": False,
            "discount_meli_boost_amount": 6.64,
            "discount_meli_boosted_percentage": 4.45,
            "original_price": 149.24,
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertEqual(desconto, 0.0)

    def test_meli_percentage_without_boost_becomes_zero_not_fee_discount(self):
        payload = {
            "original_price": 149.24,
            "meli_percentage": 2.4,
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertEqual(desconto, 0.0)

    def test_explicit_legacy_sale_fee_discount_remains_supported(self):
        payload = {
            "sale_fee_discount": 1.75,
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertEqual(desconto, 1.75)

    def test_nested_boost_payload_remains_supported(self):
        payload = {
            "promotion": {
                "boosted_offer": True,
                "discount_meli_boost_amount": 6.64,
            }
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertEqual(desconto, 6.64)

    def test_sibling_payloads_do_not_mix_boost_flag_and_amount(self):
        payload = {
            "offers": [
                {"boosted_offer": True},
                {"discount_meli_boost_amount": 6.64},
            ]
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertIsNone(desconto)

    def test_complete_second_sibling_boost_payload_is_selected(self):
        payload = {
            "offers": [
                {"boosted_offer": True},
                {
                    "boosted_offer": True,
                    "discount_meli_boost_amount": 6.64,
                },
            ]
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertEqual(desconto, 6.64)

    def test_nested_explicit_legacy_fee_discount_remains_supported(self):
        payload = {
            "promotion": {
                "fees": {
                    "sale_fee_reduction": 2.25,
                }
            }
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertEqual(desconto, 2.25)

    def test_receivable_adjustment_drops_unprovenanced_historical_discount(self):
        desconto = pricing._ml_ajustar_desconto_tarifa_recebivel_promocao(
            {},
            desconto_tarifa_ml=1.75,
            preco_promocional=110.79,
            pct_desconto_campanha=25.76,
        )

        self.assertIsNone(desconto)

    def test_receivable_adjustment_prefers_official_boosted_amount(self):
        payload = {
            "boosted_offer": True,
            "discount_meli_boost_amount": 6.64,
            "meli_percentage": 2.4,
        }

        desconto = pricing._ml_ajustar_desconto_tarifa_recebivel_promocao(
            payload,
            desconto_tarifa_ml=3.58,
            preco_promocional=110.79,
            pct_desconto_campanha=25.76,
        )

        self.assertEqual(desconto, 6.64)

    def test_receivable_adjustment_drops_current_discount_for_zero_boosted_amount(self):
        payload = {
            "boosted_offer": True,
            "discount_meli_boost_amount": 0,
            "discount_meli_boosted_percentage": 4.45,
            "original_price": 149.24,
        }

        desconto = pricing._ml_ajustar_desconto_tarifa_recebivel_promocao(
            payload,
            desconto_tarifa_ml=3.58,
            preco_promocional=110.79,
            pct_desconto_campanha=25.76,
        )

        self.assertIsNone(desconto)

    def test_receivable_adjustment_drops_current_discount_when_boost_amount_is_missing(self):
        payload = {
            "boosted_offer": True,
            "discount_meli_boosted_percentage": 4.45,
            "original_price": 149.24,
        }

        desconto = pricing._ml_ajustar_desconto_tarifa_recebivel_promocao(
            payload,
            desconto_tarifa_ml=3.58,
            preco_promocional=110.79,
            pct_desconto_campanha=25.76,
        )

        self.assertIsNone(desconto)

    def test_receivable_adds_official_boosted_amount_exactly_once(self):
        recebido_sem_desconto = pricing._ml_calcular_recebivel_promocao(
            {},
            preco_promocional=110.79,
            tarifa_ml=20.00,
            frete_ml=15.00,
            desconto_tarifa_ml=None,
        )
        recebido_com_desconto = pricing._ml_calcular_recebivel_promocao(
            {},
            preco_promocional=110.79,
            tarifa_ml=20.00,
            frete_ml=15.00,
            desconto_tarifa_ml=6.64,
        )

        self.assertEqual(recebido_sem_desconto, 75.79)
        self.assertEqual(recebido_com_desconto, 82.43)
        self.assertAlmostEqual(recebido_com_desconto - recebido_sem_desconto, 6.64)

    def test_current_promotion_without_boost_is_rendered_as_zero(self):
        payload = {
            "status": "candidate",
            "price": 114.22,
            "original_price": 149.24,
            "meli_percentage": 2.4,
            "seller_percentage": 21.1,
            "offer_id": "CANDIDATE-MLB5211215702-1",
            "promotion_type": "SMART",
        }

        extraido = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)
        calculado = promocoes._calcular_desconto_ml_valor(
            desconto_atual=extraido,
            ml_pct=23.47,
            preco_base=149.24,
            preco_final_ml=114.22,
            tarifa_base=25.37,
            tarifa_ml=19.42,
            desconto_atual_confiavel=True,
        )
        ajustado = pricing._ml_ajustar_desconto_tarifa_recebivel_promocao(
            payload,
            desconto_tarifa_ml=calculado,
            preco_promocional=114.22,
            pct_desconto_campanha=23.47,
        )

        self.assertEqual(extraido, 0.0)
        self.assertEqual(calculado, 0.0)
        self.assertEqual(ajustado, 0.0)

    def test_imported_old_meli_percentage_amount_is_not_trusted(self):
        desconto = promocoes._calcular_desconto_ml_valor(
            desconto_atual=3.58,
            ml_pct=2.4,
            preco_base=149.24,
            preco_final_ml=114.22,
            tarifa_base=25.37,
            tarifa_ml=19.42,
        )

        self.assertIsNone(desconto)


if __name__ == "__main__":
    unittest.main()
