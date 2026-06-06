#!/usr/bin/env python3
"""Fetch a client's real logo for the pitch-pack cover (logo.dev source).

The retained pitch pack (tool/pitch_pack.py) prints the target company's
name as text on the cover. Where we can fetch a clean, real logo we want
to show that instead — it reads as a tailored, client-specific document
rather than a templated mail-merge.

Source: **logo.dev** image API. Given a *verified* domain we hit
    https://img.logo.dev/{domain}?token=...&size=256&format=png
and run the result through Pillow validation before trusting it. logo.dev
returns a generated monogram fallback for domains it doesn't recognise, so
"got a 200 / got bytes" is NOT good enough — we validate dimensions,
transparency and colour complexity to reject placeholders.

Design rules:
  * NO domain guessing. We only resolve a logo for a company whose domain
    is in the hand-verified registry below (exact match after light
    normalisation). An unknown company returns None and the caller falls
    back to the existing text wordmark — no regression, no wrong logo.
  * Fully self-contained graceful degradation: no token, no domain, a
    network error, or a logo that fails validation all return None.

Token: read from the LOGODEV_TOKEN env var (publishable pk_... token).
In GitHub Actions it is passed from the LOGODEV_TOKEN repository secret —
see .github/workflows/pitch-pack.yml.

Quick check:
    LOGODEV_TOKEN=pk_xxx python -m tool.company_logo "Belron"
    LOGODEV_TOKEN=pk_xxx python -m tool.company_logo belron.com --out /tmp/belron.png
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import sys

log = logging.getLogger("company_logo")

# --- Verified company -> domain registry --------------------------------
# Hand-checked, exact-match only. Keys are normalised (see _normalize):
# lower-cased, common corporate suffixes stripped. Add accounts here as
# their domains are confirmed — never guess a domain at runtime.
_VERIFIED_DOMAINS = {
    "belron": "belron.com",
    "hsbc": "hsbc.com",
    "barclays": "barclays.com",
    "lloyds banking": "lloydsbankinggroup.com",
    "natwest": "natwest.com",
    "tesco": "tesco.com",
    "sainsburys": "sainsburys.co.uk",
    "unilever": "unilever.com",
    "diageo": "diageo.com",
    "gsk": "gsk.com",
    "astrazeneca": "astrazeneca.com",
    "bp": "bp.com",
    "shell": "shell.com",
    "vodafone": "vodafone.com",
    "bt": "bt.com",
    "national grid": "nationalgrid.com",
    "rolls-royce": "rolls-royce.com",
    "aviva": "aviva.com",
    "legal & general": "legalandgeneral.com",
    "prudential": "prudentialplc.com",
}

# Corporate suffixes / noise words stripped before registry lookup so
# "Belron Group", "Tesco PLC", "HSBC UK Ltd" all hit the same key.
_SUFFIX_RX = re.compile(
    r"\b(plc|ltd|limited|llp|group|holdings|holding|inc|corp|corporation|"
    r"company|co|uk|the)\b",
    re.IGNORECASE,
)
_NONWORD_RX = re.compile(r"[^a-z0-9&\- ]+")
_WS_RX = re.compile(r"\s+")

_TIMEOUT = 12
_REQUEST_SIZE = 256       # px we ask logo.dev for
_MIN_DIM = 128            # reject anything smaller than this on its long edge
_MAX_ASPECT = 8.0         # reject absurdly wide/tall strips (likely garbage)


def _normalize(name: str) -> str:
    s = (name or "").lower().strip()
    s = _NONWORD_RX.sub(" ", s)
    s = _SUFFIX_RX.sub(" ", s)
    s = _WS_RX.sub(" ", s).strip()
    return s


def domain_for(name: str) -> str | None:
    """Verified domain for a company name, or None if not registered.

    Accepts a bare domain too ("belron.com") so callers/CLI can pass either.
    """
    if not name:
        return None
    raw = name.strip().lower()
    # Already a domain? (contains a dot and no spaces)
    if "." in raw and " " not in raw:
        return raw.lstrip("@").removeprefix("http://").removeprefix("https://").split("/")[0]
    return _VERIFIED_DOMAINS.get(_normalize(name))


def _token() -> str:
    return (os.environ.get("LOGODEV_TOKEN") or "").strip()


def _fetch_png(domain: str, token: str) -> bytes | None:
    """Fetch the logo PNG bytes from logo.dev for a domain."""
    # Reuse the project's hardened HTTP helper (retries, UA, timeout).
    try:
        from tool.sources._http import get as _get
        r = _get(
            f"https://img.logo.dev/{domain}",
            params={"token": token, "size": _REQUEST_SIZE, "format": "png", "retina": "true"},
            timeout=_TIMEOUT,
        )
        if r is None or r.status_code != 200 or not r.content:
            log.info("logo.dev: no usable response for %s", domain)
            return None
        return r.content
    except Exception as e:  # pragma: no cover - network/runtime guard
        log.info("logo.dev fetch failed for %s: %s", domain, e)
        return None


def _bbox_of_content(img):
    """Bounding box of the non-background content, for trimming whitespace.

    Handles both transparent-background logos (trim on alpha) and
    solid-background logos (trim on difference from the corner colour).
    """
    from PIL import Image, ImageChops

    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[0] < 255:
        # Has transparency — trim to the visible (non-transparent) pixels.
        return alpha.getbbox()

    # Solid background — assume the top-left pixel is the background colour
    # and trim everything that matches it.
    rgb = img.convert("RGB")
    bg = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
    diff = ImageChops.difference(rgb, bg)
    return diff.getbbox()


def _is_placeholder(img) -> bool:
    """Best-effort reject of a blank tile or logo.dev's generated monogram
    fallback.

    The fallback is a flat-colour tile with a single initial: one
    background colour covers almost the whole image and there are only a
    handful of genuinely-present colours. We count *exact* colours at
    native resolution (no resampling, which would smear in anti-alias
    colours and hide the signal). This is a heuristic safety net — the
    verified-domain registry is the primary guard, so it stays
    conservative to avoid rejecting legitimately simple logos.
    """
    rgb = img.convert("RGB")
    colors = rgb.getcolors(maxcolors=8192)
    if colors is None:
        return False  # lots of distinct colours -> a real, rich image
    total = sum(c for c, _ in colors)
    if total == 0:
        return True
    dominant = max(c for c, _ in colors)
    # Colours covering at least 1% of the image — the "real" colours.
    significant = sum(1 for c, _ in colors if c / total >= 0.01)
    # Near-uniform tile (blank, or a tiny glyph on a flat background).
    return dominant / total >= 0.96 and significant <= 3


def _process(png_bytes: bytes, box_h: int) -> str | None:
    """Validate -> trim -> scale to box height -> return a data: URI."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(png_bytes))
        img.load()
    except Exception as e:
        log.info("logo.dev: not a decodable image (%s)", e)
        return None

    w, h = img.size
    if max(w, h) < _MIN_DIM:
        log.info("logo.dev: image too small (%dx%d)", w, h)
        return None

    # Fully transparent?
    rgba = img.convert("RGBA")
    if rgba.getchannel("A").getextrema()[1] == 0:
        log.info("logo.dev: fully transparent image rejected")
        return None

    if _is_placeholder(img):
        log.info("logo.dev: looks like a generated monogram placeholder, rejected")
        return None

    # Trim surrounding whitespace / transparent border.
    bbox = _bbox_of_content(img)
    if bbox:
        rgba = rgba.crop(bbox)

    w, h = rgba.size
    if h == 0 or w == 0:
        return None
    if max(w, h) / max(1, min(w, h)) > _MAX_ASPECT:
        log.info("logo.dev: aspect ratio %dx%d out of range, rejected", w, h)
        return None

    # Scale to the cover box height (keep aspect), don't upscale past 2x.
    scale = min(box_h / h, 2.0)
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    rgba = rgba.resize(new_size, Image.LANCZOS)

    out = io.BytesIO()
    rgba.save(out, format="PNG", optimize=True)
    b64 = base64.b64encode(out.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def logo_data_uri(company: str, *, box_h: int = 96) -> str | None:
    """Return a `data:image/png;base64,...` URI for the company's logo, or
    None to signal the caller should fall back to the text wordmark.

    box_h: target rendered height in px on the cover (image is scaled to it).
    """
    token = _token()
    if not token:
        log.info("LOGODEV_TOKEN not set — skipping logo, using text fallback")
        return None

    domain = domain_for(company)
    if not domain:
        log.info("no verified domain for %r — using text fallback", company)
        return None

    png = _fetch_png(domain, token)
    if not png:
        return None

    return _process(png, box_h)


def _main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    out_path = None
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == "--out" and i + 1 < len(argv):
            out_path = argv[i + 1]
            i += 2
        else:
            args.append(argv[i])
            i += 1
    if not args:
        print("usage: python -m tool.company_logo <company-or-domain> [--out file.png]")
        return 2

    company = args[0]
    print(f"normalized: {_normalize(company)!r}  domain: {domain_for(company)!r}")
    uri = logo_data_uri(company)
    if not uri:
        print("RESULT: no logo (text fallback would be used)")
        return 1
    print(f"RESULT: logo data URI, {len(uri)} chars")
    if out_path:
        header, b64 = uri.split(",", 1)
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(b64))
        print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
