import asyncio

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.api import create_app
from app.domain import Fault, ReconcileResult, ToolResult
from app.tables import Reconciliation
from tests.conftest import ScriptedProvider, turn

pytestmark = pytest.mark.failure


async def ambiguous(rig):
    eid = await rig.create("ticket customer demo")

    def fail(stage):
        if stage == "after_external_success":
            raise SQLAlchemyError("synthetic local persistence failure")

    rig.store.fault = fail
    await rig.runtime.advance("w", eid)
    rig.store.fault = lambda _: None
    assert (await rig.store.get(eid)).reconciliation.status == "AMBIGUOUS"
    assert await rig.effects() == 1
    return eid


async def test_lookup_budget_exhaustion_is_durable_and_manual_lookup_cannot_replay(
    rig, monkeypatch
):
    rig.settings.reconciliation_attempts = 2
    eid = await ambiguous(rig)
    lookups = []

    async def unknown(*args):
        lookups.append(args)
        return ReconcileResult(status="unknown")

    monkeypatch.setattr(rig.runtime.tools, "lookup", unknown)
    for _ in range(4):
        await rig.runtime.reconcile(eid, "reconciler")
    view = await rig.store.get(eid)
    assert view.reconciliation.status == "MANUAL_REVIEW"
    assert view.reconciliation.attempts == 2
    assert len(lookups) == 2
    await rig.runtime.reconcile(eid, "operator", "synthetic-operator")
    assert (await rig.store.get(eid)).reconciliation.status == "MANUAL_REVIEW"
    assert len(lookups) == 3
    await rig.runtime.advance("w", eid)
    assert await rig.effects() == 1


async def ambiguous_second(rig):
    eid = await rig.create("ticket customer demo")
    work = await rig.store.claim("w", eid)
    spec = rig.runtime.registry.get("upsert_ticket")
    call_id = await rig.store.propose(
        work.lease, spec, {"customer_id": "synthetic-second", "summary": "synthetic"}
    )
    await rig.store.dispatch(work.lease, call_id, spec)
    await rig.store.require_reconciliation(work.lease, call_id, "synthetic_timeout")
    return eid


async def test_deadline_and_lost_reconciliation_lease_consume_budget_without_replay(rig):
    rig.settings.reconciliation_attempts = 1
    eid = await ambiguous(rig)
    lease, _ = await rig.store.claim_reconciliation(eid, "lost-process")
    await rig.expire(eid)
    assert await rig.store.claim_reconciliation(eid, "replacement") is None
    view = await rig.store.get(eid)
    assert view.reconciliation.status == "MANUAL_REVIEW"
    assert view.reconciliation.attempts == 1
    with pytest.raises(Fault, match="stale_lease"):
        await rig.store.release_reconciliation(lease, "late")
    second = await ambiguous_second(rig)
    call_id = (await rig.store.get(second)).action.id
    async with rig.store.sessions.begin() as session:
        await session.execute(
            text(
                "UPDATE reconciliations SET deadline_at=clock_timestamp()-interval '1 second' WHERE call_id=:id"
            ),
            {"id": call_id},
        )
    await rig.runtime.reconcile(second, "deadline")
    assert (await rig.store.get(second)).reconciliation.attempts == 0
    assert (await rig.store.get(second)).reconciliation.status == "MANUAL_REVIEW"


async def test_reconciliation_double_complete_and_operator_abandon_race_are_fenced(
    rig, monkeypatch
):
    eid = await ambiguous(rig)
    started, release = asyncio.Event(), asyncio.Event()
    original = rig.runtime.tools.lookup

    async def lookup(*args):
        started.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(rig.runtime.tools, "lookup", lookup)
    first = asyncio.create_task(rig.runtime.reconcile(eid, "first"))
    await started.wait()
    await rig.runtime.reconcile(eid, "second")
    with pytest.raises(Fault, match="reconciliation_in_progress"):
        await rig.store.abandon(eid, "operator", "operator_decision")
    release.set()
    await first
    view = await rig.store.get(eid)
    assert view.reconciliation.status == "RESOLVED"
    assert view.reconciliation.attempts == 1
    assert await rig.effects() == 1
    with pytest.raises(Fault, match="reconciliation_not_required"):
        await rig.store.abandon(eid, "operator", "operator_decision")


