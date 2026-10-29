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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from backend.services import renovacao_context as ctx

try:
    RENOVACAO_TIMEZONE = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:
    RENOVACAO_TIMEZONE = dt.timezone(dt.timedelta(hours=-3))


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
    if campanha.get("contagens_verificadas") is False:
        return None
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


def _renovacao_selecionar_participacao_exata(
    promocoes: list, campanha_id: str, item_id: str, seller_id=None,
) -> dict:
    if not isinstance(promocoes, list) or any(not isinstance(raw, dict) for raw in promocoes):
        return {"situacao": "inconclusivo", "erro": "Resposta de participacoes invalida"}
    participantes = []
    for raw in promocoes:
        refs = [str(raw[key]) for key in ("promotion_id", "campaign_id", "promotionId", "campaignId") if raw.get(key)]
        for key in ("promotion", "campaign"):
            nested = raw.get(key)
            if isinstance(nested, dict) and nested.get("id"):
                refs.append(str(nested["id"]))
        identificador = str(raw.get("id") or "")
        if identificador.upper().startswith("C-MLB"):
            refs.append(identificador)
        if campanha_id not in refs:
            tipos = {str(raw[key]).strip().upper() for key in ("type", "promotion_type") if raw.get(key)}
            if not refs and (not tipos or "SELLER_CAMPAIGN" in tipos):
                return {"situacao": "inconclusivo", "erro": "Registro sem identificador da campanha"}
            continue
        if any(ref != campanha_id for ref in refs):
            return {"situacao": "inconclusivo", "erro": "Identificadores de campanha conflitantes"}
        if identificador.upper().startswith("MLB") and identificador != item_id:
            return {"situacao": "inconclusivo", "erro": "Participacao pertence a outro anuncio"}
        for key in ("item_id", "itemId", "item_id_to"):
            if raw.get(key) and str(raw[key]) != item_id:
                return {"situacao": "inconclusivo", "erro": "Participacao pertence a outro anuncio"}
        nested = raw.get("item")
        explicit_item = nested.get("id") if isinstance(nested, dict) else nested
        if explicit_item and str(explicit_item) != item_id:
            return {"situacao": "inconclusivo", "erro": "Participacao pertence a outro anuncio"}
        for key in ("seller_id", "user_id"):
            if seller_id is not None and raw.get(key) and str(raw[key]) != str(seller_id):
                return {"situacao": "inconclusivo", "erro": "Participacao pertence a outra conta"}
        tipos = {str(raw[key]).strip().upper() for key in ("promotion_type", "type") if raw.get(key)}
        if tipos - {"SELLER_CAMPAIGN"}:
            return {"situacao": "inconclusivo", "erro": "Tipo de campanha conflitante"}
        # status_item indica o estado do anuncio e nao comprova participacao.
        status = _renovacao_status_participacao({"status": raw.get("status") or raw.get("state")})
        if status in {"candidate", "eligible", "finished", "ended", "inactive", "cancelled", "canceled"}:
            continue
        if status not in {"started", "active", "approved", "pending", "programmed", "programada", "scheduled", "ready_to_start"}:
            return {"situacao": "inconclusivo", "erro": "Estado de participacao nao confirmado"}
        participantes.append(dict(raw, status=status))
    if not participantes:
        return {"situacao": "ausente"}
    assinaturas = set()
    for raw in participantes:
        percentual, base, preco = _renovacao_desconto_item(item_id, raw, {})
        assinaturas.add((percentual, base, preco))
    if len(assinaturas) != 1:
        return {"situacao": "inconclusivo", "erro": "Participacoes com termos financeiros conflitantes"}
    return {"situacao": "participante", "raw": participantes[0]}


def _renovacao_consultar_participacoes(client_id, loja, cfg, item_id, campanhas) -> tuple[dict, dict]:
    if not re.fullmatch(r"ML[A-Z]\d+", str(item_id)):
        return {campaign: {"situacao": "inconclusivo", "erro": "Identificador do anuncio invalido"} for campaign in campanhas}, cfg
    try:
        promocoes, cfg = ctx._ml_obter_promocoes_item(
            client_id, loja, cfg, item_id, strict=True, force_refresh=True,
        )
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        erro = f"Consulta de participacoes recebeu HTTP {status}" if status else "Falha na consulta de participacoes"
        return {campaign: {"situacao": "inconclusivo", "erro": erro} for campaign in campanhas}, cfg
    resultados = {}
    for campaign in campanhas:
        try:
            resultados[campaign] = _renovacao_selecionar_participacao_exata(
                promocoes, campaign, item_id, (cfg or {}).get("user_id"))
        except (ValueError, TypeError, OverflowError):
            resultados[campaign] = {"situacao": "inconclusivo", "erro": "Termos da participacao invalidos"}
    return resultados, cfg


