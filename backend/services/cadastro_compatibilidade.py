"""Read-only compatibility view over the store-scoped Cadastro records.

Legacy consumers do not carry a ``store_id``.  They may only receive values
that are identical across every active store occurrence of the same SKU; a
conflicting field is deliberately blank instead of selecting an arbitrary
store.  SKUs already controlled by ``cadastro_produtos_lojas.csv`` never fall
back to the stale global row.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
import re
from typing import Any, Callable, Iterable
import unicodedata

from fastapi import HTTPException
from backend.services import cadastro_lojas_produtos as _store
from backend.services.cadastro_fotos import (
    _cadastro_fotos_capturar_leitura,
    _cadastro_fotos_contexto_leitura,
)

from backend.services.cadastro_lojas_produtos import (
    _aliases_nome_loja,
    _bloquear_config_e_arquivo_cadastro,
    _cadastro_produtos_lojas_path,
    _ler_registros_persistidos,
    _listar_produtos_loja_sync,
    _lock_arquivo,
    _lojas_atuais,
    _normalizar_sku_chave,
)


_CAMPOS_INTERNOS = {
    "store_id",
    "sku_normalizado",
    "loja_sync",
    "row_version",
    "updated_at_utc",
    "deleted_at_utc",
    "scope_source",
}
_CAMPOS_RESERVADOS_MUTACAO_LEGADA = {
    *_CAMPOS_INTERNOS,
    "loja",
}


def _texto_compatibilidade(valor: Any) -> str:
    if valor is None:
        return ""
    return str(valor).strip()


def _normalizar_loja_leitura(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or "").strip())
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", " ", texto.casefold())
    return re.sub(r"\s+", " ", texto).strip()


def resolver_loja_ativa_para_leitura(
    client_id: str,
    loja_referencia: Any = "",
    store_id: Any = "",
    *,
    lojas: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve a identidade imutavel da loja sem escolher nomes ambiguos.

    ``store_id`` e autoridade quando informado. O nome atual ou historico e
    aceito apenas como adaptador legado quando identifica exatamente uma loja
    ativa do tenant.
    """

    lojas_ativas = (
        _lojas_atuais(client_id)
        if lojas is None
        else [dict(loja) for loja in lojas if isinstance(loja, dict)]
    )
    store_id_alvo = str(store_id or "").strip()
    referencia = str(loja_referencia or "").strip()
    referencia_norm = _normalizar_loja_leitura(referencia)

    if store_id_alvo:
        candidatas = [
            loja
            for loja in lojas_ativas
            if str(loja.get("store_id") or "").strip() == store_id_alvo
        ]
    else:
        candidatas = [
            loja
            for loja in lojas_ativas
            if referencia_norm
            and any(
                _normalizar_loja_leitura(alias) == referencia_norm
                for alias in _aliases_nome_loja(loja)
            )
        ]

    if len(candidatas) != 1:
        return {
            "store_id": "",
            "loja": "",
            "loja_resolvida": False,
            "scope": "unresolved",
        }

    loja = candidatas[0]
    identidade = str(loja.get("store_id") or "").strip()
    nome = str(loja.get("nome") or "").strip()
    if not identidade or not nome:
        return {
            "store_id": "",
            "loja": "",
            "loja_resolvida": False,
            "scope": "unresolved",
        }
    return {
        "store_id": identidade,
        "loja": nome,
        "loja_resolvida": True,
        "scope": "store",
    }


def skus_controlados_cadastro_lojas(
    client_id: str,
    skus: Iterable[Any] | None = None,
) -> set[str]:
    """Return durable store-owned SKU identities, including tombstones.

    This deliberately reads only the canonical store file.  Enriched shadows
    from legacy/compiled sources do not turn a SKU into a controlled mutation
    target, while a tombstone must keep blocking an unscoped legacy write.
    """

    solicitados = None
    if skus is not None:
        solicitados = {
            _normalizar_sku_chave(sku)
            for sku in skus
            if _normalizar_sku_chave(sku)
        }
        if not solicitados:
            return set()

    caminho = _cadastro_produtos_lojas_path(client_id)
    with _lock_arquivo(caminho):
        registros, _ = _ler_registros_persistidos(client_id)
        controlados = {
            str(item.get("sku_normalizado") or "").strip()
            for item in registros
            if str(item.get("sku_normalizado") or "").strip()
        }
    if solicitados is None:
        return controlados
    return controlados & solicitados


