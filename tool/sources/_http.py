"""Shared HTTP + XML helpers. All sources route through here."""
from __future__ import annotations
import hashlib
import logging
import time
from typing import Any, Iterable, Optional
from urllib.parse import urlencode

import requests
from lxml import etree

from tool.config import REQUEST_TIMEOUT, USER_AGENT

log = logging.getLogger("brief.http")

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})


def get(url: str, params: Optional[dict] = None, auth: Optional[tuple] = None,
        headers: Optional[dict] = None, timeout: int = REQUEST_TIMEOUT,
        tries: int = 2) -> Optional[requests.Response]:
    """GET with light retry. Returns None on failure rather than raising."""
    merged_headers = dict(headers) if headers else None
    for attempt in range(tries):
        try:
            r = _SESSION.get(url, params=params, auth=auth, headers=merged_headers, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 503):
                time.sleep(1 + attempt)
                continue
            log.info("GET %s → %s", url, r.status_code)
            return r
        except requests.RequestException as e:
            log.info("GET %s failed (%s/%s): %s", url, attempt + 1, tries, e)
            time.sleep(0.5 + attempt)
    return None


def parse_rss(content: bytes) -> list[dict]:
    """Parse RSS 2.0 or Atom via lxml. Returns list of item dicts."""
    items = []
    try:
        root = etree.fromstring(content, parser=etree.XMLParser(recover=True, huge_tree=True))
    except Exception as e:
        log.info("parse_rss failed: %s", e)
        return items

    if root is None:
        return items

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "dc": "http://purl.org/dc/elements/1.1/",
        "content": "http://purl.org/rss/1.0/modules/content/",
    }

    # RSS 2.0
    for it in root.findall(".//item"):
        items.append({
            "title": _text(it.find("title")),
            "link": _text(it.find("link")),
            "published": _text(it.find("pubDate")) or _text(it.find("dc:date", ns)),
            "summary": _text(it.find("description")) or _text(it.find("content:encoded", ns)),
            "guid": _text(it.find("guid")),
        })
    # Atom
    for it in root.findall("atom:entry", ns):
        link_el = it.find("atom:link", ns)
        href = link_el.get("href") if link_el is not None else ""
        items.append({
            "title": _text(it.find("atom:title", ns)),
            "link": href,
            "published": _text(it.find("atom:updated", ns)) or _text(it.find("atom:published", ns)),
            "summary": _text(it.find("atom:summary", ns)) or _text(it.find("atom:content", ns)),
            "guid": _text(it.find("atom:id", ns)),
        })
    return items


def _text(el) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def signal_id(source: str, payload: str) -> str:
    """Stable dedup ID."""
    return hashlib.sha1(f"{source}|{payload}".encode("utf-8")).hexdigest()[:16]
