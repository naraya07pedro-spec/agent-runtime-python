from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, text, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from app.config import Settings
from app.domain import (
    JSON,
    TERMINAL,
    ActionView,
    ApprovalDecision,
    CreateExecution,
    Dispatch,
    EventView,
    ExecutionView,
    Fault,
    HistoryView,
    Lease,
    ReconciliationView,
    State,
    ToolResult,
    Usage,
    Work,
    check_transition,
    fingerprint,
)
from app.tables import Approval, Event, Execution, Reconciliation, ToolCall, WebhookReceipt
from app.tools import ToolSpec


def action_digest(tool: str, arguments: JSON, side_effect: str, approval: bool) -> str:
    return fingerprint(
        {
            "tool": tool,
            "arguments": arguments,
            "side_effect": side_effect,
            "approval_required": approval,
            "contract_version": 1,
        }
    )


def no_fault(stage: str) -> None:
    pass


class Store:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        fault: Callable[[str], None] = no_fault,
        *,
        tenant_id: str | None = "legacy",
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.fault = fault
        self.tenant_id = tenant_id

    def for_tenant(self, tenant_id: str) -> Store:
        self.settings.tenant(tenant_id)
        return Store(self.sessions, self.settings, self.fault, tenant_id=tenant_id)

    def _scope(self) -> ColumnElement[bool]:
        return Execution.tenant_id == self.tenant_id if self.tenant_id is not None else true()

    async def _read(self, session: AsyncSession, execution_id: UUID) -> Execution:
        row = await session.scalar(
            select(Execution).where(Execution.id == execution_id, self._scope())
        )
        if row is None:
            raise Fault("execution_not_found", 404)
        return row

    @staticmethod
    async def _now(session: AsyncSession) -> datetime:
        now = await session.scalar(select(func.clock_timestamp()))
        assert isinstance(now, datetime)
        return now

    async def _locked(self, session: AsyncSession, execution_id: UUID) -> Execution:
        row = await session.scalar(
            select(Execution).where(Execution.id == execution_id, self._scope()).with_for_update()
        )
        if row is None:
            raise Fault("execution_not_found", 404)
        return row

    async def _fenced(self, session: AsyncSession, lease: Lease) -> Execution:
        row = await self._locked(session, lease.execution_id)
        now = await self._now(session)
        if (
            row.tenant_id != lease.tenant_id
            or row.lease_token != lease.token
            or row.lease_owner != lease.owner
            or row.lease_expires_at is None
            or row.lease_expires_at <= now
        ):
            raise Fault("stale_lease")
        return row

    @staticmethod
    def _event(
        session: AsyncSession,
        row: Execution,
        kind: str,
        *,
        source: str | None = None,
        call_id: UUID | None = None,
        details: JSON | None = None,
    ) -> None:
        session.add(
            Event(
                execution_id=row.id,
                kind=kind,
                from_state=source,
                to_state=row.state,
                tool_call_id=call_id,
                details=details or {},
            )
        )

    def _move(
        self,
        session: AsyncSession,
        row: Execution,
        target: State,
        kind: str,
        *,
        call_id: UUID | None = None,
        details: JSON | None = None,
    ) -> None:
        source = row.state
        check_transition(State(source), target)
        row.state = target.value
        row.updated_at = func.clock_timestamp()
        self._event(session, row, kind, source=source, call_id=call_id, details=details)

    @staticmethod
    def _release(row: Execution) -> None:
        row.lease_token = None
        row.lease_owner = None
        row.lease_expires_at = None

    def _fail(
        self, session: AsyncSession, row: Execution, code: str, call: ToolCall | None = None
    ) -> None:
        row.error_code = code
        row.outcome = {"error": code}
        if call:
            call.status = "FAILED"
            call.outcome = {"error": code}
        self._move(
            session,
            row,
            State.FAILED_PERMANENT,
            "failed",
            call_id=call.id if call else None,
            details={"error_class": code},
        )
        self._release(row)

    async def create(
        self,
        request: CreateExecution,
        key: str,
        correlation_id: UUID,
        nonce_hash: str | None = None,
    ) -> tuple[UUID, bool]:
        if self.settings.recovery_read_only:
            raise Fault("recovery_read_only", 503)
        digest = fingerprint(request.model_dump(mode="json"))
        if self.tenant_id is None:
            raise Fault("tenant_context_required", 403)
        candidate = uuid4()
        async with self.sessions.begin() as session:
            # The lock serializes only admissions for this tenant across API processes.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant, 73019))"),
                {"tenant": self.tenant_id},
            )
            now = await self._now(session)
            if nonce_hash:
                receipt = await session.scalar(
                    insert(WebhookReceipt)
                    .values(
                        tenant_id=self.tenant_id,
                        nonce_hash=nonce_hash,
                        expires_at=now + timedelta(seconds=610),
                    )
                    .on_conflict_do_nothing()
                    .returning(WebhookReceipt.nonce_hash)
                )
                if receipt is None:
                    raise Fault("webhook_replay")
            existing = list(
                (
                    await session.scalars(
                        select(Execution).where(
                            self._scope(),
                            or_(
                                Execution.idempotency_key == key,
                                Execution.business_key == request.business_key,
                            ),
                        )
                    )
                ).all()
            )
            if existing:
                if (
                    len(existing) != 1
                    or existing[0].request_digest != digest
                    or existing[0].idempotency_key != key
                ):
                    raise Fault("idempotency_conflict")
                return existing[0].id, False
            pending = await session.scalar(
                select(func.count())
                .select_from(Execution)
                .where(self._scope(), Execution.state.not_in([s.value for s in TERMINAL]))
            )
            if pending is not None and pending >= self.settings.tenant_queue_capacity:
                raise Fault("tenant_queue_full", 429)
            admissions = await session.scalar(
                select(func.count())
                .select_from(Execution)
                .where(self._scope(), Execution.created_at > now - timedelta(seconds=60))
            )
            if admissions is not None and admissions >= self.settings.tenant_admissions_per_minute:
                raise Fault("tenant_admission_rate_limited", 429)
            inserted = await session.scalar(
                insert(Execution)
                .values(
                    id=candidate,
                    tenant_id=self.tenant_id,
                    business_key=request.business_key,
                    idempotency_key=key,
                    request_digest=digest,
                    prompt=request.prompt,
                    state=State.CREATED.value,
                    correlation_id=correlation_id,
                    max_steps=request.max_steps,
                    max_tokens=request.max_tokens,
                    steps=0,
                    model_calls=0,
                    tokens_used=0,
                    retry_count=0,
                    deadline_at=now + timedelta(seconds=request.deadline_seconds),
                )
                .on_conflict_do_nothing()
                .returning(Execution.id)
            )
            if inserted:
                row = await session.get(Execution, candidate)
                assert row is not None
                self._event(session, row, "created")
                return candidate, True
            rows = list(
                (
                    await session.scalars(
                        select(Execution).where(
                            self._scope(),
                            or_(
                                Execution.idempotency_key == key,
                                Execution.business_key == request.business_key,
                            ),
                        )
                    )
                ).all()
            )
            if len(rows) != 1 or rows[0].request_digest != digest or rows[0].idempotency_key != key:
                raise Fault("idempotency_conflict")
            return rows[0].id, False

    async def get(self, execution_id: UUID) -> ExecutionView:
        async with self.sessions() as session:
            row = await self._read(session, execution_id)
            call = await session.scalar(
                select(ToolCall)
                .where(
                    ToolCall.execution_id == row.id, ToolCall.status.in_(["PROPOSED", "DISPATCHED"])
                )
                .order_by(ToolCall.ordinal.desc())
                .limit(1)
            )
            approval = await session.get(Approval, call.id) if call else None
            recovery = await session.scalar(
                select(Reconciliation)
                .join(ToolCall)
                .where(ToolCall.execution_id == row.id)
                .order_by(ToolCall.ordinal.desc())
                .limit(1)
            )
            action = (
                ActionView(
                    id=call.id,
                    tool=call.tool,
                    fingerprint=call.fingerprint,
                    arguments=call.arguments,
                    status=call.status,
                    approval_expires_at=approval.expires_at if approval else None,
                )
                if call
                else None
            )
            return ExecutionView(
                id=row.id,
                tenant_id=row.tenant_id,
                business_key=row.business_key,
                state=State(row.state),
                correlation_id=row.correlation_id,
                model_calls=row.model_calls,
                steps=row.steps,
                tokens_used=row.tokens_used,
                retry_count=row.retry_count,
                next_attempt_at=row.next_attempt_at,
                deadline_at=row.deadline_at,
                outcome=row.outcome,
                error_code=row.error_code,
                action=action,
                reconciliation=ReconciliationView(
                    status=recovery.status,
                    attempts=recovery.attempts,
                    budget=recovery.budget,
                    deadline_at=recovery.deadline_at,
                    next_attempt_at=recovery.next_attempt_at,
                )
                if recovery
                else None,
            )

    async def history(self, execution_id: UUID, cursor: int = 0, limit: int = 100) -> HistoryView:
        async with self.sessions() as session:
            await self._read(session, execution_id)
            events = list(
                (
                    await session.scalars(
                        select(Event)
                        .where(Event.execution_id == execution_id, Event.id > cursor)
                        .order_by(Event.id)
                        .limit(limit + 1)
                    )
                ).all()
            )
            items = [
                EventView(
                    id=e.id,
                    timestamp=e.timestamp,
                    kind=e.kind,
                    from_state=e.from_state,
                    to_state=e.to_state,
                    tool_call_id=e.tool_call_id,
                    metadata=e.details,
                )
                for e in events[:limit]
            ]
            return HistoryView(
                items=items, next_cursor=items[-1].id if len(events) > limit else None
            )

    async def claim(self, owner: str, execution_id: UUID | None = None) -> Work | None:
        if self.settings.recovery_read_only:
            raise Fault("recovery_read_only", 503)
        async with self.sessions.begin() as session:
            now = await self._now(session)
            query = (
                select(Execution)
                .where(
                    self._scope(),
                    Execution.state.in_([State.CREATED.value, State.RETRY_PENDING.value]),
                    or_(Execution.next_attempt_at.is_(None), Execution.next_attempt_at <= now),
                    Execution.lease_token.is_(None),
                )
                .order_by(Execution.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if execution_id:
                query = query.where(Execution.id == execution_id)
            row = await session.scalar(query)
            if row is None:
                return None
            if row.deadline_at <= now:
                self._fail(session, row, "execution_deadline")
                return None
            row.lease_token = uuid4()
            row.lease_owner = owner
            row.lease_expires_at = now + timedelta(seconds=self.settings.lease_seconds)
            row.next_attempt_at = None
            self._move(session, row, State.RUNNING, "claimed", details={"worker": owner})
            calls = list(
                (
                    await session.scalars(
                        select(ToolCall)
                        .where(ToolCall.execution_id == row.id)
                        .order_by(ToolCall.ordinal)
                    )
                ).all()
            )
            pending = next((c.id for c in calls if c.status == "PROPOSED"), None)
            observations: tuple[JSON, ...] = tuple(
                {"tool": c.tool, "result": c.outcome} for c in calls if c.status == "SUCCEEDED"
            )
            return Work(
                Lease(row.id, row.lease_token, owner, row.correlation_id, row.tenant_id),
                row.prompt,
                row.max_tokens - row.tokens_used,
                observations,
                pending,
            )

    async def start_model(self, lease: Lease) -> bool:
        async with self.sessions.begin() as session:
            row = await self._fenced(session, lease)
            if (
                row.model_calls >= row.max_steps + self.settings.max_retries + 1
                or row.tokens_used >= row.max_tokens
            ):
                self._fail(session, row, "model_budget_exhausted")
                return False
            row.model_calls += 1
            self._event(session, row, "model_started", details={"attempt": row.model_calls})
            return True

    async def record_usage(self, lease: Lease, usage: Usage) -> bool:
        async with self.sessions.begin() as session:
            row = await self._fenced(session, lease)
            row.tokens_used += usage.input_tokens + usage.output_tokens
            self._event(session, row, "model_usage", details=usage.model_dump(mode="json"))
            if row.tokens_used > row.max_tokens:
                self._fail(session, row, "token_budget_exceeded")
                return False
            return True

    async def finish(self, lease: Lease, text: str, refused: bool = False) -> None:
        async with self.sessions.begin() as session:
            row = await self._fenced(session, lease)
            row.outcome = {"text": text, "refused": refused}
            row.error_code = "model_refused" if refused else None
            self._move(
                session,
                row,
                State.FAILED_PERMANENT if refused else State.SUCCEEDED,
                "model_refused" if refused else "completed",
            )
            self._release(row)

    async def propose(self, lease: Lease, spec: ToolSpec, arguments: JSON) -> UUID | None:
        digest = action_digest(spec.name, arguments, spec.side_effect, spec.approval)
        async with self.sessions.begin() as session:
            row = await self._fenced(session, lease)
            if row.steps >= row.max_steps:
                self._fail(session, row, "step_budget_exhausted")
                return None
            exists = await session.scalar(
                select(ToolCall.id).where(
                    ToolCall.execution_id == row.id, ToolCall.fingerprint == digest
                )
            )
            if exists:
                self._fail(session, row, "repeated_action_denied")
                return None
            row.steps += 1
            call = ToolCall(
                id=uuid4(),
                execution_id=row.id,
                ordinal=row.steps,
                tool=spec.name,
                fingerprint=digest,
                # Preserve v1 legacy operation identities through migration/restore.
                operation_key=fingerprint(
                    {"business_key": row.business_key, "action": digest}
                    if row.tenant_id == "legacy"
                    else {
                        "tenant_id": row.tenant_id,
                        "business_key": row.business_key,
                        "action": digest,
                    }
                ),
                arguments=arguments,
                side_effect=spec.side_effect,
                approval_required=spec.approval,
                status="PROPOSED",
                attempt=0,
            )
            session.add(call)
            await session.flush()
            self._event(
                session,
                row,
                "action_proposed",
                call_id=call.id,
                details={"tool": spec.name, "fingerprint": digest},
            )
            if spec.approval:
                now = await self._now(session)
                session.add(
                    Approval(
                        call_id=call.id,
                        fingerprint=digest,
                        expires_at=now + timedelta(seconds=self.settings.approval_seconds),
                    )
                )
                self._move(
                    session, row, State.WAITING_FOR_APPROVAL, "approval_requested", call_id=call.id
                )
                self._release(row)
                return None
            return call.id

    async def inspect_call(self, call_id: UUID) -> Dispatch:
        async with self.sessions() as session:
            call = await session.scalar(
                select(ToolCall).join(Execution).where(ToolCall.id == call_id, self._scope())
            )
            if call is None:
                raise Fault("action_not_found", 404)
            row = await self._read(session, call.execution_id)
            return self._dispatch_view(call, row.tenant_id)

    @staticmethod
    def _dispatch_view(call: ToolCall, tenant_id: str = "legacy") -> Dispatch:
        return Dispatch(
            call.id,
            call.tool,
            call.arguments,
            call.fingerprint,
            call.operation_key,
            call.attempt,
            tenant_id,
        )

    async def dispatch(self, lease: Lease, call_id: UUID, spec: ToolSpec) -> Dispatch:
        async with self.sessions.begin() as session:
            row = await self._fenced(session, lease)
            call = await session.get(ToolCall, call_id)
            if call is None or call.execution_id != row.id or call.status != "PROPOSED":
                raise Fault("action_not_dispatchable")
            digest = action_digest(spec.name, call.arguments, spec.side_effect, spec.approval)
            if (
                call.fingerprint != digest
                or call.tool != spec.name
                or call.approval_required != spec.approval
                or call.side_effect != spec.side_effect
            ):
                raise Fault("action_fingerprint_changed")
            now = await self._now(session)
            if row.deadline_at <= now:
                raise Fault("execution_deadline")
            if spec.approval:
                approval = await session.get(Approval, call.id)
                if not approval or approval.decision is not True:
                    raise Fault("approval_required", 403)
                if approval.fingerprint != digest or approval.expires_at <= now:
                    raise Fault("approval_stale", 403)
            call.status = "DISPATCHED"
            call.attempt += 1
            self._move(
                session,
                row,
                State.TOOL_EXECUTING,
                "dispatch_intent",
                call_id=call.id,
                details={"tool": spec.name, "attempt": call.attempt},
            )
            self.fault("before_dispatch_commit")
            return self._dispatch_view(call, row.tenant_id)

    async def complete_tool(
        self, lease: Lease, call_id: UUID, result: ToolResult, reconciliation: bool = False
    ) -> None:
        async with self.sessions.begin() as session:
            row = await self._fenced(session, lease)
            call = await session.get(ToolCall, call_id)
            expected = State.RECONCILIATION_REQUIRED if reconciliation else State.TOOL_EXECUTING
            if (
                row.state != expected
                or call is None
                or call.execution_id != row.id
                or call.status != "DISPATCHED"
                or result.operation_key != call.operation_key
                or result.fingerprint != call.fingerprint
            ):
                raise Fault("outcome_identity_conflict")
            call.status = "SUCCEEDED"
            call.outcome = result.model_dump(mode="json")
            call.external_id = result.external_id
            if reconciliation:
                recovery = await session.get(Reconciliation, call_id)
                if recovery is None or recovery.status != "RECONCILING":
                    raise Fault("reconciliation_not_claimed")
                recovery.status = "RESOLVED"
            row.retry_count = 0
            row.error_code = None
            self._move(
                session,
                row,
                State.CREATED,
                "reconciled" if reconciliation else "tool_succeeded",
                call_id=call.id,
                details={"external_id": result.external_id},
            )
            self._release(row)
            self.fault("before_outcome_commit")

    async def fail(self, lease: Lease, code: str, call_id: UUID | None = None) -> None:
        async with self.sessions.begin() as session:
            row = await self._fenced(session, lease)
            call = await session.get(ToolCall, call_id) if call_id else None
            if call and call.execution_id != row.id:
                raise Fault("action_not_found", 404)
            self._fail(session, row, code, call)

    async def retry(
        self, lease: Lease, code: str, delay: float, call_id: UUID | None = None
    ) -> None:
        async with self.sessions.begin() as session:
            row = await self._fenced(session, lease)
            call = await session.get(ToolCall, call_id) if call_id else None
            if call and call.execution_id != row.id:
                raise Fault("action_not_found", 404)
            if call and call.side_effect != "read":
                raise Fault("write_retry_denied")
            now = await self._now(session)
            if row.retry_count >= self.settings.max_retries:
                self._fail(session, row, "retry_budget_exhausted", call)
                return
            if delay >= (row.deadline_at - now).total_seconds():
                self._fail(session, row, "retry_deadline_exceeded", call)
                return
            if call:
                call.status = "PROPOSED"
            row.retry_count += 1
            row.error_code = code
            row.next_attempt_at = now + timedelta(seconds=delay)
            self._move(
                session,
                row,
                State.RETRY_PENDING,
                "retry_scheduled",
                call_id=call_id,
                details={"error_class": code, "attempt": row.retry_count, "delay_seconds": delay},
            )
            self._release(row)

    async def require_reconciliation(self, lease: Lease, call_id: UUID, code: str) -> None:
        async with self.sessions.begin() as session:
            row = await self._fenced(session, lease)
            call = await session.get(ToolCall, call_id)
            if call is None or call.execution_id != row.id or call.status != "DISPATCHED":
                raise Fault("action_not_found", 404)
            await self._ensure_reconciliation(session, call)
            row.error_code = code
            self._move(
                session,
                row,
                State.RECONCILIATION_REQUIRED,
                "reconciliation_required",
                call_id=call_id,
                details={"error_class": code},
            )
            self._release(row)
            self.fault("before_reconciliation_commit")

    async def decide_approval(self, call_id: UUID, decision: ApprovalDecision, actor: str) -> UUID:
        async with self.sessions.begin() as session:
            call = await session.scalar(
                select(ToolCall).join(Execution).where(ToolCall.id == call_id, self._scope())
            )
            if call is None:
                raise Fault("action_not_found", 404)
            # Scope is checked before a caller can observe approval state or payload.
            row = await self._locked(session, call.execution_id)
            approval = await session.get(Approval, call_id)
            if (
                approval is None
                or row.state != State.WAITING_FOR_APPROVAL
                or approval.decision is not None
            ):
                raise Fault("approval_not_pending")
            now = await self._now(session)
            digest = action_digest(
                call.tool, call.arguments, call.side_effect, call.approval_required
            )
            if (
                decision.fingerprint != digest
                or approval.fingerprint != digest
                or call.fingerprint != digest
            ):
                raise Fault("approval_fingerprint_conflict")
            if approval.expires_at <= now or row.deadline_at <= now:
                raise Fault("approval_expired")
            approval.decision = decision.approve
            approval.actor = actor
            approval.decided_at = now
            if decision.approve:
                self._move(
                    session,
                    row,
                    State.CREATED,
                    "approval_granted",
                    call_id=call.id,
                    details={"actor": actor, "fingerprint": digest},
                )
            else:
                self._fail(session, row, "approval_denied", call)
                self._event(
                    session, row, "approval_denied", call_id=call.id, details={"actor": actor}
                )
            return row.id

    async def recover(self, limit: int = 100) -> int:
        async with self.sessions.begin() as session:
            now = await self._now(session)
            rows = list(
                (
                    await session.scalars(
                        select(Execution)
                        .where(
                            self._scope(),
                            Execution.state.in_([State.RUNNING.value, State.TOOL_EXECUTING.value]),
                            Execution.lease_expires_at <= now,
                        )
                        .order_by(Execution.lease_expires_at)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for row in rows:
                call = await session.scalar(
                    select(ToolCall).where(
                        ToolCall.execution_id == row.id, ToolCall.status == "DISPATCHED"
                    )
                )
                if call and call.side_effect != "read":
                    await self._ensure_reconciliation(session, call)
                    row.error_code = "lease_expired_after_dispatch"
                    self._move(
                        session,
                        row,
                        State.RECONCILIATION_REQUIRED,
                        "stale_write_recovered",
                        call_id=call.id,
                    )
                elif row.deadline_at <= now or row.retry_count >= self.settings.max_retries:
                    self._fail(session, row, "recovery_budget_exhausted", call)
                else:
                    if call:
                        call.status = "PROPOSED"
                    row.retry_count += 1
                    row.next_attempt_at = now
                    self._move(
                        session,
                        row,
                        State.RETRY_PENDING,
                        "stale_lease_recovered",
                        call_id=call.id if call else None,
                    )
                self._release(row)
            # Expired approvals have never authorized a dispatch, so failure is safe.
            expired = list(
                (
                    await session.scalars(
                        select(Execution)
                        .join(ToolCall, ToolCall.execution_id == Execution.id)
                        .join(Approval, Approval.call_id == ToolCall.id)
                        .where(
                            self._scope(),
                            Execution.state == State.WAITING_FOR_APPROVAL.value,
                            or_(Approval.expires_at <= now, Execution.deadline_at <= now),
                        )
                        .limit(limit)
                        .with_for_update(of=Execution, skip_locked=True)
                    )
                ).all()
            )
            for row in expired:
                self._fail(session, row, "approval_expired")
            receipts = delete(WebhookReceipt).where(WebhookReceipt.expires_at < now)
            if self.tenant_id is not None:
                receipts = receipts.where(WebhookReceipt.tenant_id == self.tenant_id)
            await session.execute(receipts)
            return len(rows) + len(expired)

    async def _ensure_reconciliation(self, session: AsyncSession, call: ToolCall) -> None:
        now = await self._now(session)
        await session.execute(
            insert(Reconciliation)
            .values(
                call_id=call.id,
                status="AMBIGUOUS",
                attempts=0,
                budget=self.settings.reconciliation_attempts,
                deadline_at=now + timedelta(seconds=self.settings.reconciliation_seconds),
                next_attempt_at=now,
            )
            .on_conflict_do_nothing()
        )

    def _manual_review(
        self, session: AsyncSession, row: Execution, recovery: Reconciliation, code: str
    ) -> None:
        recovery.status = "MANUAL_REVIEW"
        row.error_code = code
        self._event(
            session,
            row,
            "manual_review_required",
            call_id=recovery.call_id,
            details={"error_class": code, "attempts": recovery.attempts},
        )
        self._release(row)

    async def claim_reconciliation(
        self, execution_id: UUID | None, owner: str, operator_actor: str | None = None
    ) -> tuple[Lease, Dispatch] | None:
        async with self.sessions.begin() as session:
            now = await self._now(session)
            if execution_id is not None:
                row = await self._locked(session, execution_id)
            else:
                candidate = await session.scalar(
                    select(Execution)
                    .join(ToolCall)
                    .join(Reconciliation)
                    .where(
                        self._scope(),
                        Execution.state == State.RECONCILIATION_REQUIRED.value,
                        Reconciliation.status.in_(["AMBIGUOUS", "RECONCILING"]),
                        or_(
                            Reconciliation.next_attempt_at <= now, Reconciliation.deadline_at <= now
                        ),
                        or_(
                            Execution.lease_expires_at.is_(None), Execution.lease_expires_at <= now
                        ),
                    )
                    .order_by(Reconciliation.next_attempt_at)
                    .limit(1)
                    .with_for_update(of=Execution, skip_locked=True)
                )
                if candidate is None:
                    return None
                row = candidate
            if row.state != State.RECONCILIATION_REQUIRED:
                raise Fault("reconciliation_not_required")
            if row.lease_expires_at and row.lease_expires_at > now:
                return None
            call = await session.scalar(
                select(ToolCall).where(
                    ToolCall.execution_id == row.id, ToolCall.status == "DISPATCHED"
                )
            )
            if call is None:
                raise Fault("reconciliation_action_missing")
            recovery = await session.get(Reconciliation, call.id)
            if recovery is None:
                raise Fault("reconciliation_lifecycle_missing")
            if recovery.status in {"RESOLVED", "ABANDONED"}:
                raise Fault("reconciliation_terminal")
            if not operator_actor:
                if recovery.status == "MANUAL_REVIEW":
                    return None
                if recovery.attempts >= recovery.budget or recovery.deadline_at <= now:
                    self._manual_review(session, row, recovery, "reconciliation_budget_or_deadline")
                    return None
                if recovery.next_attempt_at > now:
                    return None
            recovery.status = "RECONCILING"
            recovery.attempts += 1
            row.lease_token = uuid4()
            row.lease_owner = owner
            row.lease_expires_at = now + timedelta(seconds=self.settings.lease_seconds)
            self._event(
                session,
                row,
                "reconciliation_claimed",
                call_id=call.id,
                details={"attempt": recovery.attempts, "operator": operator_actor},
            )
            return Lease(
                row.id, row.lease_token, owner, row.correlation_id, row.tenant_id
            ), self._dispatch_view(call, row.tenant_id)

    async def release_reconciliation(
        self, lease: Lease, code: str, operator_actor: str | None = None
    ) -> None:
        async with self.sessions.begin() as session:
            row = await self._fenced(session, lease)
            recovery = await session.scalar(
                select(Reconciliation)
                .join(ToolCall)
                .where(ToolCall.execution_id == row.id, Reconciliation.status == "RECONCILING")
            )
            if recovery is None or row.state != State.RECONCILIATION_REQUIRED:
                raise Fault("reconciliation_not_claimed")
            now = await self._now(session)
            row.error_code = code
            self._event(session, row, "reconciliation_unresolved", details={"error_class": code})
            if (
                operator_actor
                or recovery.attempts >= recovery.budget
                or recovery.deadline_at <= now
            ):
                self._manual_review(session, row, recovery, code)
            else:
                recovery.status = "AMBIGUOUS"
                recovery.next_attempt_at = now + timedelta(
                    seconds=min(
                        300,
                        self.settings.reconciliation_backoff_seconds * 2 ** (recovery.attempts - 1),
                    )
                )
                self._release(row)

    async def abandon(self, execution_id: UUID, actor: str, reason: str) -> None:
        async with self.sessions.begin() as session:
            row = await self._locked(session, execution_id)
            now = await self._now(session)
            if row.state != State.RECONCILIATION_REQUIRED:
                raise Fault("reconciliation_not_required")
            if row.lease_expires_at and row.lease_expires_at > now:
                raise Fault("reconciliation_in_progress")
            recovery = await session.scalar(
                select(Reconciliation)
                .join(ToolCall)
                .where(
                    ToolCall.execution_id == row.id,
                    Reconciliation.status.in_(["AMBIGUOUS", "RECONCILING", "MANUAL_REVIEW"]),
                )
            )
            if recovery is None:
                raise Fault("reconciliation_lifecycle_missing")
            recovery.status = "ABANDONED"
            row.outcome = {"operator_abandoned": True, "effect_unknown": True}
            row.error_code = "external_effect_unknown"
            self._move(
                session,
                row,
                State.FAILED_PERMANENT,
                "operator_abandoned",
                call_id=recovery.call_id,
                details={"actor": actor, "reason": reason},
            )
            self._release(row)

    async def cancel(self, execution_id: UUID) -> None:
        async with self.sessions.begin() as session:
            row = await self._locked(session, execution_id)
            row.outcome = {"cancelled": True}
            self._move(session, row, State.CANCELLED, "cancelled")
            self._release(row)
