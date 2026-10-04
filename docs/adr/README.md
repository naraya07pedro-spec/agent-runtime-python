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
**Consequence:** stale/mutated actions cannot borrow authority. Shared-key actor attribution
is coarse and would need real identity for multi-user deployment.

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
