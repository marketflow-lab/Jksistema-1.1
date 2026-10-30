"""Read-only physical-unit projections from the tenant's existing Cadastro.

Sales and returns remain their original commercial documents. A projection
keeps the money on the sold kit and attributes only units to its leaf SKUs.
Nothing in this module collects products, writes Cadastro, or persists sales.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
import hashlib
import json
import math
import os
import re
import unicodedata
from typing import Any

from backend.services.medias_compras_leitura import consulta_medias
from backend.services.retired_store_names import (
    TOMBSTONES_FILE, RetiredStoreNames, parse_retired_store_names,
)
from .performance import cache_vendas_response, mark_vendas_read_incomplete


def _sku(value: Any) -> str:
    from backend.services.cadastro_common import _normalizar_sku_mes

    return _normalizar_sku_mes(str(value or "").strip())


def _key(value: Any) -> str:
    return _sku(value).casefold()


def _name_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _all_stores(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"", "__todas", "todas", "todas as lojas"}


def _product_name(product: Mapping[str, Any]) -> str:
    for field in ("produto", "produto_bling", "nome", "nome_bling", "titulo_ml"):
        value = str(product.get(field) or "").strip()
        if value:
            return value
    return _sku(product.get("sku"))


def _decimal(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise _InvalidComposition("invalid_quantity")
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise _InvalidComposition("invalid_quantity") from None
    if not result.is_finite():
        raise _InvalidComposition("invalid_quantity")
    return result


def _number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    result = float(value)
    if not math.isfinite(result):
        raise _InvalidComposition("quantity_out_of_range")
    return result


def _product_id(value: Any) -> str:
    try:
        parsed = _decimal(value)
    except _InvalidComposition:
        return ""
    if parsed <= 0 or parsed != parsed.to_integral_value():
        return ""
    return str(int(parsed))


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value, parse_float=Decimal)
    except (ValueError, TypeError):
        raise _InvalidComposition("invalid_components_json") from None


@dataclass(frozen=True)
class Component:
    sku: str
    produto: str
    fator: Decimal


class _InvalidComposition(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class CompositionCatalog:
    """One request's immutable product/store snapshot and resolved leaf index."""

    def __init__(
        self,
        products: Iterable[Mapping[str, Any]] = (),
        *,
        stores: Iterable[Mapping[str, Any]] | None = None,
        _verified_legacy_ids: frozenset[tuple[str, str]] = frozenset(),
        _verified_legacy_compositions: frozenset[tuple[str, str]] = frozenset(),
        _require_field_proof: bool = False,
        _retired_names: RetiredStoreNames | None = None,
    ) -> None:
        records = [dict(product) for product in products if isinstance(product, Mapping)]
        if stores is None:
            inferred = {}
            for product in records:
                store_id = str(product.get("store_id") or "").strip()
                if store_id:
                    inferred.setdefault(store_id, {
                        "store_id": store_id, "nome": product.get("loja_sync") or store_id,
                    })
            stores = inferred.values()
        store_rows = [dict(store) for store in stores if isinstance(store, Mapping)]
        counts = Counter(str(store.get("store_id") or "").strip() for store in store_rows)
        self._known_store_ids = set(counts) - {""}
        self._retired_names = _retired_names or RetiredStoreNames()
        self._stores: dict[str, dict[str, Any]] = {}
        self._aliases: dict[str, set[str]] = defaultdict(set)
        self._products: dict[tuple[str, str], dict[str, Any]] = {}
        self._ambiguous: set[tuple[str, str]] = set()
        self._ids: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._resolved: dict[tuple[str, str], tuple[Component, ...] | None] = {}
        self._walk_cache: dict[tuple[str, str], tuple[Component, ...]] = {}
        self._warnings: dict[tuple[str, str, str], dict[str, str]] = {}
        self._composed: set[tuple[str, str]] = set()
        self._unverified_legacy_ids: set[tuple[str, str]] = set()
        self._unverified_legacy_compositions: set[tuple[str, str]] = set()
        for store in store_rows:
            store_id = str(store.get("store_id") or "").strip()
            if not store_id:
                continue
            if counts[store_id] != 1:
                self._warn("ambiguous_store", store_id)
                continue
            self._stores[store_id] = {
                "store_id": store_id, "nome": str(store.get("nome") or "").strip(),
            }
            previous = store.get("nomes_anteriores") or ()
            previous = [previous] if isinstance(previous, str) else previous
            if not isinstance(previous, (list, tuple, set)):
                previous = ()
            for alias in [store.get("nome"), *previous]:
                if _name_key(alias):
                    self._aliases[_name_key(alias)].add(store_id)
        for product in records:
            store_id = str(product.get("store_id") or "").strip()
            sku = _sku(product.get("sku") or product.get("sku_normalizado"))
            if store_id not in self._stores or not sku:
                continue
            if str(product.get("deleted_at_utc") or "").strip():
                continue
            identity = (store_id, _key(sku))
            if identity in self._products:
                self._ambiguous.add(identity)
                self._warn("ambiguous_product", store_id, sku)
            # Store only the fields necessary for composition; no credentials or
            # unrelated product metadata become part of a projection.
            self._products[identity] = {
                "sku": sku,
                "produto": _product_name(product),
                "id_bling": product.get("id_bling"),
                "formato_bling": product.get("formato_bling"),
                "componentes_bling_json": product.get("componentes_bling_json"),
                "estrutura_bling": product.get("estrutura_bling"),
            }
            product_id = _product_id(product.get("id_bling"))
            if product_id:
                self._ids[(store_id, product_id)].add(identity[1])
            if self._declares_composition(product):
                self._composed.add(identity)
            if _require_field_proof or product.get("scope_source") == "legacy_shadow":
                if identity not in _verified_legacy_ids:
                    self._unverified_legacy_ids.add(identity)
                    if product.get("scope_source") == "legacy_shadow" and not self._declares_composition(product) and not str(
                        product.get("formato_bling") or ""
                    ).strip():
                        self._warn("legacy_catalog_unverified", store_id)
                if identity not in _verified_legacy_compositions:
                    self._unverified_legacy_compositions.add(identity)
        # Resolve once so warnings are available even before a report projects.
        for identity in sorted(self._composed):
            self._expansion(*identity)

    @classmethod
    def from_products(
        cls,
        products: Iterable[Mapping[str, Any]],
        *,
        stores: Iterable[Mapping[str, Any]] | None = None,
        _verified_legacy_ids: frozenset[tuple[str, str]] = frozenset(),
        _verified_legacy_compositions: frozenset[tuple[str, str]] = frozenset(),
        _require_field_proof: bool = False,
        _retired_names: RetiredStoreNames | None = None,
    ) -> "CompositionCatalog":
        return cls(
            products, stores=stores, _verified_legacy_ids=_verified_legacy_ids,
            _verified_legacy_compositions=_verified_legacy_compositions,
            _require_field_proof=_require_field_proof,
            _retired_names=_retired_names,
        )

    @staticmethod
    def _declares_composition(product: Mapping[str, Any]) -> bool:
        if str(product.get("formato_bling") or "").strip().upper() == "E":
            return True
        primary = product.get("componentes_bling_json")
        if primary not in (None, ""):
            try:
                primary = _json_value(primary)
            except _InvalidComposition:
                return True
            return not isinstance(primary, list) or bool(primary)
        structure = product.get("estrutura_bling")
        if structure in (None, ""):
            return False
        try:
            structure = _json_value(structure)
        except _InvalidComposition:
            return True
        return not isinstance(structure, dict) or (
            "componentes" in structure
            and (not isinstance(structure["componentes"], list) or bool(structure["componentes"]))
        )

    def _warn(self, code: str, store_id: str = "", sku: str = "") -> None:
        self._warnings.setdefault((code, store_id, sku), {
            "code": code, "store_id": store_id, "sku": sku,
        })

    @property
    def warnings(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._warnings.values()]

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "catalog_products": len(self._products),
            "composed_products": len(self._composed),
            "incomplete": bool(self._warnings),
            "read_complete": getattr(self, "_read_complete", True) and not any(
                item["code"] in {"catalog_unavailable", "store_catalog_unavailable"}
                for item in self._warnings.values()
            ),
            "warnings": self.warnings,
        }

    def _store_id(self, store: Any) -> str:
        reference = str(store or "").strip()
        if reference in self._stores:
            return reference
        if reference in self._known_store_ids:
            return ""
        if self._retired_names.blocks(reference, _name_key):
            return ""
        candidates = self._aliases.get(_name_key(reference), set())
        return next(iter(candidates)) if len(candidates) == 1 else ""

    def _row_store(self, row: Mapping[str, Any], store: Any) -> str:
        # A supplied stable ID never falls back to a name or another store.
        stable_id = str(row.get("store_id") or "").strip()
        if stable_id:
            return stable_id if stable_id in self._stores else ""
        reference = row.get("loja_conta") or row.get("loja") or store
        return self._store_id(reference)

    def _components(self, product: Mapping[str, Any]) -> list[Any] | None:
        raw = product.get("componentes_bling_json")
        if raw in (None, ""):
            structure = product.get("estrutura_bling")
            if structure not in (None, ""):
                structure = _json_value(structure)
                if not isinstance(structure, dict):
                    raise _InvalidComposition("invalid_structure")
                raw = structure.get("componentes")
        if raw in (None, ""):
            if str(product.get("formato_bling") or "").strip().upper() == "E":
                raise _InvalidComposition("missing_components")
            return None
        components = _json_value(raw)
        if not isinstance(components, list):
            raise _InvalidComposition("invalid_components")
        if not components:
            if str(product.get("formato_bling") or "").strip().upper() == "E":
                raise _InvalidComposition("missing_components")
            return None
        return components

    def _component_key(self, store_id: str, component: Any) -> tuple[str, Decimal]:
        if not isinstance(component, dict) or not isinstance(component.get("produto"), dict):
            raise _InvalidComposition("invalid_component")
        factor = _decimal(component.get("quantidade"))
        if factor <= 0:
            raise _InvalidComposition("invalid_quantity")
        product = component["produto"]
        reference_id = _product_id(product.get("id"))
        explicit_sku = _key(product.get("codigo") or product.get("sku"))
        if product.get("id") not in (None, "") and not reference_id:
            raise _InvalidComposition("invalid_component_id")
        if reference_id:
            candidates = self._ids.get((store_id, reference_id), set())
            if not candidates:
                raise _InvalidComposition("missing_component")
            if len(candidates) != 1:
                raise _InvalidComposition("ambiguous_component_id")
            sku_key = next(iter(candidates))
            if explicit_sku and explicit_sku != sku_key:
                raise _InvalidComposition("conflicting_component_reference")
            if (store_id, sku_key) in self._unverified_legacy_ids:
                raise _InvalidComposition("legacy_component_identity_unverified")
        elif explicit_sku:
            sku_key = explicit_sku
        else:
            raise _InvalidComposition("missing_component_reference")
        if (store_id, sku_key) not in self._products:
            raise _InvalidComposition("missing_component")
        return sku_key, factor

    def _walk(
        self, store_id: str, sku_key: str, path: frozenset[str],
    ) -> tuple[Component, ...]:
        identity = (store_id, sku_key)
        if sku_key in path:
            raise _InvalidComposition("composition_cycle")
        if len(path) >= 64:
            raise _InvalidComposition("composition_depth_exceeded")
        if identity in self._ambiguous:
            raise _InvalidComposition("ambiguous_product")
        if identity in self._walk_cache:
            return self._walk_cache[identity]
        product = self._products.get(identity)
        if product is None:
            raise _InvalidComposition("missing_component")
        components = self._components(product)
        if components is None:
            return (Component(product["sku"], product["produto"], Decimal(1)),)
        if identity in self._unverified_legacy_compositions:
            raise _InvalidComposition("legacy_composition_unverified")
        totals: dict[str, Component] = {}
        for component in components:
            child, factor = self._component_key(store_id, component)
            for leaf in self._walk(store_id, child, path | {sku_key}):
                key = _key(leaf.sku)
                with localcontext() as context:
                    context.prec = 64
                    quantity = leaf.fator * factor
                    if key in totals:
                        quantity += totals[key].fator
                if not quantity.is_finite():
                    raise _InvalidComposition("invalid_quantity")
                totals[key] = Component(leaf.sku, leaf.produto, quantity)
        result = tuple(totals[key] for key in sorted(totals))
        self._walk_cache[identity] = result
        return result

    def _expansion(self, store_id: str, sku_key: str) -> tuple[Component, ...] | None:
        identity = (store_id, sku_key)
        if identity not in self._composed:
            return None
        if identity not in self._resolved:
            try:
                self._resolved[identity] = self._walk(store_id, sku_key, frozenset())
            except (DecimalException, OverflowError):
                self._resolved[identity] = None
                self._warn("invalid_quantity", store_id, self._products[identity]["sku"])
            except _InvalidComposition as exc:
                self._resolved[identity] = None
                self._warn(exc.code, store_id, self._products[identity]["sku"])
        return self._resolved[identity]

    def resolve(self, sku: Any, store: Any = None) -> tuple[Component, ...]:
        display = _sku(sku)
        store_id = self._store_id(store)
        expansion = self._expansion(store_id, _key(display)) if store_id else None
        if expansion is not None:
            return expansion
        product = self._products.get((store_id, _key(display)), {})
        return (Component(display, _product_name(product) or display, Decimal(1)),)

    def candidates(self, target_sku: Any, store: Any = None) -> set[str]:
        """Sold SKU filters, before SQL/dedup, for a component's physical view."""
        display = _sku(target_sku)
        target = _key(display)
        result = {str(target_sku or "").strip(), display} - {""}
        stores = list(self._stores) if _all_stores(store) else [self._store_id(store)]
        for store_id in stores:
            if not store_id:
                continue
            own = self._products.get((store_id, target))
            if own:
                result.add(own["sku"])
            # A sold kit's own view remains commercial, including invalid kits.
            if (store_id, target) in self._composed:
                continue
            for identity in self._composed:
                if identity[0] != store_id:
                    continue
                expansion = self._expansion(*identity)
                if expansion and any(_key(leaf.sku) == target for leaf in expansion):
                    result.add(self._products[identity]["sku"])
        return result

    @staticmethod
    def _synthetic_id(row: Mapping[str, Any], store_id: str, leaf: Component) -> str:
        original = row.get("id_unico") or row.get("id") or [
            row.get(field) for field in
            (
                "origem_tipo", "origem_id", "item_id", "numero_nf", "data",
                "numero_nota", "origem_codigo", "data_emissao", "id_nota",
                "sku", "quantidade",
            )
        ]
        material = json.dumps(
            [store_id, original, _key(leaf.sku)], ensure_ascii=True,
            sort_keys=True, default=str, separators=(",", ":"),
        )
        return "composicao:" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    def project_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        target_sku: Any = None,
        store: Any = None,
    ) -> list[dict[str, Any]]:
        """Project already deduplicated/eligible rows without mutating input."""
        target = _key(target_sku)
        output: list[dict[str, Any]] = []
        for source in rows:
            row = dict(source)
            sku_key = _key(row.get("sku"))
            # Projected rows are already a physical view; keep them once.
            if row.get("__composicao") or row.get("__tipo_composicao") == "kit":
                if not target or sku_key == target:
                    if target and row.get("__tipo_composicao") == "kit":
                        row["quantidade"] = row.get("quantidade_original", row.get("quantidade"))
                    output.append(row)
                continue
            row.setdefault("pedidos_contribuicao", 1)
            row.setdefault("devolucoes_contribuicao", 1)
            store_id = self._row_store(row, store)
            if not store_id and any(key == sku_key for _, key in self._composed):
                self._warn("unresolved_sale_store", "", _sku(row.get("sku")))
            expansion = self._expansion(store_id, sku_key) if store_id else None
            if not expansion or (target and sku_key == target):
                if not target or sku_key == target:
                    output.append(row)
                continue
            if target and (store_id, target) in self._composed:
                continue
            selected = [leaf for leaf in expansion if not target or _key(leaf.sku) == target]
            if not selected:
                continue
            try:
                quantity = _decimal(row.get("quantidade"))
                with localcontext() as context:
                    context.prec = 64
                    projected = [
                        (leaf, _number(quantity * leaf.fator), _number(leaf.fator))
                        for leaf in selected
                    ]
            except (_InvalidComposition, DecimalException, OverflowError):
                self._warn("invalid_sale_quantity", store_id, _sku(row.get("sku")))
                if not target or sku_key == target:
                    output.append(row)
                continue
            if not target:
                parent = dict(row)
                parent.update(
                    quantidade=0, quantidade_original=row.get("quantidade"),
                    __tipo_composicao="kit",
                )
                output.append(parent)
            for leaf, units, factor in projected:
                component = dict(row)
                component.update(
                    sku=leaf.sku, produto=leaf.produto, quantidade=units,
                    quantidade_original=row.get("quantidade"),
                    sku_original=row.get("sku"), produto_original=row.get("produto"),
                    fator_composicao=factor, valor=0, valor_total=0, valor_unitario=0,
                    pedidos_contribuicao=0, devolucoes_contribuicao=0, __composicao=True,
                    __tipo_composicao="componente",
                    id_unico_original=row.get("id_unico") or row.get("id"),
                    id_unico=self._synthetic_id(row, store_id, leaf),
                    store_id=store_id,
                )
                output.append(component)
        return output


