"""Technographics — free martech fingerprinting of company websites.

The assessment flagged free technographics (Wappalyzer / BuiltWith) as an
unexploited lane: a company's adoption of a martech / marketing-automation
platform is half of a decision whose other half is a senior marketing-ops /
digital-marketing hire. The paid tools (Wappalyzer API, BuiltWith Pro) are
out of scope, but the underlying signal is FREE — it's just a public homepage
fetch + the same script-signature matching those tools do. This module
self-hosts that detection: it fetches each seeded company homepage, matches
well-known martech vendor fingerprints in the HTML, and emits a
`martech_adoption` event when a NEW vendor appears versus the last snapshot
(a fresh adoption is the signal; a long-standing one is not).

Complements the news-based martech trigger (sources fire the same trigger
key, so they stack / dedup naturally). Snapshot-based + non-fatal per
company; seeded COMPANY_SITES extend freely. The detector is a pure function,
unit-tested against fixture HTML.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from tool.predictive import patterns as P
from tool.predictive.detector import TriggerEvent
from tool.sources._http import get, signal_id

log = logging.getLogger("brief.techno")

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOT_FILE = STATE_DIR / "technographics_snapshot.json"

# Vendor -> list of lower-cased HTML/script signatures. Standard Wappalyzer-
# style fingerprints (script src hosts, SDK markers). Conservative: each is
# distinctive enough that a match means the tag is genuinely embedded.
MARTECH_FINGERPRINTS: dict[str, list[str]] = {
    "HubSpot": ["js.hs-scripts.com", "js.hsforms.net", "hs-analytics.net",
                "js.hubspot.com"],
    "Marketo": ["munchkin.js", "mktoresp.com", ".marketo.com/js"],
    "Salesforce Marketing Cloud": ["pi.pardot.com", "cdn.pardot.com",
                                   "pardot.com/pd.js", "exct.net"],
    "Adobe Experience": ["assets.adobedtm.com", "demdex.net", "omtrdc.net",
                         "adobedc.net"],
    "Segment": ["cdn.segment.com/analytics.js", "cdn.segment.io"],
    "Tealium": ["tags.tiqcdn.com", "tealiumiq.com"],
    "Braze": ["appboycdn.com", "sdk.iad-", "js.appboycdn.com", ".braze.com/api"],
    "Optimizely": ["cdn.optimizely.com"],
    "Klaviyo": ["static.klaviyo.com", "klaviyo.js"],
    "Bloomreach": ["bloomreach.com", "exponea.com"],
    "Iterable": ["links.iterable.com", "iterable.com/api"],
    "Emarsys": ["scarabresearch.com", "emarsys.net"],
}

# Seeded company -> homepage URL. Watchlist members (so no account gate
# needed — the emitted company is pre-vetted). Extend freely. A page that
# 404s / blocks is skipped (non-fatal).
COMPANY_SITES = {
    "Sainsbury's": "https://www.sainsburys.co.uk/",
    "Tesco": "https://www.tesco.com/",
    "Boots": "https://www.boots.com/",
    "Currys": "https://www.currys.co.uk/",
    "ASOS": "https://www.asos.com/",
    "Ocado": "https://www.ocado.com/",
    "JD Sports": "https://www.jdsports.co.uk/",
    "Aviva": "https://www.aviva.co.uk/",
}


def detect_vendors(html: str) -> set[str]:
    """Return the set of martech vendors fingerprinted in the page HTML. Pure
    function — the unit of truth for the detector, tested without network."""
    if not html:
        return set()
    low = html.lower()
    found = set()
    for vendor, sigs in MARTECH_FINGERPRINTS.items():
        if any(sig.lower() in low for sig in sigs):
            found.add(vendor)
    return found


def _load_snapshot() -> dict:
    if not SNAPSHOT_FILE.exists():
        return {}
    try:
        return json.loads(SNAPSHOT_FILE.read_text())
    except Exception:
        return {}


def _save_snapshot(d: dict) -> None:
    try:
        SNAPSHOT_FILE.write_text(json.dumps(d, indent=0))
    except Exception as e:
        log.info("technographics: could not persist snapshot: %s", e)


def _fetch(url: str) -> str:
    r = get(url, tries=1)
    if not r or getattr(r, "status_code", 0) != 200 or not r.text:
        return ""
    return r.text


def detect_technographics(sites: dict | None = None) -> list[TriggerEvent]:
    """Fingerprint each seeded homepage and emit a martech_adoption event for
    every NEW vendor since the last snapshot. First sight of a company seeds
    its snapshot (no events), exactly like the CH / charity scans."""
    sites = sites if sites is not None else COMPANY_SITES
    snapshot = _load_snapshot()
    new_snapshot = dict(snapshot)
    events: list[TriggerEvent] = []
    trig = P.BY_KEY.get("martech_adoption")
    now = datetime.now(timezone.utc)

    for company, url in sites.items():
        html = _fetch(url)
        if not html:
            continue  # unreachable / blocked — leave prior snapshot intact
        vendors = detect_vendors(html)
        prior = set((snapshot.get(company) or {}).get("vendors") or [])
        new_snapshot[company] = {"vendors": sorted(vendors), "at": now.isoformat()}
        if company not in snapshot or trig is None:
            continue  # first sight — seed only, don't fire on pre-existing tags
        newly = vendors - prior
        for vendor in sorted(newly):
            events.append(TriggerEvent(
                trigger_key="martech_adoption",
                trigger_label=f"Martech adoption detected ({vendor})",
                company=company,
                evidence=(f"Technographics: {company}'s website now embeds {vendor} "
                          f"(not present at the last scan) — a fresh martech adoption "
                          f"that typically pairs with a senior marketing-ops hire."),
                url=url,
                source_label="Technographics (site fingerprint)",
                published=now,
                raw_signal_id=signal_id("techno", f"{company}|{vendor}"),
                tier_hint="covered",
            ))

    _save_snapshot(new_snapshot)
    log.info("technographics: %d sites scanned, %d new-adoption events",
             len(sites), len(events))
    return events
