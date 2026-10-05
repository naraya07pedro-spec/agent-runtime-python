import hashlib
import hmac
import json
import time
from dataclasses import replace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from app.api import create_app
from app.domain import CreateExecution, Fault
from app.identity import Credential
from app.tables import Approval, ToolCall
from tests.tenants import configure_tenants

pytestmark = pytest.mark.security


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", ""),
        ("GET", "/events"),
        ("POST", "/advance"),
        ("POST", "/cancel"),
        ("POST", "/reconcile"),
    ],
)
async def test_cross_tenant_ids_reveal_no_state_and_allow_no_write(rig, method, path):
    alpha, beta = configure_tenants(rig)
    eid, _ = await alpha.store.create(
        CreateExecution(business_key="same", prompt="notify customer demo"), "same", uuid4()
    )
    await alpha.advance("alpha-worker", eid)
    before = await alpha.store.get(eid)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(rig.settings, rig.runtime)),
        base_url="http://runtime",
    ) as client:
        response = await client.request(
            method, f"/executions/{eid}{path}", headers={"Authorization": "Bearer " + "i" * 40}
        )
    assert response.status_code == 404
    assert str(before.action.id) not in response.text
    assert await alpha.store.get(eid) == before
    assert await rig.effects() == 0
    with pytest.raises(Fault, match="execution_not_found"):
        await beta.store.history(eid)


async def test_approval_and_operator_roles_cannot_cross_tenants_or_replace_api_role(rig):
    alpha, _ = configure_tenants(rig)
    eid, _ = await alpha.store.create(
        CreateExecution(business_key="approval", prompt="notify customer demo"), "approval", uuid4()
    )
    await alpha.advance("w", eid)
    action = (await alpha.store.get(eid)).action
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(rig.settings, rig.runtime)),
        base_url="http://runtime",
    ) as client:
        denied = await client.post(
            f"/approvals/{action.id}/decision",
            headers={"Authorization": "Bearer " + "j" * 40},
            json={"fingerprint": action.fingerprint, "approve": True},
        )
        assert denied.status_code == 404
        operator = await client.post(
            f"/executions/{eid}/operator",
            headers={"Authorization": "Bearer " + "k" * 40},
            json={"action": "abandon", "reason": "operator_decision"},
        )
        assert operator.status_code == 404
        for key in ("f", "g", "i"):
            invalid_role = await client.post(
                f"/approvals/{action.id}/decision",
                headers={"Authorization": "Bearer " + key * 40},
                json={"fingerprint": action.fingerprint, "approve": True},
            )
            if key == "f":
                assert invalid_role.status_code == 200
            else:
                assert invalid_role.status_code == 401
    async with rig.store.sessions() as session:
        approval = await session.get(Approval, action.id)
        assert approval.actor == "alpha-approval"
    assert await rig.effects() == 0


async def test_tenant_header_cannot_override_bound_key_and_metrics_are_scoped(rig):
    alpha, beta = configure_tenants(rig)
    await alpha.store.create(
        CreateExecution(business_key="visible", prompt="lookup customer demo"), "visible", uuid4()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(rig.settings, rig.runtime)),
        base_url="http://runtime",
    ) as client:
        headers = {"Authorization": "Bearer " + "i" * 40, "X-Tenant-ID": "alpha"}
        response = await client.post(
            "/executions",
            headers={**headers, "Idempotency-Key": "beta"},
            json={"business_key": "beta", "prompt": "lookup customer demo"},
        )
        assert response.json()["tenant_id"] == "beta"
        metrics = await client.get("/metrics", headers=headers)
        assert 'runtime_executions{state="CREATED"} 1' in metrics.text
        assert "runtime_requests_total" not in metrics.text
        assert (await client.get("/process-metrics", headers=headers)).status_code in (401, 503)
    assert (await beta.store.get(UUID(response.json()["id"]))).tenant_id == "beta"


async def test_same_business_action_and_webhook_nonce_are_independent_per_tenant(rig):
    alpha, beta = configure_tenants(rig)
    payload = {"business_key": "same-identity", "prompt": "lookup customer demo"}
    raw = json.dumps(payload).encode()
    stamp, nonce = str(int(time.time())), "synthetic-shared-nonce"
    ids = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(rig.settings, rig.runtime)),
        base_url="http://runtime",
    ) as client:
        for tenant, key in (("alpha", "h"), ("beta", "l")):
            signature = (
                "sha256="
                + hmac.new(
                    (key * 40).encode(),
                    stamp.encode() + b"." + nonce.encode() + b"." + raw,
                    hashlib.sha256,
                ).hexdigest()
            )
            headers = {
                "X-Timestamp": stamp,
                "X-Nonce": nonce,
                "X-Signature": signature,
                "Content-Type": "application/json",
            }
            response = await client.post(f"/webhooks/{tenant}", content=raw, headers=headers)
            assert response.status_code == 200
            ids.append(response.json()["id"])
            assert (
                await client.post(f"/webhooks/{tenant}", content=raw, headers=headers)
            ).status_code == 409
            other = "beta" if tenant == "alpha" else "alpha"
            assert (
                await client.post(f"/webhooks/{other}", content=raw, headers=headers)
            ).status_code == 401
    await alpha.advance("alpha", UUID(ids[0]))
    await beta.advance("beta", UUID(ids[1]))
    async with rig.store.sessions() as session:
        operations = (await session.scalars(select(ToolCall.operation_key))).all()
    assert len(set(operations)) == 2


async def test_tenant_policy_denies_write_and_lease_cannot_change_tenant(rig):
    _, beta = configure_tenants(rig)
    eid, _ = await beta.store.create(
        CreateExecution(business_key="denied", prompt="create ticket demo"), "denied", uuid4()
    )
    await beta.advance("w", eid)
    assert (await beta.store.get(eid)).error_code == "tool_not_allowed"
    assert await rig.effects() == 0
    second, _ = await beta.store.create(
        CreateExecution(business_key="lease", prompt="lookup customer demo"), "lease", uuid4()
    )
    work = await beta.store.claim("w", second)
    with pytest.raises(Fault, match="stale_lease"):
        await beta.store.fail(replace(work.lease, tenant_id="alpha"), "forged")


async def test_key_rotation_overlap_and_revocation_preserve_tenant_identity(rig):
    configure_tenants(rig)
    tenant = rig.settings.tenants[0]
    new_key = Credential(id="alpha-api-v2", key="m" * 40)
    tenant.api_keys = (*tenant.api_keys, new_key)
    rig.settings.safe_configuration()
    app = create_app(rig.settings, rig.runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime"
    ) as client:
        for prefix in ("e", "m"):
            result = await client.post(
                "/executions",
                headers={"Authorization": "Bearer " + prefix * 40, "Idempotency-Key": "rotate"},
                json={"business_key": "rotate", "prompt": "lookup customer demo"},
            )
            assert result.status_code in (200, 201)
            assert result.json()["tenant_id"] == "alpha"
        tenant.api_keys = (new_key,)
        assert (
            await client.get("/metrics", headers={"Authorization": "Bearer " + "e" * 40})
        ).status_code == 401
        assert (
            await client.get("/metrics", headers={"Authorization": "Bearer " + "m" * 40})
        ).status_code == 200
