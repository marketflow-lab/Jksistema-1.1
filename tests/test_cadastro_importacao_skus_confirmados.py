import asyncio
import copy
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import cadastro_importacao_catalogos as service


def request(user="operador"):
    return SimpleNamespace(state=SimpleNamespace(username=user))


def provider():
    return {
        "store_id": "loja-a", "seller_id": "42", "source": "mercadolivre",
        "coverage_complete": False, "sku_coverage_complete": False,
        "skipped": [{"reason": "sku_ambiguous", "mlb": "MLB999"}],
        "items": [{
            "sku": "001", "identity_confirmed": True,
            "identity_evidence": {"store_id": "loja-a", "seller_id": "42", "sku_normalizado": "001",
                                  "listings": [{"mlb": "MLB123", "variation_id": "", "sku_normalizado": "001", "sku_sources": ["seller_custom_field"]}]},
            "fields": {"descricao": "Descricao confirmada", "titulo_ml": "Titulo",
                       "mlb_principal": "MLB123", "mlb_ids": "MLB123",
                       "link_ml": "https://produto.mercadolivre.com.br/MLB-123-produto",
                       "links_ml": '["https://produto.mercadolivre.com.br/MLB-123-produto"]',
                       "foto_url_ml": "https://http2.mlstatic.com/capa.jpg",
                       "qtd_anuncios_mlb": 1, "estoque_disponivel_ml": 10},
            "photo_candidates": [{"url": "https://http2.mlstatic.com/capa.jpg", "item_id": "MLB123"}],
            "conflicts": {"duplicate_listing_sku": ["MLB123", "MLB456"]},
        }],
    }


def current(scope="store_file", **overrides):
    return {"sku": "001", "scope_source": scope, "row_version": 7, "foto": "", "mlb_ids": "MLB123", **overrides}


def job(preview, **overrides):
    now = service._agora_utc()
    return {
        "job_id": "original", "client_id": "cliente-a", "store_id": "loja-a", "store_name": "Loja A",
        "requester_fingerprint": service._requester_fingerprint(request(), "cliente-a"), "source": "mercadolivre",
        "status": "ready", "can_apply": True, "created_at_utc": now, "updated_at_utc": now,
        "updated_ts": time.time(), "cancel_event": threading.Event(), "progress": {},
        "config_fingerprint": "a" * 64, "apply_config_fingerprint": "a" * 64, "preview": preview,
        **overrides,
    }


@pytest.fixture(autouse=True)
def isolated_jobs():
    service.CATALOG_IMPORT_JOBS.clear()
    yield
    service.CATALOG_IMPORT_JOBS.clear()


@pytest.mark.parametrize("scope", [None, "legacy_shadow", "store_file"])
def test_partial_catalog_imports_confirmed_sku_for_new_legacy_and_current_store(scope):
    rows = [] if scope is None else [current(scope, descricao="Descricao antiga", mlb_ids="MLB777")]
    preview = service._construir_preview("mercadolivre", provider(), rows)
    assert preview["can_apply"] is True
    assert preview["summary"]["aplicaveis"] == 1
    assert preview["summary"]["conflitos"] == 1
    assert preview["ignored_reason_counts"] == {"sku_ambiguous": 1}
    assert preview["partial_apply_scope"] == "confirmed_sku_catalog_fields"
    assert "confirmed_skus_only" in preview["partial_reasons"]
    assert "confirmed_existing_updates_only" not in preview["partial_reasons"]
    applied = preview["apply_rows"][0]
    assert applied["expected_scope"] == (scope or "absent")
    assert applied["fields"]["descricao"] == "Descricao confirmada"
    assert applied["fields"]["mlb_ids"] == ("MLB123|MLB777" if scope == "store_file" else "MLB123")
    assert "estoque_disponivel_ml" not in applied["fields"]
    assert "qtd_anuncios_mlb" not in applied["fields"]
    assert applied["photo_plan"]["candidates"][0]["item_id"] == "MLB123"
    assert service._preview_parcial_dados_confirmados_valida("mercadolivre", preview, "loja-a")
    assert not service._preview_parcial_dados_confirmados_valida("mercadolivre", preview, "loja-b")


@pytest.mark.parametrize("key,value", [("store_id", "loja-b"), ("seller_id", "43"), ("sku_normalizado", "999")])
def test_evidence_for_another_store_seller_or_sku_never_releases_existing_photo(key, value):
    source = provider()
    source["items"][0]["identity_evidence"][key] = value
    preview = service._construir_preview("mercadolivre", source, [current()])
    assert preview["can_apply"] is False
    assert preview["summary"]["fotos_planejadas"] == 0
    assert preview["ignored_reason_counts"]["sku_identity_not_confirmed"] == 1


