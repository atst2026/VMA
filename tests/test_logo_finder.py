"""Tests for tool/logo_finder — the company-logo resolution engine.

These cover the logic that makes deep-tech / startup accounts resolve (the
reported misses: "Geordie AI" -> geordie.ai, "OQC" -> oqc.tech), entirely
offline: domain candidates across modern TLDs, SERP result parsing + company
matching, homepage logo scraping, image validation, and the find_logo()
control flow (driven with monkeypatched network so it's deterministic).
"""
import base64
import io
import os
import sys
from urllib.parse import quote

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from tool import logo_finder as lf


def _png(draw=None, color=(255, 255, 255, 0), size=(120, 60)) -> bytes:
    """A real (>300-byte) PNG for the raster-visibility tests."""
    Image = pytest.importorskip("PIL.Image", reason="Pillow needed")
    from PIL import ImageDraw
    im = Image.new("RGBA", size, color)
    if draw:
        draw(ImageDraw.Draw(im))
    b = io.BytesIO()
    im.save(b, format="PNG")
    return b.getvalue()


# ---- image validation + embedding -------------------------------------

def test_valid_logo_raster_and_junk():
    assert not lf.valid_logo(b"", "image/png")
    assert not lf.valid_logo(b"<html>404</html>", "text/html")
    assert not lf.valid_logo(b"x" * 100, "image/png")          # tiny raster
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=")
    # 1x1 PNG: decodes but is below the minimum logo size -> rejected
    assert not lf.valid_logo(png, "image/png")


def test_valid_logo_accepts_svg_by_structure():
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80">'
           b'<rect width="200" height="80"/></svg>')
    assert lf.valid_logo(svg, "image/svg+xml")
    assert lf.valid_logo(svg, "")                  # sniffed without a content-type
    assert not lf.valid_logo(b"<svg> no close", "image/svg+xml")


def test_img_data_uri_sniffs_mime():
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=")
    assert lf.img_data_uri(png).startswith("data:image/png;base64,")
    assert lf.img_data_uri(b"\xff\xd8\xff\xe0junk").startswith("data:image/jpeg;base64,")
    assert lf.img_data_uri(b"<svg></svg>").startswith("data:image/svg+xml;base64,")


# ---- domain candidates + matching -------------------------------------

def test_domain_candidates_span_modern_tlds():
    cands = lf.domain_candidates("Geordie AI")
    # the old resolver only tried .com/.co.uk; deep-tech needs .ai/.io/.tech
    assert "geordie.com" in cands
    assert "geordie.ai" in cands
    assert any(c.endswith(".io") for c in cands)
    assert any(c.endswith(".tech") for c in cands)


def test_known_domains_cover_reported_startups():
    assert lf.KNOWN_DOMAINS["geordie ai"] == "geordie.ai"
    assert lf.KNOWN_DOMAINS["oqc"] == "oqc.tech"
    assert lf.KNOWN_DOMAINS["oxford quantum circuits"] == "oqc.tech"


def test_registrable_and_label():
    assert lf._registrable("www.oqc.tech") == "oqc.tech"
    assert lf._registrable("careers.severntrent.co.uk") == "severntrent.co.uk"
    assert lf._domain_label("geordie.ai") == "geordie"
    assert lf._domain_label("oqc.tech") == "oqc"


def test_domain_matches_company():
    assert lf._domain_matches_company("geordie.ai", "Geordie AI")
    assert lf._domain_matches_company("oqc.tech", "OQC")
    # full name -> acronym domain (Oxford Quantum Circuits -> oqc.tech)
    assert lf._domain_matches_company("www.oqc.tech", "Oxford Quantum Circuits")
    # an unrelated domain must NOT match
    assert not lf._domain_matches_company("randomsaas.com", "Geordie AI")
    assert not lf._domain_matches_company("notthem.io", "Oxford Quantum Circuits")


# ---- SERP parsing + company-domain selection --------------------------

def test_extract_result_urls_handles_engines():
    ddg = ('<a class="result__a" '
           'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgeordie.ai%2F&rut=x">Geordie AI</a>'
           '<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.linkedin.com%2Fcompany%2Fgeordie">li</a>')
    urls = lf.extract_result_urls(ddg)
    assert "https://geordie.ai/" in urls
    assert any("linkedin.com" in u for u in urls)

    google = '<a href="/url?q=https://oqc.tech/&sa=U&ved=abc">Oxford Quantum Circuits</a>'
    assert "https://oqc.tech/" in lf.extract_result_urls(google)

    bing = '<li class="b_algo"><h2><a href="https://oqc.tech/">OQC</a></h2></li>'
    assert "https://oqc.tech/" in lf.extract_result_urls(bing)


