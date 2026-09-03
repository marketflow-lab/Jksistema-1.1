"""Strict HTTP contracts for the authentication gateway."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class GatewayLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=512)
    machine_id: str = Field(min_length=1, max_length=512)
    app_version: str = Field(min_length=1, max_length=64)
    request_nonce: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not normalized or any(ord(char) < 32 for char in normalized):
            raise ValueError("invalid_username")
        return normalized

    @field_validator("machine_id", "app_version")
    @classmethod
    def strip_non_secret_fields(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("required")
        return normalized


class GatewayChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=254)
    current_password: SecretStr = Field(min_length=1, max_length=512)
    new_password: SecretStr = Field(min_length=6, max_length=512)
    machine_id: str = Field(min_length=1, max_length=512)
    app_version: str = Field(min_length=1, max_length=64)
    request_nonce: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return GatewayLoginRequest.normalize_username(value)

    @field_validator("machine_id", "app_version")
    @classmethod
    def strip_non_secret_fields(cls, value: str) -> str:
        return GatewayLoginRequest.strip_non_secret_fields(value)


__all__ = ["GatewayChangePasswordRequest", "GatewayLoginRequest"]
