import json
import logging
from datetime import UTC, datetime
from itertools import product

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.config import Settings
from app.domain import (
    TERMINAL,
    TRANSITIONS,
    Decision,
    ExternalFault,
    Fault,
    State,
    check_transition,
    fingerprint,
)
from app.observability import SafeJsonFormatter
from app.reliability import backoff, check_response, retry_after
from app.store import action_digest
from app.tools import Registry


@pytest.mark.parametrize("source,target", list(product(State, State)))
def test_state_graph_has_no_implicit_edges(source, target):
    if target in TRANSITIONS[source]:
        check_transition(source, target)
    else:
        with pytest.raises(Fault, match="illegal_transition"):
            check_transition(source, target)


@given(st.sampled_from(tuple(TERMINAL)), st.sampled_from(tuple(State)))
def test_terminal_state_cannot_be_reactivated(source, target):
    with pytest.raises(Fault):
        check_transition(source, target)


@given(st.dictionaries(st.text(min_size=1, max_size=10), st.integers(), max_size=10))
def test_fingerprint_is_independent_of_key_order(payload):
    assert fingerprint(payload) == fingerprint(dict(reversed(list(payload.items()))))


def test_action_fingerprint_binds_policy_and_payload():
    baseline = action_digest("a", {"x": 1}, "read", False)
    assert (
        len(
            {
                baseline,
                action_digest("b", {"x": 1}, "read", False),
                action_digest("a", {"x": 2}, "read", False),
                action_digest("a", {"x": 1}, "irreversible", False),
                action_digest("a", {"x": 1}, "read", True),
            }
        )
        == 5
    )


@pytest.mark.parametrize(
    "status,code,transient",
    [
        (400, "provider_invalid_request", False),
        (401, "provider_authorization", False),
        (403, "provider_authorization", False),
        (429, "rate_limited", True),
        (500, "provider_unavailable", True),
        (503, "provider_unavailable", True),
        (302, "provider_redirect_denied", False),
        (408, "provider_unavailable", True),
    ],
)
def test_failure_classification(status, code, transient):
    with pytest.raises(ExternalFault) as caught:
        check_response(httpx.Response(status, headers={"Retry-After": "7"}))
    assert caught.value.code == code
    assert caught.value.transient is transient
    if status == 429:
        assert caught.value.retry_after == 7


@pytest.mark.parametrize(
    "header,expected",
    [
        (None, None),
        ("oops", None),
        ("NaN", None),
        ("Infinity", None),
        ("-1", 0),
        ("9", 9),
        ("Thu, 01 Jan 2026 00:00:10 GMT", 10),
    ],
)
def test_retry_after(header, expected):
    assert retry_after(header, datetime(2026, 1, 1, tzinfo=UTC)) == expected


def test_backoff_honors_provider_minimum_and_is_bounded():
    assert backoff(0, 1, jitter=0) == 0.5
    assert backoff(3, 1, jitter=1) == 8
    assert backoff(100, 2, jitter=1) == 60
    assert backoff(0, 1, suggested=3600) == 3600


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "tool"},
        {"kind": "tool", "tool": "x", "text": "override"},
        {"kind": "finish", "text": "done", "tool": "x"},
        {"kind": "refuse", "text": ""},
        {"kind": "tool", "tool": "x", "arguments": {}, "approved": True},
        {"kind": "tool", "tool": "x", "arguments": "arbitrary prose"},
    ],
)
def test_malformed_decisions_are_rejected(payload):
    with pytest.raises(ValidationError):
        Decision.model_validate(payload)


def test_unknown_and_unauthorized_tools_fail_closed():
    registry = Registry(frozenset({"lookup_customer"}))
    with pytest.raises(Fault, match="unknown_tool"):
        registry.get("shell")
    with pytest.raises(Fault, match="tool_not_allowed"):
        registry.get("refund_payment")
    with pytest.raises(ValueError):
        Registry(frozenset({"shell"}))


@pytest.mark.parametrize(
    "arguments",
    [
        {"customer_id": "abc", "url": "http://127.0.0.1"},
        {"customer_id": "../../etc/passwd"},
        {"customer_id": 123},
    ],
)
def test_tool_schema_blocks_extra_fields_path_injection_and_coercion(arguments):
    with pytest.raises(Fault, match="tool_arguments_invalid"):
        Registry(frozenset({"lookup_customer"})).get("lookup_customer").validate(arguments)


def test_normal_logging_discards_payloads_and_exception_messages():
    record = logging.LogRecord(
        "runtime",
        logging.ERROR,
        "test",
        1,
        "Bearer secret",
        (),
        (ValueError, ValueError("secret-password"), None),
    )
    record.prompt = "private customer data"
    record.authorization = "Bearer token"
    record.request_id = "id-123"
    formatted = SafeJsonFormatter().format(record)
    assert "secret" not in formatted and "private" not in formatted and "token" not in formatted
    assert json.loads(formatted)["request_id"] == "id-123"


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(api_key="short"),
        dict(api_key="x" * 32, approval_key="x" * 32),
        dict(lease_seconds=10),
        dict(webhook_secret="short"),
    ],
)
def test_unsafe_configuration_rejected(kwargs):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254",
        "file:///etc/passwd",
        "https://user:password@example.com",
        "https://example.com/?token=secret",
    ],
)
def test_tool_endpoint_preflight(url):
    settings = Settings(_env_file=None, tool_base_url=url, tool_api_token="test")
    with pytest.raises(ExternalFault):
        settings.validate_tool_endpoint()
