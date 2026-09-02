import asyncio
import json
import time
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from backend.routers.integracoes import IntegracoesRouterConfig, create_integracoes_router
from backend.schemas import AuthRequest, StoreRequest
from backend.services import integracoes as integracoes_service
from backend.services import integracoes_api
from backend.services import shared_sync_bundle


TENANT = "tenant-test"
STORE = "Loja Teste"
CALLBACK_URL = "https://callback.example/auth/callback"


def _run_callback(**kwargs):
    return asyncio.run(integracoes_api.integracoes_auth_callback(object(), **kwargs))


def _redirect_parts(response):
    location = response.headers["location"]
    parsed = urlparse(location)
    return location, parsed, parse_qs(parsed.query)


def _assert_signed_result_query(query, *, status, reason=None):
    assert query["status"] == [status]
    assert len(query["sig"][0]) == 64
    assert int(query["expires"][0]) > 0
    if reason is None:
        assert "reason" not in query
    else:
        assert query["reason"] == [reason]


def _seed_store(config=None):
    integracoes_service.salvar_lojas(
        TENANT,
        [
            {
                "nome": STORE,
                "integracoes": {
                    "mercadolivre": config
                    if isinstance(config, dict)
                    else {"connected": False}
                },
            }
        ],
    )


def _ml_config():
    loja = integracoes_service.buscar_loja(TENANT, STORE)
    assert isinstance(loja, dict)
    return loja["integracoes"]["mercadolivre"]


def _start_ml(*, app_id="app-id-test", secret="client-secret-test"):
    result = asyncio.run(
        integracoes_api.start_mercadolivre_auth(
            AuthRequest(loja=STORE, client_id=app_id, client_secret=secret),
            object(),
            client_id=TENANT,
        )
    )
    draft = _ml_config()["oauth_draft"]
    return result, draft["state"]


@pytest.fixture
def isolated_integracoes(tmp_path, monkeypatch):
    info_dir = tmp_path / "info"
    info_dir.mkdir()
    monkeypatch.setattr(integracoes_service, "PASTA_INFO", str(info_dir))
    monkeypatch.setattr(
        integracoes_service,
        "ARQUIVO_LOJAS",
        str(info_dir / "lojas_config.json"),
    )
    monkeypatch.setattr(
        integracoes_service,
        "ARQUIVO_TEMP_AUTH",
        str(info_dir / "temp_integracao.json"),
    )
    monkeypatch.setattr(
        integracoes_service,
        "_get_tenant_path",
        lambda client_id: str(info_dir / client_id),
    )
    monkeypatch.setattr(
        integracoes_service,
        "_normalizar_integracao_conectada",
        lambda _servico, dados: dados,
    )
    monkeypatch.setattr(
        integracoes_api,
        "_resolver_redirect_uri_publica",
        lambda **_kwargs: CALLBACK_URL,
    )
    monkeypatch.setattr(
        integracoes_api,
        "_resolver_redirect_uri_bling",
        lambda **_kwargs: CALLBACK_URL,
    )
    monkeypatch.setattr(
        integracoes_api,
        "auth_ml_get_link",
        lambda app_id, state, **_kwargs: f"https://auth.example/{app_id}?state={state}",
    )
    monkeypatch.setattr(
        integracoes_api,
        "auth_bling_get_link",
        lambda app_id, state, **_kwargs: f"https://bling.example/{app_id}?state={state}",
    )
    _seed_store()
    return info_dir


def test_start_ml_saves_normalized_credentials_and_state_before_browser(
    isolated_integracoes,
):
    result, state = _start_ml(app_id="  app-id-test  ", secret="  client-secret-test  ")
    cfg = _ml_config()
    temp = integracoes_service.ler_temp_auth(state)

    assert cfg["connected"] is False
    assert cfg["oauth_draft"] == {
        "state": state,
        "app_id": "app-id-test",
        "client_secret": "client-secret-test",
        "saved_at": cfg["oauth_draft"]["saved_at"],
    }
    assert cfg["oauth_draft"]["saved_at"]
    assert temp["client_id"] == TENANT
    assert temp["loja"] == STORE
    assert temp["id"] == "app-id-test"
    assert temp["secret"] == "client-secret-test"
    assert state in result["url"]


