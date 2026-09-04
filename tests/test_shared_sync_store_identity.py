import hashlib
import io
import json
import os
import subprocess
import threading
import zipfile

import pytest
from fastapi import HTTPException

from backend.services import shared_sync
from backend.services import cadastro_custos
from backend.services import cadastro_fotos
from backend.services import cadastro_lojas_produtos
from backend.services import cadastro_tenant_trust
from backend.services import integracoes
from backend.services import shared_sync_apply_scope
from backend.services import shared_sync_merge_sqlite
from backend.services.path_coordination import path_lock_for


def _integration(token: str) -> dict:
    return {"access_token": token, "connected": True}


def _bundle(scope: str, files: list[tuple[str, bytes]]) -> bytes:
    entries = [
        {
            "relative_path": rel,
            "size": len(data),
            "mtime": 1,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        for rel, data in files
    ]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema": 2,
                    "scope": scope,
                    "file_count": len(entries),
                    "files": entries,
                }
            ),
        )
        for rel, data in files:
            archive.writestr(f"files/{rel}", data)
    return output.getvalue()


def _configure_integracoes_tenant(tmp_path):
    info_dir = tmp_path / "info"

    def tenant_path(client_id):
        tenant = info_dir / str(client_id or "default")
        tenant.mkdir(parents=True, exist_ok=True)
        return str(tenant)

    integracoes.configure_integracoes_context(
        pasta_info=str(info_dir),
        get_tenant_path=tenant_path,
        normalizar_integracao_conectada=lambda _servico, dados: dados,
    )
    return info_dir / "cliente-a", tenant_path


def _configure_cadastro_fotos_tenant(monkeypatch, tenant, tenant_path):
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tenant.parent), raising=False)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", tenant_path, raising=False)


def _write_strict_photo_config(tenant):
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


