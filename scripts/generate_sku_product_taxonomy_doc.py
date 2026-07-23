"""Gera a documentação versionada da árvore de categorias de produto."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


_DEFAULT_BASE_DIR = Path(__file__).resolve().parents[1]
if str(_DEFAULT_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_DEFAULT_BASE_DIR))

from backend.services.context_hub_sku_taxonomy import (  # noqa: E402
    PRODUCT_CATEGORY_TAXONOMY_SOURCE_REF,
    PRODUCT_CATEGORY_TAXONOMY_VERSION,
    taxonomy_tree_lines,
)


TARGET_RELATIVE_PATH = Path("docs/knowledge/sku-product-category-taxonomy-v1.md")


def render_document() -> str:
    tree = "\n".join(taxonomy_tree_lines())
    return f"""# Árvore de categorias de produto SKU

> Arquivo gerado. A fonte de manutenção é `{PRODUCT_CATEGORY_TAXONOMY_SOURCE_REF}`.

- Versão da taxonomia: `{PRODUCT_CATEGORY_TAXONOMY_VERSION}`
- Fonte canônica dos produtos: `info/<client_id>/SKU/*.json`
- Classe desta árvore: `generated_secondary`
- Escopo: categorias e subcategorias de produto; compatibilidade por veículo e ano é uma árvore separada.

## Regras de publicação

- A taxonomia não substitui nem duplica os dossiês canônicos de SKU.
- Cada SKU recebe uma categoria por regra determinística e auditável.
- Produtos sem evidência suficiente ficam em `Pendentes de revisão`.
- No Obsidian, a árvore é publicada em `70_Gerado/Produtos/Categorias.md`, com uma nota por nó da hierarquia.
- Notas humanas de `80_Curadoria` são preservadas pelo publicador.

## Árvore aprovada

{tree}

## Fila operacional

- Pendentes de revisão
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=str(_DEFAULT_BASE_DIR))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve(strict=True)
    target = base_dir / TARGET_RELATIVE_PATH
    rendered = render_document()
    if args.check:
        if not target.is_file() or target.read_text(encoding="utf-8") != rendered:
            print(f"documentação desatualizada: {TARGET_RELATIVE_PATH.as_posix()}")
            return 1
        print(f"documentação atualizada: {TARGET_RELATIVE_PATH.as_posix()}")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"documentação gerada: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
