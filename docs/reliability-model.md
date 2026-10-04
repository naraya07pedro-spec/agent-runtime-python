# Idempotency, leases, and partial commits

## Request identity

The caller must preserve the business key and Idempotency-Key across delivery
retries. The canonical request digest includes the prompt and execution bounds.
Both keys are uniquely constrained. Same keys and payload return the existing
execution; reusing either identity for a conflicting request returns 409. Replacing
the idempotency key while retaining the business key also returns 409.

Identity records have no TTL and no automatic deletion. Retention or backup restore
can destroy deduplication evidence and needs an explicit future policy. A new
business key is a new operation; semantic similarity between two different keys is
not an implemented deduplication guarantee.

## Action identity

Each validated proposal binds tool name, canonical arguments, effect classification,
approval requirement, and contract version into a fingerprint. An operation key
binds that fingerprint to the execution's business key. Repeating an identical
action in a later model turn is denied. Retry uses the same persisted call and key.

The tool schema and permission are checked again before a pending action dispatches.
The store recomputes its fingerprint and checks persisted approval at dispatch time.
Expiration or mutation cannot borrow authority from an old approval.

## Why an idempotency key is insufficient

An external side effect and the runtime's result row cannot be atomically committed
with ordinary HTTP. If the effect commits and the result write fails, local state
cannot distinguish success from a lost request. Blind retry could duplicate the
effect even though the local caller used an idempotency key: the receiver may ignore
that key, expire it, or apply it to a different scope.

This implementation commits dispatch intent first and never automatically sends
another write for that intent. If a result is uncertain, the action is reconciled by
read-only lookup using operation identity and fingerprint. Only positive matching
evidence completes it. An absent lookup keeps the action unresolved.

## Why absence does not prove replay is safe

Consider worker A paused immediately after committing intent. Its lease expires.
Worker B recovers the execution and queries the provider, which says absent. Worker
A can still wake up and send. If B treats absence as permission to retry, both can
produce an effect. A database lease fences database mutations, not remote HTTP.

The late-worker test controls this exact schedule with two asyncio events. A positive
result from the old worker is rejected by its stale token; the new reconciler later
records the provider's already-completed operation. An unresolved action may remain
stuck indefinitely. That loss of availability is intentional.

## Guarantees and assumptions

- Durable admission uniqueness and fenced state mutation depend on one authoritative
  PostgreSQL database, its uniqueness constraints, and preserved history.
- There is at most one automatic write dispatch for one persisted action. The HTTP
  client performs no automatic request replay, and redirects are prohibited.
- The runtime cannot prove the provider creates only one internal effect from one
  request, nor can it protect against out-of-band provider calls or malicious DB admins.
- Read/model retries can repeat work and incur additional model cost. They preserve
  local action identity and consume persistent call/retry budgets.
- Reported tokens are enforced before dispatch; token usage lost on a timeout and
  input-token billing are not an exact spend cap. Cost metadata stays null when unknown.
- Successful tool results remain durable even if a later model turn fails. Execution
  failure is not rollback of external effects.

## Recovery interface

The worker automatically recovers expired leases. `POST /executions/{id}/reconcile`
performs a bounded provider lookup; it never accepts a caller-supplied success result
or a force-retry option. Provider lookup must retain identity long enough for recovery.
If the provider cannot prove an outcome, an operator must investigate outside this
runtime. Deleting the row and creating a new key is not a supported recovery procedure.
