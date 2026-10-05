import asyncio
import os
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db import connect

pytestmark = pytest.mark.integration


async def migrate(url, *arguments):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        *arguments,
        env={**os.environ, "RUNTIME_DATABASE_URL": url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output = await process.communicate()
    return process.returncode, output


async def test_populated_v1_upgrade_preserves_uncertain_operation_and_refuses_lossy_downgrade(
    database_url,
):
    engine, _ = connect(database_url)
    eid, cid = uuid4(), uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE executions, webhook_receipts RESTART IDENTITY CASCADE")
            )
        assert (await migrate(database_url, "downgrade", "0001"))[0] == 0
        async with engine.begin() as connection:
            await connection.execute(
                text("""
                INSERT INTO executions (id,business_key,idempotency_key,request_digest,prompt,state,
                    correlation_id,max_steps,max_tokens,steps,model_calls,tokens_used,retry_count,deadline_at)
                VALUES (:id,'synthetic-v1','synthetic-v1',:digest,'synthetic migrated prompt',
                    'RECONCILIATION_REQUIRED',:correlation,4,4096,1,1,0,0,now()+interval '1 hour')
            """),
                {"id": eid, "digest": "a" * 64, "correlation": uuid4()},
            )
            await connection.execute(
                text("""
                INSERT INTO tool_calls (id,execution_id,ordinal,tool,fingerprint,operation_key,
                    arguments,side_effect,approval_required,status,attempt)
                VALUES (:id,:execution,1,'upsert_ticket',:digest,:operation,'{}'::jsonb,
                    'idempotent_write',false,'DISPATCHED',1)
            """),
                {"id": cid, "execution": eid, "digest": "b" * 64, "operation": "c" * 64},
            )
        assert (await migrate(database_url, "upgrade", "head"))[0] == 0
        # Fresh connection after DDL; don't depend on cached driver statement plans.
        await engine.dispose()
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT tenant_id FROM executions WHERE id=:id"), {"id": eid}
                )
                == "legacy"
            )
            assert (
                await connection.scalar(
                    text("SELECT operation_key FROM tool_calls WHERE id=:id"), {"id": cid}
                )
                == "c" * 64
            )
            assert (
                await connection.scalar(
                    text("SELECT status FROM reconciliations WHERE call_id=:id"), {"id": cid}
                )
                == "AMBIGUOUS"
            )
        rejected, output = await migrate(database_url, "downgrade", "0001")
        assert rejected != 0
        assert b"unsafe v2 downgrade" in output[1]
        assert (await migrate(database_url, "check"))[0] == 0
    finally:
        await migrate(database_url, "upgrade", "head")
        await engine.dispose()
