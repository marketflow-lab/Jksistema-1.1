import hashlib
import io
import json
import zipfile

import pytest
from fastapi import HTTPException

from backend.services import cadastro_fotos
from backend.services import integracoes
from backend.services import shared_sync  # noqa: F401 - configura a fachada
from backend.services import shared_sync_apply_scope
from backend.services import shared_sync_merge_sqlite


PHOTO_ALIASES = [
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
]

GENERIC_LINK_FIELDS = [
    "url",
    "link",
    "permalink",
    "produto_url",
    "anuncio_link",
    "callback_url",
    "manual_link",
]


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


def _configure_strict_tenant(tmp_path, monkeypatch):
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
    tenant = info_dir / "cliente-a"
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(info_dir), raising=False)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", tenant_path, raising=False)
    monkeypatch.setattr(
        shared_sync_apply_scope,
        "get_tenant_path",
        tenant_path,
        raising=False,
    )
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
    return tenant


def _apply_remote(
    *,
    receiver: str,
    scope: str,
    tenant,
    remote: bytes,
) -> None:
    if receiver == "add_only":
        shared_sync_merge_sqlite._shared_sync_aplicar_user_share_add_only(
            "cliente-a",
            scope,
            "operador",
            [("produtos_compilado.csv", remote)],
            str(tenant),
            str(tenant / "backup"),
        )
        return
    shared_sync_apply_scope._shared_sync_aplicar_pacote(
        "cliente-a",
        scope,
        _bundle(scope, [("produtos_compilado.csv", remote)]),
        "operador",
    )


@pytest.mark.parametrize("scope", ["cadastro", "vendas"])
@pytest.mark.parametrize("receiver", ["authoritative", "add_only"])
@pytest.mark.parametrize("photo_alias", PHOTO_ALIASES)
def test_produtos_compilado_strict_rejeita_foto_local_em_todo_alias_sem_escrever(
    tmp_path,
    monkeypatch,
    scope,
    receiver,
    photo_alias,
):
    tenant = _configure_strict_tenant(tmp_path, monkeypatch)
    target = tenant / "produtos_compilado.csv"
    target.write_text(
        "store_id,sku,titulo\nstore-A,LOCAL-1,Produto local\n",
        encoding="utf-8-sig",
    )
    before = target.read_bytes()
    remote = (
        f"store_id,sku,{photo_alias}\n"
        f"store-A,REMOTE-1,cadastro_fotos/REMOTE-1.jpg\n"
    ).encode("utf-8-sig")

    with pytest.raises(HTTPException) as exc_info:
        _apply_remote(
            receiver=receiver,
            scope=scope,
            tenant=tenant,
            remote=remote,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == (
        "shared_sync_compiled_local_photo_reference_forbidden"
    )
    assert exc_info.value.detail["campos"] == [photo_alias]
    assert exc_info.value.detail["skus"] == ["REMOTE-1"]
    assert target.read_bytes() == before
    backup_target = tenant / "backup" / "produtos_compilado.csv"
    assert not backup_target.exists()


@pytest.mark.parametrize("receiver", ["authoritative", "add_only"])
@pytest.mark.parametrize("generic_field", GENERIC_LINK_FIELDS)
def test_produtos_compilado_strict_nao_confunde_url_ou_link_generico_com_foto(
    tmp_path,
    monkeypatch,
    receiver,
    generic_field,
):
    tenant = _configure_strict_tenant(tmp_path, monkeypatch)
    target = tenant / "produtos_compilado.csv"
    target.write_text(
        f"store_id,sku,{generic_field}\n"
        "store-A,LOCAL-1,/produto/local.jpg\n",
        encoding="utf-8-sig",
    )
    remote = (
        f"store_id,sku,{generic_field}\n"
        "store-A,REMOTE-1,/produto/remoto.jpg\n"
    ).encode("utf-8-sig")

    _apply_remote(
        receiver=receiver,
        scope="cadastro",
        tenant=tenant,
        remote=remote,
    )

    result = target.read_text(encoding="utf-8-sig")
    assert "/produto/remoto.jpg" in result


@pytest.mark.parametrize("receiver", ["authoritative", "add_only"])
@pytest.mark.parametrize(
    "photo_ref",
    [
        "https://cdn.example/REMOTE-1.jpg",
        "//cdn.example/REMOTE-1.jpg",
        "blob:https://cdn.example/REMOTE-1.jpg",
    ],
)
def test_produtos_compilado_strict_aceita_foto_externa(
    receiver,
    photo_ref,
    tmp_path,
    monkeypatch,
):
    tenant = _configure_strict_tenant(tmp_path, monkeypatch)
    remote = (
        "store_id,sku,image_url\n"
        f"store-A,REMOTE-1,{photo_ref}\n"
    ).encode("utf-8-sig")

    _apply_remote(
        receiver=receiver,
        scope="cadastro",
        tenant=tenant,
        remote=remote,
    )

    result = (tenant / "produtos_compilado.csv").read_text(encoding="utf-8-sig")
    assert photo_ref in result
