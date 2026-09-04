"""Local trust registry for explicitly provisioned tenant directory aliases.

The registry intentionally lives in the lexical ``info`` root, outside every
tenant directory.  It stores filesystem identities only; paths are never
serialized.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from backend.services.path_coordination import path_lock_for


CADASTRO_TENANT_TRUST_ARQUIVO = ".cadastro_tenant_trust.json"
CADASTRO_TENANT_TRUST_LOCK = ".cadastro_tenant_trust.lock"
CADASTRO_TENANT_TRUST_SCHEMA = "jk.cadastro.tenant-trust.v1"
_CLIENT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_MAX_REGISTRY_BYTES = 1024 * 1024


class CadastroTenantTrustErro(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _falhar(code: str, message: str) -> None:
    raise CadastroTenantTrustErro(code, message)


def _client_id_exato(client_id: str) -> str:
    value = str(client_id or "").strip()
    if not _CLIENT_ID_RE.fullmatch(value) or value in {".", ".."}:
        _falhar("invalid_client_id", "Identificador de cliente invalido.")
    return value


def _caminho_e_link(path: os.PathLike[str] | str) -> bool:
    item = Path(path)
    if item.is_symlink():
        return True
    is_junction = getattr(item, "is_junction", None)
    return bool(callable(is_junction) and is_junction())


def _diretorio_regular(path: os.PathLike[str] | str, code: str) -> Path:
    item = Path(os.path.abspath(os.fspath(path)))
    if (
        not os.path.lexists(item)
        or _caminho_e_link(item)
        or os.path.normcase(os.path.normpath(os.path.realpath(item)))
        != os.path.normcase(os.path.normpath(item))
    ):
        _falhar(code, "Diretorio local inseguro.")
    try:
        info = os.stat(item, follow_symlinks=False)
    except OSError as exc:
        raise CadastroTenantTrustErro(code, "Diretorio local indisponivel.") from exc
    if not stat.S_ISDIR(info.st_mode):
        _falhar(code, "Diretorio local invalido.")
    return item


def _identidade_diretorio(path: Path) -> dict[str, int]:
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise CadastroTenantTrustErro(
            "canonical_tenant_unavailable", "Diretorio canonico indisponivel."
        ) from exc
    dev = info.st_dev
    ino = info.st_ino
    if (
        not stat.S_ISDIR(info.st_mode)
        or isinstance(dev, bool)
        or isinstance(ino, bool)
        or not isinstance(dev, int)
        or not isinstance(ino, int)
        or ino == 0
    ):
        _falhar("canonical_identity_invalid", "Identidade canonica invalida.")
    return {"st_dev": dev, "st_ino": ino}


def _object_sem_chaves_duplicadas(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _falhar("registry_invalid", "Registro local invalido.")
        result[key] = value
    return result


def _validar_identidade(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != {"st_dev", "st_ino"}:
        _falhar("registry_invalid", "Registro local invalido.")
    dev = value.get("st_dev")
    ino = value.get("st_ino")
    if (
        isinstance(dev, bool)
        or isinstance(ino, bool)
        or not isinstance(dev, int)
        or not isinstance(ino, int)
        or ino == 0
    ):
        _falhar("registry_invalid", "Registro local invalido.")
    return {"st_dev": dev, "st_ino": ino}


def _validar_registry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "clients"}:
        _falhar("registry_invalid", "Registro local invalido.")
    if value.get("schema") != CADASTRO_TENANT_TRUST_SCHEMA:
        _falhar("registry_invalid", "Registro local invalido.")
    clients = value.get("clients")
    if not isinstance(clients, dict):
        _falhar("registry_invalid", "Registro local invalido.")
    normalized: dict[str, dict[str, int]] = {}
    for raw_client_id, identity in clients.items():
        try:
            client_id = _client_id_exato(raw_client_id)
        except CadastroTenantTrustErro as exc:
            raise CadastroTenantTrustErro(
                "registry_invalid", "Registro local invalido."
            ) from exc
        if client_id != raw_client_id:
            _falhar("registry_invalid", "Registro local invalido.")
        normalized[client_id] = _validar_identidade(identity)
    return {"schema": CADASTRO_TENANT_TRUST_SCHEMA, "clients": normalized}


def _arquivo_regular(path: Path, code: str) -> None:
    if _caminho_e_link(path):
        _falhar(code, "Arquivo local inseguro.")
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise CadastroTenantTrustErro(code, "Arquivo local indisponivel.") from exc
    if not stat.S_ISREG(info.st_mode):
        _falhar(code, "Arquivo local invalido.")


def _arquivo_aberto_corresponde(path: Path, opened_info: os.stat_result, code: str) -> None:
    try:
        lexical_info = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise CadastroTenantTrustErro(code, "Arquivo local indisponivel.") from exc
    if (
        not stat.S_ISREG(opened_info.st_mode)
        or not stat.S_ISREG(lexical_info.st_mode)
        or opened_info.st_dev != lexical_info.st_dev
        or opened_info.st_ino != lexical_info.st_ino
    ):
        _falhar(code, "Arquivo local inseguro.")


def _ler_registry(info_root: Path, *, ausente_permitido: bool) -> dict[str, Any]:
    registry_path = info_root / CADASTRO_TENANT_TRUST_ARQUIVO
    if not os.path.lexists(registry_path):
        if ausente_permitido:
            return {"schema": CADASTRO_TENANT_TRUST_SCHEMA, "clients": {}}
        _falhar("registry_missing", "Registro local ausente.")
    _arquivo_regular(registry_path, "registry_invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(registry_path), flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened_info = os.fstat(stream.fileno())
            _arquivo_aberto_corresponde(registry_path, opened_info, "registry_invalid")
            raw = stream.read(_MAX_REGISTRY_BYTES + 1)
            if len(raw) > _MAX_REGISTRY_BYTES:
                _falhar("registry_invalid", "Registro local invalido.")
    except CadastroTenantTrustErro:
        raise
    except OSError as exc:
        raise CadastroTenantTrustErro("registry_invalid", "Registro local invalido.") from exc
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_sem_chaves_duplicadas)
    except CadastroTenantTrustErro:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CadastroTenantTrustErro("registry_invalid", "Registro local invalido.") from exc
    return _validar_registry(value)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _escrever_registry_atomico(info_root: Path, registry: dict[str, Any]) -> None:
    registry_path = info_root / CADASTRO_TENANT_TRUST_ARQUIVO
    if os.path.lexists(registry_path):
        _arquivo_regular(registry_path, "registry_invalid")
    payload = json.dumps(
        _validar_registry(registry),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".cadastro-tenant-trust.",
            suffix=".tmp",
            dir=info_root,
            delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, registry_path)
        temporary = ""
        _fsync_directory(info_root)
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


@contextmanager
def _registry_lock(info_root: Path, timeout_seconds: float = 10.0) -> Iterator[None]:
    lock_path = info_root / CADASTRO_TENANT_TRUST_LOCK
    with path_lock_for(lock_path):
        if os.path.lexists(lock_path):
            _arquivo_regular(lock_path, "registry_lock_unsafe")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(str(lock_path), flags, 0o600)
        except OSError as exc:
            raise CadastroTenantTrustErro(
                "registry_lock_unavailable", "Lock local indisponivel."
            ) from exc
        stream = os.fdopen(descriptor, "r+b", buffering=0)
        locked = False
        try:
            info = os.fstat(stream.fileno())
            _arquivo_aberto_corresponde(lock_path, info, "registry_lock_unsafe")
            if info.st_size == 0:
                stream.write(b"\0")
                os.fsync(stream.fileno())
            deadline = time.monotonic() + timeout_seconds
            while True:
                stream.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except (OSError, BlockingIOError) as exc:
                    if time.monotonic() >= deadline:
                        raise CadastroTenantTrustErro(
                            "registry_locked", "Registro local ocupado."
                        ) from exc
                    time.sleep(0.02)
            yield
        finally:
            if locked:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            stream.close()


def _validar_alias(
    alias_info_root: os.PathLike[str] | str,
    canonical_info_root: os.PathLike[str] | str,
    client_id: str,
) -> tuple[Path, Path, dict[str, int]]:
    client = _client_id_exato(client_id)
    alias_root = _diretorio_regular(alias_info_root, "alias_root_unsafe")
    canonical_root = _diretorio_regular(canonical_info_root, "canonical_root_unsafe")
    alias_tenant = alias_root / client
    canonical_tenant = canonical_root / client
    if not os.path.lexists(alias_tenant) or not _caminho_e_link(alias_tenant):
        _falhar("alias_tenant_required", "Alias de tenant explicito obrigatorio.")
    canonical_tenant = _diretorio_regular(canonical_tenant, "canonical_tenant_unsafe")
    try:
        alias_real = os.path.normcase(os.path.normpath(os.path.realpath(alias_tenant)))
        canonical_real = os.path.normcase(os.path.normpath(os.path.realpath(canonical_tenant)))
    except OSError as exc:
        raise CadastroTenantTrustErro("alias_target_invalid", "Destino do alias invalido.") from exc
    if alias_real != canonical_real or not os.path.isdir(alias_tenant):
        _falhar("alias_target_invalid", "Destino do alias invalido.")
    return alias_root, canonical_tenant, _identidade_diretorio(canonical_tenant)


def planejar_registro_alias(
    alias_info_root: os.PathLike[str] | str,
    canonical_info_root: os.PathLike[str] | str,
    client_id: str,
) -> dict[str, Any]:
    alias_root, _canonical_tenant, identity = _validar_alias(
        alias_info_root, canonical_info_root, client_id
    )
    registry = _ler_registry(alias_root, ausente_permitido=True)
    existing = registry["clients"].get(_client_id_exato(client_id))
    if existing is not None and existing != identity:
        _falhar("identity_conflict", "Cliente ja possui outra identidade registrada.")
    return {
        "schema": CADASTRO_TENANT_TRUST_SCHEMA,
        "mode": "dry-run",
        "action": "unchanged" if existing == identity else "register",
        "client_count_after": len(registry["clients"]) + (0 if existing is not None else 1),
    }


def registrar_alias_tenant(
    alias_info_root: os.PathLike[str] | str,
    canonical_info_root: os.PathLike[str] | str,
    client_id: str,
) -> dict[str, Any]:
    client = _client_id_exato(client_id)
    alias_root, _canonical_tenant, identity = _validar_alias(
        alias_info_root, canonical_info_root, client
    )
    with _registry_lock(alias_root):
        # Revalidate after acquiring the cross-process lock so a retarget cannot
        # be registered between validation and publication.
        locked_root, _canonical_tenant, locked_identity = _validar_alias(
            alias_info_root, canonical_info_root, client
        )
        if locked_root != alias_root or locked_identity != identity:
            _falhar("alias_changed", "Alias mudou durante o registro.")
        registry = _ler_registry(alias_root, ausente_permitido=True)
        existing = registry["clients"].get(client)
        if existing is not None and existing != identity:
            _falhar("identity_conflict", "Cliente ja possui outra identidade registrada.")
        action = "unchanged" if existing == identity else "registered"
        if existing is None:
            registry["clients"][client] = identity
            _escrever_registry_atomico(alias_root, registry)
        return {
            "schema": CADASTRO_TENANT_TRUST_SCHEMA,
            "mode": "apply",
            "action": action,
            "client_count": len(registry["clients"]),
        }


def resolver_alias_tenant_registrado(
    alias_info_root: os.PathLike[str] | str,
    client_id: str,
) -> str:
    """Resolve one alias only when its current target has the trusted identity."""

    client = _client_id_exato(client_id)
    alias_root = _diretorio_regular(alias_info_root, "alias_root_unsafe")
    alias_tenant = alias_root / client
    if not os.path.lexists(alias_tenant) or not _caminho_e_link(alias_tenant):
        _falhar("alias_tenant_required", "Alias de tenant explicito obrigatorio.")
    target = Path(os.path.realpath(alias_tenant))
    if not os.path.isdir(alias_tenant) or _caminho_e_link(target):
        _falhar("alias_target_invalid", "Destino do alias invalido.")
    identity = _identidade_diretorio(target)
    registry = _ler_registry(alias_root, ausente_permitido=False)
    expected = registry["clients"].get(client)
    if expected is None or expected != identity:
        _falhar("identity_mismatch", "Identidade do tenant nao autorizada.")
    return str(target)


__all__ = [
    "CADASTRO_TENANT_TRUST_ARQUIVO",
    "CADASTRO_TENANT_TRUST_LOCK",
    "CADASTRO_TENANT_TRUST_SCHEMA",
    "CadastroTenantTrustErro",
    "planejar_registro_alias",
    "registrar_alias_tenant",
    "resolver_alias_tenant_registrado",
]
