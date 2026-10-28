"""Read only the bounded, signed Bling XML needed to link a return invoice."""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qsl, urlsplit
from xml.parsers import expat

import requests
from urllib3.exceptions import HTTPError, ReadTimeoutError
from urllib3.util import Timeout


_XML_HOST = "www.bling.com.br"
_XML_PATH = "/relatorios/nfe.xml.php"
_XML_NAMESPACE = "http://www.portalfiscal.inf.br/nfe"
_MAX_BYTES = 1024 * 1024
_MAX_REFERENCES = 5
_READ_SECONDS = 3


class XmlReferenceError(Exception):
    def __init__(self, code, *, retry=False, after=0, status="unavailable"):
        self.code, self.retry, self.after, self.status = code, retry, after, status


def access_key(value) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{44}", value) else ""


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise XmlReferenceError("bling_timeout", retry=True, after=60)
    return remaining


def _validated_url(url, key):
    """Accept only the exact download endpoint returned in NFe details."""
    try:
        if (not isinstance(url, str) or not 1 <= len(url) <= 4096
                or any(ord(char) <= 32 or ord(char) == 127 for char in url)
                or "\\" in url or not access_key(key)):
            raise ValueError
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != _XML_HOST
                or parsed.path != _XML_PATH or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError
        # Bling also emits the empty field as '&s&'. Normalize only that exact
        # field for parsing; the original signed URL is sent unchanged.
        query = "&".join("s=" if field == "s" else field for field in parsed.query.split("&"))
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=3)
        fields = dict(pairs)
        signature = fields.get("signature", "")
        if (len(pairs) != len(fields)
                or set(fields) not in ({"chaveAcesso", "signature"}, {"chaveAcesso", "signature", "s"})
                or fields.get("s", "") != ""
                or fields["chaveAcesso"] != key or not 1 <= len(signature) <= 2048
                or any(ord(char) <= 32 or ord(char) >= 127 for char in signature)):
            raise ValueError
        return fields
    except (ValueError, TypeError):
        raise XmlReferenceError("bling_xml_unsafe_url") from None


def _download(url, key, deadline):
    expected = _validated_url(url, key)
    remaining = _remaining(deadline)
    response = None
    # A fresh session with trust_env disabled cannot pick up netrc credentials,
    # ambient authorization, cookies or proxy settings for this signed URL.
    with requests.Session() as session:
        session.trust_env = False
        session.headers.clear()
        try:
            response = session.get(
                url, headers={"Accept": "application/xml, text/xml", "Accept-Encoding": "identity"},
                stream=True, allow_redirects=False,
                timeout=Timeout(total=min(10, remaining), connect=min(3.05, remaining),
                                read=min(_READ_SECONDS, remaining)),
            )
            _remaining(deadline)
            if _validated_url(str(response.url), key) != expected:
                raise XmlReferenceError("bling_xml_unsafe_url")
            status = int(response.status_code)
            if getattr(response, "history", None) or 300 <= status < 400:
                raise XmlReferenceError("bling_xml_redirect")
            if status == 429:
                delay = str(response.headers.get("Retry-After", "60"))
                after = min(3600, max(5, int(delay))) if delay.isdigit() else 60
                raise XmlReferenceError("bling_rate_limit", retry=True, after=after)
            if status >= 500:
                raise XmlReferenceError("bling_unavailable", retry=True, after=60)
            if status != 200:
                raise XmlReferenceError("bling_xml_unavailable")
            if str(response.headers.get("Content-Encoding", "identity")).strip().lower() not in ("", "identity"):
                raise XmlReferenceError("bling_xml_compressed")
            length = str(response.headers.get("Content-Length", ""))
            if length and (not length.isdigit() or len(length) > 10 or int(length) > _MAX_BYTES):
                raise XmlReferenceError("bling_xml_too_large")
            payload = bytearray()
            while True:
                # Leave enough time for the next socket read. read1 performs at
                # most one underlying read, so a slow stream cannot hide inside
                # an unbounded attempt to fill an entire chunk.
                if _remaining(deadline) < _READ_SECONDS:
                    raise XmlReferenceError("bling_timeout", retry=True, after=60)
                chunk = response.raw.read1(8192, decode_content=False)
                _remaining(deadline)
                if not chunk:
                    break
                if len(payload) + len(chunk) > _MAX_BYTES:
                    raise XmlReferenceError("bling_xml_too_large")
                payload.extend(chunk)
            if length and int(length) != len(payload):
                raise XmlReferenceError("bling_xml_incomplete", retry=True, after=60)
            return bytes(payload)
        except (requests.exceptions.Timeout, ReadTimeoutError):
            raise XmlReferenceError("bling_timeout", retry=True, after=60) from None
        except (requests.exceptions.RequestException, HTTPError):
            raise XmlReferenceError("bling_xml_unavailable", retry=True, after=60) from None
        finally:
            if response is not None:
                response.close()


