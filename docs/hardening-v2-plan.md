# Evidence-led runtime revision

Reviewed 2026-10-05 against main `c1f7b899273759ace6b54f34c5fdaeebe9cd4ce2`.
This continues the existing runtime. It does not establish production usage.

## Discovery and exclusions

Read-only discovery covered the accessible Linux user-content roots `/workspace/scratch`
(23 session directories), `/workspace/var`, `/mnt`, `/media`, and `/home`, including 12
Git checkouts. Dependency directories and Git internals were excluded from content review.
The mounted environment exposes no Windows C:/D: drives or host Desktop/Documents/Downloads.
ChatGPT Library metadata was paged to completion for engineering-relevant file types:
1,516 entries, including 202 workflow-like JSON files. Google Drive metadata was searched
for workflows, project names and backups, then browsed (40 documents returned); a MIME
filter for JSON/ZIP/Python/SQL returned no candidates. No additional connected account
was exposed. OneDrive, Dropbox, Box, VPS and host disks are unavailable through this session.
This is an accessible-source audit, not an assertion that every personal disk was inspected.

Private correspondence, customer/CRM/prospect data, CVs, credential locations, and raw
identity-bearing workflow exports were excluded. Secret contents were neither read nor
copied. No secret-file contents, production URLs, private cloud IDs or client payloads are
included here. Metadata alone cannot establish execution, ownership, or production use.

## Selected artifact inventory

Dates below are archive/review dates unless explicitly stated otherwise. Public source
revisions are pinned so later changes cannot silently change the evidence.

| Source path / filename | Type / date | Project | Classification | Behavior established | Sensitivity / reuse / sanitization | Gap |
|---|---|---|---|---|---|---|
| `/workspace/scratch/c00d5a6e947c/production-integration-reference/src/http-client.ts` | TS / reviewed Oct 2 | production-integration-reference | VERIFIED REAL EVIDENCE | Fixed URL, timeout through body read, redirect denial, idempotency header | Public source; reuse design only; synthetic HTTP bodies, example.invalid URLs | provider boundary |
| same project, `src/idempotency.ts` | TS / Oct 2 | integration reference | VERIFIED REAL EVIDENCE | PostgreSQL unique reservation, persisted SENT/FAILED | Public source; derive new tests, no client payloads | duplicates / ambiguous outcomes |
| same project, `tests/db/postgres.test.ts` | TS / Oct 2 | integration reference | VERIFIED REAL EVIDENCE | 12 competing calls, one reservation and downstream invocation; fresh connection persistence | Explicit synthetic test; preserve contention semantics, regenerate IDs | queue contention / DB |
| same project, `tests/http-client.test.ts` | TS / Oct 2 | integration reference | VERIFIED REAL EVIDENCE | HTTP adapter failure contracts | Public test; new synthetic Python fixtures | timeout / malformed response |
| same project, `db/001_init.sql` | SQL / Oct 2 | integration reference | VERIFIED REAL EVIDENCE | Durable reservation schema | Public schema; reference only, do not transplant old state model | schema evolution |
| `/workspace/scratch/c00d5a6e947c/varevant.com/n8n/tests/recovery-contract.test.mjs` | JS / Oct 2 | VAREVANT | VERIFIED REAL EVIDENCE | Per-item array return fails; corrected object preserves context without stale output | Sanitized public excerpt; use failure semantics only | poison / output validation |
| same project, `n8n/runtime-evidence/reproduced-recovery/recorded/report.json` | JSON / Oct 2 | VAREVANT | VERIFIED REAL EVIDENCE | Saved real n8n-engine error→success on identical synthetic input with one-line correction | Already labeled reproduced; never call historical production recovery | failure injection |
| same project, `n8n/runtime-evidence/reproduced-recovery/run-recovery.mjs` | JS / Oct 2 | VAREVANT | VERIFIED REAL EVIDENCE | Reproducible isolated failure harness | Public harness; design reference only | replay / runbook |
| same project, `n8n/workflows/revenue-workflow-v6.sanitized.json` | JSON / prior sanitized export | VAREVANT | SANITIZE BEFORE USE | Exported graph and controls; not proof those controls ran | No copy needed; retain as reference; regenerate all fixtures | approval / routing |
| Library `/auth-verifier.json` | JSON / uploaded Sep 18 | n8n templates | REFERENCE ONLY | Three-node item-processing template; no MAC verification | No credential bindings found; no implementation reused | auth boundary remains missing |
| Library `/failed-job-store.json` | JSON / uploaded Sep 18 | n8n templates | REFERENCE ONLY | Item-processing template; no durable store | No bindings found; no implementation reused | poison handling remains missing |
| Library `/circuit-breaker.json` | JSON / uploaded Sep 18 | n8n templates | REFERENCE ONLY | Item-processing template; no stateful breaker | No bindings found; no implementation reused | backpressure remains missing |
| Library V8/V10/V21 workflow archives and local `source_assets/*V21*.json` | JSON/ZIP / archive metadata | VAREVANT | UNKNOWN / SANITIZE BEFORE USE | File presence only; titles do not prove locks, recovery, or live integrations | Original contents excluded; possible credentials/client identifiers; do not copy | no verified incremental proof |
| `/workspace/scratch/dcf09a439dfe/portfolio-complete` and other mirrors | TS / Sep 22 archive | integration reference | DUPLICATE / LOW VALUE | Older copies of selected source | No incremental reuse | none |
| `/workspace/scratch/dcf09a439dfe/oss-webhooks*` | TS | third-party upstream | REFERENCE ONLY | Upstream reference code, not user authorship | No copied implementation | none |
| Drive CRM/customer/prospect documents and correspondence archives | metadata only | private work | SECRET / SENSITIVE — DO NOT USE | Presence only | Contents excluded; no public inventory of identities | none |

