#!/usr/bin/env python3
"""Find a company's real, good-quality logo from just its name.

The pitch-pack cover needs the TARGET company's actual logo. The hard cases
are deep-tech / startup accounts (the BD leads Sara pitches) that aren't on
Wikidata or Wikipedia and whose domains aren't a naive ".com" guess — e.g.
"Geordie AI" -> geordie.ai, "OQC" -> oxford quantum circuits -> oqc.tech. A
logo service keyed on a guessed domain misses those entirely.

So the engine works the way a person would: find the company's official
website, then take the logo off it. Resolution order, first good hit wins:

  1. resolve the official DOMAIN
       - a small curated map (marquee names)
       - a web SEARCH (Bright Data unblocked Google, else DuckDuckGo/Bing),
         taking the first organic result whose domain matches the company
       - Wikidata "official website" (P856) for entities that are organisations
       - probe a broad set of TLDs (.com/.ai/.io/.tech/.co/.co.uk/...) and
         keep the one whose homepage actually names the company
  2. pull the logo FROM that site
       - scrape the homepage for an explicit logo element (SVG preferred), the
         SVG/mask favicon, then Clearbit / logo.dev by domain
  3. authoritative encyclopaedia logo (Wikidata P154 -> Wikipedia infobox) for
     companies that have one
  4. site icons (apple-touch-icon / favicon) as an acceptable brand mark
  5. give up -> caller renders a typographic wordmark

Every network step is best-effort and swallowed; the engine never raises.
The chosen source is logged so a miss is debuggable from the run log.
"""
from __future__ import annotations

import base64
import logging
import re
from urllib.parse import quote, quote_plus, unquote, urljoin, urlparse

log = logging.getLogger("logo_finder")

# Wikimedia (and most sites) reject an empty / bot UA. Identify honestly.
_UA = ("Mozilla/5.0 (compatible; VMA-PitchPack/1.0; +https://www.vmagroup.com) "
       "executive-search-proposal-generator")
_TIMEOUT = 9

# Domains that are never the company's own site — search aggregators, social,
# press, data brokers. A SERP hit on one of these is skipped.
_AGGREGATORS = {
    "linkedin.com", "crunchbase.com", "wikipedia.org", "wikidata.org",
    "wikimedia.org", "bloomberg.com", "reuters.com", "ft.com", "forbes.com",
    "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com",
    "glassdoor.com", "glassdoor.co.uk", "indeed.com", "pitchbook.com",
    "dnb.com", "opencorporates.com", "find-and-update.company-information."
    "service.gov.uk", "gov.uk", "trustpilot.com", "yelp.com", "amazon.com",
    "medium.com", "github.com", "apple.com", "play.google.com", "google.com",
    "bing.com", "duckduckgo.com", "tracxn.com", "owler.com", "zoominfo.com",
    "rocketreach.co", "theorg.com", "signalhire.com", "techcrunch.com",
    "sifted.eu", "businesswire.com", "prnewswire.com", "globenewswire.com",
    "yahoo.com", "msn.com", "wsj.com", "cnbc.com", "thetimes.co.uk",
    "uktech.news", "eu-startups.com", "tech.eu", "cbinsights.com",
}

# TLDs to probe, most-likely first. Deep-tech startups skew to .ai/.io/.tech.
_TLDS = (".com", ".ai", ".io", ".tech", ".co", ".co.uk", ".net", ".org",
         ".app", ".eu", ".dev", ".xyz", ".quantum")

_SUFFIX_RX = re.compile(
    r"\b(plc|ltd|limited|llp|inc|incorporated|holdings|group|the|company|co|"
    r"corp|corporation|technologies|technology|labs|ai|systems)\b",
    re.IGNORECASE)

