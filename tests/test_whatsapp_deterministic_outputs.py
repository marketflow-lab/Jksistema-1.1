from __future__ import annotations

from backend.services import codex_console


def _whatsapp_task(prompt: str, query_policy: dict | None = None) -> dict:
    return {
        "origin": "whatsapp",
        "prompt": prompt,
        "query_policy": dict(query_policy or {}),
    }


def test_stock_response_names_included_and_excluded_deposits_without_ellipsis():
    task = _whatsapp_task("qual o estoque do SKU 001 na Uai Mineirinho")
    result = {
        "success": True,
        "tool_id": "bling_stock_balances",
        "top_rows": [{
            "loja": "Uai Mineirinho",
            "sku": "001",
            "produto": "Cebolão do Radiador Sensor Temperatura",
            "saldo_bruto_retornado": 186,
            "saldo_loja_total": 61,
            "full_excluido": True,
            "cobertura_depositos_completa": True,
            "depositos": [{"id": 1, "descricao": "Loja", "saldo_fisico": 61}],
            "depositos_excluidos": [
                {"id": 2, "descricao": "Fulfillment", "saldo_fisico": 113, "motivo": "Estoque Full/Fulfillment"},
                {"id": 3, "descricao": "Devoluções/Conserto", "saldo_fisico": 10, "motivo": "desconsiderarSaldo=true na Bling"},
                {"id": 4, "descricao": "Defeito", "saldo_fisico": 2, "motivo": "desconsiderarSaldo=true na Bling"},
                {"id": 5, "descricao": "Shopee 205945277 (Fulfillment)", "saldo_fisico": 0, "motivo": "Estoque Full/Fulfillment"},
            ],
        }],
    }

    response = codex_console._codex_whatsapp_bling_stock_response(task, [result])

    assert "**Estoque disponível de loja na Bling:** 61 unidades" in response
    assert "**Saldo bruto retornado pela Bling:** 186 unidades" in response
    assert "**Loja:** 61 unidades — incluído" in response
    assert "**Fulfillment:** 113 unidades — excluído por ser Full/Fulfillment" in response
    assert "**Devoluções/Conserto:** 10 unidades — desconsiderado pela configuração da Bling" in response
    assert "**Defeito:** 2 unidades" in response
    assert "**Shopee 205945277 (Fulfillment):** 0 unidades" in response
    assert "..." not in response


def test_stock_response_marks_unknown_deposit_and_incomplete_coverage():
    result = {
        "success": True,
        "tool_id": "bling_stock_balances",
        "top_rows": [{
            "loja": "Uai Mineirinho",
            "sku": "001",
            "produto": "Produto",
            "saldo_bruto_retornado": 5,
            "saldo_loja_total": None,
            "cobertura_depositos_completa": False,
            "depositos": [],
            "depositos_excluidos": [{
                "id": 999,
                "descricao": "Deposito ID 999 - nao classificado",
                "saldo_fisico": 5,
                "motivo": "ID nao encontrado no catalogo de depositos",
            }],
        }],
    }

    response = codex_console._codex_whatsapp_bling_stock_response(
        _whatsapp_task("estoque SKU 001"),
        [result],
    )

    assert "Depósito ID 999 — não classificado" in response
    assert "ID não encontrado no catálogo de depósitos" in response
    assert "Estoque disponível de loja na Bling:** indisponível" in response
    assert "classificação dos depósitos está incompleta" in response


def test_report_continuation_forces_full_ml_contract_without_query_policy():
    task = _whatsapp_task("faça a análise desse mês na mesma loja")

    args, is_report = codex_console._codex_whatsapp_prepare_agent_tool_call(
        task,
        "mercado_livre_orders",
        {"loja": "Uai Mineirinho", "limite": 50, "max_paginas": 2},
        [],
    )

    assert is_report is True
    assert args["mode"] == "report"
    assert args["limite"] == 20_000
    assert args["max_paginas"] == 400
    assert args["force_refresh"] is True
    assert args["status"] == "paid,partially_refunded"
    assert args["statuses"] == "paid,partially_refunded"


def test_whatsapp_simple_ml_order_query_is_not_promoted_to_report_by_style_prompt():
    task = {
        "origin": "whatsapp",
        "prompt": (
            "[Origem: WhatsApp vinculado ao JK Sistema]\n"
            "Use secoes apenas quando elas realmente ajudarem em relatorios ou respostas longas.\n"
            "Texto recebido:\nMe mostre os detalhes da ultima venda na JK Pecas"
        ),
    }

    args, is_report = codex_console._codex_whatsapp_prepare_agent_tool_call(
        task,
        "mercado_livre_orders",
        {"loja": "JK Pecas", "limite": 20},
    )

    assert is_report is False
    assert args == {"loja": "JK Pecas", "limite": 20}


