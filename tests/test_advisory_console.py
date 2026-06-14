"""The advisory console — the HTML board renderer and the additive
/advisory route (the visible lane in the live platform)."""
from datetime import date

import pytest

from tool import advisory_board as B
from tool.advisory_gate import assess
from tool.advisory_signals.base import AdvisorySignal

TODAY = date(2026, 6, 14)


@pytest.fixture(autouse=True)
def _comms_default(monkeypatch):
    monkeypatch.delenv("VMA_PROFILE", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)


def _pursue_row():
    sig = AdvisorySignal(
        trigger="PayGapActionMandate", company="Mercia Water Plc",
        service_mix=["edi", "benchmarking", "coaching"],
        pain="median gender pay gap 28.4%", buyer_hint="CHRO",
        evidence=[{"source": "GOV.UK", "url":
                   "https://gender-pay-gap.service.gov.uk/x"}],
        window=("2026-03-01", "2026-07-31"), confidence=0.8,
        extra={"size_band": "5000 to 19,999", "median": 28.4})
    return assess(sig, facts={"sponsor_name": "Priya Shah",
                              "warm_route": {"note": "placed her Head of IC"}},
                  today=TODAY)


# ----------------------------------------------------- HTML renderer

def test_html_empty_state():
    page = B.render_board_html([], today=TODAY)
    assert page.startswith("<!doctype html>")
    assert "Advisory Engine" in page
    assert "No advisory leads today" in page
    assert "/advisory" not in page or "href='/comms'" in page   # nav present


def test_html_renders_pursue_card_with_pack():
    page = B.render_board_html([_pursue_row()], today=TODAY, cap=5)
    assert "Call-ready (1)" in page
    assert "Mercia Water Plc" in page
    assert "PURSUE" in page
    assert "Evidence Pack" in page              # the collapsible pack
    assert "Network Rail" in page               # the proof anchor in the pack
    assert "Lucy Cairncross" in page            # routing owner


def test_html_escapes_company_names():
    sig = AdvisorySignal(trigger="PayGapActionMandate",
                         company="<script>x</script> Ltd",
                         service_mix=["edi"], pain="p",
                         evidence=[{"source": "GOV.UK", "url":
                                    "https://gender-pay-gap.service.gov.uk/x"}],
                         window=("2026-03-01", "2026-07-31"),
                         extra={"size_band": "1000 to 4999", "median": 20.0})
    page = B.render_board_html([assess(sig, facts={}, today=TODAY)])
    assert "<script>x</script> Ltd" not in page
    assert "&lt;script&gt;" in page


# ----------------------------------------------------- the live route

def test_advisory_route_serves_html():
    from tool.dashboard import app
    client = app.test_client()
    resp = client.get("/advisory")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert "Advisory Engine" in body
