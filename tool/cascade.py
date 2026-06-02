"""Vacated Seats & Senior Moves — the unified senior-comms-move engine.

Detects senior comms moves in public news and emits two derived BD
actions per move:

  (A) REPLACEMENT SEARCH (vacated seat)
      When a CCO / Director of Comms / Head of IC leaves a watchlist
      firm, that firm needs a replacement search and its direct reports
      are flight risks. The vacated seat is the highest-intent brief
      there is. Action: pitch VMA to run the replacement search.

  (B) RE-ORG WATCH (new company)
      A senior comms hire reshapes their team within 6-12 months. When
      the *new* employer is a watchlist firm, track it for downstream
      briefs. Action: watch for team build-out.

This module merges the former "Hire Watch" (two-sided framing + triage)
with "Mandates Worth Following" (the watchlist-gated vacated-seat
detector). The vacated-seat side is sourced from following.detect_following
(arrivals "from <WatchlistCo>" + pure departures), so departure-only
announcements — the cleanest backfill signal — are now caught too.

PRECISION GATE (this is what changed)
=====================================
Every emitted event must touch the watchlist. Each side is resolved via
tool.account_match.resolve_account; the event fires only if the vacated
seat's firm OR the new employer is a watchlist account. An off-patch
headline (e.g. a US school-district "communications director" story) no
longer resolves to anything and is dropped. Other guards retained:

  1. Senior-comms title gate — must contain a tracked title fragment.
  2. Person/company sanity floor — drop mis-parsed non-person fragments.
  3. Suppression window — the same move won't re-fire within 90 days.

Runs in the daily morning brief (no longer manual-only). Detection is
free (a pure parse of already-fetched GDELT/RSS signals).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from tool import bd_retention
from tool.profiles import active_profile
from tool.state_paths import state_dir, state_root

log = logging.getLogger("brief.cascade")

STATE_DIR = state_dir()
EVENTS_FILE = STATE_DIR / "cascade_events.json"
SUPPRESS_FILE = STATE_DIR / "cascade_suppression.json"

SUPPRESS_DAYS = 90
MAX_EVENTS_KEEP = 200
# Dashboard retention follows the shared BD-Leads rule (tool.bd_retention):
# a cascade move drops 30 days after it was first presented (detected_at),
# or 90 days if it's been followed up on either side. See event_bucket /
# purge_expired below.

# Senior titles that count as cascade-worthy moves. Lower-cased,
# substring-matched against the article title. Order doesn't matter;
# any one match is sufficient. Per-profile: comms is the live list;
# marketing is a first-draft senior-marketing taxonomy (review with the
# marketing team). The active profile picks which set is used.
_COMMS_SENIOR_TITLES = [
    "chief communications officer", "cco",
    "chief comms officer",
    "communications director", "director of communications",
    "comms director", "director of comms",
    "group communications director", "group comms director",
    "global head of communications", "global head of comms",
    "head of communications", "head of comms",
    "head of internal communications", "head of internal comms",
    "head of ic",
    "head of corporate affairs",
    "corporate affairs director", "director of corporate affairs",
    "vp communications", "vp comms",
    "svp communications", "svp comms",
    "chief brand officer",
    "chief reputation officer",
    "chief public affairs officer",
]

# FIRST DRAFT — senior marketing/brand moves. Bare "cmo" is deliberately
# omitted (it also abbreviates Chief Medical Officer).
_MARKETING_SENIOR_TITLES = [
    "chief marketing officer",
    "chief brand officer", "chief growth officer", "chief customer officer",
    "global marketing director", "group marketing director",
    "marketing director", "director of marketing",
    "global head of marketing", "head of marketing",
    "vp marketing", "svp marketing", "vice president of marketing",
    "brand director", "director of brand", "head of brand",
    "global head of brand", "head of brand marketing",
    "marketing and communications director",
    "head of growth", "vp growth",
    "head of digital marketing", "digital marketing director",
    "head of performance marketing", "performance marketing director",
    "head of product marketing", "product marketing director",
    "head of ecommerce", "ecommerce director", "director of ecommerce",
    "head of customer marketing", "crm director",
]

SENIOR_TITLES = (
    _MARKETING_SENIOR_TITLES if active_profile().key == "marketing"
    else _COMMS_SENIOR_TITLES
)

# Companies we never want to flag — generic words that look like proper
# nouns to a regex but aren't. Catches common parse errors.
NON_COMPANY_TOKENS = {
    "the", "a", "an", "his", "her", "their", "its",
    "uk", "us", "ftse", "plc", "ltd", "limited", "group",
    "company", "team", "role", "post", "position",
    "communications", "comms", "department",
}


# ----- locking + atomic write -----------------------------------------
try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


@contextmanager
def _locked(path: Path):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with _LOCK:
        fd = None
        if _HAVE_FCNTL:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fd is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(exist_ok=True, parents=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".tmp",
        dir=str(path.parent), delete=False,
    )
    try:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, str(path))
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


# ----- data model -----------------------------------------------------
@dataclass
class CascadeEvent:
    event_id: str
    person_name: str
    new_company: str
    old_company: str            # may be empty if not in headline
    role: str
    article_url: str
    article_title: str
    article_date: str
    source: str
    detected_at: str
    # Two derived BD actions per move. Status is per-action so Sara can
    # call the old firm and dismiss the new-firm angle independently.
    old_co_status: str = "active"   # active / called / dismissed / n/a
    new_co_status: str = "active"   # active / called / dismissed
    old_co_opener: str = ""
    new_co_opener: str = ""
    # high = touches a watchlist account; medium = broader-market UK seat.
    confidence: str = "high"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----- entity extraction ----------------------------------------------
# Two-pass approach. Pattern A handles "Person, joins/appointed/named at
# Company as Role" forms. Pattern B handles "Company appoints/names
# Person as Role" forms. Both also try to pull an old-company from
# "formerly of X" / "previously at X" / "from X" clauses.
#
# Names are two-or-three capitalised tokens; companies are 1-4
# capitalised tokens immediately following anchor words ("at", "joins",
# "from", "of"). This is deliberately conservative — false positives
# burn Sara's outreach budget, so we'd rather drop a real move than
# fire a fake one.

# Action verbs in the title (anchor for the move). Captured group so we
# can switch on which verb fired — the verb form tells us which side
# is the person and which is the company.
_VERB_PAT = re.compile(
    r"\b(joins?|joined|appoints?|appointed|names?|named|hires?|hired|"
    r"welcomes?|welcomed|promotes?|promoted|moves?\s+to|takes?\s+up|"
    r"becomes?|steps?\s+up\s+to)\b",
    re.I)

# "company-first" verbs put the org subject first ("BP appoints X as Y").
# Note "appointed"/"named" appear in both lists because they're
# ambiguous; we resolve by checking for passive voice ("has been
# appointed") which flips them back to person-first.
_COMPANY_FIRST_VERBS = {
    "appoints", "names", "hires", "welcomes", "promotes",
    "appointed", "named", "hired", "welcomed", "promoted",
}

_FORMERLY_PAT = re.compile(
    r",?\s*(?:formerly|previously)\s+(?:of|at|with)\s+(.+?)(?:,|$|\s+joins|\s+joined)",
    re.I)

_NAME_TOKEN = re.compile(r"^[A-Z][a-zA-Z'\-]+$")


def _looks_like_real_company(co: str) -> bool:
    co_clean = (co or "").strip()
    if not co_clean:
        return False
    if co_clean.lower() in NON_COMPANY_TOKENS:
        return False
    # Two-letter all-cap acronyms (BP, GE, IK, JD, etc.) are real
    # companies; for anything longer require ≥3 chars to avoid stub
    # words like "of" / "to" sneaking through.
    if len(co_clean) < 2:
        return False
    if len(co_clean) == 2 and not co_clean.isupper():
        return False
    # A purely-numeric token ("District 163", "Dist 63") means we grabbed a
    # local-government / school-district headline, not a corporate name.
    if any(tok.isdigit() for tok in co_clean.split()):
        return False
    return True


# Function / verb / marker words that never sit inside a real person name.
# Their presence means the regex captured a sentence fragment, not a name
# (e.g. "To Replace Retiring Spector Bishop As Dist").
_NON_NAME_WORDS = {
    "to", "the", "a", "an", "as", "of", "and", "or", "for", "at", "in",
    "on", "by", "with", "from", "new", "replace", "replaces", "replacing",
    "retiring", "retire", "retires", "hired", "hire", "hires", "joins",
    "join", "joined", "appointed", "appoints", "named", "names", "after",
    "amid", "following", "who", "will", "has", "have", "been", "is", "was",
    "up", "takes", "steps", "becomes", "moves", "welcomes", "promoted",
    "promotes", "dist", "district", "school", "county", "board",
}


def _looks_like_real_person(name: str) -> bool:
    name = (name or "").strip()
    if not name or " " not in name:
        return False
    # Avoid common titles being captured as names.
    bad_starts = ("New ", "The ", "Group ", "Chief ", "Senior ")
    if name.startswith(bad_starts):
        return False
    toks = name.split()
    # A real name is 2-4 capitalised tokens, no digits, no function/verb
    # words — anything else is a mis-parsed headline fragment.
    if len(toks) > 4:
        return False
    if any(ch.isdigit() for ch in name):
        return False
    if any(t.lower().strip(".,'\"") in _NON_NAME_WORDS for t in toks):
        return False
    return True


def _extract_role(title: str) -> str:
    """Pull the longest senior-comms title fragment present in the
    article title. Longest-match wins so 'Head of Internal Comms' beats
    'Head of Comms' on the same string."""
    title_lc = title.lower()
    matches = [t for t in SENIOR_TITLES if t in title_lc]
    if not matches:
        return ""
    matches.sort(key=len, reverse=True)
    return matches[0]


def _trim_capitalised_prefix(s: str) -> str:
    """Take a string like 'NatWest as Chief Communications Officer' and
    keep only the leading run of capitalised tokens — i.e. the company
    name itself. Stops at any token that isn't a Name-Cap word."""
    out_tokens: list[str] = []
    for tok in s.split():
        # Permit Name-cap tokens AND known company connectors (& . - ')
        if _NAME_TOKEN.match(tok) or tok in {"&", "and", "of"}:
            out_tokens.append(tok)
        else:
            break
    return " ".join(out_tokens).strip()