# Curated, high-confidence domains for marquee names (cheap to extend). The
# discovery below covers everything else; this just makes the obvious names
# instant and bullet-proof.
KNOWN_DOMAINS: dict[str, str] = {
    "belron": "belron.com", "diageo": "diageo.com", "unilever": "unilever.com",
    "haleon": "haleon.com", "reckitt": "reckitt.com", "nestle": "nestle.com",
    "tesco": "tesco.com", "sainsbury's": "sainsburys.co.uk",
    "barclays": "barclays.com", "hsbc": "hsbc.com", "natwest": "natwest.com",
    "aviva": "aviva.com", "bp": "bp.com", "shell": "shell.com",
    "gsk": "gsk.com", "astrazeneca": "astrazeneca.com", "vodafone": "vodafone.com",
    "bt": "bt.com", "centrica": "centrica.com", "severn trent": "severntrent.co.uk",
    "rolls-royce": "rolls-royce.com", "burberry": "burberry.com",
    "heathrow": "heathrow.com", "deloitte": "deloitte.com", "ey": "ey.com",
    "kpmg": "kpmg.com", "pwc": "pwc.com", "arup": "arup.com",
    # deep-tech accounts seen on the BD radar
    "oqc": "oqc.tech", "oxford quantum circuits": "oqc.tech",
    "geordie": "geordie.ai", "geordie ai": "geordie.ai",
    "quantinuum": "quantinuum.com", "riverlane": "riverlane.com",
    "wayve": "wayve.ai", "synthesia": "synthesia.io", "graphcore": "graphcore.ai",
    "darktrace": "darktrace.com", "monzo": "monzo.com", "revolut": "revolut.com",
}


# ======================================================================
# Image validation + embedding
# ======================================================================
def is_svg(content: bytes, content_type: str = "") -> bool:
    if "svg" in (content_type or "").lower():
        return True
    head = content[:300].lstrip().lower()
    return head.startswith(b"<svg") or (
        head.startswith(b"<?xml") and b"<svg" in content[:800].lower())


def valid_logo(content: bytes, content_type: str = "") -> bool:
    """A real logo image: SVG (validated by structure) or a raster of sensible
    size. Rejects empty bodies, HTML error pages, tracking pixels and 16px
    favicons."""
    if not content:
        return False
    if is_svg(content, content_type):
        low = content.lower()
        return b"<svg" in low and b"</svg>" in low
    if len(content) < 300:
        return False
    ct = (content_type or "").lower()
    if ct and not ct.startswith("image"):
        return False
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(content))
        im.load()
        w, h = im.size
        return w >= 32 and h >= 16 and max(w, h) >= 48
    except Exception:
        return content[:8].startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8"))