def _renovacao_exigir_coleta_completa(itens: list[dict], esperado: int | None, campanha_id: str) -> None:
    encontrados = len({str(item.get("id") or "") for item in itens if item.get("id")})
    if esperado is not None and encontrados != esperado:
        if encontrados > esperado:
            raise HTTPException(
                status_code=409,
                detail=(f"Contagem divergente da campanha {campanha_id}: confirmados {encontrados}, esperados {esperado}. "
                        "A copia nao foi iniciada. Confira os participantes e atualize as contagens."),
            )
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
        programados_api, cfg = ctx._ml_contar_itens_promocao_status(
            client_id, nome_loja, cfg, campanha_id, tipo, "pending"
        )
        if programados_api is not None:
            programmed_count = programados_api
        elif programmed_count is None and status_programado:
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
            "contagens_verificadas": programados_api is not None and (status_programado or active_count_api is not None),
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
    """Return the campaign's local time; ML reads may use UTC timestamps."""
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
                data = data.astimezone(RENOVACAO_TIMEZONE).replace(tzinfo=None)
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
    agora = dt.datetime.now(RENOVACAO_TIMEZONE).replace(tzinfo=None)
    inicio = _renovacao_parse_data_ml(campanha.get("start_date")) or agora
    mes_origem = inicio.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    mes_atual = agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    novo_inicio = max(
        _renovacao_adicionar_meses(mes_origem, 1),
        _renovacao_adicionar_meses(mes_atual, 1),
    )
    novo_fim = _renovacao_adicionar_meses(novo_inicio, 1) - dt.timedelta(seconds=1)
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


def _renovacao_confirmar_lote(client_id, loja, cfg, item_ids, campanhas, estatisticas=None,
                              progress_callback=None, progress_start=20, progress_end=48):
    stats = estatisticas if estatisticas is not None else {}
    consultas = stats.setdefault("consultas", {})
    inconclusivos = stats.setdefault("inconclusivos", {})
    ids = list(dict.fromkeys(str(item) for item in item_ids if item))
    pendentes = [item for item in ids if item not in consultas]
    if pendentes:
        with ThreadPoolExecutor(max_workers=min(6, len(pendentes))) as executor:
            futures = {executor.submit(_renovacao_consultar_participacoes, client_id, loja,
                                      dict(cfg), item, campanhas): item for item in pendentes}
            for numero, future in enumerate(as_completed(futures), start=1):
                item = futures[future]
                resultado, _cfg_tmp = future.result()
                consultas[item] = resultado
                for campaign, registro in resultado.items():
                    if registro.get("situacao") == "inconclusivo":
                        inconclusivos.setdefault(item, {})[campaign] = registro.get("erro") or "Participacao inconclusiva"
                pct = progress_start + (progress_end-progress_start)*numero/max(1,len(pendentes))
                _renovacao_emitir_progresso(progress_callback, "Conferencia", pct,
                    f"Conferindo participacoes exatas: {numero}/{len(pendentes)} anuncios.",
                    processados=numero, total=len(pendentes))
    estados = {}
    for resultado in consultas.values():
        for campaign, registro in resultado.items():
            if registro.get("situacao") == "participante":
                status = _renovacao_status_participacao(registro.get("raw") or {})
                by_status = estados.setdefault(campaign, {})
                by_status[status] = by_status.get(status, 0) + 1
    stats["estados"] = estados
    return consultas, cfg


