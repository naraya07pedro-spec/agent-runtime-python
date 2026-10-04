# Explicit limitations

- This is an executable reference, not evidence of production adoption, a professional
  title, years of experience, paying customers, uptime, or external audit certification.
- Default decisions and provider effects are deterministic/sandboxed. Live OpenAI
  integration has contract tests but no paid live-call or model-quality evaluation.
- Guarantees depend on one authoritative PostgreSQL history. Restore/deletion can lose
  deduplication evidence. Backup/PITR, failover, replication, and disaster recovery are
  design considerations, not tested capabilities.
- There is no universal exactly-once guarantee. A lease fences local database writes,
  not remote requests. Unprovable external outcomes may remain unresolved indefinitely.
- Reconciliation is operator-triggered and read-only. There is no force replay, provider
  compensation workflow, automatic refund reversal, or automatic uncertainty resolution.
- Tools implement a sample shared provider contract. Real integrations need provider-specific
  lookup/retention semantics, per-customer authorization, and business-level validation.
- Shared API/approval credentials demonstrate separated authority, not multi-tenant RBAC,
  SSO, individual human attribution, key rotation grace windows, or four-eyes approval.
- PostgreSQL stores prompt, arguments, and results in cleartext at the application layer.
  Log redaction does not provide database privacy, encryption, erasure, or retention compliance.
- The local Compose database uses development credentials. TLS termination, production
  secrets, egress policy, DNS rebinding defense, distributed quotas, and slow-client defense
  are deployment responsibilities not implemented here.
- The default ingress limiter is global to one API process. It is not a per-tenant quota,
  abuse-resistant public gateway, or distributed admission controller.
- Worker counters are process-local and not exported by the separate API process. Durable
  state gauges/events are shared. No OpenTelemetry collector, alert rules, or SLO dashboard
  is bundled; do not infer complete fleet telemetry from the demo metrics endpoint.
- Leases have no heartbeat; long-running tools need a revised protocol. The execution
  deadline prevents new claim/dispatch/retry past the bound, but does not cancel an already
  accepted remote effect or act as a hard total wall-clock SLA.
- Token enforcement uses returned usage. Lost/unknown billed tokens and provider-side
  input cost prevent an exact spend cap. Unknown dollar cost is null.
- The queue polls at a fixed interval and claims one turn per worker loop. No sharding,
  fairness by tenant, global rate budgeting, or priority queue is implemented.
- The frozen first migration and destructive downgrade are tested on disposable databases;
  online upgrades of a populated customer database are not.
- Process tests exercise abrupt exits at two controlled boundaries, not disk corruption,
  database power loss, arbitrary instruction-level kills, or network partition chaos.
- The admission benchmark excludes TCP/TLS/model/tool work and is not production capacity.
