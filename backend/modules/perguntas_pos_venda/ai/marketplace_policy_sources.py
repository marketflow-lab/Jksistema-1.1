"""Bounded, read-only collection of official marketplace policy documents.

Only fixed topic queries leave the process. Product, order and customer context
must never be passed to this collector. Injected search/read callables are trusted
transport adapters; retrieved content remains untrusted reference data.
"""

from __future__ import annotations

import http.client
import ipaddress
import math
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from queue import Empty, Queue
from threading import BoundedSemaphore, Thread
from typing import Any, Callable
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from .deep_research_sanitization import sanitize_public_research_text


_WORKER_SLOTS = BoundedSemaphore(4)
_MAX_BYTES = 512_000
_MAX_TEXT = 16_000
_TOPICS = (
    "cancellation", "refund", "return", "payment_procedure", "shipping_procedure",
    "claim_procedure", "warranty_procedure", "platform_procedure",
)
_GROUPS = (
    (("cancellation", "refund"), "site:www.mercadolivre.com.br/ajuda cancelamento compra reembolso devolucao dinheiro"),
    (("return",), "site:www.mercadolivre.com.br/ajuda devolucao produto Compra Garantida comprador"),
    (("payment_procedure",), "site:www.mercadolivre.com.br/ajuda pagamento Pix boleto cartao prazo aprovacao compra"),
    (("shipping_procedure",), "site:www.mercadolivre.com.br/ajuda entrega envio atraso rastreamento frete compra"),
    (("claim_procedure",), "site:www.mercadolivre.com.br/ajuda reclamacao mediacao problema compra atendimento"),
    (("warranty_procedure",), "site:www.mercadolivre.com.br/ajuda garantia produto defeito cobertura Compra Garantida"),
    (("platform_procedure",), "site:www.mercadolivre.com.br/ajuda politicas procedimentos compra ajuda comprador"),
)
_PATHS = {
    "www.mercadolivre.com.br": ("/ajuda/", "/compra-garantida"),
    "vendedores.mercadolivre.com.br": ("/ajuda/", "/aprender/", "/nota/"),
}


def _official_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 2048 or re.search(r"[\s\x00-\x1f\x7f\\]", raw):
        return ""
    try:
        parsed = urlsplit(raw)
        host = str(parsed.hostname or "").lower()
        if (parsed.scheme != "https" or host not in _PATHS or parsed.username
                or parsed.password or parsed.port not in (None, 443)):
            return ""
        path = parsed.path
        for _ in range(8):
            decoded = unquote(path, errors="strict")
            if decoded == path:
                break
            path = decoded
        else:
            return ""
        if (re.search(r"[\x00-\x20\x7f\\?#]", path) or "//" in path
                or any(part in {".", ".."} for part in path.split("/"))):
            return ""
        allowed = any(
            path.startswith(prefix) if prefix.endswith("/")
            else path == prefix or path.startswith(prefix + "/")
            for prefix in _PATHS[host]
        )
        if not allowed:
            return ""
        # Search tracking, session identifiers and fragments are never forwarded.
        canonical = urlunsplit(("https", host, quote(path, safe="/-._~"), "", ""))
        if sanitize_public_research_text(canonical) != canonical:
            return ""
        return canonical
    except (TypeError, ValueError, UnicodeError):
        return ""


def _bounded_call(callback: Callable[[], Any], deadline: float) -> tuple[Any, str]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, "timeout"
    if not _WORKER_SLOTS.acquire(timeout=remaining):
        return None, "capacity_unavailable"
    results: Queue = Queue(maxsize=1)

    def run() -> None:
        try:
            results.put_nowait((callback(), ""))
        except Exception:
            results.put_nowait((None, "transport_unavailable"))
        finally:
            # Timed-out providers retain their permit until they actually exit.
            _WORKER_SLOTS.release()

    try:
        Thread(target=run, name="jk-ml-policy-read", daemon=True).start()
    except Exception:
        _WORKER_SLOTS.release()
        return None, "worker_unavailable"
    try:
        return results.get(timeout=max(0.0, deadline - time.monotonic()))
    except Empty:
        return None, "timeout"


