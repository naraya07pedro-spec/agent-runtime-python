import json
import logging
from datetime import UTC, datetime
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Histogram

SAFE_FIELDS = frozenset(
    {
        "request_id",
        "correlation_id",
        "execution_id",
        "tool_call_id",
        "tool",
        "attempt",
        "state",
        "transition",
        "duration_ms",
        "provider",
        "error_class",
        "outcome",
    }
)
EVENT_NAMES = frozenset(
    {
        "request_completed",
        "advance_completed",
        "tool_completed",
        "worker_error",
        "database_unavailable",
        "internal_error",
        "reconciliation_completed",
    }
)
logger = logging.getLogger("runtime")


class SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Never format arbitrary messages, exception repr/traceback, or nested payloads.
        event = (
            record.msg
            if isinstance(record.msg, str) and record.msg in EVENT_NAMES
            else "redacted_message"
        )
        values: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": event,
        }
        for key in SAFE_FIELDS:
            value = getattr(record, key, None)
            if value is None or isinstance(value, (str, int, float, bool)):
                values[key] = value
        return json.dumps(values, separators=(",", ":"))


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    for name in ("runtime", "uvicorn.error", "uvicorn"):
        target = logging.getLogger(name)
        target.handlers = [handler]
        target.propagate = False
        target.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "runtime_requests_total", "HTTP responses by status", ["status"], registry=self.registry
        )
        self.advances = Counter(
            "runtime_advances_total", "Claimed runtime turns", registry=self.registry
        )
        self.tools = Counter(
            "runtime_tool_calls_total",
            "Durably dispatched tool requests",
            ["tool"],
            registry=self.registry,
        )
        self.tool_duration = Histogram(
            "runtime_tool_call_duration_seconds",
            "Tool I/O duration",
            ["tool"],
            registry=self.registry,
        )
        self.retries = Counter(
            "runtime_retry_total", "Retry scheduling decisions", registry=self.registry
        )
        self.reconciliations = Counter(
            "runtime_reconciliation_total",
            "Reconciliation lookups",
            ["outcome"],
            registry=self.registry,
        )
        self.errors = Counter(
            "runtime_errors_total", "Classified runtime errors", registry=self.registry
        )