def _trim_capitalised_suffix(s: str) -> str:
    """Take a string like 'has been appointed at NatWest' and keep only
    the trailing run of capitalised tokens. Used when person is to the
    LEFT of the verb."""
    tokens = s.split()
    out_tokens: list[str] = []
    for tok in reversed(tokens):
        if _NAME_TOKEN.match(tok) or tok in {"&", "and", "of"}:
            out_tokens.insert(0, tok)
        else:
            break
    return " ".join(out_tokens).strip()


def _strip_leading_connectors(s: str) -> str:
    """Drop 'as', 'as the', 'as new', 'the', 'new' from the start of
    the company/person string — these leak in when the verb captures
    'appointed as' or 'named the new'."""
    return re.sub(r"^(?:as\s+|the\s+|new\s+|a\s+)+", "", s, flags=re.I).strip()


def _extract_company_after_role(title: str, role_start: int,
                                role: str) -> str:
    """For 'X appointed Role at Y' / 'X has been appointed as Role at Y',
    the company sits after the role anchor. Pulls the cap-word run
    after the first 'at|of|for|with' connector."""
    role_end = role_start + len(role) if role_start >= 0 else 0
    after = title[role_end:].strip()
    am = re.search(
        r"\b(?:at|of|for|with)\s+([A-Z][A-Za-z0-9&.\-' ]+?)"
        r"(?:\s+(?:after|amid|in|on|following|today|yesterday|since|while)|"
        r"\.|,|$)", after)
    if not am:
        return ""
    return _trim_capitalised_prefix(am.group(1).strip())


