import io
import json
import zipfile

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from backend.services import configuracoes_drive_sync as drive_sync


@pytest.mark.parametrize(
    "relative",
    [
        "ContextVault/80_Curadoria/nota.md",
        "ContextVault/.obsidian/app.json",
        "context_hub/context_hub.db",
        "SKU/001.json",
    ],
)
def test_backup_geral_rejeita_superficies_reservadas(relative):
    assert drive_sync._drive_sync_rel_path_excluido(relative) is True


def test_restore_geral_rejeita_manifesto_com_sku(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setattr(drive_sync, "get_tenant_path", lambda _client_id: str(tmp_path / "000002"))
    monkeypatch.setattr(drive_sync, "_drive_sync_fernet", lambda _ctx: Fernet(key))

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr(
            "manifest.json",
            json.dumps({"client_id": "000002", "files": [{"relative_path": "SKU/001.json"}]}),
        )
        output.writestr("files/SKU/001.json", "{}")

    with pytest.raises(HTTPException) as exc:
        drive_sync._drive_sync_restaurar_bytes(
            {"client_id": "000002", "username": "admin"},
            Fernet(key).encrypt(archive.getvalue()),
        )

    assert exc.value.status_code == 400
    assert not (tmp_path / "000002" / "SKU" / "001.json").exists()


def test_coleta_geral_percorre_pastas_permitidas_e_pula_reservadas(tmp_path, monkeypatch):
    tenant = tmp_path / "000002"
    allowed = tenant / "cadastro" / "produtos" / "item.json"
    reserved = tenant / "ContextVault" / "80_Curadoria" / "nota.md"
    sku = tenant / "SKU" / "001.json"
    allowed.parent.mkdir(parents=True)
    reserved.parent.mkdir(parents=True)
    sku.parent.mkdir(parents=True)
    allowed.write_text('{"ok": true}', encoding="utf-8")
    reserved.write_text("# Privado", encoding="utf-8")
    sku.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(drive_sync, "get_tenant_path", lambda _client_id: str(tenant))

    entries = drive_sync._drive_sync_coletar_arquivos("000002")
    paths = {entry["relative_path"] for entry in entries}

    assert "cadastro/produtos/item.json" in paths
    assert all(not path.lower().startswith(("contextvault/", "sku/")) for path in paths)
