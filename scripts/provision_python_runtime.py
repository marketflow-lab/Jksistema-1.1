from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
import traceback
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


LOCK_NAME = ".python-runtime-provision.lock"
VENV_MARKER_NAME = ".jk-venv-ready.json"
STATUS_RELATIVE_PATH = Path("info") / "python-runtime-status.json"
VENV_BUILD_SENTINEL_RELATIVE_PATH = Path("info") / "python-runtime-venv-build.json"
CRITICAL_IMPORTS = (
    "av",
    "fastapi",
    "faster_whisper",
    "openai_codex",
    "pandas",
    "playwright",
    "psycopg",
    "selenium",
    "uvicorn",
    "websockets",
)
QUICK_REUSE_METADATA_FIELDS = (
    "state",
    "python_version",
    "python_abi",
    "runtime_tree_sha256",
    "requirements_sha256",
    "wheel_manifest_sha256",
    "wheel_count",
)


@dataclass(frozen=True)
class RuntimeSpec:
    version: str
    minor: str
    implementation: str
    abi: str
    windows_architecture: str
    windows_installer: str
    windows_installer_size: int
    windows_installer_sha256: str
    windows_portable_path: str
    windows_portable_file_count: int
    windows_portable_total_size: int
    windows_portable_tree_sha256: str

    @property
    def portable_inventory(self) -> tuple[int, int, str]:
        return (
            self.windows_portable_file_count,
            self.windows_portable_total_size,
            self.windows_portable_tree_sha256,
        )

    @property
    def expected_bits(self) -> int:
        bits_by_architecture = {"amd64": 64, "arm64": 64, "x86": 32}
        try:
            return bits_by_architecture[self.windows_architecture]
        except KeyError as exc:
            raise ProvisionError(
                "runtime_config_invalid",
                f"Arquitetura Windows nao suportada: {self.windows_architecture}",
            ) from exc

    @property
    def implementation_name(self) -> str:
        names = {"cp": "cpython"}
        try:
            return names[self.implementation]
        except KeyError as exc:
            raise ProvisionError(
                "runtime_config_invalid",
                f"Implementacao Python nao suportada: {self.implementation}",
            ) from exc


