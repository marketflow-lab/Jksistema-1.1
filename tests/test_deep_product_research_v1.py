from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from unittest.mock import patch

from backend.modules.perguntas_pos_venda.ai import deep_research
from backend.modules.perguntas_pos_venda.ai import deep_research_crawler
from backend.modules.perguntas_pos_venda.ai import deep_research_fingerprints
from backend.modules.perguntas_pos_venda.ai import deep_research_prefetch
from backend.modules.perguntas_pos_venda.ai import provider_transport as research_transport
from backend.modules.perguntas_pos_venda.ai import sources as research_sources
from backend.modules.perguntas_pos_venda.ai.deep_research import (
    ResearchDocumentV1,
    ResearchSessionV1,
    apparent_coverage_complete,
    canonical_research_url,
    classify_research_source,
    document_matches_product,
    expected_fields_from_question,
    expected_fields_for_research,
    extract_guarded_commercial_claims,
    extract_technical_claims,
    persist_research_documents,
    research_session,
)
from backend.modules.perguntas_pos_venda.ai.official_source_registry import (
    official_domains_for_target_identity,
)
from backend.services import context_hub


VIN = "1M8GDM9AXKP042788"
PERCENT_ENCODED_VIN_QUERY = "Peugeot%20" + "".join(
    f"%{ord(character):02X}" for character in f"VIN {VIN}"
)


def _agent_input(*, question: str = "Qual a voltagem?") -> dict:
    return {
        "tenant_id": "tenant-a",
        "store": "store-a",
        "question": {"text": question},
        "item": {
            "id": "MLB100",
            "title": "Caixa BSM Peugeot 206 207 307",
            "seller_sku": "BSM-9661682980",
            "attributes": [
                {"id": "BRAND", "name": "Marca", "value_name": "Peugeot"},
                {"id": "PART_NUMBER", "name": "Part number", "value_name": "9661682980"},
            ],
        },
        "product_evidence_identity": {
            "store_ref": "store-a",
            "seller_id": "seller-a",
            "site_id": "MLB",
            "sku": "BSM-9661682980",
            "item_id": "MLB100",
            "variation_id": "",
        },
        "vehicle_identity": {
            "status": "confirmed",
            "make": "PEUGEOT",
            "model": "206",
            "model_year": "2012",
        },
    }


def _generic_component_input() -> dict:
    agent_input = _agent_input(question="Qual a voltagem?")
    agent_input["vehicle_identity"] = {}
    agent_input["item"].update(
        {
            "title": "Componente eletrico Acme 12v",
            "attributes": [{"id": "BRAND", "name": "Marca", "value_name": "Acme"}],
        }
    )
    return agent_input


def _document(seed: str, text: str, *, source_type: str = "official_manufacturer") -> ResearchDocumentV1:
    return ResearchDocumentV1(
        url=f"https://service.peugeot.fr/catalog/{seed}",
        title="Technical catalog",
        query_type="product_specification_by_code",
        query="BSM 9661682980 ficha tecnica",
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source_type=source_type,
        origin_key="peugeot.fr",
    )


def _strict_fetcher(transport: httpx.AsyncBaseTransport):
    def fetch(*args, **kwargs):
        return research_transport.fetch_research_response(
            *args,
            **kwargs,
            transport=transport,
        )

    return fetch


@pytest.fixture(autouse=True)
def _clear_sessions() -> None:
    with deep_research._SESSION_LOCK:
        deep_research._SESSIONS.clear()


@pytest.fixture
def evidence_env(tmp_path: Path) -> Path:
    info_root = tmp_path / "info"
    info_root.mkdir()
    context_hub.configure_context_hub(
        base_dir=tmp_path,
        info_root=info_root,
        surface="test",
    )
    return info_root


@pytest.mark.parametrize(
    "query",
    [
        f"Peugeot {VIN} compatibilidade",
        "Peugeot chassi 1M8G DM9AX KP04 2788 compatibilidade",
        "Peugeot VIN 8 A D 2 M K F W X C G 0 3 5 6 1 5 compatibilidade",
        "chassi informado abaixo 8 A D 2 M K F W X C G 0 3 5 6 1 5",
        "Peugeot 8 A D 2 M K F W X C G 0 3 5 6 1 5 compatibilidade",
        "Peugeot chassi 1M8GDM9AXKP04278I compatibilidade",
        f"Peugeot VIN {VIN} ou VIN 9BWZZZ377VT004251",
        PERCENT_ENCODED_VIN_QUERY,
        PERCENT_ENCODED_VIN_QUERY.replace("%", "%25"),
        PERCENT_ENCODED_VIN_QUERY.replace("%", "%25").replace("%", "%25"),
        "comprador%40example.com",
        "CPF%20000.000.000-00",
    ],
)
def test_provider_budget_rejects_every_vin_shaped_query(query: str) -> None:
    session = ResearchSessionV1(
        key="job-a",
        started_monotonic=time.monotonic(),
        expires_monotonic=time.monotonic() + 600,
    )

    assert session.reserve_queries([{"query": query, "type": "compatibility"}]) == []
    assert session.queries_used == set()


def test_research_history_never_retains_vin_queries() -> None:
    session = research_session(
        {
            "_codex_job_id": "job-history",
            "research_history": [
                {
                    "queries": [
                        f"Peugeot {VIN} compatibilidade",
                        "Peugeot 206 BSM 9661682980",
                    ]
                }
            ],
        }
    )

    assert all(VIN.casefold() not in query for query in session.queries_used)
    assert session.queries_used == {"peugeot 206 bsm 9661682980"}


def test_research_budget_is_isolated_when_job_ids_collide_across_tenants() -> None:
    tenant_a = research_session(
        {
            "_codex_job_id": "same-job",
            "tenant_id": "tenant-a",
            "store": "store-a",
            "product_evidence_identity": {"seller_id": "seller-a", "site_id": "MLB"},
        }
    )
    tenant_a.reserve_queries([{"query": "produto privado tenant a", "type": "web"}])
    tenant_b = research_session(
        {
            "_codex_job_id": "same-job",
            "tenant_id": "tenant-b",
            "store": "store-b",
            "product_evidence_identity": {"seller_id": "seller-b", "site_id": "MLB"},
        }
    )

    assert tenant_b is not tenant_a
    assert tenant_b.queries_used == set()


def test_canonical_url_preserves_identity_parameters_and_removes_tracking() -> None:
    assert canonical_research_url(
        "https://Catalog.Example/product?id=B&utm_source=x&id=A#section"
    ) == "https://catalog.example/product?id=A&id=B"
    assert canonical_research_url("https://user:secret@example.com/product") == ""
    assert canonical_research_url("https://example.com:99999/product") == ""
    assert canonical_research_url("https://[2001:db8::1]/part?id=10") == (
        "https://[2001:db8::1]/part?id=10"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://maker.example/manual.pdf?access_token=SECRET123",
        "https://maker.example/manual.pdf?X-Amz-Signature=abc",
        "https://maker.example/manual.pdf?x-goog-credential=abc",
    ],
)
def test_canonical_url_rejects_signed_or_credential_bearing_sources(url: str) -> None:
    assert canonical_research_url(url) == ""


def test_page_budget_enforces_deadline_dedup_domain_and_total_limits(monkeypatch) -> None:
    now = 1000.0
    monkeypatch.setattr(deep_research.time, "monotonic", lambda: now)
    session = ResearchSessionV1(
        key="budget",
        started_monotonic=now,
        expires_monotonic=now + 600,
    )

    assert session.reserve_page("https://one.example/a") is True
    assert session.reserve_page("https://one.example/a") is False
    for index in range(1, 5):
        assert session.reserve_page(f"https://one.example/{index}") is True
    assert session.reserve_page("https://one.example/overflow") is False
    for index in range(2, 11):
        assert session.reserve_page(f"https://domain-{index}.example/a") is True
    assert session.reserve_page("https://domain-11.example/a") is False
    now += 301
    assert session.reserve_page("https://two.example/after-deadline") is False


@pytest.mark.parametrize(
    "url",
    [
        "https://maker.example/8-A-D-2-M-K-F-W-X-C-G-0-3-5-6-1-5/manual",
        "https://maker.example/%38%41%44%32%4D%4B%46%57%58%43%47%30%33%35%36%31%35/manual",
    ],
)
def test_page_budget_rejects_vin_in_plain_or_encoded_path(url: str) -> None:
    session = ResearchSessionV1(
        key="path-privacy",
        started_monotonic=time.monotonic(),
        expires_monotonic=time.monotonic() + 600,
    )

    assert session.reserve_page(url) is False


@pytest.mark.parametrize(
    "url",
    [
        f"https://maker.example/manual?vehicle={VIN}",
        f"https://maker.example/manual?{VIN}=vehicle",
        f"https://maker.example/manual#{VIN}",
        f"https://{VIN}.maker.example/manual",
    ],
)
def test_canonical_url_rejects_vin_anywhere_before_reader_or_hash(url: str) -> None:
    with patch.object(research_sources.requests, "get") as request_get, patch.object(
        research_sources.hashlib,
        "sha256",
        side_effect=AssertionError("unsafe URL reached hashing"),
    ):
        assert canonical_research_url(url) == ""
        assert research_sources._perguntas_ia_v2_ler_fonte_tecnica(
            url,
            "ficha tecnica",
            deep=True,
        ) == ""

    request_get.assert_not_called()


def test_canonical_url_rejects_deeply_nested_percent_encoded_vin() -> None:
    encoded = "".join(f"%{ord(character):02X}" for character in VIN)
    for _round in range(11):
        encoded = encoded.replace("%", "%25")
    url = f"https://maker.example/manual/{encoded}"

    with patch.object(research_sources.requests, "get") as request_get, patch.object(
        research_sources.hashlib,
        "sha256",
        side_effect=AssertionError("unsafe URL reached hashing"),
    ):
        assert canonical_research_url(url) == ""
        assert research_sources._perguntas_ia_v2_ler_fonte_tecnica(
            url,
            "ficha tecnica",
            deep=True,
        ) == ""

    request_get.assert_not_called()


def test_canonical_url_rejects_long_path_before_hidden_vin_reaches_reader() -> None:
    url = f"https://maker.example/{'a' * 5000}/{VIN}"
    with patch.object(research_sources.requests, "get") as request_get, patch.object(
        research_sources.hashlib,
        "sha256",
        side_effect=AssertionError("unsafe URL reached hashing"),
    ):
        assert canonical_research_url(url) == ""
        assert research_sources._perguntas_ia_v2_ler_fonte_tecnica(
            url,
            "ficha tecnica",
            deep=True,
        ) == ""
    request_get.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "https://maker.example/contact/5511987654321",
        "https://maker.example/manual?id=5511987654321",
        "https://maker.example/contact/%2B12025550123",
        "https://maker.example/manual?contact=202-555-0123",
    ],
)
def test_canonical_url_rejects_compact_brazilian_phone(url: str) -> None:
    assert canonical_research_url(url) == ""


def test_canonical_url_rejects_nested_redirect_destination() -> None:
    assert canonical_research_url(
        "https://httpbin.org/redirect-to?url=http://127.0.0.1/internal"
    ) == ""


@pytest.mark.parametrize(
    "url",
    [
        "https://maker.example:444/manual.pdf",
        "http://maker.example:8080/manual.pdf",
    ],
)
def test_nonstandard_public_ports_are_rejected(url: str) -> None:
    assert canonical_research_url(url) == ""
    assert research_sources._perguntas_ia_v2_url_fonte_tecnica_segura(url) is False


def test_default_public_ports_remain_canonical() -> None:
    assert canonical_research_url("https://maker.example:443/manual") == (
        "https://maker.example/manual"
    )
    assert canonical_research_url("http://maker.example:80/manual") == (
        "http://maker.example/manual"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1.nip.io/manual.pdf",
        "http://localtest.me/manual.pdf",
        "http://2130706433/manual.pdf",
        "http://0x7f000001/manual.pdf",
        "http://224.0.0.1/manual.pdf",
        "http://239.255.255.250/manual.pdf",
        "http://[ff02::1]/manual.pdf",
    ],
)
def test_reader_url_gate_rejects_ssrf_aliases_and_alternative_ip_forms(url: str) -> None:
    assert research_sources._perguntas_ia_v2_url_fonte_tecnica_segura(url) is False


def test_reader_url_gate_rejects_hostname_resolving_to_private_ip() -> None:
    private_resolution = [
        (2, 1, 6, "", ("127.0.0.1", 0)),
    ]
    with patch.object(research_sources.socket, "getaddrinfo", return_value=private_resolution):
        assert research_sources._perguntas_ia_v2_url_fonte_tecnica_segura(
            "https://docs.example.test/manual.pdf",
            resolve_dns=True,
        ) is False


def test_reader_never_follows_redirects_including_local_targets() -> None:
    class RedirectResponse:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/internal"}

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    response = RedirectResponse()
    with patch.object(
        research_sources.requests,
        "get",
        return_value=response,
    ) as request_get, patch.object(
        research_sources,
        "_perguntas_ia_v2_host_resolve_somente_publico",
        return_value=True,
    ):
        assert research_sources._perguntas_ia_v2_ler_fonte_tecnica(
            "https://maker.example/manual",
            "ficha tecnica",
            deep=True,
        ) == ""

    assert request_get.call_args.kwargs["allow_redirects"] is False
    assert request_get.call_count == 1
    assert response.closed is True


