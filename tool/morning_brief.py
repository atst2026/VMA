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
    companies_house, gdelt, google_news, jobs, rss_feeds,
    sec_edgar,
)
from tool.state_store import filter_unseen
from tool.predictive.stacker import stack as stack_events
from tool import linkedin_resolver as lnr
from tool import predictor_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("brief")

from tool.state_paths import state_dir, state_root
STATE_DIR = state_dir()
STATE_DIR.mkdir(parents=True, exist_ok=True)


def _persist_state(repo_path: str, payload: str, message: str) -> None:
    """Persist a brief output to the dashboard-state branch SYNCHRONOUSLY.

    The brief is a short-lived CI process: the dashboard's fire-and-forget
    push_async runs on a daemon thread that the interpreter kills on exit,
    so whichever push wins the race lands and the rest (notably
    latest_signals.json) silently don't — which is why the state branch
    kept going stale. A blocking push guarantees the write lands before
    the process exits; sequential calls to distinct paths can't conflict.
    """
    try:
        from tool import github_state
        ok = github_state.push(repo_path, payload, message)
        log.info("state persist %s -> %s", repo_path, "ok" if ok else "FAILED")
    except Exception as e:
        log.warning("state persist %s errored: %s", repo_path, e)


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

    # Redundant predictive lane: same trigger phrasing via Google News
    # RSS (free, no rate-limit wall) so GDELT drops don't silently cost
    # the predictor leads. Precision unchanged — the account gate still
    # decides what survives.
    try:
        _tally("Google News (redundant predictive lane)",
               google_news.fetch_predictive_signals())
    except Exception as e:
        log.exception("google_news predictive: %s", e)

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
    # Tag the dashboard set: "new today" == not seen before this run
    # (same identity filter_unseen uses). Drives the leads NEW badge.
    _fresh_ids = {s.get("id") for s in fresh if s.get("id")}
    for _sig in ranked_all:
        _sig["is_new"] = _sig.get("id") in _fresh_ids
    ranked = rank(fresh)
    # Same filter the dashboard applies: drop appointment news
    # (the seat is filled, not open — the useful half feeds Mandates
    # Worth Following separately) and signals with no parsed company
    # (structurally unusable). Keeps the email aligned with the
    # dashboard rather than spraying noise into Sara's inbox.
    ranked = [s for s in ranked
              if (s.get("kind") or "").strip().lower() != "leadership_change"
              and (s.get("company") or "").strip()]
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
    from tool import hiring_manager as _hm
    # One uniform resolver for every lead, whatever its kind.
    for sig in ranked_all:
        company = (sig.get("company") or "").strip()
        if not company:
            continue
        c = _hm.resolve_lead_contact(sig, contacts=_contacts_cache)
        if c.get("name"):
            sig["seeded_contact_name"] = c["name"]
            sig["seeded_contact_role"] = c["title"]
        if c.get("linkedin_url"):
            sig["linkedin_profile_url"] = c["linkedin_url"]
            sig["linkedin_profile_role"] = c["title"]
            sig["linkedin_profile_name"] = c["name"]

    # Second pass: top-5 only - if we still don't have a direct LinkedIn
    # URL, spend a Bright Data call to find one. ~10 BD requests/day,
    # well inside the 5k/mo free tier. Cached in
    # tool/state/linkedin_profile_cache.json. Silent no-op if BD isn't
    # configured. Off-roster live-name resolution: BD-searches the same
    # resolved target title used everywhere else.
    log.info("Resolving direct LinkedIn URLs for top leads…")
    for sig in ranked[:5]:
        if sig.get("linkedin_profile_url"):
            continue   # already resolved via contacts table
        company = (sig.get("company") or "").strip()
        if not company:
            continue
        role = _hm.resolve_lead_contact(sig, contacts=_contacts_cache)["title"]
        resolved = lnr.resolve_profile(company, role)
        if resolved and resolved.get("url"):
            sig["linkedin_profile_url"] = resolved["url"]
            sig["linkedin_profile_role"] = role

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
    _signals_payload = json.dumps(ranked_all, indent=2, default=str)
    (STATE_DIR / "latest_signals.json").write_text(_signals_payload)
    # Persist to the dashboard-state branch so the dashboard hydrates
    # leads on cold-start instead of showing stale data until Daily Refresh.
    _persist_state("tool/state/latest_signals.json", _signals_payload,
                   "state: morning-brief latest_signals.json")

    # Vacated Seats & Senior Moves — unified senior-comms-move engine
    # (merges the former Hire Watch + Mandates Worth Following). Runs over
    # the RAW scoured signals: a senior comms person publicly moving means
    # the seat they LEFT at a watchlist firm is a live brief (replacement
    # search), and a senior hire AT a watchlist firm is a re-org to watch.
    # Watchlist-gated, so off-patch headlines are dropped. Now part of the
    # daily brief (previously a manual dashboard-only scour).
    try:
        from tool import cascade as _cascade
        cstats = _cascade.scour(signals)
        log.info("Vacated Seats & Senior Moves: %s", cstats)
    except Exception as e:
        log.info("cascade scour failed: %s", e)

    # Calendar Pulses — deterministic, date-driven placement windows
    # (FCA Consumer Duty board-report ramp, UK SRS first-cycle build-up,
    # post-Spending-Review machinery-of-government reshuffle). No signals
    # needed; the dashboard recomputes these live (days_left changes
    # daily) — this snapshot is for the artifact / email only.
    try:
        from tool import calendar_pulses as _pulses
        pulses_feed = _pulses.active_pulses()
        (STATE_DIR / "latest_pulses.json").write_text(
            json.dumps(pulses_feed, indent=2, default=str)
        )
        log.info("Calendar Pulses: %d active placement window(s) today",
                 len(pulses_feed))
    except Exception as e:
        log.info("calendar pulses failed: %s", e)

    # BD-Calendar auto-update: scour real public sources for NEW placement
    # windows, comms events and exec-search framework notices, and merge
    # them (plus the curated baseline) into the persistent calendar
    # pipelines — so the three BD-Calendar tools auto-update like Today's
    # Leads / Pre-Market instead of only showing hand-curated seeds. The
    # pipeline state files are pushed to the dashboard-state branch below.
    try:
        from tool import calendar_discovery
        cal_summary = calendar_discovery.refresh_all()
        log.info("BD-Calendar discovery: %s",
                 {k: {"new": len(v.get("new", [])),
                      "active": v.get("total_active")}
                  for k, v in cal_summary.items()})
        from tool import calendar_pipeline as _calpipe
        for _kind in ("windows", "events", "frameworks"):
            _p = STATE_DIR / f"calendar_pipeline_{_kind}.json"
            if _p.exists():
                _persist_state(_calpipe.repo_state_path(_kind),
                               _p.read_text(),
                               f"state: BD-calendar pipeline ({_kind})")
    except Exception as e:
        log.info("BD-Calendar discovery failed: %s", e)

    # Water Special-Administration Watch — the highest-value single comms
    # event in UK utilities. Runs over the RAW signals (Ofwat News RSS /
    # RNS / GDELT / trade press already scoured); a small extension of
    # the existing Ofwat feed, anchored to the fixed England & Wales
    # regulated-water universe so it stays high-precision.
    try:
        from tool import water_sar as _wsar
        water_feed = _wsar.detect_water_sar(signals)
        (STATE_DIR / "latest_water_sar.json").write_text(
            json.dumps(water_feed, indent=2, default=str)
        )
        log.info("Water SAR Watch: %d record(s) from %d raw signals",
                 len(water_feed), len(signals))
    except Exception as e:
        log.info("water SAR watch failed: %s", e)

    # Contract-End / Re-Tender Window — proactive leading indicator
    # (complements, does NOT duplicate, the reactive CONTRACT_LOSS
    # predictor). Runs over the RAW signals (Find a Tender RSS / RNS /
    # GDELT / trade press already scoured); only emits when the affected
    # employer resolves to a watchlist account.
    try:
        from tool import contract_end as _cend
        contract_feed = _cend.detect_contract_end(signals)
        (STATE_DIR / "latest_contract_end.json").write_text(
            json.dumps(contract_feed, indent=2, default=str)
        )
        log.info("Contract-End Window: %d record(s) from %d raw signals",
                 len(contract_feed), len(signals))
    except Exception as e:
        log.info("contract-end detector failed: %s", e)

    # Funding-Round detector — the pre-hire window at scaling private
    # firms (>=£20m growth round -> ~6-month senior-comms-hire lag).
    # Runs over the RAW signals (GDELT news graph + trade press already
    # scoured). Distinct population from the IPO_LISTING predictor.
    try:
        from tool import funding_round as _fund
        funding_feed = _fund.detect_funding(signals)
        _funding_payload = json.dumps(funding_feed, indent=2, default=str)
        (STATE_DIR / "latest_funding.json").write_text(_funding_payload)
        _persist_state("tool/state/latest_funding.json", _funding_payload,
                       "state: morning-brief latest_funding.json")
        log.info("Funding-Round: %d record(s) from %d raw signals",
                 len(funding_feed), len(signals))
    except Exception as e:
        log.info("funding-round detector failed: %s", e)

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
    _predictive_payload = json.dumps(pipeline_view, indent=2, default=str)
    (STATE_DIR / "latest_predictive.json").write_text(_predictive_payload)
    # Same as latest_signals: persist to dashboard-state so the Prediction
    # Signals panel hydrates on cold-start.
    _persist_state("tool/state/latest_predictive.json", _predictive_payload,
                   "state: morning-brief latest_predictive.json")
    # predictor_pipeline.json is the underlying durable pipeline the
    # dashboard reads via predictor_pipeline.all_predictors().
    _pp_path = STATE_DIR / "predictor_pipeline.json"
    if _pp_path.exists():
        _persist_state("tool/state/predictor_pipeline.json",
                       _pp_path.read_text(),
                       "state: morning-brief predictor_pipeline.json")

    # Deliver
    if mode in ("send", "test"):
        # Global kill-switch: the dashboard is the surface, so by default the
        # brief refreshes state/dashboard (done above) and emails no one.
        if not config.MORNING_BRIEF_EMAIL_ENABLED:
            log.info("Morning-brief email disabled "
                     "(config.MORNING_BRIEF_EMAIL_ENABLED=False) — state and "
                     "dashboard updated; no email sent.")
            print("✓ Brief built and dashboard updated; email delivery disabled.")
            return 0
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
        from tool.profiles import active_profile as _ap
        _brief_name = ("Marketing Brief" if _ap().key == "marketing"
                       else "Sara's Morning Brief")
        subject = (
            f"{_brief_name} — {now.strftime('%a %d %b')} "
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
