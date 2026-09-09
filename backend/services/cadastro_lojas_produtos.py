"""Store-scoped Cadastro product persistence and legacy shadow reads.

The legacy ``cadastro_produtos.csv`` remains untouched.  New mutations are
stored in ``cadastro_produtos_lojas.csv`` with ``(store_id, sku_normalizado)``
as the durable key.  Store names are intentionally treated as display data;
identity always comes from ``lojas_config.json`` through the Integracoes
service.
"""

from __future__ import annotations

import csv
import hashlib
import io
import inspect
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional

from fastapi import Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from backend.schemas import (
    CadastroProdutoLojaAtualizacaoRequest,
    CadastroProdutoLojaRequest,
)
from backend.services import integracoes
from backend.services.cadastro_common import (
    CADASTRO_COLS_BASE,
    _normalizar_sku_mes,
    configure_cadastro_common_runtime,
)
from backend.services.cadastro_custos import (
    _cadastro_custos_lock,
    _cadastro_custos_lojas_path,
    _cadastro_salvar_custos_item_loja,
    configure_cadastro_custos_runtime,
)
from backend.services.cadastro_fotos import (
    _cadastro_caminhos_variantes_fotos_preparadas,
    _cadastro_fotos_bloquear_transicao,
    _cadastro_fotos_validar_preparadas_no_lock,
    _cadastro_foto_referencia_pertence_loja,
    _cadastro_foto_referencias_invalidas_loja,
    _cadastro_foto_store_id_referencia,
    _cadastro_mapa_fotos_locais,
    _cadastro_resolver_foto_local,
    _preparar_fotos_data_url_no_tenant,
    _remover_variantes_fotos_obsoletas,
    _salvar_foto_preparada_atomico,
    _validar_caminhos_variantes_fotos,
    configure_cadastro_fotos_runtime,
)
from backend.services.path_coordination import path_lock_for, path_locks_for
from backend.services.runtime_bridge import bind_runtime_globals


logger = logging.getLogger("jk_sistema")
_runtime_get_tenant_id = None


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    if not callable(_runtime_get_tenant_id):
        raise HTTPException(
            status_code=503,
            detail="Contexto de autenticacao do Cadastro ainda nao inicializado.",
        )
    resultado = _runtime_get_tenant_id(request, authorization)
    if inspect.isawaitable(resultado):
        return await resultado
    return resultado


def get_tenant_path(client_id: str):
    raise RuntimeError("Cadastro runtime was not configured.")


def _configure_runtime_globals(target_globals, runtime_module=None):
    runtime = bind_runtime_globals(target_globals, runtime_module)
    if runtime is not None:
        runtime_logger = getattr(runtime, "logger", None)
        if runtime_logger is not None:
            target_globals["logger"] = runtime_logger
        runtime_get_tenant_id = getattr(runtime, "get_tenant_id", None)
        if (
            callable(runtime_get_tenant_id)
            and runtime_get_tenant_id is not target_globals.get("get_tenant_id")
        ):
            target_globals["_runtime_get_tenant_id"] = runtime_get_tenant_id
        if hasattr(runtime, "get_tenant_path"):
            target_globals["get_tenant_path"] = getattr(runtime, "get_tenant_path")
    return runtime


def configure_cadastro_lojas_produtos_runtime(runtime_module=None):
    configure_cadastro_common_runtime(runtime_module)
    configure_cadastro_custos_runtime(runtime_module)
    configure_cadastro_fotos_runtime(runtime_module)
    return _configure_runtime_globals(globals(), runtime_module)


configure_cadastro_lojas_produtos_runtime()


CADASTRO_PRODUTOS_LOJAS_ARQUIVO = "cadastro_produtos_lojas.csv"
CADASTRO_PRODUTOS_LOJAS_COLUNAS = [
    "store_id",
    "sku",
    "sku_normalizado",
    "loja_sync",
    "row_version",
    "updated_at_utc",
    "deleted_at_utc",
]

_COLUNAS_IDENTIDADE = {
    "store_id",
    "sku_normalizado",
    "loja_sync",
    "row_version",
    "updated_at_utc",
    "deleted_at_utc",
    "scope_source",
}
_COLUNAS_DERIVADAS_COMPILADO = {
    "produto_bling",
    "nome_bling",
    "id_bling",
    "ncm_bling",
    "cest_bling",
    "monofasico",
    "monofasico_status",
    "monofasico_confianca",
    "monofasico_fundamento",
    "monofasico_fonte",
    "monofasico_motivo",
    "monofasico_verificado_em",
}
_COLUNAS_ENRIQUECIMENTO_IGNORADAS = _COLUNAS_IDENTIDADE | {"loja"}
_ARQUIVOS_LEGADOS = {
    "cadastro_base": "cadastro_produtos.csv",
    "custos_loja": "cadastro_custos_lojas.csv",
    "produtos_compilado": "produtos_compilado.csv",
}

def _agora_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalizar_sku_chave(valor: Any) -> str:
    return _normalizar_sku_mes(str(valor or "").strip()).upper()


def _texto_csv(valor: Any) -> str:
    if valor is None:
        return ""
    if isinstance(valor, (dict, list, tuple)):
        return json.dumps(valor, ensure_ascii=False, separators=(",", ":"))
    if isinstance(valor, bool):
        return "true" if valor else "false"
    return str(valor)


def _fingerprint_sombra_legada(sombra: dict[str, Any] | None) -> str:
    """Fingerprint legacy content so catalog previews detect concurrent edits."""

    if not isinstance(sombra, dict):
        return ""
    ignoradas = {"scope_source", "row_version", "updated_at_utc", "deleted_at_utc"}
    payload = {
        str(chave): _texto_csv(valor)
        for chave, valor in sombra.items()
        if str(chave) not in ignoradas
    }
    serializado = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serializado).hexdigest()


def _payload_dict(payload: Any, *, exclude_unset: bool = True) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    if hasattr(payload, "model_dump"):
        return dict(payload.model_dump(exclude_unset=exclude_unset))
    if hasattr(payload, "dict"):
        return dict(payload.dict(exclude_unset=exclude_unset))
    raise HTTPException(status_code=400, detail="Payload de produto invalido.")


def _cadastro_produtos_lojas_path(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), CADASTRO_PRODUTOS_LOJAS_ARQUIVO)


def _lock_arquivo(caminho: str):
    return path_lock_for(caminho)


@contextmanager
def _bloquear_config_e_arquivo_cadastro(client_id: str, caminho: str):
    """Canonical lock order for writers that also inspect store config."""

    with integracoes._LOJAS_CONFIG_LOCK:
        with _lock_arquivo(caminho):
            yield


def _detectar_delimitador(texto: str) -> str:
    amostra = texto[:8192]
    try:
        return csv.Sniffer().sniff(amostra, delimiters=",;\t").delimiter
    except csv.Error:
        primeira = amostra.splitlines()[0] if amostra.splitlines() else ""
        return ";" if primeira.count(";") > primeira.count(",") else ","


def _ler_csv_generico(caminho: str) -> tuple[list[dict[str, str]], list[str]]:
    if not os.path.exists(caminho):
        return [], []
    try:
        bruto = Path(caminho).read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Nao foi possivel ler o cadastro por loja.") from exc

    texto = ""
    ultimo_erro: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin1"):
        try:
            texto = bruto.decode(encoding)
            ultimo_erro = None
            break
        except UnicodeDecodeError as exc:
            ultimo_erro = exc
    if ultimo_erro is not None:
        raise HTTPException(status_code=500, detail="Codificacao invalida no cadastro por loja.")
    if not texto.strip():
        return [], []

    leitor = csv.DictReader(
        io.StringIO(texto, newline=""),
        delimiter=_detectar_delimitador(texto),
        strict=True,
    )
    colunas: list[str] = []
    for coluna in leitor.fieldnames or []:
        nome = str(coluna or "").strip().lower()
        if not nome or nome in colunas:
            raise HTTPException(
                status_code=500,
                detail="Cadastro por loja contem cabecalho vazio ou duplicado.",
            )
        colunas.append(nome)

    linhas: list[dict[str, str]] = []
    try:
        for linha in leitor:
            if None in (linha or {}):
                raise csv.Error("linha com campos excedentes")
            normalizada: dict[str, str] = {}
            for coluna, valor in (linha or {}).items():
                nome = str(coluna or "").strip().lower()
                if not nome or nome in normalizada:
                    raise csv.Error("linha com coluna invalida ou duplicada")
                normalizada[nome] = "" if valor is None else str(valor)
            if any(str(valor).strip() for valor in normalizada.values()):
                linhas.append(normalizada)
    except csv.Error as exc:
        raise HTTPException(
            status_code=500,
            detail="Cadastro por loja contem CSV malformado; nenhuma alteracao foi feita.",
        ) from exc
    return linhas, colunas


