"""Pure planning of store aliases from caller-authenticated account identities.

The caller supplies active stores, verified account IDs and any proof that an
imported duplicate can be removed. This module performs no account lookup or
reference audit. Names are only keys for legacy records, never match evidence.
"""

from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
from typing import Any

from fastapi import HTTPException


_INVALID = "shared_sync_store_reconciliation_invalid"
_AMBIGUOUS = "shared_sync_store_reconciliation_ambiguous"


def _fail(reason: str, *, ambiguous: bool = False) -> None:
    # Reasons are constants: never include store/account/connection payloads.
    raise HTTPException(
        status_code=409,
        detail={
            "code": _AMBIGUOUS if ambiguous else _INVALID,
            "message": (
                "A identidade das lojas nao permite uma conciliacao segura."
                if ambiguous
                else "As evidencias de identidade das lojas precisam ser verificadas."
            ),
            "reason": reason,
        },
    )


def _identifier(value: Any, *, allow_empty: bool = False) -> str:
    if value is None and allow_empty:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        _fail("invalid_store_id")
    result = str(value).strip()
    if not result and not allow_empty:
        _fail("missing_store_id")
    return result


def _remote_key(store: dict) -> str:
    store_id = _identifier(store.get("store_id"), allow_empty=True)
    if store_id:
        return store_id
    name = store.get("nome")
    if not isinstance(name, str):
        _fail("missing_legacy_name")
    text = unicodedata.normalize("NFKD", name)
    text = "".join(char for char in text if not unicodedata.combining(char))
    name_key = re.sub(r"[^a-z0-9]+", "", text.lower())
    if not name_key:
        _fail("missing_legacy_name")
    return "legacy:" + name_key


def _store_index(stores: Any, *, remote: bool) -> dict[str, dict]:
    if not isinstance(stores, list):
        _fail("invalid_store_list")
    result = {}
    for store in stores:
        if not isinstance(store, dict):
            _fail("invalid_store_record")
        key = _remote_key(store) if remote else _identifier(store.get("store_id"))
        if key in result:
            _fail("duplicate_store_key", ambiguous=True)
        result[key] = store
    return result


def _accounts(value: Any, store_keys: set[str]) -> dict[str, dict[str, tuple[str, str]]]:
    if not isinstance(value, dict):
        _fail("invalid_verified_accounts")
    result = {}
    for key, providers in value.items():
        if not isinstance(key, str) or key not in store_keys or not isinstance(providers, dict):
            _fail("invalid_verified_accounts")
        identities = {}
        for provider, identity in providers.items():
            if not isinstance(provider, str) or not provider or provider != provider.strip().lower():
                _fail("invalid_verified_provider")
            if not isinstance(identity, (tuple, list)) or len(identity) != 2:
                _fail("invalid_verified_identity")
            account, site = identity
            if (
                not isinstance(account, str)
                or not account.strip()
                or account != account.strip()
                or not isinstance(site, str)
                or site != site.strip()
            ):
                _fail("invalid_verified_identity")
            identities[provider] = (account, site)
        result[key] = identities
    return result


def _relation(left: dict, right: dict) -> str:
    common = left.keys() & right.keys()
    if not common:
        return "unknown"
    equal = [left[provider] == right[provider] for provider in common]
    if all(equal):
        return "match"
    return "conflict" if any(equal) else "different"


def _id_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, (set, frozenset, list, tuple)):
        _fail("invalid_import_evidence")
    return {_identifier(item) for item in value}


