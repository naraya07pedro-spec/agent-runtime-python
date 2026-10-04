# Explain the design through its failure boundaries

These are technical study notes, not autobiographical stories. Demonstrate the code
and tests; do not describe the repository as a production system you operated.

## Trace one execution

Start at `Store.create`: canonical input is hashed; uniqueness chooses the identity;
the execution and created event commit together. A worker claims via row lock and gets
a lease token. Model-call budget is consumed **before** network I/O. The model proposes
a typed action; registry policy validates it. Approval, when required, pauses with a
persisted action fingerprint. Dispatch commits before HTTP. A matching result commits
with the outcome event, then ownership is released so a later turn can finish.

Point to `Runtime.advance` for orchestration and `Store` for transaction boundaries.
Ask what persists if the process dies between any two awaits. That question reveals
why a try/except around a tool call is not sufficient recovery design.

## Why PostgreSQL instead of another queue?

Admission uniqueness, a state update, and its audit event need one atomic transaction.
PostgreSQL already supplies that boundary. A separate broker could improve scheduling
but adds a DB/broker dual-write problem; an outbox/inbox protocol would be needed.
It should wake workers, not become an inconsistent second execution source of truth.
The current polling queue is simple and deliberately limited in throughput/fairness.

## At-least-once delivery versus effects

A caller may deliver the same event many times; database uniqueness makes it one logical
admission. Read/model work can be retried. That does **not** mean every irreversible
action is retried: this runtime records intent and permits at most one automatic write
dispatch for that durable action. Exactly-once business effects would also require the
external provider, its retention, and the caller's identity discipline to cooperate.

An idempotency key is merely an identifier unless the receiver durably enforces its
scope and lifetime. Suppose the provider sends a notification then the result DB commit
fails. Locally, “no recorded result” is indistinguishable from “request lost.” Blind retry
is unsafe; lookup by the operation identity is necessary. If lookup cannot prove a result,
the operation stays unresolved. That is an explicit availability sacrifice.

## The dangerous stale-worker schedule

1. A commits dispatch intent and pauses before sending.
2. Its lease expires; B recovers and queries the provider, which says absent.
3. A resumes and sends the action.

If B had treated absence as permission to replay, two effects could occur. The code
therefore refuses replay even after an absent lookup. A's eventual DB completion also
fails the token/expiry fence. Show the controlled-event test in
`tests/concurrency/test_ownership.py`, then the subprocess death tests. A DB lease does
not remotely cancel a packet or revoke an already accepted provider request.

## Optimistic versus pessimistic locking

An optimistic version column plus conditional updates works well when conflicts are
rare and retrying the database operation is safe. Here ownership must serialize related
execution/action/approval updates, so short pessimistic row locks simplify reasoning.
`SKIP LOCKED` lets independent workers select other ready rows rather than queue behind
one claim. Locks are released before model/tool I/O, avoiding connection and lock occupancy
for remote latency. Each completion reacquires the row and validates lease token, owner,
and expiry. Random token fencing is adequate for the local DB; monotonically increasing
fencing tokens would matter if a remote provider could enforce ordering too.

## Why explicit states and action fingerprints?

FAILED_PERMANENT means stop progressing; it does not imply no external action happened.
RECONCILIATION_REQUIRED means outcome is unknown and writes must not repeat. Keeping these
distinct prevents a generic retry worker from making the wrong decision. Terminal states
have structured outcomes and cannot return to active execution through the transition graph.

Approval authorizes one exact action, not a conversational intention. Hashing canonical
arguments plus tool/effect/approval/version binds the decision to executable meaning.
Dispatch recomputes the digest and checks expiry; approval-time validation alone has a
check/use gap. The separate credential is a demo of authority separation, not individual
identity or enterprise RBAC.

## Why distrust structured model output?

A valid JSON object can still name an unauthorized tool, forge authority, or contain
business-invalid arguments. Validate shape, then capability, then typed arguments, then
approval and durable action identity. Tool results are untrusted too: a result must match
the operation key and fingerprint before it can become authoritative. Prompt instructions
are useful guidance, not the enforcement boundary. The deterministic evals prove these
policy paths; they do not measure whether a real model chooses the right permitted action.

## Retry, budgets, and observability

Retry model/read operations only for classified transient failures, with jitter and
Retry-After. Persist counters before retry to survive crashes. Once a write was dispatched,
even a 429 is treated conservatively unless a stronger provider contract proves rejection.
Reported token budgets are not exact spend caps: usage can be lost after a paid request.
Explain this honestly instead of equating a token field with financial enforcement.

A log message is not a commit. State and event must commit together; logs carry IDs and
safe metadata for correlation. Separate API/worker metrics are process-local, so a single
API scrape is not fleet throughput. The benchmark similarly measures admission only.

## How would it evolve?

First measure queue age, DB contention, downstream rate limits, and real workload latency.
Add worker metric export, per-tenant identity/quotas, and a real provider recovery contract
before claiming production readiness. Redis can support ephemeral distributed rate limits;
Kafka can help event fanout/replay with outbox/inbox discipline; Temporal can justify itself
for durable timers and complex workflow coordination. None removes the external-effect
ambiguity by itself. See [specific adoption triggers](scale-up.md).

A defensible demonstration is: run the process-death test, inspect one dispatch and one
provider effect, explain why absence does not permit replay, then name what remains untested.
