"""Offline regressions for delayed LIGHTNING visibility and server-bound rechecks."""
import copy
import json
from types import SimpleNamespace

import pytest

from backend.services import promocoes_api_participacoes as participation
from backend.services import mercadolivre_legacy_promocoes as promotions
from backend.services import promocoes_lightning_confirmacao as confirmation
from tests.test_promocoes_seller_campaign_reference import offline_runtime

TENANT = "tenant-confirmation-test"
STORE = "store-confirmation-test"
SELLER = "111"
JOB = "a" * 32
REF = "analysis:confirmation-test"
CAMPAIGN = "LGH-MLB999000002"
ITEM = "MLB999000123"
PRICE = 147.46
CFG = {"user_id": SELLER, "site_id": "MLB"}


def candidate(**changes):
    return {
        "id": CAMPAIGN, "item_id": ITEM, "type": "LIGHTNING", "status": "candidate",
        "ref_id": f"CANDIDATE-{ITEM}-TEST", "price": PRICE, "original_price": 199.88,
        "min_discounted_price": 39.98, "max_discounted_price": 179.90,
        "stock": {"min": 5, "max": 20},
        "_jk_campaign_id_consultado": CAMPAIGN, "_jk_item_id_consultado": ITEM,
        "_jk_promotion_type_consultado": "LIGHTNING", "_jk_contexto_promocao_confirmado": True,
        "_jk_lightning_confirmed_for_send": True,
        "_jk_lightning_preexisting_participations": [],
        **changes,
    }


def pending(**changes):
    return {
        "id": CAMPAIGN, "type": "LIGHTNING", "ref_id": f"OFFER-{ITEM}-TEST",
        "status": "pending", "price": PRICE, "original_price": 199.88,
        "stock": {"remaining_stock": 5}, **changes,
    }


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


@pytest.fixture
def io(monkeypatch, offline_runtime):
    state = SimpleNamespace(
        snapshots=[[candidate()], [candidate()], [pending()]], calls=[], posts=[],
        clock=FakeClock(), latency=0.0, status=200, lookup_calls=[],
    )

    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store) == (TENANT, STORE)
        if method == "POST":
            state.posts.append((url, copy.deepcopy(kwargs)))
            return SimpleNamespace(
                status_code=201, json=lambda: {"price": PRICE, "original_price": 199.88},
            ), cfg
        assert method == "GET"
        assert url == f"https://api.mercadolibre.com/seller-promotions/items/{ITEM}"
        state.calls.append(copy.deepcopy(kwargs))
        state.clock.value += state.latency
        snapshot = copy.deepcopy(state.snapshots[min(len(state.calls) - 1, len(state.snapshots) - 1)])
        if isinstance(snapshot, Exception):
            raise snapshot
        return SimpleNamespace(status_code=state.status, json=lambda: snapshot, text=""), cfg

    monkeypatch.setattr(participation, "_ml_api_request", request, raising=False)
    monkeypatch.setattr(promotions, "_ml_api_request", request, raising=False)
    monkeypatch.setattr(participation, "_ml_obter_promocoes_item", promotions._ml_obter_promocoes_item, raising=False)
    state.service = participation

    def reconcile(raw=None, **options):
        return confirmation.reconcile_lightning_participation(
            state.service, TENANT, STORE, dict(CFG), campaign=CAMPAIGN, item_id=ITEM,
            raw=raw or candidate(), price=PRICE, clock=state.clock, sleep=state.clock.sleep, **options,
        )

    state.reconcile = reconcile
    return state


def issue(raw=None, *, now=100.0, **changes):
    job = {}
    arguments = dict(
        client_id=TENANT, loja=STORE, seller_id=SELLER, analysis_job_id=JOB,
        client_ref=REF, campaign=CAMPAIGN, item_id=ITEM, raw=raw or candidate(), price=PRICE,
        now=now,
    )
    arguments.update(changes)
    record = confirmation.issue_confirmation_context(job, **arguments)
    return job, record


def mark_unknown(job, **changes):
    return confirmation.update_confirmation_context(
        job, TENANT, STORE, SELLER, REF, submission_attempted=True, outcome="unknown",
        now=100.0, **changes,
    )


def recheck(io, job, **options):
    return confirmation.recheck_confirmation_context(
        io.service, job, TENANT, STORE, dict(CFG), REF, now=100.0,
        clock=io.clock, sleep=io.clock.sleep, **options,
    )


def test_pending_offer_without_dates_is_confirmed_after_candidate_reads(io):
    matched, _cfg, metadata = io.reconcile()
    assert matched["status"] == "pending"
    assert matched["ref_id"] == f"OFFER-{ITEM}-TEST"
    assert metadata["read_count"] == 3
    assert metadata["elapsed_seconds"] == 2.0
    assert not metadata["budget_exhausted"]
    assert io.clock.sleeps == [0.5, 1.5]
    assert io.posts == []
    assert all(call["retry_unauthorized"] is False for call in io.calls)
    assert [call["timeout"] for call in io.calls] == [6.0, 5.5, 4.0]


