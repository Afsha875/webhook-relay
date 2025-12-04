"""End-to-end demo: sign a payload, relay it through a flaky sender, print the log.

Run with ``python -m webhook_relay.demo``. Everything here is real code paths:
the same :class:`RelayService`, :class:`SqliteStore`, and signing helpers the HTTP
service uses. Only the transport is swapped for a :class:`FakeSender` that fails
the first attempt, so the output is deterministic and safe to quote in the README.
"""

from __future__ import annotations

import json

from webhook_relay.sender import FakeSender
from webhook_relay.service import RelayService
from webhook_relay.signing import compute_signature
from webhook_relay.store import SqliteStore


def main() -> None:
    # A no-op sleeper that records the backoff delays instead of waiting.
    recorded_delays: list[float] = []

    store = SqliteStore(":memory:")
    sender = FakeSender(fail_times=1)  # fail the first delivery, then succeed
    service = RelayService(
        store,
        sender,
        max_attempts=3,
        base_delay=1.0,
        factor=2.0,
        sleep=recorded_delays.append,
    )

    endpoint = service.register_endpoint(
        destination_url="https://example.test/webhooks/orders",
        secret="whsec_demo_31c0ffee",
    )

    payload = json.dumps({"type": "order.paid", "id": "ord_1001", "amount": 4200}).encode()
    signature = compute_signature(endpoint.secret, payload)

    result = service.ingest(
        endpoint_id=endpoint.id,
        body=payload,
        signature=signature,
        event_id="evt_1001",
    )
    delivery = result.delivery

    print("=== webhook-relay demo ===")
    print(f"endpoint id        : {endpoint.id}")
    print(f"destination        : {endpoint.destination_url}")
    print(f"payload bytes      : {len(payload)}")
    print(f"x-signature        : {signature[:16]}... ({len(signature)} hex chars)")
    print()
    print("--- delivery log ---")
    print(f"event id           : {delivery.event_id}")
    print(f"final status       : {delivery.status.value}")
    print(f"attempt count      : {delivery.attempt_count}")
    print(f"sender invocations : {len(sender.calls)}")
    print(f"backoff delays (s) : {recorded_delays}")
    print(f"last error         : {delivery.last_error}")
    print()

    # Idempotency: re-post the exact same event id -> no new work.
    replay = service.ingest(
        endpoint_id=endpoint.id,
        body=payload,
        signature=signature,
        event_id="evt_1001",
    )
    print("--- idempotent replay (same event id) ---")
    print(f"duplicate detected : {replay.duplicate}")
    print(f"sender invocations : {len(sender.calls)} (unchanged)")
    print(f"total deliveries   : {len(service.list_deliveries())}")


if __name__ == "__main__":
    main()
