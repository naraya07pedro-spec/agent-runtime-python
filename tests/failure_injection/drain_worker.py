"""Real worker process, synthetic sandbox effect, controlled post-commit pause."""

import asyncio
from contextlib import asynccontextmanager

import httpx

from app import worker
from app.db import connect
from app.observability import Metrics
from app.providers import FakeProvider
from app.runtime import Runtime
from app.sandbox import create_sandbox
from app.store import Store
from app.tools import HttpTools, Registry


@asynccontextmanager
async def controlled_services(settings):
    engine, sessions = connect(settings.database_url)
    try:
        sandbox = create_sandbox(settings, sessions)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=sandbox), base_url="http://sandbox:8001"
        ) as client:
            tools = HttpTools(client, settings)
            original = tools.execute

            async def execute(call):
                result = await original(call)
                print("SYNTHETIC_EFFECT_COMMITTED", flush=True)
                await asyncio.sleep(0.5)
                return result

            tools.execute = execute
            yield Runtime(
                Store(sessions, settings, tenant_id=None),
                FakeProvider(),
                Registry(settings.allowed_tools),
                tools,
                Metrics(),
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    worker.services = controlled_services
    asyncio.run(worker.run(False, None, False))
