from __future__ import annotations

import ast
from pathlib import Path

import pytest
import requests

from backend.services import text_integrity, transport_security


ROOT = Path(__file__).resolve().parents[1]
IA_SOURCE_PATHS = (
    ROOT / "backend" / "services" / "ia_common.py",
    ROOT / "backend" / "services" / "ia_providers.py",
)
OUTBOUND_PROVIDER_PATHS = tuple(
    ROOT / "backend" / "services" / name
    for name in (
        "ia_providers.py",
        "ia_web.py",
        "perguntas_pos_venda_agent.py",
        "perguntas_pos_venda_state.py",
        "favoritos_busca.py",
        "favoritos_extract.py",
        "favoritos_ranking_ia.py",
        "full_calendario.py",
    )
)


def test_utf8_strict_accepts_portuguese_and_rejects_legacy_bytes():
    expected = "Ação, NÃO e emoji válido ✅"
    assert text_integrity.decode_utf8_strict(expected.encode("utf-8")) == expected
    with pytest.raises(text_integrity.TextIntegrityError, match="invalid_utf8"):
        text_integrity.decode_utf8_strict("ação".encode("cp1252"), source_ref="legacy.txt")


def test_integrity_detector_does_not_flag_valid_portuguese_or_emoji():
    assert text_integrity.integrity_findings("NÃO: ação prática, coração e emoji 🤖✅") == []
    assert "high_confidence_mojibake" in text_integrity.integrity_findings("VocÃª pode ajudar?")
    assert "replacement_character" in text_integrity.integrity_findings("texto com �")


def test_explicit_legacy_pattern_is_recognized_without_automatic_conversion():
    legacy = "VocÃª"
    assert text_integrity.integrity_findings(legacy, legacy_patterns=[legacy]) == []
    assert text_integrity.require_clean_text(legacy, legacy_patterns=[legacy]) == legacy


def test_active_string_literals_in_ia_sources_are_clean_utf8():
    for path in IA_SOURCE_PATHS:
        source = text_integrity.read_text_utf8_strict(path)
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert text_integrity.integrity_findings(node.value) == [], (path, node.lineno, node.value)


def test_transport_defaults_to_normal_certificate_verification():
    assert transport_security.requests_tls_verify({}) is True
    session = transport_security.configure_requests_session(requests.Session(), {})
    assert session.verify is True


def test_transport_uses_explicit_trusted_ca_bundle(tmp_path):
    bundle = tmp_path / "company-ca.pem"
    bundle.write_text("test certificate placeholder", encoding="utf-8")
    assert transport_security.requests_tls_verify({"JK_IA_CA_BUNDLE": str(bundle)}) == str(bundle.resolve())


def test_transport_rejects_missing_configured_ca_bundle(tmp_path):
    with pytest.raises(transport_security.TransportSecurityError, match="trusted_ca_bundle_not_found"):
        transport_security.requests_tls_verify({"JK_IA_CA_BUNDLE": str(tmp_path / "missing.pem")})


def test_ia_provider_source_has_no_tls_disable_or_silent_decode():
    outbound_source = "\n".join(
        text_integrity.read_text_utf8_strict(path) for path in OUTBOUND_PROVIDER_PATHS
    )
    provider_source = text_integrity.read_text_utf8_strict(IA_SOURCE_PATHS[1])
    common_source = text_integrity.read_text_utf8_strict(IA_SOURCE_PATHS[0])
    assert "verify=False" not in outbound_source
    assert "session.verify = False" not in outbound_source
    assert 'errors="ignore"' not in provider_source + common_source
    assert 'errors="replace"' not in provider_source + common_source
