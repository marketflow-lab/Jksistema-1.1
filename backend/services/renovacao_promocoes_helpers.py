"""Mercado Livre campaign helper functions for Renovacao."""

from __future__ import annotations

import datetime as dt
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from fastapi import HTTPException

from backend.services import renovacao_context as ctx

def _renovacao_json_response(resp):
    try:
        return resp.json() or {}
    except Exception:
        return {}


def _renovacao_extrair_campanhas_payload(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("results") or payload.get("campaigns") or payload.get("promotions") or []
    return []


def _renovacao_ml_listar_campanhas_usuario(client_id: str, loja: str) -> tuple[list[dict], dict]:
    nome_loja = str(loja or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja do Mercado Livre.")

    cfg = ctx._obter_cfg_ml(client_id, nome_loja)
    user_id = str(cfg.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="ID do usuario do Mercado Livre nao encontrado para esta loja.")

    resp, cfg = ctx._ml_api_request(
        client_id,
        nome_loja,
        cfg,
        "GET",
        f"https://api.mercadolibre.com/seller-promotions/users/{user_id}",
        params={"app_version": "v2"},
        timeout=25,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=ctx._ml_parse_error_detail(resp, "Erro ao listar campanhas do Mercado Livre"))

    campanhas_raw = _renovacao_extrair_campanhas_payload(_renovacao_json_response(resp))
    campanhas = []
    for item in campanhas_raw:
        if not isinstance(item, dict):
            continue
        campanha_id = str(item.get("id") or item.get("promotion_id") or "").strip()
        tipo = str(item.get("type") or item.get("promotion_type") or "").strip()
        tipo_upper = tipo.upper()
        if not campanha_id:
            continue
        if tipo_upper != "SELLER_CAMPAIGN" and not campanha_id.upper().startswith("C-MLB"):
            continue
        status = str(item.get("status") or item.get("state") or "").strip()
        status_lower = status.lower()
        status_programado = status_lower in {"pending", "programmed", "scheduled", "ready_to_start"}
        active_count_api = ctx._ml_extrair_contagem_campanha(item, [
            "active_count", "active_items_count", "active_item_count", "started_items_count",
            "participating_items_count", "items_active_count",
        ])
        eligible_count = ctx._ml_extrair_contagem_campanha(item, [
            "eligible_count", "eligible_items_count", "eligible_item_count",
            "candidate_items_count", "candidate_count", "items_count", "item_count",
            "total_items", "products_count", "offers_count",
        ])
        total_count = ctx._ml_extrair_contagem_campanha(item, [
            "items_count", "item_count", "total_items", "products_count", "offers_count",
            "total_count", "total_items_count",
        ])
        programmed_count = ctx._ml_extrair_contagem_campanha(item, [
            "programmed_count", "programmed_items_count", "programmed_item_count",
            "scheduled_count", "scheduled_items_count", "scheduled_item_count",
            "pending_count", "pending_items_count", "pending_item_count",
        ])

        if not status_programado:
            active_count_api, cfg = ctx._ml_contar_itens_promocao_status(
                client_id, nome_loja, cfg, campanha_id, tipo or "SELLER_CAMPAIGN", "started"
            )

        active_count = 0 if status_programado else active_count_api
        if programmed_count is None:
            if status_programado:
                programmed_count, cfg = ctx._ml_contar_itens_promocao_status(
                    client_id, nome_loja, cfg, campanha_id, tipo or "SELLER_CAMPAIGN", "pending"
                )
                if programmed_count is None:
                    for candidato in (total_count, active_count_api, eligible_count):
                        if candidato is not None:
                            programmed_count = candidato
                            break
            else:
                programmed_count = 0
        campanhas.append({
            "id": campanha_id,
            "name": str(item.get("name") or item.get("title") or campanha_id).strip(),
            "title": str(item.get("title") or item.get("name") or campanha_id).strip(),
            "status": status,
            "type": tipo or "SELLER_CAMPAIGN",
            "promotion_type": tipo or "SELLER_CAMPAIGN",
            "sub_type": str(item.get("sub_type") or item.get("subtype") or "").strip(),
            "start_date": item.get("start_date") or item.get("date_start") or item.get("begin_date"),
            "finish_date": item.get("finish_date") or item.get("end_date") or item.get("date_end"),
            "active_count": active_count,
            "eligible_count": eligible_count,
            "programmed_count": programmed_count,
            "scheduled_count": programmed_count,
            "total_count": total_count,
            "raw": item,
        })

    prioridade_status = {"started": 0, "pending": 1, "programmed": 1, "active": 2, "finished": 9, "ended": 9}
    campanhas.sort(key=lambda c: (
        prioridade_status.get(str(c.get("status") or "").lower(), 5),
        str(c.get("start_date") or ""),
        str(c.get("name") or "").lower(),
    ))
    return campanhas, cfg


def _renovacao_parse_data_ml(valor):
    texto = str(valor or "").strip()
    if not texto:
        return None
    tentativas = [
        texto,
        texto.replace("Z", "+00:00"),
        texto.replace(".000+0000", "+00:00"),
    ]
    for item in tentativas:
        try:
            data = dt.datetime.fromisoformat(item)
            if data.tzinfo is not None:
                data = data.astimezone(dt.timezone.utc).replace(tzinfo=None)
            return data
        except Exception:
            pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(texto[:19] if "T" in texto else texto[:10], fmt)
        except Exception:
            pass
    return None


def _renovacao_dias_mes(ano: int, mes: int) -> int:
    if mes == 2:
        bissexto = (ano % 4 == 0 and ano % 100 != 0) or (ano % 400 == 0)
        return 29 if bissexto else 28
    return 30 if mes in {4, 6, 9, 11} else 31


def _renovacao_adicionar_meses(data: dt.datetime, meses: int = 1) -> dt.datetime:
    mes_base = data.month - 1 + meses
    ano = data.year + mes_base // 12
    mes = mes_base % 12 + 1
    dia = min(data.day, _renovacao_dias_mes(ano, mes))
    return data.replace(year=ano, month=mes, day=dia)


def _renovacao_datas_proximo_mes(campanha: dict) -> tuple[str, str]:
    inicio = _renovacao_parse_data_ml(campanha.get("start_date")) or (dt.datetime.now() + dt.timedelta(days=1))
    fim = _renovacao_parse_data_ml(campanha.get("finish_date"))
    duracao_dias = 14
    if fim and fim > inicio:
        duracao_dias = max(1, min(14, (fim.date() - inicio.date()).days or 1))

    novo_inicio = _renovacao_adicionar_meses(inicio, 1)
    agora = dt.datetime.now()
    while novo_inicio.date() < agora.date():
        novo_inicio = _renovacao_adicionar_meses(novo_inicio, 1)
    if novo_inicio.date() == agora.date():
        novo_inicio = agora + dt.timedelta(days=1)
    novo_inicio = novo_inicio.replace(hour=0, minute=0, second=0, microsecond=0)
    novo_fim = (novo_inicio + dt.timedelta(days=duracao_dias)).replace(hour=23, minute=59, second=59, microsecond=0)
    return novo_inicio.strftime("%Y-%m-%dT%H:%M:%S"), novo_fim.strftime("%Y-%m-%dT%H:%M:%S")


RENOVACAO_MESES_PT = {
    1: "Janeiro",
    2: "Fevereiro",
    3: "MarÃ§o",
    4: "Abril",
    5: "Maio",
    6: "Junho",
    7: "Julho",
    8: "Agosto",
    9: "Setembro",
    10: "Outubro",
    11: "Novembro",
    12: "Dezembro",
}


def _renovacao_nome_proximo_mes(campanha: dict) -> str:
    inicio_txt, _fim_txt = _renovacao_datas_proximo_mes(campanha or {})
    inicio = _renovacao_parse_data_ml(inicio_txt) or dt.datetime.now()
    mes_nome = RENOVACAO_MESES_PT.get(int(inicio.month), "")
    base = str((campanha or {}).get("name") or (campanha or {}).get("title") or "Promo").strip()
    base = re.sub(r"\s*-\s*(started|pending|programmed|active|finished|ended).*$", "", base, flags=re.I).strip()
    base = re.sub(r"\s*\([^)]*\)\s*$", "", base).strip()
    meses_regex = r"(janeiro|fevereiro|mar[Ã§c]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)"
    if re.search(meses_regex, base, flags=re.I):
        return re.sub(meses_regex, mes_nome, base, count=1, flags=re.I).strip()
    if re.search(r"\b(0?[1-9]|1[0-2])/\d{2,4}\b", base):
        return re.sub(r"\b(0?[1-9]|1[0-2])/\d{2,4}\b", mes_nome, base, count=1).strip()
    if not base or base.lower() == "promo":
        return f"Promo {mes_nome}".strip()
    return f"{base} {mes_nome}".strip()


def _renovacao_normalizar_nome(valor: str) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or "").strip().lower())
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", texto)


