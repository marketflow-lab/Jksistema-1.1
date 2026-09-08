from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path

from fastapi.params import Depends

from backend.routers.perguntas_pos_venda import create_perguntas_pos_venda_router
from backend.schemas import ia
from backend.schemas import perguntas_pos_venda as ppv_schemas
from backend.services import perguntas_pos_venda_endpoints as endpoints


ROOT = Path(__file__).resolve().parents[1]
FACADE = ROOT / "backend" / "services" / "perguntas_pos_venda_endpoints.py"
PACKAGE = ROOT / "backend" / "modules" / "perguntas_pos_venda" / "endpoints"
MODEL_TYPES = (
    ppv_schemas.PerguntasLojaConfigRequest,
    ppv_schemas.PerguntasLojasConfigLoteRequest,
    ppv_schemas.PerguntasAprovacaoRequest,
    ppv_schemas.PerguntasGerarRespostaRequest,
    ppv_schemas.PerguntasEnviarRespostaRequest,
    ppv_schemas.MLQuestionsV2ProcessRequest,
    ppv_schemas.MLQuestionsV2ReviewActionRequest,
    ppv_schemas.PosVendaMensagemRequest,
    ppv_schemas.PosVendaGerarRespostaRequest,
    ia.IAChatRequest,
    ia.IATreinamentoPerguntasPosVendaRequest,
    ia.IATreinamentoPerguntasPosVendaSimularRequest,
)
CONTRACT_HASHES = {
    # Four additive read endpoints; all legacy exports, signatures and schemas remain frozen.
    "routes": "c8168de81d0cf383e8a631ef457ac9fe7f8fb5ed6c22cdaf34f70c27902230c1",
    "exports": "df0113be39e4824acefe0f41818ad35128010e58124b2f74afac3dd7be97fb0c",
    "signatures": "21f09c1e6714a385533ec6890799b510c56b92aef59af053545b7a658fba098b",
    # Additive editor CAS fields; public edits now require revision and target.
    "schemas": "d7d403aeab996cca5539c5344f4b9100d515e9f36597d1fa0d7be9f144250271",
    "http_status_codes": "c06ceb69456b4923815b0461ddeef6f9c90b6fcf5c0568dd79b3fdbc888a4546",
    "governance": "397babf72ba67a71b0d09d49bbb87bf01be0d04cee90dd595bd4fedbbbded76d",
}
GOVERNANCE_NAMES = (
    "_ML_POS_VENDA_RECENT_DAYS",
    "_ML_POS_VENDA_SYNC_TTL_SECONDS",
    "_ML_POS_VENDA_MAX_MESSAGE_WORKERS",
    "_ML_POS_VENDA_REMOTE_CURSOR_LIMIT",
)


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _annotation(value: object) -> str:
    if value is inspect.Signature.empty:
        return ""
    if isinstance(value, str):
        return value
    return str(getattr(value, "__qualname__", repr(value)))


def _default(value: object) -> object:
    if value is inspect.Signature.empty:
        return {"kind": "required"}
    if isinstance(value, Depends):
        return {
            "kind": "Depends",
            "dependency": str(getattr(value.dependency, "__name__", "")),
            "use_cache": value.use_cache,
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _signature(function) -> dict[str, object]:
    signature = inspect.signature(function)
    return {
        "parameters": [
            {
                "name": parameter.name,
                "kind": parameter.kind.name,
                "annotation": _annotation(parameter.annotation),
                "default": _default(parameter.default),
            }
            for parameter in signature.parameters.values()
        ],
        "return": _annotation(signature.return_annotation),
        "async": inspect.iscoroutinefunction(function),
    }


def _source_paths() -> list[Path]:
    return [FACADE, *sorted(PACKAGE.glob("*.py"))] if PACKAGE.exists() else [FACADE]


def _status_codes() -> list[int]:
    values: set[int] = set()
    for path in _source_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "HTTPException":
                continue
            for keyword in node.keywords:
                if keyword.arg == "status_code" and isinstance(keyword.value, ast.Constant):
                    values.add(int(keyword.value.value))
    return sorted(values)


def _governance() -> dict[str, int]:
    values: dict[str, int] = {}
    for path in _source_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in GOVERNANCE_NAMES:
                values[target.id] = int(ast.literal_eval(node.value))
    return {
        "recent_days": values["_ML_POS_VENDA_RECENT_DAYS"],
        "sync_ttl_seconds": values["_ML_POS_VENDA_SYNC_TTL_SECONDS"],
        "max_message_workers": values["_ML_POS_VENDA_MAX_MESSAGE_WORKERS"],
        "remote_cursor_limit": values["_ML_POS_VENDA_REMOTE_CURSOR_LIMIT"],
        "endpoint_count": len(endpoints.PERGUNTAS_POS_VENDA_ENDPOINTS),
    }


def _snapshot() -> dict[str, object]:
    names = ["configure_perguntas_pos_venda_endpoints_runtime", *endpoints.PERGUNTAS_POS_VENDA_ENDPOINTS]
    routes = sorted(
        [
            {"path": route.path, "methods": sorted(route.methods or []), "name": route.name}
            for route in create_perguntas_pos_venda_router().routes
        ],
        key=lambda item: (item["path"], item["methods"], item["name"]),
    )
    return {
        "routes": routes,
        "exports": list(endpoints.__all__),
        "signatures": {name: _signature(getattr(endpoints, name)) for name in names},
        "schemas": {model.__name__: model.model_json_schema() for model in MODEL_TYPES},
        "http_status_codes": _status_codes(),
        "governance": _governance(),
    }


def test_perguntas_pos_venda_endpoints_contract_snapshot() -> None:
    snapshot = _snapshot()
    assert len(snapshot["routes"]) == 35
    assert len(snapshot["exports"]) == 33
    assert len(snapshot["schemas"]) == 12
    assert {name: _digest(value) for name, value in snapshot.items()} == CONTRACT_HASHES


def test_progressive_question_routes_have_authenticated_read_contracts() -> None:
    from backend.routers.perguntas_pos_venda import PERGUNTAS_LOADING_ROUTES
    routes = {route.path: route for route in create_perguntas_pos_venda_router().routes}
    assert {spec.path.rsplit("/", 1)[-1] for spec in PERGUNTAS_LOADING_ROUTES} == {
        "lista", "itens", "detalhe", "resumo",
    }
    expected = {
        "lista": {"request", "store_id", "loja", "status", "offset", "limit", "forcar", "client_id"},
        "itens": {"request", "store_id", "item_ids", "forcar", "client_id"},
        "detalhe": {"request", "store_id", "question_id", "forcar", "client_id"},
        "resumo": {"request", "store_ids", "store_id", "metricas", "forcar", "client_id"},
    }
    for spec in PERGUNTAS_LOADING_ROUTES:
        route = routes[spec.path]
        signature = inspect.signature(route.endpoint)
        assert route.methods == {"GET"}
        assert set(signature.parameters) == expected[spec.path.rsplit("/", 1)[-1]]
        tenant = signature.parameters["client_id"].default
        assert isinstance(tenant, Depends) and tenant.dependency.__name__ == "get_tenant_id"
        assert signature.parameters["forcar"].default is False
