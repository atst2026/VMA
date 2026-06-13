"""Calendar pulses (tool/calendar_pulses.py) — date-window activation and
the advisory/service-fit decoration that rides on every active pulse.

Focus: the new equality-action-plan / ethnicity & disability pay-reporting
pulse fires only inside its window and carries the ED&I advisory reading.
"""
from datetime import date

import pytest

from tool import calendar_pulses as cp


@pytest.fixture(autouse=True)
def _comms_default(monkeypatch):
    monkeypatch.delenv("VMA_PROFILE", raising=False)


def _row(today, key):
    return next((r for r in cp.active_pulses(today=today) if r["key"] == key),
               None)


def test_equality_pulse_active_in_window_carries_edi_advisory():
    row = _row(date(2026, 6, 13), "equality_pay_reporting_2026")
    assert row is not None
    assert row["advisory"].startswith("Advisory:")
    assert row["service_fit"]["services"][0]["key"] == "edi"
    # Policy timeline still firming → medium, watch-list framing.
    assert row["confidence"] == "medium"
    assert "watch-list" in row["angle"].lower()


def test_equality_pulse_silent_before_window_opens():
    assert _row(date(2026, 1, 15), "equality_pay_reporting_2026") is None


def test_equality_pulse_silent_after_window_closes():
    assert _row(date(2026, 9, 1), "equality_pay_reporting_2026") is None