def test_real_201_followed_by_delayed_visibility_never_reposts(io):
    io.snapshots = [[candidate()], [candidate()], [candidate()], [pending()]]
    raw = candidate()
    job, _record = issue(raw)
    details = {}
    ok, _message, cfg = participation._promo_aplicar_item_participacao_ml(
        TENANT, STORE, dict(CFG), item_id=ITEM, promotion_id=CAMPAIGN,
        promotion_type="LIGHTNING", deal_price=PRICE, stock=5,
        lightning_context=raw, official_price_context={"standard_price": 199.88, "sale_price": 153.29},
        result_details=details,
    )
    assert not ok and details["outcome"] == "unknown"
    assert details["submission_attempted"] is True
    assert len(io.posts) == 1
    mark_unknown(job)
    record, _cfg, metadata = recheck(io, job)
    assert metadata["read_count"] == 3
    assert record["outcome"] == "applied"
    assert record["confirmed"] == {
        "status": "pending", "lightning_start_date": "", "lightning_finish_date": "",
        "lightning_schedule_pending": True,
    }
    assert len(io.posts) == 1
    record, _cfg, replay = recheck(io, job)
    assert record["confirmed"]["status"] == "pending"
    assert replay["read_count"] == 0 and len(io.calls) == 4 and len(io.posts) == 1


@pytest.mark.parametrize("changes", [
    {"id": "LGH-MLB999000099"},
    {"campaign_id": "LGH-MLB999000099"},
    {"campaign": {"id": "LGH-MLB999000099"}},
    {"item_id": "MLB999000456"},
    {"type": "SELLER_CAMPAIGN"},
    {"promotion": {"type": "SMART"}},
    {"seller_id": "222"},
    {"offer": {"seller_id": "222"}},
    {"price": 147.45},
    {"price": True},
    {"price": "NaN"},
    {"price": "Infinity"},
    {"price": 0},
    {"status": "candidate"},
    {"status": "finished"},
    {"ref_id": f"OFFER-MLB999000456-FOREIGN"},
    {"ref_id": f"CANDIDATE-{ITEM}-TEST"},
    {"ref_id": ""},
    {"start_date": "2030-01-01T12:00:00"},
    {"start_date": 123, "finish_date": 124},
    {"start_date": "invalid", "finish_date": "2030-01-01T18:00:00"},
])
def test_foreign_inconsistent_or_incomplete_get_never_confirms(io, changes):
    io.snapshots = [[pending(**changes)]]
    matched, _cfg, metadata = io.reconcile(max_reads=1)
    assert matched is None and metadata["read_count"] == 1 and io.posts == []


@pytest.mark.parametrize("status_fields", [
    {"status": {"id": "pending"}},
    {"status": None, "statusItem": "pending"},
    {"status": None, "status_item": "pending"},
])
def test_valid_pending_status_aliases_are_confirmed(io, status_fields):
    io.snapshots = [[pending(**status_fields)]]
    job, _record = issue()
    mark_unknown(job)
    record, _cfg, _metadata = recheck(io, job)
    assert record["confirmed"]["status"] == "pending"


def test_duplicate_offer_conflicting_status_is_not_confirmed(io):
    io.snapshots = [[pending(), pending(status="started")]]
    matched, _cfg, _metadata = io.reconcile(max_reads=1)
    assert matched is None


def test_two_new_offer_references_are_ambiguous(io):
    io.snapshots = [[pending(), pending(ref_id=f"OFFER-{ITEM}-SECOND")]]
    matched, _cfg, _metadata = io.reconcile(max_reads=1)
    assert matched is None


def test_matching_selected_window_preserves_exact_offer_scope(io):
    selected = candidate(start_date="2030-01-01T12:00:00", finish_date="2030-01-01T18:00:00")
    wrong = pending(start_date="2030-01-02T12:00:00", finish_date="2030-01-02T18:00:00")
    right = pending(start_date=selected["start_date"], finish_date=selected["finish_date"])
    io.snapshots = [[wrong], [right]]
    matched, _cfg, metadata = io.reconcile(selected)
    assert matched["start_date"] == selected["start_date"] and metadata["read_count"] == 2


def test_known_window_never_confirms_an_undated_offer(io):
    selected = candidate(start_date="2030-01-01T12:00:00", finish_date="2030-01-01T18:00:00")
    io.snapshots = [[pending()]]
    matched, _cfg, _metadata = io.reconcile(selected, max_reads=1)
    assert matched is None