def _renovacao_encontrar_campanha_destino(campanhas: list[dict], nome: str, inicio_alvo: str) -> Optional[dict]:
    nome_norm = _renovacao_normalizar_nome(nome)
    inicio_dt = _renovacao_parse_data_ml(inicio_alvo)
    fallback_por_nome = None
    for campanha in campanhas or []:
        if not isinstance(campanha, dict):
            continue
        campanha_id = str(campanha.get("id") or "").strip()
        if not campanha_id:
            continue
        tipo = str(campanha.get("promotion_type") or campanha.get("type") or "").upper()
        if tipo and tipo != "SELLER_CAMPAIGN" and not campanha_id.upper().startswith("C-MLB"):
            continue
        nome_campanha = str(campanha.get("name") or campanha.get("title") or "").strip()
        if _renovacao_normalizar_nome(nome_campanha) != nome_norm:
            continue
        data_campanha = _renovacao_parse_data_ml(campanha.get("start_date"))
        if data_campanha and inicio_dt:
            if data_campanha.year == inicio_dt.year and data_campanha.month == inicio_dt.month:
                return campanha
            continue
        fallback_por_nome = fallback_por_nome or campanha
    return fallback_por_nome


def _renovacao_buscar_numero_em_obj(obj, chaves: set[str]):
    encontrados = []

    def visitar(valor):
        if isinstance(valor, dict):
            for chave, conteudo in valor.items():
                chave_norm = str(chave or "").strip().lower()
                if chave_norm in chaves:
                    numero = ctx._parse_float_flex(conteudo)
                    if numero is not None and numero > 0:
                        encontrados.append(float(numero))
                if isinstance(conteudo, (dict, list)):
                    visitar(conteudo)
        elif isinstance(valor, list):
            for item in valor:
                visitar(item)

    visitar(obj)
    return encontrados[0] if encontrados else None


