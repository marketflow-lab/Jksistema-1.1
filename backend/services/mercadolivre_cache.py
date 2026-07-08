"""In-memory cache helpers for Mercado Livre endpoints."""

from __future__ import annotations

import time


_ml_cache: dict = {}
_ML_CACHE_TTL = 120
_ML_CACHE_TTL_CAMPANHA = 1800
_ML_CACHE_TTL_CAMPANHA_STALE = 21600
_ML_CACHE_TTL_CONTAGENS = 900


def _ml_cache_get(chave: str, ttl: int = None):
    entry = _ml_cache.get(chave)
    ttl_usado = ttl if ttl is not None else _ML_CACHE_TTL
    if entry:
        ts, dados = entry
        if (time.time() - ts) < ttl_usado:
            return dados
    return None


def _ml_cache_get_stale(chave: str, max_age: int):
    if not max_age or max_age <= 0:
        return None
    entry = _ml_cache.get(chave)
    if not entry:
        return None
    ts, dados = entry
    if max_age is not None and max_age > 0 and (time.time() - float(ts)) > float(max_age):
        return None
    return dados


def _ml_cache_set(chave: str, dados):
    _ml_cache[chave] = (time.time(), dados)
    if len(_ml_cache) > 200:
        agora = time.time()
        expiradas = [k for k, (ts, _) in _ml_cache.items() if (agora - ts) >= _ML_CACHE_TTL]
        for k in expiradas:
            _ml_cache.pop(k, None)


def _cache_invalidar_loja(client_id: str, loja: str):
    """Remove todas as entradas de cache de uma loja especifica."""
    prefixos = (
        f"anuncios:{client_id}:{loja}:",
        f"visitas:{client_id}:{loja}:",
        f"promocoes:{client_id}:{loja}",
        f"campanha_itens:{client_id}:{loja}:",
        f"camp_contagens:{client_id}:{loja}",
    )
    chaves = [k for k in list(_ml_cache.keys()) if any(k.startswith(p) for p in prefixos)]
    for k in chaves:
        _ml_cache.pop(k, None)
    return len(chaves)


def invalidar_cache_mercado_livre(client_id: str, loja: str) -> dict:
    removidas = _cache_invalidar_loja(client_id, loja)
    return {"success": True, "entradas_removidas": removidas}
