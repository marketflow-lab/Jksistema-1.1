import hashlib
import json
from pathlib import Path

import pytest

from scripts import provision_python_runtime as provisioner


def _runtime_config(
    version="9.8.7",
    implementation="cp",
    architecture="amd64",
    *,
    installer_size=123,
    installer_sha256="1" * 64,
    portable_file_count=1,
    portable_total_size=456,
    portable_tree_sha256="2" * 64,
):
    major, minor, _patch = version.split(".")
    return {
        "schemaVersion": 1,
        "python": {
            "version": version,
            "minor": f"{major}.{minor}",
            "implementation": implementation,
            "abi": f"{implementation}{major}{minor}",
            "windowsArchitecture": architecture,
            "windowsInstaller": f"python-{version}-{architecture}.exe",
            "windowsInstallerSize": installer_size,
            "windowsInstallerSha256": installer_sha256,
            "windowsPortable": {
                "path": "portable",
                "fileCount": portable_file_count,
                "totalSize": portable_total_size,
                "treeSha256": portable_tree_sha256,
            },
        },
    }


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _windows_busy_error(winerror=32):
    error = PermissionError("simulated Windows sharing violation")
    error.winerror = winerror
    return error


def test_logger_escapa_unicode_incompativel_com_console_windows(tmp_path, monkeypatch):
    class StrictCp1252Console:
        encoding = "cp1252"

        def __init__(self):
            self.output = []

        def write(self, value):
            value.encode(self.encoding, errors="strict")
            self.output.append(value)
            return len(value)

        def flush(self):
            return None

    console = StrictCp1252Console()
    monkeypatch.setattr(provisioner.sys, "stdout", console)
    log_path = tmp_path / "provision.log"

    provisioner.ProvisionLogger(log_path).write("erro com caractere: \ufffd")

    assert "\\ufffd" in "".join(console.output)
    assert "\ufffd" in log_path.read_text(encoding="utf-8")


def _quick_reuse_fixture(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    runtime_root = source / "python_runtime"
    portable = runtime_root / "portable"
    portable.mkdir(parents=True)
    (portable / "python.exe").write_bytes(b"source-python")
    installer = runtime_root / "python-9.8.7-amd64.exe"
    installer.write_bytes(b"signed-installer-placeholder")
    portable_inventory = provisioner.tree_inventory(portable)
    _write_json(
        source / "runtime-versions.json",
        _runtime_config(
            installer_size=installer.stat().st_size,
            installer_sha256=provisioner.sha256_file(installer),
            portable_file_count=portable_inventory[0],
            portable_total_size=portable_inventory[1],
            portable_tree_sha256=portable_inventory[2],
        ),
    )
    spec = provisioner.load_runtime_spec(source)
    _write_json(
        source / "runtime-manifest.json",
        {
            "version": "1.2.3",
            "python": {
                "version": spec.version,
                "abi": spec.abi,
                "installer": {
                    "path": installer.name,
                    "size": spec.windows_installer_size,
                    "sha256": spec.windows_installer_sha256,
                },
                "portable": {
                    "path": "portable",
                    "file_count": spec.windows_portable_file_count,
                    "total_size": spec.windows_portable_total_size,
                    "tree_sha256": spec.windows_portable_tree_sha256,
                },
            },
        },
    )

    requirements = source / "requirements.txt"
    requirements.write_text("--require-hashes\n--only-binary=:all:\n", encoding="utf-8")
    wheel_root = source / "python_wheels"
    wheel_root.mkdir()
    wheel = wheel_root / "example-1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-payload")
    _write_json(
        wheel_root / "manifest.json",
        {
            "requirements_sha256": provisioner.sha256_file(requirements),
            "python_version": spec.minor,
            "abi": spec.abi,
            "wheels": [
                {
                    "name": wheel.name,
                    "size": wheel.stat().st_size,
                    "sha256": provisioner.sha256_file(wheel),
                }
            ],
        },
    )

    installed_runtime = target / ".python-runtime"
    scripts = target / ".venv" / "Scripts"
    installed_runtime.mkdir(parents=True)
    scripts.mkdir(parents=True)
    (installed_runtime / "python.exe").write_bytes(b"source-python")
    for name in ("python.exe", "pip.exe", "uvicorn.exe"):
        (scripts / name).write_bytes(b"launcher")
    _runtime_manifest, marker = provisioner.load_quick_reuse_metadata(source, spec)
    _write_json(target / ".venv" / provisioner.VENV_MARKER_NAME, marker)
    _write_json(target / provisioner.STATUS_RELATIVE_PATH, {**marker, "action": "created"})
    return source, target, spec


