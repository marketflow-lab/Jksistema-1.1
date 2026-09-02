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
        with open(caminho_legado, "rb") as arquivo:
            legado_bytes = arquivo.read()
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail="Nao foi possivel validar a configuracao global antiga de lojas.",
        ) from exc
    if not _integracoes_lojas_bytes_pertencem_ao_cliente(client_id, legado_bytes):
        raise HTTPException(
            status_code=409,
            detail=(
                "Existe uma configuracao global antiga de lojas sem vinculo "
                "comprovado com este cliente. A migracao foi bloqueada para "
                "impedir mistura de contas."
            ),
        )

    try:
        lojas = _integracoes_validar_lojas_config(
            json.loads(legado_bytes.decode("utf-8-sig")),
            caminho_legado,
        )
        _integracoes_validar_identidades_lojas_local(lojas)
        # Nunca materializa o legado bruto no tenant. Um tombstone ja
        # persistido precisa ser aplicado antes do primeiro replace atomico;
        # assim nem uma queda neste ponto ressuscita uma conta excluida.
        lojas, _ = _integracoes_aplicar_tombstones_ativos(client_id, lojas)
        _integracoes_escrever_lojas_config_atomico(destino, lojas)
        logger.info(
            "[INTEGRACOES] %s migrado com seguranca para tenant %s",
            nome_arquivo,
            client_id,
        )
        _integracoes_arquivar_legado_global_coexistente(destino)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "[INTEGRACOES] Falha ao migrar %s para tenant %s: %s",
            nome_arquivo,
            client_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Nao foi possivel migrar a configuracao antiga de lojas. "
                "A operacao foi cancelada para preservar as contas."
            ),
        ) from exc
    return destino


def _integracoes_valor_preenchido(valor):
    return valor is not None and str(valor).strip() != ""


def _integracoes_nome_normalizado(nome):
    texto = unicodedata.normalize("NFKD", str(nome or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", "", texto.lower())
    return texto


def _integracoes_servico_key(servico) -> str:
    chave = _integracoes_nome_normalizado(servico)
    if chave in {"mercadolivre", "ml"}:
        return "mercadolivre"
    if chave in {"mercadoturbo", "turbo"}:
        return "mercadoturbo"
    return chave


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


def _integracoes_quarentenar_backup_invalido(caminho: str) -> None:
    backup = _integracoes_backup_imediato_lojas(caminho)
    if not os.path.exists(backup):
        return
    destino_dir = os.path.join(
        os.path.dirname(caminho),
        "_shared_sync_backups",
        f"invalid_backup_{int(time.time() * 1000)}",
    )
    try:
        os.makedirs(destino_dir, exist_ok=False)
        shutil.move(backup, os.path.join(destino_dir, "lojas_config.json.bak"))
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "O arquivo principal de lojas esta integro, mas o backup "
                "invalido nao pôde ser isolado com seguranca."
            ),
        ) from exc
    logger.warning(
        "[INTEGRACOES] Backup imediato invalido isolado; o arquivo principal "
        "integro sera usado para gerar um novo backup."
    )


def _integracoes_isolar_backup_invalido_se_necessario(
    client_id: str,
) -> bool:
    caminho = os.path.join(_tenant_path(client_id), "lojas_config.json")
    backup = _integracoes_backup_imediato_lojas(caminho)
    if not os.path.exists(backup):
        return False
    try:
        lojas_backup = _integracoes_ler_lojas_config_arquivo(backup)
        _integracoes_validar_identidades_lojas_local(lojas_backup)
        return False
    except Exception:
        _integracoes_quarentenar_backup_invalido(caminho)
        return True


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


def _integracoes_credenciais_backup_compativeis(
    servico_key: str,
    atual: dict,
    backup: dict,
) -> bool:
    """Impede que um backup rico de outra conta seja associado por engano."""

    grupos_identidade: tuple[tuple[str, ...], ...] = ()
    if servico_key == "mercadolivre":
        grupos_identidade = (
            ("app_id", "client_id", "id"),
            ("client_secret", "secret_key", "secret"),
            ("user_id",),
        )
    elif servico_key == "bling":
        grupos_identidade = (
            ("id", "client_id", "app_id"),
            ("secret", "client_secret", "secret_key"),
        )

    for aliases in grupos_identidade:
        valor_atual = _integracoes_alias_unico(atual, *aliases)
        valor_backup = _integracoes_alias_unico(backup, *aliases)
        if (
            _integracoes_valor_preenchido(valor_atual)
            and _integracoes_valor_preenchido(valor_backup)
            and str(valor_atual).strip() != str(valor_backup).strip()
        ):
            return False

    def igual_preenchido(*aliases: str) -> bool:
        valor_atual = _integracoes_alias_unico(atual, *aliases)
        valor_backup = _integracoes_alias_unico(backup, *aliases)
        return bool(
            _integracoes_valor_preenchido(valor_atual)
            and _integracoes_valor_preenchido(valor_backup)
            and str(valor_atual).strip() == str(valor_backup).strip()
        )

    def presente_atual(*aliases: str) -> bool:
        return _integracoes_valor_preenchido(
            _integracoes_alias_unico(atual, *aliases)
        )

    def conflito_preenchido(*aliases: str) -> bool:
        valor_atual = _integracoes_alias_unico(atual, *aliases)
        valor_backup = _integracoes_alias_unico(backup, *aliases)
        return bool(
            _integracoes_valor_preenchido(valor_atual)
            and _integracoes_valor_preenchido(valor_backup)
            and str(valor_atual).strip() != str(valor_backup).strip()
        )

    if servico_key == "mercadolivre":
        mesma_aplicacao = bool(
            igual_preenchido("app_id", "client_id", "id")
            and igual_preenchido(
                "client_secret",
                "secret_key",
                "secret",
            )
        )
        grupos_conta = (
            ("user_id",),
            ("access_token",),
            ("refresh_token",),
        )
        if any(conflito_preenchido(*grupo) for grupo in grupos_conta):
            return False
        if any(presente_atual(*grupo) for grupo in grupos_conta):
            return any(igual_preenchido(*grupo) for grupo in grupos_conta)
        return mesma_aplicacao
    if servico_key == "bling":
        mesmo_oauth = bool(
            igual_preenchido("id", "client_id", "app_id")
            and igual_preenchido(
                "secret",
                "client_secret",
                "secret_key",
            )
        )
        grupos_conta = (
            ("api_key", "apikey"),
            ("access_token", "token"),
            ("refresh_token",),
        )
        if any(conflito_preenchido(*grupo) for grupo in grupos_conta):
            return False
        if any(presente_atual(*grupo) for grupo in grupos_conta):
            return any(igual_preenchido(*grupo) for grupo in grupos_conta)
        return mesmo_oauth
    if servico_key == "mercadoturbo":
        return igual_preenchido("token", "access_token")
    return False


