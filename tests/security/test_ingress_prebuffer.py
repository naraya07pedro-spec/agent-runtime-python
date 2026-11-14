"""Negative-path tests for the ingress pre-buffer size and advertised-length boundary."""

import pytest
from starlette.types import Message, Scope

from app.security import IngressLimits

pytestmark = pytest.mark.security


async def exercise(
    *,
    advertised_length: bytes | None,
    messages: list[Message],
    max_bytes: int = 1024,
) -> tuple[list[Message], list[Message], int]:
    """Call the ASGI middleware directly without a real database or server."""
    observed: list[Message] = []
    outbound: list[Message] = []
    reads = 0

    async def downstream(_scope: Scope, receive, send) -> None:
        observed.append(await receive())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> Message:
        nonlocal reads
        if reads >= len(messages):
            raise AssertionError("Middleware read more chunks than supplied")
        message = messages[reads]
        reads += 1
        return message

    async def send(message: Message) -> None:
        outbound.append(message)

    headers = (
        [(b"content-length", advertised_length)] if advertised_length is not None else []
    )
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/webhooks",
        "headers": headers,
    }
    limiter = IngressLimits(downstream, max_bytes=max_bytes, requests_per_minute=60)
    await limiter(scope, receive, send)
    return observed, outbound, reads


async def test_declared_oversize_is_rejected_without_reading_body():
    observed, outbound, reads = await exercise(
        advertised_length=b"1025",
        messages=[{"type": "http.request", "body": b"small", "more_body": False}],
    )
    assert outbound[0]["status"] == 413
    assert reads == 0
    assert observed == []


async def test_false_small_content_length_does_not_bypass_stream_limit():
    observed, outbound, reads = await exercise(
        advertised_length=b"10",
        messages=[
            {"type": "http.request", "body": b"x" * 700, "more_body": True},
            {"type": "http.request", "body": b"y" * 400, "more_body": False},
        ],
    )
    assert outbound[0]["status"] == 413
    assert reads == 2
    assert observed == []


async def test_oversized_single_chunk_is_rejected_before_forwarding():
    observed, outbound, reads = await exercise(
        advertised_length=None,
        messages=[{"type": "http.request", "body": b"x" * 8192, "more_body": False}],
    )
    assert outbound[0]["status"] == 413
    assert reads == 1
    assert observed == []


async def test_exact_boundary_is_forwarded_unchanged():
    observed, outbound, reads = await exercise(
        advertised_length=b"1024",
        messages=[
            {"type": "http.request", "body": b"a" * 1000, "more_body": True},
            {"type": "http.request", "body": b"b" * 24, "more_body": False},
        ],
    )
    assert outbound[0]["status"] == 204
    assert reads == 2
    assert observed == [
        {"type": "http.request", "body": b"a" * 1000 + b"b" * 24, "more_body": False}
    ]
