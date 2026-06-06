"""Tests for tool/logo_fetch — company-logo acquisition for pitch-pack covers.

Tests use mocked HTTP so they run without network access.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)


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


def test_well_known_icon_tried_first():
    from tool import logo_fetch
    png = _make_red_png()

    def mock_get(url, **kw):
        if "/apple-touch-icon.png" in url:
            return _mock_response(png)
        return None

    with patch.object(logo_fetch, "get", side_effect=mock_get), \
         patch.dict(os.environ, {"BRIGHT_DATA_KEY": "", "BRIGHT_DATA_ZONE": ""}):
        result = logo_fetch.fetch_logo("example.com")
    assert result is not None
    assert result[0] == png


def test_scrape_finds_logo_in_header():
    from tool import logo_fetch
    png = _make_red_png()

    def mock_get(url, **kw):
        if "/apple-touch-icon" in url or "/favicon" in url:
            return None
        if "example.com" in url and "/images/logo" not in url:
            return _mock_response(_LOGO_HTML.encode(), content_type="text/html",
                                  url="https://www.example.com")
        if "/images/logo.png" in url:
            return _mock_response(png)
        return None

    with patch.object(logo_fetch, "get", side_effect=mock_get), \
         patch.dict(os.environ, {"BRIGHT_DATA_KEY": "", "BRIGHT_DATA_ZONE": ""}):
        result = logo_fetch.fetch_logo("example.com")
    assert result is not None
    assert result[0] == png
    assert result[1] == "image/png"


def test_bright_data_fallback_on_403():
    from tool import logo_fetch
    png = _make_red_png()
    html_with_logo = '<html><body><img class="logo" src="/logo.png"></body></html>'

    def mock_get(url, **kw):
        if "/logo.png" in url:
            return _mock_response(png)
        # all direct requests return 403
        return _mock_response(b"blocked", status=403)

    def mock_bd_post(url, json=None, headers=None, timeout=None):
        r = MagicMock()
        r.status_code = 200
        r.text = html_with_logo
        return r

    with patch.object(logo_fetch, "get", side_effect=mock_get), \
         patch("requests.post", side_effect=mock_bd_post), \
         patch.dict(os.environ, {"BRIGHT_DATA_KEY": "test", "BRIGHT_DATA_ZONE": "zone1"}):
        result = logo_fetch.fetch_logo("example.com")
    assert result is not None
    assert result[1] == "image/png"


def test_returns_none_when_no_logo():
    from tool import logo_fetch

    def mock_get(url, **kw):
        if "/apple-touch-icon" in url or "/favicon" in url:
            return None
        return _mock_response(_NO_LOGO_HTML.encode(), content_type="text/html",
                              url="https://www.example.com")

    with patch.object(logo_fetch, "get", side_effect=mock_get), \
         patch.dict(os.environ, {"BRIGHT_DATA_KEY": "", "BRIGHT_DATA_ZONE": ""}):
        result = logo_fetch.fetch_logo("example.com")
    assert result is None


def test_empty_domain_returns_none():
    from tool import logo_fetch
    assert logo_fetch.fetch_logo("") is None
    assert logo_fetch.fetch_logo(None) is None


def test_svg_logo_accepted():
    from tool import logo_fetch
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect fill="red" width="100" height="100"/></svg>'
    html = b'<html><body><img class="logo" src="/logo.svg"></body></html>'

    def mock_get(url, **kw):
        if "/apple-touch-icon" in url or "/favicon" in url:
            return None
        if "logo.svg" in url:
            return _mock_response(svg, content_type="image/svg+xml")
        return _mock_response(html, content_type="text/html",
                              url="https://www.example.com")

    with patch.object(logo_fetch, "get", side_effect=mock_get), \
         patch.dict(os.environ, {"BRIGHT_DATA_KEY": "", "BRIGHT_DATA_ZONE": ""}):
        result = logo_fetch.fetch_logo("example.com")
    assert result is not None
    assert result[1] == "image/svg+xml"


def test_domain_guess_for_unknown_company():
    """pitch_proposal._fetch_client_logo should guess the domain for
    companies not in the registry (e.g. Equinor → equinor.com)."""
    from tool import pitch_proposal as pp
    png = _make_red_png()

    with patch("tool.logo_fetch.fetch_logo") as mock_fetch:
        # First call (registry domain) doesn't happen for unknown company.
        # First guess: equinor.com → found
        mock_fetch.return_value = (png, "image/png")
        result = pp._fetch_client_logo("Equinor")

    assert result is not None
    assert result[0] == png
    # Verify it was called with the guessed domain
    calls = [c.args[0] for c in mock_fetch.call_args_list]
    assert "equinor.com" in calls