def _create_tenant_junction(link_path, target_path):
    if os.name != "nt" or not hasattr(link_path, "is_junction"):
        pytest.skip("junction verificavel somente no Windows")
    target_path.mkdir(parents=True, exist_ok=True)
    link_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link_path), str(target_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("criacao de junction indisponivel")
    assert link_path.is_junction()


def _remove_tenant_junction(link_path):
    if os.path.lexists(link_path):
        os.rmdir(link_path)


def _store_csv_payload(rel: str, store_id: str) -> bytes:
    if rel == "cadastro_produtos_lojas.csv":
        return (
            "store_id,sku,nome,row_version,updated_at_utc,deleted_at_utc\n"
            f"{store_id},SKU-1,Produto,1,2026-08-28T12:00:00Z,\n"
        ).encode("utf-8")
    if rel == "cadastro_custos_lojas.csv":
        return (
            "store_id,loja_sync,sku,custo,updated_at\n"
            f"{store_id},Loja A,SKU-1,10.00,2026-08-28T12:00:00Z\n"
        ).encode("utf-8")
    return (
        "store_id,loja_sync,sku,nome\n"
        f"{store_id},Loja A,SKU-1,Produto\n"
    ).encode("utf-8")


def test_lojas_delta_usa_store_id_para_homonimas_e_mantem_chave_no_rename():
    lojas = [
        {
            "store_id": "store-A",
            "nome": "Loja Igual",
            "integracoes": {"bling": _integration("token-a")},
        },
        {
            "store_id": "store-B",
            "nome": "Loja Igual",
            "integracoes": {"bling": _integration("token-b")},
        },
    ]

    filtrado, chaves = shared_sync._shared_sync_lojas_delta_bytes(
        "lojas_integracoes",
        "lojas_config.json",
        json.dumps(lojas).encode("utf-8"),
        set(),
    )

    assert filtrado is not None
    assert len(json.loads(filtrado.decode("utf-8"))) == 2
    assert set(chaves) == {
        "lojas_integracoes:loja:store_id:store-A",
        "lojas_integracoes:loja:store_id:store-B",
    }

    renomeada = [{**lojas[0], "nome": "Nome completamente novo"}]
    _, chaves_rename = shared_sync._shared_sync_lojas_delta_bytes(
        "lojas_integracoes",
        "lojas_config.json",
        json.dumps(renomeada).encode("utf-8"),
        set(),
    )
    assert chaves_rename == ["lojas_integracoes:loja:store_id:store-A"]

    _, chaves_integracao = shared_sync._shared_sync_lojas_delta_bytes(
        "lojas_integracoes",
        "lojas_config.json",
        json.dumps(renomeada).encode("utf-8"),
        {"lojas_integracoes:loja:store_id:store-A"},
    )
    assert chaves_integracao == ["lojas_integracoes:integracao:store_id:store-A:bling"]


def test_lojas_delta_preserva_chave_de_nome_apenas_para_registro_legado():
    legado = [{"nome": "Lója Legada", "integracoes": {"bling": _integration("token")}}]

    _, chaves = shared_sync._shared_sync_lojas_delta_bytes(
        "lojas_integracoes",
        "lojas_config.json",
        json.dumps(legado).encode("utf-8"),
        set(),
    )

    assert chaves == ["lojas_integracoes:loja:lojalegada"]


def test_merge_por_store_id_preserva_homonimas_e_aplica_rename_sem_misturar(tmp_path):
    destino = tmp_path / "lojas_config.json"
    destino.write_text(
        json.dumps(
            [
                {
                    "store_id": "store-A",
                    "nome": "Loja Igual",
                    "_sync_version": 1,
                    "_sync_updated_at": "2026-08-28T10:00:00Z",
                    "integracoes": {"bling": _integration("bling-a")},
                },
                {
                    "store_id": "store-B",
                    "nome": "Loja Igual",
                    "_sync_version": 1,
                    "_sync_updated_at": "2026-08-28T10:00:00Z",
                    "integracoes": {"bling": _integration("bling-b")},
                },
            ]
        ),
        encoding="utf-8",
    )
    remoto = [
        {
            "store_id": "store-A",
            "nome": "Loja A Renomeada",
            "_sync_version": 2,
            "_sync_updated_at": "2026-08-28T11:00:00Z",
            "integracoes": {"mercadolivre": _integration("ml-a")},
        },
        {
            "store_id": "store-B",
            "nome": "Loja Igual",
            "_sync_version": 2,
            "_sync_updated_at": "2026-08-28T11:00:00Z",
            "integracoes": {"mercadoturbo": _integration("turbo-b")},
        },
    ]

    merged = json.loads(
        shared_sync._shared_sync_merge_lojas_integracoes_bytes(
            str(destino), json.dumps(remoto).encode("utf-8"), add_only=True
        ).decode("utf-8")
    )
    por_id = {item["store_id"]: item for item in merged}

    assert len(merged) == 2
    assert por_id["store-A"]["nome"] == "Loja A Renomeada"
    assert por_id["store-A"]["_sync_version"] == 2
    assert set(por_id["store-A"]["integracoes"]) == {"bling", "mercadolivre"}
    assert por_id["store-A"]["integracoes"]["bling"]["access_token"] == "bling-a"
    assert "mercadoturbo" not in por_id["store-A"]["integracoes"]
    assert set(por_id["store-B"]["integracoes"]) == {"bling", "mercadoturbo"}
    assert por_id["store-B"]["integracoes"]["bling"]["access_token"] == "bling-b"
    assert "mercadolivre" not in por_id["store-B"]["integracoes"]


def test_merge_por_store_id_preserva_uniao_estavel_de_aliases_quando_remoto_mais_novo(tmp_path):
    destino = tmp_path / "lojas_config.json"
    destino.write_text(
        json.dumps(
            [
                {
                    "store_id": "store-A",
                    "nome": "Nome Local",
                    "nomes_anteriores": ["Primeiro Nome", "ALIAS comum"],
                    "_sync_version": 1,
                    "_sync_updated_at": "2026-08-28T10:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )
    remoto = [
        {
            "store_id": "store-A",
            "nome": "Nome Remoto Novo",
            "nomes_anteriores": ["alias COMUM", "Nome Remoto Antigo"],
            "_sync_version": 2,
            "_sync_updated_at": "2026-08-28T11:00:00Z",
        }
    ]

    merged = json.loads(
        shared_sync._shared_sync_merge_lojas_integracoes_bytes(
            str(destino), json.dumps(remoto).encode("utf-8"), add_only=True,
        ).decode("utf-8")
    )[0]

    assert merged["nome"] == "Nome Remoto Novo"
    assert merged["nomes_anteriores"] == [
        "Primeiro Nome",
        "ALIAS comum",
        "Nome Remoto Antigo",
        "Nome Local",
    ]


def test_merge_csv_add_only_usa_lock_canonico_e_preserva_update_concorrente(tmp_path):
    target = tmp_path / "produtos_compilado.csv"
    target.write_text("sku,nome\nBASE,Base\n", encoding="utf-8")
    remoto = b"sku,nome\nREMOTO,Remoto\n"
    iniciado = threading.Event()
    concluido = threading.Event()
    falhas: list[BaseException] = []

    def merge_remoto() -> None:
        iniciado.set()
        try:
            shared_sync_merge_sqlite._shared_sync_merge_csv_add_only(
                str(target), remoto, "cadastro", "produtos_compilado.csv",
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            falhas.append(exc)
        finally:
            concluido.set()

    with path_lock_for(target):
        thread = threading.Thread(target=merge_remoto, daemon=True)
        thread.start()
        assert iniciado.wait(2)
        assert not concluido.wait(0.2), "Shared Sync nao aguardou o lock canonico"
        target.write_text("sku,nome\nBASE,Base\nLOCAL,Local\n", encoding="utf-8")

    thread.join(5)
    assert not thread.is_alive()
    if falhas:
        raise falhas[0]
    linhas = target.read_text(encoding="utf-8-sig").splitlines()
    assert linhas[0] == "sku,nome"
    assert set(linhas[1:]) == {"BASE,Base", "LOCAL,Local", "REMOTO,Remoto"}


@pytest.mark.parametrize(
    "rel",
    [
        "cadastro_produtos_lojas.csv",
        "cadastro_custos_lojas.csv",
        "produtos_compilado.csv",
    ],
)
def test_pull_falha_fechado_quando_delete_da_loja_foi_persistido_primeiro(
    tmp_path,
    monkeypatch,
    rel,
):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    _configure_cadastro_fotos_tenant(monkeypatch, tenant, tenant_path)
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-A", "nome": "Loja A", "integracoes": {}}],
    )
    _write_strict_photo_config(tenant)
    integracoes.excluir_loja("cliente-a", "Loja A", store_id="store-A")
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda client_id: tenant_path(client_id),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "cliente-a",
            "cadastro",
            _bundle("cadastro", [(rel, _store_csv_payload(rel, "store-A"))]),
            "operador",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "shared_sync_store_identity_invalid"
    assert exc_info.value.detail["store_ids_tombstonados"] == ["store-A"]
    assert not (tenant / rel).exists()


@pytest.mark.parametrize(
    "rel",
    [
        "cadastro_produtos_lojas.csv",
        "cadastro_custos_lojas.csv",
        "produtos_compilado.csv",
    ],
)
def test_merge_primeiro_faz_delete_detectar_associacao_da_loja(
    tmp_path,
    monkeypatch,
    rel,
):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    _configure_cadastro_fotos_tenant(monkeypatch, tenant, tenant_path)
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-A", "nome": "Loja A", "integracoes": {}}],
    )
    _write_strict_photo_config(tenant)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda client_id: tenant_path(client_id),
        raising=False,
    )

    shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "cliente-a",
        "cadastro",
        _bundle("cadastro", [(rel, _store_csv_payload(rel, "store-A"))]),
        "operador",
    )

    with pytest.raises(HTTPException) as exc_info:
        integracoes.excluir_loja("cliente-a", "Loja A", store_id="store-A")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["store_id"] == "store-A"
    assert [loja["store_id"] for loja in integracoes.carregar_lojas("cliente-a")] == [
        "store-A"
    ]


def test_custos_remotos_sem_store_id_nao_reassociam_alias_de_loja(tmp_path, monkeypatch):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "cliente-a",
        [
            {
                "store_id": "store-A",
                "nome": "Nome Atual",
                "nomes_anteriores": ["Nome Antigo"],
                "integracoes": {},
            }
        ],
    )
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        lambda client_id: tenant_path(client_id),
        raising=False,
    )
    remoto = (
        "store_id,loja_sync,sku,custo,updated_at\n"
        ",Nome Antigo,SKU-1,10.00,2026-08-28T12:00:00Z\n"
    ).encode("utf-8")

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "cliente-a",
            "cadastro",
            _bundle("cadastro", [("cadastro_custos_lojas.csv", remoto)]),
            "operador",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "shared_sync_store_id_required"
    assert not (tenant / "cadastro_custos_lojas.csv").exists()


