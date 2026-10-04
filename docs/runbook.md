# Operator runbook

## Start and inspect the sandbox

```bash
python3 scripts/bootstrap.py
docker compose up --build -d --wait
python3 scripts/smoke.py
docker compose ps
docker compose logs --tail 100 api worker
```

The Compose stack includes its own PostgreSQL server; no host PostgreSQL installation
is needed. Database port 5432 is not published. `migrate` is a one-shot prerequisite;
its successful exit is normal. API port 8000 binds only to loopback. The provider is
a sandbox: notifications/refunds create rows, not real messages or money movements.

For authenticated requests, read the generated `.env` into an operator process without
printing it or committing it. This Python snippet inspects readiness and metrics:

```python
from pathlib import Path
from urllib.request import Request, urlopen

values = dict(
    line.split("=", 1)
    for line in Path(".env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
for path in ["/ready", "/metrics"]:
    request = Request(
        "http://127.0.0.1:8000" + path,
        headers={"Authorization": "Bearer " + values["RUNTIME_API_KEY"]},
    )
    print(urlopen(request, timeout=10).read().decode())
```

`POST /executions` requires `Idempotency-Key` and a body such as
`{"business_key":"case-001","prompt":"lookup customer demo"}`. Preserve both keys
and the entire request on retries. The worker polls automatically; an operator can
also call `POST /executions/{id}/advance`. Read status and paginated `/events`.
The smoke script supplies a complete runnable admission and approval example.

## Triage by state

| Symptom | Inspect | Safe next action |
|---|---|---|
| Readiness 503 | Error code, DB health, migration revision, credentials | Repair configuration/DB; never disable auth to make readiness green |
| CREATED never advances | Worker logs; DB connection; deadline | Restart worker; normal claims are safe under duplicate workers |
| RUNNING / TOOL_EXECUTING with expired lease | DB lease expiry, dispatch event, provider logs | Run worker recovery; it classifies safe retry versus uncertain write |
| WAITING_FOR_APPROVAL | Action ID, exact args/fingerprint, expiry | Authorized human approves/denies with separate credential; expired decision cannot be renewed |
| RETRY_PENDING | `next_attempt_at`, error class, retry count | Wait until due; do not shorten Retry-After manually |
| RECONCILIATION_REQUIRED | Dispatch event and provider operation identity | Call `/reconcile`; only positive matching provider evidence completes the action |
| FAILED_PERMANENT | Classified error, prior successful tool events | Investigate; failure does not undo prior effects |
| Many 409 responses | Idempotency conflict, replay, stale lease, approval state | Preserve identity and inspect history; do not invent new keys to bypass safety |

Run recovery without dispatching new work:

```bash
docker compose exec worker python -m app.worker --recover-only
```

For reconciliation, `POST /executions/{id}/reconcile` uses the API key. Lookup is
read-only toward the provider. `provider_absent_no_replay` or unknown means leave the
operation unresolved. A paused old worker may still send later. No supported endpoint
forces replay, injects a synthetic success, or cancels an uncertain write. Escalate to
the provider/operator and preserve evidence; do not delete identity rows to unblock it.

If PostgreSQL was unavailable after a side effect, restore connectivity first. The
committed dispatch intent remains recovery evidence. Never report success from logs
alone; read the durable state and provider result. Backup restores can lose recent
identity/outcome records: isolate workers, reconcile externally, and review the restore
window before resuming dispatch. Backup/PITR restoration is not tested in this repo.

## Migrations and shutdown

```bash
docker compose run --rm migrate
# Stop containers while preserving the named database volume:
docker compose down
```

`docker compose down -v` intentionally destroys sandbox data; use only for disposable
demos/CI. Downgrade to `base` drops all runtime tables. CI tests that roundtrip only
on a dedicated empty/disposable database. Future deployed revisions should use additive
migrations, backfill validation, and rollout compatibility rather than editing revision
0001. Its final pre-release definition is frozen in this repository.

Abrupt worker shutdown is handled through leases, not guaranteed graceful drain. If a
process dies during a write, expect reconciliation. The API HTTP client and database
engine are closed on lifespan exit. Compose startup, actual TCP execution, and cleanup
are exercised by CI; multi-host rolling upgrades are not.

## Reproduce failures

Use a dedicated PostgreSQL database whose name ends in `_test`:

```bash
export TEST_DATABASE_URL=postgresql+asyncpg://runtime:runtime@localhost:5432/runtime_test
export RUNTIME_DATABASE_URL="$TEST_DATABASE_URL"
make test
make simulate
```

Tests truncate runtime and sandbox tables. The suffix guard prevents accidental use
of a normally named database but does not replace operator care. Tests must not run
against customer data. Fault hooks are test-injected; they are not exposed as an API.
