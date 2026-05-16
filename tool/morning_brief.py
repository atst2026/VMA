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
from tool.predictive import cluster as pcluster, detector as pdet, ranker as pr, render as prender, velocity as pvelocity
from tool.ranking import rank
from tool.render import render_html, render_plaintext
from tool.sources import (
    bright_data, companies_house, gdelt, jobs, rss_feeds, sec_edgar,
)
from tool.state_store import filter_unseen
from tool.predictive.stacker import stack as stack_events
from tool import linkedin_resolver as lnr
from tool import predictor_pipeline

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

    # Predictive trigger feed — 90-day sweep of CEO/CFO/Chair changes,
    # M&A, IPO, regulator action, contract loss, comms-leader
    # departures. Feeds the predictive detector; doesn't reach the
    # live-leads ranker (kind='news', no salary/title to score).
    try:
        _tally("GDELT predictive (90-day trigger events)",
               gdelt.fetch_predictive_signals())
    except Exception as e:
        log.exception("gdelt predictive: %s", e)

    try:
        _tally("SEC EDGAR (8-K filings)", sec_edgar.fetch_all())
    except Exception as e:
        log.exception("sec_edgar: %s", e)

    # Companies House emits TriggerEvents directly (see pipeline below);
    # the back-compat to_signals() call returns [] now and is kept only
    # so the source tally line is preserved in the brief footer.
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

    # Rank EVERYTHING first — this is what goes to the dashboard so Sara
    # sees all current leads, not just ones-new-since-yesterday. Dedup
    # is only used to filter the EMAIL (which still goes fresh-only so
    # Sara isn't spammed daily with the same items).
    ranked_all = rank(signals)
    log.info("Ranked %d total matching signals (all-time view for dashboard).",
             len(ranked_all))

    # Dedup for the email body only
    fresh = filter_unseen(signals)
    log.info("Scoured %d raw signals; %d new since last run (email-fresh).",
             len(signals), len(fresh))
    ranked = rank(fresh)
    log.info("Ranked %d fresh signals for the email body.", len(ranked))

    # ORDER MATTERS: run CH officer-change scan + auto-update BEFORE
    # enriching signals with seeded names. Otherwise we'd enrich with
    # pre-update data and write the now-known-wrong name to today's
    # latest_signals.json. Auto-update mutates hiring_contacts.json on
    # disk; the subsequent enrichment loop reloads the freshly-updated
    # table.
    log.info("Running CH officer-change scan (pre-enrichment for fresh contacts)…")
    try:
        # Bounded so the ~550-company watchlist can't stall the whole job
        # (which previously prevented the Actions cache — and therefore
        # the resolver cache — from ever being saved). Rotating cursor in
        # detect_officer_changes covers the full list across a few runs;
        # once the resolver cache is warm, cached lookups are ~free and
        # coverage is effectively daily again.
        ch_events = companies_house.detect_officer_changes(
            max_companies=180, time_budget_s=420,
        )
    except Exception as e:
        log.exception("CH officer-change scan: %s", e)
        ch_events = []

    try:
        from tool.contacts import auto_update as cau
        from tool.contacts.store import load_contacts as _load_contacts_pre
        from tool.contacts.store import save_contacts as _save_contacts_pre
        contacts_for_update = _load_contacts_pre()
        snapshot = cau.load_ch_snapshot_for_autoupdate()
        update_stats = cau.auto_update_contacts(
            contacts_for_update, ch_events, snapshot,
        )
        if any(update_stats[k] for k in ("expired", "populated", "refreshed")):
            _save_contacts_pre(contacts_for_update)
        log.info("contacts auto-update: %s", update_stats)
    except Exception as e:
        log.exception("contacts auto-update failed: %s", e)

    # First pass: enrich EVERY signal (the FULL ranked_all set, what
    # the dashboard reads via latest_signals.json) with seeded contact
    # name if the contacts table has one for that company + role. Free
    # (no API call) - just an in-memory dict lookup. Iterating ranked_all
    # (not ranked) ensures both fresh and previously-seen signals get
    # enriched, since the dashboard surfaces both.
    log.info("Annotating signals with seeded contact names…")
    try:
        from tool.contacts.store import load_contacts as _load_contacts
        _contacts_cache = _load_contacts()
    except Exception:
        _contacts_cache = {}
    for sig in ranked_all:
        if not (sig.get("company") or "").strip():
            continue
        named = lnr.resolve_named_contact_for_lead(sig, contacts=_contacts_cache)
        if named:
            if named.get("name"):
                sig["seeded_contact_name"] = named["name"]
                sig["seeded_contact_role"] = named.get("role")
            if named.get("url"):
                sig["linkedin_profile_url"] = named["url"]
                sig["linkedin_profile_role"] = named["role"]
                sig["linkedin_profile_name"] = named.get("name")
                sig["linkedin_profile_verified_at"] = named.get("verified_at")

    # Second pass: top-5 only - if we still don't have a direct LinkedIn
    # URL, spend a Bright Data call to find one. ~10 BD requests/day,
    # well inside the 5k/mo free tier. Cached in
    # tool/state/linkedin_profile_cache.json. Silent no-op if BD isn't
    # configured.
    log.info("Resolving direct LinkedIn URLs for top leads…")
    for sig in ranked[:5]:
        if sig.get("linkedin_profile_url"):
            continue   # already resolved via contacts table
        role = lnr.role_for_lead(sig)
        company = (sig.get("company") or "").strip()
        if not company:
            continue
        resolved = lnr.resolve_profile(company, role)
        if resolved and resolved.get("url"):
            sig["linkedin_profile_url"] = resolved["url"]
            sig["linkedin_profile_role"] = resolved["role"]

    # Predictive pipeline: feed the raw (pre-filter) signals into trigger
    # detection, run the job-ad cluster detector off the rolling 30-day
    # state, stack by company, rank. This is a *parallel* track to the
    # live-roles ranking above — neither affects the other.
    log.info("Running predictive pipeline on %d raw signals…", len(signals))
    pcluster.ingest_jobs(signals)
    # Velocity tracker: ingest today's RNS counts into per-company state,
    # then check for 3x baseline spikes across the 90-day window.
    try:
        pvelocity.ingest_signals(signals)
        velocity_events = pvelocity.detect_velocity_spikes()
    except Exception as e:
        log.exception("velocity: %s", e)
        velocity_events = []
    trigger_events = pdet.detect_events(signals)
    cluster_events = pcluster.detect_clusters()
    # CH officer-change scan + contacts auto-update already ran earlier
    # (pre-enrichment) so signals are enriched with fresh data. The
    # ch_events list from that earlier call is reused here as part of
    # the all-events combination for the predictor pipeline.
    all_events = trigger_events + cluster_events + ch_events + velocity_events
    stacks = stack_events(all_events)
    ranked_stacks = pr.rank(stacks)
    log.info(
        "Predictive: %d trigger + %d cluster + %d CH-officer events → %d stacks → %d ranked.",
        len(trigger_events), len(cluster_events), len(ch_events),
        len(stacks), len(ranked_stacks),
    )

    # First pass: enrich EVERY ranked stack with seeded contact name
    # (free, no API). Dashboard uses this for search-by-name URL.
    log.info("Annotating predictor stacks with seeded contact names…")
    try:
        from tool.contacts.store import load_contacts as _load_contacts
        _contacts_cache_p = _load_contacts()
    except Exception:
        _contacts_cache_p = {}
    for stk, _sc in ranked_stacks:
        company = (stk.company or "").strip()
        if not company:
            continue
        predictor_dict = {"events": [
            {"trigger_key": e.trigger_key, "company": stk.company}
            for e in stk.events
        ]}
        named = lnr.resolve_named_contact_for_predictor(predictor_dict, contacts=_contacts_cache_p)
        if named:
            if named.get("name"):
                stk._seeded_contact_name = named["name"]   # type: ignore[attr-defined]
                stk._seeded_contact_role = named.get("role")  # type: ignore[attr-defined]
            if named.get("url"):
                stk._resolved_profile_url = named["url"]   # type: ignore[attr-defined]
                stk._resolved_profile_role = named["role"]  # type: ignore[attr-defined]
                stk._resolved_profile_name = named.get("name")  # type: ignore[attr-defined]
                stk._resolved_profile_verified_at = named.get("verified_at")  # type: ignore[attr-defined]

    # Second pass: top-5 only - spend a BD call to resolve a direct URL
    # for the ones that didn't already get one from the contacts table.
    log.info("Resolving direct LinkedIn URLs for top predictors…")
    for stk, _sc in ranked_stacks[:5]:
        if getattr(stk, "_resolved_profile_url", None):
            continue
        company = (stk.company or "").strip()
        if not company:
            continue
        predictor_dict = {"events": [
            {"trigger_key": e.trigger_key, "company": stk.company}
            for e in stk.events
        ]}
        role = lnr.role_for_predictor(predictor_dict)
        resolved = lnr.resolve_profile(company, role)
        if resolved and resolved.get("url"):
            stk._resolved_profile_url = resolved["url"]   # type: ignore[attr-defined]
            stk._resolved_profile_role = resolved["role"]  # type: ignore[attr-defined]

    # Persist into the rolling-window pipeline (state survives across days;
    # ages out after 30d). Returns the new-since-yesterday delta we'll
    # render into the email; the full active pipeline is shown on the
    # dashboard.
    pipeline_result = predictor_pipeline.upsert(ranked_stacks)
    new_pids = pipeline_result["new_pids"]
    delta_stacks = [
        (stk, sc) for stk, sc in ranked_stacks
        if predictor_pipeline._pid(stk.company) in new_pids
    ]
    total_active = pipeline_result["total_active"]
    log.info("Pipeline delta: %d new predictors today; %d active in pipeline",
             len(delta_stacks), total_active)

    now = datetime.now()
    now_str = now.strftime("%A %d %B %Y · %H:%M")
    covered = covered_window()
    # Email renders DELTA only (new predictors today); dashboard renders full pipeline
    predictive_html = prender.render_html(
        delta_stacks, new_count=len(delta_stacks), total_active=total_active,
    )
    predictive_text = prender.render_text(
        delta_stacks, new_count=len(delta_stacks), total_active=total_active,
    )
    html = render_html(ranked, report, now_str, covered, predictive_html=predictive_html)
    text = render_plaintext(ranked, now_str, covered, predictive_text=predictive_text)
    (STATE_DIR / "latest_brief.html").write_text(html)
    (STATE_DIR / "latest_brief.txt").write_text(text)
    # Dashboard reads latest_signals.json — write the FULL ranked set
    # (not the email-fresh-only subset) so Sara sees every current
    # lead matching her criteria, regardless of when it first appeared.
    (STATE_DIR / "latest_signals.json").write_text(
        json.dumps(ranked_all, indent=2, default=str)
    )

    # Mandates Worth Following — vacated-seat / backfill detector. Runs
    # over the RAW scoured signals (like the predictor): when a senior
    # comms person is publicly announced moving, the seat they LEFT at
    # a watchlist company becomes a live brief.
    try:
        from tool import following as _fol
        following_feed = _fol.detect_following(signals)
        (STATE_DIR / "latest_following.json").write_text(
            json.dumps(following_feed, indent=2, default=str)
        )
        log.info("Mandates Worth Following: %d vacated-seat records from "
                 "%d raw signals", len(following_feed), len(signals))
    except Exception as e:
        log.info("following detector failed: %s", e)

    # Update competitor-mandate tracker so the dashboard's "Mandates
    # Worth Stealing" panel sees fresh first-seen / last-seen dates.
    try:
        from tool import competitor_mandates
        summary = competitor_mandates.reconcile()
        log.info("competitor_mandates reconcile: %s", summary)
    except Exception as e:
        log.info("competitor_mandates reconcile failed: %s", e)

    # Back-compat: dashboard's load_latest_predictive() reads this file.
    # We populate it from the full active pipeline so the dashboard shows
    # the rolling-window view, not just today's snapshot.
    pipeline = predictor_pipeline.load_pipeline()
    pipeline_view = [
        p for p in (pipeline.get("predictors") or {}).values()
        if p.get("status") != "dismissed"
    ]
    pipeline_view.sort(key=lambda p: -float(p.get("score") or 0))
    (STATE_DIR / "latest_predictive.json").write_text(
        json.dumps(pipeline_view, indent=2, default=str)
    )

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
