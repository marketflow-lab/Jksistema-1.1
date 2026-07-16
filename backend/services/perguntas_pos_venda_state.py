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
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.vendas_sync_progress import _corrigir_texto_mojibake
from ml_questions_gemini.compatibility import is_compatibility_question


def configure_perguntas_pos_venda_state_runtime(runtime_module=None, peers=None):
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


configure_perguntas_pos_venda_state_runtime()


ML_RESPOSTA_PERGUNTA_MAX_CHARS = 2000


ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO = 1900


ML_PERGUNTAS_IA_PROMPT_MAX_CHARS = 3600


ML_PERGUNTAS_IA_DESCRICAO_PROMPT_MAX_CHARS = 8000


ML_PERGUNTAS_IA_DESCRICAO_AGENT_MAX_CHARS = 12000


ML_PERGUNTAS_IA_CONTEXTO_EXTRA_PROMPT_MAX_CHARS = 900


ML_PERGUNTAS_IA_MEMORIA_SKU_MIN_BYTES = 200 * 1024


ML_PERGUNTAS_IA_MEMORIA_SKU_MAX_EVENTOS = 1000


ML_PERGUNTAS_IA_MEMORIA_SKU_PROMPT_MAX_CHARS = 4500


IA_CHAT_MESSAGE_MAX_CHARS = 4000


IA_CHAT_MESSAGE_COMPACT_TARGET_CHARS = 3900


ML_POS_VENDA_DEFAULT_MAX_CHARS = 350


ML_POS_VENDA_LIMITE_SEGURO = 340


PERGUNTAS_AUTOMACAO_INTERVALO_PADRAO_MIN = 10


PERGUNTAS_AUTOMACAO_INTERVALO_MIN = 0.25


PERGUNTAS_AUTOMACAO_INTERVALO_MAX = 1440


PERGUNTAS_AUTOMACAO_BG_LOCK = threading.Lock()


PERGUNTAS_AUTOMACAO_BG_THREAD_STARTED = False


PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS: dict[str, float] = {}


PERGUNTAS_AUTOMACAO_BG_RUNNING: set[str] = set()


PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS: dict[str, dict] = {}


PERGUNTAS_IA_MEMORIA_SKU_LOCK = threading.RLock()


def _perguntas_loja_config_path(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "perguntas_pos_venda_lojas_config.json")


def _perguntas_loja_configs_carregar(client_id: str) -> dict:
    caminho = _perguntas_loja_config_path(client_id)
    if not os.path.exists(caminho):
        return {}
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("[ML PERGUNTAS] Falha ao carregar configuracoes por loja: %s", exc)
        return {}


def _perguntas_loja_config_normalizar(config: dict | None = None) -> dict:
    config = config if isinstance(config, dict) else {}
    try:
        intervalo_minutos = float(config.get("intervalo_minutos") or PERGUNTAS_AUTOMACAO_INTERVALO_PADRAO_MIN)
    except Exception:
        intervalo_minutos = PERGUNTAS_AUTOMACAO_INTERVALO_PADRAO_MIN
    intervalo_minutos = max(PERGUNTAS_AUTOMACAO_INTERVALO_MIN, min(intervalo_minutos, PERGUNTAS_AUTOMACAO_INTERVALO_MAX))
    if float(intervalo_minutos).is_integer():
        intervalo_minutos = int(intervalo_minutos)
    pos_venda_raw = config.get("habilitar_pos_venda_automatico")
    if pos_venda_raw is None:
        pos_venda_raw = config.get("usar_pos_venda") if "usar_pos_venda" in config else config.get("responder_automaticamente")
    return {
        "responder_automaticamente": bool(config.get("responder_automaticamente")),
        # Automatico significa gerar o rascunho; publicar sempre exige humano.
        "solicitar_aprovacao": True,
        "notificar_whatsapp_aprovacoes": bool(config.get("notificar_whatsapp_aprovacoes")),
        "habilitar_pos_venda_automatico": bool(pos_venda_raw),
        "intervalo_minutos": intervalo_minutos,
    }


def _perguntas_loja_config_obter(configs_lojas: dict, nome_loja: str) -> dict | None:
    if not isinstance(configs_lojas, dict):
        return None
    if nome_loja in configs_lojas:
        return configs_lojas.get(nome_loja)

    nome_norm = _integracoes_nome_normalizado(nome_loja)
    if not nome_norm:
        return None
    for chave, config in configs_lojas.items():
        chave_texto = str(chave or "").strip()
        candidatos = [chave_texto]
        try:
            corrigido = _corrigir_texto_mojibake(chave_texto)
            if corrigido and corrigido not in candidatos:
                candidatos.append(corrigido)
        except Exception:
            pass
        if any(_integracoes_nome_normalizado(candidato) == nome_norm for candidato in candidatos):
            return config
    return None


