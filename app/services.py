from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.config import Settings
from app.db import connect
from app.observability import Metrics
from app.providers import FakeProvider, ModelProvider, OpenAIProvider
from app.runtime import Runtime
from app.store import Store
from app.tools import HttpTools, Registry


@asynccontextmanager
async def services(settings: Settings) -> AsyncIterator[Runtime]:
    engine, sessions = connect(settings.database_url)
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        ) as client:
            provider: ModelProvider = (
                OpenAIProvider(client, settings)
                if settings.model_provider == "openai"
                else FakeProvider()
            )
            yield Runtime(
                Store(sessions, settings),
                provider,
                Registry(settings.allowed_tools, settings.tool_timeout),
                HttpTools(client, settings),
                Metrics(),
            )
    finally:
        await engine.dispose()
