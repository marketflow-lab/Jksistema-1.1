import hashlib
import io
import json
import os
import sqlite3
import zipfile

import pytest
from fastapi import HTTPException

from backend.services import shared_sync  # noqa: F401 - configura o facade legado
from backend.services import shared_sync_apply_scope
from backend.services import shared_sync_collect_files
from backend.services import shared_sync_delta
from backend.services import shared_sync_merge_sqlite


TABLES = ("vendas", "notas_entrada", "notas_entrada_itens")


def _create_sales_db(path, rows_by_table=None):
    rows_by_table = rows_by_table or {}
    conn = sqlite3.connect(path)
    try:
        for table in TABLES:
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (id_unico TEXT PRIMARY KEY, payload TEXT)')
            conn.executemany(
                f'INSERT OR REPLACE INTO "{table}" (id_unico, payload) VALUES (?, ?)',
                rows_by_table.get(table, []),
            )
        conn.commit()
    finally:
        conn.close()


def _db_bytes(path):
    return shared_sync_collect_files._shared_sync_sqlite_backup_bytes(str(path))


def _read_rows(path, table):
    conn = sqlite3.connect(path)
    try:
        return dict(conn.execute(f'SELECT id_unico, payload FROM "{table}"').fetchall())
    finally:
        conn.close()


