# Observability and aggregation boundaries

Transactional execution events are the authoritative runtime history. Logs carry fixed
JSON event names, request/correlation/execution/action IDs and allowlisted scalar metadata;
raw prompts, provider bodies, exception repr and credentials are omitted. Validation errors
hide input values; SQLAlchemy hides parameter values. This is redaction, not data encryption.
Events include tenant-scoped approval credential IDs, recovery attempts and operator actions.
No tamper-proof external audit archive or OpenTelemetry traces are implemented.

| Surface | Metric / meaning | Aggregation rule |
|---|---|---|
| API `/metrics`, tenant API key | runtime_executions by state, runtime_durable_dispatches from DB events | Already covers all workers for that tenant; select one canonical scrape or deduplicate identical replica gauges |
| API `/process-metrics`, legacy infrastructure operator key | HTTP responses and process-local runtime counters/histograms | Scrape every API process directly; a load-balanced endpoint can miss processes |
| Worker loopback exporter, RUNTIME_WORKER_METRICS_PORT | Advances, tool dispatches/duration, retries, reconciliation outcomes and classified errors in that worker | Scrape every worker; keep instance labels and sum rates/histogram buckets externally |

The worker exporter defaults off; Compose enables port 9101 inside the worker on loopback.
It is not published publicly. Scrape from a same-network-namespace collector or expose only
on an explicitly protected private monitoring network. API process metrics use separate
infrastructure authority; custom tenant API/operator keys cannot read cross-tenant process
activity. One application process per scrape target avoids ambiguous multi-worker HTTP
routing. Client multiprocess mode is not silently enabled and no deployed collector is claimed.

For Prometheus process-counter aggregation, an example is
`sum by (tool) (rate(runtime_tool_calls_total[5m]))` across distinct API/worker targets.
Histogram bucket rates must also be summed before quantile computation. Counter resets are
expected on restart. Never sum runtime_executions/runtime_durable_dispatches across multiple
replicas of the same tenant/database; that multiplies the same fact. Dispatch event counts
represent retained intent, including uncertain operations, not successful external effects.
DB retention/deletion/restore can reduce those gauges.

Do not label metrics with execution/business/customer IDs, prompts, URLs or secrets. Suggested
operator signals include manual-review count/age, oldest due work, stale leases, 429/503 rates
and dependency saturation. Alert thresholds and SLOs need a real workload study. Correlation
IDs aid diagnosis but do not constitute end-to-end trace coverage.

`/health` proves process liveness only. `/ready` checks DB access, revision 0002, auth/provider
configuration and tool endpoint policy; read-only restore mode returns unavailable. Neither
endpoint makes a paid model call or proves provider availability. Full database disconnects
are sanitized as unavailability, including compound IPv4/IPv6 connection refusals.
