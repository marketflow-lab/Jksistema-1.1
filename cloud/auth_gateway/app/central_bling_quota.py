"""Company-wide Bling admission shared by Cloud Run replicas and desktops.

Only public identity hashes and quota metadata are persisted. Transactions
contain no sleeps, provider requests, token rotation or other side effects.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import re
import secrets
import time

from .central_accounts import CentralError, key_for
from .errors import GatewayUnavailable


MIN_INTERVAL = 0.35
LEASE_SECONDS = 45.0
MAX_GRANT_DELAY_SECONDS = 0.5
# A preceding grant can reserve +350 ms and dispatch +500 ms late. Discovery
# must then leave another full 350 ms before its own unknown-company request.
DISCOVERY_DRAIN_SECONDS = 2 * MIN_INTERVAL + MAX_GRANT_DELAY_SECONDS
CAPABILITY_HEADER = "X-JK-Bling-Quota"
CAPABILITY_VALUE = "company-v1"


def retry_after_seconds(headers, now, default=1.0):
    raw = next((str(value) for key, value in (headers or {}).items()
                if str(key).lower() == "retry-after"), "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        try:
            value = parsedate_to_datetime(raw).timestamp() - now
        except (TypeError, ValueError, OverflowError):
            value = default
    return max(0.0, value) if math.isfinite(value) else float(default)


def rate_limited(after):
    return CentralError("central_provider_rate_limited", 429,
                        headers={"Retry-After": str(max(1, math.ceil(after)))})


class _Busy(Exception):
    def __init__(self, after, *, blocked=False):
        self.after, self.blocked = max(0.01, float(after)), blocked


class _Permit:
    def __init__(self, clock, slot_at):
        self.clock = clock
        self.slot_at = slot_at
        self.started_at = clock()

    def start(self):
        if self.clock() > self.slot_at + MAX_GRANT_DELAY_SECONDS:
            raise rate_limited(MIN_INTERVAL)
        self.started_at = self.clock()


class BlingCompanyQuota:
    def __init__(self, documents, *, clock=time.time, sleeper=time.sleep,
                 monotonic=time.monotonic, wait_budget=3.0):
        self.documents, self.clock, self.sleep = documents, clock, sleeper
        self.monotonic, self.wait_budget = monotonic, max(0.0, float(wait_budget))
        self.gate_ref = ("provider_quota", key_for("bling", "identity-discovery-v1"))

    @staticmethod
    def _company(company):
        value = str(company or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise CentralError("central_account_identity_invalid", 502)
        return value.lower() if re.fullmatch(r"[A-Fa-f0-9]{32}", value) else value

    def account_ref(self, company):
        return "provider_quota", key_for("bling", "company-v1", self._company(company))

    def _transaction(self, references, transform):
        try:
            return self.documents.change_many(references, transform)
        except (_Busy, CentralError):
            raise
        except Exception:
            # A failed coordinator must never become an unthrottled request.
            raise GatewayUnavailable("central_quota_unavailable") from None

    def _wait_for(self, claim):
        deadline = self.monotonic() + self.wait_budget
        while True:
            began = self.monotonic()
            try:
                return claim()
            except _Busy as busy:
                remaining = deadline - self.monotonic()
                if remaining <= 0 or (busy.blocked and busy.after > remaining):
                    raise rate_limited(busy.after) from None
                delay = max(0.0, busy.after - (self.monotonic() - began))
                if delay:
                    self.sleep(min(remaining, delay, MIN_INTERVAL))

    def _claim(self, company, owner):
        now = self.clock()
        refs = {"gate": self.gate_ref, "account": self.account_ref(company)}

        def claim(rows):
            gate, account = rows["gate"] or {}, rows["account"] or {}
            if gate.get("lease_until", 0) > now or gate.get("quiet_until", 0) > now:
                raise _Busy(max(gate.get("lease_until", 0), gate.get("quiet_until", 0)) - now)
            if account.get("blocked_until", 0) > now:
                raise _Busy(account["blocked_until"] - now, blocked=True)
            if account.get("lease_until", 0) > now:
                raise _Busy(min(MIN_INTERVAL, account["lease_until"] - now))
            slot_at = max(now, account.get("next_allowed_at", 0))
            # Reserve the immediately next slot and wait outside the transaction.
            # This hides coordination latency behind the existing 350 ms gap.
            return {"account": {**account, "owner": owner, "lease_until": slot_at + LEASE_SECONDS,
                                "slot_at": slot_at, "next_allowed_at": slot_at + MIN_INTERVAL,
                                "delete_after": datetime.fromtimestamp(now + 7 * 86400, timezone.utc)}}

        return self._transaction(refs, claim)

    def _release(self, company, owner, started_at=None):
        now = self.clock()
        next_allowed_at = (now if started_at is None else started_at) + MIN_INTERVAL

        def release(rows):
            account = rows["account"] or {}
            if account.get("owner") != owner:
                return {}
            return {"account": {**account, "owner": "", "lease_until": 0,
                                "next_allowed_at": max(account.get("next_allowed_at", 0), next_allowed_at)}}

        self._transaction({"account": self.account_ref(company)}, release)

    @contextmanager
    def account(self, company):
        company = self._company(company)
        owner = secrets.token_hex(16)
        grant = self._wait_for(lambda: self._claim(company, owner))["account"]
        permit = _Permit(self.clock, grant["slot_at"])
        try:
            wait = grant["slot_at"] - self.clock()
            if wait > 0:
                self.sleep(wait)
            # Known grants may start at most 350+500 ms after claiming. The
            # discovery barrier drains for 1.2 seconds, so delayed permits cannot
            # start during an unknown request in another replica.
            permit.start()
            yield permit
        finally:
            self._release(company, owner, permit.started_at)

    def block(self, company, headers=None):
        now = self.clock()
        after = retry_after_seconds(headers, now, default=1.0)

        def block(rows):
            account = rows["account"] or {}
            return {"account": {**account, "blocked_until": max(account.get("blocked_until", 0), now + after)}}

        self._transaction({"account": self.account_ref(company)}, block)
        return after

    @contextmanager
    def discovery(self):
        """Unknown credentials cannot race identified companies in other replicas.

        Ordinary grants read this document in their account transaction. Claiming
        it stops all new grants; the drain interval covers grants already issued.
        A failed/expired discovery retains a quiet interval before normal work.
        """
        owner = secrets.token_hex(16)

        def acquire():
            now = self.clock()

            def claim(rows):
                gate = rows["gate"] or {}
                wait = max(gate.get("lease_until", 0), gate.get("quiet_until", 0)) - now
                if wait > 0:
                    raise _Busy(wait)
                return {"gate": {"owner": owner, "lease_until": now + LEASE_SECONDS,
                                 "quiet_until": now + DISCOVERY_DRAIN_SECONDS}}

            return self._transaction({"gate": self.gate_ref}, claim)

        self._wait_for(acquire)
        try:
            self.sleep(DISCOVERY_DRAIN_SECONDS)
            # Never send after a suspended worker has lost its discovery lease.
            now = self.clock()

            def validate(rows):
                gate = rows["gate"] or {}
                if gate.get("owner") != owner or gate.get("lease_until", 0) <= now:
                    raise rate_limited(MIN_INTERVAL)
                return {}

            self._transaction({"gate": self.gate_ref}, validate)
            try:
                yield
            except CentralError as exc:
                if exc.status == 429:
                    after = retry_after_seconds(exc.headers, self.clock(), default=60)
                    blocked_until = self.clock() + after

                    def throttle(rows):
                        gate = rows["gate"] or {}
                        return {"gate": {**gate, "quiet_until": max(gate.get("quiet_until", 0), blocked_until)}}

                    self._transaction({"gate": self.gate_ref}, throttle)
                raise
        finally:
            now = self.clock()

            def release(rows):
                gate = rows["gate"] or {}
                if gate.get("owner") != owner:
                    return {}
                return {"gate": {"owner": "", "lease_until": 0,
                                 "quiet_until": max(gate.get("quiet_until", 0), now + MIN_INTERVAL)}}

            self._transaction({"gate": self.gate_ref}, release)
