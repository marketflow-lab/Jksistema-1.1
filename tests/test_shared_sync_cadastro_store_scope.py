import hashlib
import io
import json
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException

from backend.services import shared_sync  # noqa: F401 - configura o facade e injeta dependencias
from backend.services import cadastro_custos
from backend.services import cadastro_fotos
from backend.services import shared_sync_apply_scope
from backend.services import shared_sync_collect_files
from backend.services import shared_sync_delta
from backend.services import shared_sync_merge_sqlite


REL = "cadastro_produtos_lojas.csv"
COST_REL = "cadastro_custos_lojas.csv"


_DELETE_STORE_SCRIPT = r"""
import json
import logging
import os
import sys

from fastapi import HTTPException
from backend.services import integracoes

info_root, client_id, store_name, store_id = sys.argv[1:5]

def tenant_path(current_client_id):
    return os.path.join(info_root, str(current_client_id))

integracoes.configure_integracoes_context(
    logger_ref=logging.getLogger("shared-sync-delete-test"),
    pasta_info=info_root,
    get_tenant_path=tenant_path,
)
print("START", flush=True)
try:
    integracoes.excluir_loja(
        client_id,
        store_name,
        store_id=store_id,
    )
except HTTPException as exc:
    print(
        json.dumps({"status": exc.status_code, "detail": exc.detail}),
        flush=True,
    )
else:
    print(json.dumps({"status": 200, "detail": {}}), flush=True)
"""


def _spawn_store_delete(tenant: Path, store_id: str = "store-a"):
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _DELETE_STORE_SCRIPT,
            str(tenant.parent),
            tenant.name,
            f"Loja {store_id}",
            store_id,
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish_store_delete(process: subprocess.Popen, timeout: float = 15.0) -> dict:
    stdout, stderr = process.communicate(timeout=timeout)
    assert process.returncode == 0, stderr
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, stderr
    return json.loads(lines[-1])


@pytest.fixture(autouse=True)
def _configure_cadastro_fotos_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tmp_path), raising=False)

    def resolver_tenant(client_id):
        resolver = getattr(shared_sync_apply_scope, "get_tenant_path", None)
        if callable(resolver):
            return resolver(client_id)
        from backend.services import cadastro_lojas_produtos

        return cadastro_lojas_produtos.get_tenant_path(client_id)

    monkeypatch.setattr(
        cadastro_fotos,
        "get_tenant_path",
        resolver_tenant,
        raising=False,
    )

    def carregar_lojas(client_id):
        config_path = Path(resolver_tenant(client_id)) / "lojas_config.json"
        if not config_path.exists():
            return []
        return json.loads(config_path.read_text(encoding="utf-8-sig"))

    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        carregar_lojas,
        raising=False,
    )


def _csv_bytes(rows, columns=None):
    product_columns = columns or [
        "store_id",
        "sku",
        "nome",
        "row_version",
        "updated_at_utc",
        "deleted_at_utc",
    ]
    return pd.DataFrame(rows, columns=product_columns).to_csv(index=False).encode("utf-8-sig")


def _read_rows(path):
    return pd.read_csv(path, dtype=str).fillna("").to_dict(orient="records")


def _write_store_config(tenant, *store_ids):
    (tenant / "lojas_config.json").write_text(
        json.dumps(
            [
                {"store_id": store_id, "nome": f"Loja {store_id}", "integracoes": {}}
                for store_id in store_ids
            ]
        ),
        encoding="utf-8",
    )


def _write_default_photo_config(tenant):
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [],
            }
        ),
        encoding="utf-8",
    )


def _photo_config_bytes(*store_ids, group_id="grupo-compartilhado", strict=True):
    return json.dumps(
        {
            "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
            "strict_store_scope": strict,
            "shared_groups": (
                [{"group_id": group_id, "store_ids": list(store_ids)}]
                if store_ids
                else []
            ),
        }
    ).encode("utf-8")


def _enable_shared_photo_group(tenant, monkeypatch, *store_ids):
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [
                    {"group_id": "grupo-compartilhado", "store_ids": list(store_ids)}
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tenant.parent), raising=False)
    monkeypatch.setattr(
        cadastro_fotos,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": store_id} for store_id in store_ids],
    )


def _cost_csv_bytes(rows, columns=None):
    cost_columns = columns or [
        "store_id",
        "loja_sync",
        "sku",
        "produto",
        "custo",
        "preco",
        "imposto",
        "updated_at",
    ]
    return pd.DataFrame(rows, columns=cost_columns).to_csv(index=False).encode("utf-8-sig")


def _row(store_id, sku, nome, version, updated_at, deleted_at=""):
    return {
        "store_id": store_id,
        "sku": sku,
        "nome": nome,
        "row_version": str(version),
        "updated_at_utc": updated_at,
        "deleted_at_utc": deleted_at,
    }


def _bundle(scope, files):
    entries = []
    for rel, data in files:
        entries.append({
            "relative_path": rel,
            "size": len(data),
            "mtime": 1,
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    manifest = {
        "schema": 2,
        "scope": scope,
        "file_count": len(entries),
        "files": entries,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for rel, data in files:
            archive.writestr(f"files/{rel}", data)
    return output.getvalue()


def test_cadastro_scope_coleta_produtos_e_custos_canonicos(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    (tenant / REL).write_bytes(_csv_bytes([
        _row("store-a", "001", "Produto A", 1, "2026-08-28T10:00:00Z"),
    ]))
    (tenant / COST_REL).write_text(
        "loja_sync,sku,custo\nLoja A,001,10.00\n", encoding="utf-8",
    )
    monkeypatch.setattr(shared_sync_collect_files, "get_tenant_path", lambda _client_id: str(tenant), raising=False)

    entries, warnings = shared_sync_collect_files._shared_sync_coletar_arquivos(
        "000002", "cadastro", username="operador", user_only=True,
    )

    names = {item["relative_path"] for item in entries}
    assert warnings == []
    assert REL in names
    assert COST_REL in names


def test_aplicar_pacote_mantem_mesma_sku_em_duas_lojas(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a", "store-b")
    _write_default_photo_config(tenant)
    outro_tenant = tmp_path / "000008"
    outro_tenant.mkdir()
    marker = outro_tenant / REL
    marker.write_text("nao alterar", encoding="utf-8")
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda client_id: str(tenant if client_id == "000002" else outro_tenant),
        raising=False,
    )
    data = _csv_bytes([
        _row("store-a", "SKU-1", "Produto Loja A", 1, "2026-08-28T10:00:00Z"),
        _row("store-b", "SKU-1", "Produto Loja B", 1, "2026-08-28T10:00:00Z"),
    ])

    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002", "cadastro", _bundle("cadastro", [(REL, data)]), "operador",
    )

    rows = _read_rows(tenant / REL)
    assert result["files"] == [REL]
    assert {(row["store_id"], row["sku"]) for row in rows} == {
        ("store-a", "SKU-1"),
        ("store-b", "SKU-1"),
    }
    assert marker.read_text(encoding="utf-8") == "nao alterar"


def test_foto_por_loja_remota_antiga_nao_sobrescreve_linha_e_foto_locais(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segmento}/001.jpg"
    photo_path = tenant / "cadastro_fotos" / "lojas" / segmento / "001.jpg"
    photo_path.parent.mkdir(parents=True)
    photo_path.write_bytes(b"foto-local-v2")
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    local = {
        **_row("store-a", "001", "Produto local v2", 2, "2026-08-28T12:00:00Z"),
        "foto": photo_rel,
    }
    remote = {
        **_row("store-a", "001", "Produto remoto v1", 1, "2026-08-28T11:00:00Z"),
        "foto": photo_rel,
    }
    (tenant / REL).write_bytes(_csv_bytes([local], columns))
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _client_id: str(tenant), raising=False)

    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        # Deliberately put the image first: manifest order must not bypass the
        # version decision made from cadastro_produtos_lojas.csv.
        _bundle("cadastro", [
            (photo_rel, b"foto-remota-v1"),
            (REL, _csv_bytes([remote], columns)),
        ]),
        "operador",
    )

    row = _read_rows(tenant / REL)[0]
    assert row["nome"] == "Produto local v2"
    assert row["row_version"] == "2"
    assert photo_path.read_bytes() == b"foto-local-v2"
    assert photo_rel not in result["files"]


def test_foto_por_loja_remota_nova_substitui_linha_e_foto_no_mesmo_lock(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segmento}/001.jpg"
    photo_path = tenant / "cadastro_fotos" / "lojas" / segmento / "001.jpg"
    photo_path.parent.mkdir(parents=True)
    photo_path.write_bytes(b"foto-local-v1")
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    local = {
        **_row("store-a", "001", "Produto local v1", 1, "2026-08-28T11:00:00Z"),
        "foto": photo_rel,
    }
    remote = {
        **_row("store-a", "001", "Produto remoto v2", 2, "2026-08-28T12:00:00Z"),
        "foto": photo_rel,
    }
    (tenant / REL).write_bytes(_csv_bytes([local], columns))
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _client_id: str(tenant), raising=False)

    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle("cadastro", [
            (photo_rel, b"foto-remota-v2"),
            (REL, _csv_bytes([remote], columns)),
        ]),
        "operador",
    )

    row = _read_rows(tenant / REL)[0]
    assert row["nome"] == "Produto remoto v2"
    assert row["row_version"] == "2"
    assert photo_path.read_bytes() == b"foto-remota-v2"
    assert set(result["files"]) == {REL, photo_rel}


def test_add_only_acopla_linha_remota_v2_e_bytes_remotos_v2(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segmento}/001.jpg"
    photo_path = tenant / photo_rel
    photo_path.parent.mkdir(parents=True)
    photo_path.write_bytes(b"bytes-local-v1-A")
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    local = {
        **_row("store-a", "001", "Produto local v1", 1, "2026-08-28T11:00:00Z"),
        "foto": photo_rel,
    }
    remote = {
        **_row("store-a", "001", "Produto remoto v2", 2, "2026-08-28T12:00:00Z"),
        "foto": photo_rel,
    }
    (tenant / REL).write_bytes(_csv_bytes([local], columns))
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )

    result = shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
        "000002",
        "cadastro",
        "operador",
        [
            (photo_rel, b"bytes-remotos-v2-B"),
            (REL, _csv_bytes([remote], columns)),
        ],
        str(tenant),
        str(tenant / "backup"),
    )

    row = _read_rows(tenant / REL)[0]
    assert row["nome"] == "Produto remoto v2"
    assert row["row_version"] == "2"
    assert photo_path.read_bytes() == b"bytes-remotos-v2-B"
    assert set(result["files"]) == {REL, photo_rel}


def test_add_only_foto_remota_perdedora_nao_toca_linha_nem_bytes_locais(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segmento}/001.jpg"
    photo_path = tenant / photo_rel
    photo_path.parent.mkdir(parents=True)
    photo_path.write_bytes(b"bytes-locais-v2-A")
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    local = {
        **_row("store-a", "001", "Produto local v2", 2, "2026-08-28T12:00:00Z"),
        "foto": photo_rel,
    }
    remote = {
        **_row("store-a", "001", "Produto remoto v1", 1, "2026-08-28T11:00:00Z"),
        "foto": photo_rel,
    }
    target = tenant / REL
    target.write_bytes(_csv_bytes([local], columns))
    target_before = target.read_bytes()
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )

    result = shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
        "000002",
        "cadastro",
        "operador",
        [
            (photo_rel, b"bytes-remotos-v1-B"),
            (REL, _csv_bytes([remote], columns)),
        ],
        str(tenant),
        str(tenant / "backup"),
    )

    assert target.read_bytes() == target_before
    assert photo_path.read_bytes() == b"bytes-locais-v2-A"
    assert result["files"] == []