def _integracoes_projetar_backup_final(
    client_id: str,
    lojas_finais: list,
    backup_path: str,
) -> list:
    """Espelha a forma final e preserva apenas credenciais recuperaveis.

    O arquivo ``.bak`` nao pode manter lojas ou integracoes que ja sumiram do
    estado final (inclusive por tombstone). Quando o principal possui apenas
    a configuracao publica de um OAuth, entretanto, o ultimo bloco completo da
    mesma conta continua disponivel para recuperar uma corrupcao de disco.
    """

    projetadas = json.loads(json.dumps(lojas_finais, ensure_ascii=False))
    projetadas, _ = _integracoes_aplicar_tombstones_ativos(
        client_id,
        projetadas,
    )
    _integracoes_validar_identidades_lojas_local(projetadas)
    if not os.path.exists(backup_path):
        return projetadas

    lojas_backup = _integracoes_ler_lojas_config_arquivo(backup_path)
    _integracoes_validar_identidades_lojas_local(lojas_backup)
    from backend.services.shared_sync_merge_integracoes import (
        _shared_sync_integracao_credencial_rank,
        _shared_sync_servico_key,
    )

    finais_por_id = {
        _integracoes_store_id(client_id, loja): loja
        for loja in projetadas
        if isinstance(loja, dict)
    }
    for loja_backup in lojas_backup:
        if not isinstance(loja_backup, dict):
            continue
        store_id = _integracoes_store_id(client_id, loja_backup)
        loja_final = finais_por_id.get(store_id)
        if not isinstance(loja_final, dict):
            continue
        if (
            not str(loja_backup.get("store_id") or "").strip()
            and str(loja_backup.get("nome") or "").strip()
            != str(loja_final.get("nome") or "").strip()
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "O backup local possui uma loja antiga sem identidade "
                    "inequivoca. A gravacao foi bloqueada para preservar as contas."
                ),
            )

        integracoes_finais = (
            loja_final.get("integracoes")
            if isinstance(loja_final.get("integracoes"), dict)
            else {}
        )
        finais_por_servico = {
            _shared_sync_servico_key(servico): (servico, dados)
            for servico, dados in integracoes_finais.items()
        }
        integracoes_backup = (
            loja_backup.get("integracoes")
            if isinstance(loja_backup.get("integracoes"), dict)
            else {}
        )
        for servico, dados_backup in integracoes_backup.items():
            servico_key = _shared_sync_servico_key(servico)
            entrada_final = finais_por_servico.get(servico_key)
            if (
                not servico_key
                or entrada_final is None
                or not isinstance(dados_backup, dict)
            ):
                continue
            chave_final, dados_final = entrada_final
            if not isinstance(dados_final, dict):
                continue
            if not _integracoes_credenciais_backup_compativeis(
                servico_key,
                dados_final,
                dados_backup,
            ):
                continue
            if _shared_sync_integracao_credencial_rank(
                servico_key,
                dados_backup,
            ) > _shared_sync_integracao_credencial_rank(
                servico_key,
                dados_final,
            ):
                integracoes_finais[chave_final] = json.loads(
                    json.dumps(dados_backup, ensure_ascii=False)
                )
    return projetadas


def _integracoes_salvar_backup_imediato(client_id: str, caminho: str) -> None:
    if not os.path.exists(caminho):
        return
    try:
        lojas_atuais = _integracoes_ler_lojas_config_arquivo(caminho)
    except Exception as exc:
        logger.warning("[INTEGRACOES] Backup imediato ignorado; arquivo atual invalido: %s", exc)
        return
    backup_path = _integracoes_backup_imediato_lojas(caminho)
    lojas_backup = _integracoes_projetar_backup_final(
        client_id,
        lojas_atuais,
        backup_path,
    )
    _integracoes_escrever_lojas_config_atomico(backup_path, lojas_backup)


def _integracoes_espelhar_backup_final_seguro(
    client_id: str,
    caminho: str,
    lojas: list,
) -> bool:
    backup_path = _integracoes_backup_imediato_lojas(caminho)
    lojas_backup = _integracoes_projetar_backup_final(
        client_id,
        lojas,
        backup_path,
    )
    _integracoes_escrever_lojas_config_atomico(backup_path, lojas_backup)
    return True


def _integracoes_restaurar_backup_imediato(caminho: str, erro_original: Exception):
    backup = _integracoes_backup_imediato_lojas(caminho)
    if not os.path.exists(backup):
        return None
    try:
        lojas = _integracoes_ler_lojas_config_arquivo(backup)
        # A publicacao ocorre somente no fim de carregar_lojas, depois de
        # validar identidades e aplicar tombstones. Gravar o backup bruto aqui
        # criava uma janela de crash capaz de ressuscitar credenciais apagadas.
        logger.warning(
            "[INTEGRACOES] lojas_config.json sera recuperado do backup "
            "imediato apos as validacoes: %s",
            erro_original,
        )
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


def _integracoes_alias_unico(cfg: dict, *chaves: str):
    preenchidos = [
        (chave, cfg.get(chave))
        for chave in chaves
        if _integracoes_valor_preenchido(cfg.get(chave))
    ]
    valores = {str(valor).strip() for _chave, valor in preenchidos}
    if len(valores) > 1:
        raise HTTPException(
            status_code=409,
            detail=(
                "Uma configuracao antiga contem aliases conflitantes para a "
                "mesma credencial. Revise os dados antes de importar."
            ),
        )
    return preenchidos[0][1] if preenchidos else None


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
            "id": _integracoes_alias_unico(cfg, "client_id", "id", "app_id"),
            "secret": _integracoes_alias_unico(cfg, "client_secret", "secret", "secret_key"),
            "access_token": _integracoes_alias_unico(cfg, "access_token", "token"),
            "refresh_token": cfg.get("refresh_token"),
            "api_key": _integracoes_alias_unico(cfg, "api_key", "apikey"),
            "connected": conectado,
            "updated_at": cfg.get("updated_at"),
        }
    elif nome_servico in {"mercadolivre", "ml"}:
        app_id = _integracoes_alias_unico(cfg, "app_id", "client_id", "id")
        secret = _integracoes_alias_unico(
            cfg,
            "secret_key",
            "client_secret",
            "secret",
        )
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
            "token": _integracoes_alias_unico(cfg, "token", "access_token"),
            "connected": conectado,
            "updated_at": cfg.get("updated_at"),
        }
    else:
        return None

    return {k: v for k, v in dados.items() if _integracoes_valor_preenchido(v) or k == "connected"}


