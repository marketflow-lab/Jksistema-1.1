from pathlib import Path

import pytest

from backend.modules.context_hub.bootstrap import bootstrap_context_hub
from backend.modules.context_hub import publication
from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.journal import _recover_publish_journal
from backend.modules.context_hub.paths import _tenant_paths


@pytest.fixture
def catalog_paths(tmp_path):
    root = tmp_path / "info"
    (root / "tenant-a").mkdir(parents=True)
    bootstrap_context_hub("tenant-a", info_root=root, surface="test")
    paths = _tenant_paths("tenant-a", info_root=root)
    catalog = paths.generated_dir / "CadastroPorLoja" / "store-a" / "scope" / "Objetos"
    catalog.mkdir(parents=True)
    (catalog / "old.md").write_text("Historical source object", encoding="utf8")
    (catalog / "active.md").write_text("Manual edit to retain for recovery", encoding="utf8")
    (paths.generated_dir / "global.md").write_text("old global generation", encoding="utf8")
    curated = paths.curated_dir / "manual.md"
    curated.write_text("Editorial remains intact", encoding="utf8")
    return paths, catalog


def _candidate(paths, generation_id, content):
    snapshot = paths.generations_dir / generation_id / "70_Gerado"
    snapshot.mkdir(parents=True)
    (snapshot / "global.md").write_text(content, encoding="utf8")
    return snapshot


def test_next_generation_and_rollback_keep_catalog_history_and_manual_edits(catalog_paths):
    paths, catalog = catalog_paths
    expected = {path.name: path.read_bytes() for path in catalog.iterdir()}
    for generation, content in [("1" * 32, "new global generation"), ("2" * 32, "rolled back global generation")]:
        _candidate(paths, generation, content)
        temporary, backup = publication._swap_generated_directory(paths, generation, previous_generation_id=None)
        publication._finalize_swapped_directory(paths, temporary, backup)
        assert (paths.generated_dir / "global.md").read_text() == content
        assert {path.name: path.read_bytes() for path in catalog.iterdir()} == expected
        assert (paths.curated_dir / "manual.md").read_text() == "Editorial remains intact"


def test_interrupted_swap_recovers_catalog_and_previous_global_tree(catalog_paths):
    paths, catalog = catalog_paths
    generation = "3" * 32
    _candidate(paths, generation, "uncommitted global generation")
    publication._swap_generated_directory(paths, generation, previous_generation_id=None)
    # The directory swap completed, but the database pointer did not commit.
    _recover_publish_journal(paths)
    assert (paths.generated_dir / "global.md").read_text() == "old global generation"
    assert (catalog / "old.md").read_text() == "Historical source object"
    assert (catalog / "active.md").read_text() == "Manual edit to retain for recovery"
    assert not paths.journal_path.exists()


def test_catalog_copy_rejects_link_flag_before_reading(catalog_paths, monkeypatch):
    paths, catalog = catalog_paths
    original = Path.is_symlink
    forbidden = catalog / "old.md"
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == forbidden or original(path))
    _candidate(paths, "4" * 32, "candidate")
    with pytest.raises(ContextHubValidationError):
        publication._copy_publish_candidate(paths, "4" * 32)
    assert (paths.generated_dir / "global.md").read_text() == "old global generation"
