import asyncio
import threading

import pandas as pd
import pytest
from fastapi import HTTPException

from backend.services import (
    cadastro_lojas_produtos,
    cadastro_sync_ncm,
    estoque_sync,
    integracoes,
)


def _configure_files(monkeypatch, tmp_path):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    cadastro = tenant / "cadastro_produtos.csv"
    estoque = tenant / "produtos_compilado.csv"
    pd.DataFrame([{"sku": "001", "ncm": "legacy", "cest": "legacy"}]).to_csv(cadastro, index=False)
    pd.DataFrame([
        {"sku": "001", "store_id": "store-a", "loja_sync": "Loja A", "id_bling": "a-1", "ncm_bling": "", "cest_bling": ""},
        {"sku": "001", "store_id": "store-b", "loja_sync": "Loja B", "id_bling": "b-1", "ncm_bling": "old-b", "cest_bling": "old-b"},
    ]).to_csv(estoque, index=False)

    def migrate(_client_id, filename, _legacy):
        return str(cadastro if filename == "cadastro_produtos.csv" else estoque)

    stores = [
        {"store_id": "store-a", "nome": "Loja A", "integracoes": {"bling": {
            "access_token": "token-a", "id": "id-a", "secret": "secret-a", "refresh_token": "refresh-a"
        }}},
        {"store_id": "store-b", "nome": "Loja B", "integracoes": {"bling": {
            "access_token": "token-b", "id": "id-b", "secret": "secret-b", "refresh_token": "refresh-b"
        }}},
    ]
    monkeypatch.setattr(cadastro_sync_ncm, "_migrar_arquivo_legado_para_tenant", migrate, raising=False)
    monkeypatch.setattr(cadastro_sync_ncm, "carregar_lojas", lambda _client_id: stores)
    monkeypatch.setattr(cadastro_sync_ncm, "ARQUIVO_DB_CADASTRO_PRODUTOS", "legacy-cadastro", raising=False)
    monkeypatch.setattr(cadastro_sync_ncm, "ARQUIVO_DB_PRODUTOS", "legacy-estoque", raising=False)
    monkeypatch.setattr(cadastro_sync_ncm, "_classificar_monofasico_cadastro", lambda _df: (0, {}))
    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "get_tenant_path",
        lambda client_id: str(tmp_path / str(client_id)),
    )
    return cadastro, estoque, stores


def test_store_scoped_sync_queries_and_updates_only_selected_store(monkeypatch, tmp_path):
    cadastro, estoque, _stores = _configure_files(monkeypatch, tmp_path)
    calls = []

    def lookup(token, product_id):
        calls.append((token, product_id))
        return {"ncm": "ncm-a", "cest": "cest-a"}, 200

    monkeypatch.setattr(cadastro_sync_ncm, "_bling_obter_ncm_cest_produto", lookup, raising=False)
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-a"] = {"client_id": "000002", "status": "running"}

    cadastro_sync_ncm._sync_ncm_cadastro_worker("000002", "job-a", "store-a")

    assert calls == [("token-a", "a-1")]
    estoque_df = pd.read_csv(estoque, dtype=str).fillna("")
    row_a = estoque_df.loc[estoque_df["loja_sync"] == "Loja A"].iloc[0]
    row_b = estoque_df.loc[estoque_df["loja_sync"] == "Loja B"].iloc[0]
    assert (row_a["ncm_bling"], row_a["cest_bling"]) == ("ncm-a", "cest-a")
    assert (row_b["ncm_bling"], row_b["cest_bling"]) == ("old-b", "old-b")
    cadastro_df = pd.read_csv(cadastro, dtype=str).fillna("")
    assert (cadastro_df.iloc[0]["ncm"], cadastro_df.iloc[0]["cest"]) == ("legacy", "legacy")
    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-a"]["status"] == "done"


def test_store_scoped_sync_nao_exige_cadastro_legado(monkeypatch, tmp_path):
    cadastro, estoque, _stores = _configure_files(monkeypatch, tmp_path)
    cadastro.unlink()
    monkeypatch.setattr(
        cadastro_sync_ncm,
        "_bling_obter_ncm_cest_produto",
        lambda _token, _product_id: ({"ncm": "ncm-a", "cest": "cest-a"}, 200),
        raising=False,
    )
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-sem-legado"] = {
        "client_id": "000002",
        "status": "running",
    }

    cadastro_sync_ncm._sync_ncm_cadastro_worker(
        "000002", "job-sem-legado", "store-a"
    )

    resultado = pd.read_csv(estoque, dtype=str, keep_default_na=False).set_index(
        "store_id"
    )
    assert resultado.at["store-a", "ncm_bling"] == "ncm-a"
    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-sem-legado"]["status"] == "done"
    assert not cadastro.exists()


