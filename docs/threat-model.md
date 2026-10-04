# Threat model

The deployment boundary is one trusted operator, one PostgreSQL database, and an
administrator-selected provider. API callers and all model/provider payloads are
untrusted. Database administrators, host administrators, and the configured provider
are outside the adversary model; their compromise defeats these guarantees.

| Boundary / attack | Enforced control | Evidence / residual risk |
|---|---|---|
| Unauthenticated execution or history access | Constant-time bearer check; missing key fails closed | Security suite; a shared key is not tenant isolation |
| Caller self-approves a refund | Distinct approval credential; model cannot supply approval fields | API credential separation and forged-approval eval |
| Approval reused after mutation or expiry | Recompute action digest at decision and dispatch; DB-time expiry | Integration mutation/expiry tests; actor is a key holder, not a named identity |
| Forged or repeated webhook | HMAC-SHA256 over timestamp, nonce, exact body; ±300s window; durable nonce uniqueness | Concurrent replay and receipt rollback test; clocks must be synchronized |
| Prompt injection invents tool authority | Strict decision contract, default-deny registry, typed arguments, policy recheck | Security and contract evals; permitted but harmful arguments still need business validation |
| Model requests arbitrary outbound URL | No URL-valued tool input; fixed administrator endpoint; HTTPS except explicit local sandbox | Endpoint tests; no DNS pinning or network egress firewall in this reference |
| Redirect or proxy leaks token | Redirects off; `trust_env=False`; fixed OpenAI origin | HTTP boundary tests; host compromise remains out of scope |
| Oversized ingress or upstream response | Streamed upstream body cap, ingress body cap including chunks, timeouts, finite connection pool | HTTP and security tests; slow clients need a gateway connection/header timeout |
| Secret or prompt appears in logs | Fixed event names and flat metadata allowlist; no exception repr or request bodies | Formatter tests; database intentionally stores prompts/arguments/results |
| Duplicate side effect through racing workers | Durable dispatch intent, row locks, leases, no replay of uncertain writes | Concurrency and process-death tests; remote systems do not enforce our lease |
| Corrupt terminal state | SQL CHECK constraints require JSON objects, complete lease and approval fields | Direct SQL constraint tests; privileged SQL can still bypass application transitions |
| Vulnerable dependency / CI privilege | Locked versions; application dependency audit; pinned action commits; read-only workflow token | CI; this is not an image/OS penetration test or supply-chain attestation |

The default allowlist enables only `lookup_customer`; the explicit local demo enables
all four **sandbox** tools. A tool's permission string documents its capability;
actual authorization is the server tool allowlist, not a user-provided role string.

Secrets belong in deployment-managed configuration. `.env` is ignored and bootstrap
creates it with mode 0600. Rotate API, approval, webhook, and provider credentials
separately. There is no zero-downtime dual-key rotation protocol. Public schema and
liveness endpoints intentionally require no authentication; execution data and metrics
do. Put TLS, egress rules, per-principal quotas, and a real identity system in front
of any deployment handling sensitive data. See [limitations](limitations.md).
