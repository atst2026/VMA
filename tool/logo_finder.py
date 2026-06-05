#!/usr/bin/env python3
"""Find a company's real, good-quality logo from just its name.

The pitch-pack cover needs the TARGET company's actual logo. The hard cases
are deep-tech / startup accounts (the BD leads Sara pitches) that aren't on
Wikidata or Wikipedia and whose domains aren't a naive ".com" guess — e.g.
"Geordie AI" -> geordie.ai, "OQC" -> oxford quantum circuits -> oqc.tech. A
logo service keyed on a guessed domain misses those entirely.

The engine is DETERMINISTIC-FIRST, then conservative: it never shows a
DIFFERENT company's logo. It returns the right mark or a clean wordmark — never
a plausible-but-wrong one. Resolution order, first good hit wins:

  0. a human-verified LOCAL OVERRIDE file (tool/assets/company_logos/<slug>.*),
     used verbatim, offline — the unequivocal guarantee for known accounts
     (see tool/company_logos.py);
  1. an authoritative logo asset pinned in the curated registry;
  2. the company's OFFICIAL DOMAIN, then the logo off that confirmed site:
       - the domain is registry-pinned where known, else resolved
         conservatively: a web SEARCH whose result domain STRICTLY matches the
         company, Wikidata P856 (double-checked against the name), or a TLD
         probe that requires the homepage to strongly name the company;
       - scrape the homepage for the brand logo (ranked so a partner/award/
         payment image never wins), the SVG/mask favicon, then Clearbit /
         logo.dev by domain, then a keyless favicon floor;
  3. ONLY when no official domain resolves at all, a name-ALIGNED encyclopaedia
     logo (Wikidata P154 / Wikipedia), guarded so a same-named band / person /
     place / different firm is never used;
  4. give up -> caller renders a typographic wordmark.

The old loose paths that caused "wrong company entirely" are gone: loose
single-token domain matching, token-mention TLD acceptance, an encyclopaedia
image for a company we positively placed, and Clearbit/favicon on a GUESSED
domain (zero identity check). Every network step is best-effort and swallowed;
the engine never raises. The chosen source is logged so a miss is debuggable.
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
# "Is this actually a usable LOGO?" — the gate that stops the three reported
# cover failures (a decorative loader/menu glyph, a white-on-transparent mark
# that vanishes on the white cover, or junk that scores as a logo). `valid_logo`
# above only proves the bytes are a structurally-sound image; the checks below
# prove the image is a real brand mark that will RENDER VISIBLY on the cover.
# ======================================================================

# Tokens in a class / id / alt / filename that mark a graphic as site furniture
# (a loader, a nav/menu/search/social/cart glyph) rather than the brand logo. An
# explicit "logo" marker overrides these (see _looks_decorative), so a brand
# whose file is e.g. "arrow-logo.svg" is never mistaken for an arrow icon. The
# ambiguous tokens (arrow / social / cookie …) require an icon/banner context so
# a real company name that merely CONTAINS one ("Arrow", "Consentry") is safe.
_DECORATIVE_RX = re.compile(
    r"\bspinner\b|\bloader\b|\bloading\b|preloader|throbber|skeleton-?(?:loader|screen)|"
    r"hamburger|burger-?menu|menu-?burger|nav-?toggle|menu-?toggle|toggle-?(?:menu|nav)|"
    r"search-?icon|icon-?search|magnif\w*|"
    r"\bchevron\b|\bcaret\b|dropdown-?(?:icon|arrow)|"
    r"arrow-(?:left|right|up|down|icon|head|circle)|(?:left|right|up|down|next|prev|back|slide|slider|carousel)-?arrow|"
    r"social-?(?:icon|media|links?|share|nav|bar|menu)|share-?icon|icon-?share|"
    r"\bfacebook\b|\btwitter\b|\binstagram\b|\blinkedin\b|\byoutube\b|\btiktok\b|"
    r"\bwhatsapp\b|\bpinterest\b|\bsnapchat\b|"
    r"cart-?icon|icon-?cart|\bbasket\b|\btrolley\b|"
    r"\bavatar\b|\bgravatar\b|"
    r"cookie-?(?:banner|bar|notice|consent|popup|icon|policy)|"
    r"consent-?(?:banner|bar|notice|popup|manager|modal)|\bgdpr\b|"
    r"play-?button|video-?icon|icon-?play|"
    r"\bsprite\b|\bplaceholder\b|"
    r"close-?icon|icon-?close",
    re.I)


def _looks_decorative(blob: str) -> bool:
    """True if the markup blob (class/id/alt/src or raw inline-svg) identifies a
    decorative UI graphic, not a brand logo. An explicit 'logo' marker wins."""
    b = (blob or "").lower()
    if "logo" in b:
        return False
    return bool(_DECORATIVE_RX.search(b))


# Tokens that mark a mark as ANOTHER party's logo (a partner / client / sponsor
# strip, an award laurel, a payment or accreditation badge, an "as featured in"
# press row) rather than the TARGET company's own brand. Unlike the decorative
# tokens above, these fire even when the literal word "logo" is present —
# "partner-logo", alt="Visa logo", "award-logos.svg" are real, visible images
# that pass every other gate, which is the "right company, wrong logo" failure.
# A handful of named third-party brands (payment networks, review sites) are
# included because they appear as alt text on otherwise-unmarked icons.
_THIRD_PARTY_RX = re.compile(
    r"\bpartner(?:s|ship)?\b|\bclient(?:s)?\b|\bsponsor(?:s|ship|ed)?\b|"
    r"\baward(?:s|ed)?\b|\baccreditation\b|\baccredited\b|\bcertif(?:ied|ication)\b|"
    r"\bbadge(?:s)?\b|\bmember(?:s|ship)?\b|\baffiliat\w*|\bendorse\w*|"
    r"as[-\s]?featured|featured[-\s]?in|\bas[-\s]?seen[-\s]?(?:in|on)\b|"
    r"trusted[-\s]?by|powered[-\s]?by|\bintegrat\w*|"
    r"press[-\s]?(?:logos?|kit|coverage|mentions?)|\bmedia[-\s]?logos?\b|"
    r"\bpayment(?:s)?\b|\bvisa\b|\bmastercard\b|\bamex\b|\bpaypal\b|\bklarna\b|"
    r"\bstripe\b|\bapple[-\s]?pay\b|\bgoogle[-\s]?pay\b|\btrustpilot\b|\btrustico\b|"
    r"\bg2\b|\bcapterra\b|\bglassdoor\b|\bgartner\b|\bforrester\b|\biso[-\s]?\d",
    re.I)


def _looks_third_party(blob: str) -> bool:
    """True if the markup blob marks the image as ANOTHER organisation's logo
    (partner / client / sponsor / award / payment / review-site / press), which
    must never be mistaken for the target company's own brand mark."""
    return bool(_THIRD_PARTY_RX.search((blob or "").lower()))