def _integracoes_coletar_legadas():
    por_loja = {}
    nomes_por_chave: dict[str, str] = {}

    def destino_para(nome_loja):
        nome = str(nome_loja or "").strip()
        if not nome:
            return None
        nome_key = _integracoes_nome_normalizado(nome)
        if not nome_key:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Uma configuracao global antiga contem nome de loja que "
                    "nao pode ser identificado com seguranca."
                ),
            )
        nome_anterior = nomes_por_chave.get(nome_key)
        if nome_anterior is not None and nome_anterior != nome:
            raise HTTPException(
                status_code=409,
                detail=(
                    "As configuracoes globais antigas contem nomes de loja "
                    "ambiguos. Revise as lojas antes de importar as integracoes."
                ),
            )
        nomes_por_chave.setdefault(nome_key, nome)
        return por_loja.setdefault(nome, {})

    def assinatura_servico(servico: str, dados: dict) -> dict:
        chave = _integracoes_servico_key(servico)
        if chave == "mercadolivre":
            aliases = {
                "app_id": dados.get("app_id") or dados.get("client_id") or dados.get("id"),
                "client_secret": dados.get("client_secret") or dados.get("secret_key") or dados.get("secret"),
                "access_token": dados.get("access_token"),
                "refresh_token": dados.get("refresh_token"),
                "user_id": dados.get("user_id"),
            }
        elif chave == "bling":
            aliases = {
                "id": dados.get("id") or dados.get("client_id") or dados.get("app_id"),
                "secret": dados.get("secret") or dados.get("client_secret") or dados.get("secret_key"),
                "access_token": dados.get("access_token") or dados.get("token"),
                "refresh_token": dados.get("refresh_token"),
                "api_key": dados.get("api_key") or dados.get("apikey"),
            }
        else:
            aliases = {"token": dados.get("token") or dados.get("access_token")}
        return {
            nome: str(valor).strip()
            for nome, valor in aliases.items()
            if _integracoes_valor_preenchido(valor)
        }

    def adicionar_servico(destino: dict, servico: str, dados: dict) -> None:
        chave = _integracoes_servico_key(servico)
        existente = destino.get(chave)
        if existente is not None:
            if assinatura_servico(chave, existente) != assinatura_servico(
                chave,
                dados,
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "As configuracoes globais antigas contem duas contas "
                        "diferentes para a mesma integracao. Revise os dados "
                        "antes de importar."
                    ),
                )
            return
        destino[chave] = dados

    legado_global = _integracoes_ler_json(os.path.join(PASTA_INFO, "integracoes.json"), {})
    if isinstance(legado_global, dict):
        for nome_loja, integracoes in legado_global.items():
            if not isinstance(integracoes, dict):
                continue
            destino = destino_para(nome_loja)
            if destino is None:
                continue
            for servico, cfg in integracoes.items():
                convertido = _integracoes_converter_legado(servico, cfg)
                if not convertido:
                    continue
                # Fontes globais nao possuem identidade de conta. Nunca combine
                # campos de dois blocos OAuth que podem pertencer a contas
                # diferentes; a primeira fonte inteira permanece autoritativa.
                adicionar_servico(destino, servico, convertido)

    bling_conf = _integracoes_ler_json(os.path.join(PASTA_INFO, "bling_conf.json"), [])
    if isinstance(bling_conf, list):
        for item in bling_conf:
            if not isinstance(item, dict):
                continue
            nome_loja = str(
                _integracoes_alias_unico(item, "loja", "nome") or ""
            ).strip()
            if not nome_loja:
                continue
            token = _integracoes_alias_unico(item, "token", "access_token")
            api_key = _integracoes_alias_unico(item, "apikey", "api_key")
            if not _integracoes_valor_preenchido(token) and not _integracoes_valor_preenchido(api_key):
                continue
            destino = destino_para(nome_loja)
            if destino is None:
                continue
            dados_bling = {
                "access_token": token,
                "api_key": api_key,
                "legacy_source": "bling_conf.json",
                "connected": bool(token or api_key),
            }
            adicionar_servico(destino, "bling", dados_bling)

    return por_loja


def _integracoes_pode_criar_lojas_legadas(client_id) -> bool:
    client_norm = str(client_id or "default").strip().lower() or "default"
    return client_norm == "default"


def _integracoes_mesclar_legadas(client_id, lojas):
    if not isinstance(lojas, list):
        lojas = []
    # integracoes.json e bling_conf.json globais nao possuem ownership. Nomes
    # iguais nao provam que credenciais pertencem a um tenant real.
    if not _integracoes_pode_criar_lojas_legadas(client_id):
        return lojas, False

    legadas = _integracoes_coletar_legadas()
    if not legadas:
        return lojas, False

    tombstones = _integracoes_ler_tombstones_estrito(client_id)
    stores_excluidas = {
        str(item.get("store_id") or "").strip()
        for item in tombstones
        if str(item.get("type") or "").strip().lower() == "store"
        and str(item.get("store_id") or "").strip()
        and not str(item.get("restored_at") or "").strip()
    }
    integracoes_excluidas = {
        (
            str(item.get("store_id") or "").strip(),
            _integracoes_servico_key(item.get("service")),
        )
        for item in tombstones
        if str(item.get("type") or "").strip().lower() == "integration"
        and str(item.get("store_id") or "").strip()
        and _integracoes_servico_key(item.get("service"))
        and not str(item.get("restored_at") or "").strip()
    }

    mudou = False
    indice = {
        _integracoes_nome_normalizado(loja.get("nome")): loja
        for loja in lojas
        if isinstance(loja, dict) and loja.get("nome")
    }

    for nome_legado, integracoes_legadas in legadas.items():
        chave = _integracoes_nome_normalizado(nome_legado)
        loja = indice.get(chave)
        if (
            isinstance(loja, dict)
            and str(loja.get("nome") or "").strip()
            != str(nome_legado or "").strip()
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Uma configuracao antiga usa um nome ambiguo para uma loja "
                    "ja cadastrada. Revise as lojas antes de importar credenciais."
                ),
            )
        pode_criar = _integracoes_pode_criar_lojas_legadas(client_id)
        store_id = _integracoes_store_id(
            client_id,
            loja if isinstance(loja, dict) else {"nome": nome_legado},
        )
        if store_id in stores_excluidas:
            continue
        if not loja and pode_criar:
            loja = {"nome": nome_legado, "integracoes": {}}
            lojas.append(loja)
            indice[chave] = loja
            mudou = True
        if not loja:
            continue

        integracoes_atuais = loja.setdefault("integracoes", {})
        servicos_presentes = {
            _integracoes_servico_key(servico)
            for servico in integracoes_atuais
        }
        for servico, dados_legados in (integracoes_legadas or {}).items():
            servico_key = _integracoes_servico_key(servico)
            if (
                not servico_key
                or servico_key in servicos_presentes
                or (store_id, servico_key) in integracoes_excluidas
            ):
                continue
            integracoes_atuais[servico_key] = json.loads(
                json.dumps(dados_legados, ensure_ascii=False)
            )
            servicos_presentes.add(servico_key)
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


