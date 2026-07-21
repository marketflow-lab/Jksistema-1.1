from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.codex_contract_snapshot import build_codex_contract_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture the internal Codex contract snapshot.")
    parser.add_argument(
        "--output",
        default=str(ROOT / "tests" / "contracts" / "codex_internal_v2.json"),
    )
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_codex_contract_snapshot(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
