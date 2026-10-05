import re

import httpx

from app.domain import JSON, ExternalFault
from app.reliability import check_response


async def bounded_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    timeout_seconds: float,
    body: JSON | None = None,
    max_bytes: int = 65536,
    response_headers: dict[str, str] | None = None,
) -> bytes:
    """Bound decompressed response bytes; do not trust Content-Length or follow redirects."""
    async with client.stream(
        method, url, headers=headers, json=body, timeout=timeout_seconds, follow_redirects=False
    ) as response:
        check_response(response)
        request_id = response.headers.get("x-github-request-id", "")
        if response_headers is not None and re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", request_id):
            response_headers["request_id"] = request_id
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > max_bytes:
                raise ExternalFault("provider_response_too_large", ambiguous=True)
        return bytes(data)