def load_runtime_spec(source_root: Path) -> RuntimeSpec:
    config_path = source_root / "runtime-versions.json"
    config = read_json(config_path, code="runtime_config_invalid")
    python = config.get("python")
    if config.get("schemaVersion") != 1 or not isinstance(python, dict):
        raise ProvisionError("runtime_config_invalid", f"Configuracao de runtime invalida: {config_path}")
    portable = python.get("windowsPortable")
    if not isinstance(portable, dict):
        raise ProvisionError(
            "runtime_config_invalid",
            "runtime-versions.json deve fixar a arvore em python.windowsPortable.",
        )
    fields = {
        "version": python.get("version"),
        "minor": python.get("minor"),
        "implementation": python.get("implementation"),
        "abi": python.get("abi"),
        "windows_architecture": python.get("windowsArchitecture"),
        "windows_installer": python.get("windowsInstaller"),
        "windows_installer_sha256": python.get("windowsInstallerSha256"),
        "windows_portable_path": portable.get("path"),
        "windows_portable_tree_sha256": portable.get("treeSha256"),
    }
    normalized = {name: str(value or "").strip() for name, value in fields.items()}
    if any(not value for value in normalized.values()):
        raise ProvisionError("runtime_config_invalid", "runtime-versions.json possui campos Python ausentes.")
    parts = normalized["version"].split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ProvisionError("runtime_config_invalid", f"Versao Python invalida: {normalized['version']}")
    derived_minor = ".".join(parts[:2])
    derived_abi = f"{normalized['implementation']}{parts[0]}{parts[1]}"
    if normalized["minor"] != derived_minor or normalized["abi"] != derived_abi:
        raise ProvisionError(
            "runtime_config_invalid",
            f"Minor/ABI divergentes da versao: {normalized['minor']}/{normalized['abi']}",
        )
    installer = normalized["windows_installer"]
    if Path(installer).name != installer or not installer.lower().endswith(".exe"):
        raise ProvisionError("runtime_config_invalid", f"Nome do instalador Python invalido: {installer}")
    for field in ("windows_installer_sha256", "windows_portable_tree_sha256"):
        value = normalized[field].lower()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ProvisionError("runtime_config_invalid", f"SHA256 invalido em {field}.")
        normalized[field] = value
    if normalized["windows_portable_path"] != "portable":
        raise ProvisionError(
            "runtime_config_invalid",
            "python.windowsPortable.path deve ser exatamente portable.",
        )
    numeric_fields = {
        "windows_installer_size": python.get("windowsInstallerSize"),
        "windows_portable_file_count": portable.get("fileCount"),
        "windows_portable_total_size": portable.get("totalSize"),
    }
    for field, value in numeric_fields.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ProvisionError("runtime_config_invalid", f"Inteiro positivo invalido em {field}.")
    spec = RuntimeSpec(**normalized, **numeric_fields)
    spec.expected_bits
    spec.implementation_name
    return spec


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ProvisionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProvisionLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, message: str) -> None:
        line = f"[{utc_now()}] {message}"
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            # Consoles legados do Windows podem usar cp1252 mesmo quando a saida
            # capturada contem caracteres Unicode. Preserve o log UTF-8 e use uma
            # representacao escapada no console, sem esconder a falha original.
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            safe_line = line.encode(encoding, errors="backslashreplace").decode(encoding)
            print(safe_line, flush=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    try:
        value = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(value & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def tree_inventory(root: Path) -> tuple[int, int, str]:
    """Return a deterministic inventory for a directory without following links.

    The tree digest is SHA256 over sorted records in this form:
    ``relative/path\\0size\\0file_sha256\\n``.
    """

    if not root.is_dir():
        raise ProvisionError("portable_missing", f"Diretorio do runtime portatil ausente: {root}")
    records: list[tuple[str, int, str]] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            candidate = current_path / name
            if candidate.is_symlink() or _is_reparse_point(candidate):
                raise ProvisionError("unsafe_runtime_tree", f"Link/reparse point proibido no runtime: {candidate}")
        for name in files:
            candidate = current_path / name
            if candidate.is_symlink() or _is_reparse_point(candidate):
                raise ProvisionError("unsafe_runtime_tree", f"Link/reparse point proibido no runtime: {candidate}")
            if not candidate.is_file():
                raise ProvisionError("unsafe_runtime_tree", f"Entrada nao regular no runtime: {candidate}")
            relative = candidate.relative_to(root).as_posix()
            records.append((relative, candidate.stat().st_size, sha256_file(candidate)))
    records.sort(key=lambda item: item[0])
    digest = hashlib.sha256()
    total_size = 0
    for relative, size, file_digest in records:
        total_size += size
        digest.update(f"{relative}\0{size}\0{file_digest}\n".encode("utf-8"))
    return len(records), total_size, digest.hexdigest()


def _resolved_child(parent: Path, relative: str, label: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise ProvisionError("invalid_manifest", f"Caminho invalido para {label}: {relative!r}")
    parent_resolved = parent.resolve()
    child = (parent_resolved / relative).resolve()
    try:
        child.relative_to(parent_resolved)
    except ValueError as exc:
        raise ProvisionError("invalid_manifest", f"Caminho fora da raiz para {label}: {relative}") from exc
    return child


def _safe_remove(path: Path, parent: Path) -> None:
    parent_resolved = parent.resolve()
    candidate = path.resolve(strict=False)
    try:
        relative = candidate.relative_to(parent_resolved)
    except ValueError as exc:
        raise ProvisionError("unsafe_path", f"Recusa ao remover caminho fora do destino: {candidate}") from exc
    if not relative.parts:
        raise ProvisionError("unsafe_path", f"Recusa ao remover a raiz de destino: {candidate}")
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or _is_reparse_point(path) or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_json(path: Path, code: str = "invalid_manifest") -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvisionError(code, f"JSON invalido ou ilegivel em {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProvisionError(code, f"Objeto JSON esperado em {path}")
    return value


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) is not a harmless existence probe on Windows. Use a
        # query-only process handle so lock recovery can never signal its owner.
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        error_access_denied = 5
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_exit_code.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        handle = open_process(process_query_limited_information, False, pid)
        if not handle:
            return ctypes.get_last_error() == error_access_denied
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            close_handle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False
    return True


class ProvisionLock(AbstractContextManager["ProvisionLock"]):
    def __init__(self, path: Path, logger: ProvisionLogger, timeout: float, stale_after: float = 1800.0) -> None:
        self.path = path
        self.logger = logger
        self.timeout = timeout
        self.stale_after = stale_after
        self.token = uuid.uuid4().hex
        self.acquired = False

    def _remove_stale_lock(self) -> bool:
        try:
            current = read_json(self.path, code="lock_invalid")
        except ProvisionError:
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                return False
            if age <= self.stale_after:
                return False
            current = {}
        pid = int(current.get("pid") or 0)
        created = float(current.get("created_unix") or 0)
        token = str(current.get("token") or "")
        stale = (pid and not _process_exists(pid)) or (not pid and time.time() - created > self.stale_after)
        if not stale:
            return False
        try:
            latest = read_json(self.path, code="lock_invalid")
            if token and latest.get("token") != token:
                return False
        except ProvisionError:
            pass
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self.logger.write(f"Lock obsoleto removido: {self.path}")
        return True

    def __enter__(self) -> "ProvisionLock":
        deadline = time.monotonic() + self.timeout
        payload = {
            "schema_version": 1,
            "pid": os.getpid(),
            "token": self.token,
            "created_at": utc_now(),
            "created_unix": time.time(),
        }
        encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        while True:
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                self._remove_stale_lock()
                if time.monotonic() >= deadline:
                    raise ProvisionError("lock_timeout", "Outro provisionamento do Python continua em execucao.")
                time.sleep(0.25)
                continue
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self.acquired = True
            self.logger.write(f"Lock adquirido: {self.path}")
            return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if not self.acquired:
            return
        try:
            current = read_json(self.path, code="lock_invalid")
            if current.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except (OSError, ProvisionError):
            pass
        self.acquired = False


def run_command(
    command: Sequence[str | os.PathLike[str]],
    logger: ProvisionLogger,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 1800.0,
    error_code: str = "command_failed",
) -> str:
    rendered = [os.fspath(value) for value in command]
    logger.write(f"Executando: {subprocess.list2cmdline(rendered)}")
    poison_names = {
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONUSERBASE",
        "VIRTUAL_ENV",
        "__PYVENV_LAUNCHER__",
    }
    process_env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in poison_names and not key.upper().startswith("PIP_")
    }
    safe_environment = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "PIP_CONFIG_FILE": "NUL" if os.name == "nt" else os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "PIP_NO_INDEX": "1",
    }
    process_env.update(safe_environment)
    if env:
        for key, value in env.items():
            normalized = key.upper()
            if normalized in poison_names or normalized.startswith("PIP_"):
                if normalized not in safe_environment or str(value) != safe_environment[normalized]:
                    raise ProvisionError(
                        "unsafe_command_environment",
                        f"Variavel de ambiente insegura recusada: {key}",
                    )
                continue
            process_env[key] = value
    try:
        completed = subprocess.run(
            rendered,
            cwd=os.fspath(cwd) if cwd else None,
            env=process_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProvisionError(error_code, f"Comando excedeu {timeout:.0f}s: {rendered[0]}") from exc
    output = completed.stdout or ""
    for line in output.rstrip().splitlines():
        logger.write(f"  {line}")
    if completed.returncode != 0:
        tail = " | ".join(output.strip().splitlines()[-8:])
        raise ProvisionError(
            error_code,
            f"Comando retornou codigo {completed.returncode}: {rendered[0]}{': ' + tail if tail else ''}",
        )
    return output


def python_executable(root: Path) -> Path:
    windows = root / "python.exe"
    return windows if windows.is_file() else root / "bin" / "python"


def venv_python(root: Path) -> Path:
    windows = root / "Scripts" / "python.exe"
    return windows if windows.is_file() else root / "bin" / "python"


def venv_console_script(root: Path, name: str) -> Path:
    windows = root / "Scripts" / f"{name}.exe"
    return windows if windows.is_file() else root / "bin" / name


def probe_python(
    executable: Path, spec: RuntimeSpec, logger: ProvisionLogger, timeout: float
) -> dict[str, Any]:
    if not executable.is_file():
        raise ProvisionError("python_missing", f"Executavel Python ausente: {executable}")
    script = (
        "import ensurepip,json,platform,struct,sys,venv;"
        "print(json.dumps({'version':platform.python_version(),"
        "'abi':f'cp{sys.version_info.major}{sys.version_info.minor}',"
        "'implementation':sys.implementation.name,'bits':struct.calcsize('P')*8}))"
    )
    output = run_command(
        [executable, "-B", "-I", "-c", script],
        logger,
        timeout=timeout,
        error_code="python_probe_failed",
    )
    try:
        probe = json.loads(output.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ProvisionError("python_probe_failed", f"Resposta invalida do Python em {executable}") from exc
    if probe.get("version") != spec.version:
        raise ProvisionError(
            "python_version_mismatch",
            f"Python {spec.version} esperado, encontrado {probe.get('version') or 'desconhecido'}.",
        )
    if probe.get("abi") != spec.abi or probe.get("implementation") != spec.implementation_name:
        raise ProvisionError("python_abi_mismatch", f"ABI {spec.abi} esperada, encontrada {probe}.")
    if int(probe.get("bits") or 0) != spec.expected_bits:
        raise ProvisionError(
            "python_arch_mismatch",
            f"O runtime Python nao corresponde a arquitetura {spec.windows_architecture}.",
        )
    return probe


def locate_runtime_manifest(source_root: Path) -> Path:
    candidates = (
        source_root / "runtime-manifest.json",
        source_root / ".installer_runtime" / "runtime-manifest.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ProvisionError("runtime_manifest_missing", f"runtime-manifest.json ausente em {source_root}")


def validate_runtime_source(
    source_root: Path, spec: RuntimeSpec, logger: ProvisionLogger, timeout: float
) -> tuple[dict[str, Any], Path, tuple[int, int, str]]:
    manifest = read_json(locate_runtime_manifest(source_root))
    python = manifest.get("python")
    if not isinstance(python, dict):
        raise ProvisionError("invalid_manifest", "Secao python ausente no runtime-manifest.json.")
    if python.get("version") != spec.version or python.get("abi") != spec.abi:
        raise ProvisionError(
            "runtime_manifest_mismatch",
            f"Manifesto requer Python {python.get('version')}/{python.get('abi')}; "
            f"esperado {spec.version}/{spec.abi}.",
        )
    installer = python.get("installer")
    if not isinstance(installer, dict):
        raise ProvisionError("invalid_manifest", "Entrada python.installer ausente no manifesto.")
    installer_path = _resolved_child(
        source_root / "python_runtime", str(installer.get("path") or ""), "python.installer"
    )
    if installer_path.name != spec.windows_installer or installer_path.parent != (source_root / "python_runtime").resolve():
        raise ProvisionError("runtime_manifest_mismatch", "Instalador Python do manifesto diverge da configuracao central.")
    manifest_installer_pin = (
        int(installer.get("size") or -1),
        str(installer.get("sha256") or "").lower(),
    )
    expected_installer_pin = (spec.windows_installer_size, spec.windows_installer_sha256)
    if manifest_installer_pin != expected_installer_pin:
        raise ProvisionError(
            "runtime_manifest_mismatch",
            "Pin do instalador Python no manifesto diverge de runtime-versions.json.",
        )
    if not installer_path.is_file():
        raise ProvisionError("python_installer_missing", f"Instalador Python ausente: {installer_path}")
    if (
        installer_path.stat().st_size != spec.windows_installer_size
        or sha256_file(installer_path) != spec.windows_installer_sha256
    ):
        raise ProvisionError("python_installer_integrity_failed", "Instalador Python diverge do manifesto.")
    packaged_installers = {
        item.name for item in (source_root / "python_runtime").glob("python-*.exe") if item.is_file()
    }
    if packaged_installers != {spec.windows_installer}:
        raise ProvisionError(
            "unexpected_python_installer",
            f"python_runtime contem instaladores inesperados: {sorted(packaged_installers)}",
        )
    portable = python.get("portable")
    if not isinstance(portable, dict):
        raise ProvisionError("invalid_manifest", "Entrada python.portable ausente no manifesto.")
    portable_root = _resolved_child(source_root / "python_runtime", str(portable.get("path") or ""), "python.portable")
    if str(portable.get("path") or "") != spec.windows_portable_path:
        raise ProvisionError(
            "runtime_manifest_mismatch",
            "Caminho do runtime portatil diverge de runtime-versions.json.",
        )
    inventory = tree_inventory(portable_root)
    expected_inventory = (
        int(portable.get("file_count") or -1),
        int(portable.get("total_size") or -1),
        str(portable.get("tree_sha256") or "").lower(),
    )
    if expected_inventory != spec.portable_inventory:
        raise ProvisionError(
            "runtime_manifest_mismatch",
            "Pin da arvore Python portatil no manifesto diverge de runtime-versions.json.",
        )
    if inventory != spec.portable_inventory:
        raise ProvisionError(
            "portable_integrity_failed",
            "Runtime portatil diverge do manifesto "
            f"(esperado {spec.portable_inventory}, encontrado {inventory}).",
        )
    probe_python(python_executable(portable_root), spec, logger, timeout)
    return manifest, portable_root, inventory


def validate_wheelhouse(source_root: Path, spec: RuntimeSpec) -> dict[str, Any]:
    requirements = source_root / "requirements.txt"
    wheel_root = source_root / "python_wheels"
    wheel_manifest_path = wheel_root / "manifest.json"
    if not requirements.is_file():
        raise ProvisionError("requirements_missing", f"requirements.txt ausente em {source_root}")
    if not wheel_root.is_dir() or not wheel_manifest_path.is_file():
        raise ProvisionError("wheelhouse_missing", f"Wheelhouse/manifesto ausente em {wheel_root}")
    manifest = read_json(wheel_manifest_path, code="wheel_manifest_invalid")
    requirements_hash = sha256_file(requirements)
    if str(manifest.get("requirements_sha256") or "").lower() != requirements_hash:
        raise ProvisionError("requirements_hash_mismatch", "Wheelhouse nao corresponde ao requirements.txt empacotado.")
    if manifest.get("python_version") != spec.minor:
        raise ProvisionError(
            "wheelhouse_target_mismatch", f"Wheelhouse nao foi gerado para Python {spec.minor}."
        )
    if manifest.get("abi") != spec.abi:
        raise ProvisionError("wheelhouse_target_mismatch", f"Wheelhouse nao foi gerado para {spec.abi}.")
    entries = manifest.get("wheels")
    if not isinstance(entries, list) or not entries:
        raise ProvisionError("wheel_manifest_invalid", "Manifesto do wheelhouse esta vazio.")
    declared: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ProvisionError("wheel_manifest_invalid", "Entrada invalida no manifesto do wheelhouse.")
        relative = str(entry.get("path") or entry.get("name") or "")
        wheel = _resolved_child(wheel_root, relative, "wheel")
        if wheel.parent != wheel_root.resolve() or wheel.suffix.lower() != ".whl" or not wheel.is_file():
            raise ProvisionError("wheel_manifest_invalid", f"Wheel invalida no manifesto: {relative}")
        expected_size = int(entry.get("size") or -1)
        expected_hash = str(entry.get("sha256") or "").lower()
        if wheel.stat().st_size != expected_size or sha256_file(wheel) != expected_hash:
            raise ProvisionError("wheel_integrity_failed", f"Wheel corrompida ou divergente: {relative}")
        declared.add(wheel.name)
    actual = {path.name for path in wheel_root.glob("*.whl") if path.is_file()}
    if actual != declared:
        missing = sorted(declared - actual)
        extra = sorted(actual - declared)
        raise ProvisionError("wheel_manifest_mismatch", f"Inventario de wheels divergente; ausentes={missing}, extras={extra}")
    return {
        "requirements_path": requirements,
        "requirements_sha256": requirements_hash,
        "wheel_root": wheel_root,
        "wheel_manifest_sha256": sha256_file(wheel_manifest_path),
        "wheel_count": len(entries),
    }


def _manifest_sha256(value: Any, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ProvisionError("invalid_manifest", f"SHA256 invalido para {label}.")
    return normalized


def load_quick_reuse_metadata(
    source_root: Path, spec: RuntimeSpec
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load package metadata while enforcing the immutable runtime pins.

    The installer and packaged portable tree are hashed here. Individual wheels,
    pip health and imports remain the deep path's responsibility.
    """

    runtime_manifest = read_json(locate_runtime_manifest(source_root))
    python = runtime_manifest.get("python")
    if not isinstance(python, dict):
        raise ProvisionError("invalid_manifest", "Secao python ausente no runtime-manifest.json.")
    if python.get("version") != spec.version or python.get("abi") != spec.abi:
        raise ProvisionError(
            "runtime_manifest_mismatch",
            f"Manifesto requer Python {python.get('version')}/{python.get('abi')}; "
            f"esperado {spec.version}/{spec.abi}.",
        )

    installer = python.get("installer")
    if not isinstance(installer, dict):
        raise ProvisionError("invalid_manifest", "Entrada python.installer ausente no manifesto.")
    installer_path = _resolved_child(
        source_root / "python_runtime", str(installer.get("path") or ""), "python.installer"
    )
    if installer_path.name != spec.windows_installer or installer_path.parent != (
        source_root / "python_runtime"
    ).resolve():
        raise ProvisionError("runtime_manifest_mismatch", "Instalador Python diverge da configuracao central.")
    manifest_installer_pin = (
        int(installer.get("size") or -1),
        _manifest_sha256(installer.get("sha256"), "python.installer"),
    )
    if manifest_installer_pin != (spec.windows_installer_size, spec.windows_installer_sha256):
        raise ProvisionError(
            "runtime_manifest_mismatch",
            "Pin do instalador Python diverge de runtime-versions.json.",
        )
    if not installer_path.is_file() or installer_path.stat().st_size != spec.windows_installer_size:
        raise ProvisionError("python_installer_missing", f"Instalador Python ausente ou truncado: {installer_path}")
    if sha256_file(installer_path) != spec.windows_installer_sha256:
        raise ProvisionError("python_installer_integrity_failed", "Instalador Python diverge do pin central.")
    packaged_installers = {
        item.name for item in (source_root / "python_runtime").glob("python-*.exe") if item.is_file()
    }
    if packaged_installers != {spec.windows_installer}:
        raise ProvisionError(
            "unexpected_python_installer",
            f"python_runtime contem instaladores inesperados: {sorted(packaged_installers)}",
        )

    portable = python.get("portable")
    if not isinstance(portable, dict):
        raise ProvisionError("invalid_manifest", "Entrada python.portable ausente no manifesto.")
    portable_root = _resolved_child(
        source_root / "python_runtime", str(portable.get("path") or ""), "python.portable"
    )
    if str(portable.get("path") or "") != spec.windows_portable_path:
        raise ProvisionError(
            "runtime_manifest_mismatch",
            "Caminho do runtime portatil diverge de runtime-versions.json.",
        )
    if (
        not portable_root.is_dir()
        or portable_root.is_symlink()
        or _is_reparse_point(portable_root)
        or not python_executable(portable_root).is_file()
    ):
        raise ProvisionError("portable_missing", f"Runtime portatil ausente ou inseguro: {portable_root}")
    portable_summary = {
        "file_count": int(portable.get("file_count") or -1),
        "total_size": int(portable.get("total_size") or -1),
        "tree_sha256": _manifest_sha256(portable.get("tree_sha256"), "python.portable"),
    }
    if portable_summary["file_count"] <= 0 or portable_summary["total_size"] <= 0:
        raise ProvisionError("invalid_manifest", "Metadados do runtime portatil sao invalidos.")
    manifest_portable_pin = (
        portable_summary["file_count"],
        portable_summary["total_size"],
        portable_summary["tree_sha256"],
    )
    if manifest_portable_pin != spec.portable_inventory:
        raise ProvisionError(
            "runtime_manifest_mismatch",
            "Pin da arvore Python portatil diverge de runtime-versions.json.",
        )
    source_inventory = tree_inventory(portable_root)
    if source_inventory != spec.portable_inventory:
        raise ProvisionError(
            "portable_integrity_failed",
            "Runtime portatil empacotado diverge do pin central.",
        )

    requirements = source_root / "requirements.txt"
    wheel_root = source_root / "python_wheels"
    wheel_manifest_path = wheel_root / "manifest.json"
    if not requirements.is_file():
        raise ProvisionError("requirements_missing", f"requirements.txt ausente em {source_root}")
    if not wheel_root.is_dir() or not wheel_manifest_path.is_file():
        raise ProvisionError("wheelhouse_missing", f"Wheelhouse/manifesto ausente em {wheel_root}")
    wheel_manifest = read_json(wheel_manifest_path, code="wheel_manifest_invalid")
    if wheel_manifest.get("python_version") != spec.minor or wheel_manifest.get("abi") != spec.abi:
        raise ProvisionError("wheelhouse_target_mismatch", f"Wheelhouse nao foi gerado para {spec.abi}.")
    requirements_hash = sha256_file(requirements)
    if _manifest_sha256(wheel_manifest.get("requirements_sha256"), "requirements") != requirements_hash:
        raise ProvisionError("requirements_hash_mismatch", "Wheelhouse diverge do requirements.txt empacotado.")
    entries = wheel_manifest.get("wheels")
    if not isinstance(entries, list) or not entries:
        raise ProvisionError("wheel_manifest_invalid", "Manifesto do wheelhouse esta vazio.")
    declared: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ProvisionError("wheel_manifest_invalid", "Entrada invalida no manifesto do wheelhouse.")
        relative = str(entry.get("path") or entry.get("name") or "")
        wheel = _resolved_child(wheel_root, relative, "wheel")
        if (
            wheel.parent != wheel_root.resolve()
            or wheel.suffix.lower() != ".whl"
            or not wheel.is_file()
            or wheel.stat().st_size != int(entry.get("size") or -1)
        ):
            raise ProvisionError("wheel_manifest_invalid", f"Wheel ausente ou truncada: {relative}")
        _manifest_sha256(entry.get("sha256"), f"wheel {relative}")
        if wheel.name in declared:
            raise ProvisionError("wheel_manifest_invalid", f"Wheel duplicada no manifesto: {relative}")
        declared.add(wheel.name)
    actual = {path.name for path in wheel_root.glob("*.whl") if path.is_file()}
    if actual != declared:
        raise ProvisionError("wheel_manifest_mismatch", "Inventario nominal do wheelhouse diverge do manifesto.")

    wheelhouse = {
        "requirements_sha256": requirements_hash,
        # Hashing the small manifest binds marker/status to this exact package;
        # individual wheel contents remain a deep-path responsibility.
        "wheel_manifest_sha256": sha256_file(wheel_manifest_path),
        "wheel_count": len(entries),
    }
    return runtime_manifest, _marker_payload(portable_summary, wheelhouse, spec)


def _marker_payload(
    runtime: dict[str, Any], wheelhouse: dict[str, Any], spec: RuntimeSpec
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "state": "ready",
        "created_at": utc_now(),
        "python_version": spec.version,
        "python_abi": spec.abi,
        "runtime_tree_sha256": runtime["tree_sha256"],
        "requirements_sha256": wheelhouse["requirements_sha256"],
        "wheel_manifest_sha256": wheelhouse["wheel_manifest_sha256"],
        "wheel_count": wheelhouse["wheel_count"],
    }


def run_health_checks(
    environment: Path,
    spec: RuntimeSpec,
    source_root: Path,
    target_root: Path,
    app_version: str,
    logger: ProvisionLogger,
    timeout: float,
) -> None:
    executable = venv_python(environment)
    probe_python(executable, spec, logger, timeout)
    run_command(
        [executable, "-B", "-I", "-m", "pip", "--isolated", "check"],
        logger,
        cwd=source_root,
        timeout=timeout,
        error_code="pip_check_failed",
    )
    modules = repr(CRITICAL_IMPORTS)
    source = repr(os.fspath(source_root))
    import_script = (
        "import importlib,sys;"
        f"sys.path.insert(0,{source});"
        f"[importlib.import_module(name) for name in {modules}];"
        "import backend_api;"
        "assert getattr(backend_api,'app',None) is not None;"
        "print('critical-imports-ok')"
    )
    run_command(
        [executable, "-B", "-I", "-c", import_script],
        logger,
        cwd=source_root,
        env={
            "JK_INFO_DIR": os.fspath(target_root / "info"),
            "JK_APP_VERSION": app_version,
        },
        timeout=timeout,
        error_code="critical_import_failed",
    )


def run_console_entrypoint_checks(
    environment: Path, source_root: Path, logger: ProvisionLogger, timeout: float
) -> None:
    # Windows console launchers embed the absolute interpreter path. These
    # checks intentionally execute the .exe wrappers, not ``python -m``.
    for name, arguments in (("pip", ("--isolated", "--version")), ("uvicorn", ("--version",))):
        executable = venv_console_script(environment, name)
        if not executable.is_file():
            raise ProvisionError("console_entrypoint_missing", f"Console launcher ausente: {executable}")
        run_command(
            [executable, *arguments],
            logger,
            cwd=source_root,
            timeout=timeout,
            error_code="console_entrypoint_failed",
        )


def run_quick_dependency_spec_check(
    environment: Path, source_root: Path, logger: ProvisionLogger, timeout: float
) -> None:
    """Confirm critical modules still exist without importing their heavy code."""

    executable = venv_python(environment)
    modules = repr((*CRITICAL_IMPORTS, "backend_api"))
    source = repr(os.fspath(source_root))
    script = (
        "import importlib.util,sys;"
        f"sys.path.insert(0,{source});"
        f"missing=[name for name in {modules} if importlib.util.find_spec(name) is None];"
        "print('quick-specs-ok' if not missing else 'missing:'+','.join(missing));"
        "raise SystemExit(1 if missing else 0)"
    )
    run_command(
        [executable, "-B", "-I", "-c", script],
        logger,
        cwd=source_root,
        timeout=timeout,
        error_code="quick_dependency_missing",
    )


def existing_venv_is_ready(
    environment: Path,
    expected_marker: dict[str, Any],
    spec: RuntimeSpec,
    source_root: Path,
    target_root: Path,
    app_version: str,
    logger: ProvisionLogger,
    timeout: float,
) -> bool:
    marker_path = environment / VENV_MARKER_NAME
    if not environment.is_dir() or not marker_path.is_file():
        return False
    try:
        marker = read_json(marker_path, code="venv_marker_invalid")
        for field in (
            "state",
            "python_version",
            "python_abi",
            "runtime_tree_sha256",
            "requirements_sha256",
            "wheel_manifest_sha256",
        ):
            if marker.get(field) != expected_marker.get(field):
                return False
        run_health_checks(environment, spec, source_root, target_root, app_version, logger, timeout)
        run_console_entrypoint_checks(environment, source_root, logger, timeout)
    except ProvisionError as exc:
        logger.write(f"Ambiente existente sera reconstruido: [{exc.code}] {exc}")
        return False
    return True


def try_quick_reuse(
    source_root: Path,
    target_root: Path,
    spec: RuntimeSpec,
    logger: ProvisionLogger,
    timeout: float,
) -> dict[str, Any] | None:
    """Reuse a previously deep-validated runtime with pinned integrity checks.

    A mismatch is not an error for the caller: it means the normal deep,
    transactional provisioning path must run.
    """

    runtime = target_root / ".python-runtime"
    environment = target_root / ".venv"
    marker_path = environment / VENV_MARKER_NAME
    status_path = target_root / STATUS_RELATIVE_PATH
    ambiguous_paths = (
        target_root / ".python-runtime.new",
        target_root / ".python-runtime.previous",
        target_root / ".venv.new",
        target_root / ".venv.previous",
        target_root / VENV_BUILD_SENTINEL_RELATIVE_PATH,
    )
    try:
        if any(path.exists() or path.is_symlink() for path in ambiguous_paths):
            raise ProvisionError("quick_reuse_ambiguous", "Ha uma promocao interrompida para recuperar.")
        for directory, label in ((runtime, "runtime"), (environment, "venv")):
            if not directory.is_dir() or directory.is_symlink() or _is_reparse_point(directory):
                raise ProvisionError("quick_reuse_missing", f"{label} instalado ausente ou inseguro.")
        required_files = (
            python_executable(runtime),
            venv_python(environment),
            venv_console_script(environment, "pip"),
            venv_console_script(environment, "uvicorn"),
            marker_path,
            status_path,
        )
        if any(
            not path.is_file() or path.is_symlink() or _is_reparse_point(path)
            for path in required_files
        ):
            raise ProvisionError(
                "quick_reuse_metadata_missing",
                "Executaveis, launchers, marcador ou status ready estao ausentes/inseguros.",
            )

        runtime_manifest, expected = load_quick_reuse_metadata(source_root, spec)
        marker = read_json(marker_path, code="venv_marker_invalid")
        status = read_json(status_path, code="runtime_status_invalid")
        for label, metadata in (("marker", marker), ("status", status)):
            if metadata.get("schema_version") != 1:
                raise ProvisionError("quick_reuse_metadata_mismatch", f"Schema invalido no {label}.")
            for field in QUICK_REUSE_METADATA_FIELDS:
                if metadata.get(field) != expected.get(field):
                    raise ProvisionError(
                        "quick_reuse_metadata_mismatch",
                        f"Campo {field} do {label} diverge do pacote atual.",
                    )
        if status.get("action") not in {"created", "reused", "quick_reused"}:
            raise ProvisionError("quick_reuse_metadata_mismatch", "Acao ready invalida no status.")
        if not str(marker.get("created_at") or "").strip():
            raise ProvisionError("quick_reuse_metadata_mismatch", "Data de criacao ausente no marcador.")

        # Wheel hashes, pip check and critical imports stay in the deep path.
        # The installed runtime tree is nevertheless bound to the immutable
        # central pin on every path; otherwise a stale marker could bless a
        # modified user-writable interpreter.
        installed_inventory = tree_inventory(runtime)
        if installed_inventory != spec.portable_inventory:
            raise ProvisionError(
                "portable_integrity_failed",
                "Runtime Python instalado diverge do pin central.",
            )
        # find_spec still catches a deleted/partially missing critical package
        # so the same boot falls through to transactional repair.
        probe_python(python_executable(runtime), spec, logger, timeout)
        probe_python(venv_python(environment), spec, logger, timeout)
        run_quick_dependency_spec_check(environment, source_root, logger, timeout)
        run_console_entrypoint_checks(environment, source_root, logger, timeout)

        result = {
            "action": "quick_reused",
            "message": "Runtime Python reutilizado com validacao rapida.",
            **expected,
            "created_at": marker["created_at"],
        }
        write_status(target_root, "ready", spec, log_file=os.fspath(logger.path), **result)
        logger.write(result["message"])
        return result
    except Exception as exc:
        if isinstance(exc, ProvisionError):
            logger.write(f"Reutilizacao rapida indisponivel: [{exc.code}] {exc}; iniciando validacao profunda.")
        else:
            logger.write(f"Reutilizacao rapida indisponivel: {exc}; iniciando validacao profunda.")
        return None


def _promote_directory(staged: Path, destination: Path, parent: Path, backup: Path) -> bool:
    _safe_remove(backup, parent)
    had_previous = destination.exists()
    if had_previous:
        os.replace(destination, backup)
    try:
        os.replace(staged, destination)
    except BaseException:
        if had_previous and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    return had_previous


def _restore_directory(destination: Path, backup: Path, parent: Path, had_previous: bool) -> None:
    _safe_remove(destination, parent)
    if had_previous and backup.exists():
        os.replace(backup, destination)


def _recover_interrupted_promotion(destination: Path, backup: Path, staged: Path, parent: Path) -> None:
    """Recover the only ambiguous states left by an interrupted atomic rename."""

    if not destination.exists() and backup.exists():
        os.replace(backup, destination)
    elif destination.exists() and backup.exists():
        # A process stopped before deleting the backup, so conservatively roll
        # back. The idempotent run will validate and promote a fresh copy.
        _safe_remove(destination, parent)
        os.replace(backup, destination)
    _safe_remove(staged, parent)


def _recover_interrupted_venv(
    destination: Path,
    backup: Path,
    legacy_staged: Path,
    build_sentinel: Path,
    parent: Path,
    logger: ProvisionLogger,
) -> None:
    had_backup = backup.exists()
    _recover_interrupted_promotion(destination, backup, legacy_staged, parent)
    if not build_sentinel.is_file():
        return
    if not had_backup and destination.exists() and not (destination / VENV_MARKER_NAME).is_file():
        logger.write(f"Removendo .venv incompleta deixada por execucao interrompida: {destination}")
        _safe_remove(destination, parent)
    _safe_remove(build_sentinel, parent)


def write_status(
    target_root: Path, status_state: str, spec: RuntimeSpec | None = None, **values: Any
) -> None:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "state": status_state,
        "updated_at": utc_now(),
    }
    if spec is not None:
        payload["python_version"] = spec.version
        payload["python_abi"] = spec.abi
    payload.update(values)
    atomic_write_json(target_root / STATUS_RELATIVE_PATH, payload)


def provision(
    source_root: Path,
    target_root: Path,
    spec: RuntimeSpec,
    logger: ProvisionLogger,
    *,
    lock_timeout: float,
    command_timeout: float,
    force: bool,
    quick_reuse: bool = False,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    target_root = target_root.resolve()
    if not source_root.is_dir():
        raise ProvisionError("source_missing", f"Raiz dos recursos nao encontrada: {source_root}")
    target_root.mkdir(parents=True, exist_ok=True)
    lock_path = target_root / LOCK_NAME
    with ProvisionLock(lock_path, logger, lock_timeout):
        if quick_reuse and not force:
            quick_result = try_quick_reuse(source_root, target_root, spec, logger, command_timeout)
            if quick_result is not None:
                return quick_result
        write_status(
            target_root,
            "provisioning",
            spec,
            started_at=utc_now(),
            stage="source_validation",
            log_file=os.fspath(logger.path),
            message="Preparando runtime Python e ambiente virtual.",
        )
        runtime_manifest, portable_source, portable_inventory = validate_runtime_source(
            source_root, spec, logger, command_timeout
        )
        wheelhouse = validate_wheelhouse(source_root, spec)
        portable_summary = {
            "file_count": portable_inventory[0],
            "total_size": portable_inventory[1],
            "tree_sha256": portable_inventory[2],
        }
        marker = _marker_payload(portable_summary, wheelhouse, spec)
        app_version = str(runtime_manifest.get("version") or "")

        runtime = target_root / ".python-runtime"
        runtime_stage = target_root / ".python-runtime.new"
        runtime_backup = target_root / ".python-runtime.previous"
        runtime_changed = False
        runtime_had_previous = False
        venv = target_root / ".venv"
        legacy_venv_stage = target_root / ".venv.new"
        venv_backup = target_root / ".venv.previous"
        venv_build_sentinel = target_root / VENV_BUILD_SENTINEL_RELATIVE_PATH
        venv_replacement_started = False
        venv_had_previous = False

        try:
            _recover_interrupted_promotion(runtime, runtime_backup, runtime_stage, target_root)
            _recover_interrupted_venv(
                venv,
                venv_backup,
                legacy_venv_stage,
                venv_build_sentinel,
                target_root,
                logger,
            )
            runtime_valid = False
            if runtime.is_dir():
                try:
                    runtime_valid = tree_inventory(runtime) == portable_inventory
                    if runtime_valid:
                        probe_python(python_executable(runtime), spec, logger, command_timeout)
                except ProvisionError as exc:
                    logger.write(f"Runtime instalado sera substituido: [{exc.code}] {exc}")
                    runtime_valid = False
            if not runtime_valid:
                _safe_remove(runtime_stage, target_root)
                logger.write(f"Copiando runtime portatil para {runtime_stage}")
                shutil.copytree(portable_source, runtime_stage, copy_function=shutil.copy2)
                if tree_inventory(runtime_stage) != portable_inventory:
                    raise ProvisionError("runtime_copy_failed", "Copia do runtime portatil falhou na verificacao de integridade.")
                probe_python(python_executable(runtime_stage), spec, logger, command_timeout)
                runtime_had_previous = _promote_directory(runtime_stage, runtime, target_root, runtime_backup)
                runtime_changed = True
                logger.write(f"Runtime Python promovido para {runtime}")

            if not force and existing_venv_is_ready(
                venv, marker, spec, source_root, target_root, app_version, logger, command_timeout
            ):
                result = {
                    "action": "reused",
                    "message": "Runtime Python e ambiente virtual ja estavam validos.",
                    **marker,
                }
                write_status(target_root, "ready", spec, log_file=os.fspath(logger.path), **result)
                _safe_remove(runtime_backup, target_root)
                logger.write(result["message"])
                return result

            # Console launchers do Windows (pip.exe, uvicorn.exe etc.) embed the
            # absolute interpreter path. Build at the final .venv path after
            # preserving the old environment; a later directory rename would
            # leave every launcher pointing at .venv.new.
            _safe_remove(venv_backup, target_root)
            venv_had_previous = venv.exists()
            atomic_write_json(
                venv_build_sentinel,
                {
                    "schema_version": 1,
                    "state": "building",
                    "started_at": utc_now(),
                    "pid": os.getpid(),
                    "previous_environment": venv_had_previous,
                },
            )
            if venv_had_previous:
                os.replace(venv, venv_backup)
            venv_replacement_started = True
            logger.write(f"Criando ambiente transacional no caminho final {venv}")
            run_command(
                [python_executable(runtime), "-B", "-I", "-m", "venv", "--copies", venv],
                logger,
                cwd=target_root,
                timeout=command_timeout,
                error_code="venv_create_failed",
            )
            staged_python = venv_python(venv)
            run_command(
                [staged_python, "-B", "-I", "-m", "ensurepip", "--upgrade"],
                logger,
                cwd=source_root,
                timeout=command_timeout,
                error_code="ensurepip_failed",
            )
            run_command(
                [
                    staged_python,
                    "-B",
                    "-I",
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--disable-pip-version-check",
                    "--no-index",
                    "--require-hashes",
                    "--only-binary=:all:",
                    "--find-links",
                    wheelhouse["wheel_root"],
                    "-r",
                    wheelhouse["requirements_path"],
                ],
                logger,
                cwd=source_root,
                timeout=command_timeout,
                error_code="offline_install_failed",
            )
            run_health_checks(
                venv, spec, source_root, target_root, app_version, logger, command_timeout
            )
            run_console_entrypoint_checks(venv, source_root, logger, command_timeout)
            atomic_write_json(venv / VENV_MARKER_NAME, marker)
            logger.write(f"Ambiente virtual validado no caminho final {venv}")

            result = {
                "action": "created",
                "message": "Runtime Python e ambiente virtual provisionados com sucesso.",
                **marker,
            }
            write_status(target_root, "ready", spec, log_file=os.fspath(logger.path), **result)
            _safe_remove(venv_build_sentinel, target_root)
            for obsolete in (venv_backup, runtime_backup):
                try:
                    _safe_remove(obsolete, target_root)
                except (OSError, ProvisionError) as cleanup_error:
                    logger.write(f"Aviso: nao foi possivel remover backup obsoleto {obsolete}: {cleanup_error}")
            logger.write(result["message"])
            return result
        except BaseException as original_error:
            rollback_errors: list[str] = []
            if venv_replacement_started:
                try:
                    _restore_directory(venv, venv_backup, target_root, venv_had_previous)
                except BaseException as rollback_error:
                    rollback_errors.append(f"venv: {rollback_error}")
            try:
                _safe_remove(legacy_venv_stage, target_root)
            except BaseException as rollback_error:
                rollback_errors.append(f"legacy_stage: {rollback_error}")
            if runtime_changed:
                try:
                    _restore_directory(runtime, runtime_backup, target_root, runtime_had_previous)
                except BaseException as rollback_error:
                    rollback_errors.append(f"runtime: {rollback_error}")
            for label, obsolete in (
                ("runtime_stage", runtime_stage),
                ("build_sentinel", venv_build_sentinel),
            ):
                try:
                    _safe_remove(obsolete, target_root)
                except BaseException as rollback_error:
                    rollback_errors.append(f"{label}: {rollback_error}")
            if rollback_errors:
                detail = "; ".join(rollback_errors)
                raise ProvisionError(
                    "rollback_failed",
                    f"Falha original: {original_error}. Falha ao restaurar ambiente anterior: {detail}",
                ) from original_error
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Provisiona o runtime Python portatil e a .venv do JK Sistema sem acesso a rede."
    )
    parser.add_argument("--source-root", required=True, type=Path, help="Raiz local_app com recursos empacotados.")
    parser.add_argument("--target-root", required=True, type=Path, help="Raiz local_app gravavel do usuario.")
    parser.add_argument("--log-file", type=Path, help="Arquivo de log; padrao: <target>/logs/python_runtime_provision.log")
    parser.add_argument("--lock-timeout", type=float, default=120.0)
    parser.add_argument("--command-timeout", type=float, default=1800.0)
    parser.add_argument("--force", action="store_true", help="Recria a .venv mesmo quando o marcador e os checks estao validos.")
    parser.add_argument(
        "--quick-reuse",
        action="store_true",
        help="No boot do Electron, tenta reutilizar um ambiente previamente validado sem rehash completo.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_root = args.source_root.resolve()
    target_root = args.target_root.resolve()
    log_file = (args.log_file or (target_root / "logs" / "python_runtime_provision.log")).resolve()
    try:
        logger = ProvisionLogger(log_file)
    except OSError as exc:
        print(f"Nao foi possivel abrir o log de provisionamento: {exc}", file=sys.stderr)
        return 1
    spec: RuntimeSpec | None = None
    try:
        spec = load_runtime_spec(source_root)
        logger.write(
            f"Inicio do provisionamento; source={source_root}; target={target_root}; "
            f"python={spec.version}; abi={spec.abi}"
        )
        result = provision(
            source_root,
            target_root,
            spec,
            logger,
            lock_timeout=max(0.0, args.lock_timeout),
            command_timeout=max(1.0, args.command_timeout),
            force=bool(args.force),
            quick_reuse=bool(args.quick_reuse),
        )
        logger.write(f"Status final: ready ({result['action']})")
        return 0
    except ProvisionError as exc:
        logger.write(f"FALHA [{exc.code}]: {exc}")
        if exc.code != "lock_timeout":
            try:
                write_status(
                    target_root,
                    "failed",
                    spec,
                    error_code=exc.code,
                    log_file=os.fspath(logger.path),
                    message=str(exc),
                    previous_environment_preserved=exc.code != "rollback_failed",
                )
            except OSError as status_error:
                logger.write(f"Nao foi possivel gravar o status de falha: {status_error}")
        return 2 if exc.code == "lock_timeout" else 1
    except BaseException as exc:
        logger.write(f"FALHA [unexpected_error]: {exc}")
        for line in traceback.format_exc().rstrip().splitlines():
            logger.write(f"  {line}")
        try:
            write_status(
                target_root,
                "failed",
                spec,
                error_code="unexpected_error",
                log_file=os.fspath(logger.path),
                message=str(exc),
                previous_environment_preserved=False,
            )
        except OSError as status_error:
            logger.write(f"Nao foi possivel gravar o status de falha: {status_error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
