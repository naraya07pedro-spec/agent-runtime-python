# Operator runbook

## Start and inspect

```bash
python3 scripts/bootstrap.py
docker compose up --build -d --wait
python3 scripts/smoke.py
docker compose ps
docker compose logs --tail 100 api worker
```

Bootstrap creates new local credentials only if `.env` is absent; it does not inspect or
modify an existing file. Existing installations must provision the new operator key outside
chat. The demo sends no real notifications/money. Migration is a one-shot prerequisite;
only API port 8000 is published on loopback. Secrets are injected into authorized clients;
do not print tokens/configuration or put them in issue reports.

Preserve business key, Idempotency-Key and body across admission retries. Inspect execution
and paginated events using the tenant API credential. The worker polls automatically.
Review exact args, provider binding, fingerprint and expiration before the separate approval
credential decides. An expired decision cannot be renewed or borrowed by a changed action.

## Triage

| Symptom | Inspect | Safe action |
|---|---|---|
| Readiness 503 / DB disconnect | Classified code, service health, schema 0002, maintenance mode | Restore connectivity/configuration; keep auth enabled |
| Cross-tenant/nonexistent ID 404 | Credential's bound tenant and role | Correct authorized identity; do not switch headers to forge a tenant |
| Admission 429 | Tenant capacity/quota and due/manual-review backlog | Respect Retry-After, drain or investigate; identical duplicate identity remains usable outside maintenance |
| CREATED/RETRY_PENDING delayed | Worker availability, due time, deadline | Restart healthy worker; preserve operation identity |
| Expired RUNNING/TOOL_EXECUTING | Lease and dispatch event | Recovery classifies read/model retry versus uncertain write |
| WAITING_FOR_APPROVAL | Exact action/target/expiry | Authorized approve/deny; never treat model text as permission |
| AMBIGUOUS/RECONCILING | Lookup attempt/budget/deadline and provider binding | Allow bounded read-only recovery; no write reset |
| MANUAL_REVIEW | Independent provider history and trust/retention limits | One explicit operator lookup, or audited abandonment with unknown effect |
| Poison-job quarantine | Error code and retained history | Diagnose defect with synthetic reproduction; no automatic replay |
| FAILED_PERMANENT/ABANDONED | Prior successful actions and effect_unknown flag | Preserve evidence; failure does not undo external effects |
| Repeated 409 | Conflict/replay/stale owner/approval state | Fix identity discipline; do not manufacture a new key to bypass safety |

`docker compose exec worker python -m app.worker --recover-only` repairs stale leases without
new model/tool dispatch. Normal workers also visit due reconciliation. API `/reconcile` is
budgeted; the tenant's separate operator credential can POST `/executions/{id}/operator`
with lookup/verification or abandon/operator_decision. See [exact semantics](reconciliation.md).
No endpoint accepts an injected success result, force replay or uncertain-write cancellation.

A replay investigation means inspect history and perform read-only lookup. Re-running a new
business key is a new external action, not recovery. If a GitHub response times out, locate
its marker in the bound repository and trusted actor history; edited/missing/duplicate
results remain unresolved. A changed repository/actor invalidates pending authority/lookup.

## Stuck worker

Use this when executions stay in `CREATED`, `RETRY_PENDING`, `RUNNING` or `TOOL_EXECUTING`
long after their due time or lease expiry. Run every command from the repository root. Do not
paste tokens into tickets; `$API_KEY` below is the tenant API credential from your own `.env`.

1. Is the worker process alive and healthy?

   ```bash
   docker compose ps worker
   docker compose logs --tail 100 worker
   ```

   Look for a restart loop, an exit code, or repeated `worker_error` log lines. An
   `error_class` of `database_unavailable` points at the database, not at the worker: fix
   connectivity first (see Triage, first row).

2. Is it claiming turns? The worker metrics port is `RUNTIME_WORKER_METRICS_PORT` (9101 in
   `docker-compose.yml`) and listens on 127.0.0.1 inside the container only. Read it twice,
   a few seconds apart:

   ```bash
   docker compose exec worker python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9101/').read().decode())" | grep runtime_advances_total
   ```

   A counter that does not move while `CREATED` or `RETRY_PENDING` rows exist means the loop is
   not claiming work. A counter that moves means the worker is fine and the problem is a
   single execution (step 4).

3. How many executions are in each state for the tenant?

   ```bash
   curl -s -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8000/metrics | grep runtime_executions
   ```

   Growth in `CREATED`/`RETRY_PENDING` with no movement confirms a stall. A pile of
   `RUNNING`/`TOOL_EXECUTING` means leases are held by a dead or hung owner.

