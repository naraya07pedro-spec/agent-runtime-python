# Architecture and data ownership

The runtime separates contracts (`domain.py`), transactional state (`store.py`),
execution coordination (`runtime.py`), provider boundaries, tenant identity, and HTTP ingress.
SQLAlchemy rows are never returned as HTTP contracts: `Store.get` constructs an
`ExecutionView` explicitly.

## Two independent commits

The runtime and external provider do not share a transaction. The sandbox can use
the same PostgreSQL server for convenience, but its HTTP handler uses an independent
session and commits before returning. The real process-death test reaches it over
TCP, kills the worker with `os._exit`, and inspects both persisted histories.

An advance owns a lease but never holds a row lock across external I/O:

| Transaction | What commits | Network after commit |
|---|---|---|
| Admission | Identity, fingerprint, CREATED event; optional webhook receipt | None |
| Claim | RUNNING, owner, token, expiry | None |
| Model start | Persistent model-call budget consumption | Model request |
| Model usage | Reported tokens; failure if budget exceeded | None |
| Proposal | Validated action identity; optional approval request | None |
| Dispatch | DISPATCHED action, TOOL_EXECUTING, audit event | Tool request |
| Completion | Result, provider ID, action SUCCEEDED, execution CREATED | None |
| Reconciliation | A fenced read-only claim; then a matching lookup result | Provider lookup only |

Returning CREATED after a tool result releases ownership. Another worker can
perform the next model turn. A final model answer transitions the execution to
SUCCEEDED. FAILED_PERMANENT refers to the execution's progress; earlier successful
tool results remain in the database and event history.

## Tables and indexes

| Table | Identity and constraints | Access path |
|---|---|---|
| executions | UUID PK; tenant-scoped unique business and idempotency keys; legal state, complete/active lease and terminal JSON-object checks | `(state,next_attempt_at)` ready queue; `(state,lease_expires_at)` recovery |
| tool_calls | UUID PK; execution FK; unique ordinal, action fingerprint, operation key; valid counters and successful outcome checks | Per-execution ordered history and pending action |
| approvals | One row per action; action FK; bound fingerprint, expiration and complete decision metadata | Direct action lookup |
| execution_events | Bigint sequence; execution and optional action FK | `(execution_id,id)` keyset pagination |
| webhook_receipts | Tenant + nonce digest PK; expiration | Expiry index for cleanup |
| reconciliations | One action FK; explicit status, attempt budget and deadline | `(status,next_attempt_at)` due lookup |

Event IDs are monotonic but not gap-free because rolled-back sequences consume
values. Pagination does not assume contiguity. Idempotency records and leases are
columns on executions; a separate table would add joins without a distinct lifecycle.
Reconciliation is an indexed execution state plus an action lifecycle row. Both are
locked through the execution owner; the ledger never becomes an independent write queue.

The sandbox's `sandbox_external_effects` table belongs to its own metadata and is
excluded from runtime migration drift checks. It is a test provider, not application
business storage. A real integration supplies its own durable provider identity and
lookup contract.

## Ownership and isolation

READ COMMITTED plus row locking is sufficient for the chosen operations. Admission
relies on database uniqueness and `INSERT ... ON CONFLICT DO NOTHING`; an application
pre-check alone would race. A tenant advisory lock serializes admission capacity/quota
checks across API processes; uniqueness remains the final identity constraint. Claims skip locked rows. Every completion reacquires the row,
checks the exact lease token and owner, and compares expiry with `clock_timestamp()`.
The system uses DB time for ownership, retry readiness, and approval expiry.

The 60-second default lease exceeds the combined 10-second model, 10-second tool,
and commit margin. Heartbeat extension is intentionally absent. Long-running tools
need a different protocol rather than silently stretching ownership.

## Dependencies and transport

FastAPI/Pydantic own HTTP schemas. SQLAlchemy async and asyncpg own PostgreSQL I/O;
Alembic owns the frozen schema migration. HTTPX owns outbound I/O with redirects
disabled, no environment proxy inheritance, bounded response bodies, and explicit
timeouts. HTTP concurrency and DB pool sizes are finite. There is no mandatory Redis,
Kafka, agent framework, or tracing backend.

## Tenant and provider authority

API and approval/operator credential bindings resolve a server-owned tenant. Store views,
claims and writes are scoped; child rows inherit identity through their parent. Global
workers carry the claimed tenant in their lease, verify it again during mutation, and
select that tenant's tool allowlist. There is no caller-controlled tenant or provider URL.
GitHub approval fingerprints also bind repository and trusted actor; changing configuration
cannot retarget an approved action. See [tenant model](tenant-model.md) and
[provider contract](provider-contract.md).

## Recovery and operations

A reconciliation attempt commits before a read-only lookup, consumes a durable budget and
has a deadline. Exhaustion enters MANUAL_REVIEW; audited abandonment ends local scheduling
with unknown effect. Positive matching evidence alone resolves the action. SIGTERM stops
new claims and drains one bounded cycle; cancellation/forced death leaves dispatch intent.
PostgreSQL pools pre-ping stale connections, hide SQL parameter values, and return sanitized
unavailability on disconnects. No transaction is transparently replayed.

The restore procedure is a separate operator boundary: a snapshot can lose recent dispatch
intent. Read-only recovery mode blocks admission/claims until external loss-window review.
The isolated [drill](backup-restore.md) verifies rollback, reconnect, row integrity and stale
ownership against the original sandbox provider ledger. It does not certify production DR.
