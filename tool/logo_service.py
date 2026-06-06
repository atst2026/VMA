#!/usr/bin/env python3
"""The single, deterministic, fail-safe company-logo service.

Design contract (non-negotiable)
--------------------------------
Every pitch pack MUST carry the CORRECT company logo. The three historical
failure modes — wrong company, right company/wrong logo, missing logo replaced
by text — are eliminated by construction:

* Resolution starts from a CANONICAL IDENTITY (tool/company_identity), never a
  raw name. The logo is keyed on the company's VERIFIED DOMAIN or a verified
  logo URL — there is no search, no fuzzy matching, no "guessing".
* Deterministic, ordered pipeline (see ``get_logo``). The sources are a verified
  registry asset and the company's OWN website (its homepage-declared icon and
  well-known apple-touch-icon path on the verified domain) — plus optional
  domain-keyed services where configured. Every URL is keyed on the verified
  identity; there is no search and no fuzzy matching.
* Every candidate is VALIDATED (HTTP 200, real image bytes, not empty, logo
  host matches the company domain where applicable) before it is accepted.
* If nothing validates, ``get_logo`` RAISES ``LogoResolutionError``. It never
  returns a placeholder, a wordmark, or a "best guess". Generation fails — by
  design it is better to fail than to ship an incorrect pack.
* Results are cached per company id so we do not re-fetch on every generation.

This is the ONLY place pitch-pack logo logic lives; all generation routes go
through ``get_logo``.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from tool import company_identity
from tool.company_identity import Company, UnknownCompanyError  # re-exported
from tool.sources._http import get
from tool.state_paths import state_dir

log = logging.getLogger("logo_service")

# Re-export so callers catch one place's exceptions.
__all__ = ["get_logo", "ResolvedLogo", "LogoError", "LogoResolutionError",
           "LogoValidationError", "UnknownCompanyError"]


class LogoError(Exception):
    """Base class for all logo failures."""


class LogoResolutionError(LogoError):
    """No valid logo could be resolved for the company — generation must stop."""


class LogoValidationError(LogoError):
    """A candidate logo failed validation."""


@dataclass(frozen=True)
class ResolvedLogo:
    company_id: str
    company_name: str
    url: str
    data: bytes
    content_type: str
    source: str            # "cache" | "registry" | "domain:clearbit" | ...

    def data_uri(self) -> str:
        return f"data:{self.content_type};base64,{base64.b64encode(self.data).decode('ascii')}"


# ---- configuration -----------------------------------------------------
# The primary deterministic source is the company's OWN website (its declared
# icon + well-known apple-touch-icon path), because that is what is reliably
# reachable. Third-party logo services are OPTIONAL and OFF by default: the
# historical "Clearbit-style" host (logo.clearbit.com) no longer resolves
# (Clearbit retired the free Logo API), so enabling it just adds a guaranteed
# miss. Set LOGO_CLEARBIT_BASE / LOGO_DEV_TOKEN to enable them where they work.
_CLEARBIT = (os.environ.get("LOGO_CLEARBIT_BASE") or "").strip()
_LOGODEV_TOKEN = (os.environ.get("LOGO_DEV_TOKEN") or "").strip()

_CACHE_TTL_SECONDS = int(os.environ.get("LOGO_CACHE_TTL", str(30 * 24 * 3600)))
_CACHE_PATH = state_dir() / "logo_cache.json"

# Logo hosts whose URL is keyed on the verified domain (so a domain match is
# not applicable — the source is deterministic by construction).
_TRUSTED_LOGO_REGISTRABLES = {
    "clearbit.com", "logo.dev", "brandfetch.io", "wikimedia.org",
}
_TWO_LEVEL_TLDS = {
    "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk", "ltd.uk", "plc.uk",
    "com.au", "co.nz", "co.za", "com.sg",
}
_MIN_LOGO_BYTES = 256          # smaller than this is a tracking pixel / placeholder


# ======================================================================
# small pure helpers
# ======================================================================
def _registrable(host: str) -> str:
    host = (host or "").lower().strip().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LEVEL_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _sniff_image(content: bytes, content_type: str) -> str | None:
    """Return a normalised image MIME if the bytes are a real image, else None.
    Sniffs magic bytes (so an HTML error page served as image/png is rejected)
    and accepts SVG by structure."""
    if not content:
        return None
    if content[:8].startswith(b"\x89PNG"):
        return "image/png"
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if content[:4] == b"GIF8":
        return "image/gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    # SVG by structure (must actually contain an <svg> element — a content-type
    # of image/svg+xml on an HTML error page is NOT trusted).
    if b"<svg" in content[:512].lstrip().lower():
        return "image/svg+xml"
    return None


def _validate(url: str, content: bytes, content_type: str,
              company: Company, enforce_domain: bool = True) -> tuple[str, str]:
    """Validate a fetched candidate. Returns (mime, "") on success, or
    ("", reason) on failure. Enforces: real image, non-empty/not a placeholder,
    and — when ``enforce_domain`` — that the logo host is the company's own
    domain or a trusted provider. ``enforce_domain`` is False for assets the
    verified domain's own homepage explicitly DECLARED (authoritative by
    provenance, even when hosted on the company's CDN)."""
    if not content or len(content) < _MIN_LOGO_BYTES:
        return "", f"empty/too-small ({len(content) if content else 0} bytes)"
    mime = _sniff_image(content, content_type)
    if not mime:
        return "", f"not an image (content-type={content_type!r})"
    if enforce_domain:
        host_reg = _registrable(urlparse(url).netloc)
        if (host_reg not in _TRUSTED_LOGO_REGISTRABLES
                and company.domain and host_reg != _registrable(company.domain)):
            return "", (f"logo host {host_reg!r} does not match company domain "
                        f"{_registrable(company.domain)!r}")
    return mime, ""


