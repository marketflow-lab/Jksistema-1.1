#!/usr/bin/env python3
"""Provision the local trust identity for one Cadastro tenant alias."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services.cadastro_tenant_trust import (  # noqa: E402
    CadastroTenantTrustErro,
    planejar_registro_alias,
    registrar_alias_tenant,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Registra localmente a identidade de um alias de tenant do Cadastro."
    )
    parser.add_argument("--alias-info-root", required=True)
    parser.add_argument("--canonical-info-root", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Publica o registro. Sem esta flag, executa somente dry-run.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.apply:
            result = registrar_alias_tenant(
                args.alias_info_root, args.canonical_info_root, args.client_id
            )
        else:
            result = planejar_registro_alias(
                args.alias_info_root, args.canonical_info_root, args.client_id
            )
    except CadastroTenantTrustErro as exc:
        print(json.dumps({"ok": False, "code": exc.code}, sort_keys=True))
        return 2
    except Exception:
        print(json.dumps({"ok": False, "code": "unexpected_error"}, sort_keys=True))
        return 3
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