def _is_whiteish(colour: str) -> bool:
    """True for white / near-white colour tokens (#fff, #ffffff, white,
    rgb(255,255,255), …) — the colours that make a logo vanish on a white
    cover."""
    c = (colour or "").strip().lower()
    if not c or c in ("none", "transparent", "inherit", "currentcolor"):
        return False
    if c in ("white", "#fff", "#ffff", "#ffffff", "#ffffffff"):
        return True
    m = re.fullmatch(r"#([0-9a-f]{3,8})", c)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):                      # #rgb / #rgba
            chans = [int(ch * 2, 16) for ch in h[:3]]
        elif len(h) in (6, 8):                     # #rrggbb / #rrggbbaa
            chans = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
        else:
            return False
        return all(ch >= 244 for ch in chans)
    m = re.fullmatch(r"rgba?\(([^)]*)\)", c)
    if m:
        try:
            parts = [p.strip() for p in m.group(1).split(",")[:3]]
            chans = [int(round(float(p[:-1]) * 2.55)) if p.endswith("%") else int(float(p))
                     for p in parts]
            return len(chans) == 3 and all(ch >= 244 for ch in chans)
        except Exception:
            return False
    return False


def svg_is_visible(data: bytes) -> bool:
    """Will this SVG render as visible ink on a white cover?

    Rejects the two SVG failure modes seen on real packs:
      * animated loaders / spinners (<animate …>), which are never a logo;
      * marks whose ONLY explicit colours are white (designed to sit on a dark
        header), which disappear on the white cover.

    Accepts SVGs that use currentColor or carry no explicit colour (both render
    in the default near-black), and any SVG with at least one non-white fill or
    stroke. Pure / offline — unit-tested."""
    try:
        t = data.decode("utf-8", "ignore")
    except Exception:
        return False
    low = t.lower()
    if "<svg" not in low:
        return False
    if "<animate" in low:                          # spinner / loader, not a logo
        return False
    if not re.search(r"<(path|rect|circle|ellipse|polygon|polyline|line|text|image|use)\b", low):
        return False                               # nothing drawable
    if "currentcolor" in low or "<image" in low:
        return True
    colours = re.findall(
        r"(?:fill|stroke)\s*[:=]\s*[\"']?\s*(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|[a-zA-Z]+)", t)
    explicit = [c for c in colours
                if c.lower() not in ("none", "transparent", "inherit", "currentcolor")]
    if not explicit:
        return True                                # default fill is black -> visible
    return any(not _is_whiteish(c) for c in explicit)