def test_complete_ml_report_uses_every_sku_and_falls_back_to_mlb_identifier():
    rows = [
        {
            "sku": f"SKU-{index:03d}",
            "title": f"Produto completo de número {index:03d} sem cortar o nome",
            "quantity": index,
            "gross_amount": index * 12.5,
        }
        for index in range(1, 26)
    ]
    rows.append({"sku": "", "item_id": "MLB123456789", "title": "Produto sem seller SKU", "quantity": 3, "gross_amount": 99})
    result = {
        "success": True,
        "tool_id": "mercado_livre_orders",
        "args": {"mode": "report", "limite": 20_000, "max_paginas": 400},
        "all_rows": rows,
        "summary": [{
            "tool_id": "mercado_livre_orders",
            "loja": "Uai Mineirinho",
            "periodo": {"data_inicio": "2026-07-01", "data_fim": "2026-07-13"},
            "summary": {
                "api_consulted": True,
                "store": "Uai Mineirinho",
                "totals": {
                    "orders": 206,
                    "items_quantity": sum(row["quantity"] for row in rows),
                    "gross_amount": sum(row["gross_amount"] for row in rows),
                    "paid_amount": sum(row["gross_amount"] for row in rows),
                    "refund_amount": 0,
                    "net_amount": sum(row["gross_amount"] for row in rows),
                },
                "paging": {"pages_fetched": 5, "scanned": 206, "has_more": False, "report_mode": True},
                "coverage_complete": True,
            },
        }],
    }

    response = codex_console._codex_whatsapp_complete_ml_report(
        _whatsapp_task("continue na mesma loja"),
        [result],
    )

    assert "**SKU:** SKU-001" in response
    assert "**SKU:** SKU-025" in response
    assert "**SKU:** MLB123456789" in response
    assert "**Qtd.:** 25" in response
    assert "**Valor unitário médio:** R$ 12,50" in response
    assert "**Total vendido:** R$ 312,50" in response
    assert "Produto completo de número 025 sem cortar o nome" in response
    assert "5 página(s), 206 pedido(s) verificado(s), 206 pedido(s) considerado(s)" in response
    assert "26 SKU(s) consolidado(s)" in response
    assert response.count("- **SKU:**") == 26
    assert "..." not in response


def test_exact_order_response_preserves_server_transcript_and_hides_it_from_model_prompt():
    result = {
        "success": True,
        "tool_id": "mercado_livre_orders",
        "exact_metadata": {
            "exact_lookup": True,
            "requested_id": "2000013990113115",
            "identifier_type": "pack",
            "matched_stores": ["JK Peças"],
            "resolved_order_ids": ["2000017389080442"],
            "found": True,
            "coverage_complete": True,
            "partial_response": False,
        },
        "all_rows": [{
            "store": "JK Peças",
            "order_id": "2000017389080442",
            "pack_id": "2000013990113115",
            "status": "paid",
            "date_created": "2026-07-13T01:13:00-03:00",
            "date_closed": "2026-07-13T01:14:00-03:00",
            "buyer_name": "Comprador Teste",
            "buyer_nickname": "CLIENTE_TESTE",
            "gross_amount": 149.9,
            "paid_amount": 149.9,
            "refund_amount": 0,
            "net_amount": 149.9,
            "items": [{
                "title": "Chave Farol",
                "sku": "299-1",
                "item_id": "MLB3030582867",
                "quantity": 1,
                "unit_price": 149.9,
                "variation_attributes": [],
            }],
            "shipment": {
                "status": "ready_to_ship",
                "substatus": "in_warehouse",
                "delivery_state": "preparing",
                "delivery_state_label": "Em preparação no armazém Full",
            },
            "fulfillment": {"is_full": True, "logistic_type": "fulfillment"},
            "return_status": {"label": "Sem devolução registrada"},
            "claims": [{
                "claim_id": "88001",
                "status": "opened",
                "stage": "claim",
                "reason_id": "PDD",
                "conversation": {
                    "available": True,
                    "messages": [{
                        "date": "2026-07-13T11:00:00Z",
                        "label": "Comprador",
                        "role": "buyer",
                        "text": "Fala exata da reclamação, sem reescrever.",
                        "attachments": [],
                    }],
                },
            }],
            "conversations": {
                "total_messages": 2,
                "complete": True,
                "post_sale": {
                    "available": True,
                    "messages": [{
                        "message_id": "msg-1",
                        "date": "2026-07-13T10:00:00Z",
                        "label": "Comprador",
                        "role": "buyer",
                        "text": "Esta é a pergunta literal do comprador.",
                        "attachments": [{"name": "foto-produto.jpg"}],
                    }],
                },
            },
        }],
        "top_rows": [],
    }

    response = codex_console._codex_exact_ml_order_response(
        _whatsapp_task("verifique a venda 2000013990113115"),
        [result],
    )
    safe_prompt = codex_console._codex_agent_results_prompt(2, [result])

    assert response.startswith(codex_console.CODEX_EXACT_ORDER_HISTORY_MARKER)
    assert "**Full:** Sim" in response
    assert "Esta é a pergunta literal do comprador." in response
    assert "Fala exata da reclamação, sem reescrever." in response
    assert "foto-produto.jpg" in response
    assert "Esta é a pergunta literal do comprador." not in safe_prompt
    assert "Fala exata da reclamação, sem reescrever." not in safe_prompt
    assert '"total_messages": 2' in safe_prompt


