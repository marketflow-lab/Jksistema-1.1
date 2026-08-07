"""Public request models and stable assistant contracts."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

class CodexAssistantChatRequest(BaseModel):
    message: str
    screen_context: Optional[dict[str, Any]] = None
    history: Optional[list[dict[str, Any]]] = None
    force_refresh: bool = False


class CodexAssistantRunRequest(BaseModel):
    screen_context: Optional[dict[str, Any]] = None
    force: bool = False
    compact: bool = False


class CodexAssistantReportRequest(BaseModel):
    prompt: str = ""
    screen_context: Optional[dict[str, Any]] = None
    thread_id: Optional[str] = None
    conversation_id: Optional[str] = None
    format: Optional[str] = "html"
    force_refresh: bool = False
    profile: Optional[str] = None
    store: Optional[str] = None
    import_list_id: Optional[str] = None


class CodexAssistantReportSettingsRequest(BaseModel):
    settings: dict[str, Any]


class CodexAssistantFinancialAdjustmentRequest(BaseModel):
    adjustment_id: Optional[str] = None
    kind: str = "advertising"
    store: str
    period_start: str
    period_end: str
    amount: float
    platform: str = "manual"
    note: str = ""


class CodexAssistantActionQueueRequest(BaseModel):
    report_id: str = ""
    action_id: str = ""
    action_type: str
    status: str = "queued"
    store: str = ""
    skus: list[str] = []
    impact_brl: Optional[float] = None
    confidence: str = ""
    owner_username: str = ""
    owner_role: str = ""
    due_at: str = ""
    title: str = ""
    evidence: str = ""
    recommendation: str = ""


class CodexAssistantActionQueueUpdateRequest(BaseModel):
    status: Optional[str] = None
    owner_username: Optional[str] = None
    owner_role: Optional[str] = None
    due_at: Optional[str] = None
    note: Optional[str] = None
    result: Optional[dict[str, Any]] = None


class CodexAssistantEvaluationRequest(BaseModel):
    screen_context: Optional[dict[str, Any]] = None
    force: bool = False


class CodexOperationalMemoryRequest(BaseModel):
    category: str = "decisoes"
    content: str
    source: Optional[str] = "manual"
    metadata: Optional[dict[str, Any]] = None
    importance: int = 3
    entry_id: Optional[str] = None


CODEX_ASSISTANT_EVALUATION_CASES: list[dict[str, Any]] = [
    {
        "id": "bling_saldo_sku_124",
        "question": "saldo SKU 124 na Bling",
        "mode": "chat",
        "expected_tools": ["bling_stock_balances"],
        "compatible_tools": ["bling_product", "bling_products", "product_data", "stock_data"],
    },
    {
        "id": "sales_30d_jk_pecas",
        "question": "gere um relatorio dos ultimos 30 dias da JK Pecas",
        "mode": "report",
        "expected_tools": ["sales_ranking"],
        "compatible_tools": ["sales_returns_query", "stockout_forecast", "stale_stock", "product_costs_and_margin", "bling_sales_orders"],
    },
    {
        "id": "open_questions",
        "question": "perguntas sem resposta",
        "mode": "chat",
        "expected_tools": ["questions_post_sale_query"],
        "compatible_tools": ["mercado_livre_readonly", "operational_dispatcher", "local_cache_query"],
    },
    {
        "id": "margin_top_skus",
        "question": "margem dos top SKUs",
        "mode": "report",
        "expected_tools": ["product_costs_and_margin"],
        "compatible_tools": ["sales_ranking", "mercado_livre_listing", "product_registry", "local_csv_query"],
    },
]
