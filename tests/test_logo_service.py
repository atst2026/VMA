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

# a real (>256 byte) PNG body and an SVG body
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 400
_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80"><rect/></svg>' + b" " * 300


class _Resp:
    def __init__(self, content=b"", status=200, content_type="image/png"):
        self.content = content
        self.status_code = status
        self.headers = {"content-type": content_type}


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    # point the cache at a temp file so tests never touch real state
    monkeypatch.setattr(ls, "_CACHE_PATH", tmp_path / "logo_cache.json")
    yield


# ---- happy path: deterministic domain source --------------------------

def test_get_logo_uses_domain_clearbit(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return _Resp(content=_PNG) if url == "https://logo.clearbit.com/diageo.com" else _Resp(status=404)

    monkeypatch.setattr(ls, "get", fake_get)
    r = ls.get_logo("Diageo")
    assert r.company_id == "diageo"
    assert r.url == "https://logo.clearbit.com/diageo.com"
    assert r.source == "domain:clearbit"
    assert r.data == _PNG
    assert r.data_uri().startswith("data:image/png;base64,")


def test_registry_logo_url_wins_over_domain(monkeypatch):
    # OQC has a pinned verified logo_url (its own oqc.tech SVG) — used first.
    def fake_get(url, **kw):
        if url == ci.resolve("OQC").logo_url:
            return _Resp(content=_SVG, content_type="image/svg+xml")
        return _Resp(content=_PNG)   # clearbit would also work, but must not be used
    monkeypatch.setattr(ls, "get", fake_get)
    r = ls.get_logo("OQC")
    assert r.source == "registry"
    assert r.url == ci.resolve("OQC").logo_url
    assert r.content_type == "image/svg+xml"


# ---- failure modes MUST raise (never silently degrade) ----------------

def test_unknown_company_raises(monkeypatch):
    monkeypatch.setattr(ls, "get", lambda url, **kw: _Resp(content=_PNG))
    with pytest.raises(ci.UnknownCompanyError):
        ls.get_logo("Some Unlisted Co")


def test_non_200_raises(monkeypatch):
    monkeypatch.setattr(ls, "get", lambda url, **kw: _Resp(status=404))
    with pytest.raises(ls.LogoResolutionError):
        ls.get_logo("Diageo")


def test_empty_or_placeholder_raises(monkeypatch):
    monkeypatch.setattr(ls, "get", lambda url, **kw: _Resp(content=b"\x89PNG" + b"x" * 10))
    with pytest.raises(ls.LogoResolutionError):
        ls.get_logo("Diageo")          # < 256 bytes -> rejected


def test_non_image_content_raises(monkeypatch):
    # an HTML error page served with an image content-type must be rejected
    monkeypatch.setattr(ls, "get",
                        lambda url, **kw: _Resp(content=b"<html>404 not found</html>" + b" " * 300,
                                                content_type="image/png"))
    with pytest.raises(ls.LogoResolutionError):
        ls.get_logo("Diageo")


def test_wrong_domain_logo_rejected(monkeypatch):
    # a registry logo_url that does NOT live on the company domain or a trusted
    # provider must be rejected (guards "right company, wrong-host logo").
    bad = ci.Company("xyzco", "XYZ Co", "xyz.com",
                     logo_url="https://randomcdn.example/notxyz.png")
    monkeypatch.setattr(ci, "resolve", lambda name: bad)
    # the wrong-host registry asset returns bytes, but the deterministic
    # clearbit source misses — so the only candidate is the wrong-host one,
    # which must be rejected on the domain check.
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
        return _Resp(content=_PNG) if "clearbit" in url else _Resp(status=404)

    monkeypatch.setattr(ls, "get", fake_get)
    r1 = ls.get_logo("Diageo")
    assert r1.source == "domain:clearbit"
    n = len(calls)
    r2 = ls.get_logo("Diageo")         # second call: served from cache, no fetch
    assert r2.source == "cache"
    assert r2.data == r1.data
    assert len(calls) == n             # no further network calls


def test_invalidate_forces_refetch(monkeypatch):
    monkeypatch.setattr(ls, "get",
                        lambda url, **kw: _Resp(content=_PNG) if "clearbit" in url else _Resp(status=404))
    ls.get_logo("Diageo")
    ls.invalidate("Diageo")
    assert ls._cache_get(ci.resolve("Diageo")) is None