def _integracoes_lojas_bytes_pertencem_ao_cliente(
    client_id: str,
    legacy_bytes: bytes,
) -> bool:
    """Valida ownership do antigo lojas_config.json global antes da migracao."""
    try:
        lojas = json.loads((legacy_bytes or b"").decode("utf-8-sig"))
    except Exception:
        return False
    if not isinstance(lojas, list):
        return False

    client_norm = str(client_id or "default").strip().lower() or "default"
    possui_ids: list[bool] = []
    for loja in lojas:
        if not isinstance(loja, dict):
            return False
        nome = str(loja.get("nome") or "").strip()
        store_id = str(loja.get("store_id") or "").strip()
        if not nome:
            return False
        possui_ids.append(bool(store_id))

    # O tenant default pode receber somente o formato realmente antigo, no
    # qual nenhuma loja tinha store_id. Se algum ID duravel apareceu, ele passa
    # a ser prova de ownership e todos precisam pertencer ao tenant solicitado.
    if client_norm == "default" and not any(possui_ids):
        return True
    if not all(possui_ids):
        return False

    for loja in lojas:
        nome = str(loja.get("nome") or "").strip()
        store_id = str(loja.get("store_id") or "").strip()
        esperado = _integracoes_store_id(client_norm, {"nome": nome})
        if store_id != esperado:
            return False
    return True


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


def _integracoes_validar_tombstones_payload(payload: object) -> list[dict]:
    from backend.services.shared_sync_merge_integracoes import (
        _shared_sync_validar_tombstones,
    )

    _shared_sync_validar_tombstones(
        payload,
        origem="Historico local de exclusoes",
        status_code=409,
    )
    return payload


def _integracoes_ler_tombstones_estrito(client_id: str) -> list[dict]:
    path = _integracoes_tombstones_path(client_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8-sig") as arquivo:
            payload = json.load(arquivo)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "O historico local de exclusoes esta invalido. "
                "A operacao foi bloqueada para preservar as contas."
            ),
        ) from exc
    return _integracoes_validar_tombstones_payload(payload)


def _integracoes_tombstone_version(valor) -> int:
    try:
        return max(0, int(valor or 0))
    except (TypeError, ValueError):
        return 0


def _integracoes_atualizar_tombstone_payload(
    client_id: str,
    payload: list[dict],
    *,
    loja: dict,
    servico: str = "",
    tipo: str = "store",
    restaurar: bool = False,
) -> list[dict]:
    store_id = _integracoes_store_id(client_id, loja or {})
    servico_key = _integracoes_servico_key(servico)
    key = f"{tipo}:{store_id}:{servico_key}"
    versao_anterior = max(
        [
            _integracoes_tombstone_version((item or {}).get("version"))
            for item in payload
            if str((item or {}).get("key") or "") == key
        ]
        or [0]
    )
    atualizado = [
        json.loads(json.dumps(item, ensure_ascii=False))
        for item in payload
        if str((item or {}).get("key") or "") != key
    ]
    evento = {
        "key": key,
        "type": tipo,
        "store_id": store_id,
        "service": servico_key,
        "version": max(
            versao_anterior,
            _integracoes_tombstone_version((loja or {}).get("_sync_version")),
        ) + 1,
    }
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if restaurar:
        evento["restored_at"] = agora
    else:
        evento["deleted_at"] = agora
    atualizado.append(evento)
    return atualizado


def registrar_tombstone_integracao(client_id: str, *, loja: dict, servico: str = "", tipo: str = "store") -> None:
    with _LOJAS_CONFIG_LOCK:
        path = _integracoes_tombstones_path(client_id)
        payload = _integracoes_ler_tombstones_estrito(client_id)
        payload = _integracoes_atualizar_tombstone_payload(
            client_id,
            payload,
            loja=loja,
            servico=servico,
            tipo=tipo,
        )
        _integracoes_validar_tombstones_payload(payload)
        _integracoes_escrever_lojas_config_atomico(path, payload)


def restaurar_tombstone_integracao(
    client_id: str,
    *,
    loja: dict,
    servico: str = "",
    tipo: str = "store",
) -> None:
    """Marca uma exclusao anterior como explicitamente recriada/reconectada."""
    with _LOJAS_CONFIG_LOCK:
        path = _integracoes_tombstones_path(client_id)
        payload = _integracoes_ler_tombstones_estrito(client_id)
        payload = _integracoes_atualizar_tombstone_payload(
            client_id,
            payload,
            loja=loja,
            servico=servico,
            tipo=tipo,
            restaurar=True,
        )
        _integracoes_validar_tombstones_payload(payload)
        _integracoes_escrever_lojas_config_atomico(path, payload)


def _integracoes_transacao_pendente_path(client_id: str) -> str:
    return os.path.join(
        _tenant_path(client_id),
        "_shared_sync_backups",
        "pending_lojas_transaction.json",
    )


def _integracoes_validar_transacao_payload(payload: object) -> tuple[list, list]:
    try:
        schema = int(payload.get("schema") or 0) if isinstance(payload, dict) else 0
    except (TypeError, ValueError):
        schema = 0
    if not isinstance(payload, dict) or schema != 1:
        raise HTTPException(
            status_code=409,
            detail="Uma transacao pendente de lojas esta invalida.",
        )
    lojas = payload.get("lojas")
    tombstones = payload.get("tombstones")
    if not isinstance(lojas, list) or not isinstance(tombstones, list):
        raise HTTPException(
            status_code=409,
            detail="Uma transacao pendente de lojas esta incompleta.",
        )
    _integracoes_validar_tombstones_payload(tombstones)
    _integracoes_validar_identidades_lojas_local(lojas)
    return lojas, tombstones


