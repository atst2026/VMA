"""Cascade-Hire Watch — detects senior comms moves in public news and
emits two derived BD actions per move:

  (A) OLD-COMPANY EXIT-RISK
      When a CCO / Director of Comms / Head of IC leaves a UK firm,
      their direct reports are now flight risks AND the firm needs a
      replacement search. Action: call the old firm proactively to
      offer market mapping / replacement search.

  (B) NEW-COMPANY RE-ORG PRESSURE
      Senior comms hires reshape their team within 6-12 months. The new
      firm becomes a forward predictor pipeline entry. Action: track
      the new firm for downstream hiring.

Why this works
==============
Manufactures opportunities from already-public news that other firms
of Sara's size don't systematically track. No JobAdder dependency.
Detection is essentially free (reuses morning_brief's GDELT/RSS scour).

Honest precision expectations
=============================
Title-only regex extraction is noisy. Conversion path:

  ~5 cascade detections per week → ~40% have a usable old-co (so old-co
  exit-risk angle fires on ~2/wk) → maybe 1 actionable call per week
  off the (A) angle, with (B) maturing into Q2/Q3.

Guards against false positives:
  1. Senior-comms title gate — must contain a tracked title fragment.
  2. Entity-extraction confidence floor — drop events where the
     extracted person looks like a non-person (single word, generic).
  3. Suppression window — same person+new-co pair won't re-fire
     within 90 days (slow signal; longer suppression than trade press).
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

log = logging.getLogger("brief.cascade")

STATE_DIR = Path(__file__).resolve().parent / "state"
EVENTS_FILE = STATE_DIR / "cascade_events.json"
SUPPRESS_FILE = STATE_DIR / "cascade_suppression.json"

SUPPRESS_DAYS = 90
MAX_EVENTS_KEEP = 200

# Senior comms titles that count as cascade-worthy moves. Lower-cased,
# substring-matched against the article title. Order doesn't matter;
# any one match is sufficient.
SENIOR_TITLES = [
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
    return True


def _looks_like_real_person(name: str) -> bool:
    name = (name or "").strip()
    if not name or " " not in name:
        return False
    # Avoid common titles being captured as names.
    bad_starts = ("New ", "The ", "Group ", "Chief ", "Senior ")
    if name.startswith(bad_starts):
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
    return (f"Hi — saw that {person} just left {old_co}. Comms teams "
            f"often reshape after a {role} departure; happy to share what "
            f"we're seeing in the senior comms market and flag any movers "
            f"in your space if useful.")


def _new_co_opener(person: str, role: str, new_co: str) -> str:
    return (f"Hi {person.split()[0]} — congrats on the {role} role at "
            f"{new_co}. New senior comms hires often reshape their teams "
            f"in the first 6-12 months; would love a quick chat about how "
            f"we can help if/when you're sizing up the team.")


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


def _is_suppressed(person: str, new_co: str, suppression: dict) -> bool:
    key = f"{person.lower()}::{new_co.lower()}"
    iso = suppression.get(key)
    if not iso:
        return False
    try:
        last = datetime.fromisoformat(iso)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - last) < timedelta(days=SUPPRESS_DAYS)


# ----- public API -----------------------------------------------------
def list_active() -> list[dict]:
    """Events where at least one side (old-co or new-co) still has an
    active BD action. Hides events Sara has fully triaged."""
    out = []
    for e in _load_events():
        old_st = e.get("old_co_status", "active")
        new_st = e.get("new_co_status", "active")
        if old_st == "active" or new_st == "active":
            out.append(e)
    return out


def list_all() -> list[dict]:
    return _load_events()


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
    with _locked(EVENTS_FILE):
        events = _load_events()
        hit = False
        for e in events:
            if e.get("event_id") == event_id:
                e[field_name] = status
                hit = True
                break
        if not hit:
            return False
        _save_events(events)
    return True


# ----- scour orchestration --------------------------------------------
def _event_id(person: str, new_co: str, url: str) -> str:
    h = hashlib.sha1(
        f"{person.lower()}|{new_co.lower()}|{url}".encode("utf-8")).hexdigest()
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


def scour() -> dict:
    """Read morning_brief signals, find senior-comms moves, emit
    cascade events. Cheap (no HTTP — pure parse of already-fetched
    data) so safe to schedule daily."""
    raw = _load_signals_raw()
    if not raw:
        log.info("cascade: no signals to parse")
        return {"signals_seen": 0, "moves_detected": 0, "events_new": 0,
                "detail": "no signals available"}

    candidates = [s for s in raw if (s.get("kind") or "").lower()
                                    == "leadership_change"]
    # Also include any non-leadership_change row whose title contains a
    # senior comms title — the kind classifier doesn't catch every
    # appointment-shaped headline.
    if not candidates:
        candidates = [s for s in raw
                      if any(t in (s.get("title") or "").lower()
                             for t in SENIOR_TITLES)]

    with _locked(EVENTS_FILE):
        events = _load_events()
        known = {e["event_id"] for e in events}
        suppression = _load_suppression()
        new_count = 0
        moves_detected = 0

        for s in candidates:
            title = s.get("title") or ""
            move = _extract_move(title)
            if not move:
                continue
            moves_detected += 1
            if _is_suppressed(move["person"], move["new_co"], suppression):
                continue
            url = s.get("url") or ""
            eid = _event_id(move["person"], move["new_co"], url)
            if eid in known:
                continue
            ev = CascadeEvent(
                event_id=eid,
                person_name=move["person"],
                new_company=move["new_co"],
                old_company=move["old_co"],
                role=move["role"],
                article_url=url,
                article_title=title,
                article_date=s.get("published") or "",
                source=s.get("source") or "",
                detected_at=_now_iso(),
                old_co_status="active" if move["old_co"] else "n/a",
                new_co_status="active",
                old_co_opener=(_old_co_opener(move["person"], move["role"],
                                              move["old_co"])
                               if move["old_co"] else ""),
                new_co_opener=_new_co_opener(move["person"], move["role"],
                                             move["new_co"]),
            )
            events.append(asdict(ev))
            known.add(eid)
            suppression[f"{move['person'].lower()}::{move['new_co'].lower()}"] \
                = _now_iso()
            new_count += 1

        if new_count:
            _save_events(events)
            _save_suppression(suppression)

        return {"signals_seen": len(raw),
                "candidates": len(candidates),
                "moves_detected": moves_detected,
                "events_new": new_count}


# ----- CLI ------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")
    import pprint
    pprint.pprint(scour())
