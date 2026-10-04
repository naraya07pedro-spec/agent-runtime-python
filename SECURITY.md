# Security policy

This is a reference implementation, not a hosted service or a security-certified
product. No release line has a maintenance SLA. Security boundaries and residual
risks are documented in [the threat model](docs/threat-model.md).

Report a suspected vulnerability using the repository's private vulnerability
reporting interface if GitHub offers it. If private reporting is unavailable,
open an issue requesting a private contact without exploit details, credentials,
or personal data. Do not post live secrets in a public issue. No private reporting
channel or response-time guarantee is implied by this document.

For a reproducible report, include the affected commit, trust boundary, expected
behavior, observed behavior, and a minimal sandbox-only test. A failure to resolve
an ambiguous external write is a documented safety tradeoff; a repeated automatic
write or stale authoritative completion is a correctness/security defect.

The demo database password and repeated test keys are intentionally non-production
fixtures. Bootstrap generates fresh API/provider credentials. Never reuse the demo
PostgreSQL credentials on a publicly reachable database.
