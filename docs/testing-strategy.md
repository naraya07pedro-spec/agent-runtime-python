# Verification strategy

The strongest claims require PostgreSQL transaction semantics and an independently
committed provider effect. SQLite/in-memory substitutes are not used to certify
ownership, deduplication, replay receipts, or recovery.

| Suite | What it proves |
|---|---|
| Unit | All 81 state pairs, fingerprint properties, terminal outcomes, bounds, error classification, logging redaction |
| Contract | Strict API/provider schemas, missing configuration, bounded upstream bodies, identity validation |
| Integration | Durable model/action lifecycle, approval mutation/expiry/denial, budgets, database constraints |
| Concurrency | 20-way admission/ownership races, action and approval races, stale completion, controlled late-send schedule |
| Failure injection | Pre-dispatch rollback, uncertain outcome, double DB failure, restart, Retry-After, recovery budgets |
| Process death | Real worker exits after claim and after irreversible provider success; replacement ownership/reconciliation |
| Security | HMAC exact-byte binding, expired signatures, concurrent replay, atomic receipt rollback, separate authority, ingress bounds |
| Deterministic evals | Twelve fixed model decisions evaluated against real runtime policy; zero paid model calls |

`pytest` marks cross-cut these directory categories. For example, a security test
using PostgreSQL has both `security` and `integration` marks. The generated report
counts by directory, so totals are not duplicated. The 81-pair unit matrix is disclosed
rather than presented as 81 distinct failure scenarios.

The process-death test starts a separate sandbox HTTP server, commits an irreversible
effect through TCP, then calls `os._exit(73)` in the worker before local outcome commit.
A second case exits with code 74 immediately after the claim transaction. These are
real abrupt process exits without Python finally handlers; not power-loss, kernel panic,
or SIGKILL-on-arbitrary-instruction testing. Tests move lease expiry into the past by
SQL instead of waiting 60 seconds. The late-worker concurrency test uses explicit
asyncio events to control order instead of timing-sensitive sleeps.

CI requires `TEST_DATABASE_URL` and `REQUIRE_POSTGRES_TESTS=1`, preventing an absent
DB from yielding a misleading green suite full of skips. `make test` and `make simulate`
also enforce this. A deliberate local fast check is `uv run pytest -m 'not integration'`;
that command is not complete verification.

The workflow performs locked dependency installation, Ruff format/lint, strict mypy
for application code, migration upgrade/check/downgrade/upgrade, branch coverage,
all test categories, deterministic evals, a measured benchmark, dependency audit,
and a separate clean Compose build/TCP smoke/cleanup job. JUnit, coverage JSON,
structured eval/benchmark results, summaries, profile, and commit provenance are
uploaded as a 30-day artifact. Selected results are archived in the repository with
their original source/tested SHA; later CI artifacts remain authoritative for later heads.

Coverage exposes untested code; it does not prove correctness. No arbitrary percentage
is used as a substitute for the failure scenarios. The suite does not certify real
LLM quality, live provider behavior, production load, backup restore, or multi-region failover.
