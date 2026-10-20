from copy import deepcopy

import pytest
from fastapi import HTTPException

from backend.services.shared_sync_store_identity import plan_store_reconciliation


INVALID = "shared_sync_store_reconciliation_invalid"
AMBIGUOUS = "shared_sync_store_reconciliation_ambiguous"


def store(store_id=None, name="Loja Igual", token="synthetic-connection"):
    result = {"nome": name, "integracoes": {"ml": {"access_token": token}}, "extra": [1, {"x": 2}]}
    if store_id is not None:
        result["store_id"] = store_id
    return result


def accounts(local=None, remote=None):
    return {"local": local or {}, "remote": remote or {}}


def ml(account="account-a", site="MLB"):
    return {"ml": [account, site]}


def both(account="account-a", company="company-a"):
    return {**ml(account), "bling": [company, ""]}


def assert_failure(local, remote, *, code=AMBIGUOUS, **kwargs):
    before = deepcopy((local, remote, kwargs))
    with pytest.raises(HTTPException) as captured:
        plan_store_reconciliation(local, remote, **kwargs)
    assert captured.value.status_code == 409
    assert captured.value.detail["code"] == code
    assert (local, remote, kwargs) == before
    return captured.value.detail


def test_same_authenticated_accounts_keep_original_id_and_full_connections():
    local = [store("local", token="synthetic-local")]
    remote = [store("remote", name="Nome remoto", token="synthetic-remote")]
    evidence = accounts({"local": both()}, {"remote": both()})
    before = deepcopy((local, remote, evidence))

    result = plan_store_reconciliation(local, remote, verified_accounts=evidence)

    assert result["mapping"] == {"remote": "local"}
    assert result["matched_local_ids"] == ["local"]
    assert result["remove_imported_ids"] == []
    assert result["local_stores"] == local
    assert result["remote_stores"] == [{**remote[0], "store_id": "local"}]
    assert (local, remote, evidence) == before
    result["local_stores"][0]["extra"][1]["x"] = 9
    result["remote_stores"][0]["integracoes"]["ml"]["access_token"] = "changed-clone"
    assert (local, remote, evidence) == before


def test_one_common_authenticated_provider_is_enough():
    result = plan_store_reconciliation(
        [store("local")], [store("remote")],
        verified_accounts=accounts({"local": ml()}, {"remote": both()}),
    )
    assert result["mapping"] == {"remote": "local"}


@pytest.mark.parametrize("evidence", [
    accounts(),
    accounts({"local": ml("other-account")}, {"remote": ml()}),
    accounts({"local": ml(site="MLA")}, {"remote": ml()}),
    accounts({"local": ml()}, {"remote": {"bling": ["account-a", "MLB"]}}),
])
def test_same_name_tokens_or_app_without_common_identity_does_not_match(evidence):
    local, remote = [store("local")], [store("remote")]
    for item in local + remote:
        item["integracoes"]["ml"].update(user_id="unverified", app_id="same-app")
    result = plan_store_reconciliation(local, remote, verified_accounts=evidence)
    assert result["mapping"] == {"remote": "remote"}
    assert result["local_stores"] == local
    assert result["matched_local_ids"] == []


def test_provider_conflict_blocks_even_if_other_provider_matches():
    assert_failure(
        [store("local")], [store("remote")],
        verified_accounts=accounts({"local": both(company="other-company")}, {"remote": both()}),
    )


def test_exact_id_retains_normal_merge_without_account_reconciliation():
    result = plan_store_reconciliation(
        [store("same")], [store("same")],
        verified_accounts=accounts({"same": ml("old-account")}, {"same": ml("new-account")}),
    )
    assert result["mapping"] == {"same": "same"}
    assert result["matched_local_ids"] == []
    assert result["remove_imported_ids"] == []


def test_exact_id_does_not_remove_preexisting_matching_store_without_import_proof():
    local = [store("first"), store("same")]
    result = plan_store_reconciliation(
        local, [store("same")],
        verified_accounts=accounts({"first": ml(), "same": ml()}, {"same": ml()}),
    )
    assert result["mapping"] == {"same": "same"}
    assert result["local_stores"] == local
    assert result["remove_imported_ids"] == []