def test_store_scoped_sync_rejects_store_from_another_tenant(monkeypatch, tmp_path):
    _configure_files(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(cadastro_sync_ncm.iniciar_sync_ncm_cadastro("missing-store", "000002"))

    assert exc_info.value.status_code == 404


def test_store_scoped_sync_accepts_exact_id_for_homonymous_stores(monkeypatch, tmp_path):
    _cadastro, estoque, stores = _configure_files(monkeypatch, tmp_path)
    stores[0]["nome"] = stores[1]["nome"] = "Loja Homonima"
    frame = pd.read_csv(estoque, dtype=str, keep_default_na=False)
    frame["loja_sync"] = "Loja Homonima"
    frame.to_csv(estoque, index=False)
    calls = []

    def lookup(token, product_id):
        calls.append((token, product_id))
        return {"ncm": "ncm-a", "cest": "cest-a"}, 200

    monkeypatch.setattr(cadastro_sync_ncm, "_bling_obter_ncm_cest_produto", lookup, raising=False)
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-a"] = {"client_id": "000002", "status": "running"}

    cadastro_sync_ncm._sync_ncm_cadastro_worker("000002", "job-a", "store-a")

    assert calls == [("token-a", "a-1")]
    resultado = pd.read_csv(estoque, dtype=str, keep_default_na=False).set_index("store_id")
    assert (resultado.at["store-a", "ncm_bling"], resultado.at["store-a", "cest_bling"]) == (
        "ncm-a",
        "cest-a",
    )
    assert (resultado.at["store-b", "ncm_bling"], resultado.at["store-b", "cest_bling"]) == (
        "old-b",
        "old-b",
    )
    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-a"]["status"] == "done"


def test_store_scoped_refresh_keeps_homonymous_credentials_by_store_id(monkeypatch, tmp_path):
    _cadastro, estoque, stores = _configure_files(monkeypatch, tmp_path)
    stores[0]["nome"] = stores[1]["nome"] = "Loja Homonima"
    frame = pd.read_csv(estoque, dtype=str, keep_default_na=False)
    frame["loja_sync"] = "Loja Homonima"
    frame.to_csv(estoque, index=False)
    lookups = []
    refreshes = []

    def lookup(token, product_id):
        lookups.append((token, product_id))
        if token == "token-b":
            return None, 401
        return {"ncm": "ncm-b", "cest": "cest-b"}, 200

    def refresh(client_id, nome, cfg, *, store_id=None):
        refreshes.append((client_id, nome, cfg["access_token"], store_id))
        renovado = {
            **cfg,
            "access_token": "token-b-renovado",
            "refresh_token": "refresh-b-renovado",
            "connected": True,
            "oauth_invalid": False,
            "status": "conectado",
            "_sync_version": int(cfg.get("_sync_version") or 0) + 1,
        }
        stores[1]["integracoes"]["bling"] = dict(renovado)
        return dict(renovado)

    monkeypatch.setattr(cadastro_sync_ncm, "_bling_obter_ncm_cest_produto", lookup, raising=False)
    monkeypatch.setattr(cadastro_sync_ncm, "renovar_token_bling_loja", refresh)
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-b"] = {"client_id": "000002", "status": "running"}

    cadastro_sync_ncm._sync_ncm_cadastro_worker("000002", "job-b", "store-b")

    assert refreshes == [("000002", "Loja Homonima", "token-b", "store-b")]
    assert lookups == [("token-b", "b-1"), ("token-b-renovado", "b-1")]
    resultado = pd.read_csv(estoque, dtype=str, keep_default_na=False).set_index("store_id")
    assert resultado.at["store-b", "ncm_bling"] == "ncm-b"
    assert resultado.at["store-a", "ncm_bling"] == ""


def test_store_scoped_refresh_requires_the_returned_snapshot_to_be_persisted(
    monkeypatch, tmp_path
):
    _cadastro, estoque, _stores = _configure_files(monkeypatch, tmp_path)
    estoque_antes = estoque.read_bytes()

    def lookup(token, _product_id):
        if token == "token-a":
            return None, 401
        return {"ncm": "nao-gravar", "cest": "nao-gravar"}, 200

    def refresh(_client_id, _nome, cfg, *, store_id=None):
        assert store_id == "store-a"
        return {
            **cfg,
            "access_token": "token-nao-persistido",
            "_sync_version": int(cfg.get("_sync_version") or 0) + 1,
        }

    monkeypatch.setattr(
        cadastro_sync_ncm,
        "_bling_obter_ncm_cest_produto",
        lookup,
        raising=False,
    )
    monkeypatch.setattr(cadastro_sync_ncm, "renovar_token_bling_loja", refresh)
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-refresh-nao-persistido"] = {
        "client_id": "000002",
        "status": "running",
    }

    cadastro_sync_ncm._sync_ncm_cadastro_worker(
        "000002", "job-refresh-nao-persistido", "store-a"
    )

    job = cadastro_sync_ncm.SYNC_NCM_JOBS["job-refresh-nao-persistido"]
    assert job["status"] == "error"
    assert estoque.read_bytes() == estoque_antes
    assert "token-nao-persistido" not in job["mensagem"]


