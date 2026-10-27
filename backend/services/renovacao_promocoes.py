"""Mercado Livre campaign operations for Renovacao."""

from __future__ import annotations
from backend.services.central_accounts_client import with_request_context

import datetime as dt
import json
import math
import os
import re
import tempfile
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
    _renovacao_contagem_esperada,
    _renovacao_consultar_participacoes,
    _renovacao_confirmar_lote,
    _renovacao_desconto_item,
    _renovacao_emitir_progresso,
    _renovacao_encontrar_campanha_destino,
    _renovacao_exigir_coleta_completa,
    _renovacao_json_response,
    _renovacao_listar_itens_campanha,
    _renovacao_ml_listar_campanhas_usuario,
    _renovacao_nome_proximo_mes,
    _renovacao_normalizar_nome,
    _renovacao_parse_data_ml,
    _renovacao_participante_campanha,
    _renovacao_preco_base_atual,
    _renovacao_preco_com_desconto,
    _renovacao_sync_job_update,
    _renovacao_status_participacao,
    _renovacao_sync_jobs_limpar_antigos,
    _renovacao_tipo_campanha,
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
        detalhe = ctx._ml_parse_error_detail(resp, "Falha na solicitacao")
        raise HTTPException(status_code=resp.status_code, detail=f"Erro ao criar campanha no Mercado Livre: {detalhe}")
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
    promotion_type = _renovacao_tipo_campanha(campanha_id, promotion_type)
    if not nome_loja or not campanha_id:
        raise HTTPException(status_code=400, detail="Selecione a loja e a campanha.")

    inicio = _renovacao_normalizar_data_periodo(start_date, fim=False)
    fim = _renovacao_normalizar_data_periodo(finish_date, fim=True)
    if not inicio and not fim:
        raise HTTPException(status_code=400, detail="Informe pelo menos uma data para alterar o periodo.")

    payload = {"promotion_type": promotion_type}
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
        detalhe = ctx._ml_parse_error_detail(resp, "Falha na solicitacao")
        raise HTTPException(status_code=resp.status_code, detail=f"Erro ao alterar periodo da campanha no Mercado Livre: {detalhe}")
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
    promotion_type = _renovacao_tipo_campanha(campanha_id, promotion_type)
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
        detalhe = ctx._ml_parse_error_detail(resp, "Falha na solicitacao")
        raise HTTPException(status_code=resp.status_code, detail=f"Erro ao excluir campanha no Mercado Livre: {detalhe}")
    _renovacao_remover_agendamento_campanha(client_id, nome_loja, campanha_id)
    return {
        "success": True,
        "loja": nome_loja,
        "campanha_id": campanha_id,
        "deleted": True,
    }


def _renovacao_identidade_participacao_valida(raw: dict, item_id: str, campanha_id: str) -> bool:
    if not isinstance(raw, dict) or raw.get("error"):
        return False
    status = _renovacao_status_participacao(raw)
    if status and status not in {"active", "started", "approved", "pending", "programmed", "programada", "scheduled", "ready_to_start"}:
        return False
    if raw.get("item_id") and str(raw["item_id"]) != item_id:
        return False
    for chave in ("promotion_id", "campaign_id"):
        if raw.get(chave) and str(raw[chave]) != campanha_id:
            return False
    for chave, esperado in (("item", item_id), ("campaign", campanha_id), ("promotion", campanha_id)):
        nested = raw.get(chave)
        if isinstance(nested, dict) and nested.get("id") and str(nested["id"]) != esperado:
            return False
    identificador = str(raw.get("id") or "")
    if identificador.startswith("MLB") and identificador != item_id:
        return False
    if identificador.startswith("C-MLB") and identificador != campanha_id:
        return False
    tipo = str(raw.get("promotion_type") or raw.get("type") or "")
    if tipo and tipo != "SELLER_CAMPAIGN":
        return False
    return True


