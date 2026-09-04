import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from backend.services import estoque_historico, estoque_lancamentos, estoque_sync


@pytest.fixture(autouse=True)
def _loja_unica_para_rotas_de_historico(monkeypatch):
    monkeypatch.setattr(
        estoque_lancamentos,
        "_carregar_lojas_historico",
        lambda _client_id: [{"store_id": "store-a", "nome": "Loja A"}],
    )


def _configurar_tenant(monkeypatch, tmp_path):
    def tenant_path(client_id):
        path = tmp_path / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        config = path / "lojas_config.json"
        if not config.exists():
            config.write_text(
                json.dumps([
                    {"store_id": "store-a", "nome": "Loja A"},
                    {"store_id": "store-b", "nome": "Loja B"},
                ]),
                encoding="utf-8",
            )
        return str(path)

    monkeypatch.setattr(estoque_historico, "get_tenant_path", tenant_path)
    return tenant_path


def _registros(saldo_a=10, saldo_b=20):
    return [
        {"sku": "a", "id_bling": "1", "saldo_loja": saldo_a, "saldo_full": 2},
        {"sku": "B", "id_bling": "2", "saldo_loja": saldo_b, "saldo_full": 3},
    ]


def test_duas_atualizacoes_no_mesmo_dia_preservam_eventos_totais_e_legado(monkeypatch, tmp_path):
    tenant_path = _configurar_tenant(monkeypatch, tmp_path)

    assert estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a",
        "Loja A",
        _registros(10, 20),
        event_id="evento-1",
        recorded_at="2026-07-23T10:00:00-03:00",
    ) == 2
    assert estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a",
        "Loja A",
        _registros(12, 25),
        event_id="evento-2",
        recorded_at="2026-07-23T11:00:00-03:00",
    ) == 2

    db_path = tmp_path / "cliente-a" / "estoque_historico.db"
    conn = sqlite3.connect(db_path)
    try:
        eventos = conn.execute(
            "SELECT event_id, total_skus, saldo_loja_total, saldo_full_total, saldo_total "
            "FROM estoque_sync_eventos ORDER BY recorded_at"
        ).fetchall()
        assert eventos == [
            ("evento-1", 2, 30.0, 5.0, 35.0),
            ("evento-2", 2, 37.0, 5.0, 42.0),
        ]
        assert conn.execute("SELECT COUNT(*) FROM estoque_sync_evento_itens").fetchone()[0] == 4
        legado = conn.execute(
            "SELECT sku, saldo_loja FROM estoque_historico ORDER BY sku"
        ).fetchall()
        assert legado == [("A", 12.0), ("B", 25.0)]
    finally:
        conn.close()

    # O mesmo event_id com o mesmo payload e uma tentativa posterior não duplica.
    assert estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a",
        "Loja A",
        _registros(10, 20),
        event_id="evento-1",
        recorded_at="2026-07-23T12:00:00-03:00",
    ) == 2
    conn = sqlite3.connect(tenant_path("cliente-a") + "/estoque_historico.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM estoque_sync_eventos").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM estoque_sync_evento_itens").fetchone()[0] == 4
    finally:
        conn.close()

    with pytest.raises(ValueError, match="payload"):
        estoque_historico._registrar_snapshot_historico_estoque(
            "cliente-a",
            "Loja A",
            _registros(999, 20),
            event_id="evento-1",
            recorded_at="2026-07-23T13:00:00-03:00",
        )


