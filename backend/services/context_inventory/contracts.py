"""Stable contracts for the semantic Context Hub inventory."""

from __future__ import annotations

import re

INVENTORY_SCHEMA_VERSION = 1

SKU_SCHEMA_VERSION = 2

ENTITY_KEYS = (
    "id",
    "kind",
    "domain",
    "title",
    "surface",
    "tenant_scope",
    "sensitivity",
    "truth_class",
    "source_refs",
    "source_hash",
    "relationships",
    "metadata",
    "content",
)

_PYTHON_SOURCE_ROOTS = (
    "backend/services",
    "backend/modules",
)

_INFRA_ROOTS: tuple[tuple[str, str, str], ...] = (
    ("electron", "Electron", "electron_app"),
    ("android", "Android", "android_app"),
    ("chrome-extensions", "Extensoes Chrome", "extensoes_chrome"),
    ("cloudflare", "Cloudflare", "cloudflare"),
    ("cloudflare-workers", "Cloudflare Workers", "cloudflare_workers"),
    ("infra", "Infraestrutura", "infra"),
)

_INFRA_SUFFIXES = {
    ".bat",
    ".css",
    ".gradle",
    ".html",
    ".java",
    ".js",
    ".json",
    ".kt",
    ".md",
    ".nsh",
    ".ps1",
    ".py",
    ".sql",
    ".toml",
    ".ts",
    ".yaml",
    ".yml",
}

_EXCLUDED_DIR_NAMES = {
    ".git",
    ".obsidian",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "dist-client-setup",
    "logs",
    "node_modules",
    "test-results",
    "context_hub",
    "ContextVault",
}

_BLOCKED_FILE_NAME_PARTS = (
    ".env",
    "credential",
    "credencial",
    "private-key",
    "private_key",
    "secret",
    "token",
)

_SKU_REQUIRED_KEYS = {
    "schema_version",
    "sku",
    "nome_produto",
    "o_que_e",
    "para_que_serve",
    "caracteristicas_tecnicas",
    "medidas_do_produto",
    "modo_de_funcionamento",
    "instalacao",
    "oem",
    "aplicacao",
    "revisao",
    "atualizado_em",
}

_SKU_FORBIDDEN_KEY_PARTS = {
    "access_token",
    "address",
    "buyer",
    "client_secret",
    "comprador",
    "cpf",
    "cnpj",
    "email",
    "endereco",
    "fonte",
    "jwt",
    "oauth",
    "password",
    "refresh_token",
    "senha",
    "telefone",
    "token",
    "url",
}

_SENSITIVE_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:access[_-]?token|refresh[_-]?token|client[_-]?secret|api[_-]?key|password|senha)\b\s*[:=]\s*[\"']?[^\s\"']{8,}"
        ),
    ),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("cpf", re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")),
)

_SKU_SENSITIVE_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    pattern for _code, pattern in _SENSITIVE_VALUE_PATTERNS
) + (
    re.compile(r"(?i)(?:\+?55\s*)?\([1-9]{2}\)\s*9?\d{4}[\s.-]*\d{4}"),
    re.compile(r"(?i)\b(?:telefone|whatsapp|celular)\b\s*[:=]\s*\+?[\d(). -]{8,}"),
    re.compile(r"(?<!\d)\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}(?!\d)"),
)

_ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}

_DOMAIN_ALIASES = {
    "admin_usuarios": "admin",
    "auth": "admin",
    "codex": "ia",
    "codex_console": "ia",
    "configuracoes": "admin",
    "frontend": "admin",
    "ia": "ia",
    "importacoes": "medias-compras",
    "mercado_livre": "mercado-livre",
    "perguntas_pos_venda": "mercado-livre",
    "promocoes": "mercado-livre",
    "shared_sync": "shared-sync",
    "whatsapp": "whatsapp",
    "whatsapp_bridge": "whatsapp",
}
