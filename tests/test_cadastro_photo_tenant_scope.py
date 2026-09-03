import asyncio
import base64
import hashlib
import inspect
import json
import os
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.params import Depends
from fastapi.testclient import TestClient

from backend.services import admin_usuarios_common, cadastro_fotos, cadastro_tenant_trust
from backend.services.cadastro_fotos_coordenacao import CadastroFotosCoordenacaoErro


def test_photo_read_handlers_require_tenant_dependency():
    for handler in (
        cadastro_fotos.servir_foto_cadastro,
        cadastro_fotos.servir_foto_cadastro_por_arquivo,
    ):
        dependency = inspect.signature(handler).parameters["client_id_sessao"].default
        assert isinstance(dependency, Depends)
        assert callable(dependency.dependency)


def test_photo_routes_enforce_permissions_after_runtime_configuration(
    monkeypatch,
    tmp_path,
):
    dependency_before_runtime = cadastro_fotos.get_tenant_id
    for handler, parameter in (
        (cadastro_fotos.upload_foto_cadastro, "client_id"),
        (cadastro_fotos.servir_foto_cadastro, "client_id_sessao"),
        (cadastro_fotos.servir_foto_cadastro_por_arquivo, "client_id_sessao"),
    ):
        captured = inspect.signature(handler).parameters[parameter].default
        assert isinstance(captured, Depends)
        assert captured.dependency is dependency_before_runtime

    info_root = tmp_path / "info"
    photo = info_root / "tenant-a" / "cadastro_fotos" / "005.png"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"tenant-a-photo")

    async def runtime_get_tenant_id(
        request,
        authorization: str | None = Header(default=None),
    ):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Token ausente ou invalido.")
        permission = authorization.removeprefix("Bearer ").strip()
        permissions = {"full": True} if permission == "full" else {permission: True}
        required = admin_usuarios_common._permissao_exigida_por_rota(
            request.url.path,
            request.method,
        )
        if required and not admin_usuarios_common._permissoes_autorizam_rota(
            permissions,
            required,
        ):
            raise HTTPException(status_code=403, detail="Permissao insuficiente.")
        return "tenant-a"

    runtime = SimpleNamespace(
        get_tenant_id=runtime_get_tenant_id,
        get_tenant_path=lambda client_id: str(info_root / str(client_id)),
    )
    monkeypatch.setattr(cadastro_fotos, "_runtime_get_tenant_id", None)
    monkeypatch.setattr(
        cadastro_fotos,
        "_runtime",
        getattr(cadastro_fotos, "_runtime", None),
        raising=False,
    )
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", cadastro_fotos.get_tenant_path)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(info_root), raising=False)
    cadastro_fotos._configure_runtime_globals(vars(cadastro_fotos), runtime)

    assert cadastro_fotos.get_tenant_id is dependency_before_runtime

    app = FastAPI()
    app.add_api_route(
        "/api/cadastro/foto/{client_id}/{filename:path}",
        cadastro_fotos.servir_foto_cadastro,
        methods=["GET"],
    )
    app.add_api_route(
        "/api/cadastro/foto-arquivo/{filename:path}",
        cadastro_fotos.servir_foto_cadastro_por_arquivo,
        methods=["GET"],
    )
    app.add_api_route(
        "/api/cadastro/foto/upload",
        cadastro_fotos.upload_foto_cadastro,
        methods=["POST"],
    )

    with TestClient(app) as client:
        missing_token = client.get("/api/cadastro/foto-arquivo/005.png")
        assert missing_token.status_code == 401

        for permission in (
            *admin_usuarios_common.CADASTRO_PHOTO_READ_PERMISSIONS,
            "full",
        ):
            authenticated = client.get(
                "/api/cadastro/foto-arquivo/005.png",
                headers={"Authorization": f"Bearer {permission}"},
            )
            assert authenticated.status_code == 200
            assert authenticated.content == b"tenant-a-photo"

        forbidden_module = client.get(
            "/api/cadastro/foto-arquivo/005.png",
            headers={"Authorization": "Bearer estoque"},
        )
        assert forbidden_module.status_code == 403

        missing_photo = client.get(
            "/api/cadastro/foto-arquivo/999.png",
            headers={"Authorization": "Bearer medias_compras"},
        )
        assert missing_photo.status_code == 404

        other_tenant = client.get(
            "/api/cadastro/foto/tenant-b/005.png",
            headers={"Authorization": "Bearer medias_compras"},
        )
        assert other_tenant.status_code == 403

        upload_from_medias = client.post(
            "/api/cadastro/foto/upload",
            headers={"Authorization": "Bearer medias_compras"},
            data={"sku": "005"},
            files={"arquivo": ("005.png", b"not-used", "image/png")},
        )
        assert upload_from_medias.status_code == 403


