"""Scan fetched signals for predictive trigger events.

A `TriggerEvent` represents one publicly-attested event at one company
that empirically precedes a senior comms hire. Multiple events at the
same company within the same 30-day window get combined into a stack
downstream (see stacker.py).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from dateutil import parser as dateparse

from tool.predictive import patterns as P


@dataclass
class TriggerEvent:
    trigger_key: str                        # patterns.BY_KEY id (e.g. "ceo_change")
    trigger_label: str                      # human label
    company: str                            # best-effort company name
    evidence: str                           # 1-line extract from the source
    url: str                                # source URL
    source_label: str                       # "LSE RNS (Investegate)" etc
    published: datetime                     # when the event was published
    raw_signal_id: str = ""                 # provenance
    tier_hint: str = "listed"               # "listed" | "covered" | "other"
    account_tier: str = "watchlist"         # "watchlist" | "off_watchlist"


# ---- Company extraction from signal titles -----------------------------
# RNS titles often look like:
#   "NatWest Group plc — Directorate Change"
#   "Barclays PLC - Appointment of Chief Executive"
#   "NWG.L NatWest Group - Directorate Change"
#   "XYZ Limited: Board Changes"
# GDELT and trade-press titles are less structured.

# Splits on whitespace-flanked separators. Whitespace-flanked is critical:
# without it, "same-store sales" splits at the hyphen, corrupting company
# extraction from titles like "Domino's Pizza Group reports same-store sales".
_RNS_SEPARATORS = re.compile(r"(?:\s+[-—–:|]\s*)|(?:\s*[—–:|]\s+)", re.UNICODE)
_LSE_TICKER = re.compile(r"^[A-Z0-9]{2,6}\.[A-Z]\s+", re.IGNORECASE)
_CO_SUFFIX_RX = re.compile(
    # UK + international corporate suffixes. Captures plc/limited/ltd/group/
    # holdings/inc/incorporated for UK + Inc/Corp/LLC for US + AG/SA/NV/
    # GmbH/BV/SpA/OY for EU. With the wider list we accept legitimate
    # international news headlines like 'Apollo Funds Inc' or 'Roche AG'.
    r"\b(plc|p\.l\.c\.|plc\.|limited|ltd|ltd\.|group|holdings|llp|"
    r"inc|inc\.|incorporated|corp|corp\.|corporation|llc|"
    r"ag|s\.a\.|sa|n\.v\.|nv|gmbh|b\.v\.|bv|spa|oy|oyj)\b",
    re.IGNORECASE,
)


def extract_company(title: str, summary: str = "") -> str:
    """Best-effort UK company name extraction.

    Strict-UK-only: returns a name ONLY if it's a known UK peer
    (from peers.SECTOR_PEERS) or has an explicit UK company suffix
    in an RNS-style title (which has a separator).

    Order:
      1) RNS-style: separator-split + suffix-at-end check. Requires
         either a separator (—, :, -, |) OR a very short candidate
         (≤3 words). Without these, 'Apollo Funds acquires Prosol
         Group' would pass because 'Group' is in the title — we now
         require either a clean RNS-style prefix or a peer match.
      2) Peer-name scan with word boundaries. Tries the full peer
         name first, then the stem (peer minus trailing 'Group/plc/
         Limited/etc.') so 'Intertek' matches peer 'Intertek Group'.
      3) Return empty (event drops with 'no company').
    """
    if not title:
        return ""
    t = title.strip()
    # A colon-attached source attribution ("Companies House: X filed…")
    # is never the subject — strip it before the separator split, or the
    # registry name wins the RNS-style extraction.
    from tool.account_match import _SOURCE_LABEL_RX
    t = _SOURCE_LABEL_RX.sub(" ", t).strip()
    t_nopfx = _LSE_TICKER.sub("", t).strip()
    parts = _RNS_SEPARATORS.split(t_nopfx, maxsplit=1)
    had_separator = len(parts) > 1
    candidate = parts[0].strip()

    # 1) RNS-style: short candidate ending in UK company suffix.
    # Without a separator we require ≤3 words to avoid matching headlines
    # like 'Apollo Funds acquires Prosol Group' as a whole company name.
    words = candidate.split()
    last3 = " ".join(words[-3:]) if len(words) >= 1 else ""
    if 1 <= len(words) <= 6 and _CO_SUFFIX_RX.search(last3):
        if had_separator or len(words) <= 3:
            return candidate.rstrip(",.;:")

    # 2) Peer-name scan with word boundaries; try full name + stem.
    haystack = f" {t} {summary} ".lower()   # t: attribution prefix stripped
    try:
        from tool.peers import SECTOR_PEERS
        all_peers = [p for names in SECTOR_PEERS.values() for p in names]
        _PEER_SUFFIX_RX = re.compile(
            r"\s+(group|plc|limited|ltd|holdings|llp|uk)$", re.IGNORECASE,
        )
        for peer in sorted(all_peers, key=len, reverse=True):
            peer_lc = peer.lower()
            # Try full name first
            if re.search(r"\b" + re.escape(peer_lc) + r"\b", haystack):
                return peer
            # Then try the stem (peer minus trailing suffix word)
            stem = _PEER_SUFFIX_RX.sub("", peer_lc).strip()
            if stem and stem != peer_lc and len(stem) >= 4:
                # Only check stem if it's distinctive enough (>=4 chars)
                # to avoid matching common words.
                if re.search(r"\b" + re.escape(stem) + r"\b", haystack):
                    return peer   # return canonical peer name
    except Exception:
        pass

    return ""


# ---- Named-employer extraction (off-watchlist public/charity/HE/housing) -
# extract_company() only resolves RNS-suffix names (… plc / Ltd) or curated
# peers — so the highest-value LOW-COMPETITION segment (housing
# associations, universities, councils, NHS trusts) is invisible: those orgs
# carry no corporate suffix and aren't on the watchlist, so their names are
# never even pulled from a news headline, and the event drops at the
# no-company stage before the account gate ever sees it. This extractor
# pulls those names, anchored on unambiguous UK org-type tails (each
# preceded by >=1 capitalised proper-noun word) plus the "University of X"
# form. High-precision by construction, and only ever a CANDIDATE — it is
# always re-validated by account_match.classify_account, so a rare
# over-capture is dropped downstream, never surfaced.
_NE_LEAD = r"(?:[A-Z][A-Za-z0-9&'’.\-]+\.?\s+){1,4}"
_NE_SUFFIX_RX = re.compile(
    r"\b(" + _NE_LEAD +
    r"(?:NHS Foundation Trust|NHS Trust|"
    r"(?:Borough|County|City|District|Metropolitan|Parish) Council|"
    r"Housing Association|Housing Group|"
    r"Multi[- ]Academy Trust|Academy Trust|"
    r"University|College))\b"
)
_NE_PREFIX_RX = re.compile(
    r"\b(University of [A-Z][A-Za-z'’.\-]+(?:\s+[A-Z][A-Za-z'’.\-]+){0,2})\b"
)
# Leading determiners / pronouns / origin markers to strip off a captured
# span ("The Riverside Housing Association" -> "Riverside Housing
# Association", "former Tesco …" never reaches here as a lead anyway).
_NE_LEAD_STOP = {
    "the", "a", "an", "its", "his", "her", "their", "our", "new",
    "former", "ex", "incoming", "outgoing", "at", "of", "for", "and",
}


def extract_named_employer(title: str, summary: str = "") -> str:
    """Best-effort extraction of a UK public-body / charity / HE / housing
    employer name from a news headline. Returns "" if none found. Prefers
    the 'University of X' form, then the suffix-anchored form. The result
    is a CANDIDATE only — callers re-validate via classify_account."""
    text = f"{title} . {summary}"
    best = ""
    for rx in (_NE_PREFIX_RX, _NE_SUFFIX_RX):
        m = rx.search(text)
        if not m:
            continue
        span = (m.group(1) or "").strip(" .,'’\"-")
        words = span.split()
        while words and words[0].lower() in _NE_LEAD_STOP:
            words.pop(0)
        span = " ".join(words)
        # Require >=2 tokens so a bare tail ('Council', 'University') can
        # never qualify on its own.
        if len(words) >= 2 and len(span) >= 6 and len(span) > len(best):
            best = span
    return best


# Agency / holding-company names that appear as the OBJECT of an
# agency-account-move headline ("Brand appoints Ogilvy", "WPP wins the X
# account"). Several are watchlist members in their own right, so — exactly
# like a regulator in a probe headline — they must not be read as the
# SUBJECT of the move. Masked from the company-resolution text so the brand
# (the actual lead) resolves instead. A real "WPP appoints a CCO" story
# carries no agency-account-move trigger, so it is unaffected.
_AGENCY_OBJECT_NAMES = [
    "WPP", "Publicis", "Publicis Groupe", "Omnicom", "Interpublic", "IPG",
    "Dentsu", "Havas", "Accenture Song", "Ogilvy", "BBH", "AMV BBDO",
    "Wunderman Thompson", "VCCP", "Saatchi & Saatchi", "McCann", "Edelman",
    "Weber Shandwick", "FleishmanHillard", "Brunswick", "FGS Global", "Teneo",
    "Hill+Knowlton", "Golin", "MSL", "Mother", "Droga5", "adam&eveDDB",
    "Leo Burnett", "Grey", "TBWA", "DDB", "BBDO", "Havas Lynx",
]


def _mask_object_names(text: str, names: list[str]) -> str:
    """Blank out object names (martech vendors / agencies) from text used for
    SUBJECT resolution, longest-first so multi-word names go before their
    substrings. Word-boundary, case-insensitive. Leaves the rest intact so
    the genuine subject (the adopter / the brand) still resolves."""
    if not text:
        return text
    out = text
    for nm in sorted(names, key=len, reverse=True):
        out = re.sub(r"\b" + re.escape(nm) + r"\b", " ", out, flags=re.IGNORECASE)
    return out


def _tier_from_source_label(label: str) -> str:
    low = (label or "").lower()
    if "rns" in low or "investegate" in low:
        return "listed"
    if any(k in low for k in ("gdelt", "prweek", "campaign", "corpcomms",
                              "provoke", "hr magazine", "people management",
                              "fca", "ofwat", "ofgem", "ofcom", "ico", "cma")):
        return "covered"
    return "other"


def _parse_date(s: str) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        d = dateparse.parse(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return datetime.now(timezone.utc)


def detect_events(signals: Iterable[dict]) -> list[TriggerEvent]:
    """Scan raw fetched signals for trigger patterns. Each signal can emit
    multiple events (e.g. an article mentioning both CEO change AND
    restructure produces two events on the same company).

    Emits debug logs for rejected items so we can see WHY nothing fired
    when a morning brief's predictive section is empty.
    """
    import logging
    log = logging.getLogger("brief.predictive.detect")

    events: list[TriggerEvent] = []
    rejected_no_company = 0
    rejected_off_universe = 0
    admitted_off_watchlist = 0
    rejected_subthreshold_regulator = 0
    rejected_contract_loss_immaterial = 0
    pattern_hits = 0

    from tool.account_match import classify_account

    for s in signals:
        title = s.get("title") or ""
        summary = s.get("summary") or ""
        body = f"{title} . {summary}"
        hits = P.match_triggers(body)
        if not hits:
            continue
        pattern_hits += 1
        # For martech-adoption / agency-account-move hits, the named vendor /
        # agency is the OBJECT, not the subject — and several (Adobe,
        # Salesforce, WPP, Publicis…) are watchlist members, so they would
        # mis-resolve as the lead. Mask them from the resolution text so the
        # adopter / brand resolves instead (or the event drops as off-universe
        # if no watchlist subject remains). Evidence still uses the original
        # body, so the vendor/agency stays visible in the dossier.
        hit_keys = {h.key for h in hits}
        mask_names: list[str] = []
        if "martech_adoption" in hit_keys:
            mask_names += P.MARTECH_VENDORS
        if "agency_account_move" in hit_keys:
            mask_names += _AGENCY_OBJECT_NAMES
        res_title = _mask_object_names(title, mask_names) if mask_names else title
        res_summary = _mask_object_names(summary, mask_names) if mask_names else summary
        # Candidate chain: structured company (most reliable) → named-employer
        # extractor (public/charity/HE/housing orgs the strict extractor
        # misses) → strict RNS-suffix/peer extractor.
        candidate = (
            (s.get("company") or "").strip()
            or extract_named_employer(res_title, res_summary)
            or extract_company(res_title, res_summary)
        )
        # Account-relevance gate, now TIERED (text-first). A watchlist name
        # appearing as the headline subject scores full weight; a
        # well-formed off-watchlist employer is admitted as a broader-market
        # lead (discounted in the ranker); off-universe noise still drops.
        # This still kills the extractor's false positives — 'Three UK' from
        # "Three arrested…", 'SSE' from a Nano Dimension story, the EQS wire
        # prefix — because mis-extracted PEER names are watchlist members and
        # are barred from the off-watchlist path (they must earn the
        # watchlist tier via a real subject match).
        #
        # Crucially, this runs EVEN WITH an empty candidate: the watchlist
        # scan is text-based, so a curated account that is the headline
        # subject still resolves at full weight even when no extractor could
        # name it (universities / NHS trusts / housing assocs carry no plc
        # suffix and aren't peers). The old no-company gate dropped those
        # before the scan ran — silently losing watchlist coverage that was
        # deliberately added. A candidate is only required for the
        # off-watchlist path. Fail-open if the watchlist can't load; the
        # resolved canonical name keeps display + contact-matching consistent.
        company, acct_tier = classify_account(candidate, res_title, res_summary)
        if not company:
            if not candidate:
                rejected_no_company += 1
                log.info("drop (no company): %r [%s]", title[:100], s.get("source", ""))
            else:
                rejected_off_universe += 1
                log.info("drop (off-universe): %s — %r [%s]",
                         candidate, title[:90], s.get("source", ""))
            continue
        if acct_tier == "off_watchlist":
            admitted_off_watchlist += 1
        for trigger in hits:
            if trigger.key == "regulator_action":
                amt = P.extract_gbp_amount_millions(body)
                if amt is None or amt < 5:
                    rejected_subthreshold_regulator += 1
                    log.info("drop (regulator <£5m): %s — %r", company, title[:100])
                    continue
            if trigger.key == "ic_platform_rfp":
                # Gate to large UK employers — IC platform purchases at small
                # cos don't predict senior comms hires. Use the curated peers
                # list as the size proxy (~140 FTSE-350-ish employers).
                from tool.peers import detect_sector
                if detect_sector(company) is None:
                    log.info("drop (ic_platform_rfp at small employer): %s", company)
                    continue
            if trigger.key in ("personal_brand_velocity", "ned_trustee_appointment"):
                # Person-centric soft signals: only fire when the item names a
                # comms / corporate-affairs role, so we're tracking a comms
                # leader's restlessness (and the resolved company is their
                # employer), not a generic board/charity appointment.
                if not P.COMMS_ROLE_RX.search(body):
                    log.info("drop (no comms-role context): %s — %r",
                             trigger.key, title[:90])
                    continue
            if trigger.key == "contract_loss":
                # Filter false positives: a contract loss only counts if it's
                # (a) reported via RNS (legally material by definition) or
                # (b) explicitly tagged with a £5m+ amount. Otherwise sports/
                # HR/SaaS "contract not renewed" noise floods the brief.
                tier = _tier_from_source_label(s.get("source", ""))
                amt = P.extract_gbp_amount_millions(body)
                material = tier == "listed" or (amt is not None and amt >= 5)
                if not material:
                    rejected_contract_loss_immaterial += 1
                    log.info("drop (contract_loss immaterial): %s — %r", company, title[:100])
                    continue
            ev = _evidence_sentence(body, trigger.patterns)
            log.info("trigger %s: %s — %r", trigger.key, company, ev[:80])
            events.append(TriggerEvent(
                trigger_key=trigger.key,
                trigger_label=trigger.label,
                company=company,
                evidence=ev,
                url=s.get("url", ""),
                source_label=s.get("source", ""),
                published=_parse_date(s.get("published", "")),
                raw_signal_id=s.get("id", ""),
                tier_hint=_tier_from_source_label(s.get("source", "")),
                account_tier=acct_tier,
            ))

    log.info(
        "detect_events summary: %d items matched patterns, %d events emitted "
        "(%d off-watchlist/broader-market), %d dropped no-company, "
        "%d dropped off-universe, %d dropped regulator-subthreshold, "
        "%d dropped contract-loss-immaterial",
        pattern_hits, len(events), admitted_off_watchlist, rejected_no_company,
        rejected_off_universe,
        rejected_subthreshold_regulator, rejected_contract_loss_immaterial,
    )
    return events


def _evidence_sentence(text: str, patterns: list) -> str:
    """Return the first sentence that contains a pattern hit, trimmed to ~200 chars."""
    # Split on full-stops, keep context
    for sent in re.split(r"(?<=[\.!?])\s+", text):
        if any(p.search(sent) for p in patterns):
            s = sent.strip()
            if len(s) > 220:
                s = s[:217] + "..."
            return s
    return (text[:200] + "...") if len(text) > 200 else text
