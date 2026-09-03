import asyncio

import pandas as pd
import pytest
from fastapi import HTTPException

from backend.schemas.estoque import (
    EstoqueLancamentosSyncLoteRequest,
    EstoqueLancamentosSyncRequest,
    EstoqueSyncRequest,
)
from backend.services import estoque_lancamentos, estoque_sync


def _homonymous_stores():
    return [
        {
            "store_id": "store-a",
            "nome": "Loja Homonima",
            "integracoes": {"bling": {
                "access_token": "token-a", "id": "id-a", "secret": "secret-a"
            }},
        },
        {
            "store_id": "store-b",
            "nome": "Loja Homonima",
            "integracoes": {"bling": {
                "access_token": "token-b", "id": "id-b", "secret": "secret-b"
            }},
        },
    ]


def test_estoque_resolves_exact_case_sensitive_id_and_rejects_legacy_homonym(monkeypatch):
    stores = _homonymous_stores()
    monkeypatch.setattr(estoque_sync, "carregar_lojas", lambda _client_id: stores)

    resolved = estoque_sync._resolver_loja_estoque(
        "000002", store_id="store-b", loja_nome="Loja Homonima"
    )

    assert resolved["store_id"] == "store-b"
    assert resolved["integracoes"]["bling"]["access_token"] == "token-b"
    assert resolved["_nome_exato_unico"] is False
    with pytest.raises(HTTPException) as ambiguous:
        estoque_sync._resolver_loja_estoque("000002", loja_nome="Loja Homonima")
    assert ambiguous.value.status_code == 409
    with pytest.raises(HTTPException) as wrong_case:
        estoque_sync._resolver_loja_estoque("000002", store_id="STORE-B")
    assert wrong_case.value.status_code == 404


def test_historico_rejeita_lojas_homonimas_antes_de_efeitos(monkeypatch):
    monkeypatch.setattr(
        estoque_lancamentos.integracoes_service,
        "carregar_lojas",
        lambda _client_id: _homonymous_stores(),
    )
    efeitos = []
    monkeypatch.setattr(
        estoque_lancamentos,
        "_bling_listar_lotes_produto",
        lambda *_args, **_kwargs: efeitos.append("bling"),
    )
    monkeypatch.setattr(
        estoque_lancamentos,
        "_estoque_historico_db_path",
        lambda *_args, **_kwargs: efeitos.append("db"),
    )
    estoque_lancamentos.ESTOQUE_LANC_SYNC_ACTIVE.pop("000002", None)

    chamadas = [
        lambda: asyncio.run(
            estoque_lancamentos.sincronizar_lancamentos_estoque_api(
                EstoqueLancamentosSyncRequest(
                    loja="Loja Homonima",
                    store_id="store-b",
                    sku="001",
                ),
                "000002",
            )
        ),
        lambda: asyncio.run(
            estoque_lancamentos.sincronizar_lancamentos_estoque_lote_api(
                EstoqueLancamentosSyncLoteRequest(
                    loja="Loja Homonima",
                    store_id="store-b",
                ),
                "000002",
            )
        ),
        lambda: asyncio.run(
            estoque_lancamentos.estoque_serie_retroativa(
                loja="Loja Homonima",
                store_id="store-b",
                client_id="000002",
            )
        ),
    ]

    for chamada in chamadas:
        with pytest.raises(HTTPException) as ambiguous:
            chamada()
        assert ambiguous.value.status_code == 409
        assert ambiguous.value.detail["code"] == "estoque_historico_loja_ambigua"

    assert efeitos == []
    assert not estoque_lancamentos.ESTOQUE_LANC_SYNC_ACTIVE.get("000002")


