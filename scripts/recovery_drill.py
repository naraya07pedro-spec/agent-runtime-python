"""Disposable PostgreSQL-container SIGKILL, rollback, reconnect and dump/restore drill.

Opt-in and *_test guards are mandatory. Dumps are temporary and never uploaded.
This is a controlled CI drill, not a production RPO/RTO guarantee.
"""

import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db import connect
from app.domain import CreateExecution, Fault
from app.sandbox import Effect, create_sandbox
from app.store import Store
from app.tables import Execution, ToolCall
from app.tools import HttpTools, Registry

TABLES = (
    "executions",
    "tool_calls",
    "approvals",
    "execution_events",
    "webhook_receipts",
    "reconciliations",
    "sandbox_external_effects",
)


async def run_command(*arguments, data=None):
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output, _ = await asyncio.wait_for(process.communicate(data), timeout=30)
    if process.returncode:
        raise RuntimeError("isolated PostgreSQL utility failed; credentials/output are suppressed")
    return output


async def docker(container, *arguments, data=None):
    argv = ["docker", "exec"]
    if data is not None:
        argv.append("-i")
    return await run_command(*argv, container, *arguments, data=data)


async def signatures(sessions):
    async with sessions() as session:
        return {
            table: list(
                (
                    await session.execute(
                        text(
                            "SELECT count(*),md5(string_agg(row_to_json(t)::text,'|' ORDER BY row_to_json(t)::text)) FROM "
                            + table
                            + " t"
                        )
                    )
                ).one()
            )
            for table in TABLES
        }


