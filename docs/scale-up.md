# Evolution driven by measured pressure

The current reference favors one transactional source of truth and inspectable failure
semantics. These are conditional design options, not implemented features or a roadmap
with promised dates.

| Observed pressure | Next change | Invariant to preserve / evidence needed |
|---|---|---|
| Worker effects invisible in API counters | Export worker metrics with instance labels; aggregate without high-cardinality IDs | Compare durable events to counters; test worker restart/reset behavior |
| Real provider adoption | Provider-specific idempotency/lookup retention, authorization, sandbox acceptance tests | Re-run partial-commit and delayed-send tests against that provider contract |
| Multiple customers | OIDC identity, tenant-scoped keys/queries/permissions, per-tenant quotas | Cross-tenant negative tests for every read/write and approval path |
| Queue age grows while DB is idle | Bounded concurrent worker loops, fair scheduling, batching of claims | Ownership fencing stays per execution; test saturation and cancellation |
| Polling/claim contention dominates | Measure realistic indexes; consider LISTEN/NOTIFY as wakeup hints | DB remains truth; lost notifications cannot strand work |
| Many consumers need event fanout | Transactional outbox to a broker; consumer inbox deduplication | No DB/broker dual-write gap; broker acknowledgement is not external success |
| Shared rate limits exceed one process | Redis or gateway quota store with explicit outage behavior | Rate-limit outage must not silently grant authority or duplicate writes |
| Long tasks exceed lease horizon | Provider jobs with durable job identity/polling, or bounded heartbeat protocol | Expiry still does not cancel an old request; test pause/resume races |
| Complex timers and workflow branching | Evaluate Temporal using real operational/cognitive costs | Durable workflow orchestration does not solve side-effect ambiguity automatically |
| History/table size dominates queries | Retention/archive policy, partitioning after measured plans | Never delete deduplication/lookup identity before its safety horizon |
| Availability needs exceed one DB node | Managed HA, tested backups/PITR, recovery drills | Quantify lost-history risk; reconcile provider outcomes across restore window |

Kafka is not warranted by a benchmark of 300 admissions. Redis is not a replacement
for durable identity. Kubernetes does not create recovery semantics. Start with a workload,
a concrete bottleneck, and a failure test for the proposed change.