def test_photo_route_permissions_cover_consumers_without_opening_uploads():
    expected = (
        "cadastro",
        "medias_compras",
        "vendas",
        "importacoes",
        "perguntas_pos_venda",
    )

    for method in ("GET", "HEAD"):
        assert admin_usuarios_common._permissao_exigida_por_rota(
            "/api/cadastro/foto-arquivo/005.png",
            method,
        ) == expected
        assert admin_usuarios_common._permissao_exigida_por_rota(
            "/api/cadastro/foto/tenant-a/005.png",
            method,
        ) == expected

    for permission in expected:
        assert admin_usuarios_common._permissoes_autorizam_rota(
            {permission: True},
            expected,
        )
    assert not admin_usuarios_common._permissoes_autorizam_rota(
        {"estoque": True},
        expected,
    )
    assert admin_usuarios_common._permissao_exigida_por_rota(
        "/api/cadastro/foto/upload",
        "POST",
    ) == "cadastro"
    assert admin_usuarios_common._permissao_exigida_por_rota(
        "/api/cadastro/foto-upload",
        "POST",
    ) == "cadastro"
    assert admin_usuarios_common._permissao_exigida_por_rota(
        "/api/cadastro/produtos",
        "GET",
    ) == "cadastro"
def test_photo_routes_resolve_auth_runtime_configured_after_dependency_capture(monkeypatch):
    dependencies = [
        inspect.signature(handler).parameters["client_id_sessao"].default.dependency
        for handler in (
            cadastro_fotos.servir_foto_cadastro,
            cadastro_fotos.servir_foto_cadastro_por_arquivo,
        )
    ]
    assert dependencies == [cadastro_fotos.get_tenant_id, cadastro_fotos.get_tenant_id]

    request = object()
    chamadas = []

    async def runtime_get_tenant_id(received_request, authorization):
        chamadas.append((received_request, authorization))
        return "cliente-configurado"

    runtime = SimpleNamespace(
        get_tenant_id=runtime_get_tenant_id,
        get_tenant_path=lambda client_id: f"tenant/{client_id}",
    )
    monkeypatch.setattr(cadastro_fotos, "_runtime_get_tenant_id", None)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", cadastro_fotos.get_tenant_path)
    monkeypatch.setattr(
        cadastro_fotos,
        "configure_cadastro_common_runtime",
        lambda _runtime: _runtime,
    )

    cadastro_fotos.configure_cadastro_fotos_runtime(runtime)

    assert dependencies == [cadastro_fotos.get_tenant_id, cadastro_fotos.get_tenant_id]
    for dependency in dependencies:
        assert asyncio.run(dependency(request, "Bearer teste")) == "cliente-configurado"
    assert chamadas == [(request, "Bearer teste"), (request, "Bearer teste")]


def _configure_photo_root(monkeypatch, tmp_path):
    info_root = tmp_path / "info"

    def tenant_path(client_id):
        tenant = info_root / str(client_id)
        tenant.mkdir(parents=True, exist_ok=True)
        return str(tenant)

    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.setattr(
        cadastro_fotos,
        "get_tenant_path",
        tenant_path,
    )
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "store-a", "nome": "Loja A"}],
    )
    return info_root


def _write_photo(info_root, client_id, content):
    photo = info_root / client_id / "cadastro_fotos" / "005.png"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(content)
    return photo


def _write_photo_config(info_root, client_id, *, strict=True, groups=None):
    config_path = (
        info_root
        / client_id
        / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": strict,
                "shared_groups": list(groups or []),
            }
        ),
        encoding="utf-8",
    )
    return config_path


@pytest.mark.parametrize(
    ("error_code", "expected_code"),
    [
        ("locked", "cadastro_photo_transition_busy"),
        ("unsafe_tenant", "cadastro_photo_transition_unavailable"),
        ("lock_unsafe", "cadastro_photo_transition_unavailable"),
        ("lock_unavailable", "cadastro_photo_transition_unavailable"),
    ],
)
def test_bloqueio_de_fotos_distingue_contenda_de_indisponibilidade(
    monkeypatch,
    tmp_path,
    error_code,
    expected_code,
):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    tenant = info_root / "000002"
    tenant.mkdir(parents=True, exist_ok=True)

    class _FalhaCoordenacao:
        def __enter__(self):
            raise CadastroFotosCoordenacaoErro(
                error_code,
                "Falha de coordenacao simulada.",
            )

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        cadastro_fotos,
        "bloquear_transicao_fotos_tenant",
        lambda *_args, **_kwargs: _FalhaCoordenacao(),
    )

    with pytest.raises(HTTPException) as exc_info:
        with cadastro_fotos._cadastro_fotos_bloquear_transicao("000002"):
            raise AssertionError("o corpo nao deve ser executado")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == expected_code


def _configure_shared_photo_group(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    store_ids = ["store-a", "store-b", "store-c", "store-isolada"]
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": store_id} for store_id in store_ids],
    )
    _write_photo_config(
        info_root,
        "000002",
        groups=[
            {
                "group_id": "fotos-compartilhadas-abc",
                "store_ids": store_ids[:3],
            }
        ],
    )
    return info_root, store_ids


def _shared_photo_paths(info_root, sku, store_ids):
    return {
        store_id: (
            info_root
            / "000002"
            / "cadastro_fotos"
            / "lojas"
            / cadastro_fotos._cadastro_store_id_foto_segmento(store_id)
            / f"{sku}.png"
        )
        for store_id in store_ids
    }


