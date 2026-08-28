"""Rastreamento AIS de navios usados nos embarques de Importacoes.

A credencial do provedor permanece somente no backend. O frontend recebe uma
resposta normalizada e nunca tem acesso ao ``x-api-key`` usado na consulta.
"""

from __future__ import annotations

import copy
import os
import threading
import time
from typing import Any, Callable, Mapping

import requests


DATALASTIC_VESSEL_URL = "https://api.datalastic.com/api/v0/vessel"
DEFAULT_CACHE_TTL_SECONDS = 300.0
DEFAULT_TIMEOUT_SECONDS = 8.0


class ErroRastreamentoNavio(RuntimeError):
    """Falha controlada devolvida pela camada de rastreamento."""

    def __init__(self, codigo: str, mensagem: str, status_code: int) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem
        self.status_code = status_code


_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()


def limpar_cache_rastreamento_navio() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _texto(valor: Any) -> str:
    return str(valor or "").strip()


def _numero_opcional(valor: Any) -> float | int | None:
    if valor is None or valor == "":
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return int(numero) if numero.is_integer() else numero


def _config_numero(
    environ: Mapping[str, str],
    nome: str,
    padrao: float,
    minimo: float,
    maximo: float,
) -> float:
    try:
        valor = float(_texto(environ.get(nome)) or padrao)
    except (TypeError, ValueError):
        valor = padrao
    return max(minimo, min(maximo, valor))


def _validar_imo(imo: str) -> str:
    valor = _texto(imo)
    if not (valor.isdigit() and len(valor) == 7):
        raise ErroRastreamentoNavio(
            "imo_invalido",
            "O IMO deve conter exatamente 7 numeros.",
            400,
        )
    soma = sum(int(valor[indice]) * peso for indice, peso in enumerate(range(7, 1, -1)))
    if soma % 10 != int(valor[-1]):
        raise ErroRastreamentoNavio(
            "imo_invalido",
            "O IMO informado nao passou na validacao do digito verificador.",
            400,
        )
    return valor


def _validar_mmsi(mmsi: str) -> str:
    valor = _texto(mmsi)
    if not (valor.isdigit() and len(valor) == 9):
        raise ErroRastreamentoNavio(
            "mmsi_invalido",
            "O MMSI deve conter exatamente 9 numeros.",
            400,
        )
    return valor


def identificar_navio(*, imo: str = "", mmsi: str = "") -> tuple[str, str]:
    imo_limpo = _texto(imo)
    mmsi_limpo = _texto(mmsi)
    if not imo_limpo and not mmsi_limpo:
        raise ErroRastreamentoNavio(
            "identificador_ausente",
            "Informe o IMO ou o MMSI do navio para consultar a posicao AIS.",
            400,
        )
    imo_validado = _validar_imo(imo_limpo) if imo_limpo else ""
    mmsi_validado = _validar_mmsi(mmsi_limpo) if mmsi_limpo else ""
    if imo_validado:
        return "imo", imo_validado
    return "mmsi", mmsi_validado


def _normalizar_resposta_datalastic(
    payload: Mapping[str, Any],
    *,
    identificador_tipo: str,
    identificador_valor: str,
) -> dict[str, Any]:
    meta = payload.get("meta")
    if isinstance(meta, Mapping) and meta.get("success") is False:
        raise ErroRastreamentoNavio(
            "navio_nao_encontrado",
            "A API nao encontrou uma posicao para esse navio.",
            404,
        )

    dados = payload.get("data")
    if isinstance(dados, list):
        dados = dados[0] if dados else None
    if not isinstance(dados, Mapping):
        raise ErroRastreamentoNavio(
            "resposta_invalida",
            "A API de rastreamento respondeu sem dados validos do navio.",
            502,
        )

    latitude = _numero_opcional(dados.get("lat"))
    longitude = _numero_opcional(dados.get("lon"))
    if latitude is None or longitude is None:
        raise ErroRastreamentoNavio(
            "posicao_indisponivel",
            "A API encontrou o navio, mas ainda nao possui coordenadas AIS validas.",
            404,
        )

    return {
        "status": "ok",
        "provider": {
            "id": "datalastic",
            "nome": "Datalastic AIS",
        },
        "identificador": {
            "tipo": identificador_tipo,
            "valor": identificador_valor,
        },
        "navio": {
            "nome": _texto(dados.get("name")),
            "imo": _texto(dados.get("imo")),
            "mmsi": _texto(dados.get("mmsi")),
            "tipo": _texto(dados.get("type_specific") or dados.get("type")),
            "pais": _texto(dados.get("country_iso")),
        },
        "posicao": {
            "latitude": latitude,
            "longitude": longitude,
            "velocidade_nos": _numero_opcional(dados.get("speed")),
            "curso_graus": _numero_opcional(dados.get("course")),
            "proa_graus": _numero_opcional(dados.get("heading")),
            "status_navegacao": _texto(dados.get("navigation_status")),
            "atualizada_em_utc": _texto(dados.get("last_position_UTC")),
        },
        "viagem": {
            "destino": _texto(dados.get("destination")),
            "eta_utc": _texto(dados.get("eta_UTC")),
        },
        "cached": False,
    }


