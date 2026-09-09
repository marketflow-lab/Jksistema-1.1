from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from backend.services import store_public_snapshot as projection
from backend.services import store_snapshot_transactions as transactions
from backend.services.store_coordination import store_lock
from backend.services import integracoes, central_accounts_client
from backend.services import store_listing_service as listing


def _row(name="Before"):
    return {"nome": name, "store_id": "store-a", "integracoes": {}}


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    root = tmp_path / "tenant-a"
    root.mkdir()
    (root / "lojas_config.json").write_text(json.dumps([_row()]), encoding="utf-8")
    projection.write_snapshot(root, projection.build_snapshot([_row()]))
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(integracoes, "_get_tenant_path", lambda _client: str(root))
    monkeypatch.setattr(central_accounts_client, "current", lambda _client: None)
    monkeypatch.setattr(listing, "_ensure_initialization", lambda *_args: True)
    return root


def _publish(tenant):
    rows = json.loads((tenant / "lojas_config.json").read_text(encoding="utf-8"))
    projection.write_snapshot(tenant, projection.build_snapshot(rows))


@contextmanager
def _transaction(tenant, *, recovery="validate", publish=None, recover=None):
    with store_lock(tenant):
        with transactions.publication_transaction(tenant, publish or (lambda: _publish(tenant)),
                                                  recover or (lambda: None), recovery=recovery):
            yield


def _write(path, value):
    transactions.before_write(path)
    path.write_text(value, encoding="utf-8")


def test_nested_transaction_publishes_only_after_outer_success(tenant):
    before = projection.read_snapshot(tenant)
    with _transaction(tenant):
        with _transaction(tenant):
            _write(tenant / "lojas_config.json", json.dumps([_row("After")]))
        assert projection.read_snapshot(tenant) == before
        assert transactions.publication_pending(tenant)
    assert projection.read_snapshot(tenant)["lojas"][0]["nome"] == "After"
    assert not transactions.publication_pending(tenant)


def test_outer_failure_rolls_back_and_never_publishes_intermediate_generation(tenant):
    before = projection.read_snapshot(tenant)
    with pytest.raises(RuntimeError):
        with _transaction(tenant, recovery="rollback"):
            with _transaction(tenant):
                _write(tenant / "lojas_config.json", json.dumps([_row("Intermediate")]))
            assert projection.read_snapshot(tenant) == before
            raise RuntimeError("external stage failed")
    assert projection.read_snapshot(tenant) == before
    assert json.loads((tenant / "lojas_config.json").read_text())[0]["nome"] == "Before"
    assert not transactions.publication_pending(tenant)


def test_real_exclusion_failure_after_inner_save_keeps_previous_cards(tenant, monkeypatch):
    before = projection.read_snapshot(tenant)
    entered = []
    def failed_tombstone(*args, **kwargs):
        entered.append(True)
        assert json.loads((tenant / "lojas_config.json").read_text()) == []
        assert projection.read_snapshot(tenant) == before
        raise RuntimeError("tombstone failed")
    monkeypatch.setattr(integracoes, "registrar_tombstone_integracao", failed_tombstone)
    with pytest.raises(RuntimeError):
        integracoes.excluir_loja("tenant-a", "Before", store_id="store-a")
    assert entered
    assert projection.read_snapshot(tenant) == before
    assert json.loads((tenant / "lojas_config.json").read_text())[0]["store_id"] == "store-a"


def test_generic_failed_domain_preserves_committed_csv_sqlite_and_requires_validation(tenant):
    csv = tenant / "cadastro.csv"
    csv.write_text("old", encoding="utf-8")
    database = tenant / "state.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE status (value TEXT)")
        db.execute("INSERT INTO status VALUES ('old')")
    before = projection.read_snapshot(tenant)
    with pytest.raises(RuntimeError):
        with _transaction(tenant):
            transactions.capture_files(tenant, [csv, database])
            _write(tenant / "lojas_config.json", json.dumps([_row("Domain committed")]))
            csv.write_text("new", encoding="utf-8")
            with sqlite3.connect(database) as db:
                db.execute("UPDATE status SET value='new'")
            raise RuntimeError("later operation failed")
    assert projection.read_snapshot(tenant) == before
    assert transactions.publication_pending(tenant)
    assert csv.read_text() == "new"
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT value FROM status").fetchone()[0] == "new"
    validated = []
    with _transaction(tenant, recover=lambda: validated.append(True)):
        pass
    assert validated == [True]
    assert projection.read_snapshot(tenant)["lojas"][0]["nome"] == "Domain committed"
    assert not transactions.publication_pending(tenant)


