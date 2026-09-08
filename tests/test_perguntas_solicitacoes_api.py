from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.params import Depends

from backend.modules.perguntas_pos_venda.endpoints import api
from backend.modules.perguntas_pos_venda.endpoints import jobs
from backend.modules.perguntas_pos_venda.endpoints import manual_questions
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.routers.perguntas_pos_venda import create_perguntas_pos_venda_router
from backend.schemas import PerguntasGerarRespostaRequest
from backend.services import codex_assistant_storage
from backend.services import cadastro_compatibilidade
from backend.services import perguntas_pos_venda_codex as orchestrator
from backend.services.codex.storage import customer_reply_state


EXPECTED_ROUTE = "/api/mercadolivre/assistant/solicitacoes"
FORBIDDEN_KEYS = {
    "access_token",
    "authorization",
    "buyer",
    "buyer_id",
    "contexto",
    "created_by",
    "email",
    "guidance_applied",
    "idempotency_key",
    "phone",
    "prompt",
    "prompt_hash",
    "request",
    "research_history",
    "result",
    "scope_verifiers",
    "sources",
    "subquestions",
    "thread_id",
    "tool_calls",
    "tool_outputs",
}


def _save(
    info_base: str,
    tenant: str,
    *,
    job_id: str,
    store_id: str,
    store_name: str = "Loja repetida",
    status: str = "completed",
    created_at: str,
    conclusion: str = "Resposta pronta para revisão.",
    seller_id: str = "",
    site_id: str = "MLB",
) -> None:
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base,
        tenant,
        {
            "job_id": job_id,
            "profile": "mercado_livre_customer_reply",
            "task_type": "public_question",
            "subject_key": f"question:{job_id}",
            "event_subject_key": f"Q-{job_id}",
            "question_id": f"Q-{job_id}",
            "item_id": f"MLB-{job_id}",
            "store": store_name,
            "store_id": store_id,
            "seller_id": seller_id or ("seller-alpha" if store_id == "store-alpha" else "seller-beta"),
            "site_id": site_id,
            "client_id": tenant,
            "status": status,
            "agent_state": "aguardando_aprovacao" if status == "completed" else "pesquisando",
            "current_step": "aprovar" if status == "completed" else "consultar",
            "attempt_count": 2,
            "created_at": created_at,
            "updated_at": created_at,
            "access_token": "segredo-token",
            "created_by": "pessoa@example.test",
            "request": {
                "pergunta": {
                    "text": "conteudo privado da pergunta",
                    "from": {"id": "buyer-secret"},
                }
            },
            "scope_verifiers": {"buyer_id": "buyer-secret"},
            "research_history": [{"query": "consulta interna secreta"}],
            "tool_outputs": [{"raw": "saida interna secreta"}],
            "result": {
                "resposta": conclusion,
                "contexto": {"access_token": "segredo-no-contexto"},
                "warnings": ["Revisar antes de enviar."],
                "data_sufficient": True,
                "requires_approval": True,
                "completion_reason": "evidence_confirmed",
            },
        },
    )


def _walk_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key).lower())
            keys.update(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_walk_keys(item))
    return keys


def _request(tenant: str = "tenant-a") -> Request:
    request = Request({"type": "http", "method": "GET", "path": EXPECTED_ROUTE})
    request.state.username = "tester"
    request.state.client_id = tenant
    return request


@pytest.fixture(autouse=True)
def authorized_store_fixture(monkeypatch) -> None:
    rows = [
        {
            "store_id": "store-alpha",
            "nome": "Loja repetida",
            "integracoes": {"mercadolivre": {"user_id": "seller-alpha", "site_id": "MLB"}},
        },
        {
            "store_id": "store-beta",
            "nome": "Loja repetida",
            "integracoes": {"mercadolivre": {"user_id": "seller-beta", "site_id": "MLB"}},
        },
    ]
    monkeypatch.setattr(
        jobs.questions_loading_support,
        "authorized_stores",
        lambda request, client_id: rows
        if request.state.client_id == client_id and request.state.username
        else [],
    )


def _list(**kwargs):
    handler = getattr(api, "ml_customer_reply_solicitacoes_list")
    client_id = kwargs.pop("client_id", "tenant-a")
    return handler(request=_request(client_id), client_id=client_id, **kwargs)


