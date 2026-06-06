#!/usr/bin/env python3
"""Fetch a BD lead company's logo directly from its official website.

Deliberately simple and self-contained. There is no registry, no cache, no
recolouring, no fallback chain of services: we find the company's official
site, take the logo image from its homepage, and return the bytes UNCHANGED.
The pitch-pack cover only sizes it to fit the box — the logo itself is never
altered.
"""
from __future__ import annotations

import base64
import logging
import re
from urllib.parse import unquote, urljoin, urlparse

from tool.sources._http import get

log = logging.getLogger("lead_logo")

# Hosts that are never the company's own homepage (search / social / press /
# data brokers). A search hit on one of these is skipped.
_NOT_OFFICIAL = (
    "duckduckgo.", "google.", "bing.", "linkedin.", "crunchbase.", "wikipedia.",
    "wikidata.", "wikimedia.", "bloomberg.", "reuters.", "ft.com", "forbes.",
    "facebook.", "twitter.", "x.com", "instagram.", "youtube.", "youtu.be",
    "glassdoor.", "indeed.", "gov.uk", "trustpilot.", "amazon.", "apple.com",
    "medium.com", "pitchbook.", "dnb.com", "zoominfo.", "yahoo.", "msn.com",
)


def official_website(company: str) -> str | None:
    """The company's official website domain, found via a web search."""
    company = (company or "").strip()
    if not company:
        return None
    r = get("https://duckduckgo.com/html/", params={"q": f"{company} official website"})
    if not r or r.status_code != 200:
        return None
    # DuckDuckGo HTML wraps each result URL as ...uddg=<percent-encoded-url>...
    for m in re.finditer(r"uddg=([^&\"']+)", r.text):
        host = urlparse(unquote(m.group(1))).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host and "." in host and not any(b in host for b in _NOT_OFFICIAL):
            return host
    return None


def _logo_on_homepage(html: str, base_url: str) -> str | None:
    """The logo image URL taken from the homepage HEADER, where a company puts
    its own brand mark. Falls back to the site's own apple-touch / mask icon.
    The footer is ignored (that is where partner / accreditation badges live)."""
    low = html.lower()
    header = html[: low.find("</header>") + 9] if "</header>" in low else html[:20000]

    # an <img> in the header that identifies itself as the logo
    for m in re.finditer(r"<img[^>]+>", header, re.I):
        tag = m.group(0)
        if "logo" in tag.lower():
            s = re.search(r"""(?:data-src|src)\s*=\s*["']([^"']+)""", tag, re.I)
            if s:
                return urljoin(base_url, s.group(1))

    # otherwise the first <img> inside the <header>
    hm = re.search(r"<header[^>]*>(.*?)</header>", html, re.I | re.S)
    if hm:
        s = re.search(r"""<img[^>]+(?:data-src|src)\s*=\s*["']([^"']+)""", hm.group(1), re.I)
        if s:
            return urljoin(base_url, s.group(1))

    # the site's own apple-touch-icon / svg mask-icon (still on the official site)
    for m in re.finditer(r"<link[^>]+>", html, re.I):
        tag = m.group(0)
        if re.search(r"""rel\s*=\s*["'][^"']*(?:apple-touch-icon|mask-icon)""", tag, re.I):
            h = re.search(r"""href\s*=\s*["']([^"']+)""", tag, re.I)
            if h:
                return urljoin(base_url, h.group(1))
    return None


def fetch_logo(company: str) -> bytes | None:
    """The company's logo bytes, taken directly from its official website, or
    None if it cannot be sourced. The bytes are returned UNCHANGED. Never
    raises — the caller falls back to the company name."""
    try:
        domain = official_website(company)
        if not domain:
            log.info("no official website found for %r", company)
            return None
        base = "https://" + domain
        page = get(base)
        if not page or page.status_code != 200 or not page.text:
            log.info("could not load homepage %s", base)
            return None
        url = _logo_on_homepage(page.text, base)
        if not url:
            log.info("no logo on homepage %s", domain)
            return None
        img = get(url)
        if not img or img.status_code != 200 or not img.content:
            log.info("could not download logo %s", url)
            return None
        log.info("logo for %r from %s (%d bytes)", company, url, len(img.content))
        return img.content
    except Exception as e:
        log.info("logo fetch failed for %r: %s", company, e)
        return None


def logo_data_uri(data: bytes) -> str:
    """Embed the raw logo bytes as a data URI, MIME sniffed (PNG/JPEG/GIF/SVG)
    so WeasyPrint renders it. The bytes are not modified."""
    head = data[:300].lstrip().lower()
    if data[:8].startswith(b"\x89PNG"):
        mime = "image/png"
    elif data[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif data[:4] == b"GIF8":
        mime = "image/gif"
    elif data[:6] == b"<?xml " or data[:5] == b"<?xml":
        mime = "image/svg+xml" if b"<svg" in data[:600].lower() else "image/png"
    elif head.startswith(b"<svg"):
        mime = "image/svg+xml"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
