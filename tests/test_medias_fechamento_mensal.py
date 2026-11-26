"""A visão deve usar os meses fechados sem perder vendas em aberto ou correções tardias."""

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from backend.modules.vendas import monthly_coverage
from backend.services import medias_compras_fechamento as fechamento
from backend.services.medias_compras_leitura import leitura_medias
from test_medias_historico_projecao import _banco, _ler, _referencia_sql_antigo


CLIENTE = "cliente-teste"
LOJA = "Loja A"
OUTRA_LOJA = "Loja B"
STORE = "loja-a"
OUTRO_STORE = "loja-b"
MESES = {"2026-07": "julho-1", "2026-09": "setembro-1", "2026-10": "outubro-1"}


def _configurar(raiz, monkeypatch):
    monkeypatch.setattr(fechamento, "_mes_atual", lambda: "2026-11")
    monkeypatch.setattr(fechamento, "raiz_tenant", lambda cliente: str(raiz / cliente))

    def cobertura(_pasta, client_id, store_id=None, *, now=None):
        if client_id != CLIENTE or store_id not in (None, STORE):
            return {}
        return {(STORE, mes): versao for mes, versao in MESES.items()}

    monkeypatch.setattr(monthly_coverage, "meses_confirmados", cobertura)


@pytest.fixture
def raiz(tmp_path, monkeypatch):
    raiz = tmp_path / CLIENTE
    raiz.mkdir()
    _configurar(tmp_path, monkeypatch)
    return raiz


@pytest.fixture
def bancos(raiz):
    primeiro = _banco(raiz / "vendas_historico_a.db", [
        ("repetido", "SKU", "2026-07-03", "Julho", 8, LOJA),
        ("mes-incompleto", "SKU", "2026-08-08", "Mês sem cobertura completa", 2, LOJA),
        ("setembro", "SKU", "2026-09-30", "Setembro", 3, LOJA),
        ("outubro", "SKU", "2026-10-13", "Outubro", 4, LOJA),
        ("novembro", "SKU", "2026-11-05", "Novembro", 5, LOJA),
        ("futura", "SKU", "2026-11-25", "Venda posterior ao corte", 9, LOJA),
        ("outra", "SKU", "2026-10-07", "Da outra loja", 200, OUTRA_LOJA),
        ("", "SEM-ID", "2026-10-09", "Sem ID", 2, LOJA),
        ("", "SEM-ID", "2026-10-10", "Sem ID", 2, LOJA),
        ("antiga", "SEM-ID", "2020-01-10", "Primeira venda", 1, LOJA),
        ("f1", "FRAC", "2026-10-05", "Fracionado", 1e16, LOJA),
        ("f2", "FRAC", "2026-10-08", "Fracionado", -1e16, LOJA),
        ("f3", "FRAC", "2026-10-01", "Fracionado", 0.5, LOJA),
    ])
    segundo = _banco(raiz / "vendas_historico.db", [
        ("repetido", "SKU", "2026-10-13", "Mesmo ID em outro mês", 7, LOJA),
        ("", "SEM-ID", "2026-10-12", "Mesmo SKU em outra base", 2, LOJA),
        ("f4", "FRAC", "2026-10-02", "Fracionado", 0.25, LOJA),
        ("fuso", "FUSO", "2026-11-01T00:30:00+02:00", "Virada", 6, LOJA),
    ])
    return [primeiro, segundo]


def _opcoes(bancos, loja=LOJA, store=STORE):
    return dict(client_id=CLIENTE, loja=loja, store_id=store, meses=MESES, db_paths=bancos)


def _consultar(bancos, meses=6, loja=LOJA, store=STORE, fim="2026-11-15"):
    planejamento = {}
    with leitura_medias() as leitura:
        dados = _ler(bancos, client=CLIENTE, loja=loja, store=store, meses=meses,
                     fim=fim, planejamento=planejamento)
    assert dados == _referencia_sql_antigo(bancos, meses, loja, fim)
    return dados, planejamento, leitura.metrics.get("historico_meses_fechados_reusados", 0)


