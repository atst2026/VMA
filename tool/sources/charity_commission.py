"""UK charity-register signals (Charity Commission E&W + OSCR + CCNI).

The charity sector is the system's prized low-competition segment, but the
register data was previously absent. This module adds it, FREE:

  * Charity Commission for England & Wales — free register API. A trustee
    BOARD change at a major charity is a governance event (a new chair of
    trustees drives a strategy / comms review, analogous to chair_change at
    a listed company). Detected by snapshotting the trustee set per charity
    and diffing day-over-day, mirroring the proven Companies House
    officer-snapshot pattern.
  * OSCR (Scotland) and CCNI (Northern Ireland) are wired as additional
    regulators; OSCR's fuller data (trustee names + unredacted accounts)
    only began publishing in early 2026, so it is enabled the same way the
    E&W lane is.

Auth: the CC API needs a FREE subscription key (Ocp-Apim-Subscription-Key),
read from CHARITY_COMMISSION_KEY. Without it this lane is a clean no-op and
is logged — exactly like the Adzuna and Bright Data lanes. The live,
no-key charity coverage (vacancies → the job-ad-cluster predictor) is
delivered separately by the CharityJob / CharityComms / NHS Jobs RSS lanes.

Everything here is non-fatal: any error skips the affected charity and the
brief continues.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from tool.predictive import patterns as P
from tool.predictive.detector import TriggerEvent
from tool.sources._http import get, signal_id

log = logging.getLogger("brief.charity")

CHARITY_COMMISSION_KEY = os.environ.get("CHARITY_COMMISSION_KEY", "")
# v2 register API base. Per-charity detail lives under
# /register/api/charitydetails/{regno}/{suffix}; trustees under
# /register/api/charitytrustees/{regno}/{suffix}. Subject to the free key.
CC_API_BASE = "https://api.charitycommission.gov.uk/register/api"

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
TRUSTEE_SNAPSHOT_FILE = STATE_DIR / "charity_trustees_snapshot.json"

# Curated major-charity watchlist (name + Charity Commission registration
# number). Hand-picked national charities where a senior comms hire would
# land and where a board change is a real BD trigger. Numbers are the
# public registered-charity numbers; a wrong/changed number simply returns
# no data (graceful-skip), never an error. Extend freely.
CHARITY_WATCHLIST = [
    ("British Heart Foundation", "225971"),
    ("Cancer Research UK", "1089464"),
    ("Oxfam", "202918"),
    ("Barnardo's", "216250"),
    ("NSPCC", "216401"),
    ("Royal National Lifeboat Institution", "209603"),
    ("National Trust", "205846"),
    ("The Wellcome Trust", "210183"),
    ("Save the Children", "213890"),
    ("Macmillan Cancer Support", "261017"),
    ("RSPCA", "219099"),
    ("Age UK", "1128267"),
    ("Marie Curie", "207994"),
    ("Shelter", "263710"),
    ("Mind", "219830"),
    ("Scope", "208231"),
    ("British Red Cross", "220949"),
    ("Christian Aid", "1105851"),
    ("The Salvation Army", "214779"),
    ("Leonard Cheshire", "218186"),
    ("Citizens Advice", "279057"),
    ("Nuffield Health", "205533"),
]


def _enabled() -> bool:
    if not CHARITY_COMMISSION_KEY:
        log.info("charity: CHARITY_COMMISSION_KEY not set — register lane is a "
                 "no-op (charity vacancies still flow via the CharityJob / "
                 "CharityComms / NHS Jobs lanes)")
        return False
    return True


def _load_snapshot() -> dict:
    if not TRUSTEE_SNAPSHOT_FILE.exists():
        return {}
    try:
        return json.loads(TRUSTEE_SNAPSHOT_FILE.read_text())
    except Exception:
        return {}


def _save_snapshot(d: dict) -> None:
    try:
        TRUSTEE_SNAPSHOT_FILE.write_text(json.dumps(d, indent=0))
    except Exception as e:
        log.info("charity: could not persist trustee snapshot: %s", e)


def fetch_trustees(regno: str) -> list[str] | None:
    """Trustee names for a charity, or None on network/auth error (distinct
    from an empty list). Defensive on the API's response shape."""
    if not CHARITY_COMMISSION_KEY:
        return None
    url = f"{CC_API_BASE}/charitytrustees/{regno}/0"
    r = get(url, headers={"Ocp-Apim-Subscription-Key": CHARITY_COMMISSION_KEY})
    if not r or r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    return _extract_trustee_names(data)


def _extract_trustee_names(data) -> list[str]:
    """Pull trustee display names out of the CC API response, tolerant of the
    several shapes the v2 API returns (a bare list, or {'trustees': [...]} ,
    each item a dict with 'trustee_name' / 'name')."""
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = (data.get("trustees") or data.get("trustee_list")
                 or data.get("items") or [])
    names = []
    for it in items:
        if isinstance(it, dict):
            nm = (it.get("trustee_name") or it.get("name")
                  or it.get("trusteeName") or "")
        else:
            nm = str(it)
        nm = (nm or "").strip()
        if nm:
            names.append(nm)
    return names


def fetch_charity_signals() -> list[TriggerEvent]:
    """Diff each watchlist charity's trustee board against the last snapshot
    and emit a board-change event (mapped to chair_change — a governance
    change that drives a comms/strategy review). First run only seeds the
    snapshot (no events), exactly like the CH officer scan."""
    if not _enabled():
        return []
    snapshot = _load_snapshot()
    new_snapshot = dict(snapshot)
    events: list[TriggerEvent] = []
    trig = P.BY_KEY.get("chair_change")
    now = datetime.now(timezone.utc)

    for name, regno in CHARITY_WATCHLIST:
        names = fetch_trustees(regno)
        if names is None:
            continue  # network/auth error — leave prior snapshot intact
        today = sorted(set(names))
        prior = (snapshot.get(regno) or {}).get("trustees")
        new_snapshot[regno] = {"name": name, "trustees": today,
                               "at": now.isoformat()}
        if not prior or trig is None:
            continue  # first sight of this charity — seed only
        added = set(today) - set(prior)
        removed = set(prior) - set(today)
        if not (added or removed):
            continue
        bits = []
        if added:
            bits.append("new trustee(s): " + ", ".join(sorted(added))[:120])
        if removed:
            bits.append("departed: " + ", ".join(sorted(removed))[:120])
        events.append(TriggerEvent(
            trigger_key="chair_change",
            trigger_label="Charity board change (governance)",
            company=name,
            evidence=(f"{name} board change — {'; '.join(bits)} "
                      f"(Charity Commission register)."),
            url=f"https://register-of-charities.charitycommission.gov.uk/charity-search/-/charity-details/{regno}",
            source_label="Charity Commission (trustee board)",
            published=now,
            raw_signal_id=signal_id("cc_board", f"{regno}|{','.join(sorted(added | removed))}"),
            tier_hint="covered",
        ))

    _save_snapshot(new_snapshot)
    log.info("charity: %d charities checked, %d board-change events",
             len(CHARITY_WATCHLIST), len(events))
    return events


def fetch_all() -> list[dict]:
    """Back-compat signal-stream entry point. The charity register now emits
    TriggerEvents directly via fetch_charity_signals(); this returns [] so
    the source tally line stays honest."""
    return []