@pytest.mark.parametrize("previous", [
    pending(),
    pending(ref_id=f"OFFER-{ITEM}-OLDER"),
    pending(price=40, start_date="2030-01-01T12:00:00", finish_date="2030-01-01T18:00:00"),
])
def test_persisted_preexisting_reference_price_or_window_excludes_match(io, previous):
    raw = candidate(_jk_lightning_preexisting_participations=[previous])
    new = pending()
    if previous.get("start_date"):
        new.update(start_date=previous["start_date"], finish_date=previous["finish_date"])
    io.snapshots = [[new]]
    job, record = issue(raw)
    mark_unknown(job)
    assert record["raw"]["_jk_lightning_preexisting_participations"]
    resolved, _cfg, _metadata = recheck(io, job, max_reads=1)
    assert resolved["confirmed"] is None


def test_old_different_price_does_not_exclude_a_unique_new_offer(io):
    raw = candidate(_jk_lightning_preexisting_participations=[
        pending(price=40, ref_id=f"OFFER-{ITEM}-OLDER"),
    ])
    io.snapshots = [[pending()]]
    job, _record = issue(raw)
    mark_unknown(job)
    resolved, _cfg, _metadata = recheck(io, job, max_reads=1)
    assert resolved["confirmed"]["status"] == "pending"


def test_transient_get_error_is_retried_only_by_reads(io):
    io.snapshots = [TimeoutError("synthetic"), [candidate()], [pending()]]
    matched, _cfg, metadata = io.reconcile()
    assert matched["status"] == "pending" and metadata["read_count"] == 3 and io.posts == []


def test_deadline_prevents_reads_and_sleep_beyond_budget(io):
    io.latency = 1.0
    io.snapshots = [[candidate()]]
    matched, _cfg, metadata = io.reconcile(timeout_seconds=2.0)
    assert matched is None and metadata["read_count"] == 2
    assert metadata["budget_exhausted"]
    assert io.clock.sleeps == [0.5]
    assert [call["timeout"] for call in io.calls] == [2.0, 0.5]


def test_late_response_is_not_accepted_outside_budget(io):
    io.snapshots = [[pending()]]
    io.latency = 7.0
    matched, _cfg, metadata = io.reconcile()
    assert matched is None and metadata["budget_exhausted"] and len(io.calls) == 1


def test_backoff_larger_than_remaining_budget_stops_without_sleep(io):
    io.snapshots = [[candidate()]]
    matched, _cfg, metadata = io.reconcile(timeout_seconds=.2)
    assert matched is None and metadata["read_count"] == 1 and metadata["budget_exhausted"]
    assert io.clock.sleeps == []


def test_401_does_not_refresh_token_or_send(io):
    io.status = 401
    matched, _cfg, metadata = io.reconcile(max_reads=1)
    assert matched is None and metadata["read_count"] == 1
    assert io.calls[0]["retry_unauthorized"] is False and io.posts == []


@pytest.mark.parametrize("attempted_method,attempted_url,changes", [
    ("POST", f"https://api.mercadolibre.com/seller-promotions/items/{ITEM}", {}),
    ("GET", "https://api.mercadolibre.com/users/me", {}),
    ("GET", f"https://api.mercadolibre.com/seller-promotions/items/{ITEM}", {"json": {"secret": "value"}}),
    ("GET", f"https://api.mercadolibre.com/seller-promotions/items/{ITEM}", {"params": {"app_version": "v1"}}),
])
def test_request_wrapper_enforces_method_endpoint_and_payload(io, attempted_method, attempted_url, changes):
    def hostile_getter(client, store, cfg, item, *, request_fn, **kwargs):
        options = {"params": {"app_version": "v2"}}
        options.update(changes)
        return request_fn(client, store, cfg, attempted_method, attempted_url, **options)

    service = SimpleNamespace(**vars(io.service)) if hasattr(io.service, "__dict__") else io.service
    service._ml_obter_promocoes_item = hostile_getter
    matched, _cfg, metadata = confirmation.reconcile_lightning_participation(
        service, TENANT, STORE, dict(CFG), campaign=CAMPAIGN, item_id=ITEM,
        raw=candidate(), price=PRICE, max_reads=1, clock=io.clock, sleep=io.clock.sleep,
    )
    assert matched is None and metadata["read_count"] == 1
    assert io.calls == io.posts == []


