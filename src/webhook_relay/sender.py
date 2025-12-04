"""The delivery transport boundary.

:class:`Sender` is the single seam between the relay logic and the outside
world. Production uses :class:`HttpxSender`; tests use :class:`FakeSender`, which
can be scripted to fail a set number of times before succeeding. Because every
retry decision flows through this interface, the retry/backoff behaviour is fully
testable offline without a real HTTP server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class DeliveryRequest:
    """Everything needed to relay one event to a destination."""

    url: str
    body: bytes
    signature: str
    event_id: str


@dataclass(frozen=True)
class DeliveryResult:
    """Outcome of a single delivery attempt."""

    ok: bool
    status_code: int | None = None
    error: str | None = None


@runtime_checkable
class Sender(Protocol):
    """Transport that pushes a signed payload to a destination URL."""

    def send(self, request: DeliveryRequest) -> DeliveryResult:
        """Attempt one delivery. Never raises for a network/HTTP failure."""
        ...


class HttpxSender:
    """Real HTTP sender. Relays the signed body via POST and treats any 2xx as success."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def send(self, request: DeliveryRequest) -> DeliveryResult:
        import httpx

        headers = {
            "Content-Type": "application/json",
            "X-Signature": request.signature,
            "X-Event-Id": request.event_id,
        }
        try:
            response = httpx.post(
                request.url,
                content=request.body,
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            return DeliveryResult(ok=False, error=f"transport error: {exc}")
        if 200 <= response.status_code < 300:
            return DeliveryResult(ok=True, status_code=response.status_code)
        return DeliveryResult(
            ok=False,
            status_code=response.status_code,
            error=f"destination returned {response.status_code}",
        )


@dataclass
class FakeSender:
    """Deterministic in-memory sender for tests and the demo.

    - ``fail_times``: fail the first N attempts, then succeed.
    - ``always_fail``: fail every attempt (simulates a permanently dead endpoint).

    Every attempt is recorded in :attr:`calls` so tests can assert the exact
    payload and count.
    """

    fail_times: int = 0
    always_fail: bool = False
    calls: list[DeliveryRequest] = field(default_factory=list)

    def send(self, request: DeliveryRequest) -> DeliveryResult:
        self.calls.append(request)
        attempt = len(self.calls)  # 1-indexed
        if self.always_fail:
            return DeliveryResult(
                ok=False, error=f"simulated permanent failure (attempt {attempt})"
            )
        if attempt <= self.fail_times:
            return DeliveryResult(ok=False, error=f"simulated failure (attempt {attempt})")
        return DeliveryResult(ok=True, status_code=200)
