import asyncio
import json
import os
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import ValidationError
from sqlalchemy import func, select

from app.config import Settings
from app.db import connect
from app.domain import CreateExecution, Decision, ExternalFault, ModelTurn, Usage
from app.observability import Metrics
from app.runtime import Runtime
from app.sandbox import Effect, SandboxBase, create_sandbox
from app.store import Store
from app.tools import HttpTools, Registry


class FixtureProvider:
    name = "deterministic-eval-fixture"

    def __init__(self, decision):
        self.decision = decision

    async def decide(self, *args):
        try:
            return ModelTurn(
                decision=Decision.model_validate(self.decision),
                usage=Usage(input_tokens=0, output_tokens=0),
            )
        except ValidationError as exc:
            raise ExternalFault("model_response_invalid") from exc


def cases():
    return [
        json.loads(line)
        for line in Path(__file__).with_name("cases.jsonl").read_text().splitlines()
    ]


async def evaluate_case(store, tools, case):
    registry = Registry(frozenset(case["allowed"]))
    runtime = Runtime(store, FixtureProvider(case["decision"]), registry, tools, Metrics())
    identity = f"eval-{case['id']}-{uuid4().hex}"
    try:
        Decision.model_validate(case["decision"])
        schema_valid = True
    except ValidationError:
        schema_valid = False
    async with store.sessions() as session:
        before = await session.scalar(select(func.count()).select_from(Effect))
    eid, _ = await store.create(
        CreateExecution(business_key=identity, prompt=case["prompt"]), identity, uuid4()
    )
    await runtime.advance("eval-worker", eid)
    observed = await store.get(eid)
    async with store.sessions() as session:
        after = await session.scalar(select(func.count()).select_from(Effect))
    passed = (
        observed.state.value == case["state"]
        and observed.error_code == case["error"]
        and schema_valid == case["schema_valid"]
        and after == before
    )
    return {
        "id": case["id"],
        "passed": passed,
        "observed_state": observed.state.value,
        "observed_error": observed.error_code,
        "schema_valid": schema_valid,
        "expected_schema_valid": case["schema_valid"],
        "external_effects": after - before,
        "tool_requested": case["decision"].get("tool"),
        "approval_wait_observed": observed.state.value == "WAITING_FOR_APPROVAL",
    }


async def main():
    config = Settings(
        tool_base_url="http://sandbox:8001",
        allow_local_sandbox=True,
        tool_api_token=secrets.token_urlsafe(32),
    )
    engine, sessions = connect(config.database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SandboxBase.metadata.create_all)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_sandbox(config, sessions))
        ) as client:
            store = Store(sessions, config)
            tools = HttpTools(client, config)
            results = [await evaluate_case(store, tools, case) for case in cases()]
        report = {
            "kind": "deterministic_contract_evals",
            "model_quality_evaluated": False,
            "timestamp": datetime.now(UTC).isoformat(),
            "commit": (
                await asyncio.to_thread(
                    subprocess.check_output, ["git", "rev-parse", "HEAD"], text=True
                )
            ).strip(),
            "ci_run_id": os.environ.get("GITHUB_RUN_ID"),
            "cases": results,
            "passed": sum(r["passed"] for r in results),
            "total": len(results),
        }
        await asyncio.to_thread(Path("artifacts").mkdir, exist_ok=True)
        await asyncio.to_thread(
            Path("artifacts/eval-results.json").write_text, json.dumps(report, indent=2) + "\n"
        )
        print(json.dumps(report))
        if report["passed"] != report["total"]:
            raise SystemExit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
