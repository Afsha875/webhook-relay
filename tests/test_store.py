"""Persistence round-trips: raw bytes, timestamps, and idempotency at the DB layer."""

from __future__ import annotations

from datetime import UTC, datetime

from webhook_relay.models import Delivery, DeliveryStatus, Endpoint, Event
from webhook_relay.store import SqliteStore


def _endpoint() -> Endpoint:
    return Endpoint(
        id="ep_1",
        destination_url="https://example.test/hook",
        secret="whsec_store",
        created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )


def test_endpoint_round_trip(store: SqliteStore) -> None:
    ep = _endpoint()
    store.add_endpoint(ep)
    assert store.get_endpoint("ep_1") == ep
    assert store.list_endpoints() == [ep]
    assert store.get_endpoint("missing") is None


def test_event_preserves_raw_bytes(store: SqliteStore) -> None:
    store.add_endpoint(_endpoint())
    raw = b'{"emoji":"\xf0\x9f\x9a\x80","n":1}'  # non-ascii bytes must survive verbatim
    event = Event(
        id="evt_1", endpoint_id="ep_1", body=raw, received_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    store.add_event(event)
    loaded = store.get_event("ep_1", "evt_1")
    assert loaded is not None
    assert loaded.body == raw


def test_delivery_update_persists(store: SqliteStore) -> None:
    store.add_endpoint(_endpoint())
    now = datetime(2026, 1, 1, tzinfo=UTC)
    delivery = Delivery(
        id="d_1",
        endpoint_id="ep_1",
        event_id="evt_1",
        status=DeliveryStatus.PENDING,
        attempt_count=0,
        next_retry_at=None,
        last_error=None,
        updated_at=now,
    )
    store.add_delivery(delivery)

    updated = Delivery(
        id="d_1",
        endpoint_id="ep_1",
        event_id="evt_1",
        status=DeliveryStatus.DELIVERED,
        attempt_count=2,
        next_retry_at=None,
        last_error=None,
        updated_at=datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
    )
    store.update_delivery(updated)

    loaded = store.get_delivery("ep_1", "evt_1")
    assert loaded == updated


def test_list_deliveries_filters_by_endpoint(store: SqliteStore) -> None:
    store.add_endpoint(_endpoint())
    store.add_endpoint(
        Endpoint(
            id="ep_2",
            destination_url="https://b.test",
            secret="s",
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
    )
    for ep_id, evt in [("ep_1", "a"), ("ep_1", "b"), ("ep_2", "c")]:
        store.add_delivery(
            Delivery(
                id=f"d_{ep_id}_{evt}",
                endpoint_id=ep_id,
                event_id=evt,
                status=DeliveryStatus.PENDING,
                attempt_count=0,
                next_retry_at=None,
                last_error=None,
                updated_at=datetime(2026, 1, 3, tzinfo=UTC),
            )
        )
    assert len(store.list_deliveries()) == 3
    assert len(store.list_deliveries("ep_1")) == 2
    assert len(store.list_deliveries("ep_2")) == 1