async def drill():
    url = os.environ.get("TEST_DATABASE_URL", "")
    container = os.environ.get("POSTGRES_CONTAINER_ID", "")
    parsed = make_url(url)
    if (
        os.environ.get("RUNTIME_RECOVERY_DRILL") != "1"
        or not re.fullmatch(r"[a-f0-9]{12,64}", container)
        or not (parsed.database or "").endswith("_test")
    ):
        raise SystemExit(
            "Recovery drill requires explicit opt-in, a PostgreSQL container ID and a disposable *_test DB"
        )
    image = (
        (await run_command("docker", "inspect", "--format", "{{.Config.Image}}", container))
        .decode()
        .strip()
    )
    if image != "postgres:17":
        raise SystemExit("Recovery drill only targets the isolated postgres:17 service")
    settings = Settings(
        _env_file=None,
        database_url=url,
        tool_api_token="synthetic-drill-token-0000000000000000",
        allowed_tools=frozenset({"upsert_ticket"}),
        allow_local_sandbox=True,
        tenant_admissions_per_minute=100000,
    )
    engine, sessions = connect(url)
    store = Store(sessions, settings)
    report = {
        "label": "SYNTHETIC DISPOSABLE DATABASE RECOVERY DRILL",
        "source_sha": os.environ.get("EVIDENCE_SOURCE_SHA"),
        "provider_is_sandbox": True,
    }
    user, database = parsed.username, parsed.database
    if user != "runtime" or database != "runtime_test":
        raise SystemExit("This drill targets only the documented synthetic CI database")
    restore_name = "runtime_restore_test"
    restored_engine = None
    try:
        key = "restore-" + uuid4().hex
        eid, _ = await store.create(
            CreateExecution(business_key=key, prompt="synthetic snapshot write"), key, uuid4()
        )
        work = await store.claim("snapshot-owner", eid)
        spec = Registry(settings.allowed_tools).get("upsert_ticket")
        cid = await store.propose(
            work.lease,
            spec,
            {"customer_id": "synthetic-drill", "summary": "synthetic restore effect"},
        )
        call = await store.dispatch(work.lease, cid, spec)
        sandbox = create_sandbox(settings, sessions)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=sandbox), base_url="http://sandbox:8001"
        ) as client:
            tools = HttpTools(client, settings)
            result = await tools.execute(call)
            before = await signatures(sessions)
            started = time.perf_counter()
            with tempfile.TemporaryDirectory(prefix="runtime-restore-drill-") as directory:
                dump = Path(directory) / "snapshot.dump"
                await run_command(
                    "docker",
                    "cp",
                    "scripts/database_backup.sh",
                    container + ":/tmp/runtime-backup.sh",
                )
                await run_command(
                    "docker",
                    "cp",
                    "scripts/database_restore.sh",
                    container + ":/tmp/runtime-restore.sh",
                )
                await docker(
                    container,
                    "env",
                    "PGDATABASE=" + database,
                    "PGUSER=" + user,
                    "bash",
                    "/tmp/runtime-backup.sh",
                    "/tmp/runtime-snapshot.dump",
                )
                await run_command(
                    "docker", "cp", container + ":/tmp/runtime-snapshot.dump", str(dump)
                )
                await asyncio.to_thread(dump.chmod, 0o600)
                report["backup_seconds"] = round(time.perf_counter() - started, 4)
                report["backup_bytes"] = dump.stat().st_size
                # A transaction is flushed but never committed before the real server SIGKILL.
                async with sessions() as victim:
                    await victim.begin()
                    row = await victim.get(Execution, eid)
                    original_prompt = row.prompt
                    row.prompt = "synthetic uncommitted update"
                    await victim.flush()
                    await run_command("docker", "kill", "--signal", "KILL", container)
                    try:
                        await victim.commit()
                    except SQLAlchemyError:
                        report["interrupted_transaction_rejected"] = True
                        await victim.rollback()
                    else:
                        raise AssertionError("transaction survived a killed database unexpectedly")
                try:
                    await store.get(eid)
                except (SQLAlchemyError, ConnectionError, TimeoutError):
                    report["outage_rejected"] = True
                else:
                    raise AssertionError("DB outage was reported as readable state")
                restart_started = time.perf_counter()
                await run_command("docker", "start", container)
                for _ in range(20):
                    try:
                        view = await store.get(eid)
                        break
                    except (SQLAlchemyError, ConnectionError, TimeoutError):
                        await asyncio.sleep(0.25)
                else:
                    raise AssertionError(
                        "database did not recover within bounded reconnect attempts"
                    )
                report["restart_reconnect_seconds"] = round(
                    time.perf_counter() - restart_started, 4
                )
                assert view.state == "TOOL_EXECUTING"
                async with sessions() as session:
                    assert (await session.get(Execution, eid)).prompt == original_prompt
                report["uncommitted_update_rolled_back"] = True
                restore_started = time.perf_counter()
                await docker(container, "createdb", "-U", user, restore_name)
                await docker(
                    container,
                    "env",
                    "PGDATABASE=" + restore_name,
                    "PGUSER=" + user,
                    "RUNTIME_RESTORE_ACK=isolated-empty-database",
                    "bash",
                    "/tmp/runtime-restore.sh",
                    "/tmp/runtime-snapshot.dump",
                )
                restored_url = parsed.set(database=restore_name).render_as_string(
                    hide_password=False
                )
                restored_engine, restored_sessions = connect(restored_url)
                assert await signatures(restored_sessions) == before
                report["snapshot_row_integrity"] = True
                report["table_rows"] = {table: value[0] for table, value in before.items()}
                settings.recovery_read_only = True
                restored = Store(restored_sessions, settings)
                try:
                    await restored.claim("unsafe-restored-worker", eid)
                except Fault as exc:
                    assert exc.code == "recovery_read_only"
                    report["restore_claim_blocked"] = True
                else:
                    raise AssertionError("restored worker claim was not blocked")
                async with restored_sessions.begin() as session:
                    await session.execute(
                        text(
                            "UPDATE executions SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"
                        ),
                        {"id": eid},
                    )
                assert await restored.recover() >= 1
                lease, restored_call = await restored.claim_reconciliation(eid, "restore-verifier")
                # The provider lookup is served from the ORIGINAL independently committed DB,
                # never the restored provider-table copy. No POST is made after restore.
                evidence = await tools.lookup_for(restored_call)
                assert evidence.status == "found" and evidence.result == result
                await restored.complete_tool(
                    lease, restored_call.call_id, evidence.result, reconciliation=True
                )
                try:
                    await restored.complete_tool(work.lease, cid, result)
                except Fault as exc:
                    assert exc.code == "stale_lease"
                    report["stale_pre_restore_worker_rejected"] = True
                else:
                    raise AssertionError("pre-restore ownership was reused")
                async with sessions() as session:
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(Effect)
                            .where(Effect.operation_key == call.operation_key)
                        )
                        == 1
                    )
                async with restored_sessions() as session:
                    saved = await session.get(ToolCall, cid)
                    assert saved.attempt == 1 and saved.operation_key == call.operation_key
                report["external_effects_for_seed"] = 1
                report["restore_verification_seconds"] = round(
                    time.perf_counter() - restore_started, 4
                )
                report["provider_replay_after_restore"] = False
                report["rpo_rto_guarantee"] = False
        report["status"] = "passed"
        await asyncio.to_thread(Path("artifacts").mkdir, exist_ok=True)
        await asyncio.to_thread(
            Path("artifacts/recovery-drill.json").write_text, json.dumps(report, indent=2) + "\n"
        )
        print(json.dumps(report, indent=2))
    finally:
        if restored_engine:
            await restored_engine.dispose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(drill())