def _renovacao_valores_participacao(raw: dict, item_id: str, campanha_id: str):
    if not _renovacao_identidade_participacao_valida(raw, item_id, campanha_id):
        return None
    preco = ctx._parse_float_flex(raw.get("price") or raw.get("deal_price"))
    base = ctx._parse_float_flex(raw.get("original_price"))
    if any(v is None or not math.isfinite(float(v)) or v <= 0 for v in (preco, base)):
        return None
    return float(preco), float(base)


def _renovacao_participacao_mantem_desconto(raw, item_id, campanha_id, desconto, preco_base=None):
    valores = _renovacao_valores_participacao(raw, item_id, campanha_id)
    if valores is None:
        return False
    preco, base = valores
    declarado = raw.get("discount_percentage")
    if declarado is None:
        declarado = raw.get("discount_percent")
    if declarado is not None:
        declarado = ctx._parse_float_flex(declarado)
        if declarado is None or not math.isfinite(float(declarado)) or abs(float(declarado) - desconto) > 0.005:
            return False
    if round(preco, 2) != _renovacao_preco_com_desconto(base, desconto):
        return False
    return preco_base is None or round(preco, 2) == _renovacao_preco_com_desconto(preco_base, desconto)


def _renovacao_adicionar_item_campanha(
    client_id: str, loja: str, cfg: dict, item_id: str, nova_campanha_id: str,
    desconto: float, preco_base: float, preco_promocional: Optional[float],
    preferir_percentual: bool = False, metodo: str = "POST", campanha_origem_id: str | None = None,
) -> tuple[bool, str, dict]:
    if _renovacao_preco_com_desconto(preco_base, desconto) is None:
        return False, "preco base ou percentual de desconto invalido", cfg
    campanhas = [nova_campanha_id] + ([campanha_origem_id] if campanha_origem_id else [])

    def ler_atual(cfg_atual):
        return _renovacao_consultar_participacoes(client_id, loja, cfg_atual, item_id, campanhas)

    def origem_confirmada(estado):
        if not campanha_origem_id:
            return True
        origem = estado.get(campanha_origem_id) or {}
        if origem.get("situacao") != "participante":
            return False
        percentual, _, _ = _renovacao_desconto_item(item_id, origem.get("raw") or {}, {})
        return percentual is not None and abs(percentual - desconto) < 0.0001

    estado, cfg = ler_atual(cfg)
    if not origem_confirmada(estado):
        return False, "Participacao ou percentual da origem nao confirmado antes da alteracao", cfg
    destino = estado.get(nova_campanha_id) or {}
    if destino.get("situacao") == "inconclusivo":
        return False, "Participacao no destino inconclusiva; nenhuma alteracao foi aplicada", cfg
    if destino.get("situacao") == "participante":
        if _renovacao_participacao_mantem_desconto(destino.get("raw") or {}, item_id, nova_campanha_id, desconto):
            return True, "", cfg
        metodo = "PUT"
    elif destino.get("situacao") == "ausente":
        metodo = "POST"
    else:
        return False, "Nao foi possivel confirmar a participacao no destino", cfg

    ajuste_usado = False
    recuperacao_usada = False
    for _tentativa in range(3):
        payload = {"promotion_id": nova_campanha_id, "promotion_type": "SELLER_CAMPAIGN",
                   "deal_price": _renovacao_preco_com_desconto(preco_base, desconto)}
        resp, cfg = ctx._ml_api_request(client_id, loja, cfg, metodo,
            f"https://api.mercadolibre.com/seller-promotions/items/{item_id}",
            params={"app_version": "v2"}, json=payload, timeout=20)
        if resp.status_code not in (200, 201):
            detalhe = ctx._ml_parse_error_detail(resp, f"Erro {resp.status_code}")
            sem_oferta = "no offers found" in str(detalhe).lower()
            if metodo == "PUT" and resp.status_code in (400, 404) and sem_oferta and not recuperacao_usada:
                estado, cfg = ler_atual(cfg)
                destino = estado.get(nova_campanha_id) or {}
                if not origem_confirmada(estado):
                    return False, "A origem nao foi confirmada apos a oferta desaparecer", cfg
                if destino.get("situacao") == "participante" and _renovacao_participacao_mantem_desconto(
                    destino.get("raw") or {}, item_id, nova_campanha_id, desconto):
                    return True, "", cfg
                if destino.get("situacao") == "ausente":
                    recuperacao_usada = True
                    metodo = "POST"
                    continue
            acao = "atualizar" if metodo == "PUT" else "incluir"
            return False, f"Erro ao {acao} anuncio na campanha: {detalhe}", cfg

        # O 2xx e provisório: somente a participacao exata lida depois confirma
        # existencia e percentual. Nunca envia PUT apenas com base no 2xx.
        for _leitura in range(2):
            estado, cfg = ler_atual(cfg)
            destino = estado.get(nova_campanha_id) or {}
            if not origem_confirmada(estado):
                return False, "A origem mudou ou ficou inconclusiva durante a confirmacao", cfg
            if destino.get("situacao") != "ausente":
                break
        if destino.get("situacao") == "inconclusivo":
            return False, "Alteracao recebida, mas a participacao no destino ficou inconclusiva", cfg
        if destino.get("situacao") != "participante":
            return False, "Inclusao recebida, mas a oferta ainda nao foi confirmada no destino", cfg
        raw = destino.get("raw") or {}
        if _renovacao_participacao_mantem_desconto(raw, item_id, nova_campanha_id, desconto):
            return True, "", cfg
        valores = _renovacao_valores_participacao(raw, item_id, nova_campanha_id)
        if valores and not ajuste_usado:
            preco_base = valores[1]
            ajuste_usado = True
            metodo = "PUT"
            continue
        break
    return False, "Mercado Livre nao confirmou o percentual de desconto da origem no destino", cfg


