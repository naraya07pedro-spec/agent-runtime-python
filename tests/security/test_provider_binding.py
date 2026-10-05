import pytest
from pydantic import SecretStr

from app.domain import ApprovalDecision
from app.tools import Registry
from tests.conftest import ScriptedProvider, turn

pytestmark = pytest.mark.security


async def test_changed_github_target_cannot_inherit_approval(rig):
    rig.settings.allowed_tools = frozenset({"create_issue"})
    rig.settings.github_token = SecretStr("synthetic-provider-token")
    rig.settings.github_repository = "synthetic-owner/approved-repo"
    rig.settings.github_actor = "synthetic-bot"
    rig.runtime.registry = Registry(rig.settings.allowed_tools)
    rig.runtime.provider = ScriptedProvider(
        turn(
            {
                "kind": "tool",
                "tool": "create_issue",
                "arguments": {"title": "Synthetic approval", "body": "Synthetic fixture"},
            }
        )
    )
    from app.github_provider import ProviderTools

    rig.runtime.tools = ProviderTools(rig.client, rig.settings)
    eid = await rig.create("synthetic provider binding")
    await rig.runtime.advance("w", eid)
    action = (await rig.store.get(eid)).action
    assert action.provider_binding == "github:synthetic-owner/approved-repo:synthetic-bot"
    await rig.store.decide_approval(
        action.id,
        ApprovalDecision(fingerprint=action.fingerprint, approve=True),
        "synthetic-approver",
    )
    rig.settings.github_repository = "synthetic-owner/unapproved-repo"
    await rig.runtime.advance("w", eid)
    view = await rig.store.get(eid)
    assert view.error_code == "action_fingerprint_changed"
    assert await rig.effects() == 0
