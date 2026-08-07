import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.schemas import IARagDocumento, IARagIndexRequest, IARagReindexRequest
from backend.services import codex_console, ia_endpoints, ia_rag
from backend.services.codex.console import runtime as console_runtime


def test_legacy_rag_flags_are_disabled_by_default(monkeypatch):
    for name in (
        "IA_RAG_LEGACY_READ_ENABLED",
        "IA_RAG_LEGACY_WRITE_ENABLED",
        "IA_RAG_LEGACY_GENERIC_SCAN_ENABLED",
        "IA_RAG_LEGACY_FORCE_REPLACE_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ia_rag, "psycopg", None, raising=False)

    config = ia_rag._ia_rag_config()

    assert config["legacy_read_enabled"] is False
    assert config["legacy_write_enabled"] is False
    assert config["legacy_generic_scan_enabled"] is False
    assert config["legacy_force_replace_enabled"] is False


def test_legacy_context_does_not_search_when_read_is_disabled(monkeypatch):
    monkeypatch.delenv("IA_RAG_LEGACY_READ_ENABLED", raising=False)
    monkeypatch.setattr(
        ia_rag.IA_RAG_SEARCH_EXECUTOR,
        "submit",
        lambda *_args, **_kwargs: pytest.fail("legacy search must remain disabled"),
    )

    assert ia_rag._ia_rag_contexto("consulta", "000002") == ""


def test_legacy_index_write_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("IA_RAG_LEGACY_WRITE_ENABLED", raising=False)
    document = IARagDocumento(source="test", title="test", content="safe")

    with pytest.raises(PermissionError, match="desativada"):
        ia_rag._ia_rag_indexar_documentos("000002", [document])


def test_legacy_index_dlp_blocks_secret_before_database_write(monkeypatch):
    monkeypatch.setenv("IA_RAG_LEGACY_WRITE_ENABLED", "true")
    monkeypatch.setattr(
        ia_rag,
        "_ia_rag_local_conectar",
        lambda _client: pytest.fail("database must not be opened after a DLP blocker"),
    )
    secret = "segredo-que-nao-pode-vazar"
    document = IARagDocumento(
        source="manual",
        title="teste",
        content=f"access_token={secret}",
    )

    with pytest.raises(ValueError) as exc:
        ia_rag._ia_rag_indexar_documentos("000002", [document])

    assert "assigned_secret" in str(exc.value)
    assert secret not in str(exc.value)


def test_generic_recursive_scan_requires_separate_opt_in(monkeypatch):
    monkeypatch.delenv("IA_RAG_LEGACY_GENERIC_SCAN_ENABLED", raising=False)
    monkeypatch.setattr(ia_rag, "_ia_rag_docs_csv_cadastro", lambda _client: [])
    monkeypatch.setattr(ia_rag, "_ia_rag_docs_csv_estoque", lambda _client: [])
    monkeypatch.setattr(ia_rag, "_ia_rag_docs_db_vendas", lambda _client: [])
    monkeypatch.setattr(
        ia_rag,
        "_ia_rag_docs_dados_completos",
        lambda _client: pytest.fail("generic recursive scan must remain disabled"),
    )

    assert ia_rag._ia_rag_docs_app("000002") == []


def test_force_replacement_requires_independent_opt_in(monkeypatch):
    monkeypatch.setenv("IA_RAG_LEGACY_WRITE_ENABLED", "true")
    monkeypatch.delenv("IA_RAG_LEGACY_FORCE_REPLACE_ENABLED", raising=False)
    monkeypatch.setattr(ia_rag, "_ia_rag_ativo", lambda: True)
    monkeypatch.setattr(
        ia_rag,
        "_ia_rag_docs_app",
        lambda _client: pytest.fail("force bloqueado nao deve sequer varrer documentos"),
    )

    with pytest.raises(HTTPException) as exc:
        ia_rag._ia_rag_reindexar_app("000002", force=True)

    assert exc.value.status_code == 403


def test_force_dlp_preflight_runs_before_removing_active_corpus(monkeypatch):
    monkeypatch.setenv("IA_RAG_LEGACY_WRITE_ENABLED", "true")
    monkeypatch.setenv("IA_RAG_LEGACY_FORCE_REPLACE_ENABLED", "true")
    monkeypatch.setattr(ia_rag, "_ia_rag_ativo", lambda: True)
    document = IARagDocumento(
        source="jkdata:test",
        title="teste",
        content="access_token=segredo-que-nao-pode-vazar",
    )
    monkeypatch.setattr(ia_rag, "_ia_rag_docs_app", lambda _client: [document])
    monkeypatch.setattr(
        ia_rag,
        "_ia_rag_limpar_fontes",
        lambda *_args, **_kwargs: pytest.fail("corpus ativo nao pode ser removido apos bloqueio DLP"),
    )

    with pytest.raises(ValueError) as exc:
        ia_rag._ia_rag_reindexar_app("000002", force=True)

    assert "segredo-que-nao-pode-vazar" not in str(exc.value)


def test_local_status_never_exposes_absolute_database_path(monkeypatch, tmp_path):
    db_path = tmp_path / "ia_rag_local.db"
    monkeypatch.setattr(ia_rag, "_ia_rag_local_db_path", lambda _client: str(db_path))
    monkeypatch.setattr(ia_rag, "_ia_rag_local_conectar", lambda _client: (_ for _ in ()).throw(OSError("blocked")))

    status = ia_rag._ia_rag_local_status("000002")

    assert "local_db_path" not in status
    assert status["local_db_name"] == "ia_rag_local.db"
    assert status["local_error"] == "OSError"


def test_legacy_index_and_reindex_routes_reject_non_full_user(monkeypatch):
    monkeypatch.setattr(console_runtime, "_codex_require_authenticated",
        lambda _request, _authorization: {
            "client_id": "000002",
            "username": "operador",
            "permissions": {"ia": True},
        },
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ia/rag/indexar",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8001),
        }
    )

    with pytest.raises(HTTPException) as index_error:
        asyncio.run(
            ia_endpoints.ia_rag_indexar(
                IARagIndexRequest(documents=[]),
                request,
                "Bearer test",
                "000002",
            )
        )
    assert index_error.value.status_code == 403

    with pytest.raises(HTTPException) as reindex_error:
        asyncio.run(
            ia_endpoints.ia_rag_reindexar(
                request,
                IARagReindexRequest(force=True),
                "Bearer test",
                "000002",
            )
        )
    assert reindex_error.value.status_code == 403
