"""Siscomex configuration, auth and TTCE helpers for Impostos."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import time
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup
from fastapi import Depends, Header, HTTPException, Request, UploadFile
from jose import JWTError

from backend.schemas import (
    ImpostoRegraRequest,
    ImpostosSimulacaoRequest,
    SimuladorCalculoRequest,
    SiscomexAliquotasRequest,
    SiscomexConfigRequest,
    SiscomexConsultaRequest,
    SiscomexFundamentoOpcionalRequest,
)
from backend.services.impostos_common import *
from backend.services.impostos_context import get_tenant_id, get_tenant_path

logger = None

def _configure_runtime_globals(target_globals, runtime_module=None, peers=None):
    if runtime_module is not None:
        runtime_logger = getattr(runtime_module, "logger", None)
        if runtime_logger is not None:
            target_globals["logger"] = runtime_logger
        for name in (
            "decodificar_access_token",
            "ler_csv_seguro",
            "salvar_csv_seguro",
            "_migrar_arquivo_legado_para_tenant",
            "ARQUIVO_DB_CADASTRO_PRODUTOS",
            "ARQUIVO_DB_PRODUTOS",
            "ARQUIVO_NCM_XLSX",
            "ARQUIVO_NCM1_XLSX",
            "pd",
        ):
            if hasattr(runtime_module, name):
                target_globals[name] = getattr(runtime_module, name)
    if peers:
        target_globals.update(peers)
    return runtime_module


def configure_impostos_siscomex_runtime(runtime_module=None, peers=None):
    return _configure_runtime_globals(globals(), runtime_module, peers)


def _arquivo_config_siscomex(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "siscomex_config.json")


def _extrair_username_do_request(request: Request) -> str:
    auth = str(request.headers.get("Authorization") or "").strip()
    if not auth.startswith("Bearer "):
        return ""
    token = auth[len("Bearer "):].strip()
    if not token:
        return ""
    try:
        payload = decodificar_access_token(token)
        return str(payload.get("sub") or "").strip().lower()
    except JWTError:
        return ""


def _mascarar_segredo(valor: str) -> str:
    txt = str(valor or "")
    if len(txt) <= 4:
        return "*" * len(txt)
    return f"{txt[:2]}{'*' * max(0, len(txt) - 4)}{txt[-2:]}"


def _sanitizar_config_siscomex(payload: dict | None, *, incluir_secret: bool = False) -> dict:
    dados = dict(SISCOMEX_CONFIG_DEFAULT)
    if isinstance(payload, dict):
        dados.update(payload)

    dados["ambiente"] = _normalizar_siscomex_ambiente(dados.get("ambiente"))
    dados["role_type"] = _normalizar_role_type_siscomex(dados.get("role_type"))
    dados["authorization_header_type"] = _normalizar_auth_header_type_siscomex(dados.get("authorization_header_type"))
    dados["tipo_operacao_padrao"] = _normalizar_tipo_operacao_siscomex(dados.get("tipo_operacao_padrao"))
    rt = str(dados.get("regime_tributario") or "simples").strip().lower()
    dados["regime_tributario"] = rt if rt in {"simples", "presumido", "real"} else "simples"
    dados["client_id"] = str(dados.get("client_id") or "").strip()
    dados["client_secret"] = str(dados.get("client_secret") or "").strip()
    try:
        dados["codigo_pais_padrao"] = max(0, int(dados.get("codigo_pais_padrao") or 0))
    except Exception:
        dados["codigo_pais_padrao"] = 0

    if incluir_secret:
        return dados

    saida = dict(dados)
    saida["client_secret"] = _mascarar_segredo(saida.get("client_secret", ""))
    saida["configurada"] = bool(dados.get("client_id") and dados.get("client_secret"))
    return saida


def _carregar_config_siscomex_store(client_id: str) -> dict:
    arquivo = _arquivo_config_siscomex(client_id)
    if not os.path.exists(arquivo):
        return {"version": 2, "scopes": {}, "meta": {}}

    try:
        with open(arquivo, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        logger.exception("[IMPOSTOS][SISCOMEX] Erro ao carregar store de configuraÃƒÂ§ÃƒÂ£o do cliente %s", client_id)
        return {"version": 2, "scopes": {}, "meta": {}}

    if isinstance(payload, dict) and isinstance(payload.get("scopes"), dict):
        return {
            "version": int(payload.get("version") or 2),
            "scopes": dict(payload.get("scopes") or {}),
            "meta": dict(payload.get("meta") or {}),
        }

    # Compatibilidade com layout legado de configuraÃƒÂ§ÃƒÂ£o ÃƒÂºnica por tenant.
    legado = _sanitizar_config_siscomex(payload if isinstance(payload, dict) else {}, incluir_secret=True)
    return {
        "version": 2,
        "scopes": {
            "__default__": legado,
        },
        "meta": {
            "last_scope_key": "__default__",
        },
    }


def _salvar_config_siscomex_store(client_id: str, store: dict) -> None:
    saida = {
        "version": 2,
        "scopes": dict((store or {}).get("scopes") or {}),
        "meta": dict((store or {}).get("meta") or {}),
    }
    with open(_arquivo_config_siscomex(client_id), "w", encoding="utf-8") as f:
        json.dump(saida, f, indent=2, ensure_ascii=False)


def _resolver_scope_siscomex(store: dict, loja_vinculada: str = "", perfil_usuario: str = "") -> tuple[str, dict]:
    scope = _siscomex_scope_payload(loja_vinculada, perfil_usuario)
    scopes = dict((store or {}).get("scopes") or {})
    meta = dict((store or {}).get("meta") or {})
    scope_key = _siscomex_scope_key(scope["loja_vinculada"], scope["perfil_usuario"])

    if isinstance(scopes.get(scope_key), dict):
        return scope_key, scope

    last_scope_key = str(meta.get("last_scope_key") or "").strip()
    if last_scope_key and isinstance(scopes.get(last_scope_key), dict):
        if last_scope_key == "__default__":
            return last_scope_key, _siscomex_scope_payload("", scope["perfil_usuario"])
        loja_scope, _, perfil_scope = last_scope_key.partition("::")
        return last_scope_key, _siscomex_scope_payload(loja_scope, perfil_scope)

    if isinstance(scopes.get("__default__"), dict):
        return "__default__", _siscomex_scope_payload("", scope["perfil_usuario"])

    if len(scopes) == 1:
        unico_scope_key = next(iter(scopes.keys()))
        if unico_scope_key == "__default__":
            return unico_scope_key, _siscomex_scope_payload("", scope["perfil_usuario"])
        loja_scope, _, perfil_scope = unico_scope_key.partition("::")
        return unico_scope_key, _siscomex_scope_payload(loja_scope, perfil_scope)

    return scope_key, scope


def _carregar_config_siscomex(
    client_id: str,
    *,
    loja_vinculada: str = "",
    perfil_usuario: str = "",
    incluir_secret: bool = False,
) -> dict:
    store = _carregar_config_siscomex_store(client_id)
    scopes = dict(store.get("scopes") or {})
    scope_key, scope = _resolver_scope_siscomex(store, loja_vinculada, perfil_usuario)

    dados = dict(SISCOMEX_CONFIG_DEFAULT)
    payload_scope = scopes.get(scope_key)
    if isinstance(payload_scope, dict):
        dados.update(payload_scope)

    cfg = _sanitizar_config_siscomex(dados, incluir_secret=incluir_secret)
    cfg.update(scope)
    cfg["scope_key"] = scope_key
    return cfg


def _salvar_config_siscomex(
    client_id: str,
    payload: dict,
    *,
    loja_vinculada: str = "",
    perfil_usuario: str = "",
) -> dict:
    scope = _siscomex_scope_payload(loja_vinculada, perfil_usuario)
    scope_key = _siscomex_scope_key(scope["loja_vinculada"], scope["perfil_usuario"])
    store = _carregar_config_siscomex_store(client_id)
    scopes = dict(store.get("scopes") or {})

    atual_scope = scopes.get(scope_key)
    if not isinstance(atual_scope, dict):
        atual_scope = {}
    atual = _sanitizar_config_siscomex(atual_scope, incluir_secret=True)
    novo = dict(atual)
    if isinstance(payload, dict):
        novo.update(payload)

    if not str((payload or {}).get("client_secret") or "").strip():
        novo["client_secret"] = atual.get("client_secret", "")

    novo = _sanitizar_config_siscomex(novo, incluir_secret=True)
    scopes[scope_key] = novo
    store["scopes"] = scopes
    meta = dict(store.get("meta") or {})
    meta["last_scope_key"] = scope_key
    store["meta"] = meta
    _salvar_config_siscomex_store(client_id, store)

    saida = _sanitizar_config_siscomex(novo, incluir_secret=False)
    saida.update(scope)
    saida["scope_key"] = scope_key
    return saida


def _siscomex_host(ambiente: str) -> str:
    return SISCOMEX_AMBIENTES[_normalizar_siscomex_ambiente(ambiente)]


def _siscomex_build_auth_headers(cfg: dict) -> dict:
    return {
        "Client-Id": str(cfg.get("client_id") or "").strip(),
        "Client-Secret": str(cfg.get("client_secret") or "").strip(),
        "Role-Type": _normalizar_role_type_siscomex(cfg.get("role_type")),
    }


def _siscomex_build_api_headers(cfg: dict, token_jwt: str, csrf_token: str) -> dict:
    headers = {
        "Role-Type": _normalizar_role_type_siscomex(cfg.get("role_type")),
        "X-CSRF-Token": str(csrf_token or "").strip(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    token = str(token_jwt or "").strip()
    modo = _normalizar_auth_header_type_siscomex(cfg.get("authorization_header_type"))
    if token:
        if modo == "raw":
            headers["Authorization"] = token
        elif modo == "token":
            headers["Authorization"] = f"Token {token}"
        else:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _siscomex_auth_cache_key(cfg: dict) -> str:
    ambiente = _normalizar_siscomex_ambiente(cfg.get("ambiente"))
    client_id = str(cfg.get("client_id") or "").strip()
    role_type = _normalizar_role_type_siscomex(cfg.get("role_type"))
    return f"{ambiente}::{role_type}::{client_id}"


def _siscomex_auth_cache_get(cfg: dict) -> dict | None:
    cache_key = _siscomex_auth_cache_key(cfg)
    agora = time.time()
    with SISCOMEX_AUTH_CACHE_LOCK:
        registro = SISCOMEX_AUTH_CACHE.get(cache_key)
        if not isinstance(registro, dict):
            return None
        expira_em = float(registro.get("expires_at") or 0)
        if expira_em <= agora:
            SISCOMEX_AUTH_CACHE.pop(cache_key, None)
            return None
        return {
            "set_token": str(registro.get("set_token") or "").strip(),
            "csrf_token": str(registro.get("csrf_token") or "").strip(),
            "csrf_expiration": str(registro.get("csrf_expiration") or "").strip(),
            "body": registro.get("body") if isinstance(registro.get("body"), dict) else {},
        }


def _siscomex_auth_cache_put(cfg: dict, auth: dict) -> None:
    cache_key = _siscomex_auth_cache_key(cfg)
    agora = time.time()
    expira_em = agora + 55
    expiracao_raw = str((auth or {}).get("csrf_expiration") or "").strip()
    if expiracao_raw.isdigit():
        try:
            expira_em = max(agora + 5, float(expiracao_raw))
        except Exception:
            expira_em = agora + 55

    registro = {
        "set_token": str((auth or {}).get("set_token") or "").strip(),
        "csrf_token": str((auth or {}).get("csrf_token") or "").strip(),
        "csrf_expiration": expiracao_raw,
        "body": (auth or {}).get("body") if isinstance((auth or {}).get("body"), dict) else {},
        "expires_at": expira_em,
    }
    with SISCOMEX_AUTH_CACHE_LOCK:
        SISCOMEX_AUTH_CACHE[cache_key] = registro


def _siscomex_extract_auth(resp: requests.Response) -> dict:
    body = {}
    try:
        body = resp.json() if resp is not None else {}
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    token_body = str(body.get("token") or "").strip()
    token_header = str((resp.headers or {}).get("Set-Token") or "").strip()
    csrf_header = str((resp.headers or {}).get("X-CSRF-Token") or "").strip()

    return {
        "set_token": token_header or token_body,
        "csrf_token": csrf_header or token_body,
        "csrf_expiration": str((resp.headers or {}).get("X-CSRF-Expiration") or "").strip(),
        "body": body,
    }


def _siscomex_resolver_mensagem_erro(resp: requests.Response) -> str:
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("detail") or payload.get("code") or "").strip() or f"HTTP {resp.status_code}"
    except Exception:
        pass
    txt = str(resp.text or "").strip()
    if not txt:
        return f"HTTP {resp.status_code}"

    # Evita exibir HTML completo (ex.: pÃƒÂ¡gina 403) no frontend.
    if "<" in txt and ">" in txt:
        titulo = re.search(r"<title[^>]*>(.*?)</title>", txt, flags=re.IGNORECASE | re.DOTALL)
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", txt, flags=re.IGNORECASE | re.DOTALL)
        msg = ""
        if h1 and h1.group(1):
            msg = re.sub(r"\s+", " ", h1.group(1)).strip()
        elif titulo and titulo.group(1):
            msg = re.sub(r"\s+", " ", titulo.group(1)).strip()
        if msg:
            return msg
        return f"HTTP {resp.status_code}"

    return txt


def _siscomex_autenticar(cfg: dict) -> dict:
    headers = _siscomex_build_auth_headers(cfg)
    if not headers["Client-Id"] or not headers["Client-Secret"]:
        raise HTTPException(status_code=400, detail="Credenciais Siscomex nÃ£o configuradas.")

    auth_cache = _siscomex_auth_cache_get(cfg)
    if auth_cache and auth_cache.get("set_token") and auth_cache.get("csrf_token"):
        return auth_cache

    host = _siscomex_host(cfg.get("ambiente"))
    url = f"https://{host}/portal/api/autenticar/chave-acesso"

    try:
        resp = requests.post(url, headers=headers, timeout=25)
    except requests.RequestException as e:
        logger.exception("[IMPOSTOS][SISCOMEX] Falha de conexÃƒÂ£o na autenticaÃƒÂ§ÃƒÂ£o")
        raise HTTPException(status_code=502, detail=f"Falha ao autenticar no Siscomex: {e}")

    auth = _siscomex_extract_auth(resp)
    if not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=_siscomex_resolver_mensagem_erro(resp))
    if not auth["set_token"] or not auth["csrf_token"]:
        raise HTTPException(status_code=502, detail="AutenticaÃƒÂ§ÃƒÂ£o Siscomex sem Set-Token/X-CSRF-Token.")
    _siscomex_auth_cache_put(cfg, auth)
    return auth


def _siscomex_post_ttce(cfg: dict, payload: dict) -> dict:
    auth = _siscomex_autenticar(cfg)
    host = _siscomex_host(cfg.get("ambiente"))
    url = f"https://{host}/ttce/api/ext/tratamentos-tributarios/importacao/"
    headers = _siscomex_build_api_headers(cfg, auth["set_token"], auth["csrf_token"])

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
    except requests.RequestException as e:
        logger.exception("[IMPOSTOS][SISCOMEX] Falha de conexÃƒÂ£o na consulta TTCE")
        raise HTTPException(status_code=502, detail=f"Falha ao consultar TTCE: {e}")

    # O TTCE valida estritamente o formato do Authorization. Se o modo configurado
    # nÃ£o for "raw", tenta uma segunda chamada com token bruto para compatibilidade.
    if (
        resp.status_code == 401
        and _normalizar_auth_header_type_siscomex(cfg.get("authorization_header_type")) != "raw"
    ):
        detalhe_401 = _siscomex_resolver_mensagem_erro(resp).lower()
        if "authorization" in detalhe_401 and "formato" in detalhe_401:
            headers_retry = _siscomex_build_api_headers(
                {**cfg, "authorization_header_type": "raw"},
                auth["set_token"],
                auth["csrf_token"],
            )
            try:
                resp = requests.post(url, headers=headers_retry, json=payload, timeout=30)
            except requests.RequestException as e:
                logger.exception("[IMPOSTOS][SISCOMEX] Falha de conexÃƒÂ£o no retry raw da consulta TTCE")
                raise HTTPException(status_code=502, detail=f"Falha ao consultar TTCE: {e}")

    if not resp.ok:
        detalhe = _siscomex_resolver_mensagem_erro(resp)
        if resp.status_code == 401:
            detalhe = f"AutenticaÃƒÂ§ÃƒÂ£o TTCE rejeitada. Verifique Role-Type, ambiente e formato do header Authorization. Detalhe: {detalhe}"
        raise HTTPException(status_code=resp.status_code, detail=detalhe)

    try:
        retorno = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="TTCE respondeu sem JSON valido.")

    return {
        "payload": retorno,
        "auth": auth,
        "response_headers": {
            "x_csrf_token": str((resp.headers or {}).get("X-CSRF-Token") or "").strip(),
            "x_csrf_expiration": str((resp.headers or {}).get("X-CSRF-Expiration") or "").strip(),
        },
    }


def _resumir_resposta_ttce(payload: dict) -> dict:
    tratamentos = payload.get("tratamentosTributarios") if isinstance(payload, dict) else []
    opcionais = payload.get("fundamentosOpcionaisDisponiveis") if isinstance(payload, dict) else []
    tratamentos = tratamentos if isinstance(tratamentos, list) else []
    opcionais = opcionais if isinstance(opcionais, list) else []

    tributos = set()
    atributos = []
    vistos = set()

    for item in tratamentos + opcionais:
        if not isinstance(item, dict):
            continue
        trib_nome = str((item.get("tributo") or {}).get("nome") or "").strip()
        if trib_nome:
            tributos.add(trib_nome)

    for tratamento in tratamentos:
        for mercadoria in tratamento.get("mercadorias") or []:
            for atributo in mercadoria.get("atributos") or []:
                codigo = str(atributo.get("codigo") or "").strip()
                if not codigo or codigo in vistos:
                    continue
                vistos.add(codigo)
                atributos.append({
                    "codigo": codigo,
                    "descricao": str(atributo.get("descricaoCodigo") or "").strip(),
                    "tipo": str(atributo.get("tipoCodigo") or "").strip(),
                    "valor": str(atributo.get("valor") or "").strip(),
                    "descricao_valor": str(atributo.get("descricaoValor") or "").strip(),
                })

    return {
        "tributos_identificados": sorted(tributos),
        "qt_tratamentos": len(tratamentos),
        "qt_fundamentos_opcionais": len(opcionais),
        "atributos_dinamicos": atributos,
    }


def _data_fato_gerador_atual_siscomex() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _payload_ttce_importacao_china(ncm: str, fundamentos: list[dict] | None = None) -> dict:
    payload = {
        "ncm": _normalizar_codigo_fiscal(ncm),
        "codigoPais": SISCOMEX_CODIGO_PAIS_CHINA,
        "dataFatoGerador": _data_fato_gerador_atual_siscomex(),
        "tipoOperacao": SISCOMEX_TIPO_OPERACAO_IMPORTACAO,
    }
    if fundamentos:
        payload["fundamentosOpcionais"] = fundamentos
    return payload


def _normalizar_percentual_ttce(valor: Any) -> float | None:
    if valor is None:
        return None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        num = float(valor)
    else:
        txt = str(valor).strip()
        if not txt:
            return None
        txt = txt.replace("%", "").replace("R$", "").replace(" ", "")
        if "," in txt and "." in txt:
            txt = txt.replace(".", "").replace(",", ".")
        elif "," in txt:
            txt = txt.replace(",", ".")
        try:
            num = float(txt)
        except Exception:
            return None
    if num < 0:
        return None
    if 0 < num <= 1:
        num *= 100.0
    return round(num, 6)


def _buscar_percentual_recursivo_ttce(obj: Any) -> float | None:
    nomes_preferidos = {
        "aliquota",
        "aliquotaadvalorem",
        "aliquotapercentual",
        "percentual",
        "percentualaliquota",
        "percentualtributo",
    }

    def _norm_key(chave: Any) -> str:
        base = unicodedata.normalize("NFKD", str(chave or ""))
        base = "".join(ch for ch in base if not unicodedata.combining(ch))
        return re.sub(r"[^a-z0-9]+", "", base.lower())

    if isinstance(obj, dict):
        for chave, valor in obj.items():
            chave_norm = _norm_key(chave)
            if chave_norm in nomes_preferidos or ("aliquota" in chave_norm and "codigo" not in chave_norm):
                pct = _normalizar_percentual_ttce(valor)
                if pct is not None:
                    return pct
        for valor in obj.values():
            pct = _buscar_percentual_recursivo_ttce(valor)
            if pct is not None:
                return pct
    elif isinstance(obj, list):
        for item in obj:
            pct = _buscar_percentual_recursivo_ttce(item)
            if pct is not None:
                return pct
    return None


def _extrair_aliquotas_ttce(payload: dict) -> dict[str, dict]:
    tributos = {
        "1": ("ii", "II"),
        "2": ("ipi", "IPI"),
        "6": ("pis", "PIS ImportaÃƒÂ§ÃƒÂ£o"),
        "7": ("cofins", "COFINS ImportaÃƒÂ§ÃƒÂ£o"),
    }
    saida = {
        chave: {
            "percentual": None,
            "fonte": "TTCE Ã¢â‚¬â€ Portal ÃƒÅ¡nico Siscomex",
            "erro": "TTCE retornou o tratamento tributÃƒÂ¡rio, mas nÃ£o trouxe alÃƒÂ­quota percentual explÃ­cita.",
        }
        for chave, _label in [("ii", "II"), ("ipi", "IPI"), ("pis", "PIS"), ("cofins", "COFINS")]
    }

    if not isinstance(payload, dict):
        return saida

    itens = []
    for chave_lista in ("tributos", "tributosCalculados", "tributosIncidentes", "tratamentosTributarios"):
        lista = payload.get(chave_lista)
        if isinstance(lista, list):
            itens.extend([x for x in lista if isinstance(x, dict)])

    for item in itens:
        tributo = item.get("tributo") if isinstance(item.get("tributo"), dict) else {}
        codigo = str(tributo.get("codigo") or item.get("codigoTributo") or "").strip().upper()
        if codigo in {"II", "IMPOSTOIMPORTACAO"}:
            codigo = "1"
        elif codigo == "IPI":
            codigo = "2"
        elif codigo == "PIS":
            codigo = "6"
        elif codigo == "COFINS":
            codigo = "7"
        if codigo not in tributos:
            continue
        chave, _nome = tributos[codigo]
        pct = _buscar_percentual_recursivo_ttce(item)
        if pct is not None:
            saida[chave] = {
                "percentual": pct,
                "fonte": "TTCE Ã¢â‚¬â€ Portal ÃƒÅ¡nico Siscomex",
                "erro": None,
            }
        elif saida[chave]["percentual"] is None:
            regime = item.get("regime") if isinstance(item.get("regime"), dict) else {}
            fundamento = item.get("fundamentoLegal") if isinstance(item.get("fundamentoLegal"), dict) else {}
            detalhe = " / ".join(
                x for x in [
                    str(regime.get("nome") or "").strip(),
                    str(fundamento.get("nome") or "").strip(),
                ] if x
            )
            if detalhe:
                saida[chave]["erro"] = f"TTCE informou {detalhe}, sem percentual explÃƒÂ­cito no retorno."

    return saida


async def obter_config_siscomex_impostos(
    request: Request,
    loja_vinculada: str = "",
    perfil_usuario: str = "",
    client_id: str = Depends(get_tenant_id),
):
    perfil = _normalizar_perfil_siscomex(perfil_usuario) or _extrair_username_do_request(request)
    cfg = _carregar_config_siscomex(
        client_id,
        loja_vinculada=loja_vinculada,
        perfil_usuario=perfil,
        incluir_secret=False,
    )
    return {"success": True, "config": cfg}


async def salvar_config_siscomex_impostos(req: SiscomexConfigRequest, request: Request, client_id: str = Depends(get_tenant_id)):
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    persistir_secret = bool(payload.pop("persistir_secret", True))
    loja_vinculada = str(payload.pop("loja_vinculada", "") or "").strip()
    perfil_usuario = _normalizar_perfil_siscomex(payload.pop("perfil_usuario", "")) or _extrair_username_do_request(request)
    if not persistir_secret:
        payload["client_secret"] = ""
    cfg = _salvar_config_siscomex(
        client_id,
        payload,
        loja_vinculada=loja_vinculada,
        perfil_usuario=perfil_usuario,
    )
    return {"success": True, "config": cfg}


async def testar_config_siscomex_impostos(request: Request, req: SiscomexConfigRequest | None = None, client_id: str = Depends(get_tenant_id)):
    payload = (req.model_dump() if req and hasattr(req, "model_dump") else (req.dict() if req else {}))
    loja_vinculada = str(payload.get("loja_vinculada") or "").strip()
    perfil_usuario = _normalizar_perfil_siscomex(payload.get("perfil_usuario") or "")
    if not perfil_usuario and request is not None:
        perfil_usuario = _extrair_username_do_request(request)

    cfg = _carregar_config_siscomex(
        client_id,
        loja_vinculada=loja_vinculada,
        perfil_usuario=perfil_usuario,
        incluir_secret=True,
    )
    if not str(cfg.get("client_id") or "").strip() or not str(cfg.get("client_secret") or "").strip():
        loja_info = _normalizar_loja_siscomex(loja_vinculada) or "Sem loja"
        perfil_info = _normalizar_perfil_siscomex(perfil_usuario) or "sem perfil"
        raise HTTPException(
            status_code=400,
            detail=f"Credenciais Siscomex nÃ£o configuradas para loja '{loja_info}' e perfil '{perfil_info}'. Salve Client-Id e Client-Secret nesse vÃƒÂ­nculo.",
        )
    auth = _siscomex_autenticar(cfg)
    return {
        "success": True,
        "mensagem": "AutenticaÃƒÂ§ÃƒÂ£o Siscomex concluida.",
        "escopo": {
            "loja_vinculada": _normalizar_loja_siscomex(loja_vinculada),
            "perfil_usuario": _normalizar_perfil_siscomex(perfil_usuario),
        },
        "auth": {
            "csrf_expiration": auth.get("csrf_expiration", ""),
            "token_recebido": bool(auth.get("set_token")),
            "csrf_recebido": bool(auth.get("csrf_token")),
        },
    }


async def consultar_ttce_impostos(req: SiscomexConsultaRequest, request: Request, client_id: str = Depends(get_tenant_id)):
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()

    loja_vinculada = str(payload.pop("loja_vinculada") or "").strip()
    perfil_usuario = _normalizar_perfil_siscomex(payload.pop("perfil_usuario") or "") or _extrair_username_do_request(request)
    cfg = _carregar_config_siscomex(
        client_id,
        loja_vinculada=loja_vinculada,
        perfil_usuario=perfil_usuario,
        incluir_secret=True,
    )
    if not str(cfg.get("client_id") or "").strip() or not str(cfg.get("client_secret") or "").strip():
        loja_info = _normalizar_loja_siscomex(loja_vinculada) or "Sem loja"
        perfil_info = _normalizar_perfil_siscomex(perfil_usuario) or "sem perfil"
        raise HTTPException(
            status_code=400,
            detail=f"Credenciais Siscomex nÃ£o configuradas para loja '{loja_info}' e perfil '{perfil_info}'. Salve Client-Id e Client-Secret nesse vÃƒÂ­nculo.",
        )

    payload["ncm"] = _normalizar_codigo_fiscal(payload.get("ncm", ""))
    payload["codigoPais"] = SISCOMEX_CODIGO_PAIS_CHINA
    payload["tipoOperacao"] = SISCOMEX_TIPO_OPERACAO_IMPORTACAO
    payload["dataFatoGerador"] = _data_fato_gerador_atual_siscomex()

    fundamentos = []
    for item in (payload.get("fundamentosOpcionais") or []):
        trib = int(item.get("codigoTributo") or 0)
        regime = int(item.get("codigoRegime") or 0)
        fundamento = int(item.get("codigoFundamentoLegal") or 0)
        if trib <= 0 or regime <= 0 or fundamento <= 0:
            continue
        registro = {
            "codigoTributo": trib,
            "codigoRegime": regime,
            "codigoFundamentoLegal": fundamento,
        }
        nomenclatura = _normalizar_codigo_fiscal(item.get("codigoNomenclaturaAlternativa", ""))
        if nomenclatura:
            registro["codigoNomenclaturaAlternativa"] = nomenclatura
        fundamentos.append(registro)
    payload["fundamentosOpcionais"] = fundamentos

    if not payload["ncm"] or len(payload["ncm"]) != 8:
        raise HTTPException(status_code=400, detail="Informe um NCM com 8 dÃƒÂ­gitos.")
    resultado = _siscomex_post_ttce(cfg, payload)
    return {
        "success": True,
        "escopo": {
            "loja_vinculada": _normalizar_loja_siscomex(loja_vinculada),
            "perfil_usuario": _normalizar_perfil_siscomex(perfil_usuario),
            "regime_tributario": str(cfg.get("regime_tributario") or "simples").strip(),
        },
        "consulta": payload,
        "resumo": _resumir_resposta_ttce(resultado.get("payload") or {}),
        "retorno": resultado.get("payload") or {},
    }


def _siscomex_build_get_header_variants(cfg: dict, token_jwt: str, csrf_token: str) -> list[dict]:
    """Monta variaÃƒÂ§ÃƒÂµes de headers para GET (TEC/TIPI), pois alguns endpoints
    aceitam formatos diferentes de Authorization/CSRF/Role-Type."""
    variantes: list[dict] = []
    vistos: set[tuple[str, str, str]] = set()

    modo_cfg = _normalizar_auth_header_type_siscomex(cfg.get("authorization_header_type"))
    modos = [modo_cfg, "raw", "bearer", "token"]
    modos_unicos = []
    for m in modos:
        if m not in modos_unicos:
            modos_unicos.append(m)

    for modo in modos_unicos:
        base = _siscomex_build_api_headers({**cfg, "authorization_header_type": modo}, token_jwt, csrf_token)
        base.pop("Content-Type", None)

        candidatos = [
            base,
            {k: v for k, v in base.items() if k != "X-CSRF-Token"},
            {k: v for k, v in base.items() if k not in {"X-CSRF-Token", "Role-Type"}},
        ]
        for h in candidatos:
            chave = (
                str(h.get("Authorization") or "").strip(),
                str(h.get("X-CSRF-Token") or "").strip(),
                str(h.get("Role-Type") or "").strip(),
            )
            if chave in vistos:
                continue
            vistos.add(chave)
            variantes.append(h)

    return variantes


def _siscomex_get_tec(host: str, ncm_digitos: str, auth_header_variants: list[dict] | None = None) -> dict:
    """Consulta a TEC (II) no Portal ÃƒÅ¡nico. Requer autenticaÃƒÂ§ÃƒÂ£o."""
    ncm_fmt = _formatar_ncm_pontos(ncm_digitos)
    url = f"https://{host}/tec/api/ext/nomenclatura/ncm/{ncm_fmt}"
    base_headers = {"Accept": "application/json", "User-Agent": "JKSistema/1.0"}
    tentativas = auth_header_variants if auth_header_variants else [{}]
    ultimo_erro = ""

    for extra in tentativas:
        headers = dict(base_headers)
        if extra:
            headers.update(extra)
        try:
            resp = requests.get(url, headers=headers, timeout=15)
        except requests.RequestException as e:
            ultimo_erro = str(e)
            logger.warning("[IMPOSTOS][TEC] Falha de conexÃƒÂ£o para NCM %s: %s", ncm_fmt, e)
            continue

        if resp.ok:
            try:
                dados = resp.json()
            except Exception:
                return {"erro": "Resposta TEC sem JSON valido", "fonte": "TEC"}

            if not isinstance(dados, dict):
                return {"erro": "Formato inesperado na resposta TEC", "fonte": "TEC"}

            return {
                "fonte": "TEC",
                "ncm": ncm_fmt,
                "descricao": str(dados.get("descricao") or dados.get("descricaoNcm") or ""),
                "aliquota_ii": _to_float(dados.get("aliquotaAd") or dados.get("aliquota") or dados.get("aliquotaII") or 0),
                "unidade_estatistica": str(dados.get("unidadeEstatistica") or dados.get("unidade") or ""),
                "raw": dados,
            }

        detalhe = _siscomex_resolver_mensagem_erro(resp)
        detalhe = detalhe[:240] + "..." if len(detalhe) > 240 else detalhe
        ultimo_erro = f"HTTP {resp.status_code}: {detalhe}" if detalhe else f"HTTP {resp.status_code}"
        if resp.status_code not in {401, 403}:
            break

    return {"erro": ultimo_erro or "Falha na consulta TEC", "fonte": "TEC"}


def _siscomex_get_tipi(host: str, ncm_digitos: str, auth_header_variants: list[dict] | None = None) -> dict:
    """Consulta a TIPI (IPI) no Portal ÃƒÅ¡nico. Requer autenticaÃƒÂ§ÃƒÂ£o."""
    ncm_fmt = _formatar_ncm_pontos(ncm_digitos)
    url = f"https://{host}/tipi/api/ext/ncm/{ncm_fmt}"
    base_headers = {"Accept": "application/json", "User-Agent": "JKSistema/1.0"}
    tentativas = auth_header_variants if auth_header_variants else [{}]
    ultimo_erro = ""

    for extra in tentativas:
        headers = dict(base_headers)
        if extra:
            headers.update(extra)
        try:
            resp = requests.get(url, headers=headers, timeout=15)
        except requests.RequestException as e:
            ultimo_erro = str(e)
            logger.warning("[IMPOSTOS][TIPI] Falha de conexÃƒÂ£o para NCM %s: %s", ncm_fmt, e)
            continue

        if resp.ok:
            try:
                dados = resp.json()
            except Exception:
                return {"erro": "Resposta TIPI sem JSON valido", "fonte": "TIPI"}

            if not isinstance(dados, dict):
                return {"erro": "Formato inesperado na resposta TIPI", "fonte": "TIPI"}

            return {
                "fonte": "TIPI",
                "ncm": ncm_fmt,
                "descricao": str(dados.get("descricao") or dados.get("descricaoNcm") or ""),
                "aliquota_ipi": _to_float(dados.get("aliquota") or dados.get("aliquotaIpi") or dados.get("aliquotaIPI") or 0),
                "raw": dados,
            }

        detalhe = _siscomex_resolver_mensagem_erro(resp)
        detalhe = detalhe[:240] + "..." if len(detalhe) > 240 else detalhe
        ultimo_erro = f"HTTP {resp.status_code}: {detalhe}" if detalhe else f"HTTP {resp.status_code}"
        if resp.status_code not in {401, 403}:
            break

    return {"erro": ultimo_erro or "Falha na consulta TIPI", "fonte": "TIPI"}


configure_impostos_siscomex_runtime()

__all__ = [
    name
    for name in globals()
    if (
        (name.startswith("_") and not name.startswith("__"))
        or name.endswith("_impostos")
        or name.startswith("consultar_")
        or name.startswith("obter_")
        or name.startswith("salvar_")
        or name.startswith("testar_")
        or name.startswith("atualizar_")
        or name.startswith("importar_")
        or name.startswith("listar_")
        or name.startswith("simular_")
        or name.startswith("aplicar_")
        or name.startswith("calcular_")
        or name.startswith("configure_")
    )
]
