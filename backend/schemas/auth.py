"""Pydantic schemas for auth."""

from typing import Any, Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str
    client_id: str = None
    machine_id: str = None
    app_version: Optional[str] = None


class GoogleLoginRequest(BaseModel):
    credential: str
    machine_id: str = None
    app_version: Optional[str] = None


class LoginResponse(BaseModel):
    success: bool
    message: str
    user_data: dict = None
    permissions: dict = None
    access_token: Optional[str] = None


class AuthRequest(BaseModel):
    loja: str
    client_id: str
    client_secret: str
    store_id: Optional[str] = None


class TokenRequest(BaseModel):
    token: str


__all__ = [
    "LoginRequest",
    "GoogleLoginRequest",
    "LoginResponse",
    "AuthRequest",
    "TokenRequest",
]