def _renovacao_montar_conferencia(consultas, campanha_origem_id, campanha_destino_id, esperado):
    def numero(valor):
        try:
            n = ctx._parse_float_flex(valor)
            return n if n is not None and math.isfinite(float(n)) else None
        except (ValueError, TypeError, OverflowError):
            return None

    linhas, extras, inconclusivos = [], [], []
    estados_origem = {}
    for item_id, resultado in consultas.items():
        origem = resultado.get(campanha_origem_id) or {"situacao": "inconclusivo"}
        destino = resultado.get(campanha_destino_id) or {"situacao": "inconclusivo"}
        if origem.get("situacao") == "inconclusivo":
            inconclusivos.append({"item_id": item_id, "erro": origem.get("erro") or "Origem inconclusiva"})
            continue
        if origem.get("situacao") != "participante":
            if destino.get("situacao") == "participante":
                extras.append(item_id)
            elif destino.get("situacao") == "inconclusivo":
                inconclusivos.append({"item_id": item_id, "erro": destino.get("erro") or "Destino inconclusivo"})
            continue
        raw_origem, raw_destino = origem.get("raw") or {}, destino.get("raw") or {}
        status = _renovacao_status_participacao(raw_origem)
        estados_origem[status] = estados_origem.get(status, 0) + 1
        percentual, _, _ = _renovacao_desconto_item(item_id, raw_origem, {})
        percentual_destino, _, _ = _renovacao_desconto_item(item_id, raw_destino, {}) if raw_destino else (None,None,None)
        confirmado = (percentual is not None and destino.get("situacao") == "participante" and
            _renovacao_participacao_mantem_desconto(raw_destino, item_id, campanha_destino_id, percentual))
        erro = ""
        if percentual is None:
            erro = "Percentual da origem inconclusivo"
        elif destino.get("situacao") == "inconclusivo":
            erro = destino.get("erro") or "Destino inconclusivo"
        elif destino.get("situacao") != "participante":
            erro = "Participacao ausente no destino"
        elif not confirmado:
            erro = "Preco ou percentual do destino diverge da origem"
        linhas.append({"item_id": item_id, "status_origem": status,
            "status_destino": _renovacao_status_participacao(raw_destino),
            "percentual_origem": percentual, "percentual_destino": percentual_destino,
            "preco_origem": numero(raw_origem.get("price") or raw_origem.get("deal_price")),
            "preco_destino": numero(raw_destino.get("price") or raw_destino.get("deal_price")),
            "base_destino": numero(raw_destino.get("original_price")),
            "confirmado": confirmado, "erro": erro})
    confirmados = sum(bool(item["confirmado"]) for item in linhas)
    contagem_coerente = esperado is not None and len(linhas) == esperado
    completa = contagem_coerente and not inconclusivos and confirmados == len(linhas)
    return {"completa": completa, "esperado": esperado, "total_origem": len(linhas),
        "confirmados": confirmados, "pendentes": len(linhas)-confirmados,
        "contagem_coerente": contagem_coerente, "estados_origem": estados_origem,
        "inconclusivos": inconclusivos, "extras_destino": sorted(extras), "itens": linhas}