def test_snapshot_failure_after_commit_retains_journal_and_repairs_without_repeating_business_write(tenant):
    calls = []
    def fail_publish():
        raise OSError("publication failed")
    before = projection.read_snapshot(tenant)
    with _transaction(tenant, publish=fail_publish):
        calls.append("business")
        _write(tenant / "lojas_config.json", json.dumps([_row("Committed")]))
    journal = json.loads((tenant / "_stores_publication" / "transaction.json").read_text())
    assert journal["state"] == "committed"
    assert projection.read_snapshot(tenant) == before
    with _transaction(tenant, recover=lambda: pytest.fail("Committed writes must not be rolled back")):
        pass
    assert calls == ["business"]
    assert projection.read_snapshot(tenant)["lojas"][0]["nome"] == "Committed"
    assert not transactions.publication_pending(tenant)


@pytest.mark.parametrize("recovery,expected", [("rollback", "Before"), ("validate", "After crash")])
def test_process_crash_prepared_journal_uses_declared_recovery_policy(tenant, recovery, expected):
    script = '''
import json, os, sys
from pathlib import Path
from backend.services.store_coordination import store_lock
from backend.services.store_snapshot_transactions import publication_transaction, before_write
root = Path(sys.argv[1])
with store_lock(root), publication_transaction(root, lambda: None, lambda: None, recovery=sys.argv[2]):
    path = root / "lojas_config.json"
    before_write(path)
    path.write_text(json.dumps([{"nome": "After crash", "store_id": "store-a", "integracoes": {}}]), encoding="utf-8")
    os._exit(19)
'''
    child = subprocess.run([sys.executable, "-c", script, str(tenant), recovery], capture_output=True, timeout=10)
    assert child.returncode == 19, child.stderr.decode(errors="replace")
    assert transactions.publication_pending(tenant)
    validated = []
    with _transaction(tenant, recover=lambda: validated.append(True)):
        pass
    assert validated == [True]
    assert projection.read_snapshot(tenant)["lojas"][0]["nome"] == expected
    assert not transactions.publication_pending(tenant)


def test_invalid_pending_journal_is_not_published_or_replaced(tenant):
    directory = tenant / "_stores_publication"
    directory.mkdir()
    (directory / "transaction.json").write_text('{"state":"prepared"}', encoding="utf-8")
    before = projection.read_snapshot(tenant)
    with pytest.raises(transactions.StorePublicationRecoveryError):
        with _transaction(tenant):
            pytest.fail("Invalid journal cannot enter a new write")
    assert projection.read_snapshot(tenant) == before

