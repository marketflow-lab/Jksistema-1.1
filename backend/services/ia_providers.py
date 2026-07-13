"""IA provider keys, model configuration and provider calls."""

from __future__ import annotations

import asyncio
import base64
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlencode, urlparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import dotenv_values
from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from jose import JWTError

from backend.schemas import (
    IAAgentQueryRequest,
    IAChatRequest,
    IASalvarConversaRequest,
    IARagIndexRequest,
    IARagReindexRequest,
)
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.ia_common import *
from backend.services.ia_context import get_tenant_id, get_tenant_path
from backend.services.ia_state import *

logger = None


def _configure_runtime_globals(target_globals, runtime_module=None, peers=None):
    runtime = bind_runtime_globals(target_globals, runtime_module)
    if runtime is not None:
        runtime_logger = getattr(runtime, "logger", None)
        if runtime_logger is not None:
            target_globals["logger"] = runtime_logger
    if peers:
        target_globals.update(peers)
    return runtime


def configure_ia_providers_runtime(runtime_module=None, peers=None):
    return _configure_runtime_globals(globals(), runtime_module, peers)


IA_AGENT_API_KEY_ENV_KEYS = (
    "GEMINI_AGENT_API_KEY",
    "VERTEX_AGENT_API_KEY",
    "VERTEX_AI_API_KEY",
    "GOOGLE_GENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
)


IA_SECRETS_PROVISIONING_URL_ENV_KEYS = (
    "JK_SECRETS_PROVISIONING_URL",
    "IA_SECRETS_PROVISIONING_URL",
)


IA_SECRETS_PROVISIONING_DOWNLOAD_TOKEN_ENV_KEYS = (
    "JK_SECRETS_PROVISIONING_DOWNLOAD_TOKEN",
    "IA_SECRETS_PROVISIONING_DOWNLOAD_TOKEN",
)


IA_SECRETS_PROVISIONING_ADMIN_TOKEN_ENV_KEYS = (
    "JK_SECRETS_PROVISIONING_ADMIN_TOKEN",
    "IA_SECRETS_PROVISIONING_ADMIN_TOKEN",
)


VERTEX_AI_API_KEY_ALLOW_ENV_KEYS = (
    "VERTEX_AI_ALLOW_API_KEY",
    "IA_VERTEX_ALLOW_API_KEY",
    "JK_IA_ALLOW_API_KEY",
)


