#!/usr/bin/env python3
"""Resolve an arbitrary company NAME to its official website + on-page logo.

The verified registry (tool/company_identity) is the highest-confidence source,
but BD leads name ANY company — there is no fixed list — so the pitch pack must
also resolve companies that aren't curated. This module supplies the logic to
find a company's official site from its name (WITHOUT a paid API) and to locate
the logo on that site's landing page:

  1. GUESS candidate domains from the name (currys -> currys.com / currys.co.uk).
  2. CONFIDENCE GATE — a candidate is only accepted when its live homepage
     actually corresponds to the company: the registrable domain matches the
     name, OR the homepage <title>/og:site_name names the company. This is what
     stops us pinning a stranger's domain (a wrong logo on a client proposal is
     worse than none).
  3. SEARCH FALLBACK — when guessing is inconclusive, a keyless web search
     (DuckDuckGo HTML) supplies candidate domains, each put through the SAME gate.
  4. LOGO EXTRACTION — from the matched homepage, the landing-page logo in
     priority order: the header wordmark <img>, then the site-declared icons
     (apple-touch / mask / svg / png).

Everything here is PURE (no network) so it is fully unit-testable; the caller
(tool/logo_service) owns all fetching and feeds the HTML in. Resolution is
best-effort — when no confident site/logo is found the caller falls back to a
clean text wordmark (see tool/pitch_proposal), so a pack is produced for every
company regardless of name.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlparse

try:                                   # bs4 is a project dependency (requirements.txt)
    from bs4 import BeautifulSoup
except Exception:                      # pragma: no cover - defensive
    BeautifulSoup = None  # type: ignore


# Hosts that are never a company's own site — directories, socials, press,
# registries, marketplaces. A search hit on any of these is discarded.
EXCLUDED_REGISTRABLES: frozenset[str] = frozenset({
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "tiktok.com", "pinterest.com", "reddit.com",
    "wikipedia.org", "wikimedia.org", "wikidata.org", "fandom.com",
    "crunchbase.com", "bloomberg.com", "reuters.com", "ft.com",
    "theguardian.com", "bbc.co.uk", "forbes.com", "businesswire.com",
    "prnewswire.com", "globenewswire.com", "yahoo.com",
    "companieshouse.gov.uk", "service.gov.uk", "gov.uk",
    "glassdoor.com", "glassdoor.co.uk", "indeed.com", "trustpilot.com",
    "amazon.com", "amazon.co.uk", "ebay.com", "ebay.co.uk",
    "google.com", "duckduckgo.com", "bing.com",
    "yell.com", "endole.co.uk", "opencorporates.com", "dnb.com",
    "zoominfo.com", "pitchbook.com", "owler.com", "rocketreach.co",
    "apple.com", "apps.apple.com", "play.google.com",
    "github.com", "medium.com", "wordpress.com", "blogspot.com",
})

# Two-level public suffixes so we extract the registrable domain correctly
# (currys.co.uk -> currys.co.uk, not co.uk).
_TWO_LEVEL_TLDS = {
    "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk", "ltd.uk", "plc.uk",
    "com.au", "co.nz", "co.za", "com.sg", "co.in", "com.br",
}

# Stripped from a name before slugging / tokenising — corporate suffixes and
# filler that aren't part of the brand mark.
_SUFFIX_WORDS = {
    "plc", "plc.", "p.l.c", "ltd", "ltd.", "limited", "llp", "llc",
    "inc", "inc.", "incorporated", "corp", "corp.", "corporation",
    "company", "co", "co.", "group", "holdings", "holding", "the",
    "gmbh", "ag", "sa", "nv", "bv", "spa", "uk", "gb",
}
# Tiny joining words dropped when building a compact slug variant.
_JOIN_WORDS = {"and", "at", "of", "the", "for", "&"}

_TLDS = (".com", ".co.uk", ".io", ".ai", ".org", ".net", ".group", ".tech")


def registrable(host: str) -> str:
    """The registrable domain of a host (www. stripped, public-suffix aware)."""
    host = (host or "").lower().strip().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LEVEL_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def is_excluded_host(host: str) -> bool:
    return registrable(host) in EXCLUDED_REGISTRABLES


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _words(name: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", (name or "").lower()) if w]


def name_tokens(name: str) -> list[str]:
    """Significant brand tokens — suffixes/filler removed. e.g.
    'Marks & Spencer plc' -> ['marks', 'spencer']."""
    toks = [w for w in _words(name) if w not in _SUFFIX_WORDS]
    return toks or _words(name)


def name_slug(name: str) -> str:
    """Compact, suffix-free slug of a name. 'Currys plc' -> 'currys'."""
    return "".join(name_tokens(name))


def candidate_domains(name: str) -> list[str]:
    """Best-guess official domains for a company name, most-likely first.
    Pure string heuristics — each one is still gated against the live site by
    the caller, so a guess that doesn't resolve/match is simply dropped."""
    toks = name_tokens(name)
    if not toks:
        return []
    stems: list[str] = []

    def _add(stem: str) -> None:
        if stem and stem not in stems:
            stems.append(stem)

    _add("".join(toks))                                  # currys / petsathome
    _add("".join(t for t in toks if t not in _JOIN_WORDS))  # petshome
    if len(toks) > 1:
        _add("-".join(toks))                             # pets-at-home

    # TLD-outer so every stem's .com is tried before any .co.uk, etc. — the
    # most-likely domains come first and survive the cap.
    out: list[str] = []
    for tld in _TLDS:
        for stem in stems:
            dom = stem + tld
            if dom not in out:
                out.append(dom)
    return out[:14]