def _strip_passive_markers(s: str) -> str:
    """Remove 'has been', 'have been', 'was', 'is' from the end of the
    subject portion of a passive-voice headline ('Sarah Chen has been
    appointed' → 'Sarah Chen')."""
    return re.sub(r"\s+(?:has\s+been|have\s+been|was|is|will\s+be)\s*$",
                  "", s, flags=re.I).strip()


def _extract_move(title: str) -> dict | None:
    """Returns {person, new_co, old_co, role} or None.

    Strategy: anchor on the role (longest senior-comms title in the
    headline) and the action verb. Headline structures we handle:

      A) "Person joins/joined/moves to Company [as Role]"
      B) "Company appoints/names/hires/welcomes Person [as Role]"
      C) "Person appointed/named [as] Role at Company"   (no object after verb)
      D) "Person has been appointed [as] Role at Company"  (passive)

    Disambiguation rule for ambiguous verbs ('appointed', 'named'):
    if the verb is followed immediately by the role anchor (no
    capitalised object between them), the structure is C/D — person
    is on the left, company is after the role via 'at|of|for'.
    Otherwise it's A or B by verb category.
    """
    role = _extract_role(title)
    if not role:
        return None

    title_lc = title.lower()
    role_start = title_lc.find(role)
    before = title[:role_start].strip() if role_start > 0 else title

    # Pull "formerly of X" out before we tokenize anything else.
    old_co = ""
    fm = _FORMERLY_PAT.search(before)
    if fm:
        old_co = fm.group(1).strip().rstrip(",")
        before = before[:fm.start()] + before[fm.end():]
        before = re.sub(r"\s+", " ", before).strip().rstrip(",")

    # Strip trailing connectors before the role anchor.
    before = re.sub(r"\s+(?:as|to\s+be|the\s+new|new|the)\s*$",
                    "", before, flags=re.I).strip().rstrip(",")

    vm = _VERB_PAT.search(before)
    if not vm:
        return None
    verb_lc = vm.group(1).lower().strip()

    left = before[:vm.start()].strip().rstrip(",")
    right = before[vm.end():].strip().lstrip(",")
    right = _strip_leading_connectors(right)

    # Detect "verb immediately precedes role" — the C/D structure
    # (no capitalised object between verb and role anchor).
    no_object_after_verb = not bool(_trim_capitalised_prefix(right))

    # Active company-first verbs (BP appoints Person) — but only if
    # there's an object after the verb. Otherwise it's past-participle
    # modifying the subject (Person was appointed).
    passive = bool(re.search(
        r"\b(?:has\s+been|have\s+been|was|is|will\s+be)\s+$",
        before[:vm.start()] + " ", re.I))

    if verb_lc in _COMPANY_FIRST_VERBS and not passive and not no_object_after_verb:
        # Company-first (e.g. "Vodafone appoints Priya Patel ...").
        company = _trim_capitalised_suffix(left)
        person = _trim_capitalised_prefix(right)
    else:
        # Person-first. Strip passive markers from left if any.
        left_clean = _strip_passive_markers(left)
        person = _trim_capitalised_suffix(left_clean)
        # Try right side first (for "Person joins Company as Role"),
        # then fall back to "at Company" after the role anchor
        # (for "Person appointed Role at Company").
        company = _trim_capitalised_prefix(right)
        if not _looks_like_real_company(company):
            company = _extract_company_after_role(title, role_start, role)

    person = _strip_leading_connectors(person)
    company = _strip_leading_connectors(company)

    # Final fallback — sometimes the verb form is non-canonical and
    # the standard branch grabs the wrong side. If the chosen 'company'
    # actually looks like a person name (two cap-words, no usual
    # company tokens like Ltd/Group/PLC), try swapping with person.
    if (_looks_like_real_person(company) and not _looks_like_real_person(person)
            and " " in company):
        person, company = company, person
        person = _strip_leading_connectors(person)

    # Last-chance: if company still bad, try the "at Company" fallback.
    if not _looks_like_real_company(company):
        cand = _extract_company_after_role(title, role_start, role)
        if _looks_like_real_company(cand):
            company = cand

    if not _looks_like_real_person(person):
        return None
    if not _looks_like_real_company(company):
        return None
    if company.lower() in {t for t in SENIOR_TITLES}:
        return None

    return {
        "person": person.strip(),
        "new_co": company.strip(),
        "old_co": old_co.strip() if _looks_like_real_company(old_co) else "",
        "role":   role,
    }