def _fetch(url: str) -> tuple[bytes, str] | None:
    """GET a candidate logo URL. Returns (content, content_type) only on a
    clean HTTP 200, else None (logged)."""
    r = get(url, timeout=12)
    if r is None:
        log.info("logo fetch: no response from %s", url)
        return None
    if r.status_code != 200:
        log.info("logo fetch: %s -> HTTP %s", url, r.status_code)
        return None
    return r.content, (r.headers.get("content-type") or "")


# ======================================================================
# cache  (per company id; JSON in the state dir)
# ======================================================================
def _load_cache() -> dict:
    try:
        return json.loads(_CACHE_PATH.read_text())
    except Exception:
        return {}


def _cache_get(company: Company) -> ResolvedLogo | None:
    entry = _load_cache().get(company.id)
    if not entry:
        return None
    if (time.time() - entry.get("ts", 0)) > _CACHE_TTL_SECONDS:
        return None
    try:
        data = base64.b64decode(entry["b64"])
    except Exception:
        return None
    # re-validate the cached bytes offline (cheap) so a corrupt entry can't
    # ship. Host was already checked when stored, so don't re-enforce it here
    # (a cached CDN-hosted declared icon would otherwise be dropped every time).
    if _validate(entry["url"], data, entry["content_type"], company,
                 enforce_domain=False)[0]:
        return ResolvedLogo(company.id, company.name, entry["url"], data,
                            entry["content_type"], "cache")
    return None


def _cache_put(resolved: ResolvedLogo) -> None:
    try:
        cache = _load_cache()
        cache[resolved.company_id] = {
            "url": resolved.url,
            "content_type": resolved.content_type,
            "b64": base64.b64encode(resolved.data).decode("ascii"),
            "ts": int(time.time()),
        }
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(cache))
    except Exception as e:           # caching is best-effort; never block on it
        log.info("logo cache write failed: %s", e)


def invalidate(name_or_id: str) -> None:
    """Drop a company's cached logo so the next generation re-resolves it."""
    try:
        company = company_identity.resolve(name_or_id)
        cache = _load_cache()
        if cache.pop(company.id, None) is not None:
            _CACHE_PATH.write_text(json.dumps(cache))
    except Exception as e:
        log.info("logo cache invalidate failed for %r: %s", name_or_id, e)


