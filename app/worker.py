import argparse
import asyncio
import signal
from uuid import UUID, uuid4

from prometheus_client import start_http_server
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.domain import Fault
from app.observability import configure_logging, logger
from app.runtime import Runtime
from app.services import services


async def drain_operation(
    operation: asyncio.Task[None], stop: asyncio.Event, deadline_seconds: float
) -> bool:
    """Return False when stopping; a forced cancellation leaves durable intent intact."""
    stopper = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait({operation, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if stopper in done:
            try:
                await asyncio.wait_for(asyncio.shield(operation), timeout=deadline_seconds)
            except TimeoutError:
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
            return False
        await operation
        return True
    finally:
        stopper.cancel()
        await asyncio.gather(stopper, return_exceptions=True)


async def cycle(
    runtime: Runtime,
    owner: str,
    execution_id: UUID | None,
    recover_only: bool,
    stop: asyncio.Event,
) -> None:
    await runtime.store.recover()
    if not recover_only and not stop.is_set():
        await runtime.reconcile(None, owner)
        if not stop.is_set() and not runtime.store.settings.recovery_read_only:
            await runtime.advance(owner, execution_id)


async def run(once: bool, execution_id: UUID | None, recover_only: bool) -> None:
    settings = Settings()
    owner = f"worker-{uuid4()}"
    configure_logging()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    async with services(settings) as runtime:
        exporter = (
            start_http_server(
                settings.worker_metrics_port, addr="127.0.0.1", registry=runtime.metrics.registry
            )
            if settings.worker_metrics_port
            else None
        )
        while not stop.is_set():
            try:
                operation = asyncio.create_task(
                    cycle(runtime, owner, execution_id, recover_only, stop)
                )
                if not await drain_operation(operation, stop, settings.worker_drain_seconds):
                    break
            except (SQLAlchemyError, OSError, TimeoutError, Fault) as exc:
                logger.error(
                    "worker_error",
                    extra={
                        "error_class": exc.code
                        if isinstance(exc, Fault)
                        else "database_unavailable"
                    },
                )
                if once:
                    raise SystemExit(1) from None
            if once or recover_only:
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
            except TimeoutError:
                pass
        if exporter:
            exporter[0].shutdown()
            exporter[0].server_close()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.remove_signal_handler(sig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded runtime worker with stale-lease recovery")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--execution", type=UUID)
    parser.add_argument("--recover-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.once, args.execution, args.recover_only))


if __name__ == "__main__":
    main()
