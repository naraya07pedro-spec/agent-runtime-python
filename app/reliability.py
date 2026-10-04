import math
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.domain import ExternalFault


def retry_after(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - (now or datetime.now(UTC))).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def check_response(response: httpx.Response) -> None:
    status = response.status_code
    if 200 <= status < 300:
        return
    if status == 429:
        raise ExternalFault(
            "rate_limited",
            transient=True,
            retry_after=retry_after(response.headers.get("retry-after")),
        )
    if status in {401, 403}:
        raise ExternalFault("provider_authorization")
    if status >= 500 or status in {408, 425}:
        raise ExternalFault("provider_unavailable", transient=True, ambiguous=True)
    if 300 <= status < 400:
        raise ExternalFault("provider_redirect_denied")
    raise ExternalFault("provider_invalid_request")


def backoff(
    attempt: int, base: float, suggested: float | None = None, jitter: float | None = None
) -> float:
    # Never shorten Retry-After. The persisted execution deadline bounds long waits.
    delay = min(60.0, base * (2 ** min(attempt, 10)))
    delay *= 0.5 + (random.random() if jitter is None else jitter) * 0.5
    return float(max(delay, suggested or 0.0))
