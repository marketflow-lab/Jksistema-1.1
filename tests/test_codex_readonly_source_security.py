from pathlib import Path

from backend.services import codex_readonly_sources


def _touch(path: Path, content: str = "{}") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_generic_discovery_excludes_context_credentials_and_databases(monkeypatch, tmp_path):
    monkeypatch.setenv("JK_CODEX_READONLY_GENERIC_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("JK_CODEX_READONLY_DISCOVERY_ALLOWLIST", "catalogo_publico.json;ContextVault/**;*.db;*config*.json")
    info_root = tmp_path / "info"
    tenant = info_root / "000002"
    _touch(tenant / "catalogo_publico.json")
    _touch(tenant / "lojas_config.json")
    _touch(tenant / "oauth_token.json")
    _touch(tenant / "ia_rag_local.db")
    _touch(tenant / "vendas_historico.db")
    _touch(tenant / "ContextVault" / "80_Curadoria" / "nota.md", "private")
    _touch(tenant / "context_hub" / "staging" / "document.json")
    _touch(tenant / "ContextVault" / ".obsidian" / "app.json")
    monkeypatch.setattr(codex_readonly_sources, "PASTA_INFO", str(info_root), raising=False)

    discovered = {
        codex_readonly_sources._rel(path, tenant)
        for path in codex_readonly_sources._discover_files("000002")
    }

    assert discovered == {"catalogo_publico.json"}


def test_generic_discovery_is_disabled_by_default(monkeypatch, tmp_path):
    info_root = tmp_path / "info"
    _touch(info_root / "000002" / "catalogo_publico.json")
    monkeypatch.setattr(codex_readonly_sources, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.delenv("JK_CODEX_READONLY_GENERIC_DISCOVERY_ENABLED", raising=False)
    monkeypatch.setenv("JK_CODEX_READONLY_DISCOVERY_ALLOWLIST", "catalogo_publico.json")

    assert codex_readonly_sources._discover_files("000002") == []


def test_generic_discovery_requires_explicit_path_allowlist(monkeypatch, tmp_path):
    info_root = tmp_path / "info"
    _touch(info_root / "000002" / "catalogo_publico.json")
    monkeypatch.setattr(codex_readonly_sources, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.setenv("JK_CODEX_READONLY_GENERIC_DISCOVERY_ENABLED", "true")
    monkeypatch.delenv("JK_CODEX_READONLY_DISCOVERY_ALLOWLIST", raising=False)

    assert codex_readonly_sources._discover_files("000002") == []


def test_redaction_covers_buyer_pii_in_keys_and_free_text():
    value = codex_readonly_sources._redact(
        {
            "email": "buyer@example.com",
            "telefone": "+55 (11) 99999-1234",
            "observacao": "Contato buyer@example.com ou CPF 123.456.789-00",
            "line": "access_token=segredo-super-secreto eyJabcdefghijk.abcdefghijk.abcdefgh",
        }
    )

    serialized = str(value)
    assert "buyer@example.com" not in serialized
    assert "99999-1234" not in serialized
    assert "123.456.789-00" not in serialized
    assert "segredo-super-secreto" not in serialized


def test_discovery_rejects_resolved_paths_outside_tenant(tmp_path):
    tenant = tmp_path / "info" / "000002"
    external = tmp_path / "outside" / "secret.json"
    _touch(external)

    assert codex_readonly_sources._discovery_path_forbidden(external, tenant) is True
