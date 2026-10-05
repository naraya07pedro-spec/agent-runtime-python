# Admission benchmark

Generated 2026-10-05T04:08:44.593250+00:00 on Linux-6.17.0-1022-azure-x86_64-with-glibc2.39, Python 3.12.14.

Tested commit: `833a4eff040bea8b952e84d9b5207c2335c036c2`. Source commit: `833a4eff040bea8b952e84d9b5207c2335c036c2`.

Scope: POST /executions admission only. in-process ASGI; excludes TCP/TLS, LLM and external tool calls.

Database: PostgreSQL 17.11 (Debian 17.11-1.pgdg13+2) on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit. CPU count reported by runner: 4.

Requests: 300; concurrency: 10; warm-up: 20.

p50: 60.92 ms; p95: 118.12 ms; p99: 188.80 ms.

Throughput: 144.67 admissions/s; errors: 0; failures: {}.

This is one local CI-runner measurement, **not production capacity**. Profiling ran before the timed sample; see `admission-profile.txt` and the query plan in `benchmark-results.json`.
