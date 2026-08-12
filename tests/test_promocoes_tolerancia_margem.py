from unittest.mock import patch

from backend.services import promocoes_api_analise as analise
from backend.services import promocoes_api_jobs as jobs
from backend.schemas.promocoes import PromoAutomacaoConfigRequest


def _parse_float(value):
    if value is None or value == "":
        return None
    return float(str(value).strip().replace("%", "").replace(",", "."))


def test_tolerancia_aprova_margem_um_ponto_abaixo_da_promocao_1():
    with patch.object(analise, "_parse_float_flex", side_effect=_parse_float, create=True):
        assert analise._promo_margens_aprovadas(20.0, 19.0, 15.0, 1.0) is True


def test_tolerancia_reprova_quando_diferenca_supera_o_limite():
    with patch.object(analise, "_parse_float_flex", side_effect=_parse_float, create=True):
        assert analise._promo_margens_aprovadas(20.0, 18.99, 15.0, 1.0) is False


def test_tolerancia_nao_reduz_a_margem_minima_geral():
    with patch.object(analise, "_parse_float_flex", side_effect=_parse_float, create=True):
        assert analise._promo_margens_aprovadas(15.5, 14.8, 15.0, 1.0) is False


def test_tolerancia_e_limitada_entre_zero_e_cem():
    with patch.object(analise, "_parse_float_flex", side_effect=_parse_float, create=True):
        assert analise._promo_sanitizar_tolerancia_margem(-2) == 0.0
        assert analise._promo_sanitizar_tolerancia_margem(150) == 100.0


def test_tolerancia_e_preservada_na_configuracao_automatica():
    payload = PromoAutomacaoConfigRequest(margem_tolerancia=1.25).model_dump()
    config = jobs._promo_automacao_sanitizar(payload)

    assert config["margem_tolerancia"] == 1.25


def test_programada_reprova_caso_real_com_margem_ml_muito_menor():
    with patch.object(analise, "_parse_float_flex", side_effect=_parse_float, create=True):
        assert analise._promo_margens_aprovadas_por_status(
            31.56,
            11.79,
            "Programado",
            15.0,
            0.0,
        ) is False


def test_programada_reprova_margem_ml_abaixo_de_quinze():
    with patch.object(analise, "_parse_float_flex", side_effect=_parse_float, create=True):
        assert analise._promo_margens_aprovadas_por_status(
            17.0,
            14.99,
            "Programada",
            15.0,
            100.0,
        ) is False


def test_programada_aceita_diferenca_exata_de_tres_pontos():
    with patch.object(analise, "_parse_float_flex", side_effect=_parse_float, create=True):
        assert analise._promo_margens_aprovadas_por_status(
            20.0,
            17.0,
            "Programada",
            15.0,
            0.0,
        ) is True


def test_programada_reprova_quando_diferenca_supera_tres_pontos():
    with patch.object(analise, "_parse_float_flex", side_effect=_parse_float, create=True):
        assert analise._promo_margens_aprovadas_por_status(
            20.0,
            16.99,
            "Programada",
            15.0,
            100.0,
        ) is False


def test_status_ativo_preserva_tolerancia_configurada():
    with patch.object(analise, "_parse_float_flex", side_effect=_parse_float, create=True):
        assert analise._promo_margens_aprovadas_por_status(
            20.0,
            19.0,
            "Ativo",
            15.0,
            1.0,
        ) is True