def _integracoes_finalizar_transacao_pendente(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        # O replay e idempotente; manter o journal e mais seguro do que
        # declarar falha depois de os dois arquivos ja terem sido publicados.
        logger.warning(
            "[INTEGRACOES] Transacao concluida sera revalidada na proxima "
            "carga: %s",
            type(exc).__name__,
        )


def _integracoes_hash_arquivo(path: str) -> str:
    try:
        with open(path, "rb") as arquivo:
            return hashlib.sha256(arquivo.read()).hexdigest()
    except OSError:
        return ""


def _integracoes_marcar_transacao_concluida(
    client_id: str,
    journal_path: str,
    journal: dict,
) -> None:
    concluida = dict(journal)
    concluida["committed"] = True
    concluida["main_sha256"] = _integracoes_hash_arquivo(
        os.path.join(_tenant_path(client_id), "lojas_config.json")
    )
    concluida["tombstones_sha256"] = _integracoes_hash_arquivo(
        _integracoes_tombstones_path(client_id)
    )
    _integracoes_escrever_lojas_config_atomico(journal_path, concluida)


def _integracoes_abortar_transacao_pendente(client_id: str) -> None:
    journal_path = _integracoes_transacao_pendente_path(client_id)
    if not os.path.exists(journal_path):
        return
    try:
        with open(journal_path, "r", encoding="utf-8-sig") as arquivo:
            payload = json.load(arquivo)
        if not isinstance(payload, dict):
            payload = {"schema": 1, "lojas": [], "tombstones": []}
        payload["aborted"] = True
        _integracoes_escrever_lojas_config_atomico(journal_path, payload)
        _integracoes_finalizar_transacao_pendente(journal_path)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Nao foi possivel invalidar a transacao local interrompida.",
        ) from exc


def _integracoes_commit_lojas_tombstones(
    client_id: str,
    lojas: list,
    tombstones: list[dict],
    *,
    permitir_reducao_confirmada: bool = False,
) -> None:
    """Publica lojas+tombstones com journal recuperavel entre os replaces."""

    with _LOJAS_CONFIG_LOCK:
        _integracoes_validar_identidades_lojas_local(lojas)
        _integracoes_validar_tombstones_payload(tombstones)
        _integracoes_isolar_backup_invalido_se_necessario(client_id)
        journal_path = _integracoes_transacao_pendente_path(client_id)
        journal = {
            "schema": 1,
            "permitir_reducao_confirmada": bool(permitir_reducao_confirmada),
            "lojas": json.loads(json.dumps(lojas, ensure_ascii=False)),
            "tombstones": json.loads(json.dumps(tombstones, ensure_ascii=False)),
        }
        _integracoes_escrever_lojas_config_atomico(journal_path, journal)
        _integracoes_escrever_lojas_config_atomico(
            _integracoes_tombstones_path(client_id),
            journal["tombstones"],
        )
        salvar_lojas(
            client_id,
            journal["lojas"],
            permitir_reducao_confirmada=permitir_reducao_confirmada,
            espelhar_backup_final=True,
        )
        _integracoes_marcar_transacao_concluida(
            client_id,
            journal_path,
            journal,
        )
        _integracoes_finalizar_transacao_pendente(journal_path)


def _integracoes_recuperar_transacao_pendente(client_id: str) -> None:
    journal_path = _integracoes_transacao_pendente_path(client_id)
    if not os.path.exists(journal_path):
        return
    try:
        with open(journal_path, "r", encoding="utf-8-sig") as arquivo:
            payload = json.load(arquivo)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Uma transacao pendente de lojas nao pôde ser validada. "
                "A carga foi bloqueada para preservar as contas."
            ),
        ) from exc
    if isinstance(payload, dict) and bool(payload.get("aborted")):
        _integracoes_finalizar_transacao_pendente(journal_path)
        return
    lojas, tombstones = _integracoes_validar_transacao_payload(payload)
    if bool(payload.get("committed")):
        main_atual = _integracoes_hash_arquivo(
            os.path.join(_tenant_path(client_id), "lojas_config.json")
        )
        tombstones_atuais = _integracoes_hash_arquivo(
            _integracoes_tombstones_path(client_id)
        )
        if (
            main_atual != str(payload.get("main_sha256") or "")
            or tombstones_atuais
            != str(payload.get("tombstones_sha256") or "")
        ):
            logger.warning(
                "[INTEGRACOES] Journal concluido ficou obsoleto e nao sera "
                "reaplicado sobre alteracoes mais novas."
            )
        _integracoes_finalizar_transacao_pendente(journal_path)
        return
    _integracoes_isolar_backup_invalido_se_necessario(client_id)
    _integracoes_escrever_lojas_config_atomico(
        _integracoes_tombstones_path(client_id),
        tombstones,
    )
    salvar_lojas(
        client_id,
        lojas,
        permitir_reducao_confirmada=bool(
            payload.get("permitir_reducao_confirmada")
        ),
        espelhar_backup_final=True,
    )
    _integracoes_marcar_transacao_concluida(
        client_id,
        journal_path,
        payload,
    )
    _integracoes_finalizar_transacao_pendente(journal_path)
    logger.warning(
        "[INTEGRACOES] Transacao interrompida de lojas concluida com seguranca."
    )


