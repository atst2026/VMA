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

# The set above is a fast-path SEED. The authoritative test is "is this P31
# type a subclass* of organization (Q43229)?" via the P279 chain — so any
# legitimate org subtype (e.g. 'public limited company' Q5225895, which is what
# made Whitbread's good logo unreachable) qualifies without enumerating it.
_ORG_ROOT = "Q43229"            # organization
_MAX_SUBCLASS_DEPTH = 6         # plc -> public company -> company -> business -> org
_SUBCLASS_ORG_CACHE: dict[str, bool] = {}   # type QID -> is-subclass*-of-org


def _type_is_org(qid: str, _depth: int = 0, _seen: set | None = None) -> bool:
    """True if a P31 type QID is the organization root or a (transitive)
    subclass of it via P279. Memoised per type QID, so each unusual type is
    walked at most once for the whole process."""
    if qid in _ELIGIBLE_ORG_QIDS or qid == _ORG_ROOT:
        return True
    if qid in _SUBCLASS_ORG_CACHE:
        return _SUBCLASS_ORG_CACHE[qid]
    if _depth >= _MAX_SUBCLASS_DEPTH:
        return False
    if _seen is None:
        _seen = set()
    if qid in _seen:
        return False
    _seen.add(qid)
    data = _wd_get({"action": "wbgetentities", "ids": qid,
                    "props": "claims", "languages": "en"})
    parents = []
    if data:
        claims = ((data.get("entities") or {}).get(qid) or {}).get("claims") or {}
        for c in claims.get("P279", []) or []:
            try:
                parents.append(c["mainsnak"]["datavalue"]["value"]["id"])
            except (KeyError, TypeError):
                continue
    result = any(_type_is_org(p, _depth + 1, _seen) for p in parents)
    _SUBCLASS_ORG_CACHE[qid] = result
    return result


def _p31_is_org(p31_qids: set) -> bool:
    """Is an entity an organisation, given its set of P31 (instance-of) types?
    Fast path: any type in the seed set (no network). Else: P279 subclass walk."""
    if p31_qids & _ELIGIBLE_ORG_QIDS:
        return True
    return any(_type_is_org(q) for q in p31_qids)

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


# --- Lead-name cleaning -------------------------------------------------
# BD-lead company names arrive with market-data noise that breaks Wikidata
# search and domain resolution outright — e.g. "Elior Group Stock",
# "Kingsoft Corporation (03888)". We strip ONLY unambiguous noise: a trailing
# stock/share word, and a trailing parenthetical that is clearly a ticker or
# exchange code (has a digit, or an exchange prefix like "LON:"). We do NOT
# touch legal suffixes or meaningful tokens (Group, Holdings, Corporation,
# "(UK)") so currently-good names are returned unchanged.
_TICKER_PAREN_RX = re.compile(r"\s*\(([^)]*)\)\s*$")
_TRAILING_MARKET_RX = re.compile(
    r"\s+(stock price|share price|stock|shares)\s*$", re.IGNORECASE)
_EXCHANGE_PREFIX_RX = re.compile(
    r"^(?:LON|LSE|NYSE|NASDAQ|NYSEARCA|AMEX|HKG|HKEX|SEHK|ASX|TSE|TYO|EPA|ETR|"
    r"FRA|AMS|BIT|BME|SWX|STO|OTC|OTCMKTS)\s*[:.]", re.IGNORECASE)


def _looks_like_ticker(inner: str) -> bool:
    s = (inner or "").strip()
    if not s:
        return False
    if any(ch.isdigit() for ch in s):     # e.g. 03888, 700
        return True
    if _EXCHANGE_PREFIX_RX.match(s):       # e.g. "LON: WTB", "NASDAQ:MSFT"
        return True
    return False                           # leave "(UK)", "(Holdings)", etc.


def clean_name(name: str) -> str:
    """Strip clear market-data noise from a lead company name. Conservative:
    returns currently-good names unchanged. Used for BOTH resolution and the
    cover heading text."""
    if not name:
        return name
    s = name.strip()
    # Strip a trailing ticker/exchange parenthetical (possibly more than one).
    m = _TICKER_PAREN_RX.search(s)
    while m and _looks_like_ticker(m.group(1)):
        s = s[:m.start()].strip()
        m = _TICKER_PAREN_RX.search(s)
    # Strip trailing market words (e.g. "... Stock", "... Share Price").
    while True:
        s2 = _TRAILING_MARKET_RX.sub("", s).strip()
        if s2 == s:
            break
        s = s2
    return s or name.strip()


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
    is_org = _p31_is_org(p31_qids)

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


_SCORED_CACHE: dict[str, list[dict]] = {}


def _scored_candidates(name: str) -> list[dict]:
    """Wikidata candidates for a name, each scored: [{qid, is_org, domain,
    score}, ...] in search-relevance order. Cached per process (one search +
    entity fetch per name), shared by domain resolution and the P154 logo
    lookup so neither path double-hits Wikidata."""
    key = _normalize(name)
    if key in _SCORED_CACHE:
        return _SCORED_CACHE[key]
    out = []
    for cand in _search_entities(name):
        is_org, domain, names = _entity_company_and_domain(cand["id"], name)
        pool = names or [cand["label"]]
        score = max((_name_match_score(name, n) for n in pool), default=0.0)
        out.append({"qid": cand["id"], "is_org": is_org,
                    "domain": domain, "score": score})
    _SCORED_CACHE[key] = out
    return out


