import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.routers.renovacao import RenovacaoRouterConfig, create_renovacao_router
from backend.schemas.renovacao import RenovacaoCampanhaSincronizarRequest
from backend.services import renovacao_promocoes as service


SOURCE = "C-MLB990001"
DESTINATION = "C-MLB990002"
ITEM = "MLB990100"


@pytest.fixture(autouse=True)
def numeric_context(monkeypatch):
    monkeypatch.setattr(service.ctx, "_parse_float_flex", lambda value: float(value) if value is not None else None)
    monkeypatch.setattr(service.ctx, "_ml_extrair_preco_promocao_raw",
                        lambda raw, **kwargs: (raw.get("price"), raw.get("discount_percentage")))


def participation(campaign, price=80):
    return {"situacao": "participante", "raw": {"id": campaign, "status": "pending",
            "type": "SELLER_CAMPAIGN", "price": price, "original_price": 100,
            "discount_percentage": 20}}


def test_same_discount_counts_only_after_both_campaigns_are_confirmed():
    result = service._renovacao_montar_conferencia({ITEM: {
        SOURCE: participation(SOURCE), DESTINATION: participation(DESTINATION)}}, SOURCE, DESTINATION, 1)
    assert result["completa"] is True
    assert result["confirmados"] == 1
    assert result["itens"][0]["percentual_origem"] == 20
    assert result["itens"][0]["percentual_destino"] == 20


def test_all_1102_matched_still_cannot_confirm_a_908_scope():
    state = {f"MLB{990100+i}": {SOURCE: participation(SOURCE), DESTINATION: participation(DESTINATION)}
             for i in range(1102)}
    result = service._renovacao_montar_conferencia(state, SOURCE, DESTINATION, 908)
    assert result["confirmados"] == 1102
    assert result["completa"] is False
    assert result["contagem_coerente"] is False


def test_missing_and_inconclusive_records_are_distinguished_from_equal_prices():
    result = service._renovacao_montar_conferencia({
        ITEM: {SOURCE: participation(SOURCE), DESTINATION: {"situacao": "ausente"}},
        "MLB990101": {SOURCE: {"situacao": "inconclusivo", "erro": "HTTP 429"}},
        "MLB990102": {SOURCE: {"situacao": "ausente"}, DESTINATION: participation(DESTINATION)},
    }, SOURCE, DESTINATION, 2)
    assert result["completa"] is False
    assert result["confirmados"] == 0
    assert result["pendentes"] == 1
    assert result["inconclusivos"][0]["item_id"] == "MLB990101"
    assert result["extras_destino"] == ["MLB990102"]


def test_same_declared_percentage_with_wrong_price_is_not_confirmed():
    result = service._renovacao_montar_conferencia({ITEM: {
        SOURCE: participation(SOURCE), DESTINATION: participation(DESTINATION, price=90)}},
        SOURCE, DESTINATION, 1)
    assert result["completa"] is False
    assert result["itens"][0]["confirmado"] is False


def test_matching_price_cannot_hide_a_different_declared_percentage():
    target = participation(DESTINATION)
    target["raw"]["discount_percentage"] = 19
    result = service._renovacao_montar_conferencia({ITEM: {
        SOURCE: participation(SOURCE), DESTINATION: target}}, SOURCE, DESTINATION, 1)
    assert result["completa"] is False
    assert result["itens"][0]["confirmado"] is False


def test_report_is_tenant_scoped_and_does_not_store_request_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(service.ctx, "get_tenant_path", lambda client: str(tmp_path / client))
    payload = {"loja": "Loja autorizada", "campanha_origem_id": SOURCE,
               "campanha_destino_id": DESTINATION, "somente_conferir": True,
               "authorization": "Bearer NEVER_STORE", "access_token": "NEVER_STORE"}
    service._renovacao_salvar_conferencia("tenant-a", "job-safe", payload, {"success": False})
    path = tmp_path / "tenant-a" / "renovacao_auditorias" / "job-safe.json"
    body = path.read_text(encoding="utf-8")
    assert "NEVER_STORE" not in body
    report = json.loads(body)
    assert report["campanha_origem_id"] == SOURCE
    assert report["somente_conferir"] is True
    assert not (tmp_path / "tenant-b").exists()
    assert not list(path.parent.glob("*.tmp"))


def test_report_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(service.ctx, "get_tenant_path", lambda client: str(tmp_path / client))
    with pytest.raises(ValueError):
        service._renovacao_salvar_conferencia("tenant-a", "../other", {}, {})
    assert not list(tmp_path.rglob("*.json"))