def test_evento_e_itens_sao_atomicos(monkeypatch, tmp_path):
    _configurar_tenant(monkeypatch, tmp_path)
    estoque_historico._garantir_tabela_historico_estoque("cliente-a")
    db_path = tmp_path / "cliente-a" / "estoque_historico.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TRIGGER falhar_item BEFORE INSERT ON estoque_sync_evento_itens
            WHEN NEW.sku = 'FAIL'
            BEGIN
                SELECT RAISE(ABORT, 'falha controlada');
            END
            """
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="falha controlada"):
        estoque_historico._registrar_snapshot_historico_estoque(
            "cliente-a",
            "Loja A",
            [
                {"sku": "OK", "saldo_loja": 1},
                {"sku": "FAIL", "saldo_loja": 2},
            ],
            event_id="evento-falha",
            recorded_at="2026-07-23T10:00:00-03:00",
        )

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM estoque_sync_eventos").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM estoque_sync_evento_itens").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM estoque_historico").fetchone()[0] == 0
    finally:
        conn.close()


def test_indices_dos_eventos_e_itens_sao_usados_sem_scan_de_itens(monkeypatch, tmp_path):
    _configurar_tenant(monkeypatch, tmp_path)
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a", "Loja A", _registros(),
        event_id="evento-indices", recorded_at="2026-07-23T10:00:00-03:00",
    )
    conn = sqlite3.connect(tmp_path / "cliente-a" / "estoque_historico.db")
    try:
        plano_rota = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT e.event_id, i.saldo_loja
            FROM estoque_sync_eventos e
            LEFT JOIN estoque_sync_evento_itens i
              ON i.event_id = e.event_id AND i.sku = ?
            WHERE e.status = 'committed'
              AND e.loja_sync = ? COLLATE NOCASE
              AND e.data_ref >= ? AND e.data_ref <= ?
            ORDER BY e.recorded_at, e.event_id
            """,
            ("A", "loja a", "2026-07-01", "2026-07-31"),
        ).fetchall()
        plano_vendas_sku = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT i.event_id, i.saldo_loja
            FROM estoque_sync_evento_itens i
            JOIN estoque_sync_eventos e ON e.event_id = i.event_id
            WHERE e.status = 'committed' AND i.sku = ? AND e.data_ref <= ?
            """,
            ("A", "2026-07-31"),
        ).fetchall()
    finally:
        conn.close()

    detalhes_rota = " | ".join(str(row[3]) for row in plano_rota)
    detalhes_vendas = " | ".join(str(row[3]) for row in plano_vendas_sku)
    assert "SCAN I" not in detalhes_rota.upper()
    assert "SCAN I" not in detalhes_vendas.upper()
    assert "idx_estoque_eventos_status_loja_data" in detalhes_rota
    assert (
        "idx_estoque_evento_itens_sku_evento" in detalhes_vendas
        or "sqlite_autoindex_estoque_sync_evento_itens_1" in detalhes_vendas
    )

    def nao_abrir_csv(*_args, **_kwargs):
        raise AssertionError("CSV nao deve ser lido sem evento pending")

    monkeypatch.setattr("builtins.open", nao_abrir_csv)
    assert estoque_historico._recuperar_eventos_pendentes_publicados("cliente-a") == 0


def test_csv_so_e_publicado_depois_do_historico_e_falha_preserva_anterior(monkeypatch, tmp_path):
    arquivo_cliente = tmp_path / "produtos_compilado.csv"
    arquivo_cliente.write_text("sku,saldo_loja\nANTIGO,5\n", encoding="utf-8")
    df_saida = estoque_sync.pd.DataFrame([{"sku": "NOVO", "saldo_loja": 10}])

    def falhar_historico(*_args, **_kwargs):
        temporarios = list(tmp_path.glob(".produtos_compilado.*.tmp"))
        assert len(temporarios) == 1
        assert arquivo_cliente.read_text(encoding="utf-8") == "sku,saldo_loja\nANTIGO,5\n"
        raise RuntimeError("falha controlada no historico")

    monkeypatch.setattr(estoque_sync, "_registrar_snapshot_historico_estoque", falhar_historico)
    with pytest.raises(RuntimeError, match="falha controlada"):
        estoque_sync._publicar_csv_estoque_apos_historico(
            "cliente-a", "Loja A", [{"sku": "NOVO", "saldo_loja": 10}],
            df_saida, str(arquivo_cliente), "evento-falha-csv",
        )
    assert arquivo_cliente.read_text(encoding="utf-8") == "sku,saldo_loja\nANTIGO,5\n"
    assert list(tmp_path.glob(".produtos_compilado.*.tmp")) == []

    ordem = []
    replace_real = estoque_sync.os.replace

    def salvar_historico(*_args, **_kwargs):
        assert arquivo_cliente.read_text(encoding="utf-8") == "sku,saldo_loja\nANTIGO,5\n"
        assert _kwargs["status"] == "pending"
        assert len(_kwargs["csv_hash"]) == 64
        ordem.append("historico")
        return 1

    def publicar_csv(origem, destino):
        assert ordem == ["historico"]
        ordem.append("replace")
        return replace_real(origem, destino)

    def confirmar_historico(*_args, **_kwargs):
        assert ordem == ["historico", "replace"]
        ordem.append("confirmacao")
        return 1

    monkeypatch.setattr(estoque_sync, "_registrar_snapshot_historico_estoque", salvar_historico)
    monkeypatch.setattr(estoque_sync, "_confirmar_evento_historico_estoque", confirmar_historico)
    monkeypatch.setattr(estoque_sync.os, "replace", publicar_csv)
    assert estoque_sync._publicar_csv_estoque_apos_historico(
        "cliente-a", "Loja A", [{"sku": "NOVO", "saldo_loja": 10}],
        df_saida, str(arquivo_cliente), "evento-ok-csv",
    ) == 1
    assert ordem == ["historico", "replace", "confirmacao"]
    assert "NOVO,10" in arquivo_cliente.read_text(encoding="utf-8")
    assert list(tmp_path.glob(".produtos_compilado.*.tmp")) == []


def test_leitura_recupera_pending_publicado_sem_reusar_event_id(monkeypatch, tmp_path):
    tenant_path = _configurar_tenant(monkeypatch, tmp_path)
    tenant_path("cliente-retry")
    arquivo_cliente = tmp_path / "cliente-retry" / "produtos_compilado.csv"
    arquivo_cliente.write_text("sku,saldo_loja\nANTIGO,5\n", encoding="utf-8")
    df_saida = estoque_sync.pd.DataFrame([{"sku": "A", "saldo_loja": 10, "saldo_full": 2}])
    registros = [{"sku": "A", "saldo_loja": 10, "saldo_full": 2}]
    confirmar_real = estoque_sync._confirmar_evento_historico_estoque

    def falhar_confirmacao(*_args, **_kwargs):
        raise RuntimeError("falha controlada na confirmacao")

    monkeypatch.setattr(estoque_sync, "_confirmar_evento_historico_estoque", falhar_confirmacao)
    with pytest.raises(estoque_sync._EstoquePublicacaoInconclusiva) as incerta:
        estoque_sync._publicar_csv_estoque_apos_historico(
            "cliente-retry", "Loja A", registros, df_saida,
            str(arquivo_cliente), "evento-retry",
        )
    assert isinstance(incerta.value.__cause__, RuntimeError)
    assert "confirmacao" in str(incerta.value.__cause__)

    db_path = tmp_path / "cliente-retry" / "estoque_historico.db"
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT status FROM estoque_sync_eventos WHERE event_id = 'evento-retry'"
        ).fetchone() == ("pending",)
        assert conn.execute("SELECT COUNT(*) FROM estoque_historico").fetchone()[0] == 0
    finally:
        conn.close()
    monkeypatch.setattr(estoque_sync, "_confirmar_evento_historico_estoque", confirmar_real)
    serie_recuperada = estoque_historico._estoque_serie_eventos(
        "cliente-retry", "Loja A", "atualizacao",
        datetime.fromisoformat("2026-01-01"), datetime.now(), "A",
    )
    assert serie_recuperada is not None
    assert serie_recuperada["event_ids"] == ["evento-retry"]
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT status, COUNT(*) FROM estoque_sync_eventos GROUP BY status"
        ).fetchall() == [("committed", 1)]
        assert conn.execute("SELECT sku, saldo_loja FROM estoque_historico").fetchall() == [("A", 10.0)]
    finally:
        conn.close()
    assert "A,10,2" in arquivo_cliente.read_text(encoding="utf-8")

    tenant_path("cliente-divergente")
    csv_divergente = tmp_path / "cliente-divergente" / "produtos_compilado.csv"
    csv_divergente.write_text("sku,saldo_loja\nOUTRO,99\n", encoding="utf-8")
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-divergente",
        "Loja A",
        registros,
        event_id="evento-hash-divergente",
        status="pending",
        csv_hash="f" * 64,
    )
    assert estoque_historico._estoque_serie_eventos(
        "cliente-divergente", "Loja A", "atualizacao",
        datetime.fromisoformat("2026-01-01"), datetime.now(), "A",
    ) is None
    vendas_pendente = estoque_historico._vendas_series_estoque_historico(
        "cliente-divergente", "Loja A", "dia",
        datetime.fromisoformat("2026-01-01"), datetime.now(),
        [datetime.now().strftime("%Y-%m-%d")], True, True, "A",
    )
    assert vendas_pendente["estoque_geral"] == [None]
    assert vendas_pendente["estoque_sku"] == [None]
    assert vendas_pendente["estoque_skus_com_saldo"] == [None]
    conn = sqlite3.connect(tmp_path / "cliente-divergente" / "estoque_historico.db")
    try:
        assert conn.execute(
            "SELECT status FROM estoque_sync_eventos WHERE event_id = 'evento-hash-divergente'"
        ).fetchone() == ("pending",)
        assert conn.execute("SELECT COUNT(*) FROM estoque_historico").fetchone()[0] == 0
    finally:
        conn.close()


def test_replace_permission_error_descarta_pending_e_preserva_csv(monkeypatch, tmp_path):
    _configurar_tenant(monkeypatch, tmp_path)
    arquivo_cliente = tmp_path / "cliente-falha" / "produtos_compilado.csv"
    arquivo_cliente.parent.mkdir(parents=True, exist_ok=True)
    conteudo_antigo = "sku,saldo_loja\nANTIGO,5\n"
    arquivo_cliente.write_text(conteudo_antigo, encoding="utf-8")
    df_saida = estoque_sync.pd.DataFrame([{"sku": "A", "saldo_loja": 10}])

    def negar_replace(*_args, **_kwargs):
        raise PermissionError("arquivo em uso")

    monkeypatch.setattr(estoque_sync.os, "replace", negar_replace)
    with pytest.raises(PermissionError, match="arquivo em uso"):
        estoque_sync._publicar_csv_estoque_apos_historico(
            "cliente-falha", "Loja A", [{"sku": "A", "saldo_loja": 10}],
            df_saida, str(arquivo_cliente), "evento-sem-replace",
        )

    assert arquivo_cliente.read_text(encoding="utf-8") == conteudo_antigo
    assert list(arquivo_cliente.parent.glob(".produtos_compilado.*.tmp")) == []
    conn = sqlite3.connect(tmp_path / "cliente-falha" / "estoque_historico.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM estoque_sync_eventos").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM estoque_sync_eventos WHERE status = 'committed'"
        ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM estoque_historico").fetchone()[0] == 0
    finally:
        conn.close()

def test_series_usam_eventos_por_atualizacao_sku_loja_e_preservam_lacuna(monkeypatch, tmp_path):
    _configurar_tenant(monkeypatch, tmp_path)
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a", "Loja A", _registros(10, 20),
        event_id="evento-1", recorded_at="2026-07-23T10:00:00-03:00",
    )
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a", "Loja A", _registros(12, 25),
        event_id="evento-2", recorded_at="2026-07-23T11:00:00-03:00",
    )
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a", "Loja B", _registros(100, 200),
        event_id="evento-outra-loja", recorded_at="2026-07-23T11:30:00-03:00",
    )
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a", "Loja A", [{"sku": "B", "saldo_loja": 30, "saldo_full": 4}],
        event_id="evento-sem-a", recorded_at="2026-07-24T09:00:00-03:00",
    )

    por_atualizacao = asyncio.run(estoque_lancamentos.estoque_serie_retroativa(
        loja="Loja A",
        store_id="store-a",
        intervalo="atualizacao",
        sku="A",
        data_inicio="2026-07-23",
        data_fim="2026-07-23",
        client_id="cliente-a",
    ))
    assert por_atualizacao["historico_fonte"] == "eventos"
    assert por_atualizacao["event_ids"] == ["evento-1", "evento-2"]
    assert por_atualizacao["saldo_retroativo"] == [10.0, 12.0]

    diaria = asyncio.run(estoque_lancamentos.estoque_serie_retroativa(
        loja="Loja A",
        store_id="store-a",
        intervalo="dia",
        data_inicio="2026-07-23",
        data_fim="2026-07-23",
        client_id="cliente-a",
    ))
    assert diaria["saldo_retroativo"] == [37.0]
    assert diaria["event_ids"] == ["evento-2"]

    vendas = estoque_historico._vendas_series_estoque_historico(
        "cliente-a",
        "Loja A",
        "dia",
        datetime.fromisoformat("2026-07-23"),
        datetime.fromisoformat("2026-07-24"),
        ["2026-07-23", "2026-07-24"],
        True,
        True,
        "A",
    )
    assert vendas["estoque_meta"]["fonte"] == "eventos"
    assert vendas["estoque_geral"] == [37.0, 30.0]
    assert vendas["estoque_sku"] == [12.0, None]
    assert vendas["estoque_skus_com_saldo"] == [2, 1]


def test_vendas_multiloja_carrega_snapshot_anterior_sem_total_parcial(monkeypatch, tmp_path):
    _configurar_tenant(monkeypatch, tmp_path)
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a", "Loja A", _registros(10, 20),
        event_id="loja-a-dia-23", recorded_at="2026-07-23T10:00:00-03:00",
    )
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a", "Loja B", _registros(100, 200),
        event_id="loja-b-dia-23", recorded_at="2026-07-23T10:30:00-03:00",
    )
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a", "Loja B", _registros(150, 250),
        event_id="loja-b-dia-24", recorded_at="2026-07-24T10:30:00-03:00",
    )

    vendas = estoque_historico._vendas_series_estoque_historico(
        "cliente-a",
        "__todas",
        "dia",
        datetime.fromisoformat("2026-07-23"),
        datetime.fromisoformat("2026-07-24"),
        ["2026-07-23", "2026-07-24"],
        True,
        True,
        "A",
    )
    assert vendas["estoque_geral"] == [330.0, 430.0]
    assert vendas["estoque_sku"] == [110.0, 160.0]
    assert vendas["estoque_skus_com_saldo"] == [2, 2]
    assert vendas["estoque_meta"]["lojas"] == 2


def test_vendas_multiloja_usa_cadastro_atual_e_expoe_cobertura_parcial(monkeypatch, tmp_path):
    tenant_path = _configurar_tenant(monkeypatch, tmp_path)
    caminho_config = Path(tenant_path("cliente-a")) / "lojas_config.json"
    caminho_config.write_text(
        json.dumps([
            {"nome": "Loja A"},
            {"nome": "Loja B"},
            {"nome": "Loja C"},
        ]),
        encoding="utf-8",
    )
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a", "Loja A", _registros(10, 20),
        event_id="loja-a", recorded_at="2026-07-23T10:00:00-03:00",
    )
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a", "Loja B", _registros(100, 200),
        event_id="loja-b", recorded_at="2026-07-24T10:00:00-03:00",
    )
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a", "Loja Antiga", _registros(1000, 2000),
        event_id="loja-antiga", recorded_at="2026-07-23T11:00:00-03:00",
    )

    vendas = estoque_historico._vendas_series_estoque_historico(
        "cliente-a",
        "__todas",
        "dia",
        datetime.fromisoformat("2026-07-23"),
        datetime.fromisoformat("2026-07-24"),
        ["2026-07-23", "2026-07-24"],
        True,
        False,
        None,
    )

    assert vendas["estoque_geral"] == [30.0, 330.0]
    assert vendas["estoque_skus_com_saldo"] == [2, 2]
    assert vendas["estoque_meta"]["lojas"] == 3
    assert vendas["estoque_meta"]["lojas_com_historico"] == 2
    assert vendas["estoque_meta"]["lojas_sem_historico"] == ["Loja C"]
    assert vendas["estoque_meta"]["lojas_historicas_ignoradas"] == 1
    assert vendas["estoque_meta"]["lojas_cobertas_por_periodo"] == [1, 2]
    assert "Cobertura parcial: 2 de 3 lojas" in vendas["estoque_meta"]["detail"]


def test_vendas_conta_skus_positivos_unicos_no_estoque_multiloja(monkeypatch, tmp_path):
    _configurar_tenant(monkeypatch, tmp_path)
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a",
        "Loja A",
        [
            {"sku": "A", "saldo_loja": 5},
            {"sku": "B", "saldo_loja": 0},
            {"sku": "C", "saldo_loja": -2},
        ],
        event_id="loja-a",
        recorded_at="2026-07-23T10:00:00-03:00",
    )
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a",
        "Loja B",
        [
            {"sku": "a", "saldo_loja": 7},
            {"sku": "C", "saldo_loja": 3},
            {"sku": "D", "saldo_loja": 4},
        ],
        event_id="loja-b",
        recorded_at="2026-07-23T10:30:00-03:00",
    )

    vendas = estoque_historico._vendas_series_estoque_historico(
        "cliente-a",
        "__todas",
        "dia",
        datetime.fromisoformat("2026-07-23"),
        datetime.fromisoformat("2026-07-23"),
        ["2026-07-23"],
        True,
        False,
        None,
    )

    assert vendas["estoque_geral"] == [17.0]
    assert vendas["estoque_skus_com_saldo"] == [3]


def test_migracao_aditiva_combina_legado_anterior_com_evento_novo(monkeypatch, tmp_path):
    _configurar_tenant(monkeypatch, tmp_path)
    db_path = tmp_path / "cliente-a" / "estoque_historico.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE estoque_historico (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_ref TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                loja_sync TEXT NOT NULL,
                sku TEXT NOT NULL,
                id_bling TEXT,
                nome_bling TEXT,
                situacao_bling TEXT,
                ncm_bling TEXT,
                saldo_loja REAL NOT NULL DEFAULT 0,
                saldo_full REAL NOT NULL DEFAULT 0,
                saldo_total REAL NOT NULL DEFAULT 0,
                UNIQUE(data_ref, loja_sync, sku)
            )
            """
        )
        conn.execute(
            "INSERT INTO estoque_historico "
            "(data_ref, recorded_at, loja_sync, sku, saldo_loja, saldo_full, saldo_total) "
            "VALUES ('2026-07-20', '2026-07-20T10:00:00', 'Loja A', 'A', 7, 50, 57)"
        )
        conn.commit()
    finally:
        conn.close()

    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a", "Loja A", [{"sku": "A", "saldo_loja": 9, "saldo_full": 60}],
        event_id="evento-novo", recorded_at="2026-07-23T10:00:00-03:00",
    )
    serie = asyncio.run(estoque_lancamentos.estoque_serie_retroativa(
        loja="Loja A",
        store_id="store-a",
        intervalo="dia",
        sku="A",
        data_inicio="2026-07-20",
        data_fim="2026-07-23",
        client_id="cliente-a",
    ))
    assert serie["historico_fonte"] == "eventos"
    assert serie["labels"] == ["2026-07-20", "2026-07-23"]
    assert serie["saldo_retroativo"] == [7.0, 9.0]
    assert serie["event_ids"][0].startswith("legacy-")
    assert serie["event_ids"][1] == "evento-novo"

    vendas = estoque_historico._vendas_series_estoque_historico(
        "cliente-a",
        "Loja A",
        "dia",
        datetime.fromisoformat("2026-07-20"),
        datetime.fromisoformat("2026-07-23"),
        ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23"],
        True,
        True,
        "A",
    )
    assert vendas["estoque_geral"] == [7.0, 7.0, 7.0, 9.0]
    assert vendas["estoque_sku"] == [7.0, 7.0, 7.0, 9.0]
    assert vendas["estoque_skus_com_saldo"] == [1, 1, 1, 1]

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM estoque_historico").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM estoque_sync_eventos").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM estoque_historico_migracoes").fetchone()[0] == 1
    finally:
        conn.close()