def test_solicitacoes_route_uses_authenticated_tenant_dependency() -> None:
    handler = getattr(api, "ml_customer_reply_solicitacoes_list")
    parameter = inspect.signature(handler).parameters["client_id"]

    assert isinstance(parameter.default, Depends)
    assert parameter.default.dependency is get_tenant_id
    assert any(
        route.path == EXPECTED_ROUTE
        and route.name == "ml_customer_reply_solicitacoes_list"
        and route.methods == {"GET"}
        for route in create_perguntas_pos_venda_router().routes
    )


def test_solicitacoes_are_isolated_by_tenant_and_exact_store_id_after_restart(
    tmp_path, monkeypatch
) -> None:
    info_base = str(tmp_path)
    monkeypatch.setattr(orchestrator, "_RUNTIME", SimpleNamespace(PASTA_INFO=info_base))
    _save(
        info_base,
        "tenant-a",
        job_id="alpha",
        store_id="store-alpha",
        created_at="2026-09-08T10:00:00+00:00",
    )
    _save(
        info_base,
        "tenant-a",
        job_id="beta",
        store_id="store-beta",
        created_at="2026-09-08T11:00:00+00:00",
    )
    _save(
        info_base,
        "tenant-b",
        job_id="other-tenant",
        store_id="store-alpha",
        created_at="2026-09-08T12:00:00+00:00",
    )

    # Simula reinicio: store_id/seller/site precisam sobreviver no SQLite, e
    # a listagem nao pode depender do cache transitorio do processo criador.
    with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
        customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()

    alpha = _list(store_id="store-alpha", status="", limit=20, offset=0)
    beta = _list(store_id="store-beta", status="", limit=20, offset=0)

    assert [item["job_id"] for item in alpha["solicitacoes"]] == ["alpha"]
    assert [item["job_id"] for item in beta["solicitacoes"]] == ["beta"]
    assert alpha["solicitacoes"][0]["loja"] == beta["solicitacoes"][0]["loja"]
    assert alpha["solicitacoes"][0]["store_id"] == "store-alpha"
    assert beta["solicitacoes"][0]["store_id"] == "store-beta"
    assert "other-tenant" not in json.dumps(alpha, ensure_ascii=False)


def test_solicitacoes_projection_is_closed_and_does_not_expose_sensitive_job_state(
    tmp_path, monkeypatch
) -> None:
    info_base = str(tmp_path)
    monkeypatch.setattr(orchestrator, "_RUNTIME", SimpleNamespace(PASTA_INFO=info_base))
    _save(
        info_base,
        "tenant-a",
        job_id="safe-view",
        store_id="store-alpha",
        created_at="2026-09-08T13:00:00+00:00",
        conclusion="Conclusao que pode aparecer ao operador.",
    )

    response = _list(store_id="store-alpha", status="completed", limit=20, offset=0)
    encoded = json.dumps(response, ensure_ascii=False).lower()
    item = response["solicitacoes"][0]

    assert item["job_id"] == "safe-view"
    assert item["status"] == "completed"
    assert item["conclusao"]
    assert FORBIDDEN_KEYS.isdisjoint(_walk_keys(response))
    for secret in (
        "segredo-token",
        "segredo-no-contexto",
        "buyer-secret",
        "pessoa@example.test",
        "consulta interna secreta",
        "saida interna secreta",
    ):
        assert secret not in encoded


def test_solicitacoes_scope_requires_matching_store_seller_and_site(
    tmp_path, monkeypatch
) -> None:
    info_base = str(tmp_path)
    monkeypatch.setattr(orchestrator, "_RUNTIME", SimpleNamespace(PASTA_INFO=info_base))
    _save(
        info_base,
        "tenant-a",
        job_id="authorized",
        store_id="store-alpha",
        status="completed",
        created_at="2026-09-08T13:00:00+00:00",
    )
    _save(
        info_base,
        "tenant-a",
        job_id="wrong-seller",
        store_id="store-alpha",
        seller_id="seller-intruso",
        status="running",
        created_at="2026-09-08T13:01:00+00:00",
    )
    _save(
        info_base,
        "tenant-a",
        job_id="wrong-site",
        store_id="store-alpha",
        site_id="MLA",
        status="failed",
        created_at="2026-09-08T13:02:00+00:00",
    )

    response = _list(store_id="store-alpha", status="", limit=20, offset=0)

    assert [item["job_id"] for item in response["solicitacoes"]] == ["authorized"]
    assert response["total"] == 1
    assert response["status_resumo"] == {"completed": 1}
    assert "wrong-seller" not in json.dumps(response)
    assert "wrong-site" not in json.dumps(response)