def test_pick_company_domain_skips_aggregators():
    results = [
        "https://www.linkedin.com/company/geordie-ai",
        "https://www.crunchbase.com/organization/geordie",
        "https://geordie.ai/about",
    ]
    assert lf.pick_company_domain(results, "Geordie AI") == "geordie.ai"

    oqc_results = [
        "https://www.bing.com/search?q=oqc",
        "https://uktech.news/quantum/oqc-raises",   # press, an aggregator
        "https://oqc.tech/",
    ]
    assert lf.pick_company_domain(oqc_results, "OQC") == "oqc.tech"


def test_pick_company_domain_none_when_unrelated():
    assert lf.pick_company_domain(
        ["https://www.linkedin.com/x", "https://example.org/y"], "Geordie AI") is None


# ---- homepage logo scraping -------------------------------------------

_HOMEPAGE = """
<html><head>
  <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
  <link rel="icon" type="image/svg+xml" href="/favicon.svg">
  <link rel="icon" sizes="32x32" href="/favicon-32.png">
</head><body>
  <header><a href="/"><img class="site-logo" src="/assets/logo.svg" alt="Geordie AI logo"></a></header>
  <p>Welcome</p>
</body></html>
"""


def test_extract_logo_urls_prefers_explicit_logo_svg():
    out = lf.extract_logo_urls(_HOMEPAGE, "https://geordie.ai")
    primary = out["primary"]
    assert primary, "should find an explicit logo"
    # the <img class=site-logo src=logo.svg> resolves absolute and leads
    assert primary[0] == "https://geordie.ai/assets/logo.svg"
    # the SVG favicon is also a primary brand mark
    assert "https://geordie.ai/favicon.svg" in primary
    # apple-touch-icon is a secondary fallback, resolved absolute
    assert "https://geordie.ai/static/apple-touch-icon.png" in out["secondary"]


_OQC_HOME = """
<html><head>
  <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
  <link rel="icon" href="/favicon.ico">
</head><body>
  <header><a href="/" class="brand">
    <svg class="oqc-mark" viewBox="0 0 100 40"><path d="M0 0h10v10H0z"/></svg>
  </a></header>
</body></html>
"""


def test_extract_logo_urls_header_inline_svg_and_apple_touch():
    # The oqc.tech shape: logo is an inline <svg> in the homepage-link, plus an
    # apple-touch-icon. No <img> logo, so the icons must carry it.
    out = lf.extract_logo_urls(_OQC_HOME, "https://oqc.tech")
    sec = out["secondary"]
    assert "https://oqc.tech/apple-touch-icon.png" in sec
    assert any(u.startswith("data:image/svg+xml") for u in sec), "inline header svg captured"


def test_find_logo_gets_oqc_logo_from_site_assets(monkeypatch):
    monkeypatch.setattr(lf, "resolve_domain",
                        lambda c, hint_url=None: ("oqc.tech", "known"))
    monkeypatch.setattr(lf, "_fetch_html", lambda url: _OQC_HOME)
    png = b"\x89PNG\r\n\x1a\n" + b"\x07" * 600
    # apple-touch-icon returns real bytes; nothing else needed
    monkeypatch.setattr(lf, "_fetch_image",
                        lambda url: png if url.endswith("/apple-touch-icon.png") else None)
    data, src = lf.find_logo("OQC")
    assert data == png and src == "site:oqc.tech"


def test_extract_logo_urls_regex_fallback():
    out = lf._extract_logo_urls_regex(
        '<img class="logo" src="/brand/logo.png">', "https://x.io")
    assert "https://x.io/brand/logo.png" in out["primary"]


# ---- find_logo control flow (monkeypatched network) -------------------

def test_find_logo_empty_is_wordmark():
    assert lf.find_logo("") == (None, "wordmark")