def test_proven_duplicate_is_removed_only_from_detached_plan():
    local = [store("original", token="synthetic-original"), store("remote", token="synthetic-imported")]
    remote = [store("remote")]
    kwargs = dict(
        verified_accounts=accounts({"original": both(), "remote": both()}, {"remote": both()}),
        preimport_store_ids={"original"}, removable_imported_ids={"remote"},
    )
    before = deepcopy((local, remote, kwargs))
    result = plan_store_reconciliation(local, remote, **kwargs)
    assert result["mapping"] == {"remote": "original"}
    assert result["remove_imported_ids"] == ["remote"]
    assert result["local_stores"] == [local[0]]
    assert result["matched_local_ids"] == ["original"]
    assert (local, remote, kwargs) == before


@pytest.mark.parametrize("removable", [None, set(), {"original"}])
def test_duplicate_requires_explicit_removal_proof(removable):
    assert_failure(
        [store("original"), store("remote")], [store("remote")],
        code=INVALID if removable == {"original"} else AMBIGUOUS,
        verified_accounts=accounts({"original": ml(), "remote": ml()}, {"remote": ml()}),
        preimport_store_ids={"original"}, removable_imported_ids=removable,
    )


def test_duplicate_without_unique_original_is_ambiguous():
    assert_failure(
        [store("first"), store("second"), store("remote")], [store("remote")],
        verified_accounts=accounts({key: ml() for key in ("first", "second", "remote")}, {"remote": ml()}),
        preimport_store_ids={"first", "second"}, removable_imported_ids={"remote"},
    )


def test_duplicate_removal_requires_pairwise_comparable_local_identities():
    assert_failure(
        [store("original"), store("remote")], [store("remote")],
        verified_accounts=accounts(
            {"original": ml(), "remote": {"bling": ["company-a", ""]}}, {"remote": both()},
        ),
        preimport_store_ids={"original"}, removable_imported_ids={"remote"},
    )


def test_duplicate_removal_requires_verified_identity_of_redundant_record():
    assert_failure(
        [store("original"), store("remote")], [store("remote")],
        verified_accounts=accounts({"original": ml()}, {"remote": ml()}),
        preimport_store_ids={"original"}, removable_imported_ids={"remote"},
    )


def test_distinct_homonyms_are_preserved_during_repair():
    local = [store("original"), store("remote"), store("unrelated")]
    result = plan_store_reconciliation(
        local, [store("remote")],
        verified_accounts=accounts({"original": ml(), "remote": ml(), "unrelated": ml("different")}, {"remote": ml()}),
        preimport_store_ids={"original", "unrelated"}, removable_imported_ids={"remote"},
    )
    assert result["local_stores"] == [local[0], local[2]]
    assert result["remove_imported_ids"] == ["remote"]


def test_legacy_name_key_uses_existing_accent_and_punctuation_normalization():
    result = plan_store_reconciliation(
        [store("original", name="Outro nome")], [store(name="  JÓÃO & Cia. ")],
        verified_accounts=accounts({"original": ml()}, {"legacy:joaocia": ml()}),
    )
    assert result["mapping"] == {"legacy:joaocia": "original"}
    assert result["remote_stores"][0]["store_id"] == "original"


def test_legacy_without_proof_never_invents_an_id():
    assert_failure([store("local")], [store()], code=INVALID, verified_accounts=accounts())


def test_legacy_duplicate_normalized_names_fail_before_matching():
    assert_failure([], [store(name="João"), store(name="Joao!")], verified_accounts=accounts())


def test_previous_mapping_with_current_proof_is_idempotent():
    kwargs = dict(
        verified_accounts=accounts({"local": both()}, {"remote": both()}),
        previous_mapping={"remote": "local"},
    )
    local, remote = [store("local")], [store("remote")]
    first = plan_store_reconciliation(local, remote, **kwargs)
    second = plan_store_reconciliation(first["local_stores"], remote, **kwargs)
    assert first == second
    assert first["matched_local_ids"] == ["local"]


@pytest.mark.parametrize("local_ids,mapping,evidence", [
    (["local"], {"remote": "deleted"}, accounts({"local": ml()}, {"remote": ml()})),
    (["local"], {"absent-remote": "local"}, accounts({"local": ml()}, {"remote": ml()})),
    (["local"], {"remote": "local"}, accounts()),
    (["local"], {"remote": "local"}, accounts({"local": ml("changed")}, {"remote": ml()})),
    (["local"], {"remote": "local"}, accounts({"local": both(company="changed")}, {"remote": both()})),
])
def test_orphan_or_unverified_previous_mapping_fails(local_ids, mapping, evidence):
    assert_failure(
        [store(key) for key in local_ids], [store("remote")], code=INVALID,
        verified_accounts=evidence, previous_mapping=mapping,
    )