def test_exact_order_whatsapp_uses_one_compact_card_without_repeating_ai_summary():
    order_id = "2000017390557390"
    result = {
        "success": True,
        "tool_id": "mercado_livre_orders",
        "exact_metadata": {
            "exact_lookup": True,
            "requested_id": order_id,
            "identifier_type": "order",
            "matched_stores": ["JK Peças"],
            "resolved_order_ids": [order_id],
            "found": True,
            "coverage_complete": True,
            "partial_response": False,
        },
        "all_rows": [{
            "store": "JK Peças",
            "order_id": order_id,
            "pack_id": order_id,
            "status": "paid",
            "date_created": "2026-07-13T08:10:00-03:00",
            "date_closed": "2026-07-13T08:11:00-03:00",
            "buyer_name": "Ricardo Santos Borges",
            "buyer_nickname": "RS20241112191408",
            "gross_amount": 599.91,
            "paid_amount": 599.91,
            "refund_amount": 0,
            "net_amount": 599.91,
            "items": [{
                "title": "Bomba C Filtro Combustivel Land Rover Evoque 2.0 Original Com Filtro",
                "sku": "254-1",
                "item_id": "MLB3433495283",
                "quantity": 1,
                "unit_price": 599.91,
                "gross_amount": 599.91,
            }],
            "shipment": {
                "shipment_id": "47511796537",
                "delivery_state": "preparing",
                "status": "pending",
                "substatus": "buffered",
                "logistic_type": "cross_docking",
            },
            "fulfillment": {"is_full": False, "logistic_type": "cross_docking"},
            "claims": [],
            "claims_status": {"available": True, "complete": True, "has_claims": False},
            "return_status": {"available": True, "has_return": False, "label": "Sem devolução registrada"},
            "conversations": {
                "total_messages": 0,
                "complete": True,
                "post_sale": {"available": True, "complete": True, "messages": []},
            },
        }],
    }
    repeated_ai_summary = "Resumo da IA que repetiria status, comprador, produto, valores, envio e pós-venda."

    response = codex_console._codex_exact_ml_order_response(
        _whatsapp_task(f"detalhes da venda {order_id}"),
        [result],
        repeated_ai_summary,
    )

    assert response.count(order_id) == 1
    assert response.count("## Produto") == 1
    assert response.count("## Valores") == 1
    assert response.count("## Envio") == 1
    assert response.count("## Pós-venda") == 1
    assert "**Status:** Pago" in response
    assert "**Logística:** Cross docking" in response
    assert "**Full:** Não" in response
    assert "**Reembolsado:** R$ 0,00" in response
    assert "**Reclamações:** nenhuma" in response
    assert "**Mensagens:** nenhuma" in response
    assert "Resumo da IA" not in response
    assert repeated_ai_summary not in response
    assert "Identificador reconhecido" not in response
    assert "Pedido 1" not in response
    assert "Status/substatus" not in response
    assert "Histórico pós-venda" not in response
    assert "Consulta read-only feita diretamente" not in response
    assert len(response) < 1_600


def test_exact_order_whatsapp_never_reports_unavailable_post_sale_as_zero():
    response = codex_console._codex_exact_ml_order_whatsapp_response(
        {
            "requested_id": "1234567890",
            "matched_stores": ["JK Peças"],
            "coverage_complete": False,
            "partial_response": True,
        },
        [{
            "order_id": "1234567890",
            "pack_id": "1234567890",
            "status": "paid",
            "date_created": "2026-07-13T08:10:00-03:00",
            "items": [],
            "shipment": {},
            "fulfillment": {"is_full": None},
            "claims": [],
            "claims_status": {"available": False, "complete": False},
            "return_status": {"available": False, "has_return": None, "label": "Indisponível"},
            "conversations": {
                "total_messages": 0,
                "complete": False,
                "post_sale": {"available": False, "complete": False, "messages": []},
            },
        }],
    )

    assert "**Reclamações:** indisponível" in response
    assert "**Mensagens:** indisponível" in response
    assert "**Full:** Indisponível" in response
    assert "Cobertura parcial" in response
