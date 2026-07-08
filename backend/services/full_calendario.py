"""Commercial calendar helpers for the Full module."""

from __future__ import annotations

import datetime as dt
import logging
import re
from datetime import datetime
from typing import Callable

import requests
from fastapi import HTTPException


logger = logging.getLogger("jk_sistema")
_env_config_bool: Callable[[object], bool] = lambda keys, default=True: default
_cache_get: Callable[..., object] = lambda key, **kwargs: None
_cache_set: Callable[..., object] = lambda key, value, **kwargs: None


def configure_full_calendario_context(
    *,
    logger_ref=None,
    env_config_bool: Callable[[object], bool] | None = None,
    cache_get: Callable[..., object] | None = None,
    cache_set: Callable[..., object] | None = None,
) -> None:
    global logger, _env_config_bool, _cache_get, _cache_set
    if logger_ref is not None:
        logger = logger_ref
    if env_config_bool is not None:
        _env_config_bool = env_config_bool
    if cache_get is not None:
        _cache_get = cache_get
    if cache_set is not None:
        _cache_set = cache_set


def _full_feriados_nacionais_ano(ano: int) -> dict:
    ano = int(ano or datetime.now().year)
    cache_key = f"full:feriados_nacionais:{ano}"
    cached = _cache_get(cache_key, ttl=7 * 24 * 60 * 60)
    if cached is not None:
        return cached

    url = f"https://brasilapi.com.br/api/feriados/v1/{ano}"
    feriados: list[dict[str, str]] = []
    erro = ""
    verify_ssl = _env_config_bool(("FERIADOS_VERIFY_SSL", "BRASILAPI_VERIFY_SSL"), default=True)
    try:
        try:
            resp = requests.get(url, timeout=12, verify=verify_ssl)
        except requests.exceptions.SSLError:
            if not verify_ssl:
                raise
            logger.warning("[FULL CALENDARIO] SSL da BrasilAPI falhou para %s; repetindo sem verificacao.", ano)
            resp = requests.get(url, timeout=12, verify=False)
        if resp.status_code == 200:
            payload = resp.json() or []
            if isinstance(payload, list):
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    data = str(item.get("date") or "").strip()[:10]
                    nome = str(item.get("name") or "").strip()
                    tipo = str(item.get("type") or "").strip()
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", data):
                        feriados.append({"data": data, "nome": nome or "Feriado nacional", "tipo": tipo or "national"})
        else:
            erro = f"BrasilAPI retornou HTTP {resp.status_code}"
    except Exception as e:
        erro = str(e)

    resultado = {
        "ano": ano,
        "fonte": "BrasilAPI",
        "fonte_url": url,
        "feriados": feriados,
        "erro": erro,
    }
    _cache_set(cache_key, resultado)
    return resultado


def calendario_comercial_full_payload(ano: int | None = None, mes: int | None = None) -> dict:
    hoje = datetime.now()
    ano_ref = int(ano or hoje.year)
    mes_ref = int(mes or hoje.month)
    if ano_ref < 2000 or ano_ref > 2100:
        raise HTTPException(status_code=400, detail="Ano invalido para calendario comercial.")
    if mes_ref < 1 or mes_ref > 12:
        raise HTTPException(status_code=400, detail="Mes invalido para calendario comercial.")

    primeiro_dia = dt.date(ano_ref, mes_ref, 1)
    inicio_grid = primeiro_dia - dt.timedelta(days=(primeiro_dia.weekday() + 1) % 7)
    fim_grid = inicio_grid + dt.timedelta(days=41)
    anos_grid = range(inicio_grid.year, fim_grid.year + 1)

    feriados_por_data: dict[str, dict[str, str]] = {}
    erros: list[str] = []
    fonte_urls: list[str] = []
    for ano_item in anos_grid:
        payload = _full_feriados_nacionais_ano(int(ano_item))
        fonte_url = str(payload.get("fonte_url") or "").strip()
        if fonte_url:
            fonte_urls.append(fonte_url)
        erro = str(payload.get("erro") or "").strip()
        if erro:
            erros.append(f"{ano_item}: {erro}")
        for feriado in payload.get("feriados") or []:
            if isinstance(feriado, dict) and feriado.get("data"):
                feriados_por_data[str(feriado.get("data"))] = feriado

    dias = []
    resumo = {"dias_comerciais": 0, "feriados": 0, "fins_semana": 0}
    feriados_mes = []
    for offset in range(42):
        data = inicio_grid + dt.timedelta(days=offset)
        data_iso = data.isoformat()
        feriado = feriados_por_data.get(data_iso)
        fim_semana = data.weekday() >= 5
        mes_atual = data.month == mes_ref and data.year == ano_ref
        dia_comercial = not fim_semana and not bool(feriado)
        if mes_atual:
            if dia_comercial:
                resumo["dias_comerciais"] += 1
            if fim_semana:
                resumo["fins_semana"] += 1
            if feriado:
                resumo["feriados"] += 1
                feriados_mes.append({
                    "data": data_iso,
                    "nome": feriado.get("nome") or "Feriado nacional",
                    "tipo": feriado.get("tipo") or "national",
                })
        dias.append({
            "data": data_iso,
            "dia": data.day,
            "mes_atual": mes_atual,
            "fim_semana": fim_semana,
            "feriado": bool(feriado),
            "nome_feriado": (feriado or {}).get("nome") or "",
            "tipo_feriado": (feriado or {}).get("tipo") or "",
            "dia_comercial": dia_comercial,
        })

    return {
        "success": True,
        "ano": ano_ref,
        "mes": mes_ref,
        "fonte": "BrasilAPI - feriados nacionais",
        "fonte_urls": sorted(set(fonte_urls)),
        "dias": dias,
        "feriados": feriados_mes,
        "resumo": resumo,
        "erros": erros,
    }


__all__ = [
    "configure_full_calendario_context",
    "_full_feriados_nacionais_ano",
    "calendario_comercial_full_payload",
]