def test_tombstones_mesclam_por_chave_e_versao_sem_regredir_delete(tmp_path):
    target = tmp_path / "lojas_sync_tombstones.json"
    target.write_text(
        json.dumps(
            [
                {
                    "key": "store:store-A:",
                    "type": "store",
                    "store_id": "store-A",
                    "version": 3,
                    "deleted_at": "2026-08-28T12:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )
    remoto = json.dumps(
        [
            {
                "key": "store:store-A:",
                "type": "store",
                "store_id": "store-A",
                "version": 2,
                "deleted_at": "2026-08-28T11:00:00Z",
            },
            {
                "key": "store:store-B:",
                "type": "store",
                "store_id": "store-B",
                "version": 1,
                "deleted_at": "2026-08-28T13:00:00Z",
            },
        ]
    ).encode("utf-8")

    merged = shared_sync_merge_sqlite._shared_sync_merge_tombstones_integracoes_payload(
        str(target),
        remoto,
    )
    por_id = {item["store_id"]: item for item in merged}

    assert por_id["store-A"]["version"] == 3
    assert por_id["store-B"]["version"] == 1


def test_tombstone_com_key_inconsistente_nao_remove_outra_loja(tmp_path):
    tenant, _tenant_path = _configure_integracoes_tenant(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [
            {"store_id": "store-A", "nome": "Loja A", "integracoes": {}},
            {"store_id": "store-B", "nome": "Loja B", "integracoes": {}},
        ],
    )
    inconsistente = json.dumps(
        [
            {
                "key": "store:store-A:",
                "type": "store",
                "store_id": "store-B",
                "service": "",
                "version": 2,
                "deleted_at": "2026-08-28T14:00:00Z",
            }
        ]
    ).encode("utf-8")

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
            "cliente-a",
            [("lojas_sync_tombstones.json", inconsistente)],
            str(tenant),
            str(tenant / "backup"),
            add_only=True,
        )

    assert exc_info.value.status_code == 502
    assert "key inconsistente" in str(exc_info.value.detail)
    assert {loja["store_id"] for loja in integracoes.carregar_lojas("cliente-a")} == {
        "store-A",
        "store-B",
    }
    assert not (tenant / "lojas_sync_tombstones.json").exists()


def test_tombstone_de_integracao_desconecta_store_id_exato_sem_afetar_homonima(tmp_path):
    tenant, _tenant_path = _configure_integracoes_tenant(tmp_path)
    tenant.mkdir(parents=True, exist_ok=True)
    lojas = [
        {
            "store_id": "store-A",
            "nome": "Loja Igual",
            "_sync_version": 100,
            "integracoes": {"bling": {"connected": True, "access_token": "token-a", "_sync_version": 1}},
        },
        {
            "store_id": "store-B",
            "nome": "Loja Igual",
            "_sync_version": 100,
            "integracoes": {"bling": {"connected": True, "access_token": "token-b", "_sync_version": 1}},
        },
    ]
    (tenant / "lojas_config.json").write_text(json.dumps(lojas), encoding="utf-8")
    tombstone = json.dumps(
        [
            {
                "key": "integration:store-A:bling",
                "type": "integration",
                "store_id": "store-A",
                "service": "bling",
                "version": 2,
                "deleted_at": "2026-08-28T15:00:00Z",
            }
        ]
    ).encode("utf-8")

    shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
        "cliente-a",
        [
            ("lojas_config.json", json.dumps(lojas).encode("utf-8")),
            ("lojas_sync_tombstones.json", tombstone),
        ],
        str(tenant),
        str(tenant / "backup"),
        add_only=True,
    )

    por_id = {loja["store_id"]: loja for loja in integracoes.carregar_lojas("cliente-a")}
    assert por_id["store-A"]["integracoes"]["bling"]["connected"] is False
    assert "access_token" not in por_id["store-A"]["integracoes"]["bling"]
    assert por_id["store-B"]["integracoes"]["bling"]["access_token"] == "token-b"
    assert por_id["store-B"]["integracoes"]["bling"]["connected"] is True


def test_reconexao_causal_posterior_vence_tombstone_no_pull_seguinte(tmp_path):
    tenant, _tenant_path = _configure_integracoes_tenant(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [
            {
                "store_id": "store-A",
                "nome": "Loja A",
                "integracoes": {
                    "bling": {"connected": True, "access_token": "token-antigo"},
                },
            }
        ],
    )
    integracoes.desconectar_api_loja(
        "cliente-a",
        "Loja A",
        "bling",
        store_id="store-A",
    )
    tombstones = json.loads(
        (tenant / "lojas_sync_tombstones.json").read_text(encoding="utf-8")
    )
    tombstone_version = tombstones[-1]["version"]

    integracoes.atualizar_api_loja(
        "cliente-a",
        "Loja A",
        "bling",
        {"connected": True, "access_token": "token-reconectado"},
        store_id="store-A",
    )
    reconectada = integracoes.carregar_lojas("cliente-a")[0]
    assert reconectada["integracoes"]["bling"]["_sync_version"] > tombstone_version

    shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
        "cliente-a",
        [("lojas_sync_tombstones.json", json.dumps(tombstones).encode("utf-8"))],
        str(tenant),
        str(tenant / "backup"),
        add_only=True,
    )

    final = integracoes.carregar_lojas("cliente-a")[0]["integracoes"]["bling"]
    assert final["connected"] is True
    assert final["access_token"] == "token-reconectado"


def test_pull_add_only_aguarda_refresh_e_preserva_oauth_local_mais_novo(
    tmp_path,
):
    tenant, _tenant_path = _configure_integracoes_tenant(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [
            {
                "store_id": "store-A",
                "nome": "Nome Local",
                "integracoes": {
                    "bling": {"access_token": "inicial", "updated_at": 1},
                },
            }
        ],
    )
    remoto = json.dumps(
        [
            {
                "store_id": "store-A",
                "nome": "Nome Remoto",
                "_sync_version": 99,
                "_sync_updated_at": "2026-08-28T13:00:00Z",
                "integracoes": {
                    "bling": {"access_token": "remoto-antigo", "updated_at": 1},
                },
            }
        ]
    ).encode("utf-8")
    iniciado = threading.Event()
    concluido = threading.Event()
    falhas: list[BaseException] = []

    def pull() -> None:
        iniciado.set()
        try:
            shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
                "cliente-a",
                [("lojas_config.json", remoto)],
                str(tenant),
                str(tenant / "backup"),
                add_only=True,
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            falhas.append(exc)
        finally:
            concluido.set()

    with integracoes._LOJAS_CONFIG_LOCK:
        thread = threading.Thread(target=pull, daemon=True)
        thread.start()
        assert iniciado.wait(2)
        assert not concluido.wait(0.2), "pull nao aguardou o lock da configuracao"
        local = integracoes.carregar_lojas("cliente-a")
        local[0]["integracoes"]["bling"] = {
            "access_token": "refresh-local",
            "updated_at": 2,
        }
        integracoes.salvar_lojas("cliente-a", local)

    thread.join(5)
    assert not thread.is_alive()
    if falhas:
        raise falhas[0]
    loja = integracoes.carregar_lojas("cliente-a")[0]
    assert loja["nome"] == "Nome Remoto"
    assert loja["integracoes"]["bling"]["access_token"] == "refresh-local"
    assert "Nome Local" in loja["nomes_anteriores"]


def test_pull_add_only_nao_ressuscita_loja_ja_tombstonada(tmp_path, monkeypatch):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-A", "nome": "Loja A", "integracoes": {}}],
    )
    integracoes.excluir_loja("cliente-a", "Loja A", store_id="store-A")
    remoto = json.dumps(
        [{"store_id": "store-A", "nome": "Snapshot antigo", "integracoes": {}}]
    ).encode("utf-8")

    shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
        "cliente-a",
        [("lojas_config.json", remoto)],
        str(tenant),
        str(tenant / "backup"),
        add_only=True,
    )

    assert integracoes.carregar_lojas("cliente-a") == []