def test_rota_historico_exige_store_id_antes_do_banco(monkeypatch):
    monkeypatch.setattr(
        estoque_lancamentos,
        "_carregar_lojas_historico",
        lambda _client_id: [{"store_id": "store-a", "nome": "Loja A"}],
    )
    efeitos = []
    monkeypatch.setattr(
        estoque_lancamentos,
        "_estoque_historico_db_path",
        lambda *_args, **_kwargs: efeitos.append("db"),
    )

    with pytest.raises(HTTPException) as sem_id:
        asyncio.run(
            estoque_lancamentos.estoque_serie_retroativa(
                loja="Loja A",
                client_id="000002",
            )
        )

    assert sem_id.value.status_code == 409
    assert sem_id.value.detail["code"] == "store_id_required"
    assert efeitos == []


def test_compiled_slice_uses_store_id_survives_rename_and_preserves_extras(monkeypatch, tmp_path):
    compiled = tmp_path / "produtos_compilado.csv"
    pd.DataFrame(
        [
            {
                "sku": "001",
                "store_id": "0007",
                "loja_sync": "Nome Antigo",
                "saldo_loja": "1",
                "cest_bling": "cest-a",
                "coluna_extra": "extra-a",
            },
            {
                "sku": "001",
                "store_id": "0008",
                "loja_sync": "Loja Homonima",
                "saldo_loja": "2",
                "cest_bling": "cest-b",
                "coluna_extra": "extra-b",
            },
            {
                "sku": "009",
                "store_id": "",
                "loja_sync": "Loja Homonima",
                "saldo_loja": "9",
                "cest_bling": "cest-legado",
                "coluna_extra": "extra-legado",
            },
        ]
    ).to_csv(compiled, index=False)
    historico_chamadas = []
    monkeypatch.setattr(
        estoque_sync,
        "_registrar_snapshot_historico_estoque",
        lambda *_a, **_k: historico_chamadas.append("registrar"),
    )
    monkeypatch.setattr(
        estoque_sync,
        "_confirmar_evento_historico_estoque",
        lambda *_a, **_k: historico_chamadas.append("confirmar"),
    )
    monkeypatch.setattr(
        estoque_sync,
        "_descartar_evento_pendente_estoque",
        lambda *_a, **_k: historico_chamadas.append("descartar"),
    )

    total_historico = estoque_sync._atualizar_produtos_compilados_loja(
        "000002",
        "Loja Homonima",
        [
            {"sku": "001", "saldo_loja": "99"},
            {"sku": "002", "saldo_loja": "10"},
        ],
        str(compiled),
        "evento-store-a",
        store_id="0007",
        loja_nome_unico=False,
    )

    result = pd.read_csv(compiled, dtype=str, keep_default_na=False)
    store_a = result.loc[result["store_id"] == "0007"].set_index("sku")
    store_b = result.loc[result["store_id"] == "0008"].iloc[0]
    legacy = result.loc[result["store_id"] == ""].iloc[0]
    assert set(store_a.index) == {"001", "002"}
    assert store_a.at["001", "loja_sync"] == "Loja Homonima"
    assert store_a.at["001", "saldo_loja"] == "99"
    assert store_a.at["001", "cest_bling"] == "cest-a"
    assert store_a.at["001", "coluna_extra"] == "extra-a"
    assert store_a.at["002", "coluna_extra"] == ""
    assert (store_b["sku"], store_b["saldo_loja"], store_b["coluna_extra"]) == (
        "001",
        "2",
        "extra-b",
    )
    assert (legacy["sku"], legacy["loja_sync"], legacy["coluna_extra"]) == (
        "009",
        "Loja Homonima",
        "extra-legado",
    )
    assert total_historico == 0
    assert historico_chamadas == []