def rastrear_navio_datalastic(
    *,
    imo: str = "",
    mmsi: str = "",
    environ: Mapping[str, str] | None = None,
    http_get: Callable[..., Any] | None = None,
    now_fn: Callable[[], float] | None = None,
) -> dict[str, Any]:
    ambiente = os.environ if environ is None else environ
    identificador_tipo, identificador_valor = identificar_navio(imo=imo, mmsi=mmsi)
    chave_api = _texto(ambiente.get("JK_DATALASTIC_API_KEY") or ambiente.get("DATALASTIC_API_KEY"))
    if not chave_api:
        raise ErroRastreamentoNavio(
            "api_nao_configurada",
            "A API AIS ainda nao esta configurada. Defina JK_DATALASTIC_API_KEY no servidor.",
            503,
        )

    cache_ttl = _config_numero(
        ambiente,
        "JK_DATALASTIC_CACHE_TTL_SECONDS",
        DEFAULT_CACHE_TTL_SECONDS,
        30.0,
        3600.0,
    )
    timeout = _config_numero(
        ambiente,
        "JK_DATALASTIC_TIMEOUT_SECONDS",
        DEFAULT_TIMEOUT_SECONDS,
        2.0,
        30.0,
    )
    agora = (now_fn or time.monotonic)()
    chave_cache = f"{identificador_tipo}:{identificador_valor}"
    with _CACHE_LOCK:
        item_cache = _CACHE.get(chave_cache)
        if item_cache and agora - item_cache[0] < cache_ttl:
            resposta_cache = copy.deepcopy(item_cache[1])
            resposta_cache["cached"] = True
            return resposta_cache

    transporte = http_get or requests.get
    try:
        resposta = transporte(
            DATALASTIC_VESSEL_URL,
            params={identificador_tipo: identificador_valor},
            headers={"Accept": "application/json", "x-api-key": chave_api},
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise ErroRastreamentoNavio(
            "api_timeout",
            "A API AIS demorou demais para responder. Tente novamente em instantes.",
            504,
        ) from exc
    except requests.RequestException as exc:
        raise ErroRastreamentoNavio(
            "api_indisponivel",
            "Nao foi possivel conectar a API AIS neste momento.",
            502,
        ) from exc

    status_code = int(getattr(resposta, "status_code", 0) or 0)
    if status_code in {401, 403}:
        raise ErroRastreamentoNavio(
            "api_credencial_invalida",
            "A API AIS recusou a credencial configurada no servidor.",
            503,
        )
    if status_code == 402:
        raise ErroRastreamentoNavio(
            "api_creditos_esgotados",
            "Os creditos ou a assinatura da API AIS estao indisponiveis.",
            503,
        )
    if status_code == 429:
        raise ErroRastreamentoNavio(
            "api_limite_excedido",
            "O limite de consultas da API AIS foi atingido. Tente novamente mais tarde.",
            429,
        )
    if status_code == 404:
        raise ErroRastreamentoNavio(
            "navio_nao_encontrado",
            "A API nao encontrou uma posicao para esse navio.",
            404,
        )
    if status_code < 200 or status_code >= 300:
        raise ErroRastreamentoNavio(
            "api_erro",
            "A API AIS respondeu com erro temporario.",
            502,
        )

    try:
        payload = resposta.json()
    except (TypeError, ValueError) as exc:
        raise ErroRastreamentoNavio(
            "resposta_invalida",
            "A API AIS respondeu em um formato invalido.",
            502,
        ) from exc
    if not isinstance(payload, Mapping):
        raise ErroRastreamentoNavio(
            "resposta_invalida",
            "A API AIS respondeu em um formato invalido.",
            502,
        )

    normalizada = _normalizar_resposta_datalastic(
        payload,
        identificador_tipo=identificador_tipo,
        identificador_valor=identificador_valor,
    )
    with _CACHE_LOCK:
        _CACHE[chave_cache] = (agora, copy.deepcopy(normalizada))
    return normalizada


__all__ = [
    "DATALASTIC_VESSEL_URL",
    "ErroRastreamentoNavio",
    "identificar_navio",
    "limpar_cache_rastreamento_navio",
    "rastrear_navio_datalastic",
]
