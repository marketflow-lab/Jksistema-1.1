#!/usr/bin/env python3
"""Dry-run por padrao para migrar fotos do Cadastro para lojas exatas."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services.cadastro_fotos_migracao import (
    CadastroFotosMigracaoErro,
    executar_migracao_fotos_contextos,
    planejar_migracao_fotos_contextos,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--info-root", action="append", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--store-id", action="append", required=True)
    parser.add_argument("--group-id", default="uai-jk-carlos")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--delete-legacy", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.apply:
            resultado = executar_migracao_fotos_contextos(
                args.info_root,
                args.client_id,
                args.store_id,
                group_id=args.group_id,
                delete_legacy=args.delete_legacy,
                expected_plan_sha256=args.expected_plan_sha256,
            )
        else:
            if args.delete_legacy:
                raise CadastroFotosMigracaoErro(
                    "delete_requires_apply",
                    "--delete-legacy so pode ser usado junto com --apply.",
                )
            resultado = planejar_migracao_fotos_contextos(
                args.info_root,
                args.client_id,
                args.store_id,
                group_id=args.group_id,
            )["report"]
        print(json.dumps(resultado, ensure_ascii=False, sort_keys=True))
        return 0
    except CadastroFotosMigracaoErro as exc:
        print(
            json.dumps(
                {"success": False, "code": exc.code, "message": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {
                    "success": False,
                    "code": "unexpected_error",
                    "message": "A migracao falhou de forma inesperada.",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
