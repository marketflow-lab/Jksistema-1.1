"""Render the semantic inventory as a compact, navigable Obsidian graph."""

from __future__ import annotations

import hashlib
from typing import (
    Any,
    Mapping,
    Sequence,
)

from .markdown import (
    _aggregate_entity,
    _paginate_markdown_rows,
    _with_graph_navigation,
    render_context_entity_markdown,
)
from .normalization import (
    _canonical_json,
    _slug,
)


class _InventoryMarkdownRenderer:
    def __init__(self, inventory: Mapping[str, Any], generated_at: str) -> None:
        self.inventory = inventory
        self.generated_at = generated_at
        self.rows = [row for row in inventory.get("entities", []) if isinstance(row, dict)]
        self.source_version = str(inventory.get("source_version") or "unknown")
        self.output: dict[str, str] = {}
        self.index_path = "70_Gerado/Mapas/Inventario-do-Programa.md"
        self.areas_path = "70_Gerado/Mapas/Areas-do-Programa.md"
        self.domain_entities = [row for row in self.rows if row.get("kind") == "domain"]
        self.domain_paths = {
            str(row["domain"]): f"70_Gerado/Dominios/{_slug(str(row['domain']))}.md"
            for row in self.domain_entities
        }
        self.screen_entities = [row for row in self.rows if row.get("kind") == "screen"]
        self.screen_paths = {
            str(row["id"]): f"70_Gerado/Mapas/Telas/{_slug(str(row['id']).split(':')[-1])}.md"
            for row in self.screen_entities
        }
        self.grouped_notes = (
            ("api", "70_Gerado/Contratos/APIs.md", "jk:map:apis", "Mapa de APIs"),
            ("schema", "70_Gerado/Contratos/Schemas.md", "jk:map:schemas", "Mapa de Schemas"),
            ("data_schema", "70_Gerado/Contratos/Dados.md", "jk:map:data", "Mapa de Dados"),
            ("test", "70_Gerado/Operacao/Testes.md", "jk:map:tests", "Mapa de Testes"),
            ("capability", "70_Gerado/Operacao/Capacidades-Codex.md", "jk:map:capabilities", "Capacidades Codex"),
            ("integration", "70_Gerado/Operacao/Runtime-e-Integracoes.md", "jk:map:integrations", "Runtime e Integracoes"),
        )
        self.grouped_paths = {
            kind: (path, title) for kind, path, _entity_id, title in self.grouped_notes
        }
        api_entities = [row for row in self.rows if row.get("kind") == "api"]
        self.api_domains = sorted({str(row.get("domain") or "sistema") for row in api_entities})
        self.api_domain_paths = {
            domain: f"70_Gerado/Contratos/APIs/{_slug(domain)}.md"
            for domain in self.api_domains
        }
        self.sku_paths = {
            "jk:sku-map:catalog": "70_Gerado/Produtos/Catalogo-SKU.md",
            "jk:sku-map:coverage": "70_Gerado/Produtos/Cobertura-SKU.md",
            "jk:sku-map:families": "70_Gerado/Produtos/Familias-SKU.md",
            "jk:sku-map:categories": "70_Gerado/Produtos/Categorias.md",
            "jk:sku-map:vehicles": "70_Gerado/Produtos/Veiculos-Compativeis.md",
        }
        self.sku_titles = {
            str(row.get("id") or ""): str(row.get("title") or "Mapa SKU")
            for row in self.rows
            if str(row.get("id") or "") in self.sku_paths
        }
        self._prepare_taxonomy_paths()

    def _prepare_taxonomy_paths(self) -> None:
        self.category_entities = [row for row in self.rows if row.get("kind") == "sku_category"]
        self.category_titles = {
            str(row.get("id") or ""): str(row.get("title") or "Categoria de produto")
            for row in self.category_entities
        }
        self.category_paths: dict[str, str] = {}
        for row in self.category_entities:
            row_id = str(row.get("id") or "")
            label = _slug(str(row.get("title") or "categoria"))[:56]
            short_hash = hashlib.sha256(row_id.encode("utf-8")).hexdigest()[:10]
            self.category_paths[row_id] = f"70_Gerado/Produtos/Categorias/{label}-{short_hash}.md"
        self.vehicle_entities = [
            row for row in self.rows if row.get("kind") == "sku_vehicle_tree_page"
        ]
        self.vehicle_titles = {
            str(row.get("id") or ""): str(row.get("title") or "Veículos compatíveis")
            for row in self.vehicle_entities
        }
        self.vehicle_paths: dict[str, str] = {}
        for row in self.vehicle_entities:
            row_id = str(row.get("id") or "")
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            relative_path = str(metadata.get("relative_path") or "").replace("\\", "/").strip()
            path_parts = [part for part in relative_path.split("/") if part]
            if not relative_path.startswith("/") and ".." not in path_parts and relative_path.endswith(".md"):
                self.vehicle_paths[row_id] = f"70_Gerado/Produtos/{relative_path}"

    def _store(self, path: str, entity: Mapping[str, Any]) -> None:
        self.output[path] = render_context_entity_markdown(
            entity,
            source_version=self.source_version,
            generated_at=self.generated_at,
        )

    def _render_index_and_areas(self) -> None:
        stats = self.inventory.get("stats") if isinstance(self.inventory.get("stats"), dict) else {}
        content = "Inventario semantico do JK Sistema.\n\n" + "\n".join(
            f"- {key}: {_canonical_json(value) if isinstance(value, (dict, list)) else value}"
            for key, value in sorted(stats.items())
        )
        index_links = [
            (path, str(row.get("title") or row.get("domain") or "Dominio"))
            for row in self.domain_entities
            if (path := self.domain_paths.get(str(row.get("domain") or "")))
        ]
        available_kinds = {str(row.get("kind") or "") for row in self.rows}
        represented_kinds = {
            kind for kind, _path, _entity_id, _title in self.grouped_notes if kind in available_kinds
        }
        area_links = [
            (path, title)
            for kind, path, _entity_id, title in self.grouped_notes
            if kind in represented_kinds
        ]
        area_links.extend(
            (path, self.sku_titles[entity_id])
            for entity_id, path in self.sku_paths.items()
            if entity_id in self.sku_titles
        )
        if area_links:
            index_links.append((self.areas_path, "Areas do Programa"))
        index = _aggregate_entity(
            entity_id="jk:map:inventory",
            kind="map",
            domain="sistema",
            title="Inventario do Programa",
            rows=self.rows,
            inventory=self.inventory,
            content=_with_graph_navigation(content, index_links),
        )
        self._store(self.index_path, index)
        if not area_links:
            return
        areas = _aggregate_entity(
            entity_id="jk:map:areas",
            kind="map",
            domain="sistema",
            title="Areas do Programa",
            rows=[
                row
                for row in self.rows
                if str(row.get("kind") or "") in represented_kinds
                or str(row.get("id") or "") in self.sku_titles
            ],
            inventory=self.inventory,
            content=_with_graph_navigation(
                "Indices tecnicos e operacionais organizados por finalidade.",
                [(self.index_path, "Inventario do Programa"), *area_links],
            ),
        )
        self._store(self.areas_path, areas)

    def _render_domains(self) -> None:
        for domain_entity in self.domain_entities:
            domain = str(domain_entity["domain"])
            members = [
                row for row in self.rows
                if row.get("domain") == domain and row.get("kind") != "domain"
            ]
            content = str(domain_entity.get("content") or "") + "\n\n" + "\n".join(
                f"- `{row['id']}` - {row['title']}" for row in members
            )
            navigation = [(self.index_path, "Inventario do Programa")]
            navigation.extend(
                (self.screen_paths[str(row["id"])], str(row.get("title") or row["id"]))
                for row in members
                if row.get("kind") == "screen" and str(row.get("id") or "") in self.screen_paths
            )
            for kind in sorted({str(row.get("kind") or "") for row in members}):
                if kind == "api" and domain in self.api_domain_paths:
                    navigation.append((self.api_domain_paths[domain], f"APIs do dominio {domain}"))
                elif kind in self.grouped_paths:
                    navigation.append(self.grouped_paths[kind])
            aggregate = _aggregate_entity(
                entity_id=domain_entity["id"],
                kind="domain",
                domain=domain,
                title=domain_entity["title"],
                rows=members or [domain_entity],
                inventory=self.inventory,
                content=_with_graph_navigation(content, navigation),
            )
            self._store(self.domain_paths[domain], aggregate)

    def _render_screens(self) -> None:
        for screen in self.screen_entities:
            related_calls = [
                row for row in self.rows
                if row.get("kind") == "frontend_api_call"
                and any(
                    rel.get("target_id") == screen["id"]
                    for rel in row.get("relationships", []) if isinstance(rel, dict)
                )
            ]
            content = str(screen.get("content") or "")
            if related_calls:
                content += "\n\nChamadas de API:\n" + "\n".join(
                    f"- {row['title']} ({row.get('metadata', {}).get('classification', 'unknown')})"
                    for row in related_calls
                )
            navigation: list[tuple[str, str]] = []
            domain = str(screen.get("domain") or "")
            domain_path = self.domain_paths.get(domain)
            navigation.append(
                (domain_path, f"Dominio {screen.get('domain')}")
                if domain_path else (self.index_path, "Inventario do Programa")
            )
            if related_calls:
                api_path = self.api_domain_paths.get(domain or "sistema")
                navigation.append(
                    (api_path, f"APIs do dominio {domain or 'sistema'}")
                    if api_path else self.grouped_paths["api"]
                )
            aggregate = dict(screen)
            aggregate["content"] = _with_graph_navigation(content, navigation)
            self._store(self.screen_paths[str(screen["id"])], aggregate)

    def _render_api_domain_maps(
        self,
        selected: Sequence[Mapping[str, Any]],
        output_path: str,
        entity_id: str,
        title: str,
    ) -> str:
        for domain in self.api_domains:
            domain_rows = [
                row for row in selected if str(row.get("domain") or "sistema") == domain
            ]
            navigation = [(output_path, title)]
            if domain in self.domain_paths:
                navigation.append((self.domain_paths[domain], f"Dominio {domain}"))
            aggregate = _aggregate_entity(
                entity_id=f"{entity_id}:{_slug(domain)}",
                kind="map",
                domain=domain,
                title=f"APIs do dominio {domain}",
                rows=domain_rows,
                inventory=self.inventory,
                content=_with_graph_navigation(
                    "\n".join(f"- `{row['id']}` - {row['title']}" for row in domain_rows),
                    navigation,
                ),
            )
            self._store(self.api_domain_paths[domain], aggregate)
        return f"Mapa de {len(selected)} APIs organizado em {len(self.api_domains)} dominios."

    def _render_test_pages(
        self,
        selected: Sequence[Mapping[str, Any]],
        output_path: str,
        entity_id: str,
    ) -> tuple[str, list[tuple[str, str]]]:
        pages = _paginate_markdown_rows(selected)
        page_links: list[tuple[str, str]] = []
        for page_number, page_rows in enumerate(pages, start=1):
            page_path = f"70_Gerado/Operacao/Testes/Parte-{page_number:03d}.md"
            page_title = f"Mapa de Testes - Parte {page_number:03d}"
            page_links.append((page_path, page_title))
            navigation = [(output_path, "Mapa de Testes")]
            navigation.extend(
                (self.domain_paths[domain], f"Dominio {domain}")
                for domain in sorted({str(row.get("domain") or "") for row in page_rows})
                if domain in self.domain_paths
            )
            aggregate = _aggregate_entity(
                entity_id=f"{entity_id}:part-{page_number:03d}",
                kind="map",
                domain="sistema",
                title=page_title,
                rows=page_rows,
                inventory=self.inventory,
                content=_with_graph_navigation(
                    "\n".join(f"- `{row['id']}` - {row['title']}" for row in page_rows),
                    navigation,
                ),
            )
            self._store(page_path, aggregate)
        content = (
            f"Mapa paginado para preservar integralmente {len(selected)} testes.\n\n"
            f"- paginas: {len(pages)}"
        )
        return content, page_links

    def _render_group(self, kind: str, output_path: str, entity_id: str, title: str) -> None:
        selected = [row for row in self.rows if row.get("kind") == kind]
        if not selected:
            return
        navigation = [(self.areas_path, "Areas do Programa")]
        if kind == "api":
            navigation.extend(
                (self.api_domain_paths[domain], f"APIs do dominio {domain}")
                for domain in self.api_domains
            )
            content = self._render_api_domain_maps(selected, output_path, entity_id, title)
        else:
            navigation.extend(
                (self.domain_paths[domain], f"Dominio {domain}")
                for domain in sorted({str(row.get("domain") or "") for row in selected})
                if domain in self.domain_paths
            )
            content = "\n".join(f"- `{row['id']}` - {row['title']}" for row in selected)
        if kind == "test":
            content, page_links = self._render_test_pages(selected, output_path, entity_id)
            navigation.extend(page_links)
        aggregate = _aggregate_entity(
            entity_id=entity_id,
            kind="map",
            domain="sistema",
            title=title,
            rows=selected,
            inventory=self.inventory,
            content=_with_graph_navigation(content, navigation),
        )
        self._store(output_path, aggregate)

    def _render_groups(self) -> None:
        for group in self.grouped_notes:
            self._render_group(*group)

    def _render_sku_maps(self) -> None:
        for row in self.rows:
            row_id = str(row.get("id") or "")
            output_path = self.sku_paths.get(row_id)
            if not output_path:
                continue
            navigation = [(self.areas_path, "Areas do Programa")]
            cadastro_path = self.domain_paths.get("cadastro")
            if cadastro_path:
                navigation.append((cadastro_path, "Dominio Cadastro"))
            navigation.extend(
                (path, self.sku_titles[entity_id])
                for entity_id, path in self.sku_paths.items()
                if entity_id != row_id and entity_id in self.sku_titles
            )
            if row_id == "jk:sku-map:categories":
                navigation.extend(
                    (self.category_paths[str(category.get("id") or "")], str(category.get("title") or "Categoria"))
                    for category in self.category_entities
                    if not str(category.get("metadata", {}).get("parent_id") or "")
                    and str(category.get("id") or "") in self.category_paths
                )
            if row_id == "jk:sku-map:vehicles":
                root_path = self.vehicle_paths.get("jk:sku-vehicle:root")
                if root_path:
                    navigation.append((root_path, "Abrir árvore por montadora"))
            aggregate = dict(row)
            aggregate["content"] = _with_graph_navigation(str(row.get("content") or ""), navigation)
            self._store(output_path, aggregate)

    def _render_categories(self) -> None:
        if "jk:sku-map:categories" not in self.sku_titles:
            return
        for row in self.category_entities:
            row_id = str(row.get("id") or "")
            output_path = self.category_paths.get(row_id)
            if not output_path:
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            parent_id = str(metadata.get("parent_id") or "")
            child_ids = [str(item) for item in metadata.get("child_ids") or []]
            navigation = [
                (self.category_paths[parent_id], self.category_titles.get(parent_id, "Categoria superior"))
                if parent_id and parent_id in self.category_paths
                else (self.sku_paths["jk:sku-map:categories"], "Categorias de produto")
            ]
            navigation.extend(
                (self.category_paths[child_id], self.category_titles.get(child_id, "Subcategoria"))
                for child_id in child_ids if child_id in self.category_paths
            )
            aggregate = dict(row)
            aggregate["content"] = _with_graph_navigation(str(row.get("content") or ""), navigation)
            self._store(output_path, aggregate)

    def _render_vehicles(self) -> None:
        if "jk:sku-map:vehicles" not in self.sku_titles:
            return
        for row in self.vehicle_entities:
            row_id = str(row.get("id") or "")
            output_path = self.vehicle_paths.get(row_id)
            if not output_path:
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            parent_id = str(metadata.get("parent_id") or "")
            child_ids = [str(item) for item in metadata.get("child_ids") or []]
            navigation = [
                (self.vehicle_paths[parent_id], self.vehicle_titles.get(parent_id, "Nível anterior"))
                if parent_id and parent_id in self.vehicle_paths
                else (self.sku_paths["jk:sku-map:vehicles"], "Veículos compatíveis por ano")
            ]
            navigation.extend(
                (self.vehicle_paths[child_id], self.vehicle_titles.get(child_id, "Próximo nível"))
                for child_id in child_ids if child_id in self.vehicle_paths
            )
            aggregate = dict(row)
            aggregate["content"] = _with_graph_navigation(str(row.get("content") or ""), navigation)
            self._store(output_path, aggregate)

    def render(self) -> dict[str, str]:
        self._render_index_and_areas()
        self._render_domains()
        self._render_screens()
        self._render_groups()
        self._render_sku_maps()
        self._render_categories()
        self._render_vehicles()
        return dict(sorted(self.output.items()))


def render_context_inventory_markdown(
    inventory: Mapping[str, Any],
    *,
    generated_at: str = "1970-01-01T00:00:00Z",
) -> dict[str, str]:
    """Render a compact collection without creating one note per SKU."""

    return _InventoryMarkdownRenderer(inventory, generated_at).render()
