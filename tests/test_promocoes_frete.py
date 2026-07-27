import logging
import unittest
from unittest.mock import patch

from backend.services import mercadolivre_legacy_planilhas as planilhas
from backend.services import mercadolivre_legacy_pricing as pricing
from backend.services import promocoes_api_analise as analise


def _parse_float(value):
    if value is None or value == "":
        return None
    text = str(value).strip().replace("R$", "").replace("%", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    return float(text)


class _Response:
    status_code = 200

    def json(self):
        return {
            "coverage": {
                "all_country": {
                    "list_cost": 16.15,
                    "discount": {"promoted_amount": 32.30},
                }
            }
        }


class _PayloadResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class PromocoesFreteTests(unittest.TestCase):
    def test_package_dimensions_are_built_from_value_name(self):
        item = {
            "shipping": {"dimensions": None},
            "attributes": [
                {"id": "SELLER_PACKAGE_LENGTH", "value_name": "6 cm"},
                {"id": "SELLER_PACKAGE_WIDTH", "value_name": "15 cm"},
                {"id": "SELLER_PACKAGE_HEIGHT", "value_name": "20 cm"},
                {"id": "SELLER_PACKAGE_WEIGHT", "value_name": "500 g"},
            ],
        }

        contexto = pricing._ml_contexto_frete_item(item, 95.11)

        self.assertEqual(contexto["dimensions"], "6x15x20,500")

    def test_package_dimensions_convert_value_struct_units(self):
        item = {
            "shipping": {},
            "attributes": [
                {"id": "SELLER_PACKAGE_LENGTH", "value_struct": {"number": 60, "unit": "mm"}},
                {"id": "SELLER_PACKAGE_WIDTH", "value_struct": {"number": 0.15, "unit": "m"}},
                {"id": "SELLER_PACKAGE_HEIGHT", "value_struct": {"number": 20, "unit": "cm"}},
                {"id": "SELLER_PACKAGE_WEIGHT", "value_struct": {"number": 0.5, "unit": "kg"}},
            ],
        }

        contexto = pricing._ml_contexto_frete_item(item, 95.11)

        self.assertEqual(contexto["dimensions"], "6x15x20,500")

    def test_explicit_shipping_dimensions_take_precedence(self):
        item = {
            "shipping": {"dimensions": "10x11x12,700"},
            "attributes": [
                {"id": "SELLER_PACKAGE_LENGTH", "value_name": "6 cm"},
                {"id": "SELLER_PACKAGE_WIDTH", "value_name": "15 cm"},
                {"id": "SELLER_PACKAGE_HEIGHT", "value_name": "20 cm"},
                {"id": "SELLER_PACKAGE_WEIGHT", "value_name": "500 g"},
            ],
        }

        contexto = pricing._ml_contexto_frete_item(item, 95.11)

        self.assertEqual(contexto["dimensions"], "10x11x12,700")

    def test_incomplete_package_dimensions_are_not_sent(self):
        item = {
            "shipping": {"dimensions": None},
            "attributes": [
                {"id": "SELLER_PACKAGE_LENGTH", "value_name": "6 cm"},
                {"id": "SELLER_PACKAGE_WIDTH", "value_name": "15 cm"},
                {"id": "SELLER_PACKAGE_HEIGHT", "value_name": "20 cm"},
            ],
        }

        contexto = pricing._ml_contexto_frete_item(item, 95.11)

        self.assertEqual(contexto["dimensions"], "")

    def test_price_aware_free_shipping_endpoint_is_preferred(self):
        chamadas = []

        def request_fn(client_id, loja, cfg, method, url, **kwargs):
            chamadas.append((url, kwargs.get("params") or {}))
            return _Response(), cfg

        patches = [
            patch.object(pricing, "_cache_get", return_value=None, create=True),
            patch.object(pricing, "_cache_set", return_value=None, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE", {}, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE_TTL", 900, create=True),
            patch.object(pricing, "logger", logging.getLogger("test.promocoes.frete"), create=True),
        ]
        for item in patches:
            item.start()
        try:
            resultado, _cfg = pricing._ml_obter_frete_detalhado(
                "000002",
                "JK Pecas",
                {"user_id": "123"},
                "MLB4680257881",
                {"free_shipping": True, "mode": "me2", "logistic_type": "cross_docking"},
                request_fn=request_fn,
                reconsultar_zero=True,
                contexto_frete={
                    "item_price": 116.01,
                    "listing_type_id": "gold_special",
                    "condition": "new",
                    "category_id": "MLB269895",
                    "mode": "me2",
                    "logistic_type": "cross_docking",
                    "dimensions": "6x15x20,500",
                    "free_shipping": True,
                },
            )
        finally:
            for item in reversed(patches):
                item.stop()

        self.assertEqual(resultado["shipping_cost"], 16.15)
        self.assertTrue(resultado["shipping_exact_for_price"])
        self.assertEqual(resultado["shipping_price_context"], 116.01)
        self.assertEqual(resultado["shipping_cost_source_path"], "coverage.all_country.list_cost")
        self.assertEqual(len(chamadas), 1)
        self.assertIn("/users/123/shipping_options/free", chamadas[0][0])
        self.assertEqual(chamadas[0][1]["item_price"], 116.01)
        self.assertEqual(chamadas[0][1]["dimensions"], "6x15x20,500")
        self.assertEqual(chamadas[0][1]["free_shipping"], "true")

    def test_price_aware_endpoint_is_used_when_current_listing_is_not_free_shipping(self):
        chamadas = []
        payload = {
            "coverage": {
                "all_country": {
                    "list_cost": 13.25,
                    "discount": {
                        "rate": 0.5,
                        "type": "mandatory",
                        "promoted_amount": 26.50,
                    },
                }
            }
        }

        def request_fn(_client_id, _loja, cfg, _method, url, **kwargs):
            chamadas.append((url, kwargs.get("params") or {}))
            return _PayloadResponse(payload), cfg

        with (
            patch.object(pricing, "_cache_get", return_value=None, create=True),
            patch.object(pricing, "_cache_set", return_value=None, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE", {}, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE_TTL", 900, create=True),
        ):
            resultado, _cfg = pricing._ml_obter_frete_detalhado(
                "000002",
                "JK Pecas",
                {"user_id": "123"},
                "MLB2127140953",
                {"free_shipping": False, "mode": "me2", "logistic_type": "cross_docking"},
                request_fn=request_fn,
                contexto_frete={
                    "item_price": 87.66,
                    "listing_type_id": "gold_special",
                    "condition": "new",
                    "category_id": "MLB33398",
                    "mode": "me2",
                    "logistic_type": "cross_docking",
                    "free_shipping": False,
                },
            )

        self.assertEqual(len(chamadas), 1)
        self.assertIn("/users/123/shipping_options/free", chamadas[0][0])
        self.assertEqual(chamadas[0][1]["item_price"], 87.66)
        self.assertEqual(chamadas[0][1]["free_shipping"], "false")
        self.assertEqual(resultado["shipping_cost"], 13.25)
        self.assertTrue(resultado["shipping_exact_for_price"])
        self.assertTrue(resultado["free_shipping"])
        self.assertEqual(resultado["shipping_cost_source_path"], "coverage.all_country.list_cost")

    def test_contextual_non_free_quote_does_not_force_buyer_free_shipping(self):
        payload = {
            "coverage": {
                "all_country": {
                    "list_cost": 5.95,
                    "discount": {
                        "rate": 0.3,
                        "type": "none",
                        "promoted_amount": 8.50,
                    },
                }
            }
        }

        def request_fn(_client_id, _loja, cfg, _method, _url, **_kwargs):
            return _PayloadResponse(payload), cfg

        with (
            patch.object(pricing, "_cache_get", return_value=None, create=True),
            patch.object(pricing, "_cache_set", return_value=None, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE", {}, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE_TTL", 900, create=True),
        ):
            resultado, _cfg = pricing._ml_obter_frete_detalhado(
                "000002",
                "JK Pecas",
                {"user_id": "123"},
                "MLB2127140953",
                {"free_shipping": False, "mode": "me2", "logistic_type": "cross_docking"},
                request_fn=request_fn,
                contexto_frete={"item_price": 18.99, "free_shipping": False},
            )

        self.assertEqual(resultado["shipping_cost"], 5.95)
        self.assertTrue(resultado["shipping_exact_for_price"])
        self.assertFalse(resultado["free_shipping"])
        self.assertIsNone(resultado["shipping_buyer_cost"])

    def test_contextual_quote_detects_free_shipping_paid_by_meli(self):
        payload = {
            "coverage": {
                "all_country": {
                    "list_cost": 7.85,
                    "free_shipping_by_meli": True,
                    "discount": {
                        "rate": 0.3,
                        "type": "none",
                        "promoted_amount": 11.21,
                    },
                }
            }
        }

        def request_fn(_client_id, _loja, cfg, _method, _url, **_kwargs):
            return _PayloadResponse(payload), cfg

        with (
            patch.object(pricing, "_cache_get", return_value=None, create=True),
            patch.object(pricing, "_cache_set", return_value=None, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE", {}, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE_TTL", 900, create=True),
        ):
            resultado, _cfg = pricing._ml_obter_frete_detalhado(
                "000002",
                "JK Pecas",
                {"user_id": "123"},
                "MLB2127140953",
                {"free_shipping": False, "mode": "me2", "logistic_type": "cross_docking"},
                request_fn=request_fn,
                contexto_frete={"item_price": 65.17, "free_shipping": False},
            )

        self.assertEqual(resultado["shipping_cost"], 7.85)
        self.assertTrue(resultado["shipping_exact_for_price"])
        self.assertTrue(resultado["free_shipping"])
        self.assertEqual(resultado["shipping_buyer_cost"], 0.0)

    def test_inexact_cached_shipping_is_not_reused_for_a_price_quote(self):
        chamadas = []
        payload = {"coverage": {"all_country": {"list_cost": 13.25}}}

        def request_fn(_client_id, _loja, cfg, _method, url, **_kwargs):
            chamadas.append(url)
            return _PayloadResponse(payload), cfg

        with (
            patch.object(
                pricing,
                "_cache_get",
                return_value={"shipping_cost": 7.85, "shipping_exact_for_price": False},
                create=True,
            ),
            patch.object(pricing, "_cache_set", return_value=None, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE", {}, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE_TTL", 900, create=True),
        ):
            resultado, _cfg = pricing._ml_obter_frete_detalhado(
                "000002",
                "JK Pecas",
                {"user_id": "123"},
                "MLB2127140953",
                {"free_shipping": False},
                request_fn=request_fn,
                contexto_frete={"item_price": 87.66, "free_shipping": False},
            )

        self.assertEqual(len(chamadas), 1)
        self.assertEqual(resultado["shipping_cost"], 13.25)
        self.assertTrue(resultado["shipping_exact_for_price"])

    def test_shipping_cache_separates_price_and_dimensions(self):
        cache_keys = []

        def cache_get(_cache, key, _ttl):
            cache_keys.append(key)
            return None

        def request_fn(_client_id, _loja, cfg, _method, _url, **_kwargs):
            return _Response(), cfg

        patches = [
            patch.object(pricing, "_cache_get", side_effect=cache_get, create=True),
            patch.object(pricing, "_cache_set", return_value=None, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE", {}, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE_TTL", 900, create=True),
            patch.object(pricing, "logger", logging.getLogger("test.promocoes.frete.cache"), create=True),
        ]
        for item in patches:
            item.start()
        try:
            for preco, dimensions, free_shipping in (
                (71.0, "6x15x20,500", True),
                (95.11, "6x15x20,500", True),
                (95.11, "6x15x20,600", True),
                (95.11, "6x15x20,600", False),
            ):
                pricing._ml_obter_frete_detalhado(
                    "000002",
                    "JK Pecas",
                    {"user_id": "123"},
                    "MLB4321924928",
                    {"free_shipping": True, "mode": "me2", "logistic_type": "fulfillment"},
                    request_fn=request_fn,
                    reconsultar_zero=True,
                    contexto_frete={
                        "item_price": preco,
                        "listing_type_id": "gold_pro",
                        "mode": "me2",
                        "logistic_type": "fulfillment",
                        "dimensions": dimensions,
                        "free_shipping": free_shipping,
                    },
                )
        finally:
            for item in reversed(patches):
                item.stop()

        self.assertEqual(len(cache_keys), 4)
        self.assertEqual(len(set(cache_keys)), 4)
        self.assertTrue(all(key.startswith("v7:") for key in cache_keys))

    def test_contextual_shipping_prefers_authoritative_all_country_over_regional_minimum(self):
        payload = {
            "coverage": {
                "all_country": {
                    "seller_cost": 16.45,
                    "list_cost": 18.00,
                },
                "regional": {"seller_cost": 12.00},
            },
            "cost": 9.00,
        }

        def request_fn(_client_id, _loja, cfg, _method, _url, **_kwargs):
            return _PayloadResponse(payload), cfg

        with (
            patch.object(pricing, "_cache_get", return_value=None, create=True),
            patch.object(pricing, "_cache_set", return_value=None, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE", {}, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE_TTL", 900, create=True),
        ):
            resultado, _cfg = pricing._ml_obter_frete_detalhado(
                "000002",
                "JK Pecas",
                {"user_id": "123"},
                "MLB999000111",
                {"free_shipping": True, "mode": "me2", "logistic_type": "cross_docking"},
                request_fn=request_fn,
                contexto_frete={"item_price": 110.79, "free_shipping": True},
            )

        self.assertEqual(resultado["shipping_cost"], 16.45)
        self.assertTrue(resultado["shipping_exact_for_price"])
        self.assertEqual(resultado["shipping_cost_source_path"], "coverage.all_country.seller_cost")
        self.assertEqual(resultado["shipping_cost_retry_source"], "users/shipping_options/free/contexto")

    def test_contextual_shipping_fallback_is_not_exact_or_usable_for_receivable_reconciliation(self):
        payload = {"coverage": {"regional": {"seller_cost": 12.00}}}

        def request_fn(_client_id, _loja, cfg, _method, _url, **_kwargs):
            return _PayloadResponse(payload), cfg

        with (
            patch.object(pricing, "_cache_get", return_value=None, create=True),
            patch.object(pricing, "_cache_set", return_value=None, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE", {}, create=True),
            patch.object(pricing, "ML_ITEM_SHIPPING_CACHE_TTL", 900, create=True),
        ):
            resultado, _cfg = pricing._ml_obter_frete_detalhado(
                "000002",
                "JK Pecas",
                {"user_id": "123"},
                "MLB999000111",
                {"free_shipping": True, "mode": "me2", "logistic_type": "cross_docking"},
                request_fn=request_fn,
                contexto_frete={"item_price": 110.79, "free_shipping": True},
            )

        self.assertEqual(resultado["shipping_cost"], 12.00)
        self.assertFalse(resultado["shipping_exact_for_price"])
        self.assertEqual(resultado["shipping_cost_source_path"], "coverage.regional.seller_cost")

        with (
            patch.object(pricing, "_parse_float_flex", side_effect=_parse_float, create=True),
            patch.object(pricing, "_to_float_safe", side_effect=_parse_float, create=True),
            patch.object(pricing, "_ml_extrair_preco_promocao_raw", return_value=(110.79, None), create=True),
        ):
            desconto = pricing._ml_ajustar_desconto_tarifa_recebivel_promocao(
                {"price": 110.79, "seller_receives": 82.15},
                preco_promocional=110.79,
                tarifa_ml=18.83,
                frete_ml=resultado["shipping_cost"],
                frete_exato=resultado["shipping_exact_for_price"],
                frete_preco_contexto=resultado["shipping_price_context"],
                tarifa_exata=True,
                tarifa_preco_contexto=110.79,
                tarifa_fonte="sites/MLB/listing_prices",
                frete_fonte=resultado["shipping_cost_retry_source"],
            )
        self.assertIsNone(desconto)

    def test_each_promotion_price_gets_its_own_shipping_query(self):
        precos_consultados = []

        def contexto(_item, preco):
            return {"item_price": preco}

        def obter(_client_id, _loja, cfg, _item_id, _shipping, **kwargs):
            preco = kwargs["contexto_frete"]["item_price"]
            precos_consultados.append(preco)
            return {
                "shipping_cost": 16.15 if preco == 116.01 else 17.25,
                "shipping_exact_for_price": True,
            }, cfg

        with (
            patch.object(analise, "_parse_float_flex", side_effect=_parse_float, create=True),
            patch.object(analise, "_ml_contexto_frete_item", side_effect=contexto, create=True),
            patch.object(analise, "_ml_obter_frete_detalhado", side_effect=obter, create=True),
            patch.object(analise, "logger", logging.getLogger("test.promocoes.analise"), create=True),
        ):
            frete_a, frete_b, _cfg = analise._promo_obter_fretes_por_preco(
                "000002",
                "JK Pecas",
                {},
                "MLB4680257881",
                {"shipping": {"free_shipping": True}},
                118.59,
                116.01,
            )

        self.assertEqual(precos_consultados, [118.59, 116.01])
        self.assertEqual(frete_a["shipping_cost"], 17.25)
        self.assertEqual(frete_b["shipping_cost"], 16.15)

    def test_exact_shipping_is_not_recalculated_by_spreadsheet_builder(self):
        with (
            patch.object(planilhas, "_to_float_safe", side_effect=_parse_float, create=True),
            patch.object(planilhas, "_to_rate_safe", side_effect=lambda value: (_parse_float(value) or 0) / 100.0, create=True),
            patch.object(planilhas, "_flag_frete_gratis", side_effect=lambda value: str(value).lower() == "sim", create=True),
            patch.object(planilhas, "_ml_estimar_taxa_fixa_por_preco", return_value=0.0, create=True),
            patch.object(planilhas, "_recalcular_frete_por_faixa_ml", side_effect=AssertionError("frete exato nao deve ser recalculado"), create=True),
            patch.object(planilhas, "_ajustar_frete_por_preco_base", side_effect=AssertionError("frete exato nao deve ser ajustado"), create=True),
            patch.object(planilhas, "_calcular_desconto_ml_valor", return_value=None, create=True),
            patch.object(planilhas, "formatar_moeda_br", side_effect=lambda value: f"R$ {value:.2f}", create=True),
            patch.object(planilhas, "_format_pct_br", side_effect=lambda value: f"{value:.2f}%", create=True),
            patch.object(planilhas, "_normalizar_sku_mes", side_effect=lambda value: str(value), create=True),
            patch.object(planilhas, "COLUNAS_PLANILHA_ANALISE_PROMO", ["Frete ML"], create=True),
        ):
            dataframe = planilhas._build_df_planilha_analise_promo(
                [{
                    "M 21 Fixa": 118.59,
                    "M ML": 116.01,
                    "Frete": "R$ 17,25",
                    "Frete ML": "R$ 16,15",
                    "Frete Gratis": "SIM",
                    "Frete Gratis ML": "SIM",
                    "frete_ml_exato": True,
                    "Custo": "R$ 50,00",
                }]
            )

        self.assertEqual(dataframe.iloc[0]["Frete ML"], "R$ 16,15")


if __name__ == "__main__":
    unittest.main()