@pytest.mark.parametrize(
    ("app_id", "secret"),
    [("   ", "secret"), ("app", "\t"), ("", "")],
)
def test_start_ml_rejects_blank_credentials(
    isolated_integracoes,
    app_id,
    secret,
):
    with pytest.raises(HTTPException) as exc:
        _start_ml(app_id=app_id, secret=secret)
    assert exc.value.status_code == 400
    assert integracoes_service.ler_temp_auth() is None
    assert "oauth_draft" not in _ml_config()


def test_start_with_new_draft_preserves_active_credentials_and_tokens(
    isolated_integracoes,
):
    active = {
        "id": "old-app-id",
        "app_id": "old-app-id",
        "secret": "old-client-secret",
        "client_secret": "old-client-secret",
        "access_token": "old-access-token",
        "refresh_token": "old-refresh-token",
        "connected": True,
        "status": "conectado",
    }
    _seed_store(active)
    active_before = deepcopy(active)

    _start_ml(app_id="new-app-id", secret="new-client-secret")
    persisted = _ml_config()

    for key, value in active_before.items():
        if key.startswith("_sync_"):
            continue
        assert persisted[key] == value
    assert persisted["oauth_draft"]["app_id"] == "new-app-id"
    assert persisted["oauth_draft"]["client_secret"] == "new-client-secret"
    assert persisted["oauth_draft"]["state"]


def test_integrations_page_uses_only_a_complete_draft_and_active_status():
    source = (
        Path(__file__).resolve().parents[1] / "static" / "integracoes.html"
    ).read_text(encoding="utf-8")

    assert "const mlDraftCompleto = !!(mlDraft.app_id && mlDraft.client_secret);" in source
    assert "document.getElementById('ml-app-id').value = mlDraftCompleto" in source
    assert "document.getElementById('ml-client-secret').value = mlDraftCompleto" in source
    assert "const mlConectado = !!mlCfg.connected && mlOAuthCompleto && !mlCfg.oauth_invalid;" in source
    assert "document.getElementById('ml-app-id').value.trim()" in source
    assert "document.getElementById('ml-client-secret').value.trim()" in source


def test_credentials_remain_on_disk_after_provider_denial(
    isolated_integracoes,
):
    _, state = _start_ml()

    response = _run_callback(
        state=state,
        error="access_denied",
        error_description="Authorization was denied",
    )
    _, _, query = _redirect_parts(response)
    cfg = _ml_config()

    _assert_signed_result_query(query, status="error", reason="provider_denied")
    assert cfg["connected"] is False
    assert cfg["oauth_draft"]["app_id"] == "app-id-test"
    assert cfg["oauth_draft"]["client_secret"] == "client-secret-test"
    assert integracoes_service.ler_temp_auth(state) is None


def test_ml_success_persists_tokens_clears_draft_and_callback_is_one_shot(
    isolated_integracoes,
    monkeypatch,
):
    _, state = _start_ml()
    exchanges = []

    def exchange(*_args, **_kwargs):
        exchanges.append(True)
        return True, {
            "access_token": "access-token-test",
            "refresh_token": "refresh-token-test",
            "user_id": "seller-test",
            "scope": "offline_access read write",
        }

    monkeypatch.setattr(integracoes_api, "auth_ml_exchange", exchange)
    response = _run_callback(code="oauth-code-test", state=state)
    location, parsed, query = _redirect_parts(response)
    cfg = _ml_config()

    assert response.status_code == 303
    assert parsed.path == "/auth/callback/result"
    _assert_signed_result_query(query, status="success")
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    for sensitive in (
        "oauth-code-test",
        state,
        "app-id-test",
        "client-secret-test",
        "access-token-test",
        "refresh-token-test",
    ):
        assert sensitive not in location
    assert cfg["connected"] is True
    assert cfg["access_token"] == "access-token-test"
    assert cfg["refresh_token"] == "refresh-token-test"
    assert cfg.get("oauth_draft") is None

    replay = _run_callback(code="oauth-code-test-2", state=state)
    _, _, replay_query = _redirect_parts(replay)
    _assert_signed_result_query(replay_query, status="error", reason="session_missing")
    assert exchanges == [True]


def test_missing_and_wrong_state_do_not_consume_the_valid_flow(
    isolated_integracoes,
    monkeypatch,
):
    _, state = _start_ml()
    exchanged = []
    monkeypatch.setattr(
        integracoes_api,
        "auth_ml_exchange",
        lambda *_args, **_kwargs: exchanged.append(True),
    )

    missing = _run_callback(code="oauth-code-test")
    _, _, missing_query = _redirect_parts(missing)
    _assert_signed_result_query(missing_query, status="error", reason="state_missing")

    wrong = _run_callback(code="oauth-code-test", state="wrong-state")
    _, _, wrong_query = _redirect_parts(wrong)
    _assert_signed_result_query(wrong_query, status="error", reason="state_invalid")

    assert integracoes_service.ler_temp_auth(state)["state"] == state
    assert exchanged == []