def exigir_mutacao_legada_sem_sku_controlado(
    client_id: str,
    skus: Iterable[Any],
) -> None:
    """Fail closed before an unscoped writer can touch a store-owned SKU."""

    controlados = sorted(skus_controlados_cadastro_lojas(client_id, skus))
    if not controlados:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "store_id_required",
            "message": (
                "Este SKU possui cadastro separado por loja. "
                "Informe o store_id e use a rota de cadastro da loja."
            ),
            "skus": controlados,
        },
    )


def exigir_mutacao_legada_sem_campos_loja(campos: Iterable[Any]) -> None:
    """Reject store-scoped metadata on an unscoped Cadastro mutation."""

    proibidos = sorted(
        {
            str(campo or "").strip().casefold()
            for campo in campos
            if str(campo or "").strip().casefold()
            in _CAMPOS_RESERVADOS_MUTACAO_LEGADA
        }
    )
    if not proibidos:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "store_id_required",
            "message": (
                "Metadados reservados de loja exigem a rota de cadastro por loja "
                "e um store_id exato."
            ),
            "campos": proibidos,
        },
    )


@contextmanager
def bloquear_mutacao_legada_sem_sku_controlado(
    client_id: str,
    skus: Iterable[Any],
):
    """Keep the store-identity guard valid through the whole legacy write.

    The canonical store file is the coordination point shared with scoped
    Cadastro writers.  Holding its reentrant lock prevents a store-owned row
    from being committed between the guard and the legacy side effects.
    """

    skus_materializados = list(skus)
    caminho = _cadastro_produtos_lojas_path(client_id)
    with _bloquear_config_e_arquivo_cadastro(client_id, caminho):
        exigir_mutacao_legada_sem_sku_controlado(client_id, skus_materializados)
        yield


def _consolidar_sku_seguro(
    sku_normalizado: str,
    produtos: list[dict[str, Any]],
    *,
    total_lojas: int,
) -> dict[str, Any]:
    produtos_ordenados = sorted(
        produtos,
        key=lambda item: str(item.get("store_id") or ""),
    )
    store_ids = sorted(
        {
            str(item.get("store_id") or "").strip()
            for item in produtos_ordenados
            if str(item.get("store_id") or "").strip()
        }
    )
    nomes_lojas = sorted(
        {
            str(item.get("loja_sync") or "").strip()
            for item in produtos_ordenados
            if str(item.get("loja_sync") or "").strip()
        }
    )

    if total_lojas == 1 and len(produtos_ordenados) == 1:
        resultado = {
            str(chave): valor
            for chave, valor in produtos_ordenados[0].items()
            if str(chave) not in {
                "sku_normalizado",
                "row_version",
                "updated_at_utc",
                "deleted_at_utc",
                "scope_source",
            }
        }
        resultado["sku"] = str(resultado.get("sku") or sku_normalizado)
        resultado["store_ids"] = "|".join(store_ids)
        resultado["store_scope_ambiguous"] = False
        resultado["campos_ambiguos"] = ""
        return resultado

    colunas = sorted(
        {
            str(chave)
            for produto in produtos_ordenados
            for chave in produto
            if str(chave) not in _CAMPOS_INTERNOS and str(chave) != "sku"
        }
    )
    resultado: dict[str, Any] = {"sku": sku_normalizado}
    ambiguos: list[str] = []
    for coluna in colunas:
        valores = [_texto_compatibilidade(produto.get(coluna)) for produto in produtos_ordenados]
        # A legacy caller has no store identity.  Absence (including a
        # tombstone) in another configured store is a real scope difference,
        # not permission to reuse the only non-empty value globally.
        valores.extend([""] * max(0, total_lojas - len(produtos_ordenados)))
        distintos = set(valores)
        if len(distintos) == 1:
            resultado[coluna] = valores[0]
        else:
            resultado[coluna] = ""
            ambiguos.append(coluna)

    resultado["store_id"] = ""
    resultado["loja_sync"] = ""
    resultado["store_ids"] = "|".join(store_ids)
    resultado["store_scope_ambiguous"] = True
    resultado["campos_ambiguos"] = "|".join(ambiguos)
    return resultado


