"""Bling, Vendas DB and Notas helpers with explicit runtime dependencies."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from fastapi import HTTPException

from backend.services.bling import BLING_SESSION, _BlingAdaptiveLimiter, _bling_get_with_adaptive_limit
from backend.services.bling_oauth import exchange_bling_refresh_token
from backend.services.sqlite_coordination import sqlite_lock_for_path

logger = logging.getLogger("jk_sistema")
PASTA_INFO = os.path.join(os.getcwd(), "info")
_get_tenant_path_fn: Callable[[str], str] | None = None
_atualizar_api_loja_fn: Callable[[str, str, str, dict], Any] | None = None
_propagar_lojas_integracoes_fn: Callable[[str, str], Any] | None = None
_criar_progresso_fn: Callable[..., Any] | None = None
_set_progresso_fn: Callable[[str, dict], Any] | None = None
_sync_log_fn: Callable[[str, str], Any] | None = None
_verificar_cancelamento_fn: Callable[[str], Any] | None = None


def _iterar_janelas_data_bling(data_inicio: str, data_fim: str, dias_por_janela: int = 31) -> list[tuple[str, str]]:
    try:
        inicio = datetime.fromisoformat(str(data_inicio or "")[:10]).date()
        fim = datetime.fromisoformat(str(data_fim or "")[:10]).date()
    except Exception:
        return [(data_inicio, data_fim)]

    if fim < inicio:
        return [(data_inicio, data_fim)]

    dias_por_janela = max(1, int(dias_por_janela or 31))
    janelas: list[tuple[str, str]] = []
    cursor = inicio
    while cursor <= fim:
        janela_fim = min(cursor + timedelta(days=dias_por_janela - 1), fim)
        janelas.append((cursor.strftime("%Y-%m-%d"), janela_fim.strftime("%Y-%m-%d")))
        cursor = janela_fim + timedelta(days=1)
    return janelas or [(data_inicio, data_fim)]


def _percentual_intervalo(progress_start: int, progress_end: int, atual: int, total: int) -> int:
    inicio = int(progress_start or 0)
    fim = int(progress_end or inicio)
    if fim < inicio:
        fim = inicio
    total = max(1, int(total or 1))
    atual = min(max(0, int(atual or 0)), total)
    span = max(0, fim - inicio)
    return min(fim, max(inicio, inicio + int((atual / total) * span)))


def _notificar_progresso_nf(
    progress_callback: Callable[[str, int, int, int, str], Any] | None,
    etapa: str,
    atual: int,
    total: int,
    percentual: int,
    mensagem: str,
) -> None:
    if not callable(progress_callback):
        return
    try:
        progress_callback(etapa, int(atual or 0), int(total or 0), int(percentual or 0), str(mensagem or ""))
    except Exception:
        logger.debug("[Bling] Falha ao emitir progresso NF.", exc_info=True)


def _notificar_log_nf(log_callback: Callable[[str], Any] | None, mensagem: str) -> None:
    if not callable(log_callback):
        return
    try:
        log_callback(str(mensagem or ""))
    except Exception:
        logger.debug("[Bling] Falha ao emitir log NF.", exc_info=True)


def configure_bling_vendas_context(
    *,
    pasta_info: str | None = None,
    get_tenant_path: Callable[[str], str] | None = None,
    atualizar_api_loja: Callable[[str, str, str, dict], Any] | None = None,
    propagar_lojas_integracoes_cliente: Callable[[str, str], Any] | None = None,
    criar_progresso: Callable[..., Any] | None = None,
    set_progresso: Callable[[str, dict], Any] | None = None,
    sync_log: Callable[[str, str], Any] | None = None,
    verificar_cancelamento: Callable[[str], Any] | None = None,
    logger_instance: logging.Logger | None = None,
) -> None:
    """Wire the small set of app-level dependencies used by Bling/Vendas helpers."""
    global PASTA_INFO, logger, _get_tenant_path_fn, _atualizar_api_loja_fn, _propagar_lojas_integracoes_fn
    global _criar_progresso_fn, _set_progresso_fn, _sync_log_fn, _verificar_cancelamento_fn
    if pasta_info is not None:
        PASTA_INFO = str(pasta_info)
    if logger_instance is not None:
        logger = logger_instance
    if get_tenant_path is not None:
        _get_tenant_path_fn = get_tenant_path
    if atualizar_api_loja is not None:
        _atualizar_api_loja_fn = atualizar_api_loja
    if propagar_lojas_integracoes_cliente is not None:
        _propagar_lojas_integracoes_fn = propagar_lojas_integracoes_cliente
    if criar_progresso is not None:
        _criar_progresso_fn = criar_progresso
    if set_progresso is not None:
        _set_progresso_fn = set_progresso
    if sync_log is not None:
        _sync_log_fn = sync_log
    if verificar_cancelamento is not None:
        _verificar_cancelamento_fn = verificar_cancelamento


def get_tenant_path(client_id: str) -> str:
    if callable(_get_tenant_path_fn):
        return _get_tenant_path_fn(client_id)
    tenant_path = os.path.join(PASTA_INFO, str(client_id or "default"))
    os.makedirs(tenant_path, exist_ok=True)
    return tenant_path


def atualizar_api_loja(client_id: str, nome_loja: str, api_nome: str, dados_api: dict):
    if callable(_atualizar_api_loja_fn):
        return _atualizar_api_loja_fn(client_id, nome_loja, api_nome, dados_api)
    from backend.services.integracoes import atualizar_api_loja as _atualizar
    return _atualizar(client_id, nome_loja, api_nome, dados_api)


def _shared_sync_propagar_lojas_integracoes_cliente(client_id: str, machine_id: str = ""):
    if callable(_propagar_lojas_integracoes_fn):
        return _propagar_lojas_integracoes_fn(client_id, machine_id)
    from backend.services.shared_sync_user_pairs import _shared_sync_propagar_lojas_integracoes_cliente as _propagar
    return _propagar(client_id, machine_id)


def _criar_progresso(*args, **kwargs):
    if callable(_criar_progresso_fn):
        return _criar_progresso_fn(*args, **kwargs)
    from backend.services.vendas_sync_progress import _criar_progresso as _criar
    return _criar(*args, **kwargs)


def _set_progresso(client_id: str, data: dict):
    if callable(_set_progresso_fn):
        return _set_progresso_fn(client_id, data)
    from backend.services.vendas_sync_progress import _set_progresso as _set
    return _set(client_id, data)


def _sync_log(client_id: str, mensagem: str):
    if callable(_sync_log_fn):
        return _sync_log_fn(client_id, mensagem)
    from backend.services.vendas_sync_progress import _sync_log as _log
    return _log(client_id, mensagem)


def _verificar_cancelamento(client_id: str):
    if callable(_verificar_cancelamento_fn):
        return _verificar_cancelamento_fn(client_id)
    from backend.services.vendas_sync_progress import _verificar_cancelamento as _verificar
    return _verificar(client_id)


def _bling_cancel_callback(client_id: str | None):
    return (lambda: _verificar_cancelamento(client_id)) if client_id else None


def _bling_retry_headers(resp) -> dict[str, str] | None:
    retry_after = str((getattr(resp, "headers", None) or {}).get("Retry-After") or "").strip()
    return {"Retry-After": retry_after} if retry_after else None


def _bling_raise_required_response(resp, action: str) -> None:
    status = int(getattr(resp, "status_code", 0) or 0)
    if status == 429:
        raise HTTPException(
            status_code=429,
            detail=f"Limite de solicitacoes da Bling atingido ao {action}.",
            headers=_bling_retry_headers(resp),
        )
    if status in {500, 502, 503, 504}:
        raise HTTPException(status_code=503, detail=f"Servico Bling indisponivel ao {action}.")
    raise HTTPException(status_code=502, detail=f"Falha HTTP {status or 'invalida'} da Bling ao {action}.")


def _bling_json_data(resp, *, action: str, default, expected_type):
    try:
        payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Resposta invalida da Bling ao {action}.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail=f"Resposta invalida da Bling ao {action}.")
    data = payload.get("data", default)
    if not isinstance(data, expected_type):
        raise HTTPException(status_code=502, detail=f"Resposta invalida da Bling ao {action}.")
    return data


def _bling_required_dict(value: Any, action: str) -> dict:
    if not isinstance(value, dict):
        raise HTTPException(status_code=502, detail=f"Resposta invalida da Bling ao {action}.")
    return value


def _bling_required_items(value: Any, action: str) -> list[dict]:
    payload = _bling_required_dict(value, action)
    items = payload.get("itens")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=502, detail=f"Itens obrigatorios ausentes da Bling ao {action}.")
    for item in items:
        _bling_required_dict(item, action)
    return items


def _bling_required_float(value: Any, action: str) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Valor numerico invalido da Bling ao {action}.") from exc


def _bling_refresh_token(client_id, client_secret, refresh_token):
    """Compatibilidade: fluxos por loja usam o gerenciador single-flight."""
    return exchange_bling_refresh_token(client_id, client_secret, refresh_token)


def _bling_marcar_oauth_invalido(client_id: str, nome_loja: str, cfg: dict | None, motivo: str) -> dict:
    from backend.services.integracoes import marcar_token_bling_invalido

    return marcar_token_bling_invalido(client_id, nome_loja, cfg, motivo)


def _bling_salvar_oauth_valido(client_id: str, nome_loja: str, cfg: dict) -> dict:
    atualizado = dict(cfg or {})
    atualizado["connected"] = True
    atualizado["status"] = "conectado"
    atualizado["motivo"] = ""
    atualizado["oauth_invalid"] = False
    atualizado["shared_without_oauth_tokens"] = False
    atualizado["updated_at"] = str(time.time())
    atualizar_api_loja(client_id, nome_loja, "bling", atualizado)
    return atualizado


def _bling_renovar_token_loja(client_id: str, nome_loja: str, cfg: dict | None) -> dict:
    from backend.services.integracoes import renovar_token_bling_loja

    return renovar_token_bling_loja(client_id, nome_loja, cfg)


def _bling_executar_com_refresh(client_id: str, nome_loja: str, cfg: dict, chamada: Callable[[str], tuple[Any, int]], on_refresh: Optional[Callable[[], None]] = None) -> tuple[Any, int, dict]:
    cfg = dict(cfg or {})
    access_token = str(cfg.get("access_token") or "").strip()
    resultado, status = chamada(access_token)
    if status == 401:
        if on_refresh:
            on_refresh()
        cfg = _bling_renovar_token_loja(client_id, nome_loja, cfg)
        resultado, status = chamada(str(cfg.get("access_token") or ""))
    if status == 401:
        motivo = "Token Bling expirado. Refaça a conexão em Integrações."
        _bling_marcar_oauth_invalido(client_id, nome_loja, cfg, motivo)
    return resultado, status, cfg


def _bling_listar_produtos(access_token):
    url = "https://api.bling.com.br/Api/v3/produtos"
    headers = {"Authorization": f"Bearer {access_token}"}
    produtos = []
    limiter = _BlingAdaptiveLimiter(start_interval=0.08)

    # Busca produtos simples (P) e variações (V), pois alguns SKUs podem existir somente como variação.
    for tipo in ("P", "V"):
        for pagina in range(1, 1000):
            resp = _bling_get_with_adaptive_limit(
                url,
                headers=headers,
                params={"pagina": pagina, "limite": 100, "tipo": tipo},
                timeout=20,
                limiter=limiter,
                max_attempts=3,
            )
            if resp is None:
                return None, 503
            if resp.status_code == 401:
                return None, 401
            if resp.status_code == 429:
                return None, 429
            if resp.status_code != 200:
                return None, resp.status_code
            try:
                data = resp.json().get("data", [])
            except Exception:
                logger.exception("[Bling] Resposta invalida ao listar produtos (tipo=%s, pagina=%s)", tipo, pagina)
                return None, 502
            if not data:
                break
            for p in data:
                ncm_val = p.get("ncm")
                if isinstance(ncm_val, dict):
                    ncm_val = ncm_val.get("codigo") or ncm_val.get("id") or ncm_val.get("valor")
                produtos.append({
                    "id_bling": p.get("id"),
                    "sku": p.get("codigo"),
                    "nome_bling": p.get("nome"),
                    "situacao_bling": p.get("situacao"),
                    "ncm_bling": str(ncm_val or "").strip()
                })

    vistos = set()
    produtos_unicos = []
    for p in produtos:
        chave = str(p.get("id_bling") or "").strip() or str(p.get("sku") or "").strip().upper()
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        produtos_unicos.append(p)
    return produtos_unicos, 200


def _bling_obter_ncm_cest_produto(access_token: str, produto_id: str):
    """Retorna NCM e CEST via endpoint de detalhe do produto; usado apenas em sincronização opcional."""
    pid = str(produto_id or "").strip()
    if not pid:
        return {"ncm": "", "cest": ""}, 200
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = _bling_get_with_adaptive_limit(
        f"https://api.bling.com.br/Api/v3/produtos/{pid}",
        headers=headers,
        timeout=20,
        limiter=_BlingAdaptiveLimiter(start_interval=0.08),
        max_attempts=3,
    )
    if resp is None:
        return {"ncm": "", "cest": ""}, 503
    if resp.status_code == 401:
        return {"ncm": "", "cest": ""}, 401
    if resp.status_code == 429:
        return {"ncm": "", "cest": ""}, 429
    if resp.status_code != 200:
        return {"ncm": "", "cest": ""}, resp.status_code

    try:
        data_det = resp.json().get("data", {}) or {}
        trib = data_det.get("tributacao") or {}

        ncm_det = trib.get("ncm") if isinstance(trib, dict) else data_det.get("ncm")
        if isinstance(ncm_det, dict):
            ncm_det = ncm_det.get("codigo") or ncm_det.get("id") or ncm_det.get("valor")
        ncm_str = str(ncm_det or "").strip()

        cest_str = ""
        if isinstance(trib, dict):
            cest_det = trib.get("cest")
            if cest_det:
                if isinstance(cest_det, dict):
                    cest_det = cest_det.get("codigo") or cest_det.get("id") or cest_det.get("valor")
                cest_str = str(cest_det or "").strip()

        if not cest_str:
            cest_det = data_det.get("cest")
            if cest_det:
                if isinstance(cest_det, dict):
                    cest_det = cest_det.get("codigo") or cest_det.get("id") or cest_det.get("valor")
                cest_str = str(cest_det or "").strip()

        if not cest_str and isinstance(trib, dict):
            for chave in trib.keys():
                if 'cest' in str(chave).lower():
                    valor = trib[chave]
                    if isinstance(valor, dict):
                        valor = valor.get("codigo") or valor.get("id") or valor.get("valor")
                    cest_str = str(valor or "").strip()
                    if cest_str:
                        break

        if not cest_str:
            def _buscar_cest_recursivo(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if "cest" in str(k).lower():
                            if isinstance(v, dict):
                                val = v.get("codigo") or v.get("id") or v.get("valor")
                            else:
                                val = v
                            val = str(val or "").strip()
                            if val:
                                return val
                        found = _buscar_cest_recursivo(v)
                        if found:
                            return found
                elif isinstance(obj, list):
                    for item in obj:
                        found = _buscar_cest_recursivo(item)
                        if found:
                            return found
                return ""

            cest_str = _buscar_cest_recursivo(data_det)

        return {"ncm": ncm_str, "cest": cest_str}, 200
    except Exception as e:
        logger.exception(f"Erro ao buscar NCM/CEST para {pid}: {e}")
        return {"ncm": "", "cest": ""}, 500


def _carregar_mapeamento_unidades():
    """Carrega mapeamento de IDs de unidades para nomes amigáveis."""
    mapeamento_path = os.path.join(PASTA_INFO, "unidades_nomes.json")
    mapeamento_padrao = {}
    
    try:
        if os.path.exists(mapeamento_path):
            with open(mapeamento_path, 'r', encoding='utf-8') as f:
                dados = json.load(f)
                # Remove aliases genéricos antigos (Unidade 2, Unidade Principal, etc.)
                if isinstance(dados, dict):
                    limpo = {}
                    alterado = False
                    for k, v in dados.items():
                        nome = str(v or "").strip()
                        nome_sem_prefixo = re.sub(r"^\s*Unidade\s+", "", nome, flags=re.IGNORECASE).strip()
                        if nome_sem_prefixo != nome:
                            alterado = True
                        nome = nome_sem_prefixo
                        if re.match(r"^Unidade(\s+\d+|\s+Principal)?$", nome, re.IGNORECASE):
                            alterado = True
                            continue
                        limpo[str(k)] = nome
                    if alterado:
                        try:
                            _salvar_mapeamento_unidades(limpo)
                        except Exception:
                            pass
                    return limpo
                return {}
    except Exception:
        pass
    
    # Criar arquivo padrão se não existir
    try:
        os.makedirs(PASTA_INFO, exist_ok=True)
        with open(mapeamento_path, 'w', encoding='utf-8') as f:
            json.dump(mapeamento_padrao, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
    
    return mapeamento_padrao


def _salvar_mapeamento_unidades(mapeamento: dict):
    """Salva mapeamento de IDs de unidades para nomes amigáveis."""
    mapeamento_path = os.path.join(PASTA_INFO, "unidades_nomes.json")
    try:
        os.makedirs(PASTA_INFO, exist_ok=True)
        with open(mapeamento_path, 'w', encoding='utf-8') as f:
            json.dump(mapeamento, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def _bling_buscar_unidade_negocio(access_token, unidade_id, cache=None, mapeamento=None):
    """Busca o nome de uma unidade de negócio por ID.
    Como a API não expõe nomes, usa mapeamento manual."""
    if cache is None:
        cache = {}
    if mapeamento is None:
        mapeamento = {}
    
    unidade_id_str = str(unidade_id)
    
    # Verificar cache
    if unidade_id_str in cache:
        return cache[unidade_id_str]
    
    # Verificar mapeamento manual
    if unidade_id_str in mapeamento:
        nome = mapeamento[unidade_id_str]
        cache[unidade_id_str] = nome
        return nome
    
    # Fallback: usar ID formatado
    # Evita exibir fallback genérico que confunde com nome real da loja.
    fallback = ""
    cache[unidade_id_str] = fallback
    return fallback


def _bling_map_canais_venda_basico(access_token: str):
    """Retorna mapa {idCanalVenda: descricao} para usar como nome real da loja virtual."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = "https://api.bling.com.br/Api/v3/canais-venda"
    mapa = {}
    pagina = 1
    limiter = _BlingAdaptiveLimiter(start_interval=0.08)
    while True:
        resp = _bling_get_with_adaptive_limit(
            url,
            headers=headers,
            params={"pagina": pagina, "limite": 100},
            timeout=20,
            limiter=limiter,
            max_attempts=3,
        )
        if resp is None:
            break
        if resp.status_code == 401:
            return None, 401
        if resp.status_code != 200:
            break

        try:
            data = resp.json().get("data", [])
        except Exception:
            return None, 502
        if not data:
            break

        for item in data:
            cid = str(item.get("id") or "").strip()
            nome = str(item.get("descricao") or "").strip()
            if cid and nome:
                mapa[cid] = nome

        pagina += 1
        if pagina > 50:
            break

    return mapa, 200


