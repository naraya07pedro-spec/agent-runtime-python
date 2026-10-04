import json

import httpx
import pytest

from app.config import Settings
from app.domain import ExternalFault
from app.providers import OpenAIProvider

pytestmark = pytest.mark.contract


def response_body():
    return {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "name": "lookup_customer",
                "arguments": '{"customer_id":"demo"}',
            }
        ],
        "usage": {"input_tokens": 12, "output_tokens": 8},
    }


def test_native_function_call_decodes_into_typed_contract():
    turn = OpenAIProvider.parse(json.dumps(response_body()).encode())
    assert turn.decision.tool == "lookup_customer"
    assert turn.decision.arguments == {"customer_id": "demo"}
    assert turn.usage.input_tokens == 12
    assert turn.usage.cost_usd is None


@pytest.mark.parametrize(
    "change",
    [
        "malformed",
        "two_calls",
        "incomplete",
        "missing_usage",
        "negative_usage",
        "malformed_arguments",
        "empty_final",
    ],
)
def test_provider_malformed_envelopes_fail_closed(change):
    body = response_body()
    if change == "malformed":
        raw = b"not json"
    else:
        if change == "two_calls":
            body["output"] *= 2
        if change == "incomplete":
            body["status"] = "incomplete"
        if change == "missing_usage":
            body.pop("usage")
        if change == "negative_usage":
            body["usage"]["input_tokens"] = -1
        if change == "malformed_arguments":
            body["output"][0]["arguments"] = "{broken"
        if change == "empty_final":
            body["output"] = []
        raw = json.dumps(body).encode()
    with pytest.raises(ExternalFault, match="model_response_invalid"):
        OpenAIProvider.parse(raw)


@pytest.mark.parametrize("kind", ["output_text", "refusal"])
def test_native_terminal_content(kind):
    body = response_body()
    body["output"] = [
        {
            "type": "message",
            "content": [
                {"type": kind, "text" if kind == "output_text" else "refusal": "A bounded answer"}
            ],
        }
    ]
    turn = OpenAIProvider.parse(json.dumps(body).encode())
    assert turn.decision.kind == ("finish" if kind == "output_text" else "refuse")


async def test_missing_credentials_never_calls_network():
    def deny(request):
        pytest.fail("network must not be attempted")

    async with httpx.AsyncClient(transport=httpx.MockTransport(deny)) as client:
        provider = OpenAIProvider(client, Settings(_env_file=None, model_provider="openai"))
        with pytest.raises(ExternalFault, match="model_configuration_missing"):
            await provider.decide("hello", [], [], 128)


async def test_request_configuration_is_fixed_and_bounded():
    def respond(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        payload = json.loads(request.content)
        assert payload["store"] is False
        assert payload["parallel_tool_calls"] is False
        assert payload["max_output_tokens"] == 128
        return httpx.Response(200, json=response_body())

    settings = Settings(
        _env_file=None, openai_api_key="test-key", openai_model="explicit-test-model"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await OpenAIProvider(client, settings).decide("hello", [], [], 128)


@pytest.mark.parametrize(
    "status,code",
    [(429, "rate_limited"), (500, "provider_unavailable"), (400, "provider_invalid_request")],
)
async def test_provider_http_errors(status, code):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(status))
    ) as client:
        settings = Settings(_env_file=None, openai_api_key="test", openai_model="test")
        with pytest.raises(ExternalFault, match=code):
            await OpenAIProvider(client, settings).decide("hello", [], [], 128)


async def test_provider_timeout_is_classified():
    def timeout(request):
        raise httpx.ReadTimeout("secret upstream body must not escape")

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        provider = OpenAIProvider(
            client, Settings(_env_file=None, openai_api_key="test", openai_model="test")
        )
        with pytest.raises(ExternalFault, match="model_transport") as caught:
            await provider.decide("hello", [], [], 128)
        assert caught.value.transient is True