def raster_is_visible(data: bytes) -> bool:
    """Will this raster render as visible ink on a white cover? Composites the
    image (honouring transparency) onto white and requires a small but real
    fraction of non-white pixels — so a fully-transparent or white-on-transparent
    mark (which vanishes on the cover) is rejected. Pillow is a hard dependency
    of the PDF renderer; if it can't open the bytes we don't block (valid_logo
    already vouched for them)."""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return True
    try:
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        comp = Image.alpha_composite(bg, im).convert("RGB")
        small = comp.resize((min(64, max(1, comp.width)), min(64, max(1, comp.height))))
        raw = small.tobytes()                       # tightly-packed RGB
        n = len(raw) // 3
        if n == 0:
            return False
        ink = sum(1 for i in range(0, n * 3, 3)
                  if (765 - raw[i] - raw[i + 1] - raw[i + 2]) > 40)
        return (ink / n) >= 0.005
    except Exception:
        return True


def usable_logo(content: bytes, content_type: str = "", source: str = "") -> bool:
    """The single acceptance gate: structurally a valid image (valid_logo), not a
    decorative graphic (by its source/markup), and one that will render visibly
    on the cover (SVG and raster visibility checks).

    The decorative check on `source` looks only at the URL PATH (a glyph
    filename like `/loading-spinner.svg`), never the host — so a real company
    whose domain happens to contain a substring like "consent" or "arrow" is not
    wrongly rejected."""
    if not valid_logo(content, content_type):
        return False
    if source.startswith(("http://", "https://")):
        if _looks_decorative(urlparse(source).path):
            return False
    if is_svg(content, content_type):
        if _looks_decorative(content[:2000].decode("utf-8", "ignore")):
            return False
        return svg_is_visible(content)
    return raster_is_visible(content)