def _create_directory_reparse(link_path, target_path):
    target_path.mkdir(parents=True, exist_ok=True)
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link_path), str(target_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.fail(f"Nao foi possivel criar junction de teste: {result.stderr}")
        is_junction = getattr(link_path, "is_junction", None)
        assert callable(is_junction) and is_junction()
        return

    try:
        link_path.symlink_to(target_path, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symlink de diretorio indisponivel: {exc}")
    assert link_path.is_symlink()


def _remove_directory_reparse(link_path):
    if not os.path.lexists(link_path):
        return
    is_junction = getattr(link_path, "is_junction", None)
    if os.name == "nt" and callable(is_junction) and is_junction():
        os.rmdir(link_path)
    else:
        link_path.unlink()


def _snapshot_files(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_scoped_photo_prefers_requested_tenant(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    _write_photo(info_root, "000002", b"old-photo")
    expected = _write_photo(info_root, "000016", b"current-photo")

    response = asyncio.run(
        cadastro_fotos.servir_foto_cadastro("000016", "005.png", "000016")
    )

    assert response.path == str(expected)


def test_scoped_photo_never_falls_back_to_another_tenant(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    _write_photo(info_root, "000002", b"other-tenant-photo")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(cadastro_fotos.servir_foto_cadastro("000016", "005.png", "000016"))

    assert exc_info.value.status_code == 404


def test_scoped_photo_keeps_default_fallback(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    expected = _write_photo(info_root, "default", b"shared-default-photo")

    response = asyncio.run(
        cadastro_fotos.servir_foto_cadastro("000016", "005.png", "000016")
    )

    assert response.path == str(expected)


def test_strict_store_config_never_falls_back_to_tenant_root_or_default(
    monkeypatch,
    tmp_path,
):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    _write_photo_config(info_root, "000002", strict=True)
    _write_photo(info_root, "000002", b"tenant-root")
    _write_photo(info_root, "default", b"default-root")

    assert cadastro_fotos._cadastro_mapa_fotos_locais("000002", "store-a") == {}
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_fotos.servir_foto_cadastro(
                "000002",
                "005.png",
                "000002",
                "store-a",
            )
        )

    assert exc_info.value.status_code == 404


def test_strict_store_config_rejects_unscoped_writes_before_reading_upload(
    monkeypatch,
    tmp_path,
):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    _write_photo_config(info_root, "000002", strict=True)
    payload = "data:image/png;base64," + base64.b64encode(b"nova").decode("ascii")

    assert cadastro_fotos._salvar_foto_data_url_no_tenant("000002", "005", "") == ""

    with pytest.raises(HTTPException) as helper_exc:
        cadastro_fotos._salvar_foto_data_url_no_tenant("000002", "005", payload)

    class UploadProbe:
        filename = "005.png"
        content_type = "image/png"
        read_called = False

        async def read(self):
            self.read_called = True
            return b"nao-deve-ser-lido"

    upload = UploadProbe()
    with pytest.raises(HTTPException) as route_exc:
        asyncio.run(cadastro_fotos.upload_foto_cadastro("005", upload, "000002"))

    assert helper_exc.value.status_code == 409
    assert helper_exc.value.detail["code"] == "store_id_required"
    assert route_exc.value.status_code == 409
    assert route_exc.value.detail["code"] == "store_id_required"
    assert upload.read_called is False
    assert not (info_root / "000002" / "cadastro_fotos" / "005.png").exists()


def test_unsafe_config_entry_fails_closed_instead_of_enabling_legacy_fallback(
    monkeypatch,
    tmp_path,
):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    config_path = (
        info_root / "000002" / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO
    )
    config_path.mkdir(parents=True)
    _write_photo(info_root, "000002", b"tenant-root")
    _write_photo(info_root, "default", b"default-root")

    assert cadastro_fotos._cadastro_mapa_fotos_locais("000002", "store-a") == {}
    with pytest.raises(HTTPException) as read_exc:
        asyncio.run(
            cadastro_fotos.servir_foto_cadastro(
                "000002",
                "005.png",
                "000002",
                "store-a",
            )
        )
    with pytest.raises(HTTPException) as write_exc:
        cadastro_fotos._salvar_foto_data_url_no_tenant(
            "000002",
            "005",
            "data:image/png;base64," + base64.b64encode(b"nova").decode("ascii"),
            store_id="store-a",
        )

    assert read_exc.value.status_code == 404
    assert write_exc.value.status_code == 409
    assert write_exc.value.detail["code"] == "cadastro_photo_config_invalid"


def test_symlinked_config_fails_closed_instead_of_enabling_legacy_fallback(
    monkeypatch,
    tmp_path,
):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    tenant_path = info_root / "000002"
    tenant_path.mkdir(parents=True, exist_ok=True)
    external_config = tmp_path / "outside-config.json"
    external_config.write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": False,
                "shared_groups": [],
            }
        ),
        encoding="utf-8",
    )
    config_path = tenant_path / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO
    try:
        config_path.symlink_to(external_config)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symlink de arquivo indisponivel: {exc}")
    _write_photo(info_root, "000002", b"tenant-root")
    _write_photo(info_root, "default", b"default-root")
    before = external_config.read_bytes()

    assert config_path.is_symlink()
    assert cadastro_fotos._cadastro_mapa_fotos_locais("000002", "store-a") == {}
    with pytest.raises(HTTPException) as read_exc:
        asyncio.run(
            cadastro_fotos.servir_foto_cadastro(
                "000002",
                "005.png",
                "000002",
                "store-a",
            )
        )
    with pytest.raises(HTTPException) as write_exc:
        cadastro_fotos._salvar_foto_data_url_no_tenant(
            "000002",
            "005",
            "data:image/png;base64," + base64.b64encode(b"nova").decode("ascii"),
            store_id="store-a",
        )

    assert read_exc.value.status_code == 404
    assert write_exc.value.status_code == 409
    assert write_exc.value.detail["code"] == "cadastro_photo_config_invalid"
    assert external_config.read_bytes() == before


@pytest.mark.parametrize(
    "reparse_component",
    ["cadastro_fotos", "lojas", "hashed_store"],
)
def test_store_photo_runtime_rejects_reparse_directories_without_touching_target(
    monkeypatch,
    tmp_path,
    reparse_component,
):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    _write_photo_config(info_root, "000002", strict=True)
    tenant_path = info_root / "000002"
    segment = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    outside = tmp_path / f"outside-{reparse_component}"

    if reparse_component == "cadastro_fotos":
        link_path = tenant_path / "cadastro_fotos"
        outside_photo = outside / "lojas" / segment / "005.png"
    elif reparse_component == "lojas":
        link_path = tenant_path / "cadastro_fotos" / "lojas"
        outside_photo = outside / segment / "005.png"
    else:
        link_path = tenant_path / "cadastro_fotos" / "lojas" / segment
        outside_photo = outside / "005.png"

    outside_photo.parent.mkdir(parents=True, exist_ok=True)
    outside_photo.write_bytes(b"outside-tenant-photo")
    before = _snapshot_files(outside)
    _create_directory_reparse(link_path, outside)
    try:
        assert cadastro_fotos._cadastro_mapa_fotos_locais("000002", "store-a") == {}
        with pytest.raises(HTTPException) as read_exc:
            asyncio.run(
                cadastro_fotos.servir_foto_cadastro(
                    "000002",
                    f"lojas/{segment}/005.png",
                    "000002",
                    "store-a",
                )
            )
        with pytest.raises(HTTPException) as write_exc:
            cadastro_fotos._salvar_foto_data_url_no_tenant(
                "000002",
                "SKU-NEW",
                "data:image/png;base64," + base64.b64encode(b"nova").decode("ascii"),
                store_id="store-a",
            )

        assert read_exc.value.status_code == 404
        assert write_exc.value.status_code == 409
        assert _snapshot_files(outside) == before
    finally:
        _remove_directory_reparse(link_path)


def test_tenant_root_reparse_to_different_client_fails_closed(
    monkeypatch,
    tmp_path,
):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    target_tenant = info_root / "000003"
    _write_photo_config(info_root, "000003", strict=False)
    segment = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    outside_photo = (
        target_tenant / "cadastro_fotos" / "lojas" / segment / "005.png"
    )
    outside_photo.parent.mkdir(parents=True, exist_ok=True)
    outside_photo.write_bytes(b"foto-de-outro-cliente")
    before = _snapshot_files(target_tenant)
    tenant_link = info_root / "000002"
    _create_directory_reparse(tenant_link, target_tenant)
    try:
        assert cadastro_fotos._cadastro_mapa_fotos_locais("000002", "store-a") == {}
        with pytest.raises(HTTPException) as read_exc:
            asyncio.run(
                cadastro_fotos.servir_foto_cadastro(
                    "000002",
                    f"lojas/{segment}/005.png",
                    "000002",
                    "store-a",
                )
            )
        with pytest.raises(HTTPException) as write_exc:
            cadastro_fotos._salvar_foto_data_url_no_tenant(
                "000002",
                "005",
                "data:image/png;base64," + base64.b64encode(b"nova").decode("ascii"),
                store_id="store-a",
            )

        assert read_exc.value.status_code == 404
        assert write_exc.value.status_code == 409
        assert _snapshot_files(target_tenant) == before
    finally:
        _remove_directory_reparse(tenant_link)


def test_info_root_below_reparse_ancestor_fails_closed_without_touching_target(
    monkeypatch,
    tmp_path,
):
    canonical_parent = tmp_path / "canonical-parent"
    canonical_info = canonical_parent / "info"
    _write_photo_config(canonical_info, "000002", strict=True)
    segment = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    canonical_photo = (
        canonical_info
        / "000002"
        / "cadastro_fotos"
        / "lojas"
        / segment
        / "005.png"
    )
    canonical_photo.parent.mkdir(parents=True, exist_ok=True)
    canonical_photo.write_bytes(b"nao-tocar")
    before = _snapshot_files(canonical_parent)

    alias_parent = tmp_path / "alias-parent"
    _create_directory_reparse(alias_parent, canonical_parent)
    alias_info = alias_parent / "info"
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(alias_info), raising=False)
    monkeypatch.setattr(
        cadastro_fotos,
        "get_tenant_path",
        lambda client_id: str(alias_info / str(client_id)),
    )
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "store-a", "nome": "Loja A"}],
    )
    try:
        assert cadastro_fotos._cadastro_mapa_fotos_locais("000002", "store-a") == {}
        with pytest.raises(HTTPException) as read_exc:
            asyncio.run(
                cadastro_fotos.servir_foto_cadastro(
                    "000002",
                    f"lojas/{segment}/005.png",
                    "000002",
                    "store-a",
                )
            )
        with pytest.raises(HTTPException) as write_exc:
            cadastro_fotos._salvar_foto_data_url_no_tenant(
                "000002",
                "005",
                "data:image/png;base64," + base64.b64encode(b"nova").decode("ascii"),
                store_id="store-a",
            )

        assert read_exc.value.status_code == 404
        assert write_exc.value.status_code == 409
        assert _snapshot_files(canonical_parent) == before
    finally:
        _remove_directory_reparse(alias_parent)


