#!/usr/bin/env python3
"""Resolve a company NAME to its official DOMAIN for the pitch-pack cover logo.

The comms proposal cover (tool/pitch_proposal.py) shows the target company's
real logo when it can fetch a clean one. logo.dev's image API is keyed on a
DOMAIN, but BD leads only carry a company NAME (see predictor_pipeline) — so
we need a name -> domain hop.

Two-step resolution, highest confidence first:

  1. tool/company_identity.resolve(name).domain — the hand-verified registry.
     If it has a domain, use it: zero network, zero guessing.
  2. Wikidata (no API key, no signup):
       a. wbsearchentities to find candidate entities for the name.
       b. For the top candidates, fetch the entity and read the official
          website (property P856); extract the registrable domain.
     Two confidence guards, BOTH required to accept:
       - the entity must be a company / business / organisation
         (instance-of P31 against an allow-list of org types + a few
          common subclasses), AND
       - the entity label/aliases must closely match the lead name
         (normalise + fuzzy compare).
     Anything ambiguous, not a company, or with no P856 -> None, and the
     caller falls back to the text wordmark.

Why the guards matter: a wrong domain would feed a wrong logo onto a
client-facing proposal. The P31 + name-match gates here, combined with the
existing Pillow visibility gate in pitch_proposal, mean the worst case is
always the clean text wordmark — never a wrong logo.

Resolved domains are cached for the life of the process (a pitch-pack run is
short-lived) so repeat lookups for the same name don't re-hit Wikidata.

No API key is required. Wikimedia asks for a descriptive User-Agent, which we
set on every request.
"""
from __future__ import annotations

import difflib
import logging
import os
import re

log = logging.getLogger("company_domain")

_WD_API = "https://www.wikidata.org/w/api.php"
_HTTP_TIMEOUT = 10
# Wikimedia policy: identify the client with a descriptive UA incl. contact.
_USER_AGENT = (
    os.environ.get("WIKIDATA_USER_AGENT")
    or "VMA-PitchPack/1.0 (https://github.com/atst2026/VMA; logo domain lookup)"
)

# How many search candidates to inspect before giving up.
_MAX_CANDIDATES = 5

# Name-match confidence: accept a Wikidata domain only when the entity label
# (or an alias) scores at least this close to the lead name after
# normalisation. _name_match_score folds in sequence ratio, token overlap and
# containment. Erring strict — the fallback (text wordmark) is always clean.
_ACCEPT_SCORE = 0.84

# Wikidata QIDs that count as "this entity is a company/organisation".
# P31 (instance of) must include one of these (directly or via the small set
# of subclasses we expand below) for a candidate to be eligible.
_COMPANY_QIDS = {
    "Q4830453",   # business
    "Q6881511",   # enterprise
    "Q783794",    # company
    "Q891723",    # public company
    "Q210167",    # technology company
    "Q43229",     # organization
    "Q161726",    # multinational corporation
    "Q1589009",   # privately held company
    "Q167037",    # corporation
    "Q6500733",   # retail chain
    "Q507619",    # chain store
}

_NONWORD_RX = re.compile(r"[^a-z0-9 ]+")
_WS_RX = re.compile(r"\s+")
_SUFFIX_RX = re.compile(
    r"\b(plc|ltd|limited|llp|group|holdings|holding|inc|incorporated|corp|"
    r"corporation|company|co|uk|the|and)\b",
    re.IGNORECASE,
)
_BAD_DOMAINS = {
    "wikipedia.org", "wikidata.org", "linkedin.com", "facebook.com",
    "twitter.com", "x.com", "instagram.com", "youtube.com",
}

# Process-lifetime cache: normalised name -> domain | None (None cached too,
# so a miss isn't retried within the same run).
_DOMAIN_CACHE: dict[str, str | None] = {}


def _normalize(name: str) -> str:
    s = (name or "").lower().strip()
    s = s.replace("&", " and ")
    s = _NONWORD_RX.sub(" ", s)
    s = _SUFFIX_RX.sub(" ", s)
    return _WS_RX.sub(" ", s).strip()


