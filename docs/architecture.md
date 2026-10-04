# Architecture and data ownership

The runtime separates contracts (`domain.py`), transactional state (`store.py`),
execution coordination (`runtime.py`), providers, and HTTP ingress. It uses 17 small
application modules instead of one package directory per architectural noun.
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
| executions | UUID PK; unique business and idempotency keys; legal state, complete/active lease and terminal JSON-object checks | `(state,next_attempt_at)` ready queue; `(state,lease_expires_at)` recovery |
| tool_calls | UUID PK; execution FK; unique ordinal, action fingerprint, operation key; valid counters and successful outcome checks | Per-execution ordered history and pending action |
| approvals | One row per action; action FK; bound fingerprint, expiration and complete decision metadata | Direct action lookup |
| execution_events | Bigint sequence; execution and optional action FK | `(execution_id,id)` keyset pagination |
| webhook_receipts | Nonce digest PK; expiration | Expiry index for cleanup |

Event IDs are monotonic but not gap-free because rolled-back sequences consume
values. Pagination does not assume contiguity. Idempotency records and leases are
columns on executions; a separate table would add joins without a distinct lifecycle.
Reconciliation is an indexed execution state, not a second competing queue.

The sandbox's `sandbox_external_effects` table belongs to its own metadata and is
excluded from runtime migration drift checks. It is a test provider, not application
business storage. A real integration supplies its own durable provider identity and
lookup contract.

## Ownership and isolation

READ COMMITTED plus row locking is sufficient for the chosen operations. Admission
relies on database uniqueness and `INSERT ... ON CONFLICT DO NOTHING`; an application
pre-check would race. Claims skip locked rows. Every completion reacquires the row,
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
