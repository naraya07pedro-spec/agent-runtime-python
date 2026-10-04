"""Independent transactional fake external service. Never sends messages or money."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException
from pydantic import Field
from sqlalchemy import String, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import Settings
from app.db import connect
from app.domain import JSON, Contract, Fault, ReconcileResult, ToolResult
from app.security import authenticate
from app.store import action_digest
from app.tools import Registry

ALL_SANDBOX_TOOLS = frozenset(
    {"lookup_customer", "upsert_ticket", "send_notification", "refund_payment"}
)


class SandboxBase(DeclarativeBase):
    pass


class Effect(SandboxBase):
    __tablename__ = "sandbox_external_effects"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    operation_key: Mapped[str] = mapped_column(String(64), index=True)
    dedup_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str] = mapped_column(String(80))
    value: Mapped[str] = mapped_column(String(4000))


class Envelope(Contract):
    arguments: JSON
    operation_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


def create_sandbox(
    settings: Settings | None = None, sessions: async_sessionmaker[AsyncSession] | None = None
) -> FastAPI:
    config = settings or Settings()
    engine, default_sessions = connect(config.database_url)
    factory = sessions or default_sessions
    registry = Registry(ALL_SANDBOX_TOOLS)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with engine.begin() as connection:
            await connection.run_sync(SandboxBase.metadata.create_all)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="External provider simulator — no real effects", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "sandbox"}

    def auth(header: str | None) -> None:
        try:
            authenticate(header, config.tool_api_token)
        except Fault as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc

    @app.post("/tools/{tool}", response_model=ToolResult)
    async def execute(
        tool: str, body: Envelope, authorization: Annotated[str | None, Header()] = None
    ) -> ToolResult:
        auth(authorization)
        try:
            spec = registry.get(tool)
            arguments = spec.validate(body.arguments)
        except Fault as exc:
            raise HTTPException(status_code=400, detail=exc.code) from exc
        if body.fingerprint != action_digest(tool, arguments, spec.side_effect, spec.approval):
            raise HTTPException(status_code=409, detail="fingerprint_conflict")
        if spec.side_effect == "read":
            return ToolResult(
                operation_key=body.operation_key,
                fingerprint=body.fingerprint,
                external_id="customer-record",
                value="Sandbox customer exists",
            )
        async with factory.begin() as session:
            effect_id = uuid4()
            dedup = body.operation_key if spec.side_effect == "idempotent_write" else None
            inserted = await session.scalar(
                insert(Effect)
                .values(
                    id=effect_id,
                    operation_key=body.operation_key,
                    dedup_key=dedup,
                    fingerprint=body.fingerprint,
                    tool=tool,
                    value="Sandbox effect committed",
                )
                .on_conflict_do_nothing()
                .returning(Effect.id)
            )
            effect = (
                await session.get(Effect, effect_id)
                if inserted
                else await session.scalar(select(Effect).where(Effect.dedup_key == dedup))
            )
            assert effect is not None
            if effect.fingerprint != body.fingerprint:
                raise HTTPException(status_code=409, detail="idempotency_conflict")
            return ToolResult(
                operation_key=effect.operation_key,
                fingerprint=effect.fingerprint,
                external_id=str(effect.id),
                value=effect.value,
            )

    @app.get("/operations/{operation_key}", response_model=ReconcileResult)
    async def lookup(
        operation_key: str, authorization: Annotated[str | None, Header()] = None
    ) -> ReconcileResult:
        auth(authorization)
        async with factory() as session:
            effects = list(
                (
                    await session.scalars(
                        select(Effect).where(Effect.operation_key == operation_key).limit(2)
                    )
                ).all()
            )
            if len(effects) != 1:
                return ReconcileResult(status="absent" if not effects else "unknown")
            effect = effects[0]
            return ReconcileResult(
                status="found",
                result=ToolResult(
                    operation_key=effect.operation_key,
                    fingerprint=effect.fingerprint,
                    external_id=str(effect.id),
                    value=effect.value,
                ),
            )

    return app
