from __future__ import annotations

import asyncio
import time
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.domain import ExternalFault, Fault, Lease, ModelTurn
from app.observability import Metrics, logger
from app.providers import ModelProvider
from app.reliability import backoff
from app.store import Store
from app.tools import HttpTools, Registry, ToolSpec


class Runtime:
    def __init__(
        self,
        store: Store,
        provider: ModelProvider,
        registry: Registry,
        tools: HttpTools,
        metrics: Metrics,
    ) -> None:
        self.store, self.provider, self.registry = store, provider, registry
        self.tools, self.metrics = tools, metrics

    def for_tenant(self, tenant_id: str) -> Runtime:
        if self.store.tenant_id == tenant_id:
            return self
        return Runtime(
            self.store.for_tenant(tenant_id),
            self.provider,
            Registry(
                self.store.settings.tenant(tenant_id).allowed_tools,
                self.store.settings.tool_timeout,
            ),
            self.tools,
            self.metrics,
        )

    async def _external_failure(
        self,
        lease: Lease,
        error: ExternalFault,
        call_id: UUID | None,
        spec: ToolSpec | None,
        dispatched: bool,
    ) -> None:
        self.metrics.errors.inc()
        if dispatched and spec and spec.side_effect != "read":
            assert call_id is not None
            await self.store.require_reconciliation(lease, call_id, error.code)
        elif error.transient:
            view = await self.store.get(lease.execution_id)
            delay = backoff(
                view.retry_count, self.store.settings.retry_base_seconds, error.retry_after
            )
            await self.store.retry(lease, error.code, delay, call_id if dispatched else None)
            self.metrics.retries.inc()
        else:
            await self.store.fail(lease, error.code, call_id)

    async def advance(self, owner: str, execution_id: UUID | None = None) -> UUID | None:
        work = await self.store.claim(owner, execution_id)
        if work is None:
            return None
        started = time.perf_counter()
        self.metrics.advances.inc()
        lease, call_id = work.lease, work.pending_call_id
        spec: ToolSpec | None = None
        dispatched = False
        try:
            tenant = self.store.settings.tenant(lease.tenant_id)
            registry = Registry(
                self.registry.allowed
                if self.store.tenant_id == lease.tenant_id
                else tenant.allowed_tools,
                self.store.settings.tool_timeout,
                tenant.github_repository,
                self.store.settings.github_actor,
            )
            if call_id is None:
                if not await self.store.start_model(lease):
                    return lease.execution_id
                async with asyncio.timeout(self.store.settings.model_timeout):
                    turn = ModelTurn.model_validate(
                        await self.provider.decide(
                            work.prompt,
                            work.observations,
                            registry.schemas(),
                            work.remaining_tokens,
                        )
                    )
                if not await self.store.record_usage(lease, turn.usage):
                    return lease.execution_id
                decision = turn.decision
                if decision.kind != "tool":
                    assert decision.text is not None
                    await self.store.finish(lease, decision.text, refused=decision.kind == "refuse")
                    return lease.execution_id
                assert decision.tool is not None
                spec = registry.get(decision.tool)
                arguments = spec.validate(decision.arguments)
                self.tools.preflight(spec.name, lease.tenant_id)
                call_id = await self.store.propose(lease, spec, arguments)
                if call_id is None:
                    return lease.execution_id
            else:
                call = await self.store.inspect_call(call_id)
                spec = registry.get(call.tool)
                spec.validate(call.arguments)
                self.tools.preflight(spec.name, lease.tenant_id)
            assert spec is not None
            call = await self.store.dispatch(lease, call_id, spec)
            dispatched = True
            self.metrics.tools.labels(spec.name).inc()
            self.store.fault("after_dispatch_commit")
            with self.metrics.tool_duration.labels(spec.name).time():
                async with asyncio.timeout(spec.timeout_seconds):
                    result = await self.tools.execute(call)
            self.store.fault("after_external_success")
            await self.store.complete_tool(lease, call_id, result)
            logger.info(
                "tool_completed",
                extra={
                    "execution_id": str(lease.execution_id),
                    "correlation_id": str(lease.correlation_id),
                    "tool_call_id": str(call.call_id),
                    "tool": spec.name,
                    "attempt": call.attempt,
                    "outcome": "persisted",
                },
            )
        except ExternalFault as exc:
            await self._external_failure(lease, exc, call_id, spec, dispatched)
        except TimeoutError:
            await self._external_failure(
                lease,
                ExternalFault("execution_timeout", transient=True, ambiguous=dispatched),
                call_id,
                spec,
                dispatched,
            )
        except ValidationError:
            if dispatched and spec and spec.side_effect != "read":
                assert call_id is not None
                await self.store.require_reconciliation(lease, call_id, "tool_response_invalid")
            else:
                await self.store.fail(lease, "model_response_invalid", call_id)
        except Fault as exc:
            if exc.code == "stale_lease":
                raise
            if dispatched and spec and spec.side_effect != "read":
                assert call_id is not None
                await self.store.require_reconciliation(lease, call_id, exc.code)
            else:
                await self.store.fail(lease, exc.code, call_id)
        except (SQLAlchemyError, OSError):
            # If this repair write also fails, propagate. Durable DISPATCHED intent
            # is the recovery evidence; an unavailable DB is never reported as success.
            if dispatched and spec and spec.side_effect != "read":
                assert call_id is not None
                await self.store.require_reconciliation(
                    lease, call_id, "outcome_persistence_failed"
                )
            else:
                raise
        except Exception:
            # Quarantine an unexpected pre-dispatch bug; uncertainty after a write
            # stays in the reconciliation ledger. Never log the exception payload.
            if dispatched and spec and spec.side_effect != "read":
                assert call_id is not None
                await self.store.require_reconciliation(lease, call_id, "unexpected_after_dispatch")
            else:
                await self.store.fail(lease, "poison_job_quarantined", call_id)
        finally:
            logger.info(
                "advance_completed",
                extra={
                    "execution_id": str(lease.execution_id),
                    "correlation_id": str(lease.correlation_id),
                    "provider": self.provider.name,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
        return lease.execution_id

    async def reconcile(
        self, execution_id: UUID | None, owner: str, operator_actor: str | None = None
    ) -> None:
        claimed = await self.store.claim_reconciliation(execution_id, owner, operator_actor)
        if claimed is None:
            return
        lease, call = claimed
        try:
            async with asyncio.timeout(self.store.settings.tool_timeout):
                result = await self.tools.lookup_for(call)
            self.metrics.reconciliations.labels(result.status).inc()
            if result.result:
                await self.store.complete_tool(
                    lease, call.call_id, result.result, reconciliation=True
                )
            else:
                await self.store.release_reconciliation(
                    lease, f"provider_{result.status}_no_replay", operator_actor
                )
        except (ExternalFault, TimeoutError) as exc:
            await self.store.release_reconciliation(
                lease,
                exc.code if isinstance(exc, ExternalFault) else "reconciliation_timeout",
                operator_actor,
            )
        except (Fault, SQLAlchemyError, OSError):
            raise
        except Exception:
            await self.store.release_reconciliation(
                lease, "reconciliation_internal_error", operator_actor
            )
