"""Manutenção única do Shared Sync v2; nunca publica ou importa dados."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend_api  # configura Firebase e o runtime somente após get_tenant_path
from backend.services import shared_sync


def preview() -> dict:
    invites = shared_sync._shared_sync_invites_all()
    links = shared_sync._shared_sync_links_all()
    pending = [item for item in invites if str(item.get("status") or "pending").lower() == "pending"]
    groups = {}
    for item in links:
        if bool(item.get("active", True)):
            groups.setdefault(shared_sync._shared_sync_item_pair_key(item), []).append(item)
    return {
        "pending_invites": len(pending),
        "active_links": sum(len(items) for items in groups.values()),
        "active_pairs": len(groups),
        "duplicate_links": sum(max(0, len(items) - 1) for items in groups.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    before = preview()
    if not args.apply:
        print(json.dumps({"apply": False, "before": before}, ensure_ascii=False))
        return
    result = shared_sync._shared_sync_migrate_v2_records()
    print(json.dumps({"apply": True, "before": before, "result": result, "after": preview()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