def test_add_only_foto_store_orfa_sem_csv_falha_antes_de_escrever(
    tmp_path,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segmento}/ORFA.jpg"

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
            "000002",
            "cadastro",
            "operador",
            [(photo_rel, b"bytes-orfa")],
            str(tenant),
            str(tenant / "backup"),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "shared_sync_store_photo_row_required"
    assert not (tenant / photo_rel).exists()


def test_add_only_foto_store_orfa_com_csv_nao_e_publicada(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segmento}/001.jpg"
    orphan_rel = f"cadastro_fotos/lojas/{segmento}/ORFA.jpg"
    photo_path = tenant / photo_rel
    photo_path.parent.mkdir(parents=True)
    photo_path.write_bytes(b"bytes-validos-A")
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    local = {
        **_row("store-a", "001", "Produto local v1", 1, "2026-08-28T11:00:00Z"),
        "foto": photo_rel,
    }
    remote = {
        **_row("store-a", "001", "Produto remoto v2", 2, "2026-08-28T12:00:00Z"),
        "foto": photo_rel,
    }
    (tenant / REL).write_bytes(_csv_bytes([local], columns))
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )

    result = shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
        "000002",
        "cadastro",
        "operador",
        [
            (orphan_rel, b"bytes-orfa-B"),
            (REL, _csv_bytes([remote], columns)),
        ],
        str(tenant),
        str(tenant / "backup"),
    )

    row = _read_rows(tenant / REL)[0]
    assert row["row_version"] == "2"
    assert row["foto"] == photo_rel
    assert photo_path.read_bytes() == b"bytes-validos-A"
    assert not (tenant / orphan_rel).exists()
    assert orphan_rel not in result["files"]


