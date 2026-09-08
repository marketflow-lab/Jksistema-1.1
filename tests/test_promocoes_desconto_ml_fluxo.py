import asyncio
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services import mercadolivre_legacy_core as mlcore
from backend.services import mercadolivre_legacy_promocoes as promotions
from backend.services import promocoes_api_analise as analysis
from backend.services import promocoes_api_jobs as jobs
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
        jobs.configure_promocoes_api_jobs_runtime(peers=peers)

    def _executar_fluxo(
        self,
        raw_b: dict,
        *,
        raw_b_resolvido: dict | None = None,
        price_info: dict | None = None,
        item_overrides: dict | None = None,
        fee_por_preco: dict | None = None,
        frete_b_data: dict | None = None,
        promocoes_item: list[dict] | None = None,
        custo: float = 18.38,
        imposto_rate: float = 0.24,
        com_arquivos: bool = False,
        promo_b_id: str = PROMO_B_ID,
        promo_b_type: str = "SMART",
        raw_a_overrides: dict | None = None,
        retornar_resultado: bool = False,
    ):
        raw_b_resolvido = copy.deepcopy(raw_b if raw_b_resolvido is None else raw_b_resolvido)
        price_info = copy.deepcopy(price_info or {
            "price": 130.0,
            "standard_price": 149.24,
            "original_price": 149.24,
            "promotion_id": PROMO_A_ID,
            "promotion_type": "SELLER_CAMPAIGN",
        })
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
        raw_a.update(copy.deepcopy(raw_a_overrides or {}))
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
        item.update(copy.deepcopy(item_overrides or {}))
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
            return copy.deepcopy(raw_b if campaign_id == promo_b_id else raw_a), cfg

        def obter_taxas(_client, _loja, cfg, item_taxa, *_args, **_kwargs):
            preco = round(float(item_taxa["price"]), 2)
            tarifa = 18.83 if preco == 110.79 else (19.42 if preco == 114.22 else 22.10)
            info = {
                "ad_cost": tarifa,
                "fixed_fee_amount": 0.0,
                "sale_fee_pct": 17.0,
                "listing_type_name": "Classico",
                "ad_cost_source": "sites/MLB/listing_prices",
                "ad_cost_exact_for_price": True,
                "ad_cost_price_context": preco,
            }
            info.update(copy.deepcopy((fee_por_preco or {}).get(preco, {})))
            return info, cfg

        def obter_fretes(*args, **_kwargs):
            cfg = args[2]
            preco_a = float(args[5])
            preco_b = float(args[6])
            frete_b = {
                "shipping_cost": 16.45,
                "shipping_buyer_cost": 0.0,
                "free_shipping": True,
                "shipping_exact_for_price": True,
                "shipping_price_context": preco_b,
                "shipping_cost_retry_source": "users/shipping_options/free/contexto",
            }
            frete_b.update(copy.deepcopy(frete_b_data or {}))
            return (
                {
                    "shipping_cost": 14.00,
                    "shipping_buyer_cost": 0.0,
                    "free_shipping": True,
                    "shipping_exact_for_price": True,
                    "shipping_price_context": preco_a,
                    "shipping_cost_retry_source": "users/shipping_options/free/contexto",
                },
                frete_b,
                cfg,
            )

        def obter_promocoes_item(*args, **_kwargs):
            registros = [copy.deepcopy(raw_a)]
            if promocoes_item is None:
                registros.append(copy.deepcopy(raw_b))
            else:
                registros.extend(copy.deepcopy(promocoes_item))
            return registros, args[2]

        patches = [
            patch.object(analysis, "_obter_cfg_ml", return_value={"user_id": "123"}),
            patch.object(analysis, "_ml_listar_itens_promocao_com_raw", side_effect=listar_a),
            patch.object(analysis, "_ml_listar_itens_promocao_multistatus_com_raw", side_effect=listar_b),
            patch.object(analysis, "_ml_buscar_itens_batch", side_effect=lambda *a, **k: ([copy.deepcopy(item)], a[2])),
            patch.object(analysis, "_carregar_custos_impostos_cadastro_por_sku_loja", return_value=({}, {})),
            patch.object(analysis, "_resolver_custo_medio_por_skus", return_value=custo),
            patch.object(analysis, "_resolver_imposto_rate_por_sku", return_value=imposto_rate),
            patch.object(analysis, "_ml_extrair_variacoes_resumo", return_value=[]),
            patch.object(analysis, "_ml_obter_item_promocao_raw", side_effect=detalhar),
            patch.object(
                analysis,
                "_ml_resolver_raw_promocao_equivalente_para_analise",
                side_effect=lambda c, l, cfg, i, raw, *a, **k: (copy.deepcopy(raw_b_resolvido), cfg),
            ),
            patch.object(
                analysis,
                "_ml_obter_promocoes_item",
                side_effect=obter_promocoes_item,
            ),
            patch.object(
                analysis,
                "_ml_obter_preco_detalhado",
                side_effect=lambda *a, **k: (copy.deepcopy(price_info), a[2]),
            ),
            patch.object(analysis, "_promo_obter_fretes_por_preco", side_effect=obter_fretes),
            patch.object(analysis, "_ml_obter_taxas_anuncio", side_effect=obter_taxas),
            patch.object(analysis, "_salvar_planilha_analise_promo", return_value=None),
            patch.object(analysis, "_build_df_planilha_analise_promo", side_effect=capturar_build),
            patch.object(
                analysis,
                "ler_e_tratar_arquivo",
                return_value=analysis.pd.DataFrame([{
                    "MLB": ITEM_ID,
                    "SKU": "SKU-TESTE",
                    "TITLE": "Produto prova boost",
                    "FINAL_PRICE": 148.46,
                    "DISCOUNT_PERCENTAGE": 22.789682,
                }]),
            ),
            patch.object(
                analysis,
                "_extrair_mapa_promocao2_arquivo",
                return_value=(
                    {ITEM_ID: {
                        "MLB": ITEM_ID,
                        "SKU": "",
                        "TÃ­tulo": "Produto prova boost",
                        "PreÃ§o Final ML": "R$ 148,46",
                        "Desconto ML": "",
                        "ML % Campanha": "22,79%",
                    }},
                    [ITEM_ID],
                ),
            ),
        ]
        for patcher in patches:
            patcher.start()
        try:
            meta = {
                "promo_b_id": promo_b_id,
                "promo_b_type": promo_b_type,
                "promo_texto": "Top ferramentas",
            }
            if com_arquivos:
                class UploadTeste:
                    filename = "promocao.xlsx"

                    async def read(self):
                        return b"conteudo substituido pelo mock"

                meta["arquivo_nome"] = UploadTeste.filename
                resultado = asyncio.run(analysis.analisar_promo_via_api_com_arquivos(
                    loja="Loja Teste",
                    promocao_a_id=PROMO_A_ID,
                    promocao_a_type="SELLER_CAMPAIGN",
                    margem_minima=-100,
                    promocoes_b_meta=json.dumps([meta]),
                    files=[UploadTeste()],
                    client_id="000002",
                ))
            else:
                resultado = asyncio.run(analysis.analisar_promo_via_api_sem_arquivos(
                    loja="Loja Teste",
                    promocao_a_id=PROMO_A_ID,
                    promocao_a_type="SELLER_CAMPAIGN",
                    margem_minima=-100,
                    promocoes_b_meta=json.dumps([meta]),
                    client_id="000002",
                ))
        finally:
            for patcher in reversed(patches):
                patcher.stop()
        if retornar_resultado:
            return resultado
        return capturadas[0], resultado["analises"][0]["data"][0]

    def test_resultado_zero_explica_filtro_sem_liberar_participacoes(self):
        raw_b = {
            "id": PROMO_B_ID, "type": "SMART", "status": "started",
            "price": 114.22, "original_price": 149.24,
        }
        for com_arquivos in (False, True):
            with self.subTest(com_arquivos=com_arquivos):
                resultado = self._executar_fluxo(
                    raw_b,
                    raw_a_overrides={"price": 149.24, "seller_percentage": 0},
                    retornar_resultado=True,
                    com_arquivos=com_arquivos,
                )
                campanha = resultado["analises"][0]
                self.assertEqual(campanha["data"], [])
                self.assertEqual(campanha["total"], 0)
                self.assertEqual(campanha["diagnostico"], {
                    "candidatos": 1, "linhas_montadas": 1,
                    "status_ok": 1, "pct_fixa_ok": 0,
                    "excluidos_status": 0, "excluidos_pct_fixa": 1,
                    "total_exibido": 0,
                })
                self.assertIn("% Fixa", campanha["motivo_sem_resultados"])
                self.assertIn("zerado", campanha["motivo_sem_resultados"])
                participacoes = jobs._promo_automacao_montar_participacoes(resultado, "Loja Teste")
                self.assertEqual(participacoes["promocoes"], [])

    def test_diagnostico_preserva_linha_financeira_quando_resultado_valido(self):
        raw_b = {
            "id": PROMO_B_ID, "type": "SMART", "status": "started",
            "price": 114.22, "original_price": 149.24,
        }
        for com_arquivos in (False, True):
            with self.subTest(com_arquivos=com_arquivos):
                _interna, esperada = self._executar_fluxo(raw_b, com_arquivos=com_arquivos)
                resultado = self._executar_fluxo(raw_b, com_arquivos=com_arquivos, retornar_resultado=True)
                campanha = resultado["analises"][0]
                self.assertEqual(campanha["data"], [esperada])
                self.assertEqual(campanha["motivo_sem_resultados"], "")
                self.assertEqual(campanha["diagnostico"]["total_exibido"], 1)

    def test_diagnostico_preserva_criterios_e_ordem_sem_mutar_linhas(self):
        linhas = [
            {"MLB": "A", "Status": "Ativo", "% Fixa": "0%"},
            {"MLB": "B", "Status": "Ativo", "% Fixa": "12%", "Decisao": "Nao participar"},
            {"MLB": "C", "Status": "Encerrado", "% Fixa": "5%"},
            {"MLB": "D", "Status": "Programada", "% Fixa": ""},
            {"MLB": "E", "Status": "Elegivel", "% Fixa": "-2%"},
        ]
        original = copy.deepcopy(linhas)
        selecionadas, diagnostico, motivo = analysis._promo_filtrar_resultado_com_diagnostico(linhas, 5)
        self.assertEqual(selecionadas, [linhas[1], linhas[3], linhas[4]])
        self.assertEqual(linhas, original)
        self.assertIs(selecionadas[0], linhas[1])
        self.assertEqual(diagnostico["excluidos_status"], 1)
        self.assertEqual(diagnostico["excluidos_pct_fixa"], 1)
        self.assertEqual(motivo, "")

    @staticmethod
    def _contexto_tarifa_smart_split():
        raw = {
            "id": "P-MLB17753016",
            "promotion_id": "P-MLB17753016",
            "type": "SMART",
            "status": "started",
            "ref_id": "OFFER-MLB4407660734-13520914530",
            "price": 147.34,
            "original_price": 192.28,
            "seller_percentage": 20.4,
            "meli_percentage": 3.0,
        }
        fee = {
            "ad_cost": 22.10,
            "ad_cost_source": "sites/MLB/listing_prices",
            "ad_cost_exact_for_price": True,
            "ad_cost_price_context": 147.34,
        }
        shipping = {
            "shipping_cost": 16.45,
            "shipping_exact_for_price": True,
            "shipping_price_context": 147.34,
        }
        return raw, fee, shipping

    @staticmethod
    def _candidate_sem_identidade_publica_contextualizado():
        return {
            "id": ITEM_ID,
            "item_id": ITEM_ID,
            "status": "candidate",
            "ref_id": f"CANDIDATE-{ITEM_ID}-17903092",
            "price": 80.00,
            "original_price": 100.00,
            "seller_percentage": 18.16,
            "meli_percentage": 1.84,
            "_jk_campaign_id_consultado": "P-MLB17903092",
            "_jk_promotion_type_consultado": "SMART",
            "_jk_item_id_consultado": ITEM_ID,
            "_jk_contexto_promocao_confirmado": True,
        }

    @staticmethod
    def _confirmar_contexto_candidate(raw: dict, campaign_id: str, promotion_type: str = "SMART"):
        contextualizado = copy.deepcopy(raw)
        contextualizado["_jk_campaign_id_consultado"] = campaign_id
        contextualizado["_jk_promotion_type_consultado"] = promotion_type
        contextualizado["_jk_item_id_consultado"] = ITEM_ID
        contextualizado["_jk_contexto_promocao_confirmado"] = True
        return contextualizado

    def test_adapter_anota_contexto_da_consulta_no_candidate_sem_id_e_tipo(self):
        campaign_id = "P-MLB17903092"
        promotion_type = "SMART"
        offer_id = f"CANDIDATE-{ITEM_ID}-17903092"
        referencia = [{
            "id": campaign_id,
            "type": promotion_type,
            "status": "candidate",
            "ref_id": offer_id,
        }]
        candidate_sem_identidade = {
            "id": ITEM_ID,
            "item_id": ITEM_ID,
            "status": "candidate",
            "ref_id": offer_id,
            "price": 80.00,
            "original_price": 100.00,
            "seller_percentage": 18.16,
            "meli_percentage": 1.84,
        }

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "results": [copy.deepcopy(candidate_sem_identidade)],
                    "paging": {"total": 1},
                }

        def request_fn(*_args, **_kwargs):
            return FakeResponse(), {"user_id": "123"}

        with patch.object(
            promotions,
            "_ml_obter_promocoes_item",
            return_value=(referencia, {"user_id": "123"}),
        ):
            resolvido, _cfg = promotions._ml_obter_item_promocao_raw(
                "000002",
                "Loja Teste",
                {"user_id": "123"},
                campaign_id,
                promotion_type,
                ITEM_ID,
                request_fn=request_fn,
            )

        self.assertNotIn("promotion_id", resolvido)
        self.assertNotIn("promotion_type", resolvido)
        self.assertNotIn("type", resolvido)
        self.assertEqual(resolvido["_jk_campaign_id_consultado"], campaign_id)
        self.assertEqual(resolvido["_jk_promotion_type_consultado"], promotion_type)
        self.assertEqual(resolvido["_jk_item_id_consultado"], ITEM_ID)
        self.assertTrue(resolvido["_jk_contexto_promocao_confirmado"])

    def test_adapter_nao_carimba_candidate_com_campanha_ou_tipo_divergente(self):
        campaign_id = "P-MLB17903092"
        promotion_type = "SMART"
        offer_id = f"CANDIDATE-{ITEM_ID}-17903092"
        referencia = [{
            "id": campaign_id,
            "type": promotion_type,
            "status": "candidate",
            "ref_id": offer_id,
        }]
        candidate_base = {
            "id": ITEM_ID,
            "item_id": ITEM_ID,
            "status": "candidate",
            "ref_id": offer_id,
            "price": 80.00,
            "original_price": 100.00,
            "seller_percentage": 18.16,
            "meli_percentage": 1.84,
        }

        class FakeResponse:
            status_code = 200

            def __init__(self, candidate):
                self.candidate = copy.deepcopy(candidate)

            def json(self):
                return {
                    "results": [copy.deepcopy(self.candidate)],
                    "paging": {"total": 1},
                }

        for campo, valor in (
            ("promotion_id", "P-OUTRA-CAMPANHA"),
            ("type", "DEAL"),
        ):
            with self.subTest(campo=campo):
                contaminado = dict(candidate_base, **{campo: valor})

                def request_fn(*_args, **_kwargs):
                    return FakeResponse(contaminado), {"user_id": "123"}

                with patch.object(
                    promotions,
                    "_ml_obter_promocoes_item",
                    return_value=(referencia, {"user_id": "123"}),
                ):
                    resolvido, _cfg = promotions._ml_obter_item_promocao_raw(
                        "000002",
                        "Loja Teste",
                        {"user_id": "123"},
                        campaign_id,
                        promotion_type,
                        ITEM_ID,
                        request_fn=request_fn,
                    )

                self.assertEqual(resolvido, {})

    def test_adapter_rejeita_id_de_campanha_aninhado_divergente(self):
        raw = {
            "id": ITEM_ID,
            "item_id": ITEM_ID,
            "campaign": {"id": "P-OUTRA-CAMPANHA"},
            "status": "candidate",
            "ref_id": f"CANDIDATE-{ITEM_ID}-17903092",
        }
        resolvido = promotions._promo_contextualizar_entry_campanha(
            raw,
            campaign_id="P-MLB17903092",
            promotion_type_consultado="SMART",
            item_id=ITEM_ID,
            status_consultado="candidate",
            status_param_consultado="status",
        )
        self.assertEqual(resolvido, {})

    def test_adapter_aceita_status_started_da_campanha_com_item_candidate(self):
        raw = {
            "id": ITEM_ID,
            "item_id": ITEM_ID,
            "campaign": {"id": "P-MLB17903092", "status": "started"},
            "status": "candidate",
            "ref_id": f"CANDIDATE-{ITEM_ID}-17903092",
        }
        resolvido = promotions._promo_contextualizar_entry_campanha(
            raw,
            campaign_id="P-MLB17903092",
            promotion_type_consultado="SMART",
            item_id=ITEM_ID,
            status_consultado="candidate",
            status_param_consultado="status",
        )
        self.assertTrue(resolvido["_jk_contexto_promocao_confirmado"])

    def test_adapter_carimba_pricing_candidate_sem_offer_sem_liberar_acao(self):
        raw = {
            "id": ITEM_ID,
            "item_id": ITEM_ID,
            "status": "candidate",
            "price": 80.00,
            "original_price": 100.00,
            "seller_percentage": 18.16,
            "meli_percentage": 1.84,
        }
        resolvido = promotions._promo_contextualizar_entry_campanha(
            raw,
            campaign_id="P-MLB17903092",
            promotion_type_consultado="SMART",
            item_id=ITEM_ID,
            status_consultado="candidate",
            status_param_consultado="status",
        )
        self.assertTrue(resolvido["_jk_contexto_promocao_confirmado"])
        self.assertEqual(promotions._ml_promocao_raw_offer_id(resolvido), "")

    def test_consulta_item_campanha_carimba_contexto_completo_sem_identidade_publica(self):
        campaign_id = "P-MLB17903092"
        promotion_type = "SMART"
        candidate = {
            "id": ITEM_ID,
            "item_id": ITEM_ID,
            "status": "candidate",
            "ref_id": f"CANDIDATE-{ITEM_ID}-17903092",
            "price": 80.00,
            "_jk_campaign_id_consultado": "P-FORJADA",
            "nested": {
                "preservar": "sim",
                "lista": [{"_jk_injetado": "remover"}],
            },
        }

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"results": [copy.deepcopy(candidate)]}

        with patch.object(
            promotions,
            "_ml_api_request",
            return_value=(FakeResponse(), {"user_id": "123"}),
        ) as request_mock:
            resultado, _cfg = promotions._promo_consultar_item_na_campanha(
                "000002",
                "Loja Teste",
                {"user_id": "123"},
                campaign_id,
                promotion_type,
                ITEM_ID,
            )

        self.assertTrue(resultado["success"])
        self.assertTrue(resultado["found"])
        self.assertEqual(resultado["status"], "candidate")
        entry = resultado["entry"]
        self.assertNotIn("promotion_id", entry)
        self.assertNotIn("promotion_type", entry)
        self.assertNotIn("type", entry)
        self.assertEqual(entry["_jk_campaign_id_consultado"], campaign_id)
        self.assertEqual(entry["_jk_promotion_type_consultado"], promotion_type)
        self.assertEqual(entry["_jk_item_id_consultado"], ITEM_ID)
        self.assertIs(entry["_jk_contexto_promocao_confirmado"], True)
        self.assertEqual(entry["nested"]["preservar"], "sim")
        self.assertNotIn("_jk_injetado", entry["nested"]["lista"][0])
        params_enviados = request_mock.call_args.kwargs["params"]
        self.assertEqual(params_enviados["promotion_type"], promotion_type)
        self.assertEqual(params_enviados["item_id"], ITEM_ID)

    def test_consulta_item_campanha_rejeita_identidade_ou_status_explicito_divergente(self):
        campaign_id = "P-MLB17903092"
        promotion_type = "SMART"
        candidate_base = {
            "id": ITEM_ID,
            "item_id": ITEM_ID,
            "status": "candidate",
            "ref_id": f"CANDIDATE-{ITEM_ID}-17903092",
            "price": 80.00,
        }

        class FakeResponse:
            def __init__(self, status_code, payload=None):
                self.status_code = status_code
                self.payload = copy.deepcopy(payload or {})

            def json(self):
                return copy.deepcopy(self.payload)

        casos = (
            (
                "campanha",
                dict(candidate_base, promotion_id="P-OUTRA-CAMPANHA"),
                lambda _params: True,
            ),
            (
                "tipo",
                dict(candidate_base, type="DEAL"),
                lambda _params: True,
            ),
            (
                "item",
                dict(candidate_base, item_id="MLB111222333"),
                lambda _params: True,
            ),
            (
                "status",
                dict(
                    candidate_base,
                    status="started",
                    ref_id=f"OFFER-{ITEM_ID}-17903092",
                ),
                lambda params: params.get("status") == "candidate",
            ),
        )

        for nome, contaminado, deve_responder in casos:
            with self.subTest(nome=nome):
                def request_fn(*_args, **kwargs):
                    if deve_responder(kwargs.get("params") or {}):
                        return FakeResponse(200, {"results": [contaminado]}), {"user_id": "123"}
                    return FakeResponse(404), {"user_id": "123"}

                with patch.object(promotions, "_ml_api_request", side_effect=request_fn):
                    resultado, _cfg = promotions._promo_consultar_item_na_campanha(
                        "000002",
                        "Loja Teste",
                        {"user_id": "123"},
                        campaign_id,
                        promotion_type,
                        ITEM_ID,
                    )

                self.assertFalse(resultado["found"])
                self.assertNotIn("entry", resultado)

    def test_listagem_moderna_carimba_campaign_type_item_e_confirmacao(self):
        campaign_id = "P-MLB17903092"
        promotion_type = "SMART"
        candidate = {
            "id": ITEM_ID,
            "item_id": ITEM_ID,
            "status": "candidate",
            "ref_id": f"CANDIDATE-{ITEM_ID}-17903092",
            "price": 80.00,
        }
        urls = []

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "results": [copy.deepcopy(candidate)],
                    "paging": {"total": 1},
                }

        def request_fn(_client, _loja, cfg, _method, url, **_kwargs):
            urls.append(url)
            return FakeResponse(), cfg

        with (
            patch.object(
                promotions,
                "quote_plus",
                side_effect=lambda valor: str(valor),
                create=True,
            ),
            patch.object(
                promotions,
                "_ml_api_request_com_retry",
                side_effect=request_fn,
            ),
        ):
            itens, raw_por_item, _cfg = promotions._ml_listar_itens_promocao_com_raw(
                "000002",
                "Loja Teste",
                {"user_id": "123"},
                campaign_id,
                promotion_type=promotion_type,
                buscar_detalhes=False,
                status_promocao="candidate",
                status_item_preferencial="",
            )

        self.assertEqual(itens, [{"id": ITEM_ID}])
        self.assertTrue(urls)
        self.assertIn(f"/promotions/{campaign_id}/items?", urls[0])
        self.assertIn("promotion_type=SMART", urls[0])
        self.assertIn("status=candidate", urls[0])
        entry = raw_por_item[ITEM_ID]
        self.assertEqual(entry["_jk_campaign_id_consultado"], campaign_id)
        self.assertEqual(entry["_jk_promotion_type_consultado"], promotion_type)
        self.assertEqual(entry["_jk_item_id_consultado"], ITEM_ID)
        self.assertIs(entry["_jk_contexto_promocao_confirmado"], True)

    def test_listagem_moderna_rejeita_campanha_ou_tipo_explicito_divergente(self):
        campaign_id = "P-MLB17903092"
        promotion_type = "SMART"
        candidate_base = {
            "id": ITEM_ID,
            "item_id": ITEM_ID,
            "status": "candidate",
            "ref_id": f"CANDIDATE-{ITEM_ID}-17903092",
            "price": 80.00,
        }

        class FakeResponse:
            def __init__(self, status_code, payload=None):
                self.status_code = status_code
                self.payload = copy.deepcopy(payload or {})

            def json(self):
                return copy.deepcopy(self.payload)

        for nome, contaminado in (
            ("campanha", dict(candidate_base, promotion_id="P-OUTRA-CAMPANHA")),
            ("tipo", dict(candidate_base, type="DEAL")),
        ):
            with self.subTest(nome=nome):
                def request_fn(_client, _loja, cfg, _method, url, **_kwargs):
                    if "promotion_type=SMART" in url:
                        return FakeResponse(
                            200,
                            {
                                "results": [contaminado],
                                "paging": {"total": 1},
                            },
                        ), cfg
                    return FakeResponse(404), cfg

                with (
                    patch.object(
                        promotions,
                        "quote_plus",
                        side_effect=lambda valor: str(valor),
                        create=True,
                    ),
                    patch.object(
                        promotions,
                        "_ml_api_request_com_retry",
                        side_effect=request_fn,
                    ),
                    patch.object(
                        promotions,
                        "logger",
                        create=True,
                    ),
                ):
                    itens, raw_por_item, _cfg = promotions._ml_listar_itens_promocao_com_raw(
                        "000002",
                        "Loja Teste",
                        {"user_id": "123"},
                        campaign_id,
                        promotion_type=promotion_type,
                        buscar_detalhes=False,
                        status_promocao="candidate",
                        status_item_preferencial="",
                    )

                self.assertEqual(itens, [])
                self.assertEqual(raw_por_item, {})

    def test_promocoes_por_item_remove_marcadores_forjados_recursivamente_sem_mutar_resposta(self):
        payload_externo = {
            "results": [{
                "id": "P-MLB17903092",
                "type": "SMART",
                "status": "candidate",
                "_jk_campaign_id_consultado": "P-FORJADA",
                "_jk_contexto_promocao_confirmado": True,
                "promotion": {
                    "name": "Campanha externa",
                    "_jk_promotion_type_consultado": "SMART",
                    "offers": [{
                        "ref_id": f"CANDIDATE-{ITEM_ID}-17903092",
                        "_jk_item_id_consultado": ITEM_ID,
                        "meta": {
                            "seguro": "preservar",
                            "_jk_injetado": "remover",
                        },
                    }],
                },
            }],
        }
        snapshot_externo = copy.deepcopy(payload_externo)

        class FakeResponse:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return payload_externo

        def request_fn(_client, _loja, cfg, *_args, **_kwargs):
            return FakeResponse(), cfg

        def afirmar_sem_marcador_interno(valor):
            if isinstance(valor, dict):
                for chave, filho in valor.items():
                    self.assertFalse(str(chave).startswith("_jk_"), chave)
                    afirmar_sem_marcador_interno(filho)
            elif isinstance(valor, list):
                for filho in valor:
                    afirmar_sem_marcador_interno(filho)

        with (
            patch.object(
                promotions,
                "ML_ITEM_PROMOTIONS_CACHE",
                {},
                create=True,
            ),
            patch.object(promotions, "_cache_set") as cache_set_mock,
        ):
            resultado, _cfg = promotions._ml_obter_promocoes_item(
                "000002",
                "Loja Teste",
                {"user_id": "123"},
                ITEM_ID,
                request_fn=request_fn,
                force_refresh=True,
            )

        afirmar_sem_marcador_interno(resultado)
        self.assertEqual(
            resultado[0]["promotion"]["offers"][0]["meta"]["seguro"],
            "preservar",
        )
        self.assertEqual(payload_externo, snapshot_externo)
        self.assertIn("_jk_campaign_id_consultado", payload_externo["results"][0])
        cache_set_mock.assert_called_once()
        afirmar_sem_marcador_interno(cache_set_mock.call_args.args[2])

    def test_promocoes_por_item_tambem_sanitiza_cache_sem_mutar_objeto_cacheado(self):
        cache_contaminado = [{
            "id": "P-MLB17903092",
            "_jk_contexto_promocao_confirmado": True,
            "promotion": {
                "_jk_campaign_id_consultado": "P-FORJADA",
                "offers": [{
                    "ref_id": f"CANDIDATE-{ITEM_ID}-17903092",
                    "meta": {
                        "preservar": "sim",
                        "_jk_injetado": "remover",
                    },
                }],
            },
        }]
        snapshot_cache = copy.deepcopy(cache_contaminado)

        def request_nao_deve_ser_chamado(*_args, **_kwargs):
            raise AssertionError("cache valido nao deve consultar a API")

        with (
            patch.object(
                promotions,
                "ML_ITEM_PROMOTIONS_CACHE",
                {},
                create=True,
            ),
            patch.object(
                promotions,
                "ML_ITEM_PROMOTIONS_CACHE_TTL",
                300,
                create=True,
            ),
            patch.object(
                promotions,
                "_cache_get",
                return_value=cache_contaminado,
            ),
        ):
            resultado, _cfg = promotions._ml_obter_promocoes_item(
                "000002",
                "Loja Teste",
                {"user_id": "123"},
                ITEM_ID,
                request_fn=request_nao_deve_ser_chamado,
            )

        self.assertEqual(cache_contaminado, snapshot_cache)
        self.assertNotIn("_jk_contexto_promocao_confirmado", resultado[0])
        self.assertNotIn(
            "_jk_campaign_id_consultado",
            resultado[0]["promotion"],
        )
        meta = resultado[0]["promotion"]["offers"][0]["meta"]
        self.assertEqual(meta["preservar"], "sim")
        self.assertNotIn("_jk_injetado", meta)

    def test_candidate_contextualizado_calcula_tarifa_frete_liquido_e_acao_exatos(self):
        candidate = self._candidate_sem_identidade_publica_contextualizado()

        linha_interna, linha_json = self._executar_fluxo(
            candidate,
            raw_b_resolvido=candidate,
            price_info={
                "price": 80.00,
                "standard_price": 100.00,
                "original_price": 100.00,
                "promotion_id": PROMO_A_ID,
                "promotion_type": "SELLER_CAMPAIGN",
                "price_source": "item",
            },
            item_overrides={"price": 80.00, "original_price": 100.00},
            fee_por_preco={80.00: {"ad_cost": 16.61}},
            frete_b_data={"shipping_cost": 18.85},
            custo=20.00,
            imposto_rate=0.10,
            promo_b_id="P-MLB17903092",
            promo_b_type="SMART",
        )

        self.assertTrue(linha_interna[analysis.PROMO_DESCONTO_ML_CONFIAVEL_KEY])
        self.assertEqual(
            linha_interna[analysis.PROMO_DESCONTO_ML_FONTE_KEY],
            "seller_promotions.smart_split_reconciliado",
        )
        self.assertEqual(linha_interna["Desconto ML"], "R$ 1,84")
        self.assertTrue(linha_interna[analysis.PROMO_TARIFA_ML_EXATA_KEY])
        self.assertEqual(
            linha_interna[analysis.PROMO_TARIFA_ML_FONTE_KEY],
            "listing_prices.menos_coparticipacao_ml_smart",
        )
        self.assertEqual(linha_interna["Tarifa ML"], "R$ 14,77")
        self.assertTrue(linha_interna["frete_ml_exato"])
        self.assertEqual(linha_interna["Frete ML"], "R$ 18,85")
        self.assertEqual(_valor_por_sufixo(linha_interna, "quido ML"), "R$ 18,38")
        self.assertEqual(linha_interna["Margem ML"], "22,98%")
        self.assertTrue(linha_interna["action_financeiro_exato"])
        self.assertEqual(linha_interna["action_tarifa_ml"], 14.77)
        self.assertEqual(linha_interna["action_valor_liquido_ml"], 18.38)
        self.assertAlmostEqual(linha_interna["action_margem_ml"], 22.975, places=6)

        self.assertEqual(linha_json["Tarifa ML"], "R$ 14,77")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "R$ 18,38")
        self.assertEqual(linha_json["Margem ML"], "22,98%")
        self.assertTrue(linha_json["action_financeiro_exato"])

    def test_candidate_contextualizado_sem_offer_calcula_pricing_mas_bloqueia_acao(self):
        candidate = self._candidate_sem_identidade_publica_contextualizado()
        candidate.pop("ref_id", None)

        linha_interna, linha_json = self._executar_fluxo(
            candidate,
            raw_b_resolvido=candidate,
            price_info={
                "price": 80.00,
                "standard_price": 100.00,
                "original_price": 100.00,
                "promotion_id": PROMO_A_ID,
                "promotion_type": "SELLER_CAMPAIGN",
                "price_source": "item",
            },
            item_overrides={"price": 80.00, "original_price": 100.00},
            fee_por_preco={80.00: {"ad_cost": 16.61}},
            frete_b_data={"shipping_cost": 18.85},
            custo=20.00,
            imposto_rate=0.10,
            promo_b_id="P-MLB17903092",
            promo_b_type="SMART",
        )

        self.assertEqual(linha_json["Tarifa ML"], "R$ 14,77")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "R$ 18,38")
        self.assertEqual(linha_json["Margem ML"], "22,98%")
        self.assertFalse(linha_interna["action_financeiro_exato"])
        self.assertFalse(linha_json["action_financeiro_exato"])
        self.assertEqual(
            next(valor for chave, valor in linha_json.items() if str(chave).startswith("A")),
            "Nao participar",
        )

    def test_candidate_contextualizado_falha_fechado_em_mismatch_de_campanha_ou_tipo(self):
        candidate = self._candidate_sem_identidade_publica_contextualizado()
        shipping = {
            "shipping_cost": 18.85,
            "shipping_exact_for_price": True,
            "shipping_price_context": 80.00,
        }
        fee = {
            "ad_cost": 16.61,
            "ad_cost_source": "sites/MLB/listing_prices",
            "ad_cost_exact_for_price": True,
            "ad_cost_price_context": 80.00,
        }

        def calcular(raw):
            with (
                patch.object(
                    analysis,
                    "_promo_obter_fretes_por_preco",
                    return_value=(copy.deepcopy(shipping), copy.deepcopy(shipping), {"user_id": "123"}),
                ),
                patch.object(
                    analysis,
                    "_ml_obter_taxas_anuncio",
                    return_value=(copy.deepcopy(fee), {"user_id": "123"}),
                ),
            ):
                resultado, _cfg = analysis._promo_obter_contexto_financeiro_acao(
                    "000002",
                    "Loja Teste",
                    {"user_id": "123"},
                    ITEM_ID,
                    {
                        "id": ITEM_ID,
                        "price": 80.00,
                        "category_id": "MLB123",
                        "listing_type_id": "gold_special",
                        "shipping": {"mode": "me2", "logistic_type": "cross_docking"},
                    },
                    raw,
                    "P-MLB17903092",
                    100.00,
                    80.00,
                    20.00,
                    20.00,
                    0.10,
                    promotion_type_esperado="SMART",
                )
                return resultado

        with self.subTest("campanha_explicita_divergente"):
            campanha_divergente = dict(candidate, promotion_id="P-OUTRA-CAMPANHA")
            resultado = calcular(campanha_divergente)
            self.assertFalse(resultado["exato"])
            self.assertIsNone(resultado["tarifa"])
            self.assertIsNone(resultado["valor_liquido"])
            self.assertIsNone(resultado["margem"])

        with self.subTest("tipo_explicito_divergente"):
            tipo_divergente = dict(candidate, type="DEAL")
            resultado = calcular(tipo_divergente)
            self.assertFalse(resultado["exato"])
            self.assertIsNone(resultado["tarifa"])
            self.assertIsNone(resultado["valor_liquido"])
            self.assertIsNone(resultado["margem"])

    def test_financeiro_acao_recusa_raw_publico_sem_contexto_confirmado(self):
        raw_publico = {
            "promotion_id": "P-MLB17903092",
            "promotion_type": "SMART",
            "item_id": ITEM_ID,
            "status": "candidate",
            "offer_id": f"CANDIDATE-{ITEM_ID}-PUBLICO",
            "price": 80.00,
            "original_price": 100.00,
            "sale_fee_amount": 8.00,
        }
        with (
            patch.object(
                analysis,
                "_promo_obter_fretes_por_preco",
                return_value=(
                    {},
                    {
                        "shipping_cost": 5.00,
                        "shipping_exact_for_price": True,
                        "shipping_price_context": 80.00,
                    },
                    {"user_id": "123"},
                ),
            ),
            patch.object(
                analysis,
                "_ml_obter_taxas_anuncio",
                return_value=({"ad_cost": 8.00}, {"user_id": "123"}),
            ),
        ):
            resultado, _cfg = analysis._promo_obter_contexto_financeiro_acao(
                "000002",
                "Loja Teste",
                {"user_id": "123"},
                ITEM_ID,
                {"id": ITEM_ID, "price": 80.00},
                raw_publico,
                "P-MLB17903092",
                100.00,
                80.00,
                20.00,
                20.00,
                0.10,
                promotion_type_esperado="SMART",
            )
        self.assertFalse(resultado["exato"])
        self.assertIsNone(resultado["tarifa"])

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
            "offer_id": f"CANDIDATE-{ITEM_ID}-BOOST-PCT",
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
        self.assertEqual(linha_json["Tarifa ML"], "R$ 12,19")
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
        self.assertEqual(linha_json["Tarifa ML"], "R$ 15,89")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "R$ 36,09")
        self.assertEqual(linha_json["Margem ML"], "31,59%")

    def test_boost_false_preserva_coparticipacao_regular_do_split_smart(self):
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
            "boosted_offer": False,
        }

        linha_interna, linha_json = self._executar_fluxo(raw_b)

        self.assertTrue(linha_interna[analysis.PROMO_DESCONTO_ML_CONFIAVEL_KEY])
        self.assertEqual(
            linha_interna[analysis.PROMO_DESCONTO_ML_FONTE_KEY],
            "seller_promotions.smart_split_reconciliado",
        )
        self.assertEqual(linha_json["Desconto ML"], "R$ 3,53")
        self.assertEqual(linha_json["Tarifa ML"], "R$ 15,89")
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
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "")
        self.assertEqual(linha_json["Margem ML"], "")

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

    def test_tarifa_liquida_exata_preserva_margem_sem_inventar_beneficio(self):
        raw_b = {
            "id": PROMO_B_ID,
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
            "offer_id": "OFFER-B-TARIFA-LIQUIDA",
            "price": 110.79,
            "original_price": 149.24,
            "sale_fee_amount": 12.19,
        }

        linha_interna, linha_json = self._executar_fluxo(raw_b)

        self.assertFalse(linha_interna[analysis.PROMO_DESCONTO_ML_CONFIAVEL_KEY])
        self.assertTrue(linha_interna[analysis.PROMO_TARIFA_ML_EXATA_KEY])
        self.assertEqual(linha_json["Desconto ML"], analysis.PROMO_DESCONTO_ML_NAO_INFORMADO)
        self.assertEqual(linha_json["Tarifa ML"], "R$ 12,19")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "R$ 37,18")
        self.assertEqual(linha_json["Margem ML"], "33,56%")

    def test_tarifa_atual_nao_e_aplicada_sobre_preco_antigo_de_arquivo(self):
        tarifa, exata, _fonte = analysis._promo_resolver_tarifa_ml_cobrada(
            {
                "price": 147.34,
                "sale_fee_amount": 25.22,
                "seller_receives": 112.50,
            },
            148.46,
            {
                "ad_cost": 22.27,
                "ad_cost_exact_for_price": True,
                "ad_cost_price_context": 148.46,
            },
            {
                "shipping_cost": 9.62,
                "shipping_exact_for_price": True,
                "shipping_price_context": 148.46,
            },
            5.71,
            True,
        )

        self.assertEqual(tarifa, 22.27)
        self.assertFalse(exata)

    def test_listing_prices_menos_smart_split_confiavel_resolve_tarifa_exata(self):
        raw, fee, shipping = self._contexto_tarifa_smart_split()

        tarifa, exata, fonte = analysis._promo_resolver_tarifa_ml_cobrada(
            raw,
            147.34,
            fee,
            shipping,
            5.71,
            True,
            "seller_promotions.smart_split_reconciliado",
        )

        self.assertEqual(tarifa, 16.39)
        self.assertTrue(exata)
        self.assertEqual(fonte, "listing_prices.menos_coparticipacao_ml_smart")

    def test_listing_prices_nao_desconta_smart_split_com_fonte_nao_confiavel(self):
        raw, fee, shipping = self._contexto_tarifa_smart_split()

        tarifa, exata, fonte = analysis._promo_resolver_tarifa_ml_cobrada(
            raw,
            147.34,
            fee,
            shipping,
            5.71,
            True,
            "seller_promotions.meli_percentage",
        )

        self.assertEqual(tarifa, 22.10)
        self.assertFalse(exata)
        self.assertNotEqual(fonte, "listing_prices.menos_coparticipacao_ml_smart")

    def test_listing_prices_nao_desconta_smart_split_em_contexto_de_preco_divergente(self):
        raw, fee, shipping = self._contexto_tarifa_smart_split()
        fee["ad_cost_price_context"] = 148.46

        tarifa, exata, fonte = analysis._promo_resolver_tarifa_ml_cobrada(
            raw,
            147.34,
            fee,
            shipping,
            5.71,
            True,
            "seller_promotions.smart_split_reconciliado",
        )

        self.assertEqual(tarifa, 22.10)
        self.assertFalse(exata)
        self.assertNotEqual(fonte, "listing_prices.menos_coparticipacao_ml_smart")

    def test_listing_prices_nao_desconta_smart_split_maior_que_a_tarifa(self):
        raw, fee, shipping = self._contexto_tarifa_smart_split()

        tarifa, exata, fonte = analysis._promo_resolver_tarifa_ml_cobrada(
            raw,
            147.34,
            fee,
            shipping,
            22.11,
            True,
            "seller_promotions.smart_split_reconciliado",
        )

        self.assertEqual(tarifa, 22.10)
        self.assertFalse(exata)
        self.assertNotEqual(fonte, "listing_prices.menos_coparticipacao_ml_smart")

    def test_tarifa_liquida_do_raw_nao_recebe_smart_split_duas_vezes(self):
        raw, fee, shipping = self._contexto_tarifa_smart_split()
        raw["sale_fee_amount"] = 16.39

        tarifa, exata, fonte = analysis._promo_resolver_tarifa_ml_cobrada(
            raw,
            147.34,
            fee,
            shipping,
            5.71,
            True,
            "seller_promotions.smart_split_reconciliado",
        )

        self.assertEqual(tarifa, 16.39)
        self.assertTrue(exata)
        self.assertEqual(fonte, "seller_promotions.sale_fee_amount")

    def test_fluxo_caso_real_usa_oferta_ativa_e_tarifa_liquida_uma_vez(self):
        active = {
            "id": "P-AGOSTO-TOP-ITENS",
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "started",
            "item_id": ITEM_ID,
            "offer_id": "OFFER-MLB4407660734-13520914530",
            "price": 147.34,
            "original_price": 192.28,
            "seller_percentage": 20.4,
            "meli_percentage": 2.97,
            "boosted_offer": True,
            "discount_meli_boost_amount": 5.71,
            "total_price_for_boosted_offer": 147.34,
            "sale_fee_amount": 25.22,
            "seller_receives": 112.50,
        }
        candidate = {
            "id": "P-AGOSTO-TOP-ITENS",
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
            "ref_id": "CANDIDATE-MLB4407660734-77013259494",
            "price": 148.46,
            "original_price": 192.28,
            "seller_percentage": 21.0,
            "meli_percentage": 1.79,
            "boosted_offer": True,
            "discount_meli_boost_amount": 8.83,
            "total_price_for_boosted_offer": 148.46,
        }

        linha_interna, linha_json = self._executar_fluxo(
            candidate,
            raw_b_resolvido=candidate,
            promocoes_item=[candidate, active],
            price_info={
                "price": 147.34,
                "standard_price": 192.28,
                "original_price": 192.28,
                "promotion_id": "OFFER-MLB4407660734-13520914530",
                "promotion_type": "marketplace_campaign",
                "price_source": "sale_price",
            },
            item_overrides={"price": 147.34, "original_price": 192.28},
            fee_por_preco={147.34: {"ad_cost": 22.10}},
            frete_b_data={"shipping_cost": 9.62},
            custo=53.92,
        )

        self.assertEqual(linha_interna["deal_price"], 147.34)
        self.assertEqual(linha_interna["preco_promocional_ml"], 147.34)
        self.assertEqual(linha_interna["offer_id"], "CANDIDATE-MLB4407660734-77013259494")
        self.assertEqual(linha_interna["action_offer_id"], "CANDIDATE-MLB4407660734-77013259494")
        self.assertEqual(linha_interna["pricing_offer_id"], "OFFER-MLB4407660734-13520914530")
        self.assertEqual(linha_interna[analysis.PROMO_TARIFA_ML_EXATA_KEY], True)
        self.assertEqual(_valor_por_sufixo(linha_json, "o Final ML"), "R$ 147,34")
        self.assertEqual(linha_json["ML % Campanha"], "23,37%")
        self.assertEqual(linha_json["Desconto ML"], "R$ 5,71")
        self.assertEqual(linha_json["Tarifa ML"], "R$ 25,22")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "R$ 23,22")
        self.assertEqual(linha_json["Margem ML"], "15,76%")

    def test_fluxo_caso_live_separa_candidato_da_oferta_ativa_mais_barata(self):
        candidate = {
            "id": "P-MLB17919018",
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
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

        linha_interna, linha_json = self._executar_fluxo(
            candidate,
            raw_b_resolvido=candidate,
            promocoes_item=[candidate, active],
            price_info={
                "price": 147.34,
                "standard_price": 192.28,
                "original_price": 192.28,
                "promotion_id": "OFFER-MLB4407660734-13520914530",
                "promotion_type": "SMART",
                "price_source": "sale_price",
            },
            item_overrides={"price": 147.34, "original_price": 192.28},
            fee_por_preco={147.34: {"ad_cost": 22.10}},
            custo=53.92,
        )

        self.assertEqual(linha_interna["deal_price"], 147.34)
        self.assertEqual(linha_interna["preco_promocional_ml"], 147.34)
        self.assertEqual(linha_interna["offer_id"], "CANDIDATE-MLB4407660734-77013259494")
        self.assertEqual(linha_interna["action_promotion_id"], PROMO_B_ID)
        self.assertEqual(linha_interna["action_offer_id"], "CANDIDATE-MLB4407660734-77013259494")
        self.assertEqual(linha_interna["action_deal_price"], 148.46)
        self.assertAlmostEqual(linha_interna["action_discount_percentage"], 22.789682, places=6)
        self.assertEqual(linha_interna["pricing_promotion_id"], "P-MLB17753016")
        self.assertEqual(linha_interna["pricing_offer_id"], "OFFER-MLB4407660734-13520914530")
        self.assertEqual(linha_interna["pricing_source"], "sale_price_active_offer")
        self.assertEqual(_valor_por_sufixo(linha_json, "o Final ML"), "R$ 147,34")
        self.assertEqual(linha_json["ML % Campanha"], "23,37%")
        self.assertEqual(linha_json["Desconto ML"], "R$ 5,71")
        self.assertTrue(linha_interna[analysis.PROMO_TARIFA_ML_EXATA_KEY])
        self.assertEqual(
            linha_interna[analysis.PROMO_TARIFA_ML_FONTE_KEY],
            "listing_prices.menos_coparticipacao_ml_smart",
        )
        self.assertEqual(linha_json["Tarifa ML"], "R$ 16,39")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "R$ 25,22")
        self.assertEqual(linha_json["Margem ML"], "17,12%")
        self.assertEqual(linha_json["action_offer_id"], "CANDIDATE-MLB4407660734-77013259494")
        self.assertEqual(linha_json["action_deal_price"], 148.46)

    def test_decisao_usa_financeiro_da_acao_quando_pricing_e_de_outra_campanha(self):
        candidate = self._confirmar_contexto_candidate({
            "id": PROMO_B_ID,
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
            "offer_id": f"CANDIDATE-{ITEM_ID}-ACTION",
            "price": 148.46,
            "original_price": 149.24,
            "sale_fee_amount": 80.00,
        }, PROMO_B_ID)
        active = {
            "id": "P-ACTIVE-PRICING",
            "promotion_id": "P-ACTIVE-PRICING",
            "promotion_type": "SMART",
            "status": "started",
            "item_id": ITEM_ID,
            "offer_id": "OFFER-ACTIVE-PRICING",
            "price": 147.34,
            "original_price": 149.24,
            "sale_fee_amount": 16.39,
        }

        linha_interna, linha_json = self._executar_fluxo(
            candidate,
            raw_b_resolvido=active,
            promocoes_item=[candidate, active],
            price_info={
                "price": 147.34,
                "standard_price": 149.24,
                "original_price": 149.24,
                "promotion_id": "OFFER-ACTIVE-PRICING",
                "promotion_type": "SMART",
                "price_source": "sale_price",
            },
            item_overrides={"price": 147.34, "original_price": 149.24},
            fee_por_preco={
                147.34: {"ad_cost": 22.10},
                148.46: {"ad_cost": 22.27},
            },
        )

        self.assertEqual(linha_interna["pricing_promotion_id"], "P-ACTIVE-PRICING")
        self.assertEqual(linha_interna["action_promotion_id"], PROMO_B_ID)
        self.assertEqual(linha_interna["Margem ML"], "41,24%")
        self.assertEqual(linha_interna["action_tarifa_ml"], 80.0)
        self.assertTrue(linha_interna["action_financeiro_exato"])
        self.assertLess(linha_interna["action_valor_liquido_ml"], 0)
        self.assertLess(linha_interna["action_margem_ml"], 0)
        self.assertEqual(
            next(valor for chave, valor in linha_interna.items() if str(chave).startswith("Participar ou ")),
            "Nao participar",
        )

        self.assertEqual(linha_json["action_tarifa_ml"], 80.0)
        self.assertTrue(linha_json["action_financeiro_exato"])
        self.assertLess(linha_json["action_valor_liquido_ml"], 0)
        self.assertLess(linha_json["action_margem_ml"], 0)
        self.assertEqual(
            next(valor for chave, valor in linha_json.items() if str(chave).startswith("A")),
            "Nao participar",
        )

    def test_financeiro_acao_recusa_campanha_ou_item_divergente(self):
        raw_valido = {
            "id": PROMO_B_ID,
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
            "offer_id": "CANDIDATE-ACTION",
            "price": 148.46,
            "original_price": 149.24,
            "sale_fee_amount": 16.00,
        }
        cenarios = {
            "campanha_divergente": {
                "id": "P-OUTRA-CAMPANHA",
                "promotion_id": "P-OUTRA-CAMPANHA",
            },
            "item_divergente": {
                "item_id": "MLB111222333",
            },
        }

        for nome, divergencia in cenarios.items():
            with self.subTest(nome=nome):
                raw_b = dict(raw_valido, **divergencia)
                linha_interna, linha_json = self._executar_fluxo(raw_b)

                self.assertFalse(linha_interna["action_financeiro_exato"])
                self.assertIsNone(linha_interna["action_tarifa_ml"])
                self.assertIsNone(linha_interna["action_valor_liquido_ml"])
                self.assertIsNone(linha_interna["action_margem_ml"])
                self.assertEqual(
                    next(
                        valor
                        for chave, valor in linha_interna.items()
                        if str(chave).startswith("Participar ou ")
                    ),
                    "Nao participar",
                )

                self.assertFalse(linha_json["action_financeiro_exato"])
                self.assertEqual(
                    next(valor for chave, valor in linha_json.items() if str(chave).startswith("A")),
                    "Nao participar",
                )

    def test_fluxo_com_arquivos_congela_acao_e_nao_expoe_offer_de_pricing_como_legado(self):
        candidate = {
            "id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
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
            "item_id": ITEM_ID,
            "ref_id": "OFFER-MLB4407660734-13520914530",
            "price": 147.34,
            "original_price": 192.28,
            "seller_percentage": 20.4,
            "meli_percentage": 3.0,
        }

        linha_interna, linha_json = self._executar_fluxo(
            candidate,
            raw_b_resolvido=active,
            promocoes_item=[candidate, active],
            price_info={
                "price": 147.34,
                "standard_price": 192.28,
                "original_price": 192.28,
                "promotion_id": "OFFER-MLB4407660734-13520914530",
                "promotion_type": "SMART",
                "price_source": "sale_price",
            },
            item_overrides={"price": 147.34, "original_price": 192.28},
            fee_por_preco={147.34: {"ad_cost": 22.10}},
            custo=53.92,
            com_arquivos=True,
        )

        self.assertEqual(linha_interna["deal_price"], 147.34)
        self.assertEqual(linha_interna["offer_id"], "CANDIDATE-MLB4407660734-77013259494")
        self.assertEqual(linha_interna["action_promotion_id"], PROMO_B_ID)
        self.assertEqual(linha_interna["action_offer_id"], "CANDIDATE-MLB4407660734-77013259494")
        self.assertEqual(linha_interna["action_deal_price"], 148.46)
        self.assertAlmostEqual(linha_interna["action_discount_percentage"], 22.789682, places=6)
        self.assertEqual(linha_interna["pricing_promotion_id"], "P-MLB17753016")
        self.assertEqual(linha_interna["pricing_offer_id"], "OFFER-MLB4407660734-13520914530")
        self.assertEqual(linha_interna["pricing_price"], 147.34)
        self.assertEqual(linha_interna["pricing_source"], "sale_price_active_offer")
        self.assertEqual(_valor_por_sufixo(linha_json, "o Final ML"), "R$ 147,34")
        self.assertEqual(linha_json["ML % Campanha"], "23,37%")
        self.assertEqual(linha_json["Desconto ML"], "R$ 5,71")
        self.assertEqual(linha_json["offer_id"], "CANDIDATE-MLB4407660734-77013259494")
        self.assertEqual(linha_json["action_offer_id"], "CANDIDATE-MLB4407660734-77013259494")
        self.assertEqual(linha_json["action_deal_price"], 148.46)
        self.assertEqual(linha_json["pricing_offer_id"], "OFFER-MLB4407660734-13520914530")
        self.assertEqual(linha_json["pricing_price"], 147.34)

    def test_fluxo_com_arquivos_aceita_tarifa_cobrada_exata_sem_fee_base(self):
        candidate = self._confirmar_contexto_candidate({
            "id": PROMO_B_ID,
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "candidate",
            "item_id": ITEM_ID,
            "offer_id": f"CANDIDATE-{ITEM_ID}-TARIFA-DIRETA",
            "price": 148.46,
            "original_price": 149.24,
            "sale_fee_amount": 16.00,
        }, PROMO_B_ID)

        linha_interna, linha_json = self._executar_fluxo(
            candidate,
            fee_por_preco={
                148.46: {
                    "ad_cost": None,
                    "ad_cost_source": "",
                    "ad_cost_exact_for_price": False,
                },
            },
            com_arquivos=True,
        )

        self.assertTrue(linha_interna[analysis.PROMO_TARIFA_ML_EXATA_KEY])
        self.assertEqual(linha_interna[analysis.PROMO_TARIFA_ML_FONTE_KEY], "seller_promotions.sale_fee_amount")
        self.assertEqual(linha_interna["Tarifa ML"], "R$ 16,00")
        self.assertEqual(_valor_por_sufixo(linha_interna, "quido ML"), "R$ 62,00")
        self.assertEqual(linha_interna["Margem ML"], "41,76%")
        self.assertEqual(
            next(valor for chave, valor in linha_interna.items() if str(chave).startswith("Participar ou ")),
            "Participar",
        )

        self.assertEqual(linha_json["Tarifa ML"], "R$ 16,00")
        self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "R$ 62,00")
        self.assertEqual(linha_json["Margem ML"], "41,76%")
        self.assertEqual(
            next(valor for chave, valor in linha_json.items() if str(chave).startswith("A")),
            "Participar",
        )

    def test_frontend_prioriza_contexto_de_acao_ao_confirmar_participacao(self):
        javascript = (
            Path(__file__).resolve().parents[1]
            / "static"
            / "frontend_promo"
            / "participacao.js"
        ).read_text(encoding="utf-8")

        self.assertIn("['action_offer_id', 'offer_id'", javascript)
        self.assertIn("['action_deal_price', 'deal_price'", javascript)
        self.assertIn("['action_discount_percentage', 'ML % Campanha'", javascript)
        self.assertIn("offer_id: actionOfferId", javascript)
        self.assertIn("action_financeiro_exato", javascript)

    def test_automacao_servidor_prioriza_contexto_de_acao(self):
        payload = jobs._promo_automacao_montar_participacoes(
            {
                "analises": [{
                    "promo_b_id": "P-MLB17919018",
                    "promo_b_type": "SMART",
                    "data": [{
                        "MLB": "MLB4407660734",
                        "Acao": "Participar",
                        "action_offer_id": "CANDIDATE-MLB4407660734-77013259494",
                        "offer_id": "OFFER-MLB4407660734-13520914530",
                        "action_deal_price": 148.46,
                        "deal_price": 147.34,
                        "action_discount_percentage": 22.789682,
                        "ML % Campanha": "23,37%",
                    }],
                }],
            },
            "Loja Teste",
        )

        item = payload["promocoes"][0]["items"][0]
        self.assertEqual(item["offer_id"], "CANDIDATE-MLB4407660734-77013259494")
        self.assertEqual(item["deal_price"], 148.46)
        self.assertAlmostEqual(item["discount_percentage"], 22.789682, places=6)

    def test_automacao_nao_envia_acao_com_contexto_financeiro_incompleto(self):
        payload = jobs._promo_automacao_montar_participacoes(
            {
                "analises": [{
                    "promo_b_id": "P-MLB17919018",
                    "promo_b_type": "SMART",
                    "data": [{
                        "MLB": "MLB4407660734",
                        "Acao": "Participar",
                        "action_offer_id": "CANDIDATE-MLB4407660734-77013259494",
                        "action_deal_price": 148.46,
                        "action_discount_percentage": 22.789682,
                        "action_financeiro_exato": False,
                    }],
                }],
            },
            "Loja Teste",
        )

        self.assertEqual(payload["promocoes"], [])

    def test_fluxo_bloqueia_margem_quando_frete_ou_tarifa_nao_sao_exatos(self):
        active = {
            "id": "P-AGOSTO-TOP-ITENS",
            "promotion_id": PROMO_B_ID,
            "promotion_type": "SMART",
            "status": "started",
            "item_id": ITEM_ID,
            "offer_id": "OFFER-MLB4407660734-13520914530",
            "price": 147.34,
            "original_price": 192.28,
            "boosted_offer": True,
            "discount_meli_boost_amount": 5.71,
            "total_price_for_boosted_offer": 147.34,
            "sale_fee_amount": 25.22,
        }
        price_info = {
            "price": 147.34,
            "standard_price": 192.28,
            "original_price": 192.28,
            "promotion_id": "OFFER-MLB4407660734-13520914530",
            "price_source": "sale_price",
        }

        with self.subTest("frete_inexato"):
            linha_interna, linha_json = self._executar_fluxo(
                active,
                price_info=price_info,
                item_overrides={"price": 147.34, "original_price": 192.28},
                frete_b_data={
                    "shipping_cost": 9.62,
                    "shipping_exact_for_price": False,
                },
                custo=53.92,
            )
            self.assertEqual(_valor_por_sufixo(linha_interna, "quido ML"), "")
            self.assertEqual(linha_interna["Margem ML"], "")
            self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "")
            self.assertEqual(linha_json["Margem ML"], "")

        with self.subTest("tarifa_inexata"):
            sem_tarifa_direta = dict(active)
            sem_tarifa_direta.pop("sale_fee_amount")
            linha_interna, linha_json = self._executar_fluxo(
                sem_tarifa_direta,
                price_info=price_info,
                item_overrides={"price": 147.34, "original_price": 192.28},
                fee_por_preco={147.34: {
                    "ad_cost": 22.10,
                    "ad_cost_exact_for_price": False,
                    "ad_cost_price_context": 148.46,
                }},
                frete_b_data={"shipping_cost": 9.62},
                custo=53.92,
            )
            self.assertEqual(linha_interna[analysis.PROMO_TARIFA_ML_EXATA_KEY], False)
            self.assertEqual(_valor_por_sufixo(linha_interna, "quido ML"), "")
            self.assertEqual(linha_interna["Margem ML"], "")
            self.assertEqual(_valor_por_sufixo(linha_json, "quido ML"), "")
            self.assertEqual(linha_json["Margem ML"], "")


if __name__ == "__main__":
    unittest.main()