def test_find_logo_scrapes_site_logo(monkeypatch):
    monkeypatch.setattr(lf, "resolve_domain",
                        lambda c, hint_url=None: ("geordie.ai", "known"))
    monkeypatch.setattr(lf, "_fetch_html", lambda url: _HOMEPAGE)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg></svg>'
    # only the explicit logo URL returns bytes
    monkeypatch.setattr(lf, "_fetch_image",
                        lambda url: svg if url.endswith("/assets/logo.svg") else None)
    data, src = lf.find_logo("Geordie AI")
    assert data == svg
    assert src == "site:geordie.ai"


def test_find_logo_falls_back_to_clearbit_then_favicon_then_wordmark(monkeypatch):
    monkeypatch.setattr(lf, "resolve_domain",
                        lambda c, hint_url=None: ("oqc.tech", "known"))
    monkeypatch.setattr(lf, "_fetch_html", lambda url: "<html></html>")  # no logo on page
    monkeypatch.setattr(lf, "_fetch_image", lambda url: None)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 400
    monkeypatch.setattr(lf, "_clearbit", lambda d: png)
    data, src = lf.find_logo("OQC")
    assert data == png and src == "clearbit:oqc.tech"

    # clearbit misses -> the keyless favicon floor still yields a brand mark
    monkeypatch.setattr(lf, "_clearbit", lambda d: None)
    monkeypatch.setattr(lf, "_logodev", lambda d: None)
    fav = b"\x89PNG\r\n\x1a\n" + b"\x01" * 500
    monkeypatch.setattr(lf, "_favicon_floor", lambda d: fav)
    data, src = lf.find_logo("OQC")
    assert data == fav and src == "favicon:oqc.tech"

    # everything misses (incl. the favicon floor) and no encyclopaedia -> wordmark
    monkeypatch.setattr(lf, "_favicon_floor", lambda d: None)
    monkeypatch.setattr(lf, "_wikidata_logo", lambda c: (None, ""))
    monkeypatch.setattr(lf, "_wikipedia_logo", lambda c: (None, ""))
    assert lf.find_logo("OQC") == (None, "wordmark")


def test_find_logo_never_uses_encyclopaedia_for_resolved_domain(monkeypatch):
    # The Geordie failure mode: a same-named Wikipedia image must NOT be used
    # when we have the company's real domain. With a domain resolved, the
    # favicon floor wins and the (stubbed) encyclopaedia is never consulted.
    monkeypatch.setattr(lf, "resolve_domain",
                        lambda c, hint_url=None: ("geordie.ai", "known"))
    monkeypatch.setattr(lf, "_site_logo_candidates", lambda d, c="": [])
    monkeypatch.setattr(lf, "_clearbit", lambda d: None)
    monkeypatch.setattr(lf, "_logodev", lambda d: None)
    fav = b"\x89PNG\r\n\x1a\n" + b"\x02" * 500
    monkeypatch.setattr(lf, "_favicon_floor", lambda d: fav)
    wrong = b"\x89PNG\r\n\x1a\n" + b"\xff" * 500   # a same-named Wikipedia image

    def _boom(c):
        raise AssertionError("encyclopaedia must not be consulted for a resolved domain")
    monkeypatch.setattr(lf, "_wikidata_logo", _boom)
    monkeypatch.setattr(lf, "_wikipedia_logo", _boom)
    data, src = lf.find_logo("Geordie AI")
    assert data == fav and src == "favicon:geordie.ai"
    assert data != wrong


def test_resolve_domain_uses_known_map_for_reported_cases():
    # No network needed: the two reported accounts are in the curated map, so
    # they resolve instantly and correctly.
    assert lf.resolve_domain("Geordie AI") == ("geordie.ai", "known")
    assert lf.resolve_domain("OQC") == ("oqc.tech", "known")
    assert lf.resolve_domain("Oxford Quantum Circuits") == ("oqc.tech", "known")


# ======================================================================
# Logo VISIBILITY + decorative gates — the three reported cover failures:
#   (1) a decorative loader/spinner picked as the "logo",
#   (2) a white-on-transparent mark that vanishes on the white cover,
#   (3) junk that scores as a logo.
# Every check below is pure/offline.
# ======================================================================

def test_is_whiteish():
    for c in ("#fff", "#ffffff", "white", "rgb(255,255,255)", "#fefefe",
              "rgb(100%,100%,100%)"):
        assert lf._is_whiteish(c), c
    for c in ("#24486f", "black", "#000", "none", "transparent",
              "currentColor", "rgb(20,30,40)", ""):
        assert not lf._is_whiteish(c), c