def test_tenant_root_reparse_to_same_canonical_client_requires_local_trust(
    monkeypatch,
    tmp_path,
):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    canonical_root = tmp_path / "canonical"
    canonical_tenant = canonical_root / "000002"
    canonical_tenant.mkdir(parents=True)
    (canonical_tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [],
            }
        ),
        encoding="utf-8",
    )
    tenant_link = info_root / "000002"
    tenant_link.parent.mkdir(parents=True, exist_ok=True)
    _create_directory_reparse(tenant_link, canonical_tenant)
    try:
        with pytest.raises(HTTPException) as untrusted_exc:
            cadastro_fotos._salvar_foto_data_url_no_tenant(
                "000002",
                "005",
                "data:image/png;base64," + base64.b64encode(b"nao-confiavel").decode("ascii"),
                store_id="store-a",
            )
        assert untrusted_exc.value.status_code == 409

        cadastro_tenant_trust.registrar_alias_tenant(
            info_root, canonical_root, "000002"
        )
        relative = cadastro_fotos._salvar_foto_data_url_no_tenant(
            "000002",
            "005",
            "data:image/png;base64," + base64.b64encode(b"canonica").decode("ascii"),
            store_id="store-a",
        )

        assert (canonical_tenant / relative).read_bytes() == b"canonica"
        assert cadastro_fotos._cadastro_resolver_foto_local(
            cadastro_fotos._cadastro_mapa_fotos_locais("000002", "store-a"),
            "005",
        ) == relative
    finally:
        _remove_directory_reparse(tenant_link)


