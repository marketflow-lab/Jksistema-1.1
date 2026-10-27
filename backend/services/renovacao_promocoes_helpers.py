"""Mercado Livre campaign helper functions for Renovacao."""

from __future__ import annotations

import datetime as dt
import math
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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


def _renovacao_tipo_campanha(campanha_id: str, tipo: str = "") -> str:
    if str(campanha_id or "").strip().upper().startswith("C-MLB"):
        return "SELLER_CAMPAIGN"
    return str(tipo or "SELLER_CAMPAIGN").strip().upper() or "SELLER_CAMPAIGN"


def _renovacao_contagem_esperada(campanha: dict) -> Optional[int]:
    def numero(valor):
        n = ctx._parse_float_flex(valor)
        return int(n) if n is not None and math.isfinite(float(n)) and n >= 0 else None

    ativos = numero(campanha.get("active_count"))
    programados = numero(campanha.get("programmed_count"))
    if programados is None:
        programados = numero(campanha.get("scheduled_count"))
    if ativos is not None and programados is not None:
        return ativos + programados
    if ativos is not None or programados is not None:
        return None
    return numero(campanha.get("total_count"))


def _renovacao_status_participacao(raw: dict) -> str:
    if not isinstance(raw, dict):
        return ""
    status = raw.get("status") or raw.get("state") or raw.get("status_item") or raw.get("statusItem")
    if isinstance(status, dict):
        status = status.get("id") or status.get("name") or status.get("status")
    return str(status or "").strip().lower()


def _renovacao_participante_campanha(
    raw: dict, campanha_id: str, item_id: str, contexto_campanha: bool = False,
) -> bool:
    if not isinstance(raw, dict):
        return False
    status = _renovacao_status_participacao(raw)
    if status not in {"started", "active", "approved", "pending", "programmed", "programada", "scheduled", "ready_to_start"}:
        return False
    tipo = str(raw.get("promotion_type") or raw.get("type") or "").strip().upper()
    if tipo and tipo != "SELLER_CAMPAIGN":
        return False
    referencias = []
    for chave in ("promotion_id", "campaign_id"):
        if raw.get(chave):
            referencias.append(str(raw[chave]))
    for chave in ("promotion", "campaign"):
        obj = raw.get(chave)
        if isinstance(obj, dict) and obj.get("id"):
            referencias.append(str(obj["id"]))
    identificador = str(raw.get("id") or "")
    if identificador.upper().startswith("C-MLB"):
        referencias.append(identificador)
    if any(ref != campanha_id for ref in referencias) or (not referencias and not contexto_campanha):
        return False
    for chave in ("item_id", "itemId"):
        if raw.get(chave) and str(raw[chave]) != item_id:
            return False
    obj = raw.get("item")
    item_explicito = obj.get("id") if isinstance(obj, dict) else obj
    if item_explicito and str(item_explicito) != item_id:
        return False
    return not identificador.upper().startswith("MLB") or identificador == item_id


def _renovacao_exigir_coleta_completa(itens: list[dict], esperado: int | None, campanha_id: str) -> None:
    encontrados = len({str(item.get("id") or "") for item in itens if item.get("id")})
    if esperado is not None and encontrados < esperado:
        raise HTTPException(
            status_code=409,
            detail=(f"Coleta incompleta da campanha {campanha_id}: encontrados {encontrados} de {esperado} "
                    f"anuncios ativos ou programados. Faltam {esperado - encontrados}. A copia nao foi iniciada."),
        )


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
        detalhe = ctx._ml_parse_error_detail(resp, "Falha na consulta")
        raise HTTPException(status_code=resp.status_code, detail=f"Erro ao listar campanhas do Mercado Livre: {detalhe}")

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
        tipo = _renovacao_tipo_campanha(campanha_id, tipo)
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
            programmed_count, cfg = ctx._ml_contar_itens_promocao_status(
                client_id, nome_loja, cfg, campanha_id, tipo, "pending"
            )
            if status_programado:
                if programmed_count is None:
                    for candidato in (total_count, active_count_api, eligible_count):
                        if candidato is not None:
                            programmed_count = candidato
                            break
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
    preco_promo, desconto = ctx._ml_extrair_preco_promocao_raw(raw, priorizar_percentual_total_api=True)
    preco_base = _renovacao_buscar_numero_em_obj(raw, {
        "original_price", "regular_price", "base_price", "standard_price", "price_before_discount",
    })
    if preco_promo is None and raw:
        preco_promo = _renovacao_buscar_numero_em_obj(raw, {
            "deal_price", "promotion_price", "campaign_price", "final_price",
        })

    # A porcentagem pertence a origem. O preco atual do anuncio pode ja incluir
    # outra promocao e nao serve como divisor para reconstruir esse desconto.
    if desconto is None and preco_base and preco_promo and preco_base > preco_promo:
        desconto = round((1 - (float(preco_promo) / float(preco_base))) * 100, 4)
    if desconto is None or not math.isfinite(float(desconto)) or not 0 < desconto < 100:
        ctx.logger.warning(f"[RENOVACAO ML] Sem desconto identificavel para {item_id}")
        return None, preco_base, preco_promo
    if preco_base is None and preco_promo and math.isfinite(float(preco_promo)) and preco_promo > 0:
        preco_base = round(float(preco_promo) / (1 - (float(desconto) / 100)), 2)
    if preco_promo is None:
        preco_promo = _renovacao_preco_com_desconto(preco_base, desconto)
    return round(float(desconto), 4), preco_base, preco_promo


