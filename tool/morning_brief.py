#!/usr/bin/env python3
"""Sara's morning brief: scour → filter → rank → render → deliver.

Usage:
    python3 tool/morning_brief.py           # preview only
    python3 tool/morning_brief.py send      # live send to stehrani@vmagroup.com
    python3 tool/morning_brief.py test      # send to amirt12@hotmail.com (practice run)
"""
from __future__ import annotations
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Make the repo root importable no matter how this script was invoked.
# (Direct `python tool/morning_brief.py` puts tool/ on sys.path, not the repo
# root, so `from tool import config` would fail without this.)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool import config
from tool.email_send import send as email_send
from tool.predictive import cluster as pcluster, detector as pdet, ranker as pr, render as prender
from tool.ranking import rank
from tool.render import render_html, render_plaintext
from tool.sources import (
    bright_data, companies_house, gdelt, jobs, rss_feeds, sec_edgar,
)
from tool.state_store import filter_unseen
from tool.predictive.stacker import stack as stack_events

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("brief")

STATE_DIR = Path(__file__).resolve().parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def covered_window() -> str:
    """Human-readable description of the window this brief covers.
    Monday = Sat + Sun + Mon-to-now. Other weekdays = prior business day to now.
    """
    today = date.today()
    if today.weekday() == 0:   # Monday
        sat = today - timedelta(days=2)
        return f"{sat.strftime('%a %d %b')} → {today.strftime('%a %d %b')} (weekend + today)"
    yesterday = today - timedelta(days=1)
    return f"{yesterday.strftime('%a %d %b')} → {today.strftime('%a %d %b')}"


def run() -> dict:
    """Fetch from every source. Return {'signals': [...], 'report': {source: count}}."""
    all_signals: list[dict] = []
    report: dict[str, int] = {}

    def _tally(label: str, got: list[dict]):
        all_signals.extend(got)
        report[label] = report.get(label, 0) + len(got)
        log.info("  %s → %d", label, len(got))

    log.info("Scouring sources…")
    try:
        _tally("RSS (RNS + regulators + trade press + procurement)", rss_feeds.fetch_all())
    except Exception as e:
        log.exception("rss_feeds: %s", e)

    try:
        _tally("Job boards (Adzuna/Greenhouse/Lever/Ashby/LinkedIn public)", jobs.fetch_all())
    except Exception as e:
        log.exception("jobs: %s", e)

    try:
        _tally("GDELT (global news graph)", gdelt.fetch_all())
    except Exception as e:
        log.exception("gdelt: %s", e)

    try:
        _tally("SEC EDGAR (8-K filings)", sec_edgar.fetch_all())
    except Exception as e:
        log.exception("sec_edgar: %s", e)

    try:
        _tally("Companies House", companies_house.to_signals())
    except Exception as e:
        log.exception("companies_house: %s", e)

    try:
        _tally("Bright Data (licensed LinkedIn surface)", bright_data.fetch_all())
    except Exception as e:
        log.exception("bright_data: %s", e)

    return {"signals": all_signals, "report": report}


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "preview").lower()
    result = run()
    signals = result["signals"]
    report = result["report"]

    # Dedup across runs (honest state)
    fresh = filter_unseen(signals)
    log.info("Scoured %d raw signals; %d new since last run.", len(signals), len(fresh))

    ranked = rank(fresh)
    log.info("Ranked %d matching signals above the role-match threshold.", len(ranked))

    # Predictive pipeline: feed the raw (pre-filter) signals into trigger
    # detection, run the job-ad cluster detector off the rolling 30-day
    # state, stack by company, rank. This is a *parallel* track to the
    # live-roles ranking above — neither affects the other.
    log.info("Running predictive pipeline on %d raw signals…", len(signals))
    pcluster.ingest_jobs(signals)
    trigger_events = pdet.detect_events(signals)
    cluster_events = pcluster.detect_clusters()
    all_events = trigger_events + cluster_events
    stacks = stack_events(all_events)
    ranked_stacks = pr.rank(stacks)
    log.info(
        "Predictive: %d trigger events + %d cluster events → %d stacks → %d ranked.",
        len(trigger_events), len(cluster_events), len(stacks), len(ranked_stacks),
    )

    # Persist
    now = datetime.now()
    now_str = now.strftime("%A %d %B %Y · %H:%M")
    covered = covered_window()
    predictive_html = prender.render_html(ranked_stacks)
    predictive_text = prender.render_text(ranked_stacks)
    html = render_html(ranked, report, now_str, covered, predictive_html=predictive_html)
    text = render_plaintext(ranked, now_str, covered, predictive_text=predictive_text)
    (STATE_DIR / "latest_brief.html").write_text(html)
    (STATE_DIR / "latest_brief.txt").write_text(text)
    (STATE_DIR / "latest_signals.json").write_text(json.dumps(ranked, indent=2, default=str))
    (STATE_DIR / "latest_predictive.json").write_text(json.dumps([
        {
            "company": stk.company,
            "score": sc,
            "depth": stk.depth,
            "events": [
                {
                    "trigger_key": e.trigger_key,
                    "trigger_label": e.trigger_label,
                    "evidence": e.evidence,
                    "url": e.url,
                    "source": e.source_label,
                    "published": e.published.isoformat(),
                    "tier": e.tier_hint,
                }
                for e in stk.events
            ],
        }
        for stk, sc in ranked_stacks
    ], indent=2, default=str))

    # Deliver
    if mode in ("send", "test"):
        # Skip the send if there's literally nothing new to show. This is
        # what prevents the second cron of the day (the BST/GMT companion)
        # from blasting Sara with an empty 0-signal brief — dedup state
        # has already removed everything the first run sent.
        if not ranked and not ranked_stacks:
            log.info(
                "No new live signals and no predictive stacks. "
                "Skipping send to %s — Sara already received today's brief "
                "(or there's genuinely nothing today).",
                config.TEST_RECIPIENT if mode == "test" else config.RECIPIENT,
            )
            print("✓ No new content; skipping send.")
            return 0

        to = config.TEST_RECIPIENT if mode == "test" else config.RECIPIENT
        n_pred = len(ranked_stacks)
        subject = (
            f"Sara's Morning Brief — {now.strftime('%a %d %b')} "
            f"({len(ranked)} live · {n_pred} pre-advert)"
        )
        if mode == "test":
            subject = "[TEST] " + subject
        log.info("Sending to %s …", to)
        result = email_send(to, subject, html, text)
        log.info("Send result: %s", result)
        if not result.get("ok"):
            print("\n--- EMAIL SEND FAILED ---")
            print(result)
            print(f"\nBrief saved to {STATE_DIR/'latest_brief.html'}")
            return 2
        print(f"✓ Sent to {to}. Status {result.get('status')}.")
        return 0

    # preview
    print(text)
    print(f"\n[brief saved to {STATE_DIR/'latest_brief.html'}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
