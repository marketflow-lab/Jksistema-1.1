from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "build_black_jhon_evaluation_dataset.py"
SPEC = importlib.util.spec_from_file_location("build_black_jhon_evaluation_dataset", SCRIPT)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_anonymizer_removes_high_confidence_identifiers():
    value = builder.anonymize_question(
        "Loja Segredo pedido 12345678901, MLB 123456789, contato teste@example.com, +55 31 99999-9999",
        stores=["Loja Segredo"],
    )
    assert "Loja Segredo" not in value
    assert "teste@example.com" not in value
    assert "99999-9999" not in value
    assert builder.dlp_findings(value) == []


def test_builder_creates_100_real_and_20_synthetic_staging_cases(monkeypatch, tmp_path):
    monkeypatch.setenv("JK_EVAL_HMAC_KEY", "test-only-secret-with-32-characters")
    source = tmp_path / "source"
    source.mkdir()
    categories = list(builder.QUOTAS)
    phrases = {
        "stock_catalog_listing_fitment": "Consulte estoque SKU 123 do produto no anúncio.",
        "sales_returns_reports": "Gere relatório de vendas e devoluções do período.",
        "integrations_orders": "Consulte pedidos da integração Bling Mercado Livre.",
        "public_questions_post_sale": "Consulte pergunta do cliente no pós-venda.",
        "margin_fiscal": "Calcule margem, custo fiscal, NCM e lucro.",
        "context_hub": "Busque documentação no Context Hub e Obsidian.",
        "ambiguity_continuity_failure": "Continue a mesma consulta que falhou e está sem dados.",
    }
    index = 0
    for category in categories:
        for position in range(builder.QUOTAS[category]):
            index += 1
            record = {
                "prompt": f"{phrases[category]} referência curta {index}.",
                "conversation_id": f"conversation-{index}",
                "origin": "app",
                "created_by": "Operador Real",
                "tool_calls": [{"tool_id": "product_data"}],
            }
            (source / f"{index:03d}.json").write_text(json.dumps(record), encoding="utf-8")

    rows = builder.build_dataset(source)
    assert len(rows) == 120
    assert sum(row["source_kind"] == "real_anonymized" for row in rows) == 100
    assert sum(row["source_kind"] == "synthetic_security" for row in rows) == 20
    assert sum(row["split"] == "calibration" for row in rows) == 90
    assert sum(row["split"] == "holdout" for row in rows) == 30
    assert all(row["review"]["status"] == "pending" for row in rows)
