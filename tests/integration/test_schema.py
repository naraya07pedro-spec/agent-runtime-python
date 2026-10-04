"""Reject corrupt durable states even when callers bypass the application."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "assignment",
    [
        "state = 'SUCCEEDED', outcome = 'null'::jsonb",
        "state = 'FAILED_PERMANENT', outcome = '[]'::jsonb",
        "state = 'RUNNING'",
    ],
)
async def test_database_rejects_invalid_execution_state(rig, assignment):
    eid = await rig.create()
    with pytest.raises(IntegrityError):
        async with rig.store.sessions.begin() as session:
            await session.execute(
                text(f"UPDATE executions SET {assignment} WHERE id = :id"), {"id": eid}
            )
    assert (await rig.store.get(eid)).state == "CREATED"


async def test_database_rejects_incomplete_action_and_approval(rig):
    eid = await rig.create("notify customer demo")
    await rig.runtime.advance("w", eid)
    action = (await rig.store.get(eid)).action
    for query in [
        "UPDATE tool_calls SET status = 'SUCCEEDED', outcome = 'null'::jsonb WHERE id = :id",
        "UPDATE tool_calls SET attempt = -1 WHERE id = :id",
        "UPDATE approvals SET decision = true WHERE call_id = :id",
    ]:
        with pytest.raises(IntegrityError):
            async with rig.store.sessions.begin() as session:
                await session.execute(text(query), {"id": action.id})
    assert await rig.effects() == 0
