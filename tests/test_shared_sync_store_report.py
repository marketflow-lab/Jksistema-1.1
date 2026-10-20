"""Synthetic privacy and original-file invariants for the offline store report."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3

import pytest

SPEC = importlib.util.spec_from_file_location(
    "store_report", Path(__file__).resolve().parents[1] / "scripts" / "shared_sync_store_report.py",
)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)

A = "jk-original"
B = "jk-copy"
SECRET = "PRIVATE_TOKEN_SENTINEL_do_not_export"
ACCOUNT = "PRIVATE_ACCOUNT_SENTINEL_do_not_export"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def tenant(tmp_path):
    root = tmp_path / "tenant"
    root.mkdir()
    write_json(root / "lojas_config.json", [
        {"store_id": A, "nome": "Minha Loja", "integracoes": {"access_token": SECRET, "seller_id": ACCOUNT}},
        {"store_id": B, "nome": " MINHA LOJA ", "email": "private@example.invalid", "cpf": "123.456.789-00"},
    ])
    return root


def hashes(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file() and not path.is_symlink()}


def stores(result):
    return {entry["store_id"]: entry for entry in result["stores"]}


def codes(result):
    return {entry["code"] for entry in result["warnings"]}


def assert_private(result, root):
    serialized = json.dumps(result, ensure_ascii=False)
    for forbidden in (SECRET, ACCOUNT, "private@example.invalid", "123.456.789-00", str(root), root.as_posix()):
        assert forbidden not in serialized


def test_exact_csv_json_and_photo_references_never_assign_a_name_to_a_copy(tenant):
    (tenant / "produtos.csv").write_text(
        "store_id;loja_sync;secret\n"
        f"{A};Minha Loja;{SECRET}\n"
        f"{A} ;Minha Loja;{SECRET}\n"
        f";Minha Loja;{SECRET}\n", encoding="utf-8-sig",
    )
    write_json(tenant / "pedidos.json", {"records": [
        {"store_ref": B, "account_id": ACCOUNT, "notes": SECRET},
        {"store_id": A, "children": [{"store_ref": B}]},
        {"loja": "minha loja", "prompt": {"store_id": B, "text": SECRET}},
        {"store_id": ACCOUNT},
    ]})
    paths = [
        f"cadastro_fotos/lojas/sid-{hashlib.sha256(A.encode()).hexdigest()}/one.jpg",
        f"cadastro_fotos/lojas/{B}/two.png",
        "cadastro_fotos/lojas/Minha Loja/three.jpg",
        "cadastro_fotos/global.jpg",
    ]
    for relative in paths:
        path = tenant / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(SECRET.encode())
    before = hashes(tenant)
    result = report.build_report(tenant)
    actual = stores(result)
    assert actual[A]["exact_references"]["csv"] == {"store_id": 1, "store_ref": 0}
    assert actual[A]["exact_references"]["json"] == {"store_id": 1, "store_ref": 0}
    assert actual[B]["exact_references"]["json"] == {"store_id": 0, "store_ref": 2}
    assert actual[A]["photos"] == {"sid_sha256": 1, "legacy_id": 0}
    assert actual[B]["photos"] == {"sid_sha256": 0, "legacy_id": 1}
    assert result["unattributed_photos"] == 2
    assert result["candidate_groups_by_name"][0]["store_ids"] == [B, A]
    ambiguous = result["ambiguous_name_references"][0]
    assert ambiguous["reference_counts"] == {"csv": 2, "json": 1, "sqlite": 0}
    assert ambiguous["assigned_to_store"] is False
    assert result["complete"] is False and result["zero_proves_unused"] is False
    assert result["final_state_guaranteed"] is False
    assert hashes(tenant) == before
    assert_private(result, tenant)


def test_active_sqlite_is_opened_only_on_external_db_wal_shm_copy_and_originals_match(tenant, monkeypatch):
    database = tenant / "vendas.db"
    writer = sqlite3.connect(database)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("CREATE TABLE vendas (store_id TEXT, store_ref TEXT, loja_sync TEXT, secret TEXT, account_id TEXT)")
        writer.executemany("INSERT INTO vendas VALUES (?, ?, ?, ?, ?)", [
            (A, A, "Minha Loja", SECRET, ACCOUNT),
            (A, A, "Minha Loja", SECRET, ACCOUNT),
            (B, B, "Minha Loja", SECRET, ACCOUNT),
            (None, None, "Minha Loja", SECRET, ACCOUNT),
        ])
        writer.commit()
        assert Path(str(database) + "-wal").is_file()
        assert Path(str(database) + "-shm").is_file()
        original_connect = sqlite3.connect
        opened = []

        def connect_copy(database_uri, *args, **kwargs):
            uri = database_uri
            assert str(tenant) not in uri and tenant.as_posix() not in uri
            assert "snapshot.db?mode=ro" in uri
            from urllib.parse import unquote, urlparse
            path = Path(unquote(urlparse(uri.split("?", 1)[0]).path).lstrip("/") if os.name == "nt" else unquote(urlparse(uri.split("?", 1)[0]).path))
            assert path.is_file()
            assert Path(str(path) + "-wal").is_file()
            assert Path(str(path) + "-shm").is_file()
            opened.append(uri)
            return original_connect(uri, *args, **kwargs)

        monkeypatch.setattr(report.sqlite3, "connect", connect_copy)
        before = hashes(tenant)
        result = report.build_report(tenant)
        assert opened
        actual = stores(result)
        assert actual[A]["exact_references"]["sqlite"] == {"store_id": 2, "store_ref": 2}
        assert actual[B]["exact_references"]["sqlite"] == {"store_id": 1, "store_ref": 1}
        assert result["ambiguous_name_references"][0]["reference_counts"]["sqlite"] == 1
        assert not ({"sqlite_not_examined", "sqlite_copy_unstable", "capture_changed"} & codes(result))
        assert hashes(tenant) == before
        assert_private(result, tenant)
    finally:
        writer.close()


def test_sqlite_changes_during_copy_are_flagged_without_claiming_final_state(tenant, monkeypatch):
    database = tenant / "vendas.db"
    writer = sqlite3.connect(database)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE rows (store_id TEXT)")
        writer.execute("INSERT INTO rows VALUES (?)", (A,))
        writer.commit()
        original_read = report.StoreReport.read
        changed = False

        def read_and_write(self, path, *args, **kwargs):
            nonlocal changed
            value = original_read(self, path, *args, **kwargs)
            if path == database and not changed:
                changed = True
                writer.execute("INSERT INTO rows VALUES (?)", (B,))
                writer.commit()
            return value

        monkeypatch.setattr(report.StoreReport, "read", read_and_write)
        result = report.build_report(tenant)
        assert changed and "sqlite_copy_unstable" in codes(result)
        assert result["complete"] is False and result["final_state_guaranteed"] is False
    finally:
        writer.close()


def test_private_files_backups_and_store_journals_are_not_read_or_counted(tenant, monkeypatch):
    for name in (
        "integracoes.json", "oauth_tokens.json", "lojas_config.json.bak",
        "lojas_sync_journal.json", "backups/old.json",
        "_shared_sync_backups/capture/products.json",
        "_stores_publication/generation/preimages/stores.json",
    ):
        write_json(tenant / name, {"store_id": A, "secret": SECRET})
    original_read = report.StoreReport.read

    def ensure_private_not_opened(self, path, *args, **kwargs):
        assert path.name == "lojas_config.json"
        return original_read(self, path, *args, **kwargs)

    monkeypatch.setattr(report.StoreReport, "read", ensure_private_not_opened)
    before = hashes(tenant)
    result = report.build_report(tenant)
    assert stores(result)[A]["exact_references"] == report._empty_refs()
    assert result["coverage"]["ignored_backups"] == 5
    assert result["coverage"]["sensitive_files_excluded"] == 2
    assert result["complete"] is False and "sensitive_excluded" in codes(result)
    assert hashes(tenant) == before
    assert_private(result, tenant)


@pytest.mark.parametrize("name,data", [
    ("broken.json", b'{"store_id":'),
    ("duplicate.json", b'{"store_id":"jk-original","store_id":"jk-copy"}'),
    ("bad.csv", b'store_id,store_id\njk-original,jk-copy\n'),
    ("invalid.csv", b'store_id\n\xff\n'),
    ("invalid.db", b"not SQLite"),
    ("unsupported.xlsx", b"not examined"),
    ("different_schema.json", b'{"storeId":"jk-original"}'),
])
def test_invalid_or_unexamined_formats_are_incomplete_not_safe_zero(tenant, name, data):
    (tenant / name).write_bytes(data)
    before = hashes(tenant)
    result = report.build_report(tenant)
    assert result["complete"] is False
    assert codes(result) & {"invalid_format", "unsupported_format", "sqlite_not_examined"}
    assert result["zero_proves_unused"] is False
    assert hashes(tenant) == before


def test_hard_link_alias_is_not_read_or_attributed(tenant, tmp_path):
    outside = tmp_path / "outside.json"
    write_json(outside, {"store_id": A, "private": SECRET})
    os.link(outside, tenant / "alias.json")
    before = hashes(tenant)
    result = report.build_report(tenant)
    assert result["coverage"]["linked_entries"] == 1
    assert "linked_entry_skipped" in codes(result) and result["complete"] is False
    assert stores(result)[A]["exact_references"] == report._empty_refs()
    assert hashes(tenant) == before
    assert_private(result, tenant)


def test_symlink_directory_is_not_followed(tenant, tmp_path):
    outside = tmp_path / "external"
    outside.mkdir()
    write_json(outside / "secret.json", {"store_id": A, "private": SECRET})
    try:
        (tenant / "alias").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    result = report.build_report(tenant)
    assert "linked_entry_skipped" in codes(result)
    assert stores(result)[A]["exact_references"] == report._empty_refs()
    assert_private(result, tenant)


def test_reparse_point_attribute_is_rejected():
    class Reparse:
        st_mode = 0o40755
        st_file_attributes = 0x400
    assert report._is_alias(Reparse()) is True


def test_email_and_document_in_store_name_are_redacted(tenant):
    write_json(tenant / "lojas_config.json", [
        {"store_id": A, "nome": "private@example.invalid"},
        {"store_id": B, "nome": "123.456.789-00"},
    ])
    result = report.build_report(tenant)
    assert all(store["name"] == "" for store in result["stores"])
    assert "name_redacted" in codes(result)
    assert_private(result, tenant)


def test_sqlite_schema_and_table_names_never_leak(tenant):
    with sqlite3.connect(tenant / "records.sqlite") as connection:
        name = 'private@example.invalid_' + SECRET
        connection.execute('CREATE TABLE "' + name + '" (store_ref TEXT, free_text TEXT)')
        connection.execute('INSERT INTO "' + name + '" VALUES (?, ?)', (A, SECRET))
        connection.execute('CREATE TABLE unrelated (payload TEXT)')
        connection.execute('INSERT INTO unrelated VALUES (?)', (SECRET,))
    result = report.build_report(tenant)
    assert stores(result)[A]["exact_references"]["sqlite"]["store_ref"] == 1
    assert result["coverage"]["tables_without_supported_columns"] == 1
    assert result["complete"] is False
    assert_private(result, tenant)


def test_cli_writes_only_a_new_external_report_and_has_safe_diagnostics(tenant, tmp_path, capsys):
    before = hashes(tenant)
    output = tmp_path / "report.json"
    assert report.main(["--tenant-root", str(tenant), "--output", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert_private(result, tenant)
    saved = output.read_bytes()
    assert report.main(["--tenant-root", str(tenant), "--output", str(output)]) == 2
    assert output.read_bytes() == saved
    inside = tenant / "report.json"
    assert report.main(["--tenant-root", str(tenant), "--output", str(inside)]) == 2
    assert not inside.exists() and hashes(tenant) == before
    captured = capsys.readouterr()
    assert str(tenant) not in captured.out + captured.err


def test_zero_with_complete_supported_scan_still_never_authorizes_deletion(tenant):
    result = report.build_report(tenant)
    assert result["complete"] is True
    assert result["zero_proves_unused"] is False
    assert result["final_state_guaranteed"] is False
    assert result["candidate_groups_by_name"][0]["name_does_not_prove_account"] is True


def test_sqlite_sidecar_disappearing_during_capture_is_incomplete(tenant, monkeypatch):
    database = tenant / "partial.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE records (store_id TEXT)")
    connection.commit()
    connection.close()
    wal = Path(str(database) + "-wal")
    wal.write_bytes(b"synthetic-sidecar")
    original_read = report.StoreReport.read

    def disappear(self, path, *args, **kwargs):
        value = original_read(self, path, *args, **kwargs)
        if path == database and wal.exists():
            wal.unlink()
        return value

    monkeypatch.setattr(report.StoreReport, "read", disappear)
    result = report.build_report(tenant)
    assert "sqlite_copy_unstable" in codes(result)
    assert "sqlite_not_examined" in codes(result)
    assert result["complete"] is False


def test_cli_never_prints_unexpected_exception_content(tenant, tmp_path, monkeypatch, capsys):
    def fail(_root):
        raise RuntimeError(SECRET + str(tenant))
    monkeypatch.setattr(report, "build_report", fail)
    assert report.main(["--tenant-root", str(tenant), "--output", str(tmp_path / "report.json")]) == 2
    output = capsys.readouterr()
    assert SECRET not in output.err and str(tenant) not in output.err
    assert not (tmp_path / "report.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive path contract")
def test_cli_rejects_case_alias_of_tenant_output(tenant):
    output = Path(str(tenant).swapcase()) / "report.json"
    assert report.main(["--tenant-root", str(tenant), "--output", str(output)]) == 2
    assert not (tenant / "report.json").exists()


def _historical_registry(tenant):
    write_json(tenant / "lojas_config.json", [
        {"store_id": A, "nome": "Loja Atual A", "nomes_anteriores": ["Alias Comum", "Conta Antiga"],
         "integracoes": {"access_token": SECRET, "seller_id": ACCOUNT}},
        {"store_id": B, "nome": "Loja Atual B", "nomes_anteriores": [" Alias Comum "]},
    ])


def _name_rows(tenant, kind, rows):
    if kind == "csv":
        import csv
        with (tenant / "historical.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["store_id", "loja_sync", "loja_conta"])
            writer.writeheader()
            writer.writerows(rows)
    elif kind == "json":
        write_json(tenant / "historical.json", {"records": rows})
    else:
        with sqlite3.connect(tenant / "historical.db") as connection:
            connection.execute("CREATE TABLE records (store_id TEXT, loja_sync TEXT, loja_conta TEXT)")
            connection.executemany(
                "INSERT INTO records VALUES (?, ?, ?)",
                [(row.get("store_id"), row.get("loja_sync"), row.get("loja_conta")) for row in rows],
            )


def test_shared_historical_alias_remains_ambiguous_without_attributing_data(tenant):
    _historical_registry(tenant)
    for kind in ("csv", "json", "sqlite"):
        _name_rows(tenant, kind, [{"loja_sync": "Alias Comum"}])
    before = hashes(tenant)

    result = report.build_report(tenant)

    assert all(entry["exact_references"] == report._empty_refs() for entry in result["stores"])
    assert len(result["ambiguous_name_references"]) == 1
    ambiguous = result["ambiguous_name_references"][0]
    assert report._normal_name(ambiguous["name"]) == "alias comum"
    assert ambiguous["candidate_store_ids"] == sorted([A, B])
    assert ambiguous["reference_counts"] == {"csv": 1, "json": 1, "sqlite": 1}
    assert ambiguous["assigned_to_store"] is False
    assert "name_only_reference" in codes(result)
    assert result["complete"] is False and result["zero_proves_unused"] is False
    assert hashes(tenant) == before
    assert_private(result, tenant)


@pytest.mark.parametrize("kind", ["csv", "json", "sqlite"])
def test_pipe_labels_and_loja_conta_are_counted_but_exact_id_takes_precedence(tenant, kind):
    _historical_registry(tenant)
    _name_rows(tenant, kind, [
        {"store_id": A, "loja_sync": "Alias Comum | Loja Atual B", "loja_conta": "Conta Antiga"},
        {"loja_sync": " Alias Comum | Loja Atual B "},
        {"loja_conta": "Conta Antiga"},
    ])
    before = hashes(tenant)

    result = report.build_report(tenant)

    actual = stores(result)
    assert actual[A]["exact_references"][kind] == {"store_id": 1, "store_ref": 0}
    assert actual[B]["exact_references"] == report._empty_refs()
    ambiguous = {report._normal_name(item["name"]): item for item in result["ambiguous_name_references"]}
    assert set(ambiguous) == {"alias comum", "loja atual b", "conta antiga"}
    expected_candidates = {"alias comum": sorted([A, B]), "loja atual b": [B], "conta antiga": [A]}
    for name, entry in ambiguous.items():
        assert entry["reference_counts"] == {format_: int(format_ == kind) for format_ in ("csv", "json", "sqlite")}
        assert entry["candidate_store_ids"] == expected_candidates[name]
        assert entry["assigned_to_store"] is False
    assert hashes(tenant) == before
    assert_private(result, tenant)


def test_duplicate_historical_spellings_do_not_duplicate_candidate_store_ids(tenant):
    write_json(tenant / "lojas_config.json", [
        {"store_id": A, "nome": "Loja Atual", "nomes_anteriores": ["Loja Atual", "loja atual", "Alias Antigo", " ALIAS ANTIGO "]},
        {"store_id": B, "nome": "Outra Loja"},
    ])
    write_json(tenant / "references.json", {"loja_sync": "Alias Antigo"})
    before = hashes(tenant)

    result = report.build_report(tenant)

    assert result["candidate_groups_by_name"] == []
    assert result["ambiguous_name_references"][0]["candidate_store_ids"] == [A]
    assert result["ambiguous_name_references"][0]["reference_counts"] == {"csv": 0, "json": 1, "sqlite": 0}
    assert all(entry["exact_references"] == report._empty_refs() for entry in result["stores"])
    assert hashes(tenant) == before
    assert_private(result, tenant)


@pytest.mark.parametrize("alias", [
    "historical-private@example.invalid",
    "987.654.321-00",
    "Bearer " + SECRET,
    "C:\\private\\historical-name",
    "historical\x00invalid",
    "SENSITIVE_HISTORICAL_ALIAS_" * 10,
])
def test_sensitive_historical_aliases_are_omitted_and_make_report_incomplete(tenant, alias):
    write_json(tenant / "lojas_config.json", [
        {"store_id": A, "nome": "Loja Atual A", "nomes_anteriores": [alias]},
        {"store_id": B, "nome": "Loja Atual B", "nomes_anteriores": [alias]},
    ])
    before = hashes(tenant)

    result = report.build_report(tenant)

    assert result["complete"] is False
    assert codes(result) - {"observation_only", "name_is_not_account_identity"}
    assert result["candidate_groups_by_name"] == []
    assert result["ambiguous_name_references"] == []
    serialized = json.dumps(result, ensure_ascii=False)
    assert alias not in serialized
    assert "SENSITIVE_HISTORICAL_ALIAS_" not in serialized
    assert hashes(tenant) == before
    assert_private(result, tenant)


@pytest.mark.parametrize("aliases", [
    "INVALID_HISTORICAL_SCALAR",
    "",
    0,
    False,
    {},
    42,
    True,
    {"name": "INVALID_HISTORICAL_OBJECT", "access_token": SECRET},
    [42],
    [False],
    [None],
    [{"name": "INVALID_HISTORICAL_ITEM", "access_token": SECRET}],
    [["INVALID_HISTORICAL_NESTED"]],
])
def test_invalid_historical_alias_types_are_incomplete_without_exporting_values(tenant, aliases):
    write_json(tenant / "lojas_config.json", [
        {"store_id": A, "nome": "Loja Atual A", "nomes_anteriores": aliases},
        {"store_id": B, "nome": "Loja Atual B"},
    ])
    before = hashes(tenant)

    result = report.build_report(tenant)

    assert result["complete"] is False
    assert codes(result) - {"observation_only", "name_is_not_account_identity"}
    assert result["candidate_groups_by_name"] == []
    assert result["ambiguous_name_references"] == []
    assert "INVALID_HISTORICAL_" not in json.dumps(result, ensure_ascii=False)
    assert hashes(tenant) == before
    assert_private(result, tenant)


def test_null_historical_aliases_preserve_legacy_absence_compatibility(tenant):
    write_json(tenant / "lojas_config.json", [
        {"store_id": A, "nome": "Loja Atual A", "nomes_anteriores": None},
        {"store_id": B, "nome": "Loja Atual B"},
    ])
    before = hashes(tenant)

    result = report.build_report(tenant)

    assert result["complete"] is True
    assert result["candidate_groups_by_name"] == []
    assert result["zero_proves_unused"] is False
    assert hashes(tenant) == before
    assert_private(result, tenant)