def test_missing_tenant_in_temp_flow_fails_closed(
    isolated_integracoes,
    monkeypatch,
):
    state = "state-without-tenant"
    integracoes_service.salvar_temp_auth(
        {
            "state": state,
            "loja": STORE,
            "servico": "mercadolivre",
            "id": "app-id-test",
            "secret": "client-secret-test",
            "created_at": time.time(),
        }
    )
    exchanged = []
    monkeypatch.setattr(
        integracoes_api,
        "auth_ml_exchange",
        lambda *_args, **_kwargs: exchanged.append(True),
    )

    response = _run_callback(code="oauth-code-test", state=state)
    _, _, query = _redirect_parts(response)

    _assert_signed_result_query(query, status="error", reason="incomplete_data")
    assert exchanged == []


def test_new_credentials_never_reuse_refresh_from_active_connection(
    isolated_integracoes,
    monkeypatch,
):
    active = {
        "id": "old-app-id",
        "app_id": "old-app-id",
        "secret": "old-client-secret",
        "client_secret": "old-client-secret",
        "access_token": "old-access-token",
        "refresh_token": "old-refresh-token",
        "connected": True,
        "status": "conectado",
    }
    _seed_store(active)
    _, state = _start_ml(app_id="new-app-id", secret="new-client-secret")
    monkeypatch.setattr(
        integracoes_api,
        "auth_ml_exchange",
        lambda *_args, **_kwargs: (True, {"access_token": "new-access-token"}),
    )

    response = _run_callback(code="oauth-code-test", state=state)
    _, _, query = _redirect_parts(response)
    cfg = _ml_config()

    _assert_signed_result_query(query, status="error", reason="missing_refresh_token")
    assert cfg["access_token"] == "old-access-token"
    assert cfg["refresh_token"] == "old-refresh-token"
    assert cfg["connected"] is True
    assert cfg["oauth_draft"]["app_id"] == "new-app-id"


def test_same_active_credentials_may_reuse_existing_refresh(
    isolated_integracoes,
    monkeypatch,
):
    active = {
        "id": "app-id-test",
        "app_id": "app-id-test",
        "secret": "client-secret-test",
        "client_secret": "client-secret-test",
        "access_token": "old-access-token",
        "refresh_token": "old-refresh-token",
        "user_id": "seller-test",
        "connected": True,
        "status": "conectado",
    }
    _seed_store(active)
    _, state = _start_ml()
    monkeypatch.setattr(
        integracoes_api,
        "auth_ml_exchange",
        lambda *_args, **_kwargs: (
            True,
            {"access_token": "new-access-token", "user_id": "seller-test"},
        ),
    )

    response = _run_callback(code="oauth-code-test", state=state)
    _, _, query = _redirect_parts(response)
    cfg = _ml_config()

    _assert_signed_result_query(query, status="success")
    assert cfg["access_token"] == "new-access-token"
    assert cfg["refresh_token"] == "old-refresh-token"
    assert cfg.get("oauth_draft") is None


def test_same_app_credentials_never_reuse_refresh_from_another_seller(
    isolated_integracoes,
    monkeypatch,
):
    active = {
        "id": "app-id-test",
        "app_id": "app-id-test",
        "secret": "client-secret-test",
        "client_secret": "client-secret-test",
        "access_token": "old-access-token",
        "refresh_token": "old-refresh-token",
        "user_id": "seller-old",
        "connected": True,
        "status": "conectado",
    }
    _seed_store(active)
    _, state = _start_ml()
    monkeypatch.setattr(
        integracoes_api,
        "auth_ml_exchange",
        lambda *_args, **_kwargs: (
            True,
            {"access_token": "new-access-token", "user_id": "seller-new"},
        ),
    )

    response = _run_callback(code="oauth-code-test", state=state)
    _, _, query = _redirect_parts(response)
    cfg = _ml_config()

    _assert_signed_result_query(query, status="error", reason="missing_refresh_token")
    assert cfg["access_token"] == "old-access-token"
    assert cfg["refresh_token"] == "old-refresh-token"
    assert cfg["user_id"] == "seller-old"
    assert cfg["oauth_draft"]["state"] == state