def test_registered_tenant_alias_retarget_blocks_photo_read_and_write(
    monkeypatch,
    tmp_path,
):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    canonical_root = tmp_path / "canonical"
    canonical_tenant = canonical_root / "000002"
    _write_photo_config(canonical_root, "000002", strict=True)
    tenant_link = info_root / "000002"
    tenant_link.parent.mkdir(parents=True, exist_ok=True)
    _create_directory_reparse(tenant_link, canonical_tenant)
    cadastro_tenant_trust.registrar_alias_tenant(
        info_root, canonical_root, "000002"
    )

    other_tenant = tmp_path / "other" / "000002"
    segment = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    other_photo = other_tenant / "cadastro_fotos" / "lojas" / segment / "005.png"
    other_photo.parent.mkdir(parents=True, exist_ok=True)
    other_photo.write_bytes(b"nao-autorizada")
    (other_tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [],
            }
        ),
        encoding="utf-8",
    )
    _remove_directory_reparse(tenant_link)
    _create_directory_reparse(tenant_link, other_tenant)
    before = _snapshot_files(other_tenant)
    try:
        with pytest.raises(HTTPException) as read_error:
            asyncio.run(
                cadastro_fotos.servir_foto_cadastro(
                    "000002",
                    f"lojas/{segment}/005.png",
                    "000002",
                    "store-a",
                )
            )
        with pytest.raises(HTTPException) as write_error:
            cadastro_fotos._salvar_foto_data_url_no_tenant(
                "000002",
                "005",
                "data:image/png;base64," + base64.b64encode(b"nova").decode("ascii"),
                store_id="store-a",
            )

        assert read_error.value.status_code == 404
        assert write_error.value.status_code == 409
        assert _snapshot_files(other_tenant) == before
    finally:
        _remove_directory_reparse(tenant_link)


