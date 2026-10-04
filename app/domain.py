from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

type JSON = dict[str, JsonValue]


def fingerprint(value: JSON) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class State(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    TOOL_EXECUTING = "TOOL_EXECUTING"
    RETRY_PENDING = "RETRY_PENDING"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    SUCCEEDED = "SUCCEEDED"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    CANCELLED = "CANCELLED"


TERMINAL = frozenset({State.SUCCEEDED, State.FAILED_PERMANENT, State.CANCELLED})
TRANSITIONS: dict[State, frozenset[State]] = {
    State.CREATED: frozenset({State.RUNNING, State.CANCELLED, State.FAILED_PERMANENT}),
    State.RUNNING: frozenset(
        {
            State.WAITING_FOR_APPROVAL,
            State.TOOL_EXECUTING,
            State.RETRY_PENDING,
            State.SUCCEEDED,
            State.FAILED_PERMANENT,
        }
    ),
    State.WAITING_FOR_APPROVAL: frozenset({State.CREATED, State.CANCELLED, State.FAILED_PERMANENT}),
    State.TOOL_EXECUTING: frozenset(
        {State.CREATED, State.RETRY_PENDING, State.RECONCILIATION_REQUIRED, State.FAILED_PERMANENT}
    ),
    State.RETRY_PENDING: frozenset({State.RUNNING, State.CANCELLED, State.FAILED_PERMANENT}),
    State.RECONCILIATION_REQUIRED: frozenset({State.CREATED}),
    State.SUCCEEDED: frozenset(),
    State.FAILED_PERMANENT: frozenset(),
    State.CANCELLED: frozenset(),
}


class Fault(Exception):
    """Stable, safe error codes. Never put provider bodies or credentials in messages."""

    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class ExternalFault(Fault):
    def __init__(
        self,
        code: str,
        *,
        transient: bool = False,
        ambiguous: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(code, 502)
        self.transient = transient
        self.ambiguous = ambiguous
        self.retry_after = retry_after


def check_transition(source: State, target: State) -> None:
    if target not in TRANSITIONS[source]:
        raise Fault("illegal_transition")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateExecution(Contract):
    business_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    prompt: str = Field(min_length=1, max_length=8000)
    max_steps: int = Field(default=4, ge=1, le=8)
    max_tokens: int = Field(default=4096, ge=32, le=32768)
    deadline_seconds: int = Field(default=1800, ge=60, le=86400)


class Decision(Contract):
    kind: Literal["tool", "finish", "refuse"]
    tool: str | None = Field(default=None, max_length=80)
    arguments: JSON = Field(default_factory=dict)
    text: str | None = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.kind == "tool":
            if not self.tool or self.text is not None:
                raise ValueError("tool decision requires only a tool and arguments")
        elif self.tool is not None or self.arguments or not self.text:
            raise ValueError("terminal decision requires only nonempty text")
        return self


class Usage(Contract):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    # Cost is supplied only if independently known, never guessed from token counts.
    cost_usd: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")


class ModelTurn(Contract):
    decision: Decision
    usage: Usage


class ToolResult(Contract):
    operation_key: str = Field(min_length=64, max_length=64)
    fingerprint: str = Field(min_length=64, max_length=64)
    external_id: str = Field(min_length=1, max_length=128)
    value: str = Field(max_length=4000)


class ReconcileResult(Contract):
    status: Literal["found", "absent", "unknown"]
    result: ToolResult | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.status == "found") != (self.result is not None):
            raise ValueError("found requires a result; absence must not contain one")
        return self


class ApprovalDecision(Contract):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    approve: bool


class ActionView(Contract):
    id: UUID
    tool: str
    fingerprint: str
    arguments: JSON
    status: str
    approval_expires_at: datetime | None


class ExecutionView(Contract):
    id: UUID
    business_key: str
    state: State
    correlation_id: UUID
    model_calls: int
    steps: int
    tokens_used: int
    retry_count: int
    next_attempt_at: datetime | None
    deadline_at: datetime
    outcome: JSON | None
    error_code: str | None
    action: ActionView | None


class EventView(Contract):
    id: int
    timestamp: datetime
    kind: str
    from_state: str | None
    to_state: str
    tool_call_id: UUID | None
    metadata: JSON


class HistoryView(Contract):
    items: list[EventView]
    next_cursor: int | None


@dataclass(frozen=True)
class Lease:
    execution_id: UUID
    token: UUID
    owner: str
    correlation_id: UUID


@dataclass(frozen=True)
class Work:
    lease: Lease
    prompt: str
    remaining_tokens: int
    observations: tuple[JSON, ...]
    pending_call_id: UUID | None


@dataclass(frozen=True)
class Dispatch:
    call_id: UUID
    tool: str
    arguments: JSON
    fingerprint: str
    operation_key: str
    attempt: int