@pytest.mark.parametrize(
    "provider_result,reason",
    [
        (None, "exchange_failed"),
        ({}, "incomplete_tokens"),
        ({"access_token": "   ", "refresh_token": "refresh"}, "incomplete_tokens"),
        ({"access_token": "access", "refresh_token": "\t"}, "missing_refresh_token"),
    ],
)
def test_malformed_or_incomplete_ml_tokens_never_report_success(
    isolated_integracoes,
    monkeypatch,
    provider_result,
    reason,
):
    _, state = _start_ml()
    monkeypatch.setattr(
        integracoes_api,
        "auth_ml_exchange",
        lambda *_args, **_kwargs: (True, provider_result),
    )

    response = _run_callback(code="oauth-code-test", state=state)
    _, _, query = _redirect_parts(response)
    cfg = _ml_config()

    _assert_signed_result_query(query, status="error", reason=reason)
    assert cfg["connected"] is False
    assert "access_token" not in cfg
    assert cfg["oauth_draft"]["state"] == state


def test_provider_and_exchange_errors_are_not_reflected(
    isolated_integracoes,
    monkeypatch,
):
    malicious = '\"><script>window.bad=true</script>'
    _, provider_state = _start_ml()
    provider = _run_callback(
        state=provider_state,
        error=malicious,
        error_description=malicious,
    )
    provider_location, _, provider_query = _redirect_parts(provider)
    _assert_signed_result_query(provider_query, status="error", reason="provider_denied")
    assert malicious not in provider_location

    _, exchange_state = _start_ml()
    monkeypatch.setattr(
        integracoes_api,
        "auth_ml_exchange",
        lambda *_args, **_kwargs: (False, malicious),
    )
    exchange = _run_callback(code="oauth-code-test", state=exchange_state)
    exchange_location, _, exchange_query = _redirect_parts(exchange)
    _assert_signed_result_query(exchange_query, status="error", reason="exchange_failed")
    assert malicious not in exchange_location


def test_two_temp_flows_are_independent_and_expired_flow_is_removed(
    isolated_integracoes,
):
    now = time.time()
    integracoes_service.salvar_temp_auth({"state": "flow-a", "created_at": now})
    integracoes_service.salvar_temp_auth({"state": "flow-b", "created_at": now + 0.01})

    assert integracoes_service.consumir_temp_auth("flow-a")["state"] == "flow-a"
    assert integracoes_service.consumir_temp_auth("flow-a") is None
    assert integracoes_service.ler_temp_auth("flow-b")["state"] == "flow-b"

    integracoes_service.salvar_temp_auth(
        {
            "state": "expired-flow",
            "created_at": now - integracoes_service._TEMP_AUTH_TTL_SECONDS - 1,
        }
    )
    assert integracoes_service.ler_temp_auth("expired-flow") is None
    payload = json.loads(
        Path(integracoes_service.ARQUIVO_TEMP_AUTH).read_text(encoding="utf-8")
    )
    assert "expired-flow" not in payload["flows"]
    assert "flow-b" in payload["flows"]

    before_bytes = Path(integracoes_service.ARQUIVO_TEMP_AUTH).read_bytes()
    before_mtime = Path(integracoes_service.ARQUIVO_TEMP_AUTH).stat().st_mtime_ns
    assert integracoes_service.consumir_temp_auth("unknown-flow") is None
    assert Path(integracoes_service.ARQUIVO_TEMP_AUTH).read_bytes() == before_bytes
    assert Path(integracoes_service.ARQUIVO_TEMP_AUTH).stat().st_mtime_ns == before_mtime


