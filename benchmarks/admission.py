"""Measure API admission with real PostgreSQL and in-process HTTP transport."""

import argparse
import asyncio
import cProfile
import io
import json
import os
import platform
import pstats
import secrets
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.api import create_app
from app.config import Settings
from app.services import services


def percentile(values, fraction):
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    left = int(index)
    right = min(left + 1, len(ordered) - 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (index - left)


async def run(count, concurrency, warmup):
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url or not (make_url(database_url).database or "").endswith("_test"):
        raise SystemExit("benchmark requires TEST_DATABASE_URL pointing to a dedicated *_test DB")
    config = Settings(
        _env_file=None,
        database_url=database_url,
        api_key=secrets.token_urlsafe(32),
        approval_key=secrets.token_urlsafe(32),
        requests_per_minute=100000,
    )
    async with services(config) as runtime:
        app = create_app(config, runtime)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://benchmark"
        ) as client:
            gate = asyncio.Semaphore(concurrency)
            durations = []
            failures = Counter()

            async def request(record=True):
                async with gate:
                    identity = "bench-" + uuid4().hex
                    started = time.perf_counter()
                    response = await client.post(
                        "/executions",
                        json={"business_key": identity, "prompt": "lookup customer demo"},
                        headers={
                            "Authorization": "Bearer " + config.api_key.get_secret_value(),
                            "Idempotency-Key": identity,
                        },
                    )
                    if record:
                        durations.append((time.perf_counter() - started) * 1000)
                        if response.status_code != 201:
                            failures[str(response.status_code)] += 1
                    elif response.status_code != 201:
                        raise RuntimeError("benchmark warmup failed")

            profiler = cProfile.Profile()
            profiler.enable()
            for _ in range(warmup):
                await request(False)
            profiler.disable()
            stream = io.StringIO()
            pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(30)
            await asyncio.to_thread(Path("artifacts").mkdir, exist_ok=True)
            await asyncio.to_thread(
                Path("artifacts/admission-profile.txt").write_text, stream.getvalue()
            )
            started = time.perf_counter()
            await asyncio.gather(*[request() for _ in range(count)])
            elapsed = time.perf_counter() - started
            async with runtime.store.sessions() as session:
                version = await session.scalar(text("SELECT version()"))
                plan = (
                    (
                        await session.execute(
                            text(
                                "EXPLAIN (ANALYZE, BUFFERS) SELECT id FROM executions WHERE state='CREATED' AND lease_token IS NULL ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            report = {
                "timestamp": datetime.now(UTC).isoformat(),
                "commit": (
                    await asyncio.to_thread(
                        subprocess.check_output, ["git", "rev-parse", "HEAD"], text=True
                    )
                ).strip(),
                "ci_run_id": os.environ.get("GITHUB_RUN_ID"),
                "python": platform.python_version(),
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
                "database": version,
                "database_mode": "PostgreSQL, durable writes, default service settings",
                "transport": "in-process ASGI; excludes TCP/TLS, LLM and external tool calls",
                "scope": "POST /executions admission only",
                "request_count": count,
                "concurrency": concurrency,
                "warmup": warmup,
                "elapsed_seconds": elapsed,
                "p50_ms": percentile(durations, 0.50),
                "p95_ms": percentile(durations, 0.95),
                "p99_ms": percentile(durations, 0.99),
                "requests_per_second": count / elapsed,
                "errors": sum(failures.values()),
                "successes": count - sum(failures.values()),
                "failure_types": dict(failures),
                "claim_query_plan": list(plan),
                "production_capacity_claim": False,
            }
            await asyncio.to_thread(
                Path("artifacts/benchmark-results.json").write_text,
                json.dumps(report, indent=2) + "\n",
            )
            print(json.dumps(report))
            if failures:
                raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=20)
    args = parser.parse_args()
    if min(args.requests, args.concurrency, args.warmup) < 1:
        parser.error("all counts must be positive")
    asyncio.run(run(args.requests, args.concurrency, args.warmup))
