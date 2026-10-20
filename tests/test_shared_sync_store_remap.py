"""Typed source projection: no filesystem, runtime initialization or data loss."""

import builtins
import csv
import hashlib
import io
import json
import sqlite3

import pytest
from fastapi import HTTPException

from backend.services import shared_sync_store_remap as remap


def photo(store, name="001.jpg"):
    segment = "sid-" + hashlib.sha256(store.encode()).hexdigest()
    return f"cadastro_fotos/lojas/{segment}/{name}"


def csv_bytes(rows, delimiter=",", bom=False):
    out = io.StringIO(newline="")
    csv.writer(out, delimiter=delimiter, lineterminator="\r\n").writerows(rows)
    return (b"\xef\xbb\xbf" if bom else b"") + out.getvalue().encode()


def read_csv(data, delimiter=","):
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=delimiter))


def project(rel, data, mapping=None, *, scope="cadastro", names=None):
    return remap.remap_store_sources(
        scope, [(rel, data)], mapping if mapping is not None else {"remote": "local"}, names,
    )[0]


def rejected(operation, reason=None):
    with pytest.raises(HTTPException) as caught:
        operation()
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "shared_sync_store_remap_ambiguous"
    if reason:
        assert caught.value.detail["reason"] == reason
    return caught.value.detail


def sqlite_bytes(statements):
    with sqlite3.connect(":memory:") as db:
        for sql, args in statements:
            db.execute(sql, args)
        db.commit()
        return db.serialize()


def sqlite_rows(data, query):
    with sqlite3.connect(":memory:") as db:
        db.deserialize(data)
        return db.execute(query).fetchall()


@pytest.mark.parametrize("delimiter,bom", [(",", False), (";", True), ("\t", False)])
def test_csv_preserves_columns_values_tombstones_and_typed_photos(delimiter, bom):
    remote_photo = photo("remote")
    external = "https://example.invalid/" + remote_photo
    rows = [
        ["store_id", "sku", "foto", "foto_url", "descricao", "loja_id", "row_version", "deleted_at_utc"],
        ["remote", "001", remote_photo, external, "remote; vírgula, texto", "remote", "005", ""],
        ["other", "002", "", "", "001.5000", "channel", "6", "2026-10-01T00:00:00Z"],
    ]
    data = csv_bytes(rows, delimiter, bom)
    _, changed = project("cadastro_produtos_lojas.csv", data)
    expected = [list(row) for row in rows]
    expected[1][0] = "local"
    expected[1][2] = photo("local")
    assert read_csv(changed, delimiter) == expected
    assert changed.startswith(b"\xef\xbb\xbf") == bom
    assert read_csv(data, delimiter) == rows


def test_exact_case_sensitive_ids_and_noop_bytes():
    raw = b'store_id,sku,descricao\nRemote,001," remote "\n'
    assert project("cadastro_produtos_lojas.csv", raw)[1] is raw
    sources = [("unknown.bin", b"\xff\x00")]
    result = remap.remap_store_sources("cadastro", sources, {"same": "same"})
    assert result == sources and result is not sources


def test_names_are_rewritten_only_in_typed_csv_columns():
    data = csv_bytes([
        ["store_id", "sku", "loja_sync", "loja_conta", "descricao", "loja_id"],
        ["remote", "001", "Old", "Old", "Old", "Old"],
    ])
    changed = project("cadastro_custos_lojas.csv", data, names={"Old": "New"})[1]
    assert read_csv(changed)[1] == ["local", "001", "New", "New", "Old", "Old"]


@pytest.mark.parametrize("mapping", [
    {"remote": "local"},
    {"remote": "target", "local": "target"},
])
def test_csv_many_to_one_collision_is_rejected_even_for_identical_data(mapping):
    data = csv_bytes([["store_id", "sku", "foto"], ["remote", "001", ""], ["local", "001", ""]])
    rejected(lambda: project("cadastro_produtos_lojas.csv", data, mapping), "csv_row_collision")