def _capturar_cadastro_para_leitura(client_id: str):
    from backend.services.medias_compras_leitura import reutilizar, chave_arquivo, medir_etapa, leitura_atual
    if leitura_atual() is None:
        return _capturar_cadastro_para_leitura_sem_cache(client_id)
    with medir_etapa("cadastro"):
        return reutilizar(
            ("cadastro", client_id, chave_arquivo(_store.get_tenant_path(client_id))),
            lambda: _capturar_cadastro_para_leitura_sem_cache(client_id),
        )


def _capturar_cadastro_para_leitura_sem_cache(client_id: str):
    """Capture a coherent input once; never enrich products under writer locks."""

    tenant = Path(_store.get_tenant_path(client_id))
    caminho = _cadastro_produtos_lojas_path(client_id)
    legados = {arquivo: tenant / arquivo for arquivo in _store._ARQUIVOS_LEGADOS.values()}
    with _store.integracoes.lojas_config_lock(client_id):
        with _store.path_locks_for([caminho, *legados.values()]):
            lojas = _lojas_atuais(client_id)
            registros, _ = _ler_registros_persistidos(client_id)
            fotos = _cadastro_fotos_capturar_leitura(client_id, lojas)
            fontes = {}
            for arquivo, path in legados.items():
                try:
                    from backend.services.medias_compras_leitura import capturar_bytes
                    fontes[arquivo] = capturar_bytes(path)
                except FileNotFoundError:
                    fontes[arquivo] = b""
                except OSError as exc:
                    raise HTTPException(
                        status_code=500, detail="Nao foi possivel ler o cadastro por loja.",
                    ) from exc
    from backend.services.medias_compras_leitura import leitura_atual
    current = leitura_atual()
    if current is not None:
        current.values[("cadastro_fotos", client_id)] = fotos
    return registros, lojas, fontes, fotos


def visao_compatibilidade_produtos_lojas(
    client_id: str, *, skus: Iterable[Any] | None = None,
    _normalizar_sku_consulta: Callable[[Any], str] | None = None,
) -> dict[str, Any]:
    """Return safe global rows plus every SKU owned by the new store file."""

    skus = tuple(skus) if skus is not None else None
    opcoes_filtro = ({"_normalizar_sku_consulta": _normalizar_sku_consulta}
                     if _normalizar_sku_consulta is not None else {})
    registros, lojas_atuais, fontes, fotos = _capturar_cadastro_para_leitura(client_id)
    with _cadastro_fotos_contexto_leitura(fotos):
        skus_controlados = {
            str(item.get("sku_normalizado") or "").strip()
            for item in registros
            if str(item.get("sku_normalizado") or "").strip()
        }
        if not skus_controlados:
            return {"produtos": [], "skus_controlados": set()}

        por_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for loja in lojas_atuais:
            store_id = str(loja.get("store_id") or "").strip()
            if not store_id:
                continue
            produtos_loja = _listar_produtos_loja_sync(
                client_id, store_id, skus=skus, _registros_persistidos=registros,
                _lojas_capturadas=lojas_atuais, _fontes_capturadas=fontes, **opcoes_filtro,
            )
            for produto in produtos_loja:
                sku = _normalizar_sku_chave(
                    produto.get("sku_normalizado") or produto.get("sku") or ""
                )
                if sku in skus_controlados:
                    por_sku[sku].append(produto)

    produtos = [
        _consolidar_sku_seguro(
            sku,
            por_sku[sku],
            total_lojas=len(lojas_atuais),
        )
        for sku in sorted(por_sku)
        if por_sku[sku]
    ]
    return {"produtos": produtos, "skus_controlados": skus_controlados}