def site_name(html: str) -> str:
    """The site's self-declared name: og:site_name, else <title>. Lower-cased."""
    if not html:
        return ""
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        og = soup.find("meta", attrs={"property": "og:site_name"})
        if og and og.get("content"):
            return og["content"].strip().lower()
        if soup.title and soup.title.string:
            return soup.title.string.strip().lower()
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return (m.group(1).strip().lower() if m else "")


def name_matches_site(name: str, domain: str, html: str) -> bool:
    """The CONFIDENCE GATE. True only when this domain/page credibly belongs to
    `name`, so the caller never pins a wrong company's logo onto a proposal.

    Accepts on either signal:
      * the registrable domain's core IS the name (currys.co.uk for 'Currys',
        or one contains the other for compacted multi-word names); or
      * the homepage's declared name (og:site_name/<title>) contains the brand
        tokens of `name`.
    """
    nslug = name_slug(name)
    if not nslug:
        return False
    core = _slug(registrable(domain).rsplit(".", 1)[0].split(".")[0])
    if core and (core == nslug
                 or (len(core) >= 4 and core in nslug)
                 or (len(nslug) >= 4 and nslug in core)):
        return True
    site = site_name(html)
    if site:
        toks = [t for t in name_tokens(name) if len(t) >= 3]
        if toks and all(t in site for t in toks):
            return True
        # single distinctive token (>=5 chars) is enough for a long brand word
        long_toks = [t for t in name_tokens(name) if len(t) >= 5]
        if long_toks and any(t in site for t in long_toks) and len(name_tokens(name)) == 1:
            return True
    return False


# Logo-cue substrings used to spot the brand mark in the header/nav.
_LOGO_CUE = re.compile(r"logo|brand|masthead|site-?header|site-?logo", re.I)
_ICON_RANK = {"apple-touch-icon": 4, "mask-icon": 3, "icon": 2, "shortcut icon": 2}