def test_bling_new_attempt_invalidates_old_callback_and_success_clears_pending_state(
    isolated_integracoes,
    monkeypatch,
):
    async def start_bling(app_id, secret):
        return await integracoes_api.start_bling_auth(
            AuthRequest(loja=STORE, client_id=app_id, client_secret=secret),
            object(),
            client_id=TENANT,
        )

    asyncio.run(start_bling("old-bling-app", "old-bling-secret"))
    old_state = integracoes_service.ler_temp_auth()["state"]
    asyncio.run(start_bling("new-bling-app", "new-bling-secret"))
    new_state = integracoes_service.ler_temp_auth()["state"]
    exchanges = []

    def exchange(*_args, **_kwargs):
        exchanges.append(True)
        return True, {
            "access_token": "bling-access",
            "refresh_token": "bling-refresh",
        }

    monkeypatch.setattr(integracoes_api, "auth_bling_exchange", exchange)

    old_response = _run_callback(code="old-code", state=old_state)
    _, _, old_query = _redirect_parts(old_response)
    _assert_signed_result_query(old_query, status="error", reason="store_changed")
    assert exchanges == []

    new_response = _run_callback(code="new-code", state=new_state)
    _, _, new_query = _redirect_parts(new_response)
    _assert_signed_result_query(new_query, status="success")
    cfg = integracoes_service.buscar_loja(TENANT, STORE)["integracoes"]["bling"]
    assert cfg["connected"] is True
    assert cfg["oauth_invalid"] is False
    assert cfg["shared_without_oauth_tokens"] is False
    assert cfg.get("oauth_pending_state") is None
    assert cfg["access_token"] == "bling-access"
    assert cfg["refresh_token"] == "bling-refresh"
    assert exchanges == [True]


def test_newer_attempt_invalidates_old_callback_without_consuming_new_flow(
    isolated_integracoes,
    monkeypatch,
):
    _, old_state = _start_ml(app_id="old-draft-app", secret="old-draft-secret")
    _, new_state = _start_ml(app_id="new-draft-app", secret="new-draft-secret")
    exchanges = []
    monkeypatch.setattr(
        integracoes_api,
        "auth_ml_exchange",
        lambda *_args, **_kwargs: exchanges.append(True),
    )

    response = _run_callback(code="old-code", state=old_state)
    _, _, query = _redirect_parts(response)

    _assert_signed_result_query(query, status="error", reason="store_changed")
    assert exchanges == []
    assert _ml_config()["oauth_draft"]["state"] == new_state
    assert integracoes_service.ler_temp_auth(new_state)["state"] == new_state


@pytest.mark.parametrize("change", ["disconnect", "delete"])
def test_callback_after_disconnect_or_delete_never_reconnects_or_recreates_store(
    isolated_integracoes,
    monkeypatch,
    change,
):
    _, state = _start_ml()
    if change == "disconnect":
        integracoes_service.desconectar_api_loja(TENANT, STORE, "mercadolivre")
    else:
        asyncio.run(integracoes_api.delete_loja(STORE, client_id=TENANT))
    exchanges = []
    monkeypatch.setattr(
        integracoes_api,
        "auth_ml_exchange",
        lambda *_args, **_kwargs: exchanges.append(True),
    )

    response = _run_callback(code="late-code", state=state)
    _, _, query = _redirect_parts(response)

    _assert_signed_result_query(query, status="error", reason="store_changed")
    assert exchanges == []
    loja = integracoes_service.buscar_loja(TENANT, STORE)
    if change == "disconnect":
        cfg = loja["integracoes"]["mercadolivre"]
        assert cfg["connected"] is False
        assert "oauth_draft" not in cfg
        assert "access_token" not in cfg
        assert "refresh_token" not in cfg
    else:
        assert loja is None