Public corroboration: integration revision `4e714a901a29ecb092816e8791b1b8303460720a`,
VAREVANT revision `a601fb150b89f5ec07dda1ac7b9e2ae9c16ed19b`.
The HTTP adapter blob is `4ee8571e7edb94171a43c2ab0d0ece92f8269a0c`;
the PostgreSQL test blob is `67958867ab084e8f702924c1d83eec40b399e121`;
the reproduction report blob is `9d911231afa8c974a206d284d4747bfed545807e`.
Local HTTP adapter SHA-256: `7edf7fde0bdc530a0e5fd5a832f232aaced884a3830657a3f792f64dcd1adc74`.
Local PG test SHA-256: `db68162c883f71b2f4b11a66278633684409471738b9dfa73fd158b3cba2448d`.
Source code and captured synthetic executions establish artifacts, not unaided authorship
or customer scale. The old adapter's transient retries are not a safe rule for uncertain
non-idempotent writes; v2 preserves the stronger runtime no-replay boundary.

## Sanitization rules

No raw client artifact is transplanted. New fixtures are explicitly synthetic and generated
from the observed failure mechanisms: timeout after commit, concurrent reservation, malformed
output, persistence loss, and return-shape violations. Names, domains, email/phone fields,
production URLs, real database IDs and identity-bearing timestamps are omitted or replaced
with synthetic IDs and localhost/example.invalid. Public excerpts already sanitized remain
references rather than claims about raw production data. Unknown exports remain unknown.

## Gap-to-evidence and implementation decision