def test_tombstone_remoto_de_store_nao_orfana_catalogo_local_offline(
    tmp_path,
    monkeypatch,
):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-A", "nome": "Loja A", "integracoes": {}}],
    )
    (tenant / "cadastro_produtos_lojas.csv").write_text(
        "store_id,sku,sku_normalizado,row_version,updated_at_utc,deleted_at_utc\n"
        "store-A,SKU-1,SKU-1,1,2026-08-28T12:00:00Z,\n",
        encoding="utf-8",
    )
    tombstone = json.dumps(
        [
            {
                "key": "store:store-A:",
                "type": "store",
                "store_id": "store-A",
                "service": "",
                "version": 2,
                "deleted_at": "2026-08-28T16:00:00Z",
            }
        ]
    ).encode("utf-8")

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
            "cliente-a",
            [("lojas_sync_tombstones.json", tombstone)],
            str(tenant),
            str(tenant / "backup"),
            add_only=True,
        )

    assert exc_info.value.status_code == 409
    assert integracoes.carregar_lojas("cliente-a")[0]["store_id"] == "store-A"
    assert not (tenant / "lojas_sync_tombstones.json").exists()
    assert (tenant / "cadastro_produtos_lojas.csv").exists()


@pytest.mark.parametrize("add_only", [False, True])
def test_shared_sync_legacy_csv_nao_toca_sku_controlado(
    tmp_path,
    monkeypatch,
    add_only,
):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-A", "nome": "Loja A", "integracoes": {}}],
    )
    (tenant / "cadastro_produtos_lojas.csv").write_text(
        "store_id,sku,sku_normalizado,row_version,updated_at_utc,deleted_at_utc\n"
        "store-A,SKU-1,SKU-1,1,2026-08-28T12:00:00Z,\n",
        encoding="utf-8",
    )
    target = tenant / "cadastro_produtos.csv"
    target.write_text("sku,nome\nSKU-1,Local protegido\n", encoding="utf-8")
    antes = target.read_bytes()
    remoto = b"sku,nome\nOUTRO,Remoto\n"

    with pytest.raises(HTTPException) as exc_info:
        if add_only:
            shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
                "cliente-a",
                "cadastro",
                "operador",
                [("cadastro_produtos.csv", remoto)],
                str(tenant),
                str(tenant / "backup"),
            )
        else:
            monkeypatch.setattr(
                shared_sync_apply_scope,
                "get_tenant_path",
                lambda client_id: tenant_path(client_id),
                raising=False,
            )
            shared_sync_apply_scope._shared_sync_aplicar_pacote(
                "cliente-a",
                "cadastro",
                _bundle("cadastro", [("cadastro_produtos.csv", remoto)]),
                "operador",
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert target.read_bytes() == antes


@pytest.mark.parametrize("add_only", [False, True])
@pytest.mark.parametrize(
    "campo_reservado",
    [
        "store_id",
        "loja_sync",
        "loja",
        "sku_normalizado",
        "row_version",
        "updated_at_utc",
        "deleted_at_utc",
        "scope_source",
    ],
)
def test_shared_sync_legacy_csv_rejeita_metadado_scoped_remoto(
    tmp_path,
    monkeypatch,
    add_only,
    campo_reservado,
):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    target = tenant / "cadastro_produtos.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("sku,nome\nLOCAL,Preservado\n", encoding="utf-8")
    antes = target.read_bytes()
    remoto = f"sku,nome,{campo_reservado}\nREMOTO,Produto,valor\n".encode("utf-8")

    with pytest.raises(HTTPException) as exc_info:
        if add_only:
            shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
                "cliente-a",
                "cadastro",
                "operador",
                [("cadastro_produtos.csv", remoto)],
                str(tenant),
                str(tenant / "backup"),
            )
        else:
            monkeypatch.setattr(
                shared_sync_apply_scope,
                "get_tenant_path",
                lambda client_id: tenant_path(client_id),
                raising=False,
            )
            shared_sync_apply_scope._shared_sync_aplicar_pacote(
                "cliente-a",
                "cadastro",
                _bundle("cadastro", [("cadastro_produtos.csv", remoto)]),
                "operador",
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert campo_reservado in exc_info.value.detail["campos"]
    assert target.read_bytes() == antes


@pytest.mark.parametrize("scope", ["cadastro", "vendas"])
@pytest.mark.parametrize("add_only", [False, True])
@pytest.mark.parametrize("identidade_remota", ["ausente", "tombstonada"])
def test_produtos_compilado_remoto_exige_store_id_atual_exato_sem_reusar_homonimo(
    tmp_path,
    monkeypatch,
    scope,
    add_only,
    identidade_remota,
):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-B", "nome": "Nome Igual", "integracoes": {}}],
    )
    (tenant / "lojas_sync_tombstones.json").write_text(
        json.dumps(
            [
                {
                    "key": "store:store-A:",
                    "type": "store",
                    "store_id": "store-A",
                    "service": "",
                    "version": 10,
                    "deleted_at": "2026-08-28T12:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )
    target = tenant / "produtos_compilado.csv"
    target.write_text(
        "sku,loja_sync\nLOCAL-HISTORICO,Nome Igual\n",
        encoding="utf-8",
    )
    antes = target.read_bytes()
    if identidade_remota == "ausente":
        remoto = b"sku,loja_sync\nREMOTO,Nome Igual\n"
        codigo = "shared_sync_store_id_required"
    else:
        remoto = b"store_id,sku,loja_sync\nstore-A,REMOTO,Nome Igual\n"
        codigo = "shared_sync_store_identity_invalid"

    with pytest.raises(HTTPException) as exc_info:
        if add_only:
            shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
                "cliente-a",
                scope,
                "operador",
                [("produtos_compilado.csv", remoto)],
                str(tenant),
                str(tenant / "backup"),
            )
        else:
            monkeypatch.setattr(
                shared_sync_apply_scope,
                "get_tenant_path",
                lambda client_id: tenant_path(client_id),
                raising=False,
            )
            shared_sync_apply_scope._shared_sync_aplicar_pacote(
                "cliente-a",
                scope,
                _bundle(scope, [("produtos_compilado.csv", remoto)]),
                "operador",
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == codigo
    assert target.read_bytes() == antes


@pytest.mark.parametrize("scope", ["cadastro", "vendas"])
@pytest.mark.parametrize("add_only", [False, True])
def test_produtos_compilado_remoto_aceita_store_id_atual_exato(
    tmp_path,
    monkeypatch,
    scope,
    add_only,
):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-B", "nome": "Nome Igual", "integracoes": {}}],
    )
    target = tenant / "produtos_compilado.csv"
    target.write_text("store_id,sku\nstore-B,LOCAL\n", encoding="utf-8")
    remoto = b"store_id,sku\nstore-B,REMOTO\n"

    if add_only:
        shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
            "cliente-a",
            scope,
            "operador",
            [("produtos_compilado.csv", remoto)],
            str(tenant),
            str(tenant / "backup"),
        )
    else:
        monkeypatch.setattr(
            shared_sync_apply_scope,
            "get_tenant_path",
            lambda client_id: tenant_path(client_id),
            raising=False,
        )
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "cliente-a",
            scope,
            _bundle(scope, [("produtos_compilado.csv", remoto)]),
            "operador",
        )

    assert "REMOTO" in target.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("scope", ["cadastro", "vendas"])
@pytest.mark.parametrize("store_ids", [("store-A", "store-B"), ("store-A", "store-a")])
def test_delta_produtos_compilado_nao_colide_mesmo_sku_entre_store_ids(
    scope,
    store_ids,
):
    primeiro, segundo = store_ids
    payload = (
        "store_id,sku,nome\n"
        f"{primeiro},SKU-IGUAL,Produto A\n"
        f"{segundo},sku-igual,Produto B\n"
    ).encode("utf-8")

    delta, keys = shared_sync._shared_sync_csv_delta_bytes(
        scope,
        "produtos_compilado.csv",
        payload,
        set(),
    )

    assert delta is not None
    assert len(keys) == 2
    assert len(set(keys)) == 2
    texto = delta.decode("utf-8-sig")
    assert primeiro in texto
    assert segundo in texto


@pytest.mark.parametrize("scope", ["cadastro", "vendas"])
@pytest.mark.parametrize("store_id_remoto", ["store-B", "store-a"])
def test_add_only_produtos_compilado_nao_perde_sku_de_outra_store_id(
    tmp_path,
    scope,
    store_id_remoto,
):
    tenant, _tenant_path = _configure_integracoes_tenant(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [
            {"store_id": "store-A", "nome": "Loja A", "integracoes": {}},
            {"store_id": store_id_remoto, "nome": "Loja B", "integracoes": {}},
        ],
    )
    target = tenant / "produtos_compilado.csv"
    target.write_text(
        "store_id,sku,loja_sync\n"
        "store-A,SKU-IGUAL,Loja A\n"
        ",SKU-IGUAL,Nome legado historico\n",
        encoding="utf-8",
    )
    remoto = (
        "store_id,sku,loja_sync\n"
        f"{store_id_remoto},sku-igual,Loja B\n"
    ).encode("utf-8")

    shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
        "cliente-a",
        scope,
        "operador",
        [("produtos_compilado.csv", remoto)],
        str(tenant),
        str(tenant / "backup"),
    )

    texto = target.read_text(encoding="utf-8-sig")
    assert "store-A" in texto
    assert store_id_remoto in texto
    assert "Nome legado historico" in texto


