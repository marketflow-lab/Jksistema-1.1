"""Shared quota invariants, without provider calls or a live Firestore project."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading

import pytest

from cloud.auth_gateway.app.central_accounts import CentralError
from cloud.auth_gateway.app.central_bling_quota import BlingCompanyQuota, _Permit, retry_after_seconds
from cloud.auth_gateway.app.central_store import FirestoreDocuments, MemoryDocuments
from cloud.auth_gateway.app.errors import GatewayUnavailable


class Clock:
    def __init__(self):
        self.now = 1800000000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def quota(documents=None, clock=None, **kwargs):
    clock = clock or Clock()
    return BlingCompanyQuota(documents or MemoryDocuments(), clock=clock,
                             sleeper=clock.sleep, monotonic=clock, **kwargs), clock


def test_same_company_shares_spacing_between_instances_and_token_generations():
    documents = MemoryDocuments()
    first, clock = quota(documents)
    second, _ = quota(documents, clock)
    starts = []
    for limiter in (first, second, first):
        with limiter.account("same-company"):
            starts.append(clock())
    assert starts[1] - starts[0] == pytest.approx(0.35, abs=1e-6)
    assert starts[2] - starts[1] == pytest.approx(0.35, abs=1e-6)
    assert len(documents._documents) == 1
    assert "same-company" not in repr(documents._documents)


def test_hexadecimal_company_id_case_does_not_split_the_quota():
    limiter, _ = quota()
    assert limiter.account_ref("436C56A5679921F5F13A3D6433561773") == limiter.account_ref("436c56a5679921f5f13a3d6433561773")
    assert limiter.account_ref("opaque-A") != limiter.account_ref("opaque-a")


def test_different_companies_do_not_wait_for_each_other():
    limiter, clock = quota()
    start = clock()
    with limiter.account("company-a"):
        with limiter.account("company-b"):
            assert clock() == start


@pytest.mark.parametrize("response_latency", [0.0, 0.2, 0.5])
def test_minimum_interval_is_measured_between_starts_without_post_response_delay(response_latency):
    limiter, clock = quota()
    start = clock()
    with limiter.account("a"):
        clock.sleep(response_latency)
    with limiter.account("a"):
        assert clock() - start == pytest.approx(max(0.35, response_latency), abs=1e-6)


@pytest.mark.parametrize("coordination_latency", [0.02, 0.05])
def test_company_spacing_hides_coordination_latency_within_existing_interval(coordination_latency):
    clock = Clock()

    class Documents(MemoryDocuments):
        def change_many(self, references, transform):
            result = super().change_many(references, transform)
            clock.sleep(coordination_latency)
            return result

    limiter, _ = quota(Documents(), clock)
    starts = []
    for _ in range(10):
        with limiter.account("a"):
            starts.append(clock())
            clock.sleep(0.2)
    assert all(later - earlier == pytest.approx(0.35, abs=1e-6)
               for earlier, later in zip(starts, starts[1:]))


def test_delayed_grant_cannot_send_after_discovery_drain_window():
    clock = Clock()

    class Documents(MemoryDocuments):
        def change_many(self, references, transform):
            result = super().change_many(references, transform)
            if "account" in result and result["account"].get("owner"):
                clock.sleep(1.1)
            return result

    limiter, _ = quota(Documents(), clock)
    with pytest.raises(CentralError):
        with limiter.account("a"):
            pytest.fail("a suspended grant must be reacquired before network")


def test_same_company_has_only_one_inflight_provider_call_across_replicas():
    documents = MemoryDocuments()
    first = BlingCompanyQuota(documents, wait_budget=0)
    second = BlingCompanyQuota(documents, wait_budget=0)
    barrier = threading.Barrier(8)
    release = threading.Event()

    def run(index):
        barrier.wait()
        try:
            with (first if index % 2 else second).account("same-company"):
                release.wait(2)
                return "admitted"
        except CentralError as exc:
            assert exc.status == 429
            return "limited"

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(run, index) for index in range(8)]
        # Losers complete while the sole winner holds its provider lease.
        while sum(future.done() for future in futures) < 7:
            release.wait(0.01)
        release.set()
        results = [future.result(timeout=3) for future in futures]
    assert results.count("admitted") == 1


def test_retry_after_blocks_company_in_other_replica_but_not_other_accounts():
    documents = MemoryDocuments()
    first, clock = quota(documents)
    second, _ = quota(documents, clock)
    with first.account("same-company"):
        first.block("same-company", {"retry-after": "17"})
    with pytest.raises(CentralError) as error:
        with second.account("same-company"):
            pytest.fail("provider must not be called during Retry-After")
    assert error.value.headers == {"Retry-After": "17"}
    with second.account("other-company"):
        pass
    clock.sleep(17)
    with second.account("same-company"):
        pass


def test_unknown_discovery_excludes_every_identified_company():
    documents = MemoryDocuments()
    first, clock = quota(documents)
    second, _ = quota(documents, clock, wait_budget=0)
    before = clock()
    with first.discovery():
        assert clock() - before >= 1.0
        for company in ("a", "b"):
            with pytest.raises(CentralError):
                with second.account(company):
                    pytest.fail("unknown credentials can belong to this known company")
    with pytest.raises(CentralError):
        with second.account("a"):
            pytest.fail("discovery release must retain a quiet interval")
    clock.sleep(0.35)
    with second.account("a"):
        pass


def test_discovery_respects_spacing_after_latest_permitted_delayed_future_slot():
    clock = Clock()
    limiter, _ = quota(clock=clock)
    began = clock()
    limiter.documents.change(*limiter.account_ref("a"), lambda _: {"next_allowed_at": began + 0.35})
    grant = limiter._claim("a", "known-owner")["account"]
    permit = _Permit(clock, grant["slot_at"])
    starts = []

    def sleep_with_delayed_known_start(seconds):
        end = clock() + seconds
        clock.now = grant["slot_at"] + 0.5
        permit.start()
        starts.append(clock())
        clock.now = end

    limiter.sleep = sleep_with_delayed_known_start
    with limiter.discovery():
        assert clock() - starts[0] >= 0.349999


def test_unknown_429_keeps_global_barrier_for_retry_after():
    limiter, clock = quota(wait_budget=0)
    with pytest.raises(CentralError):
        with limiter.discovery():
            raise CentralError("central_provider_rate_limited", 429, headers={"Retry-After": "21"})
    with pytest.raises(CentralError) as error:
        with limiter.account("any-company"):
            pytest.fail("unknown account throttle must not be bypassed by a known alias")
    assert error.value.headers == {"Retry-After": "21"}
    clock.sleep(21)
    with limiter.account("any-company"):
        pass


def test_expired_owner_cannot_release_a_new_lease():
    limiter, clock = quota()
    limiter._claim("a", "old")
    clock.sleep(46)
    limiter._claim("a", "new")
    limiter._release("a", "old")
    assert limiter.documents.get(*limiter.account_ref("a"))["owner"] == "new"


def test_provider_exception_keeps_spacing_and_releases_only_its_lease():
    limiter, clock = quota()
    with pytest.raises(RuntimeError):
        with limiter.account("a"):
            raise RuntimeError("provider failure")
    assert limiter.documents.get(*limiter.account_ref("a"))["owner"] == ""
    before = clock()
    with limiter.account("a"):
        assert clock() - before >= 0.349


def test_coordinator_failure_prevents_provider_call():
    class Broken:
        def change_many(self, *_args):
            raise RuntimeError("database is unavailable")

    limiter, _ = quota(Broken())
    with pytest.raises(GatewayUnavailable):
        with limiter.account("a"):
            pytest.fail("fail closed")


def test_refresh_admission_failure_is_distinct_from_consuming_a_token(monkeypatch):
    from cloud.auth_gateway.app.central_provider import ProviderTransport

    class Broken:
        def change_many(self, *_args):
            raise RuntimeError("database is unavailable")

    limiter, _ = quota(Broken())
    provider = ProviderTransport(bling_quota=limiter)
    monkeypatch.setattr(provider, "_token", lambda *_args: pytest.fail("must not send token POST"))
    with pytest.raises(CentralError) as error:
        provider.refresh({"provider": "bling", "seller_id": "a", "app_id": "app",
                          "app_secret": "secret", "refresh_token": "refresh"})
    assert error.value.code == "central_quota_unavailable"
    assert error.value.headers == {"Retry-After": "2"}


@pytest.mark.parametrize("identity", ["", None, "bad/id", "bad\nidentity", "a" * 129])
def test_missing_or_untrusted_company_identity_is_never_a_separate_bucket(identity):
    limiter, _ = quota()
    with pytest.raises(CentralError):
        with limiter.account(identity):
            pytest.fail("company identity must be confirmed before admission")


def test_retry_after_accepts_dates_and_rejects_nonfinite_values():
    assert retry_after_seconds({"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"},
                               datetime(2015, 10, 21, 7, 27, 53, tzinfo=timezone.utc).timestamp()) == 7
    assert retry_after_seconds({"Retry-After": "nan"}, 0, default=60) == 60


def test_multi_document_memory_transform_rolls_back_on_failure():
    documents = MemoryDocuments()
    documents.change("group", "one", lambda _: {"value": 1})
    references = {"one": ("group", "one"), "two": ("group", "two")}

    def fail(rows):
        rows["one"]["value"] = 2
        raise RuntimeError("conflict")

    with pytest.raises(RuntimeError):
        documents.change_many(references, fail)
    assert documents.get("group", "one") == {"value": 1}
    assert documents.get("group", "two") is None


def test_firestore_reads_barrier_and_company_before_writing_and_retries(monkeypatch):
    from firebase_admin import firestore
    events = []

    class Reference:
        def __init__(self, key):
            self.key = key

        def get(self, **kwargs):
            assert kwargs["retry"] is None
            events.append(("read", self.key))
            return self

        def to_dict(self):
            return None

    class Client:
        def collection(self, group):
            assert group == "jk_central_v1_provider_quota"
            return self

        def document(self, key):
            return Reference(key)

        def transaction(self, **kwargs):
            assert kwargs == {"max_attempts": 3}
            return self

        def set(self, ref, value):
            events.append(("write", ref.key))

    monkeypatch.setattr(firestore, "transactional", lambda fn: fn)
    documents = FirestoreDocuments(Client())
    documents.change_many({"gate": ("provider_quota", "gate"), "account": ("provider_quota", "a")},
                          lambda rows: {"account": {"next_allowed_at": 1}})
    assert events == [("read", "gate"), ("read", "a"), ("write", "a")]