def _integracoes_recuperar_backup_imediato(
    client_id: str,
    arquivo_lojas: str,
    lojas: list,
) -> tuple[list, bool]:
    """Recupera dados tenant-attributed do .bak sem substituir o estado atual."""
    backup_path = _integracoes_backup_imediato_lojas(arquivo_lojas)
    if not os.path.exists(backup_path):
        return lojas, False
    try:
        with open(backup_path, "rb") as arquivo:
            backup_bytes = arquivo.read()
        tombstones_path = _integracoes_tombstones_path(client_id)
        tombstones_bytes = None
        if os.path.exists(tombstones_path):
            with open(tombstones_path, "rb") as arquivo:
                tombstones_bytes = arquivo.read()
        from backend.services.shared_sync_merge_integracoes import (
            _shared_sync_recuperar_backup_lojas_integracoes_bytes,
        )

        current_bytes = json.dumps(
            lojas,
            ensure_ascii=False,
            indent=4,
        ).encode("utf-8")
        merged_bytes = _shared_sync_recuperar_backup_lojas_integracoes_bytes(
            current_bytes,
            backup_bytes,
            client_id=client_id,
            local_tombstones_bytes=tombstones_bytes,
        )
        merged = json.loads(merged_bytes.decode("utf-8"))
        if not isinstance(merged, list):
            raise ValueError("backup nao contem lista de lojas")
        return merged, merged != lojas
    except HTTPException as exc:
        logger.warning(
            "[INTEGRACOES] Backup imediato preservado; recuperacao bloqueada: %s",
            exc.detail,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "O backup local de lojas diverge da configuracao atual. "
                "A gravacao foi bloqueada para preservar as contas."
            ),
        ) from exc
    except Exception as exc:
        logger.warning(
            "[INTEGRACOES] Backup imediato preservado; recuperacao invalida: %s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "O backup local de lojas nao pôde ser validado. "
                "A gravacao foi bloqueada para preservar as contas."
            ),
        ) from exc


def _integracoes_aplicar_tombstones_ativos(
    client_id: str,
    lojas: list,
) -> tuple[list, bool]:
    """Remove do estado carregado registros ainda marcados como excluidos."""
    tombstones_path = _integracoes_tombstones_path(client_id)
    if not os.path.exists(tombstones_path):
        return lojas, False
    try:
        with open(tombstones_path, "r", encoding="utf-8-sig") as arquivo:
            payload = json.load(arquivo)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "O historico local de exclusoes de lojas esta invalido. "
                "A gravacao foi bloqueada para preservar as contas."
            ),
        ) from exc
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "O historico local de exclusoes de lojas esta invalido. "
                "A gravacao foi bloqueada para preservar as contas."
            ),
        )
    _integracoes_validar_tombstones_payload(payload)

    stores_excluidas = {
        str(item.get("store_id") or "").strip()
        for item in payload
        if str(item.get("type") or "").strip().lower() == "store"
        and str(item.get("store_id") or "").strip()
        and not str(item.get("restored_at") or "").strip()
    }
    integracoes_excluidas = {
        (
            str(item.get("store_id") or "").strip(),
            _integracoes_servico_key(item.get("service")),
        )
        for item in payload
        if str(item.get("type") or "").strip().lower() == "integration"
        and str(item.get("store_id") or "").strip()
        and _integracoes_servico_key(item.get("service"))
        and not str(item.get("restored_at") or "").strip()
    }
    filtradas = []
    mudou = False
    for loja in lojas if isinstance(lojas, list) else []:
        if not isinstance(loja, dict):
            filtradas.append(loja)
            continue
        store_id = _integracoes_store_id(client_id, loja)
        if store_id in stores_excluidas:
            mudou = True
            continue
        copia = json.loads(json.dumps(loja, ensure_ascii=False))
        integracoes_loja = (
            copia.get("integracoes")
            if isinstance(copia.get("integracoes"), dict)
            else {}
        )
        integracoes_filtradas = {
            servico: dados
            for servico, dados in integracoes_loja.items()
            if (store_id, _integracoes_servico_key(servico))
            not in integracoes_excluidas
        }
        if len(integracoes_filtradas) != len(integracoes_loja):
            copia["integracoes"] = integracoes_filtradas
            mudou = True
        filtradas.append(copia)
    return filtradas, mudou


def _integracoes_contradicao_tombstones_lojas(
    client_id: str,
    lojas: list,
) -> str:
    payload = _integracoes_ler_tombstones_estrito(client_id)
    stores_ativos = {
        str(item.get("store_id") or "").strip()
        for item in payload
        if str(item.get("type") or "").strip().lower() == "store"
        and str(item.get("store_id") or "").strip()
        and not str(item.get("restored_at") or "").strip()
    }
    integracoes_ativas = {
        (
            str(item.get("store_id") or "").strip(),
            _integracoes_servico_key(item.get("service")),
        )
        for item in payload
        if str(item.get("type") or "").strip().lower() == "integration"
        and str(item.get("store_id") or "").strip()
        and _integracoes_servico_key(item.get("service"))
        and not str(item.get("restored_at") or "").strip()
    }
    ignoradas = {
        "connected",
        "status",
        "motivo",
        "oauth_invalid",
        "shared_without_oauth_tokens",
        "_sync_version",
        "_sync_updated_at",
        "updated_at",
    } | _INTEGRACOES_SYNC_TRANSIENT_KEYS
    for loja in lojas if isinstance(lojas, list) else []:
        if not isinstance(loja, dict):
            continue
        store_id = _integracoes_store_id(client_id, loja)
        if store_id in stores_ativos:
            return "store"
        integracoes = (
            loja.get("integracoes")
            if isinstance(loja.get("integracoes"), dict)
            else {}
        )
        for servico, dados in integracoes.items():
            if (store_id, _integracoes_servico_key(servico)) not in integracoes_ativas:
                continue
            contradiz = bool(
                isinstance(dados, dict)
                and (
                    bool(dados.get("connected"))
                    or any(
                        str(chave or "").strip().lower() not in ignoradas
                        and _integracoes_valor_preenchido(valor)
                        for chave, valor in dados.items()
                    )
                )
            )
            if contradiz:
                return "integration"
    return ""


def _integracoes_validar_tombstones_contra_lojas_para_envio(
    client_id: str,
    lojas: list,
) -> None:
    contradicao = _integracoes_contradicao_tombstones_lojas(client_id, lojas)
    if contradicao == "store":
        raise HTTPException(
            status_code=409,
            detail=(
                "Uma loja local ainda possui uma exclusao pendente. "
                "Reconecte ou exclua a loja antes de sincronizar."
            ),
        )
    if contradicao == "integration":
        raise HTTPException(
            status_code=409,
            detail=(
                "Uma integracao local ainda possui uma exclusao pendente. "
                "Reconecte ou desconecte novamente antes de sincronizar."
            ),
        )


def _integracoes_validar_estado_atual_para_envio(client_id: str) -> None:
    """Preflight puro: bloqueia contradicoes antes de qualquer normalizacao."""
    arquivo_lojas = os.path.join(_tenant_path(client_id), "lojas_config.json")
    if not os.path.exists(arquivo_lojas):
        return
    try:
        lojas = _integracoes_ler_lojas_config_arquivo(arquivo_lojas)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "A configuracao local de lojas esta invalida. "
                "O envio foi bloqueado para preservar as contas."
            ),
        ) from exc
    _integracoes_validar_identidades_lojas_local(lojas)
    _integracoes_validar_tombstones_contra_lojas_para_envio(client_id, lojas)