def _parse(payload, key, sku, deadline):
    """Collect scalar references only; DTDs/entities and duplicate NFes fail closed."""
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack, values, invoice_ids = [], {}, []
    node_count = 0
    prefix = None

    def reject(*_args):
        raise XmlReferenceError("bling_xml_unsafe")

    def name(local):
        return _XML_NAMESPACE + "}" + local

    def start(tag, attributes):
        nonlocal node_count, prefix
        _remaining(deadline)
        stack.append(tag)
        node_count += 1
        if len(stack) > 64 or node_count > 20000:
            raise XmlReferenceError("bling_xml_too_complex")
        if len(stack) == 1:
            if tag == name("NFe"):
                prefix = (name("NFe"), name("infNFe"))
            elif tag == name("nfeProc"):
                prefix = (name("nfeProc"), name("NFe"), name("infNFe"))
            else:
                raise XmlReferenceError("bling_xml_invalid")
        if tuple(stack) == prefix:
            invoice_ids.append(attributes.get("Id", ""))
            if len(invoice_ids) > 1:
                raise XmlReferenceError("bling_xml_invalid")
        path = tuple(stack)
        if path in targets():
            values.setdefault(path, []).append("")

    def targets():
        if prefix is None:
            return ()
        return (prefix + (name("ide"), name("tpNF")),
                prefix + (name("ide"), name("NFref"), name("refNFe")),
                prefix + (name("det"), name("prod"), name("cProd")),
                (name("nfeProc"), name("protNFe"), name("infProt"), name("chNFe")))

    def characters(text):
        path = tuple(stack)
        if path in values:
            values[path][-1] += text
            if len(values[path][-1]) > 512:
                raise XmlReferenceError("bling_xml_invalid")

    parser.StartElementHandler = start
    parser.EndElementHandler = lambda _tag: stack.pop()
    parser.CharacterDataHandler = characters
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    try:
        _remaining(deadline)
        parser.Parse(payload, True)
        _remaining(deadline)
    except expat.ExpatError:
        raise XmlReferenceError("bling_xml_invalid") from None
    if len(invoice_ids) != 1:
        raise XmlReferenceError("bling_xml_invalid")
    type_path, reference_path, sku_path, protocol_path = targets()
    types = [value.strip() for value in values.get(type_path, [])]
    protocols = [value.strip() for value in values.get(protocol_path, [])]
    if (invoice_ids[0] != "NFe" + key or types != ["0"]
            or any(value != key for value in protocols)):
        raise XmlReferenceError("bling_xml_document_mismatch")
    if sku not in {value.strip().upper() for value in values.get(sku_path, [])}:
        raise XmlReferenceError("bling_xml_sku_mismatch")
    keys = list(dict.fromkeys(value.strip() for value in values.get(reference_path, [])))
    if any(not access_key(value) or value == key for value in keys):
        raise XmlReferenceError("bling_xml_invalid_reference")
    if len(keys) > _MAX_REFERENCES:
        raise XmlReferenceError("bling_ambiguous_document", status="ambiguous")
    return keys


def referenced_invoice_keys(document, sku, *, deadline):
    """Verify the inbound XML against its authenticated API detail, then discard it."""
    url = document.get("xml")
    if not url:
        return []
    key = access_key(document.get("chaveAcesso"))
    if not key or str(document.get("tipo")) != "0":
        raise XmlReferenceError("bling_xml_document_mismatch")
    sku = str(sku or "").strip().upper()
    if not sku:
        raise XmlReferenceError("bling_sku_missing", status="not_linked")
    payload = _download(url, key, deadline)
    return _parse(payload, key, sku, deadline)
