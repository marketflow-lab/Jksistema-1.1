import csv
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.services import cadastro_fotos, cadastro_lojas_produtos, integracoes
from backend.services import cadastro_lojas_listagem as listagem


def _write_csv(path: Path, rows):
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _configure(monkeypatch, tmp_path, stores_by_client):
    info_root = tmp_path / "info"

    def tenant_path(client_id):
        path = info_root / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.setattr(
        integracoes,
        "carregar_lojas",
        lambda client_id: stores_by_client.get(str(client_id), []),
    )
    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "_cadastro_mapa_fotos_locais",
        lambda _client_id, _store_id=None: {},
    )
    return info_root


def test_bulk_uses_one_locked_snapshot_orders_by_store_then_sku_and_suppresses_tombstones(
    monkeypatch,
    tmp_path,
):
    stores = {
        "tenant-a": [
            {"store_id": "store-b", "nome": "Loja Homonima"},
            {"store_id": "store-a", "nome": "Loja Homonima"},
        ]
    }
    info_root = _configure(monkeypatch, tmp_path, stores)
    tenant = info_root / "tenant-a"
    _write_csv(
        tenant / "cadastro_produtos_lojas.csv",
        [
            {
                "store_id": "store-a",
                "sku": "002",
                "sku_normalizado": "002",
                "loja_sync": "nome antigo",
                "nome": "Canonico A2",
                "segredo_operacional": "nao projetar",
            },
            {
                "store_id": "store-a",
                "sku": "001",
                "sku_normalizado": "001",
                "loja_sync": "nome antigo",
                "nome": "Excluido",
                "deleted_at_utc": "2026-09-09T10:00:00Z",
            },
            {
                "store_id": "store-b",
                "sku": "003",
                "sku_normalizado": "003",
                "loja_sync": "nome antigo",
                "nome": "Canonico B3",
            },
            {
                "store_id": "store-b",
                "sku": "001",
                "sku_normalizado": "001",
                "loja_sync": "nome antigo",
                "nome": "Canonico B1",
            },
        ],
    )
    _write_csv(
        tenant / "cadastro_produtos.csv",
        [
            {"store_id": "store-a", "sku": "001", "nome": "Sombra tombstonada"},
            {"store_id": "store-a", "sku": "004", "nome": "Sombra A4"},
            {"store_id": "store-b", "sku": "002", "nome": "Sombra B2"},
        ],
    )

    real_reader = cadastro_lojas_produtos._ler_csv_generico
    reads = []
    lock_state = {"active": False, "paths": []}

    @contextmanager
    def snapshot_locks(paths):
        assert not lock_state["active"]
        lock_state["active"] = True
        lock_state["paths"] = [Path(path).name for path in paths]
        try:
            yield
        finally:
            lock_state["active"] = False

    def counted_reader(path):
        assert lock_state["active"], "todas as fontes devem ser lidas dentro do snapshot"
        reads.append(Path(path).name)
        return real_reader(path)

    monkeypatch.setattr(listagem, "path_locks_for", snapshot_locks)
    monkeypatch.setattr(cadastro_lojas_produtos, "_ler_csv_generico", counted_reader)

    result = listagem.listar_produtos_lojas_snapshot_sync("tenant-a", view="summary")

    assert lock_state["paths"] == [
        "cadastro_produtos_lojas.csv",
        "cadastro_produtos.csv",
        "cadastro_custos_lojas.csv",
        "produtos_compilado.csv",
    ]
    assert reads == lock_state["paths"]
    assert [
        (item["store_id"], item["sku_normalizado"])
        for item in result["produtos"]
    ] == [
        ("store-b", "001"),
        ("store-b", "002"),
        ("store-b", "003"),
        ("store-a", "002"),
        ("store-a", "004"),
    ]
    assert not any(
        item["store_id"] == "store-a" and item["sku_normalizado"] == "001"
        for item in result["produtos"]
    )
    assert all("segredo_operacional" not in item for item in result["produtos"])
    assert result == {
        "produtos": result["produtos"],
        "lojas": [
            {
                "store_id": "store-b",
                "loja_sync": "Loja Homonima",
                "status": "ok",
                "total": 3,
            },
            {
                "store_id": "store-a",
                "loja_sync": "Loja Homonima",
                "status": "ok",
                "total": 2,
            },
        ],
        "total": 5,
        "partial": False,
    }


