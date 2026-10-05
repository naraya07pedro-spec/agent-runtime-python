"""Real PostgreSQL, synthetic GitHub HTTP contract, no live external issue creation."""

import json

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.domain import ApprovalDecision
from app.github_provider import ProviderTools
from app.tables import ToolCall
from app.tools import Registry
from tests.conftest import ScriptedProvider, turn

pytestmark = pytest.mark.integration


async def test_approved_github_commit_timeout_reconciles_without_a_second_creation(rig):
    rig.settings.allowed_tools = frozenset({"create_issue"})
    rig.settings.github_token = SecretStr("synthetic-github-token")
    rig.settings.github_repository = "synthetic-owner/synthetic-repo"
    rig.settings.github_actor = "synthetic-bot"
    rig.runtime.registry = Registry(rig.settings.allowed_tools)
    rig.runtime.provider = ScriptedProvider(
        turn(
            {
                "kind": "tool",
                "tool": "create_issue",
                "arguments": {"title": "Synthetic runtime contract", "body": "Synthetic fixture"},
            }
        ),
        turn({"kind": "finish", "text": "Verified synthetic provider outcome"}),
    )
    committed, methods = [], []

    def handle(request):
        methods.append(request.method)
        if request.method == "POST":
            body = json.loads(request.content)
            committed.append({"id": 7001, "number": 7, **body, "user": {"login": "synthetic-bot"}})
            raise httpx.ReadTimeout("synthetic provider committed before timeout", request=request)
        return httpx.Response(
            200, json=committed, headers={"X-GitHub-Request-ID": "SYNTHETIC:lookup"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        rig.runtime.tools = ProviderTools(client, rig.settings)
        eid = await rig.create("synthetic github contract")
        await rig.runtime.advance("w", eid)
        action = (await rig.store.get(eid)).action
        assert methods == []
        await rig.store.decide_approval(
            action.id,
            ApprovalDecision(fingerprint=action.fingerprint, approve=True),
            "synthetic-approver",
        )
        await rig.runtime.advance("w", eid)
        assert (await rig.store.get(eid)).state == "RECONCILIATION_REQUIRED"
        await rig.runtime.reconcile(eid, "reconciler")
        assert (await rig.store.get(eid)).reconciliation.status == "RESOLVED"
        await rig.runtime.advance("w", eid)
    assert (await rig.store.get(eid)).state == "SUCCEEDED"
    assert methods == ["POST", "GET"]
    async with rig.store.sessions() as session:
        call = await session.scalar(select(ToolCall).where(ToolCall.execution_id == eid))
        assert call.outcome["provider_request_id"] == "SYNTHETIC:lookup"
        assert call.external_id == "synthetic-owner/synthetic-repo#7"
        assert call.attempt == 1
