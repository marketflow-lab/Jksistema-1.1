"""Background jobs for the Favoritos ranking workflow."""

from __future__ import annotations

import asyncio
import copy
import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request

from backend.schemas import (
    FavoritosEnriquecerDatasRequest,
    FavoritosJobColetaTermoRequest,
    FavoritosJobStartRequest,
    FavoritosPrimeiraPaginaRequest,
    FavoritosRankingIARequest,
)
from backend.services import favoritos_endpoints


FAVORITOS_JOB_PAUSE_SLEEP_S = 0.2
FAVORITOS_JOB_STEP_SLEEP_S = 0.18
FAVORITOS_JOB_MAX_RESULTS = 60
FAVORITOS_JOB_ENRICH_MAX = 30
FAVORITOS_JOB_MODE_AVANTPRO_BROWSER = "avantpro_browser"
FAVORITOS_JOB_MEMORY_TTL_S = 60 * 60
FAVORITOS_JOB_STORAGE_TTL_S = 30 * 24 * 60 * 60
FAVORITOS_JOB_STORAGE_MAX_PER_CLIENT = 100
FAVORITOS_JOB_MAX_SELECTED = 1000
FAVORITOS_JOB_RUNNING_STATUSES = {"running", "paused", "canceling"}
FAVORITOS_JOB_ACTIVE: dict[str, dict] = {}
FAVORITOS_JOB_BY_OWNER: dict[str, str] = {}
FAVORITOS_JOB_LOCK = threading.RLock()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _model_dict(model: Any) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model or {})


def _owner_key(client_id: str, username: str) -> str:
    return f"{str(client_id or '').strip()}:{str(username or 'default').strip().lower()}"


def _job_owner_matches(job: dict, client_id: str, username: str) -> bool:
    if not isinstance(job, dict):
        return False
    return (
        str(job.get("client_id") or "").strip() == str(client_id or "").strip()
        and str(job.get("username") or "").strip().lower() == str(username or "").strip().lower()
    )


def _job_timestamp(job: dict) -> float:
    for field in ("finished_at", "updated_at", "started_at"):
        value = str((job or {}).get(field) or "").strip()
        if not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
    return 0.0


def _job_prune_memory() -> None:
    now = time.time()
    with FAVORITOS_JOB_LOCK:
        for job_id, job in list(FAVORITOS_JOB_ACTIVE.items()):
            if str((job or {}).get("status") or "") in FAVORITOS_JOB_RUNNING_STATUSES:
                continue
            timestamp = _job_timestamp(job)
            if timestamp and now - timestamp < FAVORITOS_JOB_MEMORY_TTL_S:
                continue
            FAVORITOS_JOB_ACTIVE.pop(job_id, None)
            owner = str((job or {}).get("owner_key") or "")
            if owner and FAVORITOS_JOB_BY_OWNER.get(owner) == job_id:
                FAVORITOS_JOB_BY_OWNER.pop(owner, None)


def _safe_client_dir(client_id: str) -> Path:
    client = _clean_text(client_id or "default", 80).replace("/", "_").replace("\\", "_") or "default"
    path = Path("info") / client / "favoritos_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_find_storage_path(job: dict | str, client_id: str | None = None) -> Path:
    if isinstance(job, dict):
        job_id = str(job.get("id") or "").strip()
        client = str(job.get("client_id") or client_id or "default").strip()
    else:
        job_id = str(job or "").strip()
        client = str(client_id or "default").strip()
    favoritos_jobs_dir = _safe_client_dir(client)
    return favoritos_jobs_dir / f"{job_id}.json"


