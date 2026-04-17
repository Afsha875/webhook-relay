"""Core relay behaviour: verification, retry/backoff, idempotency, replay integrity."""

from __future__ import annotations

import json

import pytest

from tests.conftest import ServiceFactory
from webhook_relay.models import DeliveryStatus
from webhook_relay.sender import FakeSender
from webhook_relay.service import InvalidSignatureError, UnknownEndpointError
from webhook_relay.signing import compute_signature

DESTINATION = "https://example.test/hook"
SECRET = "whsec_svc_c0ffee"
PAYLOAD = json.dumps({"type": "user.created", "id": "usr_7"}).encode()


def _sign(body: bytes = PAYLOAD, secret: str = SECRET) -> str:
    return compute_signature(secret, body)


def test_ingest_rejects_unknown_endpoint(make_service: ServiceFactory) -> None:
    service, _ = make_service(FakeSender())
    with pytest.raises(UnknownEndpointError):
        service.ingest("does-not-exist", PAYLOAD, _sign(), "evt_1")


def test_ingest_rejects_bad_signature(make_service: ServiceFactory) -> None:
    service, _ = make_service(FakeSender())
    endpoint = service.register_endpoint(DESTINATION, SECRET)
    with pytest.raises(InvalidSignatureError):
        service.ingest(endpoint.id, PAYLOAD, "deadbeef", "evt_1")


def test_ingest_rejects_missing_signature(make_service: ServiceFactory) -> None:
    service, _ = make_service(FakeSender())
    endpoint = service.register_endpoint(DESTINATION, SECRET)
    with pytest.raises(InvalidSignatureError):
        service.ingest(endpoint.id, PAYLOAD, None, "evt_1")


def test_delivers_first_try_when_sender_healthy(make_service: ServiceFactory) -> None:
    sender = FakeSender(fail_times=0)
    service, sleeper = make_service(sender)
    endpoint = service.register_endpoint(DESTINATION, SECRET)

    result = service.ingest(endpoint.id, PAYLOAD, _sign(), "evt_ok")

    assert result.delivery.status is DeliveryStatus.DELIVERED
    assert result.delivery.attempt_count == 1
    assert len(sender.calls) == 1
    assert sleeper.delays == []  # no retries -> no backoff waits


def test_retries_twice_then_delivers_with_backoff(make_service: ServiceFactory) -> None:
    sender = FakeSender(fail_times=2)
    service, sleeper = make_service(sender, max_attempts=3)
    endpoint = service.register_endpoint(DESTINATION, SECRET)

    result = service.ingest(endpoint.id, PAYLOAD, _sign(), "evt_retry")

    assert result.delivery.status is DeliveryStatus.DELIVERED
    assert result.delivery.attempt_count == 3
    assert len(sender.calls) == 3
    # Two failures -> two backoff waits following the 1s, 2s schedule.
    assert sleeper.delays == [1.0, 2.0]
    assert result.delivery.next_retry_at is None
    assert result.delivery.last_error is None


def test_permanent_failure_ends_failed_after_max_attempts(make_service: ServiceFactory) -> None:
    sender = FakeSender(always_fail=True)
    service, sleeper = make_service(sender, max_attempts=3)
    endpoint = service.register_endpoint(DESTINATION, SECRET)

    result = service.ingest(endpoint.id, PAYLOAD, _sign(), "evt_dead")

    assert result.delivery.status is DeliveryStatus.FAILED
    assert result.delivery.attempt_count == 3
    assert len(sender.calls) == 3
    assert sleeper.delays == [1.0, 2.0]  # waits only between attempts, not after the last
    assert result.delivery.next_retry_at is None
    assert result.delivery.last_error is not None


def test_backoff_respects_custom_base_and_factor(make_service: ServiceFactory) -> None:
    sender = FakeSender(fail_times=3)
    service, sleeper = make_service(sender, max_attempts=4, base_delay=0.5, factor=3.0)
    endpoint = service.register_endpoint(DESTINATION, SECRET)

    result = service.ingest(endpoint.id, PAYLOAD, _sign(), "evt_custom")

    assert result.delivery.status is DeliveryStatus.DELIVERED
    assert result.delivery.attempt_count == 4
    assert sleeper.delays == [0.5, 1.5, 4.5]


def test_idempotent_repost_does_not_duplicate(make_service: ServiceFactory) -> None:
    sender = FakeSender(fail_times=0)
    service, _ = make_service(sender)
    endpoint = service.register_endpoint(DESTINATION, SECRET)

    first = service.ingest(endpoint.id, PAYLOAD, _sign(), "evt_dupe")
    second = service.ingest(endpoint.id, PAYLOAD, _sign(), "evt_dupe")

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.delivery.id == first.delivery.id
    assert len(sender.calls) == 1  # the replay never re-delivers
    assert len(service.list_deliveries()) == 1


def test_relayed_body_and_signature_match_the_original(make_service: ServiceFactory) -> None:
    sender = FakeSender(fail_times=0)
    service, _ = make_service(sender)
    endpoint = service.register_endpoint(DESTINATION, SECRET)

    service.ingest(endpoint.id, PAYLOAD, _sign(), "evt_bytes")

    sent = sender.calls[0]
    assert sent.url == DESTINATION
    assert sent.body == PAYLOAD
    # The relay re-signs with the endpoint secret; downstream can verify it too.
    assert sent.signature == compute_signature(SECRET, PAYLOAD)


def test_event_id_defaults_to_body_signature_when_omitted(make_service: ServiceFactory) -> None:
    sender = FakeSender(fail_times=0)
    service, _ = make_service(sender)
    endpoint = service.register_endpoint(DESTINATION, SECRET)

    # No explicit event id on either post -> both derive the same id -> idempotent.
    first = service.ingest(endpoint.id, PAYLOAD, _sign(), None)
    second = service.ingest(endpoint.id, PAYLOAD, _sign(), None)

    assert first.duplicate is False
    assert second.duplicate is True
    assert len(service.list_deliveries()) == 1


def test_deliveries_are_listed_per_endpoint(make_service: ServiceFactory) -> None:
    sender = FakeSender(fail_times=0)
    service, _ = make_service(sender)
    ep_a = service.register_endpoint(DESTINATION, SECRET)
    ep_b = service.register_endpoint("https://other.test/hook", "whsec_b")

    service.ingest(ep_a.id, PAYLOAD, _sign(), "a1")
    service.ingest(ep_b.id, PAYLOAD, compute_signature("whsec_b", PAYLOAD), "b1")

    assert len(service.list_deliveries()) == 2
    assert len(service.list_deliveries(ep_a.id)) == 1
    assert service.list_deliveries(ep_a.id)[0].endpoint_id == ep_a.id
