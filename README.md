# Bounded Agent Runtime

## Hiring manager quick scan

**Role fit:** Python Backend · FastAPI · API/Integration · AI Application Engineering

**What to notice:** this is not a prompt demo. It is a PostgreSQL-backed runtime built around explicit state, authorization, approvals, worker fencing, uncertain external outcomes and recovery.

- **Python / FastAPI / Pydantic / PostgreSQL**
- **261 passing tests** and **90.01% coverage** in the preserved v2 evidence archive
- Real process-death, concurrency, tenant-isolation and PostgreSQL interruption tests
- Persistent approvals and bounded reconciliation instead of blind retries
- CI covers typing, migrations, security checks, Docker smoke, evals and recovery drills

**Fastest review path:** [architecture](docs/architecture.md) → [process-death tests](tests/failure_injection/test_process_death.py) → [tenant security](tests/security/test_tenants.py) → [recovery drill](scripts/recovery_drill.py)


A Python/FastAPI agent runtime with PostgreSQL state, tenant authorization, persistent
human approvals and recovery for uncertain external effects.

When a tool succeeds and its worker dies before recording the result, blind retry can
repeat the effect. This runtime commits dispatch intent before HTTP, fences database
updates with leases, and resolves uncertainty through bounded, read-only reconciliation.

**Verified scope:** an executable engineering reference with PostgreSQL concurrency,
process-death, isolation, failure and security tests. Default decisions/effects are
synthetic. The GitHub Issues adapter has deterministic contract tests; live authenticated
provider writes and live model quality remain unverified. No production users, uptime,
customer scale, professional title or universal exactly-once guarantee is claimed.

## Review the evidence

| Engineering question | Executable evidence |
|---|---|
| External success outlives the worker | [Process death](tests/failure_injection/test_process_death.py), [partial commits](tests/failure_injection/test_recovery.py) |
| Competing or stale workers | [PostgreSQL ownership races](tests/concurrency/test_ownership.py) |
| Cross-tenant access or approval replay | [Tenant security tests](tests/security/test_tenants.py), [tenant model](docs/tenant-model.md) |
| Provider commits before timeout | [GitHub runtime recovery](tests/integration/test_github_runtime.py), [native contracts](tests/contract/test_github_provider.py) |
| Target changes after approval | [Provider binding regression](tests/security/test_provider_binding.py) |
| Recovery consumes budget or races operators | [Reconciliation lifecycle tests](tests/failure_injection/test_reconciliation_lifecycle.py) |
| Shutdown, overload and poison jobs | [Real SIGTERM](tests/failure_injection/test_sigterm.py), [admission contention](tests/concurrency/test_admission_bounds.py) |
| Database interruption and restore | [Connection death](tests/failure_injection/test_database_interruptions.py), [SIGKILL/restore drill](scripts/recovery_drill.py) |
| What passed on this revision | [Exact-head CI](https://github.com/naraya07pedro-spec/agent-runtime-python/actions/workflows/verify.yml): generated JUnit, coverage, eval, benchmark and recovery artifacts |

[Evidence inventory and 18-gap plan](docs/hardening-v2-plan.md) record source provenance,
exclusions and sanitization. New fixtures preserve observed failure mechanisms and are
explicitly synthetic; private workflow/customer data is not copied. Committed
[earlier reports](artifacts/test-summary.md) retain their original v1 SHA/date. Use current
CI artifacts for later revisions; test counts are not counts of unique failure scenarios.
The [v2 archive](evidence/v2/README.md) preserves its tested SHA, 261 passing tests,
90.01% coverage and actual interruption/restore drill results.

## Architecture

PostgreSQL owns execution identity, the work queue, approvals, recovery budgets and
transactional events. Workers take short row locks, release them before I/O, and recheck
lease token/owner/tenant/expiry before completion. A separate reconciliation ledger tracks
AMBIGUOUS, RECONCILING, MANUAL_REVIEW, RESOLVED and ABANDONED. Unknown or absent provider
results never permit another write. Abandonment records `effect_unknown`, not cancellation.

Tenant identity comes from server-bound credentials. Model output cannot choose tenant,
repository, URL, authority, approval or retry safety. Per-tenant admission capacity and
quotas are shared through PostgreSQL. API/worker process metrics are exported separately;
DB metrics represent durable facts across workers. No mandatory broker or agent framework.

Read [architecture](docs/architecture.md), [recovery semantics](docs/reliability-model.md),
[reconciliation](docs/reconciliation.md), [provider contract](docs/provider-contract.md)
and [limitations](docs/limitations.md).

## Run the sandbox

Requires Python 3.12+ and Docker Compose.

```bash
git clone https://github.com/naraya07pedro-spec/agent-runtime-python.git
cd agent-runtime-python
python3 scripts/bootstrap.py
docker compose up --build -d --wait
python3 scripts/smoke.py
```

Bootstrap creates random local keys only when `.env` does not exist; existing configuration
is left unread and unchanged. Compose runs PostgreSQL, migrations, an API, worker and
independently committed sandbox provider. Only API port 8000 is published on loopback.
Sandbox notifications/refunds send no messages or money. OpenAPI: `http://localhost:8000/docs`.
API, approval and operator credentials have distinct authority. See the [runbook](docs/runbook.md).

For Python development: `uv sync --locked`; configure PostgreSQL and the sandbox through
injected environment or local configuration, run `uv run alembic upgrade head`, then
`uv run uvicorn app.api:create_app --factory --no-access-log` and
`uv run python -m app.worker`. Use disposable databases for all tests.

## Provider and security boundaries

| Tool | Effect | Approval |
|---|---|---|
| lookup_customer | Sandbox read | No |
| upsert_ticket | Sandbox idempotent write | No |
| send_notification / refund_payment | Sandbox irreversible append | Yes |
| create_issue | Non-idempotent GitHub issue in a bound repository | Yes |

The fake model recognizes `lookup customer demo`, `create ticket demo` and
`notify customer demo`. The optional OpenAI Responses adapter uses native function calls
with independent validation and bounded I/O. Configure credentials outside chat; no paid
provider call is part of normal CI. [Optional live GitHub check](provider_checks/test_github_live.py)
requires a separate disposable-repository opt-in and otherwise skips.

## Verify and operate

```bash
uv sync --locked
make lint
# TEST_DATABASE_URL must name a disposable *_test database; tests truncate it.
REQUIRE_POSTGRES_TESTS=1 uv run pytest --cov=app
make eval
make benchmark
```

CI checks the exact PR head with Ruff, strict mypy, PostgreSQL tests, deterministic evals,
87% minimum combined statement/branch coverage, migration roundtrip/drift/populated upgrade,
tracked credential patterns, dependency audit, Docker build/TCP smoke, worker metrics and
actual PostgreSQL SIGKILL/restore. The optional live provider result is separate from the
normal test count. Benchmark artifacts measure CI-runner admission only, not production capacity.

[Backup/restore](docs/backup-restore.md) requires read-only recovery mode and review of
post-snapshot external effects before workers resume. [Threat model](docs/threat-model.md),
[operability](docs/observability.md), [ADRs](docs/adr/README.md),
[red-team findings](docs/review-gates.md) and [interview notes](docs/interview-notes.md)
explain both verified behavior and unresolved boundaries.

MIT licensed. Claims describe the artifact and measured tests, not production history.
