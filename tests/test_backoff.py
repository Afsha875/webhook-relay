"""The documented exponential backoff schedule (1s, 2s, 4s, ...)."""

from __future__ import annotations

import pytest

from webhook_relay.backoff import backoff_delay, backoff_delays


def test_documented_schedule() -> None:
    assert backoff_delays(3) == [1.0, 2.0, 4.0]


def test_schedule_extends_geometrically() -> None:
    assert backoff_delays(5) == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_custom_base_and_factor() -> None:
    assert backoff_delays(3, base_delay=0.5, factor=3.0) == [0.5, 1.5, 4.5]


def test_single_delay_is_one_indexed() -> None:
    assert backoff_delay(1) == 1.0
    assert backoff_delay(2) == 2.0
    assert backoff_delay(3) == 4.0


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        backoff_delay(0)
    with pytest.raises(ValueError):
        backoff_delays(-1)
