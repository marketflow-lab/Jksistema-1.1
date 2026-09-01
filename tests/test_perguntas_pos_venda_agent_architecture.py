from __future__ import annotations

import ast
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.modules.perguntas_pos_venda.ai import runtime
from backend.routers.ia import create_ia_router
from backend.routers.perguntas_pos_venda import create_perguntas_pos_venda_router
from backend.schemas.ia import IAAgentQueryRequest
from backend.services import perguntas_pos_venda_agent
from backend.services import perguntas_pos_venda_core
from backend.services import ia_endpoints
from backend.services.perguntas_pos_venda_state import _ia_agent_endpoint_headers


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "backend" / "modules" / "perguntas_pos_venda" / "ai"
FACADE = ROOT / "backend" / "services" / "perguntas_pos_venda_agent.py"
PUBLIC_EXPORTS = [
    "PerguntasAgentRuntime",
    "QuestionAgentResult",
    "configure_perguntas_pos_venda_agent_runtime",
    "build_agent_input",
    "generate_response",
    "build_post_sale_context",
    "validate_post_sale_response",
]
CONTRACT_HASHES = {
    "routes": "dc6cb53458cb92d7a76a72b148a26e7d4b658cfdbb829e2841a2f3c59eb95a39",
    "aliases": "f149aa04a166eacd3f942889d2495dc5a23be9bb3317160d97e247ae834aa68e",
    "schema": "500b7ccbcedc02d3a254e8114e974355be5635d2f01bd7dc929b212643cb254a",
    "policy": "5cda037a74f48108c06cc2635fe82412629189e66757b3c36bf693fb132e5955",
    "prompts": "8f95be994258f4ae120205d9a43ad915b1723aa3e6323d7f8c3716f4eed0f95f",
    "exports": "fbd5bf89bb3a7d13d32c55dda0a0b89996c3c6a4828f94a250c94c5f18b74550",
}
LAYERS = {
    "contracts": 0, "attachments": 0, "telemetry_core": 0,
    "deep_research_contracts": 0, "deep_research_prefetch": 0, "deep_research_scoping": 0,
    "input_contracts": 0, "official_source_registry": 0,
    "runtime": 1, "deep_research_analysis": 1, "research_url_security": 1,
    "inputs": 2, "provider_transport": 2, "deep_research_fingerprints": 2,
    "deep_research_documents": 3,
    "queries": 3, "context": 4, "sources": 4, "evidence": 5,
    "deep_research": 4, "deep_research_crawler": 4,
    "compatibility": 6, "tools": 6, "client_workflows": 7, "general_commercial": 7, "validation": 7,
    "approval": 8, "clients": 8, "post_sale": 8, "providers": 8, "telemetry": 8,
    "execution": 9, "api": 10, "__init__": 10,
}


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _local_dependencies(path: Path) -> set[str]:
    dependencies: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and node.module:
            dependency = node.module.split(".", 1)[0]
            if (PACKAGE / f"{dependency}.py").exists():
                dependencies.add(dependency)
    return dependencies


