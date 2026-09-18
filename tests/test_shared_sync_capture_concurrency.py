"""A slow packer must neither retain store locks nor reopen live inputs."""
import hashlib
import io
import json
import multiprocessing
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from backend.services import shared_sync  # noqa: F401 - binds service peers only
from backend.services import cadastro_fotos, central_accounts_client, integracoes
from backend.services import shared_sync_bundle as bundle_service
from backend.services import shared_sync_collect_files as collect
from backend.services import shared_sync_delta as delta
from backend.services.store_coordination import store_lock


def _replace_in_other_process(tenant, output):
    started = time.monotonic()
    with store_lock(tenant):
        elapsed = time.monotonic() - started
        Path(tenant, "cadastro_produtos.csv").write_bytes(b"sku,nome\n1,new\n")
    output.put(elapsed)


@pytest.mark.parametrize("stage", ["hash", "delta", "zip"])
def test_packing_releases_real_process_lock_and_uses_captured_bytes(tmp_path, monkeypatch, stage):
    source = tmp_path / "cadastro_produtos.csv"
    original = b"sku,nome\n1,original\n"
    source.write_bytes(original)
    monkeypatch.setattr(collect, "get_tenant_path", lambda _: str(tmp_path), raising=False)
    monkeypatch.setattr(bundle_service, "get_tenant_path", lambda _: str(tmp_path), raising=False)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_fotos_escopo_estrito", lambda _: False)
    monkeypatch.setattr(central_accounts_client, "current", lambda _: None)

    @contextmanager
    def coordinated(_client_id):
        with store_lock(str(tmp_path)):
            yield

    monkeypatch.setattr(integracoes, "_integracoes_bloquear_rmw_lojas", coordinated)
    entered, release = threading.Event(), threading.Event()

    def pause():
        entered.set()
        assert release.wait(25), "packer was not released"

    if stage == "hash":
        real_entry = bundle_service._shared_sync_entry_from_bytes

        def paused_entry(*args, **kwargs):
            pause()
            return real_entry(*args, **kwargs)

        monkeypatch.setattr(bundle_service, "_shared_sync_entry_from_bytes", paused_entry)
    elif stage == "delta":
        real_delta = delta._shared_sync_delta_for_entry

        def paused_delta(*args, **kwargs):
            pause()
            return real_delta(*args, **kwargs)

        monkeypatch.setattr(delta, "_shared_sync_delta_for_entry", paused_delta)
    else:
        real_write = zipfile.ZipFile.writestr

        def paused_write(self, *args, **kwargs):
            pause()
            return real_write(self, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "writestr", paused_write)

    result, failures = [], []

    def pack():
        try:
            result.append(bundle_service._shared_sync_montar_pacote(
                "synthetic", "cadastro", "tester", known_keys=set() if stage == "delta" else None,
            ))
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=pack)
    worker.start()
    ctx = multiprocessing.get_context("spawn")
    output = ctx.Queue()
    writer = ctx.Process(target=_replace_in_other_process, args=(str(tmp_path), output))
    try:
        assert entered.wait(10), failures
        writer.start()
        writer.join(15)
        assert writer.exitcode == 0, "store lock stayed held during slow packaging"
        assert output.get(timeout=2) < 0.5
        assert source.read_bytes() != original
    finally:
        release.set()
        worker.join(10)
        if writer.pid and writer.is_alive():
            writer.terminate()
            writer.join(5)
        output.close()
    assert not worker.is_alive()
    assert not failures
    payload, manifest, warnings = result[0]
    assert not warnings
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        data = archive.read("files/cadastro_produtos.csv")
    assert b"original" in data and b"new" not in data
    if stage != "delta":
        assert data == original
    assert manifest["files"][0]["sha256"] == hashlib.sha256(data).hexdigest()


def test_capture_does_not_hash_live_files(tmp_path, monkeypatch):
    (tmp_path / "cadastro_produtos.csv").write_bytes(b"sku,nome\n1,original\n")
    monkeypatch.setattr(collect, "get_tenant_path", lambda _: str(tmp_path), raising=False)
    monkeypatch.setattr(collect, "_shared_sync_sha256_file", lambda _: pytest.fail("live file hashed"))
    monkeypatch.setattr(collect, "_shared_sync_bytes_sha256", lambda _: pytest.fail("hash under capture lock"))
    entries, warnings = collect._shared_sync_coletar_arquivos("synthetic", "cadastro", capture_only=True)
    assert not warnings and len(entries) == 1
    assert entries[0]["data"] == b"sku,nome\n1,original\n"
    assert "sha256" not in entries[0]


@pytest.mark.parametrize("known_keys", [None, set()])
def test_snapshot_keeps_photo_trust_and_bytes_after_live_sources_disappear(tmp_path, monkeypatch, known_keys):
    segment = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segment}/001.jpg"
    photo = tmp_path / photo_rel
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"captured-photo")
    catalog = tmp_path / "cadastro_produtos_lojas.csv"
    catalog.write_text(
        "store_id,sku,foto,row_version,updated_at_utc,deleted_at_utc\n"
        f"store-a,001,{photo_rel},1,2026-08-01T00:00:00Z,\n", encoding="utf-8-sig",
    )
    stores = tmp_path / "lojas_config.json"
    stores.write_text(json.dumps([{"store_id": "store-a", "nome": "A"}]), encoding="utf-8")
    config = tmp_path / "cadastro_fotos_config.json"
    config.write_text(json.dumps({"schema": "jk.cadastro.fotos.v1", "strict_store_scope": True, "shared_groups": []}), encoding="utf-8")
    monkeypatch.setattr(collect, "get_tenant_path", lambda _: str(tmp_path), raising=False)
    monkeypatch.setattr(bundle_service, "get_tenant_path", lambda _: str(tmp_path), raising=False)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_fotos_escopo_estrito", lambda _: True)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_carregar_lojas_foto", lambda _: [{"store_id": "store-a"}])
    monkeypatch.setattr(central_accounts_client, "current", lambda _: None)

    @contextmanager
    def coordinated(_client_id):
        with store_lock(str(tmp_path)):
            yield
        # Simulate a committed concurrent replacement/removal after capture.
        photo.unlink()
        catalog.unlink()
        stores.write_text("[]", encoding="utf-8")
        config.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(cadastro_fotos, "_cadastro_fotos_escopo_estrito", lambda _: pytest.fail("live photo config reread"))

    monkeypatch.setattr(integracoes, "_integracoes_bloquear_rmw_lojas", coordinated)
    payload, manifest, warnings = bundle_service._shared_sync_montar_pacote(
        "synthetic", "cadastro", "tester", known_keys=known_keys,
    )
    assert not warnings
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.read("files/" + photo_rel) == b"captured-photo"
        assert b"store-a" in archive.read("files/cadastro_produtos_lojas.csv")
        assert json.loads(archive.read("files/cadastro_fotos_config.json"))["strict_store_scope"] is True
        for entry in manifest["files"]:
            assert hashlib.sha256(archive.read("files/" + entry["relative_path"])).hexdigest() == entry["sha256"]