def _renovacao_desconto_item(item_id: str, raw: dict, detalhe: dict) -> tuple[Optional[float], Optional[float], Optional[float]]:
    raw = raw if isinstance(raw, dict) else {}
    detalhe = detalhe if isinstance(detalhe, dict) else {}
    preco_promo, desconto = ctx._ml_extrair_preco_promocao_raw(raw)
    preco_base = _renovacao_buscar_numero_em_obj(raw, {
        "original_price", "regular_price", "base_price", "standard_price", "price_before_discount",
    })
    if preco_base is None:
        preco_base = ctx._parse_float_flex(detalhe.get("original_price"))
    preco_item = ctx._parse_float_flex(detalhe.get("price"))
    if preco_base is None and preco_item is not None:
        preco_base = float(preco_item)
    if preco_promo is None and raw:
        preco_promo = _renovacao_buscar_numero_em_obj(raw, {
            "deal_price", "promotion_price", "campaign_price", "final_price",
        })

    if desconto is None and preco_base and preco_promo and preco_base > preco_promo:
        desconto = round((1 - (float(preco_promo) / float(preco_base))) * 100, 4)
    if preco_base is None and preco_promo and desconto is not None and 0 < desconto < 100:
        preco_base = round(float(preco_promo) / (1 - (float(desconto) / 100)), 2)
    if preco_promo is None and preco_base and desconto is not None and 0 < desconto < 100:
        preco_promo = round(float(preco_base) * (1 - (float(desconto) / 100)), 2)

    if desconto is None or desconto <= 0:
        ctx.logger.warning(f"[RENOVACAO ML] Sem desconto identificavel para {item_id}")
        return None, preco_base, preco_promo
    return round(float(desconto), 4), preco_base, preco_promo


def _renovacao_emitir_progresso(
    progress_callback: Optional[Callable[[dict], None]],
    etapa: str,
    percentual: int | float,
    mensagem: str,
    **extras,
) -> None:
    if not progress_callback:
        return
    try:
        pct = max(0, min(100, int(round(float(percentual or 0)))))
    except Exception:
        pct = 0
    payload = {
        "etapa": str(etapa or "Sincronizacao"),
        "percentual": pct,
        "progress": pct,
        "mensagem": str(mensagem or ""),
    }
    if extras:
        payload.update(extras)
    try:
        progress_callback(payload)
    except Exception:
        ctx.logger.debug("[RENOVACAO ML] Falha ao emitir progresso", exc_info=True)