def test_runtime_spec_comes_only_from_central_config(tmp_path):
    _write_json(tmp_path / "runtime-versions.json", _runtime_config())

    spec = provisioner.load_runtime_spec(tmp_path)

    assert spec.version == "9.8.7"
    assert spec.minor == "9.8"
    assert spec.abi == "cp98"
    assert spec.windows_installer == "python-9.8.7-amd64.exe"
    assert spec.windows_installer_size == 123
    assert spec.windows_installer_sha256 == "1" * 64
    assert spec.portable_inventory == (1, 456, "2" * 64)


def test_runtime_spec_rejects_minor_or_abi_drift(tmp_path):
    config = _runtime_config()
    config["python"]["abi"] = "cp97"
    _write_json(tmp_path / "runtime-versions.json", config)

    with pytest.raises(provisioner.ProvisionError, match="Minor/ABI"):
        provisioner.load_runtime_spec(tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("windowsInstallerSize", 0),
        ("windowsInstallerSha256", "not-a-sha256"),
        ("windowsPortable", None),
    ),
)
def test_runtime_spec_rejects_missing_or_invalid_supply_chain_pins(tmp_path, field, value):
    config = _runtime_config()
    config["python"][field] = value
    _write_json(tmp_path / "runtime-versions.json", config)

    with pytest.raises(provisioner.ProvisionError) as raised:
        provisioner.load_runtime_spec(tmp_path)

    assert raised.value.code == "runtime_config_invalid"


def test_tree_inventory_uses_documented_stable_records(tmp_path):
    root = tmp_path / "portable"
    (root / "Lib").mkdir(parents=True)
    (root / "python.exe").write_bytes(b"python")
    (root / "Lib" / "module.py").write_bytes(b"value = 1\n")

    count, total_size, tree_hash = provisioner.tree_inventory(root)

    expected = hashlib.sha256()
    for relative in ("Lib/module.py", "python.exe"):
        path = root / Path(relative)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        expected.update(f"{relative}\0{path.stat().st_size}\0{digest}\n".encode())
    assert count == 2
    assert total_size == len(b"python") + len(b"value = 1\n")
    assert tree_hash == expected.hexdigest()


def test_run_command_removes_python_and_pip_environment_poison(tmp_path, monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = "ok\n"

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return Completed()

    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "poison-home"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "poison-path"))
    monkeypatch.setenv("PYTHONUSERBASE", str(tmp_path / "poison-user"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "poison-venv"))
    monkeypatch.setenv("PIP_INDEX_URL", "https://evil.invalid/simple")
    monkeypatch.setenv("PIP_TRUSTED_HOST", "evil.invalid")
    monkeypatch.setattr(provisioner.subprocess, "run", fake_run)

    provisioner.run_command(
        ["python", "-B", "-I", "-c", "print('ok')"],
        provisioner.ProvisionLogger(tmp_path / "provision.log"),
    )

    environment = captured["env"]
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE", "VIRTUAL_ENV"):
        assert name not in environment
    assert "PIP_INDEX_URL" not in environment
    assert "PIP_TRUSTED_HOST" not in environment
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PIP_NO_INDEX"] == "1"
    assert environment["PIP_CONFIG_FILE"].upper() == ("NUL" if provisioner.os.name == "nt" else "/DEV/NULL")

    with pytest.raises(provisioner.ProvisionError) as raised:
        provisioner.run_command(
            ["python", "-I", "-c", "pass"],
            provisioner.ProvisionLogger(tmp_path / "provision.log"),
            env={"PYTHONPATH": "poison"},
        )
    assert raised.value.code == "unsafe_command_environment"


def test_wheelhouse_requires_exact_manifest_inventory(tmp_path):
    spec_config = _runtime_config()
    _write_json(tmp_path / "runtime-versions.json", spec_config)
    spec = provisioner.load_runtime_spec(tmp_path)
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("--require-hashes\n--only-binary=:all:\n", encoding="utf-8")
    wheel_root = tmp_path / "python_wheels"
    wheel_root.mkdir()
    wheel = wheel_root / "example-1.0-py3-none-any.whl"
    wheel.write_bytes(b"test-wheel")
    manifest = {
        "requirements_sha256": provisioner.sha256_file(requirements),
        "python_version": spec.minor,
        "abi": spec.abi,
        "wheels": [
            {"name": wheel.name, "size": wheel.stat().st_size, "sha256": provisioner.sha256_file(wheel)}
        ],
    }
    _write_json(wheel_root / "manifest.json", manifest)

    result = provisioner.validate_wheelhouse(tmp_path, spec)
    assert result["wheel_count"] == 1

    (wheel_root / "undeclared-1.0-py3-none-any.whl").write_bytes(b"unexpected")
    with pytest.raises(provisioner.ProvisionError, match="Inventario"):
        provisioner.validate_wheelhouse(tmp_path, spec)


