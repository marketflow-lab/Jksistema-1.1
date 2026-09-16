from __future__ import annotations

import time
import sys
from threading import BoundedSemaphore, Event
from types import SimpleNamespace

import pytest

from backend.modules.perguntas_pos_venda.ai import marketplace_policy_sources as policy


URL = "https://www.mercadolivre.com.br/ajuda/cancelamento_123"
TEXT = "O comprador pode acompanhar o procedimento no detalhe da compra e consultar as regras aplicaveis ao pedido."


@pytest.mark.parametrize("url", [
    "http://www.mercadolivre.com.br/ajuda/cancelamento",
    "https://www.mercadolivre.com.br.evil.test/ajuda/cancelamento",
    "https://evil.test/ajuda/cancelamento",
    "https://mercadolivre.com.br/ajuda/cancelamento",
    "https://www.mercadolivre.com.br@evil.test/ajuda/cancelamento",
    "https://user:password@www.mercadolivre.com.br/ajuda/cancelamento",
    "https://www.mercadolivre.com.br:444/ajuda/cancelamento",
    "https://www.mercadolivre.com.br/produto/123",
    "https://www.mercadolivre.com.br/compra-garantida-evil",
    "https://www.mercadolivre.com.br/ajuda/../private",
    "https://www.mercadolivre.com.br/ajuda/%2e%2e/private",
    "https://www.mercadolivre.com.br/ajuda/%252e%252e/private",
    "https://www.mercadolivre.com.br/ajuda/%2fprivate",
    "https://www.mercadolivre.com.br/ajuda/%5cprivate",
    "https://www.mercadolivre.com.br/ajuda/%00private",
    "https://www.mercadolivre.com.br/ajuda/%3fredirect=evil",
    "https://www.mercadolivre.com.br/ajuda/white space",
    "https://www.mercadolivre.com.br/ajuda/\nprivate",
    "https://127.0.0.1/ajuda/cancelamento",
    "https://[::1]/ajuda/cancelamento",
    "file:///ajuda/cancelamento",
])
def test_untrusted_search_urls_never_reach_reader(url):
    read_calls = []
    result = policy.collect_official_marketplace_policy(
        ["cancellation"], site_id="MLB", client_id="tenant-a",
        search=lambda *_args, **_kwargs: [{"url": url, "snippet": TEXT}],
        read=lambda *args, **_kwargs: read_calls.append(args) or TEXT,
    )
    assert result["status"] == "unavailable"
    assert result["sources"] == []
    assert read_calls == []


@pytest.mark.parametrize("url", [
    URL,
    "https://www.mercadolivre.com.br/compra-garantida",
    "https://vendedores.mercadolivre.com.br/ajuda/cancelamento",
    "https://vendedores.mercadolivre.com.br/aprender/devolucao",
    "https://vendedores.mercadolivre.com.br/nota/reembolso",
])
def test_allowlisted_official_pages_are_read_with_tracking_removed(url):
    seen = []
    result = policy.collect_official_marketplace_policy(
        ["refund"], site_id="MLB", client_id="tenant-a",
        search=lambda *_args, **_kwargs: [{"link": url + "?session=SECRET#buyer", "title": "Ajuda oficial"}],
        read=lambda current, **_kwargs: seen.append(current) or TEXT,
    )
    assert result["status"] == "available"
    assert seen == [url]
    assert result["sources"][0]["url"] == url
    assert result["sources"][0]["consulted_at"] == result["consulted_at"]
    assert "SECRET" not in str(result)