def _obter_openai_api_key() -> str:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if api_key:
        return api_key

    # Fallback robusto: tenta ler o .env explicitamente da pasta do programa
    # e tambÃƒÂ©m do diretÃƒÂ³rio de trabalho atual (execuÃƒÂ§ÃƒÂµes via atalhos/serviÃƒÂ§os).
    env_paths = []
    try:
        env_paths.append(os.path.join(BASE_DIR, ".env"))
    except Exception:
        pass
    try:
        env_paths.append(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass

    for env_path in dict.fromkeys([p for p in env_paths if p]):
        if not os.path.exists(env_path):
            continue
        try:
            values = dotenv_values(env_path)
            candidate = ""
            if isinstance(values, dict):
                for k, v in values.items():
                    key_norm = str(k or "").replace("\ufeff", "").strip().upper()
                    if key_norm == "OPENAI_API_KEY":
                        candidate = str(v or "").strip()
                        break
            if candidate:
                os.environ["OPENAI_API_KEY"] = candidate
                return candidate
        except Exception:
            # Ignora arquivo invalido e segue fallback.
            pass

    secure_key = _secure_read_secret("OPENAI_API_KEY")
    if secure_key:
        os.environ["OPENAI_API_KEY"] = secure_key
        return secure_key

    key_file = os.path.join(PASTA_INFO, "openai_api_key.txt")
    if os.path.exists(key_file):
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""
    return ""


def _obter_deepseek_api_key() -> str:
    api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if api_key:
        return api_key
    # Fallback: lÃƒÂª .env explicitamente
    env_paths = []
    try:
        env_paths.append(os.path.join(BASE_DIR, ".env"))
    except Exception:
        pass
    try:
        env_paths.append(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass
    for env_path in dict.fromkeys([p for p in env_paths if p]):
        if not os.path.exists(env_path):
            continue
        try:
            values = dotenv_values(env_path)
            if isinstance(values, dict):
                for k, v in values.items():
                    if str(k or "").replace("\ufeff", "").strip().upper() == "DEEPSEEK_API_KEY":
                        candidate = str(v or "").strip()
                        if candidate:
                            os.environ["DEEPSEEK_API_KEY"] = candidate
                            return candidate
        except Exception:
            pass
    secure_key = _secure_read_secret("DEEPSEEK_API_KEY")
    if secure_key:
        os.environ["DEEPSEEK_API_KEY"] = secure_key
        return secure_key

    key_file = os.path.join(PASTA_INFO, "deepseek_api_key.txt")
    if os.path.exists(key_file):
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""
    return ""


def _obter_gemini_api_key() -> str:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if api_key:
        return api_key

    env_paths = []
    try:
        env_paths.append(os.path.join(BASE_DIR, ".env"))
    except Exception:
        pass
    try:
        env_paths.append(os.path.join(os.getcwd(), ".env"))
    except Exception:
        pass

    for env_path in dict.fromkeys([p for p in env_paths if p]):
        if not os.path.exists(env_path):
            continue
        try:
            values = dotenv_values(env_path)
            if isinstance(values, dict):
                for k, v in values.items():
                    key_norm = str(k or "").replace("\ufeff", "").strip().upper()
                    if key_norm == "GEMINI_API_KEY":
                        candidate = str(v or "").strip()
                        if candidate:
                            os.environ["GEMINI_API_KEY"] = candidate
                            return candidate
        except Exception:
            pass

    secure_key = _secure_read_secret("GEMINI_API_KEY")
    if secure_key:
        os.environ["GEMINI_API_KEY"] = secure_key
        return secure_key

    key_file = os.path.join(PASTA_INFO, "gemini_api_key.txt")
    if os.path.exists(key_file):
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""
    return ""


def _salvar_ia_provider_api_key(provider: str, valor: str | None, limpar: bool = False) -> None:
    provider_norm = str(provider or "").strip().lower()
    meta = {
        "openai": ("OPENAI_API_KEY", "openai_api_key.txt"),
        "deepseek": ("DEEPSEEK_API_KEY", "deepseek_api_key.txt"),
        "gemini": ("GEMINI_API_KEY", "gemini_api_key.txt"),
    }.get(provider_norm)
    if not meta:
        return

    env_key, filename = meta
    key_file = os.path.join(PASTA_INFO, filename)
    if limpar:
        try:
            if os.path.exists(key_file):
                os.remove(key_file)
        except Exception:
            logger.exception("[CONFIG] Falha ao limpar chave %s", provider_norm)
        try:
            _secure_delete_secret(env_key)
        except Exception:
            logger.exception("[CONFIG] Falha ao limpar chave %s no cofre local", provider_norm)
        os.environ.pop(env_key, None)
        return

    api_key = str(valor or "").strip()
    if not api_key:
        return

    gravou_no_cofre = False
    try:
        gravou_no_cofre = bool(_secure_write_secret(env_key, api_key))
    except Exception:
        logger.exception("[CONFIG] Falha ao gravar chave %s no cofre local", provider_norm)
    if gravou_no_cofre:
        try:
            if os.path.exists(key_file):
                os.remove(key_file)
        except Exception:
            logger.exception("[CONFIG] Falha ao remover arquivo legado da chave %s", provider_norm)
    else:
        os.makedirs(PASTA_INFO, exist_ok=True)
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(api_key)
    os.environ[env_key] = api_key


def _gemini_nome_curto(model_name: str) -> str:
    nome = str(model_name or "").strip()
    if nome.lower().startswith("gemini:"):
        nome = nome.split(":", 1)[1]
    if nome.startswith("models/"):
        nome = nome.split("/", 1)[1]
    return nome


def _vertex_modelo_nome_curto(model_name: str) -> str:
    nome = str(model_name or "").strip()
    if nome.lower().startswith("vertex:"):
        nome = nome.split(":", 1)[1]
    if nome.startswith("publishers/google/models/"):
        nome = nome.rsplit("/", 1)[-1]
    if nome.startswith("models/"):
        nome = nome.split("/", 1)[1]
    return nome


def _modelo_eh_vertex_ai(model_name: str) -> bool:
    nome = str(model_name or "").strip().lower()
    return nome.startswith("vertex:")


def _modelo_eh_codex(model_name: str) -> bool:
    nome = str(model_name or "").strip().lower()
    return nome.startswith("codex:")


def _codex_modelo_nome_curto(model_name: str | None) -> str:
    nome = str(model_name or "").strip()
    if nome.lower().startswith("codex:"):
        nome = nome.split(":", 1)[1].strip()
    return nome or "gpt-5.5"


IA_MODELO_PADRAO_SISTEMA = "vertex:gemini-2.5-flash"


CODEX_CONFIGURABLE_MODELS = (
    ("gpt-5.6-sol", "Codex GPT-5.6 Sol"),
    ("gpt-5.6-terra", "Codex GPT-5.6 Terra"),
    ("gpt-5.6-luna", "Codex GPT-5.6 Luna"),
    ("gpt-5.5", "Codex GPT-5.5"),
    ("gpt-5.4", "Codex GPT-5.4"),
    ("gpt-5.4-mini", "Codex GPT-5.4 Mini"),
    ("gpt-5.3-codex-spark", "Codex GPT-5.3 Codex Spark"),
)


def _listar_modelos_codex_configuraveis() -> list[dict[str, str]]:
    return [
        {"name": f"codex:{slug}", "display_name": display_name}
        for slug, display_name in CODEX_CONFIGURABLE_MODELS
    ]


GEMINI_31_FLASH_MODEL = "gemini-3.1-flash"


FAVORITOS_PESQUISAS_IA_MODEL = (
    os.getenv("FAVORITOS_PESQUISAS_IA_MODEL")
    or IA_MODELO_PADRAO_SISTEMA
).strip()


FAVORITOS_PESQUISAS_IA_FALLBACK_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview",
    os.getenv("GEMINI_MODEL") or "",
    "gemini-2.5-flash",
]


def _normalizar_ia_modelo_padrao(model_name: str | None) -> str:
    nome = str(model_name or "").strip()
    if not nome:
        return IA_MODELO_PADRAO_SISTEMA
    if nome.lower().startswith("gemini:"):
        curto = _gemini_nome_curto(nome)
        return f"gemini:{curto}" if curto else IA_MODELO_PADRAO_SISTEMA
    if _modelo_eh_vertex_ai(nome):
        curto = _vertex_modelo_nome_curto(nome)
        return f"vertex:{curto}" if curto else IA_MODELO_PADRAO_SISTEMA
    if _modelo_eh_codex(nome):
        curto = _codex_modelo_nome_curto(nome)
        return f"codex:{curto}" if curto else "codex:gpt-5.5"
    if nome.lower().startswith(("gpt-", "deepseek-")):
        return nome
    curto = _vertex_modelo_nome_curto(nome)
    return f"vertex:{curto}" if curto else IA_MODELO_PADRAO_SISTEMA


def _vertex_generation_config(model_name: str, modo_rapido: bool = False, json_mode: bool = False) -> dict:
    modelo = _vertex_modelo_nome_curto(model_name).lower()
    cfg: dict = {
        "maxOutputTokens": 768 if modo_rapido else 4096,
    }
    if json_mode:
        cfg["temperature"] = 0
        cfg["responseMimeType"] = "application/json"
    elif modo_rapido:
        cfg["temperature"] = 0.2

    # Gemini 2.5 pode consumir todo o limite com thinking tokens e voltar HTTP 200 sem texto.
    if modelo.startswith("gemini-2.5-flash"):
        cfg["thinkingConfig"] = {"thinkingBudget": 0}
    elif modelo.startswith("gemini-2.5-pro"):
        cfg["thinkingConfig"] = {"thinkingBudget": 128 if modo_rapido else 512}
    return cfg


def _gemini_api_key_para_ia() -> str:
    return (_vertex_ai_agent_api_key() or _obter_gemini_api_key() or "").strip()


def _extrair_texto_generate_content(data: dict | None) -> str:
    if not isinstance(data, dict):
        return ""
    candidatos = data.get("candidates") if isinstance(data.get("candidates"), list) else []
    textos: list[str] = []
    for candidato in candidatos:
        if not isinstance(candidato, dict):
            continue
        content = candidato.get("content") if isinstance(candidato.get("content"), dict) else {}
        parts = content.get("parts") if isinstance(content.get("parts"), list) else []
        for parte in parts:
            if isinstance(parte, dict):
                texto = str(parte.get("text") or "").strip()
                if texto:
                    textos.append(texto)
    return "\n".join(textos).strip()


def _chamar_gemini_api_direta(model_name: str, request_body: dict, api_key: str) -> str:
    chave = str(api_key or "").strip()
    if not chave:
        raise RuntimeError("Chave da Gemini API nao configurada.")
    model = _gemini_nome_curto(model_name) or _vertex_modelo_nome_curto(model_name) or _vertex_ai_modelo_padrao()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.loads(json.dumps(request_body or {}))
    resp = requests.post(
        url,
        params={"key": chave},
        headers={"Content-Type": "application/json"},
        json=body,
        verify=False,
        timeout=60,
    )
    if not resp.ok:
        detail = "Falha ao chamar a Gemini API direta."
        try:
            erro = resp.json().get("error") or {}
            if erro.get("message"):
                detail = str(erro.get("message"))
        except Exception:
            pass
        raise RuntimeError(f"Gemini API HTTP {resp.status_code}: {detail}")
    texto = _extrair_texto_generate_content(resp.json())
    if not texto:
        raise RuntimeError("Gemini API retornou sem texto.")
    return texto


def _ia_modelo_padrao_configurado() -> str:
    try:
        cfg = _carregar_configuracoes_globais()
        return _normalizar_ia_modelo_padrao(cfg.get("ia_modelo_padrao"))
    except Exception:
        logger.exception("Erro ao carregar modelo padrao de IA")
        return IA_MODELO_PADRAO_SISTEMA


def _ia_modelo_finalidade_configurado(finalidade: str) -> str:
    chave_por_finalidade = {
        "perguntas": "ia_modelo_perguntas",
        "pos_venda": "ia_modelo_pos_venda",
        "pos-venda": "ia_modelo_pos_venda",
        "chat": "ia_modelo_chat",
        "favoritos": "ia_modelo_favoritos",
    }
    chave = chave_por_finalidade.get(str(finalidade or "").strip().lower())
    try:
        cfg = _carregar_configuracoes_globais()
        fallback = cfg.get("ia_modelo_padrao") or IA_MODELO_PADRAO_SISTEMA
        return _normalizar_ia_modelo_padrao((cfg.get(chave) if chave else "") or fallback)
    except Exception:
        logger.exception("Erro ao carregar modelo de IA para finalidade %s", finalidade)
        return _ia_modelo_padrao_configurado()


def _ia_modelo_perguntas_configurado() -> str:
    return _ia_modelo_finalidade_configurado("perguntas")


def _ia_modelo_pos_venda_configurado() -> str:
    return _ia_modelo_finalidade_configurado("pos_venda")


def _ia_modelo_chat_configurado() -> str:
    return _ia_modelo_finalidade_configurado("chat")


def _ia_modelo_favoritos_configurado() -> str:
    return _ia_modelo_finalidade_configurado("favoritos")


def _ia_modo_finalidade_configurado(finalidade: str) -> str:
    chave_por_finalidade = {
        "perguntas": "ia_modo_perguntas",
        "pos_venda": "ia_modo_pos_venda",
        "pos-venda": "ia_modo_pos_venda",
        "chat": "ia_modo_chat",
        "favoritos": "ia_modo_favoritos",
    }
    chave = chave_por_finalidade.get(str(finalidade or "").strip().lower())
    try:
        cfg = _carregar_configuracoes_globais()
        fallback = cfg.get("ia_modo_padrao") or "modelo"
        return _normalizar_ia_modo((cfg.get(chave) if chave else "") or fallback)
    except Exception:
        logger.exception("Erro ao carregar modo de IA para finalidade %s", finalidade)
        return "modelo"


def _ia_modo_perguntas_configurado() -> str:
    return _ia_modo_finalidade_configurado("perguntas")


def _ia_modo_pos_venda_configurado() -> str:
    return _ia_modo_finalidade_configurado("pos_venda")


def _ia_agent_resource_name_configurado() -> str:
    return (
        _vertex_config_valor("ia_agent_resource_name")
        or (os.getenv("VERTEX_AI_AGENT_RESOURCE_NAME") or "").strip()
        or (os.getenv("GEMINI_AGENT_RESOURCE_NAME") or "").strip()
    ).strip()


def _ia_agent_endpoint_url_configurado() -> str:
    return (
        _vertex_config_valor("ia_agent_endpoint_url")
        or (os.getenv("VERTEX_AI_AGENT_ENDPOINT_URL") or "").strip()
        or (os.getenv("GEMINI_AGENT_ENDPOINT_URL") or "").strip()
        or (os.getenv("JK_IA_AGENT_ENDPOINT_URL") or "").strip()
    ).strip()


def _ia_favoritos_usar_imagem_configurado() -> bool:
    try:
        cfg = _carregar_configuracoes_globais()
        return bool(cfg.get("ia_favoritos_usar_imagem"))
    except Exception:
        logger.exception("Erro ao carregar configuracao de imagem para IA de favoritos")
        return False


IA_PROVIDER_CONFIG_KEYS = {
    "openai": "ia_openai_ativa",
    "deepseek": "ia_deepseek_ativa",
    "gemini": "ia_gemini_ativa",
    "vertex": "ia_vertex_ativa",
}


IA_PROVIDER_LABELS = {
    "codex": "Codex",
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "gemini": "Gemini",
    "vertex": "Vertex AI",
}


def _ia_provedor_por_modelo(model_name: str | None) -> str:
    nome = str(model_name or "").strip()
    if _modelo_eh_codex(nome):
        return "codex"
    if _modelo_eh_vertex_ai(nome):
        return "vertex"
    if _modelo_eh_gemini_api(nome):
        return "gemini"
    if nome.startswith("deepseek-"):
        return "deepseek"
    return "openai"


def _ia_provedor_ativo(provedor: str) -> bool:
    chave = IA_PROVIDER_CONFIG_KEYS.get(str(provedor or "").strip().lower())
    if not chave:
        return True
    try:
        cfg = _carregar_configuracoes_globais()
        if chave not in cfg:
            return True
        return bool(cfg.get(chave))
    except Exception:
        logger.exception("Erro ao validar se provedor de IA esta ativo: %s", provedor)
        return True


def _ia_validar_provedor_ativo(provedor: str) -> None:
    provedor_norm = str(provedor or "").strip().lower()
    if _ia_provedor_ativo(provedor_norm):
        return
    nome = IA_PROVIDER_LABELS.get(provedor_norm, provedor_norm.upper())
    raise HTTPException(
        status_code=403,
        detail=f"{nome} esta desativada nas configuracoes. Peca para um admin reativar.",
    )


def _chamar_codex_chat(payload: IAChatRequest, client_id: str) -> str:
    """Executa o modelo Codex configurado sem expor ferramentas ou o workspace."""
    from backend.services import codex_console

    if not codex_console._codex_enabled():
        raise HTTPException(status_code=503, detail="Codex esta desabilitado neste runtime.")
    if not codex_console._codex_sdk_installed():
        raise HTTPException(status_code=503, detail="Dependencia do Codex nao instalada neste runtime.")
    if not codex_console._codex_auth_detected():
        raise HTTPException(status_code=503, detail="Autenticacao local do Codex nao encontrada.")

    mensagem = _ia_chat_mensagem_contextual(payload)
    if not mensagem:
        raise HTTPException(status_code=400, detail="Mensagem vazia.")

    blocos = [mensagem]
    contexto = payload.context if isinstance(payload.context, dict) else {}
    if contexto:
        try:
            contexto_json = json.dumps(contexto, ensure_ascii=False, default=str)
        except Exception:
            contexto_json = str(contexto)
        if contexto_json.strip():
            blocos.append(f"Contexto do JK Sistema:\n{contexto_json[:16000]}")

    resultados = payload.tool_results if isinstance(payload.tool_results, list) else []
    if resultados:
        try:
            resultados_json = json.dumps(resultados, ensure_ascii=False, default=str)
        except Exception:
            resultados_json = str(resultados)
        if resultados_json.strip():
            blocos.append(f"Resultados de consultas internas autorizadas:\n{resultados_json[:18000]}")

    anexos_texto = []
    for anexo in _ia_chat_normalizar_anexos(payload):
        texto = _ia_chat_extrair_texto_anexo(anexo)
        if texto:
            anexos_texto.append(f"Arquivo {anexo.get('name') or 'anexo'}:\n{texto[:12000]}")
        elif str(anexo.get("mime_type") or "").startswith("image/"):
            anexos_texto.append(
                f"Imagem {anexo.get('name') or 'anexo'} anexada; esta integracao configurada do Codex aceita somente texto."
            )
    if anexos_texto:
        blocos.append("\n\n".join(anexos_texto)[:18000])

    prompt = "\n\n".join(blocos)[:52000]
    model = _codex_modelo_nome_curto(payload.model)
    cwd = codex_console._codex_readonly_cwd_for_session(
        {"client_id": str(client_id or "default"), "username": "ia-configurada"},
        uuid.uuid4().hex,
    )

    try:
        from openai_codex import ApprovalMode, Codex, CodexConfig
        from openai_codex.generated.v2_all import ReasoningEffort, ReasoningSummary

        with Codex(
            CodexConfig(
                codex_bin=codex_console._codex_runtime_bin(),
                env=codex_console._codex_sdk_env(),
                cwd=cwd,
                config_overrides=codex_console._codex_nonfull_config_overrides(),
            )
        ) as codex:
            thread = codex.thread_start(
                cwd=cwd,
                model=model,
                approval_mode=ApprovalMode.deny_all,
                ephemeral=True,
                developer_instructions=(
                    "Voce e a IA Codex selecionada nas Configuracoes do JK Sistema. "
                    "Responda em portugues do Brasil, somente em texto, usando apenas o contexto fornecido. "
                    "Nao use ferramentas, nao leia arquivos e nao execute nem alegue executar alteracoes."
                ),
            )
            resultado = thread.run(
                prompt,
                cwd=cwd,
                model=model,
                approval_mode=ApprovalMode.deny_all,
                effort=ReasoningEffort.medium,
                summary=ReasoningSummary.model_validate("auto"),
            )
    except HTTPException:
        raise
    except Exception as exc:
        if logger is not None:
            logger.warning("[IA CODEX] Falha ao gerar resposta: %s", exc)
        raise HTTPException(status_code=503, detail=f"Codex indisponivel: {exc}") from exc

    status = str(getattr(getattr(resultado, "status", None), "value", getattr(resultado, "status", "")) or "")
    if status == "failed":
        erro = getattr(resultado, "error", None)
        detalhe = str(getattr(erro, "message", "") or "Codex falhou ao gerar a resposta.")
        raise HTTPException(status_code=503, detail=detalhe)
    resposta = str(getattr(resultado, "final_response", "") or "").strip()
    if not resposta:
        raise HTTPException(status_code=502, detail="Codex concluiu sem resposta final.")
    return resposta


def _vertex_config_valor(chave: str, padrao: str = "") -> str:
    try:
        cfg = _carregar_configuracoes_globais()
        valor = str(cfg.get(chave) or "").strip()
        if valor:
            return valor
    except Exception:
        pass
    return str(padrao or "").strip()


def _vertex_ai_location() -> str:
    return (
        _vertex_config_valor("ia_vertex_location")
        or (os.getenv("VERTEX_AI_LOCATION") or "").strip()
        or "global"
    ).strip()


def _vertex_ai_modelo_padrao() -> str:
    return (
        _vertex_modelo_nome_curto(_vertex_config_valor("ia_vertex_model"))
        or _vertex_modelo_nome_curto(os.getenv("VERTEX_AI_MODEL") or "")
        or "gemini-2.5-flash"
    )


def _vertex_ai_project_id_configurado() -> str:
    return (
        _vertex_config_valor("ia_vertex_project_id")
        or (os.getenv("VERTEX_AI_PROJECT_ID") or "").strip()
    ).strip()


def _vertex_ai_service_account_email() -> str:
    return (
        _vertex_config_valor("ia_vertex_service_account_email")
        or (os.getenv("VERTEX_AI_SERVICE_ACCOUNT") or "").strip()
    ).strip()


def _vertex_ai_agent_api_key_arquivo() -> str:
    if os.path.exists(ARQUIVO_VERTEX_AGENT_API_KEY):
        try:
            with open(ARQUIVO_VERTEX_AGENT_API_KEY, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""
    return ""


def _vertex_ai_agent_api_key() -> str:
    api_key = _env_config_value(*IA_AGENT_API_KEY_ENV_KEYS, cache_as="GEMINI_AGENT_API_KEY")
    if api_key:
        return api_key
    api_key = _secure_read_any_secret(IA_AGENT_API_KEY_ENV_KEYS)
    if api_key:
        os.environ["GEMINI_AGENT_API_KEY"] = api_key
        return api_key
    return _vertex_ai_agent_api_key_arquivo()


def _salvar_vertex_agent_api_key(valor: str | None, limpar: bool = False) -> None:
    if limpar:
        try:
            if os.path.exists(ARQUIVO_VERTEX_AGENT_API_KEY):
                os.remove(ARQUIVO_VERTEX_AGENT_API_KEY)
        except Exception:
            logger.exception("Erro ao remover chave do agente Vertex")
        try:
            _secure_delete_secret("GEMINI_AGENT_API_KEY")
        except Exception:
            logger.exception("Erro ao remover chave do agente Vertex do cofre local")
        for key in IA_AGENT_API_KEY_ENV_KEYS:
            os.environ.pop(key, None)
        return
    api_key = str(valor or "").strip()
    if not api_key:
        return
    gravou_no_cofre = False
    try:
        gravou_no_cofre = bool(_secure_write_secret("GEMINI_AGENT_API_KEY", api_key))
    except Exception:
        logger.exception("Erro ao gravar chave do agente Vertex no cofre local")
    if gravou_no_cofre:
        try:
            if os.path.exists(ARQUIVO_VERTEX_AGENT_API_KEY):
                os.remove(ARQUIVO_VERTEX_AGENT_API_KEY)
        except Exception:
            logger.exception("Erro ao remover arquivo legado da chave do agente Vertex")
    else:
        os.makedirs(os.path.dirname(ARQUIVO_VERTEX_AGENT_API_KEY), exist_ok=True)
        with open(ARQUIVO_VERTEX_AGENT_API_KEY, "w", encoding="utf-8") as f:
            f.write(api_key)
    os.environ["GEMINI_AGENT_API_KEY"] = api_key


def _ia_secrets_provisioning_url(path: str = "") -> str:
    base = _env_config_value(*IA_SECRETS_PROVISIONING_URL_ENV_KEYS).strip()
    if not base:
        return ""
    base = base.rstrip("/")
    path_norm = "/" + str(path or "").strip().strip("/")
    if path_norm == "/":
        return base
    if base.endswith(path_norm):
        return base
    return base + path_norm


def _ia_secrets_provisioning_download_token() -> str:
    return _env_config_value(*IA_SECRETS_PROVISIONING_DOWNLOAD_TOKEN_ENV_KEYS).strip()


def _ia_secrets_provisioning_admin_token() -> str:
    return _env_config_value(*IA_SECRETS_PROVISIONING_ADMIN_TOKEN_ENV_KEYS).strip()


def _ia_secrets_bundle_atual() -> dict:
    bundle = {
        "OPENAI_API_KEY": _obter_openai_api_key(),
        "DEEPSEEK_API_KEY": _obter_deepseek_api_key(),
        "GEMINI_API_KEY": _obter_gemini_api_key(),
        "GEMINI_AGENT_API_KEY": _vertex_ai_agent_api_key(),
        "GROQ_API_KEY": _secure_read_secret("GROQ_API_KEY"),
    }
    return {k: v for k, v in bundle.items() if str(v or "").strip()}


def _ia_secrets_publicar_no_provisionador_se_configurado() -> dict:
    url = _ia_secrets_provisioning_url("/admin/secrets")
    admin_token = _ia_secrets_provisioning_admin_token()
    if not url or not admin_token:
        return {"success": False, "configured": False, "message": "Provisionador de chaves nao configurado."}
    bundle = _ia_secrets_bundle_atual()
    if not bundle:
        return {"success": False, "configured": True, "message": "Nenhuma chave de IA configurada para publicar."}
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, headers=headers, json={"secrets": bundle}, timeout=20)
        data = resp.json() if resp.headers.get("content-type", "").lower().startswith("application/json") else {}
    except Exception as exc:
        logger.warning("[IA-SECRETS] Falha ao publicar chaves no provisionador: %s", exc)
        return {"success": False, "configured": True, "message": "Falha ao publicar chaves no provisionador."}
    if resp.status_code >= 400 or data.get("success") is False:
        logger.warning("[IA-SECRETS] Provisionador recusou publicacao: status=%s", resp.status_code)
        return {"success": False, "configured": True, "message": "Provisionador recusou a publicacao das chaves."}
    return {
        "success": True,
        "configured": True,
        "version": str(data.get("version") or "").strip(),
    }


def _ia_secrets_validar_sessao_ativa(sessao: dict) -> None:
    username = str((sessao or {}).get("username") or "").strip().lower()
    client_id = str((sessao or {}).get("client_id") or "").strip()
    if not username or not client_id:
        raise HTTPException(status_code=401, detail="Sessao invalida. Faca login novamente.")
    usuarios, _ws, _headers = carregar_usuarios_sheets()
    usuario = usuarios.get(username) if isinstance(usuarios, dict) else None
    if not isinstance(usuario, dict):
        raise HTTPException(status_code=403, detail="Usuario nao encontrado para provisionar chaves.")
    usuario_client = str(usuario.get("client_id") or client_id or "default").strip() or "default"
    if usuario_client != client_id:
        raise HTTPException(status_code=403, detail="Sessao invalida para este cliente.")
    if not _login_usuario_ativo(usuario):
        raise HTTPException(status_code=403, detail="Usuario inativo nao pode baixar chaves de IA.")
    validade_ok, msg_validade = _login_validade_ok(usuario)
    if not validade_ok:
        raise HTTPException(status_code=403, detail=msg_validade or "Acesso expirado ou invalido.")


def _ia_secrets_provisionar_cofre_local(sessao: dict) -> dict:
    if not _secure_store_available():
        return {
            "success": False,
            "configured": False,
            "message": "Cofre local do Windows indisponivel neste ambiente.",
            "local_store": _secure_secrets_status(),
        }
    url = _ia_secrets_provisioning_url("/download")
    if not url:
        return {
            "success": False,
            "configured": False,
            "message": "Servidor de provisionamento de chaves nao configurado.",
            "local_store": _secure_secrets_status(),
        }
    headers = {"Content-Type": "application/json"}
    download_token = _ia_secrets_provisioning_download_token()
    if download_token:
        headers["Authorization"] = f"Bearer {download_token}"
        headers["X-JK-Provisioning-Token"] = download_token
    payload = {
        "username": str((sessao or {}).get("username") or "").strip().lower(),
        "client_id": str((sessao or {}).get("client_id") or "").strip(),
        "machine_id": str((sessao or {}).get("machine_id") or "").strip(),
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        data = resp.json() if resp.headers.get("content-type", "").lower().startswith("application/json") else {}
    except Exception as exc:
        logger.warning("[IA-SECRETS] Falha ao baixar chaves do provisionador: %s", exc)
        return {
            "success": False,
            "configured": True,
            "message": "Nao foi possivel baixar as chaves de IA agora.",
            "local_store": _secure_secrets_status(),
        }
    if resp.status_code >= 400 or data.get("success") is False:
        logger.warning("[IA-SECRETS] Provisionador recusou download: status=%s", resp.status_code)
        return {
            "success": False,
            "configured": True,
            "message": str(data.get("message") or "Provisionador recusou o download das chaves."),
            "local_store": _secure_secrets_status(),
        }
    secrets_payload = data.get("secrets")
    if not isinstance(secrets_payload, dict):
        secrets_payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    if not secrets_payload:
        return {
            "success": False,
            "configured": True,
            "message": "Provisionador nao retornou chaves para gravar.",
            "local_store": _secure_secrets_status(),
        }
    version = str(data.get("version") or data.get("etag") or "").strip()
    saved = _secure_write_secrets_bundle(secrets_payload, version=version, source="provisionador")
    for key in saved.get("saved") or []:
        value = _secure_read_secret(key)
        if value:
            os.environ[str(key)] = value
    return {
        "success": bool(saved.get("saved")),
        "configured": True,
        "saved": saved.get("saved") or [],
        "skipped": saved.get("skipped") or [],
        "version": version,
        "local_store": _secure_secrets_status(),
    }


def _vertex_ai_credentials_file() -> str:
    candidatos = [
        os.getenv("VERTEX_AI_CREDENTIALS_FILE"),
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        os.path.join(BASE_DIR, "jkjkjk-485920-e598a0a0dcb9.json"),
        os.path.join(os.getcwd(), "jkjkjk-485920-e598a0a0dcb9.json"),
    ]
    for candidato in candidatos:
        caminho = str(candidato or "").strip()
        if caminho and os.path.exists(caminho):
            return caminho
    return ""


def _vertex_ai_auth() -> tuple[str, str]:
    cred_file = _vertex_ai_credentials_file()
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    if cred_file:
        creds = Credentials.from_service_account_file(cred_file, scopes=scopes)
        project_id = (_vertex_ai_project_id_configurado() or getattr(creds, "project_id", "") or "").strip()
    else:
        creds, default_project_id = google.auth.default(scopes=scopes)
        project_id = (
            _vertex_ai_project_id_configurado()
            or str(default_project_id or "")
            or getattr(creds, "project_id", "")
            or getattr(creds, "quota_project_id", "")
            or ""
        ).strip()
    target_service_account = _vertex_ai_service_account_email()
    if target_service_account and impersonated_credentials is not None:
        origem = str(getattr(creds, "service_account_email", "") or "").strip().lower()
        if origem != target_service_account.lower():
            creds = impersonated_credentials.Credentials(
                source_credentials=creds,
                target_principal=target_service_account,
                target_scopes=scopes,
                lifetime=3600,
            )
    session = requests.Session()
    session.verify = False
    creds.refresh(GoogleAuthRequest(session=session))
    if not project_id:
        raise RuntimeError("Project ID da Vertex AI nao encontrado.")
    return str(creds.token or ""), project_id


def _vertex_ai_headers_e_project() -> tuple[dict, str]:
    api_key = _vertex_ai_agent_api_key()
    auth_mode = _env_config_value(*VERTEX_AI_AUTH_MODE_ENV_KEYS).strip().lower().replace("-", "_")
    usar_api_key_primeiro = auth_mode in {"api_key", "apikey", "key", "chave"}
    permitir_api_key = (
        usar_api_key_primeiro
        or _env_config_bool(VERTEX_AI_API_KEY_ALLOW_ENV_KEYS, default=False)
    )

    def _headers_api_key(causa: Exception | None = None) -> tuple[dict, str]:
        project_id = _vertex_ai_project_id_configurado()
        if not project_id:
            if causa is not None:
                raise RuntimeError("Project ID da Vertex AI nao encontrado para usar a chave do agente.") from causa
            raise RuntimeError("Project ID da Vertex AI nao encontrado para usar a chave do agente.")
        logger.warning("[IA] Usando chave de API para Vertex AI via .env/configuracao.")
        return {"x-goog-api-key": api_key, "Content-Type": "application/json"}, project_id

    if api_key and usar_api_key_primeiro:
        return _headers_api_key()

    try:
        token, project_id = _vertex_ai_auth()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, project_id
    except Exception as exc:
        if api_key and permitir_api_key:
            return _headers_api_key(exc)
        raise


def _vertex_ai_generate_url(model_name: str) -> str:
    model = _vertex_modelo_nome_curto(model_name) or _vertex_ai_modelo_padrao()
    location = _vertex_ai_location()
    _headers, project_id = _vertex_ai_headers_e_project()
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    return f"https://{host}/v1/projects/{project_id}/locations/{location}/publishers/google/models/{model}:generateContent"


def _listar_modelos_vertex_ai() -> list[dict]:
    return [
        {"name": "vertex:gemini-2.5-flash", "display_name": "Vertex Gemini 2.5 Flash", "description": "Vertex AI Gemini generateContent"},
        {"name": "vertex:gemini-2.5-pro", "display_name": "Vertex Gemini 2.5 Pro", "description": "Vertex AI Gemini generateContent"},
        {"name": "vertex:gemini-2.5-flash-lite", "display_name": "Vertex Gemini 2.5 Flash-Lite", "description": "Vertex AI Gemini generateContent"},
        {"name": "vertex:gemini-2.0-flash", "display_name": "Vertex Gemini 2.0 Flash", "description": "Vertex AI Gemini generateContent"},
        {"name": "vertex:gemini-2.0-flash-lite", "display_name": "Vertex Gemini 2.0 Flash-Lite", "description": "Vertex AI Gemini generateContent"},
    ]


def _listar_modelos_gemini_api(force_refresh: bool = False) -> list[dict]:
    if not _ia_provedor_ativo("gemini"):
        return []
    return [
        {"name": "gemini:gemini-2.5-flash", "display_name": "Gemini API 2.5 Flash", "description": "Google Gemini API generateContent"},
        {"name": "gemini:gemini-2.5-pro", "display_name": "Gemini API 2.5 Pro", "description": "Google Gemini API generateContent"},
        {"name": "gemini:gemini-2.0-flash", "display_name": "Gemini API 2.0 Flash", "description": "Google Gemini API generateContent"},
    ]


def _listar_modelos_gemini_api_desativada(force_refresh: bool = False) -> list[dict]:
    return []


def _modelo_eh_gemini_api(model_name: str) -> bool:
    return str(model_name or "").strip().lower().startswith("gemini:")


def _ia_compactar_mensagem_chat(mensagem: str, limite: int = IA_CHAT_MESSAGE_COMPACT_TARGET_CHARS) -> str:
    texto = str(mensagem or "").strip()
    limite = max(1000, min(int(limite or IA_CHAT_MESSAGE_COMPACT_TARGET_CHARS), IA_CHAT_MESSAGE_MAX_CHARS))
    if len(texto) <= limite:
        return texto
    marcador = "\n\n[Mensagem compactada automaticamente para caber no limite da IA.]\n\n"
    espaco = max(1, limite - len(marcador))
    inicio_len = max(600, int(espaco * 0.68))
    fim_len = max(300, espaco - inicio_len)
    if inicio_len + fim_len > espaco:
        fim_len = max(0, espaco - inicio_len)
    return (texto[:inicio_len].rstrip() + marcador + texto[-fim_len:].lstrip())[:limite]


def _chamar_openai_responses(payload: IAChatRequest, client_id: str) -> str:
    _ia_validar_provedor_ativo("openai")

    def _resposta_fallback_simpatico(pergunta: str) -> str:
        pergunta_limpa = str(pergunta or "").strip()
        if not pergunta_limpa:
            return (
                "Oi! Eu estou aqui para ajudar. Pode me enviar sua pergunta novamente e eu te respondo com prazer."
            )
        return (
            "Oi! Estou com uma instabilidade momentanea para gerar a resposta completa agora, "
            "mas continuo disponivel para ajudar.\n\n"
            "Se quiser, reformule em uma frase curta que eu tento novamente em seguida."
        )

    api_key = _obter_openai_api_key()
    if not api_key:
        logger.warning("[IA] OPENAI_API_KEY ausente. Retornando fallback simpatico.")
        return _resposta_fallback_simpatico(payload.message)

    _MODELOS_PERMITIDOS = {
        "gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4", "gpt-5.5",
    }
    _model_env = (os.getenv("OPENAI_MODEL") or "gpt-5.4-nano").strip()
    _model_req = str(payload.model or "").strip()
    model = _model_req if _model_req in _MODELOS_PERMITIDOS else _model_env
    mensagem = str(payload.message or "").strip()
    anexos = _ia_chat_normalizar_anexos(payload)
    ctx_payload = payload.context if isinstance(payload.context, dict) else {}
    modo_rapido = bool(ctx_payload.get("modo_rapido_sidebar"))
    fluxo_perguntas_publicas_v2 = str(ctx_payload.get("tipo") or "").strip() == "novo_fluxo_perguntas_v2"
    desativa_recursos_chat = _ia_contexto_desativa_recursos_chat(ctx_payload)
    usa_contexto = False if (modo_rapido or desativa_recursos_chat) else _ia_chat_usa_contexto_tela(mensagem, anexos)
    if not modo_rapido and not desativa_recursos_chat and isinstance(payload.context, dict) and (payload.context.get("usuario_atual") or payload.context.get("memoria_conversas_usuario")):
        usa_contexto = True

    if _ia_chat_eh_saudacao_curta(mensagem) and not anexos:
        return _ia_chat_resposta_saudacao(payload)

    if not mensagem and not anexos:
        raise HTTPException(status_code=400, detail="Mensagem vazia.")
    if len(mensagem) > IA_CHAT_MESSAGE_MAX_CHARS:
        logger.warning("[IA] Mensagem longa (%s chars) compactada antes da OpenAI.", len(mensagem))
        mensagem = _ia_compactar_mensagem_chat(mensagem)

    rag_query = mensagem or " ".join([a.get("name") or "anexo" for a in anexos])
    contexto_rag = _ia_rag_contexto(rag_query, client_id) if usa_contexto else ""

    historico = []
    for item in (payload.history or [])[-8:]:
        role = "assistant" if item.get("role") == "assistant" else "user"
        content = str(item.get("content") or "").strip()
        if content:
            historico.append({"role": role, "content": content[:1500]})

    if fluxo_perguntas_publicas_v2:
        system_prompt = (
            "Voce responde perguntas publicas de pre-venda do Mercado Livre como a equipe da loja. "
            "Nunca se apresente como IA, assistente, Vertex, Gemini ou JK Sistema. "
            "Use a busca web e o grounding apenas para entender o link do anuncio e a pergunta do comprador. "
            "Nao ofereca contato externo e nao invente informacao ausente. "
            "Responda somente no formato solicitado pelo prompt do app."
        )
    elif modo_rapido:
        system_prompt = (
            "Voce e o assistente IA do JK Sistema. Responda em portugues do Brasil, "
            "de forma curta, humana e direta. Use o historico recente somente quando for necessario "
            "para entender pedidos como repetir, confirmar ou continuar."
        )
    else:
        system_prompt = (
            "VocÃƒÂª ÃƒÂ© o assistente IA do JK Sistema. Responda sempre em portuguÃƒÂªs do Brasil, "
            "com tom simpÃƒÂ¡tico, cordial, humano e profissional. "
            "Escreva como uma pessoa experiente ajudando outra pessoa, com linguagem natural e acolhedora. "
            "Cumprimente de forma breve quando fizer sentido, sem exagero. "
            "Quando o contexto informar o nome do usuario, use esse nome de forma natural para manter continuidade. "
            "Responda exatamente ao que o usuario pediu e nao antecipe analises extras. "
            "Nao traga resumo automatico da tela, numeros ou listas se isso nao foi solicitado. "
            "Se a mensagem for ambigua, curta ou genÃƒÂ©rica, responda de forma simples e natural, sem puxar dados da tela. "
            "Evite respostas roboticas ou muito duras. "
            "Prefira frases curtas, claras e diretas; use listas apenas quando realmente ajudarem. "
            "Se a pergunta pedir explicaÃƒÂ§ÃƒÂ£o, explique de forma didÃƒÂ¡tica e prÃƒÂ¡tica. "
            "Quando a pergunta for sobre o contexto da tela, use apenas os dados relevantes e diga se algo estiver faltando. "
            "Quando houver anexos (imagens/arquivos), considere o conteÃƒÂºdo dos anexos na resposta. "
            "Quando houver contexto de busca web, use essas fontes externas com cautela e deixe claro quando a informaÃƒÂ§ÃƒÂ£o veio da internet. "
            "Se a resposta usar internet, cite ao final 2 a 4 fontes curtas com nome do site e, quando houver, a data publicada. "
            "Se a pergunta pedir noticias do dia, priorize noticias recentes e mencione que se tratam de manchetes/resumos coletados na web. "
            "Quando houver resultados de funÃƒÂ§ÃƒÂµes do backend, trate esses resultados como a fonte mais confiÃƒÂ¡vel para nÃƒÂºmeros e fatos operacionais. "
            "Quando comparar meses, perÃƒÂ­odos, lojas ou SKUs com duas ou mais colunas de valores, responda preferencialmente em tabela Markdown. "
            "Em comparaÃƒÂ§ÃƒÂµes financeiras, use colunas separadas como SKU, Produto, MÃƒÂªs/PerÃƒÂ­odo, Quantidade, Valor vendido, Valor devolvido e VariaÃƒÂ§ÃƒÂ£o; nÃ£o use barras verticais dentro de listas. "
            "Nunca invente totais, SKUs, preÃ§os ou datas. "
            "Quando fizer sentido, finalize com uma sugestÃƒÂ£o curta de prÃƒÂ³ximo passo. "
            "Evite texto longo e repetitivo; seja claro, acionÃƒÂ¡vel e focado no que o usuÃƒÂ¡rio pediu."
        )
        if not desativa_recursos_chat:
            system_prompt += _ia_chat_bloco_prompt_analise_especialista(
                mensagem,
                payload.page,
                payload.context if isinstance(payload.context, dict) else None,
            )
            system_prompt += _ia_treinamento_ppv_bloco_prompt(
                client_id,
                payload.page,
                payload.context if isinstance(payload.context, dict) else None,
            )

    input_messages = [{"role": "system", "content": system_prompt}]
    input_messages.extend(historico)
    pergunta_usuario = mensagem or "Analise os anexos enviados e responda de forma objetiva."
    resumo_anexos = ""
    if anexos:
        itens = ", ".join([f"{a.get('name')} ({a.get('mime_type')})" for a in anexos])
        resumo_anexos = f"\n\nAnexos enviados pelo usuÃƒÂ¡rio: {itens}"

    contexto_tela = f"Contexto da tela em JSON:\n{_montar_contexto_ia(payload, client_id)}" if usa_contexto else ""
    texto_estoque = _ia_estoque_texto(client_id) if (usa_contexto and _ia_chat_deve_anexar_estoque_contexto(mensagem)) else ""
    bloco_estoque = f"\n\n{texto_estoque}" if texto_estoque else ""
    # SKUs vendidos no perÃƒÂ­odo lidos direto do banco de dados
    bloco_vendas = ""
    bloco_vendas_exato = ""
    bloco_web = ""
    bloco_funcoes = ""
    if usa_contexto and _ia_chat_deve_anexar_vendas_db_contexto(mensagem):
        try:
            ctx = payload.context if isinstance(payload.context, dict) else {}
            _v_ini = str(ctx.get("data_inicio") or "").strip()
            _v_fim = str(ctx.get("data_fim") or "").strip()
            _v_loja = str(ctx.get("loja") or "").strip()
            texto_vendas = _ia_vendas_db_texto(client_id, _v_ini, _v_fim, _v_loja)
            bloco_vendas = f"\n\n{texto_vendas}" if texto_vendas else ""
            texto_vendas_exato = _ia_vendas_contexto_exato(mensagem, client_id, ctx)
            bloco_vendas_exato = f"\n\n{texto_vendas_exato}" if texto_vendas_exato else ""
        except Exception:
            bloco_vendas = ""
            bloco_vendas_exato = ""
    if not modo_rapido and _ia_chat_precisa_busca_web(mensagem, payload.page, payload.context if isinstance(payload.context, dict) else None):
        try:
            query_web = str(ctx_payload.get("web_search_query") or mensagem).strip()
            texto_web = _ia_web_contexto(query_web, client_id)
            bloco_web = f"\n\n{texto_web}" if texto_web else ""
        except Exception:
            bloco_web = ""
    if not modo_rapido and not desativa_recursos_chat and not fluxo_perguntas_publicas_v2:
        try:
            texto_funcoes = _ia_chat_contexto_funcoes(payload, client_id)
            bloco_funcoes = f"\n\n{texto_funcoes}" if texto_funcoes else ""
        except Exception as exc:
            logger.warning(f"[IA TOOLS] Falha ao executar funcoes no chat OpenAI: {exc}")
            bloco_funcoes = ""
    contexto_recuperado = f"\n\nContexto recuperado por busca semantica:\n{contexto_rag}" if contexto_rag else ""
    bloco_contexto = f"{contexto_tela}{bloco_estoque}{bloco_vendas}{bloco_vendas_exato}{bloco_web}{bloco_funcoes}{contexto_recuperado}{resumo_anexos}" if (contexto_tela or bloco_estoque or bloco_vendas or bloco_vendas_exato or bloco_web or bloco_funcoes or contexto_recuperado or resumo_anexos) else ""

    user_content = [{
        "type": "input_text",
        "text": (
            f"{bloco_contexto}\n\n" if bloco_contexto else ""
            f"Pergunta do usuÃƒÂ¡rio:\n{pergunta_usuario}\n\n"
            "InstruÃƒÂ§ÃƒÂ£o adicional: responda de forma humana e natural, com foco no que foi pedido "
            "e destacando apenas as informaÃƒÂ§ÃƒÂµes mais relevantes. "
            "Se a resposta comparar valores entre meses, perÃƒÂ­odos ou SKUs, use tabela Markdown com cabeÃƒÂ§alho e separador. "
            "Nao inclua dados nao solicitados."
        ),
    }]

    for anexo in anexos:
        nome = str(anexo.get("name") or "anexo")
        mime = str(anexo.get("mime_type") or "application/octet-stream")
        if mime.startswith("image/"):
            user_content.append({
                "type": "input_image",
                "image_url": f"data:{mime};base64,{anexo.get('data_base64')}",
            })
            continue

        texto_anexo = _ia_chat_extrair_texto_anexo(anexo)
        if texto_anexo:
            user_content.append({
                "type": "input_text",
                "text": f"ConteÃƒÂºdo extraÃƒÂ­do do arquivo '{nome}':\n{texto_anexo}",
            })
        else:
            user_content.append({
                "type": "input_text",
                "text": (
                    f"Arquivo '{nome}' anexado com tipo '{mime}', porÃƒÂ©m sem extraÃƒÂ§ÃƒÂ£o automÃƒÂ¡tica disponÃƒÂ­vel. "
                    "Considere este contexto ao orientar o usuÃƒÂ¡rio."
                ),
            })

    input_messages.append({"role": "user", "content": user_content})

    try:
        request_json = {
            "model": model,
            "input": input_messages,
        }
        if modo_rapido:
            request_json["max_output_tokens"] = 360
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=request_json,
            verify=False,
            timeout=45,
        )
    except requests.RequestException as exc:
        logger.warning(f"[IA] Falha de conexÃƒÂ£o com OpenAI: {exc}")
        return _resposta_fallback_simpatico(mensagem)

    if not resp.ok:
        detail = "Falha ao chamar a OpenAI."
        try:
            erro = resp.json().get("error") or {}
            if erro.get("message"):
                detail = str(erro.get("message"))
        except Exception:
            pass
        logger.warning(f"[IA] OpenAI HTTP {resp.status_code}: {detail}")
        return _resposta_fallback_simpatico(mensagem)

    data = resp.json()
    texto = _extrair_texto_openai_response(data)
    if not texto:
        logger.warning("[IA] OpenAI retornou sem texto. Usando fallback simpatico.")
        return _resposta_fallback_simpatico(mensagem)
    return texto


def _chamar_deepseek_chat(payload: IAChatRequest, client_id: str) -> str:
    _ia_validar_provedor_ativo("deepseek")

    def _resposta_fallback_simpatico(pergunta: str) -> str:
        if not str(pergunta or "").strip():
            return "Oi! Eu estou aqui para ajudar. Pode me enviar sua pergunta novamente."
        return (
            "Oi! Estou com uma instabilidade momentanea para gerar a resposta agora, "
            "mas continuo disponivel para ajudar.\n\n"
            "Se quiser, reformule em uma frase curta que eu tento novamente em seguida."
        )

    api_key = _obter_deepseek_api_key()
    if not api_key:
        logger.warning("[IA] DEEPSEEK_API_KEY ausente. Retornando fallback simpatico.")
        return _resposta_fallback_simpatico(payload.message)

    _MODELOS_PERMITIDOS = {
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "deepseek-chat",
        "deepseek-reasoner",
    }
    _model_req = str(payload.model or "").strip()
    model = _model_req if _model_req in _MODELOS_PERMITIDOS else "deepseek-v4-flash"
    mensagem = str(payload.message or "").strip()
    anexos = _ia_chat_normalizar_anexos(payload)
    ctx_payload = payload.context if isinstance(payload.context, dict) else {}
    modo_rapido = bool(ctx_payload.get("modo_rapido_sidebar"))
    fluxo_perguntas_publicas_v2 = str(ctx_payload.get("tipo") or "").strip() == "novo_fluxo_perguntas_v2"
    desativa_recursos_chat = _ia_contexto_desativa_recursos_chat(ctx_payload)
    usa_contexto = False if (modo_rapido or desativa_recursos_chat) else _ia_chat_usa_contexto_tela(mensagem, anexos)
    if not modo_rapido and not desativa_recursos_chat and isinstance(payload.context, dict) and (payload.context.get("usuario_atual") or payload.context.get("memoria_conversas_usuario")):
        usa_contexto = True

    if _ia_chat_tem_imagem(anexos):
        logger.info("[IA] DeepSeek selecionado com imagem anexada; o modelo nao suporta entrada visual nesta API.")
        return (
            "O modelo DeepSeek selecionado nao suporta analise de imagem nesta integracao. "
            "Se quiser analisar imagem colada ou anexada, selecione um modelo OpenAI no campo de modelo e envie novamente."
        )

    if _ia_chat_eh_saudacao_curta(mensagem) and not anexos:
        return _ia_chat_resposta_saudacao(payload)

    if not mensagem and not anexos:
        raise HTTPException(status_code=400, detail="Mensagem vazia.")
    if len(mensagem) > IA_CHAT_MESSAGE_MAX_CHARS:
        logger.warning("[IA] Mensagem longa (%s chars) compactada antes da OpenAI.", len(mensagem))
        mensagem = _ia_compactar_mensagem_chat(mensagem)

    rag_query = mensagem or " ".join([a.get("name") or "anexo" for a in (anexos or [])])
    contexto_rag = _ia_rag_contexto(rag_query, client_id) if usa_contexto else ""

    historico = []
    for item in (payload.history or [])[-8:]:
        role = "assistant" if item.get("role") == "assistant" else "user"
        content = str(item.get("content") or "").strip()
        if content:
            historico.append({"role": role, "content": content[:1500]})

    if fluxo_perguntas_publicas_v2:
        system_prompt = (
            "Voce responde perguntas publicas de pre-venda do Mercado Livre como a equipe da loja. "
            "Nunca se apresente como IA, assistente, Vertex, Gemini ou JK Sistema. "
            "Use a busca web e o grounding apenas para entender o link do anuncio e a pergunta do comprador. "
            "Nao ofereca contato externo e nao invente informacao ausente. "
            "Responda somente no formato solicitado pelo prompt do app."
        )
    elif modo_rapido:
        system_prompt = (
            "Voce e o assistente IA do JK Sistema. Responda em portugues do Brasil, "
            "de forma curta, humana e direta. Use o historico recente somente quando for necessario "
            "para entender pedidos como repetir, confirmar ou continuar."
        )
    else:
        system_prompt = (
            "VocÃƒÂª ÃƒÂ© o assistente IA do JK Sistema. Responda sempre em portuguÃƒÂªs do Brasil, "
            "com tom simpÃƒÂ¡tico, cordial, humano e profissional. "
            "Escreva como uma pessoa experiente ajudando outra pessoa, com linguagem natural e acolhedora. "
            "Cumprimente de forma breve quando fizer sentido, sem exagero. "
            "Quando o contexto informar o nome do usuario, use esse nome de forma natural para manter continuidade. "
            "Responda exatamente ao que o usuario pediu e nao antecipe analises extras. "
            "Nao traga resumo automatico da tela, numeros ou listas se isso nao foi solicitado. "
            "Se a mensagem for ambigua, curta ou genÃƒÂ©rica, responda de forma simples e natural, sem puxar dados da tela. "
            "Evite respostas roboticas ou muito duras. "
            "Prefira frases curtas, claras e diretas; use listas apenas quando realmente ajudarem. "
            "Se a pergunta pedir explicaÃƒÂ§ÃƒÂ£o, explique de forma didÃƒÂ¡tica e prÃƒÂ¡tica. "
            "Quando a pergunta for sobre o contexto da tela, use apenas os dados relevantes e diga se algo estiver faltando. "
            "Quando houver anexos (imagens/arquivos), considere o conteÃƒÂºdo dos anexos na resposta. "
            "Quando houver contexto de busca web, use essas fontes externas com cautela e deixe claro quando a informaÃƒÂ§ÃƒÂ£o veio da internet. "
            "Se a resposta usar internet, cite ao final 2 a 4 fontes curtas com nome do site e, quando houver, a data publicada. "
            "Se a pergunta pedir noticias do dia, priorize noticias recentes e mencione que se tratam de manchetes/resumos coletados na web. "
            "Quando houver resultados de funÃƒÂ§ÃƒÂµes do backend, trate esses resultados como a fonte mais confiÃƒÂ¡vel para nÃƒÂºmeros e fatos operacionais. "
            "Nunca invente totais, SKUs, preÃ§os ou datas. "
            "Quando fizer sentido, finalize com uma sugestÃƒÂ£o curta de prÃƒÂ³ximo passo. "
            "Evite texto longo e repetitivo; seja claro, acionÃƒÂ¡vel e focado no que o usuÃƒÂ¡rio pediu."
        )
        if not desativa_recursos_chat:
            system_prompt += _ia_chat_bloco_prompt_analise_especialista(
                mensagem,
                payload.page,
                payload.context if isinstance(payload.context, dict) else None,
            )
            system_prompt += _ia_treinamento_ppv_bloco_prompt(
                client_id,
                payload.page,
                payload.context if isinstance(payload.context, dict) else None,
            )

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(historico)

    pergunta_usuario = mensagem or "Analise os anexos enviados e responda de forma objetiva."
    resumo_anexos = ""
    if anexos:
        itens = ", ".join([f"{a.get('name')} ({a.get('mime_type')})" for a in anexos])
        resumo_anexos = f"\n\nAnexos enviados pelo usuÃƒÂ¡rio: {itens}"

    contexto_tela = f"Contexto da tela em JSON:\n{_montar_contexto_ia(payload, client_id)}" if usa_contexto else ""
    texto_estoque = _ia_estoque_texto(client_id) if (usa_contexto and _ia_chat_deve_anexar_estoque_contexto(mensagem)) else ""
    bloco_estoque = f"\n\n{texto_estoque}" if texto_estoque else ""
    bloco_vendas = ""
    bloco_vendas_exato = ""
    bloco_web = ""
    bloco_funcoes = ""
    if usa_contexto and _ia_chat_deve_anexar_vendas_db_contexto(mensagem):
        try:
            ctx = payload.context if isinstance(payload.context, dict) else {}
            texto_vendas = _ia_vendas_db_texto(
                client_id,
                str(ctx.get("data_inicio") or "").strip(),
                str(ctx.get("data_fim") or "").strip(),
                str(ctx.get("loja") or "").strip(),
            )
            bloco_vendas = f"\n\n{texto_vendas}" if texto_vendas else ""
            texto_vendas_exato = _ia_vendas_contexto_exato(mensagem, client_id, ctx)
            bloco_vendas_exato = f"\n\n{texto_vendas_exato}" if texto_vendas_exato else ""
        except Exception:
            bloco_vendas = ""
            bloco_vendas_exato = ""
    if not modo_rapido and _ia_chat_precisa_busca_web(mensagem, payload.page, payload.context if isinstance(payload.context, dict) else None):
        try:
            query_web = str(ctx_payload.get("web_search_query") or mensagem).strip()
            texto_web = _ia_web_contexto(query_web, client_id)
            bloco_web = f"\n\n{texto_web}" if texto_web else ""
        except Exception:
            bloco_web = ""
    if not modo_rapido and not desativa_recursos_chat and not fluxo_perguntas_publicas_v2:
        try:
            texto_funcoes = _ia_chat_contexto_funcoes(payload, client_id)
            bloco_funcoes = f"\n\n{texto_funcoes}" if texto_funcoes else ""
        except Exception as exc:
            logger.warning(f"[IA TOOLS] Falha ao executar funcoes no chat DeepSeek: {exc}")
            bloco_funcoes = ""
    contexto_recuperado = f"\n\nContexto recuperado por busca semantica:\n{contexto_rag}" if contexto_rag else ""
    bloco_contexto = (
        f"{contexto_tela}{bloco_estoque}{bloco_vendas}{bloco_vendas_exato}{bloco_web}{bloco_funcoes}{contexto_recuperado}{resumo_anexos}"
        if (contexto_tela or bloco_estoque or bloco_vendas or bloco_vendas_exato or bloco_web or bloco_funcoes or contexto_recuperado or resumo_anexos)
        else ""
    )

    user_text = (
        (f"{bloco_contexto}\n\n" if bloco_contexto else "")
        + f"Pergunta do usuÃƒÂ¡rio:\n{pergunta_usuario}\n\n"
        "InstruÃƒÂ§ÃƒÂ£o adicional: responda de forma humana e natural, com foco no que foi pedido "
        "e destacando apenas as informaÃƒÂ§ÃƒÂµes mais relevantes. "
        "Se a resposta comparar valores entre meses, perÃƒÂ­odos ou SKUs, use tabela Markdown com cabeÃƒÂ§alho e separador. "
        "Nao inclua dados nao solicitados."
    )
    messages.append({"role": "user", "content": user_text})

    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                **({"max_tokens": 360} if modo_rapido else {}),
            },
            verify=False,
            timeout=60,
        )
    except requests.RequestException as exc:
        logger.warning(f"[IA] Falha de conexÃƒÂ£o com DeepSeek: {exc}")
        return _resposta_fallback_simpatico(mensagem)

    if not resp.ok:
        detail = "Falha ao chamar a DeepSeek."
        try:
            erro = resp.json().get("error") or {}
            if erro.get("message"):
                detail = str(erro.get("message"))
        except Exception:
            pass
        logger.warning(f"[IA] DeepSeek HTTP {resp.status_code}: {detail}")
        return _resposta_fallback_simpatico(mensagem)

    try:
        texto = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("[IA] DeepSeek retornou sem texto. Usando fallback simpatico.")
        return _resposta_fallback_simpatico(mensagem)
    return texto or _resposta_fallback_simpatico(mensagem)


