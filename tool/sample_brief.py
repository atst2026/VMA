#!/usr/bin/env python3
"""Generate a sample brief with synthetic data — lets Sara see the full
email shape before real sources run. Exercises BOTH the live-roles
section AND the pre-advert predictive section so the layout is visible
even on days when real predictive news doesn't land.

Run:
    python3 tool/sample_brief.py               # preview
    python3 tool/sample_brief.py test          # send to amirt12@hotmail.com
    python3 tool/sample_brief.py send          # send to stehrani@vmagroup.com
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool import config
from tool.email_send import send
from tool.predictive import ranker as pr, render as prender
from tool.predictive.detector import TriggerEvent
from tool.predictive.stacker import stack as stack_events
from tool.ranking import rank
from tool.render import render_html, render_plaintext

STATE_DIR = Path(__file__).resolve().parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)


# -- Live-roles synthetic signals (same format as rss_feeds / jobs output)
SAMPLE_LIVE_SIGNALS = [
    {"id": "l1", "source": "LSE RNS (Investegate)", "kind": "rns",
     "title": "NatWest Group plc — Directorate Change",
     "url": "https://www.investegate.co.uk/natwest/directorate-change",
     "published": "2026-04-22T07:02:00+00:00", "company": "NatWest Group",
     "geo": "UK", "summary": "Board announces Head of Corporate Affairs departure.",
     "weight": 1.3},
    {"id": "l2", "source": "Adzuna (Indeed + aggregators)", "kind": "job",
     "title": "Head of Corporate Communications — AstraZeneca",
     "url": "https://www.adzuna.co.uk/jobs/4200001",
     "published": "2026-04-22T08:45:00+00:00", "company": "AstraZeneca",
     "geo": "UK", "summary": "Permanent. £110k–£135k. London/Cambridge.",
     "weight": 1.0},
    {"id": "l3", "source": "Greenhouse (monzo)", "kind": "job",
     "title": "Internal Communications Director",
     "url": "https://boards.greenhouse.io/monzo/jobs/123456",
     "published": "2026-04-22T09:00:00+00:00", "company": "monzo",
     "geo": "UK", "summary": "London — internal comms leadership.",
     "weight": 1.0},
    {"id": "l4", "source": "GDELT", "kind": "leadership_change",
     "title": "Unilever appoints new Chief Communications Officer",
     "url": "https://www.reuters.com/business/unilever-cco",
     "published": "2026-04-22T06:30:00+00:00", "company": "Unilever",
     "geo": "UK", "summary": "External hire from a US-listed peer.",
     "weight": 1.0},
    {"id": "l5", "source": "LinkedIn Jobs (public)", "kind": "job",
     "title": "Communications Director, EMEA",
     "url": "https://www.linkedin.com/jobs/view/3987654321",
     "published": "2026-04-22T05:00:00+00:00", "company": "Palo Alto Networks",
     "geo": "UK", "summary": "London. EMEA scope.", "weight": 1.1},
    {"id": "l6", "source": "PRWeek UK", "kind": "trade_press",
     "title": "Rolls-Royce hires ex-BAE head of corporate affairs",
     "url": "https://www.prweek.com/rolls-royce",
     "published": "2026-04-22T08:30:00+00:00", "company": "Rolls-Royce",
     "geo": "UK",
     "summary": "BAE Systems' Head of Corporate Affairs moves to Rolls-Royce; BAE role now open.",
     "weight": 1.0},
]


# -- Pre-advert synthetic trigger events (bypasses the detector, so we
# can show the rendered section in sample mode deterministically).
def _sample_predictive_stacks():
    now = datetime.now(timezone.utc)
    events = [
        # Stacked example: Barclays — CEO change + restructure
        TriggerEvent(
            trigger_key="ceo_change",
            trigger_label="CEO change",
            company="Barclays plc",
            evidence="Barclays announces the appointment of a new Chief Executive Officer effective Q3.",
            url="https://www.investegate.co.uk/barclays/ceo-change",
            source_label="LSE RNS (Investegate)",
            published=now, tier_hint="listed",
        ),
        TriggerEvent(
            trigger_key="restructure",
            trigger_label="Restructure / transformation announced",
            company="Barclays plc",
            evidence="Barclays has announced a strategic review of Corporate Affairs ahead of Q3.",
            url="https://www.campaignlive.co.uk/barclays-restructure",
            source_label="Campaign",
            published=now, tier_hint="covered",
        ),
        # Single — material regulator
        TriggerEvent(
            trigger_key="regulator_action",
            trigger_label="Material regulator action",
            company="Thames Water",
            evidence="Ofwat fines Thames Water £45m for repeated operational failings.",
            url="https://www.ofwat.gov.uk/thames-water-2026",
            source_label="Ofwat News",
            published=now, tier_hint="covered",
        ),
        # Single — M&A
        TriggerEvent(
            trigger_key="mna",
            trigger_label="M&A announcement",
            company="Haleon plc",
            evidence="Haleon plc announces recommended cash offer for a UK-listed peer.",
            url="https://www.investegate.co.uk/haleon/offer",
            source_label="LSE RNS (Investegate)",
            published=now, tier_hint="listed",
        ),
        # Single — job-ad cluster (Thames Water mechanic)
        TriggerEvent(
            trigger_key="job_ad_cluster",
            trigger_label="Job-ad cluster (2+ mid-level comms, no senior yet)",
            company="Severn Trent",
            evidence=("3 mid-level comms/PR roles posted at Severn Trent in the last 30 days; "
                      "no senior Head-of-Comms role currently posted. Titles: Communications Manager; "
                      "Senior Internal Comms; PR Manager"),
            url="https://www.linkedin.com/jobs/severn-trent",
            source_label="LinkedIn Jobs (public)",
            published=now, tier_hint="covered",
        ),
        # Single — CHRO change
        TriggerEvent(
            trigger_key="chro_change",
            trigger_label="CHRO / HR leadership change",
            company="BT",
            evidence="BT has announced the appointment of a new Chief People Officer.",
            url="https://www.hrmagazine.co.uk/bt-cpo",
            source_label="HR Magazine",
            published=now, tier_hint="covered",
        ),
    ]
    stacks = stack_events(events)
    return pr.rank(stacks)


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "preview").lower()
    ranked_live = rank(SAMPLE_LIVE_SIGNALS)
    ranked_pred = _sample_predictive_stacks()

    now = datetime.now()
    now_str = now.strftime("%A %d %B %Y · %H:%M")
    covered = "Tue 21 Apr → Wed 22 Apr (SAMPLE — synthetic data for layout preview)"

    predictive_html = prender.render_html(ranked_pred)
    predictive_text = prender.render_text(ranked_pred)

    html = render_html(
        ranked_live,
        {"LSE RNS": 1, "Adzuna": 1, "Greenhouse": 1, "GDELT": 1,
         "LinkedIn Jobs": 1, "PRWeek UK": 1, "Ofwat": 1, "Campaign": 1,
         "HR Magazine": 1},
        now_str, covered,
        predictive_html=predictive_html,
    )
    text = render_plaintext(ranked_live, now_str, covered,
                            predictive_text=predictive_text)

    (STATE_DIR / "sample_brief.html").write_text(html)
    (STATE_DIR / "sample_brief.txt").write_text(text)

    if mode in ("send", "test"):
        to = config.TEST_RECIPIENT if mode == "test" else config.RECIPIENT
        subject = f"[SAMPLE] Sara's Morning Brief — {now.strftime('%a %d %b')}"
        result = send(to, subject, html, text)
        if not result.get("ok"):
            print("Send failed:", result)
            return 2
        print(f"✓ Sample sent to {to} — status {result.get('status')}")
        return 0

    print(text)
    print(f"\n[HTML preview written to {STATE_DIR/'sample_brief.html'}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
