# Admission benchmark

Generated 2026-10-04T18:21:26.230903+00:00 on Linux-6.17.0-1022-azure-x86_64-with-glibc2.39, Python 3.12.14.

Tested commit: `876d507b25c94f03dfd144d2f24d431e5d812841`. Source commit: `b7c4c9b3806d3b43cc02b6528a9e8879ec775161`.

Scope: POST /executions admission only. in-process ASGI; excludes TCP/TLS, LLM and external tool calls.

Database: PostgreSQL 17.11 (Debian 17.11-1.pgdg13+2) on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit. CPU count reported by runner: 4.

Requests: 300; concurrency: 10; warm-up: 20.

p50: 58.31 ms; p95: 103.65 ms; p99: 281.35 ms.

Throughput: 145.99 admissions/s; errors: 0; failures: {}.

This is one local CI-runner measurement, **not production capacity**. Profiling ran before the timed sample; see `admission-profile.txt` and the query plan in `benchmark-results.json`.
