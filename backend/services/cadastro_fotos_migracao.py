"""Planejamento e migracao segura de fotos legadas para escopo por loja."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import posixpath
import re
import tempfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import unquote, urlsplit

from backend.services.cadastro_fotos import (
    CADASTRO_FOTOS_CONFIG_ARQUIVO,
    CADASTRO_FOTOS_CONFIG_SCHEMA,
    _cadastro_foto_cabecalho_normalizar,
    _cadastro_foto_coluna_candidata,
    _cadastro_foto_referencia_local_cadastro,
    _cadastro_foto_referencia_limpar_wrappers,
    _cadastro_store_id_foto_segmento,
)
from backend.services.cadastro_common import _normalizar_sku_mes
from backend.services.cadastro_fotos_coordenacao import (
    CadastroFotosCoordenacaoErro,
    bloquear_transicao_fotos_tenant,
    cadastro_fotos_transition_lock_path,
)


EXTENSOES_FOTO_SUPORTADAS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
ARQUIVOS_CADASTRO_COM_REFERENCIA_FOTO = (
    "cadastro_produtos.csv",
    "cadastro_produtos_lojas.csv",
    "produtos_compilado.csv",
)
LIMITE_ARQUIVO_CADASTRO_REFERENCIAS = 64 * 1024 * 1024
MIGRACAO_JOURNAL_ARQUIVO = ".cadastro-fotos-migracao-v1.json"
MIGRACAO_JOURNAL_SCHEMA = "jk.cadastro.fotos.migration.v1"
MIGRACAO_QUARENTENA_PASTA = ".cadastro-fotos-legadas-quarentena-v1"
MIGRACAO_PLANO_SCHEMA = "jk.cadastro.fotos.migration-plan.v1"
_REFERENCIA_FOTO_POR_LOJA = object()


class CadastroFotosMigracaoErro(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _falhar(code: str, message: str) -> None:
    raise CadastroFotosMigracaoErro(code, message)


def _sha256_arquivo(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def _manifesto_direto(pasta: Path, *, obrigatorio: bool = True) -> dict[str, dict[str, Any]]:
    if not pasta.is_dir():
        if obrigatorio:
            _falhar("photo_directory_missing", "A pasta de fotos legadas nao existe.")
        return {}
    manifesto: dict[str, dict[str, Any]] = {}
    nomes_casefold: set[str] = set()
    for caminho in sorted(pasta.iterdir(), key=lambda item: item.name.casefold()):
        if (
            caminho.name.startswith(".cadastro-foto-migracao.")
            and caminho.name.endswith(".tmp")
        ):
            _falhar(
                "migration_temp_cleanup_pending",
                "Existe um arquivo temporario de copia que precisa ser removido.",
            )
        if caminho.suffix.casefold() not in EXTENSOES_FOTO_SUPORTADAS:
            continue
        if caminho.is_symlink() or not caminho.is_file():
            _falhar("unsafe_photo_entry", "A pasta contem uma entrada de foto insegura.")
        chave = caminho.name.casefold()
        if chave in nomes_casefold:
            _falhar("ambiguous_photo_name", "Existem nomes de foto ambiguos por caixa.")
        nomes_casefold.add(chave)
        tamanho = caminho.stat().st_size
        if tamanho <= 0:
            _falhar("empty_photo", "A pasta contem uma foto vazia.")
        manifesto[caminho.name] = {
            "path": caminho,
            "size": tamanho,
            "sha256": _sha256_arquivo(caminho),
        }
    return manifesto


def _digest_manifesto(manifesto: dict[str, dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for nome in sorted(manifesto, key=str.casefold):
        item = manifesto[nome]
        digest.update(nome.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item["size"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(item["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _manifestos_iguais(
    esperado: dict[str, dict[str, Any]],
    atual: dict[str, dict[str, Any]],
) -> bool:
    if set(esperado) != set(atual):
        return False
    return all(
        esperado[nome]["size"] == atual[nome]["size"]
        and esperado[nome]["sha256"] == atual[nome]["sha256"]
        for nome in esperado
    )


def _sku_chave_resolucao_foto(valor: object) -> str:
    sku = _normalizar_sku_mes(str(valor or "").strip()).upper()
    if not sku:
        return ""
    partes = re.split(r"(\d+)", sku)
    return "".join(str(int(parte)) if parte.isdigit() else parte for parte in partes)


def _foto_preferida_resolver(
    manifesto: dict[str, dict[str, Any]],
    sku: object,
) -> str:
    sku_norm = _normalizar_sku_mes(str(sku or "").strip())
    if not sku_norm:
        return ""
    ordenados = sorted(
        manifesto,
        key=lambda nome: (
            Path(nome).suffix.casefold() != ".png",
            nome.casefold(),
        ),
    )
    exatos = [
        nome
        for nome in ordenados
        if _normalizar_sku_mes(Path(nome).stem) == sku_norm
    ]
    if exatos:
        return exatos[0]
    chave = _sku_chave_resolucao_foto(sku_norm)
    return next(
        (
            nome
            for nome in ordenados
            if _sku_chave_resolucao_foto(Path(nome).stem) == chave
        ),
        "",
    )


def _referencia_foto_legada_nome(foto: object, client_id: str) -> str | object | None:
    """Extrai apenas referencias locais diretas da pasta legada de fotos."""

    caminho = _cadastro_foto_referencia_limpar_wrappers(foto).replace("\\", "/")
    if not caminho or re.match(r"^data:", caminho, re.IGNORECASE):
        return None

    for _ in range(3):
        decodificado = unquote(caminho)
        if decodificado == caminho:
            break
        caminho = _cadastro_foto_referencia_limpar_wrappers(decodificado).replace(
            "\\", "/"
        )

    caminho_windows = bool(re.match(r"^[a-z]:/", caminho, re.IGNORECASE))
    if caminho.startswith("//"):
        try:
            caminho_url = urlsplit(f"https:{caminho}")
        except ValueError as exc:
            raise CadastroFotosMigracaoErro(
                "legacy_photo_reference_invalid",
                "Existe uma referencia local de foto invalida no cadastro.",
            ) from exc
        caminho = posixpath.normpath(
            "/" + str(caminho_url.path or "").replace("\\", "/").lstrip("/")
        )
        caminho_fold = caminho.casefold()
        if not (
            caminho_fold.startswith("/api/cadastro/foto/")
            or caminho_fold.startswith("/api/cadastro/foto-arquivo/")
        ):
            return None
    elif caminho_windows:
        # Caminho absoluto do Windows: continua como referencia local.
        pass
    elif caminho.casefold().startswith("file:"):
        try:
            caminho = str(urlsplit(caminho).path or "").replace("\\", "/")
        except ValueError as exc:
            raise CadastroFotosMigracaoErro(
                "legacy_photo_reference_invalid",
                "Existe uma referencia local de foto invalida no cadastro.",
            ) from exc
    elif re.match(r"^([a-z][a-z0-9+.-]*):", caminho, re.IGNORECASE):
        esquema = caminho.split(":", 1)[0].casefold()
        if esquema not in {"http", "https"}:
            return None
        if re.match(r"^https?://", caminho, re.IGNORECASE):
            try:
                caminho_url = urlsplit(caminho)
            except ValueError as exc:
                raise CadastroFotosMigracaoErro(
                    "legacy_photo_reference_invalid",
                    "Existe uma referencia local de foto invalida no cadastro.",
                ) from exc
            caminho_api = posixpath.normpath(
                "/" + str(caminho_url.path or "").replace("\\", "/").lstrip("/")
            )
        else:
            caminho_api = posixpath.normpath(
                "/" + caminho.split(":", 1)[1].replace("\\", "/").lstrip("/")
            )
        caminho_api_fold = caminho_api.casefold()
        if not (
            caminho_api_fold.startswith("/api/cadastro/foto/")
            or caminho_api_fold.startswith("/api/cadastro/foto-arquivo/")
        ):
            return None
        caminho = caminho_api
    caminho = caminho.split("?", 1)[0].split("#", 1)[0]
    caminho = posixpath.normpath("/" + caminho.lstrip("/"))
    caminho_rota = caminho.lstrip("/")
    caminho_rota_fold = caminho_rota.casefold()
    prefixo_arquivo = "api/cadastro/foto-arquivo/"
    prefixo_tenant = "api/cadastro/foto/"
    if caminho_rota_fold.startswith(prefixo_arquivo):
        relativo = caminho_rota[len(prefixo_arquivo):]
    elif caminho_rota_fold.startswith(prefixo_tenant):
        partes_api = caminho_rota[len(prefixo_tenant):].strip("/").split("/")
        if len(partes_api) < 2 or not partes_api[0]:
            _falhar(
                "legacy_photo_reference_invalid",
                "Existe uma referencia local de foto invalida no cadastro.",
            )
        if partes_api[0] != str(client_id):
            _falhar(
                "legacy_photo_reference_cross_tenant",
                "Existe uma referencia de foto pertencente a outro cliente.",
            )
        relativo = "/".join(partes_api[1:])
    else:
        relativo = caminho.strip("/")

    relativo = relativo.strip("/")
    if relativo.casefold().startswith("cadastro_fotos/"):
        relativo = relativo.split("/", 1)[1]
    if relativo.casefold().startswith("lojas/"):
        return _REFERENCIA_FOTO_POR_LOJA
    if "/cadastro_fotos/" in relativo.casefold():
        indice = relativo.casefold().rfind("/cadastro_fotos/")
        relativo = relativo[indice + len("/cadastro_fotos/"):]

    partes = relativo.split("/")
    nome = partes[-1]
    if (
        not nome
        or nome in {".", ".."}
        or ":" in nome
        or "\x00" in nome
        or Path(nome).suffix.casefold() not in EXTENSOES_FOTO_SUPORTADAS
    ):
        return None
    return nome


def _validar_referencias_fotos_legadas(
    contexto: dict[str, Path],
    manifesto: dict[str, dict[str, Any]],
    *,
    client_id: str,
) -> dict[str, dict[str, Any]]:
    """Impede ativar escopo estrito quando uma referencia perderia seu SKU."""

    por_nome = {nome.casefold(): nome for nome in manifesto}
    estado: dict[str, dict[str, Any]] = {}
    for arquivo_nome in ARQUIVOS_CADASTRO_COM_REFERENCIA_FOTO:
        caminho = contexto["tenant"] / arquivo_nome
        if not os.path.lexists(caminho):
            continue
        if _caminho_e_link(caminho) or not caminho.is_file():
            _falhar(
                "product_catalog_unsafe",
                "Um arquivo de cadastro usado pela migracao e inseguro.",
            )
        tamanho = caminho.stat().st_size
        if tamanho > LIMITE_ARQUIVO_CADASTRO_REFERENCIAS:
            _falhar(
                "product_catalog_too_large",
                "Um arquivo de cadastro excede o limite da migracao.",
            )
        try:
            conteudo = caminho.read_bytes()
            texto = ""
            for encoding in ("utf-8-sig", "utf-8", "latin1"):
                try:
                    texto = conteudo.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            amostra = texto[:8192]
            try:
                delimitador = csv.Sniffer().sniff(
                    amostra,
                    delimiters=",;\t",
                ).delimiter
            except csv.Error:
                primeira = amostra.splitlines()[0] if amostra.splitlines() else ""
                delimitador = max(
                    (",", ";", "\t"),
                    key=lambda item: primeira.count(item),
                )
            leitor = csv.DictReader(
                io.StringIO(texto, newline=""),
                delimiter=delimitador,
                strict=True,
            )
            cabecalhos = [
                _cadastro_foto_cabecalho_normalizar(item)
                for item in (leitor.fieldnames or [])
            ]
            if any(not item for item in cabecalhos) or len(cabecalhos) != len(set(cabecalhos)):
                _falhar(
                    "product_catalog_invalid",
                    "Um arquivo de cadastro possui colunas ambiguas.",
                )
            colunas_foto = [
                cabecalho
                for cabecalho in cabecalhos
                if _cadastro_foto_coluna_candidata(cabecalho)
            ]
            for linha in leitor:
                if None in linha:
                    _falhar(
                        "product_catalog_invalid",
                        "Um arquivo de cadastro possui uma linha invalida.",
                    )
                normalizada = {
                    _cadastro_foto_cabecalho_normalizar(chave): valor
                    for chave, valor in linha.items()
                    if chave is not None
                }
                if not colunas_foto:
                    continue
                for coluna_foto in colunas_foto:
                    valor_referencia = normalizada.get(coluna_foto)
                    nome_referencia = _referencia_foto_legada_nome(
                        valor_referencia,
                        client_id,
                    )
                    if nome_referencia is _REFERENCIA_FOTO_POR_LOJA:
                        continue
                    if nome_referencia is None:
                        # A tela transforma qualquer valor que nao seja data:
                        # ou HTTP(S) completo em uma rota local pelo basename.
                        # Se o parser conservador nao conseguir materializar
                        # esse basename, a exclusao do legado deve parar.
                        if _cadastro_foto_referencia_local_cadastro(valor_referencia):
                            _falhar(
                                "legacy_photo_reference_invalid",
                                "Existe uma referencia local de foto invalida no cadastro.",
                            )
                        continue
                    if not isinstance(nome_referencia, str):
                        _falhar(
                            "legacy_photo_reference_invalid",
                            "Existe uma referencia local de foto invalida no cadastro.",
                        )
                    if "sku" not in cabecalhos and "sku_normalizado" not in cabecalhos:
                        _falhar(
                            "product_catalog_invalid",
                            "Um arquivo de cadastro com fotos nao possui SKU.",
                        )
                    nome_manifesto = por_nome.get(nome_referencia.casefold())
                    if nome_manifesto is None:
                        _falhar(
                            "referenced_legacy_photo_missing",
                            "Uma referencia local aponta para uma foto legada ausente.",
                        )
                    sku = normalizada.get("sku_normalizado") or normalizada.get("sku") or ""
                    if not _sku_chave_resolucao_foto(sku) or (
                        _sku_chave_resolucao_foto(sku)
                        != _sku_chave_resolucao_foto(Path(nome_manifesto).stem)
                    ):
                        _falhar(
                            "legacy_photo_filename_sku_mismatch",
                            "Uma foto legada nao pode ser associada com seguranca ao SKU.",
                        )
                    nome_preferido = _foto_preferida_resolver(manifesto, sku)
                    if (
                        nome_preferido
                        and nome_preferido.casefold() != nome_manifesto.casefold()
                        and manifesto[nome_preferido]["sha256"]
                        != manifesto[nome_manifesto]["sha256"]
                    ):
                        _falhar(
                            "legacy_photo_variant_ambiguous",
                            "Variantes diferentes da mesma foto tornariam a referencia ambigua.",
                        )
                    _falhar(
                        "legacy_photo_reference_requires_store_migration",
                        "Uma referencia local de foto precisa ser convertida para o escopo por loja antes da migracao.",
                    )
        except CadastroFotosMigracaoErro:
            raise
        except (OSError, UnicodeError, csv.Error) as exc:
            raise CadastroFotosMigracaoErro(
                "product_catalog_invalid",
                "Nao foi possivel validar as referencias de foto do cadastro.",
            ) from exc
        estado[arquivo_nome] = {
            "size": len(conteudo),
            "sha256": hashlib.sha256(conteudo).hexdigest(),
        }
    return estado


def _normalizar_store_ids(store_ids: Iterable[str]) -> list[str]:
    normalizados = [str(item or "").strip() for item in store_ids]
    if (
        len(normalizados) < 2
        or any(not item for item in normalizados)
        or len(set(normalizados)) != len(normalizados)
    ):
        _falhar("invalid_store_group", "O grupo precisa de store_ids exatos e unicos.")
    return normalizados


def _caminho_e_link(caminho: Path) -> bool:
    """Rejeita symlinks e junctions antes de qualquer escrita destrutiva."""
    if caminho.is_symlink():
        return True
    is_junction = getattr(caminho, "is_junction", None)
    return bool(callable(is_junction) and is_junction())


def _caminho_lexico_sem_reparse(caminho: Path) -> bool:
    caminho_abs = os.path.abspath(os.fspath(caminho))
    caminho_real = os.path.realpath(caminho_abs)
    return os.path.normcase(os.path.normpath(caminho_real)) == os.path.normcase(
        os.path.normpath(caminho_abs)
    )


def _resolver_contexto(info_root: os.PathLike[str] | str, client_id: str) -> dict[str, Any]:
    client = str(client_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", client):
        _falhar("invalid_client_id", "O client_id informado e invalido.")
    raiz_informada = Path(info_root).expanduser().absolute()
    if _caminho_e_link(raiz_informada) or not _caminho_lexico_sem_reparse(
        raiz_informada
    ):
        _falhar("unsafe_info_root", "A raiz de dados informada e insegura.")
    raiz = raiz_informada.resolve()
    if not raiz.is_dir():
        _falhar("info_root_missing", "A raiz de dados informada nao existe.")
    tenant_informado = raiz / client
    if _caminho_e_link(tenant_informado):
        _falhar("unsafe_tenant_path", "A pasta do cliente e insegura.")
    tenant = tenant_informado.resolve()
    try:
        if os.path.commonpath([str(raiz), str(tenant)]) != str(raiz):
            _falhar("unsafe_tenant_path", "O cliente esta fora da raiz de dados.")
    except ValueError:
        _falhar("unsafe_tenant_path", "O cliente esta fora da raiz de dados.")
    if tenant.parent != raiz or not tenant.is_dir() or tenant.is_symlink():
        _falhar("tenant_missing", "A pasta canonica do cliente nao existe.")
    tenant_stat = os.stat(tenant, follow_symlinks=False)
    temporarios_controle = (
        (f".{CADASTRO_FOTOS_CONFIG_ARQUIVO}.", ".create.tmp"),
        (f".{MIGRACAO_JOURNAL_ARQUIVO}.", ".tmp"),
    )
    for entrada in tenant.iterdir():
        if any(
            entrada.name.startswith(prefixo) and entrada.name.endswith(sufixo)
            for prefixo, sufixo in temporarios_controle
        ):
            _falhar(
                "migration_temp_cleanup_pending",
                "Existe um arquivo temporario de controle que precisa ser removido.",
            )
    fotos = tenant / "cadastro_fotos"
    if fotos.exists() and (_caminho_e_link(fotos) or not fotos.is_dir()):
        _falhar("unsafe_photo_directory", "A pasta de fotos legadas e insegura.")
    lojas_fotos = fotos / "lojas"
    if lojas_fotos.exists() and (_caminho_e_link(lojas_fotos) or not lojas_fotos.is_dir()):
        _falhar("unsafe_store_photos_root", "A raiz de fotos por loja e insegura.")
    return {
        "info_root": raiz,
        "tenant": tenant,
        "tenant_object": {
            "st_dev": int(tenant_stat.st_dev),
            "st_ino": int(tenant_stat.st_ino),
        },
        "photos": fotos,
        "store_photos": lojas_fotos,
        "config": tenant / CADASTRO_FOTOS_CONFIG_ARQUIVO,
        "stores": tenant / "lojas_config.json",
        "journal": tenant / MIGRACAO_JOURNAL_ARQUIVO,
        "quarantine": fotos / MIGRACAO_QUARENTENA_PASTA,
        "lock": cadastro_fotos_transition_lock_path(tenant),
    }


def _lojas_configuradas(
    caminho: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not os.path.lexists(caminho):
        _falhar("stores_config_missing", "A configuracao de lojas nao existe.")
    if _caminho_e_link(caminho) or not caminho.is_file():
        _falhar("stores_config_unsafe", "A configuracao de lojas e insegura.")
    try:
        if caminho.stat().st_size > 4 * 1024 * 1024:
            _falhar("stores_config_too_large", "A configuracao de lojas excede o limite.")
        conteudo = caminho.read_bytes()
        if len(conteudo) > 4 * 1024 * 1024:
            _falhar("stores_config_too_large", "A configuracao de lojas excede o limite.")
        payload = json.loads(conteudo.decode("utf-8-sig"))
    except CadastroFotosMigracaoErro:
        raise
    except Exception as exc:
        raise CadastroFotosMigracaoErro(
            "stores_config_invalid",
            "Nao foi possivel validar a configuracao de lojas.",
        ) from exc
    if not isinstance(payload, list):
        _falhar("stores_config_invalid", "A configuracao de lojas e invalida.")
    store_ids: set[str] = set()
    lojas: list[dict[str, Any]] = []
    for loja in payload:
        if not isinstance(loja, dict):
            _falhar("stores_config_invalid", "A configuracao de lojas e invalida.")
        store_id = str(loja.get("store_id") or "").strip()
        if not store_id:
            continue
        if store_id in store_ids:
            _falhar("stores_config_invalid", "A configuracao de lojas possui store_id duplicado.")
        store_ids.add(store_id)
        lojas.append(dict(loja))
    return lojas, {
        "size": len(conteudo),
        "sha256": hashlib.sha256(conteudo).hexdigest(),
    }


def _store_ids_configurados(caminho: Path) -> tuple[set[str], dict[str, Any]]:
    lojas, estado = _lojas_configuradas(caminho)
    return {
        str(loja.get("store_id") or "").strip()
        for loja in lojas
        if str(loja.get("store_id") or "").strip()
    }, estado


def _config_desejada(store_ids: list[str], group_id: str) -> dict[str, Any]:
    grupo = str(group_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", grupo):
        _falhar("invalid_group_id", "O identificador do grupo compartilhado e invalido.")
    return {
        "schema": CADASTRO_FOTOS_CONFIG_SCHEMA,
        "strict_store_scope": True,
        "shared_groups": [{"group_id": grupo, "store_ids": list(store_ids)}],
    }


def _bytes_config(config: dict[str, Any]) -> bytes:
    return (json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _config_action(caminho: Path, desejada: dict[str, Any]) -> str:
    if not os.path.lexists(caminho):
        return "create"
    if _caminho_e_link(caminho) or not caminho.is_file():
        _falhar("photo_config_unsafe", "O destino da configuracao de fotos e inseguro.")
    try:
        with caminho.open("r", encoding="utf-8-sig") as arquivo:
            atual = json.load(arquivo)
    except Exception as exc:
        raise CadastroFotosMigracaoErro(
            "photo_config_invalid",
            "A configuracao de fotos existente e invalida.",
        ) from exc
    if atual != desejada:
        _falhar("photo_config_conflict", "A configuracao de fotos existente diverge do plano.")
    return "keep"


def _manifesto_portavel(
    manifesto: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        nome: {
            "size": int(item["size"]),
            "sha256": str(item["sha256"]),
        }
        for nome, item in sorted(manifesto.items(), key=lambda par: par[0].casefold())
    }


def _nome_manifesto_seguro(nome: str) -> bool:
    return (
        bool(nome)
        and nome not in {".", ".."}
        and Path(nome).name == nome
        and "/" not in nome
        and "\\" not in nome
        and Path(nome).suffix.casefold() in EXTENSOES_FOTO_SUPORTADAS
    )


def _manifesto_portavel_validar(payload: object) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict) or not payload:
        _falhar("migration_journal_invalid", "O journal da migracao e invalido.")
    manifesto: dict[str, dict[str, Any]] = {}
    nomes_casefold: set[str] = set()
    for nome, item in payload.items():
        if not isinstance(nome, str) or not _nome_manifesto_seguro(nome):
            _falhar("migration_journal_invalid", "O journal da migracao e invalido.")
        chave = nome.casefold()
        if chave in nomes_casefold:
            _falhar("migration_journal_invalid", "O journal da migracao e ambiguo.")
        nomes_casefold.add(chave)
        if not isinstance(item, dict) or set(item) != {"size", "sha256"}:
            _falhar("migration_journal_invalid", "O journal da migracao e invalido.")
        tamanho = item.get("size")
        sha256 = item.get("sha256")
        if (
            not isinstance(tamanho, int)
            or isinstance(tamanho, bool)
            or tamanho <= 0
            or not isinstance(sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        ):
            _falhar("migration_journal_invalid", "O journal da migracao e invalido.")
        manifesto[nome] = {"size": tamanho, "sha256": sha256}
    return manifesto


def _journal_payload(
    client_id: str,
    store_ids: list[str],
    group_id: str,
    manifesto: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": MIGRACAO_JOURNAL_SCHEMA,
        "client_id": str(client_id),
        "group_id": str(group_id),
        "store_ids": list(store_ids),
        "source_manifest": _manifesto_portavel(manifesto),
        "desired_config_sha256": hashlib.sha256(_bytes_config(config)).hexdigest(),
    }


def _journal_carregar(
    caminho: Path,
    *,
    client_id: str,
    store_ids: list[str],
    group_id: str,
    config: dict[str, Any],
) -> dict[str, dict[str, Any]] | None:
    if not os.path.lexists(caminho):
        return None
    if _caminho_e_link(caminho) or not caminho.is_file():
        _falhar("migration_journal_unsafe", "O journal da migracao e inseguro.")
    try:
        if caminho.stat().st_size > 8 * 1024 * 1024:
            _falhar("migration_journal_invalid", "O journal da migracao excede o limite.")
        with caminho.open("r", encoding="utf-8") as arquivo:
            payload = json.load(arquivo)
    except CadastroFotosMigracaoErro:
        raise
    except Exception as exc:
        raise CadastroFotosMigracaoErro(
            "migration_journal_invalid",
            "Nao foi possivel validar o journal da migracao.",
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "client_id",
        "group_id",
        "store_ids",
        "source_manifest",
        "desired_config_sha256",
    }:
        _falhar("migration_journal_invalid", "O journal da migracao e invalido.")
    esperado = {
        "schema": MIGRACAO_JOURNAL_SCHEMA,
        "client_id": str(client_id),
        "group_id": str(group_id),
        "store_ids": list(store_ids),
        "desired_config_sha256": hashlib.sha256(_bytes_config(config)).hexdigest(),
    }
    if any(payload.get(chave) != valor for chave, valor in esperado.items()):
        _falhar("migration_journal_conflict", "O journal diverge da migracao solicitada.")
    return _manifesto_portavel_validar(payload.get("source_manifest"))


def _quarentena_validar(
    pasta: Path,
    esperado: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    if not os.path.lexists(pasta):
        return {}
    if _caminho_e_link(pasta) or not pasta.is_dir():
        _falhar("unsafe_quarantine", "A quarentena da migracao e insegura.")
    manifesto = _manifesto_direto(pasta, obrigatorio=False)
    entradas = list(pasta.iterdir())
    if len(entradas) != len(manifesto):
        _falhar("quarantine_conflict", "A quarentena contem entradas inesperadas.")
    if esperado is not None:
        extras = set(manifesto) - set(esperado)
        divergentes = {
            nome
            for nome in set(manifesto) & set(esperado)
            if manifesto[nome]["size"] != esperado[nome]["size"]
            or manifesto[nome]["sha256"] != esperado[nome]["sha256"]
        }
        if extras or divergentes:
            _falhar("quarantine_conflict", "A quarentena diverge do journal.")
    return manifesto


def _estado_journal_validar(
    contexto: dict[str, Path],
    esperado: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    origem = _manifesto_direto(contexto["photos"], obrigatorio=False)
    quarentena = _quarentena_validar(contexto["quarantine"], esperado)
    if set(origem) & set(quarentena):
        _falhar("source_quarantine_conflict", "Uma foto existe na origem e na quarentena.")
    presentes = {**origem, **quarentena}
    if set(presentes) - set(esperado):
        _falhar("source_changed", "A origem ou quarentena contem fotos inesperadas.")
    for nome, item in presentes.items():
        if (
            item["size"] != esperado[nome]["size"]
            or item["sha256"] != esperado[nome]["sha256"]
        ):
            _falhar("source_changed", "Uma foto da migracao foi alterada.")
    return origem, quarentena


def _destinos_para_manifesto(
    contexto: dict[str, Path],
    store_ids: list[str],
    esperado: dict[str, dict[str, Any]],
    *,
    codigo: str,
) -> list[dict[str, Any]]:
    destinos: list[dict[str, Any]] = []
    for store_id in store_ids:
        segmento = _cadastro_store_id_foto_segmento(store_id)
        pasta = contexto["store_photos"] / segmento
        if pasta.exists() and (_caminho_e_link(pasta) or not pasta.is_dir()):
            _falhar("unsafe_store_photo_directory", "Uma pasta de loja e insegura.")
        atual = _manifesto_direto(pasta, obrigatorio=False)
        if not _manifestos_iguais(esperado, atual):
            _falhar(codigo, "Uma pasta de loja diverge do manifesto verificado.")
        destinos.append(
            {
                "store_id": store_id,
                "segment": segmento,
                "path": pasta,
                "missing": 0,
                "equal": len(esperado),
                "conflicts": 0,
                "current_manifest": _manifesto_portavel(atual),
            }
        )
    return destinos


def planejar_migracao_fotos_contexto(
    info_root: os.PathLike[str] | str,
    client_id: str,
    store_ids: Iterable[str],
    *,
    group_id: str = "uai-jk-carlos",
) -> dict[str, Any]:
    stores = _normalizar_store_ids(store_ids)
    contexto = _resolver_contexto(info_root, client_id)
    configurados, stores_config_state = _store_ids_configurados(contexto["stores"])
    if any(store_id not in configurados for store_id in stores):
        _falhar("store_not_found", "Um store_id do grupo nao pertence ao cliente.")
    desejada = _config_desejada(stores, group_id)
    action = _config_action(contexto["config"], desejada)
    journal = _journal_carregar(
        contexto["journal"],
        client_id=str(client_id),
        store_ids=stores,
        group_id=group_id,
        config=desejada,
    )

    if journal is not None:
        referencias_estado = _validar_referencias_fotos_legadas(
            contexto,
            journal,
            client_id=str(client_id),
        )
        origem, quarentena = _estado_journal_validar(contexto, journal)
        destinos = _destinos_para_manifesto(
            contexto,
            stores,
            journal,
            codigo="destination_changed",
        )
        plano = {
            "context": contexto,
            "source_manifest": journal,
            "source_current": origem,
            "quarantine_current": quarentena,
            "source_references_state": referencias_estado,
            "stores_config_state": stores_config_state,
            "desired_config": desejada,
            "config_action": action,
            "destinations": destinos,
            "state": "recovery_pending",
            "report": {
                "source_files": len(journal),
                "source_bytes": sum(item["size"] for item in journal.values()),
                "source_manifest_sha256": _digest_manifesto(journal),
                "stores": len(stores),
                "copies_required": 0,
                "copies_equal": len(journal) * len(stores),
                "conflicts": 0,
                "legacy_files_to_delete": len(origem) + len(quarentena),
                "config_action": action,
                "ready": True,
                "status": "recovery_pending",
            },
        }
        _materializacao_canonica_planejar(
            plano,
            client_id=str(client_id),
            store_ids=stores,
        )
        return plano

    quarentena = _quarentena_validar(contexto["quarantine"], None)
    if quarentena:
        _falhar("orphan_quarantine", "Existe quarentena sem journal de recuperacao.")
    origem = _manifesto_direto(contexto["photos"], obrigatorio=False)
    if not origem:
        manifestos_destino: list[dict[str, dict[str, Any]]] = []
        destinos_vazios: list[dict[str, Any]] = []
        for store_id in stores:
            segmento = _cadastro_store_id_foto_segmento(store_id)
            pasta = contexto["store_photos"] / segmento
            if pasta.exists() and (_caminho_e_link(pasta) or not pasta.is_dir()):
                _falhar("unsafe_store_photo_directory", "Uma pasta de loja e insegura.")
            atual = _manifesto_direto(pasta, obrigatorio=False)
            manifestos_destino.append(atual)
            destinos_vazios.append(
                {
                    "store_id": store_id,
                    "segment": segmento,
                    "path": pasta,
                    "missing": 0,
                    "equal": len(atual),
                    "conflicts": 0,
                    "current_manifest": _manifesto_portavel(atual),
                }
            )
        esperado = manifestos_destino[0] if manifestos_destino else {}
        if action == "keep" and esperado and all(
            _manifestos_iguais(esperado, atual) for atual in manifestos_destino[1:]
        ):
            referencias_estado = _validar_referencias_fotos_legadas(
                contexto,
                esperado,
                client_id=str(client_id),
            )
            plano = {
                "context": contexto,
                "source_manifest": _manifesto_portavel(esperado),
                "source_current": {},
                "quarantine_current": {},
                "source_references_state": referencias_estado,
                "stores_config_state": stores_config_state,
                "desired_config": desejada,
                "config_action": action,
                "destinations": destinos_vazios,
                "state": "already_migrated",
                "report": {
                    "source_files": len(esperado),
                    "source_bytes": sum(item["size"] for item in esperado.values()),
                    "source_manifest_sha256": _digest_manifesto(esperado),
                    "stores": len(stores),
                    "copies_required": 0,
                    "copies_equal": len(esperado) * len(stores),
                    "conflicts": 0,
                    "legacy_files_to_delete": 0,
                    "config_action": action,
                    "ready": True,
                    "status": "already_migrated",
                },
            }
            _materializacao_canonica_planejar(
                plano,
                client_id=str(client_id),
                store_ids=stores,
            )
            return plano
        if action == "keep" and any(manifestos_destino):
            _falhar("destination_conflict", "As pastas de loja divergem entre si.")
        _falhar("no_legacy_photos", "Nao ha fotos legadas diretas para migrar.")

    destinos: list[dict[str, Any]] = []
    referencias_estado = _validar_referencias_fotos_legadas(
        contexto,
        origem,
        client_id=str(client_id),
    )
    conflitos = 0
    copiar = 0
    iguais = 0
    for store_id in stores:
        segmento = _cadastro_store_id_foto_segmento(store_id)
        pasta = contexto["store_photos"] / segmento
        if pasta.exists() and (_caminho_e_link(pasta) or not pasta.is_dir()):
            _falhar("unsafe_store_photo_directory", "Uma pasta de loja e insegura.")
        atual = _manifesto_direto(pasta, obrigatorio=False)
        faltantes = set(origem) - set(atual)
        extras = set(atual) - set(origem)
        divergentes = {
            nome
            for nome in set(origem) & set(atual)
            if origem[nome]["size"] != atual[nome]["size"]
            or origem[nome]["sha256"] != atual[nome]["sha256"]
        }
        conflitos += len(extras) + len(divergentes)
        copiar += len(faltantes)
        iguais += len(origem) - len(faltantes) - len(divergentes)
        destinos.append(
            {
                "store_id": store_id,
                "segment": segmento,
                "path": pasta,
                "missing": len(faltantes),
                "equal": len(origem) - len(faltantes) - len(divergentes),
                "conflicts": len(extras) + len(divergentes),
                "current_manifest": _manifesto_portavel(atual),
            }
        )
    if conflitos:
        _falhar("destination_conflict", "Uma pasta de loja contem fotos divergentes ou extras.")

    plano = {
        "context": contexto,
        "source_manifest": origem,
        "source_current": origem,
        "quarantine_current": {},
        "source_references_state": referencias_estado,
        "stores_config_state": stores_config_state,
        "desired_config": desejada,
        "config_action": action,
        "destinations": destinos,
        "state": "ready",
        "report": {
            "source_files": len(origem),
            "source_bytes": sum(item["size"] for item in origem.values()),
            "source_manifest_sha256": _digest_manifesto(origem),
            "stores": len(stores),
            "copies_required": copiar,
            "copies_equal": iguais,
            "conflicts": conflitos,
            "legacy_files_to_delete": len(origem),
            "config_action": action,
            "ready": True,
        },
    }
    _materializacao_canonica_planejar(
        plano,
        client_id=str(client_id),
        store_ids=stores,
    )
    return plano


def _plano_aprovacao_sha256(
    planos: list[dict[str, Any]],
    *,
    client_id: str,
    store_ids: list[str],
    group_id: str,
) -> str:
    """Vincula o apply ao estado exato exibido no dry-run, sem expor paths."""

    contextos: list[dict[str, Any]] = []
    for plano in planos:
        tenant_normalizado = os.path.normcase(
            os.path.normpath(str(plano["context"]["tenant"]))
        )
        contextos.append(
            {
                "tenant_identity_sha256": hashlib.sha256(
                    tenant_normalizado.encode("utf-8")
                ).hexdigest(),
                "tenant_object": plano["context"]["tenant_object"],
                "state": plano["state"],
                "source_manifest": _manifesto_portavel(plano["source_manifest"]),
                "source_current": _manifesto_portavel(plano.get("source_current", {})),
                "quarantine_current": _manifesto_portavel(
                    plano.get("quarantine_current", {})
                ),
                "source_references_state": plano.get("source_references_state", {}),
                "stores_config_state": plano["stores_config_state"],
                "desired_config": plano["desired_config"],
                "config_action": plano["config_action"],
                "destinations": [
                    {
                        chave: destino[chave]
                        for chave in (
                            "store_id",
                            "segment",
                            "missing",
                            "equal",
                            "conflicts",
                        )
                    }
                    | {"current_manifest": destino.get("current_manifest", {})}
                    for destino in plano["destinations"]
                ],
                "report": plano["report"],
            }
        )
    payload = {
        "schema": MIGRACAO_PLANO_SCHEMA,
        "client_id": str(client_id),
        "store_ids": list(store_ids),
        "group_id": str(group_id),
        "contexts": contextos,
    }
    serializado = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serializado).hexdigest()


def planejar_migracao_fotos_contextos(
    info_roots: Iterable[os.PathLike[str] | str],
    client_id: str,
    store_ids: Iterable[str],
    *,
    group_id: str = "uai-jk-carlos",
) -> dict[str, Any]:
    roots = list(info_roots)
    if not roots:
        _falhar("missing_contexts", "Informe ao menos uma raiz de dados.")
    stores = _normalizar_store_ids(store_ids)
    planos = [
        planejar_migracao_fotos_contexto(root, client_id, stores, group_id=group_id)
        for root in roots
    ]
    tenants_normalizados = {
        os.path.normcase(str(item["context"]["tenant"])) for item in planos
    }
    if len(tenants_normalizados) != len(planos):
        _falhar("duplicate_context", "A mesma pasta de cliente foi informada mais de uma vez.")
    report: dict[str, Any] = {
        "client_id": str(client_id),
        "contexts": [item["report"] for item in planos],
        "total_copies_required": sum(item["report"]["copies_required"] for item in planos),
        "total_legacy_files_to_delete": sum(
            item["report"]["legacy_files_to_delete"] for item in planos
        ),
        "ready": True,
    }
    if all(item["state"] == "already_migrated" for item in planos):
        report["status"] = "already_migrated"
    report["plan_sha256"] = _plano_aprovacao_sha256(
        planos,
        client_id=str(client_id),
        store_ids=stores,
        group_id=group_id,
    )
    return {
        "plans": planos,
        "report": report,
    }


def _escrever_atomico(caminho: Path, conteudo: bytes) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{caminho.name}.",
            suffix=".tmp",
            dir=caminho.parent,
            delete=False,
        ) as arquivo:
            temporario = arquivo.name
            arquivo.write(conteudo)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, caminho)
        temporario = ""
        _fsync_diretorio(caminho.parent)
    finally:
        if temporario:
            try:
                os.unlink(temporario)
            except OSError as exc:
                raise CadastroFotosMigracaoErro(
                    "migration_temp_cleanup_failed",
                    "A operacao foi interrompida porque um arquivo temporario nao pode ser removido.",
                ) from exc


def _criar_config_atomica_se_ausente(
    caminho: Path,
    desejada: dict[str, Any],
) -> bool:
    """Publica uma config completa sem nunca sobrescrever um ator concorrente."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{caminho.name}.",
            suffix=".create.tmp",
            dir=caminho.parent,
            delete=False,
        ) as arquivo:
            temporario = arquivo.name
            arquivo.write(_bytes_config(desejada))
            arquivo.flush()
            os.fsync(arquivo.fileno())
        try:
            # hard-link no mesmo diretorio fornece publicacao fail-if-exists
            # tanto no Windows/NTFS quanto em POSIX, sem janela de overwrite.
            os.link(temporario, caminho)
        except FileExistsError:
            acao = _config_action(caminho, desejada)
            if acao == "keep":
                return False
            _falhar("photo_config_race", "A configuracao mudou durante a publicacao.")
        except OSError as exc:
            raise CadastroFotosMigracaoErro(
                "photo_config_atomic_create_failed",
                "Nao foi possivel publicar a configuracao sem sobrescrever dados.",
            ) from exc
        _fsync_diretorio(caminho.parent)
        if _config_action(caminho, desejada) != "keep":
            _falhar("photo_config_race", "A configuracao mudou durante a publicacao.")
        return True
    finally:
        if temporario:
            try:
                os.unlink(temporario)
            except OSError as exc:
                raise CadastroFotosMigracaoErro(
                    "migration_temp_cleanup_failed",
                    "A operacao foi interrompida porque um arquivo temporario nao pode ser removido.",
                ) from exc