def test_compiled_slice_removes_legacy_name_only_when_current_name_is_unique(monkeypatch, tmp_path):
    compiled = tmp_path / "produtos_compilado.csv"
    pd.DataFrame(
        [
            {"sku": "001", "store_id": "", "loja_sync": "Loja Unica", "saldo_loja": "1"},
            {"sku": "002", "store_id": "store-b", "loja_sync": "Outra", "saldo_loja": "2"},
        ]
    ).to_csv(compiled, index=False)
    monkeypatch.setattr(estoque_sync, "_registrar_snapshot_historico_estoque", lambda *_a, **_k: 1)
    monkeypatch.setattr(estoque_sync, "_confirmar_evento_historico_estoque", lambda *_a, **_k: 1)
    monkeypatch.setattr(estoque_sync, "_descartar_evento_pendente_estoque", lambda *_a, **_k: None)

    estoque_sync._atualizar_produtos_compilados_loja(
        "000002",
        "Loja Unica",
        [{"sku": "001", "saldo_loja": "10"}],
        str(compiled),
        "evento-unico",
        store_id="store-a",
        loja_nome_unico=True,
    )

    result = pd.read_csv(compiled, dtype=str, keep_default_na=False)
    assert len(result) == 2
    assert result.loc[result["sku"] == "001", "store_id"].tolist() == ["store-a"]
    assert result.loc[result["store_id"] == "store-b", "saldo_loja"].tolist() == ["2"]


def test_estoque_impl_uses_exact_homonymous_credentials_and_persists_store_id(monkeypatch, tmp_path):
    stores = _homonymous_stores()
    monkeypatch.setattr(estoque_sync, "carregar_lojas", lambda _client_id: stores)
    monkeypatch.setattr(estoque_sync, "get_tenant_path", lambda _client_id: str(tmp_path), raising=False)
    monkeypatch.setattr(estoque_sync, "_novo_event_id_estoque", lambda: "evento-b")
    calls = []
    publications = []

    def execute(
        client_id,
        nome,
        cfg,
        _call,
        on_refresh=None,
        *,
        store_id=None,
        require_owned_refresh=False,
    ):
        assert require_owned_refresh is True
        calls.append((client_id, nome, cfg["access_token"], store_id))
        if len(calls) == 1:
            payload = [{
                "sku": "001",
                "id_bling": "b-1",
                "nome_bling": "Produto B",
                "situacao_bling": "A",
                "ncm_bling": "1234",
            }]
        elif len(calls) == 2:
            payload = {"deposito-b": "loja"}
        else:
            payload = {"b-1": {"loja": 7, "full": 3}}
        return payload, 200, cfg

    def publish(client_id, loja, records, path, event_id, **kwargs):
        publications.append((client_id, loja, records, path, event_id, kwargs))
        return 1

    monkeypatch.setattr(estoque_sync, "_bling_executar_com_refresh", execute, raising=False)
    monkeypatch.setattr(estoque_sync, "_bling_listar_produtos", lambda _token: None, raising=False)
    monkeypatch.setattr(estoque_sync, "_bling_map_depositos", lambda _token: None, raising=False)
    monkeypatch.setattr(estoque_sync, "_bling_saldos", lambda *_args: None, raising=False)
    monkeypatch.setattr(estoque_sync, "_atualizar_produtos_compilados_loja", publish)

    result = asyncio.run(
        estoque_sync._sincronizar_estoque_impl(
            EstoqueSyncRequest(loja="Loja Homonima", store_id="store-b"),
            "000002",
        )
    )

    assert result["success"] is True
    assert calls == [
        ("000002", "Loja Homonima", "token-b", "store-b"),
        ("000002", "Loja Homonima", "token-b", "store-b"),
        ("000002", "Loja Homonima", "token-b", "store-b"),
    ]
    assert len(publications) == 1
    _client, _name, records, _path, _event, kwargs = publications[0]
    assert kwargs == {"store_id": "store-b", "loja_nome_unico": False}
    assert records[0]["store_id"] == "store-b"
    assert records[0]["saldo_loja"] == 7
    assert records[0]["saldo_full"] == 3