@pytest.mark.parametrize(
    ("campo", "valor_novo"),
    [
        ("id", "id-outra-conta"),
        ("secret", "secret-outra-conta"),
        ("access_token", "token-reconectado"),
        ("refresh_token", "refresh-reconectado"),
        ("connected", False),
        ("oauth_invalid", True),
        ("status", "reautenticacao_necessaria"),
        ("_sync_version", 99),
    ],
)
def test_store_scoped_sync_aborts_if_bling_fingerprint_changes_before_commit(
    monkeypatch, tmp_path, campo, valor_novo
):
    _cadastro, estoque, stores = _configure_files(monkeypatch, tmp_path)
    estoque_antes = estoque.read_bytes()

    def lookup(_token, _product_id):
        stores[0]["integracoes"]["bling"][campo] = valor_novo
        return {"ncm": "nao-gravar", "cest": "nao-gravar"}, 200

    monkeypatch.setattr(
        cadastro_sync_ncm,
        "_bling_obter_ncm_cest_produto",
        lookup,
        raising=False,
    )
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-config-race"] = {
        "client_id": "000002",
        "status": "running",
    }

    cadastro_sync_ncm._sync_ncm_cadastro_worker(
        "000002", "job-config-race", "store-a"
    )

    job = cadastro_sync_ncm.SYNC_NCM_JOBS["job-config-race"]
    assert job["status"] == "error"
    assert estoque.read_bytes() == estoque_antes
    assert "secret-a" not in job["mensagem"]
    assert "token-a" not in job["mensagem"]
    assert str(valor_novo) not in job["mensagem"]


def test_store_scoped_sync_aborts_if_store_is_renamed_before_commit(monkeypatch, tmp_path):
    _cadastro, estoque, stores = _configure_files(monkeypatch, tmp_path)
    estoque_antes = estoque.read_bytes()

    def lookup(_token, _product_id):
        stores[0]["nome"] = "Loja A Renomeada Durante Sync"
        return {"ncm": "nao-gravar", "cest": "nao-gravar"}, 200

    monkeypatch.setattr(
        cadastro_sync_ncm,
        "_bling_obter_ncm_cest_produto",
        lookup,
        raising=False,
    )
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-rename-race"] = {
        "client_id": "000002",
        "status": "running",
    }

    cadastro_sync_ncm._sync_ncm_cadastro_worker(
        "000002", "job-rename-race", "store-a"
    )

    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-rename-race"]["status"] == "error"
    assert estoque.read_bytes() == estoque_antes


def test_store_scoped_sync_aborts_if_store_is_removed_before_commit(monkeypatch, tmp_path):
    _cadastro, estoque, stores = _configure_files(monkeypatch, tmp_path)
    estoque_antes = estoque.read_bytes()

    def lookup(_token, _product_id):
        stores[:] = [loja for loja in stores if loja["store_id"] != "store-a"]
        return {"ncm": "nao-gravar", "cest": "nao-gravar"}, 200

    monkeypatch.setattr(
        cadastro_sync_ncm,
        "_bling_obter_ncm_cest_produto",
        lookup,
        raising=False,
    )
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-remove-race"] = {
        "client_id": "000002",
        "status": "running",
    }

    cadastro_sync_ncm._sync_ncm_cadastro_worker(
        "000002", "job-remove-race", "store-a"
    )

    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-remove-race"]["status"] == "error"
    assert estoque.read_bytes() == estoque_antes


def test_store_scoped_sync_aborts_if_legacy_name_becomes_ambiguous_before_commit(
    monkeypatch, tmp_path
):
    _cadastro, estoque, stores = _configure_files(monkeypatch, tmp_path)
    frame = pd.read_csv(estoque, dtype=str, keep_default_na=False)
    frame.loc[frame["store_id"] == "store-a", "store_id"] = ""
    frame.to_csv(estoque, index=False)
    estoque_antes = estoque.read_bytes()

    def lookup(_token, _product_id):
        stores.append(
            {
                "store_id": "store-c",
                "nome": "  LOJA A  ",
                "integracoes": {},
            }
        )
        return {"ncm": "nao-gravar", "cest": "nao-gravar"}, 200

    monkeypatch.setattr(
        cadastro_sync_ncm,
        "_bling_obter_ncm_cest_produto",
        lookup,
        raising=False,
    )
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-homonym-race"] = {
        "client_id": "000002",
        "status": "running",
    }

    cadastro_sync_ncm._sync_ncm_cadastro_worker(
        "000002", "job-homonym-race", "store-a"
    )

    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-homonym-race"]["status"] == "error"
    assert estoque.read_bytes() == estoque_antes


def test_store_scoped_sync_survives_display_rename_by_exact_id(monkeypatch, tmp_path):
    _cadastro, estoque, stores = _configure_files(monkeypatch, tmp_path)
    stores[0]["nome"] = "Loja A Renomeada"
    calls = []

    def lookup(token, product_id):
        calls.append((token, product_id))
        return {"ncm": "ncm-renomeado", "cest": "cest-renomeado"}, 200

    monkeypatch.setattr(cadastro_sync_ncm, "_bling_obter_ncm_cest_produto", lookup, raising=False)
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-rename"] = {"client_id": "000002", "status": "running"}

    cadastro_sync_ncm._sync_ncm_cadastro_worker("000002", "job-rename", "store-a")

    assert calls == [("token-a", "a-1")]
    resultado = pd.read_csv(estoque, dtype=str, keep_default_na=False).set_index("store_id")
    assert resultado.at["store-a", "loja_sync"] == "Loja A"
    assert resultado.at["store-a", "ncm_bling"] == "ncm-renomeado"
    assert resultado.at["store-b", "ncm_bling"] == "old-b"