def test_solicitacoes_filter_order_pagination_and_status_summary(
    tmp_path, monkeypatch
) -> None:
    info_base = str(tmp_path)
    monkeypatch.setattr(orchestrator, "_RUNTIME", SimpleNamespace(PASTA_INFO=info_base))
    fixtures = (
        ("old-completed", "completed", "2026-09-08T09:00:00+00:00"),
        ("running", "running", "2026-09-08T10:00:00+00:00"),
        ("new-completed", "completed", "2026-09-08T11:00:00+00:00"),
        ("newest-completed", "completed", "2026-09-08T12:00:00+00:00"),
    )
    for job_id, status, created_at in fixtures:
        _save(
            info_base,
            "tenant-a",
            job_id=job_id,
            store_id="store-alpha",
            status=status,
            created_at=created_at,
        )

    first = _list(store_id="store-alpha", status="completed", limit=2, offset=0)
    second = _list(store_id="store-alpha", status="completed", limit=2, offset=2)

    assert [item["job_id"] for item in first["solicitacoes"]] == [
        "newest-completed",
        "new-completed",
    ]
    assert [item["job_id"] for item in second["solicitacoes"]] == ["old-completed"]
    assert first["total"] == second["total"] == 3
    assert first["limit"] == 2 and first["offset"] == 0
    assert second["limit"] == 2 and second["offset"] == 2
    assert first["status_resumo"]["completed"] == 3
    assert first["status_resumo"].get("running", 0) == 1


def test_job_identity_resolution_uses_exact_store_id_for_homonymous_stores(
    monkeypatch,
) -> None:
    tenants: list[str] = []

    def load_stores(client_id: str):
        tenants.append(client_id)
        return [
            {
                "store_id": "store-alpha",
                "nome": "Loja repetida",
                "integracoes": {
                    "mercadolivre": {"user_id": "seller-alpha", "site_id": "MLB"}
                },
            },
            {
                "store_id": "store-beta",
                "nome": "Loja repetida",
                "integracoes": {
                    "mercadolivre": {"user_id": "seller-beta", "site_id": "MLB"}
                },
            },
        ]

    monkeypatch.setattr(
        orchestrator,
        "_RUNTIME",
        SimpleNamespace(carregar_lojas=load_stores),
    )
    monkeypatch.setattr(
        cadastro_compatibilidade,
        "resolver_loja_ativa_para_leitura",
        lambda client_id, loja, store_id: {
            "store_id": store_id,
            "nome": loja,
        }
        if client_id == "tenant-a"
        else {},
    )

    identity = orchestrator._resolve_job_store_identity(
        "tenant-a",
        "Loja repetida",
        store_id="store-beta",
    )

    assert tenants == ["tenant-a"]
    assert identity == {
        "store_id": "store-beta",
        "seller_id": "seller-beta",
        "site_id": "MLB",
    }


def test_manual_generation_propagates_canonical_store_id_to_created_job(
    monkeypatch,
) -> None:
    captured: dict = {}

    def create_job(**kwargs):
        captured.update(kwargs)
        return {"job_id": "job-beta", "status": "queued", "queued": True}

    monkeypatch.setattr(manual_questions.perguntas_pos_venda_codex, "enabled", lambda: True)
    monkeypatch.setattr(manual_questions.perguntas_pos_venda_codex, "create_job", create_job)
    monkeypatch.setattr(
        manual_questions,
        "resolve_scope",
        lambda request, client_id, store_id: SimpleNamespace(
            client_id=client_id,
            store_id=store_id,
            seller_id="seller-beta",
            site_id="MLB",
            name="Loja repetida",
        ),
    )
    request = PerguntasGerarRespostaRequest(
        loja="Loja repetida",
        store_id="store-beta",
        pergunta={
            "id": "Q-beta",
            "store_id": "store-beta",
            "item_id": "MLB-beta",
            "text": "Serve?",
        },
        **{"async": True},
    )

    response = manual_questions.ml_perguntas_gerar_resposta_manual(
        request,
        _request("tenant-a"),
        client_id="tenant-a",
    )

    assert request.store_id == "store-beta"
    assert captured["client_id"] == "tenant-a"
    assert captured["store_id"] == "store-beta"
    assert captured["request"]["pergunta"]["store_id"] == "store-beta"
    assert response["job_id"] == "job-beta"
