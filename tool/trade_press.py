"""Trade-press warm-call trigger.

Scrapes UK comms trade publications (PR Week, Provoke, Corp Comms,
Campaign) once a day looking for named senior-comms people Sara is
tracking. When a tracked person is mentioned — quoted, awarded,
promoted, featured — emit a 'warm-call trigger' event with a pre-
drafted opener referencing the article.

Why this works
==============
Cold outreach to senior UK comms leaders converts at ~2% (industry
baseline). A same-day call referencing a public mention plausibly runs
15-30% within 48h of publication. It's the cheapest single new fee
mechanic available without integrating JobAdder.

How precision is preserved
==========================
Three guards prevent the channel decaying through over-use:

  1. Entity resolution: free-text "John Smith" only fires if it maps
     to a canonical tracked-contact record (state file).
  2. Article-level dedup: same article URL = one event, ever.
  3. Suppression window: same (person, source) pair won't re-fire
     within SUPPRESS_DAYS (60d default), even on a different article,
     so Sara isn't re-opening on the same hook three times in a row.

Sources can break (publishers killed RSS in May 2026, see
rss_feeds.py); the design treats sources as pluggable and tolerates
any one failing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

log = logging.getLogger("brief.trade_press")

from tool.state_paths import state_root
STATE_DIR = state_root()
EVENTS_FILE = STATE_DIR / "trade_press_events.json"
SUPPRESS_FILE = STATE_DIR / "trade_press_suppression.json"
TRACKED_FILE = STATE_DIR / "trade_press_tracked.json"   # canonical contact list

SUPPRESS_DAYS = 60          # don't re-fire on same (person, source) within this window
MAX_EVENTS_KEEP = 200       # cap stored events so the panel stays usable

# ----- sources --------------------------------------------------------
# Each source is a (key, name, url, kind) row. "kind" tells the fetcher
# which adapter to use. RSS adapters reuse the existing tool.sources
# parser; "homepage_html" hits an editorial index page and pulls
# article anchors. Missing/broken sources are skipped silently so one
# publisher killing their feed doesn't take the whole scour down.
_COMMS_SOURCES = [
    {"key": "corpcomms", "name": "CorpComms Magazine",
     "url":  "https://www.corpcommsmagazine.co.uk/feed/",
     "kind": "rss"},
    {"key": "prweek_uk", "name": "PR Week UK",
     "url":  "https://www.prweek.com/uk/news",
     "kind": "homepage_html"},
    {"key": "provoke", "name": "Provoke Media",
     "url":  "https://www.provokemedia.com/latest/articles",
     "kind": "homepage_html"},
    {"key": "campaign_uk", "name": "Campaign UK",
     "url":  "https://www.campaignlive.co.uk/news",
     "kind": "homepage_html"},
]

# Marketing desk (FIRST DRAFT) — UK marketing trade press. Graceful-skip if
# a feed path moves (same as the comms set). Review with the marketing team.
_MARKETING_SOURCES = [
    {"key": "marketing_week", "name": "Marketing Week",
     "url":  "https://www.marketingweek.com/feed/",
     "kind": "rss"},
    {"key": "the_drum", "name": "The Drum",
     "url":  "https://www.thedrum.com/rss.xml",
     "kind": "rss"},
    {"key": "marketing_beat", "name": "Marketing Beat",
     "url":  "https://www.marketingbeat.co.uk/feed/",
     "kind": "rss"},
    {"key": "campaign_uk", "name": "Campaign UK",
     "url":  "https://www.campaignlive.co.uk/news",
     "kind": "homepage_html"},
]

from tool.profiles import active_profile as _active_profile
SOURCES = (_MARKETING_SOURCES if _active_profile().key == "marketing"
           else _COMMS_SOURCES)

# Hook-type classification — drives the opener template. Order matters:
# first match wins.
HOOK_PATTERNS = [
    ("award",     re.compile(r"\b(award|wins|winner|honou?red|named.*of the year)\b", re.I)),
    ("promotion", re.compile(r"\b(promote[ds]?|appoint(ed|s|ment)|joins as|new role|new chief)\b", re.I)),
    ("quoted",    re.compile(r"\b(said|told|commented|warned|argued|wrote)\b", re.I)),
    ("featured",  re.compile(r"\b(interview|profile|feature|q\s*&\s*a|byline|opinion|op-?ed)\b", re.I)),
]


# ----- locking + atomic write -----------------------------------------
try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


@contextmanager
def _locked(path: Path):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with _LOCK:
        fd = None
        if _HAVE_FCNTL:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fd is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(exist_ok=True, parents=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".tmp",
        dir=str(path.parent), delete=False,
    )
    try:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, str(path))
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


# ----- data model -----------------------------------------------------
@dataclass
class TradePressEvent:
    event_id: str               # stable hash (person_id + article_url)
    person_id: str              # canonical id from tracked list
    person_name: str            # display name
    person_company: str         # current company (from tracked list)
    source_key: str
    source_name: str
    article_url: str
    article_title: str
    article_date: str           # ISO date or empty
    hook_type: str              # award / promotion / quoted / featured
    snippet: str                # short context line for the opener
    opener: str                 # drafted opener text
    detected_at: str            # ISO timestamp
    status: str = "active"      # active / called / dismissed


def _today_iso() -> str:
    return date.today().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----- tracked-contacts list -------------------------------------------
def load_tracked() -> list[dict]:
    """Read the canonical tracked-contacts list. Each entry is:
       {id, name, company, aliases?, role?, linkedin?}. Empty list if
       the file isn't seeded yet — the scour is then a no-op (every
       mention fails entity resolution), which is the correct empty-
       state behaviour."""
    if not TRACKED_FILE.exists():
        return []
    try:
        data = json.loads(TRACKED_FILE.read_text())
        return data if isinstance(data, list) else []
    except Exception as e:
        log.info("trade_press: tracked load failed: %s", e)
        return []


def _build_name_index(tracked: list[dict]) -> dict[str, dict]:
    """Map every name / alias (lower-cased) → contact record. Allows
    O(1) lookup when scanning article text."""
    idx: dict[str, dict] = {}
    for c in tracked:
        names = [c.get("name", "")]
        names.extend(c.get("aliases") or [])
        for n in names:
            n = (n or "").strip().lower()
            if n:
                idx[n] = c
    return idx


# ----- events store ---------------------------------------------------
def _load_events() -> list[dict]:
    if not EVENTS_FILE.exists():
        return []
    try:
        data = json.loads(EVENTS_FILE.read_text())
        return data if isinstance(data, list) else []
    except Exception as e:
        log.info("trade_press: events load failed: %s", e)
        return []


def _save_events(events: list[dict]) -> None:
    # Cap + sort newest-first before persisting.
    events.sort(key=lambda e: e.get("detected_at", ""), reverse=True)
    if len(events) > MAX_EVENTS_KEEP:
        events = events[:MAX_EVENTS_KEEP]
    payload = json.dumps(events, indent=2)
    _atomic_write(EVENTS_FILE, payload)
    try:
        from tool import github_state
        github_state.push_async(
            "tool/state/trade_press_events.json", payload,
            "state: update trade-press triggers")
    except Exception as e:
        log.info("trade_press: github persist skipped: %s", e)


def _load_suppression() -> dict:
    if not SUPPRESS_FILE.exists():
        return {}
    try:
        data = json.loads(SUPPRESS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_suppression(s: dict) -> None:
    payload = json.dumps(s, indent=2)
    _atomic_write(SUPPRESS_FILE, payload)
    try:
        from tool import github_state
        github_state.push_async(
            "tool/state/trade_press_suppression.json", payload,
            "state: update trade-press suppression window")
    except Exception:
        pass


def _is_suppressed(person_id: str, source_key: str, suppression: dict) -> bool:
    key = f"{person_id}::{source_key}"
    iso = suppression.get(key)
    if not iso:
        return False
    try:
        last = datetime.fromisoformat(iso)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - last) < timedelta(days=SUPPRESS_DAYS)


# ----- public API -----------------------------------------------------
def list_active() -> list[dict]:
    """Active triggers — what the dashboard panel shows."""
    return [e for e in _load_events() if e.get("status", "active") == "active"]


def list_all() -> list[dict]:
    return _load_events()


def mark(event_id: str, status: str) -> bool:
    """Triage state mutation. Same shape as lead_status / candidate_watch."""
    if status not in {"active", "called", "dismissed"} or not event_id:
        return False
    with _locked(EVENTS_FILE):
        events = _load_events()
        hit = False
        for e in events:
            if e.get("event_id") == event_id:
                e["status"] = status
                hit = True
                break
        if not hit:
            return False
        _save_events(events)
    return True


# ----- opener templates -----------------------------------------------
_OPENER_TEMPLATES = {
    "award":     "Hi {first}, congratulations on {snippet} — saw it in "
                 "{source}. Worth a quick catch-up while it's fresh?",
    "promotion": "Hi {first}, saw the news in {source} about {snippet}. "
                 "Congratulations — would love to hear how you're "
                 "thinking about the team shape over the next 6 months.",
    "quoted":    "Hi {first}, read your comments in {source} on {snippet}. "
                 "Sharp point — would love 15 minutes to compare notes "
                 "on what we're seeing in the market.",
    "featured":  "Hi {first}, picked up your piece in {source} — {snippet}. "
                 "Genuinely useful framing. Worth a coffee next time "
                 "you're free?",
}


def _classify_hook(text: str) -> str:
    for label, pat in HOOK_PATTERNS:
        if pat.search(text):
            return label
    return "featured"   # default — safer than "quoted"


def _build_opener(person_name: str, source_name: str, hook: str, snippet: str) -> str:
    first = person_name.split()[0] if person_name else "there"
    tmpl = _OPENER_TEMPLATES.get(hook, _OPENER_TEMPLATES["featured"])
    snippet = (snippet or "").strip().rstrip(".") or "the piece"
    return tmpl.format(first=first, source=source_name, snippet=snippet)


# ----- scour orchestration --------------------------------------------
def _event_id(person_id: str, article_url: str) -> str:
    """Stable id — same person + same article = same event forever.
    Article-level dedup falls out of this automatically."""
    h = hashlib.sha1(f"{person_id}|{article_url}".encode("utf-8")).hexdigest()
    return h[:16]


def _scan_article_for_matches(title: str, summary: str, body: str,
                              name_index: dict[str, dict]) -> list[tuple[dict, str]]:
    """Return list of (contact, snippet) for every tracked person
    named in the article. snippet is the sentence containing the
    mention, used to make the opener relevant."""
    haystack = " ".join(filter(None, [title, summary, body]))
    haystack_lc = haystack.lower()
    seen: set[str] = set()
    hits: list[tuple[dict, str]] = []
    for name_lc, contact in name_index.items():
        if name_lc in haystack_lc and contact["id"] not in seen:
            seen.add(contact["id"])
            # Pull a sentence-sized snippet around the first match for
            # opener context. Cheap windowing — no NLP needed.
            i = haystack_lc.find(name_lc)
            start = max(0, haystack.rfind(".", 0, i) + 1)
            end = haystack.find(".", i + len(name_lc))
            if end == -1:
                end = min(len(haystack), i + 220)
            snippet = haystack[start:end].strip()
            if len(snippet) > 240:
                snippet = snippet[:237] + "…"
            hits.append((contact, snippet))
    return hits


def _emit_event(contact: dict, source: dict, article: dict,
                snippet: str, suppression: dict) -> dict | None:
    """Apply suppression + dedup, build the opener, return the event
    dict to store. Returns None if suppressed or already exists."""
    if _is_suppressed(contact["id"], source["key"], suppression):
        return None
    title = article.get("title", "")
    url = article.get("url", "")
    hook = _classify_hook(" ".join([title, snippet]))
    ev = TradePressEvent(
        event_id=_event_id(contact["id"], url),
        person_id=contact["id"],
        person_name=contact.get("name", ""),
        person_company=contact.get("company", ""),
        source_key=source["key"],
        source_name=source["name"],
        article_url=url,
        article_title=title,
        article_date=article.get("date", ""),
        hook_type=hook,
        snippet=snippet,
        opener=_build_opener(contact.get("name", ""), source["name"], hook, snippet),
        detected_at=_now_iso(),
    )
    return asdict(ev)


def scour() -> dict:
    """Run all sources, resolve names, dedup, emit events. Returns a
    summary dict suitable for logging.

    Designed to be cheap to call — entirety of the work is HTTP fetches
    and string scans; no LLM calls, no third-party APIs. Safe to schedule
    daily."""
    tracked = load_tracked()
    if not tracked:
        log.info("trade_press: no tracked contacts seeded; nothing to do")
        return {"sources_ok": 0, "sources_failed": 0,
                "articles_seen": 0, "events_new": 0,
                "detail": "tracked list empty"}
    name_index = _build_name_index(tracked)

    with _locked(EVENTS_FILE):
        events = _load_events()
        known_event_ids = {e["event_id"] for e in events}
        suppression = _load_suppression()

        sources_ok = 0
        sources_failed = 0
        articles_seen = 0
        new_events = 0

        for source in SOURCES:
            try:
                articles = _fetch_source(source)
            except Exception as e:
                log.info("trade_press: source %s failed: %s", source["key"], e)
                sources_failed += 1
                continue
            sources_ok += 1
            articles_seen += len(articles)
            for art in articles:
                hits = _scan_article_for_matches(
                    art.get("title", ""), art.get("summary", ""),
                    art.get("body", ""), name_index)
                for contact, snippet in hits:
                    ev = _emit_event(contact, source, art, snippet, suppression)
                    if ev is None:
                        continue
                    if ev["event_id"] in known_event_ids:
                        continue
                    events.append(ev)
                    known_event_ids.add(ev["event_id"])
                    # Set suppression window the moment we fire.
                    suppression[f"{contact['id']}::{source['key']}"] = _now_iso()
                    new_events += 1

        if new_events:
            _save_events(events)
            _save_suppression(suppression)

        return {"sources_ok": sources_ok, "sources_failed": sources_failed,
                "articles_seen": articles_seen, "events_new": new_events}


# ----- source adapters ------------------------------------------------
def _fetch_source(source: dict) -> list[dict]:
    """Dispatch to the right adapter. Each returns a list of
    {title, url, summary, body, date} dicts."""
    kind = source.get("kind")
    if kind == "rss":
        return _fetch_rss(source)
    if kind == "homepage_html":
        return _fetch_homepage_html(source)
    log.info("trade_press: unknown kind %s", kind)
    return []


def _fetch_rss(source: dict) -> list[dict]:
    """Reuses the existing _http.parse_rss helper."""
    from tool.sources._http import get, parse_rss
    r = get(source["url"])
    if not r or r.status_code != 200:
        return []
    items = parse_rss(r.content)
    out: list[dict] = []
    for it in items:
        out.append({
            "title":   it.get("title", ""),
            "url":     it.get("link", ""),
            "summary": it.get("summary", "") or it.get("description", ""),
            "body":    "",
            "date":    it.get("published", "") or it.get("updated", ""),
        })
    return out


def _fetch_homepage_html(source: dict) -> list[dict]:
    """Hit an editorial index page, extract article anchors, and pull
    the title from the link text. We deliberately don't follow into the
    article body — title + URL is enough signal for entity matching,
    and one HTTP request per source keeps the scour fast and polite.

    Heuristic: any <a href> whose text is ≥30 chars (a real headline,
    not nav) and whose href looks like an article slug, on the same
    domain as the homepage."""
    from tool.sources._http import get
    from urllib.parse import urljoin, urlparse
    r = get(source["url"])
    if not r or r.status_code != 200:
        return []
    try:
        from lxml import html as lhtml
        doc = lhtml.fromstring(r.content)
    except Exception as e:
        log.info("trade_press: %s parse failed: %s", source["key"], e)
        return []
    home_host = urlparse(source["url"]).netloc
    out: list[dict] = []
    seen_urls: set[str] = set()
    for a in doc.xpath("//a[@href]"):
        title = (a.text_content() or "").strip()
        href = a.get("href") or ""
        if len(title) < 30:
            continue
        full = urljoin(source["url"], href)
        if urlparse(full).netloc != home_host:
            continue
        # Crude "looks like an article" filter: slug-style path with
        # multiple segments or a year-month pattern. Drops obvious
        # category/tag links.
        path = urlparse(full).path.strip("/")
        if not path or path.count("/") < 1:
            continue
        if full in seen_urls:
            continue
        seen_urls.add(full)
        out.append({"title": title, "url": full, "summary": "",
                    "body": "", "date": ""})
        if len(out) >= 60:
            break
    return out


# ----- CLI / convenience ----------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")
    import pprint
    pprint.pprint(scour())