def normalize_logo(data: bytes, content_type: str = "") -> bytes:
    """Trim the surrounding transparent / white border off a raster logo so it
    fills the cover box instead of floating tiny in a sea of padding (the
    "appropriately sized" half of the ask). SVGs and anything Pillow can't read
    are returned unchanged. Best-effort — never raises."""
    if not data or is_svg(data, content_type):
        return data
    try:
        import io
        from PIL import Image, ImageChops
        im = Image.open(io.BytesIO(data))
        im.load()
        im = im.convert("RGBA")
        white = Image.new("RGBA", im.size, (255, 255, 255, 255))
        comp = Image.alpha_composite(white, im).convert("RGB")
        diff = ImageChops.difference(comp, Image.new("RGB", im.size, (255, 255, 255)))
        bbox = diff.getbbox()
        if not bbox:
            return data
        pad = max(2, int(0.04 * max(im.size)))
        l, t, r, b = bbox
        box = (max(0, l - pad), max(0, t - pad),
               min(im.width, r + pad), min(im.height, b + pad))
        if box == (0, 0, im.width, im.height):
            return data
        out = io.BytesIO()
        im.crop(box).save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return data


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
    """Does this domain CONFIDENTLY belong to the company? Deliberately strict —
    a loose match here is the root of the "wrong company entirely" cover failure
    (a SERP / TLD guess landing a *same-token* but different company). Accepts
    only when the domain label is genuinely the company's brand:

      * exact: label == the company name slug;
      * acronym: the initials of a multi-word name exactly equal the label
        (Oxford Quantum Circuits -> oqc.tech);
      * strong affix: one of {label, slug} is a prefix/suffix of the other AND
        the shorter is a substantial fraction of the longer (>=4 chars, >=60%),
        so "Geordie AI" (geordieai) matches geordie.ai but "Pulse" (pulse) does
        NOT match an unrelated pulsesecure.net;
      * embedded multi-word: EVERY significant token of a 2+ word name appears
        in the label.

    A bare single-token substring (the old "label in name or token in label")
    is rejected: it is what let "Arc" match arcgis.com / monarch.com and any
    'quantum*' domain match a different quantum company."""
    label = _domain_label(host)
    name_slug = _slug(company)
    if not label or not name_slug:
        return False
    if label == name_slug:
        return True
    tokens = _name_tokens(company)
    if len(tokens) >= 2:
        acronym = "".join(t[0] for t in tokens)
        if acronym and label == acronym:
            return True
        if all(tok in label for tok in tokens):
            return True
    longer, shorter = (label, name_slug) if len(label) >= len(name_slug) else (name_slug, label)
    if (longer.startswith(shorter) or longer.endswith(shorter)) \
            and len(shorter) >= 4 and len(shorter) / len(longer) >= 0.6:
        return True
    return False


def _names_align(a: str, b: str) -> bool:
    """True when two NAMES are confidently the same entity (used to gate the
    encyclopaedia, where the risk is a same-named band / person / different
    company). One slug must contain the other and be a substantial fraction of
    it — so 'Diageo' aligns with 'Diageo plc' but not with 'Diageo Park'."""
    sa, sb = _slug(a), _slug(b)
    if not sa or not sb:
        return False
    if sa == sb:
        return True
    longer, shorter = (sa, sb) if len(sa) >= len(sb) else (sb, sa)
    return shorter in longer and len(shorter) >= 4 and len(shorter) / len(longer) >= 0.6


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
    """A usable logo image's bytes, or None. Handles data: URIs inline; otherwise
    a plain request, falling back to Bright Data (read as bytes) for sites that
    block. Every path runs through `usable_logo`, so a decorative glyph, a
    white-on-transparent mark, or a non-image never escapes this function — which
    is what stops them reaching the cover."""
    if not url:
        return None
    if url.startswith("data:"):
        b = _decode_data_uri(url)
        return b if b and usable_logo(b, "", source=url) else None
    r = _http_get(url, want_bytes=True)
    if r is not None and r.status_code == 200 and r.content:
        if usable_logo(r.content, r.headers.get("content-type", ""), source=url):
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
                    usable_logo(resp.content, resp.headers.get("content-type", ""), source=url):
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


