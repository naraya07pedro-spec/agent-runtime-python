"""Subprocess fixture: exit without finally handlers after the external commit."""

import asyncio
import os
import sys
from uuid import UUID

from app.config import Settings
from app.services import services


async def main():
    async with services(Settings()) as runtime:
        if len(sys.argv) > 2 and sys.argv[2] == "after_claim":
            assert await runtime.store.claim("dead-claim-owner", UUID(sys.argv[1]))
            os._exit(74)

        def crash(stage):
            if stage == "after_external_success":
                os._exit(73)

        runtime.store.fault = crash
        await runtime.advance("subprocess-crash-worker", UUID(sys.argv[1]))
    raise SystemExit("crash checkpoint was not reached")


if __name__ == "__main__":
    asyncio.run(main())
