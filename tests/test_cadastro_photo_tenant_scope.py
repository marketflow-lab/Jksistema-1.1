import asyncio

import pytest
from fastapi import HTTPException

from backend.services import cadastro_fotos


def _configure_photo_root(monkeypatch, tmp_path):
    info_root = tmp_path / "info"
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.setattr(
        cadastro_fotos,
        "get_tenant_path",
        lambda client_id: str(info_root / str(client_id)),
    )
    return info_root


def _write_photo(info_root, client_id, content):
    photo = info_root / client_id / "cadastro_fotos" / "005.png"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(content)
    return photo


def test_scoped_photo_prefers_requested_tenant(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    _write_photo(info_root, "000002", b"old-photo")
    expected = _write_photo(info_root, "000016", b"current-photo")

    response = asyncio.run(cadastro_fotos.servir_foto_cadastro("000016", "005.png"))

    assert response.path == str(expected)


def test_scoped_photo_never_falls_back_to_another_tenant(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    _write_photo(info_root, "000002", b"other-tenant-photo")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(cadastro_fotos.servir_foto_cadastro("000016", "005.png"))

    assert exc_info.value.status_code == 404


def test_scoped_photo_keeps_default_fallback(monkeypatch, tmp_path):
    info_root = _configure_photo_root(monkeypatch, tmp_path)
    expected = _write_photo(info_root, "default", b"shared-default-photo")

    response = asyncio.run(cadastro_fotos.servir_foto_cadastro("000016", "005.png"))

    assert response.path == str(expected)
