import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import connect
from app.domain import Fault
from app.tables import Execution

pytestmark = pytest.mark.failure


async def terminate(sessions, pid):
    async with sessions.begin() as killer:
        assert await killer.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})


async def test_connection_killed_mid_transaction_rolls_back_and_pool_recovers(rig, database_url):
    eid = await rig.create()
    engine, killers = connect(database_url)
    try:
        async with rig.store.sessions() as victim:
            await victim.begin()
            pid = await victim.scalar(text("SELECT pg_backend_pid()"))
            row = await victim.get(Execution, eid)
            row.prompt = "synthetic uncommitted change"
            await victim.flush()
            await terminate(killers, pid)
            with pytest.raises(SQLAlchemyError):
                await victim.commit()
            await victim.rollback()
        async with rig.store.sessions() as session:
            row = await session.get(Execution, eid)
            assert row.prompt == "lookup customer demo"
        await rig.runtime.advance("reconnected", eid)
        assert (await rig.store.get(eid)).state == "CREATED"
    finally:
        await engine.dispose()


async def test_idle_connection_death_is_replaced_and_stale_worker_still_rejected(rig, database_url):
    eid = await rig.create()
    work = await rig.store.claim("old", eid)
    async with rig.store.sessions() as idle:
        pid = await idle.scalar(text("SELECT pg_backend_pid()"))
    engine, killers = connect(database_url)
    try:
        await terminate(killers, pid)
        assert (await rig.store.get(eid)).state == "RUNNING"
        await rig.expire(eid)
        assert await rig.store.recover() == 1
        replacement = await rig.store.claim("replacement", eid)
        assert replacement.lease.token != work.lease.token
        with pytest.raises(Fault, match="stale_lease"):
            await rig.store.finish(work.lease, "unsafe late completion")
        await rig.store.finish(replacement.lease, "synthetic recovered")
        assert (await rig.store.get(eid)).state == "SUCCEEDED"
    finally:
        await engine.dispose()


async def test_restore_maintenance_blocks_claims_and_admission_but_allows_safe_lookup(rig):
    from tests.failure_injection.test_reconciliation_lifecycle import ambiguous

    eid = await ambiguous(rig)
    rig.settings.recovery_read_only = True
    with pytest.raises(Fault, match="recovery_read_only"):
        await rig.runtime.advance("restored", eid)
    with pytest.raises(Fault, match="recovery_read_only"):
        await rig.create()
    await rig.runtime.reconcile(eid, "verify-restored")
    assert (await rig.store.get(eid)).reconciliation.status == "RESOLVED"
    assert await rig.effects() == 1