def test_csv_row_collision_uses_normalized_sku():
    data = csv_bytes([["store_id", "sku"], ["remote", "AbC "], ["local", "abc"]])
    rejected(lambda: project("produtos_compilado.csv", data), "csv_row_collision")


def test_csv_legacy_blank_sku_uses_normalized_sku_without_changing_values():
    rows = [["store_id", "sku", "sku_normalizado"], ["remote", "", "001"], ["local", "", "002"]]
    result = project("cadastro_produtos_lojas.csv", csv_bytes(rows))[1]
    assert read_csv(result) == [rows[0], ["local", "", "001"], rows[2]]
    rows[2][2] = "001"
    rejected(lambda: project("cadastro_produtos_lojas.csv", csv_bytes(rows)), "csv_row_collision")


def test_csv_changed_identity_without_sku_is_ambiguous():
    rejected(lambda: project("cadastro_produtos_lojas.csv", b"store_id,name\nremote,anything\n"),
             "csv_identity_without_sku")


def test_photo_paths_are_translated_without_changing_image_bytes():
    image = b"\xff\xd8synthetic"
    assert project(photo("remote"), image) == (photo("local"), image)


@pytest.mark.parametrize("second", ["local", "other"])
def test_photo_path_collision_is_rejected_before_returning_partial_sources(second):
    source = [(photo("remote"), b"same"), (photo(second), b"same")]
    original = list(source)
    rejected(lambda: remap.remap_store_sources("cadastro", source, {"remote": "local", "other": "local"}),
             "photo_path_collision")
    assert source == original


def photo_config(groups):
    return json.dumps({
        "schema": "jk.cadastro.fotos.v1",
        "strict_store_scope": True,
        "shared_groups": [{"group_id": name, "store_ids": members} for name, members in groups],
    }).encode()


def test_photo_groups_remap_and_remove_only_equivalent_singleton():
    data = photo_config([("a", ["remote", "local"]), ("b", ["remote2", "third"])])
    changed = project("cadastro_fotos_config.json", data, {"remote": "local", "remote2": "local2"})[1]
    assert json.loads(changed)["shared_groups"] == [{"group_id": "b", "store_ids": ["local2", "third"]}]


def test_photo_groups_cannot_expand_propagation_by_new_overlap():
    data = photo_config([("a", ["remote", "one"]), ("b", ["local", "two"])])
    rejected(lambda: project("cadastro_fotos_config.json", data), "photo_group_overlap")


def test_photo_group_original_singleton_is_not_silently_discarded():
    rejected(lambda: project("cadastro_fotos_config.json", photo_config([("a", ["remote"])])),
             "invalid_photo_group")


def test_unaffected_photo_config_keeps_bytes():
    data = photo_config([("a", ["one", "two"])])
    assert project("cadastro_fotos_config.json", data)[1] is data


def test_favorites_planilhas_remap_label_and_key_preserving_url_and_metadata():
    url = "https://docs.google.com/spreadsheets/d/Old"
    raw = json.dumps({"planilhas": {"old": {"loja": "Old", "url": url, "updated_at": "unchanged"}},
                      "updated_at": "outside"}).encode()
    changed = project("favoritos_planilhas_lojas.json", raw, scope="favoritos_planilhas",
                      names={"Old": "Nova São"})[1]
    assert json.loads(changed) == {
        "planilhas": {"novasao": {"loja": "Nova São", "url": url, "updated_at": "unchanged"}},
        "updated_at": "outside",
    }


def test_favorites_homonymous_label_is_noop_without_name_translation():
    raw = b'{ "planilhas": {"same":{"loja":"Same","url":"https://docs.google.com/spreadsheets/d/1"}} }'
    assert project("favoritos_planilhas_lojas.json", raw, scope="favoritos_planilhas",
                   names={"Same": "Same"})[1] is raw


def test_favorites_label_collision_fails_without_dropping_a_sheet():
    raw = json.dumps({"planilhas": {
        "old": {"loja": "Old", "url": "first"},
        "new": {"loja": "New", "url": "second"},
    }}).encode()
    rejected(lambda: project("favoritos_planilhas_lojas.json", raw, scope="favoritos_planilhas",
                             names={"Old": "New"}), "planilha_collision")


