"""In-house failure + hiring-restart detectors — the down-market demand engine.

In a quiet market the binding constraint is not finding hiring signals
(there are fewer, definitionally) — it is finding WILLINGNESS TO PAY A
FEE. The two highest-fee-probability populations are:

  1. Companies whose do-it-themselves route is visibly failing: a senior
     comms/marketing role still openly advertised after AGED_DAYS with no
     agency attached, or pulled and reposted (the repost signature). They
     have already paid the cost of the free route — the highest-converting
     BD call there is.

  2. Companies coming out of a hiring freeze: the first senior posting
     after RESTART_GAP_DAYS of silence. Budget is moving again and most
     competitors are still treating the account as dormant — the first
     call after the thaw wins the relationship.

Data flow: zero extra fetches. The daily job signals the brief already
collects (which have ALREADY passed the role-taxonomy + salary filters in
tool/sources/jobs.py — so every "job" signal here is a senior in-scope
role) are folded into a per-company posting ledger persisted across runs.
Recruiter-posted roles never reach the ledger: agency/competitor posters
are excluded upstream by the source-level company exclusions.

Ledger shape (state/posting_ledger.json):
    {"version": 1, "companies": {
        "<norm company>": {
            "display": "Acme Group",
            "last_posting": "2026-06-09",
            "restart_detected": "2026-06-09",   # thaw episode pending
            "restart_gap_days": 212,
            "restart_fired": "",                # set once the event is emitted
            "roles": {
                "<norm title>": {
                    "title": "Head of Communications",
                    "url": "https://…",
                    "first_seen": "2026-04-20", "last_seen": "2026-06-09",
                    "repost_detected": "", "last_gap_days": 0,
                    "repost_fired": "", "aged_fired": ""}}}}}

Detection is a pure function over (ledger, today) so the whole engine is
unit-testable with injected state and fixed dates; persistence is a thin
load/save shell around it. Everything is bounded and non-fatal — a ledger
failure logs and yields [] so the brief always completes.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

from tool.predictive.detector import TriggerEvent
from tool.state_paths import state_dir

log = logging.getLogger("brief.inhouse")

# A role continuously live this long without being filled = failing search.
AGED_DAYS = 45
# A role that vanishes for >= MIN and <= MAX days then returns = repost.
# Below MIN it is scrape jitter; above MAX it is a genuinely new opening.
REPOST_GAP_MIN_DAYS = 14
REPOST_GAP_MAX_DAYS = 150
# "Continuously live" means seen within this many days of today.
RECENT_DAYS = 7
# First posting after this long a company-level silence = hiring restart.
RESTART_GAP_DAYS = 180
# Ledger hygiene: forget roles unseen this long; forget companies whose
# last posting is older than this (bounds the file; a gap that long is
# beyond the restart window anyway).
ROLE_PRUNE_DAYS = 210
COMPANY_PRUNE_DAYS = 720

_SUFFIX_RX = re.compile(
    r"\b(plc|p l c|limited|ltd|group|holdings|llp|inc|incorporated|"
    r"corp|corporation|llc|uk)\b")


def _ledger_path() -> Path:
    return Path(str(state_dir())) / "posting_ledger.json"


def _norm_company(name: str | None) -> str:
    """Stable company key: lowercase alphanumerics, corporate suffixes
    stripped so 'Acme Ltd' / 'ACME Limited' / 'Acme Group plc' merge."""
    s = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    s = _SUFFIX_RX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_title(title: str | None) -> str:
    """Stable role key: lowercase alphanumerics, bracketed qualifiers and
    location tails dropped so 'Head of Comms (London)' tracks as one role."""
    t = re.sub(r"\(.*?\)", " ", (title or "").lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _d(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def load_ledger() -> dict:
    try:
        with open(_ledger_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("companies"), dict):
            return data
    except FileNotFoundError:
        pass
    except Exception as e:
        log.info("posting ledger unreadable (%s) — starting fresh", e)
    return {"version": 1, "companies": {}}


def save_ledger(state: dict) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(path)


def ingest_jobs(signals: list[dict], *, today: date | None = None,
                state: dict | None = None) -> dict:
    """Fold today's job signals into the ledger. Marks (but does not emit)
    repost and restart episodes; the detect_* functions turn pending marks
    into TriggerEvents exactly once. Pass `state` + `today` for tests;
    omit both for the daily run (loads, updates, saves)."""
    persist = state is None
    st = load_ledger() if state is None else state
    today = today or datetime.now(timezone.utc).date()
    companies = st.setdefault("companies", {})

    for sig in signals:
        if (sig.get("kind") or "") != "job":
            continue
        company = (sig.get("company") or "").strip()
        title = (sig.get("title") or "").strip()
        ck, tk = _norm_company(company), _norm_title(title)
        if not ck or not tk:
            continue
        co = companies.setdefault(ck, {"display": company, "roles": {}})
        co["display"] = company or co.get("display", "")

        # Company-level restart: a posting after a long silence.
        prev = _d(co.get("last_posting"))
        if prev is not None and prev < today:
            gap = (today - prev).days
            if gap >= RESTART_GAP_DAYS:
                co["restart_detected"] = today.isoformat()
                co["restart_gap_days"] = gap
        if prev is None or prev < today:
            co["last_posting"] = today.isoformat()

        # Role-level tracking: ageing + the repost signature.
        roles = co.setdefault("roles", {})
        role = roles.get(tk)
        if role is None:
            roles[tk] = {"title": title, "url": sig.get("url") or "",
                         "first_seen": today.isoformat(),
                         "last_seen": today.isoformat()}
            continue
        last = _d(role.get("last_seen"))
        if last is not None and last < today:
            gap = (today - last).days
            if REPOST_GAP_MIN_DAYS <= gap <= REPOST_GAP_MAX_DAYS:
                role["repost_detected"] = today.isoformat()
                role["last_gap_days"] = gap
            elif gap > REPOST_GAP_MAX_DAYS:
                # A genuinely new opening for the same title: new episode.
                role["first_seen"] = today.isoformat()
                role.pop("aged_fired", None)
                role.pop("repost_detected", None)
                role.pop("repost_fired", None)
        if last is None or last < today:
            role["last_seen"] = today.isoformat()
        role["title"] = title or role.get("title", "")
        if sig.get("url"):
            role["url"] = sig["url"]

    _prune(st, today)
    if persist:
        save_ledger(st)
    return st


def _prune(state: dict, today: date) -> None:
    companies = state.get("companies", {})
    for ck in list(companies):
        co = companies[ck]
        roles = co.get("roles", {})
        for tk in list(roles):
            last = _d(roles[tk].get("last_seen"))
            if last is None or (today - last).days > ROLE_PRUNE_DAYS:
                del roles[tk]
        last_post = _d(co.get("last_posting"))
        if last_post is None or (today - last_post).days > COMPANY_PRUNE_DAYS:
            del companies[ck]


def _event(key: str, label: str, company: str, evidence: str, url: str,
           published: datetime) -> TriggerEvent:
    from tool.sources._http import signal_id
    return TriggerEvent(
        trigger_key=key,
        trigger_label=label,
        company=company,
        evidence=evidence,
        url=url,
        source_label="Posting-ledger analysis",
        published=published,
        raw_signal_id=signal_id("inhouse", f"{key}|{company}|{published.date()}"),
        tier_hint="covered",
    )


def detect_inhouse_failure(*, today: date | None = None,
                           state: dict | None = None) -> list[TriggerEvent]:
    """Emit inhouse_search_failing events: senior roles aged past
    AGED_DAYS still live, plus pending repost episodes. Fires once per
    episode (the fired marker survives in the ledger)."""
    persist = state is None
    st = load_ledger() if state is None else state
    today = today or datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc)
    events: list[TriggerEvent] = []

    for co in st.get("companies", {}).values():
        display = co.get("display") or ""
        for role in co.get("roles", {}).values():
            first, last = _d(role.get("first_seen")), _d(role.get("last_seen"))
            if not display or first is None or last is None:
                continue
            title = role.get("title") or "a senior role"
            url = role.get("url") or ""

            # Aged: continuously live past the threshold, not yet fired
            # for this episode.
            age = (today - first).days
            live = (today - last).days <= RECENT_DAYS
            if live and age >= AGED_DAYS and not role.get("aged_fired"):
                role["aged_fired"] = today.isoformat()
                events.append(_event(
                    "inhouse_search_failing", "In-house search failing",
                    display,
                    (f"'{title}' has been openly advertised for {age} days "
                     f"with no recruiter attached — their own route to "
                     f"filling it is not working."),
                    url, now))

            # Repost: pending episode not yet fired.
            rd = _d(role.get("repost_detected"))
            rf = _d(role.get("repost_fired"))
            if rd is not None and (rf is None or rf < rd):
                role["repost_fired"] = rd.isoformat()
                gap = role.get("last_gap_days", 0)
                events.append(_event(
                    "inhouse_search_failing", "In-house search failing",
                    display,
                    (f"'{title}' was advertised, withdrawn for {gap} days, "
                     f"then reposted — a repost signature: their route to "
                     f"filling it has already failed once."),
                    url, now))

    if persist:
        save_ledger(st)
    log.info("in-house failure: %d events", len(events))
    return events


def detect_hiring_restart(*, today: date | None = None,
                          state: dict | None = None) -> list[TriggerEvent]:
    """Emit hiring_restart events for pending thaw episodes (first senior
    posting after RESTART_GAP_DAYS of company-level silence)."""
    persist = state is None
    st = load_ledger() if state is None else state
    today = today or datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc)
    events: list[TriggerEvent] = []

    for co in st.get("companies", {}).values():
        display = co.get("display") or ""
        rd = _d(co.get("restart_detected"))
        rf = _d(co.get("restart_fired"))
        if not display or rd is None or (rf is not None and rf >= rd):
            continue
        co["restart_fired"] = rd.isoformat()
        gap = co.get("restart_gap_days", RESTART_GAP_DAYS)
        months = max(1, round(gap / 30))
        events.append(_event(
            "hiring_restart", "Hiring restart (account thaw)", display,
            (f"First senior comms/marketing posting after roughly "
             f"{months} months of silence ({gap} days) — the hiring "
             f"freeze at this account has visibly ended."),
            "", now))

    if persist:
        save_ledger(st)
    log.info("hiring restart: %d events", len(events))
    return events