def _chamar_gemini_chat(payload: IAChatRequest, client_id: str) -> str:
    _ia_validar_provedor_ativo("gemini")

    def _resposta_fallback_simpatico(pergunta: str) -> str:
        if not str(pergunta or "").strip():
            return "Oi! Eu estou aqui para ajudar. Pode me enviar sua pergunta novamente."
        return (
            "Oi! Estou com uma instabilidade momentanea para gerar a resposta pela Gemini API agora, "
            "mas continuo disponivel para ajudar.\n\n"
            "Se quiser, reformule em uma frase curta que eu tento novamente em seguida."
        )

    model = _gemini_nome_curto(payload.model) or _gemini_nome_curto(_ia_modelo_chat_configurado()) or "gemini-2.5-flash"
    mensagem = str(payload.message or "").strip()
    anexos = _ia_chat_normalizar_anexos(payload)
    ctx_payload = payload.context if isinstance(payload.context, dict) else {}
    modo_rapido = bool(ctx_payload.get("modo_rapido_sidebar"))
    fluxo_perguntas_publicas_v2 = str(ctx_payload.get("tipo") or "").strip() == "novo_fluxo_perguntas_v2"
    desativa_recursos_chat = _ia_contexto_desativa_recursos_chat(ctx_payload)

    if _ia_chat_eh_saudacao_curta(mensagem) and not anexos:
        return _ia_chat_resposta_saudacao(payload)
    if not mensagem and not anexos:
        raise HTTPException(status_code=400, detail="Mensagem vazia.")
    if len(mensagem) > IA_CHAT_MESSAGE_MAX_CHARS:
        logger.warning("[IA] Mensagem longa (%s chars) compactada antes da Gemini API.", len(mensagem))
        mensagem = _ia_compactar_mensagem_chat(mensagem)

    if fluxo_perguntas_publicas_v2:
        system_prompt = (
            "Voce responde perguntas publicas de pre-venda do Mercado Livre como a equipe da loja. "
            "Nunca se apresente como IA, assistente, Vertex, Gemini ou JK Sistema. "
            "Use a busca web e o grounding apenas para entender o link do anuncio e a pergunta do comprador. "
            "Nao ofereca contato externo e nao invente informacao ausente. "
            "Responda somente no formato solicitado pelo prompt do app."
        )
    elif modo_rapido:
        system_prompt = (
            "Voce e o assistente IA do JK Sistema. Responda em portugues do Brasil, "
            "de forma curta, humana e direta."
        )
    else:
        system_prompt = (
            "Voce e o assistente IA do JK Sistema. Responda sempre em portugues do Brasil, "
            "com tom simpatico, cordial, humano e profissional. "
            "Responda exatamente ao que foi pedido e nao invente totais, SKUs, precos ou datas."
        )
        if not desativa_recursos_chat:
            system_prompt += _ia_chat_bloco_prompt_analise_especialista(
                mensagem,
                payload.page,
                payload.context if isinstance(payload.context, dict) else None,
            )
            system_prompt += _ia_treinamento_ppv_bloco_prompt(
                client_id,
                payload.page,
                payload.context if isinstance(payload.context, dict) else None,
            )

    historico = []
    for item in (payload.history or [])[-8:]:
        role = "model" if item.get("role") == "assistant" else "user"
        content = str(item.get("content") or "").strip()
        if content:
            historico.append({"role": role, "parts": [{"text": content[:1500]}]})

    contexto_tela = f"Contexto da tela em JSON:\n{_montar_contexto_ia(payload, client_id)}" if not modo_rapido and not desativa_recursos_chat else ""
    pergunta_usuario = mensagem or "Analise os anexos enviados e responda de forma objetiva."
    user_text = (
        (f"{contexto_tela}\n\n" if contexto_tela else "")
        + f"Pergunta do usuario:\n{pergunta_usuario}\n\n"
        "Instrucao adicional: responda de forma humana e natural, com foco no que foi pedido."
    )
    user_parts = [{"text": user_text}]
    for anexo in anexos:
        nome = str(anexo.get("name") or "anexo")
        mime = str(anexo.get("mime_type") or "application/octet-stream")
        if mime.startswith("image/"):
            user_parts.append({
                "inlineData": {
                    "mimeType": mime,
                    "data": anexo.get("data_base64"),
                }
            })
            continue
        texto_anexo = _ia_chat_extrair_texto_anexo(anexo)
        if texto_anexo:
            user_parts.append({"text": f"Conteudo extraido do arquivo '{nome}':\n{texto_anexo}"})
        else:
            user_parts.append({"text": f"Arquivo '{nome}' anexado com tipo '{mime}', sem extracao automatica disponivel."})

    contents = list(historico)
    contents.append({"role": "user", "parts": user_parts})
    request_body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": _vertex_generation_config(model, modo_rapido=modo_rapido),
    }

    try:
        return _chamar_gemini_api_direta(model, request_body, _gemini_api_key_para_ia())
    except Exception as exc:
        logger.warning("[IA] Gemini API direta indisponivel: %s", exc)
        return _resposta_fallback_simpatico(mensagem)