# ----- openers --------------------------------------------------------
def _old_co_opener(person: str, role: str, old_co: str) -> str:
    # Person may be unknown on a pure-departure announcement.
    lead = (f"saw that {person} just left {old_co}" if person
            else f"saw that {old_co}'s {role} seat looks to have opened up")
    return (f"Hi — {lead}. Comms teams often reshape after a senior "
            f"departure; happy to share what we're seeing in the senior "
            f"comms market and flag any movers in your space if useful.")


def _new_co_opener(person: str, role: str, new_co: str) -> str:
    greet = f"Hi {person.split()[0]} — " if person else "Hi — "
    subj = (f"congrats on the {role} role at {new_co}" if person
            else f"congrats to {new_co} on the new {role}")
    return (f"{greet}{subj}. New senior comms hires often reshape their "
            f"teams in the first 6-12 months; would love a quick chat about "
            f"how we can help if/when you're sizing up the team.")


# ----- events store ---------------------------------------------------
def _load_events() -> list[dict]:
    if not EVENTS_FILE.exists():
        return []
    try:
        data = json.loads(EVENTS_FILE.read_text())
        return data if isinstance(data, list) else []
    except Exception as e:
        log.info("cascade: events load failed: %s", e)
        return []


def _save_events(events: list[dict]) -> None:
    events.sort(key=lambda e: e.get("detected_at", ""), reverse=True)
    if len(events) > MAX_EVENTS_KEEP:
        events = events[:MAX_EVENTS_KEEP]
    payload = json.dumps(events, indent=2)
    _atomic_write(EVENTS_FILE, payload)
    try:
        from tool import github_state
        github_state.push_async(
            "tool/state/cascade_events.json", payload,
            "state: update cascade-hire events")
    except Exception as e:
        log.info("cascade: github persist skipped: %s", e)