def test_estoque_impl_revalidates_store_identity_before_publishing(monkeypatch, tmp_path):
    initial = [{
        "store_id": "store-a",
        "nome": "Loja A",
        "integracoes": {"bling": {
            "access_token": "token-a", "id": "id-a", "secret": "secret-a"
        }},
    }]
    renamed = [{
        **initial[0],
        "nome": "Loja Renomeada",
    }]
    config_reads = []

    def load_stores(_client_id):
        config_reads.append(1)
        return initial if len(config_reads) == 1 else renamed

    api_calls = []

    def execute(
        _client_id,
        _name,
        cfg,
        _call,
        on_refresh=None,
        *,
        store_id=None,
        require_owned_refresh=False,
    ):
        assert require_owned_refresh is True
        api_calls.append(store_id)
        if len(api_calls) == 1:
            payload = [{
                "sku": "001",
                "id_bling": "a-1",
                "nome_bling": "Produto A",
                "situacao_bling": "A",
                "ncm_bling": "1234",
            }]
        elif len(api_calls) == 2:
            payload = {"deposito-a": "loja"}
        else:
            payload = {"a-1": {"loja": 1, "full": 0}}
        return payload, 200, cfg

    publications = []
    monkeypatch.setattr(estoque_sync, "carregar_lojas", load_stores)
    monkeypatch.setattr(estoque_sync, "get_tenant_path", lambda _client_id: str(tmp_path), raising=False)
    monkeypatch.setattr(estoque_sync, "_novo_event_id_estoque", lambda: "evento-a")
    monkeypatch.setattr(estoque_sync, "_bling_executar_com_refresh", execute, raising=False)
    monkeypatch.setattr(estoque_sync, "_bling_listar_produtos", lambda _token: None, raising=False)
    monkeypatch.setattr(estoque_sync, "_bling_map_depositos", lambda _token: None, raising=False)
    monkeypatch.setattr(estoque_sync, "_bling_saldos", lambda *_args: None, raising=False)
    monkeypatch.setattr(
        estoque_sync,
        "_atualizar_produtos_compilados_loja",
        lambda *_args, **_kwargs: publications.append("publish"),
    )

    with pytest.raises(HTTPException) as changed:
        asyncio.run(
            estoque_sync._sincronizar_estoque_impl(
                EstoqueSyncRequest(loja="Loja A", store_id="store-a"),
                "000002",
            )
        )

    assert changed.value.status_code == 409
    assert len(config_reads) >= 2
    assert publications == []


def test_historico_lote_revalidates_store_before_database_writes(monkeypatch):
    config = {
        "store_id": "store-a",
        "nome": "Loja A",
        "integracoes": {"bling": {
            "access_token": "token-a", "id": "id-a", "secret": "secret-a"
        }},
    }
    resolutions = []

    def resolve(_client_id, _name, _store_id):
        resolutions.append(1)
        if len(resolutions) == 1:
            return config
        raise HTTPException(status_code=409, detail={"code": "store_identity_changed"})

    writes = []
    monkeypatch.setattr(estoque_lancamentos, "_resolver_loja_historico_segura", resolve)
    monkeypatch.setattr(
        estoque_lancamentos,
        "_bling_listar_naturezas",
        lambda _token: ({}, 200),
        raising=False,
    )
    monkeypatch.setattr(
        estoque_lancamentos,
        "_bling_listar_notas_entrada",
        lambda *_args, **_kwargs: ([], [], 200),
        raising=False,
    )
    monkeypatch.setattr(
        estoque_lancamentos,
        "_bling_listar_vendas_fallback_nf_saida",
        lambda *_args, **_kwargs: ([], 200),
        raising=False,
    )
    monkeypatch.setattr(
        estoque_lancamentos,
        "_limpar_lancamentos_nf_periodo",
        lambda *_args: writes.append("clear"),
    )
    monkeypatch.setattr(
        estoque_lancamentos,
        "_salvar_movimentos_nf_estoque",
        lambda *_args: writes.append("save"),
    )

    with pytest.raises(HTTPException) as changed:
        asyncio.run(
            estoque_lancamentos._sincronizar_lancamentos_estoque_lote_impl(
                EstoqueLancamentosSyncLoteRequest(
                    loja="Loja A",
                    store_id="store-a",
                    data_inicio="2026-08-01",
                    data_fim="2026-08-02",
                ),
                "000002",
            )
        )

    assert changed.value.status_code == 409
    assert len(resolutions) == 2
    assert writes == []
