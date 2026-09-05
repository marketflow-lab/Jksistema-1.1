"""Mercado Livre campaign operations for Renovacao."""

from __future__ import annotations
from backend.services.central_accounts_client import with_request_context

import datetime as dt
import json
import re
import threading
import uuid
from typing import Callable, Optional

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from backend.schemas.renovacao import RenovacaoCampanhaSincronizarRequest
from backend.services import renovacao_context as ctx
from backend.services.renovacao_agendamento_store import (
    _renovacao_agendamento_key,
    _renovacao_agendamento_sanitizar,
    _renovacao_agendamentos_carregar,
    _renovacao_remover_agendamento_campanha,
)
from backend.services.renovacao_promocoes_helpers import (
    _renovacao_datas_proximo_mes,
    _renovacao_desconto_item,
    _renovacao_emitir_progresso,
    _renovacao_encontrar_campanha_destino,
    _renovacao_json_response,
    _renovacao_listar_itens_campanha,
    _renovacao_ml_listar_campanhas_usuario,
    _renovacao_nome_proximo_mes,
    _renovacao_normalizar_nome,
    _renovacao_parse_data_ml,
    _renovacao_sync_job_update,
    _renovacao_sync_jobs_limpar_antigos,
)

def _renovacao_criar_campanha_ml(
    client_id: str,
    loja: str,
    cfg: dict,
    nome: str,
    campanha_fonte: dict,
    datas_alvo: tuple[str, str] | None = None,
) -> tuple[str, dict, dict]:
    inicio, fim = datas_alvo or _renovacao_datas_proximo_mes(campanha_fonte or {})
    payload = {
        "promotion_type": "SELLER_CAMPAIGN",
        "sub_type": "FLEXIBLE_PERCENTAGE",
        "name": nome,
        "start_date": inicio,
        "finish_date": fim,
    }
    resp, cfg = ctx._ml_api_request(
        client_id,
        loja,
        cfg,
        "POST",
        "https://api.mercadolibre.com/seller-promotions/promotions",
        params={"app_version": "v2"},
        json=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=ctx._ml_parse_error_detail(resp, "Erro ao criar campanha no Mercado Livre"))
    data = _renovacao_json_response(resp)
    nova_id = str(data.get("id") or data.get("promotion_id") or data.get("campaign_id") or "").strip()
    if not nova_id:
        raise HTTPException(status_code=500, detail="Mercado Livre criou a campanha, mas nao retornou o ID.")
    return nova_id, payload, cfg