def test_interrupted_promotion_restores_previous_directory(tmp_path):
    destination = tmp_path / ".venv"
    backup = tmp_path / ".venv.previous"
    staged = tmp_path / ".venv.new"
    destination.mkdir()
    backup.mkdir()
    staged.mkdir()
    (destination / "state.txt").write_text("new", encoding="utf-8")
    (backup / "state.txt").write_text("previous", encoding="utf-8")

    provisioner._recover_interrupted_promotion(destination, backup, staged, tmp_path)

    assert (destination / "state.txt").read_text(encoding="utf-8") == "previous"
    assert not backup.exists()
    assert not staged.exists()


def test_failed_atomic_promotion_immediately_restores_previous_directory(tmp_path, monkeypatch):
    destination = tmp_path / ".venv"
    backup = tmp_path / ".venv.previous"
    staged = tmp_path / ".venv.new"
    destination.mkdir()
    staged.mkdir()
    (destination / "state.txt").write_text("previous", encoding="utf-8")
    (staged / "state.txt").write_text("new", encoding="utf-8")
    real_replace = provisioner.os.replace

    def fail_new_promotion(source, target):
        if Path(source) == staged and Path(target) == destination:
            raise OSError("simulated promotion failure")
        return real_replace(source, target)

    monkeypatch.setattr(provisioner.os, "replace", fail_new_promotion)

    with pytest.raises(OSError, match="simulated"):
        provisioner._promote_directory(staged, destination, tmp_path, backup)

    assert (destination / "state.txt").read_text(encoding="utf-8") == "previous"
    assert not backup.exists()


def test_venv_rename_retries_windows_lock_then_succeeds(tmp_path, monkeypatch):
    destination = tmp_path / ".venv"
    backup = tmp_path / ".venv.previous"
    destination.mkdir()
    (destination / "state.txt").write_text("working", encoding="utf-8")
    logger = provisioner.ProvisionLogger(tmp_path / "provision.log")
    real_replace = provisioner.os.replace
    attempts = []

    def flaky_replace(source, target):
        attempts.append((Path(source), Path(target)))
        if len(attempts) < 3:
            raise _windows_busy_error(32)
        return real_replace(source, target)

    monkeypatch.setattr(provisioner.os, "replace", flaky_replace)

    provisioner._replace_venv_with_retry(destination, backup, logger, retry_delays=(0, 0, 0))

    assert len(attempts) == 3
    assert not destination.exists()
    assert (backup / "state.txt").read_text(encoding="utf-8") == "working"
    assert "tentativa 2/4" in logger.path.read_text(encoding="utf-8")
    assert 12 <= sum(provisioner.VENV_RENAME_RETRY_DELAYS) <= 15
    assert provisioner.WINDOWS_RENAME_BUSY_ERRORS == {5, 32, 33}


def test_venv_rename_persistent_windows_lock_is_classified_and_preserves_source(
    tmp_path, monkeypatch
):
    destination = tmp_path / ".venv"
    backup = tmp_path / ".venv.previous"
    destination.mkdir()
    (destination / "state.txt").write_text("working", encoding="utf-8")
    logger = provisioner.ProvisionLogger(tmp_path / "provision.log")
    attempts = []

    def blocked_replace(source, target):
        attempts.append((Path(source), Path(target)))
        raise _windows_busy_error(5)

    monkeypatch.setattr(provisioner.os, "replace", blocked_replace)

    with pytest.raises(provisioner.ProvisionError) as raised:
        provisioner._replace_venv_with_retry(destination, backup, logger, retry_delays=(0, 0))

    assert raised.value.code == "venv_in_use"
    assert len(attempts) == 3
    assert (destination / "state.txt").read_text(encoding="utf-8") == "working"
    assert not backup.exists()
    assert "continuou em uso apos 3 tentativas" in logger.path.read_text(encoding="utf-8")


def test_runtime_cache_cleanup_is_strictly_scoped_to_pyc_inside_pycache(tmp_path):
    runtime = tmp_path / ".python-runtime"
    cache = runtime / "Lib" / "example" / "__pycache__"
    cache.mkdir(parents=True)
    generated = cache / "module.cpython-314.pyc"
    generated.write_bytes(b"generated-bytecode")
    preserved_in_cache = cache / "metadata.txt"
    preserved_in_cache.write_text("keep", encoding="utf-8")
    preserved_outside_cache = runtime / "Lib" / "manual.pyc"
    preserved_outside_cache.write_bytes(b"keep-outside-cache")
    logger = provisioner.ProvisionLogger(tmp_path / "provision.log")

    removed = provisioner._remove_installed_runtime_bytecode_caches(runtime, logger)

    assert removed == (1, len(b"generated-bytecode"))
    assert not generated.exists()
    assert preserved_in_cache.read_text(encoding="utf-8") == "keep"
    assert preserved_outside_cache.read_bytes() == b"keep-outside-cache"
    assert cache.is_dir()