def _normalizar_registro_persistido(linha: dict[str, Any]) -> dict[str, str]:
    item = {
        str(chave or "").strip().lower(): _texto_csv(valor)
        for chave, valor in (linha or {}).items()
        if str(chave or "").strip()
    }
    item["store_id"] = str(item.get("store_id") or "").strip()
    sku = _normalizar_sku_mes(item.get("sku") or item.get("sku_normalizado") or "")
    item["sku"] = sku
    item["sku_normalizado"] = _normalizar_sku_chave(
        item.get("sku_normalizado") or sku
    )
    item["loja_sync"] = str(item.get("loja_sync") or "").strip()
    try:
        versao = max(1, int(str(item.get("row_version") or "1").strip()))
    except (TypeError, ValueError):
        versao = 1
    item["row_version"] = str(versao)
    item["updated_at_utc"] = str(item.get("updated_at_utc") or "").strip()
    item["deleted_at_utc"] = str(item.get("deleted_at_utc") or "").strip()
    return item


def _ler_registros_persistidos_caminho(
    caminho: os.PathLike[str] | str,
) -> tuple[list[dict[str, str]], list[str]]:
    linhas, colunas = _ler_csv_generico(os.fspath(caminho))
    registros = [_normalizar_registro_persistido(linha) for linha in linhas]
    chaves: set[tuple[str, str]] = set()
    for item in registros:
        chave = (item.get("store_id", ""), item.get("sku_normalizado", ""))
        if not all(chave):
            raise HTTPException(
                status_code=500,
                detail="Cadastro por loja contem registro sem store_id ou SKU.",
            )
        if chave in chaves:
            raise HTTPException(
                status_code=500,
                detail="Cadastro por loja contem chave duplicada.",
            )
        chaves.add(chave)
    return registros, colunas


def _ler_registros_persistidos(client_id: str) -> tuple[list[dict[str, str]], list[str]]:
    return _ler_registros_persistidos_caminho(
        _cadastro_produtos_lojas_path(client_id)
    )


def _ordenar_colunas(registros: Iterable[dict[str, Any]], extras: Iterable[str] = ()) -> list[str]:
    colunas = list(CADASTRO_PRODUTOS_LOJAS_COLUNAS)
    for coluna in extras:
        nome = str(coluna or "").strip().lower()
        if nome and nome not in colunas:
            colunas.append(nome)
    for item in registros:
        for coluna in item:
            nome = str(coluna or "").strip().lower()
            if nome and nome not in colunas:
                colunas.append(nome)
    return colunas


def _assinatura_registros(registros: Iterable[dict[str, Any]], colunas: Iterable[str]) -> list[tuple[str, ...]]:
    cols = list(colunas)
    return sorted(
        tuple(_texto_csv(item.get(coluna, "")) for coluna in cols)
        for item in registros
    )


def _salvar_registros_atomico_caminho(
    caminho: os.PathLike[str] | str,
    registros: list[dict[str, Any]],
    colunas_existentes: Iterable[str] = (),
) -> None:
    caminho = os.path.abspath(os.fspath(caminho))
    pasta = os.path.dirname(caminho)
    os.makedirs(pasta, exist_ok=True)
    normalizados = [_normalizar_registro_persistido(item) for item in registros]
    colunas = _ordenar_colunas(normalizados, colunas_existentes)
    temporario = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            prefix=".cadastro_produtos_lojas.",
            suffix=".tmp",
            dir=pasta,
            delete=False,
        ) as arquivo:
            temporario = arquivo.name
            escritor = csv.DictWriter(
                arquivo,
                fieldnames=colunas,
                extrasaction="ignore",
                lineterminator="\n",
            )
            escritor.writeheader()
            for item in normalizados:
                escritor.writerow({coluna: _texto_csv(item.get(coluna, "")) for coluna in colunas})
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, caminho)
        temporario = ""

        lidos, _ = _ler_registros_persistidos_caminho(caminho)
        if _assinatura_registros(lidos, colunas) != _assinatura_registros(normalizados, colunas):
            raise RuntimeError("readback_mismatch")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[CADASTRO LOJAS] Falha na gravacao atomica/readback: %s", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail="Nao foi possivel salvar o cadastro por loja com seguranca.",
        ) from exc
    finally:
        if temporario:
            try:
                os.unlink(temporario)
            except OSError:
                pass


def _salvar_registros_atomico(
    client_id: str,
    registros: list[dict[str, Any]],
    colunas_existentes: Iterable[str] = (),
) -> None:
    _salvar_registros_atomico_caminho(
        _cadastro_produtos_lojas_path(client_id),
        registros,
        colunas_existentes,
    )


def _capturar_estados_arquivos(caminhos: Iterable[str]) -> dict[str, tuple[bool, bytes]]:
    estados: dict[str, tuple[bool, bytes]] = {}
    for caminho_bruto in caminhos:
        caminho = os.path.abspath(str(caminho_bruto or ""))
        if not caminho or caminho in estados:
            continue
        if not os.path.exists(caminho):
            estados[caminho] = (False, b"")
            continue
        try:
            estados[caminho] = (True, Path(caminho).read_bytes())
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="Nao foi possivel preparar a transacao do cadastro por loja.",
            ) from exc
    return estados


def _restaurar_arquivo_atomico(caminho: str, existia: bool, conteudo: bytes) -> None:
    if not existia:
        try:
            os.unlink(caminho)
        except FileNotFoundError:
            pass
        return

    pasta = os.path.dirname(caminho)
    os.makedirs(pasta, exist_ok=True)
    temporario = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{os.path.basename(caminho)}.rollback.",
            suffix=".tmp",
            dir=pasta,
            delete=False,
        ) as arquivo:
            temporario = arquivo.name
            arquivo.write(conteudo)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, caminho)
        temporario = ""
    finally:
        if temporario:
            try:
                os.unlink(temporario)
            except OSError:
                pass


def _rollback_arquivos(estados: dict[str, tuple[bool, bytes]]) -> None:
    falhas: list[str] = []
    for caminho, (existia, conteudo) in reversed(list(estados.items())):
        try:
            _restaurar_arquivo_atomico(caminho, existia, conteudo)
        except Exception as exc:
            falhas.append(f"{os.path.basename(caminho)}:{type(exc).__name__}")
    if falhas:
        logger.critical(
            "[CADASTRO LOJAS] Rollback incompleto da transacao: %s",
            ",".join(falhas),
        )
        raise HTTPException(
            status_code=500,
            detail="Falha critica ao reverter a alteracao do cadastro por loja.",
        )


