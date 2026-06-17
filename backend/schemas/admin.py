"""Pydantic schemas for admin."""

from typing import Any, Optional

from pydantic import BaseModel


class AdminUserUpsertRequest(BaseModel):
    username: str
    original_username: Optional[str] = None
    password: Optional[str] = None
    name: str = ""
    email: str = ""
    client_id: str = "default"
    permissions: dict = {}
    active: bool = True
    valid_until: Optional[str] = None
    machine_id: Optional[str] = None
    max_machines: Optional[int] = 1


class AdminUserPasswordRequest(BaseModel):
    password: str


class UserChangePasswordRequest(BaseModel):
    current_password: str = ""
    new_password: str = ""
    confirm_password: Optional[str] = None


class AdminUserMaxMachinesRequest(BaseModel):
    max_machines: int


class AdminUserPermissionsRequest(BaseModel):
    permissions: dict = {}


class AdminUserStatusRequest(BaseModel):
    active: bool


class AdminUserMessageRequest(BaseModel):
    username: str
    client_id: Optional[str] = None
    title: Optional[str] = ""
    message: str


class UserChatAttachment(BaseModel):
    name: str = ""
    mime_type: Optional[str] = "application/octet-stream"
    data_base64: str = ""
    size: Optional[int] = 0


class UserChatMessageRequest(BaseModel):
    username: str
    client_id: Optional[str] = None
    message: str
    attachments: Optional[list[UserChatAttachment]] = None
    call: Optional[dict[str, Any]] = None


class UserChatTypingRequest(BaseModel):
    username: str
    client_id: Optional[str] = None
    typing: bool = True


class MachinePresenceHeartbeatRequest(BaseModel):
    machine_id: Optional[str] = None
    page: Optional[str] = ""
    app_version: Optional[str] = None


__all__ = [
    "AdminUserUpsertRequest",
    "AdminUserPasswordRequest",
    "UserChangePasswordRequest",
    "AdminUserMaxMachinesRequest",
    "AdminUserPermissionsRequest",
    "AdminUserStatusRequest",
    "AdminUserMessageRequest",
    "UserChatAttachment",
    "UserChatMessageRequest",
    "UserChatTypingRequest",
    "MachinePresenceHeartbeatRequest",
]