def _renovacao_copiar_mlbs_campanha(
    client_id: str, loja: str, cfg: dict, campanha_origem_id: str, campanha_destino_id: str,
    promotion_type_origem: str = "SELLER_CAMPAIGN", promotion_type_destino: str = "SELLER_CAMPAIGN",
    preferir_percentual: bool = False, expected_origem_count: int | None = None,
    progress_callback: Optional[Callable[[dict], None]] = None, verificar_destino: bool = True,
    origem_preparada: tuple[list[dict], dict] | None = None, expected_destino_count: int | None = None,
    somente_conferir: bool = False,
) -> tuple[dict, dict]:
    stats = {"campanhas_consulta": [campanha_origem_id, campanha_destino_id]}
    if origem_preparada is not None:
        candidatos = [str(item.get("id")) for item in origem_preparada[0] if item.get("id")]
        consultas, cfg = _renovacao_confirmar_lote(client_id, loja, cfg, candidatos,
            stats["campanhas_consulta"], stats, progress_callback, 18, 48)
        raw_por_item = {item: estado[campanha_origem_id]["raw"] for item,estado in consultas.items()
            if estado.get(campanha_origem_id,{}).get("situacao") == "participante"}
        itens_ref = [{"id": item} for item in raw_por_item]
    else:
        itens_ref, raw_por_item, cfg = _renovacao_listar_itens_campanha(
            client_id, loja, cfg, campanha_origem_id, promotion_type_origem,
            expected_count=expected_origem_count, progress_callback=progress_callback,
            progress_start=18, progress_end=48, validar_quantidade=not somente_conferir,
            estatisticas=stats)
    item_ids = list(dict.fromkeys(str(item.get("id")) for item in itens_ref if item.get("id")))
    if not somente_conferir:
        _renovacao_exigir_coleta_completa(itens_ref, expected_origem_count, campanha_origem_id)
        if not item_ids:
            raise HTTPException(400, "Nao encontrei participantes confirmados na campanha de origem.")
        if stats.get("inconclusivos"):
            raise HTTPException(409, "Existem consultas de participacao inconclusivas. Nenhuma copia foi iniciada.")
    consultas_iniciais = stats.get("consultas", {})
    # Mesmo uma coleta preparada so pode decidir POST/PUT por associacao exata.
    faltando_snapshot = [item for item in item_ids if item not in consultas_iniciais]
    if faltando_snapshot:
        _novas, cfg = _renovacao_confirmar_lote(client_id, loja, cfg, faltando_snapshot,
            stats["campanhas_consulta"], stats, progress_callback, 49, 58)
        consultas_iniciais = stats["consultas"]
    ids_destino = {item for item in item_ids if
        consultas_iniciais.get(item,{}).get(campanha_destino_id,{}).get("situacao") == "participante"}
    erros_operacao = {}
    atualizados = set()
    if not somente_conferir:
        detalhes, cfg = ctx._ml_buscar_itens_batch(client_id, loja, cfg, item_ids)
        detalhes_por_id = {str(item.get("id")): item for item in detalhes if isinstance(item,dict)}
        for numero, item_id in enumerate(item_ids, start=1):
            if numero == 1 or numero == len(item_ids) or numero % 20 == 0:
                _renovacao_emitir_progresso(progress_callback, "Sincronizacao",
                    60+30*numero/max(1,len(item_ids)), f"Conferindo e sincronizando {numero}/{len(item_ids)}: {item_id}")
            try:
                percentual, _, preco_origem = _renovacao_desconto_item(item_id, raw_por_item[item_id], {})
                if percentual is None:
                    erros_operacao[item_id] = "Percentual da origem nao confirmado"
                    continue
                destino = consultas_iniciais.get(item_id,{}).get(campanha_destino_id,{})
                if destino.get("situacao") == "participante" and _renovacao_participacao_mantem_desconto(
                    destino.get("raw") or {}, item_id, campanha_destino_id, percentual):
                    continue
                base, cfg = _renovacao_preco_base_atual(client_id, loja, cfg, item_id, detalhes_por_id.get(item_id) or {})
                if base is None:
                    erros_operacao[item_id] = "Preco base atual nao confirmado"
                    continue
                ok, erro, cfg = _renovacao_adicionar_item_campanha(client_id, loja, cfg, item_id,
                    campanha_destino_id, percentual, base, preco_origem, preferir_percentual=preferir_percentual,
                    metodo="PUT" if item_id in ids_destino else "POST", campanha_origem_id=campanha_origem_id)
                if not ok:
                    erros_operacao[item_id] = erro
                elif item_id in ids_destino:
                    atualizados.add(item_id)
            except HTTPException as exc:
                erros_operacao[item_id] = str(exc.detail)
            except Exception:
                erros_operacao[item_id] = "Falha ao consultar ou aplicar o desconto deste item"
        # Verificacao final independente das respostas de POST/PUT.
        final_stats = {}
        consultas, cfg = _renovacao_confirmar_lote(client_id, loja, cfg,
            list(consultas_iniciais), stats["campanhas_consulta"], final_stats,
            progress_callback, 91, 97)
    else:
        consultas = consultas_iniciais
    conferencia = _renovacao_montar_conferencia(consultas, campanha_origem_id,
                                              campanha_destino_id, expected_origem_count)
    rows = conferencia["itens"]
    falhas = [{"item_id": row["item_id"], "erro": row["erro"]} for row in rows if not row["confirmado"]]
    falhas.extend(conferencia["inconclusivos"])
    finais_confirmados = {row["item_id"] for row in rows if row["confirmado"]}
    resumo = {"success": conferencia["completa"], "somente_conferir": somente_conferir,
        "total_origem": conferencia["total_origem"], "total_origem_esperado": expected_origem_count,
        "mlbs_ja_presentes": len(ids_destino), "faltantes": len(set(item_ids)-ids_destino),
        "incluidos": 0 if somente_conferir else len(finais_confirmados-ids_destino),
        "atualizados": len(atualizados & finais_confirmados),
        "falhas": falhas[:50], "falhas_total": len(falhas),
        "conferencia": conferencia, "erros_operacao": erros_operacao}
    return _renovacao_sanitizar_relatorio(resumo), cfg