def img_data_uri(data: bytes) -> str:
    """Embed raw image bytes as a data URI with the correct MIME sniffed
    (WeasyPrint renders PNG/JPEG/GIF and SVG)."""
    if data[:8].startswith(b"\x89PNG"):
        mime = "image/png"
    elif data[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif data[:4] == b"GIF8":
        mime = "image/gif"
    elif is_svg(data):
        mime = "image/svg+xml"
    else:
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


# ======================================================================
# Name / domain helpers (pure)
# ======================================================================
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _name_tokens(company: str) -> list[str]:
    """Significant lowercase tokens of a company name (suffixes/stopwords
    stripped), used to confirm a candidate domain belongs to the company."""
    core = _SUFFIX_RX.sub(" ", company or "")
    toks = [t for t in re.split(r"[^a-z0-9]+", core.lower()) if len(t) >= 2]
    # keep a short all-caps acronym like "oqc" even though len-rules above pass it
    if not toks and company:
        toks = [_slug(company)]
    return toks


def _registrable(host: str) -> str:
    """Best-effort registrable domain (handles .co.uk / .com.au two-level TLDs
    without a full PSL)."""
    host = (host or "").lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    two_level = {"co.uk", "org.uk", "gov.uk", "ac.uk", "com.au", "co.nz",
                 "co.za", "com.br", "co.in"}
    if ".".join(parts[-2:]) in two_level:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _domain_label(host: str) -> str:
    """The brandable label of a registrable domain: 'oqc' for 'oqc.tech',
    'geordie' for 'geordie.ai', 'severntrent' for 'severntrent.co.uk'."""
    reg = _registrable(host)
    return _slug(reg.split(".")[0])


def domain_candidates(company: str) -> list[str]:
    """Guessed domains across a broad TLD set, most-likely first. Used both as
    probe targets and as inputs to domain-keyed logo services."""
    out: list[str] = []
    key = (company or "").strip().lower()
    if key in KNOWN_DOMAINS:
        out.append(KNOWN_DOMAINS[key])
    core = _SUFFIX_RX.sub(" ", company or "").strip()
    bases = [b for b in dict.fromkeys([_slug(core), _slug(company)]) if b]
    for base in bases:
        for tld in _TLDS:
            out.append(base + tld)
    return list(dict.fromkeys(out))


def _domain_matches_company(host: str, company: str) -> bool:
    """Does this domain plausibly belong to the company? True when the domain
    label and the company name overlap — the label inside the name slug, a name
    token inside the label, or the company's initials matching the label (so a
    full name like 'Oxford Quantum Circuits' matches the acronym domain
    'oqc.tech')."""
    label = _domain_label(host)
    if not label:
        return False
    name_slug = _slug(company)
    if label in name_slug or name_slug in label:
        return True
    tokens = _name_tokens(company)
    if any(tok in label or label in tok for tok in tokens):
        return True
    # initials of a multi-word name -> acronym domain (Oxford Quantum Circuits
    # -> oqc). Kept strict (exact label) to avoid spurious matches.
    if len(tokens) >= 2:
        acronym = "".join(t[0] for t in tokens)
        if acronym and label == acronym:
            return True
    return False


# ======================================================================
# HTTP (network — thin, swallowed)
# ======================================================================
def _http_get(url: str, params: dict | None = None, want_bytes: bool = False):
    try:
        import requests
        r = requests.get(url, params=params, timeout=_TIMEOUT,
                        headers={"User-Agent": _UA}, allow_redirects=True)
        return r
    except Exception as e:
        log.info("GET %s failed: %s", url[:80], e)
        return None


def _bd_html(url: str) -> str | None:
    """Fetch a URL through Bright Data's Web Unlocker (bypasses bot walls /
    captchas on Google and corporate sites). No-op when BD isn't configured."""
    try:
        from tool.linkedin_resolver import _bright_data_fetch
        return _bright_data_fetch(url)
    except Exception as e:
        log.info("Bright Data unavailable: %s", e)
        return None


def _fetch_html(url: str) -> str | None:
    """HTML for a SERP or homepage — Bright Data first (reliable in CI), then a
    plain request."""
    html = _bd_html(url)
    if html:
        return html
    r = _http_get(url)
    if r is not None and r.status_code == 200 and r.text:
        return r.text
    return None


def _fetch_image(url: str) -> bytes | None:
    """Logo image bytes. Handles data: URIs inline; otherwise a plain request,
    falling back to Bright Data (read as bytes) for sites that block."""
    if not url:
        return None
    if url.startswith("data:"):
        return _decode_data_uri(url)
    r = _http_get(url, want_bytes=True)
    if r is not None and r.status_code == 200 and r.content:
        if valid_logo(r.content, r.headers.get("content-type", "")):
            return r.content
    # blocked? try Bright Data, reading raw bytes
    try:
        import requests
        from tool.linkedin_resolver import (BRIGHT_DATA_KEY, BD_ZONE,
                                            BD_ENDPOINT)
        if BRIGHT_DATA_KEY and BD_ZONE:
            resp = requests.post(BD_ENDPOINT,
                                 json={"zone": BD_ZONE, "url": url, "format": "raw"},
                                 headers={"Authorization": f"Bearer {BRIGHT_DATA_KEY}",
                                          "Content-Type": "application/json"},
                                 timeout=30)
            if resp.status_code == 200 and resp.content and \
                    valid_logo(resp.content, resp.headers.get("content-type", "")):
                return resp.content
    except Exception as e:
        log.info("image BD fetch %s failed: %s", url[:60], e)
    return None


def _decode_data_uri(uri: str) -> bytes | None:
    try:
        head, _, data = uri.partition(",")
        if ";base64" in head:
            return base64.b64decode(data)
        return unquote(data).encode("utf-8")  # e.g. inline svg
    except Exception:
        return None


# ======================================================================
# SERP -> official domain (pure parser + network driver)
# ======================================================================
def extract_result_urls(html: str) -> list[str]:
    """Pull candidate result URLs out of a SERP page, covering DuckDuckGo HTML
    (uddg redirect), Bing (direct hrefs) and Google (/url?q= redirect). Pure —
    unit-tested against fixtures."""
    if not html:
        return []
    urls: list[str] = []
    # DuckDuckGo: href="...uddg=<encoded>&..."
    for m in re.finditer(r'uddg=([^&"\']+)', html):
        urls.append(unquote(m.group(1)))
    # Google: href="/url?q=<encoded>&..."
    for m in re.finditer(r'/url\?q=(https?[^&"\']+)', html):
        urls.append(unquote(m.group(1)))
    # Direct external links (Bing b_algo, generic): href="https://..."
    for m in re.finditer(r'href="(https?://[^"]+)"', html):
        urls.append(m.group(1))
    # de-dupe, preserve order
    seen, out = set(), []
    for u in urls:
        u = u.strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def pick_company_domain(result_urls: list[str], company: str) -> str | None:
    """First result URL whose registrable domain isn't an aggregator and
    plausibly belongs to the company."""
    for u in result_urls:
        try:
            host = urlparse(u).netloc.lower()
        except Exception:
            continue
        if not host:
            continue
        reg = _registrable(host)
        if reg in _AGGREGATORS or any(reg == a or reg.endswith("." + a)
                                      for a in _AGGREGATORS):
            continue
        if _domain_matches_company(host, company):
            return reg
    return None


def _serp_domain(company: str) -> str | None:
    """Search the web for the company and return its official domain."""
    q = f"{company} official website"
    engines = [
        f"https://www.google.com/search?q={quote_plus(q)}&num=10&hl=en",
        f"https://html.duckduckgo.com/html/?q={quote_plus(q)}",
        f"https://www.bing.com/search?q={quote_plus(q)}&count=10",
    ]
    for url in engines:
        html = _fetch_html(url)
        if not html:
            continue
        dom = pick_company_domain(extract_result_urls(html), company)
        if dom:
            log.info("SERP domain for %r: %s (via %s)", company, dom,
                     urlparse(url).netloc)
            return dom
    return None


# ======================================================================
# Wikidata / Wikipedia
# ======================================================================
def _wd_get(params: dict) -> dict | None:
    r = _http_get("https://www.wikidata.org/w/api.php", params=params)
    if r is not None and r.status_code == 200:
        try:
            return r.json()
        except Exception:
            return None
    return None


def _wikidata_org_entities(company: str) -> list[str]:
    """Entity IDs for the company, best match first. We don't hard-filter to
    organisations here (that needs extra calls); the P154/P856 lookups below
    naturally skip people/places that carry neither."""
    j = _wd_get({"action": "wbsearchentities", "search": company,
                 "language": "en", "type": "item", "format": "json", "limit": 5})
    if not j:
        return []
    try:
        return [e["id"] for e in j.get("search", [])]
    except Exception:
        return []


def _wikidata_claim_value(qid: str, prop: str):
    j = _wd_get({"action": "wbgetclaims", "entity": qid, "property": prop,
                 "format": "json"})
    if not j:
        return None
    try:
        claim = j["claims"][prop][0]["mainsnak"]["datavalue"]["value"]
        return claim
    except Exception:
        return None


def wikidata_official_site(company: str) -> str | None:
    """Wikidata 'official website' (P856) -> registrable domain."""
    for qid in _wikidata_org_entities(company)[:4]:
        val = _wikidata_claim_value(qid, "P856")
        if isinstance(val, str) and val.startswith("http"):
            host = urlparse(val).netloc
            if host:
                return _registrable(host)
    return None


def _commons_filepath(filename: str, width: int = 512) -> bytes | None:
    url = "https://commons.wikimedia.org/wiki/Special:FilePath/" + quote(filename)
    return _fetch_image(url + f"?width={width}")


def _wikidata_logo(company: str) -> tuple[bytes | None, str]:
    """Wikidata 'logo image' (P154) -> the official logo on Commons."""
    for qid in _wikidata_org_entities(company)[:4]:
        fname = _wikidata_claim_value(qid, "P154")
        if isinstance(fname, str) and fname:
            img = _commons_filepath(fname, 512)
            if img:
                return img, f"wikidata:{qid}"
    return None, ""


def _wikipedia_logo(company: str) -> tuple[bytes | None, str]:
    r = _http_get("https://en.wikipedia.org/w/api.php", params={
        "action": "query", "format": "json", "prop": "pageimages",
        "piprop": "original", "titles": company, "redirects": 1})
    if r is None or r.status_code != 200:
        return None, ""
    try:
        pages = r.json().get("query", {}).get("pages", {})
    except Exception:
        return None, ""
    for p in pages.values():
        src = (p.get("original") or {}).get("source")
        if src:
            img = _fetch_image(src)
            if img:
                return img, "wikipedia"
    return None, ""


# ======================================================================
# Homepage logo scraping
# ======================================================================
def _rel(link) -> str:
    rel = link.get("rel")
    if isinstance(rel, (list, tuple)):
        return " ".join(rel).lower()
    return str(rel or "").lower()


def _sz(link) -> int:
    m = re.match(r"(\d+)", link.get("sizes") or "")
    return int(m.group(1)) if m else 0


def _img_src(img):
    return (img.get("src") or img.get("data-src") or img.get("data-lazy-src")
            or img.get("data-original") or img.get("data-image"))


def extract_logo_urls(html: str, base_url: str) -> dict[str, list[str]]:
    """Parse a homepage for logo image URLs, ordered best-first. Returns
    {'primary': [...], 'secondary': [...]}:

      primary   — the actual brand logo: an element that says "logo", the
                  <img>/<svg> inside the homepage link or header/nav, or a
                  mask/SVG favicon.
      secondary — acceptable square brand marks: apple-touch-icon, an inline
                  header SVG serialised to a data URI, then sized favicons.

    Modern startup sites (e.g. oqc.tech, geordie.ai) put the logo in the header
    link as an <img> or inline <svg> and ship an apple-touch-icon, so all of
    those are covered. Pure — unit-tested against fixtures."""
    primary: list[str] = []
    secondary: list[str] = []

    def add(lst, href):
        if not href:
            return
        href = href.strip()
        u = href if href.startswith("data:") else urljoin(base_url, href)
        if u and u not in primary and u not in secondary:
            lst.append(u)

    try:
        from bs4 import BeautifulSoup
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return _extract_logo_urls_regex(html, base_url)

    root = urlparse(base_url).netloc.lower()

    def is_root_anchor(a) -> bool:
        href = (a.get("href") or "").strip()
        if href in ("/", "#", "/#", "./"):
            return True
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            return False
        try:
            p = urlparse(urljoin(base_url, href))
            return p.netloc.lower() == root and p.path in ("", "/")
        except Exception:
            return False

    # 1. <img> elements that identify themselves as a logo (SVG first).
    svg_logo, other_logo = [], []
    for img in soup.find_all("img"):
        attrs = " ".join(str(img.get(k, "")) for k in
                         ("class", "id", "alt", "src", "data-src", "title")).lower()
        if "logo" in attrs:
            s = _img_src(img)
            if s:
                (svg_logo if ".svg" in s.lower() else other_logo).append(s)
    for s in svg_logo + other_logo:
        add(primary, s)

    # 2. <img> inside the homepage link (<a href="/">) — almost always the logo.
    for a in soup.find_all("a"):
        if is_root_anchor(a):
            for img in a.find_all("img"):
                add(primary, _img_src(img))

    # 3. first <img> inside header / nav / a top-bar.
    for sel in ("header img", "nav img", "[class*=header] img",
                "[class*=navbar] img", "[class*=Header] img"):
        for img in soup.select(sel)[:3]:
            add(primary, _img_src(img))

    # 4. mask-icon / SVG favicon files (monochrome brand mark).
    for link in soup.find_all("link"):
        rel, href = _rel(link), link.get("href")
        if not href:
            continue
        typ = (link.get("type") or "").lower()
        base = href.lower().split("?")[0]
        if "mask-icon" in rel or ("icon" in rel and ("svg" in typ or base.endswith(".svg"))):
            add(primary, href)

    # 5. apple-touch-icon (largest) — a reliable, self-contained square mark.
    apple = [(_sz(l), l.get("href")) for l in soup.find_all("link")
             if l.get("href") and "apple-touch-icon" in _rel(l)]
    for _, href in sorted(apple, key=lambda t: -t[0]):
        add(secondary, href)

    # 6. inline <svg> that is (or sits inside) the homepage link / header — the
    #    logo on script-built sites. Serialised to a data URI for embedding.
    svgs = []
    for a in soup.find_all("a"):
        if is_root_anchor(a):
            svgs += a.find_all("svg")
    svgs += soup.select("header svg, nav svg")
    for svg in svgs[:3]:
        blob = " ".join(str(svg.get(k, "")) for k in
                        ("class", "id", "aria-label", "role")).lower()
        try:
            raw = str(svg)
        except Exception:
            continue
        if "</svg>" not in raw:
            continue
        # take it if it looks logo-ish OR it's the header/anchor svg (most are)
        if "icon" in blob and "logo" not in blob:
            continue
        if "xmlns" not in raw[:120]:
            raw = raw.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
        add(secondary, "data:image/svg+xml;utf8," + quote(raw))

    # 7. sized <link rel=icon> (largest first).
    icons = [(_sz(l), l.get("href")) for l in soup.find_all("link")
             if l.get("href") and "icon" in _rel(l)
             and "apple" not in _rel(l) and "mask" not in _rel(l)]
    for _, href in sorted(icons, key=lambda t: -t[0]):
        add(secondary, href)

    return {"primary": primary, "secondary": secondary}


def _extract_logo_urls_regex(html: str, base_url: str) -> dict[str, list[str]]:
    """bs4-free fallback parser."""
    primary, secondary = [], []
    for m in re.finditer(r'<img[^>]+>', html, re.I):
        tag = m.group(0)
        if "logo" in tag.lower():
            sm = re.search(r'(?:data-src|src)\s*=\s*["\']([^"\']+)', tag, re.I)
            if sm:
                primary.append(urljoin(base_url, sm.group(1)))
    for m in re.finditer(r'<link[^>]+>', html, re.I):
        tag = m.group(0).lower()
        hm = re.search(r'href\s*=\s*["\']([^"\']+)', m.group(0), re.I)
        if not hm:
            continue
        href = urljoin(base_url, hm.group(1))
        if "mask-icon" in tag or ".svg" in tag:
            primary.append(href)
        elif "apple-touch-icon" in tag or "icon" in tag:
            secondary.append(href)
    return {"primary": list(dict.fromkeys(primary)),
            "secondary": list(dict.fromkeys(secondary))}


# ======================================================================
# Domain-keyed logo services
# ======================================================================
def _clearbit(domain: str) -> bytes | None:
    r = _http_get(f"https://logo.clearbit.com/{domain}",
                  params={"size": 512, "format": "png"}, want_bytes=True)
    if r is not None and r.status_code == 200 and \
            valid_logo(r.content, r.headers.get("content-type", "")):
        return r.content
    return None


def _logodev(domain: str) -> bytes | None:
    import os
    token = os.environ.get("LOGO_DEV_TOKEN", "").strip()
    if not token:
        return None
    r = _http_get(f"https://img.logo.dev/{domain}",
                  params={"token": token, "size": 512, "format": "png"},
                  want_bytes=True)
    if r is not None and r.status_code == 200 and \
            valid_logo(r.content, r.headers.get("content-type", "")):
        return r.content
    return None


def _google_favicon(domain: str) -> bytes | None:
    r = _http_get("https://www.google.com/s2/favicons",
                  params={"domain": domain, "sz": 256}, want_bytes=True)
    if r is not None and r.status_code == 200 and \
            valid_logo(r.content, r.headers.get("content-type", "")):
        return r.content
    return None


def _favicon_floor(domain: str) -> bytes | None:
    """Keyless favicon services — a guaranteed real brand mark for ANY live
    domain (used as the floor before giving up to a wordmark). Google's service
    upsizes to 256px; DuckDuckGo's is a clean fallback."""
    for fn in (_google_favicon, _duckduckgo_favicon):
        try:
            b = fn(domain)
        except Exception:
            b = None
        if b:
            return b
    return None


def _duckduckgo_favicon(domain: str) -> bytes | None:
    r = _http_get(f"https://icons.duckduckgo.com/ip3/{domain}.ico", want_bytes=True)
    if r is not None and r.status_code == 200 and \
            valid_logo(r.content, r.headers.get("content-type", "")):
        return r.content
    return None


# ======================================================================
# Domain resolution + top-level entry point
# ======================================================================
def _probe_tlds(company: str) -> str | None:
    """Fetch guessed domains and keep the first whose homepage actually names
    the company (so a squatter on <name>.com doesn't win)."""
    tokens = _name_tokens(company)
    for dom in domain_candidates(company):
        html = _fetch_html("https://" + dom)
        if not html:
            continue
        low = html.lower()
        # the page should mention the company (a token, or the name slug in a
        # collapsed form) somewhere in its first chunk
        head = low[:20000]
        if any(t in head for t in tokens) or _slug(company)[:12] in _slug(head):
            log.info("TLD probe matched %s for %r", dom, company)
            return dom
    return None


def resolve_domain(company: str, hint_url: str | None = None
                   ) -> tuple[str | None, str]:
    """The company's official domain. (domain, how)."""
    if hint_url:
        host = urlparse(hint_url if "//" in hint_url else "//" + hint_url).netloc
        reg = _registrable(host)
        if reg and reg not in _AGGREGATORS:
            return reg, "hint"
    key = (company or "").strip().lower()
    if key in KNOWN_DOMAINS:
        return KNOWN_DOMAINS[key], "known"
    for fn, how in ((_serp_domain, "serp"),
                    (wikidata_official_site, "wikidata_p856"),
                    (_probe_tlds, "tld_probe")):
        try:
            dom = fn(company)
        except Exception as e:
            log.info("domain resolver %s failed for %r: %s", how, company, e)
            dom = None
        if dom:
            return dom, how
    return None, ""


def _short(u: str) -> str:
    return (u[:70] + "…") if len(u) > 71 else u


def _site_logo_candidates(domain: str) -> list[str]:
    """All logo/icon URLs scraped from the homepage, ordered best-first
    (header logo -> apple-touch-icon -> inline svg -> favicons)."""
    html = _fetch_html("https://" + domain) or _fetch_html("http://" + domain)
    if not html:
        return []
    try:
        c = extract_logo_urls(html, "https://" + domain)
    except Exception as e:
        log.info("logo scrape parse failed for %s: %s", domain, e)
        return []
    return c.get("primary", []) + c.get("secondary", [])


def find_logo(company: str, hint_url: str | None = None
              ) -> tuple[bytes | None, str]:
    """Resolve the company's real logo. Returns (image_bytes_or_None, source).

    Once the official domain is known, EVERY logo/icon the site itself ships is
    tried (header logo, apple-touch-icon, favicon) plus a keyless favicon floor,
    BEFORE any name-based encyclopaedia lookup. That guarantees a real brand
    mark for a resolved domain and removes the failure modes seen on the BD
    radar: a wrong, same-named Wikipedia image, or no logo at all.

    Never raises — the caller falls back to a typographic wordmark."""
    company = (company or "").strip()
    if not company:
        return None, "wordmark"

    domain, how = resolve_domain(company, hint_url)

    if domain:
        # 1. the company's own website assets, best-first.
        for u in _site_logo_candidates(domain)[:12]:
            img = _fetch_image(u)
            if img:
                log.info("logo for %r via site %s (%s, %d bytes)",
                         company, domain, _short(u), len(img))
                return img, f"site:{domain}"
        # 2. domain-keyed logo services.
        for fn, name in ((_clearbit, "clearbit"), (_logodev, "logodev")):
            try:
                img = fn(domain)
            except Exception:
                img = None
            if img:
                log.info("logo for %r via %s:%s", company, name, domain)
                return img, f"{name}:{domain}"
        # 3. keyless favicon floor — a real brand mark for any live domain.
        img = _favicon_floor(domain)
        if img:
            log.info("logo for %r via favicon:%s", company, domain)
            return img, f"favicon:{domain}"

    # 4. encyclopaedia logo — only when no usable domain resolved (so we never
    #    risk a same-named entity's image for a company we DID place).
    for fn, name in ((_wikidata_logo, "wikidata"), (_wikipedia_logo, "wikipedia")):
        try:
            img, src = fn(company)
        except Exception:
            img, src = None, ""
        if img:
            log.info("logo for %r via %s (%d bytes)", company, src, len(img))
            return img, src

    # 5. no domain at all -> Clearbit / favicon on guessed candidates.
    if not domain:
        for d in domain_candidates(company)[:5]:
            for fn, name in ((_clearbit, "clearbit"), (_favicon_floor, "favicon")):
                try:
                    img = fn(d)
                except Exception:
                    img = None
                if img:
                    log.info("logo for %r via %s:%s (guessed)", company, name, d)
                    return img, f"{name}:{d}"

    log.info("no logo resolved for %r — wordmark fallback", company)
    return None, "wordmark"
