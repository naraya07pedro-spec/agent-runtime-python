import asyncio
from uuid import uuid4

import pytest

from app.domain import ApprovalDecision, CreateExecution, Fault, State

pytestmark = [pytest.mark.integration, pytest.mark.concurrency]


async def test_twenty_duplicate_requests_have_one_identity_and_creation_event(rig):
    request = CreateExecution(business_key="duplicate-delivery", prompt="create ticket demo")
    results = await asyncio.gather(
        *[rig.store.create(request, "same-key", uuid4()) for _ in range(20)]
    )
    assert len({eid for eid, _ in results}) == 1
    assert sum(int(created) for _, created in results) == 1
    history = await rig.store.history(results[0][0])
    assert [event.kind for event in history.items] == ["created"]


async def test_twenty_workers_cannot_own_same_lease(rig):
    eid = await rig.create()
    work = await asyncio.gather(*[rig.store.claim(f"worker-{i}", eid) for i in range(20)])
    assert sum(item is not None for item in work) == 1


async def test_concurrent_advance_produces_one_external_write(rig):
    eid = await rig.create("create ticket demo")
    await asyncio.gather(*[rig.runtime.advance(f"worker-{i}", eid) for i in range(20)])
    assert await rig.effects() == 1
    history = await rig.store.history(eid)
    assert sum(e.kind == "dispatch_intent" for e in history.items) == 1


async def test_stale_owner_cannot_complete_or_renew_work(rig):
    eid = await rig.create()
    stale = await rig.store.claim("stale", eid)
    await rig.expire(eid)
    assert await rig.store.recover() == 1
    current = await rig.store.claim("new", eid)
    assert current.lease.token != stale.lease.token
    with pytest.raises(Fault, match="stale_lease"):
        await rig.store.finish(stale.lease, "false success")
    assert (await rig.store.get(eid)).state == State.RUNNING
    await rig.store.finish(current.lease, "new owner result")
    assert (await rig.store.get(eid)).outcome["text"] == "new owner result"


async def test_expired_worker_can_send_late_but_absent_lookup_never_authorizes_replay(rig):
    eid = await rig.create("notify customer demo")
    await rig.runtime.advance("proposer", eid)
    action = (await rig.store.get(eid)).action
    await rig.store.decide_approval(
        action.id, ApprovalDecision(fingerprint=action.fingerprint, approve=True), "human"
    )
    entered, release = asyncio.Event(), asyncio.Event()
    original = rig.runtime.tools.execute

    async def delayed(call):
        entered.set()
        await release.wait()
        return await original(call)

    rig.runtime.tools.execute = delayed
    old_worker = asyncio.create_task(rig.runtime.advance("old-worker", eid))
    async with asyncio.timeout(5):
        await entered.wait()
    await rig.expire(eid)
    await rig.store.recover()
    await rig.runtime.reconcile(eid, "reconciler")
    assert (await rig.store.get(eid)).error_code == "provider_absent_no_replay"
    assert await rig.runtime.advance("new-worker", eid) is None
    assert await rig.effects() == 0
    release.set()
    with pytest.raises(Fault, match="stale_lease"):
        await old_worker
    assert await rig.effects() == 1
    await rig.runtime.reconcile(eid, "reconciler-2")
    assert (await rig.store.get(eid)).state == State.CREATED
    assert await rig.effects() == 1


async def test_approval_decisions_race_atomically(rig):
    eid = await rig.create("notify customer demo")
    await rig.runtime.advance("proposer", eid)
    action = (await rig.store.get(eid)).action
    outcomes = await asyncio.gather(
        *[
            rig.store.decide_approval(
                action.id,
                ApprovalDecision(fingerprint=action.fingerprint, approve=choice),
                f"human-{choice}",
            )
            for choice in [True, False]
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, Fault) for result in outcomes) == 1
