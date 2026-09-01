"""Versioned, reviewed registry of manufacturer-owned public domains.

Search-result text never grants official authority. New manufacturers are added
here only after domain ownership is reviewed, keeping the trust decision global,
auditable and independent from tenants, listings and retrieved page content.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata


OFFICIAL_SOURCE_REGISTRY_VERSION = "jk_official_source_registry_v1"

_ROOT_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$"
)

_REGISTRY: dict[str, tuple[str, ...]] = {
    "bmw": ("bmw.com", "bmw.com.br"),
    "bosch": ("bosch.com", "bosch.com.br"),
    "chevrolet": ("chevrolet.com", "chevrolet.com.br"),
    "citroen": ("citroen.com", "citroen.com.br", "citroen.fr"),
    "cofap": ("mmcofap.com.br",),
    "continental": ("continental.com",),
    "delphi": ("delphiautoparts.com",),
    "denso": ("denso.com",),
    "fiat": ("fiat.com", "fiat.com.br"),
    "ford": ("ford.com", "ford.com.br"),
    "grundfos": ("grundfos.com", "product-selection.grundfos.com"),
    "honda": ("honda.com", "honda.com.br"),
    "hyundai": ("hyundai.com", "hyundai.com.br"),
    "kia": ("kia.com", "kia.com.br"),
    "mercedes": ("mercedes-benz.com", "mercedes-benz.com.br"),
    "nissan": ("nissan-global.com", "nissan.com.br"),
    "peugeot": ("peugeot.com", "peugeot.com.br", "peugeot.fr"),
    "renault": ("renault.com", "renault.com.br", "renault.fr"),
    "schneider": ("se.com", "schneider-electric.com"),
    "seaflo": ("seaflo.com",),
    "shimano": ("shimano.com",),
    "toyota": ("toyota.com", "toyota.com.br"),
    "valeo": ("valeo.com",),
    "volkswagen": ("volkswagen.com", "volkswagen.com.br"),
    "vonder": ("vonder.com.br",),
    "vw": ("volkswagen.com", "volkswagen.com.br"),
    "weg": ("weg.net",),
}

def _validate_registry() -> None:
    for brand, roots in _REGISTRY.items():
        if not re.fullmatch(r"[a-z0-9]{2,40}", brand) or not roots:
            raise RuntimeError("Invalid official-source brand registry entry.")
        for root in roots:
            if not _ROOT_RE.fullmatch(root) or root.endswith(
                (".local", ".internal", ".home.arpa", ".onion")
            ):
                raise RuntimeError("Invalid official-source domain registry entry.")
            try:
                ipaddress.ip_address(root)
            except ValueError:
                continue
            raise RuntimeError("IP addresses are forbidden in the official-source registry.")


_validate_registry()


def official_domains_for_brand(value: object) -> tuple[str, ...]:
    key = re.sub(r"[^a-z0-9]", "", str(value or "").casefold())
    return _REGISTRY.get(key, ())


def official_domains_for_target_identity(value: object) -> tuple[str, ...]:
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", str(value or "").casefold())
        if not unicodedata.combining(character)
    )
    brand_matches = {
        roots
        for token in re.findall(r"[a-z0-9]{2,40}", normalized)
        if (roots := official_domains_for_brand(token))
    }
    if len(brand_matches) == 1:
        return next(iter(brand_matches))
    if len(brand_matches) > 1:
        return ()
    return ()


def is_reviewed_official_domain(value: object) -> bool:
    host = str(value or "").strip(".").casefold()
    return any(
        host == root or host.endswith("." + root)
        for roots in _REGISTRY.values()
        for root in roots
    )


__all__ = [
    "OFFICIAL_SOURCE_REGISTRY_VERSION",
    "is_reviewed_official_domain",
    "official_domains_for_brand",
    "official_domains_for_target_identity",
]
