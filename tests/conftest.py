"""Shared fixtures: an in-memory store and a service factory with a recording clock."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Protocol

import pytest

from webhook_relay.sender import Sender
from webhook_relay.service import RelayService
from webhook_relay.store import SqliteStore


class RecordingSleeper:
    """A ``sleep`` stand-in that records requested delays instead of waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class FixedClock:
    """A monotonically advancing clock so ``updated_at`` values are deterministic."""

    def __init__(self, start: datetime | None = None, step_seconds: float = 1.0) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)
        self._step = timedelta(seconds=step_seconds)

    def __call__(self) -> datetime:
        current = self._now
        self._now = self._now + self._step
        return current


@pytest.fixture
def store() -> Iterator[SqliteStore]:
    s = SqliteStore(":memory:")
    try:
        yield s
    finally:
        s.close()


class ServiceFactory(Protocol):
    """Callable that builds a service (and its recording sleeper) for a sender."""

    def __call__(
        self,
        sender: Sender,
        *,
        max_attempts: int = ...,
        base_delay: float = ...,
        factor: float = ...,
    ) -> tuple[RelayService, RecordingSleeper]: ...


@pytest.fixture
def make_service(store: SqliteStore) -> ServiceFactory:
    """Factory: build a service around a given sender with fast, recorded time."""

    def _make(
        sender: Sender,
        *,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        factor: float = 2.0,
    ) -> tuple[RelayService, RecordingSleeper]:
        sleeper = RecordingSleeper()
        service = RelayService(
            store,
            sender,
            max_attempts=max_attempts,
            base_delay=base_delay,
            factor=factor,
            sleep=sleeper,
            clock=FixedClock(),
        )
        return service, sleeper

    return _make
