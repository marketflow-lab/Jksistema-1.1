import asyncio
import copy
import json
import unittest
from unittest.mock import patch

from backend.services import mercadolivre_legacy_core as mlcore
from backend.services import promocoes_api_analise as analysis
from backend.services import promocoes_core


ITEM_ID = "MLB999000111"
PROMO_A_ID = "SELLER-A"
PROMO_B_ID = "SMART-B"


def _valor_por_sufixo(row: dict, sufixo: str):
    for chave, valor in row.items():
        if str(chave).endswith(sufixo):
            return valor
    raise AssertionError(f"Campo com sufixo {sufixo!r} nao encontrado: {list(row)}")


class PromocoesDescontoMlFluxoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        core_peers = {
            nome: getattr(promocoes_core, nome)
            for nome in promocoes_core.__all__
            if hasattr(promocoes_core, nome)
        }
        mlcore.configure_mercadolivre_legacy_core_runtime(peers=core_peers)
        peers = dict(core_peers)
        peers.update({
            nome: getattr(mlcore, nome)
            for nome in mlcore.__all__
            if hasattr(mlcore, nome)
        })
        analysis.configure_promocoes_api_analise_runtime(peers=peers)

    def _executar_fluxo(self, raw_b: dict):
        raw_a = {
            "id": PROMO_A_ID,
            "promotion_id": PROMO_A_ID,
            "promotion_type": "SELLER_CAMPAIGN",
            "status": "started",
            "item_id": ITEM_ID,
            "price": 130.00,
            "original_price": 149.24,
            "seller_percentage": 12.89,
        }
        item = {
            "id": ITEM_ID,
            "title": "Produto prova boost",
            "price": 130.00,
            "original_price": 149.24,
            "listing_type_id": "gold_special",
            "category_id": "MLB123",
            "domain_id": "MLB-TEST",
            "shipping": {
                "free_shipping": True,
                "mode": "me2",
                "logistic_type": "cross_docking",
                "dimensions": "20x10x5,500",
            },
        }
        capturadas = []
        build_real = analysis._build_df_planilha_analise_promo

        def capturar_build(linhas, *args, **kwargs):
            capturadas.extend(copy.deepcopy(linhas))
            return build_real(linhas, *args, **kwargs)

        def listar_a(*_args, **_kwargs):
            return ([{"id": ITEM_ID}], {ITEM_ID: copy.deepcopy(raw_a)}, {"user_id": "123"})

        def listar_b(*_args, **_kwargs):
            return ([{"id": ITEM_ID}], {ITEM_ID: copy.deepcopy(raw_b)}, {"user_id": "123"})

        def detalhar(_client, _loja, cfg, campaign_id, *_args, **_kwargs):
            return copy.deepcopy(raw_b if campaign_id == PROMO_B_ID else raw_a), cfg

        def obter_taxas(_client, _loja, cfg, item_taxa, *_args, **_kwargs):
            preco = round(float(item_taxa["price"]), 2)
            tarifa = 18.83 if preco == 110.79 else (19.42 if preco == 114.22 else 22.10)
            return {
                "ad_cost": tarifa,
                "fixed_fee_amount": 0.0,
                "sale_fee_pct": 17.0,
                "listing_type_name": "Classico",
                "ad_cost_source": "sites/MLB/listing_prices",
                "ad_cost_exact_for_price": True,
                "ad_cost_price_context": preco,
            }, cfg

        def obter_fretes(*args, **_kwargs):
            cfg = args[2]
            preco_a = float(args[5])
            preco_b = float(args[6])
            return (
                {
                    "shipping_cost": 14.00,
                    "shipping_buyer_cost": 0.0,
                    "free_shipping": True,
                    "shipping_exact_for_price": True,
                    "shipping_price_context": preco_a,
                    "shipping_cost_retry_source": "users/shipping_options/free/contexto",
                },
                {
                    "shipping_cost": 16.45,
                    "shipping_buyer_cost": 0.0,
                    "free_shipping": True,
                    "shipping_exact_for_price": True,
                    "shipping_price_context": preco_b,
                    "shipping_cost_retry_source": "users/shipping_options/free/contexto",
                },
                cfg,
            )

        patches = [
            patch.object(analysis, "_obter_cfg_ml", return_value={"user_id": "123"}),
            patch.object(analysis, "_ml_listar_itens_promocao_com_raw", side_effect=listar_a),
            patch.object(analysis, "_ml_listar_itens_promocao_multistatus_com_raw", side_effect=listar_b),
            patch.object(analysis, "_ml_buscar_itens_batch", side_effect=lambda *a, **k: ([copy.deepcopy(item)], a[2])),
            patch.object(analysis, "_carregar_custos_impostos_cadastro_por_sku_loja", return_value=({}, {})),
            patch.object(analysis, "_resolver_custo_medio_por_skus", return_value=18.38),
            patch.object(analysis, "_resolver_imposto_rate_por_sku", return_value=0.24),
            patch.object(analysis, "_ml_extrair_variacoes_resumo", return_value=[]),
            patch.object(analysis, "_ml_obter_item_promocao_raw", side_effect=detalhar),
            patch.object(
                analysis,
                "_ml_resolver_raw_promocao_equivalente_para_analise",
                side_effect=lambda c, l, cfg, i, raw, *a, **k: (raw, cfg),
            ),
            patch.object(
                analysis,
                "_ml_obter_promocoes_item",
                side_effect=lambda *a, **k: ([copy.deepcopy(raw_a), copy.deepcopy(raw_b)], a[2]),
            ),
            patch.object(
                analysis,
                "_ml_obter_preco_detalhado",
                side_effect=lambda *a, **k: ({
                    "price": 130.0,
                    "standard_price": 149.24,
                    "original_price": 149.24,
                    "promotion_id": PROMO_A_ID,
                    "promotion_type": "SELLER_CAMPAIGN",
                }, a[2]),
            ),
            patch.object(analysis, "_promo_obter_fretes_por_preco", side_effect=obter_fretes),
            patch.object(analysis, "_ml_obter_taxas_anuncio", side_effect=obter_taxas),
            patch.object(analysis, "_salvar_planilha_analise_promo", return_value=None),
            patch.object(analysis, "_build_df_planilha_analise_promo", side_effect=capturar_build),
        ]
        for patcher in patches:
            patcher.start()
        try:
            resultado = asyncio.run(analysis.analisar_promo_via_api_sem_arquivos(
                loja="Loja Teste",
                promocao_a_id=PROMO_A_ID,
                promocao_a_type="SELLER_CAMPAIGN",
                margem_minima=-100,
                promocoes_b_meta=json.dumps([{
                    "promo_b_id": PROMO_B_ID,
                    "promo_b_type": "SMART",
                    "promo_texto": "Top ferramentas",
                }]),
                client_id="000002",
            ))
        finally:
            for patcher in reversed(patches):
                patcher.stop()
        return capturadas[0], resultado["analises"][0]["data"][0]

    def test_boost_oficial_sobrevive_ao_builder_e_json(self):
        raw_b = {
            "id": PROMO_B_ID,
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
            "offer_id": "OFFER-B-001",
            "price": 114.22,
            "original_price": 149.24,
            "seller_percentage": 21.1,
            "meli_percentage": 2.4,
            "boosted_offer": True,
            "discount_meli_boosted_percentage": 4.45,
            "discount_meli_boost_amount": 6.64,
            "total_price_for_boosted_offer": 110.79,
            "seller_receives": 82.15,
        }

        linha_interna, linha_json = self._executar_fluxo(raw_b)

        self.assertTrue(linha_interna[analysis.PROMO_DESCONTO_ML_CONFIAVEL_KEY])
        self.assertEqual(
            linha_interna[analysis.PROMO_DESCONTO_ML_FONTE_KEY],
            "seller_promotions.discount_meli_boost_amount",
        )
        self.assertEqual(linha_interna["preco_final_ml_display"], 82.15)
        self.assertEqual(linha_json["Desconto ML"], "R$ 6,64")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "R$ 37,18")
        self.assertEqual(linha_json["Margem ML"], "33,56%")
        self.assertNotIn(analysis.PROMO_DESCONTO_ML_CONFIAVEL_KEY, linha_json)
        self.assertNotIn(analysis.PROMO_DESCONTO_ML_FONTE_KEY, linha_json)
        self.assertNotIn("discount_meli_boost_amount", linha_json)

    def test_boost_percentual_calcula_os_664_quando_amount_nao_vem(self):
        raw_b = {
            "id": PROMO_B_ID,
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
            "offer_id": "OFFER-B-BOOST-PCT",
            "price": 114.22,
            "original_price": 149.24,
            "seller_percentage": 21.1,
            "meli_percentage": 2.4,
            "boosted_offer": True,
            "discount_meli_boosted_percentage": 4.45,
            "total_price_for_boosted_offer": 110.79,
        }

        linha_interna, linha_json = self._executar_fluxo(raw_b)

        self.assertTrue(linha_interna[analysis.PROMO_DESCONTO_ML_CONFIAVEL_KEY])
        self.assertEqual(
            linha_interna[analysis.PROMO_DESCONTO_ML_FONTE_KEY],
            "seller_promotions.discount_meli_boosted_percentage_calculado",
        )
        self.assertEqual(linha_json["Desconto ML"], "R$ 6,64")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "R$ 37,18")
        self.assertEqual(linha_json["Margem ML"], "33,56%")

    def test_smart_split_real_calcula_353_sem_campos_de_boost(self):
        raw_b = {
            "id": PROMO_B_ID,
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
            "offer_id": "CANDIDATE-MLB5211215702-76553900860",
            "price": 114.22,
            "original_price": 149.24,
            "seller_percentage": 21.1,
            "meli_percentage": 2.4,
        }

        linha_interna, linha_json = self._executar_fluxo(raw_b)

        self.assertTrue(linha_interna[analysis.PROMO_DESCONTO_ML_CONFIAVEL_KEY])
        self.assertEqual(
            linha_interna[analysis.PROMO_DESCONTO_ML_FONTE_KEY],
            "seller_promotions.smart_split_reconciliado",
        )
        self.assertEqual(linha_json["Desconto ML"], "R$ 3,53")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "R$ 36,09")
        self.assertEqual(linha_json["Margem ML"], "31,59%")

    def test_boost_com_amount_zero_nao_inventa_desconto_pelo_percentual(self):
        raw_b = {
            "id": PROMO_B_ID,
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
            "offer_id": "OFFER-B-AMOUNT-ZERO",
            "price": 110.79,
            "original_price": 149.24,
            "seller_percentage": 21.1,
            "meli_percentage": 2.4,
            "boosted_offer": True,
            "discount_meli_boosted_percentage": 4.45,
            "discount_meli_boost_amount": 0,
        }

        linha_interna, linha_json = self._executar_fluxo(raw_b)

        self.assertFalse(linha_interna[analysis.PROMO_DESCONTO_ML_CONFIAVEL_KEY])
        self.assertEqual(linha_interna[analysis.PROMO_DESCONTO_ML_FONTE_KEY], "")
        self.assertEqual(linha_json["Desconto ML"], "Não informado pela API")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "")
        self.assertEqual(linha_json["Margem ML"], "")

    def test_flag_boost_invalida_nao_inventa_desconto(self):
        raw_b = {
            "id": PROMO_B_ID,
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
            "offer_id": "OFFER-B-FLAG-INVALIDA",
            "price": 110.79,
            "original_price": 149.24,
            "seller_percentage": 21.1,
            "meli_percentage": 2.4,
            "boosted_offer": "talvez",
            "discount_meli_boosted_percentage": 4.45,
        }

        linha_interna, linha_json = self._executar_fluxo(raw_b)

        self.assertFalse(linha_interna[analysis.PROMO_DESCONTO_ML_CONFIAVEL_KEY])
        self.assertEqual(linha_interna[analysis.PROMO_DESCONTO_ML_FONTE_KEY], "")
        self.assertEqual(linha_json["Desconto ML"], "Não informado pela API")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "")
        self.assertEqual(linha_json["Margem ML"], "")

    def test_boost_percentual_zero_nao_vira_zero_confiavel(self):
        raw_b = {
            "id": PROMO_B_ID,
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
            "offer_id": "OFFER-B-PCT-ZERO",
            "price": 110.79,
            "original_price": 149.24,
            "seller_percentage": 21.1,
            "meli_percentage": 2.4,
            "boosted_offer": True,
            "discount_meli_boosted_percentage": 0,
        }

        linha_interna, linha_json = self._executar_fluxo(raw_b)

        self.assertFalse(linha_interna[analysis.PROMO_DESCONTO_ML_CONFIAVEL_KEY])
        self.assertEqual(linha_interna[analysis.PROMO_DESCONTO_ML_FONTE_KEY], "")
        self.assertEqual(linha_json["Desconto ML"], "Não informado pela API")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "")
        self.assertEqual(linha_json["Margem ML"], "")

    def test_seller_receives_incompativel_nao_substitui_fonte_calculada(self):
        raw_b = {
            "id": PROMO_B_ID,
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
            "offer_id": "OFFER-B-SELLER-RECEIVES-INVALIDO",
            "price": 114.22,
            "original_price": 149.24,
            "seller_percentage": 21.1,
            "meli_percentage": 2.4,
            "seller_receives": 1.00,
        }

        linha_interna, linha_json = self._executar_fluxo(raw_b)

        self.assertTrue(linha_interna[analysis.PROMO_DESCONTO_ML_CONFIAVEL_KEY])
        self.assertEqual(
            linha_interna[analysis.PROMO_DESCONTO_ML_FONTE_KEY],
            "seller_promotions.smart_split_reconciliado",
        )
        self.assertEqual(linha_json["Desconto ML"], "R$ 3,53")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "R$ 36,09")
        self.assertEqual(linha_json["Margem ML"], "31,59%")

    def test_desconto_ausente_permanece_nao_informado_sem_inferencia(self):
        raw_b = {
            "id": PROMO_B_ID,
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
            "offer_id": "OFFER-B-SEM-BOOST",
            "price": 110.79,
            "original_price": 149.24,
            "meli_percentage": 2.4,
        }

        linha_interna, linha_json = self._executar_fluxo(raw_b)

        self.assertFalse(linha_interna[analysis.PROMO_DESCONTO_ML_CONFIAVEL_KEY])
        self.assertEqual(linha_interna[analysis.PROMO_DESCONTO_ML_FONTE_KEY], "")
        self.assertEqual(linha_json["Desconto ML"], "Não informado pela API")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "")
        self.assertEqual(linha_json["Margem ML"], "")
        self.assertNotIn(analysis.PROMO_DESCONTO_ML_CONFIAVEL_KEY, linha_json)
        self.assertNotIn(analysis.PROMO_DESCONTO_ML_FONTE_KEY, linha_json)


if __name__ == "__main__":
    unittest.main()