def test_failed_report_replace_preserves_previous_report_and_cleans_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(service.ctx, "get_tenant_path", lambda client: str(tmp_path / client))
    directory = tmp_path / "tenant-a" / "renovacao_auditorias"
    directory.mkdir(parents=True)
    target = directory / "job-safe.json"
    target.write_text('{"previous":true}', encoding="utf-8")
    monkeypatch.setattr(service.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("blocked")))
    with pytest.raises(OSError):
        service._renovacao_salvar_conferencia("tenant-a", "job-safe", {}, {})
    assert json.loads(target.read_text(encoding="utf-8")) == {"previous": True}
    assert not list(directory.glob("*.tmp"))


def router_with_capture(capture):
    noop = lambda *args, **kwargs: {}
    return create_renovacao_router(RenovacaoRouterConfig(
        get_tenant_id=noop, listar_campanhas_usuario=noop, criar_ou_completar_proximo_mes=noop,
        criar_campanha_manual=noop, atualizar_periodo_campanha=noop, deletar_campanha=noop,
        sincronizar_promocao_existente=noop, iniciar_sincronizacao_promocao=capture,
        sync_job_get=noop, agendamento_atual=noop, agendamento_put=noop))


def test_audit_uses_authenticated_tenant_and_cannot_override_it_in_body():
    calls = []
    router = router_with_capture(lambda client, req: calls.append((client, req)) or {"job_id": "safe"})
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/sincronizar-promocao/iniciar"))
    req = RenovacaoCampanhaSincronizarRequest(loja="Loja", campanha_origem_id=SOURCE,
        campanha_destino_id=DESTINATION, somente_conferir=True, client_id="other-tenant")
    assert endpoint(req, client_id="authenticated-tenant") == {"job_id": "safe"}
    assert calls[0][0] == "authenticated-tenant"
    assert calls[0][1].somente_conferir is True
    assert "client_id" not in calls[0][1].model_dump()


def test_old_sync_page_without_observed_count_cannot_start_mutations():
    calls = []
    router = router_with_capture(lambda *args: calls.append(args))
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/sincronizar-promocao/iniciar"))
    req = RenovacaoCampanhaSincronizarRequest(loja="Loja", campanha_origem_id=SOURCE,
                                             campanha_destino_id=DESTINATION)
    with pytest.raises(HTTPException) as error:
        endpoint(req, client_id="authenticated-tenant")
    assert error.value.status_code == 409
    assert calls == []


def test_separate_audit_route_forces_read_only_even_when_body_requests_writes():
    calls = []
    router = router_with_capture(lambda client, req: calls.append((client, req)) or {"job_id": "audit"})
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith("/conferir-promocao/iniciar"))
    req = RenovacaoCampanhaSincronizarRequest(loja="Loja", campanha_origem_id=SOURCE,
        campanha_destino_id=DESTINATION, somente_conferir=False)
    assert endpoint(req, client_id="authenticated-tenant") == {"job_id": "audit"}
    assert calls[0][0] == "authenticated-tenant"
    assert calls[0][1].somente_conferir is True
    assert req.somente_conferir is False


def test_empty_audit_preserves_expected_zero(monkeypatch):
    campaigns = [{"id": campaign, "active_count": 0, "programmed_count": 0} for campaign in (SOURCE, DESTINATION)]
    monkeypatch.setattr(service, "_renovacao_ml_listar_campanhas_usuario", lambda *_: (campaigns, {}))
    captured = []
    monkeypatch.setattr(service, "_renovacao_copiar_mlbs_campanha", lambda *args, **kwargs:
                        (captured.append(kwargs) or {"success": True}, {}))
    service._renovacao_sincronizar_promocao_existente("tenant-a", "Loja", SOURCE, DESTINATION,
        quantidade_origem_esperada=0, somente_conferir=True)
    assert captured[0]["expected_origem_count"] == 0


def test_partial_audit_worker_does_not_label_result_as_success(tmp_path, monkeypatch):
    monkeypatch.setattr(service.ctx, "get_tenant_path", lambda client: str(tmp_path / client))
    monkeypatch.setattr(service, "_renovacao_sincronizar_promocao_existente", lambda *args, **kwargs: {
        "success": False, "somente_conferir": True, "falhas_total": 1})
    updates = []
    monkeypatch.setattr(service, "_renovacao_sync_job_update", lambda job, **kwargs: updates.append(kwargs))
    service._renovacao_sincronizar_promocao_worker("tenant-a", "job-safe", {
        "loja": "Loja", "campanha_origem_id": SOURCE, "campanha_destino_id": DESTINATION,
        "somente_conferir": True})
    assert updates[-1]["status"] == "done"
    assert updates[-1]["etapa"] == "Pendencias"
    assert updates[-1]["result"]["success"] is False
    assert "pendencias" in updates[-1]["mensagem"].lower()
