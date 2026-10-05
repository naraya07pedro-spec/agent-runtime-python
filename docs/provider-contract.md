# GitHub Issues provider contract

`create_issue` implements GitHub's native REST issue-creation/list contract. The tool is
disabled by default and always requires persistent approval. The model supplies only
bounded title/body fields. Repository, trusted actor, fixed HTTPS origin, credentials,
version header and operation marker are server owned. Repositories must be distinct per
tenant. The approved fingerprint includes repository/actor binding; a changed target
cannot inherit approval or be used for reconciliation.

Required environment names: `RUNTIME_GITHUB_TOKEN`, `RUNTIME_GITHUB_REPOSITORY` (legacy
tenant), `RUNTIME_GITHUB_ACTOR`. Custom tenant repositories belong in `RUNTIME_TENANTS`.
Provision credentials in a secret manager or deployment environment, never in chat.
Use a dedicated GitHub credential with Issues write access only to the configured test
repository. The optional workflow uses secret `GITHUB_PROVIDER_TEST_TOKEN` and repository
variables `GITHUB_PROVIDER_TEST_REPOSITORY` / `GITHUB_PROVIDER_TEST_ACTOR`.

The adapter sends one POST with native `title`/`body`. A stable operation/fingerprint marker
is appended. GitHub's documented create-issue parameters contain no idempotency-key
contract; consequently this adapter treats POST as non-idempotent and does not send a
misleading idempotency header. This is an inference from the documented contract, not
a claim about undisclosed GitHub internals. Redirects are disabled, decompressed responses
are capped at 64 KiB, and each request has a timeout. Normalized outcome, issue reference
and a validated `X-GitHub-Request-ID` persist; raw provider payloads are not logged/stored.

All failures after dispatch—including timeout, malformed response, 400/403/429/5xx and
failed local persistence—require reconciliation. A Retry-After does not prove POST was
effect-free. Model/read retries retain their separate bounded classification. Live lookup
uses GET only, scans at most three pages of ten recent issues, and never follows an
untrusted Link URL. Exact title/body/marker and trusted author must match, with one match
inside the scanned window. Missing, edited, oversized or multiple matches remain unknown.
This does not prove global uniqueness outside that window or protect against compromise
of the provider/trusted actor. There is no compensation or automatic re-creation.

Default CI runs [synthetic native HTTP contracts](../tests/contract/test_github_provider.py)
and [real-PostgreSQL approval/commit-timeout recovery](../tests/integration/test_github_runtime.py).
These validate the adapter and local recovery policy; they do not establish live
authenticated GitHub write behavior. OpenAI model behavior and billing remain untested.

The separate [live check](../provider_checks/test_github_live.py) skips without explicit
opt-in, a disposable-repository acknowledgement and dedicated configuration. It attempts
one synthetic POST and at most three GETs, with no retry or uncertain cleanup write.
It is never automatically run with connected-account credentials. The manual workflow
is optional; its result must be inspected for skips before claiming a live integration.
If creation times out, preserve its synthetic marker and investigate in the provider;
re-running that test creates a new identity and is not recovery of the old operation.

Primary references: [Issues REST](https://docs.github.com/en/rest/issues/issues),
[REST best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api).