@pytest.mark.parametrize("add_only", [False, True])
def test_shared_sync_foto_global_nao_reintroduz_fallback_de_sku_controlado(
    tmp_path,
    monkeypatch,
    add_only,
):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    _configure_cadastro_fotos_tenant(monkeypatch, tenant, tenant_path)
    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-A", "nome": "Loja A", "integracoes": {}}],
    )
    (tenant / "cadastro_produtos_lojas.csv").write_text(
        "store_id,sku,sku_normalizado,row_version,updated_at_utc,deleted_at_utc\n"
        "store-A,SKU-1,SKU-1,1,2026-08-28T12:00:00Z,\n",
        encoding="utf-8",
    )
    rel = "cadastro_fotos/SKU-1.jpg"

    with pytest.raises(HTTPException) as exc_info:
        if add_only:
            shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
                "cliente-a",
                "cadastro",
                "operador",
                [(rel, b"foto-remota")],
                str(tenant),
                str(tenant / "backup"),
            )
        else:
            monkeypatch.setattr(
                shared_sync_apply_scope,
                "get_tenant_path",
                lambda client_id: tenant_path(client_id),
                raising=False,
            )
            shared_sync_apply_scope._shared_sync_aplicar_pacote(
                "cliente-a",
                "cadastro",
                _bundle("cadastro", [(rel, b"foto-remota")]),
                "operador",
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert not (tenant / rel).exists()


@pytest.mark.parametrize("add_only", [False, True])
def test_shared_sync_foto_global_bloqueia_em_strict_sem_sku_scoped(
    tmp_path,
    monkeypatch,
    add_only,
):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    _configure_cadastro_fotos_tenant(monkeypatch, tenant, tenant_path)
    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-A", "nome": "Loja A", "integracoes": {}}],
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
    assert not (tenant / "cadastro_produtos_lojas.csv").exists()
    rel = "cadastro_fotos/SKU-SEM-LINHA.jpg"

    with pytest.raises(HTTPException) as exc_info:
        if add_only:
            shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
                "cliente-a",
                "cadastro",
                "operador",
                [(rel, b"foto-remota")],
                str(tenant),
                str(tenant / "backup"),
            )
        else:
            monkeypatch.setattr(
                shared_sync_apply_scope,
                "get_tenant_path",
                lambda client_id: tenant_path(client_id),
                raising=False,
            )
            shared_sync_apply_scope._shared_sync_aplicar_pacote(
                "cliente-a",
                "cadastro",
                _bundle("cadastro", [(rel, b"foto-remota")]),
                "operador",
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert exc_info.value.detail["skus"] == []
    assert not (tenant / rel).exists()


def test_add_only_foto_global_considera_outra_extensao_como_existente(
    tmp_path,
    monkeypatch,
):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    _configure_cadastro_fotos_tenant(monkeypatch, tenant, tenant_path)
    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "get_tenant_path",
        tenant_path,
        raising=False,
    )
    pasta = tenant / "cadastro_fotos"
    pasta.mkdir(parents=True, exist_ok=True)
    local = pasta / "SKU-LIVRE.png"
    local.write_bytes(b"foto-local")

    resultado = shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
        "cliente-a",
        "cadastro",
        "operador",
        [("cadastro_fotos/SKU-LIVRE.jpg", b"foto-remota")],
        str(tenant),
        str(tenant / "backup"),
    )

    assert resultado["file_count"] == 0
    assert local.read_bytes() == b"foto-local"
    assert not (pasta / "SKU-LIVRE.jpg").exists()