@pytest.mark.parametrize("servico", ["mercadolivre", "bling"])
@pytest.mark.parametrize("mudanca", ["disconnect", "recreate"])
@pytest.mark.parametrize("resultado", ["success", "provider_denied"])
def test_oauth_so_restaura_tombstone_depois_do_callback_bem_sucedido(
    isolated_integracoes,
    monkeypatch,
    servico,
    mudanca,
    resultado,
):
    credenciais = {
        "access_token": f"{servico}-access-antigo",
        "refresh_token": f"{servico}-refresh-antigo",
        "connected": True,
    }
    if servico == "mercadolivre":
        credenciais.update({
            "app_id": "ml-app-antigo",
            "client_secret": "ml-secret-antigo",
            "user_id": "seller-antigo",
        })
    else:
        credenciais.update({
            "id": "bling-app-antigo",
            "secret": "bling-secret-antigo",
        })
    integracoes_service.atualizar_api_loja(
        TENANT,
        STORE,
        servico,
        credenciais,
        require_existing=True,
    )

    if mudanca == "disconnect":
        integracoes_service.desconectar_api_loja(TENANT, STORE, servico)
    else:
        asyncio.run(integracoes_api.delete_loja(STORE, client_id=TENANT))
        asyncio.run(
            integracoes_api.create_loja(
                StoreRequest(nome=STORE),
                client_id=TENANT,
            )
        )

    tombstones_antes = integracoes_service._integracoes_ler_tombstones_estrito(TENANT)
    exclusao_antes = next(
        item
        for item in tombstones_antes
        if item.get("type") == "integration" and item.get("service") == servico
    )
    assert exclusao_antes.get("deleted_at")
    assert not exclusao_antes.get("restored_at")

    if servico == "mercadolivre":
        asyncio.run(
            integracoes_api.start_mercadolivre_auth(
                AuthRequest(
                    loja=STORE,
                    client_id="ml-app-novo",
                    client_secret="ml-secret-novo",
                ),
                object(),
                client_id=TENANT,
            )
        )
        cfg = _ml_config()
        assert cfg["oauth_draft"]["app_id"] == "ml-app-novo"
        oauth_state = cfg["oauth_draft"]["state"]
    else:
        asyncio.run(
            integracoes_api.start_bling_auth(
                AuthRequest(
                    loja=STORE,
                    client_id="bling-app-novo",
                    client_secret="bling-secret-novo",
                ),
                object(),
                client_id=TENANT,
            )
        )
        cfg = integracoes_service.buscar_loja(TENANT, STORE)["integracoes"]["bling"]
        assert cfg.get("oauth_pending_state")
        oauth_state = cfg["oauth_pending_state"]

    tombstones_depois = integracoes_service._integracoes_ler_tombstones_estrito(TENANT)
    exclusao_depois = next(
        item
        for item in tombstones_depois
        if item.get("type") == "integration" and item.get("service") == servico
    )
    assert exclusao_depois == exclusao_antes

    if resultado == "success":
        if servico == "mercadolivre":
            monkeypatch.setattr(
                integracoes_api,
                "auth_ml_exchange",
                lambda *_args, **_kwargs: (
                    True,
                    {
                        "access_token": "ml-access-novo",
                        "refresh_token": "ml-refresh-novo",
                        "user_id": "seller-novo",
                    },
                ),
            )
        else:
            monkeypatch.setattr(
                integracoes_api,
                "auth_bling_exchange",
                lambda *_args, **_kwargs: (
                    True,
                    {
                        "access_token": "bling-access-novo",
                        "refresh_token": "bling-refresh-novo",
                    },
                ),
            )
        response = _run_callback(code="oauth-code", state=oauth_state)
    else:
        response = _run_callback(
            state=oauth_state,
            error="access_denied",
            error_description="Authorization was denied",
        )
    _, _, query = _redirect_parts(response)
    _assert_signed_result_query(
        query,
        status="success" if resultado == "success" else "error",
        reason=None if resultado == "success" else "provider_denied",
    )

    tombstones_finais = integracoes_service._integracoes_ler_tombstones_estrito(TENANT)
    exclusao_final = next(
        item
        for item in tombstones_finais
        if item.get("type") == "integration" and item.get("service") == servico
    )
    if resultado == "success":
        assert exclusao_final.get("restored_at")
        assert not exclusao_final.get("deleted_at")
        cfg_final = integracoes_service.buscar_loja(TENANT, STORE)["integracoes"][servico]
        assert cfg_final["connected"] is True
        token_esperado = (
            "ml-access-novo" if servico == "mercadolivre" else "bling-access-novo"
        )
        assert cfg_final["access_token"] == token_esperado
    else:
        assert exclusao_final == exclusao_antes


def test_cas_blocks_draft_replacement_during_token_exchange(
    isolated_integracoes,
    monkeypatch,
):
    _, state = _start_ml()

    def exchange_and_replace_draft(*_args, **_kwargs):
        integracoes_service.atualizar_api_loja(
            TENANT,
            STORE,
            "mercadolivre",
            {
                "oauth_draft": {
                    "state": "newer-state",
                    "app_id": "newer-app",
                    "client_secret": "newer-secret",
                    "saved_at": str(time.time()),
                }
            },
            require_existing=True,
        )
        return True, {
            "access_token": "late-access",
            "refresh_token": "late-refresh",
        }

    monkeypatch.setattr(integracoes_api, "auth_ml_exchange", exchange_and_replace_draft)
    response = _run_callback(code="old-code", state=state)
    _, _, query = _redirect_parts(response)
    cfg = _ml_config()

    _assert_signed_result_query(query, status="error", reason="store_changed")
    assert cfg["oauth_draft"]["state"] == "newer-state"
    assert "access_token" not in cfg
    assert "refresh_token" not in cfg