def test_context_contains_only_minimal_server_confirmation_data():
    raw = candidate(
        access_token="synthetic-secret", note="synthetic-private",
        shipping={"secret": "synthetic-private"}, seller_receives=999,
        _jk_lightning_preexisting_participations=[
            pending(access_token="synthetic-secret", hidden={"secret": "synthetic-private"}),
        ],
    )
    job, record = issue(raw)
    encoded = json.dumps(job)
    assert "synthetic-secret" not in encoded and "synthetic-private" not in encoded
    assert "shipping" not in encoded and "seller_receives" not in encoded
    assert record["price_cents"] == 14746
    assert record["expires_at"] - record["issued_at"] == 86400
    record["raw"]["ref_id"] = "caller-mutated"
    assert job[confirmation.CONTEXT_KEY][REF]["raw"]["ref_id"] != "caller-mutated"
    assert not confirmation.resolve_confirmation_context(job, TENANT, STORE, SELLER, REF, now=100)["pollable"]


@pytest.mark.parametrize("client,store,seller,ref", [
    ("tenant-other", STORE, SELLER, REF),
    (TENANT, "store-other", SELLER, REF),
    (TENANT, STORE, "222", REF),
    (TENANT, STORE, SELLER, "analysis:other"),
])
def test_context_cannot_be_replayed_in_other_tenant_store_seller_or_row(client, store, seller, ref):
    job, _record = issue()
    with pytest.raises(confirmation.ConfirmationContextError):
        confirmation.resolve_confirmation_context(job, client, store, seller, ref, now=100)


@pytest.mark.parametrize("now", [99.9, 86500, 86501])
def test_context_rejects_clock_before_issue_and_expiration(now):
    job, _record = issue()
    with pytest.raises(confirmation.ConfirmationContextError):
        confirmation.resolve_confirmation_context(job, TENANT, STORE, SELLER, REF, now=now)


@pytest.mark.parametrize("changes", [
    {"analysis_job_id": "../foreign"}, {"client_ref": ""}, {"campaign": ""},
    {"item_id": "MLA999000123"}, {"price": True}, {"price": "NaN"},
    {"price": "Infinity"}, {"price": 0}, {"ttl_seconds": 0}, {"ttl_seconds": 86401},
    {"raw": candidate(start_date="2030-01-01T12:00:00")},
])
def test_issue_rejects_invalid_binding_price_and_calendar(changes):
    with pytest.raises(confirmation.ConfirmationContextError):
        issue(**changes)


@pytest.mark.parametrize("changes", [
    {"campaign": "LGH-MLB999000099"}, {"item_id": "MLB999000456"}, {"price": PRICE + .01},
])
def test_duplicate_client_ref_cannot_overwrite_previous_context(changes):
    job, before = issue()
    with pytest.raises(confirmation.ConfirmationContextError):
        confirmation.issue_confirmation_context(
            job, TENANT, STORE, SELLER, JOB, REF,
            changes.get("campaign", CAMPAIGN), changes.get("item_id", ITEM), candidate(),
            changes.get("price", PRICE), now=100,
        )
    assert job[confirmation.CONTEXT_KEY][REF] == before


@pytest.mark.parametrize("corruption", [
    {"expires_at": None}, {"issued_at": None}, {"price_cents": True},
    {"analysis_job_id": "../foreign"}, {"raw": None}, {"version": 99},
])
def test_incomplete_canonical_context_is_not_authority(corruption):
    job, _record = issue()
    job[confirmation.CONTEXT_KEY][REF].update(corruption)
    with pytest.raises(confirmation.ConfirmationContextError):
        confirmation.resolve_confirmation_context(job, TENANT, STORE, SELLER, REF, now=100)


@pytest.mark.parametrize("outcome,attempted", [("prepared", False), ("blocked", False), ("rejected", True)])
def test_no_get_for_a_non_submitted_or_rejected_context(io, outcome, attempted):
    job, _record = issue()
    confirmation.update_confirmation_context(
        job, TENANT, STORE, SELLER, REF, submission_attempted=attempted, outcome=outcome, now=100,
    )
    record, _cfg, metadata = recheck(io, job)
    assert not record["pollable"] and metadata["read_count"] == 0
    assert io.calls == io.posts == []


def test_confirmation_is_monotonic_under_replay(io):
    io.snapshots = [[pending()]]
    job, _record = issue()
    mark_unknown(job)
    confirmed, _cfg, _metadata = recheck(io, job)
    replay = confirmation.update_confirmation_context(
        job, TENANT, STORE, SELLER, REF, submission_attempted=False, outcome="rejected", now=100,
    )
    assert replay == confirmed and replay["confirmed"]["status"] == "pending"


def test_context_survives_json_reload_with_owner_binding(io):
    job, _record = issue()
    mark_unknown(job)
    reloaded = json.loads(json.dumps(job))
    io.snapshots = [[pending()]]
    record, _cfg, _metadata = recheck(io, reloaded)
    assert record["confirmed"]["status"] == "pending"
    assert reloaded[confirmation.CONTEXT_KEY][REF]["outcome"] == "applied"
