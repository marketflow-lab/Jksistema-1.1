from pathlib import Path

from fastapi import FastAPI

from backend.routers.whatsapp_bridge import create_whatsapp_bridge_router
from backend.services import whatsapp as whatsapp_package
from backend.services import whatsapp_bridge
from backend.services.whatsapp import contracts, formatting, gateway


def test_bridge_reexports_the_same_request_contracts() -> None:
    names = (
        "WhatsappAdhocMessageRequest",
        "WhatsappBindingRevokeRequest",
        "WhatsappBridgeConfigRequest",
        "WhatsappPairingCodeRequest",
        "WhatsappPhoneRegistrationRequest",
        "WhatsappPhoneSettingsRequest",
        "WhatsappTemplatesRequest",
        "WhatsappVoiceToggleRequest",
    )
    for name in names:
        assert getattr(whatsapp_bridge, name) is getattr(contracts, name)
    assert whatsapp_bridge._QuestionResearchPending is contracts._QuestionResearchPending
    assert contracts.QuestionResearchPending is contracts._QuestionResearchPending


def test_bridge_public_api_contract_is_stable() -> None:
    assert whatsapp_bridge.__all__ == [
        "WhatsappAdhocMessageRequest",
        "WhatsappBridgeConfigRequest",
        "WhatsappBindingRevokeRequest",
        "WhatsappPairingCodeRequest",
        "WhatsappPhoneRegistrationRequest",
        "WhatsappPhoneSettingsRequest",
        "WhatsappTemplatesRequest",
        "WhatsappVoiceToggleRequest",
        "whatsapp_bridge_download_whisper",
        "whatsapp_bridge_iniciar_background",
        "whatsapp_bridge_pairing_code",
        "whatsapp_bridge_poll_once",
        "whatsapp_bridge_register_phone",
        "whatsapp_bridge_revoke",
        "whatsapp_bridge_send_adhoc_message",
        "whatsapp_bridge_status",
        "whatsapp_bridge_sync_templates",
        "whatsapp_bridge_test",
        "whatsapp_bridge_update_config",
        "whatsapp_bridge_update_phone_settings",
        "whatsapp_bridge_voice_calls",
        "whatsapp_bridge_voice_disable",
        "whatsapp_bridge_voice_enable",
        "whatsapp_bridge_voice_preflight",
    ]


def test_whatsapp_router_and_openapi_contracts_are_stable() -> None:
    expected_routes = {
        ("/api/admin/whatsapp/config", "PUT"),
        ("/api/admin/whatsapp/messages", "POST"),
        ("/api/admin/whatsapp/pairing-code", "POST"),
        ("/api/admin/whatsapp/phone-settings", "PUT"),
        ("/api/admin/whatsapp/phones", "POST"),
        ("/api/admin/whatsapp/revoke", "POST"),
        ("/api/admin/whatsapp/status", "GET"),
        ("/api/admin/whatsapp/templates/sync", "POST"),
        ("/api/admin/whatsapp/test", "POST"),
        ("/api/admin/whatsapp/voice/calls", "GET"),
        ("/api/admin/whatsapp/voice/disable", "POST"),
        ("/api/admin/whatsapp/voice/enable", "POST"),
        ("/api/admin/whatsapp/voice/preflight", "POST"),
        ("/api/admin/whatsapp/whisper/download", "POST"),
    }
    router = create_whatsapp_bridge_router()
    actual_routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }
    assert actual_routes == expected_routes

    app = FastAPI()
    app.include_router(router)
    schemas = app.openapi()["components"]["schemas"]
    assert {name for name in schemas if name.startswith("Whatsapp")} == {
        "WhatsappAdhocMessageRequest",
        "WhatsappBindingRevokeRequest",
        "WhatsappBridgeConfigRequest",
        "WhatsappPairingCodeRequest",
        "WhatsappPhoneRegistrationRequest",
        "WhatsappPhoneSettingsRequest",
        "WhatsappTemplatesRequest",
        "WhatsappVoiceToggleRequest",
    }
    assert schemas["WhatsappAdhocMessageRequest"]["required"] == ["phone_number", "message"]
    assert schemas["WhatsappPhoneSettingsRequest"]["required"] == ["subject_id", "username", "client_id"]
    assert schemas["WhatsappTemplatesRequest"]["properties"]["create_missing"]["default"] is False


def test_bridge_formatting_facade_delegates_to_component(monkeypatch) -> None:
    expected = ["formatted"]
    calls = []

    def fake_response_parts(value, title):
        calls.append((value, title))
        return expected

    monkeypatch.setattr(formatting, "_whatsapp_response_parts", fake_response_parts)

    assert whatsapp_bridge._whatsapp_response_parts("body", "title") is expected
    assert calls == [("body", "title")]


def test_bridge_gateway_facade_delegates_to_component(monkeypatch) -> None:
    expected = {"success": True}
    calls = []

    def fake_gateway_json(config, method, path, payload=None, timeout=15):
        calls.append((config, method, path, payload, timeout))
        return expected

    monkeypatch.setattr(gateway, "gateway_json", fake_gateway_json)
    config = {"worker_url": "https://worker.example", "bridge_token": "secret"}

    assert whatsapp_bridge._gateway_json(config, "POST", "/bridge/test", {"value": 1}, timeout=7) is expected
    assert calls == [(config, "POST", "/bridge/test", {"value": 1}, 7)]


def test_new_components_are_explicit_and_do_not_import_the_facade() -> None:
    component_names = {
        "settings.py",
        "media.py",
        "message.py",
        "intent.py",
        "retry_policy.py",
        "tool_results.py",
        "report_scheduling.py",
    }
    package_dir = Path(whatsapp_package.__file__).resolve().parent
    for name in component_names:
        source = (package_dir / name).read_text(encoding="utf-8")
        assert "import whatsapp_bridge" not in source
        assert "from backend.services import whatsapp_bridge" not in source

    assert whatsapp_package.__all__ == [
        "WhatsappAdhocMessageRequest",
        "WhatsappBindingRevokeRequest",
        "WhatsappBridgeConfigRequest",
        "WhatsappPairingCodeRequest",
        "WhatsappPhoneRegistrationRequest",
        "WhatsappPhoneSettingsRequest",
        "WhatsappTemplatesRequest",
        "WhatsappVoiceToggleRequest",
    ]