def test_noop_persistence_never_reports_success(
    isolated_integracoes,
    monkeypatch,
):
    _, state = _start_ml()
    monkeypatch.setattr(
        integracoes_api,
        "auth_ml_exchange",
        lambda *_args, **_kwargs: (
            True,
            {"access_token": "access", "refresh_token": "refresh"},
        ),
    )
    monkeypatch.setattr(integracoes_api, "atualizar_api_loja", lambda *_args, **_kwargs: None)

    response = _run_callback(code="oauth-code", state=state)
    _, _, query = _redirect_parts(response)

    _assert_signed_result_query(query, status="error", reason="persistence_failed")
    assert _ml_config()["oauth_draft"]["state"] == state


def test_temp_auth_endpoint_binds_flow_to_authenticated_tenant(monkeypatch):
    captured = []
    monkeypatch.setattr(
        integracoes_api,
        "salvar_temp_auth",
        lambda payload: captured.append(payload) or "generated-state",
    )

    result = asyncio.run(
        integracoes_api.save_temp_auth_endpoint(
            {"client_id": "other-tenant", "state": "requested-state"},
            client_id=TENANT,
        )
    )

    assert captured[0]["client_id"] == TENANT
    assert result == {"success": True, "state": "generated-state"}


def test_oauth_draft_is_never_included_in_shared_sync_snapshot():
    payload = {
        "lojas": [
            {
                "nome": STORE,
                "integracoes": {
                    "mercadolivre": {
                        "oauth_draft": {
                            "state": "transient-state",
                            "app_id": "draft-app",
                            "client_secret": "draft-secret",
                        },
                        "connected": False,
                    }
                },
            }
        ]
    }

    sanitized = shared_sync_bundle._shared_sync_remove_transient_oauth(payload)

    cfg = sanitized["lojas"][0]["integracoes"]["mercadolivre"]
    assert "oauth_draft" not in cfg
    assert "transient-state" not in json.dumps(sanitized)
    assert "draft-secret" not in json.dumps(sanitized)


def test_public_result_page_is_signed_public_and_registered_before_static_mount(
    tmp_path,
):
    app = FastAPI()

    async def tenant_dependency(*_args, **_kwargs):
        raise AssertionError("Public OAuth result must not resolve a tenant session.")

    app.include_router(
        create_integracoes_router(
            IntegracoesRouterConfig(get_tenant_id=tenant_dependency),
        )
    )
    app.mount("/", StaticFiles(directory=str(tmp_path), html=True), name="static-test")
    client = TestClient(app)

    forged = client.get("/auth/callback/result?status=success")
    assert forged.status_code == 200
    assert 'data-oauth-result="error"' in forged.text
    assert "retorno não é válido ou expirou" in forged.text

    success_location = integracoes_api._oauth_result_redirect("success").headers["location"]
    success = client.get(success_location)
    assert success.status_code == 200
    assert 'data-oauth-result="success"' in success.text
    assert "Volte ao JK Sistema" in success.text
    assert "/integracoes.html" not in success.text
    assert "frontend_index" not in success.text

    parsed_success = urlparse(success_location)
    success_query = parse_qs(parsed_success.query)
    tampered = client.get(
        "/auth/callback/result",
        params={
            "status": "error",
            "reason": "provider_denied",
            "expires": success_query["expires"][0],
            "sig": success_query["sig"][0],
        },
    )
    assert 'data-oauth-result="error"' in tampered.text
    assert "retorno não é válido ou expirou" in tampered.text

    expired_at = int(time.time()) - 1
    expired_sig = integracoes_api._oauth_result_signature("success", "", expired_at)
    expired = client.get(
        "/auth/callback/result",
        params={"status": "success", "expires": expired_at, "sig": expired_sig},
    )
    assert 'data-oauth-result="error"' in expired.text
    assert "retorno não é válido ou expirou" in expired.text

    unicode_sig = client.get(
        "/auth/callback/result",
        params={"status": "success", "expires": int(time.time()) + 60, "sig": "é"},
    )
    assert unicode_sig.status_code == 200
    assert 'data-oauth-result="error"' in unicode_sig.text
    assert "retorno não é válido ou expirou" in unicode_sig.text

    for response in (forged, success, tampered, expired, unicode_sig):
        assert response.headers["cache-control"] == "no-store, max-age=0"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert "default-src 'none'" in response.headers["content-security-policy"]
