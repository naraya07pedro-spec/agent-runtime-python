import httpx
import pytest

from app.domain import ExternalFault
from app.http_boundary import bounded_request

pytestmark = pytest.mark.contract


async def test_untrusted_upstream_response_has_size_limit():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"x" * 2048))
    ) as client:
        with pytest.raises(ExternalFault, match="provider_response_too_large"):
            await bounded_request(
                client, "GET", "https://example.test", headers={}, timeout_seconds=1, max_bytes=1024
            )


async def test_upstream_redirect_never_followed():
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as client:
        with pytest.raises(ExternalFault, match="provider_redirect_denied"):
            await bounded_request(
                client, "GET", "https://example.test", headers={}, timeout_seconds=1
            )
    assert calls == ["https://example.test"]
