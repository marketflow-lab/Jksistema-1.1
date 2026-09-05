from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_private_credentials_bundle.py"
SPEC = importlib.util.spec_from_file_location("build_private_credentials_bundle", SCRIPT)
assert SPEC and SPEC.loader
bundle_builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle_builder)


def _service_account(project_id: str) -> dict:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return {
        "type": "service_account",
        "project_id": project_id,
        "private_key_id": "synthetic-unit-test-key",
        "private_key": pem,
        "client_email": f"firebase-adminsdk@{project_id}.iam.gserviceaccount.com",
    }


def _decrypt(bundle: dict, password: str) -> dict:
    salt = base64.b64decode(bundle["kdf"]["salt"])
    iv = base64.b64decode(bundle["cipher"]["iv"])
    tag = base64.b64decode(bundle["cipher"]["tag"])
    ciphertext = base64.b64decode(bundle["ciphertext"])
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=bundle["kdf"]["iterations"],
    ).derive(password.encode("utf-8"))
    plaintext = AESGCM(key).decrypt(iv, ciphertext + tag, bundle_builder.AAD)
    return json.loads(plaintext)


def test_bundle_collects_only_allowlisted_secrets_and_encrypts_plaintext(tmp_path, monkeypatch):
    source = tmp_path / "source"
    info = source / "info"
    info.mkdir(parents=True)
    (source / "firebase-presence.env").write_text(
        "FIREBASE_PROJECT_ID=projeto-sintetico\n"
        "FIREBASE_WEB_API_KEY=synthetic-firebase-web-key\n"
        "ACCESS_TOKEN=must-not-be-packaged\n",
        encoding="utf-8",
    )
    (info / "openai_api_key.txt").write_text("synthetic-openai-key", encoding="utf-8")
    account = _service_account("projeto-sintetico")
    (info / "firebase-service-account.json").write_text(json.dumps(account), encoding="utf-8")
    monkeypatch.setattr(bundle_builder, "_collect_windows_credentials", lambda _root: {})

    payload, report = bundle_builder.collect_payload([source])
    assert payload["environment"] == {
        "FIREBASE_PROJECT_ID": "projeto-sintetico",
        "FIREBASE_WEB_API_KEY": "synthetic-firebase-web-key",
        "OPENAI_API_KEY": "synthetic-openai-key",
    }
    assert report["oauth_tokens_included"] == 0
    assert len(payload["files"]) == 1

    password = "senha-sintetica-com-mais-de-16-caracteres"
    encrypted = bundle_builder.encrypt_payload(payload, password)
    raw_bundle = json.dumps(encrypted)
    assert "synthetic-openai-key" not in raw_bundle
    assert "must-not-be-packaged" not in raw_bundle
    assert "PRIVATE KEY" not in raw_bundle
    assert _decrypt(encrypted, password) == payload
    with pytest.raises(Exception):
        _decrypt(encrypted, "senha-incorreta-com-mais-de-16-caracteres")


def test_conflicting_service_accounts_fail_closed(tmp_path, monkeypatch):
    first = tmp_path / "first" / "info"
    second = tmp_path / "second" / "info"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "firebase-service-account.json").write_text(
        json.dumps(_service_account("projeto-um")), encoding="utf-8"
    )
    (second / "firebase-service-account.json").write_text(
        json.dumps(_service_account("projeto-dois")), encoding="utf-8"
    )
    monkeypatch.setattr(bundle_builder, "_collect_windows_credentials", lambda _root: {})

    with pytest.raises(ValueError, match="credenciais diferentes"):
        bundle_builder.collect_payload([first.parent, second.parent])
