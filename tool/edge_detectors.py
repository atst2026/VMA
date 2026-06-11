"""Edge detectors — second-order BD signals derived from data the
pipeline already holds. No new scraping, no new workflows: each detector
is a pure read of an existing feed, surfacing the lead a jobs-board
competitor never sees.

  1. INTERIM-TO-PERM WATCH (detect_interim_covers)
     A senior comms/marketing seat advertised as interim / FTC /
     maternity cover is a dated promise of a permanent search: the seat
     exists, the budget exists, and the perm brief typically follows
     within two quarters — usually before it is ever advertised. Job
     boards list the interim ad; nobody watches for the perm search that
     follows it. We watch the company, not the ad.

  2. FOLLOW-ON BUILD-OUT (detect_follow_on)
     When a senior leader lands at a new company (a cascade move with
     both sides parsed), the vacated seat is already a lead on the old
     firm. The SECOND fee — the one competitors miss — is the new
     leader's team build-out, which lands one to two quarters after they
     start, pitched to a buyer with no incumbent-agency loyalty.

  3. STATED COMMS INTENT (intent_phrase)
     Companies literally announce hiring intent in funding and results
     coverage ("invest in our brand", "strengthen communications",
     "scale the marketing team"). A phrase match upgrades a generic
     trigger row into a stated-intent row — the AD can quote the
     company's own words on the call.

The two row detectors PERSIST their detections (edge_watches.json), so a
watch outlives its source feed: Live Jobs rotate after 7 days and
cascade events churn, but the interim ad's real payoff is the perm
search months later. Persisted watches follow the shared BD retention
rule (tool.bd_retention — 30 days, 90 when followed up) and the standard
predictor triage overlay, so ✓/✕ behave exactly like every other row.

All detectors are defensive: they never raise (skip the bad record /
return "" instead) because they run on every dashboard render.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from tool.state_paths import state_dir

log = logging.getLogger("brief.edge")

STATE_DIR = state_dir()
WATCH_FILE = STATE_DIR / "edge_watches.json"

# Predicted windows shown on the card (compact, matches predictor rows).
INTERIM_WINDOW = "~2-6 months"
FOLLOW_ON_WINDOW = "~3-6 months"

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


# ---- detection patterns ---------------------------------------------------
# Senior gate: an interim cover only predicts a paid perm search when the
# seat itself is senior. Deliberately excludes bare "officer"/"manager"
# (junior comms officers cycle through cover roles constantly).
_SENIOR_RX = re.compile(
    r"\b(?:chief|director|head of|vp|svp|evp|president)\b", re.I)

# Cover language. "Contract"/"cover" alone are too loose (every job board
# says "permanent contract"); these forms are unambiguous.
_INTERIM_RX = re.compile(
    r"\b(?:interim|maternity(?:\s+leave)?\s+cover|"
    r"parental(?:\s+leave)?\s+cover|fixed[\s-]?term|ftc|"
    r"(?:6|9|12)[\s-]?month)\b", re.I)

# Stated-intent phrases, as companies actually write them in funding and
# results coverage. Each match returns the company's own words.
_INTENT_RXS = [re.compile(p, re.I) for p in (
    r"invest(?:ing|ment)?[\w\s,]{0,24}?\bin (?:our |the |its )?"
    r"(?:brand|communications?|comms|marketing|public relations|pr)\b"
    r"(?: (?:team|function|capability|presence))?",
    r"strengthen(?:ing)? (?:our |the |its )?"
    r"(?:communications?|comms|brand|marketing)"
    r"(?: (?:team|function|capability))?",
    r"(?:build(?:ing)?(?: out)?|grow(?:ing)?|expand(?:ing)?|"
    r"scal(?:e|ing)(?: up)?|doubl(?:e|ing) down on) "
    r"(?:our |the |its |a )?(?:communications?|comms|marketing|brand|pr)"
    r"(?: (?:team|function|capability|presence|efforts?))?",
    r"(?:appoint(?:ing)?|hir(?:e|ing)|recruit(?:ing)?|search(?:ing)? for) "
    r"(?:a |an |its |their )?(?:new |first )?"
    r"(?:head of|director of|chief|cmo|cco|vp of)[\w\s]{0,24}",
    r"(?:agency|creative|media|pr) (?:review|pitch)\b",
    r"review(?:ing)? (?:of )?(?:our |the |its )?"
    r"(?:agency|communications?|comms|marketing) "
    r"(?:arrangements|roster|support)",
)]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")


# ---- the persistent watch store -------------------------------------------
@contextmanager
def _locked():
    """Serialise read-modify-write across threads and processes."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = WATCH_FILE.with_suffix(".lock")
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


def _load_store() -> dict:
    if not WATCH_FILE.exists():
        return {}
    try:
        d = json.loads(WATCH_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_store(data: dict) -> None:
    payload = json.dumps(data, indent=2)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".tmp",
        dir=str(STATE_DIR), delete=False,
    )
    try:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, str(WATCH_FILE))
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise
    # Persist to the repo (background; never blocks the render).
    try:
        from tool import github_state
        github_state.push_async("tool/state/edge_watches.json", payload,
                                "state: update edge watches")
    except Exception:
        pass


