"""Synthetic tenant keys and identities; never derived from private source artifacts."""

from app.identity import Credential, TenantConfig


def configure_tenants(rig):
    rig.settings.tenants = (
        TenantConfig(
            id="alpha",
            api_keys=(Credential(id="alpha-api", key="e" * 40),),
            approval_keys=(Credential(id="alpha-approval", key="f" * 40),),
            operator_keys=(Credential(id="alpha-operator", key="g" * 40),),
            webhook_secrets=("h" * 40,),
            allowed_tools=rig.settings.allowed_tools,
        ),
        TenantConfig(
            id="beta",
            api_keys=(Credential(id="beta-api", key="i" * 40),),
            approval_keys=(Credential(id="beta-approval", key="j" * 40),),
            operator_keys=(Credential(id="beta-operator", key="k" * 40),),
            webhook_secrets=("l" * 40,),
            allowed_tools=frozenset({"lookup_customer"}),
        ),
    )
    rig.settings.safe_configuration()
    return rig.runtime.for_tenant("alpha"), rig.runtime.for_tenant("beta")