def test_shared_sync_grupo_bloqueia_a_v1_contra_b_v5_com_mesmo_nome_e_bytes_diferentes(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b")
    _write_store_config(tenant, *store_ids)
    _enable_shared_photo_group(tenant, monkeypatch, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    refs = {
        store_id: (
            "cadastro_fotos/lojas/"
            f"{cadastro_fotos._cadastro_store_id_foto_segmento(store_id)}/001.jpg"
        )
        for store_id in store_ids
    }
    local_b = {
        **_row("store-b", "001", "Local B v5", 5, "2026-08-28T15:00:00Z"),
        "foto": refs["store-b"],
    }
    target = tenant / REL
    target.write_bytes(_csv_bytes([local_b], columns))
    target_before = target.read_bytes()
    b_path = tenant / refs["store-b"]
    b_path.parent.mkdir(parents=True, exist_ok=True)
    b_path.write_bytes(b"bytes-b-v5")
    remote_a = {
        **_row("store-a", "001", "Remoto A v1", 1, "2026-08-28T11:00:00Z"),
        "foto": refs["store-a"],
    }

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle(
                "cadastro",
                [
                    (refs["store-a"], b"bytes-a-v1"),
                    (REL, _csv_bytes([remote_a], columns)),
                ],
            ),
            "operador",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "shared_group_photo_precedence_ambiguous"
    assert target.read_bytes() == target_before
    assert b_path.read_bytes() == b"bytes-b-v5"
    assert not (tenant / refs["store-a"]).exists()


def test_shared_sync_grupo_bloqueia_delta_metadado_orfao_a_v1_contra_b_v5(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b")
    _write_store_config(tenant, *store_ids)
    _enable_shared_photo_group(tenant, monkeypatch, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    refs = {
        store_id: (
            "cadastro_fotos/lojas/"
            f"{cadastro_fotos._cadastro_store_id_foto_segmento(store_id)}/001.jpg"
        )
        for store_id in store_ids
    }
    local_b = {
        **_row("store-b", "001", "Local B v5", 5, "2026-08-28T15:00:00Z"),
        "foto": refs["store-b"],
    }
    target = tenant / REL
    target.write_bytes(_csv_bytes([local_b], columns))
    target_before = target.read_bytes()
    a_path = tenant / refs["store-a"]
    b_path = tenant / refs["store-b"]
    a_path.parent.mkdir(parents=True, exist_ok=True)
    b_path.parent.mkdir(parents=True, exist_ok=True)
    a_path.write_bytes(b"bytes-a-v1-orfaos")
    b_path.write_bytes(b"bytes-b-v5")
    remote_a = {
        **_row("store-a", "001", "Remoto A v1", 1, "2026-08-28T11:00:00Z"),
        "foto": refs["store-a"],
    }

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle("cadastro", [(REL, _csv_bytes([remote_a], columns))]),
            "operador",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "shared_group_photo_precedence_ambiguous"
    assert target.read_bytes() == target_before
    assert a_path.read_bytes() == b"bytes-a-v1-orfaos"
    assert b_path.read_bytes() == b"bytes-b-v5"


def test_shared_sync_grupo_aceita_delta_metadado_quando_fotos_ja_sao_coerentes(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b")
    _write_store_config(tenant, *store_ids)
    _enable_shared_photo_group(tenant, monkeypatch, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    refs = {
        store_id: (
            "cadastro_fotos/lojas/"
            f"{cadastro_fotos._cadastro_store_id_foto_segmento(store_id)}/001.jpg"
        )
        for store_id in store_ids
    }
    local_rows = [
        {
            **_row(store_id, "001", f"Local {store_id}", 1, "2026-08-28T10:00:00Z"),
            "foto": refs[store_id],
        }
        for store_id in store_ids
    ]
    target = tenant / REL
    target.write_bytes(_csv_bytes(local_rows, columns))
    for ref in refs.values():
        photo = tenant / ref
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(b"bytes-coerentes")
    remote_a = {
        **_row("store-a", "001", "Metadado A v2", 2, "2026-08-28T11:00:00Z"),
        "foto": refs["store-a"],
    }

    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle("cadastro", [(REL, _csv_bytes([remote_a], columns))]),
        "operador",
    )

    rows = {row["store_id"]: row for row in _read_rows(target)}
    assert rows["store-a"]["row_version"] == "2"
    assert rows["store-b"]["row_version"] == "1"
    assert all((tenant / ref).read_bytes() == b"bytes-coerentes" for ref in refs.values())
    assert result["files"] == [REL]


def test_shared_sync_grupo_usa_mesma_uniao_de_colunas_do_merge_antes_da_foto(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b")
    _write_store_config(tenant, *store_ids)
    _enable_shared_photo_group(tenant, monkeypatch, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    local_columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    remote_columns = [*local_columns, "campo_novo_vazio"]
    refs = {
        store_id: (
            "cadastro_fotos/lojas/"
            f"{cadastro_fotos._cadastro_store_id_foto_segmento(store_id)}/001.jpg"
        )
        for store_id in store_ids
    }
    local_rows = [
        {
            **_row(store_id, "001", f"Produto {store_id}", 1, "2026-08-28T10:00:00Z"),
            "foto": refs[store_id],
        }
        for store_id in store_ids
    ]
    target = tenant / REL
    target.write_bytes(_csv_bytes(local_rows, local_columns))
    before = target.read_bytes()
    for ref in refs.values():
        photo = tenant / ref
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(b"foto-local-antiga")
    remote_a = {**local_rows[0], "campo_novo_vazio": ""}

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle(
                "cadastro",
                [
                    (refs["store-a"], b"foto-remota-sem-versao-nova"),
                    (REL, _csv_bytes([remote_a], remote_columns)),
                ],
            ),
            "operador",
        )

    assert exc_info.value.detail["code"] == "shared_group_photo_precedence_ambiguous"
    assert target.read_bytes() == before
    assert all((tenant / ref).read_bytes() == b"foto-local-antiga" for ref in refs.values())


def test_shared_sync_propaga_foto_vencedora_para_todo_grupo_compartilhado(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b", "store-c")
    _write_store_config(tenant, *store_ids)
    _enable_shared_photo_group(tenant, monkeypatch, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )

    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    photos = {
        store_id: (
            f"cadastro_fotos/lojas/"
            f"{cadastro_fotos._cadastro_store_id_foto_segmento(store_id)}/001.jpg"
        )
        for store_id in store_ids
    }
    local_rows = [
        {
            **_row(store_id, "001", f"Produto {store_id} v1", 1, "2026-08-28T11:00:00Z"),
            "foto": photos[store_id],
        }
        for store_id in store_ids
    ]
    (tenant / REL).write_bytes(_csv_bytes(local_rows, columns))
    for photo_rel in photos.values():
        photo_path = tenant / photo_rel
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        photo_path.write_bytes(b"foto-local-v1")

    remote_rows = [
        {
            **_row(store_id, "001", f"Produto {store_id} v2", 2, "2026-08-28T12:00:00Z"),
            "foto": photos[store_id],
        }
        for store_id in store_ids
    ]
    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle(
            "cadastro",
            [
                *((photo_rel, b"foto-compartilhada-v2") for photo_rel in photos.values()),
                (REL, _csv_bytes(remote_rows, columns)),
            ],
        ),
        "operador",
    )

    assert all((tenant / photo_rel).read_bytes() == b"foto-compartilhada-v2" for photo_rel in photos.values())
    rows = {row["store_id"]: row for row in _read_rows(tenant / REL)}
    assert set(rows) == set(store_ids)
    assert rows["store-a"]["row_version"] == "2"
    assert rows["store-b"]["row_version"] == "2"
    assert rows["store-c"]["row_version"] == "2"
    assert set(result["files"]) == {REL, *photos.values()}


def test_shared_sync_troca_extensao_no_grupo_e_remove_variantes_com_backup(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b", "store-c")
    _write_store_config(tenant, *store_ids)
    _enable_shared_photo_group(tenant, monkeypatch, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )

    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    jpgs = {}
    pngs = {}
    for store_id in store_ids:
        segmento = cadastro_fotos._cadastro_store_id_foto_segmento(store_id)
        jpgs[store_id] = f"cadastro_fotos/lojas/{segmento}/001.jpg"
        pngs[store_id] = f"cadastro_fotos/lojas/{segmento}/001.png"
    local_rows = [
        {
            **_row(store_id, "001", f"Produto {store_id} v1", 1, "2026-08-28T11:00:00Z"),
            "foto": jpgs[store_id],
        }
        for store_id in store_ids
    ]
    (tenant / REL).write_bytes(_csv_bytes(local_rows, columns))
    for photo_rel in jpgs.values():
        photo_path = tenant / photo_rel
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        photo_path.write_bytes(b"jpg-antigo")

    remote_rows = [
        {
            **_row(store_id, "001", f"Produto {store_id} v2", 2, "2026-08-28T12:00:00Z"),
            "foto": pngs[store_id],
        }
        for store_id in store_ids
    ]
    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle(
            "cadastro",
            [
                *((photo_rel, b"png-novo") for photo_rel in pngs.values()),
                (REL, _csv_bytes(remote_rows, columns)),
            ],
        ),
        "operador",
    )

    assert all((tenant / photo_rel).read_bytes() == b"png-novo" for photo_rel in pngs.values())
    assert all(not (tenant / photo_rel).exists() for photo_rel in jpgs.values())
    rows = {row["store_id"]: row for row in _read_rows(tenant / REL)}
    assert all(rows[store_id]["foto"] == pngs[store_id] for store_id in store_ids)
    backups = list((tenant / "_shared_sync_backups").glob("cadastro_*"))
    assert len(backups) == 1
    assert all((backups[0] / photo_rel).read_bytes() == b"jpg-antigo" for photo_rel in jpgs.values())
    assert set(result["files"]) == {REL, *pngs.values()}


def test_shared_sync_rejeita_extensoes_conflitantes_no_mesmo_grupo_antes_do_merge(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b", "store-c")
    _write_store_config(tenant, *store_ids)
    _enable_shared_photo_group(tenant, monkeypatch, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )

    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    segmento_a = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    segmento_b = cadastro_fotos._cadastro_store_id_foto_segmento("store-b")
    photo_a = f"cadastro_fotos/lojas/{segmento_a}/001.jpg"
    photo_b = f"cadastro_fotos/lojas/{segmento_b}/001.png"
    local_rows = [
        {
            **_row(store_id, "001", f"Local {store_id}", 1, "2026-08-28T11:00:00Z"),
            "foto": (
                f"cadastro_fotos/lojas/"
                f"{cadastro_fotos._cadastro_store_id_foto_segmento(store_id)}/001.jpg"
            ),
        }
        for store_id in store_ids
    ]
    target = tenant / REL
    target.write_bytes(_csv_bytes(local_rows, columns))
    target_antes = target.read_bytes()
    fotos_antes = {}
    for row in local_rows:
        caminho = tenant / row["foto"]
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(f"foto-{row['store_id']}".encode())
        fotos_antes[row["foto"]] = caminho.read_bytes()

    remote_rows = [
        {
            **_row("store-a", "001", "Remoto A", 2, "2026-08-28T12:00:00Z"),
            "foto": photo_a,
        },
        {
            **_row("store-b", "001", "Remoto B", 2, "2026-08-28T12:00:00Z"),
            "foto": photo_b,
        },
    ]
    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle(
                "cadastro",
                [
                    (photo_a, b"jpg-remoto"),
                    (photo_b, b"png-remoto"),
                    (REL, _csv_bytes(remote_rows, columns)),
                ],
            ),
            "operador",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "shared_group_photo_conflict"
    assert exc_info.value.detail["skus"] == ["001"]
    assert target.read_bytes() == target_antes
    assert all((tenant / rel).read_bytes() == data for rel, data in fotos_antes.items())
    assert not (tenant / "_shared_sync_backups").exists()


def test_shared_sync_rejeita_path_raw_em_grupo_compartilhado_sem_mutar(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b", "store-c")
    _write_store_config(tenant, *store_ids)
    _enable_shared_photo_group(tenant, monkeypatch, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    target = tenant / REL
    local = {
        **_row("store-a", "001", "Local", 1, "2026-08-28T11:00:00Z"),
        "foto": "",
    }
    target.write_bytes(_csv_bytes([local], columns))
    target_antes = target.read_bytes()
    raw_rel = "cadastro_fotos/lojas/store-a/001.jpg"
    remote = {
        **_row("store-a", "001", "Remoto", 2, "2026-08-28T12:00:00Z"),
        "foto": raw_rel,
    }

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle(
                "cadastro",
                [(raw_rel, b"foto-raw"), (REL, _csv_bytes([remote], columns))],
            ),
            "operador",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "shared_group_photo_path_noncanonical"
    assert target.read_bytes() == target_antes
    assert not (tenant / raw_rel).exists()
    assert not (tenant / "_shared_sync_backups").exists()


def test_shared_sync_reverte_csv_e_grupo_se_segunda_gravacao_de_foto_falhar(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b", "store-c")
    _write_store_config(tenant, *store_ids)
    _enable_shared_photo_group(tenant, monkeypatch, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    photos = {
        store_id: (
            f"cadastro_fotos/lojas/"
            f"{cadastro_fotos._cadastro_store_id_foto_segmento(store_id)}/001.jpg"
        )
        for store_id in store_ids
    }
    local_rows = [
        {
            **_row(store_id, "001", f"Local {store_id}", 1, "2026-08-28T11:00:00Z"),
            "foto": photos[store_id],
        }
        for store_id in store_ids
    ]
    target = tenant / REL
    target.write_bytes(_csv_bytes(local_rows, columns))
    target_antes = target.read_bytes()
    for store_id, photo_rel in photos.items():
        photo_path = tenant / photo_rel
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        photo_path.write_bytes(f"antiga-{store_id}".encode())
    fotos_antes = {rel: (tenant / rel).read_bytes() for rel in photos.values()}

    remote_rows = [
        {
            **_row(store_id, "001", f"Remoto {store_id} v2", 2, "2026-08-28T12:00:00Z"),
            "foto": photos[store_id],
        }
        for store_id in store_ids
    ]
    escrever_real = shared_sync_apply_scope._shared_sync_atomic_write
    chamadas = 0

    def falhar_na_segunda_foto(target_abs, data):
        nonlocal chamadas
        chamadas += 1
        if chamadas == 2:
            raise OSError("falha injetada na segunda foto")
        return escrever_real(target_abs, data)

    monkeypatch.setattr(
        shared_sync_apply_scope,
        "_shared_sync_atomic_write",
        falhar_na_segunda_foto,
    )
    with pytest.raises(OSError, match="segunda foto"):
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle(
                "cadastro",
                [
                    *((rel, b"foto-nova") for rel in photos.values()),
                    (REL, _csv_bytes(remote_rows, columns)),
                ],
            ),
            "operador",
        )

    assert target.read_bytes() == target_antes
    assert all((tenant / rel).read_bytes() == data for rel, data in fotos_antes.items())


def test_shared_sync_reverte_troca_de_extensao_se_limpeza_do_grupo_falhar(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b", "store-c")
    _write_store_config(tenant, *store_ids)
    _enable_shared_photo_group(tenant, monkeypatch, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    jpgs = {}
    pngs = {}
    for store_id in store_ids:
        segmento = cadastro_fotos._cadastro_store_id_foto_segmento(store_id)
        jpgs[store_id] = f"cadastro_fotos/lojas/{segmento}/001.jpg"
        pngs[store_id] = f"cadastro_fotos/lojas/{segmento}/001.png"
    local_rows = [
        {
            **_row(store_id, "001", f"Local {store_id}", 1, "2026-08-28T11:00:00Z"),
            "foto": jpgs[store_id],
        }
        for store_id in store_ids
    ]
    target = tenant / REL
    target.write_bytes(_csv_bytes(local_rows, columns))
    target_antes = target.read_bytes()
    for store_id, photo_rel in jpgs.items():
        photo_path = tenant / photo_rel
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        photo_path.write_bytes(f"jpg-antigo-{store_id}".encode())
    jpgs_antes = {rel: (tenant / rel).read_bytes() for rel in jpgs.values()}

    remote_rows = [
        {
            **_row(store_id, "001", f"Remoto PNG {store_id}", 2, "2026-08-28T12:00:00Z"),
            "foto": pngs[store_id],
        }
        for store_id in store_ids
    ]
    remover_real = (
        shared_sync_apply_scope._shared_sync_remover_variantes_obsoletas_foto_store
    )
    chamadas = 0

    def falhar_na_segunda_limpeza(*args, **kwargs):
        nonlocal chamadas
        chamadas += 1
        if chamadas == 2:
            raise OSError("falha injetada na limpeza")
        return remover_real(*args, **kwargs)

    monkeypatch.setattr(
        shared_sync_apply_scope,
        "_shared_sync_remover_variantes_obsoletas_foto_store",
        falhar_na_segunda_limpeza,
    )
    with pytest.raises(OSError, match="limpeza"):
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle(
                "cadastro",
                [
                    *((rel, b"png-novo") for rel in pngs.values()),
                    (REL, _csv_bytes(remote_rows, columns)),
                ],
            ),
            "operador",
        )

    assert target.read_bytes() == target_antes
    assert all((tenant / rel).read_bytes() == data for rel, data in jpgs_antes.items())
    assert all(not (tenant / rel).exists() for rel in pngs.values())


def test_foto_por_loja_em_url_api_absoluta_normaliza_para_chave_canonica(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segmento}/001.jpg"
    absolute_ref = (
        "https://jk.local/api/cadastro/foto/000002/"
        f"cadastro_fotos/lojas/{segmento}/001.jpg"
    )
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    remote = {
        **_row("store-a", "001", "Produto remoto", 1, "2026-08-28T12:00:00Z"),
        "foto": absolute_ref,
    }
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _client_id: str(tenant), raising=False)
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "store-a"}],
    )

    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle("cadastro", [
            (photo_rel, b"foto-remota"),
            (REL, _csv_bytes([remote], columns)),
        ]),
        "operador",
    )

    assert (tenant / photo_rel).read_bytes() == b"foto-remota"
    assert set(result["files"]) == {REL, photo_rel}


@pytest.mark.parametrize(
    "photo_column",
    [
        "foto",
        "cg_foto",
        "imagem",
        "imagem_url",
        "image_url",
        "url_imagem",
        "link_imagem",
        "picture",
        "thumbnail_url",
        "link_foto",
    ],
)
def test_foto_por_loja_em_url_api_absoluta_rejeita_store_cruzada(
    tmp_path,
    monkeypatch,
    photo_column,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a", "store-b")
    _write_default_photo_config(tenant)
    segmento_b = cadastro_fotos._cadastro_store_id_foto_segmento("store-b")
    photo_rel = f"cadastro_fotos/lojas/{segmento_b}/001.jpg"
    absolute_ref = (
        "https://jk.local/api/cadastro/foto/000002/"
        f"cadastro_fotos/lojas/{segmento_b}/001.jpg"
    )
    columns = [
        "store_id", "sku", "nome", photo_column, "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    remote = {
        **_row("store-a", "001", "Produto A", 1, "2026-08-28T12:00:00Z"),
        photo_column: absolute_ref,
    }
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _client_id: str(tenant), raising=False)
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "store-a"}, {"store_id": "store-b"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle("cadastro", [
                (photo_rel, b"foto-store-b"),
                (REL, _csv_bytes([remote], columns)),
            ]),
            "operador",
        )

    assert exc_info.value.status_code == 502
    assert not (tenant / REL).exists()
    assert not (tenant / photo_rel).exists()


def test_shared_sync_recusa_foto_hash_de_outra_store(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a", "store-b")
    _write_default_photo_config(tenant)
    segmento_b = cadastro_fotos._cadastro_store_id_foto_segmento("store-b")
    photo_rel = f"cadastro_fotos/lojas/{segmento_b}/001.jpg"
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    remote = {
        **_row("store-a", "001", "Produto A", 1, "2026-08-28T12:00:00Z"),
        "foto": photo_rel,
    }
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _client_id: str(tenant), raising=False)

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle("cadastro", [
                (photo_rel, b"foto-store-b"),
                (REL, _csv_bytes([remote], columns)),
            ]),
            "operador",
        )

    assert exc_info.value.status_code == 502
    assert not (tenant / REL).exists()
    assert not (tenant / photo_rel).exists()


def test_shared_sync_fotos_store_ids_casefold_equivalentes_nao_colidem(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "StoreA", "storea")
    _write_default_photo_config(tenant)
    segmento_upper = cadastro_fotos._cadastro_store_id_foto_segmento("StoreA")
    segmento_lower = cadastro_fotos._cadastro_store_id_foto_segmento("storea")
    photo_upper = f"cadastro_fotos/lojas/{segmento_upper}/001.jpg"
    photo_lower = f"cadastro_fotos/lojas/{segmento_lower}/001.jpg"
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    rows = [
        {
            **_row("StoreA", "001", "Produto upper", 1, "2026-08-28T12:00:00Z"),
            "foto": photo_upper,
        },
        {
            **_row("storea", "001", "Produto lower", 1, "2026-08-28T12:00:00Z"),
            "foto": photo_lower,
        },
    ]
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _client_id: str(tenant), raising=False)

    shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle("cadastro", [
            (photo_upper, b"upper"),
            (photo_lower, b"lower"),
            (REL, _csv_bytes(rows, columns)),
        ]),
        "operador",
    )

    assert segmento_upper != segmento_lower
    assert (tenant / photo_upper).read_bytes() == b"upper"
    assert (tenant / photo_lower).read_bytes() == b"lower"
    assert {row["store_id"] for row in _read_rows(tenant / REL)} == {"StoreA", "storea"}


def test_shared_sync_recusa_path_raw_legado_quando_store_id_e_ambiguo(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "StoreA", "storea")
    _write_default_photo_config(tenant)
    photo_rel = "cadastro_fotos/lojas/StoreA/001.jpg"
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    remote = {
        **_row("StoreA", "001", "Produto A", 1, "2026-08-28T12:00:00Z"),
        "foto": photo_rel,
    }
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _client_id: str(tenant), raising=False)
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "StoreA"}, {"store_id": "storea"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle("cadastro", [
                (photo_rel, b"foto-ambigua"),
                (REL, _csv_bytes([remote], columns)),
            ]),
            "operador",
        )

    assert exc_info.value.status_code == 502
    assert not (tenant / REL).exists()
    assert not (tenant / photo_rel).exists()


