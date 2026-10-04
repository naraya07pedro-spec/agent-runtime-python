import asyncio
import os
import socket
import sys

import httpx
import pytest

from app.domain import ApprovalDecision, State
from app.store import Store

pytestmark = [pytest.mark.integration, pytest.mark.failure]


async def test_real_process_exit_after_external_success(rig, database_url):
    eid = await rig.create("notify customer demo")
    await rig.runtime.advance("approval-requester", eid)
    action = (await rig.store.get(eid)).action
    await rig.store.decide_approval(
        action.id, ApprovalDecision(fingerprint=action.fingerprint, approve=True), "human"
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        "RUNTIME_DATABASE_URL": database_url,
        "RUNTIME_ALLOW_LOCAL_SANDBOX": "true",
        "RUNTIME_TOOL_BASE_URL": f"http://127.0.0.1:{port}",
        "RUNTIME_TOOL_API_TOKEN": "d" * 40,
        "RUNTIME_ALLOWED_TOOLS": '["send_notification"]',
        "RUNTIME_MODEL_PROVIDER": "fake",
    }
    server = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "app.sandbox:create_sandbox",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-access-log",
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with httpx.AsyncClient() as client:
            async with asyncio.timeout(15):
                while True:
                    if server.returncode is not None:
                        _, stderr = await server.communicate()
                        pytest.fail("sandbox process failed: " + stderr.decode())
                    try:
                        if (await client.get(f"http://127.0.0.1:{port}/health")).status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    await asyncio.sleep(0.05)
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tests.failure_injection.crash_worker",
            str(eid),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        async with asyncio.timeout(15):
            stdout, stderr = await worker.communicate()
        assert worker.returncode == 73, (stdout.decode(), stderr.decode())
        assert await rig.effects() == 1
        assert (await rig.store.get(eid)).state == State.TOOL_EXECUTING
        await rig.expire(eid)
        rig.runtime.store = Store(rig.store.sessions, rig.settings)
        await rig.store.recover()
        await rig.runtime.reconcile(eid, "restarted-reconciler")
        assert (await rig.store.get(eid)).state == State.CREATED
        assert await rig.effects() == 1
    finally:
        if server.returncode is None:
            server.terminate()
        await server.communicate()


async def test_real_process_exit_after_claim_can_be_reclaimed(rig, database_url):
    from sqlalchemy import select

    from app.domain import Fault, Lease
    from app.tables import Execution

    eid = await rig.create()
    worker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.failure_injection.crash_worker",
        str(eid),
        "after_claim",
        env={**os.environ, "RUNTIME_DATABASE_URL": database_url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(15):
            stdout, stderr = await worker.communicate()
        assert worker.returncode == 74, (stdout.decode(), stderr.decode())
    finally:
        if worker.returncode is None:
            worker.kill()
            await worker.communicate()
    async with rig.store.sessions() as session:
        row = await session.scalar(select(Execution).where(Execution.id == eid))
        old_lease = Lease(row.id, row.lease_token, row.lease_owner, row.correlation_id)
    assert (await rig.store.get(eid)).state == State.RUNNING
    await rig.expire(eid)
    assert await rig.store.recover() == 1
    fresh = await rig.store.claim("replacement", eid)
    assert fresh.lease.token != old_lease.token
    with pytest.raises(Fault, match="stale_lease"):
        await rig.store.finish(old_lease, "late result")
    await rig.store.finish(fresh.lease, "recovered")
    assert (await rig.store.get(eid)).state == State.SUCCEEDED
    assert await rig.effects() == 0
