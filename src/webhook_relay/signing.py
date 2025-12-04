"""HMAC-SHA256 request signing and verification.

A sender computes ``compute_signature(secret, raw_body)`` and puts the hex digest
in the ``X-Signature`` header. The relay recomputes it over the raw body and
compares in constant time. Any change to the body or the secret changes the
digest, so tampered payloads are rejected.
"""

from __future__ import annotations

import hashlib
import hmac


def compute_signature(secret: str, body: bytes) -> str:
    """Return the hex HMAC-SHA256 of ``body`` keyed by ``secret``.

    This is the exact value a well-behaved sender must place in ``X-Signature``.
    """
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    """Return True iff ``signature`` matches the expected digest for ``body``.

    A missing or empty signature is always rejected. The comparison uses
    :func:`hmac.compare_digest` to avoid leaking timing information.
    """
    if not signature:
        return False
    expected = compute_signature(secret, body)
    return hmac.compare_digest(expected, signature)
