"""Domain entities for the webhook relay.

These are plain, framework-free dataclasses. The API layer maps them to/from
Pydantic schemas; the storage layer maps them to/from SQLite rows. Keeping the
core domain free of FastAPI/SQLite imports is what makes the service unit-testable
without a running server or a real HTTP destination.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DeliveryStatus(str, Enum):
    """Lifecycle of a single event's delivery to its destination."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


@dataclass(frozen=True)
class Endpoint:
    """A registered destination: where relayed events go and the shared secret."""

    id: str
    destination_url: str
    secret: str
    created_at: datetime


@dataclass(frozen=True)
class Event:
    """An inbound webhook payload accepted at /ingest.

    ``id`` is the caller-supplied idempotency key (X-Event-Id). The raw ``body``
    bytes are stored verbatim so the exact signed payload can be re-signed and
    replayed on every retry.
    """

    id: str
    endpoint_id: str
    body: bytes
    received_at: datetime


@dataclass(frozen=True)
class Delivery:
    """The delivery record for one event: status, attempts, and next retry time."""

    id: str
    endpoint_id: str
    event_id: str
    status: DeliveryStatus
    attempt_count: int
    next_retry_at: datetime | None
    last_error: str | None
    updated_at: datetime