def test_reader_retries_read_timeout_once_and_uses_canonical_jina_url() -> None:
    page = "Part number: 9661682980\nVoltagem: 12 V"

    class Response:
        status_code = 200
        encoding = "utf-8"

        def __init__(self) -> None:
            self.closed = False

        def iter_content(self, *, chunk_size: int, decode_unicode: bool):
            assert chunk_size == 16 * 1024
            assert decode_unicode is False
            yield page.encode("utf-8")

        @staticmethod
        def raise_for_status() -> None:
            return None

        def close(self) -> None:
            self.closed = True

    response = Response()
    diagnostics: dict[str, object] = {}
    started = time.monotonic()
    deadline = started + 10
    with patch.object(
        research_sources,
        "fetch_research_response",
        side_effect=[research_sources.requests.exceptions.ReadTimeout(), response],
    ) as request_get, patch.object(
        research_sources,
        "_perguntas_ia_v2_host_resolve_somente_publico",
        return_value=True,
    ):
        content = research_sources._perguntas_ia_v2_ler_fonte_tecnica(
            "https://maker.example/manual",
            "ficha tecnica",
            deep=True,
            deadline_monotonic=deadline,
            diagnostics=diagnostics,
        )

    assert content == page
    assert request_get.call_count == 2
    assert request_get.call_args_list[0].args[0] == (
        "https://r.jina.ai/https://maker.example/manual"
    )
    connect_timeout, read_timeout = request_get.call_args.kwargs["timeout"]
    assert 0 < connect_timeout <= 5
    assert read_timeout >= connect_timeout
    assert connect_timeout + read_timeout <= deadline - started
    assert request_get.call_args.kwargs["allow_redirects"] is False
    assert diagnostics == {
        "read_timeout": True,
        "retry_success": True,
        "retry_attempted": True,
    }
    assert response.closed is True


@pytest.mark.parametrize("status_code", [429, 502, 503, 504])
def test_reader_retries_only_transient_http_status_once(status_code: int) -> None:
    class Response:
        encoding = "utf-8"

        def __init__(self, status: int, body: str = "") -> None:
            self.status_code = status
            self.body = body
            self.closed = False

        def iter_content(self, **_kwargs):
            yield self.body.encode("utf-8")

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise research_sources.requests.exceptions.HTTPError()

        def close(self) -> None:
            self.closed = True

    first = Response(status_code)
    second = Response(200, "Voltagem: 12 V")
    diagnostics: dict[str, object] = {}
    with patch.object(
        research_sources,
        "fetch_research_response",
        side_effect=[first, second],
    ) as request_get, patch.object(
        research_sources,
        "_perguntas_ia_v2_host_resolve_somente_publico",
        return_value=True,
    ):
        content = research_sources._perguntas_ia_v2_ler_fonte_tecnica(
            "https://maker.example/spec",
            "voltagem",
            deep=True,
            deadline_monotonic=time.monotonic() + 10,
            diagnostics=diagnostics,
        )

    assert content == "Voltagem: 12 V"
    assert request_get.call_count == 2
    assert first.closed is True
    assert second.closed is True
    assert diagnostics["retry_success"] is True


@pytest.mark.parametrize("status_code", [302, 403, 404])
def test_reader_never_retries_terminal_http_status(status_code: int) -> None:
    class Response:
        encoding = "utf-8"

        def __init__(self) -> None:
            self.status_code = status_code
            self.closed = False

        def raise_for_status(self) -> None:
            raise research_sources.requests.exceptions.HTTPError()

        def close(self) -> None:
            self.closed = True

    response = Response()
    with patch.object(
        research_sources,
        "fetch_research_response",
        return_value=response,
    ) as request_get, patch.object(
        research_sources,
        "_perguntas_ia_v2_host_resolve_somente_publico",
        return_value=True,
    ):
        assert research_sources._perguntas_ia_v2_ler_fonte_tecnica(
            "https://maker.example/spec",
            "voltagem",
            deep=True,
            deadline_monotonic=time.monotonic() + 10,
        ) == ""

    assert request_get.call_count == 1
    assert response.closed is True


def test_source_authority_uses_curated_exact_domains_not_brand_substrings() -> None:
    agent_input = _agent_input()
    item = {"title": "Peugeot technical catalog", "snippet": "manual tecnico"}

    assert classify_research_source(
        item,
        "https://service.peugeot.fr/catalog",
        agent_input,
        query_type="target_interface_official",
    ) == "official_manufacturer"
    assert classify_research_source(
        item,
        "https://peugeot-pecas-falsas.example/catalog",
        agent_input,
        query_type="target_interface_official",
    ) != "official_manufacturer"
    assert classify_research_source(
        item,
        "https://affordableparts.example/catalog",
        {**agent_input, "vehicle_identity": {"make": "FORD"}},
        query_type="target_interface_official",
    ) not in {"official_manufacturer", "official_oem"}


@pytest.mark.parametrize(
    ("brand", "url"),
    [
        ("Seaflo", "https://www.seaflo.com/catalog/pump"),
        ("Vonder", "https://vonder.com.br/catalogo/bomba"),
        ("Schneider", "https://www.se.com/br/catalog/product"),
        ("Grundfos", "https://product-selection.grundfos.com/catalog"),
        ("Cofap", "https://mmcofap.com.br/catalogo"),
        ("WEG", "https://weg.net/catalog"),
    ],
)
def test_source_authority_supports_exact_brand_roots_and_curated_aliases(
    brand: str,
    url: str,
) -> None:
    agent_input = _agent_input()
    agent_input["item"]["attributes"] = [
        {"id": "BRAND", "name": "Marca", "value_name": brand}
    ]

    assert classify_research_source(
        {"title": "Catalogo tecnico", "snippet": "manual tecnico"},
        url,
        agent_input,
    ) == "official_manufacturer"


def test_official_document_requires_exact_product_code_and_variation() -> None:
    agent_input = _agent_input()

    assert document_matches_product(
        "Caixa BSM Peugeot para 206 e 207",
        agent_input,
        source_type="official_manufacturer",
    ) is False
    assert document_matches_product(
        "Catalogo Peugeot BSM codigo 9661682980 para 206",
        agent_input,
        source_type="official_manufacturer",
    ) is True

    varied = _agent_input()
    varied["product_evidence_identity"]["variation_id"] = "V12"
    varied["item"]["variations"] = [
        {
            "id": "V12",
            "attribute_combinations": [{"name": "Voltagem", "value_name": "12 V"}],
        }
    ]
    assert document_matches_product(
        "Catalogo Peugeot BSM codigo 9661682980 24 V",
        varied,
        source_type="official_manufacturer",
    ) is False
    assert document_matches_product(
        "Catalogo Peugeot BSM codigo 9661682980 12 V",
        varied,
        source_type="official_manufacturer",
    ) is True


def test_multivariation_without_selection_never_matches_as_global() -> None:
    unresolved = _agent_input()
    unresolved["item"]["variations"] = [
        {
            "id": "V12",
            "seller_sku": "BSM-12",
            "attribute_combinations": [
                {"name": "Voltagem", "value_name": "12 V"}
            ],
        },
        {
            "id": "V24",
            "seller_sku": "BSM-24",
            "attribute_combinations": [
                {"name": "Voltagem", "value_name": "24 V"}
            ],
        },
    ]

    assert document_matches_product(
        "Catalogo Peugeot BSM codigo 9661682980 12 V",
        unresolved,
        source_type="official_manufacturer",
    ) is False


def test_product_without_variations_keeps_exact_global_fact_matching() -> None:
    product_global = _agent_input()

    assert product_global["product_evidence_identity"]["variation_id"] == ""
    assert "variations" not in product_global["item"]
    assert document_matches_product(
        "Catalogo Peugeot BSM codigo 9661682980 12 V",
        product_global,
        source_type="official_manufacturer",
    ) is True


def test_unmatched_safe_page_is_retained_as_source_only_lead(
    evidence_env: Path,
) -> None:
    agent_input = _agent_input()
    document = deep_research.make_research_document(
        item={"title": "Especificacoes de outro componente"},
        url="https://seo-specs-unknown.example/ficha-tecnica",
        query="BSM 9661682980 ficha tecnica",
        query_type="product_specification_by_code",
        text="Outro componente. Voltagem: 12 V. Potencia: 60 W.",
        agent_input=agent_input,
    )

    assert document is not None
    assert document.claim_eligible is False
    assert document.source_type == "blog"
    verified, metrics = persist_research_documents(
        "tenant-a",
        agent_input,
        [document],
        metrics={
            "pages_discovered": 1,
            "pages_attempted": 1,
            "pages_read": 1,
            "read_failures": 0,
            "read_timeouts": 0,
            "retry_successes": 0,
            "duration_ms": 5,
            "stop_reason": "no_new_facts",
        },
    )

    database = evidence_env / "tenant-a" / "context_hub" / "context_hub.db"
    with sqlite3.connect(database) as connection:
        source_count = connection.execute(
            "SELECT COUNT(*) FROM product_evidence_sources"
        ).fetchone()[0]
        claim_count = connection.execute(
            "SELECT COUNT(*) FROM product_evidence_claims"
        ).fetchone()[0]
    assert verified == []
    assert source_count == 1
    assert claim_count == 0
    assert metrics["pages_attempted"] == 1
    assert metrics["pages_read"] == 1
    assert metrics["coverage_complete"] is False
    assert metrics["fields_confirmed"] == 0


def test_local_seller_sku_alone_never_links_an_external_source() -> None:
    agent_input = _agent_input()
    agent_input["vehicle_identity"] = {}
    agent_input["item"] = {
        "id": "MLB-LOCAL",
        "title": "Componente eletrico Acme",
        "seller_sku": "LOCAL1234",
        "attributes": [{"id": "BRAND", "name": "Marca", "value_name": "Acme"}],
    }
    agent_input["product_evidence_identity"].update(
        {"sku": "LOCAL1234", "item_id": "MLB-LOCAL"}
    )

    document = deep_research.make_research_document(
        item={"title": "Componente LOCAL1234"},
        url="https://www.digikey.com/catalog/local1234",
        query="LOCAL1234 ficha tecnica",
        query_type="product_specification_by_code",
        text="Part number: LOCAL1234. Voltagem: 12 V.",
        agent_input=agent_input,
    )

    assert document is not None
    assert document.source_type == "technical_distributor"
    assert document.claim_eligible is False


def test_labelled_part_number_still_links_the_exact_external_product() -> None:
    agent_input = _agent_input()
    agent_input["vehicle_identity"] = {}
    agent_input["item"] = {
        "id": "MLB-MPN",
        "title": "Componente eletrico Acme",
        "seller_sku": "LOCAL1234",
        "attributes": [
            {"id": "BRAND", "name": "Marca", "value_name": "Acme"},
            {"id": "PART_NUMBER", "name": "Part number", "value_name": "MPN-1234"},
        ],
    }
    agent_input["product_evidence_identity"].update(
        {"sku": "LOCAL1234", "item_id": "MLB-MPN"}
    )

    document = deep_research.make_research_document(
        item={"title": "Datasheet MPN-1234"},
        url="https://www.digikey.com/catalog/mpn-1234",
        query="MPN-1234 datasheet",
        query_type="product_specification_by_code",
        text="Part number: MPN-1234. Voltagem: 12 V.",
        agent_input=agent_input,
    )

    assert document is not None
    assert document.source_type == "technical_distributor"
    assert document.claim_eligible is True
    assert {
        claim.field_name for claim in extract_technical_claims(document.text)
    } >= {"reference.part_number", "electrical.voltage"}


def test_claim_eligible_copy_replaces_higher_authority_source_only_lead() -> None:
    agent_input = _agent_input()
    agent_input["vehicle_identity"] = {}
    agent_input["item"]["title"] = "Modulo Peugeot BSM-L04"
    body = "Modulo Peugeot BSM-L04. Marca: Peugeot. Voltagem: 12 V."
    entries = [
        (
            {"title": "Catalogo oficial Peugeot"},
            "https://service.peugeot.fr/catalog/bsm-l04",
            "BSM-L04 ficha tecnica",
            "product_specification_by_code",
        ),
        (
            {"title": "Ficha tecnica BSM-L04"},
            "https://www.digikey.com/catalog/bsm-l04",
            "BSM-L04 ficha tecnica",
            "product_specification_by_code",
        ),
    ]
    readings = {
        canonical_research_url(url): body for _item, url, _query, _kind in entries
    }
    documents = []
    hashes = set()
    signatures = set()

    new_facts = deep_research_crawler._append_documents(
        entries, readings, agent_input, documents, hashes, signatures,
    )

    assert len(documents) == 1
    assert documents[0].source_type == "technical_distributor"
    assert documents[0].claim_eligible is True
    assert new_facts >= 1

    official_lead = deep_research.make_research_document(
        item=entries[0][0], url=entries[0][1], query=entries[0][2],
        query_type=entries[0][3], text=body, agent_input=agent_input,
    )
    assert official_lead is not None and official_lead.claim_eligible is False
    deduplicated = deep_research_fingerprints._deduplicate_research_documents(
        [official_lead, documents[0]],
    )
    assert deduplicated == [documents[0]]

    exact_body = "Part number: 9661682980. Marca: Peugeot. Voltagem: 12 V."
    reversed_entries = [entries[1], entries[0]]
    exact_readings = {
        canonical_research_url(url): exact_body
        for _item, url, _query, _kind in reversed_entries
    }
    official_documents = []
    new_facts = deep_research_crawler._append_documents(
        reversed_entries, exact_readings, agent_input,
        official_documents, set(), set(),
    )
    assert len(official_documents) == 1
    assert official_documents[0].claim_eligible is True
    assert official_documents[0].source_type == "official_manufacturer"
    assert new_facts >= 1