def test_previous_mapping_does_not_authorize_removing_another_candidate():
    assert_failure(
        [store("local"), store("remote")], [store("remote")],
        verified_accounts=accounts({"local": ml(), "remote": ml()}, {"remote": ml()}),
        previous_mapping={"remote": "local"},
    )


def test_previous_mapping_can_repair_an_independently_proven_duplicate():
    result = plan_store_reconciliation(
        [store("local"), store("remote")], [store("remote")],
        verified_accounts=accounts({"local": ml(), "remote": ml()}, {"remote": ml()}),
        previous_mapping={"remote": "local"},
        preimport_store_ids={"local"}, removable_imported_ids={"remote"},
    )
    assert result["mapping"] == {"remote": "local"}
    assert result["remove_imported_ids"] == ["remote"]


def test_two_remote_ids_for_same_account_fail_even_with_exact_local_ids():
    assert_failure(
        [store("a"), store("b")], [store("a"), store("b")],
        verified_accounts=accounts({"a": ml(), "b": ml()}, {"a": ml(), "b": ml()}),
    )


def test_remote_conflicting_providers_fail_even_without_local_stores():
    assert_failure(
        [], [store("a"), store("b")],
        verified_accounts=accounts(remote={"a": both(), "b": both(company="different")}),
    )


def test_disjoint_remote_providers_cannot_map_many_to_one():
    assert_failure(
        [store("local")], [store("a"), store("b")],
        verified_accounts=accounts({"local": both()}, {"a": ml(), "b": {"bling": ["company-a", ""]}}),
    )


def test_conflicting_candidate_is_not_silently_skipped_for_a_good_match():
    assert_failure(
        [store("a"), store("b")], [store("remote")],
        verified_accounts=accounts({"a": both(), "b": both(company="different")}, {"remote": both()}),
    )


@pytest.mark.parametrize("local,remote", [
    ([store()], [store("remote")]),
    ([store(True)], [store("remote")]),
    ([store("local")], [store({"invalid": "id"})]),
    ([store("local")], [store(name="!!!")]),
    ([42], []),
    ([], [42]),
    ({}, []),
])
def test_invalid_records_fail_closed(local, remote):
    assert_failure(local, remote, code=INVALID, verified_accounts=accounts())


@pytest.mark.parametrize("local,remote", [
    ([store("local"), store("local")], []),
    ([], [store("remote"), store("remote")]),
])
def test_duplicate_ids_fail_closed(local, remote):
    assert_failure(local, remote, verified_accounts=accounts())


@pytest.mark.parametrize("evidence", [
    None,
    {"local": []},
    {"local": {"absent": ml()}},
    {"remote": {"remote": {"ML": ["account", "MLB"]}}},
    {"remote": {"remote": {"ml": ["account"]}}},
    {"remote": {"remote": {"ml": ["", "MLB"]}}},
    {"remote": {"remote": {"ml": [123, "MLB"]}}},
    {"remote": {"remote": {"ml": ["account", None]}}},
    {"remote": {"remote": {"ml": [" account ", "MLB"]}}},
    {"other-tenant": {}},
])
def test_malformed_authenticated_evidence_fails_closed(evidence):
    assert_failure([store("local")], [store("remote")], code=INVALID, verified_accounts=evidence)


def test_errors_contain_no_record_or_identity_values():
    detail = assert_failure(
        [store("private-local", name="private-name", token="private-token")],
        [store("private-remote")],
        verified_accounts=accounts(
            {"private-local": both("private-account", "private-company")},
            {"private-remote": both("private-account", "other-private-company")},
        ),
    )
    assert "private" not in str(detail)


def test_empty_snapshot_has_no_side_effects_or_implicit_deletions():
    local = [store("local")]
    result = plan_store_reconciliation(local, [], verified_accounts=accounts())
    assert result == {
        "mapping": {}, "remove_imported_ids": [], "local_stores": local,
        "remote_stores": [], "matched_local_ids": [],
    }


def test_previous_mapping_cannot_ignore_an_unverified_exact_id_duplicate():
    assert_failure(
        [store("local"), store("remote")], [store("remote")],
        verified_accounts=accounts({"local": ml()}, {"remote": ml()}),
        previous_mapping={"remote": "local"},
        preimport_store_ids={"local"}, removable_imported_ids={"remote"},
    )