def test_fechamento_sobrevive_a_reinicio_e_preserva_deduplicacao_por_periodo(bancos, tmp_path):
    assert fechamento.preparar_fechamentos(**_opcoes(bancos))
    assert fechamento.fechamentos_disponiveis(**_opcoes(bancos)) == set(MESES)

    # O processo filho começa sem projeções em memória e lê os mesmos arquivos.
    script = """
import json, sys
from pathlib import Path
from pytest import MonkeyPatch
sys.path.insert(0, "tests")
from test_medias_fechamento_mensal import _configurar, _consultar
with MonkeyPatch.context() as patch:
    _configurar(Path(sys.argv[1]), patch)
    saida = {}
    for periodo in (3, 6, 12):
        dados, planejamento, hits = _consultar(sys.argv[2:], meses=periodo)
        saida[periodo] = [dados[0]["SKU"]["vendas_mensais"]["2026-10"],
                          dados[0]["FUSO"]["vendas_mensais"]["2026-10"],
                          planejamento["SEM-ID"]["primeira_venda"], hits]
    print(json.dumps(saida))
"""
    filho = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path), *bancos],
                           cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                           timeout=45)
    assert filho.returncode == 0, filho.stderr
    resultados = json.loads(filho.stdout)
    assert resultados["3"][:3] == [11, 6, "2020-01-10"]
    for periodo in ("6", "12"):
        assert resultados[periodo][:3] == [4, 6, "2020-01-10"]
    assert resultados["3"][3] > 0


def test_mes_parcial_avanca_com_a_data_e_cada_loja_mantem_seus_totais(bancos):
    opcoes = _opcoes(bancos)
    assert fechamento.preparar_fechamentos(**opcoes)
    antes, _, reutilizados = _consultar(bancos)
    with sqlite3.connect(bancos[0]) as conn:
        conn.execute("INSERT INTO vendas VALUES ('nova','SKU','2026-11-22','Nova',3,?)", (LOJA,))
        conn.execute("UPDATE vendas SET quantidade=250 WHERE id_unico='outra'")
        conn.execute("UPDATE vendas SET quantidade=6 WHERE id_unico='mes-incompleto'")
    assert fechamento.fechamentos_disponiveis(**opcoes) == set(MESES)
    depois, _, _ = _consultar(bancos, fim="2026-11-25")
    outra, _, _ = _consultar(bancos, loja=OUTRA_LOJA, store=OUTRO_STORE)
    todas, _, _ = _consultar(bancos, loja="__todas", store="")
    assert reutilizados > 0
    assert antes[0]["SKU"]["vendas_mensais"]["2026-11"] == 5
    assert depois[0]["SKU"]["vendas_mensais"]["2026-11"] == 17
    assert depois[0]["SKU"]["vendas_mensais"]["2026-08"] == 6
    assert outra[0]["SKU"]["vendas_mensais"]["2026-10"] == 250
    assert todas[0]["SKU"]["vendas_mensais"]["2026-10"] == 254