def _bling_map_unidades_por_canais(access_token: str):
    """Monta mapa {idUnidadeNegocio: nome} a partir de Canais de Venda (filiais)."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url_canais = "https://api.bling.com.br/Api/v3/canais-venda"
    mapa = {}
    pagina = 1
    limiter = _BlingAdaptiveLimiter(start_interval=0.08)

    while True:
        resp = _bling_get_with_adaptive_limit(
            url_canais,
            headers=headers,
            params={"pagina": pagina, "limite": 100},
            timeout=20,
            limiter=limiter,
            max_attempts=3,
        )
        if resp is None:
            break

        if resp.status_code == 401:
            return None, 401
        if resp.status_code != 200:
            break

        try:
            canais = resp.json().get("data", [])
        except Exception:
            return None, 502
        if not canais:
            break

        for canal in canais:
            cid = canal.get("id")
            if not cid:
                continue
            det = _bling_get_with_adaptive_limit(
                f"https://api.bling.com.br/Api/v3/canais-venda/{cid}",
                headers=headers,
                timeout=20,
                limiter=limiter,
                max_attempts=3,
            )
            if det is None:
                continue

            if det.status_code == 401:
                return None, 401
            if det.status_code != 200:
                continue

            try:
                data_det = det.json().get("data", {}) or {}
            except Exception:
                continue
            for filial in data_det.get("filiais", []) or []:
                uid = str(filial.get("idUnidadeNegocio") or "").strip()
                nome = str(filial.get("unidadeNegocio") or "").strip()
                if uid and nome:
                    mapa[uid] = nome

        pagina += 1
        if pagina > 50:
            break

    return mapa, 200


def _bling_map_lojas_virtuais(access_token: str):
    """Retorna mapa {id_loja_virtual: descricao} da API do Bling."""
    url = "https://api.bling.com.br/Api/v3/configuracoes/lojas-virtuais"
    headers = {"Authorization": f"Bearer {access_token}"}
    mapa = {}
    pagina = 1
    limiter = _BlingAdaptiveLimiter(start_interval=0.08)
    while True:
        resp = _bling_get_with_adaptive_limit(
            url,
            headers=headers,
            params={"situacao": 1, "pagina": pagina, "limite": 100},
            timeout=20,
            limiter=limiter,
            max_attempts=3,
        )
        if resp is None:
            break

        if resp.status_code == 401:
            return None, 401
        if resp.status_code != 200:
            break

        try:
            data = resp.json().get("data", [])
        except Exception:
            return None, 502
        if not data:
            break

        for loja in data:
            lid = str(loja.get("id") or "").strip()
            desc = str(loja.get("descricao") or "").strip()
            if lid and desc:
                mapa[lid] = desc

        pagina += 1
        if pagina > 50:
            break

    return mapa, 200


def _normalizar_nome_loja_virtual_candidato(valor) -> str:
    """Filtra identificadores técnicos e mantém apenas nomes plausíveis para exibição."""
    texto = _remover_prefixo_unidade_nome(valor)
    if not texto:
        return ""
    texto = re.sub(r"^\s*Unidade\s+", "", texto, flags=re.IGNORECASE).strip()
    if not texto:
        return ""
    if re.fullmatch(r"\d{6,}", texto):
        return ""
    if re.fullmatch(r"[A-Z]{2}\d{8,}", texto, re.IGNORECASE):
        return ""
    letras = "".join(ch for ch in texto if ch.isalpha())
    if len(letras) < 3:
        return ""
    return texto


def _remover_prefixo_unidade_nome(valor) -> str:
    texto = str(valor or "").strip()
    if not texto:
        return ""
    return re.sub(r"^\s*Unidade\s+", "", texto, flags=re.IGNORECASE).strip()


def _normalizar_cnpj(valor) -> str:
    txt = re.sub(r"\D", "", str(valor or ""))
    if len(txt) != 14:
        return ""
    return f"{txt[0:2]}.{txt[2:5]}.{txt[5:8]}/{txt[8:12]}-{txt[12:14]}"


def _carregar_mapeamento_lojas_virtuais_cliente(client_id: str):
    """Carrega aliases de loja virtual por tenant para cobrir limitações de escopo da API Bling."""
    try:
        tenant_path = get_tenant_path(client_id)
    except Exception:
        tenant_path = os.path.join(PASTA_INFO, str(client_id or ""))
        os.makedirs(tenant_path, exist_ok=True)

    path_map = os.path.join(tenant_path, "lojas_virtuais_map.json")
    defaults = {
        "loja_id": {
            "203597895": "JKPEÇAS LTDA",
            "203866611": "MagaluJk",
            "205644668": "TikTokJK",
            "205336002": "ShopJKPecasdeck",
            "205518024": "DeckasJK",
        },
        "unidade_id": {
            "611947": "JKPEÇAS LTDA",
            "869084": "MagaluJk",
            "2433376": "TikTokJK",
            "2133327": "ShopJKPecasdeck",
            "2318720": "DeckasJK",
        },
        "cnpj": {
            "47.960.950/0001-21": "MagaluJk",
            "27.415.911/0001-36": "TikTokJK",
            "35.635.824/0001-12": "ShopJKPecasdeck",
        }
    }

    mapa = {}
    if os.path.exists(path_map):
        try:
            with open(path_map, "r", encoding="utf-8") as f:
                mapa = json.load(f) or {}
        except Exception:
            mapa = {}

    changed = False
    for secao in ("loja_id", "unidade_id", "cnpj"):
        if not isinstance(mapa.get(secao), dict):
            mapa[secao] = {}
            changed = True
        for k, v in defaults.get(secao, {}).items():
            if not mapa[secao].get(k):
                mapa[secao][k] = v
                changed = True

    if changed or not os.path.exists(path_map):
        try:
            with open(path_map, "w", encoding="utf-8") as f:
                json.dump(mapa, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    return mapa


def _resolver_nome_loja_virtual(mapa_cliente: dict, loja_id: str = "", unidade_id: str = "", nome_oficial: str = "", intermediador_nome: str = "", intermediador_cnpj: str = "", canal: str = "") -> str:
    mapa_cliente = mapa_cliente or {}
    mapa_loja_id = mapa_cliente.get("loja_id") or {}
    mapa_unidade_id = mapa_cliente.get("unidade_id") or {}
    mapa_cnpj = mapa_cliente.get("cnpj") or {}

    loja_id = str(loja_id or "").strip()
    unidade_id = str(unidade_id or "").strip()

    oficial = _normalizar_nome_loja_virtual_candidato(nome_oficial)
    inter = _normalizar_nome_loja_virtual_candidato(intermediador_nome)
    canal_norm = _normalizar_nome_loja_virtual_candidato(canal)
    nomes_genericos = {"MERCADO LIVRE", "BALCAO/PAINEL", "BALCAO PAINEL", "BALCAO"}

    def _eh_generico(nome: str) -> bool:
        norm = _normalizar_texto(nome)
        if norm in nomes_genericos:
            return True
        compacto = re.sub(r"[^A-Z0-9]+", "", norm)
        if compacto == "MERCADOLIVRE":
            return True
        if compacto.startswith("BALC") and compacto.endswith("PAINEL"):
            return True
        return False

    # Prioriza nomes oficiais vindos das informações da venda/NF.
    if oficial and not _eh_generico(oficial):
        return oficial

    if inter and not _eh_generico(inter):
        return inter

    if canal_norm and not _eh_generico(canal_norm):
        return canal_norm

    if loja_id and mapa_loja_id.get(loja_id):
        alias = str(mapa_loja_id.get(loja_id)).strip()
        return alias
    if unidade_id and mapa_unidade_id.get(unidade_id):
        alias = str(mapa_unidade_id.get(unidade_id)).strip()
        return alias

    cnpj_norm = _normalizar_cnpj(intermediador_cnpj)
    if cnpj_norm and mapa_cnpj.get(cnpj_norm):
        return str(mapa_cnpj.get(cnpj_norm)).strip()

    if canal and str(canal).strip() and not _eh_generico(str(canal).strip()):
        return str(canal).strip()

    return ""


def _normalizar_unidade_negocio_ml(nome_unidade: str = "", canal: str = "", prefer_full: bool = False) -> str:
    """Mantém o nome de unidade vindo da Bling, sem criar rótulos sintéticos."""
    nome = _remover_prefixo_unidade_nome(nome_unidade)
    return nome


def _normalizar_unidade_devolucao_entrada(unidade_nf: str = "", natureza_operacao: str = "", loja_nf: str = "") -> str:
    """Mantém unidade oficial da Bling para devoluções, sem criar lojas virtuais sintéticas."""
    unidade = _remover_prefixo_unidade_nome(unidade_nf)
    loja = _remover_prefixo_unidade_nome(loja_nf)
    return unidade or loja


def _eh_unidade_sintetica_sistema(nome: str) -> bool:
    nome_norm = _normalizar_texto(nome)
    if not nome_norm:
        return False
    sinteticas = {
        "MERCADO LIVRE - LOJA",
        "MERCADO LIVRE LOJA",
        "MERCADO LIVRE - FULL",
        "MERCADO LIVRE FULL",
        "MERCADO LIVRE - LOJA + FULL",
        "MERCADO LIVRE LOJA + FULL",
    }
    if nome_norm in sinteticas:
        return True
    return nome_norm.startswith("UNIDADE JKPECAS")


def _eh_devolucao_nota_entrada(
    natureza_operacao: str = "",
    unidade_nf: str = "",
    loja_nf: str = "",
    finalidade_operacao: str = "",
) -> int:
    """Classifica nota de entrada como devolução com base em natureza/finalidade fiscal."""
    natureza_norm = _normalizar_texto(natureza_operacao)
    finalidade_norm = _normalizar_texto(finalidade_operacao)

    if "DEVOLUCAO" in natureza_norm and "MERCADOR" in natureza_norm:
        return 1

    if "DEVOLUCAO" in finalidade_norm and "MERCADOR" in finalidade_norm:
        return 1

    return 0


def _eh_devolucao_por_cfop_itens(itens_nf: list) -> int:
    """Classifica devolução por CFOP dos itens quando natureza/finalidade não vêm descritivas."""
    if not isinstance(itens_nf, list) or not itens_nf:
        return 0

    cfops_devolucao = {
        "1201", "2201",  # devolução de venda de produção
        "1202", "2202",  # devolução de venda de mercadoria adquirida
    }

    for item in itens_nf:
        if not isinstance(item, dict):
            continue
        cfop = str(item.get("cfop") or "").strip()
        if cfop in cfops_devolucao:
            return 1

    return 0


def _tipo_devolucao_cfop_full_estoque(
    natureza_operacao: str = "",
    finalidade_operacao: str = "",
    itens_nf: list | None = None,
) -> str:
    """Classifica o tipo de devolução para notas no padrão da NF 088344."""
    if not _eh_devolucao_por_cfop_itens(itens_nf or []):
        return ""

    natureza_norm = _normalizar_texto(natureza_operacao)
    finalidade_norm = _normalizar_texto(finalidade_operacao)

    if ("COMPRA" in natureza_norm or "COMERCIALIZ" in natureza_norm) and not finalidade_norm:
        return "Devolução Full Estoque"

    return ""


def _devolucao_deve_ir_para_ml_full(unidade_virtual: str = "", natureza_operacao: str = "") -> bool:
    """Regra de negócio: devoluções Full Estoque ou Compra para Comercialização vão para ML Full."""
    unidade_norm = _normalizar_texto(unidade_virtual)
    natureza_norm = _normalizar_texto(natureza_operacao)

    if unidade_norm in {
        "DEVOLUCAO FULL ESTOQUE",
        "MERCADO LIVRE FULL",
        "MERCADO LIVRE - FULL",
    }:
        return True

    return "COMPRA" in natureza_norm and "COMERCIALIZ" in natureza_norm


def _classificar_unidade_virtual_devolucao(
    unidade_virtual: str = "",
    natureza_operacao: str = "",
    loja_conta: str = "",
) -> str:
    """Classifica devoluções por regra de negócio e loja, com persistência consistente."""
    loja_norm = _normalizar_texto(loja_conta)
    unidade_limpa = _remover_prefixo_unidade_nome(unidade_virtual)
    unidade_norm = _normalizar_texto(unidade_limpa)

    if _devolucao_deve_ir_para_ml_full(unidade_limpa, natureza_operacao):
        return "Mercado Livre Full"

    unidades_genericas = {
        "",
        "MERCADO LIVRE",
        "MERCADO LIVRE - LOJA",
        "MERCADO LIVRE LOJA",
        "BALCAO/PAINEL",
        "BALCAO PAINEL",
    }
    if unidade_limpa and unidade_norm not in unidades_genericas and not _eh_unidade_sintetica_sistema(unidade_limpa):
        return unidade_limpa

    if "IMPORTS" in loja_norm:
        return "Carlos Jose"
    if "CARLOS" in loja_norm:
        return "Carlos Jose"
    if "DECKAS" in loja_norm:
        return "Deckas"
    if "UAI" in loja_norm or "MINEIRINHO" in loja_norm:
        return "Uai Mineirinho"

    return "JK PEÇAS LTDA"


def _sql_filtro_unidade_devolucao(unidade_negocio: str):
    """Retorna condição SQL + parâmetros para filtrar devoluções por unidade virtual com fallback de campo."""
    unidade_valor = str(unidade_negocio or "").strip()
    unidade_norm = unidade_valor.lower()
    if not unidade_norm or unidade_norm == "__todos":
        return "", []

    expr_raw = "coalesce(nullif(trim(unidade_negocio_virtual), ''), nullif(trim(unidade_negocio), ''), '')"
    expr_norm = f"lower({expr_raw})"
    expr_natureza_norm = "lower(coalesce(natureza_operacao, ''))"
    condicao_ml_full = (
        "(("
        + expr_norm
        + " IN ('devolução full estoque', 'devolucao full estoque', 'mercado livre - full', 'mercado livre full')) "
        + "OR ("
        + expr_natureza_norm
        + " LIKE '%compra%' AND "
        + expr_natureza_norm
        + " LIKE '%comercializ%'))"
    )

    if unidade_norm == "__ml_loja_full":
        return "", []
    elif unidade_norm in ("mercado livre - full", "mercado livre full"):
        return f" AND {condicao_ml_full}", []
    elif unidade_norm in ("mercado livre - loja", "mercado livre loja"):
        return f" AND NOT {condicao_ml_full}", []
    elif unidade_norm in ("jk peças ltda", "jk pecas ltda", "jkpeças ltda", "jkpecas ltda", "unidade jkpeças ltda", "unidade jkpecas ltda"):
        return f" AND NOT {condicao_ml_full}", []
    else:
        sql_match, params = _sql_match_variacoes(expr_raw, expr_norm, unidade_valor)
        if not sql_match:
            return "", []
        return f" AND {sql_match}", params


def _sql_filtro_loja_notas_entrada(cur, loja: str):
    """Retorna condição SQL + parâmetros para filtrar notas de entrada por loja selecionada."""
    loja_norm = str(loja or "").strip().lower()
    if not loja_norm or loja_norm == "__todas":
        return "", []

    aliases = _variacoes_nome_loja(loja_norm)
    if not aliases:
        return "", []

    expr_loja = "lower(coalesce(nullif(trim(loja_conta), ''), ''))"
    expr_unidade = "lower(coalesce(nullif(trim(unidade_negocio_virtual), ''), nullif(trim(unidade_negocio), ''), ''))"
    # Filtro estrito por loja para identificar corretamente em qual loja houve devolução.
    # Fallback legado: quando loja_conta estiver vazio, tenta extrair prefixo do id_unico.
    expr_prefixo_id = "lower(case when instr(coalesce(id_unico, ''), '_') > 1 then substr(id_unico, 1, instr(id_unico, '_') - 1) else '' end)"
    placeholders = ", ".join(["?"] * len(aliases))
    sql = (
        f" AND ("
        f"{expr_loja} IN ({placeholders}) "
        f"OR ({expr_loja} = '' AND {expr_prefixo_id} IN ({placeholders})) "
        f"OR {expr_unidade} IN ({placeholders})"
        f")"
    )
    return sql, (aliases + aliases + aliases)


def _bling_obter_numero_nf(access_token: str, nota_fiscal_id: str):
    """Resolve o número visível da NF-e a partir do ID retornado no pedido de venda."""
    nota_fiscal_id = str(nota_fiscal_id or "").strip()
    if not nota_fiscal_id:
        return "", 200

    headers = {"Authorization": f"Bearer {access_token}"}
    urls = [
        f"https://api.bling.com.br/Api/v3/nfe/{nota_fiscal_id}",
        f"https://www.bling.com.br/Api/v3/nfe/{nota_fiscal_id}",
    ]

    def _extrair_numero_nf(obj):
        if isinstance(obj, dict):
            numero = str(obj.get("numero") or obj.get("numeroNota") or "").strip()
            if numero:
                return numero
            for v in obj.values():
                found = _extrair_numero_nf(v)
                if found:
                    return found
        elif isinstance(obj, list):
            for v in obj:
                found = _extrair_numero_nf(v)
                if found:
                    return found
        return ""

    ultimo_status = 502
    limiter = _BlingAdaptiveLimiter(start_interval=0.08)
    for url in urls:
        resp = _bling_get_with_adaptive_limit(
            url,
            headers=headers,
            timeout=20,
            limiter=limiter,
            max_attempts=3,
        )

        if resp is None:
            ultimo_status = 503
            continue

        if resp.status_code == 401:
            return "", 401

        ultimo_status = resp.status_code
        if resp.status_code != 200:
            continue

        try:
            data = resp.json().get("data", {}) or {}
        except Exception:
            ultimo_status = 502
            continue
        numero_nf = _extrair_numero_nf(data)
        return numero_nf, 200

    return "", ultimo_status


def _bling_map_depositos(access_token):
    url = "https://api.bling.com.br/Api/v3/depositos"
    headers = {"Authorization": f"Bearer {access_token}"}
    limiter = _BlingAdaptiveLimiter(start_interval=0.08)
    resp = _bling_get_with_adaptive_limit(
        url,
        headers=headers,
        params={"situacao": 1},
        timeout=20,
        limiter=limiter,
        max_attempts=3,
    )
    if resp is None:
        return None, 503
    if resp.status_code == 401:
        return None, 401
    if resp.status_code == 429:
        return None, 429
    if resp.status_code != 200:
        return None, resp.status_code

    mapa = {}
    try:
        dados = resp.json().get("data", [])
    except Exception:
        logger.exception("[Bling] Resposta invalida ao listar depositos.")
        return None, 502

    for dep in dados:
        did = str(dep.get("id"))
        nome = str(dep.get("descricao", "")).upper()
        if any(t in nome for t in ["FULL", "FULFILLMENT", "MERCADO LIVRE", "ENVIO"]):
            tipo = "FULL"
        elif dep.get("padrao"):
            tipo = "LOJA_PADRAO"
        else:
            tipo = "LOJA_SECUNDARIA"

        if dep.get("desconsiderarSaldo") and tipo != "FULL":
            continue
        mapa[did] = tipo
    return mapa, 200


def _bling_saldos(access_token, produtos_ids, mapa_deps):
    url = "https://api.bling.com.br/Api/v3/estoques/saldos"
    headers = {"Authorization": f"Bearer {access_token}"}
    saldos = {}
    limiter = _BlingAdaptiveLimiter(start_interval=0.1)

    if not isinstance(produtos_ids, (list, tuple, set)):
        produtos_ids = []

    for i in range(0, len(produtos_ids), 50):
        chunk = [str(pid).strip() for pid in list(produtos_ids)[i:i+50] if str(pid).strip()]
        if not chunk:
            continue
        params = [("idsProdutos[]", pid) for pid in chunk]
        resp = _bling_get_with_adaptive_limit(
            url,
            headers=headers,
            params=params,
            timeout=25,
            limiter=limiter,
            max_attempts=3,
        )
        if resp is None:
            return None, 503
        if resp.status_code == 401:
            return None, 401
        if resp.status_code == 429:
            return None, 429
        if resp.status_code != 200:
            return None, resp.status_code

        try:
            itens = resp.json().get("data", [])
        except Exception:
            logger.exception("[Bling] Resposta invalida ao consultar saldos de produtos.")
            return None, 502

        for item in itens:
            pid = str(item.get("produto", {}).get("id") or "").strip()
            if not pid:
                continue
            if pid not in saldos:
                saldos[pid] = {"loja": 0, "full": 0}
            for dep in item.get("depositos", []):
                qtd = float(dep.get("saldoFisico", 0) or 0)
                did = str(dep.get("id"))
                tipo = mapa_deps.get(did)
                if tipo == "FULL":
                    saldos[pid]["full"] += qtd
                elif tipo in ("LOJA_PADRAO", "LOJA_SECUNDARIA"):
                    saldos[pid]["loja"] += qtd
    return saldos, 200


def _variacoes_nome_loja(base: str) -> list[str]:
    base = str(base or "").strip().lower()
    if not base:
        return []

    def _sem_acentos(txt: str) -> str:
        norm = unicodedata.normalize("NFKD", txt or "")
        return "".join(ch for ch in norm if not unicodedata.combining(ch))

    def _limpar_sufixos(txt: str) -> str:
        return re.sub(r"\b(ltda\.?|eireli|me|sa)\b", "", txt, flags=re.IGNORECASE).strip()

    variantes = []
    sem_prefixo = re.sub(r"^\s*unidade\s+", "", base, flags=re.IGNORECASE).strip()
    candidatos = [base, _sem_acentos(base), sem_prefixo, _sem_acentos(sem_prefixo)]

    for cand in candidatos:
        cand = str(cand or "").strip().lower()
        if not cand:
            continue
        variantes.append(re.sub(r"\s+", " ", cand))
        sem_sufixo = _limpar_sufixos(cand).lower().strip()
        if sem_sufixo:
            variantes.append(re.sub(r"\s+", " ", sem_sufixo))
            variantes.append(re.sub(r"\s+", " ", f"{sem_sufixo} ltda".strip()))

    compactos = [re.sub(r"[^a-z0-9]+", "", v) for v in variantes]
    if any(c.startswith("jkpecas") for c in compactos if c):
        variantes.extend([
            "jk peças",
            "jk pecas",
            "jkpeças",
            "jkpecas",
            "jk peças ltda",
            "jk pecas ltda",
            "jkpeças ltda",
            "jkpecas ltda",
        ])

    return list(dict.fromkeys(v for v in variantes if v))


def _variacoes_nome_loja_nocase(loja: str) -> list[str]:
    """Gera aliases preservando acentos para comparacoes SQL com COLLATE NOCASE."""
    base = str(loja or "").strip()
    if not base:
        return []

    def _sem_acentos(txt: str) -> str:
        norm = unicodedata.normalize("NFKD", txt or "")
        return "".join(ch for ch in norm if not unicodedata.combining(ch))

    def _limpar_sufixos(txt: str) -> str:
        return re.sub(r"\b(ltda\.?|eireli|me|sa)\b", "", txt, flags=re.IGNORECASE).strip()

    variantes = []

    sem_prefixo = re.sub(r"^\s*unidade\s+", "", base, flags=re.IGNORECASE).strip()
    candidatos = [base, _sem_acentos(base), sem_prefixo, _sem_acentos(sem_prefixo)]

    for cand in candidatos:
        cand = str(cand or "").strip()
        if not cand:
            continue
        variantes.append(re.sub(r"\s+", " ", cand))

        sem_sufixo = _limpar_sufixos(cand).strip()
        if sem_sufixo:
            variantes.append(re.sub(r"\s+", " ", sem_sufixo))
            variantes.append(re.sub(r"\s+", " ", f"{sem_sufixo} LTDA".strip()))
            variantes.append(re.sub(r"\s+", " ", f"{sem_sufixo} ltda".strip()))

    compactos = [re.sub(r"[^a-z0-9]+", "", _sem_acentos(v).lower()) for v in variantes]
    if any(c.startswith("jkpecas") for c in compactos if c):
        variantes.extend([
            "JK Peças",
            "JK Pecas",
            "JKPEÇAS",
            "JKPECAS",
            "JK Peças LTDA",
            "JK Pecas LTDA",
            "JKPEÇAS LTDA",
            "JKPECAS LTDA",
            "jk peças",
            "jk pecas",
            "jkpeças",
            "jkpecas",
            "jk peças ltda",
            "jk pecas ltda",
            "jkpeças ltda",
            "jkpecas ltda",
        ])

    return list(dict.fromkeys(v for v in variantes if v))


def _sql_match_variacoes(expr_raw: str, expr_norm: str, valor: str):
    aliases_norm = _variacoes_nome_loja(valor)
    aliases_raw = _variacoes_nome_loja_nocase(valor)

    clausulas = []
    params = []

    if aliases_norm:
        placeholders_norm = ", ".join(["?"] * len(aliases_norm))
        clausulas.append(f"{expr_norm} IN ({placeholders_norm})")
        params.extend(aliases_norm)

    if aliases_raw:
        placeholders_raw = ", ".join(["?"] * len(aliases_raw))
        clausulas.append(f"({expr_raw}) COLLATE NOCASE IN ({placeholders_raw})")
        params.extend(aliases_raw)

    if not clausulas:
        return "", []

    return "(" + " OR ".join(clausulas) + ")", params


def _sql_filtro_unidade_com_mapa(unidade_negocio: str, mapa_cliente: dict):
    """Filtro estendido: inclui loja_id/unidade_id do mapa para cobrir registros cujo
    campo unidade_negocio está vazio mas foi resolvido em memória via _resolver_nome_loja_virtual."""
    sql_base, params_base = _sql_filtro_unidade_vendas(unidade_negocio)

    mapa_cliente = mapa_cliente or {}
    nome_norm = _normalizar_texto(str(unidade_negocio or "").strip())
    if not nome_norm or nome_norm == "__TODOS":
        return sql_base, params_base

    loja_ids = [k for k, v in (mapa_cliente.get("loja_id") or {}).items()
                if _normalizar_texto(str(v)) == nome_norm]
    unidade_ids = [k for k, v in (mapa_cliente.get("unidade_id") or {}).items()
                   if _normalizar_texto(str(v)) == nome_norm]

    if not loja_ids and not unidade_ids:
        return sql_base, params_base

    extras = []
    extra_params = []
    if loja_ids:
        placeholders = ", ".join(["?"] * len(loja_ids))
        # Fallback por ID só deve atuar quando o nome da unidade não veio preenchido,
        # para não misturar unidades explícitas (ex.: Mercado Livre Full) com JK Peças.
        extras.append(
            f"(trim(coalesce(loja_id,'')) IN ({placeholders}) "
            f"AND trim(coalesce(unidade_negocio,'')) = '')"
        )
        extra_params.extend(loja_ids)
    if unidade_ids:
        placeholders = ", ".join(["?"] * len(unidade_ids))
        extras.append(
            f"(trim(coalesce(unidade_id,'')) IN ({placeholders}) "
            f"AND trim(coalesce(unidade_negocio,'')) = '')"
        )
        extra_params.extend(unidade_ids)

    if not sql_base:
        # Sem filtro base: usa apenas os IDs
        cond = " OR ".join(extras)
        return f" AND ({cond})", extra_params

    # Combina: (condição_nome OR condição_ids)
    cond_base = sql_base.lstrip(" AND ").strip()
    cond_ids = " OR ".join(extras)
    return f" AND (({cond_base}) OR ({cond_ids}))", params_base + extra_params


def _sql_filtro_unidade_vendas(unidade_negocio: str, expr_base: str = "trim(replace(replace(coalesce(unidade_negocio, ''), 'Unidade ', ''), 'unidade ', ''))"):
    unidade_norm = str(unidade_negocio or "").strip()
    if not unidade_norm or unidade_norm == "__todos":
        return "", []

    expr_norm = f"lower({expr_base})"

    if unidade_norm.lower() == "__ml_loja_full":
        aliases_ml = [
            "mercado livre - loja",
            "mercado livre - full",
            "jkpeças ltda",
            "jkpecas ltda",
            "filial fulfillment 1",
            "mercado livre full",
            "mercado livre loja",
        ]
        aliases_ml_raw = [
            "Mercado Livre - Loja",
            "Mercado Livre - Full",
            "JKPEÇAS LTDA",
            "JKPECAS LTDA",
            "Filial Fulfillment 1",
            "Mercado Livre Full",
            "Mercado Livre Loja",
        ]
        placeholders_norm = ", ".join(["?"] * len(aliases_ml))
        placeholders_raw = ", ".join(["?"] * len(aliases_ml_raw))
        sql = (
            f" AND (({expr_norm} IN ({placeholders_norm})) "
            f"OR (({expr_base}) COLLATE NOCASE IN ({placeholders_raw})))"
        )
        return sql, aliases_ml + aliases_ml_raw

    sql_match, params = _sql_match_variacoes(expr_base, expr_norm, unidade_norm)
    if not sql_match:
        return "", []
    return f" AND {sql_match}", params


def _sql_filtro_loja_vendas(loja: str):
    """Retorna condição SQL + parâmetros para filtrar vendas por aliases de loja."""
    loja_norm = str(loja or "").strip()
    if not loja_norm or loja_norm == "__todas":
        return "", []

    expr_loja_raw = "trim(coalesce(loja_conta, ''))"
    expr_loja_norm = "lower(trim(coalesce(loja_conta, '')))"
    sql_match, params = _sql_match_variacoes(expr_loja_raw, expr_loja_norm, loja_norm)
    if not sql_match:
        return "", []

    return f" AND {sql_match}", params


def _slug_loja_para_arquivo(loja: str) -> str:
    texto = str(loja or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", "_", texto)
    return texto.strip("_") or "loja"


def _get_vendas_db_path(client_id: str, loja: str = None) -> str:
    tenant_path = get_tenant_path(client_id)
    if loja and str(loja).strip() and str(loja).strip() != "__todas":
        slug = _slug_loja_para_arquivo(loja)
        return os.path.join(tenant_path, f"vendas_historico_{slug}.db")
    return os.path.join(tenant_path, "vendas_historico.db")


def _listar_bancos_vendas_tenant(client_id: str, loja: str = None) -> list[str]:
    """Lista os bancos de vendas a consultar para o tenant (1 loja específica ou todas)."""
    tenant_path = get_tenant_path(client_id)
    paths = []

    if loja and str(loja).strip() and str(loja).strip() != "__todas":
        # Mantém banco alvo em prioridade, mas inclui fallback para evitar "sumiço" ao trocar
        # nome de loja/slug entre versões ou quando ainda há dados no banco legado.
        alvo = _get_vendas_db_path(client_id, loja)
        if os.path.exists(alvo):
            paths.append(alvo)

    legado = _get_vendas_db_path(client_id, None)
    if os.path.exists(legado):
        paths.append(legado)
    if os.path.exists(tenant_path):
        for nome in sorted(os.listdir(tenant_path)):
            if not nome.startswith("vendas_historico_") or not nome.endswith(".db"):
                continue
            # Ignora arquivos de backup (ex: vendas_historico_jk_pecas.backup_*.db)
            if ".backup_" in nome:
                continue
            p = os.path.join(tenant_path, nome)
            if os.path.exists(p):
                paths.append(p)
    # remove duplicados preservando ordem
    uniq = []
    seen = set()
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return uniq


def _chave_deduplicacao_venda(item: dict) -> tuple:
    id_unico = str(item.get("id_unico") or "").strip()
    if id_unico:
        return ("id_unico", id_unico)

    return (
        "fallback",
        str(item.get("data") or "")[:10],
        str(item.get("loja_conta") or "").strip().lower(),
        str(item.get("numero") or "").strip(),
        str(item.get("sku") or "").strip().upper(),
        float(item.get("quantidade") or 0),
        float(item.get("valor") or 0),
        str(item.get("comprador") or "").strip().lower(),
    )


def _deduplicar_vendas_consolidadas(itens: list[dict]) -> list[dict]:
    mapa: dict[tuple, dict] = {}

    for item in itens or []:
        chave = _chave_deduplicacao_venda(item)
        atual = mapa.get(chave)
        if atual is None:
            mapa[chave] = item
            continue

        # Mantém o registro mais "rico" quando houver colisão entre bancos.
        score_atual = int(bool(str(atual.get("numero_nf") or "").strip())) + int(bool(str(atual.get("nota_fiscal_id") or "").strip())) + int(bool(str(atual.get("unidade_negocio") or "").strip()))
        score_novo = int(bool(str(item.get("numero_nf") or "").strip())) + int(bool(str(item.get("nota_fiscal_id") or "").strip())) + int(bool(str(item.get("unidade_negocio") or "").strip()))

        if score_novo > score_atual:
            mapa[chave] = item

    return list(mapa.values())


def _deve_excluir_venda_ebazar(devolucao=0, comprador: str = "", canal: str = "") -> bool:
    """Regra de negócio: vendas EBAZAR não entram em listagem/somatórios (exceto devoluções)."""
    try:
        if int(devolucao or 0) == 1:
            return False
    except Exception:
        pass
    comprador_norm = _normalizar_texto(comprador)
    canal_norm = _normalizar_texto(canal)
    return "EBAZAR" in comprador_norm or "EBAZAR" in canal_norm


def _get_vendas_db_unlocked(client_id: str, loja: str = None):
    tenant_path = get_tenant_path(client_id)
    db_path = _get_vendas_db_path(client_id, loja)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vendas (
                id_unico TEXT PRIMARY KEY,
                data TEXT,
                loja_conta TEXT,
                canal TEXT,
                numero TEXT,
                situacao TEXT,
                sku TEXT,
                produto TEXT,
                quantidade REAL,
                valor REAL,
                mes_ano TEXT,
                devolucao INTEGER DEFAULT 0,
                numero_nf TEXT,
                comprador TEXT,
                unidade_negocio TEXT,
                nota_fiscal_id TEXT,
                loja_id TEXT,
                unidade_id TEXT,
                intermediador_nome TEXT,
                intermediador_cnpj TEXT
            )
            """
        )
        # Garantir coluna devolucao em bases antigas
        cols = [row[1] for row in cur.execute("PRAGMA table_info(vendas)").fetchall()]
        if "devolucao" not in cols:
            cur.execute("ALTER TABLE vendas ADD COLUMN devolucao INTEGER DEFAULT 0")
        if "numero_nf" not in cols:
            cur.execute("ALTER TABLE vendas ADD COLUMN numero_nf TEXT")
        if "comprador" not in cols:
            cur.execute("ALTER TABLE vendas ADD COLUMN comprador TEXT")
        if "unidade_negocio" not in cols:
            cur.execute("ALTER TABLE vendas ADD COLUMN unidade_negocio TEXT")
        if "nota_fiscal_id" not in cols:
            cur.execute("ALTER TABLE vendas ADD COLUMN nota_fiscal_id TEXT")
        if "loja_id" not in cols:
            cur.execute("ALTER TABLE vendas ADD COLUMN loja_id TEXT")
        if "unidade_id" not in cols:
            cur.execute("ALTER TABLE vendas ADD COLUMN unidade_id TEXT")
        if "intermediador_nome" not in cols:
            cur.execute("ALTER TABLE vendas ADD COLUMN intermediador_nome TEXT")
        if "intermediador_cnpj" not in cols:
            cur.execute("ALTER TABLE vendas ADD COLUMN intermediador_cnpj TEXT")

        # Migração: remover prefixo "Unidade " dos nomes legados já salvos.
        cur.execute(
            """
            UPDATE vendas
            SET unidade_negocio = ltrim(substr(unidade_negocio, 9))
            WHERE unidade_negocio IS NOT NULL
              AND trim(unidade_negocio) != ''
              AND lower(unidade_negocio) LIKE 'unidade %'
            """
        )

        # Regra de negócio: devoluções não devem vir de saídas/pedidos, apenas de notas de entrada.
        cur.execute("UPDATE vendas SET devolucao = 0 WHERE devolucao IS NULL OR devolucao != 0")
        conn.commit()
    finally:
        conn.close()
    return db_path


