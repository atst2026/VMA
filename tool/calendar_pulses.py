"""Calendar Pulses — deterministic, date-driven placement windows.

Some of the most placeable senior-comms briefs are *knowable in advance*
because a statute or regulator fixes the date. The first mandatory UK
sustainability-reporting cycle, the annual FCA Consumer Duty board-report
deadline, and the post-Spending-Review machinery-of-government reshuffles
all force a predictable comms-capacity build-up in a defined cohort of
employers. Most recruiters react when the role is advertised; Sara can be
in the room before it is, because the *timing* is on a calendar.

Design (per the strict detection-engine filter report):

  * Deterministic. No scraping, no LLM, no external calls, $0/run. The
    dates are hand-curated from published statute / regulator cadence and
    maintained in PULSES below.
  * High-precision. A pulse only surfaces when *today* is inside its
    ACTION window — the run-up before the deadline when employers staff
    up — not for the whole year and not after the deadline has passed.
  * Placeable, not a diary. Each active pulse resolves a capped cohort of
    real watchlist companies in scope (tool.peers.SECTOR_PEERS) plus the
    exact comms seat the deadline creates demand for and a one-line
    pitch angle. Named targets + a dated reason + a role = a lead Sara
    can work, not generic "this is happening" noise.

Scope is deliberately narrow: three pulses we can defend on a published
calendar. Adding a pulse = appending one entry to PULSES.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

log = logging.getLogger("brief.pulses")

# Per-pulse target-cohort cap. Enough named accounts to act on, small
# enough to stay a focused hit list rather than a sector dump.
_MAX_TARGETS = 12

# A pulse whose ACTION window opened within this many days is flagged
# `just_opened` — a genuine, deterministic "new this week" cue for the
# dashboard ribbon. No fabrication: it is purely (today - window_start).
_JUST_OPENED_DAYS = 10


# Each pulse:
#   key         stable id
#   name        human label (panel headline)
#   window      (start ISO, end ISO) — the ACTION window for Sara: the
#               run-up when employers build capacity, ending at/just
#               before the legal date. Outside this range the pulse is
#               silent.
#   legal_date  the underlying statutory / regulator date (context line)
#   sectors     SECTOR_PEERS keys whose watchlist names are in scope
#   seat        the specific comms seat the deadline creates demand for
#   angle       one-line commission angle (what Sara actually pitches)
#   scope_note  precise, honest description of who is in scope
#   confidence  "high"  = fixed statutory / recurring regulator date
#               "medium"= policy timeline still firming
#   source      citation so Sara can sanity-check the driver
PULSES: list[dict] = [
    {
        "key": "fca_consumer_duty_2026",
        "name": "FCA Consumer Duty — annual board-report ramp",
        "window": ("2026-04-01", "2026-07-31"),
        "legal_date": "2026-07-31",
        "sectors": ["financial_services"],
        "seat": "Head of Regulatory / Customer Communications "
                "(Consumer Duty board report + remediation comms)",
        "angle": "FS firms must lay an annual Consumer Duty board report "
                 "by 31 Jul; the Q2 run-up is a repeatable retained-search "
                 "window for the permanent regulatory-comms seat — pitch "
                 "before they advertise.",
        "scope_note": "FCA-regulated retail financial-services firms "
                      "(banks, insurers, asset & wealth managers).",
        "confidence": "high",
        "source": "FCA Consumer Duty (PRIN 2A) — annual board report, "
                  "recurring 31 July cadence.",
    },
    {
        "key": "uk_srs_2026",
        "name": "UK SRS — first sustainability-reporting build-up",
        "window": ("2026-01-01", "2026-12-31"),
        "legal_date": "2026 (endorsement + FCA listed-issuer CP)",
        "sectors": [
            "financial_services", "energy_utilities",
            "pharma_healthcare", "industrial_manufacturing",
        ],
        "seat": "Head of Sustainability / ESG & Corporate-Reporting "
                "Communications",
        "angle": "UK SRS (IFRS S1/S2) endorsement + FCA listed-issuer "
                 "consultation land in 2026; large issuers build "
                 "sustainability-reporting comms ahead of the first "
                 "mandatory cycle — get the retained brief before the "
                 "rush hire.",
        "scope_note": "FTSE-weight UK-listed issuers in the high-exposure "
                      "sectors (financials, energy/utilities, pharma, "
                      "industrials).",
        "confidence": "medium",
        "source": "DBT/FRC UK SRS endorsement programme + FCA consultation "
                  "on listed-company sustainability disclosure, 2026.",
    },
    {
        "key": "mog_post_sr_2026",
        "name": "Machinery-of-government — post-Spending-Review reshuffle",
        "window": ("2026-04-01", "2026-12-31"),
        "legal_date": "FY2026/27 departmental delivery cycle",
        "sectors": ["public_sector_charities"],
        "seat": "Director of Communications (GCS) — transition & "
                "change communications",
        "angle": "Spending Review 2025 settlements drive 2026/27 "
                 "departmental restructures; GCS comms-leadership and "
                 "transition-comms briefs open as departments and ALBs "
                 "reorganise — be ahead of the GCS recruitment cycle.",
        "scope_note": "UK central-government departments and major ALBs "
                      "(plus large charities exposed to the same funding "
                      "cycle).",
        "confidence": "medium",
        "source": "HM Treasury Spending Review 2025 settlements → "
                  "departmental delivery-plan / reorganisation cycle, "
                  "FY2026/27.",
    },
    {
        "key": "agm_reporting_2026",
        "name": "UK annual-report & AGM season",
        "window": ("2026-02-01", "2026-06-30"),
        "legal_date": "Dec-year-end reporting + AGM cycle, 2026",
        "sectors": ["financial_services", "energy_utilities",
                    "industrial_manufacturing", "pharma_healthcare"],
        "seat": "Head of Investor Relations / Corporate-Reporting & "
                "Governance Communications",
        "angle": "Dec-year-end issuers publish annual reports Feb–Apr and "
                 "run AGMs Apr–Jun; the run-up is a repeatable retained-"
                 "search window for the permanent IR/corporate-reporting "
                 "comms seat — pitch before the crunch.",
        "scope_note": "FTSE-weight Dec-year-end UK-listed issuers "
                      "(financials, energy/utilities, industrials, pharma).",
        "confidence": "high",
        "source": "Companies Act annual-report + Listing Rules AGM cycle "
                  "(December year-ends), 2026.",
    },
    {
        "key": "gender_pay_gap_2026",
        "name": "Gender Pay Gap — reporting & scrutiny window",
        "window": ("2026-03-01", "2026-06-30"),
        "legal_date": "2026-04-04 (private) / 2026-03-30 (public)",
        "sectors": ["financial_services", "public_sector_charities",
                    "retail_consumer"],
        "seat": "Head of Internal / DEI Communications "
                "(gender-pay narrative + scrutiny response)",
        "angle": "Statutory GPG reports land Mar–Apr; the publication & "
                 "media-scrutiny window is a repeatable retained-search "
                 "window for the permanent internal/DEI-comms seat.",
        "scope_note": "UK employers with 250+ staff (statutory GPG "
                      "reporters).",
        "confidence": "high",
        "source": "Equality Act 2010 (Gender Pay Gap Information) "
                  "Regulations 2017 — annual 4 Apr / 30 Mar deadlines.",
    },
    {
        "key": "nhs_planning_2026",
        "name": "NHS operational-planning & restructure round",
        "window": ("2026-01-01", "2026-05-31"),
        "legal_date": "NHS England 2026/27 operational planning + Apr FY",
        "sectors": ["public_sector_charities"],
        "seat": "Director of Communications — NHS transition & "
                "change communications",
        "angle": "NHS planning guidance + ICB/trust restructures cluster "
                 "Q1 into the new financial year; comms-leadership and "
                 "change-comms briefs open before the recruitment cycle.",
        "scope_note": "NHS trusts, ICBs and arm's-length bodies.",
        "confidence": "medium",
        "source": "NHS England operational planning guidance + FY2026/27 "
                  "cycle.",
    },
    {
        "key": "he_clearing_2026",
        "name": "Higher-Education clearing & new-intake comms",
        "window": ("2026-06-15", "2026-09-30"),
        "legal_date": "UCAS Clearing Jul–Aug; academic year starts Sep 2026",
        "sectors": ["public_sector_charities"],
        "seat": "Head of Communications / Student Recruitment & "
                "Marketing Communications",
        "angle": "Clearing + new-intake drive a predictable HE comms & "
                 "marketing capacity surge; secure the retained brief "
                 "before the summer scramble.",
        "scope_note": "UK universities and higher-education providers.",
        "confidence": "medium",
        "source": "UCAS Clearing cycle + academic year 2026/27.",
    },
]


def _parse(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


def _targets_for(sectors: list[str]) -> list[str]:
    """Resolve the in-scope watchlist cohort. De-duped, order-stable,
    capped. These are real names from the curated watchlist so the pulse
    is a workable hit list, not an abstract calendar entry."""
    try:
        from tool.peers import SECTOR_PEERS
    except Exception:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for sec in sectors:
        for co in SECTOR_PEERS.get(sec, []):
            k = co.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(co)
            if len(out) >= _MAX_TARGETS:
                return out
    return out


def active_pulses(today: Optional[date] = None) -> list[dict]:
    """Pulses whose ACTION window is open *today*. Each record is a
    placeable lead: dated reason + named target cohort + the seat + a
    pitch angle.

    Sorted high-confidence first, then by urgency (fewest days left in
    the window first) so the most time-critical pulse is at the top.
    """
    from tool.advisory import advisory_for

    if today is None:
        today = date.today()

    out: list[dict] = []
    for p in PULSES:
        try:
            start, end = _parse(p["window"][0]), _parse(p["window"][1])
        except Exception as e:  # malformed entry — skip, never crash
            log.info("pulse %s skipped: bad window (%s)", p.get("key"), e)
            continue
        if not (start <= today <= end):
            continue

        targets = _targets_for(p.get("sectors", []))
        days_left = (end - today).days
        days_open = (today - start).days
        out.append({
            "key":         p["key"],
            "name":        p["name"],
            "window":      f"{p['window'][0]} → {p['window'][1]}",
            "days_left":   days_left,
            # Ribbon places a pulse on the month its ACTION window ends
            # (the deadline run-up Sara must have acted by).
            "act_by":      p["window"][1],
            "just_opened": 0 <= days_open <= _JUST_OPENED_DAYS,
            "legal_date":  p.get("legal_date", ""),
            "seat":        p.get("seat", ""),
            "angle":       p.get("angle", ""),
            "scope_note":  p.get("scope_note", ""),
            "sector":      (p.get("sectors") or [""])[0],
            "targets":     targets,
            "confidence":  p.get("confidence", "medium"),
            "source":      p.get("source", ""),
            "advisory":    advisory_for(p["key"]),
        })

    out.sort(key=lambda r: (r["confidence"] != "high", r["days_left"]))
    return out


def load_pulses(limit: int = 10) -> list[dict]:
    """Dashboard accessor. Computed LIVE (pulses depend only on today's
    date, so a 05:30-cron snapshot would be stale on days_left). No
    external calls, no signals needed."""
    return active_pulses()[:limit]


# ---------------------------------------------------------------------------
# UK & European comms industry events — awards, conferences, summits.
# Distinct mechanic from statutory pulses: these surface NETWORKING and
# CANDIDATE-VISIBILITY moments rather than statute-forced hiring windows.
# Both internal-comms (IoIC, IABC) and external-comms (PRWeek, CIPR,
# PRCA, SABRE, European Excellence) events are covered.
#
# Dates are pinned to confirmed or typical 2026 windows. Each event has
# an ACTION window — typically the 4–8 weeks before the date when
# shortlists land, panels are confirmed, and outreach to finalists or
# attendees has the strongest hook.
# ---------------------------------------------------------------------------
# Each entry's date + source link was verified against the organiser's own
# 2026 listing (May 2026). Events with no firm public 2026 ceremony date, or
# whose date has already passed, are deliberately omitted rather than guessed.
INDUSTRY_EVENTS: list[dict] = [
    {
        "key": "cipr_excellence_2026",
        "name": "CIPR Excellence Awards",
        "event_date": "2026-07-01",
        "action_window": ("2026-05-01", "2026-07-01"),
        "location": "Royal Lancaster Hotel, London",
        "focus": "external",
        "why_now": "UK PR practitioner gold standard (42nd year); 145 orgs "
                   "across 33 categories. Senior in-house comms judges + "
                   "finalists = a direct relationship route.",
        "source": "https://awards.cipr.co.uk/",
    },
    {
        "key": "communicate_ic_engagement_live_2026",
        "name": "Internal Communications & Engagement Live",
        "event_date": "2026-07-08",
        "action_window": ("2026-05-08", "2026-07-08"),
        "location": "The Brewery, London",
        "focus": "internal",
        "why_now": "One-day UK IC conference; delegates + speakers skew "
                   "Head of IC / Director of IC at large UK employers.",
        "source": "https://www.communicatemagazine.com/conference/internal-communications-and-engagement-live-2026/",
    },
    {
        "key": "ioic_awards_2026",
        "name": "IoIC Awards",
        "event_date": "2026-09-17",
        "action_window": ("2026-07-17", "2026-09-17"),
        "location": "London",
        "focus": "internal",
        "why_now": "Premier UK internal-comms awards. Finalists = visible "
                   "Heads of IC and IC Directors at large UK employers.",
        "source": "https://www.ioic.org.uk/awards.html",
    },
    {
        "key": "icco_global_summit_2026",
        "name": "ICCO Global Summit",
        "event_date": "2026-11-11",
        "action_window": ("2026-09-11", "2026-11-11"),
        "location": "Milan",
        "focus": "external",
        "why_now": "International Communications Consultancy Organisation "
                   "annual summit (11-13 Nov). Consultancy CEOs + senior "
                   "in-house clients attend; relationship-building venue.",
        "source": "https://iccopr.com/globalsummit/",
    },
    {
        "key": "cipr_conference_2026",
        "name": "CIPR Annual Conference",
        "event_date": "2026-11-18",
        "action_window": ("2026-09-18", "2026-11-18"),
        "location": "London",
        "focus": "external",
        "why_now": "Theme: Organisational Resilience. Speaker line-up is a "
                   "tier-1 list of senior in-house comms leaders; pre-event "
                   "outreach has a clear hook.",
        "source": "https://cipr.co.uk/CIPR/CIPR/Network/Events_/Annual_conference.aspx",
    },
    {
        "key": "european_excellence_2026",
        "name": "European Excellence Awards",
        "event_date": "2026-12-11",
        "action_window": ("2026-10-11", "2026-12-11"),
        "location": "Europe",
        "focus": "mixed",
        "why_now": "Pan-EU comms awards. UK + EU CCO attendance; useful "
                   "for European-headquartered briefs landing in London offices.",
        "source": "https://www.excellence-awards.com/",
    },
]


def active_events(today: Optional[date] = None,
                  lookahead_days: int = 180) -> list[dict]:
    """Industry events whose ACTION window is open today OR whose event
    date falls within the next `lookahead_days`. Sorted by event date
    so the soonest event is at the top."""
    if today is None:
        today = date.today()
    horizon = today.replace() if False else today
    out: list[dict] = []
    for e in INDUSTRY_EVENTS:
        try:
            ev_date = _parse(e["event_date"])
            win_start, win_end = (_parse(e["action_window"][0]),
                                  _parse(e["action_window"][1]))
        except Exception as exc:
            log.info("event %s skipped: %s", e.get("key"), exc)
            continue
        # Show if action window is open OR event is upcoming within horizon.
        in_window = win_start <= today <= win_end
        upcoming = today <= ev_date <= today.fromordinal(
            today.toordinal() + lookahead_days)
        if not (in_window or upcoming):
            continue
        out.append({
            "key":           e["key"],
            "name":          e["name"],
            "event_date":    e["event_date"],
            "act_by":        e["event_date"],          # ribbon-month bucketing
            "days_to_event": (ev_date - today).days,
            "location":      e.get("location", ""),
            "focus":         e.get("focus", "mixed"),
            "why_now":       e.get("why_now", ""),
            "source":        e.get("source", ""),
            "in_action_window": in_window,
            "type":          "event",                  # distinguishes from pulse
        })
    out.sort(key=lambda r: r["days_to_event"])
    return out


def load_events(limit: int = 24) -> list[dict]:
    """Dashboard accessor for industry events. Same pattern as load_pulses."""
    return active_events()[:limit]
