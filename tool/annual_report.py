"""Annual-report quote extraction for the Pitch Pack.

Replaces Section 2 ("Why this matters now") with 3–5 quoted passages
from the target company's most recent annual report — specifically
strategic-priority language from the CEO statement / strategic report.

Falls back to None if extraction fails, in which case pitch_pack reverts
to GDELT headlines under a re-labelled section ("Recent market context")
so Sara can see at a glance whether the pack contains bespoke strategic
context or generic news.

Source: Companies House Document API. Works cleanly for UK-listed and
medium-sized UK private companies (statutory strategic report required
at £36m+ turnover). Degrades to None for abbreviated / micro-entity
filings, non-UK firms, and scanned-image PDFs.

Implementation notes:
  - PDF parsing: pypdf (pure Python, no native deps)
  - Caps text extraction at first 80 pages (CEO statement and strategic
    report are always at the front of UK annual reports)
  - Sentence scoring favours strategic-priority keywords + penalises
    generic boilerplate phrasing ("we believe", "going forward", etc.)
  - Returns multiple candidate quotes — Sara picks the best one when
    she edits the pack before sending
"""
from __future__ import annotations
import io
import logging
import re
from dataclasses import dataclass
from typing import Iterable

import requests

from tool.config import COMPANIES_HOUSE_KEY, SOURCES
from tool.sources._http import get

log = logging.getLogger("pitch_pack.annual_report")


@dataclass
class Quote:
    text: str               # The actual quoted sentence
    heading: str            # Section heading where it was found
    page: int               # 1-based page number in the report
    score: int              # Higher = more strategic-priority signal


@dataclass
class AnnualReport:
    company_number: str
    filing_date: str        # ISO date string from CH filing-history
    quotes: list[Quote]
    page_count: int


# ---- Section heading detection -----------------------------------------
# UK annual reports follow conventional structure: CEO / Chair statement
# in the first ~40 pages, then strategic report, then governance, then
# financials. We restrict quote extraction to the first two sections.
_SECTION_RX = re.compile(
    r"(?:^|\n)\s*("
    r"chief executive(?:'s)?\s+(?:review|statement|report|letter)|"
    r"ceo(?:'s)?\s+(?:review|statement|letter)|"
    r"chief executive officer(?:'s)?\s+(?:review|statement|letter)|"
    r"chairman(?:'s)?\s+(?:review|statement|letter)|"
    r"chair(?:'s)?\s+(?:review|statement|letter)|"
    r"strategic report|"
    r"our strategy|"
    r"strategic priorities|"
    r"strategic objectives"
    r")",
    re.IGNORECASE,
)


# ---- Strategic-priority keyword scorer ---------------------------------
_KEYWORDS = re.compile(
    r"\b(priorit|strategic|focus|transform|rebuild|challenge|ambition|"
    r"vision|growth|invest|capability|culture|trust|reputat|stakeholder|"
    r"deliver|drive|accelerat|pivot|reposition|innovat)\w*\b",
    re.IGNORECASE,
)

_BOILERPLATE = re.compile(
    r"\b(?:we believe|we will continue|we remain (?:committed|focused)|"
    r"going forward|in conclusion|i would like to thank|as i mentioned|"
    r"i am pleased to|i am delighted to)\b",
    re.IGNORECASE,
)


def _score_sentence(s: str) -> int:
    """Rank candidates by strategic-priority density minus boilerplate."""
    keyword_hits = len(_KEYWORDS.findall(s))
    boilerplate_hits = len(_BOILERPLATE.findall(s))
    score = keyword_hits * 10 - boilerplate_hits * 8
    # Prefer first-person plural ("our" / "we") since those are forward-
    # looking; CEO statements use them; financial-detail sentences don't.
    if re.search(r"\b(?:our|we|us)\b", s, re.IGNORECASE):
        score += 2
    return score


