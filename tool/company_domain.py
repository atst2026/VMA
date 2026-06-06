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
       - the entity must be an eligible ORGANISATION (instance-of P31 in an
         allow-list spanning companies, universities, public-sector bodies /
         councils, NHS trusts and charities — the real lead universe), AND
       - the entity label/aliases must closely match the lead name
         (normalise + fuzzy compare; an exact match is preferred, and the
          substring bonus is withheld for named sub-entities like a club or
          press so a parent org isn't conflated with them).
     When an entity lists several official websites, the one whose label
     matches the lead name wins (so a renamed org resolves to its CURRENT
     domain, not a stale one). Anything ambiguous, not an org, or with no
     P856 -> None, and the caller falls back to the text wordmark.

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

# Wikidata QIDs that count as "this entity is an eligible ORGANISATION".
# A candidate's instance-of (P31) must include one of these. The real lead
# universe isn't only private companies — it includes universities, councils,
# NHS trusts and charities — so the allow-list spans commercial + public-sector
# + non-profit org types. The name-match (>= _ACCEPT_SCORE) and Pillow quality
# gates are unchanged, so widening "what is an org" never loosens "is it the
# right named org with a usable logo". All QIDs below are label-verified.
_ELIGIBLE_ORG_QIDS = {
    # ---- Commercial ----
    "Q4830453",    # business
    "Q6881511",    # enterprise
    "Q783794",     # company
    "Q891723",     # public company
    "Q210167",     # technology company
    "Q43229",      # organization
    "Q161726",     # multinational corporation
    "Q1589009",    # privately held company
    "Q167037",     # corporation
    "Q6500733",    # retail chain
    "Q507619",     # chain store
    # ---- Education ----
    "Q3918",       # university
    "Q5341295",    # educational organization
    "Q38723",      # higher education institution
    "Q3354859",    # collegiate university
    "Q62078547",   # public research university
    "Q875538",     # public university
    # ---- Public sector / government ----
    "Q837766",     # local authority
    "Q110416322",  # unitary authority in Wales
    "Q327333",     # government agency
    "Q2659904",    # government organization
    "Q1639780",    # regulatory agency
    "Q294163",     # public institution
    # ---- Health ----
    "Q6954187",    # NHS foundation trust
    "Q6954197",    # NHS trust
    # ---- Charity / non-profit ----
    "Q708676",     # charitable organization
    "Q163740",     # nonprofit organization
}

_NONWORD_RX = re.compile(r"[^a-z0-9 ]+")
_WS_RX = re.compile(r"\s+")
_SUFFIX_RX = re.compile(
    r"\b(plc|ltd|limited|llp|group|holdings|holding|inc|incorporated|corp|"
    r"corporation|company|co|uk|the|and)\b",
    re.IGNORECASE,
)

# Tokens that mark a DIFFERENT entity sharing a name prefix — a subsidiary,
# sports team, publisher, building, or election rather than the parent org.
# When the longer name adds one of these, the substring/containment bonus is
# withheld so "Cambridge University" doesn't match "Cambridge University Press"
# or "Cambridge University Cricket Club".
_DISAMBIG_WORDS = {
    "press", "club", "fc", "afc", "rfc", "cricket", "rugby", "football",
    "election", "elections", "offices", "office", "constituency", "ward",
    "museum", "library", "society", "union", "students", "alumni",
    "station", "branch", "team", "band", "album", "song",
}
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
    # Containment bonus: one name is a substring of the other — a strong
    # signal (e.g. "sainsbury" in "j sainsbury"), BUT withheld when the longer
    # name adds a disambiguation word, so a parent org isn't conflated with a
    # named sub-entity ("Cambridge University" vs "...Cricket Club"/"...Press").
    contain = 0.0
    shorter, longer = sorted((na, nb), key=len)
    if len(shorter) >= 4 and shorter in longer:
        extra = set(longer.split()) - set(shorter.split())
        if not (extra & _DISAMBIG_WORDS):
            contain = 0.9
    return max(ratio, jac, contain)


def _strip_domain(value: str) -> str:
    v = (value or "").strip().lower()
    v = v.removeprefix("http://").removeprefix("https://").removeprefix("www.")
    return v.split("/")[0].split("?")[0]


# TLD preference when an entity lists several official sites — keeps a UK
# org on its primary domain rather than a foreign-country variant.
_TLD_RANK = (".com", ".co.uk", ".uk", ".io", ".ai", ".org", ".net")
# Public-suffix-ish endings stripped to get a domain's registrable LABEL, so
# we can name-match it: entaingroup.com -> "entaingroup", cam.ac.uk -> "cam".
_PUBLIC_SUFFIXES = (
    ".co.uk", ".org.uk", ".ac.uk", ".gov.uk", ".net.uk", ".plc.uk",
    ".com", ".org", ".net", ".io", ".ai", ".uk", ".eu",
)
# Wikidata statement rank -> preference (lower = better). Deprecated dropped.
_RANK_PREF = {"preferred": 0, "normal": 1}


def _tld_rank(d: str) -> int:
    for i, suf in enumerate(_TLD_RANK):
        if d.endswith(suf):
            return i
    return len(_TLD_RANK)


def _registrable_apex(domain: str) -> str:
    """Reduce a domain to its registrable apex (drop sub-domains), so per-
    language/country sub-domains collapse to one key for logo lookup:
    'en.powys.gov.uk'/'cy.powys.gov.uk' -> 'powys.gov.uk'; 'cam.ac.uk' stays."""
    for suf in sorted(_PUBLIC_SUFFIXES, key=len, reverse=True):
        if domain.endswith(suf):
            head = domain[: -len(suf)]            # e.g. "en.powys" or "cam"
            last = head.rsplit(".", 1)[-1]        # "powys" / "cam"
            return f"{last}{suf}" if last else domain
    return domain


def _domain_label(domain: str) -> str:
    """Registrable label of a domain as words, for name matching.
    'entaingroup.com' -> 'entaingroup'; 'gvc-plc.com' -> 'gvc plc';
    'powys.gov.uk' -> 'powys'."""
    d = _registrable_apex(domain)
    for suf in sorted(_PUBLIC_SUFFIXES, key=len, reverse=True):
        if d.endswith(suf):
            d = d[: -len(suf)]
            break
    return d.replace("-", " ").replace(".", " ").strip()


def _pick_domain(cands: list[tuple[str, str]], lead_name: str,
                 entity_names: list[str] | None = None) -> str | None:
    """Choose the best official-website domain for an entity.

    cands: list of (domain, statement_rank). Ranked by, in order:
      1. how well the domain's registrable label matches the LEAD name (so a
         renamed org's CURRENT domain wins — entaingroup.com beats the stale
         gvc-plc.com for "Entain", because the entity still lists "GVC
         Holdings" as an alias and gvc-plc would match THAT);
      2. Wikidata statement rank (preferred over normal);
      3. TLD preference (primary UK/.com over a foreign variant).
    Sub-domains are reduced to the registrable apex and de-duplicated first.
    """
    if not cands:
        return None
    # Reduce to apex + dedupe, keeping the best (lowest) rank seen per apex.
    by_apex: dict[str, str] = {}
    for dom, rank in cands:
        apex = _registrable_apex(dom)
        if apex not in by_apex or _RANK_PREF.get(rank, 2) < _RANK_PREF.get(by_apex[apex], 2):
            by_apex[apex] = rank

    match_names = [lead_name] if lead_name else (entity_names or [])

    def sort_key(item: tuple[str, str]):
        domain, rank = item
        label = _domain_label(domain)
        name_sc = max((_name_match_score(n, label) for n in match_names), default=0.0)
        return (-name_sc, _RANK_PREF.get(rank, 2), _tld_rank(domain))

    return sorted(by_apex.items(), key=sort_key)[0][0]


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


def _entity_company_and_domain(qid: str, lead_name: str = "") -> tuple[bool, str | None, list[str]]:
    """Fetch an entity; return (is_org, domain_or_None, names).
    names = label + aliases (en) for the name-match guard. lead_name is used to
    pick the right domain when an entity lists several official websites."""
    data = _wd_get({"action": "wbgetentities", "ids": qid,
                    "props": "claims|labels|aliases", "languages": "en"})
    if not data:
        return (False, None, [])
    ent = (data.get("entities") or {}).get(qid) or {}
    claims = ent.get("claims") or {}

    # instance-of (P31) -> is this an eligible organisation?
    p31_qids = set()
    for c in claims.get("P31", []) or []:
        try:
            p31_qids.add(c["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            continue
    is_org = bool(p31_qids & _ELIGIBLE_ORG_QIDS)

    # names: label + en aliases (needed to score domain candidates below).
    names = []
    lbl = ((ent.get("labels") or {}).get("en") or {}).get("value")
    if lbl:
        names.append(lbl)
    for a in (ent.get("aliases") or {}).get("en", []) or []:
        if a.get("value"):
            names.append(a["value"])

    # official website(s) (P856) -> (domain, rank). An entity may list several
    # (a renamed org keeps its old site; a multinational lists per-country
    # sites). Drop deprecated statements; _pick_domain then prefers the domain
    # whose label matches the name, then preferred rank, then TLD.
    cands = []
    for c in claims.get("P856", []) or []:
        if c.get("rank") == "deprecated":
            continue
        try:
            url = c["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        dom = _strip_domain(url)
        if dom and dom not in _BAD_DOMAINS:
            cands.append((dom, c.get("rank") or "normal"))
    domain = _pick_domain(cands, lead_name, names)

    return (is_org, domain, names)


def _registry_domain(name: str) -> str | None:
    try:
        from tool import company_identity
        return company_identity.resolve(name).domain or None
    except Exception:
        return None


def _resolve_via_wikidata(name: str) -> str | None:
    """Search Wikidata and accept the best eligible candidate: an organisation
    with an official website whose name closely matches the lead.

    Two passes so an EXACT normalised name match always wins over a merely
    close one — e.g. "Cambridge University" resolves to the University (exact
    via its alias) rather than "Cambridge University Press" (a fuzzy 0.86
    sequence match that would otherwise pass on search-order alone)."""
    scored = []  # (score, qid, domain) for eligible, domain-bearing candidates
    for cand in _search_entities(name):
        is_org, domain, names = _entity_company_and_domain(cand["id"], name)
        if not is_org or not domain:
            continue
        pool = names or [cand["label"]]
        score = max((_name_match_score(name, n) for n in pool), default=0.0)
        scored.append((score, cand["id"], domain))

    # Pass 1: an exact normalised match (score == 1.0), preserving search order.
    for score, qid, domain in scored:
        if score >= 0.999:
            log.info("wikidata: %r -> %s via %s (exact match)", name, domain, qid)
            return domain
    # Pass 2: first candidate clearing the confidence threshold.
    for score, qid, domain in scored:
        if score >= _ACCEPT_SCORE:
            log.info("wikidata: %r -> %s via %s (confidence %.2f)",
                     name, domain, qid, score)
            return domain
    if scored:
        log.info("wikidata: no confident match for %r (best %.2f)",
                 name, max(s for s, _, _ in scored))
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