def test_untyped_json_text_and_secret_values_are_preserved():
    data = b'{ "description":"remote", "token":"remote", "extra":{"anything":"remote"} }'
    assert project("cadastro_produtos_meta.json", data)[1] is data


@pytest.mark.parametrize("payload", [
    {"store_id": "remote"},
    {"products": [{"store_ids": ["remote", "other"]}]},
])
def test_unknown_metadata_schema_with_affected_identity_fails(payload):
    data = json.dumps(payload).encode()
    detail = rejected(lambda: project("cadastro_produtos_meta.json", data), "unknown_json_schema")
    assert "remote" not in json.dumps(detail)


def test_unknown_metadata_photo_reference_is_blocked_but_prose_is_preserved():
    affected = json.dumps({"foto": photo("remote")}).encode()
    rejected(lambda: project("cadastro_produtos_meta.json", affected), "unknown_json_schema")
    text = json.dumps({"description": photo("remote")}).encode()
    assert project("cadastro_produtos_meta.json", text)[1] is text


def test_oauth_and_transport_data_are_never_remapped():
    data = b'{"store_id":"remote","client_id":"remote","access_token":"remote"}'
    for rel in ("integracoes.json", "lojas_config.json", "manifest.json"):
        assert project(rel, data)[1] is data


def sales_db(store="remote", label="Same", collate=""):
    return sqlite_bytes([
        (f"CREATE TABLE vendas(id_unico TEXT PRIMARY KEY, store_id TEXT {collate}, loja_conta TEXT, loja_id TEXT, sku TEXT)", ()),
        ("INSERT INTO vendas VALUES(?,?,?,?,?)", ("Same_001", store, label, "bling-channel", "001")),
    ])


def test_sqlite_projects_store_without_changing_sale_or_bling_channel_identity():
    data = sales_db()
    changed = project("vendas_historico.db", data, scope="vendas", names={"Same": "Same"})[1]
    assert sqlite_rows(changed, "SELECT * FROM vendas") == [
        ("Same_001", "local", "Same", "bling-channel", "001"),
    ]
    assert sqlite_rows(data, "SELECT store_id FROM vendas") == [("remote",)]


def test_sqlite_legacy_schema_without_store_id_is_byte_identical():
    data = sqlite_bytes([
        ("CREATE TABLE vendas(id_unico TEXT, loja_conta TEXT, loja_id TEXT)", ()),
        ("INSERT INTO vendas VALUES(?,?,?)", ("Same_001", "Same", "remote")),
    ])
    assert project("vendas_historico.db", data, scope="vendas", names={"Same": "Same"})[1] is data


def test_sqlite_exact_store_matching_even_with_nocase_schema():
    data = sales_db("Remote", collate="COLLATE NOCASE")
    assert project("vendas_historico.db", data, scope="vendas")[1] is data


def test_sqlite_label_change_is_blocked_without_id_unico_contract():
    rejected(lambda: project("vendas_historico.db", sales_db(), scope="vendas",
                             names={"Same": "Other"}), "sqlite_name_identity_unverified")


def test_sqlite_uniqueness_collision_is_rejected():
    data = sqlite_bytes([
        ("CREATE TABLE vendas(id_unico TEXT PRIMARY KEY, store_id TEXT UNIQUE)", ()),
        ("INSERT INTO vendas VALUES(?,?)", ("one", "remote")),
        ("INSERT INTO vendas VALUES(?,?)", ("two", "local")),
    ])
    rejected(lambda: project("vendas_historico.db", data, scope="vendas"), "sqlite_remap_failed")


def test_unknown_sqlite_table_with_affected_identity_is_rejected():
    data = sqlite_bytes([
        ("CREATE TABLE private_schema(store_id TEXT)", ()),
        ("INSERT INTO private_schema VALUES(?)", ("remote",)),
    ])
    rejected(lambda: project("vendas_historico.db", data, scope="vendas"), "unknown_sqlite_table")