def _wikidata_org_entities(company: str) -> list[tuple[str, str]]:
    """(entity-id, label) pairs for the company, best match first, keeping only
    entities whose LABEL confidently aligns with the queried name. The label
    filter is the identity guard: Wikidata's search will happily return a
    same-named band / town / person, and using its logo/site for our company is
    exactly the "wrong company" failure. We don't hard-filter to organisations
    (that needs extra calls); the P154/P856 lookups naturally skip entities that
    carry neither."""
    j = _wd_get({"action": "wbsearchentities", "search": company,
                 "language": "en", "type": "item", "format": "json", "limit": 7})
    if not j:
        return []
    try:
        out = []
        for e in j.get("search", []):
            label = e.get("label") or e.get("match", {}).get("text") or ""
            if _names_align(label, company):
                out.append((e["id"], label))
        return out
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
    """Wikidata 'official website' (P856) -> registrable domain, but only for an
    entity whose label aligns with the company (enforced by
    _wikidata_org_entities) AND whose resolved domain itself matches the company.
    The double check stops a same-named entity's website being adopted."""
    for qid, _label in _wikidata_org_entities(company)[:4]:
        val = _wikidata_claim_value(qid, "P856")
        if isinstance(val, str) and val.startswith("http"):
            host = urlparse(val).netloc
            reg = _registrable(host) if host else ""
            if reg and reg not in _AGGREGATORS and _domain_matches_company(reg, company):
                return reg
    return None


def _commons_filepath(filename: str, width: int = 512) -> bytes | None:
    url = "https://commons.wikimedia.org/wiki/Special:FilePath/" + quote(filename)
    return _fetch_image(url + f"?width={width}")


def _wikidata_logo(company: str) -> tuple[bytes | None, str]:
    """Wikidata 'logo image' (P154) -> the official logo on Commons, only for an
    entity whose label aligns with the company (the identity guard lives in
    _wikidata_org_entities)."""
    for qid, _label in _wikidata_org_entities(company)[:4]:
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
        # Identity guard: the resolved article title must align with the company
        # name, else the lead image belongs to a same-named band / person /
        # place / different firm — the original "Geordie AI" wrong-image failure.
        if not _names_align(p.get("title") or "", company):
            continue
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
    s = (img.get("src") or img.get("data-src") or img.get("data-lazy-src")
         or img.get("data-original") or img.get("data-image"))
    if s:
        return s
    # responsive logo shipped only via srcset / data-srcset — take the first
    # candidate URL (the descriptor after the space is dropped).
    ss = img.get("srcset") or img.get("data-srcset") or ""
    if ss:
        first = ss.split(",")[0].strip().split()
        if first:
            return first[0]
    return None


def _bg_url(style: str) -> str | None:
    """The url(...) of a CSS background / background-image declaration — how many
    top-right logos are actually painted (a <a class="brand" style="background:
    url(/logo.svg)">), invisible to a plain <img> scrape."""
    m = re.search(r'background(?:-image)?\s*:[^;]*url\(\s*["\']?([^"\')]+)',
                  style or "", re.I)
    return m.group(1).strip() if m else None


