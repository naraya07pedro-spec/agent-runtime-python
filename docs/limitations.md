# Explicit limitations

- This reference establishes an artifact and controlled tests, not production adoption,
  tenure/title, paying customers, uptime, ROI, customer scale or external certification.
- The GitHub Issues adapter is exercised with synthetic native HTTP responses and real
  PostgreSQL recovery. Its optional live authenticated write check has not been run with
  credentials. OpenAI live behavior, model reasoning quality and billing remain unverified.
- Local fencing does not cancel remote requests. Markers are correlation evidence, not
  provider-enforced idempotency. GitHub lookup covers at most 30 recent issues and trusts
  the configured actor; provider compromise, copied markers and effects outside the lookup
  window require operator review. No universal exactly-once or compensation is implemented.
- Automatic reconciliation has a durable budget/deadline. Manual review can remain unresolved
  indefinitely. ABANDONED ends local scheduling while preserving unknown external effect.
- Tenant/role isolation is enforced in application queries and policies. There is no RLS,
  OIDC/SSO, verified individual identity, automatic provisioning or four-eyes approval. The
  service/database administrators remain trusted. Provider credentials are service scoped.
- Rotation overlap/revocation is configuration based and tested with synthetic keys. Every
  real process must receive the updated configuration; no online revocation service or real
  external credential rotation has been tested.
- PostgreSQL stores prompts/arguments/outcomes in cleartext at the application layer. Log
  redaction and hidden validation inputs do not implement encryption, erasure, retention
  compliance, or tamper-proof audit. DBA alteration/deletion can destroy evidence.
- Backup/restore and PostgreSQL SIGKILL/reconnect are controlled disposable-database drills.
  There is no production RPO/RTO guarantee, automated restore detection, PITR/HA/multi-region
  failover or disk-corruption certification. Lost post-snapshot intent remains dangerous;
  maintenance mode and external loss-window review are mandatory before dispatch resumes.
- Process counters reset and must be scraped from every process. DB gauges duplicate across
  API replicas and must not be summed across duplicate scrapes. No deployed collector, SLO
  dashboard, alert rules or end-to-end OpenTelemetry tracing is claimed.
- Queue capacity/admission quotas share PostgreSQL state, but scheduling has no tenant
  fairness, priority queue, sharding or account-wide provider quota. Reconciliation and work
  each take one item per polling cycle. All-route distributed ingress quotas/slow-client
  defense, TLS, egress/DNS-rebinding policy and production secrets remain deployment work.
- SIGTERM stops new claims and allows a bounded drain. Deadline expiry/SIGKILL preserves
  intent for recovery rather than guaranteeing completion. Leases have no heartbeat and
  deadlines cannot undo a remote effect already accepted.
- Poison pre-dispatch jobs are quarantined as FAILED_PERMANENT with an explicit code;
  uncertain writes stay in reconciliation. No automatic poison-job replay or external DLQ
  service is implemented. Business rejection versus programmer defect still needs diagnosis.
- The populated v1→v2 upgrade and guarded downgrade are tested on synthetic databases.
  Rolling mixed-version customer deployment is not certified; drain old workers first.
- Reported tokens can be lost after a paid call; token budgets are not exact spend caps.
  Admission benchmarks omit TCP/TLS/model/tool latency and are not production capacity.
