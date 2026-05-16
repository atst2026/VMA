"""Funding-Round detector — the pre-hire window at scaling private firms.

When a private company closes a material growth round (Series B/C+,
growth equity, £20m+), it predictably professionalises communications
~6 months later: a first or step-change senior in-house comms hire
(Head of Comms / Comms Director / VP Comms / Head of Public Affairs).
Most recruiters react to the job ad; the round itself is public months
before it.

Boundary (NOT a duplicate of predictive.patterns.IPO_LISTING):
IPO_LISTING covers PUBLIC-market admission. This covers PRIVATE VC /
growth-equity rounds — a different population (scale-ups, not listed
issuers) and a different, longer comms-hire lead time.

Precision by construction (per the strict detection-engine filter):
funded scale-ups are by definition mostly NOT on Sara's established
watchlist, so gating on account_match would be all false-negatives.
The precision gate here is instead, ALL required together:

  1. a funding-round phrase (Series A-E / growth round / investment
     round / "raises … funding" / "led by" an investor),
  2. an explicit amount that parses to >= MIN_GBP_M (£20m; $/€ accepted
     at rough numeric parity but flagged medium-confidence),
  3. NOT a debt/bond/loan facility (equity growth rounds predict the
     comms hire; refinancing does not), and
  4. a named subject company captured from the headline (the lead).

No external calls. Runs over the RAW scoured signals (GDELT news graph
+ trade press already fetched every run) — a reader, not a new scraper.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable

log = logging.getLogger("brief.funding")

STATE_DIR = Path(__file__).resolve().parent / "state"

# £20m+ is the plan's threshold: below it a dedicated senior in-house
# comms hire is statistically unlikely (agency/contractor instead).
MIN_GBP_M = 20.0

_CO = r"([A-Z][\w&.\-' ]{1,45}?)"

# Funding-round context — at least one must be present alongside the
# amount, so "raises £30m contract / order / loan" cannot fire.
_FUND_CTX_RX = re.compile(
    r"\bseries [a-e]\b|\bseed (?:round|extension|funding)\b"
    r"|\bgrowth (?:round|equity|capital)\b|\blate[- ]stage\b"
    r"|\bpre-?ipo round\b|\bventure round\b|\bfunding round\b"
    r"|\binvestment round\b|\bfundrais\w+\b|\bcapital raise\b"
    r"|\bequity (?:round|raise|investment|financing)\b"
    r"|\braises?\b[^.]{0,40}\b(?:funding|investment|capital|equity)\b"
    r"|\b(?:led by|backed by|co-?led by)\b[^.]{0,40}\b(?:ventures?|capital|partners|equity|fund)\b",
    re.IGNORECASE,
)

# Debt / refinancing — explicitly excluded (different signal class).
_DEBT_RX = re.compile(
    r"\b(?:debt facility|loan facility|term loan|credit facility|"
    r"bond (?:issue|offering)|refinanc\w+|revolving credit|"
    r"venture debt)\b",
    re.IGNORECASE,
)

# Amount: currency symbol/code + number + magnitude. Captures
# (currency, number, magnitude).
_AMOUNT_RX = re.compile(
    r"(£|\$|€|gbp|usd|eur)\s?(\d{1,4}(?:[.,]\d{1,3})?)\s?"
    r"(bn|b|billion|m|mn|million)\b",
    re.IGNORECASE,
)

# Round label for display, if a specific one is stated.
_ROUND_LABEL_RX = re.compile(
    r"\bseries\s([a-e])\b|\b(seed)\b|\b(growth)\s(?:round|equity|capital)"
    r"|\b(pre-?ipo)\b|\b(late[- ]stage)\b",
    re.IGNORECASE,
)

# Subject-company capture. Two reliable shapes (cf. tool.following).
_SUBJECT_RX = [
    re.compile(p, re.IGNORECASE) for p in (
        r"^(?:UK |British |London-based |scale-?up |startup |fintech |the )*"
        + _CO + r"\s+(?:has |today |just )?"
        r"(?:raises?|raised|secures?|secured|closes?|closed|completes?|"
        r"completed|lands?|landed|banks?|banked|nets?|netted|bags?|bagged)\b",
        r"\b(?:investment in|backs?|funding for|round for)\s+" + _CO
        + r"(?=[,.;:)]|\s+(?:to\b|as\b|after\b|in\b|—|–)|$)",
        # "<funding context> … for <Co>" — e.g. "$50m Series B for Acme".
        # The "for" must follow a funding token so it stays precise.
        r"\b(?:series [a-e]|seed|growth round|growth equity|funding round|"
        r"investment round|venture round|raise|backing|round)\b[^.]{0,25}?"
        r"\bfor\s+" + _CO
        + r"(?=[,.;:)]|\s+(?:to\b|as\b|after\b|led by|in\b|—|–)|$)",
    )
]

_LEAD_DESCRIPTORS = {
    "uk", "british", "london-based", "london", "scale-up", "scaleup",
    "startup", "start-up", "fintech", "healthtech", "biotech", "the",
}


def _to_gbp_m(currency: str, num: str, mag: str) -> float | None:
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return None
    m = mag.lower()
    if m in ("bn", "b", "billion"):
        v *= 1000.0
    # $/€ accepted at rough numeric parity (flagged medium downstream).
    return v


def _is_gbp(currency: str) -> bool:
    return currency.lower() in ("£", "gbp")


def _clean_company(span: str) -> str:
    span = (span or "").strip(" .,'-\"")
    words = span.split()
    while words and words[0].lower() in _LEAD_DESCRIPTORS:
        words.pop(0)
    return " ".join(words).strip(" .,'-\"")


def _round_label(text: str) -> str:
    m = _ROUND_LABEL_RX.search(text)
    if not m:
        return "funding round"
    if m.group(1):
        return f"Series {m.group(1).upper()}"
    if m.group(2):
        return "Seed round"
    if m.group(3):
        return "Growth round"
    if m.group(4):
        return "Pre-IPO round"
    if m.group(5):
        return "Late-stage round"
    return "funding round"


def detect_funding(signals: Iterable[dict]) -> list[dict]:
    """Return funding-round records (>= MIN_GBP_M) with a named subject
    company. Each: {company, round, amount, evidence, url, source,
    sector, window, confidence}.
    """
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
        if not text:
            continue
        if not _FUND_CTX_RX.search(text) or _DEBT_RX.search(text):
            continue

        am = _AMOUNT_RX.search(text)
        if not am:
            continue
        gbp_m = _to_gbp_m(am.group(1), am.group(2), am.group(3))
        if gbp_m is None or gbp_m < MIN_GBP_M:
            continue
        is_gbp = _is_gbp(am.group(1))

        company = None
        for rx in _SUBJECT_RX:
            m = rx.search(title) or rx.search(text)
            if m:
                company = _clean_company(m.group(1))
                if company and len(company) >= 2:
                    break
                company = None
        if not company:
            fallback = (s.get("company") or "").strip()
            company = fallback if len(fallback) >= 2 else None
        if not company:
            continue

        rnd = _round_label(text)
        key = (company.lower(), rnd.lower())
        if key in seen:
            continue
        seen.add(key)

        cur = am.group(1)
        cur_sym = "£" if is_gbp else ("$" if cur in ("$",) or cur.lower() == "usd"
                                      else "€" if cur in ("€",) or cur.lower() == "eur"
                                      else cur)
        mag = am.group(3).lower()
        mag_disp = "bn" if mag in ("bn", "b", "billion") else "m"
        amount_disp = f"{cur_sym}{am.group(2)}{mag_disp}"

        out.append({
            "company":    company,
            "round":      rnd,
            "amount":     amount_disp,
            "_gbp_m":     gbp_m,
            "evidence":   (title[:200] or summary[:200]),
            "url":        s.get("url", ""),
            "source":     s.get("source", ""),
            "sector":     detect_sector(company) or "",
            "window":     "senior-comms hire window: now → ~6 months",
            "advisory":   advisory_for("funding"),
            "confidence": "high" if (is_gbp and gbp_m >= MIN_GBP_M) else "medium",
        })

    # High-confidence first, then biggest raise (larger round = larger
    # comms build-out), then company.
    out.sort(key=lambda r: (r["confidence"] != "high",
                            -float(r.get("_gbp_m") or 0), r["company"]))
    for r in out:
        r.pop("_gbp_m", None)
    return out


def load_funding(limit: int = 30) -> list[dict]:
    """Dashboard accessor. Reads latest_funding.json. No external calls."""
    path = STATE_DIR / "latest_funding.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        log.info("latest_funding.json parse failed: %s", e)
        return []
    return data[:limit] if isinstance(data, list) else []