def _renovacao_criar_campanha_manual_ml(
    client_id: str,
    loja: str,
    nome: str,
    start_date: str,
    finish_date: str,
) -> dict:
    nome_loja = str(loja or "").strip()
    nome_campanha = str(nome or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja do Mercado Livre.")
    if not nome_campanha:
        raise HTTPException(status_code=400, detail="Informe o nome da nova campanha.")

    def parse_data_local(valor: str, campo: str) -> dt.date:
        texto = str(valor or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto):
            raise HTTPException(status_code=400, detail=f"{campo} deve estar no formato YYYY-MM-DD.")
        try:
            return dt.datetime.strptime(texto, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{campo} e invalida.")

    inicio_data = parse_data_local(start_date, "A data inicial")
    fim_data = parse_data_local(finish_date, "A data final")
    if inicio_data < dt.date.today():
        raise HTTPException(status_code=400, detail="A data inicial nao pode ser anterior a hoje.")
    if fim_data < inicio_data:
        raise HTTPException(status_code=400, detail="A data final nao pode ser anterior a data inicial.")

    inicio_ml = f"{inicio_data:%Y-%m-%d}T00:00:00"
    fim_ml = f"{fim_data:%Y-%m-%d}T23:59:59"
    cfg = ctx._obter_cfg_ml(client_id, nome_loja)
    nova_id, payload, _cfg = _renovacao_criar_campanha_ml(
        client_id,
        nome_loja,
        cfg,
        nome_campanha,
        {},
        datas_alvo=(inicio_ml, fim_ml),
    )
    ctx._cache_invalidar_loja(client_id, nome_loja)
    return {
        "success": True,
        "loja": nome_loja,
        "nova_campanha": {
            "id": nova_id,
            "nome": payload["name"],
            "start_date": payload["start_date"],
            "finish_date": payload["finish_date"],
            "promotion_type": payload["promotion_type"],
            "sub_type": payload["sub_type"],
        },
    }


def _renovacao_normalizar_data_periodo(valor: str | None, *, fim: bool = False) -> str:
    texto = str(valor or "").strip()
    if not texto:
        return ""
    data = _renovacao_parse_data_ml(texto)
    if data:
        hora = "23:59:59" if fim else "00:00:00"
        return f"{data:%Y-%m-%d}T{hora}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto):
        return f"{texto}T{'23:59:59' if fim else '00:00:00'}"
    return texto


def _renovacao_data_periodo_igual(enviada: str, retornada: str | None) -> bool:
    if not enviada:
        return True
    enviada_dt = _renovacao_parse_data_ml(enviada)
    retornada_dt = _renovacao_parse_data_ml(retornada)
    if enviada_dt and retornada_dt:
        return enviada_dt.date() == retornada_dt.date()
    return str(enviada or "")[:10] == str(retornada or "")[:10]


def _renovacao_buscar_campanha_ml(
    client_id: str,
    loja: str,
    cfg: dict,
    campanha_id: str,
    promotion_type: str,
) -> tuple[dict, dict]:
    resp, cfg = ctx._ml_api_request(
        client_id,
        loja,
        cfg,
        "GET",
        f"https://api.mercadolibre.com/seller-promotions/promotions/{campanha_id}",
        params={"app_version": "v2", "promotion_type": promotion_type},
        timeout=25,
    )
    if resp.status_code != 200:
        return {}, cfg
    data = _renovacao_json_response(resp)
    if isinstance(data, dict):
        return data, cfg
    return {}, cfg


def _renovacao_descrever_periodo_campanha(campanha: dict | None) -> str:
    campanha = campanha if isinstance(campanha, dict) else {}
    inicio = campanha.get("start_date") or campanha.get("date_start") or campanha.get("begin_date") or ""
    fim = campanha.get("finish_date") or campanha.get("end_date") or campanha.get("date_end") or ""
    return f"{str(inicio)[:10] or '-'} ate {str(fim)[:10] or '-'}"


def _renovacao_atualizar_periodo_campanha_ml(
    client_id: str,
    loja: str,
    campanha_id: str,
    start_date: str | None,
    finish_date: str | None,
    promotion_type: str = "SELLER_CAMPAIGN",
    nome: str | None = None,
) -> dict:
    nome_loja = str(loja or "").strip()
    campanha_id = str(campanha_id or "").strip()
    promotion_type = str(promotion_type or "SELLER_CAMPAIGN").strip() or "SELLER_CAMPAIGN"
    if not nome_loja or not campanha_id:
        raise HTTPException(status_code=400, detail="Selecione a loja e a campanha.")

    inicio = _renovacao_normalizar_data_periodo(start_date, fim=False)
    fim = _renovacao_normalizar_data_periodo(finish_date, fim=True)
    if not inicio and not fim:
        raise HTTPException(status_code=400, detail="Informe pelo menos uma data para alterar o periodo.")

    payload = {}
    if str(nome or "").strip():
        payload["name"] = str(nome or "").strip()
    if inicio:
        payload["start_date"] = inicio
    if fim:
        payload["finish_date"] = fim

    inicio_dt = _renovacao_parse_data_ml(inicio) if inicio else None
    fim_dt = _renovacao_parse_data_ml(fim) if fim else None
    if inicio_dt and fim_dt and fim_dt <= inicio_dt:
        raise HTTPException(status_code=400, detail="A data final precisa ser maior que a data inicial.")

    cfg = ctx._obter_cfg_ml(client_id, nome_loja)
    resp, cfg = ctx._ml_api_request(
        client_id,
        nome_loja,
        cfg,
        "PUT",
        f"https://api.mercadolibre.com/seller-promotions/promotions/{campanha_id}",
        params={"app_version": "v2", "promotion_type": promotion_type},
        json=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=ctx._ml_parse_error_detail(resp, "Erro ao alterar periodo da campanha no Mercado Livre"))
    resposta_put = _renovacao_json_response(resp)
    campanha_verificada, cfg = _renovacao_buscar_campanha_ml(client_id, nome_loja, cfg, campanha_id, promotion_type)
    if not campanha_verificada or not (
        campanha_verificada.get("start_date")
        or campanha_verificada.get("date_start")
        or campanha_verificada.get("begin_date")
        or campanha_verificada.get("finish_date")
        or campanha_verificada.get("end_date")
        or campanha_verificada.get("date_end")
    ):
        try:
            campanhas, cfg = _renovacao_ml_listar_campanhas_usuario(client_id, nome_loja)
            campanha_verificada = next(
                (item for item in campanhas if str(item.get("id") or "") == campanha_id),
                {},
            )
        except Exception as exc:
            ctx.logger.warning(
                "[RENOVACAO PERIODO] Nao foi possivel confirmar periodo atualizado loja=%s campanha=%s: %s",
                nome_loja,
                campanha_id,
                exc,
            )

    inicio_ok = _renovacao_data_periodo_igual(inicio, campanha_verificada.get("start_date")) if inicio and campanha_verificada else True
    fim_ok = _renovacao_data_periodo_igual(fim, campanha_verificada.get("finish_date")) if fim and campanha_verificada else True
    if campanha_verificada and (not inicio_ok or not fim_ok):
        periodo_atual = _renovacao_descrever_periodo_campanha(campanha_verificada)
        ctx.logger.warning(
            "[RENOVACAO PERIODO] Mercado Livre manteve periodo antigo loja=%s campanha=%s payload=%s retorno=%s",
            nome_loja,
            campanha_id,
            payload,
            campanha_verificada,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "Mercado Livre recebeu a solicitacao, mas manteve o periodo atual "
                f"({periodo_atual}). Verifique se o novo intervalo excede o limite permitido "
                "ou se o status da campanha permite alterar essa data."
            ),
        )
    return {
        "success": True,
        "loja": nome_loja,
        "campanha_id": campanha_id,
        "payload": payload,
        "campaign": campanha_verificada or resposta_put,
    }


def _renovacao_deletar_campanha_ml(
    client_id: str,
    loja: str,
    campanha_id: str,
    promotion_type: str = "SELLER_CAMPAIGN",
) -> dict:
    nome_loja = str(loja or "").strip()
    campanha_id = str(campanha_id or "").strip()
    promotion_type = str(promotion_type or "SELLER_CAMPAIGN").strip() or "SELLER_CAMPAIGN"
    if not nome_loja or not campanha_id:
        raise HTTPException(status_code=400, detail="Selecione a loja e a campanha.")

    cfg = ctx._obter_cfg_ml(client_id, nome_loja)
    resp, cfg = ctx._ml_api_request(
        client_id,
        nome_loja,
        cfg,
        "DELETE",
        f"https://api.mercadolibre.com/seller-promotions/promotions/{campanha_id}",
        params={"app_version": "v2", "promotion_type": promotion_type},
        timeout=30,
    )
    if resp.status_code not in (200, 202, 204):
        raise HTTPException(status_code=resp.status_code, detail=ctx._ml_parse_error_detail(resp, "Erro ao deletar campanha no Mercado Livre"))
    _renovacao_remover_agendamento_campanha(client_id, nome_loja, campanha_id)
    return {
        "success": True,
        "loja": nome_loja,
        "campanha_id": campanha_id,
        "deleted": True,
    }


def _renovacao_adicionar_item_campanha(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    nova_campanha_id: str,
    desconto: float,
    preco_base: float,
    preco_promocional: Optional[float],
    preferir_percentual: bool = False,
) -> tuple[bool, str, dict]:
    deal_price = preco_promocional
    if deal_price is None and preco_base:
        deal_price = round(float(preco_base) * (1 - (float(desconto) / 100)), 2)
    if deal_price is None or deal_price <= 0:
        return False, "preco promocional invalido", cfg

    payload_preco = {
        "promotion_id": nova_campanha_id,
        "promotion_type": "SELLER_CAMPAIGN",
        "deal_price": round(float(deal_price), 2),
    }
    payload_percentual = {
        "promotion_id": nova_campanha_id,
        "promotion_type": "SELLER_CAMPAIGN",
        "discount_percentage": round(float(desconto), 4),
    }
    payloads = [payload_percentual, payload_preco] if preferir_percentual else [payload_preco, payload_percentual]
    ultimo_erro = ""
    for payload in payloads:
        resp, cfg = ctx._ml_api_request(
            client_id,
            loja,
            cfg,
            "POST",
            f"https://api.mercadolibre.com/seller-promotions/items/{item_id}",
            params={"app_version": "v2"},
            json=payload,
            timeout=20,
        )
        if resp.status_code in (200, 201):
            return True, "", cfg
        ultimo_erro = ctx._ml_parse_error_detail(resp, f"Erro {resp.status_code} ao incluir item")
    return False, ultimo_erro, cfg


def _renovacao_copiar_mlbs_campanha(
    client_id: str,
    loja: str,
    cfg: dict,
    campanha_origem_id: str,
    campanha_destino_id: str,
    promotion_type_origem: str = "SELLER_CAMPAIGN",
    promotion_type_destino: str = "SELLER_CAMPAIGN",
    preferir_percentual: bool = False,
    expected_origem_count: int | None = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> tuple[dict, dict]:
    _renovacao_emitir_progresso(
        progress_callback,
        "Origem",
        15,
        "Carregando MLBs ativos ou programados da campanha de origem...",
        campanha_id=campanha_origem_id,
        esperado=expected_origem_count or 0,
    )
    itens_ref, raw_por_item, cfg = _renovacao_listar_itens_campanha(
        client_id,
        loja,
        cfg,
        campanha_origem_id,
        promotion_type_origem or "SELLER_CAMPAIGN",
        expected_count=expected_origem_count,
        progress_callback=progress_callback,
        progress_start=18,
        progress_end=48,
    )
    item_ids = [str(item.get("id") or "").strip() for item in itens_ref if str(item.get("id") or "").strip()]
    item_ids = list(dict.fromkeys(item_ids))
    if not item_ids:
        raise HTTPException(status_code=400, detail="Nao encontrei MLBs ativos ou programados nessa campanha para copiar.")
    _renovacao_emitir_progresso(
        progress_callback,
        "Origem",
        50,
        f"Campanha de origem carregada com {len(item_ids)} MLB(s).",
        total=len(item_ids),
    )

    ids_destino: set[str] = set()
    try:
        _renovacao_emitir_progresso(
            progress_callback,
            "Destino",
            52,
            "Conferindo MLBs que ja estao na campanha nova...",
            campanha_id=campanha_destino_id,
        )
        itens_destino, _raw_destino, cfg = _renovacao_listar_itens_campanha(
            client_id,
            loja,
            cfg,
            campanha_destino_id,
            promotion_type_destino or "SELLER_CAMPAIGN",
            progress_callback=progress_callback,
            progress_start=53,
            progress_end=58,
        )
        ids_destino = {
            str(item.get("id") or "").strip()
            for item in itens_destino
            if str(item.get("id") or "").strip()
        }
    except Exception as exc:
        ctx.logger.warning("[RENOVACAO ML] Falha ao verificar itens da campanha destino %s: %s", campanha_destino_id, exc)
        ids_destino = set()

    item_ids_para_incluir = [item_id for item_id in item_ids if item_id not in ids_destino]
    _renovacao_emitir_progresso(
        progress_callback,
        "Comparacao",
        60,
        f"{len(item_ids_para_incluir)} MLB(s) faltam na campanha nova. {len(ids_destino.intersection(set(item_ids)))} ja estavam presentes.",
        faltantes=len(item_ids_para_incluir),
        ja_presentes=len(ids_destino.intersection(set(item_ids))),
    )
    detalhes_por_id = {}
    if item_ids_para_incluir:
        _renovacao_emitir_progresso(
            progress_callback,
            "Detalhes",
            62,
            f"Buscando detalhes de {len(item_ids_para_incluir)} MLB(s) para calcular preco e desconto...",
            total=len(item_ids_para_incluir),
        )
        detalhes, cfg = ctx._ml_buscar_itens_batch(client_id, loja, cfg, item_ids_para_incluir)
        detalhes_por_id = {str(item.get("id") or "").strip(): item for item in detalhes if isinstance(item, dict)}

    sucessos = []
    falhas = []
    total_para_incluir = len(item_ids_para_incluir)
    intervalo_emit = max(1, min(25, max(1, total_para_incluir // 50))) if total_para_incluir else 1
    for idx_item, item_id in enumerate(item_ids_para_incluir, start=1):
        if idx_item == 1 or idx_item == total_para_incluir or idx_item % intervalo_emit == 0:
            pct = 65 + (28 * idx_item / max(1, total_para_incluir))
            _renovacao_emitir_progresso(
                progress_callback,
                "Inclusao",
                pct,
                f"Incluindo MLB {idx_item}/{total_para_incluir}: {item_id}",
                item_id=item_id,
                processados=idx_item,
                total=total_para_incluir,
                incluidos=len(sucessos),
                falhas=len(falhas),
            )
        desconto, preco_base, preco_promocional = _renovacao_desconto_item(
            item_id,
            raw_por_item.get(item_id) or {},
            detalhes_por_id.get(item_id) or {},
        )
        if desconto is None or preco_base is None:
            raw_exato, cfg = ctx._ml_obter_item_promocao_raw(
                client_id,
                loja,
                cfg,
                campanha_origem_id,
                promotion_type_origem or "SELLER_CAMPAIGN",
                item_id,
            )
            if raw_exato:
                desconto, preco_base, preco_promocional = _renovacao_desconto_item(
                    item_id,
                    raw_exato,
                    detalhes_por_id.get(item_id) or {},
                )
        if desconto is None or preco_base is None:
            falhas.append({"item_id": item_id, "erro": "Nao foi possivel identificar desconto/preco base"})
            continue
        ok, erro, cfg = _renovacao_adicionar_item_campanha(
            client_id,
            loja,
            cfg,
            item_id,
            campanha_destino_id,
            desconto,
            preco_base,
            preco_promocional,
            preferir_percentual=preferir_percentual,
        )
        if ok:
            sucessos.append({"item_id": item_id, "desconto": desconto})
        else:
            falhas.append({"item_id": item_id, "erro": erro})

    _renovacao_emitir_progresso(
        progress_callback,
        "Finalizando",
        96,
        f"Finalizando sincronizacao. Incluidos: {len(sucessos)}. Ja presentes: {len(ids_destino.intersection(set(item_ids)))}. Falhas: {len(falhas)}.",
        incluidos=len(sucessos),
        falhas=len(falhas),
    )
    return {
        "total_origem": len(item_ids),
        "mlbs_ja_presentes": len(ids_destino.intersection(set(item_ids))),
        "faltantes": len(item_ids_para_incluir),
        "incluidos": len(sucessos),
        "falhas": falhas[:50],
        "falhas_total": len(falhas),
    }, cfg


def _renovacao_criar_ou_completar_proximo_mes(
    client_id: str,
    loja: str,
    campanha_id: str,
    nome: str = "",
    promotion_type: str = "SELLER_CAMPAIGN",
) -> dict:
    loja = str(loja or "").strip()
    campanha_id = str(campanha_id or "").strip()
    nome = str(nome or "").strip()
    promotion_type = str(promotion_type or "SELLER_CAMPAIGN").strip() or "SELLER_CAMPAIGN"
    if not loja or not campanha_id:
        raise HTTPException(status_code=400, detail="Informe loja e campanha de origem.")

    campanhas, cfg = _renovacao_ml_listar_campanhas_usuario(client_id, loja)
    campanha_fonte = next((c for c in campanhas if str(c.get("id") or "").strip() == campanha_id), None)
    if not campanha_fonte:
        campanha_fonte = {"id": campanha_id, "promotion_type": promotion_type}
    if not nome:
        nome = _renovacao_nome_proximo_mes(campanha_fonte)
    if not nome:
        raise HTTPException(status_code=400, detail="Informe o nome da nova campanha.")

    inicio_alvo, fim_alvo = _renovacao_datas_proximo_mes(campanha_fonte)
    campanha_destino = _renovacao_encontrar_campanha_destino(campanhas, nome, inicio_alvo)
    campanha_existente = bool(campanha_destino)
    if campanha_destino:
        nova_campanha_id = str(campanha_destino.get("id") or "").strip()
        payload_campanha = {
            "name": str(campanha_destino.get("name") or nome).strip(),
            "start_date": campanha_destino.get("start_date") or inicio_alvo,
            "finish_date": campanha_destino.get("finish_date") or fim_alvo,
        }
        promotion_type_destino = str(campanha_destino.get("promotion_type") or campanha_destino.get("type") or "SELLER_CAMPAIGN").strip() or "SELLER_CAMPAIGN"
    else:
        nova_campanha_id, payload_campanha, cfg = _renovacao_criar_campanha_ml(
            client_id,
            loja,
            cfg,
            nome,
            campanha_fonte,
            datas_alvo=(inicio_alvo, fim_alvo),
        )
        promotion_type_destino = "SELLER_CAMPAIGN"

    itens_ref, raw_por_item, cfg = _renovacao_listar_itens_campanha(client_id, loja, cfg, campanha_id, promotion_type)
    item_ids = [str(item.get("id") or "").strip() for item in itens_ref if str(item.get("id") or "").strip()]
    item_ids = list(dict.fromkeys(item_ids))
    if not item_ids:
        raise HTTPException(status_code=400, detail="Nao encontrei MLBs ativos ou programados nessa campanha para copiar.")

    ids_destino: set[str] = set()
    if campanha_existente:
        try:
            itens_destino, _raw_destino, cfg = _renovacao_listar_itens_campanha(
                client_id,
                loja,
                cfg,
                nova_campanha_id,
                promotion_type_destino,
            )
            ids_destino = {
                str(item.get("id") or "").strip()
                for item in itens_destino
                if str(item.get("id") or "").strip()
            }
        except Exception as exc:
            ctx.logger.warning("[RENOVACAO ML] Falha ao verificar itens da campanha existente %s: %s", nova_campanha_id, exc)
            ids_destino = set()

    item_ids_para_incluir = [item_id for item_id in item_ids if item_id not in ids_destino]
    detalhes_por_id = {}
    if item_ids_para_incluir:
        detalhes, cfg = ctx._ml_buscar_itens_batch(client_id, loja, cfg, item_ids_para_incluir)
        detalhes_por_id = {str(item.get("id") or "").strip(): item for item in detalhes if isinstance(item, dict)}

    sucessos = []
    falhas = []
    for item_id in item_ids_para_incluir:
        desconto, preco_base, preco_promocional = _renovacao_desconto_item(item_id, raw_por_item.get(item_id) or {}, detalhes_por_id.get(item_id) or {})
        if desconto is None or preco_base is None:
            falhas.append({"item_id": item_id, "erro": "Nao foi possivel identificar desconto/preco base"})
            continue
        ok, erro, cfg = _renovacao_adicionar_item_campanha(
            client_id,
            loja,
            cfg,
            item_id,
            nova_campanha_id,
            desconto,
            preco_base,
            preco_promocional,
        )
        if ok:
            sucessos.append({"item_id": item_id, "desconto": desconto})
        else:
            falhas.append({"item_id": item_id, "erro": erro})

    ctx._cache_invalidar_loja(client_id, loja)
    return {
        "success": True,
        "loja": loja,
        "origem": {"id": campanha_id, "nome": campanha_fonte.get("name") or campanha_id},
        "nova_campanha": {
            "id": nova_campanha_id,
            "nome": payload_campanha.get("name") or nome,
            "start_date": payload_campanha.get("start_date"),
            "finish_date": payload_campanha.get("finish_date"),
            "existente": campanha_existente,
        },
        "campanha_existente": campanha_existente,
        "total_origem": len(item_ids),
        "mlbs_ja_presentes": len(ids_destino.intersection(set(item_ids))),
        "faltantes": len(item_ids_para_incluir),
        "incluidos": len(sucessos),
        "falhas": falhas[:50],
        "falhas_total": len(falhas),
    }


def _renovacao_sincronizar_promocao_existente(
    client_id: str,
    loja: str,
    campanha_origem_id: str,
    campanha_destino_id: str,
    promotion_type_origem: str = "SELLER_CAMPAIGN",
    promotion_type_destino: str = "SELLER_CAMPAIGN",
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    _renovacao_emitir_progresso(
        progress_callback,
        "Preparando",
        5,
        "Validando loja e campanhas selecionadas...",
    )
    loja = str(loja or "").strip()
    campanha_origem_id = str(campanha_origem_id or "").strip()
    campanha_destino_id = str(campanha_destino_id or "").strip()
    promotion_type_origem = str(promotion_type_origem or "SELLER_CAMPAIGN").strip() or "SELLER_CAMPAIGN"
    promotion_type_destino = str(promotion_type_destino or "SELLER_CAMPAIGN").strip() or "SELLER_CAMPAIGN"
    if not loja or not campanha_origem_id or not campanha_destino_id:
        raise HTTPException(status_code=400, detail="Informe loja, campanha de origem e campanha nova.")
    if campanha_origem_id == campanha_destino_id:
        raise HTTPException(status_code=400, detail="A campanha de origem e a campanha nova devem ser diferentes.")

    _renovacao_emitir_progresso(
        progress_callback,
        "Campanhas",
        10,
        "Listando campanhas criadas pelo usuario no Mercado Livre...",
        loja=loja,
    )
    campanhas, cfg = _renovacao_ml_listar_campanhas_usuario(client_id, loja)
    campanha_origem = next((c for c in campanhas if str(c.get("id") or "").strip() == campanha_origem_id), None) or {
        "id": campanha_origem_id,
        "promotion_type": promotion_type_origem,
    }
    campanha_destino = next((c for c in campanhas if str(c.get("id") or "").strip() == campanha_destino_id), None) or {
        "id": campanha_destino_id,
        "promotion_type": promotion_type_destino,
    }
    promotion_type_origem = str(campanha_origem.get("promotion_type") or campanha_origem.get("type") or promotion_type_origem).strip() or "SELLER_CAMPAIGN"
    promotion_type_destino = str(campanha_destino.get("promotion_type") or campanha_destino.get("type") or promotion_type_destino).strip() or "SELLER_CAMPAIGN"
    active_origem = ctx._parse_float_flex(campanha_origem.get("active_count"))
    programados_origem = ctx._parse_float_flex(campanha_origem.get("programmed_count"))
    if programados_origem is None:
        programados_origem = ctx._parse_float_flex(campanha_origem.get("scheduled_count"))
    expected_origem_count = int(active_origem or 0) + int(programados_origem or 0)
    if expected_origem_count <= 0:
        total_origem = ctx._parse_float_flex(campanha_origem.get("total_count"))
        expected_origem_count = int(total_origem or 0)
    _renovacao_emitir_progresso(
        progress_callback,
        "Campanhas",
        12,
        f"Origem localizada. Ativos/programados esperados: {expected_origem_count or '-'}."
    )

    copia, cfg = _renovacao_copiar_mlbs_campanha(
        client_id,
        loja,
        cfg,
        campanha_origem_id,
        campanha_destino_id,
        promotion_type_origem,
        promotion_type_destino,
        preferir_percentual=True,
        expected_origem_count=expected_origem_count or None,
        progress_callback=progress_callback,
    )

    _renovacao_emitir_progresso(
        progress_callback,
        "Finalizando",
        98,
        "Atualizando cache local das campanhas...",
    )
    ctx._cache_invalidar_loja(client_id, loja)
    return {
        "success": True,
        "loja": loja,
        "origem": {
            "id": campanha_origem_id,
            "nome": campanha_origem.get("name") or campanha_origem.get("title") or campanha_origem_id,
        },
        "campanha_destino": {
            "id": campanha_destino_id,
            "nome": campanha_destino.get("name") or campanha_destino.get("title") or campanha_destino_id,
            "start_date": campanha_destino.get("start_date"),
            "finish_date": campanha_destino.get("finish_date"),
        },
        **copia,
    }


def _renovacao_listar_campanhas_usuario_payload(client_id: str, loja: str):
    campanhas, _cfg = _renovacao_ml_listar_campanhas_usuario(client_id, loja)
    ag_payload = _renovacao_agendamentos_carregar(client_id)
    ag_por_chave = {}
    for entry in ag_payload.get("agendamentos") or []:
        ag = _renovacao_agendamento_sanitizar(entry)
        if _renovacao_normalizar_nome(ag.get("loja")) == _renovacao_normalizar_nome(loja):
            ag_por_chave[_renovacao_agendamento_key(ag.get("loja"), ag.get("campanha_id"))] = ag
    for campanha in campanhas:
        ag = ag_por_chave.get(_renovacao_agendamento_key(loja, campanha.get("id")))
        campanha["auto_renew_enabled"] = bool((ag or {}).get("enabled"))
        campanha["agendamento"] = ag or {
            "enabled": False,
            "loja": str(loja or "").strip(),
            "campanha_id": str(campanha.get("id") or "").strip(),
            "last_error": "",
        }
        campanha.pop("raw", None)
    return {"success": True, "loja": loja, "campaigns": campanhas, "total": len(campanhas)}


def _renovacao_sincronizar_promocao_worker(client_id: str, job_id: str, payload: dict) -> None:
    def progress_hook(data: dict) -> None:
        _renovacao_sync_job_update(job_id, status="running", **(data or {}))

    try:
        _renovacao_emitir_progresso(progress_hook, "Preparando", 2, "Iniciando sincronizacao da promocao...")
        resultado = _renovacao_sincronizar_promocao_existente(
            client_id,
            payload.get("loja") or "",
            payload.get("campanha_origem_id") or "",
            payload.get("campanha_destino_id") or "",
            payload.get("promotion_type_origem") or "SELLER_CAMPAIGN",
            payload.get("promotion_type_destino") or "SELLER_CAMPAIGN",
            progress_callback=progress_hook,
        )
        _renovacao_sync_job_update(
            job_id,
            status="done",
            etapa="Concluido",
            percentual=100,
            progress=100,
            mensagem="Sincronizacao concluida.",
            result=jsonable_encoder(resultado),
            error="",
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
        _renovacao_sync_job_update(
            job_id,
            status="error",
            etapa="Erro",
            percentual=100,
            progress=100,
            mensagem=detail or "Erro ao sincronizar promocao.",
            error=detail or "Erro ao sincronizar promocao.",
        )
    except Exception as exc:
        ctx.logger.exception("[RENOVACAO ML] Falha no job de sincronizacao %s", job_id)
        msg = str(exc) or "Erro ao sincronizar promocao."
        _renovacao_sync_job_update(
            job_id,
            status="error",
            etapa="Erro",
            percentual=100,
            progress=100,
            mensagem=msg,
            error=msg,
        )


def _renovacao_sincronizar_promocao_iniciar_payload(client_id: str, req: RenovacaoCampanhaSincronizarRequest):
    _renovacao_sync_jobs_limpar_antigos()
    job_id = uuid.uuid4().hex
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    _renovacao_sync_job_update(
        job_id,
        client_id=client_id,
        status="running",
        etapa="Preparando",
        percentual=0,
        progress=0,
        mensagem="Aguardando inicio da sincronizacao...",
        result=None,
        error="",
    )
    thread = threading.Thread(
            target=with_request_context(_renovacao_sincronizar_promocao_worker),
        args=(client_id, job_id, payload),
        daemon=True,
        name=f"renovacao-sync-{job_id[:8]}",
    )
    thread.start()
    return {"success": True, "job_id": job_id}