def _cadastro_propagar_referencias_fotos_preparadas(
    registros: list[dict[str, Any]],
    preparadas: list[dict[str, Any]],
    *,
    registrar_evento: bool = False,
    store_id_origem: str = "",
) -> int:
    """Mantem as referencias CSV coerentes com o fan-out fisico compartilhado."""

    grupos: dict[tuple[str, str, str], dict[str, str]] = {}
    for item in preparadas:
        sku = _normalizar_sku_chave(item.get("sku") or "")
        store_id = str(item.get("store_id") or "").strip()
        relativo = str(item.get("relativo") or "").strip().replace("\\", "/")
        conteudo = item.get("conteudo")
        if not sku or not store_id or not relativo or not isinstance(conteudo, bytes):
            continue
        chave = (
            sku,
            os.path.basename(relativo).casefold(),
            hashlib.sha256(conteudo).hexdigest(),
        )
        grupos.setdefault(chave, {})[store_id] = relativo

    alterados = 0
    for (sku, _nome, _digest), referencias in grupos.items():
        membros = [
            registro
            for registro in registros
            if str(registro.get("store_id") or "").strip() in referencias
            and _normalizar_sku_chave(
                registro.get("sku_normalizado") or registro.get("sku") or ""
            )
            == sku
            and not str(registro.get("deleted_at_utc") or "").strip()
        ]
        versao_evento = max(
            (
                int(str(registro.get("row_version") or "0"))
                for registro in membros
                if str(registro.get("foto") or "").strip().replace("\\", "/")
                == referencias[str(registro.get("store_id") or "").strip()]
            ),
            default=0,
        )
        timestamp_evento = max(
            (
                str(registro.get("updated_at_utc") or "").strip()
                for registro in membros
                if str(registro.get("foto") or "").strip().replace("\\", "/")
                == referencias[str(registro.get("store_id") or "").strip()]
            ),
            default="",
        )
        for registro in membros:
            store_id = str(registro.get("store_id") or "").strip()
            desejada = referencias[store_id]
            atual = str(registro.get("foto") or "").strip().replace("\\", "/")
            propagar_evento_bytes = (
                registrar_evento
                and bool(store_id_origem)
                and store_id != str(store_id_origem).strip()
            )
            if atual == desejada and not propagar_evento_bytes:
                continue
            try:
                versao_atual = int(str(registro.get("row_version") or "0"))
            except (TypeError, ValueError):
                versao_atual = 0
            registro["foto"] = desejada
            registro["row_version"] = str(max(versao_atual + 1, versao_evento))
            registro["updated_at_utc"] = max(
                str(registro.get("updated_at_utc") or "").strip(),
                timestamp_evento,
            )
            alterados += 1
    return alterados


def _lojas_atuais(client_id: str) -> list[dict[str, Any]]:
    lojas = integracoes.carregar_lojas(client_id)
    return [dict(loja) for loja in (lojas or []) if isinstance(loja, dict)]


def resolver_loja_cadastro(client_id: str, store_id: str) -> dict[str, str]:
    """Resolve an exact store id through Integracoes; names never identify rows."""

    store_id_alvo = str(store_id or "").strip()
    if not store_id_alvo:
        raise HTTPException(status_code=400, detail="store_id e obrigatorio.")
    correspondentes = [
        loja
        for loja in _lojas_atuais(client_id)
        if str(loja.get("store_id") or "").strip() == store_id_alvo
    ]
    if len(correspondentes) == 1:
        loja = correspondentes[0]
        return {
            "store_id": store_id_alvo,
            "nome": str(loja.get("nome") or "").strip(),
        }
    if len(correspondentes) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "store_config_ambiguous",
                "message": "A identidade da loja esta duplicada na configuracao.",
            },
        )
    raise HTTPException(status_code=404, detail="Loja nao encontrada para este cliente.")


def _revalidar_loja_cadastro_para_commit(
    client_id: str,
    snapshot: dict[str, str],
) -> dict[str, str]:
    """Revalidate the exact store identity while the config lock is held."""

    try:
        atual = resolver_loja_cadastro(client_id, snapshot.get("store_id") or "")
    except HTTPException as exc:
        if exc.status_code not in {404, 409}:
            raise
        raise HTTPException(
            status_code=409,
            detail={
                "code": "store_config_changed",
                "message": "A configuracao da loja mudou durante a operacao.",
            },
        ) from exc
    if atual["nome"] != str(snapshot.get("nome") or "").strip():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "store_config_changed",
                "message": "A configuracao da loja mudou durante a operacao.",
            },
        )
    return atual


@contextmanager
def _bloquear_loja_cadastro_para_commit(
    client_id: str,
    snapshot: dict[str, str],
):
    """Hold config, catalog, costs and transition through commit/rollback."""

    with integracoes._LOJAS_CONFIG_LOCK:
        with _lock_arquivo(_cadastro_produtos_lojas_path(client_id)):
            with _cadastro_custos_lock(client_id):
                with _cadastro_fotos_bloquear_transicao(client_id):
                    loja = _revalidar_loja_cadastro_para_commit(
                        client_id,
                        snapshot,
                    )
                    yield loja


def _nome_loja_chave(valor: Any) -> str:
    """Normalize display names only for safe legacy-name matching."""

    return str(valor or "").strip().casefold()


def _aliases_nome_loja(loja: dict[str, Any]) -> list[Any]:
    """Return current and persisted historical display names for one store."""

    aliases: list[Any] = [loja.get("nome")]
    anteriores = loja.get("nomes_anteriores")
    if isinstance(anteriores, str):
        aliases.append(anteriores)
    elif isinstance(anteriores, (list, tuple, set)):
        aliases.extend(anteriores)
    return aliases


def _nomes_lojas_unicos(lojas: Iterable[dict[str, Any]]) -> tuple[dict[str, str], set[str]]:
    ids_por_nome: dict[str, str] = {}
    ambiguos: set[str] = set()
    for loja in lojas:
        store_id = str(loja.get("store_id") or "").strip()
        if not store_id:
            continue
        for nome in _aliases_nome_loja(loja):
            chave_nome = _nome_loja_chave(nome)
            if not chave_nome:
                continue
            if chave_nome in ids_por_nome and ids_por_nome[chave_nome] != store_id:
                ambiguos.add(chave_nome)
            else:
                ids_por_nome[chave_nome] = store_id
    for nome in ambiguos:
        ids_por_nome.pop(nome, None)
    return ids_por_nome, ambiguos


def _labels_loja(linha: dict[str, Any]) -> list[str]:
    bruto = linha.get("loja_sync")
    if bruto is None or not str(bruto).strip():
        bruto = linha.get("loja")
    if isinstance(bruto, list):
        valores = bruto
    else:
        valores = str(bruto or "").split("|")
    labels: list[str] = []
    for valor in valores:
        label = str(valor or "").strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _merge_preenchidos(destino: dict[str, str], origem: dict[str, Any]) -> None:
    for chave, valor in origem.items():
        nome = str(chave or "").strip().lower()
        if not nome or nome in _COLUNAS_ENRIQUECIMENTO_IGNORADAS:
            continue
        texto = _texto_csv(valor)
        if texto.strip() and not str(destino.get(nome) or "").strip():
            destino[nome] = texto


def _merge_sobrescrever_preenchidos(
    destino: dict[str, str], origem: dict[str, Any]
) -> set[str]:
    """Overlay store-proven legacy values while retaining generic defaults."""

    preenchidos: set[str] = set()
    for chave, valor in origem.items():
        nome = str(chave or "").strip().lower()
        if not nome or nome in _COLUNAS_ENRIQUECIMENTO_IGNORADAS:
            continue
        texto = _texto_csv(valor)
        if texto.strip():
            destino[nome] = texto
            preenchidos.add(nome)
    return preenchidos


def _aplicar_compilado(
    produto: dict[str, str],
    compilado: dict[str, Any],
    *,
    sobrescrever_aliases_legadas: bool = False,
    campos_explicitos_loja: set[str] | None = None,
) -> set[str]:
    antes = dict(produto)
    _merge_preenchidos(produto, compilado)
    derivados = {
        chave
        for chave, valor in produto.items()
        if str(valor or "").strip() and not str(antes.get(chave) or "").strip()
    }
    campos_explicitos = campos_explicitos_loja or set()
    aliases = {
        "produto_bling": ("produto_bling", "nome_bling"),
        "ncm": ("ncm", "ncm_bling"),
        "cest": ("cest", "cest_bling"),
        "id_bling": ("id_bling",),
    }
    for destino, fontes in aliases.items():
        if destino in campos_explicitos:
            continue
        if not sobrescrever_aliases_legadas and str(produto.get(destino) or "").strip():
            continue
        for fonte in fontes:
            valor = _texto_csv(compilado.get(fonte, ""))
            if valor.strip():
                produto[destino] = valor
                derivados.add(destino)
                break
    return derivados


