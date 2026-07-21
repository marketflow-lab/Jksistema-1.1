import json
from pathlib import Path

from backend.services.codex_contract_snapshot import build_codex_contract_snapshot


SNAPSHOT_PATH = Path(__file__).parent / "contracts" / "codex_internal_v2.json"


def test_internal_codex_contracts_match_reviewed_snapshot():
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert build_codex_contract_snapshot() == expected


def test_contract_snapshot_freezes_security_boundaries():
    snapshot = build_codex_contract_snapshot()
    security = snapshot["contracts"]["security"]
    assert security == {
        "execution_plane": "in_app_operations",
        "development_write_enabled": False,
        "allowed_sandboxes": ["read_only"],
        "development_error_code": "DEVELOPMENT_REQUIRES_CODEX_DESKTOP",
    }
    assert snapshot["contracts"]["mcp"]["protocol"] == "mcp_v2"
    assert snapshot["contracts_sha256"]