def _emit_persisted(kind: str, fresh: list[dict], limit: int) -> list[dict]:
    """Merge fresh detections into the durable watch store and emit every
    un-expired watch of this kind. A watch must outlive its source feed —
    Live Jobs rotate after 7 days, cascade events churn — because the
    payoff (the perm search / the build-out brief) lands months later."""
    try:
        from tool import bd_retention, predictor_status
        statuses = predictor_status.get_statuses()
        with _locked():
            store = _load_store()
            bucket = store.get(kind) or {}
            changed = False
            for row in fresh:
                rec = bucket.get(row["fid"])
                if rec:
                    if rec.get("row") != row:
                        rec["row"] = row          # refresh url/evidence
                        changed = True
                else:
                    bucket[row["fid"]] = {"first_seen": _now_iso(),
                                          "row": row}
                    changed = True
            keep, out = {}, []
            for fid, rec in bucket.items():
                status = statuses.get(fid, "active")
                if bd_retention.is_expired(rec.get("first_seen"), status):
                    changed = True
                    continue
                keep[fid] = rec
                out.append({**rec["row"], "first_seen": rec.get("first_seen")})
            if changed or kind not in store:
                store[kind] = keep
                _save_store(store)
        out.sort(key=lambda r: r.get("first_seen") or "", reverse=True)
        return out[:limit]
    except Exception as e:
        log.info("edge watch store (%s): %s", kind, e)
        return fresh[:limit]


# ---- detector 1: interim-to-perm watch -------------------------------------
def detect_interim_covers(signals: list[dict] | None,
                          limit: int = 10) -> list[dict]:
    """Scan the live job signals for senior interim / FTC / maternity-cover
    ads and emit a perm-search watch on each company. Returns specialist
    rows in the shared _kind shape, durably persisted (see module doc)."""
    fresh = []
    for s in signals or []:
        try:
            title = (s.get("title") or "").strip()
            company = (s.get("company") or "").strip()
            if not title or not company:
                continue
            if not (_INTERIM_RX.search(title) and _SENIOR_RX.search(title)):
                continue
            evidence = (
                f"“{title}” advertised at {company}. An interim "
                f"cover on a senior seat is a dated promise of a permanent "
                f"search — the perm brief typically follows within two "
                f"quarters, before it is ever advertised.")
            url = s.get("url") or ""
            source = s.get("source") or ""
            fresh.append({
                "company": company,
                "evidence": evidence,
                "url": url, "source": source,
                "strength": "medium",
                "window_label": INTERIM_WINDOW,
                "_kind": "interim_watch",
                "fid": ("interim_"
                        + str(s.get("lead_id") or s.get("id")
                              or _slug(company))),
                "status": "active", "_opp": 1.0,
                # A real event so lead_engine / the gate score this like
                # any predictor trigger (key is in both desk taxonomies).
                "events": [{
                    "trigger_key": "interim_watch",
                    "trigger_label": "Interim cover advertised",
                    "evidence": evidence, "url": url, "source": source,
                    "published": s.get("published") or "",
                }],
            })
        except Exception:
            continue
    return _emit_persisted("interim_watch", fresh, limit)


# ---- detector 2: follow-on build-out ---------------------------------------
def detect_follow_on(cascade_events: list[dict] | None,
                     limit: int = 10) -> list[dict]:
    """For each active cascade move with BOTH sides parsed, emit a
    build-out watch on the NEW company. (The move row already leads on the
    vacated seat at the old firm; when only one side parsed, that row
    already covers the new company — skip to avoid a duplicate.)"""
    fresh = []
    for e in cascade_events or []:
        try:
            new_co = (e.get("new_company") or "").strip()
            old_co = (e.get("old_company") or "").strip()
            person = (e.get("person_name") or "").strip()
            role = (e.get("role") or "").strip()
            if not new_co or not old_co or not person:
                continue
            if (e.get("new_co_status") or "active") != "active":
                continue
            evidence = (
                f"{person} has just landed at {new_co}"
                + (f" as {role}" if role else "")
                + f" (from {old_co}). Incoming senior leaders reshape their "
                  f"team within two quarters — the build-out briefs follow, "
                  f"and the new leader carries no incumbent-agency loyalty.")
            url = e.get("article_url") or ""
            source = e.get("source") or ""
            published = e.get("article_date") or e.get("detected_at") or ""
            fresh.append({
                "company": new_co,
                "evidence": evidence,
                "url": url, "source": source,
                "strength": "medium",
                "window_label": FOLLOW_ON_WINDOW,
                "_kind": "follow_on",
                "fid": ("followon_"
                        + str(e.get("event_id") or _slug(new_co))),
                "status": "active", "_opp": 1.2,
                "events": [{
                    "trigger_key": "follow_on",
                    "trigger_label": "New leader build-out",
                    "evidence": evidence, "url": url, "source": source,
                    "published": published,
                }],
            })
        except Exception:
            continue
    return _emit_persisted("follow_on", fresh, limit)


# ---- detector 3: stated comms intent ---------------------------------------
def intent_phrase(text: str | None) -> str:
    """Return the company's own stated-intent words from an evidence
    snippet ('' when none). Pure and cheap — runs per row per render."""
    if not text:
        return ""
    try:
        for rx in _INTENT_RXS:
            m = rx.search(text)
            if m:
                phrase = re.sub(r"\s+", " ", m.group(0)).strip(" .,;:")
                return phrase[:90]
    except Exception:
        pass
    return ""
