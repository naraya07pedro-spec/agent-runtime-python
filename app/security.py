import hashlib
import hmac
import re
import time
from datetime import UTC, datetime

from pydantic import SecretStr
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.domain import Fault


def authenticate(header: str | None, secret: SecretStr | None) -> None:
    if secret is None:
        raise Fault("authentication_not_configured", 503)
    expected = "Bearer " + secret.get_secret_value()
    if header is None or not hmac.compare_digest(header.encode(), expected.encode()):
        raise Fault("unauthorized", 401)


def verify_webhook(
    body: bytes,
    timestamp: str | None,
    nonce: str | None,
    signature: str | None,
    secret: SecretStr | None,
    now: datetime | None = None,
) -> str:
    if secret is None:
        raise Fault("webhook_not_configured", 503)
    if (
        not timestamp
        or not re.fullmatch(r"\d{10}", timestamp)
        or not nonce
        or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", nonce)
    ):
        raise Fault("webhook_signature_invalid", 401)
    if abs((now or datetime.now(UTC)).timestamp() - int(timestamp)) > 300:
        raise Fault("webhook_timestamp_expired", 401)
    signed = timestamp.encode() + b"." + nonce.encode() + b"." + body
    expected = (
        "sha256=" + hmac.new(secret.get_secret_value().encode(), signed, hashlib.sha256).hexdigest()
    )
    if not signature or not hmac.compare_digest(signature.encode(), expected.encode()):
        raise Fault("webhook_signature_invalid", 401)
    return hashlib.sha256(nonce.encode()).hexdigest()


class IngressLimits:
    """Single-process ingress bound; a gateway must enforce distributed quotas."""

    def __init__(self, app: ASGIApp, max_bytes: int, requests_per_minute: int) -> None:
        self.app, self.max_bytes = app, max_bytes
        self.capacity = requests_per_minute
        self.tokens = float(requests_per_minute)
        self.last = time.monotonic()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(scope.get("state", {}).get("request_id", "unassigned"))
        now = time.monotonic()
        self.tokens = min(
            float(self.capacity), self.tokens + (now - self.last) * self.capacity / 60
        )
        self.last = now
        if scope["path"] not in {"/health", "/ready"}:
            if self.tokens < 1:
                await JSONResponse(
                    {"error": {"code": "ingress_rate_limited", "request_id": request_id}},
                    status_code=429,
                    headers={"Retry-After": str(max(1, 60 // self.capacity))},
                )(scope, receive, send)
                return
            self.tokens -= 1
        data = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            data.extend(message.get("body", b""))
            if len(data) > self.max_bytes:
                await JSONResponse(
                    {"error": {"code": "request_too_large", "request_id": request_id}},
                    status_code=413,
                )(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def buffered_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(data), "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)
