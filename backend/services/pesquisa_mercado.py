"""SQLite checkpoints and atomic seller publications for Pesquisa de Mercado.

The Electron main process owns navigation and the shared Avant ranking calculation.
This service stores that calculation, verifies its provenance and seller identity,
and exposes paginated rankings without loading the whole crawl into memory.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException

from backend.services.cadastro_tenant_trust import (
    CadastroTenantTrustErro,
    resolver_alias_tenant_registrado,
)

ACTIVE_STATUSES = {"running", "paused", "waiting_login"}
TERMINAL_STATUSES = {"cancelled", "completed", "completed_partial", "failed"}
STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES
MAX_CHECKPOINT_BYTES = 64 * 1024 * 1024
MAX_PUBLICATION_BYTES = 64 * 1024 * 1024
COUNTER_KEYS = ("categories", "pages", "sellers", "ads", "pending")
CATEGORY_LISTING_HOSTS = {
    "lista.mercadolivre.com.br",
    "imoveis.mercadolivre.com.br",
    "servicos.mercadolivre.com.br",
}
_STORE = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and 0 <= number <= 1e18 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _integer(value: Any, default: int = 0) -> int:
    number = _number(value)
    return min(int(number), 2**63 - 1) if number is not None else default


def _json(value: Any, max_bytes: int) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise HTTPException(422, "Dados de pesquisa invalidos.") from None
    if len(encoded.encode("utf-8")) > max_bytes:
        raise HTTPException(413, "Etapa da pesquisa excede o tamanho permitido.")
    return encoded


def _ml_url(value: Any, *, image: bool = False) -> str:
    raw = _text(value, 4000)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        allowed = (host == "mercadolivre.com.br" or host.endswith(".mercadolivre.com.br"))
        if image:
            allowed = allowed or host == "mlstatic.com" or host.endswith(".mlstatic.com")
        if not allowed or parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
            return ""
        return urlunsplit(("https", host, parts.path, parts.query, ""))
    except ValueError:
        return ""


def normalize_scope_category(value: Any) -> dict:
    """Validate the initial category before creating any persistent execution."""
    if not isinstance(value, dict) or set(value) != {"key", "nome", "url"}:
        raise HTTPException(422, "Categoria de pesquisa invalida.")
    limits = {"key": 500, "nome": 400, "url": 4000}
    for field, limit in limits.items():
        raw = value.get(field)
        if not isinstance(raw, str) or not raw.strip() or len(raw) > limit or any(ord(char) < 32 or ord(char) == 127 for char in raw):
            raise HTTPException(422, "Categoria de pesquisa invalida.")
    try:
        source_url = urlsplit(value["url"].strip())
        if source_url.username or source_url.password or source_url.port is not None:
            raise HTTPException(422, "Link de categoria invalido.")
    except ValueError:
        raise HTTPException(422, "Link de categoria invalido.") from None
    url = _ml_url(value["url"])
    if not url:
        raise HTTPException(422, "Link de categoria invalido.")
    parts = urlsplit(url)
    path = unquote(parts.path).lower()
    host = (parts.hostname or "").lower()
    query = parse_qs(parts.query, keep_blank_values=True)
    blocked_seller = any(re.sub(r"[_-]", "", field).lower() in {"seller", "sellerid", "custid", "customerid", "user", "userid"} for field in query)
    blocked_seller = blocked_seller or any(re.search(r"(?:_CustId_|_SellerId_|(?:^|[|,;])(?:seller[_-]?id|cust[_-]?id):)", value, re.I) for values in query.values() for value in values)
    blocked_product = any(re.sub(r"[_-]", "", field).lower() in {"wid", "itemid", "productid", "catalogproductid"} for field in query)
    blocked_product = blocked_product or any(re.search(r"(?:^|[|,;])(?:item_id|catalog_product_id):", value, re.I) for values in query.values() for value in values)
    blocked_path = re.search(r"(?:^|/)(?:p|up|perfil|loja|stores?|vendedor)(?:/|$)|/MLB-\d+|_CustId_|_SellerId_", path, re.I)
    if blocked_path or blocked_seller or blocked_product:
        raise HTTPException(422, "Selecione uma categoria do Mercado Livre.")
    listing = host in CATEGORY_LISTING_HOSTS and bool(path.strip("/"))
    landing = host in {"mercadolivre.com.br", "www.mercadolivre.com.br"} and bool(re.match(r"/(?:c|categorias)/[^/]+", path))
    if not listing and not landing:
        raise HTTPException(422, "Selecione uma categoria do Mercado Livre.")
    # Credentials and tracking parameters must not become saved scope metadata.
    for field in list(query):
        if re.search(r"(?:token|password|senha|secret|authorization|api[_-]?key|cookie)", field, re.I):
            raise HTTPException(422, "Link de categoria invalido.")
        if re.match(r"(?:tracking|utm_|matt_|position|type|search_layout)", field, re.I):
            query.pop(field)
    normalized_url = urlunsplit(("https", host, parts.path.rstrip("/") or "/", urlencode(query, doseq=True), ""))
    return {"key": value["key"].strip(), "nome": _text(value["nome"], 400), "url": normalized_url}


def _canonical_profile(value: Any) -> str:
    url = _ml_url(value)
    if not url:
        return ""
    parts = urlsplit(url)
    path = unquote(parts.path).rstrip("/").lower()
    # _CustId_ and seller_id prove numeric identity across profile/listing URLs.
    match = re.search(r"_custid_(\d+)", path, re.I)
    query = parse_qs(parts.query)
    customer = (query.get("seller_id") or query.get("cust_id") or [""])[0]
    if match or str(customer).isdigit():
        return "id:" + (match.group(1) if match else str(customer))
    if path.startswith("/perfil/") and path != "/perfil":
        return "profile:" + path
    if path.startswith("/loja/") and path != "/loja":
        return "store:" + path
    return ""


def _seller_identity(seller: dict) -> tuple[str, str]:
    seller_id = _text(seller.get("id"), 80)
    return (seller_id if seller_id.isdigit() else "", _canonical_profile(seller.get("perfilUrl")))


def _matches_identity(seller: dict, seller_id: Any, profile: Any) -> bool:
    expected_id, expected_profile = _seller_identity(seller)
    observed_id = _text(seller_id, 80)
    observed_profile = _canonical_profile(profile)
    if expected_id and observed_id:
        return expected_id == observed_id
    if expected_id and observed_profile.startswith("id:"):
        return observed_profile == "id:" + expected_id
    if expected_profile and observed_id and expected_profile.startswith("id:"):
        return expected_profile == "id:" + observed_id
    return bool(expected_profile and observed_profile == expected_profile)


def _normalize_ad(raw: dict, seller: dict, original_index: int) -> tuple[str, dict]:
    item_id = _text(raw.get("id") or raw.get("mlb"), 100).upper().replace("-", "")
    url = _ml_url(raw.get("url") or raw.get("permalink") or raw.get("link"))
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    catalog_match = re.search(r"/p/(MLB\d+)(?:/|$)", parts.path, re.I)
    if catalog_match and item_id == catalog_match.group(1).upper():
        item_id = ""
    if not re.fullmatch(r"MLB\d+", item_id):
        item_id = ""
        for field in ("wid", "item_id"):
            candidate = str((query.get(field) or [""])[0]).upper().replace("-", "")
            if re.fullmatch(r"MLB\d+", candidate):
                item_id = candidate
                break
        if not item_id:
            pdp = str((query.get("pdp_filters") or [""])[0])
            match = re.search(r"(?:^|[|,;])item_id:(MLB-?\d+)(?:$|[|,;])", pdp, re.I)
            if match:
                item_id = match.group(1).upper().replace("-", "")
        if not item_id and not catalog_match:
            match = re.search(r"(?:^|/)MLB-(\d+)(?:-|/|$)", parts.path, re.I)
            item_id = "MLB" + match.group(1) if match else ""
    if not item_id and not url:
        raise HTTPException(422, "Anuncio sem identificador ou link valido do Mercado Livre.")
    key = "id:" + item_id if item_id else "url:" + url
    source = _text(raw.get("vendasFonte"), 100).lower()
    monthly_source = _text(raw.get("media_mensal_fonte"), 100).lower()
    sales = _number(raw.get("vendas"))
    monthly = _number(raw.get("media_vendas_mensal"))
    belongs = _matches_identity(
        seller, raw.get("seller_id") or raw.get("vendedor_id") or raw.get("sellerId"),
        raw.get("seller_perfil_url") or raw.get("vendedorPerfilUrl"),
    )
    catalog = bool(raw.get("catalogo") or raw.get("catalog_listing") or raw.get("vendas_agregadas_catalogo"))
    catalog_identified = _matches_identity(
        seller, raw.get("avant_seller_id"), raw.get("avant_seller_perfil_url"),
    )
    avant = any(value in {"avant", "avant_pro", "avant-pro"} or value.startswith("avantpro") for value in (source, monthly_source))
    reason = _text(raw.get("erro"), 600)
    if not belongs:
        reason = "Identidade do vendedor do anuncio nao confirmada."
    elif catalog and not catalog_identified:
        reason = "Vendas de catalogo sem identificacao especifica do vendedor."
    elif not avant:
        reason = "Metricas do Avant Pro indisponiveis."
    elif monthly is None:
        reason = reason or "Metricas insuficientes do Avant Pro."
    rankable = bool(belongs and (not catalog or catalog_identified) and avant and monthly is not None and raw.get("rankable") is not False)
    if not rankable and not reason:
        reason = "Metricas insuficientes do Avant Pro."
    ad = {
        "id": item_id, "url": url, "titulo": _text(raw.get("titulo")),
        "imagem": _ml_url(raw.get("imagem"), image=True), "preco": _number(raw.get("preco")),
        "vendedor": seller["nome"], "seller_key": seller["key"],
        "categoria": _text(raw.get("categoria") or seller.get("categoria"), 400),
        "vendas": sales, "vendasFonte": source, "data_criacao": _text(raw.get("data_criacao"), 80),
        "media_mensal_fonte": monthly_source,
        "media_vendas_mensal": monthly, "rankable": rankable,
        "status": "ranked" if rankable else "pending", "erro": "" if rankable else reason,
        "posicao": _integer(raw.get("posicao"), original_index + 1),
        "seller_id": _text(raw.get("seller_id") or raw.get("vendedor_id") or raw.get("sellerId"), 80),
        "seller_perfil_url": _ml_url(raw.get("seller_perfil_url") or raw.get("vendedorPerfilUrl")),
        "catalogo": catalog, "avant_seller_id": _text(raw.get("avant_seller_id"), 80),
        "avant_seller_perfil_url": _ml_url(raw.get("avant_seller_perfil_url")),
    }
    return key, ad


def _ranking_key(ad: dict) -> str:
    return json.dumps([
        int(ad["rankable"]), ad["media_vendas_mensal"], ad["vendas"],
        ad["posicao"] or 9999, ad["ordem_coleta"],
    ], separators=(",", ":"))


def _ranking_compare(left: str, right: str) -> int:
    """Mirror the Favoritos comparator, including its monthly tolerance."""
    a, b = json.loads(left), json.loads(right)
    if a[0] != b[0]:
        return -1 if a[0] > b[0] else 1
    monthly_a, monthly_b = (a[1] if a[1] is not None else -1), (b[1] if b[1] is not None else -1)
    if abs(monthly_a - monthly_b) > 0.0001:
        return -1 if monthly_a > monthly_b else 1
    sales_a, sales_b = (a[2] if a[2] is not None else -1), (b[2] if b[2] is not None else -1)
    if sales_a != sales_b:
        return -1 if sales_a > sales_b else 1
    for index in (3, 4):
        if a[index] != b[index]:
            return -1 if a[index] < b[index] else 1
    return 0


class PesquisaMercadoStore:
    def __init__(self, info_root: str | Path):
        self.info_root = Path(info_root).resolve()
        self._schema_lock = threading.Lock()
        self._initialized: set[str] = set()

    def _owner(self, client_id: str, username: str) -> tuple[str, str]:
        tenant = str(client_id or "").strip()
        user = str(username or "").strip().lower()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", tenant):
            raise HTTPException(401, "Cliente autenticado invalido.")
        if not user or len(user) > 256 or any(ord(c) < 32 for c in user):
            raise HTTPException(401, "Identidade autenticada indisponivel.")
        return tenant, user

    def _path(self, tenant: str) -> Path:
        directory = self.info_root / tenant
        directory.mkdir(parents=True, exist_ok=True)
        if directory.resolve() != directory:
            try:
                directory = Path(resolver_alias_tenant_registrado(self.info_root, tenant))
            except CadastroTenantTrustErro:
                raise HTTPException(503, "Diretorio de pesquisa indisponivel.") from None
        path = directory / "pesquisa_mercado.db"
        if path.is_symlink() or path.resolve().parent != directory.resolve():
            raise HTTPException(503, "Banco de pesquisa indisponivel.")
        return path

    def _connect(self, tenant: str) -> sqlite3.Connection:
        path = self._path(tenant)
        conn = sqlite3.connect(str(path), timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.create_collation("PM_RANKING", _ranking_compare)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        with self._schema_lock:
            if str(path) not in self._initialized:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS pm_jobs (
                        id TEXT PRIMARY KEY, tenant TEXT NOT NULL, username TEXT NOT NULL,
                        status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                        checkpoint TEXT NOT NULL DEFAULT '{}', counters TEXT NOT NULL DEFAULT '{}',
                        message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                        next_order INTEGER NOT NULL DEFAULT 1
                    );
                    CREATE INDEX IF NOT EXISTS pm_jobs_owner ON pm_jobs(tenant,username,created_at DESC);
                    CREATE UNIQUE INDEX IF NOT EXISTS pm_jobs_one_active ON pm_jobs(tenant,username)
                        WHERE status IN ('running','paused','waiting_login');
                    CREATE TABLE IF NOT EXISTS pm_sellers (
                        job_id TEXT NOT NULL REFERENCES pm_jobs(id) ON DELETE CASCADE,
                        seller_key TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
                        PRIMARY KEY(job_id,seller_key)
                    );
                    CREATE TABLE IF NOT EXISTS pm_ads (
                        job_id TEXT NOT NULL, seller_key TEXT NOT NULL, ad_key TEXT NOT NULL,
                        payload TEXT NOT NULL, category TEXT NOT NULL, search_text TEXT NOT NULL,
                        rankable INTEGER NOT NULL, monthly REAL, sales REAL, original_position INTEGER NOT NULL,
                        collection_order INTEGER NOT NULL, ranking_key TEXT NOT NULL,
                        PRIMARY KEY(job_id,seller_key,ad_key),
                        FOREIGN KEY(job_id,seller_key) REFERENCES pm_sellers(job_id,seller_key) ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS pm_ads_seller ON pm_ads(job_id,seller_key);
                    CREATE INDEX IF NOT EXISTS pm_ads_category ON pm_ads(job_id,category);
                    CREATE INDEX IF NOT EXISTS pm_ads_pending ON pm_ads(job_id,rankable);
                    CREATE TABLE IF NOT EXISTS pm_discovery (
                        job_id TEXT NOT NULL REFERENCES pm_jobs(id) ON DELETE CASCADE,
                        seed_key TEXT NOT NULL, category_key TEXT NOT NULL,
                        status TEXT NOT NULL, item_id TEXT NOT NULL, url TEXT NOT NULL,
                        PRIMARY KEY(job_id,seed_key)
                    );
                    CREATE INDEX IF NOT EXISTS pm_discovery_status ON pm_discovery(job_id,status);
                """)
                columns = {row[1] for row in conn.execute("PRAGMA table_info(pm_ads)")}
                if "ranking_key" not in columns:
                    conn.execute("ALTER TABLE pm_ads ADD COLUMN ranking_key TEXT NOT NULL DEFAULT ''")
                    for row in conn.execute("SELECT job_id,seller_key,ad_key,payload FROM pm_ads").fetchall():
                        conn.execute("UPDATE pm_ads SET ranking_key=? WHERE job_id=? AND seller_key=? AND ad_key=?", (_ranking_key(json.loads(row["payload"])), row["job_id"], row["seller_key"], row["ad_key"]))
                conn.execute("CREATE INDEX IF NOT EXISTS pm_ads_shared_ranking ON pm_ads(job_id,ranking_key COLLATE PM_RANKING)")
                self._initialized.add(str(path))
        return conn

    @contextmanager
    def _connection(self, tenant: str, write: bool = False):
        conn = None
        try:
            conn = self._connect(tenant)
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except sqlite3.Error:
            if conn is not None:
                conn.rollback()
            raise HTTPException(503, "Persistencia da pesquisa temporariamente indisponivel.") from None
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _job(conn, job_id: str, tenant: str, user: str):
        row = conn.execute("SELECT * FROM pm_jobs WHERE id=? AND tenant=? AND username=?", (job_id, tenant, user)).fetchone()
        if row is None:
            raise HTTPException(404, "Pesquisa nao encontrada.")
        return row

    @staticmethod
    def _public(row) -> dict:
        return {
            "id": row["id"], "status": row["status"], "revision": row["revision"],
            "checkpoint": json.loads(row["checkpoint"]), "counters": json.loads(row["counters"]),
            "message": row["message"], "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    @staticmethod
    def _pending(conn, job_id: str) -> int:
        ads = conn.execute("SELECT COUNT(*) FROM pm_ads WHERE job_id=? AND rankable=0", (job_id,)).fetchone()[0]
        failures = conn.execute("SELECT COUNT(*) FROM pm_discovery WHERE job_id=? AND status='failed'", (job_id,)).fetchone()[0]
        return ads + failures

    def start(self, client_id: str, username: str, category: dict | None = None) -> dict:
        tenant, user = self._owner(client_id, username)
        normalized_category = normalize_scope_category(category) if category is not None else None
        checkpoint = {
            "version": 1, "scope": {"category": normalized_category}, "categories": [normalized_category],
            "categoryIndex": 0, "discoveryInitialized": True, "phase": "categories",
        } if normalized_category is not None else {}
        with self._connection(tenant, write=True) as conn:
            active = conn.execute("SELECT * FROM pm_jobs WHERE tenant=? AND username=? AND status IN ('running','paused','waiting_login')", (tenant, user)).fetchone()
            if active is not None:
                return self._public(active)
            job_id = uuid.uuid4().hex
            now = _now()
            counters = _json(dict.fromkeys(COUNTER_KEYS, 0), 1024)
            message = f"Preparando pesquisa em {normalized_category['nome']}." if normalized_category else "Preparando pesquisa em todo ML."
            conn.execute("INSERT INTO pm_jobs(id,tenant,username,status,checkpoint,counters,message,created_at,updated_at) VALUES(?,?,?,'running',?,?,?,?,?)", (job_id, tenant, user, _json(checkpoint, MAX_CHECKPOINT_BYTES), counters, message, now, now))
            return self._public(self._job(conn, job_id, tenant, user))

    def latest(self, client_id: str, username: str) -> dict | None:
        tenant, user = self._owner(client_id, username)
        with self._connection(tenant) as conn:
            row = conn.execute("SELECT * FROM pm_jobs WHERE tenant=? AND username=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (tenant, user)).fetchone()
            return self._public(row) if row else None

    def get(self, job_id: str, client_id: str, username: str) -> dict:
        tenant, user = self._owner(client_id, username)
        with self._connection(tenant) as conn:
            return self._public(self._job(conn, job_id, tenant, user))

    def checkpoint(self, job_id: str, client_id: str, username: str, *, checkpoint=None, status=None, counters=None, message=None) -> dict:
        tenant, user = self._owner(client_id, username)
        if status is not None and status not in STATUSES:
            raise HTTPException(422, "Estado de pesquisa invalido.")
        if checkpoint is not None and not isinstance(checkpoint, dict):
            raise HTTPException(422, "Checkpoint de pesquisa invalido.")
        discovery = None
        if checkpoint is not None:
            checkpoint = dict(checkpoint)
            event = checkpoint.pop("discoveredSeed", None)
            if event is not None:
                if not isinstance(event, dict) or event.get("status") not in {"consulted", "failed"}:
                    raise HTTPException(422, "Evento de descoberta invalido.")
                seed_key = self._seed_key(event.get("key"))
                discovery = (job_id, seed_key, _text(event.get("category_key"), 500), event["status"], _text(event.get("id"), 100), _ml_url(event.get("url")))
        encoded_checkpoint = _json(checkpoint, MAX_CHECKPOINT_BYTES) if checkpoint is not None else None
        with self._connection(tenant, write=True) as conn:
            row = self._job(conn, job_id, tenant, user)
            # Cancellation is terminal even if an in-flight collector writes late.
            if row["status"] == "cancelled":
                return self._public(row)
            if row["status"] == "failed" and status == "running":
                other = conn.execute("SELECT id FROM pm_jobs WHERE tenant=? AND username=? AND status IN ('running','paused','waiting_login') AND id<>?", (tenant, user, job_id)).fetchone()
                if other is not None:
                    raise HTTPException(409, "Outra pesquisa ja esta ativa para este usuario.")
            elif row["status"] in TERMINAL_STATUSES:
                if status is None or status == row["status"]:
                    return self._public(row)
                raise HTTPException(409, "Pesquisa encerrada. Inicie uma nova execucao.")
            if checkpoint is not None:
                previous_checkpoint = json.loads(row["checkpoint"])
                original_scope = previous_checkpoint.get("scope")
                if "scope" in checkpoint and checkpoint["scope"] != original_scope:
                    raise HTTPException(422, "O escopo da pesquisa nao pode ser alterado.")
                if original_scope is not None:
                    checkpoint["scope"] = original_scope
                else:
                    checkpoint.pop("scope", None)
                encoded_checkpoint = _json(checkpoint, MAX_CHECKPOINT_BYTES)
            next_counters = json.loads(row["counters"])
            if counters is not None:
                if not isinstance(counters, dict) or set(counters) - set(COUNTER_KEYS):
                    raise HTTPException(422, "Contadores de pesquisa invalidos.")
                for key, value in counters.items():
                    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**53 - 1:
                        raise HTTPException(422, "Contadores de pesquisa invalidos.")
                    # Publication counts are authoritative SQLite counts.
                    if key in {"categories", "pages"}:
                        next_counters[key] = value
            next_status = status or row["status"]
            next_message = _text(message, 600) if message is not None else row["message"]
            next_checkpoint = encoded_checkpoint if encoded_checkpoint is not None else row["checkpoint"]
            if discovery is not None:
                conn.execute("INSERT INTO pm_discovery(job_id,seed_key,category_key,status,item_id,url) VALUES(?,?,?,?,?,?) ON CONFLICT(job_id,seed_key) DO UPDATE SET category_key=excluded.category_key,status=excluded.status,item_id=excluded.item_id,url=excluded.url", discovery)
                next_counters["pending"] = self._pending(conn, job_id)
            conn.execute("UPDATE pm_jobs SET status=?,checkpoint=?,counters=?,message=?,revision=revision+1,updated_at=? WHERE id=?", (next_status, next_checkpoint, _json(next_counters, 1024), next_message, _now(), job_id))
            return self._public(self._job(conn, job_id, tenant, user))

    @staticmethod
    def _seed_key(value: Any) -> str:
        if not isinstance(value, str) or not value or len(value) > 4000 or _text(value, 4000) != value:
            raise HTTPException(422, "Identificador de descoberta invalido.")
        if value.lower().startswith("url:") and not _ml_url(value[4:]):
            raise HTTPException(422, "Link de descoberta invalido.")
        return value

    def discovery(self, job_id: str, seed_key: str, client_id: str, username: str) -> dict:
        tenant, user = self._owner(client_id, username)
        key = self._seed_key(seed_key)
        with self._connection(tenant) as conn:
            self._job(conn, job_id, tenant, user)
            seen = conn.execute("SELECT 1 FROM pm_discovery WHERE job_id=? AND seed_key=?", (job_id, key)).fetchone() is not None
            return {"seen": seen}

    def publish(self, job_id: str, seller_key: str, client_id: str, username: str, seller: dict, anuncios: list[dict]) -> dict:
        tenant, user = self._owner(client_id, username)
        key = _text(seller_key, 500)
        if not key or key != str(seller_key) or str(seller.get("key") or key) != key:
            raise HTTPException(422, "Identificador de vendedor invalido.")
        normalized_seller = {
            "key": key, "id": _text(seller.get("id"), 80), "nome": _text(seller.get("nome"), 200),
            "perfilUrl": _ml_url(seller.get("perfilUrl")), "categoria": _text(seller.get("categoria"), 400),
        }
        if not normalized_seller["nome"]:
            normalized_seller["nome"] = normalized_seller["id"] or "Vendedor nao identificado"
        unique_ads: dict[str, dict] = {}
        for index, raw in enumerate(anuncios):
            ad_key, ad = _normalize_ad(raw, normalized_seller, index)
            unique_ads.setdefault(ad_key, ad)
        publication = _json({"seller": normalized_seller, "anuncios": list(unique_ads.values())}, MAX_PUBLICATION_BYTES)
        digest = hashlib.sha256(publication.encode("utf-8")).hexdigest()
        with self._connection(tenant, write=True) as conn:
            row = self._job(conn, job_id, tenant, user)
            if row["status"] not in ACTIVE_STATUSES:
                raise HTTPException(409, "Pesquisa encerrada. Coleta tardia descartada.")
            previous = conn.execute("SELECT digest FROM pm_sellers WHERE job_id=? AND seller_key=?", (job_id, key)).fetchone()
            if previous is not None and previous["digest"] == digest:
                return self._public(row)
            orders = {item["ad_key"]: item["collection_order"] for item in conn.execute("SELECT ad_key,collection_order FROM pm_ads WHERE job_id=? AND seller_key=?", (job_id, key))}
            next_order = row["next_order"]
            conn.execute("INSERT INTO pm_sellers(job_id,seller_key,payload,digest) VALUES(?,?,?,?) ON CONFLICT(job_id,seller_key) DO UPDATE SET payload=excluded.payload,digest=excluded.digest", (job_id, key, _json(normalized_seller, 10000), digest))
            conn.execute("DELETE FROM pm_ads WHERE job_id=? AND seller_key=?", (job_id, key))
            for ad_key, ad in unique_ads.items():
                order = orders.get(ad_key)
                if order is None:
                    order = next_order
                    next_order += 1
                ad["ordem_coleta"] = order
                ad["posicao_original"] = ad["posicao"]
                search = " ".join((ad["titulo"], ad["vendedor"], ad["id"])).casefold()
                conn.execute("INSERT INTO pm_ads VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (job_id, key, ad_key, _json(ad, 30000), ad["categoria"], search, int(ad["rankable"]), ad["media_vendas_mensal"], ad["vendas"], ad["posicao"], order, _ranking_key(ad)))
            ads_count = conn.execute("SELECT COUNT(*) FROM pm_ads WHERE job_id=?", (job_id,)).fetchone()[0]
            counters = json.loads(row["counters"])
            counters.update(ads=ads_count, pending=self._pending(conn, job_id), sellers=conn.execute("SELECT COUNT(*) FROM pm_sellers WHERE job_id=?", (job_id,)).fetchone()[0])
            conn.execute("UPDATE pm_jobs SET counters=?,next_order=?,revision=revision+1,updated_at=? WHERE id=?", (_json(counters, 1024), next_order, _now(), job_id))
            return self._public(self._job(conn, job_id, tenant, user))

    def ranking(self, job_id: str, client_id: str, username: str, *, offset=0, limit=100, q="", category="", seller_key="") -> dict:
        tenant, user = self._owner(client_id, username)
        clauses = ["job_id=?"]
        args: list[Any] = [job_id]
        if seller_key:
            clauses.append("seller_key=?")
            args.append(seller_key)
        if category:
            clauses.append("category=?")
            args.append(category)
        if q:
            clauses.append("instr(search_text,?)>0")
            args.append(q.casefold())
        where = " AND ".join(clauses)
        with self._connection(tenant) as conn:
            job = self._job(conn, job_id, tenant, user)
            total = conn.execute("SELECT COUNT(*) FROM pm_ads WHERE " + where, args).fetchone()[0]
            rows = conn.execute("SELECT payload FROM pm_ads WHERE " + where + " ORDER BY ranking_key COLLATE PM_RANKING LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
            items = []
            for index, row in enumerate(rows):
                ad = json.loads(row["payload"])
                ad["rank"] = offset + index + 1 if ad["rankable"] else None
                items.append(ad)
            return {"items": items, "total": total, "revision": job["revision"]}

    def sellers(self, job_id: str, client_id: str, username: str, *, seller_key: str = "") -> dict:
        tenant, user = self._owner(client_id, username)
        where = "s.job_id=?"
        args = [job_id]
        if seller_key:
            where += " AND s.seller_key=?"
            args.append(seller_key)
        with self._connection(tenant) as conn:
            self._job(conn, job_id, tenant, user)
            rows = conn.execute("SELECT s.seller_key,s.payload,COUNT(a.ad_key) AS ads,COALESCE(SUM(CASE WHEN a.rankable=0 THEN 1 ELSE 0 END),0) AS pending FROM pm_sellers s LEFT JOIN pm_ads a ON a.job_id=s.job_id AND a.seller_key=s.seller_key WHERE " + where + " GROUP BY s.seller_key,s.payload ORDER BY s.rowid", args).fetchall()
            return {"items": [{**json.loads(row["payload"]), "seller_key": row["seller_key"], "vendedor": json.loads(row["payload"])["nome"], "ads": row["ads"], "pending": row["pending"], "status": "completed"} for row in rows]}

    def categories(self, job_id: str, client_id: str, username: str) -> dict:
        tenant, user = self._owner(client_id, username)
        with self._connection(tenant) as conn:
            job = self._job(conn, job_id, tenant, user)
            categories = {row[0] for row in conn.execute("SELECT DISTINCT category FROM pm_ads WHERE job_id=? AND category<>''", (job_id,))}
            checkpoint = json.loads(job["checkpoint"])
            incomplete = set()
            for category in checkpoint.get("incompleteCategories") or []:
                name = _text(category.get("nome") or category.get("categoria") or category.get("key"), 400) if isinstance(category, dict) else _text(category, 400)
                if name:
                    categories.add(name)
                    incomplete.add(name)
            if job["status"] in {"paused", "waiting_login", "cancelled", "failed"}:
                current = checkpoint.get("currentCategory")
                queue = checkpoint.get("categories")
                index = checkpoint.get("categoryIndex", 0)
                open_category = False
                if isinstance(current, dict):
                    if isinstance(queue, list) and isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(queue):
                        queued = queue[index]
                        if isinstance(queued, dict):
                            current_key, queued_key = current.get("key"), queued.get("key")
                            if current_key and queued_key:
                                open_category = current_key == queued_key
                            else:
                                open_category = bool(current.get("nome") and current.get("nome") == queued.get("nome"))
                    elif not isinstance(queue, list):
                        open_category = isinstance(checkpoint.get("page"), dict)
                    if open_category:
                        name = _text(current.get("nome") or current.get("categoria") or current.get("key"), 400)
                        if name:
                            categories.add(name)
                            incomplete.add(name)
            return {"items": [{"categoria": category, "status": "incomplete" if category in incomplete else "completed"} for category in sorted(categories, key=str.casefold)]}


def configure_pesquisa_mercado_store(info_root: str | Path) -> PesquisaMercadoStore:
    global _STORE
    _STORE = PesquisaMercadoStore(info_root)
    return _STORE


def get_pesquisa_mercado_store() -> PesquisaMercadoStore:
    if _STORE is None:
        raise HTTPException(503, "Pesquisa de Mercado indisponivel.")
    return _STORE
