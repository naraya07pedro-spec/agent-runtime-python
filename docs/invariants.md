# Invariants and scope

Written before the runtime implementation. Each invariant must have an executable
test; the evidence map is maintained in the README. This is a reference system,
not evidence of production deployments or professional tenure.

1. A request identity (business key + idempotency key + canonical request digest)
   is immutable. Concurrent duplicates converge; conflicting reuse is rejected.
2. Every external action has one durable operation identity. Dispatch is recorded
   before I/O. An ambiguous write is never automatically dispatched again.
3. This is **not universal exactly-once execution**. If an external outcome cannot
   be proved, availability is sacrificed: the operation stays in reconciliation.
   Downstream systems must not turn one request into multiple effects themselves.
4. Missing configuration fails before dispatch and cannot produce success.
5. Unknown tools and permissions absent from the server's allowlist are denied.
6. Model output, tool input, provider output, and persisted action fingerprints
   are validated at their respective trust boundaries.
7. Approval is a durable, immutable decision tied to one action fingerprint and
   expiration. Changed or expired actions cannot inherit approval.
8. One unexpired lease token owns an execution. Every state mutation by a worker
   is fenced with that token and checked against database time.
9. Lease expiry does not prove a previous worker stopped. A dispatched write goes
   to reconciliation; a read/model turn may be recovered and retried within budget.
10. State transition and its event commit together. Terminal states carry a
    result or classified error; uncertain writes remain explicitly nonterminal.
11. Retries reuse the persisted action and operation key. Attempt, step, token,
    wall-clock, and timeout bounds survive restarts.
12. Normal logs contain identifiers and allowlisted metadata, never prompts,
    tool arguments, results, authorization headers, signatures, or exception text.
13. A signed webhook's replay record and execution creation commit atomically.
14. Reconciliation is read-only toward the external provider. An absent lookup
    does not authorize replay: a delayed old worker may still send its request.

## Verification policy

Concurrency claims require real PostgreSQL transactions and concurrent tasks.
Failure recovery requires inspecting durable state and independent provider
effects. Deterministic contract evals are separate from model quality. Performance
numbers must include their environment and exclude any production-capacity claim.

## Initial inspection

Base: `main`, commit `a72df099963580bc2e50944742e6b9227a9df479`.
The repository contained only a Python `.gitignore` and Evan Naraya's MIT license.
Both are preserved. Work is isolated on `feat/staff-grade-agent-runtime`.

## V2 invariants

- Tenant identity is server-bound; every scoped read/write/approval inherits execution tenant.
- Tenant business/idempotency/nonce identity is independent; lease mutation checks tenant.
- GitHub approval and reconciliation retain their repository/actor binding; configuration
  changes cannot retarget a previously approved action.
- Reconciliation attempts persist before GET. Budget/deadline exhaustion stops automatic
  lookup; operator abandonment records unknown effect without resetting dispatch identity.
- Read-only restore mode blocks admission/claims; loss-window review precedes resumed writes.
- SIGTERM stops new claims and drains bounded work; forced cancellation preserves durable intent.
