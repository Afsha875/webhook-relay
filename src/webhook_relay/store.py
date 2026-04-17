"""SQLite-backed persistence for endpoints, events, and deliveries.

The store is a thin repository: it knows rows, not business rules. Datetimes are
stored as ISO-8601 UTC strings; event bodies are stored as raw BLOBs so the exact
signed bytes survive a round-trip. Idempotency is enforced at the schema level:
``(endpoint_id, event_id)`` is the primary key of both ``events`` and
``deliveries``, so a re-posted event can never create a second row.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime

from webhook_relay.models import Delivery, DeliveryStatus, Endpoint, Event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS endpoints (
    id             TEXT PRIMARY KEY,
    destination_url TEXT NOT NULL,
    secret         TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    endpoint_id TEXT NOT NULL,
    id          TEXT NOT NULL,
    body        BLOB NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (endpoint_id, id),
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id)
);

CREATE TABLE IF NOT EXISTS deliveries (
    id            TEXT NOT NULL,
    endpoint_id   TEXT NOT NULL,
    event_id      TEXT NOT NULL,
    status        TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    next_retry_at TEXT,
    last_error    TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (endpoint_id, event_id),
    FOREIGN KEY (endpoint_id) REFERENCES endpoints(id)
);
"""


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


class SqliteStore:
    """Repository over a single SQLite connection, guarded by a lock."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- endpoints ---------------------------------------------------------

    def add_endpoint(self, endpoint: Endpoint) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO endpoints (id, destination_url, secret, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    endpoint.id,
                    endpoint.destination_url,
                    endpoint.secret,
                    _iso(endpoint.created_at),
                ),
            )
            self._conn.commit()

    def get_endpoint(self, endpoint_id: str) -> Endpoint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM endpoints WHERE id = ?", (endpoint_id,)
            ).fetchone()
        return self._endpoint_from_row(row) if row is not None else None

    def list_endpoints(self) -> list[Endpoint]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM endpoints ORDER BY created_at").fetchall()
        return [self._endpoint_from_row(row) for row in rows]

    # -- events ------------------------------------------------------------

    def add_event(self, event: Event) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (endpoint_id, id, body, received_at) VALUES (?, ?, ?, ?)",
                (event.endpoint_id, event.id, event.body, _iso(event.received_at)),
            )
            self._conn.commit()

    def get_event(self, endpoint_id: str, event_id: str) -> Event | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM events WHERE endpoint_id = ? AND id = ?",
                (endpoint_id, event_id),
            ).fetchone()
        return self._event_from_row(row) if row is not None else None

    # -- deliveries --------------------------------------------------------

    def add_delivery(self, delivery: Delivery) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO deliveries "
                "(id, endpoint_id, event_id, status, attempt_count, next_retry_at, "
                "last_error, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                self._delivery_params(delivery),
            )
            self._conn.commit()

    def update_delivery(self, delivery: Delivery) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE deliveries SET status = ?, attempt_count = ?, next_retry_at = ?, "
                "last_error = ?, updated_at = ? WHERE endpoint_id = ? AND event_id = ?",
                (
                    delivery.status.value,
                    delivery.attempt_count,
                    _iso(delivery.next_retry_at) if delivery.next_retry_at else None,
                    delivery.last_error,
                    _iso(delivery.updated_at),
                    delivery.endpoint_id,
                    delivery.event_id,
                ),
            )
            self._conn.commit()

    def get_delivery(self, endpoint_id: str, event_id: str) -> Delivery | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM deliveries WHERE endpoint_id = ? AND event_id = ?",
                (endpoint_id, event_id),
            ).fetchone()
        return self._delivery_from_row(row) if row is not None else None

    def list_deliveries(self, endpoint_id: str | None = None) -> list[Delivery]:
        with self._lock:
            if endpoint_id is None:
                rows = self._conn.execute("SELECT * FROM deliveries ORDER BY updated_at").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM deliveries WHERE endpoint_id = ? ORDER BY updated_at",
                    (endpoint_id,),
                ).fetchall()
        return [self._delivery_from_row(row) for row in rows]

    # -- row mapping -------------------------------------------------------

    @staticmethod
    def _endpoint_from_row(row: sqlite3.Row) -> Endpoint:
        created = _parse_dt(row["created_at"])
        assert created is not None
        return Endpoint(
            id=row["id"],
            destination_url=row["destination_url"],
            secret=row["secret"],
            created_at=created,
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> Event:
        received = _parse_dt(row["received_at"])
        assert received is not None
        body = row["body"]
        return Event(
            id=row["id"],
            endpoint_id=row["endpoint_id"],
            body=bytes(body),
            received_at=received,
        )

    @staticmethod
    def _delivery_from_row(row: sqlite3.Row) -> Delivery:
        updated = _parse_dt(row["updated_at"])
        assert updated is not None
        return Delivery(
            id=row["id"],
            endpoint_id=row["endpoint_id"],
            event_id=row["event_id"],
            status=DeliveryStatus(row["status"]),
            attempt_count=row["attempt_count"],
            next_retry_at=_parse_dt(row["next_retry_at"]),
            last_error=row["last_error"],
            updated_at=updated,
        )

    @staticmethod
    def _delivery_params(
        delivery: Delivery,
    ) -> tuple[str, str, str, str, int, str | None, str | None, str]:
        return (
            delivery.id,
            delivery.endpoint_id,
            delivery.event_id,
            delivery.status.value,
            delivery.attempt_count,
            _iso(delivery.next_retry_at) if delivery.next_retry_at else None,
            delivery.last_error,
            _iso(delivery.updated_at),
        )
