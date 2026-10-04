# Review gates and material findings

This review maps actual changes to executable evidence. It is a self-review, not an
independent security audit or certification. Verification summaries retain exact source
and tested commit identities; see [test evidence](../artifacts/test-summary.md).

| Gate | Finding / decision | Evidence |
|---|---|---|
| Ownership | Expired workers must not persist late success | Token/owner/DB-time fence; six concurrency scenarios |
| Remote effects | Absent lookup cannot authorize replay while an old worker may resume | Controlled delayed-send race; unresolved state retained |
| Partial commit | External success can outlive one or both local repair writes | Outcome/reconciliation fault hooks; restart tests |
| Process boundary | Exceptions alone do not establish process-death behavior | Separate worker exits after claim and after external commit |
| Admission | Application pre-checks would race | Unique constraints; 20 concurrent deliveries; one created event |
| Approval | Action mutation/expiry and competing decisions can invalidate authority | Digest recheck at dispatch; separate credential; race/denial tests |
| Replay | Nonce receipt must be durable and atomic with admission | Eight simultaneous signed deliveries; conflict rolls back receipt |
| Database | SQL non-null permits JSON `null`; partial lease/decision rows undermine invariants | JSON-object CHECK constraints, active lease and complete approval constraints; direct SQL tests |
| Trust | Typed model output can still request unauthorized tools or authority | Default-deny registry; strict contracts; 12 deterministic eval cases |
| HTTP | Reading an entire upstream response before checking length leaves allocation unbounded | Streamed response cap and HTTP contract tests |
| Configuration | Readiness previously checked DB/auth but not selected provider configuration | Model/tool preflight checks and readiness regression |
| Error contract | Ingress size/rate errors lacked request IDs | Sanitized uniform error metadata and security assertions |
| Bootstrap | Inspection alone cannot prove Compose works | Clean CI image build, actual TCP API/worker/approval/readiness/metrics, cleanup |
| Migrations | Metadata and frozen initial DDL must agree | Empty upgrade, Alembic drift check, destructive roundtrip in disposable CI DB |
| CI | Early PostgreSQL health command quoting prevented startup | Corrected command; subsequent full PostgreSQL and container jobs pass |
| Test gates | Optional local DB skips can disguise incomplete verification | Required-DB CI/Make targets; fast subset explicitly documented |
| Evidence | Benchmark scope and source identity can be overstated | Actual JSON/JUnit/profile/provenance; no production-capacity or model-quality claims |
| Operations | Process-local metrics do not represent fleet activity | Explicit telemetry limitation and deployment follow-up |

Remaining limitations are [listed separately](limitations.md), including provider lookup
assumptions, no tenant isolation, no tested restore/HA, and no live model-quality evidence.
They are not silently converted into implemented features by this review.
