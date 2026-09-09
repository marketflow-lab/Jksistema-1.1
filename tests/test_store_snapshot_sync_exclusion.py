import hashlib
import io
import json
import zipfile

import pytest
from fastapi import HTTPException

from backend.services import shared_sync  # noqa: F401 - compose facade dependencies
from backend.services import shared_sync_apply_scope as apply
from backend.services import shared_sync_collect_files as collect
from backend.services import shared_sync_common as common


@pytest.mark.parametrize("relative", [
    "lojas_public_snapshot.json",
    "LOJAS_PUBLIC_SNAPSHOT.JSON",
    "nested/lojas_public_snapshot.json",
    "lojas_public_snapshot.json:stream",
    "lojas_public_snapshot.json. ",
    "lojas_public_snapshot.json.tmp.123",
    "_stores_publication/journal.json",
    "_STORES_PUBLICATION\\preimages\\stores.json",
    "nested/_stores_publication. /preimage.json",
    "context_hub/training_read_index.sqlite",
    "context_hub/training_read_index.sqlite-wal",
    "context_hub/training_read_index.sqlite-shm",
    "CONTEXT_HUB/training-index-worker.lock",
])
def test_public_projection_and_preimages_are_rejected_on_import(relative, monkeypatch, tmp_path):
    monkeypatch.setitem(common.SHARED_SYNC_SCOPES, "synthetic", {"patterns": ["*"]})
    assert common._shared_sync_path_permanently_excluded(relative)
    assert not collect._shared_sync_scope_match("synthetic", relative)
    with pytest.raises(HTTPException) as unsafe:
        common._shared_sync_resolve_tenant_path(str(tmp_path), relative)
    assert unsafe.value.status_code == 400

    data = b'{"synthetic":"never-import"}'
    manifest = {"schema": 1, "scope": "synthetic", "files": [{
        "relative_path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    }]}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("files/" + relative, data)
    with pytest.raises(HTTPException) as rejected:
        apply._shared_sync_read_validated_bundle(stream.getvalue(), "synthetic")
    assert rejected.value.status_code == 400
    assert "permanentemente excluido" in str(rejected.value.detail)


def test_export_prunes_publication_tree_even_with_wildcard_scope(tmp_path, monkeypatch):
    private = tmp_path / "_STORES_PUBLICATION"
    private.mkdir()
    (private / "preimages.json").write_text('{"private":true}', encoding="utf-8")
    (tmp_path / "LOJAS_PUBLIC_SNAPSHOT.JSON").write_text('{"derived":true}', encoding="utf-8")
    (tmp_path / "ordinary.json").write_text('{"allowed":true}', encoding="utf-8")
    index_dir = tmp_path / "context_hub"
    index_dir.mkdir()
    for name in ("training_read_index.sqlite", "training_read_index.sqlite-wal", "training_read_index.sqlite-shm"):
        (index_dir / name).write_bytes(b"private-derived-projection")
    monkeypatch.setattr(collect, "get_tenant_path", lambda _: str(tmp_path), raising=False)
    monkeypatch.setitem(common.SHARED_SYNC_SCOPES, "synthetic", {"patterns": ["*"]})
    entries, warnings = collect._shared_sync_coletar_arquivos("tenant-test", "synthetic")
    assert [entry["relative_path"] for entry in entries] == ["ordinary.json"]
    assert warnings == []
