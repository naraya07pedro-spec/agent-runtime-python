# Architecture decisions

These decisions record the implemented reference, not a hypothetical platform.
Each names the constraint, rejected alternative, and consequence so a reviewer can
challenge the tradeoff. See [scale-up criteria](../scale-up.md) before changing them.

## 001 — PostgreSQL owns execution truth

**Context:** identity, ownership, outcome, and audit events must agree after restart.
**Decision:** use PostgreSQL transactions and constraints for all authoritative state.
**Alternative:** in-memory state, Redis-only leases, or a broker as outcome storage.
**Consequence:** correctness has one transactional boundary; DB availability and preserved
history become prerequisites. Queue scale competes with state/history workload.

## 002 — Explicit execution and action states

**Context:** a generic failed flag cannot distinguish safe retry from an uncertain write.
**Decision:** an explicit transition graph plus separate action status and approval rows.
**Alternative:** implicit control flow or a single success/error boolean.
**Consequence:** recovery policy is inspectable and tested; every new edge requires reviewing
side effects, lease release, terminal outcome, and event semantics.

## 003 — Duplicate delivery converges through identity

**Context:** callers and workers may repeat requests after losing responses.
**Decision:** unique business/idempotency keys plus immutable canonical fingerprints.
**Alternative:** pre-check then insert, or claim universal exactly-once delivery.
**Consequence:** concurrent duplicates converge at the database; conflicting reuse fails.
New keys and lost/restored identity history fall outside deduplication guarantees.

## 004 — Commit intent before external I/O; reconcile uncertainty

**Context:** ordinary HTTP and a local DB cannot atomically commit an external effect.
**Decision:** persist dispatch, send once, then persist result; uncertain writes require
read-only provider lookup. Absence never authorizes replay.
**Alternative:** retry every timeout, or assume downstream idempotency is universal.
**Consequence:** duplicate automatic writes are prevented at the cost of possibly permanent
uncertainty. One DB transaction never remains open across a provider request.

## 005 — Approval binds to a persisted action

**Context:** model arguments can change and approvals can arrive late or race.
**Decision:** fingerprint exact tool/args/effect/approval policy/version; persist one immutable
decision and expiry, revalidate on dispatch, separate approval credential.
**Alternative:** a prompt saying “approved,” an in-memory flag, or reusable blanket approval.
**Consequence:** stale/mutated actions cannot borrow authority. Configured tenant/role credential IDs now separate authority; attribution remains key based,
not verified individual identity. GitHub action fingerprints additionally bind repository/actor.

## 006 — Typed, default-deny tool registry

**Context:** structured output can still request an unknown or dangerous capability.
**Decision:** server-owned tool names, strict Pydantic inputs, fixed endpoint, effect class,
approval, timeout, retry, idempotency, and audit metadata.
**Alternative:** dynamic Python eval, model-selected URLs, unconstrained framework plugins.
**Consequence:** adding a tool is an explicit engineering change; schema validity does not
replace real authorization/business policy at a provider.

## 007 — Provider abstraction ends at a strict decision contract

**Context:** fake tests and a real model adapter must obey the same runtime rules.
**Decision:** one `ModelProvider` protocol returning validated `ModelTurn`; OpenAI native
function calls with parallel calls disabled, followed by independent runtime validation.
**Alternative:** trust free-form text or let an agent SDK execute tools itself.
**Consequence:** deterministic policy evals run offline; these evals do not establish live
model reasoning quality. Unknown or malformed output fails closed.

## 008 — Pessimistic claims and token fencing

**Context:** two workers can race; an expired worker may resume after a new claim.
**Decision:** short `FOR UPDATE SKIP LOCKED` claims, UUID lease tokens, owner and DB-time
expiry checked under a row lock before worker mutation.
**Alternative:** long-held transaction across I/O or optimistic version retry everywhere.
**Consequence:** database completion is fenced without holding connections through external
latency. Remote HTTP is not fenced. Leases do not renew; long tasks need a new protocol.

## 009 — Retry only classified safe work

**Context:** 429/timeout/5xx do not universally imply no external effect.
**Decision:** model/read transient failures get bounded exponential jitter and unshortened
Retry-After; any dispatched write error goes to reconciliation.
**Alternative:** blanket retry decorator or immediate fixed-interval retry loops.
**Consequence:** retry behavior survives restart and avoids storms/unsafe writes. Conservative
handling may leave a write unresolved even when a provider rejected it before any effect.

## 010 — Transactional events, sanitized operational logs

**Context:** a success log can precede a failed commit; logs may contain sensitive input.
**Decision:** append state events in the state transaction, return keyset-paginated history,
and emit only flat allowlisted log metadata and fixed event names.
**Alternative:** reconstruct state from access logs or log arbitrary exception/body content.
**Consequence:** persisted history is reliable evidence; sanitized logs give less diagnostic
detail. Worker metrics are process-local; fleet export is a separate deployment concern.

## 011 — Server-bound tenant identity and shared admission bounds

**Context:** globally unique keys and shared API authority cannot isolate unrelated tenants.
**Decision:** server-side tenant/role credential bindings, scoped execution queries/approvals,
tenant business/idempotency/nonce uniqueness and a claimed lease tenant. PostgreSQL advisory
locks serialize capacity/quota checks across API processes; child rows inherit identity
through their execution FK. Preserve legacy keys.
**Consequence:** no new identity service/queue is needed, but workers/DBAs remain privileged.
Configuration rotation needs rollout to all processes; RLS/OIDC/fair scheduling remain future work.

## 012 — Bounded reconciliation has its own ledger

**Context:** execution state alone cannot record consumed lookup budget, deadline or operator
resolution while preserving external uncertainty.
**Decision:** an action-owned reconciliation row, attempts committed before GET, fenced claims,
manual review on exhaustion, positive lookup resolution and audited unknown-effect abandonment.
**Consequence:** automated recovery terminates; uncertainty may persist manually. No reset or
compensation endpoint is introduced and a late external request cannot be cancelled locally.

## 013 — One concrete provider contract with target-bound approval

**Context:** generic HTTP fixtures cannot document a real non-idempotent provider contract.
**Decision:** GitHub Issues, fixed origin, trusted actor/exact marker lookup, persisted
repository/actor binding and normalized bounded responses. Deterministic contracts are the
CI default; a single live write requires separate disposable-repository opt-in.
**Consequence:** provider-specific limits are inspectable; live write behavior remains unverified
until that optional test really passes. A marker is not provider-enforced exactly-once behavior.

## 014 — Process metrics and database facts are distinct

**Decision:** export every worker process separately and protect API process telemetry with
infrastructure authority. Tenant DB gauges already cover all workers; do not sum duplicate
replica scrapes.
**Consequence:** fleet rates need external scraping/aggregation. No misleading global counter
is synthesized from one API process and no deployed collector is claimed.

## 015 — Restore is an operator safety boundary

**Context:** a consistent backup can still omit dispatches whose effects happened later.
**Decision:** backup/restore wrappers and a disposable SIGKILL/rollback/integrity/reconnect
drill; read-only recovery mode before any restored worker claim. Verify original provider
evidence and the snapshot loss window before dispatch resumes.
**Consequence:** reproducible operational evidence without fake RPO/RTO guarantees. PITR/HA,
production promotion and automatic missing-intent reconstruction are outside this revision.