def test_looks_decorative():
    for blob in ("loading-spinner", "nav-toggle hamburger", "icon-search",
                 "social facebook", "menu-toggle", "preloader", "icon-close",
                 "slider-arrow", "arrow-right", "cookie-banner"):
        assert lf._looks_decorative(blob), blob
    # an explicit "logo" marker always overrides an icon-ish token
    for blob in ("arrow-logo", "site-logo brand", "company logo search",
                 "acme-mark", "header__brand"):
        assert not lf._looks_decorative(blob), blob
    # real company-name substrings must NOT trip the filter (no icon context)
    for blob in ("arrow", "consentry", "socialchain", "loadingdock",
                 "cartrust", "carter"):
        assert not lf._looks_decorative(blob), blob


def test_svg_is_visible_rejects_invisible_and_decorative():
    white = (b'<svg xmlns="http://www.w3.org/2000/svg">'
             b'<rect fill="#ffffff" width="10" height="10"/></svg>')
    assert not lf.svg_is_visible(white)            # white-on-white -> invisible
    white_named = (b'<svg xmlns="http://www.w3.org/2000/svg">'
                   b'<path fill="white" d="M0 0h9v9z"/></svg>')
    assert not lf.svg_is_visible(white_named)
    spinner = (b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="5">'
               b'<animateTransform attributeName="transform"/></circle></svg>')
    assert not lf.svg_is_visible(spinner)          # animated loader, never a logo
    empty = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    assert not lf.svg_is_visible(empty)            # nothing drawable
    # visible cases: explicit colour, default (black) fill, currentColor
    assert lf.svg_is_visible(
        b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="#24486f" d="M0 0h9v9z"/></svg>')
    assert lf.svg_is_visible(
        b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h9v9z"/></svg>')
    assert lf.svg_is_visible(
        b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M0 0h9z"/></svg>')


def test_raster_is_visible():
    pytest.importorskip("PIL")
    assert not lf.raster_is_visible(_png())                          # transparent
    assert not lf.raster_is_visible(_png(color=(255, 255, 255, 255)))  # solid white
    # a white mark on a transparent background also vanishes on the cover
    white_mark = _png(lambda d: d.rectangle((10, 20, 110, 40),
                                            fill=(255, 255, 255, 255)))
    assert not lf.raster_is_visible(white_mark)
    real = _png(lambda d: d.rectangle((10, 10, 110, 50), fill=(30, 60, 110, 255)))
    assert lf.raster_is_visible(real)


def test_usable_logo_combines_gates():
    navy_svg = (b'<svg xmlns="http://www.w3.org/2000/svg">'
                b'<path fill="#24486f" d="M0 0h9v9z"/></svg>')
    assert lf.usable_logo(navy_svg, "image/svg+xml")
    white_svg = (b'<svg xmlns="http://www.w3.org/2000/svg">'
                 b'<rect fill="#fff" width="9" height="9"/></svg>')
    assert not lf.usable_logo(white_svg, "image/svg+xml")
    # a decorative source FILENAME (path) is rejected even when the bytes are fine
    assert not lf.usable_logo(navy_svg, "image/svg+xml",
                              source="https://x.io/loading-spinner.svg")


def test_usable_logo_decorative_check_ignores_host():
    # The decorative filter must look at the URL path, never the host — a real
    # company whose domain contains a substring like "loading"/"consent"/"arrow"
    # (e.g. a logo at consentry.com) must NOT be rejected.
    navy_svg = (b'<svg xmlns="http://www.w3.org/2000/svg">'
                b'<path fill="#24486f" d="M0 0h9v9z"/></svg>')
    assert lf.usable_logo(navy_svg, "image/svg+xml",
                          source="https://consentry.com/assets/brand.svg")
    assert lf.usable_logo(navy_svg, "image/svg+xml",
                          source="https://arrowglobal.com/logo.svg")


def test_normalize_logo_trims_padding_and_passes_svg_through():
    pytest.importorskip("PIL")
    from PIL import Image
    padded = _png(lambda d: d.rectangle((90, 40, 150, 80), fill=(20, 20, 20, 255)),
                  size=(240, 120))
    out = lf.normalize_logo(padded, "image/png")
    assert Image.open(io.BytesIO(out)).size < (240, 120)   # padding trimmed away
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
    assert lf.normalize_logo(svg, "image/svg+xml") == svg  # svg returned unchanged
    assert lf.normalize_logo(b"", "") == b""               # empty unchanged


# ---- extraction skips decorative / invisible candidates ----------------

def test_extract_logo_urls_skips_decorative_and_invisible_inline_svg():
    html = """
    <html><body><header><a href="/">
      <svg class="loading-spinner"><circle r="5"><animateTransform/></circle></svg>
      <svg class="brand-mark"><rect fill="#ffffff" width="9" height="9"/></svg>
    </a></header></body></html>"""
    out = lf.extract_logo_urls(html, "https://x.io")
    # neither the animated spinner nor the white-only mark becomes a candidate
    assert not any(u.startswith("data:image/svg+xml")
                   for u in out["primary"] + out["secondary"])


def test_extract_logo_urls_skips_decorative_img():
    html = ('<header><a href="/">'
            '<img class="social-icon" src="/fb.svg">'
            '<img class="site-logo" src="/logo.svg"></a></header>')
    out = lf.extract_logo_urls(html, "https://x.io")
    assert "https://x.io/logo.svg" in out["primary"]
    assert "https://x.io/fb.svg" not in out["primary"]


# ---- the top-right-logo coverage gap: img / CSS-bg / <style> / srcset --------

def test_extract_logo_urls_top_right_nav_img():
    # a logo placed top-right in the nav (Framer/Webflow startup-site shape)
    html = ('<nav class="nav"><div class="nav-right">'
            '<a href="/" class="brand"><img src="/images/geordie-logo.svg" alt="Geordie">'
            '</a></div></nav>')
    out = lf.extract_logo_urls(html, "https://geordie.ai")
    assert "https://geordie.ai/images/geordie-logo.svg" in out["primary"]


def test_extract_logo_urls_css_background_logo():
    # the logo painted as a CSS background-image on a branded anchor (no <img>)
    html = ('<header><a href="/" class="navbar-brand" '
            'style="background-image:url(\'/assets/logo.svg\');width:160px"></a></header>')
    out = lf.extract_logo_urls(html, "https://x.io")
    assert "https://x.io/assets/logo.svg" in out["primary"]


def test_extract_logo_urls_style_block_logo():
    # a class-driven CSS logo: .site-logo{background:url(/brand-logo.png)}
    html = ('<head><style>.site-logo{background:#fff url(/static/brand-logo.png) no-repeat}'
            '</style></head><body><div class="site-logo"></div></body>')
    out = lf.extract_logo_urls(html, "https://x.io")
    assert "https://x.io/static/brand-logo.png" in out["primary"]


def test_extract_logo_urls_srcset_logo():
    # a responsive logo shipped only via srcset
    html = '<header><img class="logo" srcset="/logo@1x.png 1x, /logo@2x.png 2x"></header>'
    out = lf.extract_logo_urls(html, "https://x.io")
    assert "https://x.io/logo@1x.png" in out["primary"]


def test_fetch_image_validates_data_uris():
    # the white/animated inline SVG must NOT escape _fetch_image (the hole that
    # let an invisible mark and a spinner reach the cover).
    white = ('<svg xmlns="http://www.w3.org/2000/svg">'
             '<rect fill="#fff" width="9" height="9"/></svg>')
    assert lf._fetch_image("data:image/svg+xml;utf8," + quote(white)) is None
    navy = ('<svg xmlns="http://www.w3.org/2000/svg">'
            '<path fill="#24486f" d="M0 0h9v9z"/></svg>')
    assert lf._fetch_image("data:image/svg+xml;utf8," + quote(navy)) is not None


# ---- find_logo laddering: a real mark always beats junk ----------------

def test_find_logo_inline_svg_loses_to_raster_service(monkeypatch):
    # An inline header SVG (where the spinner / white mark live) must NEVER beat a
    # reliable raster service. No usable site file -> Clearbit wins, inline ignored.
    monkeypatch.setattr(lf, "resolve_domain", lambda c, hint_url=None: ("acme.com", "known"))
    monkeypatch.setattr(lf, "_site_logo_candidates",
                        lambda d, c="": ["data:image/svg+xml;utf8,<svg></svg>"])
    png = b"\x89PNG\r\n\x1a\n" + b"\x05" * 600
    monkeypatch.setattr(lf, "_clearbit", lambda d: png)
    data, src = lf.find_logo("Acme")
    assert src == "clearbit:acme.com" and data == png


def test_find_logo_prefers_usable_site_file(monkeypatch):
    monkeypatch.setattr(lf, "resolve_domain", lambda c, hint_url=None: ("acme.com", "known"))
    monkeypatch.setattr(lf, "_site_logo_candidates",
                        lambda d, c="": ["https://acme.com/apple-touch-icon.png"])
    png = b"\x89PNG\r\n\x1a\n" + b"\x06" * 600
    monkeypatch.setattr(lf, "_fetch_image",
                        lambda u: png if u.endswith("apple-touch-icon.png") else None)
    monkeypatch.setattr(lf, "_clearbit", lambda d: b"\x89PNG\r\n\x1a\n" + b"\x09" * 600)
    data, src = lf.find_logo("Acme")
    assert src == "site:acme.com" and data == png


def test_find_logo_unusable_site_file_falls_through_to_service(monkeypatch):
    # A white/invisible site logo file (_fetch_image returns None for it) must not
    # strand the cover — resolution continues to the next rung.
    monkeypatch.setattr(lf, "resolve_domain", lambda c, hint_url=None: ("acme.com", "known"))
    monkeypatch.setattr(lf, "_site_logo_candidates", lambda d, c="": ["https://acme.com/logo.svg"])
    monkeypatch.setattr(lf, "_fetch_image", lambda u: None)   # rejected by the gate
    png = b"\x89PNG\r\n\x1a\n" + b"\x07" * 600
    monkeypatch.setattr(lf, "_clearbit", lambda d: png)
    data, src = lf.find_logo("Acme")
    assert src == "clearbit:acme.com" and data == png


def test_find_logo_junk_only_falls_to_wordmark(monkeypatch):
    # When every rung yields only junk, the result is the wordmark sentinel — so
    # the cover prints the company name, never a blank space (the PDF-2 failure).
    monkeypatch.setattr(lf, "resolve_domain", lambda c, hint_url=None: ("acme.com", "known"))
    monkeypatch.setattr(lf, "_site_logo_candidates",
                        lambda d, c="": ["data:image/svg+xml;utf8,<svg></svg>"])
    monkeypatch.setattr(lf, "_fetch_image", lambda u: None)
    monkeypatch.setattr(lf, "_clearbit", lambda d: None)
    monkeypatch.setattr(lf, "_logodev", lambda d: None)
    monkeypatch.setattr(lf, "_favicon_floor", lambda d: None)
    monkeypatch.setattr(lf, "_wikidata_logo", lambda c: (None, ""))
    monkeypatch.setattr(lf, "_wikipedia_logo", lambda c: (None, ""))
    assert lf.find_logo("Acme") == (None, "wordmark")


# ======================================================================
# The unequivocal fix: deterministic-first resolution + a conservative live
# fallback that prefers a clean wordmark over a WRONG company's logo. These
# pin the behaviour that closes the three reported failure modes for good.
# ======================================================================

# ---- (a) "wrong company entirely": strict identity, no loose/guessed paths --

def test_domain_matches_company_is_strict_about_namesakes():
    # the loose single-token / short-substring matches that picked a DIFFERENT
    # company are now rejected …
    assert not lf._domain_matches_company("arcgis.com", "Arc")
    assert not lf._domain_matches_company("monarch.com", "Arc")
    assert not lf._domain_matches_company("pulsesecure.net", "Pulse")
    assert not lf._domain_matches_company("quantumcomputinginc.com", "Quantum Motion")
    # … while the genuine matches still resolve
    assert lf._domain_matches_company("geordie.ai", "Geordie AI")
    assert lf._domain_matches_company("oqc.tech", "Oxford Quantum Circuits")
    assert lf._domain_matches_company("quantummotion.tech", "Quantum Motion")
    assert lf._domain_matches_company("severntrent.co.uk", "Severn Trent")


def test_resolve_domain_uses_registry_not_serp(monkeypatch):
    # a registry-pinned account resolves to its EXACT domain with no web search,
    # so a SERP can never substitute a same-named company.
    def _no_serp(c):
        raise AssertionError("SERP must not run for a registry-pinned company")
    monkeypatch.setattr(lf, "_serp_domain", _no_serp)
    assert lf.resolve_domain("Hilton Hotels") == ("hilton.com", "registry")
    assert lf.resolve_domain("GKN Automotive") == ("gknautomotive.com", "registry")


def test_probe_tlds_requires_strong_name_match(monkeypatch):
    # a guessed <name>.com whose page merely contains a stray token is rejected
    weak = "<html><head><title>Welcome</title></head><body>available email main</body></html>"
    monkeypatch.setattr(lf, "_fetch_html", lambda url: weak)
    assert lf._probe_tlds("Pol AI") is None
    # but a page that actually names the company is accepted
    strong = "<html><body><h1>Pol AI</h1><p>we build robots</p></body></html>"
    monkeypatch.setattr(lf, "_fetch_html", lambda url: strong)
    assert lf._probe_tlds("Pol AI") in lf.domain_candidates("Pol AI")


def test_find_logo_local_override_wins_offline(monkeypatch, tmp_path):
    # a human-dropped file is used verbatim and SHORT-CIRCUITS all resolution —
    # the unequivocal guarantee.
    from tool import company_logos
    monkeypatch.setattr(company_logos, "OVERRIDE_DIR", tmp_path)
    data = b"\x89PNG\r\n\x1a\n" + b"\x33" * 300
    (tmp_path / "acmerobotics.png").write_bytes(data)

    def _boom(*a, **k):
        raise AssertionError("resolution must not run when an override exists")
    monkeypatch.setattr(lf, "resolve_domain", _boom)
    got, src = lf.find_logo("Acme Robotics")
    assert got == data and src == "local:acmerobotics.png"


def test_find_logo_no_domain_never_guesses_a_service(monkeypatch):
    # no official domain -> the old guessed-domain Clearbit/favicon (zero
    # identity check) is GONE; a name-aligned encyclopaedia is the only option,
    # else a clean wordmark. A wrong company's mark can't sneak in.
    monkeypatch.setattr(lf, "resolve_domain", lambda c, hint_url=None: (None, ""))

    def _boom(*a, **k):
        raise AssertionError("guessed-domain logo services must not run")
    monkeypatch.setattr(lf, "_clearbit", _boom)
    monkeypatch.setattr(lf, "_favicon_floor", _boom)
    monkeypatch.setattr(lf, "_logodev", _boom)
    monkeypatch.setattr(lf, "_wikidata_logo", lambda c: (None, ""))
    monkeypatch.setattr(lf, "_wikipedia_logo", lambda c: (None, ""))
    assert lf.find_logo("Totally Unknown Startup Co") == (None, "wordmark")


def test_find_logo_placed_domain_no_logo_is_wordmark_never_encyclopaedia(monkeypatch):
    # Once we've POSITIVELY placed a domain, a same-named encyclopaedia image is
    # never reached for — the cover degrades to a wordmark, not a wrong logo.
    monkeypatch.setattr(lf, "resolve_domain", lambda c, hint_url=None: ("acme.com", "registry"))
    monkeypatch.setattr(lf, "_site_logo_candidates", lambda d, c="": [])
    monkeypatch.setattr(lf, "_clearbit", lambda d: None)
    monkeypatch.setattr(lf, "_logodev", lambda d: None)
    monkeypatch.setattr(lf, "_favicon_floor", lambda d: None)

    def _boom(c):
        raise AssertionError("encyclopaedia must never run once a domain is placed")
    monkeypatch.setattr(lf, "_wikidata_logo", _boom)
    monkeypatch.setattr(lf, "_wikipedia_logo", _boom)
    assert lf.find_logo("Acme") == (None, "wordmark")


def test_wikipedia_logo_rejects_misaligned_title(monkeypatch):
    # a same-named non-company page (band / person) must not donate its image
    class _Resp:
        status_code = 200
        def json(self):
            return {"query": {"pages": {"1": {
                "title": "Geordie (musician)",
                "original": {"source": "https://x/img.png"}}}}}
    monkeypatch.setattr(lf, "_http_get", lambda *a, **k: _Resp())

    def _no_fetch(u):
        raise AssertionError("must not fetch a misaligned page's image")
    monkeypatch.setattr(lf, "_fetch_image", _no_fetch)
    assert lf._wikipedia_logo("Geordie AI") == (None, "")


def test_wikipedia_logo_accepts_aligned_title(monkeypatch):
    class _Resp:
        status_code = 200
        def json(self):
            return {"query": {"pages": {"1": {
                "title": "Geordie AI",
                "original": {"source": "https://x/img.png"}}}}}
    monkeypatch.setattr(lf, "_http_get", lambda *a, **k: _Resp())
    png = b"\x89PNG\r\n\x1a\n" + b"\x01" * 300
    monkeypatch.setattr(lf, "_fetch_image", lambda u: png)
    assert lf._wikipedia_logo("Geordie AI") == (png, "wikipedia")


# ---- (b) "right company, wrong logo": skip third-party marks, rank brand-first

def test_extract_logo_urls_skips_third_party_partner_logo():
    # a partner / payment badge carries the word "logo" but is ANOTHER brand;
    # it must never be chosen over the company's own mark.
    html = ('<header><a href="/">'
            '<img class="partner-logo" alt="Visa logo" src="/visa.svg">'
            '<img class="site-logo" alt="Acme logo" src="/logo.svg">'
            '</a></header>')
    out = lf.extract_logo_urls(html, "https://acme.com")
    everything = out["primary"] + out["secondary"]
    assert "https://acme.com/logo.svg" in out["primary"]
    assert "https://acme.com/visa.svg" not in everything


def test_looks_third_party():
    for blob in ("partner-logo", "client logos", "award-winning", "payment-logos",
                 "Visa logo", "trusted-by", "as-featured-in", "sponsor strip",
                 "iso-27001", "trustpilot rating"):
        assert lf._looks_third_party(blob), blob
    for blob in ("site-logo", "navbar-brand", "acme logo", "header__logo",
                 "company-mark", "brand"):
        assert not lf._looks_third_party(blob), blob


def test_rank_site_cands_brand_beats_partner_and_favicon():
    cands = ["https://x.com/apple-touch-icon.png",
             "https://x.com/img/partner/visa.png",
             "https://x.com/assets/acme-logo.svg"]
    ranked = lf._rank_site_cands(cands, "Acme")
    # the company's own logo file (name + "logo") leads
    assert ranked[0] == "https://x.com/assets/acme-logo.svg"
    # the bare touch-icon sinks to the bottom
    assert ranked[-1] == "https://x.com/apple-touch-icon.png"


def test_looks_third_party_respects_own_name_tokens():
    # a brand whose own name contains a marker word keeps its own logo …
    assert not lf._looks_third_party("partners group logo", ("partners",))
    assert not lf._looks_third_party("virgin media logo", ("virgin", "media"))
    # … while a genuine third-party mark on that page is still caught
    assert lf._looks_third_party("visa logo", ("partners",))
    assert lf._looks_third_party("press-logos strip", ("virgin", "media"))


def test_extract_logo_urls_keeps_brand_whose_name_has_marker_word():
    # "Virgin Media" / "John Lewis Partnership": the brand's own name contains a
    # third-party marker word; its own logo must NOT be filtered out. (Both are
    # registry accounts; this was a real regression.)
    vm = ('<header><a href="/">'
          '<img class="logo" alt="Virgin Media logo" src="/vm-logo.svg"></a></header>')
    out = lf.extract_logo_urls(vm, "https://virginmedia.com", company="Virgin Media")
    assert "https://virginmedia.com/vm-logo.svg" in out["primary"]

    jl = ('<header><a href="/">'
          '<img class="logo" alt="John Lewis Partnership" src="/jl.svg"></a></header>')
    out2 = lf.extract_logo_urls(jl, "https://johnlewis.com",
                                company="John Lewis Partnership")
    assert "https://johnlewis.com/jl.svg" in out2["primary"]


def test_extract_logo_urls_own_token_guard_is_what_keeps_partners_group():
    # "Partners Group": "partners" is a third-party marker, so WITHOUT the
    # company context its logo is dropped — but WITH it, the own-token guard
    # keeps it. Proves the guard, not just the regex, is doing the work.
    html = ('<header><a href="/">'
            '<img class="partners-logo" alt="Partners Group" src="/pg.svg"></a></header>')
    out_blind = lf.extract_logo_urls(html, "https://partnersgroup.com")
    assert "https://partnersgroup.com/pg.svg" not in out_blind["primary"]
    out_named = lf.extract_logo_urls(html, "https://partnersgroup.com",
                                     company="Partners Group")
    assert "https://partnersgroup.com/pg.svg" in out_named["primary"]