def test_store_scoped_sync_uses_legacy_name_only_when_unique(monkeypatch, tmp_path):
    _cadastro, estoque, _stores = _configure_files(monkeypatch, tmp_path)
    frame = pd.read_csv(estoque, dtype=str, keep_default_na=False)
    frame.loc[frame["store_id"] == "store-a", "store_id"] = ""
    frame.to_csv(estoque, index=False)
    calls = []

    def lookup(token, product_id):
        calls.append((token, product_id))
        return {"ncm": "ncm-legado", "cest": "cest-legado"}, 200

    monkeypatch.setattr(cadastro_sync_ncm, "_bling_obter_ncm_cest_produto", lookup, raising=False)
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-legacy"] = {"client_id": "000002", "status": "running"}

    cadastro_sync_ncm._sync_ncm_cadastro_worker("000002", "job-legacy", "store-a")

    assert calls == [("token-a", "a-1")]
    resultado = pd.read_csv(estoque, dtype=str, keep_default_na=False)
    legado = resultado.loc[resultado["store_id"] == ""].iloc[0]
    assert (legado["ncm_bling"], legado["cest_bling"]) == ("ncm-legado", "cest-legado")
    assert resultado.loc[resultado["store_id"] == "store-b", "ncm_bling"].iloc[0] == "old-b"


def test_store_scoped_sync_matches_unique_legacy_name_by_strip_casefold(monkeypatch, tmp_path):
    _cadastro, estoque, _stores = _configure_files(monkeypatch, tmp_path)
    frame = pd.read_csv(estoque, dtype=str, keep_default_na=False)
    mask_a = frame["store_id"] == "store-a"
    frame.loc[mask_a, "store_id"] = ""
    frame.loc[mask_a, "loja_sync"] = "  loja a  "
    frame.to_csv(estoque, index=False)
    calls = []

    def lookup(token, product_id):
        calls.append((token, product_id))
        return {"ncm": "ncm-casefold", "cest": "cest-casefold"}, 200

    monkeypatch.setattr(cadastro_sync_ncm, "_bling_obter_ncm_cest_produto", lookup, raising=False)
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-casefold"] = {
        "client_id": "000002",
        "status": "running",
    }

    cadastro_sync_ncm._sync_ncm_cadastro_worker(
        "000002", "job-casefold", "store-a"
    )

    assert calls == [("token-a", "a-1")]
    resultado = pd.read_csv(estoque, dtype=str, keep_default_na=False)
    legado = resultado.loc[resultado["store_id"] == ""].iloc[0]
    assert (legado["ncm_bling"], legado["cest_bling"]) == (
        "ncm-casefold",
        "cest-casefold",
    )


def test_store_scoped_sync_rejects_casefold_homonym_legacy_name(monkeypatch, tmp_path):
    _cadastro, estoque, stores = _configure_files(monkeypatch, tmp_path)
    stores[0]["nome"] = "Loja A"
    stores[1]["nome"] = "  loja a  "
    frame = pd.read_csv(estoque, dtype=str, keep_default_na=False)
    mask_a = frame["store_id"] == "store-a"
    frame.loc[mask_a, "store_id"] = ""
    frame.loc[mask_a, "loja_sync"] = "LOJA A"
    frame.to_csv(estoque, index=False)
    calls = []

    def lookup(token, product_id):
        calls.append((token, product_id))
        return {"ncm": "nao-aplicar", "cest": "nao-aplicar"}, 200

    monkeypatch.setattr(cadastro_sync_ncm, "_bling_obter_ncm_cest_produto", lookup, raising=False)
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-homonima"] = {
        "client_id": "000002",
        "status": "running",
    }

    cadastro_sync_ncm._sync_ncm_cadastro_worker(
        "000002", "job-homonima", "store-a"
    )

    assert calls == []
    resultado = pd.read_csv(estoque, dtype=str, keep_default_na=False)
    legado = resultado.loc[resultado["store_id"] == ""].iloc[0]
    assert legado["ncm_bling"] == ""
    assert legado["cest_bling"] == ""
    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-homonima"]["total"] == 0