def test_only_fixed_topic_queries_leave_context_and_document_budget_covers_both_groups():
    queries, reads, deadlines = [], [], []

    def search(query, **kwargs):
        queries.append((query, kwargs))
        group = len(queries)
        return [{"url": f"https://www.mercadolivre.com.br/ajuda/group-{group}-{n}"} for n in range(8)]

    def read(url, *, deadline_monotonic):
        reads.append(url)
        deadlines.append(deadline_monotonic)
        return TEXT

    before = time.monotonic()
    result = policy.collect_official_marketplace_policy(
        ["cancellation", "refund", "return", "platform_procedure", "buyer@example.com", "ORDER-PRIVATE"],
        site_id="MLB", client_id="tenant-a", search=search, read=read, timeout_seconds=999,
    )
    assert result["status"] == "partial"
    assert result["covered_topics"] == ["cancellation", "refund", "return"]
    assert "platform_procedure" not in result["covered_topics"]
    assert len(queries) == 2
    assert len(reads) == len(result["sources"]) == 3
    assert any("group-1" in url for url in reads)
    assert any("group-2" in url for url in reads)
    assert all(kwargs == {"client_id": "tenant-a", "max_results": 6, "fast": True} for _, kwargs in queries)
    assert all("buyer@example.com" not in query and "ORDER-PRIVATE" not in query for query, _ in queries)
    assert len(set(deadlines)) == 1
    assert before < deadlines[0] <= before + 18.1
    assert set(result["topics"]) == {"cancellation", "refund", "return", "platform_procedure"}


@pytest.mark.parametrize("topics", [["refund"], ["refund", "return"]])
def test_failed_reads_also_consume_total_attempt_budget(topics):
    queries, reads = [], []

    def search(query, **_kwargs):
        queries.append(query)
        return [{"url": f"https://www.mercadolivre.com.br/ajuda/group-{len(queries)}-{n}"} for n in range(6)]

    def read(url, **_kwargs):
        reads.append(url)
        raise TimeoutError("provider unavailable")

    result = policy.collect_official_marketplace_policy(
        topics, site_id="MLB", client_id="tenant-a", search=search, read=read,
    )
    assert result["status"] == "unavailable"
    assert result["sources"] == []
    assert len(reads) == 3
    if len(topics) == 2:
        assert sum("group-1" in url for url in reads) == 2
        assert sum("group-2" in url for url in reads) == 1


@pytest.mark.parametrize("topics", [[], ["buyer@example.com"], "refund", None])
def test_empty_or_invalid_topic_selection_never_starts_transport(topics):
    def forbidden(*_args, **_kwargs):
        pytest.fail("unsupported topic input must not start transport")

    result = policy.collect_official_marketplace_policy(topics, site_id="MLB", client_id="tenant-a", search=forbidden, read=forbidden)
    assert result["status"] == "unavailable"
    assert result["reason"] == "no_supported_topics"
    assert result["topics"] == []


@pytest.mark.parametrize("site", ["", "MLA", "MLM", None])
def test_site_must_be_explicitly_confirmed_before_any_transport(site):
    def forbidden(*_args, **_kwargs):
        pytest.fail("unconfirmed or unsupported site must not start transport")

    result = policy.collect_official_marketplace_policy(["refund"], site_id=site, client_id="tenant-a", search=forbidden, read=forbidden)
    assert result["status"] == "unsupported_site"
    assert result["sources"] == []
    assert result["covered_topics"] == []


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), "invalid"])
def test_invalid_or_exhausted_budget_never_starts_search(timeout):
    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid budget must not start transport")

    result = policy.collect_official_marketplace_policy(["refund"], site_id="MLB", client_id="tenant-a", search=forbidden, timeout_seconds=timeout)
    assert result["status"] == "unavailable"
    assert result["reason"] == "timeout"


@pytest.mark.parametrize("read_result", [
    "", "texto curto", {}, {"text": TEXT, "status_code": 403},
    {"text": TEXT, "final_url": "https://evil.test/ajuda/cancelamento"},
    {"text": "Access denied " + TEXT},
    {"text": "Verifique que você é humano " + TEXT},
    {"text": "x" * (policy._MAX_BYTES + 1)},
])
def test_search_snippets_are_not_used_when_document_read_is_invalid(read_result):
    result = policy.collect_official_marketplace_policy(
        ["refund"], site_id="MLB", client_id="tenant-a",
        search=lambda *_args, **_kwargs: [{"url": URL, "snippet": "SEARCH-ONLY " + TEXT}],
        read=lambda *_args, **_kwargs: read_result,
    )
    assert result["status"] == "unavailable"
    assert result["sources"] == []
    assert "SEARCH-ONLY" not in str(result)


