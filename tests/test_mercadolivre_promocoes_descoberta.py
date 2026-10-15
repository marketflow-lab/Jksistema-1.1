"""Discovery contracts verified with synthetic API responses and no network."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.services import mercadolivre_promocoes_descoberta as discovery
from backend.routers import mercado_livre as routes


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


def item(index):
    return f"MLB{1000000 + index}"


def candidate(campaign="P-DISCOVERED", kind="PRICE_MATCHING", **extra):
    return {"id": campaign, "type": kind, "status": "candidate", **extra}


class Context:
    def __init__(self, count=2):
        self.count = count
        self.seller = "seller-a"
        self.site = "MLB"
        self.calls = []
        self.promotions = {}
        self.details = {}
        self.custom = None
        self.scroll_supported = True
        self.returned_seller = None

    def obter_cfg_ml(self, client, store):
        return {"user_id": self.seller, "site_id": self.site, "access_token": "synthetic-secret"}

    def ml_api_request_com_retry(self, client, store, cfg, method, url, **kwargs):
        assert method == "GET", "Discovery must never mutate the marketplace"
        path = url.removeprefix("https://api.mercadolibre.com")
        params = kwargs["params"]
        self.calls.append((client, store, path, dict(params)))
        returned = dict(cfg)
        if self.returned_seller:
            returned["user_id"] = self.returned_seller
        if self.custom:
            result = self.custom(path, params)
            if result is not None:
                return result, returned
        if path.endswith("/items/search"):
            if not self.scroll_supported and params.get("search_type") == "scan":
                return Response({}, 400), returned
            start = int(params.get("scroll_id") or params.get("offset") or 0)
            limit = params["limit"]
            batch = [item(index) for index in range(start, min(start + limit, self.count))]
            data = {"results": batch, "paging": {"total": self.count, "limit": limit}}
            if params.get("search_type") == "scan" and batch:
                data["scroll_id"] = str(start + len(batch))
            return Response(data), returned
        if path.startswith("/seller-promotions/items/"):
            value = self.promotions.get(path.rsplit("/", 1)[-1], [])
            return (value if isinstance(value, Response) else Response(value)), returned
        if path.startswith("/seller-promotions/promotions/"):
            campaign = path.split("/")[3]
            if path.endswith("/items"):
                total = 2 if params["status"] == "candidate" else 0
                return Response({"results": [], "paging": {"total": total}}), returned
            detail = self.details.get((campaign, params["promotion_type"]), {
                "id": campaign, "type": params["promotion_type"], "status": "started",
                "name": "Nome oficial da campanha", "start_date": "2026-10-01", "finish_date": "2026-10-31",
            })
            return (detail if isinstance(detail, Response) else Response(detail)), returned
        raise AssertionError(path)


@pytest.fixture
def env(monkeypatch):
    context = Context()
    monkeypatch.setattr(discovery, "_ctx", lambda: context)
    monkeypatch.setattr(discovery, "_ml_cache_get", lambda *args: None)
    monkeypatch.setattr(discovery, "_JOBS", {})
    monkeypatch.setattr(discovery, "_CURRENT", {})
    monkeypatch.setattr(discovery, "_SLOTS", threading.BoundedSemaphore(2))
    monkeypatch.setattr(discovery, "_launch_worker", discovery._run_job)
    monkeypatch.setattr(discovery, "_ROUND_ITEMS", 2000)
    monkeypatch.setattr(discovery, "_ROUND_SECONDS", 1000)
    return context


def start(store="Loja", client="tenant-a", refresh=False):
    return discovery.iniciar_descoberta_promocoes_mercado_livre(client, store, refresh)


def poll(job, store="Loja", client="tenant-a"):
    return discovery.consultar_descoberta_promocoes_mercado_livre(client, store, job["job_id"])


def test_discovers_missing_real_campaign_and_ignores_individual_discount(env):
    for index in range(2):
        env.promotions[item(index)] = [candidate(), {"type": "PRICE_DISCOUNT", "suggested_discounted_price": 80.96}]
    result = start()
    assert result["complete"] and result["status"] == "completed"
    assert result["progress"] == {"processed": 2, "total": 2, "failed": 0, "pages": 2}
    assert result["ignored_without_campaign_id"] == 2
    assert result["seller_id"] == "seller-a" and result["site_id"] == "MLB"
    assert len(result["campaigns"]) == 1
    campaign = result["campaigns"][0]
    assert campaign == {"id": "P-DISCOVERED", "name": "Nome oficial da campanha", "title": "Nome oficial da campanha",
                        "status": "started", "type": "PRICE_MATCHING", "promotion_type": "PRICE_MATCHING",
                        "selection_group": "mercado_livre", "start_date": "2026-10-01", "finish_date": "2026-10-31",
                        "active_count": 0, "eligible_count": 2, "source": "item_discovery"}
    assert sum(call[2] == "/seller-promotions/promotions/P-DISCOVERED" for call in env.calls) == 1
    assert "synthetic-secret" not in str(result) and "access_token" not in str(discovery._JOBS)
    calls = len(env.calls)
    assert start()["job_id"] == result["job_id"] and len(env.calls) == calls


def test_scan_exhaustion_above_offset_limit(env):
    env.count = 1205
    result = start()
    assert result["complete"] and result["progress"]["processed"] == 1205
    searches = [call for call in env.calls if call[2].endswith("/items/search")]
    assert len(searches) == 14 and searches[-1][3]["scroll_id"] == "1205"
    assert all(call[3]["search_type"] == "scan" for call in searches)


def test_budget_resume_retains_items_campaigns_and_monotonic_revision(env, monkeypatch):
    monkeypatch.setattr(discovery, "_ROUND_ITEMS", 1)
    env.promotions[item(0)] = [candidate()]
    first = start()
    assert not first["complete"] and first["resumable"] and first["progress"]["processed"] == 1
    second = start()
    third = start()
    assert first["job_id"] == second["job_id"] == third["job_id"]
    assert first["revision"] < second["revision"] < third["revision"]
    assert third["complete"] and len(third["campaigns"]) == 1
    assert sum(call[2] == "/seller-promotions/items/" + item(0) for call in env.calls) == 1
    assert poll(third) == third


def test_failed_item_is_retried_without_repeating_successful_items(env):
    env.promotions[item(0)] = Response({}, 503)
    first = start()
    assert not first["complete"] and first["progress"]["failed"] == 1
    env.promotions[item(0)] = [candidate()]
    second = start()
    assert second["complete"] and second["progress"]["failed"] == 0
    assert sum(call[2] == "/seller-promotions/items/" + item(1) for call in env.calls) == 1


@pytest.mark.parametrize("error", [{"message": "No offers found"}, {"error": "no_offers_found"}])
def test_legitimate_404_no_offers_is_successful_empty_inspection(env, error):
    env.promotions[item(0)] = Response(error, 404)
    result = start()
    assert result["complete"] and result["progress"]["processed"] == 2
    assert result["progress"]["failed"] == 0


def test_other_404_keeps_scan_incomplete(env):
    env.promotions[item(0)] = Response({"message": "item unavailable"}, 404)
    result = start()
    assert not result["complete"] and result["progress"]["failed"] == 1


def test_metadata_must_confirm_campaign_type_and_owner_before_publication(env):
    env.promotions[item(0)] = [candidate()]
    env.details[("P-DISCOVERED", "PRICE_MATCHING")] = {"id": "P-FOREIGN", "type": "PRICE_MATCHING", "seller_id": "other", "status": "started"}
    result = start()
    assert not result["complete"] and not result["campaigns"]
    assert result["progress"]["failed"] == 1
    env.details.clear()
    retry = start()
    assert retry["complete"] and len(retry["campaigns"]) == 1


@pytest.mark.parametrize("bad", [candidate("OFFER-MLB1000000-BAD"), candidate(item(0)),
                                  candidate(seller_id="foreign"), candidate(item_id=item(9)),
                                  candidate(campaign_id="P-CONFLICT")])
def test_item_offer_and_foreign_identities_never_become_campaigns(env, bad):
    env.promotions[item(0)] = [bad]
    result = start()
    assert not result["campaigns"] and not result["complete"] and not result["resumable"]
    assert not any(call[2].startswith("/seller-promotions/promotions/") for call in env.calls)


def test_unknown_counts_are_null_and_types_deduplicate_as_a_pair(env):
    env.promotions[item(0)] = [candidate(), candidate(kind="SMART")]
    def custom(path, params):
        if path.startswith("/seller-promotions/promotions/") and path.endswith("/items"):
            return Response({"results": []})
    env.custom = custom
    result = start()
    assert result["complete"] and len(result["campaigns"]) == 2
    assert all(c["active_count"] is None and c["eligible_count"] is None for c in result["campaigns"])


def test_poll_cannot_cross_tenant_store_seller_or_site(env):
    job = start()
    for client, store in [("tenant-b", "Loja"), ("tenant-a", "Outra loja")]:
        with pytest.raises(HTTPException) as error:
            poll(job, store, client)
        assert error.value.status_code == 404
    env.seller = "seller-b"
    with pytest.raises(HTTPException) as error:
        poll(job)
    assert error.value.status_code == 404
    replacement = start()
    assert replacement["job_id"] != job["job_id"] and replacement["seller_id"] == "seller-b"
    env.site = "MLA"
    with pytest.raises(HTTPException) as error:
        poll(replacement)
    assert error.value.status_code == 404


def test_changed_identity_in_returned_config_stops_without_publishing(env):
    env.returned_seller = "other"
    result = start()
    assert result["status"] == "failed" and not result["complete"] and not result["resumable"]
    assert not result["campaigns"] and result["progress"]["processed"] == 0
    assert any(w["code"] == "IDENTITY_CHANGED" for w in result["warnings"])


def test_generation_replacement_and_worker_limit_are_idempotent(env, monkeypatch):
    launched = []
    monkeypatch.setattr(discovery, "_launch_worker", launched.append)
    old = start()
    assert start()["job_id"] == old["job_id"] and len(launched) == 1
    replacement = start(refresh=True)
    assert len(launched) == 2 and replacement["job_id"] != old["job_id"]
    third = start("Outra loja")
    assert third["status"] == "partial" and any(w["code"] == "BUSY" for w in third["warnings"])
    with pytest.raises(HTTPException):
        poll(old)
    discovery._run_job(old["job_id"])
    assert not env.calls
    discovery._run_job(replacement["job_id"])
    resumed = start("Outra loja")
    assert resumed["job_id"] == third["job_id"] and len(launched) == 3
    discovery._run_job(resumed["job_id"])
    assert poll(resumed, "Outra loja")["complete"]


def test_completed_cache_ttl_creates_a_new_generation(env, monkeypatch):
    old = start()
    monkeypatch.setattr(discovery, "_COMPLETE_TTL", -1)
    replacement = start()
    assert replacement["job_id"] != old["job_id"]


def test_offset_fallback_limit_is_visible_incomplete_coverage(env):
    env.count = 1001
    env.scroll_supported = False
    result = start()
    assert not result["complete"] and not result["resumable"]
    assert result["progress"]["processed"] == 1000 and result["progress"]["total"] == 1001
    assert any(w["code"] == "INVENTORY_LIMIT" for w in result["warnings"])


def test_truncated_inventory_or_repeated_scroll_is_never_complete(env):
    def custom(path, params):
        if path.endswith("/items/search"):
            return Response({"results": [item(0)], "scroll_id": "same", "paging": {"total": 20}})
    env.custom = custom
    result = start()
    assert not result["complete"] and not result["resumable"] and result["progress"]["processed"] == 1
    assert any(w["code"] == "INVENTORY_FAILED" for w in result["warnings"])


def test_inventory_total_drift_invalidates_coverage_claim(env):
    env.count = 3
    def custom(path, params):
        if path.endswith("/items/search") and params.get("scroll_id"):
            return Response({"results": [], "paging": {"total": 2}})
    env.custom = custom
    result = start()
    assert not result["complete"] and not result["resumable"]
    assert any(w["code"] == "INVENTORY_CHANGED" for w in result["warnings"])


def test_discovery_routes_keep_legacy_routes_and_bind_tenant(env, monkeypatch):
    app = FastAPI()
    app.include_router(routes.create_mercado_livre_router(routes.MercadoLivreRouterConfig(get_tenant_id=lambda: "tenant-a")))
    paths = {route.path for route in app.routes}
    assert {"/api/mercadolivre/promocoes", "/api/mercadolivre/promocoes/contagens"} <= paths
    with TestClient(app) as client:
        first = client.post("/api/mercadolivre/promocoes/descoberta/start", params={"loja": "Loja"})
        assert first.status_code == 200
        job = first.json()
        assert client.get("/api/mercadolivre/promocoes/descoberta/" + job["job_id"], params={"loja": "Loja"}).json() == job
        new = client.post("/api/mercadolivre/promocoes/descoberta/start", params={"loja": "Loja", "force_refresh": "true"}).json()
        assert new["job_id"] != job["job_id"]
        assert client.get("/api/mercadolivre/promocoes/descoberta/" + job["job_id"], params={"loja": "Loja"}).status_code == 404
    assert all(call[0] == "tenant-a" for call in env.calls)


def test_authoritative_base_campaign_skips_unsupported_lightning_metadata(env, monkeypatch):
    env.promotions[item(0)] = [candidate("LGH-MLB1000", "LIGHTNING"), candidate()]
    base = {"seller_id": "seller-a", "site_id": "MLB", "campaigns": [{"id": "LGH-MLB1000", "type": "LIGHTNING"}]}
    keys = []
    def cached(key, ttl):
        keys.append(key)
        return base
    monkeypatch.setattr(discovery, "_ml_cache_get", cached)
    env.details[("LGH-MLB1000", "LIGHTNING")] = Response({}, 404)
    result = start()
    assert result["complete"] and [campaign["id"] for campaign in result["campaigns"]] == ["P-DISCOVERED"]
    assert keys == ["promocoes:tenant-a:Loja:seller-a:MLB"]
    assert not any("promotions/LGH-" in call[2] for call in env.calls)


def test_foreign_or_old_identity_base_cache_cannot_skip_discovery(env, monkeypatch):
    env.promotions[item(0)] = [candidate()]
    monkeypatch.setattr(discovery, "_ml_cache_get", lambda *args: {
        "seller_id": "seller-other", "site_id": "MLB", "campaigns": [{"id": "P-DISCOVERED", "type": "PRICE_MATCHING"}]
    })
    result = start()
    assert result["complete"] and len(result["campaigns"]) == 1
    assert any(call[2] == "/seller-promotions/promotions/P-DISCOVERED" for call in env.calls)


def test_capacity_pressure_does_not_replace_resumed_oldest_checkpoint(env, monkeypatch):
    launched = []
    monkeypatch.setattr(discovery, "_launch_worker", launched.append)
    now = discovery.time.time()
    for index in range(discovery._MAX_JOBS):
        scope = ("tenant-a", "Loja" if index == 0 else f"Loja-{index}", "seller-a", "MLB")
        job = discovery._Job(f"checkpoint-{index}", scope, status="partial", updated_at=now - 100 + index)
        discovery._JOBS[job.job_id] = job
        discovery._CURRENT[scope] = job.job_id
    oldest = discovery._JOBS["checkpoint-0"]
    oldest.pending_items.extend([item(20), item(21)])
    oldest.campaigns[("P-CACHED", "PRICE_MATCHING")] = {"id": "P-CACHED", "type": "PRICE_MATCHING"}
    result = start()
    assert result["job_id"] == oldest.job_id
    assert list(oldest.pending_items) == [item(20), item(21)]
    assert result["campaigns"] == [{"id": "P-CACHED", "type": "PRICE_MATCHING"}]
    assert len(discovery._JOBS) == discovery._MAX_JOBS
    assert launched == [oldest.job_id]
    assert not env.calls


def test_expired_checkpoint_is_replaced_even_when_resuming(env, monkeypatch):
    launched = []
    monkeypatch.setattr(discovery, "_launch_worker", launched.append)
    scope = ("tenant-a", "Loja", "seller-a", "MLB")
    expired = discovery._Job("expired-checkpoint", scope, status="partial",
                             updated_at=discovery.time.time() - discovery._CHECKPOINT_TTL - 1)
    expired.pending_items.append(item(20))
    discovery._JOBS[expired.job_id] = expired
    discovery._CURRENT[scope] = expired.job_id
    result = start()
    assert result["job_id"] != expired.job_id and expired.job_id not in discovery._JOBS
    assert discovery._JOBS[result["job_id"]].pending_items == discovery.deque()
    with pytest.raises(HTTPException) as error:
        poll({"job_id": expired.job_id})
    assert error.value.status_code == 404
