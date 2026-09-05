"""Build an encrypted, untracked credential bundle for the private installer.

The command never prints secret values or source paths. OAuth access/refresh
tokens and operational tenant files are deliberately outside this bundle.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from typing import Iterable

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


FORMAT = "jk-private-credentials-v1"
AAD = FORMAT.encode("utf-8")
ITERATIONS = 600_000
MAX_SECRET_CHARS = 64 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024

ALLOWED_ENV_KEYS = {
    "BRAVE_SEARCH_API_KEY",
    "DAILY_API_KEY",
    "DATALASTIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "FIREBASE_API_KEY",
    "FIREBASE_APP_ID",
    "FIREBASE_AUTH_DOMAIN",
    "FIREBASE_DATABASE_URL",
    "FIREBASE_PROJECT_ID",
    "FIREBASE_REALTIME_DATABASE_URL",
    "FIREBASE_WEB_API_KEY",
    "FIREBASE_WEB_APP_ID",
    "FIREBASE_WEB_AUTH_DOMAIN",
    "GEMINI_AGENT_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_GENAI_API_KEY",
    "GOOGLE_LOGIN_CLIENT_ID",
    "GOOGLE_LOGIN_CLIENT_SECRET",
    "GROQ_API_KEY",
    "IA_AGENT_API_KEY",
    "IA_AGENT_ENDPOINT_API_KEY",
    "IA_DEEPSEEK_API_KEY",
    "IA_GROQ_API_KEY",
    "IA_OPENAI_API_KEY",
    "JK_AGENT_ENDPOINT_API_KEY",
    "JK_DAILY_API_KEY",
    "JK_DATALASTIC_API_KEY",
    "JK_FIREBASE_API_KEY",
    "JK_FIREBASE_APP_ID",
    "JK_FIREBASE_AUTH_DOMAIN",
    "JK_FIREBASE_DATABASE_URL",
    "JK_FIREBASE_EXPECTED_PROJECT_ID",
    "JK_FIREBASE_PROJECT_ID",
    "JK_FIREBASE_REALTIME_DATABASE_URL",
    "JK_FIREBASE_WEB_API_KEY",
    "JK_FIREBASE_WEB_APP_ID",
    "OPENAI_API_KEY",
    "OPENAI_PROJECT_ID",
    "SERPAPI_KEY",
    "TAVILY_API_KEY",
    "VERTEX_AGENT_API_KEY",
    "VERTEX_AI_API_KEY",
    "VERTEX_AI_PROJECT_ID",
}

CREDENTIAL_FILE_NAMES = {
    "firebase-service-account.json": "firebase-service-account.json",
    "firebase_service_account.json": "firebase-service-account.json",
    "credentials.json": "credentials.json",
}

SECRET_TEXT_FILES = {
    "openai_api_key.txt": "OPENAI_API_KEY",
    "deepseek_api_key.txt": "DEEPSEEK_API_KEY",
    "gemini_api_key.txt": "GEMINI_API_KEY",
    "vertex_agent_api_key.txt": "GEMINI_AGENT_API_KEY",
    "groq_api_key.txt": "GROQ_API_KEY",
}

WINDOWS_CREDENTIAL_KEYS = (
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_AGENT_API_KEY",
    "GROQ_API_KEY",
)


def _read_text(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        return ""
    if path.stat().st_size > MAX_SECRET_CHARS:
        raise ValueError(f"Arquivo de segredo excede o limite: {path.name}")
    return path.read_text(encoding="utf-8-sig").strip()


def _parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    raw = _read_text(path)
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip().upper()
        if key.startswith("EXPORT "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key in ALLOWED_ENV_KEYS and value and "\x00" not in value and "\r" not in value and "\n" not in value:
            values[key] = value
    return values


def _candidate_env_files(root: Path) -> Iterable[Path]:
    yield root / "firebase-presence.env"
    yield root / ".env"
    yield root / "info" / "firebase-presence.env"
    yield root / "info" / ".env"


def _candidate_credential_files(root: Path) -> Iterable[tuple[Path, str]]:
    for base in (root / "info", root):
        for source_name, target_name in CREDENTIAL_FILE_NAMES.items():
            yield base / source_name, target_name
        if base.is_dir() and not base.is_symlink():
            for candidate in sorted(base.glob("jkjkjk-*.json")):
                yield candidate, candidate.name


def _candidate_secret_text_files(root: Path) -> Iterable[tuple[Path, str]]:
    for base in (root / "info", root):
        for filename, key in SECRET_TEXT_FILES.items():
            yield base / filename, key


def _validate_service_account(raw: bytes, target: str) -> None:
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"Credencial excede o limite: {target}")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"Credencial JSON invalida: {target}") from exc
    private_key = str(payload.get("private_key") or "") if isinstance(payload, dict) else ""
    client_email = str(payload.get("client_email") or "") if isinstance(payload, dict) else ""
    if not (
        isinstance(payload, dict)
        and payload.get("type") == "service_account"
        and str(payload.get("project_id") or "").strip()
        and client_email.endswith(".gserviceaccount.com")
        and "-----BEGIN PRIVATE KEY-----" in private_key
        and "-----END PRIVATE KEY-----" in private_key
    ):
        raise ValueError(f"Service account invalida: {target}")


def _collect_windows_credentials(source_root: Path) -> dict[str, str]:
    if os.name != "nt":
        return {}
    module_path = source_root / "backend" / "services" / "secure_credentials.py"
    if not module_path.is_file():
        return {}
    import importlib.util

    spec = importlib.util.spec_from_file_location("jk_private_bundle_secure_credentials", module_path)
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result: dict[str, str] = {}
    for key in WINDOWS_CREDENTIAL_KEYS:
        value = str(module.read_secret(key) or "").strip()
        if value:
            result[key] = value
    return result


def collect_payload(source_roots: list[Path]) -> tuple[dict, dict[str, int]]:
    environment: dict[str, str] = {}
    files_by_target: dict[str, bytes] = {}
    env_files_used = 0
    secret_text_files_used = 0

    for root in source_roots:
        if not root.is_dir() or root.is_symlink():
            continue
        for env_path in _candidate_env_files(root):
            parsed = _parse_dotenv(env_path)
            if parsed:
                env_files_used += 1
            for key, value in parsed.items():
                environment.setdefault(key, value)
        for secret_path, key in _candidate_secret_text_files(root):
            value = _read_text(secret_path)
            if value:
                secret_text_files_used += 1
                environment.setdefault(key, value)
        for credential_path, target in _candidate_credential_files(root):
            if not credential_path.is_file() or credential_path.is_symlink():
                continue
            raw = credential_path.read_bytes()
            _validate_service_account(raw, target)
            existing = files_by_target.get(target)
            if existing is not None and existing != raw:
                raise ValueError(f"Foram encontradas credenciais diferentes para o mesmo destino: {target}")
            files_by_target[target] = raw

    if source_roots:
        for key, value in _collect_windows_credentials(source_roots[0]).items():
            environment.setdefault(key, value)

    files = [
        {
            "target": target,
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        for target, raw in sorted(files_by_target.items())
    ]
    if not environment and not files:
        raise ValueError("Nenhuma chave ou service account permitida foi encontrada.")
    payload = {"version": 1, "environment": dict(sorted(environment.items())), "files": files}
    report = {
        "environment_keys": len(environment),
        "service_accounts": len(files),
        "environment_files": env_files_used,
        "secret_text_files": secret_text_files_used,
        "oauth_tokens_included": 0,
    }
    return payload, report


def _password_from_file(path: Path, generate: bool) -> tuple[str, bool]:
    generated = False
    if path.is_file():
        password = path.read_text(encoding="utf-8-sig").strip()
    elif generate:
        path.parent.mkdir(parents=True, exist_ok=True)
        password = secrets.token_urlsafe(36)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(password + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        generated = True
    else:
        raise ValueError("Arquivo de senha ausente; use --generate-password para cria-lo com seguranca.")
    if not 16 <= len(password) <= 512 or "\x00" in password or "\r" in password or "\n" in password:
        raise ValueError("A senha deve ter entre 16 e 512 caracteres e uma unica linha.")
    return password, generated


def encrypt_payload(payload: dict, password: str) -> dict:
    plaintext = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(plaintext) > MAX_FILE_BYTES:
        raise ValueError("O payload privado excede o limite permitido.")
    salt = secrets.token_bytes(32)
    iv = secrets.token_bytes(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(
        password.encode("utf-8")
    )
    encrypted_with_tag = AESGCM(key).encrypt(iv, plaintext, AAD)
    ciphertext, tag = encrypted_with_tag[:-16], encrypted_with_tag[-16:]
    return {
        "format": FORMAT,
        "kdf": {
            "name": "pbkdf2-sha256",
            "iterations": ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
        },
        "cipher": {
            "name": "aes-256-gcm",
            "iv": base64.b64encode(iv).decode("ascii"),
            "tag": base64.b64encode(tag).decode("ascii"),
        },
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary_name, 0o600)
        except OSError:
            pass
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", action="append", required=True)
    parser.add_argument("--password-file", required=True)
    parser.add_argument("--generate-password", action="store_true")
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        roots = [Path(value).resolve(strict=False) for value in args.source_root]
        password, generated = _password_from_file(Path(args.password_file), args.generate_password)
        payload, report = collect_payload(roots)
        bundle = encrypt_payload(payload, password)
        output_path = Path(args.output)
        _atomic_write_json(output_path, bundle)
        report.update(
            {
                "format": FORMAT,
                "password_generated": generated,
                "bundle_bytes": output_path.stat().st_size,
                "bundle_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
            }
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"PRIVATE_BUNDLE_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
