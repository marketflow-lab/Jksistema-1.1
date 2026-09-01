"""Storage and OAuth helpers for the Integracoes module."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from typing import Callable
from datetime import datetime, timezone
from urllib.parse import quote, quote_plus, urlencode

import requests
from fastapi import HTTPException

from backend.services.bling_oauth import exchange_bling_refresh_token


logger = logging.getLogger("jk_sistema")
PASTA_INFO = ""
ARQUIVO_LOJAS = ""
ARQUIVO_TEMP_AUTH = ""
_get_tenant_path: Callable[[str], str] | None = None
_normalizar_integracao_conectada: Callable[[str, object], object] = lambda servico, dados: dados
_resolver_redirect_uri_publica: Callable[..., str] = lambda **kwargs: ""
_resolver_redirect_uri_bling: Callable[..., str] = lambda **kwargs: ""
_bling_session = requests.Session()
_LOJAS_CONFIG_LOCK = threading.RLock()
_TEMP_AUTH_LOCK = threading.RLock()
_TEMP_AUTH_TTL_SECONDS = 15 * 60
_BLING_REFRESH_LOCKS_GUARD = threading.Lock()
_BLING_REFRESH_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_INTEGRACOES_SYNC_TRANSIENT_KEYS = {"oauth_draft", "oauth_pending_state"}


def configure_integracoes_context(
    *,
    logger_ref=None,
    pasta_info: str,
    get_tenant_path: Callable[[str], str],
    normalizar_integracao_conectada: Callable[[str, object], object] | None = None,
    resolver_redirect_uri_publica: Callable[..., str] | None = None,
    resolver_redirect_uri_bling: Callable[..., str] | None = None,
    bling_session=None,
) -> None:
    global logger, PASTA_INFO, ARQUIVO_LOJAS, ARQUIVO_TEMP_AUTH, _get_tenant_path
    global _normalizar_integracao_conectada, _resolver_redirect_uri_publica
    global _resolver_redirect_uri_bling, _bling_session

    if logger_ref is not None:
        logger = logger_ref
    PASTA_INFO = str(pasta_info or "")
    ARQUIVO_LOJAS = os.path.join(PASTA_INFO, "lojas_config.json")
    ARQUIVO_TEMP_AUTH = os.path.join(PASTA_INFO, "temp_integracao.json")
    _get_tenant_path = get_tenant_path
    if normalizar_integracao_conectada is not None:
        _normalizar_integracao_conectada = normalizar_integracao_conectada
    if resolver_redirect_uri_publica is not None:
        _resolver_redirect_uri_publica = resolver_redirect_uri_publica
    if resolver_redirect_uri_bling is not None:
        _resolver_redirect_uri_bling = resolver_redirect_uri_bling
    if bling_session is not None:
        _bling_session = bling_session


def _tenant_path(client_id: str) -> str:
    if not callable(_get_tenant_path):
        raise RuntimeError("Integracoes service context was not configured.")
    return _get_tenant_path(client_id)


def _integracoes_migrar_arquivo_legado_para_tenant(client_id: str, nome_arquivo: str, caminho_legado: str):
    tenant_path = _tenant_path(client_id)
    destino = os.path.join(tenant_path, nome_arquivo)
    if os.path.exists(destino):
        return destino
    if not (caminho_legado and os.path.exists(caminho_legado)):
        return destino

    try:
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        shutil.move(caminho_legado, destino)
        logger.info("[INTEGRACOES] %s movido para tenant %s", nome_arquivo, client_id)
    except Exception as e:
        try:
            shutil.copy2(caminho_legado, destino)
            logger.warning("[INTEGRACOES] %s copiado para tenant %s (origem preservada): %s", nome_arquivo, client_id, e)
        except Exception as e2:
            logger.warning("[INTEGRACOES] Falha ao migrar %s para tenant %s: %s", nome_arquivo, client_id, e2)
    return destino


def _integracoes_valor_preenchido(valor):
    return valor is not None and str(valor).strip() != ""


def _integracoes_nome_normalizado(nome):
    texto = unicodedata.normalize("NFKD", str(nome or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", "", texto.lower())
    return texto


def _integracoes_nome_equivalente(nome_a, nome_b) -> bool:
    """Compara nomes de loja tolerando acentos, pequenas perdas e texto normalizado."""
    norm_a = _integracoes_nome_normalizado(nome_a)
    norm_b = _integracoes_nome_normalizado(nome_b)
    if not norm_a or not norm_b:
        return False
    if norm_a == norm_b:
        return True
    if norm_a in norm_b or norm_b in norm_a:
        return True
    if min(len(norm_a), len(norm_b)) >= 5 and SequenceMatcher(None, norm_a, norm_b).ratio() >= 0.88:
        return True
    return False


def _integracoes_ler_json(caminho, padrao):
    if not os.path.exists(caminho):
        return padrao
    try:
        with open(caminho, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("[INTEGRACOES] Falha ao ler %s: %s", caminho, e)
        return padrao


def _integracoes_validar_lojas_config(payload, origem: str) -> list:
    if not isinstance(payload, list):
        raise ValueError(f"{origem} deve conter uma lista de lojas.")
    return payload


def _integracoes_ler_lojas_config_arquivo(caminho: str) -> list:
    with open(caminho, "r", encoding="utf-8-sig") as f:
        return _integracoes_validar_lojas_config(json.load(f), caminho)


def _integracoes_backup_imediato_lojas(caminho: str) -> str:
    return f"{caminho}.bak"


def _integracoes_escrever_lojas_config_atomico(caminho: str, lojas: list) -> None:
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp_path = f"{caminho}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(lojas, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        _integracoes_replace_with_retry(tmp_path, caminho)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _integracoes_replace_with_retry(source: str, target: str, *, attempts: int = 12) -> None:
    """Retry only transient Windows access denials without weakening atomicity."""

    maximum_attempts = max(1, int(attempts))
    for attempt in range(maximum_attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 >= maximum_attempts:
                raise
            time.sleep(min(0.25, 0.02 * (2 ** min(attempt, 4))))


def _integracoes_salvar_backup_imediato(caminho: str) -> None:
    if not os.path.exists(caminho):
        return
    try:
        lojas_atuais = _integracoes_ler_lojas_config_arquivo(caminho)
    except Exception as exc:
        logger.warning("[INTEGRACOES] Backup imediato ignorado; arquivo atual invalido: %s", exc)
        return
    _integracoes_escrever_lojas_config_atomico(_integracoes_backup_imediato_lojas(caminho), lojas_atuais)


def _integracoes_restaurar_backup_imediato(caminho: str, erro_original: Exception):
    backup = _integracoes_backup_imediato_lojas(caminho)
    if not os.path.exists(backup):
        return None
    try:
        lojas = _integracoes_ler_lojas_config_arquivo(backup)
        _integracoes_escrever_lojas_config_atomico(caminho, lojas)
        logger.warning("[INTEGRACOES] lojas_config.json restaurado do backup imediato apos falha de leitura: %s", erro_original)
        return lojas
    except Exception as exc:
        logger.error("[INTEGRACOES] Falha ao restaurar backup imediato de lojas_config.json: %s", exc)
        return None


def _integracoes_validar_regressao_lojas(caminho: str, novas_lojas: list, permitir_reducao_confirmada: bool = False) -> None:
    if permitir_reducao_confirmada:
        return
    if not os.path.exists(caminho):
        return
    try:
        lojas_atuais = _integracoes_ler_lojas_config_arquivo(caminho)
    except Exception:
        return
    if len(lojas_atuais) >= 3 and len(novas_lojas) < len(lojas_atuais) - 1:
        msg = (
            "Gravacao de lojas bloqueada: o snapshot novo removeria muitas lojas "
            f"de uma vez ({len(lojas_atuais)} -> {len(novas_lojas)})."
        )
        logger.error("[INTEGRACOES] %s", msg)
        raise HTTPException(status_code=409, detail=msg)


def _integracoes_merge_sem_sobrescrever(atual, legado):
    atual = dict(atual or {})
    mudou = False
    for chave, valor in (legado or {}).items():
        if not _integracoes_valor_preenchido(valor):
            continue
        if not _integracoes_valor_preenchido(atual.get(chave)):
            atual[chave] = valor
            mudou = True
    if legado.get("connected") and "connected" not in atual:
        atual["connected"] = True
        mudou = True
    return atual, mudou


def _integracoes_converter_legado(servico, cfg):
    if not isinstance(cfg, dict):
        return None
    nome_servico = _integracoes_nome_normalizado(servico)
    if "status" in cfg or "connected" in cfg:
        conectado = bool(cfg.get("status") or cfg.get("connected"))
    else:
        conectado = bool(cfg.get("access_token") or cfg.get("refresh_token") or cfg.get("token"))

    if nome_servico == "bling":
        dados = {
            "id": cfg.get("client_id") or cfg.get("id"),
            "secret": cfg.get("client_secret") or cfg.get("secret"),
            "access_token": cfg.get("access_token") or cfg.get("token"),
            "refresh_token": cfg.get("refresh_token"),
            "connected": conectado,
            "updated_at": cfg.get("updated_at"),
        }
    elif nome_servico in {"mercadolivre", "ml"}:
        app_id = cfg.get("app_id") or cfg.get("client_id") or cfg.get("id")
        secret = cfg.get("secret_key") or cfg.get("client_secret") or cfg.get("secret")
        dados = {
            "id": app_id,
            "app_id": app_id,
            "secret": secret,
            "client_secret": secret,
            "access_token": cfg.get("access_token"),
            "refresh_token": cfg.get("refresh_token"),
            "user_id": cfg.get("user_id"),
            "connected": conectado,
            "updated_at": cfg.get("updated_at"),
        }
    elif nome_servico in {"mercadoturbo", "turbo"}:
        dados = {
            "token": cfg.get("token"),
            "connected": conectado,
            "updated_at": cfg.get("updated_at"),
        }
    else:
        return None

    return {k: v for k, v in dados.items() if _integracoes_valor_preenchido(v) or k == "connected"}


def _integracoes_coletar_legadas():
    por_loja = {}

    legado_global = _integracoes_ler_json(os.path.join(PASTA_INFO, "integracoes.json"), {})
    if isinstance(legado_global, dict):
        for nome_loja, integracoes in legado_global.items():
            if not isinstance(integracoes, dict):
                continue
            destino = por_loja.setdefault(str(nome_loja or "").strip(), {})
            for servico, cfg in integracoes.items():
                convertido = _integracoes_converter_legado(servico, cfg)
                if not convertido:
                    continue
                chave = "bling" if _integracoes_nome_normalizado(servico) == "bling" else (
                    "mercadolivre" if _integracoes_nome_normalizado(servico) in {"mercadolivre", "ml"} else "mercadoturbo"
                )
                destino[chave], _ = _integracoes_merge_sem_sobrescrever(destino.get(chave), convertido)

    bling_conf = _integracoes_ler_json(os.path.join(PASTA_INFO, "bling_conf.json"), [])
    if isinstance(bling_conf, list):
        for item in bling_conf:
            if not isinstance(item, dict):
                continue
            nome_loja = str(item.get("loja") or item.get("nome") or "").strip()
            if not nome_loja:
                continue
            token = item.get("token") or item.get("access_token")
            api_key = item.get("apikey") or item.get("api_key")
            if not _integracoes_valor_preenchido(token) and not _integracoes_valor_preenchido(api_key):
                continue
            destino = por_loja.setdefault(nome_loja, {})
            dados_bling = {
                "access_token": token,
                "api_key": api_key,
                "legacy_source": "bling_conf.json",
                "connected": bool(token or api_key),
            }
            destino["bling"], _ = _integracoes_merge_sem_sobrescrever(destino.get("bling"), dados_bling)

    return por_loja


def _integracoes_pode_criar_lojas_legadas(client_id) -> bool:
    client_norm = str(client_id or "default").strip().lower() or "default"
    return client_norm == "default"


def _integracoes_mesclar_legadas(client_id, lojas):
    if not isinstance(lojas, list):
        lojas = []

    legadas = _integracoes_coletar_legadas()
    if not legadas:
        return lojas, False

    mudou = False
    indice = {
        _integracoes_nome_normalizado(loja.get("nome")): loja
        for loja in lojas
        if isinstance(loja, dict) and loja.get("nome")
    }

    for nome_legado, integracoes_legadas in legadas.items():
        chave = _integracoes_nome_normalizado(nome_legado)
        loja = indice.get(chave)
        pode_criar = not lojas and _integracoes_pode_criar_lojas_legadas(client_id)
        if not loja and pode_criar:
            loja = {"nome": nome_legado, "integracoes": {}}
            lojas.append(loja)
            indice[chave] = loja
            mudou = True
        if not loja:
            continue

        integracoes_atuais = loja.setdefault("integracoes", {})
        for servico, dados_legados in (integracoes_legadas or {}).items():
            novo, alterou = _integracoes_merge_sem_sobrescrever(integracoes_atuais.get(servico), dados_legados)
            if alterou or servico not in integracoes_atuais:
                integracoes_atuais[servico] = novo
                mudou = True

    return lojas, mudou


def _integracoes_normalizar_oauth_compartilhado_lojas(lojas):
    if not isinstance(lojas, list):
        return lojas, False
    mudou = False
    for loja in lojas:
        if not isinstance(loja, dict):
            continue
        integracoes = loja.get("integracoes")
        if not isinstance(integracoes, dict):
            continue
        for servico, dados in list(integracoes.items()):
            normalizado = _normalizar_integracao_conectada(servico, dados)
            if isinstance(normalizado, dict) and normalizado != dados:
                integracoes[servico] = normalizado
                mudou = True
    return lojas, mudou


def _integracoes_sync_clean(value):
    if isinstance(value, list):
        return [_integracoes_sync_clean(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _integracoes_sync_clean(item)
            for key, item in value.items()
            if str(key or "").strip().lower()
            not in {"_sync_version", "_sync_updated_at", *_INTEGRACOES_SYNC_TRANSIENT_KEYS}
        }
    return value


def _integracoes_store_id(client_id: str, loja: dict) -> str:
    existing = str((loja or {}).get("store_id") or "").strip()
    if existing:
        return existing
    seed = f"{str(client_id or 'default').strip().lower()}|{_integracoes_nome_normalizado((loja or {}).get('nome'))}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _integracoes_normalizar_sync_metadata(client_id: str, lojas: list, atuais: list | None = None):
    atuais_por_id = {
        _integracoes_store_id(client_id, item): item
        for item in (atuais or []) if isinstance(item, dict)
    }
    changed = False
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for loja in lojas or []:
        if not isinstance(loja, dict):
            continue
        store_id = _integracoes_store_id(client_id, loja)
        if loja.get("store_id") != store_id:
            loja["store_id"] = store_id
            changed = True
        current = atuais_por_id.get(store_id) or {}
        content_changed = _integracoes_sync_clean(loja) != _integracoes_sync_clean(current)
        current_version = int(current.get("_sync_version") or 0)
        expected_version = max(1, current_version + (1 if current and content_changed else 0))
        if int(loja.get("_sync_version") or 0) != expected_version:
            loja["_sync_version"] = expected_version
            changed = True
        if content_changed or not loja.get("_sync_updated_at"):
            loja["_sync_updated_at"] = now
            changed = True
        integracoes = loja.get("integracoes") if isinstance(loja.get("integracoes"), dict) else {}
        current_integracoes = current.get("integracoes") if isinstance(current.get("integracoes"), dict) else {}
        for service, data in integracoes.items():
            if not isinstance(data, dict):
                continue
            current_data = current_integracoes.get(service) if isinstance(current_integracoes.get(service), dict) else {}
            integration_changed = _integracoes_sync_clean(data) != _integracoes_sync_clean(current_data)
            current_iv = int(current_data.get("_sync_version") or 0)
            expected_iv = max(1, current_iv + (1 if current_data and integration_changed else 0))
            if int(data.get("_sync_version") or 0) != expected_iv:
                data["_sync_version"] = expected_iv
                changed = True
            if integration_changed or not data.get("_sync_updated_at"):
                data["_sync_updated_at"] = now
                changed = True
    return lojas, changed


def _integracoes_tombstones_path(client_id: str) -> str:
    return os.path.join(_tenant_path(client_id), "lojas_sync_tombstones.json")


def registrar_tombstone_integracao(client_id: str, *, loja: dict, servico: str = "", tipo: str = "store") -> None:
    with _LOJAS_CONFIG_LOCK:
        path = _integracoes_tombstones_path(client_id)
        payload = _integracoes_ler_json(path, [])
        if not isinstance(payload, list):
            payload = []
        store_id = _integracoes_store_id(client_id, loja or {})
        key = f"{tipo}:{store_id}:{str(servico or '').strip().lower()}"
        payload = [item for item in payload if str((item or {}).get("key") or "") != key]
        payload.append({
            "key": key,
            "type": tipo,
            "store_id": store_id,
            "service": str(servico or "").strip().lower(),
            "version": int((loja or {}).get("_sync_version") or 0) + 1,
            "deleted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        _integracoes_escrever_lojas_config_atomico(path, payload)


def carregar_lojas(client_id: str):
    """Carrega as lojas do cliente do arquivo JSON."""
    with _LOJAS_CONFIG_LOCK:
        arquivo_lojas = _integracoes_migrar_arquivo_legado_para_tenant(client_id, "lojas_config.json", ARQUIVO_LOJAS)

        if os.path.exists(arquivo_lojas):
            try:
                lojas = _integracoes_ler_lojas_config_arquivo(arquivo_lojas)
            except Exception as e:
                lojas = _integracoes_restaurar_backup_imediato(arquivo_lojas, e)
                if lojas is None:
                    logger.error("Erro ao carregar lojas do cliente %s: %s", client_id, e)
                    raise HTTPException(
                        status_code=500,
                        detail="Configuracao de lojas invalida; gravacao bloqueada para preservar integracoes.",
                    )
            lojas, mudou = _integracoes_mesclar_legadas(client_id, lojas)
            lojas, mudou_oauth = _integracoes_normalizar_oauth_compartilhado_lojas(lojas)
            mudou = mudou or mudou_oauth
            lojas, mudou_sync = _integracoes_normalizar_sync_metadata(client_id, lojas, lojas)
            mudou = mudou or mudou_sync
            if mudou:
                salvar_lojas(client_id, lojas)
            return lojas
        lojas, mudou = _integracoes_mesclar_legadas(client_id, [])
        lojas, mudou_oauth = _integracoes_normalizar_oauth_compartilhado_lojas(lojas)
        mudou = mudou or mudou_oauth
        lojas, mudou_sync = _integracoes_normalizar_sync_metadata(client_id, lojas, lojas)
        mudou = mudou or mudou_sync
        if mudou:
            salvar_lojas(client_id, lojas)
        return lojas


def salvar_lojas(client_id: str, lojas: list, *, permitir_reducao_confirmada: bool = False):
    """Salva as lojas do cliente no arquivo JSON."""
    tenant_path = _tenant_path(client_id)
    arquivo_lojas = os.path.join(tenant_path, "lojas_config.json")

    with _LOJAS_CONFIG_LOCK:
        try:
            _integracoes_validar_lojas_config(lojas, "payload de lojas")
            atuais = []
            if os.path.exists(arquivo_lojas):
                try:
                    atuais = _integracoes_ler_lojas_config_arquivo(arquivo_lojas)
                except Exception:
                    atuais = []
            lojas, _ = _integracoes_normalizar_sync_metadata(client_id, lojas, atuais)
            _integracoes_validar_regressao_lojas(arquivo_lojas, lojas, permitir_reducao_confirmada)
            _integracoes_salvar_backup_imediato(arquivo_lojas)
            _integracoes_escrever_lojas_config_atomico(arquivo_lojas, lojas)
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Erro ao salvar lojas do cliente %s: %s", client_id, e)
            raise HTTPException(status_code=500, detail="Erro ao salvar configuracao de lojas.")


def buscar_loja(client_id: str, nome_loja: str):
    """Busca uma loja especifica do cliente."""
    lojas = carregar_lojas(client_id)
    nome_alvo = str(nome_loja or "").strip()
    for loja in lojas:
        if str(loja.get("nome") or "").strip() == nome_alvo:
            return loja
    for loja in lojas:
        if _integracoes_nome_equivalente(loja.get("nome"), nome_alvo):
            return loja
    return None


def _integracoes_encontrar_loja(lojas: list, nome_loja: str):
    nome_alvo = str(nome_loja or "").strip()
    for loja in lojas:
        if isinstance(loja, dict) and str(loja.get("nome") or "").strip() == nome_alvo:
            return loja
    for loja in lojas:
        if isinstance(loja, dict) and _integracoes_nome_equivalente(loja.get("nome"), nome_alvo):
            return loja
    return None


def atualizar_api_loja(
    client_id: str,
    nome_loja: str,
    api_nome: str,
    dados_api: dict,
    *,
    require_existing: bool = False,
    expected_oauth_state: str | None = None,
):
    """Cria ou atualiza uma loja e sua integracao para um cliente especifico."""
    if isinstance(dados_api, dict) and str(api_nome or "").strip().lower() in {"bling", "mercadolivre", "ml"}:
        try:
            dados_api = _normalizar_integracao_conectada(api_nome, dados_api)
        except Exception:
            dados_api = dict(dados_api or {})
    # O lock cobre todo o read-modify-write. Antes, carregar e salvar eram
    # protegidos isoladamente, permitindo que duas lojas perdessem updates.
    with _LOJAS_CONFIG_LOCK:
        lojas = carregar_lojas(client_id)
        exige_identidade_exata = bool(require_existing or expected_oauth_state)
        if exige_identidade_exata:
            nome_exato = str(nome_loja or "").strip()
            loja = next(
                (
                    item
                    for item in lojas
                    if isinstance(item, dict)
                    and str(item.get("nome") or "").strip() == nome_exato
                ),
                None,
            )
        else:
            loja = _integracoes_encontrar_loja(lojas, nome_loja)
        if loja is None:
            if exige_identidade_exata:
                raise HTTPException(
                    status_code=409,
                    detail="A loja do fluxo OAuth nao existe mais.",
                )
            loja = {"nome": nome_loja, "integracoes": {}}
            lojas.append(loja)
        integracoes = loja.setdefault("integracoes", {})
        atual = integracoes.get(api_nome)
        if expected_oauth_state:
            atual_dict = atual if isinstance(atual, dict) else {}
            if str(api_nome or "").strip().lower() == "bling":
                oauth_state_atual = str(
                    atual_dict.get("oauth_pending_state") or ""
                ).strip()
            else:
                draft = atual_dict.get("oauth_draft")
                oauth_state_atual = str(
                    draft.get("state") if isinstance(draft, dict) else ""
                ).strip()
            if not oauth_state_atual or not secrets.compare_digest(
                oauth_state_atual,
                str(expected_oauth_state).strip(),
            ):
                raise HTTPException(
                    status_code=409,
                    detail="O fluxo OAuth foi substituido ou cancelado.",
                )
        if isinstance(atual, dict) and isinstance(dados_api, dict):
            merged = dict(atual)
            merged.update(dados_api)
            integracoes[api_nome] = merged
        else:
            integracoes[api_nome] = dados_api
        salvar_lojas(client_id, lojas)


def _bling_refresh_lock(client_id: str, nome_loja: str) -> threading.Lock:
    key = (
        str(client_id or "default").strip().lower() or "default",
        _integracoes_nome_normalizado(nome_loja),
    )
    with _BLING_REFRESH_LOCKS_GUARD:
        lock = _BLING_REFRESH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _BLING_REFRESH_LOCKS[key] = lock
        return lock


def _bling_config_atual(client_id: str, nome_loja: str) -> dict:
    with _LOJAS_CONFIG_LOCK:
        loja = _integracoes_encontrar_loja(carregar_lojas(client_id), nome_loja)
        if not isinstance(loja, dict):
            return {}
        return dict(((loja.get("integracoes") or {}).get("bling") or {}))


def _atualizar_bling_cas(
    client_id: str,
    nome_loja: str,
    *,
    expected_refresh_token: str,
    expected_access_token: str | None = None,
    expected_updated_at: str | None = None,
    expected_sync_version: str | int | None = None,
    dados_api: dict,
) -> tuple[bool, dict]:
    """Atualiza OAuth somente se o snapshot que iniciou a operacao for atual."""
    with _LOJAS_CONFIG_LOCK:
        lojas = carregar_lojas(client_id)
        loja = _integracoes_encontrar_loja(lojas, nome_loja)
        if not isinstance(loja, dict):
            raise HTTPException(status_code=404, detail="Loja nao encontrada para renovar token Bling.")
        integracoes = loja.setdefault("integracoes", {})
        atual = dict(integracoes.get("bling") or {})
        refresh_atual = str(atual.get("refresh_token") or "").strip()
        if refresh_atual != str(expected_refresh_token or "").strip():
            return False, atual
        if expected_access_token is not None and str(atual.get("access_token") or "").strip() != str(expected_access_token or "").strip():
            return False, atual
        if expected_updated_at is not None and str(atual.get("updated_at") or "").strip() != str(expected_updated_at or "").strip():
            return False, atual
        if expected_sync_version is not None and str(atual.get("_sync_version") or "").strip() != str(expected_sync_version or "").strip():
            return False, atual
        atualizado = dict(atual)
        atualizado.update(dict(dados_api or {}))
        try:
            normalizado = _normalizar_integracao_conectada("bling", atualizado)
            if isinstance(normalizado, dict):
                atualizado = normalizado
        except Exception:
            pass
        integracoes["bling"] = atualizado
        salvar_lojas(client_id, lojas)
        return True, dict(atualizado)


def renovar_token_bling_loja(client_id: str, nome_loja: str, cfg: dict | None = None) -> dict:
    """Single-flight por tenant/loja com releitura e persistencia CAS."""
    hint = dict(cfg or {})
    expected_refresh = str(hint.get("refresh_token") or "").strip()
    expected_access = str(hint.get("access_token") or "").strip()
    expected_updated_at = str(hint.get("updated_at") or "").strip()
    with _bling_refresh_lock(client_id, nome_loja):
        atual = _bling_config_atual(client_id, nome_loja)
        refresh_atual = str(atual.get("refresh_token") or "").strip()
        access_atual = str(atual.get("access_token") or "").strip()
        updated_at_atual = str(atual.get("updated_at") or "").strip()

        # Outra thread renovou ou o usuario reconectou enquanto este chamador
        # ainda carregava o snapshot antigo. Reutilize o token mais novo.
        if expected_refresh and refresh_atual and refresh_atual != expected_refresh:
            return atual
        if expected_refresh and refresh_atual == expected_refresh and (
            (expected_access and access_atual and access_atual != expected_access)
            or (expected_updated_at and updated_at_atual and updated_at_atual != expected_updated_at)
        ):
            return atual

        base = dict(hint)
        base.update(atual)
        client_oauth_id = str(base.get("id") or base.get("client_id") or "").strip()
        client_secret = str(base.get("secret") or base.get("client_secret") or "").strip()
        refresh_usado = str(base.get("refresh_token") or expected_refresh or "").strip()
        access_usado = str(base.get("access_token") or "").strip()
        updated_at_usado = str(base.get("updated_at") or "").strip()
        sync_version_usada = base.get("_sync_version")
        if not (client_oauth_id and client_secret and refresh_usado):
            raise HTTPException(
                status_code=401,
                detail="Credenciais Bling incompletas. Refaca a conexao em Integracoes.",
            )

        try:
            novos = exchange_bling_refresh_token(client_oauth_id, client_secret, refresh_usado)
        except HTTPException as exc:
            if exc.status_code == 401:
                recente = _bling_config_atual(client_id, nome_loja)
                if str(recente.get("refresh_token") or "").strip() != refresh_usado:
                    return recente
                gravou_invalido, _persistido_invalido = _atualizar_bling_cas(
                    client_id,
                    nome_loja,
                    expected_refresh_token=refresh_usado,
                    expected_access_token=access_usado,
                    expected_updated_at=updated_at_usado,
                    expected_sync_version=sync_version_usada,
                    dados_api={
                        "connected": False,
                        "status": "reautenticacao_necessaria",
                        "motivo": str(exc.detail or "Token Bling expirado."),
                        "oauth_invalid": True,
                        "shared_without_oauth_tokens": False,
                        "updated_at": str(time.time()),
                    },
                )
                if not gravou_invalido:
                    return _bling_config_atual(client_id, nome_loja)
            raise

        access_token = str(novos.get("access_token") or "").strip()
        if not access_token:
            raise HTTPException(status_code=502, detail="Resposta invalida ao renovar token do Bling.")
        refresh_novo = str(novos.get("refresh_token") or refresh_usado).strip()
        atualizado = dict(base)
        atualizado.update({
            "id": client_oauth_id,
            "secret": client_secret,
            "access_token": access_token,
            "refresh_token": refresh_novo,
            "connected": True,
            "status": "conectado",
            "motivo": "",
            "oauth_invalid": False,
            "shared_without_oauth_tokens": False,
            "updated_at": str(time.time()),
        })
        gravou, persistido = _atualizar_bling_cas(
            client_id,
            nome_loja,
            expected_refresh_token=refresh_usado,
            expected_access_token=access_usado,
            expected_updated_at=updated_at_usado,
            expected_sync_version=sync_version_usada,
            dados_api=atualizado,
        )
        # Uma reconexao pode vencer o CAS enquanto o POST estava em voo.
        return persistido if gravou else _bling_config_atual(client_id, nome_loja)


def marcar_token_bling_invalido(client_id: str, nome_loja: str, cfg: dict | None, motivo: str) -> dict:
    """Invalida apenas o mesmo refresh token que produziu o 401 observado."""
    expected_refresh = str((cfg or {}).get("refresh_token") or "").strip()
    if not expected_refresh:
        return _bling_config_atual(client_id, nome_loja)
    gravou, persistido = _atualizar_bling_cas(
        client_id,
        nome_loja,
        expected_refresh_token=expected_refresh,
        expected_access_token=str((cfg or {}).get("access_token") or "").strip(),
        expected_updated_at=str((cfg or {}).get("updated_at") or "").strip(),
        expected_sync_version=(cfg or {}).get("_sync_version"),
        dados_api={
            "connected": False,
            "status": "reautenticacao_necessaria",
            "motivo": str(motivo or "Token Bling expirado. Refaca a conexao em Integracoes."),
            "oauth_invalid": True,
            "shared_without_oauth_tokens": False,
            "updated_at": str(time.time()),
        },
    )
    return persistido if gravou else _bling_config_atual(client_id, nome_loja)


def desconectar_api_loja(client_id: str, nome_loja: str, api_nome: str) -> dict:
    # A desconexao concorre com refresh/reconexao OAuth e, por isso, tambem
    # precisa manter o lock durante todo o ciclo read-modify-write.
    with _LOJAS_CONFIG_LOCK:
        lojas = carregar_lojas(client_id)
        for loja in lojas:
            if loja.get("nome") != nome_loja:
                continue
            integracoes = loja.setdefault("integracoes", {})
            integracoes[api_nome] = {"connected": False}
            registrar_tombstone_integracao(client_id, loja=loja, servico=api_nome, tipo="integration")
            salvar_lojas(client_id, lojas)
            return loja
    raise HTTPException(status_code=404, detail="Loja nao encontrada.")


def _integracoes_temp_auth_carregar_fluxos() -> tuple[dict[str, dict], bool]:
    payload = _integracoes_ler_json(ARQUIVO_TEMP_AUTH, {})
    fluxos: dict[str, dict] = {}
    if isinstance(payload, dict) and isinstance(payload.get("flows"), dict):
        fluxos = {
            str(chave): dict(valor)
            for chave, valor in payload["flows"].items()
            if str(chave).strip() and isinstance(valor, dict)
        }
    elif isinstance(payload, dict) and str(payload.get("state") or "").strip():
        estado_legado = str(payload.get("state") or "").strip()
        fluxos[estado_legado] = dict(payload)

    agora = time.time()
    ativos: dict[str, dict] = {}
    for estado, dados in fluxos.items():
        try:
            criado_em = float(dados.get("created_at") or 0)
        except (TypeError, ValueError):
            criado_em = 0
        if (
            criado_em <= 0
            or criado_em > agora + 60
            or agora - criado_em > _TEMP_AUTH_TTL_SECONDS
        ):
            continue
        if str(dados.get("state") or "").strip() != estado:
            continue
        ativos[estado] = dados
    return ativos, ativos != fluxos


def _integracoes_temp_auth_persistir_fluxos(fluxos: dict[str, dict]) -> None:
    _integracoes_escrever_lojas_config_atomico(
        ARQUIVO_TEMP_AUTH,
        {"version": 2, "flows": fluxos},
    )


def salvar_temp_auth(dados):
    registro = dict(dados or {})
    estado = str(registro.get("state") or "").strip()
    if not estado:
        estado = secrets.token_urlsafe(24)
    registro["state"] = estado
    try:
        criado_em = float(registro.get("created_at") or time.time())
    except (TypeError, ValueError):
        criado_em = time.time()
    registro["created_at"] = criado_em
    with _TEMP_AUTH_LOCK:
        fluxos, _ = _integracoes_temp_auth_carregar_fluxos()
        fluxos[estado] = registro
        _integracoes_temp_auth_persistir_fluxos(fluxos)
    return estado


def ler_temp_auth(state: str | None = None):
    estado = str(state or "").strip()
    with _TEMP_AUTH_LOCK:
        fluxos, mudou = _integracoes_temp_auth_carregar_fluxos()
        if mudou:
            _integracoes_temp_auth_persistir_fluxos(fluxos)
        if estado:
            registro = fluxos.get(estado)
        else:
            registro = max(
                fluxos.values(),
                key=lambda item: float(item.get("created_at") or 0),
                default=None,
            )
        return dict(registro) if isinstance(registro, dict) else None


def consumir_temp_auth(state: str | None):
    estado = str(state or "").strip()
    if not estado:
        return None
    with _TEMP_AUTH_LOCK:
        fluxos, mudou = _integracoes_temp_auth_carregar_fluxos()
        registro = fluxos.pop(estado, None)
        if registro is not None or mudou:
            _integracoes_temp_auth_persistir_fluxos(fluxos)
        return dict(registro) if isinstance(registro, dict) else None


def limpar_temp_auth(state: str | None = None):
    estado = str(state or "").strip()
    with _TEMP_AUTH_LOCK:
        fluxos, mudou = _integracoes_temp_auth_carregar_fluxos()
        if estado:
            mudou = fluxos.pop(estado, None) is not None or mudou
        elif fluxos:
            fluxos = {}
            mudou = True
        if mudou:
            _integracoes_temp_auth_persistir_fluxos(fluxos)


def auth_bling_get_link(client_id, state, redirect_uri=None):
    redirect_final = _resolver_redirect_uri_publica(saved_redirect_uri=redirect_uri)
    redirect = quote_plus(redirect_final)
    state_final = quote_plus(str(state or ""))
    return f"https://www.bling.com.br/Api/v3/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri={redirect}&state={state_final}"


def auth_bling_exchange(client_id, client_secret, code, redirect_uri=None):
    url = "https://www.bling.com.br/Api/v3/oauth/token"
    credential = f"{client_id}:{client_secret}"
    headers = {
        "Authorization": f"Basic {base64.b64encode(credential.encode()).decode()}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "enable-jwt": "1",
    }
    redirect_final = _resolver_redirect_uri_bling(saved_redirect_uri=redirect_uri)
    payload = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_final}
    try:
        resp = _bling_session.post(url, headers=headers, data=payload, timeout=20)
        if resp.status_code == 200:
            return True, resp.json()
        return False, resp.text
    except Exception as e:
        return False, str(e)


def auth_ml_get_link(app_id, state, redirect_uri=None):
    redirect_final = _resolver_redirect_uri_publica(saved_redirect_uri=redirect_uri)
    params = {
        "response_type": "code",
        "client_id": app_id,
        "redirect_uri": redirect_final,
        "state": str(state or ""),
        "scope": "offline_access read write",
    }
    return "https://auth.mercadolivre.com.br/authorization?" + urlencode(params, quote_via=quote)


def auth_ml_exchange(app_id, client_secret, code, redirect_uri=None):
    url = "https://api.mercadolibre.com/oauth/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    redirect_final = _resolver_redirect_uri_publica(saved_redirect_uri=redirect_uri)
    payload = {
        "grant_type": "authorization_code",
        "client_id": app_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_final,
    }
    try:
        logger.info("[ML EXCHANGE] Iniciando troca OAuth sanitizada.")

        resp = requests.post(url, headers=headers, data=payload, timeout=10)

        logger.info("[ML EXCHANGE] Status Code: %s", resp.status_code)

        if resp.status_code == 200:
            result = resp.json()
            logger.info("[ML EXCHANGE] SUCCESS")
            return True, result

        logger.error("[ML EXCHANGE] Falha OAuth HTTP %s.", resp.status_code)
        return False, f"Mercado Livre recusou a troca OAuth (HTTP {resp.status_code})."

    except Exception:
        logger.error("[ML EXCHANGE] Falha de comunicacao na troca OAuth.")
        return False, "Falha de comunicacao com o Mercado Livre durante a troca OAuth."


__all__ = [
    "configure_integracoes_context",
    "ARQUIVO_LOJAS",
    "ARQUIVO_TEMP_AUTH",
    "_integracoes_valor_preenchido",
    "_integracoes_nome_normalizado",
    "_integracoes_nome_equivalente",
    "_integracoes_ler_json",
    "_integracoes_merge_sem_sobrescrever",
    "_integracoes_converter_legado",
    "_integracoes_coletar_legadas",
    "_integracoes_pode_criar_lojas_legadas",
    "_integracoes_mesclar_legadas",
    "_integracoes_normalizar_oauth_compartilhado_lojas",
    "carregar_lojas",
    "salvar_lojas",
    "buscar_loja",
    "atualizar_api_loja",
    "renovar_token_bling_loja",
    "marcar_token_bling_invalido",
    "desconectar_api_loja",
    "registrar_tombstone_integracao",
    "salvar_temp_auth",
    "ler_temp_auth",
    "consumir_temp_auth",
    "limpar_temp_auth",
    "auth_bling_get_link",
    "auth_bling_exchange",
    "auth_ml_get_link",
    "auth_ml_exchange",
]