def test_operacoes_tardias_invalidam_apenas_mes_afetado_e_reconstroem_total(bancos):
    opcoes = _opcoes(bancos)
    assert fechamento.preparar_fechamentos(**opcoes)
    with sqlite3.connect(bancos[0]) as conn:
        conn.execute("UPDATE vendas SET quantidade=40 WHERE id_unico='outubro'")
        conn.execute("INSERT INTO vendas VALUES ('atrasada','SKU','2026-10-22','Atrasada',12,?)", (LOJA,))
    assert fechamento.fechamentos_disponiveis(**opcoes) == {"2026-07", "2026-09"}
    corrigido, _, _ = _consultar(bancos)
    assert corrigido[0]["SKU"]["vendas_mensais"]["2026-10"] == 52

    assert fechamento.preparar_fechamentos(**opcoes)
    assert fechamento.fechamentos_disponiveis(**opcoes) == set(MESES)
    with sqlite3.connect(bancos[0]) as conn:
        conn.execute("DELETE FROM vendas WHERE id_unico='atrasada'")
        conn.execute("UPDATE vendas SET data='2026-09-20' WHERE id_unico='outubro'")
    assert fechamento.fechamentos_disponiveis(**opcoes) == {"2026-07"}
    movido, _, _ = _consultar(bancos)
    assert movido[0]["SKU"]["vendas_mensais"]["2026-09"] == 43
    assert movido[0]["SKU"]["vendas_mensais"]["2026-10"] == 0


def test_substituicao_por_id_e_rollback_preservam_coerencia_do_fechamento(raiz):
    path = _banco(raiz / "vendas_historico.db", [
        ("id-venda", "SKU", "2026-10-01", "Pedido original", 4, LOJA),
    ])
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE UNIQUE INDEX venda_por_id ON vendas(id_unico)")
    opcoes = _opcoes([path])
    assert fechamento.preparar_fechamentos(**opcoes)
    with sqlite3.connect(path) as conn:
        substituir = "INSERT OR REPLACE INTO vendas VALUES ('id-venda','SKU','2026-09-18','Revisado',8,?)"
        conn.execute(substituir, (OUTRA_LOJA,))
        conn.rollback()
        assert fechamento.fechamentos_disponiveis(**opcoes) == set(MESES)
        conn.execute(substituir, (OUTRA_LOJA,))
    assert fechamento.fechamentos_disponiveis(**opcoes) == {"2026-07", "2026-09"}
    origem, _, _ = _consultar([path])
    destino, _, _ = _consultar([path], loja=OUTRA_LOJA, store=OUTRO_STORE)
    assert "SKU" not in origem[0]
    assert destino[0]["SKU"]["vendas_mensais"]["2026-09"] == 8


def test_update_or_replace_invalida_o_mes_da_venda_conflitante(raiz):
    path = _banco(raiz / "vendas_historico.db", [
        ("existente", "REMOVIDO", "2026-07-02", "Registro substituído", 9, LOJA),
        ("original", "NOVO", "2026-09-02", "Registro atualizado", 2, LOJA),
    ])
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE UNIQUE INDEX venda_por_id ON vendas(id_unico)")
    opcoes = _opcoes([path])
    assert fechamento.preparar_fechamentos(**opcoes)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE OR REPLACE vendas SET id_unico='existente', data='2026-10-05' WHERE id_unico='original'")
    assert fechamento.fechamentos_disponiveis(**opcoes) == set()
    atual, _, _ = _consultar([path])
    assert "REMOVIDO" not in atual[0]
    assert atual[0]["NOVO"]["vendas_mensais"]["2026-10"] == 2


def test_soma_de_magnitudes_excedentes_nao_impede_reuso_de_outro_mes(raiz):
    path = _banco(raiz / "vendas_historico.db", [
        ("venda", "SKU", "2026-09-02", "Venda comum", 15, LOJA),
        ("ajuste", "SKU", "2026-10-11", "Ajuste", 1e308, LOJA),
        ("estorno", "SKU", "2026-10-12", "Estorno", -1e308, LOJA),
    ])
    assert fechamento.preparar_fechamentos(**_opcoes([path]))
    dados, _, reusados = _consultar([path])
    assert reusados > 0
    assert dados[0]["SKU"]["vendas_mensais"]["2026-09"] == 15
    assert dados[0]["SKU"]["vendas_mensais"]["2026-10"] == 0