def test_runtime_integrity_check_removes_generated_caches_then_matches_pin(tmp_path):
    runtime = tmp_path / ".python-runtime"
    runtime.mkdir()
    (runtime / "python.exe").write_bytes(b"signed-runtime-placeholder")
    expected = provisioner.tree_inventory(runtime)
    generated = runtime / "Lib" / "__pycache__" / "argparse.cpython-314.pyc"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"generated-bytecode")
    logger = provisioner.ProvisionLogger(tmp_path / "provision.log")

    actual = provisioner._verify_installed_runtime_after_checks(runtime, expected, logger)

    assert actual == expected
    assert not generated.exists()
    assert not generated.parent.exists()


def test_runtime_integrity_check_rejects_non_cache_drift(tmp_path):
    runtime = tmp_path / ".python-runtime"
    runtime.mkdir()
    (runtime / "python.exe").write_bytes(b"signed-runtime-placeholder")
    expected = provisioner.tree_inventory(runtime)
    unexpected = runtime / "unexpected.bin"
    unexpected.write_bytes(b"not-a-cache")
    logger = provisioner.ProvisionLogger(tmp_path / "provision.log")

    with pytest.raises(provisioner.ProvisionError) as raised:
        provisioner._verify_installed_runtime_after_checks(runtime, expected, logger)

    assert raised.value.code == "installed_runtime_integrity_failed"
    assert unexpected.read_bytes() == b"not-a-cache"


def test_runtime_copy_normalizes_transient_stage_bytecode(tmp_path, monkeypatch):
    source = tmp_path / "portable"
    staged = tmp_path / ".python-runtime.new"
    source.mkdir()
    (source / "python.exe").write_bytes(b"runtime")
    expected = provisioner.tree_inventory(source)
    logger = provisioner.ProvisionLogger(tmp_path / "provision.log")
    real_copytree = provisioner.shutil.copytree
    attempts = []

    def copy_with_transient_cache(source_path, destination_path, **kwargs):
        attempts.append(Path(destination_path))
        result = real_copytree(source_path, destination_path, **kwargs)
        generated = Path(destination_path) / "Lib" / "__pycache__" / "os.cpython-314.pyc"
        generated.parent.mkdir(parents=True)
        generated.write_bytes(b"transient-cache")
        return result

    monkeypatch.setattr(provisioner.shutil, "copytree", copy_with_transient_cache)

    actual = provisioner._copy_runtime_tree_with_retry(
        source, staged, tmp_path, expected, logger, retry_delays=()
    )

    assert actual == expected
    assert attempts == [staged]
    assert not list(staged.rglob("*.pyc"))


def test_runtime_copy_recreates_stage_after_first_integrity_mismatch(tmp_path, monkeypatch):
    source = tmp_path / "portable"
    staged = tmp_path / ".python-runtime.new"
    source.mkdir()
    (source / "python.exe").write_bytes(b"runtime")
    expected = provisioner.tree_inventory(source)
    logger = provisioner.ProvisionLogger(tmp_path / "provision.log")
    real_copytree = provisioner.shutil.copytree
    attempts = []

    def first_copy_is_corrupted(source_path, destination_path, **kwargs):
        attempts.append(Path(destination_path))
        result = real_copytree(source_path, destination_path, **kwargs)
        if len(attempts) == 1:
            (Path(destination_path) / "unexpected.bin").write_bytes(b"scanner-drift")
        return result

    monkeypatch.setattr(provisioner.shutil, "copytree", first_copy_is_corrupted)

    actual = provisioner._copy_runtime_tree_with_retry(
        source, staged, tmp_path, expected, logger, retry_delays=(0,)
    )

    assert actual == expected
    assert len(attempts) == 2
    assert not (staged / "unexpected.bin").exists()
    assert "Nova tentativa" in logger.path.read_text(encoding="utf-8")


def test_runtime_copy_retries_transient_shutil_error(tmp_path, monkeypatch):
    source = tmp_path / "portable"
    staged = tmp_path / ".python-runtime.new"
    source.mkdir()
    (source / "python.exe").write_bytes(b"runtime")
    expected = provisioner.tree_inventory(source)
    logger = provisioner.ProvisionLogger(tmp_path / "provision.log")
    real_copytree = provisioner.shutil.copytree
    attempts = []

    def first_copy_fails(source_path, destination_path, **kwargs):
        attempts.append(Path(destination_path))
        if len(attempts) == 1:
            raise provisioner.shutil.Error([("source", "destination", "sharing violation")])
        return real_copytree(source_path, destination_path, **kwargs)

    monkeypatch.setattr(provisioner.shutil, "copytree", first_copy_fails)

    actual = provisioner._copy_runtime_tree_with_retry(
        source, staged, tmp_path, expected, logger, retry_delays=(0,)
    )

    assert actual == expected
    assert len(attempts) == 2
    assert "sharing violation" in logger.path.read_text(encoding="utf-8")