def _renovacao_complementar_itens_campanha_por_varredura(
    client_id: str, loja: str, cfg: dict, campanha_id: str,
    ids_existentes: list[str], raw_por_item: dict[str, dict], max_items: int = 5000,
    progress_callback: Optional[Callable[[dict], None]] = None,
    progress_start: int = 30, progress_end: int = 55, estatisticas: dict | None = None,
) -> tuple[list[str], dict[str, dict], dict]:
    stats = estatisticas if estatisticas is not None else {}
    user_id = str((cfg or {}).get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(502, "Conta da loja nao confirmada para consultar participantes.")
    limite = max(1, min(int(max_items or 5000), 20000))
    todos_ids, cfg = ctx._ml_listar_ids_anuncios_ativos(client_id, loja, cfg, user_id, limite=limite)
    candidatos = list(dict.fromkeys(list(ids_existentes) + [str(item) for item in todos_ids if item]))
    campanhas = stats.get("campanhas_consulta") or [campanha_id]
    consultas, cfg = _renovacao_confirmar_lote(client_id, loja, cfg, candidatos, campanhas, stats,
        progress_callback, progress_start, progress_end)
    raw_total = {}
    for item in candidatos:
        registro = (consultas.get(item) or {}).get(campanha_id) or {}
        if registro.get("situacao") == "participante":
            raw_total[item] = registro["raw"]
    return list(raw_total), raw_total, cfg


def _renovacao_listar_itens_campanha(
    client_id: str, loja: str, cfg: dict, campanha_id: str, promotion_type: str,
    expected_count: int | None = None, progress_callback: Optional[Callable[[dict], None]] = None,
    progress_start: int = 15, progress_end: int = 55,
    validar_quantidade: bool = True, estatisticas: dict | None = None,
) -> tuple[list[dict], dict[str, dict], dict]:
    stats = estatisticas if estatisticas is not None else {}
    promotion_type = _renovacao_tipo_campanha(campanha_id, promotion_type)
    if expected_count is None:
        ativos, cfg = ctx._ml_contar_itens_promocao_status(client_id, loja, cfg, campanha_id, promotion_type, "started")
        programados, cfg = ctx._ml_contar_itens_promocao_status(client_id, loja, cfg, campanha_id, promotion_type, "pending")
        if ativos is None or programados is None:
            if validar_quantidade:
                raise HTTPException(502, "Nao foi possivel confirmar a quantidade de participantes da campanha.")
        else:
            expected_count = int(ativos) + int(programados)
    candidatos = []
    for status_item, status in (("active", ""), ("", "started"), ("", "pending"), ("", "")):
        itens, _raw, cfg = ctx._ml_listar_itens_promocao_com_raw(
            client_id, loja, cfg, campanha_id, promotion_type=promotion_type,
            usar_fallback_pesado=False, max_items=5000, buscar_detalhes=False,
            status_item_preferencial=status_item, status_promocao=status)
        candidatos.extend(str(item.get("id")) for item in itens or [] if isinstance(item,dict) and item.get("id"))
    candidatos = list(dict.fromkeys(candidatos))
    campanhas = stats.get("campanhas_consulta") or [campanha_id]
    consultas, cfg = _renovacao_confirmar_lote(client_id, loja, cfg, candidatos, campanhas, stats,
        progress_callback, progress_start, max(progress_start, progress_end-20))
    raw_total = {item: consultas[item][campanha_id]["raw"] for item in candidatos
                 if consultas[item].get(campanha_id, {}).get("situacao") == "participante"}
    ids = list(raw_total)
    if not validar_quantidade or (expected_count is not None and len(ids) < expected_count):
        ids, raw_total, cfg = _renovacao_complementar_itens_campanha_por_varredura(
            client_id, loja, cfg, campanha_id, ids, raw_total,
            max_items=max(5000, expected_count or 0), progress_callback=progress_callback,
            progress_start=max(progress_start, progress_end-20), progress_end=progress_end,
            estatisticas=stats)
    itens = [{"id": item} for item in ids]
    inconclusivos = [item for item, erros in stats.get("inconclusivos", {}).items() if campanha_id in erros]
    if validar_quantidade and inconclusivos:
        raise HTTPException(409, f"Participacao inconclusiva em {len(inconclusivos)} anuncio(s) da campanha {campanha_id}. A copia nao foi iniciada.")
    if validar_quantidade:
        _renovacao_exigir_coleta_completa(itens, expected_count, campanha_id)
    _renovacao_emitir_progresso(progress_callback, "Conferencia", progress_end,
        f"Campanha {campanha_id}: {len(ids)} participantes confirmados por anuncio.", encontrados=len(ids), esperado=expected_count)
    return itens, raw_total, cfg