def test_explicitly_unconfirmed_group_does_not_fall_back_to_old_photo_mode():
    source = provider()
    source["items"][0].update(identity_confirmed=False, identity_evidence={}, identity_reasons=["user_product_identity_conflict"])
    preview = service._construir_preview("mercadolivre", source, [current()])
    assert not preview["apply_rows"]
    assert preview["items"][0]["warnings"] == ["user_product_identity_conflict"]


@pytest.mark.parametrize("tamper", ["unproven_mlb", "other_sku", "missing_legacy_hash", "aggregate_field", "other_photo"])
def test_partial_apply_validator_rejects_changed_evidence_and_unbounded_fields(tamper):
    preview = service._construir_preview("mercadolivre", provider(), [current("legacy_shadow")])
    row = preview["apply_rows"][0]
    if tamper == "unproven_mlb": row["fields"]["mlb_principal"] = "MLB999"
    elif tamper == "other_sku": row["sku"] = "999"
    elif tamper == "missing_legacy_hash": row.pop("legacy_snapshot_hash")
    elif tamper == "aggregate_field": row["fields"]["estoque_disponivel_ml"] = 999
    else: row["photo_plan"]["candidates"][0]["item_id"] = "MLB999"
    assert not service._preview_parcial_dados_confirmados_valida("mercadolivre", preview, "loja-a")


def test_legacy_foreign_photo_is_replaced_but_confirmed_store_photo_is_kept(monkeypatch):
    monkeypatch.setattr(service, "_cadastro_foto_store_id_referencia", lambda tenant, photo: "loja-b" if "outra" in photo else "loja-a")
    for photo, expected in [("outra.jpg", 1), ("propria.jpg", 0)]:
        preview = service._construir_preview("mercadolivre", provider(), [current("legacy_shadow", foto=photo)], client_id="cliente-a")
        assert preview["summary"]["fotos_planejadas"] == expected


def test_all_ignored_reasons_are_counted_even_when_public_examples_are_truncated():
    source = provider()
    source["skipped"] = [{"reason": "sku_ambiguous", "mlb": f"MLB{i}"} for i in range(120)]
    preview = service._construir_preview("mercadolivre", source, [])
    public = service._public_job(job(preview))
    assert len(public["ignored"]) == 100
    assert public["ignored_total"] == 120
    assert public["ignored_reason_counts"] == {"sku_ambiguous": 120}
    assert "identity_evidence" not in json.dumps(public)


def test_failed_photo_preserves_metadata_and_returns_only_failed_sku_for_retry(monkeypatch):
    captured = []
    def writer(tenant, store, payloads, **kwargs):
        captured.extend(payloads)
        assert "mlb_ids" in kwargs["campos_legados_ignorados"]
        assert "descricao" not in kwargs["campos_legados_ignorados"]
        return {"incluidos": len(payloads), "atualizados": 0, "total": len(payloads)}
    monkeypatch.setattr(service, "salvar_produtos_loja_em_lote", writer)
    monkeypatch.setattr(service.cadastro_ml, "_download_photo_data_url", lambda *args: (_ for _ in ()).throw(OSError("offline")))
    result = service._salvar_payloads_importacao_catalogo("cliente-a", "loja-a", "mercadolivre", [{
        "sku": "001", "row_version": 0, "__expected_scope": "absent", "descricao": "Preservar", "mlb_ids": "MLB123",
        "__catalog_photo_plan": {"candidates": [{"url": "https://http2.mlstatic.com/capa.jpg", "item_id": "MLB123"}]},
    }], campos_derivados_permitidos=set(), precommit_validator=lambda _: None)
    assert captured[0]["descricao"] == "Preservar"
    assert "__foto_data_url" not in captured[0]
    assert result["photos_failed"] == [{"sku": "001", "reason": "photo_download_failed"}]
    assert result["_pending_photo_plans"][0]["sku"] == "001"


def install_retry_job(monkeypatch):
    preview = service._construir_preview("mercadolivre", provider(), [current()])
    original = job(preview, status="applied", can_apply=False, apply_result={"can_retry_photos": True}, pending_photo_plans=[
        {"sku": sku, "photo_plan": {"candidates": [{"url": "https://http2.mlstatic.com/capa.jpg", "item_id": "MLB123"}]}}
        for sku in ("001", "002", "003")
    ])
    service.CATALOG_IMPORT_JOBS["original"] = original
    monkeypatch.setattr(service, "_configuracao_aplicacao_fingerprint", lambda *args: "a" * 64)
    monkeypatch.setattr(service, "_listar_produtos_loja_sync", lambda *args, **kwargs: [current(), current(sku="002", foto="foto-propria.jpg"), current(sku="003", mlb_ids="MLB999")])
    monkeypatch.setattr(service, "_cadastro_foto_store_id_referencia", lambda tenant, photo: "loja-a" if photo == "foto-propria.jpg" else "")
    monkeypatch.setattr(service, "_provider_for", lambda *args: pytest.fail("Retry must not recollect the catalog"))
    return original