def _chamar_vertex_ai_chat(payload: IAChatRequest, client_id: str) -> str:
    _ia_validar_provedor_ativo("vertex")

    def _resposta_fallback_simpatico(pergunta: str) -> str:
        if not str(pergunta or "").strip():
            return "Oi! Eu estou aqui para ajudar. Pode me enviar sua pergunta novamente."
        return (
            "Oi! Estou com uma instabilidade momentanea para gerar a resposta pela Vertex AI agora, "
            "mas continuo disponivel para ajudar.\n\n"
            "Se quiser, reformule em uma frase curta que eu tento novamente em seguida."
        )

    model = _vertex_modelo_nome_curto(payload.model) or _vertex_ai_modelo_padrao()
    mensagem = str(payload.message or "").strip()
    anexos = _ia_chat_normalizar_anexos(payload)
    ctx_payload = payload.context if isinstance(payload.context, dict) else {}
    modo_rapido = bool(ctx_payload.get("modo_rapido_sidebar"))
    fluxo_perguntas_publicas_v2 = str(ctx_payload.get("tipo") or "").strip() == "novo_fluxo_perguntas_v2"
    desativa_recursos_chat = _ia_contexto_desativa_recursos_chat(ctx_payload)
    usa_contexto = False if (modo_rapido or desativa_recursos_chat) else _ia_chat_usa_contexto_tela(mensagem, anexos)
    if not modo_rapido and not desativa_recursos_chat and isinstance(payload.context, dict) and (payload.context.get("usuario_atual") or payload.context.get("memoria_conversas_usuario")):
        usa_contexto = True

    if _ia_chat_eh_saudacao_curta(mensagem) and not anexos:
        return _ia_chat_resposta_saudacao(payload)

    if not mensagem and not anexos:
        raise HTTPException(status_code=400, detail="Mensagem vazia.")
    if len(mensagem) > IA_CHAT_MESSAGE_MAX_CHARS:
        logger.warning("[IA] Mensagem longa (%s chars) compactada antes da Vertex AI.", len(mensagem))
        mensagem = _ia_compactar_mensagem_chat(mensagem)

    headers: dict | None = None
    project_id = ""
    vertex_credentials_error: Exception | None = None
    try:
        headers, project_id = _vertex_ai_headers_e_project()
    except Exception as exc:
        vertex_credentials_error = exc
        logger.warning(f"[IA] Credenciais Vertex AI indisponiveis: {exc}")

    location = _vertex_ai_location()
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    url = (
        f"https://{host}/v1/projects/{project_id}/locations/{location}/publishers/google/models/{model}:generateContent"
        if headers and project_id
        else ""
    )

    rag_query = mensagem or " ".join([a.get("name") or "anexo" for a in (anexos or [])])
    contexto_rag = _ia_rag_contexto(rag_query, client_id) if usa_contexto else ""

    historico = []
    for item in (payload.history or [])[-8:]:
        role = "model" if item.get("role") == "assistant" else "user"
        content = str(item.get("content") or "").strip()
        if content:
            historico.append({"role": role, "parts": [{"text": content[:1500]}]})

    if fluxo_perguntas_publicas_v2:
        system_prompt = (
            "Voce responde perguntas publicas de pre-venda do Mercado Livre como a equipe da loja. "
            "Nunca se apresente como IA, assistente, Vertex, Gemini ou JK Sistema. "
            "Use a busca web e o grounding apenas para entender o link do anuncio e a pergunta do comprador. "
            "Nao ofereca contato externo e nao invente informacao ausente. "
            "Responda somente no formato solicitado pelo prompt do app."
        )
    elif modo_rapido:
        system_prompt = (
            "Voce e o assistente IA do JK Sistema. Responda em portugues do Brasil, "
            "de forma curta, humana e direta. Use o historico recente somente quando for necessario "
            "para entender pedidos como repetir, confirmar ou continuar."
        )
    else:
        system_prompt = (
            "Voce e o assistente IA do JK Sistema. Responda sempre em portugues do Brasil, "
            "com tom simpatico, cordial, humano e profissional. "
            "Quando o contexto informar o nome do usuario, use esse nome de forma natural para manter continuidade. "
            "Responda exatamente ao que o usuario pediu e nao invente totais, SKUs, precos ou datas. "
            "Quando houver contexto de busca web, use essas fontes externas com cautela, deixe claro quando a informacao veio da internet "
            "e cite de 2 a 4 fontes curtas quando a resposta depender desses resultados. "
            "Quando houver resultados de funcoes do backend, trate esses resultados como a fonte mais confiavel. "
            "Quando comparar meses, periodos, lojas ou SKUs com duas ou mais colunas de valores, responda preferencialmente em tabela Markdown. "
            "Se houver anexos, considere o conteudo deles. Seja claro, acionavel e focado."
        )
        if not desativa_recursos_chat:
            system_prompt += _ia_chat_bloco_prompt_analise_especialista(
                mensagem,
                payload.page,
                payload.context if isinstance(payload.context, dict) else None,
            )
            system_prompt += _ia_treinamento_ppv_bloco_prompt(
                client_id,
                payload.page,
                payload.context if isinstance(payload.context, dict) else None,
            )

    pergunta_usuario = mensagem or "Analise os anexos enviados e responda de forma objetiva."
    resumo_anexos = ""
    if anexos:
        itens = ", ".join([f"{a.get('name')} ({a.get('mime_type')})" for a in anexos])
        resumo_anexos = f"\n\nAnexos enviados pelo usuario: {itens}"

    contexto_tela = f"Contexto da tela em JSON:\n{_montar_contexto_ia(payload, client_id)}" if usa_contexto else ""
    texto_estoque = _ia_estoque_texto(client_id) if (usa_contexto and _ia_chat_deve_anexar_estoque_contexto(mensagem)) else ""
    bloco_estoque = f"\n\n{texto_estoque}" if texto_estoque else ""
    bloco_vendas = ""
    bloco_vendas_exato = ""
    bloco_web = ""
    bloco_funcoes = ""
    if usa_contexto and _ia_chat_deve_anexar_vendas_db_contexto(mensagem):
        try:
            ctx = payload.context if isinstance(payload.context, dict) else {}
            texto_vendas = _ia_vendas_db_texto(
                client_id,
                str(ctx.get("data_inicio") or "").strip(),
                str(ctx.get("data_fim") or "").strip(),
                str(ctx.get("loja") or "").strip(),
            )
            bloco_vendas = f"\n\n{texto_vendas}" if texto_vendas else ""
            texto_vendas_exato = _ia_vendas_contexto_exato(mensagem, client_id, ctx)
            bloco_vendas_exato = f"\n\n{texto_vendas_exato}" if texto_vendas_exato else ""
        except Exception:
            bloco_vendas = ""
            bloco_vendas_exato = ""
    if not modo_rapido and _ia_chat_precisa_busca_web(mensagem, payload.page, payload.context if isinstance(payload.context, dict) else None):
        try:
            query_web = str(ctx_payload.get("web_search_query") or mensagem).strip()
            texto_web = _ia_web_contexto(query_web, client_id)
            bloco_web = f"\n\n{texto_web}" if texto_web else ""
        except Exception:
            bloco_web = ""
    if not modo_rapido and not desativa_recursos_chat and not fluxo_perguntas_publicas_v2:
        try:
            texto_funcoes = _ia_chat_contexto_funcoes(payload, client_id)
            bloco_funcoes = f"\n\n{texto_funcoes}" if texto_funcoes else ""
        except Exception as exc:
            logger.warning(f"[IA TOOLS] Falha ao executar funcoes no chat Vertex AI: {exc}")
            bloco_funcoes = ""

    contexto_recuperado = f"\n\nContexto recuperado por busca semantica:\n{contexto_rag}" if contexto_rag else ""
    bloco_contexto = (
        f"{contexto_tela}{bloco_estoque}{bloco_vendas}{bloco_vendas_exato}{bloco_web}{bloco_funcoes}{contexto_recuperado}{resumo_anexos}"
        if (contexto_tela or bloco_estoque or bloco_vendas or bloco_vendas_exato or bloco_web or bloco_funcoes or contexto_recuperado or resumo_anexos)
        else ""
    )
    user_text = (
        (f"{bloco_contexto}\n\n" if bloco_contexto else "")
        + f"Pergunta do usuario:\n{pergunta_usuario}\n\n"
        "Instrucao adicional: responda de forma humana e natural, com foco no que foi pedido. "
        "Se comparar valores, use tabela Markdown. Nao inclua dados nao solicitados."
    )

    user_parts = [{"text": user_text}]
    for anexo in anexos:
        nome = str(anexo.get("name") or "anexo")
        mime = str(anexo.get("mime_type") or "application/octet-stream")
        if mime.startswith("image/"):
            user_parts.append({
                "inlineData": {
                    "mimeType": mime,
                    "data": anexo.get("data_base64"),
                }
            })
            continue
        texto_anexo = _ia_chat_extrair_texto_anexo(anexo)
        if texto_anexo:
            user_parts.append({"text": f"Conteudo extraido do arquivo '{nome}':\n{texto_anexo}"})
        else:
            user_parts.append({"text": f"Arquivo '{nome}' anexado com tipo '{mime}', sem extracao automatica disponivel."})

    contents = list(historico)
    contents.append({"role": "user", "parts": user_parts})

    request_body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": _vertex_generation_config(model, modo_rapido=modo_rapido),
    }
    if _vertex_google_search_grounding_ativo(payload):
        request_body["tools"] = [{"googleSearch": {}}]

    if url and headers:
        try:
            resp = requests.post(
                url,
                headers=headers,
                json=request_body,
                verify=False,
                timeout=60,
            )
        except requests.RequestException as exc:
            logger.warning(f"[IA] Falha de conexao com Vertex AI: {exc}")
        else:
            if resp.ok:
                texto = _extrair_texto_generate_content(resp.json())
                if texto:
                    return texto
                logger.warning("[IA] Vertex AI retornou sem texto. Usando fallback simpatico.")
            else:
                detail = "Falha ao chamar a Vertex AI."
                try:
                    erro = resp.json().get("error") or {}
                    if erro.get("message"):
                        detail = str(erro.get("message"))
                except Exception:
                    pass
                logger.warning(f"[IA] Vertex AI HTTP {resp.status_code}: {detail}")
    elif vertex_credentials_error:
        logger.warning("[IA] Vertex AI sem credenciais validas.")

    return _resposta_fallback_simpatico(mensagem)


def _usuario_pode_escolher_modelo_chat(request: Request, client_id: str) -> bool:
    auth = str(request.headers.get("Authorization") or "").strip()
    if not auth.startswith("Bearer "):
        return False
    token = auth[len("Bearer "):].strip()
    if not token:
        return False
    try:
        payload = decodificar_access_token(token)
        username = str(payload.get("sub") or "").strip()
        token_client_id = str(payload.get("client_id") or "").strip()
        if not username or not token_client_id or token_client_id != str(client_id or "").strip():
            return False
        permissoes = _carregar_permissoes_usuario(username, client_id)
        return permissoes.get("full") is True or permissoes.get("admin_usuarios") is True
    except Exception:
        return False

configure_ia_providers_runtime()

__all__ = [
    name
    for name in globals()
    if (
        (name.startswith("_") and not name.startswith("__"))
        or name.startswith("IA_")
        or name.startswith("FAVORITOS_PESQUISAS_IA")
        or name.startswith("GEMINI_")
        or name.startswith("VERTEX_")
        or name.startswith("ia_")
        or name == "servir_imagem_ia"
    )
]
