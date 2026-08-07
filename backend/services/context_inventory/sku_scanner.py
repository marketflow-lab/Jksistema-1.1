"""Scan canonical tenant SKU dossiers into safe semantic inventory entities."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import (
    Any,
    Mapping,
)

from backend.services.context_hub_sku_taxonomy import (
    PRODUCT_CATEGORY_TAXONOMY_SOURCE_REF,
    PRODUCT_CATEGORY_TAXONOMY_VERSION,
    classify_sku_product,
    taxonomy_nodes,
    taxonomy_tree_lines,
)
from backend.services.context_hub_sku_vehicle_tree import (
    VEHICLE_TREE_TRUTH_CLASS,
    VEHICLE_YEAR_TREE_SOURCE_REF,
    VEHICLE_YEAR_TREE_VERSION,
    build_vehicle_year_tree,
    normalize_sku_vehicle_years,
    vehicle_model_id,
)

from .contracts import (
    SKU_SCHEMA_VERSION,
    _SKU_REQUIRED_KEYS,
)
from .entities import (
    _entity,
    _finding,
)
from .normalization import (
    _normal_key,
    _sha256_bytes,
    _sha256_value,
    _slug,
)
from .security import (
    _read_bytes,
    _read_text,
    _safe_resolve,
    _safe_summary,
)
from .sku_content import (
    _contains_forbidden_sku_key,
    _contains_sensitive_sku_value,
    build_sku_content,
    _sku_family,
    _sku_field,
    _sku_items,
    _sku_section,
    _sku_top_keys,
)


def _sku_dir(info_root: Path, client_id: str) -> Path:
    if info_root.name == client_id and (info_root / "SKU").exists():
        return info_root / "SKU"
    return info_root / client_id / "SKU"


def _empty_stats() -> dict[str, Any]:
    return {
        "files": 0,
        "valid": 0,
        "invalid": 0,
        "pending": 0,
        "excluded": 0,
        "families": 0,
        "categories": 0,
        "classified_categories": 0,
        "pending_category_review": 0,
        "vehicle_year_tree": {
            "tree_version": VEHICLE_YEAR_TREE_VERSION,
            "truth_class": VEHICLE_TREE_TRUTH_CLASS,
            "records": 0,
            "pending": 0,
            "overrides_applied": 0,
            "brands": 0,
            "models": 0,
            "years": 0,
            "distinct_years": 0,
            "pages": 0,
            "max_degree": 0,
        },
    }


class _SkuInventoryScanner:
    def __init__(
        self,
        info_root: Path,
        client_id: str,
        surface: str,
        findings: list[dict[str, str]],
    ) -> None:
        self.info_root = info_root
        self.client_id = client_id
        self.surface = surface
        self.findings = findings
        self.stats = _empty_stats()
        self.entities: list[dict[str, Any]] = []
        self.families: dict[str, list[str]] = defaultdict(list)
        self.category_members: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.vehicle_records: list[dict[str, Any]] = []
        self.vehicle_pending: list[dict[str, Any]] = []
        self.vehicle_overrides = 0
        self.names: list[tuple[str, str]] = []
        self.index: dict[str, Any] = {}
        self.index_ref = f"info/{client_id}/SKU/_INDICE.json"
        self.index_path = Path()
        self.safe_sku = Path()
        self.files: list[Path] = []

    def _resolve_catalog(self) -> bool:
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.client_id or "")
            or self.client_id in {".", ".."}
        ):
            self.findings.append(
                _finding("invalid_client_id", "blocker", "info", "Identificador de tenant invalido.")
            )
            return False
        try:
            info_resolved = self.info_root.resolve(strict=True)
        except OSError:
            self.findings.append(
                _finding("info_root_missing", "blocker", "info", "Raiz info nao encontrada.")
            )
            return False
        sku_path = _sku_dir(info_resolved, self.client_id)
        safe_sku = _safe_resolve(sku_path, info_resolved)
        if safe_sku is None or not safe_sku.is_dir():
            self.findings.append(
                _finding(
                    "sku_root_missing",
                    "blocker",
                    f"info/{self.client_id}/SKU",
                    "Catalogo SKU nao encontrado.",
                )
            )
            return False
        self.safe_sku = safe_sku
        self.index_path = safe_sku / "_INDICE.json"
        self.files = sorted(
            path for path in safe_sku.glob("*.json") if path.name != "_INDICE.json"
        )
        self.stats["files"] = len(self.files)
        return True

    def _load_index(self) -> None:
        if not self.index_path.is_file() or self.index_path.is_symlink():
            self.findings.append(
                _finding("sku_index_missing", "blocker", self.index_ref, "Indice canonico SKU ausente.")
            )
            return
        try:
            loaded = json.loads(_read_text(self.index_path))
            self.index = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            self.findings.append(
                _finding("sku_index_invalid", "blocker", self.index_ref, "Indice canonico SKU invalido.")
            )

    def _reject(self, code: str, ref: str, message: str, *, pending: bool = False) -> None:
        self.findings.append(_finding(code, "blocker", ref, message))
        if pending:
            self.stats["pending"] += 1
        self.stats["invalid"] += 1

    def _load_dossier(self, path: Path) -> tuple[dict[str, Any], str, str, str] | None:
        ref = f"info/{self.client_id}/SKU/{path.name}"
        if path.is_symlink() or _safe_resolve(path, self.safe_sku) is None:
            self._reject("unsafe_sku_path", ref, "Dossie SKU fora da raiz canonica.")
            return None
        try:
            data = json.loads(_read_text(path))
        except (OSError, json.JSONDecodeError):
            self._reject("sku_json_invalid", ref, "Dossie SKU com JSON invalido.")
            return None
        if not isinstance(data, dict):
            self._reject("sku_schema_invalid", ref, "Dossie SKU nao e um objeto.")
            return None
        if _sku_top_keys(data) != _SKU_REQUIRED_KEYS or data.get("schema_version") != SKU_SCHEMA_VERSION:
            self._reject("sku_schema_invalid", ref, "Dossie SKU usa schema inesperado.")
            return None
        if _contains_forbidden_sku_key(data):
            self._reject("sku_forbidden_field", ref, "Dossie SKU contem campo nao permitido.")
            return None
        if _contains_sensitive_sku_value(data):
            self._reject("sku_sensitive_value", ref, "Dossie SKU contem dado sensivel nao permitido.")
            return None
        sku = str(data.get("sku") or "").strip()
        normalized_sku = _slug(sku, fallback="")
        if not normalized_sku or path.stem.casefold() != sku.casefold():
            self._reject("sku_identity_mismatch", ref, "Identidade do SKU diverge do nome do arquivo.")
            return None
        revision = _sku_section(data, "revisao")
        pending = _sku_field(revision, "pendencias", [])
        status = _normal_key(_sku_field(revision, "status", ""))
        if status != "revisado" or not isinstance(pending, list) or pending:
            self._reject("sku_pending_review", ref, "Dossie SKU possui revisao pendente.", pending=True)
            return None
        return data, sku, normalized_sku, ref

    def _bind_vehicles(
        self,
        data: Mapping[str, Any],
        sku: str,
        normalized_sku: str,
        ref: str,
        allowed: dict[str, Any],
        content: str,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[str], int]:
        application = _sku_section(data, "aplicacao")
        normalized = normalize_sku_vehicle_years(
            sku=sku,
            product_name=str(data.get("nome_produto") or ""),
            vehicle_items=_sku_items(_sku_field(application, "veiculos_compativeis", {})),
            source_ref=ref,
        )
        records = [
            dict(item) for item in normalized.get("records", []) if isinstance(item, Mapping)
        ]
        pending = [
            dict(item) for item in normalized.get("pending", []) if isinstance(item, Mapping)
        ]
        overrides = int(normalized.get("overrides_applied") or 0)
        self.vehicle_records.extend(records)
        self.vehicle_pending.extend(pending)
        self.vehicle_overrides += overrides
        model_ids = sorted(
            {
                vehicle_model_id(str(item.get("brand") or ""), str(item.get("model") or ""))
                for item in records
            }
        )
        allowed["vehicle_year_tree"] = {
            "tree_version": VEHICLE_YEAR_TREE_VERSION,
            "truth_class": VEHICLE_TREE_TRUTH_CLASS,
            "records": [
                {
                    "brand": item.get("brand"),
                    "model": item.get("model"),
                    "year": item.get("year"),
                    "status": item.get("status"),
                    "verified_through": item.get("verified_through"),
                    "restrictions": item.get("restrictions"),
                    "override_id": item.get("override_id"),
                }
                for item in records
            ],
            "pending_count": len(pending),
            "overrides_applied": overrides,
        }
        if records:
            content += f"\nRelações veículo-ano materializadas: {len(records)}"
        for pending_item in pending:
            self.findings.append(
                _finding(
                    "sku_vehicle_year_pending",
                    "warning",
                    ref,
                    "Aplicação de veículo não materializada na árvore: "
                    + str(pending_item.get("reason") or "motivo não informado"),
                    entity_id=f"jk:sku:{normalized_sku}",
                )
            )
        return content, records, pending, model_ids, overrides

    def _classify_product(
        self,
        data: Mapping[str, Any],
        sku: str,
        application_type: str,
        allowed: dict[str, Any],
        content: str,
    ) -> tuple[str, dict[str, Any], str, list[str], str]:
        classification = classify_sku_product(
            sku=sku,
            product_name=str(data.get("nome_produto") or ""),
            application_type=application_type,
            description=str(_sku_section(data, "o_que_e") or ""),
            uses=[
                str(item) for item in _sku_items(_sku_section(data, "para_que_serve"))
                if isinstance(item, str)
            ],
            characteristics=[
                str(item) for item in _sku_items(_sku_section(data, "caracteristicas_tecnicas"))
                if isinstance(item, str)
            ],
        )
        category_id = str(classification["category_id"])
        category_path = [str(item) for item in classification.get("path") or []]
        category_status = str(classification.get("status") or "pending_review")
        allowed["product_category"] = {
            "taxonomy_version": PRODUCT_CATEGORY_TAXONOMY_VERSION,
            "category_id": category_id,
            "status": category_status,
            "rule_id": str(classification.get("rule_id") or ""),
            "truth_class": "generated_secondary",
        }
        content += "\nCategoria derivada de produto: " + " > ".join(category_path)
        counter = "classified_categories" if category_status == "classified_by_rule" else "pending_category_review"
        self.stats[counter] += 1
        return content, dict(classification), category_id, category_path, category_status

    def _sku_entity(
        self,
        *,
        data: Mapping[str, Any],
        sku: str,
        normalized_sku: str,
        ref: str,
        family: str,
        application_type: str,
        allowed: Mapping[str, Any],
        content: str,
        classification: Mapping[str, Any],
        category_id: str,
        category_path: list[str],
        category_status: str,
        vehicle_records: list[dict[str, Any]],
        vehicle_pending: list[dict[str, Any]],
        vehicle_model_ids: list[str],
        vehicle_overrides: int,
    ) -> dict[str, Any]:
        return _entity(
            entity_id=f"jk:sku:{normalized_sku}",
            kind="sku",
            domain="cadastro",
            title=f"SKU {sku} - {data.get('nome_produto')}",
            surface=self.surface,
            tenant_scope="client",
            sensitivity="internal_catalog",
            truth_class="canonical",
            source_refs=[ref],
            source_hash=_sha256_value(allowed),
            relationships=[
                {"type": "member_of", "target_id": f"jk:sku-family:{_slug(family)}"},
                {"type": "classified_as", "target_id": category_id},
                *[
                    {"type": "indexed_by_vehicle_model", "target_id": model_id}
                    for model_id in vehicle_model_ids
                ],
            ],
            metadata={
                "sku": sku,
                "family": family,
                "application_type": application_type,
                "product_category_id": category_id,
                "product_category_path": category_path,
                "product_category_status": category_status,
                "product_category_rule": str(classification.get("rule_id") or ""),
                "product_category_truth_class": "generated_secondary",
                "product_category_taxonomy_version": PRODUCT_CATEGORY_TAXONOMY_VERSION,
                "vehicle_year_tree_version": VEHICLE_YEAR_TREE_VERSION,
                "vehicle_year_truth_class": VEHICLE_TREE_TRUTH_CLASS,
                "vehicle_year_record_count": len(vehicle_records),
                "vehicle_year_pending_count": len(vehicle_pending),
                "vehicle_year_overrides_applied": vehicle_overrides,
                "updated_at": str(data.get("atualizado_em") or ""),
                "render_individually": False,
            },
            content=content,
        )

    def _process_dossier(self, path: Path) -> None:
        loaded = self._load_dossier(path)
        if loaded is None:
            return
        data, sku, normalized_sku, ref = loaded
        application = _sku_section(data, "aplicacao")
        application_type = str(_sku_field(application, "tipo", "geral") or "geral")
        family = _sku_family(str(data.get("nome_produto") or ""), application_type)
        content, allowed = build_sku_content(data, sku, family)
        content, records, pending, model_ids, overrides = self._bind_vehicles(
            data, sku, normalized_sku, ref, allowed, content
        )
        content, classification, category_id, category_path, category_status = self._classify_product(
            data, sku, application_type, allowed, content
        )
        entity_id = f"jk:sku:{normalized_sku}"
        self.families[family].append(entity_id)
        self.names.append((sku, str(data.get("nome_produto") or "")))
        self.category_members[category_id].append(
            {
                "id": entity_id,
                "sku": sku,
                "title": str(data.get("nome_produto") or ""),
                "source_hash": _sha256_value(allowed),
            }
        )
        self.entities.append(
            self._sku_entity(
                data=data,
                sku=sku,
                normalized_sku=normalized_sku,
                ref=ref,
                family=family,
                application_type=application_type,
                allowed=allowed,
                content=content,
                classification=classification,
                category_id=category_id,
                category_path=category_path,
                category_status=category_status,
                vehicle_records=records,
                vehicle_pending=pending,
                vehicle_model_ids=model_ids,
                vehicle_overrides=overrides,
            )
        )
        self.stats["valid"] += 1

    def _build_vehicle_tree(self) -> dict[str, Any]:
        try:
            tree = build_vehicle_year_tree(self.vehicle_records)
        except ValueError:
            self.findings.append(
                _finding(
                    "sku_vehicle_tree_invalid",
                    "blocker",
                    VEHICLE_YEAR_TREE_SOURCE_REF,
                    "Registros incompatíveis impediram a geração segura da árvore por veículo e ano.",
                )
            )
            tree = build_vehicle_year_tree([])
        tree_stats = dict(tree.get("stats") or {})
        self.stats["vehicle_year_tree"] = {
            "tree_version": VEHICLE_YEAR_TREE_VERSION,
            "truth_class": VEHICLE_TREE_TRUTH_CLASS,
            "records": int(tree_stats.get("records") or 0),
            "pending": len(self.vehicle_pending),
            "overrides_applied": self.vehicle_overrides,
            "brands": int(tree_stats.get("brands") or 0),
            "models": int(tree_stats.get("models") or 0),
            "years": int(tree_stats.get("years") or 0),
            "distinct_years": int(tree_stats.get("distinct_years") or 0),
            "pages": int(tree_stats.get("pages") or 0),
            "max_degree": int(tree_stats.get("max_degree") or 0),
        }
        return tree

    def _index_lists(self) -> tuple[list[Any], list[Any]]:
        excluded = self.index.get("skus_excluidos")
        if excluded is None:
            excluded = next(
                (value for key, value in self.index.items() if _normal_key(key) == "skus_excluidos"),
                [],
            )
        excluded = excluded if isinstance(excluded, list) else []
        pending = next(
            (value for key, value in self.index.items() if _normal_key(key) == "skus_com_pendencias"),
            [],
        )
        return excluded, pending if isinstance(pending, list) else []

    def _validate_index(self, pending: list[Any]) -> None:
        if self.index.get("schema_version") != SKU_SCHEMA_VERSION:
            self.findings.append(
                _finding("sku_index_schema_invalid", "blocker", self.index_ref, "Indice SKU usa schema inesperado.")
            )
        if (
            self.index.get("total_skus") != len(self.files)
            or self.index.get("arquivos_revisados") != len(self.files)
            or pending
        ):
            self.findings.append(
                _finding("sku_index_inconsistent", "blocker", self.index_ref, "Contagens do indice SKU estao inconsistentes.")
            )

    def _map_specs(
        self,
        excluded: list[Any],
        vehicle_tree: Mapping[str, Any],
    ) -> tuple[tuple[Any, ...], ...]:
        catalog_content = "\n".join(
            [
                f"Catalogo canonico de SKU: {len(self.names)} itens.",
                *[f"- {sku}: {name}" for sku, name in sorted(self.names)],
            ]
        )
        coverage = self.index.get("cobertura") if isinstance(self.index.get("cobertura"), dict) else {}
        coverage_content = "Cobertura do catalogo SKU.\n" + "\n".join(
            f"- {_safe_summary(key)}: {_safe_summary(value)}" for key, value in sorted(coverage.items())
        )
        coverage_content += "\n- excluidos: " + (", ".join(str(item) for item in excluded) or "nenhum")
        family_content = "Familias derivadas do catalogo SKU.\n" + "\n".join(
            f"- {family}: {len(ids)} SKU(s)" for family, ids in sorted(self.families.items())
        )
        category_content = "\n".join(
            [
                f"Taxonomia de categorias de produto v{PRODUCT_CATEGORY_TAXONOMY_VERSION}.",
                "",
                *taxonomy_tree_lines(),
                "",
                f"- SKUs classificados por regra: {self.stats['classified_categories']}",
                f"- SKUs pendentes de revisão: {self.stats['pending_category_review']}",
            ]
        )
        vehicle_content = "\n".join(
            [
                f"Árvore SKU por veículo e ano v{VEHICLE_YEAR_TREE_VERSION}.",
                "",
                "Hierarquia: montadora > modelo ou aplicação > ano > SKUs compatíveis.",
                f"- Relações SKU-veículo-ano: {self.stats['vehicle_year_tree']['records']}",
                f"- Montadoras: {self.stats['vehicle_year_tree']['brands']}",
                f"- Modelos ou aplicações: {self.stats['vehicle_year_tree']['models']}",
                f"- Grupos de ano: {self.stats['vehicle_year_tree']['years']}",
                f"- Pendências não inferidas: {self.stats['vehicle_year_tree']['pending']}",
                f"- Correções pesquisadas aplicadas: {self.vehicle_overrides}",
            ]
        )
        return (
            ("catalog", "Catalogo SKU", catalog_content, {"sku_count": len(self.names)}, "canonical", [self.index_ref]),
            ("coverage", "Cobertura SKU", coverage_content, {"coverage": coverage, "excluded": excluded}, "canonical", [self.index_ref]),
            ("families", "Familias SKU", family_content, {"families": {family: sorted(ids) for family, ids in sorted(self.families.items())}}, "canonical", [self.index_ref]),
            ("categories", "Categorias de produto", category_content, {"taxonomy_version": PRODUCT_CATEGORY_TAXONOMY_VERSION, "category_count": self.stats["categories"], "classified": self.stats["classified_categories"], "pending_review": self.stats["pending_category_review"]}, "generated_secondary", [self.index_ref, PRODUCT_CATEGORY_TAXONOMY_SOURCE_REF]),
            ("vehicles", "Veículos compatíveis por ano", vehicle_content, dict(self.stats["vehicle_year_tree"]), VEHICLE_TREE_TRUTH_CLASS, [self.index_ref, VEHICLE_YEAR_TREE_SOURCE_REF, "docs/knowledge/sku-vehicle-year-tree-v1.md"]),
        )

    def _append_maps(self, excluded: list[Any], vehicle_tree: Mapping[str, Any]) -> None:
        index_hash = (
            _sha256_bytes(_read_bytes(self.index_path))
            if self.index_path.is_file() else _sha256_value({})
        )
        for map_id, title, content, metadata, truth_class, source_refs in self._map_specs(excluded, vehicle_tree):
            self.entities.append(
                _entity(
                    entity_id=f"jk:sku-map:{map_id}",
                    kind="sku_map",
                    domain="cadastro",
                    title=title,
                    surface=self.surface,
                    tenant_scope="client",
                    sensitivity="internal_catalog",
                    truth_class=truth_class,
                    source_refs=source_refs,
                    source_hash=_sha256_value({"index": index_hash, "map": map_id, "metadata": metadata}),
                    relationships=(
                        [{"type": "contains", "target_id": str(vehicle_tree["root_id"])}]
                        if map_id == "vehicles" else []
                    ),
                    metadata={**metadata, "render_individually": True},
                    content=content,
                )
            )

    def _vehicle_page_content(
        self,
        page: Mapping[str, Any],
        node: Mapping[str, Any],
    ) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        records = [dict(item) for item in page.get("records", []) if isinstance(item, Mapping)]
        year_groups = [
            dict(item) for item in page.get("year_groups", []) if isinstance(item, Mapping)
        ]
        node_path = [str(item) for item in node.get("path", [])]
        lines = ["Caminho: " + " > ".join(node_path), f"Árvore: v{VEHICLE_YEAR_TREE_VERSION}"]
        if str(page.get("kind") or "") != "model":
            lines.append("Índice paginado da árvore de compatibilidade.")
            return lines, records, year_groups, node_path
        lines.extend(
            [
                f"Anos conhecidos: {len(year_groups)}",
                f"Relações SKU-veículo-ano: {len(records)}",
            ]
        )
        for group in year_groups:
            lines.extend(["", f"## {group.get('year')}"])
            group_records = [
                dict(item) for item in group.get("records", []) if isinstance(item, Mapping)
            ]
            for record in sorted(
                group_records,
                key=lambda item: (
                    str(item.get("sku") or "").casefold(),
                    str(item.get("product_name") or "").casefold(),
                ),
            ):
                details: list[str] = []
                restrictions = [
                    str(item) for item in record.get("restrictions", []) if str(item).strip()
                ]
                if restrictions:
                    details.append("restrições: " + "; ".join(restrictions))
                if str(record.get("status") or "") == "vigente":
                    details.append(
                        f"vigente, verificado até {record.get('verified_through')}; sem ano final confirmado"
                    )
                suffix = f" ({'; '.join(details)})" if details else ""
                lines.append(f"- SKU {record.get('sku')} — {record.get('product_name')}{suffix}")
        return lines, records, year_groups, node_path

    def _append_vehicle_pages(self, vehicle_tree: Mapping[str, Any]) -> None:
        nodes = {
            str(node.get("id") or ""): node
            for node in vehicle_tree.get("nodes", []) if isinstance(node, Mapping)
        }
        for page in vehicle_tree.get("page_specs", []):
            if not isinstance(page, Mapping):
                continue
            node_id = str(page.get("node_id") or "")
            lines, records, year_groups, node_path = self._vehicle_page_content(
                page, nodes.get(node_id, {})
            )
            parent_id = str(page.get("parent_id") or "")
            child_ids = [str(item) for item in page.get("child_ids", [])]
            relationships = [
                {
                    "type": "child_of",
                    "target_id": parent_id or "jk:sku-map:vehicles",
                },
                *[{"type": "contains", "target_id": child_id} for child_id in child_ids],
            ]
            source_refs = sorted(
                {
                    str(item.get("source_ref") or "")
                    for item in records if str(item.get("source_ref") or "").strip()
                }
            ) or [VEHICLE_YEAR_TREE_SOURCE_REF]
            self.entities.append(
                _entity(
                    entity_id=node_id,
                    kind="sku_vehicle_tree_page",
                    domain="cadastro",
                    title=str(page.get("title") or "Veículos compatíveis"),
                    surface=self.surface,
                    tenant_scope="client",
                    sensitivity="internal_catalog",
                    truth_class=VEHICLE_TREE_TRUTH_CLASS,
                    source_refs=[*source_refs, "docs/knowledge/sku-vehicle-year-tree-v1.md"],
                    source_hash=_sha256_value({"tree_version": VEHICLE_YEAR_TREE_VERSION, "page": page, "path": node_path}),
                    relationships=relationships,
                    metadata={
                        "tree_version": VEHICLE_YEAR_TREE_VERSION,
                        "node_kind": str(page.get("kind") or ""),
                        "tree_path": node_path,
                        "relative_path": str(page.get("relative_path") or ""),
                        "parent_id": parent_id,
                        "child_ids": child_ids,
                        "record_count": len(records),
                        "year_group_count": len(year_groups),
                        "render_individually": True,
                    },
                    content="\n".join(lines),
                )
            )

    def _append_categories(self, category_nodes: list[dict[str, Any]]) -> None:
        nodes = {str(node["id"]): node for node in category_nodes}
        for node in category_nodes:
            node_id = str(node["id"])
            direct = sorted(
                self.category_members.get(node_id, []),
                key=lambda item: (str(item.get("sku") or "").casefold(), str(item.get("id") or "")),
            )
            descendants = sorted(
                [
                    member
                    for category_id, members in self.category_members.items()
                    if category_id == node_id or category_id.startswith(node_id + ":")
                    for member in members
                ],
                key=lambda item: (str(item.get("sku") or "").casefold(), str(item.get("id") or "")),
            )
            child_ids = [str(item) for item in node.get("child_ids") or []]
            lines = [
                "Caminho: " + " > ".join(str(item) for item in node.get("path") or []),
                f"Taxonomia: v{PRODUCT_CATEGORY_TAXONOMY_VERSION}",
                f"SKUs nesta categoria e subcategorias: {len(descendants)}",
            ]
            if child_ids:
                lines.extend(["", "Subcategorias:"])
                lines.extend(
                    f"- {nodes[child_id]['label']}: "
                    f"{sum(1 for member_id in self.category_members if member_id == child_id or member_id.startswith(child_id + ':') for _member in self.category_members[member_id])} SKU(s)"
                    for child_id in child_ids
                )
            if direct:
                lines.extend(["", "SKUs classificados diretamente:"])
                lines.extend(f"- SKU {member['sku']} - {member['title']}" for member in direct)
            elif bool(node.get("is_leaf")):
                lines.extend(["", "Nenhum SKU classificado diretamente nesta categoria."])
            parent_id = str(node.get("parent_id") or "")
            relationships = ([{"type": "child_of", "target_id": parent_id}] if parent_id else [])
            relationships.extend({"type": "contains", "target_id": child_id} for child_id in child_ids)
            node_path = [str(item) for item in node.get("path") or []]
            self.entities.append(
                _entity(
                    entity_id=node_id,
                    kind="sku_category",
                    domain="cadastro",
                    title=str(node.get("label") or "Categoria de produto"),
                    surface=self.surface,
                    tenant_scope="client",
                    sensitivity="internal_catalog",
                    truth_class="generated_secondary",
                    source_refs=[self.index_ref, PRODUCT_CATEGORY_TAXONOMY_SOURCE_REF],
                    source_hash=_sha256_value({"taxonomy_version": PRODUCT_CATEGORY_TAXONOMY_VERSION, "node": node, "members": [(member["id"], member["source_hash"]) for member in descendants]}),
                    relationships=relationships,
                    metadata={
                        "taxonomy_version": PRODUCT_CATEGORY_TAXONOMY_VERSION,
                        "category_path": node_path,
                        "parent_id": parent_id,
                        "child_ids": child_ids,
                        "direct_member_count": len(direct),
                        "member_count": len(descendants),
                        "is_leaf": bool(node.get("is_leaf")),
                        "is_review_queue": bool(node.get("is_review_queue")),
                        "render_individually": True,
                    },
                    content="\n".join(lines),
                )
            )

    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not self._resolve_catalog():
            return [], self.stats
        self._load_index()
        for path in self.files:
            self._process_dossier(path)
        excluded, pending = self._index_lists()
        self.stats["excluded"] = len(excluded)
        self.stats["families"] = len(self.families)
        category_nodes = taxonomy_nodes()
        self.stats["categories"] = sum(
            1 for node in category_nodes if not bool(node.get("is_review_queue"))
        )
        vehicle_tree = self._build_vehicle_tree()
        self._validate_index(pending)
        self._append_maps(excluded, vehicle_tree)
        self._append_vehicle_pages(vehicle_tree)
        self._append_categories(category_nodes)
        return self.entities, self.stats


def _scan_sku(
    base: Path,
    info_root: Path,
    client_id: str,
    surface: str,
    findings: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    del base
    return _SkuInventoryScanner(info_root, client_id, surface, findings).scan()
