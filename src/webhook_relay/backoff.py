"""Exponential backoff schedule.

The delay applied *before* the ``n``-th retry (1-indexed) is
``base_delay * factor ** (n - 1)``. With the defaults (base 1s, factor 2) the
schedule is 1s, 2s, 4s, 8s, ... The schedule is computed as pure data so tests
can assert the exact sequence without ever sleeping.
"""

from __future__ import annotations


def backoff_delay(attempt_index: int, base_delay: float = 1.0, factor: float = 2.0) -> float:
    """Delay in seconds before retry number ``attempt_index`` (1-indexed).

    ``attempt_index=1`` is the wait after the first failed attempt.
    """
    if attempt_index < 1:
        raise ValueError("attempt_index must be >= 1")
    return base_delay * factor ** (attempt_index - 1)


def backoff_delays(count: int, base_delay: float = 1.0, factor: float = 2.0) -> list[float]:
    """The first ``count`` backoff delays as a list, e.g. ``[1.0, 2.0, 4.0]``."""
    if count < 0:
        raise ValueError("count must be >= 0")
    return [backoff_delay(i, base_delay, factor) for i in range(1, count + 1)]