def _load_suppression() -> dict:
    if not SUPPRESS_FILE.exists():
        return {}
    try:
        data = json.loads(SUPPRESS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_suppression(s: dict) -> None:
    payload = json.dumps(s, indent=2)
    _atomic_write(SUPPRESS_FILE, payload)
    try:
        from tool import github_state
        github_state.push_async(
            "tool/state/cascade_suppression.json", payload,
            "state: update cascade suppression window")
    except Exception:
        pass


def _is_suppressed_key(key: str, suppression: dict) -> bool:
    iso = suppression.get(key)
    if not iso:
        return False
    try:
        last = datetime.fromisoformat(iso)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - last) < timedelta(days=SUPPRESS_DAYS)


def event_bucket(e: dict) -> str:
    """Roll a two-sided cascade event up to a single dashboard triage
    bucket — 'active', 'followed_up' or 'dismissed' — matching the BD
    Leads / Hire Watch filter pills. An event is active while either real
    (status != 'n/a') side is still active; followed-up once a side has
    been called / followed-up and none stays active; dismissed only when
    every real side is dismissed. Single source of truth shared by the
    dashboard render and the retention filter."""
    sides = [e.get("old_co_status", "active"), e.get("new_co_status", "active")]
    sides = [s for s in sides if s != "n/a"]
    if any(s == "active" for s in sides):
        return "active"
    if any(s in ("called", "followed_up") for s in sides):
        return "followed_up"
    if sides and all(s == "dismissed" for s in sides):
        return "dismissed"
    return "active"


def purge_expired(events: list[dict]) -> tuple[list[dict], int]:
    """Drop events past their BD-Leads dashboard-retention window
    (tool.bd_retention), anchored on detected_at (when the move was first
    presented): 30 days for active / dismissed events, 90 days once
    followed up on either side. Returns (kept_events, removed_count)."""
    kept: list[dict] = []
    removed = 0
    for e in events:
        if bd_retention.is_expired(e.get("detected_at"), event_bucket(e)):
            removed += 1
            continue
        kept.append(e)
    return kept, removed


# ----- public API -----------------------------------------------------
def _event_on_watchlist(e: dict) -> bool:
    """True if either side of the event resolves to a watchlist account.
    Fail-open: if the watchlist can't load, resolve_account returns the
    company string, so events are kept rather than wrongly dropped."""
    from tool.account_match import resolve_account
    for co in (e.get("old_company", ""), e.get("new_company", "")):
        if co and resolve_account(co, co):
            return True
    return False


def _event_kept(e: dict) -> bool:
    """Read-path relevance filter. Keeps watchlist events AND the newer
    broader-market UK events (which carry a confidence tier). Drops legacy
    events persisted before the gate existed (no confidence + not watchlist)
    AND — self-heal — any event whose person field is a mis-parsed headline
    fragment rather than a real name, e.g. the old US school-district
    'To Replace Retiring … As Dist' row. Departure-only events (no person)
    are unaffected."""
    person = e.get("person_name") or e.get("person") or ""
    if person and not _looks_like_real_person(person):
        return False
    return _event_on_watchlist(e) or e.get("confidence") in ("high", "medium")


def _watchlist_first(events: list[dict]) -> list[dict]:
    """Stable sort putting watchlist/high-confidence seats first while
    preserving the detected-at recency order within each tier."""
    return sorted(events, key=lambda e: e.get("confidence", "high") != "high")