def test_store_scoped_sync_persists_monofasico_only_for_selected_store(monkeypatch, tmp_path):
    _cadastro, estoque, _stores = _configure_files(monkeypatch, tmp_path)

    def lookup(_token, _product_id):
        return {"ncm": "2710", "cest": "cest-a"}, 200

    def classify(frame):
        frame["monofasico"] = "Sim"
        frame["monofasico_status"] = "confirmado"
        frame["monofasico_verificado_em"] = "2026-08-28T12:00:00+00:00"
        return len(frame), {"confirmado": len(frame)}

    monkeypatch.setattr(cadastro_sync_ncm, "_bling_obter_ncm_cest_produto", lookup, raising=False)
    monkeypatch.setattr(cadastro_sync_ncm, "_classificar_monofasico_cadastro", classify)
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-a"] = {"client_id": "000002", "status": "running"}

    cadastro_sync_ncm._sync_ncm_cadastro_worker("000002", "job-a", "store-a")

    estoque_df = pd.read_csv(estoque, dtype=str).fillna("")
    row_a = estoque_df.loc[estoque_df["loja_sync"] == "Loja A"].iloc[0]
    row_b = estoque_df.loc[estoque_df["loja_sync"] == "Loja B"].iloc[0]
    assert row_a["monofasico"] == "Sim"
    assert row_a["monofasico_status"] == "confirmado"
    assert row_b["monofasico"] == ""
    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-a"]["monofasico_alterados"] == 1