def test_runtime_copy_persistent_drift_is_classified(tmp_path, monkeypatch):
    source = tmp_path / "portable"
    staged = tmp_path / ".python-runtime.new"
    source.mkdir()
    (source / "python.exe").write_bytes(b"runtime")
    expected = provisioner.tree_inventory(source)
    logger = provisioner.ProvisionLogger(tmp_path / "provision.log")
    real_copytree = provisioner.shutil.copytree
    attempts = []

    def always_corrupted(source_path, destination_path, **kwargs):
        attempts.append(Path(destination_path))
        result = real_copytree(source_path, destination_path, **kwargs)
        (Path(destination_path) / "unexpected.bin").write_bytes(b"persistent-drift")
        return result

    monkeypatch.setattr(provisioner.shutil, "copytree", always_corrupted)

    with pytest.raises(provisioner.ProvisionError) as raised:
        provisioner._copy_runtime_tree_with_retry(
            source, staged, tmp_path, expected, logger, retry_delays=(0, 0)
        )

    assert raised.value.code == "runtime_copy_failed"
    assert len(attempts) == 3
    assert "esperado=" in str(raised.value)
    assert "apos 3 tentativas" in logger.path.read_text(encoding="utf-8")


def test_interrupted_direct_venv_build_without_backup_removes_incomplete_environment(tmp_path):
    destination = tmp_path / ".venv"
    backup = tmp_path / ".venv.previous"
    staged = tmp_path / ".venv.new"
    sentinel = tmp_path / "info" / "python-runtime-venv-build.json"
    destination.mkdir()
    (destination / "partial.txt").write_text("incomplete", encoding="utf-8")
    _write_json(sentinel, {"state": "building", "previous_environment": False})

    provisioner._recover_interrupted_venv(
        destination,
        backup,
        staged,
        sentinel,
        tmp_path,
        provisioner.ProvisionLogger(tmp_path / "provision.log"),
    )

    assert not destination.exists()
    assert not sentinel.exists()


def test_interrupted_venv_before_rename_preserves_preexisting_environment(tmp_path):
    destination = tmp_path / ".venv"
    backup = tmp_path / ".venv.previous"
    staged = tmp_path / ".venv.new"
    sentinel = tmp_path / "info" / "python-runtime-venv-build.json"
    destination.mkdir()
    (destination / "state.txt").write_text("working-previous", encoding="utf-8")
    _write_json(sentinel, {"state": "building", "previous_environment": True})
    logger = provisioner.ProvisionLogger(tmp_path / "provision.log")

    provisioner._recover_interrupted_venv(
        destination,
        backup,
        staged,
        sentinel,
        tmp_path,
        logger,
    )

    assert (destination / "state.txt").read_text(encoding="utf-8") == "working-previous"
    assert not backup.exists()
    assert not sentinel.exists()
    assert "Preservando .venv existente" in logger.path.read_text(encoding="utf-8")


def test_interrupted_direct_venv_build_restores_backup_before_cleaning_sentinel(tmp_path):
    destination = tmp_path / ".venv"
    backup = tmp_path / ".venv.previous"
    staged = tmp_path / ".venv.new"
    sentinel = tmp_path / "info" / "python-runtime-venv-build.json"
    destination.mkdir()
    backup.mkdir()
    (destination / "state.txt").write_text("incomplete-new", encoding="utf-8")
    (backup / "state.txt").write_text("working-previous", encoding="utf-8")
    _write_json(sentinel, {"state": "building", "previous_environment": True})

    provisioner._recover_interrupted_venv(
        destination,
        backup,
        staged,
        sentinel,
        tmp_path,
        provisioner.ProvisionLogger(tmp_path / "provision.log"),
    )

    assert (destination / "state.txt").read_text(encoding="utf-8") == "working-previous"
    assert not backup.exists()
    assert not sentinel.exists()


def test_ready_status_accepts_the_same_ready_state_as_venv_marker(tmp_path):
    _write_json(tmp_path / "runtime-versions.json", _runtime_config())
    spec = provisioner.load_runtime_spec(tmp_path)

    provisioner.write_status(
        tmp_path,
        "ready",
        spec,
        state="ready",
        action="created",
        requirements_sha256="a" * 64,
    )

    status = json.loads((tmp_path / "info" / "python-runtime-status.json").read_text(encoding="utf-8"))
    assert status["state"] == "ready"
    assert status["python_version"] == spec.version
    assert status["python_abi"] == spec.abi