def list_active() -> list[dict]:
    """Events where at least one side (old-co or new-co) still has an
    active BD action. Hides fully-triaged and retention-expired events;
    filters legacy junk so it can't leak into the Top-3 builder.
    Watchlist/high-confidence first."""
    out = []
    for e in _load_events():
        old_st = e.get("old_co_status", "active")
        new_st = e.get("new_co_status", "active")
        if (old_st != "active" and new_st != "active") or not _event_kept(e):
            continue
        if bd_retention.is_expired(e.get("detected_at"), event_bucket(e)):
            continue  # past its 30-day window — gone from every tab
        out.append(e)
    return _watchlist_first(out)


def list_all() -> list[dict]:
    # Hide retention-expired events (30d default / 90d followed-up, from
    # detected_at) immediately on the read path — the daily scour persists
    # the actual removal. Mirrors predictor_pipeline.all_predictors'
    # in-memory filtering.
    kept, _ = purge_expired(_load_events())
    return _watchlist_first([e for e in kept if _event_kept(e)])


def mark(event_id: str, side: str, status: str) -> bool:
    """Mark one side of a cascade event. side ∈ {old_co, new_co},
    status ∈ {active, called, dismissed, n/a}."""
    if side not in {"old_co", "new_co"}:
        return False
    # "called" kept as legacy alias for "followed_up" so old records
    # (and any in-flight requests) still validate.
    if status not in {"active", "called", "followed_up", "dismissed", "n/a"}:
        return False
    field_name = f"{side}_status"
    ts_field = f"{side}_status_at"
    with _locked(EVENTS_FILE):
        events = _load_events()
        hit = False
        for e in events:
            if e.get("event_id") == event_id:
                e[field_name] = status
                # Stamp when this side was triaged so the post-triage
                # retention clock has a start point; clear it if the side
                # reverts to active / n-a (no longer settled).
                if status in {"called", "followed_up", "dismissed"}:
                    e[ts_field] = _now_iso()
                else:
                    e.pop(ts_field, None)
                hit = True
                break
        if not hit:
            return False
        _save_events(events)
    return True


# ----- scour orchestration --------------------------------------------
def _event_id(person: str, old_co: str, new_co: str, url: str) -> str:
    h = hashlib.sha1(
        f"{person.lower()}|{old_co.lower()}|{new_co.lower()}|{url}"
        .encode("utf-8")).hexdigest()
    return h[:16]


def _load_signals_raw() -> list[dict]:
    """Read latest_signals.json from morning-brief output — bypassing
    the dashboard-side leadership_change filter (which hides them from
    Today's Leads). Those are exactly the rows we want here."""
    p = STATE_DIR / "latest_signals.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except Exception as e:
        log.info("cascade: signals load failed: %s", e)
        return []


# Broader-market UK-relevance gate (non-watchlist vacated-seat tier only).
# A watchlist seat is relevant wherever it's reported; a non-watchlist seat
# is only a UK-desk lead if it is UK.
_UK_GEO_RX = re.compile(
    r"\b(?:uk|u\.k\.|united kingdom|britain|british|england|scotland|"
    r"scottish|wales|welsh|northern ireland|london|manchester|birmingham|"
    r"leeds|glasgow|edinburgh|bristol|liverpool|cambridge|oxford|cardiff|"
    r"belfast|ftse)\b|\.co\.uk\b",
    re.IGNORECASE,
)


def _is_uk(geo: str, text: str) -> bool:
    if (geo or "").upper() == "UK":
        return True
    return bool(_UK_GEO_RX.search(text or ""))


