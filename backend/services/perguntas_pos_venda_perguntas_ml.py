"""Internal slice for perguntas_pos_venda_core."""

from __future__ import annotations

from __future__ import annotations
import base64
import copy
import csv
import functools
import hashlib
import html as html_lib
import io
import json
import logging
import math
import os
import random
import re
import sqlite3
import tempfile
import threading
import time
import traceback
import unicodedata
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from backend.services.codex_turn_context import EVIDENCE_ENVELOPE_V2, normalize_evidence_envelope
from backend.modules.perguntas_pos_venda.ai import api as perguntas_agent_api
from backend.modules.perguntas_pos_venda.ai.validation import ML_PERGUNTAS_IA_V2_MODO
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.vendas_sync_progress import _corrigir_texto_mojibake


def configure_perguntas_pos_venda_perguntas_ml_runtime(runtime_module=None, peers=None):
    _configure_common = globals().get("configure_promocoes_common_runtime")
    if callable(_configure_common):
        try:
            _configure_common(runtime_module)
        except TypeError:
            _configure_common()
    runtime = bind_runtime_globals(globals(), runtime_module)
    if peers:
        globals().update(peers)
    return runtime


configure_perguntas_pos_venda_perguntas_ml_runtime()


ML_POS_VENDA_MAX_SENTENCES = 3


def _pos_venda_ia_limitar_sentencas(texto: str, limite: int = ML_POS_VENDA_MAX_SENTENCES) -> str:
    resposta = re.sub(r"\s+", " ", str(texto or "")).strip()
    if not resposta:
        return ""
    partes = [parte.strip() for parte in re.split(r"(?<=[.!?])\s+", resposta) if parte.strip()]
    return " ".join(partes[: max(1, int(limite or 1))]).strip()


def _pos_venda_ia_limpar_resposta(texto: str, limite: int | None = None) -> str:
    limite_num = int(limite or ML_POS_VENDA_DEFAULT_MAX_CHARS)
    limite_num = max(1, min(limite_num, ML_POS_VENDA_DEFAULT_MAX_CHARS))
    limite_seguro = min(limite_num, ML_POS_VENDA_LIMITE_SEGURO)
    resposta = str(texto or "").strip()
    resposta = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", resposta)
    resposta = re.sub(r"\s*```$", "", resposta)
    resposta = re.sub(r"[*_`#]+", "", resposta)
    resposta = re.sub(r"\n{3,}", "\n\n", resposta).strip()
    if len(resposta) > limite_seguro:
        resposta = resposta[: max(0, limite_seguro - 3)].rstrip() + "..."
    return resposta


def _pos_venda_ia_resposta_final_loja(texto: str, loja: str, limite: int | None = None) -> str:
    limite_num = int(limite or ML_POS_VENDA_DEFAULT_MAX_CHARS)
    limite_num = max(1, min(limite_num, ML_POS_VENDA_DEFAULT_MAX_CHARS))
    limite_seguro = min(limite_num, ML_POS_VENDA_LIMITE_SEGURO)
    assinatura = _perguntas_ia_assinatura_loja(loja)
    assinatura_curta = _perguntas_ia_assinatura_loja("")
    separador = "\n\n"
    corpo = _perguntas_ia_remover_apresentacao_sistema(texto)
    corpo = re.sub(
        r"(?is)\s*Equipe\s+.+?\s+agradece\s+(?:(?:o\s+)?seu\s+contato\.?|pelo\s+contato,\s*Precisando\s+estamos\s+[àa]\s+disposi[cç][ãa]o!)\s*$",
        "",
        corpo,
    ).strip()
    corpo = _pos_venda_ia_limpar_resposta(corpo, limite_num)
    # A assinatura obrigatoria conta como uma sentenca; o corpo fica com no
    # maximo duas para manter o rascunho completo dentro do contrato 3/340.
    corpo = _pos_venda_ia_limitar_sentencas(corpo, ML_POS_VENDA_MAX_SENTENCES - 1)
    if not corpo:
        return ""
    if len(assinatura) + len(separador) + 20 > limite_seguro and len(assinatura_curta) < len(assinatura):
        assinatura = assinatura_curta
    limite_corpo = limite_seguro - len(separador) - len(assinatura)
    if limite_corpo <= 0:
        return _pos_venda_ia_limpar_resposta(assinatura, limite_num)
    if len(corpo) > limite_corpo:
        corte = max(1, limite_corpo - 3)
        corpo = corpo[:corte].rstrip() + "..."
    return f"{corpo}{separador}{assinatura}".strip()


ML_POS_VENDA_PIPELINE_V2_MODO = "pipeline_pos_venda_v2"


ML_POS_VENDA_PIPELINE_ETAPAS = (
    (1, "receber_mensagem_e_conferir_historico"),
    (2, "buscar_dados_do_pedido"),
    (3, "buscar_dados_do_anuncio"),
    (4, "buscar_status_do_envio"),
    (5, "buscar_status_de_pagamento"),
    (6, "buscar_nota_fiscal"),
    (7, "buscar_reclamacao_ou_mediacao"),
    (8, "classificar_motivo_da_mensagem"),
    (9, "decidir_se_pode_responder_automaticamente"),
    (10, "decidir_se_precisa_consultar_regras_oficiais"),
    (11, "montar_contexto_para_ia"),
    (12, "ia_gera_resposta"),
    (13, "validador_revisa_resposta"),
    (14, "envia_resposta_ou_manda_para_humano"),
    (15, "salva_auditoria"),
)


def _ml_pos_venda_pipeline_base() -> list[dict]:
    return [{"ordem": ordem, "etapa": etapa, "status": "pendente", "detalhe": ""} for ordem, etapa in ML_POS_VENDA_PIPELINE_ETAPAS]


def _ml_pos_venda_pipeline_marcar(contexto: dict, ordem: int, status: str, detalhe: str = "", dados: Optional[dict] = None) -> None:
    etapas = contexto.setdefault("etapas_pipeline", _ml_pos_venda_pipeline_base())
    for etapa in etapas:
        if int(etapa.get("ordem") or 0) == int(ordem):
            etapa["status"] = str(status or "").strip() or "ok"
            etapa["detalhe"] = str(detalhe or "").strip()[:500]
            if dados:
                etapa["dados"] = dados
            return


def _ml_pos_venda_auditoria_path(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "ml_pos_venda_ia_auditoria.jsonl")


def _ml_pos_venda_auditoria_compactar(valor: Any, limite_texto: int = 1600):
    if isinstance(valor, dict):
        return {str(k)[:80]: _ml_pos_venda_auditoria_compactar(v, limite_texto) for k, v in list(valor.items())[:80]}
    if isinstance(valor, list):
        return [_ml_pos_venda_auditoria_compactar(item, limite_texto) for item in valor[:60]]
    if isinstance(valor, str):
        return valor[:limite_texto]
    return valor


def _ml_pos_venda_auditoria_registrar(client_id: str, evento: dict) -> str:
    audit_id = hashlib.sha1(
        f"{time.time()}:{random.random()}:{evento.get('loja')}:{evento.get('pack_id')}:{evento.get('order_id')}".encode("utf-8", errors="ignore")
    ).hexdigest()[:16]
    payload = _ml_pos_venda_auditoria_compactar(evento)
    payload["audit_id"] = audit_id
    payload["created_at"] = dt.datetime.now().isoformat(timespec="seconds")
    caminho = _ml_pos_venda_auditoria_path(client_id)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return audit_id