def _get_vendas_db(client_id: str, loja: str = None):
    db_path = _get_vendas_db_path(client_id, loja)
    with sqlite_lock_for_path(db_path):
        return _get_vendas_db_unlocked(client_id, loja)


def _bling_listar_vendas(access_token: str, data_inicio: str, data_fim: str, loja_nome: str, client_id: str = None, unidades_cache: dict = None, unidades_mapeamento: dict = None, mapa_lojas_cliente: dict = None):
    """Busca vendas no Bling com detalhes de itens."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url_lista = "https://api.bling.com.br/Api/v3/pedidos/vendas"
    registros = []
    max_paginas = 200
    assinaturas_paginas = set()
    debug_suspeitos_logados = 0
    cache_numero_nf = {}
    unidades_cache = unidades_cache or {}
    unidades_mapeamento = unidades_mapeamento or {}
    mapa_canais, status_canais = _bling_map_canais_venda_basico(access_token)
    if status_canais == 401:
        return None, 401
    mapa_canais = mapa_canais or {}
    mapa_lojas_virtuais, _ = _bling_map_lojas_virtuais(access_token)
    mapa_lojas_virtuais = mapa_lojas_virtuais or {}
    mapa_unidades_api, status_unidades_api = _bling_map_unidades_por_canais(access_token)
    if status_unidades_api == 401:
        return None, 401
    if mapa_unidades_api:
        unidades_mapeamento.update(mapa_unidades_api)
        # Persiste para uso em filtros e sessões futuras
        try:
            _salvar_mapeamento_unidades(unidades_mapeamento)
        except Exception:
            pass

    def _normalizar_data_bling(valor: str) -> str:
        """Converte YYYY-MM-DD / ISO em data curta YYYY-MM-DD (comportamento legado estável)."""
        txt = str(valor or "").strip()
        if not txt:
            return datetime.now().strftime("%Y-%m-%d")

        # ISO com T (ex: 2026-03-30T00:00:00)
        if "T" in txt:
            txt = txt.replace("T", " ")

        # Mantém apenas a parte da data
        if len(txt) >= 10:
            return txt[:10]

        return txt

    pagina = 1
    limiter_lista = _BlingAdaptiveLimiter(start_interval=0.08)
    limiter_detalhe = _BlingAdaptiveLimiter(start_interval=0.06)
    while True:
        # Verificar cancelamento antes de cada requisição
        if client_id:
            _verificar_cancelamento(client_id)

        # Guardrails para evitar travamento infinito na etapa 1
        if pagina > max_paginas:
            raise HTTPException(
                status_code=502,
                detail=f"Listagem de vendas excedeu {max_paginas} páginas. Verifique o filtro de datas ({data_inicio} a {data_fim}) na integração Bling."
            )
        
        # Mantém formato legado (YYYY-MM-DD), que estava estável no fluxo antigo.
        dt_ini = _normalizar_data_bling(data_inicio)
        dt_fim = _normalizar_data_bling(data_fim)
        if client_id:
            pct_pagina = min(30, 8 + max(0, pagina - 1))
            _sync_log(client_id, f"[SYNC] Bling: consultando página {pagina} ({dt_ini} até {dt_fim})")
            _set_progresso(client_id, _criar_progresso("Bling", pagina, 0, pct_pagina, f"Consultando Bling - página {pagina}..."))
        params = {"dataInicial": dt_ini, "dataFinal": dt_fim, "pagina": pagina, "limite": 100}
        resp = _bling_get_with_adaptive_limit(
            url_lista,
            headers=headers,
            params=params,
            timeout=25,
            limiter=limiter_lista,
            max_attempts=3,
            cancel_callback=_bling_cancel_callback(client_id),
        )
        if resp is None:
            raise HTTPException(status_code=503, detail=f"Servico Bling indisponivel ao listar vendas (pagina {pagina}).")
        if resp.status_code == 401:
            return None, 401
        if resp.status_code != 200:
            _bling_raise_required_response(resp, "listar pedidos de venda")

        pedidos = _bling_json_data(
            resp,
            action="listar pedidos de venda",
            default=[],
            expected_type=list,
        )
        if not pedidos:
            break

        pedidos = [
            _bling_required_dict(item, "listar pedidos de venda")
            for item in pedidos
        ]

        # Proteção: se a paginação vier repetindo os mesmos pedidos, interrompe com erro claro.
        assinatura = tuple(str(p.get("id")) for p in pedidos[:8])
        if assinatura in assinaturas_paginas:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Paginação da Bling retornou páginas repetidas (filtro de data possivelmente ignorado). "
                    f"Parado na página {pagina} para evitar loop infinito."
                )
            )
        assinaturas_paginas.add(assinatura)

        total_pedidos_pagina = len(pedidos)
        for idx_pedido, p in enumerate(pedidos, start=1):
            p = _bling_required_dict(p, "listar pedidos de venda")
            # Verificar cancelamento para cada venda
            if client_id:
                _verificar_cancelamento(client_id)
                if idx_pedido == 1 or idx_pedido % 20 == 0 or idx_pedido == total_pedidos_pagina:
                    pct_pedido = min(35, pct_pagina + int((idx_pedido / max(total_pedidos_pagina, 1)) * 5))
                    _set_progresso(
                        client_id,
                        _criar_progresso(
                            "Bling",
                            pagina,
                            0,
                            pct_pedido,
                            f"Consultando Bling - página {pagina}, pedido {idx_pedido}/{total_pedidos_pagina}..."
                        )
                    )
            
            pid = p.get("id")
            if pid is None or not str(pid).strip():
                raise HTTPException(status_code=502, detail="Pedido sem identificador obrigatorio na resposta da Bling.")
            url_det = f"https://api.bling.com.br/Api/v3/pedidos/vendas/{pid}"
            det = _bling_get_with_adaptive_limit(
                url_det,
                headers=headers,
                timeout=25,
                limiter=limiter_detalhe,
                max_attempts=3,
                cancel_callback=_bling_cancel_callback(client_id),
            )
            if det is None:
                raise HTTPException(
                    status_code=503,
                    detail="Servico Bling indisponivel ao buscar detalhe obrigatorio de pedido.",
                )
            if det.status_code == 401:
                return None, 401
            if det.status_code != 200:
                _bling_raise_required_response(det, "buscar detalhe obrigatorio de pedido")
            venda = _bling_json_data(
                det,
                action="buscar detalhe obrigatorio de pedido",
                default={},
                expected_type=dict,
            )
            if not venda:
                raise HTTPException(status_code=502, detail="Detalhe obrigatorio de pedido ausente na resposta da Bling.")
            data_venda = venda.get("data") or p.get("data")
            numero = venda.get("numeroPedidoLoja") or str(venda.get("numero"))
            situacao_obj = venda.get("situacao") or {}
            situacao = situacao_obj.get("nome", "-") if isinstance(situacao_obj, dict) else "-"
            devolucao = 0
            loja_obj = venda.get("loja") or {}
            if not isinstance(loja_obj, dict):
                loja_obj = {}
            loja_virtual_nome = ""
            loja_virtual_id = str(loja_obj.get("id") or "").strip()
            unidade_negocio_obj = loja_obj.get("unidadeNegocio") or {}
            if not isinstance(unidade_negocio_obj, dict):
                unidade_negocio_obj = {}
            unidade_id = str(unidade_negocio_obj.get("id") or "").strip()
            # Prioridade: descrição oficial do canal de venda (igual ao nome no Bling)
            if loja_virtual_id and loja_virtual_id in mapa_canais:
                loja_virtual_nome = mapa_canais.get(loja_virtual_id) or ""
            elif loja_virtual_id and loja_virtual_id in mapa_lojas_virtuais:
                loja_virtual_nome = mapa_lojas_virtuais.get(loja_virtual_id) or ""

            canal = loja_virtual_nome or loja_obj.get("descricao") or "Balcão/Painel"
            
            # Inicializar variáveis
            numero_nf = ""
            comprador = ""
            loja_virtual = ""
            nota_fiscal_id = ""
            intermediador_nome = ""
            intermediador_cnpj = ""
            
            # Extrair loja virtual do pedido (via unidade de negócio)
            loja_obj = venda.get("loja")
            if loja_obj and isinstance(loja_obj, dict):
                unidade_negocio_obj = loja_obj.get("unidadeNegocio")
                if unidade_negocio_obj and isinstance(unidade_negocio_obj, dict):
                    unidade_id = unidade_negocio_obj.get("id")
                    if unidade_id:
                        loja_virtual = _bling_buscar_unidade_negocio(access_token, unidade_id, unidades_cache, unidades_mapeamento)

            loja_virtual_por_unidade = loja_virtual
            intermediador = venda.get("intermediador") or {}
            if isinstance(intermediador, dict):
                intermediador_nome = _normalizar_nome_loja_virtual_candidato(intermediador.get("nomeUsuario"))
                intermediador_cnpj = str(intermediador.get("cnpj") or "").strip()

            # Resolver nome de loja virtual com mapeamento do tenant + dados reais da venda.
            loja_virtual = _resolver_nome_loja_virtual(
                mapa_lojas_cliente,
                loja_id=loja_virtual_id,
                unidade_id=str(unidade_id or ""),
                nome_oficial=loja_virtual_nome or loja_virtual_por_unidade,
                intermediador_nome=intermediador_nome,
                intermediador_cnpj=intermediador_cnpj,
                canal=canal,
            )
            loja_virtual = _normalizar_unidade_negocio_ml(loja_virtual, canal, prefer_full=False)
            
            if venda.get("notaFiscal"):
                nf_obj = venda.get("notaFiscal", {})
                if isinstance(nf_obj, dict):
                    nota_fiscal_id = str(nf_obj.get("id") or "").strip()
                    # Importante: ID interno da nota NÃO é o número fiscal exibido no Bling.
                    # Salvar apenas o campo "numero" quando disponível.
                    numero_nf = str(nf_obj.get("numero") or "").strip()

            # Quando o pedido retorna apenas o ID da nota, resolve o número da NF-e.
            if nota_fiscal_id and not numero_nf:
                if nota_fiscal_id in cache_numero_nf:
                    numero_nf = cache_numero_nf[nota_fiscal_id]
                else:
                    numero_nf, status_nf = _bling_obter_numero_nf(access_token, nota_fiscal_id)
                    if status_nf == 401:
                        return None, 401
                    if status_nf != 200:
                        logger.warning(
                            "[Bling] Enriquecimento opcional do numero visivel da NF indisponivel (status=%s).",
                            status_nf,
                        )
                        numero_nf = ""
                    cache_numero_nf[nota_fiscal_id] = numero_nf

            # Debug controlado sem payloads, IDs fiscais ou identificadores de pedidos.
            if debug_suspeitos_logados < 12:
                suspeito_loja = not loja_virtual or str(loja_virtual).strip().lower() == "balcão/painel"
                suspeito_nf = not numero_nf
                if suspeito_loja or suspeito_nf:
                    logger.info(
                        "[SYNC-DEBUG] enriquecimento parcial de venda: loja_resolvida=%s nf_resolvida=%s",
                        not suspeito_loja,
                        not suspeito_nf,
                    )
                    debug_suspeitos_logados += 1
            
            # Tentar obter comprador do contato
            contato = venda.get("contato", {})
            if isinstance(contato, dict):
                comprador = contato.get("nome", "")
            
            venda_itens = _bling_required_items(venda, "buscar detalhe obrigatorio de pedido")
            for item in venda_itens:
                sku = item.get("codigo") or "N/D"
                produto = item.get("descricao") or "Produto s/ descrição"
                qtd = _bling_required_float(item.get("quantidade", 0), "detalhar item de pedido")
                valor = _bling_required_float(item.get("valor", 0), "detalhar item de pedido") * qtd
                registros.append({
                    "data": data_venda,
                    "loja_conta": loja_nome,
                    "canal": canal,
                    "id_pedido": str(pid or ""),
                    "numero": numero,
                    "situacao": situacao,
                    "devolucao": devolucao,
                    "sku": sku,
                    "produto": produto,
                    "quantidade": qtd,
                    "valor": valor,
                    "numero_nf": numero_nf,
                    "comprador": comprador,
                    "unidade_negocio": loja_virtual or "",
                    "nota_fiscal_id": nota_fiscal_id,
                    "loja_id": loja_virtual_id,
                    "unidade_id": str(unidade_id or ""),
                    "intermediador_nome": intermediador_nome,
                    "intermediador_cnpj": intermediador_cnpj,
                })
        pagina += 1

    return registros, 200


def _bling_listar_naturezas(access_token: str):
    url = "https://api.bling.com.br/Api/v3/naturezas-operacoes"
    headers = {"Authorization": f"Bearer {access_token}"}
    natureza_map = {}
    pagina = 1
    limiter = _BlingAdaptiveLimiter(start_interval=0.07)
    while True:
        resp = _bling_get_with_adaptive_limit(
            url,
            headers=headers,
            params={"pagina": pagina, "limite": 100},
            timeout=20,
            limiter=limiter,
            max_attempts=3,
        )
        if resp is None:
            raise HTTPException(status_code=503, detail="Servico Bling indisponivel ao listar naturezas de operacao.")
        if resp.status_code == 401:
            return None, 401
        if resp.status_code != 200:
            _bling_raise_required_response(resp, "listar naturezas de operacao")

        data = _bling_json_data(
            resp,
            action="listar naturezas de operacao",
            default=[],
            expected_type=list,
        )
        if not data:
            break

        for n in data:
            n = _bling_required_dict(n, "listar naturezas de operacao")
            nid = n.get("id")
            desc = n.get("descricao")
            if nid is not None and desc is not None:
                natureza_map[str(nid)] = desc
        pagina += 1

    return natureza_map, 200


def _normalizar_texto(texto: str):
    if not texto:
        return ""
    texto = str(texto).upper()
    nfkd = unicodedata.normalize('NFKD', texto)
    return "".join([c for c in nfkd if not unicodedata.combining(c)])


def _bling_obter_detalhes_nf(access_token: str, nf_id: str, cancel_callback=None):
    """Obtém os detalhes de uma NF específica incluindo itens"""
    url = f"https://api.bling.com.br/Api/v3/nfe/{nf_id}"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    resp = _bling_get_with_adaptive_limit(
        url,
        headers=headers,
        timeout=15,
        limiter=_BlingAdaptiveLimiter(start_interval=0.08),
        max_attempts=3,
        cancel_callback=cancel_callback,
    )
    if resp is None:
        return None, 503
    if resp.status_code == 401:
        return None, 401
    if resp.status_code == 403:
        return None, 403
    if resp.status_code == 429:
        raise HTTPException(
            status_code=429,
            detail="Limite de solicitacoes da Bling atingido ao buscar detalhe de nota fiscal.",
            headers=_bling_retry_headers(resp),
        )
    if resp.status_code in {500, 502, 503, 504}:
        return None, 503
    if resp.status_code != 200:
        return None, 502
    try:
        detalhe = _bling_json_data(
            resp,
            action="buscar detalhe de nota fiscal",
            default={},
            expected_type=dict,
        )
    except HTTPException:
        return None, 502
    return detalhe, 200


def _extrair_codigo_origem_nf(nf_obj: dict) -> str:
    """Extrai código de origem (ex.: 'N 002713' / 'V 60863') de payloads de NF."""
    if not isinstance(nf_obj, dict):
        return ""

    candidatos = [
        nf_obj.get("origem"),
        nf_obj.get("origemDocumento"),
        nf_obj.get("documentoOrigem"),
        nf_obj.get("referencia"),
        nf_obj.get("referenciaOrigem"),
    ]

    for cand in candidatos:
        if isinstance(cand, str):
            txt = cand.strip()
            if txt:
                return txt
        if isinstance(cand, dict):
            tipo = str(
                cand.get("tipo")
                or cand.get("sigla")
                or cand.get("tipoDocumento")
                or ""
            ).strip()
            numero = str(
                cand.get("numero")
                or cand.get("documento")
                or cand.get("codigo")
                or ""
            ).strip()
            if tipo and numero:
                return f"{tipo} {numero}".strip()
            if numero:
                return numero

    return ""


def _bling_listar_notas_entrada(
    access_token: str,
    data_inicio: str,
    data_fim: str,
    naturezas_map: dict,
    client_id: str = None,
    progress_callback: Callable[[str, int, int, int, str], Any] | None = None,
    log_callback: Callable[[str], Any] | None = None,
    progress_start: int = 15,
    progress_end: int = 44,
):
    janelas = _iterar_janelas_data_bling(data_inicio, data_fim, dias_por_janela=31)
    if len(janelas) > 1:
        registros_total: list[dict] = []
        itens_total: list[dict] = []
        total_janelas = len(janelas)
        for idx_janela, (inicio_janela, fim_janela) in enumerate(janelas, 1):
            if client_id:
                _verificar_cancelamento(client_id)
            sub_start = _percentual_intervalo(progress_start, progress_end, idx_janela - 1, total_janelas)
            sub_end = _percentual_intervalo(progress_start, progress_end, idx_janela, total_janelas)
            sub_end = max(sub_start, sub_end)
            mensagem_janela = (
                f"NF-e de entrada: janela {idx_janela}/{total_janelas} "
                f"({inicio_janela} a {fim_janela})"
            )
            _notificar_progresso_nf(
                progress_callback,
                "Notas Entrada",
                idx_janela,
                total_janelas,
                sub_start,
                mensagem_janela,
            )
            _notificar_log_nf(log_callback, f"[ESTOQUE][LANC] {mensagem_janela}")
            registros_janela, itens_janela, status_janela = _bling_listar_notas_entrada(
                access_token,
                inicio_janela,
                fim_janela,
                naturezas_map,
                client_id,
                progress_callback=progress_callback,
                log_callback=log_callback,
                progress_start=sub_start,
                progress_end=sub_end,
            )
            if status_janela != 200:
                return registros_janela, itens_janela, status_janela
            registros_total.extend(registros_janela or [])
            itens_total.extend(itens_janela or [])
            _notificar_log_nf(
                log_callback,
                (
                    f"[ESTOQUE][LANC] Entrada janela {idx_janela}/{total_janelas}: "
                    f"{len(registros_janela or [])} notas, {len(itens_janela or [])} itens"
                ),
            )
        return registros_total, itens_total, 200

    url = "https://api.bling.com.br/Api/v3/nfe"
    headers = {"Authorization": f"Bearer {access_token}"}
    registros = []
    itens = []
    pagina = 1
    limiter = _BlingAdaptiveLimiter(start_interval=0.08)
    mapa_lojas_cliente = {}
    if client_id:
        try:
            mapa_lojas_cliente = _carregar_mapeamento_lojas_virtuais_cliente(client_id)
        except Exception:
            mapa_lojas_cliente = {}
    
    while True:
        # Verificar cancelamento antes de cada requisição
        if client_id:
            _verificar_cancelamento(client_id)
        
        pct_pagina = min(progress_end, max(progress_start, progress_start + max(0, pagina - 1)))
        msg_pagina = f"NF-e de entrada: consultando pagina {pagina} ({data_inicio} a {data_fim})"
        _notificar_progresso_nf(progress_callback, "Notas Entrada", pagina, 0, pct_pagina, msg_pagina)
        if pagina == 1 or pagina % 5 == 0:
            _notificar_log_nf(log_callback, f"[ESTOQUE][LANC] {msg_pagina}")

        params = {
            "tipo": 0,
            "dataEmissaoInicial": f"{data_inicio} 00:00:00",
            "dataEmissaoFinal": f"{data_fim} 23:59:59",
            "pagina": pagina,
            "limite": 100
        }
        resp = _bling_get_with_adaptive_limit(
            url,
            headers=headers,
            params=params,
            timeout=25,
            limiter=limiter,
            max_attempts=3,
            cancel_callback=_bling_cancel_callback(client_id),
        )
        if resp is None:
            raise HTTPException(status_code=503, detail="Servico Bling indisponivel ao listar NFe de entrada.")
        if resp.status_code == 401:
            return None, None, 401
        if resp.status_code != 200:
            _bling_raise_required_response(resp, "listar NFe de entrada")

        notas = _bling_json_data(
            resp,
            action="listar NFe de entrada",
            default=[],
            expected_type=list,
        )
        if not notas:
            break

        total_notas_pagina = len(notas)
        pct_detalhe = min(progress_end, max(progress_start, pct_pagina + 1))
        _notificar_progresso_nf(
            progress_callback,
            "Notas Entrada",
            pagina,
            0,
            pct_detalhe,
            f"NF-e de entrada: pagina {pagina} retornou {total_notas_pagina} notas; detalhando itens...",
        )

        for idx_nf, nf in enumerate(notas, 1):
            nf = _bling_required_dict(nf, "listar NFe de entrada")
            # Verificar cancelamento para cada nota
            if client_id:
                _verificar_cancelamento(client_id)

            if idx_nf == 1 or idx_nf % 25 == 0 or idx_nf == total_notas_pagina:
                _notificar_progresso_nf(
                    progress_callback,
                    "Notas Entrada",
                    idx_nf,
                    total_notas_pagina,
                    pct_detalhe,
                    (
                        f"NF-e de entrada: detalhando nota {idx_nf}/{total_notas_pagina} "
                        f"da pagina {pagina} ({data_inicio} a {data_fim})"
                    ),
                )
            
            nid = nf.get("id")
            if nid is None or not str(nid).strip():
                raise HTTPException(
                    status_code=502,
                    detail="NFe de entrada sem identificador para buscar detalhe obrigatorio.",
                )
            numero = nf.get("numero")
            data_emissao = nf.get("dataEmissao") or nf.get("data")
            valor = nf.get("valorTotal") or nf.get("total") or 0
            contato = nf.get("contato") or {}
            fornecedor = contato.get("nome") if isinstance(contato, dict) else None

            natureza = nf.get("naturezaOperacao") or {}
            if not isinstance(natureza, dict):
                natureza = {}
            natureza_id = natureza.get("id")
            natureza_desc = natureza.get("descricao")
            if not natureza_desc and natureza_id is not None:
                natureza_desc = naturezas_map.get(str(natureza_id))

            finalidade_desc = ""
            finalidade_obj = nf.get("finalidade") or nf.get("finalidadeOperacao") or {}
            if isinstance(finalidade_obj, dict):
                finalidade_desc = str(
                    finalidade_obj.get("descricao")
                    or finalidade_obj.get("nome")
                    or finalidade_obj.get("label")
                    or finalidade_obj.get("valor")
                    or ""
                ).strip()
            elif finalidade_obj:
                finalidade_desc = str(finalidade_obj).strip()

            loja_nf = nf.get("loja") or {}
            unidade_nf = ""
            loja_desc_nf = ""
            loja_id_nf = ""
            unidade_id_nf = ""
            if isinstance(loja_nf, dict):
                loja_id_nf = str(loja_nf.get("id") or "").strip()
                unidade_obj = loja_nf.get("unidadeNegocio") or {}
                if isinstance(unidade_obj, dict):
                    unidade_id_nf = str(unidade_obj.get("id") or "").strip()
                    unidade_nf = str(unidade_obj.get("nome") or unidade_obj.get("descricao") or "").strip()
                loja_desc_nf = str(loja_nf.get("descricao") or "").strip()
                if not unidade_nf:
                    unidade_nf = loja_desc_nf
            intermediador_nf = nf.get("intermediador") or {}
            intermediador_nome_nf = ""
            intermediador_cnpj_nf = ""
            if isinstance(intermediador_nf, dict):
                intermediador_nome_nf = _normalizar_nome_loja_virtual_candidato(intermediador_nf.get("nomeUsuario"))
                intermediador_cnpj_nf = str(intermediador_nf.get("cnpj") or "").strip()
            unidade_resolvida_nf = _resolver_nome_loja_virtual(
                mapa_lojas_cliente,
                loja_id=loja_id_nf,
                unidade_id=unidade_id_nf,
                nome_oficial=unidade_nf or loja_desc_nf,
                intermediador_nome=intermediador_nome_nf,
                intermediador_cnpj=intermediador_cnpj_nf,
                canal=loja_desc_nf,
            )

            devolucao = _eh_devolucao_nota_entrada(natureza_desc, unidade_nf, loja_desc_nf, finalidade_desc)
            origem_codigo = _extrair_codigo_origem_nf(nf)
            unidade_virtual = _normalizar_unidade_devolucao_entrada(unidade_resolvida_nf or unidade_nf, natureza_desc, loja_desc_nf)

            registros.append({
                "id": nid,
                "numero": numero,
                "data_emissao": data_emissao,
                "natureza_operacao": natureza_desc,
                "finalidade_operacao": finalidade_desc,
                "devolucao": devolucao,
                "valor": valor,
                "fornecedor": fornecedor,
                "origem_codigo": origem_codigo,
                "unidade_negocio": unidade_nf,
                "unidade_negocio_virtual": unidade_virtual,
            })

            # A listagem pode trazer apenas uma amostra de itens. O detalhe e
            # obrigatorio para toda NF-e de entrada, mesmo quando ha itens ali.
            nota_itens: list[dict] = []
            if nid:
                detalhe_nf, status_det = _bling_obter_detalhes_nf(
                    access_token,
                    str(nid),
                    cancel_callback=_bling_cancel_callback(client_id),
                )
                if status_det == 401:
                    return None, None, 401
                if status_det == 429:
                    raise HTTPException(status_code=429, detail="Limite da Bling ao buscar detalhe obrigatorio de NFe de entrada.")
                if status_det == 403:
                    raise HTTPException(status_code=403, detail="Permissao insuficiente para detalhar NFe de entrada na Bling.")
                if status_det == 503:
                    raise HTTPException(status_code=503, detail="Servico Bling indisponivel ao detalhar NFe de entrada.")
                if status_det != 200 or not isinstance(detalhe_nf, dict):
                    raise HTTPException(status_code=502, detail="Detalhe obrigatorio de NFe de entrada ausente ou invalido.")
                if status_det == 200:
                    nota_itens = _bling_required_items(
                        detalhe_nf, "detalhar NFe de entrada"
                    )

                    data_emissao = detalhe_nf.get("dataEmissao") or detalhe_nf.get("data") or data_emissao
                    valor = detalhe_nf.get("valorTotal") or detalhe_nf.get("total") or valor
                    contato_det = detalhe_nf.get("contato") or {}
                    if isinstance(contato_det, dict):
                        fornecedor = contato_det.get("nome") or fornecedor

                    natureza_det = detalhe_nf.get("naturezaOperacao") or {}
                    if isinstance(natureza_det, dict):
                        natureza_id_det = natureza_det.get("id")
                        natureza_desc = (
                            natureza_det.get("descricao")
                            or natureza_det.get("nome")
                            or (naturezas_map.get(str(natureza_id_det)) if natureza_id_det is not None else "")
                            or natureza_desc
                        )

                    finalidade_det = detalhe_nf.get("finalidade") or detalhe_nf.get("finalidadeOperacao") or {}
                    if isinstance(finalidade_det, dict):
                        finalidade_desc = str(
                            finalidade_det.get("descricao")
                            or finalidade_det.get("nome")
                            or finalidade_det.get("label")
                            or finalidade_det.get("valor")
                            or finalidade_desc
                            or ""
                        ).strip()
                    elif finalidade_det:
                        finalidade_desc = str(finalidade_det).strip()

                    loja_det = detalhe_nf.get("loja") or {}
                    if isinstance(loja_det, dict):
                        loja_id_det = str(loja_det.get("id") or "").strip()
                        unidade_obj_det = loja_det.get("unidadeNegocio") or {}
                        unidade_id_det = ""
                        unidade_det = ""
                        if isinstance(unidade_obj_det, dict):
                            unidade_id_det = str(unidade_obj_det.get("id") or "").strip()
                            unidade_det = str(unidade_obj_det.get("nome") or unidade_obj_det.get("descricao") or "").strip()
                        loja_desc_det = str(loja_det.get("descricao") or "").strip()
                        if loja_desc_det:
                            loja_desc_nf = loja_desc_det
                        if loja_id_det:
                            loja_id_nf = loja_id_det
                        if unidade_id_det:
                            unidade_id_nf = unidade_id_det
                        if unidade_det:
                            unidade_nf = unidade_det
                        elif loja_desc_det and not unidade_nf:
                            unidade_nf = loja_desc_det

                    intermediador_det = detalhe_nf.get("intermediador") or {}
                    if isinstance(intermediador_det, dict):
                        intermediador_nome_nf = _normalizar_nome_loja_virtual_candidato(intermediador_det.get("nomeUsuario")) or intermediador_nome_nf
                        intermediador_cnpj_nf = str(intermediador_det.get("cnpj") or "").strip() or intermediador_cnpj_nf

                    unidade_resolvida_nf = _resolver_nome_loja_virtual(
                        mapa_lojas_cliente,
                        loja_id=loja_id_nf,
                        unidade_id=unidade_id_nf,
                        nome_oficial=unidade_nf or loja_desc_nf,
                        intermediador_nome=intermediador_nome_nf,
                        intermediador_cnpj=intermediador_cnpj_nf,
                        canal=loja_desc_nf,
                    )
                    devolucao = _eh_devolucao_nota_entrada(natureza_desc, unidade_nf, loja_desc_nf, finalidade_desc)
                    origem_codigo = _extrair_codigo_origem_nf(detalhe_nf) or origem_codigo
                    unidade_virtual = _normalizar_unidade_devolucao_entrada(unidade_resolvida_nf or unidade_nf, natureza_desc, loja_desc_nf)

                    if registros:
                        registros[-1].update({
                            "data_emissao": data_emissao,
                            "natureza_operacao": natureza_desc,
                            "finalidade_operacao": finalidade_desc,
                            "devolucao": devolucao,
                            "valor": valor,
                            "fornecedor": fornecedor,
                            "origem_codigo": origem_codigo,
                            "unidade_negocio": unidade_nf,
                            "unidade_negocio_virtual": unidade_virtual,
                            "_detalhe_validado": True,
                        })
            devolucao_cfop = _eh_devolucao_por_cfop_itens(nota_itens)
            if devolucao_cfop:
                devolucao = 1
            tipo_especial = _tipo_devolucao_cfop_full_estoque(natureza_desc, finalidade_desc, nota_itens)
            if tipo_especial:
                unidade_virtual = tipo_especial
            if registros:
                registros[-1]["devolucao"] = devolucao
                registros[-1]["unidade_negocio_virtual"] = unidade_virtual
            for item in nota_itens:
                item = _bling_required_dict(item, "detalhar item de NFe de entrada")
                # SKU vem diretamente no campo "codigo" do item
                sku = item.get("codigo") or item.get("sku") or item.get("id")
                descricao = item.get("descricao") or item.get("nome")
                
                quantidade = _bling_required_float(
                    item.get("quantidade"), "detalhar item de NFe de entrada"
                )
                valor_unitario = _bling_required_float(
                    item.get("valor"), "detalhar item de NFe de entrada"
                )

                itens.append({
                    "id_nota": nid,
                    "numero_nota": numero,
                    "origem_codigo": origem_codigo,
                    "data_emissao": data_emissao,
                    "sku": sku,
                    "descricao": descricao,
                    "quantidade": quantidade,
                    "valor_unitario": valor_unitario,
                    "valor_total": quantidade * valor_unitario if quantidade and valor_unitario else 0,
                    "natureza_operacao": natureza_desc,
                    "finalidade_operacao": finalidade_desc,
                    "devolucao": devolucao,
                    "fornecedor": fornecedor
                    ,"unidade_negocio": unidade_nf,
                    "unidade_negocio_virtual": unidade_virtual
                })

        _notificar_log_nf(
            log_callback,
            (
                f"[ESTOQUE][LANC] Entrada {data_inicio} a {data_fim}, pagina {pagina}: "
                f"{len(notas)} notas, {len(itens)} itens acumulados"
            ),
        )
        pagina += 1

    return registros, itens, 200


def _bling_listar_vendas_fallback_nf_saida(
    access_token: str,
    data_inicio: str,
    data_fim: str,
    loja_nome: str,
    client_id: str = None,
    mapa_lojas_cliente: dict = None,
    progress_callback: Callable[[str, int, int, int, str], Any] | None = None,
    log_callback: Callable[[str], Any] | None = None,
    progress_start: int = 45,
    progress_end: int = 74,
):
    janelas = _iterar_janelas_data_bling(data_inicio, data_fim, dias_por_janela=31)
    if len(janelas) > 1:
        registros_total: list[dict] = []
        total_janelas = len(janelas)
        for idx_janela, (inicio_janela, fim_janela) in enumerate(janelas, 1):
            if client_id:
                _verificar_cancelamento(client_id)
            sub_start = _percentual_intervalo(progress_start, progress_end, idx_janela - 1, total_janelas)
            sub_end = _percentual_intervalo(progress_start, progress_end, idx_janela, total_janelas)
            sub_end = max(sub_start, sub_end)
            mensagem_janela = (
                f"NF-e de saida: janela {idx_janela}/{total_janelas} "
                f"({inicio_janela} a {fim_janela})"
            )
            _notificar_progresso_nf(
                progress_callback,
                "Notas Saida",
                idx_janela,
                total_janelas,
                sub_start,
                mensagem_janela,
            )
            _notificar_log_nf(log_callback, f"[ESTOQUE][LANC] {mensagem_janela}")
            registros_janela, status_janela = _bling_listar_vendas_fallback_nf_saida(
                access_token,
                inicio_janela,
                fim_janela,
                loja_nome,
                client_id,
                mapa_lojas_cliente,
                progress_callback=progress_callback,
                log_callback=log_callback,
                progress_start=sub_start,
                progress_end=sub_end,
            )
            if status_janela != 200:
                return registros_janela, status_janela
            registros_total.extend(registros_janela or [])
            _notificar_log_nf(
                log_callback,
                (
                    f"[ESTOQUE][LANC] Saida janela {idx_janela}/{total_janelas}: "
                    f"{len(registros_janela or [])} itens"
                ),
            )
        return registros_total, 200

    """Fallback: monta vendas a partir de NF-e de saída (tipo=1), útil para operações Fulfillment."""
    url = "https://api.bling.com.br/Api/v3/nfe"
    headers = {"Authorization": f"Bearer {access_token}"}
    registros = []
    pagina = 1
    max_paginas = 400
    limiter = _BlingAdaptiveLimiter(start_interval=0.08)
    mapa_lojas_cliente = mapa_lojas_cliente or {}
    if not mapa_lojas_cliente and client_id:
        try:
            mapa_lojas_cliente = _carregar_mapeamento_lojas_virtuais_cliente(client_id)
        except Exception:
            mapa_lojas_cliente = {}

    while True:
        if client_id:
            _verificar_cancelamento(client_id)

        # Guardrail para evitar loop infinito em paginação inconsistente da API.
        if pagina > max_paginas:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Listagem de NF-e de saída excedeu {max_paginas} páginas. "
                    "Refine o período da sincronização para continuar."
                ),
            )

        pct_pagina = min(progress_end, max(progress_start, progress_start + max(0, pagina - 1)))
        msg_pagina = f"NF-e de saida: consultando pagina {pagina} ({data_inicio} a {data_fim})"
        _notificar_progresso_nf(progress_callback, "Notas Saida", pagina, 0, pct_pagina, msg_pagina)
        if pagina == 1 or pagina % 5 == 0:
            _notificar_log_nf(log_callback, f"[ESTOQUE][LANC] {msg_pagina}")

        params = {
            "tipo": 1,
            "dataEmissaoInicial": f"{data_inicio} 00:00:00",
            "dataEmissaoFinal": f"{data_fim} 23:59:59",
            "pagina": pagina,
            "limite": 100,
        }

        resp = _bling_get_with_adaptive_limit(
            url,
            headers=headers,
            params=params,
            timeout=25,
            limiter=limiter,
            max_attempts=3,
            cancel_callback=_bling_cancel_callback(client_id),
        )
        if resp is None:
            raise HTTPException(status_code=503, detail="Servico Bling indisponivel ao listar NFe de saida.")
        if resp.status_code == 401:
            return None, 401
        if resp.status_code == 403:
            _notificar_log_nf(
                log_callback,
                "[ESTOQUE][LANC] Fallback de NFe de saida indisponivel por permissao 403; nenhuma venda sera inferida por esta fonte.",
            )
            return [], 403
        if resp.status_code != 200:
            _bling_raise_required_response(resp, "listar NFe de saida")

        notas = _bling_json_data(
            resp,
            action="listar NFe de saida",
            default=[],
            expected_type=list,
        )
        if not notas:
            break

        total_notas_pagina = len(notas)
        pct_detalhe = min(progress_end, max(progress_start, pct_pagina + 1))
        _notificar_progresso_nf(
            progress_callback,
            "Notas Saida",
            pagina,
            0,
            pct_detalhe,
            f"NF-e de saida: pagina {pagina} retornou {total_notas_pagina} notas; detalhando itens...",
        )

        for idx_nf, nf in enumerate(notas, 1):
            nf = _bling_required_dict(nf, "listar NFe de saida")
            if client_id:
                _verificar_cancelamento(client_id)

            if idx_nf == 1 or idx_nf % 25 == 0 or idx_nf == total_notas_pagina:
                _notificar_progresso_nf(
                    progress_callback,
                    "Notas Saida",
                    idx_nf,
                    total_notas_pagina,
                    pct_detalhe,
                    (
                        f"NF-e de saida: detalhando nota {idx_nf}/{total_notas_pagina} "
                        f"da pagina {pagina} ({data_inicio} a {data_fim})"
                    ),
                )

            nf_id = str(nf.get("id") or "").strip()
            if not nf_id:
                raise HTTPException(status_code=502, detail="NFe de saida sem identificador para buscar detalhe obrigatorio.")
            numero_nf = str(nf.get("numero") or "").strip()
            data_venda = nf.get("dataEmissao") or nf.get("data") or ""
            contato = nf.get("contato") or {}
            comprador = contato.get("nome") if isinstance(contato, dict) else ""

            # Tenta recuperar número do pedido de origem (ex.: "V 60863").
            origem_codigo = _extrair_codigo_origem_nf(nf)
            m_origem = re.search(r"(\d+)$", str(origem_codigo or ""))
            numero_pedido = str(nf.get("numeroPedidoLoja") or "").strip() or (m_origem.group(1) if m_origem else "")
            numero = numero_pedido or numero_nf or nf_id

            loja_nf = nf.get("loja") or {}
            loja_desc = ""
            unidade_id = ""
            loja_id = ""
            if isinstance(loja_nf, dict):
                loja_desc = str(loja_nf.get("descricao") or "").strip()
                loja_id = str(loja_nf.get("id") or "").strip()
                un = loja_nf.get("unidadeNegocio") or {}
                if isinstance(un, dict):
                    unidade_id = str(un.get("id") or "").strip()

            canal = loja_desc or "Mercado Livre Full"
            unidade_negocio = _normalizar_unidade_negocio_ml(loja_desc or canal, canal, prefer_full=True)

            detalhe, status_det = _bling_obter_detalhes_nf(
                access_token,
                nf_id,
                cancel_callback=_bling_cancel_callback(client_id),
            )
            if status_det == 401:
                return None, 401
            if status_det == 403:
                _notificar_log_nf(
                    log_callback,
                    "[ESTOQUE][LANC] Fallback de NFe de saida interrompido: permissao 403 ao detalhar nota.",
                )
                return [], 403
            if status_det == 429:
                raise HTTPException(status_code=429, detail="Limite da Bling ao buscar detalhe obrigatorio de NFe de saida.")
            if status_det == 503:
                raise HTTPException(status_code=503, detail="Servico Bling indisponivel ao detalhar NFe de saida.")
            if status_det != 200 or not detalhe:
                raise HTTPException(status_code=502, detail="Detalhe obrigatorio de NFe de saida ausente ou invalido.")

            # Alguns payloads de listagem de NF não trazem metadados completos de loja/unidade.
            # Prioriza os dados do detalhe para preservar corretamente a loja virtual.
            loja_det = detalhe.get("loja") or {}
            if isinstance(loja_det, dict):
                loja_desc_det = str(loja_det.get("descricao") or "").strip()
                loja_id_det = str(loja_det.get("id") or "").strip()
                unidade_det = loja_det.get("unidadeNegocio") or {}
                unidade_id_det = ""
                if isinstance(unidade_det, dict):
                    unidade_id_det = str(unidade_det.get("id") or "").strip()

                if loja_desc_det:
                    loja_desc = loja_desc_det
                if loja_id_det:
                    loja_id = loja_id_det
                if unidade_id_det:
                    unidade_id = unidade_id_det

            intermediador_nome = ""
            intermediador_cnpj = ""
            intermediador = detalhe.get("intermediador") or {}
            if isinstance(intermediador, dict):
                intermediador_nome = _normalizar_nome_loja_virtual_candidato(intermediador.get("nomeUsuario"))
                intermediador_cnpj = str(intermediador.get("cnpj") or "").strip()

            canal = loja_desc or canal or "Mercado Livre Full"
            nome_oficial_resolver = loja_desc
            loja_id_resolver = loja_id
            unidade_id_resolver = unidade_id

            # Para vendas Full/Fulfillment, prioriza o nome oficial da loja virtual
            # retornado pela Bling em vez do mapeamento genérico por loja_id.
            nome_oficial_norm = _normalizar_texto(nome_oficial_resolver)
            if "FULL" in nome_oficial_norm or "FULFILL" in nome_oficial_norm:
                loja_id_resolver = ""
                unidade_id_resolver = ""

            unidade_resolvida = _resolver_nome_loja_virtual(
                mapa_lojas_cliente,
                loja_id=loja_id_resolver,
                unidade_id=unidade_id_resolver,
                nome_oficial=nome_oficial_resolver,
                intermediador_nome=intermediador_nome,
                intermediador_cnpj=intermediador_cnpj,
                canal=canal,
            )
            unidade_negocio = _normalizar_unidade_negocio_ml(unidade_resolvida or loja_desc or canal, canal, prefer_full=True)

            itens = _bling_required_items(detalhe, "detalhar NFe de saida")
            for item in itens:
                item = _bling_required_dict(item, "detalhar item de NFe de saida")
                sku = item.get("codigo") or item.get("sku") or "N/D"
                produto = item.get("descricao") or item.get("nome") or "Produto s/ descrição"
                qtd = _bling_required_float(
                    item.get("quantidade", 0), "detalhar item de NFe de saida"
                )
                valor = _bling_required_float(
                    item.get("valor", 0), "detalhar item de NFe de saida"
                ) * qtd
                registros.append({
                    "data": data_venda,
                    "loja_conta": loja_nome,
                    "canal": canal,
                    "numero": numero,
                    "situacao": "Faturado",
                    "devolucao": 0,
                    "sku": sku,
                    "produto": produto,
                    "quantidade": qtd,
                    "valor": valor,
                    "numero_nf": numero_nf,
                    "comprador": comprador,
                    "unidade_negocio": unidade_negocio,
                    "nota_fiscal_id": nf_id,
                    "loja_id": loja_id,
                    "unidade_id": unidade_id,
                    "intermediador_nome": intermediador_nome,
                    "intermediador_cnpj": intermediador_cnpj,
                })

        _notificar_log_nf(
            log_callback,
            (
                f"[ESTOQUE][LANC] Saida {data_inicio} a {data_fim}, pagina {pagina}: "
                f"{len(notas)} notas, {len(registros)} itens acumulados"
            ),
        )
        pagina += 1

    return registros, 200


def _get_notas_entrada_db_unlocked(client_id: str, loja: str = None):
    db_path = _get_vendas_db(client_id, loja)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS notas_entrada (
                id_unico TEXT PRIMARY KEY,
                id_bling TEXT,
                numero TEXT,
                data_emissao TEXT,
                natureza_operacao TEXT,
                finalidade_operacao TEXT,
                devolucao INTEGER DEFAULT 0,
                valor REAL,
                fornecedor TEXT,
                origem_codigo TEXT,
                loja_conta TEXT,
                unidade_negocio TEXT,
                unidade_negocio_virtual TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS notas_entrada_itens (
                id_unico TEXT PRIMARY KEY,
                id_nota TEXT,
                numero_nota TEXT,
                data_emissao TEXT,
                sku TEXT,
                descricao TEXT,
                quantidade REAL,
                valor_unitario REAL,
                valor_total REAL,
                natureza_operacao TEXT,
                finalidade_operacao TEXT,
                devolucao INTEGER DEFAULT 0,
                fornecedor TEXT,
                origem_codigo TEXT,
                loja_conta TEXT,
                unidade_negocio TEXT,
                unidade_negocio_virtual TEXT
            )
            """
        )
        cols_notas = [row[1] for row in cur.execute("PRAGMA table_info(notas_entrada)").fetchall()]
        if "origem_codigo" not in cols_notas:
            cur.execute("ALTER TABLE notas_entrada ADD COLUMN origem_codigo TEXT")
        if "finalidade_operacao" not in cols_notas:
            cur.execute("ALTER TABLE notas_entrada ADD COLUMN finalidade_operacao TEXT")
        if "loja_conta" not in cols_notas:
            cur.execute("ALTER TABLE notas_entrada ADD COLUMN loja_conta TEXT")
        if "unidade_negocio" not in cols_notas:
            cur.execute("ALTER TABLE notas_entrada ADD COLUMN unidade_negocio TEXT")
        if "unidade_negocio_virtual" not in cols_notas:
            cur.execute("ALTER TABLE notas_entrada ADD COLUMN unidade_negocio_virtual TEXT")

        cols_itens = [row[1] for row in cur.execute("PRAGMA table_info(notas_entrada_itens)").fetchall()]
        if "origem_codigo" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN origem_codigo TEXT")
        if "finalidade_operacao" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN finalidade_operacao TEXT")
        if "loja_conta" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN loja_conta TEXT")
        if "unidade_negocio" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN unidade_negocio TEXT")
        if "unidade_negocio_virtual" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN unidade_negocio_virtual TEXT")

        # Backfill da loja em bases antigas: id_unico sempre prefixado com "{loja}_...".
        cur.execute(
            """
            UPDATE notas_entrada
            SET loja_conta = substr(id_unico, 1, instr(id_unico, '_') - 1)
            WHERE (loja_conta IS NULL OR trim(loja_conta) = '')
              AND instr(id_unico, '_') > 1
            """
        )
        cur.execute(
            """
            UPDATE notas_entrada_itens
            SET loja_conta = substr(id_unico, 1, instr(id_unico, '_') - 1)
            WHERE (loja_conta IS NULL OR trim(loja_conta) = '')
              AND instr(id_unico, '_') > 1
            """
        )
        cur.execute(
            """
            UPDATE notas_entrada_itens
            SET loja_conta = (
                SELECT n.loja_conta
                FROM notas_entrada n
                WHERE CAST(n.id_bling AS TEXT) = CAST(notas_entrada_itens.id_nota AS TEXT)
                  AND n.loja_conta IS NOT NULL
                  AND trim(n.loja_conta) != ''
                LIMIT 1
            )
            WHERE (loja_conta IS NULL OR trim(loja_conta) = '')
            """
        )

        # Migração: remover prefixo "Unidade " dos campos legados.
        cur.execute(
            """
            UPDATE notas_entrada
            SET unidade_negocio = ltrim(substr(unidade_negocio, 9))
            WHERE unidade_negocio IS NOT NULL
              AND trim(unidade_negocio) != ''
              AND lower(unidade_negocio) LIKE 'unidade %'
            """
        )
        cur.execute(
            """
            UPDATE notas_entrada
            SET unidade_negocio_virtual = ltrim(substr(unidade_negocio_virtual, 9))
            WHERE unidade_negocio_virtual IS NOT NULL
              AND trim(unidade_negocio_virtual) != ''
              AND lower(unidade_negocio_virtual) LIKE 'unidade %'
            """
        )
        cur.execute(
            """
            UPDATE notas_entrada_itens
            SET unidade_negocio = ltrim(substr(unidade_negocio, 9))
            WHERE unidade_negocio IS NOT NULL
              AND trim(unidade_negocio) != ''
              AND lower(unidade_negocio) LIKE 'unidade %'
            """
        )
        cur.execute(
            """
            UPDATE notas_entrada_itens
            SET unidade_negocio_virtual = ltrim(substr(unidade_negocio_virtual, 9))
            WHERE unidade_negocio_virtual IS NOT NULL
              AND trim(unidade_negocio_virtual) != ''
              AND lower(unidade_negocio_virtual) LIKE 'unidade %'
            """
        )

        # Backfill: devolução deve seguir natureza/finalidade fiscal.
        cur.execute(
            """
            UPDATE notas_entrada
            SET devolucao = 1
            WHERE coalesce(devolucao, 0) = 0
              AND (
                    lower(coalesce(natureza_operacao, '')) LIKE '%devolu%mercador%'
                    OR lower(coalesce(finalidade_operacao, '')) LIKE '%devolu%mercador%'
              )
            """
        )

        cur.execute(
            """
            UPDATE notas_entrada_itens
            SET devolucao = 1
            WHERE coalesce(devolucao, 0) = 0
              AND (
                    lower(coalesce(natureza_operacao, '')) LIKE '%devolu%mercador%'
                    OR lower(coalesce(finalidade_operacao, '')) LIKE '%devolu%mercador%'
              )
            """
        )

        # Backfill do tipo para padrão CFOP de devolução sem finalidade descritiva (caso 088344).
        cur.execute(
            """
            UPDATE notas_entrada
            SET unidade_negocio_virtual = 'Devolução Full Estoque'
            WHERE coalesce(devolucao, 0) = 1
                AND lower(coalesce(natureza_operacao, '')) LIKE '%compra%'
                AND lower(coalesce(natureza_operacao, '')) LIKE '%comercializ%'
                AND coalesce(trim(finalidade_operacao), '') = ''
                AND (
                    unidade_negocio_virtual IS NULL
                    OR trim(unidade_negocio_virtual) = ''
                    OR lower(trim(unidade_negocio_virtual)) = 'mercado livre - full'
                    OR lower(trim(unidade_negocio_virtual)) = 'mercado livre full'
                )
            """
        )
        cur.execute(
            """
            UPDATE notas_entrada_itens
            SET unidade_negocio_virtual = 'Devolução Full Estoque'
            WHERE coalesce(devolucao, 0) = 1
                AND lower(coalesce(natureza_operacao, '')) LIKE '%compra%'
                AND lower(coalesce(natureza_operacao, '')) LIKE '%comercializ%'
                AND coalesce(trim(finalidade_operacao), '') = ''
                AND (
                    unidade_negocio_virtual IS NULL
                    OR trim(unidade_negocio_virtual) = ''
                    OR lower(trim(unidade_negocio_virtual)) = 'mercado livre - full'
                    OR lower(trim(unidade_negocio_virtual)) = 'mercado livre full'
                )
            """
        )

        # Regra de negócio: devoluções da loja Imports ficam vinculadas à loja virtual Carlos Jose.
        cur.execute(
            """
            UPDATE notas_entrada
            SET unidade_negocio_virtual = 'Carlos Jose'
            WHERE coalesce(devolucao, 0) = 1
                AND lower(coalesce(loja_conta, '')) LIKE '%imports%'
            """
        )
        cur.execute(
            """
            UPDATE notas_entrada_itens
            SET unidade_negocio_virtual = 'Carlos Jose'
            WHERE coalesce(devolucao, 0) = 1
                AND lower(coalesce(loja_conta, '')) LIKE '%imports%'
            """
        )

        conn.commit()
    finally:
        conn.close()
    return db_path