def test_shared_sync_aceita_path_raw_legado_seguro_quando_store_e_unica(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "StoreA")
    _write_default_photo_config(tenant)
    photo_rel = "cadastro_fotos/lojas/StoreA/001.jpg"
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    remote = {
        **_row("StoreA", "001", "Produto legado", 1, "2026-08-28T12:00:00Z"),
        "foto": photo_rel,
    }
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _client_id: str(tenant), raising=False)
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "StoreA"}],
    )

    shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle("cadastro", [
            (photo_rel, b"foto-legada-segura"),
            (REL, _csv_bytes([remote], columns)),
        ]),
        "operador",
    )

    assert (tenant / photo_rel).read_bytes() == b"foto-legada-segura"


@pytest.mark.parametrize("ext", ["jpg", "gif", "bmp"])
def test_foto_por_loja_no_delta_inclui_digest_do_conteudo(ext):
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segmento}/001.{ext}"
    old_key = (
        f"cadastro:file:{photo_rel}:revision:"
        f"{hashlib.sha256(b'foto-v1').hexdigest()}"
    )

    data, keys = shared_sync_delta._shared_sync_delta_for_entry(
        "cadastro",
        {"relative_path": photo_rel, "data": b"foto-v2"},
        {old_key},
    )

    assert data == b"foto-v2"
    assert keys == [
        f"cadastro:file:{photo_rel}:revision:"
        f"{hashlib.sha256(b'foto-v2').hexdigest()}"
    ]


def test_foto_legada_fora_de_lojas_preserva_overwrite_generico(tmp_path, monkeypatch):
    from backend.services import cadastro_lojas_produtos

    tenant = tmp_path / "000002"
    tenant.mkdir()
    photo_rel = "cadastro_fotos/001.jpg"
    photo_path = tenant / "cadastro_fotos" / "001.jpg"
    photo_antiga = tenant / "cadastro_fotos" / "001.png"
    photo_path.parent.mkdir(parents=True)
    photo_antiga.write_bytes(b"foto-legada-local")
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _client_id: str(tenant), raising=False)
    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", lambda _client_id: str(tenant), raising=False)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tmp_path), raising=False)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", lambda _client_id: str(tenant), raising=False)

    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle("cadastro", [(photo_rel, b"foto-legada-remota")]),
        "operador",
    )

    assert photo_path.read_bytes() == b"foto-legada-remota"
    assert not photo_antiga.exists()
    mapa = cadastro_fotos._cadastro_mapa_fotos_locais("000002")
    assert cadastro_fotos._cadastro_resolver_foto_local(mapa, "001") == photo_rel
    assert result["files"] == [photo_rel]


def test_custos_mantem_mesma_sku_em_duas_lojas_por_store_id(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a", "store-b")
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _client_id: str(tenant), raising=False)
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", lambda _client_id: str(tenant), raising=False)
    data = _cost_csv_bytes([
        {
            "store_id": "store-a",
            "loja_sync": "Mesmo nome",
            "sku": "001",
            "custo": "10.00",
            "updated_at": "28/08/2026 10:00",
        },
        {
            "store_id": "store-b",
            "loja_sync": "Mesmo nome",
            "sku": "001",
            "custo": "11.00",
            "updated_at": "28/08/2026 10:00",
        },
    ])

    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002", "cadastro", _bundle("cadastro", [(COST_REL, data)]), "operador",
    )

    rows = _read_rows(tenant / COST_REL)
    assert result["files"] == [COST_REL]
    assert {(row["store_id"], row["sku"], row["custo"]) for row in rows} == {
        ("store-a", "001", "10.00"),
        ("store-b", "001", "11.00"),
    }