def _abs(base_url: str, href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith("data:") or href.startswith("javascript:"):
        return None
    return urljoin(base_url, href)


def logo_urls_from_html(html: str, base_url: str) -> list[tuple[str, str]]:
    """Ordered (kind, absolute_url) logo candidates extracted from a homepage.

    kind is 'img' (a header/nav wordmark <img> — the real landing-page logo,
    preferred) or 'declared' (a <link rel=...icon...> the page declares). The
    caller fetches + validates each in order; the first real image wins.
    """
    if not html or BeautifulSoup is None:
        return _logo_urls_regex(html, base_url)
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _push(kind: str, url: str | None) -> None:
        if url and url not in seen:
            seen.add(url)
            out.append((kind, url))

    # 1. header/nav wordmark <img>. Search the header/nav first, then the whole
    #    page; an <img> counts as the logo when it (or its wrapping <a>) carries
    #    a logo cue in class/id/alt/src.
    regions = soup.find_all(["header", "nav"]) or [soup]
    scopes = regions + ([soup] if regions and regions[0] is not soup else [])
    for scope in scopes:
        for img in scope.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            attrs = " ".join(filter(None, [
                " ".join(img.get("class", []) if isinstance(img.get("class"), list)
                         else [img.get("class") or ""]),
                img.get("id") or "", img.get("alt") or "", src,
                (img.parent.get("class") and " ".join(img.parent.get("class")) or "")
                if img.parent else "",
            ]))
            if _LOGO_CUE.search(attrs):
                _push("img", _abs(base_url, src))
        if out:
            break

    # 2. site-declared icons (best-ranked first).
    best_rank, best_url = 0, None
    for link in soup.find_all("link"):
        rel = " ".join(link.get("rel", [])).lower() if isinstance(link.get("rel"), list) \
            else (link.get("rel") or "").lower()
        if "icon" not in rel:
            continue
        href = link.get("href")
        url = _abs(base_url, href)
        if not url:
            continue
        low = url.lower()
        rank = (_ICON_RANK.get("apple-touch-icon", 0) if "apple-touch-icon" in rel
                else _ICON_RANK.get("mask-icon", 0) if ("mask-icon" in rel or low.endswith(".svg"))
                else 2 if low.endswith(".png")
                else 1)
        if rank > best_rank:
            best_rank, best_url = rank, url
    _push("declared", best_url)
    return out


def _logo_urls_regex(html: str, base_url: str) -> list[tuple[str, str]]:
    """Regex fallback used only if bs4 is unavailable — declared icon + cued
    <img>. Keeps the module working without a hard bs4 dependency."""
    out: list[tuple[str, str]] = []
    if not html:
        return out
    for m in re.finditer(r"<img\b[^>]*>", html, re.I):
        tag = m.group(0)
        if not _LOGO_CUE.search(tag):
            continue
        src_m = re.search(r"""src\s*=\s*["']([^"']+)""", tag, re.I)
        url = _abs(base_url, src_m.group(1)) if src_m else None
        if url:
            out.append(("img", url))
            break
    best_rank, best_url = 0, None
    for m in re.finditer(r"<link\b[^>]*>", html, re.I):
        tag = m.group(0)
        rel_m = re.search(r"""rel\s*=\s*["']([^"']+)""", tag, re.I)
        href_m = re.search(r"""href\s*=\s*["']([^"']+)""", tag, re.I)
        if not rel_m or not href_m or "icon" not in rel_m.group(1).lower():
            continue
        url = _abs(base_url, href_m.group(1))
        if not url:
            continue
        rel, low = rel_m.group(1).lower(), url.lower()
        rank = (4 if "apple-touch-icon" in rel
                else 3 if ("mask-icon" in rel or low.endswith(".svg"))
                else 2 if low.endswith(".png") else 1)
        if rank > best_rank:
            best_rank, best_url = rank, url
    if best_url:
        out.append(("declared", best_url))
    return out


def search_result_domains(html: str, limit: int = 5) -> list[str]:
    """Registrable domains of the organic results on a DuckDuckGo HTML results
    page, in order, with directories/socials/press filtered out. Used as the
    name->domain fallback when domain-guessing is inconclusive."""
    if not html:
        return []
    hrefs: list[str] = []
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            hrefs.append(a["href"])
    else:                              # pragma: no cover - defensive
        hrefs = re.findall(r"""href\s*=\s*["']([^"']+)""", html, re.I)

    out: list[str] = []
    for href in hrefs:
        target = href
        # DuckDuckGo HTML wraps results in /l/?uddg=<encoded-url>.
        if "uddg=" in href:
            qs = parse_qs(urlparse(href).query)
            if qs.get("uddg"):
                target = qs["uddg"][0]
        host = urlparse(target if "://" in target else "https://" + target).netloc
        if not host:
            continue
        reg = registrable(host)
        if not reg or "." not in reg or reg in EXCLUDED_REGISTRABLES:
            continue
        if reg not in out:
            out.append(reg)
        if len(out) >= limit:
            break
    return out


@dataclass(frozen=True)
class SiteMatch:
    domain: str          # registrable domain confidently matched to the name
    homepage_url: str    # the URL actually fetched (post-redirect base)
    via: str             # "guess" | "search"