def _public_addresses(host: str) -> list[str]:
    addresses = list(dict.fromkeys(
        str(row[4][0]) for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ))
    if not addresses:
        return []
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return []
    return addresses


def _read_official_page(url: str, *, deadline_monotonic: float) -> dict[str, str]:
    """Pin a public DNS address, verify TLS for the official host, reject redirects."""
    canonical = _official_url(url)
    if not canonical:
        return {}
    parsed = urlsplit(canonical)
    host = str(parsed.hostname)
    addresses = _public_addresses(host)
    remaining = deadline_monotonic - time.monotonic()
    if not addresses or remaining <= 0:
        return {}
    timeout = min(6.0, remaining)
    connection = http.client.HTTPSConnection(host, 443, timeout=timeout)
    raw_socket = None
    response = None
    try:
        # Connect to the checked numeric address, preventing a second DNS lookup.
        raw_socket = socket.create_connection((addresses[0], 443), timeout=timeout)
        if time.monotonic() >= deadline_monotonic:
            return {}
        connection.sock = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=host)
        raw_socket = None
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            return {}
        connection.sock.settimeout(min(6.0, remaining))
        connection.request("GET", parsed.path, headers={
            "User-Agent": "JK-Sistema-Policy-Reader/1.0",
            "Accept": "text/html,text/plain", "Accept-Encoding": "identity",
            "Connection": "close",
        })
        response = connection.getresponse()
        if response.status != 200:
            return {}
        content_type = str(response.getheader("Content-Type", "")).lower()
        if not any(kind in content_type for kind in ("text/html", "text/plain", "application/xhtml+xml")):
            return {}
        if str(response.getheader("Content-Encoding", "identity")).lower() not in ("", "identity"):
            return {}
        length = response.getheader("Content-Length")
        if length and (not str(length).isdigit() or int(length) > _MAX_BYTES):
            return {}
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                return {}
            if connection.sock:
                connection.sock.settimeout(min(4.0, remaining))
            chunk = response.read(min(8192, _MAX_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_BYTES:
                return {}
            chunks.append(chunk)
        return {"url": canonical, "text": b"".join(chunks).decode("utf-8", errors="replace")}
    finally:
        if response is not None:
            response.close()
        connection.close()
        if raw_socket is not None:
            raw_socket.close()


class _VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self.hidden_depth += 1
        if tag == "title":
            self.in_title = True
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "template"} and self.hidden_depth:
            self.hidden_depth -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)
            if self.in_title:
                self.title_parts.append(data)


def _clean_document(value: Any, url: str, fallback_title: object, consulted_at: str) -> dict:
    if isinstance(value, str):
        raw, final_url, title = value, url, fallback_title
    elif isinstance(value, dict):
        raw = str(value.get("text") or value.get("html") or "")
        final_url = str(value.get("final_url") or value.get("url") or url)
        title = value.get("title") or fallback_title
        if value.get("status_code", 200) != 200:
            return {}
    else:
        return {}
    canonical = _official_url(final_url)
    if not canonical or len(raw.encode("utf-8")) > _MAX_BYTES:
        return {}
    parser = _VisibleHTML()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return {}
    text = sanitize_public_research_text(" ".join(parser.parts), _MAX_TEXT)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) < 60 or re.search(r"(?i)(captcha|access denied|verifique que voc[eê] [eé] humano)", text[:600]):
        return {}
    clean_title = sanitize_public_research_text(" ".join(parser.title_parts) or str(title or ""), 240)
    return {"url": canonical, "title": clean_title.strip(), "text": text, "consulted_at": consulted_at}


def _default_search(query: str, **kwargs: Any) -> list[dict]:
    from .sources import _ia_agent_perguntas_buscar_web_publica
    # Public fixed queries need no tenant cache; avoid creating operational files.
    kwargs["client_id"] = ""
    return _ia_agent_perguntas_buscar_web_publica(query, **kwargs)


