import asyncio

import pytest

from app.worker import drain_operation


async def test_sigterm_event_drains_one_inflight_operation_without_cancelling_it():
    started, complete, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancelled = []

    async def work():
        started.set()
        try:
            await complete.wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    operation = asyncio.create_task(work())
    drain = asyncio.create_task(drain_operation(operation, stop, 1))
    await started.wait()
    stop.set()
    complete.set()
    assert not await drain
    assert not cancelled


async def test_drain_deadline_cancels_inflight_task_and_propagates_real_errors():
    stop = asyncio.Event()

    async def blocked():
        await asyncio.Event().wait()

    operation = asyncio.create_task(blocked())
    stop.set()
    assert not await drain_operation(operation, stop, 0.01)
    assert operation.cancelled()

    async def broken():
        raise RuntimeError("synthetic")

    with pytest.raises(RuntimeError):
        await drain_operation(asyncio.create_task(broken()), asyncio.Event(), 1)
