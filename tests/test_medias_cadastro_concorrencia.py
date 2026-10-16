"""Concurrent Cadastro reads preserve a coherent request snapshot without blocking writers."""

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from backend.services import cadastro_compatibilidade as cadastro
from backend.services import cadastro_lojas_produtos as produtos
from backend.services import integracoes
from test_medias_compras_carregamento import _csv, ambiente


WAIT_SECONDS = 5


@contextmanager
def _reter_enriquecimento(monkeypatch, labels):
    """Pause real enrichment, observing overlap through independent public calls."""
    entered = {label: threading.Event() for label in labels}
    release = threading.Event()
    local = threading.local()
    original = produtos._enriquecer_produto

    def enriquecer(registro, loja, contexto, **kwargs):
        label = getattr(local, "label", None)
        if label in entered:
            entered[label].set()
            assert release.wait(WAIT_SECONDS * 3), "A fixture nao liberou o enriquecimento."
        return original(registro, loja, contexto, **kwargs)

    def consultar(label, client_id, referencia):
        local.label = label
        return cadastro.visao_produtos_cadastro_contexto_loja(client_id, referencia)

    monkeypatch.setattr(produtos, "_enriquecer_produto", enriquecer)
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="cadastro-concorrencia") as pool:
        try:
            yield pool, consultar, entered, release
        finally:
            release.set()


@pytest.mark.parametrize("primeira_referencia", ["store-a", "__todas"])
def test_consultas_do_mesmo_cliente_enriquecem_simultaneamente(
    ambiente, monkeypatch, primeira_referencia,
):
    with _reter_enriquecimento(monkeypatch, ["primeira", "segunda"]) as (pool, consultar, entered, release):
        primeira = pool.submit(consultar, "primeira", "cliente-a", primeira_referencia)
        assert entered["primeira"].wait(WAIT_SECONDS)
        segunda = pool.submit(consultar, "segunda", "cliente-a", "store-b")
        try:
            assert entered["segunda"].wait(WAIT_SECONDS), (
                "A segunda loja deve enriquecer enquanto a primeira consulta ainda esta retida."
            )
        finally:
            release.set()
        resposta_a = primeira.result(timeout=WAIT_SECONDS)
        resposta_b = segunda.result(timeout=WAIT_SECONDS)

    assert resposta_a["scope"] == ("global_safe" if primeira_referencia == "__todas" else "store")
    assert resposta_b["scope"] == "store"
    assert resposta_b["store_id"] == "store-b"
    assert resposta_b["produtos"][0]["m3 individual"] == "0,900"
    assert resposta_b["produtos"][0]["foto"] == ambiente["refs"]["cliente-a", "store-b"]


@pytest.mark.parametrize("referencia", ["store-a", "__todas"])
def test_atualizacao_de_lojas_adquire_lock_durante_enriquecimento(
    ambiente, monkeypatch, referencia,
):
    writer_entered = threading.Event()

    def atualizar_nome():
        with integracoes.lojas_config_lock("cliente-a"):
            ambiente["stores"]["cliente-a"][0]["nome"] = "Loja A renomeada"
            ambiente["write_stores"]("cliente-a")
            writer_entered.set()

    with _reter_enriquecimento(monkeypatch, ["leitora"]) as (pool, consultar, entered, release):
        leitora = pool.submit(consultar, "leitora", "cliente-a", referencia)
        assert entered["leitora"].wait(WAIT_SECONDS)
        escritora = pool.submit(atualizar_nome)
        try:
            assert writer_entered.wait(WAIT_SECONDS), (
                "Atualizar lojas nao deve aguardar o enriquecimento de uma consulta de leitura."
            )
        finally:
            release.set()
        escritora.result(timeout=WAIT_SECONDS)
        anterior = leitora.result(timeout=WAIT_SECONDS)

    if referencia == "store-a":
        assert anterior["produtos"][0]["loja_sync"] == "Loja A"
    atual = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a")
    assert atual["produtos"][0]["loja_sync"] == "Loja A renomeada"
    assert atual["store_id"] == "store-a"


def test_cliente_distinto_nao_recebe_dados_da_consulta_retida(ambiente, monkeypatch):
    with _reter_enriquecimento(monkeypatch, ["retida"]) as (pool, consultar, entered, release):
        retida = pool.submit(consultar, "retida", "cliente-a", "store-a")
        assert entered["retida"].wait(WAIT_SECONDS)
        outra = pool.submit(consultar, "outra", "cliente-b", "store-a")
        try:
            resposta = outra.result(timeout=WAIT_SECONDS)
            assert resposta["produtos"][0]["m3 individual"] == "0,250"
            assert resposta["produtos"][0]["foto"] == ambiente["refs"]["cliente-b", "store-a"]
            assert resposta["produtos"][0]["foto"] != ambiente["refs"]["cliente-a", "store-a"]
        finally:
            release.set()
        assert retida.result(timeout=WAIT_SECONDS)["produtos"][0]["m3 individual"] == "0,125"


