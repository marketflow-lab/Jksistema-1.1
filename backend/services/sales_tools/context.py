from __future__ import annotations

from typing import Optional

from .parsing import _ia_extrair_periodo_mensagem_vendas, _ia_resolver_loja_mensagem_vendas
from .repository import _ia_vendas_db_top_skus
from .runtime import _normalizar_texto


def _ia_vendas_contexto_exato(mensagem: str, client_id: str, contexto: Optional[dict] = None) -> str:
    texto = _normalizar_texto(mensagem or "")
    if "SKU" not in texto and "PRODUTO" not in texto:
        return ""
    if not any(chave in texto for chave in ("MAIS VENDEU", "MAIS VENDIDO", "LIDER", "TOP")):
        return ""

    data_inicio, data_fim = _ia_extrair_periodo_mensagem_vendas(mensagem, contexto)
    if not data_inicio or not data_fim:
        return ""
    loja = _ia_resolver_loja_mensagem_vendas(client_id, mensagem, contexto)
    top_skus = _ia_vendas_db_top_skus(client_id, data_inicio, data_fim, loja, limite=5)
    if not top_skus:
        return ""

    lider = top_skus[0]
    top_linhas = [
        f"{item['sku']} | {item['nome']} | {int(float(item['qtd']))}un | R${float(item['valor']):.2f}"
        for item in top_skus
    ]
    loja_txt = loja or "Todas as lojas"
    return (
        "Consulta exata para a pergunta do usuario sobre vendas:\n"
        f"Periodo consultado: {data_inicio} a {data_fim}\n"
        f"Loja considerada: {loja_txt}\n"
        f"SKU lider: {lider['sku']} | {lider['nome']} | {int(float(lider['qtd']))} unidades | R${float(lider['valor']):.2f}\n"
        "Top SKUs no periodo:\n" + "\n".join(top_linhas)
    )


__all__ = [
    "_ia_vendas_contexto_exato",
]
