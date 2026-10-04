from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import Field, ValidationError

from app.config import Settings
from app.domain import JSON, Contract, Dispatch, ExternalFault, Fault, ReconcileResult, ToolResult
from app.reliability import check_response


class CustomerInput(Contract):
    customer_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class TicketInput(CustomerInput):
    summary: str = Field(min_length=1, max_length=500)


class NotificationInput(CustomerInput):
    message: str = Field(min_length=1, max_length=1000)


class RefundInput(CustomerInput):
    payment_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    amount_cents: int = Field(ge=1, le=100000)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[Contract]
    permission: str
    side_effect: Literal["read", "idempotent_write", "irreversible"]
    approval: bool
    timeout_seconds: float = 10.0
    retry_policy: str = "read-only: classified bounded retry; writes: reconcile after dispatch"
    idempotency_strategy: str = "sha256(business_key, action_fingerprint); durable dispatch intent"
    audit_behavior: str = "transactional proposal, dispatch, outcome, approval and recovery events"
    output_model: type[ToolResult] = ToolResult

    def validate(self, arguments: JSON) -> JSON:
        try:
            return self.input_model.model_validate(arguments).model_dump(mode="json")
        except ValidationError as exc:
            raise Fault("tool_arguments_invalid", 422) from exc


class Registry:
    def __init__(self, allowed: frozenset[str], timeout: float = 10) -> None:
        specs = [
            ToolSpec(
                "lookup_customer",
                "Read a sandbox customer record",
                CustomerInput,
                "customer:read",
                "read",
                False,
                timeout,
            ),
            ToolSpec(
                "upsert_ticket",
                "Write a sandbox support ticket",
                TicketInput,
                "ticket:write",
                "idempotent_write",
                False,
                timeout,
            ),
            ToolSpec(
                "send_notification",
                "Send one sandbox notification",
                NotificationInput,
                "notification:send",
                "irreversible",
                True,
                timeout,
            ),
            ToolSpec(
                "refund_payment",
                "Issue one sandbox refund (no real money)",
                RefundInput,
                "payment:refund",
                "irreversible",
                True,
                timeout,
            ),
        ]
        self.specs = {s.name: s for s in specs}
        if allowed - self.specs.keys():
            raise ValueError("configuration includes an unknown tool")
        self.allowed = allowed

    def get(self, name: str) -> ToolSpec:
        if name not in self.specs:
            raise Fault("unknown_tool", 403)
        if name not in self.allowed:
            raise Fault("tool_not_allowed", 403)
        return self.specs[name]

    def schemas(self) -> list[JSON]:
        return [
            {
                "type": "function",
                "name": s.name,
                "description": s.description,
                "parameters": s.input_model.model_json_schema(),
                "strict": True,
            }
            for name, s in self.specs.items()
            if name in self.allowed
        ]


class HttpTools:
    """Fixed administrator-configured endpoint. Model arguments never select a URL."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    def preflight(self) -> None:
        self.settings.validate_tool_endpoint()

    def headers(self) -> dict[str, str]:
        self.preflight()
        assert self.settings.tool_api_token is not None
        return {"Authorization": f"Bearer {self.settings.tool_api_token.get_secret_value()}"}

    async def execute(self, call: Dispatch) -> ToolResult:
        try:
            response = await self.client.post(
                f"{self.settings.tool_base_url.rstrip('/')}/tools/{call.tool}",
                headers={**self.headers(), "Idempotency-Key": call.operation_key},
                json={
                    "arguments": call.arguments,
                    "operation_key": call.operation_key,
                    "fingerprint": call.fingerprint,
                },
                timeout=self.settings.tool_timeout,
            )
            check_response(response)
            result = ToolResult.model_validate_json(response.content)
            if result.operation_key != call.operation_key or result.fingerprint != call.fingerprint:
                raise ExternalFault("tool_result_identity_mismatch", ambiguous=True)
            return result
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ExternalFault("tool_transport_uncertain", transient=True, ambiguous=True) from exc
        except (ValidationError, ValueError) as exc:
            raise ExternalFault("tool_response_invalid", ambiguous=True) from exc

    async def lookup(self, operation_key: str, action_fingerprint: str) -> ReconcileResult:
        try:
            response = await self.client.get(
                f"{self.settings.tool_base_url.rstrip('/')}/operations/{operation_key}",
                headers=self.headers(),
                timeout=self.settings.tool_timeout,
            )
            check_response(response)
            result = ReconcileResult.model_validate_json(response.content)
            if result.result and (
                result.result.operation_key != operation_key
                or result.result.fingerprint != action_fingerprint
            ):
                raise ExternalFault("reconciliation_identity_mismatch", ambiguous=True)
            return result
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ExternalFault("reconciliation_transport", transient=True) from exc
        except (ValidationError, ValueError) as exc:
            raise ExternalFault("reconciliation_response_invalid", ambiguous=True) from exc