def _pick_best(candidates: list[dict]):
    """Two-pass pick over already-scored candidates (search order preserved):
    an EXACT normalised match wins over a merely-close one."""
    for c in candidates:
        if c["score"] >= 0.999:
            return c
    for c in candidates:
        if c["score"] >= _ACCEPT_SCORE:
            return c
    return None


def _best_entity(name: str) -> tuple[str | None, str | None]:
    """(qid, domain) of the best eligible ORG with an official website whose
    name matches the lead — the domain-resolution entity. (None, None) if none."""
    best = _pick_best([c for c in _scored_candidates(name)
                       if c["is_org"] and c["domain"]])
    if best:
        log.info("wikidata: %r -> %s via %s (score %.2f)",
                 name, best["domain"], best["qid"], best["score"])
        return best["qid"], best["domain"]
    return None, None


def _logo_qid(name: str) -> str | None:
    """QID of the best eligible ORG whose name matches the lead, WITHOUT
    requiring an official website (P856) — so we can read a P154 logo even for
    registry-resolved companies or orgs that list no website."""
    best = _pick_best([c for c in _scored_candidates(name) if c["is_org"]])
    return best["qid"] if best else None


def _resolve_via_wikidata(name: str) -> str | None:
    """Domain for a name via Wikidata (see _best_entity)."""
    return _best_entity(name)[1]


def resolve_domain(name: str) -> str | None:
    """Resolve a company name to its official domain, or None to signal the
    caller should use the text wordmark.

    Order: verified registry (no network) -> Wikidata with company + name
    confidence guards. Results (including misses) are cached per process.
    """
    if not name or not name.strip():
        return None
    name = clean_name(name)   # strip market-data noise before any lookup

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


# ======================================================================
# Wikidata logo image (P154) — the PREFERRED cover logo source
# ----------------------------------------------------------------------
# logo.dev's image API returns the brand SYMBOL, often without the company
# name. Wikidata's "logo image" (P154) is frequently the full logo WITH the
# name. So the cover prefers P154 when one exists and passes the quality +
# visible-on-white gates (applied by the caller in pitch_proposal); otherwise
# it falls back to logo.dev. This needs the entity QID for EVERY company —
# including registry-resolved ones — so wikidata_logo_png looks the entity up
# by name via the shared, cached candidate scorer (one extra Wikidata lookup
# per company, cached for the run).
#
# Many P154 files are SVG; rasterising them needs cairosvg (a real dependency,
# riding the cairo stack WeasyPrint already requires — the CI workflow apt-
# installs libcairo2). If cairosvg is unavailable or a file fails to
# rasterise, we fall back to logo.dev rather than failing.
# ======================================================================
_COMMONS_FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/"
_P154_MAX_BYTES = 4_000_000
_P154_RASTER_WIDTH = 600          # px width we rasterise SVGs to
_P154_CACHE: dict[str, bytes | None] = {}   # keyed on QID


def _p154_filename(qid: str) -> str | None:
    data = _wd_get({"action": "wbgetentities", "ids": qid,
                    "props": "claims", "languages": "en"})
    if not data:
        return None
    claims = ((data.get("entities") or {}).get(qid) or {}).get("claims") or {}
    for c in claims.get("P154", []) or []:
        if c.get("rank") == "deprecated":
            continue
        try:
            return c["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
    return None


def _fetch_p154_raster(filename: str) -> bytes | None:
    """Fetch a Commons P154 image as raster bytes. PNG/JPEG/WebP returned as
    downloaded; SVG rasterised via cairosvg. None on any failure / size cap."""
    import urllib.parse
    try:
        from tool.sources._http import get as _get
        url = _COMMONS_FILEPATH + urllib.parse.quote(filename.replace(" ", "_"))
        r = _get(url, headers={"User-Agent": _USER_AGENT}, timeout=_HTTP_TIMEOUT)
        if r is None or r.status_code != 200 or not r.content:
            return None
        raw = r.content
        if len(raw) > _P154_MAX_BYTES:
            log.info("P154 %s too large (%d bytes) — skipped", filename, len(raw))
            return None
        if filename.lower().endswith(".svg"):
            try:
                import cairosvg
                return cairosvg.svg2png(bytestring=raw,
                                        output_width=_P154_RASTER_WIDTH)
            except Exception as e:
                log.info("P154 svg raster failed for %s (%s) — fallback", filename, e)
                return None
        return raw
    except Exception as e:  # pragma: no cover - network/runtime guard
        log.info("P154 fetch failed for %s: %s", filename, e)
        return None


def wikidata_logo_png(company: str) -> bytes | None:
    """Raster bytes of the company's Wikidata logo image (P154), or None.
    Works for any company (registry- or Wikidata-resolved) by name-matching an
    eligible org entity. Cached per process. The caller must still run the
    bytes through its quality + visible-on-white gates before use."""
    if not company or not company.strip():
        return None
    company = clean_name(company)   # strip market-data noise before lookup
    qid = _logo_qid(company)
    if not qid:
        return None
    if qid in _P154_CACHE:
        return _P154_CACHE[qid]
    fn = _p154_filename(qid)
    out = _fetch_p154_raster(fn) if fn else None
    _P154_CACHE[qid] = out
    return out