def _query_plans(topics: list[str]) -> list[tuple[list[str], str]]:
    effective = [topic for topic in topics if topic != "platform_procedure"]
    if not effective and "platform_procedure" in topics:
        effective = ["platform_procedure"]
    return [([topic for topic in group if topic in topics], query)
            for group, query in _GROUPS if any(topic in effective for topic in group)]


def _collect_query(plan: tuple[list[str], str], *, search: Callable, read: Callable,
                   client_id: str, deadline: float, consulted_at: str,
                   sources: list[dict], seen: set[str], document_limit: int) -> tuple[bool, str]:
    _topics, query = plan
    result, error = _bounded_call(
        lambda: search(query, client_id=client_id, max_results=6, fast=True), deadline,
    )
    if error or not isinstance(result, list):
        return False, error or "no_official_documents"
    found = False
    for row in result[:6]:
        if not isinstance(row, dict):
            continue
        url = _official_url(row.get("url") or row.get("link"))
        if not url:
            continue
        if url in seen:
            found = found or any(source["url"] == url for source in sources)
            continue
        if len(seen) >= document_limit:
            break
        seen.add(url)
        value, read_error = _bounded_call(lambda url=url: read(url, deadline_monotonic=deadline), deadline)
        if read_error:
            error = read_error
            continue
        document = _clean_document(value, url, row.get("title"), consulted_at)
        if document and not any(source["url"] == document["url"] for source in sources):
            sources.append(document)
            found = True
    return found, error or ("" if found else "no_official_documents")


def collect_official_marketplace_policy(
    topics: list[str], *, site_id: str, client_id: str,
    search: Callable | None = None, read: Callable | None = None,
    timeout_seconds: float = 18,
) -> dict[str, Any]:
    """Collect at most two fixed topic searches and three read official pages.

    Search adapter: ``search(query, client_id=..., max_results=6, fast=True)``.
    Read adapter: ``read(url, deadline_monotonic=...)`` -> text or a mapping with
    ``text`` and optional ``url``, ``final_url``, ``title``, ``status_code``.
    Search snippets never count as consulted policy documents.
    Status describes document access, not confirmation of any order's eligibility.
    """
    requested = topics if isinstance(topics, list) else []
    selected = [topic for topic in _TOPICS if topic in requested]
    consulted_at = datetime.now(timezone.utc).isoformat()
    site = str(site_id or "").strip()
    site = site if re.fullmatch(r"[A-Z]{3}", site) else ""
    result: dict[str, Any] = {
        "status": "unavailable", "site_id": site,
        "consulted_at": consulted_at, "topics": selected, "covered_topics": [], "sources": [],
    }
    if result["site_id"] != "MLB":
        return {**result, "status": "unsupported_site", "reason": "confirmed_mlb_site_required"}
    if not selected:
        return {**result, "reason": "no_supported_topics"}
    try:
        seconds = float(timeout_seconds)
    except (TypeError, ValueError):
        seconds = 0
    if not math.isfinite(seconds) or seconds <= 0:
        return {**result, "reason": "timeout"}
    deadline = time.monotonic() + min(seconds, 18.0)
    covered: set[str] = set()
    seen: set[str] = set()
    errors: list[str] = []
    all_plans = _query_plans(selected)
    plans = all_plans[:2]
    for index, plan in enumerate(plans):
        found, error = _collect_query(
            plan, search=search or _default_search, read=read or _read_official_page,
            client_id=client_id, deadline=deadline, consulted_at=consulted_at,
            sources=result["sources"], seen=seen,
            document_limit=2 if len(plans) == 2 and index == 0 else 3,
        )
        if found:
            covered.update(plan[0])
        if error:
            errors.append(error)
    if result["sources"]:
        result["status"] = "available" if set(selected) <= covered and not errors else "partial"
    result["covered_topics"] = [topic for topic in selected if topic in covered]
    if result["status"] != "available":
        result["reason"] = (
            "timeout" if "timeout" in errors else
            "topic_query_budget_exhausted" if len(all_plans) > 2 else
            "official_policy_documents_incomplete"
        )
    return result


__all__ = ["collect_official_marketplace_policy"]