# ======================================================================
# the pipeline
# ======================================================================
def _homepage_declared_icon(domain: str) -> str | None:
    """The icon URL the company's OWN homepage explicitly declares
    (<link rel="apple-touch-icon"> preferred, then mask/svg icon, then a sized
    icon). This is the site's own canonical brand mark — a declaration, not a
    scrape of arbitrary <img>s, so it can't pick a partner/award badge. Returns
    an absolute URL or None."""
    r = get(f"https://{domain}", timeout=12)
    if r is None or r.status_code != 200 or not r.text:
        return None
    best_rank, best_url = 0, None
    for m in re.finditer(r"<link\b[^>]*>", r.text, re.I):
        tag = m.group(0)
        rel_m = re.search(r"""rel\s*=\s*["']([^"']+)""", tag, re.I)
        href_m = re.search(r"""href\s*=\s*["']([^"']+)""", tag, re.I)
        if not rel_m or not href_m:
            continue
        rel = rel_m.group(1).lower()
        if "icon" not in rel:
            continue
        url = urljoin(f"https://{domain}/", href_m.group(1))
        low = url.lower()
        rank = (4 if "apple-touch-icon" in rel
                else 3 if ("mask-icon" in rel or low.endswith(".svg"))
                else 2 if low.endswith(".png")
                else 1)
        if rank > best_rank:
            best_rank, best_url = rank, url
    return best_url


def get_logo(name_or_id: str) -> ResolvedLogo:
    """Resolve the CORRECT logo for a company, or raise.

    Pipeline (first validated hit wins; sources tried LAZILY, in order):
      1. canonical identity (raises UnknownCompanyError if not known);
      2. cache (validated) for the company id;
      3. verified registry logo asset;
      4. the company's own homepage-DECLARED icon (apple-touch-icon etc.);
      5. deterministic well-known paths on the company's own domain
         (/apple-touch-icon.png …), plus any configured service (logo.dev /
         Clearbit-style) where it resolves;
      (no domain and no asset -> enrichment, which here means "add a verified
       registry entry" — there is no safe automatic derivation);
      6. otherwise raise LogoResolutionError.

    Never guesses, never degrades to text. The chosen source is logged."""
    company = company_identity.resolve(name_or_id)   # UnknownCompanyError

    cached = _cache_get(company)
    if cached is not None:
        log.info("logo for %s (%s): cache hit (%s)", company.name, company.id, cached.url)
        return cached

    if not (company.logo_url or company.domain):
        raise LogoResolutionError(
            f"{company.name} ({company.id}) has no verified domain or logo asset; "
            f"add one to tool/company_identity.py")

    failures: list[str] = []

    def _attempt(source: str, url: str, authoritative: bool) -> ResolvedLogo | None:
        fetched = _fetch(url)
        if not fetched:
            failures.append(f"{source}:{url} (no 200)")
            return None
        content, content_type = fetched
        mime, reason = _validate(url, content, content_type, company,
                                 enforce_domain=not authoritative)
        if not mime:
            failures.append(f"{source}:{url} ({reason})")
            log.info("logo for %s: rejected %s — %s", company.name, url, reason)
            return None
        resolved = ResolvedLogo(company.id, company.name, url, content, mime, source)
        _cache_put(resolved)
        log.info("logo for %s (%s): %s via %s (%d bytes, %s)",
                 company.name, company.id, url, source, len(content), mime)
        return resolved

    # 3. pinned verified registry asset
    if company.logo_url:
        r = _attempt("registry", company.logo_url, authoritative=False)
        if r:
            return r

    d = company.domain
    if d:
        # 4. the company's OWN homepage-declared icon (authoritative by provenance)
        declared = _homepage_declared_icon(d)
        if declared:
            r = _attempt("domain:declared", declared, authoritative=True)
            if r:
                return r
        # 5. deterministic well-known paths on the company's own domain
        for path in ("apple-touch-icon.png", "apple-touch-icon-precomposed.png"):
            r = _attempt("domain:apple-touch", f"https://{d}/{path}", authoritative=False)
            if r:
                return r
        # optional configured services (off unless they resolve in this deploy)
        if _LOGODEV_TOKEN:
            r = _attempt("domain:logodev",
                         f"https://img.logo.dev/{d}?token={_LOGODEV_TOKEN}&format=png&size=512",
                         authoritative=False)
            if r:
                return r
        if _CLEARBIT:
            r = _attempt("domain:clearbit", f"{_CLEARBIT}/{d}", authoritative=False)
            if r:
                return r

    raise LogoResolutionError(
        f"no valid logo for {company.name} ({company.id}); tried: " + "; ".join(failures))
