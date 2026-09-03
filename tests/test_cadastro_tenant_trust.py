import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.services import cadastro_tenant_trust as trust
from backend.services import shared_sync_collect_files


def _create_directory_alias(link_path: Path, target_path: Path) -> None:
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
            pytest.fail("Nao foi possivel criar junction de teste.")
    else:
        link_path.symlink_to(target_path, target_is_directory=True)


def _remove_directory_alias(link_path: Path) -> None:
    if not os.path.lexists(link_path):
        return
    is_junction = getattr(link_path, "is_junction", None)
    if os.name == "nt" and callable(is_junction) and is_junction():
        os.rmdir(link_path)
    else:
        link_path.unlink()


def _layout(tmp_path: Path, clients=("000002",)):
    alias_root = tmp_path / "alias-info"
    canonical_root = tmp_path / "canonical-info"
    alias_root.mkdir(parents=True)
    canonical_root.mkdir(parents=True)
    for client_id in clients:
        canonical_tenant = canonical_root / client_id
        canonical_tenant.mkdir()
        _create_directory_alias(alias_root / client_id, canonical_tenant)
    return alias_root, canonical_root


def test_registry_is_local_path_free_closed_schema_and_idempotent(tmp_path):
    alias_root, canonical_root = _layout(tmp_path)

    first = trust.registrar_alias_tenant(alias_root, canonical_root, "000002")
    second = trust.registrar_alias_tenant(alias_root, canonical_root, "000002")

    assert first["action"] == "registered"
    assert second["action"] == "unchanged"
    raw = (alias_root / trust.CADASTRO_TENANT_TRUST_ARQUIVO).read_text("utf-8")
    assert str(alias_root) not in raw
    assert str(canonical_root) not in raw
    assert set(json.loads(raw)) == {"schema", "clients"}
    assert set(json.loads(raw)["clients"]["000002"]) == {"st_dev", "st_ino"}


def test_runtime_resolver_requires_registry_and_returns_canonical_target(tmp_path):
    alias_root, canonical_root = _layout(tmp_path)

    with pytest.raises(trust.CadastroTenantTrustErro) as missing:
        trust.resolver_alias_tenant_registrado(alias_root, "000002")
    assert missing.value.code == "registry_missing"

    trust.registrar_alias_tenant(alias_root, canonical_root, "000002")
    resolved = trust.resolver_alias_tenant_registrado(alias_root, "000002")

    assert os.path.normcase(resolved) == os.path.normcase(
        str((canonical_root / "000002").resolve())
    )


def test_registry_entry_for_one_client_does_not_authorize_another(tmp_path):
    alias_root, canonical_root = _layout(tmp_path, ("000002", "000003"))
    trust.registrar_alias_tenant(alias_root, canonical_root, "000002")

    with pytest.raises(trust.CadastroTenantTrustErro) as error:
        trust.resolver_alias_tenant_registrado(alias_root, "000003")

    assert error.value.code == "identity_mismatch"


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        '{"schema":"jk.cadastro.tenant-trust.v1","schema":"x","clients":{}}',
        '{"schema":"jk.cadastro.tenant-trust.v1","clients":{},"extra":1}',
        '{"schema":"jk.cadastro.tenant-trust.v1","clients":{"000002":{"st_dev":1,"st_ino":2,"extra":3}}}',
        '{"schema":"jk.cadastro.tenant-trust.v1","clients":{"000002":{"st_dev":true,"st_ino":2}}}',
        '{"schema":"jk.cadastro.tenant-trust.v1","clients":{"000002":{"st_dev":1,"st_ino":0}}}',
        '{"schema":"jk.cadastro.tenant-trust.v1","clients":{"../000002":{"st_dev":1,"st_ino":2}}}',
    ],
)
def test_malformed_duplicate_extra_and_invalid_identity_fail_closed(tmp_path, raw):
    alias_root, _canonical_root = _layout(tmp_path)
    (alias_root / trust.CADASTRO_TENANT_TRUST_ARQUIVO).write_text(raw, encoding="utf-8")

    with pytest.raises(trust.CadastroTenantTrustErro) as exc:
        trust.resolver_alias_tenant_registrado(alias_root, "000002")

    assert exc.value.code == "registry_invalid"


