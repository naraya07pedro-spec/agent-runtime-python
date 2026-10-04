from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RUNTIME_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://runtime:runtime@localhost:5432/runtime"
    api_key: SecretStr | None = None
    approval_key: SecretStr | None = None
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

    @model_validator(mode="after")
    def safe_configuration(self) -> Self:
        if self.lease_seconds <= self.model_timeout + self.tool_timeout + 2:
            raise ValueError("lease must exceed model + tool timeout plus commit margin")
        keys = [s.get_secret_value() for s in (self.api_key, self.approval_key) if s]
        if any(len(k) < 32 for k in keys) or len(set(keys)) != len(keys):
            raise ValueError("API and approval keys must be distinct and at least 32 characters")
        if self.webhook_secret and len(self.webhook_secret.get_secret_value()) < 32:
            raise ValueError("webhook secret must be at least 32 characters")
        return self

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
