"""Tests for tool/lead_logo — sourcing a BD lead's logo from its official
website. All offline: the HTTP layer (`get`) is monkeypatched.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from tool import lead_logo as ll


class _Resp:
    def __init__(self, text="", content=b"", status=200):
        self.text = text
        self.content = content
        self.status_code = status


# ---- official_website (web search) ------------------------------------

def test_official_website_picks_first_non_aggregator(monkeypatch):
    ddg = ('<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FOQC">w</a>'
           '<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.oqc.tech%2F&rut=x">OQC</a>')
    monkeypatch.setattr(ll, "get", lambda url, **kw: _Resp(text=ddg))
    assert ll.official_website("Oxford Quantum Circuits") == "oqc.tech"


def test_official_website_none_when_no_results(monkeypatch):
    monkeypatch.setattr(ll, "get", lambda url, **kw: _Resp(text="<html>nothing here</html>"))
    assert ll.official_website("Some Unlisted Co") is None
    assert ll.official_website("") is None


# ---- logo extraction from the homepage --------------------------------

def test_logo_on_homepage_prefers_header_logo_ignores_footer():
    html = ('<header><a href="/"><img class="logo" src="/img/acme-logo.svg"></a></header>'
            '<footer><img src="/badges/cyber-essentials.png"></footer>')
    assert ll._logo_on_homepage(html, "https://acme.com") == "https://acme.com/img/acme-logo.svg"


def test_logo_on_homepage_first_header_img_then_icon_fallback():
    # no "logo"-marked img, but a header img -> take it
    html = '<header><img src="/brand.png"></header>'
    assert ll._logo_on_homepage(html, "https://acme.com") == "https://acme.com/brand.png"
    # nothing in the header -> the site's own apple-touch-icon
    html2 = '<head><link rel="apple-touch-icon" href="/at.png"></head><header><nav>menu</nav></header>'
    assert ll._logo_on_homepage(html2, "https://acme.com") == "https://acme.com/at.png"


# ---- fetch_logo orchestration -----------------------------------------

def test_fetch_logo_returns_bytes_unchanged(monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + b"\x10" * 300

    def fake_get(url, **kw):
        if "duckduckgo" in url:
            return _Resp(text='href="//x/l/?uddg=https%3A%2F%2Facme.com%2F"')
        if url == "https://acme.com":
            return _Resp(text='<header><img class="logo" src="/logo.png"></header>')
        if url == "https://acme.com/logo.png":
            return _Resp(content=png)
        return _Resp(status=404)

    monkeypatch.setattr(ll, "get", fake_get)
    assert ll.fetch_logo("Acme") == png            # byte-identical, unchanged


def test_fetch_logo_none_when_no_official_site(monkeypatch):
    monkeypatch.setattr(ll, "get", lambda url, **kw: _Resp(text=""))
    assert ll.fetch_logo("Nobody Co") is None


def test_fetch_logo_never_raises(monkeypatch):
    def boom(url, **kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(ll, "get", boom)
    assert ll.fetch_logo("Acme") is None


# ---- data URI ----------------------------------------------------------

def test_logo_data_uri_sniffs_mime():
    assert ll.logo_data_uri(b"\x89PNG\r\n\x1a\nxxxx").startswith("data:image/png;base64,")
    assert ll.logo_data_uri(b"\xff\xd8\xff\xe0junk").startswith("data:image/jpeg;base64,")
    assert ll.logo_data_uri(b"GIF89a----").startswith("data:image/gif;base64,")
    assert ll.logo_data_uri(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>").startswith(
        "data:image/svg+xml;base64,")
