from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain import JSON, State


class Base(DeclarativeBase):
    pass


class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (
        CheckConstraint(
            "state IN (" + ",".join(repr(s.value) for s in State) + ")", name="execution_state"
        ),
        CheckConstraint(
            "steps >= 0 AND model_calls >= 0 AND tokens_used >= 0 AND retry_count >= 0",
            name="nonnegative_budgets",
        ),
        CheckConstraint(
            "(lease_token IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL)"
            " OR (lease_token IS NOT NULL AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="complete_lease",
        ),
        CheckConstraint(
            "state NOT IN ('SUCCEEDED','FAILED_PERMANENT','CANCELLED') OR "
            "(outcome IS NOT NULL AND jsonb_typeof(outcome) = 'object')",
            name="terminal_outcome",
        ),
        CheckConstraint(
            "state NOT IN ('RUNNING','TOOL_EXECUTING') OR lease_token IS NOT NULL",
            name="active_requires_lease",
        ),
        Index("ix_execution_ready", "state", "next_attempt_at"),
        Index("ix_execution_stale", "state", "lease_expires_at"),
        Index("ix_execution_tenant_created", "tenant_id", "created_at"),
        UniqueConstraint("tenant_id", "business_key", name="uq_tenant_business"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_tenant_idempotency"),
        CheckConstraint("tenant_id ~ '^[A-Za-z0-9_.-]{1,64}$'", name="valid_tenant_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), server_default="legacy")
    business_key: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_digest: Mapped[str] = mapped_column(String(64))
    prompt: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32))
    correlation_id: Mapped[UUID]
    max_steps: Mapped[int] = mapped_column(Integer)
    max_tokens: Mapped[int] = mapped_column(Integer)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None]
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[JSON | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint("execution_id", "ordinal", name="uq_call_ordinal"),
        UniqueConstraint("execution_id", "fingerprint", name="uq_call_fingerprint"),
        CheckConstraint(
            "status IN ('PROPOSED','DISPATCHED','SUCCEEDED','FAILED')", name="call_status"
        ),
        CheckConstraint("ordinal > 0 AND attempt >= 0", name="call_counters"),
        CheckConstraint(
            "status != 'SUCCEEDED' OR (external_id IS NOT NULL AND outcome IS NOT NULL "
            "AND jsonb_typeof(outcome) = 'object')",
            name="successful_call_outcome",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    execution_id: Mapped[UUID] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    tool: Mapped[str] = mapped_column(String(80))
    fingerprint: Mapped[str] = mapped_column(String(64))
    operation_key: Mapped[str] = mapped_column(String(64), unique=True)
    arguments: Mapped[JSON] = mapped_column(JSONB)
    side_effect: Mapped[str] = mapped_column(String(24))
    approval_required: Mapped[bool]
    status: Mapped[str] = mapped_column(String(16))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[JSON | None] = mapped_column(JSONB)
    external_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "(decision IS NULL AND actor IS NULL AND decided_at IS NULL) OR "
            "(decision IS NOT NULL AND actor IS NOT NULL AND decided_at IS NOT NULL)",
            name="complete_approval_decision",
        ),
    )
    call_id: Mapped[UUID] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="CASCADE"), primary_key=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    decision: Mapped[bool | None]
    actor: Mapped[str | None] = mapped_column(String(80))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "execution_events"
    __table_args__ = (Index("ix_event_history", "execution_id", "id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[UUID] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    kind: Mapped[str] = mapped_column(String(80))
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32))
    tool_call_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="SET NULL")
    )
    details: Mapped[JSON] = mapped_column(JSONB, default=dict)


class WebhookReceipt(Base):
    __tablename__ = "webhook_receipts"
    __table_args__ = (Index("ix_receipt_expiry", "expires_at"),)
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True, server_default="legacy")
    nonce_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Reconciliation(Base):
    __tablename__ = "reconciliations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('AMBIGUOUS','RECONCILING','MANUAL_REVIEW','RESOLVED','ABANDONED')",
            name="reconciliation_status",
        ),
        CheckConstraint("attempts >= 0 AND budget > 0", name="reconciliation_budget"),
        Index("ix_reconciliation_due", "status", "next_attempt_at"),
    )
    call_id: Mapped[UUID] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(24))
    attempts: Mapped[int] = mapped_column(Integer)
    budget: Mapped[int] = mapped_column(Integer)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