def _contexto_legado_de_tenant(
    client_id: str,
    loja: dict[str, str],
    tenant: os.PathLike[str] | str,
    lojas: Iterable[dict[str, Any]],
    *,
    fotos: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the legacy shadow from explicit, already trusted inputs.

    The regular CRUD wrapper below supplies the runtime tenant and current
    stores.  Photo migration uses this pure variant while it already owns the
    tenant transition lock, avoiding any temporary mutation of runtime globals
    when more than one canonical data root is migrated together.
    """

    lojas = [dict(item) for item in lojas if isinstance(item, dict)]
    ids_por_nome, nomes_ambiguos = _nomes_lojas_unicos(lojas)
    store_ids_atuais = {
        str(item.get("store_id") or "").strip()
        for item in lojas
        if str(item.get("store_id") or "").strip()
    }
    tenant = os.fspath(tenant)
    fontes: dict[str, list[dict[str, str]]] = {}
    colunas_fontes: dict[str, list[str]] = {}
    for fonte, arquivo in _ARQUIVOS_LEGADOS.items():
        linhas, colunas = _ler_csv_generico(os.path.join(tenant, arquivo))
        fontes[fonte] = linhas
        colunas_fontes[fonte] = colunas

    associacoes: set[tuple[str, str]] = set()
    nao_mapeados: list[dict[str, str]] = []
    vistos_nao_mapeados: set[tuple[str, str, str, str]] = set()
    resumo = {
        fonte: {"arquivo": _ARQUIVOS_LEGADOS[fonte], "linhas": len(linhas), "associacoes_comprovadas": 0, "nao_mapeadas": 0}
        for fonte, linhas in fontes.items()
    }

    base_generica: dict[str, dict[str, str]] = {}
    base_por_loja: dict[str, dict[str, dict[str, str]]] = {}
    compilado_por_loja: dict[str, dict[str, dict[str, str]]] = {}
    custos_por_loja: dict[str, dict[str, dict[str, str]]] = {}
    prioridades_compilado: dict[tuple[str, str], int] = {}
    prioridades_custos: dict[tuple[str, str], int] = {}
    campos_derivados_sombras: dict[str, set[str]] = {}

    for fonte, linhas in fontes.items():
        for indice, linha in enumerate(linhas, start=2):
            sku = _normalizar_sku_chave(linha.get("sku") or "")
            labels = _labels_loja(linha)
            ids_linha: list[str] = []
            if not sku:
                chave_erro = (fonte, str(indice), "", "sku_ausente")
                if chave_erro not in vistos_nao_mapeados:
                    vistos_nao_mapeados.add(chave_erro)
                    nao_mapeados.append({
                        "fonte": fonte,
                        "linha": str(indice),
                        "sku": "",
                        "loja_sync": "|".join(labels),
                        "motivo": "sku_ausente",
                    })
                    resumo[fonte]["nao_mapeadas"] += 1
                continue

            # Any historical source that already carries a durable identity
            # must be resolved by that exact, case-sensitive store_id.  Its
            # display name may be stale or conflicting and can never remap the
            # row (nor turn it into a global fallback) after deletion.
            store_id_fonte = str(linha.get("store_id") or "").strip()
            if store_id_fonte:
                if store_id_fonte not in store_ids_atuais:
                    motivo = "store_id_nao_corresponde_a_loja_atual"
                    chave_erro = (fonte, sku, store_id_fonte, motivo)
                    if chave_erro not in vistos_nao_mapeados:
                        vistos_nao_mapeados.add(chave_erro)
                        nao_mapeados.append({
                            "fonte": fonte,
                            "linha": str(indice),
                            "sku": sku,
                            "store_id": store_id_fonte,
                            "loja_sync": "|".join(labels),
                            "motivo": motivo,
                        })
                        resumo[fonte]["nao_mapeadas"] += 1
                    continue
                ids_linha.append(store_id_fonte)
                associacoes.add((store_id_fonte, sku))
                resumo[fonte]["associacoes_comprovadas"] += 1

            if not labels:
                if ids_linha:
                    # Exact store_id above is already decisive.
                    pass
                else:
                    chave_erro = (fonte, sku, "", "sem_loja_comprovada")
                    if chave_erro not in vistos_nao_mapeados:
                        vistos_nao_mapeados.add(chave_erro)
                        nao_mapeados.append({
                            "fonte": fonte,
                            "linha": str(indice),
                            "sku": sku,
                            "loja_sync": "",
                            "motivo": "sem_loja_comprovada",
                        })
                        resumo[fonte]["nao_mapeadas"] += 1
                    if fonte == "cadastro_base":
                        _merge_preenchidos(base_generica.setdefault(sku, {}), linha)
                    continue

            # An exact store_id has already mapped the row.  loja_sync is
            # display-only in that format, so never remap or reject it by name.
            if store_id_fonte:
                labels = []

            if not labels and not ids_linha:
                chave_erro = (fonte, sku, "", "sem_loja_comprovada")
                if chave_erro not in vistos_nao_mapeados:
                    vistos_nao_mapeados.add(chave_erro)
                    nao_mapeados.append({
                        "fonte": fonte,
                        "linha": str(indice),
                        "sku": sku,
                        "loja_sync": "",
                        "motivo": "sem_loja_comprovada",
                    })
                    resumo[fonte]["nao_mapeadas"] += 1
                if fonte == "cadastro_base":
                    _merge_preenchidos(base_generica.setdefault(sku, {}), linha)
                continue

            for label in labels:
                chave_label = _nome_loja_chave(label)
                if chave_label in nomes_ambiguos:
                    motivo = "nome_loja_atual_ambiguo"
                    store_id = ""
                else:
                    store_id = ids_por_nome.get(chave_label, "")
                    motivo = "label_nao_corresponde_a_loja_atual"
                if not store_id:
                    chave_erro = (fonte, sku, label, motivo)
                    if chave_erro not in vistos_nao_mapeados:
                        vistos_nao_mapeados.add(chave_erro)
                        nao_mapeados.append({
                            "fonte": fonte,
                            "linha": str(indice),
                            "sku": sku,
                            "loja_sync": label,
                            "motivo": motivo,
                        })
                        resumo[fonte]["nao_mapeadas"] += 1
                    continue
                ids_linha.append(store_id)
                associacoes.add((store_id, sku))
                resumo[fonte]["associacoes_comprovadas"] += 1

            for store_id in set(ids_linha):
                if fonte == "cadastro_base":
                    destino = base_por_loja.setdefault(store_id, {}).setdefault(sku, {})
                    _merge_preenchidos(destino, linha)
                elif fonte == "produtos_compilado":
                    prioridade = 2 if store_id_fonte else 1
                    chave_compilado = (store_id, sku)
                    if prioridade >= prioridades_compilado.get(chave_compilado, 0):
                        prioridades_compilado[chave_compilado] = prioridade
                        compilado_por_loja.setdefault(store_id, {})[sku] = dict(linha)
                elif fonte == "custos_loja":
                    # A identidade duravel sempre prevalece sobre a associacao
                    # legada por nome, independentemente da ordem das linhas.
                    # Entre linhas da mesma classe, a ultima continua vencendo.
                    prioridade = 2 if store_id_fonte else 1
                    chave_custo = (store_id, sku)
                    if prioridade >= prioridades_custos.get(chave_custo, 0):
                        prioridades_custos[chave_custo] = prioridade
                        custos_por_loja.setdefault(store_id, {})[sku] = dict(linha)

    store_id = loja["store_id"]
    nome_loja = loja["nome"]
    sombras: dict[str, dict[str, str]] = {}
    for assoc_store_id, sku in sorted(associacoes):
        if assoc_store_id != store_id:
            continue
        produto: dict[str, str] = {}
        _merge_preenchidos(produto, base_generica.get(sku, {}))
        campos_explicitos_loja = _merge_sobrescrever_preenchidos(
            produto, base_por_loja.get(store_id, {}).get(sku, {})
        )
        campos_derivados_sombras[sku] = _aplicar_compilado(
            produto,
            compilado_por_loja.get(store_id, {}).get(sku, {}),
            sobrescrever_aliases_legadas=True,
            campos_explicitos_loja=campos_explicitos_loja,
        )
        produto["store_id"] = store_id
        produto["sku"] = _normalizar_sku_mes(produto.get("sku") or sku)
        produto["sku_normalizado"] = sku
        produto["loja_sync"] = nome_loja
        produto["row_version"] = "0"
        produto["updated_at_utc"] = ""
        produto["deleted_at_utc"] = ""
        produto["scope_source"] = "legacy_shadow"
        sombras[sku] = produto

    return {
        "client_id": client_id,
        "sombras": sombras,
        "compilado": compilado_por_loja.get(store_id, {}),
        "custos": custos_por_loja.get(store_id, {}),
        "fotos": (
            dict(fotos)
            if fotos is not None
            else _cadastro_mapa_fotos_locais(client_id, store_id)
        ),
        "campos_derivados_sombras": campos_derivados_sombras,
        "nao_mapeados": nao_mapeados,
        "resumo_fontes": resumo,
        "colunas_fontes": colunas_fontes,
    }


def _contexto_legado(client_id: str, loja: dict[str, str]) -> dict[str, Any]:
    return _contexto_legado_de_tenant(
        client_id,
        loja,
        get_tenant_path(client_id),
        _lojas_atuais(client_id),
    )


def _enriquecer_produto(
    registro: dict[str, Any],
    loja: dict[str, str],
    contexto: dict[str, Any],
    *,
    scope_source: str = "store_file",
) -> dict[str, Any]:
    produto = _normalizar_registro_persistido(registro)
    for destino, fontes in {
        "produto_bling": ("nome_bling",),
        "ncm": ("ncm_bling",),
        "cest": ("cest_bling",),
    }.items():
        if str(produto.get(destino) or "").strip():
            continue
        for fonte in fontes:
            valor = _texto_csv(produto.get(fonte, ""))
            if valor.strip():
                produto[destino] = valor
                break
    sku = produto["sku_normalizado"]
    client_id = loja.get("client_id") or contexto.get("client_id") or ""
    store_id = loja.get("store_id") or ""
    foto_local = _cadastro_resolver_foto_local(
        contexto.get("fotos", {}), produto.get("sku") or sku
    )
    foto_local_store_id = (
        _cadastro_foto_store_id_referencia(client_id, foto_local)
        if foto_local
        else ""
    )
    if foto_local and foto_local_store_id == store_id:
        produto["foto"] = foto_local
    elif not _cadastro_foto_referencia_pertence_loja(
        client_id,
        store_id,
        produto.get("foto") or "",
    ):
        produto["foto"] = ""
    if not str(produto.get("foto") or "").strip() and foto_local:
        produto["foto"] = foto_local
    _aplicar_compilado(produto, contexto.get("compilado", {}).get(sku, {}))
    for campo in _cadastro_foto_referencias_invalidas_loja(
        client_id,
        store_id,
        produto,
    ):
        produto[campo] = ""
    if not str(produto.get("foto") or "").strip() and foto_local:
        produto["foto"] = foto_local
    custo = contexto.get("custos", {}).get(sku)
    if isinstance(custo, dict):
        for campo in ("custo", "preco", "imposto"):
            if campo in custo:
                produto[campo] = _texto_csv(custo.get(campo))
    produto["store_id"] = loja["store_id"]
    produto["loja_sync"] = loja["nome"]
    produto["scope_source"] = scope_source
    if scope_source == "legacy_shadow":
        produto["row_version"] = 0
    else:
        try:
            produto["row_version"] = int(str(produto.get("row_version") or "0"))
        except (TypeError, ValueError):
            produto["row_version"] = 0
    return produto


def _registro_por_chave(
    registros: list[dict[str, str]], store_id: str, sku_normalizado: str
) -> tuple[int | None, dict[str, str] | None]:
    for indice, item in enumerate(registros):
        if item.get("store_id") == store_id and item.get("sku_normalizado") == sku_normalizado:
            return indice, item
    return None, None


def _dados_mutacao(
    payload: Any,
    *,
    campos_derivados_permitidos: Iterable[str] | None = None,
) -> tuple[dict[str, str], str, str]:
    bruto = _payload_dict(payload)
    data: dict[str, str] = {}
    foto_data_url = ""
    foto_filename = ""
    derivados_permitidos = {
        str(campo or "").strip().lower()
        for campo in (campos_derivados_permitidos or ())
        if str(campo or "").strip()
    }
    for chave, valor in bruto.items():
        nome = str(chave or "").strip().lower()
        if not nome:
            continue
        if nome == "__foto_data_url":
            foto_data_url = str(valor or "").strip()
            continue
        if nome == "__foto_filename":
            foto_filename = str(valor or "").strip()
            continue
        if nome in {"__legacy_snapshot_hash", "__expected_scope"}:
            continue
        if nome in _COLUNAS_IDENTIDADE:
            continue
        if nome in _COLUNAS_DERIVADAS_COMPILADO and nome not in derivados_permitidos:
            continue
        data[nome] = _texto_csv(valor)
    return data, foto_data_url, foto_filename


def _validar_referencias_fotos_loja(
    client_id: str,
    store_id: str,
    registro: dict[str, Any],
) -> None:
    if _cadastro_foto_referencias_invalidas_loja(client_id, store_id, registro):
        raise HTTPException(status_code=400, detail="Foto nao pertence a esta loja.")


def _materializar_foto_local_existente(
    client_id: str,
    loja: dict[str, str],
    contexto: dict[str, Any],
    registro: dict[str, Any],
) -> bool:
    """Persist the canonical reference when scoped bytes already exist.

    Reads already resolve a blank ``foto`` through the per-store filename map.
    Only bytes owned by the selected store may become a persisted reference.
    A legacy/global fallback remains read-only and must not block an unrelated
    product write.  The caller owns the catalog and photo-transition locks, and
    its normal row-version increment records this as part of the same event.
    """

    if str(registro.get("foto") or "").strip():
        return False
    foto_local = _cadastro_resolver_foto_local(
        contexto.get("fotos", {}),
        registro.get("sku") or registro.get("sku_normalizado") or "",
    )
    if not foto_local:
        return False
    if (
        _cadastro_foto_store_id_referencia(client_id, foto_local)
        != str(loja.get("store_id") or "").strip()
    ):
        return False
    registro["foto"] = foto_local
    return True


def _extrair_custos_mutacao(dados: dict[str, str]) -> dict[str, str]:
    return {
        campo: dados.pop(campo)
        for campo in ("custo", "preco", "imposto")
        if campo in dados
    }


def _validar_row_version(
    payload: Any,
    existente: dict[str, str] | None,
    *,
    sombra: dict[str, str] | None = None,
    obrigatoria: bool = False,
) -> None:
    bruto = _payload_dict(payload)
    esperado_escopo = str(bruto.get("__expected_scope") or "").strip().lower()
    if esperado_escopo:
        if esperado_escopo not in {"absent", "legacy_shadow", "store_file"}:
            raise HTTPException(status_code=400, detail="__expected_scope invalido.")
        if existente is not None:
            atual_escopo = "store_file"
        elif sombra is not None:
            atual_escopo = "legacy_shadow"
        else:
            atual_escopo = "absent"
        if esperado_escopo != atual_escopo:
            raise HTTPException(status_code=409, detail="Produto foi alterado por outra operacao.")

    esperado = bruto.get("row_version")
    if esperado in (None, ""):
        if obrigatoria:
            raise HTTPException(status_code=409, detail="row_version e obrigatoria para atualizar.")
        return
    try:
        esperado_int = int(str(esperado).strip())
        if existente is not None:
            atual_int = int(str(existente.get("row_version") or "0").strip())
        elif sombra is not None:
            atual_int = 0
        else:
            return
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="row_version invalida.") from exc
    if esperado_int != atual_int:
        raise HTTPException(status_code=409, detail="Produto foi alterado por outra operacao.")
    if sombra is not None:
        esperado_sombra = str(bruto.get("__legacy_snapshot_hash") or "").strip()
        if esperado_sombra and esperado_sombra != _fingerprint_sombra_legada(sombra):
            raise HTTPException(status_code=409, detail="Produto legado foi alterado por outra operacao.")


def salvar_produto_loja(
    client_id: str,
    store_id: str,
    payload: Any,
    sku_original: str | None = None,
    *,
    somente_criar: bool = False,
    somente_atualizar: bool = False,
) -> dict[str, Any]:
    """Atomically upsert one store product for imports and internal callers.

    When ``sku_original`` identifies only a legacy shadow record, that record is
    materialized in the new file before applying the incoming fields.
    """

    loja_snapshot = resolver_loja_cadastro(client_id, store_id)
    dados, foto_data_url, foto_filename = _dados_mutacao(payload)
    custos_mutacao = _extrair_custos_mutacao(dados)
    sku_payload = dados.get("sku", "")
    sku_alvo = _normalizar_sku_chave(sku_original or sku_payload)
    if not sku_alvo:
        raise HTTPException(status_code=400, detail="SKU e obrigatorio.")
    if sku_original and sku_payload and _normalizar_sku_chave(sku_payload) != sku_alvo:
        raise HTTPException(status_code=400, detail="Alteracao da chave SKU nao e permitida nesta rota.")

    caminho = _cadastro_produtos_lojas_path(client_id)
    with _bloquear_loja_cadastro_para_commit(client_id, loja_snapshot) as loja:
        with _cadastro_custos_lock(client_id):
            registros, colunas = _ler_registros_persistidos(client_id)
            indice, existente = _registro_por_chave(registros, loja["store_id"], sku_alvo)
            contexto = _contexto_legado(client_id, loja)
            sombra = contexto["sombras"].get(sku_alvo)
            if somente_atualizar:
                if existente is not None and str(existente.get("deleted_at_utc") or "").strip():
                    raise HTTPException(status_code=404, detail="Produto nao encontrado nesta loja.")
                if existente is None and sombra is None:
                    raise HTTPException(status_code=404, detail="Produto nao encontrado nesta loja.")
            _validar_row_version(
                payload,
                existente,
                sombra=sombra,
                obrigatoria=somente_atualizar,
            )
            if (
                somente_criar
                and existente is not None
                and not str(existente.get("deleted_at_utc") or "").strip()
            ):
                raise HTTPException(status_code=409, detail="Ja existe um produto com este SKU nesta loja.")
            if somente_criar and existente is None and sombra is not None:
                raise HTTPException(status_code=409, detail="SKU ja associado a esta loja no cadastro legado.")

            if existente is not None:
                base = dict(existente)
                versao = int(str(existente.get("row_version") or "0")) + 1
            elif sombra is not None:
                derivados = contexto.get("campos_derivados_sombras", {}).get(sku_alvo, set())
                base = {
                    chave: _texto_csv(valor)
                    for chave, valor in sombra.items()
                    if chave not in {"scope_source", "row_version", "updated_at_utc", "deleted_at_utc"}
                    and chave not in derivados
                }
                versao = 1
            else:
                base = {}
                versao = 1

            base.update(dados)
            for campo_custo in ("custo", "preco", "imposto"):
                base.pop(campo_custo, None)
            sku_exibicao = _normalizar_sku_mes(base.get("sku") or sku_original or sku_alvo)
            base["store_id"] = loja["store_id"]
            base["sku"] = sku_exibicao
            base["sku_normalizado"] = sku_alvo
            base["loja_sync"] = loja["nome"]
            base["row_version"] = str(versao)
            base["updated_at_utc"] = _agora_utc()
            base["deleted_at_utc"] = ""
            base.pop("scope_source", None)

            fotos_preparadas: list[dict[str, Any]] = []
            if foto_data_url:
                fotos_preparadas = _preparar_fotos_data_url_no_tenant(
                    client_id,
                    sku_exibicao,
                    foto_data_url,
                    foto_filename,
                    store_id=loja["store_id"],
                )
                foto_preparada_loja = next(
                    (
                        item
                        for item in fotos_preparadas
                        if str(item.get("store_id") or "") == loja["store_id"]
                    ),
                    None,
                )
                if not foto_preparada_loja:
                    raise HTTPException(
                        status_code=500,
                        detail="A foto da loja nao foi preparada.",
                    )
                base["foto"] = str(foto_preparada_loja["relativo"])
            else:
                _materializar_foto_local_existente(
                    client_id,
                    loja,
                    contexto,
                    base,
                )
            _validar_referencias_fotos_loja(
                client_id,
                loja["store_id"],
                base,
            )

            if indice is None:
                registros.append(base)
            else:
                registros[indice] = base
            _cadastro_propagar_referencias_fotos_preparadas(
                registros,
                fotos_preparadas,
                registrar_evento=bool(fotos_preparadas),
                store_id_origem=loja["store_id"],
            )

            caminhos_variantes_fotos = (
                _cadastro_caminhos_variantes_fotos_preparadas(fotos_preparadas)
                if fotos_preparadas
                else []
            )
            caminhos_transacao = [caminho]
            if custos_mutacao:
                caminhos_transacao.append(_cadastro_custos_lojas_path(client_id))
            caminhos_transacao.extend(caminhos_variantes_fotos)
            with (
                _cadastro_fotos_bloquear_transicao(client_id),
                path_locks_for(caminhos_variantes_fotos),
            ):
                _cadastro_fotos_validar_preparadas_no_lock(
                    client_id, fotos_preparadas
                )
                _validar_caminhos_variantes_fotos(caminhos_variantes_fotos)
                estados = _capturar_estados_arquivos(caminhos_transacao)
                try:
                    for foto_preparada in fotos_preparadas:
                        _salvar_foto_preparada_atomico(foto_preparada)
                    _remover_variantes_fotos_obsoletas(
                        fotos_preparadas,
                        caminhos_variantes_fotos,
                    )
                    if custos_mutacao:
                        _cadastro_salvar_custos_item_loja(
                            client_id,
                            loja["store_id"],
                            loja["nome"],
                            sku_exibicao,
                            custos_mutacao,
                        )
                    _salvar_registros_atomico(client_id, registros, colunas)
                    contexto = _contexto_legado(client_id, loja)
                    produto = _enriquecer_produto(base, loja, contexto)
                except BaseException:
                    _rollback_arquivos(estados)
                    raise
    _notificar_ficha_catalogo(client_id, loja["store_id"])
    return produto


def salvar_produtos_loja_em_lote(
    client_id: str,
    store_id: str,
    itens: Iterable[Any],
    *,
    campos_derivados_permitidos: Iterable[str] | None = None,
    precommit_validator: Callable[[dict[str, str]], None] | None = None,
) -> dict[str, Any]:
    """Upsert a validated batch through one lock, replace and readback cycle."""

    loja_snapshot = resolver_loja_cadastro(client_id, store_id)
    preparados: list[tuple[Any, dict[str, str], str, str, str, dict[str, str]]] = []
    skus_vistos: set[str] = set()
    for payload in list(itens or []):
        dados, foto_data_url, foto_filename = _dados_mutacao(
            payload,
            campos_derivados_permitidos=campos_derivados_permitidos,
        )
        custos_mutacao = _extrair_custos_mutacao(dados)
        sku = _normalizar_sku_chave(dados.get("sku") or "")
        if not sku:
            raise HTTPException(status_code=400, detail="Todos os produtos do lote precisam de SKU.")
        if sku in skus_vistos:
            raise HTTPException(status_code=400, detail=f"SKU duplicado no lote: {sku}.")
        skus_vistos.add(sku)
        preparados.append((payload, dados, sku, foto_data_url, foto_filename, custos_mutacao))

    if not preparados:
        return {
            "success": True,
            "store_id": loja_snapshot["store_id"],
            "loja_sync": loja_snapshot["nome"],
            "incluidos": 0,
            "atualizados": 0,
            "total": 0,
            "produtos": [],
        }

    caminho = _cadastro_produtos_lojas_path(client_id)
    with _bloquear_loja_cadastro_para_commit(client_id, loja_snapshot) as loja:
        if precommit_validator is not None:
            precommit_validator(dict(loja))
        with _cadastro_custos_lock(client_id):
            registros, colunas = _ler_registros_persistidos(client_id)
            contexto = _contexto_legado(client_id, loja)
            incluidos = 0
            atualizados = 0
            salvos: list[dict[str, str]] = []
            fotos_preparadas: list[dict[str, Any]] = []
            custos_por_sku: list[tuple[str, dict[str, str]]] = []
            registros_por_chave: dict[
                tuple[str, str], tuple[int, dict[str, str]]
            ] = {}
            for indice_registro, registro in enumerate(registros):
                chave = (
                    str(registro.get("store_id") or ""),
                    str(registro.get("sku_normalizado") or ""),
                )
                registros_por_chave.setdefault(chave, (indice_registro, registro))

            for payload, dados, sku_alvo, foto_data_url, foto_filename, custos_mutacao in preparados:
                indice, existente = registros_por_chave.get(
                    (loja["store_id"], sku_alvo), (None, None)
                )
                sombra = contexto["sombras"].get(sku_alvo)
                _validar_row_version(payload, existente, sombra=sombra)
                sku_exibicao_existente = ""
                if existente is not None:
                    base = dict(existente)
                    sku_exibicao_existente = str(existente.get("sku") or "").strip()
                    versao = int(str(existente.get("row_version") or "0")) + 1
                    atualizados += 1
                elif sombra is not None:
                    sku_exibicao_existente = str(sombra.get("sku") or "").strip()
                    derivados = contexto.get("campos_derivados_sombras", {}).get(sku_alvo, set())
                    base = {
                        chave: _texto_csv(valor)
                        for chave, valor in sombra.items()
                        if chave not in {"scope_source", "row_version", "updated_at_utc", "deleted_at_utc"}
                        and chave not in derivados
                    }
                    versao = 1
                    atualizados += 1
                else:
                    base = {}
                    versao = 1
                    incluidos += 1

                base.update(dados)
                for campo_custo in ("custo", "preco", "imposto"):
                    base.pop(campo_custo, None)
                sku_exibicao = sku_exibicao_existente or _normalizar_sku_mes(
                    base.get("sku") or sku_alvo
                )
                base["store_id"] = loja["store_id"]
                base["sku"] = sku_exibicao
                base["sku_normalizado"] = sku_alvo
                base["loja_sync"] = loja["nome"]
                base["row_version"] = str(versao)
                base["updated_at_utc"] = _agora_utc()
                base["deleted_at_utc"] = ""
                base.pop("scope_source", None)
                if foto_data_url:
                    novas_fotos_preparadas = _preparar_fotos_data_url_no_tenant(
                        client_id,
                        sku_exibicao,
                        foto_data_url,
                        foto_filename,
                        store_id=loja["store_id"],
                    )
                    foto_preparada_loja = next(
                        (
                            item
                            for item in novas_fotos_preparadas
                            if str(item.get("store_id") or "") == loja["store_id"]
                        ),
                        None,
                    )
                    if not foto_preparada_loja:
                        raise HTTPException(
                            status_code=500,
                            detail="A foto da loja nao foi preparada.",
                        )
                    base["foto"] = str(foto_preparada_loja["relativo"])
                    fotos_preparadas.extend(novas_fotos_preparadas)
                else:
                    _materializar_foto_local_existente(
                        client_id,
                        loja,
                        contexto,
                        base,
                    )
                _validar_referencias_fotos_loja(
                    client_id,
                    loja["store_id"],
                    base,
                )

                if indice is None:
                    indice = len(registros)
                    registros.append(base)
                else:
                    registros[indice] = base
                registros_por_chave[(loja["store_id"], sku_alvo)] = (indice, base)
                salvos.append(base)
                if custos_mutacao:
                    custos_por_sku.append((sku_exibicao, custos_mutacao))

            _cadastro_propagar_referencias_fotos_preparadas(
                registros,
                fotos_preparadas,
                registrar_evento=bool(fotos_preparadas),
                store_id_origem=loja["store_id"],
            )
            destinos_foto = [str(item["caminho"]) for item in fotos_preparadas]
            if len(set(map(os.path.normcase, destinos_foto))) != len(destinos_foto):
                raise HTTPException(
                    status_code=400,
                    detail="Duas imagens do lote resultam no mesmo arquivo de destino.",
                )
            caminhos_variantes_fotos = (
                _cadastro_caminhos_variantes_fotos_preparadas(fotos_preparadas)
                if fotos_preparadas
                else []
            )
            caminhos_transacao = [caminho, *caminhos_variantes_fotos]
            if custos_por_sku:
                caminhos_transacao.append(_cadastro_custos_lojas_path(client_id))
            with (
                _cadastro_fotos_bloquear_transicao(client_id),
                path_locks_for(caminhos_variantes_fotos),
            ):
                _cadastro_fotos_validar_preparadas_no_lock(
                    client_id, fotos_preparadas
                )
                _validar_caminhos_variantes_fotos(caminhos_variantes_fotos)
                estados = _capturar_estados_arquivos(caminhos_transacao)
                try:
                    for foto_preparada in fotos_preparadas:
                        _salvar_foto_preparada_atomico(foto_preparada)
                    _remover_variantes_fotos_obsoletas(
                        fotos_preparadas,
                        caminhos_variantes_fotos,
                    )
                    for sku_exibicao, custos_mutacao in custos_por_sku:
                        _cadastro_salvar_custos_item_loja(
                            client_id,
                            loja["store_id"],
                            loja["nome"],
                            sku_exibicao,
                            custos_mutacao,
                        )
                    _salvar_registros_atomico(client_id, registros, colunas)
                    contexto = _contexto_legado(client_id, loja)
                    produtos_saida = [
                        _enriquecer_produto(item, loja, contexto) for item in salvos
                    ]
                except BaseException:
                    _rollback_arquivos(estados)
                    raise

    _notificar_ficha_catalogo(client_id, loja["store_id"])
    return {
        "success": True,
        "store_id": loja["store_id"],
        "loja_sync": loja["nome"],
        "incluidos": incluidos,
        "atualizados": atualizados,
        "total": len(salvos),
        "produtos": produtos_saida,
    }


def _criar_produto_loja(client_id: str, store_id: str, payload: Any) -> dict[str, Any]:
    return salvar_produto_loja(client_id, store_id, payload, somente_criar=True)


def _listar_produtos_loja_sync(
    client_id: str,
    store_id: str,
    *,
    include_deleted: bool = False,
    include_legacy_snapshot_hash: bool = False,
) -> list[dict[str, Any]]:
    loja = resolver_loja_cadastro(client_id, store_id)
    caminho = _cadastro_produtos_lojas_path(client_id)
    with _lock_arquivo(caminho):
        registros, _ = _ler_registros_persistidos(client_id)
    contexto = _contexto_legado(client_id, loja)
    saida: list[dict[str, Any]] = []
    chaves_explicitadas: set[str] = set()
    for item in registros:
        if item.get("store_id") != loja["store_id"]:
            continue
        sku = item.get("sku_normalizado", "")
        chaves_explicitadas.add(sku)
        apagado = bool(str(item.get("deleted_at_utc") or "").strip())
        if apagado and not include_deleted:
            continue
        saida.append(_enriquecer_produto(item, loja, contexto))
    for sku, sombra in contexto["sombras"].items():
        if sku in chaves_explicitadas:
            continue
        enriquecido = _enriquecer_produto(
            sombra,
            loja,
            contexto,
            scope_source="legacy_shadow",
        )
        if include_legacy_snapshot_hash:
            enriquecido["__legacy_snapshot_hash"] = _fingerprint_sombra_legada(sombra)
        saida.append(enriquecido)
    return sorted(saida, key=lambda item: str(item.get("sku_normalizado") or ""))


async def listar_produtos_loja(
    store_id: str,
    include_deleted: bool = False,
    view: Literal["full", "summary"] = "full",
    client_id: str = Depends(get_tenant_id),
):
    from backend.services.cadastro_lojas_listagem import (
        listar_produtos_loja_snapshot_sync,
    )

    return await run_in_threadpool(
        listar_produtos_loja_snapshot_sync,
        client_id,
        store_id,
        include_deleted=include_deleted,
        view=view,
    )


def _obter_produto_loja_sync(client_id: str, store_id: str, sku: str) -> dict[str, Any]:
    loja = resolver_loja_cadastro(client_id, store_id)
    sku_normalizado = _normalizar_sku_chave(sku)
    if not sku_normalizado:
        raise HTTPException(status_code=400, detail="SKU invalido.")
    caminho = _cadastro_produtos_lojas_path(client_id)
    with _lock_arquivo(caminho):
        registros, _ = _ler_registros_persistidos(client_id)
    _, item = _registro_por_chave(registros, loja["store_id"], sku_normalizado)
    contexto = _contexto_legado(client_id, loja)
    if item is not None:
        if str(item.get("deleted_at_utc") or "").strip():
            raise HTTPException(status_code=404, detail="Produto nao encontrado nesta loja.")
        return _enriquecer_produto(item, loja, contexto)
    sombra = contexto["sombras"].get(sku_normalizado)
    if sombra is not None:
        return _enriquecer_produto(sombra, loja, contexto, scope_source="legacy_shadow")
    raise HTTPException(status_code=404, detail="Produto nao encontrado nesta loja.")


async def obter_produto_loja(
    store_id: str,
    sku: str,
    client_id: str = Depends(get_tenant_id),
):
    return {"produto": _obter_produto_loja_sync(client_id, store_id, sku)}


async def criar_produto_loja(
    store_id: str,
    payload: CadastroProdutoLojaRequest,
    client_id: str = Depends(get_tenant_id),
):
    produto = _criar_produto_loja(client_id, store_id, payload)
    return {
        "success": True,
        "message": "Produto incluido com sucesso nesta loja.",
        "sku": produto["sku"],
        "produto": produto,
    }


async def atualizar_produto_loja(
    store_id: str,
    sku: str,
    payload: CadastroProdutoLojaAtualizacaoRequest,
    client_id: str = Depends(get_tenant_id),
):
    produto = salvar_produto_loja(
        client_id,
        store_id,
        payload,
        sku_original=sku,
        somente_atualizar=True,
    )
    return {
        "success": True,
        "message": "Produto atualizado com sucesso nesta loja.",
        "sku": produto["sku"],
        "produto": produto,
    }


async def excluir_produto_loja(
    store_id: str,
    sku: str,
    row_version: int,
    client_id: str = Depends(get_tenant_id),
):
    loja_snapshot = resolver_loja_cadastro(client_id, store_id)
    sku_normalizado = _normalizar_sku_chave(sku)
    if not sku_normalizado:
        raise HTTPException(status_code=400, detail="SKU invalido.")
    caminho = _cadastro_produtos_lojas_path(client_id)
    with _bloquear_loja_cadastro_para_commit(client_id, loja_snapshot) as loja:
        registros, colunas = _ler_registros_persistidos(client_id)
        indice, existente = _registro_por_chave(registros, loja["store_id"], sku_normalizado)
        contexto = _contexto_legado(client_id, loja)
        sombra = contexto["sombras"].get(sku_normalizado)
        if existente is not None and str(existente.get("deleted_at_utc") or "").strip():
            raise HTTPException(status_code=404, detail="Produto nao encontrado nesta loja.")
        if existente is None and sombra is None:
            raise HTTPException(status_code=404, detail="Produto nao encontrado nesta loja.")
        _validar_row_version(
            {"row_version": row_version},
            existente,
            sombra=sombra,
            obrigatoria=True,
        )

        agora = _agora_utc()
        if existente is None:
            tombstone = {
                "store_id": loja["store_id"],
                "sku": _normalizar_sku_mes(sku),
                "sku_normalizado": sku_normalizado,
                "loja_sync": loja["nome"],
                "row_version": "1",
                "updated_at_utc": agora,
                "deleted_at_utc": agora,
            }
            registros.append(tombstone)
            versao = 1
        else:
            tombstone = dict(existente)
            versao = int(str(existente.get("row_version") or "0")) + 1
            tombstone["loja_sync"] = loja["nome"]
            tombstone["row_version"] = str(versao)
            tombstone["updated_at_utc"] = agora
            tombstone["deleted_at_utc"] = agora
            registros[indice] = tombstone
        _salvar_registros_atomico(client_id, registros, colunas)
    _notificar_ficha_catalogo(client_id, loja["store_id"])
    return {
        "success": True,
        "sku": _normalizar_sku_mes(sku),
        "store_id": loja["store_id"],
        "row_version": versao,
        "deleted_at_utc": agora,
    }


def _notificar_ficha_catalogo(client_id: str, store_id: str) -> None:
    try:
        from backend.modules.context_hub.catalog_product_sync import notify_catalog_committed
        notify_catalog_committed(
            client_id, store_id, info_root=Path(get_tenant_path(client_id)).parent)
    except Exception:
        # The CSV transaction is already committed. Reconciliation recovers a
        # missing notification without reporting a false save failure.
        pass


async def listar_colunas_produtos_loja(
    store_id: str,
    client_id: str = Depends(get_tenant_id),
):
    loja = resolver_loja_cadastro(client_id, store_id)
    produtos = _listar_produtos_loja_sync(client_id, store_id, include_deleted=True)
    contexto = _contexto_legado(client_id, loja)
    colunas_fonte = [
        coluna
        for colunas in contexto.get("colunas_fontes", {}).values()
        for coluna in colunas
    ]
    colunas = _ordenar_colunas(produtos, [*CADASTRO_COLS_BASE, *colunas_fonte])
    if "scope_source" not in colunas:
        colunas.append("scope_source")
    return {"colunas": colunas}


def preview_migracao_produtos_loja(client_id: str, store_id: str) -> dict[str, Any]:
    """Build a pure read-only migration preview from exact current store names."""

    loja = resolver_loja_cadastro(client_id, store_id)
    contexto = _contexto_legado(client_id, loja)
    caminho = _cadastro_produtos_lojas_path(client_id)
    with _lock_arquivo(caminho):
        registros, _ = _ler_registros_persistidos(client_id)
    existentes = {
        item["sku_normalizado"]
        for item in registros
        if item.get("store_id") == loja["store_id"]
    }
    produtos = [
        _enriquecer_produto(item, loja, contexto, scope_source="legacy_shadow")
        for sku, item in sorted(contexto["sombras"].items())
        if sku not in existentes
    ]
    return {
        "store_id": loja["store_id"],
        "loja_sync": loja["nome"],
        "dry_run": True,
        "would_write": False,
        "arquivo_destino": CADASTRO_PRODUTOS_LOJAS_ARQUIVO,
        "total_produtos_materializaveis": len(produtos),
        "total_ja_materializados": len(existentes & set(contexto["sombras"])),
        "produtos": produtos,
        "nao_mapeados": contexto["nao_mapeados"],
        "resumo_fontes": contexto["resumo_fontes"],
    }


async def preview_migracao_produtos_loja_endpoint(
    store_id: str,
    client_id: str = Depends(get_tenant_id),
):
    return preview_migracao_produtos_loja(client_id, store_id)


__all__ = [
    "CADASTRO_PRODUTOS_LOJAS_ARQUIVO",
    "CADASTRO_PRODUTOS_LOJAS_COLUNAS",
    "configure_cadastro_lojas_produtos_runtime",
    "resolver_loja_cadastro",
    "salvar_produto_loja",
    "salvar_produtos_loja_em_lote",
    "listar_produtos_loja",
    "obter_produto_loja",
    "criar_produto_loja",
    "atualizar_produto_loja",
    "excluir_produto_loja",
    "listar_colunas_produtos_loja",
    "preview_migracao_produtos_loja",
    "preview_migracao_produtos_loja_endpoint",
]