def _detect_events(signals: list[dict]) -> list[dict]:
    """Pure detection + tiered relevance gate (no persistence). One
    side-resolved move dict per emitted event:

      * REPLACEMENT SEARCH (old-co / vacated seat) — the commission play.
        Surfaced when the vacated seat's employer is a watchlist account
        (confidence=high) OR any UK employer (confidence=medium / broader
        market): a vacated senior-comms seat is a search mandate anywhere in
        the UK, not only at the ~550 watchlist names.
      * RE-ORG WATCH (new-co) — speculative team build-out, kept
        WATCHLIST-ONLY (not worth surfacing for a random employer).

    Precision floors keep junk out: the move must carry a senior-comms role
    + move verb (following's gate); the broader tier additionally requires
    UK relevance AND a sane employer name (following._looks_like_company).
    Off-patch / mis-parsed US headlines resolve to nothing on both tiers and
    are dropped."""
    from tool.account_match import resolve_account
    from tool import following

    # Vacated seats — watchlist + broader-market UK — keyed by source URL.
    seats_by_url: dict[str, dict] = {}
    try:
        for r in following.detect_following(signals, include_unresolved=True):
            seats_by_url.setdefault(r.get("url", ""), r)
    except Exception as e:
        log.info("cascade: following extract failed: %s", e)

    out: list[dict] = []
    for s in signals:
        if not isinstance(s, dict):
            continue
        title = s.get("title") or ""
        summary = s.get("summary") or ""
        url = s.get("url") or ""
        text = title + " . " + summary

        move = _extract_move(title)

        # Re-org-watch (new-co) side: WATCHLIST ONLY (speculative).
        new_co_raw = (move or {}).get("new_co", "")
        new_acct = resolve_account(new_co_raw, new_co_raw) if new_co_raw else None

        # Replacement-search (old-co / vacated seat) side: watchlist (high)
        # OR a UK employer (medium / broader market).
        seat = seats_by_url.get(url)
        old_acct = None
        old_watchlist = False
        old_role = ""
        if seat:
            old_role = seat.get("vacated_role", "") or ""
            if seat.get("watchlist"):
                old_acct, old_watchlist = (seat.get("company") or None), True
            elif _is_uk(seat.get("geo", ""), text):
                old_acct = seat.get("company") or None        # broader-market UK
        elif move and move.get("old_co"):
            r = resolve_account(move["old_co"], move["old_co"])
            if r:
                old_acct, old_watchlist = r, True

        if not (old_acct or new_acct):
            continue

        person = (move or {}).get("person", "") or ""
        role = (move or {}).get("role", "") or old_role
        confidence = "high" if (old_watchlist or new_acct) else "medium"
        out.append({
            "person":      person,
            "role":        role,
            "old_company": old_acct or "",
            "new_company": new_acct or "",
            "old_status":  "active" if old_acct else "n/a",
            "new_status":  "active" if new_acct else "n/a",
            "confidence":  confidence,
            "url":         url,
            "title":       title,
            "published":   s.get("published") or "",
            "source":      s.get("source") or "",
        })
    return out


def scour(signals: list[dict] | None = None) -> dict:
    """Detect senior-comms moves, watchlist-gate them, and persist new
    events. Cheap (no HTTP — pure parse of already-fetched data) so it runs
    in the daily brief. Pass `signals` to gate an in-memory batch; otherwise
    reads latest_signals.json."""
    raw = signals if signals is not None else _load_signals_raw()
    if not raw:
        log.info("cascade: no signals to parse")
        return {"signals_seen": 0, "moves_detected": 0, "events_new": 0,
                "detail": "no signals available"}

    detected = _detect_events(raw)

    with _locked(EVENTS_FILE):
        events = _load_events()
        known = {e["event_id"] for e in events}
        suppression = _load_suppression()
        new_count = 0

        for d in detected:
            person, old_co, new_co = d["person"], d["old_company"], d["new_company"]
            url = d["url"]
            supp_key = f"{person.lower()}::{old_co.lower()}::{new_co.lower()}"
            if _is_suppressed_key(supp_key, suppression):
                continue
            eid = _event_id(person, old_co, new_co, url)
            if eid in known:
                continue
            ev = CascadeEvent(
                event_id=eid,
                person_name=person,
                new_company=new_co,
                old_company=old_co,
                role=d["role"],
                article_url=url,
                article_title=d["title"],
                article_date=d["published"],
                source=d["source"],
                detected_at=_now_iso(),
                old_co_status=d["old_status"],
                new_co_status=d["new_status"],
                old_co_opener=(_old_co_opener(person, d["role"], old_co)
                               if d["old_status"] == "active" else ""),
                new_co_opener=(_new_co_opener(person, d["role"], new_co)
                               if d["new_status"] == "active" else ""),
                confidence=d.get("confidence", "high"),
            )
            events.append(asdict(ev))
            known.add(eid)
            suppression[supp_key] = _now_iso()
            new_count += 1

        events, purged = purge_expired(events)
        if new_count or purged:
            _save_events(events)
        if new_count:
            _save_suppression(suppression)

        return {"signals_seen": len(raw),
                "moves_detected": len(detected),
                "events_new": new_count,
                "purged_triaged": purged}


# ----- CLI ------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")
    import pprint
    pprint.pprint(scour())
