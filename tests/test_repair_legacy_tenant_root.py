from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "repair_legacy_tenant_root.py"
SPEC = importlib.util.spec_from_file_location("repair_legacy_tenant_root", SCRIPT_PATH)
assert SPEC and SPEC.loader
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


def _create_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip("Ambiente sem permissao para criar junction de teste.")
    else:
        link.symlink_to(target, target_is_directory=True)


def _runtime_with_legacy_tenant(tmp_path: Path, name: str = "case") -> tuple[Path, Path, Path]:
    runtime = tmp_path / f"runtime-{name}"
    info = runtime / "info"
    source = tmp_path / f"source-{name}"
    info.mkdir(parents=True)
    source.mkdir()
    (source / "lojas_config.json").write_text(
        json.dumps([{"store_id": "store-1", "nome": "Loja"}]),
        encoding="utf-8",
    )
    (source / "cadastro_fotos" / "loja").mkdir(parents=True)
    (source / "cadastro_fotos" / "loja" / "foto.jpg").write_bytes(b"foto-real")
    (source / "diretorio-vazio").mkdir()
    tenant = info / "000002"
    _create_directory_link(tenant, source)
    return runtime, source, tenant


def _remove_test_link(candidate: Path) -> None:
    if os.path.lexists(candidate) and repair._is_reparse(candidate):
        repair._remove_link(candidate)


