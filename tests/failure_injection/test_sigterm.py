import asyncio
import json
import os
import signal
import sys

import pytest

pytestmark = pytest.mark.failure


async def test_real_sigterm_drains_committed_effect_and_stops_before_another_claim(
    rig, database_url
):
    eid = await rig.create("create ticket demo")
    second = await rig.create("create ticket demo")
    env = {
        **os.environ,
        "RUNTIME_DATABASE_URL": database_url,
        "RUNTIME_API_KEY": "a" * 40,
        "RUNTIME_APPROVAL_KEY": "b" * 40,
        "RUNTIME_WEBHOOK_SECRET": "c" * 40,
        "RUNTIME_TOOL_API_TOKEN": "d" * 40,
        "RUNTIME_ALLOWED_TOOLS": json.dumps(sorted(rig.settings.allowed_tools)),
        "RUNTIME_ALLOW_LOCAL_SANDBOX": "true",
        "RUNTIME_WORKER_METRICS_PORT": "0",
        "RUNTIME_WORKER_DRAIN_SECONDS": "3",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.failure_injection.drain_worker",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        marker = await asyncio.wait_for(process.stdout.readline(), timeout=15)
        assert marker == b"SYNTHETIC_EFFECT_COMMITTED\n"
        process.send_signal(signal.SIGTERM)
        await asyncio.wait_for(process.communicate(), timeout=10)
        assert process.returncode == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    first = await rig.store.get(eid)
    assert first.state == "CREATED" and first.model_calls == 1
    assert (await rig.store.get(second)).model_calls == 0
    assert await rig.effects() == 1