def test_registry_symlink_fails_closed(tmp_path):
    alias_root, _canonical_root = _layout(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({"schema": trust.CADASTRO_TENANT_TRUST_SCHEMA, "clients": {}}),
        encoding="utf-8",
    )
    registry = alias_root / trust.CADASTRO_TENANT_TRUST_ARQUIVO
    try:
        registry.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlink de arquivo indisponivel: {exc}")

    with pytest.raises(trust.CadastroTenantTrustErro) as error:
        trust.resolver_alias_tenant_registrado(alias_root, "000002")

    assert error.value.code == "registry_invalid"
    assert outside.is_file()


def test_retarget_after_registration_fails_identity_check(tmp_path):
    alias_root, canonical_root = _layout(tmp_path)
    trust.registrar_alias_tenant(alias_root, canonical_root, "000002")
    alias = alias_root / "000002"
    other = tmp_path / "other" / "000002"
    _remove_directory_alias(alias)
    _create_directory_alias(alias, other)

    with pytest.raises(trust.CadastroTenantTrustErro) as error:
        trust.resolver_alias_tenant_registrado(alias_root, "000002")

    assert error.value.code == "identity_mismatch"


@pytest.mark.parametrize("linked_root", ["alias", "canonical"])
def test_registration_rejects_reparse_info_roots(tmp_path, linked_root):
    real_alias_root, real_canonical_root = _layout(tmp_path / "real")
    link = tmp_path / f"{linked_root}-linked"
    target = real_alias_root if linked_root == "alias" else real_canonical_root
    _create_directory_alias(link, target)
    alias_root = link if linked_root == "alias" else real_alias_root
    canonical_root = link if linked_root == "canonical" else real_canonical_root

    with pytest.raises(trust.CadastroTenantTrustErro) as error:
        trust.registrar_alias_tenant(alias_root, canonical_root, "000002")

    assert error.value.code == f"{linked_root}_root_unsafe"


def test_registration_requires_physical_alias_to_be_an_explicit_reparse(tmp_path):
    alias_root = tmp_path / "alias-info"
    canonical_root = tmp_path / "canonical-info"
    (alias_root / "000002").mkdir(parents=True)
    (canonical_root / "000002").mkdir(parents=True)

    with pytest.raises(trust.CadastroTenantTrustErro) as error:
        trust.registrar_alias_tenant(alias_root, canonical_root, "000002")

    assert error.value.code == "alias_tenant_required"


def test_registration_rejects_reparse_canonical_tenant(tmp_path):
    alias_root = tmp_path / "alias-info"
    canonical_root = tmp_path / "canonical-info"
    external_tenant = tmp_path / "external" / "000002"
    alias_root.mkdir()
    canonical_root.mkdir()
    _create_directory_alias(canonical_root / "000002", external_tenant)
    _create_directory_alias(alias_root / "000002", external_tenant)

    with pytest.raises(trust.CadastroTenantTrustErro) as error:
        trust.registrar_alias_tenant(alias_root, canonical_root, "000002")

    assert error.value.code == "canonical_tenant_unsafe"


def test_canonical_directory_recreation_fails_identity_check(tmp_path):
    alias_root, canonical_root = _layout(tmp_path)
    tenant = canonical_root / "000002"
    trust.registrar_alias_tenant(alias_root, canonical_root, "000002")
    alias = alias_root / "000002"
    _remove_directory_alias(alias)
    old = canonical_root / "000002-old"
    tenant.rename(old)
    tenant.mkdir()
    _create_directory_alias(alias, tenant)

    with pytest.raises(trust.CadastroTenantTrustErro) as error:
        trust.resolver_alias_tenant_registrado(alias_root, "000002")

    assert error.value.code == "identity_mismatch"


def test_registration_preserves_other_clients_under_concurrency(tmp_path):
    clients = tuple(f"{number:06d}" for number in range(2, 10))
    alias_root, canonical_root = _layout(tmp_path, clients)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda client_id: trust.registrar_alias_tenant(
                    alias_root, canonical_root, client_id
                ),
                clients,
            )
        )

    assert all(result["action"] == "registered" for result in results)
    registry = json.loads(
        (alias_root / trust.CADASTRO_TENANT_TRUST_ARQUIVO).read_text("utf-8")
    )
    assert set(registry["clients"]) == set(clients)


