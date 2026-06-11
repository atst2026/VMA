"""RNS enquiries-block CAPTURE — the warm-up half of the diffing signal.

Every RNS announcement ends with an enquiries block naming the issuer's
IR/comms contacts and usually their financial PR agency. Diffing those
names between successive announcements from the same issuer is a
registry-grade people-change signal sitting exactly on VMA's IR and
corporate comms patch — but you cannot diff one announcement. This
module is deliberately just the capture half: archive the raw block
text per announcement per issuer from day one, so when the parser and
differ land (the real build — the blocks are wildly inconsistent free
text), history already exists and the diff fires immediately instead of
six weeks later.

Store: tool/state/rns_contact_blocks.json
  {issuer_key: {"company": str,
                "blocks": [{"at", "url", "block"} ...newest-first, capped]}}
Budget: at most CAP fetches per run, RNS-kind signals only, URLs never
re-fetched. Never raises.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from tool.state_paths import state_dir

log = logging.getLogger("brief.rns_contacts")

MAX_BLOCKS_PER_ISSUER = 12
FETCH_CAP_PER_RUN = 15
BLOCK_CHARS = 1500

# The enquiries block opens with one of these headings in virtually
# every RNS house style.
_ENQ_RX = re.compile(
    r"(enquiries:?|for further information|further enquiries|"
    r"media contacts?:?|investor relations contacts?:?|contacts?:)"
    r"(.{0,%d})" % BLOCK_CHARS, re.I | re.S)
_RNS_HOST_RX = re.compile(
    r"investegate\.co\.uk|londonstockexchange\.com|rns\b", re.I)
_TAG_RX = re.compile(r"<[^>]+>")


def _file():
    return state_dir() / "rns_contact_blocks.json"


def _norm(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _load() -> dict:
    try:
        f = _file()
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, indent=1))


def extract_block(html_or_text: str | None) -> str | None:
    """Best-effort: the enquiries block as plain text, or None."""
    if not html_or_text:
        return None
    text = _TAG_RX.sub(" ", html_or_text)
    text = re.sub(r"\s+", " ", text)
    m = _ENQ_RX.search(text)
    if not m:
        return None
    block = (m.group(1) + m.group(2)).strip()
    # A real block names people/organisations — demand some substance.
    return block if len(block) > 60 else None


def _default_fetch(url: str) -> str | None:
    try:
        from tool.sources._http import get
        r = get(url, timeout=15)
        return r.text if r is not None and getattr(r, "status_code", 0) == 200 else None
    except Exception:
        return None


def capture_from_signals(signals: list[dict], fetch=None,
                         cap: int = FETCH_CAP_PER_RUN) -> int:
    """Archive enquiries blocks for today's RNS-kind signals. Returns the
    number of new blocks stored. Never raises."""
    try:
        fetch = fetch or _default_fetch
        store = _load()
        seen_urls = {b.get("url") for rec in store.values()
                     for b in rec.get("blocks", [])}
        now = datetime.now(timezone.utc).isoformat()
        fetched = stored = 0
        for sig in signals or []:
            if fetched >= cap:
                break
            if not isinstance(sig, dict):
                continue
            url = (sig.get("url") or "").strip()
            company = (sig.get("company") or "").strip()
            if (not url or not company or url in seen_urls
                    or (sig.get("kind") != "rns"
                        and not _RNS_HOST_RX.search(url))):
                continue
            fetched += 1
            block = extract_block(fetch(url))
            if not block:
                continue
            key = _norm(company)
            rec = store.setdefault(key, {"company": company, "blocks": []})
            rec["blocks"].insert(0, {"at": now, "url": url, "block": block})
            rec["blocks"] = rec["blocks"][:MAX_BLOCKS_PER_ISSUER]
            seen_urls.add(url)
            stored += 1
        if stored:
            _save(store)
        log.info("rns_contacts: %d enquiries blocks archived "
                 "(%d pages fetched)", stored, fetched)
        return stored
    except Exception as e:
        log.info("rns_contacts capture skipped (%s)", e)
        return 0