def _contract_snapshot() -> dict[str, object]:
    routes = sorted(
        [{"path": route.path, "methods": sorted(route.methods or []), "name": route.name}
         for route in create_perguntas_pos_venda_router().routes],
        key=lambda item: (item["path"], item["methods"], item["name"]),
    )
    aliases = sorted(
        [{"path": route.path, "methods": sorted(route.methods or []), "name": route.name}
         for route in create_ia_router().routes if route.name == "ia_agent_perguntas_query"],
        key=lambda item: item["path"],
    )
    policy = {
        "version": runtime._PERGUNTAS_IA_RESPONSE_POLICY_VERSION,
        "method_version": runtime._PERGUNTAS_IA_SELLER_METHOD_VERSION,
        "text": runtime._PERGUNTAS_IA_RESPONSE_POLICY,
        "commercial_state_policy": runtime._PERGUNTAS_IA_COMMERCIAL_STATE_POLICY,
        "allowed_tools": sorted(runtime._PERGUNTAS_IA_ALLOWED_TOOLS),
        "decisions": ["yes", "no", "conditional", "insufficient"],
        "max_sentences": 3,
    }
    prompt_targets = {
        "execution.py": ["_perguntas_ia_v2_prompt_dados", "_perguntas_ia_v2_prompt"],
        "tools.py": [
            "_ia_agent_perguntas_blocos_prompt",
            "_ia_agent_perguntas_prompt_pos_venda",
            "_ia_agent_perguntas_prompt_regulado",
            "_ia_agent_perguntas_prompt_pre_venda",
            "_ia_agent_perguntas_categoria_regulada",
            "_ia_agent_perguntas_montar_prompt",
        ],
        "client_workflows.py": [
            "_compatibility_prompt", "_initial_general_response", "_context_hub_response", "_web_fallback",
        ],
        "general_commercial.py": [
            "_general_fit_evaluation_prompt", "_general_research_final_prompt",
        ],
        "clients.py": ["_generate_public_compatibility_answer"],
        "ml_questions_gemini/prompt_builder.py": ["build"],
        "backend/modules/perguntas_pos_venda/endpoints/training.py": ["ml_ia_treinamento_simular"],
        "backend/services/ia_treinamento_ppv.py": ["_ia_treinamento_ppv_profile_v2_bloco_prompt"],
        "backend/services/perguntas_pos_venda_perguntas_ml.py": ["_perguntas_ia_gerar_resposta"],
    }
    prompts: dict[str, str] = {}
    for filename, names in prompt_targets.items():
        source_path = ROOT / filename if "/" in filename else PACKAGE / filename
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: ast.get_source_segment(source, node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
        }
        prompts.update({f"{filename}:{name}": functions[name] for name in names})
    return {
        "routes": routes,
        "aliases": aliases,
        "schema": IAAgentQueryRequest.model_json_schema(),
        "policy": policy,
        "prompts": prompts,
        "exports": list(perguntas_pos_venda_agent.__all__),
    }


def test_ppv_public_contract_snapshot() -> None:
    snapshot = _contract_snapshot()
    assert len(snapshot["routes"]) == 31
    assert len(snapshot["aliases"]) == 2
    assert len(snapshot["policy"]["allowed_tools"]) == 7
    assert snapshot["exports"] == PUBLIC_EXPORTS
    assert {name: _digest(value) for name, value in snapshot.items()} == CONTRACT_HASHES


def _agent_request(headers: dict[str, str]) -> Request:
    return Request({
        "type": "http",
        "headers": [
            (str(key).lower().encode("ascii"), str(value).encode("ascii"))
            for key, value in headers.items()
        ],
    })


def _bound_agent_headers(client_id: str, loja: str) -> dict[str, str]:
    import backend_api  # noqa: F401 - injects the legacy runtime dependencies used by state.py

    return _ia_agent_endpoint_headers(client_id, loja)


def test_agent_endpoint_binds_tenant_and_store_outside_model_input() -> None:
    headers = _bound_agent_headers("tenant-servidor", "Loja Servidor")
    assert headers["X-JK-Agent-Binding"] == "tenant-store-v1"
    request = _agent_request(headers)
    payload = IAAgentQueryRequest(classMethod="query", input={})
    generated = SimpleNamespace(answer="Resposta", model="codex:gpt-5.5", diagnostics=[])

    with patch.object(ia_endpoints.perguntas_agent_api, "authorize_request"), patch.object(
        ia_endpoints.perguntas_agent_api,
        "parse_request_input",
        return_value={"task": "mercado_livre_question_draft_v2"},
    ), patch.object(
        ia_endpoints.perguntas_agent_api,
        "generate_response",
        return_value=generated,
    ) as generate:
        result = ia_endpoints.ia_agent_perguntas_query(payload, request)

    bound_input = generate.call_args.args[1]
    assert generate.call_args.args[0] == "tenant-servidor"
    assert bound_input["tenant_id"] == "tenant-servidor"
    assert bound_input["store"] == "Loja Servidor"
    assert result["output"]["answer"] == "Resposta"


