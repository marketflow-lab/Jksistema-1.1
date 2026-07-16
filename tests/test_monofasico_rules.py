from __future__ import annotations

import pandas as pd

from backend.services.cadastro_sync_ncm import _classificar_monofasico_cadastro
from backend.services.monofasico_rules import avaliar_monofasico


def test_anexo_i_direct_codes_are_monophase():
    assert avaliar_monofasico("8708.99.90", "Peça nova para automóvel")["status"] == "sim"
    assert avaliar_monofasico("8512.20.11", "Lanterna nova")["status"] == "sim"


def test_old_broad_prefix_false_positives_are_not_monophase():
    for ncm in ("8421.39.90", "8482.10.10", "9026.20.90", "4016.93.00", "8714.10.00"):
        assert avaliar_monofasico(ncm, "Produto novo")["status"] == "nao"


def test_ex_entry_requires_the_exact_product_description():
    assert avaliar_monofasico("4016.99.90", "Tapete automotivo para carro")["status"] == "sim"
    assert avaliar_monofasico("4016.99.90", "Manopla de câmbio de borracha")["status"] == "nao"
    assert avaliar_monofasico("8536.50.90", "Interruptor 24V para caminhão")["status"] == "sim"
    assert avaliar_monofasico("8536.50.90", "Comando de vidro para automóvel")["status"] == "nao"


def test_anexo_ii_is_description_dependent():
    assert avaliar_monofasico("4009.31.00", "Mangueira de água para Ford Focus")["status"] == "sim"
    assert avaliar_monofasico("4009.12.90", "Presilha da mangueira intercooler")["status"] == "nao"
    assert avaliar_monofasico("8481.80.92", "Válvula moduladora solenoide Nissan Frontier")["status"] == "sim"
    assert avaliar_monofasico("8481.80.92", "Sensor de posição do câmbio")["status"] == "nao"
    assert avaliar_monofasico("8481.10.00", "Válvula reguladora de pressão 3/4")["status"] == "revisao"


def test_used_exclusion_only_reads_catalog_title():
    used = avaliar_monofasico("8481.10.00", "Válvula reguladora usada | produto em bom estado")
    boilerplate = avaliar_monofasico("8708.99.90", "Peça nova | pode ser usado em vários veículos")
    assert used["status"] == "nao"
    assert used["regra"] == "produto_usado"
    assert boilerplate["status"] == "sim"


def test_missing_ncm_is_never_forced_to_non_monophase():
    result = avaliar_monofasico("", "Produto sem NCM")
    assert result["status"] == "nao_verificado"
    assert result["is_monofasico"] is None


def test_cadastro_helper_preserves_historical_ncm_as_review():
    df = pd.DataFrame(
        [
            {"sku": "A", "ncm": "87089990", "produto_bling": "Peça automotiva nova"},
            {
                "sku": "B",
                "ncm": "",
                "ncm_auditoria": "87089990",
                "ncm_fonte_auditoria": "Histórico do cadastro de 28/04/2026; requer reconfirmação",
                "produto_bling": "Peça automotiva nova",
            },
            {"sku": "C", "ncm": "", "produto_bling": "Sem classificação"},
        ]
    ).fillna("")

    changed, counts = _classificar_monofasico_cadastro(df)

    assert changed == 3
    assert counts == {"sim": 1, "revisao": 1, "nao_verificado": 1}
    assert df.loc[0, "monofasico"] == "Monofásico"
    assert df.loc[1, "monofasico"] == "Revisão necessária"
    assert df.loc[2, "monofasico"] == "Não verificado"