def test_invalid_store_config_fails_closed_for_reads_and_writes(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    config_path = (
        info_root
        / "000002"
        / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": False,
                "shared_groups": [
                    {
                        "group_id": "grupo-invalido",
                        "store_ids": ["store-a", "store-inexistente"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _write_photo(info_root, "000002", b"tenant-root")
    _write_photo(info_root, "default", b"default-root")

    assert cadastro_fotos._cadastro_mapa_fotos_locais("000002", "store-a") == {}
    with pytest.raises(HTTPException) as read_exc:
        asyncio.run(
            cadastro_fotos.servir_foto_cadastro(
                "000002",
                "005.png",
                "000002",
                "store-a",
            )
        )
    with pytest.raises(HTTPException) as write_exc:
        cadastro_fotos._salvar_foto_data_url_no_tenant(
            "000002",
            "005",
            "data:image/png;base64," + base64.b64encode(b"nova").decode("ascii"),
            store_id="store-a",
        )

    assert read_exc.value.status_code == 404
    assert write_exc.value.status_code == 409
    assert write_exc.value.detail["code"] == "cadastro_photo_config_invalid"
    segment = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    assert not (
        info_root
        / "000002"
        / "cadastro_fotos"
        / "lojas"
        / segment
        / "005.png"
    ).exists()


def test_shared_group_fans_out_identical_bytes_to_hashed_store_paths(
    monkeypatch,
    tmp_path,
):
    info_root, store_ids = _configure_shared_photo_group(monkeypatch, tmp_path)
    payload = "data:image/png;base64," + base64.b64encode(b"foto-compartilhada").decode(
        "ascii"
    )

    relative = cadastro_fotos._salvar_foto_data_url_no_tenant(
        "000002",
        "SKU-ABC",
        payload,
        store_id="store-a",
    )
    group_paths = _shared_photo_paths(info_root, "SKU-ABC", store_ids[:3])

    assert relative == (
        "cadastro_fotos/lojas/"
        f"{cadastro_fotos._cadastro_store_id_foto_segmento('store-a')}/SKU-ABC.png"
    )
    assert all(path.read_bytes() == b"foto-compartilhada" for path in group_paths.values())
    assert len({path.parent.name for path in group_paths.values()}) == 3


def test_updating_photo_from_any_shared_member_replicates_to_entire_group(
    monkeypatch,
    tmp_path,
):
    info_root, store_ids = _configure_shared_photo_group(monkeypatch, tmp_path)
    group_paths = _shared_photo_paths(info_root, "SKU-UPDATE", store_ids[:3])

    for member in store_ids[:3]:
        expected = f"foto-de-{member}".encode("utf-8")
        payload = "data:image/png;base64," + base64.b64encode(expected).decode("ascii")

        relative = cadastro_fotos._salvar_foto_data_url_no_tenant(
            "000002",
            "SKU-UPDATE",
            payload,
            store_id=member,
        )

        assert relative == (
            "cadastro_fotos/lojas/"
            f"{cadastro_fotos._cadastro_store_id_foto_segmento(member)}/SKU-UPDATE.png"
        )
        assert all(path.read_bytes() == expected for path in group_paths.values())


def test_shared_group_extension_change_removes_old_variant_in_every_store(
    monkeypatch,
    tmp_path,
):
    info_root, store_ids = _configure_shared_photo_group(monkeypatch, tmp_path)
    png_paths = _shared_photo_paths(info_root, "SKU-EXT", store_ids[:3])
    cadastro_fotos._salvar_foto_data_url_no_tenant(
        "000002",
        "SKU-EXT",
        "data:image/png;base64," + base64.b64encode(b"png-antiga").decode("ascii"),
        store_id="store-a",
    )

    relative = cadastro_fotos._salvar_foto_data_url_no_tenant(
        "000002",
        "SKU-EXT",
        "data:image/jpeg;base64," + base64.b64encode(b"jpg-nova").decode("ascii"),
        store_id="store-b",
    )
    jpg_paths = {store_id: path.with_suffix(".jpg") for store_id, path in png_paths.items()}

    assert relative.endswith("/SKU-EXT.jpg")
    assert all(not path.exists() for path in png_paths.values())
    assert all(path.read_bytes() == b"jpg-nova" for path in jpg_paths.values())


@pytest.mark.parametrize("failure_write", [2, 3])
def test_shared_group_write_failure_rolls_back_every_replica(
    monkeypatch,
    tmp_path,
    failure_write,
):
    info_root, store_ids = _configure_shared_photo_group(monkeypatch, tmp_path)
    old_payload = "data:image/png;base64," + base64.b64encode(b"foto-anterior").decode(
        "ascii"
    )
    cadastro_fotos._salvar_foto_data_url_no_tenant(
        "000002",
        "SKU-ROLLBACK",
        old_payload,
        store_id="store-a",
    )
    group_paths = _shared_photo_paths(info_root, "SKU-ROLLBACK", store_ids[:3])
    before = {store_id: path.read_bytes() for store_id, path in group_paths.items()}
    real_save = cadastro_fotos._salvar_foto_preparada_atomico
    calls = 0

    def fail_during_fanout(prepared):
        nonlocal calls
        calls += 1
        if calls == failure_write:
            raise OSError(f"falha simulada na gravacao {failure_write}")
        return real_save(prepared)

    monkeypatch.setattr(
        cadastro_fotos,
        "_salvar_foto_preparada_atomico",
        fail_during_fanout,
    )
    new_payload = "data:image/png;base64," + base64.b64encode(b"foto-incompleta").decode(
        "ascii"
    )

    with pytest.raises(OSError, match=f"gravacao {failure_write}"):
        cadastro_fotos._salvar_foto_data_url_no_tenant(
            "000002",
            "SKU-ROLLBACK",
            new_payload,
            store_id="store-a",
        )

    assert {store_id: path.read_bytes() for store_id, path in group_paths.items()} == before


def test_store_outside_shared_group_remains_isolated(monkeypatch, tmp_path):
    info_root, store_ids = _configure_shared_photo_group(monkeypatch, tmp_path)
    group_paths = _shared_photo_paths(info_root, "SKU-ISOLADO", store_ids[:3])
    isolated_path = _shared_photo_paths(
        info_root,
        "SKU-ISOLADO",
        ["store-isolada"],
    )["store-isolada"]

    cadastro_fotos._salvar_foto_data_url_no_tenant(
        "000002",
        "SKU-ISOLADO",
        "data:image/png;base64," + base64.b64encode(b"grupo").decode("ascii"),
        store_id="store-a",
    )

    assert not isolated_path.exists()
    cadastro_fotos._salvar_foto_data_url_no_tenant(
        "000002",
        "SKU-ISOLADO",
        "data:image/png;base64," + base64.b64encode(b"isolada").decode("ascii"),
        store_id="store-isolada",
    )
    assert isolated_path.read_bytes() == b"isolada"
    assert all(path.read_bytes() == b"grupo" for path in group_paths.values())


def test_store_scoped_photos_do_not_collide(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    payload_a = "data:image/png;base64," + base64.b64encode(b"store-a").decode("ascii")
    payload_b = "data:image/png;base64," + base64.b64encode(b"store-b").decode("ascii")

    relative_a = cadastro_fotos._salvar_foto_data_url_no_tenant(
        "000002", "005", payload_a, store_id="store-a"
    )
    relative_b = cadastro_fotos._salvar_foto_data_url_no_tenant(
        "000002", "005", payload_b, store_id="store-b"
    )

    segment_a = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    segment_b = cadastro_fotos._cadastro_store_id_foto_segmento("store-b")
    assert relative_a == f"cadastro_fotos/lojas/{segment_a}/005.png"
    assert relative_b == f"cadastro_fotos/lojas/{segment_b}/005.png"
    assert (info_root / "000002" / relative_a).read_bytes() == b"store-a"
    assert (info_root / "000002" / relative_b).read_bytes() == b"store-b"


def test_store_ids_case_equivalentes_e_com_ponto_espaco_usam_segmentos_distintos(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    store_ids = ["StoreA", "storea", "Loja . Centro"]
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": store_id} for store_id in store_ids],
    )

    relatives = {}
    for store_id in store_ids:
        payload = "data:image/png;base64," + base64.b64encode(store_id.encode("utf-8")).decode("ascii")
        relatives[store_id] = cadastro_fotos._salvar_foto_data_url_no_tenant(
            "000002", "005", payload, store_id=store_id,
        )

    assert len(set(relatives.values())) == len(store_ids)
    for store_id, relative in relatives.items():
        segmento = cadastro_fotos._cadastro_store_id_foto_segmento(store_id)
        assert segmento == "sid-" + hashlib.sha256(store_id.encode("utf-8")).hexdigest()
        assert relative == f"cadastro_fotos/lojas/{segmento}/005.png"
        assert (info_root / "000002" / relative).read_bytes() == store_id.encode("utf-8")


def test_store_photo_hashed_serve_mapeia_segmento_para_store_exata(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    stores = ["StoreA", "storea"]
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": store_id} for store_id in stores],
    )
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("StoreA")
    expected = info_root / "000002" / "cadastro_fotos" / "lojas" / segmento / "005.png"
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"StoreA-photo")

    response = asyncio.run(cadastro_fotos.servir_foto_cadastro(
        "000002", f"lojas/{segmento}/005.png", "000002", "StoreA",
    ))

    assert response.path == str(expected)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(cadastro_fotos.servir_foto_cadastro(
            "000002", f"lojas/{segmento}/005.png", "000002", "storea",
        ))
    assert exc_info.value.status_code == 404


def test_store_photo_hashed_never_falls_back_to_default(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    default_photo = (
        info_root
        / "default"
        / "cadastro_fotos"
        / "lojas"
        / segmento
        / "005.png"
    )
    default_photo.parent.mkdir(parents=True)
    default_photo.write_bytes(b"wrong-scope")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_fotos.servir_foto_cadastro(
                "000002",
                f"lojas/{segmento}/005.png",
                "000002",
                "store-a",
            )
        )

    assert exc_info.value.status_code == 404


def test_absolute_local_photo_api_url_preserves_exact_store_scope(monkeypatch, tmp_path):
    _configure_photo_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "store-a"}, {"store_id": "store-b"}],
    )
    segmento_a = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    segmento_b = cadastro_fotos._cadastro_store_id_foto_segmento("store-b")
    url_a = f"https://jk.local/api/cadastro/foto-arquivo/lojas/{segmento_a}/005.png?rev=1"
    url_b = f"https://jk.local/api/cadastro/foto-arquivo/lojas/{segmento_b}/005.png#foto"
    url_b_codificada = (
        "https://jk.local/api/cadastro/foto-arquivo/"
        f"%6Co%6Aas/{segmento_b}/005.png"
    )

    assert cadastro_fotos._cadastro_foto_referencia_pertence_loja(
        "000002", "store-a", url_a,
    )
    assert not cadastro_fotos._cadastro_foto_referencia_pertence_loja(
        "000002", "store-a", url_b,
    )
    assert not cadastro_fotos._cadastro_foto_referencia_pertence_loja(
        "000002", "store-a", url_b_codificada,
    )


def test_tenant_photo_api_url_rejects_other_tenant_with_same_store(monkeypatch, tmp_path):
    _configure_photo_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "store-a"}],
    )
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    mesma_loja = f"lojas/{segmento}/005.png"

    assert cadastro_fotos._cadastro_foto_referencia_pertence_loja(
        "000002",
        "store-a",
        f"/api/cadastro/foto/000002/{mesma_loja}",
    )
    assert not cadastro_fotos._cadastro_foto_referencia_pertence_loja(
        "000002",
        "store-a",
        f"/api/cadastro/foto/000003/{mesma_loja}",
    )
    assert not cadastro_fotos._cadastro_foto_referencia_pertence_loja(
        "000002",
        "store-a",
        f"https://jk.local/api/cadastro/foto/000003/{mesma_loja}?rev=1",
    )


