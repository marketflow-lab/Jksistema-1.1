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
from backend.services.transport_security import requests_tls_verify
from ml_questions_gemini.classifier import QuestionClassifier, normalize as _ml_question_normalize
from ml_questions_gemini.compatibility import PROFILE_BY_TARGET_TYPE, TARGET_TYPES
from ml_questions_gemini.schemas import QuestionCategory
from backend.modules.perguntas_pos_venda.ai import provider_transport as perguntas_agent_provider_transport
from backend.modules.perguntas_pos_venda.ai import telemetry_core as perguntas_agent_telemetry


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


ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO = 2000


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
    return {
        "responder_automaticamente": bool(config.get("responder_automaticamente")),
        # Automatico significa gerar o rascunho; publicar sempre exige humano.
        "solicitar_aprovacao": True,
        "notificar_whatsapp_aprovacoes": bool(config.get("notificar_whatsapp_aprovacoes")),
        # Pos-venda aceita somente resposta manual. O valor legado permanece no
        # arquivo historico, mas nunca pode reativar sugestoes de IA/Black Jhon.
        "habilitar_pos_venda_automatico": False,
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
        "habilitar_pos_venda_automatico": False,
        "intervalo_minutos": intervalo_cfg["intervalo_minutos"],
        "updated_at": datetime.now().isoformat(timespec="seconds"),
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
        "updated_at": datetime.now().isoformat(timespec="seconds"),
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
    approval["resolved_at"] = datetime.now().isoformat(timespec="seconds")
    approval["resolved_reason"] = str(motivo or "respondida_por_outro_fluxo").strip()
    resposta_literal = resposta if isinstance(resposta, str) else str(resposta or "")
    if resposta_literal.strip():
        approval["resposta_enviada"] = resposta_literal
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
        return f"Equipe {nome_loja} agradece pelo contato, Precisando estamos a disposição!"
    return "Equipe da loja agradece pelo contato, Precisando estamos a disposição!"


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
    """Acrescenta a assinatura publica sem reescrever o corpo produzido pela IA."""

    assinatura = _perguntas_ia_assinatura_loja(loja)
    corpo = resposta if isinstance(resposta, str) else str(resposta or "")
    if not corpo.strip():
        return ""
    if corpo.rstrip().endswith(assinatura):
        return corpo
    return f"{corpo}\n\n{assinatura}"


class PerguntasIARespostaIndisponivel(RuntimeError):
    pass


class PerguntasIAClassificacaoInconclusiva(PerguntasIARespostaIndisponivel):
    """A classificacao terminou em revisao sem uma resposta geravel."""

    def __init__(
        self,
        message: str,
        classificacao: Optional[dict] = None,
        *,
        allow_contextual: bool = True,
    ):
        super().__init__(message)
        self.classificacao = dict(classificacao) if isinstance(classificacao, dict) else {}
        self.allow_contextual = bool(allow_contextual)


class PerguntasIAProviderIndisponivel(PerguntasIARespostaIndisponivel):
    """Falha tecnica transitória do provedor que pode consumir retry operacional."""

    def __init__(self, message: str, *, reason: str = "provider_unavailable"):
        super().__init__(message)
        self.reason = str(reason or "provider_unavailable")


class PerguntasIASegurancaBloqueada(PerguntasIARespostaIndisponivel):
    """A mensagem foi bloqueada por seguranca e nao pode herdar contexto."""


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


ML_PERGUNTAS_IA_CATEGORIAS = {categoria.value for categoria in QuestionCategory}
ML_PERGUNTAS_IA_FLUXOS = {"perguntas_anuncio", "pos_venda"}
ML_PERGUNTAS_IA_FLAGS = (
    "usar_busca_web",
    "usar_mercado_livre_anuncio",
    "usar_bling",
)
ML_PERGUNTAS_IA_SUBQUESTION_INTENTS = {
    "compatibility", "shipping", "stock", "price", "invoice",
    "warranty_originality", "product_feature", "other_product", "general",
    "post_sale",
}
ML_PERGUNTAS_IA_SUBQUESTION_CATEGORY = {
    "compatibility": "compatibility",
    "shipping": "shipping",
    "stock": "stock",
    "price": "price",
    "invoice": "invoice",
    "warranty_originality": "warranty_originality",
    "product_feature": "product_feature",
    "other_product": "other_product",
    "post_sale": "post_sale",
}
ML_PERGUNTAS_IA_GENERAL_CATEGORIES = {
    "greeting", "prohibited_contact", "regulated_product", "unknown",
}
ML_PERGUNTAS_IA_INTENCAO_CATEGORIAS = {
    "duvida_produto": {
        "greeting", "shipping", "product_feature", "warranty_originality",
        "invoice", "prohibited_contact", "regulated_product",
    },
    "compatibilidade": {"compatibility"},
    "outra_peca": {"other_product"},
    "preco_estoque": {"price", "stock"},
    "pos_venda_defeito": {"post_sale"},
    "troca_garantia": {"post_sale"},
    "entrega": {"post_sale"},
    "cancelamento": {"post_sale"},
    "reclamacao": {"post_sale"},
    "nao_entendi": {"unknown"},
}
ML_PERGUNTAS_IA_COMPATIBILITY_TARGET_TYPES = {"", *TARGET_TYPES}
ML_PERGUNTAS_IA_COMPATIBILITY_PROFILES = {"", *PROFILE_BY_TARGET_TYPE.values()}
ML_PERGUNTAS_IA_CONTINUIDADE_TIPOS = {
    "independente",
    "continuacao",
    "novo_assunto",
    "inconclusiva",
}
ML_PERGUNTAS_IA_CLASSIFICATION_CONTRACT_VERSION = "jk_ml_question_classification_v3"
ML_PERGUNTAS_IA_COMPATIBILITY_PROFILE_BY_TARGET_TYPE = dict(PROFILE_BY_TARGET_TYPE)
ML_PERGUNTAS_IA_CLASSIFICATION_PAGE = "Perguntas e pós venda"
ML_PERGUNTAS_IA_CLASSIFICATION_CONTRACT_HASH = hashlib.sha256(
    json.dumps(
        {
            "version": ML_PERGUNTAS_IA_CLASSIFICATION_CONTRACT_VERSION,
            "intent_categories": {
                intent: sorted(categories)
                for intent, categories in ML_PERGUNTAS_IA_INTENCAO_CATEGORIAS.items()
            },
            "subquestion_categories": ML_PERGUNTAS_IA_SUBQUESTION_CATEGORY,
            "compatibility_applicable_iff_category": True,
            "compatibility_target_types": sorted(ML_PERGUNTAS_IA_COMPATIBILITY_TARGET_TYPES),
            "compatibility_profiles": sorted(ML_PERGUNTAS_IA_COMPATIBILITY_PROFILES),
            "compatibility_profile_by_target_type": ML_PERGUNTAS_IA_COMPATIBILITY_PROFILE_BY_TARGET_TYPE,
            "continuity_types": sorted(ML_PERGUNTAS_IA_CONTINUIDADE_TIPOS),
            "continuity_inherited_iff_continuation": True,
            "no_local_inference_or_reclassification": True,
            "contract_repair_attempts": 1,
            "semantic_continuity_repair_attempts": 1,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


class _PerguntasIAContratoClassificacaoInvalido(PerguntasIARespostaIndisponivel):
    """Internal, parseable classification that violated the canonical contract."""

    def __init__(self, campo: str, violation_code: str):
        super().__init__(
            f"Classificacao de intencao da IA em formato invalido: {campo}."
        )
        self.violation_code = violation_code


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


def _perguntas_ia_schema_invalido(campo: str) -> None:
    codigos = {
        "coerencia de compatibilidade": "compatibility_applicable_iff_category",
        "tipo e perfil da compatibilidade": "compatibility_target_profile_required",
        "par tipo e perfil da compatibilidade": "compatibility_target_profile_pair",
        "compatibilidade nao aplicavel nao pode definir alvo ou perfil": "compatibility_not_applicable_fields",
        "compatibilidade.target_type": "compatibility_target_type",
        "compatibilidade.compatibility_profile": "compatibility_profile",
        "continuidade": "continuity_object_required",
        "continuidade.tipo": "continuity_type",
        "continuidade.herdou_historico": "continuity_inherited_flag",
        "coerencia da continuidade": "continuity_inherited_iff_continuation",
        "objeto JSON obrigatorio": "classification_object_required",
    }
    raise _PerguntasIAContratoClassificacaoInvalido(
        campo,
        codigos.get(campo, "classification_contract_violation"),
    )


def _perguntas_ia_intencao_normalizar(
    data: object,
    *,
    exigir_continuidade: bool = False,
) -> dict:
    """Valida o contrato canonico da IA sem inferir ou reclassificar assunto."""
    if not isinstance(data, dict):
        _perguntas_ia_schema_invalido("objeto JSON ausente")

    intencao = data.get("intencao")
    if not isinstance(intencao, str) or intencao.strip() not in ML_PERGUNTAS_IA_INTENCOES:
        _perguntas_ia_schema_invalido("intencao")
    intencao = intencao.strip()

    categoria = data.get("categoria")
    if not isinstance(categoria, str) or categoria.strip() not in ML_PERGUNTAS_IA_CATEGORIAS:
        _perguntas_ia_schema_invalido("categoria")
    categoria = categoria.strip()

    categorias_brutas = data.get("categorias")
    if not isinstance(categorias_brutas, list) or not categorias_brutas:
        _perguntas_ia_schema_invalido("categorias")
    categorias: list[str] = []
    for valor in categorias_brutas:
        if not isinstance(valor, str) or valor.strip() not in ML_PERGUNTAS_IA_CATEGORIAS:
            _perguntas_ia_schema_invalido("categorias")
        valor = valor.strip()
        if valor in categorias:
            _perguntas_ia_schema_invalido("categorias duplicadas")
        categorias.append(valor)
    if categoria not in categorias:
        _perguntas_ia_schema_invalido("categoria principal ausente de categorias")

    categorias_permitidas = ML_PERGUNTAS_IA_INTENCAO_CATEGORIAS[intencao]
    if categoria not in categorias_permitidas or not set(categorias).issubset(categorias_permitidas):
        _perguntas_ia_schema_invalido("coerencia semantica entre intencao e categorias")

    fluxo = data.get("fluxo")
    if not isinstance(fluxo, str) or fluxo.strip() not in ML_PERGUNTAS_IA_FLUXOS:
        _perguntas_ia_schema_invalido("fluxo")
    fluxo = fluxo.strip()
    intencao_pos_venda = intencao in ML_PERGUNTAS_IA_INTENCOES_POS_VENDA
    categoria_pos_venda = categoria == QuestionCategory.POST_SALE.value
    categorias_pos_venda = QuestionCategory.POST_SALE.value in categorias
    if (
        (fluxo == "pos_venda") != intencao_pos_venda
        or categoria_pos_venda != intencao_pos_venda
        or categorias_pos_venda != intencao_pos_venda
    ):
        _perguntas_ia_schema_invalido("coerencia entre intencao, categoria e fluxo")

    continuidade_bruta = data.get("continuidade")
    if continuidade_bruta is None and not exigir_continuidade:
        continuidade_bruta = {
            "tipo": "inconclusiva" if categoria == QuestionCategory.UNKNOWN.value else "independente",
            "herdou_historico": False,
        }
    if not isinstance(continuidade_bruta, dict):
        _perguntas_ia_schema_invalido("continuidade")
    continuidade_tipo = continuidade_bruta.get("tipo")
    herdou_historico = continuidade_bruta.get("herdou_historico")
    if (
        not isinstance(continuidade_tipo, str)
        or continuidade_tipo.strip() not in ML_PERGUNTAS_IA_CONTINUIDADE_TIPOS
    ):
        _perguntas_ia_schema_invalido("continuidade.tipo")
    continuidade_tipo = continuidade_tipo.strip()
    if not isinstance(herdou_historico, bool):
        _perguntas_ia_schema_invalido("continuidade.herdou_historico")
    if herdou_historico != (continuidade_tipo == "continuacao"):
        _perguntas_ia_schema_invalido("coerencia da continuidade")

    confianca = data.get("confianca")
    if isinstance(confianca, bool) or not isinstance(confianca, (int, float)):
        _perguntas_ia_schema_invalido("confianca")
    confianca = float(confianca)
    if not math.isfinite(confianca) or not 0.0 <= confianca <= 1.0:
        _perguntas_ia_schema_invalido("confianca")

    flags_brutas = data.get("flags")
    if not isinstance(flags_brutas, dict):
        _perguntas_ia_schema_invalido("flags")
    flags: dict[str, bool] = {}
    for chave in ML_PERGUNTAS_IA_FLAGS:
        valor = flags_brutas.get(chave)
        if not isinstance(valor, bool):
            _perguntas_ia_schema_invalido(f"flags.{chave}")
        flags[chave] = valor
    if fluxo == "pos_venda" and any(flags.values()):
        _perguntas_ia_schema_invalido("flags de pos-venda")

    subperguntas_brutas = data.get("subperguntas")
    if not isinstance(subperguntas_brutas, list) or not subperguntas_brutas:
        _perguntas_ia_schema_invalido("subperguntas")
    subperguntas: list[dict] = []
    categorias_cobertas: set[str] = set()
    for indice, valor in enumerate(subperguntas_brutas, start=1):
        if not isinstance(valor, dict):
            _perguntas_ia_schema_invalido(f"subperguntas[{indice}]")
        sub_intent = valor.get("intent")
        sub_question = valor.get("question")
        required_evidence = valor.get("required_evidence")
        if not isinstance(sub_intent, str) or sub_intent.strip() not in ML_PERGUNTAS_IA_SUBQUESTION_INTENTS:
            _perguntas_ia_schema_invalido(f"subperguntas[{indice}].intent")
        if not isinstance(sub_question, str) or not sub_question.strip():
            _perguntas_ia_schema_invalido(f"subperguntas[{indice}].question")
        if not isinstance(required_evidence, str) or not required_evidence.strip():
            _perguntas_ia_schema_invalido(f"subperguntas[{indice}].required_evidence")
        sub_intent = sub_intent.strip()
        if sub_intent == "general":
            categorias_gerais = set(categorias) & ML_PERGUNTAS_IA_GENERAL_CATEGORIES
            if not categorias_gerais:
                _perguntas_ia_schema_invalido(f"subperguntas[{indice}].intent inconsistente")
            categorias_cobertas.update(categorias_gerais)
        else:
            categoria_esperada = ML_PERGUNTAS_IA_SUBQUESTION_CATEGORY[sub_intent]
            if categoria_esperada not in categorias:
                _perguntas_ia_schema_invalido(f"subperguntas[{indice}].intent inconsistente")
            categorias_cobertas.add(categoria_esperada)
        subperguntas.append({
            "intent": sub_intent,
            "question": sub_question.strip()[:500],
            "required_evidence": required_evidence.strip()[:500],
        })
    if categorias_cobertas != set(categorias):
        _perguntas_ia_schema_invalido("subperguntas nao cobrem todas as categorias")

    compatibilidade_bruta = data.get("compatibilidade")
    if not isinstance(compatibilidade_bruta, dict):
        _perguntas_ia_schema_invalido("compatibilidade")
    aplicavel = compatibilidade_bruta.get("aplicavel")
    if not isinstance(aplicavel, bool):
        _perguntas_ia_schema_invalido("compatibilidade.aplicavel")
    target_item = compatibilidade_bruta.get("target_item")
    target_type = compatibilidade_bruta.get("target_type")
    compatibility_profile = compatibilidade_bruta.get("compatibility_profile")
    technical_focus = compatibilidade_bruta.get("technical_focus")
    missing_fields = compatibilidade_bruta.get("missing_fields")
    decisive_fields = compatibilidade_bruta.get("decisive_fields")
    if not isinstance(target_item, str):
        _perguntas_ia_schema_invalido("compatibilidade.target_item")
    if not isinstance(target_type, str) or target_type.strip() not in ML_PERGUNTAS_IA_COMPATIBILITY_TARGET_TYPES:
        _perguntas_ia_schema_invalido("compatibilidade.target_type")
    if not isinstance(compatibility_profile, str) or compatibility_profile.strip() not in ML_PERGUNTAS_IA_COMPATIBILITY_PROFILES:
        _perguntas_ia_schema_invalido("compatibilidade.compatibility_profile")
    if not isinstance(technical_focus, str):
        _perguntas_ia_schema_invalido("compatibilidade.technical_focus")
    if not isinstance(missing_fields, list) or any(not isinstance(item, str) or not item.strip() for item in missing_fields):
        _perguntas_ia_schema_invalido("compatibilidade.missing_fields")
    if not isinstance(decisive_fields, list) or any(not isinstance(item, str) or not item.strip() for item in decisive_fields):
        _perguntas_ia_schema_invalido("compatibilidade.decisive_fields")
    tem_categoria_compatibilidade = QuestionCategory.COMPATIBILITY.value in categorias
    if aplicavel != tem_categoria_compatibilidade:
        _perguntas_ia_schema_invalido("coerencia de compatibilidade")
    if aplicavel and (not target_type.strip() or not compatibility_profile.strip()):
        _perguntas_ia_schema_invalido("tipo e perfil da compatibilidade")
    if aplicavel and (
        ML_PERGUNTAS_IA_COMPATIBILITY_PROFILE_BY_TARGET_TYPE.get(target_type.strip())
        != compatibility_profile.strip()
    ):
        _perguntas_ia_schema_invalido("par tipo e perfil da compatibilidade")
    if not aplicavel and (
        target_item.strip() or target_type.strip() or compatibility_profile.strip()
        or missing_fields
    ):
        _perguntas_ia_schema_invalido("compatibilidade nao aplicavel nao pode definir alvo ou perfil")

    normalizada = {
        "intencao": intencao,
        "categoria": categoria,
        "categorias": categorias,
        "fluxo": fluxo,
        "confianca": confianca,
        "continuidade": {
            "tipo": continuidade_tipo,
            "herdou_historico": herdou_historico,
        },
        "flags": flags,
        "subperguntas": subperguntas,
        "compatibilidade": {
            "aplicavel": aplicavel,
            "target_item": target_item.strip()[:200],
            "target_type": target_type.strip(),
            "compatibility_profile": compatibility_profile.strip(),
            "technical_focus": technical_focus.strip()[:500],
            "missing_fields": [item.strip()[:200] for item in missing_fields[:12]],
            "decisive_fields": [item.strip()[:200] for item in decisive_fields[:12]],
        },
        **flags,
    }
    for chave, limite in (("motivo", 500), ("acao", 500), ("source", 80), ("model", 120)):
        if chave in data:
            valor = data.get(chave)
            if not isinstance(valor, str):
                _perguntas_ia_schema_invalido(chave)
            normalizada[chave] = valor.strip()[:limite]
    return normalizada


def _perguntas_ia_classification_prompt(
    entrada: dict,
    *,
    violation_code: str = "",
    continuity_repair: bool = False,
) -> str:
    mapa_perfis = ", ".join(
        f"{target_type}={profile}"
        for target_type, profile in ML_PERGUNTAS_IA_COMPATIBILITY_PROFILE_BY_TARGET_TYPE.items()
    )
    reparo = ""
    if violation_code:
        reparo = (
            "A resposta anterior era um objeto JSON parseavel, mas violou o contrato canonico "
            f"no codigo {violation_code}. Gere uma classificacao nova a partir dos Dados; "
            "nao tente reproduzir nem completar a resposta anterior. "
        )
    reparo_continuidade = ""
    if continuity_repair:
        reparo_continuidade = (
            "A classificacao semantica anterior terminou como nao_entendi. "
            "Reanalise uma unica vez se a mensagem atual e continuacao do assunto ativo no historico. "
            "Nao copie nem presuma a classificacao anterior. Se o historico nao resolver o assunto, "
            "mantenha nao_entendi e continuidade.tipo=inconclusiva. "
        )
    return (
        f"Contrato interno: {ML_PERGUNTAS_IA_CLASSIFICATION_CONTRACT_VERSION}; "
        f"hash={ML_PERGUNTAS_IA_CLASSIFICATION_CONTRACT_HASH}. "
        + reparo
        + reparo_continuidade
        + "Classifique pela IA o turno atual do comprador do Mercado Livre no contexto da conversa. "
        "A mensagem atual continua sendo o turno classificado, mas o historico deve resolver referencias, "
        "respostas curtas e continuacoes que nao repetem o assunto. "
        "Se o comprador apenas explica que ainda nao conferiu, desmontou ou obteve um dado solicitado pela loja, "
        "herde o assunto ativo da pergunta anterior e reformule subperguntas com os dados ja presentes no historico. "
        "Nao invente fatos e nao herde assunto quando a mensagem atual iniciar tema novo. "
        "Se o comprador diz que ja comprou, recebeu, quer trocar, relata defeito, problema, item apagando, quebrado, nao funciona, entrega ou garantia, classifique como pos-venda. "
        "Nao confunda relato de defeito pos-compra com compatibilidade do produto. Classifique lateralidade, lado esquerdo/direito ou lado especifico como product_feature, salvo quando a pergunta realmente comparar aplicacao em outro alvo. "
        "Retorne somente JSON valido, sem markdown e exatamente com o contrato pedido. "
        "intencao deve ser uma de: duvida_produto, compatibilidade, outra_peca, preco_estoque, pos_venda_defeito, troca_garantia, entrega, cancelamento, reclamacao, nao_entendi. "
        "categoria deve ser uma de: greeting, price, stock, shipping, compatibility, product_feature, warranty_originality, invoice, other_product, prohibited_contact, regulated_product, post_sale, unknown. "
        "categorias deve ser uma lista sem repeticao dessas categorias e deve conter categoria. "
        "Mantenha coerencia semantica estrita: duvida_produto aceita somente greeting, shipping, product_feature, warranty_originality, invoice, prohibited_contact ou regulated_product; "
        "compatibilidade aceita somente compatibility; outra_peca aceita somente other_product; preco_estoque aceita somente price e/ou stock; "
        "pos_venda_defeito, troca_garantia, entrega, cancelamento e reclamacao aceitam somente post_sale; nao_entendi aceita somente unknown. "
        "fluxo deve ser perguntas_anuncio ou pos_venda. confianca deve ser numero entre 0 e 1. "
        "continuidade deve conter somente tipo e herdou_historico. tipo deve ser independente, continuacao, novo_assunto ou inconclusiva. "
        "herdou_historico deve ser true se, e somente se, tipo for continuacao. "
        "Use continuacao quando a mensagem atual depende semanticamente do historico; use novo_assunto quando ela troca o tema; "
        "use independente quando ela se sustenta sozinha; use inconclusiva quando nem o historico permite identificar o assunto. "
        "flags deve conter os booleanos usar_busca_web, usar_mercado_livre_anuncio e usar_bling. Em pos_venda todos devem ser false. "
        "subperguntas deve ser uma lista nao vazia de objetos somente com intent, question e required_evidence. "
        "intent deve ser um de: compatibility, shipping, stock, price, invoice, warranty_originality, product_feature, other_product, general, post_sale. "
        "Cada categoria deve ser coberta por uma subpergunta semanticamente correspondente e nenhuma subpergunta pode introduzir categoria ausente. Nao invente assunto ausente. "
        "compatibilidade deve conter aplicavel, target_item, target_type, compatibility_profile, technical_focus, missing_fields e decisive_fields. "
        "REGRA IFF OBRIGATORIA: compatibilidade.aplicavel deve ser true se, e somente se, categorias contiver compatibility. "
        "aplicavel=true significa que a pergunta exige analise de compatibilidade; nao significa que o produto serve. "
        "target_type deve ser vazio ou vehicle, machine_tool, phone_computing, electrical_electronic, hydraulic, dimensional, generic. "
        "Quando aplicavel=true, target_type e compatibility_profile devem formar exatamente um destes pares: "
        f"{mapa_perfis}. "
        "Nao invente target_item ausente e nao use generic como correcao automatica de um alvo desconhecido; descreva os dados decisivos ausentes em missing_fields. "
        "Quando compatibilidade.aplicavel for false, target_item, target_type, compatibility_profile e missing_fields devem estar vazios; technical_focus e decisive_fields podem descrever a caracteristica tecnica pedida. "
        "target_item e technical_focus devem conter somente o alvo e termos tecnicos; nunca copie nome do comprador, telefone, e-mail, pedido, endereco, CEP, placa, chassi ou VIN para esses campos. "
        "Exemplo valido de compatibilidade independente, sem copiar os valores: {\"intencao\":\"compatibilidade\",\"categoria\":\"compatibility\",\"categorias\":[\"compatibility\"],\"fluxo\":\"perguntas_anuncio\",\"confianca\":0.95,\"continuidade\":{\"tipo\":\"independente\",\"herdou_historico\":false},\"flags\":{\"usar_busca_web\":true,\"usar_mercado_livre_anuncio\":true,\"usar_bling\":true},\"subperguntas\":[{\"intent\":\"compatibility\",\"question\":\"Serve no Honda Civic 2008?\",\"required_evidence\":\"codigo e interface decisiva\"}],\"compatibilidade\":{\"aplicavel\":true,\"target_item\":\"Honda Civic 2008\",\"target_type\":\"vehicle\",\"compatibility_profile\":\"vehicle_fitment\",\"technical_focus\":\"codigo e encaixe\",\"missing_fields\":[\"codigo OEM\"],\"decisive_fields\":[\"codigo OEM\",\"conector\"]}}. "
        "Exemplo de continuidade: se a pergunta anterior era sobre servir em um veiculo e a loja pediu o codigo da peca, "
        "a resposta atual 'ainda nao desmontei para ver o codigo' continua sendo compatibilidade; use continuidade.tipo=continuacao, "
        "herdou_historico=true e escreva a subpergunta completa com o veiculo ja informado. "
        "Exemplo valido sem compatibilidade, sem copiar os valores: {\"intencao\":\"duvida_produto\",\"categoria\":\"product_feature\",\"categorias\":[\"product_feature\"],\"fluxo\":\"perguntas_anuncio\",\"confianca\":0.95,\"continuidade\":{\"tipo\":\"independente\",\"herdou_historico\":false},\"flags\":{\"usar_busca_web\":false,\"usar_mercado_livre_anuncio\":true,\"usar_bling\":true},\"subperguntas\":[{\"intent\":\"product_feature\",\"question\":\"pergunta objetiva\",\"required_evidence\":\"atributo do anuncio ou fonte tecnica\"}],\"compatibilidade\":{\"aplicavel\":false,\"target_item\":\"\",\"target_type\":\"\",\"compatibility_profile\":\"\",\"technical_focus\":\"\",\"missing_fields\":[],\"decisive_fields\":[]}}.\n\n"
        f"Dados:\n{json.dumps(entrada, ensure_ascii=False, default=str)[:6000]}"
    )


def _perguntas_ia_classificacao_parsear(texto: str) -> dict:
    data = _perguntas_ia_json_obj(texto)
    if not data:
        bruto = str(texto or "").strip()
        try:
            candidato = json.loads(bruto)
        except (TypeError, ValueError):
            raise _PerguntasIAContratoClassificacaoInvalido(
                "Classificacao de intencao da IA sem objeto JSON parseavel.",
                violation_code="classification_json_parseable",
            )
        if not isinstance(candidato, dict):
            _perguntas_ia_schema_invalido("objeto JSON obrigatorio")
        data = candidato
    return _perguntas_ia_intencao_normalizar(data, exigir_continuidade=True)


def _perguntas_ia_provider_failure_code(exc: BaseException) -> str:
    """Return only allowlisted transient-provider failures, without message matching."""

    current: Optional[BaseException] = exc
    visited: set[int] = set()
    while isinstance(current, BaseException) and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, (TimeoutError, requests.exceptions.Timeout)):
            return "provider_timeout"
        if isinstance(
            current,
            (ConnectionError, BrokenPipeError, requests.exceptions.ConnectionError),
        ):
            return "provider_connection"
        status_code = getattr(current, "status_code", None)
        if status_code is None:
            response = getattr(current, "response", None)
            status_code = getattr(response, "status_code", None)
        try:
            normalized_status = int(status_code or 0)
        except (TypeError, ValueError):
            normalized_status = 0
        if normalized_status == 429:
            return "provider_http_429"
        if 500 <= normalized_status <= 599:
            return "provider_http_5xx"
        current = current.__cause__ or current.__context__
    return ""


def _perguntas_ia_mensagem_pos_venda_evidente(texto: object) -> bool:
    normalizado = _ml_question_normalize(texto)
    if re.search(
        r"\b(?:ja\s+comprei|comprei|fiz\s+a\s+compra|realizei\s+a\s+compra|"
        r"efetuei\s+a\s+compra|recebi|devolv\w*|defeito\w*|quebrad\w*|"
        r"nao\s+funciona|parou\s+de\s+funcionar|reembolso|cancelar\s+(?:a\s+)?compra)\b",
        normalizado,
    ):
        return True
    if re.search(
        r"\b(?:meu|minha)\s+(?:peca|produto|item|pedido)\s+"
        r"(?:ja\s+)?(?:chegou|foi\s+entregue)\b",
        normalizado,
    ) or re.search(
        r"\b(?:a\s+|o\s+)?(?:peca|produto|item|pedido)\s+"
        r"(?:ja\s+)?(?:chegou|foi\s+entregue)\b.{0,80}\b(?:e|mas)\s+"
        r"(?:eu\s+)?(?:ainda\s+)?nao\s+(?:conferi|testei|instalei|abri)\b",
        normalizado,
    ):
        return True
    if re.search(
        r"\b(?:pedido|compra|produto|peca|item)\b.{0,50}\b(?:chegou|veio|recebi)\b"
        r".{0,50}\b(?:errad\w*|danific\w*|defeito\w*|quebrad\w*|faltando)\b",
        normalizado,
    ):
        return True
    if re.search(
        r"\b(?:ja\s+)?(?:instalei|montei|coloquei)\b.{0,70}"
        r"\b(?:nao\s+(?:encaix\w*|serv\w*|funcion\w*)|ficou\s+(?:folgad\w*|apertad\w*))\b",
        normalizado,
    ):
        return True
    if re.search(
        r"\b(?:produto|peca|item)\b.{0,45}\b(?:veio|mandaram|enviaram|recebi)\b"
        r".{0,70}\b(?:diferente|divergente|errad\w*|nao\s+(?:serv\w*|encaix\w*)|"
        r"nao\s+corresponde\w*)\b",
        normalizado,
    ) or re.search(
        r"\b(?:mandaram|enviaram)\b.{0,45}\b(?:produto|peca|item)\b"
        r".{0,70}\b(?:diferente|divergente|errad\w*|nao\s+(?:serv\w*|encaix\w*))\b",
        normalizado,
    ):
        return True
    if re.search(
        r"\b(?:meu\s+pedido|minha\s+compra|minha\s+entrega|a\s+entrega)\b"
        r".{0,70}\b(?:nao\s+cheg\w*|atrasad\w*|rastre\w*)\b",
        normalizado,
    ):
        return True
    if re.search(
        r"\b(?:chegou|recebi|esta\s+comigo|ja\s+esta\s+comigo)\b.{0,70}"
        r"\b(?:nao\s+(?:serv\w*|encaix\w*|funcion\w*|deu\s+certo)|"
        r"veio\s+(?:errad\w*|diferente)|deu\s+errad\w*|"
        r"ficou\s+(?:grand\w*|pequen\w*|folgad\w*|apertad\w*)|"
        r"incomplet\w*|faltando|avariad\w*)\b",
        normalizado,
    ):
        return True
    if re.search(
        r"\b(?:produto|peca|item|pedido|bomba)\b.{0,35}\b(?:esta|ficou)\s+(?:aqui|comigo)\b"
        r".{0,55}\b(?:nao\s+(?:serv\w*|encaix\w*|funcion\w*)|"
        r"incomplet\w*|faltando|avariad\w*)\b|"
        r"^\s*(?:veio|chegou)\b.{0,60}\b(?:faltando|incomplet\w*|avariad\w*)\b",
        normalizado,
    ):
        return True
    if re.search(
        r"\b(?:acionar|usar|solicitar)\s+(?:a\s+)?garantia\b|\b(?:pela|em)\s+garantia\b",
        normalizado,
    ):
        return True
    return bool(
        re.search(
            r"\b(?:quero|preciso|posso|como)\s+trocar\b.{0,60}"
            r"\b(?:produto|item|pedido|que\s+recebi)\b|\btroca\s+ou\s+devolucao\b",
            normalizado,
        )
    )


def _perguntas_ia_prompt_injection_evidente(texto: object) -> bool:
    normalizado = _ml_question_normalize(texto)
    if QuestionClassifier._has_prompt_injection(normalizado):
        return True
    return bool(
        re.search(
            r"\b(?:desconsidere|ignore|esqueca|esqueça|anule|substitua)\b.{0,80}"
            r"\b(?:instrucoes|instruções|regras|orientacoes|orientações|prompt|sistema|"
            r"descricao|descrição|anuncio|anúncio|historico|histórico|evidencia|evidência|"
            r"resposta|tudo|acima)\b",
            normalizado,
        )
        or re.search(
            r"\bsiga\s+(?:as\s+)?minhas\s+instrucoes\b.{0,100}"
            r"\b(?:diga|responda|confirme)\b",
            normalizado,
        )
        or re.search(
            r"\b(?:diga|responda)\b.{0,45}\b(?:que\s+serve|sim\s*,?\s*serve)\b|"
            r"\bconfirme\s+que\s+serve\b|"
            r"\bsubstitua\s+(?:a\s+)?resposta\b.{0,45}\bsim\b|"
            r"\bresponda\s+apenas\s+sim\b|\bdiga\s+sim\b|"
            r"\b(?:sua\s+)?resposta\s+deve\s+ser\s+sim\b",
            normalizado,
        )
    )


_PERGUNTAS_IA_CONTINUATION_NON_TARGET_TOKENS = frozenset({
    "a", "as", "o", "os", "um", "uma", "de", "da", "do", "das", "dos", "e",
    "em", "na", "no", "nas", "nos", "ao", "aos", "para", "por", "com", "sem",
    "serve", "servir", "servem", "compativel", "compatibilidade", "aplica", "aplicacao",
    "modelo", "carro", "veiculo", "meu", "minha", "seu", "sua", "este", "esta", "esse",
    "essa", "peca", "produto", "anuncio", "anunciada", "anunciado", "bomba", "filtro",
    "combustivel", "original", "codigo", "codigos", "oem", "interface", "encaixe", "conector",
    "voltagem", "tensao", "medida", "medidas", "dimensao", "dimensoes", "rosca", "pino", "pinos", "vias",
    "lado", "conexao", "ligacao",
    "npt", "material", "composicao", "cor", "marca", "fabricante", "whatsapp", "telefone",
    "alvo", "informado", "informada", "pergunta", "bom", "boa", "dia", "tarde", "noite",
    "ola", "amigo", "amiga", "quero", "gostaria", "preciso", "saber", "qual", "quais",
    "pode", "posso", "antes", "ainda", "conferi", "conferir", "desmontei", "desmontar",
    "agora", "verdade", "corrigindo", "correcao", "mudando", "assunto", "mas", "eh",
    "nao", "ja", "pois", "vai", "oficina", "comprar", "compra", "faz", "fazer", "possivel",
    "responda", "diga", "apenas", "sim", "acompanha", "inclui", "vem", "potencia", "peso",
    "confirmar", "confirma", "entao", "isso", "exato", "certo", "entendi",
    "quis", "dizer", "realidade", "correto", "corrijo", "enganei", "era", "troque",
    "chassi", "chassis", "vin", "final", "ano", "anos", "dynamic", "black", "se", "hse",
    "gasolina", "diesel", "flex", "etanol", "alcool", "gnv", "hibrido", "eletrico",
    "automatico", "automatica", "manual", "automatizado", "cvt", "direito", "direita",
    "esquerdo", "esquerda", "dianteiro", "dianteira", "traseiro", "traseira", "motor",
    "audi", "bmw", "chevrolet", "citroen", "fiat", "ford", "honda", "hyundai", "jeep",
    "kia", "land", "range", "rover", "mercedes", "mitsubishi", "nissan", "peugeot",
    "renault", "subaru", "suzuki", "toyota", "volkswagen", "volvo",
})


def _perguntas_ia_continuation_negated_identity(texto: object) -> set[str]:
    normalizado = _ml_question_normalize(texto)
    captures = []
    subject = (
        r"(?:(?:o|esse|este)\s+modelo|(?:meu|esse|este)\s+(?:carro|veiculo)|"
        r"(?:a|essa|esta)\s+peca|(?:o|esse|este)\s+produto)"
    )
    for pattern in (
        rf"^\s*(?:{subject}\s+)?nao\s+(?:e|eh)\s+(?:para\s+)?([^;,.?]+)",
        rf"^\s*(?:{subject}\s+)?nao\s+serve\s+(?:no|na|para|em)\s+([^;,.?]+)",
        rf"^\s*(?:{subject}\s+)?nao\s+corresponde\s+(?:a|ao|para)\s+([^;,.?]+)",
        rf"^\s*(?:{subject}\s+)?nao\s+(?:e|eh)\s+compativel\s+com\s+([^;,.?]+)",
        r"^\s*nao\s+([^,;]+),",
        r",\s*(?:e\s+)?nao\s+([^,;.!?]+)",
    ):
        match = re.search(pattern, normalizado)
        if match:
            captures.append(str(match.group(1) or ""))
    return {
        token
        for capture in captures
        for token in re.findall(r"\b[a-z0-9]{2,}\b", capture)
        if token not in _PERGUNTAS_IA_CONTINUATION_NON_TARGET_TOKENS
        and not re.fullmatch(r"(?:19|20)\d{2}", token)
        and not re.fullmatch(r"\d+(?:[.,]\d+)?", token)
    }


def _perguntas_ia_texto_autoritativo_atual(texto: object) -> str:
    """Isolate the positive side of an explicit buyer correction when possible."""

    normalizado = _ml_question_normalize(texto)
    for pattern in (
        r"\bna\s+verdade\b\s*(?:e|eh)?\s*(.+)$",
        r"\b(?:corrigindo|correcao)\b\s*[:,-]?\s*(?:e|eh)?\s*(.+)$",
        r"\b(?:eu\s+)?quis\s+dizer\b\s*[:,-]?\s*(?:e|eh)?\s*(.+)$",
        r"\bna\s+realidade\b\s*[:,-]?\s*(?:e|eh)?\s*(.+)$",
        r"\bo\s+correto\s+(?:e|eh)\b\s*(.+)$",
        r"\bcorrijo\b\s*[:,-]?\s*(?:e|eh)?\s*(.+)$",
        r"\bme\s+enganei\b\s*[:,-]?\s*(?:e|eh)?\s*(.+)$",
        r"\bo\s+meu\s+(?:e|eh)\b\s*(.+)$",
        r"\bera\b\s*(.+?)(?:,\s*(?:e\s+)?nao\b|$)",
        r"\btroque\s+para\b\s*(.+)$",
        r"\bmudando\s+de\s+assunto\b\s*[:,-]?\s*(.+)$",
        r"\bagora\s+(?:e|eh)\s+para\b\s*(.+)$",
        r"\bnao\s+(?:e|eh)\b[^;,]*(?:;|,\s*(?:mas\s+)?)(?:e|eh)?\s*(.+)$",
    ):
        match = re.search(pattern, normalizado)
        if match and str(match.group(1) or "").strip():
            return str(match.group(1)).strip()
    if not _perguntas_ia_continuation_has_negated_qualifier(normalizado):
        for pattern in (
            r"^\s*(.+?),\s*(?:e\s+)?nao\s+[a-z0-9].*$",
            r"^\s*nao\s+[a-z0-9][^,]*,\s*(.+)$",
        ):
            match = re.search(pattern, normalizado)
            if match and str(match.group(1) or "").strip():
                return str(match.group(1)).strip()
    return normalizado


def _perguntas_ia_mudanca_explicita(texto: object) -> bool:
    normalizado = _ml_question_normalize(texto)
    technical_negation = _perguntas_ia_continuation_has_negated_qualifier(normalizado)
    return bool(
        re.search(
            r"\b(?:na\s+verdade|corrigindo|correcao|mudando\s+de\s+assunto|"
            r"outro\s+assunto|agora\s+(?:e|eh)\s+para|(?:eu\s+)?quis\s+dizer|"
            r"na\s+realidade|o\s+correto\s+(?:e|eh)|corrijo|me\s+enganei|"
            r"o\s+meu\s+(?:e|eh)|troque\s+para)\b",
            normalizado,
        )
        or (not technical_negation and re.search(
            r"\bera\b.{0,60},\s*(?:e\s+)?nao\b|"
            r"^\s*[^,]+,\s*(?:e\s+)?nao\s+[^,]+$|"
            r"^\s*nao\s+[^,]+,\s*[^,]+$",
            normalizado,
        ))
        or re.search(
            r"\bnao\s+(?:e|eh)\b[^;,]{1,80}"
            r"(?:;|,\s*(?:mas\s+)?|\s+mas\s+)(?:e|eh)?\s*[a-z0-9]",
            normalizado,
        )
    )


def _perguntas_ia_assunto_atual_autossuficiente(texto: object) -> bool:
    """Detect explicit new public-question subjects without guessing vague turns."""

    bruto = str(texto or "")
    normalizado = _ml_question_normalize(texto)
    request_like = bool(
        "?" in bruto
        or re.search(
            r"^\s*(?:qual|quais|quanto|quantos|quanta|quantas|tem|possui|emite|"
            r"acompanha|inclui|vem|informe|pode\s+informar)\b|"
            r"\b(?:quero|gostaria|preciso)\s+saber\b",
            normalizado,
        )
    )
    if not request_like:
        return False
    return bool(
        re.search(
            r"\b(?:prazo|tempo)\b.{0,30}\b(?:entrega|envio|chegar|demora)\b|"
            r"\b(?:frete|rastreio|rastreamento)\b|"
            r"\b(?:tem|possui|qual|quanto\s+tempo\s+de)\s+garantia\b|"
            r"\bgarantia\s+(?:de|e|eh)\b|"
            r"\bquantas?\s+(?:unidades?|pecas?|itens?)\b|"
            r"\b(?:kit|jogo|par)\b.{0,24}\b(?:vem|inclui|acompanha)\b|"
            r"\b(?:qual|informe|tem|possui|e|eh)\s+(?:a\s+)?(?:voltagem|tensao)\b|"
            r"\btem\s+(?:110|127|220|12|24)\s*v(?:olts?)?\b|"
            r"\b(?:qual|quanto|tem)\s+(?:o\s+)?(?:preco|valor|estoque)\b|"
            r"\b(?:preco|valor|estoque)\s+(?:do|da|desse|dessa|disponivel)\b|"
            r"\b(?:material|composicao|cor|medida|medidas|dimensao|dimensoes|tamanho)\b|"
            r"\b(?:qual\s+)?(?:lado|marca|fabricante)\b|"
            r"^\s*(?:e|eh)\s+(?:original|genuino|genuina|paralelo|paralela)\b|"
            r"\b(?:produto|peca|item)\b.{0,18}\b(?:original|genuino|genuina|paralelo|paralela)\b|"
            r"\b(?:emite|emitem|tem|possui|acompanha)\b.{0,24}\bnota\s+fiscal\b|"
            r"\b(?:nota\s+fiscal|nf-e|nfe|cnpj)\b",
            normalizado,
        )
    )


def _perguntas_ia_continuation_negated_qualifiers(texto: object) -> dict[str, set[str]]:
    normalizado = _ml_question_normalize(texto).replace(",", ".")
    patterns = {
        "fuel": {
            "gasolina": r"gasolina",
            "diesel": r"diesel",
            "flex": r"flex",
            "etanol": r"etanol",
            "alcool": r"alcool",
            "gnv": r"gnv",
            "hibrido": r"hibrido",
            "eletrico": r"eletrico",
        },
        "displacement": {
            re.sub(r"\s+", "", value): re.escape(value)
            for value in re.findall(r"\b\d\s*\.\s*\d\b", normalizado)
        },
        "transmission": {
            "automatico": r"automatic[oa]s?",
            "manual": r"manual(?:is)?",
            "automatizado": r"automatizad[oa]s?",
            "cvt": r"cvt",
        },
        "side": {
            "direito": r"direit[oa]s?",
            "esquerdo": r"esquerd[oa]s?",
            "dianteiro": r"dianteir[oa]s?",
            "traseiro": r"traseir[oa]s?",
        },
        "engine": {
            value: re.escape(value)
            for value in re.findall(r"\b(?:v[468]|\d{1,2}v)\b", normalizado)
        },
    }
    result: dict[str, set[str]] = {category: set() for category in patterns}
    prefix = r"(?:nao|sem|exceto)\s+(?:(?:e|eh|usa|serve|tem|para)\s+)?(?:motor\s+)?"
    suffix = r"\s+(?:nao|excluido|excluida)"
    for category, values in patterns.items():
        for canonical, pattern in values.items():
            if re.search(rf"\b{prefix}{pattern}\b", normalizado) or re.search(
                rf"\b{pattern}{suffix}\b",
                normalizado,
            ):
                result[category].add(canonical)
    return result


def _perguntas_ia_continuation_has_negated_qualifier(texto: object) -> bool:
    return any(
        values
        for values in _perguntas_ia_continuation_negated_qualifiers(texto).values()
    )


def _perguntas_ia_continuation_decisive_values(texto: object) -> set[str]:
    normalizado = _ml_question_normalize(texto).replace(",", ".")
    values = {
        re.sub(r"\s+", "", value)
        for value in re.findall(
            r"\b(?:\d{2,3}\s*v|m\d{1,3}|[1-9]\s*/\s*[1-9](?:\s*npt)?|"
            r"\d+(?:\.\d+)?\s*x\s*\d+(?:\.\d+)?\s*(?:mm|cm)|"
            r"\d+(?:\.\d+)?\s*(?:mm|cm|bar|psi|pinos?|vias?))\b",
            normalizado,
        )
    }
    values.update(
        re.findall(
            r"\b(?:[a-z]{1,5}\d[a-z0-9]*(?:-[a-z0-9]+)+|[a-z]{1,5}\d{4,}[a-z0-9]*)\b",
            normalizado,
        )
    )
    values.update(
        re.sub(r"\s+", "", value)
        for value in re.findall(r"\b(?:\d{1,3}\s+){3,}\d{2,3}\b", normalizado)
    )
    for match in re.finditer(
        r"\b(?:codigo|oem|referencia|ref)\b(?:\s+original)?\s*(?::|e|eh)?\s*"
        r"([a-z0-9][a-z0-9-]{3,})\b",
        normalizado,
    ):
        values.add(str(match.group(1) or "").strip())
    return {value for value in values if value}


def _perguntas_ia_continuation_facts(texto: object) -> dict:
    normalizado = _ml_question_normalize(texto)
    normalizado = re.sub(
        r"\b(?:chassi|chassis|vin)\b(?:\s+final)?\s*[:#-]?\s*[a-z0-9-]{4,25}\b",
        " ",
        normalizado,
    )
    decisive = _perguntas_ia_continuation_decisive_values(normalizado)
    decisive_components = {
        component
        for value in decisive
        for component in re.findall(r"[a-z0-9]{2,}", value)
    }
    identity = {
        token
        for token in re.findall(r"\b[a-z0-9]{2,}\b", normalizado)
        if token not in _PERGUNTAS_IA_CONTINUATION_NON_TARGET_TOKENS
        and not re.fullmatch(r"(?:19|20)\d{2}", token)
        and not re.fullmatch(r"\d+(?:[.,]\d+)?", token)
        and not re.fullmatch(r"(?:v[468]|\d{1,2}v)", token)
        and not re.fullmatch(r"[a-hj-npr-z0-9]{17}", token)
        and token not in decisive_components
    }
    negated_identity = _perguntas_ia_continuation_negated_identity(normalizado)
    identity.difference_update(negated_identity)
    years = {int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", normalizado)}
    for first, second in re.findall(r"\b(\d{2})\s*/\s*(\d{2})\b", normalizado):
        for value in (first, second):
            numeric = int(value)
            years.add(2000 + numeric if numeric <= 49 else 1900 + numeric)
    normalized_decimal = normalizado.replace(",", ".")
    qualifiers = {
        "fuel": {
            value
            for value in (
                "gasolina", "diesel", "flex", "etanol", "alcool", "gnv", "hibrido", "eletrico",
            )
            if re.search(rf"\b{re.escape(value)}\b", normalizado)
        },
        "displacement": {
            re.sub(r"\s+", "", value)
            for value in re.findall(r"\b\d\s*\.\s*\d\b", normalized_decimal)
        },
        "transmission": {
            canonical
            for pattern, canonical in (
                (r"\bautomatic[oa]s?\b", "automatico"),
                (r"\bmanual(?:is)?\b", "manual"),
                (r"\bautomatizad[oa]s?\b", "automatizado"),
                (r"\bcvt\b", "cvt"),
            )
            if re.search(pattern, normalizado)
        },
        "side": {
            canonical
            for pattern, canonical in (
                (r"\bdireit[oa]s?\b", "direito"),
                (r"\besquerd[oa]s?\b", "esquerdo"),
                (r"\bdianteir[oa]s?\b", "dianteiro"),
                (r"\btraseir[oa]s?\b", "traseiro"),
            )
            if re.search(pattern, normalizado)
        },
        "engine": set(re.findall(r"\b(?:v[468]|\d{1,2}v)\b", normalizado)),
    }
    negated_qualifiers = _perguntas_ia_continuation_negated_qualifiers(normalizado)
    for category, values in negated_qualifiers.items():
        qualifiers[category].difference_update(values)
    return {
        "identity": identity,
        "years": years,
        "qualifiers": qualifiers,
        "negated_qualifiers": negated_qualifiers,
        "negated_identity": negated_identity,
        "decisive": decisive,
    }


def _perguntas_ia_current_facts_explicit(texto: object, facts: dict) -> bool:
    normalizado = _ml_question_normalize(texto)
    if (
        facts.get("years")
        or any((facts.get("qualifiers") or {}).values())
        or any((facts.get("negated_qualifiers") or {}).values())
        or facts.get("negated_identity")
        or facts.get("decisive")
        or _perguntas_ia_mudanca_explicita(texto)
    ):
        return True
    if re.search(
        r"\b(?:serve|servir|compativel|compatibilidade|modelo|carro|veiculo)\b|"
        r"^\s*e\s+(?:o|a|um|uma)?\s*[a-z0-9]",
        normalizado,
    ):
        return True
    identity = set(facts.get("identity") or set())
    if not identity or "?" in str(texto or ""):
        return False
    if re.search(r"\b(?:responda|diga|informe|explique|mostre|envie|mande)\b", normalizado):
        return False
    return len(re.findall(r"\b[a-z0-9]+\b", normalizado)) <= 8


def _perguntas_ia_turno_continuacao_positiva(texto: object) -> bool:
    """Allow history inheritance only for evidenced or explicitly elliptical turns."""

    if (
        _perguntas_ia_prompt_injection_evidente(texto)
        or _perguntas_ia_mensagem_pos_venda_evidente(texto)
        or _perguntas_ia_assunto_atual_autossuficiente(texto)
    ):
        return False
    authoritative = _perguntas_ia_texto_autoritativo_atual(texto)
    facts = _perguntas_ia_continuation_facts(authoritative)
    if facts.get("negated_identity") and not facts.get("identity"):
        return False
    qualifiers = facts.get("qualifiers") if isinstance(facts.get("qualifiers"), dict) else {}
    negated = (
        facts.get("negated_qualifiers")
        if isinstance(facts.get("negated_qualifiers"), dict)
        else {}
    )
    if any(values and not set(qualifiers.get(category) or set()) for category, values in negated.items()):
        return False
    if _perguntas_ia_current_facts_explicit(texto, facts):
        return True
    normalizado = _ml_question_normalize(texto)
    return bool(
        re.search(
            r"\b(?:ainda\s+)?nao\s+(?:conferi|desmontei|verifiquei|comparei|olhei|sei|"
            r"consigo\s+(?:conferir|desmontar|verificar|comparar))\b|"
            r"\bnao\s+(?:e|eh)\s+possivel\s+(?:conferir|desmontar|verificar|comparar)\b|"
            r"\b(?:vai|vou)\s+para\s+(?:a\s+)?oficina\b|"
            r"\b(?:pode|consegue)\s+confirmar(?:\s+(?:entao|agora|isso))?\b|"
            r"^\s*(?:confirma|isso|exato|certo|entendi|e\s+esse|e\s+essa)\s*[?.!]*$",
            normalizado,
        )
    )


def _perguntas_ia_continuation_resolved_facts(
    pergunta_atual: object,
    historico: list[dict],
) -> tuple[dict, dict]:
    """Resolve the latest buyer target, with the current turn overriding older facts."""

    raw_current = _perguntas_ia_continuation_facts(pergunta_atual)
    current = _perguntas_ia_continuation_facts(
        _perguntas_ia_texto_autoritativo_atual(pergunta_atual)
    )
    current["negated_identity"] = set(current.get("negated_identity") or set()).union(
        set(raw_current.get("negated_identity") or set())
    )
    current_negated_qualifiers = (
        current.get("negated_qualifiers")
        if isinstance(current.get("negated_qualifiers"), dict)
        else {}
    )
    raw_negated_qualifiers = (
        raw_current.get("negated_qualifiers")
        if isinstance(raw_current.get("negated_qualifiers"), dict)
        else {}
    )
    current["negated_qualifiers"] = {
        category: set(current_negated_qualifiers.get(category) or set()).union(
            set(raw_negated_qualifiers.get(category) or set())
        )
        for category in ("fuel", "displacement", "transmission", "side", "engine")
    }
    current_has_explicit_target = _perguntas_ia_current_facts_explicit(
        pergunta_atual,
        current,
    )
    if not current_has_explicit_target:
        current = {
            "identity": set(),
            "years": set(),
            "qualifiers": {
                category: set()
                for category in ("fuel", "displacement", "transmission", "side", "engine")
            },
            "negated_qualifiers": {},
            "negated_identity": set(),
            "decisive": set(),
        }
    historical = [
        _perguntas_ia_continuation_facts(
            _perguntas_ia_texto_autoritativo_atual(event.get("text"))
        )
        for event in historico
        if isinstance(event, dict)
        and _ml_question_normalize(event.get("role") or event.get("from_role"))
        not in {"seller", "loja", "store"}
        and str(event.get("text") or "").strip()
    ]
    current_identity = set(current.get("identity") or set())
    current_negated_identity = set(current.get("negated_identity") or set())
    reference: dict = {}
    reference_index = -1
    if not current_identity and not current_negated_identity:
        for index in range(len(historical) - 1, -1, -1):
            if historical[index].get("identity"):
                reference_index = index
                reference = historical[index]
                break
    elif current_identity:
        for index in range(len(historical) - 1, -1, -1):
            facts = historical[index]
            historical_identity = set(facts.get("identity") or set())
            if not historical_identity:
                continue
            if historical_identity == current_identity:
                reference_index = index
                reference = facts
            break
    reference_tail = historical[reference_index:] if reference_index >= 0 else []

    def latest_values(key: str) -> set:
        for facts in reversed(reference_tail):
            values = set(facts.get(key) or set())
            if values:
                return values
        return set()

    def latest_qualifier(category: str) -> set:
        for facts in reversed(reference_tail):
            qualifiers = facts.get("qualifiers") if isinstance(facts.get("qualifiers"), dict) else {}
            negated = (
                facts.get("negated_qualifiers")
                if isinstance(facts.get("negated_qualifiers"), dict)
                else {}
            )
            values = set(qualifiers.get(category) or set())
            denied = set(negated.get(category) or set())
            if values or denied:
                return values - denied
        return set()
    reference_identity = set(reference.get("identity") or set())
    resolved_identity = current_identity or reference_identity
    current_years = set(current.get("years") or set())
    reference_years = latest_values("years")
    current_decisive = set(current.get("decisive") or set())
    reference_decisive = latest_values("decisive")
    current_qualifiers = (
        current.get("qualifiers") if isinstance(current.get("qualifiers"), dict) else {}
    )
    current_negated = (
        current.get("negated_qualifiers")
        if isinstance(current.get("negated_qualifiers"), dict)
        else {}
    )
    resolved_qualifiers = {}
    for category in ("fuel", "displacement", "transmission", "side", "engine"):
        explicit = set(current_qualifiers.get(category) or set())
        negated = set(current_negated.get(category) or set())
        inherited = latest_qualifier(category) - negated
        resolved_qualifiers[category] = explicit or inherited
    return (
        {
            "identity": resolved_identity,
            "years": current_years or reference_years,
            "qualifiers": resolved_qualifiers,
            "negated_qualifiers": {
                category: set(current_negated.get(category) or set())
                for category in ("fuel", "displacement", "transmission", "side", "engine")
            },
            "negated_identity": current_negated_identity,
            "decisive": current_decisive or reference_decisive,
        },
        current,
    )


def _perguntas_ia_continuation_facts_within(facts: dict, resolved: dict) -> bool:
    identity = set(facts.get("identity") or set())
    if not identity or identity != set(resolved.get("identity") or set()):
        return False
    if identity.intersection(set(resolved.get("negated_identity") or set())):
        return False
    if not set(facts.get("years") or set()).issubset(set(resolved.get("years") or set())):
        return False
    if not set(facts.get("decisive") or set()).issubset(set(resolved.get("decisive") or set())):
        return False
    qualifiers = facts.get("qualifiers") if isinstance(facts.get("qualifiers"), dict) else {}
    resolved_qualifiers = (
        resolved.get("qualifiers")
        if isinstance(resolved.get("qualifiers"), dict)
        else {}
    )
    resolved_negated = (
        resolved.get("negated_qualifiers")
        if isinstance(resolved.get("negated_qualifiers"), dict)
        else {}
    )
    return all(
        set(values or set()).issubset(set(resolved_qualifiers.get(category) or set()))
        and not set(values or set()).intersection(
            set(resolved_negated.get(category) or set())
        )
        for category, values in qualifiers.items()
    )


def _perguntas_ia_continuation_facts_cover(facts: dict, required: dict) -> bool:
    if not set(required.get("identity") or set()).issubset(set(facts.get("identity") or set())):
        return False
    if not set(required.get("years") or set()).issubset(set(facts.get("years") or set())):
        return False
    if not set(required.get("decisive") or set()).issubset(set(facts.get("decisive") or set())):
        return False
    qualifiers = facts.get("qualifiers") if isinstance(facts.get("qualifiers"), dict) else {}
    required_qualifiers = (
        required.get("qualifiers")
        if isinstance(required.get("qualifiers"), dict)
        else {}
    )
    return all(
        set(values or set()).issubset(set(qualifiers.get(category) or set()))
        for category, values in required_qualifiers.items()
    )


def _perguntas_ia_continuacao_compatibilidade_ancorada(
    classificada: dict,
    *,
    pergunta_atual: object,
    historico: list[dict],
) -> bool:
    continuidade = (
        classificada.get("continuidade")
        if isinstance(classificada.get("continuidade"), dict)
        else {}
    )
    if str(continuidade.get("tipo") or "") != "continuacao":
        return True
    categorias = classificada.get("categorias") if isinstance(classificada.get("categorias"), list) else []
    if str(classificada.get("categoria") or "") != "compatibility" and "compatibility" not in categorias:
        return True
    compatibility = (
        classificada.get("compatibilidade")
        if isinstance(classificada.get("compatibilidade"), dict)
        else {}
    )
    target_text = str(compatibility.get("target_item") or "")
    if _perguntas_ia_continuation_has_negated_qualifier(target_text):
        return False
    resolved_facts, current_facts = _perguntas_ia_continuation_resolved_facts(
        pergunta_atual,
        historico,
    )
    current_qualifiers = (
        current_facts.get("qualifiers")
        if isinstance(current_facts.get("qualifiers"), dict)
        else {}
    )
    current_negated = (
        current_facts.get("negated_qualifiers")
        if isinstance(current_facts.get("negated_qualifiers"), dict)
        else {}
    )
    if any(
        values and not set(current_qualifiers.get(category) or set())
        for category, values in current_negated.items()
    ):
        return False
    target_facts = _perguntas_ia_continuation_facts(target_text)
    if not _perguntas_ia_continuation_facts_within(target_facts, resolved_facts):
        return False
    target_identity = target_facts.get("identity") or set()
    output_facts = [target_facts]
    for subquestion in classificada.get("subperguntas") or []:
        if not isinstance(subquestion, dict) or str(subquestion.get("intent") or "") != "compatibility":
            continue
        subquestion_text = str(subquestion.get("question") or "")
        if _perguntas_ia_continuation_has_negated_qualifier(subquestion_text):
            return False
        sub_facts = _perguntas_ia_continuation_facts(subquestion_text)
        if (sub_facts.get("identity") or set()) != target_identity:
            return False
        if not _perguntas_ia_continuation_facts_within(sub_facts, resolved_facts):
            return False
        output_facts.append(sub_facts)
    combined = {
        "identity": set().union(*(facts.get("identity") or set() for facts in output_facts)),
        "years": set().union(*(facts.get("years") or set() for facts in output_facts)),
        "decisive": set().union(*(facts.get("decisive") or set() for facts in output_facts)),
        "qualifiers": {
            category: set().union(*(
                (
                    facts.get("qualifiers", {}).get(category) or set()
                    if isinstance(facts.get("qualifiers"), dict)
                    else set()
                )
                for facts in output_facts
            ))
            for category in ("fuel", "displacement", "transmission", "side", "engine")
        },
    }
    return _perguntas_ia_continuation_facts_cover(combined, current_facts)


def _perguntas_ia_deve_reparar_continuidade(
    classificada: dict,
    *,
    pergunta_atual: object,
    historico: list[dict],
    contract_repair_attempted: bool,
) -> bool:
    """Allow one semantic repair only when history can safely resolve an unknown turn."""

    if contract_repair_attempted or not historico:
        return False
    if str(classificada.get("categoria") or "") != QuestionCategory.UNKNOWN.value:
        return False
    if str(classificada.get("intencao") or "") != "nao_entendi":
        return False
    continuidade = (
        classificada.get("continuidade")
        if isinstance(classificada.get("continuidade"), dict)
        else {}
    )
    if str(continuidade.get("tipo") or "") == "novo_assunto":
        return False
    texto_normalizado = _ml_question_normalize(pergunta_atual)
    if _perguntas_ia_mudanca_explicita(texto_normalizado):
        return False
    if _perguntas_ia_assunto_atual_autossuficiente(texto_normalizado):
        return False
    if _perguntas_ia_prompt_injection_evidente(texto_normalizado):
        return False
    for event in historico:
        if not isinstance(event, dict) or _ml_question_normalize(event.get("role")) in {
            "seller",
            "loja",
            "store",
        }:
            continue
        if _perguntas_ia_prompt_injection_evidente(event.get("text")):
            return False
    return _perguntas_ia_turno_continuacao_positiva(pergunta_atual)


def _perguntas_ia_validar_continuidade_segura(
    classificada: dict,
    *,
    pergunta_atual: object,
    historico: list[dict],
) -> None:
    """Fail closed when model output conflicts with injection or post-sale evidence."""

    continuidade = (
        classificada.get("continuidade")
        if isinstance(classificada.get("continuidade"), dict)
        else {}
    )
    continuidade_tipo = str(continuidade.get("tipo") or "")
    buyer_history_texts = [
        event.get("text")
        for event in historico
        if isinstance(event, dict)
        and _ml_question_normalize(event.get("role")) not in {"seller", "loja", "store"}
    ]
    current_injection = _perguntas_ia_prompt_injection_evidente(pergunta_atual)
    inherited_injection = bool(
        continuidade_tipo in {"continuacao", "inconclusiva"}
        and any(
            _perguntas_ia_prompt_injection_evidente(text)
            for text in buyer_history_texts
        )
    )
    if current_injection or inherited_injection:
        raise PerguntasIASegurancaBloqueada(
            "A classificacao foi bloqueada pela politica de seguranca."
        )

    post_sale_classification = bool(
        str(classificada.get("categoria") or "") == QuestionCategory.POST_SALE.value
        or str(classificada.get("fluxo") or "") == "pos_venda"
    )
    current_post_sale = _perguntas_ia_mensagem_pos_venda_evidente(pergunta_atual)
    inherited_post_sale = bool(
        continuidade_tipo in {"continuacao", "inconclusiva"}
        and any(_perguntas_ia_mensagem_pos_venda_evidente(text) for text in buyer_history_texts)
    )
    if (current_post_sale or inherited_post_sale) and not post_sale_classification:
        raise PerguntasIAClassificacaoInconclusiva(
            "A classificacao conflitou com sinais evidentes de pos-venda.",
            classificacao=classificada,
            allow_contextual=False,
        )
    if continuidade_tipo == "continuacao" and not _perguntas_ia_turno_continuacao_positiva(
        pergunta_atual
    ):
        raise PerguntasIAClassificacaoInconclusiva(
            "A classificacao tentou herdar um assunto diferente da pergunta atual.",
            classificacao=classificada,
            allow_contextual=False,
        )
    if not _perguntas_ia_continuacao_compatibilidade_ancorada(
        classificada,
        pergunta_atual=pergunta_atual,
        historico=historico,
    ):
        raise PerguntasIAClassificacaoInconclusiva(
            "A continuacao introduziu um alvo ou qualificadores ausentes na conversa.",
            classificacao=classificada,
            allow_contextual=True,
        )


def _perguntas_ia_classificar_intencao(client_id: str, loja: str, pergunta: dict, item: dict) -> dict:
    historico = pergunta.get("buyer_question_chat") if isinstance(pergunta.get("buyer_question_chat"), list) else []
    question_id = str(pergunta.get("id") or "").strip()
    current_text_normalized = _ml_question_normalize(pergunta.get("text"))
    previous_events = []
    for event in historico:
        if not isinstance(event, dict):
            continue
        event_question_id = str(event.get("question_id") or "").strip()
        if question_id and event_question_id == question_id:
            continue
        previous_events.append(event)
    if current_text_normalized:
        previous_events = [
            event
            for event in previous_events
            if _ml_question_normalize(event.get("role") or event.get("from_role"))
            in {"seller", "loja", "store"}
            or _ml_question_normalize(event.get("text")) != current_text_normalized
        ]
    mensagens = []
    for evento in previous_events[-8:]:
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
    }
    prompt = _perguntas_ia_classification_prompt(entrada)
    model_req = _normalizar_ia_modelo_padrao(_ia_modelo_perguntas_configurado())
    payload = IAChatRequest(
        message=prompt,
        page=ML_PERGUNTAS_IA_CLASSIFICATION_PAGE,
        context={
            "modulo": "perguntas_pos_venda",
            "tipo": "classificacao_intencao_perguntas_ml",
            "classification_contract_version": ML_PERGUNTAS_IA_CLASSIFICATION_CONTRACT_VERSION,
            "classification_contract_hash": ML_PERGUNTAS_IA_CLASSIFICATION_CONTRACT_HASH,
            "desativar_recursos_chat": True,
            "desativar_busca_web_chat": True,
            "modo_rapido_sidebar": True,
            "loja": loja,
        },
        model=model_req,
    )
    perf_t0 = time.perf_counter()
    try:
        resposta, model_usado = perguntas_agent_provider_transport.invoke_model(client_id, payload, model_req)
        contract_repair_attempted = False
        semantic_repair_attempted = False
        repair_violation_code = ""
        try:
            classificada = _perguntas_ia_classificacao_parsear(resposta)
        except _PerguntasIAContratoClassificacaoInvalido as exc:
            contract_repair_attempted = True
            repair_violation_code = exc.violation_code
            repair_payload = IAChatRequest(
                message=_perguntas_ia_classification_prompt(
                    entrada,
                    violation_code=repair_violation_code,
                ),
                page=ML_PERGUNTAS_IA_CLASSIFICATION_PAGE,
                context={
                    "modulo": "perguntas_pos_venda",
                    "tipo": "classificacao_intencao_perguntas_ml_reparo_contrato",
                    "classification_contract_version": ML_PERGUNTAS_IA_CLASSIFICATION_CONTRACT_VERSION,
                    "classification_contract_hash": ML_PERGUNTAS_IA_CLASSIFICATION_CONTRACT_HASH,
                    "contract_violation_code": repair_violation_code,
                    "desativar_recursos_chat": True,
                    "desativar_busca_web_chat": True,
                    "modo_rapido_sidebar": True,
                    "loja": loja,
                },
                model=model_req,
            )
            resposta_reparada, model_usado = perguntas_agent_provider_transport.invoke_model(
                client_id,
                repair_payload,
                model_req,
            )
            classificada = _perguntas_ia_classificacao_parsear(resposta_reparada)
        _perguntas_ia_validar_continuidade_segura(
            classificada,
            pergunta_atual=pergunta.get("text") or "",
            historico=mensagens,
        )
        if _perguntas_ia_deve_reparar_continuidade(
            classificada,
            pergunta_atual=pergunta.get("text") or "",
            historico=mensagens,
            contract_repair_attempted=contract_repair_attempted,
        ):
            semantic_repair_attempted = True
            semantic_payload = IAChatRequest(
                message=_perguntas_ia_classification_prompt(
                    entrada,
                    continuity_repair=True,
                ),
                page=ML_PERGUNTAS_IA_CLASSIFICATION_PAGE,
                context={
                    "modulo": "perguntas_pos_venda",
                    "tipo": "classificacao_intencao_perguntas_ml_reparo_continuidade",
                    "classification_contract_version": ML_PERGUNTAS_IA_CLASSIFICATION_CONTRACT_VERSION,
                    "classification_contract_hash": ML_PERGUNTAS_IA_CLASSIFICATION_CONTRACT_HASH,
                    "desativar_recursos_chat": True,
                    "desativar_busca_web_chat": True,
                    "modo_rapido_sidebar": True,
                    "loja": loja,
                },
                model=model_req,
            )
            resposta_semantica, model_usado = perguntas_agent_provider_transport.invoke_model(
                client_id,
                semantic_payload,
                model_req,
            )
            classificada = _perguntas_ia_classificacao_parsear(resposta_semantica)
            _perguntas_ia_validar_continuidade_segura(
                classificada,
                pergunta_atual=pergunta.get("text") or "",
                historico=mensagens,
            )
        classificada["model"] = model_usado
        classificada["source"] = "ia"
        perguntas_agent_telemetry.record(
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
            classification_contract_version=ML_PERGUNTAS_IA_CLASSIFICATION_CONTRACT_VERSION,
            classification_contract_hash=ML_PERGUNTAS_IA_CLASSIFICATION_CONTRACT_HASH,
            continuity_type=(classificada.get("continuidade") or {}).get("tipo"),
            contract_repair_attempted=contract_repair_attempted,
            semantic_repair_attempted=semantic_repair_attempted,
            contract_violation_code=repair_violation_code,
        )
        return classificada
    except Exception as exc:
        perguntas_agent_telemetry.record(
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
            source="ia",
        )
        if isinstance(exc, PerguntasIARespostaIndisponivel):
            raise
        provider_failure_code = _perguntas_ia_provider_failure_code(exc)
        if provider_failure_code:
            raise PerguntasIAProviderIndisponivel(
                "Classificacao de intencao temporariamente indisponivel no provedor.",
                reason=provider_failure_code,
            ) from exc
        raise PerguntasIARespostaIndisponivel(
            f"Classificacao de intencao pela IA indisponivel: {type(exc).__name__}."
        ) from exc


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
    agora = datetime.now().isoformat(timespec="seconds")
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
        "at": datetime.now().isoformat(timespec="seconds"),
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
        resposta, _model_usado = perguntas_agent_provider_transport.invoke_model(client_id, payload, model_req)
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
        memoria["updated_at"] = datetime.now().isoformat(timespec="seconds")
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


def _ia_agent_endpoint_headers(client_id: str = "", loja: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    api_key = (
        _env_config_value("JK_AGENT_ENDPOINT_API_KEY", "IA_AGENT_ENDPOINT_API_KEY")
        or _vertex_ai_agent_api_key_arquivo()
        or _vertex_ai_agent_api_key()
    )
    if api_key:
        headers["X-JK-Agent-Key"] = api_key
    tenant_bound = str(client_id or "").strip()
    store_bound = str(loja or "").strip()
    if tenant_bound and store_bound:
        headers["X-JK-Agent-Binding"] = "tenant-store-v1"
        headers["X-Client-ID"] = quote(tenant_bound, safe="")
        headers["X-JK-Store"] = quote(store_bound, safe="")
    return headers


def _ia_agent_http_post(url: str, body: dict, headers: dict, *, timeout: int = 75) -> dict:
    try:
        resp = requests.post(
            url,
            headers=headers,
            json=body,
            verify=requests_tls_verify(),
            timeout=timeout,
        )
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
    resposta_atual_bruta = str(pergunta.get("_resposta_atual") or "")
    resposta_atual = resposta_atual_bruta if resposta_atual_bruta.strip() else ""
    return {
        "id": pergunta.get("id") or "",
        "text": pergunta.get("text") or "",
        "current_draft_to_avoid": resposta_atual,
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
PEER_EXPORTS.extend([
    "PerguntasIAClassificacaoInconclusiva",
    "PerguntasIAProviderIndisponivel",
    "PerguntasIASegurancaBloqueada",
])
PEER_EXPORTS = [
    name for name in PEER_EXPORTS
    if name not in {"_perguntas_ia_intencao_heuristica", "_perguntas_ia_intencao_fluxo"}
]
__all__ = PEER_EXPORTS + ["configure_perguntas_pos_venda_state_runtime"]

configure_perguntas_pos_venda_state_runtime()