def test_shared_sync_aceita_apenas_alias_tenant_registrado_e_nao_retargetado(
    tmp_path,
    monkeypatch,
):
    alias_root = tmp_path / "alias-info"
    canonical_root = tmp_path / "canonical-info"
    outro_root = tmp_path / "outro-info"
    alias_root.mkdir()
    canonical_tenant = canonical_root / "cliente-a"
    outro_tenant = outro_root / "cliente-a"
    alias_tenant = alias_root / "cliente-a"
    _create_tenant_junction(alias_tenant, canonical_tenant)

    def tenant_path(_client_id):
        return str(alias_tenant)

    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        tenant_path,
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_lojas_produtos,
        "get_tenant_path",
        tenant_path,
        raising=False,
    )
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(alias_root), raising=False)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", tenant_path, raising=False)
    pacote = _bundle(
        "cadastro",
        [("cadastro_fotos/ALIAS-1.jpg", b"foto-alias")],
    )

    try:
        with pytest.raises(HTTPException) as nao_registrado:
            shared_sync_apply_scope._shared_sync_aplicar_pacote(
                "cliente-a",
                "cadastro",
                pacote,
                "operador",
            )
        assert nao_registrado.value.detail["code"] == (
            "shared_sync_tenant_alias_untrusted"
        )
        assert not (canonical_tenant / "cadastro_fotos" / "ALIAS-1.jpg").exists()

        cadastro_tenant_trust.registrar_alias_tenant(
            alias_root,
            canonical_root,
            "cliente-a",
        )
        shared_sync_apply_scope._shared_sync_aplicar_pacote(
            "cliente-a",
            "cadastro",
            pacote,
            "operador",
        )
        assert (
            canonical_tenant / "cadastro_fotos" / "ALIAS-1.jpg"
        ).read_bytes() == b"foto-alias"

        _remove_tenant_junction(alias_tenant)
        _create_tenant_junction(alias_tenant, outro_tenant)
        with pytest.raises(HTTPException) as retargetado:
            shared_sync_apply_scope._shared_sync_aplicar_pacote(
                "cliente-a",
                "cadastro",
                _bundle(
                    "cadastro",
                    [("cadastro_fotos/ALIAS-2.jpg", b"foto-retarget")],
                ),
                "operador",
            )
        assert retargetado.value.detail["code"] == (
            "shared_sync_tenant_alias_untrusted"
        )
        assert not (outro_tenant / "cadastro_fotos" / "ALIAS-2.jpg").exists()
    finally:
        _remove_tenant_junction(alias_tenant)


@pytest.mark.parametrize("add_only", [False, True])
def test_shared_sync_foto_global_arbitraria_resolve_referencia_do_sku_scoped(
    tmp_path,
    monkeypatch,
    add_only,
):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    _configure_cadastro_fotos_tenant(monkeypatch, tenant, tenant_path)
    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-A", "nome": "Loja A", "integracoes": {}}],
    )
    rel = "cadastro_fotos/portrait.jpg"
    (tenant / "cadastro_produtos_lojas.csv").write_text(
        "store_id,sku,sku_normalizado,foto,row_version,updated_at_utc,deleted_at_utc\n"
        f"store-A,001,001,{rel},1,2026-08-28T12:00:00Z,\n",
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        if add_only:
            shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
                "cliente-a",
                "cadastro",
                "operador",
                [(rel, b"foto-remota")],
                str(tenant),
                str(tenant / "backup"),
            )
        else:
            monkeypatch.setattr(
                shared_sync_apply_scope,
                "get_tenant_path",
                lambda client_id: tenant_path(client_id),
                raising=False,
            )
            shared_sync_apply_scope._shared_sync_aplicar_pacote(
                "cliente-a",
                "cadastro",
                _bundle("cadastro", [(rel, b"foto-remota")]),
                "operador",
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_id_required"
    assert exc_info.value.detail["skus"] == ["001"]
    assert not (tenant / rel).exists()


@pytest.mark.parametrize("add_only", [False, True])
def test_shared_sync_foto_global_url_absoluta_resolve_tenant_e_sku_referenciador(
    tmp_path,
    monkeypatch,
    add_only,
):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    _configure_cadastro_fotos_tenant(monkeypatch, tenant, tenant_path)
    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-A", "nome": "Loja A", "integracoes": {}}],
    )
    rel = "cadastro_fotos/portrait.jpg"
    absolute_ref = "https://jk.local/api/cadastro/foto/cliente-a/portrait.jpg"
    (tenant / "cadastro_produtos_lojas.csv").write_text(
        "store_id,sku,sku_normalizado,foto,row_version,updated_at_utc,deleted_at_utc\n"
        f"store-A,001,001,{absolute_ref},1,2026-08-28T12:00:00Z,\n",
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        if add_only:
            shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
                "cliente-a", "cadastro", "operador", [(rel, b"foto-remota")],
                str(tenant), str(tenant / "backup"),
            )
        else:
            monkeypatch.setattr(
                shared_sync_apply_scope,
                "get_tenant_path",
                lambda client_id: tenant_path(client_id),
                raising=False,
            )
            shared_sync_apply_scope._shared_sync_aplicar_pacote(
                "cliente-a", "cadastro", _bundle("cadastro", [(rel, b"foto-remota")]),
                "operador",
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["skus"] == ["001"]
    assert not (tenant / rel).exists()


def test_snapshot_autoritativo_nao_remove_loja_sem_tombstone_remoto(tmp_path):
    tenant, _tenant_path = _configure_integracoes_tenant(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [
            {"store_id": "store-A", "nome": "Loja A", "integracoes": {}},
            {"store_id": "store-B", "nome": "Loja B", "integracoes": {}},
        ],
    )
    snapshot_incompleto = json.dumps(
        [{"store_id": "store-A", "nome": "Loja A", "integracoes": {}}]
    ).encode("utf-8")

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
            "cliente-a",
            [("lojas_config.json", snapshot_incompleto)],
            str(tenant),
            str(tenant / "backup"),
            add_only=False,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "shared_sync_store_removal_requires_tombstone"
    assert exc_info.value.detail["store_ids"] == ["store-B"]

    snapshot_antigo = json.dumps(
        [{"store_id": "store-B", "nome": "Loja B antiga", "integracoes": {}}]
    ).encode("utf-8")
    shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
        "cliente-a",
        [("lojas_config.json", snapshot_antigo)],
        str(tenant),
        str(tenant / "backup"),
        add_only=True,
    )
    assert {loja["store_id"] for loja in integracoes.carregar_lojas("cliente-a")} == {
        "store-A",
        "store-B",
    }