def test_visible_official_document_is_sanitized_and_duplicate_urls_read_once():
    calls = []
    html = (
        "<html><head><title>Ajuda &amp; Reembolso</title><style>CSS-SECRET</style></head><body>"
        "<script>SCRIPT-SECRET</script><template>TEMPLATE-SECRET</template>"
        "<noscript>NOSCRIPT-SECRET</noscript><svg>SVG-SECRET</svg>"
        f"<p>{TEXT}</p><p>Contato privado buyer@example.com</p></body></html>"
    )

    def read(url, **_kwargs):
        calls.append(url)
        return html

    result = policy.collect_official_marketplace_policy(
        ["cancellation", "return"], site_id="MLB", client_id="tenant-a",
        search=lambda *_args, **_kwargs: [{"url": URL}, {"url": URL + "?source=duplicate"}], read=read,
    )
    assert result["status"] == "available"
    assert calls == [URL]
    document = result["sources"][0]
    assert document["title"] == "Ajuda & Reembolso"
    assert TEXT in document["text"]
    assert "SECRET" not in document["text"]
    assert "buyer@example.com" not in document["text"]


def test_transport_failure_returns_partial_documents_without_exposing_exception():
    queries = []

    def search(query, **_kwargs):
        queries.append(query)
        if len(queries) == 2:
            raise RuntimeError("SECRET-CREDENTIAL-ERROR")
        return [{"url": URL}]

    result = policy.collect_official_marketplace_policy(
        ["refund", "return"], site_id="MLB", client_id="tenant-a", search=search, read=lambda *_args, **_kwargs: TEXT,
    )
    assert result["status"] == "partial"
    assert len(result["sources"]) == 1
    assert "SECRET" not in str(result)


def test_timeout_retains_worker_capacity_until_provider_really_finishes(monkeypatch):
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(policy, "_WORKER_SLOTS", slots)
    entered, release = Event(), Event()

    def blocked():
        entered.set()
        release.wait(timeout=2)
        return "late result"

    try:
        value, reason = policy._bounded_call(blocked, time.monotonic() + 0.04)
        assert entered.is_set()
        assert value is None and reason == "timeout"
        assert not slots.acquire(blocking=False)
        calls = []
        value, reason = policy._bounded_call(lambda: calls.append(True), time.monotonic() + 0.02)
        assert value is None and reason == "capacity_unavailable"
        assert calls == []
    finally:
        release.set()
        assert slots.acquire(timeout=2)
        slots.release()


def test_collector_preserves_prior_source_as_partial_when_read_deadline_expires(monkeypatch):
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(policy, "_WORKER_SLOTS", slots)
    release = Event()
    reads = []

    def read(url, **_kwargs):
        reads.append(url)
        if len(reads) == 2:
            release.wait(timeout=2)
        return TEXT

    try:
        result = policy.collect_official_marketplace_policy(
            ["refund"], site_id="MLB", client_id="tenant-a", timeout_seconds=0.08,
            search=lambda *_args, **_kwargs: [{"url": URL}, {"url": URL + "-second"}], read=read,
        )
        assert result["status"] == "partial"
        assert result["reason"] == "timeout"
        assert len(result["sources"]) == 1
    finally:
        release.set()
        assert slots.acquire(timeout=2)
        slots.release()


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["10.0.0.1"], ["::1"], ["169.254.169.254"], ["8.8.8.8", "10.0.0.1"], ["224.0.0.1"]])
def test_default_reader_rejects_private_or_mixed_dns_before_connecting(monkeypatch, addresses):
    monkeypatch.setattr(policy.socket, "getaddrinfo", lambda *_args, **_kwargs: [(None, None, None, None, (ip, 443)) for ip in addresses])

    def forbidden(*_args, **_kwargs):
        pytest.fail("unsafe DNS result must not open a socket")

    monkeypatch.setattr(policy.socket, "create_connection", forbidden)
    assert policy._read_official_page(URL, deadline_monotonic=time.monotonic() + 1) == {}