def _renovacao_criar_ou_completar_proximo_mes(
    client_id: str,
    loja: str,
    campanha_id: str,
    nome: str = "",
    promotion_type: str = "SELLER_CAMPAIGN",
    quantidade_origem_esperada: int | None = None,
) -> dict:
    loja = str(loja or "").strip()
    campanha_id = str(campanha_id or "").strip()
    nome = str(nome or "").strip()
    promotion_type = _renovacao_tipo_campanha(campanha_id, promotion_type)
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

    esperado_origem = _renovacao_contagem_esperada(campanha_fonte)
    if quantidade_origem_esperada is not None:
        if esperado_origem is not None and esperado_origem != quantidade_origem_esperada:
            raise HTTPException(409, f"A contagem da origem mudou de {quantidade_origem_esperada} para {esperado_origem}. Atualize as campanhas e confira antes de copiar.")
        esperado_origem = quantidade_origem_esperada
    itens_origem, raw_origem, cfg = _renovacao_listar_itens_campanha(
        client_id, loja, cfg, campanha_id, promotion_type,
        expected_count=esperado_origem,
    )
    _renovacao_exigir_coleta_completa(itens_origem, esperado_origem, campanha_id)
    if not itens_origem:
        raise HTTPException(status_code=400, detail="Nao encontrei MLBs ativos ou programados nessa campanha para copiar.")

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
        promotion_type_destino = _renovacao_tipo_campanha(
            nova_campanha_id, campanha_destino.get("promotion_type") or campanha_destino.get("type"),
        )
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

    copia, cfg = _renovacao_copiar_mlbs_campanha(
        client_id, loja, cfg, campanha_id, nova_campanha_id,
        promotion_type, promotion_type_destino,
        verificar_destino=campanha_existente,
        expected_origem_count=esperado_origem,
        origem_preparada=(itens_origem, raw_origem),
        expected_destino_count=_renovacao_contagem_esperada(campanha_destino or {}),
    )

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
        **copia,
    }


