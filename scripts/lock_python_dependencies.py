from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from email.parser import Parser
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "requirements.in"
DEFAULT_OUTPUT = ROOT / "requirements.txt"
DEFAULT_CACHE = ROOT / ".dependency-lock"
RUNTIME_VERSIONS_FILE = ROOT / "runtime-versions.json"


def load_python_runtime() -> dict[str, str]:
    try:
        config = json.loads(RUNTIME_VERSIONS_FILE.read_text(encoding="utf-8"))
        python_config = config["python"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Configuracao de runtime invalida: {RUNTIME_VERSIONS_FILE}") from exc

    required_fields = ("version", "minor", "implementation", "abi")
    values = {field: str(python_config.get(field) or "").strip() for field in required_fields}
    if any(not value for value in values.values()):
        raise RuntimeError("runtime-versions.json nao possui todos os campos Python obrigatorios")
    if not re.fullmatch(r"\d+\.\d+\.\d+", values["version"]):
        raise RuntimeError(f"Versao Python invalida: {values['version']}")

    major, minor, _patch = values["version"].split(".")
    expected_minor = f"{major}.{minor}"
    expected_abi = f"cp{major}{minor}"
    if values["minor"] != expected_minor:
        raise RuntimeError(f"Minor Python deve ser {expected_minor}, encontrado {values['minor']}")
    if values["implementation"] != "cp":
        raise RuntimeError("A geracao de locks exige a implementacao CPython (cp)")
    if values["abi"] != expected_abi:
        raise RuntimeError(f"ABI Python deve ser {expected_abi}, encontrada {values['abi']}")
    return values


PYTHON_RUNTIME = load_python_runtime()
PYTHON_VERSION = PYTHON_RUNTIME["version"]
TARGET_PYTHON = PYTHON_RUNTIME["minor"]
TARGET_ABI = PYTHON_RUNTIME["abi"]
TARGETS = {
    "win32": (["win_amd64"], TARGET_ABI),
    "linux": (
        [
            "manylinux_2_28_x86_64",
            "manylinux2014_x86_64",
            "manylinux_2_17_x86_64",
            "manylinux1_x86_64",
        ],
        TARGET_ABI,
    ),
}


def canonicalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def wheel_identity(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        metadata_names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA") and name.count("/") == 1
        ]
        if len(metadata_names) != 1:
            raise RuntimeError(f"Wheel sem METADATA unica: {path.name}")
        metadata = Parser().parsestr(archive.read(metadata_names[0]).decode("utf-8", "replace"))
    name = canonicalize_name(str(metadata.get("Name") or ""))
    version = str(metadata.get("Version") or "").strip()
    if not name or not version:
        raise RuntimeError(f"Nome ou versao ausente no wheel: {path.name}")
    return name, version


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def requirements_for_target(source: Path, destination: Path, system_name: str) -> Path:
    selected: list[str] = []
    marker_pattern = re.compile(r'\s*;\s*sys_platform\s*==\s*["\'](win32|linux)["\']\s*$')
    for line in source.read_text(encoding="utf-8").splitlines():
        match = marker_pattern.search(line)
        if match:
            if match.group(1) != system_name:
                continue
            line = line[: match.start()].rstrip()
        selected.append(line)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(selected).rstrip() + "\n", encoding="utf-8", newline="\n")
    return destination


def download_target(requirements: Path, cache_dir: Path, platforms: list[str], abi: str) -> None:
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True)
    command = [
        sys.executable,
        "-m",
        "pip",
        "--isolated",
        "download",
        "--disable-pip-version-check",
        "--quiet",
        "--dest",
        str(cache_dir),
        "--only-binary=:all:",
        "--python-version",
        TARGET_PYTHON,
        "--implementation",
        "cp",
        "--abi",
        abi,
        "-r",
        str(requirements),
    ]
    for platform in platforms:
        command[command.index("--python-version"):command.index("--python-version")] = ["--platform", platform]
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
        }
    )
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def inventory(cache_dir: Path) -> dict[str, dict[str, object]]:
    artifacts = sorted(cache_dir.iterdir())
    unsupported = [path.name for path in artifacts if path.suffix.lower() != ".whl"]
    if unsupported:
        raise RuntimeError(f"Artefatos nao-wheel encontrados: {', '.join(unsupported)}")

    result: dict[str, dict[str, object]] = {}
    for wheel in artifacts:
        name, version = wheel_identity(wheel)
        current = result.setdefault(name, {"version": version, "hashes": set()})
        if current["version"] != version:
            raise RuntimeError(f"Mais de uma versao resolvida para {name}: {current['version']} e {version}")
        hashes = current["hashes"]
        assert isinstance(hashes, set)
        hashes.add(sha256(wheel))
    return result


def render_lock(inventories: dict[str, dict[str, dict[str, object]]]) -> str:
    package_names = sorted({name for packages in inventories.values() for name in packages})
    lines = [
        "# Gerado por scripts/lock_python_dependencies.py. Nao edite manualmente.",
        f"# Alvos: CPython {TARGET_PYTHON} em Windows x64 e Linux x64; somente wheels binarias.",
        "--require-hashes",
        "--only-binary=:all:",
        "",
    ]
    for name in package_names:
        platforms = [platform for platform, packages in inventories.items() if name in packages]
        versions = {str(inventories[platform][name]["version"]) for platform in platforms}
        if len(versions) != 1:
            detail = ", ".join(
                f"{platform}={inventories[platform][name]['version']}" for platform in platforms
            )
            raise RuntimeError(f"Versao divergente entre plataformas para {name}: {detail}")
        version = versions.pop()
        marker = ""
        if platforms == ["win32"]:
            marker = ' ; sys_platform == "win32"'
        elif platforms == ["linux"]:
            marker = ' ; sys_platform == "linux"'
        hashes = sorted(
            {
                str(value)
                for platform in platforms
                for value in inventories[platform][name]["hashes"]
            }
        )
        lines.append(f"{name}=={version}{marker} \\")
        for index, digest in enumerate(hashes):
            continuation = " \\" if index < len(hashes) - 1 else ""
            lines.append(f"    --hash=sha256:{digest}{continuation}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Gera o lock Python {TARGET_PYTHON} com hashes Windows e Linux."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()

    requirements = args.input.resolve()
    output = args.output.resolve()
    cache_root = args.cache_dir.resolve()
    if not requirements.is_file():
        raise FileNotFoundError(requirements)

    inventories: dict[str, dict[str, dict[str, object]]] = {}
    for system_name, (platforms, abi) in TARGETS.items():
        target_cache = cache_root / system_name
        target_requirements = requirements_for_target(
            requirements,
            cache_root / f"requirements-{system_name}.in",
            system_name,
        )
        download_target(target_requirements, target_cache, platforms, abi)
        inventories[system_name] = inventory(target_cache)
        print(f"[python-lock] {system_name}: {len(inventories[system_name])} pacotes")

    content = render_lock(inventories)
    output.write_text(content, encoding="utf-8", newline="\n")
    print(f"[python-lock] lock gravado em {output} ({len(content.splitlines())} linhas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
