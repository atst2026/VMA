"""Tests for tool/logo_service — the deterministic, validated, fail-safe logo
resolver. All offline: the HTTP layer (`get`) and the cache path are
monkeypatched, so no network and no shared state.
"""
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from tool import company_identity as ci
from tool import logo_service as ls

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 400
_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80"><rect/></svg>' + b" " * 300


class _Resp:
    def __init__(self, content=b"", status=200, content_type="image/png", text=""):
        self.content = content
        self.status_code = status
        self.headers = {"content-type": content_type}
        self.text = text


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "_CACHE_PATH", tmp_path / "logo_cache.json")
    yield


# ---- happy paths: the company's OWN website -----------------------------

def test_uses_homepage_declared_icon(monkeypatch):
    def fake_get(url, **kw):
        if url == "https://diageo.com":
            return _Resp(text='<link rel="apple-touch-icon" href="/ati.png">')
        if url == "https://diageo.com/ati.png":
            return _Resp(content=_PNG)
        return _Resp(status=404)
    monkeypatch.setattr(ls, "get", fake_get)
    r = ls.get_logo("Diageo")
    assert r.source == "domain:declared"
    assert r.url == "https://diageo.com/ati.png"
    assert r.data == _PNG and r.data_uri().startswith("data:image/png;base64,")


def test_uses_well_known_apple_touch_path(monkeypatch):
    def fake_get(url, **kw):
        if url == "https://diageo.com":
            return _Resp(text="<html><head></head></html>")     # nothing declared
        if url == "https://diageo.com/apple-touch-icon.png":
            return _Resp(content=_PNG)
        return _Resp(status=404)
    monkeypatch.setattr(ls, "get", fake_get)
    r = ls.get_logo("Diageo")
    assert r.source == "domain:apple-touch"
    assert r.url == "https://diageo.com/apple-touch-icon.png"


def test_registry_logo_url_wins(monkeypatch):
    # OQC has a pinned verified logo_url (its own oqc.tech SVG) — used first,
    # before the homepage is even fetched.
    def fake_get(url, **kw):
        if url == ci.resolve("OQC").logo_url:
            return _Resp(content=_SVG, content_type="image/svg+xml")
        raise AssertionError(f"should not fetch {url} once the registry asset works")
    monkeypatch.setattr(ls, "get", fake_get)
    r = ls.get_logo("OQC")
    assert r.source == "registry" and r.content_type == "image/svg+xml"


def test_declared_icon_on_cdn_is_accepted(monkeypatch):
    # an icon the verified homepage DECLARES is authoritative even when hosted on
    # the company's CDN (host != company domain) — provenance, not host, is trust.
    def fake_get(url, **kw):
        if url == "https://diageo.com":
            return _Resp(text='<link rel="apple-touch-icon" href="https://cdn.assets.example/diageo.png">')
        if url == "https://cdn.assets.example/diageo.png":
            return _Resp(content=_PNG)
        return _Resp(status=404)
    monkeypatch.setattr(ls, "get", fake_get)
    r = ls.get_logo("Diageo")
    assert r.url == "https://cdn.assets.example/diageo.png" and r.source == "domain:declared"


# ---- failure modes MUST raise (never silently degrade) ------------------

def test_unknown_company_raises(monkeypatch):
    monkeypatch.setattr(ls, "get", lambda url, **kw: _Resp(content=_PNG))
    with pytest.raises(ci.UnknownCompanyError):
        ls.get_logo("Some Unlisted Co")


def test_all_sources_miss_raises(monkeypatch):
    monkeypatch.setattr(ls, "get", lambda url, **kw: _Resp(status=404))
    with pytest.raises(ls.LogoResolutionError):
        ls.get_logo("Diageo")


def test_empty_or_placeholder_raises(monkeypatch):
    def fake_get(url, **kw):
        if url == "https://diageo.com":
            return _Resp(text="<html></html>")
        return _Resp(content=b"\x89PNG" + b"x" * 10)     # < 256 bytes everywhere
    monkeypatch.setattr(ls, "get", fake_get)
    with pytest.raises(ls.LogoResolutionError):
        ls.get_logo("Diageo")


def test_non_image_content_raises(monkeypatch):
    # an HTML error page served with an image content-type must be rejected
    def fake_get(url, **kw):
        if url == "https://diageo.com":
            return _Resp(text="<html></html>")
        return _Resp(content=b"<html>404 not found</html>" + b" " * 300, content_type="image/png")
    monkeypatch.setattr(ls, "get", fake_get)
    with pytest.raises(ls.LogoResolutionError):
        ls.get_logo("Diageo")


def test_wrong_domain_registry_logo_rejected(monkeypatch):
    # a pinned registry asset on a non-company, non-trusted host is rejected, and
    # with no other source the resolution fails (never ships a wrong-host logo).
    bad = ci.Company("xyzco", "XYZ Co", "xyz.com",
                     logo_url="https://randomcdn.example/notxyz.png")
    monkeypatch.setattr(ci, "resolve", lambda name: bad)
    monkeypatch.setattr(ls, "get",
                        lambda url, **kw: _Resp(content=_PNG)
                        if "randomcdn" in url else _Resp(status=404))
    with pytest.raises(ls.LogoResolutionError):
        ls.get_logo("XYZ Co")


def test_no_domain_no_asset_raises(monkeypatch):
    nodomain = ci.Company("nodom", "No Domain Co", None)
    monkeypatch.setattr(ci, "resolve", lambda name: nodomain)
    with pytest.raises(ls.LogoResolutionError):
        ls.get_logo("No Domain Co")


# ---- caching -----------------------------------------------------------

def test_cache_avoids_refetch(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if url == "https://diageo.com":
            return _Resp(text="<html></html>")
        if url == "https://diageo.com/apple-touch-icon.png":
            return _Resp(content=_PNG)
        return _Resp(status=404)

    monkeypatch.setattr(ls, "get", fake_get)
    r1 = ls.get_logo("Diageo")
    n = len(calls)
    r2 = ls.get_logo("Diageo")
    assert r2.source == "cache" and r2.data == r1.data
    assert len(calls) == n             # no further network calls


def test_invalidate_forces_refetch(monkeypatch):
    def fake_get(url, **kw):
        if url == "https://diageo.com":
            return _Resp(text="<html></html>")
        if url == "https://diageo.com/apple-touch-icon.png":
            return _Resp(content=_PNG)
        return _Resp(status=404)
    monkeypatch.setattr(ls, "get", fake_get)
    ls.get_logo("Diageo")
    ls.invalidate("Diageo")
    assert ls._cache_get(ci.resolve("Diageo")) is None
