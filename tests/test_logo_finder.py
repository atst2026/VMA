"""Tests for tool/logo_finder — the company-logo resolution engine.

These cover the logic that makes deep-tech / startup accounts resolve (the
reported misses: "Geordie AI" -> geordie.ai, "OQC" -> oqc.tech), entirely
offline: domain candidates across modern TLDs, SERP result parsing + company
matching, homepage logo scraping, image validation, and the find_logo()
control flow (driven with monkeypatched network so it's deterministic).
"""
import base64
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from tool import logo_finder as lf


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


def test_find_logo_falls_back_to_clearbit_then_wordmark(monkeypatch):
    monkeypatch.setattr(lf, "resolve_domain",
                        lambda c, hint_url=None: ("oqc.tech", "known"))
    monkeypatch.setattr(lf, "_fetch_html", lambda url: "<html></html>")  # no logo on page
    monkeypatch.setattr(lf, "_fetch_image", lambda url: None)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 400
    monkeypatch.setattr(lf, "_clearbit", lambda d: png)
    data, src = lf.find_logo("OQC")
    assert data == png and src == "clearbit:oqc.tech"

    # and if even clearbit misses, and no encyclopaedia, we get the wordmark
    monkeypatch.setattr(lf, "_clearbit", lambda d: None)
    monkeypatch.setattr(lf, "_logodev", lambda d: None)
    monkeypatch.setattr(lf, "_wikidata_logo", lambda c: (None, ""))
    monkeypatch.setattr(lf, "_wikipedia_logo", lambda c: (None, ""))
    monkeypatch.setattr(lf, "_google_favicon", lambda d: None)
    assert lf.find_logo("OQC") == (None, "wordmark")


def test_resolve_domain_uses_known_map_for_reported_cases():
    # No network needed: the two reported accounts are in the curated map, so
    # they resolve instantly and correctly.
    assert lf.resolve_domain("Geordie AI") == ("geordie.ai", "known")
    assert lf.resolve_domain("OQC") == ("oqc.tech", "known")
    assert lf.resolve_domain("Oxford Quantum Circuits") == ("oqc.tech", "known")
