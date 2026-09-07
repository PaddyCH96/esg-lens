"""Behavioural rate-limit tests — UAT 01-collectors #4.

WHY THIS FILE EXISTS
--------------------
`test_http_client.py::test_token_bucket_exists_and_not_lock_only` asserts that the string
"TokenBucketTransport" appears in http.py. That is a grep, not a test: it passes even if the
limiter never limits anything. UAT #4 requires "observed request rates stay within 10/s for
EDGAR and 1/s for GDELT", which can only be established by measuring elapsed time.

Getting this wrong has a real consequence: SEC blocks the source IP for exceeding 10 req/s.
That is the single most expensive failure mode in Phase 1, and it is not detectable from source
inspection.

These tests drive TokenBucketTransport directly with a stub inner transport, so they measure the
limiter and nothing else — no network, no sockets, no flakiness from real I/O.
"""

from __future__ import annotations

import time

import httpx
import pytest

from esg_lens.collectors.http import TokenBucketTransport
from esg_lens.config import settings


class _StubTransport(httpx.AsyncBaseTransport):
    """Inner transport that returns instantly, so elapsed time is purely the limiter."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    async def aclose(self) -> None:  # pragma: no cover - nothing to close
        return None


async def _fire(transport: TokenBucketTransport, url: str, n: int) -> float:
    start = time.monotonic()
    for _ in range(n):
        await transport.handle_async_request(httpx.Request("GET", url))
    return time.monotonic() - start


@pytest.mark.asyncio
async def test_gdelt_limited_to_one_request_per_second():
    """4 requests at 1/s: one free token, then three ~1s waits. Must take >= ~3s."""
    stub = _StubTransport()
    tb = TokenBucketTransport(stub, {"api.gdeltproject.org": 1})

    elapsed = await _fire(tb, "https://api.gdeltproject.org/api/v2/doc/doc", 4)

    assert len(stub.calls) == 4
    assert elapsed >= 2.7, f"4 requests at 1/s took only {elapsed:.2f}s — limiter is not limiting"
    observed_rate = 4 / elapsed
    assert observed_rate <= 1.5, f"observed {observed_rate:.2f} req/s exceeds the 1/s GDELT budget"


@pytest.mark.asyncio
async def test_edgar_limited_to_ten_requests_per_second():
    """15 requests at 10/s: ten free tokens, then five ~0.1s waits. SEC blocks above 10/s."""
    stub = _StubTransport()
    tb = TokenBucketTransport(stub, {"sec.gov": 10})

    elapsed = await _fire(tb, "https://data.sec.gov/submissions/CIK0000320193.json", 15)

    assert len(stub.calls) == 15
    assert elapsed >= 0.4, f"15 requests at 10/s took only {elapsed:.2f}s — limiter is not limiting"


@pytest.mark.asyncio
async def test_buckets_are_per_host_not_global():
    """A slow GDELT bucket must not throttle EDGAR. If buckets were global, the 1/s GDELT
    rate would drag EDGAR down to 1/s too and Phase 1 collection would take forever."""
    stub = _StubTransport()
    tb = TokenBucketTransport(stub, {"sec.gov": 10, "api.gdeltproject.org": 1})

    await tb.handle_async_request(httpx.Request("GET", "https://api.gdeltproject.org/x"))
    elapsed = await _fire(tb, "https://data.sec.gov/y", 5)

    assert elapsed < 0.3, f"EDGAR took {elapsed:.2f}s after a GDELT call — buckets are shared"


@pytest.mark.asyncio
async def test_unknown_host_still_gets_a_finite_rate():
    """An unmapped host must not divide by zero or run unthrottled forever."""
    stub = _StubTransport()
    tb = TokenBucketTransport(stub, {"sec.gov": 10})
    elapsed = await _fire(tb, "https://example.com/z", 3)
    assert len(stub.calls) == 3
    assert elapsed < 30, "unknown host appears to have an unusably small rate"


def test_user_agent_carries_a_real_contact_email():
    """SEC requires a genuine contact address, not merely a string containing '@'.

    The USER_AGENT validator in config.py only checks for '@', so the shipped default
    'esg-lens@example.com' satisfies it while still violating SEC's actual policy. This test
    fails on the known placeholder domains so the gap is visible rather than silent.
    """
    ua = settings.USER_AGENT
    assert "@" in ua, "USER_AGENT must contain a contact email (SEC EDGAR requirement)"
    placeholder = ("example.com", "example.org", "you@", "changeme", "test@test")
    offending = [p for p in placeholder if p in ua.lower()]
    assert not offending, (
        f"USER_AGENT still uses a placeholder contact {offending}: {ua!r}. "
        "SEC EDGAR requires a real address — copy .env.example to .env and set CONTACT_EMAIL "
        "before running any live collection."
    )