def _integracoes_validar_identidades_lojas_local(lojas: list) -> None:
    nomes: set[str] = set()
    store_ids: set[str] = set()
    for loja in lojas if isinstance(lojas, list) else []:
        if not isinstance(loja, dict):
            raise HTTPException(
                status_code=409,
                detail="A configuracao local contem uma loja invalida.",
            )
        nome_key = _integracoes_nome_normalizado(loja.get("nome"))
        if not nome_key or nome_key in nomes:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A configuracao local contem nomes de loja ambiguos ou "
                    "duplicados. Revise as lojas antes de sincronizar."
                ),
            )
        nomes.add(nome_key)
        store_id = str(loja.get("store_id") or "").strip()
        if store_id:
            if store_id in store_ids:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A configuracao local contem identidades de loja "
                        "duplicadas. Revise as lojas antes de sincronizar."
                    ),
                )
            store_ids.add(store_id)
        integracoes_loja = (
            loja.get("integracoes")
            if isinstance(loja.get("integracoes"), dict)
            else {}
        )
        servicos_vistos: set[str] = set()
        for servico, dados in integracoes_loja.items():
            if not isinstance(dados, dict):
                raise HTTPException(
                    status_code=409,
                    detail="A configuracao local contem integracao invalida.",
                )
            servico_key = _integracoes_servico_key(servico)
            if not servico_key or servico_key in servicos_vistos:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A configuracao local contem aliases duplicados ou "
                        "invalidos para uma integracao."
                    ),
                )
            servicos_vistos.add(servico_key)
            if servico_key == "mercadolivre":
                _integracoes_alias_unico(dados, "app_id", "client_id", "id")
                _integracoes_alias_unico(
                    dados,
                    "client_secret",
                    "secret_key",
                    "secret",
                )
            elif servico_key == "bling":
                _integracoes_alias_unico(dados, "id", "client_id", "app_id")
                _integracoes_alias_unico(
                    dados,
                    "secret",
                    "client_secret",
                    "secret_key",
                )
                _integracoes_alias_unico(dados, "access_token", "token")
                _integracoes_alias_unico(dados, "api_key", "apikey")
            elif servico_key == "mercadoturbo":
                _integracoes_alias_unico(dados, "token", "access_token")


def _integracoes_recuperar_legado_global_coexistente(
    client_id: str,
    arquivo_lojas: str,
    lojas: list,
) -> tuple[list, bool, bool]:
    legado_path = str(ARQUIVO_LOJAS or "").strip()
    if (
        not legado_path
        or os.path.abspath(legado_path) == os.path.abspath(arquivo_lojas)
        or not os.path.exists(legado_path)
        or not os.path.exists(arquivo_lojas)
    ):
        return lojas, False, False
    try:
        with open(legado_path, "rb") as arquivo:
            legado_bytes = arquivo.read()
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail="Nao foi possivel validar a configuracao global antiga de lojas.",
        ) from exc
    if not _integracoes_lojas_bytes_pertencem_ao_cliente(
        client_id,
        legado_bytes,
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Existe uma configuracao global antiga sem vinculo comprovado "
                "com este cliente. A recuperacao foi bloqueada."
            ),
        )

    tombstones_bytes = None
    tombstones_path = _integracoes_tombstones_path(client_id)
    if os.path.exists(tombstones_path):
        with open(tombstones_path, "rb") as arquivo:
            tombstones_bytes = arquivo.read()
    from backend.services.shared_sync_merge_integracoes import (
        _shared_sync_recuperar_backup_lojas_integracoes_bytes,
    )

    current_bytes = json.dumps(
        lojas,
        ensure_ascii=False,
        indent=4,
    ).encode("utf-8")
    merged_bytes = _shared_sync_recuperar_backup_lojas_integracoes_bytes(
        current_bytes,
        legado_bytes,
        client_id=client_id,
        local_tombstones_bytes=tombstones_bytes,
    )
    merged = json.loads(merged_bytes.decode("utf-8"))
    return merged, merged != lojas, True


def _integracoes_arquivar_legado_global_coexistente(
    arquivo_lojas: str,
) -> None:
    legado_path = str(ARQUIVO_LOJAS or "").strip()
    if (
        not legado_path
        or not os.path.exists(legado_path)
        or os.path.abspath(legado_path) == os.path.abspath(arquivo_lojas)
    ):
        return
    try:
        backup_dir = os.path.join(
            os.path.dirname(arquivo_lojas),
            "_shared_sync_backups",
            f"legacy_recovery_{int(time.time() * 1000)}",
        )
        os.makedirs(backup_dir, exist_ok=True)
        shutil.move(
            legado_path,
            os.path.join(backup_dir, "legacy_root_lojas_config.json"),
        )
    except Exception as exc:
        logger.warning(
            "[INTEGRACOES] Lojas globais recuperadas, mas o legado nao pôde "
            "ser arquivado: %s",
            type(exc).__name__,
        )


