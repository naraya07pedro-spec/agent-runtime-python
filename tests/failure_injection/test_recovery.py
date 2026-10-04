import asyncio

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.domain import ExternalFault, State
from app.store import Store
from tests.conftest import ScriptedProvider

pytestmark = [pytest.mark.integration, pytest.mark.failure]


class ProcessCrash(BaseException):
    pass


def inject_once(target, error):
    fired = False

    def fault(stage):
        nonlocal fired
        if stage == target and not fired:
            fired = True
            raise error

    return fault


async def test_external_success_plus_failed_commit_is_reconciled_without_replay(rig):
    rig.store.fault = inject_once(
        "before_outcome_commit", SQLAlchemyError("injected database failure")
    )
    eid = await rig.create("create ticket demo")
    await rig.runtime.advance("w", eid)
    assert await rig.effects() == 1
    view = await rig.store.get(eid)
    assert view.state == State.RECONCILIATION_REQUIRED and view.action.status == "DISPATCHED"
    assert await rig.runtime.advance("another-worker", eid) is None
    await rig.runtime.reconcile(eid, "reconciler")
    assert (await rig.store.get(eid)).state == State.CREATED
    await rig.runtime.advance("w2", eid)
    assert (await rig.store.get(eid)).state == State.SUCCEEDED
    assert await rig.effects() == 1
    assert "reconciled" in [e.kind for e in (await rig.store.history(eid)).items]


async def test_database_remains_down_after_effect_then_restart_recovers_durable_intent(rig):
    def fail(stage):
        if stage in {"before_outcome_commit", "before_reconciliation_commit"}:
            raise SQLAlchemyError("db unavailable")

    rig.store.fault = fail
    eid = await rig.create("create ticket demo")
    with pytest.raises(SQLAlchemyError):
        await rig.runtime.advance("w", eid)
    assert (await rig.store.get(eid)).state == State.TOOL_EXECUTING
    assert await rig.effects() == 1
    await rig.expire(eid)
    rig.runtime.store = Store(rig.store.sessions, rig.settings)
    await rig.store.recover()
    assert (await rig.store.get(eid)).state == State.RECONCILIATION_REQUIRED
    await rig.runtime.reconcile(eid, "fresh-process")
    assert await rig.effects() == 1


async def test_database_failure_before_dispatch_prevents_external_effect(rig):
    rig.store.fault = inject_once("before_dispatch_commit", SQLAlchemyError("commit failure"))
    eid = await rig.create("create ticket demo")
    with pytest.raises(SQLAlchemyError):
        await rig.runtime.advance("w", eid)
    assert await rig.effects() == 0
    assert (await rig.store.get(eid)).action.status == "PROPOSED"
    await rig.expire(eid)
    await rig.store.recover()
    await rig.runtime.advance("w2", eid)
    assert await rig.effects() == 1


@pytest.mark.parametrize(
    "stage,effects", [("after_dispatch_commit", 0), ("after_external_success", 1)]
)
async def test_crash_after_durable_intent_never_causes_blind_write_retry(rig, stage, effects):
    rig.store.fault = inject_once(stage, ProcessCrash())
    eid = await rig.create("create ticket demo")
    with pytest.raises(ProcessCrash):
        await rig.runtime.advance("crashed-worker", eid)
    assert await rig.effects() == effects
    await rig.expire(eid)
    await rig.store.recover()
    await rig.runtime.reconcile(eid, "restarted-worker")
    expected = State.CREATED if effects else State.RECONCILIATION_REQUIRED
    assert (await rig.store.get(eid)).state == expected
    assert await rig.effects() == effects


async def test_read_tool_can_retry_after_crash_with_same_operation_identity(rig):
    rig.store.fault = inject_once("after_dispatch_commit", ProcessCrash())
    eid = await rig.create()
    with pytest.raises(ProcessCrash):
        await rig.runtime.advance("w", eid)
    before = await rig.store.inspect_call((await rig.store.get(eid)).action.id)
    await rig.expire(eid)
    await rig.store.recover()
    await rig.runtime.advance("w2", eid)
    after = await rig.store.inspect_call(before.call_id)
    assert after.operation_key == before.operation_key and after.attempt == 2


@pytest.mark.parametrize("failure", ["timeout", "malformed", "500", "429", "400"])
async def test_any_uncertain_dispatched_write_requires_reconciliation(rig, failure):
    original = rig.runtime.tools.execute

    async def faulty(call):
        await original(call)
        if failure == "timeout":
            raise TimeoutError()
        raise ExternalFault(
            "injected_" + failure, transient=failure in {"500", "429"}, ambiguous=True
        )

    rig.runtime.tools.execute = faulty
    eid = await rig.create("create ticket demo")
    await rig.runtime.advance("w", eid)
    assert (await rig.store.get(eid)).state == State.RECONCILIATION_REQUIRED
    assert await rig.effects() == 1
    await rig.runtime.reconcile(eid, "r")
    assert await rig.effects() == 1


async def test_malformed_model_never_reaches_tool(rig):
    rig.runtime.provider = ScriptedProvider(
        {
            "decision": {"kind": "tool", "tool": "upsert_ticket", "arguments": "not a mapping"},
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    )
    eid = await rig.create()
    await rig.runtime.advance("w", eid)
    assert (await rig.store.get(eid)).error_code == "model_response_invalid"
    assert await rig.effects() == 0


async def test_cancelled_worker_leaves_recoverable_intent(rig):
    rig.store.fault = inject_once("after_dispatch_commit", asyncio.CancelledError())
    eid = await rig.create("create ticket demo")
    with pytest.raises(asyncio.CancelledError):
        await rig.runtime.advance("w", eid)
    await rig.expire(eid)
    await rig.store.recover()
    assert (await rig.store.get(eid)).state == State.RECONCILIATION_REQUIRED
    assert await rig.effects() == 0
