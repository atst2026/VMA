"""Direct company-site harvesting — names, titles and PUBLISHED emails
from the company's own pages, with no third-party dependency.

This is the free backbone under both contact pipelines. The original
leadership-page source routed through Bright Data -> Google SERP, which
silently no-ops without a configured zone (and produced exactly the
zero-resolution runs the logs show). Corporate sites themselves rarely
block a plain browser-UA GET — so fetch THEM directly:

  harvest(company) ->
    {domain, people: [{name, title}], emails: [{email, name, title,
     url}], pages: [urls actually read], at}

  - domain comes from the hand registry / Wikidata (company_identity /
    company_domain), falling back to a normalised .com/.co.uk guess;
  - the homepage is fetched first and its nav is mined for
    leadership/team/press/contact links, topped up with canned paths;
  - people are parsed with the resolver's own name+title extractor
    (so the same title taxonomy gates what counts as a hit);
  - emails are page-published addresses with a person attribution where
    the page provides one — application/generic inboxes excluded for
    attribution but personal pairs kept for FORMAT inference;
  - everything is cached per company for CACHE_DAYS (state file), so a
    company costs at most MAX_FETCHES_PER_COMPANY HTTP gets per
    fortnight across every caller.

Never raises; empty results are honest results.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

from tool.state_paths import state_dir

log = logging.getLogger("brief.contacts.site")

CACHE_DAYS = 14
MAX_FETCHES_PER_COMPANY = 6
_CACHE_MAX = 400

# Canned paths worth trying when the homepage nav gives nothing.
_LEADERSHIP_PATHS = (
    "/leadership", "/about/leadership", "/about-us/leadership",
    "/our-team", "/about/our-team", "/team", "/about/management",
    "/about-us/our-people", "/who-we-are", "/about",
)
_PRESS_PATHS = (
    "/media", "/press", "/media-enquiries", "/newsroom", "/press-office",
    "/media-centre", "/contact", "/contact-us",
)
# Homepage links whose text/href suggest the pages we want.
_LEAD_LINK_RX = re.compile(
    r"(?i)(leadership|our[\s\-]?team|our[\s\-]?people|management[\s\-]?team|"
    r"executive|board[\s\-]?of[\s\-]?directors|who[\s\-]?we[\s\-]?are)")
_PRESS_LINK_RX = re.compile(
    r"(?i)(media|press|newsroom|journalist|contact)")
_HREF_RX = re.compile(
    r"""<a[^>]+href=["']([^"'#?]+)[^"']*["'][^>]*>(.{0,120}?)</a>""",
    re.I | re.S)

_EMAIL_RX = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._%+\-']*@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Same person-name-near-the-address attribution the RNS parser uses.
_NAME_BEFORE_RX = re.compile(
    r"([A-Z][a-z]*(?:[A-Z][a-z]+|['’\-][A-Za-z]+)*"
    r"(?:\s+[A-Z][a-z]*(?:[A-Z][a-z]+|['’\-][A-Za-z]+)*){1,2})"
    r"[^@]{0,90}$")


def _cache_file():
    return state_dir() / "site_pages_cache.json"


def _load_cache() -> dict:
    try:
        f = _cache_file()
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}


def _save_cache(d: dict) -> None:
    try:
        if len(d) > _CACHE_MAX:
            for k in list(d)[:len(d) - _CACHE_MAX]:
                d.pop(k, None)
        _cache_file().write_text(json.dumps(d))
    except Exception:
        pass


def _fresh(rec: dict) -> bool:
    try:
        t = datetime.fromisoformat(rec.get("at") or "")
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t) < timedelta(days=CACHE_DAYS)
    except Exception:
        return False


# A segment that is EXACTLY a person's name (2-3 capitalised words,
# no role/org words) — used to pair sibling-element card layouts where
# the name and the title live in adjacent tags.
_PERSON_SEG_RX = re.compile(
    r"[A-Z][a-z]*(?:[A-Z][a-z]+|['’\-][A-Za-z]+)*"
    r"(?:\s+[A-Z][a-z]*(?:[A-Z][a-z]+|['’\-][A-Za-z]+)*){1,2}")
_NOT_A_PERSON = frozenset(w.lower() for w in (
    "Head", "Director", "Chief", "Group", "Officer", "Executive",
    "Manager", "Team", "Board", "Leadership", "Communications",
    "Corporate", "Affairs", "Marketing", "Investor", "Relations",
    "People", "Media", "Press", "Contact", "About", "Our", "The",
    "Senior", "Global", "Company", "Limited", "Plc", "Holdings",
    "View", "Read", "More", "Profile", "Linkedin", "Email",
))


def _seg_person(seg: str) -> str | None:
    seg = seg.strip(" -–—·•|,")
    m = _PERSON_SEG_RX.fullmatch(seg)
    if not m:
        return None
    words = seg.split()
    if any(w.lower() in _NOT_A_PERSON for w in words):
        return None
    return seg


def _paired_people(text: str) -> list[dict]:
    """Real leadership pages put the name and the title in SIBLING
    elements (<h3>Jane Smith</h3><p>Chief Comms Officer</p>) — the
    inline 'Name — Title' regex never sees them as one string. The
    block boundaries we inserted (' . ') turn that into adjacent
    segments; pair a pure-name segment with a titled neighbour (next
    first, then previous, for title-first cards)."""
    from tool.contacts.resolver import classify_title
    segs = [s.strip(" .·•|-–—,") for s in re.split(r"\s*\.\s+", text)]
    segs = [s for s in segs if s]
    out = []
    for i, seg in enumerate(segs):
        name = _seg_person(seg)
        if not name:
            continue
        for j in (i + 1, i + 2, i - 1):
            if j < 0 or j >= len(segs) or j == i:
                continue
            cand = segs[j].strip(" -–—·•|,")
            if not (2 < len(cand) <= 90) or _seg_person(cand):
                continue
            slot = classify_title(cand)
            if slot:
                out.append({"name": name, "title": cand, "slot": slot})
                break
    return out


