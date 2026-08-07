"""Named public API for sales, returns, stock, and analytics tools."""

from . import charts, context, inventory, metrics, monthly, parsing, repository, returns, virtual_stores

build_stale_stock_chart = charts._ia_stale_stock_chart_data
build_stockout_chart = charts._ia_stockout_chart_data
stock_chart_number = charts._ia_stock_chart_number

get_days_without_sale = inventory._ia_tool_get_days_without_sale
get_days_without_sale_top = inventory._ia_tool_get_days_without_sale_top
get_stock_data = inventory._ia_tool_get_stock_data
get_stockout_forecast = inventory._ia_tool_get_stockout_forecast

to_float = parsing._ia_tool_float
resolve_sku = parsing._ia_tool_resolver_sku
extract_month_year = parsing._ia_extrair_mes_ano_mensagem
extract_months_year = parsing._ia_extrair_meses_ano_mensagem
extract_sales_period = parsing._ia_extrair_periodo_mensagem_vendas
extract_comparison_periods = parsing._ia_extrair_periodos_comparacao
resolve_store = parsing._ia_resolver_loja_mensagem_vendas

default_month_period = monthly._ia_periodo_mensal_padrao
get_sales_by_month_period = monthly._ia_tool_get_sales_by_month_period
get_top_skus_sales_by_month = monthly._ia_tool_get_top_skus_sales_by_month
get_top_skus_returns_by_month = monthly._ia_tool_get_top_skus_returns_by_month
get_month_sales_returns_details = monthly._ia_tool_get_month_sales_returns_details
get_sku_sales_by_month = monthly._ia_tool_get_sku_sales_by_month
compare_sku_sales_months = monthly._ia_tool_compare_sku_sales_months

get_sales_by_period = metrics._ia_tool_get_sales_by_period
get_sales_quantity_by_period = metrics._ia_tool_get_sales_quantity_by_period
period_metrics = metrics._ia_metricas_periodo_raw
get_avg_ticket_by_period = metrics._ia_tool_get_avg_ticket_by_period
get_return_rate_by_period = metrics._ia_tool_get_return_rate_by_period
get_period_comparison = metrics._ia_tool_get_period_comparison
get_sales_timeseries = metrics._ia_tool_get_sales_timeseries
detect_sales_anomalies = metrics._ia_tool_detect_sales_anomalies
get_profit_by_period = metrics._ia_tool_get_profit_by_period

returns_summary = repository._ia_devolucoes_db_resumo
get_sales_reference_date = repository._ia_obter_data_referencia_vendas
sales_timeseries_rows = repository._ia_vendas_timeseries_raw
query_sku_sales_returns = repository._ia_vendas_db_consulta_sku_vendas_devolucoes
query_sku_sales = repository._ia_vendas_db_consulta_sku
extract_ml_item_ids = repository._ia_extrair_item_ids_ml
connected_ml_stores = repository._ia_lojas_ml_conectadas
sales_database_text = repository._ia_vendas_db_texto
get_top_skus = repository._ia_vendas_db_top_skus

get_returns_quantity_by_period = returns._ia_tool_get_returns_quantity_by_period
get_returns_by_period = returns._ia_tool_get_returns_by_period
scope_notice = returns._ia_chat_scope_notice
get_returns_data = returns._ia_tool_get_returns_data
get_returns_by_sku_period = returns._ia_tool_get_returns_by_sku_period

get_sales_by_virtual_store_period = virtual_stores._ia_tool_get_sales_by_virtual_store_period
get_sales_by_sku_virtual_store = virtual_stores._ia_tool_get_sales_by_sku_virtual_store
get_exact_sales_context = context._ia_vendas_contexto_exato

__all__ = [
    "build_stale_stock_chart",
    "build_stockout_chart",
    "stock_chart_number",
    "get_days_without_sale",
    "get_days_without_sale_top",
    "get_stock_data",
    "get_stockout_forecast",
    "to_float",
    "resolve_sku",
    "extract_month_year",
    "extract_months_year",
    "extract_sales_period",
    "extract_comparison_periods",
    "resolve_store",
    "default_month_period",
    "get_sales_by_month_period",
    "get_top_skus_sales_by_month",
    "get_top_skus_returns_by_month",
    "get_month_sales_returns_details",
    "get_sku_sales_by_month",
    "compare_sku_sales_months",
    "get_sales_by_period",
    "get_sales_quantity_by_period",
    "period_metrics",
    "get_avg_ticket_by_period",
    "get_return_rate_by_period",
    "get_period_comparison",
    "get_sales_timeseries",
    "detect_sales_anomalies",
    "get_profit_by_period",
    "returns_summary",
    "get_sales_reference_date",
    "sales_timeseries_rows",
    "query_sku_sales_returns",
    "query_sku_sales",
    "extract_ml_item_ids",
    "connected_ml_stores",
    "sales_database_text",
    "get_top_skus",
    "get_returns_quantity_by_period",
    "get_returns_by_period",
    "scope_notice",
    "get_returns_data",
    "get_returns_by_sku_period",
    "get_sales_by_virtual_store_period",
    "get_sales_by_sku_virtual_store",
    "get_exact_sales_context",
]