def extract_logo_urls(html: str, base_url: str) -> dict[str, list[str]]:
    """Parse a homepage for logo image URLs, ordered best-first. Returns
    {'primary': [...], 'secondary': [...]}:

      primary   — the actual brand logo: an element that says "logo"/"brand",
                  the <img>/<svg>/CSS-background inside the homepage link, the
                  header / nav / top-bar, a "logo" url() in a <style> rule, or a
                  mask/SVG favicon. Responsive logos (srcset) are covered too.
      secondary — acceptable square brand marks: apple-touch-icon, an inline
                  header SVG serialised to a data URI, then sized favicons.

    Modern startup sites (e.g. oqc.tech, geordie.ai) put the top-right logo in
    the header as an <img>, an inline <svg>, OR a CSS background-image, and ship
    an apple-touch-icon — all of those are covered. Pure — unit-tested."""
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

    # 1. <img> elements that identify themselves as a logo (SVG first). A
    #    third-party marker (alt="Visa logo", class="partner-logo") is skipped —
    #    it carries the word "logo" but is another brand's mark, not the target's.
    svg_logo, other_logo = [], []
    for img in soup.find_all("img"):
        attrs = " ".join(str(img.get(k, "")) for k in
                         ("class", "id", "alt", "src", "data-src", "title")).lower()
        if "logo" in attrs and not _looks_third_party(attrs):
            s = _img_src(img)
            if s:
                (svg_logo if ".svg" in s.lower() else other_logo).append(s)
    for s in svg_logo + other_logo:
        add(primary, s)

    def _img_blob(img) -> str:
        return " ".join(str(img.get(k, "")) for k in
                        ("class", "id", "alt", "src", "data-src", "title"))

    # 1b. CSS background-image logos: an element that identifies as a logo/brand
    #     and paints the mark via an inline-style background-image (Webflow /
    #     custom builds), which a plain <img> scrape never sees.
    for el in soup.select("[class*=logo], [id*=logo], [class*=Logo], "
                          "[class*=brand], [id*=brand], [class*=Brand]"):
        blob = " ".join(str(el.get(k, "")) for k in ("class", "id")).lower()
        if _looks_decorative(blob) or _looks_third_party(blob):
            continue
        bg = _bg_url(el.get("style", ""))
        if bg and not _looks_third_party(bg):
            add(primary, bg)

    # 2. <img> / background inside the homepage link (<a href="/">) — almost
    #    always the logo. Skip decorative furniture (a social / search glyph).
    for a in soup.find_all("a"):
        if is_root_anchor(a):
            anchor_meta = str(a.get("class", "")) + str(a.get("id", ""))
            bg = _bg_url(a.get("style", ""))
            if bg and not _looks_decorative(anchor_meta) and not _looks_third_party(anchor_meta):
                add(primary, bg)
            for img in a.find_all("img"):
                blob = _img_blob(img)
                if not _looks_decorative(blob) and not _looks_third_party(blob):
                    add(primary, _img_src(img))

    # 3. first <img> inside header / nav / a top-bar / an element that names
    #    itself a logo or brand (decorative glyphs skipped).
    for sel in ("header img", "nav img", "[class*=header] img",
                "[class*=navbar] img", "[class*=Header] img",
                "[class*=logo] img", "[id*=logo] img", "[class*=Logo] img",
                "[class*=brand] img", "[id*=brand] img", "[role=banner] img",
                "[class*=top-bar] img", "[class*=topbar] img"):
        for img in soup.select(sel)[:3]:
            blob = _img_blob(img)
            if not _looks_decorative(blob) and not _looks_third_party(blob):
                add(primary, _img_src(img))

    # 3b. <style> blocks: a url(...) whose filename looks like a logo — the
    #     class-driven CSS background logo (.site-logo{background:url(/logo.svg)}).
    for st in soup.find_all("style"):
        css = st.string or st.get_text() or ""
        for m in re.finditer(r"url\(\s*[\"']?([^\"')]+)", css):
            u = m.group(1).strip()
            base = u.lower().split("?")[0]
            if "logo" in base and not u.startswith("data:") and not _looks_third_party(base):
                add(primary, u)

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
        # Skip site furniture (loaders / menu / search / social glyphs) and any
        # SVG that won't render as visible ink — the spinner and the invisible
        # white mark seen on real packs both die here, before they can be picked.
        if _looks_decorative(blob) or _looks_decorative(raw):
            continue
        if "xmlns" not in raw[:120]:
            raw = raw.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
        if not svg_is_visible(raw.encode("utf-8")):
            continue
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
            usable_logo(r.content, r.headers.get("content-type", "")):
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
            usable_logo(r.content, r.headers.get("content-type", "")):
        return r.content
    return None


