"""Smoke test that the README demo actually runs and prints the delivery log."""

from __future__ import annotations

from _pytest.capture import CaptureFixture

from webhook_relay import demo


def test_demo_runs_and_reports_delivery(capsys: CaptureFixture[str]) -> None:
    demo.main()
    out = capsys.readouterr().out
    assert "final status       : delivered" in out
    assert "attempt count      : 2" in out
    assert "backoff delays (s) : [1.0]" in out
    assert "duplicate detected : True" in out
    assert "total deliveries   : 1" in out