def test_research_document_preserves_line_boundaries_before_claim_extraction() -> None:
    agent_input = _agent_input()
    document = deep_research.make_research_document(
        item={"title": "Catalogo Peugeot 9661682980"},
        url="https://service.peugeot.fr/catalog/9661682980",
        query="Peugeot 9661682980 ficha tecnica",
        query_type="product_specification_by_code",
        text=(
            "Codigo OEM: 9661682980\nMarca: Peugeot\n"
            "Peso do produto: 800 g\nVoltagem: 12 V"
        ),
        agent_input=agent_input,
    )

    assert document is not None
    assert "\n" in document.text
    brands = [
        claim.value
        for claim in extract_technical_claims(document.text)
        if claim.field_name == "product.brand"
    ]
    assert brands == ["Peugeot"]


def test_multi_product_official_page_keeps_only_exact_product_section() -> None:
    agent_input = _agent_input()
    document = deep_research.make_research_document(
        item={"title": "Catalogo tecnico Peugeot"},
        url="https://service.peugeot.fr/catalog/bsm",
        query="Peugeot 9661682980 ficha tecnica",
        query_type="product_specification_by_code",
        text=(
            "# Catalogo BSM\n"
            "## Caixa BSM 9661682980\nVoltagem: 12 V\nPotencia: 60 W\n"
            "## Caixa BSM 9675877980\nVoltagem: 24 V\nPotencia: 90 W"
        ),
        agent_input=agent_input,
    )

    assert document is not None
    values = {
        (claim.field_name, claim.value)
        for claim in extract_technical_claims(document.text)
    }
    assert ("electrical.voltage", "12") in values
    assert ("electrical.power", "60") in values
    assert ("electrical.voltage", "24") not in values
    assert "9675877980" not in document.text


def test_inline_multi_product_paragraph_fails_closed_instead_of_mixing_codes() -> None:
    document = deep_research.make_research_document(
        item={"title": "Catalogo tecnico Peugeot"},
        url="https://service.peugeot.fr/catalog/bsm-inline",
        query="Peugeot 9661682980 ficha tecnica",
        query_type="product_specification_by_code",
        text=(
            "Produto 9661682980: Voltagem 12 V e potencia 60 W. "
            "Produto 9675877980: Voltagem 24 V e potencia 90 W."
        ),
        agent_input=_agent_input(),
    )

    assert document is not None
    assert document.claim_eligible is False


def test_target_interface_claim_resets_the_new_fact_counter() -> None:
    agent_input = _agent_input(question="Serve na Shimano FC-MT510-1?")
    agent_input["vehicle_identity"] = {}
    agent_input["intent"] = {
        "compatibilidade": {"target_item": "Shimano FC-MT510-1", "aplicavel": True},
    }
    entry = (
        {"title": "Shimano FC-MT510-1 technical data"},
        "https://www.bike-components.de/en/Shimano/FC-MT510-1-technical-data/",
        "Shimano FC-MT510-1 fixacao especificacoes",
        "target_interface_technical",
    )
    readings = {
        canonical_research_url(entry[1]): "Shimano FC-MT510-1 usa fixacao ASSIMETRICA.",
    }
    signatures: set[tuple[str, str, str, str]] = set()

    new_facts = deep_research_crawler._append_documents(
        [entry], readings, agent_input, [], set(), signatures,
    )

    assert new_facts == 1
    assert any(
        signature[1:] == ("interface.symmetry", "asymmetric", "")
        for signature in signatures
    )


def test_ineligible_blog_target_claim_does_not_reset_the_new_fact_counter() -> None:
    agent_input = _agent_input(question="Serve na Shimano FC-MT510-1?")
    agent_input["vehicle_identity"] = {}
    agent_input["intent"] = {
        "compatibilidade": {"target_item": "Shimano FC-MT510-1", "aplicavel": True},
    }
    entry = (
        {"title": "Opinião sobre Shimano FC-MT510-1"},
        "https://random-bike.example/blog/shimano-fc-mt510-1",
        "Shimano FC-MT510-1 fixacao especificacoes",
        "target_interface_technical",
    )
    readings = {
        canonical_research_url(entry[1]): "Shimano FC-MT510-1 usa fixacao ASSIMETRICA.",
    }
    signatures: set[tuple[str, str, str, str]] = set()

    new_facts = deep_research_crawler._append_documents(
        [entry], readings, agent_input, [], set(), signatures,
    )

    assert new_facts == 0
    assert signatures == set()


@pytest.mark.parametrize(
    "competing_code",
    [
        "9675 877 980",
        "AB123",
        "BCD 104",
        "AB 1234",
        "ABCDEF",
        "MODELOX",
        "X-ABC",
    ],
)
def test_inline_multi_product_paragraph_rejects_grouped_or_short_competing_code(
    competing_code: str,
) -> None:
    document = deep_research.make_research_document(
        item={"title": "Catalogo tecnico Peugeot"},
        url="https://service.peugeot.fr/catalog/bsm-inline-ambiguous",
        query="Peugeot 9661682980 ficha tecnica",
        query_type="product_specification_by_code",
        text=(
            "Produto 9661682980: Material plastico. "
            f"Produto {competing_code}: Voltagem 24 V e potencia 90 W."
        ),
        agent_input=_agent_input(),
    )

    assert document is not None
    assert document.claim_eligible is False


def test_rendered_web_context_sanitizes_result_and_preloaded_vins() -> None:
    url = "https://maker.example/manual"
    encoded_vin = "".join(f"%{ord(character):02X}" for character in VIN)
    lines = research_sources._ia_agent_perguntas_renderizar_resultados_web(
        [
            (
                {
                    "title": f"Catalogo do veiculo {VIN} joao@example.com",
                    "snippet": (
                        f"Aplicacao identificada pelo VIN {VIN}; telefone +55 11 98765-4321; "
                        "call +1 202 555 0123; 202-555-0123"
                    ),
                },
                url,
            )
        ],
        "ficha tecnica",
        "product_specification_by_code",
        {
            "tentadas": 0,
            "confirmada": False,
            "disable_live_reads": True,
            "preloaded": {
                canonical_research_url(url): (
                    f"Pagina renderizada para VIN {encoded_vin}; CPF 123.456.789-09"
                )
            },
        },
    )
    rendered = "\n".join(lines)

    assert VIN not in rendered
    assert encoded_vin not in rendered
    assert "joao@example.com" not in rendered
    assert "+55 11 98765-4321" not in rendered
    assert "+1 202 555 0123" not in rendered
    assert "202-555-0123" not in rendered
    assert "123.456.789-09" not in rendered
    assert "[CHASSI_PROTEGIDO]" in rendered
    assert "[EMAIL_PROTEGIDO]" in rendered
    assert "[TELEFONE_PROTEGIDO]" in rendered
    assert "[DOCUMENTO_PROTEGIDO]" in rendered


def test_unheaded_official_catalog_stops_at_next_labelled_product() -> None:
    agent_input = _agent_input()
    document = deep_research.make_research_document(
        item={"title": "Catalogo tecnico Peugeot"},
        url="https://service.peugeot.fr/catalog/bsm-lines",
        query="Peugeot 9661682980 ficha tecnica",
        query_type="product_specification_by_code",
        text=(
            "Codigo OEM: 9661682980\nVoltagem: 12 V\nPotencia: 60 W\n"
            "Codigo OEM: 9675877980\nVoltagem: 24 V\nPotencia: 90 W"
        ),
        agent_input=agent_input,
    )

    assert document is not None
    assert "Voltagem: 12 V" in document.text
    assert "Voltagem: 24 V" not in document.text


def test_official_catalog_index_hit_without_target_section_fails_closed() -> None:
    agent_input = _agent_input()

    document = deep_research.make_research_document(
        item={"title": "Catalogo tecnico Peugeot"},
        url="https://service.peugeot.fr/catalog/bsm",
        query="Peugeot 9661682980 ficha tecnica",
        query_type="product_specification_by_code",
        text=(
            "# Indice\nCodigos: 9661682980, 9675877980\n"
            "## Caixa BSM 9675877980\nVoltagem: 24 V\nPotencia: 90 W"
        ),
        agent_input=agent_input,
    )

    assert document is not None
    assert document.claim_eligible is False


def test_two_structural_blocks_for_same_code_and_variant_are_ambiguous() -> None:
    agent_input = _agent_input()

    document = deep_research.make_research_document(
        item={"title": "Catalogo tecnico Peugeot"},
        url="https://service.peugeot.fr/catalog/bsm",
        query="Peugeot 9661682980 ficha tecnica",
        query_type="product_specification_by_code",
        text=(
            "## Caixa BSM 9661682980 linha A\nVoltagem: 12 V\n"
            "## Caixa BSM 9661682980 linha B\nVoltagem: 24 V"
        ),
        agent_input=agent_input,
    )

    assert document is not None
    assert document.claim_eligible is False


def test_repeated_code_inside_one_product_section_is_not_false_ambiguity() -> None:
    agent_input = _agent_input()
    document = deep_research.make_research_document(
        item={"title": "Catalogo tecnico Peugeot"},
        url="https://service.peugeot.fr/catalog/bsm-detail",
        query="Peugeot 9661682980 ficha tecnica",
        query_type="product_specification_by_code",
        text=(
            "## Caixa BSM 9661682980\n"
            "Codigo OEM: 9661682980\nVoltagem: 12 V\nPotencia: 60 W"
        ),
        agent_input=agent_input,
    )

    assert document is not None
    assert {claim.value for claim in extract_technical_claims(document.text)} >= {"12", "60"}


def test_official_catalog_table_materializes_only_the_exact_product_row() -> None:
    agent_input = _agent_input()
    document = deep_research.make_research_document(
        item={"title": "Tabela tecnica Peugeot"},
        url="https://service.peugeot.fr/catalog/bsm-table",
        query="Peugeot 9661682980 ficha tecnica",
        query_type="product_specification_by_code",
        text=(
            "| Codigo | Voltagem | Potencia |\n"
            "| --- | --- | --- |\n"
            "| 9661682980 | 12 V | 60 W |\n"
            "| 9675877980 | 24 V | 90 W |"
        ),
        agent_input=agent_input,
    )

    assert document is not None
    assert "Voltagem: 12 V" in document.text
    assert "Voltagem: 24 V" not in document.text
    assert {claim.value for claim in extract_technical_claims(document.text)} >= {"12", "60"}


def test_selected_variation_requires_the_complete_selector_not_shared_number() -> None:
    agent_input = _agent_input()
    agent_input["product_evidence_identity"]["variation_id"] = "V12"
    agent_input["item"]["variations"] = [
        {
            "id": "V12",
            "attribute_combinations": [{"name": "Voltagem", "value_name": "12 V"}],
        }
    ]
    document = deep_research.make_research_document(
        item={"title": "Catalogo tecnico Peugeot"},
        url="https://service.peugeot.fr/catalog/bsm-variants",
        query="Peugeot 9661682980 12 V ficha tecnica",
        query_type="product_specification_by_code",
        text=(
            "## Caixa BSM 9661682980 12 A\nCorrente: 12 A\nPotencia: 90 W\n"
            "## Caixa BSM 9661682980 12 V\nVoltagem: 12 V\nPotencia: 60 W"
        ),
        agent_input=agent_input,
    )

    assert document is not None
    assert "Corrente: 12 A" not in document.text
    assert "Voltagem: 12 V" in document.text


def test_selected_variation_scopes_nested_sections_under_shared_product_code() -> None:
    agent_input = _agent_input()
    agent_input["product_evidence_identity"]["variation_id"] = "V12"
    agent_input["item"]["variations"] = [
        {
            "id": "V12",
            "attribute_combinations": [{"name": "Voltagem", "value_name": "12 V"}],
        }
    ]
    document = deep_research.make_research_document(
        item={"title": "Catalogo tecnico Peugeot"},
        url="https://service.peugeot.fr/catalog/bsm-nested-variants",
        query="Peugeot 9661682980 12 V ficha tecnica",
        query_type="product_specification_by_code",
        text=(
            "# Caixa BSM 9661682980\n"
            "## Variacao 12 V\nVoltagem: 12 V\nPotencia: 60 W\n"
            "## Variacao 24 V\nVoltagem: 24 V\nPotencia: 90 W"
        ),
        agent_input=agent_input,
    )

    assert document is not None
    assert "Voltagem: 12 V" in document.text
    assert "Voltagem: 24 V" not in document.text


