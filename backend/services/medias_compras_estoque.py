"""Read-only stock position for one SKU and one store in Medias Compras."""

from __future__ import annotations

import csv
import math
import os
from typing import Any

from fastapi import Depends, HTTPException, Query

from backend.services import cadastro_compatibilidade as cadastro_compat
from backend.services import medias_compras_common as common


def _pertence_loja(
    registro: dict[str, Any], client_id: str, store_id: str, lojas: list[dict],
) -> bool:
    identidade = str(registro.get("store_id") or "").strip()
    if identidade:
        return identidade == store_id
    nome = str(registro.get("loja_sync") or registro.get("loja") or "").strip()
    if not nome:
        return False
    resolvida = cadastro_compat.resolver_loja_ativa_para_leitura(
        client_id, nome, lojas=lojas,
    )
    return bool(resolvida.get("loja_resolvida") and resolvida.get("store_id") == store_id)


def _numero_saldo(valor: Any) -> float | None:
    texto = str(valor if valor is not None else "").strip()
    if not texto:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        numero = float(texto)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def _ler_saldo_sku(
    client_id: str, sku: str, store_id: str, lojas: list[dict],
) -> tuple[float | None, str]:
    try:
        caminho = os.path.join(common.get_tenant_path(client_id), "produtos_compilado.csv")
        with open(caminho, "r", encoding="utf-8-sig", newline="") as arquivo:
            leitor = csv.DictReader(arquivo, strict=True)
            colunas = [str(coluna).strip().casefold() for coluna in (leitor.fieldnames or [])]
            if "sku" not in colunas or len(colunas) != len(set(colunas)):
                return None, ""
            # A real saldo_loja of zero must never fall back to Full/total stock.
            coluna_saldo = next((coluna for coluna in ("saldo_loja", "saldo", "saldo_total") if coluna in colunas), "")
            if not coluna_saldo:
                return None, ""
            canonicas, legadas = [], []
            for linha in leitor:
                if None in linha:
                    return None, ""
                registro = {str(chave).strip().casefold(): valor for chave, valor in linha.items()}
                if common._normalizar_sku_mes(registro.get("sku")) != sku:
                    continue
                if not _pertence_loja(registro, client_id, store_id, lojas):
                    continue
                (canonicas if str(registro.get("store_id") or "").strip() else legadas).append(registro)
            # Exact durable identities take precedence over old duplicate shadows.
            selecionadas = canonicas or legadas
            if not selecionadas:
                return None, ""
            saldos = [_numero_saldo(linha.get(coluna_saldo)) for linha in selecionadas]
            if any(saldo is None for saldo in saldos):
                return None, ""
            total = sum(saldos)
            if not math.isfinite(total):
                return None, ""
            atualizacoes = {str(linha.get("last_update") or "").strip() for linha in selecionadas}
            atualizado_em = next(iter(atualizacoes)) if len(atualizacoes) == 1 else ""
            return round(total, 2), atualizado_em
    except (OSError, RuntimeError, UnicodeError, csv.Error, ValueError):
        return None, ""


def _ler_transito_sku(
    client_id: str, sku: str, store_id: str, lojas: list[dict],
) -> tuple[float | None, list[dict]]:
    try:
        snapshot = common._carregar_listas_pedidos(client_id)
        selecionadas = [
            lista for lista in snapshot
            if isinstance(lista, dict) and _pertence_loja(lista, client_id, store_id, lojas)
        ]
        for lista in selecionadas:
            if common._normalizar_status_lista_pedido(lista.get("status")) not in common.LISTA_PEDIDO_STATUS_CONTABILIZADOS_TRANSITO:
                continue
            for item in (lista.get("itens") or []):
                if not isinstance(item, dict) or common._normalizar_sku_mes(item.get("SKU")) != sku:
                    continue
                quantidade = float(item.get("Quantidade"))
                if not math.isfinite(quantidade):
                    return None, []
        mapa = common._mapa_estoque_em_transito_detalhado_por_sku(
            client_id, "__todas", snapshot=selecionadas,
        )
        detalhe = mapa.get(sku, {})
        total = float(detalhe.get("total", 0))
        if not math.isfinite(total):
            return None, []
        return round(total, 2), detalhe.get("listas") or []
    except (HTTPException, OSError, RuntimeError, UnicodeError, TypeError, ValueError):
        return None, []


def _api_medias_compras_posicao_estoque_sync(
    sku: str, loja: str, store_id: str, client_id: str,
) -> dict[str, Any]:
    sku_normalizado = common._normalizar_sku_mes(sku)
    if not sku_normalizado:
        raise HTTPException(status_code=400, detail="Informe o SKU para consultar o estoque.")
    escopo = common._resolver_escopo_loja_medias(
        client_id, loja, store_id, exigir_especifica=True,
    )
    lojas = cadastro_compat._lojas_atuais(client_id)
    # Verify the same immutable identity against the snapshot used for legacy rows.
    identidade = cadastro_compat.resolver_loja_ativa_para_leitura(
        client_id, store_id=escopo["store_id"], lojas=lojas,
    )
    if not identidade.get("loja_resolvida"):
        raise HTTPException(status_code=409, detail="A identidade da loja nao esta disponivel neste cliente.")
    identidade_loja = identidade["store_id"]
    saldo, atualizado_em = _ler_saldo_sku(client_id, sku_normalizado, identidade_loja, lojas)
    transito, listas = _ler_transito_sku(client_id, sku_normalizado, identidade_loja, lojas)
    resposta = {
        "sku": str(sku),
        "loja": identidade["loja"],
        "store_id": identidade_loja,
        "saldo_atual_estoque": saldo,
        "estoque_em_transito": transito,
        "estoque_em_transito_listas": listas,
    }
    if atualizado_em:
        resposta["estoque_atualizado_em"] = atualizado_em
    return resposta


async def api_medias_compras_posicao_estoque(
    sku: str = Query(..., min_length=1, max_length=200),
    loja: str = "__todas",
    store_id: str = "",
    client_id: str = Depends(common.get_tenant_id),
):
    from backend.services.blocking_workers import run_blocking

    return await run_blocking(
        _api_medias_compras_posicao_estoque_sync,
        sku=sku, loja=loja, store_id=store_id, client_id=client_id,
    )


__all__ = ["api_medias_compras_posicao_estoque"]
