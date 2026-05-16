"""Contract-End / Re-Tender Window detector.

A flagship contract approaching its end date — or a major project nearing
hand-over — is a leading indicator of organisational change at the
employer it concerns. In the re-procurement window the incumbent reviews
its stakeholder / change / transition comms capacity (defend the
account, or manage a transition out); the contracting authority reviews
its procurement & transition comms. Both are placeable senior-comms
windows, and the recompete is visible in Find a Tender notices and
project-completion press *months* before any contract-loss RNS.

Boundary (deliberate — this is NOT a duplicate of the predictor's
CONTRACT_LOSS trigger):

  * predictive.patterns.CONTRACT_LOSS is REACTIVE — it fires once a
    material loss / termination / non-renewal has been *disclosed*.
  * This detector is PROACTIVE — it fires while the contract is still
    running, when its expiry / re-tender / hand-over window opens. That
    window is the lead time CONTRACT_LOSS cannot give Sara.

Precision by construction (per the strict detection-engine filter):
  * Every end/transition phrase below bakes in the contract-context
    noun (contract / framework / concession / tender / project /
    programme), so a bare "ends" can never fire.
  * The resolved company must be on Sara's watchlist. We resolve the
    whole signal text via account_match.resolve_account (text-first,
    fail-closed when company=None): unlike the directional vacated-seat
    case, a contract-end event legitimately concerns whichever watchlist
    party is named — incumbent OR buyer — so either is a valid lead.

No external calls. Runs over the RAW scoured signals (the Find a Tender
RSS, Investegate RNS, GDELT and trade press are already fetched every
run) — a reader on existing signals, not a new scraper.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable

log = logging.getLogger("brief.contract_end")

STATE_DIR = Path(__file__).resolve().parent / "state"

# Forward-looking contract-end / re-tender / hand-over phrasing. Each
# alternative is self-contained (contract-context noun + window verb)
# so it is high-precision against noisy procurement text. Deliberately
# does NOT cover already-disclosed loss/termination — that is
# CONTRACT_LOSS's job (see module docstring).
_CONTRACT = r"(?:contract|framework|concession|agreement|deal|tender|outsourc\w*|managed service|PFI|PF2|programme|project)"
_END_RX = re.compile(
    r"\b" + _CONTRACT + r"\b[^.]{0,40}?\b(?:expir\w+|due to (?:expire|end)|"
    r"comes? to an end|coming to an end|reaching the end|nearing the end|"
    r"set to end|runs? until|end date|expiry date|due for renewal)\b"
    r"|\b(?:expir\w+|due to expire|comes? to an end|nearing the end of|"
    r"end of (?:the )?(?:current )?)\b[^.]{0,30}?\b" + _CONTRACT + r"\b"
    r"|\b(?:re-?tender\w*|re-?procur\w+|re-?compet\w+|re-?bid\w*|"
    r"out to tender|going (?:back )?(?:out )?to tender|recompet\w+)\b"
    r"|\b(?:renewal|replacement) of (?:the |its |a )?[^.]{0,40}?\b" + _CONTRACT + r"\b"
    r"|\b" + _CONTRACT + r"\b[^.]{0,30}?\b(?:renewal|recompete|up for renewal|out for re-?tender)\b"
    r"|\btransition\w* to (?:a )?new (?:supplier|provider|contractor|operator)\b"
    r"|\bincumbent (?:supplier|provider|contractor|operator)\b"
    r"|\b(?:completes?|completed|conclud\w+|hands? over|hand-?over of|"
    r"delivers? the final|final phase of|mobilis\w+ (?:the )?new)\b"
    r"[^.]{0,40}?\b" + _CONTRACT + r"\b"
    r"|\b" + _CONTRACT + r"\b[^.]{0,30}?\b(?:awarded|award) to\b[^.]{0,50}?"
    r"\b(?:replac\w+|displac\w+|over the incumbent|previously held by|"
    r"taking over from)\b",
    re.IGNORECASE,
)

_WHO = ("CCO / Director of Corporate Affairs — change & transition "
        "comms capacity is reviewed in the recompete window; pitch "
        "interim/retained before the internal scramble.")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()[:70]


def _confidence(source: str) -> str:
    s = (source or "").lower()
    if any(k in s for k in ("find a tender", "find-tender", "rns",
                            "investegate", "gov.uk", "companies house")):
        return "high"
    return "medium"


def detect_contract_end(signals: Iterable[dict]) -> list[dict]:
    """Return contract-end / re-tender window records whose employer
    resolves to a watchlist account.

    Each record: {company, event, evidence, url, source, sector,
    confidence}.
    """
    from tool.account_match import resolve_account
    from tool.advisory import advisory_for
    try:
        from tool.peers import detect_sector
    except Exception:
        detect_sector = lambda _n: None  # noqa: E731

    out: list[dict] = []
    seen: set[tuple] = set()
    for s in signals:
        if not isinstance(s, dict):
            continue
        title = s.get("title") if isinstance(s.get("title"), str) else ""
        summary = s.get("summary") if isinstance(s.get("summary"), str) else ""
        text = (title + " . " + summary).strip(" .")
        if not text or not _END_RX.search(text):
            continue

        # Text-first watchlist resolution; company=None so it fails
        # CLOSED (returns None) rather than echoing a garbage string.
        company = resolve_account(None, text)
        if not company:
            continue

        key = (company.lower(), _norm(title or summary))
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "company":    company,
            "event":      "Contract-end / re-tender window",
            "evidence":   (title[:200] or summary[:200]),
            "url":        s.get("url", ""),
            "source":     s.get("source", ""),
            "sector":     detect_sector(company) or "",
            "advisory":   advisory_for("contract_end"),
            "confidence": _confidence(s.get("source", "")),
        })

    out.sort(key=lambda r: (r["confidence"] != "high", r["company"]))
    return out


def load_contract_end(limit: int = 30) -> list[dict]:
    """Dashboard accessor. Reads latest_contract_end.json. No external calls."""
    path = STATE_DIR / "latest_contract_end.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        log.info("latest_contract_end.json parse failed: %s", e)
        return []
    return data[:limit] if isinstance(data, list) else []