def test_claim_extraction_separates_semantics_and_rejects_bsm_label_metadata() -> None:
    claims = extract_technical_claims(
        "Entrada: 12 V; Saida: 5 V; potencia: 60 W; peso do produto: 800 g; "
        "peso da embalagem: 1,2 kg; certificacoes CE e INMETRO; "
        "Codigo OEM: 9661682980; Part number: OPER-459; "
        "Part 63Q Level 02 Oper 459 Serial 1234567."
    )
    values = {(claim.field_name, claim.scope, claim.value, claim.unit) for claim in claims}

    assert ("electrical.input_voltage", "product", "12", "V") in values
    assert ("electrical.output_voltage", "product", "5", "V") in values
    assert ("weight.product", "product", "800", "g") in values
    assert ("weight.package", "package", "1,2", "kg") in values
    assert any(field == "certification.ce" for field, *_rest in values)
    assert any(field == "certification.inmetro" for field, *_rest in values)
    assert any(field.startswith("reference.oem_code.9661682980") for field, *_rest in values)
    assert not any("oper_459" in field and field.startswith("reference.oem_code") for field, *_rest in values)
    assert not any("63q" in field and field.startswith("reference.oem_code") for field, *_rest in values)


def test_claim_extraction_covers_interfaces_kit_performance_and_installation() -> None:
    claims = extract_technical_claims(
        "Conector: 6 pinos. Rosca: M12 x 1,5. Fixacao: 4 furos. "
        "Kit contem bomba, chicote e suportes. Vazao: 800 L/h. Pressao: 3 bar. "
        "Requer rele de 30 A. Referencia cruzada: ABC-1234."
    )
    fields = {claim.field_name for claim in claims}

    assert "interface.connector_pins" in fields
    assert "interface.thread" in fields
    assert "interface.fixation_holes" in fields
    assert {"kit.content.bomba", "kit.content.chicote", "kit.content.suportes"} <= fields
    assert "performance.flow_rate" in fields
    assert "performance.pressure" in fields
    assert any(field.startswith("installation.requirement.") for field in fields)
    assert "reference.cross_reference.abc_1234" in fields


def test_current_listing_keeps_condition_as_short_lived_candidate_fact() -> None:
    agent_input = _agent_input()
    agent_input["item"].update(
        {
            "official_current_listing": True,
            "permalink": "https://produto.mercadolivre.com.br/MLB-100",
            "condition": "new",
        }
    )

    document = deep_research.listing_document(agent_input)

    assert document is not None
    condition_claims = [
        claim
        for claim in extract_technical_claims(document.text)
        if claim.field_name == "product.condition"
    ]
    assert [claim.value for claim in condition_claims] == ["new"]
    assert document.source_type == "official_listing"


def test_current_listing_projects_only_the_exact_selected_variation() -> None:
    agent_input = _agent_input()
    agent_input["product_evidence_identity"].update(
        {"sku": "BSM-12", "variation_id": "V12"}
    )
    agent_input["item"].update(
        {
            "official_current_listing": True,
            "permalink": "https://produto.mercadolivre.com.br/MLB-100",
            "title": "Componente Acme 12 V ou 24 V",
            "description": "Escolha 12 V com 60 W ou 24 V com 90 W.",
            "attributes": [
                {"id": "BRAND", "name": "Marca", "value_name": "Acme"},
                {
                    "id": "VOLTAGE",
                    "name": "Voltagem",
                    "value_name": "12 V / 24 V",
                },
                {
                    "id": "POWER",
                    "name": "Potencia",
                    "value_name": "60 W / 90 W",
                },
            ],
            "variations": [
                {
                    "id": "V12",
                    "seller_sku": "BSM-12",
                    "attribute_combinations": [
                        {"id": "VOLTAGE", "name": "Voltagem", "value_name": "12 V"}
                    ],
                    "attributes": [
                        {"id": "POWER", "name": "Potencia", "value_name": "60 W"}
                    ],
                },
                {
                    "id": "V24",
                    "seller_sku": "BSM-24",
                    "attribute_combinations": [
                        {"id": "VOLTAGE", "name": "Voltagem", "value_name": "24 V"}
                    ],
                    "attributes": [
                        {"id": "POWER", "name": "Potencia", "value_name": "90 W"}
                    ],
                },
            ],
        }
    )

    document = deep_research.listing_document(agent_input)

    assert document is not None
    assert "12 V" in document.text
    assert "60 W" in document.text
    assert "24 V" not in document.text
    assert "90 W" not in document.text
    claims = {
        (claim.field_name, claim.value, claim.unit)
        for claim in extract_technical_claims(document.text)
    }
    assert ("electrical.voltage", "12", "V") in claims
    assert ("electrical.voltage", "24", "V") not in claims
    assert ("electrical.power", "60", "W") in claims
    assert ("electrical.power", "90", "W") not in claims


def test_current_listing_rejects_multivariation_without_selection() -> None:
    agent_input = _agent_input()
    agent_input["item"].update(
        {
            "official_current_listing": True,
            "permalink": "https://produto.mercadolivre.com.br/MLB-100",
            "title": "Componente Acme 12 V ou 24 V",
            "variations": [
                {"id": "V12", "seller_sku": "BSM-12"},
                {"id": "V24", "seller_sku": "BSM-24"},
            ],
        }
    )

    assert deep_research.listing_document(agent_input) is None


@pytest.mark.parametrize(
    "text",
    [
        "Codigo OEM: UNKNOWN",
        "OEM code: UNAVAILABLE",
        "Codigo original: SEM-CODIGO",
        "Part number: NOT-AVAILABLE",
    ],
)
def test_missing_value_sentinels_never_become_product_codes(text: str) -> None:
    assert not any(
        claim.field_name.startswith("reference.")
        for claim in extract_technical_claims(text)
    )


def test_brand_and_manufacturer_stop_before_the_next_sentence_or_label() -> None:
    claims = extract_technical_claims(
        "Marca: Acme. Peso do produto: 800 g. "
        "Fabricante: Bosch. Voltagem: 12 V. "
        "Brand: ACME - Voltage: 12 V"
    )
    values = [
        claim.value
        for claim in claims
        if claim.field_name in {"product.brand", "product.manufacturer"}
    ]

    assert values == ["Acme", "Bosch"]


def test_brand_and_manufacturer_stop_before_slash_delimited_label() -> None:
    claims = extract_technical_claims(
        "Marca: Acme / Fabricante: Bosch\nFabricante: Bosch / Marca: Acme"
    )
    values = {
        (claim.field_name, claim.value)
        for claim in claims
        if claim.field_name in {"product.brand", "product.manufacturer"}
    }

    assert values == {
        ("product.brand", "Acme"),
        ("product.manufacturer", "Bosch"),
    }


def test_expected_fields_keeps_input_output_and_diameter_distinct() -> None:
    assert expected_fields_from_question(
        "Qual a voltagem de entrada e de saida e o diametro?"
    ) == {
        "electrical.input_voltage",
        "electrical.output_voltage",
        "dimensions.diameter",
    }


def test_local_seller_sku_is_never_promoted_to_oem_compatibility_link() -> None:
    agent_input = _agent_input(question="Serve no Peugeot 206 2012?")
    agent_input["item"] = {
        "id": "MLB100",
        "title": "Caixa BSM Peugeot",
        "seller_sku": "LOCAL1234",
        "attributes": [{"id": "BRAND", "value_name": "Peugeot"}],
    }
    document = _document(
        "local-sku",
        "Codigo OEM: LOCAL1234. Compativel com Peugeot 206 2012.",
    )

    assert extract_guarded_commercial_claims(document, agent_input) == []


def test_complete_vehicle_oem_product_link_is_required_for_fitment_claim() -> None:
    agent_input = _agent_input(question="Serve no Peugeot 206 2012?")
    agent_input["item"]["attributes"].append(
        {"id": "OEM_CODE", "name": "Codigo OEM", "value_name": "9661682980"}
    )
    document = _document(
        "fitment",
        "Codigo OEM: 9661682980. Compativel com Peugeot 206 2012.",
    )

    claims = extract_guarded_commercial_claims(document, agent_input)

    assert [claim.field_name for claim in claims] == [
        "compatibility.vehicle_oem_product_link"
    ]


@pytest.mark.parametrize("status", ["partial", "confirmed"])
def test_fitment_requires_confirmed_identity_and_all_decoded_configuration(
    status: str,
) -> None:
    agent_input = _agent_input(question="Serve no Peugeot 206 2012 1.6 TU5JP4 GTI?")
    agent_input["item"]["attributes"].append(
        {"id": "OEM_CODE", "name": "Codigo OEM", "value_name": "9661682980"}
    )
    agent_input["vehicle_identity"].update(
        {
            "status": status,
            "series": "GTI",
            "engine_model": "TU5JP4",
            "engine_displacement_l": "1.6",
        }
    )
    incomplete = _document(
        f"fitment-{status}",
        "Codigo OEM: 9661682980. Compativel com Peugeot 206 2012.",
    )
    complete = replace(
        incomplete,
        text=(
            "Codigo OEM: 9661682980. Compativel com Peugeot 206 2012, "
            "serie GTI, motor TU5JP4 1.6."
        ),
    )

    assert extract_guarded_commercial_claims(incomplete, agent_input) == []
    expected_count = 1 if status == "confirmed" else 0
    assert len(extract_guarded_commercial_claims(complete, agent_input)) == expected_count


def test_near_homonym_technical_product_with_conflicting_power_is_rejected() -> None:
    agent_input = _agent_input()
    agent_input["vehicle_identity"] = {}
    agent_input["item"] = {
        "id": "MLB-PUMP",
        "title": "Mini Bomba Dagua 22w 24v Solar Bateria",
        "attributes": [{"id": "BRAND", "name": "Marca", "value_name": "Acme"}],
    }
    wrong_product = (
        "Mini Bomba Dagua Solar Bateria 24v. Potencia: 50 W. "
        "Marca: Acme."
    )

    assert document_matches_product(
        wrong_product,
        agent_input,
        source_type="technical_independent",
    ) is False


def test_shared_voltage_does_not_make_an_unrelated_product_the_same_identity() -> None:
    agent_input = _agent_input()
    agent_input["vehicle_identity"] = {}
    agent_input["item"] = {
        "id": "MLB-SENSOR",
        "title": "Sensor ABS Dianteiro Ford Focus 12V",
        "attributes": [{"id": "BRAND", "name": "Marca", "value_name": "Ford"}],
    }

    assert document_matches_product(
        "Lampada LED Universal 12V. Marca: Outra.",
        agent_input,
        source_type="technical_independent",
    ) is False


def test_deep_reader_keeps_full_transient_page_for_all_specifications() -> None:
    page = (
        "# Caixa BSM 9661682980\n"
        "Peso do produto: 800 g\nMaterial: aluminio\nPotencia: 60 W\nINMETRO"
    )

    class Response:
        status_code = 200
        encoding = "utf-8"
        closed = False

        @staticmethod
        def iter_content(*, chunk_size: int, decode_unicode: bool):
            assert chunk_size == 16 * 1024
            assert decode_unicode is False
            yield page.encode("utf-8")

        @staticmethod
        def raise_for_status() -> None:
            return None

        @classmethod
        def close(cls) -> None:
            cls.closed = True

    with patch.object(research_sources.requests, "get", return_value=Response()), patch.object(
        research_sources,
        "_perguntas_ia_v2_host_resolve_somente_publico",
        return_value=True,
    ):
        deep_page = research_sources._perguntas_ia_v2_ler_fonte_tecnica(
            "https://service.peugeot.fr/catalog/9661682980",
            "BSM 9661682980 ficha tecnica",
            deep=True,
        )

    assert deep_page == page
    assert Response.closed is True
    fields = {claim.field_name for claim in extract_technical_claims(deep_page)}
    assert {"weight.product", "construction.material", "electrical.power"} <= fields


def test_deep_reader_stops_at_decompressed_byte_cap_and_closes_immediately() -> None:
    class Response:
        status_code = 200
        encoding = "utf-8"

        def __init__(self) -> None:
            self.closed = False
            self.chunks_requested = 0

        def iter_content(self, *, chunk_size: int, decode_unicode: bool):
            assert chunk_size == 16 * 1024
            assert decode_unicode is False
            for chunk in (b"a" * 400_000, b"b" * 400_000, b"never-read"):
                self.chunks_requested += 1
                yield chunk

        @staticmethod
        def raise_for_status() -> None:
            return None

        def close(self) -> None:
            self.closed = True

    response = Response()
    with patch.object(
        research_sources.requests, "get", return_value=response,
    ) as request_get, patch.object(
        research_sources,
        "_perguntas_ia_v2_host_resolve_somente_publico",
        return_value=True,
    ):
        deep_page = research_sources._perguntas_ia_v2_ler_fonte_tecnica(
            "https://service.peugeot.fr/catalog/9661682980",
            "BSM 9661682980 ficha tecnica",
            deep=True,
        )

    assert len(deep_page.encode("utf-8")) == 600_000
    assert deep_page.endswith("b" * 200_000)
    assert response.chunks_requested == 2
    assert response.closed is True
    assert request_get.call_args.kwargs["stream"] is True


