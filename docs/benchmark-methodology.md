# Admission benchmark methodology

Run `uv run --locked python -m benchmarks.admission` with `TEST_DATABASE_URL` pointing
to a disposable PostgreSQL database ending in `_test`. Apply migrations first. The harness appends uniquely keyed benchmark rows and does
not truncate existing data; use a fresh database for comparable samples. It calls the actual FastAPI admission path through HTTPX ASGI transport and
performs durable PostgreSQL transactions. SQLite, mocked stores, and fake timings
are not used.

The fixed workload is 20 warm-up requests followed by 300 unique admissions, with a
semaphore limiting concurrency to 10. Prompts do not trigger model/tool execution
because this measures admission only. Every request receives a unique business and
idempotency key. The clock is monotonic `perf_counter`; the recorded per-request
sample includes waiting within the active admission operation, and total elapsed
wall time produces attempted admissions/second; a passing run has zero failures. The harness records errors and
failure types rather than dropping them silently.

A separate cProfile warm-up precedes the timed measurement; profiling overhead is
not included in reported latency. `admission-profile.txt` contains the top cumulative
call costs. `benchmark-results.json` includes an EXPLAIN ANALYZE of the claim query,
Python/platform/CPU count, PostgreSQL version, workload, p50/p95/p99, elapsed time,
throughput, error count, commit, and CI run ID. Percentiles are computed by linear
interpolation over sorted samples. See [the executable harness](../benchmarks/admission.py).

This is **one CI-runner admission microbenchmark**, not agent completion throughput
or production capacity. It excludes TCP/TLS, gateway overhead, real LLM latency/cost,
tool/provider latency, sustained queue processing, retention growth, and noisy-neighbor
variation. The small sample gives a rough p99, not a statistically stable tail estimate.
No baseline improvement is claimed because no comparable before/after trial was run.

Before making a deployment capacity claim, run repeated longer trials over TCP against
an isolated environment, vary worker/connection counts and dataset size, report confidence
and saturation behavior, and include full model/tool workflows. Establish the SLO before
optimizing. A queue plan on a few hundred rows does not justify a new index for millions
of rows; measure realistic due/stale distributions first.
