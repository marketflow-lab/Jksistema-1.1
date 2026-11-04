"""Short, bounded cache for successful Importações provider reads."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
from threading import Lock
from time import monotonic, time


TTL_SEGUNDOS = 60


@dataclass(frozen=True)
class Consulta:
    valor: object
    consultado_em_ms: int
    expira_em: float


class CacheConsultas:
    def __init__(self, limite: int):
        self._limite = limite
        self._dados: OrderedDict[tuple, Consulta] = OrderedDict()
        self._em_andamento: dict[tuple, Future] = {}
        self._lock = Lock()

    def consultar(self, chave: tuple, carregar) -> Consulta:
        with self._lock:
            copia = self._dados.get(chave)
            if copia is not None and copia.expira_em > monotonic():
                self._dados.move_to_end(chave)
                return copia
            self._dados.pop(chave, None)
            pendente = self._em_andamento.get(chave)
            dono = pendente is None
            if dono:
                pendente = self._em_andamento[chave] = Future()

        if not dono:
            return pendente.result()

        try:
            valor = carregar()
            consulta = Consulta(valor, int(time() * 1000), monotonic() + TTL_SEGUNDOS)
            with self._lock:
                self._dados[chave] = consulta
                while len(self._dados) > self._limite:
                    self._dados.popitem(last=False)
            pendente.set_result(consulta)
            return consulta
        except BaseException as erro:
            pendente.set_exception(erro)
            raise
        finally:
            with self._lock:
                self._em_andamento.pop(chave, None)