def test_bulk_same_store_id_remains_isolated_by_authenticated_tenant(monkeypatch, tmp_path):
    stores = {
        "tenant-a": [{"store_id": "store-same", "nome": "Loja A"}],
        "tenant-b": [{"store_id": "store-same", "nome": "Loja B"}],
    }
    info_root = _configure(monkeypatch, tmp_path, stores)
    for tenant_id, marker in (("tenant-a", "Produto A"), ("tenant-b", "Produto B")):
        _write_csv(
            info_root / tenant_id / "cadastro_produtos_lojas.csv",
            [
                {
                    "store_id": "store-same",
                    "sku": "001",
                    "sku_normalizado": "001",
                    "nome": marker,
                }
            ],
        )

    result_a = listagem.listar_produtos_lojas_snapshot_sync("tenant-a")
    result_b = listagem.listar_produtos_lojas_snapshot_sync("tenant-b")

    assert [item["nome"] for item in result_a["produtos"]] == ["Produto A"]
    assert [item["nome"] for item in result_b["produtos"]] == ["Produto B"]
    assert result_a["lojas"][0]["loja_sync"] == "Loja A"
    assert result_b["lojas"][0]["loja_sync"] == "Loja B"


def test_bulk_reports_partial_store_projection_without_leaking_exception_text(monkeypatch):
    stores = [
        {"store_id": "store-a", "nome": "Loja A"},
        {"store_id": "store-b", "nome": "Loja B"},
    ]
    monkeypatch.setattr(
        listagem,
        "_capturar_snapshot",
        lambda _client_id, _store_ids: (stores, {"store-a": [], "store-b": []}, {}),
    )

    def project(_client_id, store, _records, _index, *, include_deleted):
        assert include_deleted is False
        if store["store_id"] == "store-b":
            raise RuntimeError("caminho/sensivel token=nao-expor")
        return [{"store_id": "store-a", "sku": "001"}]

    monkeypatch.setattr(listagem, "_produtos_loja_do_snapshot", project)

    result = listagem.listar_produtos_lojas_snapshot_sync("tenant-a")

    assert result == {
        "produtos": [{"store_id": "store-a", "sku": "001"}],
        "lojas": [
            {"store_id": "store-a", "loja_sync": "Loja A", "status": "ok", "total": 1},
            {
                "store_id": "store-b",
                "loja_sync": "Loja B",
                "status": "error",
                "total": 0,
                "erro_codigo": "store_projection_failed",
            },
        ],
        "total": 1,
        "partial": True,
    }
    assert "sensivel" not in repr(result)


def test_bulk_fails_closed_with_structured_503_when_every_store_projection_fails(monkeypatch):
    stores = [
        {"store_id": "store-a", "nome": "Loja A"},
        {"store_id": "store-b", "nome": "Loja B"},
    ]
    monkeypatch.setattr(
        listagem,
        "_capturar_snapshot",
        lambda _client_id, _store_ids: (stores, {"store-a": [], "store-b": []}, {}),
    )
    monkeypatch.setattr(
        listagem,
        "_produtos_loja_do_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("internal secret")),
    )

    with pytest.raises(HTTPException) as exc_info:
        listagem.listar_produtos_lojas_snapshot_sync("tenant-a")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "cadastro_stores_unavailable"
    assert [item["status"] for item in exc_info.value.detail["lojas"]] == [
        "error",
        "error",
    ]
    assert "secret" not in repr(exc_info.value.detail)


def test_individual_snapshot_supports_summary_projection_and_explicit_tombstones(
    monkeypatch,
    tmp_path,
):
    info_root = _configure(
        monkeypatch,
        tmp_path,
        {"tenant-a": [{"store_id": "store-a", "nome": "Loja A"}]},
    )
    _write_csv(
        info_root / "tenant-a" / "cadastro_produtos_lojas.csv",
        [
            {
                "store_id": "store-a",
                "sku": "001",
                "sku_normalizado": "001",
                "nome": "Ativo",
                "campo_interno": "somente-full",
            },
            {
                "store_id": "store-a",
                "sku": "002",
                "sku_normalizado": "002",
                "nome": "Excluido",
                "deleted_at_utc": "2026-09-09T10:00:00Z",
            },
        ],
    )

    summary = listagem.listar_produtos_loja_snapshot_sync(
        "tenant-a", "store-a", view="summary"
    )
    full_with_deleted = listagem.listar_produtos_loja_snapshot_sync(
        "tenant-a", "store-a", include_deleted=True, view="full"
    )

    assert [item["sku_normalizado"] for item in summary] == ["001"]
    assert "campo_interno" not in summary[0]
    assert [item["sku_normalizado"] for item in full_with_deleted] == ["001", "002"]
    assert full_with_deleted[0]["campo_interno"] == "somente-full"
    assert full_with_deleted[1]["deleted_at_utc"] == "2026-09-09T10:00:00Z"
