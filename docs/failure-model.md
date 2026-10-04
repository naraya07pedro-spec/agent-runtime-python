# Failure model and executable scenarios

The system assumes process death, delayed packets, unreliable HTTP responses, and
transaction failures. A timeout says the caller stopped waiting; it does not say
the receiver did nothing.

| Scenario | Expected durable path | Executable proof |
|---|---|---|
| Concurrent duplicate delivery | One CREATED identity; other callers replay | `tests/concurrency/test_ownership.py` |
| Worker dies after claim | RUNNING → RETRY_PENDING after expiry; new token | Ownership and recovery tests |
| DB fails before dispatch commit | No external call; claim later recovered | `test_database_failure_before_dispatch_prevents_external_effect` |
| External success, final DB commit fails | TOOL_EXECUTING → RECONCILIATION_REQUIRED | `test_external_success_plus_failed_commit_is_reconciled_without_replay` |
| DB also rejects reconciliation write | DISPATCHED intent survives; expiry recovers | `test_database_remains_down_after_effect_then_restart_recovers_durable_intent` |
| Real process exits after external success | External effect exists once; new process reconciles | `tests/failure_injection/test_process_death.py` |
| Approval payload changes | FAILED_PERMANENT before external I/O | Approval mutation tests |
| Model 429 then success | RETRY_PENDING until Retry-After; eventual finish | Runtime retry tests |
| Unknown or unauthorized model tool | FAILED_PERMANENT; zero effects | Security and eval suites |
| Lookup says absent while old worker is delayed | Stay in reconciliation; no new dispatch | Late-send concurrency test |

`ExternalFault` distinguishes configuration, authorization, invalid requests,
rate limiting, transport uncertainty, and malformed responses. For model/read
operations, only classified transient failures receive bounded exponential backoff
with jitter. Retry-After is never shortened; a delay exceeding the execution
deadline fails instead of silently retrying earlier.

All errors **after write dispatch** are handled conservatively as reconciliation,
including 400 and 429. A stronger provider contract could prove some responses mean
no effect, but this generic adapter does not assume that contract.

Fault injection is constructor-injected into the store; no HTTP endpoint or
production environment switch enables faults. Hooks cover before dispatch commit,
after durable dispatch, after external success, before outcome commit, and before
reconciliation commit. Tests assert persisted state and provider effects, not only
raised exceptions. Process-crash fixtures deliberately bypass normal cleanup.
