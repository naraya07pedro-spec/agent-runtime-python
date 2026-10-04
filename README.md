# Bounded Agent Runtime

A production-style Python/FastAPI reference for stateful AI execution: durable
PostgreSQL state, constrained tools, idempotency, approval, and partial-failure recovery.

The difficult case is not calling a tool. It is knowing what to do when the tool
succeeded and the worker died before recording that success. This runtime records
dispatch intent **before** external I/O, fences state changes with expiring leases,
and routes uncertain writes to read-only reconciliation.

**Scope:** an executable engineering reference, not a deployed commercial platform.
The default model is deterministic and the external service is a sandbox. There are
no production traffic, customer, uptime, professional-tenure, or model-quality claims.

## Engineering evidence

Start with these files; the important claims have executable boundaries.

| Question | Implementation / evidence |
|---|---|
| What if external success outlives the worker? | [Real process-death test](tests/failure_injection/test_process_death.py), [partial commit tests](tests/failure_injection/test_recovery.py) |
| Can two workers own one execution? | [Concurrent PostgreSQL races](tests/concurrency/test_ownership.py), [fenced store](app/store.py) |
| Can an expired worker still send? | [Late-send race test](tests/concurrency/test_ownership.py), [reliability model](docs/reliability-model.md) |
| Can the model invent authority? | [Policy and HMAC tests](tests/security/test_boundaries.py), [typed registry](app/tools.py) |
| Does changed input inherit approval? | [Approval mutation/expiry tests](tests/integration/test_runtime.py) |
| What actually passed? | [Test evidence](artifacts/test-summary.md), [CI](https://github.com/naraya07pedro-spec/agent-runtime-python/actions/workflows/verify.yml) |
| Are evals reproducible? | [Contract cases](evals/cases.jsonl), [harness](evals/run.py), [measured results](artifacts/eval-summary.md) |
| Where are the performance numbers from? | [Methodology](docs/benchmark-methodology.md), [measured admission results](artifacts/benchmark-summary.md) |
| Can someone operate it? | [Runbook](docs/runbook.md), [failure model](docs/failure-model.md), [limitations](docs/limitations.md) |

## Architecture

```mermaid
flowchart TD
    API[FastAPI admission] --> DB[(PostgreSQL state and events)]
    W[Worker] --> DB
    W --> M[Model adapter]
    M --> G{Schema and permission gate}
    G -->|Approved action| I[Durable dispatch intent]
    G -->|Needs approval| A[Persisted human decision]
    A --> DB
    I --> DB
    I --> X[External provider]
    X -->|Proven outcome| DB
    X -->|Uncertain outcome| R[Read-only reconciliation]
    R --> X
    R --> DB
```

There is no mandatory agent framework or broker. PostgreSQL is both the execution
source of truth and the work queue. Model selection cannot bypass deterministic
permissions, schemas, approval, or the durable dispatch boundary.

## Quick start

Requires Python 3.12+ and Docker with Compose.

```bash
git clone https://github.com/naraya07pedro-spec/agent-runtime-python.git
cd agent-runtime-python
python3 scripts/bootstrap.py
docker compose up --build
```

Bootstrap generates separate random local credentials and preserves existing keys.
Compose starts PostgreSQL, migrations, the API, a worker, and an independent sandbox
provider. Only the API is published, on `127.0.0.1:8000`. These Compose database
credentials are for local development. The sandbox sends no real notifications
and transfers no money.

In another terminal:

```bash
python3 scripts/smoke.py
```

This checks duplicate admission, worker execution, the approval lifecycle, readiness,
and durable metrics over real TCP. OpenAPI is at `http://localhost:8000/docs`. Protected endpoints require
the generated API key; approval decisions use the separate approval key.

### Local Python development

Install [uv](https://docs.astral.sh/uv/), then `uv sync --locked`. Set
`RUNTIME_DATABASE_URL` to your PostgreSQL instance and run `uv run alembic upgrade head`.
Run the API with `uv run uvicorn app.api:create_app --factory --no-access-log`, and
the worker with `uv run python -m app.worker`. Configure the sandbox endpoint and
credentials through `.env`; no paid model API is needed.

## Execution semantics

1. `POST /executions` binds an `Idempotency-Key` and business key to a canonical
   request fingerprint. Identical duplicates converge; conflicting reuse is 409.
2. A worker claims one row using `FOR UPDATE SKIP LOCKED`. A fresh lease token and
   database timestamp fence each subsequent worker mutation.
3. A typed model decision can finish, refuse, or propose one tool. The server owns
   the tool allowlist and input schemas. Steps, model calls, reported tokens, and
   dispatch deadlines are persisted bounds.
4. Sensitive calls wait for a persisted decision bound to the action fingerprint.
5. Dispatch intent and its event commit before HTTP I/O. No DB transaction remains
   open while waiting on a model or tool.
6. A known result is committed. An uncertain write stays in
   `RECONCILIATION_REQUIRED`; the runtime does not automatically dispatch it again.

This provides **at-most-one automatic write dispatch per durable operation**, not
universal exactly-once business effects. A provider must support trustworthy lookup
by operation identity to resolve uncertainty. An absent lookup alone cannot prove
an old worker will not send later. See [invariants](docs/invariants.md) and
[reliability semantics](docs/reliability-model.md).

```mermaid
flowchart TD
    D[Dispatch intent committed] --> E[External write]
    E --> P{Outcome commit succeeds?}
    P -->|Yes| C[Continue agent]
    P -->|No or worker dies| R[Reconciliation required]
    R --> L{Provider lookup proves completion?}
    L -->|Matching identity and result| C
    L -->|Absent or unknown| R
```

The complete [state machine](docs/state-machine.md) distinguishes execution state
from action state. A later agent failure does not undo already committed actions.

## Tools and providers

| Tool | Effect | Approval |
|---|---|---|
| `lookup_customer` | Read-only sandbox lookup | No |
| `upsert_ticket` | Idempotent sandbox write | No |
| `send_notification` | Irreversible sandbox append | Yes |
| `refund_payment` | Irreversible sandbox append | Yes |

The simulator intentionally permits duplicate irreversible requests, so the runtime
cannot hide behind provider deduplication in its safety tests. The idempotent ticket
tool separately demonstrates a downstream uniqueness contract.

`FakeProvider` recognizes `lookup customer demo`, `create ticket demo`, and
`notify customer demo`. Other prompts produce an explicit refusal. The optional
[OpenAI Responses adapter](app/providers.py) uses native function calls, disables
parallel tool calls, validates the response, and enforces HTTP/time limits. Enable
it by setting `RUNTIME_MODEL_PROVIDER=openai`, `RUNTIME_OPENAI_API_KEY`, and an
explicit supported `RUNTIME_OPENAI_MODEL`. **Live model behavior and billing have
not been validated by the deterministic test suite.**

## Verification

```bash
uv sync --locked
make lint
uv run pytest -m 'not integration'

# Dedicated disposable database; tests truncate runtime tables.
export TEST_DATABASE_URL=postgresql+asyncpg://runtime:runtime@localhost:5432/runtime_test
export RUNTIME_DATABASE_URL="$TEST_DATABASE_URL"
REQUIRE_POSTGRES_TESTS=1 uv run pytest --cov=app
make simulate
make eval
make benchmark
```

CI requires PostgreSQL, checks empty-database migrations and downgrade/upgrade,
executes all tests and deterministic evals, measures admission performance, audits
locked application dependencies, and builds/runs the complete Compose stack.
Results include date, environment, source commit, and the actual tested commit.
PR CI can test GitHub's synthetic merge commit; the evidence records that explicitly.

To reproduce the flagship failure only:

```bash
REQUIRE_POSTGRES_TESTS=1 uv run pytest tests/failure_injection/test_process_death.py -v
```

## Operational and design notes

- [Architecture and transaction boundaries](docs/architecture.md)
- [Threat model](docs/threat-model.md) and [security policy](SECURITY.md)
- [Observability](docs/observability.md) and [operator runbook](docs/runbook.md)
- [Testing strategy](docs/testing-strategy.md) and [review findings](docs/review-gates.md)
- [Architecture decisions](docs/adr/README.md)
- [Interview reasoning](docs/interview-notes.md) and [scale-up design](docs/scale-up.md)
- [Explicit limitations](docs/limitations.md)

MIT licensed. This repository's implementation and measurements are evidence of
the artifact, not evidence of production adoption, tenure, or a professional title.
