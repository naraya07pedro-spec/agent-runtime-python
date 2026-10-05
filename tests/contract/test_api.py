from uuid import uuid4

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.api import create_app

pytestmark = [pytest.mark.integration, pytest.mark.contract]


async def test_http_statuses_openapi_and_metrics(rig):
    app = create_app(rig.settings, rig.runtime)
    headers = {"Authorization": "Bearer " + "a" * 40, "Idempotency-Key": "api-contract"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/ready")).status_code == 200
        response = await client.post(
            "/executions",
            headers=headers,
            json={"business_key": "api-contract", "prompt": "lookup customer demo"},
        )
        assert response.status_code == 201, response.text
        UUID = response.json()["id"]
        path = f"/executions/{UUID}"
        assert response.headers["Location"] == path
        assert "X-Request-ID" in response.headers
        duplicate = await client.post(
            "/executions",
            headers=headers,
            json={"business_key": "api-contract", "prompt": "lookup customer demo"},
        )
        assert duplicate.status_code == 200 and duplicate.json()["id"] == UUID
        conflict = await client.post(
            "/executions",
            headers=headers,
            json={"business_key": "api-contract", "prompt": "different"},
        )
        assert conflict.status_code == 409
        assert (await client.post(path + "/advance", headers=headers)).json()["state"] == "CREATED"
        assert (await client.post(path + "/advance", headers=headers)).json()[
            "state"
        ] == "SUCCEEDED"
        history = await client.get(path + "/events?limit=2", headers=headers)
        assert history.status_code == 200 and history.json()["next_cursor"]
        metrics = await client.get("/metrics", headers=headers)
        assert 'runtime_executions{state="SUCCEEDED"} 1' in metrics.text
        assert "runtime_durable_dispatches" in metrics.text
        schema = (await client.get("/openapi.json")).json()
        assert "ExecutionView" in schema["components"]["schemas"]
        assert "Execution" not in schema["components"]["schemas"]


@pytest.mark.parametrize("path", ["/executions/not-a-uuid", "/executions/" + str(uuid4())])
async def test_invalid_and_missing_resource_errors(rig, path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(rig.settings, rig.runtime)),
        base_url="http://test",
    ) as client:
        response = await client.get(path, headers={"Authorization": "Bearer " + "a" * 40})
        assert response.status_code in {404, 422}
        assert set(response.json()) == {"error"}
        assert "request_id" in response.json()["error"]


async def test_database_outage_returns_unavailable_and_no_false_success(rig):
    async def unavailable(*args, **kwargs):
        raise SQLAlchemyError("secret database password")

    rig.store.create = unavailable
    app = create_app(rig.settings, rig.runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/executions",
            headers={"Authorization": "Bearer " + "a" * 40, "Idempotency-Key": "test"},
            json={"business_key": "test", "prompt": "hello"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_unavailable"
        assert "secret" not in response.text


async def test_invalid_correlation_identifier_is_rejected(rig):
    app = create_app(rig.settings, rig.runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health", headers={"X-Correlation-ID": "injected-log-content"})
        assert response.status_code == 422
        assert "injected-log-content" not in response.text


async def test_readiness_rejects_missing_model_and_tool_configuration(rig):
    rig.settings.model_provider = "openai"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(rig.settings, rig.runtime)),
        base_url="http://test",
    ) as client:
        response = await client.get("/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "model_configuration_missing"
        rig.settings.model_provider = "fake"
        rig.settings.tool_api_token = None
        response = await client.get("/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "tool_configuration_missing"
