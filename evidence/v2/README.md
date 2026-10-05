# V2 measured evidence

Archived from successful [CI run 37262155984](https://github.com/naraya07pedro-spec/agent-runtime-python/actions/runs/37262155984),
which checked out and tested exact source `833a4eff040bea8b952e84d9b5207c2335c036c2`.
Both verify and container-smoke passed. These reports retain that original source/date;
they do not silently become results for a later commit. Current head/main CI is the
authority for subsequent changes.

| Evidence | Scope |
|---|---|
| [Test summary](test-summary.md), [JUnit](junit.xml), [counts](test-counts.json) | 261 passed; 0 failed/skipped; includes the 81-pair transition matrix |
| [Coverage totals](coverage-totals.json) | 90.01% combined lines/branches; extracted unmodified from machine coverage JSON |
| [Deterministic evals](eval-summary.md) | 12/12 contract cases; no live model-quality assessment |
| [Provider status](optional-provider-status.json) | One optional authenticated provider test skipped; no live write claim |
| [Recovery drill](recovery-summary.md), [raw result](recovery-drill.json) | Actual disposable PostgreSQL SIGKILL, rollback, reconnect and isolated restore; synthetic provider |
| [Admission sample](benchmark-summary.md), [raw result](benchmark-results.json) | ASGI admission only; 300 requests, concurrency 10; no production-capacity inference |
| [Verification provenance](verification-provenance.json), [archive hashes](archive-provenance.json) | Exact commit, app/test/migration/dependency identities and downloaded archive checksum |

Full per-file coverage JSON remains in the linked CI artifact (30-day retention). Its
SHA-256 and unchanged totals are saved here; other listed machine reports are byte-for-byte
copies. Artifact naming uses the workflow event SHA, while provenance records the actual
checked-out PR head. No database dump, credentials, private workflow or client payload is
archived. Recovery timings measure one small synthetic CI database, not RPO/RTO guarantees.