def test_tombstone_e_conflito_global_continuam_sem_fallback(ambiente):
    tenant = ambiente["root"] / "cliente-a"
    rows, _ = produtos._ler_registros_persistidos("cliente-a")
    rows.append({"store_id": "store-a", "sku": "DEL", "sku_normalizado": "DEL",
                 "nome": "Produto excluido", "deleted_at_utc": "2026-10-16T12:00:00Z"})
    _csv(tenant / "cadastro_produtos_lojas.csv", rows)
    _csv(tenant / "cadastro_produtos.csv", [
        {"sku": "DEL", "store_id": "store-a", "loja_sync": "Loja A", "nome": "Legado nao autorizado"},
    ])

    loja = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a")
    global_segura = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "__todas")

    assert {item["sku"] for item in loja["produtos"]} == {"001"}
    assert "DEL" in loja["skus_controlados"]
    assert {item["sku"] for item in global_segura["produtos"]} == {"001"}
    item = global_segura["produtos"][0]
    assert item["m3 individual"] == ""
    assert item["store_scope_ambiguous"] is True
    assert "m3 individual" in item["campos_ambiguos"].split("|")


def test_nome_ambiguo_nao_substitui_identidade_exata(ambiente):
    ambiente["stores"]["cliente-a"][1]["nomes_anteriores"] = ["Loja Antiga"]
    ambiente["write_stores"]("cliente-a")

    ambigua = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "Loja Antiga")
    exata = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a")

    assert ambigua["scope"] == "unresolved"
    assert ambigua["produtos"] == []
    assert ambigua["store_id"] == ""
    assert exata["scope"] == "store"
    assert exata["store_id"] == "store-a"
    assert exata["produtos"][0]["m3 individual"] == "0,125"


def test_nova_consulta_rele_cadastro_sem_alterar_resposta_anterior(ambiente):
    primeira = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a")
    preservada = copy.deepcopy(primeira)
    rows, _ = produtos._ler_registros_persistidos("cliente-a")
    for row in rows:
        if row["store_id"] == "store-a":
            row["nome"] = "Produto alterado apos a primeira consulta"
            row["m3 individual"] = "0,750"
    _csv(ambiente["root"] / "cliente-a" / "cadastro_produtos_lojas.csv", rows)

    segunda = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a")
    outra_loja = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-b")
    outro_cliente = cadastro.visao_produtos_cadastro_contexto_loja("cliente-b", "store-a")

    assert primeira == preservada
    assert segunda["produtos"][0]["nome"] == "Produto alterado apos a primeira consulta"
    assert segunda["produtos"][0]["m3 individual"] == "0,750"
    assert outra_loja["produtos"][0]["m3 individual"] == "0,900"
    assert outro_cliente["produtos"][0]["m3 individual"] == "0,250"


def test_consulta_global_preserva_fontes_legadas_ate_enriquecer_todas_as_lojas(
    ambiente, monkeypatch,
):
    fonte = ambiente["root"] / "cliente-a" / "cadastro_custos_lojas.csv"

    def gravar_custos(valor):
        _csv(fonte, [
            {"store_id": store_id, "sku": "001", "custo": valor}
            for store_id in ("store-a", "store-b")
        ])

    gravar_custos("10,00")

    def atualizar_fonte():
        with integracoes.lojas_config_lock("cliente-a"):
            with produtos.path_locks_for([fonte]):
                gravar_custos("20,00")

    with _reter_enriquecimento(monkeypatch, ["capturada"]) as (pool, consultar, entered, release):
        capturada = pool.submit(consultar, "capturada", "cliente-a", "__todas")
        assert entered["capturada"].wait(WAIT_SECONDS)
        atualizacao = pool.submit(atualizar_fonte)
        try:
            atualizacao.result(timeout=WAIT_SECONDS)
        finally:
            release.set()
        anterior = capturada.result(timeout=WAIT_SECONDS)

    # A segunda loja ainda nao havia sido enriquecida quando o CSV mudou.
    # Um valor preenchido e nao ambiguo prova que ambas usaram a mesma captura.
    produto_anterior = anterior["produtos"][0]
    assert produto_anterior["store_ids"] == "store-a|store-b"
    assert produto_anterior["custo"] == "10,00"
    assert "custo" not in produto_anterior["campos_ambiguos"].split("|")

    atual = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "__todas")
    assert atual["produtos"][0]["custo"] == "20,00"
    assert "custo" not in atual["produtos"][0]["campos_ambiguos"].split("|")
