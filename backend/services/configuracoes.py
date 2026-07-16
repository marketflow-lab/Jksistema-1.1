"""Global configuration persistence helpers."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import HTTPException

from backend.services.env_config import _env_config_bool


CONFIG_GLOBAIS_DEFAULT = {
    "auto_sync_estoque_janela_minutos": 30,
    "ia_modelo_padrao": "codex:gpt-5.5",
    "ia_modelo_perguntas": "codex:gpt-5.6-sol",
    "ia_modelo_pos_venda": "codex:gpt-5.5",
    "ia_modelo_chat": "codex:gpt-5.5",
    "ia_modelo_favoritos": "codex:gpt-5.5",
    "ia_modo_padrao": "modelo",
    "ia_modo_perguntas": "modelo",
    "ia_modo_pos_venda": "modelo",
    "ia_modo_chat": "modelo",
    "ia_modo_favoritos": "modelo",
    "ia_vertex_project_id": "",
    "ia_vertex_location": "global",
    "ia_vertex_model": "gemini-2.5-flash",
    "ia_vertex_service_account_email": "",
    "ia_agent_resource_name": "",
    "ia_agent_endpoint_url": "",
    "ia_favoritos_usar_imagem": False,
    "ia_openai_ativa": True,
    "ia_deepseek_ativa": True,
    "ia_gemini_ativa": True,
    "ia_vertex_ativa": False,
}


_logger = logging.getLogger("jk_sistema")
_arquivo_config_globais = ""
_openai_api_key: Callable[[], str] = lambda: ""
_deepseek_api_key: Callable[[], str] = lambda: ""
_gemini_api_key: Callable[[], str] = lambda: ""
_agent_api_key: Callable[[], str] = lambda: ""
_env_texto: Callable[..., str] = lambda *nomes: ""
_firebase_deve_usar: Callable[[], bool] = lambda: False
_firebase_db: Callable[[], Any] = lambda: None
_firebase_access_obrigatorio: Callable[[], bool] = lambda: False


def configure_configuracoes_context(
    *,
    arquivo_config_globais: str | None = None,
    logger: logging.Logger | None = None,
    openai_api_key: Callable[[], str] | None = None,
    deepseek_api_key: Callable[[], str] | None = None,
    gemini_api_key: Callable[[], str] | None = None,
    agent_api_key: Callable[[], str] | None = None,
    env_texto: Callable[..., str] | None = None,
    firebase_deve_usar: Callable[[], bool] | None = None,
    firebase_db: Callable[[], Any] | None = None,
    firebase_access_obrigatorio: Callable[[], bool] | None = None,
) -> None:
    global _logger, _arquivo_config_globais
    global _openai_api_key, _deepseek_api_key, _gemini_api_key, _agent_api_key
    global _env_texto, _firebase_deve_usar, _firebase_db, _firebase_access_obrigatorio

    if arquivo_config_globais is not None:
        _arquivo_config_globais = str(arquivo_config_globais)
    if logger is not None:
        _logger = logger
    if openai_api_key is not None:
        _openai_api_key = openai_api_key
    if deepseek_api_key is not None:
        _deepseek_api_key = deepseek_api_key
    if gemini_api_key is not None:
        _gemini_api_key = gemini_api_key
    if agent_api_key is not None:
        _agent_api_key = agent_api_key
    if env_texto is not None:
        _env_texto = env_texto
    if firebase_deve_usar is not None:
        _firebase_deve_usar = firebase_deve_usar
    if firebase_db is not None:
        _firebase_db = firebase_db
    if firebase_access_obrigatorio is not None:
        _firebase_access_obrigatorio = firebase_access_obrigatorio


def _normalizar_ia_modo(valor: object) -> str:
    texto = str(valor or "").strip().lower()
    if texto in {"agente", "agent", "cloud_agent", "agente_cloud", "agent_cloud", "vertex_agent", "agent_engine"}:
        return "agente"
    return "modelo"


def _normalizar_configuracoes_globais(payload: Optional[dict] = None) -> dict:
    dados = dict(CONFIG_GLOBAIS_DEFAULT)
    if isinstance(payload, dict):
        dados.update(payload)
    for chave in ("ia_modo_padrao", "ia_modo_perguntas", "ia_modo_pos_venda", "ia_modo_chat", "ia_modo_favoritos"):
        dados[chave] = _normalizar_ia_modo(dados.get(chave))
    for chave in ("ia_agent_resource_name", "ia_agent_endpoint_url"):
        dados[chave] = str(dados.get(chave) or "").strip()
    dados["ia_openai_ativa"] = bool(dados.get("ia_openai_ativa", True))
    dados["ia_deepseek_ativa"] = bool(dados.get("ia_deepseek_ativa", True))
    dados["ia_gemini_ativa"] = bool(dados.get("ia_gemini_ativa", False))
    dados["ia_vertex_ativa"] = bool(dados.get("ia_vertex_ativa", True))
    dados["ia_openai_api_key_configurada"] = bool(_openai_api_key())
    dados["ia_deepseek_api_key_configurada"] = bool(_deepseek_api_key())
    dados["ia_gemini_api_key_configurada"] = bool(_gemini_api_key())
    dados["ia_agent_api_key_configurada"] = bool(_agent_api_key())
    return dados


def _carregar_configuracoes_globais_local() -> dict:
    dados = dict(CONFIG_GLOBAIS_DEFAULT)
    if not _arquivo_config_globais or not os.path.exists(_arquivo_config_globais):
        return _normalizar_configuracoes_globais(dados)
    try:
        with open(_arquivo_config_globais, "r", encoding="utf-8-sig") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            dados.update(payload)
    except Exception:
        _logger.exception("Erro ao carregar configuracoes globais")
    return _normalizar_configuracoes_globais(dados)


def _configuracoes_chaves_sensiveis() -> tuple[str, ...]:
    return (
        "ia_openai_api_key", "ia_openai_api_key_limpar", "ia_openai_api_key_configurada",
        "ia_deepseek_api_key", "ia_deepseek_api_key_limpar", "ia_deepseek_api_key_configurada",
        "ia_gemini_api_key", "ia_gemini_api_key_limpar", "ia_gemini_api_key_configurada",
        "ia_agent_api_key", "ia_agent_api_key_limpar", "ia_agent_api_key_configurada",
    )


def _salvar_configuracoes_globais_local(payload: dict) -> None:
    for chave in _configuracoes_chaves_sensiveis():
        payload.pop(chave, None)
    if not _arquivo_config_globais:
        raise RuntimeError("Arquivo de configuracoes globais nao configurado.")
    os.makedirs(os.path.dirname(_arquivo_config_globais), exist_ok=True)
    with open(_arquivo_config_globais, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def _firebase_configuracoes_globais_collection_name() -> str:
    return _env_texto("FIREBASE_CONFIG_COLLECTION", "JK_FIREBASE_CONFIG_COLLECTION") or "jk_sistema_configuracoes"


def _firebase_configuracoes_globais_doc_id() -> str:
    return _env_texto("FIREBASE_CONFIG_DOC_ID", "JK_FIREBASE_CONFIG_DOC_ID") or "configuracoes_globais"


def _firebase_configuracoes_globais_db():
    if not _firebase_deve_usar():
        return None
    return _firebase_db()


def _carregar_configuracoes_globais_firebase() -> Optional[dict]:
    try:
        db = _firebase_configuracoes_globais_db()
        if db is None:
            return None
        snap = db.collection(_firebase_configuracoes_globais_collection_name()).document(_firebase_configuracoes_globais_doc_id()).get(timeout=5)
        if not snap.exists:
            return None
        payload = snap.to_dict() or {}
        if not isinstance(payload, dict):
            return None
        dados = _normalizar_configuracoes_globais(payload)
        _salvar_configuracoes_globais_local(dict(dados))
        return dados
    except Exception as exc:
        _logger.warning("[CONFIG] Falha ao carregar configuracoes globais no Firebase: %s", exc)
        return None


def _salvar_configuracoes_globais_firebase(payload: dict) -> bool:
    try:
        db = _firebase_configuracoes_globais_db()
        if db is None:
            return False
        data = dict(payload)
        for chave in _configuracoes_chaves_sensiveis():
            data.pop(chave, None)
        data["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        db.collection(_firebase_configuracoes_globais_collection_name()).document(_firebase_configuracoes_globais_doc_id()).set(data, merge=True, timeout=5)
        return True
    except Exception as exc:
        _logger.warning("[CONFIG] Falha ao salvar configuracoes globais no Firebase: %s", exc)
        if _firebase_access_obrigatorio():
            raise HTTPException(status_code=503, detail=f"Firebase indisponivel para salvar configuracoes: {exc}")
        return False


def _configuracoes_globais_ler_firebase() -> bool:
    return _env_config_bool(
        (
            "JK_CONFIG_FIREBASE_READ_ON_GET",
            "JK_CONFIG_FIREBASE_READ",
            "FIREBASE_CONFIG_READ_ON_GET",
        ),
        default=False,
    )


def _configuracoes_globais_salvar_firebase_bloqueante() -> bool:
    return _env_config_bool(
        (
            "JK_CONFIG_FIREBASE_SYNC_BLOCKING",
            "FIREBASE_CONFIG_SYNC_BLOCKING",
        ),
        default=False,
    )


def _salvar_configuracoes_globais_firebase_background(payload: dict) -> None:
    data = dict(payload or {})
    threading.Thread(
        target=_salvar_configuracoes_globais_firebase,
        args=(data,),
        name="configuracoes-globais-firebase-sync",
        daemon=True,
    ).start()


def _carregar_configuracoes_globais() -> dict:
    local = _carregar_configuracoes_globais_local()
    if _arquivo_config_globais and os.path.exists(_arquivo_config_globais) and not _configuracoes_globais_ler_firebase():
        return local
    remoto = _carregar_configuracoes_globais_firebase()
    if remoto:
        local_updated = str(local.get("updated_at") or "").strip()
        remoto_updated = str(remoto.get("updated_at") or "").strip()
        if local_updated and (not remoto_updated or local_updated >= remoto_updated):
            return local
        return remoto
    return local


def _salvar_configuracoes_globais(dados: dict) -> None:
    payload = _normalizar_configuracoes_globais(dados)
    _salvar_configuracoes_globais_local(dict(payload))
    if _configuracoes_globais_salvar_firebase_bloqueante():
        _salvar_configuracoes_globais_firebase(dict(payload))
    else:
        _salvar_configuracoes_globais_firebase_background(dict(payload))