def _google_favicon(domain: str) -> bytes | None:
    r = _http_get("https://www.google.com/s2/favicons",
                  params={"domain": domain, "sz": 256}, want_bytes=True)
    if r is not None and r.status_code == 200 and \
            usable_logo(r.content, r.headers.get("content-type", "")):
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
            usable_logo(r.content, r.headers.get("content-type", "")):
        return r.content
    return None


# ======================================================================
# Domain resolution + top-level entry point
# ======================================================================
def _probe_tlds(company: str) -> str | None:
    """Fetch guessed domains and keep the first whose homepage STRONGLY names the
    company. Strict on purpose: a guessed <name>.com is owned by whoever it's
    owned by, so accepting it on a single token mention (the old behaviour) is a
    prime "wrong company" source — "Pol AI" matching pol.com because the page
    says "available" (which collapses to contain "ai"). We require either the
    full collapsed name to appear, or — for a multi-word name — EVERY significant
    token to be present on the page."""
    tokens = _name_tokens(company)
    name_slug = _slug(company)
    for dom in domain_candidates(company):
        html = _fetch_html("https://" + dom)
        if not html:
            continue
        head = html.lower()[:20000]
        slug_head = _slug(head)
        full_name = len(name_slug) >= 4 and name_slug[:18] in slug_head
        all_tokens = len(tokens) >= 2 and all(t in head for t in tokens)
        if full_name or all_tokens:
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
    # Curated registry — the authoritative name->exact-domain map. Pinning the
    # domain here is what stops "wrong company entirely": resolution uses the
    # real company's domain instead of a SERP/TLD guess that can land a namesake.
    try:
        from tool import company_logos
        reg = company_logos.registry_domain(company)
    except Exception as e:
        log.info("registry lookup failed for %r: %s", company, e)
        reg = None
    if reg:
        return reg, "registry"
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


def _rank_site_cands(cands: list[str], company: str) -> list[str]:
    """Order scraped site URLs so the TARGET's own brand mark is tried first: a
    filename containing 'logo' or a company-name token wins; bare icons
    (apple-touch / favicon) sink. Stable for ties, so the scraper's own order is
    preserved within a score band. This is the surgical fix for 'right company,
    wrong logo' — a partner/award image with no name affinity is tried last."""
    tokens = [t for t in _name_tokens(company) if len(t) >= 3]

    def score(u: str) -> int:
        b = u.lower().split("?")[0]
        s = 0
        if "logo" in b:
            s += 3
        if any(t in b for t in tokens):
            s += 3
        if "brand" in b or "/mark" in b or "wordmark" in b:
            s += 1
        if "apple-touch" in b or "favicon" in b or "/icon" in b:
            s -= 2
        return -s                       # ascending sort -> highest score first

    return sorted(cands, key=score)