def _perguntas_loja_config_salvar(
    client_id: str,
    loja: str,
    responder_automaticamente: bool,
    solicitar_aprovacao: bool,
    notificar_whatsapp_aprovacoes: bool,
    habilitar_pos_venda_automatico: bool,
    intervalo_minutos: float | None = None,
) -> dict:
    nome_loja = str(loja or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    configs = _perguntas_loja_configs_carregar(client_id)
    intervalo_cfg = _perguntas_loja_config_normalizar({"intervalo_minutos": intervalo_minutos})
    payload = {
        "responder_automaticamente": bool(responder_automaticamente),
        "solicitar_aprovacao": True,
        "notificar_whatsapp_aprovacoes": bool(notificar_whatsapp_aprovacoes),
        "habilitar_pos_venda_automatico": bool(habilitar_pos_venda_automatico),
        "intervalo_minutos": intervalo_cfg["intervalo_minutos"],
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    configs[nome_loja] = payload
    caminho = _perguntas_loja_config_path(client_id)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as fh:
        json.dump(configs, fh, ensure_ascii=False, indent=2)
    return _perguntas_loja_config_normalizar(payload) | {"updated_at": payload["updated_at"]}


def _perguntas_ia_state_path(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "perguntas_pos_venda_ia_state.json")


def _perguntas_ia_aprovacoes_path(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "perguntas_pos_venda_ia_aprovacoes.json")


def _ml_questions_v2_webhook_events_path(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "ml_questions_v2_webhook_events.json")


def _perguntas_ia_ler_json(caminho: str, fallback):
    if not os.path.exists(caminho):
        return fallback
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if data is not None else fallback
    except Exception as exc:
        logger.warning("[ML PERGUNTAS IA] Falha ao ler %s: %s", os.path.basename(caminho), exc)
        return fallback


def _perguntas_ia_salvar_json(caminho: str, payload) -> None:
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def _perguntas_ia_state_carregar(client_id: str) -> dict:
    data = _perguntas_ia_ler_json(_perguntas_ia_state_path(client_id), {})
    return data if isinstance(data, dict) else {}


def _perguntas_ia_state_salvar(client_id: str, data: dict) -> None:
    _perguntas_ia_salvar_json(_perguntas_ia_state_path(client_id), data if isinstance(data, dict) else {})


def _perguntas_ia_aprovacoes_carregar(client_id: str) -> list[dict]:
    data = _perguntas_ia_ler_json(_perguntas_ia_aprovacoes_path(client_id), [])
    return data if isinstance(data, list) else []


def _perguntas_ia_aprovacoes_salvar(client_id: str, data: list[dict]) -> None:
    _perguntas_ia_salvar_json(_perguntas_ia_aprovacoes_path(client_id), data if isinstance(data, list) else [])


def _perguntas_ia_aprovacao_id(loja: str, question_id: str) -> str:
    base = f"{loja}:{question_id}".encode("utf-8", errors="ignore")
    return hashlib.sha1(base).hexdigest()[:16]


def _pos_venda_ia_aprovacao_id(loja: str, pack_id: str, message_id: str = "") -> str:
    base = f"pos_venda:{loja}:{pack_id}:{message_id}".encode("utf-8", errors="ignore")
    return hashlib.sha1(base).hexdigest()[:16]


def _perguntas_ia_marcar_processada(state: dict, loja: str, question_id: str, status: str) -> None:
    nome_loja = str(loja or "").strip()
    qid = str(question_id or "").strip()
    if not nome_loja or not qid:
        return
    loja_state = state.setdefault(nome_loja, {})
    processadas = loja_state.setdefault("processadas", {})
    processadas[qid] = {
        "status": status,
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if len(processadas) > 1000:
        for chave in list(processadas.keys())[:-800]:
            processadas.pop(chave, None)


def _perguntas_ia_ja_processada(state: dict, loja: str, question_id: str) -> bool:
    return str(question_id or "").strip() in (
        ((state or {}).get(str(loja or "").strip()) or {}).get("processadas") or {}
    )


def _perguntas_ia_aprovacao_pendente(aprovacoes: list[dict], loja: str, question_id: str) -> Optional[dict]:
    qid = str(question_id or "").strip()
    nome_loja = str(loja or "").strip()
    for item in aprovacoes or []:
        if (
            isinstance(item, dict)
            and str(item.get("question_id") or "").strip() == qid
            and str(item.get("loja") or "").strip() == nome_loja
            and str(item.get("status") or "pending") == "pending"
        ):
            return item
    return None


def _perguntas_ia_resolver_aprovacao(approval: dict, status: str, motivo: str, resposta: str = "") -> bool:
    if not isinstance(approval, dict) or str(approval.get("status") or "pending") != "pending":
        return False
    approval["status"] = str(status or "answered_elsewhere").strip() or "answered_elsewhere"
    approval["resolved_at"] = dt.datetime.now().isoformat(timespec="seconds")
    approval["resolved_reason"] = str(motivo or "respondida_por_outro_fluxo").strip()
    if resposta:
        approval["resposta_enviada"] = str(resposta or "").strip()
    return True


def _perguntas_ia_resolver_aprovacoes_pendentes(
    client_id: str,
    loja: str,
    *,
    question_id: str = "",
    pack_id: str = "",
    status: str = "answered_elsewhere",
    motivo: str = "respondida_por_outro_fluxo",
    resposta: str = "",
) -> list[dict]:
    nome_loja = str(loja or "").strip()
    qid = str(question_id or "").strip()
    pack = str(pack_id or "").strip()
    if not nome_loja or (not qid and not pack):
        return []
    aprovacoes = _perguntas_ia_aprovacoes_carregar(client_id)
    resolvidas = []
    mudou = False
    for approval in aprovacoes:
        if not isinstance(approval, dict) or str(approval.get("status") or "pending") != "pending":
            continue
        if str(approval.get("loja") or "").strip() != nome_loja:
            continue
        corresponde = bool(qid and str(approval.get("question_id") or "").strip() == qid)
        corresponde = corresponde or bool(pack and str(approval.get("pack_id") or "").strip() == pack)
        if not corresponde:
            continue
        if _perguntas_ia_resolver_aprovacao(approval, status, motivo, resposta):
            resolvidas.append(dict(approval))
            mudou = True
    if mudou:
        _perguntas_ia_aprovacoes_salvar(client_id, aprovacoes)
    return resolvidas


def _perguntas_ia_pergunta_respondida_ml(client_id: str, loja: str, cfg: dict, question_id: str) -> tuple[bool, dict, dict]:
    qid = str(question_id or "").strip()
    if not qid:
        return False, {}, cfg
    resp, cfg = _ml_api_request(
        client_id,
        loja,
        cfg,
        "GET",
        f"https://api.mercadolibre.com/questions/{quote_plus(qid)}",
        timeout=12,
    )
    if resp.status_code >= 400:
        return False, {}, cfg
    pergunta = resp.json() or {}
    if not isinstance(pergunta, dict):
        return False, {}, cfg
    status = str(pergunta.get("status") or "").strip().upper()
    answer = pergunta.get("answer") if isinstance(pergunta.get("answer"), dict) else {}
    respondida = bool(str(answer.get("text") or "").strip()) or status == "ANSWERED"
    return respondida, pergunta, cfg


def _ml_pos_venda_conversa_respondida_pela_loja(conversa: dict) -> bool:
    if str((conversa or {}).get("last_message_role") or "").strip().lower() == "loja":
        return True
    mensagens = (conversa or {}).get("messages") if isinstance((conversa or {}).get("messages"), list) else []
    for msg in reversed(mensagens):
        if not isinstance(msg, dict):
            continue
        if str(msg.get("from_role") or "").strip().lower() == "seller":
            return True
        if str(msg.get("from_role") or "").strip():
            return False
    return False


def _perguntas_ia_limpar_resposta(texto: str) -> str:
    resposta = str(texto or "").strip()
    resposta = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", resposta)
    resposta = re.sub(r"\s*```$", "", resposta)
    resposta = re.sub(r"\n{3,}", "\n\n", resposta).strip()
    limite = min(ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO, ML_RESPOSTA_PERGUNTA_MAX_CHARS)
    if len(resposta) > limite:
        resposta = resposta[: max(0, limite - 3)].rstrip() + "..."
    return resposta


def _perguntas_ia_assinatura_loja(loja: str) -> str:
    nome_loja = re.sub(r"\s+", " ", str(loja or "").strip())
    if nome_loja:
        return f"Equipe {nome_loja} agradece o seu contato."
    return "Equipe da loja agradece o seu contato."


def _perguntas_ia_remover_apresentacao_sistema(texto: str) -> str:
    resposta = str(texto or "").strip()
    if not resposta:
        return ""
    resposta = re.sub(
        r"(?is)^\s*(?:ol[aá][!,.\s]*)?(?:eu\s+)?sou\s+(?:o|a|um|uma)?\s*(?:assistente|ia|intelig[êe]ncia\s+artificial)[^.!?\n]*(?:jk\s*sistema|sistema)?[.!?]?\s*",
        "",
        resposta,
    ).strip()
    resposta = re.sub(
        r"(?is)^\s*(?:ol[aá][!,.\s]*)?estou\s+(?:aqui\s+)?(?:para|pra)\s+ajudar[.!?]?\s*",
        "",
        resposta,
    ).strip()
    linhas = []
    for linha in resposta.splitlines():
        linha_norm = _favoritos_normalizar_sem_acentos(linha)
        if "jk sistema" in linha_norm and any(termo in linha_norm for termo in ("assistente", " ia ", "inteligencia artificial", "sistema")):
            continue
        if any(termo in linha_norm for termo in ("sou o assistente", "sou a assistente", "sou uma ia", "sou um assistente")):
            continue
        linhas.append(linha)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(linhas)).strip()


def _perguntas_ia_resposta_final_loja(resposta: str, loja: str) -> str:
    assinatura = _perguntas_ia_assinatura_loja(loja)
    corpo = _perguntas_ia_limpar_resposta(_perguntas_ia_remover_apresentacao_sistema(resposta))
    corpo = re.sub(
        r"(?is)\s*Equipe\s+.+?\s+agradece\s+(?:o\s+)?seu\s+contato\.?\s*$",
        "",
        corpo,
    ).strip()
    if not corpo:
        return ""
    limite = min(ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO, ML_RESPOSTA_PERGUNTA_MAX_CHARS)
    separador = "\n\n"
    total = len(corpo) + len(separador) + len(assinatura)
    if total > limite:
        limite_corpo = max(40, limite - len(separador) - len(assinatura) - 3)
        corpo = corpo[:limite_corpo].rstrip() + "..."
    return _perguntas_ia_limpar_resposta(f"{corpo}{separador}{assinatura}")


class PerguntasIARespostaIndisponivel(RuntimeError):
    pass


def _perguntas_ia_resposta_fallback_invalida(texto: str) -> bool:
    normalizado = _favoritos_normalizar_sem_acentos(texto)
    if not normalizado:
        return False
    sinais_fallback = (
        "instabilidade momentanea" in normalizado,
        "reformule em uma frase curta" in normalizado,
        "gerar a resposta pela vertex ai" in normalizado,
        "gerar a resposta completa agora" in normalizado,
    )
    return any(sinais_fallback)


ML_PERGUNTAS_IA_INTENCOES = {
    "duvida_produto",
    "compatibilidade",
    "outra_peca",
    "preco_estoque",
    "pos_venda_defeito",
    "troca_garantia",
    "entrega",
    "cancelamento",
    "reclamacao",
    "nao_entendi",
}


ML_PERGUNTAS_IA_INTENCOES_POS_VENDA = {
    "pos_venda_defeito",
    "troca_garantia",
    "entrega",
    "cancelamento",
    "reclamacao",
}


def _perguntas_ia_intencao_fluxo(intencao: str) -> str:
    return "pos_venda" if str(intencao or "").strip() in ML_PERGUNTAS_IA_INTENCOES_POS_VENDA else "perguntas_anuncio"


def _perguntas_ia_intencao_heuristica(pergunta: dict, item: dict | None = None) -> dict:
    pergunta = pergunta if isinstance(pergunta, dict) else {}
    item = item if isinstance(item, dict) else {}
    historico = pergunta.get("buyer_question_chat") if isinstance(pergunta.get("buyer_question_chat"), list) else []
    textos = [str(pergunta.get("text") or "")]
    for evento in historico[-6:]:
        if not isinstance(evento, dict):
            continue
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        if role in {"seller", "loja", "store"}:
            continue
        textos.append(str(evento.get("text") or ""))
    texto = _favoritos_normalizar_sem_acentos(" ".join(textos))

    sinais_compra = (
        "comprei", "compre", "minha compra", "pedido", "recebi", "chegou", "produto chegou",
        "efetuei a compra", "numero da compra", "n da compra", "número da compra",
    )
    sinais_defeito = (
        "defeito", "problema", "nao funciona", "nao funcionou", "parou", "apagando", "apaga",
        "queimou", "falhando", "mal funcionamento", "nao acende", "nao liga", "quebrou",
        "veio ruim", "veio com defeito", "uma fica", "uma nao", "um fica",
    )
    sinais_troca = (
        "trocar", "troca", "garantia", "devolver", "devolucao", "reembolso", "assistencia",
        "quero devolver", "quero trocar", "acionar garantia",
    )
    sinais_entrega = (
        "entrega", "rastreio", "rastreamento", "correio", "transportadora", "nao chegou",
        "atrasou", "rota de entrega",
    )
    sinais_compat_contextuais = (
        "da certo", "veiculo", "chassi", " vin ", "corolla", "civic", "focus", "hilux",
    )
    tem_compatibilidade = bool(
        is_compatibility_question(texto)
        or any(sinal in texto for sinal in sinais_compat_contextuais)
    )
    sinais_outra = (
        "voces tem", "voce tem", "tem essa peca", "tem o", "tem a", "manda link", "envia link",
        "outro lado", "lado esquerdo", "lado direito", "outra peca", "acabamento", "complemento",
    )
    sinais_preco_estoque = ("valor", "preco", "quanto", "tem estoque", "disponivel", "pronta entrega")

    tem_compra = any(s in texto for s in sinais_compra)
    tem_defeito = any(s in texto for s in sinais_defeito)
    tem_troca = any(s in texto for s in sinais_troca)
    if tem_compra and (tem_defeito or tem_troca):
        intencao = "pos_venda_defeito" if tem_defeito else "troca_garantia"
        return {
            "intencao": intencao,
            "fluxo": "pos_venda",
            "confianca": 0.96,
            "motivo": "mensagem indica compra ja realizada com defeito/troca",
            "acao": "responder como pos-venda, pedir dados/fotos e orientar atendimento pela compra",
            "usar_busca_web": False,
            "usar_mercado_livre_anuncio": False,
            "usar_bling": False,
            "source": "heuristica",
        }
    if tem_compra and any(s in texto for s in sinais_entrega):
        return {
            "intencao": "entrega",
            "fluxo": "pos_venda",
            "confianca": 0.92,
            "motivo": "mensagem indica compra/pedido com assunto de entrega",
            "acao": "responder como pos-venda de entrega",
            "usar_busca_web": False,
            "usar_mercado_livre_anuncio": False,
            "usar_bling": False,
            "source": "heuristica",
        }
    if tem_troca and not tem_compatibilidade:
        return {
            "intencao": "troca_garantia",
            "fluxo": "pos_venda",
            "confianca": 0.88,
            "motivo": "mensagem pede troca, garantia ou devolucao",
            "acao": "responder como pos-venda",
            "usar_busca_web": False,
            "usar_mercado_livre_anuncio": False,
            "usar_bling": False,
            "source": "heuristica",
        }
    if any(s in texto for s in sinais_outra):
        return {
            "intencao": "outra_peca",
            "fluxo": "perguntas_anuncio",
            "confianca": 0.80,
            "motivo": "comprador parece procurar outra peca ou variacao",
            "acao": "buscar somente outra peca quando necessario",
            "usar_busca_web": True,
            "usar_mercado_livre_anuncio": True,
            "usar_bling": True,
            "source": "heuristica",
        }
    if tem_compatibilidade:
        return {
            "intencao": "compatibilidade",
            "fluxo": "perguntas_anuncio",
            "confianca": 0.78,
            "motivo": "mensagem pergunta compatibilidade/aplicacao",
            "acao": "responder compatibilidade com cautela",
            "usar_busca_web": True,
            "usar_mercado_livre_anuncio": True,
            "usar_bling": True,
            "source": "heuristica",
        }
    if any(s in texto for s in sinais_preco_estoque):
        return {
            "intencao": "preco_estoque",
            "fluxo": "perguntas_anuncio",
            "confianca": 0.72,
            "motivo": "mensagem pergunta preco/estoque/disponibilidade",
            "acao": "responder somente se houver dado seguro",
            "usar_busca_web": False,
            "usar_mercado_livre_anuncio": True,
            "usar_bling": True,
            "source": "heuristica",
        }
    return {
        "intencao": "duvida_produto",
        "fluxo": "perguntas_anuncio",
        "confianca": 0.55,
        "motivo": "sem sinais fortes de pos-venda ou compatibilidade",
        "acao": "responder a duvida do produto com os dados disponiveis",
        "usar_busca_web": True,
        "usar_mercado_livre_anuncio": True,
        "usar_bling": True,
        "source": "heuristica",
    }


def _perguntas_ia_json_obj(texto: str) -> dict:
    bruto = str(texto or "").strip()
    if not bruto:
        return {}
    blocos = re.findall(r"```(?:json)?\s*(.*?)\s*```", bruto, flags=re.DOTALL | re.IGNORECASE)
    candidatos = [item.strip() for item in blocos if item.strip()]
    candidatos.append(bruto)
    for candidato in candidatos:
        inicio = candidato.find("{")
        fim = candidato.rfind("}")
        if inicio < 0 or fim <= inicio:
            continue
        trecho = candidato[inicio:fim + 1]
        try:
            data = json.loads(trecho)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _perguntas_ia_intencao_normalizar(data: object, fallback: Optional[dict] = None) -> dict:
    fallback = fallback if isinstance(fallback, dict) else {}
    data = data if isinstance(data, dict) else {}
    intencao = str(data.get("intencao") or data.get("intent") or fallback.get("intencao") or "duvida_produto").strip().lower()
    intencao = intencao.replace("-", "_").replace(" ", "_")
    aliases = {
        "defeito": "pos_venda_defeito",
        "problema_produto": "pos_venda_defeito",
        "mal_funcionamento": "pos_venda_defeito",
        "garantia": "troca_garantia",
        "troca": "troca_garantia",
        "devolucao": "troca_garantia",
        "devolução": "troca_garantia",
        "prazo_entrega": "entrega",
        "duvida": "duvida_produto",
        "produto": "duvida_produto",
    }
    intencao = aliases.get(intencao, intencao)
    if intencao not in ML_PERGUNTAS_IA_INTENCOES:
        intencao = str(fallback.get("intencao") or "duvida_produto")
    fluxo = str(data.get("fluxo") or fallback.get("fluxo") or _perguntas_ia_intencao_fluxo(intencao)).strip().lower()
    if intencao in ML_PERGUNTAS_IA_INTENCOES_POS_VENDA:
        fluxo = "pos_venda"
    elif fluxo not in {"pos_venda", "perguntas_anuncio"}:
        fluxo = "perguntas_anuncio"
    try:
        confianca = float(str(data.get("confianca") or data.get("confidence") or fallback.get("confianca") or 0).replace(",", "."))
    except Exception:
        confianca = 0.0
    confianca = max(0.0, min(confianca, 1.0))

    def _bool_campo(chave: str, default: bool) -> bool:
        valor = data[chave] if chave in data else fallback.get(chave, default)
        if isinstance(valor, bool):
            return valor
        texto = str(valor or "").strip().lower()
        if texto in {"0", "false", "nao", "não", "off", "no"}:
            return False
        if texto in {"1", "true", "sim", "on", "yes"}:
            return True
        return bool(default)

    return {
        "intencao": intencao,
        "fluxo": fluxo,
        "confianca": confianca,
        "motivo": str(data.get("motivo") or data.get("reason") or fallback.get("motivo") or "").strip()[:500],
        "acao": str(data.get("acao") or data.get("action") or fallback.get("acao") or "").strip()[:500],
        "usar_busca_web": _bool_campo("usar_busca_web", fluxo != "pos_venda"),
        "usar_mercado_livre_anuncio": _bool_campo("usar_mercado_livre_anuncio", fluxo != "pos_venda"),
        "usar_bling": _bool_campo("usar_bling", fluxo != "pos_venda"),
        "source": str(data.get("source") or fallback.get("source") or "").strip()[:80],
    }


def _perguntas_ia_classificar_intencao(client_id: str, loja: str, pergunta: dict, item: dict) -> dict:
    heuristica = _perguntas_ia_intencao_heuristica(pergunta, item)
    historico = pergunta.get("buyer_question_chat") if isinstance(pergunta.get("buyer_question_chat"), list) else []
    mensagens = []
    for evento in historico[-8:]:
        if not isinstance(evento, dict):
            continue
        texto = str(evento.get("text") or "").strip()
        if not texto:
            continue
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        mensagens.append({
            "role": "seller" if role in {"seller", "loja", "store"} else "buyer",
            "text": texto[:500],
        })
    entrada = {
        "loja": loja,
        "question_id": pergunta.get("id") or "",
        "item_id": pergunta.get("item_id") or "",
        "pergunta_atual": pergunta.get("text") or "",
        "historico": mensagens,
        "titulo_anuncio": (item or {}).get("title") or pergunta.get("item_title") or "",
        "sku": _ml_extrair_sku(item or {}) or pergunta.get("item_sku") or "",
        "heuristica": heuristica,
    }
    prompt = (
        "Classifique a intencao da ultima mensagem do comprador do Mercado Livre. "
        "Use o historico apenas para entender continuidade, mas classifique a ultima mensagem. "
        "Se o comprador diz que ja comprou, recebeu, quer trocar, relata defeito, problema, item apagando, quebrado, nao funciona, entrega ou garantia, classifique como pos-venda. "
        "Nao confunda relato de defeito pos-compra com compatibilidade do produto. "
        "Retorne somente JSON valido, sem markdown, com estes campos: "
        "intencao, fluxo, confianca, motivo, acao, usar_busca_web, usar_mercado_livre_anuncio, usar_bling. "
        "intencao deve ser uma de: duvida_produto, compatibilidade, outra_peca, preco_estoque, pos_venda_defeito, troca_garantia, entrega, cancelamento, reclamacao, nao_entendi. "
        "fluxo deve ser perguntas_anuncio ou pos_venda.\n\n"
        f"Dados:\n{json.dumps(entrada, ensure_ascii=False, default=str)[:6000]}"
    )
    model_req = _normalizar_ia_modelo_padrao(_ia_modelo_perguntas_configurado())
    payload = IAChatRequest(
        message=prompt,
        page="Perguntas e pós venda",
        context={
            "modulo": "perguntas_pos_venda",
            "tipo": "classificacao_intencao_perguntas_ml",
            "desativar_recursos_chat": True,
            "desativar_busca_web_chat": True,
            "modo_rapido_sidebar": True,
            "loja": loja,
        },
        model=model_req,
    )
    perf_t0 = time.perf_counter()
    try:
        resposta, model_usado = _ia_agent_perguntas_chamar_modelo(client_id, payload, model_req)
        data = _perguntas_ia_json_obj(resposta)
        classificada = _perguntas_ia_intencao_normalizar(data, heuristica)
        classificada["model"] = model_usado
        classificada["source"] = "ia"
        if heuristica.get("fluxo") == "pos_venda" and float(heuristica.get("confianca") or 0) >= 0.90 and classificada.get("fluxo") != "pos_venda":
            classificada = {**heuristica, "source": "heuristica_sobrepos_ia", "model": model_usado}
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            {
                "question": {"id": pergunta.get("id") or "", "item_id": pergunta.get("item_id") or ""},
                "item": {"id": (item or {}).get("id") or pergunta.get("item_id") or "", "seller_sku": _ml_extrair_sku(item or {})},
            },
            "classificacao_intencao",
            time.perf_counter() - perf_t0,
            status="ok",
            intencao=classificada.get("intencao"),
            fluxo=classificada.get("fluxo"),
            confianca=classificada.get("confianca"),
            source=classificada.get("source"),
            modelo=classificada.get("model"),
        )
        return classificada
    except Exception as exc:
        fallback = _perguntas_ia_intencao_normalizar(heuristica)
        fallback["source"] = "heuristica_fallback"
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            {
                "question": {"id": pergunta.get("id") or "", "item_id": pergunta.get("item_id") or ""},
                "item": {"id": (item or {}).get("id") or pergunta.get("item_id") or "", "seller_sku": _ml_extrair_sku(item or {})},
            },
            "classificacao_intencao",
            time.perf_counter() - perf_t0,
            status="erro",
            erro=type(exc).__name__,
            intencao=fallback.get("intencao"),
            fluxo=fallback.get("fluxo"),
            source=fallback.get("source"),
        )
        return fallback