def direct_fetch(url: str) -> str | None:
    """Plain browser-UA GET via the shared HTTP helper. None on any
    failure — a blocked site is an honest miss, never an error."""
    try:
        from tool.sources._http import get
        r = get(url, timeout=15)
        if r is None or getattr(r, "status_code", 0) != 200:
            return None
        ctype = (r.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text" not in ctype:
            return None
        return r.text[:600_000]
    except Exception:
        return None


def _resolve_domain(company: str) -> str | None:
    try:
        from tool.company_domain import resolve_domain
        d = resolve_domain(company)
        if d:
            return d
    except Exception:
        pass
    s = re.sub(r"(?i)\b(plc|ltd|limited|group|holdings|llp|inc)\b", "",
               company or "").strip()
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return f"{s}.com" if len(s) >= 3 else None


def _page_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = text.replace("&#64;", "@").replace("&commat;", "@")
    text = re.sub(r"\s*\[\s*at\s*\]\s*|\s+at\s+(?=[a-z0-9\-]+\s*(\.|\[dot\]))",
                  "@", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def _nav_links(html: str, base: str, rx) -> list[str]:
    out, seen = [], set()
    host = urlparse(base).netloc
    for m in _HREF_RX.finditer(html or ""):
        href, label = m.group(1).strip(), re.sub(r"<[^>]+>", " ", m.group(2))
        if not (rx.search(href) or rx.search(label)):
            continue
        url = urljoin(base, href)
        p = urlparse(url)
        if p.netloc != host or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out[:3]


def _emails_from_text(text: str) -> list[dict]:
    try:
        from tool.contacts.ad_contact import is_application_inbox
    except Exception:
        def is_application_inbox(e):  # type: ignore[misc]
            return False
    out, seen = [], set()
    for m in _EMAIL_RX.finditer(text):
        email = m.group(0).strip(".,;:'")
        low = email.lower()
        if low in seen or low.endswith((".png", ".jpg", ".svg", ".gif")):
            continue
        seen.add(low)
        if is_application_inbox(email):
            continue
        name = ""
        nm = _NAME_BEFORE_RX.search(text[max(0, m.start() - 110):m.start()])
        if nm:
            name = nm.group(1).strip()
        out.append({"email": email, "name": name})
    return out


def harvest(company: str, cache: dict | None = None,
            fetch=None) -> dict:
    """All people + published emails the company's own site offers,
    cached. Returns {domain, people, emails, pages, at}; the empty
    shape on any failure. Never raises."""
    empty = {"domain": "", "people": [], "emails": [], "pages": [],
             "at": datetime.now(timezone.utc).isoformat()}
    try:
        company = (company or "").strip()
        if not company or company == "—":
            return empty
        own_cache = cache is None
        if own_cache:
            cache = _load_cache()
        key = company.lower()
        hit = cache.get(key)
        if hit and _fresh(hit):
            return hit

        fetch = fetch or direct_fetch
        domain = _resolve_domain(company)
        if not domain:
            cache[key] = empty
            if own_cache:
                _save_cache(cache)
            return empty
        base = f"https://{domain}"
        fetches = 0
        people: list[dict] = []
        emails: list[dict] = []
        pages: list[str] = []
        seen_people: set = set()
        seen_emails: set = set()

        def _absorb(url: str, html: str) -> None:
            from tool.contacts.resolver import _extract_name_title_pairs
            pages.append(url)
            # Block-element boundaries become hard stops (".") so one
            # person's card can't bleed into the next when tags are
            # stripped — the extractor's title pattern halts at "." and
            # its own whitespace collapsing would eat plain newlines.
            blocky = re.sub(
                r"(?i)</(h[1-6]|p|li|div|td|th|tr|section|article|"
                r"figcaption|span)>|<br\s*/?>", " . ", html)
            found = [{"name": c.name, "title": c.role_title,
                      "slot": getattr(c, "_slot", None)}
                     for c in _extract_name_title_pairs(blocky)]
            found += _paired_people(_page_text(blocky))
            for p in found:
                k = p["name"].lower()
                if k not in seen_people:
                    seen_people.add(k)
                    people.append({**p, "url": url})
            for e in _emails_from_text(_page_text(html)):
                if e["email"].lower() not in seen_emails:
                    seen_emails.add(e["email"].lower())
                    emails.append({**e, "url": url})

        home = fetch(base)
        fetches += 1
        queue: list[str] = []
        if home:
            _absorb(base, home)
            queue += _nav_links(home, base, _LEAD_LINK_RX)
            queue += _nav_links(home, base, _PRESS_LINK_RX)
        queue += [base + p for p in _LEADERSHIP_PATHS]
        queue += [base + p for p in _PRESS_PATHS]
        tried = {base}
        for url in queue:
            if fetches >= MAX_FETCHES_PER_COMPANY:
                break
            if len(people) >= 4 and emails:
                break          # plenty — stop spending
            if url in tried:
                continue
            tried.add(url)
            html = fetch(url)
            fetches += 1
            if html:
                _absorb(url, html)

        rec = {"domain": domain, "people": people, "emails": emails,
               "pages": pages, "at": datetime.now(timezone.utc).isoformat()}
        cache[key] = rec
        if own_cache:
            _save_cache(cache)
        if people or emails:
            log.info("site harvest %s (%s): %d people, %d emails from "
                     "%d pages", company, domain, len(people),
                     len(emails), len(pages))
        return rec
    except Exception as e:
        log.info("site harvest skipped for %s (%s)", company, e)
        return empty