def _renovacao_sincronizar_promocao_existente(
    client_id: str,
    loja: str,
    campanha_origem_id: str,
    campanha_destino_id: str,
    promotion_type_origem: str = "SELLER_CAMPAIGN",
    promotion_type_destino: str = "SELLER_CAMPAIGN",
    progress_callback: Optional[Callable[[dict], None]] = None,
    quantidade_origem_esperada: int | None = None,
    somente_conferir: bool = False,
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
    promotion_type_origem = _renovacao_tipo_campanha(
        campanha_origem_id, campanha_origem.get("promotion_type") or campanha_origem.get("type") or promotion_type_origem,
    )
    promotion_type_destino = _renovacao_tipo_campanha(
        campanha_destino_id, campanha_destino.get("promotion_type") or campanha_destino.get("type") or promotion_type_destino,
    )
    expected_origem_count = _renovacao_contagem_esperada(campanha_origem)
    contagens_origem_api = {"ativos": campanha_origem.get("active_count"),
        "programados": campanha_origem.get("programmed_count"), "total": expected_origem_count}
    if quantidade_origem_esperada is not None:
        if not somente_conferir and expected_origem_count is not None and expected_origem_count != quantidade_origem_esperada:
            raise HTTPException(409, f"A contagem da origem mudou de {quantidade_origem_esperada} para {expected_origem_count}. Atualize as campanhas e confira antes de copiar.")
        expected_origem_count = quantidade_origem_esperada
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
        expected_origem_count=expected_origem_count,
        expected_destino_count=_renovacao_contagem_esperada(campanha_destino),
        progress_callback=progress_callback,
        somente_conferir=somente_conferir,
    )

    _renovacao_emitir_progresso(
        progress_callback,
        "Finalizando",
        98,
        "Atualizando cache local das campanhas...",
    )
    if not somente_conferir:
        ctx._cache_invalidar_loja(client_id, loja)
    return {
        "success": True,
        "loja": loja,
        "origem": {
            "id": campanha_origem_id,
            "nome": campanha_origem.get("name") or campanha_origem.get("title") or campanha_origem_id,
        },
        "contagens_origem_api": contagens_origem_api,
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


def _renovacao_sanitizar_relatorio(valor):
    secretos = {"authorization", "access_token", "refresh_token", "cookie", "set-cookie", "password",
                "client_secret", "secret", "api_key", "cfg", "headers", "raw", "request", "response"}
    if isinstance(valor, dict):
        return {str(key): _renovacao_sanitizar_relatorio(item) for key, item in valor.items()
                if str(key).lower() not in secretos}
    if isinstance(valor, (list, tuple)):
        return [_renovacao_sanitizar_relatorio(item) for item in valor]
    if isinstance(valor, str):
        texto = re.sub(r"(?i)Bearer\s+[^\s\"',;]+", "[credencial omitida]", valor)
        texto = re.sub(r"(?i)\b(?:access_token|refresh_token|client_secret|api_key|password)\b[\"']?\s*[:=]\s*[\"']?[^\s\"',;}]+", "[credencial omitida]", texto)
        texto = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[credencial omitida]", texto)
        texto = re.sub(r"\bAPP_USR-[A-Za-z0-9_-]+", "[credencial omitida]", texto)
        texto = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[email omitido]", texto)
        texto = re.sub(r"(?i)\b[A-Z]:[\\/][^\r\n]*", "[caminho omitido]", texto)
        texto = re.sub(r"\\\\[^\\\s]+\\[^\r\n]*", "[caminho omitido]", texto)
        return re.sub(r"/(?:srv|home|root|tmp|var|etc|opt|app|workspace|usr|Users)/[^\r\n]*", "[caminho omitido]", texto)
    if isinstance(valor, float) and not math.isfinite(valor):
        return None
    return valor if valor is None or isinstance(valor, (bool, int, float)) else None


def _renovacao_salvar_conferencia(client_id: str, job_id: str, payload: dict, resultado: dict) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", job_id):
        raise ValueError("Identificador de conferencia invalido")
    pasta = os.path.join(ctx.get_tenant_path(client_id), "renovacao_auditorias")
    os.makedirs(pasta, exist_ok=True)
    relatorio = {
        "job_id": job_id, "conferido_em": dt.datetime.now(dt.timezone.utc).isoformat(),
        "loja": payload.get("loja"), "campanha_origem_id": payload.get("campanha_origem_id"),
        "campanha_destino_id": payload.get("campanha_destino_id"),
        "somente_conferir": bool(payload.get("somente_conferir")), "resultado": resultado,
    }
    fd, temporario = tempfile.mkstemp(prefix=job_id + "-", suffix=".tmp", dir=pasta)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
            json.dump(_renovacao_sanitizar_relatorio(jsonable_encoder(relatorio)), arquivo, ensure_ascii=False, allow_nan=False)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, os.path.join(pasta, job_id + ".json"))
    finally:
        if os.path.exists(temporario):
            os.unlink(temporario)


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
            quantidade_origem_esperada=payload.get("quantidade_origem_esperada"),
            somente_conferir=bool(payload.get("somente_conferir")),
        )
        resultado = _renovacao_sanitizar_relatorio(resultado)
        try:
            _renovacao_salvar_conferencia(client_id, job_id, payload, resultado)
            resultado["relatorio_id"] = job_id
        except Exception:
            resultado["aviso_relatorio"] = "A conferencia terminou, mas o relatorio nao foi salvo. Baixe o resultado nesta tela."
        completa = resultado.get("success") is True
        _renovacao_sync_job_update(
            job_id,
            status="done",
            etapa="Concluido" if completa else "Pendencias",
            percentual=100,
            progress=100,
            mensagem=("Conferencia concluida." if payload.get("somente_conferir") else "Sincronizacao concluida.") if completa else "Operacao encerrada com pendencias. Confira o relatorio.",
            result=jsonable_encoder(resultado),
            error="",
        )
    except HTTPException as exc:
        detail = _renovacao_sanitizar_relatorio(exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False))
        try:
            _renovacao_salvar_conferencia(client_id, job_id, payload, {
                "success": False, "somente_conferir": bool(payload.get("somente_conferir")), "erro": detail})
        except Exception:
            pass
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
        ctx.logger.error("[RENOVACAO ML] Falha no processamento da conferencia: %s", type(exc).__name__)
        msg = _renovacao_sanitizar_relatorio(str(exc) or "Erro ao sincronizar promocao.")
        try:
            _renovacao_salvar_conferencia(client_id, job_id, payload, {
                "success": False, "somente_conferir": bool(payload.get("somente_conferir")), "erro": msg})
        except Exception:
            pass
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