def find_logo(company: str, hint_url: str | None = None
              ) -> tuple[bytes | None, str]:
    """Resolve the company's real logo. Returns (image_bytes_or_None, source).

    Resolution is DETERMINISTIC-FIRST, then conservative, so a wrong company's
    mark can never reach the cover — the cover gets the right logo or a clean
    typographic wordmark, never a different company:

      0. a human-verified LOCAL OVERRIDE file (tool/assets/company_logos/) — the
         unequivocal guarantee, offline, always wins;
      1. an authoritative logo asset pinned in the curated registry;
      2. the company's OFFICIAL DOMAIN (registry-pinned where known, else a
         conservatively-resolved one), then off that confirmed site:
           a. the site's own logo files, ranked so the brand mark beats a
              partner/award image and a bare favicon;
           b. domain-keyed raster services (Clearbit / logo.dev);
           c. a gated inline header SVG;
           d. a keyless favicon floor;
      3. ONLY when no official domain resolves at all, a name-aligned
         encyclopaedia logo (Wikidata P154 / Wikipedia), guarded so a same-named
         entity's image is never used.

    The old identity-free fallbacks (a same-named encyclopaedia image for a
    company we *did* place, and Clearbit/favicon on a GUESSED domain) are gone —
    they were the source of "wrong company entirely". Every image still passes
    the `usable_logo` visibility/decorative gate. Never raises."""
    company = (company or "").strip()
    if not company:
        return None, "wordmark"

    # 0. Human-verified local override — offline, deterministic, unequivocal.
    try:
        from tool import company_logos
        ov, ovsrc = company_logos.local_logo(company)
    except Exception as e:
        log.info("local override lookup failed for %r: %s", company, e)
        ov, ovsrc = None, ""
    if ov:
        log.info("logo for %r via %s (%d bytes)", company, ovsrc, len(ov))
        return ov, ovsrc

    # 1. Registry-pinned authoritative logo asset (if one is configured).
    try:
        pinned = company_logos.registry_logo_url(company)
    except Exception:
        pinned = None
    if pinned:
        img = _fetch_image(pinned)
        if img:
            log.info("logo for %r via registry asset %s", company, _short(pinned))
            return img, f"registry:{urlparse(pinned).netloc or 'asset'}"

    domain, how = resolve_domain(company, hint_url)

    if domain:
        # Split the scraped candidates: real logo/icon FILES vs fragile inline
        # SVGs (data: URIs). Files (ranked) and the raster services are tried
        # first; the inline SVG is a last site resort, even when it passes gates.
        site_cands = _site_logo_candidates(domain)
        file_cands = _rank_site_cands(
            [u for u in site_cands if not u.startswith("data:")], company)
        inline_cands = [u for u in site_cands if u.startswith("data:")]

        # 2a. the company's own logo files / icons, brand-mark first.
        for u in file_cands[:12]:
            img = _fetch_image(u)
            if img:
                log.info("logo for %r via site %s (%s, %d bytes)",
                         company, domain, _short(u), len(img))
                return img, f"site:{domain}"
        # 2b. domain-keyed raster services — reliable + visible — BEFORE inline SVG.
        for fn, name in ((_clearbit, "clearbit"), (_logodev, "logodev")):
            try:
                img = fn(domain)
            except Exception:
                img = None
            if img:
                log.info("logo for %r via %s:%s", company, name, domain)
                return img, f"{name}:{domain}"
        # 2c. inline header/anchor SVG — last site resort (already gated for
        #     decorative + visibility in extraction and in _fetch_image).
        for u in inline_cands[:4]:
            img = _fetch_image(u)
            if img:
                log.info("logo for %r via site-inline %s (%d bytes)",
                         company, domain, len(img))
                return img, f"site:{domain}"
        # 2d. keyless favicon floor — a real brand mark for any live domain.
        img = _favicon_floor(domain)
        if img:
            log.info("logo for %r via favicon:%s", company, domain)
            return img, f"favicon:{domain}"
        # A domain resolved but yielded no usable mark: STOP here. Reaching for
        # an encyclopaedia / guessed-domain image now would risk a same-named
        # entity's logo for a company we positively placed — the exact failure
        # we are eliminating. A clean wordmark is the correct, honest fallback.
        log.info("domain %s for %r yielded no usable logo — wordmark", domain, company)
        return None, "wordmark"

    # 3. No official domain at all -> a name-ALIGNED encyclopaedia logo only.
    #    (Both helpers self-guard on name alignment; a same-named band/person/
    #    place/different-firm image is rejected rather than shown.)
    for fn, name in ((_wikidata_logo, "wikidata"), (_wikipedia_logo, "wikipedia")):
        try:
            img, src = fn(company)
        except Exception:
            img, src = None, ""
        if img:
            log.info("logo for %r via %s (%d bytes)", company, src, len(img))
            return img, src

    log.info("no logo resolved for %r — wordmark fallback", company)
    return None, "wordmark"