@pytest.mark.parametrize(
    "agent_input",
    [
        {"tenant_id": "tenant-atacante", "store": "Loja Servidor"},
        {"tenant_id": "tenant-servidor", "store": "Loja Atacante"},
    ],
)
def test_agent_endpoint_rejects_payload_tenant_or_store_divergence(agent_input: dict) -> None:
    headers = _bound_agent_headers("tenant-servidor", "Loja Servidor")
    request = _agent_request(headers)
    payload = IAAgentQueryRequest(classMethod="query", input={})
    parsed = {"task": "mercado_livre_question_draft_v2", **agent_input}

    with patch.object(ia_endpoints.perguntas_agent_api, "authorize_request"), patch.object(
        ia_endpoints.perguntas_agent_api,
        "parse_request_input",
        return_value=parsed,
    ), patch.object(ia_endpoints.perguntas_agent_api, "generate_response") as generate:
        with pytest.raises(HTTPException) as exc_info:
            ia_endpoints.ia_agent_perguntas_query(payload, request)

    assert exc_info.value.status_code == 403
    generate.assert_not_called()


def test_ppv_facade_and_components_respect_budgets() -> None:
    assert len(FACADE.read_text(encoding="utf-8").splitlines()) <= 300
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 800, path.name
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 120, f"{path.name}:{node.name}"
            if isinstance(node, ast.ImportFrom):
                assert not any(alias.name == "*" for alias in node.names), path.name


def test_ppv_prefetch_component_is_pinned_in_installer_manifest() -> None:
    manifest = json.loads(
        (ROOT / "electron_app" / "installer-required-resources.json").read_text(encoding="utf-8")
    )
    source = "backend/modules/perguntas_pos_venda/ai/deep_research_prefetch.py"

    assert source in set(manifest.get("requiredSourceFiles") or [])
    assert source in set(manifest.get("requiredPackagedSourceParity") or [])
    assert f"local_app/{source}" in set(manifest.get("requiredPackagedFiles") or [])


def test_ppv_dependency_direction_has_no_cycles_or_legacy_imports() -> None:
    graph = {path.stem: _local_dependencies(path) for path in PACKAGE.glob("*.py")}
    for owner, dependencies in graph.items():
        for dependency in dependencies:
            assert LAYERS[dependency] <= LAYERS[owner], f"{owner} -> {dependency}"
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        assert module not in visiting, f"ciclo detectado em {module}"
        if module in visited:
            return
        visiting.add(module)
        for dependency in graph[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in graph:
        visit(module)
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "bind_runtime_globals" not in source, path.name
        assert "globals().update" not in source, path.name
        assert "PEER_EXPORTS" not in source, path.name
        assert "backend.services.perguntas_pos_venda_agent" not in source, path.name
        assert "backend.services.perguntas_pos_venda_core" not in source, path.name


def test_ppv_runtime_is_allowlisted_idempotent_and_thread_safe() -> None:
    sentinel = lambda: "sentinel"
    peers = {"public_model": sentinel, "not_allowlisted": object()}
    try:
        first = runtime.configure_runtime(peers=peers)
        second = runtime.configure_runtime(peers=peers)
        assert first is second
        assert first.models.public_model is sentinel
        assert callable(first.policies.public_requires_approval)
        assert callable(first.policies.post_sale_requires_approval)
        assert callable(first.telemetry.record)
        assert "not_allowlisted" not in first.overrides
        with ThreadPoolExecutor(max_workers=8) as executor:
            configured = list(executor.map(lambda _: runtime.configure_runtime(peers=peers), range(16)))
        assert all(item is first for item in configured)
        assert runtime.configure_runtime(first) is first
    finally:
        runtime.configure_runtime()


def test_ppv_private_helpers_are_not_reexported_or_consumed_from_facade() -> None:
    assert list(perguntas_pos_venda_agent.__all__) == PUBLIC_EXPORTS
    assert not any(name.startswith("_ia_agent_") for name in vars(perguntas_pos_venda_agent))
    assert perguntas_pos_venda_core._AGENT_PRIVATE_EXPORTS.isdisjoint(perguntas_pos_venda_core.__all__)
    for path in (ROOT / "backend").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "backend.services.perguntas_pos_venda_agent":
                assert not any(alias.name.startswith("_") for alias in node.names), path
