"""Compatibility facade for the Medias Compras module."""

from __future__ import annotations

from backend.schemas import (
    ListaCompraRequest,
    ListaPedidoAddSkuRequest,
    ListaPedidoPreferenciasColunasRequest,
    ListaPedidoSkuAnaliseConcorrentesRequest,
    ListaPedidoSkuAprovacaoRequest,
    ListaPedidoStatusRequest,
    ListaPedidoUpdateRequest,
    MediasComprasItem,
    MediasComprasRequest,
    MediasComprasSkusOcultosRequest,
)
from backend.services.medias_compras_common import *
from backend.services.medias_compras_common import COMMON_EXPORTS, configure_medias_compras_common_runtime
from backend.services.medias_compras_fiscal import *
from backend.services.medias_compras_fiscal import configure_medias_compras_fiscal_runtime
from backend.services.medias_compras_excel import *
from backend.services.medias_compras_excel import configure_medias_compras_excel_runtime
from backend.services.medias_compras_visao import *
from backend.services.medias_compras_visao import configure_medias_compras_visao_runtime
from backend.services.medias_compras_sugestoes import *
from backend.services.medias_compras_sugestoes import configure_medias_compras_sugestoes_runtime
from backend.services.medias_compras_listas import *
from backend.services.medias_compras_listas import configure_medias_compras_listas_runtime
from backend.services.medias_compras_importacao import *
from backend.services.medias_compras_importacao import configure_medias_compras_importacao_runtime


def configure_medias_compras_runtime(runtime_module=None):
    configure_medias_compras_common_runtime(runtime_module)
    configure_medias_compras_fiscal_runtime(runtime_module)
    configure_medias_compras_excel_runtime(runtime_module)
    configure_medias_compras_visao_runtime(runtime_module)
    configure_medias_compras_sugestoes_runtime(runtime_module)
    configure_medias_compras_listas_runtime(runtime_module)
    configure_medias_compras_importacao_runtime(runtime_module)
    return runtime_module


configure_medias_compras_runtime()

__all__ = [
    "configure_medias_compras_runtime",
    "ListaCompraRequest",
    "ListaPedidoAddSkuRequest",
    "ListaPedidoPreferenciasColunasRequest",
    "ListaPedidoSkuAnaliseConcorrentesRequest",
    "ListaPedidoSkuAprovacaoRequest",
    "ListaPedidoStatusRequest",
    "ListaPedidoUpdateRequest",
    "MediasComprasItem",
    "MediasComprasRequest",
    "MediasComprasSkusOcultosRequest",
    *COMMON_EXPORTS,
    "_recalcular_frete_internacional_itens_lista",
    "_indice_ncm_referencia_aliquotas",
    "_enriquecer_itens_lista_pedido_com_impostos",
    "_construir_mapa_m3_sku",
    "_normalizar_cabecalho_excel",
    "_to_float_excel",
    "_linha_resumo_excel_sku",
    "_gerar_excel_lista_pedido_bytes",
    "api_medias_compras_calcular",
    "api_medias_compras_visao",
    "api_medias_compras_gerar_lista_compra",
    "api_medias_compras_gerar_lista_compra_get",
    "api_medias_compras_gerar_lista_sugestao",
    "api_medias_compras_produtos_sem_venda",
    "api_medias_compras_listas_pedidos",
    "api_medias_compras_skus_ocultos_get",
    "api_medias_compras_skus_ocultos_put",
    "api_medias_compras_preferencias_colunas_get",
    "api_medias_compras_preferencias_colunas_put",
    "api_medias_compras_concorrentes_links",
    "api_medias_compras_lista_pedido_concorrentes_links_lote",
    "api_medias_compras_lista_pedido_detalhe",
    "api_medias_compras_lista_pedido_custo_posto",
    "api_medias_compras_lista_pedido_editar",
    "api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku",
    "api_medias_compras_lista_pedido_atualizar_aprovacao_sku",
    "api_medias_compras_lista_pedido_adicionar_sku",
    "api_medias_compras_lista_pedido_atualizar_status",
    "api_medias_compras_lista_pedido_excluir",
    "api_medias_compras_lista_pedido_download",
    "api_medias_compras_lista_pedido_gerar_download",
    "api_medias_compras_lista_pedido_importar_excel_precos",
    "api_medias_compras_lista_pedido_importar_excel_nova_lista",
    "api_medias_compras_download",
]