def _perguntas_ia_intencao_agent(agent_input: dict) -> dict:
    agent_input = agent_input if isinstance(agent_input, dict) else {}
    intent = agent_input.get("intent") if isinstance(agent_input.get("intent"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    if not intent and isinstance(context.get("intencao_atendimento"), dict):
        intent = context.get("intencao_atendimento")
    return _perguntas_ia_intencao_normalizar(intent)


def _perguntas_ia_fluxo_pos_venda(agent_input: dict) -> bool:
    return _perguntas_ia_intencao_agent(agent_input).get("fluxo") == "pos_venda"


def _perguntas_ia_compactar_contexto(texto: str, limite: int) -> str:
    texto = str(texto or "").strip()
    if not texto:
        return ""
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto).strip()
    limite = max(120, int(limite or 120))
    if len(texto) <= limite:
        return texto
    corte = texto[: max(0, limite - 80)].rstrip()
    ultimo_ponto = max(corte.rfind(". "), corte.rfind("\n"), corte.rfind("; "))
    if ultimo_ponto >= int(limite * 0.45):
        corte = corte[: ultimo_ponto + 1].rstrip()
    return corte + "\n[Contexto reduzido automaticamente para caber na IA.]"


def _perguntas_ia_limitar_prompt(prompt: str, pergunta: str) -> str:
    prompt = str(prompt or "").strip()
    if len(prompt) <= ML_PERGUNTAS_IA_PROMPT_MAX_CHARS:
        return prompt
    pergunta = str(pergunta or "").strip()
    sufixo = f"\n\nPergunta do comprador:\n{pergunta}" if pergunta else ""
    margem = 120
    limite_prefixo = max(800, ML_PERGUNTAS_IA_PROMPT_MAX_CHARS - len(sufixo) - margem)
    prefixo = prompt[:limite_prefixo].rstrip()
    return (
        prefixo
        + "\n\n[Prompt reduzido automaticamente: descricao/contexto muito longos.]"
        + sufixo
    )[:ML_PERGUNTAS_IA_PROMPT_MAX_CHARS]


def _perguntas_ia_memoria_sku_limite_bytes() -> int:
    bruto = (
        os.getenv("ML_PERGUNTAS_IA_MEMORIA_SKU_MAX_BYTES")
        or os.getenv("ML_PERGUNTAS_IA_MEMORIA_SKU_BYTES")
        or ""
    )
    try:
        valor = int(float(str(bruto or ML_PERGUNTAS_IA_MEMORIA_SKU_MIN_BYTES).replace(",", ".")))
    except Exception:
        valor = ML_PERGUNTAS_IA_MEMORIA_SKU_MIN_BYTES
    return max(ML_PERGUNTAS_IA_MEMORIA_SKU_MIN_BYTES, valor)


def _perguntas_ia_memoria_sku_normalizar(valor: object) -> str:
    texto = _normalizar_sku_mes(str(valor or "").strip())
    texto = re.sub(r"\s+", " ", texto).strip()[:90]
    return texto


def _perguntas_ia_memoria_sku_de_fontes(
    *,
    sku: object = "",
    agent_input: Optional[dict] = None,
    pergunta: Optional[dict] = None,
    item: Optional[dict] = None,
    contexto: Optional[dict] = None,
    approval: Optional[dict] = None,
) -> str:
    agent_input = agent_input if isinstance(agent_input, dict) else {}
    pergunta = pergunta if isinstance(pergunta, dict) else {}
    item = item if isinstance(item, dict) else {}
    contexto = contexto if isinstance(contexto, dict) else {}
    approval = approval if isinstance(approval, dict) else {}
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    agent_item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    agent_context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    produto = approval.get("produto") if isinstance(approval.get("produto"), dict) else {}
    for candidato in (
        sku,
        contexto.get("sku"),
        contexto.get("item_sku"),
        item.get("seller_sku"),
        item.get("sku"),
        pergunta.get("item_sku"),
        agent_context.get("sku"),
        agent_context.get("item_sku"),
        agent_item.get("seller_sku"),
        agent_item.get("sku"),
        question.get("item_sku"),
        approval.get("sku"),
        approval.get("item_sku"),
        approval.get("seller_sku"),
        produto.get("sku"),
        produto.get("item_sku"),
    ):
        sku_norm = _perguntas_ia_memoria_sku_normalizar(candidato)
        if sku_norm:
            return sku_norm
    return ""


def _perguntas_ia_memoria_sku_dir(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "perguntas_pos_venda_memoria_sku")


def _perguntas_ia_memoria_sku_path(client_id: str, sku: str) -> str:
    sku_norm = _perguntas_ia_memoria_sku_normalizar(sku) or "sku"
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", sku_norm).strip("_")[:80] or "sku"
    digest = hashlib.sha1(sku_norm.upper().encode("utf-8", errors="ignore")).hexdigest()[:10]
    return os.path.join(_perguntas_ia_memoria_sku_dir(client_id), f"{slug}_{digest}.json")


def _perguntas_ia_memoria_payload_vazio(sku: str) -> dict:
    agora = dt.datetime.now().isoformat(timespec="seconds")
    return {
        "version": 1,
        "sku": _perguntas_ia_memoria_sku_normalizar(sku),
        "created_at": agora,
        "updated_at": agora,
        "resumo_compacto": "",
        "eventos": [],
        "estatisticas": {"total_eventos": 0, "compactacoes": 0},
    }


def _perguntas_ia_memoria_normalizar(payload: object, sku: str) -> dict:
    base = _perguntas_ia_memoria_payload_vazio(sku)
    if not isinstance(payload, dict):
        return base
    eventos = payload.get("eventos") if isinstance(payload.get("eventos"), list) else []
    eventos_norm = [item for item in eventos if isinstance(item, dict)]
    stats = payload.get("estatisticas") if isinstance(payload.get("estatisticas"), dict) else {}
    base.update({
        "version": int(payload.get("version") or 1),
        "sku": _perguntas_ia_memoria_sku_normalizar(payload.get("sku") or sku),
        "created_at": payload.get("created_at") or base["created_at"],
        "updated_at": payload.get("updated_at") or base["updated_at"],
        "resumo_compacto": str(payload.get("resumo_compacto") or "").strip()[:24000],
        "eventos": eventos_norm[-ML_PERGUNTAS_IA_MEMORIA_SKU_MAX_EVENTOS:],
        "estatisticas": {
            "total_eventos": int(stats.get("total_eventos") or len(eventos_norm)),
            "compactacoes": int(stats.get("compactacoes") or 0),
        },
    })
    return base


def _perguntas_ia_memoria_carregar(client_id: str, sku: str) -> dict:
    sku_norm = _perguntas_ia_memoria_sku_normalizar(sku)
    if not sku_norm:
        return {}
    caminho = _perguntas_ia_memoria_sku_path(client_id, sku_norm)
    data = _perguntas_ia_ler_json(caminho, {})
    return _perguntas_ia_memoria_normalizar(data, sku_norm)


def _perguntas_ia_memoria_bytes(payload: dict) -> int:
    return len(json.dumps(payload or {}, ensure_ascii=False, default=str).encode("utf-8", errors="ignore"))


def _perguntas_ia_memoria_salvar(client_id: str, memoria: dict) -> None:
    sku = _perguntas_ia_memoria_sku_normalizar((memoria or {}).get("sku"))
    if not sku:
        return
    caminho = _perguntas_ia_memoria_sku_path(client_id, sku)
    _perguntas_ia_salvar_json(caminho, memoria)


def _perguntas_ia_memoria_resumir_matches(matches: object) -> list[dict]:
    saida = []
    for match in (matches if isinstance(matches, list) else [])[:3]:
        if not isinstance(match, dict):
            continue
        resumo = {}
        for chave in (
            "sku", "seller_sku", "id", "item_id", "id_bling", "title", "titulo",
            "nome", "marca", "categoria", "condition", "loja", "loja_consulta",
            "url", "link", "permalink",
        ):
            valor = match.get(chave)
            if valor not in (None, "", [], {}):
                resumo[chave] = str(valor)[:500]
        for chave in ("price", "preco", "available_quantity", "estoque", "saldo_loja", "saldo_full"):
            valor = match.get(chave)
            if valor not in (None, ""):
                resumo[chave] = valor
        descricao = str(match.get("description") or match.get("descricao") or "").strip()
        if descricao:
            resumo["descricao"] = descricao[:900]
        if resumo:
            saida.append(resumo)
    return saida


def _perguntas_ia_memoria_resumir_tool_results(tool_results: object) -> list[dict]:
    saida = []
    for tool in (tool_results if isinstance(tool_results, list) else [])[:8]:
        if not isinstance(tool, dict):
            continue
        result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
        item = {
            "function": str(tool.get("function") or "")[:120],
            "found": bool(result.get("found")),
            "timeout": bool(result.get("timeout")),
            "error": str(result.get("error") or "")[:220],
        }
        matches = _perguntas_ia_memoria_resumir_matches(result.get("matches"))
        if matches:
            item["matches"] = matches
        contexto = str(result.get("context") or "").strip()
        if contexto:
            item["context"] = contexto[:2200]
        message = str(result.get("message") or "").strip()
        if message:
            item["message"] = message[:300]
        saida.append({k: v for k, v in item.items() if v not in ("", [], {})})
    return saida


def _perguntas_ia_memoria_evento_base(tipo: str, loja: str, sku: str, item_id: str = "", question_id: str = "") -> dict:
    return {
        "tipo": str(tipo or "evento").strip()[:80],
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "loja": str(loja or "").strip()[:160],
        "sku": _perguntas_ia_memoria_sku_normalizar(sku),
        "item_id": str(item_id or "").strip()[:80],
        "question_id": str(question_id or "").strip()[:80],
    }


def _perguntas_ia_memoria_compactar_local(memoria: dict, removidos: list[dict]) -> str:
    resumo_atual = str((memoria or {}).get("resumo_compacto") or "").strip()
    linhas = [resumo_atual] if resumo_atual else []
    for evento in removidos[-40:]:
        tipo = str(evento.get("tipo") or "")
        pergunta = str(evento.get("pergunta") or "").strip()
        resposta = str(evento.get("resposta_aprovada") or evento.get("resposta_rascunho") or "").strip()
        fatos = []
        for tool in evento.get("tool_results") or []:
            if not isinstance(tool, dict):
                continue
            if tool.get("found"):
                fatos.append(str(tool.get("function") or "ferramenta"))
        linha = f"- {tipo}"
        if pergunta:
            linha += f" | pergunta: {pergunta[:180]}"
        if resposta:
            linha += f" | resposta: {resposta[:220]}"
        if fatos:
            linha += f" | fontes: {', '.join(fatos[:4])}"
        linhas.append(linha)
    return _perguntas_ia_compactar_contexto("\n".join(linhas), 12000)


def _perguntas_ia_memoria_compactar_com_ia(client_id: str, memoria: dict, removidos: list[dict]) -> str:
    if not removidos:
        return str((memoria or {}).get("resumo_compacto") or "").strip()[:12000]
    eventos_txt = json.dumps(removidos[-80:], ensure_ascii=False, default=str)[:50000]
    resumo_atual = str((memoria or {}).get("resumo_compacto") or "").strip()[:12000]
    prompt = (
        "Compacte a memoria tecnica deste SKU para uso em respostas do Mercado Livre. "
        "Preserve fatos uteis do produto, aplicacoes, codigos, compatibilidade, alertas, respostas aprovadas e padroes de atendimento. "
        "Remova repeticoes, dados fracos, erros de ferramentas e informacoes sem fonte. "
        "Nao invente nada. Responda em topicos curtos, com no maximo 9000 caracteres.\n\n"
        f"Resumo atual:\n{resumo_atual or '-'}\n\n"
        f"Eventos antigos a compactar em JSON:\n{eventos_txt}"
    )
    model_req = _normalizar_ia_modelo_padrao(_ia_modelo_perguntas_configurado())
    payload = IAChatRequest(
        message=prompt,
        page="Perguntas e pos venda",
        context={
            "modulo": "perguntas_pos_venda",
            "tipo": "compactacao_memoria_sku",
            "modo_rapido_sidebar": True,
        },
        model=model_req,
    )
    try:
        resposta, _model_usado = _ia_agent_perguntas_chamar_modelo(client_id, payload, model_req)
        resumo = _perguntas_ia_compactar_contexto(resposta, 12000)
        return resumo or _perguntas_ia_memoria_compactar_local(memoria, removidos)
    except Exception as exc:
        logger.warning("[ML PERGUNTAS IA] Falha ao compactar memoria do SKU com IA: %s", exc)
        return _perguntas_ia_memoria_compactar_local(memoria, removidos)


def _perguntas_ia_memoria_garantir_limite(client_id: str, memoria: dict) -> dict:
    limite = _perguntas_ia_memoria_sku_limite_bytes()
    eventos = memoria.get("eventos") if isinstance(memoria.get("eventos"), list) else []
    if _perguntas_ia_memoria_bytes(memoria) <= limite:
        return memoria

    manter_min = 30
    removidos: list[dict] = []
    while len(eventos) > manter_min and _perguntas_ia_memoria_bytes(memoria) > limite:
        remover_qtd = max(1, min(40, len(eventos) - manter_min))
        removidos.extend(eventos[:remover_qtd])
        eventos = eventos[remover_qtd:]
        memoria["eventos"] = eventos

    if removidos:
        memoria["resumo_compacto"] = _perguntas_ia_memoria_compactar_com_ia(client_id, memoria, removidos)
        stats = memoria.setdefault("estatisticas", {})
        stats["compactacoes"] = int(stats.get("compactacoes") or 0) + 1

    while eventos and _perguntas_ia_memoria_bytes(memoria) > limite:
        eventos = eventos[1:]
        memoria["eventos"] = eventos

    if _perguntas_ia_memoria_bytes(memoria) > limite:
        memoria["resumo_compacto"] = str(memoria.get("resumo_compacto") or "")[:8000]
    return memoria


def _perguntas_ia_memoria_registrar_evento(client_id: str, sku: str, evento: dict) -> dict:
    sku_norm = _perguntas_ia_memoria_sku_normalizar(sku)
    if not sku_norm or not isinstance(evento, dict):
        return {}
    with PERGUNTAS_IA_MEMORIA_SKU_LOCK:
        memoria = _perguntas_ia_memoria_carregar(client_id, sku_norm)
        if not memoria:
            memoria = _perguntas_ia_memoria_payload_vazio(sku_norm)
        evento["sku"] = sku_norm
        memoria.setdefault("eventos", []).append(evento)
        memoria["eventos"] = [
            ev for ev in memoria.get("eventos", []) if isinstance(ev, dict)
        ][-ML_PERGUNTAS_IA_MEMORIA_SKU_MAX_EVENTOS:]
        memoria["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        stats = memoria.setdefault("estatisticas", {})
        stats["total_eventos"] = int(stats.get("total_eventos") or 0) + 1
        memoria = _perguntas_ia_memoria_garantir_limite(client_id, memoria)
        _perguntas_ia_memoria_salvar(client_id, memoria)
        return {
            "sku": sku_norm,
            "bytes": _perguntas_ia_memoria_bytes(memoria),
            "limite_bytes": _perguntas_ia_memoria_sku_limite_bytes(),
            "eventos": len(memoria.get("eventos") or []),
        }


def _perguntas_ia_memoria_registrar_pesquisa(
    client_id: str,
    loja: str,
    agent_input: dict,
    tool_results: list[dict],
    resposta_rascunho: str,
) -> dict:
    sku = _perguntas_ia_memoria_sku_de_fontes(agent_input=agent_input)
    if not sku:
        return {}
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    evento = _perguntas_ia_memoria_evento_base(
        "pesquisa_ia",
        loja,
        sku,
        item_id=item.get("id") or question.get("item_id") or "",
        question_id=question.get("id") or "",
    )
    evento.update({
        "titulo": str(item.get("title") or "")[:500],
        "pergunta": str(question.get("text") or "")[:1200],
        "resposta_rascunho": str(resposta_rascunho or "")[:1900],
        "tool_results": _perguntas_ia_memoria_resumir_tool_results(tool_results),
    })
    return _perguntas_ia_memoria_registrar_evento(client_id, sku, evento)


def _perguntas_ia_memoria_registrar_resposta_aprovada(
    client_id: str,
    loja: str,
    resposta: str,
    *,
    approval: Optional[dict] = None,
    pergunta: Optional[dict] = None,
    origem: str = "manual",
    sku: object = "",
    item_id: object = "",
    question_id: object = "",
) -> dict:
    approval = approval if isinstance(approval, dict) else {}
    pergunta = pergunta if isinstance(pergunta, dict) else {}
    produto = approval.get("produto") if isinstance(approval.get("produto"), dict) else {}
    sku_norm = _perguntas_ia_memoria_sku_de_fontes(
        sku=sku,
        pergunta=pergunta,
        item=produto,
        approval=approval,
    )
    if not sku_norm:
        return {}
    qid = str(question_id or approval.get("question_id") or pergunta.get("id") or "").strip()
    item_id_norm = str(item_id or approval.get("item_id") or pergunta.get("item_id") or produto.get("id") or "").strip()
    evento = _perguntas_ia_memoria_evento_base("resposta_aprovada", loja, sku_norm, item_id=item_id_norm, question_id=qid)
    evento.update({
        "origem": str(origem or "manual")[:80],
        "titulo": str(approval.get("titulo") or pergunta.get("item_title") or produto.get("title") or "")[:500],
        "pergunta": str(approval.get("pergunta") or pergunta.get("text") or "")[:1200],
        "resposta_aprovada": str(resposta or "")[:1900],
    })
    return _perguntas_ia_memoria_registrar_evento(client_id, sku_norm, evento)


def _perguntas_ia_memoria_bloco_prompt(client_id: str, agent_input: dict) -> str:
    sku = _perguntas_ia_memoria_sku_de_fontes(agent_input=agent_input)
    if not sku:
        return ""
    memoria = _perguntas_ia_memoria_carregar(client_id, sku)
    if not memoria:
        return ""
    resumo = str(memoria.get("resumo_compacto") or "").strip()
    eventos = [ev for ev in (memoria.get("eventos") or []) if isinstance(ev, dict)]
    aprovadas = [ev for ev in eventos if str(ev.get("tipo") or "") == "resposta_aprovada"][-6:]
    partes = [f"Memoria local do SKU {sku} (capacidade minima por SKU: {ML_PERGUNTAS_IA_MEMORIA_SKU_MIN_BYTES // 1024} KB)."]
    if resumo:
        partes.append(f"Resumo compacto acumulado:\n{resumo[:12000]}")
    if aprovadas:
        linhas = []
        for ev in aprovadas:
            perguntas_anuncio = []
            for evento_anuncio in ev.get("perguntas_anuncio") or []:
                if not isinstance(evento_anuncio, dict):
                    continue
                texto_anuncio = str(evento_anuncio.get("text") or "").strip()
                if texto_anuncio:
                    perguntas_anuncio.append(
                        f"{evento_anuncio.get('label') or evento_anuncio.get('role') or 'Comprador'}: {texto_anuncio[:180]}"
                    )
            linhas.append(
                "Pergunta: "
                + str(ev.get("pergunta") or "-")[:350]
                + "\nResposta aprovada: "
                + str(ev.get("resposta_aprovada") or "-")[:600]
                + "\nPerguntas anteriores no anuncio: "
                + (" | ".join(perguntas_anuncio[:4]) or "-")
            )
        partes.append("Respostas aprovadas recentes para aprender tom e padrao:\n" + "\n\n".join(linhas))
    bloco = "\n\n".join([p for p in partes if p.strip()])
    return _perguntas_ia_compactar_contexto(bloco, ML_PERGUNTAS_IA_MEMORIA_SKU_PROMPT_MAX_CHARS)


def _ml_pos_venda_memoria_items(conversa: Optional[dict], approval: Optional[dict] = None) -> list[dict]:
    conversa = conversa if isinstance(conversa, dict) else {}
    approval = approval if isinstance(approval, dict) else {}
    conversa_aprovacao = approval.get("conversa") if isinstance(approval.get("conversa"), dict) else {}
    fontes = []
    for origem in (conversa, conversa_aprovacao):
        items = origem.get("items") if isinstance(origem.get("items"), list) else []
        fontes.extend([item for item in items if isinstance(item, dict)])
    if approval:
        fontes.append({
            "id": approval.get("item_id") or "",
            "sku": approval.get("sku") or approval.get("item_sku") or "",
            "title": approval.get("titulo") or "",
        })
    saida = []
    vistos = set()
    for item in fontes:
        sku = _perguntas_ia_memoria_sku_normalizar(item.get("sku") or item.get("seller_sku"))
        if not sku:
            continue
        chave = sku.upper()
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append({
            "id": str(item.get("id") or item.get("item_id") or "").strip(),
            "sku": sku,
            "title": str(item.get("title") or item.get("titulo") or "").strip(),
            "quantity": item.get("quantity"),
        })
    return saida


def _ml_pos_venda_memoria_ultima_mensagem(conversa: Optional[dict], approval: Optional[dict] = None) -> str:
    conversa = conversa if isinstance(conversa, dict) else {}
    approval = approval if isinstance(approval, dict) else {}
    texto_aprovacao = str(approval.get("pergunta") or "").strip()
    if texto_aprovacao:
        return texto_aprovacao[:1200]
    texto = str(
        conversa.get("last_message_text")
        or ""
    ).strip()
    last_role = str(conversa.get("last_message_role") or "").strip().lower()
    if texto and last_role not in {"loja", "seller", "store"}:
        return texto[:1200]
    mensagens = conversa.get("messages") if isinstance(conversa.get("messages"), list) else []
    for msg in reversed(mensagens):
        if not isinstance(msg, dict):
            continue
        if str(msg.get("from_role") or "").strip().lower() == "seller":
            continue
        texto_msg = str(msg.get("text") or "").strip()
        if texto_msg:
            return texto_msg[:1200]
    return texto[:1200] if texto else ""


def _ml_pos_venda_memoria_historico(conversa: Optional[dict]) -> list[dict]:
    conversa = conversa if isinstance(conversa, dict) else {}
    mensagens = conversa.get("messages") if isinstance(conversa.get("messages"), list) else []
    historico = []
    for msg in mensagens[-10:]:
        if not isinstance(msg, dict):
            continue
        texto = str(msg.get("text") or "").strip()
        if not texto:
            continue
        role = str(msg.get("from_role") or "").strip().lower()
        historico.append({
            "role": "seller" if role == "seller" else "buyer",
            "text": texto[:700],
            "date": str(msg.get("date") or "")[:80],
        })
    return historico


def _ml_pos_venda_perguntas_anuncio_chat(conversa: Optional[dict]) -> list[dict]:
    conversa = conversa if isinstance(conversa, dict) else {}
    chat = conversa.get("buyer_listing_question_chat") if isinstance(conversa.get("buyer_listing_question_chat"), list) else []
    saida = []
    for evento in chat[-20:]:
        if not isinstance(evento, dict):
            continue
        texto = str(evento.get("text") or "").strip()
        if not texto:
            continue
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        saida.append({
            "role": "seller" if role in {"seller", "loja", "store"} else "buyer",
            "label": evento.get("label") or ("Loja" if role in {"seller", "loja", "store"} else "Comprador"),
            "text": texto[:700],
            "date": str(evento.get("date") or evento.get("date_created") or "")[:80],
            "question_id": str(evento.get("question_id") or "")[:80],
        })
    return saida


def _ml_pos_venda_memoria_question_id(conversa: Optional[dict], approval: Optional[dict] = None) -> str:
    conversa = conversa if isinstance(conversa, dict) else {}
    approval = approval if isinstance(approval, dict) else {}
    if approval.get("question_id"):
        return str(approval.get("question_id") or "").strip()[:80]
    pack_id = str(conversa.get("pack_id") or approval.get("pack_id") or "").strip()
    marcador = str(
        conversa.get("last_message_id")
        or conversa.get("last_message_date")
        or conversa.get("order_id")
        or approval.get("order_id")
        or ""
    ).strip()
    return f"pos_venda:{pack_id}:{marcador}"[:80] if pack_id else marcador[:80]


def _ml_pos_venda_memoria_bloco_prompt(client_id: str, conversa: dict) -> str:
    partes = []
    for item in _ml_pos_venda_memoria_items(conversa)[:4]:
        sku = item.get("sku") or ""
        if not sku:
            continue
        agent_input = {
            "question": {
                "item_sku": sku,
                "text": conversa.get("last_message_text") or "",
            },
            "item": {
                "id": item.get("id") or "",
                "seller_sku": sku,
                "sku": sku,
                "title": item.get("title") or "",
            },
        }
        bloco = _perguntas_ia_memoria_bloco_prompt(client_id, agent_input)
        if bloco:
            partes.append(bloco)
    return _perguntas_ia_compactar_contexto(
        "\n\n".join(partes),
        ML_PERGUNTAS_IA_MEMORIA_SKU_PROMPT_MAX_CHARS,
    )


def _ml_pos_venda_memoria_registrar_evento(
    client_id: str,
    loja: str,
    conversa: Optional[dict],
    resposta: str,
    *,
    tipo: str,
    campo_resposta: str,
    origem: str,
    model_usado: str = "",
    approval: Optional[dict] = None,
) -> list[dict]:
    conversa = conversa if isinstance(conversa, dict) else {}
    approval = approval if isinstance(approval, dict) else {}
    items = _ml_pos_venda_memoria_items(conversa, approval)
    if not items:
        return []
    pergunta = _ml_pos_venda_memoria_ultima_mensagem(conversa, approval)
    question_id = _ml_pos_venda_memoria_question_id(conversa, approval)
    pack_id = str(conversa.get("pack_id") or approval.get("pack_id") or "").strip()
    order_id = str(conversa.get("order_id") or approval.get("order_id") or "").strip()
    buyer_id = str(conversa.get("buyer_id") or approval.get("buyer_id") or "").strip()
    historico = _ml_pos_venda_memoria_historico(conversa)
    perguntas_anuncio = _ml_pos_venda_perguntas_anuncio_chat(conversa)
    resultados = []
    for item in items:
        evento = _perguntas_ia_memoria_evento_base(
            tipo,
            loja,
            item.get("sku") or "",
            item_id=item.get("id") or "",
            question_id=question_id,
        )
        evento.update({
            "origem": str(origem or "pos_venda")[:80],
            "titulo": str(item.get("title") or approval.get("titulo") or conversa.get("item_title") or "")[:500],
            "pergunta": pergunta,
            campo_resposta: str(resposta or "")[:1900],
            "pack_id": pack_id[:80],
            "order_id": order_id[:80],
            "buyer_id": buyer_id[:80],
            "model": str(model_usado or approval.get("model") or "")[:160],
            "historico": historico,
            "perguntas_anuncio": perguntas_anuncio,
        })
        registrado = _perguntas_ia_memoria_registrar_evento(client_id, item.get("sku") or "", evento)
        if registrado:
            resultados.append(registrado)
    return resultados


def _ml_pos_venda_memoria_registrar_geracao(
    client_id: str,
    loja: str,
    conversa: dict,
    resposta_rascunho: str,
    model_usado: str = "",
) -> list[dict]:
    return _ml_pos_venda_memoria_registrar_evento(
        client_id,
        loja,
        conversa,
        resposta_rascunho,
        tipo="pos_venda_ia",
        campo_resposta="resposta_rascunho",
        origem="geracao_pos_venda",
        model_usado=model_usado,
    )


def _ml_pos_venda_memoria_registrar_resposta_enviada(
    client_id: str,
    loja: str,
    conversa: Optional[dict],
    resposta: str,
    *,
    origem: str,
    approval: Optional[dict] = None,
) -> list[dict]:
    return _ml_pos_venda_memoria_registrar_evento(
        client_id,
        loja,
        conversa,
        resposta,
        tipo="resposta_aprovada",
        campo_resposta="resposta_aprovada",
        origem=origem,
        approval=approval,
    )


def _ia_agent_extrair_texto(valor: Any) -> str:
    if valor is None:
        return ""
    if isinstance(valor, str):
        return valor.strip()
    if isinstance(valor, dict):
        for chave in ("resposta", "response", "answer", "text", "message", "content", "output", "result"):
            texto = _ia_agent_extrair_texto(valor.get(chave))
            if texto:
                return texto
        for chave in ("messages", "candidates", "choices"):
            lista = valor.get(chave)
            if isinstance(lista, list):
                for item in reversed(lista):
                    texto = _ia_agent_extrair_texto(item)
                    if texto:
                        return texto
        return ""
    if isinstance(valor, list):
        partes = [_ia_agent_extrair_texto(item) for item in valor]
        return "\n".join([parte for parte in partes if parte]).strip()
    if isinstance(valor, (int, float, bool)):
        return str(valor).strip()
    return ""


def _ia_agent_engine_query_url(resource_name: str) -> str:
    resource = str(resource_name or "").strip()
    if not resource:
        return ""
    if resource.startswith(("http://", "https://")):
        return resource if resource.endswith(":query") else f"{resource.rstrip('/')}:query"
    resource = resource.strip("/")
    return f"https://aiplatform.googleapis.com/v1/{resource}:query"


def _ia_agent_endpoint_query_url(endpoint_url: str) -> str:
    url = str(endpoint_url or "").strip()
    if not url:
        return ""
    if url.endswith(("/api/ia/agente/perguntas/query", "/api/ia/agent/perguntas/query")):
        return url
    return f"{url.rstrip('/')}/api/ia/agente/perguntas/query"


def _ia_agent_endpoint_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    api_key = (
        _env_config_value("JK_AGENT_ENDPOINT_API_KEY", "IA_AGENT_ENDPOINT_API_KEY")
        or _vertex_ai_agent_api_key_arquivo()
        or _vertex_ai_agent_api_key()
    )
    if api_key:
        headers["X-JK-Agent-Key"] = api_key
    return headers


def _ia_agent_http_post(url: str, body: dict, headers: dict, *, timeout: int = 75) -> dict:
    try:
        resp = requests.post(url, headers=headers, json=body, verify=False, timeout=timeout)
    except requests.RequestException as exc:
        raise PerguntasIARespostaIndisponivel(f"Agente Cloud indisponivel: {exc}") from exc
    if not resp.ok:
        detalhe = resp.text[:500]
        try:
            erro = resp.json().get("error") or resp.json().get("detail") or resp.json()
            if erro:
                detalhe = json.dumps(erro, ensure_ascii=False)[:500]
        except Exception:
            pass
        raise PerguntasIARespostaIndisponivel(f"Agente Cloud retornou HTTP {resp.status_code}: {detalhe}")
    try:
        data = resp.json()
    except Exception as exc:
        raise PerguntasIARespostaIndisponivel("Agente Cloud retornou resposta em formato invalido.") from exc
    return data if isinstance(data, dict) else {"output": data}


def _perguntas_ia_item_para_agente(item: dict, descricao: str = "") -> dict:
    item = item if isinstance(item, dict) else {}
    item_id = str(item.get("id") or "").strip()
    permalink = str(item.get("permalink") or item.get("url") or item.get("link") or "").strip()
    if not permalink and item_id:
        permalink = _favoritos_ml_url_item_id(item_id)
    descricao = str(
        descricao
        or item.get("description")
        or item.get("descricao")
        or item.get("plain_text")
        or ""
    ).strip()
    atributos = []
    for attr in (item.get("attributes") or [])[:40]:
        if not isinstance(attr, dict):
            continue
        atributos.append({
            "id": attr.get("id") or "",
            "name": attr.get("name") or "",
            "value_name": attr.get("value_name") or attr.get("value_id") or "",
        })
    sale_terms = []
    for term in (item.get("sale_terms") or [])[:20]:
        if not isinstance(term, dict):
            continue
        sale_terms.append({
            "id": term.get("id") or "",
            "name": term.get("name") or "",
            "value_name": term.get("value_name") or term.get("value_id") or "",
        })
    variations = []
    for variation in (item.get("variations") or [])[:20]:
        if not isinstance(variation, dict):
            continue
        variations.append({
            "id": variation.get("id") or "",
            "available_quantity": variation.get("available_quantity"),
            "price": variation.get("price"),
            "attribute_combinations": variation.get("attribute_combinations") or [],
        })
    return {
        "id": item_id,
        "title": item.get("title") or "",
        "permalink": permalink,
        "link": permalink,
        "url": permalink,
        "thumbnail": item.get("thumbnail") or "",
        "price": item.get("price"),
        "currency_id": item.get("currency_id") or "",
        "available_quantity": item.get("available_quantity"),
        "status": item.get("status") or "",
        "condition": item.get("condition") or "",
        "category_id": item.get("category_id") or "",
        "catalog_product_id": item.get("catalog_product_id") or "",
        "listing_type_id": item.get("listing_type_id") or "",
        "buying_mode": item.get("buying_mode") or "",
        "seller_sku": _ml_extrair_sku(item),
        "description": descricao[:ML_PERGUNTAS_IA_DESCRICAO_AGENT_MAX_CHARS],
        "attributes": atributos,
        "sale_terms": sale_terms,
        "shipping": item.get("shipping") if isinstance(item.get("shipping"), dict) else {},
        "variations": variations,
        "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
        "pictures_count": len(item.get("pictures") or []) if isinstance(item.get("pictures"), list) else 0,
    }


def _perguntas_ia_pergunta_para_agente(pergunta: dict) -> dict:
    pergunta = pergunta if isinstance(pergunta, dict) else {}
    historico = pergunta.get("buyer_question_chat") if isinstance(pergunta.get("buyer_question_chat"), list) else []
    resposta_atual = str(pergunta.get("_resposta_atual") or "").strip()
    return {
        "id": pergunta.get("id") or "",
        "text": pergunta.get("text") or "",
        "current_draft_to_avoid": resposta_atual[:1200],
        "item_id": pergunta.get("item_id") or "",
        "date_created": pergunta.get("date_created") or "",
        "status": pergunta.get("status") or "",
        "buyer_id": pergunta.get("buyer_id") or "",
        "buyer_name": pergunta.get("buyer_name") or "",
        "history_count": pergunta.get("buyer_question_history_count") or len(historico),
        "history_source": "Mercado Livre questions/search: mesmo comprador no mesmo anuncio",
        "history": historico[-10:],
    }


def _ia_agent_endpoint_api_key_configurada() -> str:
    return (
        _env_config_value("JK_AGENT_ENDPOINT_API_KEY", "IA_AGENT_ENDPOINT_API_KEY")
        or _vertex_ai_agent_api_key()
        or ""
    ).strip()


def _ml_pos_venda_classificar_motivo(conversa: dict, reclamacao: dict) -> dict:
    ultima = _ml_pos_venda_ultima_mensagem_comprador(conversa)
    texto = _ml_pos_venda_texto_norm(ultima.get("text") or conversa.get("last_message_text") or "")
    if reclamacao.get("available"):
        return {"motivo": "reclamacao_mediacao", "confianca": 0.98, "evidencia": "ha reclamacao ou mediacao aberta"}
    regras = [
        ("entrega", ("entrega", "envio", "rastreio", "rastrear", "chega", "chegou", "recebi", "recebido", "atraso", "transportadora")),
        ("nota_fiscal", ("nota fiscal", "nf", "nfe", "danfe", "xml", "cupom fiscal")),
        ("pagamento", ("pagamento", "paguei", "pago", "boleto", "pix", "cartao", "estorno", "cobrado", "cobranca")),
        ("cancelamento", ("cancelar", "cancelamento", "cancelei", "desistir", "desistencia")),
        ("troca_devolucao", ("troca", "trocar", "devolver", "devolucao", "arrependimento", "reembolso")),
        ("defeito_garantia", ("defeito", "quebrado", "nao funciona", "parou", "garantia", "avaria", "danificado", "problema")),
        ("duvida_produto", ("como usa", "como funciona", "manual", "instalar", "instalacao", "compativel", "serve", "medida")),
        ("agradecimento", ("obrigado", "obrigada", "valeu", "perfeito", "ok", "certo")),
    ]
    for motivo, termos in regras:
        if any(termo in texto for termo in termos):
            return {"motivo": motivo, "confianca": 0.86, "evidencia": f"termos: {', '.join([t for t in termos if t in texto][:4])}"}
    if not texto:
        return {"motivo": "sem_texto", "confianca": 0.4, "evidencia": "mensagem vazia ou apenas anexo"}
    return {"motivo": "outro", "confianca": 0.55, "evidencia": "sem gatilho claro"}

PEER_EXPORTS = ['ML_RESPOSTA_PERGUNTA_MAX_CHARS', 'ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO', 'ML_PERGUNTAS_IA_PROMPT_MAX_CHARS', 'ML_PERGUNTAS_IA_DESCRICAO_PROMPT_MAX_CHARS', 'ML_PERGUNTAS_IA_DESCRICAO_AGENT_MAX_CHARS', 'ML_PERGUNTAS_IA_CONTEXTO_EXTRA_PROMPT_MAX_CHARS', 'ML_PERGUNTAS_IA_MEMORIA_SKU_MIN_BYTES', 'ML_PERGUNTAS_IA_MEMORIA_SKU_MAX_EVENTOS', 'ML_PERGUNTAS_IA_MEMORIA_SKU_PROMPT_MAX_CHARS', 'IA_CHAT_MESSAGE_MAX_CHARS', 'IA_CHAT_MESSAGE_COMPACT_TARGET_CHARS', 'ML_POS_VENDA_DEFAULT_MAX_CHARS', 'ML_POS_VENDA_LIMITE_SEGURO', 'PERGUNTAS_AUTOMACAO_INTERVALO_PADRAO_MIN', 'PERGUNTAS_AUTOMACAO_INTERVALO_MIN', 'PERGUNTAS_AUTOMACAO_INTERVALO_MAX', 'PERGUNTAS_AUTOMACAO_BG_LOCK', 'PERGUNTAS_AUTOMACAO_BG_THREAD_STARTED', 'PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS', 'PERGUNTAS_AUTOMACAO_BG_RUNNING', 'PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS', 'PERGUNTAS_IA_MEMORIA_SKU_LOCK', '_perguntas_loja_config_path', '_perguntas_loja_configs_carregar', '_perguntas_loja_config_normalizar', '_perguntas_loja_config_obter', '_perguntas_loja_config_salvar', '_perguntas_ia_state_path', '_perguntas_ia_aprovacoes_path', '_ml_questions_v2_webhook_events_path', '_perguntas_ia_ler_json', '_perguntas_ia_salvar_json', '_perguntas_ia_state_carregar', '_perguntas_ia_state_salvar', '_perguntas_ia_aprovacoes_carregar', '_perguntas_ia_aprovacoes_salvar', '_perguntas_ia_aprovacao_id', '_pos_venda_ia_aprovacao_id', '_perguntas_ia_marcar_processada', '_perguntas_ia_ja_processada', '_perguntas_ia_aprovacao_pendente', '_perguntas_ia_resolver_aprovacao', '_perguntas_ia_resolver_aprovacoes_pendentes', '_perguntas_ia_pergunta_respondida_ml', '_ml_pos_venda_conversa_respondida_pela_loja', '_perguntas_ia_limpar_resposta', '_perguntas_ia_assinatura_loja', '_perguntas_ia_remover_apresentacao_sistema', '_perguntas_ia_resposta_final_loja', 'PerguntasIARespostaIndisponivel', '_perguntas_ia_resposta_fallback_invalida', 'ML_PERGUNTAS_IA_INTENCOES', 'ML_PERGUNTAS_IA_INTENCOES_POS_VENDA', '_perguntas_ia_intencao_fluxo', '_perguntas_ia_intencao_heuristica', '_perguntas_ia_json_obj', '_perguntas_ia_intencao_normalizar', '_perguntas_ia_classificar_intencao', '_perguntas_ia_intencao_agent', '_perguntas_ia_fluxo_pos_venda', '_perguntas_ia_compactar_contexto', '_perguntas_ia_limitar_prompt', '_perguntas_ia_memoria_sku_limite_bytes', '_perguntas_ia_memoria_sku_normalizar', '_perguntas_ia_memoria_sku_de_fontes', '_perguntas_ia_memoria_sku_dir', '_perguntas_ia_memoria_sku_path', '_perguntas_ia_memoria_payload_vazio', '_perguntas_ia_memoria_normalizar', '_perguntas_ia_memoria_carregar', '_perguntas_ia_memoria_bytes', '_perguntas_ia_memoria_salvar', '_perguntas_ia_memoria_resumir_matches', '_perguntas_ia_memoria_resumir_tool_results', '_perguntas_ia_memoria_evento_base', '_perguntas_ia_memoria_compactar_local', '_perguntas_ia_memoria_compactar_com_ia', '_perguntas_ia_memoria_garantir_limite', '_perguntas_ia_memoria_registrar_evento', '_perguntas_ia_memoria_registrar_pesquisa', '_perguntas_ia_memoria_registrar_resposta_aprovada', '_perguntas_ia_memoria_bloco_prompt', '_ml_pos_venda_memoria_items', '_ml_pos_venda_memoria_ultima_mensagem', '_ml_pos_venda_memoria_historico', '_ml_pos_venda_perguntas_anuncio_chat', '_ml_pos_venda_memoria_question_id', '_ml_pos_venda_memoria_bloco_prompt', '_ml_pos_venda_memoria_registrar_evento', '_ml_pos_venda_memoria_registrar_geracao', '_ml_pos_venda_memoria_registrar_resposta_enviada', '_ia_agent_extrair_texto', '_ia_agent_engine_query_url', '_ia_agent_endpoint_query_url', '_ia_agent_endpoint_headers', '_ia_agent_http_post', '_perguntas_ia_item_para_agente', '_perguntas_ia_pergunta_para_agente', '_ia_agent_endpoint_api_key_configurada', '_ml_pos_venda_classificar_motivo']
__all__ = PEER_EXPORTS + ["configure_perguntas_pos_venda_state_runtime"]

configure_perguntas_pos_venda_state_runtime()
