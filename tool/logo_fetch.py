"""Fetch a company logo from the web for pitch-pack cover use.

Strategy (tried in order, first success wins):
  1. Scrape the company's own website for <img> elements whose class / id /
     alt text contains "logo", prioritising images inside <header> or <nav>.
  2. Clearbit Logo API — free, no key, reliable for major companies.
  3. Return None → the caller falls back to a typographic wordmark.

The returned image is validated with Pillow (raster) or a basic XML check
(SVG) so the cover never embeds a corrupt or invisible asset.
"""
from __future__ import annotations

import io
import logging
import re
from urllib.parse import urljoin

from tool.sources._http import get

log = logging.getLogger("logo_fetch")

_LOGO_RE = re.compile(r"logo", re.IGNORECASE)
_MIN_BYTES = 100
_MIN_DIM = 48


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def fetch_logo(domain: str) -> tuple[bytes, str] | None:
    """Return *(image_bytes, mime_type)* for *domain*, or ``None``."""
    if not domain:
        return None
    domain = domain.strip().lower()
    result = _try_scrape(domain) or _try_clearbit(domain)
    if result:
        log.info("logo acquired for %s (%d bytes, %s)", domain, len(result[0]), result[1])
    else:
        log.info("no logo found for %s — will use text wordmark", domain)
    return result


# ------------------------------------------------------------------
# Strategy 1 — scrape the company website
# ------------------------------------------------------------------

def _try_scrape(domain: str) -> tuple[bytes, str] | None:
    from bs4 import BeautifulSoup

    for origin in (f"https://www.{domain}", f"https://{domain}"):
        resp = get(origin, timeout=10, tries=1)
        if resp and resp.status_code == 200:
            break
    else:
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    base = resp.url

    candidates: list[str] = []

    # 1a — <img> with "logo" in attrs, inside <header>/<nav> (highest signal)
    for container in soup.find_all(["header", "nav"]):
        for img in container.find_all("img"):
            if _img_looks_like_logo(img):
                src = _img_src(img)
                if src:
                    candidates.append(urljoin(base, src))

    # 1b — <img> with "logo" anywhere on the page
    for img in soup.find_all("img"):
        if _img_looks_like_logo(img):
            src = _img_src(img)
            if src:
                url = urljoin(base, src)
                if url not in candidates:
                    candidates.append(url)

    # 1c — apple-touch-icon (a high-res square icon, decent fallback)
    for link in soup.find_all("link", rel=True):
        rels = link["rel"] if isinstance(link["rel"], list) else [link["rel"]]
        if any("apple-touch-icon" in r for r in rels) and link.get("href"):
            candidates.append(urljoin(base, link["href"]))

    for url in candidates:
        result = _download_and_validate(url)
        if result:
            return result

    return None


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
# Strategy 2 — Clearbit Logo API
# ------------------------------------------------------------------

def _try_clearbit(domain: str) -> tuple[bytes, str] | None:
    url = f"https://logo.clearbit.com/{domain}?size=512"
    resp = get(url, timeout=8, tries=1)
    if not resp or resp.status_code != 200:
        return None
    if len(resp.content) < _MIN_BYTES:
        return None
    ct = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
    if not ct.startswith("image/"):
        return None
    return _validate(resp.content, ct)


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