def _ml_pos_venda_texto_norm(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", texto)


def _ml_pos_venda_ultima_mensagem_comprador(conversa: dict) -> dict:
    conversa = conversa if isinstance(conversa, dict) else {}
    mensagens = conversa.get("messages") if isinstance(conversa.get("messages"), list) else []
    for msg in reversed(mensagens):
        if not isinstance(msg, dict):
            continue
        if str(msg.get("from_role") or "").strip().lower() == "seller":
            continue
        texto = str(msg.get("text") or "").strip()
        if texto:
            return msg
    texto = str(conversa.get("last_message_text") or "").strip()
    return {"text": texto, "date": conversa.get("last_message_date") or "", "from_role": "buyer"} if texto else {}


def _ml_pos_venda_resumir_pagamento(order: dict) -> dict:
    pagamentos = order.get("payments") if isinstance(order.get("payments"), list) else []
    resumo = []
    for pagamento in pagamentos[:8]:
        if not isinstance(pagamento, dict):
            continue
        resumo.append({
            "id": str(pagamento.get("id") or "").strip(),
            "status": str(pagamento.get("status") or "").strip(),
            "status_detail": str(pagamento.get("status_detail") or "").strip(),
            "payment_type": str(pagamento.get("payment_type") or pagamento.get("payment_type_id") or "").strip(),
            "transaction_amount": pagamento.get("transaction_amount"),
            "date_approved": pagamento.get("date_approved") or "",
        })
    statuses = {str(p.get("status") or "").strip().lower() for p in resumo if p.get("status")}
    return {
        "pagamentos": resumo,
        "status_geral": "approved" if statuses and statuses <= {"approved"} else (", ".join(sorted(statuses)) if statuses else ""),
        "todos_aprovados": bool(statuses and statuses <= {"approved"}),
    }


def _ml_pos_venda_shipping_id(order: dict) -> str:
    shipping = order.get("shipping") if isinstance(order.get("shipping"), dict) else {}
    return str(
        shipping.get("id")
        or shipping.get("shipment_id")
        or order.get("shipping_id")
        or order.get("shipment_id")
        or ""
    ).strip()


def _ml_pos_venda_status_envio_base(order: dict) -> dict:
    shipping = order.get("shipping") if isinstance(order.get("shipping"), dict) else {}
    return {
        "shipment_id": _ml_pos_venda_shipping_id(order),
        "status": str(shipping.get("status") or order.get("shipping_status") or "").strip(),
        "substatus": str(shipping.get("substatus") or "").strip(),
        "logistic_type": str(shipping.get("logistic_type") or "").strip(),
        "mode": str(shipping.get("mode") or "").strip(),
        "fonte": "order",
    }


def _ml_pos_venda_buscar_status_envio(client_id: str, loja: str, cfg: dict, order: dict) -> tuple[dict, dict]:
    resumo = _ml_pos_venda_status_envio_base(order)
    shipment_id = resumo.get("shipment_id") or ""
    if not shipment_id:
        resumo["available"] = False
        resumo["erro"] = "Pedido sem shipment_id."
        return resumo, cfg
    try:
        resp, cfg = _ml_api_request(
            client_id,
            loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/shipments/{quote_plus(shipment_id)}",
            timeout=18,
        )
        if resp.status_code != 200:
            resumo["available"] = False
            resumo["erro"] = _ml_parse_error_detail(resp, "Nao foi possivel consultar o envio.")
            return resumo, cfg
        data = resp.json() or {}
        etd = data.get("estimated_delivery_time") if isinstance(data.get("estimated_delivery_time"), dict) else {}
        tracking = data.get("tracking") if isinstance(data.get("tracking"), dict) else {}
        resumo.update({
            "available": True,
            "fonte": "shipments_api",
            "status": str(data.get("status") or resumo.get("status") or "").strip(),
            "substatus": str(data.get("substatus") or resumo.get("substatus") or "").strip(),
            "logistic_type": str(data.get("logistic_type") or resumo.get("logistic_type") or "").strip(),
            "mode": str(data.get("mode") or resumo.get("mode") or "").strip(),
            "tracking_number": str(data.get("tracking_number") or tracking.get("number") or "").strip(),
            "tracking_method": str(data.get("tracking_method") or tracking.get("method") or "").strip(),
            "date_delivered": data.get("date_delivered") or "",
            "estimated_delivery": etd.get("date") or etd.get("estimated_delivery_time") or "",
        })
    except Exception as exc:
        resumo["available"] = False
        resumo["erro"] = str(exc)[:300]
    return resumo, cfg


def _ml_pos_venda_resumir_anuncio_para_ia(item: dict, descricao: str = "") -> dict:
    item = item if isinstance(item, dict) else {}
    atributos = []
    for attr in (item.get("attributes") or [])[:25]:
        if not isinstance(attr, dict):
            continue
        atributos.append({
            "name": str(attr.get("name") or "").strip(),
            "value": str(attr.get("value_name") or attr.get("value_id") or "").strip(),
        })
    return {
        "id": str(item.get("id") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "sku": _ml_extrair_sku(item),
        "status": str(item.get("status") or "").strip(),
        "condition": str(item.get("condition") or "").strip(),
        "price": item.get("price"),
        "currency_id": item.get("currency_id") or "",
        "permalink": str(item.get("permalink") or "").strip(),
        "category_id": str(item.get("category_id") or "").strip(),
        "shipping": item.get("shipping") if isinstance(item.get("shipping"), dict) else {},
        "attributes": atributos,
        "description": _perguntas_ia_compactar_contexto(descricao, 2500),
    }


def _ml_pos_venda_buscar_dados_anuncios(client_id: str, loja: str, cfg: dict, order: dict, conversa: dict) -> tuple[list[dict], dict]:
    item_ids = _ml_pos_venda_item_ids_pedido(order)
    if not item_ids:
        for item in conversa.get("items") or []:
            if isinstance(item, dict) and str(item.get("id") or "").strip():
                item_ids.append(str(item.get("id") or "").strip())
    item_ids = list(dict.fromkeys([item_id for item_id in item_ids if item_id]))[:8]
    itens, cfg = _ml_buscar_itens_batch(client_id, loja, cfg, item_ids)
    anuncios = []
    for item in itens[:8]:
        if not isinstance(item, dict):
            continue
        descricao, cfg = _perguntas_ia_descricao_item(client_id, loja, cfg, str(item.get("id") or ""), item)
        anuncios.append(_ml_pos_venda_resumir_anuncio_para_ia(item, descricao))
    return anuncios, cfg


def _ml_pos_venda_buscar_nota_fiscal_local(client_id: str, loja: str, order_id: str, pack_id: str) -> dict:
    chaves = [str(v or "").strip() for v in (order_id, pack_id) if str(v or "").strip()]
    if not chaves:
        return {"available": False, "fonte": "local", "motivo": "pedido_sem_numero"}
    for db_path in _listar_bancos_vendas_tenant(client_id, loja):
        if not os.path.exists(db_path):
            continue
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cols = {row[1] for row in cur.execute("PRAGMA table_info(vendas)").fetchall()}
            if not cols:
                conn.close()
                continue
            colunas = [c for c in ("id_unico", "numero", "numero_nf", "nota_fiscal_id", "situacao", "devolucao", "comprador", "loja_conta") if c in cols]
            if not colunas:
                conn.close()
                continue
            termos = []
            params = []
            for col in ("numero", "id_unico"):
                if col not in cols:
                    continue
                for chave in chaves:
                    termos.append(f"CAST({col} AS TEXT) LIKE ?")
                    params.append(f"%{chave}%")
            if not termos:
                conn.close()
                continue
            sql = f"SELECT {', '.join(colunas)} FROM vendas WHERE {' OR '.join(termos)} LIMIT 10"
            rows = cur.execute(sql, params).fetchall()
            conn.close()
            for row in rows:
                data = {col: row[col] for col in colunas}
                if str(data.get("numero_nf") or data.get("nota_fiscal_id") or "").strip():
                    return {
                        "available": True,
                        "fonte": os.path.basename(db_path),
                        "numero_nf": str(data.get("numero_nf") or "").strip(),
                        "nota_fiscal_id": str(data.get("nota_fiscal_id") or "").strip(),
                        "situacao": str(data.get("situacao") or "").strip(),
                        "devolucao": str(data.get("devolucao") or "").strip(),
                    }
        except Exception as exc:
            logger.warning("[ML POS VENDA IA] Falha ao buscar NF local em %s: %s", os.path.basename(db_path), exc)
    return {"available": False, "fonte": "local", "motivo": "nao_encontrada"}


def _ml_pos_venda_buscar_reclamacao_pedido(client_id: str, loja: str, cfg: dict, order: dict, seller_id: str) -> tuple[dict, dict]:
    order_id = str(order.get("id") or "").strip()
    if not order_id:
        return {"available": False, "claims": [], "motivo": "pedido_sem_id"}, cfg
    claims = []
    consultas = [
        {"tipo": "mediacao", "label": "Mediacao", "params": {"type": "mediations", "status": "opened"}},
        {"tipo": "devolucao", "label": "Devolucao", "params": {"type": "return", "status": "opened"}},
    ]
    for consulta in consultas:
        params = {
            **consulta["params"],
            "resource": "order",
            "resource_id": order_id,
            "limit": 10,
            "offset": 0,
            "sort": "last_updated:desc",
        }
        try:
            resp, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                "GET",
                "https://api.mercadolibre.com/post-purchase/v1/claims/search",
                params=params,
                timeout=18,
            )
            if resp.status_code != 200:
                logger.warning("[ML POS VENDA IA] Falha ao buscar %s do pedido %s: %s", consulta["label"], order_id, _ml_parse_error_detail(resp, "erro"))
                continue
            data = resp.json() or {}
            lote = data.get("data") or data.get("results") or []
            lote_claims = lote if isinstance(lote, list) else []
            for claim in lote_claims:
                if not isinstance(claim, dict):
                    continue
                claim = dict(claim)
                claim["_jk_claim_tipo"] = consulta["tipo"]
                claim["_jk_claim_tipo_label"] = consulta["label"]
                claims.append(claim)
        except Exception as exc:
            logger.warning("[ML POS VENDA IA] Erro ao buscar %s do pedido %s: %s", consulta["label"], order_id, exc)
    reason_ids = [str(claim.get("reason_id") or "").strip() for claim in claims if isinstance(claim, dict)]
    motivos, cfg = _ml_mediacao_buscar_motivos_claims(client_id, loja, cfg, reason_ids)
    normalizadas = []
    for claim in claims[:10]:
        reason_id = str(claim.get("reason_id") or "").strip()
        if reason_id and motivos.get(reason_id):
            claim = dict(claim)
            claim["reason"] = motivos[reason_id]
        normalizada = _ml_mediacao_normalizar_claim(claim, order, seller_id)
        normalizadas.append({
            "claim_id": normalizada.get("claim_id") or "",
            "claim_kind": normalizada.get("claim_kind") or "",
            "claim_status": normalizada.get("claim_status") or "",
            "claim_stage": normalizada.get("claim_stage") or "",
            "claim_reason_id": normalizada.get("claim_reason_id") or "",
            "claim_reason_name": normalizada.get("claim_reason_name") or "",
            "claim_reason_detail": normalizada.get("claim_reason_detail") or "",
            "claim_last_updated": normalizada.get("claim_last_updated") or "",
        })
    return {"available": bool(normalizadas), "claims": normalizadas}, cfg


def _ml_pos_venda_decidir_regras_oficiais(classificacao: dict, envio: dict, pagamento: dict, reclamacao: dict) -> dict:
    motivo = str(classificacao.get("motivo") or "").strip()
    precisa = False
    razoes = []
    if motivo in {"cancelamento", "troca_devolucao", "defeito_garantia", "reclamacao_mediacao"}:
        precisa = True
        razoes.append("motivo envolve politica de Mercado Livre/pos-compra")
    if motivo == "entrega" and not str(envio.get("status") or "").strip():
        precisa = True
        razoes.append("status de envio indisponivel")
    if motivo == "pagamento" and not pagamento.get("todos_aprovados"):
        precisa = True
        razoes.append("pagamento nao confirmado como aprovado")
    if reclamacao.get("available"):
        precisa = True
        razoes.append("ha reclamacao/mediacao aberta")
    return {"precisa_consultar": precisa, "razoes": razoes}


def _ml_pos_venda_decidir_automatizacao(classificacao: dict, regras: dict, order: dict, envio: dict, pagamento: dict, reclamacao: dict) -> dict:
    motivos_humano = []
    motivo = str(classificacao.get("motivo") or "").strip()
    if not order:
        motivos_humano.append("pedido_nao_encontrado")
    if float(classificacao.get("confianca") or 0) < 0.70:
        motivos_humano.append("classificacao_baixa_confianca")
    if regras.get("precisa_consultar"):
        motivos_humano.append("precisa_regras_oficiais")
    if reclamacao.get("available"):
        motivos_humano.append("reclamacao_ou_mediacao_aberta")
    if motivo in {"cancelamento", "troca_devolucao", "defeito_garantia", "reclamacao_mediacao"}:
        motivos_humano.append(f"motivo_sensivel_{motivo}")
    if motivo == "pagamento" and not pagamento.get("todos_aprovados"):
        motivos_humano.append("pagamento_nao_aprovado")
    if motivo == "entrega" and not envio.get("status"):
        motivos_humano.append("envio_sem_status")
    pode = not motivos_humano
    return {
        "pode_responder_automaticamente": pode,
        "destino_sugerido": "auto" if pode else "humano",
        "motivos_humano": list(dict.fromkeys(motivos_humano)),
    }


def _ml_pos_venda_montar_contexto_pipeline(client_id: str, loja: str, cfg: dict, conversa: dict, max_chars: int | None = None) -> tuple[dict, dict]:
    contexto = {
        "pipeline_modo": ML_POS_VENDA_PIPELINE_V2_MODO,
        "etapas_pipeline": _ml_pos_venda_pipeline_base(),
        "loja": loja,
        "pack_id": conversa.get("pack_id") or "",
        "order_id": conversa.get("order_id") or "",
        "buyer_id": conversa.get("buyer_id") or "",
        "max_chars": int(max_chars or conversa.get("seller_max_message_length") or ML_POS_VENDA_DEFAULT_MAX_CHARS),
    }
    mensagens = conversa.get("messages") if isinstance(conversa.get("messages"), list) else []
    ultima = _ml_pos_venda_ultima_mensagem_comprador(conversa)
    contexto["mensagem"] = {
        "ultima_mensagem_comprador": str(ultima.get("text") or "")[:1200],
        "data": ultima.get("date") or conversa.get("last_message_date") or "",
        "historico_mensagens": _ml_pos_venda_memoria_historico(conversa),
        "total_mensagens": len(mensagens),
    }
    _ml_pos_venda_pipeline_marcar(contexto, 1, "ok", f"{len(mensagens)} mensagens carregadas")

    order, cfg = _ml_pos_venda_buscar_pedido(client_id, loja, cfg, str(conversa.get("order_id") or ""))
    contexto["pedido"] = {
        "id": str(order.get("id") or conversa.get("order_id") or "").strip(),
        "pack_id": str(order.get("pack_id") or conversa.get("pack_id") or "").strip(),
        "status": str(order.get("status") or "").strip(),
        "date_created": order.get("date_created") or "",
        "date_closed": order.get("date_closed") or "",
        "total_amount": order.get("total_amount") or order.get("paid_amount") or 0,
        "tags": order.get("tags") if isinstance(order.get("tags"), list) else [],
    }
    _ml_pos_venda_pipeline_marcar(contexto, 2, "ok" if order else "aviso", "pedido carregado" if order else "pedido nao encontrado")

    anuncios, cfg = _ml_pos_venda_buscar_dados_anuncios(client_id, loja, cfg, order, conversa)
    contexto["anuncios"] = anuncios
    _ml_pos_venda_pipeline_marcar(contexto, 3, "ok" if anuncios else "aviso", f"{len(anuncios)} anuncio(s) carregado(s)")

    envio, cfg = _ml_pos_venda_buscar_status_envio(client_id, loja, cfg, order)
    contexto["envio"] = envio
    _ml_pos_venda_pipeline_marcar(contexto, 4, "ok" if envio.get("status") else "aviso", envio.get("status") or envio.get("erro") or "sem status")

    pagamento = _ml_pos_venda_resumir_pagamento(order)
    contexto["pagamento"] = pagamento
    _ml_pos_venda_pipeline_marcar(contexto, 5, "ok" if pagamento.get("pagamentos") else "aviso", pagamento.get("status_geral") or "pagamento nao identificado")

    nota_fiscal = _ml_pos_venda_buscar_nota_fiscal_local(client_id, loja, contexto["pedido"].get("id") or "", contexto["pedido"].get("pack_id") or "")
    contexto["nota_fiscal"] = nota_fiscal
    _ml_pos_venda_pipeline_marcar(contexto, 6, "ok" if nota_fiscal.get("available") else "aviso", nota_fiscal.get("numero_nf") or nota_fiscal.get("motivo") or "nao encontrada")

    seller_id = str((cfg or {}).get("user_id") or conversa.get("seller_id") or "").strip()
    reclamacao, cfg = _ml_pos_venda_buscar_reclamacao_pedido(client_id, loja, cfg, order, seller_id)
    contexto["reclamacao_mediacao"] = reclamacao
    _ml_pos_venda_pipeline_marcar(contexto, 7, "ok" if reclamacao.get("available") else "ok", "com ocorrencia" if reclamacao.get("available") else "sem ocorrencia aberta")

    classificacao = _ml_pos_venda_classificar_motivo(conversa, reclamacao)
    contexto["classificacao"] = classificacao
    _ml_pos_venda_pipeline_marcar(contexto, 8, "ok", classificacao.get("motivo") or "")

    regras = _ml_pos_venda_decidir_regras_oficiais(classificacao, envio, pagamento, reclamacao)
    contexto["regras_oficiais"] = regras
    _ml_pos_venda_pipeline_marcar(contexto, 10, "aviso" if regras.get("precisa_consultar") else "ok", "; ".join(regras.get("razoes") or []) or "sem consulta obrigatoria")

    decisao = _ml_pos_venda_decidir_automatizacao(classificacao, regras, order, envio, pagamento, reclamacao)
    contexto["decisao_automacao"] = decisao
    _ml_pos_venda_pipeline_marcar(contexto, 9, "ok" if decisao.get("pode_responder_automaticamente") else "humano", "; ".join(decisao.get("motivos_humano") or []) or "auto permitido")

    # Dados variaveis de estoque, pedido e atendimento pertencem ao envelope da rodada,
    # nunca a memoria duravel do produto.
    contexto["memoria_sku"] = ""
    contexto["durable_memory_policy"] = "stable_reference_only"
    contexto["perguntas_anteriores_anuncio"] = _ml_pos_venda_perguntas_anuncio_chat(conversa)
    evidence_records = []
    evidence_sources = []
    for field, source in (
        ("mensagem", "mercado_livre_messages"),
        ("pedido", "mercado_livre_order"),
        ("anuncios", "mercado_livre_listing"),
        ("envio", "mercado_livre_shipping"),
        ("pagamento", "mercado_livre_payment"),
        ("nota_fiscal", "local_invoice_index"),
        ("reclamacao_mediacao", "mercado_livre_claim"),
    ):
        value = contexto.get(field)
        if value not in (None, "", [], {}):
            evidence_records.append({
                "field": field,
                "value": value,
                "store": loja,
                "source": source,
                "authority": "confirmed",
            })
            evidence_sources.append(source)
    gaps = []
    for field in ("pack_id", "order_id", "buyer_id"):
        if not str(contexto.get(field) or "").strip():
            gaps.append(field)
    if not contexto.get("mensagem"):
        gaps.append("mensagem")
    if not contexto.get("pedido") or not str((contexto.get("pedido") or {}).get("id") or "").strip():
        gaps.append("pedido")
    evidence_sufficient = bool(evidence_records and not gaps)
    contexto["evidence_envelope"] = normalize_evidence_envelope({
        "schema_version": EVIDENCE_ENVELOPE_V2,
        "status": "completed" if evidence_sufficient else ("partial" if evidence_records else "missing"),
        "records": evidence_records,
        "sources": evidence_sources,
        "gaps": gaps,
        "confidence": "high" if evidence_sufficient else ("medium" if evidence_records else "unknown"),
        "evidence_sufficient": evidence_sufficient,
        "coverage_complete": evidence_sufficient,
        "scope": {
            "task_type": "post_sale",
            "store": loja,
            "pack_id": contexto.get("pack_id") or "",
            "order_id": contexto.get("order_id") or "",
            "buyer_id": contexto.get("buyer_id") or "",
        },
    }).to_dict()
    _ml_pos_venda_pipeline_marcar(contexto, 11, "ok", "contexto estruturado montado")
    return contexto, cfg


def _ml_pos_venda_pipeline_resumo(contexto: Optional[dict]) -> dict:
    contexto = contexto if isinstance(contexto, dict) else {}
    return {
        "pipeline_modo": contexto.get("pipeline_modo") or ML_POS_VENDA_PIPELINE_V2_MODO,
        "audit_id": contexto.get("audit_id") or "",
        "etapas_pipeline": contexto.get("etapas_pipeline") or [],
        "classificacao": contexto.get("classificacao") or {},
        "decisao_automacao": contexto.get("decisao_automacao") or {},
        "decisao_final": contexto.get("decisao_final") or {},
        "validacao": contexto.get("validacao") or {},
        "regras_oficiais": contexto.get("regras_oficiais") or {},
        "envio": contexto.get("envio") or {},
        "pagamento": contexto.get("pagamento") or {},
        "nota_fiscal": contexto.get("nota_fiscal") or {},
        "reclamacao_mediacao": contexto.get("reclamacao_mediacao") or {},
    }


def _ml_pos_venda_executar_pipeline_ia(
    client_id: str,
    loja: str,
    cfg: dict,
    conversa: dict,
    max_chars: int | None = None,
) -> tuple[dict, dict]:
    contexto, cfg = _ml_pos_venda_montar_contexto_pipeline(client_id, loja, cfg, conversa, max_chars)
    resposta, model_usado = _ml_pos_venda_gerar_resposta_ia(client_id, loja, conversa, max_chars, contexto_pipeline=contexto)
    _ml_pos_venda_pipeline_marcar(contexto, 12, "ok", model_usado)
    validacao = perguntas_agent_api.validate_post_sale_response(resposta, contexto, max_chars)
    contexto["validacao"] = validacao
    _ml_pos_venda_pipeline_marcar(contexto, 13, "ok" if validacao.get("ok") else "humano", "; ".join(validacao.get("issues") or []) or "validada")
    pode_auto = bool(validacao.get("ok") and not validacao.get("requires_human_review") and (contexto.get("decisao_automacao") or {}).get("pode_responder_automaticamente"))
    motivos_decisao = list((contexto.get("decisao_automacao") or {}).get("motivos_humano") or [])
    motivos_decisao.extend(validacao.get("issues") or [])
    decisao_final = {
        "pode_enviar_automaticamente": pode_auto,
        "destino": "auto" if pode_auto else "humano",
        "motivos": list(dict.fromkeys(motivos_decisao)),
    }
    contexto["decisao_final"] = decisao_final
    _ml_pos_venda_pipeline_marcar(contexto, 14, decisao_final["destino"], "; ".join(decisao_final.get("motivos") or []) or "auto permitido")
    audit_id = _ml_pos_venda_auditoria_registrar(client_id, {
        "evento": "pos_venda_ia_pipeline",
        "loja": loja,
        "pack_id": contexto.get("pack_id") or "",
        "order_id": contexto.get("order_id") or "",
        "buyer_id": contexto.get("buyer_id") or "",
        "model": model_usado,
        "resposta": resposta,
        "contexto": contexto,
    })
    contexto["audit_id"] = audit_id
    _ml_pos_venda_pipeline_marcar(contexto, 15, "ok", audit_id)
    return {
        "resposta": resposta,
        "model": model_usado,
        "contexto_ia": contexto,
        "validacao": validacao,
        "decisao": decisao_final,
        "audit_id": audit_id,
        "pode_enviar_automaticamente": pode_auto,
    }, cfg


def _perguntas_ia_descricao_item(client_id: str, loja: str, cfg: dict, item_id: str, item: Optional[dict] = None) -> tuple[str, dict]:
    item_id = str(item_id or "").strip()
    item = item if isinstance(item, dict) else {}
    fallback = str(
        item.get("description")
        or item.get("descricao")
        or item.get("plain_text")
        or item.get("text")
        or ""
    ).strip()
    if not item_id:
        return fallback[:ML_PERGUNTAS_IA_DESCRICAO_AGENT_MAX_CHARS], cfg
    try:
        resp, cfg = _ml_api_request(
            client_id,
            loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/items/{item_id}/description",
            timeout=12,
        )
        if resp.status_code == 200:
            descricao_api = _ml_favoritos_extrair_texto_descricao(resp.json() or {})
            if descricao_api:
                return descricao_api[:ML_PERGUNTAS_IA_DESCRICAO_AGENT_MAX_CHARS], cfg
    except Exception as exc:
        logger.warning("[ML PERGUNTAS IA] Falha ao buscar descricao do item %s: %s", item_id, exc)
    return fallback[:ML_PERGUNTAS_IA_DESCRICAO_AGENT_MAX_CHARS], cfg


def _perguntas_ia_query_peca(texto: str, titulo_atual: str = "") -> str:
    base = _favoritos_ranking_texto_norm(texto)
    base = re.sub(r"https?://\S+", " ", base)
    base = re.sub(r"\b(VOCE|VOCES|VCS|TEM|VENDE|TERIA|CONSEGUE|MANDA|ENVIA|LINK|ANUNCIO|PRODUTO|PECA|OUTRA|OUTRO|PRECISO|PROCURO|QUERO|POR|FAVOR|OBRIGADO|OBRIGADA|BOM|DIA|BOA|TARDE|NOITE|DE|DA|DO|DAS|DOS|PARA|MEU|MINHA|CARRO)\b", " ", base)
    base = re.sub(r"[^A-Z0-9 ]+", " ", base)
    tokens = [tok for tok in base.split() if len(tok) >= 2]
    if len(tokens) < 2 and titulo_atual:
        tokens.extend(_favoritos_ranking_texto_norm(titulo_atual).split()[:8])
    return " ".join(dict.fromkeys(tokens))[:180]


def _perguntas_ia_score_texto(query: str, *textos: str) -> int:
    q_norm = _favoritos_ranking_texto_norm(query)
    alvo = _favoritos_ranking_texto_norm(*textos)
    if not q_norm or not alvo:
        return 0
    q_tokens = [tok for tok in q_norm.split() if len(tok) >= 2]
    if not q_tokens:
        return 0
    alvo_tokens = set(alvo.split())
    score = sum(2 for tok in q_tokens if tok in alvo_tokens)
    if q_norm and q_norm in alvo:
        score += 8
    codigos_q = set(_favoritos_ranking_extrair_codigos(query))
    codigos_alvo = set(_favoritos_ranking_extrair_codigos(*textos))
    if codigos_q and codigos_alvo:
        score += 15 * len(codigos_q & codigos_alvo)
    return score


def _perguntas_ia_buscar_cadastro_peca(client_id: str, query: str, limite: int = 8) -> list[dict]:
    cadastro_por_sku, _ = _favoritos_carregar_cadastro_por_sku(client_id)
    if not cadastro_por_sku:
        return []
    candidatos = []
    for sku, row in cadastro_por_sku.items():
        titulo = (
            _favoritos_sku_pick(row, ["nome", "produto", "produto_bling", "titulo"])
            or _favoritos_sku_pick(row, ["descriÃ§Ã£o", "descricao", "description"])
        )
        descricao = _favoritos_sku_pick(row, ["descricao", "descriÃ§Ã£o", "description"])
        marca = _favoritos_sku_pick(row, ["marca"])
        mlb_ids = _favoritos_sku_pick(row, ["mlb_ids", "mlb", "anuncios_mlb", "titulos_anuncios_mlb"])
        score = _perguntas_ia_score_texto(query, sku, titulo, descricao, marca, mlb_ids)
        if score <= 0:
            continue
        candidatos.append({
            "sku": str(sku or "").strip(),
            "titulo": _cadastro_limpar_nome(titulo) or str(titulo or "").strip()[:180],
            "descricao": str(descricao or "").strip()[:500],
            "marca": str(marca or "").strip(),
            "mlb_ids": str(mlb_ids or "").strip(),
            "score": score,
        })
    candidatos.sort(key=lambda item: int(item.get("score") or 0), reverse=True)
    return candidatos[:limite]


def _perguntas_ia_resumir_item_ml(item: dict) -> dict:
    item = item or {}
    item_id = str(item.get("id") or "").strip()
    titulo = str(item.get("title") or "").strip()
    permalink = str(item.get("permalink") or "").strip() or (f"https://produto.mercadolivre.com.br/{item_id}" if item_id else "")
    return {
        "id": item_id,
        "titulo": titulo,
        "sku": _ml_extrair_sku(item),
        "status": str(item.get("status") or "").strip(),
        "preco": item.get("price"),
        "estoque": item.get("available_quantity"),
        "link": permalink,
    }


def _perguntas_ia_buscar_anuncios_ml_peca(
    client_id: str,
    loja: str,
    cfg: dict,
    query: str,
    candidatos_cadastro: list[dict],
    limite: int = 5,
) -> tuple[list[dict], dict]:
    cfg_local = dict(cfg or {})
    user_id = str(cfg_local.get("user_id") or "").strip()
    if not user_id:
        return [], cfg

    ids: list[str] = []

    def _add_id(valor: str) -> None:
        item_id = _extrair_item_id(str(valor or "")) or str(valor or "").strip().upper().replace("-", "")
        if item_id and item_id.startswith("MLB") and item_id not in ids:
            ids.append(item_id)

    for cad in candidatos_cadastro[:5]:
        for raw in re.findall(r"MLB[- ]?\d+", str(cad.get("mlb_ids") or ""), flags=re.IGNORECASE):
            _add_id(raw)

    consultas = []
    if query:
        consultas.append({"q": query})
    for cad in candidatos_cadastro[:4]:
        sku = str(cad.get("sku") or "").strip()
        titulo = str(cad.get("titulo") or "").strip()
        if sku:
            consultas.append({"seller_sku": sku})
        if titulo:
            consultas.append({"q": titulo[:120]})

    vistos_consulta = set()
    for params_base in consultas[:10]:
        chave = json.dumps(params_base, sort_keys=True)
        if chave in vistos_consulta:
            continue
        vistos_consulta.add(chave)
        try:
            resp, cfg_local = _ml_api_request(
                client_id,
                loja,
                cfg_local,
                "GET",
                f"https://api.mercadolibre.com/users/{user_id}/items/search",
                params={**params_base, "status": "active", "limit": 20, "offset": 0},
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            data = resp.json() or {}
            for item in data.get("results") or []:
                if isinstance(item, dict):
                    _add_id(str(item.get("id") or ""))
                else:
                    _add_id(str(item or ""))
                if len(ids) >= 40:
                    break
        except Exception as exc:
            logger.warning("[ML PERGUNTAS IA] Falha ao buscar anuncio de outra peca na loja %s: %s", loja, exc)
        if len(ids) >= 40:
            break

    itens, cfg_local = _ml_buscar_itens_batch(client_id, loja, cfg_local, ids[:40])
    resultados = []
    vistos = set()
    for item in itens or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "").lower() != "active":
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id or item_id in vistos:
            continue
        score = _perguntas_ia_score_texto(query, item.get("title") or "", _ml_extrair_sku(item), item_id)
        if score <= 0 and not any(item_id in str(cad.get("mlb_ids") or "") for cad in candidatos_cadastro):
            continue
        resumo = _perguntas_ia_resumir_item_ml(item)
        resumo["score"] = score
        resultados.append(resumo)
        vistos.add(item_id)
    resultados.sort(key=lambda item: (int(item.get("score") or 0), int(item.get("estoque") or 0)), reverse=True)
    return resultados[:limite], cfg_local


def _perguntas_ia_contexto_outra_peca(
    client_id: str,
    loja: str,
    cfg: dict,
    texto_pergunta: str,
    titulo_atual: str,
) -> tuple[str, dict, dict]:
    query = _perguntas_ia_query_peca(texto_pergunta, titulo_atual)
    if not query:
        return "", cfg, {"busca_outra_peca": True, "query": ""}

    cadastro = _perguntas_ia_buscar_cadastro_peca(client_id, query, limite=8)
    anuncios, cfg = _perguntas_ia_buscar_anuncios_ml_peca(client_id, loja, cfg, query, cadastro, limite=5)
    contexto = {
        "busca_outra_peca": True,
        "query": query,
        "cadastro": cadastro,
        "anuncios_ativos_ml": anuncios,
    }

    linhas = [
        "Busca interna por outra peÃ§a solicitada pelo comprador:",
        f"- Consulta interpretada: {query}",
    ]
    if cadastro:
        linhas.append("- Produtos encontrados no cadastro:")
        for item in cadastro[:5]:
            linhas.append(f"  - SKU {item.get('sku') or '-'} | {item.get('titulo') or '-'}")
    else:
        linhas.append("- Produtos encontrados no cadastro: nenhum candidato claro.")

    if anuncios:
        linhas.append("- Anuncios ativos encontrados na conta Mercado Livre da loja:")
        for item in anuncios[:5]:
            estoque = item.get("estoque")
            estoque_txt = f" | estoque {estoque}" if estoque not in (None, "") else ""
            preco = item.get("preco")
            preco_txt = f" | preco R$ {preco}" if preco not in (None, "") else ""
            linhas.append(f"  - {item.get('titulo') or '-'} | {item.get('id') or '-'}{preco_txt}{estoque_txt} | link: {item.get('link') or '-'}")
        linhas.append("Instrucao: se responder indicando produto, use somente links da lista acima.")
    else:
        linhas.append("- Anuncios ativos encontrados na conta Mercado Livre da loja: nenhum.")
        linhas.append("Instrucao: informe de forma sincera que nao localizou anuncio ativo dessa peca na conta, sem inventar link.")

    return "\n".join(linhas), cfg, contexto


def _perguntas_ia_contexto_fallback_sanitizar_texto(value: object, limit: int) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[dado removido]", text, flags=re.I)
    text = re.sub(r"\b[A-HJ-NPR-Z0-9]{17}\b", "[identificador removido]", text, flags=re.I)
    text = re.sub(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", "[dado removido]", text)
    text = re.sub(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", "[dado removido]", text)
    text = re.sub(r"(?<!\d)(?:\+?55\s*)?\(?\d{2}\)?\s*9?\d{4}[-\s]?\d{4}(?!\d)", "[dado removido]", text)
    return re.sub(r"\s+", " ", text).strip()[: max(1, int(limit or 1))]


def _perguntas_ia_historico_anterior(pergunta: dict) -> list[dict]:
    historico = list(
        pergunta.get("buyer_question_chat")
        if isinstance(pergunta.get("buyer_question_chat"), list)
        else []
    )
    question_id = str(pergunta.get("id") or "").strip()
    texto_atual = unicodedata.normalize("NFKD", str(pergunta.get("text") or ""))
    texto_atual = "".join(char for char in texto_atual if not unicodedata.combining(char))
    texto_atual = re.sub(r"\s+", " ", texto_atual).strip().casefold()
    anteriores = []
    for evento in historico:
        if not isinstance(evento, dict):
            continue
        if question_id and str(evento.get("question_id") or "").strip() == question_id:
            continue
        anteriores.append(evento)
    if not texto_atual:
        return anteriores
    filtrados = []
    for evento in anteriores:
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        if role in {"seller", "loja", "store"}:
            filtrados.append(evento)
            continue
        texto_evento = unicodedata.normalize("NFKD", str(evento.get("text") or ""))
        texto_evento = "".join(
            char for char in texto_evento if not unicodedata.combining(char)
        )
        texto_evento = re.sub(r"\s+", " ", texto_evento).strip().casefold()
        if texto_evento != texto_atual:
            filtrados.append(evento)
    return filtrados


def _perguntas_ia_contexto_fallback_classificacao(value: object) -> dict:
    data = value if isinstance(value, dict) else {}
    continuity = data.get("continuidade") if isinstance(data.get("continuidade"), dict) else {}
    compatibility = (
        data.get("compatibilidade")
        if isinstance(data.get("compatibilidade"), dict)
        else {}
    )
    return {
        "categoria": str(data.get("categoria") or "")[:40],
        "categorias": [str(item or "")[:40] for item in (data.get("categorias") or [])[:8]],
        "continuidade": {
            "tipo": str(continuity.get("tipo") or "")[:40],
            "herdou_historico": continuity.get("herdou_historico") is True,
        },
        "compatibilidade": {
            "aplicavel": compatibility.get("aplicavel") is True,
            "target_item": _perguntas_ia_contexto_fallback_sanitizar_texto(
                compatibility.get("target_item"),
                300,
            ),
            "target_type": str(compatibility.get("target_type") or "")[:40],
        },
    }


def _perguntas_ia_gerar_resposta(
    client_id: str,
    loja: str,
    cfg: dict,
    pergunta: dict,
    item: dict,
) -> tuple[str, dict, dict]:
    item_id = str((pergunta or {}).get("item_id") or "").strip()
    question_id = str((pergunta or {}).get("id") or "").strip()
    texto_pergunta = str((pergunta or {}).get("text") or "").strip()
    titulo = str((item or {}).get("title") or "").strip()
    sku = _ml_extrair_sku(item or {})
    descricao, cfg = _perguntas_ia_descricao_item(client_id, loja, cfg, item_id, item)
    contexto = {
        "loja": loja,
        "question_id": question_id,
        "item_id": item_id,
        "titulo": titulo,
        "sku": sku,
        "permalink": str((item or {}).get("permalink") or (pergunta or {}).get("item_permalink") or "").strip(),
        "descricao": descricao,
        "descricao_chars": len(descricao or ""),
        "descricao_disponivel": bool(descricao),
        "pergunta": texto_pergunta,
        "assinatura_obrigatoria": _perguntas_ia_assinatura_loja(loja),
    }
    historico_transitorio = []
    for evento in _perguntas_ia_historico_anterior(pergunta)[-10:]:
        if not isinstance(evento, dict):
            continue
        if question_id and str(evento.get("question_id") or "").strip() == question_id:
            continue
        texto_evento = _perguntas_ia_contexto_fallback_sanitizar_texto(
            evento.get("text"),
            500,
        )
        if not texto_evento:
            continue
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        historico_transitorio.append({
            "role": "seller" if role in {"seller", "loja", "store"} else "buyer",
            "text": texto_evento[:500],
        })
    fallback_context = {
        "question": {
            "text": _perguntas_ia_contexto_fallback_sanitizar_texto(texto_pergunta, 1200)
        },
        "item": {
            "title": _perguntas_ia_contexto_fallback_sanitizar_texto(titulo, 500),
            "description": _perguntas_ia_contexto_fallback_sanitizar_texto(
                descricao,
                3500,
            ),
        },
        "history": historico_transitorio,
    }
    try:
        intencao_atendimento = _perguntas_ia_classificar_intencao(
            client_id,
            loja,
            pergunta,
            item,
        )
    except Exception as exc:
        if exc.__class__.__name__ == "PerguntasIAClassificacaoInconclusiva":
            fallback_context["classification"] = _perguntas_ia_contexto_fallback_classificacao(
                getattr(exc, "classificacao", {})
            )
            try:
                setattr(exc, "ppv_fallback_context", fallback_context)
            except Exception:
                pass
        raise
    contexto["intencao_atendimento"] = intencao_atendimento
    fallback_context["classification"] = _perguntas_ia_contexto_fallback_classificacao(
        intencao_atendimento
    )
    if intencao_atendimento.get("intencao") == "outra_peca":
        contexto_outra_peca_txt, cfg, contexto_outra_peca = _perguntas_ia_contexto_outra_peca(
            client_id,
            loja,
            cfg,
            texto_pergunta,
            titulo,
        )
    else:
        contexto_outra_peca_txt, contexto_outra_peca = "", {}
    contexto["busca_outra_peca"] = contexto_outra_peca
    descricao_prompt = _perguntas_ia_compactar_contexto(
        descricao,
        ML_PERGUNTAS_IA_DESCRICAO_PROMPT_MAX_CHARS,
    )
    contexto_outra_peca_prompt = _perguntas_ia_compactar_contexto(
        contexto_outra_peca_txt,
        ML_PERGUNTAS_IA_CONTEXTO_EXTRA_PROMPT_MAX_CHARS,
    )
    historico_anterior = _perguntas_ia_historico_anterior(pergunta)
    linhas_historico = []
    for evento in historico_anterior[-10:]:
        if not isinstance(evento, dict):
            continue
        texto_evento = str(evento.get("text") or "").strip()
        if not texto_evento:
            continue
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        rotulo = "Loja" if role in {"seller", "loja", "store"} else "Comprador"
        linhas_historico.append(f"{rotulo}: {texto_evento[:500]}")
    historico_prompt = _perguntas_ia_compactar_contexto("\n".join(linhas_historico), 1600)
    bloco_historico_prompt = f"Historico da conversa:\n{historico_prompt}\n\n" if historico_prompt else ""
    resposta_atual = _perguntas_ia_compactar_contexto(
        str((pergunta or {}).get("_resposta_atual") or ""),
        ML_RESPOSTA_PERGUNTA_MAX_CHARS,
    )
    bloco_resposta_atual = (
        "RESPOSTA ATUAL QUE O OPERADOR ESTA EDITANDO:\n"
        f"{resposta_atual}\n\n"
        "Preserve literalmente todo trecho que a orientacao do operador nao mandar alterar. "
        "Se ele pedir para repetir a resposta anterior removendo ou trocando apenas uma parte, reutilize esta resposta "
        "e faca somente a alteracao pedida.\n\n"
        if resposta_atual
        else ""
    )
    orientacao_usuario = _perguntas_ia_compactar_contexto(
        str((pergunta or {}).get("_orientacao_usuario") or ""),
        1200,
    )
    bloco_orientacao_usuario = (
        "COMANDO EDITORIAL DO OPERADOR PARA ESTA NOVA RESPOSTA:\n"
        f"{orientacao_usuario}\n\n"
        "Execute esse comando literalmente, sem explicar o que foi alterado e sem criar uma resposta diferente da solicitada. "
        "Se o operador fornecer a frase final, copie a redacao dele. Se pedir para remover, incluir, trocar ou manter um trecho, "
        "altere somente esse trecho. Nao mencione esta orientacao ao comprador. So deixe de cumpri-la se ela contradizer "
        "dados confirmados ou as regras de seguranca do Mercado Livre.\n\n"
        if orientacao_usuario
        else ""
    )
    if intencao_atendimento.get("fluxo") == "pos_venda":
        prompt = (
            "Gere um rascunho via IA de pos-venda para uma mensagem recebida no Mercado Livre. "
            "Use as orientacoes salvas no treinamento de pos-venda. "
            "Nao responda como venda, compatibilidade ou aplicacao do produto. "
            "Se o comprador relata defeito, mau funcionamento, troca ou garantia, reconheca o problema e responda primeiro com o que ja estiver confirmado. "
            "Evite solicitar dados; somente quando indispensavel, peça a evidencia minima pelo detalhe da compra. "
            "Nao invente causa tecnica, prazo, garantia, estoque ou procedimento. "
            "Nao mencione SKU, codigo interno, quantidade em estoque, preco ou nome da loja. "
            "A resposta sera enviada ao comprador, portanto seja cordial, objetiva e comercial. "
            "Nunca se apresente como IA, assistente ou JK Sistema. "
            f"Finalize exatamente com: {_perguntas_ia_assinatura_loja(loja)} "
            f"Nao use markdown. A resposta deve ter no maximo {ML_POS_VENDA_LIMITE_SEGURO} caracteres.\n\n"
            f"Loja: {loja}\n"
            f"ID da pergunta: {question_id}\n"
            f"ID do anuncio: {item_id}\n"
            f"Titulo do anuncio: {titulo or '-'}\n"
            f"Intencao classificada:\n{json.dumps(intencao_atendimento, ensure_ascii=False, default=str)}\n\n"
            f"{bloco_historico_prompt}"
            f"{bloco_orientacao_usuario}"
            f"{bloco_resposta_atual}"
            f"Pergunta do comprador:\n{texto_pergunta}"
        )
    else:
        prompt = (
            "Gere um rascunho via IA para uma pergunta recebida no Mercado Livre. "
            "Use as orientacoes salvas no treinamento de perguntas de anuncio. "
            "Use o titulo e a descricao do anuncio como contexto interno, sem repetir dados desnecessarios ao comprador. "
            "Nao invente compatibilidade, medidas, estoque, prazo, garantia ou informacoes tecnicas que nao estejam no contexto. "
            "Se o comprador perguntar por outra peca, use a busca interna por outra peca quando ela estiver presente no contexto. "
            "Somente quando a pergunta for sobre outra peca, e houver anuncio ativo encontrado dessa outra peca, informe que temos a peca e envie o link retornado. "
            "Se a pergunta for apenas sobre compatibilidade do anuncio atual, nao fale que o anuncio esta ativo e nao envie link do proprio anuncio. "
            "Quando citar o veiculo, nunca copie a pergunta inteira do comprador; extraia apenas modelo, motor, ano e cambio, ou use 'veiculo informado'. "
            "Nunca invente link; use somente links retornados na lista de anuncios ativos quando o link for realmente necessario. "
            "Nao mencione SKU, codigo interno, quantidade em estoque, preco ou nome da loja na resposta ao comprador, salvo se o comprador perguntar isso diretamente. "
            "Se a pergunta depender de dado ausente, responda pedindo a informacao necessaria de forma educada, exceto chassi em compatibilidade automotiva. "
            "Em perguntas de compatibilidade automotiva sem confirmacao objetiva, nao peça chassi, foto, anexo ou confirmacao generica de mecanico. "
            "Informe de forma condicional apenas a aplicacao e os codigos efetivamente confirmados no anuncio. "
            "Quando houver historico da conversa, responda considerando a ultima pergunta no contexto das mensagens anteriores, sem reiniciar o atendimento. "
            "A resposta sera enviada ao comprador, portanto seja cordial, objetiva e comercial. "
            "Nunca se apresente como IA, assistente ou JK Sistema. "
            f"Finalize exatamente com: {_perguntas_ia_assinatura_loja(loja)} "
            f"Nao use markdown. A resposta pode usar o detalhamento necessario e deve ter no maximo {ML_RESPOSTA_PERGUNTA_MAX_CHARS} caracteres.\n\n"
            f"Loja: {loja}\n"
            f"ID da pergunta: {question_id}\n"
            f"ID do anuncio: {item_id}\n"
            f"SKU interno (nao mencionar ao comprador): {sku or '-'}\n"
            f"Titulo do anuncio: {titulo or '-'}\n"
            f"Intencao classificada:\n{json.dumps(intencao_atendimento, ensure_ascii=False, default=str)}\n\n"
            f"Descricao do anuncio:\n{descricao_prompt or '-'}\n\n"
            f"{contexto_outra_peca_prompt + chr(10) + chr(10) if contexto_outra_peca_prompt else ''}"
            f"{bloco_historico_prompt}"
            f"{bloco_orientacao_usuario}"
            f"{bloco_resposta_atual}"
            f"Pergunta do comprador:\n{texto_pergunta}"
        )
    tipo_treinamento = "pos_venda" if intencao_atendimento.get("fluxo") == "pos_venda" else "perguntas_anuncio"
    prompt = _perguntas_ia_limitar_prompt(prompt, texto_pergunta)
    payload = IAChatRequest(
        message=prompt,
        page="Perguntas e pÃ³s venda",
        context={
            "modulo": "perguntas_pos_venda",
            "tipo": "resposta_pos_venda" if tipo_treinamento == "pos_venda" else "resposta_automatica_ml",
            "tipo_treinamento": tipo_treinamento,
            "loja": loja,
            "produto": contexto,
        },
        model=None,
    )
    model_req = _normalizar_ia_modelo_padrao(_ia_modelo_perguntas_configurado())
    payload.model = model_req
    agent_input = perguntas_agent_api.build_agent_input(client_id, loja, pergunta, item, contexto, prompt)
    try:
        agent_result = perguntas_agent_api.generate_response(client_id, agent_input)
    except Exception as exc:
        if exc.__class__.__name__ == "PerguntasIAClassificacaoInconclusiva":
            try:
                setattr(exc, "ppv_fallback_context", fallback_context)
            except Exception:
                pass
        raise
    resposta_limpa = _perguntas_ia_resposta_final_loja(agent_result.answer, loja)
    model_usado = agent_result.model
    diagnostico_ia = agent_result.diagnostics
    diagnostico_v2 = {}
    if diagnostico_ia and isinstance(diagnostico_ia[0], dict) and isinstance(diagnostico_ia[0].get("result"), dict):
        diagnostico_v2 = diagnostico_ia[0].get("result") or {}
    return resposta_limpa, cfg, {
        **contexto,
        "model": model_usado,
        "modo_ia": ML_PERGUNTAS_IA_V2_MODO,
        "diagnostico_ia": diagnostico_ia,
        "ia_requer_revisao_humana": bool(diagnostico_v2.get("needs_human_review")),
        "ia_decision": diagnostico_v2.get("decision") or "",
        "ia_categoria": diagnostico_v2.get("category") or "",
        "ia_validacao_ok": diagnostico_v2.get("validation_ok"),
        "ia_validacao_issues": diagnostico_v2.get("validation_issues") or [],
    }


def _perguntas_ia_enviar_resposta_ml(
    client_id: str,
    loja: str,
    cfg: dict,
    question_id: str,
    resposta: str,
) -> tuple[dict, dict]:
    question_id = str(question_id or "").strip()
    texto = _perguntas_ia_limpar_resposta(resposta)
    if not question_id:
        raise HTTPException(status_code=400, detail="ID da pergunta nao informado.")
    if not texto:
        raise HTTPException(status_code=400, detail="Resposta vazia.")
    resp, cfg = _ml_api_request(
        client_id,
        loja,
        cfg,
        "POST",
        "https://api.mercadolibre.com/answers",
        json={"question_id": question_id, "text": texto},
        timeout=20,
    )
    if resp.status_code not in {200, 201}:
        raise HTTPException(status_code=resp.status_code, detail=_ml_parse_error_detail(resp, "Erro ao responder pergunta no Mercado Livre"))
    try:
        data = resp.json() or {}
    except Exception:
        data = {"raw": resp.text}
    return data, cfg


def _ml_perguntas_resumir_status(perguntas: list[dict]) -> dict:
    resumo = {}
    for pergunta in perguntas or []:
        status = str((pergunta or {}).get("status") or "UNKNOWN").upper()
        resumo[status] = resumo.get(status, 0) + 1
    return resumo


def _ml_perguntas_tempo_resposta(client_id: str, loja: str, cfg: dict, seller_id: str) -> tuple[dict, dict]:
    seller_id = str(seller_id or "").strip()
    if not seller_id:
        return {"available": False, "erro": "Seller ID nao informado."}, cfg

    cache_key = f"perguntas_response_time:{client_id}:{loja}:{seller_id}"
    cached = _ml_cache_get(cache_key, ttl=3600)
    if cached is not None:
        return cached, cfg

    url = f"https://api.mercadolibre.com/users/{seller_id}/questions/response_time"
    try:
        resp, cfg = _ml_api_request(client_id, loja, cfg, "GET", url, timeout=12)
        if resp.status_code == 200:
            data = resp.json() or {}
            if not isinstance(data, dict):
                data = {}
            payload = {
                "available": True,
                "user_id": data.get("user_id") or seller_id,
                "total": data.get("total") or {},
                "weekend": data.get("weekend") or {},
                "weekdays_working_hours": data.get("weekdays_working_hours") or {},
                "weekdays_extra_hours": data.get("weekdays_extra_hours") or {},
                "updated_hint": "Mercado Livre atualiza esta informacao uma vez por dia.",
            }
            _ml_cache_set(cache_key, payload)
            return payload, cfg
        if resp.status_code == 404:
            payload = {
                "available": False,
                "user_id": seller_id,
                "erro": "O Mercado Livre ainda nao possui dados de tempo de resposta para esta conta.",
            }
            _ml_cache_set(cache_key, payload)
            return payload, cfg
        payload = {
            "available": False,
            "user_id": seller_id,
            "erro": _ml_parse_error_detail(resp, "Erro ao buscar tempo de resposta no Mercado Livre"),
        }
        return payload, cfg
    except Exception as exc:
        logger.warning("[ML PERGUNTAS] Falha ao buscar tempo de resposta seller=%s: %s", seller_id, exc)
        return {
            "available": False,
            "user_id": seller_id,
            "erro": "Nao foi possivel carregar o tempo de resposta informado pelo Mercado Livre.",
        }, cfg


def _ml_perguntas_foto_item(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    pictures = item.get("pictures") if isinstance(item.get("pictures"), list) else []
    primeira_foto = pictures[0] if pictures and isinstance(pictures[0], dict) else {}
    return str(
        item.get("secure_thumbnail")
        or item.get("thumbnail")
        or primeira_foto.get("secure_url")
        or primeira_foto.get("url")
        or ""
    ).strip()


def _ml_perguntas_nome_comprador(comprador: dict, usuario: dict | None = None) -> str:
    comprador = comprador if isinstance(comprador, dict) else {}
    usuario = usuario if isinstance(usuario, dict) else {}
    nome = str(
        comprador.get("nickname")
        or usuario.get("nickname")
        or comprador.get("name")
        or usuario.get("name")
        or ""
    ).strip()
    if nome:
        return nome
    partes = [
        str(usuario.get("first_name") or "").strip(),
        str(usuario.get("last_name") or "").strip(),
    ]
    nome_real = " ".join([parte for parte in partes if parte]).strip()
    return nome_real or str(comprador.get("id") or usuario.get("id") or "").strip()


def _ml_perguntas_buscar_usuarios(client_id: str, loja: str, cfg: dict, user_ids: list[str]) -> tuple[dict[str, dict], dict]:
    ids = [str(user_id or "").strip() for user_id in user_ids if str(user_id or "").strip()]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}, cfg

    usuarios: dict[str, dict] = {}
    ids = ids[:500]
    max_workers = min(6, max(1, len(ids)))

    def _buscar_usuario(user_id: str):
        cfg_local = dict(cfg or {})
        try:
            resp, _cfg_local = _ml_api_request(
                client_id,
                loja,
                cfg_local,
                "GET",
                f"https://api.mercadolibre.com/users/{user_id}",
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json() or {}
                if isinstance(data, dict):
                    return user_id, data
        except Exception as exc:
            logger.warning("[ML PERGUNTAS] Falha ao buscar comprador %s: %s", user_id, exc)
        return user_id, {}

    if max_workers <= 1:
        for user_id in ids:
            chave, data = _buscar_usuario(user_id)
            if data:
                usuarios[chave] = data
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futuros = [executor.submit(_buscar_usuario, user_id) for user_id in ids]
            for futuro in as_completed(futuros):
                try:
                    chave, data = futuro.result()
                    if data:
                        usuarios[chave] = data
                except Exception as exc:
                    logger.warning("[ML PERGUNTAS] Falha em busca paralela de comprador: %s", exc)
    return usuarios, cfg


def _ml_perguntas_normalizar(pergunta: dict, item_por_id: dict[str, dict], usuario_por_id: dict[str, dict] | None = None) -> dict:
    pergunta = pergunta or {}
    item_id = str(pergunta.get("item_id") or "").strip()
    item = item_por_id.get(item_id) or {}
    answer = pergunta.get("answer") if isinstance(pergunta.get("answer"), dict) else None
    comprador = pergunta.get("from") if isinstance(pergunta.get("from"), dict) else {}
    from_id = str(comprador.get("id") or "").strip() if comprador else ""
    usuario = (usuario_por_id or {}).get(from_id) or {}
    item_sku = _ml_extrair_sku(item) if item else ""
    return {
        "id": pergunta.get("id"),
        "date_created": pergunta.get("date_created"),
        "last_updated": pergunta.get("last_updated"),
        "item_id": item_id,
        "item_title": item.get("title") or "",
        "item_permalink": item.get("permalink") or "",
        "item_thumbnail": _ml_perguntas_foto_item(item),
        "item_sku": item_sku,
        "seller_id": pergunta.get("seller_id"),
        "status": pergunta.get("status"),
        "text": pergunta.get("text") or "",
        "deleted_from_listing": bool(pergunta.get("deleted_from_listing")),
        "hold": bool(pergunta.get("hold")),
        "suspected_spam": bool(pergunta.get("suspected_spam")),
        "from_id": comprador.get("id") if comprador else None,
        "buyer_id": comprador.get("id") if comprador else None,
        "buyer_name": _ml_perguntas_nome_comprador(comprador, usuario),
        "buyer_nickname": str(comprador.get("nickname") or usuario.get("nickname") or "").strip() if comprador or usuario else "",
        "answer": {
            "text": answer.get("text") or "",
            "status": answer.get("status") or "",
            "date_created": answer.get("date_created") or "",
        } if answer else None,
    }


def _ml_perguntas_completar_skus_itens(client_id: str, loja: str, cfg: dict, itens: list[dict]) -> list[dict]:
    """Completa variações apenas quando o item da pergunta veio sem SKU."""
    completos = []
    for item in itens or []:
        if not isinstance(item, dict):
            continue
        if _ml_extrair_sku(item):
            completos.append(item)
            continue
        try:
            item = _ml_favoritos_completar_variacoes_item(client_id, loja, cfg, item)
        except Exception as exc:
            logger.debug("[ML PERGUNTAS] Nao foi possivel completar SKU do item %s: %s", item.get("id"), exc)
        completos.append(item)
    return completos


def _ml_perguntas_chave_historico(pergunta: dict) -> tuple[str, str]:
    return (
        str((pergunta or {}).get("item_id") or "").strip(),
        str((pergunta or {}).get("buyer_id") or (pergunta or {}).get("from_id") or "").strip(),
    )


def _ml_perguntas_copia_historico(pergunta: dict) -> dict:
    pergunta = pergunta if isinstance(pergunta, dict) else {}
    answer = pergunta.get("answer") if isinstance(pergunta.get("answer"), dict) else None
    return {
        "id": pergunta.get("id"),
        "date_created": pergunta.get("date_created") or "",
        "last_updated": pergunta.get("last_updated") or "",
        "item_id": pergunta.get("item_id") or "",
        "item_title": pergunta.get("item_title") or "",
        "item_permalink": pergunta.get("item_permalink") or "",
        "item_thumbnail": pergunta.get("item_thumbnail") or "",
        "item_sku": pergunta.get("item_sku") or "",
        "seller_id": pergunta.get("seller_id"),
        "status": pergunta.get("status") or "",
        "text": pergunta.get("text") or "",
        "from_id": pergunta.get("from_id"),
        "buyer_id": pergunta.get("buyer_id"),
        "buyer_name": pergunta.get("buyer_name") or "",
        "buyer_nickname": pergunta.get("buyer_nickname") or "",
        "answer": {
            "text": answer.get("text") or "",
            "status": answer.get("status") or "",
            "date_created": answer.get("date_created") or "",
        } if answer else None,
    }


def _ml_perguntas_montar_chat_historico(perguntas: list[dict]) -> list[dict]:
    eventos = []
    perguntas_ordenadas = sorted(
        [pergunta for pergunta in (perguntas or []) if isinstance(pergunta, dict)],
        key=lambda item: str((item or {}).get("date_created") or (item or {}).get("last_updated") or ""),
    )
    for pergunta in perguntas_ordenadas:
        if not isinstance(pergunta, dict):
            continue
        texto = str(pergunta.get("text") or "").strip()
        if texto:
            eventos.append({
                "role": "buyer",
                "label": "Comprador",
                "text": texto,
                "date": pergunta.get("date_created") or "",
                "question_id": pergunta.get("id") or "",
            })
        answer = pergunta.get("answer") if isinstance(pergunta.get("answer"), dict) else None
        resposta = str((answer or {}).get("text") or "").strip()
        if resposta:
            eventos.append({
                "role": "seller",
                "label": "Loja",
                "text": resposta,
                "date": (answer or {}).get("date_created") or pergunta.get("last_updated") or pergunta.get("date_created") or "",
                "question_id": pergunta.get("id") or "",
            })
    return eventos


def _ml_perguntas_anexar_historico_comprador(
    client_id: str,
    loja: str,
    cfg: dict,
    seller_id: str,
    perguntas_norm: list[dict],
) -> tuple[list[dict], dict]:
    grupos_alvo: dict[str, set[str]] = {}
    for pergunta in perguntas_norm or []:
        item_id, buyer_id = _ml_perguntas_chave_historico(pergunta)
        if item_id and buyer_id:
            grupos_alvo.setdefault(item_id, set()).add(buyer_id)
    if not grupos_alvo:
        return perguntas_norm, cfg

    historico_por_chave: dict[tuple[str, str], list[dict]] = {}
    for item_id, buyer_ids in list(grupos_alvo.items())[:20]:
        try:
            resp, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                "GET",
                "https://api.mercadolibre.com/questions/search",
                params={
                    "seller_id": seller_id,
                    "item_id": item_id,
                    "api_version": 4,
                    "limit": 50,
                    "sort_fields": "date_created",
                    "sort_types": "DESC",
                },
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning("[ML PERGUNTAS] Falha ao buscar historico item=%s loja=%s status=%s", item_id, loja, resp.status_code)
                continue
            data = resp.json() or {}
            lote = data.get("questions") or data.get("results") or []
            if not isinstance(lote, list):
                continue
            for pergunta_raw in lote:
                if not isinstance(pergunta_raw, dict):
                    continue
                comprador = pergunta_raw.get("from") if isinstance(pergunta_raw.get("from"), dict) else {}
                buyer_id = str(comprador.get("id") or "").strip()
                if buyer_id not in buyer_ids:
                    continue
                pergunta_norm = _ml_perguntas_normalizar(pergunta_raw, {}, {})
                pergunta_norm["item_id"] = item_id
                historico_por_chave.setdefault((item_id, buyer_id), []).append(_ml_perguntas_copia_historico(pergunta_norm))
        except Exception as exc:
            logger.warning("[ML PERGUNTAS] Nao foi possivel montar historico item=%s loja=%s: %s", item_id, loja, exc)

    for pergunta in perguntas_norm or []:
        chave = _ml_perguntas_chave_historico(pergunta)
        historico = [
            _ml_perguntas_copia_historico(item)
            for item in (historico_por_chave.get(chave) or [])
            if isinstance(item, dict)
        ]
        pergunta_atual = _ml_perguntas_copia_historico(pergunta)
        if not historico:
            historico = [pergunta_atual]
        por_id = {}
        sem_id = []
        for item in historico + [pergunta_atual]:
            pergunta_id = str((item or {}).get("id") or "").strip()
            if pergunta_id:
                por_id[pergunta_id] = item
            else:
                sem_id.append(item)
        historico_final = sorted(
            list(por_id.values()) + sem_id,
            key=lambda item: str((item or {}).get("date_created") or (item or {}).get("last_updated") or ""),
        )
        pergunta["buyer_question_history"] = historico_final
        pergunta["buyer_question_chat"] = _ml_perguntas_montar_chat_historico(historico_final)
        pergunta["buyer_question_history_count"] = len(historico_final)
    return perguntas_norm, cfg

PEER_EXPORTS = ['_pos_venda_ia_limpar_resposta', '_pos_venda_ia_resposta_final_loja', 'ML_POS_VENDA_PIPELINE_V2_MODO', 'ML_POS_VENDA_PIPELINE_ETAPAS', '_ml_pos_venda_pipeline_base', '_ml_pos_venda_pipeline_marcar', '_ml_pos_venda_auditoria_path', '_ml_pos_venda_auditoria_compactar', '_ml_pos_venda_auditoria_registrar', '_ml_pos_venda_texto_norm', '_ml_pos_venda_ultima_mensagem_comprador', '_ml_pos_venda_resumir_pagamento', '_ml_pos_venda_shipping_id', '_ml_pos_venda_status_envio_base', '_ml_pos_venda_buscar_status_envio', '_ml_pos_venda_resumir_anuncio_para_ia', '_ml_pos_venda_buscar_dados_anuncios', '_ml_pos_venda_buscar_nota_fiscal_local', '_ml_pos_venda_buscar_reclamacao_pedido', '_ml_pos_venda_decidir_regras_oficiais', '_ml_pos_venda_decidir_automatizacao', '_ml_pos_venda_montar_contexto_pipeline', '_ml_pos_venda_pipeline_resumo', '_ml_pos_venda_executar_pipeline_ia', '_perguntas_ia_descricao_item', '_perguntas_ia_indica_busca_outra_peca', '_perguntas_ia_query_peca', '_perguntas_ia_score_texto', '_perguntas_ia_buscar_cadastro_peca', '_perguntas_ia_resumir_item_ml', '_perguntas_ia_buscar_anuncios_ml_peca', '_perguntas_ia_contexto_outra_peca', '_perguntas_ia_gerar_resposta', '_perguntas_ia_enviar_resposta_ml', '_ml_perguntas_resumir_status', '_ml_perguntas_tempo_resposta', '_ml_perguntas_foto_item', '_ml_perguntas_nome_comprador', '_ml_perguntas_buscar_usuarios', '_ml_perguntas_normalizar', '_ml_perguntas_completar_skus_itens', '_ml_perguntas_chave_historico', '_ml_perguntas_copia_historico', '_ml_perguntas_montar_chat_historico', '_ml_perguntas_anexar_historico_comprador']
PEER_EXPORTS = [name for name in PEER_EXPORTS if name != "_perguntas_ia_indica_busca_outra_peca"]
__all__ = PEER_EXPORTS + ["configure_perguntas_pos_venda_perguntas_ml_runtime"]

configure_perguntas_pos_venda_perguntas_ml_runtime()
