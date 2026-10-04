import argparse
import asyncio
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.domain import Fault
from app.observability import configure_logging, logger
from app.services import services


async def run(once: bool, execution_id: UUID | None, recover_only: bool) -> None:
    settings = Settings()
    owner = f"worker-{uuid4()}"
    configure_logging()
    async with services(settings) as runtime:
        while True:
            try:
                await runtime.store.recover()
                if not recover_only:
                    await runtime.advance(owner, execution_id)
            except (SQLAlchemyError, Fault) as exc:
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
                return
            await asyncio.sleep(settings.worker_poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded runtime worker with stale-lease recovery")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--execution", type=UUID)
    parser.add_argument("--recover-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.once, args.execution, args.recover_only))


if __name__ == "__main__":
    main()