def test_listing_or_conflicting_values_never_claim_apparent_coverage() -> None:
    agent_input = _agent_input()
    listing = _document("listing", "Voltagem: 12 V", source_type="official_listing")
    conflict_a = _document("a", "Voltagem: 12 V")
    conflict_b = _document("b", "Voltagem: 24 V")

    assert apparent_coverage_complete(agent_input, [listing]) is False
    assert apparent_coverage_complete(agent_input, [conflict_a, conflict_b]) is False


def test_applicable_category_fields_prevent_question_only_early_completion() -> None:
    agent_input = _agent_input(question="Qual a potencia?")
    only_power = _document("power", "Codigo OEM: 9661682980. Potencia: 60 W")

    assert apparent_coverage_complete(agent_input, [only_power]) is False


def test_classified_compatibility_intent_prevents_generic_coverage_completion() -> None:
    agent_input = _agent_input(question="Dá na Shimano MT510?")
    agent_input["vehicle_identity"] = {}
    agent_input["intent"] = {
        "categoria": "compatibility",
        "categorias": ["compatibility"],
        "compatibilidade": {
            "aplicavel": True,
            "target_item": "Shimano MT510",
            "target_type": "dimensional",
        },
    }
    complete_generic_dossier = _document(
        "generic-dossier",
        "Marca: Peugeot. Peso do produto: 800 g. Dimensoes: 10 x 20 x 30 mm. "
        "Material: aluminio. Codigo OEM: 9661682980. Voltagem: 12 V. "
        "Potencia: 60 W. Corrente: 5 A.",
    )

    assert "compatibility" in expected_fields_for_research(agent_input)
    assert apparent_coverage_complete(agent_input, [complete_generic_dossier]) is False


def test_pump_category_does_not_finish_without_applicable_dossier_fields() -> None:
    agent_input = _agent_input(question="Qual a vazao desta bomba?")
    agent_input["vehicle_identity"] = {}
    agent_input["item"]["title"] = "Mini Bomba Dagua 22w 24v 800l/h"
    agent_input["item"]["attributes"] = [
        {"id": "BRAND", "name": "Marca", "value_name": "Acme"},
        {
            "id": "PART_NUMBER",
            "name": "Part number",
            "value_name": "BSM-9661682980",
        },
    ]
    partial = _document(
        "pump-partial",
        "Marca: Acme. Peso do produto: 800 g. Dimensoes: 10 x 20 x 30 mm. "
        "Material: aluminio. Voltagem: 24 V. Vazao: 800 L/h. Pressao: 3 bar.",
        source_type="official_manufacturer",
    )

    expected = expected_fields_for_research(agent_input)
    assert {
        "electrical.power",
        "electrical.current",
        "interface.connector",
        "interface.fixation_holes",
        "installation.requirement",
    } <= expected
    assert apparent_coverage_complete(agent_input, [partial]) is False


def test_complete_pump_dossier_can_finish_category_coverage() -> None:
    agent_input = _agent_input(question="Qual a vazao desta bomba?")
    agent_input["vehicle_identity"] = {}
    agent_input["item"]["title"] = "Mini Bomba Dagua 22w 24v 800l/h"
    agent_input["item"]["attributes"] = [
        {"id": "BRAND", "name": "Marca", "value_name": "Acme"},
        {
            "id": "PART_NUMBER",
            "name": "Part number",
            "value_name": "BSM-9661682980",
        },
    ]
    complete = _document(
        "pump-complete",
        "Part number: BSM-9661682980. Marca: Acme. Peso do produto: 800 g. "
        "Dimensoes: 10 x 20 x 30 mm. "
        "Material: aluminio. Voltagem: 24 V. Potencia: 22 W. Corrente: 1 A. "
        "Vazao: 800 L/h. Pressao: 3 bar. Altura manometrica: 5 m. "
        "Diametro: 12 mm. Conector: 2 pinos. Fixacao: 4 furos. "
        "Requer rele de 30 A.",
        source_type="official_manufacturer",
    )

    assert apparent_coverage_complete(agent_input, [complete]) is True


def test_equivalent_copies_count_as_one_technical_origin() -> None:
    agent_input = _generic_component_input()
    facts = (
        "Marca: Acme\nPeso do produto: 800 g\nDimensoes: 10 x 20 x 30 mm\n"
        "Material: aluminio\nVoltagem: 12 V"
    )
    copy_a = replace(
        _document("copy-a", f"Loja A\n{facts}", source_type="technical_independent"),
        origin_key="one.example",
    )
    copy_b = replace(
        _document("copy-b", f"Loja B\n{facts}", source_type="technical_independent"),
        origin_key="two.example",
    )

    assert apparent_coverage_complete(agent_input, [copy_a, copy_b]) is False


def test_equivalent_copies_with_different_editorial_headers_count_once() -> None:
    agent_input = _generic_component_input()
    facts = (
        "Marca: Acme\nPeso do produto: 800 g\nDimensoes: 10 x 20 x 30 mm\n"
        "Material: aluminio\nVoltagem: 12 V"
    )
    copy_a = replace(
        _document(
            "copy-header-a",
            f"Ficha tecnica exclusiva Underhood Service\n{facts}",
            source_type="technical_independent",
        ),
        origin_key="underhoodservice.com",
    )
    copy_b = replace(
        _document(
            "copy-header-b",
            f"Guia completo Repair Pal\n{facts}",
            source_type="technical_independent",
        ),
        origin_key="repairpal.com",
    )

    assert apparent_coverage_complete(agent_input, [copy_a, copy_b]) is False


def test_equivalent_spec_block_with_different_navigation_counts_once() -> None:
    agent_input = _generic_component_input()
    facts = (
        "Marca: Acme\nPeso do produto: 800 g\nDimensoes: 10 x 20 x 30 mm\n"
        "Material: aluminio\nVoltagem: 12 V"
    )
    navigation_a = " ".join(f"menu-a-{index}" for index in range(30))
    navigation_b = " ".join(f"rodape-b-{index}" for index in range(30))
    copy_a = replace(
        _document(
            "copy-nav-a",
            f"{navigation_a}\n{facts}",
            source_type="technical_independent",
        ),
        origin_key="underhoodservice.com",
    )
    copy_b = replace(
        _document(
            "copy-nav-b",
            f"{navigation_b}\n{facts}",
            source_type="technical_independent",
        ),
        origin_key="repairpal.com",
    )

    assert apparent_coverage_complete(agent_input, [copy_a, copy_b]) is False


def test_single_copied_claim_with_different_navigation_counts_once() -> None:
    agent_input = _generic_component_input()
    copy_a = replace(
        _document(
            "copy-one-a",
            "menu catalogo Power pecas suporte\nVoltagem: 12 V\nrodape institucional",
            source_type="technical_independent",
        ),
        origin_key="underhoodservice.com",
    )
    copy_b = replace(
        _document(
            "copy-one-b",
            "produtos Material ajuda contato blog\nVoltagem: 12 V\ntermos e privacidade",
            source_type="technical_independent",
        ),
        origin_key="repairpal.com",
    )

    assert deep_research._technical_content_hash(
        copy_a.text
    ) != deep_research._technical_content_hash(copy_b.text)
    assert deep_research._technical_copy_fingerprint(
        copy_a.text
    ) == deep_research._technical_copy_fingerprint(copy_b.text)
    assert apparent_coverage_complete(agent_input, [copy_a, copy_b]) is False


def test_two_independent_technical_sources_with_same_facts_can_activate() -> None:
    agent_input = _generic_component_input()
    facts = (
        "Marca: Acme\nPeso do produto: 800 g\nDimensoes: 10 x 20 x 30 mm\n"
        "Material: aluminio\nVoltagem: 12 V"
    )
    source_a = replace(
        _document(
            "independent-a",
            facts
            + "\nO circuito de alimentacao foi documentado para operacao continua "
            "em equipamentos compactos desta familia.",
            source_type="technical_independent",
        ),
        origin_key="underhoodservice.com",
    )
    source_b = replace(
        _document(
            "independent-b",
            facts
            + "\nO desempenho eletrico permanece estavel durante uso prolongado "
            "em instalacoes moveis desta linha.",
            source_type="technical_independent",
        ),
        origin_key="repairpal.com",
    )

    assert deep_research._technical_copy_fingerprint(
        source_a.text
    ) != deep_research._technical_copy_fingerprint(source_b.text)
    assert apparent_coverage_complete(agent_input, [source_a, source_b]) is True


def test_listing_copy_never_hides_one_of_two_technical_activation_sources(
    evidence_env: Path,
) -> None:
    agent_input = _agent_input(question="Qual a voltagem?")
    agent_input["vehicle_identity"] = {}
    agent_input["item"].update({
        "official_current_listing": True,
        "permalink": "https://produto.mercadolivre.com.br/MLB-100",
        "attributes": [
            {"id": "BRAND", "name": "Marca", "value_name": "Peugeot"},
            {"id": "PART_NUMBER", "name": "Part number", "value_name": "9661682980"},
            {"id": "VOLTAGE", "name": "Voltagem", "value_name": "12 V"},
        ],
    })
    listing = deep_research.listing_document(agent_input)
    assert listing is not None
    technical_a = replace(
        listing,
        url="https://underhoodservice.com/catalog/9661682980",
        source_type="technical_independent",
        origin_key="underhoodservice.com",
        query_type="product_specification_by_code",
    )
    technical_b = replace(
        _document(
            "independent-listing-b",
            "Part number: 9661682980. Voltagem: 12 V.\n"
            "O circuito de alimentacao foi medido durante operacao continua "
            "em uma bancada tecnica independente para esta referencia.",
            source_type="technical_independent",
        ),
        url="https://repairpal.com/catalog/9661682980",
        origin_key="repairpal.com",
    )

    verified, _metrics = persist_research_documents(
        "tenant-a",
        agent_input,
        [technical_a, technical_b],
        metrics={
            "pages_discovered": 2,
            "pages_attempted": 2,
            "pages_read": 2,
            "duration_ms": 5,
            "stop_reason": "no_new_facts",
        },
    )

    voltage = next(
        fact for fact in verified if fact["field_name"] == "electrical.voltage"
    )
    assert voltage["value"] == "12"
    assert voltage["unit"] == "V"


def test_unknown_seo_pages_never_self_promote_to_technical_authority() -> None:
    item = {"title": "Ficha tecnica completa", "snippet": "manual tecnico datasheet"}

    assert classify_research_source(
        item,
        "https://seo-specs-unknown.example/ficha-tecnica",
        _agent_input(),
    ) == "blog"


def test_one_hop_discovers_only_same_domain_technical_documents() -> None:
    page = (
        "[Manual PDF](/downloads/manual-9661682980.pdf) "
        "[Catalogo externo](https://evil.example/catalog.pdf) "
        "[Contato](/contato)"
    )

    assert research_sources._ia_agent_perguntas_links_tecnicos_mesmo_dominio(
        "https://service.peugeot.fr/products/9661682980",
        page,
    ) == ["https://service.peugeot.fr/downloads/manual-9661682980.pdf"]


def test_one_hop_rejects_unrelated_hosts_with_same_public_suffix() -> None:
    page = "[Manual externo](https://evil.com.au/manual.pdf)"

    assert research_sources._ia_agent_perguntas_links_tecnicos_mesmo_dominio(
        "https://maker.com.au/product",
        page,
    ) == []


def test_discovery_budget_does_not_start_provider_after_deadline() -> None:
    calls: list[str] = []

    def search(query: str, **_kwargs):
        calls.append(query)
        return []

    result = research_sources._ia_agent_perguntas_prefetch_web(
        "tenant-a",
        ["query-a", "query-b"],
        search,
        deadline_monotonic=time.monotonic() - 1,
    )

    assert result == {}
    assert calls == []