def test_snapshot_autoritativo_preserva_aliases_locais_da_mesma_store_id(tmp_path):
    tenant, _tenant_path = _configure_integracoes_tenant(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [
            {
                "store_id": "store-A",
                "nome": "Nome Local",
                "nomes_anteriores": ["Nome Original"],
                "integracoes": {"bling": {"access_token": "local"}},
            }
        ],
    )
    remoto = json.dumps(
        [
            {
                "store_id": "store-A",
                "nome": "Nome Remoto",
                "integracoes": {"mercadoturbo": {"token": "remoto"}},
            }
        ]
    ).encode("utf-8")

    shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
        "cliente-a",
        [("lojas_config.json", remoto)],
        str(tenant),
        str(tenant / "backup"),
        add_only=False,
    )

    loja = integracoes.carregar_lojas("cliente-a")[0]
    assert loja["nome"] == "Nome Remoto"
    assert loja["nomes_anteriores"] == ["Nome Original", "Nome Local"]
    assert loja["integracoes"]["bling"]["access_token"] == "local"
    assert loja["integracoes"]["mercadoturbo"]["token"] == "remoto"


def test_snapshot_autoritativo_preserva_clock_v100_contra_stale_v50(tmp_path):
    tenant, _tenant_path = _configure_integracoes_tenant(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [
            {
                "store_id": "store-A",
                "nome": "Nome Local",
                "integracoes": {
                    "bling": {
                        "access_token": "token-local",
                        "connected": True,
                        "updated_at": 1,
                    }
                },
            }
        ],
    )

    def aplicar(version: int, nome: str, token: str) -> None:
        timestamp = f"2026-08-28T12:{version % 60:02d}:00Z"
        remoto = json.dumps(
            [
                {
                    "store_id": "store-A",
                    "nome": nome,
                    "_sync_version": version,
                    "_sync_updated_at": timestamp,
                    "integracoes": {
                        "bling": {
                            "access_token": token,
                            "connected": True,
                            "updated_at": version,
                            "_sync_version": version,
                            "_sync_updated_at": timestamp,
                        }
                    },
                }
            ]
        ).encode("utf-8")
        shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
            "cliente-a",
            [("lojas_config.json", remoto)],
            str(tenant),
            str(tenant / "backup"),
            add_only=False,
        )

    aplicar(100, "Nome v100", "token-100")
    aceito = integracoes.carregar_lojas("cliente-a")[0]
    assert aceito["nome"] == "Nome v100"
    assert aceito["_sync_version"] >= 100
    assert aceito["integracoes"]["bling"]["access_token"] == "token-100"
    assert aceito["integracoes"]["bling"]["_sync_version"] >= 100

    aplicar(50, "Nome v50 stale", "token-50")
    final = integracoes.carregar_lojas("cliente-a")[0]
    assert final["nome"] == "Nome v100"
    assert final["_sync_version"] >= 100
    assert final["integracoes"]["bling"]["access_token"] == "token-100"
    assert final["integracoes"]["bling"]["_sync_version"] >= 100

    versao_antes_mutacao = final["_sync_version"]
    final["nome"] = "Nome local posterior"
    integracoes.salvar_lojas("cliente-a", [final])
    assert integracoes.carregar_lojas("cliente-a")[0]["_sync_version"] > versao_antes_mutacao


@pytest.mark.parametrize("integracoes_remotas", [{}, {"bling": None}])
def test_rename_autoritativo_nao_apaga_integracao_local_ausente_sem_tombstone(
    tmp_path,
    integracoes_remotas,
):
    tenant, _tenant_path = _configure_integracoes_tenant(tmp_path)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-A", "nome": "Nome Local", "integracoes": {}}],
    )
    inicial = json.dumps(
        [
            {
                "store_id": "store-A",
                "nome": "Nome v50",
                "_sync_version": 50,
                "_sync_updated_at": "2026-08-28T12:50:00Z",
                "integracoes": {
                    "bling": {
                        "access_token": "token-100",
                        "connected": True,
                        "updated_at": 100,
                        "_sync_version": 100,
                        "_sync_updated_at": "2026-08-28T13:40:00Z",
                    }
                },
            }
        ]
    ).encode("utf-8")
    shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
        "cliente-a",
        [("lojas_config.json", inicial)],
        str(tenant),
        str(tenant / "backup"),
        add_only=False,
    )

    rename = json.dumps(
        [
            {
                "store_id": "store-A",
                "nome": "Nome v60",
                "_sync_version": 60,
                "_sync_updated_at": "2026-08-28T13:00:00Z",
                "integracoes": integracoes_remotas,
            }
        ]
    ).encode("utf-8")
    shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
        "cliente-a",
        [("lojas_config.json", rename)],
        str(tenant),
        str(tenant / "backup"),
        add_only=False,
    )

    final = integracoes.carregar_lojas("cliente-a")[0]
    assert final["nome"] == "Nome v60"
    assert final["_sync_version"] >= 60
    assert final["integracoes"]["bling"]["access_token"] == "token-100"
    assert final["integracoes"]["bling"]["_sync_version"] >= 100