def _get_notas_entrada_db(client_id: str, loja: str = None):
    db_path = _get_vendas_db_path(client_id, loja)
    with sqlite_lock_for_path(db_path):
        return _get_notas_entrada_db_unlocked(client_id, loja)

__all__ = [
    "configure_bling_vendas_context",
    "_bling_refresh_token",
    "_bling_marcar_oauth_invalido",
    "_bling_salvar_oauth_valido",
    "_bling_renovar_token_loja",
    "_bling_executar_com_refresh",
    "_bling_listar_produtos",
    "_bling_obter_ncm_cest_produto",
    "_carregar_mapeamento_unidades",
    "_salvar_mapeamento_unidades",
    "_bling_buscar_unidade_negocio",
    "_bling_map_canais_venda_basico",
    "_bling_map_unidades_por_canais",
    "_bling_map_lojas_virtuais",
    "_normalizar_nome_loja_virtual_candidato",
    "_remover_prefixo_unidade_nome",
    "_normalizar_cnpj",
    "_carregar_mapeamento_lojas_virtuais_cliente",
    "_resolver_nome_loja_virtual",
    "_normalizar_unidade_negocio_ml",
    "_normalizar_unidade_devolucao_entrada",
    "_eh_unidade_sintetica_sistema",
    "_eh_devolucao_nota_entrada",
    "_eh_devolucao_por_cfop_itens",
    "_tipo_devolucao_cfop_full_estoque",
    "_devolucao_deve_ir_para_ml_full",
    "_classificar_unidade_virtual_devolucao",
    "_sql_filtro_unidade_devolucao",
    "_sql_filtro_loja_notas_entrada",
    "_bling_obter_numero_nf",
    "_bling_map_depositos",
    "_bling_saldos",
    "_variacoes_nome_loja",
    "_variacoes_nome_loja_nocase",
    "_sql_match_variacoes",
    "_sql_filtro_unidade_com_mapa",
    "_sql_filtro_unidade_vendas",
    "_sql_filtro_loja_vendas",
    "_slug_loja_para_arquivo",
    "_get_vendas_db_path",
    "_listar_bancos_vendas_tenant",
    "_chave_deduplicacao_venda",
    "_deduplicar_vendas_consolidadas",
    "_deve_excluir_venda_ebazar",
    "_get_vendas_db",
    "_bling_listar_vendas",
    "_bling_listar_naturezas",
    "_normalizar_texto",
    "_bling_obter_detalhes_nf",
    "_extrair_codigo_origem_nf",
    "_bling_listar_notas_entrada",
    "_bling_listar_vendas_fallback_nf_saida",
    "_get_notas_entrada_db",
]
