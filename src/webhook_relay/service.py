"""Relay orchestration: register, ingest, verify, and deliver with retries.

This is the heart of the service and it is deliberately framework-free. Every
external dependency is injected:

* :class:`~webhook_relay.store.SqliteStore` for persistence,
* a :class:`~webhook_relay.sender.Sender` for the actual delivery, and
* a ``sleep`` callable and a ``clock`` callable for time.

Injecting ``sleep`` is what lets the retry loop run at full speed in tests while
still recording the *exact* backoff delays it would have waited in production.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from webhook_relay.backoff import backoff_delay
from webhook_relay.models import Delivery, DeliveryStatus, Endpoint, Event
from webhook_relay.sender import DeliveryRequest, Sender
from webhook_relay.signing import compute_signature, verify_signature
from webhook_relay.store import SqliteStore

Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class UnknownEndpointError(Exception):
    """Raised when ingesting for an endpoint id that was never registered."""


class InvalidSignatureError(Exception):
    """Raised when the X-Signature header is missing or does not match the body."""


@dataclass(frozen=True)
class IngestResult:
    """Outcome of an ingest call.

    ``delivery`` is the final (or in-progress) delivery record. ``duplicate`` is
    True when the event id was already seen, in which case no new work was done.
    """

    delivery: Delivery
    duplicate: bool


class RelayService:
    """Coordinates signature verification, storage, and retrying delivery."""

    def __init__(
        self,
        store: SqliteStore,
        sender: Sender,
        *,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        factor: float = 2.0,
        sleep: Sleeper = time.sleep,
        clock: Clock = _utc_now,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._store = store
        self._sender = sender
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._factor = factor
        self._sleep = sleep
        self._clock = clock

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    # -- admin -------------------------------------------------------------

    def register_endpoint(self, destination_url: str, secret: str) -> Endpoint:
        endpoint = Endpoint(
            id=uuid.uuid4().hex,
            destination_url=destination_url,
            secret=secret,
            created_at=self._clock(),
        )
        self._store.add_endpoint(endpoint)
        return endpoint

    def list_endpoints(self) -> list[Endpoint]:
        return self._store.list_endpoints()

    def get_endpoint(self, endpoint_id: str) -> Endpoint | None:
        return self._store.get_endpoint(endpoint_id)

    def list_deliveries(self, endpoint_id: str | None = None) -> list[Delivery]:
        return self._store.list_deliveries(endpoint_id)

    # -- ingest ------------------------------------------------------------

    def ingest(
        self,
        endpoint_id: str,
        body: bytes,
        signature: str | None,
        event_id: str | None = None,
    ) -> IngestResult:
        """Verify, persist, and relay one inbound webhook.

        Raises :class:`UnknownEndpointError` for an unregistered endpoint and
        :class:`InvalidSignatureError` for a bad/missing signature. Re-posting an
        event id that was already accepted is a no-op (idempotent) and returns the
        existing delivery with ``duplicate=True``.
        """
        endpoint = self._store.get_endpoint(endpoint_id)
        if endpoint is None:
            raise UnknownEndpointError(endpoint_id)

        if not verify_signature(endpoint.secret, body, signature):
            raise InvalidSignatureError(endpoint_id)

        resolved_event_id = event_id or compute_signature(endpoint.secret, body)

        existing = self._store.get_delivery(endpoint_id, resolved_event_id)
        if existing is not None:
            return IngestResult(delivery=existing, duplicate=True)

        now = self._clock()
        self._store.add_event(
            Event(
                id=resolved_event_id,
                endpoint_id=endpoint_id,
                body=body,
                received_at=now,
            )
        )
        self._store.add_delivery(
            Delivery(
                id=uuid.uuid4().hex,
                endpoint_id=endpoint_id,
                event_id=resolved_event_id,
                status=DeliveryStatus.PENDING,
                attempt_count=0,
                next_retry_at=None,
                last_error=None,
                updated_at=now,
            )
        )
        delivery = self.deliver_with_retries(endpoint_id, resolved_event_id)
        return IngestResult(delivery=delivery, duplicate=False)

    # -- delivery ----------------------------------------------------------

    def attempt_delivery(self, endpoint_id: str, event_id: str) -> Delivery:
        """Perform exactly one delivery attempt and update the delivery record.

        Terminal deliveries (delivered/failed) are returned unchanged, which makes
        repeated calls safe and idempotent.
        """
        delivery = self._store.get_delivery(endpoint_id, event_id)
        if delivery is None:
            raise KeyError(f"no delivery for {endpoint_id}/{event_id}")
        if delivery.status is not DeliveryStatus.PENDING:
            return delivery

        endpoint = self._store.get_endpoint(endpoint_id)
        event = self._store.get_event(endpoint_id, event_id)
        if endpoint is None or event is None:
            raise KeyError(f"missing endpoint/event for {endpoint_id}/{event_id}")

        signature = compute_signature(endpoint.secret, event.body)
        result = self._sender.send(
            DeliveryRequest(
                url=endpoint.destination_url,
                body=event.body,
                signature=signature,
                event_id=event_id,
            )
        )

        attempt_count = delivery.attempt_count + 1
        now = self._clock()

        if result.ok:
            updated = replace(
                delivery,
                status=DeliveryStatus.DELIVERED,
                attempt_count=attempt_count,
                next_retry_at=None,
                last_error=None,
                updated_at=now,
            )
        elif attempt_count >= self._max_attempts:
            updated = replace(
                delivery,
                status=DeliveryStatus.FAILED,
                attempt_count=attempt_count,
                next_retry_at=None,
                last_error=result.error,
                updated_at=now,
            )
        else:
            delay = backoff_delay(attempt_count, self._base_delay, self._factor)
            updated = replace(
                delivery,
                status=DeliveryStatus.PENDING,
                attempt_count=attempt_count,
                next_retry_at=now + timedelta(seconds=delay),
                last_error=result.error,
                updated_at=now,
            )

        self._store.update_delivery(updated)
        return updated

    def deliver_with_retries(self, endpoint_id: str, event_id: str) -> Delivery:
        """Attempt delivery, sleeping the backoff schedule between retries.

        The injected ``sleep`` receives the computed backoff delay; in tests it is
        a no-op recorder, so the exact 1s/2s/4s sequence is asserted without any
        real waiting.
        """
        delivery = self.attempt_delivery(endpoint_id, event_id)
        while delivery.status is DeliveryStatus.PENDING:
            delay = backoff_delay(delivery.attempt_count, self._base_delay, self._factor)
            self._sleep(delay)
            delivery = self.attempt_delivery(endpoint_id, event_id)
        return delivery