def test_preflight_e_aplicacao_materializam_raiz_fisica_sem_apagar_origem(tmp_path, monkeypatch):
    runtime, source, tenant = _runtime_with_legacy_tenant(tmp_path, "success")
    monkeypatch.setattr(repair, "_assert_runtime_stopped", lambda: None)

    plan = repair.inspect_legacy_tenant_roots(runtime, "000002")
    tenant_plan = plan["tenants"][0]
    assert plan["mode"] == "plan"
    assert tenant_plan["requires_migration"] is True
    assert tenant_plan["file_count"] == 2
    assert tenant_plan["source_preserved"] is True

    result = repair.apply_legacy_tenant_root_repair(
        runtime,
        "000002",
        tenant_plan["confirmation_token"],
    )

    assert result["changed"] is True
    assert result["source_preserved"] is True
    assert tenant.is_dir()
    assert not repair._is_reparse(tenant)
    assert (tenant / "lojas_config.json").read_bytes() == (source / "lojas_config.json").read_bytes()
    assert (tenant / "cadastro_fotos" / "loja" / "foto.jpg").read_bytes() == b"foto-real"
    assert (tenant / "diretorio-vazio").is_dir()
    assert source.is_dir()
    assert (source / "cadastro_fotos" / "loja" / "foto.jpg").read_bytes() == b"foto-real"
    assert not (runtime / "info" / repair.JOURNAL_NAME).exists()
    status = json.loads((runtime / "info" / repair.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["state"] == "complete"
    assert status["source_preserved"] is True


def test_aplicacao_exige_token_do_inventario_atual_e_preserva_junction(tmp_path, monkeypatch):
    runtime, source, tenant = _runtime_with_legacy_tenant(tmp_path, "token")
    monkeypatch.setattr(repair, "_assert_runtime_stopped", lambda: None)
    try:
        with pytest.raises(repair.TenantRootMigrationError) as exc_info:
            repair.apply_legacy_tenant_root_repair(runtime, "000002", "token-incorreto")
        assert exc_info.value.code == "confirmation_token_mismatch"
        assert repair._is_reparse(tenant)
        assert (source / "lojas_config.json").exists()
        assert not (runtime / "info" / repair.JOURNAL_NAME).exists()
    finally:
        _remove_test_link(tenant)


def test_preflight_rejeita_link_aninhado_sem_criar_staging(tmp_path):
    runtime, _source, tenant = _runtime_with_legacy_tenant(tmp_path, "nested")
    external = tmp_path / "nested-external"
    external.mkdir()
    nested = tenant / "atalho"
    _create_directory_link(nested, external)
    try:
        with pytest.raises(repair.TenantRootMigrationError) as exc_info:
            repair.inspect_legacy_tenant_roots(runtime, "000002")
        assert exc_info.value.code == "tenant_source_reparse"
        assert not any(
            item.name.startswith(repair.STAGE_PREFIX)
            for item in (runtime / "info").iterdir()
        )
    finally:
        _remove_test_link(nested)
        _remove_test_link(tenant)


def test_falha_durante_troca_restaura_junction_e_remove_copia_temporaria(tmp_path, monkeypatch):
    runtime, source, tenant = _runtime_with_legacy_tenant(tmp_path, "rollback")
    monkeypatch.setattr(repair, "_assert_runtime_stopped", lambda: None)
    plan = repair.inspect_legacy_tenant_roots(runtime, "000002")["tenants"][0]
    try:
        with pytest.raises(repair.TenantRootMigrationError):
            repair.apply_legacy_tenant_root_repair(
                runtime,
                "000002",
                plan["confirmation_token"],
                fault_phase="after_link_moved",
            )
        assert repair._is_reparse(tenant)
        assert (tenant / "lojas_config.json").read_bytes() == (source / "lojas_config.json").read_bytes()
        assert not (runtime / "info" / repair.JOURNAL_NAME).exists()
        assert not any(
            item.name.startswith((repair.STAGE_PREFIX, repair.BACKUP_PREFIX))
            for item in (runtime / "info").iterdir()
        )
    finally:
        _remove_test_link(tenant)


def test_recuperacao_explicitamente_conclui_troca_interrompida(tmp_path, monkeypatch):
    runtime, source, tenant = _runtime_with_legacy_tenant(tmp_path, "recovery")
    monkeypatch.setattr(repair, "_assert_runtime_stopped", lambda: None)
    plan = repair.inspect_legacy_tenant_roots(runtime, "000002")["tenants"][0]

    with pytest.raises(SystemExit):
        repair.apply_legacy_tenant_root_repair(
            runtime,
            "000002",
            plan["confirmation_token"],
            fault_phase="crash_after_link_moved",
        )

    assert not os.path.lexists(tenant)
    assert (runtime / "info" / repair.JOURNAL_NAME).exists()
    recovered = repair.recover_legacy_tenant_root_repair(runtime)

    assert recovered["state"] == "complete"
    assert recovered["source_preserved"] is True
    assert tenant.is_dir()
    assert not repair._is_reparse(tenant)
    assert (tenant / "lojas_config.json").read_bytes() == (source / "lojas_config.json").read_bytes()
    assert source.is_dir()
    assert not (runtime / "info" / repair.JOURNAL_NAME).exists()


@pytest.mark.skipif(os.name != "nt", reason="Contrato de nomes Win32 legados")
def test_copia_verificada_suporta_nome_legado_terminado_em_ponto(tmp_path, monkeypatch):
    runtime, source, tenant = _runtime_with_legacy_tenant(tmp_path, "trailing-dot")
    legacy = repair._io_path(source / "arquivo-legado.")
    legacy.write_bytes(b"conteudo-legado")
    target_legacy = repair._io_path(tenant / "arquivo-legado.")
    monkeypatch.setattr(repair, "_assert_runtime_stopped", lambda: None)
    try:
        plan = repair.inspect_legacy_tenant_roots(runtime, "000002")["tenants"][0]
        assert plan["requires_migration"] is True
        assert plan["file_count"] == 3
        result = repair.apply_legacy_tenant_root_repair(
            runtime,
            "000002",
            plan["confirmation_token"],
        )
        assert result["changed"] is True
        assert target_legacy.read_bytes() == b"conteudo-legado"
    finally:
        target_legacy.unlink(missing_ok=True)
        legacy.unlink(missing_ok=True)
        _remove_test_link(tenant)


def test_reparador_offline_esta_incluido_no_contrato_do_instalador():
    package = json.loads((ROOT / "electron_app" / "package.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (ROOT / "electron_app" / "installer-required-resources.json").read_text(encoding="utf-8")
    )
    local_app = next(
        item
        for item in package["build"]["extraResources"]
        if item.get("to") == "local_app"
    )
    filters = local_app["filter"]
    relative = "scripts/repair_legacy_tenant_root.py"
    assert relative in filters
    assert relative in manifest["requiredSourceFiles"]
