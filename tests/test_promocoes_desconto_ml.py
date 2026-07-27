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


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


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
        self.promocoes_iter_patch = patch.object(
            promocoes,
            "_ml_iterar_campos_payload_limitado",
            side_effect=_ml_iterar_campos_payload_limitado,
            create=True,
        )
        self.promocoes_request_patch = patch.object(
            promocoes,
            "_ml_api_request",
            create=True,
        )
        self.normalize_patch = patch.object(
            pricing,
            "normalizar_texto",
            side_effect=lambda value: str(value or "").strip().lower(),
            create=True,
        )
        self.parse_patch.start()
        self.iter_patch.start()
        self.to_float_patch.start()
        self.promocoes_to_float_patch.start()
        self.promocoes_iter_patch.start()
        self.promocoes_request_patch.start()
        self.normalize_patch.start()

    def tearDown(self):
        self.normalize_patch.stop()
        self.promocoes_request_patch.stop()
        self.promocoes_iter_patch.stop()
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

    def test_meli_percentage_without_boost_is_unknown_not_fee_discount(self):
        payload = {
            "original_price": 149.24,
            "meli_percentage": 2.4,
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertIsNone(desconto)

    def test_total_price_for_boosted_offer_is_the_effective_buyer_price(self):
        payload = {
            "boosted_offer": True,
            "discount_meli_boost_amount": 6.64,
            "total_price_for_boosted_offer": 110.79,
            "price": 117.43,
            "original_price": 149.24,
        }

        preco, _ = pricing._ml_extrair_preco_promocao_raw(payload)

        self.assertEqual(preco, 110.79)

    def test_residual_boosted_total_is_ignored_when_boost_is_false(self):
        payload = {
            "boosted_offer": False,
            "total_price_for_boosted_offer": 110.79,
            "price": 117.43,
            "original_price": 149.24,
        }

        preco, _ = pricing._ml_extrair_preco_promocao_raw(payload)

        self.assertEqual(preco, 117.43)

    def test_explicit_legacy_sale_fee_discount_remains_supported(self):
        payload = {
            "sale_fee_discount": 1.75,
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertEqual(desconto, 1.75)

    def test_nested_boost_payload_is_rejected_without_validated_direct_offer(self):
        payload = {
            "promotion": {
                "boosted_offer": True,
                "discount_meli_boost_amount": 6.64,
            }
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertIsNone(desconto)

    def test_sibling_payloads_do_not_mix_boost_flag_and_amount(self):
        payload = {
            "offers": [
                {"boosted_offer": True},
                {"discount_meli_boost_amount": 6.64},
            ]
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertIsNone(desconto)

    def test_complete_second_sibling_boost_payload_is_not_selected(self):
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

        self.assertIsNone(desconto)

    def test_nested_explicit_legacy_fee_discount_is_rejected(self):
        payload = {
            "promotion": {
                "fees": {
                    "sale_fee_reduction": 2.25,
                }
            }
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertIsNone(desconto)

    def test_shipping_fee_discount_is_not_a_sale_fee_discount(self):
        payload = {
            "shipping_fee_discount_amount": 6.64,
            "shipping": {"sale_fee_discount_amount": 6.64},
        }

        desconto = pricing._ml_extrair_desconto_tarifa_promocao_raw(payload)

        self.assertIsNone(desconto)

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

    def test_receivable_adjustment_reconciles_exact_fee_discount(self):
        payload = {
            "status": "candidate",
            "price": 110.79,
            "seller_receives": 82.15,
            "promotion_type": "SMART",
        }

        desconto = pricing._ml_ajustar_desconto_tarifa_recebivel_promocao(
            payload,
            preco_promocional=110.79,
            tarifa_ml=18.83,
            frete_ml=16.45,
            frete_exato=True,
            frete_preco_contexto=110.79,
            tarifa_exata=True,
            tarifa_preco_contexto=110.79,
            tarifa_fonte="sites/MLB/listing_prices",
            frete_fonte="users/shipping_options/free/contexto",
        )

        self.assertEqual(desconto, 6.64)

    def test_receivable_adjustment_requires_explicit_fee_provenance(self):
        payload = {
            "status": "candidate",
            "price": 110.79,
            "seller_receives": 82.15,
            "promotion_type": "SMART",
        }

        desconto = pricing._ml_ajustar_desconto_tarifa_recebivel_promocao(
            payload,
            preco_promocional=110.79,
            tarifa_ml=18.83,
            frete_ml=16.45,
            frete_exato=True,
            frete_preco_contexto=110.79,
            frete_fonte="users/shipping_options/free/contexto",
        )

        self.assertIsNone(desconto)

    def test_listing_prices_materializes_fee_price_provenance(self):
        item = {
            "id": "MLB999000111",
            "price": 110.79,
            "category_id": "MLB123",
            "listing_type_id": "gold_special",
            "shipping": {"logistic_type": "cross_docking", "mode": "me2"},
        }

        def request_fn(*_args, **_kwargs):
            return _FakeResponse(200, {
                "sale_fee_amount": 18.83,
                "listing_fee_amount": 0,
                "listing_type_name": "Classico",
                "sale_fee_details": {
                    "fixed_fee": 0,
                    "percentage_fee": 17,
                    "meli_percentage_fee": 17,
                    "financing_add_on_fee": 0,
                },
            }), {}

        with (
            patch.object(pricing, "_ml_nome_tipo_anuncio", return_value="Classico", create=True),
            patch.object(pricing, "_cache_get", return_value=None, create=True),
            patch.object(pricing, "_cache_set", create=True),
            patch.object(pricing, "ML_LISTING_FEE_CACHE", {}, create=True),
            patch.object(pricing, "ML_LISTING_FEE_CACHE_TTL", 60, create=True),
        ):
            info, _ = pricing._ml_obter_taxas_anuncio(
                "000002",
                "Loja",
                {},
                item,
                request_fn=request_fn,
            )

        self.assertEqual(info["ad_cost"], 18.83)
        self.assertTrue(info["ad_cost_exact_for_price"])
        self.assertEqual(info["ad_cost_price_context"], 110.79)
        self.assertEqual(info["ad_cost_source"], "sites/MLB/listing_prices")

    def test_receivable_adjustment_rejects_mismatched_promotion_price(self):
        payload = {
            "status": "candidate",
            "price": 114.22,
            "seller_receives": 82.15,
            "promotion_type": "SMART",
        }

        desconto = pricing._ml_ajustar_desconto_tarifa_recebivel_promocao(
            payload,
            preco_promocional=110.79,
            tarifa_ml=18.83,
            frete_ml=16.45,
            frete_exato=True,
            frete_preco_contexto=110.79,
            tarifa_exata=True,
            tarifa_preco_contexto=110.79,
            tarifa_fonte="sites/MLB/listing_prices",
            frete_fonte="users/shipping_options/free/contexto",
        )

        self.assertIsNone(desconto)

    def test_receivable_adjustment_requires_exact_shipping_context(self):
        payload = {
            "status": "candidate",
            "price": 110.79,
            "seller_receives": 82.15,
            "promotion_type": "SMART",
        }

        desconto = pricing._ml_ajustar_desconto_tarifa_recebivel_promocao(
            payload,
            preco_promocional=110.79,
            tarifa_ml=18.83,
            frete_ml=16.45,
            frete_exato=False,
            frete_preco_contexto=110.79,
            tarifa_exata=True,
            tarifa_preco_contexto=110.79,
            tarifa_fonte="sites/MLB/listing_prices",
            frete_fonte="users/shipping_options/free/contexto",
        )

        self.assertIsNone(desconto)

    def test_receivable_adjustment_ignores_nested_or_generic_receive_aliases(self):
        payload = {
            "status": "candidate",
            "price": 110.79,
            "offers": [
                {
                    "price": 110.79,
                    "seller_receives": 82.15,
                }
            ],
            "net_price": 82.15,
            "promotion_type": "SMART",
        }

        desconto = pricing._ml_ajustar_desconto_tarifa_recebivel_promocao(
            payload,
            preco_promocional=110.79,
            tarifa_ml=18.83,
            frete_ml=16.45,
            frete_exato=True,
            frete_preco_contexto=110.79,
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

    def test_current_promotion_without_boost_fields_is_not_rendered_as_zero(self):
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

        self.assertIsNone(extraido)
        self.assertIsNone(calculado)
        self.assertIsNone(ajustado)

    def test_same_name_campaign_never_replaces_selected_campaign_id(self):
        atual = {
            "id": "P-MLB17781036",
            "type": "SMART",
            "status": "candidate",
            "name": "Top ferramentas",
            "price": 117.43,
        }
        registros = [
            {
                "id": "P-MLB17771036",
                "type": "SMART",
                "status": "candidate",
                "name": "Top ferramentas",
                "price": 114.22,
                "boosted_offer": True,
                "discount_meli_boost_amount": 3.53,
            },
            {
                "id": "P-MLB17781036",
                "type": "SMART",
                "status": "candidate",
                "name": "Top ferramentas",
                "price": 117.43,
                "boosted_offer": True,
                "discount_meli_boost_amount": 6.64,
                "total_price_for_boosted_offer": 110.79,
            },
        ]

        with patch.object(promocoes, "_ml_obter_promocoes_item", return_value=(registros, {"ok": True})):
            resolvido, _ = promocoes._ml_resolver_raw_promocao_equivalente_para_analise(
                "000002",
                "Loja",
                {},
                "MLB5211215702",
                atual,
                "P-MLB17781036",
                "SMART",
                request_fn=lambda *args, **kwargs: None,
            )

        self.assertEqual(resolvido["id"], "P-MLB17781036")
        self.assertEqual(resolvido["discount_meli_boost_amount"], 6.64)
        self.assertEqual(resolvido["total_price_for_boosted_offer"], 110.79)

    def test_homonymous_campaign_is_ignored_when_exact_id_is_absent(self):
        atual = {
            "id": "P-MLB17781036",
            "type": "SMART",
            "status": "candidate",
            "name": "Top ferramentas",
            "price": 117.43,
        }
        registros = [
            {
                "id": "P-MLB17771036",
                "type": "SMART",
                "status": "candidate",
                "name": "Top ferramentas",
                "price": 114.22,
                "boosted_offer": True,
                "discount_meli_boost_amount": 3.53,
            }
        ]

        with patch.object(promocoes, "_ml_obter_promocoes_item", return_value=(registros, {})):
            resolvido, _ = promocoes._ml_resolver_raw_promocao_equivalente_para_analise(
                "000002",
                "Loja",
                {},
                "MLB5211215702",
                atual,
                "P-MLB17781036",
                "SMART",
                request_fn=lambda *args, **kwargs: None,
            )

        self.assertEqual(resolvido, atual)

    def test_campaign_items_discards_offer_from_another_campaign(self):
        referencia = [
            {
                "id": "P-MLB17781036",
                "type": "SMART",
                "status": "candidate",
                "ref_id": "CANDIDATE-MLB5211215702-76543970545",
            }
        ]
        payload_contaminado = {
            "results": [
                {
                    "id": "MLB5211215702",
                    "status": "started",
                    "offer_id": "OFFER-MLB5211215702-13385902378",
                    "price": 114.22,
                },
                {
                    "id": "MLB5211215702",
                    "status": "candidate",
                    "offer_id": "CANDIDATE-MLB5211215702-76543970545",
                    "price": 117.43,
                },
            ],
            "paging": {"total": 2},
        }

        def request_fn(*args, **kwargs):
            return _FakeResponse(200, payload_contaminado), {}

        with patch.object(promocoes, "_ml_obter_promocoes_item", return_value=(referencia, {})):
            resolvido, _ = promocoes._ml_obter_item_promocao_raw(
                "000002",
                "Loja",
                {},
                "P-MLB17781036",
                "SMART",
                "MLB5211215702",
                request_fn=request_fn,
            )

        self.assertEqual(resolvido["status"], "candidate")
        self.assertEqual(
            resolvido["offer_id"],
            "CANDIDATE-MLB5211215702-76543970545",
        )
        self.assertEqual(resolvido["price"], 117.43)

    def test_same_campaign_id_does_not_merge_different_offer_or_status(self):
        atual_contaminado = {
            "id": "MLB5211215702",
            "promotion_id": "P-MLB17781036",
            "type": "SMART",
            "status": "started",
            "offer_id": "OFFER-MLB5211215702-13385902378",
            "price": 114.22,
            "seller_receives": 78.35,
        }
        referencia_exata = [
            {
                "id": "P-MLB17781036",
                "type": "SMART",
                "status": "candidate",
                "ref_id": "CANDIDATE-MLB5211215702-76543970545",
                "price": 117.43,
            }
        ]

        with patch.object(promocoes, "_ml_obter_promocoes_item", return_value=(referencia_exata, {})):
            resolvido, _ = promocoes._ml_resolver_raw_promocao_equivalente_para_analise(
                "000002",
                "Loja",
                {},
                "MLB5211215702",
                atual_contaminado,
                "P-MLB17781036",
                "SMART",
                request_fn=lambda *args, **kwargs: None,
            )

        self.assertEqual(resolvido["status"], "candidate")
        self.assertEqual(
            resolvido["ref_id"],
            "CANDIDATE-MLB5211215702-76543970545",
        )
        self.assertEqual(resolvido["price"], 117.43)
        self.assertNotIn("seller_receives", resolvido)

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

    def test_smart_split_reconciles_exact_meli_amount_and_source(self):
        desconto, fonte = promocoes._calcular_desconto_ml_valor(
            ml_pct=2.4,
            seller_pct=21.1,
            preco_base=149.24,
            preco_final_ml=114.22,
            retornar_fonte=True,
        )

        self.assertEqual(desconto, 3.53)
        self.assertEqual(fonte, "seller_promotions.smart_split_reconciliado")

    def test_boost_percentage_is_monetized_from_base_price(self):
        desconto, fonte = promocoes._calcular_desconto_ml_valor(
            boost_pct=4.45,
            preco_base=149.24,
            preco_final_ml=110.79,
            retornar_fonte=True,
        )

        self.assertEqual(desconto, 6.64)
        self.assertEqual(
            fonte,
            "seller_promotions.discount_meli_boosted_percentage_calculado",
        )

    def test_meli_percentage_without_seller_percentage_remains_unknown(self):
        desconto = promocoes._calcular_desconto_ml_valor(
            ml_pct=2.4,
            preco_base=149.24,
            preco_final_ml=114.22,
        )

        self.assertIsNone(desconto)

    def test_boost_percentage_above_total_discount_is_rejected(self):
        desconto, fonte = promocoes._calcular_desconto_ml_valor(
            boost_pct=30,
            preco_base=149.24,
            preco_final_ml=114.22,
            retornar_fonte=True,
        )

        self.assertIsNone(desconto)
        self.assertEqual(fonte, "")

    def test_inconsistent_smart_percentages_remain_unknown(self):
        desconto, fonte = promocoes._calcular_desconto_ml_valor(
            ml_pct=10,
            seller_pct=10,
            preco_base=149.24,
            preco_final_ml=114.22,
            retornar_fonte=True,
        )

        self.assertIsNone(desconto)
        self.assertEqual(fonte, "")

    def test_trusted_direct_amount_wins_over_calculated_fallbacks(self):
        desconto, fonte = promocoes._calcular_desconto_ml_valor(
            desconto_atual=7.25,
            desconto_atual_confiavel=True,
            ml_pct=2.4,
            seller_pct=21.1,
            boost_pct=4.45,
            preco_base=149.24,
            preco_final_ml=110.79,
            retornar_fonte=True,
        )

        self.assertEqual(desconto, 7.25)
        self.assertEqual(fonte, "seller_promotions.valor_direto")

    def test_trusted_explicit_zero_is_preserved(self):
        desconto, fonte = promocoes._calcular_desconto_ml_valor(
            desconto_atual=0,
            desconto_atual_confiavel=True,
            boost_pct=4.45,
            preco_base=149.24,
            preco_final_ml=110.79,
            retornar_fonte=True,
        )

        self.assertEqual(desconto, 0.0)
        self.assertEqual(fonte, "seller_promotions.valor_direto")


if __name__ == "__main__":
    unittest.main()