def _renovacao_sync_job_update(job_id: str, **kwargs) -> None:
    if not job_id:
        return
    with ctx.RENOVACAO_SYNC_JOBS_LOCK:
        atual = dict(ctx.RENOVACAO_SYNC_JOBS.get(job_id) or {})
        atual.update(kwargs)
        atual["updated_at"] = time.time()
        ctx.RENOVACAO_SYNC_JOBS[job_id] = atual


def _renovacao_sync_job_get(job_id: str) -> dict:
    with ctx.RENOVACAO_SYNC_JOBS_LOCK:
        return dict(ctx.RENOVACAO_SYNC_JOBS.get(job_id) or {})


def _renovacao_sync_jobs_limpar_antigos(max_age_seconds: int = 24 * 60 * 60) -> None:
    agora = time.time()
    with ctx.RENOVACAO_SYNC_JOBS_LOCK:
        antigos = [
            job_id for job_id, job in ctx.RENOVACAO_SYNC_JOBS.items()
            if agora - float((job or {}).get("updated_at") or agora) > max_age_seconds
        ]
        for job_id in antigos:
            ctx.RENOVACAO_SYNC_JOBS.pop(job_id, None)


def _renovacao_complementar_itens_campanha_por_varredura(
    client_id: str,
    loja: str,
    cfg: dict,
    campanha_id: str,
    ids_existentes: list[str],
    raw_por_item: dict[str, dict],
    max_items: int = 5000,
    progress_callback: Optional[Callable[[dict], None]] = None,
    progress_start: int = 30,
    progress_end: int = 55,
) -> tuple[list[str], dict[str, dict], dict]:
    user_id = str((cfg or {}).get("user_id") or "").strip()
    if not user_id:
        return ids_existentes, raw_por_item, cfg

    ids = list(dict.fromkeys([str(item_id or "").strip() for item_id in ids_existentes if str(item_id or "").strip()]))
    vistos = set(ids)
    raw_total = dict(raw_por_item or {})
    limite = max(1, min(int(max_items or 5000), 20000))
    _renovacao_emitir_progresso(
        progress_callback,
        "Varredura",
        progress_start,
        "Listando anuncios ativos da loja para completar a campanha de origem...",
        campanha_id=campanha_id,
        ja_encontrados=len(ids),
    )
    todos_ids, cfg = ctx._ml_listar_ids_anuncios_ativos(client_id, loja, cfg, user_id, limite=limite)
    campanha_norm = str(campanha_id or "").strip().lower()
    _renovacao_emitir_progresso(
        progress_callback,
        "Varredura",
        progress_start,
        f"Endpoint direto incompleto. Verificando {len(todos_ids)} anuncio(s) ativos da loja...",
        campanha_id=campanha_id,
        encontrados=len(ids),
        total=len(todos_ids),
    )
    ctx.logger.info(
        "[RENOVACAO ML] Varredura da campanha %s em %s anuncio(s) ativos para completar sincronizacao.",
        campanha_id,
        len(todos_ids),
    )

    candidatos = [str(item_id or "").strip() for item_id in todos_ids if str(item_id or "").strip() and str(item_id or "").strip() not in vistos]
    max_workers = min(6, max(2, len(candidatos))) if candidatos else 0
    if not max_workers:
        return ids, raw_total, cfg

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(ctx._ml_obter_promocoes_item, client_id, loja, dict(cfg), item_id): item_id
            for item_id in candidatos
        }
        processados = 0
        ultimo_emitido = 0
        intervalo_emit = max(1, min(50, max(1, len(candidatos) // 20)))
        for future in as_completed(future_map):
            processados += 1
            item_id = future_map[future]
            try:
                promocoes_item, _cfg_tmp = future.result()
            except Exception:
                promocoes_item = []
            if (
                processados == 1
                or processados == len(candidatos)
                or processados - ultimo_emitido >= intervalo_emit
            ):
                ultimo_emitido = processados
                pct = progress_start
                if candidatos:
                    pct = progress_start + ((progress_end - progress_start) * processados / len(candidatos))
                _renovacao_emitir_progresso(
                    progress_callback,
                    "Varredura",
                    pct,
                    f"Verificando anuncios ativos: {processados}/{len(candidatos)}. Encontrados na campanha: {len(ids)}.",
                    campanha_id=campanha_id,
                    processados=processados,
                    total=len(candidatos),
                    encontrados=len(ids),
                )
            if campanha_norm not in ctx._ml_extrair_ids_promocoes_item(promocoes_item):
                continue
            vistos.add(item_id)
            ids.append(item_id)
            raw_encontrado = ctx._ml_encontrar_promocao_raw_item(promocoes_item, campanha_id)
            if isinstance(raw_encontrado, dict) and raw_encontrado:
                raw_total.setdefault(item_id, raw_encontrado)
            if len(ids) >= limite:
                ctx.logger.warning("[RENOVACAO ML] Varredura da campanha %s limitada a %s anuncios", campanha_id, limite)
                break

    ctx.logger.info("[RENOVACAO ML] Campanha %s: %s item(ns) apos complemento por varredura.", campanha_id, len(ids))
    _renovacao_emitir_progresso(
        progress_callback,
        "Varredura",
        progress_end,
        f"Varredura concluida. {len(ids)} MLB(s) encontrados na campanha de origem.",
        campanha_id=campanha_id,
        encontrados=len(ids),
    )
    return ids, raw_total, cfg


def _renovacao_listar_itens_campanha(
    client_id: str,
    loja: str,
    cfg: dict,
    campanha_id: str,
    promotion_type: str,
    expected_count: int | None = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
    progress_start: int = 15,
    progress_end: int = 55,
) -> tuple[list[dict], dict[str, dict], dict]:
    _renovacao_emitir_progresso(
        progress_callback,
        "Listagem",
        progress_start,
        f"Carregando MLBs da campanha {campanha_id}...",
        campanha_id=campanha_id,
    )
    tentativas = [
        {"status_item_preferencial": "active", "status_promocao": ""},
        {"status_item_preferencial": "", "status_promocao": "started"},
        {"status_item_preferencial": "", "status_promocao": "pending"},
        {"status_item_preferencial": "", "status_promocao": ""},
    ]
    ids = []
    raw_total = {}
    for idx_tentativa, tentativa in enumerate(tentativas):
        pct_tentativa = progress_start + (
            max(1, progress_end - progress_start) * (idx_tentativa + 1) / (len(tentativas) + 1)
        )
        _renovacao_emitir_progresso(
            progress_callback,
            "Listagem",
            pct_tentativa,
            f"Consultando itens da campanha no Mercado Livre ({idx_tentativa + 1}/{len(tentativas)})...",
            campanha_id=campanha_id,
            tentativa=idx_tentativa + 1,
        )
        itens, raw_por_item, cfg = ctx._ml_listar_itens_promocao_com_raw(
            client_id,
            loja,
            cfg,
            campanha_id,
            promotion_type=promotion_type or "SELLER_CAMPAIGN",
            usar_fallback_pesado=(idx_tentativa == len(tentativas) - 1),
            max_items=5000,
            buscar_detalhes=False,
            status_item_preferencial=tentativa["status_item_preferencial"],
            status_promocao=tentativa["status_promocao"],
        )
        for item in itens or []:
            item_id = str((item or {}).get("id") or "").strip()
            if item_id and item_id not in ids:
                ids.append(item_id)
        for item_id, raw in (raw_por_item or {}).items():
            raw_total.setdefault(str(item_id).strip(), raw)
    alvo = int(expected_count or 0)
    if alvo and len(ids) < min(alvo, 5000):
        ctx.logger.warning(
            "[RENOVACAO ML] Campanha %s retornou %s/%s itens pelo endpoint direto; complementando por varredura.",
            campanha_id,
            len(ids),
            alvo,
        )
        _renovacao_emitir_progresso(
            progress_callback,
            "Listagem",
            min(progress_end - 5, progress_start + 20),
            f"Endpoint direto retornou {len(ids)}/{alvo}. Complementando por varredura nos anuncios ativos...",
            campanha_id=campanha_id,
            encontrados=len(ids),
            esperado=alvo,
        )
        ids, raw_total, cfg = _renovacao_complementar_itens_campanha_por_varredura(
            client_id,
            loja,
            cfg,
            campanha_id,
            ids,
            raw_total,
            max_items=max(5000, alvo),
            progress_callback=progress_callback,
            progress_start=min(progress_end - 20, progress_start + 22),
            progress_end=progress_end,
        )
    else:
        _renovacao_emitir_progresso(
            progress_callback,
            "Listagem",
            progress_end,
            f"Campanha carregada com {len(ids)} MLB(s).",
            campanha_id=campanha_id,
            encontrados=len(ids),
            esperado=alvo,
        )
    return [{"id": item_id} for item_id in ids], raw_total, cfg