def composition_source_paths(client_id: str) -> tuple[str, ...]:
    """Files affecting this projection's cache signature; no integration calls."""
    from backend.services import cadastro_lojas_produtos as catalog

    try:
        tenant = os.fspath(catalog.get_tenant_path(client_id))
    except (RuntimeError, TypeError):
        return ()
    names = (
        catalog.CADASTRO_PRODUTOS_LOJAS_ARQUIVO,
        *catalog._ARQUIVOS_LEGADOS.values(),
        "lojas_config.json",
        "lojas_config.json.bak",
        TOMBSTONES_FILE,
    )
    return tuple(os.path.join(tenant, name) for name in dict.fromkeys(names))


def _store_field_proofs(products, sources, catalog, canonical_records):
    """Only exact, persisted store IDs can prove account-specific Bling fields.

    Cadastro's legacy shadow may inherit a generic row by SKU. That association
    alone cannot prove the account owning a Bling ID or a bill of materials.
    """
    projected = {
        (str(product.get("store_id") or "").strip(), _key(product.get("sku"))): product
        for product in products
    }

    def components(product):
        raw = product.get("componentes_bling_json")
        if raw not in (None, ""):
            return _json_value(raw)
        raw = product.get("estrutura_bling")
        if raw not in (None, ""):
            structure = _json_value(raw)
            return structure.get("componentes") if isinstance(structure, dict) else None
        return None

    def evidence_for(records):
        evidence: dict[tuple[str, str], dict[str, list[Any]]] = defaultdict(
            lambda: {"id_bling": [], "components": []},
        )
        for record in records:
            identity = (str(record.get("store_id") or "").strip(), _key(record.get("sku")))
            if not identity[0] or identity not in projected or record.get("deleted_at_utc"):
                continue
            raw_id = record.get("id_bling")
            if raw_id not in (None, ""):
                evidence[identity]["id_bling"].append(_product_id(raw_id))
            try:
                values = components(record)
            except _InvalidComposition:
                values = "__invalid_components__"
            if values is not None:
                evidence[identity]["components"].append(values)
        return evidence

    legacy_records = []
    for filename in catalog._ARQUIVOS_LEGADOS.values():
        raw = sources.get(filename, b"")
        records, _ = catalog._ler_csv_capturado(raw)
        legacy_records.extend(records)
    canonical = evidence_for(canonical_records)
    legacy = evidence_for(legacy_records)
    verified_ids = set()
    verified_compositions = set()
    for identity, product in projected.items():
        # Enrichment can fill a blank field even on a store_file row. Verify
        # each field independently, with the canonical value taking precedence.
        canonical_fields = canonical.get(identity, {})
        legacy_fields = legacy.get(identity, {})
        product_id = _product_id(product.get("id_bling"))
        observed_ids = canonical_fields.get("id_bling") or legacy_fields.get("id_bling")
        if product_id and observed_ids and all(value == product_id for value in observed_ids):
            verified_ids.add(identity)
        try:
            expected = components(product)
        except _InvalidComposition:
            continue
        observed_components = canonical_fields.get("components") or legacy_fields.get("components")
        if isinstance(expected, list) and observed_components and all(
            value == expected for value in observed_components
        ):
            verified_compositions.add(identity)
    return frozenset(verified_ids), frozenset(verified_compositions)


