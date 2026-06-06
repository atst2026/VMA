"""Tests for tool/logo_fetch — company-logo acquisition for pitch-pack covers.

Tests use mocked HTTP so they run without network access.
"""
import base64
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

# Minimal valid 2x2 red PNG (passes Pillow verify + dimension check at >=48px
# is skipped here; the module's _MIN_DIM gate is tested separately).
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
    "nGP4z8BQDwAEgAF/pooBPQAAAABJRU5ErkJggg==")

# 64x64 red PNG — large enough to pass _MIN_DIM.
def _make_red_png(size: int = 64) -> bytes:
    try:
        import io
        from PIL import Image
        img = Image.new("RGB", (size, size), (255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        pytest.skip("Pillow not available")

_LOGO_HTML = """
<html><head></head><body>
<header>
  <img class="site-logo" src="/images/logo.png" alt="Acme Logo">
</header>
<main><p>Hello</p></main>
</body></html>
"""

_NO_LOGO_HTML = """
<html><head></head><body>
<header><img src="/banner.jpg" alt="banner"></header>
</body></html>
"""


def _mock_response(content, status=200, content_type="image/png", url=None):
    r = MagicMock()
    r.status_code = status
    r.content = content
    r.text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    r.url = url or "https://www.example.com"
    r.headers = {"Content-Type": content_type}
    return r


def test_scrape_finds_logo_in_header():
    from tool import logo_fetch
    png = _make_red_png()

    def mock_get(url, **kw):
        if "example.com" in url and "/images/logo" not in url:
            return _mock_response(_LOGO_HTML.encode(), content_type="text/html",
                                  url="https://www.example.com")
        if "/images/logo.png" in url:
            return _mock_response(png)
        return None

    with patch.object(logo_fetch, "get", side_effect=mock_get):
        result = logo_fetch.fetch_logo("example.com")
    assert result is not None
    assert result[0] == png
    assert result[1] == "image/png"


def test_returns_none_when_no_logo():
    from tool import logo_fetch

    def mock_get(url, **kw):
        if "clearbit" in url:
            return _mock_response(b"", status=404)
        return _mock_response(_NO_LOGO_HTML.encode(), content_type="text/html",
                              url="https://www.example.com")

    with patch.object(logo_fetch, "get", side_effect=mock_get):
        result = logo_fetch.fetch_logo("example.com")
    assert result is None


def test_clearbit_fallback():
    from tool import logo_fetch
    png = _make_red_png()

    def mock_get(url, **kw):
        if "clearbit" in url:
            return _mock_response(png, content_type="image/png")
        # website returns no logo
        return _mock_response(_NO_LOGO_HTML.encode(), content_type="text/html",
                              url="https://www.example.com")

    with patch.object(logo_fetch, "get", side_effect=mock_get):
        result = logo_fetch.fetch_logo("example.com")
    assert result is not None
    assert result[1] == "image/png"


def test_empty_domain_returns_none():
    from tool import logo_fetch
    assert logo_fetch.fetch_logo("") is None
    assert logo_fetch.fetch_logo(None) is None


def test_svg_logo_accepted():
    from tool import logo_fetch
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect fill="red" width="100" height="100"/></svg>'
    html = b'<html><body><img class="logo" src="/logo.svg"></body></html>'

    def mock_get(url, **kw):
        if "logo.svg" in url:
            return _mock_response(svg, content_type="image/svg+xml")
        if "clearbit" in url:
            return None
        return _mock_response(html, content_type="text/html",
                              url="https://www.example.com")

    with patch.object(logo_fetch, "get", side_effect=mock_get):
        result = logo_fetch.fetch_logo("example.com")
    assert result is not None
    assert result[1] == "image/svg+xml"
