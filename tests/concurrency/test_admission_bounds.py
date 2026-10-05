import asyncio
from uuid import uuid4

import pytest

from app.domain import CreateExecution, Fault
from app.store import Store
from tests.tenants import configure_tenants

pytestmark = pytest.mark.concurrency


async def test_queue_capacity_is_shared_across_store_instances_and_duplicate_bypasses_limit(rig):
    alpha, beta = configure_tenants(rig)
    rig.settings.tenant_queue_capacity = 2

    async def create(index):
        store = Store(rig.store.sessions, rig.settings, tenant_id="alpha")
        key = f"synthetic-{index}"
        try:
            return await store.create(
                CreateExecution(business_key=key, prompt="lookup customer demo"), key, uuid4()
            )
        except Fault as exc:
            return exc.code

    results = await asyncio.gather(*(create(i) for i in range(12)))
    assert sum(isinstance(r, tuple) for r in results) == 2
    assert results.count("tenant_queue_full") == 10
    eid = next(r[0] for r in results if isinstance(r, tuple))
    view = await alpha.store.get(eid)
    duplicate, created = await alpha.store.create(
        CreateExecution(business_key=view.business_key, prompt="lookup customer demo"),
        view.business_key,
        uuid4(),
    )
    assert duplicate == eid and not created
    await beta.store.create(
        CreateExecution(business_key="beta", prompt="lookup customer demo"), "beta", uuid4()
    )
    await alpha.store.cancel(eid)
    await alpha.store.create(
        CreateExecution(business_key="new", prompt="lookup customer demo"), "new", uuid4()
    )


async def test_admission_quota_is_durable_across_process_equivalent_instances(rig):
    alpha, _ = configure_tenants(rig)
    rig.settings.tenant_admissions_per_minute = 1
    request = CreateExecution(business_key="one", prompt="lookup customer demo")
    eid, _ = await alpha.store.create(request, "one", uuid4())
    other = Store(rig.store.sessions, rig.settings, tenant_id="alpha")
    assert await other.create(request, "one", uuid4()) == (eid, False)
    with pytest.raises(Fault, match="tenant_admission_rate_limited"):
        await other.create(
            CreateExecution(business_key="two", prompt="lookup customer demo"), "two", uuid4()
        )