def test_blocked_search_restores_slots_and_opens_bounded_callable_circuit() -> None:
    slots = threading.BoundedSemaphore(12)
    worker_slots = threading.BoundedSemaphore(12)
    provider_gate = threading.Event()
    provider_entered = threading.Event()
    provider_calls: list[str] = []

    def blocked_search(query: str, **_kwargs):
        provider_calls.append(query)
        provider_entered.set()
        provider_gate.wait(timeout=5.0)
        return [{"title": "late", "url": "https://maker.example/late"}]

    try:
        with patch.object(research_sources, "_IA_AGENT_PERGUNTAS_WEB_QUERY_SLOTS", slots), patch.object(
            research_sources,
            "_IA_AGENT_PERGUNTAS_WEB_WORKER_SLOTS",
            worker_slots,
        ), patch.object(
            research_sources,
            "_IA_AGENT_PERGUNTAS_WEB_PREFETCH_MAX_SECONDS",
            0.10,
        ):
            started = time.monotonic()
            first = research_sources._ia_agent_perguntas_prefetch_web(
                "tenant-a",
                ["blocked-query"],
                blocked_search,
            )
            elapsed = time.monotonic() - started

            assert provider_entered.is_set()
            assert first == {}
            assert elapsed < 1.0

            acquired = [slots.acquire(blocking=False) for _ in range(12)]
            try:
                assert all(acquired)
                assert not slots.acquire(blocking=False)
            finally:
                for was_acquired in acquired:
                    if was_acquired:
                        slots.release()

            live_worker_capacity = [worker_slots.acquire(blocking=False) for _ in range(12)]
            try:
                assert sum(live_worker_capacity) == 11
            finally:
                for was_acquired in live_worker_capacity:
                    if was_acquired:
                        worker_slots.release()

            retry_started = time.monotonic()
            second = research_sources._ia_agent_perguntas_prefetch_web(
                "tenant-a",
                ["must-not-start"],
                blocked_search,
            )
            assert second == {}
            assert time.monotonic() - retry_started < 0.05
            assert provider_calls == ["blocked-query"]

            provider_gate.set()
            circuit_deadline = time.monotonic() + 1.0
            while (
                research_sources._ia_agent_perguntas_web_search_circuit_open(blocked_search)
                and time.monotonic() < circuit_deadline
            ):
                time.sleep(0.01)
            assert not research_sources._ia_agent_perguntas_web_search_circuit_open(blocked_search)

            recovered_worker_capacity = [worker_slots.acquire(blocking=False) for _ in range(12)]
            try:
                assert all(recovered_worker_capacity)
            finally:
                for was_acquired in recovered_worker_capacity:
                    if was_acquired:
                        worker_slots.release()

            third = research_sources._ia_agent_perguntas_prefetch_web(
                "tenant-a",
                ["recovered-query"],
                blocked_search,
            )
            assert provider_calls == ["blocked-query", "recovered-query"]
            assert third == {
                "recovered-query": [
                    {"title": "late", "url": "https://maker.example/late"},
                ],
            }
    finally:
        provider_gate.set()


def test_prefetch_result_cannot_be_mutated_after_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked = threading.Event()
    release = threading.Event()
    counter_lock = threading.Lock()
    worker_calls = 0
    real_time = deep_research_prefetch.time

    class ControlledTime:
        @staticmethod
        def monotonic() -> float:
            nonlocal worker_calls
            if threading.current_thread().name.startswith("ml-questions-web-"):
                with counter_lock:
                    worker_calls += 1
                    call_number = worker_calls
                if call_number == 2:
                    checked.set()
                    release.wait(timeout=2.0)
                return 0.0
            return 1.0 if checked.is_set() else 0.0

    monkeypatch.setattr(deep_research_prefetch, "time", ControlledTime)
    try:
        result = deep_research_prefetch.prefetch_web(
            "tenant-a",
            ["query"],
            lambda *_args, **_kwargs: [{"url": "https://maker.example/spec"}],
            query_slots=threading.BoundedSemaphore(1),
            worker_slots=threading.BoundedSemaphore(1),
            max_seconds=0.5,
            logger=research_sources.logger,
        )
        assert checked.is_set()
        assert result == {}
    finally:
        monkeypatch.setattr(deep_research_prefetch, "time", real_time)
        release.set()

    real_time.sleep(0.05)
    assert result == {}


def test_search_results_are_ranked_before_the_six_item_limit() -> None:
    query = "produto ficha tecnica"
    low_priority = [
        {
            "title": f"Resultado comum {index}",
            "url": f"https://catalog-{index}.example/produto",
            "snippet": "Pagina geral",
        }
        for index in range(1, 8)
    ]
    official_last = {
        "title": "Manual official do fabricante",
        "url": "https://support.maker.example/manual.pdf",
        "snippet": "Manual tecnico",
    }

    used_query, query_type, items = research_sources._ia_agent_perguntas_itens_web(
        {"type": "product_feature_technical", "query": query},
        {query: [*low_priority, official_last]},
        set(),
    )

    assert used_query == query
    assert query_type == "product_feature_technical"
    assert len(items) == 6
    assert items[0][1] == "https://support.maker.example/manual.pdf"


def test_deep_batch_counts_only_nonempty_bodies_as_pages_read() -> None:
    entries = [
        ({}, "https://one.example/spec", "spec", "technical"),
        ({}, "https://two.example/spec", "spec", "technical"),
        ({}, "https://three.example/spec", "spec", "technical"),
    ]

    def reader(url: str, _query: str, *, diagnostics: dict, **_kwargs) -> str:
        if "one.example" in url:
            diagnostics["retry_success"] = True
            return "Voltagem: 12 V"
        if "two.example" in url:
            diagnostics["read_timeout"] = True
        return ""

    readings, metrics, deadline_exhausted = research_sources.read_deep_batch(
        entries,
        deadline_monotonic=time.monotonic() + 5,
        reader=reader,
    )

    assert len(readings) == 1
    assert metrics == {
        "pages_attempted": 3,
        "pages_read": 1,
        "read_failures": 2,
        "read_timeouts": 1,
        "retry_successes": 1,
    }
    assert deadline_exhausted is False


def test_failed_batches_do_not_trigger_no_new_facts_before_later_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries = [
        {"type": "product_feature_technical", "query": f"query-{index}"}
        for index in range(1, 4)
    ]
    urls = {
        query["query"]: f"https://source-{index}.example/spec"
        for index, query in enumerate(queries, start=1)
    }

    class Session:
        deadline_monotonic = time.monotonic() + 10
        pages_used = 0
        queries_used = queries

        def remaining_seconds(self) -> float:
            return max(0.0, self.deadline_monotonic - time.monotonic())

        def reserve_page(self, _url: str) -> bool:
            self.pages_used += 1
            return True

    session = Session()
    metrics_empty = {
        "pages_attempted": 0,
        "pages_read": 0,
        "read_failures": 0,
        "read_timeouts": 0,
        "retry_successes": 0,
    }
    read_urls: list[str] = []

    def read_batch(entries, **_kwargs):
        if not entries:
            return {}, dict(metrics_empty), False
        _item, url, _query, _query_type = entries[0]
        read_urls.append(url)
        if "source-3" not in url:
            return {}, dict(metrics_empty, pages_attempted=1, read_failures=1), False
        key = canonical_research_url(url)
        return {
            key: "Part number: 9661682980\nVoltagem: 12 V"
        }, dict(metrics_empty, pages_attempted=1, pages_read=1), False

    callbacks = deep_research_crawler.DeepResearchCallbacks(
        reserve_queries=lambda *_args: (
            session,
            queries,
            {query["query"]: [query["query"]] for query in queries},
        ),
        prefetch_web=lambda *_args, **_kwargs: {
            query["query"]: [{"url": urls[query["query"]]}] for query in queries
        },
        select_items=lambda query, _prefetch, _seen: (
            query["query"],
            query["type"],
            [({"title": "Technical page"}, urls[query["query"]])],
        ),
        read_batch=read_batch,
        discover_links=lambda *_args: [],
        render_results=lambda *_args: [],
        render_listings=lambda *_args: [],
    )
    captured: dict = {}

    def persist(_client_id, _agent_input, documents, *, metrics):
        captured["documents"] = list(documents)
        captured["metrics"] = dict(metrics)
        return [], dict(metrics, repository_status="completed")

    monkeypatch.setattr(deep_research_crawler, "persist_research_documents", persist)

    result = deep_research_crawler.collect_deep_research_context(
        "tenant-a",
        queries,
        _agent_input(),
        phase="question",
        search=lambda *_args, **_kwargs: [],
        callbacks=callbacks,
    )

    assert read_urls == list(urls.values())
    assert captured["metrics"]["pages_attempted"] == 3
    assert captured["metrics"]["pages_read"] == 1
    assert captured["metrics"]["read_failures"] == 2
    assert any(document.claim_eligible for document in captured["documents"])
    assert result["research_metrics"]["repository_status"] == "completed"


def test_no_new_facts_waits_for_pending_interface_equivalence_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries = [
        {"type": "product_feature_technical", "query": "product-background"},
        {"type": "target_interface_technical", "query": "target-background"},
        {"type": "interface_equivalence", "query": "product-target-equivalence"},
    ]
    urls = {
        query["query"]: f"https://source-{index}.example/spec"
        for index, query in enumerate(queries, start=1)
    }

    class Session:
        deadline_monotonic = time.monotonic() + 10
        pages_used = 0
        queries_used = queries

        def remaining_seconds(self) -> float:
            return max(0.0, self.deadline_monotonic - time.monotonic())

        def reserve_page(self, _url: str) -> bool:
            self.pages_used += 1
            return True

    session = Session()
    read_urls: list[str] = []
    empty_metrics = {
        "pages_attempted": 0,
        "pages_read": 0,
        "read_failures": 0,
        "read_timeouts": 0,
        "retry_successes": 0,
    }

    def read_batch(entries, **_kwargs):
        if not entries:
            return {}, dict(empty_metrics), False
        _item, url, _query, _query_type = entries[0]
        read_urls.append(url)
        key = canonical_research_url(url)
        body = (
            "Página técnica consultada sem medidas extraíveis."
            if "source-3" not in url
            else "Comparação técnica entre produto e Shimano MT510 disponível para análise."
        )
        return {key: body}, dict(empty_metrics, pages_attempted=1, pages_read=1), False

    callbacks = deep_research_crawler.DeepResearchCallbacks(
        reserve_queries=lambda *_args: (
            session,
            queries,
            {query["query"]: [query["query"]] for query in queries},
        ),
        prefetch_web=lambda *_args, **_kwargs: {
            query["query"]: [{"url": urls[query["query"]]}] for query in queries
        },
        select_items=lambda query, _prefetch, _seen: (
            query["query"],
            query["type"],
            [({"title": "Technical page"}, urls[query["query"]])],
        ),
        read_batch=read_batch,
        discover_links=lambda *_args: [],
        render_results=lambda *_args: [],
        render_listings=lambda *_args: [],
    )
    captured: dict = {}

    def persist(_client_id, _agent_input, documents, *, metrics):
        captured["documents"] = list(documents)
        captured["metrics"] = dict(metrics)
        return [], dict(metrics, repository_status="completed")

    monkeypatch.setattr(deep_research_crawler, "persist_research_documents", persist)
    agent_input = _agent_input(question="Dá na Shimano MT510?")
    agent_input["vehicle_identity"] = {}
    agent_input["intent"] = {
        "categoria": "compatibility",
        "compatibilidade": {
            "aplicavel": True,
            "target_item": "Shimano MT510",
            "target_type": "dimensional",
        },
    }

    deep_research_crawler.collect_deep_research_context(
        "tenant-a",
        queries,
        agent_input,
        phase="question",
        search=lambda *_args, **_kwargs: [],
        callbacks=callbacks,
    )

    assert read_urls == list(urls.values())
    assert captured["metrics"]["pages_read"] == 3