async def test_abandon_keeps_dispatch_evidence_and_rejects_late_completion(rig):
    eid = await ambiguous(rig)
    lease, call = await rig.store.claim_reconciliation(eid, "late")
    await rig.expire(eid)
    await rig.store.abandon(eid, "synthetic-operator", "operator_decision")
    view = await rig.store.get(eid)
    assert view.state == "FAILED_PERMANENT"
    assert view.outcome == {"operator_abandoned": True, "effect_unknown": True}
    assert view.action.status == "DISPATCHED"
    assert view.reconciliation.status == "ABANDONED"
    with pytest.raises(Fault, match="stale_lease"):
        await rig.store.complete_tool(
            lease,
            call.call_id,
            ToolResult(
                operation_key=call.operation_key,
                fingerprint=call.fingerprint,
                external_id="late",
                value="late",
            ),
            reconciliation=True,
        )
    assert any(event.kind == "operator_abandoned" for event in (await rig.store.history(eid)).items)
    assert await rig.runtime.advance("w", eid) is None
    assert await rig.effects() == 1


async def test_operator_endpoint_rejects_api_role_and_never_accepts_unverified_success(rig):
    rig.settings.operator_key = SecretStr("synthetic-operator-key-000000000000")
    eid = await ambiguous(rig)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(rig.settings, rig.runtime)),
        base_url="http://runtime",
    ) as client:
        path = f"/executions/{eid}/operator"
        action = {"action": "abandon", "reason": "operator_decision"}
        assert (
            await client.post(path, json=action, headers={"Authorization": "Bearer " + "a" * 40})
        ).status_code == 401
        headers = {"Authorization": "Bearer synthetic-operator-key-000000000000"}
        assert (
            await client.post(
                path, json={"action": "resolve", "reason": "verification"}, headers=headers
            )
        ).status_code == 422
        assert (await client.post(path, json=action, headers=headers)).status_code == 200


async def test_poison_model_job_is_quarantined_and_next_execution_progresses(rig):
    first = await rig.create()
    rig.runtime.provider = ScriptedProvider(
        RuntimeError("synthetic secret must not reach logs"),
        turn({"kind": "finish", "text": "synthetic success"}),
    )
    await rig.runtime.advance("w", first)
    assert (await rig.store.get(first)).error_code == "poison_job_quarantined"
    second = await rig.create()
    await rig.runtime.advance("w", second)
    assert (await rig.store.get(second)).state == "SUCCEEDED"


async def test_unexpected_exception_after_side_effect_stays_ambiguous(rig):
    eid = await rig.create("ticket customer demo")

    def fail(stage):
        if stage == "after_external_success":
            raise RuntimeError("synthetic private body")

    rig.store.fault = fail
    await rig.runtime.advance("w", eid)
    view = await rig.store.get(eid)
    assert view.error_code == "unexpected_after_dispatch"
    assert view.reconciliation.status == "AMBIGUOUS"
    assert await rig.effects() == 1


async def test_worker_style_auto_reconciliation_honors_due_time(rig):
    eid = await ambiguous(rig)
    async with rig.store.sessions.begin() as session:
        await session.execute(
            text("UPDATE reconciliations SET next_attempt_at=clock_timestamp()+interval '1 hour'")
        )
    await rig.runtime.reconcile(None, "worker")
    assert (await rig.store.get(eid)).reconciliation.attempts == 0
    async with rig.store.sessions.begin() as session:
        await session.execute(text("UPDATE reconciliations SET next_attempt_at=clock_timestamp()"))
    await rig.runtime.reconcile(None, "worker")
    async with rig.store.sessions() as session:
        recovery = await session.scalar(select(Reconciliation))
        assert recovery.status == "RESOLVED"
