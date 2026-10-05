# Verification strategy

The strongest claims require PostgreSQL transaction semantics and an independently
committed provider effect. SQLite/in-memory substitutes are not used to certify
ownership, deduplication, replay receipts, or recovery.

| Suite | What it proves |
|---|---|
| Unit | All 81 state pairs, fingerprint properties, terminal outcomes, bounds, error classification, logging redaction |
| Contract | Strict API/provider schemas, missing configuration, bounded upstream bodies, identity validation |
| Integration | Durable model/action lifecycle, approval mutation/expiry/denial, budgets, database constraints |
| Concurrency | Admission/ownership/approval races, late sends, tenant capacity/quota across independent connections |
| Failure injection | Partial commits, poison jobs, bounded reconciliation/operator races, connection termination, real SIGTERM |
| Process death | Real worker exits after claim and after irreversible provider success; replacement ownership/reconciliation |
| Security | HMAC/replay, tenant/role isolation, rotation, target-bound approval, policy and ingress limits |
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

Coverage exposes untested code; it does not prove correctness. CI requires 87% combined
statement/branch coverage alongside the scenario gates. No percentage replaces failure
semantics. The suite does not certify real model quality, live authenticated provider writes,
production load or multi-region failover.

CI checks out the exact PR head, not a synthetic merge commit. A separate recovery drill
uses a real PostgreSQL SIGKILL, rollback/reconnect, backup wrappers and isolated restore.
It checks row integrity and reconciles only against the original synthetic provider ledger;
its timings are controlled-drill measurements. The optional live-provider XML/status is
reported separately: its default skip is not counted as a passing live contract.

