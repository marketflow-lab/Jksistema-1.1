"""Closed, versioned inputs. The caller never supplies the acting tenant."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StoreCreate(ClosedModel):
    name: str = Field(min_length=1, max_length=100)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class StoreGrant(ClosedModel):
    username: str = Field(min_length=1, max_length=128)
    access: Literal["read", "write", "revoke"]


class ConnectRequest(ClosedModel):
    provider: Literal["mercadolivre", "bling"]
    app_id: str = Field(min_length=1, max_length=200)
    app_secret: SecretStr = Field(min_length=1, max_length=512)


class ProviderRequest(ClosedModel):
    provider: Literal["mercadolivre", "bling"]
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=1, max_length=2048)
    params: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class DisconnectRequest(ClosedModel):
    provider: Literal["mercadolivre", "bling"]


class LegacyConnection(ClosedModel):
    provider: Literal["mercadolivre", "bling"]
    app_id: str = Field(min_length=1, max_length=200)
    app_secret: SecretStr = Field(min_length=1, max_length=512)
    access_token: SecretStr = Field(min_length=1, max_length=8192)
    refresh_token: SecretStr = Field(min_length=1, max_length=8192)
    expires_at: int = Field(gt=0)
    expected_account_id: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9_-]{0,128}$")
    expected_site_id: str = Field(default="", max_length=20, pattern=r"^[A-Za-z0-9_-]{0,20}$")


class LegacyStore(ClosedModel):
    store_id: str = Field(pattern=r"^(?:[a-f0-9]{24}|[a-f0-9]{32})$")
    name: str = Field(min_length=1, max_length=100)
    connections: list[LegacyConnection] = Field(min_length=2, max_length=2)


class LegacyAdoptionRequest(ClosedModel):
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    stores: list[LegacyStore] = Field(min_length=1, max_length=100)
