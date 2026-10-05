import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.domain import Fault
from app.identity import Credential, TenantConfig, resolve_identity


def test_config_rejects_cross_tenant_key_reuse_and_hides_invalid_secret_inputs():
    marker = "synthetic-private-value-short"
    with pytest.raises(ValidationError) as invalid:
        Settings(_env_file=None, api_key=marker)
    assert marker not in str(invalid.value)
    tenants = tuple(
        TenantConfig(id=name, api_keys=(Credential(id="same-key", key="x" * 40),))
        for name in ("alpha", "beta")
    )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, tenants=tenants)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, tenants=(TenantConfig(id="legacy"),))
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            tenants=(
                TenantConfig(id="alpha", webhook_secrets=(SecretStr("x" * 40),)),
                TenantConfig(id="beta", webhook_secrets=(SecretStr("x" * 40),)),
            ),
        )


def test_missing_and_wrong_role_keys_fail_closed_and_repositories_do_not_overlap():
    tenant = TenantConfig(id="alpha", api_keys=(Credential(id="a1", key="x" * 40),))
    assert resolve_identity("Bearer " + "x" * 40, "api", (tenant,)).tenant_id == "alpha"
    with pytest.raises(Fault, match="authentication_not_configured"):
        resolve_identity("Bearer " + "x" * 40, "operator", (tenant,))
    with pytest.raises(Fault, match="unauthorized"):
        resolve_identity("Bearer wrong", "api", (tenant,))
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            github_repository="same/repo",
            tenants=(TenantConfig(id="alpha", github_repository="SAME/REPO"),),
        )