def visao_produtos_cadastro_contexto_loja(
    client_id: str,
    loja_referencia: Any = "",
    *,
    skus: Iterable[Any] | None = None,
    _normalizar_sku_consulta: Callable[[Any], str] | None = None,
) -> dict[str, Any]:
    """Resolve uma loja somente para leitura e falha fechado se for ambigua.

    O identificador exato tem precedencia. Nomes servem apenas para adaptar
    consumidores legados de leitura e precisam identificar uma unica loja.
    Sem loja especifica, devolve a projecao global segura, que esvazia campos
    divergentes entre lojas.
    """

    referencia = str(loja_referencia or "").strip()
    referencia_norm = _normalizar_loja_leitura(referencia)
    opcoes_filtro = ({"_normalizar_sku_consulta": _normalizar_sku_consulta}
                     if _normalizar_sku_consulta is not None else {})
    if referencia_norm in {"", "todas", "todas as lojas"} or referencia == "__todas":
        visao = visao_compatibilidade_produtos_lojas(client_id, skus=skus, **opcoes_filtro)
        return {
            **visao,
            "store_id": "",
            "loja_resolvida": True,
            "scope": "global_safe",
        }

    registros, lojas, fontes, fotos = _capturar_cadastro_para_leitura(client_id)
    with _cadastro_fotos_contexto_leitura(fotos):
        skus_controlados = {
            str(item.get("sku_normalizado") or "").strip()
            for item in registros
            if str(item.get("sku_normalizado") or "").strip()
        }
        identidade = resolver_loja_ativa_para_leitura(
            client_id,
            referencia,
            referencia if any(
                str(loja.get("store_id") or "").strip() == referencia
                for loja in lojas
            ) else "",
            lojas=lojas,
        )
        if not identidade.get("loja_resolvida"):
            return {
                "produtos": [],
                "skus_controlados": skus_controlados,
                "store_id": "",
                "loja_resolvida": False,
                "scope": "unresolved",
            }
        store_id = str(identidade.get("store_id") or "").strip()
        if not store_id:
            return {
                "produtos": [],
                "skus_controlados": skus_controlados,
                "store_id": "",
                "loja_resolvida": False,
                "scope": "unresolved",
            }
        produtos = _listar_produtos_loja_sync(
            client_id, store_id, skus=skus, _registros_persistidos=registros,
            _lojas_capturadas=lojas, _fontes_capturadas=fontes, **opcoes_filtro,
        )

    return {
        "produtos": [dict(item) for item in produtos],
        "skus_controlados": skus_controlados,
        "store_id": store_id,
        "loja_resolvida": True,
        "scope": "store",
    }


def mesclar_produtos_legados_com_contexto_loja(
    client_id: str,
    produtos_legados: Iterable[dict[str, Any]],
    loja_referencia: Any = "",
) -> dict[str, Any]:
    """Adapta consumidores legados sem permitir fallback entre lojas."""

    visao = visao_produtos_cadastro_contexto_loja(client_id, loja_referencia)
    if visao["scope"] == "store":
        return visao
    if visao["scope"] == "unresolved":
        return visao

    controlados = set(visao["skus_controlados"])
    preservados = [
        dict(item)
        for item in produtos_legados
        if _normalizar_sku_chave(item.get("sku") or "") not in controlados
    ]
    return {
        **visao,
        "produtos": preservados + list(visao["produtos"]),
    }


def mesclar_produtos_legados_com_lojas(
    client_id: str, produtos_legados: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    visao = visao_compatibilidade_produtos_lojas(client_id)
    controlados = set(visao["skus_controlados"])
    if not controlados:
        return [dict(item) for item in produtos_legados]
    preservados = [
        dict(item)
        for item in produtos_legados
        if _normalizar_sku_chave(item.get("sku") or "") not in controlados
    ]
    return preservados + list(visao["produtos"])


def obter_produto_controlado_para_compatibilidade(
    client_id: str, sku: str
) -> tuple[bool, dict[str, Any] | None]:
    sku_normalizado = _normalizar_sku_chave(sku)
    visao = visao_compatibilidade_produtos_lojas(client_id)
    controlado = sku_normalizado in set(visao["skus_controlados"])
    produto = next(
        (
            item
            for item in visao["produtos"]
            if _normalizar_sku_chave(item.get("sku") or "") == sku_normalizado
        ),
        None,
    )
    return controlado, produto


__all__ = [
    "bloquear_mutacao_legada_sem_sku_controlado",
    "exigir_mutacao_legada_sem_campos_loja",
    "exigir_mutacao_legada_sem_sku_controlado",
    "mesclar_produtos_legados_com_lojas",
    "mesclar_produtos_legados_com_contexto_loja",
    "obter_produto_controlado_para_compatibilidade",
    "resolver_loja_ativa_para_leitura",
    "skus_controlados_cadastro_lojas",
    "visao_produtos_cadastro_contexto_loja",
    "visao_compatibilidade_produtos_lojas",
]