def test_truly_external_photo_url_remains_allowed(monkeypatch, tmp_path):
    _configure_photo_root(monkeypatch, tmp_path)

    assert cadastro_fotos._cadastro_foto_referencia_pertence_loja(
        "000002",
        "store-a",
        "https://cdn.example.test/produtos/005.png",
    )


def test_store_photo_legada_raw_ambigua_por_casefold_e_recusada(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "StoreA"}, {"store_id": "storea"}],
    )
    legacy = info_root / "000002" / "cadastro_fotos" / "lojas" / "StoreA" / "005.png"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"ambiguous")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(cadastro_fotos.servir_foto_cadastro(
            "000002", "lojas/StoreA/005.png", "000002", "StoreA",
        ))

    assert exc_info.value.status_code == 404
    assert cadastro_fotos._cadastro_resolver_foto_local(
        cadastro_fotos._cadastro_mapa_fotos_locais("000002", "StoreA"),
        "005",
    ) == ""


def test_store_scoped_photos_use_sku_instead_of_original_filename(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    payload_a = "data:image/png;base64," + base64.b64encode(b"sku-a").decode("ascii")
    payload_b = "data:image/png;base64," + base64.b64encode(b"sku-b").decode("ascii")

    relative_a = cadastro_fotos._salvar_foto_data_url_no_tenant(
        "000002", "SKU-A", payload_a, "imagem.png", store_id="store-a"
    )
    relative_b = cadastro_fotos._salvar_foto_data_url_no_tenant(
        "000002", "SKU-B", payload_b, "imagem.png", store_id="store-a"
    )

    assert relative_a.endswith("/SKU-A.png")
    assert relative_b.endswith("/SKU-B.png")
    assert relative_a != relative_b
    assert (info_root / "000002" / relative_a).read_bytes() == b"sku-a"
    assert (info_root / "000002" / relative_b).read_bytes() == b"sku-b"


def test_store_scoped_photo_names_do_not_collide_after_sanitizing_sku(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    payload_a = "data:image/png;base64," + base64.b64encode(b"slash").decode("ascii")
    payload_b = "data:image/png;base64," + base64.b64encode(b"colon").decode("ascii")

    relative_a = cadastro_fotos._salvar_foto_data_url_no_tenant(
        "000002", "A/B", payload_a, store_id="store-a"
    )
    relative_b = cadastro_fotos._salvar_foto_data_url_no_tenant(
        "000002", "A:B", payload_b, store_id="store-a"
    )

    assert relative_a != relative_b
    assert (info_root / "000002" / relative_a).read_bytes() == b"slash"
    assert (info_root / "000002" / relative_b).read_bytes() == b"colon"


def test_store_scoped_photo_serving_accepts_safe_subpath(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    expected = info_root / "000002" / "cadastro_fotos" / "lojas" / "store-a" / "005.png"
    expected.parent.mkdir(parents=True, exist_ok=True)
    expected.write_bytes(b"store-photo")

    response = asyncio.run(
        cadastro_fotos.servir_foto_cadastro(
            "000002", "lojas/store-a/005.png", "000002"
        )
    )

    assert response.path == str(expected)


def test_store_scoped_photo_serving_accepts_name_generated_from_unicode_sku(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    expected = (
        info_root
        / "000002"
        / "cadastro_fotos"
        / "lojas"
        / "store-a"
        / "SKU 1 Á.png"
    )
    expected.parent.mkdir(parents=True, exist_ok=True)
    expected.write_bytes(b"unicode-store-photo")

    response = asyncio.run(
        cadastro_fotos.servir_foto_cadastro(
            "000002", "lojas/store-a/SKU 1 Á.png", "000002"
        )
    )

    assert response.path == str(expected)


@pytest.mark.parametrize("filename", ["../005.png", "lojas/../../005.png", "lojas/store a/005.png"])
def test_store_scoped_photo_serving_rejects_unsafe_subpath(monkeypatch, tmp_path, filename):
    _configure_photo_root(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(cadastro_fotos.servir_foto_cadastro("000002", filename, "000002"))

    assert exc_info.value.status_code == 404


def test_scoped_photo_rejects_client_id_different_from_authenticated_tenant(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    _write_photo(info_root, "000002", b"other-tenant-photo")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(cadastro_fotos.servir_foto_cadastro("000002", "005.png", "000016"))

    assert exc_info.value.status_code == 403


def test_scoped_photo_requires_authenticated_tenant(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    _write_photo(info_root, "000002", b"tenant-photo")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(cadastro_fotos.servir_foto_cadastro("000002", "005.png", ""))

    assert exc_info.value.status_code == 401


def test_photo_by_filename_uses_only_authenticated_tenant(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    _write_photo(info_root, "000002", b"other-tenant-photo")
    expected = _write_photo(info_root, "000016", b"authenticated-photo")

    response = asyncio.run(
        cadastro_fotos.servir_foto_cadastro_por_arquivo("005.png", "000016")
    )

    assert response.path == str(expected)


def test_photo_by_filename_never_scans_other_tenants(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    _write_photo(info_root, "000002", b"other-tenant-photo")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(cadastro_fotos.servir_foto_cadastro_por_arquivo("005.png", "000016"))

    assert exc_info.value.status_code == 404


def test_photo_by_filename_keeps_explicit_default_fallback(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    expected = _write_photo(info_root, "default", b"shared-default-photo")

    response = asyncio.run(
        cadastro_fotos.servir_foto_cadastro_por_arquivo("005.png", "000016")
    )

    assert response.path == str(expected)


def test_store_scoped_photo_rejects_store_not_owned_by_authenticated_tenant(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    forbidden = info_root / "000002" / "cadastro_fotos" / "lojas" / "store-b" / "005.png"
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_bytes(b"unknown-store-photo")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            cadastro_fotos.servir_foto_cadastro(
                "000002", "lojas/store-b/005.png", "000002"
            )
        )

    assert exc_info.value.status_code == 404
