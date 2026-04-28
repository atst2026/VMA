#!/usr/bin/env python3
"""Manual fortnightly sweep — same engine as the daily morning brief but
widened to a 14-day look-back. Use when you want a catch-up:
returning from leave, reviewing the fortnight, or just making sure
nothing slipped past the daily run.

Both sections (live roles + pre-advert predictors) are included.

Usage:
    python3 -m tool.sweep                  # preview
    python3 -m tool.sweep test             # send to amirt12@hotmail.com
    python3 -m tool.sweep send             # send to stehrani@vmagroup.com
"""
from __future__ import annotations
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Set sweep mode BEFORE importing source modules so they pick up the
# widened windows from the start.
os.environ.setdefault("VMA_SWEEP_DAYS", "14")

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool import config
from tool.email_send import send as email_send
from tool.predictive import cluster as pcluster, detector as pdet, ranker as pr
from tool.predictive import render as prender
from tool.predictive.stacker import stack as stack_events
from tool.ranking import rank
from tool.render import render_html, render_plaintext
from tool.sources import (
    bright_data, companies_house, gdelt, jobs, rss_feeds, sec_edgar,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("sweep")

STATE_DIR = _REPO_ROOT / "tool" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def fetch_all() -> dict:
    out: list[dict] = []
    report: dict[str, int] = {}

    def _tally(label: str, got: list[dict]):
        out.extend(got)
        report[label] = report.get(label, 0) + len(got)
        log.info("  %s → %d", label, len(got))

    log.info("Sweep mode active (window: %d days). Scouring sources…",
             config.sweep_days())
    for label, fn in [
        ("RSS (RNS + regulators + trade press + procurement)", rss_feeds.fetch_all),
        ("Job boards (Adzuna/Greenhouse/Lever/Ashby/LinkedIn public)", jobs.fetch_all),
        ("GDELT (global news graph)", gdelt.fetch_all),
        ("SEC EDGAR (8-K filings)", sec_edgar.fetch_all),
        ("Companies House", companies_house.to_signals),
        ("Bright Data (licensed LinkedIn surface)", bright_data.fetch_all),
    ]:
        try:
            _tally(label, fn())
        except Exception as e:
            log.exception("%s failed: %s", label, e)

    return {"signals": out, "report": report}


def covered_window() -> str:
    days = config.sweep_days()
    today = date.today()
    start = today - timedelta(days=days)
    return f"{start.strftime('%a %d %b')} → {today.strftime('%a %d %b')} ({days}-day catch-up sweep)"


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "preview").lower()
    days = config.sweep_days()
    log.info("Mode: %s · Window: %d days", mode, days)

    result = fetch_all()
    signals = result["signals"]
    report = result["report"]

    # NO state-based dedup. The sweep is for catch-up — Sara wants
    # everything in the window, including items that may have appeared
    # in past daily briefs. Only in-run dedup happens (rank() handles it).
    log.info("Scoured %d raw signals (no across-run dedup in sweep mode).",
             len(signals))

    ranked_live = rank(signals)
    log.info("Live-roles ranked: %d items.", len(ranked_live))

    # Predictive pipeline
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

    now = datetime.now()
    now_str = now.strftime("%A %d %B %Y · %H:%M")
    covered = covered_window()
    predictive_html = prender.render_html(ranked_stacks, limit=10)
    predictive_text = prender.render_text(ranked_stacks, limit=10)
    html = render_html(ranked_live, report, now_str, covered,
                       predictive_html=predictive_html)
    text = render_plaintext(ranked_live, now_str, covered,
                            predictive_text=predictive_text)

    # Persist (separate filenames so the daily brief's state isn't overwritten)
    (STATE_DIR / "latest_sweep.html").write_text(html)
    (STATE_DIR / "latest_sweep.txt").write_text(text)
    (STATE_DIR / "latest_sweep_signals.json").write_text(
        json.dumps(ranked_live, indent=2, default=str)
    )

    if mode in ("send", "test"):
        to = config.TEST_RECIPIENT if mode == "test" else config.RECIPIENT
        subject = f"[{days}-DAY SWEEP] Sara's Catch-up Brief — {now.strftime('%a %d %b')}"
        log.info("Sending to %s …", to)
        result = email_send(to, subject, html, text)
        log.info("Send result: %s", result)
        if not result.get("ok"):
            print("\n--- EMAIL SEND FAILED ---")
            print(result)
            print(f"\nSweep saved to {STATE_DIR/'latest_sweep.html'}")
            return 2
        print(f"✓ Sent to {to}.")
        return 0

    print(text)
    print(f"\n[sweep saved to {STATE_DIR/'latest_sweep.html'}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