def _bundle(scope, files, *, schema=2, overrides=None):
    entries = []
    overrides = overrides or {}
    for rel, data in files:
        item = {
            "relative_path": rel,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        item.update(overrides.get(rel, {}))
        entries.append(item)
    manifest = {"scope": scope, "files": entries}
    if schema is not None:
        manifest["schema"] = schema
    if schema == 2:
        manifest["file_count"] = len(entries)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for rel, data in files:
            zf.writestr("files/" + rel, data)
    return out.getvalue()


def test_coleta_vendas_inclui_legado_e_bancos_por_loja_sem_estado_ou_sidecars(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _create_sales_db(tenant / "vendas_historico.db", {"vendas": [("legacy", "ok")]})
    _create_sales_db(tenant / "vendas_historico_jk.db", {"vendas": [("jk", "ok")]})
    (tenant / "vendas_sync_state.json").write_text('{"local":true}', encoding="utf-8")
    (tenant / "vendas_historico_orfao.db-wal").write_bytes(b"nao transportar")
    (tenant / "vendas_historico_orfao.db-shm").write_bytes(b"nao transportar")
    (tenant / "vendas_historico_jk.db.backup_2026").write_bytes(b"nao transportar")
    (tenant / "vendas_historico_jk.db.sharedsync_x.tmp").write_bytes(b"nao transportar")
    monkeypatch.setattr(shared_sync_collect_files, "get_tenant_path", lambda _client: str(tenant), raising=False)

    entries, warnings = shared_sync_collect_files._shared_sync_coletar_arquivos("000002", "vendas")

    assert warnings == []
    assert {entry["relative_path"] for entry in entries} == {
        "vendas_historico.db",
        "vendas_historico_jk.db",
    }
    assert all(entry["data"].startswith(b"SQLite format 3\x00") for entry in entries)


def test_backup_api_inclui_commit_que_ainda_esta_no_wal(tmp_path):
    path = tmp_path / "vendas_historico_jk.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE vendas (id_unico TEXT PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT INTO vendas VALUES ('wal-1', 'confirmado')")
        conn.commit()
        assert os.path.exists(str(path) + "-wal")

        snapshot = shared_sync_collect_files._shared_sync_sqlite_backup_bytes(str(path))
    finally:
        conn.close()

    snapshot_path = tmp_path / "snapshot.db"
    snapshot_path.write_bytes(snapshot)
    assert _read_rows(snapshot_path, "vendas") == {"wal-1": "confirmado"}


def test_banco_vendas_acima_do_limite_falha_413_sem_sucesso_parcial(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _create_sales_db(tenant / "vendas_historico_jk.db", {"vendas": [("1", "x" * 2000)]})
    monkeypatch.setattr(shared_sync_collect_files, "get_tenant_path", lambda _client: str(tenant), raising=False)
    monkeypatch.setattr(shared_sync_collect_files, "_shared_sync_max_file_bytes", lambda: 128, raising=False)

    with pytest.raises(HTTPException) as raised:
        shared_sync_collect_files._shared_sync_coletar_arquivos("000002", "vendas")
    assert raised.value.status_code == 413


def test_merge_add_only_das_tres_tabelas_preserva_conflito_local_e_e_idempotente(tmp_path):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    target = tenant / "vendas_historico_jk.db"
    remote = tmp_path / "remote.db"
    local_rows = {table: [(f"{table}-conflito", "local"), (f"{table}-local", "somente-local")] for table in TABLES}
    remote_rows = {table: [(f"{table}-conflito", "remoto"), (f"{table}-novo", "novo")] for table in TABLES}
    _create_sales_db(target, local_rows)
    _create_sales_db(remote, remote_rows)

    first = shared_sync_merge_sqlite._shared_sync_apply_vendas_dbs_add_only(
        [(target.name, _db_bytes(remote))], str(tenant), str(tenant / "_shared_sync_backups" / "teste"),
    )
    second = shared_sync_merge_sqlite._shared_sync_apply_vendas_dbs_add_only(
        [(target.name, _db_bytes(remote))], str(tenant), str(tenant / "_shared_sync_backups" / "teste-2"),
    )

    assert first["added"] == 3
    assert second["added"] == 0
    assert second["file_count"] == 0
    for table in TABLES:
        rows = _read_rows(target, table)
        assert rows[f"{table}-conflito"] == "local"
        assert rows[f"{table}-local"] == "somente-local"
        assert rows[f"{table}-novo"] == "novo"


def test_delta_vendas_transporta_as_tres_tabelas(tmp_path):
    source = tmp_path / "source.db"
    _create_sales_db(source, {table: [(f"{table}-1", table)] for table in TABLES})

    delta, keys = shared_sync_delta._shared_sync_vendas_delta_db_bytes(
        "vendas_historico_jk.db", _db_bytes(source), set(),
    )

    assert delta is not None
    assert len(keys) == 3
    delta_path = tmp_path / "delta.db"
    delta_path.write_bytes(delta)
    for table in TABLES:
        assert _read_rows(delta_path, table) == {f"{table}-1": table}


def test_aplicacao_valida_hash_e_corrupcao_antes_de_alterar_destino(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    target = tenant / "vendas_historico_jk.db"
    _create_sales_db(target, {"vendas": [("local", "intacto")]})
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _client: str(tenant), raising=False)

    good_remote = tmp_path / "good.db"
    _create_sales_db(good_remote, {"vendas": [("remote", "novo")]})
    good_data = _db_bytes(good_remote)
    bad_hash_bundle = _bundle(
        "vendas",
        [(target.name, good_data)],
        overrides={target.name: {"sha256": "0" * 64}},
    )
    with pytest.raises(HTTPException) as bad_hash:
        shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "vendas", bad_hash_bundle)
    assert bad_hash.value.status_code == 502
    assert _read_rows(target, "vendas") == {"local": "intacto"}

    bad_size_bundle = _bundle(
        "vendas",
        [(target.name, good_data)],
        overrides={target.name: {"size": len(good_data) + 1}},
    )
    with pytest.raises(HTTPException) as bad_size:
        shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "vendas", bad_size_bundle)
    assert bad_size.value.status_code == 502
    assert _read_rows(target, "vendas") == {"local": "intacto"}

    corrupt_data = b"SQLite quebrado"
    corrupt_bundle = _bundle("vendas", [(target.name, corrupt_data)])
    with pytest.raises(HTTPException) as corrupt:
        shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "vendas", corrupt_bundle)
    assert corrupt.value.status_code == 502
    assert _read_rows(target, "vendas") == {"local": "intacto"}

    monkeypatch.setattr(shared_sync_apply_scope, "_shared_sync_max_file_bytes", lambda: 128, raising=False)
    with pytest.raises(HTTPException) as oversized:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002", "vendas", _bundle("vendas", [(target.name, good_data)]),
        )
    assert oversized.value.status_code == 413
    assert _read_rows(target, "vendas") == {"local": "intacto"}