4. Inspect one affected execution and its events before touching anything:

   ```bash
   curl -s -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8000/executions/$EXECUTION_ID
   curl -s -H "Authorization: Bearer $API_KEY" "http://127.0.0.1:8000/executions/$EXECUTION_ID/events?limit=50"
   ```

   Check `state`, `next_attempt_at` and the last event kind. If the last event is a dispatch
   intent with no result, the outcome of that external call is unknown: do not retry it by hand.

5. Repair stale leases without dispatching anything new:

   ```bash
   docker compose exec worker python -m app.worker --recover-only
   ```

   This runs `recover()` once and exits. For an expired `RUNNING`/`TOOL_EXECUTING` row it
   schedules a retry if the pending call was a read or model call and the retry budget and
   deadline allow, fails it with `recovery_budget_exhausted` otherwise, and moves an
   uncertain write to `RECONCILIATION_REQUIRED` with `lease_expired_after_dispatch`. It never
   replays a write. Rows whose lease has not expired are left alone, so a healthy slow turn
   is not stolen.

6. Restart the worker only if step 1 or 2 showed it is not claiming work:

   ```bash
   docker compose restart worker
   ```

   Compose gives it 35 seconds to drain (`RUNTIME_WORKER_DRAIN_SECONDS` defaults to 30); work
   cut off after that is picked up through lease recovery, not lost. Then repeat step 2.

7. Stop and escalate if: executions sit in `RECONCILIATION_REQUIRED` or `MANUAL_REVIEW` (use
   the operator path in the Triage table, not a restart), `RUNTIME_RECOVERY_READ_ONLY=true`
   is set (the worker recovers and reconciles but does not advance, by design), or the same
   execution returns to a stalled state after recovery. Keep the execution ID, event history
   and `worker_error` lines as evidence.

## Interruption and recovery

After database unavailability, reconnect and inspect durable intent. Do not infer success
from a log or reset the row. Failed transactions roll back; the worker does not transparently
retry a write whose external outcome is unknown. Stale connections are pre-pinged and stale
ownership remains fenced. Run the isolated [connection tests](../tests/failure_injection/test_database_interruptions.py)
and [SIGKILL/restore drill](backup-restore.md) only against synthetic disposable databases.

Before restore: stop admission and all workers, fence old deployments, and configure
RUNTIME_RECOVERY_READ_ONLY=true in EVERY new API/worker process. A shell export does not
change running Compose containers. Keep read-only mode through schema/row/tenant validation
and review of provider effects since the snapshot. Positive evidence can resolve persisted
uncertain dispatches; lost post-snapshot intent cannot be automatically reconstructed.
Only resume after a recorded operator review. Promotion/destructive operations are manual.

## Shutdown and schema evolution

SIGTERM/SIGINT stop new claims. The active cycle drains for RUNTIME_WORKER_DRAIN_SECONDS
(default 30), then cancellation leaves durable intent for lease recovery. Compose grants
35 seconds. SIGKILL skips cleanup; leases/dispatch state provide recovery evidence. A local
stop cannot cancel a request already accepted externally. Real SIGTERM and abrupt process
exit tests exercise specific boundaries, not every possible instruction-level fault.

Drain old workers before 0002 upgrade; mixed-version rolling deployment is not certified.
0001 is frozen. 0002 preserves legacy operation identities and backfills uncertainty rows.
The downgrade refuses tenant/reconciliation/provider-binding evidence loss. CI's base
roundtrip uses an empty disposable database only. Never delete evidence to force downgrade.
`docker compose down` preserves the volume; `down -v` destroys demo data and is CI-only.

## Security and incident handoff

Rotate keys with [configured overlap/cutover](tenant-model.md), distribute the change to
all processes, then revoke the old key. API/approval/operator roles remain separate.
Keep logs, execution/event IDs and independent provider request references; exclude raw
payloads, client identifiers and secrets from public incidents. Restrict provider credentials,
monitor 429/503/manual-review age and compare [process/DB metrics](observability.md) correctly.
For suspected provider/worker/DB compromise, stop dispatch and preserve private evidence;
application fencing does not establish trustworthy state under a malicious administrator.

Reproduce using a dedicated *_test PostgreSQL database: `REQUIRE_POSTGRES_TESTS=1 uv run pytest`.
Tests truncate runtime/sandbox tables. Neither the suffix guard nor this runbook authorizes
using customer data. No production uptime, RPO/RTO or incident-response certification is claimed.
