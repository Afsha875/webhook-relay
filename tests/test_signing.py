"""HMAC signing/verification: the security boundary of the whole service."""

from __future__ import annotations

from webhook_relay.signing import compute_signature, verify_signature

SECRET = "whsec_test_9a8b7c"
BODY = b'{"type":"order.paid","id":"ord_42"}'


def test_signature_is_deterministic_sha256_hex() -> None:
    sig = compute_signature(SECRET, BODY)
    # HMAC-SHA256 hex digest is always 64 characters.
    assert len(sig) == 64
    assert all(c in "0123456789abcdef" for c in sig)
    assert compute_signature(SECRET, BODY) == sig


def test_correct_signature_is_accepted() -> None:
    sig = compute_signature(SECRET, BODY)
    assert verify_signature(SECRET, BODY, sig) is True


def test_tampered_body_is_rejected() -> None:
    sig = compute_signature(SECRET, BODY)
    tampered = BODY.replace(b"ord_42", b"ord_99")
    assert verify_signature(SECRET, tampered, sig) is False


def test_wrong_secret_is_rejected() -> None:
    sig = compute_signature(SECRET, BODY)
    assert verify_signature("whsec_wrong", BODY, sig) is False


def test_missing_signature_is_rejected() -> None:
    assert verify_signature(SECRET, BODY, None) is False
    assert verify_signature(SECRET, BODY, "") is False