def _renovacao_preco_com_desconto(preco_base, desconto) -> Optional[float]:
    try:
        if isinstance(preco_base, bool) or isinstance(desconto, bool):
            return None
        base = Decimal(str(preco_base))
        percentual = Decimal(str(desconto))
        if not base.is_finite() or not percentual.is_finite() or base <= 0 or not 0 < percentual < 100:
            return None
        preco = (base * (1 - percentual / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(preco) if preco > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _renovacao_preco_base_atual(
    client_id: str, loja: str, cfg: dict, item_id: str, detalhe: dict,
) -> tuple[Optional[float], dict]:
    detalhe = detalhe if isinstance(detalhe, dict) else {}
    if str(detalhe.get("id") or item_id) != item_id:
        return None, cfg
    original = ctx._parse_float_flex(detalhe.get("original_price"))
    if original is not None and math.isfinite(float(original)) and original > 0:
        return float(original), cfg

    # /items.price pode ser o preco promocional. Confirma o preco standard antes
    # de aplicar novamente a porcentagem, evitando desconto sobre desconto.
    resp, cfg = ctx._ml_api_request(
        client_id, loja, cfg, "GET",
        f"https://api.mercadolibre.com/items/{item_id}/prices", timeout=12,
    )
    body = _renovacao_json_response(resp)
    if resp.status_code != 200 or not isinstance(body, dict) or str(body.get("id") or "") != item_id:
        return None, cfg
    moeda = str(detalhe.get("currency_id") or ("BRL" if item_id.startswith("MLB") else ""))
    agora = dt.datetime.now(dt.timezone.utc)
    precos = set()
    for entry in body.get("prices") or []:
        if not isinstance(entry, dict) or str(entry.get("type") or "").lower() != "standard":
            continue
        if moeda and entry.get("currency_id") != moeda:
            continue
        conditions = entry.get("conditions") or {}
        if not isinstance(conditions, dict):
            continue
        quantidade = conditions.get("min_purchase_unit", 1)
        contextos = conditions.get("context_restrictions") or []
        if isinstance(quantidade, bool) or quantidade != 1 or not isinstance(contextos, list):
            continue
        if set(contextos) - {"channel_marketplace"}:
            continue
        if entry.get("net_amount") is not None or str(entry.get("amount_tax_inclusion_type") or "").lower() == "net":
            continue
        vigente = True
        for chave, inicio in (("start_time", True), ("end_time", False)):
            if conditions.get(chave):
                try:
                    limite = dt.datetime.fromisoformat(str(conditions[chave]).replace("Z", "+00:00"))
                    if limite.tzinfo is None or (agora < limite if inicio else agora >= limite):
                        vigente = False
                except (ValueError, TypeError):
                    vigente = False
        preco = ctx._parse_float_flex(entry.get("amount"))
        if vigente and preco is not None and math.isfinite(float(preco)) and preco > 0:
            precos.add(float(preco))
    return (precos.pop() if len(precos) == 1 else None), cfg


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
            executor.submit(
                ctx._ml_obter_promocoes_item, client_id, loja, dict(cfg), item_id,
                force_refresh=True,
            ): item_id
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
            raw_encontrado = ctx._ml_encontrar_promocao_raw_item(promocoes_item, campanha_id)
            if not _renovacao_participante_campanha(raw_encontrado, campanha_id, item_id):
                continue
            vistos.add(item_id)
            ids.append(item_id)
            raw_total[item_id] = raw_encontrado
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
    promotion_type = _renovacao_tipo_campanha(campanha_id, promotion_type)
    if expected_count is None:
        ativos, cfg = ctx._ml_contar_itens_promocao_status(
            client_id, loja, cfg, campanha_id, promotion_type, "started",
        )
        programados, cfg = ctx._ml_contar_itens_promocao_status(
            client_id, loja, cfg, campanha_id, promotion_type, "pending",
        )
        if ativos is None or programados is None:
            raise HTTPException(status_code=502, detail="Nao foi possivel confirmar a quantidade de participantes da campanha.")
        expected_count = int(ativos) + int(programados)
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
    sem_status = set()
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
            usar_fallback_pesado=False,
            max_items=5000,
            buscar_detalhes=False,
            status_item_preferencial=tentativa["status_item_preferencial"],
            status_promocao=tentativa["status_promocao"],
        )
        for item in itens or []:
            item_id = str((item or {}).get("id") or "").strip()
            raw = (raw_por_item or {}).get(item_id) or {}
            if item_id and _renovacao_participante_campanha(raw, campanha_id, item_id, contexto_campanha=True):
                raw_total[item_id] = raw
            elif item_id and not _renovacao_status_participacao(raw):
                sem_status.add(item_id)
        for item_id in raw_total:
            if item_id not in ids:
                ids.append(item_id)
    pendentes = sem_status - set(ids)
    if pendentes:
        with ThreadPoolExecutor(max_workers=min(6, len(pendentes))) as executor:
            futures = {
                executor.submit(ctx._ml_obter_promocoes_item, client_id, loja, dict(cfg), item_id, force_refresh=True): item_id
                for item_id in pendentes
            }
            for future in as_completed(futures):
                item_id = futures[future]
                try:
                    promocoes, _cfg_tmp = future.result()
                    raw = ctx._ml_encontrar_promocao_raw_item(promocoes, campanha_id)
                except Exception:
                    continue
                if _renovacao_participante_campanha(raw, campanha_id, item_id):
                    ids.append(item_id)
                    raw_total[item_id] = raw
    alvo = int(expected_count or 0)
    if alvo and len(ids) < alvo:
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
        _renovacao_exigir_coleta_completa([{"id": item_id} for item_id in ids], alvo, campanha_id)
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