| Gap | Existing evidence / confidence | Reusable? | Missing evidence | Proposed revision |
|---|---|---|---|---|
| Real provider integration | Generic HTTP adapter / high for local contract | Design | Live authenticated write | GitHub Issues REST adapter; deterministic local contracts; optional live test |
| Ambiguous external outcome | Runtime dispatch intent + crash tests / high | Preserve | Provider-specific lookup | Trusted actor and exact operation/body marker lookup; never replay POST |
| Multi-tenant identity | No verified prior implementation | No | Bound credential identity | Configured tenant/role key bindings; legacy tenant compatibility |
| Tenant authorization | Default-deny runtime tools / high, single tenant | Extend | Cross-tenant races | Tenant-scoped store, approvals, tool policies, webhook nonces and keys |
| Bounded reconciliation | Existing unbounded read-only lookup / high | Extend | Budget/deadline/operator semantics | Independent durable lifecycle and audited operator action |
| Metrics aggregation | Process counters + DB state gauge / high | Extend | Worker export | Per-process worker exporter; DB-derived tenant totals; explicit aggregation rules |
| Backup/restore | No verified prior drill | No | Restore integrity | Reproducible PG dump/restore drill and preserved dispatch ledger |
| DB restart/interruption | PG integration tests / high | Extend | Connection death/restart | Terminated backend, rollback, reconnect and container restart drill |
| Graceful shutdown | No verified SIGTERM drain | No | Active-operation SIGTERM | Stop new claims, bounded drain, preserve intent on forced exit |
| Queue/backpressure | PG unique reservation / high | Design | Multi-process saturation | Serialized tenant admission capacity and durable rate quota |
| Rate limiting | Process-local ingress limiter / high | Extend | Tenant-wide admissions | PG-backed admission window; gateway still bounds all ingress |
| Poison messages | n8n malformed-output reproduction / high | Failure semantics | Worker progress after unexpected bug | Quarantine pre-dispatch failure; reconcile uncertain writes; continue next job |
| Replay tooling | Synthetic recovery harness / high | Design | Safe operator procedure | Inspect/lookup/abandon only; no automatic write reset |
| Schema evolution | Migration v1 drift/roundtrip / high | Extend | Populated v1 upgrade | Add 0002, preserve legacy operation keys; guarded downgrade |
| Audit integrity | Transactional execution events / high | Preserve | Tamper-proof external archive | Operator events and least-privilege guidance; no cryptographic audit claim |
| Secret rotation | No verified rotation drill | No | Credential cutover | Multiple configured keys per role, identity audit IDs, explicit revoke/restart tests |
| Disaster recovery | No verified prior DR | No | Lost post-snapshot effects | Restore drill, maintenance/no-replay mode, explicit RPO/RTO assumptions |
| Operator runbook | Runtime runbook + reproduction procedure / high | Extend | Live incidents | Troubleshooting and bounded recovery actions; measured drills only |

## Architecture review before implementation

Keep the existing state machine and persisted dispatch intent. Tenant identity is established
by server-side credential binding, never a caller-supplied tenant header. Executions carry
tenant_id; child actions/approvals/events inherit it through their execution foreign key.
Admission business/idempotency keys and webhook receipts are tenant scoped. Workers retain
privileged global scheduling but each claimed lease carries its tenant and tool policy.

Use an independent reconciliation row per dispatched action: AMBIGUOUS → RECONCILING →
RESOLVED or back to AMBIGUOUS within budget/deadline; exhaustion → MANUAL_REVIEW. An operator
may perform a read-only verification or ABANDON with an explicit unknown-effect terminal
outcome. Neither missing lookup nor abandonment authorizes another POST. Claims are fenced,
attempts are committed before lookup, and operator races lock the execution row.

GitHub is one concrete external contract; no new service framework is necessary. The
administrator binds each tenant to a repository and trusted provider actor. Issue creation
requires approval; the model cannot choose repository, URL, token, actor or marker. GitHub
does not document an idempotency key for this POST, so a stable marker is lookup evidence,
not an exactly-once guarantee. A missing/edited/duplicated marker remains unresolved. Only
normalized, bounded responses and request IDs persist; raw provider bodies are not logged.

Global execution/admission facts come from PostgreSQL. Process-local latency/counters are
exported by every process and aggregated externally, without summing duplicated DB gauges.
No Redis/Kafka/service mesh is introduced. Tenant capacity and admission quotas serialize
through a PostgreSQL advisory lock and bounded indexed queries.

Add SIGTERM draining, explicit unexpected-error quarantine, connection-loss tests, and
disposable-database backup/restart/restore drills in CI. Do not touch production databases.
0001 remains frozen. 0002 has a guarded rollback that rejects tenant/lifecycle data loss.

Primary contracts consulted: [GitHub Issues](https://docs.github.com/en/rest/issues/issues),
[REST retry guidance](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api),
[Prometheus Python multiprocess limits](https://prometheus.github.io/client_python/multiprocess/),
[PostgreSQL 17 pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html).

The local execution environment cannot run PostgreSQL as an unprivileged service or Docker;
those gates must run in GitHub's disposable PostgreSQL/container jobs. A green local static
check is not reported as a green DB drill. Live provider writes are unavailable until a
separately configured, explicitly opted-in disposable provider repository is supplied.