def test_fallback_legado_e_isolamento_por_cliente(monkeypatch, tmp_path):
    _configurar_tenant(monkeypatch, tmp_path)
    db_path = tmp_path / "cliente-legado" / "estoque_historico.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE estoque_historico (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_ref TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                loja_sync TEXT NOT NULL,
                sku TEXT NOT NULL,
                id_bling TEXT,
                nome_bling TEXT,
                situacao_bling TEXT,
                ncm_bling TEXT,
                saldo_loja REAL NOT NULL DEFAULT 0,
                saldo_full REAL NOT NULL DEFAULT 0,
                saldo_total REAL NOT NULL DEFAULT 0,
                UNIQUE(data_ref, loja_sync, sku)
            )
            """
        )
        conn.execute(
            "INSERT INTO estoque_historico "
            "(data_ref, recorded_at, loja_sync, sku, saldo_loja, saldo_full, saldo_total) "
            "VALUES ('2026-07-20', '2026-07-20T10:00:00', 'Loja A', 'A', 7, 50, 57)"
        )
        conn.commit()
    finally:
        conn.close()
    legado = asyncio.run(estoque_lancamentos.estoque_serie_retroativa(
        loja="Loja A",
        store_id="store-a",
        intervalo="dia",
        data_inicio="2026-07-20",
        data_fim="2026-07-20",
        client_id="cliente-legado",
    ))
    assert legado["saldo_retroativo"] == [7.0]
    assert legado["fonte"] == "lancamentos"
    assert "historico_fonte" not in legado

    conn = sqlite3.connect(db_path)
    try:
        eventos_legados_antes = conn.execute("SELECT COUNT(*) FROM estoque_sync_eventos").fetchone()[0]
    finally:
        conn.close()

    estoque_historico._registrar_snapshot_historico_estoque(
        "outro-cliente", "Loja A", _registros(99, 1),
        event_id="evento-isolado", recorded_at="2026-07-20T11:00:00-03:00",
    )
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM estoque_sync_eventos").fetchone()[0] == eventos_legados_antes
    finally:
        conn.close()


def test_vendas_omite_historico_de_lojas_homonimas(monkeypatch, tmp_path):
    tenant_path = _configurar_tenant(monkeypatch, tmp_path)
    caminho_config = Path(tenant_path("cliente-a")) / "lojas_config.json"
    caminho_config.write_text(
        json.dumps([
            {"store_id": "store-a", "nome": "Loja Homonima"},
            {"store_id": "store-b", "nome": "loja homonima"},
        ]),
        encoding="utf-8",
    )

    for loja in ("Loja Homonima", "__todas"):
        serie = estoque_historico._vendas_series_estoque_historico(
            "cliente-a",
            loja,
            "dia",
            datetime.fromisoformat("2026-07-20"),
            datetime.fromisoformat("2026-07-21"),
            ["2026-07-20", "2026-07-21"],
            True,
            False,
            None,
        )
        assert serie["estoque_geral"] == [None, None]
        assert serie["estoque_meta"]["success"] is False
        assert serie["estoque_meta"]["code"] == "estoque_historico_loja_ambigua"
        assert serie["estoque_meta"]["store_ids"] == ["store-a", "store-b"]


def test_vendas_omite_historico_sem_configuracao_de_identidade(monkeypatch, tmp_path):
    tenant_path = _configurar_tenant(monkeypatch, tmp_path)
    tenant_dir = Path(tenant_path("cliente-a"))
    (tenant_dir / "lojas_config.json").unlink()
    monkeypatch.setattr(
        estoque_historico,
        "get_tenant_path",
        lambda _client_id: str(tenant_dir),
    )

    serie = estoque_historico._vendas_series_estoque_historico(
        "cliente-a",
        "Loja A",
        "dia",
        datetime.fromisoformat("2026-07-20"),
        datetime.fromisoformat("2026-07-21"),
        ["2026-07-20", "2026-07-21"],
        True,
        False,
        None,
    )

    assert serie["estoque_geral"] == [None, None]
    assert serie["estoque_meta"]["success"] is False
    assert serie["estoque_meta"]["code"] == "estoque_historico_identidade_indisponivel"