def test_pacote_legado_sem_hash_continua_sendo_lido_e_estado_e_ignorado(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    target = tenant / "vendas_historico_jk.db"
    remote = tmp_path / "remote.db"
    _create_sales_db(target, {"vendas": [("local", "local")]})
    _create_sales_db(remote, {"vendas": [("remote", "remote")]})
    files = [(target.name, _db_bytes(remote)), ("vendas_sync_state.json", b'{"dia":"local-na-origem"}')]
    bundle = _bundle("vendas", files, schema=1)
    # Simula o formato anterior, no qual tamanho e hash podiam nao existir.
    source = io.BytesIO(bundle)
    rebuilt = io.BytesIO()
    with zipfile.ZipFile(source, "r") as old, zipfile.ZipFile(rebuilt, "w") as new:
        manifest = json.loads(old.read("manifest.json"))
        for item in manifest["files"]:
            item.pop("size", None)
            item.pop("sha256", None)
        new.writestr("manifest.json", json.dumps(manifest))
        for rel, _data in files:
            new.writestr("files/" + rel, old.read("files/" + rel))
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _client: str(tenant), raising=False)

    result = shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "vendas", rebuilt.getvalue())

    assert result["file_count"] == 1
    assert _read_rows(target, "vendas") == {"local": "local", "remote": "remote"}
    assert not (tenant / "vendas_sync_state.json").exists()


def test_falha_na_promocao_do_segundo_banco_reverte_o_primeiro(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    targets = [tenant / "vendas_historico_a.db", tenant / "vendas_historico_b.db"]
    remotes = [tmp_path / "remote_a.db", tmp_path / "remote_b.db"]
    for index, target in enumerate(targets):
        _create_sales_db(target, {"vendas": [(f"local-{index}", "local")]})
        _create_sales_db(remotes[index], {"vendas": [(f"remote-{index}", "remote")]})

    real_replace = shared_sync_merge_sqlite.os.replace

    def fail_second_stage(source, destination):
        if str(destination).endswith("vendas_historico_b.db") and str(source).endswith(".tmp"):
            raise OSError("falha simulada na segunda promocao")
        return real_replace(source, destination)

    monkeypatch.setattr(shared_sync_merge_sqlite.os, "replace", fail_second_stage)
    with pytest.raises(OSError, match="falha simulada"):
        shared_sync_merge_sqlite._shared_sync_apply_vendas_dbs_add_only(
            [(targets[index].name, _db_bytes(remotes[index])) for index in range(2)],
            str(tenant),
            str(tenant / "_shared_sync_backups" / "rollback"),
        )

    assert _read_rows(targets[0], "vendas") == {"local-0": "local"}
    assert _read_rows(targets[1], "vendas") == {"local-1": "local"}


def test_banco_ocupado_e_reportado_como_423(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    target = tenant / "vendas_historico_jk.db"
    remote = tmp_path / "remote.db"
    _create_sales_db(target)
    _create_sales_db(remote, {"vendas": [("remote", "remote")]})
    monkeypatch.setattr(shared_sync_merge_sqlite, "SHARED_SYNC_SQLITE_LOCK_RETRIES", 1, raising=False)
    monkeypatch.setattr(
        shared_sync_merge_sqlite,
        "_shared_sync_stage_vendas_db_add_only",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
    )

    with pytest.raises(HTTPException) as raised:
        shared_sync_merge_sqlite._shared_sync_apply_vendas_dbs_add_only(
            [(target.name, _db_bytes(remote))], str(tenant), str(tenant / "backup"),
        )
    assert raised.value.status_code == 423
    assert _read_rows(target, "vendas") == {}


def test_destino_wal_com_escrita_ativa_falha_423_sem_promover(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    target = tenant / "vendas_historico_jk.db"
    remote = tmp_path / "remote.db"
    _create_sales_db(target, {"vendas": [("local", "confirmado")]})
    _create_sales_db(remote, {"vendas": [("remote", "novo")]})
    monkeypatch.setattr(shared_sync_merge_sqlite, "SHARED_SYNC_SQLITE_LOCK_RETRIES", 1, raising=False)

    writer = sqlite3.connect(target)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO vendas VALUES ('em-voo', 'nao-confirmado')")

        with pytest.raises(HTTPException) as raised:
            shared_sync_merge_sqlite._shared_sync_apply_vendas_dbs_add_only(
                [(target.name, _db_bytes(remote))],
                str(tenant),
                str(tenant / "backup-wal"),
            )
        assert raised.value.status_code == 423
    finally:
        writer.rollback()
        writer.close()

    assert _read_rows(target, "vendas") == {"local": "confirmado"}