@pytest.mark.parametrize("writer_kind", ["custos", "produtos_compilado"])
def test_shared_sync_writer_store_id_serializa_exclusao_cross_process(
    tmp_path,
    monkeypatch,
    writer_kind,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )

    ready = threading.Event()
    release = threading.Event()
    failures = []
    writer_result = []
    if writer_kind == "custos":
        target_rel = COST_REL
        payload = _cost_csv_bytes(
            [
                {
                    "store_id": "store-a",
                    "loja_sync": "Loja store-a",
                    "sku": "001",
                    "custo": "10.00",
                    "updated_at": "01/09/2026 10:00",
                }
            ]
        )
        write_real = shared_sync_merge_sqlite._shared_sync_write_csv_atomic

        def write_gated(target_abs, dataframe):
            if Path(target_abs).name == COST_REL:
                ready.set()
                if not release.wait(15):
                    raise AssertionError("timeout aguardando liberar writer de custos")
            return write_real(target_abs, dataframe)

        monkeypatch.setattr(
            shared_sync_merge_sqlite,
            "_shared_sync_write_csv_atomic",
            write_gated,
        )
    else:
        target_rel = "produtos_compilado.csv"
        payload = b"store_id,sku,nome\nstore-a,001,Produto\n"
        write_real = shared_sync_apply_scope._shared_sync_atomic_write

        def write_gated(target_abs, data):
            if Path(target_abs).name == target_rel:
                ready.set()
                if not release.wait(15):
                    raise AssertionError("timeout aguardando liberar writer compilado")
            return write_real(target_abs, data)

        monkeypatch.setattr(
            shared_sync_apply_scope,
            "_shared_sync_atomic_write",
            write_gated,
        )

    def run_writer():
        try:
            writer_result.append(
                shared_sync_apply_scope._shared_sync_aplicar_pacote(
                    "000002",
                    "cadastro",
                    _bundle("cadastro", [(target_rel, payload)]),
                    "operador",
                )
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.append(exc)

    writer = threading.Thread(target=run_writer, daemon=True)
    delete_process = None
    writer.start()
    try:
        assert ready.wait(10), "writer nao chegou ao commit protegido"
        delete_process = _spawn_store_delete(tenant)
        assert delete_process.stdout.readline().strip() == "START"
        with pytest.raises(subprocess.TimeoutExpired):
            delete_process.wait(timeout=0.5)
    finally:
        release.set()

    writer.join(15)
    assert not writer.is_alive()
    if failures:
        raise failures[0]
    assert writer_result[0]["files"] == [target_rel]
    assert delete_process is not None
    delete_result = _finish_store_delete(delete_process)
    assert delete_result["status"] == 409
    assert delete_result["detail"]["code"] == "store_has_legacy_catalog_records"
    assert json.loads((tenant / "lojas_config.json").read_text(encoding="utf-8-sig"))[0][
        "store_id"
    ] == "store-a"
    assert _read_rows(tenant / target_rel)[0]["store_id"] == "store-a"


@pytest.mark.parametrize("writer_kind", ["custos", "produtos_compilado"])
def test_shared_sync_writer_store_id_revalida_exclusao_ja_commitada(
    tmp_path,
    monkeypatch,
    writer_kind,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    delete_process = _spawn_store_delete(tenant)
    assert delete_process.stdout.readline().strip() == "START"
    delete_result = _finish_store_delete(delete_process)
    assert delete_result["status"] == 200

    if writer_kind == "custos":
        target_rel = COST_REL
        payload = _cost_csv_bytes(
            [
                {
                    "store_id": "store-a",
                    "loja_sync": "Loja store-a",
                    "sku": "001",
                    "custo": "10.00",
                    "updated_at": "01/09/2026 10:00",
                }
            ]
        )
    else:
        target_rel = "produtos_compilado.csv"
        payload = b"store_id,sku,nome\nstore-a,001,Produto\n"

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle("cadastro", [(target_rel, payload)]),
            "operador",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "shared_sync_store_identity_invalid"
    assert exc_info.value.detail["store_ids_ausentes"] == ["store-a"]
    assert exc_info.value.detail["store_ids_tombstonados"] == ["store-a"]
    assert not (tenant / target_rel).exists()
    assert not (tenant / "_shared_sync_backups").exists()


def test_custos_delta_e_merge_propagam_atualizacao_sem_perder_linhas_ou_colunas(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a", "store-b")
    target = tenant / COST_REL
    columns = [
        "store_id", "loja_sync", "sku", "produto", "custo", "preco",
        "imposto", "updated_at", "observacao",
    ]
    current = {
        "store_id": "store-a",
        "loja_sync": "Loja A",
        "sku": "1",
        "custo": "10.00",
        "updated_at": "28/08/2026 10:00",
        "observacao": "local antiga",
    }
    unrelated = {
        "store_id": "store-b",
        "loja_sync": "Loja B",
        "sku": "001",
        "custo": "20.00",
        "updated_at": "28/08/2026 10:00",
        "observacao": "preservar",
    }
    updated = {
        "store_id": "store-a",
        "loja_sync": "Loja A renomeada",
        "sku": "001",
        "custo": "12.50",
        "updated_at": "28/08/2026 11:00",
        "observacao": "remota nova",
    }
    target.write_bytes(_cost_csv_bytes([current, unrelated], columns))
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", lambda _client_id: str(tenant), raising=False)

    current_key = shared_sync_delta._shared_sync_cadastro_custos_delta_key(
        "cadastro", COST_REL, current, columns,
    )
    content_only = {**current, "custo": "10.50", "observacao": "mesmo timestamp, outro conteudo"}
    content_only_key = shared_sync_delta._shared_sync_cadastro_custos_delta_key(
        "cadastro", COST_REL, content_only, columns,
    )
    updated_key = shared_sync_delta._shared_sync_cadastro_custos_delta_key(
        "cadastro", COST_REL, updated, columns,
    )
    delta, keys = shared_sync_delta._shared_sync_csv_delta_bytes(
        "cadastro", COST_REL, _cost_csv_bytes([updated], columns), {current_key},
    )
    merge = shared_sync_merge_sqlite._shared_sync_merge_cadastro_custos_versioned(
        "000002", str(target), delta,
    )

    rows = _read_rows(target)
    by_store = {row["store_id"].lower(): row for row in rows}
    assert current_key != updated_key
    assert current_key != content_only_key
    assert keys == [updated_key]
    assert merge["updated"] == 1
    assert len(rows) == 2
    assert by_store["store-a"]["custo"] == "12.50"
    assert by_store["store-a"]["observacao"] == "remota nova"
    assert by_store["store-b"]["custo"] == "20.00"
    assert by_store["store-b"]["observacao"] == "preservar"


def test_edicao_comum_preserva_coluna_extra_recebida_do_shared_sync(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    target = tenant / COST_REL
    columns = [
        "store_id", "loja_sync", "sku", "produto", "custo", "preco",
        "imposto", "updated_at", "observacao",
    ]
    remote = _cost_csv_bytes(
        [
            {
                "store_id": "store-a",
                "loja_sync": "Loja A",
                "sku": "001",
                "custo": "10.00",
                "updated_at": "28/08/2026 10:00",
                "observacao": "preservar na edicao",
            },
            {
                "store_id": "store-b",
                "loja_sync": "Loja B",
                "sku": "002",
                "custo": "20.00",
                "updated_at": "28/08/2026 10:00",
                "observacao": "linha alheia",
            },
        ],
        columns,
    )
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", lambda _client_id: str(tenant), raising=False)
    shared_sync_merge_sqlite._shared_sync_merge_cadastro_custos_versioned(
        "000002", str(target), remote,
    )

    cadastro_custos._cadastro_salvar_custos_item_loja(
        "000002", "store-a", "Loja A", "001", {"custo": "12.50"},
    )

    rows = _read_rows(target)
    by_store = {row["store_id"]: row for row in rows}
    assert by_store["store-a"]["custo"] == "12.50"
    assert by_store["store-a"]["observacao"] == "preservar na edicao"
    assert by_store["store-b"]["observacao"] == "linha alheia"


def test_custos_com_mesmo_updated_at_convergem_por_digest_completo(tmp_path, monkeypatch):
    tenant_a = tmp_path / "000002"
    tenant_b = tmp_path / "000003"
    tenant_a.mkdir()
    tenant_b.mkdir()
    target_a = tenant_a / COST_REL
    target_b = tenant_b / COST_REL
    first = {
        "store_id": "store-a",
        "loja_sync": "Loja A",
        "sku": "001",
        "custo": "10.00",
        "updated_at": "28/08/2026 10:00",
    }
    second = {**first, "custo": "11.00"}
    target_a.write_bytes(_cost_csv_bytes([first]))
    target_b.write_bytes(_cost_csv_bytes([second]))
    monkeypatch.setattr(
        cadastro_custos,
        "get_tenant_path",
        lambda client_id: str(tenant_a if client_id == "000002" else tenant_b),
        raising=False,
    )

    result_a = shared_sync_merge_sqlite._shared_sync_merge_cadastro_custos_versioned(
        "000002", str(target_a), _cost_csv_bytes([second]),
    )
    result_b = shared_sync_merge_sqlite._shared_sync_merge_cadastro_custos_versioned(
        "000003", str(target_b), _cost_csv_bytes([first]),
    )

    assert result_a["conflicts"] == 1
    assert result_b["conflicts"] == 1
    assert _read_rows(target_a) == _read_rows(target_b)


def test_custos_legados_usam_loja_normalizada_e_sku_sem_colapsar_outras_linhas(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    target = tenant / COST_REL
    columns = ["loja_sync", "sku", "custo", "updated_at", "campo_legado"]
    target.write_bytes(_cost_csv_bytes([
        {
            "loja_sync": "Loja Á",
            "sku": "1",
            "custo": "10.00",
            "updated_at": "28/08/2026 10:00",
            "campo_legado": "substituir",
        },
        {
            "loja_sync": "Loja B",
            "sku": "001",
            "custo": "20.00",
            "updated_at": "28/08/2026 10:00",
            "campo_legado": "preservar",
        },
    ], columns))
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", lambda _client_id: str(tenant), raising=False)
    remote = _cost_csv_bytes([{
        "loja_sync": "  loja a  ",
        "sku": "001",
        "custo": "13.00",
        "updated_at": "28/08/2026 12:00",
        "campo_legado": "atualizado",
    }], columns)

    result = shared_sync_merge_sqlite._shared_sync_merge_cadastro_custos_versioned(
        "000002", str(target), remote,
    )

    rows = _read_rows(target)
    assert result["updated"] == 1
    assert len(rows) == 2
    assert sorted((row["custo"], row["campo_legado"]) for row in rows) == [
        ("13.00", "atualizado"),
        ("20.00", "preservar"),
    ]


@pytest.mark.parametrize(
    "row",
    [
        {"store_id": "", "loja_sync": "", "sku": "001", "custo": "10.00"},
        {"store_id": "store-a", "loja_sync": "Loja A", "sku": "", "custo": "10.00"},
    ],
)
def test_custos_rejeitam_linha_sem_identidade_ou_sku(row):
    with pytest.raises(HTTPException) as exc_info:
        shared_sync_delta._shared_sync_csv_delta_bytes(
            "cadastro", COST_REL, _cost_csv_bytes([row]), set(),
        )

    assert exc_info.value.status_code == 502


def test_merge_atualiza_mesma_chave_normalizada_por_versao_e_updated_at(tmp_path):
    target = tmp_path / REL
    target.write_bytes(_csv_bytes([
        _row("store-a", "1", "Inicial", 1, "2026-08-28T10:00:00Z"),
    ]))

    by_time = shared_sync_merge_sqlite._shared_sync_merge_cadastro_lojas_versioned(
        str(target),
        _csv_bytes([_row(" store-a ", "001", "Horario novo", 1, "2026-08-28T11:00:00Z")]),
    )
    by_version = shared_sync_merge_sqlite._shared_sync_merge_cadastro_lojas_versioned(
        str(target),
        _csv_bytes([_row("store-a", "001", "Versao nova", 2, "2026-08-28T09:00:00Z")]),
    )

    rows = _read_rows(target)
    assert by_time["updated"] == 1
    assert by_version["updated"] == 1
    assert len(rows) == 1
    assert rows[0]["nome"] == "Versao nova"
    assert rows[0]["row_version"] == "2"


def test_store_id_opaco_preserva_lojas_que_diferem_por_caixa(tmp_path):
    target = tmp_path / REL
    target.write_bytes(_csv_bytes([
        _row("STORE-A", "001", "Maiuscula", 1, "2026-08-28T10:00:00Z"),
    ]))

    result = shared_sync_merge_sqlite._shared_sync_merge_cadastro_lojas_versioned(
        str(target),
        _csv_bytes([_row("store-a", "001", "Minuscula", 1, "2026-08-28T10:00:00Z")]),
    )

    rows = _read_rows(target)
    assert result["added"] == 1
    assert {(row["store_id"], row["nome"]) for row in rows} == {
        ("STORE-A", "Maiuscula"),
        ("store-a", "Minuscula"),
    }


def test_tombstone_mais_novo_nao_e_ressuscitado_por_payload_antigo(tmp_path):
    target = tmp_path / REL
    deleted_at = "2026-08-28T12:00:00Z"
    target.write_bytes(_csv_bytes([
        _row("store-a", "001", "Ativo", 2, "2026-08-28T11:00:00Z"),
    ]))

    deletion = shared_sync_merge_sqlite._shared_sync_merge_cadastro_lojas_versioned(
        str(target),
        _csv_bytes([_row("store-a", "001", "Excluido", 3, deleted_at, deleted_at)]),
    )
    old_payload = shared_sync_merge_sqlite._shared_sync_merge_cadastro_lojas_versioned(
        str(target),
        _csv_bytes([_row(" store-a ", "001", "Payload antigo", 2, "2026-08-28T11:00:00Z")]),
    )

    rows = _read_rows(target)
    assert deletion["updated"] == 1
    assert deletion["deleted"] == 1
    assert old_payload["updated"] == 0
    assert old_payload["skipped_older"] == 1
    assert old_payload["deleted"] == 1
    assert rows[0]["nome"] == "Excluido"
    assert rows[0]["deleted_at_utc"] == deleted_at


def test_mesma_versao_tombstone_vence_ativo_em_ambas_as_direcoes(tmp_path):
    active = _row("store-a", "001", "Ativo", 5, "2026-08-28T13:00:00Z")
    tombstone = _row(
        "store-a",
        "001",
        "Excluido",
        5,
        "2026-08-28T09:00:00Z",
        "2026-08-28T09:00:00Z",
    )

    active_target = tmp_path / "active.csv"
    active_target.write_bytes(_csv_bytes([active]))
    deletion = shared_sync_merge_sqlite._shared_sync_merge_cadastro_lojas_versioned(
        str(active_target), _csv_bytes([tombstone]),
    )

    tombstone_target = tmp_path / "tombstone.csv"
    tombstone_target.write_bytes(_csv_bytes([tombstone]))
    stale_active = shared_sync_merge_sqlite._shared_sync_merge_cadastro_lojas_versioned(
        str(tombstone_target), _csv_bytes([active]),
    )

    assert deletion["updated"] == 1
    assert _read_rows(active_target)[0]["deleted_at_utc"] == "2026-08-28T09:00:00Z"
    assert stale_active["updated"] == 0
    assert stale_active["skipped_older"] == 1
    assert _read_rows(tombstone_target)[0]["deleted_at_utc"] == "2026-08-28T09:00:00Z"

    resurrection = shared_sync_merge_sqlite._shared_sync_merge_cadastro_lojas_versioned(
        str(tombstone_target),
        _csv_bytes([_row("store-a", "001", "Ressuscitado", 6, "2026-08-28T08:00:00Z")]),
    )
    resurrected = _read_rows(tombstone_target)[0]
    assert resurrection["updated"] == 1
    assert resurrected["nome"] == "Ressuscitado"
    assert resurrected["deleted_at_utc"] == ""


def test_delta_do_novo_arquivo_reenvia_nova_revisao_da_mesma_chave():
    old = _row("store-a", "001", "Antigo", 1, "2026-08-28T10:00:00Z")
    new = _row(" store-a ", "001", "Novo", 2, "2026-08-28T11:00:00Z")
    columns = list(old)
    old_key = shared_sync_delta._shared_sync_cadastro_lojas_delta_key(
        "cadastro", REL, old, columns,
    )
    new_key = shared_sync_delta._shared_sync_cadastro_lojas_delta_key(
        "cadastro", REL, new, columns,
    )

    data, keys = shared_sync_delta._shared_sync_csv_delta_bytes(
        "cadastro", REL, _csv_bytes([new]), {old_key},
    )
    repeated_data, repeated_keys = shared_sync_delta._shared_sync_csv_delta_bytes(
        "cadastro", REL, _csv_bytes([new]), {old_key, new_key},
    )

    assert old_key != new_key
    assert data is not None
    assert keys == [new_key]
    assert repeated_data is None
    assert repeated_keys == []


def test_delta_distingue_conteudo_com_mesma_versao_e_timestamp():
    first = _row("store-a", "001", "Conteudo A", 4, "2026-08-28T10:00:00.123456Z")
    second = _row("store-a", "001", "Conteudo B", 4, "2026-08-28T10:00:00.123456Z")
    columns = list(first)

    first_key = shared_sync_delta._shared_sync_cadastro_lojas_delta_key(
        "cadastro", REL, first, columns,
    )
    second_key = shared_sync_delta._shared_sync_cadastro_lojas_delta_key(
        "cadastro", REL, second, columns,
    )
    data, keys = shared_sync_delta._shared_sync_csv_delta_bytes(
        "cadastro", REL, _csv_bytes([second]), {first_key},
    )

    assert first_key != second_key
    assert data is not None
    assert keys == [second_key]


def test_empate_ativo_converge_por_digest_e_reporta_conflito(tmp_path):
    timestamp = "2026-08-28T10:00:00.123456Z"
    first = _row("store-a", "001", "Conteudo A", 4, timestamp)
    second = _row("store-a", "001", "Conteudo B", 4, timestamp)
    first_target = tmp_path / "first.csv"
    second_target = tmp_path / "second.csv"
    first_target.write_bytes(_csv_bytes([first]))
    second_target.write_bytes(_csv_bytes([second]))

    first_result = shared_sync_merge_sqlite._shared_sync_merge_cadastro_lojas_versioned(
        str(first_target), _csv_bytes([second]),
    )
    second_result = shared_sync_merge_sqlite._shared_sync_merge_cadastro_lojas_versioned(
        str(second_target), _csv_bytes([first]),
    )

    first_row = _read_rows(first_target)[0]
    second_row = _read_rows(second_target)[0]
    assert first_result["conflicts"] == 1
    assert second_result["conflicts"] == 1
    assert first_row == second_row


def test_merge_e_crud_compartilham_lock_sem_lost_update(tmp_path, monkeypatch):
    from backend.services import cadastro_lojas_produtos

    tenant = tmp_path / "000002"
    tenant.mkdir()
    target = tenant / REL
    target.write_bytes(_csv_bytes([
        _row("store-a", "BASE", "Base", 1, "2026-08-28T10:00:00.000001Z"),
    ]))
    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tmp_path), raising=False)
    monkeypatch.setattr(
        cadastro_fotos,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", lambda _client_id: str(tenant), raising=False)
    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "resolver_loja_cadastro",
        lambda _client_id, store_id: {"store_id": store_id, "nome": "Loja A"},
    )
    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "_contexto_legado",
        lambda *_args, **_kwargs: {
            "sombras": {},
            "compilado": {},
            "custos": {},
            "fotos": {},
        },
    )

    writer_entered = threading.Event()
    allow_writer = threading.Event()
    crud_done = threading.Event()
    failures = []
    original_writer = shared_sync_merge_sqlite._shared_sync_write_csv_atomic

    def paused_writer(target_abs, df):
        writer_entered.set()
        if not allow_writer.wait(5):
            raise AssertionError("timeout aguardando liberacao do writer")
        original_writer(target_abs, df)

    monkeypatch.setattr(shared_sync_merge_sqlite, "_shared_sync_write_csv_atomic", paused_writer)

    def run_merge():
        try:
            shared_sync_merge_sqlite._shared_sync_merge_cadastro_lojas_versioned(
                str(target),
                _csv_bytes([_row("store-b", "SYNC", "Remoto", 1, "2026-08-28T10:00:00.000002Z")]),
            )
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            failures.append(exc)

    def run_crud():
        try:
            cadastro_lojas_produtos.salvar_produto_loja(
                "000002", "store-a", {"sku": "CRUD", "nome": "Local"},
            )
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            failures.append(exc)
        finally:
            crud_done.set()

    merge_thread = threading.Thread(target=run_merge, daemon=True)
    merge_thread.start()
    assert writer_entered.wait(5)

    crud_thread = threading.Thread(target=run_crud, daemon=True)
    crud_thread.start()
    try:
        assert not crud_done.wait(0.25), "CRUD nao aguardou o lock mantido pelo merge"
    finally:
        allow_writer.set()

    merge_thread.join(5)
    crud_thread.join(5)
    assert not merge_thread.is_alive()
    assert not crud_thread.is_alive()
    if failures:
        raise failures[0]
    assert {(row["store_id"], row["sku"]) for row in _read_rows(target)} == {
        ("store-a", "BASE"),
        ("store-a", "CRUD"),
        ("store-b", "SYNC"),
    }


def test_cadastro_produtos_legado_preserva_merge_add_only(tmp_path):
    target = tmp_path / "cadastro_produtos.csv"
    target.write_text("sku,nome\n001,Local\n", encoding="utf-8")
    remote = "sku,nome\n001,Remoto\n002,Novo\n".encode("utf-8")

    result = shared_sync_merge_sqlite._shared_sync_merge_csv_add_only(
        str(target), remote, "cadastro", "cadastro_produtos.csv",
    )

    rows = _read_rows(target)
    assert result["added"] == 1
    assert {row["sku"]: row["nome"] for row in rows} == {
        "001": "Local",
        "002": "Novo",
    }


@pytest.mark.parametrize(
    "photo_column",
    [
        "foto",
        "cg_foto",
        "imagem",
        "imagem_url",
        "image_url",
        "url_imagem",
        "link_imagem",
        "picture",
        "thumbnail_url",
        "link_foto",
    ],
)
def test_shared_sync_strict_rejeita_foto_local_no_csv_legado_sem_backup(
    tmp_path,
    monkeypatch,
    photo_column,
):
    from backend.services import cadastro_lojas_produtos

    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [],
    )
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [],
            }
        ),
        encoding="utf-8",
    )
    target = tenant / "cadastro_produtos.csv"
    target.write_text("sku,nome,foto\nBASE,Local,\n", encoding="utf-8-sig")
    target_before = target.read_bytes()
    remote = pd.DataFrame(
        [{"sku": "001", "nome": "Remoto", photo_column: "cadastro_fotos/001.jpg"}]
    ).to_csv(index=False).encode("utf-8-sig")

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle("cadastro", [("cadastro_produtos.csv", remote)]),
            "operador",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert exc_info.value.detail["skus"] == ["001"]
    assert target.read_bytes() == target_before
    assert not (tenant / "_shared_sync_backups").exists()
    assert not (tenant / "cadastro_fotos").exists()


