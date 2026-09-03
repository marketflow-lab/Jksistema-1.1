"""Fail-closed migration of the pre-tenant catalog files."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from typing import Callable

from backend.services.cadastro_fotos_coordenacao import (
    CadastroFotosCoordenacaoErro,
    bloquear_transicao_fotos_tenant,
)
from backend.services.path_coordination import path_lock_for


_CATALOGOS_COM_REFERENCIA_FOTO = {
    "cadastro_produtos.csv",
    "produtos_compilado.csv",
}
_FOTOS_CONFIG_ARQUIVO = "cadastro_fotos_config.json"
_FOTOS_CONFIG_SCHEMA = "jk.cadastro.fotos.v1"


def _legacy_tenant_digest(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def _legacy_tenant_scope_estrito(tenant_path: str) -> bool:
    """Le a trava strict sem depender do runtime singleton do Cadastro."""

    caminho = os.path.join(tenant_path, _FOTOS_CONFIG_ARQUIVO)
    if not os.path.lexists(caminho):
        return False
    if os.path.islink(caminho) or not os.path.isfile(caminho):
        return True
    try:
        if os.path.getsize(caminho) > 262_144:
            return True
        with open(caminho, "r", encoding="utf-8-sig") as arquivo:
            config = json.load(arquivo)
    except Exception:
        return True
    if (
        not isinstance(config, dict)
        or config.get("schema") != _FOTOS_CONFIG_SCHEMA
        or not isinstance(config.get("strict_store_scope"), bool)
        or not isinstance(config.get("shared_groups"), list)
    ):
        return True
    if config["strict_store_scope"] or config["shared_groups"]:
        # Sem o runtime completo nao e possivel provar que os store_ids dos
        # grupos ainda pertencem ao tenant. Qualquer grupo bloqueia o legado.
        return True
    return False


def migrar_arquivo_legado_para_tenant_seguro(
    client_id: str,
    nome_arquivo: str,
    caminho_legado: str,
    *,
    get_tenant_path: Callable[[str], str],
    logger: logging.Logger,
) -> str:
    """Importa o arquivo global somente para ``default``, sob o mutex comum.

    A publicacao usa hard-link de um temporario no diretorio de destino. Assim,
    uma criacao concorrente nunca e sobrescrita e leitores nao observam arquivo
    parcial. A origem so e removida depois da verificacao por hash.
    """

    identidade = str(client_id or "").strip()
    nome = str(nome_arquivo or "").strip()
    tenant_path = os.path.abspath(str(get_tenant_path(identidade) or ""))
    destino = os.path.join(tenant_path, nome)

    if identidade != "default":
        return destino
    if not nome or os.path.basename(nome) != nome:
        logger.warning("[MIGRACAO] Nome de arquivo legado invalido; operacao bloqueada.")
        return destino

    from backend.services import integracoes

    tenant_real = os.path.realpath(tenant_path)
    if os.path.normcase(os.path.normpath(tenant_real)) != os.path.normcase(
        os.path.normpath(tenant_path)
    ):
        logger.warning("[MIGRACAO] Diretorio legado default inseguro; operacao bloqueada.")
        return destino
    cadastro_path = os.path.join(tenant_path, "cadastro_produtos_lojas.csv")
    custos_path = os.path.join(tenant_path, "cadastro_custos_lojas.csv")
    try:
        transicao = bloquear_transicao_fotos_tenant(
            tenant_real,
            timeout_seconds=10,
        )
        with (
            integracoes._LOJAS_CONFIG_LOCK,
            path_lock_for(cadastro_path),
            path_lock_for(custos_path),
            transicao,
        ):
            if os.path.lexists(destino):
                return destino
            origem = os.path.abspath(str(caminho_legado or ""))
            if (
                not origem
                or os.path.islink(origem)
                or not os.path.isfile(origem)
            ):
                return destino

            if nome.casefold() in _CATALOGOS_COM_REFERENCIA_FOTO:
                if _legacy_tenant_scope_estrito(tenant_path):
                    logger.warning(
                        "[MIGRACAO] Legado global ignorado no tenant default em escopo estrito."
                    )
                    return destino

            os.makedirs(os.path.dirname(destino), exist_ok=True)
            temporario = ""
            publicou = False
            try:
                digest_origem = hashlib.sha256()
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=f".{nome}.",
                    suffix=".legacy.tmp",
                    dir=os.path.dirname(destino),
                    delete=False,
                ) as arquivo_destino, open(origem, "rb") as arquivo_origem:
                    temporario = arquivo_destino.name
                    for bloco in iter(lambda: arquivo_origem.read(1024 * 1024), b""):
                        digest_origem.update(bloco)
                        arquivo_destino.write(bloco)
                    arquivo_destino.flush()
                    os.fsync(arquivo_destino.fileno())

                digest_esperado = digest_origem.hexdigest()
                if _legacy_tenant_digest(temporario) != digest_esperado:
                    logger.warning(
                        "[MIGRACAO] Copia temporaria de %s falhou na verificacao.",
                        nome,
                    )
                    return destino
                try:
                    os.link(temporario, destino)
                    publicou = True
                except FileExistsError:
                    return destino
                except OSError as exc:
                    logger.warning(
                        "[MIGRACAO] Publicacao exclusiva de %s falhou: %s",
                        nome,
                        type(exc).__name__,
                    )
                    return destino

                if (
                    os.path.islink(destino)
                    or not os.path.isfile(destino)
                    or _legacy_tenant_digest(destino) != digest_esperado
                ):
                    try:
                        os.unlink(destino)
                    except OSError:
                        pass
                    publicou = False
                    logger.warning(
                        "[MIGRACAO] Destino %s falhou na verificacao; operacao bloqueada.",
                        nome,
                    )
                    return destino

                try:
                    origem_inalterada = (
                        os.path.isfile(origem)
                        and not os.path.islink(origem)
                        and _legacy_tenant_digest(origem) == digest_esperado
                    )
                except OSError:
                    origem_inalterada = False
                if origem_inalterada:
                    try:
                        os.unlink(origem)
                        logger.info(
                            "[MIGRACAO] %s movido para o tenant legado default.",
                            nome,
                        )
                    except OSError as exc:
                        logger.warning(
                            "[MIGRACAO] %s copiado para default; origem preservada (%s).",
                            nome,
                            type(exc).__name__,
                        )
                else:
                    logger.warning(
                        "[MIGRACAO] %s copiado para default; origem mudou e foi preservada.",
                        nome,
                    )
                return destino
            except OSError as exc:
                if publicou:
                    try:
                        os.unlink(destino)
                    except OSError:
                        pass
                logger.warning(
                    "[MIGRACAO] Falha ao migrar %s para default: %s",
                    nome,
                    type(exc).__name__,
                )
                return destino
            finally:
                if temporario:
                    try:
                        os.unlink(temporario)
                    except OSError:
                        pass
    except CadastroFotosCoordenacaoErro as exc:
        logger.warning(
            "[MIGRACAO] Coordenacao do legado default indisponivel (%s).",
            exc.code,
        )
        return destino


__all__ = ["migrar_arquivo_legado_para_tenant_seguro"]
