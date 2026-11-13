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

    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int,
        requests_per_minute: int,
        role_bearers: tuple[tuple[str, bytes], ...] = (),
    ) -> None:
        self.app, self.max_bytes = app, max_bytes
        self.capacity = requests_per_minute
        self.tokens = float(requests_per_minute)
        # Unknown/invalid credentials cannot consume the entire shared quota.
        # Keep the global bound unchanged; reserve 75% for role-matched bearer
        # credentials. A reverse proxy still must enforce distributed limits.
        self.untrusted_capacity = max(1, requests_per_minute // 4)
        self.untrusted_tokens = float(self.untrusted_capacity)
        self.role_bearers = role_bearers
        self.last = time.monotonic()

    def _trusted_bearer(self, scope: Scope) -> bool:
        path = scope.get("path", "")
        if path == "/process-metrics":
            role = "process"
        elif path.startswith("/approvals/"):
            role = "approval"
        elif path.startswith("/executions/") and path.endswith("/operator"):
            role = "operator"
        elif path == "/metrics" or path == "/executions" or path.startswith("/executions/"):
            role = "api"
        else:
            return False

        auth_headers = [value for key, value in scope.get("headers", []) if key.lower() == b"authorization"]
        if len(auth_headers) != 1 or len(auth_headers[0]) > 8192:
            return False
        supplied = auth_headers[0]
        matched = False
        # Compare every configured key of the role, rather than trusting a
        # bearer-shaped header or a key belonging to a different role.
        for configured_role, expected in self.role_bearers:
            if configured_role == role and hmac.compare_digest(supplied, expected):
                matched = True
        return matched

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(scope.get("state", {}).get("request_id", "unassigned"))
        now = time.monotonic()
        elapsed = max(0.0, now - self.last)
        self.tokens = min(
            float(self.capacity), self.tokens + elapsed * self.capacity / 60
        )
        self.untrusted_tokens = min(
            float(self.untrusted_capacity),
            self.untrusted_tokens + elapsed * self.untrusted_capacity / 60,
        )
        self.last = now
        # Only the cheap, database-free liveness endpoint is exempt. /ready
        # performs PostgreSQL queries and must not be a free flood target.
        if scope["path"] != "/health":
            trusted = self._trusted_bearer(scope)
            if self.tokens < 1 or (not trusted and self.untrusted_tokens < 1):
                await JSONResponse(
                    {"error": {"code": "ingress_rate_limited", "request_id": request_id}},
                    status_code=429,
                    headers={"Retry-After": str(max(1, 60 // self.capacity))},
                )(scope, receive, send)
                return
            self.tokens -= 1
            if not trusted:
                self.untrusted_tokens -= 1
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