def _name_match_score(a: str, b: str) -> float:
    """Confidence in [0,1] that two company names refer to the same company.
    Max of: sequence ratio, token Jaccard, and a containment bonus, on the
    normalised forms."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    jac = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    contain = 0.0
    shorter, longer = sorted((na, nb), key=len)
    if len(shorter) >= 4 and shorter in longer:
        contain = 0.9
    return max(ratio, jac, contain)


def _strip_domain(value: str) -> str:
    v = (value or "").strip().lower()
    v = v.removeprefix("http://").removeprefix("https://").removeprefix("www.")
    return v.split("/")[0].split("?")[0]


# TLD preference when an entity lists several official sites — keeps a UK
# company on its primary domain rather than a foreign-country variant.
_TLD_RANK = (".com", ".co.uk", ".uk", ".io", ".ai", ".org", ".net")


def _pick_domain(domains: list[str]) -> str | None:
    if not domains:
        return None
    def rank(d: str) -> int:
        for i, suf in enumerate(_TLD_RANK):
            if d.endswith(suf):
                return i
        return len(_TLD_RANK)
    return sorted(domains, key=rank)[0]


def _wd_get(params: dict) -> dict | None:
    """GET the Wikidata API with the required descriptive User-Agent."""
    try:
        from tool.sources._http import get as _get
        r = _get(_WD_API, params={**params, "format": "json"},
                 headers={"User-Agent": _USER_AGENT}, timeout=_HTTP_TIMEOUT)
        if r is None or r.status_code != 200:
            log.info("wikidata: bad response (%s)", getattr(r, "status_code", None))
            return None
        return r.json()
    except Exception as e:  # pragma: no cover - network/parse guard
        log.info("wikidata request failed: %s", e)
        return None


def _search_entities(name: str) -> list[dict]:
    """wbsearchentities -> [{'id','label','aliases'(maybe)}...] best-effort."""
    data = _wd_get({
        "action": "wbsearchentities", "search": name,
        "language": "en", "uselang": "en", "type": "item",
        "limit": _MAX_CANDIDATES,
    })
    if not data:
        return []
    out = []
    for hit in data.get("search", []) or []:
        out.append({
            "id": hit.get("id"),
            "label": hit.get("label") or "",
            # wbsearchentities returns the matched alias in "match" sometimes.
            "match": (hit.get("match") or {}).get("text") or "",
            "description": hit.get("description") or "",
        })
    return [c for c in out if c["id"]]


def _entity_company_and_domain(qid: str) -> tuple[bool, str | None, list[str]]:
    """Fetch an entity; return (is_company, domain_or_None, names).
    names = label + aliases (en) for the name-match guard."""
    data = _wd_get({"action": "wbgetentities", "ids": qid,
                    "props": "claims|labels|aliases", "languages": "en"})
    if not data:
        return (False, None, [])
    ent = (data.get("entities") or {}).get(qid) or {}
    claims = ent.get("claims") or {}

    # instance-of (P31) -> is this a company/organisation?
    p31_qids = set()
    for c in claims.get("P31", []) or []:
        try:
            p31_qids.add(c["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            continue
    is_company = bool(p31_qids & _COMPANY_QIDS)

    # official website (P856) -> registrable domain. An entity can list
    # several (per-country) sites; prefer a .com/.co.uk/.uk site over a
    # foreign TLD so a UK company resolves to its primary domain (e.g.
    # Deliveroo lists deliveroo.it first, but we want deliveroo.co.uk).
    domains = []
    for c in claims.get("P856", []) or []:
        try:
            url = c["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        dom = _strip_domain(url)
        if dom and dom not in _BAD_DOMAINS:
            domains.append(dom)
    domain = _pick_domain(domains)

    # names: label + en aliases
    names = []
    lbl = ((ent.get("labels") or {}).get("en") or {}).get("value")
    if lbl:
        names.append(lbl)
    for a in (ent.get("aliases") or {}).get("en", []) or []:
        if a.get("value"):
            names.append(a["value"])
    return (is_company, domain, names)


def _registry_domain(name: str) -> str | None:
    try:
        from tool import company_identity
        return company_identity.resolve(name).domain or None
    except Exception:
        return None


def _resolve_via_wikidata(name: str) -> str | None:
    """Search Wikidata, then accept the first candidate that is a company,
    has an official website, AND whose name closely matches the lead."""
    for cand in _search_entities(name):
        is_company, domain, names = _entity_company_and_domain(cand["id"])
        if not is_company or not domain:
            continue
        # Best name-match across label + aliases (fall back to the search label).
        pool = names or [cand["label"]]
        score = max((_name_match_score(name, n) for n in pool), default=0.0)
        if score >= _ACCEPT_SCORE:
            log.info("wikidata: %r -> %s via %s (confidence %.2f)",
                     name, domain, cand["id"], score)
            return domain
        log.info("wikidata: %s rejected for %r (best name score %.2f)",
                 cand["id"], name, score)
    return None


def resolve_domain(name: str) -> str | None:
    """Resolve a company name to its official domain, or None to signal the
    caller should use the text wordmark.

    Order: verified registry (no network) -> Wikidata with company + name
    confidence guards. Results (including misses) are cached per process.
    """
    if not name or not name.strip():
        return None

    # Registry override — highest confidence, no network call.
    reg = _registry_domain(name)
    if reg:
        return reg

    key = _normalize(name)
    if key in _DOMAIN_CACHE:
        return _DOMAIN_CACHE[key]

    domain = _resolve_via_wikidata(name)
    _DOMAIN_CACHE[key] = domain
    return domain
