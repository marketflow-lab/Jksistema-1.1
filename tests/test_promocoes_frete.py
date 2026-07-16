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
        self.assertEqual(len(chamadas), 1)
        self.assertIn("/users/123/shipping_options/free", chamadas[0][0])
        self.assertEqual(chamadas[0][1]["item_price"], 116.01)
        self.assertEqual(chamadas[0][1]["dimensions"], "6x15x20,500")

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
            for preco, dimensions in (
                (71.0, "6x15x20,500"),
                (95.11, "6x15x20,500"),
                (95.11, "6x15x20,600"),
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
                        "free_shipping": True,
                    },
                )
        finally:
            for item in reversed(patches):
                item.stop()

        self.assertEqual(len(cache_keys), 3)
        self.assertEqual(len(set(cache_keys)), 3)
        self.assertTrue(all(key.startswith("v5:") for key in cache_keys))

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
