import copy

import pandas as pd
import pytest

from backend.services import mercadolivre_legacy_core as mlcore
from backend.services import mercadolivre_legacy_planilhas as planilhas
from backend.services import promocoes_core


@pytest.fixture(autouse=True, scope="module")
def configurar_runtime():
    peers = {
        nome: getattr(promocoes_core, nome)
        for nome in promocoes_core.__all__
        if hasattr(promocoes_core, nome)
    }
    mlcore.configure_mercadolivre_legacy_core_runtime(peers=peers)


def linha_analise(**alteracoes):
    linha = {
        "MLB": "MLB999000111",
        "deal_price": 80,
        "Custo": "R$ 20,00",
        "Tarifa ML": "R$ 5,23",
        "Frete ML": "A calcular",
        "Imposto ML": "",
        "Imposto": "R$ 1,83",
        "Margem": "12,34%",
        "Margem ML": "999%",
        "frete_ml_exato": False,
        "_jk_tarifa_ml_exata": False,
        "_jk_tarifa_ml_estimada": True,
        "_jk_tarifa_ml_estimativa_motivo": "Tarifa-base para o preco promocional.",
        "action_financeiro_exato": False,
        "action_financeiro_estimado": True,
        "action_financeiro_estimativa_motivo": "Tarifa promocional estimada.",
        "action_valor_liquido_ml": 18.38,
        "action_margem_ml": 22.975,
    }
    linha.update(alteracoes)
    return linha


def valor_liquido_ml(saida):
    return next(valor for campo, valor in saida.items() if campo.endswith("quido ML"))


def montar_saida(linha, **opcoes):
    return planilhas._build_df_planilha_analise_promo([linha], **opcoes).to_dict(orient="records")[0]


def test_api_preserva_estimativa_canonica_e_nao_acrescenta_rotulo():
    linha = linha_analise()
    original = copy.deepcopy(linha)

    saida = montar_saida(linha)

    assert saida["Tarifa ML"] == "R$ 5,23"
    assert valor_liquido_ml(saida) == "R$ 18,38"
    assert saida["Margem ML"] == "22,98%"
    assert saida["Margem"] == "12,34%"
    assert saida["Imposto ML"] == ""
    assert saida["Imposto Fixa"] == "R$ 1,83"
    assert not any("(Estimado)" in str(valor) for valor in saida.values())
    assert linha == original


@pytest.mark.parametrize(
    "liquido,margem,tarifa,liquido_esperado,margem_esperada,tarifa_esperada",
    [
        (18.38, 22.975, "R$ 5,23", "R$ 18,38", "22,98%", "R$ 5,23"),
        (0, 0, "R$ 0,00", "R$ 0,00", "0%", "R$ 0,00"),
        (-4.15, -5.1875, "R$ 5,23", "R$ -4,15", "-5,19%", "R$ 5,23"),
    ],
)
def test_exportacao_destaca_valores_canonicos_positivos_zero_e_negativos(
    liquido, margem, tarifa, liquido_esperado, margem_esperada, tarifa_esperada
):
    linha = linha_analise(
        **{"Tarifa ML": tarifa},
        action_valor_liquido_ml=liquido,
        action_margem_ml=margem,
    )

    saida = montar_saida(linha, destacar_estimativas=True)

    assert saida["Tarifa ML"] == f"{tarifa_esperada} (Estimado)"
    assert valor_liquido_ml(saida) == f"{liquido_esperado} (Estimado)"
    assert saida["Margem ML"] == f"{margem_esperada} (Estimado)"
    assert saida["Margem"] == "12,34%"


def test_exportacao_mantem_valores_exatos_sem_rotulo():
    linha = linha_analise(
        _jk_tarifa_ml_exata=True,
        _jk_tarifa_ml_estimada=False,
        action_financeiro_exato=True,
        action_financeiro_estimado=False,
    )

    saida = montar_saida(linha, destacar_estimativas=True)

    assert saida["Tarifa ML"] == "R$ 5,23"
    assert valor_liquido_ml(saida) == "R$ 18,38"
    assert saida["Margem ML"] == "22,98%"


@pytest.mark.parametrize("estimado", [False, True])
def test_valores_ausentes_nao_recebem_rotulo_nem_reutilizam_margem_antiga(estimado):
    linha = linha_analise(
        **{"Tarifa ML": "A calcular"},
        _jk_tarifa_ml_estimada=estimado,
        action_financeiro_estimado=estimado,
        action_valor_liquido_ml=None,
        action_margem_ml=None,
    )

    saida = montar_saida(linha, destacar_estimativas=True)

    assert saida["Tarifa ML"] == "A calcular"
    assert valor_liquido_ml(saida) == ""
    assert saida["Margem ML"] == ""


def test_contexto_inexato_sem_estimativa_nao_expoe_resultado_antigo():
    linha = linha_analise(action_financeiro_estimado=False)

    saida = montar_saida(linha, destacar_estimativas=True)

    assert saida["Tarifa ML"] == "R$ 5,23 (Estimado)"
    assert valor_liquido_ml(saida) == ""
    assert saida["Margem ML"] == ""


def test_tarifa_exata_pode_acompanhar_margem_estimada_por_outro_componente():
    linha = linha_analise(_jk_tarifa_ml_exata=True, _jk_tarifa_ml_estimada=False)

    saida = montar_saida(linha, destacar_estimativas=True)

    assert saida["Tarifa ML"] == "R$ 5,23"
    assert valor_liquido_ml(saida) == "R$ 18,38 (Estimado)"
    assert saida["Margem ML"] == "22,98% (Estimado)"


def test_xlsx_real_destaca_estimativas_sem_contaminar_dataframe_da_api(tmp_path, monkeypatch):
    monkeypatch.setattr(planilhas, "get_tenant_path", lambda _client: str(tmp_path), raising=False)
    linhas = [
        linha_analise(),
        linha_analise(
            _jk_tarifa_ml_exata=True,
            _jk_tarifa_ml_estimada=False,
            action_financeiro_exato=True,
            action_financeiro_estimado=False,
        ),
        linha_analise(
            **{"Tarifa ML": "A calcular"},
            _jk_tarifa_ml_estimada=False,
            action_financeiro_estimado=False,
            action_valor_liquido_ml=None,
            action_margem_ml=None,
        ),
    ]
    originais = copy.deepcopy(linhas)

    nome = planilhas._salvar_planilha_analise_promo("tenant-teste", linhas)
    exportadas = pd.read_excel(
        tmp_path / "tmp" / "promo" / nome,
        sheet_name="Analise Promocao",
        keep_default_na=False,
    ).to_dict(orient="records")
    api = planilhas._build_df_planilha_analise_promo(linhas).to_dict(orient="records")

    assert len(exportadas) == len(api) == 3
    assert exportadas[0]["Tarifa ML"] == "R$ 5,23 (Estimado)"
    assert valor_liquido_ml(exportadas[0]) == "R$ 18,38 (Estimado)"
    assert exportadas[0]["Margem ML"] == "22,98% (Estimado)"
    assert exportadas[1]["Tarifa ML"] == "R$ 5,23"
    assert exportadas[1]["Margem ML"] == "22,98%"
    assert exportadas[2]["Tarifa ML"] == "A calcular"
    assert exportadas[2]["Margem ML"] == ""
    assert api[0]["Tarifa ML"] == "R$ 5,23"
    assert api[0]["Margem ML"] == "22,98%"
    assert not any("(Estimado)" in str(valor) for row in api for valor in row.values())
    assert linhas == originais
