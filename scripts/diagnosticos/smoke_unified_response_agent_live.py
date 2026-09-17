"""Manual live-provider smoke test for the unified Mercado Livre response agent."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import backend_api as _backend_runtime  # noqa: E402,F401  # binds the app runtime
from backend.modules.perguntas_pos_venda.ai.clients import _PerguntasCodexV3Client
from backend.modules.perguntas_pos_venda.ai.unified_response_agent import (
    UnifiedResponseAgentOperationalError,
    run_unified_response_agent,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required because this invokes the configured live Codex provider.",
    )
    parser.add_argument("--model", default="codex:gpt-5.6-sol")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    if not args.confirm_live:
        raise SystemExit("Use --confirm-live to invoke the configured live provider.")
    os.environ.setdefault("JK_CODEX_CONSOLE_ENABLED", "1")

    packet = {
        "identity": {
            "store_ref": "manual-smoke-store",
            "seller_id": "manual-smoke-seller",
            "site_id": "MLB",
            "sku": "632-K",
            "item_id": "MLB4992847213",
            "variation_id": "",
        },
        "question": {
            "id": "manual-smoke-question",
            "text": "Quais bitolas de fios que este conjunto aceita 4 mm, 6 mm, 10mm?",
        },
        "listing_facts": {
            "title": "Conjunto Tomada Industrial Plug Macho 2p+t 32a Azul 220-250v",
            "permalink": "https://produto.mercadolivre.com.br/MLB-4992847213",
            "attributes": [],
        },
    }
    agent_input = {
        "_unified_response_flow": "pre_sale",
        "task": "manual_unified_response_agent_smoke",
        "store": "Loja Smoke",
        "store_id": "manual-smoke-store",
        "seller_id": "manual-smoke-seller",
        "site_id": "MLB",
        "question": packet["question"],
        "item": {
            "id": "MLB4992847213",
            "seller_sku": "632-K",
            "title": packet["listing_facts"]["title"],
        },
        "context": {"sku": "632-K"},
    }
    client = _PerguntasCodexV3Client(
        "manual-smoke-tenant",
        "Loja Smoke",
        args.model,
        agent_input,
        reasoning_effort="low",
    )
    client.sku_question_context = packet
    invocations: list[dict[str, str]] = []
    research_rounds: list[int] = []

    def invoke_turn(
        prompt: str,
        tool_results: list[dict[str, Any]],
        force_answer: bool,
    ) -> dict[str, Any]:
        before = str(client.codex_thread_id or "")
        result = client.invoke_unified_turn(prompt, tool_results, force_answer)
        invocations.append({"before": before, "after": str(client.codex_thread_id or "")})
        return result

    def execute_research(
        _requests: list[dict[str, str]],
        round_number: int,
    ) -> list[dict[str, Any]]:
        research_rounds.append(round_number)
        return [
            {
                "function": "web_search_product_identity",
                "result": {
                    "found": True,
                    "read_only": True,
                    "source_kind": "manual_smoke_fixture",
                    "product_research_evidence": [{
                        "ref": "smoke:identity:modelo-32a",
                        "brand": "Fabricante identificado",
                        "model": "Tomada industrial 32 A",
                    }],
                },
            },
            {
                "function": "web_search_question_context",
                "result": {
                    "found": True,
                    "read_only": True,
                    "source_kind": "manual_smoke_fixture",
                    "sources": [
                        {
                            "ref": "smoke:manual:a",
                            "claim": "borne para 4 e 6 mm2 com condutor flexivel",
                        },
                        {
                            "ref": "smoke:catalogo:b",
                            "claim": "limite de 10 mm2 somente para condutor rigido",
                        },
                    ],
                },
            },
        ]

    try:
        result = run_unified_response_agent(
            flow="pre_sale",
            context={
                "flow": "pre_sale",
                "server_identity": packet["identity"],
                "response_signature": "A equipe Loja Smoke agradece o contato.",
                "response_policy": "Responda com os fatos tecnicos comprovados e condicione diferencas relevantes.",
                "sku_question_context": packet,
            },
            invoke_turn=invoke_turn,
            execute_research=execute_research,
        )
    except UnifiedResponseAgentOperationalError as exc:
        print(json.dumps({
            "ok": False,
            "blocked": "live_provider_unavailable",
            "error_code": exc.code,
        }, sort_keys=True))
        return 2

    answer_normalized = str(result.get("answer") or "").casefold()
    same_thread = bool(
        len(invocations) >= 2
        and invocations[0]["after"]
        and all(
            invocation["before"] == invocations[0]["after"]
            for invocation in invocations[1:]
        )
    )
    checks = {
        "researched": bool(research_rounds),
        "same_thread": same_thread,
        "conditional_wire_answer": all(
            term in answer_normalized for term in ("4", "6", "10", "flex", "rigi")
        ),
        "technical_consensus": result.get("evidence_basis") == "technical_consensus",
        "human_review": result.get("requires_human_review") is True,
        "no_generic_fallback": "informação não está confirmada" not in answer_normalized,
    }
    print(json.dumps({
        "ok": all(checks.values()),
        "checks": checks,
        "provider_turns": len(invocations),
        "research_rounds": research_rounds,
        "evidence_basis": result.get("evidence_basis"),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
