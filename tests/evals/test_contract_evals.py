import pytest

from evals.run import cases, evaluate_case

pytestmark = [pytest.mark.integration, pytest.mark.eval]


@pytest.mark.parametrize("case", cases(), ids=lambda case: case["id"])
async def test_runtime_contract_regression(rig, case):
    result = await evaluate_case(rig.store, rig.runtime.tools, case)
    assert result["passed"], result