@pytest.mark.parametrize("photo_ref", ["", "https://cdn.example/cadastro_fotos/001.jpg"])
def test_shared_sync_strict_preserva_foto_vazia_ou_url_externa_no_csv_legado(
    tmp_path,
    monkeypatch,
    photo_ref,
):
    from backend.services import cadastro_lojas_produtos

    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [],
    )
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [],
            }
        ),
        encoding="utf-8",
    )
    remote = pd.DataFrame(
        [{"sku": "001", "nome": "Remoto", "foto": photo_ref}]
    ).to_csv(index=False).encode("utf-8-sig")

    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle("cadastro", [("cadastro_produtos.csv", remote)]),
        "operador",
    )

    assert result["files"] == ["cadastro_produtos.csv"]
    assert _read_rows(tenant / "cadastro_produtos.csv")[0]["foto"] == photo_ref


def test_shared_sync_peer_novo_instala_config_antes_do_csv_e_fanout(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b", "store-c")
    _write_store_config(tenant, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    refs = {
        store_id: (
            "cadastro_fotos/lojas/"
            f"{cadastro_fotos._cadastro_store_id_foto_segmento(store_id)}/001.jpg"
        )
        for store_id in store_ids
    }
    rows = [
        {
            **_row(store_id, "001", f"Produto {store_id}", 1, "2026-09-02T10:00:00Z"),
            "foto": refs[store_id],
        }
        for store_id in store_ids
    ]
    config = _photo_config_bytes(*store_ids)

    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle(
            "cadastro",
            [
                (cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO, config),
                (REL, _csv_bytes(rows, columns)),
                *((ref, b"foto-grupo-v1") for ref in refs.values()),
            ],
        ),
        "operador",
    )

    persisted = json.loads(
        (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).read_text(
            encoding="utf-8"
        )
    )
    assert persisted["strict_store_scope"] is True
    assert persisted["shared_groups"][0]["store_ids"] == sorted(store_ids)
    assert all((tenant / ref).read_bytes() == b"foto-grupo-v1" for ref in refs.values())
    assert result["files"][0] == cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO


def test_machine_sync_peer_novo_aceita_cadastro_e_fotos_legados_sem_config(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    from backend.services import cadastro_lojas_produtos

    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    remote = _csv_bytes(
        [
            {
                **_row("store-a", "001", "Produto legado", 1, "2026-09-08T10:00:00Z"),
                "foto": "cadastro_fotos/001.jpg",
            }
        ],
        [
            "store_id",
            "sku",
            "nome",
            "foto",
            "row_version",
            "updated_at_utc",
            "deleted_at_utc",
        ],
    )
    remote_cost = _cost_csv_bytes(
        [
            {
                "store_id": "",
                "loja_sync": "Loja store-a",
                "sku": "001",
                "produto": "Produto legado",
                "custo": "10.00",
                "preco": "20.00",
                "imposto": "0",
                "updated_at": "2026-09-08T10:00:00Z",
            }
        ]
    )
    remote_legacy_products = _csv_bytes(
        [
            {
                "sku": "001",
                "produto": "Produto legado",
                "loja_sync": "Loja store-a",
                "foto": "cadastro_fotos/001.jpg",
            }
        ],
        ["sku", "produto", "loja_sync", "foto"],
    )
    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle(
            "cadastro",
            [
                (REL, remote),
                (COST_REL, remote_cost),
                ("cadastro_produtos.csv", remote_legacy_products),
                ("cadastro_fotos/001.jpg", b"foto-legada"),
            ],
        ),
        "operador",
        {"allow_legacy_cadastro_bootstrap": True},
    )

    assert (tenant / "cadastro_fotos" / "001.jpg").read_bytes() == b"foto-legada"
    assert _read_rows(tenant / REL)[0]["foto"] == "cadastro_fotos/001.jpg"
    assert _read_rows(tenant / COST_REL)[0]["sku"] == "001"
    assert _read_rows(tenant / "cadastro_produtos.csv")[0]["loja_sync"] == (
        "Loja store-a"
    )
    assert not (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).exists()
    assert REL in result["files"]


def test_user_share_peer_novo_continua_exigindo_config_de_fotos(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    remote = _csv_bytes(
        [_row("store-a", "001", "Produto", 1, "2026-09-08T10:00:00Z")]
    )

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle("cadastro", [(REL, remote)]),
            "operador",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "cadastro_photo_config_required"
    assert not (tenant / REL).exists()


def test_shared_sync_config_antes_das_lojas_falha_sem_efeitos(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle(
                "cadastro",
                [
                    (
                        cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO,
                        _photo_config_bytes("store-a", "store-b"),
                    )
                ],
            ),
            "operador",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "cadastro_photo_config_store_identity_invalid"
    assert list(tenant.iterdir()) == []


@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        json.dumps(
            {
                "schema": "jk.cadastro.fotos.v1",
                "strict_store_scope": True,
                "shared_groups": [],
                "extra": True,
            }
        ).encode(),
        b'{"schema":"jk.cadastro.fotos.v1","schema":"jk.cadastro.fotos.v1",'
        b'"strict_store_scope":true,"shared_groups":[]}',
        json.dumps(
            {
                "schema": "jk.cadastro.fotos.v1",
                "strict_store_scope": True,
                "shared_groups": [
                    {
                        "group_id": "grupo",
                        "store_ids": ["store-a", "store-b"],
                        "extra": True,
                    }
                ],
            }
        ).encode(),
        _photo_config_bytes("store-a", "store-a"),
        _photo_config_bytes("store-a", "store-foreign"),
        json.dumps(
            {
                "schema": "jk.cadastro.fotos.v1",
                "strict_store_scope": True,
                "shared_groups": [
                    {"group_id": "g1", "store_ids": ["store-a", "store-b"]},
                    {"group_id": "g2", "store_ids": ["store-b", "store-c"]},
                ],
            }
        ).encode(),
    ],
    ids=["malformed", "top-extra", "json-duplicate", "group-extra", "duplicate-store", "foreign", "overlap"],
)
def test_shared_sync_config_remota_invalida_falha_fechado(
    tmp_path,
    monkeypatch,
    payload,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a", "store-b", "store-c")
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle(
                "cadastro",
                [(cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO, payload)],
            ),
            "operador",
        )

    assert exc_info.value.status_code == 409
    assert not (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).exists()


def test_shared_sync_config_rejeita_store_tombstonada(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a", "store-b")
    (tenant / "lojas_sync_tombstones.json").write_text(
        json.dumps(
            [
                {
                    "key": "store:store-b",
                    "type": "store",
                    "store_id": "store-b",
                    "version": 1,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle(
                "cadastro",
                [
                    (
                        cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO,
                        _photo_config_bytes("store-a", "store-b"),
                    )
                ],
            ),
            "operador",
        )

    assert exc_info.value.detail["code"] == "cadastro_photo_config_store_identity_invalid"
    assert exc_info.value.detail["store_ids_tombstonados"] == ["store-b"]
    assert not (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).exists()


def test_shared_sync_config_local_divergente_e_write_once(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a", "store-b")
    _write_default_photo_config(tenant)
    config_path = tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO
    before = config_path.read_bytes()
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle(
                "cadastro",
                [
                    (
                        cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO,
                        _photo_config_bytes("store-a", "store-b"),
                    )
                ],
            ),
            "operador",
        )

    assert exc_info.value.detail["code"] == "cadastro_photo_config_conflict"
    assert config_path.read_bytes() == before


def test_shared_sync_rollback_remove_config_csv_e_foto_novos(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b")
    _write_store_config(tenant, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    refs = {
        store_id: (
            "cadastro_fotos/lojas/"
            f"{cadastro_fotos._cadastro_store_id_foto_segmento(store_id)}/001.jpg"
        )
        for store_id in store_ids
    }
    rows = [
        {
            **_row(store_id, "001", "Produto", 1, "2026-09-02T10:00:00Z"),
            "foto": refs[store_id],
        }
        for store_id in store_ids
    ]
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "_shared_sync_remover_variantes_obsoletas_foto_store",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("falha-pos-foto")),
    )

    with pytest.raises(OSError, match="falha-pos-foto"):
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle(
                "cadastro",
                [
                    (
                        cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO,
                        _photo_config_bytes(*store_ids),
                    ),
                    (REL, _csv_bytes(rows, columns)),
                    *((ref, b"foto") for ref in refs.values()),
                ],
            ),
            "operador",
        )

    assert not (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).exists()
    assert not (tenant / REL).exists()
    assert all(not (tenant / ref).exists() for ref in refs.values())


def test_shared_sync_delta_add_only_instala_config_csv_e_fanout(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b")
    _write_store_config(tenant, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    refs = {
        store_id: (
            "cadastro_fotos/lojas/"
            f"{cadastro_fotos._cadastro_store_id_foto_segmento(store_id)}/001.jpg"
        )
        for store_id in store_ids
    }
    rows = [
        {
            **_row(store_id, "001", "Produto", 1, "2026-09-02T10:00:00Z"),
            "foto": refs[store_id],
        }
        for store_id in store_ids
    ]

    result = shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
        "000002",
        "cadastro",
        "operador",
        [
            (
                cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO,
                _photo_config_bytes(*store_ids),
            ),
            (REL, _csv_bytes(rows, columns)),
            *((ref, b"foto-delta") for ref in refs.values()),
        ],
        str(tenant),
        str(tenant / "backup"),
    )

    assert cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO in result["files"]
    assert all((tenant / ref).read_bytes() == b"foto-delta" for ref in refs.values())


def test_shared_sync_config_link_local_falha_fechado(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a", "store-b")
    externo = tmp_path / "externo.json"
    externo.write_bytes(_photo_config_bytes("store-a", "store-b"))
    config_path = tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO
    try:
        config_path.symlink_to(externo)
    except OSError as exc:
        pytest.skip(f"symlink indisponivel: {exc}")
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle(
                "cadastro",
                [
                    (
                        cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO,
                        _photo_config_bytes("store-a", "store-b"),
                    )
                ],
            ),
            "operador",
        )

    assert exc_info.value.detail["code"] == "cadastro_photo_config_unsafe"
    assert externo.read_bytes() == _photo_config_bytes("store-a", "store-b")


def test_shared_sync_row_vencedora_sem_bytes_rejeita_e_reverte(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    ref = f"cadastro_fotos/lojas/{segmento}/001.jpg"
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    row = {
        **_row("store-a", "001", "Produto", 1, "2026-09-02T10:00:00Z"),
        "foto": ref,
    }

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle("cadastro", [(REL, _csv_bytes([row], columns))]),
            "operador",
        )

    assert exc_info.value.detail["code"] == "shared_sync_store_photo_bytes_required"
    assert not (tenant / REL).exists()
    assert not (tenant / ref).exists()


def test_shared_sync_delta_metadado_sem_bytes_aceita_foto_ja_existente(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    ref = f"cadastro_fotos/lojas/{segmento}/001.jpg"
    photo = tenant / ref
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"bytes-conhecidos")
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    local = {
        **_row("store-a", "001", "Local", 1, "2026-09-02T10:00:00Z"),
        "foto": ref,
    }
    remote = {
        **_row("store-a", "001", "Metadado novo", 2, "2026-09-02T11:00:00Z"),
        "foto": ref,
    }
    (tenant / REL).write_bytes(_csv_bytes([local], columns))

    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle("cadastro", [(REL, _csv_bytes([remote], columns))]),
        "operador",
    )

    assert _read_rows(tenant / REL)[0]["nome"] == "Metadado novo"
    assert photo.read_bytes() == b"bytes-conhecidos"
    assert result["files"] == [REL]


def test_shared_sync_delta_muda_ref_sem_bytes_rejeita(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    old_ref = f"cadastro_fotos/lojas/{segmento}/001.jpg"
    new_ref = f"cadastro_fotos/lojas/{segmento}/001.png"
    old_photo = tenant / old_ref
    old_photo.parent.mkdir(parents=True, exist_ok=True)
    old_photo.write_bytes(b"old")
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    local = {
        **_row("store-a", "001", "Local", 1, "2026-09-02T10:00:00Z"),
        "foto": old_ref,
    }
    remote = {
        **_row("store-a", "001", "Remoto", 2, "2026-09-02T11:00:00Z"),
        "foto": new_ref,
    }
    target = tenant / REL
    target.write_bytes(_csv_bytes([local], columns))
    before = target.read_bytes()

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle("cadastro", [(REL, _csv_bytes([remote], columns))]),
            "operador",
        )

    assert exc_info.value.detail["code"] == "shared_sync_store_photo_bytes_required"
    assert target.read_bytes() == before
    assert old_photo.read_bytes() == b"old"
    assert not (tenant / new_ref).exists()


@pytest.mark.parametrize("ext", ["gif", "bmp"])
def test_shared_sync_aplica_foto_store_gif_bmp(tmp_path, monkeypatch, ext):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    ref = f"cadastro_fotos/lojas/{segmento}/001.{ext}"
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    row = {
        **_row("store-a", "001", "Produto", 1, "2026-09-02T10:00:00Z"),
        "foto": ref,
    }

    shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle("cadastro", [(REL, _csv_bytes([row], columns)), (ref, b"imagem")]),
        "operador",
    )

    assert (tenant / ref).read_bytes() == b"imagem"


@pytest.mark.parametrize("ext", ["gif", "bmp"])
def test_shared_sync_aplica_foto_global_legada_gif_bmp(tmp_path, monkeypatch, ext):
    from backend.services import cadastro_lojas_produtos

    tenant = tmp_path / "000002"
    tenant.mkdir()
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    rel = f"cadastro_fotos/001.{ext}"

    result = shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "000002",
        "cadastro",
        _bundle("cadastro", [(rel, b"legacy")]),
        "operador",
    )

    assert (tenant / rel).read_bytes() == b"legacy"
    assert result["files"] == [rel]


@pytest.mark.parametrize("events,blocked", [
    ([{"version": 1, "deleted_at": "2026-09-01"}, {"version": 2, "restored_at": "2026-09-02"}], False),
    ([{"version": 3, "deleted_at": "2026-09-03"}, {"version": 2, "restored_at": "2026-09-02"}], True),
    ([{"version": 2, "restored_at": "2026-09-02"}, {"version": 2, "deleted_at": "2026-09-02"}], True),
])
def test_restoration_event_order_is_shared_by_csv_and_photo_config(tmp_path, monkeypatch, events, blocked):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a", "store-b")
    tombs = [{"type": "store", "key": "store:store-a:", "store_id": "store-a", **event} for event in events]
    (tenant / "lojas_sync_tombstones.json").write_text(json.dumps(tombs), encoding="utf-8")
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _: str(tenant), raising=False)
    files = [
        ("cadastro_fotos_config.json", _photo_config_bytes("store-a", "store-b")),
        (REL, _csv_bytes([_row("store-a", "001", "Produto", 1, "2026-09-01T10:00:00Z")])),
        (COST_REL, _cost_csv_bytes([{"store_id": "store-a", "sku": "001", "custo": "12.00"}])),
        ("produtos_compilado.csv", b"store_id,sku,nome\nstore-a,001,Produto\n"),
    ]
    if blocked:
        with pytest.raises(HTTPException):
            shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "cadastro", _bundle("cadastro", files))
        assert not (tenant / REL).exists()
        assert not (tenant / "cadastro_fotos_config.json").exists()
    else:
        result = shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "cadastro", _bundle("cadastro", files))
        assert result["file_count"] == 4
        assert _read_rows(tenant / REL)[0]["sku"] == "001"
        assert _read_rows(tenant / COST_REL)[0]["custo"] == "12.00"


