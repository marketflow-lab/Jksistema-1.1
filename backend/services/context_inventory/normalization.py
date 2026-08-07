"""Normalization component."""



from __future__ import annotations



import hashlib
import json
import re
import unicodedata
from typing import Any



from .contracts import _DOMAIN_ALIASES



def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _sha256_value(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))

def _strip_accents(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char))

def _slug(value: Any, *, fallback: str = "item") -> str:
    text = _strip_accents(value).lower().strip()
    text = re.sub(r"[^a-z0-9._-]+", "-", text)
    text = re.sub(r"[-_.]{2,}", "-", text).strip("-._")
    return text or fallback


def slug(value: Any, *, fallback: str = "item") -> str:
    """Public stable slug normalizer for Context Hub consumers."""

    return _slug(value, fallback=fallback)

def _normal_key(value: Any) -> str:
    return _slug(value, fallback="").replace("-", "_")

def _normal_api_path(value: str) -> str:
    path = str(value or "").strip().split("?", 1)[0]
    path = re.sub(r"\$\{[^}]+\}", "{dynamic}", path)
    # Starlette accepts converters such as ``{filename:path}``, while the
    # generated OpenAPI contract exposes the same parameter as ``{filename}``.
    # Stable API IDs follow the public OpenAPI spelling.
    path = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*):[^{}]+\}", r"{\1}", path)
    path = re.sub(r"/+", "/", path)
    if path != "/":
        path = path.rstrip("/")
    return path

def _domain(value: Any) -> str:
    raw = _slug(value, fallback="sistema").replace("-py", "")
    raw_under = raw.replace("-", "_").replace(".", "_")
    if raw_under in _DOMAIN_ALIASES:
        return _DOMAIN_ALIASES[raw_under]
    searchable = raw.replace("_", "-").replace(".", "-")
    canonical = {
        "admin",
        "cadastro",
        "documentacao",
        "estoque",
        "etiquetas",
        "favoritos",
        "fiscal",
        "ia",
        "integracoes",
        "medias-compras",
        "mercado-livre",
        "plataforma",
        "renovacao",
        "reuniao",
        "shared-sync",
        "sistema",
        "vendas",
        "whatsapp",
    }
    if searchable in canonical:
        return searchable
    groups: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("whatsapp", ("whatsapp",)),
        ("shared-sync", ("shared-sync", "drive-sync")),
        ("ia", ("codex", "-ia-", "ia-", "ia-sidebar", "gemini", "openai", "assistant")),
        (
            "mercado-livre",
            (
                "mercado-livre",
                "mercadolivre",
                "anunciosml",
                "perguntas-pos-venda",
                "ml-questions",
                "ml-pos-venda",
                "ml-product",
                "promoc",
                "promo",
                "full",
                "pesquisa-ml",
            ),
        ),
        ("vendas", ("venda", "devolu", "notas-entrada", "unidades-negocios")),
        ("estoque", ("estoque", "stock")),
        ("favoritos", ("favorito", "avant")),
        ("medias-compras", ("medias-compras", "importacoes", "importar-colunas")),
        ("fiscal", ("imposto", "fiscal", "monofasico", "siscomex", "transito", "simulador")),
        ("renovacao", ("renovacao",)),
        ("etiquetas", ("etiqueta", "qrcode")),
        ("reuniao", ("sala-reuniao", "rustdesk", "daily")),
        ("cadastro", ("cadastro", "produto", "foto", "ncm", "sku", "catalog")),
        ("integracoes", ("integracoes", "bling", "lojas", "oauth")),
        (
            "plataforma",
            (
                "electron",
                "android",
                "chrome-extension",
                "cloudflare",
                "firebase",
                "infra",
                "runtime",
                "mobile-update",
                "app-update",
                "package",
                "provision-python",
            ),
        ),
        (
            "admin",
            (
                "admin",
                "auth",
                "configur",
                "permission",
                "presence",
                "client-id",
                "dashboard",
                "global-ui",
                "frontend-index",
            ),
        ),
        ("documentacao", ("document", "guide", "readme")),
    )
    padded = f"-{searchable}-"
    for target, markers in groups:
        if any(marker in searchable or marker in padded for marker in markers):
            return target
    return "sistema"