def candidate_quotes(text: str, heading: str, page: int,
                     min_len: int = 80, max_len: int = 320) -> list[Quote]:
    """Extract scored candidate sentences from a passage."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: list[Quote] = []
    for raw in sentences:
        s = re.sub(r"\s+", " ", raw).strip()
        # Drop fragments and run-ons
        if not (min_len <= len(s) <= max_len):
            continue
        # Must contain at least one strategic keyword
        if not _KEYWORDS.search(s):
            continue
        # Drop sentences that look like figures / table fragments
        if re.search(r"(?:\d{1,3}[,.]\d{3}|\£\d+m|\$\d+m|table|note \d+)",
                     s, re.IGNORECASE):
            continue
        score = _score_sentence(s)
        if score <= 0:
            continue
        out.append(Quote(text=s, heading=heading, page=page, score=score))
    return out


# ---- PDF text extraction -----------------------------------------------
def extract_pages(pdf_bytes: bytes, max_pages: int = 80) -> list[tuple[int, str]]:
    """Returns [(1-based page number, page text)]. Caps at max_pages.
    Tries pypdf first; falls back to pdfplumber for tricky PDFs (e.g.
    linearised / iXBRL-wrapped filings where pypdf's recovery mode loses
    the embedded text)."""
    # First-try: pypdf (lightweight)
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages = []
        for i, page in enumerate(reader.pages[:max_pages]):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if text.strip():
                pages.append((i + 1, text))
        if pages:
            return pages
        log.info("pypdf returned 0 pages of text — trying pdfplumber fallback")
    except ImportError:
        log.warning("pypdf not installed; annual_report extraction disabled")
        return []
    except Exception as e:
        log.info("pypdf failed (%s); trying pdfplumber fallback", e)

    # Fallback: pdfplumber (heavier but more tolerant of optimised PDFs)
    try:
        import pdfplumber
    except ImportError:
        log.info("pdfplumber not installed — cannot fall back; aborting")
        return []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = []
            for i, page in enumerate(pdf.pages[:max_pages]):
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                if text.strip():
                    pages.append((i + 1, text))
            return pages
    except Exception as e:
        log.info("pdfplumber also failed: %s", e)
        return []


def find_quotes_in_pdf(pdf_bytes: bytes, top_n: int = 5) -> tuple[list[Quote], int]:
    """Main extraction logic: identify CEO-statement / strategic-report
    sections in the PDF, score candidate sentences, return top N.
    Returns (quotes, total_page_count_inspected)."""
    pages = extract_pages(pdf_bytes)
    if not pages:
        return [], 0

    # Locate each relevant section's starting page + heading text
    sections: list[tuple[int, str]] = []   # (page_num, heading_text)
    for page_num, text in pages:
        for m in _SECTION_RX.finditer(text):
            heading = m.group(1).strip()
            sections.append((page_num, heading.title()))

    if not sections:
        # No CEO/strategic-report headings found — try the first ~20 pages
        # anyway since some companies don't label sections explicitly
        sections = [(p, "Front section") for p, _ in pages[:5]]

    # For each detected section, take that page + next 2 pages of context
    seen_pages: set[int] = set()
    all_quotes: list[Quote] = []
    for start_page, heading in sections:
        for offset in range(3):
            target_page = start_page + offset
            if target_page in seen_pages:
                continue
            seen_pages.add(target_page)
            page_text = next((t for p, t in pages if p == target_page), "")
            if not page_text:
                continue
            all_quotes.extend(candidate_quotes(page_text, heading, target_page))

    # Deduplicate near-identical sentences (same first 60 chars)
    deduped: dict[str, Quote] = {}
    for q in all_quotes:
        key = q.text[:60].lower()
        if key not in deduped or q.score > deduped[key].score:
            deduped[key] = q

    ranked = sorted(deduped.values(), key=lambda q: -q.score)[:top_n]
    return ranked, len(pages)


# ---- Companies House Document API integration -------------------------
def _fetch_filings_accounts(company_number: str) -> list[dict]:
    """Return the most-recent accounts filings (newest first)."""
    if not COMPANIES_HOUSE_KEY or not company_number:
        return []
    url = f"{SOURCES['companies_house_api']}/company/{company_number}/filing-history"
    r = get(url, params={"category": "accounts", "items_per_page": 20},
            auth=(COMPANIES_HOUSE_KEY, ""))
    if not r or r.status_code != 200:
        return []
    return r.json().get("items", []) or []


def _is_full_annual_report(filing: dict) -> bool:
    """Skip abbreviated / dormant / micro-entity filings — they don't
    contain CEO statements or strategic reports we can quote from."""
    description = (filing.get("description") or "").lower()
    for skip in ("abbreviated", "dormant", "micro-entity", "micro entity",
                 "audit exemption"):
        if skip in description:
            return False
    return True


def _download_pdf(document_metadata_url: str) -> bytes | None:
    """Fetch the PDF content for a filing. CH Document API redirects to
    an AWS S3 URL — we follow redirects but strip auth from the S3 leg
    (Basic Auth in an S3 signed-URL request can break the signature)."""
    if not COMPANIES_HOUSE_KEY or not document_metadata_url:
        return None
    content_url = document_metadata_url.rstrip("/") + "/content"
    try:
        # Don't auto-follow redirects so we can re-issue the second leg
        # without Basic Auth (S3 URLs are pre-signed and don't need it).
        r = requests.get(
            content_url,
            auth=(COMPANIES_HOUSE_KEY, ""),
            headers={"Accept": "application/pdf"},
            timeout=45,
            allow_redirects=False,
        )
        if r.status_code in (301, 302, 303, 307, 308):
            redirect_url = r.headers.get("Location", "")
            if redirect_url:
                r = requests.get(redirect_url, timeout=45)
    except requests.RequestException as e:
        log.info("annual_report PDF fetch failed: %s", e)
        return None
    if r.status_code != 200 or not r.content:
        log.info("annual_report PDF fetch HTTP %s", r.status_code)
        return None
    # Diagnostic: how big is the PDF and does it look like one?
    head = r.content[:8]
    log.info("annual_report PDF: %d bytes, magic=%r",
             len(r.content), head)
    return r.content


def fetch_strategic_quotes(company_number: str,
                           top_n: int = 5,
                           max_filings_to_try: int = 3) -> AnnualReport | None:
    """End-to-end: find the latest full annual report at CH, download
    the PDF, extract top N strategic-priority quotes.
    Returns None if any step fails (caller should fall back to GDELT).

    Caps at max_filings_to_try (default 3) to avoid wasting time on
    successive failed PDF parses — if the 3 most recent filings can't
    be extracted, older ones almost certainly can't be either, and the
    pitch pack should fall back to GDELT quickly instead of waiting
    a minute per call."""
    if not company_number:
        return None
    filings = _fetch_filings_accounts(company_number)
    if not filings:
        log.info("annual_report: no accounts filings for %s", company_number)
        return None
    eligible = [f for f in filings if _is_full_annual_report(f)][:max_filings_to_try]
    log.info("annual_report: trying up to %d of %d eligible filings",
             len(eligible), len([f for f in filings if _is_full_annual_report(f)]))
    for filing in eligible:
        links = filing.get("links") or {}
        metadata_url = links.get("document_metadata")
        if not metadata_url:
            continue
        log.info("annual_report: trying filing %s (%s)",
                 filing.get("date"), filing.get("description", "")[:60])
        pdf_bytes = _download_pdf(metadata_url)
        if not pdf_bytes:
            continue
        quotes, pages = find_quotes_in_pdf(pdf_bytes, top_n=top_n)
        if not quotes:
            log.info("annual_report: PDF parsed (%d pages) but no quotes scored above threshold",
                     pages)
            continue
        log.info("annual_report: extracted %d quotes from %s annual report (%d pages)",
                 len(quotes), filing.get("date"), pages)
        return AnnualReport(
            company_number=company_number,
            filing_date=filing.get("date", ""),
            quotes=quotes,
            page_count=pages,
        )
    log.info("annual_report: tried %d filings without success — falling back to GDELT",
             len(eligible))
    return None
