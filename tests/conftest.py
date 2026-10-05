import os
import subprocess
import sys
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from app.config import Settings
from app.db import connect
from app.domain import CreateExecution
from app.observability import Metrics
from app.providers import FakeProvider
from app.runtime import Runtime
from app.sandbox import ALL_SANDBOX_TOOLS, Effect, SandboxBase, create_sandbox
from app.store import Store
from app.tools import HttpTools, Registry


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        if os.environ.get("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("TEST_DATABASE_URL is required by this verification gate")
        pytest.skip(
            "PostgreSQL not configured; set TEST_DATABASE_URL to a dedicated *_test database"
        )
    from sqlalchemy.engine import make_url

    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("Tests truncate data: database name must end with _test")
    env = {**os.environ, "RUNTIME_DATABASE_URL": url}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
    return url


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        api_key="a" * 40,
        approval_key="b" * 40,
        webhook_secret="c" * 40,
        tool_api_token="d" * 40,
        allowed_tools=ALL_SANDBOX_TOOLS,
        allow_local_sandbox=True,
        retry_base_seconds=0,
        requests_per_minute=100000,
        tenant_admissions_per_minute=100000,
        reconciliation_backoff_seconds=0,
    )


@dataclass
class Rig:
    runtime: Runtime
    client: httpx.AsyncClient
    settings: Settings

    @property
    def store(self):
        return self.runtime.store

    async def create(self, prompt="lookup customer demo", **kwargs) -> UUID:
        key = uuid4().hex
        execution_id, _ = await self.store.create(
            CreateExecution(business_key=key, prompt=prompt, **kwargs), key, uuid4()
        )
        return execution_id

    async def effects(self) -> int:
        async with self.store.sessions() as session:
            return int(await session.scalar(select(func.count()).select_from(Effect)))

    async def expire(self, execution_id) -> None:
        async with self.store.sessions.begin() as session:
            await session.execute(
                text(
                    "UPDATE executions SET lease_expires_at = clock_timestamp() - interval '1 second' WHERE id = :id"
                ),
                {"id": execution_id},
            )


@pytest_asyncio.fixture
async def rig(database_url, settings):
    settings.database_url = database_url
    engine, sessions = connect(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(SandboxBase.metadata.create_all)
        await connection.execute(
            text(
                "TRUNCATE execution_events, reconciliations, approvals, tool_calls, executions, webhook_receipts, sandbox_external_effects RESTART IDENTITY CASCADE"
            )
        )
    sandbox = create_sandbox(settings, sessions)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sandbox), base_url="http://sandbox:8001"
    ) as client:
        runtime = Runtime(
            Store(sessions, settings),
            FakeProvider(),
            Registry(settings.allowed_tools),
            HttpTools(client, settings),
            Metrics(),
        )
        yield Rig(runtime, client, settings)
    await engine.dispose()


class ScriptedProvider:
    name = "scripted-contract-fixture"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def decide(self, *args):
        from app.domain import ModelTurn

        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return ModelTurn.model_validate(response)


def turn(decision, input_tokens=0, output_tokens=0):
    return {
        "decision": decision,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
