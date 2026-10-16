"""Private per-calculation read reuse. Never retained in result cache entries."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from io import BytesIO
from functools import wraps
from pathlib import Path
import os
import time


@dataclass
class LeituraMedias:
    values: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)


_LEITURA = ContextVar("medias_read_snapshot", default=None)


@contextmanager
def leitura_medias(*, metrics=None):
    current = _LEITURA.get()
    if current is not None:
        yield current
        return
    value = LeituraMedias(metrics=metrics if metrics is not None else {})
    token = _LEITURA.set(value)
    try:
        yield value
    finally:
        _LEITURA.reset(token)


def leitura_atual():
    return _LEITURA.get()


def consulta_medias(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with leitura_medias():
            return function(*args, **kwargs)
    return wrapped


def etapa(nome):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with medir_etapa(nome):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def registrar_tempo(nome, inicio):
    current = _LEITURA.get()
    if current is not None:
        current.metrics[nome] = current.metrics.get(nome, 0) + (time.perf_counter() - inicio) * 1000


def registrar_contagem(nome, quantidade=1):
    current = _LEITURA.get()
    if current is not None:
        current.metrics[nome] = current.metrics.get(nome, 0) + quantidade


def fotos_da_consulta(function):
    @wraps(function)
    def wrapped(client_id, *args, **kwargs):
        current = _LEITURA.get()
        snapshot = current.values.get(("cadastro_fotos", client_id)) if current else None
        if snapshot is None:
            return function(client_id, *args, **kwargs)
        from backend.services.cadastro_fotos import _cadastro_fotos_contexto_leitura
        with _cadastro_fotos_contexto_leitura(snapshot):
            return function(client_id, *args, **kwargs)
    return wrapped


def linhas_ncm_referencia(client_id, garantir, conectar, campos):
    def carregar():
        garantir(client_id)
        conn = conectar(client_id)
        try:
            selected = "ncm,cest,descricao,descricao_completa,ii,ipi,pis,cofins" if leitura_atual() else campos
            return conn.execute("SELECT " + selected + " FROM ncm_referencia").fetchall()
        finally:
            conn.close()
    return reutilizar(("ncm", client_id, garantir, conectar), carregar)


def reutilizar(chave, construir):
    current = _LEITURA.get()
    if current is None:
        return construir()
    if chave not in current.values:
        current.values[chave] = construir()
    return current.values[chave]


def chave_arquivo(path):
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def capturar_bytes(path, content=None):
    current = _LEITURA.get()
    if current is None:
        return content if content is not None else Path(path).read_bytes()
    key = ("bytes", chave_arquivo(path))
    if content is not None and current is not None:
        current.values.setdefault(key, content)
    def read():
        registrar_contagem("leituras_fisicas")
        with medir_etapa("csv_leitura"):
            return Path(path).read_bytes()
    return reutilizar(key, read)


def ler_csv(path, **kwargs):
    import pandas as pd
    if _LEITURA.get() is None:
        return pd.read_csv(path, **kwargs)
    # Parser options are part of the key: stock inference and fiscal string
    # parsing intentionally keep their existing, different number semantics.
    key = ("pandas", chave_arquivo(path), repr(sorted(kwargs.items())))
    def parse():
        source = capturar_bytes(path)
        registrar_contagem("csv_parses")
        with medir_etapa("csv_parse"):
            return pd.read_csv(BytesIO(source), **kwargs)
    frame = reutilizar(key, parse)
    registrar_contagem("csv_copias")
    with medir_etapa("csv_copia"):
        return frame.copy(deep=True)


@contextmanager
def medir_etapa(nome):
    started = time.perf_counter()
    try:
        yield
    finally:
        current = _LEITURA.get()
        if current is not None:
            current.metrics[nome] = current.metrics.get(nome, 0) + (time.perf_counter() - started) * 1000