def test_atomic_publication_failure_preserves_registry_and_cleans_temporary(
    tmp_path, monkeypatch
):
    alias_root, canonical_root = _layout(tmp_path, ("000002", "000003"))
    trust.registrar_alias_tenant(alias_root, canonical_root, "000002")
    registry_path = alias_root / trust.CADASTRO_TENANT_TRUST_ARQUIVO
    before = registry_path.read_bytes()

    def fail_before_publication(_source, _target):
        raise OSError("falha injetada antes da publicacao")

    monkeypatch.setattr(trust.os, "replace", fail_before_publication)
    with pytest.raises(OSError, match="falha injetada"):
        trust.registrar_alias_tenant(alias_root, canonical_root, "000003")

    assert registry_path.read_bytes() == before
    assert list(alias_root.glob(".cadastro-tenant-trust.*.tmp")) == []


def test_registry_stays_outside_tenant_and_shared_sync_collection(tmp_path, monkeypatch):
    alias_root, canonical_root = _layout(tmp_path)
    tenant = canonical_root / "000002"
    (tenant / "cadastro_produtos.csv").write_text(
        "sku,descricao\n001,Produto\n", encoding="utf-8"
    )
    trust.registrar_alias_tenant(alias_root, canonical_root, "000002")
    registry_path = alias_root / trust.CADASTRO_TENANT_TRUST_ARQUIVO
    monkeypatch.setattr(
        shared_sync_collect_files,
        "get_tenant_path",
        lambda _client_id: str(tenant),
        raising=False,
    )

    entries, _warnings = shared_sync_collect_files._shared_sync_coletar_arquivos(
        "000002", "cadastro", username="operador", user_only=True
    )
    names = {entry["relative_path"] for entry in entries}

    assert registry_path.parent == alias_root
    assert not registry_path.is_relative_to(tenant)
    assert "cadastro_produtos.csv" in names
    assert trust.CADASTRO_TENANT_TRUST_ARQUIVO not in names


def test_cross_process_registration_has_no_lost_update(tmp_path):
    clients = ("000002", "000003", "000004", "000005")
    alias_root, canonical_root = _layout(tmp_path, clients)
    script = Path(__file__).parents[1] / "scripts" / "register-cadastro-tenant-alias.py"
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                str(script),
                "--alias-info-root",
                str(alias_root),
                "--canonical-info-root",
                str(canonical_root),
                "--client-id",
                client_id,
                "--apply",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for client_id in clients
    ]
    outputs = [process.communicate(timeout=30) for process in processes]

    assert [process.returncode for process in processes] == [0, 0, 0, 0], outputs
    registry = json.loads(
        (alias_root / trust.CADASTRO_TENANT_TRUST_ARQUIVO).read_text("utf-8")
    )
    assert set(registry["clients"]) == set(clients)


def test_dry_run_cli_does_not_write_and_apply_output_contains_no_paths(tmp_path):
    alias_root, canonical_root = _layout(tmp_path)
    script = Path(__file__).parents[1] / "scripts" / "register-cadastro-tenant-alias.py"
    command = [
        sys.executable,
        str(script),
        "--alias-info-root",
        str(alias_root),
        "--canonical-info-root",
        str(canonical_root),
        "--client-id",
        "000002",
    ]

    dry_run = subprocess.run(command, check=False, capture_output=True, text=True)
    assert dry_run.returncode == 0
    assert not (alias_root / trust.CADASTRO_TENANT_TRUST_ARQUIVO).exists()
    assert str(alias_root) not in dry_run.stdout
    assert str(canonical_root) not in dry_run.stdout
    assert json.loads(dry_run.stdout)["mode"] == "dry-run"

    applied = subprocess.run(command + ["--apply"], check=False, capture_output=True, text=True)
    assert applied.returncode == 0
    assert json.loads(applied.stdout)["action"] == "registered"
    assert str(alias_root) not in applied.stdout
    assert str(canonical_root) not in applied.stdout


def test_physical_tenant_needs_no_registry(tmp_path):
    alias_root = tmp_path / "info"
    tenant = alias_root / "000002"
    tenant.mkdir(parents=True)

    with pytest.raises(trust.CadastroTenantTrustErro) as error:
        trust.resolver_alias_tenant_registrado(alias_root, "000002")

    assert error.value.code == "alias_tenant_required"