def test_shared_sync_failure_after_inner_commit_never_publishes_intermediate_stores(tenant, monkeypatch):
    from fastapi import HTTPException
    from backend.services import shared_sync  # Initialize the existing compatibility facade.
    from backend.services import shared_sync_apply_scope

    before = projection.read_snapshot(tenant)
    canonical_before = (tenant / "lojas_config.json").read_bytes()
    original = integracoes._integracoes_commit_lojas_tombstones
    entered = []
    def fail_after_commit(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.append(True)
        rows = json.loads((tenant / "lojas_config.json").read_text(encoding="utf-8"))
        assert any(row["store_id"] == "store-b" for row in rows)
        assert projection.read_snapshot(tenant) == before
        raise RuntimeError("external shared sync stage failed")
    monkeypatch.setattr(integracoes, "_integracoes_commit_lojas_tombstones", fail_after_commit)
    remote = [_row(), {"nome": "Remote", "store_id": "store-b", "integracoes": {}}]
    with pytest.raises(HTTPException):
        shared_sync_apply_scope._shared_sync_aplicar_lojas_integracoes(
            "tenant-a", [("lojas_config.json", json.dumps(remote).encode())],
            str(tenant), str(tenant / "_shared_sync_backups" / "test"))
    assert entered
    assert projection.read_snapshot(tenant) == before
    assert (tenant / "lojas_config.json").read_bytes() == canonical_before

@pytest.mark.parametrize("blocked_file", ["journal", "preimage"])
def test_housekeeping_failure_does_not_report_committed_operation_as_failed(tenant, monkeypatch, blocked_file):
    original_unlink = Path.unlink
    business_calls = []
    def temporarily_denied(path, *args, **kwargs):
        if path.parent.name == "_stores_publication":
            is_journal = path.name == "transaction.json"
            if (blocked_file == "journal" and is_journal) or (blocked_file == "preimage" and len(path.name) == 32):
                raise PermissionError("Temporary housekeeping denial")
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", temporarily_denied)
        with _transaction(tenant):
            business_calls.append("commit")
            _write(tenant / "lojas_config.json", json.dumps([_row("Committed despite cleanup failure")]))
    assert business_calls == ["commit"]
    assert projection.read_snapshot(tenant)["lojas"][0]["nome"] == "Committed despite cleanup failure"
    with _transaction(tenant, recover=lambda: pytest.fail("Housekeeping must not repeat business recovery")):
        _write(tenant / "lojas_config.json", json.dumps([_row("Next operation")]))
    assert projection.read_snapshot(tenant)["lojas"][0]["nome"] == "Next operation"
    assert business_calls == ["commit"]


@pytest.mark.parametrize("state", ["committed", "prepared_validate"])
def test_unneeded_preimages_do_not_block_canonical_availability(tenant, state):
    def fail_publish():
        raise OSError("Projection unavailable")
    if state == "committed":
        with _transaction(tenant, publish=fail_publish):
            _write(tenant / "lojas_config.json", json.dumps([_row("Canonical commit")]))
    else:
        with pytest.raises(RuntimeError):
            with _transaction(tenant):
                _write(tenant / "lojas_config.json", json.dumps([_row("Canonical commit")]))
                raise RuntimeError("Domain work requires validation")
    # Backups are no longer an authority for either case. Their loss must not
    # undo a successful domain write or prevent validation of its current state.
    directory = tenant / "_stores_publication"
    for preimage in directory.iterdir():
        if len(preimage.name) == 32:
            preimage.unlink()
    recovered = []
    with _transaction(tenant, recover=lambda: recovered.append(True)):
        assert json.loads((tenant / "lojas_config.json").read_text())[0]["nome"] == "Canonical commit"
        _write(tenant / "lojas_config.json", json.dumps([_row("Next confirmed operation")]))
    assert recovered == ([True] if state == "prepared_validate" else [])
    assert projection.read_snapshot(tenant)["lojas"][0]["nome"] == "Next confirmed operation"


def test_completed_recovery_is_not_repeated_when_its_projection_fails(tenant):
    script = '''
import json, os, sys
from pathlib import Path
from backend.services.store_coordination import store_lock
from backend.services.store_snapshot_transactions import publication_transaction, before_write
root = Path(sys.argv[1])
with store_lock(root), publication_transaction(root, lambda: None, lambda: None, recovery="rollback"):
    target = root / "lojas_config.json"
    before_write(target)
    target.write_text(json.dumps([{"nome": "Interrupted", "store_id": "store-a", "integracoes": {}}]))
    os._exit(19)
'''
    child = subprocess.run([sys.executable, "-c", script, str(tenant)], capture_output=True, timeout=10)
    assert child.returncode == 19, child.stderr.decode(errors="replace")
    recoveries = []
    def recover_once():
        assert json.loads((tenant / "lojas_config.json").read_text())[0]["nome"] == "Before"
        recoveries.append("recovered")
        _write(tenant / "lojas_config.json", json.dumps([_row("Recovered canonical state")]))
    def fail_publish():
        raise OSError("Projection unavailable")
    with _transaction(tenant, recover=recover_once, publish=fail_publish):
        pass
    assert recoveries == ["recovered"]
    with _transaction(tenant, recover=lambda: pytest.fail("Already completed recovery cannot repeat")):
        assert json.loads((tenant / "lojas_config.json").read_text())[0]["nome"] == "Recovered canonical state"
    assert projection.read_snapshot(tenant)["lojas"][0]["nome"] == "Recovered canonical state"
    assert recoveries == ["recovered"]


@pytest.mark.parametrize("recovery", ["validate", "rollback"])
def test_commit_marker_failure_obeys_domain_ownership_without_duplicate_write(tenant, monkeypatch, recovery):
    before = projection.read_snapshot(tenant)
    original_save = transactions._save
    operations = []
    def fail_commit_marker(root, journal):
        if journal["state"] == "committed":
            raise OSError("Commit marker disk failure")
        return original_save(root, journal)
    def operate():
        with _transaction(tenant, recovery=recovery):
            operations.append("business")
            _write(tenant / "lojas_config.json", json.dumps([_row("Domain mutation")]))
    with monkeypatch.context() as patch:
        patch.setattr(transactions, "_save", fail_commit_marker)
        if recovery == "rollback":
            with pytest.raises(OSError):
                operate()
        else:
            operate()
    assert operations == ["business"]
    assert projection.read_snapshot(tenant) == before
    canonical_name = json.loads((tenant / "lojas_config.json").read_text())[0]["nome"]
    assert canonical_name == ("Before" if recovery == "rollback" else "Domain mutation")
    assert transactions.publication_pending(tenant) is (recovery == "validate")
    validations = []
    with _transaction(tenant, recover=lambda: validations.append(True)):
        pass
    assert validations == ([True] if recovery == "validate" else [])
    assert projection.read_snapshot(tenant)["lojas"][0]["nome"] == canonical_name
    assert operations == ["business"]