def plan_store_reconciliation(
    local_stores: list[dict],
    remote_stores: list[dict],
    *,
    verified_accounts: dict,
    previous_mapping: dict | None = None,
    preimport_store_ids: Any = None,
    removable_imported_ids: Any = None,
) -> dict:
    """Return a detached plan; raise 409 before callers apply ambiguous plans.

    ``previous_mapping`` must be restricted to this snapshot and peer context.
    The caller must authenticate every supplied account identity and prove that
    removable IDs were imported, unedited and have no live local references.
    ``matched_local_ids`` identifies account-proven matches whose local
    connections the caller must preserve; normal exact-ID merges are excluded.
    """
    local = _store_index(local_stores, remote=False)
    remote = _store_index(remote_stores, remote=True)
    if not isinstance(verified_accounts, dict) or set(verified_accounts) - {"local", "remote"}:
        _fail("invalid_verified_accounts")
    local_accounts = _accounts(verified_accounts.get("local", {}), set(local))
    remote_accounts = _accounts(verified_accounts.get("remote", {}), set(remote))
    if previous_mapping is None:
        previous_mapping = {}
    if not isinstance(previous_mapping, dict):
        _fail("invalid_previous_mapping")
    previous = {}
    for key, value in previous_mapping.items():
        if not isinstance(key, str) or key not in remote:
            _fail("orphan_previous_mapping")
        target = _identifier(value)
        if target not in local:
            _fail("orphan_previous_mapping")
        if _relation(remote_accounts.get(key, {}), local_accounts.get(target, {})) != "match":
            _fail("unverified_previous_mapping")
        previous[key] = target

    preimport = _id_set(preimport_store_ids)
    removable = _id_set(removable_imported_ids)
    if removable & preimport or removable - set(local):
        _fail("invalid_removal_evidence")

    remote_keys = list(remote)
    for index, key in enumerate(remote_keys):
        for other_key in remote_keys[index + 1 :]:
            if _relation(remote_accounts.get(key, {}), remote_accounts.get(other_key, {})) in {"match", "conflict"}:
                _fail("duplicate_remote_account", ambiguous=True)

    mapping = {}
    removed = set()
    matched = set()

    def candidates(key: str) -> list[str]:
        matches = []
        for local_id in local:
            relation = _relation(remote_accounts.get(key, {}), local_accounts.get(local_id, {}))
            if relation == "conflict":
                _fail("conflicting_provider_identity", ambiguous=True)
            if relation == "match":
                matches.append(local_id)
        return matches

    def choose(matches: list[str], *, required_target: str | None = None) -> str:
        if len(matches) == 1:
            target = matches[0]
        else:
            originals = set(matches) & preimport
            if len(originals) != 1:
                _fail("multiple_local_accounts", ambiguous=True)
            target = next(iter(originals))
            duplicates = set(matches) - {target}
            if not duplicates <= removable:
                _fail("duplicate_not_removable", ambiguous=True)
            if any(
                _relation(local_accounts.get(target, {}), local_accounts.get(duplicate, {})) != "match"
                for duplicate in duplicates
            ):
                _fail("unverified_duplicate_identity", ambiguous=True)
            removed.update(duplicates)
        if required_target is not None and target != required_target:
            _fail("conflicting_previous_mapping", ambiguous=True)
        return target

    for key, store in remote.items():
        store_id = _identifier(store.get("store_id"), allow_empty=True)
        if key in previous:
            matches = candidates(key)
            if store_id in local and store_id != previous[key] and store_id not in matches:
                _fail("unverified_duplicate_identity", ambiguous=True)
            target = choose(matches, required_target=previous[key])
            matched.add(target)
        elif store_id and store_id in local:
            # An established exact ID retains the existing merge semantics.
            # Only evidence of an imported duplicate allows replacing that ID.
            target = store_id
            if preimport and store_id not in preimport:
                matches = candidates(key)
                if set(matches) & preimport:
                    if store_id not in matches:
                        _fail("unverified_duplicate_identity", ambiguous=True)
                    target = choose(matches)
                    matched.add(target)
        else:
            matches = candidates(key)
            if matches:
                target = choose(matches)
                matched.add(target)
            elif store_id:
                target = store_id
            else:
                _fail("unmatched_legacy_store")
        if target in mapping.values():
            _fail("multiple_remote_stores_for_local", ambiguous=True)
        mapping[key] = target

    if removed & set(mapping.values()):
        _fail("removed_store_still_targeted", ambiguous=True)
    local_clones = [deepcopy(store) for key, store in local.items() if key not in removed]
    remote_clones = []
    for key, store in remote.items():
        clone = deepcopy(store)
        clone["store_id"] = mapping[key]
        remote_clones.append(clone)
    return {
        "mapping": mapping,
        "remove_imported_ids": sorted(removed),
        "local_stores": local_clones,
        "remote_stores": remote_clones,
        "matched_local_ids": sorted(matched),
    }
