"""Source projections reused by the Medias view while it rebuilds a result."""

from collections import OrderedDict
import csv
import hashlib
import os
import sys
from threading import RLock

from fastapi import HTTPException

from backend.services.medias_compras_leitura import capturar_bytes, registrar_contagem


_CAMPOS_SALDO = {"saldo", "saldo_loja", "saldo_total"}
_LIMITE_ITENS = 96
_LIMITE_BYTES = 32 * 1024 * 1024
_LOCK = RLock()
_CACHE = OrderedDict()
_BYTES = 0
_AUSENTE = object()


def _digest(value):
    return hashlib.sha256(repr(value).encode("utf-8")).digest()


def _peso(value):
    peso = sys.getsizeof(value)
    if isinstance(value, dict):
        return peso + sum(_peso(key) + _peso(item) for key, item in value.items())
    if isinstance(value, (tuple, list, set, frozenset)):
        return peso + sum(_peso(item) for item in value)
    return peso


def _ler(key):
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return _AUSENTE
        _CACHE.move_to_end(key)
        return entry[0]


def _publicar(key, value):
    global _BYTES
    peso = _peso(key) + _peso(value)
    if peso > _LIMITE_BYTES:
        return
    with _LOCK:
        anterior = _CACHE.pop(key, None)
        if anterior is not None:
            _BYTES -= anterior[1]
        _CACHE[key] = (value, peso)
        _BYTES += peso
        while len(_CACHE) > _LIMITE_ITENS or _BYTES > _LIMITE_BYTES:
            _, (_, removido) = _CACHE.popitem(last=False)
            _BYTES -= removido


def _caminho_lexical(path):
    return os.path.normcase(os.path.abspath(path)) if path else ""


def _revisoes(assinatura, compilado, estoque_legado, cadastro_legado):
    fontes = {etapa: [] for etapa in ("estoque", "transito", "catalogo", "fotos", "compilado")}
    for entrada in assinatura[1]:
        path = entrada[0]
        partes = path.replace("\\", "/").casefold().split("/")
        nome = partes[-1]
        if "cadastro_fotos" in partes:
            fontes["fotos"].append(entrada)
        if nome == "produtos_compilado.csv" or path == estoque_legado:
            fontes["estoque"].append(entrada)
            if path == compilado:
                fontes["compilado"].append(entrada)
        if nome == "listas_pedidos.json":
            fontes["transito"].append(entrada)
        if (
            path == cadastro_legado
            or (nome.startswith(("cadastro_produtos", "cadastro_custos")) and nome.endswith(".csv"))
            or nome in {"lojas_config.json", "lojas_config.json.bak"}
        ):
            fontes["catalogo"].append(entrada)
    # A directory or file may change without changing the name of any SKU.
    # Keep the physical photo revision for both the local map and the legacy
    # catalog projection that applies photo fallback to its rows.
    return {etapa: _digest(entradas) if entradas else None for etapa, entradas in fontes.items()}


def _catalogo_do_compilado(path):
    from backend.services.cadastro_lojas_produtos import _ler_csv_capturado

    try:
        bruto = capturar_bytes(path, fixar=True)
    except FileNotFoundError:
        bruto = capturar_bytes(path, b"", fixar=True)
    linhas, colunas = _ler_csv_capturado(bruto)
    campos = tuple(campo for campo in colunas if campo not in _CAMPOS_SALDO)
    digest = hashlib.sha256(_digest(campos))
    for linha in linhas:
        digest.update(repr(tuple(linha[campo] for campo in campos)).encode("utf-8"))
        digest.update(b"\0")
    return digest.digest()


class EtapasVisao:
    """Reuse stage outputs validated against the current tenant and sources.

    Pending outputs are private to this attempt until ResultadoCache runs its
    normal post-calculation probe. A rejected aggregate can still validate an
    independent stage for the next attempt.
    """

    def __init__(self, client_id, fonte, tenant, *, estoque_legado=None, cadastro_legado=None):
        self._autoridade = fonte.autoridade
        self._escopo = (str(client_id), _digest(fonte.autoridade))
        self._assinatura = fonte.assinatura
        self._caminhos = (
            _caminho_lexical(os.path.join(tenant, "produtos_compilado.csv")),
            _caminho_lexical(estoque_legado),
            _caminho_lexical(cadastro_legado),
        )
        self._revisoes = None
        self._pendentes = []

    def _versoes(self):
        if self._revisoes is None:
            self._revisoes = _revisoes(self._assinatura, *self._caminhos)
        return self._revisoes

    def _chave(self, etapa, dependencias, variacao):
        versoes = self._versoes()
        return (self._escopo, etapa, tuple(versoes[fonte] for fonte in dependencias), variacao)

    def obter(self, etapa, carregar, *, variacao=(), completo=None):
        dependencias = (etapa,)
        chave = self._chave(etapa, dependencias, variacao)
        salvo = _ler(chave)
        if salvo is not _AUSENTE:
            registrar_contagem(f"{etapa}_cache_hits")
            return salvo
        valor = carregar()
        if completo is None or completo(valor):
            self._pendentes.append((chave, dependencias, valor))
        return valor

    def catalogo(self, incluir_fotos, carregar):
        if self._versoes()["compilado"] is None:
            return carregar()
        dependencias = ("catalogo", "fotos") if incluir_fotos else ("catalogo",)
        chave_compilado = self._chave("compilado", ("compilado",), ())
        revisao_compilado = _ler(chave_compilado)
        if revisao_compilado is not _AUSENTE:
            chave = self._chave("catalogo", dependencias, (incluir_fotos, revisao_compilado))
            salvo = _ler(chave)
            if salvo is not _AUSENTE:
                registrar_contagem("catalogo_cache_hits")
                return salvo

        # Use the same captured bytes as Cadastro. Even when a stock writer
        # replaces the physical CSV, a catalog projection computed from those
        # bytes can be keyed by its own logical revision.
        try:
            revisao_compilado = _catalogo_do_compilado(self._caminhos[0])
        except (OSError, csv.Error, HTTPException):
            return carregar()
        self._pendentes.append((chave_compilado, ("compilado",), revisao_compilado))
        chave = self._chave("catalogo", dependencias, (incluir_fotos, revisao_compilado))
        salvo = _ler(chave)
        if salvo is not _AUSENTE:
            registrar_contagem("catalogo_cache_hits")
            return salvo
        valor = carregar()
        if valor.completo:
            self._pendentes.append((chave, dependencias, valor))
        return valor

    def confirmar(self, fonte):
        if not self._pendentes or fonte.pendente or fonte.autoridade != self._autoridade:
            return
        atual = _revisoes(fonte.assinatura, *self._caminhos)
        for chave, dependencias, valor in self._pendentes:
            if all(self._versoes()[etapa] == atual[etapa] for etapa in dependencias):
                _publicar(chave, valor)
        self._pendentes.clear()
