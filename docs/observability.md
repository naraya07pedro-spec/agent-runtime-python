# Observability and its limits

The authoritative audit trail is `execution_events`, inserted in the same transaction
as each state change. Inspect `GET /executions/{id}` and
`GET /executions/{id}/events?cursor=0&limit=100` with the API credential. History uses
keyset pagination; follow `next_cursor` until null. Events cover admission, ownership,
model call/usage, proposals, approval, dispatch, outcome, retry, recovery, and reconciliation.

Each HTTP request gets a new `X-Request-ID`. A valid UUID `X-Correlation-ID` can be
provided by the caller; otherwise one is generated. Admission persists that correlation
ID, which follows subsequent worker logs. A later HTTP request has its own request
correlation; use execution ID to join its access log to the original execution.

JSON logs include timestamp, level, a fixed event name, and allowlisted fields:
request/correlation/execution/action IDs, tool, attempt, state/transition, provider,
duration, classified error, and outcome. Fields not relevant to an event are null.
Not every transition is duplicated into logs; durable events are the complete state
history. Model usage events contain reported input/output tokens and nullable cost.
Unknown cost remains null rather than being presented as zero.

| Metric | Meaning / caution |
|---|---|
| `runtime_requests_total{status}` | HTTP responses observed by correlation middleware; early validation/unknown exceptions are not a complete access-log census |
| `runtime_advances_total` | Claimed turns in the current process |
| `runtime_tool_calls_total{tool}` | Dispatches observed in the current process |
| `runtime_tool_call_duration_seconds{tool}` | Outbound tool I/O histogram |
| `runtime_retry_total` | Retry scheduling paths observed in the current process |
| `runtime_reconciliation_total{outcome}` | Lookup results: found, absent, or unknown |
| `runtime_errors_total` | Classified external failures, not every application error |
| `runtime_executions{state}` | Current durable DB counts, queried at scrape time |

`GET /metrics` requires the API credential. Counters/histograms are **process-local**:
a Compose API scrape does not aggregate the separate worker's counters. DB state
gauges and execution history include worker effects. A production deployment needs
worker metric export and multi-process aggregation before setting rate-based SLOs.
Avoid execution IDs, prompts, customer IDs, or business keys as metric labels.

`/health` proves only process liveness. `/ready` checks DB access and migration revision,
required API/approval configuration, selected model credentials, and configured tool
endpoint policy. It does not send a billable model call or prove provider availability.

Suggested operator signals, not claimed implemented alert rules: age/count of unresolved
reconciliations, due work age, stale leases, 5xx rate, and dependency saturation. Set
thresholds from a workload/SLO study; this repository has no measured production SLO.
