# Tenant identity and authorization

The compatibility tenant is `legacy`. Existing API/approval keys and execution identities
remain bound to it. Migration 0002 adds tenant-scoped uniqueness without changing existing
legacy operation keys. Child tool calls, approvals, reconciliation rows and events inherit
their tenant through the execution foreign key; the API never authorizes from a child ID
alone. This is application isolation, not PostgreSQL RLS or separate databases.

`RUNTIME_TENANTS` supplies configured tenant IDs, per-role credentials, tool allowlists,
webhook secrets and optional provider repository. See the deny-all [configuration
template](../config/tenants.example.json). Inject real credentials externally. A credential
record contains a non-personal audit `id` and secret `key`; API, approval and operator roles
are distinct. No key or webhook secret can be shared across tenants/roles. Credential
arrays are excluded from model serialization and validation errors hide input values.

Bearer identity resolves on the server; a supplied X-Tenant-ID has no authority. A tenant
API credential can admit/read/advance/cancel/lookup its own executions. Approval credentials
can decide only that tenant's pending approvals. Operator credentials can lookup/abandon
only its uncertain executions. Cross-tenant/nonexistent IDs return the same 404 class.
The legacy infrastructure operator key separately protects process-wide metrics; custom
tenant operator keys cannot read those aggregate process counters.

Webhook `/webhooks/{tenant_id}` selects a server-configured secret; the signature binds
the exact body, timestamp and nonce. `/webhooks` remains the legacy route. Replay receipts
are unique per tenant. Identical business keys across tenants create independent identities.
Tenant admission capacity and a sliding 60-second quota serialize through a PostgreSQL
advisory lock, so API processes share the limit. Identical duplicates bypass capacity/rate
quota and conflicting input remains 409. Limits return 429 and Retry-After; DB unavailability
does not silently grant admission. These are admission limits, not a distributed quota
for every HTTP route or upstream provider account.

Workers are privileged global schedulers. Every claimed lease and dispatch carries a
tenant; store writes verify that tenant with token/owner/expiry under a row lock. The
claimed tenant's current tool policy is checked before proposal/dispatch. A caller cannot
choose an external provider URL/repository. Stable tenant IDs and provider bindings are
operational invariants; deleting/reassigning them does not transfer pending authority.

For rotation, add a new credential with a distinct audit ID to the same role, deploy to
all API processes, verify cutover, then remove the old key everywhere. An overlap window
accepts both; removal rejects the old key. Restart/reload is required for real processes;
revocation is not pushed dynamically. Webhook secret arrays support the same bounded
overlap. Rotate a GitHub token while preserving repository/actor; changing the target
invalidates pending action authority and prevents lookup until its original binding is
restored. No token rotation is performed against a real external account in these tests.

Remaining boundaries: no OIDC/SSO, individual human verification, four-eyes approvals,
RLS, automatic provisioning, per-provider tenant credentials, or fairness guarantees.
All tenants share the same trusted service/database administrators. A malicious DBA or
compromised worker is outside the tested application-credential isolation boundary.
