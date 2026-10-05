from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.identity import Credential, TenantConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RUNTIME_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://runtime:runtime@localhost:5432/runtime"
    api_key: SecretStr | None = None
    approval_key: SecretStr | None = None
    operator_key: SecretStr | None = None
    tenants: tuple[TenantConfig, ...] = Field(default=(), repr=False, exclude=True)
    webhook_secret: SecretStr | None = None
    tool_api_token: SecretStr | None = None
    tool_base_url: str = "http://sandbox:8001"
    allow_local_sandbox: bool = False
    allowed_tools: frozenset[str] = frozenset({"lookup_customer"})
    model_provider: Literal["fake", "openai"] = "fake"
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    model_timeout: float = Field(default=10.0, ge=0.01, le=60)
    tool_timeout: float = Field(default=10.0, ge=0.01, le=60)
    lease_seconds: int = Field(default=60, ge=5, le=300)
    approval_seconds: int = Field(default=900, ge=1, le=86400)
    retry_base_seconds: float = Field(default=1.0, ge=0, le=60)
    max_retries: int = Field(default=3, ge=0, le=5)
    max_request_bytes: int = Field(default=65536, ge=1024, le=1048576)
    requests_per_minute: int = Field(default=120, ge=1, le=100000)
    worker_poll_seconds: float = Field(default=1.0, ge=0.01, le=60)
    reconciliation_attempts: int = Field(default=3, ge=1, le=10)
    reconciliation_seconds: int = Field(default=3600, ge=1, le=86400)
    reconciliation_backoff_seconds: float = Field(default=5, ge=0, le=300)
    tenant_queue_capacity: int = Field(default=1000, ge=1, le=100000)
    tenant_admissions_per_minute: int = Field(default=120, ge=1, le=100000)
    worker_metrics_port: int = Field(default=0, ge=0, le=65535)
    worker_drain_seconds: float = Field(default=30, ge=1, le=300)
    recovery_read_only: bool = False
    github_token: SecretStr | None = None
    github_actor: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.\[\]-]{1,80}$")
    github_repository: str | None = Field(
        default=None, max_length=100, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
    )

    @model_validator(mode="after")
    def safe_configuration(self) -> Self:
        if self.lease_seconds <= self.model_timeout + self.tool_timeout + 2:
            raise ValueError("lease must exceed model + tool timeout plus commit margin")
        keys = [
            c.key.get_secret_value()
            for t in self.identities()
            for c in (*t.api_keys, *t.approval_keys, *t.operator_keys)
        ]
        if any(len(k) < 32 for k in keys) or len(set(keys)) != len(keys):
            raise ValueError("API and approval keys must be distinct and at least 32 characters")
        if self.webhook_secret and len(self.webhook_secret.get_secret_value()) < 32:
            raise ValueError("webhook secret must be at least 32 characters")
        ids = [t.id for t in self.tenants]
        if "legacy" in ids or len(ids) != len(set(ids)):
            raise ValueError("tenant IDs must be unique; legacy is reserved")
        for tenant in self.tenants:
            if any(len(s.get_secret_value()) < 32 for s in tenant.webhook_secrets):
                raise ValueError("tenant webhook secrets must be at least 32 characters")
            for credentials in (tenant.api_keys, tenant.approval_keys, tenant.operator_keys):
                if len({c.id for c in credentials}) != len(credentials):
                    raise ValueError("credential audit IDs must be unique per tenant and role")
        webhook_keys = [s.get_secret_value() for t in self.identities() for s in t.webhook_secrets]
        if len(webhook_keys) != len(set(webhook_keys)) or set(webhook_keys) & set(keys):
            raise ValueError("webhook secrets must be distinct per tenant and from bearer keys")
        repositories = [
            t.github_repository.lower() for t in self.identities() if t.github_repository
        ]
        if len(repositories) != len(set(repositories)):
            raise ValueError("GitHub repositories must be distinct per tenant")
        return self

    def identities(self) -> tuple[TenantConfig, ...]:
        def credential(key: SecretStr | None, role: str) -> tuple[Credential, ...]:
            return (Credential(id=f"legacy-{role}", key=key),) if key else ()

        return (
            TenantConfig(
                id="legacy",
                api_keys=credential(self.api_key, "api"),
                approval_keys=credential(self.approval_key, "approval"),
                operator_keys=credential(self.operator_key, "operator"),
                webhook_secrets=(self.webhook_secret,) if self.webhook_secret else (),
                allowed_tools=self.allowed_tools,
                github_repository=self.github_repository,
            ),
            *self.tenants,
        )

    def tenant(self, tenant_id: str) -> TenantConfig:
        from app.domain import Fault

        for tenant in self.identities():
            if tenant.id == tenant_id:
                return tenant
        raise Fault("tenant_not_configured", 403)

    def validate_tool_endpoint(self) -> None:
        from app.domain import ExternalFault

        parsed = urlsplit(self.tool_base_url)
        local = parsed.hostname in {"sandbox", "localhost", "127.0.0.1"}
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ExternalFault("unsafe_tool_endpoint")
        if parsed.scheme != "https" and not (
            self.allow_local_sandbox and local and parsed.scheme == "http"
        ):
            raise ExternalFault("unsafe_tool_endpoint")
        if not self.tool_api_token or not parsed.hostname:
            raise ExternalFault("tool_configuration_missing")