@cache_vendas_response(
    "composition_catalog", lambda args: composition_source_paths(args["client_id"]),
    cache_if=lambda catalog: catalog.metadata.get("read_complete") is not False and not any(warning["code"] in {
        "catalog_unavailable", "store_catalog_unavailable",
    } for warning in catalog.warnings),
)
@consulta_medias
def load_composition_catalog(client_id: str) -> CompositionCatalog:
    """Reuse Cadastro's coherent local snapshot and legacy store safeguards."""
    from backend.services import cadastro_compatibilidade as compatibility
    from backend.services import cadastro_lojas_produtos as catalog

    try:
        records, stores, sources, photos = compatibility._capturar_cadastro_para_leitura(client_id)
    except Exception:
        result = CompositionCatalog()
        result._warn("catalog_unavailable")
        return result
    products: list[dict[str, Any]] = []
    failures: list[str] = []
    counts = Counter(str(store.get("store_id") or "").strip() for store in stores)
    with compatibility._cadastro_fotos_contexto_leitura(photos):
        for store in stores:
            store_id = str(store.get("store_id") or "").strip()
            if not store_id or counts[store_id] != 1:
                continue
            try:
                products.extend(catalog._listar_produtos_loja_sync(
                    client_id, store_id, _registros_persistidos=records,
                    _lojas_capturadas=stores, _fontes_capturadas=sources,
                    _incluir_fotos=False,
                ))
            except Exception:
                failures.append(store_id)
    proof_complete = True
    try:
        legacy_ids, legacy_compositions = _store_field_proofs(products, sources, catalog, records)
    except Exception:
        # Failure to prove legacy origin cannot disable canonical store rows.
        mark_vendas_read_incomplete()
        proof_complete = False
        legacy_ids, legacy_compositions = frozenset(), frozenset()
    result = CompositionCatalog.from_products(
        products, stores=stores, _verified_legacy_ids=legacy_ids,
        _verified_legacy_compositions=legacy_compositions, _require_field_proof=True,
        _retired_names=parse_retired_store_names(sources.get(TOMBSTONES_FILE, b"[]")),
    )
    result._read_complete = proof_complete
    for store_id in failures:
        result._warn("store_catalog_unavailable", store_id)
    return result