def test_quick_reuse_checks_pinned_runtime_trees_without_wheel_hashes(tmp_path, monkeypatch):
    source, target, spec = _quick_reuse_fixture(tmp_path)
    logger = provisioner.ProvisionLogger(target / "logs" / "provision.log")
    real_sha256_file = provisioner.sha256_file
    hashed_paths = []
    probed = []
    spec_checks = []
    launcher_checks = []
    inventoried = []

    def guarded_hash(path):
        path = Path(path)
        hashed_paths.append(path)
        assert path in {
            source / "requirements.txt",
            source / "python_wheels" / "manifest.json",
            source / "python_runtime" / spec.windows_installer,
        }
        return real_sha256_file(path)

    monkeypatch.setattr(provisioner, "sha256_file", guarded_hash)
    monkeypatch.setattr(
        provisioner,
        "tree_inventory",
        lambda root: inventoried.append(Path(root)) or spec.portable_inventory,
    )
    monkeypatch.setattr(
        provisioner,
        "probe_python",
        lambda executable, *_args, **_kwargs: probed.append(Path(executable)) or {},
    )
    monkeypatch.setattr(
        provisioner,
        "run_quick_dependency_spec_check",
        lambda environment, source_root, *_args, **_kwargs: spec_checks.append(
            (Path(environment), Path(source_root))
        ),
    )
    monkeypatch.setattr(
        provisioner,
        "run_console_entrypoint_checks",
        lambda environment, *_args, **_kwargs: launcher_checks.append(Path(environment)),
    )
    monkeypatch.setattr(
        provisioner,
        "run_health_checks",
        lambda *_args, **_kwargs: pytest.fail("quick reuse must not run heavy import/pip checks"),
    )

    result = provisioner.try_quick_reuse(source, target, spec, logger, 1)

    assert result is not None
    assert result["action"] == "quick_reused"
    assert probed == [target / ".python-runtime" / "python.exe", target / ".venv" / "Scripts" / "python.exe"]
    assert spec_checks == [(target / ".venv", source)]
    assert launcher_checks == [target / ".venv"]
    assert inventoried == [source / "python_runtime" / "portable", target / ".python-runtime"]
    assert set(hashed_paths) == {
        source / "requirements.txt",
        source / "python_wheels" / "manifest.json",
        source / "python_runtime" / spec.windows_installer,
    }
    status = json.loads((target / provisioner.STATUS_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert status["state"] == "ready"
    assert status["action"] == "quick_reused"


def test_quick_reuse_normalizes_generated_runtime_bytecode(tmp_path, monkeypatch):
    source, target, spec = _quick_reuse_fixture(tmp_path)
    runtime = target / ".python-runtime"
    generated = runtime / "Lib" / "__pycache__" / "pathlib.cpython-314.pyc"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"generated-while-app-was-running")
    logger = provisioner.ProvisionLogger(target / "logs" / "provision.log")
    monkeypatch.setattr(provisioner, "probe_python", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(provisioner, "run_quick_dependency_spec_check", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(provisioner, "run_console_entrypoint_checks", lambda *_args, **_kwargs: None)

    result = provisioner.try_quick_reuse(source, target, spec, logger, 1)

    assert result is not None
    assert result["action"] == "quick_reused"
    assert not generated.exists()
    assert provisioner.tree_inventory(runtime) == spec.portable_inventory
    assert "Caches de bytecode removidos" in logger.path.read_text(encoding="utf-8")


def test_quick_reuse_metadata_mismatch_falls_through_to_deep_path(tmp_path, monkeypatch):
    source, target, spec = _quick_reuse_fixture(tmp_path)
    status_path = target / provisioner.STATUS_RELATIVE_PATH
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["wheel_manifest_sha256"] = "f" * 64
    _write_json(status_path, status)
    deep_called = []

    def deep_validation(*_args, **_kwargs):
        deep_called.append(True)
        raise provisioner.ProvisionError("deep_path_reached", "expected test stop")

    monkeypatch.setattr(provisioner, "validate_runtime_source", deep_validation)
    with pytest.raises(provisioner.ProvisionError) as raised:
        provisioner.provision(
            source,
            target,
            spec,
            provisioner.ProvisionLogger(target / "logs" / "provision.log"),
            lock_timeout=0,
            command_timeout=1,
            force=False,
            quick_reuse=True,
        )

    assert raised.value.code == "deep_path_reached"
    assert deep_called == [True]


def test_quick_reuse_missing_critical_package_falls_through_to_deep_path(tmp_path, monkeypatch):
    source, target, spec = _quick_reuse_fixture(tmp_path)
    deep_called = []

    monkeypatch.setattr(provisioner, "probe_python", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(provisioner, "run_console_entrypoint_checks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        provisioner,
        "run_quick_dependency_spec_check",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            provisioner.ProvisionError("quick_dependency_missing", "fastapi ausente")
        ),
    )

    def deep_validation(*_args, **_kwargs):
        deep_called.append(True)
        raise provisioner.ProvisionError("deep_path_reached", "expected test stop")

    monkeypatch.setattr(provisioner, "validate_runtime_source", deep_validation)
    with pytest.raises(provisioner.ProvisionError) as raised:
        provisioner.provision(
            source,
            target,
            spec,
            provisioner.ProvisionLogger(target / "logs" / "provision.log"),
            lock_timeout=0,
            command_timeout=1,
            force=False,
            quick_reuse=True,
        )

    assert raised.value.code == "deep_path_reached"
    assert deep_called == [True]


def test_console_entrypoint_checks_execute_windows_launchers_directly(tmp_path, monkeypatch):
    environment = tmp_path / ".venv"
    scripts = environment / "Scripts"
    scripts.mkdir(parents=True)
    pip_exe = scripts / "pip.exe"
    uvicorn_exe = scripts / "uvicorn.exe"
    pip_exe.write_bytes(b"launcher")
    uvicorn_exe.write_bytes(b"launcher")
    commands = []

    monkeypatch.setattr(
        provisioner,
        "run_command",
        lambda command, *_args, **_kwargs: commands.append([str(value) for value in command]) or "",
    )

    provisioner.run_console_entrypoint_checks(
        environment,
        tmp_path,
        provisioner.ProvisionLogger(tmp_path / "provision.log"),
        1,
    )

    assert commands == [[str(pip_exe), "--isolated", "--version"], [str(uvicorn_exe), "--version"]]


def test_provision_lock_rejects_concurrent_writer(tmp_path):
    logger = provisioner.ProvisionLogger(tmp_path / "provision.log")
    lock_path = tmp_path / provisioner.LOCK_NAME

    with provisioner.ProvisionLock(lock_path, logger, timeout=0.1):
        with pytest.raises(provisioner.ProvisionError) as raised:
            with provisioner.ProvisionLock(lock_path, logger, timeout=0.0):
                pass
        assert raised.value.code == "lock_timeout"

    assert not lock_path.exists()


def test_provision_lock_recovers_when_owner_pid_is_stale(tmp_path):
    logger = provisioner.ProvisionLogger(tmp_path / "provision.log")
    lock_path = tmp_path / provisioner.LOCK_NAME
    _write_json(
        lock_path,
        {
            "schema_version": 1,
            "pid": 999999,
            "token": "stale-owner",
            "created_unix": 1,
        },
    )

    with provisioner.ProvisionLock(lock_path, logger, timeout=0.1):
        current = json.loads(lock_path.read_text(encoding="utf-8"))
        assert current["pid"] == provisioner.os.getpid()
        assert current["token"] != "stale-owner"

    assert not lock_path.exists()


@pytest.mark.skipif(provisioner.os.name != "nt", reason="probe Win32 especifico")
def test_windows_process_probe_never_signals_the_process():
    child = provisioner.subprocess.Popen(
        [provisioner.sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=provisioner.subprocess.DEVNULL,
        stderr=provisioner.subprocess.DEVNULL,
    )
    try:
        assert provisioner._process_exists(child.pid) is True
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_cli_failure_writes_machine_readable_status(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    _write_json(source / "runtime-versions.json", _runtime_config())

    exit_code = provisioner.main(
        [
            "--source-root",
            str(source),
            "--target-root",
            str(target),
            "--log-file",
            str(target / "logs" / "provision.log"),
            "--lock-timeout",
            "0",
        ]
    )

    assert exit_code == 1
    status = json.loads((target / "info" / "python-runtime-status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["error_code"] == "runtime_manifest_missing"
    assert status["python_version"] == "9.8.7"
    assert not (target / provisioner.LOCK_NAME).exists()


def test_cli_venv_in_use_status_confirms_previous_environment_was_preserved(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    target = tmp_path / "target"
    _write_json(source / "runtime-versions.json", _runtime_config())

    def fail_with_busy_venv(*_args, **_kwargs):
        raise provisioner.ProvisionError("venv_in_use", "ambiente em uso")

    monkeypatch.setattr(provisioner, "provision", fail_with_busy_venv)

    exit_code = provisioner.main(
        [
            "--source-root",
            str(source),
            "--target-root",
            str(target),
            "--log-file",
            str(target / "logs" / "provision.log"),
        ]
    )

    assert exit_code == 1
    status = json.loads((target / provisioner.STATUS_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert status["error_code"] == "venv_in_use"
    assert status["previous_environment_preserved"] is True


def test_runtime_source_rejects_an_old_python_installer(tmp_path, monkeypatch):
    runtime_root = tmp_path / "python_runtime"
    portable = runtime_root / "portable"
    portable.mkdir(parents=True)
    (portable / "python.exe").write_bytes(b"fake")
    installer = runtime_root / "python-9.8.7-amd64.exe"
    installer.write_bytes(b"configured-installer")
    (runtime_root / "python-old-amd64.exe").write_bytes(b"obsolete")
    file_count, total_size, tree_hash = provisioner.tree_inventory(portable)
    _write_json(
        tmp_path / "runtime-versions.json",
        _runtime_config(
            installer_size=installer.stat().st_size,
            installer_sha256=provisioner.sha256_file(installer),
            portable_file_count=file_count,
            portable_total_size=total_size,
            portable_tree_sha256=tree_hash,
        ),
    )
    spec = provisioner.load_runtime_spec(tmp_path)
    _write_json(
        tmp_path / "runtime-manifest.json",
        {
            "version": "test",
            "python": {
                "version": spec.version,
                "abi": spec.abi,
                "installer": {
                    "path": installer.name,
                    "size": installer.stat().st_size,
                    "sha256": provisioner.sha256_file(installer),
                },
                "portable": {
                    "path": "portable",
                    "file_count": file_count,
                    "total_size": total_size,
                    "tree_sha256": tree_hash,
                },
            },
        },
    )
    monkeypatch.setattr(provisioner, "probe_python", lambda *_args, **_kwargs: {})

    with pytest.raises(provisioner.ProvisionError) as raised:
        provisioner.validate_runtime_source(tmp_path, spec, provisioner.ProvisionLogger(tmp_path / "log"), 1)

    assert raised.value.code == "unexpected_python_installer"


def test_runtime_source_rejects_self_consistent_manifest_that_diverges_from_central_pin(
    tmp_path, monkeypatch
):
    runtime_root = tmp_path / "python_runtime"
    portable = runtime_root / "portable"
    portable.mkdir(parents=True)
    (portable / "python.exe").write_bytes(b"fake")
    installer = runtime_root / "python-9.8.7-amd64.exe"
    installer.write_bytes(b"configured-installer")
    inventory = provisioner.tree_inventory(portable)
    _write_json(
        tmp_path / "runtime-versions.json",
        _runtime_config(
            installer_size=installer.stat().st_size,
            installer_sha256=provisioner.sha256_file(installer),
            portable_file_count=inventory[0],
            portable_total_size=inventory[1],
            portable_tree_sha256=inventory[2],
        ),
    )
    spec = provisioner.load_runtime_spec(tmp_path)
    _write_json(
        tmp_path / "runtime-manifest.json",
        {
            "version": "test",
            "python": {
                "version": spec.version,
                "abi": spec.abi,
                "installer": {
                    "path": installer.name,
                    "size": installer.stat().st_size,
                    "sha256": "f" * 64,
                },
                "portable": {
                    "path": "portable",
                    "file_count": inventory[0],
                    "total_size": inventory[1],
                    "tree_sha256": inventory[2],
                },
            },
        },
    )
    monkeypatch.setattr(provisioner, "probe_python", lambda *_args, **_kwargs: {})

    with pytest.raises(provisioner.ProvisionError) as raised:
        provisioner.validate_runtime_source(
            tmp_path, spec, provisioner.ProvisionLogger(tmp_path / "log"), 1
        )

    assert raised.value.code == "runtime_manifest_mismatch"


def test_server_launchers_share_scoped_venv_cleanup():
    repo_root = Path(__file__).resolve().parents[1]
    cleanup_blocks = []
    for name in ("iniciar_servidor.bat", "iniciar_servidor_dev.bat"):
        content = (repo_root / name).read_text(encoding="utf-8")
        start = content.index("echo 0. Limpando processos antigos do JK Sistema...")
        end = content.index("timeout /t 2 >nul", start)
        cleanup_blocks.append(content[start:end])

    assert cleanup_blocks[0] == cleanup_blocks[1]
    cleanup = cleanup_blocks[0]
    assert "StartsWith($venvPrefixLower)" in cleanup
    assert "StartsWith($runtimePrefixLower)" in cleanup
    assert "Test-OwnPrivatePython" in cleanup
    assert "Wait-Process" in cleanup
    assert "Stop-JkProcess $owner ('processo na porta ' + $port)" in cleanup
    assert "processo preservado" not in cleanup
    assert "uvicorn backend_api:app" not in cleanup


def test_server_launchers_pin_electron_and_backend_to_checkout():
    repo_root = Path(__file__).resolve().parents[1]
    for name in ("iniciar_servidor.bat", "iniciar_servidor_dev.bat"):
        content = (repo_root / name).read_text(encoding="utf-8")

        assert 'set "JK_APP_ROOT_DIR=%~dp0"' in content
        assert 'set "JK_LOCAL_BACKEND_SOURCE_DIR=%~dp0"' in content
        assert 'set "JK_LOCAL_BACKEND_DIR=%~dp0"' in content
        assert 'set "JK_APP_VERSION="' in content
        assert "Get-Content -Raw -LiteralPath '%~dp0package.json'" in content
        assert 'do set "JK_APP_VERSION=%%V"' in content
        assert "Nao foi possivel ler a versao local em package.json" in content
        assert 'set "JK_FIREBASE_LIVE_FEATURES=true"' in content
        assert 'set "FIREBASE_LIVE_FEATURES=true"' in content
        assert 'set "JK_FIREBASE_CHAT_PRESENCE_ENABLED=true"' in content
        assert 'set "JK_LOCAL_SERVERS_PREPARED_BY_LAUNCHER=1"' in content