def test_photo_retry_prepares_only_failed_still_bound_sku_and_is_idempotent(monkeypatch):
    install_retry_job(monkeypatch)
    public = asyncio.run(service.retentar_fotos_importacao_catalogo("loja-a", "original", request(), "cliente-a"))
    repeated = asyncio.run(service.retentar_fotos_importacao_catalogo("loja-a", "original", request(), "cliente-a"))
    assert repeated["job_id"] == public["job_id"]
    assert public["job_id"] != "original"
    assert public["can_apply"] is True
    assert public["summary"]["fotos_planejadas"] == 1
    assert public["ignored_reason_counts"] == {"photo_already_saved": 1, "photo_sku_binding_changed": 1}
    retry = service.CATALOG_IMPORT_JOBS[public["job_id"]]
    assert retry["preview"]["apply_rows"][0]["fields"] == {}
    assert retry["preview"]["apply_rows"][0]["row_version"] == 7
    assert "photo_plan" not in json.dumps(public)


def test_photo_retry_does_not_treat_generic_fallback_as_a_saved_store_photo(monkeypatch):
    install_retry_job(monkeypatch)
    monkeypatch.setattr(service, "_listar_produtos_loja_sync", lambda *args, **kwargs: [current(foto="cadastro_fotos/001.jpg")])
    public = asyncio.run(service.retentar_fotos_importacao_catalogo("loja-a", "original", request(), "cliente-a"))
    assert public["summary"]["fotos_planejadas"] == 1
    assert public["can_apply"] is True


@pytest.mark.parametrize("terminal", ["stale", "error", "cancelled"])
def test_photo_retry_rebuilds_expired_or_failed_review_from_current_row_version(monkeypatch, terminal):
    install_retry_job(monkeypatch)
    first = asyncio.run(service.retentar_fotos_importacao_catalogo("loja-a", "original", request(), "cliente-a"))
    service.CATALOG_IMPORT_JOBS[first["job_id"]]["status"] = terminal
    monkeypatch.setattr(service, "_listar_produtos_loja_sync", lambda *args, **kwargs: [current(row_version=8)])
    renewed = asyncio.run(service.retentar_fotos_importacao_catalogo("loja-a", "original", request(), "cliente-a"))
    assert renewed["job_id"] != first["job_id"]
    assert renewed["status"] == "ready"
    assert renewed["can_apply"] is True
    assert service.CATALOG_IMPORT_JOBS[renewed["job_id"]]["preview"]["apply_rows"][0]["row_version"] == 8


@pytest.mark.parametrize("scope", ["tenant", "store", "requester", "integration"])
def test_photo_retry_checks_tenant_store_requester_and_connection(monkeypatch, scope):
    install_retry_job(monkeypatch)
    if scope == "integration":
        monkeypatch.setattr(service, "_configuracao_aplicacao_fingerprint", lambda *args: "b" * 64)
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.retentar_fotos_importacao_catalogo(
            "loja-b" if scope == "store" else "loja-a", "original",
            request("outro" if scope == "requester" else "operador"),
            "cliente-b" if scope == "tenant" else "cliente-a",
        ))
    assert error.value.status_code in (404, 409)
    assert len(service.CATALOG_IMPORT_JOBS) == 1


def test_full_apply_uses_validated_partial_legacy_plan_and_keeps_private_evidence_out_of_payload(monkeypatch):
    preview = service._construir_preview("mercadolivre", provider(), [current("legacy_shadow")])
    service.CATALOG_IMPORT_JOBS["original"] = job(preview)
    monkeypatch.setattr(service, "_configuracao_aplicacao_fingerprint", lambda *args: "a" * 64)
    saved = []
    def save(tenant, store, source, payloads, **kwargs):
        saved.extend(payloads)
        return {"incluidos": 1, "atualizados": 0, "total": 1, "fotos_salvas": 1}
    monkeypatch.setattr(service, "_salvar_payloads_importacao_catalogo", save)
    public = asyncio.run(service.aplicar_importacao_catalogo("loja-a", "original", request(), "cliente-a"))
    assert public["status"] == "applied"
    assert saved[0]["__expected_scope"] == "legacy_shadow"
    assert saved[0]["__legacy_snapshot_hash"]
    assert saved[0]["mlb_principal"] == "MLB123"
    assert "identity_evidence" not in saved[0]
    assert public["apply_result"]["partial_application"] is True