def test_favorites_sqlite_without_store_identity_keeps_original_bytes():
    data = sqlite_bytes([
        ("CREATE TABLE favoritos_historico(id TEXT, payload TEXT)", ()),
        ("INSERT INTO favoritos_historico VALUES(?,?)", ("one", '{"text":"remote"}')),
    ])
    assert project("favoritos_historico_user.db", data, scope="favoritos_historico")[1] is data


def test_favorites_sqlite_cannot_borrow_a_sales_table_contract():
    data = sales_db()
    rejected(lambda: project("favoritos_historico_user.sqlite", data, scope="favoritos_historico"),
             "unknown_sqlite_table")


def test_sqlite_trigger_cannot_mutate_unrelated_data():
    data = sqlite_bytes([
        ("CREATE TABLE vendas(store_id TEXT, loja_id TEXT)", ()),
        ("INSERT INTO vendas VALUES(?,?)", ("remote", "channel")),
        ("CREATE TRIGGER sabotage AFTER UPDATE ON vendas BEGIN UPDATE vendas SET loja_id='changed'; END", ()),
    ])
    rejected(lambda: project("vendas_historico.db", data, scope="vendas"), "sqlite_schema_unsupported")


def test_sqlite_changed_identity_foreign_key_is_rejected():
    data = sqlite_bytes([
        ("CREATE TABLE vendas(store_id TEXT PRIMARY KEY)", ()),
        ("CREATE TABLE child(store_ref TEXT REFERENCES vendas(store_id))", ()),
        ("INSERT INTO vendas VALUES(?)", ("remote",)),
    ])
    rejected(lambda: project("vendas_historico.db", data, scope="vendas"), "sqlite_identity_foreign_key")


def test_stock_table_with_only_label_and_payload_hash_is_unchanged():
    data = sqlite_bytes([
        ("CREATE TABLE estoque_sync_eventos(event_id TEXT, loja_sync TEXT, payload_hash TEXT)", ()),
        ("INSERT INTO estoque_sync_eventos VALUES(?,?,?)", ("event", "Same", "remote")),
    ])
    assert project("estoque_historico.db", data, scope="vendas")[1] is data
    rejected(lambda: project("estoque_historico.db", data, scope="vendas",
                             names={"Same": "Other"}), "sqlite_name_identity_unverified")


def test_remapper_opens_only_in_memory_databases_and_no_files(monkeypatch):
    data = sales_db()
    original_connect = sqlite3.connect
    opened = []

    def connect(target, *args, **kwargs):
        opened.append(target)
        assert target == ":memory:"
        return original_connect(target, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("No file I/O permitted")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", connect)
        patch.setattr(builtins, "open", forbidden)
        changed = project("vendas_historico.db", data, scope="vendas")[1]
    assert opened == [":memory:"]
    assert sqlite_rows(changed, "SELECT store_id FROM vendas") == [("local",)]


@pytest.mark.parametrize("mapping", [
    {"remote": ""},
    {"remote": " local"},
    {"remote": None},
    {"remote": "middle", "middle": "local"},
    {"remote": "local", "local": "remote"},
])
def test_invalid_or_non_idempotent_mapping_is_rejected(mapping):
    rejected(lambda: project("cadastro_produtos_meta.json", b"{}", mapping))


def test_projection_is_idempotent_for_all_typed_sources():
    original = [
        ("cadastro_produtos_lojas.csv", csv_bytes([["store_id", "sku", "foto"], ["remote", "001", photo("remote")]])),
        (photo("remote"), b"image"),
        ("cadastro_fotos_config.json", photo_config([("group", ["remote", "other"])])),
    ]
    first = remap.remap_store_sources("cadastro", original, {"remote": "local"})
    second = remap.remap_store_sources("cadastro", first, {"remote": "local"})
    assert second == first
    assert all(before is after for (_, before), (_, after) in zip(first, second))
