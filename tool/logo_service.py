#!/usr/bin/env python3
"""The single company-logo service: pull a company's logo from its own website.

A BD lead can name ANY company — there is no fixed list — so every pitch pack
must be able to show that company's logo, taken from the company's own site.
Resolution is ordered by confidence and every candidate is validated, so we get
the RIGHT logo without shipping a wrong one:

* VERIFIED FAST-PATH — a curated identity (tool/company_identity) gives a
  hand-verified domain / logo asset for the companies we know. Highest
  confidence, tried first.
* DERIVED PATH (any other name) — find the company's official site from its
  name (tool/company_web): guess the domain and confirm it against the live
  homepage, falling back to a keyless web search, behind a CONFIDENCE GATE so a
  stranger's domain is never accepted. The logo is then read off that landing
  page (the header wordmark <img>, then the site-declared icons).
* Every candidate is VALIDATED (HTTP 200, real image bytes, not empty; on
  non-authoritative sources the host must match the company domain) before it is
  accepted. Logos read from a company's OWN homepage are authoritative by
  provenance (the host check is relaxed for them).
* Results are cached per company id so we do not re-fetch on every generation.
* If nothing validates, ``get_logo`` RAISES ``LogoResolutionError``. The caller
  (tool/pitch_proposal) then renders a clean text wordmark of the company name,
  so a pack is always produced — never a wrong logo, never a failed pack.

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
from urllib.parse import urlparse

from tool import company_identity
from tool import company_web
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
def _try(company: Company, source: str, url: str, authoritative: bool,
         failures: list[str]) -> ResolvedLogo | None:
    """Fetch, validate and (on success) cache one candidate URL. ``authoritative``
    is True for assets the company's own homepage declared/carries (provenance,
    so the host check is relaxed); False for guessed/well-known/service URLs."""
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


def _homepage(domain: str) -> tuple[str | None, str]:
    """Fetch a domain's homepage. Returns (html_or_None, base_url) — base_url is
    the post-redirect URL when available so relative logo hrefs resolve right."""
    r = get(f"https://{domain}", timeout=12)
    base = getattr(r, "url", None) or f"https://{domain}"
    if r is None or getattr(r, "status_code", 0) != 200 or not getattr(r, "text", ""):
        return None, f"https://{domain}"
    return r.text, base


def _from_domain(company: Company, domain: str, prefix: str,
                 failures: list[str]) -> ResolvedLogo | None:
    """The logo from a company's OWN site: the landing-page logo read out of the
    homepage HTML (header wordmark <img>, then declared icons — authoritative by
    provenance), then the deterministic well-known apple-touch paths. ``prefix``
    labels the source ('domain' for verified identities, 'derived' otherwise)."""
    html, base = _homepage(domain)
    if html:
        for kind, url in company_web.logo_urls_from_html(html, base):
            src = f"{prefix}:{'logo-img' if kind == 'img' else 'declared'}"
            r = _try(company, src, url, authoritative=True, failures=failures)
            if r:
                return r
    for path in ("apple-touch-icon.png", "apple-touch-icon-precomposed.png"):
        r = _try(company, f"{prefix}:apple-touch", f"https://{domain}/{path}",
                 authoritative=False, failures=failures)
        if r:
            return r
    return None


def _from_services(company: Company, domain: str,
                   failures: list[str]) -> ResolvedLogo | None:
    """Optional domain-keyed logo services — OFF unless configured for the deploy
    (see LOGO_DEV_TOKEN / LOGO_CLEARBIT_BASE)."""
    if _LOGODEV_TOKEN:
        r = _try(company, "domain:logodev",
                 f"https://img.logo.dev/{domain}?token={_LOGODEV_TOKEN}&format=png&size=512",
                 authoritative=False, failures=failures)
        if r:
            return r
    if _CLEARBIT:
        r = _try(company, "domain:clearbit", f"{_CLEARBIT}/{domain}",
                 authoritative=False, failures=failures)
        if r:
            return r
    return None


_DDG_HTML = "https://html.duckduckgo.com/html/"


def _search_domains(name: str) -> list[str]:
    """Keyless name->domain fallback: the organic result domains for an official-
    site search (directories/socials/press filtered out by company_web)."""
    try:
        r = get(_DDG_HTML, params={"q": f"{name} official website"}, timeout=12)
    except Exception as e:                          # network is best-effort
        log.info("logo search failed for %r: %s", name, e)
        return []
    if r is None or getattr(r, "status_code", 0) != 200 or not getattr(r, "text", ""):
        return []
    return company_web.search_result_domains(r.text)


def _derive_logo(name: str) -> ResolvedLogo | None:
    """Best-effort logo for a company NOT in the verified registry. Finds the
    official site from the name (guess then web search), behind a confidence
    gate (company_web.name_matches_site) so a wrong company's logo is never
    used, and reads the logo off that site's landing page. None if nothing
    confident resolves (caller then falls back to a text wordmark)."""
    slug = company_identity.slugify(name) or "company"
    stub = Company(slug, name, None)
    cached = _cache_get(stub)
    if cached is not None:
        log.info("logo for %r: cache hit (derived, %s)", name, cached.url)
        return cached

    failures: list[str] = []
    tried: set[str] = set()

    def _attempt_domain(raw_domain: str, via: str) -> ResolvedLogo | None:
        domain = company_web.registrable(raw_domain)
        if not domain or "." not in domain or domain in tried \
                or company_web.is_excluded_host(domain):
            return None
        tried.add(domain)
        html, base = _homepage(domain)
        if not html:
            failures.append(f"{via}:{domain} (no homepage)")
            return None
        if not company_web.name_matches_site(name, domain, html):
            failures.append(f"{via}:{domain} (name/site mismatch)")
            return None
        # Confident match — read the logo off this landing page.
        company = Company(slug, name, domain)
        for kind, url in company_web.logo_urls_from_html(html, base):
            src = f"derived:{'logo-img' if kind == 'img' else 'declared'}"
            r = _try(company, src, url, authoritative=True, failures=failures)
            if r:
                return r
        for path in ("apple-touch-icon.png", "apple-touch-icon-precomposed.png"):
            r = _try(company, "derived:apple-touch", f"https://{domain}/{path}",
                     authoritative=False, failures=failures)
            if r:
                return r
        return None

    for dom in company_web.candidate_domains(name)[:8]:   # a. guessed domains
        r = _attempt_domain(dom, "guess")
        if r:
            return r
    for dom in _search_domains(name):                      # b. web-search fallback
        r = _attempt_domain(dom, "search")
        if r:
            return r

    log.info("derive: no confident site/logo for %r; tried: %s",
             name, "; ".join(failures[:8]))
    return None


def get_logo(name_or_id: str) -> ResolvedLogo:
    """Resolve the company's logo from its own website, or raise.

    Order (first validated hit wins; sources tried LAZILY):
      1. VERIFIED identity (tool/company_identity) — cache, pinned asset, the
         company's own homepage logo on the verified domain, well-known paths,
         optional services;
      2. otherwise DERIVE the site from the name (guess + web search, behind a
         confidence gate) and read the logo off its landing page;
      3. if nothing validates, raise LogoResolutionError. The caller falls back
         to a text wordmark — this never returns a wrong/placeholder logo.

    The chosen source is logged."""
    name = (name_or_id or "").strip()

    # 1. verified registry identity — highest confidence
    try:
        company = company_identity.resolve(name)
    except UnknownCompanyError:
        company = None

    if company is not None:
        cached = _cache_get(company)
        if cached is not None:
            log.info("logo for %s (%s): cache hit (%s)",
                     company.name, company.id, cached.url)
            return cached
        if not (company.logo_url or company.domain):
            raise LogoResolutionError(
                f"{company.name} ({company.id}) has no verified domain or logo "
                f"asset; add one to tool/company_identity.py")
        failures: list[str] = []
        if company.logo_url:
            r = _try(company, "registry", company.logo_url,
                     authoritative=False, failures=failures)
            if r:
                return r
        if company.domain:
            r = (_from_domain(company, company.domain, "domain", failures)
                 or _from_services(company, company.domain, failures))
            if r:
                return r
        raise LogoResolutionError(
            f"no valid logo for {company.name} ({company.id}); tried: "
            + "; ".join(failures))

    # 2. unknown company — derive from the web (confidence-gated, best-effort)
    resolved = _derive_logo(name)
    if resolved is not None:
        return resolved
    raise LogoResolutionError(
        f"no confident logo for unknown company {name!r} "
        f"(no matching official site found)")
