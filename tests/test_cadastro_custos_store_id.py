import pandas as pd
from pathlib import Path
import pytest
from fastapi import HTTPException

from backend.services import cadastro_custos


def _configure_tenant(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    monkeypatch.setattr(
        cadastro_custos,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    return tenant


def _import_cost(*, sku="001", loja="Loja", store_id=None, custo="10.00"):
    frame = pd.DataFrame(
        [{"produto": "Produto", "custo": custo, "preco": "20.00", "imposto": "5.00"}],
        index=[sku],
    )
    return cadastro_custos._cadastro_importar_custos_loja(
        "000002",
        frame,
        "sku",
        ["produto", "custo", "preco", "imposto"],
        loja,
        store_id=store_id,
    )


def test_mesma_sku_coexiste_em_duas_lojas_identificadas_por_store_id(tmp_path, monkeypatch):
    _configure_tenant(tmp_path, monkeypatch)

    first = _import_cost(store_id="store-a", loja="Mesmo nome", custo="10.00")
    second = _import_cost(store_id="store-b", loja="Mesmo nome", custo="11.00")

    rows = cadastro_custos._cadastro_ler_custos_lojas("000002").sort_values("store_id")
    assert first["custos_loja_incluidos"] == 1
    assert second["custos_loja_incluidos"] == 1
    assert rows[["store_id", "sku", "loja_sync", "custo"]].to_dict(orient="records") == [
        {"store_id": "store-a", "sku": "001", "loja_sync": "Mesmo nome", "custo": "10.00"},
        {"store_id": "store-b", "sku": "001", "loja_sync": "Mesmo nome", "custo": "11.00"},
    ]
    mapa = cadastro_custos._cadastro_mapa_custos_lojas("000002")
    assert "mesmo nome" not in mapa.get("001", {})


def test_mapa_prefere_store_id_a_linha_legada_posterior(tmp_path, monkeypatch):
    tenant = _configure_tenant(tmp_path, monkeypatch)
    (tenant / "cadastro_custos_lojas.csv").write_text(
        "store_id,loja_sync,sku,custo\n"
        "store-a,Loja A,001,10.00\n"
        ",Loja A,001,999.00\n",
        encoding="utf-8",
    )

    mapa = cadastro_custos._cadastro_mapa_custos_lojas("000002")
    loja_key = cadastro_custos._cadastro_norm_loja_custo("Loja A")

    assert mapa["001"][loja_key]["store_id"] == "store-a"
    assert mapa["001"][loja_key]["custo"] == "10.00"


def test_rename_de_loja_atualiza_display_sem_trocar_identidade(tmp_path, monkeypatch):
    _configure_tenant(tmp_path, monkeypatch)
    _import_cost(store_id="store-a", loja="Nome antigo", custo="10.00")

    result = _import_cost(store_id="store-a", loja="Nome novo", custo="12.50")

    rows = cadastro_custos._cadastro_ler_custos_lojas("000002")
    assert result["custos_loja_atualizados"] == 1
    assert result["custos_loja_incluidos"] == 0
    assert len(rows) == 1
    assert rows.iloc[0]["store_id"] == "store-a"
    assert rows.iloc[0]["loja_sync"] == "Nome novo"
    assert rows.iloc[0]["custo"] == "12.50"


def test_arquivo_legado_sem_store_id_continua_lendo_e_atualizando_por_nome(tmp_path, monkeypatch):
    tenant = _configure_tenant(tmp_path, monkeypatch)
    path = tenant / "cadastro_custos_lojas.csv"
    path.write_text(
        "loja_sync,sku,produto,custo,preco,imposto,updated_at\n"
        "Loja A,001,Produto,9.00,20.00,5.00,01/01/2026 10:00\n",
        encoding="utf-8",
    )

    before = cadastro_custos._cadastro_ler_custos_lojas("000002")
    result = _import_cost(loja="Loja Á", custo="13.00")
    after = cadastro_custos._cadastro_ler_custos_lojas("000002")

    assert list(before.columns) == cadastro_custos.CADASTRO_CUSTOS_LOJAS_COLS
    assert before.iloc[0]["store_id"] == ""
    assert result["custos_loja_atualizados"] == 1
    assert result["custos_loja_incluidos"] == 0
    assert len(after) == 1
    assert after.iloc[0]["store_id"] == ""
    assert after.iloc[0]["loja_sync"] == "Loja Á"
    assert after.iloc[0]["custo"] == "13.00"
    assert path.read_text(encoding="utf-8").splitlines()[0].startswith("store_id,")


def test_escrita_de_custos_usa_replace_atomico_no_mesmo_diretorio(tmp_path, monkeypatch):
    tenant = _configure_tenant(tmp_path, monkeypatch)
    real_replace = cadastro_custos.os.replace
    chamadas = []

    def registrar_replace(origem, destino):
        chamadas.append((Path(origem), Path(destino)))
        return real_replace(origem, destino)

    monkeypatch.setattr(cadastro_custos.os, "replace", registrar_replace)
    _import_cost(store_id="store-a", loja="Loja A")

    destino = tenant / "cadastro_custos_lojas.csv"
    assert chamadas and chamadas[-1][1] == destino
    assert chamadas[-1][0].parent == destino.parent
    assert not list(destino.parent.glob("*.tmp"))


def test_importacao_falha_fechada_se_arquivo_existente_esta_corrompido(tmp_path, monkeypatch):
    tenant = _configure_tenant(tmp_path, monkeypatch)
    caminho = tenant / "cadastro_custos_lojas.csv"
    caminho.write_bytes(b'"cabecalho sem fechamento\nlinha')
    antes = caminho.read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        _import_cost(store_id="store-a", loja="Loja A")

    assert exc_info.value.status_code == 500
    assert caminho.read_bytes() == antes
