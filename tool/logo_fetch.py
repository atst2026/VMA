"""Fetch a company logo from their website for pitch-pack cover use.

Strategy (tried in order, first success wins):
  1. Direct HTTP: try well-known icon paths (/apple-touch-icon.png etc.)
     that often bypass WAFs, then scrape the HTML for <img> logo elements.
  2. Bright Data Web Unlocker: re-scrape through the proxy, which renders
     JS and bypasses CDN/WAF blocks (nestle.com, kpmg.com etc.).
  3. Return None → the caller falls back to a typographic wordmark.

Every candidate image is validated with Pillow (raster) or a basic XML
check (SVG) so the cover never embeds a corrupt or invisible asset.
"""
from __future__ import annotations

import io
import logging
import os
import re
from urllib.parse import urljoin

import requests

from tool.sources._http import get

log = logging.getLogger("logo_fetch")

_LOGO_RE = re.compile(r"logo", re.IGNORECASE)
_MIN_BYTES = 100
_MIN_DIM = 48

_WELL_KNOWN_ICONS = (
    "/apple-touch-icon.png",
    "/apple-touch-icon-precomposed.png",
    "/favicon.svg",
)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def fetch_logo(domain: str) -> tuple[bytes, str] | None:
    """Return *(image_bytes, mime_type)* for *domain*, or ``None``."""
    if not domain:
        return None
    domain = domain.strip().lower()

    result = (
        _try_well_known_icons(domain)
        or _try_scrape_direct(domain)
        or _try_bright_data_scrape(domain)
    )

    if result:
        log.info("logo acquired for %s (%d bytes, %s)",
                 domain, len(result[0]), result[1])
    else:
        log.info("no logo found for %s — will use text wordmark", domain)
    return result


# ------------------------------------------------------------------
# Strategy 1a — well-known icon paths (often bypass WAFs)
# ------------------------------------------------------------------

def _try_well_known_icons(domain: str) -> tuple[bytes, str] | None:
    for prefix in (f"https://www.{domain}", f"https://{domain}"):
        for path in _WELL_KNOWN_ICONS:
            result = _download_and_validate(prefix + path)
            if result:
                return result
    return None


# ------------------------------------------------------------------
# Strategy 1b — direct HTML scrape (works when no WAF)
# ------------------------------------------------------------------

def _try_scrape_direct(domain: str) -> tuple[bytes, str] | None:
    for origin in (f"https://www.{domain}", f"https://{domain}"):
        resp = get(origin, timeout=10, tries=1)
        if resp and resp.status_code == 200:
            return _extract_logo_from_html(resp.text, resp.url)
    return None


# ------------------------------------------------------------------
# Strategy 2 — Bright Data Web Unlocker (bypasses WAFs)
# ------------------------------------------------------------------

def _try_bright_data_scrape(domain: str) -> tuple[bytes, str] | None:
    bd_key = os.environ.get("BRIGHT_DATA_KEY", "").strip()
    bd_zone = os.environ.get("BRIGHT_DATA_ZONE", "").strip()
    if not bd_key or not bd_zone:
        log.info("Bright Data not configured — skipping WAF bypass")
        return None

    url = f"https://www.{domain}"
    log.info("trying Bright Data Web Unlocker for %s", url)
    payload = {"zone": bd_zone, "url": url, "format": "raw"}
    headers = {
        "Authorization": f"Bearer {bd_key}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(
            "https://api.brightdata.com/request",
            json=payload, headers=headers, timeout=30,
        )
        if r.status_code != 200 or not r.text:
            log.info("Bright Data %s → HTTP %s", url, r.status_code)
            return None
    except requests.RequestException as exc:
        log.info("Bright Data fetch failed: %s", exc)
        return None

    return _extract_logo_from_html(r.text, url)


# ------------------------------------------------------------------
# HTML → logo extraction (shared by direct + Bright Data paths)
# ------------------------------------------------------------------

def _extract_logo_from_html(html: str, base_url: str,
                            ) -> tuple[bytes, str] | None:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    candidates: list[str] = []

    # 1 — <img> with "logo" in attrs, inside <header>/<nav> (highest signal)
    for container in soup.find_all(["header", "nav"]):
        for img in container.find_all("img"):
            if _img_looks_like_logo(img):
                src = _img_src(img)
                if src:
                    candidates.append(urljoin(base_url, src))

    # 2 — <img> with "logo" anywhere on the page
    for img in soup.find_all("img"):
        if _img_looks_like_logo(img):
            src = _img_src(img)
            if src:
                url = urljoin(base_url, src)
                if url not in candidates:
                    candidates.append(url)

    # 3 — apple-touch-icon / shortcut icon declared in <head>
    for link in soup.find_all("link", rel=True):
        rels = link["rel"] if isinstance(link["rel"], list) else [link["rel"]]
        rels_lower = " ".join(rels).lower()
        if ("apple-touch-icon" in rels_lower or "icon" in rels_lower) \
                and link.get("href"):
            url = urljoin(base_url, link["href"])
            if url not in candidates:
                candidates.append(url)

    # 4 — og:image meta tag (some sites use their logo here)
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        url = urljoin(base_url, og["content"])
        if url not in candidates:
            candidates.append(url)

    for url in candidates:
        result = _download_and_validate(url)
        if result:
            return result

    return None


# ------------------------------------------------------------------
# Tag helpers
# ------------------------------------------------------------------

def _img_looks_like_logo(tag) -> bool:
    classes = tag.get("class", [])
    if isinstance(classes, list):
        classes = " ".join(classes)
    haystack = " ".join([
        classes,
        tag.get("id", ""),
        tag.get("alt", ""),
        tag.get("src", ""),
        tag.get("data-src", ""),
    ])
    return bool(_LOGO_RE.search(haystack))


def _img_src(tag) -> str | None:
    for attr in ("src", "data-src"):
        v = tag.get(attr)
        if v and not v.startswith("data:"):
            return v.split()[0]
    srcset = tag.get("srcset", "")
    if srcset:
        first = srcset.split(",")[0].strip().split()[0]
        if first and not first.startswith("data:"):
            return first
    return None


# ------------------------------------------------------------------
# Download + validation
# ------------------------------------------------------------------

def _download_and_validate(url: str) -> tuple[bytes, str] | None:
    resp = get(url, timeout=8, tries=1)
    if not resp or resp.status_code != 200:
        return None
    if len(resp.content) < _MIN_BYTES:
        return None
    ct = resp.headers.get("Content-Type", "").split(";")[0].strip()
    if not ct.startswith("image/"):
        return None
    return _validate(resp.content, ct)


def _validate(data: bytes, mime: str) -> tuple[bytes, str] | None:
    if "svg" in mime:
        return _validate_svg(data, mime)
    return _validate_raster(data, mime)


def _validate_svg(data: bytes, mime: str) -> tuple[bytes, str] | None:
    try:
        text = data.decode("utf-8", errors="replace")
        if "<svg" not in text.lower():
            return None
        return data, "image/svg+xml"
    except Exception:
        return None


def _validate_raster(data: bytes, mime: str) -> tuple[bytes, str] | None:
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        if w < _MIN_DIM or h < _MIN_DIM:
            log.info("logo too small: %dx%d", w, h)
            return None
        return data, mime
    except Exception as exc:
        log.info("logo validation failed: %s", exc)
        return None