@pytest.mark.parametrize("cross_user", [False, True])
def test_full_cadastro_rollback_includes_completed_photos_costs_and_new_files(tmp_path, monkeypatch, cross_user):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _: str(tenant), raising=False)
    segment = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    old_ref = f"cadastro_fotos/lojas/{segment}/001.jpg"
    new_ref = f"cadastro_fotos/lojas/{segment}/001.png"
    old_photo = tenant / old_ref
    old_photo.parent.mkdir(parents=True)
    old_photo.write_bytes(b"original-photo")
    columns = ["store_id", "sku", "nome", "foto", "row_version", "updated_at_utc", "deleted_at_utc"]
    original = _csv_bytes([{**_row("store-a", "001", "Original", 1, "2026-09-01T10:00:00Z"), "foto": old_ref}], columns)
    (tenant / REL).write_bytes(original)
    remote = _csv_bytes([{**_row("store-a", "001", "Updated", 2, "2026-09-02T10:00:00Z"), "foto": new_ref}], columns)
    files = [(REL, remote), (new_ref, b"new-photo"),
             (COST_REL, _cost_csv_bytes([{"store_id": "store-a", "sku": "001", "custo": "15.00"}])),
             ("cadastro_produtos_meta.json", b'{"updated":true}')]
    original_write = shared_sync_apply_scope._shared_sync_atomic_write
    def fail_after_costs(path, data):
        if Path(path).name == "cadastro_produtos_meta.json":
            assert (tenant / COST_REL).exists()
            assert (tenant / new_ref).read_bytes() == b"new-photo"
            raise OSError("synthetic final write failure")
        return original_write(path, data)
    monkeypatch.setattr(shared_sync_apply_scope, "_shared_sync_atomic_write", fail_after_costs)
    if cross_user:
        write_missing = shared_sync_merge_sqlite._shared_sync_write_missing_file
        def fail_missing(path, data):
            if Path(path).name == "cadastro_produtos_meta.json":
                raise OSError("synthetic final write failure")
            return write_missing(path, data)
        monkeypatch.setattr(shared_sync_merge_sqlite, "_shared_sync_write_missing_file", fail_missing)
    with pytest.raises(OSError):
        if cross_user:
            shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only("000002", "cadastro", "user", files, str(tenant), str(tenant / "_backup"))
        else:
            shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "cadastro", _bundle("cadastro", files))
    assert (tenant / REL).read_bytes() == original
    assert old_photo.read_bytes() == b"original-photo"
    assert not (tenant / new_ref).exists()
    assert not (tenant / COST_REL).exists()
    assert not (tenant / "cadastro_produtos_meta.json").exists()


