"""Public-network gates for technical research readers."""

from __future__ import annotations

import ipaddress
import re
import socket
from queue import Empty, Queue
from threading import Thread
from urllib.parse import urlparse

from .deep_research_contracts import canonical_research_url


def _perguntas_ia_v2_endereco_publico(value: object) -> bool:
    try:
        address = ipaddress.ip_address(str(value or "").split("%", 1)[0])
    except ValueError:
        return False
    return bool(
        address.is_global
        and not address.is_multicast
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_private
        and not address.is_reserved
        and not address.is_unspecified
    )


def _perguntas_ia_v2_host_resolve_somente_publico(host: str) -> bool:
    result: Queue = Queue(maxsize=1)

    def resolve() -> None:
        try:
            addresses = {
                str(entry[4][0]).split("%", 1)[0]
                for entry in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
                if entry[4]
            }
            result.put_nowait(
                bool(addresses)
                and all(_perguntas_ia_v2_endereco_publico(value) for value in addresses)
            )
        except Exception:
            try:
                result.put_nowait(False)
            except Exception:
                pass

    worker = Thread(target=resolve, name="jk-ppv-public-dns", daemon=True)
    try:
        worker.start()
    except Exception:
        return False
    worker.join(timeout=1.5)
    if worker.is_alive():
        return False
    try:
        return bool(result.get_nowait())
    except Empty:
        return False


def _perguntas_ia_v2_url_fonte_tecnica_segura(
    url: str,
    *,
    resolve_dns: bool = False,
    host_resolver=None,
) -> bool:
    try:
        parsed = urlparse(canonical_research_url(url))
    except Exception:
        return False
    host = str(parsed.hostname or "").strip().lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return False
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".local", ".internal", ".home.arpa", ".onion")
    ):
        return False
    if host in {"localtest.me", "lvh.me", "vcap.me"} or host.endswith(
        (".localtest.me", ".lvh.me", ".nip.io", ".sslip.io", ".xip.io")
    ):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None and re.fullmatch(r"(?:0x[0-9a-f]+|\d+)", host, flags=re.IGNORECASE):
        try:
            numeric = int(host, 16 if host.casefold().startswith("0x") else 10)
            address = ipaddress.ip_address(numeric)
        except (ValueError, OverflowError):
            return False
    if address is None and re.fullmatch(r"[0-9a-fx.]+", host, flags=re.IGNORECASE):
        return False
    if address is not None and not _perguntas_ia_v2_endereco_publico(address):
        return False
    resolver = host_resolver or _perguntas_ia_v2_host_resolve_somente_publico
    if resolve_dns and address is None and not resolver(host):
        return False
    if str(parsed.path or "").lower().endswith((
        ".7z", ".apk", ".bat", ".bin", ".cmd", ".com", ".dmg", ".exe",
        ".img", ".iso", ".jar", ".js", ".msi", ".ps1", ".rar", ".scr",
        ".sh", ".tar", ".tgz", ".vbs", ".xlsm", ".zip",
    )):
        return False
    return not any(
        domain in host
        for domain in ("mercadolivre.", "amazon.", "shopee.", "aliexpress.", "magazineluiza.")
    )


__all__ = [
    "_perguntas_ia_v2_endereco_publico",
    "_perguntas_ia_v2_host_resolve_somente_publico",
    "_perguntas_ia_v2_url_fonte_tecnica_segura",
]
