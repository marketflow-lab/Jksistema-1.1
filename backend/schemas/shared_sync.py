"""Pydantic schemas for shared sync."""

from typing import Any, Optional

from pydantic import BaseModel


class SharedSyncScopeConfigRequest(BaseModel):
    enabled: bool = False
    allowed_users: Optional[list[str]] = None
    auto_pull: bool = False
    auto_push: bool = False
    share_between_users: bool = False
    conflict: Optional[str] = "latest_wins"


class SharedSyncConfigRequest(BaseModel):
    scopes: dict = {}


class SharedSyncRunRequest(BaseModel):
    scopes: Optional[list[str]] = None
    machine_id: Optional[str] = None
    operation_id: str = ""


class SharedSyncPreviewRequest(BaseModel):
    direction: str = "pull"
    scopes: Optional[list[str]] = None
    machine_id: Optional[str] = None


class SharedSyncMachineConfigRequest(BaseModel):
    enabled: bool = False
    scopes: Optional[list[str]] = None
    auto_pull: bool = False
    auto_push: bool = False
    machine_id: Optional[str] = None


class SharedSyncUserInviteCreateRequest(BaseModel):
    target_username: str = ""
    target_client_id: Optional[str] = None
    scopes: Optional[list[str]] = None
    keep_synced: bool = False
    message: Optional[str] = ""
    machine_id: Optional[str] = None


class SharedSyncUserInviteActionRequest(BaseModel):
    keep_synced: bool = False
    machine_id: Optional[str] = None


class SharedSyncUserLinkUpdateRequest(BaseModel):
    keep_synced: Optional[bool] = None
    active: Optional[bool] = None


class SharedSyncUserLinkRunRequest(BaseModel):
    scopes: Optional[list[str]] = None
    machine_id: Optional[str] = None
    operation_id: str = ""


class SharedSyncUserLinkCreateRequest(BaseModel):
    target_username: str = ""
    target_client_id: Optional[str] = None
    scopes: Optional[list[str]] = None
    message: Optional[str] = ""
    machine_id: Optional[str] = None


__all__ = [
    "SharedSyncScopeConfigRequest",
    "SharedSyncConfigRequest",
    "SharedSyncRunRequest",
    "SharedSyncPreviewRequest",
    "SharedSyncMachineConfigRequest",
    "SharedSyncUserInviteCreateRequest",
    "SharedSyncUserInviteActionRequest",
    "SharedSyncUserLinkUpdateRequest",
    "SharedSyncUserLinkRunRequest",
    "SharedSyncUserLinkCreateRequest",
]
