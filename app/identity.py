"""Server-bound tenant identities. Configuration is not returned through the API."""

import hmac
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.domain import Fault

type Role = Literal["api", "approval", "operator"]


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,32}$")
    key: SecretStr = Field(repr=False, exclude=True)


class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    api_keys: tuple[Credential, ...] = Field(default=(), repr=False, exclude=True)
    approval_keys: tuple[Credential, ...] = Field(default=(), repr=False, exclude=True)
    operator_keys: tuple[Credential, ...] = Field(default=(), repr=False, exclude=True)
    webhook_secrets: tuple[SecretStr, ...] = Field(default=(), repr=False, exclude=True)
    allowed_tools: frozenset[str] = frozenset()
    github_repository: str | None = Field(
        default=None, max_length=100, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
    )


@dataclass(frozen=True)
class AuthContext:
    tenant_id: str
    role: Role
    credential_id: str


def resolve_identity(
    header: str | None, role: Role, tenants: tuple[TenantConfig, ...]
) -> AuthContext:
    configured = False
    result = None
    presented = (header or "").encode()
    for tenant in tenants:
        keys = getattr(tenant, f"{role}_keys")
        for credential in keys:
            configured = True
            if hmac.compare_digest(
                presented, ("Bearer " + credential.key.get_secret_value()).encode()
            ):
                result = AuthContext(tenant.id, role, credential.id)
    if not configured:
        raise Fault("authentication_not_configured", 503)
    if result is None:
        raise Fault("unauthorized", 401)
    return result