def _install_transport(monkeypatch, *, status=200, headers=None, body=b"official document"):
    events = []
    raw_socket = SimpleNamespace(close=lambda: events.append(("raw-close",)))
    tls_socket = SimpleNamespace(settimeout=lambda timeout: events.append(("read-timeout", timeout)))

    class Response:
        def __init__(self):
            self.status = status
            self.offset = 0

        def getheader(self, name, default=None):
            return {"Content-Type": "text/html", **(headers or {})}.get(name, default)

        def read(self, size):
            events.append(("read", size))
            chunk = body[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk

        def close(self):
            events.append(("response-close",))

    class Connection:
        sock = None

        def __init__(self, host, port, *, timeout):
            events.append(("connection", host, port, timeout))

        def request(self, method, path, *, headers):
            events.append(("request", method, path, headers))

        def getresponse(self):
            return Response()

        def close(self):
            events.append(("connection-close",))

    def connect(address, *, timeout):
        events.append(("connect", address, timeout))
        return raw_socket

    def wrap(sock, *, server_hostname):
        assert sock is raw_socket
        events.append(("tls", server_hostname))
        return tls_socket

    monkeypatch.setattr(policy, "_public_addresses", lambda _host: ["8.8.8.8"])
    monkeypatch.setattr(policy.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(policy.socket, "create_connection", connect)
    monkeypatch.setattr(policy.ssl, "create_default_context", lambda: SimpleNamespace(wrap_socket=wrap))
    return events


def test_default_reader_pins_public_address_preserves_tls_host_and_deadline(monkeypatch):
    events = _install_transport(monkeypatch, body=TEXT.encode())
    result = policy._read_official_page(URL + "?secret=1", deadline_monotonic=time.monotonic() + 1)
    assert result == {"url": URL, "text": TEXT}
    connect = next(event for event in events if event[0] == "connect")
    assert connect[1] == ("8.8.8.8", 443)
    assert 0 < connect[2] <= 1
    assert ("tls", "www.mercadolivre.com.br") in events
    request = next(event for event in events if event[0] == "request")
    assert request[1:3] == ("GET", "/ajuda/cancelamento_123")
    assert request[3]["Accept-Encoding"] == "identity"
    assert ("response-close",) in events and ("connection-close",) in events


@pytest.mark.parametrize("status,headers", [
    (302, {"Location": "https://evil.test/private"}),
    (403, {}),
    (200, {"Content-Type": "application/octet-stream"}),
    (200, {"Content-Encoding": "gzip"}),
    (200, {"Content-Length": str(policy._MAX_BYTES + 1)}),
    (200, {"Content-Length": "not-a-number"}),
])
def test_default_reader_rejects_redirect_error_and_unsafe_payload_headers(monkeypatch, status, headers):
    events = _install_transport(monkeypatch, status=status, headers=headers)
    assert policy._read_official_page(URL, deadline_monotonic=time.monotonic() + 1) == {}
    assert not any(event[0] == "read" for event in events)
    assert sum(event[0] == "request" for event in events) == 1
    assert ("response-close",) in events and ("connection-close",) in events


def test_default_reader_enforces_streaming_limit_without_content_length(monkeypatch):
    events = _install_transport(monkeypatch, body=b"x" * (policy._MAX_BYTES + 1))
    assert policy._read_official_page(URL, deadline_monotonic=time.monotonic() + 1) == {}
    assert any(event[0] == "read" for event in events)
    assert ("response-close",) in events and ("connection-close",) in events


def test_expired_reader_deadline_never_opens_socket(monkeypatch):
    events = _install_transport(monkeypatch)
    assert policy._read_official_page(URL, deadline_monotonic=time.monotonic() - 1) == {}
    assert events == []


@pytest.mark.parametrize("topic,required_terms", [
    ("payment_procedure", ("pagamento", "Pix", "boleto", "cartao")),
    ("shipping_procedure", ("entrega", "envio", "rastreamento")),
    ("claim_procedure", ("reclamacao", "mediacao")),
    ("warranty_procedure", ("garantia", "defeito", "cobertura")),
    ("platform_procedure", ("politicas", "procedimentos", "comprador")),
])
def test_specific_procedure_topics_use_targeted_fixed_queries_and_report_coverage(topic, required_terms):
    queries = []

    def search(query, **_kwargs):
        queries.append(query)
        return [{"url": URL}]

    result = policy.collect_official_marketplace_policy(
        [topic, "buyer@example.com"], site_id="MLB", client_id="tenant-a",
        search=search, read=lambda *_args, **_kwargs: TEXT,
    )
    assert result["status"] == "available"
    assert result["topics"] == result["covered_topics"] == [topic]
    assert len(queries) == 1
    assert all(term in queries[0] for term in required_terms)
    assert queries[0].startswith("site:www.mercadolivre.com.br/ajuda ")
    assert "buyer@example.com" not in queries[0]


def test_query_limit_reports_only_two_collected_topic_groups_as_covered():
    queries = []

    def search(query, **_kwargs):
        queries.append(query)
        return [{"url": URL + f"-{len(queries)}"}]

    result = policy.collect_official_marketplace_policy(
        ["payment_procedure", "shipping_procedure", "claim_procedure"],
        site_id="MLB", client_id="tenant-a", search=search, read=lambda *_args, **_kwargs: TEXT,
    )
    assert len(queries) == 2
    assert "pagamento" in queries[0] and "entrega" in queries[1]
    assert all("reclamacao" not in query for query in queries)
    assert result["status"] == "partial"
    assert result["reason"] == "topic_query_budget_exhausted"
    assert result["topics"] == ["payment_procedure", "shipping_procedure", "claim_procedure"]
    assert result["covered_topics"] == ["payment_procedure", "shipping_procedure"]


@pytest.mark.parametrize("first_read_succeeds", [False, True])
def test_coverage_never_includes_topics_with_no_read_document(first_read_succeeds):
    queries = []

    def search(query, **_kwargs):
        queries.append(query)
        return [{"url": URL + f"-{len(queries)}"}]

    def read(url, **_kwargs):
        return TEXT if first_read_succeeds and url.endswith("-1") else ""

    result = policy.collect_official_marketplace_policy(
        ["payment_procedure", "shipping_procedure"], site_id="MLB", client_id="tenant-a", search=search, read=read,
    )
    assert len(queries) == 2
    assert result["status"] == ("partial" if first_read_succeeds else "unavailable")
    assert result["covered_topics"] == (["payment_procedure"] if first_read_succeeds else [])


def test_general_platform_topic_does_not_replace_specific_procedure_search():
    queries = []

    def search(query, **_kwargs):
        queries.append(query)
        return [{"url": URL}]

    result = policy.collect_official_marketplace_policy(
        ["platform_procedure", "payment_procedure"], site_id="MLB", client_id="tenant-a",
        search=search, read=lambda *_args, **_kwargs: TEXT,
    )
    assert len(queries) == 1 and "pagamento" in queries[0]
    assert result["status"] == "partial"
    assert result["covered_topics"] == ["payment_procedure"]


def test_default_search_disables_operational_tenant_cache_without_changing_query(monkeypatch):
    calls = []
    returned = [{"url": URL}]

    def search(query, **kwargs):
        calls.append((query, kwargs))
        return returned

    module_name = "backend.modules.perguntas_pos_venda.ai.sources"
    monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(_ia_agent_perguntas_buscar_web_publica=search))
    query = "site:www.mercadolivre.com.br/ajuda pagamento Pix boleto cartao prazo aprovacao compra"
    result = policy._default_search(query, client_id="tenant-private", max_results=6, fast=True)
    assert result is returned
    assert calls == [(query, {"client_id": "", "max_results": 6, "fast": True})]
