"""Agency-relationship ledger — who holds (and held) each company's accounts.

The agency_account_move trigger already detects PRWeek / Campaign "Pitch
Update" items, but until now each move was consumed as a one-off lead
signal and the relationship it disclosed was thrown away. This module
keeps it: every detected move is folded into a per-company history
(agency, discipline, appointed/ended, date, source), so a dossier or lead
card answers "what was their last agency relationship?" from accumulated
public record rather than inferring it from job-ad age alone
(competitor_mandates.py remains the stale-brief inference layer; this is
the actual relationship history).

State: <state_dir>/agency_relationships.json — path resolved per call so
the desk namespace (comms vs marketing) is honoured. Non-fatal
everywhere; a ledger failure can never cost a brief.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from tool.state_paths import state_dir

log = logging.getLogger("brief.agency_relationships")

HISTORY_CAP = 40          # per-company move entries kept
PRUNE_DAYS = 365 * 3      # relationship history is the point — keep 3 years

# Discipline of the account that moved, first match wins (PR before the
# generic "marketing" so a "PR and marketing account" reads as PR).
_DISCIPLINES: list[tuple[str, re.Pattern]] = [
    ("PR", re.compile(r"\b(?:pr|public relations|comms|communications)\b", re.I)),
    ("creative", re.compile(r"\b(?:creative|advertising)\b", re.I)),
    ("media", re.compile(r"\bmedia\b", re.I)),
    ("brand", re.compile(r"\bbrand\b", re.I)),
    ("marketing", re.compile(r"\bmarketing\b", re.I)),
]

_ENDED_RX = re.compile(
    r"\b(?:loses|lost|drops|dropped|splits? (?:with|from)|parts ways|"
    r"ends (?:its |their )?(?:relationship|contract|retainer)|"
    r"moves? (?:its |their )?.{0,25}account (?:away )?from)\b",
    re.IGNORECASE,
)

# Generic agency-name capture for moves naming an agency outside the
# curated list: "appoints Fox & Hare as", "hands the account to Bray Leino".
_CAPTURE_RXS = [
    re.compile(r"\b(?:appoints?|names?|hires?)\s+"
               r"([A-Z][\w&+'’.\-]*(?:\s+[A-Z&+][\w&+'’.\-]*){0,3})\s+(?:as|to)\b"),
    re.compile(r"\bhands?\b.{0,40}?\baccount to\s+"
               r"([A-Z][\w&+'’.\-]*(?:\s+[A-Z&+][\w&+'’.\-]*){0,3})"),
    re.compile(r"\bawards?\b.{0,55}?\b(?:account|brief|business|mandate) to\s+"
               r"([A-Z][\w&+'’.\-]*(?:\s+[A-Z&+][\w&+'’.\-]*){0,3})"),
    re.compile(r"\bswitch(?:es|ed)?\b.{0,40}?\b(?:account|business) to\s+"
               r"([A-Z][\w&+'’.\-]*(?:\s+[A-Z&+][\w&+'’.\-]*){0,3})"),
]


def _path() -> Path:
    return Path(str(state_dir())) / "agency_relationships.json"


def _load() -> dict:
    try:
        d = json.loads(_path().read_text())
        if isinstance(d, dict) and isinstance(d.get("companies"), dict):
            return d
    except FileNotFoundError:
        pass
    except Exception as e:
        log.info("agency-relationship ledger unreadable (%s) — starting fresh", e)
    return {"version": 1, "companies": {}}


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def _key(company: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (company or "").lower()).strip("_")
    return s or "unknown"


def _field(e, name: str):
    if isinstance(e, dict):
        return e.get(name)
    return getattr(e, name, None)


def _event_date(e) -> str:
    pub = _field(e, "published")
    if isinstance(pub, datetime):
        return pub.date().isoformat()
    s = str(pub or "")[:10]
    return s if len(s) == 10 else datetime.now(timezone.utc).date().isoformat()


def _known_agency(text: str) -> str:
    """The first curated agency / holding-company name in the text,
    longest-first so 'Publicis Groupe' beats 'Publicis'."""
    from tool.predictive.detector import _AGENCY_OBJECT_NAMES
    for nm in sorted(_AGENCY_OBJECT_NAMES, key=len, reverse=True):
        if re.search(r"\b" + re.escape(nm) + r"\b", text or "", re.IGNORECASE):
            return nm
    return ""


def _captured_agency(text: str) -> str:
    for rx in _CAPTURE_RXS:
        m = rx.search(text or "")
        if m:
            return m.group(1).strip(" .,;:'’\"-")
    return ""


def _discipline(text: str) -> str:
    for label, rx in _DISCIPLINES:
        if rx.search(text or ""):
            return label
    return ""


def record_moves(events) -> int:
    """Fold agency_account_move trigger events (TriggerEvent objects or
    dicts) into the per-company relationship history. Deduped on signal
    id; returns the number of new entries. Never raises."""
    try:
        moves = [e for e in (events or [])
                 if _field(e, "trigger_key") == "agency_account_move"]
        if not moves:
            return 0
        data = _load()
        companies = data["companies"]
        added = 0
        for e in moves:
            company = (_field(e, "company") or "").strip()
            if not company:
                continue
            evidence = (_field(e, "evidence") or "").strip()
            date = _event_date(e)
            agency = _known_agency(evidence) or _captured_agency(evidence)
            eid = (_field(e, "raw_signal_id")
                   or f"{date}|{agency}|{_field(e, 'url') or ''}")
            rec = companies.setdefault(_key(company),
                                       {"company": company, "history": []})
            rec["company"] = company
            if any(h.get("id") == eid for h in rec["history"]):
                continue
            rec["history"].append({
                "id": eid,
                "date": date,
                "agency": agency,
                "discipline": _discipline(evidence),
                "direction": "ended" if _ENDED_RX.search(evidence) else "appointed",
                "evidence": evidence[:300],
                "url": _field(e, "url") or "",
                "source": _field(e, "source_label") or _field(e, "source") or "",
            })
            rec["history"].sort(key=lambda h: h.get("date") or "")
            del rec["history"][:-HISTORY_CAP]
            added += 1
        # Prune companies whose newest entry is older than PRUNE_DAYS.
        today = datetime.now(timezone.utc).date()
        for k in list(companies):
            hist = companies[k].get("history") or []
            newest = max((h.get("date") or "" for h in hist), default="")
            try:
                age = (today - datetime.fromisoformat(newest).date()).days
            except ValueError:
                age = PRUNE_DAYS + 1
            if age > PRUNE_DAYS:
                companies.pop(k, None)
        _save(data)
        if added:
            log.info("agency-relationship ledger: %d move(s) recorded", added)
        return added
    except Exception as e:
        log.info("agency-relationship ledger update skipped (%s)", e)
        return 0


def history(company: str) -> list[dict]:
    """All recorded moves for a company, newest first."""
    try:
        rec = _load()["companies"].get(_key(company)) or {}
        return sorted(rec.get("history") or [],
                      key=lambda h: h.get("date") or "", reverse=True)
    except Exception:
        return []


def last_relationship(company: str) -> dict | None:
    """The most recent recorded move — the 'last known agency relationship'."""
    h = history(company)
    return h[0] if h else None


def summary_lines(company: str) -> list[str]:
    """Markdown lines for the dossier's Agency relationships section.
    Empty list when nothing is on file."""
    hist = history(company)
    if not hist:
        return []
    lines: list[str] = []
    last = hist[0]
    if last.get("agency"):
        verb = "ended" if last.get("direction") == "ended" else "appointed"
        disc = f" ({last['discipline']})" if last.get("discipline") else ""
        lines.append(f"Last known relationship: **{last['agency']}**{disc}, "
                     f"{verb} {last.get('date') or '?'}.")
        lines.append("")
    for h in hist[:6]:
        who = h.get("agency") or "unnamed agency"
        disc = f", {h['discipline']}" if h.get("discipline") else ""
        src = h.get("source") or ""
        url = h.get("url") or ""
        link = f" ([{src or 'source'}]({url}))" if url else (f" ({src})" if src else "")
        lines.append(f"- **{h.get('date') or '????-??-??'}** — {who} "
                     f"{h.get('direction') or 'appointed'}{disc}: "
                     f"{h.get('evidence') or '—'}{link}")
    return lines