def test_invalid_cost_reference_is_rejected_before_any_product_write(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _: str(tenant), raising=False)
    writes = []
    monkeypatch.setattr(shared_sync_apply_scope, "_shared_sync_atomic_write", lambda *args: writes.append(args[0]))
    files = [(REL, _csv_bytes([_row("store-a", "001", "Product", 1, "2026-09-01T10:00:00Z")])),
             (COST_REL, _cost_csv_bytes([{"store_id": "missing-store", "sku": "001", "custo": "15.00"}]))]
    with pytest.raises(HTTPException) as caught:
        shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "cadastro", _bundle("cadastro", files))
    assert caught.value.detail["code"] == "shared_sync_store_identity_invalid"
    assert writes == []
    assert not (tenant / REL).exists()


def test_manual_roundtrip_preserves_store_sku_costs_custom_fields_and_photo_versions(tmp_path, monkeypatch):
    a, b = tmp_path / "machine-a" / "000002", tmp_path / "machine-b" / "000002"
    for tenant in (a, b):
        tenant.mkdir(parents=True)
        _write_store_config(tenant, "store-a", "store-b")
        _write_default_photo_config(tenant)
        stores = json.loads((tenant / "lojas_config.json").read_text())
        for store in stores:
            store["nome"] = "Same store name"
        (tenant / "lojas_config.json").write_text(json.dumps(stores))
    current = [a]
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _: str(current[0]), raising=False)
    columns = ["store_id", "sku", "nome", "foto", "custom_field", "row_version", "updated_at_utc", "deleted_at_utc"]
    refs = {sid: f"cadastro_fotos/lojas/{cadastro_fotos._cadastro_store_id_foto_segmento(sid)}/001.jpg" for sid in ("store-a", "store-b")}
    rows = [{**_row(sid, "001", sid, 1, "2026-09-01T10:00:00Z"), "foto": refs[sid], "custom_field": sid + "-extra"} for sid in refs]
    costs = [{"store_id": sid, "loja_sync": "Same store name", "sku": "001", "custo": str(i + 10), "updated_at": "2026-09-01T10:00:00Z"} for i, sid in enumerate(refs)]
    files = [(REL, _csv_bytes(rows, columns)), (COST_REL, _cost_csv_bytes(costs)),
             *[(refs[sid], (sid + "-photo").encode()) for sid in refs]]
    initial = _bundle("cadastro", files)
    # Origin already has the initial state; destination receives that same snapshot.
    for tenant in (a, b):
        current[0] = tenant
        monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tenant.parent), raising=False)
        shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "cadastro", initial)
    current[0] = b
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(b.parent), raising=False)
    before = {rel: (b / rel).read_bytes() for rel, _ in files}
    shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "cadastro", initial)
    assert {rel: (b / rel).read_bytes() for rel, _ in files} == before
    rows[0].update(nome="Changed on B", row_version="2", updated_at_utc="2026-09-02T10:00:00Z")
    costs[0].update(custo="99", updated_at="2026-09-02T10:00:00Z")
    newer = _bundle("cadastro", [(REL, _csv_bytes(rows, columns)), (COST_REL, _cost_csv_bytes(costs)),
                                  (refs["store-a"], b"newer-photo"), (refs["store-b"], b"store-b-photo")])
    shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "cadastro", newer)
    current[0] = a
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(a.parent), raising=False)
    shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "cadastro", newer)
    shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "cadastro", initial)
    products = _read_rows(a / REL)
    assert len(products) == 2
    keyed = {row["store_id"]: row for row in products}
    assert keyed["store-a"]["nome"] == "Changed on B"
    assert keyed["store-b"]["nome"] == "store-b"
    assert all(row["sku"] == "001" for row in products)
    assert keyed["store-a"]["custom_field"] == "store-a-extra"
    assert (a / refs["store-a"]).read_bytes() == b"newer-photo"
    assert (a / refs["store-b"]).read_bytes() == b"store-b-photo"
    assert {row["store_id"]: row["custo"] for row in _read_rows(a / COST_REL)} == {"store-a": "99", "store-b": "11"}


def test_failed_manual_pull_does_not_ack_and_same_snapshot_can_be_retried(tmp_path, monkeypatch):
    from backend.services import shared_sync_machine as machine
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _: str(tenant), raising=False)
    files = [(REL, _csv_bytes([_row("store-a", "001", "Product", 1, "2026-09-01T10:00:00Z")])),
             ("cadastro_produtos_meta.json", b'{"updated":true}')]
    bundle = _bundle("cadastro", files)
    meta = {"snapshot_id": "same-snapshot", "snapshot_hash": "same-hash"}
    monkeypatch.setattr(machine, "_shared_sync_remote_meta_by_id", lambda *_: meta)
    monkeypatch.setattr(machine, "_shared_sync_obter_bundle_por_id", lambda *a, **k: (bundle, meta))
    monkeypatch.setattr(machine, "_shared_sync_machine_local_stamp", lambda *a: "stamp")
    monkeypatch.setattr(machine, "_shared_sync_pull_already_current", lambda *a: False)
    monkeypatch.setattr(machine, "_shared_sync_aplicar_pacote", shared_sync_apply_scope._shared_sync_aplicar_pacote)
    receipts, history = [], []
    def receipt(*a, **k):
        receipts.append(True)
        return {"state": "confirmed"}
    monkeypatch.setattr(machine, "_shared_sync_machine_receipt", receipt)
    monkeypatch.setattr(machine, "_shared_sync_state_update", lambda *a, **k: history.append(a))
    original_write = shared_sync_apply_scope._shared_sync_atomic_write
    def fail(path, data):
        if Path(path).name == "cadastro_produtos_meta.json":
            raise OSError("synthetic failure")
        return original_write(path, data)
    monkeypatch.setattr(shared_sync_apply_scope, "_shared_sync_atomic_write", fail)
    session = {"client_id": "000002", "username": "test-user"}
    with pytest.raises(OSError):
        machine._shared_sync_machine_pull_scope(session, "cadastro", force=True, machine_id="B")
    assert receipts == history == []
    assert not (tenant / REL).exists()
    monkeypatch.setattr(shared_sync_apply_scope, "_shared_sync_atomic_write", original_write)
    result = machine._shared_sync_machine_pull_scope(session, "cadastro", force=True, machine_id="B")
    assert result["success"] is True
    assert len(receipts) == len(history) == 1
    assert _read_rows(tenant / REL)[0]["sku"] == "001"


def test_completed_photo_fanout_is_rolled_back_when_final_scope_file_fails(
    tmp_path,
    monkeypatch,
):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    store_ids = ("store-a", "store-b", "store-c")
    _write_store_config(tenant, *store_ids)
    _enable_shared_photo_group(tenant, monkeypatch, *store_ids)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )
    columns = [
        "store_id", "sku", "nome", "foto", "row_version",
        "updated_at_utc", "deleted_at_utc",
    ]
    jpgs = {}
    pngs = {}
    for store_id in store_ids:
        segmento = cadastro_fotos._cadastro_store_id_foto_segmento(store_id)
        jpgs[store_id] = f"cadastro_fotos/lojas/{segmento}/001.jpg"
        pngs[store_id] = f"cadastro_fotos/lojas/{segmento}/001.png"
    local_rows = [
        {
            **_row(store_id, "001", f"Local {store_id}", 1, "2026-08-28T11:00:00Z"),
            "foto": jpgs[store_id],
        }
        for store_id in store_ids
    ]
    target = tenant / REL
    target.write_bytes(_csv_bytes(local_rows, columns))
    target_antes = target.read_bytes()
    for store_id, photo_rel in jpgs.items():
        photo_path = tenant / photo_rel
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        photo_path.write_bytes(f"jpg-antigo-{store_id}".encode())
    jpgs_antes = {rel: (tenant / rel).read_bytes() for rel in jpgs.values()}

    remote_rows = [
        {
            **_row(store_id, "001", f"Remoto PNG {store_id}", 2, "2026-08-28T12:00:00Z"),
            "foto": pngs[store_id],
        }
        for store_id in store_ids
    ]
    write_real = shared_sync_apply_scope._shared_sync_atomic_write
    def fail_metadata(path, data):
        if Path(path).name == "cadastro_produtos_meta.json":
            assert all((tenant / rel).read_bytes() == b"png-novo" for rel in pngs.values())
            assert all(not (tenant / rel).exists() for rel in jpgs.values())
            raise OSError("late scope failure")
        return write_real(path, data)
    monkeypatch.setattr(shared_sync_apply_scope, "_shared_sync_atomic_write", fail_metadata)
    with pytest.raises(OSError, match="late scope failure"):
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "000002",
            "cadastro",
            _bundle(
                "cadastro",
                [
                    *((rel, b"png-novo") for rel in pngs.values()),
                    (REL, _csv_bytes(remote_rows, columns)),
                    ("cadastro_produtos_meta.json", b"{}"),
                ],
            ),
            "operador",
        )

    assert target.read_bytes() == target_antes
    assert all((tenant / rel).read_bytes() == data for rel, data in jpgs_antes.items())
    assert all(not (tenant / rel).exists() for rel in pngs.values())


def test_invalid_cadastro_metadata_blocks_snapshot_before_changes(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    tenant.mkdir()
    _write_store_config(tenant, "store-a")
    _write_default_photo_config(tenant)
    monkeypatch.setattr(shared_sync_apply_scope, "get_tenant_path", lambda _: str(tenant), raising=False)
    data = _csv_bytes([_row("store-a", "001", "Product", 1, "2026-09-01T10:00:00Z")])
    with pytest.raises(HTTPException) as caught:
        shared_sync_apply_scope._shared_sync_aplicar_pacote("000002", "cadastro", _bundle("cadastro", [
            (REL, data), ("cadastro_produtos_meta.json", b"invalid-json"),
        ]))
    assert caught.value.detail["code"] == "shared_sync_cadastro_metadata_invalid"
    assert not (tenant / REL).exists()
