import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from backend.schemas.promocoes import PromoAplicarParticipacaoRequest, PromoAutomacaoConfigRequest
from backend.services import promocoes_api_analise as analise
from backend.services import promocoes_api_jobs as jobs
from backend.services import promocoes_api_participacoes as participacoes
from backend.services.promocoes_validacao import validar_promocoes_por_anuncio


CUPOM = "SELLER_COUPON_CAMPAIGN"


@pytest.mark.parametrize("tipo", ["SELLER_CAMPAIGN", "SMART", "PRICE_DISCOUNT", "DEAL", ""])
def test_campanhas_de_preco_continuam_aceitas(tipo):
    validar_promocoes_por_anuncio(tipo, [{"promo_b_type": tipo}])


@pytest.mark.parametrize("key", ["promo_b_type", "promotion_type", "promoType", "type"])
def test_cupom_rejeitado_inclusive_configuracao_serializada(key):
    with pytest.raises(HTTPException, match="cupom") as erro:
        validar_promocoes_por_anuncio("SELLER_CAMPAIGN", json.dumps([{key: " seller_coupon_campaign "}]))
    assert erro.value.status_code == 400


@pytest.mark.parametrize("tipo_a,meta", [(CUPOM, "[]"), ("SELLER_CAMPAIGN", json.dumps([{"promo_b_type": CUPOM}]))])
def test_start_rejeita_cupom_antes_de_iniciar_worker(tipo_a, meta):
    with patch.object(jobs, "_ensure_promo_worker_running") as worker:
        with pytest.raises(HTTPException, match="cupom"):
            jobs._promo_start_api_worker_job(client_id="tenant-teste", loja="Loja", promocao_a_id="A", promocao_a_type=tipo_a, promocoes_b_meta=meta)
        worker.assert_not_called()


@pytest.mark.parametrize("func", [analise.analisar_promo_via_api_sem_arquivos, analise.analisar_promo_via_api_com_arquivos, jobs.iniciar_analise_promo_via_api_com_arquivos])
def test_worker_e_arquivos_rejeitam_cupom_antes_de_consultar_loja(func):
    with patch.object(analise, "_obter_cfg_ml", create=True) as cfg:
        with pytest.raises(HTTPException, match="cupom"):
            asyncio.run(func(loja="Loja", promocao_a_id="A", promocao_a_type=CUPOM, promocoes_b_meta="[]"))
        cfg.assert_not_called()


def test_automacao_incompativel_nao_e_salva():
    req = PromoAutomacaoConfigRequest(enabled=True, promocao_a_type=CUPOM)
    with patch.object(jobs, "_promo_automacao_carregar") as carregar:
        with pytest.raises(HTTPException, match="cupom"):
            jobs.promo_automacao_salvar(req, client_id="tenant-teste")
        carregar.assert_not_called()


def test_tipo_omitido_e_identificado_pelo_registro_da_campanha():
    raw = {"type": CUPOM, "sub_type": "FIXED_PERCENTAGE", "fixed_percentage": 5, "price": 0}
    with patch.object(analise, "_obter_cfg_ml", return_value={}, create=True), patch.object(
        analise, "_ml_listar_itens_promocao_com_raw", return_value=([{"id": "ITEM"}], {"ITEM": raw}, {}), create=True
    ), patch.object(analise, "_promo_meta_contagem", return_value=None, create=True), patch.object(analise, "_carregar_custos_impostos_cadastro_por_sku_loja", create=True) as custos:
        with pytest.raises(HTTPException, match="cupom"):
            asyncio.run(analise.analisar_promo_via_api_sem_arquivos(
                loja="Loja", promocao_a_id="A", promocao_a_type="",
                promocoes_b_meta=json.dumps([{"promo_b_id": "B", "promo_b_type": "SMART"}]),
            ))
        custos.assert_not_called()


def test_promocao_b_sem_tipo_rejeita_cupom_retornado_pela_api():
    raw_a = {"type": "SELLER_CAMPAIGN", "status": "started", "price": 95}
    raw_b = {"type": CUPOM, "fixed_percentage": 5, "price": 0}
    with patch.object(analise, "_obter_cfg_ml", return_value={}, create=True), patch.object(
        analise, "_ml_listar_itens_promocao_com_raw", return_value=([{"id": "ITEM"}], {"ITEM": raw_a}, {}), create=True
    ), patch.object(
        analise, "_ml_listar_itens_promocao_multistatus_com_raw", return_value=([{"id": "ITEM"}], {"ITEM": raw_b}, {}), create=True
    ), patch.object(analise, "_promo_meta_contagem", return_value=None, create=True), patch.object(
        analise, "_promo_total_esperado_por_contagens", return_value=None, create=True
    ), patch.object(analise, "_carregar_custos_impostos_cadastro_por_sku_loja", return_value=({}, {}), create=True), patch.object(
        analise, "_ml_buscar_itens_batch", create=True
    ) as batch:
        with pytest.raises(HTTPException, match="cupom"):
            asyncio.run(analise.analisar_promo_via_api_sem_arquivos(
                loja="Loja", promocao_a_id="A", promocao_a_type="SELLER_CAMPAIGN",
                promocoes_b_meta=json.dumps([{"promo_b_id": "B", "promo_b_type": ""}]),
            ))
        batch.assert_not_called()


def test_automacao_antiga_com_cupom_pode_ser_desativada():
    req = PromoAutomacaoConfigRequest(enabled=False, promocao_a_type=CUPOM)
    with patch.object(jobs, "_promo_automacao_carregar", return_value={}), patch.object(jobs, "_promo_automacao_salvar") as salvar, patch.object(jobs, "_promo_automacao_public_payload", return_value={}):
        jobs.promo_automacao_salvar(req, client_id="tenant-teste")
    assert salvar.call_args.args[1]["enabled"] is False


@pytest.mark.parametrize("func", [participacoes.aplicar_participacoes_promocoes, participacoes.aplicar_participacoes_promocoes_start])
def test_aplicacao_antiga_com_cupom_nao_cria_job_nem_chama_ml(func):
    req = PromoAplicarParticipacaoRequest(loja="Loja", promocoes=[
        {"promotion_id": "B", "promotion_type": "SMART", "items": [{"item_id": "ITEM-B"}]},
        {"promotion_id": "A", "promotion_type": CUPOM, "items": [{"item_id": "ITEM-A"}]},
    ])
    with patch.object(participacoes, "_obter_cfg_ml", create=True) as cfg, patch.object(participacoes, "_promo_job_set", create=True) as gravar:
        with pytest.raises(HTTPException, match="cupom"):
            func(req, client_id="tenant-teste")
        cfg.assert_not_called()
        gravar.assert_not_called()