def carregar_lojas(client_id: str):
    """Carrega as lojas do cliente do arquivo JSON."""
    with _LOJAS_CONFIG_LOCK:
        _integracoes_recuperar_transacao_pendente(client_id)
        destino_esperado = os.path.join(
            _tenant_path(client_id),
            "lojas_config.json",
        )
        tenant_ja_existia = os.path.exists(destino_esperado)
        legado_distinto_existia = bool(
            ARQUIVO_LOJAS
            and os.path.abspath(ARQUIVO_LOJAS)
            != os.path.abspath(destino_esperado)
            and os.path.exists(ARQUIVO_LOJAS)
        )
        arquivo_lojas = _integracoes_migrar_arquivo_legado_para_tenant(client_id, "lojas_config.json", ARQUIVO_LOJAS)
        migrou_legado_root = bool(
            not tenant_ja_existia
            and legado_distinto_existia
            and os.path.exists(arquivo_lojas)
        )
        arquivo_existe = os.path.exists(arquivo_lojas)
        backup_existe = os.path.exists(
            _integracoes_backup_imediato_lojas(arquivo_lojas)
        )
        lojas = []
        restaurou_backup_arquivo = False
        principal_valido = False
        if arquivo_existe:
            try:
                lojas = _integracoes_ler_lojas_config_arquivo(arquivo_lojas)
                _integracoes_validar_identidades_lojas_local(lojas)
                principal_valido = True
            except HTTPException:
                raise
            except Exception as e:
                lojas = _integracoes_restaurar_backup_imediato(arquivo_lojas, e)
                if lojas is None:
                    logger.error("Erro ao carregar lojas do cliente %s: %s", client_id, e)
                    raise HTTPException(
                        status_code=500,
                        detail="Configuracao de lojas invalida; gravacao bloqueada para preservar integracoes.",
                    )
                restaurou_backup_arquivo = True
        backup_invalido_isolado = False
        if principal_valido and backup_existe:
            backup_path = _integracoes_backup_imediato_lojas(arquivo_lojas)
            try:
                backup_validado = _integracoes_ler_lojas_config_arquivo(
                    backup_path
                )
                _integracoes_validar_identidades_lojas_local(backup_validado)
            except Exception:
                # O .bak e auxiliar. Se o principal esta integro, um backup
                # corrompido e isolado e refeito sem bloquear a aplicacao.
                _integracoes_quarentenar_backup_invalido(arquivo_lojas)
                backup_existe = False
                backup_invalido_isolado = True
        mudou_backup = False
        if backup_existe:
            lojas, mudou_backup = _integracoes_recuperar_backup_imediato(
                client_id,
                arquivo_lojas,
                lojas,
            )
        _integracoes_validar_identidades_lojas_local(lojas)
        lojas, mudou_legado_root, legado_root_validado = (
            _integracoes_recuperar_legado_global_coexistente(
                client_id,
                arquivo_lojas,
                lojas,
            )
        )
        fonte_recuperada_de_backup = bool(
            restaurou_backup_arquivo
            or (not arquivo_existe and backup_existe)
        )
        mudou_tombstones = False
        if migrou_legado_root or fonte_recuperada_de_backup:
            # Fontes de recuperacao nunca podem ressuscitar uma exclusao. O
            # snapshot principal saudavel, por outro lado, nao e apagado por
            # inferencia: operacoes pareadas usam o journal acima e qualquer
            # contradicao restante falha fechado no preflight de sincronizacao.
            lojas, mudou_tombstones = _integracoes_aplicar_tombstones_ativos(
                client_id,
                lojas,
            )
        lojas, mudou = _integracoes_mesclar_legadas(client_id, lojas)
        mudou = (
            mudou
            or mudou_backup
            or mudou_legado_root
            or mudou_tombstones
            or migrou_legado_root
            or backup_invalido_isolado
            or fonte_recuperada_de_backup
        )
        lojas, mudou_oauth = _integracoes_normalizar_oauth_compartilhado_lojas(lojas)
        mudou = mudou or mudou_oauth
        lojas, mudou_sync = _integracoes_normalizar_sync_metadata(client_id, lojas, lojas)
        mudou = mudou or mudou_sync
        contradicao_pendente = _integracoes_contradicao_tombstones_lojas(
            client_id,
            lojas,
        )
        if mudou and not contradicao_pendente:
            salvar_lojas(
                client_id,
                lojas,
                permitir_reducao_confirmada=mudou_tombstones,
                espelhar_backup_final=(
                    mudou_backup
                    or mudou_legado_root
                    or mudou_tombstones
                    or fonte_recuperada_de_backup
                    or migrou_legado_root
                    or backup_invalido_isolado
                ),
            )
        if legado_root_validado and os.path.exists(arquivo_lojas):
            _integracoes_arquivar_legado_global_coexistente(arquivo_lojas)
        return lojas


def salvar_lojas(
    client_id: str,
    lojas: list,
    *,
    permitir_reducao_confirmada: bool = False,
    espelhar_backup_final: bool = False,
):
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
            _integracoes_validar_identidades_lojas_local(lojas)
            _integracoes_validar_tombstones_contra_lojas_para_envio(
                client_id,
                lojas,
            )
            _integracoes_validar_regressao_lojas(arquivo_lojas, lojas, permitir_reducao_confirmada)
            if not espelhar_backup_final:
                _integracoes_salvar_backup_imediato(client_id, arquivo_lojas)
            _integracoes_escrever_lojas_config_atomico(arquivo_lojas, lojas)
            # Depois do replace atomico bem-sucedido, o backup acompanha o
            # snapshot final. Ele conserva somente um bloco OAuth mais rico da
            # mesma conta; nao deixa para tras lojas/servicos ja removidos nem
            # perde uma atualizacao concluida em outra loja.
            _integracoes_espelhar_backup_final_seguro(
                client_id,
                arquivo_lojas,
                lojas,
            )
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
        loja_criada = loja is None
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
        api_key = _integracoes_servico_key(api_nome)
        dados_dict = dados_api if isinstance(dados_api, dict) else {}
        reativou_integracao = bool(dados_dict.get("connected")) or any(
            _integracoes_valor_preenchido(dados_dict.get(chave))
            for chave in (
                "access_token",
                "refresh_token",
                "app_id",
                "client_id",
                "id",
                "client_secret",
                "secret",
                "token",
            )
        )
        precisa_restaurar = bool(
            loja_criada or api_key == "criacao" or reativou_integracao
        )
        if precisa_restaurar:
            tombstones = _integracoes_ler_tombstones_estrito(client_id)
            tombstones = _integracoes_atualizar_tombstone_payload(
                client_id,
                tombstones,
                loja=loja,
                tipo="store",
                restaurar=True,
            )
            if api_key != "criacao" and reativou_integracao:
                tombstones = _integracoes_atualizar_tombstone_payload(
                    client_id,
                    tombstones,
                    loja=loja,
                    servico=api_key,
                    tipo="integration",
                    restaurar=True,
                )
            _integracoes_commit_lojas_tombstones(
                client_id,
                lojas,
                tombstones,
            )
        else:
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
            tombstones = _integracoes_atualizar_tombstone_payload(
                client_id,
                _integracoes_ler_tombstones_estrito(client_id),
                loja=loja,
                servico=api_nome,
                tipo="integration",
            )
            _integracoes_commit_lojas_tombstones(
                client_id,
                lojas,
                tombstones,
            )
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
    "restaurar_tombstone_integracao",
    "salvar_temp_auth",
    "ler_temp_auth",
    "consumir_temp_auth",
    "limpar_temp_auth",
    "auth_bling_get_link",
    "auth_bling_exchange",
    "auth_ml_get_link",
    "auth_ml_exchange",
]