def test_mt510_target_research_keeps_symmetric_and_asymmetric_facts_in_grounding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_input = _agent_input(question="Tem a 36 compatível com Mt 510??")
    agent_input["vehicle_identity"] = {}
    agent_input["item"].update({
        "title": "Coroa Deckas 36D BCD 94 96mm Simetrica",
        "seller_sku": "DECKAS-36",
        "attributes": [
            {"id": "BRAND", "name": "Marca", "value_name": "Deckas"},
            {
                "id": "PART_NUMBER",
                "name": "Part number",
                "value_name": "DECKAS-36-SYM",
            },
        ],
    })
    agent_input["intent"] = {
        "categoria": "compatibility",
        "compatibilidade": {
            "aplicavel": True,
            "target_item": "MT 510",
            "target_type": "dimensional",
            "compatibility_profile": "interface",
            "technical_focus": "BCD fixacao simetrica assimetrica",
        },
    }
    generated = research_sources._ia_agent_perguntas_queries_web(agent_input, [])
    selected_queries = [
        query for query in generated
        if query["type"] in {"target_interface_official", "product_interface_technical"}
    ]
    target_query = next(
        query for query in selected_queries if query["type"] == "target_interface_official"
    )
    assert "MT 510" in target_query["query"]

    pages = {
        "target_interface_official": [
            (
                "https://si.shimano.com/en/product/component/deore-m5100/FC-MT510-1.html",
                "Shimano FC-MT510-1. PCD/BCD: 96 mm. Fixacao: 4 bracos ASSIMETRICO.",
            ),
            (
                "https://www.bike-components.de/en/Shimano/FC-MT510-1-technical-data/",
                "No componente FC-MT510-1, a geometria de fixacao e assimetrica.",
            ),
        ],
        "product_interface_technical": [(
            "https://catalog.deckas.example/deckas-36-sym",
            "Part number: DECKAS-36-SYM. Dentes: 36. BCD: 94/96 mm. Fixacao: SIMETRICA.",
        )],
    }

    class Session:
        deadline_monotonic = time.monotonic() + 10
        pages_used = 0
        queries_used = selected_queries

        def remaining_seconds(self) -> float:
            return max(0.0, self.deadline_monotonic - time.monotonic())

        def reserve_page(self, _url: str) -> bool:
            self.pages_used += 1
            return True

    session = Session()
    empty_metrics = {
        "pages_attempted": 0, "pages_read": 0, "read_failures": 0,
        "read_timeouts": 0, "retry_successes": 0,
    }

    def prefetch(_client_id, queries, _search, **_kwargs):
        result = {}
        for query in queries:
            kind = next(item["type"] for item in selected_queries if item["query"] == query)
            result[query] = [
                {"url": url, "title": kind} for url, _body in pages[kind]
            ]
        return result

    def select_items(query, prefetched, _seen):
        items = prefetched[query["query"]]
        return query["query"], query["type"], [(item, item["url"]) for item in items]

    def read_batch(entries, **_kwargs):
        if not entries:
            return {}, dict(empty_metrics), False
        bodies = {
            canonical_research_url(url): body
            for values in pages.values()
            for url, body in values
        }
        readings = {canonical_research_url(url): bodies[canonical_research_url(url)] for _item, url, _query, _query_type in entries}
        count = len(readings)
        return readings, dict(
            empty_metrics, pages_attempted=count, pages_read=count,
        ), False

    def render_results(items, _query, _kind, state):
        return [
            state["preloaded"].get(canonical_research_url(url), "")
            for _item, url in items
        ]

    callbacks = deep_research_crawler.DeepResearchCallbacks(
        reserve_queries=lambda *_args: (
            session,
            selected_queries,
            {query["query"]: [query["query"]] for query in selected_queries},
        ),
        prefetch_web=prefetch,
        select_items=select_items,
        read_batch=read_batch,
        discover_links=lambda *_args: [],
        render_results=render_results,
        render_listings=lambda *_args: [],
    )
    captured = {}

    def persist(_client_id, _agent_input, documents, *, metrics):
        captured["documents"] = list(documents)
        return [], dict(metrics, repository_status="completed")

    monkeypatch.setattr(deep_research_crawler, "persist_research_documents", persist)
    result = deep_research_crawler.collect_deep_research_context(
        "tenant-a", selected_queries, agent_input,
        phase="question", search=lambda *_args, **_kwargs: [], callbacks=callbacks,
    )

    assert "Fixacao: SIMETRICA" in result["context"]
    assert "FC-MT510-1" in result["context"]
    assert "96 mm" in result["context"]
    assert "ASSIMETRICO" in result["context"]
    assert len(captured["documents"]) == 3
    assert any(document.claim_eligible for document in captured["documents"])
    assert any(
        not document.claim_eligible and "FC-MT510-1" in document.text
        for document in captured["documents"]
    )
    by_field = {value["field_name"]: value for value in result["verified_target_evidence"]}
    assert by_field["interface.symmetry"]["value"] == "asymmetric"
    assert "interface.bolt_pattern" not in by_field
    assert len(by_field["interface.symmetry"]["sources"]) == 2


def test_target_evidence_requires_official_or_two_independent_sources() -> None:
    agent_input = _agent_input(question="Tem a 36 compatível com Mt 510??")
    agent_input["vehicle_identity"] = {}
    agent_input["intent"] = {
        "compatibilidade": {"target_item": "MT 510", "aplicavel": True},
    }

    def target_document(url: str, title: str, text: str) -> ResearchDocumentV1:
        document = deep_research.make_research_document(
            item={"url": url, "title": title},
            url=url,
            query="MT 510 BCD fixacao manual especificacoes fabricante",
            query_type="target_interface_official",
            text=text,
            agent_input=agent_input,
        )
        assert document is not None
        return document

    documents = [
        target_document(
            "https://si.shimano.com/en/product/component/deore-m5100/FC-MT510-1.html",
            "Shimano FC-MT510-1 technical specifications",
            "# Shimano FC-MT510-1\n## Specifications\nPCD: 96 mm\nFixacao: 4 bracos.",
        ),
        target_document(
            "https://www.bike-components.de/en/Shimano/FC-MT510-1-technical-data/",
            "FC-MT510-1 technical data",
            "# Shimano FC-MT510-1\nMontagem assimetrica para a coroa.",
        ),
        target_document(
            "https://www.bike-discount.de/en/shimano-fc-mt510-1-crankset-specification",
            "Shimano FC-MT510-1 specification",
            "# Shimano FC-MT510-1\nA fixacao da coroa e ASSIMETRICA.",
        ),
        target_document(
            "https://www.bike-components.de/en/Shimano/FC-MT510-1-symmetric-rumor/",
            "FC-MT510-1 forum copy",
            "Shimano FC-MT510-1 teria montagem simetrica segundo uma pagina isolada.",
        ),
    ]

    values = deep_research_crawler._verified_target_evidence(agent_input, documents)
    by_field = {value["field_name"]: value for value in values}

    assert by_field["interface.symmetry"]["value"] == "asymmetric"
    assert "interface.bolt_pattern" not in by_field
    assert "interface.fixation_geometry" not in by_field
    assert {
        source["origin_key"] for source in by_field["interface.symmetry"]["sources"]
    } == {"bike-components.de", "bike-discount.de"}
    assert all(value["scope"] == "target" for value in values)
    assert all("text" not in source and "snippet" not in source for value in values for source in value["sources"])


def test_copied_target_fact_with_different_wrappers_counts_as_one_source() -> None:
    agent_input = _agent_input(question="Tem a 36 compatível com Mt 510??")
    agent_input["vehicle_identity"] = {}
    agent_input["intent"] = {
        "compatibilidade": {"target_item": "Shimano FC-MT510-1", "aplicavel": True},
    }

    fact = "Shimano FC-MT510-1 usa fixacao ASSIMETRICA para a coroa."
    documents = []
    for url, wrapper in (
        (
            "https://www.bike-components.de/en/Shimano/FC-MT510-1-technical-data/",
            "Menu catalogo ajuda\n{fact}\n"
            "Aplicacao tecnica para bicicletas desta linha em uso urbano.\n"
            "Rodape termos de uso",
        ),
        (
            "https://www.bike-discount.de/en/shimano-fc-mt510-1-specification",
            "Navegacao produtos contato\n{fact}\n"
            "Material selecionado para desempenho cotidiano e montagem simplificada.\n"
            "Copyright todos os direitos",
        ),
    ):
        document = deep_research.make_research_document(
            item={"url": url, "title": "Shimano FC-MT510-1 technical data"},
            url=url,
            query="Shimano FC-MT510-1 fixacao especificacoes",
            query_type="target_interface_technical",
            text=wrapper.format(fact=fact),
            agent_input=agent_input,
        )
        assert document is not None
        documents.append(document)

    assert documents[0].origin_key != documents[1].origin_key
    assert documents[0].copy_fingerprint != documents[1].copy_fingerprint
    assert deep_research_crawler._verified_target_evidence(agent_input, documents) == []


def test_copied_target_fact_with_branded_assertion_prefix_counts_as_one_source() -> None:
    agent_input = _agent_input(question="Tem a 36 compatível com Mt 510??")
    agent_input["vehicle_identity"] = {}
    agent_input["intent"] = {
        "compatibilidade": {"target_item": "Shimano FC-MT510-1", "aplicavel": True},
    }
    documents = []
    for url, fact in (
        (
            "https://www.bike-components.de/en/Shimano/FC-MT510-1-technical-data/",
            "Loja Alfa informa: a fixacao e ASSIMETRICA.",
        ),
        (
            "https://www.bike-discount.de/en/shimano-fc-mt510-1-specification",
            "Portal Beta informa: a fixacao e ASSIMETRICA.",
        ),
    ):
        document = deep_research.make_research_document(
            item={"url": url, "title": "Shimano FC-MT510-1 technical data"},
            url=url,
            query="Shimano FC-MT510-1 fixacao especificacoes",
            query_type="target_interface_technical",
            text=f"# Shimano FC-MT510-1\n{fact}",
            agent_input=agent_input,
        )
        assert document is not None
        documents.append(document)

    assert deep_research_crawler._verified_target_evidence(agent_input, documents) == []


@pytest.mark.parametrize(
    ("negative_fact", "positive_fact", "field_name"),
    [
        (
            "Este modelo nao possui fixacao simetrica.",
            "Este modelo possui fixacao simetrica.",
            "interface.symmetry",
        ),
        (
            "Este modelo nunca utiliza fixacao simetrica.",
            "Este modelo utiliza fixacao simetrica.",
            "interface.symmetry",
        ),
        (
            "Este modelo jamais utiliza fixacao simetrica.",
            "Este modelo utiliza fixacao simetrica.",
            "interface.symmetry",
        ),
        (
            "Fixacao simetrica nao suportada.",
            "Fixacao simetrica suportada.",
            "interface.symmetry",
        ),
        ("Fixacao nao simetrica.", "Fixacao simetrica.", "interface.symmetry"),
        ("Fixacao not symmetric.", "Fixacao symmetric.", "interface.symmetry"),
        ("Este modelo nao e BCD 96 mm.", "BCD: 96 mm.", "interface.bolt_pattern"),
        ("Este modelo nao possui 4 furos.", "Este modelo possui 4 furos.", "interface.fixation_geometry"),
        ("Conector: nao USB-C.", "Conector: USB-C.", "interface.connector_type"),
    ],
)
def test_negated_official_target_fact_never_activates_as_positive(
    negative_fact: str, positive_fact: str, field_name: str,
) -> None:
    agent_input = _agent_input(question="Serve na Shimano FC-MT510-1?")
    agent_input["vehicle_identity"] = {}
    agent_input["intent"] = {
        "compatibilidade": {"target_item": "Shimano FC-MT510-1", "aplicavel": True},
    }

    def official_document(fact: str) -> ResearchDocumentV1:
        document = deep_research.make_research_document(
            item={"title": "Shimano FC-MT510-1 specifications"},
            url="https://si.shimano.com/en/product/deore-m5100/FC-MT510-1.html",
            query="Shimano FC-MT510-1 manual especificacoes fabricante",
            query_type="target_interface_official",
            text=f"# Shimano FC-MT510-1\n{fact}",
            agent_input=agent_input,
        )
        assert document is not None
        return document

    negative = deep_research_crawler._verified_target_evidence(
        agent_input, [official_document(negative_fact)],
    )
    positive = deep_research_crawler._verified_target_evidence(
        agent_input, [official_document(positive_fact)],
    )
    assert not any(value["field_name"] == field_name for value in negative)
    assert any(value["field_name"] == field_name for value in positive)


def test_accented_target_brand_uses_the_same_reviewed_official_domains() -> None:
    accented = official_domains_for_target_identity("Citroën C3 2015")
    plain = official_domains_for_target_identity("Citroen C3 2015")

    assert accented == plain
    assert accented == ("citroen.com", "citroen.com.br", "citroen.fr")


@pytest.mark.parametrize(
    ("target_identity", "url", "fact"),
    [
        ("Shimano", "https://si.shimano.com/en/product/spec.html", "Fixacao SIMETRICA."),
        ("Citroen", "https://citroen.com/technical/spec.html", "PCD: 108 mm."),
        ("Honda Civic", "https://honda.com/technical/civic.html", "PCD: 114 mm."),
        ("Honda Civic 2015", "https://honda.com/technical/civic-2015.html", "PCD: 114 mm."),
    ],
)
def test_brand_or_vehicle_model_only_target_never_activates_interface_facts(
    target_identity: str, url: str, fact: str,
) -> None:
    agent_input = _agent_input(question=f"Serve em {target_identity}?")
    agent_input["vehicle_identity"] = {}
    agent_input["intent"] = {
        "compatibilidade": {"target_item": target_identity, "aplicavel": True},
    }
    document = deep_research.make_research_document(
        item={"title": f"{target_identity} technical specifications"},
        url=url,
        query=f"{target_identity} especificacoes fabricante",
        query_type="target_interface_official",
        text=f"# {target_identity}\n{fact}",
        agent_input=agent_input,
    )
    assert document is not None
    assert deep_research_crawler._verified_target_evidence(agent_input, [document]) == []


def test_target_official_authority_is_bound_to_target_brand_not_product_brand() -> None:
    agent_input = _agent_input(question="Serve no Bosch 0 281 002 845?")
    agent_input["item"]["attributes"] = [
        {"id": "BRAND", "name": "Marca", "value_name": "Shimano"},
    ]

    def target_document(target: str, text: str) -> ResearchDocumentV1:
        agent_input["intent"] = {"compatibilidade": {"target_item": target}}
        document = deep_research.make_research_document(
            item={"title": f"Technical page for {target}"},
            url="https://si.shimano.com/en/technical/specification.html",
            query=f"{target} manual especificacoes fabricante",
            query_type="target_interface_official",
            text=text,
            agent_input=agent_input,
        )
        assert document is not None
        return document

    bosch_target = "Bosch 0 281 002 845"
    cross_brand = target_document(
        bosch_target,
        "Bosch 0 281 002 845. Tipo de conector: USB-C.",
    )
    assert cross_brand.source_type == "official_manufacturer"
    assert deep_research_crawler._verified_target_evidence(
        agent_input, [cross_brand],
    ) == []

    shimano_target = "Shimano FC-MT510-1"
    same_brand = target_document(
        shimano_target,
        "# Shimano FC-MT510-1\nPCD: 96 mm\nFixacao: 4 bracos.",
    )
    same_brand_values = deep_research_crawler._verified_target_evidence(
        agent_input, [same_brand],
    )
    same_brand_fields = {value["field_name"]: value for value in same_brand_values}
    assert same_brand_fields["interface.bolt_pattern"]["value"] == "96"
    assert same_brand_fields["interface.bolt_pattern"]["sources"][0]["authority"] == "official_manufacturer"