def _job_persist(job: dict) -> None:
    if not job or not job.get("id"):
        return
    path = _job_find_storage_path(job)
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(job, fh, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def _job_prune_storage(client_id: str) -> None:
    directory = _safe_client_dir(client_id)
    now = time.time()
    retained = 0
    for path in sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            with path.open("r", encoding="utf-8") as fh:
                job = json.load(fh)
            if str((job or {}).get("status") or "") in FAVORITOS_JOB_RUNNING_STATUSES:
                continue
            age = max(0.0, now - path.stat().st_mtime)
            retained += 1
            if age <= FAVORITOS_JOB_STORAGE_TTL_S and retained <= FAVORITOS_JOB_STORAGE_MAX_PER_CLIENT:
                continue
            if path.parent.resolve() != directory.resolve():
                continue
            path.unlink(missing_ok=True)
        except Exception:
            continue


def _normalize_orphaned_loaded_job(job: dict) -> bool:
    if str(job.get("status") or "").lower() != "canceling" or not job.get("cancel_requested"):
        return False
    job["status"] = "canceled"
    job["etapa"] = "Cancelado"
    job["mensagem"] = "Favoritos cancelado pelo usuario."
    job["finished_at"] = job.get("finished_at") or _now_iso()
    job["updated_at"] = _now_iso()
    return True


def _job_load(job_id: str, client_id: str, username: str) -> dict | None:
    path = _job_find_storage_path(job_id, client_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            job = json.load(fh)
        if (
            not isinstance(job, dict)
            or str(job.get("id") or "") != str(job_id)
            or not _job_owner_matches(job, client_id, username)
        ):
            return None
        normalized = _normalize_orphaned_loaded_job(job)
        with FAVORITOS_JOB_LOCK:
            FAVORITOS_JOB_ACTIVE[job_id] = job
            owner = str(job.get("owner_key") or _owner_key(client_id, username))
            FAVORITOS_JOB_BY_OWNER[owner] = job_id
        if normalized:
            _job_persist(job)
        return copy.deepcopy(job)
    except Exception:
        return None


def _job_load_latest(client_id: str, username: str) -> dict | None:
    favoritos_jobs_dir = _safe_client_dir(client_id)
    files = sorted(favoritos_jobs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for path in files:
        try:
            with path.open("r", encoding="utf-8") as fh:
                job = json.load(fh)
            if not job.get("id") or not _job_owner_matches(job, client_id, username):
                continue
            normalized = _normalize_orphaned_loaded_job(job)
            job_id = str(job.get("id") or "")
            with FAVORITOS_JOB_LOCK:
                FAVORITOS_JOB_ACTIVE[job_id] = job
                FAVORITOS_JOB_BY_OWNER[_owner_key(client_id, username)] = job_id
            if normalized:
                _job_persist(job)
            return copy.deepcopy(job)
        except Exception:
            continue
    return None


def _job_resolve_active(job_id: str, client_id: str, username: str) -> dict | None:
    _job_prune_memory()
    with FAVORITOS_JOB_LOCK:
        job = FAVORITOS_JOB_ACTIVE.get(job_id or "")
    if job and _job_owner_matches(job, client_id, username):
        return copy.deepcopy(job)
    if not job_id:
        return None
    return _job_load(job_id, client_id, username)


def _job_get(job_id: str, client_id: str, username: str) -> dict:
    job = _job_resolve_active(job_id, client_id, username)
    if not job:
        raise HTTPException(status_code=404, detail="Job de favoritos nao encontrado.")
    return job


def _clean_text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _normalize_item_id(value: Any) -> str:
    text = str(value or "").strip().upper().replace("-", "")
    if "MLB" in text:
        idx = text.find("MLB")
        text = text[idx:]
    return text


def _anuncio_key(anuncio: dict) -> str:
    item_id = _normalize_item_id(anuncio.get("id") or anuncio.get("mlb"))
    if item_id:
        return f"id:{item_id}"
    url = str(anuncio.get("url") or anuncio.get("permalink") or anuncio.get("link") or "").split("#")[0].strip().lower()
    return f"url:{url}" if url else ""


def _normalizar_texto_ml(value: Any) -> str:
    import unicodedata

    text = unicodedata.normalize("NFD", str(value or "").strip().lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.split())


def _titulo_parece_filtro_ml(titulo: Any) -> bool:
    normalizado = _normalizar_texto_ml(titulo)
    return not normalizado or normalizado in {
        "resultados",
        "pecas de motos e quadriciclos",
        "lubrificantes e fluidos",
        "pecas de linha pesada",
        "pecas de carros e caminhonetes",
        "acessorios de motos e quadriciclos",
        "categorias",
        "condicao",
        "tipo de envio",
        "custo de envio",
        "tempo de entrega",
    }


def _condicao_anuncio(anuncio: dict) -> str:
    attrs = anuncio.get("attributes") if isinstance(anuncio, dict) else []
    if isinstance(attrs, list):
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            attr_id = str(attr.get("id") or "").upper()
            if attr_id in {"ITEM_CONDITION", "CONDITION"}:
                valor = attr.get("value_name") or attr.get("value_id") or attr.get("name") or attr.get("id")
                texto = _normalizar_texto_ml(valor)
                if texto:
                    return texto
    for campo in ("condicao", "condition", "item_condition", "itemCondition", "itemConditionName", "estado", "item_state"):
        texto = _normalizar_texto_ml(anuncio.get(campo))
        if texto:
            return texto
    return ""


def _anuncio_produto_novo(anuncio: dict) -> bool:
    condicao = _condicao_anuncio(anuncio)
    return not (condicao in {"used", "usado", "usada", "2230581"} or "usad" in condicao)


def _normalizar_termos(termos: Any, quantidade: int) -> list[dict]:
    vistos: set[str] = set()
    saida: list[dict] = []
    for idx, item in enumerate(termos if isinstance(termos, list) else [], start=1):
        if isinstance(item, dict):
            termo = _clean_text(item.get("termo") or item.get("term") or item.get("valor") or item.get("value"), 160)
            campo = item.get("campo") or item.get("field") or idx
        else:
            termo = _clean_text(item, 160)
            campo = idx
        chave = termo.lower()
        if not termo or chave in vistos:
            continue
        vistos.add(chave)
        try:
            campo_int = int(campo)
        except Exception:
            campo_int = idx
        saida.append({"campo": campo_int, "termo": termo})
        if len(saida) >= max(1, min(3, int(quantidade or 1))):
            break
    return saida


def _normalizar_item_job(item: Any, quantidade: int) -> dict | None:
    data = _model_dict(item)
    sku = _clean_text(data.get("sku"), 80)
    if not sku:
        return None
    return {
        "sku": sku,
        "loja": _clean_text(data.get("loja"), 160),
        "titulo": _clean_text(data.get("titulo"), 240),
        "descricao": _clean_text(data.get("descricao"), 2000),
        "termos": _normalizar_termos(data.get("termos"), quantidade),
        "cadastro": data.get("cadastro") if isinstance(data.get("cadastro"), dict) else None,
    }


def _job_public(job: dict) -> dict:
    with FAVORITOS_JOB_LOCK:
        data = copy.deepcopy(job)
    started_at = data.get("started_at")
    finished_at = data.get("finished_at")
    return {
        "success": True,
        "job_id": data.get("id"),
        "status": data.get("status"),
        "percentual": int(data.get("percentual") or 0),
        "etapa": data.get("etapa") or "",
        "mensagem": data.get("mensagem") or "",
        "sku_atual": data.get("sku_atual") or "",
        "indice": int(data.get("indice") or 0),
        "total": int(data.get("total") or 0),
        "resultados_parciais": data.get("resultados") or [],
        "logs": (data.get("logs") or [])[-80:],
        "erro": data.get("erro") or "",
        "started_at": started_at,
        "updated_at": data.get("updated_at") or started_at,
        "finished_at": finished_at,
        "paused": data.get("status") == "paused",
        "cancel_requested": bool(data.get("cancel_requested")),
    }


def _job_update(job_id: str, **updates) -> dict:
    with FAVORITOS_JOB_LOCK:
        job = FAVORITOS_JOB_ACTIVE.get(job_id)
        if not job:
            raise RuntimeError("Job de favoritos nao encontrado.")
        job.update(updates)
        job["updated_at"] = _now_iso()
        _job_persist(job)
        return copy.deepcopy(job)


def _job_log(job_id: str, message: str) -> None:
    msg = _clean_text(message, 500)
    if not msg:
        return
    with FAVORITOS_JOB_LOCK:
        job = FAVORITOS_JOB_ACTIVE.get(job_id)
        if not job:
            return
        logs = list(job.get("logs") or [])
        logs.append(f"{_now_iso()} - {msg}")
        job["logs"] = logs[-120:]
        job["mensagem"] = msg
        job["updated_at"] = _now_iso()
        _job_persist(job)


def _job_check_cancel(job_id: str) -> None:
    with FAVORITOS_JOB_LOCK:
        job = FAVORITOS_JOB_ACTIVE.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job de favoritos nao encontrado.")
        if job.get("cancel_requested") or job.get("status") == "canceling":
            raise InterruptedError("Favoritos cancelado pelo usuario.")


def _job_wait_control(job_id: str) -> None:
    while True:
        _job_check_cancel(job_id)
        with FAVORITOS_JOB_LOCK:
            job = FAVORITOS_JOB_ACTIVE.get(job_id) or {}
            paused = job.get("status") == "paused"
        if not paused:
            return
        time.sleep(FAVORITOS_JOB_PAUSE_SLEEP_S)


def _job_short_pause(job_id: str, seconds: float = FAVORITOS_JOB_STEP_SLEEP_S) -> None:
    deadline = time.time() + max(0.0, float(seconds or 0))
    while time.time() < deadline:
        _job_wait_control(job_id)
        time.sleep(min(FAVORITOS_JOB_PAUSE_SLEEP_S, max(0.0, deadline - time.time())))


def _run_async(coro):
    return asyncio.run(coro)


def _buscar_primeira_pagina(client_id: str, termo: str) -> list[dict]:
    req = FavoritosPrimeiraPaginaRequest(
        termo=termo,
        max_anuncios=FAVORITOS_JOB_MAX_RESULTS,
        usar_automatico=True,
    )
    data = _run_async(favoritos_endpoints.favoritos_ml_primeira_pagina(req, client_id))
    return [item for item in (data or {}).get("anuncios") or [] if isinstance(item, dict)]


def _normalizar_anuncio(anuncio: dict, sku: str, pesquisa: dict) -> dict:
    item_id = _normalize_item_id(anuncio.get("id") or anuncio.get("mlb"))
    url = str(anuncio.get("url") or anuncio.get("permalink") or anuncio.get("link") or "").strip()
    titulo = _clean_text(anuncio.get("titulo") or anuncio.get("title"), 500)
    imagem = str(
        anuncio.get("imagem")
        or anuncio.get("thumbnail")
        or anuncio.get("image")
        or anuncio.get("picture")
        or ""
    ).strip()
    return {
        **anuncio,
        "id": item_id or anuncio.get("id"),
        "sku_favorito": sku,
        "titulo": titulo,
        "url": url,
        "imagem": imagem,
        "thumbnail": imagem or anuncio.get("thumbnail") or "",
        "preco": anuncio.get("preco", anuncio.get("price", "")),
        "price": anuncio.get("price", anuncio.get("preco", "")),
        "preco_promocional": anuncio.get("preco_promocional", anuncio.get("promotional_price", "")),
        "vendedor": _clean_text(anuncio.get("vendedor") or anuncio.get("seller"), 160),
        "vendas": anuncio.get("vendas"),
        "vendasFonte": anuncio.get("vendasFonte") or anuncio.get("vendas_fonte") or "mercadolivre_backend",
        "vendas_fonte": anuncio.get("vendas_fonte") or anuncio.get("vendasFonte") or "mercadolivre_backend",
        "data_criacao": anuncio.get("data_criacao") or anuncio.get("date_created") or "",
        "posicao": anuncio.get("posicao") or 9999,
        "pesquisas_origem": [pesquisa.get("termo")],
        "campos_origem": [f"Pesquisa {pesquisa.get('campo') or ''}".strip()],
        "origem_dados": anuncio.get("origem_dados") or "mercadolivre_backend",
    }


def _deduplicar_anuncios(anuncios: list[dict]) -> list[dict]:
    mapa: dict[str, dict] = {}
    for anuncio in anuncios:
        chave = _anuncio_key(anuncio)
        if not chave:
            continue
        atual = mapa.get(chave)
        if not atual:
            mapa[chave] = dict(anuncio)
            continue
        for campo in ("titulo", "url", "imagem", "thumbnail", "vendedor", "data_criacao", "tipo_anuncio", "condicao"):
            if not atual.get(campo) and anuncio.get(campo):
                atual[campo] = anuncio.get(campo)
        try:
            atual["posicao"] = min(int(atual.get("posicao") or 9999), int(anuncio.get("posicao") or 9999))
        except Exception:
            pass
        for campo in ("pesquisas_origem", "campos_origem"):
            valores = list(atual.get(campo) or [])
            for valor in anuncio.get(campo) or []:
                if valor and valor not in valores:
                    valores.append(valor)
            atual[campo] = valores
    return list(mapa.values())


def _merge_enriquecimento(client_id: str, anuncios: list[dict]) -> None:
    if not anuncios:
        return
    pendentes = [
        item
        for item in anuncios
        if item
        and (
            not item.get("data_criacao")
            or not item.get("vendedor")
            or item.get("vendas") in (None, "")
            or not (item.get("tipo_anuncio") or item.get("listing_type_id") or item.get("listing_type_name"))
            or not _condicao_anuncio(item)
        )
    ][:FAVORITOS_JOB_ENRICH_MAX]
    if not pendentes:
        return
    req = FavoritosEnriquecerDatasRequest(
        max_anuncios=min(FAVORITOS_JOB_ENRICH_MAX, len(pendentes)),
        anuncios=[{"id": item.get("id") or "", "url": item.get("url") or "", "imagem": item.get("imagem") or item.get("thumbnail") or ""} for item in pendentes],
    )
    data = _run_async(favoritos_endpoints.favoritos_ml_enriquecer_datas(req, client_id))
    resultados = [item for item in (data or {}).get("resultados") or [] if isinstance(item, dict)]
    mapa = {}
    for anuncio in anuncios:
        for chave in (_anuncio_key(anuncio), f"id:{_normalize_item_id(anuncio.get('id'))}"):
            if chave:
                mapa[chave] = anuncio
    for info in resultados:
        alvo = mapa.get(_anuncio_key(info)) or mapa.get(f"id:{_normalize_item_id(info.get('id'))}")
        if not alvo:
            continue
        for campo in (
            "data_criacao",
            "fonte",
            "fonte_data_criacao",
            "data_criacao_confianca",
            "vendedor",
            "fonte_vendedor",
            "vendas",
            "fonte_vendas",
            "visitas",
            "fonte_visitas",
            "listing_type_id",
            "listing_type_name",
            "tipo_anuncio",
            "parcelamento_sem_juros",
            "shipping",
            "logistic_type",
            "shipping_mode",
            "is_full",
            "condicao",
            "condition",
            "item_condition",
        ):
            if info.get(campo) not in (None, ""):
                alvo[campo] = info.get(campo)
        if info.get("fonte_vendas"):
            alvo["vendasFonte"] = info.get("fonte_vendas")
            alvo["vendas_fonte"] = info.get("fonte_vendas")


def _parse_number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace(".", "").replace(",", ".") if isinstance(value, str) and "," in value else value)
    except Exception:
        return None


def _rankear_backend(anuncios: list[dict]) -> list[dict]:
    def key(item_index):
        index, item = item_index
        vendas = _parse_number(item.get("vendas"))
        posicao = _parse_number(item.get("posicao")) or 9999
        return (-(vendas if vendas is not None else -1), posicao, index)

    return [item for _idx, item in sorted(enumerate(anuncios), key=key)[:FAVORITOS_JOB_MAX_RESULTS]]


def _filtrar_ia(client_id: str, info: dict, anuncios: list[dict], usar_ia: bool, max_confirmados: int) -> dict:
    if not usar_ia or not anuncios:
        return {"anuncios": anuncios[:FAVORITOS_JOB_MAX_RESULTS], "removidos": [], "removidosTotal": 0, "usouIa": False}
    pesquisas = [str(item.get("termo") or "").strip() for item in info.get("termos") or [] if str(item.get("termo") or "").strip()]
    req = FavoritosRankingIARequest(
        sku=info.get("sku") or "",
        titulo=info.get("titulo") or "",
        descricao=info.get("descricao") or "",
        pesquisas=pesquisas,
        meus_anuncios=[],
        anuncios=anuncios[:180],
        max_anuncios=min(180, len(anuncios)),
        max_confirmados=max(1, min(20, int(max_confirmados or 8))),
        usar_imagem=True,
    )
    data = favoritos_endpoints.favoritos_filtrar_ranking_ia(req, client_id)
    manter = {_normalize_item_id(item) for item in (data or {}).get("manter_ids") or [] if _normalize_item_id(item)}
    remover = {_normalize_item_id(item) for item in (data or {}).get("remover_ids") or [] if _normalize_item_id(item)}
    motivos = {
        _normalize_item_id(item.get("id") or item.get("mlb")): _clean_text(item.get("motivo") or item.get("reason") or "Removido pela IA", 240)
        for item in (data or {}).get("removidos") or []
        if isinstance(item, dict) and _normalize_item_id(item.get("id") or item.get("mlb"))
    }
    confirmados: list[dict] = []
    removidos: list[dict] = []
    for anuncio in anuncios:
        item_id = _normalize_item_id(anuncio.get("id"))
        if item_id and (item_id in remover or item_id in motivos):
            removidos.append({**anuncio, "motivo": motivos.get(item_id) or "Removido pela IA"})
            continue
        if not manter or (item_id and item_id in manter):
            if len(confirmados) < max(1, min(20, int(max_confirmados or 8))):
                confirmados.append(anuncio)
    return {
        "anuncios": confirmados,
        "removidos": removidos,
        "removidosTotal": len(removidos),
        "usouIa": True,
        "confirmados": len(confirmados),
        "maxConfirmados": max_confirmados,
    }


def _browser_queue(selecionados: list[dict]) -> list[dict]:
    fila: list[dict] = []
    for sku_index, info in enumerate(selecionados, start=1):
        for termo_index, pesquisa in enumerate(info.get("termos") or [], start=1):
            fila.append(
                {
                    "sku": info.get("sku") or "",
                    "loja": info.get("loja") or "",
                    "titulo": info.get("titulo") or "",
                    "descricao": info.get("descricao") or "",
                    "campo": pesquisa.get("campo") or termo_index,
                    "termo": pesquisa.get("termo") or "",
                    "sku_index": sku_index,
                    "termo_index": termo_index,
                    "key": f"{info.get('sku') or ''}:{pesquisa.get('campo') or termo_index}:{pesquisa.get('termo') or ''}",
                }
            )
    return fila


def _coleta_key(req: dict) -> str:
    return f"{req.get('sku') or ''}:{req.get('campo') or ''}:{req.get('termo') or ''}"


def _anuncio_tem_dados_avant_confiaveis(anuncio: dict) -> bool:
    fonte_vendas = str(anuncio.get("vendasFonte") or anuncio.get("vendas_fonte") or "").lower()
    fonte_vendedor = str(anuncio.get("vendedorFonte") or anuncio.get("vendedor_fonte") or "").lower()
    origem = str(anuncio.get("origem_dados") or "").lower()
    return bool(
        "avant" in fonte_vendas
        or "avant" in fonte_vendedor
        or "avant" in origem
        or anuncio.get("data_criacao")
        or anuncio.get("vendedor")
    )


def _build_resultados_browser_job(job: dict) -> list[dict]:
    coletas = job.get("coletas") or {}
    resultados: list[dict] = []
    for info in job.get("selecionados") or []:
        coletados: list[dict] = []
        for pesquisa in info.get("termos") or []:
            key = _coleta_key({"sku": info.get("sku"), "campo": pesquisa.get("campo"), "termo": pesquisa.get("termo")})
            payload = coletas.get(key) or {}
            anuncios = [item for item in payload.get("anuncios") or [] if isinstance(item, dict)]
            coletados.extend(_normalizar_anuncio(anuncio, info.get("sku") or "", pesquisa) for anuncio in anuncios)
        unicos = [
            anuncio
            for anuncio in _deduplicar_anuncios(coletados)
            if anuncio and not _titulo_parece_filtro_ml(anuncio.get("titulo")) and _anuncio_produto_novo(anuncio)
        ]
        total_com_dados_avant = sum(1 for anuncio in unicos if _anuncio_tem_dados_avant_confiaveis(anuncio))
        ranking_base = _rankear_backend(unicos)
        resultados.append(
            {
                **info,
                "opcoes_promocao": job.get("opcoes_promocao") or {},
                "anuncios": ranking_base,
                "total_com_dados_avant": total_com_dados_avant,
                "removidos_ia": [],
                "removidos_ia_total": 0,
                "usou_ia": False,
                "data_ranking_iso": _now_iso(),
            }
        )
    return resultados


def _processar_job(job_id: str) -> None:
    try:
        with FAVORITOS_JOB_LOCK:
            job = copy.deepcopy(FAVORITOS_JOB_ACTIVE.get(job_id) or {})
        client_id = job.get("client_id") or ""
        selecionados = list(job.get("selecionados") or [])
        total = len(selecionados)
        usar_ia = bool(job.get("usar_ia"))
        max_confirmados = int(job.get("max_confirmados_ia") or 8)
        opcoes_promocao = job.get("opcoes_promocao") or {}
        resultados: list[dict] = []
        _job_update(job_id, status="running", etapa="Preparando", percentual=1, total=total)
        _job_log(job_id, f"Iniciando favoritos em background: {total} SKU(s).")

        for idx, info in enumerate(selecionados, start=1):
            _job_wait_control(job_id)
            sku = info.get("sku") or ""
            base_pct = int(((idx - 1) / max(total, 1)) * 100)
            _job_update(job_id, etapa="SKU", sku_atual=sku, indice=idx, percentual=base_pct, status="running")
            _job_log(job_id, f"SKU {sku} ({idx}/{total}): preparando pesquisas.")
            _job_short_pause(job_id)

            if not info.get("termos"):
                grupo = {
                    **info,
                    "opcoes_promocao": opcoes_promocao,
                    "anuncios": [],
                    "erro": f"Nenhum campo Pesquisa 1 a {job.get('quantidade_pesquisas') or 1} preenchido para este SKU.",
                }
                resultados.append(grupo)
                _job_update(job_id, resultados=resultados, percentual=int((idx / max(total, 1)) * 100))
                continue

            coletados: list[dict] = []
            for pesquisa in info.get("termos") or []:
                _job_wait_control(job_id)
                termo = pesquisa.get("termo") or ""
                pct = base_pct + int((8 / max(total, 1)) * 1)
                _job_update(job_id, etapa="Pesquisa", sku_atual=sku, indice=idx, percentual=min(98, pct))
                _job_log(job_id, f"SKU {sku}: pesquisando {termo}.")
                try:
                    anuncios = _buscar_primeira_pagina(client_id, termo)
                except Exception as exc:
                    _job_log(job_id, f"SKU {sku}: pesquisa {termo} falhou. {exc}")
                    anuncios = []
                coletados.extend(_normalizar_anuncio(anuncio, sku, pesquisa) for anuncio in anuncios)
                _job_short_pause(job_id)

            unicos = [
                anuncio
                for anuncio in _deduplicar_anuncios(coletados)
                if anuncio and not _titulo_parece_filtro_ml(anuncio.get("titulo")) and _anuncio_produto_novo(anuncio)
            ]
            _job_wait_control(job_id)
            _job_update(job_id, etapa="Enriquecimento", sku_atual=sku, indice=idx)
            _job_log(job_id, f"SKU {sku}: enriquecendo {len(unicos)} anuncio(s).")
            try:
                _merge_enriquecimento(client_id, unicos)
            except Exception as exc:
                _job_log(job_id, f"SKU {sku}: enriquecimento falhou, usando dados coletados. {exc}")
            _job_short_pause(job_id)
            unicos = [anuncio for anuncio in unicos if _anuncio_produto_novo(anuncio)]

            ranking_base = _rankear_backend(unicos)
            _job_wait_control(job_id)
            _job_update(job_id, etapa="IA" if usar_ia else "Ranking", sku_atual=sku, indice=idx)
            if usar_ia:
                _job_log(job_id, f"SKU {sku}: IA comparando ranking.")
            try:
                filtro_ia = _filtrar_ia(client_id, info, ranking_base, usar_ia, max_confirmados)
            except Exception as exc:
                _job_log(job_id, f"SKU {sku}: IA falhou, mantendo ranking normal. {exc}")
                filtro_ia = {
                    "anuncios": ranking_base[:FAVORITOS_JOB_MAX_RESULTS],
                    "removidos": [],
                    "removidosTotal": 0,
                    "usouIa": False,
                }
            _job_short_pause(job_id)
            grupo = {
                **info,
                "opcoes_promocao": opcoes_promocao,
                "anuncios": filtro_ia["anuncios"] if filtro_ia.get("usouIa") else ranking_base[:FAVORITOS_JOB_MAX_RESULTS],
                "removidos_ia": filtro_ia.get("removidos") or [],
                "removidos_ia_total": filtro_ia.get("removidosTotal") or 0,
                "usou_ia": bool(filtro_ia.get("usouIa")),
                "ia_confirmados": filtro_ia.get("confirmados") or 0,
                "ia_max_confirmados": filtro_ia.get("maxConfirmados") or (max_confirmados if usar_ia else 0),
                "data_ranking_iso": _now_iso(),
            }
            resultados.append(grupo)
            _job_update(
                job_id,
                resultados=resultados,
                etapa="SKU concluido",
                percentual=min(99, int((idx / max(total, 1)) * 100)),
            )
            _job_log(job_id, f"SKU {sku}: ranking concluido com {len(grupo.get('anuncios') or [])} anuncio(s).")
            _job_short_pause(job_id)

        _job_update(
            job_id,
            status="done",
            etapa="Finalizado",
            sku_atual="",
            indice=total,
            percentual=100,
            resultados=resultados,
            finished_at=_now_iso(),
        )
        _job_log(job_id, f"Favoritos concluido: {len(resultados)} SKU(s).")
    except InterruptedError as exc:
        _job_update(job_id, status="canceled", etapa="Cancelado", erro=str(exc), finished_at=_now_iso())
        _job_log(job_id, "Favoritos cancelado pelo usuario.")
    except Exception as exc:
        _job_update(job_id, status="error", etapa="Erro", erro=str(exc), finished_at=_now_iso())
        _job_log(job_id, f"Erro ao fazer favoritos: {exc}")
    finally:
        with FAVORITOS_JOB_LOCK:
            job = FAVORITOS_JOB_ACTIVE.get(job_id) or {}
            owner = job.get("owner_key")
            if owner and FAVORITOS_JOB_BY_OWNER.get(owner) == job_id:
                FAVORITOS_JOB_BY_OWNER.pop(owner, None)


def favoritos_jobs_start(
    req: FavoritosJobStartRequest,
    request: Request,
    client_id: str = Depends(favoritos_endpoints.get_tenant_id),
):
    username = favoritos_endpoints._extrair_username_do_request(request)
    if len(req.selecionados or []) > FAVORITOS_JOB_MAX_SELECTED:
        raise HTTPException(
            status_code=422,
            detail=f"Selecione no maximo {FAVORITOS_JOB_MAX_SELECTED} SKUs por execucao.",
        )
    _job_prune_memory()
    _job_prune_storage(client_id)
    quantidade = max(1, min(3, int(req.quantidade_pesquisas or 1)))
    selecionados = [_normalizar_item_job(item, quantidade) for item in (req.selecionados or [])]
    selecionados = [item for item in selecionados if item]
    if not selecionados:
        raise HTTPException(status_code=400, detail="Selecione ao menos um SKU para fazer favoritos.")

    owner = _owner_key(client_id, username)
    modo_coleta = str(req.modo_coleta or "").strip()
    with FAVORITOS_JOB_LOCK:
        active_id = FAVORITOS_JOB_BY_OWNER.get(owner)
        active_job = FAVORITOS_JOB_ACTIVE.get(active_id or "")
        if active_job and active_job.get("status") in {"running", "paused", "canceling"}:
            return {**_job_public(active_job), "started": False, "already_running": True}

        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "client_id": client_id,
            "username": username,
            "owner_key": owner,
            "status": "running",
            "percentual": 0,
            "etapa": "Preparando",
            "mensagem": "Iniciando favoritos em background.",
            "sku_atual": "",
            "indice": 0,
            "total": len(selecionados),
            "resultados": [],
            "logs": [],
            "erro": "",
            "loja": str(req.loja or "").strip(),
            "quantidade_pesquisas": quantidade,
            "usar_ia": bool(req.usar_ia),
            "max_confirmados_ia": int(req.max_confirmados_ia or 8),
            "opcoes_promocao": req.opcoes_promocao or {},
            "modo_coleta": modo_coleta,
            "selecionados": selecionados,
            "fila_coletas": _browser_queue(selecionados) if modo_coleta == FAVORITOS_JOB_MODE_AVANTPRO_BROWSER else [],
            "coletas": {},
            "cancel_requested": False,
            "started_at": _now_iso(),
            "updated_at": _now_iso(),
            "finished_at": "",
        }
        FAVORITOS_JOB_ACTIVE[job_id] = job
        FAVORITOS_JOB_BY_OWNER[owner] = job_id
        _job_persist(job)

    if modo_coleta == FAVORITOS_JOB_MODE_AVANTPRO_BROWSER:
        _job_log(job_id, "Job criado para coleta visual pelo navegador interno.")
        return {**_job_public(job), "started": True, "browser_collection": True}

    thread = threading.Thread(target=_processar_job, args=(job_id,), daemon=True, name=f"favoritos-job-{job_id[:8]}")
    thread.start()
    return {**_job_public(job), "started": True}


def favoritos_jobs_latest(
    request: Request,
    client_id: str = Depends(favoritos_endpoints.get_tenant_id),
):
    username = favoritos_endpoints._extrair_username_do_request(request)
    _job_prune_memory()
    _job_prune_storage(client_id)
    job = _job_load_latest(client_id, username)
    if not job:
        raise HTTPException(status_code=404, detail="Nenhum job de favoritos encontrado.")
    return _job_public(job)


def favoritos_jobs_status(
    job_id: str,
    request: Request,
    client_id: str = Depends(favoritos_endpoints.get_tenant_id),
):
    username = favoritos_endpoints._extrair_username_do_request(request)
    job = _job_get(job_id, client_id, username)
    return _job_public(job)


def favoritos_jobs_pause(
    job_id: str,
    request: Request,
    client_id: str = Depends(favoritos_endpoints.get_tenant_id),
):
    username = favoritos_endpoints._extrair_username_do_request(request)
    with FAVORITOS_JOB_LOCK:
        job = _job_get(job_id, client_id, username)
        if job.get("status") == "running":
            job["status"] = "paused"
            job["mensagem"] = "Favoritos pausado. Clique em retomar para continuar."
            job["updated_at"] = _now_iso()
            FAVORITOS_JOB_ACTIVE[job_id] = job
            _job_persist(job)
    return _job_public(job)


def favoritos_jobs_resume(
    job_id: str,
    request: Request,
    client_id: str = Depends(favoritos_endpoints.get_tenant_id),
):
    username = favoritos_endpoints._extrair_username_do_request(request)
    with FAVORITOS_JOB_LOCK:
        job = _job_get(job_id, client_id, username)
        if job.get("status") == "paused":
            job["status"] = "running"
            job["mensagem"] = "Favoritos retomado."
            job["updated_at"] = _now_iso()
            FAVORITOS_JOB_ACTIVE[job_id] = job
            _job_persist(job)
    return _job_public(job)


def favoritos_jobs_cancel(
    job_id: str,
    request: Request,
    client_id: str = Depends(favoritos_endpoints.get_tenant_id),
):
    username = favoritos_endpoints._extrair_username_do_request(request)
    with FAVORITOS_JOB_LOCK:
        job = _job_get(job_id, client_id, username)
        if job.get("status") in {"done", "error", "canceled"}:
            return _job_public(job)
        job["cancel_requested"] = True
        if job.get("modo_coleta") == FAVORITOS_JOB_MODE_AVANTPRO_BROWSER:
            job["status"] = "canceled"
            job["etapa"] = "Cancelado"
            job["finished_at"] = _now_iso()
            job["mensagem"] = "Favoritos cancelado pelo usuario."
        else:
            job["status"] = "canceling"
            job["mensagem"] = "Cancelamento solicitado."
        job["updated_at"] = _now_iso()
        FAVORITOS_JOB_ACTIVE[job_id] = job
        _job_persist(job)
    return _job_public(job)


def favoritos_jobs_proxima_coleta(
    job_id: str,
    request: Request,
    client_id: str = Depends(favoritos_endpoints.get_tenant_id),
):
    username = favoritos_endpoints._extrair_username_do_request(request)
    with FAVORITOS_JOB_LOCK:
        job = _job_resolve_active(job_id, client_id, username)
        if not job:
            raise HTTPException(status_code=404, detail="Job de favoritos nao encontrado.")
        if job.get("modo_coleta") != FAVORITOS_JOB_MODE_AVANTPRO_BROWSER:
            raise HTTPException(status_code=400, detail="Job nao usa coleta visual AvantPro.")
        if job.get("cancel_requested") or job.get("status") in {"canceling", "canceled"}:
            if job.get("status") != "canceled":
                job.update(
                    {
                        "status": "canceled",
                        "etapa": "Cancelado",
                        "finished_at": job.get("finished_at") or _now_iso(),
                        "updated_at": _now_iso(),
                        "mensagem": "Favoritos cancelado pelo usuario.",
                    }
                )
                FAVORITOS_JOB_ACTIVE[job_id] = job
                _job_persist(job)
            return {"success": True, "pending": False, **_job_public(job)}
        coletas = job.get("coletas") or {}
        fila = job.get("fila_coletas") or []
        for index, tarefa in enumerate(fila, start=1):
            key = tarefa.get("key") or _coleta_key(tarefa)
            if key in coletas:
                continue
            job.update(
                {
                    "status": "running",
                    "etapa": "Coleta visual",
                    "sku_atual": tarefa.get("sku") or "",
                    "indice": index,
                    "percentual": min(95, int(((index - 1) / max(len(fila), 1)) * 100)),
                    "mensagem": f"Coletar AvantPro: {tarefa.get('termo') or ''}",
                    "updated_at": _now_iso(),
                }
            )
            FAVORITOS_JOB_ACTIVE[job_id] = job
            _job_persist(job)
            return {"success": True, "pending": True, "job_id": job_id, "coleta": tarefa}
        resultados = _build_resultados_browser_job(job)
        job.update(
            {
                "status": "done",
                "etapa": "Finalizado",
                "percentual": 100,
                "resultados": resultados,
                "finished_at": _now_iso(),
                "updated_at": _now_iso(),
                "mensagem": "Favoritos concluido pela coleta visual.",
            }
        )
        FAVORITOS_JOB_ACTIVE[job_id] = job
        _job_persist(job)
        return {"success": True, "pending": False, **_job_public(job)}


def favoritos_jobs_coleta_termo(
    job_id: str,
    req: FavoritosJobColetaTermoRequest,
    request: Request,
    client_id: str = Depends(favoritos_endpoints.get_tenant_id),
):
    username = favoritos_endpoints._extrair_username_do_request(request)
    with FAVORITOS_JOB_LOCK:
        job = _job_resolve_active(job_id, client_id, username)
        if not job:
            raise HTTPException(status_code=404, detail="Job de favoritos nao encontrado.")
        if job.get("cancel_requested") or job.get("status") in {"canceling", "canceled"}:
            raise HTTPException(status_code=409, detail="Job de favoritos ja foi cancelado.")
        payload = _model_dict(req)
        key = _coleta_key(payload)
        coletas = dict(job.get("coletas") or {})
        coletas[key] = payload
        job["coletas"] = coletas
        job["mensagem"] = f"Coleta recebida: {payload.get('termo') or ''}"
        job["updated_at"] = _now_iso()
        fila = job.get("fila_coletas") or []
        if fila and all((item.get("key") or _coleta_key(item)) in coletas for item in fila):
            resultados = _build_resultados_browser_job(job)
            job.update(
                {
                    "status": "done",
                    "etapa": "Finalizado",
                    "percentual": 100,
                    "resultados": resultados,
                    "finished_at": _now_iso(),
                    "mensagem": "Favoritos concluido pela coleta visual.",
                }
            )
        FAVORITOS_JOB_ACTIVE[job_id] = job
        _job_persist(job)
        return _job_public(job)


__all__ = [
    "favoritos_jobs_start",
    "favoritos_jobs_latest",
    "favoritos_jobs_status",
    "favoritos_jobs_proxima_coleta",
    "favoritos_jobs_coleta_termo",
    "favoritos_jobs_pause",
    "favoritos_jobs_resume",
    "favoritos_jobs_cancel",
]
