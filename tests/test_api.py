"""HTTP-level tests through the real FastAPI app with a FakeSender behind it."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from webhook_relay.api import create_app
from webhook_relay.sender import FakeSender
from webhook_relay.service import RelayService
from webhook_relay.signing import compute_signature
from webhook_relay.store import SqliteStore

SECRET = "whsec_api_1234"
PAYLOAD = json.dumps({"type": "invoice.paid", "id": "inv_9"}).encode()


@pytest.fixture
def client() -> Iterator[TestClient]:
    store = SqliteStore(":memory:")
    sender = FakeSender(fail_times=1)  # exercise one retry through the HTTP path
    service = RelayService(store, sender, sleep=lambda _seconds: None)
    app = create_app(service)
    with TestClient(app) as c:
        c.app.state.sender = sender  # type: ignore[attr-defined]
        yield c
    store.close()


def _register(client: TestClient, secret: str = SECRET) -> str:
    resp = client.post(
        "/endpoints",
        json={"destination_url": "https://example.test/hook", "secret": secret},
    )
    assert resp.status_code == 201
    endpoint_id: str = resp.json()["id"]
    return endpoint_id


def test_register_and_list_endpoints(client: TestClient) -> None:
    endpoint_id = _register(client)
    listing = client.get("/endpoints").json()
    assert len(listing) == 1
    assert listing[0]["id"] == endpoint_id
    # The secret must never be exposed over the admin API.
    assert "secret" not in listing[0]


def test_ingest_accepts_valid_signature_and_delivers(client: TestClient) -> None:
    endpoint_id = _register(client)
    signature = compute_signature(SECRET, PAYLOAD)

    resp = client.post(
        f"/ingest/{endpoint_id}",
        content=PAYLOAD,
        headers={"X-Signature": signature, "X-Event-Id": "evt_http_1"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "delivered"
    assert body["attempt_count"] == 2  # failed once, then delivered
    assert body["duplicate"] is False


def test_ingest_rejects_bad_signature_with_401(client: TestClient) -> None:
    endpoint_id = _register(client)
    resp = client.post(
        f"/ingest/{endpoint_id}",
        content=PAYLOAD,
        headers={"X-Signature": "not-a-valid-signature", "X-Event-Id": "evt_bad"},
    )
    assert resp.status_code == 401


def test_ingest_rejects_missing_signature_with_401(client: TestClient) -> None:
    endpoint_id = _register(client)
    resp = client.post(f"/ingest/{endpoint_id}", content=PAYLOAD)
    assert resp.status_code == 401


def test_ingest_tampered_body_with_401(client: TestClient) -> None:
    endpoint_id = _register(client)
    signature = compute_signature(SECRET, PAYLOAD)
    tampered = PAYLOAD.replace(b"inv_9", b"inv_0")
    resp = client.post(
        f"/ingest/{endpoint_id}",
        content=tampered,
        headers={"X-Signature": signature, "X-Event-Id": "evt_tamper"},
    )
    assert resp.status_code == 401


def test_ingest_unknown_endpoint_with_404(client: TestClient) -> None:
    signature = compute_signature(SECRET, PAYLOAD)
    resp = client.post(
        "/ingest/nope",
        content=PAYLOAD,
        headers={"X-Signature": signature, "X-Event-Id": "evt_x"},
    )
    assert resp.status_code == 404


def test_ingest_is_idempotent_over_http(client: TestClient) -> None:
    endpoint_id = _register(client)
    signature = compute_signature(SECRET, PAYLOAD)
    headers = {"X-Signature": signature, "X-Event-Id": "evt_dupe"}

    first = client.post(f"/ingest/{endpoint_id}", content=PAYLOAD, headers=headers)
    second = client.post(f"/ingest/{endpoint_id}", content=PAYLOAD, headers=headers)

    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True

    deliveries = client.get("/deliveries", params={"endpoint_id": endpoint_id}).json()
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "delivered"


def test_delivery_log_reports_attempts_and_status(client: TestClient) -> None:
    endpoint_id = _register(client)
    signature = compute_signature(SECRET, PAYLOAD)
    client.post(
        f"/ingest/{endpoint_id}",
        content=PAYLOAD,
        headers={"X-Signature": signature, "X-Event-Id": "evt_log"},
    )

    deliveries = client.get("/deliveries").json()
    assert len(deliveries) == 1
    entry = deliveries[0]
    assert entry["event_id"] == "evt_log"
    assert entry["status"] == "delivered"
    assert entry["attempt_count"] == 2
    assert entry["next_retry_at"] is None
