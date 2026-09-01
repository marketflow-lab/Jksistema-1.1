"""Response contracts for local Firebase credential provisioning."""

from typing import Literal

from pydantic import BaseModel


class FirebaseProvisioningResponse(BaseModel):
    """Sanitized provisioning state returned to the local administrator UI."""

    success: bool
    configured: bool
    ready: bool
    source: Literal["canonical", "legacy", "none"]
    code: str
    restart_required: bool
    can_migrate: bool
    replacement_required: bool


__all__ = ["FirebaseProvisioningResponse"]
