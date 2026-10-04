import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.domain import ApprovalDecision, CreateExecution, ExecutionView, Fault, HistoryView
from app.observability import configure_logging, logger
from app.runtime import Runtime
from app.security import IngressLimits, authenticate, verify_webhook
from app.services import services
from app.tables import Execution


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        if runtime is not None:
            app.state.runtime = runtime
            yield
        else:
            async with services(config) as instance:
                app.state.runtime = instance
                yield

    app = FastAPI(title="Bounded Agent Runtime", version="0.1.0", lifespan=lifespan)
    # Dependency-injected tests need no implicit process/global application state.
    if runtime is not None:
        app.state.runtime = runtime

    def service(request: Request) -> Runtime:
        return cast(Runtime, request.app.state.runtime)

    def api_auth(request: Request) -> None:
        authenticate(request.headers.get("authorization"), config.api_key)

    def approval_auth(request: Request) -> None:
        authenticate(request.headers.get("authorization"), config.approval_key)

    def error(request: Request, code: str, status: int) -> JSONResponse:
        request_id = str(getattr(request.state, "request_id", uuid4()))
        return JSONResponse({"error": {"code": code, "request_id": request_id}}, status_code=status)

    @app.exception_handler(Fault)
    async def domain_error(request: Request, exc: Fault) -> JSONResponse:
        return error(request, exc.code, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error(request, "invalid_request", 422)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("database_unavailable", extra={"request_id": str(request.state.request_id)})
        return error(request, "database_unavailable", 503)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Central exception sanitization intentionally covers unknown programmer
        # errors. They return 500 and are counted, never converted into success.
        logger.error("internal_error", extra={"request_id": str(request.state.request_id)})
        return error(request, "internal_error", 500)

    app.add_middleware(
        IngressLimits,
        max_bytes=config.max_request_bytes,
        requests_per_minute=config.requests_per_minute,
    )

    @app.middleware("http")
    async def correlation(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = uuid4()
        started = time.perf_counter()
        try:
            request.state.correlation_id = (
                UUID(request.headers["x-correlation-id"])
                if "x-correlation-id" in request.headers
                else uuid4()
            )
        except ValueError:
            return error(request, "invalid_correlation_id", 422)
        response = await call_next(request)
        response.headers["X-Request-ID"] = str(request.state.request_id)
        response.headers["X-Correlation-ID"] = str(request.state.correlation_id)
        service(request).metrics.requests.labels(str(response.status_code)).inc()
        logger.info(
            "request_completed",
            extra={
                "request_id": str(request.state.request_id),
                "correlation_id": str(request.state.correlation_id),
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "outcome": str(response.status_code),
            },
        )
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/ready")
    async def ready(request: Request) -> dict[str, str]:
        async with service(request).store.sessions() as session:
            version = await session.scalar(text("SELECT version_num FROM alembic_version"))
            if version != "0001":
                raise Fault("schema_not_ready", 503)
        if config.api_key is None or config.approval_key is None:
            raise Fault("authentication_not_configured", 503)
        return {"status": "ready"}

    @app.post(
        "/executions",
        response_model=ExecutionView,
        status_code=201,
        dependencies=[Depends(api_auth)],
    )
    async def create(
        request: Request,
        body: CreateExecution,
        response: Response,
        idempotency_key: Annotated[
            str, Header(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
        ],
    ) -> ExecutionView:
        instance = service(request)
        execution_id, created = await instance.store.create(
            body, idempotency_key, request.state.correlation_id
        )
        response.status_code = 201 if created else 200
        response.headers["Location"] = f"/executions/{execution_id}"
        return await instance.store.get(execution_id)

    @app.get(
        "/executions/{execution_id}", response_model=ExecutionView, dependencies=[Depends(api_auth)]
    )
    async def get(request: Request, execution_id: UUID) -> ExecutionView:
        return await service(request).store.get(execution_id)

    @app.get(
        "/executions/{execution_id}/events",
        response_model=HistoryView,
        dependencies=[Depends(api_auth)],
    )
    async def events(
        request: Request,
        execution_id: UUID,
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> HistoryView:
        return await service(request).store.history(execution_id, cursor, limit)

    @app.post(
        "/executions/{execution_id}/advance",
        response_model=ExecutionView,
        dependencies=[Depends(api_auth)],
    )
    async def advance(request: Request, execution_id: UUID) -> ExecutionView:
        instance = service(request)
        await instance.advance(f"api-{request.state.request_id}", execution_id)
        return await instance.store.get(execution_id)

    @app.post(
        "/executions/{execution_id}/reconcile",
        response_model=ExecutionView,
        dependencies=[Depends(api_auth)],
    )
    async def reconcile(request: Request, execution_id: UUID) -> ExecutionView:
        instance = service(request)
        await instance.reconcile(execution_id, f"reconcile-{request.state.request_id}")
        return await instance.store.get(execution_id)

    @app.post(
        "/executions/{execution_id}/cancel",
        response_model=ExecutionView,
        dependencies=[Depends(api_auth)],
    )
    async def cancel(request: Request, execution_id: UUID) -> ExecutionView:
        await service(request).store.cancel(execution_id)
        return await service(request).store.get(execution_id)

    @app.post(
        "/approvals/{call_id}/decision",
        response_model=ExecutionView,
        dependencies=[Depends(approval_auth)],
    )
    async def approve(request: Request, call_id: UUID, decision: ApprovalDecision) -> ExecutionView:
        execution_id = await service(request).store.decide_approval(
            call_id, decision, "approval-key-holder"
        )
        return await service(request).store.get(execution_id)

    @app.post("/webhooks", response_model=ExecutionView)
    async def webhook(request: Request, body: CreateExecution) -> ExecutionView:
        raw = await request.body()
        nonce_hash = verify_webhook(
            raw,
            request.headers.get("x-timestamp"),
            request.headers.get("x-nonce"),
            request.headers.get("x-signature"),
            config.webhook_secret,
        )
        # Signed business identity supplies a stable idempotency key; unsigned headers cannot change it.
        execution_id, _ = await service(request).store.create(
            body, body.business_key, request.state.correlation_id, nonce_hash
        )
        return await service(request).store.get(execution_id)

    @app.get("/metrics", dependencies=[Depends(api_auth)])
    async def metrics(request: Request) -> Response:
        instance = service(request)
        async with instance.store.sessions() as session:
            states = (
                await session.execute(
                    select(Execution.state, func.count()).group_by(Execution.state)
                )
            ).all()
        gauges = (
            "# HELP runtime_executions Durable execution counts\n# TYPE runtime_executions gauge\n"
        )
        gauges += "".join(
            f'runtime_executions{{state="{state}"}} {count}\n' for state, count in states
        )
        return Response(
            generate_latest(instance.metrics.registry) + gauges.encode(),
            headers={"Content-Type": CONTENT_TYPE_LATEST},
        )

    return app
