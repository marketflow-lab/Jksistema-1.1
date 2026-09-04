"""Coordenacao local e cross-process para transicoes das fotos do Cadastro."""

from __future__ import annotations

import hashlib
import os
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from backend.services.path_coordination import canonical_path_key, path_lock_for


_LOCK_ROOT_NAME = ".jk-cadastro-fotos-locks"
_LOCK_LOCAL = threading.local()


class CadastroFotosCoordenacaoErro(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _falhar(code: str, message: str) -> None:
    raise CadastroFotosCoordenacaoErro(code, message)


def _caminho_e_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(callable(is_junction) and is_junction())


def _diretorio_fisico(path: os.PathLike[str] | str, code: str) -> Path:
    lexical = Path(os.path.abspath(os.fspath(path)))
    if (
        not os.path.lexists(lexical)
        or _caminho_e_link(lexical)
        or os.path.normcase(os.path.normpath(os.path.realpath(lexical)))
        != os.path.normcase(os.path.normpath(lexical))
    ):
        _falhar(code, "Diretorio de coordenacao inseguro.")
    try:
        info = os.stat(lexical, follow_symlinks=False)
    except OSError as exc:
        raise CadastroFotosCoordenacaoErro(
            code, "Diretorio de coordenacao indisponivel."
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        _falhar(code, "Diretorio de coordenacao invalido.")
    return lexical


def cadastro_fotos_transition_lock_path(
    tenant_path: os.PathLike[str] | str,
) -> Path:
    tenant = _diretorio_fisico(tenant_path, "unsafe_tenant")
    digest = hashlib.sha256(canonical_path_key(tenant).encode("utf-8")).hexdigest()
    if os.name == "nt":
        # Somente uma chave para o RLock em processo; o lock cross-process real
        # e um mutex nomeado do Windows e nao cria artefatos no tenant.
        return tenant / f".{digest}.cadastro-fotos-mutex-key"
    lock_root = Path("/tmp") / f"{_LOCK_ROOT_NAME}-{os.getuid()}"
    if os.path.lexists(lock_root):
        lock_root = _diretorio_fisico(lock_root, "unsafe_lock_root")
    return lock_root / f"{digest}.lock"


def _arquivo_aberto_corresponde(path: Path, opened: os.stat_result) -> None:
    try:
        lexical = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise CadastroFotosCoordenacaoErro(
            "lock_unsafe", "Lock de coordenacao indisponivel."
        ) from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(lexical.st_mode)
        or opened.st_dev != lexical.st_dev
        or opened.st_ino != lexical.st_ino
    ):
        _falhar("lock_unsafe", "Lock de coordenacao inseguro.")


def _mutex_windows_nome(tenant_path: os.PathLike[str] | str) -> str:
    digest = hashlib.sha256(
        canonical_path_key(tenant_path).encode("utf-8")
    ).hexdigest()
    return f"Local\\JKCadastroFotos-{digest}"


@contextmanager
def _bloquear_mutex_windows(
    tenant_path: os.PathLike[str] | str,
    timeout_seconds: float,
) -> Iterator[None]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    create_mutex.restype = wintypes.HANDLE
    wait_for_single = kernel32.WaitForSingleObject
    wait_for_single.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait_for_single.restype = wintypes.DWORD
    release_mutex = kernel32.ReleaseMutex
    release_mutex.argtypes = [wintypes.HANDLE]
    release_mutex.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = create_mutex(None, False, _mutex_windows_nome(tenant_path))
    if not handle:
        _falhar("lock_unavailable", "Lock de coordenacao indisponivel.")
    acquired = False
    try:
        timeout_ms = min(0xFFFFFFFE, max(0, int(timeout_seconds * 1000)))
        result = int(wait_for_single(handle, timeout_ms))
        if result in {0x00000000, 0x00000080}:
            acquired = True
        elif result == 0x00000102:
            _falhar("locked", "As fotos do cliente estao sendo atualizadas.")
        else:
            _falhar("lock_unavailable", "Lock de coordenacao indisponivel.")
        yield
    finally:
        if acquired:
            release_mutex(handle)
        close_handle(handle)


@contextmanager
def _bloquear_arquivo_posix(lock_path: Path, timeout_seconds: float) -> Iterator[None]:
    import fcntl

    lock_path.parent.mkdir(mode=0o700, parents=False, exist_ok=True)
    _diretorio_fisico(lock_path.parent, "unsafe_lock_root")
    if os.path.lexists(lock_path) and (
        _caminho_e_link(lock_path) or not lock_path.is_file()
    ):
        _falhar("lock_unsafe", "Lock de coordenacao inseguro.")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(lock_path), flags, 0o600)
    except OSError as exc:
        raise CadastroFotosCoordenacaoErro(
            "lock_unavailable", "Lock de coordenacao indisponivel."
        ) from exc
    stream = os.fdopen(descriptor, "r+b", buffering=0)
    locked_file = False
    try:
        _arquivo_aberto_corresponde(lock_path, os.fstat(stream.fileno()))
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked_file = True
                break
            except (OSError, BlockingIOError) as exc:
                if timeout_seconds == 0 or time.monotonic() >= deadline:
                    raise CadastroFotosCoordenacaoErro(
                        "locked", "As fotos do cliente estao sendo atualizadas."
                    ) from exc
                time.sleep(0.02)
        yield
    finally:
        if locked_file:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        stream.close()


@contextmanager
def bloquear_transicao_fotos_tenant(
    tenant_path: os.PathLike[str] | str,
    *,
    timeout_seconds: float = 10.0,
) -> Iterator[None]:
    """Serializa writers de fotos e a migracao no mesmo tenant fisico."""

    timeout = max(0.0, float(timeout_seconds))
    tenant = _diretorio_fisico(tenant_path, "unsafe_tenant")
    lock_path = cadastro_fotos_transition_lock_path(tenant)
    key = canonical_path_key(lock_path)
    pid = os.getpid()
    if getattr(_LOCK_LOCAL, "pid", None) != pid:
        _LOCK_LOCAL.pid = pid
        _LOCK_LOCAL.held = {}
    held = getattr(_LOCK_LOCAL, "held", None)
    if held is None:
        held = {}
        _LOCK_LOCAL.held = held
    if key in held:
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return

    thread_lock = path_lock_for(lock_path)
    acquired_thread = (
        thread_lock.acquire(blocking=False)
        if timeout == 0
        else thread_lock.acquire(timeout=timeout)
    )
    if not acquired_thread:
        _falhar("locked", "As fotos do cliente estao sendo atualizadas.")

    try:
        bloqueio = (
            _bloquear_mutex_windows(tenant, timeout)
            if os.name == "nt"
            else _bloquear_arquivo_posix(lock_path, timeout)
        )
        with bloqueio:
            held[key] = 1
            try:
                yield
            finally:
                held.pop(key, None)
    finally:
        thread_lock.release()


__all__ = [
    "CadastroFotosCoordenacaoErro",
    "bloquear_transicao_fotos_tenant",
    "cadastro_fotos_transition_lock_path",
]
