"""Administrative routes for the local WhatsApp/Joao bridge."""

from __future__ import annotations

from fastapi import APIRouter

from backend.services import whatsapp_bridge


router = APIRouter(prefix="/api/admin/whatsapp", tags=["whatsapp-joao"])


def create_whatsapp_bridge_router() -> APIRouter:
    bridge_router = APIRouter(prefix="/api/admin/whatsapp", tags=["whatsapp-joao"])
    bridge_router.add_api_route("/status", whatsapp_bridge.whatsapp_bridge_status, methods=["GET"])
    bridge_router.add_api_route("/config", whatsapp_bridge.whatsapp_bridge_update_config, methods=["PUT"])
    bridge_router.add_api_route("/phone-settings", whatsapp_bridge.whatsapp_bridge_update_phone_settings, methods=["PUT"])
    bridge_router.add_api_route("/phones", whatsapp_bridge.whatsapp_bridge_register_phone, methods=["POST"])
    bridge_router.add_api_route("/messages", whatsapp_bridge.whatsapp_bridge_send_adhoc_message, methods=["POST"])
    bridge_router.add_api_route("/test", whatsapp_bridge.whatsapp_bridge_test, methods=["POST"])
    bridge_router.add_api_route("/pairing-code", whatsapp_bridge.whatsapp_bridge_pairing_code, methods=["POST"])
    bridge_router.add_api_route("/revoke", whatsapp_bridge.whatsapp_bridge_revoke, methods=["POST"])
    bridge_router.add_api_route("/whisper/download", whatsapp_bridge.whatsapp_bridge_download_whisper, methods=["POST"])
    bridge_router.add_api_route("/templates/sync", whatsapp_bridge.whatsapp_bridge_sync_templates, methods=["POST"])
    bridge_router.add_api_route("/voice/preflight", whatsapp_bridge.whatsapp_bridge_voice_preflight, methods=["POST"])
    bridge_router.add_api_route("/voice/enable", whatsapp_bridge.whatsapp_bridge_voice_enable, methods=["POST"])
    bridge_router.add_api_route("/voice/disable", whatsapp_bridge.whatsapp_bridge_voice_disable, methods=["POST"])
    bridge_router.add_api_route("/voice/calls", whatsapp_bridge.whatsapp_bridge_voice_calls, methods=["GET"])
    return bridge_router


__all__ = ["create_whatsapp_bridge_router", "router"]