def test_delete_aguarda_pull_add_only_e_tombstone_final_impede_ressurreicao(
    tmp_path,
    monkeypatch,
):
    tenant, tenant_path = _configure_integracoes_tenant(tmp_path)
    monkeypatch.setattr(cadastro_custos, "get_tenant_path", tenant_path, raising=False)
    integracoes.salvar_lojas(
        "cliente-a",
        [{"store_id": "store-A", "nome": "Loja A", "integracoes": {}}],
    )
    remoto = json.dumps(
        [{"store_id": "store-A", "nome": "Loja A Remota", "integracoes": {}}]
    ).encode("utf-8")
    pull_no_commit = threading.Event()
    liberar_pull = threading.Event()
    delete_concluido = threading.Event()
    falhas: list[BaseException] = []
    salvar_real = integracoes.salvar_lojas

    def salvar_observado(*args, **kwargs):
        if threading.current_thread().name == "pull-shared-sync":
            pull_no_commit.set()
            if not liberar_pull.wait(5):
                raise AssertionError("timeout liberando pull")
        return salvar_real(*args, **kwargs)

    monkeypatch.setattr(integracoes, "salvar_lojas", salvar_observado)

    def pull() -> None:
        try:
            shared_sync_merge_sqlite._shared_sync_aplicar_lojas_integracoes_atomico(
                "cliente-a",
                [("lojas_config.json", remoto)],
                str(tenant),
                str(tenant / "backup"),
                add_only=True,
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            falhas.append(exc)

    def delete() -> None:
        try:
            integracoes.excluir_loja("cliente-a", "Loja A Remota", store_id="store-A")
        except BaseException as exc:  # pragma: no cover - surfaced below
            falhas.append(exc)
        finally:
            delete_concluido.set()

    pull_thread = threading.Thread(target=pull, name="pull-shared-sync", daemon=True)
    pull_thread.start()
    assert pull_no_commit.wait(5)
    delete_thread = threading.Thread(target=delete, name="delete-store", daemon=True)
    delete_thread.start()
    assert not delete_concluido.wait(0.2), "delete nao aguardou o pull sob config lock"
    liberar_pull.set()
    pull_thread.join(5)
    delete_thread.join(5)
    assert not pull_thread.is_alive()
    assert not delete_thread.is_alive()
    if falhas:
        raise falhas[0]
    assert integracoes.carregar_lojas("cliente-a") == []
    tombstones = json.loads(
        (tenant / "lojas_sync_tombstones.json").read_text(encoding="utf-8")
    )
    assert {item["store_id"] for item in tombstones} == {"store-A"}


def test_merge_legado_usa_nome_exato_normalizado_e_nunca_substring(tmp_path):
    destino = tmp_path / "lojas_config.json"
    destino.write_text(
        json.dumps(
            [{"nome": "Loja Norte", "integracoes": {"bling": _integration("local")}}]
        ),
        encoding="utf-8",
    )
    remoto = [
        {"nome": "Lója Norte", "integracoes": {"mercadolivre": _integration("exato")}},
        {"nome": "Loja Norte Filial", "integracoes": {"mercadoturbo": _integration("filial")}},
        {
            "store_id": "store-com-id",
            "nome": "Loja Norte",
            "integracoes": {"bling": _integration("duravel")},
        },
    ]

    merged = json.loads(
        shared_sync._shared_sync_merge_lojas_integracoes_bytes(
            str(destino), json.dumps(remoto).encode("utf-8"), add_only=True
        ).decode("utf-8")
    )

    assert len(merged) == 3
    legado = next(item for item in merged if not item.get("store_id") and item["nome"] == "Loja Norte")
    assert set(legado["integracoes"]) == {"bling", "mercadolivre"}
    filial = next(item for item in merged if item["nome"] == "Loja Norte Filial")
    assert set(filial["integracoes"]) == {"mercadoturbo"}
    duravel = next(item for item in merged if item.get("store_id") == "store-com-id")
    assert set(duravel["integracoes"]) == {"bling"}


def test_resumo_de_manifesto_nao_colapsa_lojas_homonimas_com_store_id():
    resumo = shared_sync._shared_sync_resumo_lojas_integracoes(
        [
            {"store_id": "store-A", "nome": "Igual", "integracoes": {"bling": {"connected": True}}},
            {"store_id": "store-B", "nome": "Igual", "integracoes": {"bling": {"connected": True}}},
        ]
    )

    assert resumo["lojas"] == {"store_id:store-A", "store_id:store-B"}
    assert resumo["conectadas"] == {
        "store_id:store-A:bling",
        "store_id:store-B:bling",
    }


@pytest.mark.parametrize(
    ("rel", "conteudo"),
    [
        (
            "cadastro_produtos_lojas.csv",
            "store_id,sku,row_version,updated_at_utc,deleted_at_utc\nstore-A,SKU-1,1,2026-08-28T10:00:00Z,,EXTRA\n",
        ),
        (
            "cadastro_custos_lojas.csv",
            "store_id,loja_sync,sku,updated_at\nstore-A,Loja,SKU-1,2026-08-28T10:00:00Z,EXTRA\n",
        ),
        (
            "cadastro_produtos_lojas.csv",
            'store_id,sku,row_version,updated_at_utc,deleted_at_utc\nstore-A,"SKU sem fim,1,2026-08-28T10:00:00Z,\n',
        ),
        (
            "cadastro_custos_lojas.csv",
            'store_id,loja_sync,sku,updated_at\nstore-A,Loja,"SKU sem fim,2026-08-28T10:00:00Z\n',
        ),
    ],
)
def test_delta_csv_canonico_falha_fechado_em_linha_malformada(rel, conteudo):
    with pytest.raises(HTTPException) as erro:
        shared_sync._shared_sync_csv_delta_bytes(
            "cadastro", rel, conteudo.encode("utf-8"), set()
        )

    assert erro.value.status_code == 502