def test_mes_fora_da_janela_fornece_metadados_sem_reler_suas_vendas(raiz, monkeypatch):
    monkeypatch.setitem(MESES, "2020-02", "fevereiro-2020")
    bancos = [
        _banco(raiz / "vendas_historico_a.db", [
            ("tit-ativo", "ATIVO", "2020-02-12", "Título antigo ainda válido", 3, LOJA),
            ("tit-arquivado", "ARQUIVADO", "2020-02-09", "Título preservado", 5, LOJA),
        ]),
        _banco(raiz / "vendas_historico.db", [
            ("primeira", "ATIVO", "2020-02-02", "Sem descrição", 1, LOJA),
            ("antiga", "ARQUIVADO", "2022-04-18", "-", 1, LOJA),
            ("recente", "ATIVO", "2026-11-03", "Sem descrição", 8, LOJA),
            ("mes-aberto", "ATIVO", "2026-09-01", "-", 2, LOJA),
        ]),
    ]
    _, antes, _ = _consultar(bancos)
    opcoes = {**_opcoes(bancos), "meses": {"2020-02": MESES["2020-02"]}}
    assert fechamento.preparar_fechamentos(**opcoes)

    with leitura_medias() as leitura:
        (_, titulos, ultimas), depois, reutilizados = _consultar(bancos)
    assert reutilizados > 0
    assert leitura.metrics["historico_linhas_lidas"] == 3
    assert depois == antes
    assert depois["ATIVO"]["primeira_venda"] == "2020-02-02"
    assert depois["ATIVO"]["vendas_mensais"]["2026-09"] == 2
    assert depois["ATIVO"]["vendas_mensais"]["2026-11"] == 8
    assert titulos["ATIVO"] == {"titulo": "Título antigo ainda válido", "data": "2020-02-12"}
    assert titulos["ARQUIVADO"]["titulo"] == "Título preservado"
    assert ultimas == {"ATIVO": "2026-11-03", "ARQUIVADO": "2022-04-18"}


@pytest.mark.parametrize("loja_da_nova_venda", [LOJA, OUTRA_LOJA])
@pytest.mark.parametrize("journal", ["delete", "wal"])
def test_venda_durante_preparo_invalida_apenas_fechamento_da_propria_loja(
    raiz, monkeypatch, loja_da_nova_venda, journal,
):
    path = _banco(raiz / "vendas_historico.db", [
        ("original", "SKU", "2026-10-05", "Inicial", 3, LOJA),
    ])
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA journal_mode=" + journal).fetchone()[0] == journal
    opcoes = {**_opcoes([path]), "meses": {"2026-10": MESES["2026-10"]}}
    resumir = fechamento._resumir

    def gravar_apos_calculo(*args):
        resumo = resumir(*args)
        with closing(sqlite3.connect(path, timeout=1)) as escritor, escritor:
            escritor.execute("INSERT INTO vendas VALUES ('nova','SKU','2026-10-16','Nova',8,?)",
                             (loja_da_nova_venda,))
        return resumo

    with monkeypatch.context() as patch:
        patch.setattr(fechamento, "_resumir", gravar_apos_calculo)
        publicado = fechamento.preparar_fechamentos(**opcoes)

    if loja_da_nova_venda == LOJA:
        assert not publicado
        assert fechamento.fechamentos_disponiveis(**opcoes) == set()
        dados, _, hits = _consultar([path])
        assert hits == 0
        assert dados[0]["SKU"]["vendas_mensais"]["2026-10"] == 11
        assert fechamento.preparar_fechamentos(**opcoes)
    else:
        assert publicado
        outra, _, _ = _consultar([path], loja=OUTRA_LOJA, store=OUTRO_STORE)
        assert outra[0]["SKU"]["vendas_mensais"]["2026-10"] == 8
    assert fechamento.fechamentos_disponiveis(**opcoes) == {"2026-10"}
    dados, _, hits = _consultar([path])
    assert hits > 0
    assert dados[0]["SKU"]["vendas_mensais"]["2026-10"] == (11 if loja_da_nova_venda == LOJA else 3)
