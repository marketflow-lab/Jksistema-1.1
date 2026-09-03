from __future__ import annotations

import asyncio
import sqlite3

import pytest
from fastapi import HTTPException as FastAPIHTTPException

from backend.modules.vendas import legacy
from backend.modules.vendas.dependencies import LegacyBlingVendasAdapter
from backend.modules.vendas.errors import VendasDomainError
from backend.modules.vendas import sync_period
from backend.schemas import VendasSyncRequest
from backend.services import bling_vendas


def _create_database(path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE vendas (
                id_unico TEXT PRIMARY KEY, data TEXT, loja_conta TEXT, canal TEXT,
                numero TEXT, situacao TEXT, devolucao INTEGER, sku TEXT,
                produto TEXT, quantidade REAL, valor REAL, mes_ano TEXT,
                numero_nf TEXT, comprador TEXT, unidade_negocio TEXT,
                nota_fiscal_id TEXT, loja_id TEXT, unidade_id TEXT,
                intermediador_nome TEXT, intermediador_cnpj TEXT
            );
            CREATE TABLE notas_entrada (
                id_unico TEXT PRIMARY KEY, id_bling TEXT, numero TEXT,
                data_emissao TEXT, natureza_operacao TEXT, finalidade_operacao TEXT,
                devolucao INTEGER, valor REAL, fornecedor TEXT, origem_codigo TEXT,
                loja_conta TEXT, unidade_negocio TEXT, unidade_negocio_virtual TEXT
            );
            CREATE TABLE notas_entrada_itens (
                id_unico TEXT PRIMARY KEY, id_nota TEXT, numero_nota TEXT,
                origem_codigo TEXT, data_emissao TEXT, sku TEXT, descricao TEXT,
                quantidade REAL, valor_unitario REAL, valor_total REAL,
                natureza_operacao TEXT, finalidade_operacao TEXT, devolucao INTEGER,
                fornecedor TEXT, loja_conta TEXT, unidade_negocio TEXT,
                unidade_negocio_virtual TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO vendas (id_unico, data, loja_conta, sku) VALUES (?, ?, ?, ?)",
            ("old-sale", "2026-07-18", "Loja A", "OLD"),
        )
        conn.execute(
            "INSERT INTO notas_entrada (id_unico, data_emissao, loja_conta) VALUES (?, ?, ?)",
            ("old-note", "2026-07-18", "Loja A"),
        )
        conn.execute(
            "INSERT INTO notas_entrada_itens (id_unico, data_emissao, loja_conta) VALUES (?, ?, ?)",
            ("old-item", "2026-07-18", "Loja A"),
        )
        conn.commit()
    finally:
        conn.close()


def _payloads():
    vendas = [
        (
            "new-sale",
            "2026-07-18",
            "Loja A",
            "Canal",
            "1",
            "Atendido",
            0,
            "NEW",
            "Produto",
            1,
            10,
            "2026-07",
            "100",
            "Comprador",
            "Unidade",
            "nf-1",
            "loja-1",
            "unidade-1",
            "",
            "",
        )
    ]
    notas = [
        (
            "new-note",
            "note-1",
            "100",
            "2026-07-18",
            "Compra",
            "Normal",
            0,
            10,
            "Fornecedor",
            "origem",
            "Loja A",
            "Unidade",
            "Unidade",
        )
    ]
    itens = [
        (
            "new-item",
            "note-1",
            "100",
            "origem",
            "2026-07-18",
            "NEW",
            "Produto",
            1,
            10,
            10,
            "Compra",
            "Normal",
            0,
            "Fornecedor",
            "Loja A",
            "Unidade",
            "Unidade",
        )
    ]
    return vendas, notas, itens


def _create_legacy_database_without_devolucao(path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE vendas (
                id_unico TEXT PRIMARY KEY, data TEXT, loja_conta TEXT, canal TEXT,
                numero TEXT, situacao TEXT, sku TEXT, produto TEXT,
                quantidade REAL, valor REAL, mes_ano TEXT, numero_nf TEXT,
                comprador TEXT, unidade_negocio TEXT, nota_fiscal_id TEXT,
                loja_id TEXT, unidade_id TEXT, intermediador_nome TEXT,
                intermediador_cnpj TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO vendas (id_unico, data, loja_conta, sku) VALUES (?, ?, ?, ?)",
            ("old-sale", "2026-07-18", "Loja A", "OLD"),
        )
        conn.commit()
    finally:
        conn.close()


def _ids(path, table):
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute(f"SELECT id_unico FROM {table} ORDER BY id_unico")]
    finally:
        conn.close()


@pytest.mark.parametrize("fail_on_check", [1, 2, 3, 4])
def test_atomic_persistence_rolls_back_every_table_on_failure(
    tmp_path, monkeypatch, fail_on_check
) -> None:
    db_path = tmp_path / "vendas_historico_loja_a.db"
    _create_database(db_path)
    req = VendasSyncRequest(
        loja="Loja A",
        data_inicio="2026-07-18",
        data_fim="2026-07-18",
        forcar_resync=True,
    )
    vendas, notas, itens = _payloads()
    checks = 0

    def fail_during_transaction(_client_id):
        nonlocal checks
        checks += 1
        if checks == fail_on_check:
            raise VendasDomainError(status_code=409, detail="cancelado")

    monkeypatch.setattr(sync_period, "_verificar_cancelamento", fail_during_transaction)

    with pytest.raises(VendasDomainError) as exc:
        sync_period._persistir_periodo_atomico(
            str(db_path), req, vendas, notas, itens, "tenant-test"
        )

    assert exc.value.status_code == 409
    assert _ids(db_path, "vendas") == ["old-sale"]
    assert _ids(db_path, "notas_entrada") == ["old-note"]
    assert _ids(db_path, "notas_entrada_itens") == ["old-item"]


def test_valid_empty_force_resync_clears_all_three_tables(tmp_path) -> None:
    db_path = tmp_path / "vendas_historico_loja_a.db"
    _create_database(db_path)
    req = VendasSyncRequest(
        loja="Loja A",
        data_inicio="2026-07-18",
        data_fim="2026-07-18",
        forcar_resync=True,
    )

    result = sync_period._persistir_periodo_atomico(
        str(db_path), req, [], [], [], "tenant-test"
    )

    assert result == {"vendas": 0, "notas": 0, "itens": 0}
    assert _ids(db_path, "vendas") == []
    assert _ids(db_path, "notas_entrada") == []
    assert _ids(db_path, "notas_entrada_itens") == []


def test_persistencia_migra_banco_legado_dentro_da_transacao(tmp_path) -> None:
    db_path = tmp_path / "vendas_historico_loja_a.db"
    _create_legacy_database_without_devolucao(db_path)
    req = VendasSyncRequest(
        loja="Loja A",
        data_inicio="2026-07-18",
        data_fim="2026-07-18",
        forcar_resync=True,
    )

    vendas, notas, itens = _payloads()
    result = sync_period._persistir_periodo_atomico(
        str(db_path), req, vendas, notas, itens, "tenant-test"
    )

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(vendas)")}
    finally:
        conn.close()
    assert "devolucao" in columns
    assert result == {"vendas": 1, "notas": 1, "itens": 1}
    assert _ids(db_path, "vendas") == ["new-sale"]


def test_falha_apos_alter_reverte_dados_e_schema_legado(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "vendas_historico_loja_a.db"
    _create_legacy_database_without_devolucao(db_path)
    req = VendasSyncRequest(
        loja="Loja A",
        data_inicio="2026-07-18",
        data_fim="2026-07-18",
        forcar_resync=True,
    )
    vendas, notas, itens = _payloads()
    monkeypatch.setattr(
        sync_period,
        "_verificar_cancelamento",
        lambda _client: (_ for _ in ()).throw(
            VendasDomainError(status_code=409, detail="cancelado")
        ),
    )

    with pytest.raises(VendasDomainError):
        sync_period._persistir_periodo_atomico(
            str(db_path), req, vendas, notas, itens, "tenant-test"
        )

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(vendas)")}
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()
    assert "devolucao" not in columns
    assert "notas_entrada" not in tables
    assert "notas_entrada_itens" not in tables
    assert _ids(db_path, "vendas") == ["old-sale"]


def test_empty_non_force_sync_preserves_existing_day(tmp_path) -> None:
    db_path = tmp_path / "vendas_historico_loja_a.db"
    _create_database(db_path)
    req = VendasSyncRequest(
        loja="Loja A",
        data_inicio="2026-07-18",
        data_fim="2026-07-18",
        forcar_resync=False,
    )

    sync_period._persistir_periodo_atomico(
        str(db_path), req, [], [], [], "tenant-test"
    )

    assert _ids(db_path, "vendas") == ["old-sale"]
    assert _ids(db_path, "notas_entrada") == ["old-note"]
    assert _ids(db_path, "notas_entrada_itens") == ["old-item"]


def test_payload_rejeita_registro_fora_do_dia_solicitado(monkeypatch) -> None:
    monkeypatch.setattr(sync_period, "_verificar_cancelamento", lambda _client: None)
    req = VendasSyncRequest(
        loja="Loja A",
        data_inicio="2026-07-18",
        data_fim="2026-07-18",
        forcar_resync=True,
    )

    with pytest.raises(VendasDomainError) as exc:
        sync_period._preparar_payload_vendas(
            [{"data": "2026-07-17", "numero": "1", "sku": "SKU", "quantidade": 1, "valor": 1}],
            req,
            "tenant-test",
        )

    assert exc.value.status_code == 502


def test_reapplying_same_payload_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "vendas_historico_loja_a.db"
    _create_database(db_path)
    req = VendasSyncRequest(
        loja="Loja A",
        data_inicio="2000-01-01",
        data_fim="2000-01-01",
        forcar_resync=False,
    )
    vendas, notas, itens = _payloads()
    venda = list(vendas[0])
    venda[1] = "2000-01-01"
    venda[11] = "2000-01"
    nota = list(notas[0])
    nota[3] = "2000-01-01"
    item = list(itens[0])
    item[4] = "2000-01-01"

    first = sync_period._persistir_periodo_atomico(
        str(db_path), req, [tuple(venda)], [tuple(nota)], [tuple(item)], "tenant-test"
    )
    second = sync_period._persistir_periodo_atomico(
        str(db_path), req, [tuple(venda)], [tuple(nota)], [tuple(item)], "tenant-test"
    )

    assert first == {"vendas": 1, "notas": 1, "itens": 1}
    assert second == {"vendas": 0, "notas": 1, "itens": 1}
    assert _ids(db_path, "vendas") == ["new-sale", "old-sale"]
    assert _ids(db_path, "notas_entrada") == ["new-note", "old-note"]
    assert _ids(db_path, "notas_entrada_itens") == ["new-item", "old-item"]


def test_mandatory_note_detail_failure_aborts_before_database_write(monkeypatch) -> None:
    cfg = {
        "access_token": "access",
        "refresh_token": "refresh",
        "id": "client",
        "secret": "secret",
    }
    database_touched = False

    def execute_with_refresh(
        _client_id,
        _loja,
        current_cfg,
        chamada,
        on_refresh=None,
        *,
        store_id,
    ):
        assert store_id == "store-a"
        result, status = chamada(current_cfg["access_token"])
        return result, status, current_cfg

    def database_path(*_args, **_kwargs):
        nonlocal database_touched
        database_touched = True
        raise AssertionError("database must not be touched after incomplete collection")

    monkeypatch.setattr(
        sync_period,
        "buscar_loja",
        lambda *_args: {"store_id": "store-a", "integracoes": {"bling": cfg}},
    )
    monkeypatch.setattr(sync_period, "_carregar_mapeamento_unidades", lambda: {})
    monkeypatch.setattr(
        sync_period, "_carregar_mapeamento_lojas_virtuais_cliente", lambda _client: {}
    )
    monkeypatch.setattr(sync_period, "_bling_executar_com_refresh", execute_with_refresh)
    monkeypatch.setattr(sync_period, "_bling_listar_vendas", lambda *_args: ([], 200))
    monkeypatch.setattr(
        sync_period, "_bling_listar_vendas_fallback_nf_saida", lambda *_args: ([], 403)
    )
    monkeypatch.setattr(sync_period, "_bling_listar_naturezas", lambda *_args: ({}, 200))
    monkeypatch.setattr(
        sync_period,
        "_bling_listar_notas_entrada",
        lambda *_args: (
            [
                {
                    "id": "note-1",
                    "numero": "100",
                    "data_emissao": "2026-07-18",
                    "natureza_operacao": "Compra",
                }
            ],
            [],
            200,
        ),
    )
    monkeypatch.setattr(sync_period, "_bling_obter_detalhes_nf", lambda *_args: (None, 503))
    monkeypatch.setattr(sync_period, "_get_vendas_db_path", database_path)

    req = VendasSyncRequest(
        loja="Loja A",
        data_inicio="2026-07-18",
        data_fim="2026-07-18",
        forcar_resync=True,
    )
    with pytest.raises(VendasDomainError) as exc:
        asyncio.run(
            sync_period._sincronizar_vendas_periodo_impl(
                req, "tenant-test", reset_estado=False
            )
        )

    assert exc.value.status_code == 503
    assert database_touched is False


def test_full_valid_empty_force_sync_clears_only_after_collection(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "vendas_historico_loja_a.db"
    _create_database(db_path)
    cfg = {
        "access_token": "access",
        "refresh_token": "refresh",
        "id": "client",
        "secret": "secret",
    }
    collected = []

    def execute_with_refresh(
        _client_id,
        _loja,
        current_cfg,
        chamada,
        on_refresh=None,
        *,
        store_id,
    ):
        assert store_id == "store-a"
        result, status = chamada(current_cfg["access_token"])
        return result, status, current_cfg

    def list_sales(*_args):
        collected.append("sales")
        return [], 200

    def list_outbound(*_args):
        collected.append("outbound")
        return [], 403

    def list_natures(*_args):
        collected.append("natures")
        return {}, 200

    def list_notes(*_args):
        collected.append("notes")
        return [], [], 200

    def initialize_database(*_args):
        assert collected == ["sales", "outbound", "natures", "notes"]
        return str(db_path)

    monkeypatch.setattr(
        sync_period,
        "buscar_loja",
        lambda *_args: {"store_id": "store-a", "integracoes": {"bling": cfg}},
    )
    monkeypatch.setattr(sync_period, "_carregar_mapeamento_unidades", lambda: {})
    monkeypatch.setattr(
        sync_period, "_carregar_mapeamento_lojas_virtuais_cliente", lambda _client: {}
    )
    monkeypatch.setattr(sync_period, "_bling_executar_com_refresh", execute_with_refresh)
    monkeypatch.setattr(sync_period, "_bling_listar_vendas", list_sales)
    monkeypatch.setattr(sync_period, "_bling_listar_vendas_fallback_nf_saida", list_outbound)
    monkeypatch.setattr(sync_period, "_bling_listar_naturezas", list_natures)
    monkeypatch.setattr(sync_period, "_bling_listar_notas_entrada", list_notes)
    monkeypatch.setattr(sync_period, "_get_vendas_db_path", initialize_database)

    req = VendasSyncRequest(
        loja="Loja A",
        data_inicio="2026-07-18",
        data_fim="2026-07-18",
        forcar_resync=True,
    )
    result = asyncio.run(
        sync_period._sincronizar_vendas_periodo_impl(
            req, "tenant-test", reset_estado=False
        )
    )

    assert result["success"] is True
    assert result["total"] == result["notas_entrada_total"] == 0
    assert _ids(db_path, "vendas") == []
    assert _ids(db_path, "notas_entrada") == []
    assert _ids(db_path, "notas_entrada_itens") == []


def test_legacy_adapter_translates_fastapi_http_exception(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise FastAPIHTTPException(
            status_code=429,
            detail="aguarde",
            headers={"Retry-After": "3"},
        )

    monkeypatch.setattr(bling_vendas, "_bling_listar_vendas", fail)

    with pytest.raises(VendasDomainError) as exc:
        LegacyBlingVendasAdapter().call("_bling_listar_vendas", "token")

    assert exc.value.status_code == 429
    assert exc.value.detail == "aguarde"
    assert exc.value.headers == {"Retry-After": "3"}


def test_legacy_exports_delegate_through_installed_adapter(monkeypatch) -> None:
    calls = []

    def fake_call(helper_name, *args, **kwargs):
        calls.append((helper_name, args, kwargs))
        return "delegated"

    monkeypatch.setattr(legacy, "legacy_call", fake_call)

    assert legacy._bling_listar_naturezas("token") == "delegated"
    assert calls == [("_bling_listar_naturezas", ("token",), {})]