def test_target_evidence_fails_closed_for_unscoped_or_conflicting_variants() -> None:
    agent_input = _agent_input(question="Serve na Shimano FC-MT510-1?")
    agent_input["intent"] = {
        "compatibilidade": {"target_item": "Shimano FC-MT510-1"},
    }

    def official_document(text: str) -> ResearchDocumentV1:
        document = deep_research.make_research_document(
            item={"title": "Shimano FC-MT510-1 specifications"},
            url="https://si.shimano.com/en/product/deore-m5100/FC-MT510-1.html",
            query="MT 510 manual especificacoes fabricante",
            query_type="target_interface_official",
            text=text,
            agent_input=agent_input,
        )
        assert document is not None
        return document

    title_only = official_document("PCD: 96 mm. Fixacao: 4 bracos assimetrica.")
    mixed_variant = official_document(
        "Shimano FC-MT510-1 usa fixacao assimetrica; "
        "Shimano FC-MT511-1 usa fixacao simetrica."
    )
    conflicting_values = official_document(
        "Shimano FC-MT510-1. PCD: 96 mm.\n"
        "Shimano FC-MT510-1. PCD: 104 mm."
    )
    related_variant = official_document(
        "# Shimano FC-MT510-1\nFixacao assimetrica.\n"
        "# Shimano FC-MT511-1\nFixacao simetrica."
    )

    assert deep_research_crawler._verified_target_evidence(agent_input, [title_only]) == []
    mixed = deep_research_crawler._verified_target_evidence(agent_input, [mixed_variant])
    assert not any(value["field_name"] == "interface.symmetry" for value in mixed)
    conflicting = deep_research_crawler._verified_target_evidence(
        agent_input, [conflicting_values],
    )
    assert not any(value["field_name"] == "interface.bolt_pattern" for value in conflicting)
    related = deep_research_crawler._verified_target_evidence(agent_input, [related_variant])
    symmetry = next(value for value in related if value["field_name"] == "interface.symmetry")
    assert symmetry["value"] == "asymmetric"


def test_deep_batch_never_exceeds_four_readers_per_job() -> None:
    entries = [
        ({}, f"https://page-{index}.example/spec", "spec", "technical")
        for index in range(12)
    ]
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    def reader(_url: str, _query: str, **_kwargs) -> str:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return "Voltagem: 12 V"

    _readings, metrics, deadline_exhausted = research_sources.read_deep_batch(
        entries,
        deadline_monotonic=time.monotonic() + 5,
        reader=reader,
    )

    assert metrics["pages_read"] == 12
    assert 1 <= maximum_active <= 4
    assert deadline_exhausted is False


def test_technical_reader_global_semaphore_never_exceeds_eight_requests() -> None:
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    class Response:
        status_code = 200
        encoding = "utf-8"

        @staticmethod
        def iter_content(**_kwargs):
            yield b"Part number: MPN-1234\nVoltagem: 12 V"

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def close() -> None:
            return None

    def request_get(*_args, **_kwargs):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.04)
        with lock:
            active -= 1
        return Response()

    with patch.object(
        research_sources,
        "fetch_research_response",
        side_effect=request_get,
    ) as mocked_get, patch.object(
        research_sources,
        "_perguntas_ia_v2_url_fonte_tecnica_segura",
        return_value=True,
    ), patch.object(
        research_sources,
        "requests_tls_verify",
        return_value=True,
    ):
        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(
                executor.map(
                    lambda index: research_sources._perguntas_ia_v2_ler_fonte_tecnica(
                        f"https://maker-{index}.example/spec",
                        "MPN-1234",
                        deep=True,
                        deadline_monotonic=time.monotonic() + 5,
                    ),
                    range(12),
                )
            )

    assert mocked_get.call_count == 12
    assert all(result for result in results)
    assert 1 <= maximum_active <= 8


def test_slow_drip_deadline_closes_streams_and_releases_global_semaphore() -> None:
    first_started = threading.Event()
    slow_streams = []
    gate = threading.BoundedSemaphore(1)
    slow_diagnostics = {}

    class SlowBody(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = threading.Event()

        async def __aiter__(self):
            first_started.set()
            while not self.closed.is_set():
                await asyncio.sleep(0.005)
                yield b"small technical chunk\n"

        async def aclose(self) -> None:
            self.closed.set()

    async def handler(request: httpx.Request) -> httpx.Response:
        if "slow.example" in str(request.url):
            stream = SlowBody()
            slow_streams.append(stream)
            return httpx.Response(200, stream=stream)
        return httpx.Response(200, content=b"Part number: MPN-1234\nVoltagem: 12 V")

    transport = httpx.MockTransport(handler)

    with patch.object(
        research_sources, "fetch_research_response", side_effect=_strict_fetcher(transport),
    ) as mocked_fetch, patch.object(
        research_sources,
        "_perguntas_ia_v2_url_fonte_tecnica_segura",
        return_value=True,
    ), patch.object(
        research_sources,
        "requests_tls_verify",
        return_value=True,
    ), patch.object(
        research_sources,
        "_IA_AGENT_PERGUNTAS_WEB_READ_SLOTS",
        gate,
    ):
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as executor:
            slow = executor.submit(
                research_sources._perguntas_ia_v2_ler_fonte_tecnica,
                "https://slow.example/spec",
                "MPN-1234",
                deep=True,
                deadline_monotonic=time.monotonic() + 0.70,
                diagnostics=slow_diagnostics,
            )
            assert first_started.wait(timeout=1)
            quick = executor.submit(
                research_sources._perguntas_ia_v2_ler_fonte_tecnica,
                "https://quick.example/spec",
                "MPN-1234",
                deep=True,
                deadline_monotonic=time.monotonic() + 2,
            )
            expired = slow.result(timeout=2)
            recovered = quick.result(timeout=2)
        elapsed = time.monotonic() - started

    assert expired == ""
    assert elapsed < 1.5
    assert len(slow_streams) == 1
    assert all(stream.closed.is_set() for stream in slow_streams)
    assert slow_diagnostics["read_timeout"] is True
    assert recovered == "Part number: MPN-1234\nVoltagem: 12 V"
    assert mocked_fetch.call_count == 2
    assert gate._value == 1


def test_pending_headers_obey_total_deadline_and_release_global_semaphore() -> None:
    headers_started = threading.Event()
    headers_cancelled = threading.Event()
    gate = threading.BoundedSemaphore(1)
    slow_diagnostics = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if "slow-headers.example" in str(request.url):
            headers_started.set()
            try:
                await asyncio.sleep(10)
            finally:
                headers_cancelled.set()
        return httpx.Response(200, content=b"Part number: MPN-1234\nVoltagem: 12 V")

    transport = httpx.MockTransport(handler)
    with patch.object(
        research_sources, "fetch_research_response", side_effect=_strict_fetcher(transport),
    ) as mocked_fetch, patch.object(
        research_sources,
        "_perguntas_ia_v2_url_fonte_tecnica_segura",
        return_value=True,
    ), patch.object(
        research_sources,
        "requests_tls_verify",
        return_value=True,
    ), patch.object(
        research_sources,
        "_IA_AGENT_PERGUNTAS_WEB_READ_SLOTS",
        gate,
    ):
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as executor:
            slow = executor.submit(
                research_sources._perguntas_ia_v2_ler_fonte_tecnica,
                "https://slow-headers.example/spec",
                "MPN-1234",
                deep=True,
                deadline_monotonic=time.monotonic() + 0.70,
                diagnostics=slow_diagnostics,
            )
            assert headers_started.wait(timeout=1)
            quick = executor.submit(
                research_sources._perguntas_ia_v2_ler_fonte_tecnica,
                "https://quick.example/spec",
                "MPN-1234",
                deep=True,
                deadline_monotonic=time.monotonic() + 2,
            )
            expired = slow.result(timeout=2)
            recovered = quick.result(timeout=2)
        elapsed = time.monotonic() - started

    assert expired == ""
    assert elapsed < 1.5
    assert headers_cancelled.is_set()
    assert slow_diagnostics["read_timeout"] is True
    assert recovered == "Part number: MPN-1234\nVoltagem: 12 V"
    assert mocked_fetch.call_count == 2
    assert gate._value == 1


def test_connection_error_at_total_deadline_is_counted_as_read_timeout() -> None:
    diagnostics = {}
    gate = threading.BoundedSemaphore(1)

    def expire_then_disconnect(*_args, **kwargs):
        deadline = kwargs["deadline_monotonic"]
        time.sleep(max(0.0, deadline - time.monotonic()) + 0.01)
        raise research_sources.requests.exceptions.ConnectionError("deadline close")

    with patch.object(
        research_sources, "fetch_research_response", side_effect=expire_then_disconnect,
    ) as mocked_fetch, patch.object(
        research_sources,
        "_perguntas_ia_v2_url_fonte_tecnica_segura",
        return_value=True,
    ), patch.object(
        research_sources,
        "_IA_AGENT_PERGUNTAS_WEB_READ_SLOTS",
        gate,
    ):
        content = research_sources._perguntas_ia_v2_ler_fonte_tecnica(
            "https://maker.example/spec",
            "MPN-1234",
            deep=True,
            deadline_monotonic=time.monotonic() + 0.70,
            diagnostics=diagnostics,
        )

    assert content == ""
    assert diagnostics["read_timeout"] is True
    assert mocked_fetch.call_count == 1
    assert gate._value == 1


def test_persist_reconciles_real_activation_and_conflict_metrics(evidence_env: Path) -> None:
    agent_input = _agent_input()
    agent_input["vehicle_identity"] = {}
    agent_input["item"].update(
        {
            "title": "Componente tecnico Acme",
            "attributes": [
                {"id": "BRAND", "name": "Marca", "value_name": "Acme"},
                {
                    "id": "PART_NUMBER",
                    "name": "Part number",
                    "value_name": "BSM-9661682980",
                },
            ],
        }
    )
    common = (
        "Marca: Acme; Peso do produto: 800 g; Dimensoes: 10 x 20 x 30 mm; "
        "Material: aluminio;"
    )
    first_verified, first_metrics = persist_research_documents(
        "tenant-a",
        agent_input,
        [_document("12v", f"Part number: BSM-9661682980; {common} Voltagem: 12 V")],
        metrics={
            "pages_discovered": 1,
            "pages_read": 1,
            "duration_ms": 10,
            "stop_reason": "no_new_facts",
        },
    )

    assert any(value["field_name"] == "electrical.voltage" for value in first_verified)
    assert first_metrics["coverage_complete"] is True
    assert first_metrics["fields_missing"] == 0
    assert first_metrics["stop_reason"] == "coverage_complete"

    second_verified, second_metrics = persist_research_documents(
        "tenant-a",
        agent_input,
        [_document("24v", f"Part number: BSM-9661682980; {common} Voltagem: 24 V")],
        metrics={
            "pages_discovered": 1,
            "pages_read": 1,
            "duration_ms": 10,
            "stop_reason": "coverage_complete",
        },
    )

    assert not any(value["field_name"] == "electrical.voltage" for value in second_verified)
    assert second_metrics["coverage_complete"] is False
    assert second_metrics["fields_missing"] == 1
    assert second_metrics["stop_reason"] != "coverage_complete"


def test_tenant_or_incomplete_item_identity_is_rejected_before_storage(evidence_env: Path) -> None:
    mismatch = _agent_input()
    mismatch["tenant_id"] = "tenant-b"
    values, metrics = persist_research_documents(
        "tenant-a",
        mismatch,
        [],
        metrics={"stop_reason": "no_new_facts"},
    )
    assert values == []
    assert metrics["repository_status"] == "tenant_mismatch"

    incomplete = _agent_input()
    incomplete["product_evidence_identity"]["item_id"] = ""
    incomplete["item"]["id"] = ""
    values, metrics = persist_research_documents(
        "tenant-a",
        incomplete,
        [],
        metrics={"stop_reason": "no_new_facts"},
    )
    assert values == []
    assert metrics["repository_status"] == "identity_incomplete"


def test_unresolved_multivariation_is_rejected_before_evidence_storage(
    evidence_env: Path,
) -> None:
    unresolved = _agent_input()
    unresolved["item"]["variations"] = [
        {"id": "V12", "seller_sku": "BSM-12"},
        {"id": "V24", "seller_sku": "BSM-24"},
    ]
    document = _document(
        "unresolved-variation",
        "Part number: BSM-9661682980; Voltagem: 12 V",
    )

    values, metrics = persist_research_documents(
        "tenant-a",
        unresolved,
        [document],
        metrics={
            "pages_discovered": 1,
            "pages_read": 1,
            "duration_ms": 5,
            "stop_reason": "coverage_complete",
        },
    )

    assert values == []
    assert metrics["repository_status"] == "variation_unresolved"
    assert metrics["coverage_complete"] is False
    assert metrics["stop_reason"] == "no_new_facts"