def test_store_scoped_sync_preserves_concurrent_sku_insertion_and_columns(monkeypatch, tmp_path):
    _cadastro, estoque, _stores = _configure_files(monkeypatch, tmp_path)

    def lookup(_token, _product_id):
        concorrente = pd.read_csv(estoque, dtype=str).fillna("")
        concorrente["coluna_concorrente"] = ["preservar-a", "preservar-b"]
        concorrente = pd.concat(
            [
                concorrente,
                pd.DataFrame(
                    [
                        {
                            "sku": "999",
                            "loja_sync": "Loja A",
                            "id_bling": "a-999",
                            "ncm_bling": "ncm-concorrente",
                            "cest_bling": "cest-concorrente",
                            "coluna_concorrente": "linha-nova",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        ).fillna("")
        concorrente.to_csv(estoque, index=False)
        return {"ncm": "ncm-a", "cest": "cest-a"}, 200

    monkeypatch.setattr(cadastro_sync_ncm, "_bling_obter_ncm_cest_produto", lookup, raising=False)
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-a"] = {"client_id": "000002", "status": "running"}

    cadastro_sync_ncm._sync_ncm_cadastro_worker("000002", "job-a", "store-a")

    resultado = pd.read_csv(estoque, dtype=str).fillna("")
    row_a = resultado.loc[(resultado["loja_sync"] == "Loja A") & (resultado["sku"] == "001")].iloc[0]
    row_b = resultado.loc[(resultado["loja_sync"] == "Loja B") & (resultado["sku"] == "001")].iloc[0]
    row_nova = resultado.loc[(resultado["loja_sync"] == "Loja A") & (resultado["sku"] == "999")].iloc[0]
    assert len(resultado) == 3
    assert (row_a["ncm_bling"], row_a["cest_bling"]) == ("ncm-a", "cest-a")
    assert row_a["coluna_concorrente"] == "preservar-a"
    assert (row_b["ncm_bling"], row_b["cest_bling"]) == ("old-b", "old-b")
    assert row_b["coluna_concorrente"] == "preservar-b"
    assert (row_nova["ncm_bling"], row_nova["cest_bling"]) == ("ncm-concorrente", "cest-concorrente")
    assert row_nova["coluna_concorrente"] == "linha-nova"
    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-a"]["status"] == "done"


def test_store_scoped_sync_replace_failure_preserves_original_and_cleans_temp(monkeypatch, tmp_path):
    _cadastro, estoque, _stores = _configure_files(monkeypatch, tmp_path)
    conteudo_original = estoque.read_bytes()
    chamadas_replace = []

    monkeypatch.setattr(
        cadastro_sync_ncm,
        "_bling_obter_ncm_cest_produto",
        lambda _token, _product_id: ({"ncm": "ncm-a", "cest": "cest-a"}, 200),
        raising=False,
    )

    def falhar_replace(origem, destino):
        chamadas_replace.append((origem, destino))
        raise OSError("replace bloqueado pelo teste")

    monkeypatch.setattr(cadastro_sync_ncm.os, "replace", falhar_replace)
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-a"] = {"client_id": "000002", "status": "running"}

    cadastro_sync_ncm._sync_ncm_cadastro_worker("000002", "job-a", "store-a")

    assert chamadas_replace
    origem, destino = chamadas_replace[-1]
    assert estoque.parent == estoque.__class__(origem).parent == estoque.__class__(destino).parent
    assert estoque.read_bytes() == conteudo_original
    assert not list(estoque.parent.glob(f".{estoque.name}.*.tmp"))
    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-a"]["status"] == "error"


def test_store_scoped_sync_fails_closed_on_concurrent_target_ncm_change(monkeypatch, tmp_path):
    _cadastro, estoque, _stores = _configure_files(monkeypatch, tmp_path)

    def lookup(_token, _product_id):
        concorrente = pd.read_csv(estoque, dtype=str).fillna("")
        mask = (concorrente["loja_sync"] == "Loja A") & (concorrente["sku"] == "001")
        concorrente.loc[mask, "ncm_bling"] = "ncm-outro-writer"
        concorrente.to_csv(estoque, index=False)
        return {"ncm": "ncm-a", "cest": "cest-a"}, 200

    monkeypatch.setattr(cadastro_sync_ncm, "_bling_obter_ncm_cest_produto", lookup, raising=False)
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-a"] = {"client_id": "000002", "status": "running"}

    cadastro_sync_ncm._sync_ncm_cadastro_worker("000002", "job-a", "store-a")

    resultado = pd.read_csv(estoque, dtype=str).fillna("")
    row_a = resultado.loc[(resultado["loja_sync"] == "Loja A") & (resultado["sku"] == "001")].iloc[0]
    assert row_a["ncm_bling"] == "ncm-outro-writer"
    assert row_a["cest_bling"] == ""
    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-a"]["status"] == "error"


def test_store_scoped_commit_holds_config_lock_before_stock_lock(monkeypatch, tmp_path):
    _cadastro, estoque, stores = _configure_files(monkeypatch, tmp_path)
    base = pd.read_csv(estoque, dtype=str, keep_default_na=False)
    desejado = base.copy(deep=True)
    desejado.loc[desejado["store_id"] == "store-a", "ncm_bling"] = "ncm-a"
    original_commit = cadastro_sync_ncm._sync_ncm_commit_estoque
    lock_results = []

    def commit_observado(*args, **kwargs):
        def probe_config_lock():
            adquiriu = integracoes._LOJAS_CONFIG_LOCK.acquire(timeout=0.1)
            lock_results.append(adquiriu)
            if adquiriu:
                integracoes._LOJAS_CONFIG_LOCK.release()

        probe = threading.Thread(target=probe_config_lock, daemon=True)
        probe.start()
        probe.join(2)
        assert not probe.is_alive()
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(
        cadastro_sync_ncm,
        "_sync_ncm_commit_estoque",
        commit_observado,
    )
    cfg = stores[0]["integracoes"]["bling"]

    cadastro_sync_ncm._sync_ncm_commit_estoque_loja_revalidado(
        "000002",
        "store-a",
        "Loja A",
        cadastro_sync_ncm._sync_ncm_bling_fingerprint(cfg),
        str(estoque),
        base,
        desejado,
        permitir_fallback_nome=True,
    )

    assert lock_results == [False]
    resultado = pd.read_csv(estoque, dtype=str, keep_default_na=False).set_index(
        "store_id"
    )
    assert resultado.at["store-a", "ncm_bling"] == "ncm-a"


def test_global_sync_rejeita_sku_controlado_antes_de_criar_job(monkeypatch, tmp_path):
    cadastro, estoque, _stores = _configure_files(monkeypatch, tmp_path)
    controlado = cadastro.parent / "cadastro_produtos_lojas.csv"
    pd.DataFrame(
        [
            {
                "store_id": "store-a",
                "sku": "001",
                "sku_normalizado": "001",
                "row_version": "1",
                "updated_at_utc": "2026-08-28T12:00:00Z",
                "deleted_at_utc": "",
            }
        ]
    ).to_csv(controlado, index=False)
    cadastro_antes = cadastro.read_bytes()
    estoque_antes = estoque.read_bytes()
    controlado_antes = controlado.read_bytes()
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(cadastro_sync_ncm.iniciar_sync_ncm_cadastro(None, "000002"))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert cadastro_sync_ncm.SYNC_NCM_JOBS == {}
    assert cadastro.read_bytes() == cadastro_antes
    assert estoque.read_bytes() == estoque_antes
    assert controlado.read_bytes() == controlado_antes


def test_worker_global_rejeita_sku_controlado_sem_efeitos(monkeypatch, tmp_path):
    cadastro, estoque, _stores = _configure_files(monkeypatch, tmp_path)
    controlado = cadastro.parent / "cadastro_produtos_lojas.csv"
    pd.DataFrame(
        [{"store_id": "store-a", "sku": "001", "sku_normalizado": "001"}]
    ).to_csv(controlado, index=False)
    snapshots = {
        caminho: caminho.read_bytes() for caminho in (cadastro, estoque, controlado)
    }
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-global-controlado"] = {
        "client_id": "000002",
        "status": "running",
    }

    cadastro_sync_ncm._sync_ncm_cadastro_worker(
        "000002", "job-global-controlado"
    )

    job = cadastro_sync_ncm.SYNC_NCM_JOBS["job-global-controlado"]
    assert job["status"] == "error"
    assert job["code"] == "store_id_required"
    for caminho, conteudo in snapshots.items():
        assert caminho.read_bytes() == conteudo


def test_global_sync_without_store_identity_fails_safe(monkeypatch, tmp_path):
    cadastro, estoque, _stores = _configure_files(monkeypatch, tmp_path)
    legado = pd.read_csv(estoque, dtype=str).fillna("")
    legado.loc[legado["id_bling"] == "b-1", "sku"] = "002"
    legado = legado.drop(columns=["store_id", "loja_sync", "cest_bling"])
    legado.to_csv(estoque, index=False)
    calls = []

    def lookup(token, product_id):
        calls.append((token, product_id))
        return {"ncm": f"ncm-{product_id}", "cest": ""}, 200

    monkeypatch.setattr(cadastro_sync_ncm, "_bling_obter_ncm_cest_produto", lookup, raising=False)
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-global"] = {"client_id": "000002", "status": "running"}

    cadastro_sync_ncm._sync_ncm_cadastro_worker("000002", "job-global")

    resultado = pd.read_csv(estoque, dtype=str).fillna("")
    assert "loja_sync" not in resultado.columns
    assert "cest_bling" not in resultado.columns
    assert resultado.set_index("sku")["ncm_bling"].to_dict() == {
        "001": "",
        "002": "old-b",
    }
    assert calls == []
    cadastro_resultado = pd.read_csv(cadastro, dtype=str).fillna("")
    assert cadastro_resultado.iloc[0]["ncm"] == "legacy"
    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-global"]["status"] == "done"


def test_store_scoped_sync_uses_unique_historical_name_for_legacy_row(
    monkeypatch, tmp_path
):
    _cadastro, estoque, stores = _configure_files(monkeypatch, tmp_path)
    stores[0]["nome"] = "Loja A Renomeada"
    stores[0]["nomes_anteriores"] = ["Loja A"]
    frame = pd.read_csv(estoque, dtype=str, keep_default_na=False)
    frame.loc[frame["store_id"] == "store-a", "store_id"] = ""
    frame.to_csv(estoque, index=False)

    monkeypatch.setattr(
        cadastro_sync_ncm,
        "_bling_obter_ncm_cest_produto",
        lambda _token, _product_id: ({"ncm": "ncm-alias", "cest": "cest-alias"}, 200),
        raising=False,
    )
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-alias"] = {
        "client_id": "000002",
        "status": "running",
    }

    cadastro_sync_ncm._sync_ncm_cadastro_worker(
        "000002", "job-alias", "store-a"
    )

    resultado = pd.read_csv(estoque, dtype=str, keep_default_na=False)
    legado = resultado.loc[resultado["loja_sync"] == "Loja A"].iloc[0]
    assert (legado["ncm_bling"], legado["cest_bling"]) == (
        "ncm-alias",
        "cest-alias",
    )
    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-alias"]["status"] == "done"


def test_global_sync_revalida_sku_criado_durante_lookup_sem_efeitos_legados(
    monkeypatch, tmp_path
):
    cadastro, estoque, _stores = _configure_files(monkeypatch, tmp_path)
    cadastro_antes = cadastro.read_bytes()
    estoque_antes = estoque.read_bytes()
    canonico = cadastro.parent / "cadastro_produtos_lojas.csv"
    criou = False

    def lookup(_token, _product_id):
        nonlocal criou
        if not criou:
            criou = True
            pd.DataFrame(
                [{"store_id": "store-a", "sku": "001", "sku_normalizado": "001"}]
            ).to_csv(canonico, index=False)
        return {"ncm": "nao-gravar", "cest": "nao-gravar"}, 200

    monkeypatch.setattr(
        cadastro_sync_ncm,
        "_bling_obter_ncm_cest_produto",
        lookup,
        raising=False,
    )
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-race-scoped"] = {
        "client_id": "000002",
        "status": "running",
    }

    cadastro_sync_ncm._sync_ncm_cadastro_worker(
        "000002", "job-race-scoped"
    )

    job = cadastro_sync_ncm.SYNC_NCM_JOBS["job-race-scoped"]
    assert job["status"] == "error"
    assert job["code"] == "store_id_required"
    assert cadastro.read_bytes() == cadastro_antes
    assert estoque.read_bytes() == estoque_antes
    assert canonico.exists()


def test_global_sync_preserva_edicao_legada_concorrente_nao_fiscal(
    monkeypatch, tmp_path
):
    cadastro, _estoque, _stores = _configure_files(monkeypatch, tmp_path)
    frame = pd.read_csv(cadastro, dtype=str, keep_default_na=False)
    frame["nome"] = "Nome inicial"
    frame["preco"] = "10"
    frame.to_csv(cadastro, index=False)
    editou = False

    def lookup(_token, product_id):
        nonlocal editou
        if not editou:
            editou = True
            atual = pd.read_csv(cadastro, dtype=str, keep_default_na=False)
            atual.loc[0, "nome"] = "Nome concorrente"
            atual.loc[0, "preco"] = "99"
            atual.to_csv(cadastro, index=False)
        return {"ncm": f"ncm-{product_id}", "cest": "cest-novo"}, 200

    monkeypatch.setattr(
        cadastro_sync_ncm,
        "_bling_obter_ncm_cest_produto",
        lookup,
        raising=False,
    )
    cadastro_sync_ncm.SYNC_NCM_JOBS.clear()
    cadastro_sync_ncm.SYNC_NCM_JOBS["job-merge-legado"] = {
        "client_id": "000002",
        "status": "running",
    }

    cadastro_sync_ncm._sync_ncm_cadastro_worker(
        "000002", "job-merge-legado"
    )

    resultado = pd.read_csv(cadastro, dtype=str, keep_default_na=False).iloc[0]
    assert resultado["nome"] == "Nome concorrente"
    assert resultado["preco"] == "99"
    assert resultado["ncm"] == "ncm-a-1"
    assert resultado["cest"] == "cest-novo"
    assert cadastro_sync_ncm.SYNC_NCM_JOBS["job-merge-legado"]["status"] == "done"


def test_ncm_e_estoque_compartilham_lock_sem_lost_update(monkeypatch, tmp_path):
    estoque = tmp_path / "produtos_compilado.csv"
    pd.DataFrame([
        {
            "sku": "A-1",
            "loja_sync": "Loja A",
            "id_bling": "a-1",
            "ncm_bling": "",
            "cest_bling": "",
            "saldo_loja": "1",
        },
        {
            "sku": "B-1",
            "loja_sync": "Loja B",
            "id_bling": "b-1",
            "ncm_bling": "ncm-b",
            "cest_bling": "cest-b",
            "saldo_loja": "2",
        },
    ]).to_csv(estoque, index=False)
    base = pd.read_csv(estoque, dtype=str).fillna("")
    desejado = base.copy(deep=True)
    desejado.loc[desejado["loja_sync"] == "Loja A", "ncm_bling"] = "ncm-a"

    writer_entered = threading.Event()
    allow_ncm_write = threading.Event()
    stock_started = threading.Event()
    stock_done = threading.Event()
    failures = []
    original_ncm_writer = cadastro_sync_ncm._sync_ncm_salvar_estoque_atomico

    def paused_ncm_writer(frame, caminho):
        writer_entered.set()
        if not allow_ncm_write.wait(5):
            raise AssertionError("timeout aguardando liberacao do writer NCM")
        original_ncm_writer(frame, caminho)

    monkeypatch.setattr(cadastro_sync_ncm, "_sync_ncm_salvar_estoque_atomico", paused_ncm_writer)
    monkeypatch.setattr(estoque_sync, "_registrar_snapshot_historico_estoque", lambda *_a, **_k: 1)
    monkeypatch.setattr(estoque_sync, "_confirmar_evento_historico_estoque", lambda *_a, **_k: 1)
    monkeypatch.setattr(estoque_sync, "_descartar_evento_pendente_estoque", lambda *_a, **_k: None)

    def run_ncm():
        try:
            cadastro_sync_ncm._sync_ncm_commit_estoque(
                str(estoque), base, desejado, "Loja A",
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.append(exc)

    def run_stock():
        stock_started.set()
        try:
            estoque_sync._atualizar_produtos_compilados_loja(
                "000002",
                "Loja B",
                [{
                    "sku": "B-1",
                    "loja_sync": "Loja B",
                    "id_bling": "b-1",
                    "ncm_bling": "ncm-b",
                    "cest_bling": "cest-b",
                    "saldo_loja": "99",
                }],
                str(estoque),
                "evento-cross-writer",
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.append(exc)
        finally:
            stock_done.set()

    ncm_thread = threading.Thread(target=run_ncm, daemon=True)
    ncm_thread.start()
    assert writer_entered.wait(5)

    stock_thread = threading.Thread(target=run_stock, daemon=True)
    stock_thread.start()
    assert stock_started.wait(5)
    try:
        assert not stock_done.wait(0.25), "Estoque nao aguardou o lock mantido pelo NCM"
    finally:
        allow_ncm_write.set()

    ncm_thread.join(5)
    stock_thread.join(5)
    assert not ncm_thread.is_alive()
    assert not stock_thread.is_alive()
    if failures:
        raise failures[0]

    resultado = pd.read_csv(estoque, dtype=str).fillna("")
    row_a = resultado.loc[resultado["loja_sync"] == "Loja A"].iloc[0]
    row_b = resultado.loc[resultado["loja_sync"] == "Loja B"].iloc[0]
    assert row_a["ncm_bling"] == "ncm-a"
    assert row_a["saldo_loja"] == "1"
    assert row_b["ncm_bling"] == "ncm-b"
    assert row_b["saldo_loja"] == "99"


def test_stock_writer_fails_closed_on_corrupt_existing_csv(monkeypatch, tmp_path):
    estoque = tmp_path / "produtos_compilado.csv"
    estoque.write_text('sku,loja_sync\n"sem fechamento\n', encoding="utf-8")
    conteudo_original = estoque.read_bytes()
    publicacoes = []
    monkeypatch.setattr(
        estoque_sync,
        "_publicar_csv_estoque_apos_historico",
        lambda *_args, **_kwargs: publicacoes.append(True),
    )

    with pytest.raises(RuntimeError, match="nenhuma alteracao foi publicada"):
        estoque_sync._atualizar_produtos_compilados_loja(
            "000002",
            "Loja A",
            [{"sku": "A", "loja_sync": "Loja A"}],
            str(estoque),
            "evento-corrupto",
        )

    assert estoque.read_bytes() == conteudo_original
    assert publicacoes == []
