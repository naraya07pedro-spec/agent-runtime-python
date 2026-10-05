import asyncio
import signal
from contextlib import asynccontextmanager
from types import SimpleNamespace

from app import worker
from app.observability import Metrics


async def test_worker_survives_compound_connection_refusal_and_stops_new_claims(
    monkeypatch, settings
):
    callbacks, recovered, claimed = {}, [], []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, callback: callbacks.update({sig: callback})
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: True)
    settings.worker_poll_seconds = 0.01

    async def recover():
        recovered.append(True)
        if len(recovered) == 1:
            raise OSError("synthetic IPv4/IPv6 refusal; private exception text")
        callbacks[signal.SIGTERM]()

    async def advance(*args):
        claimed.append(True)

    async def reconcile(*args):
        claimed.append(True)

    runtime = SimpleNamespace(
        store=SimpleNamespace(recover=recover, settings=settings),
        advance=advance,
        reconcile=reconcile,
        metrics=Metrics(),
    )

    @asynccontextmanager
    async def fake_services(_settings):
        yield runtime

    monkeypatch.setattr(worker, "Settings", lambda: settings)
    monkeypatch.setattr(worker, "services", fake_services)
    await worker.run(False, None, False)
    assert len(recovered) == 2
    assert not claimed
