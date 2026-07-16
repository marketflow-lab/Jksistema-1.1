"""Componentes isolados do domínio WhatsApp.

O módulo legado :mod:`backend.services.whatsapp_bridge` continua sendo a
fachada de compatibilidade enquanto as responsabilidades são extraídas de
forma incremental para este pacote.
"""

from .contracts import (
    WhatsappAdhocMessageRequest,
    WhatsappBindingRevokeRequest,
    WhatsappBridgeConfigRequest,
    WhatsappPairingCodeRequest,
    WhatsappPhoneRegistrationRequest,
    WhatsappPhoneSettingsRequest,
    WhatsappTemplatesRequest,
    WhatsappVoiceToggleRequest,
)

__all__ = [
    "WhatsappAdhocMessageRequest",
    "WhatsappBindingRevokeRequest",
    "WhatsappBridgeConfigRequest",
    "WhatsappPairingCodeRequest",
    "WhatsappPhoneRegistrationRequest",
    "WhatsappPhoneSettingsRequest",
    "WhatsappTemplatesRequest",
    "WhatsappVoiceToggleRequest",
]