def _fsync_diretorio(pasta: Path) -> None:
    try:
        descriptor = os.open(str(pasta), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


@contextmanager
def _bloquear_tenant(contexto: dict[str, Any]) -> Iterator[None]:
    try:
        with bloquear_transicao_fotos_tenant(
            contexto["tenant"],
            timeout_seconds=0,
        ):
            yield
    except CadastroFotosCoordenacaoErro as exc:
        if exc.code == "locked":
            raise CadastroFotosMigracaoErro(
                "migration_locked",
                "Ja existe uma atualizacao de fotos em andamento para este cliente.",
            ) from exc
        code = (
            "migration_lock_unavailable"
            if exc.code == "lock_unavailable"
            else "migration_lock_unsafe"
        )
        raise CadastroFotosMigracaoErro(
            code,
            "Nao foi possivel coordenar a migracao de fotos.",
        ) from exc


@contextmanager
def _bloquear_contextos(contextos: list[dict[str, Any]]) -> Iterator[None]:
    ordenados = sorted(contextos, key=lambda item: os.path.normcase(str(item["tenant"])))
    with ExitStack() as pilha:
        for contexto in ordenados:
            pilha.enter_context(_bloquear_tenant(contexto))
        yield


def _copiar_foto_atomica(origem: Path, destino: Path, esperado: dict[str, Any]) -> bool:
    if destino.exists():
        if (
            destino.is_file()
            and not destino.is_symlink()
            and destino.stat().st_size == esperado["size"]
            and _sha256_arquivo(destino) == esperado["sha256"]
        ):
            return False
        _falhar("destination_changed", "Um destino mudou durante a migracao.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".cadastro-foto-migracao.",
            suffix=".tmp",
            dir=destino.parent,
            delete=False,
        ) as arquivo_destino, origem.open("rb") as arquivo_origem:
            temporario = arquivo_destino.name
            for bloco in iter(lambda: arquivo_origem.read(1024 * 1024), b""):
                arquivo_destino.write(bloco)
            arquivo_destino.flush()
            os.fsync(arquivo_destino.fileno())
        temporario_path = Path(temporario)
        if (
            temporario_path.stat().st_size != esperado["size"]
            or _sha256_arquivo(temporario_path) != esperado["sha256"]
        ):
            _falhar("copy_verification_failed", "Uma copia nao passou na verificacao de hash.")
        try:
            # Publicacao fail-if-exists: nunca sobrescreve um upload por-loja
            # que tenha surgido entre o precheck e a publicacao da copia.
            os.link(temporario, destino)
        except FileExistsError:
            if (
                destino.is_file()
                and not destino.is_symlink()
                and destino.stat().st_size == esperado["size"]
                and _sha256_arquivo(destino) == esperado["sha256"]
            ):
                return False
            _falhar("destination_changed", "Um destino mudou durante a migracao.")
        except OSError as exc:
            raise CadastroFotosMigracaoErro(
                "copy_atomic_create_failed",
                "Nao foi possivel publicar a copia sem sobrescrever dados.",
            ) from exc
        _fsync_diretorio(destino.parent)
        if (
            not destino.is_file()
            or destino.is_symlink()
            or destino.stat().st_size != esperado["size"]
            or _sha256_arquivo(destino) != esperado["sha256"]
        ):
            _falhar("destination_changed", "Um destino mudou durante a migracao.")
        return True
    finally:
        if temporario:
            try:
                os.unlink(temporario)
            except OSError as exc:
                raise CadastroFotosMigracaoErro(
                    "migration_temp_cleanup_failed",
                    "A copia foi interrompida porque um arquivo temporario nao pode ser removido.",
                ) from exc


def _journal_escrever(
    plano: dict[str, Any],
    *,
    client_id: str,
    store_ids: list[str],
    group_id: str,
) -> None:
    payload = _journal_payload(
        client_id,
        store_ids,
        group_id,
        plano["source_manifest"],
        plano["desired_config"],
    )
    serializado = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    # O journal e deliberadamente portavel: nenhum caminho do host e persistido.
    _escrever_atomico(plano["context"]["journal"], serializado)


def _contextos_resolver_unicos(
    roots: list[os.PathLike[str] | str],
    client_id: str,
) -> list[dict[str, Any]]:
    contextos = [_resolver_contexto(root, client_id) for root in roots]
    tenants = {os.path.normcase(str(item["tenant"])) for item in contextos}
    if len(tenants) != len(contextos):
        _falhar("duplicate_context", "A mesma pasta de cliente foi informada mais de uma vez.")
    return contextos


def _revalidar_plano_antes_exclusao(
    plano: dict[str, Any],
    *,
    client_id: str,
    store_ids: list[str],
    group_id: str,
) -> None:
    contexto = plano["context"]
    atual = _resolver_contexto(contexto["info_root"], client_id)
    if (
        os.path.normcase(str(atual["tenant"]))
        != os.path.normcase(str(contexto["tenant"]))
        or atual["tenant_object"] != contexto["tenant_object"]
    ):
        _falhar("context_changed", "O contexto da migracao mudou antes da exclusao.")
    configurados, stores_config_state = _store_ids_configurados(atual["stores"])
    if stores_config_state != plano["stores_config_state"]:
        _falhar("stores_config_changed", "A configuracao de lojas mudou durante a migracao.")
    if any(store_id not in configurados for store_id in store_ids):
        _falhar("store_not_found", "Um store_id deixou de pertencer ao cliente.")
    desejada = _config_desejada(store_ids, group_id)
    if desejada != plano["desired_config"] or _config_action(atual["config"], desejada) != "keep":
        _falhar("photo_config_changed", "A configuracao mudou antes da exclusao.")
    esperado = plano["source_manifest"]
    referencias_esperadas = plano.get("source_references_state")
    if referencias_esperadas is not None:
        referencias_atuais = _validar_referencias_fotos_legadas(
            atual,
            esperado,
            client_id=str(client_id),
        )
        if referencias_atuais != referencias_esperadas:
            _falhar(
                "photo_references_changed",
                "As referencias de foto mudaram durante a migracao.",
            )
    if plano["state"] != "already_migrated":
        journal = _journal_carregar(
            atual["journal"],
            client_id=str(client_id),
            store_ids=store_ids,
            group_id=group_id,
            config=desejada,
        )
        if journal is None or not _manifestos_iguais(esperado, journal):
            _falhar("migration_journal_changed", "O journal mudou antes da exclusao.")
        _estado_journal_validar(atual, esperado)
    else:
        origem = _manifesto_direto(atual["photos"], obrigatorio=False)
        quarentena = _quarentena_validar(atual["quarantine"], None)
        if origem or quarentena:
            _falhar("legacy_state_changed", "A origem concluida mudou antes da exclusao.")
    _destinos_para_manifesto(
        atual,
        store_ids,
        esperado,
        codigo="destination_changed",
    )


def _revalidar_foto_imediatamente_antes_unlink(
    plano: dict[str, Any],
    *,
    client_id: str,
    store_ids: list[str],
    group_id: str,
    nome: str,
    esperado: dict[str, Any],
) -> None:
    contexto = plano["context"]
    atual = _resolver_contexto(contexto["info_root"], client_id)
    if atual["tenant_object"] != contexto["tenant_object"]:
        _falhar("context_changed", "O contexto da migracao mudou antes da exclusao.")
    configurados, stores_config_state = _store_ids_configurados(atual["stores"])
    if stores_config_state != plano["stores_config_state"]:
        _falhar("stores_config_changed", "A configuracao de lojas mudou durante a migracao.")
    if any(store_id not in configurados for store_id in store_ids):
        _falhar("store_not_found", "Um store_id deixou de pertencer ao cliente.")
    desejada = _config_desejada(store_ids, group_id)
    if desejada != plano["desired_config"] or _config_action(atual["config"], desejada) != "keep":
        _falhar("photo_config_changed", "A configuracao mudou antes da exclusao.")
    for store_id in store_ids:
        pasta = atual["store_photos"] / _cadastro_store_id_foto_segmento(store_id)
        if _caminho_e_link(pasta) or not pasta.is_dir():
            _falhar("destination_changed", "Uma pasta de loja mudou antes da exclusao.")
        destino = pasta / nome
        if (
            not destino.is_file()
            or destino.is_symlink()
            or destino.stat().st_size != esperado["size"]
            or _sha256_arquivo(destino) != esperado["sha256"]
        ):
            _falhar("destination_changed", "Uma foto de destino mudou antes da exclusao.")
    referencias_esperadas = plano.get("source_references_state")
    if referencias_esperadas is not None:
        referencias_atuais = _validar_referencias_fotos_legadas(
            atual,
            plano["source_manifest"],
            client_id=str(client_id),
        )
        if referencias_atuais != referencias_esperadas:
            _falhar(
                "photo_references_changed",
                "As referencias de foto mudaram durante a migracao.",
            )


def _remover_quarentena_vazia_concluida(plano: dict[str, Any]) -> None:
    quarentena = plano["context"]["quarantine"]
    if quarentena.is_dir() and not any(quarentena.iterdir()):
        try:
            quarentena.rmdir()
            _fsync_diretorio(quarentena.parent)
        except OSError as exc:
            raise CadastroFotosMigracaoErro(
                "legacy_cleanup_pending",
                "O estado esta migrado, mas a quarentena vazia ainda precisa ser removida.",
            ) from exc
    if os.path.lexists(quarentena):
        _falhar(
            "legacy_cleanup_pending",
            "O estado esta migrado, mas a quarentena vazia ainda precisa ser removida.",
        )


def _revalidar_plano_concluido_sem_residuos(
    plano: dict[str, Any],
    *,
    client_id: str,
    store_ids: list[str],
    group_id: str,
) -> None:
    """Confirma o estado final imediatamente antes de declarar sucesso."""

    contexto = plano["context"]
    atual = _resolver_contexto(contexto["info_root"], client_id)
    if (
        os.path.normcase(str(atual["tenant"]))
        != os.path.normcase(str(contexto["tenant"]))
        or atual["tenant_object"] != contexto["tenant_object"]
    ):
        _falhar("context_changed", "O contexto da migracao mudou antes da conclusao.")
    configurados, stores_config_state = _store_ids_configurados(atual["stores"])
    if stores_config_state != plano["stores_config_state"]:
        _falhar("stores_config_changed", "A configuracao de lojas mudou durante a migracao.")
    if any(store_id not in configurados for store_id in store_ids):
        _falhar("store_not_found", "Um store_id deixou de pertencer ao cliente.")
    desejada = _config_desejada(store_ids, group_id)
    if (
        desejada != plano["desired_config"]
        or _config_action(atual["config"], desejada) != "keep"
    ):
        _falhar("photo_config_changed", "A configuracao mudou antes da conclusao.")
    if os.path.lexists(atual["journal"]) or os.path.lexists(atual["quarantine"]):
        _falhar(
            "legacy_cleanup_pending",
            "A migracao terminou, mas ainda existe um artefato de limpeza.",
        )
    if _manifesto_direto(atual["photos"], obrigatorio=False):
        _falhar(
            "legacy_cleanup_pending",
            "A migracao terminou, mas ainda existe uma foto na pasta legada.",
        )
    referencias_esperadas = plano.get("source_references_state")
    if referencias_esperadas is not None:
        referencias_atuais = _validar_referencias_fotos_legadas(
            atual,
            plano["source_manifest"],
            client_id=str(client_id),
        )
        if referencias_atuais != referencias_esperadas:
            _falhar(
                "photo_references_changed",
                "As referencias de foto mudaram antes da conclusao.",
            )
    _destinos_para_manifesto(
        atual,
        store_ids,
        plano["source_manifest"],
        codigo="destination_changed",
    )


def _resultado_execucao(
    *,
    status: str,
    client_id: str,
    planos: list[dict[str, Any]],
    stores: list[str],
    group_id: str,
    copiados: int,
    ignorados: int,
    excluidos: int,
) -> dict[str, Any]:
    return {
        "success": True,
        "status": status,
        "client_id": str(client_id),
        "contexts": len(planos),
        "stores": len(stores),
        "copied": copiados,
        "already_equal": ignorados,
        "legacy_deleted": excluidos,
        "strict_store_scope": True,
        "shared_group": str(group_id),
    }


def _referencia_foto_scoped_canonica(
    valor: object,
    *,
    client_id: str,
) -> str | None:
    """Normalize one local store reference; return None for true externals."""

    if not _cadastro_foto_referencia_local_cadastro(valor):
        return None
    caminho = _cadastro_foto_referencia_limpar_wrappers(valor).replace("\\", "/")
    for _ in range(3):
        decodificado = unquote(caminho)
        if decodificado == caminho:
            break
        caminho = decodificado.replace("\\", "/")
    if caminho.startswith("//") or re.match(r"^https?:", caminho, re.IGNORECASE):
        try:
            caminho = str(
                urlsplit(caminho if not caminho.startswith("//") else f"https:{caminho}").path
                or ""
            ).replace("\\", "/")
        except ValueError:
            return ""
    caminho = caminho.split("?", 1)[0].split("#", 1)[0]
    caminho_fold = caminho.casefold()
    prefixo_arquivo = "/api/cadastro/foto-arquivo/"
    prefixo_tenant = "/api/cadastro/foto/"
    if caminho_fold.startswith(prefixo_arquivo):
        caminho = caminho[len(prefixo_arquivo):]
    elif caminho_fold.startswith(prefixo_tenant):
        partes = caminho[len(prefixo_tenant):].strip("/").split("/")
        if len(partes) < 3 or partes[0] != str(client_id):
            return ""
        caminho = "/".join(partes[1:])
    caminho = caminho.strip("/")
    marcador = "cadastro_fotos/lojas/"
    indice = caminho.casefold().rfind(marcador)
    if indice >= 0:
        caminho = caminho[indice:]
    elif caminho.casefold().startswith("lojas/"):
        caminho = f"cadastro_fotos/{caminho}"
    else:
        return ""
    partes = caminho.split("/")
    if (
        len(partes) != 4
        or partes[0].casefold() != "cadastro_fotos"
        or partes[1].casefold() != "lojas"
        or any(not parte or parte in {".", ".."} for parte in partes)
    ):
        return ""
    return "/".join(partes)


def _materializacao_canonica_planejar(
    plano: dict[str, Any],
    *,
    client_id: str,
    store_ids: list[str],
    evento_utc: str = "",
) -> dict[str, Any]:
    """Prepare canonical active rows for every proven store/SKU association."""

    from fastapi import HTTPException

    from backend.services.cadastro_lojas_produtos import (
        _contexto_legado_de_tenant,
        _ler_registros_persistidos_caminho,
    )

    contexto = plano["context"]
    caminho = contexto["tenant"] / "cadastro_produtos_lojas.csv"
    try:
        lojas, _estado_lojas = _lojas_configuradas(contexto["stores"])
        lojas_por_id = {
            str(loja.get("store_id") or "").strip(): loja
            for loja in lojas
            if str(loja.get("store_id") or "").strip()
        }
        registros, colunas = _ler_registros_persistidos_caminho(caminho)
    except CadastroFotosMigracaoErro:
        raise
    except HTTPException as exc:
        raise CadastroFotosMigracaoErro(
            "canonical_product_catalog_invalid",
            "Nao foi possivel validar o cadastro canonico por loja.",
        ) from exc

    manifesto = plano["source_manifest"]
    chaves_existentes = {
        (
            str(item.get("store_id") or "").strip(),
            _normalizar_sku_mes(
                item.get("sku_normalizado") or item.get("sku") or ""
            ).strip().upper(),
        )
        for item in registros
    }
    criados = 0
    atualizados = 0
    instante = evento_utc or "__CADASTRO_PHOTO_EVENT__"

    def materializar_registro(registro: dict[str, Any], nome_foto: str) -> bool:
        nonlocal atualizados
        store_id = str(registro.get("store_id") or "").strip()
        segmento = _cadastro_store_id_foto_segmento(store_id)
        desejada = f"cadastro_fotos/lojas/{segmento}/{nome_foto}"
        for coluna, referencia in list(registro.items()):
            if not _cadastro_foto_coluna_candidata(coluna) or not str(
                referencia or ""
            ).strip():
                continue
            normalizada = _referencia_foto_scoped_canonica(
                referencia,
                client_id=str(client_id),
            )
            if normalizada is not None and normalizada != desejada:
                _falhar(
                    "canonical_photo_reference_conflict",
                    "Uma linha canonica aponta para uma foto local divergente.",
                )
        foto_atual = str(registro.get("foto") or "").strip()
        if foto_atual:
            # External references remain explicit and do not authorize the
            # local bytes.  A matching scoped reference is already idempotent.
            return False
        try:
            versao = max(1, int(str(registro.get("row_version") or "1")))
        except (TypeError, ValueError):
            _falhar(
                "canonical_product_catalog_invalid",
                "Uma linha canonica possui row_version invalida.",
            )
        registro["foto"] = desejada
        registro["row_version"] = str(versao + 1)
        registro["updated_at_utc"] = instante
        atualizados += 1
        return True

    for registro in registros:
        store_id = str(registro.get("store_id") or "").strip()
        if store_id not in store_ids or str(
            registro.get("deleted_at_utc") or ""
        ).strip():
            continue
        sku = registro.get("sku_normalizado") or registro.get("sku") or ""
        nome_foto = _foto_preferida_resolver(manifesto, sku)
        if nome_foto:
            materializar_registro(registro, nome_foto)

    for store_id in store_ids:
        loja = lojas_por_id.get(store_id)
        if loja is None:
            _falhar("store_not_found", "Um store_id do grupo nao pertence ao cliente.")
        try:
            legado = _contexto_legado_de_tenant(
                str(client_id),
                {
                    "store_id": store_id,
                    "nome": str(loja.get("nome") or "").strip(),
                },
                contexto["tenant"],
                lojas,
                fotos={},
            )
        except HTTPException as exc:
            raise CadastroFotosMigracaoErro(
                "legacy_product_association_invalid",
                "Nao foi possivel comprovar as associacoes legadas por loja.",
            ) from exc
        for sku, sombra in sorted(legado.get("sombras", {}).items()):
            chave = (store_id, str(sku))
            if chave in chaves_existentes:
                # Inclusive tombstones are authoritative and are never
                # resurrected by a filesystem migration.
                continue
            nome_foto = _foto_preferida_resolver(manifesto, sku)
            if not nome_foto:
                continue
            derivados = legado.get("campos_derivados_sombras", {}).get(sku, set())
            novo = {
                str(chave_campo): str(valor or "")
                for chave_campo, valor in sombra.items()
                if chave_campo
                not in {
                    "scope_source",
                    "row_version",
                    "updated_at_utc",
                    "deleted_at_utc",
                }
                and chave_campo not in derivados
            }
            novo.update(
                {
                    "store_id": store_id,
                    "sku": _normalizar_sku_mes(novo.get("sku") or sku),
                    "sku_normalizado": str(sku),
                    "loja_sync": str(loja.get("nome") or "").strip(),
                    "row_version": "1",
                    "updated_at_utc": instante,
                    "deleted_at_utc": "",
                    "foto": (
                        f"cadastro_fotos/lojas/"
                        f"{_cadastro_store_id_foto_segmento(store_id)}/{nome_foto}"
                    ),
                }
            )
            registros.append(novo)
            chaves_existentes.add(chave)
            criados += 1

    return {
        "path": caminho,
        "records": registros,
        "columns": colunas,
        "created": criados,
        "updated": atualizados,
        "changed": bool(criados or atualizados),
    }


def _materializar_cadastros_canonicos_transacional(
    planos: list[dict[str, Any]],
    *,
    client_id: str,
    store_ids: list[str],
) -> dict[str, int]:
    """Commit every context or restore every canonical CSV byte-for-byte."""

    from fastapi import HTTPException

    from backend.services.cadastro_lojas_produtos import (
        _capturar_estados_arquivos,
        _rollback_arquivos,
        _salvar_registros_atomico_caminho,
    )

    evento = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    preparadas = [
        _materializacao_canonica_planejar(
            plano,
            client_id=str(client_id),
            store_ids=store_ids,
            evento_utc=evento,
        )
        for plano in planos
    ]
    estados = _capturar_estados_arquivos(
        [str(item["path"]) for item in preparadas if item["changed"]]
    )
    try:
        for item in preparadas:
            if item["changed"]:
                _salvar_registros_atomico_caminho(
                    item["path"],
                    item["records"],
                    item["columns"],
                )
    except BaseException as exc:
        try:
            _rollback_arquivos(estados)
        except HTTPException as rollback_exc:
            raise CadastroFotosMigracaoErro(
                "canonical_materialization_rollback_failed",
                "Falha critica ao reverter a materializacao canonica.",
            ) from rollback_exc
        if isinstance(exc, CadastroFotosMigracaoErro):
            raise
        raise CadastroFotosMigracaoErro(
            "canonical_materialization_failed",
            "Nao foi possivel materializar as fotos no cadastro canonico.",
        ) from exc
    return {
        "created": sum(int(item["created"]) for item in preparadas),
        "updated": sum(int(item["updated"]) for item in preparadas),
    }


def executar_migracao_fotos_contextos(
    info_roots: Iterable[os.PathLike[str] | str],
    client_id: str,
    store_ids: Iterable[str],
    *,
    group_id: str = "uai-jk-carlos",
    delete_legacy: bool = False,
    expected_plan_sha256: str | None = None,
) -> dict[str, Any]:
    if not delete_legacy:
        _falhar(
            "delete_confirmation_required",
            "A execucao exige confirmacao explicita para excluir as fotos legadas.",
        )
    esperado_plano = str(expected_plan_sha256 or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", esperado_plano):
        _falhar(
            "plan_confirmation_required",
            "A execucao exige o SHA-256 exato emitido pelo dry-run.",
        )
    roots = list(info_roots)
    if not roots:
        _falhar("missing_contexts", "Informe ao menos uma raiz de dados.")
    stores = _normalizar_store_ids(store_ids)
    contextos = _contextos_resolver_unicos(roots, client_id)
    with _bloquear_contextos(contextos):
        planejamento = planejar_migracao_fotos_contextos(
            roots,
            client_id,
            stores,
            group_id=group_id,
        )
        plano_atual_sha256 = planejamento["report"]["plan_sha256"]
        if not hmac.compare_digest(esperado_plano, plano_atual_sha256):
            _falhar(
                "migration_plan_changed",
                "O estado mudou desde o dry-run; gere e aprove um novo plano.",
            )
        planos = planejamento["plans"]
        copiados = 0
        ignorados = 0

        if all(plano["state"] == "already_migrated" for plano in planos):
            _materializar_cadastros_canonicos_transacional(
                planos,
                client_id=str(client_id),
                store_ids=stores,
            )
            for plano in planos:
                plano["source_references_state"] = _validar_referencias_fotos_legadas(
                    plano["context"],
                    plano["source_manifest"],
                    client_id=str(client_id),
                )
            for plano in planos:
                _revalidar_plano_antes_exclusao(
                    plano,
                    client_id=str(client_id),
                    store_ids=stores,
                    group_id=group_id,
                )
                _remover_quarentena_vazia_concluida(plano)
                _revalidar_plano_concluido_sem_residuos(
                    plano,
                    client_id=str(client_id),
                    store_ids=stores,
                    group_id=group_id,
                )
            return _resultado_execucao(
                status="already_migrated",
                client_id=str(client_id),
                planos=planos,
                stores=stores,
                group_id=group_id,
                copiados=0,
                ignorados=sum(
                    len(plano["source_manifest"]) * len(stores) for plano in planos
                ),
                excluidos=0,
            )

        # Em uma execucao com mais de uma raiz pode haver contextos ja
        # concluidos ao lado de outros ainda prontos/retomaveis. Limpa a
        # quarentena vazia desses contextos antes de continuar.
        for plano in planos:
            if plano["state"] == "already_migrated":
                _revalidar_plano_antes_exclusao(
                    plano,
                    client_id=str(client_id),
                    store_ids=stores,
                    group_id=group_id,
                )
                _remover_quarentena_vazia_concluida(plano)

        for plano in planos:
            if plano["state"] != "ready":
                ignorados += len(plano["source_manifest"]) * len(stores)
                continue
            origem = plano["source_manifest"]
            for destino in plano["destinations"]:
                pasta = destino["path"]
                for nome, esperado in origem.items():
                    if _copiar_foto_atomica(esperado["path"], pasta / nome, esperado):
                        copiados += 1
                    else:
                        ignorados += 1

        # Nenhum journal e criado antes de todas as origens e todos os destinos
        # terem sido novamente verificados em todos os contextos.
        for plano in planos:
            if plano["state"] == "already_migrated":
                continue
            if plano["state"] == "ready":
                origem_atual = _manifesto_direto(plano["context"]["photos"])
                if not _manifestos_iguais(plano["source_manifest"], origem_atual):
                    _falhar("source_changed", "As fotos de origem mudaram durante a copia.")
            else:
                _estado_journal_validar(plano["context"], plano["source_manifest"])
            _destinos_para_manifesto(
                plano["context"],
                stores,
                plano["source_manifest"],
                codigo="destination_verification_failed",
            )

        for plano in planos:
            if plano["state"] == "ready":
                _journal_escrever(
                    plano,
                    client_id=str(client_id),
                    store_ids=stores,
                    group_id=group_id,
                )

        # A configuracao e gravada somente depois do journal; qualquer falha a
        # partir daqui deixa um estado retomavel, sem depender de rollback.
        for plano in planos:
            if plano["state"] != "already_migrated":
                config_path = plano["context"]["config"]
                acao_atual = _config_action(config_path, plano["desired_config"])
                if acao_atual == "create":
                    _criar_config_atomica_se_ausente(
                        config_path,
                        plano["desired_config"],
                    )

        # O CSV canonico e a autorizacao versionada dos bytes no Shared Sync.
        # Materializa todas as associacoes comprovadas (inclusive quando o
        # arquivo ainda nao existia) depois do fan-out verificado e antes do
        # primeiro move/descarte legado. A escrita multi-contexto possui
        # rollback byte-a-byte proprio.
        _materializar_cadastros_canonicos_transacional(
            planos,
            client_id=str(client_id),
            store_ids=stores,
        )
        for plano in planos:
            plano["source_references_state"] = _validar_referencias_fotos_legadas(
                plano["context"],
                plano["source_manifest"],
                client_id=str(client_id),
            )

        # Mover para a quarentena ja altera o legado. Portanto, todos os
        # contextos passam por uma barreira completa antes do primeiro move.
        for plano in planos:
            _revalidar_plano_antes_exclusao(
                plano,
                client_id=str(client_id),
                store_ids=stores,
                group_id=group_id,
            )

        for plano in planos:
            if plano["state"] == "already_migrated":
                continue
            _revalidar_plano_antes_exclusao(
                plano,
                client_id=str(client_id),
                store_ids=stores,
                group_id=group_id,
            )
            contexto = plano["context"]
            esperado = plano["source_manifest"]
            journal_atual = _journal_carregar(
                contexto["journal"],
                client_id=str(client_id),
                store_ids=stores,
                group_id=group_id,
                config=plano["desired_config"],
            )
            if journal_atual is None or not _manifestos_iguais(esperado, journal_atual):
                _falhar("migration_journal_changed", "O journal mudou antes da quarentena.")
            origem_atual, _ = _estado_journal_validar(contexto, esperado)
            quarentena = contexto["quarantine"]
            if origem_atual:
                quarentena.mkdir(parents=False, exist_ok=True)
                if _caminho_e_link(quarentena) or not quarentena.is_dir():
                    _falhar("unsafe_quarantine", "A quarentena da migracao e insegura.")
            for nome, item in origem_atual.items():
                origem_path = contexto["photos"] / nome
                temporaria = quarentena / nome
                if temporaria.exists():
                    _falhar("source_quarantine_conflict", "Uma foto ja existe na quarentena.")
                if (
                    not origem_path.is_file()
                    or origem_path.is_symlink()
                    or origem_path.stat().st_size != item["size"]
                    or _sha256_arquivo(origem_path) != item["sha256"]
                ):
                    _falhar("source_changed", "Uma foto mudou antes de ir para a quarentena.")
                os.replace(origem_path, temporaria)
            _fsync_diretorio(contexto["photos"])
            if quarentena.is_dir():
                _fsync_diretorio(quarentena)

        # Esta e a ultima barreira antes do primeiro unlink. Revalida lojas,
        # configuracao, journal, estado legado e os tres destinos em conjunto.
        for plano in planos:
            _revalidar_plano_antes_exclusao(
                plano,
                client_id=str(client_id),
                store_ids=stores,
                group_id=group_id,
            )

        excluidos = 0
        limpeza_pendente = False
        for plano in planos:
            if plano["state"] == "already_migrated":
                continue
            contexto = plano["context"]
            quarentena = contexto["quarantine"]
            _, manifesto_quarentena = _estado_journal_validar(
                contexto,
                plano["source_manifest"],
            )
            for nome, item in manifesto_quarentena.items():
                temporaria = quarentena / nome
                try:
                    _revalidar_foto_imediatamente_antes_unlink(
                        plano,
                        client_id=str(client_id),
                        store_ids=stores,
                        group_id=group_id,
                        nome=nome,
                        esperado=item,
                    )
                    if (
                        not temporaria.is_file()
                        or temporaria.is_symlink()
                        or temporaria.stat().st_size != item["size"]
                        or _sha256_arquivo(temporaria) != item["sha256"]
                    ):
                        _falhar("quarantine_changed", "Uma foto mudou antes da exclusao.")
                    temporaria.unlink()
                    _fsync_diretorio(quarentena)
                    excluidos += 1
                except CadastroFotosMigracaoErro:
                    raise
                except OSError:
                    limpeza_pendente = True
            try:
                if quarentena.is_dir() and not any(quarentena.iterdir()):
                    quarentena.rmdir()
                    _fsync_diretorio(contexto["photos"])
            except OSError:
                limpeza_pendente = True
            # Nao aceite sucesso se a pasta for recriada entre o rmdir e a
            # barreira final. O journal permanece para retomada fail-closed.
            if os.path.lexists(quarentena):
                limpeza_pendente = True

        if limpeza_pendente:
            _falhar(
                "legacy_cleanup_pending",
                "As copias foram verificadas, mas a limpeza final ficou pendente.",
            )

        # Mantem o journal se qualquer destino/configuracao tiver mudado
        # durante a limpeza, permitindo investigacao e retomada fail-closed.
        for plano in planos:
            _revalidar_plano_antes_exclusao(
                plano,
                client_id=str(client_id),
                store_ids=stores,
                group_id=group_id,
            )
            if (
                plano["state"] != "already_migrated"
                and os.path.lexists(plano["context"]["quarantine"])
            ):
                _falhar(
                    "legacy_cleanup_pending",
                    "As copias foram verificadas, mas a quarentena ainda existe.",
                )

        # O journal e o ultimo artefato removido. Se essa remocao falhar, a
        # proxima execucao valida tudo e conclui idempotentemente.
        for plano in planos:
            if plano["state"] == "already_migrated":
                continue
            journal = plano["context"]["journal"]
            try:
                journal.unlink()
                _fsync_diretorio(journal.parent)
            except OSError:
                _falhar(
                    "legacy_cleanup_pending",
                    "A limpeza terminou, mas o journal ainda precisa ser removido.",
                )

        for plano in planos:
            _revalidar_plano_concluido_sem_residuos(
                plano,
                client_id=str(client_id),
                store_ids=stores,
                group_id=group_id,
            )

        status = (
            "recovered"
            if any(plano["state"] == "recovery_pending" for plano in planos)
            else "migrated"
        )
        return _resultado_execucao(
            status=status,
            client_id=str(client_id),
            planos=planos,
            stores=stores,
            group_id=group_id,
            copiados=copiados,
            ignorados=ignorados,
            excluidos=excluidos,
        )


__all__ = [
    "CadastroFotosMigracaoErro",
    "EXTENSOES_FOTO_SUPORTADAS",
    "planejar_migracao_fotos_contexto",
    "planejar_migracao_fotos_contextos",
    "executar_migracao_fotos_contextos",
]
