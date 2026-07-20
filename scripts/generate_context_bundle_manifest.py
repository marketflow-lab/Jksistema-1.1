"""Gera o manifesto deterministico do bundle docs/knowledge."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_DEFAULT_BASE_DIR = Path(__file__).resolve().parents[1]
if str(_DEFAULT_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_DEFAULT_BASE_DIR))

from backend.services.context_hub_inventory import build_context_bundle_manifest


def _source_version(base_dir: Path) -> str:
    # A versao do Electron e a fonte canonica da release distribuida. O
    # package.json da raiz continua empacotado e precisa ser identico, mas nao
    # pode conduzir sozinho manifests consumidos pelo instalador.
    payload = json.loads((base_dir / "electron_app" / "package.json").read_text(encoding="utf-8"))
    return str(payload.get("version") or "unknown")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=str(_DEFAULT_BASE_DIR))
    parser.add_argument("--source-version", default="")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve(strict=True)
    output = base_dir / "context-bundle-manifest.json"
    manifest = build_context_bundle_manifest(
        base_dir / "docs" / "knowledge",
        args.source_version or _source_version(base_dir),
    )
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            print("context-bundle-manifest.json esta ausente ou desatualizado")
            return 1
        print("context-bundle-manifest.json esta atualizado")
        return 0
    output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"manifesto gerado: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
